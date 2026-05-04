"""
Genomeer — Metagenomics Tool Functions
=======================================
Real CLI wrappers for the `meta-env1` micromamba environment.
Every function follows the same contract as genomeer/src/genomeer/tools/function/basic.py:
  - Pure Python callable importable by the agent
  - Returns a dict with meaningful keys for downstream use
  - Writes all output files into `output_dir`
  - Raises RuntimeError with formatted message on failure

Coverage (28 tools):
  QC          : run_fastp, run_multiqc, run_nanostat, run_fastqc
  Assembly    : run_metaspades, run_megahit, run_flye
  Mapping     : run_minimap2, run_bowtie2, run_bwa_mem, compute_coverage_samtools, sort_index_bam
  Taxonomy    : run_kraken2, run_bracken, run_metaphlan4, run_gtdbtk, run_krona
  Binning     : run_metabat2, run_das_tool, run_checkm2
  Annotation  : run_prokka, run_prodigal, run_diamond, run_hmmer, run_humann3
  AMR/Virulence: run_amrfinderplus, run_rgi_card
  Stats/Viz   : run_microbiome_diversity (Python, via scikit-bio + matplotlib)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

# ---------------------------------------------------------------------------
# Internal helpers — same pattern as helper.py _run_in_env
# ---------------------------------------------------------------------------

_META_ENV = "meta-env1"
_BIO_ENV  = "bio-agent-env1"


def _micromamba_bin() -> str:
    """Return micromamba executable path (mirrors env_manager.ensure_micromamba logic)."""
    from genomeer.runtime.env_manager import ensure_micromamba
    return str(ensure_micromamba())


def _env_prefix(env_name: str) -> Path:
    from genomeer.runtime.env_manager import ENVS_DIR
    return ENVS_DIR / env_name


def _run(argv: List[str], env_name: str = _META_ENV, timeout: int = 7200,
         extra_env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run argv inside micromamba env and return CompletedProcess."""
    mm = _micromamba_bin()
    prefix = _env_prefix(env_name)
    cmd = [mm, "run", "-p", str(prefix), *argv]
    print("CMD:", " ".join(cmd))
    env = dict(os.environ)
    env.pop("CONDA_PREFIX", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _assert_ok(proc: subprocess.CompletedProcess, label: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {proc.returncode})\n"
            f"--- STDOUT ---\n{proc.stdout[-4000:]}\n"
            f"--- STDERR ---\n{proc.stderr[-4000:]}"
        )


def _ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ===========================================================================
# QC & PREPROCESSING
# ===========================================================================

def validate_fastq_input(
    fastq_path: str,
    min_reads: int = 1000,
) -> Dict[str, Any]:
    """
    Quick validation of a FASTQ file before running the pipeline.
    Checks: file exists, non-empty, valid FASTQ format (first 4 lines),
    and estimates read count.
    Returns dict(valid, n_reads_estimated, format_ok, file_size_mb, message).
    """
    p = Path(fastq_path)
    if not p.exists():
        return {"valid": False, "message": f"File not found: {fastq_path}"}
    size_mb = p.stat().st_size / (1024 ** 2)
    if size_mb == 0:
        return {"valid": False, "message": f"File is empty: {fastq_path}"}

    import gzip
    try:
        opener = gzip.open if fastq_path.endswith(".gz") else open
        with opener(fastq_path, "rt", errors="replace") as fh:
            lines = [fh.readline() for _ in range(8)]
        if not lines[0].startswith("@"):
            return {"valid": False, "message": "Not a valid FASTQ: first line must start with '@'"}
        if not lines[2].startswith("+"):
            return {"valid": False, "message": "Not a valid FASTQ: third line must start with '+'"}
    except Exception as e:
        return {"valid": False, "message": f"Could not read file: {e}"}

    # Estimate read count from file size (rough: ~250 bytes per read compressed)
    n_reads_est = int(size_mb * 1024 * 1024 / 250)
    warn = n_reads_est < min_reads

    return {
        "valid": True,
        "format_ok": True,
        "file_size_mb": round(size_mb, 2),
        "n_reads_estimated": n_reads_est,
        "message": (
            f"Valid FASTQ. ~{n_reads_est:,} reads estimated. "
            + (f"WARNING: fewer than {min_reads} reads, may be insufficient for assembly." if warn else "")
        ),
    }


def run_host_decontamination(
    input_r1: str,
    input_r2: str,
    output_dir: str,
    host_index: str,      # path to bowtie2 index prefix (e.g. /db/hg38/hg38)
    threads: int = 8,
) -> Dict[str, Any]:
    """
    Remove host reads using Bowtie2.
    Outputs only unaligned reads (non-host = microbial) for downstream analysis.
    Returns dict with clean_r1, clean_r2, host_reads_pct, n_reads_after.
    """
    import re
    out = _ensure_dir(output_dir)
    clean_r1 = str(out / "host_removed_R1.fastq.gz")
    clean_r2 = str(out / "host_removed_R2.fastq.gz")
    sam_out = "/dev/null"

    cmd = [
        "bowtie2", "-x", host_index,
        "-1", input_r1, "-2", input_r2,
        "--un-conc-gz", str(out / "host_removed_R%.fastq.gz"),
        "-S", sam_out,
        "--threads", str(threads),
        "--very-sensitive"
    ]
    proc = _run(cmd, timeout=7200)
    _assert_ok(proc, "bowtie2_host_decontam")

    host_pct = 0.0
    m = re.search(r"(\d+\.\d+)%\s+overall alignment rate", proc.stderr)
    if m:
        host_pct = float(m.group(1))

    return {
        "clean_r1": clean_r1,
        "clean_r2": clean_r2,
        "host_alignment_pct": host_pct,
        "microbial_pct": round(100.0 - host_pct, 2),
        "stdout": proc.stdout[-1000:],
    }


