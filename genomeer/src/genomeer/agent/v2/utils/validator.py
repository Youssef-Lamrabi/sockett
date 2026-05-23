"""
ToolValidator — deterministic post-executor gate (Phase 1 + Phase 2 + Phase 3).

All output file names, column names and quality thresholds are verified
against official tool documentation and peer-reviewed publications.
Sources are cited inline per contract.

Decision flow per step:
  validate(step_title, run_dir, stdout) → ContractResult
    ok=True  + score≥0   → bookkeeping + skip observer → orchestrator
    ok=True  + score=-1  → no contract for this step   → observer (LLM)
    ok=False             → retry logic based on RUNTIME:
      RUNTIME="fast"  (<30 min)  → up to 3 sequential variant retries → observer
      RUNTIME="medium"(30m–2h)   → 1 retry with best hint             → observer
      RUNTIME="long"  (>2h)      → 0 retries, immediately             → observer

VARIANTS (fast tools only): ordered list of param-mutation hints tried per retry.
  retry 1 → VARIANTS[0], retry 2 → VARIANTS[1], retry 3 → VARIANTS[2]
"""

from __future__ import annotations

import csv
import glob
import gzip
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ContractResult:
    ok: bool
    score: float          # 0.0–1.0; -1.0 = sentinel "no contract"
    reason: str
    retry_params: Optional[Dict] = field(default=None)


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------

