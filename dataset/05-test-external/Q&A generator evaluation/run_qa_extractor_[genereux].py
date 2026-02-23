import json
import time
import re
import requests
from pathlib import Path
from datetime import datetime
import PyPDF2
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np


# =========================
# CONFIG
# =========================
API_URL = "http://10.52.88.105:11434/api/generate"
MODEL = "gpt-oss:20b"
OUTPUT_DIR = Path("dataset_output")
OUTPUT_DIR.mkdir(exist_ok=True)
MAX_CHARS_PER_SECTION = 12000
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
TOP_K = 3
TEMPERATURE = 0.3
CATEGORIES = {
    "pipeline_design": ["pipeline", "workflow", "protocol", "steps", "orchestration", "snakemake", "nextflow", "cwl", "workflow management"],
    "qc_preprocessing": ["quality control", "qc", "fastqc", "multiqc", "adapter trimming", "quality trimming", "filtering", "low quality reads", "read filtering", "cutadapt", "trimmomatic", "bbduk"],
    "sequencing": ["sequencing", "fastq", "illumina", "nanopore", "pacbio", "16s", "amplicon", "shotgun", "paired-end", "single-end", "library preparation"],
    "host_decontamination": ["host removal", "decontamination", "host filtering", "human contamination", "bowtie2 host", "kneaddata", "bmtool", "bbmap"],
    "alignment": ["alignment", "mapping", "bam", "sam", "bowtie", "bowtie2", "bwa", "minimap2", "reference alignment"],
    "assembly": ["assembly", "contigs", "scaffolds", "de novo assembly", "co-assembly", "megahit", "metaspades", "spades", "idba-ud"],
    "assembly_qc": ["assembly quality", "n50", "l50", "contig length", "assembly statistics", "quast", "metaquast", "checkm"],
    "binning": ["binning", "metagenome bins", "mag", "metagenome-assembled genomes", "metabat", "maxbin", "concoct", "das tool"],
    "bin_qc": ["bin quality", "completeness", "contamination", "checkm", "gtdb-tk", "mag quality"],
    "taxonomy": ["taxonomy", "taxonomic profiling", "otu", "asv", "species abundance", "kraken", "kraken2", "bracken", "metaphlan", "centrifuge", "gtdb"],
    "annotation": ["annotation", "functional annotation", "gene prediction", "orfs", "cds", "kegg", "eggnog", "cog", "pfam", "interpro", "prokka", "dram"],
    "functional_profiling": ["pathway analysis", "functional profiling", "metabolic pathways", "enzyme abundance", "humann", "humann3", "minpath"],
    "quantification": ["abundance", "counts", "normalization", "relative abundance", "coverage", "rpkm", "tpm", "fpkm", "depth"],
    "diversity_analysis": ["alpha diversity", "beta diversity", "shannon", "simpson", "bray curtis", "ordination", "pcoa", "nmds"],
    "statistical_analysis": ["differential abundance", "statistical testing", "significance", "anova", "wilcoxon", "lefse", "deseq2", "aldex2"],
    "visualization": ["visualization", "plot", "heatmap", "barplot", "boxplot", "ordination plot", "phyloseq", "ggplot", "ggtree"],
    "machine_learning": ["machine learning", "deep learning", "classification", "prediction", "random forest", "svm", "neural network"],
    "multiomics": ["multi-omics", "integration", "metabolomics", "proteomics", "transcriptomics", "systems biology"],
    "genomics_infra": ["container", "docker", "singularity", "conda", "environment", "genomic", "reference genome", "versioning", "reproducibility"]
}


SECTION_MARKERS = {
    "introduction": ["introduction", "background"],
    "methods": ["methods", "materials", "methodology"],
    "results": ["results"],
    "discussion": ["discussion"],
    "conclusion": ["conclusion"]
}


# =========================
# HELPERS
# =========================
def infer_category(text):
    t = text.lower()
    for cat, kws in CATEGORIES.items():
        if any(k in t for k in kws):
            return cat
    return "other"

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def clean_answer(ans):
    banned = [
        "according to the",
        "the evidence",
        "this study",
        "this paper",
        "[", "]"
    ]
    for b in banned:
        if b in ans.lower():
            return None
    return ans.strip()


# =========================
# LLM CALL
# =========================
def call_llm(system, user, max_tokens=2000):
    payload = {
        "model": MODEL,
        "prompt": f"SYSTEM:\n{system}\n\nUSER:\n{user}",
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "stream": False
    }
    r = requests.post(API_URL, json=payload, timeout=90)
    r.raise_for_status()
    return r.json()["response"].strip()


