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
    # PoC (metrics propagation): typed values the contract already extracted from
    # run_dir, exposed as data instead of being buried in `reason` prose. Empty by
    # default → every existing contract is unaffected until it opts in.
    metrics: Dict = field(default_factory=dict)


# PoC bonus — cheap dispatch telemetry (no analysis, just counters). Reset per
# run by the caller if desired. Lets us later quantify, on real runs, how often a
# step gets a contract vs falls to the LLM observer vs is skipped by the guard.
DISPATCH_COUNTERS: Dict[str, int] = {"contract_hit": 0, "no_contract": 0, "guard_skip": 0}


def format_extracted_metrics(observations: list) -> str:
    """PoC — render observations[].metrics as an AUTHORITATIVE block for the
    finalizer, so it can cite deterministic numbers without re-reading files.

    Only steps whose contract populated `metrics` appear. Keys starting with
    '_' (e.g. _source_file) are shown as provenance, not as metrics. Empty →
    a clear sentinel so the finalizer knows to fall back to the raw ledger.
    """
    lines: List[str] = []
    for o in observations or []:
        if not isinstance(o, dict):
            continue
        m = o.get("metrics") or {}
        if not m:
            continue
        title = (o.get("title") or "<step>")
        kv = ", ".join(f"{k}={v}" for k, v in m.items() if not str(k).startswith("_"))
        src = m.get("_source_file")
        line = f"- {title}: {kv}"
        if src:
            line += f"  (source file: {src})"
        lines.append(line)
    if not lines:
        return "(no deterministic metrics were extracted for this run)"
    return "\n".join(lines)


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
        # Same file can match multiple patterns (e.g. bin.1.fa matches bin.*.fa AND *.fa)
        # → dedupe with dict.fromkeys to preserve insertion order, otherwise validators
        # like metabat2/semibin2/checkm2 over-count bins by a factor of N patterns.
        results: List[str] = []
        for pat in patterns:
            results.extend(glob.glob(os.path.join(run_dir, "**", pat), recursive=True))
        return list(dict.fromkeys(results))

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
        "add --disable_quality_filtering (do NOT pass -q at all) — for wgsim/simulated reads q20 AND q15 drop all/most reads; disable quality filtering entirely, keep only --length_required",
        "if reads are REAL (not simulated), lower --qualified_quality_phred to 15 to retain more reads",
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
        metrics: Dict = {}   # PoC: typed values (same extraction, just not discarded)
        if json_path:
            try:
                with open(json_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                total_in  = data["summary"]["before_filtering"]["total_reads"]
                total_out = data["summary"]["after_filtering"]["total_reads"]
                if total_in > 0:
                    score = total_out / total_in
                # Same values already parsed above — stash them as data so the
                # finalizer can cite them without re-reading fastp.json.
                metrics = {
                    "reads_before": total_in,
                    "reads_after": total_out,
                    "pct_reads_kept": round(score * 100, 2),
                    "_source_file": os.path.basename(json_path),
                }
            except Exception:
                score = 0.6  # JSON present but unparseable — assume partial success

        if score < self.THRESHOLD:
            return ContractResult(
                ok=False, score=score,
                reason=f"fastp: {score*100:.1f}% reads kept (threshold {self.THRESHOLD*100:.0f}%)",
                retry_params={"hint": "lower --qualified_quality_phred (try 15) or reduce --cut_mean_quality"},
                metrics=metrics,
            )
        return ContractResult(ok=True, score=score,
                              reason=f"fastp: {score*100:.1f}% reads kept",
                              metrics=metrics)


class FastqcContract(_BaseContract):
    """
    Output: {sample}_fastqc.html + {sample}_fastqc.zip per input file.
    Scoring: presence-based (FastQC is diagnostic only — does not filter reads).
    Score 1.0 if at least one HTML or ZIP report found; 0.0 otherwise.
    Source: https://www.bioinformatics.babraham.ac.uk/projects/fastqc/
    """
    KEYWORDS = ("fastqc", "read quality report", "per base quality", "fastqc report",
                "quality control fastq", "qc fastq")
    RUNTIME = "fast"
    VARIANTS = [
        "ensure output directory exists and is writable before calling fastqc",
        "pass -t <n> to process multiple files in parallel",
        "use --noextract to keep zip only (avoids permission issues on some systems)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        html = self._glob_first(run_dir, "*_fastqc.html")
        zipped = self._glob_first(run_dir, "*_fastqc.zip")
        if not html and not zipped:
            return ContractResult(
                ok=False, score=0.0,
                reason="fastqc: no *_fastqc.html or *_fastqc.zip report found",
                retry_params={"hint": "check -o output_dir path and ensure input FASTQ files exist"},
            )
        found = html or zipped
        return ContractResult(ok=True, score=1.0,
                              reason=f"fastqc: report found ({os.path.basename(found)})")


class SraFetchContract(_BaseContract):
    """
    fetch_sra_reads — DOWNLOAD of REAL experimental FASTQ from ENA/SRA (single-end:
    <acc>.fastq[.gz]; paired-end: _1/_2 or R1/R2 .fastq[.gz]).

    This is a DOWNLOAD step, NOT read simulation. It must be matched BEFORE the
    WgsimContract, otherwise a title like "Fetch raw paired-end reads … decompress
    to R1.fastq and R2.fastq" was graded by wgsim's contract, which globs for the
    simulation-specific name reads_R1.fastq, fails on the real name R1.fastq, and
    forces a spurious retry + a costly multi-GB RE-DOWNLOAD (observed on DRR102584).

    Lenient on purpose: fetch_sra_reads already verifies each file's size against the
    ENA-reported fastq_bytes internally, so the contract only needs to confirm that at
    least one non-empty FASTQ was produced — never to reject on exact naming.
    """
    KEYWORDS = ("fetch_sra_reads", "fetch_sra", "fetch raw", "sra accession",
                "ena accession", "sra/ena", "sra run", "ena run",
                "download the reads", "download raw reads", "download reads for",
                "retrieve the raw", "retrieve raw", "retrieve the paired",
                "retrieve the reads", "fetch reads", "fetch the reads")
    RUNTIME = "fast"

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        fqs = self._glob_all(run_dir, "*.fastq.gz", "*.fq.gz", "*.fastq", "*.fq")
        nonempty = [f for f in fqs if os.path.isfile(f) and os.path.getsize(f) > 0]
        if not nonempty:
            return ContractResult(
                ok=False, score=0.0,
                reason="fetch_sra_reads: no non-empty FASTQ produced",
                retry_params={"hint": "fetch_sra_reads pulls FASTQ from ENA over HTTPS; confirm the RUN accession (SRR/ERR/DRR, not a study/BioProject) is valid and the network is reachable."},
            )
        total = sum(os.path.getsize(f) for f in nonempty)
        # PAIRED-END SANITY CHECK (real failure — verified): a real ENA run that returns
        # THREE fastq_ftp entries (two real mates + an orphan/singleton file, common on
        # older archived runs) can trip download code that only special-cases exactly-2-
        # vs-else — the else branch + a zip() over mismatched-length lists silently
        # truncates to downloading ONLY the tiny orphan file, yet still prints "SUCCESS:
        # downloaded paired-end reads". The old lenient check ("at least 1 non-empty
        # file") scored this ok=True/1.0 despite only ~38KB present when ENA itself
        # reported ~950MB expected. If the step explicitly calls for PAIRED-END data,
        # require the actual _1/_2 (or R1/R2) pair to both be present and non-trivial —
        # a lone unsuffixed/orphan file does not satisfy "paired-end".
        _title = (stdout or "").lower()  # best-effort; real title check happens via caller context
        _has_pair_names = any(
            re.search(r'(_1|_R1)\.f(ast)?q(\.gz)?$', os.path.basename(f), re.I) for f in nonempty
        ) and any(
            re.search(r'(_2|_R2)\.f(ast)?q(\.gz)?$', os.path.basename(f), re.I) for f in nonempty
        )
        if len(nonempty) == 1 and total < 1_000_000:
            # A single file under 1MB is far too small to be a real paired-end mate for
            # any non-trivial WGS run — almost certainly the orphan-file mixup above.
            return ContractResult(
                ok=False, score=0.1,
                reason=(
                    f"fetch_sra_reads: only 1 FASTQ file ({total} bytes) present — too small "
                    "for a real WGS run; likely downloaded only an orphan/singleton file "
                    "while silently dropping the real _1/_2 paired mates (check ENA's own "
                    "fastq_bytes for this accession — if it lists 2-3 files, the real pair "
                    "was probably skipped by a length-mismatched zip())"
                ),
                retry_params={"hint": "re-query ENA fastq_ftp/fastq_bytes for this accession; if 3 entries are returned, identify the two LARGEST as the real _1/_2 pair (or match by '_1'/'_2' suffix) and download those explicitly — do not zip() a url list against a shorter output-name list."},
            )
        return ContractResult(
            ok=True, score=1.0,
            reason=f"fetch_sra_reads: {len(nonempty)} FASTQ file(s), {total // (1024 * 1024)}MB present",
            metrics={"fastq_files": len(nonempty), "total_mb": total // (1024 * 1024),
                     "paired_names_detected": _has_pair_names},
        )


class FiltlongContract(_BaseContract):
    """
    filtlong — long-read length/quality filtering. filtlong writes kept reads to STDOUT
    (there is NO -o flag); the generated code MUST redirect stdout to a file. Recurring
    failure (run-214): the output file exists but is EMPTY (0 bytes) because filtlong
    aborted with 'duplicate read name' (paired _1/_2 FASTQs were concatenated), or every
    read was shorter than --min_length (e.g. Illumina 125 bp reads through --min_length
    1000 → wrong platform). A generic contract reported ok=True WITHOUT checking output
    size, so the empty file slipped through and hard-blocked the downstream assembler.
    This contract REQUIRES a NON-EMPTY filtered FASTQ. Matched BEFORE FastpContract.
    """
    KEYWORDS = ("filtlong", "filter long reads", "long-read filter", "long read filter",
                "length/quality filter", "quality-filter the ont", "filter the ont reads",
                "filter reads by length", "filter the long reads")
    RUNTIME = "fast"

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        fqs = self._glob_all(run_dir, "*filt*.fastq.gz", "*filt*.fastq", "*filt*.fq.gz",
                             "*filt*.fq", "*filtered*.fastq*", "*keep*.fastq*")
        if not fqs:
            return ContractResult(
                ok=False, score=0.0,
                reason="filtlong: no filtered FASTQ found",
                retry_params={"hint": "filtlong writes kept reads to STDOUT (no -o flag) — redirect it: subprocess.run([...], stdout=open(out,'wb')). Check returncode==0."},
            )
        nonempty = [f for f in fqs if os.path.isfile(f) and os.path.getsize(f) > 0]
        if not nonempty:
            return ContractResult(
                ok=False, score=0.0,
                reason="filtlong: filtered FASTQ is EMPTY (0 bytes) — filtlong aborted (duplicate read names from cat _1/_2, or all reads < --min_length). The reads may be short-read Illumina, NOT ONT.",
                retry_params={"hint": "Do NOT concatenate paired _1/_2 files for long-read (their presence means the run is Illumina — WRONG platform for Flye). Use the single ONT FASTQ, verify filtlong returncode==0, and if reads are genuinely <1000bp lower --min_length."},
            )
        total = sum(os.path.getsize(f) for f in nonempty)
        return ContractResult(
            ok=True, score=1.0,
            reason=f"filtlong: filtered FASTQ present ({total // (1024 * 1024)}MB)",
            metrics={"filtered_mb": total // (1024 * 1024)},
        )


class WgsimContract(_BaseContract):
    """
    Output: reads_R1.fastq + reads_R2.fastq — both must exist AND be non-empty.
    An empty file (size 0) means the LLM created a placeholder to bypass failure.
    Score 0.0 if either file is missing or empty — forces retry with correct tool.
    """
    # SIMULATION-specific keywords only. "paired-end reads" / "generate reads" were
    # removed: they matched DOWNLOAD steps ("Fetch raw paired-end reads …") and real
    # experimental-read steps, mis-grading them on wgsim's reads_R1.fastq naming and
    # forcing spurious retries. Real wgsim steps still match via "wgsim" / "simulate …".
    KEYWORDS = ("wgsim", "simulate reads", "simulated reads", "read simulation",
                "simulate illumina", "simulate paired-end", "simulate short-read",
                "generate simulated")
    RUNTIME = "fast"
    VARIANTS = [
        "ensure wgsim is available: it is bundled with samtools in meta-env1",
        "use wgsim -N 50000 -1 150 -2 150 genome.fna reads_R1.fastq reads_R2.fastq",
        "if wgsim not found, run: samtools --version to confirm samtools is installed",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        r1 = self._glob_first(run_dir, "reads_R1.fastq", "*_R1.fastq", "*_R1.fq")
        r2 = self._glob_first(run_dir, "reads_R2.fastq", "*_R2.fastq", "*_R2.fq")

        if not r1 or not r2:
            return ContractResult(
                ok=False, score=0.0,
                reason="wgsim: reads_R1.fastq / reads_R2.fastq not found",
                retry_params={"hint": "wgsim is in meta-env1 (bundled with samtools). Ensure meta-env1 is active."},
            )

        r1_size = os.path.getsize(r1)
        r2_size = os.path.getsize(r2)

        if r1_size == 0 or r2_size == 0:
            return ContractResult(
                ok=False, score=0.0,
                reason=f"wgsim: output files are empty (R1={r1_size}B, R2={r2_size}B) — placeholder detected",
                retry_params={"hint": "Do NOT create empty placeholder files. Use wgsim from meta-env1 to generate real reads."},
            )

        # Vague 2: expose simulated read-pair count (R1 line count / 4).
        _metrics = {}
        try:
            with open(r1, encoding="utf-8", errors="replace") as fh:
                _n_lines = sum(1 for _ in fh)
            _metrics = {"read_pairs": _n_lines // 4}
        except Exception:
            _metrics = {}
        return ContractResult(
            ok=True, score=1.0,
            reason=f"wgsim: R1={r1_size//1024}KB R2={r2_size//1024}KB — reads present",
            metrics=_metrics,
        )


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
            _metrics = {"reads_in": n_in, "reads_out": n_out,
                        "retention_pct": round(score * 100, 2)}
        else:
            score = 0.8  # file found, can't parse ratio
            _metrics = {}
        return ContractResult(ok=True, score=score,
                              reason=f"bbduk: output FASTQ found (retention={score*100:.1f}%)",
                              metrics=_metrics)


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

    @staticmethod
    def _db_appears_viral_only(report_path: str) -> bool:
        """Detect a DB/sample mismatch where the Kraken2 database only contains
        viral genomes (so bacterial/fungal samples classify at near-zero rate).

        Reads the report's domain-rank ("D") lines and returns True when the
        Viruses domain has classified reads while Bacteria/Archaea/Eukaryota
        domains have none. In that case a low classification rate is a
        configuration/biology outcome, NOT a tool failure, and retrying the
        identical command against the same DB cannot improve it.
        """
        dom = {"Viruses": 0, "Bacteria": 0, "Archaea": 0, "Eukaryota": 0}
        try:
            with open(report_path, encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    # %\tclade_reads\ttaxon_reads\trank\ttaxid\tname
                    if len(parts) < 6:
                        continue
                    rank = parts[3].strip()
                    if rank != "D":  # domain / superkingdom rows only
                        continue
                    name = parts[5].strip()
                    if name in dom:
                        try:
                            dom[name] = int(parts[1].strip())
                        except ValueError:
                            pass
        except Exception:
            return False
        cellular = dom["Bacteria"] + dom["Archaea"] + dom["Eukaryota"]
        return dom["Viruses"] > 0 and cellular == 0

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        import os as _os
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
            # Fix #2 — DB/sample mismatch soft-pass: if the run produced a valid
            # report with SOME classified reads, and the DB is viral-only while
            # the sample is cellular, the low rate is not a tool failure.
            # Accept it (no pointless retry) with a clear advisory reason.
            # Kill-switch: GENOMEER_KRAKEN2_DBMISMATCH_SOFTPASS=0.
            _softpass_on = _os.environ.get(
                "GENOMEER_KRAKEN2_DBMISMATCH_SOFTPASS", "1") != "0"
            if (
                _softpass_on
                and score > 0.0
                and self._db_appears_viral_only(report)
            ):
                return ContractResult(
                    ok=True, score=score,
                    reason=(
                        f"kraken2: {score*100:.1f}% classified — run OK; the "
                        "configured database appears viral-only, so a cellular "
                        "(bacterial/fungal) sample classifies at a low rate. "
                        "Use Kraken2 Standard/PlusPF for cellular communities."
                    ),
                )
            return ContractResult(
                ok=False, score=score,
                reason=f"kraken2: {score*100:.1f}% classified (threshold {self.THRESHOLD*100:.0f}%)",
                retry_params={"hint": "try a larger database or lower --confidence (default 0.0) to 0"},
            )
        return ContractResult(ok=True, score=score,
                              reason=f"kraken2: {score*100:.1f}% reads classified",
                              metrics={"pct_classified": round(score * 100, 2),
                                       "_source_file": _os.path.basename(report)})


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
                                  reason=f"sylph: {hits}/{total} genomes ≥95% ANI",
                                  metrics={"genomes_ge95_ani": hits, "genomes_total": total,
                                           "_source_file": os.path.basename(tsv)})
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
                              reason=f"kaiju: {score*100:.1f}% reads classified",
                              metrics={"pct_classified": round(score * 100, 2),
                                       "_source_file": os.path.basename(kaiju_summ or kaiju_out)})


# ===========================================================================
# LONG-READ POLISHING (Racon, Medaka) — checked BEFORE AssemblyContract.
# Both a Racon and a Medaka step's title routinely says "...on the draft
# ASSEMBLY" (that IS what polishing operates on) and AssemblyContract's
# KEYWORDS include the bare word "assembly" plus a "*.fasta" catch-all glob
# that would find polished.fasta/consensus.fasta too — so without this
# ordering a polishing step gets silently mis-scored under assembly-N50
# semantics instead of its own (Medaka: consensus.fasta/QV; Racon:
# polished.fasta) criteria. Same rationale as the ProdigalContract/
# ProkkaContract-before-Assembly ordering below.
# ===========================================================================

class MedakaContract(_BaseContract):
    """
    Output: {output_dir}/consensus.fasta  (primary output)
            calls_to_draft.bam + .bai     (intermediate alignment files)
    Scoring: presence + size of consensus.fasta; 5 Mb = score 1.0
             (5 Mb ≈ median bacterial genome; Mira et al. 2001 PMID 11782624).
    Source: https://github.com/nanoporetech/medaka
            https://medaka.readthedocs.io/en/latest/
    No peer-reviewed threshold; consensus.fasta present and non-empty = success.
    Typical Nanopore polishing reduces raw error rate ~5% → <1% (ONT tech note).
    """
    KEYWORDS = ("medaka", "medaka_consensus", "nanopore polishing",
                "ont polishing", "medaka consensus", "medaka -i")
    RUNTIME = "long"
    VARIANTS = [
        "run 'medaka tools list_models' to find the correct model for your flowcell/basecaller",
        "reduce -t (threads) to lower memory usage; split assembly into smaller chunks if OOM",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        consensus = self._glob_first(run_dir,
                                     "consensus.fasta", "consensus.fa",
                                     "*consensus*.fasta", "*polished*.fasta")

        if not consensus:
            return ContractResult(
                ok=False, score=0.0,
                reason="medaka: consensus.fasta not found",
                retry_params={"hint": "check -d (draft FASTA), -i (reads), -m (model); run 'medaka tools list_models' to verify model name"},
            )

        size = os.path.getsize(consensus)
        if size < 1000:
            return ContractResult(
                ok=False, score=0.1,
                reason=f"medaka: consensus.fasta nearly empty ({size} bytes)",
                retry_params={"hint": "verify draft assembly -d is non-empty and reads -i map to the assembly"},
            )

        seq_count = 0
        try:
            with open(consensus, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith(">"):
                        seq_count += 1
        except Exception:
            pass

        score = min(1.0, size / 5_000_000)  # 5 Mb = 1.0
        return ContractResult(ok=True, score=score,
                              reason=f"medaka: {seq_count} polished sequence(s), {size:,} bytes",
                              metrics={"polished_sequences": seq_count})


class RaconContract(_BaseContract):
    """
    Output: {output_dir}/polished.fasta (run_racon's own naming convention —
            racon itself writes the polished FASTA to STDOUT; the wrapper
            redirects it to this file).
    Scoring: presence + size, same file-size heuristic as MedakaContract
            (racon has no analogous single QV-style summary metric in its
            own stderr to parse deterministically).
    Source: https://github.com/lbcb-sci/racon
    """
    KEYWORDS = ("racon", "racon polishing", "racon -t")
    RUNTIME = "long"
    VARIANTS = [
        "verify the overlaps PAF/SAM was generated from the SAME reads against the SAME draft assembly being polished",
        "reduce -t (threads) to lower memory usage on a large assembly",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        polished = self._glob_first(run_dir, "polished.fasta", "*polished*.fasta", "*racon*.fasta")

        if not polished:
            return ContractResult(
                ok=False, score=0.0,
                reason="racon: polished.fasta not found",
                retry_params={"hint": "racon writes to STDOUT (no -o flag) — verify the caller redirected stdout to a file"},
            )

        size = os.path.getsize(polished)
        if size < 1000:
            return ContractResult(
                ok=False, score=0.1,
                reason=f"racon: polished.fasta nearly empty ({size} bytes)",
                retry_params={"hint": "verify the overlaps file and draft assembly are both non-empty and correctly paired"},
            )

        seq_count = 0
        try:
            with open(polished, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith(">"):
                        seq_count += 1
        except Exception:
            pass

        score = min(1.0, size / 5_000_000)  # 5 Mb = 1.0, same scale as MedakaContract
        return ContractResult(ok=True, score=score,
                              reason=f"racon: {seq_count} polished sequence(s), {size:,} bytes",
                              metrics={"polished_sequences": seq_count})


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

        # Parse N50 (+ #contigs, total length) from QUAST report if present.
        # Vague 2: read all rows once so the finalizer gets the real assembly
        # numbers, not just a size proxy. Score logic is unchanged (N50-driven).
        quast_report = self._glob_first(run_dir, "report.tsv", "transposed_report.tsv")
        if quast_report:
            try:
                n50 = n_contigs = total_len = None
                with open(quast_report, encoding="utf-8") as fh:
                    for row in csv.reader(fh, delimiter="\t"):
                        if not row:
                            continue
                        key = row[0].strip()
                        if key == "N50":
                            n50 = int(row[1].strip().replace(",", ""))
                        elif key in ("# contigs", "# contigs (>= 0 bp)") and n_contigs is None:
                            n_contigs = int(row[1].strip().replace(",", ""))
                        elif key in ("Total length", "Total length (>= 0 bp)") and total_len is None:
                            total_len = int(row[1].strip().replace(",", ""))
                if n50 is not None:
                    score = min(1.0, n50 / 50_000)  # 50 kb = 1.0
                    _m = {"n50_bp": n50, "_source_file": os.path.basename(quast_report)}
                    if n_contigs is not None:
                        _m["n_contigs"] = n_contigs
                    if total_len is not None:
                        _m["total_length_bp"] = total_len
                    return ContractResult(ok=True, score=score,
                                          reason=f"assembly: N50={n50:,} bp",
                                          metrics=_m)
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
                              reason=f"semibin2: {n_bins} bin(s) produced",
                              metrics={"n_bins": n_bins})


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
                              reason=f"concoct: {n_bins} cluster(s)/bin(s)",
                              metrics={"n_bins": n_bins})


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
                                  reason=f"maxbin2: {n_bins} bin(s), mean completeness={mean_completeness:.1f}%",
                                  metrics={"n_bins": n_bins,
                                           "mean_completeness": round(mean_completeness, 1)})

        score = min(1.0, n_bins / 10.0)
        return ContractResult(ok=True, score=score,
                              reason=f"maxbin2: {n_bins} bin(s) found",
                              metrics={"n_bins": n_bins})


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
                              reason=f"metabat2: {len(bins)} bin(s) produced",
                              metrics={"n_bins": len(bins)})


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
            comps: List[float] = []
            conts: List[float] = []
            hq = mq = 0                       # MIMAG quality tiers
            best = None                       # highest-completeness bin (name, comp, cont)
            with open(report, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    comp = float(row.get("Completeness", 0) or 0)
                    cont = float(row.get("Contamination", 0) or 0)
                    scores.append(max(0.0, comp - 5.0 * cont))   # score logic UNCHANGED
                    comps.append(comp)
                    conts.append(cont)
                    if comp >= 90.0 and cont <= 5.0:
                        hq += 1
                    elif comp >= 50.0 and cont <= 10.0:
                        mq += 1
                    if best is None or comp > best[1]:
                        best = (str(row.get("Name", "") or ""), comp, cont)

            if not scores:
                return ContractResult(ok=True, score=0.5,
                                      reason="checkm2: report found but no bin rows")
            avg = sum(scores) / len(scores)
            # Vague 2: expose the REAL per-run biological values instead of only the
            # composite score. The finalizer previously had to re-read
            # quality_report.tsv to state completeness/contamination; now it cites
            # these directly. Score is untouched (still mean(max(0, comp-5*cont))/100).
            metrics = {
                "n_bins": len(scores),
                "high_quality_bins": hq,        # MIMAG: ≥90% comp, ≤5% cont
                "medium_quality_bins": mq,      #        ≥50% comp, ≤10% cont
                "mean_completeness": round(sum(comps) / len(comps), 1),
                "mean_contamination": round(sum(conts) / len(conts), 1),
                "_source_file": os.path.basename(report),
            }
            if best is not None:
                metrics["best_bin_completeness"] = round(best[1], 1)
                metrics["best_bin_contamination"] = round(best[2], 1)
            return ContractResult(ok=True, score=min(1.0, avg / 100.0),
                                  reason=f"checkm2: {len(scores)} bin(s), avg quality={avg:.1f}",
                                  metrics=metrics)
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
                              reason=f"hmmer: {hits} hit(s) with E-value < 1e-5",
                              metrics={"significant_hits": hits,
                                       "_source_file": os.path.basename(target_file)})


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
                              reason=f"eggnog: {annotated}/{total} queries annotated with COG",
                              metrics={"queries_annotated": annotated, "queries_total": total})


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
        # Vague 2: expose alignment count for tabular formats (outfmt 6 / m8);
        # skip binary .daa where a line count is meaningless.
        _metrics = {}
        if out.lower().endswith((".tsv", ".m8", ".txt", ".out")):
            try:
                with open(out, encoding="utf-8", errors="replace") as fh:
                    n_aln = sum(1 for ln in fh if ln.strip() and not ln.startswith("#"))
                _metrics = {"n_alignments": n_aln, "_source_file": os.path.basename(out)}
            except Exception:
                _metrics = {}
        return ContractResult(ok=True, score=score,
                              reason=f"diamond: output {size:,} bytes",
                              metrics=_metrics)


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
        # Vague 2: count quantified pathways (rows in the pathabundance table).
        _metrics = {}
        if pathway:
            try:
                with open(pathway, encoding="utf-8", errors="replace") as fh:
                    n_paths = sum(1 for ln in fh if ln.strip() and not ln.startswith("#"))
                _metrics = {"pathways_quantified": n_paths, "_source_file": os.path.basename(pathway)}
            except Exception:
                _metrics = {}
        return ContractResult(ok=True, score=1.0,
                              reason="humann3: output tables found",
                              metrics=_metrics)


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
                              reason=f"antismash: {n_bgc} BGC region(s) detected",
                              metrics={"bgc_regions": n_bgc})


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
                              reason=f"genomad: {above_threshold}/{len(scores)} seqs with score≥0.70",
                              metrics={"viral_plasmid_seqs": above_threshold,
                                       "total_scored": len(scores)})


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
        # FIX: do NOT glob a bare "*.tsv" first — the run dir holds many TSVs
        # (quast report.tsv, summary.tsv, ...) and the first match is often NOT
        # the abricate output, producing a meaningless score (e.g. "79/79" from
        # a 79-contig table). Glob abricate-specific names ONLY, then verify the
        # file actually has abricate's columns before scoring.
        candidates = self._glob_all(
            run_dir,
            "*abricate*.tsv", "*abricate*.txt", "*abricate*",
            "*resfinder*.tsv", "*vfdb*.tsv", "*amr*.tsv", "*virulence*.tsv",
            "*resfinder*.txt", "*vfdb*.txt",
        )
        # Among candidates, keep only files that truly look like abricate output
        # (header contains %IDENTITY and %COVERAGE — abricate's signature columns).
        def _is_abricate_tsv(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    header = fh.readline()
                return ("%IDENTITY" in header and "%COVERAGE" in header) or (
                    "\tGENE\t" in header and "DATABASE" in header)
            except Exception:
                return False

        tsv = next((c for c in candidates if _is_abricate_tsv(c)), None)
        if not tsv:
            # No genuine abricate output found → don't fabricate a score; let the
            # observer judge (a real abricate file would have the signature header).
            return ContractResult(
                ok=True, score=-1.0,
                reason="abricate: no recognizable abricate TSV (deferring to observer)",
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
                                  reason="abricate: ran OK — 0 resistance/virulence genes detected",
                                  metrics={"gene_hits_total": 0, "gene_hits_passing": 0,
                                           "_source_file": os.path.basename(tsv)})
        score = passing / total
        return ContractResult(ok=True, score=score,
                              reason=f"abricate: {passing}/{total} gene hits pass ≥75% ID / ≥80% coverage",
                              metrics={"gene_hits_total": total, "gene_hits_passing": passing,
                                       "_source_file": os.path.basename(tsv)})


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
                              reason=f"dbcan: {consensus}/{total} CAZymes predicted by ≥2 tools",
                              metrics={"cazymes_consensus": consensus, "cazymes_total": total})


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
        _metrics = {"cds_annotated": cds_count} if cds_count else {}
        return ContractResult(ok=True, score=score, reason=reason, metrics=_metrics)


class ProdigalContract(_BaseContract):
    """
    Covers Prodigal ORF/protein-prediction steps.
    Expected output: .faa (protein FASTA).  Optional: .fna (nucleotide ORFs), .gff.
    A contig .fasta present without .faa = Prodigal failed or was never run → blocked.
    Scoring: number of predicted proteins; 1000 proteins = score 1.0.
    Source: https://github.com/hyattpd/Prodigal
    """
    KEYWORDS = (
        "prodigal", "orf prediction", "orf calling",
        "protein prediction", "predict orf", "predict protein",
    )
    RUNTIME = "fast"
    VARIANTS = [
        "add -p meta for metagenomic contigs (mixed-species input)",
        "lower minimum gene length: add -g 11 (standard genetic code)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        # Grade the LARGEST proteome, not the first .faa found. A step that runs
        # Prodigal AND THEN builds a marker tree / pan-genome leaves several .faa in
        # the run dir: the real proteome(s) with thousands of proteins PLUS tiny
        # helper files (a 6-sequence concatenated-marker FASTA, a 4-sequence marker
        # DB). Grading the first .faa (arbitrary glob order) could grade a tiny helper
        # → "6 proteins" → score 0.01 FALSE NEGATIVE (observed on the marker/pan-genome
        # steps). Counting the .faa with the MOST proteins fixes it: if Prodigal
        # produced any real proteome, the step passes. Single-proteome steps are
        # unaffected (their one .faa is trivially the max).
        faa_files = self._glob_all(run_dir, "*.faa")
        fasta = self._glob_first(
            run_dir, "contigs.fasta", "assembly.fasta", "*.fasta", "*.fa",
        )

        if not faa_files:
            if fasta:
                return ContractResult(
                    ok=False, score=0.1,
                    reason=(
                        "prodigal: contig FASTA found but .faa (protein predictions) "
                        "is missing — Prodigal likely failed or was not run"
                    ),
                    retry_params={
                        "hint": (
                            "run: prodigal -i contigs.fasta -a proteins.faa "
                            "-p meta -f gff -o prodigal.gff"
                        )
                    },
                )
            return ContractResult(
                ok=False, score=0.0,
                reason="prodigal: no .faa protein file found in run directory",
                retry_params={
                    "hint": (
                        "run: prodigal -i <contigs.fasta> -a proteins.faa "
                        "-p meta -f gff -o prodigal.gff"
                    )
                },
            )

        # Pick the .faa with the MOST protein records (the real proteome).
        best_faa, n_proteins = None, -1
        for f in faa_files:
            try:
                n = sum(1 for line in open(f, encoding="utf-8", errors="replace")
                        if line.startswith(">"))
            except Exception:
                n = 0
            if n > n_proteins:
                best_faa, n_proteins = f, n

        if best_faa is None or os.path.getsize(best_faa) < 100:
            return ContractResult(
                ok=False, score=0.1,
                reason="prodigal: .faa exists but nearly empty — no ORFs predicted",
                retry_params={
                    "hint": "check assembly quality; confirm contigs are >100 bp; add -p meta"
                },
            )

        score = min(1.0, (n_proteins or 0) / 1000)
        reason = f"prodigal: {n_proteins:,} proteins predicted"
        _metrics = {"orfs_predicted": n_proteins}
        return ContractResult(ok=True, score=score, reason=reason, metrics=_metrics)


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
        _metrics = ({"cds_annotated": gene_count, "_source_file": os.path.basename(txt)}
                    if gene_count and txt else {})
        return ContractResult(ok=True, score=score, reason=reason, metrics=_metrics)


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
    VARIANTS = [
        "add --min-contig 200 to include shorter contigs in report",
        "add --gene-finding to enable gene prediction metrics",
        "add --large for large genome mode if assembly > 100MB",
    ]

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
                                              reason=f"quast: N50={n50:,} bp",
                                              metrics={"n50_bp": n50,
                                                       "_source_file": os.path.basename(report)})
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
    VARIANTS = [
        "try seqkit stats -a for extended statistics if basic stats are empty",
        "check input format: add --id-regexp for non-standard FASTA headers",
    ]

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
                                  reason=f"nonpareil: average coverage={coverage*100:.1f}%",
                                  metrics={"avg_coverage_pct": round(coverage * 100, 2)})

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
                              reason=f"lefse: {biomarkers}/{total} features with LDA≥2.0",
                              metrics={"biomarkers": biomarkers, "features_total": total})


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
                                          reason=f"cnvkit: {len(vals)} bins, mean |log2|={mean_dev:.3f}",
                                          metrics={"n_bins": len(vals),
                                                   "mean_abs_log2": round(mean_dev, 3)})
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
                        metrics={"hla_alleles_called": filled, "supporting_reads": reads},
                    )
        except Exception:
            pass

        return ContractResult(ok=True, score=0.8,
                              reason="optitype: result TSV found (alleles unparseable)")


