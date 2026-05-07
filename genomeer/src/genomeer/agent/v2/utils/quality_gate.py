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
import logging
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger("genomeer.quality_gate")


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

    # FIX BUG4: run_bracken was iterated in _observer but had no gate → silent ok
    "run_bracken": {
        "metric_key":     "n_species_estimated",
        "metric_label":   "Species estimated by Bracken",
        "warn_threshold": 1,
        "fail_threshold": 0,
        "fail_on_zero":   True,
        "parse_regex":    r"(\d+)\s+species",
        "fix_hint": (
            "Bracken estimated 0 species. Check: "
            "(1) Kraken2 was run successfully before Bracken. "
            "(2) The Bracken database matches the Kraken2 database version. "
            "(3) The read length parameter (-r) matches the actual read length."
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

    # ── Long-Read Polishing ───────────────────────────────────────────────────

    "run_medaka": {
        # Medaka logs QV (Quality Value) of the polished consensus.
        # QV = -10 * log10(error_rate): QV20 = 99% acc, QV30 = 99.9% acc.
        # Medaka typically outputs: "[M::] INFO: mean qv: 28.35" in its stderr.
        "metric_key":     "mean_qv",
        "metric_label":   "Medaka consensus QV (Quality Value)",
        "warn_threshold": 20,   # QV10–20: polishing worked but quality suboptimal
        "fail_threshold": 10,   # QV < 10: polishing failed or reads too noisy
        "warn_below":     20,
        "fail_below":     10,
        "parse_regex":    r"(?:mean\s*qv|consensus\s*qv)[:\s]+([0-9]+(?:\.[0-9]+)?)",
        "fix_hint": (
            "Medaka consensus QV is very low. Check: "
            "(1) Were Racon rounds run before Medaka (1-2 rounds recommended)? "
            "(2) Is the model correct for the flowcell/kit (e.g. r941_min_high_g360)? "
            "(3) Are input reads high-quality ONT reads (mean Q >= Q8)?"
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

    # ── Annotation & Profiling ───────────────────────────────────────────────

    "run_prokka": {
        "metric_key":     "n_genes_predicted",
        "metric_label":   "Number of genes predicted",
        "warn_threshold": 100,
        "fail_threshold": 10,
        "parse_regex":    r"(\d+)\s+genes? predicted",
        "fix_hint": (
            "Prokka predicted very few genes. Check: "
            "(1) Assembly quality (N50, total length). "
            "(2) Correct domain specification (e.g., Archaea vs Bacteria)."
        ),
    },

    "run_diamond": {
        "metric_key":     "hit_rate_pct",
        "metric_label":   "DIAMOND hit rate (%)",
        "warn_threshold": 20.0,
        "fail_threshold": 5.0,
        "parse_regex":    r"hit_rate[\"':,\s]+([0-9.]+)",
        "fix_hint": (
            "DIAMOND found very few functional hits. "
            "Consider using a more sensitive mode (--sensitive or --more-sensitive) "
            "or check if the correct reference database was used."
        ),
    },

    "run_humann3": {
        "metric_key":     "mapped_reads_pct",
        "metric_label":   "% reads mapped to pathways",
        "warn_threshold": 10.0,
        "fail_threshold": 1.0,
        "parse_regex":    r"mapped_reads_pct[\"':,\s]+([0-9.]+)",
        "fix_hint": (
            "HUMAnN3 mapped very few reads to functional pathways. "
            "This may indicate a high proportion of novel or uncharacterized genes."
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

    # ── Viromics ─────────────────────────────────────────────────────────────
    "run_virsorter2": {
        "metric_key":     "n_viral_sequences",
        "metric_label":   "Viral sequences identified",
        "warn_threshold": 1,
        "fail_threshold": 0,
        "fail_on_zero":   True,
        "parse_regex":    r"n_viral_sequences[\"':,\s]+([0-9]+)",
        "fix_hint": (
            "VirSorter2 found 0 viral sequences. Check if the input assembly has contigs > 1500bp "
            "or if the sample is exclusively bacterial/host."
        ),
    },

    "run_checkv": {
        "metric_key":     "mean_completeness",
        "metric_label":   "Mean Viral Completeness (%)",
        "warn_threshold": 50.0,
        "fail_threshold": 10.0,
        "parse_regex":    r"mean_completeness[\"':,\s]+([0-9.]+)",
        "fix_hint": (
            "CheckV completeness is very low. The viral contigs are highly fragmented. "
            "Consider using a different assembler (e.g. metaSPAdes with --rnaviral)."
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
    gate = BIOLOGICAL_GATES.get(tool_name) or BIOLOGICAL_GATES.get(tool_name.lower())
    if gate is None:
        logger.debug(f"No biological gate defined for tool: {tool_name}")
        return ("ok", "")

    # --- FIX 7: Strict check for missing metrics ---
    if result_dict is None and gate.get("metric_key") is not None:
        return ("fail", f"[QA-FAIL] Tool {tool_name} returned no valid JSON metrics. Execution probably failed or parser crashed.")

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

    # BUG-26: q30_rate should be a 0–1 fraction (fastp JSON convention).
    # If the value is > 1.0 the wrapper returned a percentage (0–100) instead.
    # Normalise and log a warning so the gate still fires correctly.
    if value is not None and metric_key == "q30_rate" and value > 1.0:
        logger.warning(
            f"[QA] q30_rate value {value:.2f} looks like a percentage (expected 0–1 fraction). "
            "Dividing by 100 to normalise. Check run_fastp() wrapper."
        )
        value = value / 100.0

    # 2. From stdout via regex
    if value is None and parse_regex and stdout_text:
        m = re.search(parse_regex, stdout_text, re.IGNORECASE)
        if m:
            try:
                value = float(m.group(1))
            except (ValueError, IndexError):
                pass

    # Handle special case for warn/fail below explicit fields
    warn_below = gate.get("warn_below", warn_thresh)
    fail_below = gate.get("fail_below", fail_thresh)

    # ── If we couldn't extract a number, log a warning and return warn ────────
    if value is None:
        msg = f"[QA-WARN] {metric_label}: metric could not be extracted. Manual verification recommended. {fix_hint}"
        logger.warning(f"Quality gate metric extraction failed for {tool_name}: {metric_label}")
        return ("warn", msg)

    # ── Threshold comparisons ─────────────────────────────────────────────

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
