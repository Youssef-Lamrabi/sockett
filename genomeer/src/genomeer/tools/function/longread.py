"""
genomeer/src/genomeer/tools/function/longread.py
==================================================
Wrappers for long-read (Nanopore/PacBio) assembly and QC tools:
  - Flye       : run_flye       — de-novo assembler, metagenome-capable (--meta)
  - Unicycler  : run_unicycler  — single-isolate hybrid/long/short assembler
  - Filtlong   : run_filtlong   — length/quality read filtering pre-assembly
  - NanoPlot   : run_nanoplot   — long-read QC report

Same execution pattern as viromics.py: invoked via `micromamba run -p <prefix>`
so these tools resolve correctly regardless of the calling process's own PATH
(they live in meta-env1, a separate conda env).

NOTE (same convention as every other tools/function/*.py module): generated
scripts NEVER import this module directly (genomeer.* is not installed in the
execution envs). The LLM reads the paired description as a recipe and writes
equivalent standalone code. This file exists for documentation, discoverability,
and unit testing of the exact logic the description prescribes.
"""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

_META_ENV = os.environ.get("GENOMEER_META_ENV", "meta-env1")


def _micromamba_bin() -> str:
    from genomeer.runtime.env_manager import ensure_micromamba
    return str(ensure_micromamba())


def _env_prefix(env_name: str) -> Path:
    from genomeer.runtime.env_manager import ENVS_DIR
    return ENVS_DIR / env_name


def _run(argv: List[str], env_name: str = _META_ENV, timeout: int = 7200) -> subprocess.CompletedProcess:
    """Run argv inside micromamba env using -p <prefix>."""
    mm = _micromamba_bin()
    prefix = _env_prefix(env_name)
    cmd = [mm, "run", "-p", str(prefix)] + argv

    env = dict(os.environ)
    env.pop("CONDA_PREFIX", None)
    from genomeer.runtime.env_manager import ENVS_DIR
    env["MAMBA_ROOT_PREFIX"] = str(ENVS_DIR.parent.parent)

    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)


# ===========================================================================
# Flye — long-read de-novo assembler (metagenome-capable)
# ===========================================================================

_FLYE_READ_TYPES = {"nano-raw", "nano-hq", "nano-corr", "pacbio-raw", "pacbio-hifi", "pacbio-corr"}


def run_flye(
    reads_fastq: str,
    output_dir: str,
    read_type: str = "nano-hq",
    meta: bool = True,
    threads: int = 8,
    timeout: int = 14400,
) -> Dict[str, Any]:
    """Flye long-read de-novo assembler (Nanopore/PacBio), metagenome-capable."""
    if read_type not in _FLYE_READ_TYPES:
        raise ValueError(f"read_type must be one of {sorted(_FLYE_READ_TYPES)}")
    os.makedirs(output_dir, exist_ok=True)

    cmd = ["flye", f"--{read_type}", reads_fastq, "--out-dir", output_dir, "--threads", str(threads)]
    if meta:
        cmd.append("--meta")

    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"flye failed (rc={proc.returncode}): {(proc.stderr or '')[-2000:]}")

    assembly = Path(output_dir) / "assembly.fasta"
    info = Path(output_dir) / "assembly_info.txt"
    n_contigs = 0
    n50: Optional[int] = None
    if info.exists():
        lengths: List[int] = []
        with open(info) as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                try:
                    lengths.append(int(row["length"]))
                except (KeyError, ValueError, TypeError):
                    continue
        n_contigs = len(lengths)
        if lengths:
            lengths.sort(reverse=True)
            total = sum(lengths)
            running = 0
            for length in lengths:
                running += length
                if running >= total / 2:
                    n50 = length
                    break

    return {
        "ok": True,
        "returncode": proc.returncode,
        "assembly_fasta": str(assembly) if assembly.exists() else None,
        "assembly_info": str(info) if info.exists() else None,
        "n_contigs": n_contigs,
        "n50_bp": n50,
        "output_dir": output_dir,
    }


