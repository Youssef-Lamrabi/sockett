"""
metagenomics.py — CLI wrapper functions for metagenomics tools.

Each function builds a subprocess command, runs it with a timeout,
and returns a structured dict so the LLM agent can consume the result.
All heavy lifting (parsing, filtering) is left to the caller.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


# ── helpers ──────────────────────────────────────────────────────────────────

def _run(cmd: List[str], timeout: int, cwd: Optional[str] = None) -> Dict[str, Any]:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return {
        "ok": res.returncode == 0,
        "returncode": res.returncode,
        "stdout": res.stdout,
        "stderr": res.stderr,
        "cmd": cmd,
    }


def _which(name: str) -> str:
    import shutil
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(
            f"'{name}' not found on PATH. Install it in the active conda env."
        )
    return path


def _mkdir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


# ── Assembly QC ───────────────────────────────────────────────────────────────

def run_quast(
    contigs_fasta: str,
    output_dir: str,
    *,
    reference: Optional[str] = None,
    threads: int = 4,
    meta: bool = True,
    min_contig: int = 500,
    timeout: int = 300,
) -> Dict[str, Any]:
    """QUAST assembly quality assessment."""
    _mkdir(output_dir)
    cmd = [_which("quast.py"), contigs_fasta, "-o", output_dir,
           "-t", str(threads), "--min-contig", str(min_contig)]
    if meta:
        cmd.append("--meta")
    if reference:
        cmd += ["-r", reference]
    result = _run(cmd, timeout)
    report = os.path.join(output_dir, "report.tsv")
    result.update({"report_tsv": report if os.path.exists(report) else None,
                   "report_html": os.path.join(output_dir, "report.html"),
                   "output_dir": output_dir})
    return result


# ── Binning ───────────────────────────────────────────────────────────────────

def run_semibin2(
    contigs_fasta: str,
    output_dir: str,
    *,
    bam_files: Optional[List[str]] = None,
    environment: Optional[str] = None,
    threads: int = 4,
    min_len: int = 1000,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """SemiBin2 deep-learning metagenomic binning."""
    _mkdir(output_dir)
    cmd = [_which("SemiBin2"), "single_easy_bin",
           "-i", contigs_fasta, "-o", output_dir,
           "--threads", str(threads), "--min-len", str(min_len)]
    if environment:
        cmd += ["--environment", environment]
    elif bam_files:
        for b in bam_files:
            cmd += ["-b", b]
    result = _run(cmd, timeout)
    bins_dir = os.path.join(output_dir, "output_bins")
    bins = glob.glob(os.path.join(bins_dir, "*.fa")) + glob.glob(os.path.join(bins_dir, "*.fna"))
    result.update({"bins_dir": bins_dir, "bin_count": len(bins), "output_dir": output_dir})
    return result


def run_concoct(
    contigs_fasta: str,
    output_dir: str,
    *,
    bam_files: Optional[List[str]] = None,
    chunk_size: int = 10000,
    overlap_size: int = 0,
    clusters: int = 400,
    threads: int = 4,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """CONCOCT composition+coverage binning (4-step pipeline)."""
    _mkdir(output_dir)
    chunks_fa  = os.path.join(output_dir, "contigs_10k.fna")
    chunks_bed = os.path.join(output_dir, "contigs_10k.bed")
    cov_table  = os.path.join(output_dir, "coverage_table.tsv")
    bins_dir   = os.path.join(output_dir, "bins")

    # step 1 – cut up
    with open(chunks_fa, "w") as fh:
        r1 = subprocess.run(
            [_which("cut_up_fasta.py"), contigs_fasta,
             "-c", str(chunk_size), "-o", str(overlap_size),
             "--merge_last", "-b", chunks_bed],
            capture_output=True, text=True, timeout=120, stdout=fh)

    # step 2 – coverage table
    if bam_files:
        with open(cov_table, "w") as fh:
            subprocess.run(
                [_which("concoct_coverage_table.py"), chunks_bed] + bam_files,
                capture_output=True, text=True, timeout=300, stdout=fh)

    # step 3 – cluster
    r3 = _run([_which("concoct"),
               "--composition_file", chunks_fa,
               "--coverage_file", cov_table,
               "-b", output_dir, "-t", str(threads),
               "-c", str(clusters)], timeout)

    # step 4 – merge + extract
    merged = os.path.join(output_dir, "clustering_merged.csv")
    with open(merged, "w") as fh:
        subprocess.run(
            [_which("merge_cutup_clustering.py"),
             os.path.join(output_dir, "clustering_gt1000.csv")],
            capture_output=True, text=True, timeout=60, stdout=fh)

    _mkdir(bins_dir)
    subprocess.run(
        [_which("extract_fasta_bins.py"), contigs_fasta, merged,
         "--output_path", bins_dir],
        capture_output=True, text=True, timeout=120)

    bins = glob.glob(os.path.join(bins_dir, "*.fa"))
    r3.update({"clustering_tsv": merged, "bins_dir": bins_dir, "bin_count": len(bins)})
    return r3


def run_maxbin2(
    contigs_fasta: str,
    output_prefix: str,
    *,
    abund_list: Optional[str] = None,
    reads: Optional[List[str]] = None,
    min_contig_length: int = 1000,
    threads: int = 4,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """MaxBin2 marker-gene EM binning."""
    _mkdir(os.path.dirname(output_prefix) or ".")
    cmd = [_which("run_MaxBin.pl"), "-contig", contigs_fasta,
           "-out", output_prefix, "-thread", str(threads),
           "-min_contig_length", str(min_contig_length)]
    if abund_list:
        cmd += ["-abund_list", abund_list]
    elif reads:
        for r in reads:
            cmd += ["-reads", r]
    result = _run(cmd, timeout)
    bins = glob.glob(f"{output_prefix}.*.fasta")
    summary = f"{output_prefix}.summary"
    result.update({"bins_dir": os.path.dirname(output_prefix),
                   "bin_count": len(bins),
                   "summary_tsv": summary if os.path.exists(summary) else None})
    return result


# ── Bin quality ───────────────────────────────────────────────────────────────

def run_checkm2(
    bins_dir: str,
    output_dir: str,
    *,
    threads: int = 4,
    extension: str = "fna",
    min_completeness: float = 0.0,
    timeout: int = 1800,
) -> Dict[str, Any]:
    """CheckM2 ML-based bin quality assessment."""
    _mkdir(output_dir)
    bin_glob = os.path.join(bins_dir, f"*.{extension}")
    cmd = [_which("checkm2"), "predict",
           "--threads", str(threads),
           "--input", bin_glob,
           "--output-directory", output_dir,
           "--extension", extension]
    result = _run(cmd, timeout)
    report = os.path.join(output_dir, "quality_report.tsv")
    hq, mq = [], []
    if os.path.exists(report):
        import csv
        with open(report) as f:
            for row in csv.DictReader(f, delimiter="\t"):
                try:
                    comp = float(row.get("Completeness", 0))
                    cont = float(row.get("Contamination", 100))
                except ValueError:
                    continue
                if comp >= 90 and cont <= 5:
                    hq.append(row["Name"])
                elif comp >= 50 and cont <= 10:
                    mq.append(row["Name"])
    result.update({"quality_report_tsv": report,
                   "high_quality_bins": hq,
                   "medium_quality_bins": mq,
                   "output_dir": output_dir})
    return result


# ── Taxonomic classification ──────────────────────────────────────────────────

def run_kraken2(
    reads: List[str],
    db_path: str,
    output_prefix: str,
    *,
    paired: bool = False,
    gzip_compressed: bool = False,
    confidence: float = 0.0,
    threads: int = 4,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """Kraken2 k-mer taxonomic classification."""
    out_dir = os.path.dirname(output_prefix) or "."
    _mkdir(out_dir)
    kraken_out = f"{output_prefix}.kraken"
    report     = f"{output_prefix}_report.txt"
    cmd = [_which("kraken2"), "--db", db_path,
           "--threads", str(threads),
           "--output", kraken_out,
           "--report", report,
           "--confidence", str(confidence)]
    if gzip_compressed:
        cmd.append("--gzip-compressed")
    if paired:
        cmd.append("--paired")
    cmd += reads
    result = _run(cmd, timeout)
    # parse classified count from stderr
    classified = unclassified = 0
    for line in result["stderr"].splitlines():
        if "sequences classified" in line:
            classified = int(line.split()[0].replace(",", ""))
        elif "sequences unclassified" in line:
            unclassified = int(line.split()[0].replace(",", ""))
    result.update({"kraken_output": kraken_out, "report_txt": report,
                   "classified_count": classified,
                   "unclassified_count": unclassified})
    return result


def run_sylph(
    reads: List[str],
    output_prefix: str,
    *,
    db_path: Optional[str] = None,
    threads: int = 4,
    min_ani: float = 0.95,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Sylph sketch-based metagenomic profiling."""
    sketch = f"{output_prefix}.sylsp"
    r1 = _run([_which("sylph"), "sketch"] + reads + ["-o", sketch], 120)
    result = {"sketch": sketch, "sketch_ok": r1["ok"]}
    if db_path and r1["ok"]:
        profile = f"{output_prefix}_profile.tsv"
        r2 = _run([_which("sylph"), "profile", sketch,
                   "-d", db_path, "-t", str(threads),
                   "--min-ani", str(min_ani), "-o", profile], timeout)
        result.update({"profile_tsv": profile, **r2})
    return result


