from __future__ import annotations
import re, yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from genomeer.runtime.env_manager import REGISTRY_PATH 
Kind = Literal["bin", "py", "r"]

@dataclass
class EnvSpec:
    name: str
    description: str | None = None
    spec: str | None = None            # filename of the env yaml
    python: str | None = None          # e.g. "3.11"
    provides_bins: list[str] = field(default_factory=list)
    provides_py: list[str] = field(default_factory=list)
    provides_r: list[str] = field(default_factory=list)

    # normalized caches
    _bins_norm: set[str] = field(init=False, default_factory=set)
    _py_norm_names: set[str] = field(init=False, default_factory=set)
    _py_norm_full: set[str] = field(init=False, default_factory=set)
    _r_norm_names: set[str] = field(init=False, default_factory=set)
    _r_norm_full: set[str] = field(init=False, default_factory=set)

    def __post_init__(self):
        # Binaries: compare case-insensitively
        self._bins_norm = {b.strip().lower() for b in self.provides_bins}

        # Python/R: keep both name-only and "name==ver" exacts
        def split_pkg(p: str) -> tuple[str, str | None]:
            p = p.strip()
            m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*==\s*([A-Za-z0-9_\-\.]+)\s*$", p)
            if m:
                return m.group(1).lower(), m.group(2)
            return p.lower(), None

        py_names, py_full = set(), set()
        for p in self.provides_py:
            name, ver = split_pkg(p)
            py_names.add(name)
            py_full.add(f"{name}=={ver}" if ver else name)

        r_names, r_full = set(), set()
        for p in self.provides_r:
            name, ver = split_pkg(p)
            r_names.add(name)
            r_full.add(f"{name}=={ver}" if ver else name)

        self._py_norm_names = py_names
        self._py_norm_full = py_full
        self._r_norm_names = r_names
        self._r_norm_full = r_full


def _load_registry(path: Path | str) -> list[EnvSpec]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Env registry not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    envs = data.get("envs", [])
    out: list[EnvSpec] = []
    for e in envs:
        out.append(
            EnvSpec(
                name=e.get("name", ""),
                description=e.get("description"),
                spec=e.get("spec"),
                python=e.get("python"),
                provides_bins=e.get("provides_bins", []) or [],
                provides_py=e.get("provides_py", []) or [],
                provides_r=e.get("provides_r", []) or [],
            )
        )
    return out


def _normalize_tool_and_kind(tool: str, kind: str) -> tuple[str, Kind, str | None]:
    """Return (name_lower, kind_literal, version_if_any)."""
    k = kind.strip().lower()
    if k not in {"bin", "py", "r"}:
        raise ValueError("kind must be one of: 'bin', 'py', 'r'")

    tool = (tool or "").strip()
    if not tool:
        raise ValueError("tool must be a non-empty string")

    # Parse potential version constraint "name==x.y"
    m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*==\s*([A-Za-z0-9_\-\.]+)\s*$", tool)
    if m:
        return m.group(1).lower(), k, m.group(2)
    return tool.lower(), k, None


def _score_match(env: EnvSpec, tool_name: str, kind: Kind, req_version: str | None) -> tuple[int, str]:
    """
    Higher score = better. Also return a reason string.
    Scoring tiers:
      100: exact version match (py/r only)
       80: name-only match (py/r)
       60: bin match (case-insensitive)
        0: no match
    """
    if kind == "bin":
        if tool_name in env._bins_norm:
            return 60, "matched binary"
        return 0, "no binary match"

    # py / r
    full_key = f"{tool_name}=={req_version}" if req_version else tool_name
    if kind == "py":
        if req_version and full_key in env._py_norm_full:
            return 100, "matched python package (exact version)"
        if tool_name in env._py_norm_names or full_key in env._py_norm_full:
            return 80, "matched python package (name)"
        return 0, "no python package match"

    if kind == "r":
        if req_version and full_key in env._r_norm_full:
            return 100, "matched R package (exact version)"
        if tool_name in env._r_norm_names or full_key in env._r_norm_full:
            return 80, "matched R package (name)"
        return 0, "no R package match"

    return 0, "no match"


def _tiebreak(a: EnvSpec, b: EnvSpec, kind: Kind) -> int:
    """
    Deterministic tiebreak:
      1) Prefer env with fewer total provides (more specific)
      2) Prefer higher python version (if both have numeric python)
      3) Lexicographic by env name
    Return -1 if a<b (a wins), 1 if a>b (b wins), 0 if equal.
    """
    def size(e: EnvSpec) -> int:
        return len(e.provides_bins) + len(e.provides_py) + len(e.provides_r)

    sa, sb = size(a), size(b)
    if sa != sb:
        return -1 if sa < sb else 1

    def pyv(e: EnvSpec) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in (e.python or "").split(".") if x.isdigit())
        except Exception:
            return ()

    va, vb = pyv(a), pyv(b)
    if va != vb:
        # prefer higher
        return -1 if va > vb else 1

    return -1 if a.name < b.name else (1 if a.name > b.name else 0)