# ===========================================================================
# Unicycler — single-isolate hybrid / long-only / short-only assembler
# ===========================================================================

def run_unicycler(
    output_dir: str,
    read1: Optional[str] = None,
    read2: Optional[str] = None,
    long_reads: Optional[str] = None,
    threads: int = 8,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """Unicycler single-isolate bacterial assembler (hybrid / long-only / short-only)."""
    if not long_reads and not (read1 and read2):
        raise ValueError("Provide long_reads, or read1+read2, or all three for hybrid mode.")
    os.makedirs(output_dir, exist_ok=True)

    cmd = ["unicycler", "-o", output_dir, "-t", str(threads)]
    if read1 and read2:
        cmd += ["-1", read1, "-2", read2]
    if long_reads:
        cmd += ["-l", long_reads]

    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"unicycler failed (rc={proc.returncode}): {(proc.stderr or '')[-2000:]}")

    assembly = Path(output_dir) / "assembly.fasta"
    gfa = Path(output_dir) / "assembly.gfa"
    n_contigs = 0
    n_circular = 0
    if assembly.exists():
        with open(assembly) as f:
            for line in f:
                if line.startswith(">"):
                    n_contigs += 1
                    if "circular=true" in line:
                        n_circular += 1

    return {
        "ok": True,
        "returncode": proc.returncode,
        "assembly_fasta": str(assembly) if assembly.exists() else None,
        "assembly_gfa": str(gfa) if gfa.exists() else None,
        "n_contigs": n_contigs,
        "n_circular": n_circular,
        "output_dir": output_dir,
    }


# ===========================================================================
# Filtlong — long-read length/quality filtering (pre-assembly)
# ===========================================================================

def run_filtlong(
    input_fastq: str,
    output_fastq: str,
    min_length: int = 1000,
    keep_percent: float = 90.0,
    target_bases: Optional[int] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Filtlong long-read length/quality filtering. Writes filtered reads to output_fastq."""
    os.makedirs(os.path.dirname(output_fastq) or ".", exist_ok=True)

    args = ["filtlong", "--min_length", str(min_length), "--keep_percent", str(keep_percent)]
    if target_bases:
        args += ["--target_bases", str(target_bases)]
    args.append(input_fastq)

    mm = _micromamba_bin()
    prefix = _env_prefix(_META_ENV)
    cmd = [mm, "run", "-p", str(prefix)] + args

    with open(output_fastq, "wb") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"filtlong failed (rc={proc.returncode}): {stderr[-2000:]}")

    read_count = 0
    if os.path.exists(output_fastq):
        with open(output_fastq) as f:
            read_count = sum(1 for i, _ in enumerate(f) if i % 4 == 0)

    return {
        "ok": True,
        "returncode": proc.returncode,
        "output_fastq": output_fastq,
        "read_count": read_count,
    }


# ===========================================================================
# NanoPlot — long-read QC report
# ===========================================================================

def run_nanoplot(
    reads_fastq: str,
    output_dir: str,
    threads: int = 4,
    timeout: int = 600,
) -> Dict[str, Any]:
    """NanoPlot long-read QC report (fastqc equivalent for ONT/PacBio)."""
    os.makedirs(output_dir, exist_ok=True)
    cmd = ["NanoPlot", "--fastq", reads_fastq, "--outdir", output_dir, "-t", str(threads)]

    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"NanoPlot failed (rc={proc.returncode}): {(proc.stderr or '')[-2000:]}")

    stats_file = Path(output_dir) / "NanoStats.txt"
    stats: Dict[str, float] = {}
    if stats_file.exists():
        with open(stats_file) as f:
            for line in f:
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().replace(",", "")
                try:
                    stats[key] = float(val)
                except ValueError:
                    continue

    return {
        "ok": True,
        "returncode": proc.returncode,
        "nanostats_txt": str(stats_file) if stats_file.exists() else None,
        "report_html": str(Path(output_dir) / "NanoPlot-report.html"),
        "stats": stats,
        "output_dir": output_dir,
    }
