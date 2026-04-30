import os
import sys
import shlex
import gzip
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple


def download_from_ncbi(
    *,
    # Core selectors (choose at least one among: assembly_accessions, species_taxids, taxids, genera, strains;
    # or just pass groups='all' for a broad pull)
    groups: str = "all",                         # e.g. "bacteria" or "plant" or "all" or "bacteria,viral"
    section: str = "refseq",                     # "refseq" | "genbank"
    formats: str = "fasta",                      # e.g. "fasta" or "fasta,assembly-report"
    assembly_levels: str = "all",                # "all|complete|chromosome|scaffold|contig"
    genera: Optional[str] = None,                # comma-separated genera names; or use fuzzy_genus=True
    strains: Optional[str] = None,               # comma-separated strains or filepath (one per line)
    species_taxids: Optional[str] = None,        # e.g. "3702" or "9606,9685"
    taxids: Optional[str] = None,                # any rank taxids, comma-separated
    assembly_accessions: Optional[str] = None,   # e.g. "GCF_000001735.4" or comma-separated
    refseq_categories: Optional[str] = None,     # e.g. "reference,representative"
    type_materials: Optional[str] = None,        # e.g. "any" | "all" | "reference" | ...
    fuzzy_genus: bool = False,
    fuzzy_accessions: bool = False,

    # IO & behavior
    output_folder: Optional[Union[str, Path]] = None,  # default -> safe temp dir
    flat_output: bool = False,
    human_readable: bool = False,
    progress_bar: bool = False,
    uri: Optional[str] = None,                          # override base URI, e.g. "https://ftp.ncbi.nlm.nih.gov/genomes"
    parallel: int = 1,
    retries: int = 0,
    metadata_table: Optional[Union[str, Path]] = None,
    dry_run: bool = False,
    no_cache: bool = False,
    verbose: bool = False,
    debug: bool = False,

    # Post-processing
    decompress: bool = False,                           # if True, gunzip *.gz into same folder

    # Execution
    env: Optional[Dict[str, str]] = None,               # extra env vars if needed
    timeout_sec: int = 1800,                            # generous for big pulls
) -> Dict[str, Any]:
    """
    Download genomes from NCBI via 'ncbi-genome-download' with safe defaults.

    Returns:
        {
          "ok": bool,
          "cmd": List[str],           # final command executed
          "stdout": str,
          "stderr": str,
          "output_folder": str,       # resolved path
          "downloaded_files": List[str],   # paths (may be empty on dry-run or no match)
          "decompressed_files": List[str], # if decompress=True
          "note": str                 # helpful message on what happened
        }
    """
    # --------- Prepare output folder (avoid default '/' permission issues) ---------
    if output_folder is None:
        output_folder = Path(tempfile.mkdtemp(prefix="ngd_"))
        created_temp = True
    else:
        output_folder = Path(output_folder)
        output_folder.mkdir(parents=True, exist_ok=True)
        created_temp = False

    # --------- Build command line ---------
    cmd: List[str] = ["ncbi-genome-download"]

    # Section & formats
    if section:
        cmd += ["-s", section]
    if formats:
        cmd += ["-F", formats]

    # Filters
    if assembly_levels and assembly_levels.lower() != "all":
        cmd += ["-l", assembly_levels]
    if genera:
        cmd += ["-g", genera]
    if fuzzy_genus:
        cmd += ["--fuzzy-genus"]
    if strains:
        cmd += ["-S", strains]
    if species_taxids:
        cmd += ["-T", species_taxids]
    if taxids:
        cmd += ["-t", taxids]
    if assembly_accessions:
        cmd += ["-A", assembly_accessions]
    if fuzzy_accessions:
        cmd += ["--fuzzy-accessions"]
    if refseq_categories:
        cmd += ["-R", refseq_categories]
    if type_materials:
        cmd += ["-M", type_materials]

    # Output & behavior
    cmd += ["-o", str(output_folder)]
    if flat_output:
        cmd += ["--flat-output"]
    if human_readable:
        cmd += ["-H"]
    if progress_bar:
        cmd += ["-P"]
    if uri:
        cmd += ["-u", uri]
    if parallel and parallel != 1:
        cmd += ["-p", str(parallel)]
    if retries and retries > 0:
        cmd += ["-r", str(retries)]
    if metadata_table:
        cmd += ["-m", str(metadata_table)]
    if dry_run:
        cmd += ["-n"]
    if no_cache:
        cmd += ["-N"]
    if verbose:
        cmd += ["-v"]
    if debug:
        cmd += ["-d"]

    # REQUIRED positional: groups
    groups = (groups or "all").strip()
    cmd.append(groups)

    # --------- Execute ---------
    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            env=run_env,
        )
    except FileNotFoundError as e:
        note = (
            "ncbi-genome-download not found. Install with:\n"
            "  pip install ncbi-genome-download\n"
            "or via conda/bioconda:\n"
            "  conda install -c bioconda ncbi-genome-download"
        )
        payload = {
            "ok": False,
            "cmd": cmd,
            "stdout": "",
            "stderr": str(e),
            "output_folder": str(output_folder),
            "downloaded_files": [],
            "decompressed_files": [],
            "note": note,
        }
        print(payload)
        return payload
    except subprocess.TimeoutExpired as e:
        payload = {
            "ok": False,
            "cmd": cmd,
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
            "output_folder": str(output_folder),
            "downloaded_files": [],
            "decompressed_files": [],
            "note": f"Command timed out after {timeout_sec}s.",
        }
        print(payload)
        return payload

    # --------- Collect outputs ---------
    stdout, stderr = proc.stdout or "", proc.stderr or ""

    # Dry-run: nothing to collect, but still return useful info
    if dry_run:
        payload = {
            "ok": proc.returncode == 0,
            "cmd": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "output_folder": str(output_folder),
            "downloaded_files": [],
            "decompressed_files": [],
            "note": "Dry run: no files downloaded. See stdout for planned downloads.",
        }
        print(payload)
        return payload

    # Find files (common patterns from ncbi-genome-download)
    patterns = [
        "**/*.fna", "**/*.fa", "**/*.fasta",
        "**/*.fna.gz", "**/*.fa.gz", "**/*.fasta.gz",
        "**/*.gff", "**/*.gff.gz",
        "**/*.gbff", "**/*.gbff.gz",
        "**/assembly_report.txt", "**/*assembly_report.txt", "**/*assembly-report.txt",
        "**/*.md5", "**/*.txt", "**/*.gz"
    ]
    found: List[str] = []
    for pat in patterns:
        for p in output_folder.glob(pat):
            if p.is_file():
                found.append(str(p))

    # Optional: decompress gz outputs (only text-based FASTA/GFF/GBFF, leaving md5 etc. compressed)
    decompressed: List[str] = []
    if decompress:
        to_unzip_ext = {".gz"}
        for path_str in list(found):  # iterate over a snapshot
            p = Path(path_str)
            if p.suffix.lower() in to_unzip_ext and p.name.endswith((".fna.gz", ".fa.gz", ".fasta.gz", ".gff.gz", ".gbff.gz")):
                out_path = p.with_suffix("")  # remove .gz
                try:
                    with gzip.open(p, "rb") as fin, open(out_path, "wb") as fout:
                        shutil.copyfileobj(fin, fout)
                    decompressed.append(str(out_path))
                except Exception as e:
                    # keep going even if one file fails to decompress
                    pass

    # --------- Build return ---------
    ok = (proc.returncode == 0) and (len(found) > 0 or "No downloads matched your filter" not in stderr)

    # Helpful note
    if "No downloads matched your filter" in stderr:
        note = (
            "No downloads matched your filter. Tips:\n"
            "- Ensure 'groups' matches the accession/taxid scope (try groups='all').\n"
            "- If using '-A', verify the accession exists in the chosen 'section' (refseq vs genbank).\n"
            "- Try relaxing filters (remove '-l complete', '-R reference', etc.).\n"
            "- For species, consider '-T <species_taxid>' (e.g., -T 3702 for Arabidopsis)."
        )
    elif "Permission denied" in stderr:
        note = (
            "Permission error: ensure 'output_folder' is writable. "
            "A temporary directory was used by default if none was provided."
        )
    else:
        note = "Completed." if ok else "Exited with a non-zero status; see stderr."

    payload = {
        "ok": ok,
        "cmd": cmd,
        "stdout": stdout,
        "stderr": stderr,
        "output_folder": str(output_folder),
        "downloaded_files": sorted(found),
        "decompressed_files": sorted(decompressed),
        "note": note,
    }
    print(payload)
    return payload