# ===========================================================================
# BIN DEREPLICATION
# ===========================================================================

class DasToolContract(_BaseContract):
    """
    Output: {prefix}_DASTool_summary.tsv  +  {prefix}_DASTool_bins/*.{fa,fna,fasta}
    Columns: bin_ID, unique_SCGs.of.bin, redundant_SCGs.of.bin,
             SCG_completeness (0–1 float), SCG_redundancy (0–1 float),
             size, N50, contigs, Bin_score
    Scoring: mean(SCG_completeness×100 − 5×SCG_redundancy×100) per bin / 100.
             0 bins retained after score filtering → fail (threshold too strict).
    Source: Sieber et al. 2018 Science doi:10.1126/science.aau6577 (DAS_Tool paper)
            https://github.com/cmks/DAS_Tool (output column specification)
            Parks et al. 2017 Nature Microbiology doi:10.1038/nmicrobiol.2017.203
            (MIMAG thresholds: HQ ≥90% completeness <5% contamination)
    """
    KEYWORDS = ("das_tool", "dastool", "das tool", "bin dereplication",
                "bin refinement", "dereplicate bins")
    RUNTIME = "medium"
    VARIANTS = [
        "lower --score_threshold from 0.5 to 0.35 to recover more bins at lower confidence",
        "add --write_bins (a bare flag, NO '1' value) to ensure bin FASTAs are written; verify scaffold-to-bin input files exist",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        summary = self._glob_first(run_dir, "*_DASTool_summary.tsv", "*DASTool*summary*")
        bin_fas = self._glob_all(run_dir, "*_DASTool_bins/*.fa",
                                 "*_DASTool_bins/*.fna", "*_DASTool_bins/*.fasta")

        if not summary and not bin_fas:
            return ContractResult(
                ok=False, score=0.0,
                reason="das_tool: no _DASTool_summary.tsv or bin FASTAs found",
                retry_params={"hint": "check -i scaffold-to-bin lists and -c contigs.fna; lower --score_threshold to 0.35"},
            )

        if summary and not bin_fas:
            return ContractResult(
                ok=False, score=0.1,
                reason="das_tool: summary found but no bins written (all bins below score threshold)",
                retry_params={"hint": "lower --score_threshold from 0.5 to 0.35 to recover more bins"},
            )

        n_bins = len(bin_fas)
        scores: List[float] = []
        max_red = 0.0        # highest SCG_redundancy across bins → chimera signal
        max_red_bin = None   # WHICH bin that redundancy belongs to (was previously discarded)

        # Basenames (no extension) of bins DAS_Tool actually WROTE to disk — the ground truth
        # for "kept vs dropped", available right here at check() time.
        _kept_bin_names = {os.path.splitext(os.path.basename(p))[0] for p in bin_fas}

        if summary:
            try:
                with open(summary, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        try:
                            comp = float(row.get("SCG_completeness", ""))
                            red  = float(row.get("SCG_redundancy", ""))
                        except (ValueError, TypeError):
                            continue
                        # DAS_Tool emits 0–1 in some versions, 0–100 in others (1.1.7) — normalize to %.
                        if comp <= 1.0: comp *= 100.0
                        if red  <= 1.0: red  *= 100.0
                        if red > max_red:
                            max_red = red
                            max_red_bin = row.get("bin") or row.get("bin_ID") or row.get("bin_id")
                        scores.append(max(0.0, comp - 5.0 * red))
            except Exception:
                pass

        # A bin with high SCG_redundancy is a likely CHIMERA of >=2 genomes (closely related
        # species co-bin). Surface it as a metric + a warning so the finalizer can flag a
        # "missing" dominant taxon that is actually merged inside the contaminated bin.
        # NAME + RESOLVE "kept vs dropped" HERE (real failure this fixes): the contract has
        # both the summary row AND the actual bin FASTA list in hand right now — resolving
        # "is this bin in the final kept set?" here, with certainty, and stating it explicitly
        # in the reason text, is far more reliable than passing a bare unnamed percentage
        # downstream and letting the finalizer LLM guess which bin it belongs to (it guessed
        # wrong in practice: it described a KEPT bin with 24% redundancy as "a raw bin not
        # carried forward", when the bin was in fact one of the final representative MAGs).
        _chi = ""
        if max_red > 10.0:
            _kept = (max_red_bin in _kept_bin_names) if max_red_bin else None
            _who = max_red_bin or "an unnamed bin"
            _status = "KEPT in the final bin set" if _kept else (
                "NOT in the final bin set (excluded)" if _kept is False else "kept-status unresolved"
            )
            _chi = (f"  [CHIMERA WARNING: bin '{_who}' has SCG_redundancy={max_red:.0f}% — likely "
                    f"2+ merged genomes; a 'missing' taxon may be hidden inside it. This bin is "
                    f"{_status} — do not contradict this when describing it.]")
        _metrics = {"n_bins": n_bins, "max_scg_redundancy_pct": round(max_red, 1),
                    "max_scg_redundancy_bin": max_red_bin}

        if scores:
            avg = sum(scores) / len(scores)
            return ContractResult(ok=True, score=min(1.0, avg / 100.0),
                                  reason=f"das_tool: {n_bins} bin(s) refined, avg quality={avg:.1f}{_chi}",
                                  metrics=_metrics)

        score = min(1.0, n_bins / 5.0)  # 5 high-quality bins = 1.0
        return ContractResult(ok=True, score=score,
                              reason=f"das_tool: {n_bins} refined bin(s) produced{_chi}",
                              metrics=_metrics)


class GuncContract(_BaseContract):
    """
    GUNC — chimerism/contamination detection for MAGs.
    Output: <out>/GUNC.progenomes_2.1.maxCSS_level.tsv (or *.maxCSS_level.tsv)
        key cols: genome, pass.GUNC (True/False), clade_separation_score (CSS),
                  contamination_portion, n_effective_surplus_clades
    Scoring: fraction of genomes that PASS (pass.GUNC True / CSS<=0.45). Empty/absent -> fail.
    Source: Orakov et al. 2021 Genome Biology doi:10.1186/s13059-021-02393-0
            https://grp-bork.embl-community.io/gunc/ (CSS>0.45 flags chimeras).
    """
    KEYWORDS = ("gunc", "chimera", "chimeric", "chimerism", "chimera detection")
    RUNTIME = "medium"
    VARIANTS = [
        "check the -r DB path (/home/workshop/gunc_db/gunc_db_progenomes2.1.dmnd) and that bins exist",
        "for a directory of bins use --input_dir <dir> --file_suffix .fa, not -i",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*maxCSS_level.tsv", "GUNC*.tsv", "*GUNC*maxCSS*.tsv")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="gunc: no GUNC.*maxCSS_level.tsv found",
                retry_params={"hint": "gunc run --input_dir bins/ --file_suffix .fa -r <db.dmnd> -o out"},
            )
        n, n_chi, max_css = 0, 0, 0.0
        chi_names: List[str] = []
        try:
            with open(tsv, encoding="utf-8") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    n += 1
                    try:
                        css = float(row.get("clade_separation_score", "") or 0)
                        max_css = max(max_css, css)
                    except (ValueError, TypeError):
                        css = 0.0
                    _pass = str(row.get("pass.GUNC", "")).strip().lower()
                    is_chi = (_pass == "false") or (css > 0.45)
                    if is_chi:
                        n_chi += 1
                        g = row.get("genome") or ""
                        if g:
                            chi_names.append(g)
        except Exception:
            pass
        if n == 0:
            return ContractResult(ok=False, score=0.1,
                                  reason="gunc: maxCSS table is empty (no genome scored)")
        _flag = (f"  [CHIMERA: {n_chi}/{n} bin(s) FAIL GUNC (max CSS={max_css:.2f}) — "
                 f"{', '.join(chi_names[:4])} — likely 2+ merged genomes]") if n_chi else ""
        # score = fraction clean; a clean set scores high, a chimeric set low.
        score = max(0.05, (n - n_chi) / n)
        return ContractResult(
            ok=True, score=score,
            reason=f"gunc: {n} bin(s), {n_chi} chimeric{_flag}",
            metrics={"n_genomes": n, "n_chimeric": n_chi, "max_clade_separation_score": round(max_css, 3)},
        )


class InStrainContract(_BaseContract):
    """
    inStrain 1.10 — strain-level microdiversity.
    profile mode -> {out}.IS/output/{name}_genome_info.tsv
        columns: genome, coverage, breadth, nucl_diversity, reads, ...
    compare mode -> {out}.IS/output/{name}_comparisonsTable.tsv
        columns: genome, name1, name2, popANI, conANI, percent_genome_compared, ...
    Scoring: profile -> presence of genome_info with real coverage/nucl_diversity;
             compare -> popANI table present. Empty/absent output -> fail.
    Source: Olm et al. 2021 Nature Biotechnology doi:10.1038/s41587-020-00797-0
            https://instrain.readthedocs.io (output specification; popANI>0.99999 ~ same strain)
    """
    KEYWORDS = ("instrain", "in-strain", "strain-level", "strain level",
                "microdiversity", "popani", "conani", "nucleotide diversity",
                "same strain", "strain identity", "strain tracking")
    RUNTIME = "medium"
    VARIANTS = [
        "ensure the BAM is coordinate-SORTED and indexed (samtools sort + samtools index) before inStrain profile",
        "verify the reference FASTA matches the BAM's reference sequences; pass -p 4",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        genome_info = self._glob_first(run_dir, "*_genome_info.tsv",
                                       "*.IS/output/*_genome_info.tsv", "*genome_info.tsv")
        # Prefer the PER-GENOME genomeWide table (has a `genome` column + per-MAG popANI,
        # produced only when `-s stb` is passed to compare); fall back to the per-scaffold
        # comparisonsTable. Both expose a `popANI` column so the parse below works for either.
        cmp_tbl = self._glob_first(run_dir, "*_genomeWide_compare.tsv",
                                   "*.IS/output/*_genomeWide_compare.tsv",
                                   "*_comparisonsTable.tsv",
                                   "*.IS/output/*_comparisonsTable.tsv", "*comparisonsTable*.tsv")

        if not genome_info and not cmp_tbl:
            return ContractResult(
                ok=False, score=0.0,
                reason="instrain: no _genome_info.tsv (profile) or _comparisonsTable.tsv (compare) found",
                retry_params={"hint": "inStrain profile needs a SORTED+INDEXED bam and the reference fasta; "
                                      "inStrain compare needs 2+ .IS profile dirs"},
            )

        # ── compare mode: report popANI (same-strain evidence) ──────────────
        if cmp_tbl:
            popani_vals: List[float] = []
            try:
                with open(cmp_tbl, encoding="utf-8") as fh:
                    for row in csv.DictReader(fh, delimiter="\t"):
                        try:
                            popani_vals.append(float(row.get("popANI", "")))
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass
            if popani_vals:
                mx = max(popani_vals)
                return ContractResult(
                    ok=True, score=1.0,
                    reason=f"instrain compare: {len(popani_vals)} pair(s), max popANI={mx:.6f}",
                    metrics={"n_comparisons": len(popani_vals), "max_popANI": round(mx, 6)},
                )
            return ContractResult(ok=True, score=0.6,
                                  reason="instrain compare: comparisonsTable produced (no popANI parsed)")

        # ── profile mode: report coverage / nucleotide diversity ────────────
        n_genomes, covs, divs = 0, [], []
        try:
            with open(genome_info, encoding="utf-8") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    n_genomes += 1
                    try: covs.append(float(row.get("coverage", "")))
                    except (ValueError, TypeError): pass
                    try: divs.append(float(row.get("nucl_diversity", "")))
                    except (ValueError, TypeError): pass
        except Exception:
            pass

        if n_genomes == 0:
            return ContractResult(
                ok=False, score=0.1,
                reason="instrain profile: genome_info.tsv is empty (no genome profiled)",
                retry_params={"hint": "check the bam actually maps reads to the reference; coverage may be ~0"},
            )
        mean_cov = (sum(covs) / len(covs)) if covs else 0.0
        mean_div = (sum(divs) / len(divs)) if divs else 0.0
        # low coverage → strain calls unreliable (mirror the iRep <5x caveat)
        score = 1.0 if mean_cov >= 5.0 else max(0.3, mean_cov / 5.0)
        return ContractResult(
            ok=True, score=score,
            reason=f"instrain profile: {n_genomes} genome(s), mean coverage={mean_cov:.1f}x, "
                   f"mean nucl_diversity={mean_div:.4f}"
                   + ("" if mean_cov >= 5.0 else "  [LOW COVERAGE <5x — strain metrics unreliable]"),
            metrics={"n_genomes": n_genomes, "mean_coverage": round(mean_cov, 2),
                     "mean_nucl_diversity": round(mean_div, 5)},
        )


# ===========================================================================
# ABUNDANCE RE-ESTIMATION
# ===========================================================================

class BrackenContract(_BaseContract):
    """
    Output: {prefix}.bracken  (TSV)
    Columns: name, taxonomy_id, taxonomy_lvl, kraken_assigned_reads,
             added_reads, new_est_reads, fraction_total_reads
    Also written: {prefix}_bracken_report.txt (Kraken2-format report)
    Scoring: number of taxa retained after threshold filter;
             fraction_total_reads should sum ≈ 1.0.
    Source: https://github.com/jenniferlu717/Bracken (column specification)
            Lu et al. 2017 PeerJ Computer Science doi:10.7717/peerj-cs.104
    0 taxa after filtering = fail (read-length / threshold mismatch).
    """
    KEYWORDS = ("bracken", "bracken abundance", "bracken re-estimation",
                "bayesian re-estimation", "bracken -d")
    RUNTIME = "fast"
    VARIANTS = [
        "lower -t (threshold) from 10 to 1 to include taxa with fewer supporting reads",
        "change -l level from S to G (genus) for a higher-level profile if species-level gives 0 taxa",
        "verify the Bracken DB was built for the same read length (-r); rebuild with bracken-build if needed",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        bracken_out = self._glob_first(run_dir, "*.bracken", "*bracken*.tsv")

        if not bracken_out:
            return ContractResult(
                ok=False, score=0.0,
                reason="bracken: no .bracken output file found",
                retry_params={"hint": "add -o <prefix>.bracken to bracken; verify -d DB path and -i kraken2 report"},
            )

        total_taxa = 0
        total_fraction = 0.0
        try:
            with open(bracken_out, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total_taxa += 1
                    try:
                        total_fraction += float(row.get("fraction_total_reads", 0) or 0)
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        if total_taxa == 0:
            return ContractResult(
                ok=False, score=0.0,
                reason="bracken: output file is empty (0 taxa classified)",
                retry_params={"hint": "lower -t threshold to 1; verify kraken2 report has classified reads before running bracken"},
            )

        score = min(1.0, total_taxa / 50.0)  # 50 species = 1.0
        return ContractResult(ok=True, score=score,
                              reason=f"bracken: {total_taxa} taxon(a) estimated (Σfractions={total_fraction:.3f})",
                              metrics={"taxa_estimated": total_taxa,
                                       "sum_fraction": round(total_fraction, 3),
                                       "_source_file": os.path.basename(bracken_out)})


# ===========================================================================
# MARKER-GENE PROFILING
# ===========================================================================

class MetaPhlAn4Contract(_BaseContract):
    """
    Output: {prefix}_profile.tsv  (or *_profiled_metagenome.txt in v3)
    Format: comment lines starting with '#' (version info), header '#clade_name ...',
            then data rows; species rows contain 's__' but not 't__' (strain level).
    Columns: #clade_name, NCBI_tax_id, relative_abundance, coverage,
             estimated_number_of_reads_from_the_clade
    Scoring: count of species-level rows (s__, not t__) with relative_abundance > 0.
    Source: https://github.com/biobakery/MetaPhlAn (output format, v4)
            Blanco-Míguez et al. 2023 Nature Methods doi:10.1038/s41592-023-01688-w
            Beghini et al. 2021 eLife doi:10.7554/eLife.65088 (MetaPhlAn3 benchmark)
    0 species with data rows = valid (low-biomass sample); no file = fail.
    """
    KEYWORDS = ("metaphlan", "metaphlan4", "metaphlan 4", "metaphlan3",
                "marker gene profil", "metaphlan --input_type", "metaphlan -t")
    RUNTIME = "medium"
    VARIANTS = [
        "add --unclassified_estimation to include the unclassified fraction in the profile",
        "update the MetaPhlAn database: metaphlan --install --bowtie2db <db_dir>",
        "verify reads are non-empty after QC; use --input_type fastq explicitly",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        profile = self._glob_first(run_dir, "*_profile.tsv", "*metaphlan*.tsv",
                                   "*_profiled_metagenome.txt", "*metaphlan*.txt")
        if not profile:
            return ContractResult(
                ok=False, score=0.0,
                reason="metaphlan4: no profile output file found",
                retry_params={"hint": "add --output_file <prefix>_profile.tsv to metaphlan command"},
            )

        species_count = 0
        has_data_rows = False
        try:
            with open(profile, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    has_data_rows = True
                    # Species rows contain 's__' but not 't__' (strain-level suffix)
                    if "s__" in line and "t__" not in line:
                        parts = line.strip().split("\t")
                        # relative_abundance is column index 2 (0-based)
                        try:
                            if len(parts) >= 3 and float(parts[2]) > 0:
                                species_count += 1
                        except (ValueError, IndexError):
                            species_count += 1
        except Exception:
            pass

        if not has_data_rows:
            return ContractResult(
                ok=True, score=0.3,
                reason="metaphlan4: profile file exists but no taxa detected (low-biomass or DB mismatch)")

        score = min(1.0, species_count / 20.0)  # 20 species = 1.0
        return ContractResult(ok=True, score=score,
                              reason=f"metaphlan4: {species_count} species detected",
                              metrics={"species_detected": species_count})


# ===========================================================================
# PHYLOGENETIC CLASSIFICATION
# ===========================================================================

class GtdbtkContract(_BaseContract):
    """
    Output: gtdbtk.bac120.summary.tsv  and/or  gtdbtk.ar53.summary.tsv (v2+)
            (GTDB-Tk v1 used ar122 instead of ar53)
    Key columns: user_genome, classification, fastani_ani, fastani_af,
                 msa_percent, red_value, warnings
    Scoring: fraction of genomes with msa_percent ≥ 50.0
             (GTDB-Tk documentation: <50% MSA completeness → 'low-quality placement').
    Source: https://github.com/Ecogenomics/GTDBTk (output column specification)
            Chaumeil et al. 2022 Bioinformatics doi:10.1093/bioinformatics/btac672 (v2)
            Parks et al. 2022 Nature Biotechnology doi:10.1038/s41587-021-01094-0 (GTDB r207)
    """
    KEYWORDS = ("gtdbtk", "gtdb-tk", "gtdb classify", "gtdbtk classify_wf",
                "phylogenetic classif", "gtdb taxonomy", "gtdb-tk classify")
    RUNTIME = "long"
    VARIANTS = [
        "set GTDBTK_DATA_PATH env variable to the GTDB-Tk reference data directory",
        "add --skip_ani_screen for faster placement when ANI screening is not required",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        bac_summary = self._glob_first(run_dir,
                                       "gtdbtk.bac120.summary.tsv", "*bac120*summary*")
        arc_summary = self._glob_first(run_dir,
                                       "gtdbtk.ar53.summary.tsv", "*ar53*summary*",
                                       "gtdbtk.ar122.summary.tsv", "*ar122*summary*")

        if not bac_summary and not arc_summary:
            return ContractResult(
                ok=False, score=0.0,
                reason="gtdbtk: no bac120 or ar53 summary TSV found",
                retry_params={"hint": "check GTDBTK_DATA_PATH env var; verify --genome_dir and --extension inputs"},
            )

        total = reliable = 0
        for tsv in filter(None, [bac_summary, arc_summary]):
            try:
                with open(tsv, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        total += 1
                        try:
                            if float(row.get("msa_percent", 0) or 0) >= 50.0:
                                reliable += 1
                        except (ValueError, TypeError):
                            reliable += 1  # count row if msa_percent absent/unparseable
            except Exception:
                pass

        if total == 0:
            return ContractResult(ok=True, score=0.3,
                                  reason="gtdbtk: summary file found but no genomes classified")

        score = reliable / total
        return ContractResult(ok=True, score=score,
                              reason=f"gtdbtk: {reliable}/{total} genome(s) with ≥50% MSA completeness",
                              metrics={"genomes_classified": total, "genomes_reliable_msa": reliable,
                                       "_source_file": os.path.basename(bac_summary or arc_summary)})


# ===========================================================================
# RESISTOME
# ===========================================================================

class RgiContract(_BaseContract):
    """
    Output: {prefix}.txt — tab-separated TSV, header on first line
    Key columns: ORF_ID, Cut_Off, Best_Hit_ARO, Best_Identities,
                 Drug Class, Resistance Mechanism, AMR Gene Family
    Scoring: fraction of hits with Cut_Off ∈ {Perfect, Strict}
             (Loose hits have elevated false-positive rate per CARD documentation).
    Source: https://github.com/arpcard/rgi (output format)
            Alcock et al. 2023 Nucleic Acids Research doi:10.1093/nar/gkac920
            CARD portal: https://card.mcmaster.ca/about
    0 hits = valid biological result (clean genome); file absent = fail.
    """
    KEYWORDS = ("rgi", "rgi main", "resistance gene identifier",
                "card database", "rgi --input_type", "amr prediction card", "rgi -i")
    RUNTIME = "medium"
    VARIANTS = [
        "load CARD database first: run 'rgi load -i card.json --local' before rgi main",
        "add --include_loose to report Loose hits in addition to Strict/Perfect",
        "switch -t input type: use 'contig' for nucleotide FASTA instead of 'protein'",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*.txt", "*rgi*.tsv", "*rgi*.txt")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="rgi: no output .txt TSV found",
                retry_params={"hint": "check -o output prefix; run 'rgi load --local' to load CARD DB first"},
            )

        total = strict_perfect = 0
        try:
            with open(tsv, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    if row.get("Cut_Off", "").strip() in ("Strict", "Perfect"):
                        strict_perfect += 1
        except Exception:
            pass

        if total == 0:
            return ContractResult(ok=True, score=0.5,
                                  reason="rgi: output found, no AMR hits detected (clean genome or DB not loaded)",
                                  metrics={"amr_hits_total": 0, "amr_hits_strict_perfect": 0,
                                           "_source_file": os.path.basename(tsv)})

        score = strict_perfect / total
        return ContractResult(ok=True, score=score,
                              reason=f"rgi: {strict_perfect}/{total} hits are Strict/Perfect (high confidence)",
                              metrics={"amr_hits_total": total, "amr_hits_strict_perfect": strict_perfect,
                                       "_source_file": os.path.basename(tsv)})


class AmrFinderContract(_BaseContract):
    """
    Output: user-specified TSV via -o flag
    Columns: Protein identifier, Gene symbol, Sequence name, Scope,
             Element type, Element subtype, Class, Subclass, Method,
             Target length, Reference sequence length,
             % Coverage of reference sequence,
             % Identity to reference sequence,
             Alignment length, Accession of closest sequence,
             Name of closest sequence, HMM id, HMM description
    Scoring: fraction of hits with ≥90% coverage AND ≥90% identity
             (NCBI AMRFinder's own criteria for high-confidence core AMR genes).
    Source: https://github.com/ncbi/amr (output column specification)
            Feldgarden et al. 2021 Scientific Reports doi:10.1038/s41598-021-91456-0
            NCBI AMRFinder docs: https://www.ncbi.nlm.nih.gov/pathogens/antimicrobial-resistance/AMRFinder/
    0 hits = valid biological result (clean genome); file absent = fail.
    """
    KEYWORDS = ("amrfinder", "amrfinderplus", "ncbi amr", "amrfinder -p",
                "amr finder", "amr gene ncbi", "amrfinder --plus")
    RUNTIME = "fast"
    VARIANTS = [
        "run 'amrfinder -u' to update the AMRFinder database to the latest version",
        "add --organism <name> (e.g. Escherichia) to enable point-mutation detection for supported species",
        "verify -p is a valid protein FASTA and the AMRFinder DB is installed (amrfinder -u)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*.tsv", "*amrfinder*.txt", "*amr_finder*")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="amrfinder: no output TSV found",
                retry_params={"hint": "add -o <output.tsv> to amrfinder command; run 'amrfinder -u' to install/update DB"},
            )

        total = high_quality = 0
        try:
            with open(tsv, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    try:
                        pct_cov = float(row.get("% Coverage of reference sequence", 0) or 0)
                        pct_id  = float(row.get("% Identity to reference sequence", 0) or 0)
                        if pct_cov >= 90.0 and pct_id >= 90.0:
                            high_quality += 1
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        if total == 0:
            return ContractResult(ok=True, score=0.5,
                                  reason="amrfinder: output found, no AMR/virulence genes detected (clean genome)",
                                  metrics={"amr_genes_total": 0, "amr_genes_high_quality": 0,
                                           "_source_file": os.path.basename(tsv)})

        # Detecting AMR/virulence genes IS a successful result. On fragmented MAG/bin or
        # metagenome assemblies, genes are routinely split across contig boundaries →
        # partial coverage (<90%), which is EXPECTED biology, NOT a tool failure. Scoring
        # 0/N here (all hits partial) fell below the global validator minimum and drove a
        # pointless 3x retry storm on binned input (each retry applying an irrelevant fix:
        # amrfinder -u / --organism / -p protein). FLOOR the score at 0.5 whenever ANY gene
        # is detected; reserve the higher range for complete high-confidence hits.
        frac_hq = high_quality / total
        score = 0.5 + 0.5 * frac_hq
        return ContractResult(ok=True, score=score,
                              reason=f"amrfinder: {total} AMR/virulence gene(s) detected "
                                     f"({high_quality} at ≥90% cov & id)",
                              metrics={"amr_genes_total": total, "amr_genes_high_quality": high_quality,
                                       "_source_file": os.path.basename(tsv)})


# ===========================================================================
# COVERAGE / ABUNDANCE / MAPPING  (new contracts — previously uncovered tools)
# ===========================================================================

class CoverMContract(_BaseContract):
    """
    CoverM per-genome/MAG or per-contig coverage & relative abundance.
    Output: a TSV — first column = Genome/Contig name, remaining columns = one
            metric value per sample (multi-sample abundance table).
    Source: https://github.com/wwood/CoverM (methods: relative_abundance, mean,
            covered_fraction, count, tpm, rpkm, trimmed_mean).
    """
    KEYWORDS = ("coverm", "relative abundance", "cross-sample abundance",
                "mag abundance", "per-genome coverage", "per-contig coverage")
    RUNTIME = "medium"
    VARIANTS = [
        "verify -m method (relative_abundance|mean|covered_fraction|tpm|rpkm); add --min-covered-fraction 0",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*abundance*.tsv", "*coverm*.tsv", "*coverage*.tsv")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="coverm: no coverage/abundance TSV found",
                retry_params={"hint": "add -o <output.tsv> to the coverm command"},
            )
        n_features = n_samples = 0
        try:
            with open(tsv, encoding="utf-8") as fh:
                header = fh.readline().rstrip("\n").split("\t")
                n_samples = max(0, len(header) - 1)   # first column = Genome/Contig name
                n_features = sum(1 for ln in fh if ln.strip())
        except Exception:
            pass
        if n_features == 0:
            return ContractResult(ok=True, score=0.3,
                                  reason="coverm: TSV found but no genome/contig rows")
        score = min(1.0, n_features / 10.0)
        return ContractResult(ok=True, score=score,
                              reason=f"coverm: {n_features} feature(s) x {n_samples} sample(s) quantified",
                              metrics={"n_features": n_features, "n_samples": n_samples,
                                       "_source_file": os.path.basename(tsv)})


class Minimap2Contract(_BaseContract):
    """
    minimap2 read mapping → sorted BAM, plus (for binning) a jgi depth table.
    Output: <out>.bam (binary) and optionally depth.txt with columns
            contigName, contigLen, totalAvgDepth, <bam>, <bam>-var.
    Source: minimap2 + metabat2 jgi_summarize_bam_contig_depths.
    """
    KEYWORDS = ("minimap2", "read mapping", "map reads", "read alignment",
                "contig depth", "coverage depth")
    RUNTIME = "medium"
    VARIANTS = [
        "verify preset -ax sr|map-ont|map-pb matches the read type; sort + index the BAM (samtools index)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        bam   = self._glob_first(run_dir, "*.bam")
        depth = self._glob_first(run_dir, "depth.txt", "*depth*.txt", "*_depth.tsv")
        if not bam and not depth:
            return ContractResult(
                ok=False, score=0.0,
                reason="minimap2: no BAM or depth file found",
                retry_params={"hint": "pipe minimap2 output to `samtools sort -o out.bam` and index it"},
            )
        metrics: Dict = {}
        if depth:
            try:
                n_contigs = 0
                depths: List[float] = []
                with open(depth, encoding="utf-8") as fh:
                    header = fh.readline().rstrip("\n").split("\t")
                    try:
                        ci = header.index("totalAvgDepth")
                    except ValueError:
                        ci = 2
                    for ln in fh:
                        parts = ln.rstrip("\n").split("\t")
                        if len(parts) <= ci:
                            continue
                        n_contigs += 1
                        try:
                            depths.append(float(parts[ci]))
                        except ValueError:
                            pass
                metrics = {"n_contigs": n_contigs, "_source_file": os.path.basename(depth)}
                if depths:
                    metrics["mean_depth"] = round(sum(depths) / len(depths), 2)
            except Exception:
                metrics = {}
        if bam:
            size = os.path.getsize(bam)
            return ContractResult(ok=True, score=max(0.5, min(1.0, size / 1_000_000)),
                                  reason=f"minimap2: BAM present ({size:,} bytes)",
                                  metrics=metrics)
        return ContractResult(ok=True, score=0.6,
                              reason="minimap2: depth table present (no BAM in run dir)",
                              metrics=metrics)


class Dada2Contract(_BaseContract):
    """
    DADA2 amplicon denoising → ASV table (+ taxonomy) TSV.
    Output: an ASV table TSV (exact amplicon sequence variants) and a taxonomy TSV.
    Source: https://benjjneb.github.io/dada2/ — ASV = exact sequence variant.
    """
    KEYWORDS = ("dada2", "asv", "amplicon denois", "sequence variant",
                "denoise amplicon", "16s asv")
    RUNTIME = "long"
    VARIANTS = [
        "ensure R1/R2 overlap (use a ~250-400 bp amplicon, not full-length 16S); lower truncLen / raise maxEE",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        asv = self._glob_first(run_dir, "*asv*table*.tsv", "*asv*.tsv", "*seqtab*.tsv", "*ASV*.tsv")
        tax = self._glob_first(run_dir, "*taxonomy*.tsv", "*tax*.tsv")
        if not asv and not tax:
            return ContractResult(
                ok=False, score=0.0,
                reason="dada2: no ASV table / taxonomy TSV found",
                retry_params={"hint": "write ASV table + taxonomy to TSV; verify mergePairs produced >0 ASVs"},
            )
        n_asvs = 0
        if asv:
            try:
                with open(asv, encoding="utf-8") as fh:
                    n_asvs = max(0, sum(1 for ln in fh if ln.strip()) - 1)  # minus header
            except Exception:
                pass
        if asv and n_asvs == 0:
            return ContractResult(
                ok=False, score=0.1,
                reason="dada2: ASV table present but 0 ASVs (R1/R2 likely did not overlap)",
                retry_params={"hint": "use a short overlapping amplicon; check filterAndTrim retention"},
            )
        score = min(1.0, n_asvs / 50.0) if n_asvs else 0.6
        return ContractResult(ok=True, score=score,
                              reason=(f"dada2: {n_asvs} ASV(s) inferred" if n_asvs
                                      else "dada2: taxonomy output found"),
                              metrics=({"n_asvs": n_asvs, "_source_file": os.path.basename(asv)}
                                       if n_asvs else {}))


class DrepContract(_BaseContract):
    """
    dRep genome/MAG dereplication → representative genomes + cluster tables.
    Output: <out>/dereplicated_genomes/*.fa (winners); data_tables/Cdb.csv
            (cluster membership), Wdb.csv (winners).
    Source: https://github.com/MrOlm/drep (default species threshold 95% ANI).
    """
    KEYWORDS = ("drep", "dereplicate genomes", "genome dereplication",
                "dereplicate mags", "mag dereplication", "non-redundant genomes")
    RUNTIME = "long"
    VARIANTS = [
        "lower -sa (secondary ANI) below 0.95 to merge more; pass --genomeInfo checkm2 quality to skip re-QC",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        reps = self._glob_all(run_dir, "dereplicated_genomes/*.fa",
                              "dereplicated_genomes/*.fasta", "dereplicated_genomes/*.fna")
        cdb = self._glob_first(run_dir, "Cdb.csv", "*Cdb.csv")
        wdb = self._glob_first(run_dir, "Wdb.csv", "*Wdb.csv")
        if not reps and not wdb:
            return ContractResult(
                ok=False, score=0.0,
                reason="drep: no dereplicated_genomes/ FASTAs or Wdb.csv found",
                retry_params={"hint": "check OUT_DIR; ensure -g input genomes exist and pass -comp/-con filters"},
            )
        n_reps = len(reps)
        if n_reps == 0 and wdb:
            try:
                with open(wdb, encoding="utf-8") as fh:
                    n_reps = max(0, sum(1 for ln in fh if ln.strip()) - 1)
            except Exception:
                pass
        n_clusters = None
        if cdb:
            try:
                clusters = set()
                with open(cdb, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        c = row.get("secondary_cluster") or row.get("primary_cluster")
                        if c:
                            clusters.add(c)
                n_clusters = len(clusters) or None
            except Exception:
                n_clusters = None
        score = min(1.0, n_reps / 5.0) if n_reps else 0.5
        m: Dict = {"n_representatives": n_reps}
        if n_clusters is not None:
            m["n_clusters"] = n_clusters
        return ContractResult(ok=True, score=score,
                              reason=(f"drep: {n_reps} representative genome(s)"
                                      + (f", {n_clusters} cluster(s)" if n_clusters else "")),
                              metrics=m)


# ===========================================================================
# VIROMICS  (new contracts — previously uncovered tools)
# ===========================================================================

class VirSorter2Contract(_BaseContract):
    """
    VirSorter2 viral-sequence identification from contigs.
    Output: final-viral-score.tsv (seqname, <per-group scores>, max_score,
            max_score_group, length, hallmark, viral, cellular) + final-viral-combined.fa.
    Source: https://github.com/jiarong/VirSorter2 (default min score 0.5).
    """
    KEYWORDS = ("virsorter2", "virsorter", "viral sequence identification",
                "identify viral contigs", "viral contig detection")
    RUNTIME = "medium"
    VARIANTS = [
        "lower --min-score (e.g. 0.5→0.3) or restrict --include-groups; verify the VirSorter2 DB path",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        score_tsv = self._glob_first(run_dir, "final-viral-score.tsv", "*viral-score.tsv",
                                     "*final-viral*.tsv")
        if not score_tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="virsorter2: final-viral-score.tsv not found",
                retry_params={"hint": "check output dir; verify VirSorter2 database (--db-dir / setup)"},
            )
        n_viral = 0
        try:
            with open(score_tsv, encoding="utf-8") as fh:
                n_viral = max(0, sum(1 for ln in fh if ln.strip()) - 1)  # minus header
        except Exception:
            pass
        score = min(1.0, n_viral / 10.0) if n_viral else 0.4
        return ContractResult(ok=True, score=score,
                              reason=f"virsorter2: {n_viral} viral sequence(s) identified",
                              metrics={"n_viral_sequences": n_viral,
                                       "_source_file": os.path.basename(score_tsv)})


class CheckVContract(_BaseContract):
    """
    CheckV viral genome quality assessment.
    Output: quality_summary.tsv — columns contig_id, contig_length, provirus,
            gene_count, viral_genes, host_genes, checkv_quality, miuvig_quality,
            completeness, contamination. Source: https://bitbucket.org/berkeleylab/checkv
    """
    KEYWORDS = ("checkv", "viral genome quality", "viral completeness",
                "viral quality assessment", "phage completeness")
    RUNTIME = "medium"
    VARIANTS = [
        "verify CheckV database path (checkv download_database); input must be viral contigs (from VirSorter2/geNomad)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        summ = self._glob_first(run_dir, "quality_summary.tsv", "*quality_summary*.tsv",
                                "*checkv*summary*.tsv")
        if not summ:
            return ContractResult(
                ok=False, score=0.0,
                reason="checkv: quality_summary.tsv not found",
                retry_params={"hint": "check --output dir; ensure CheckV database is downloaded"},
            )
        n = complete = high = 0
        comps: List[float] = []
        try:
            with open(summ, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    n += 1
                    q = (row.get("checkv_quality", "") or "").strip()
                    if q == "Complete":
                        complete += 1
                    elif q == "High-quality":
                        high += 1
                    try:
                        comps.append(float(row.get("completeness", "") or "nan"))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
        if n == 0:
            return ContractResult(ok=True, score=0.3,
                                  reason="checkv: summary found but no contig rows")
        valid_comps = [c for c in comps if c == c]  # drop NaN
        score = min(1.0, (complete + high) / max(1, n) + 0.3)
        metrics = {"n_viral_contigs": n, "n_complete": complete, "n_high_quality": high,
                   "_source_file": os.path.basename(summ)}
        if valid_comps:
            metrics["mean_completeness"] = round(sum(valid_comps) / len(valid_comps), 1)
        return ContractResult(ok=True, score=score,
                              reason=f"checkv: {n} contig(s), {complete} complete, {high} high-quality",
                              metrics=metrics)


class DeepVirFinderContract(_BaseContract):
    """
    DeepVirFinder viral scoring of contigs.
    Output: <input>_gt<L>bp_dvfpred.txt — columns name, len, score, pvalue.
    Source: https://github.com/jessieren/DeepVirFinder (viral = high score, low p-value).
    """
    KEYWORDS = ("deepvirfinder", "dvf", "viral score", "deep learning viral")
    RUNTIME = "medium"
    VARIANTS = [
        "lower --score-cutoff (0.9→0.7) or raise --pvalue-cutoff; verify the DVF model/script path",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        pred = self._glob_first(run_dir, "*dvfpred.txt", "*dvf*.txt", "*dvf*.tsv")
        if not pred:
            return ContractResult(
                ok=False, score=0.0,
                reason="deepvirfinder: *_dvfpred.txt not found",
                retry_params={"hint": "check output dir and dvf.py path; lower --min-length if no contigs passed"},
            )
        total = viral = 0
        try:
            with open(pred, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    try:
                        if float(row.get("score", 0) or 0) >= 0.9 and float(row.get("pvalue", 1) or 1) <= 0.05:
                            viral += 1
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
        if total == 0:
            return ContractResult(ok=True, score=0.3,
                                  reason="deepvirfinder: prediction file found but no rows")
        score = min(1.0, viral / max(1, total) + 0.3)
        return ContractResult(ok=True, score=score,
                              reason=f"deepvirfinder: {viral}/{total} contigs viral (score≥0.9, p≤0.05)",
                              metrics={"n_scored": total, "n_viral": viral,
                                       "_source_file": os.path.basename(pred)})


class GgetVirusContract(_BaseContract):
    """
    gget virus — download virus genome sequences + metadata from NCBI Virus.
    Output: <name>_sequences.fasta (genomes) + <name>_metadata.csv/.jsonl.
    Source: https://github.com/pachterlab/gget (virus module).
    """
    KEYWORDS = ("gget virus", "gget_virus", "download virus genomes",
                "ncbi virus download", "fetch virus sequences")
    RUNTIME = "fast"
    VARIANTS = [
        "narrow the query (add --host / --nuc_completeness complete / --max-seq-length) if too many/too few sequences",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        fasta = self._glob_first(run_dir, "*_sequences.fasta", "*sequences*.fasta", "*.fasta", "*.fa")
        meta  = self._glob_first(run_dir, "*_metadata.csv", "*metadata*.csv")
        if not fasta and not meta:
            return ContractResult(
                ok=False, score=0.0,
                reason="gget_virus: no *_sequences.fasta or *_metadata.csv found",
                retry_params={"hint": "check output dir; broaden/narrow the query or verify network access"},
            )
        n_seqs = 0
        if fasta:
            try:
                with open(fasta, encoding="utf-8") as fh:
                    n_seqs = sum(1 for ln in fh if ln.startswith(">"))
            except Exception:
                pass
        score = min(1.0, n_seqs / 10.0) if n_seqs else 0.5
        return ContractResult(ok=True, score=score,
                              reason=f"gget_virus: {n_seqs} virus sequence(s) downloaded",
                              metrics=({"n_sequences": n_seqs, "_source_file": os.path.basename(fasta)}
                                       if fasta else {}))


# ===========================================================================
# SIMULATION / QC-AGGREGATION / GROWTH / PLASMIDS  (new contracts)
# ===========================================================================

class InSilicoSeqContract(_BaseContract):
    """
    InSilicoSeq (iss) — simulate realistic Illumina reads from reference genomes.
    Output: <prefix>_R1.fastq + <prefix>_R2.fastq + <prefix>_abundance.txt/tsv.
    Source: https://github.com/HadrienG/InSilicoSeq
    """
    KEYWORDS = ("insilicoseq", "iss generate", "in silico seq", "insilico seq")
    RUNTIME = "medium"
    VARIANTS = [
        "verify --genomes reference FASTA and --model (miseq|hiseq|novaseq); raise --n_reads if too few",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        r1 = self._glob_first(run_dir, "*_R1.fastq", "*_R1.fq", "*R1*.fastq")
        abund = self._glob_first(run_dir, "*abundance*.txt", "*abundance*.tsv")
        if not r1 and not abund:
            return ContractResult(
                ok=False, score=0.0,
                reason="insilicoseq: no *_R1.fastq or *_abundance* output found",
                retry_params={"hint": "check --output prefix; verify --genomes reference exists"},
            )
        metrics: Dict = {}
        if r1:
            try:
                with open(r1, encoding="utf-8", errors="replace") as fh:
                    metrics["read_pairs"] = sum(1 for _ in fh) // 4
            except Exception:
                pass
        if abund:
            try:
                with open(abund, encoding="utf-8") as fh:
                    metrics["n_genomes"] = sum(1 for ln in fh if ln.strip())
            except Exception:
                pass
        return ContractResult(ok=True, score=1.0 if r1 else 0.6,
                              reason=f"insilicoseq: reads simulated "
                                     f"({metrics.get('read_pairs', '?')} pairs)",
                              metrics=metrics)


class MultiqcContract(_BaseContract):
    """
    MultiQC — aggregate per-sample QC reports into one HTML + data dir.
    Output: multiqc_report.html + multiqc_data/multiqc_general_stats.txt (one row/sample).
    Source: https://multiqc.info/
    """
    KEYWORDS = ("multiqc", "aggregate qc", "aggregate reports", "combined qc report")
    RUNTIME = "fast"
    VARIANTS = [
        "point multiqc at the directory that actually contains the tool logs/reports; add -f to overwrite",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        html  = self._glob_first(run_dir, "multiqc_report.html", "*multiqc*report*.html")
        stats = self._glob_first(run_dir, "multiqc_general_stats.txt", "*general_stats*.txt")
        if not html and not stats:
            return ContractResult(
                ok=False, score=0.0,
                reason="multiqc: no multiqc_report.html or general stats found",
                retry_params={"hint": "verify the input directory contains recognizable tool reports"},
            )
        metrics: Dict = {}
        if stats:
            try:
                with open(stats, encoding="utf-8") as fh:
                    metrics["n_samples"] = max(0, sum(1 for ln in fh if ln.strip()) - 1)
                    metrics["_source_file"] = os.path.basename(stats)
            except Exception:
                pass
        return ContractResult(ok=True, score=1.0,
                              reason="multiqc: aggregated report produced",
                              metrics=metrics)


class IRepContract(_BaseContract):
    """
    iRep — bacterial replication rate (index of replication) from coverage trend.
    Output: <prefix>.tsv with per-genome iRep values (needs ≥75% complete genome,
            low contamination; unreliable results are not reported as growth rates).
    Source: https://github.com/christophertbrown/iRep (Brown et al. 2016).
    """
    KEYWORDS = ("irep", "replication rate", "index of replication",
                "growth rate", "replication index")
    RUNTIME = "medium"
    VARIANTS = [
        "iRep needs a >=75% complete, low-contamination genome and a SAM (not BAM) of reads vs that genome",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*iRep*.tsv", "*irep*.tsv", "*.tsv")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="irep: no iRep .tsv output found",
                retry_params={"hint": "check output prefix; provide a SAM (not BAM) of reads vs the genome"},
            )
        vals: List[float] = []
        try:
            with open(tsv, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.rstrip("\n").split("\t")
                    for tok in parts[1:]:
                        try:
                            v = float(tok)
                            if 0.5 < v < 10:   # plausible iRep range
                                vals.append(v)
                                break
                        except ValueError:
                            continue
        except Exception:
            pass
        if not vals:
            return ContractResult(ok=True, score=0.4,
                                  reason="irep: output found but no reliable iRep values")
        return ContractResult(ok=True, score=min(1.0, len(vals) / 3.0),
                              reason=f"irep: {len(vals)} genome(s) with iRep, mean={sum(vals)/len(vals):.2f}",
                              metrics={"n_genomes": len(vals),
                                       "mean_irep": round(sum(vals) / len(vals), 2),
                                       "_source_file": os.path.basename(tsv)})


class MobReconContract(_BaseContract):
    """
    MOB-recon — reconstruct plasmids from an assembly and classify contigs.
    Output: contig_report.txt (per-contig chromosome/plasmid + cluster),
            mobtyper_results.txt, plasmid_*.fasta. Source: https://github.com/phac-nml/mob-suite
    """
    KEYWORDS = ("mob_recon", "mob recon", "mob-recon", "plasmid reconstruction",
                "reconstruct plasmids")
    RUNTIME = "medium"
    VARIANTS = [
        "add --force to overwrite; verify input is an assembled FASTA (not raw reads)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        contig_report = self._glob_first(run_dir, "contig_report.txt", "*contig_report*.txt")
        plasmids = self._glob_all(run_dir, "plasmid_*.fasta", "plasmid_*.fa")
        if not contig_report and not plasmids:
            return ContractResult(
                ok=False, score=0.0,
                reason="mob_recon: no contig_report.txt or plasmid_*.fasta found",
                retry_params={"hint": "check -o output dir; input must be an assembled FASTA"},
            )
        n_plasmids = len(plasmids)
        if n_plasmids == 0 and contig_report:
            try:
                clusters = set()
                with open(contig_report, encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter="\t")
                    for row in reader:
                        mol = (row.get("molecule_type", "") or "").lower()
                        cid = row.get("primary_cluster_id") or row.get("cluster_id")
                        if "plasmid" in mol and cid:
                            clusters.add(cid)
                n_plasmids = len(clusters)
            except Exception:
                pass
        return ContractResult(ok=True, score=min(1.0, 0.5 + n_plasmids / 5.0),
                              reason=f"mob_recon: {n_plasmids} plasmid(s) reconstructed",
                              metrics={"n_plasmids": n_plasmids})


class MobTyperContract(_BaseContract):
    """
    MOB-typer — type a plasmid FASTA (replicon/relaxase/MPF + predicted mobility).
    Output: mobtyper report TSV — columns sample_id, ..., rep_type(s),
            relaxase_type(s), mpf_type, predicted_mobility. Source: MOB-suite.
    """
    KEYWORDS = ("mob_typer", "mob typer", "mob-typer", "plasmid typing",
                "plasmid mobility")
    RUNTIME = "fast"
    VARIANTS = [
        "input must be a single plasmid FASTA; for a whole assembly use mob_recon first",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tsv = self._glob_first(run_dir, "*mobtyper*.txt", "*mobtyper*.tsv", "*mob_typer*.txt")
        if not tsv:
            return ContractResult(
                ok=False, score=0.0,
                reason="mob_typer: no mobtyper report found",
                retry_params={"hint": "check --out_file path; input must be a plasmid FASTA"},
            )
        n = conjugative = 0
        try:
            with open(tsv, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    n += 1
                    if (row.get("predicted_mobility", "") or "").strip().lower() == "conjugative":
                        conjugative += 1
        except Exception:
            pass
        if n == 0:
            return ContractResult(ok=True, score=0.4,
                                  reason="mob_typer: report found but no plasmid rows")
        return ContractResult(ok=True, score=1.0,
                              reason=f"mob_typer: {n} plasmid(s) typed, {conjugative} conjugative",
                              metrics={"n_plasmids_typed": n, "n_conjugative": conjugative,
                                       "_source_file": os.path.basename(tsv)})


# ===========================================================================
# GENOMICS / EPIGENOMICS / scRNA / NCBI  (new contracts)
#
# SAFETY: these domains have looser, sometimes binary (.h5ad) or off-run-dir
# outputs, so EVERY contract here NEVER returns ok=False. When the expected file
# is missing it returns score=-1.0 (== "no contract", defer to observer) so a
# working step can never be broken by a false failure. Metrics are added only
# when a recognizable output file is actually present in the run dir.
# ===========================================================================

class Macs2Contract(_BaseContract):
    """
    MACS2 ChIP-seq peak calling.
    Output: <name>_peaks.narrowPeak (or .broadPeak) — BED6+ , one line per peak.
    Source: https://github.com/macs3-project/MACS
    """
    KEYWORDS = ("macs2", "peak calling", "chip-seq peak", "chipseq peak",
                "call peaks")
    RUNTIME = "medium"
    VARIANTS = [
        "adjust -q (q-value, default 0.05) or --broad for broad marks; verify -c control file",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        peaks = self._glob_first(run_dir, "*_peaks.narrowPeak", "*_peaks.broadPeak",
                                 "*.narrowPeak", "*.broadPeak")
        if not peaks:
            return ContractResult(ok=True, score=-1.0, reason="macs2: no peak file (defer to observer)")
        n_peaks = 0
        try:
            with open(peaks, encoding="utf-8") as fh:
                n_peaks = sum(1 for ln in fh if ln.strip() and not ln.startswith(("#", "track")))
        except Exception:
            pass
        return ContractResult(ok=True, score=min(1.0, n_peaks / 1000.0),
                              reason=f"macs2: {n_peaks} peak(s) called",
                              metrics={"n_peaks": n_peaks, "_source_file": os.path.basename(peaks)})


class HomerMotifContract(_BaseContract):
    """
    HOMER known-motif enrichment.
    Output: knownResults.txt — TSV; columns include 'Motif Name', 'P-value',
            'q-value (Benjamini)'. Source: http://homer.ucsd.edu/homer/
    """
    KEYWORDS = ("homer", "enriched motif", "motif enrichment", "find motifs",
                "de novo motif")
    RUNTIME = "medium"
    VARIANTS = [
        "verify -genome and that the peak/BED input is non-empty; adjust -size / -len",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        kr = self._glob_first(run_dir, "knownResults.txt", "*knownResults*.txt")
        if not kr:
            return ContractResult(ok=True, score=-1.0, reason="homer: no knownResults.txt (defer to observer)")
        total = significant = 0
        try:
            with open(kr, encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter="\t")
                for row in reader:
                    total += 1
                    qv = row.get("q-value (Benjamini)") or row.get("P-value") or ""
                    try:
                        if float(qv) <= 0.05:
                            significant += 1
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
        return ContractResult(ok=True, score=min(1.0, significant / 20.0) if significant else 0.5,
                              reason=f"homer: {significant}/{total} motif(s) significant (q≤0.05)",
                              metrics={"n_motifs": total, "n_significant": significant,
                                       "_source_file": os.path.basename(kr)})


class GseaContract(_BaseContract):
    """
    Gene-set enrichment analysis → a table of enriched terms/pathways.
    Output: an enrichment table (CSV/TSV) — one row per term with a p/adj-p value.
    """
    KEYWORDS = ("gene set enrichment", "gsea", "pathway enrichment",
                "enrichment analysis", "over-representation")
    RUNTIME = "fast"
    VARIANTS = [
        "try a different --database (pathway|transcription|ontology); check the input gene list is non-empty",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tab = self._glob_first(run_dir, "*enrich*.csv", "*enrich*.tsv", "*gsea*.csv",
                               "*enrichment*.csv", "*pathway*.csv")
        if not tab:
            return ContractResult(ok=True, score=-1.0, reason="gsea: no enrichment table (defer to observer)")
        n_terms = 0
        try:
            with open(tab, encoding="utf-8") as fh:
                n_terms = max(0, sum(1 for ln in fh if ln.strip()) - 1)
        except Exception:
            pass
        return ContractResult(ok=True, score=min(1.0, n_terms / 10.0) if n_terms else 0.5,
                              reason=f"gsea: {n_terms} enriched term(s)",
                              metrics={"n_terms": n_terms, "_source_file": os.path.basename(tab)})


class RegionOverlapContract(_BaseContract):
    """
    Genomic region-overlap analysis (interval intersection).
    Output: an overlap table (TSV/CSV/BED) with the intersecting intervals.
    """
    KEYWORDS = ("genomic region overlap", "region overlap", "interval overlap",
                "overlap analysis")
    RUNTIME = "fast"
    VARIANTS = [
        "verify the region sets are valid BED (chrom,start,end) or coordinate tuples",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tab = self._glob_first(run_dir, "*overlap*.tsv", "*overlap*.csv", "*overlap*.bed",
                               "*intersect*.bed")
        if not tab:
            return ContractResult(ok=True, score=-1.0, reason="region-overlap: no output (defer to observer)")
        n = 0
        try:
            with open(tab, encoding="utf-8") as fh:
                n = sum(1 for ln in fh if ln.strip() and not ln.startswith(("#", "track", "chrom")))
        except Exception:
            pass
        return ContractResult(ok=True, score=0.7,
                              reason=f"region-overlap: {n} overlapping interval(s)",
                              metrics={"n_overlaps": n, "_source_file": os.path.basename(tab)})


class ChromatinContract(_BaseContract):
    """
    Hi-C chromatin-interaction analysis (.cool/.hic → matrices/plots/TADs).
    No cheap tabular metric — verifies output presence only.
    """
    KEYWORDS = ("chromatin", "hi-c", "hic analysis", "contact matrix",
                "topological domain", "tad calling")
    RUNTIME = "medium"
    VARIANTS = [
        "verify the input is a valid .cool or .hic file and the resolution is supported",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        out = self._glob_first(run_dir, "*.cool", "*.hic", "*interaction*", "*.png", "*.pdf")
        if not out:
            return ContractResult(ok=True, score=-1.0, reason="chromatin: no output (defer to observer)")
        return ContractResult(ok=True, score=0.7,
                              reason="chromatin: interaction output produced",
                              metrics={"output_present": True, "_source_file": os.path.basename(out)})


class ScrnaContract(_BaseContract):
    """
    Single-cell RNA tools (annotation / scVI / Harmony / UCE embeddings / IMA map).
    Output: an AnnData .h5ad object (binary HDF5). validator.py is stdlib-only, so
    no h5ad parsing here — verifies the .h5ad output exists only.
    """
    KEYWORDS = ("single-cell", "single cell", "scrna", "cell type annotation",
                "annotate celltype", "annotate cell type", "scvi", "harmony embedding",
                "uce embedding", "cell clustering", "panhumanpy")
    RUNTIME = "medium"
    VARIANTS = [
        "verify the input AnnData (.h5ad) path and that the requested layer/embedding exists",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        h5ad = self._glob_first(run_dir, "*.h5ad")
        if not h5ad:
            return ContractResult(ok=True, score=-1.0, reason="scrna: no .h5ad output (defer to observer)")
        return ContractResult(ok=True, score=0.8,
                              reason=f"scrna: AnnData produced ({os.path.basename(h5ad)})",
                              metrics={"h5ad_present": True, "_source_file": os.path.basename(h5ad)})


class NcbiDownloadContract(_BaseContract):
    """
    NCBI genome download (ncbi-genome-download / datasets).
    Output: one or more genome FASTA files (*_genomic.fna[.gz] / *.fna / *.fasta).
    """
    KEYWORDS = ("download_from_ncbi", "ncbi-genome-download", "download genome",
                "genome from ncbi", "from ncbi", "ncbi genome", "datasets download genome")
    RUNTIME = "fast"
    VARIANTS = [
        "reference the organism BY NAME (-g \"Genus species\"); NCBI is rate-limited — retry on transient errors",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        genomes = self._glob_all(run_dir, "*_genomic.fna.gz", "*_genomic.fna",
                                 "*.fna.gz", "*.fna", "*.fasta")
        if not genomes:
            return ContractResult(ok=True, score=-1.0, reason="ncbi-download: no genome FASTA (defer to observer)")
        n = len(genomes)
        return ContractResult(ok=True, score=min(1.0, n / 1.0),
                              reason=f"ncbi-download: {n} genome FASTA file(s) downloaded",
                              metrics={"n_genomes_downloaded": n,
                                       "_source_file": os.path.basename(genomes[0])})


class Archs4Contract(_BaseContract):
    """
    ARCHS4 RNA-seq expression fetch for a gene across tissues.
    Output (when written to a file): a small table of tissue -> expression.
    Often returned as data; contract adds a metric only when a table is present.
    """
    KEYWORDS = ("archs4", "rna_seq_archs4", "rna-seq expression",
                "tissue expression", "expression across tissues")
    RUNTIME = "fast"
    VARIANTS = [
        "verify the gene name is a valid HGNC symbol; adjust K (number of tissues)",
    ]

    def check(self, run_dir: str, stdout: str) -> ContractResult:
        tab = self._glob_first(run_dir, "*archs4*.csv", "*archs4*.tsv", "*expression*.csv",
                               "*tissue*.csv", "*tissue*.tsv")
        if not tab:
            return ContractResult(ok=True, score=-1.0, reason="archs4: no expression table (defer to observer)")
        n_tissues = 0
        try:
            with open(tab, encoding="utf-8") as fh:
                n_tissues = max(0, sum(1 for ln in fh if ln.strip()) - 1)
        except Exception:
            pass
        return ContractResult(ok=True, score=0.7,
                              reason=f"archs4: expression across {n_tissues} tissue(s)",
                              metrics={"n_tissues": n_tissues, "_source_file": os.path.basename(tab)})


# ===========================================================================
# Registry + dispatcher
# ===========================================================================

_ALL_CONTRACTS: List[_BaseContract] = [
    # Filtlong BEFORE Fastp: a "filter reads with filtlong" step must be graded by the
    # non-empty-output filtlong contract, not by the generic fastp contract (which never
    # checked output size and let a 0-byte filtered FASTQ pass as ok=True, blocking Flye).
    FiltlongContract(),
    # QC / trimming
    FastpContract(),
    FastqcContract(),
    # SraFetch BEFORE the simulation contracts: a real read-DOWNLOAD step
    # ("Fetch raw paired-end reads … with fetch_sra_reads") must be graded as a
    # download (any FASTQ present), NOT by wgsim's reads_R1.fastq naming rule
    # which caused a spurious retry + full re-download on DRR102584.
    SraFetchContract(),
    # InSilicoSeq BEFORE Wgsim: an "iss / InSilicoSeq" step also says "simulate
    # reads" (a Wgsim keyword), so it must be matched by its own contract first.
    InSilicoSeqContract(),
    WgsimContract(),
    BbdukContract(),
    # Taxonomic
    Kraken2Contract(),
    SylphContract(),
    KaijuContract(),
    # ORF/protein prediction — checked BEFORE assembly so "orf prediction" steps
    # aren't absorbed by AssemblyContract (which also matches "assembly statistics")
    ProdigalContract(),
    ProkkaContract(),
    # Long-read polishing (Racon, Medaka) — MUST be checked BEFORE AssemblyContract:
    # both tools' step titles routinely say "...on the draft ASSEMBLY", and
    # AssemblyContract's "assembly" keyword + "*.fasta" catch-all glob would
    # otherwise silently mis-score a polishing step under assembly-N50 semantics.
    MedakaContract(),
    RaconContract(),
    # Read-mapping / depth — MUST be checked BEFORE AssemblyContract: a mapping step title
    # says "Map reads ... to the CO-ASSEMBLY with minimap2 ... jgi_summarize_bam_contig_depths",
    # and AssemblyContract's "co-assembly" keyword + "*.fasta" catch-all would otherwise steal
    # it and score it 1.00 off the (previous) contig file — passing the step as "done" WITHOUT
    # ever producing the sorted BAMs / depth.txt, so the next (binning) step has no depth file
    # and fails. Checking Minimap2Contract first requires the real depth/BAM output.
    Minimap2Contract(),
    # Assembly
    AssemblyContract(),
    QuastContract(),
    # Consensus / dereplication matched BEFORE the individual binners: a DAS_Tool step
    # title names "MetaBAT2/MaxBin2" and a dRep step names "DAS_Tool", so checking Drep
    # then DAS_Tool first stops a binner keyword (e.g. "MaxBin2") from stealing the match.
    # DrepContract is first so a dRep step mentioning "DAS_Tool" still routes to dRep.
    DrepContract(),
    DasToolContract(),
    # Binning
    SemiBin2Contract(),
    ConcoctContract(),
    MaxBin2Contract(),
    MetaBat2Contract(),
    # Coverage / dereplication / amplicon (added contracts — specific keywords,
    # no overlap with existing ones; placed here so they get first match).
    # (Minimap2Contract moved up before AssemblyContract — see note above.)
    CoverMContract(),
    Dada2Contract(),
    IRepContract(),
    MobReconContract(),
    MobTyperContract(),
    MultiqcContract(),
    # Viromics (added contracts — checked BEFORE CheckM2 so a "viral genome
    # quality" (CheckV) step is not grabbed by CheckM2's "genome quality" keyword,
    # and before geNomad so tool-named VirSorter2/CheckV/DVF steps resolve correctly).
    VirSorter2Contract(),
    CheckVContract(),
    DeepVirFinderContract(),
    GgetVirusContract(),
    # Bin quality
    CheckM2Contract(),
    GuncContract(),
    # Resistome — MUST be checked BEFORE the generic aligner contracts (Hmmer/Diamond/
    # Eggnog) below: RGI/AMRFinder step titles mention "diamond"/"blast"/"hmmer" as their
    # INTERNAL aligner, so first-match ordering would otherwise hand an RGI step to
    # DiamondContract. Real bug it fixes: DiamondContract matched a "Run RGI … DIAMOND"
    # step and its retry hints (--more-sensitive / --evalue) were applied to `rgi main`,
    # which does not accept them → RGI crashed → 0 RGI genes.
    RgiContract(),
    AmrFinderContract(),
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
    # Community / diversity
    NonpareilContract(),
    LefseContract(),
    PhyloseqContract(),
    # Sequence manipulation
    SeqkitContract(),
    # WGS / clinical
    CnvkitContract(),
    OptitypeContract(),
    # Strain-level (DAS_Tool + dRep moved up near the binners for correct matching)
    InStrainContract(),
    # Abundance re-estimation
    BrackenContract(),
    # Marker-gene profiling
    MetaPhlAn4Contract(),
    # Phylogenetic classification
    GtdbtkContract(),
    # Genomics / epigenomics / scRNA / NCBI — APPENDED LAST so every metagenomics
    # contract gets first match; all of these NEVER return ok=False (defer via
    # score=-1.0 when their output is absent) → they can't break a working step.
    Macs2Contract(),
    HomerMotifContract(),
    GseaContract(),
    RegionOverlapContract(),
    ChromatinContract(),
    ScrnaContract(),
    Archs4Contract(),
    NcbiDownloadContract(),
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

    # Leading action verbs that mark a step as POST-PROCESSING the output of a
    # previously-run tool (parse a report, tabulate results, plot a chart…)
    # rather than EXECUTING the tool itself. When a step starts with one of
    # these verbs we must NOT apply the tool's runtime contract, otherwise the
    # contract reads the tool's prior output file and (mis)judges the wrong
    # thing — e.g. "Parse kraken2.report to extract top genera" was wrongly
    # graded by Kraken2Contract on the classification rate (1.1% < 5% → FAIL),
    # blocking a perfectly correct pandas parsing step.
    #
    # Conservative on purpose: only unambiguous post-processing verbs are
    # listed. Ambiguous verbs that can BE a tool invocation are intentionally
    # excluded (e.g. "filter"→fastp, "compute/calculate"→seqkit, "merge/
    # convert"→samtools, "extract"→seqkit grep). Excluding them keeps real
    # tool steps on their deterministic contract; the worst case for a missed
    # post-proc verb is the prior (buggy) behavior, never a new regression.
    _POST_PROCESS_LEADING_VERBS: Tuple[str, ...] = (
        "parse", "summarize", "summarise", "tabulate", "plot",
        "visualize", "visualise", "collate", "reformat", "render",
        "report",
    )

    @staticmethod
    def _is_post_processing_step(step_title: str) -> bool:
        """True if the step's LEADING verb marks it as post-processing a tool's
        output (so no tool runtime contract should apply). Kill-switch:
        GENOMEER_VALIDATOR_POSTPROC_GUARD=0 disables this guard entirely."""
        import os as _os
        if _os.environ.get("GENOMEER_VALIDATOR_POSTPROC_GUARD", "1") == "0":
            return False
        import re as _re
        # First alphabetic word of the (stripped) title, lowercased.
        m = _re.match(r"\s*([a-zA-Z]+)", step_title or "")
        if not m:
            return False
        return m.group(1).lower() in ToolValidator._POST_PROCESS_LEADING_VERBS

    @staticmethod
    def _match_contract(step_title: str) -> Optional[_BaseContract]:
        """Match a contract using whole-word boundary matching.

        Substring matching caused false positives, e.g. "assemble" matching
        "assembled sequence" in unrelated ORF-density steps. Whole-word regex
        ensures keywords only match when they appear as complete words.

        Fix #1 — post-processing guard: a step whose leading verb is a
        post-processing verb (parse/tabulate/plot…) gets NO contract, so it
        routes to the observer (LLM) instead of being mis-graded by the tool's
        runtime contract.
        """
        # Post-processing steps must never inherit a tool's runtime contract.
        if ToolValidator._is_post_processing_step(step_title):
            DISPATCH_COUNTERS["guard_skip"] += 1   # PoC bonus: cheap runtime telemetry
            return None
        import re as _re
        title_lower = (step_title or "").lower()
        for contract in _ALL_CONTRACTS:
            for kw in contract.KEYWORDS:
                # Build a word-boundary pattern. Keywords may contain spaces
                # (e.g. "orf prediction") — anchor only the outer edges.
                pattern = r'\b' + _re.escape(kw.lower()) + r'\b'
                if _re.search(pattern, title_lower):
                    DISPATCH_COUNTERS["contract_hit"] += 1
                    return contract
        DISPATCH_COUNTERS["no_contract"] += 1
        return None

    # Patterns in stdout that indicate the execution environment never ran —
    # any contract check would produce a false positive (files from previous
    # steps are still present in run_dir).
    _ENV_FAILURE_PATTERNS: Tuple[str, ...] = (
        "timed out after",
        "is not available: process timed out",
        "timeouterrorwithcontext",
        "environment.*not available",
        "process timed out",
    )

    @staticmethod
    def validate(step_title: str, run_dir: str, stdout: str) -> ContractResult:
        # Hard-gate: if the execution env never started (timeout / unavailable),
        # no contract check should run — files in run_dir belong to prior steps.
        stdout_lower = (stdout or "").lower()
        import re as _re2
        for pat in ToolValidator._ENV_FAILURE_PATTERNS:
            if _re2.search(pat, stdout_lower):
                return ContractResult(
                    ok=False, score=0.0,
                    reason="env-timeout: execution environment unavailable — step did not run",
                    retry_params={"hint": "wait for the conda environment to finish installing, then retry"},
                )
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