def run_fastp(
    input_r1: str,
    output_dir: str,
    input_r2: Optional[str] = None,
    threads: int = 4,
    min_quality: int = 20,
    min_length: int = 50,
    detect_adapter_for_pe: bool = True,
    json_report: bool = True,
    html_report: bool = True,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run fastp for adapter trimming and quality control on Illumina reads.
    Supports both single-end and paired-end FASTQ (optionally gzipped).
    Returns a dict with paths to trimmed reads, JSON stats, and HTML report.
    """
    out = _ensure_dir(output_dir)
    stem = Path(input_r1).stem.replace(".fastq", "").replace(".fq", "")

    out_r1 = str(out / f"{stem}_R1_clean.fastq.gz")
    cmd = ["fastp", "-i", input_r1, "-o", out_r1, "-w", str(threads),
           "-q", str(min_quality), "-l", str(min_length)]

    out_r2 = None
    if input_r2:
        out_r2 = str(out / f"{stem}_R2_clean.fastq.gz")
        cmd += ["-I", input_r2, "-O", out_r2]
        if detect_adapter_for_pe:
            cmd += ["--detect_adapter_for_pe"]

    json_path = str(out / f"{stem}_fastp.json") if json_report else None
    html_path = str(out / f"{stem}_fastp.html") if html_report else None
    if json_path:
        cmd += ["-j", json_path]
    if html_path:
        cmd += ["-h", html_path]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd)
    _assert_ok(proc, "fastp")

    stats = {}
    if json_path and Path(json_path).exists():
        with open(json_path) as f:
            stats = json.load(f)

    # FIX G8: expose q30_rate at top-level so quality_gate can read it directly
    # fastp JSON structure: stats["summary"]["before_filtering"]["q30_rate"]
    q30_before = 0.0
    q30_after  = 0.0
    try:
        q30_before = float(stats["summary"]["before_filtering"]["q30_rate"])
        q30_after  = float(stats["summary"]["after_filtering"]["q30_rate"])
    except (KeyError, TypeError, ValueError):
        pass

    return {
        "out_r1": out_r1,
        "out_r2": out_r2,
        "json_report": json_path,
        "html_report": html_path,
        "summary": stats.get("summary", {}),
        "q30_rate": q30_after if q30_after > 0 else q30_before,  # quality gate reads this key
        "q30_rate_before": q30_before,
        "q30_rate_after":  q30_after,
    }


def run_fastqc(
    input_files: List[str],
    output_dir: str,
    threads: int = 4,
) -> Dict[str, Any]:
    """
    Run FastQC quality assessment on one or more FASTQ files.
    Returns dict with output_dir and list of generated HTML report paths.
    """
    out = _ensure_dir(output_dir)
    cmd = ["fastqc", "--outdir", str(out), "--threads", str(threads)] + input_files
    proc = _run(cmd)
    _assert_ok(proc, "fastqc")
    html_reports = sorted(str(p) for p in out.glob("*_fastqc.html"))
    return {"output_dir": str(out), "html_reports": html_reports, "stdout": proc.stdout}


def run_multiqc(
    input_dir: str,
    output_dir: str,
    report_name: str = "multiqc_report",
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run MultiQC to aggregate QC reports from fastp, FastQC, Kraken2, etc.
    Scans input_dir recursively and produces an interactive HTML summary.
    Returns dict with html_report path and data_dir.
    """
    out = _ensure_dir(output_dir)
    cmd = ["multiqc", input_dir, "--outdir", str(out), "--filename", report_name, "--force"]
    if extra_args:
        cmd += extra_args.split()
    proc = _run(cmd)
    _assert_ok(proc, "multiqc")
    html = str(out / f"{report_name}.html")
    return {"html_report": html, "data_dir": str(out / f"{report_name}_data"), "stdout": proc.stdout}


def run_nanostat(
    input_fastq: str,
    output_dir: str,
    threads: int = 4,
) -> Dict[str, Any]:
    """
    Run NanoStat to compute quality statistics on Oxford Nanopore long reads.
    Returns dict with stats_file path and parsed key metrics (N50, mean quality, etc.).
    """
    out = _ensure_dir(output_dir)
    stats_file = str(out / "nanostat_report.txt")
    cmd = ["NanoStat", "--fastq", input_fastq, "--outdir", str(out),
           "--name", "nanostat_report.txt", "--threads", str(threads)]
    proc = _run(cmd)
    _assert_ok(proc, "NanoStat")
    return {"stats_file": stats_file, "stdout": proc.stdout}


# ===========================================================================
# ASSEMBLY
# ===========================================================================

def run_metaspades(
    output_dir: str,
    reads_r1: Optional[str] = None,
    reads_r2: Optional[str] = None,
    reads_single: Optional[str] = None,
    threads: int = 8,
    memory_gb: int = 16,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run metaSPAdes for metagenome de-novo assembly from Illumina short reads.
    Supports paired-end (reads_r1 + reads_r2) or single-end (reads_single).
    Returns dict with contigs_fasta, scaffolds_fasta, assembly_graph, and log.
    """
    out = _ensure_dir(output_dir)
    # FIX G11: --meta flag is required for metagenome mode in metaSPAdes
    cmd = ["metaspades.py", "--meta", "-o", str(out), "-t", str(threads), "-m", str(memory_gb)]
    if reads_r1:
        cmd += ["-1", reads_r1]
    if reads_r2:
        cmd += ["-2", reads_r2]
    if reads_single:
        cmd += ["-s", reads_single]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd, timeout=21600)
    _assert_ok(proc, "metaSPAdes")

    contigs = str(out / "contigs.fasta")
    scaffolds = str(out / "scaffolds.fasta")
    return {
        "contigs_fasta": contigs if Path(contigs).exists() else None,
        "scaffolds_fasta": scaffolds if Path(scaffolds).exists() else None,
        "assembly_graph": str(out / "assembly_graph.fastg"),
        "log": str(out / "spades.log"),
        "output_dir": str(out),
    }


def run_megahit(
    output_dir: str,
    reads_r1: Optional[str] = None,
    reads_r2: Optional[str] = None,
    reads_single: Optional[str] = None,
    threads: int = 8,
    memory_fraction: float = 0.5,
    min_contig_len: int = 500,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run MEGAHIT for fast and memory-efficient metagenome assembly.
    More suitable than metaSPAdes for very large datasets or low-memory systems.
    Returns dict with contigs_fasta path.
    """
    out = _ensure_dir(output_dir)
    final_out = out / "megahit_out"
    if final_out.exists():
        shutil.rmtree(final_out)

    cmd = ["megahit", "-o", str(final_out), "-t", str(threads),
           "--memory", str(memory_fraction), "--min-contig-len", str(min_contig_len)]
    if reads_r1:
        cmd += ["-1", reads_r1]
    if reads_r2:
        cmd += ["-2", reads_r2]
    if reads_single:
        cmd += ["-r", reads_single]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd, timeout=21600)
    _assert_ok(proc, "MEGAHIT")

    contigs = str(final_out / "final.contigs.fa")
    return {
        "contigs_fasta": contigs if Path(contigs).exists() else None,
        "output_dir": str(final_out),
        "log": str(final_out / "log"),
    }


def run_flye(
    input_reads: str,
    output_dir: str,
    read_type: str = "nano-raw",
    genome_size: str = "5m",
    threads: int = 8,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run Flye assembler optimized for Oxford Nanopore or PacBio long reads.
    read_type options: 'nano-raw', 'nano-hq', 'nano-corr', 'pacbio-raw', 'pacbio-hifi'.
    genome_size: estimated metagenome size (e.g. '5m', '100m', '1g').
    Returns dict with assembly_fasta, assembly_info, and log.
    """
    out = _ensure_dir(output_dir)
    cmd = ["flye", f"--{read_type}", input_reads, "--out-dir", str(out),
           "--threads", str(threads), "--meta"]
    if genome_size:
        cmd += ["--genome-size", genome_size]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd, timeout=21600)
    _assert_ok(proc, "Flye")

    assembly = str(out / "assembly.fasta")
    return {
        "assembly_fasta": assembly if Path(assembly).exists() else None,
        "assembly_info": str(out / "assembly_info.txt"),
        "log": str(out / "flye.log"),
        "output_dir": str(out),
    }


# ===========================================================================
# MAPPING & COVERAGE
# ===========================================================================

def run_minimap2(
    reads: str,
    reference: str,
    output_bam: str,
    preset: str = "sr",
    threads: int = 4,
    sort_and_index: bool = True,
) -> Dict[str, Any]:
    """
    Align reads to a reference genome/assembly using minimap2.
    preset: 'sr' (short reads), 'map-ont' (Nanopore), 'map-pb' (PacBio), 'asm5' (assembly).
    If sort_and_index=True, produces a sorted BAM with .bai index.
    Returns dict with bam_path, index_path, and alignment stats.
    """
    out_dir = _ensure_dir(Path(output_bam).parent)
    sam_path = str(out_dir / Path(output_bam).stem) + ".sam"

    cmd_map = ["minimap2", "-ax", preset, "-t", str(threads), reference, reads]
    with open(sam_path, "w") as sam_f:
        proc_map = subprocess.run(
            [_micromamba_bin(), "run", "-p", str(_env_prefix(_META_ENV))] + cmd_map,
            stdout=sam_f, stderr=subprocess.PIPE, text=True, timeout=7200
        )
    _assert_ok(proc_map, "minimap2")

    if sort_and_index:
        proc_sort = _run(["samtools", "sort", "-@", str(threads), "-o", output_bam, sam_path])
        _assert_ok(proc_sort, "samtools sort")
        proc_idx = _run(["samtools", "index", output_bam])
        _assert_ok(proc_idx, "samtools index")
        Path(sam_path).unlink(missing_ok=True)
        index_path = output_bam + ".bai"
    else:
        shutil.move(sam_path, output_bam)
        index_path = None

    flagstat = _run(["samtools", "flagstat", output_bam])
    return {"bam_path": output_bam, "index_path": index_path, "flagstat": flagstat.stdout}


def run_bowtie2(
    reads_r1: str,
    reference_index: str,
    output_bam: str,
    reads_r2: Optional[str] = None,
    threads: int = 4,
    sort_and_index: bool = True,
) -> Dict[str, Any]:
    """
    Align Illumina reads to a reference using Bowtie2. 
    reference_index: path prefix (without .bt2 extension) — use bowtie2-build if needed.
    Returns dict with bam_path, index_path, alignment_rate.
    """
    out_dir = _ensure_dir(Path(output_bam).parent)
    sam_path = str(out_dir / Path(output_bam).stem) + ".sam"

    cmd = ["bowtie2", "-x", reference_index, "-1", reads_r1, "-p", str(threads),
           "--no-unal", "-S", sam_path]
    if reads_r2:
        cmd += ["-2", reads_r2]

    proc = _run(cmd)
    _assert_ok(proc, "bowtie2")
    alignment_rate = [l for l in proc.stderr.splitlines() if "overall alignment rate" in l]

    if sort_and_index:
        _assert_ok(_run(["samtools", "sort", "-@", str(threads), "-o", output_bam, sam_path]), "samtools sort")
        _assert_ok(_run(["samtools", "index", output_bam]), "samtools index")
        Path(sam_path).unlink(missing_ok=True)

    return {
        "bam_path": output_bam,
        "index_path": output_bam + ".bai" if sort_and_index else None,
        "alignment_rate": alignment_rate[0].strip() if alignment_rate else "unknown",
    }


def compute_coverage_samtools(
    bam_path: str,
    output_tsv: str,
    min_mapping_quality: int = 20,
) -> Dict[str, Any]:
    """
    Compute per-contig/chromosome coverage statistics using samtools coverage.
    Produces a TSV with columns: rname, startpos, endpos, numreads, covbases,
    coverage, meandepth, meanbaseq, meanmapq.
    Returns dict with coverage_tsv path and summary stats.
    """
    _ensure_dir(Path(output_tsv).parent)
    cmd = ["samtools", "coverage", "-q", str(min_mapping_quality), bam_path, "-o", output_tsv]
    proc = _run(cmd)
    _assert_ok(proc, "samtools coverage")

    total_cov = 0.0
    n_contigs = 0
    try:
        with open(output_tsv) as f:
            next(f)
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 6:
                    total_cov += float(parts[5])
                    n_contigs += 1
    except Exception:
        pass

    return {
        "coverage_tsv": output_tsv,
        "mean_coverage_across_contigs": round(total_cov / n_contigs, 2) if n_contigs else 0,
        "n_contigs": n_contigs,
    }


def sort_index_bam(bam_path: str, threads: int = 4) -> Dict[str, Any]:
    """
    Sort and index a BAM file using samtools. Produces sorted BAM and .bai index.
    Returns dict with sorted_bam and index_path.
    """
    sorted_bam = bam_path.replace(".bam", ".sorted.bam")
    _assert_ok(_run(["samtools", "sort", "-@", str(threads), "-o", sorted_bam, bam_path]), "samtools sort")
    _assert_ok(_run(["samtools", "index", sorted_bam]), "samtools index")
    return {"sorted_bam": sorted_bam, "index_path": sorted_bam + ".bai"}


# ===========================================================================
# TAXONOMIC CLASSIFICATION
# ===========================================================================

def run_kraken2(
    output_dir: str,
    reads_r1: str,
    db_path: str,
    reads_r2: Optional[str] = None,
    threads: int = 4,
    confidence: float = 0.1,
    report_minimizer_data: bool = False,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run Kraken2 for k-mer based taxonomic classification of metagenomic reads.
    Requires a pre-built Kraken2 database (db_path). Use MiniKraken2 for testing.
    Returns dict with report, output, and classified/unclassified counts.
    """
    out = _ensure_dir(output_dir)
    report = str(out / "kraken2_report.txt")
    output_file = str(out / "kraken2_output.txt")

    cmd = ["kraken2", "--db", db_path, "--threads", str(threads),
           "--confidence", str(confidence),
           "--report", report, "--output", output_file]
    if reads_r2:
        cmd += ["--paired", reads_r1, reads_r2]
    else:
        cmd += [reads_r1]
    if report_minimizer_data:
        cmd += ["--report-minimizer-data"]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd)
    _assert_ok(proc, "Kraken2")

    # FIX G9: parse classified_pct as float so quality_gate can apply threshold
    classified_pct: Optional[float] = None
    classification_summary_str = ""
    for line in proc.stderr.splitlines():
        if "classified" in line.lower():
            classification_summary_str = line.strip()
            import re as _re
            m = _re.search(r"([0-9]+\.[0-9]+)%\s+of\s+sequences\s+classified", line, _re.IGNORECASE)
            if m:
                try:
                    classified_pct = float(m.group(1))
                except ValueError:
                    pass

    # FIX: Enregistrer la DB kraken2 avec checksum
    try:
        from genomeer.utils.version_tracker import VersionTracker
        VersionTracker().record_db("kraken2_standard", db_path, compute_checksum=True)
    except Exception:
        pass

    return {
        "report": report,
        "output": output_file,
        "db_used": db_path,
        "classified_pct": classified_pct,            # FIX G9: float for quality gate
        "classification_summary": classification_summary_str or proc.stderr[:500],
    }


def run_bracken(
    kraken2_report: str,
    db_path: str,
    output_dir: str,
    level: str = "S",
    read_length: int = 150,
    threshold: int = 10,
) -> Dict[str, Any]:
    """
    Run Bracken to re-estimate species/genus abundances from Kraken2 reports.
    level: 'S' (species), 'G' (genus), 'F' (family), 'O' (order), 'C' (class), 'P' (phylum).
    Returns dict with bracken_output and bracken_report paths.
    """
    out = _ensure_dir(output_dir)
    bracken_out = str(out / f"bracken_{level}.txt")
    bracken_report = str(out / f"bracken_{level}_report.txt")

    cmd = ["bracken", "-d", db_path, "-i", kraken2_report,
           "-o", bracken_out, "-r", str(read_length),
           "-l", level, "-t", str(threshold),
           "-w", bracken_report]
    proc = _run(cmd)
    _assert_ok(proc, "Bracken")

    return {
        "bracken_output": bracken_out,
        "bracken_report": bracken_report,
        "level": level,
        "stdout": proc.stdout,
    }


def run_metaphlan4(
    input_reads: str,
    output_dir: str,
    threads: int = 4,
    db_path: Optional[str] = None,
    input_type: str = "fastq",
    analysis_type: str = "rel_ab_w_read_stats",
    bowtie2out: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run MetaPhlAn4 for marker-gene based taxonomic and functional profiling.
    Uses a curated database of clade-specific marker genes (auto-downloads on first run).
    input_type: 'fastq', 'fasta', 'bowtie2out', 'sam'.
    Returns dict with profile_tsv and bowtie2out paths.
    """
    out = _ensure_dir(output_dir)
    profile_tsv = str(out / "metaphlan4_profile.tsv")
    bt2_out = bowtie2out or str(out / "metaphlan4_bowtie2.bz2")

    cmd = ["metaphlan", input_reads, "--input_type", input_type,
           "--nproc", str(threads), "--output_file", profile_tsv,
           "-t", analysis_type, "--bowtie2out", bt2_out]
    if db_path:
        cmd += ["--index", db_path]

    proc = _run(cmd, timeout=14400)
    _assert_ok(proc, "MetaPhlAn4")

    return {
        "profile_tsv": profile_tsv,
        "bowtie2out": bt2_out,
        "output_dir": str(out),
        "stdout": proc.stdout,
    }


def run_gtdbtk(
    bins_dir: str,
    output_dir: str,
    db_path: str,
    extension: str = "fa",
    threads: int = 8,
    skip_ani_screen: bool = False,
) -> Dict[str, Any]:
    """
    Run GTDB-Tk to classify metagenome-assembled genomes (MAGs) with GTDB taxonomy.
    bins_dir: directory containing MAG FASTA files (one genome per file).
    db_path: path to GTDB-Tk reference database (GTDBTK_DATA_PATH).
    Returns dict with summary_tsv, classify_dir, and failed_genomes.
    """
    out = _ensure_dir(output_dir)
    cmd = ["gtdbtk", "classify_wf",
           "--genome_dir", bins_dir,
           "--out_dir", str(out),
           "--extension", extension,
           "--cpus", str(threads),
           "--prefix", "gtdbtk"]
    if skip_ani_screen:
        cmd += ["--skip_ani_screen"]

    extra = {"GTDBTK_DATA_PATH": db_path}
    proc = _run(cmd, extra_env=extra, timeout=21600)
    _assert_ok(proc, "GTDB-Tk")

    summary = str(out / "gtdbtk.bac120.summary.tsv")
    return {
        "summary_tsv": summary if Path(summary).exists() else None,
        "ar53_summary": str(out / "gtdbtk.ar53.summary.tsv"),
        "classify_dir": str(out),
        "stdout": proc.stdout[-2000:],
    }


def run_krona(
    kraken2_report: str,
    output_html: str,
    input_type: str = "kraken2",
) -> Dict[str, Any]:
    """
    Generate an interactive Krona pie chart from a Kraken2 or Bracken report.
    input_type: 'kraken2' or 'text' (tab-separated count+taxonomy).
    Returns dict with html_path.
    """
    _ensure_dir(Path(output_html).parent)
    if input_type == "kraken2":
        cmd = ["ktImportTaxonomy", "-t", "5", "-m", "3", "-o", output_html, kraken2_report]
    else:
        cmd = ["ktImportText", "-o", output_html, kraken2_report]
    proc = _run(cmd)
    _assert_ok(proc, "Krona")
    return {"html_path": output_html}


# ===========================================================================
# BINNING
# ===========================================================================

def run_metabat2(
    assembly_fasta: str,
    output_dir: str,
    bam_paths: Optional[List[str]] = None,
    min_contig: int = 2500,
    threads: int = 8,
) -> Dict[str, Any]:
    """
    Run MetaBAT2 to bin assembled contigs into metagenome-assembled genomes (MAGs).
    bam_paths: list of BAM files (sorted+indexed) for coverage information.
    If bam_paths is None, binning is done by composition only (less accurate).
    Returns dict with bins_dir, n_bins, and depth_file.
    """
    out = _ensure_dir(output_dir)
    depth_file = str(out / "depth.txt")

    if bam_paths:
        proc_depth = _run(
            ["jgi_summarize_bam_contig_depths", "--outputDepth", depth_file] + bam_paths
        )
        _assert_ok(proc_depth, "jgi_summarize_bam_contig_depths")

    bins_prefix = str(out / "bin")
    cmd = ["metabat2", "-i", assembly_fasta, "-o", bins_prefix,
           "-m", str(min_contig), "-t", str(threads), "--unbinned"]
    if bam_paths and Path(depth_file).exists():
        cmd += ["-a", depth_file]

    proc = _run(cmd)
    _assert_ok(proc, "MetaBAT2")

    bins = sorted(Path(out).glob("bin.*.fa"))
    return {
        "bins_dir": str(out),
        "n_bins": len(bins),
        "bin_files": [str(b) for b in bins],
        "depth_file": depth_file if Path(depth_file).exists() else None,
    }


def run_das_tool(
    contigs_fasta: str,
    output_dir: str,
    bins_scaffolds_tsv_list: List[str],
    binner_names: List[str],
    threads: int = 8,
    score_threshold: float = 0.5,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run DAS_Tool to dereplicate and refine bins from multiple binning algorithms.
    bins_scaffolds_tsv_list: list of scaffold-to-bin TSV files (one per binner).
    binner_names: list of binner names matching bins_scaffolds_tsv_list order.
    Returns dict with refined_bins_dir, summary_tsv, and n_refined_bins.
    """
    out = _ensure_dir(output_dir)
    prefix = str(out / "dastool")
    bins_str = ",".join(bins_scaffolds_tsv_list)
    names_str = ",".join(binner_names)

    cmd = ["DAS_Tool", "-i", bins_str, "-l", names_str, "-c", contigs_fasta,
           "-o", prefix, "--threads", str(threads),
           "--score_threshold", str(score_threshold), "--write_bins"]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd, timeout=14400)
    _assert_ok(proc, "DAS_Tool")

    bins_dir = prefix + "_DASTool_bins"
    summary = prefix + "_DASTool_summary.tsv"
    n_bins = len(list(Path(bins_dir).glob("*.fa"))) if Path(bins_dir).exists() else 0

    return {
        "refined_bins_dir": bins_dir if Path(bins_dir).exists() else str(out),
        "summary_tsv": summary if Path(summary).exists() else None,
        "n_refined_bins": n_bins,
        "stdout": proc.stdout[-2000:],
    }


def run_checkm2(
    bins_dir: str,
    output_dir: str,
    threads: int = 8,
    db_path: Optional[str] = None,
    extension: str = "fa",
) -> Dict[str, Any]:
    """
    Run CheckM2 to assess completeness and contamination of MAGs using machine learning.
    Much faster than CheckM1 and does not require a reference database download.
    Returns dict with quality_report_tsv and summary statistics.
    """
    out = _ensure_dir(output_dir)
    cmd = ["checkm2", "predict", "--input", bins_dir, "--output-directory", str(out),
           "--threads", str(threads), "--extension", extension, "--force"]
    if db_path:
        cmd += ["--database_path", db_path]

    proc = _run(cmd, timeout=14400)
    _assert_ok(proc, "CheckM2")

    report = str(out / "quality_report.tsv")
    stats = {"completeness": [], "contamination": []}
    try:
        with open(report) as f:
            next(f)
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    stats["completeness"].append(float(parts[1]))
                    stats["contamination"].append(float(parts[2]))
    except Exception:
        pass

    n = len(stats["completeness"])
    return {
        "quality_report_tsv": report if Path(report).exists() else None,
        "n_bins_assessed": n,
        "mean_completeness": round(sum(stats["completeness"]) / n, 1) if n else None,
        "mean_contamination": round(sum(stats["contamination"]) / n, 1) if n else None,
        "output_dir": str(out),
    }


# ===========================================================================
# FUNCTIONAL ANNOTATION
# ===========================================================================

def run_prokka(
    contigs_fasta: str,
    output_dir: str,
    sample_name: str = "metagenome",
    kingdom: str = "Bacteria",
    threads: int = 4,
    metagenome: bool = True,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run Prokka for rapid prokaryotic genome annotation of assembled contigs or MAGs.
    metagenome=True enables Prokka's metagenome mode (shorter minimum ORF length).
    Returns dict with gff, faa (proteins), ffn (CDS nucleotides), and tsv annotation table.
    """
    out = _ensure_dir(output_dir)
    prefix = str(out / sample_name)
    cmd = ["prokka", "--outdir", str(out), "--prefix", sample_name,
           "--kingdom", kingdom, "--cpus", str(threads), "--force"]
    if metagenome:
        cmd += ["--metagenome"]
    if extra_args:
        cmd += extra_args.split()
    cmd += [contigs_fasta]

    proc = _run(cmd, timeout=14400)
    _assert_ok(proc, "Prokka")

    return {
        "gff": f"{prefix}.gff",
        "faa": f"{prefix}.faa",
        "ffn": f"{prefix}.ffn",
        "tsv": f"{prefix}.tsv",
        "gbk": f"{prefix}.gbk",
        "txt_stats": f"{prefix}.txt",
        "output_dir": str(out),
    }


def run_prodigal(
    input_fasta: str,
    output_dir: str,
    mode: str = "meta",
    output_format: str = "gff",
) -> Dict[str, Any]:
    """
    Run Prodigal for ab-initio gene prediction in prokaryotic sequences.
    mode: 'meta' (metagenomics), 'single' (isolated genome), 'anon' (anonymous sequences).
    Returns dict with gene predictions GFF/GBK, protein FASTA, and nucleotide FASTA.
    """
    out = _ensure_dir(output_dir)
    stem = Path(input_fasta).stem
    coords_file = str(out / f"{stem}.{output_format}")
    proteins_faa = str(out / f"{stem}_proteins.faa")
    genes_fna = str(out / f"{stem}_genes.fna")

    cmd = ["prodigal", "-i", input_fasta, "-p", mode,
           "-f", output_format, "-o", coords_file,
           "-a", proteins_faa, "-d", genes_fna]

    proc = _run(cmd)
    _assert_ok(proc, "Prodigal")

    return {
        "coords_file": coords_file,
        "proteins_faa": proteins_faa,
        "genes_fna": genes_fna,
        "stdout": proc.stdout,
    }


def run_diamond(
    query_fasta: str,
    db_path: str,
    output_dir: str,
    mode: str = "blastp",
    threads: int = 8,
    max_target_seqs: int = 5,
    evalue: float = 1e-5,
    output_format: str = "6 qseqid sseqid pident length evalue bitscore stitle",
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run DIAMOND for ultra-fast protein/translated nucleotide sequence alignment.
    mode: 'blastp' (protein vs protein DB) or 'blastx' (nucleotide vs protein DB).
    db_path: pre-built DIAMOND database (.dmnd). Use 'diamond makedb' to create.
    Returns dict with hits_tsv and summary statistics.
    """
    out = _ensure_dir(output_dir)
    hits_tsv = str(out / "diamond_hits.tsv")

    cmd = ["diamond", mode, "-d", db_path, "-q", query_fasta,
           "-o", hits_tsv, "-p", str(threads),
           "-k", str(max_target_seqs), "-e", str(evalue),
           "--outfmt"] + output_format.split()
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd, timeout=14400)
    _assert_ok(proc, "DIAMOND")

    n_hits = 0
    try:
        with open(hits_tsv) as f:
            n_hits = sum(1 for _ in f)
    except Exception:
        pass

    return {"hits_tsv": hits_tsv, "n_hits": n_hits, "stdout": proc.stdout}


def run_hmmer(
    query_fasta: str,
    hmm_db: str,
    output_dir: str,
    threads: int = 8,
    evalue: float = 1e-5,
    program: str = "hmmsearch",
) -> Dict[str, Any]:
    """
    Run HMMER for protein family annotation using hidden Markov model profiles.
    program: 'hmmsearch' (query=HMM, target=sequences) or 'hmmscan' (reverse).
    hmm_db: path to HMM profile database (e.g. Pfam-A.hmm, TIGRFAM).
    Returns dict with domtblout, tblout, and n_hits.
    """
    out = _ensure_dir(output_dir)
    tblout = str(out / "hmmer_tblout.txt")
    domtblout = str(out / "hmmer_domtblout.txt")
    stdout_file = str(out / "hmmer_stdout.txt")

    cmd = [program, "--cpu", str(threads), "-E", str(evalue),
           "--tblout", tblout, "--domtblout", domtblout,
           hmm_db, query_fasta]

    proc = _run(cmd, timeout=14400)
    _assert_ok(proc, f"HMMER/{program}")

    with open(stdout_file, "w") as f:
        f.write(proc.stdout)

    n_hits = 0
    try:
        with open(tblout) as f:
            n_hits = sum(1 for l in f if not l.startswith("#"))
    except Exception:
        pass

    return {"tblout": tblout, "domtblout": domtblout, "n_hits": n_hits}


def run_humann3(
    input_reads: str,
    output_dir: str,
    threads: int = 4,
    nucleotide_db: Optional[str] = None,
    protein_db: Optional[str] = None,
    bypass_nucleotide_search: bool = False,
    extra_args: str = "",
) -> Dict[str, Any]:
    """
    Run HUMAnN3 for functional profiling of metagenomes (gene families, pathways).
    Produces pathway abundance, pathway coverage, and gene family tables.
    Input can be FASTQ reads or pre-classified sequences from MetaPhlAn.
    Returns dict with pathabundance, pathcoverage, genefamilies TSV paths.
    """
    out = _ensure_dir(output_dir)
    cmd = ["humann", "--input", input_reads, "--output", str(out),
           "--threads", str(threads)]
    if nucleotide_db:
        cmd += ["--nucleotide-database", nucleotide_db]
    if protein_db:
        cmd += ["--protein-database", protein_db]
    if bypass_nucleotide_search:
        cmd += ["--bypass-nucleotide-search"]
    if extra_args:
        cmd += extra_args.split()

    proc = _run(cmd, timeout=21600)
    _assert_ok(proc, "HUMAnN3")

    stem = Path(input_reads).stem.replace(".fastq", "").replace(".fq", "")
    return {
        "pathabundance_tsv": str(out / f"{stem}_pathabundance.tsv"),
        "pathcoverage_tsv": str(out / f"{stem}_pathcoverage.tsv"),
        "genefamilies_tsv": str(out / f"{stem}_genefamilies.tsv"),
        "output_dir": str(out),
        "stdout": proc.stdout[-2000:],
    }


# ===========================================================================
# AMR & VIRULENCE
# ===========================================================================

def run_amrfinderplus(
    input_fasta: str,
    output_dir: str,
    organism: Optional[str] = None,
    threads: int = 4,
    db_path: Optional[str] = None,
    protein: bool = True,
) -> Dict[str, Any]:
    """
    Run NCBI AMRFinderPlus to identify antimicrobial resistance genes (ARGs),
    stress response genes, and virulence factors in genomic sequences.
    organism: restrict to organism-specific point mutations (e.g. 'Escherichia', 'Klebsiella').
    protein=True: input is protein FASTA; False: nucleotide.
    Returns dict with amr_report_tsv and n_hits.
    """
    out = _ensure_dir(output_dir)
    report = str(out / "amrfinderplus_report.tsv")

    flag = "-p" if protein else "-n"
    cmd = ["amrfinder", flag, input_fasta, "-o", report,
           "--threads", str(threads), "--plus"]
    if organism:
        cmd += ["--organism", organism]
    if db_path:
        cmd += ["--database", db_path]

    proc = _run(cmd, timeout=7200)
    _assert_ok(proc, "AMRFinderPlus")

    n_hits = 0
    try:
        with open(report) as f:
            n_hits = sum(1 for l in f if not l.startswith("Protein")) - 1
    except Exception:
        pass

    if db_path:
        try:
            from genomeer.utils.version_tracker import VersionTracker
            VersionTracker().record_db("amrfinder_db", db_path, compute_checksum=True)
        except Exception:
            pass

    return {"amr_report_tsv": report, "n_hits": max(0, n_hits), "stdout": proc.stdout}


def run_rgi_card(
    input_fasta: str,
    output_dir: str,
    input_type: str = "contig",
    alignment_tool: str = "BLAST",
    db_path: Optional[str] = None,
    threads: int = 4,
    low_quality: bool = False,
) -> Dict[str, Any]:
    """
    Run RGI (Resistance Gene Identifier) against the CARD (Comprehensive Antibiotic
    Resistance Database) to detect ARGs in assembled contigs or protein sequences.
    input_type: 'contig', 'protein', 'read'.
    Returns dict with rgi_tsv, json_report, and n_hits.
    """
    out = _ensure_dir(output_dir)
    prefix = str(out / "rgi_output")

    cmd = ["rgi", "main", "-i", input_fasta, "-o", prefix,
           "-t", input_type, "-a", alignment_tool,
           "-n", str(threads), "--clean"]
    if db_path:
        cmd = ["rgi", "load", "--card_json", db_path] + cmd[1:]
    if low_quality:
        cmd += ["--low_quality"]

    proc = _run(cmd, timeout=7200)
    _assert_ok(proc, "RGI/CARD")

    tsv = prefix + ".txt"
    json_r = prefix + ".json"
    n_hits = 0
    try:
        with open(tsv) as f:
            n_hits = sum(1 for l in f if not l.startswith("ORF_ID")) - 1
    except Exception:
        pass

    if db_path:
        try:
            from genomeer.utils.version_tracker import VersionTracker
            VersionTracker().record_db("card_db", db_path, compute_checksum=True)
        except Exception:
            pass

    return {
        "rgi_tsv": tsv if Path(tsv).exists() else None,
        "json_report": json_r if Path(json_r).exists() else None,
        "n_hits": max(0, n_hits),
    }


# ===========================================================================
# MICROBIOME STATISTICS & VISUALIZATION
# ===========================================================================

def run_microbiome_diversity(
    abundance_table: str,
    output_dir: str,
    sample_metadata: Optional[str] = None,
    grouping_column: Optional[str] = None,
    metrics: List[str] = None,
) -> Dict[str, Any]:
    """
    Compute alpha and beta diversity metrics for microbiome community data.
    Requires scikit-bio, pandas, matplotlib (available in bio-agent-env1).
    abundance_table: TSV/CSV with taxa as rows, samples as columns (or transposed).
    sample_metadata: optional TSV with sample metadata for group comparisons.
    metrics: list from ['shannon', 'observed_otus', 'faith_pd', 'bray_curtis', 'jaccard'].
    Returns dict with alpha_tsv, beta_tsv, and diversity plots.
    """
    if metrics is None:
        metrics = ["shannon", "observed_otus", "bray_curtis"]

    out = _ensure_dir(output_dir)

    script = f"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os, warnings
warnings.filterwarnings('ignore')

out_dir = "{str(out)}"
os.makedirs(out_dir, exist_ok=True)

df = pd.read_csv("{abundance_table}", sep='\\t', index_col=0)
if df.shape[1] > df.shape[0]:
    df = df.T

results = {{"alpha": {{}}, "files": {{}}, "metrics_computed": {list(metrics)}}}

# ---- Alpha diversity ----
from scipy.stats import entropy as sp_entropy

alpha_rows = []
for sample in df.columns:
    counts = df[sample].values.astype(float)
    counts = counts[counts > 0]
    row = {{"sample": sample}}
    if "shannon" in {list(metrics)}:
        props = counts / counts.sum()
        row["shannon"] = float(-np.sum(props * np.log(props)))
    if "observed_otus" in {list(metrics)}:
        row["observed_otus"] = int((counts > 0).sum())
    alpha_rows.append(row)

alpha_df = pd.DataFrame(alpha_rows).set_index("sample")
alpha_tsv = os.path.join(out_dir, "alpha_diversity.tsv")
alpha_df.to_csv(alpha_tsv, sep='\\t')
results["files"]["alpha_tsv"] = alpha_tsv

# ---- Alpha boxplot ----
if "shannon" in alpha_df.columns:
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(range(len(alpha_df)), alpha_df["shannon"].values)
    ax.set_xticks(range(len(alpha_df)))
    ax.set_xticklabels(alpha_df.index.tolist(), rotation=45, ha='right')
    ax.set_ylabel("Shannon Index")
    ax.set_title("Alpha Diversity (Shannon)")
    plt.tight_layout()
    plot_alpha = os.path.join(out_dir, "alpha_diversity_shannon.png")
    plt.savefig(plot_alpha, dpi=150)
    plt.close()
    results["files"]["alpha_plot"] = plot_alpha

# ---- Beta diversity (Bray-Curtis) ----
if "bray_curtis" in {list(metrics)}:
    from scipy.spatial.distance import braycurtis
    samples = df.columns.tolist()
    n = len(samples)
    bc_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(i+1, n):
            d = braycurtis(df.iloc[:, i].values, df.iloc[:, j].values)
            bc_matrix[i, j] = d
            bc_matrix[j, i] = d
    bc_df = pd.DataFrame(bc_matrix, index=samples, columns=samples)
    beta_tsv = os.path.join(out_dir, "beta_bray_curtis.tsv")
    bc_df.to_csv(beta_tsv, sep='\\t')
    results["files"]["beta_tsv"] = beta_tsv

    # Heatmap
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(bc_matrix, cmap='Blues', aspect='auto')
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(samples, rotation=45, ha='right')
    ax.set_yticklabels(samples)
    plt.colorbar(im, ax=ax, label='Bray-Curtis Distance')
    ax.set_title("Beta Diversity (Bray-Curtis)")
    plt.tight_layout()
    heatmap_path = os.path.join(out_dir, "beta_bray_curtis_heatmap.png")
    plt.savefig(heatmap_path, dpi=150)
    plt.close()
    results["files"]["beta_heatmap"] = heatmap_path

with open(os.path.join(out_dir, "diversity_results.json"), "w") as f:
    json.dump(results, f, indent=2)
print(json.dumps(results))
"""
    script_path = str(out / "_diversity_script.py")
    with open(script_path, "w") as f:
        f.write(script)

    proc = _run(["python", script_path], env_name=_BIO_ENV)
    _assert_ok(proc, "microbiome_diversity")
    Path(script_path).unlink(missing_ok=True)

    try:
        result = json.loads(proc.stdout)
    except Exception:
        result = {"output_dir": str(out), "stdout": proc.stdout}

    result["output_dir"] = str(out)
    return result


# =============================================================================
# Phase 5: Advanced Statistical Methods (Publication-Grade)
# =============================================================================

def run_permanova(
    distance_matrix_tsv: str,
    metadata_tsv: str,
    group_column: str,
    output_dir: str,
    permutations: int = 9999,
    strata_column: Optional[str] = None,
) -> dict:
    """
    Run PERMANOVA via R vegan::adonis2() to test if communities differ between groups.

    Parameters
    ----------
    distance_matrix_tsv : Path to symmetric TSV distance matrix (sample x sample)
    metadata_tsv        : Sample metadata TSV (sample IDs as first column)
    group_column        : Column in metadata to test (e.g. "treatment")
    output_dir          : Directory to write results
    permutations        : Number of permutations (default 9999)
    strata_column       : Optional stratification column (e.g. "patient_id")

    Returns dict with: permanova_tsv, r2, p_value, f_statistic, output_dir
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    strata_line = f"strata = metadata${strata_column}," if strata_column else ""
    r_script = f"""
library(vegan); library(data.table)
dist_mat <- as.dist(as.matrix(fread("{distance_matrix_tsv}", header=TRUE, row.names=1)))
metadata <- as.data.frame(fread("{metadata_tsv}", header=TRUE))
rownames(metadata) <- metadata[,1]
result <- adonis2(dist_mat ~ {group_column}, data=metadata, permutations={permutations}, {strata_line}method="bray")
out_df <- as.data.frame(result); out_df$Statistic <- rownames(out_df)
write.table(out_df, "{out}/permanova_results.tsv", sep="\\t", quote=FALSE, row.names=FALSE)
cat(sprintf('{{"r2":%f,"f_statistic":%f,"p_value":%f}}',
    result["Model","R2"], result["Model","F"], result["Model","Pr(>F)"]))
"""
    s = str(out / "_permanova.R")
    Path(s).write_text(r_script)
    proc = _run(["Rscript", s], env_name=_META_ENV)
    _assert_ok(proc, "permanova")
    Path(s).unlink(missing_ok=True)
    try:
        stats = json.loads(proc.stdout.strip().split("\n")[-1])
    except Exception:
        stats = {}
    return {
        "permanova_tsv": str(out / "permanova_results.tsv"),
        "r2": stats.get("r2"), "p_value": stats.get("p_value"),
        "f_statistic": stats.get("f_statistic"),
        "group_column": group_column, "permutations": permutations,
        "output_dir": str(out),
    }


def run_lefse(
    otu_table_tsv: str,
    metadata_tsv: str,
    class_column: str,
    output_dir: str,
    subject_column: Optional[str] = None,
    lda_threshold: float = 2.0,
    alpha: float = 0.05,
) -> dict:
    """
    Run LEfSe for microbial biomarker discovery (LDA + Kruskal-Wallis).

    Parameters
    ----------
    otu_table_tsv  : OTU/taxa table (features x samples) TSV
    metadata_tsv   : Sample metadata TSV
    class_column   : Column defining the class grouping
    output_dir     : Directory to write results
    lda_threshold  : Minimum LDA score threshold (default 2.0)
    alpha          : Statistical significance threshold (default 0.05)

    Returns dict with: biomarkers_tsv, n_biomarkers, plot_png, output_dir
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    input_lefse = str(out / "lefse_input.txt")
    result_file = str(out / "lefse_results.txt")
    plot_file   = str(out / "lefse_biomarkers.png")

    prep = f"""
import pandas as pd
meta = pd.read_csv("{metadata_tsv}", sep="\\t", index_col=0)
otu = pd.read_csv("{otu_table_tsv}", sep="\\t", index_col=0)
common = meta.index.intersection(otu.columns)
rows = [meta["{class_column}"].rename("class")]
{"rows.append(meta['" + subject_column + "'].rename('subject'))" if subject_column else ""}
rows += [otu[f] for f in otu.index if f in common]
pd.DataFrame(rows, columns=common).to_csv("{input_lefse}", sep="\\t", header=False)
"""
    pp = str(out / "_lefse_prep.py"); Path(pp).write_text(prep)
    _run(["python", pp], env_name=_META_ENV); Path(pp).unlink(missing_ok=True)

    proc = _run(["lefse-run.py", input_lefse, result_file,
                 "--lefse_alpha", str(alpha), "--lda_abs_th", str(lda_threshold)], env_name=_META_ENV)
    _assert_ok(proc, "lefse")
    _run(["lefse-plot_res.py", result_file, plot_file, "--format", "png"], env_name=_META_ENV)

    n = 0
    try:
        with open(result_file) as f:
            n = sum(1 for l in f if l.strip() and len(l.split("\t")) > 2 and l.split("\t")[2])
    except Exception:
        pass

    return {
        "biomarkers_tsv": result_file, "n_biomarkers": n,
        "plot_png": plot_file if Path(plot_file).exists() else None,
        "lda_threshold": lda_threshold, "output_dir": str(out),
    }


def run_ancom_bc(
    count_table_tsv: str,
    metadata_tsv: str,
    group_column: str,
    output_dir: str,
    formula: Optional[str] = None,
    p_adj_method: str = "BH",
    alpha: float = 0.05,
) -> dict:
    """
    Run ANCOM-BC (compositional differential abundance) via R.

    The most rigorous library-size-corrected test for compositional microbiome data.
    No rarefaction required.

    Parameters
    ----------
    count_table_tsv : Raw count table (features x samples) TSV
    metadata_tsv    : Sample metadata TSV
    group_column    : Column defining the comparison group
    output_dir      : Directory to write results
    formula         : R formula string (default: group_column only)
    p_adj_method    : P-value adjustment ("BH", "bonferroni", "holm")
    alpha           : Significance threshold (default 0.05)

    Returns dict with: results_tsv, n_significant, volcano_plot_png, output_dir
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    formula_str = formula or group_column

    r_script = f"""
suppressPackageStartupMessages({{library(ANCOMBC);library(phyloseq);library(data.table);library(ggplot2)}})
counts <- as.matrix(fread("{count_table_tsv}", header=TRUE, row.names=1))
meta   <- as.data.frame(fread("{metadata_tsv}", header=TRUE)); rownames(meta) <- meta[,1]; meta <- meta[,-1]
common <- intersect(colnames(counts), rownames(meta))
ps <- phyloseq(otu_table(counts[,common], taxa_are_rows=TRUE), sample_data(meta[common,,drop=FALSE]))
meta(sample_data(ps))${group_column} <- as.factor(sample_data(ps)${group_column})
out_ancom <- ancombc(phyloseq=ps, formula="{formula_str}", p_adj_method="{p_adj_method}", alpha={alpha}, verbose=FALSE)
res <- out_ancom$res
df <- data.frame(feature=rownames(res$lfc), lfc=res$lfc[,1], se=res$se[,1],
                 W=res$W[,1], p_value=res$p_val[,1], p_adj=res$q_val[,1], diff_abund=res$diff_abn[,1])
df <- df[order(df$p_adj),]
write.table(df, "{out}/ancombc_results.tsv", sep="\\t", quote=FALSE, row.names=FALSE)
n_sig <- sum(df$diff_abund, na.rm=TRUE)
cat(sprintf('{{"n_significant":%d}}', n_sig))
df$log10_padj <- -log10(df$p_adj+1e-300)
df$sig <- ifelse(df$diff_abund, "Significant", "Not significant")
p <- ggplot(df,aes(lfc,log10_padj,color=sig))+geom_point(size=2,alpha=0.8)+
     scale_color_manual(values=c("Significant"="#e74c3c","Not significant"="#95a5a6"))+
     geom_hline(yintercept=-log10({alpha}),linetype="dashed")+
     labs(title="ANCOM-BC",x="Log Fold Change",y="-log10(adj.p)")+theme_bw()
ggsave("{out}/ancombc_volcano.png",p,width=8,height=6,dpi=150)
"""
    s = str(out / "_ancombc.R"); Path(s).write_text(r_script)
    proc = _run(["Rscript", s], env_name=_META_ENV)
    _assert_ok(proc, "ancom_bc")
    Path(s).unlink(missing_ok=True)
    try:
        n_sig = json.loads(proc.stdout.strip().split("\n")[-1]).get("n_significant", 0)
    except Exception:
        n_sig = 0
    return {
        "results_tsv": str(out / "ancombc_results.tsv"),
        "n_significant": n_sig,
        "volcano_plot_png": str(out / "ancombc_volcano.png"),
        "group_column": group_column, "formula": formula_str,
        "p_adj_method": p_adj_method, "alpha": alpha,
        "output_dir": str(out),
    }
