import re, contextlib, concurrent.futures, threading, shutil, os
from pathlib import Path
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

_RETRIEVAL_TIMEOUT_SEC = 60

# Keywords that mark a tool as a stub/toy/deprecated/internal — excluded from retrieval
_STUB_KEYWORDS = re.compile(
    r"\[STUB\]|\[DEPRECATED\]|\[INTERNAL API\]|\[INTERNAL\]"
    r"|\bToy\b|\bstub\b|\bplaceholder\b|\bDO NOT USE\b"
    r"|DO NOT IMPORT IN GENERATED SCRIPTS"
    r"|NOT AVAILABLE IN EXECUTION ENVIRONMENTS",
    re.IGNORECASE,
)

# CLI tools that must be present on PATH to be offered to the generator
# key = tool name in description, value = CLI executable to check
_CLI_TOOL_BINARIES = {
    # ── Core alignment / manipulation ─────────────────────────────────────────
    "samtools": "samtools",
    "bowtie2": "bowtie2",
    "bwa": "bwa",
    "minimap2": "minimap2",
    "blast": "blastn",
    "bedtools": "bedtools",
    "fastqc": "fastqc",
    "fastqc": "fastqc",
    "fastp": "fastp",
    "wgsim": "wgsim",
    "seqkit": "seqkit",
    "bbduk": "bbduk.sh",
    "diamond": "diamond",
    # ── Assembly / scaffolding ────────────────────────────────────────────────
    "megahit": "megahit",
    "spades": "spades.py",
    "quast": "quast.py",
    # ── Binning ───────────────────────────────────────────────────────────────
    "semibin2": "SemiBin2",
    "concoct": "concoct",
    "maxbin2": "run_MaxBin.pl",
    "das_tool": "DAS_Tool",
    "dastool": "DAS_Tool",
    # ── Bin quality ───────────────────────────────────────────────────────────
    "checkm2": "checkm2",
    "gunc": "gunc",
    # ── Coverage / abundance ──────────────────────────────────────────────────
    "coverm": "coverm",
    # ── Replication rate ──────────────────────────────────────────────────────
    "irep": "iRep",
    # ── Strain-level microdiversity / SNVs ────────────────────────────────────
    "instrain": "inStrain",
    # ── Plasmid reconstruction / mobility ─────────────────────────────────────
    "mob_recon": "mob_recon",
    "mob_typer": "mob_typer",
    "mob_suite": "mob_recon",
    # ── MAG dereplication ─────────────────────────────────────────────────────
    "drep": "dRep",
    "mash": "mash",
    "fastani": "fastANI",
    # ── Taxonomic classification ──────────────────────────────────────────────
    "kraken2": "kraken2",
    "metaphlan": "metaphlan",
    "metaphlan4": "metaphlan",
    # gtdbtk has a description but is NOT installed (needs ~80GB GTDB DB, left out
    # of meta-env1.yaml). Listing it here lets detection flag it ABSENT so the
    # retriever filters it out instead of falsely offering it to the planner.
    "gtdbtk": "gtdbtk",
    "esearch": "esearch",
    "efetch": "efetch",
    "query_ncbi_entrez": "esearch",
    "gget": "gget",
    "run_gget_virus": "gget",
    "kaiju": "kaiju",
    "sylph": "sylph",
    # ── Gene prediction / annotation ──────────────────────────────────────────
    "prodigal": "prodigal",
    "prokka": "prokka",
    "hmmer": "hmmscan",
    "eggnog-mapper": "emapper.py",
    "humann3": "humann",
    # ── Specialized annotation ────────────────────────────────────────────────
    "antismash": "antismash",
    "genomad": "genomad",
    "abricate": "abricate",
    "dbcan": "run_dbcan",
    "run_dbcan": "run_dbcan",
    "pharokka": "pharokka.py",
    # ── AMR / Resistance ──────────────────────────────────────────────────────
    "amrfinder": "amrfinder",
    "amrfinderplus": "amrfinder",
    "rgi": "rgi",
    # ── Genome annotation ─────────────────────────────────────────────────────
    "prokka": "prokka",
    # ── Community / stats ─────────────────────────────────────────────────────
    "lefse": "lefse_run.py",
    "nonpareil": "nonpareil",
    # ── QC aggregation / read simulation (pip-installed in meta-env1) ──────────
    "multiqc": "multiqc",
    "insilicoseq": "iss",
}