def run_kaiju(
    reads: List[str],
    db_path: str,
    output_prefix: str,
    *,
    paired: bool = False,
    threads: int = 4,
    taxon_rank: str = "species",
    timeout: int = 1800,
) -> Dict[str, Any]:
    """Kaiju protein-level taxonomic classification."""
    nodes_dmp = os.path.join(db_path, "nodes.dmp")
    names_dmp = os.path.join(db_path, "names.dmp")
    fmi_files = glob.glob(os.path.join(db_path, "*.fmi"))
    if not fmi_files:
        raise FileNotFoundError(f"No .fmi database found in {db_path}")
    fmi = fmi_files[0]
    out_txt = f"{output_prefix}.txt"
    summary = f"{output_prefix}_summary.tsv"

    cmd = [_which("kaiju"), "-t", nodes_dmp, "-f", fmi,
           "-z", str(threads), "-o", out_txt]
    if paired and len(reads) == 2:
        cmd += ["-i", reads[0], "-j", reads[1]]
    else:
        cmd += ["-i", reads[0]]
    result = _run(cmd, timeout)

    if result["ok"]:
        _run([_which("kaiju2table"),
              "-t", nodes_dmp, "-n", names_dmp,
              "-r", taxon_rank, "-o", summary, out_txt], 120)

    result.update({"classification_txt": out_txt, "summary_tsv": summary})
    return result