# =========================
# PDF EXTRACTION
# =========================
def extract_pdf_text(pdf_path):
    reader = PyPDF2.PdfReader(open(pdf_path, "rb"))
    text = []
    for page in reader.pages:
        if page.extract_text():
            text.append(page.extract_text())
    meta = reader.metadata or {}
    return (
        re.sub(r"\s+", " ", " ".join(text)),
        meta.get("/Title", "Unknown"),
        len(reader.pages),
    )


# =========================
# SECTION INFERENCE
# =========================
def infer_sections(text):
    text_l = text.lower()
    indices = []

    for sec, markers in SECTION_MARKERS.items():
        for m in markers:
            idx = text_l.find(f" {m} ")
            if idx != -1:
                indices.append((idx, sec))

    indices.sort()
    sections = {}

    for i, (start, sec) in enumerate(indices):
        end = indices[i+1][0] if i+1 < len(indices) else len(text)
        sections[sec] = text[start:end][:MAX_CHARS_PER_SECTION]

    return sections


# =========================
# CLAIM EXTRACTION & ROUTING
# =========================
def extract_claims(section_text, section_name):
    system = f"""
You extract ATOMIC SCIENTIFIC CLAIMS from {section_name} text.

Rules:
- Each claim must be a single verifiable statement
- The claim should come from the paper and be detailed enough to help build latter a pair of question and answer
- No references to papers or authors
- No citations
- No speculation
- Extract differents claim if possible for factual or conceptual facts 

Return JSON ONLY:
{{{{ "claims": [{{{{ "text": "...", "type": "factual" }}}}, {{{{ "text": "...", "type": "conceptual" }}}}] }}}}
"""
    raw = call_llm(system, section_text)
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return json.loads(raw)["claims"]

def route_claim(claim):
    c = claim.lower()
    if any(x in c for x in ["defined as", "refers to", "is a process"]):
        return "factual"
    if any(x in c for x in ["because", "depends on", "affects", "leads to"]):
        return "conceptual"
    return "ignore"

# =========================
# VECTOR RETRIEVAL
# =========================
def build_vector_index(chunks):
    vectorizer = TfidfVectorizer(stop_words="english")
    X = vectorizer.fit_transform(chunks)
    return vectorizer, X

def retrieve_top_chunks(query, vectorizer, X, chunks, k=TOP_K):
    q_vec = vectorizer.transform([query])
    sims = cosine_similarity(q_vec, X)[0]
    top_idx = np.argsort(sims)[-k:][::-1]
    return [chunks[i] for i in top_idx]


# =========================
# GENERATORS
# =========================
def generate_factual_qa(claim, evidence):
    system = """
Generate ONE factual Q&A pair.

Rules (STRICT):
- Use ONLY the evidence text verbatim
- Answer must be 1–3 sentences MAX
- DO NOT say:
  "according to the paper"
  "according to the text"
  "the evidence shows"
- DO NOT include citations, numbers in brackets, or years
- DO NOT explain beyond the stated fact
- If missing, say exactly:
  "Not specified in the provided text."

Return JSON ONLY.

Format:
{ "type": "factual", "question": "...", "answer": "..." }
"""
    qa = json.loads(call_llm(system, f"CLAIM:\n{claim}\n\nEVIDENCE:\n{evidence}"))
    qa["category"] = infer_category(qa["question"] + " " + qa["answer"] + " " + claim)
    return qa


def generate_conceptual_qa(claim, evidence):
    system = """
Generate ONE conceptual Q&A pair.

Rules (STRICT):
- Question must start with WHY or HOW
- Answer must be <= 5 sentences
- Answer must explain mechanism or relationship ONLY
- No citations, references, or years
- No phrases like "according to the study"
- No speculation beyond evidence

Return JSON ONLY.

Format:
{ "type": "conceptual", "question": "...", "answer": "..." }
"""
    qa = json.loads(call_llm(system, f"CLAIM:\n{claim}\n\nEVIDENCE:\n{evidence}"))
    qa["category"] = infer_category(qa["question"] + " " + qa["answer"] + " " + claim)
    return qa