class _BaseContract:
    KEYWORDS: Tuple[str, ...] = ()
    # "fast" <30min → 3 retries, "medium" 30m-2h → 1 retry, "long" >2h → 0 retries
    RUNTIME: str = "medium"
    # Ordered variant hints for fast tools (retry 1→[0], retry 2→[1], retry 3→[2])
    VARIANTS: List[str] = []

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        raise NotImplementedError

    @staticmethod
    def _glob_first(run_dir: str, *patterns: str) -> Optional[str]:
        for pat in patterns:
            hits = glob.glob(os.path.join(run_dir, "**", pat), recursive=True)
            if hits:
                return hits[0]
        return None

    @staticmethod
    def _glob_all(run_dir: str, *patterns: str) -> List[str]:
        results: List[str] = []
        for pat in patterns:
            results.extend(glob.glob(os.path.join(run_dir, "**", pat), recursive=True))
        return results

    @staticmethod
    def _file_nonempty(path: Optional[str]) -> bool:
        return bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)

    @staticmethod
    def _parse_float_stdout(stdout: str, pattern: str) -> Optional[float]:
        m = re.search(pattern, stdout, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None


# ===========================================================================
# QC / TRIMMING
# ===========================================================================

class FastpContract(_BaseContract):
    """
    Output: fastp.json  (always written unless --json /dev/null)
    Scoring: fraction of reads passing filter.
    Source: https://github.com/OpenGene/fastp — JSON schema summary.*_filtering.total_reads
    Threshold 0.40: below this, the library is too degraded for downstream use.
    """
    KEYWORDS = ("fastp", "quality control", "qc reads", "trim reads", "adapter trim",
                "read trimming", "filter reads")
    RUNTIME = "fast"
    VARIANTS = [
        "lower --qualified_quality_phred to 15 (default is 20) to retain more reads",
        "disable quality trimming flags --disable_quality_filtering; only trim adapters",
        "reduce --length_required to 30 and --average_qual to 10",
    ]
    THRESHOLD = 0.40

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        json_path = self._glob_first(run_dir, "fastp.json", "*fastp*.json")
        html_path = self._glob_first(run_dir, "fastp.html", "*fastp*.html")

        if not json_path and not html_path:
            return ContractResult(
                ok=False, score=0.0,
                reason="fastp: no output JSON/HTML found",
                retry_params={"hint": "verify --json and --html output paths in the fastp command"},
            )

        score = 1.0
        if json_path:
            try:
                with open(json_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                total_in  = data["summary"]["before_filtering"]["total_reads"]
                total_out = data["summary"]["after_filtering"]["total_reads"]
                if total_in > 0:
                    score = total_out / total_in
            except Exception:
                score = 0.6  # JSON present but unparseable — assume partial success

        if score < self.THRESHOLD:
            return ContractResult(
                ok=False, score=score,
                reason=f"fastp: {score*100:.1f}% reads kept (threshold {self.THRESHOLD*100:.0f}%)",
                retry_params={"hint": "lower --qualified_quality_phred (try 15) or reduce --cut_mean_quality"},
            )
        return ContractResult(ok=True, score=score,
                              reason=f"fastp: {score*100:.1f}% reads kept")


class BbdukContract(_BaseContract):
    """
    Output: user-specified FASTQ file; stats appear in stderr.
    Scoring: parse 'Input:' and 'Result:' from bbduk stderr/stdout.
    Source: https://jgi.doe.gov/data-and-tools/software-tools/bbtools/bb-tools-user-guide/bbduk-guide/
    """
    KEYWORDS = ("bbduk", "bbtools", "adapter removal", "kmer trim")
    RUNTIME = "fast"
    VARIANTS = [
        "lower qtrim threshold: use trimq=15 instead of trimq=20",
        "lower minlen=30 to keep shorter reads after trimming",
        "remove ktrim=r and run in kmer-counting mode only (no trimming) to check adapter presence",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        # bbduk output is just a filtered FASTQ; check for any .fastq.gz or .fq.gz
        out_fq = self._glob_first(run_dir, "*.fastq.gz", "*.fq.gz", "*.fastq", "*.fq")
        if not out_fq:
            return ContractResult(
                ok=False, score=0.0,
                reason="bbduk: no output FASTQ found",
                retry_params={"hint": "check out= path in bbduk command and ensure input reads exist"},
            )

        # Parse retention rate from stderr-captured stdout
        m_in  = re.search(r"Input:\s+([\d,]+)\s+reads", stdout)
        m_out = re.search(r"Result:\s+([\d,]+)\s+reads", stdout)
        if m_in and m_out:
            n_in  = int(m_in.group(1).replace(",", ""))
            n_out = int(m_out.group(1).replace(",", ""))
            score = n_out / n_in if n_in > 0 else 1.0
        else:
            score = 0.8  # file found, can't parse ratio
        return ContractResult(ok=True, score=score,
                              reason=f"bbduk: output FASTQ found (retention={score*100:.1f}%)")


# ===========================================================================
# TAXONOMIC CLASSIFICATION
# ===========================================================================

class Kraken2Contract(_BaseContract):
    """
    Output: *.report (TSV, tab-delimited)
    Columns: %reads, clade_covered_reads, taxon_reads, rank, taxid, name
    Scoring: % reads classified.
    Source: https://github.com/DerrickWood/kraken2/blob/master/docs/MANUAL.markdown
    Threshold: 5% — metagenomics samples with very low biomass can be sparse.
    """
    KEYWORDS = ("kraken2", "kraken", "taxonomic classif", "classify reads", "kraken report",
                "kraken database", "kraken2 --db")
    RUNTIME = "medium"
    VARIANTS = [
        "add --confidence 0 to disable confidence threshold and classify all reads",
    ]
    THRESHOLD = 0.05

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        report = self._glob_first(run_dir, "*.report", "*_report.txt", "*kraken*.txt",
                                  "*kraken2*.report")
        if not report:
            return ContractResult(
                ok=False, score=0.0,
                reason="kraken2: no report file found",
                retry_params={"hint": "add --report <path>.report to the kraken2 command"},
            )

        score: Optional[float] = None
        m = re.search(r"([\d.]+)\s*%\s*reads\s+class", stdout, re.IGNORECASE)
        if m:
            score = float(m.group(1)) / 100.0
        else:
            try:
                with open(report, encoding="utf-8") as fh:
                    for line in fh:
                        parts = line.strip().split("\t")
                        # Report format: %\tclade_reads\ttaxon_reads\trank\ttaxid\tname
                        if len(parts) >= 6 and "root" in parts[5].strip():
                            score = float(parts[0].strip()) / 100.0
                            break
            except Exception:
                pass

        if score is None:
            return ContractResult(ok=True, score=0.5,
                                  reason="kraken2: report found (classification rate unparseable)")
        if score < self.THRESHOLD:
            return ContractResult(
                ok=False, score=score,
                reason=f"kraken2: {score*100:.1f}% classified (threshold {self.THRESHOLD*100:.0f}%)",
                retry_params={"hint": "try a larger database or lower --confidence (default 0.0) to 0"},
            )
        return ContractResult(ok=True, score=score,
                              reason=f"kraken2: {score*100:.1f}% reads classified")


class SylphContract(_BaseContract):
    """
    Output: TSV file with columns:
      Sample_file, Genome_file, Taxonomic_abundance, Sequence_abundance,
      Adjusted_ANI, Eff_cov, ANI_5-95_percentile, ...
    Scoring: count rows with Adjusted_ANI >= 95% (species-level threshold).
    Source: https://sylph-docs.github.io/Output-format/
            Nature Biotechnology 2024, doi:10.1038/s41587-024-02412-y
    """
    KEYWORDS = ("sylph", "sylph profile", "sylph sketch", "ANI-based profil")
    RUNTIME = "fast"
    VARIANTS = [
        "use sylph profile with --min-count-correct 3 instead of default to reduce false positives",
        "re-sketch reads with a larger sketch size (-c 200) for better sensitivity",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*profile*.tsv", "*sylph*.tsv", "*.tsv")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="sylph: no output TSV found",
                retry_params={"hint": "add -o <output.tsv> to sylph profile command"},
            )

        try:
            hits = 0
            total = 0
            with open(tsv, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    ani_val = row.get("Adjusted_ANI", "")
                    try:
                        if float(ani_val) >= 95.0:
                            hits += 1
                    except (ValueError, TypeError):
                        pass
            if total == 0:
                return ContractResult(ok=True, score=0.3,
                                      reason="sylph: TSV found but no genome rows")
            score = min(1.0, hits / max(1, total))
            return ContractResult(ok=True, score=score,
                                  reason=f"sylph: {hits}/{total} genomes ≥95% ANI")
        except Exception as e:
            return ContractResult(ok=True, score=0.5,
                                  reason=f"sylph: TSV found (parse error: {e})")


class KaijuContract(_BaseContract):
    """
    Output: kaiju.out (3-col: C/U, read_id, taxid)
            kaiju2table output: file, percent, reads, taxon_id, taxon_name
    Scoring: % reads classified = 100 - percent(Unclassified row, taxon_id=0).
    Source: https://github.com/bioinformatics-centre/kaiju
            https://taxpasta.readthedocs.io/en/0.2.3/supported_profilers/kaiju/
    Threshold: 5% (same rationale as kraken2)
    """
    KEYWORDS = ("kaiju", "kaiju2table", "kaiju classify", "kaiju -t")
    RUNTIME = "fast"
    VARIANTS = [
        "lower -m (minimum match length) from 11 to 7 for higher sensitivity",
        "add -e 5 to allow up to 5 mismatches in the match",
        "switch to greedy mode by removing -m and adding -s 60 (minimum score)",
    ]
    THRESHOLD = 0.05

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        kaiju_out  = self._glob_first(run_dir, "kaiju.out", "*.out", "*kaiju*.txt")
        kaiju_summ = self._glob_first(run_dir, "*summary*.tsv", "*kaiju2table*", "*_summary.tsv")

        if not kaiju_out and not kaiju_summ:
            return ContractResult(
                ok=False, score=0.0,
                reason="kaiju: no output file found",
                retry_params={"hint": "add -o <output.txt> to kaiju command and verify -t/-f database paths"},
            )

        score: Optional[float] = None

        # Try kaiju2table summary first: column "percent", taxon_id 0 = unclassified
        if kaiju_summ:
            try:
                with open(kaiju_summ, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        if str(row.get("taxon_id", "")).strip() == "0":
                            pct_unclass = float(row.get("percent", "100"))
                            score = (100.0 - pct_unclass) / 100.0
                            break
            except Exception:
                pass

        # Fallback: count C/U lines in kaiju.out
        if score is None and kaiju_out:
            try:
                classified = unclassified = 0
                with open(kaiju_out, encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("C\t"):
                            classified += 1
                        elif line.startswith("U\t"):
                            unclassified += 1
                total = classified + unclassified
                if total > 0:
                    score = classified / total
            except Exception:
                pass

        if score is None:
            return ContractResult(ok=True, score=0.5,
                                  reason="kaiju: output found (classification rate unparseable)")
        if score < self.THRESHOLD:
            return ContractResult(
                ok=False, score=score,
                reason=f"kaiju: {score*100:.1f}% classified (threshold {self.THRESHOLD*100:.0f}%)",
                retry_params={"hint": "lower -m (minimum match length) from 11 to 7 for higher sensitivity"},
            )
        return ContractResult(ok=True, score=score,
                              reason=f"kaiju: {score*100:.1f}% reads classified")


# ===========================================================================
# ASSEMBLY
# ===========================================================================

class AssemblyContract(_BaseContract):
    """
    Covers metaSPAdes, SPAdes, MEGAHIT, Flye, metaFlye.
    Output: contigs.fasta / scaffolds.fasta (SPAdes) or final.contigs.fa (MEGAHIT)
            or assembly.fasta (Flye).
    Scoring: N50 from QUAST report.tsv if available; otherwise file-size proxy.
    Source: QUAST report.tsv column 'N50' (verified column name).
    """
    KEYWORDS = (
        "assembly", "assemble", "metaspades", "spades", "megahit",
        "flye", "metaflye", "de novo", "scaffold", "co-assembly",
    )
    RUNTIME = "long"
    # long → 0 retries; single best hint injected then observer
    VARIANTS = [
        "reduce --memory (SPAdes) or -m (MEGAHIT) to 50% of current value; lower --k-list to '21,33,55'",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        contigs = self._glob_first(
            run_dir,
            "contigs.fasta", "scaffolds.fasta", "final.contigs.fa",
            "assembly.fasta", "flye.fasta", "*.fasta", "*.fa",
        )
        if not contigs:
            return ContractResult(
                ok=False, score=0.0,
                reason="assembly: no contig/scaffold FASTA found",
                retry_params={"hint": "check assembler output directory; reduce --memory or --k-list for SPAdes"},
            )

        size = os.path.getsize(contigs)
        if size < 1000:
            return ContractResult(
                ok=False, score=0.1,
                reason=f"assembly: output FASTA exists but nearly empty ({size} bytes)",
                retry_params={"hint": "lower --min-contig-len (try 200) or increase --memory"},
            )

        # Parse N50 from QUAST report if present
        quast_report = self._glob_first(run_dir, "report.tsv", "transposed_report.tsv")
        if quast_report:
            try:
                with open(quast_report, encoding="utf-8") as fh:
                    reader = csv.reader(fh, delimiter="\t")
                    for row in reader:
                        if row and row[0].strip() == "N50":
                            n50 = int(row[1].strip().replace(",", ""))
                            score = min(1.0, n50 / 50_000)  # 50 kb = 1.0
                            return ContractResult(ok=True, score=score,
                                                  reason=f"assembly: N50={n50:,} bp")
            except Exception:
                pass

        score = min(1.0, size / 1_000_000)
        return ContractResult(ok=True, score=score,
                              reason=f"assembly: contig file found ({size:,} bytes)")


# ===========================================================================
# BINNING
# ===========================================================================

class SemiBin2Contract(_BaseContract):
    """
    Output directory: output_bins/ with SemiBin_{label}.fa.gz (compressed by default)
                      or *.fa (uncompressed with --no-gz-compress)
    Also writes: bins_info.tsv (bp count, contig count, N50, L50 per bin)
                 contig_bins.tsv (contig → bin mapping)
    Source: https://semibin.readthedocs.io/en/latest/output/
            PMC10311329 (SemiBin2 paper, Bioinformatics 2023)
    """
    KEYWORDS = ("semibin2", "semibin", "single_easy_bin", "semibin single")
    RUNTIME = "long"
    VARIANTS = [
        "lower --min-len from 1000 to 500; if no BAM is provided try adding --self-supervised flag",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        # Bins can be .fa.gz (default) or .fa (--no-gz-compress)
        bins_gz = self._glob_all(run_dir, "SemiBin_*.fa.gz", "*.fa.gz")
        bins_fa = self._glob_all(run_dir, "SemiBin_*.fa", "*.fa")
        # bins_info.tsv: per-bin bp count, contig count, N50, L50
        bins_info = self._glob_first(run_dir, "bins_info.tsv")

        all_bins = bins_gz + bins_fa
        if not all_bins and not bins_info:
            return ContractResult(
                ok=False, score=0.0,
                reason="semibin2: no bin files (SemiBin_*.fa.gz / bins_info.tsv) found",
                retry_params={"hint": "lower --min-len (try 500 instead of 1000); check -b BAM file exists"},
            )

        n_bins = len(all_bins)
        if n_bins == 0 and bins_info:
            # Count rows in bins_info.tsv
            try:
                with open(bins_info, encoding="utf-8") as fh:
                    n_bins = sum(1 for _ in fh) - 1  # subtract header
            except Exception:
                n_bins = 1

        score = min(1.0, n_bins / 10.0)
        return ContractResult(ok=True, score=score,
                              reason=f"semibin2: {n_bins} bin(s) produced")


class ConcoctContract(_BaseContract):
    """
    Output pipeline:
      1. cut_up_fasta.py → *.bed + *_10K.fasta
      2. concoct_coverage_table.py → coverage_table.tsv
      3. concoct -b concoct_output/ → concoct_output/clustering_gt1000.csv
      4. merge_cutup_clustering.py → clustering_merged.csv
      5. extract_fasta_bins.py → bins/*.fa
    Key file: clustering_merged.csv (contig_id, cluster_id — no header, comma-separated)
              bins/*.fa (actual bin FASTA files after extraction)
    Source: https://concoct-doctest.readthedocs.io/en/latest/complete_example.html
            https://github.com/BinPro/CONCOCT
    """
    KEYWORDS = ("concoct", "concoct -c", "concoct binning", "concoct_coverage")
    RUNTIME = "medium"
    VARIANTS = [
        "increase -c (max clusters) to 100 or 150; verify concoct_coverage_table.py was run first",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        # Final bin FASTAs (step 5)
        bin_fastas = self._glob_all(run_dir, "bins/*.fa", "bins/*.fasta")
        # Intermediate clustering CSV (step 3 or 4)
        clustering  = self._glob_first(run_dir, "clustering_merged.csv",
                                       "clustering_gt1000.csv", "clustering*.csv")

        if not bin_fastas and not clustering:
            return ContractResult(
                ok=False, score=0.0,
                reason="concoct: no clustering CSV or bin FASTA files found",
                retry_params={"hint": "verify full CONCOCT pipeline ran (all 5 steps); check -c (max clusters)"},
            )

        n_bins = len(bin_fastas)

        # If only clustering CSV, count unique cluster IDs (column 2, 0-indexed)
        if n_bins == 0 and clustering:
            try:
                ids: set = set()
                with open(clustering, encoding="utf-8") as fh:
                    for line in fh:
                        parts = line.strip().split(",")
                        if len(parts) >= 2:
                            ids.add(parts[1].strip())
                n_bins = len(ids)
            except Exception:
                n_bins = 1

        score = min(1.0, n_bins / 10.0)
        return ContractResult(ok=True, score=score,
                              reason=f"concoct: {n_bins} cluster(s)/bin(s)")


class MaxBin2Contract(_BaseContract):
    """
    Output: {prefix}.001.fasta, {prefix}.002.fasta, ... (zero-padded)
            {prefix}.summary (tab-sep: Bin name, Completeness, Genome size, GC content)
            {prefix}.marker  (marker gene counts per bin)
            {prefix}.noclass (unassigned contigs)
    Scoring: mean Completeness from summary file (values like "57.9%").
    Source: https://downloads.jbei.org/data/microbial_communities/MaxBin/MaxBin.html
    """
    KEYWORDS = ("maxbin2", "maxbin", "run_maxbin", "run_MaxBin2")
    RUNTIME = "medium"
    VARIANTS = [
        "lower -prob_threshold from 0.9 to 0.7 to bin more contigs at lower confidence",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        summary = self._glob_first(run_dir, "*.summary")
        bin_fastas = self._glob_all(run_dir, "*.fasta", "*.fa")
        # Filter to numbered bins only (e.g. out.001.fasta)
        bin_fastas = [f for f in bin_fastas
                      if re.search(r"\.\d{3}\.(fasta|fa)$", f, re.IGNORECASE)]

        if not summary and not bin_fastas:
            return ContractResult(
                ok=False, score=0.0,
                reason="maxbin2: no .summary or numbered .fasta bin files found",
                retry_params={"hint": "lower -prob_threshold from 0.9 to 0.7; check -contig input path"},
            )

        n_bins = len(bin_fastas)
        mean_completeness: Optional[float] = None

        if summary:
            try:
                values = []
                with open(summary, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        # Column: "Completeness" with values like "57.9%"
                        c_str = row.get("Completeness", "").replace("%", "").strip()
                        if c_str:
                            values.append(float(c_str))
                        if n_bins == 0:
                            n_bins += 1
                if values:
                    mean_completeness = sum(values) / len(values)
            except Exception:
                pass

        if mean_completeness is not None:
            score = min(1.0, mean_completeness / 100.0)
            return ContractResult(ok=True, score=score,
                                  reason=f"maxbin2: {n_bins} bin(s), mean completeness={mean_completeness:.1f}%")

        score = min(1.0, n_bins / 10.0)
        return ContractResult(ok=True, score=score,
                              reason=f"maxbin2: {n_bins} bin(s) found")


class MetaBat2Contract(_BaseContract):
    """
    Output: bin.1.fa, bin.2.fa, ... (MetaBAT2 default naming convention)
    Scoring: number of bins produced, normalized to 10 bins = 1.0.
    Source: https://bitbucket.org/berkeleylab/metabat/src/master/
    """
    KEYWORDS = ("metabat", "metabat2", "genome bins", "bin contigs")
    RUNTIME = "medium"
    VARIANTS = [
        "lower --minContig from 2500 to 1500 to include shorter contigs in binning",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        all_fa = self._glob_all(run_dir, "bin.*.fa", "bin*.fa", "*.bin.fa", "*.fa")
        bins = [b for b in all_fa
                if re.search(r"bin[._]?\d", os.path.basename(b), re.IGNORECASE)]

        if not bins:
            return ContractResult(
                ok=False, score=0.0,
                reason="metabat2: no bin FASTA files found",
                retry_params={"hint": "lower --minContig to 1500; verify coverage depth file (jgi_summarize_bam_contig_depths)"},
            )

        score = min(1.0, len(bins) / 10.0)
        return ContractResult(ok=True, score=score,
                              reason=f"metabat2: {len(bins)} bin(s) produced")


# ===========================================================================
# BIN QUALITY
# ===========================================================================

class CheckM2Contract(_BaseContract):
    """
    Output: quality_report.tsv
    Exact columns: Name, Completeness, Contamination, Completeness_Model_Used, ...
    Scoring: mean(Completeness - 5*Contamination) / 100 per bin.
    Source: https://github.com/chklovski/CheckM2
            Standard MIMAG thresholds: high-quality ≥90% comp, <5% cont.
    """
    KEYWORDS = ("checkm2", "checkm", "bin quality", "bin completeness",
                "genome quality", "bin contamination")
    RUNTIME = "medium"
    VARIANTS = [
        "verify --database_path points to the CheckM2 database directory (contains uniref100.KO.1.dmnd); re-run checkm2 database --download if missing",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        report = self._glob_first(run_dir, "quality_report.tsv", "*checkm2*.tsv",
                                  "checkm2_output*.tsv")
        if not report:
            return ContractResult(
                ok=False, score=0.0,
                reason="checkm2: quality_report.tsv not found",
                retry_params={"hint": "verify --database_path and --input directory (bins with .fna/.fa extension)"},
            )

        try:
            scores: List[float] = []
            with open(report, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    comp = float(row.get("Completeness", 0) or 0)
                    cont = float(row.get("Contamination", 0) or 0)
                    scores.append(max(0.0, comp - 5.0 * cont))

            if not scores:
                return ContractResult(ok=True, score=0.5,
                                      reason="checkm2: report found but no bin rows")
            avg = sum(scores) / len(scores)
            return ContractResult(ok=True, score=min(1.0, avg / 100.0),
                                  reason=f"checkm2: {len(scores)} bin(s), avg quality={avg:.1f}")
        except Exception as e:
            return ContractResult(ok=True, score=0.5,
                                  reason=f"checkm2: report found (parse error: {e})")


# ===========================================================================
# FUNCTIONAL ANNOTATION
# ===========================================================================

class HmmerContract(_BaseContract):
    """
    Output: *.tblout (per-sequence) or *.domtblout (per-domain)
    tblout columns (space-delimited, 19 cols):
      target.name, accession, query.name, accession, E-value, score, bias, ...
    domtblout columns (space-delimited, 23 cols): target.name, ... c-Evalue, ...
    Scoring: count hits with full-sequence E-value < 1e-5 (canonical threshold).
    Source: HMMER3 User Guide pp.67–70; rhmmer column reference.
    """
    KEYWORDS = ("hmmer", "hmmscan", "hmmsearch", "hmmpress", "pfam scan",
                "protein domain", "hmm profile")
    RUNTIME = "fast"
    VARIANTS = [
        "raise E-value threshold: use -E 1e-3 instead of 1e-5 to capture more distant homologs",
        "add --cut_ga flag to use profile-specific Pfam gathering thresholds instead of a fixed E-value",
        "use -E 0.01 and --domE 0.01 for both full-sequence and domain-level thresholds",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tblout    = self._glob_first(run_dir, "*.tblout", "*_tblout.tsv", "*hmmer*.tsv")
        domtblout = self._glob_first(run_dir, "*.domtblout", "*_domtblout.tsv")

        if not tblout and not domtblout:
            return ContractResult(
                ok=False, score=0.0,
                reason="hmmer: no .tblout or .domtblout output found",
                retry_params={"hint": "add --tblout <path.tblout> to hmmscan/hmmsearch command"},
            )

        hits = 0
        target_file = tblout or domtblout
        try:
            with open(target_file, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 6:
                        continue
                    # Column 5 (0-indexed) = full-sequence E-value
                    try:
                        evalue = float(parts[4])
                        if evalue < 1e-5:
                            hits += 1
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass

        score = min(1.0, hits / 100.0)  # 100 significant hits = score 1.0
        return ContractResult(ok=True, score=score,
                              reason=f"hmmer: {hits} hit(s) with E-value < 1e-5")


class EggnogContract(_BaseContract):
    """
    Output: {prefix}.emapper.annotations (tab-separated, 21 columns)
    Header line starts with '#query' (v2.1+).
    Key columns: #query, seed_ortholog, evalue, score, COG_category, KEGG_ko
    Scoring: fraction of queries with a valid annotation (non-empty COG_category).
    Source: https://github.com/eggnogdb/eggnog-mapper/wiki/eggNOG-mapper-v2.0.2-v2.0.8
            MBE 2021 doi:10.1093/molbev/msab293
    """
    KEYWORDS = ("eggnog", "eggnog-mapper", "emapper", "emapper.py",
                "functional annotation", "cog annotation")
    RUNTIME = "medium"
    VARIANTS = [
        "add --sensmode ultra-sensitive to emapper.py for deeper ortholog search",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        annot = self._glob_first(run_dir, "*.emapper.annotations",
                                 "*.annotations", "*emapper*")
        if not annot:
            return ContractResult(
                ok=False, score=0.0,
                reason="eggnog-mapper: *.emapper.annotations not found",
                retry_params={"hint": "check -i (input proteins), --output prefix, and --data_dir (eggnog db path)"},
            )

        annotated = total = 0
        try:
            with open(annot, encoding="utf-8") as fh:
                reader = csv.reader(fh, delimiter="\t")
                for row in reader:
                    if not row or row[0].startswith("#"):
                        continue
                    total += 1
                    # COG_category is column index 6 (0-based) in v2.1+
                    if len(row) > 6 and row[6].strip() not in ("", "-"):
                        annotated += 1
        except Exception:
            pass

        score = (annotated / total) if total > 0 else 0.5
        return ContractResult(ok=True, score=score,
                              reason=f"eggnog: {annotated}/{total} queries annotated with COG")


class DiamondContract(_BaseContract):
    """
    Output: user-specified TSV (outfmt 6) or .m8 file.
    outfmt 6 columns: qseqid sseqid pident length mismatch gapopen
                      qstart qend sstart send evalue bitscore
    Scoring: file size proxy (100 KB = 1.0).
    Source: https://github.com/bbuchfink/diamond/wiki/3.-Command-line-options
    """
    KEYWORDS = ("diamond", "diamond blastp", "diamond blastx", "protein alignment",
                "diamond --db", "diamond blastx -q")
    RUNTIME = "medium"
    VARIANTS = [
        "add --more-sensitive flag for 2x more sensitive mode; lower --evalue to 1e-3",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        out = self._glob_first(run_dir, "*.tsv", "*.m8", "*.txt", "*.out", "*.daa")
        if not out:
            return ContractResult(
                ok=False, score=0.0,
                reason="diamond: no output file found",
                retry_params={"hint": "check -o output path and -d database path"},
            )
        size = os.path.getsize(out)
        score = min(1.0, size / 100_000)
        return ContractResult(ok=True, score=score,
                              reason=f"diamond: output {size:,} bytes")


class EggnogHumannContract(_BaseContract):
    """
    Output: *_genefamilies.tsv, *_pathabundance.tsv, *_pathcoverage.tsv
    Source: https://huttenhower.sph.harvard.edu/humann
    """
    KEYWORDS = ("humann", "humann3", "functional profil", "pathway abundance",
                "humann --input", "humann3 --input")
    RUNTIME = "long"
    VARIANTS = [
        "verify --nucleotide-database (ChocoPhlAn) and --protein-database (UniRef90) paths are correct and non-empty",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        genefam = self._glob_first(run_dir, "*genefamilies*", "*gene_families*")
        pathway = self._glob_first(run_dir, "*pathabundance*", "*pathway_abundance*")
        if not genefam and not pathway:
            return ContractResult(
                ok=False, score=0.0,
                reason="humann3: genefamilies/pathabundance output not found",
                retry_params={"hint": "check --output dir and --nucleotide-database / --protein-database paths"},
            )
        return ContractResult(ok=True, score=1.0,
                              reason="humann3: output tables found")


# ===========================================================================
# SPECIALIZED ANNOTATION
# ===========================================================================

class AntismashContract(_BaseContract):
    """
    Output: {contig_id}.region{NNN}.gbk — one file per BGC region found.
            index.html + {genome}.json in output directory.
    Scoring: number of BGC regions (*.region*.gbk count).
    Source: https://docs.antismash.secondarymetabolites.org/understanding_output/
    Note: 0 BGC regions is a valid result for non-BGC-rich organisms.
    """
    KEYWORDS = ("antismash", "biosynthetic gene cluster", "bgc", "secondary metabolite",
                "antismash --taxon", "antismash --cpus")
    RUNTIME = "medium"
    VARIANTS = [
        "add --genefinding-tool prodigal-m (meta mode) for metagenomic contigs instead of prodigal",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        index   = self._glob_first(run_dir, "index.html")
        regions = self._glob_all(run_dir, "*.region*.gbk")
        genome_json = self._glob_first(run_dir, "*.json")

        if not index and not genome_json:
            return ContractResult(
                ok=False, score=0.0,
                reason="antismash: no index.html or JSON output found",
                retry_params={"hint": "check --output-dir and input FASTA path; try --genefinding-tool prodigal"},
            )

        n_bgc = len(regions)
        # 0 regions = valid (no BGCs in this organism); score 0.5 as neutral
        score = min(1.0, 0.5 + n_bgc / 20.0) if n_bgc > 0 else 0.5
        return ContractResult(ok=True, score=score,
                              reason=f"antismash: {n_bgc} BGC region(s) detected")


class GenomadContract(_BaseContract):
    """
    Output: {prefix}_summary/{prefix}_virus_summary.tsv
            {prefix}_summary/{prefix}_plasmid_summary.tsv
    Virus TSV columns: seq_name, length, topology, coordinates, n_genes, genetic_code,
                       virus_score, fdr, n_hallmarks, marker_enrichment, taxonomy
    Scoring: mean virus_score of detected sequences (threshold 0.70 from official docs).
    Source: https://portal.nersc.gov/genomad/quickstart.html
            https://portal.nersc.gov/genomad/post_classification_filtering.html
            Nature Biotechnology 2023 doi:10.1038/s41587-023-01953-y
    """
    KEYWORDS = ("genomad", "genomad end-to-end", "virus identification",
                "plasmid identification", "virome")
    RUNTIME = "fast"
    VARIANTS = [
        "add --min-score 0.5 to use relaxed threshold instead of default 0.7",
        "add --relaxed flag (equivalent to --min-score 0.0) to capture all candidate sequences",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        virus_tsv   = self._glob_first(run_dir, "*_virus_summary.tsv")
        plasmid_tsv = self._glob_first(run_dir, "*_plasmid_summary.tsv")

        if not virus_tsv and not plasmid_tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="genomad: no *_virus_summary.tsv or *_plasmid_summary.tsv found",
                retry_params={"hint": "verify genomad database path (3rd positional arg); check --splits value"},
            )

        scores: List[float] = []
        target = virus_tsv or plasmid_tsv
        score_col = "virus_score" if virus_tsv else "plasmid_score"
        try:
            with open(target, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    v = row.get(score_col, "")
                    try:
                        scores.append(float(v))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        if not scores:
            return ContractResult(ok=True, score=0.5,
                                  reason="genomad: summary found but no scored sequences")
        above_threshold = sum(1 for s in scores if s >= 0.70)
        score = min(1.0, above_threshold / max(1, len(scores)))
        return ContractResult(ok=True, score=score,
                              reason=f"genomad: {above_threshold}/{len(scores)} seqs with score≥0.70")


class AbricateContract(_BaseContract):
    """
    Output: TSV to stdout (redirected to file), 15 columns:
      FILE, SEQUENCE, START, END, STRAND, GENE, COVERAGE, COVERAGE_MAP, GAPS,
      %COVERAGE, %IDENTITY, DATABASE, ACCESSION, PRODUCT, RESISTANCE
    Scoring: fraction of hits passing %IDENTITY≥75 AND %COVERAGE≥80 (abricate defaults).
    Source: https://github.com/tseemann/abricate
    """
    KEYWORDS = ("abricate", "resistance gene", "arg screening", "abricate --db",
                "antibiotic resistance", "virulence factor screen")
    RUNTIME = "fast"
    VARIANTS = [
        "lower --minid to 60 (default 75) to detect more divergent resistance genes",
        "lower --mincov to 60 (default 80) AND --minid to 60 for maximum sensitivity",
        "try a different --db: card (resistance), vfdb (virulence), ncbi (comprehensive)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*.tsv", "abricate_*.txt", "*abricate*")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="abricate: no output TSV found (abricate writes to stdout — check redirect)",
                retry_params={"hint": "add '> output.tsv' to abricate command; check --db name (card/vfdb/ncbi...)"},
            )

        total = passing = 0
        try:
            with open(tsv, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    try:
                        pct_id  = float(row.get("%IDENTITY", 0))
                        pct_cov = float(row.get("%COVERAGE", 0))
                        if pct_id >= 75.0 and pct_cov >= 80.0:
                            passing += 1
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        if total == 0:
            return ContractResult(ok=True, score=0.5,
                                  reason="abricate: TSV found but no hits (0 resistance genes)")
        score = passing / total
        return ContractResult(ok=True, score=score,
                              reason=f"abricate: {passing}/{total} hits pass ≥75% ID / ≥80% coverage")


class DbcanContract(_BaseContract):
    """
    Output: overview.txt (tab-separated, 6 columns):
      Gene ID, EC#, HMMER, dbCAN_sub, DIAMOND, #ofTools
    Scoring: fraction of predicted CAZymes with #ofTools ≥ 2 (consensus recommendation).
    Source: https://run-dbcan.readthedocs.io/en/latest/user_guide/CAZyme_annotation.html
    """
    KEYWORDS = ("dbcan", "run_dbcan", "cazyme", "carbohydrate-active", "dbcan annotation")
    RUNTIME = "fast"
    VARIANTS = [
        "add --tools hmmer diamond dbCANsub to run all three tools and get #ofTools consensus",
        "switch to --mode meta for metagenomic/fragmented sequences instead of --mode prok",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        overview = self._glob_first(run_dir, "overview.txt", "*overview*")
        if not overview:
            return ContractResult(
                ok=False, score=0.0,
                reason="dbcan: overview.txt not found",
                retry_params={"hint": "check --out_dir path and --db_dir (dbCAN database directory)"},
            )

        total = consensus = 0
        try:
            with open(overview, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    try:
                        n_tools = int(row.get("#ofTools", 0))
                        if n_tools >= 2:
                            consensus += 1
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        if total == 0:
            return ContractResult(ok=True, score=0.5,
                                  reason="dbcan: overview.txt found but no gene rows")
        score = consensus / total
        return ContractResult(ok=True, score=score,
                              reason=f"dbcan: {consensus}/{total} CAZymes predicted by ≥2 tools")


class PharokkaContract(_BaseContract):
    """
    Output: {prefix}.gff (GFF3), {prefix}.gbk (GenBank)
            {prefix}_cds_functions.tsv (CDS/tRNA/tmRNA/CRISPR counts + PHROG functions)
            {prefix}_length_gc_cds_density.tsv (phage stats)
    Scoring: presence of .gff + .gbk; bonus from CDS count if parseable.
    Source: https://pharokka.readthedocs.io/en/stable/output/
    """
    KEYWORDS = ("pharokka", "phage annotation", "phage genome annotation",
                "pharokka.py -i", "pharokka -i")
    RUNTIME = "fast"
    VARIANTS = [
        "add -f to force overwrite if output directory already exists",
        "switch gene caller: use -g prodigal instead of default phanotate for non-standard phages",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        gff = self._glob_first(run_dir, "*.gff")
        gbk = self._glob_first(run_dir, "*.gbk")

        if not gff and not gbk:
            return ContractResult(
                ok=False, score=0.0,
                reason="pharokka: no GFF or GBK output found",
                retry_params={"hint": "check -o output dir; add -f to force overwrite if dir exists"},
            )

        # Try to count CDS from cds_functions.tsv or from stdout
        cds_count = 0
        cds_tsv = self._glob_first(run_dir, "*cds_functions*")
        if cds_tsv:
            try:
                with open(cds_tsv, encoding="utf-8") as fh:
                    for line in fh:
                        m = re.search(r"CDS.*?(\d+)", line, re.IGNORECASE)
                        if m:
                            cds_count = int(m.group(1))
                            break
            except Exception:
                pass

        score = min(1.0, cds_count / 300.0) if cds_count > 0 else 0.7
        reason = (f"pharokka: {cds_count} CDS annotated" if cds_count
                  else "pharokka: GFF/GBK output found")
        return ContractResult(ok=True, score=score, reason=reason)


class ProkkaContract(_BaseContract):
    """
    Output: *.gff, *.faa, *.gbk
            *.txt (summary: CDS count, rRNA count, etc.)
    Scoring: CDS count from *.txt summary; 5000 CDS = 1.0.
    Source: https://github.com/tseemann/prokka
    """
    KEYWORDS = ("prokka", "bacterial annotation", "prokka --outdir",
                "gene prediction", "genome annotation")
    RUNTIME = "fast"
    VARIANTS = [
        "add --force to overwrite existing output directory",
        "add --metagenome flag for metagenome-assembled genomes (disables RNAmmer)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        gff = self._glob_first(run_dir, "*.gff")
        faa = self._glob_first(run_dir, "*.faa")
        gbk = self._glob_first(run_dir, "*.gbk", "*.gbff")
        txt = self._glob_first(run_dir, "*.txt")

        if not any([gff, faa, gbk]):
            return ContractResult(
                ok=False, score=0.0,
                reason="prokka: no GFF/FAA/GBK output found",
                retry_params={"hint": "check --outdir and input FASTA; add --force to overwrite existing output"},
            )

        gene_count = 0
        if txt:
            try:
                with open(txt, encoding="utf-8") as fh:
                    for line in fh:
                        # Line like: "CDS: 3425"
                        m = re.match(r"CDS\s*:\s*(\d+)", line.strip())
                        if m:
                            gene_count = int(m.group(1))
                            break
            except Exception:
                pass

        score = min(1.0, gene_count / 5000.0) if gene_count > 0 else 0.7
        reason = (f"prokka: {gene_count} CDS annotated" if gene_count
                  else "prokka: output files found")
        return ContractResult(ok=True, score=score, reason=reason)


# ===========================================================================
# ASSEMBLY QC
# ===========================================================================

class QuastContract(_BaseContract):
    """
    Output: report.tsv (tab-separated, metric_name / value columns)
    Key rows: N50, L50, Total length, # contigs
    Scoring: N50 from report.tsv; 50 kb = 1.0.
    Source: https://quast.sourceforge.net/docs/manual.html
    """
    KEYWORDS = ("quast", "assembly qc", "assembly quality", "assembly statistics",
                "quast.py", "metaquast")
    RUNTIME = "fast"

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        report = self._glob_first(run_dir, "report.tsv", "transposed_report.tsv",
                                  "report.html")
        if not report:
            return ContractResult(
                ok=False, score=0.0,
                reason="quast: no report.tsv found",
                retry_params={"hint": "check -o output directory and input FASTA path"},
            )

        if report.endswith(".html"):
            return ContractResult(ok=True, score=1.0,
                                  reason="quast: report.html found")

        try:
            with open(report, encoding="utf-8") as fh:
                reader = csv.reader(fh, delimiter="\t")
                for row in reader:
                    if row and row[0].strip() == "N50":
                        n50 = int(row[1].strip().replace(",", ""))
                        return ContractResult(ok=True, score=min(1.0, n50 / 50_000),
                                              reason=f"quast: N50={n50:,} bp")
        except Exception:
            pass

        return ContractResult(ok=True, score=1.0, reason="quast: report found")


# ===========================================================================
# SEQUENCE MANIPULATION
# ===========================================================================

class SeqkitContract(_BaseContract):
    """
    seqkit has many subcommands; output varies.
    stats subcommand produces: file, format, type, num_seqs, sum_len, min_len,
                                avg_len, max_len, Q1, Q2, Q3, sum_gap, N50, Q20(%), Q30(%)
    For other subcommands (seq, grep, fx2tab, etc.) we just check output file exists.
    Source: https://bioinf.shenwei.me/seqkit/usage/
    """
    KEYWORDS = ("seqkit", "seqkit stats", "seqkit seq", "seqkit grep",
                "seqkit fx2tab")
    RUNTIME = "fast"

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        # Any output file
        out = self._glob_first(run_dir, "*.tsv", "*.fasta", "*.fa", "*.fastq",
                               "*.fq", "*.txt")
        if not out and not stdout.strip():
            return ContractResult(
                ok=False, score=0.0,
                reason="seqkit: no output file found",
                retry_params={"hint": "add -o <output> to seqkit command or redirect stdout"},
            )
        return ContractResult(ok=True, score=1.0,
                              reason="seqkit: output found")


# ===========================================================================
# COMMUNITY / DIVERSITY
# ===========================================================================

class NonpareilContract(_BaseContract):
    """
    Output: {prefix}.npo (6-col TSV: effort, avg_redundancy, std, Q1, Q2, Q3)
            {prefix}.npa (3-col: fraction, replicate_id, redundancy)
    Coverage is reported in stdout as "Average coverage: X.XX (XX%)"
    Standard target: ≥95% average coverage.
    Source: https://nonpareil.readthedocs.io/en/latest/redundancy.html
            mSystems 2018 PMC5893860 doi:10.1128/mSystems.00039-18
    """
    KEYWORDS = ("nonpareil", "metagenome coverage", "sequencing coverage estimate",
                "nonpareil -s")
    RUNTIME = "fast"
    VARIANTS = [
        "switch algorithm: use -T kmer instead of -T alignment for faster but slightly less accurate coverage estimate",
        "increase -r (subsampling replicates) from default 1024 to 4096 for a smoother coverage curve",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        npo = self._glob_first(run_dir, "*.npo")
        npa = self._glob_first(run_dir, "*.npa")

        if not npo and not npa:
            return ContractResult(
                ok=False, score=0.0,
                reason="nonpareil: no .npo or .npa output file found",
                retry_params={"hint": "check -b <prefix> output path and input reads format (-f fastq/fasta)"},
            )

        # Parse coverage from stdout: "Average coverage: 0.97 (97%)"
        m = re.search(r"Average coverage[:\s]+([\d.]+)\s*\(", stdout, re.IGNORECASE)
        if m:
            coverage = float(m.group(1))
            score = min(1.0, coverage)
            return ContractResult(ok=True, score=score,
                                  reason=f"nonpareil: average coverage={coverage*100:.1f}%")

        return ContractResult(ok=True, score=0.7,
                              reason="nonpareil: .npo output found (coverage unparseable from stdout)")


class LefseContract(_BaseContract):
    """
    Output: {prefix}.res (5-column TSV):
      col1: feature name
      col2: log of highest mean
      col3: class with highest mean (empty = non-discriminative)
      col4: LDA score (log10; empty if non-discriminative)
      col5: p-value from Kruskal-Wallis (empty if non-discriminative)
    Scoring: fraction of features with LDA score ≥ 2.0 (default threshold).
    Source: https://huttenhower.sph.harvard.edu/lefse/
            https://github.com/biobakery/biobakery/wiki/lefse
    """
    KEYWORDS = ("lefse", "lefse_run", "lda effect size", "linear discriminant",
                "differential abundance", "lefse_format_input")
    RUNTIME = "fast"
    VARIANTS = [
        "lower -l (LDA threshold) from 2.0 to 1.5 to detect weaker but significant biomarkers",
        "lower -a (Kruskal-Wallis alpha) from 0.05 to 0.1 and -w (Wilcoxon alpha) to 0.1",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        res = self._glob_first(run_dir, "*.res", "*lefse*.txt", "*lefse*")
        if not res:
            return ContractResult(
                ok=False, score=0.0,
                reason="lefse: no .res output file found",
                retry_params={"hint": "run lefse_run.py <formatted.in> <output.res>; verify input formatting"},
            )

        total = biomarkers = 0
        try:
            with open(res, encoding="utf-8") as fh:
                for line in fh:
                    parts = line.strip().split("\t")
                    if len(parts) < 4:
                        continue
                    total += 1
                    # col3 = class (non-empty = discriminative), col4 = LDA score
                    if parts[2].strip() and parts[3].strip():
                        try:
                            if float(parts[3].strip()) >= 2.0:
                                biomarkers += 1
                        except ValueError:
                            pass
        except Exception:
            pass

        score = min(1.0, biomarkers / 10.0)  # 10 biomarkers = 1.0
        return ContractResult(ok=True, score=score,
                              reason=f"lefse: {biomarkers}/{total} features with LDA≥2.0")


class PhyloseqContract(_BaseContract):
    """
    Output: alpha_diversity.tsv, beta_diversity.tsv, ordination.png (R script outputs)
    Source: https://joey711.github.io/phyloseq/
    """
    KEYWORDS = ("phyloseq", "alpha diversity", "beta diversity", "ordination",
                "otu table", "16s analysis")
    RUNTIME = "fast"
    VARIANTS = [
        "ensure phyloseq, vegan, and ggplot2 are installed: run install.packages(c('phyloseq','vegan','ggplot2')) in R",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        alpha  = self._glob_first(run_dir, "*alpha*", "*diversity*.tsv")
        beta   = self._glob_first(run_dir, "*beta*", "*ordination*")
        png    = self._glob_first(run_dir, "*.png", "*.pdf")

        if not any([alpha, beta, png]):
            return ContractResult(
                ok=False, score=0.0,
                reason="phyloseq: no output (TSV, PNG, PDF) found",
                retry_params={"hint": "check R script path and that phyloseq/ggplot2/vegan are installed in the R env"},
            )
        return ContractResult(ok=True, score=1.0,
                              reason="phyloseq: output files found")


# ===========================================================================
# WGS / CLINICAL
# ===========================================================================

class CnvkitContract(_BaseContract):
    """
    Output: {sample}.cnr (per-bin ratios), {sample}.cns (segments)
    .cnr columns: chromosome, start, end, gene, log2, depth, weight
    .cns columns: chromosome, start, end, gene, log2, depth, weight, probes
    Scoring: presence of .cnr + .cns; mean |log2| deviation from 0 as quality signal.
    Source: https://cnvkit.readthedocs.io/en/stable/fileformats.html
    """
    KEYWORDS = ("cnvkit", "cnvkit.py", "copy number", "cnv analysis",
                "cnvkit batch", "cnvkit segment")
    RUNTIME = "fast"
    VARIANTS = [
        "switch segmentation method: use --segment-method hmm instead of cbs for noisier data",
        "add --method wgs if whole-genome sequencing (not capture panel) was used",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        cnr = self._glob_first(run_dir, "*.cnr")
        cns = self._glob_first(run_dir, "*.cns")

        if not cnr and not cns:
            return ContractResult(
                ok=False, score=0.0,
                reason="cnvkit: no .cnr or .cns output files found",
                retry_params={"hint": "check --output-dir and BAM/reference inputs; verify samtools is in PATH"},
            )

        # Signal: mean absolute log2 deviation (higher = more CNV signal)
        if cnr:
            try:
                vals: List[float] = []
                with open(cnr, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        try:
                            vals.append(abs(float(row.get("log2", 0))))
                        except (ValueError, TypeError):
                            pass
                if vals:
                    mean_dev = sum(vals) / len(vals)
                    score = min(1.0, mean_dev / 1.0)  # 1.0 log2 deviation = score 1.0
                    return ContractResult(ok=True, score=score,
                                          reason=f"cnvkit: {len(vals)} bins, mean |log2|={mean_dev:.3f}")
            except Exception:
                pass

        return ContractResult(ok=True, score=0.8,
                              reason="cnvkit: .cnr/.cns output found")


class OptitypeContract(_BaseContract):
    """
    Output: {prefix}_{timestamp}_result.tsv (8 columns):
              [row_index], A1, A2, B1, B2, C1, C2, Reads, Objective
            {prefix}_{timestamp}_coverage_plot.pdf
    Scoring: Reads count and presence of all 6 HLA allele calls.
    Source: https://github.com/FRED-2/OptiType
    """
    KEYWORDS = ("optitype", "hla typing", "hla-typing", "optitype pipeline",
                "hla type", "optitypepipeline")
    RUNTIME = "fast"
    VARIANTS = [
        "verify razers3 is in PATH (required by OptiType); use --enumerate 3 to get alternative solutions",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        # Pattern: any TSV with timestamp in name
        result_tsv = self._glob_first(run_dir, "*_result.tsv", "*result*.tsv",
                                      "*.tsv")
        if not result_tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="optitype: no result TSV found",
                retry_params={"hint": "check --outdir and that razers3 is in PATH (required by OptiType)"},
            )

        try:
            with open(result_tsv, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    # 6 HLA allele columns: A1, A2, B1, B2, C1, C2
                    alleles = [row.get(col, "").strip()
                               for col in ("A1", "A2", "B1", "B2", "C1", "C2")]
                    filled = sum(1 for a in alleles if a)
                    reads = int(float(row.get("Reads", 0) or 0))
                    score = filled / 6.0
                    return ContractResult(
                        ok=True, score=score,
                        reason=f"optitype: {filled}/6 HLA alleles called, {reads} supporting reads",
                    )
        except Exception:
            pass

        return ContractResult(ok=True, score=0.8,
                              reason="optitype: result TSV found (alleles unparseable)")


# ===========================================================================
# Registry + dispatcher
# ===========================================================================

_ALL_CONTRACTS: List[_BaseContract] = [
    # QC / trimming
    FastpContract(),
    BbdukContract(),
    # Taxonomic
    Kraken2Contract(),
    SylphContract(),
    KaijuContract(),
    # Assembly
    AssemblyContract(),
    QuastContract(),
    # Binning
    SemiBin2Contract(),
    ConcoctContract(),
    MaxBin2Contract(),
    MetaBat2Contract(),
    # Bin quality
    CheckM2Contract(),
    # Functional annotation
    HmmerContract(),
    EggnogContract(),
    DiamondContract(),
    EggnogHumannContract(),
    # Specialized annotation
    AntismashContract(),
    GenomadContract(),
    AbricateContract(),
    DbcanContract(),
    PharokkaContract(),
    ProkkaContract(),
    # Community / diversity
    NonpareilContract(),
    LefseContract(),
    PhyloseqContract(),
    # Sequence manipulation
    SeqkitContract(),
    # WGS / clinical
    CnvkitContract(),
    OptitypeContract(),
]


class ToolValidator:
    """
    Public interface.

    ToolValidator.validate(step_title, run_dir, stdout) → ContractResult
      score = -1.0  → no contract matched → caller routes to observer (LLM)
      score in [0,1] → deterministic result

    ToolValidator.max_retries(step_title) → int
      fast   → 3   (sequential param variants, each <30 min)
      medium → 1   (one best hint, 30 min–2 h)
      long   → 0   (immediately observer, >2 h — retry cost too high)

    ToolValidator.get_variant_hint(step_title, retry_idx, fallback) → str
      Returns VARIANTS[retry_idx] if defined, else fallback hint from ContractResult.
    """

    # Maps RUNTIME string → max number of validator-driven retries before observer
    _RUNTIME_MAX_RETRIES: Dict[str, int] = {
        "fast":   3,
        "medium": 1,
        "long":   0,
    }

    @staticmethod
    def _match_contract(step_title: str) -> Optional[_BaseContract]:
        title_lower = (step_title or "").lower()
        for contract in _ALL_CONTRACTS:
            if any(kw in title_lower for kw in contract.KEYWORDS):
                return contract
        return None

    @staticmethod
    def validate(step_title: str, run_dir: str, stdout: str) -> ContractResult:
        contract = ToolValidator._match_contract(step_title)
        if contract is not None:
            return contract.check(run_dir, stdout)
        return ContractResult(ok=True, score=-1.0, reason="no_contract")

    @staticmethod
    def max_retries(step_title: str) -> int:
        """
        Return the maximum number of validator-driven retries for this step.
        fast=3, medium=1, long=0 (immediately pass to observer on failure).
        Returns 1 (medium default) if no contract matches.
        """
        contract = ToolValidator._match_contract(step_title)
        if contract is None:
            return 1  # no contract → default medium behaviour
        return ToolValidator._RUNTIME_MAX_RETRIES.get(contract.RUNTIME, 1)

    @staticmethod
    def get_variant_hint(step_title: str, retry_idx: int, fallback: str) -> str:
        """
        Return the variant hint for retry attempt `retry_idx` (0-based).
        Uses VARIANTS[retry_idx] if defined on the contract, else `fallback`.
        """
        contract = ToolValidator._match_contract(step_title)
        if contract is None:
            return fallback
        variants = getattr(contract, "VARIANTS", [])
        if variants and retry_idx < len(variants):
            return variants[retry_idx]
        return fallback

    @staticmethod
    def has_contract(step_title: str) -> bool:
        return ToolValidator._match_contract(step_title) is not None
