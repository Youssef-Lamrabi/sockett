import re, contextlib, concurrent.futures, threading, shutil
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
    "maxbin2": "run_MaxBin2.pl",
    # ── Bin quality ───────────────────────────────────────────────────────────
    "checkm2": "checkm2",
    # ── Taxonomic classification ──────────────────────────────────────────────
    "kraken2": "kraken2",
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
    "dbcan": "run_dbcan.py",
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
}

def _available_cli_tools() -> set:
    """Return the set of CLI tool names that are actually on PATH."""
    return {name for name, exe in _CLI_TOOL_BINARIES.items() if shutil.which(exe)}

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