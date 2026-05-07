"""
genomeer/src/genomeer/utils/thresholds.py
==========================================
BUG-34: Single source of truth for MIMAG / metagenomics quality thresholds.

Previously these were duplicated across:
  - quality_gate.py     (Observer routing decisions)
  - bio_rag.py          (_QualityThresholdsFetcher, RAG context injection)
  - benchmark.py        (PipelineOutputEval scoring)

Any change to a threshold now only needs to happen here.
"""

from __future__ import annotations
from typing import Dict, Any

# ---------------------------------------------------------------------------
# MIMAG / CAMI standard thresholds
# Keyed by metric name, values follow quality_gate.py convention.
# ---------------------------------------------------------------------------

MIMAG_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "assembly_n50": {
        "good_label":       "> 10,000 bp",
        "acceptable_label": "1,000 – 10,000 bp",
        "poor_label":       "< 500 bp",
        "warn_threshold":   1_000,
        "fail_threshold":   200,
        "unit":             "bp",
        "tool":             "metaSPAdes / MEGAHIT / Flye",
        "interpretation": (
            "N50 > 10 kb indicates a high-quality metagenome assembly suitable for binning. "
            "N50 < 500 bp suggests highly fragmented assembly; consider increasing depth or "
            "switching assembler. Reference: metaSPAdes paper (Nurk et al. 2017, Genome Research)."
        ),
    },
    "mean_completeness": {
        "good_label":       ">= 90%",
        "acceptable_label": "50 – 90%",
        "poor_label":       "< 50%",
        "warn_threshold":   50.0,
        "fail_threshold":   20.0,
        "pass_threshold":   90.0,
        "unit":             "%",
        "tool":             "CheckM2",
        "interpretation": (
            "CheckM2 completeness >= 90% with contamination <= 5% defines a 'high-quality draft MAG' "
            "per MIMAG standards (Bowers et al. 2017, Nature Biotechnology). "
            "Medium quality: >= 50% complete, <= 10% contaminated. "
            "Low quality bins (<50%) are unsuitable for genomic inference."
        ),
    },
    "mean_contamination": {
        "good_label":       "<= 5%",
        "acceptable_label": "5 – 10%",
        "poor_label":       "> 10%",
        "pass_below":       5.0,
        "warn_below":       10.0,
        "fail_above":       10.0,
        "inverted":         True,
        "unit":             "%",
        "tool":             "CheckM2",
        "interpretation": (
            "Contamination >10% suggests chimeric bins or multiple organisms co-binned. "
            "Use DAS_Tool for bin refinement. Re-binning with larger minimum contig size may help."
        ),
        # Alias standard keys for code that expects uniform naming (M-05)
        "fail_threshold":   10.0,   # mirrors fail_above
        "warn_threshold":   5.0,    # mirrors warn_below boundary
        "pass_threshold":   5.0,    # mirrors pass_below
    },
    "classified_pct": {
        "good_label":       "> 60%",
        "acceptable_label": "20 – 60%",
        "poor_label":       "< 5%",
        "warn_threshold":   20.0,
        "fail_threshold":   3.0,
        "pass_threshold":   60.0,
        "unit":             "%",
        "tool":             "Kraken2 / MetaPhlAn4",
        "interpretation": (
            "Very low classification rates (<5%) may indicate: "
            "(1) Novel organisms not in the database; "
            "(2) Wrong database (e.g., bacterial DB on viral metagenome); "
            "(3) Low-quality reads. "
            "MetaPhlAn4 typically classifies fewer reads than Kraken2 but with higher specificity."
        ),
    },
    "q30_rate": {
        "good_label":       "> 80%",
        "acceptable_label": "60 – 80%",
        "poor_label":       "< 40%",
        "warn_threshold":   0.60,   # NOTE: fastp reports as 0.0–1.0 fraction
        "fail_threshold":   0.40,
        "pass_threshold":   0.80,
        "unit":             "(fraction)",
        "tool":             "fastp",
        "interpretation": (
            "Q30 = 0.1% error rate per base. "
            "< 40% Q30 indicates poor library quality and will impair assembly significantly. "
            "Q30 > 80% after trimming is optimal for metagenome assembly."
        ),
    },
    "diversity_shannon": {
        "good_label":       "> 3.0",
        "acceptable_label": "1.5 – 3.0",
        "poor_label":       "< 1.0",
        "warn_threshold":   1.5,
        "fail_threshold":   0.0,
        "pass_threshold":   3.0,
        "unit":             "index",
        "tool":             "vegan R / HUMAnN3",
        "interpretation": (
            "Shannon index < 1.0 indicates very low diversity, typical of dysbiotic gut or "
            "single-species dominated environments. "
            "Shannon > 3.5 is typical of healthy human gut (Turnbaugh et al. 2009, Nature). "
            "Environmental (soil/marine) samples typically show Shannon 4–6."
        ),
    },
    "coverage_depth": {
        "good_label":       "> 10x",
        "acceptable_label": "5 – 10x",
        "poor_label":       "< 5x",
        "warn_threshold":   2.0,
        "fail_threshold":   0.5,
        "unit":             "X",
        "tool":             "samtools / jgi_summarize_bam_contig_depths",
        "interpretation": (
            "MetaBAT2 requires minimum 5x coverage per contig for reliable binning. "
            "< 5x: most contigs will be unbinned. "
            "> 30x: sufficient for complete MAG recovery in most communities. "
            "Coverage is calculated per contig, not per sample."
        ),
    },
    "n_hq_mags": {
        "good_label":       "Depends on community complexity",
        "acceptable_label": "1 – 10 for simple communities",
        "poor_label":       "0 MAGs from >1GB of reads",
        "warn_threshold":   1,
        "fail_threshold":   0,
        "pass_threshold":   1,
        "unit":             "MAGs",
        "tool":             "MetaBAT2 + CheckM2",
        "interpretation": (
            "0 MAGs from sufficient data suggests: low coverage, too-short contigs, or "
            "divergent community. In gut metagenomes, typical studies recover 10–100 MAGs "
            "from 10-20 Gbp data. Reference: Pasolli et al. 2019, Cell (4,930 human gut MAGs)."
        ),
    },
}


def validate_thresholds() -> list[str]:
    """Return list of warnings for any threshold entries missing standard keys."""
    _required = {"unit", "tool"}
    issues = []
    for name, entry in MIMAG_THRESHOLDS.items():
        for k in _required:
            if k not in entry:
                issues.append(f"{name}: missing required key '{k}'")
    return issues