def resolve_env(tool: str, kind: Kind, registry_path: Path | str = REGISTRY_PATH) -> dict:
    """
    Decide the best environment that provides `tool` of `kind` ("bin"|"py"|"r").
    Returns a dict:
      {
        "env_name": str | None,
        "spec": str | None,
        "python": str | None,
        "score": int,
        "reason": str,
        "description": str | None,
        "candidates": [ ... ]   # only included if no match
      }
    """
    name, k, req_ver = _normalize_tool_and_kind(tool, kind)
    envs = _load_registry(registry_path)

    scored: list[tuple[int, str, EnvSpec]] = []
    for e in envs:
        score, why = _score_match(e, name, k, req_ver)
        if score > 0:
            scored.append((score, why, e))

    if not scored:
        # help the caller by showing what each env exposes (brief)
        cands = []
        for e in envs:
            if k == "bin":
                cands.append({"env": e.name, "bins": e.provides_bins[:10]})
            elif k == "py":
                cands.append({"env": e.name, "py": e.provides_py[:10]})
            else:
                cands.append({"env": e.name, "r": e.provides_r[:10]})

        return {
            "env_name": None,
            "spec": None,
            "python": None,
            "score": 0,
            "reason": f"No environment provides {tool!r} as {k}.",
            "description": None,
            "candidates": cands,
        }

    # keep highest score, then tiebreak
    max_score = max(s for s, _, _ in scored)
    top = [t for t in scored if t[0] == max_score]

    if len(top) == 1:
        score, why, env = top[0]
    else:
        # deterministic tiebreak among equals
        env = top[0][2]
        score = top[0][0]
        why = top[0][1]
        for _, _, e in top[1:]:
            if _tiebreak(e, env, k) < 0:
                env = e

    return {
        "env_name": env.name,
        "spec": env.spec,
        "python": env.python,
        "score": score,
        "reason": why,
        "description": env.description,
    }


# --- tiny CLI for debugging (optional) ---
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m genomeer.runtime.env_resolver <tool> <kind: bin|py|r> [registry_path]")
        sys.exit(2)
    tool = sys.argv[1]
    kind = sys.argv[2]
    reg = sys.argv[3] if len(sys.argv) > 3 else REGISTRY_PATH
    print(resolve_env(tool, kind, reg))


# ---------------------------------------------------------------------------
# FIX G_ENV1: resolve_env_for_code — imported by BioAgent.py (line 46)
# Determines correct micromamba env for a generated code block.
# Priority: env_hint > meta-env1 tool scan > R lang > keep current_env
# ---------------------------------------------------------------------------
META_ENV_SIGNALS = {
    # CLI binaries
    "fastp", "fastqc", "multiqc", "nanostat", "nanoplot", "trim-galore",
    "metaspades.py", "megahit", "flye",
    "minimap2", "bowtie2", "bwa", "bwa-mem2", "samtools", "bedtools",
    "kraken2", "bracken", "metaphlan", "gtdbtk", "krona",
    "metabat2", "das_tool", "checkm2", "virsorter2", "checkv", "deepvirfinder",
    "prokka", "prodigal", "diamond", "hmmsearch", "hmmscan",
    "humann", "amrfinder", "rgi", "virsorter", "dvf.py",
    "prefetch", "fasterq-dump", "seqtk", "pigz",
    # Python wrapper names
    "run_fastp", "run_fastqc", "run_kraken2", "run_metaphlan4",
    "run_metaspades", "run_megahit", "run_flye", "run_minimap2",
    "run_bowtie2", "run_bwa_mem", "run_metabat2", "run_das_tool",
    "run_checkm2", "run_prokka", "run_prodigal", "run_diamond",
    "run_hmmer", "run_humann3", "run_amrfinderplus", "run_rgi_card",
    "run_bracken", "run_gtdbtk", "run_krona", "run_nanostat",
    "run_multiqc", "compute_coverage_samtools",
    "run_virsorter2", "run_checkv", "run_deepvirfinder",
    "from genomeer.tools.function.metagenomics",
    "from genomeer.tools.function.viromics",
}


def resolve_env_for_code(
    code: str,
    lang: str | None,
    env_hint: str | None,
    current_env: str,
) -> str:
    """
    Determine the correct micromamba environment for a generated code block.

    Priority order:
      1. Explicit env="..." attribute in <EXECUTE> tag  → use as-is
      2. Code mentions meta-env1 CLI tools             → return "meta-env1"
      3. Code is R                                     → return "bio-agent-env1"
      4. Keep current_env                              → no change needed
    """
    # 1. LLM explicitly declared the env in the tag
    if env_hint:
        return env_hint

    # 2. Scan code for metagenomics tool usage
    if code:
        code_lower = code.lower()
        for tool in META_ENV_SIGNALS:
            if tool in code_lower:
                return "meta-env1"

    # 3. R code always runs in bio-agent-env1 (has Rscript + R packages)
    if lang and lang.upper() == "R":
        return "bio-agent-env1"

    # 4. No change needed
    return current_env