# ─── Multi-env bin path discovery ─────────────────────────────────────────
# CRITICAL FIX: shutil.which() only checks the current PATH (= bio-agent-env1
# when uvicorn runs). Bioinfo tools like seqkit/prokka/kraken2 actually live
# in meta-env1, a SEPARATE conda env. Without this fix, the agent's tool
# inventory falsely flags these tools as "UNAVAILABLE" and the planner
# resorts to fallbacks (e.g. Biopython instead of seqkit), losing
# correctness and performance.
#
# Path resolution: matches genomeer.runtime.env_manager.ENVS_DIR convention
# (RUNTIME_PKG_HOME defaults to ~/.bioagentpkg).
_RUNTIME_PKG_HOME = Path(os.environ.get("RUNTIME_PKG_HOME", str(Path.home() / ".bioagentpkg"))).resolve()
_ENVS_DIR = _RUNTIME_PKG_HOME / "runtime" / "pkgs" / "envs"
# Order matters: most-likely env first for early-exit optimisation.
_KNOWN_ENV_BINS = [
    _ENVS_DIR / "meta-env1" / "bin",        # bioinfo CLI stack (seqkit, prokka, kraken2, ...)
    _ENVS_DIR / "bio-agent-env1" / "bin",   # generic python env (uvicorn host)
    _ENVS_DIR / "btools_env_py310" / "bin", # HLA stack (cnvkit, OptiType, ...)
    _ENVS_DIR / "amplicon-env1" / "bin",    # amplicon 16S R stack (DADA2, phyloseq, vegan)
]

def _which_in_envs(exe: str) -> bool:
    """Check if executable exists in current PATH OR in any of the known
    runtime conda env bin directories. Treats a regular file with the +x
    bit (or just-existing for windows-style scripts) as available."""
    if shutil.which(exe):
        return True
    for env_bin in _KNOWN_ENV_BINS:
        try:
            cand = env_bin / exe
            if cand.is_file():
                # On unix require +x; on windows .exe/.py are executable by extension
                if os.name == "nt" or os.access(str(cand), os.X_OK):
                    return True
        except (OSError, ValueError):
            continue
    return False

