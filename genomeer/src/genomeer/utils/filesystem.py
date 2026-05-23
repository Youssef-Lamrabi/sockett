"""
Abstract filesystem helpers for generated code.

These functions are injected into the GENERATOR prompt so the LLM can reference
them by name instead of writing raw glob/os calls, reducing hallucination of
incorrect paths.

They are also available as real Python callables that can be injected into the
REPL execution environment via _inject_custom_functions_to_repl().
"""
import glob as _glob
import os as _os


def list_files(run_dir: str, pattern: str = "*") -> list[str]:
    """Return sorted list of absolute paths matching `pattern` inside `run_dir`.

    Examples
    --------
    list_files(run_dir, "*.fna")      -> all .fna files
    list_files(run_dir, "*.fna.gz")   -> compressed FASTA
    list_files(run_dir, "*.fna*")     -> both .fna and .fna.gz
    list_files(run_dir)               -> every file (no subdirs)
    """
    matches = sorted(_glob.glob(_os.path.join(run_dir, pattern)))
    return [p for p in matches if _os.path.isfile(p)]


def get_file(run_dir: str, pattern: str) -> str:
    """Return the path of the first file matching `pattern` in `run_dir`.

    Raises FileNotFoundError if nothing matches — never returns an invented path.
    """
    matches = list_files(run_dir, pattern)
    if not matches:
        available = list_files(run_dir)
        raise FileNotFoundError(
            f"No file matching '{pattern}' in '{run_dir}'. "
            f"Available files: {[_os.path.basename(p) for p in available]}"
        )
    return matches[0]


# ── Prompt snippet injected into GENERATOR / INPUT_GUARD prompts ─────────────

FILESYSTEM_PROMPT_SNIPPET = """\
FILESYSTEM HELPERS (always available in your code — use these instead of hardcoding paths):

  from genomeer.utils.filesystem import list_files, get_file

  # List all .fna files in the run directory:
  fna_files = list_files(run_dir, "*.fna")
  # Also works with .fna.gz, *.faa, *.gff, etc.

  # Get the first matching file (raises FileNotFoundError with a helpful message if missing):
  fasta_path = get_file(run_dir, "*.fna")

  # NEVER hardcode filenames like "GCF_000005845.2.fna" — they will be wrong.
  # ALWAYS use list_files() or get_file() with run_dir.
"""