def generate_workflow(methods_text, doi):
    system = """
Extract ONE scientific workflow from the provided text.

Definition:
A workflow is an ordered sequence of concrete, actionable steps that describe how a scientific task is performed from start to finish.

Rules (STRICT):
- The instruction MUST be phrased as a scientific question (e.g., "How can ...?")
- Do NOT mention "expert-level", "workflow", or "pipeline" in the instruction
- Steps MUST be a logically ordered array of concise, imperative sentences
- Each step must describe ONE concrete action
- Do NOT number the steps
- Constraints MUST be an array of clear technical or experimental requirements
- Do NOT include explanations, commentary, or background text
- Do NOT invent steps that are not supported by the text
- Use ONLY information present in the provided text
- If no clear workflow is described, return an EMPTY JSON object: {}

Output:
- Return ONLY valid JSON
- No prose, no markdown, no preamble

Schema:
{
  "type": "workflow",
  "instruction": "How can ...?",
  "steps": [
    "...",
    "..."
  ],
  "constraints": [
    "...",
    "..."
  ]
}
"""
    raw = call_llm(system, methods_text, max_tokens=5000)
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)

    return {
        "type": "workflow",
        "category": "pipeline_design",
        "instruction": data.get("instruction", "").strip(),
        "steps": [s.strip() for s in data.get("steps", [])],
        "constraints": [c.strip() for c in data.get("constraints", [])],
        "extracted_from": {
            "doi": doi,
            "info": "In the DOI, '/' has been replaced by '_', but some DOIs originally contain underscores."
        }
    }


# =========================
# MAIN PIPELINE
# =========================
def process_paper(pdf_path):
    print(f"\n📄 Processing {pdf_path.name}")

    # doi = pdf_path.split("/")[-1].split('.pdf')[0]
    doi = pdf_path.stem
    text, title, pages = extract_pdf_text(pdf_path)
    sections = infer_sections(text)

    dataset = []
    for sec in ["introduction", "methods", "discussion"]:
        if sec not in sections:
            continue

        chunks = chunk_text(sections[sec])
        vectorizer, X = build_vector_index(chunks)
        print(f"  → Extracting claims from {sec}")
        claims = extract_claims(sections[sec], sec)

        for claim_dict in claims:
            claim = claim_dict["text"]
            kind = claim_dict["type"]
            # kind = route_claim(claim)
            if kind == "ignore":
                print("[claim type: ignore] - Moving to the next.")
                print("--"*10 + "\n" + claim + "\n" + "--"*10)
                continue

            top_chunks = retrieve_top_chunks(claim, vectorizer, X, chunks)
            evidence = "\n".join(top_chunks)

            try:
                if kind == "factual":
                    qa = generate_factual_qa(claim, evidence)
                    qa["answer"] = clean_answer(qa["answer"])
                    if qa["answer"] is None:
                        continue
                    qa["extracted_from"] = {
                        "evidence": evidence,
                        "claim": claim
                    }
                    dataset.append(qa)
                elif kind == "conceptual":
                    qa = generate_conceptual_qa(claim, evidence)
                    qa["answer"] = clean_answer(qa["answer"])
                    if qa["answer"] is None:
                        continue
                    qa["extracted_from"] = {
                        "evidence": evidence,
                        "claim": claim
                    }
                    dataset.append(qa)
            except Exception:
                continue

            time.sleep(0.5)

    if "methods" in sections:
        dataset.append(generate_workflow(sections["methods"], doi))

    output = {
        "metadata": {
            "source_pdf": pdf_path.name,
            "paper_title": title,
            "num_pages": pages,
            "generated_items": len(dataset),
            "date": datetime.now().isoformat()
        },
        "data": dataset
    }

    out_path = OUTPUT_DIR / f"{pdf_path.stem}.json"
    json.dump(output, open(out_path, "w"), indent=2)
    print(f"✓ Saved {out_path.name}")


# =========================
# ENTRYPOINT
# =========================
def main():
    print("\n" + "="*60)
    print("Q&A GENERATOR - DATASET-ORIENTED METHOD")
    print("="*60 + "\n")
    stats = {"success":0}
    
    pdfs = list(Path("pdfs_batch1").glob("*.pdf"))
    # pdfs = pdfs[:5]
    # pdfs = pdfs[37:]
    pdfs = pdfs[352:]
    if not pdfs:
        print("❌ No PDFs found in ./pdfs_batch1/")
        return

    for i, pdf in enumerate(pdfs):
        print(f"\n[{i}/{len(pdfs)}]")
        try:
            process_paper(pdf)
            stats["success"] += 1
        except Exception as e:
            print(f"⚠️ Failed {pdf.name}: {e}")

    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")
    print(f" Processed: {stats['success']}/{len(pdfs)}")
    
if __name__ == "__main__":
    main()
    