def _registry_declared_bins() -> set:
    """Lowercased set of every binary declared under any env's `provides_bins`
    in the runtime registry (index.yaml).

    These tools are INSTALLABLE ON DEMAND: the executor lazily calls `ensure_env`
    before using them, so meta-env1 (and the other envs) may not physically exist
    yet when this module is imported. Treating registry-declared bins as available
    stops the planner from falsely seeing them as missing and bailing the whole
    pipeline to QA on a fresh machine. Returns an empty set on any error → the
    caller then falls back to physical-only detection (previous behaviour)."""
    try:
        import yaml
        from genomeer.runtime.env_manager import REGISTRY_PATH
        data = yaml.safe_load(Path(REGISTRY_PATH).read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    bins = set()
    for env in (data.get("envs") or []):
        for b in (env.get("provides_bins") or []):
            if isinstance(b, str) and b.strip():
                bins.add(b.strip().lower())
    return bins

def _available_cli_tools() -> set:
    """Return the set of CLI tool NAMES considered available to the planner:
      (a) physically present on PATH or in an installed env bin dir, OR
      (b) declared in a registry env's provides_bins (installable on demand).
    Case (b) fixes the false-negative where a fresh machine (meta-env1 not yet
    installed) made the planner route every metagenomics pipeline to QA."""
    declared = _registry_declared_bins()
    return {
        name for name, exe in _CLI_TOOL_BINARIES.items()
        if _which_in_envs(exe) or str(exe).strip().lower() in declared
    }

_AVAILABLE_CLI = _available_cli_tools()  # computed once at import time


class ToolRetriever:
    """Retrieve tools from the tool registry, filtering stubs and unavailable CLIs."""

    def __init__(self):
        pass

    @staticmethod
    def filter_tools(tools: list) -> list:
        """
        Remove tools that are:
        1. Marked as stub/toy/deprecated in their description.
        2. Require a CLI backend that is not installed on the current machine.
        """
        kept = []
        for tool in tools:
            desc = ""
            if isinstance(tool, dict):
                desc = tool.get("description", "")
            elif hasattr(tool, "description"):
                desc = tool.description or ""

            # Skip stubs / deprecated
            if _STUB_KEYWORDS.search(desc):
                continue

            # Skip CLI tools whose binary is absent
            skip = False
            for cli_name, exe in _CLI_TOOL_BINARIES.items():
                if cli_name.lower() in desc.lower() and shutil.which(exe) is None:
                    skip = True
                    break
            if skip:
                continue

            kept.append(tool)
        return kept

    def prompt_based_retrieval(self, query: str, resources: dict, llm=None) -> dict:
        """Use a prompt-based approach to retrieve the most relevant resources for a query.
        Args:
            query: The user's query
            resources: A dictionary with keys 'tools', 'data_lake', and 'libraries',
                      each containing a list of available resources
            llm: Optional LLM instance to use for retrieval (if None, will create a new one)
        Returns:
            A dictionary with the same keys, but containing only the most relevant resources
        """
        
        # Pre-filter: remove stubs, deprecated tools, and unavailable CLI backends
        resources = dict(resources)
        resources["tools"] = self.filter_tools(resources.get("tools", []))

        # Create a prompt for the LLM to select relevant resources
        prompt = f"""
            You are an expert biomedical research assistant. Your task is to select the relevant resources to help answer a user's query.

            USER QUERY: {query}

            Below are the available resources. For each category, select items that are directly or indirectly relevant to answering the query.
            Be generous in your selection - include resources that might be useful for the task, even if they're not explicitly mentioned in the query.
            It's better to include slightly more resources than to miss potentially useful ones.

            AVAILABLE TOOLS:
            {self._format_resources_for_prompt(resources.get("tools", []))}

            AVAILABLE DATA LAKE ITEMS:
            {self._format_resources_for_prompt(resources.get("data_lake", []))}

            AVAILABLE SOFTWARE LIBRARIES:
            {self._format_resources_for_prompt(resources.get("libraries", []))}

            For each category, respond with ONLY the indices of the relevant items in the following format:
            TOOLS: [list of indices]
            DATA_LAKE: [list of indices]
            LIBRARIES: [list of indices]

            For example:
            TOOLS: [0, 3, 5, 7, 9]
            DATA_LAKE: [1, 2, 4]
            LIBRARIES: [0, 2, 4, 5, 8]

            If a category has no relevant items, use an empty list, e.g., DATA_LAKE: []

            IMPORTANT GUIDELINES:
            1. Be generous but not excessive - aim to include all potentially relevant resources
            2. ALWAYS prioritize database tools for general queries - include as many database tools as possible
            3. Include all literature search tools
            4. For wet lab sequence type of queries, ALWAYS include molecular biology tools
            5. For data lake items, include datasets that could provide useful information
            6. For libraries, include those that provide functions needed for analysis
            7. Don't exclude resources just because they're not explicitly mentioned in the query
            8. When in doubt about a database tool or molecular biology tool, include it rather than exclude it
        """

        # Use the provided LLM or create a new one
        if llm is None:
            llm = ChatOpenAI(model="gpt-4o")

        # Invoke the LLM with a hard timeout to prevent indefinite blocking
        def _call_llm():
            if hasattr(llm, "invoke"):
                return llm.invoke([HumanMessage(content=prompt)]).content
            return str(llm(prompt))

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call_llm)
                response_content = fut.result(timeout=_RETRIEVAL_TIMEOUT_SEC)
        except concurrent.futures.TimeoutError:
            # Retrieval timed out: fall back to returning all resources unfiltered
            return {k: list(v) for k, v in resources.items()}
        except Exception:
            # Any LLM error: same safe fallback
            return {k: list(v) for k, v in resources.items()}

        # Parse the response to extract the selected indices
        selected_indices = self._parse_llm_response(response_content)

        # Get the selected resources
        selected_resources = {
            "tools": [
                resources["tools"][i] for i in selected_indices.get("tools", []) if i < len(resources.get("tools", []))
            ],
            "data_lake": [
                resources["data_lake"][i]
                for i in selected_indices.get("data_lake", [])
                if i < len(resources.get("data_lake", []))
            ],
            "libraries": [
                resources["libraries"][i]
                for i in selected_indices.get("libraries", [])
                if i < len(resources.get("libraries", []))
            ],
        }

        return selected_resources

    def _format_resources_for_prompt(self, resources: list) -> str:
        """Format resources for inclusion in the prompt."""
        formatted = []
        for i, resource in enumerate(resources):
            if isinstance(resource, dict):
                # Handle dictionary format (from tool registry or data lake/libraries with descriptions)
                name = resource.get("name", f"Resource {i}")
                description = resource.get("description", "")
                formatted.append(f"{i}. {name}: {description}")
            elif isinstance(resource, str):
                # Handle string format (simple strings)
                formatted.append(f"{i}. {resource}")
            else:
                # Try to extract name and description from tool objects
                name = getattr(resource, "name", str(resource))
                desc = getattr(resource, "description", "")
                formatted.append(f"{i}. {name}: {desc}")

        return "\n".join(formatted) if formatted else "None available"

    def _parse_llm_response(self, response: str) -> dict:
        """Parse the LLM response to extract the selected indices."""
        selected_indices = {"tools": [], "data_lake": [], "libraries": []}

        # Extract indices for each category
        tools_match = re.search(r"TOOLS:\s*\[(.*?)\]", response, re.IGNORECASE)
        if tools_match and tools_match.group(1).strip():
            with contextlib.suppress(ValueError):
                selected_indices["tools"] = [int(idx.strip()) for idx in tools_match.group(1).split(",") if idx.strip()]

        data_lake_match = re.search(r"DATA_LAKE:\s*\[(.*?)\]", response, re.IGNORECASE)
        if data_lake_match and data_lake_match.group(1).strip():
            with contextlib.suppress(ValueError):
                selected_indices["data_lake"] = [
                    int(idx.strip()) for idx in data_lake_match.group(1).split(",") if idx.strip()
                ]

        libraries_match = re.search(r"LIBRARIES:\s*\[(.*?)\]", response, re.IGNORECASE)
        if libraries_match and libraries_match.group(1).strip():
            with contextlib.suppress(ValueError):
                selected_indices["libraries"] = [
                    int(idx.strip()) for idx in libraries_match.group(1).split(",") if idx.strip()
                ]

        return selected_indices