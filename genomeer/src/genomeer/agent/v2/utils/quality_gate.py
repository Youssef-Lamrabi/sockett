"""
genomeer/agent/v2/utils/quality_gate.py
========================================
Biological quality gates for the Observer node.

Extracts key metrics from metagenomics tool outputs and returns
warn/fail signals so the observer can set <STATUS:blocked> instead
of silently accepting biologically invalid results.

Usage (inside observer node):
    from genomeer.agent.v2.utils.quality_gate import check_quality
    level, message = check_quality(tool_name, result_dict, stdout_text)
    # level: "ok" | "warn" | "fail"
"""

from __future__ import annotations
import re
from typing import Tuple, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Quality thresholds
# ---------------------------------------------------------------------------
# Format per tool:
#   metric_key      : key in result_dict (or None to parse from stdout)
#   warn_threshold  : value below this triggers a WARNING
#   fail_threshold  : value below this triggers a FAIL (blocks the pipeline)
#   parse_regex     : optional regex to extract value from raw stdout if not in dict
# ---------------------------------------------------------------------------

BIOLOGICAL_GATES: Dict[str, Dict[str, Any]] = {

    # ── QC ──────────────────────────────────────────────────────────────────

    "run_fastp": {
        # FIX G4: run_fastp now exposes q30_rate at top-level of return dict (G8 fix)
        # Previously relied on regex parsing of raw text which never matched
        "metric_key":     "q30_rate",
        "metric_label":   "Q30 base rate (after filtering)",
        "warn_threshold": 0.60,
        "fail_threshold": 0.40,
        "parse_regex":    r"q30_rate[\"':,\s]+([0-9.]+)",  # fallback if dict missing
        "fix_hint": (
            "Poor Q30 rate. Try lowering --min_quality threshold or check if the "
            "library preparation had quality issues."
        ),
    },

    "run_fastqc": {
        "metric_key":     None,          # FastQC result is in HTML — we parse stderr keyword
        "metric_label":   "FastQC PASS/FAIL",
        "warn_threshold": None,
        "fail_threshold": None,
        "parse_regex":    r"FAIL",       # Any FAIL in stdout is a warning
        "fail_sentinel":  True,          # Presence of regex = WARN (not fail)
        "fix_hint": (
            "FastQC reported one or more FAIL modules. "
            "Inspect the HTML report before proceeding with assembly."
        ),
    },

    # ── Host Decontamination ────────────────────────────────────────────────
    "run_host_decontamination": {
        "metric_key":     "microbial_pct",
        "metric_label":   "% non-host reads retained",
        "warn_threshold": 10.0,
        "fail_threshold": 1.0,
        "parse_regex":    r"microbial_pct[\"':,\s]+([0-9.]+)",
        "fix_hint": (
            "Very few non-host reads retained (<1%). The sample may be predominantly "
            "host material with very low microbial biomass. Consider: "
            "(1) Verify sample origin and collection method. "
            "(2) Use a more sensitive metagenomic protocol. "
            "(3) Check if the host index is correct for this organism."
        ),
    },

    # ── Taxonomic classification ─────────────────────────────────────────────

    "run_kraken2": {
        # FIX G4: run_kraken2 now exposes classified_pct as float (G9 fix)
        # Previously only classification_summary string was returned; regex fallback kept
        "metric_key":     "classified_pct",
        "metric_label":   "% reads classified",
        "warn_threshold": 20.0,
        "fail_threshold": 3.0,
        "parse_regex":    r"([0-9]+\.[0-9]+)%\s+of\s+sequences\s+classified",
        "fix_hint": (
            "Very few reads were classified by Kraken2. Check: "
            "(1) Is the database appropriate for this sample type? "
            "(2) Did fastp remove too many reads? "
            "(3) Is there host/contaminant DNA dominating?"
        ),
    },

    "run_metaphlan4": {
        "metric_key":     None,
        "metric_label":   "MetaPhlAn4 hits",
        "warn_threshold": None,
        "fail_threshold": None,
        "parse_regex":    r"#estimated_reads_mapped_to_known_clades:\s*([0-9]+)",
        "warn_below":     1000,
        "fail_below":     100,
        "fix_hint": (
            "Very few reads mapped to known marker genes. "
            "This could indicate an unusual metagenome (e.g. non-standard hosts) "
            "or the sample may require the extended MetaPhlAn4 database."
        ),
    },

    # ── Assembly ─────────────────────────────────────────────────────────────

    "run_metaspades": {
        "metric_key":     "n50_bp",
        "metric_label":   "Assembly N50 (bp)",
        "warn_threshold": 1000,
        "fail_threshold": 200,
        "parse_regex":    r"N50\s+([0-9]+)",
        "fix_hint": (
            "Assembly N50 is very low. Consider: "
            "(1) More aggressive fastp trimming. "
            "(2) Using MEGAHIT (lower RAM, often better for low-coverage samples). "
            "(3) Verify input reads are not heavily contaminated."
        ),
    },

    "run_megahit": {
        "metric_key":     "n50_bp",
        "metric_label":   "Assembly N50 (bp)",
        "warn_threshold": 1000,
        "fail_threshold": 200,
        "parse_regex":    r"N50\s+([0-9]+)",
        "fix_hint": (
            "MEGAHIT assembly N50 is very low. "
            "Try increasing --min-contig-len or subsample to check read quality."
        ),
    },

    "run_flye": {
        "metric_key":     "n50_bp",
        "metric_label":   "Assembly N50 (bp)",
        "warn_threshold": 5000,
        "fail_threshold": 500,
        "parse_regex":    r"N50\s+([0-9]+)",
        "fix_hint": (
            "Flye assembly N50 is very low for long reads. "
            "Check NanoStat output: filter reads <500bp, or add --min-overlap flag."
        ),
    },

    # ── Coverage ─────────────────────────────────────────────────────────────

    "compute_coverage_samtools": {
        "metric_key":     "mean_coverage_across_contigs",
        "metric_label":   "Mean contig coverage (X)",
        "warn_threshold": 2.0,
        "fail_threshold": 0.5,
        "parse_regex":    r"mean(?:coverage|depth)[\"':,\s]+([0-9.]+)",
        "fix_hint": (
            "Very low coverage. MetaBAT2 binning requires >5X coverage for reliable bins. "
            "Consider downsampling or using more input reads."
        ),
    },

    # ── Binning ───────────────────────────────────────────────────────────────

    "run_metabat2": {
        "metric_key":     "n_bins",
        "metric_label":   "Number of bins produced",
        "warn_threshold": 1,
        "fail_threshold": 0,
        "parse_regex":    r"n_bins[\"':,\s]+([0-9]+)",
        "fail_on_zero":   True,
        "fix_hint": (
            "MetaBAT2 produced 0 bins. Check: "
            "(1) Assembly contigs are long enough (min_contig >= 1500 bp). "
            "(2) Coverage depth files were correctly generated. "
            "(3) Try a lower --min-contig threshold."
        ),
    },

    "run_checkm2": {
        "metric_key":     "mean_completeness",
        "metric_label":   "Mean MAG completeness (%)",
        "warn_threshold": 50.0,
        "fail_threshold": 20.0,
        "parse_regex":    r"mean_completeness[\"':,\s]+([0-9.]+)",
        "fix_hint": (
            "MAG completeness is very low. This may indicate: "
            "(1) Poor assembly or binning. "
            "(2) Unusual/novel organisms not covered by CheckM2 models. "
            "Consider running DAS_Tool to refine bins before downstream analysis."
        ),
    },

    "run_gtdbtk": {
        "metric_key":     "n_classified",
        "metric_label":   "MAGs classified by GTDB-Tk",
        "warn_threshold": 1,
        "fail_threshold": 0,
        "fail_on_zero":   True,
        "parse_regex":    r"(\d+)\s+(?:genomes?|MAGs?)\s+(?:classified|processed)",
        "fix_hint": (
            "GTDB-Tk classified 0 MAGs. Check: "
            "(1) CheckM2 output bins exist and are in FASTA format. "
            "(2) The GTDB database path is set correctly (GTDBTK_DATA_PATH env var). "
            "(3) Bins are > 50% complete (CheckM2 filter)."
        ),
    },

    "run_das_tool": {
        "metric_key":     "n_bins_refined",
        "metric_label":   "Bins after DAS_Tool refinement",
        "warn_threshold": 1,
        "fail_threshold": 0,
        "fail_on_zero":   True,
        "parse_regex":    r"(\d+)\s+(?:bins?|genomes?)\s+(?:written|selected|scored)",
        "fix_hint": (
            "DAS_Tool produced 0 refined bins. Check: "
            "(1) At least one binner produced bins (MetaBAT2 output not empty). "
            "(2) Scaffold2bin files were correctly formatted. "
            "(3) Score threshold (--score_threshold) may be too strict — try 0.3."
        ),
    },

}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_quality(
    tool_name: str,
    result_dict: Optional[Dict[str, Any]],
    stdout_text: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Check biological quality for a metagenomics tool output.

    Parameters
    ----------
    tool_name   : Name of the tool function called (e.g. 'run_kraken2')
    result_dict : The dict returned by the tool wrapper (may be None)
    stdout_text : Raw stdout/stderr from the subprocess (optional, used for parsing)

    Returns
    -------
    (level, message) where level is 'ok', 'warn', or 'fail'
    """
    gate = BIOLOGICAL_GATES.get(tool_name)
    if gate is None:
        return ("ok", "")

    result_dict = result_dict or {}
    stdout_text = stdout_text or ""

    metric_key     = gate.get("metric_key")
    metric_label   = gate.get("metric_label", metric_key)
    warn_thresh    = gate.get("warn_threshold")
    fail_thresh    = gate.get("fail_threshold")
    parse_regex    = gate.get("parse_regex")
    fix_hint       = gate.get("fix_hint", "")

    # ── Special handling: sentinel-based (FastQC FAIL lines) ──────────────
    if gate.get("fail_sentinel"):
        if parse_regex and re.search(parse_regex, stdout_text, re.IGNORECASE):
            return ("warn", f"[QA-WARN] {metric_label}: FAIL modules detected in output. {fix_hint}")
        return ("ok", f"{metric_label}: no FAIL modules detected")

    # ── Try extracting metric value ────────────────────────────────────────
    value: Optional[float] = None

    # 1. From result dict
    if metric_key and metric_key in result_dict:
        try:
            value = float(result_dict[metric_key])
        except (TypeError, ValueError):
            pass

    # 2. From stdout via regex
    if value is None and parse_regex and stdout_text:
        m = re.search(parse_regex, stdout_text, re.IGNORECASE)
        if m:
            try:
                value = float(m.group(1))
            except (ValueError, IndexError):
                pass

    # ── If we couldn't extract a number, just return ok ───────────────────
    if value is None:
        return ("ok", f"{metric_label}: could not extract metric (skipping gate)")

    # ── Threshold comparisons ─────────────────────────────────────────────
    # Handle special case for warn/fail below explicit fields
    warn_below = gate.get("warn_below", warn_thresh)
    fail_below = gate.get("fail_below", fail_thresh)

    if fail_below is not None and value < fail_below:
        return (
            "fail",
            (
                f"[QA-FAIL] {metric_label} = {value:.2f} is BELOW fail threshold {fail_below}. "
                f"Pipeline should NOT continue. {fix_hint}"
            )
        )
    if warn_below is not None and value < warn_below:
        return (
            "warn",
            (
                f"[QA-WARN] {metric_label} = {value:.2f} is below warn threshold {warn_below}. "
                f"Proceed with caution. {fix_hint}"
            )
        )

    return ("ok", f"{metric_label} = {value:.2f} ✓")


def format_quality_message(level: str, message: str) -> str:
    """
    Format the quality check result as an XML-tagged observation
    suitable for injection into the observer node's LLM prompt.
    """
    if level == "ok":
        return f"<quality_check status='ok'>{message}</quality_check>"
    elif level == "warn":
        return f"<quality_check status='warn'>{message}</quality_check>"
    else:
        return f"<quality_check status='fail'>{message}</quality_check>"
