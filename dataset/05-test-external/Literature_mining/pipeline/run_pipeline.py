import os
print("HOME DIR SEEN BY PIPELINE:", os.path.expanduser("~"))
print("CONFIG EXISTS:", os.path.exists(os.path.expanduser("~/.pybliometrics/config.ini")))

import sys
import os
import json
import logging
from datetime import datetime
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
os.makedirs("../output", exist_ok=True)

from data_sources.ingestion_manager import ingest_all_sources
from classifiers.semantic_classifier import classify_semantic
from extractors.tool_extractor import (
    extract_tools,
    extract_mag_count,
    extract_assembly_count
)

# LOGGING
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# SEMANTIC LABEL NORMALIZATION
def format_semantic_labels(raw):
    """Convert [('assembly', 0.77), ('shotgun', 0.61)] → 'assembly, shotgun'."""
    if not raw:
        return ""
    return ", ".join([label for label, score in raw])

# TOOL CATEGORY DICTIONARY
TOOL_CATEGORIES = {
    "assembly": ["megahit", "spades", "metaspades", "idba-ud", "velvet", "ray", "abyss"],
    "mapping": ["bowtie2", "bwa", "minimap2", "hisat2", "star"],
    "binning": ["metabat", "metabat2", "maxbin", "maxbin2", "concoct", "vamb"],
    "profiling": ["metaphlan", "humann", "bracken", "kraken2", "kaiju", "centrifuge"],
    "taxonomy": ["qiime", "qiime2", "mothur", "rdp classifier"],
    "qc": ["fastqc", "trimmomatic", "cutadapt", "fastp", "bbduk"],
    "alignment": ["blast", "diamond", "hmmer", "clustal", "mafft"]
}

def normalize_tool_name(t):
    return t.lower().strip()

def categorize_tools(tools_list):
    """
    tools_list = ['Bowtie2', 'FastQC', 'MetaBAT2']
    returns a dict with category columns:
       assembly_tools, mapping_tools, qc_tools, binning_tools, ...
    """
    categorized = {cat + "_tools": [] for cat in TOOL_CATEGORIES.keys()}
    categorized["other_tools"] = []

    for tool in tools_list:
        t_norm = normalize_tool_name(tool)
        placed = False

        for category, toolnames in TOOL_CATEGORIES.items():
            if t_norm in toolnames:
                categorized[category + "_tools"].append(tool)
                placed = True
                break

        if not placed:
            categorized["other_tools"].append(tool)

    # Convert lists → comma-separated strings for Excel
    for key in categorized:
        categorized[key] = ", ".join(categorized[key]) if categorized[key] else ""

    return categorized

# PROCESS A SINGLE PAPER
def process_paper(paper: dict) -> dict:
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    combined = f"{title} {abstract}".strip()

    # FIXED: PubMed → keep pmid AND doi
    pmid = paper.get("pmid") or None
    doi = paper.get("doi") or None

    # FIXED: UID = PMID if available, else DOI
    uid = pmid if pmid else doi

    # Semantic classification
    semantic_raw = classify_semantic(combined)
    semantic_labels = format_semantic_labels(semantic_raw)

    # Tool extraction
    tools = extract_tools(combined)
    tool_categories = categorize_tools(tools)

    # MAG + assembly counts
    mag = extract_mag_count(combined)
    asm = extract_assembly_count(combined)

    # Base output row
    record = {
        "uid": uid,
        "pmid": pmid,              
        "doi": doi,                
        "source": paper.get("source", ""),
        "title": title,
        "abstract": abstract,

        "semantic_category": semantic_labels,
        "semantic_scores_raw": semantic_raw,
        "tools_detected_raw": tools,

        "mag_count": mag or 0,
        "assembly_count": asm or 0,
    }

    # Add category columns
    record.update(tool_categories)

    return record



# MAIN PIPELINE RUNNER
def run_pipeline(
    query="metagenomics AND shotgun AND annotation AND bioinformatics",
    dimensions_token=None,
    output_prefix="deep_metagenomics"
):
    logging.info("Starting literature mining pipeline...")
    logging.info(f"Query: {query}")

    # Get data
    papers = ingest_all_sources(
        query, 
        dimensions_token=dimensions_token, 
        max_pubmed=1000, 
        include=['PubMed']
    )
    logging.info(f"Total papers retrieved: {len(papers)}")

    # Process metadata + NLP
    processed = [process_paper(p) for p in papers]

    # DataFrame assembly
    df = pd.DataFrame(processed)

    # Save
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    excel_path = f"../output/{output_prefix}_{timestamp}.xlsx"
    json_path = f"../output/{output_prefix}_{timestamp}.json"
    df.to_excel(excel_path, index=False)
    print(f"Saved Excel file to {excel_path}")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2, ensure_ascii=False)

    logging.info(f"Excel saved → {excel_path}")
    logging.info(f"JSON saved  → {json_path}")
    logging.info("Pipeline completed successfully.")

def run_pipeline2(
    query="metagenomics AND shotgun AND annotation AND bioinformatics",
    dimensions_token=None,
    output_prefix="deep_metagenomics"
):
    logging.info("Starting literature mining pipeline...")
    logging.info(f"Query: {query}")

    # Get data
    papers = ingest_all_sources(
        query, 
        dimensions_token=dimensions_token, 
        max_pubmed=1000, 
        include=['Scopus']
    )
    logging.info(f"Total papers retrieved: {len(papers)}")

    # Process metadata + NLP
    processed = [process_paper(p) for p in papers]

    # DataFrame assembly
    df = pd.DataFrame(processed)

    # Save
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    excel_path = f"../output/{output_prefix}_{timestamp}.xlsx"
    json_path = f"../output/{output_prefix}_{timestamp}.json"
    df.to_excel(excel_path, index=False)
    print(f"Saved Excel file to {excel_path}")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2, ensure_ascii=False)

    logging.info(f"Excel saved → {excel_path}")
    logging.info(f"JSON saved  → {json_path}")
    logging.info("Pipeline completed successfully.")



# CLI
if __name__ == "__main__":
    run_pipeline()
    run_pipeline2()