# ── Functional annotation ─────────────────────────────────────────────────────

def run_hmmer(
    proteins_faa: str,
    hmm_db: str,
    output_prefix: str,
    *,
    mode: str = "hmmscan",
    evalue: float = 1e-5,
    threads: int = 4,
    timeout: int = 600,
) -> Dict[str, Any]:
    """HMMER profile HMM protein family annotation."""
    tblout   = f"{output_prefix}_tblout.tsv"
    domtblout = f"{output_prefix}_domtblout.tsv"
    out_dir = os.path.dirname(output_prefix) or "."
    _mkdir(out_dir)

    if mode not in ("hmmscan", "hmmsearch"):
        raise ValueError("mode must be 'hmmscan' or 'hmmsearch'")
    # hmmscan: protein vs HMM db  |  hmmsearch: HMM vs protein db
    if mode == "hmmscan":
        cmd = [_which("hmmscan"), "--tblout", tblout, "--domtblout", domtblout,
               "--cpu", str(threads), "-E", str(evalue), hmm_db, proteins_faa]
    else:
        cmd = [_which("hmmsearch"), "--tblout", tblout, "--domtblout", domtblout,
               "--cpu", str(threads), "-E", str(evalue), hmm_db, proteins_faa]
    result = _run(cmd, timeout)
    # count non-comment lines in tblout
    hit_count = 0
    if os.path.exists(tblout):
        with open(tblout) as f:
            hit_count = sum(1 for l in f if not l.startswith("#") and l.strip())
    result.update({"tblout_tsv": tblout, "domtblout_tsv": domtblout, "hit_count": hit_count})
    return result


def run_eggnog(
    proteins_faa: str,
    output_prefix: str,
    data_dir: str,
    *,
    threads: int = 4,
    evalue: float = 1e-3,
    score: float = 60.0,
    tax_scope: str = "auto",
    timeout: int = 1800,
) -> Dict[str, Any]:
    """EggNOG-mapper orthology-based functional annotation."""
    out_dir = os.path.dirname(output_prefix) or "."
    _mkdir(out_dir)
    cmd = [_which("emapper.py"),
           "-i", proteins_faa,
           "-o", os.path.basename(output_prefix),
           "--output_dir", out_dir,
           "--cpu", str(threads),
           "--data_dir", data_dir,
           "--evalue", str(evalue),
           "--score", str(score),
           "--tax_scope", tax_scope,
           "--override"]
    result = _run(cmd, timeout)
    ann = f"{output_prefix}.emapper.annotations"
    result.update({"annotations_tsv": ann,
                   "exists": os.path.exists(ann),
                   "output_dir": out_dir})
    return result


def run_diamond(
    query: str,
    db_path: str,
    output_file: str,
    *,
    mode: str = "blastp",
    evalue: float = 1e-5,
    threads: int = 4,
    top: int = 1,
    outfmt: int = 6,
    timeout: int = 1800,
) -> Dict[str, Any]:
    """DIAMOND fast protein alignment."""
    if mode not in ("blastp", "blastx"):
        raise ValueError("mode must be 'blastp' or 'blastx'")
    _mkdir(os.path.dirname(output_file) or ".")
    cmd = [_which("diamond"), mode,
           "-q", query, "-d", db_path, "-o", output_file,
           "--outfmt", str(outfmt),
           "-p", str(threads),
           "--evalue", str(evalue),
           "--top", str(top)]
    result = _run(cmd, timeout)
    hit_count = 0
    if os.path.exists(output_file):
        with open(output_file) as f:
            hit_count = sum(1 for l in f if l.strip())
    result.update({"hits_tsv": output_file, "hit_count": hit_count})
    return result


