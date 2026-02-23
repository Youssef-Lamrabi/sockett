import numpy as np
from typing import List, Tuple, Dict
from sentence_transformers import SentenceTransformer, util


# Load model once at import time
model = SentenceTransformer("all-MiniLM-L6-v2")

# Categories with example sentences
CATEGORY_EXAMPLES: Dict[str, List[str]] = {
    "taxonomy": [
        "taxonomic profiling with Kraken2",
        "we used MetaPhlAn for microbial classification",
        "Bracken abundance estimation",
        "16s or shotgun based taxonomic assignments",
    ],
    "assembly": [
        "we assembled contigs using MEGAHIT",
        "SPAdes was used for assembly",
        "contig assembly from short reads",
        "de novo metagenome assembly",
    ],
    "binning": [
        "metagenomic binning was performed",
        "MAG recovery using MetaBAT2",
        "bins were constructed using MaxBin2",
        "genome binning workflow",
    ],
    "genome_resolved": [
        "genome-resolved metagenomics",
        "we recovered hundreds of genomes",
        "high-quality MAG reconstruction",
        "dereplication of MAGs",
    ],
    "functional_annotation": [
        "functional annotation using eggNOG",
        "KEGG pathway reconstruction",
        "HUMAnN3 functional profiling",
        "Prokka annotation of MAGs",
    ],
    "qc": [
        "quality control using FastQC",
        "adapter trimming with Trimmomatic",
        "reads were cleaned using Cutadapt",
        "quality filtering pipeline",
    ],
    "shotgun": [
        "shotgun metagenomic sequencing",
        "whole-genome shotgun approach",
        "deep shotgun sequencing was applied",
        "unassembled reads were processed",
    ],
    "clinical_microbiome": [
        "clinical cohort analysis",
        "samples from patients were collected",
        "association with disease states",
        "microbiome signatures in human subjects",
    ],
    "cancer_microbiome": [
        "microbiome and cancer interactions",
        "oncology cohort microbiome study",
        "microbiome profiles in cancer patients",
        "cancer-associated microbiome shifts",
    ],
    "pediatric_microbiome": [
        "pediatric microbiome study",
        "children cohort microbiome analysis",
        "microbiota in infants or youth",
        "microbiome maturation in childhood",
    ],
    "differential_abundance": [
        "differential abundance using LEfSe",
        "DESeq2 differential analysis",
        "group comparisons in microbial abundance",
        "statistical test for microbiome differences",
    ],
    "benchmarking": [
        "benchmarking metagenomics tools",
        "performance comparison of classifiers",
        "evaluation of sequencing pipelines",
        "benchmark datasets were used",
    ],
    "machine_learning": [
        "machine learning model was trained",
        "random forest classification",
        "deep learning for microbiome prediction",
        "ML applied to metagenome data",
    ],
    "review": [
        "this is a review article",
        "we summarize recent advances",
        "overview of metagenomics research",
        "comprehensive review of tools",
    ],
}

# Pre-encode example sentences
CATEGORY_EMBS = {
    label: model.encode(examples, convert_to_tensor=True)
    for label, examples in CATEGORY_EXAMPLES.items()
}

# Keyword fallback dictionary
KEYWORDS: Dict[str, List[str]] = {
    "taxonomy": ["kraken", "metaphlan", "bracken", "classification"],
    "assembly": ["assembly", "contig", "megahit", "spades"],
    "binning": ["binning", "metabat", "maxbin", "mag recovery"],
    "functional_annotation": ["eggnog", "prokka", "kegg", "humann"],
    "qc": ["fastqc", "cutadapt", "trimmomatic", "quality control"],
    "shotgun": ["shotgun", "wgs", "whole genome"],
    "differential_abundance": ["lefse", "deseq2", "differential abundance"],
    "machine_learning": ["machine learning", "deep learning", "random forest"],
    "review": ["review", "overview", "summary"],
    "cancer_microbiome": ["cancer", "tumor", "oncology"],
    "pediatric_microbiome": ["children", "pediatric", "infant"],
    "clinical_microbiome": ["cohort", "patient", "clinical"],
}

# MAIN CLASSIFIER FUNCTION
def classify_semantic(
    text: str,
    threshold: float = 0.42
) -> List[Tuple[str, float]]:
    """
    Multi-label semantic classifier for metagenomics publications.

    Args:
        text: abstract/title input
        threshold: cosine similarity cutoff for neural classification

    Returns:
        List of (label, confidence) sorted descending by confidence.
    """

    if not text or not text.strip():
        return []

    text_emb = model.encode(text, convert_to_tensor=True)
    results: Dict[str, float] = {}

    # Neural similarity using Sentence-BERT
    for label, emb_matrix in CATEGORY_EMBS.items():
        sim = util.cos_sim(text_emb, emb_matrix).max().item()
        if sim >= threshold:
            results[label] = sim
    
    # Keyword fallback for robustness
    lowered = text.lower()
    for label, kw_list in KEYWORDS.items():
        if any(keyword in lowered for keyword in kw_list):
            # Ensure fallback scores do not overwrite higher neural scores
            results[label] = max(results.get(label, 0), 0.50)

    
    # Sort labels by descending confidence
    return sorted(results.items(), key=lambda x: x[1], reverse=True)