def run_humann3(
    input_reads: str,
    output_dir: str,
    *,
    threads: int = 4,
    nucleotide_db: Optional[str] = None,
    protein_db: Optional[str] = None,
    taxonomic_profile: Optional[str] = None,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """HUMAnN3 functional profiling of metagenomes."""
    _mkdir(output_dir)
    cmd = [_which("humann"),
           "--input", input_reads,
           "--output", output_dir,
           "--threads", str(threads)]
    if nucleotide_db:
        cmd += ["--nucleotide-database", nucleotide_db]
    if protein_db:
        cmd += ["--protein-database", protein_db]
    if taxonomic_profile:
        cmd += ["--taxonomic-profile", taxonomic_profile]
    result = _run(cmd, timeout)
    base = os.path.splitext(os.path.basename(input_reads))[0].replace(".fastq", "").replace(".gz", "")
    result.update({
        "genefamilies_tsv": os.path.join(output_dir, f"{base}_genefamilies.tsv"),
        "pathabundance_tsv": os.path.join(output_dir, f"{base}_pathabundance.tsv"),
        "pathcoverage_tsv": os.path.join(output_dir, f"{base}_pathcoverage.tsv"),
        "output_dir": output_dir,
    })
    return result


# ── Specialized annotation ────────────────────────────────────────────────────

def run_antismash(
    input_fasta: str,
    output_dir: str,
    *,
    taxon: str = "bacteria",
    threads: int = 4,
    genefinding_tool: str = "prodigal-m",
    minimal: bool = False,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """antiSMASH biosynthetic gene cluster detection."""
    _mkdir(output_dir)
    cmd = [_which("antismash"),
           "--taxon", taxon,
           "--output-dir", output_dir,
           "--genefinding-tool", genefinding_tool,
           "--cpus", str(threads)]
    if minimal:
        cmd.append("--minimal")
    cmd.append(input_fasta)
    result = _run(cmd, timeout)
    # count regions from region files
    regions = glob.glob(os.path.join(output_dir, "*.region*.gbk"))
    bgc_types: List[str] = []
    for r in regions:
        name = os.path.basename(r)
        if "." in name:
            bgc_types.append(name.split(".")[-2] if len(name.split(".")) > 2 else "unknown")
    result.update({"html_report": os.path.join(output_dir, "index.html"),
                   "bgc_count": len(regions),
                   "bgc_types": list(set(bgc_types)),
                   "output_dir": output_dir})
    return result


def run_genomad(
    contigs_fasta: str,
    output_dir: str,
    db_path: str,
    *,
    threads: int = 4,
    splits: int = 8,
    min_score: float = 0.7,
    timeout: int = 1800,
) -> Dict[str, Any]:
    """geNomad virus/plasmid identification."""
    _mkdir(output_dir)
    cmd = [_which("genomad"), "end-to-end", "--cleanup",
           "--splits", str(splits),
           "--threads", str(threads),
           contigs_fasta, output_dir, db_path]
    result = _run(cmd, timeout)
    base = os.path.splitext(os.path.basename(contigs_fasta))[0]
    sub = os.path.join(output_dir, f"{base}_summary")
    virus_tsv   = os.path.join(sub, f"{base}_virus_summary.tsv")
    plasmid_tsv = os.path.join(sub, f"{base}_plasmid_summary.tsv")
    def _count(tsv: str) -> int:
        if not os.path.exists(tsv):
            return 0
        with open(tsv) as f:
            return sum(1 for l in f if not l.startswith("seq_name") and l.strip())
    result.update({"virus_summary_tsv": virus_tsv,
                   "plasmid_summary_tsv": plasmid_tsv,
                   "virus_count": _count(virus_tsv),
                   "plasmid_count": _count(plasmid_tsv)})
    return result


def run_abricate(
    contigs_fasta: str,
    output_file: str,
    *,
    db: str = "resfinder",
    minid: float = 80.0,
    mincov: float = 80.0,
    timeout: int = 300,
) -> Dict[str, Any]:
    """ABRicate AMR/virulence gene screening."""
    _mkdir(os.path.dirname(output_file) or ".")
    valid_dbs = {"resfinder", "card", "ncbi", "argannot", "vfdb", "plasmidfinder", "ecoh"}
    if db not in valid_dbs:
        raise ValueError(f"db must be one of {valid_dbs}")
    cmd = [_which("abricate"), "--db", db,
           "--minid", str(minid), "--mincov", str(mincov),
           contigs_fasta]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    with open(output_file, "w") as f:
        f.write(res.stdout)
    gene_count = sum(1 for l in res.stdout.splitlines()
                     if l.strip() and not l.startswith("#FILE"))
    genes = [l.split("\t")[4] for l in res.stdout.splitlines()
             if l.strip() and not l.startswith("#") and len(l.split("\t")) > 4]
    return {"ok": res.returncode == 0, "returncode": res.returncode,
            "results_tsv": output_file, "gene_count": gene_count,
            "resistance_genes": list(set(genes)),
            "stdout": res.stdout, "stderr": res.stderr}


# ── Sequence manipulation ─────────────────────────────────────────────────────

def run_seqkit(
    subcommand: str,
    input_files: List[str],
    *,
    output_file: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
    threads: int = 4,
    timeout: int = 120,
) -> Dict[str, Any]:
    """SeqKit FASTA/FASTQ manipulation toolkit."""
    cmd = [_which("seqkit"), subcommand, "--threads", str(threads)]
    if extra_args:
        cmd += extra_args
    cmd += input_files
    if output_file:
        cmd += ["-o", output_file]
    result = _run(cmd, timeout)
    if output_file:
        result["output_file"] = output_file
    return result


def run_bbduk(
    input_reads: List[str],
    output_reads: List[str],
    *,
    ref: str = "adapters",
    ktrim: str = "r",
    qtrim: str = "r",
    trimq: int = 20,
    minlen: int = 50,
    threads: int = 4,
    timeout: int = 300,
) -> Dict[str, Any]:
    """BBDuk adapter trimming and quality filtering."""
    cmd = [_which("bbduk.sh")]
    if len(input_reads) == 2:
        cmd += [f"in1={input_reads[0]}", f"in2={input_reads[1]}",
                f"out1={output_reads[0]}", f"out2={output_reads[1]}",
                "tpe", "tbo"]
    else:
        cmd += [f"in={input_reads[0]}", f"out={output_reads[0]}"]
    cmd += [f"ref={ref}", f"ktrim={ktrim}", "k=23", "mink=11", "hdist=1",
            f"qtrim={qtrim}", f"trimq={trimq}", f"minlen={minlen}",
            f"threads={threads}"]
    result = _run(cmd, timeout)
    # parse stats from stderr
    stats: Dict[str, str] = {}
    for line in result["stderr"].splitlines():
        if "Input:" in line or "Output:" in line or "Result:" in line:
            stats[line.split(":")[0].strip()] = line.split(":", 1)[-1].strip()
    result["stats"] = stats
    return result


# ── CAZyme annotation ─────────────────────────────────────────────────────────

def run_dbcan(
    proteins_faa: str,
    output_dir: str,
    db_dir: str,
    *,
    input_type: str = "protein",
    tools: Optional[List[str]] = None,
    threads: int = 4,
    timeout: int = 600,
) -> Dict[str, Any]:
    """dbCAN CAZyme annotation pipeline."""
    _mkdir(output_dir)
    if tools is None:
        tools = ["hmmer", "diamond"]
    cmd = [_which("run_dbcan.py"), proteins_faa, input_type,
           "--out_dir", output_dir, "--db_dir", db_dir,
           "--tools"] + tools + ["-t", str(threads)]
    result = _run(cmd, timeout)
    overview = os.path.join(output_dir, "overview.txt")
    cazymes: Dict[str, int] = {}
    if os.path.exists(overview):
        with open(overview) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 3 and not line.startswith("Gene"):
                    fam = parts[2]
                    cazymes[fam] = cazymes.get(fam, 0) + 1
    result.update({"overview_tsv": overview,
                   "cazyme_count": sum(cazymes.values()),
                   "families": cazymes})
    return result


# ── Phage annotation ──────────────────────────────────────────────────────────

def run_pharokka(
    input_fasta: str,
    output_dir: str,
    db_dir: str,
    *,
    threads: int = 4,
    gene_predictor: str = "phanotate",
    force: bool = True,
    timeout: int = 1800,
) -> Dict[str, Any]:
    """Pharokka phage genome annotation."""
    _mkdir(output_dir)
    cmd = [_which("pharokka.py"),
           "-i", input_fasta,
           "-o", output_dir,
           "-d", db_dir,
           "-t", str(threads),
           "-g", gene_predictor]
    if force:
        cmd.append("-f")
    result = _run(cmd, timeout)
    base = "pharokka"
    result.update({
        "gff": os.path.join(output_dir, f"{base}.gff"),
        "gbk": os.path.join(output_dir, f"{base}.gbk"),
        "phrog_summary": os.path.join(output_dir, f"{base}_top_hits_card.tsv"),
        "output_dir": output_dir,
    })
    return result


# ── Community analysis ────────────────────────────────────────────────────────

def run_phyloseq(
    otu_table: str,
    output_dir: str,
    *,
    tax_table: Optional[str] = None,
    metadata: Optional[str] = None,
    analysis: Optional[List[str]] = None,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Phyloseq R microbiome analysis (alpha/beta diversity, ordination)."""
    import shutil
    if not shutil.which("Rscript"):
        raise FileNotFoundError("Rscript not found on PATH.")
    _mkdir(output_dir)
    if analysis is None:
        analysis = ["alpha_diversity", "beta_diversity", "ordination"]

    alpha_tsv = os.path.join(output_dir, "alpha_diversity.tsv")
    beta_tsv  = os.path.join(output_dir, "beta_diversity.tsv")
    ord_plot  = os.path.join(output_dir, "ordination.png")

    r_lines = [
        "library(phyloseq)",
        "library(ggplot2)",
        f'otu <- read.table("{otu_table}", sep="\\t", header=TRUE, row.names=1)',
        "OTU <- otu_table(as.matrix(otu), taxa_are_rows=TRUE)",
        "ps <- phyloseq(OTU)",
    ]
    if tax_table:
        r_lines += [
            f'tax <- read.table("{tax_table}", sep="\\t", header=TRUE, row.names=1)',
            "TAX <- tax_table(as.matrix(tax))",
            "ps <- phyloseq(OTU, TAX)",
        ]
    if metadata:
        r_lines += [
            f'meta <- read.table("{metadata}", sep="\\t", header=TRUE, row.names=1)',
            "SAMP <- sample_data(meta)",
            "ps <- merge_phyloseq(ps, SAMP)",
        ]
    if "alpha_diversity" in analysis:
        r_lines += [
            "alpha <- estimate_richness(ps, measures=c('Observed','Shannon','Simpson','Chao1'))",
            f'write.table(alpha, "{alpha_tsv}", sep="\\t", quote=FALSE)',
        ]
    if "beta_diversity" in analysis:
        r_lines += [
            "bc <- distance(ps, method='bray')",
            f'write.table(as.matrix(bc), "{beta_tsv}", sep="\\t", quote=FALSE)',
        ]
    if "ordination" in analysis:
        r_lines += [
            "ord <- ordinate(ps, method='PCoA', distance='bray')",
            "p <- plot_ordination(ps, ord)",
            f'ggsave("{ord_plot}", p, width=8, height=6)',
        ]

    r_script = "\n".join(r_lines)
    res = subprocess.run(
        ["Rscript", "-e", r_script],
        capture_output=True, text=True, timeout=timeout)
    return {"ok": res.returncode == 0, "returncode": res.returncode,
            "alpha_div_tsv": alpha_tsv if os.path.exists(alpha_tsv) else None,
            "beta_div_tsv": beta_tsv if os.path.exists(beta_tsv) else None,
            "ordination_plot": ord_plot if os.path.exists(ord_plot) else None,
            "stdout": res.stdout, "stderr": res.stderr}


def run_lefse(
    input_tsv: str,
    output_prefix: str,
    *,
    class_row: int = 0,
    lda_threshold: float = 2.0,
    pvalue: float = 0.05,
    timeout: int = 300,
) -> Dict[str, Any]:
    """LEfSe linear discriminant analysis for biomarker discovery."""
    _mkdir(os.path.dirname(output_prefix) or ".")
    formatted = f"{output_prefix}.in"
    results   = f"{output_prefix}.res"
    plot      = f"{output_prefix}_barplot.png"

    r1 = _run([_which("lefse_format_input.py"), input_tsv, formatted,
               "-c", str(class_row + 1), "-s", "-1", "-u", "2",
               "-o", "1000000"], 60)
    if not r1["ok"]:
        r1.update({"results_tsv": None, "significant_features": [], "plot_png": None})
        return r1

    r2 = _run([_which("lefse_run.py"), formatted, results,
               "-l", str(lda_threshold),
               "--alpha", str(pvalue)], 120)

    _run([_which("lefse_plot_res.py"), results, plot, "--format", "png"], 60)

    features = []
    if os.path.exists(results):
        with open(results) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 3 and parts[2]:
                    features.append(parts[0])
    r2.update({"results_tsv": results,
               "significant_features": features,
               "plot_png": plot if os.path.exists(plot) else None})
    return r2


# ── Coverage estimation ───────────────────────────────────────────────────────

def run_nonpareil(
    reads_file: str,
    output_prefix: str,
    *,
    method: str = "kmer",
    threads: int = 4,
    subsample_n: int = 1000,
    timeout: int = 600,
) -> Dict[str, Any]:
    """Nonpareil metagenome coverage and sequencing effort estimation."""
    _mkdir(os.path.dirname(output_prefix) or ".")
    fmt = "fastq" if reads_file.endswith((".fastq", ".fastq.gz", ".fq", ".fq.gz")) else "fasta"
    cmd = [_which("nonpareil"),
           "-s", reads_file,
           "-T", method,
           "-f", fmt,
           "-b", output_prefix,
           "-t", str(threads),
           "-n", str(subsample_n)]
    result = _run(cmd, timeout)
    npo = f"{output_prefix}.npo"
    # parse coverage from .npo if exists
    coverage = None
    if os.path.exists(npo):
        with open(npo) as f:
            for line in f:
                if line.startswith("C\t"):
                    try:
                        coverage = float(line.split("\t")[1])
                    except (IndexError, ValueError):
                        pass
    result.update({"npo_file": npo,
                   "coverage_estimate": coverage,
                   "output_prefix": output_prefix})
    return result


# ── Bin dereplication ─────────────────────────────────────────────────────────

def run_das_tool(
    bins_dirs: List[str],
    contigs_fasta: str,
    output_dir: str,
    *,
    labels: Optional[List[str]] = None,
    db_path: Optional[str] = None,
    search_engine: str = "diamond",
    threads: int = 4,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """DAS_Tool bin dereplication and refinement across multiple binners."""
    _mkdir(output_dir)
    if labels is None:
        labels = [f"binner{i}" for i in range(len(bins_dirs))]
    bins_csv   = ",".join(bins_dirs)
    labels_csv = ",".join(labels)
    output_prefix = os.path.join(output_dir, "dastool")
    cmd = [_which("DAS_Tool"),
           "-i", bins_csv,
           "-l", labels_csv,
           "-c", contigs_fasta,
           "-o", output_prefix,
           "--threads", str(threads),
           "--search_engine", search_engine,
           "--write_bins"]
    if db_path:
        cmd += ["--db_directory", db_path]
    result = _run(cmd, timeout)
    summary_tsv = f"{output_prefix}_DASTool_summary.tsv"
    bins_out    = f"{output_prefix}_DASTool_bins"
    bins = glob.glob(os.path.join(bins_out, "*.fa")) + glob.glob(os.path.join(bins_out, "*.fna"))
    result.update({
        "summary_tsv": summary_tsv if os.path.exists(summary_tsv) else None,
        "bins_dir": bins_out,
        "bin_count": len(bins),
        "output_dir": output_dir,
    })
    return result


# ── Abundance re-estimation ───────────────────────────────────────────────────

def run_bracken(
    kraken2_report: str,
    db_path: str,
    output_prefix: str,
    *,
    read_length: int = 150,
    level: str = "S",
    threshold: int = 10,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Bracken Bayesian re-estimation of species abundance from a Kraken2 report."""
    _mkdir(os.path.dirname(output_prefix) or ".")
    bracken_out = f"{output_prefix}.bracken"
    report_out  = f"{output_prefix}_bracken_report.txt"
    cmd = [_which("bracken"),
           "-d", db_path,
           "-i", kraken2_report,
           "-o", bracken_out,
           "-w", report_out,
           "-r", str(read_length),
           "-l", level,
           "-t", str(threshold)]
    result = _run(cmd, timeout)
    species_count = 0
    if os.path.exists(bracken_out):
        with open(bracken_out) as f:
            species_count = sum(1 for l in f if not l.startswith("name") and l.strip())
    result.update({
        "bracken_tsv": bracken_out,
        "report_txt": report_out,
        "species_count": species_count,
    })
    return result


# ── Marker-gene profiling ─────────────────────────────────────────────────────

def run_metaphlan4(
    reads: List[str],
    output_prefix: str,
    *,
    db_path: Optional[str] = None,
    analysis_type: str = "rel_ab_w_read_stats",
    threads: int = 4,
    timeout: int = 3600,
) -> Dict[str, Any]:
    """MetaPhlAn 4 marker-gene taxonomic profiling."""
    _mkdir(os.path.dirname(output_prefix) or ".")
    profile_tsv = f"{output_prefix}_profile.tsv"
    bowtie2_out = f"{output_prefix}.bowtie2.bz2"
    cmd = [_which("metaphlan"),
           ",".join(reads),
           "--input_type", "fastq",
           "--nproc", str(threads),
           "--output_file", profile_tsv,
           "--bowtie2out", bowtie2_out,
           "-t", analysis_type]
    if db_path:
        cmd += ["--bowtie2db", db_path]
    result = _run(cmd, timeout)
    species_count = 0
    if os.path.exists(profile_tsv):
        with open(profile_tsv) as f:
            species_count = sum(
                1 for l in f
                if not l.startswith("#") and "s__" in l and l.strip()
            )
    result.update({
        "profile_tsv": profile_tsv,
        "bowtie2_out": bowtie2_out,
        "species_count": species_count,
    })
    return result


# ── Phylogenetic classification ───────────────────────────────────────────────

def run_gtdbtk(
    bins_dir: str,
    output_dir: str,
    *,
    extension: str = "fna",
    cpus: int = 4,
    pplacer_cpus: int = 1,
    skip_ani_screen: bool = False,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """GTDB-Tk phylogenetic classification of MAGs against the GTDB reference."""
    _mkdir(output_dir)
    cmd = [_which("gtdbtk"), "classify_wf",
           "--genome_dir", bins_dir,
           "--out_dir", output_dir,
           "--cpus", str(cpus),
           "--pplacer_cpus", str(pplacer_cpus),
           "--extension", extension]
    if skip_ani_screen:
        cmd.append("--skip_ani_screen")
    result = _run(cmd, timeout)
    summary_bac = os.path.join(output_dir, "gtdbtk.bac120.summary.tsv")
    summary_arc = os.path.join(output_dir, "gtdbtk.ar53.summary.tsv")
    classified_count = 0
    for tsv in (summary_bac, summary_arc):
        if os.path.exists(tsv):
            with open(tsv) as f:
                classified_count += sum(
                    1 for l in f if not l.startswith("user_genome") and l.strip()
                )
    result.update({
        "bac120_summary_tsv": summary_bac if os.path.exists(summary_bac) else None,
        "ar53_summary_tsv":   summary_arc if os.path.exists(summary_arc) else None,
        "classified_count": classified_count,
        "output_dir": output_dir,
    })
    return result


# ── Genome annotation ─────────────────────────────────────────────────────────

def run_prokka(
    contigs_fasta: str,
    output_dir: str,
    *,
    prefix: str = "prokka",
    kingdom: str = "Bacteria",
    genus: str = "",
    species: str = "",
    threads: int = 4,
    timeout: int = 1800,
) -> Dict[str, Any]:
    """Prokka rapid prokaryote genome annotation."""
    _mkdir(output_dir)
    cmd = [_which("prokka"),
           "--outdir", output_dir,
           "--prefix", prefix,
           "--kingdom", kingdom,
           "--cpus", str(threads),
           "--force",
           contigs_fasta]
    if genus:
        cmd += ["--genus", genus]
    if species:
        cmd += ["--species", species]
    result = _run(cmd, timeout)
    base = os.path.join(output_dir, prefix)
    cds_count = 0
    txt = f"{base}.txt"
    if os.path.exists(txt):
        with open(txt) as f:
            for line in f:
                if line.startswith("CDS:"):
                    try:
                        cds_count = int(line.split(":")[1].strip())
                    except ValueError:
                        pass
    result.update({
        "gff": f"{base}.gff",
        "gbk": f"{base}.gbk",
        "faa": f"{base}.faa",
        "ffn": f"{base}.ffn",
        "summary_txt": txt,
        "cds_count": cds_count,
        "output_dir": output_dir,
    })
    return result


# ── Long-read polishing ───────────────────────────────────────────────────────

def run_medaka(
    assembly_fasta: str,
    reads_fastq: str,
    output_dir: str,
    *,
    model: str = "r941_min_hac_g507",
    threads: int = 4,
    timeout: int = 7200,
) -> Dict[str, Any]:
    """Medaka consensus polishing for Oxford Nanopore assemblies."""
    _mkdir(output_dir)
    consensus = os.path.join(output_dir, "consensus.fasta")
    cmd = [_which("medaka_consensus"),
           "-i", reads_fastq,
           "-d", assembly_fasta,
           "-o", output_dir,
           "-m", model,
           "-t", str(threads)]
    result = _run(cmd, timeout)
    seq_count = 0
    if os.path.exists(consensus):
        with open(consensus) as f:
            seq_count = sum(1 for l in f if l.startswith(">"))
    result.update({
        "consensus_fasta": consensus if os.path.exists(consensus) else None,
        "sequence_count": seq_count,
        "output_dir": output_dir,
    })
    return result


# ── Resistome ─────────────────────────────────────────────────────────────────

def run_rgi(
    input_fasta: str,
    output_prefix: str,
    *,
    input_type: str = "protein",
    alignment_tool: str = "DIAMOND",
    include_loose: bool = False,
    threads: int = 4,
    timeout: int = 1800,
) -> Dict[str, Any]:
    """RGI (Resistance Gene Identifier) resistome prediction against CARD database."""
    _mkdir(os.path.dirname(output_prefix) or ".")
    if input_type not in ("protein", "contig", "read"):
        raise ValueError("input_type must be 'protein', 'contig', or 'read'")
    cmd = [_which("rgi"), "main",
           "-i", input_fasta,
           "-o", output_prefix,
           "-t", input_type,
           "-a", alignment_tool,
           "-n", str(threads),
           "--clean"]
    if include_loose:
        cmd.append("--include_loose")
    result = _run(cmd, timeout)
    tsv = f"{output_prefix}.txt"
    genes: List[str] = []
    if os.path.exists(tsv):
        with open(tsv) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) > 8 and not line.startswith("ORF_ID"):
                    genes.append(parts[8])  # Best_Hit_ARO column
    result.update({
        "results_tsv": tsv if os.path.exists(tsv) else None,
        "amr_gene_count": len(genes),
        "amr_genes_detected": list(set(genes)),
    })
    return result


def run_amrfinder(
    proteins_faa: str,
    output_file: str,
    *,
    organism: Optional[str] = None,
    plus: bool = True,
    threads: int = 4,
    timeout: int = 600,
) -> Dict[str, Any]:
    """NCBI AMRFinderPlus AMR, stress, and virulence gene identification."""
    _mkdir(os.path.dirname(output_file) or ".")
    cmd = [_which("amrfinder"),
           "-p", proteins_faa,
           "-o", output_file,
           "--threads", str(threads)]
    if organism:
        cmd += ["--organism", organism]
    if plus:
        cmd.append("--plus")
    result = _run(cmd, timeout)
    genes: List[str] = []
    drug_classes: List[str] = []
    if os.path.exists(output_file):
        with open(output_file) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) > 10 and not line.startswith("Protein identifier"):
                    genes.append(parts[5])           # Gene symbol column
                    drug_classes.append(parts[10])   # Drug class column
    result.update({
        "results_tsv": output_file if os.path.exists(output_file) else None,
        "amr_gene_count": len(genes),
        "amr_genes_detected": list(set(genes)),
        "drug_classes": list(set(drug_classes)),
    })
    return result
