import json
import time
import re
import requests
from pathlib import Path
from datetime import datetime
import PyPDF2


# =========================
# CONFIG
# =========================
API_URL = "http://10.52.88.105:11434/api/generate"
MODEL = "gpt-oss:20b"
OUTPUT_DIR = Path("dataset_output")
OUTPUT_DIR.mkdir(exist_ok=True)
MAX_CHARS_PER_SECTION = 12000
TEMPERATURE = 0.3
CATEGORIES = {
    "pipeline_design": ["pipeline", "workflow", "protocol", "steps"],
    "sequencing": ["sequencing", "fastq", "illumina", "16s", "shotgun"],
    "taxonomy": ["taxonomy", "otu", "asv", "kraken", "bracken"],
    "annotation": ["annotation", "kegg", "pathway", "gene"],
    "quantification": ["abundance", "counts", "normalization"],
    "visualization": ["plot", "ordination", "pcoa", "heatmap"],
    "metagenomics": ["metagenomics", "microbiome", "microbial"],
    "genomics_infra": ["genomic", "alignment", "bam", "sam"]
}


# =========================
# HELPER
# =========================
def infer_category(text):
    t = text.lower()
    for cat, kws in CATEGORIES.items():
        if any(k in t for k in kws):
            return cat
    return "other"


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
# PDF TEXT EXTRACTION
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
SECTION_MARKERS = {
    "introduction": ["introduction", "background", "main"],
    "methods": ["methods", "materials", "methodology"],
    "results": ["results"],
    "discussion": ["discussion"],
    "conclusion": ["conclusion"]
}

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
# CLAIM EXTRACTION
# =========================
def extract_claims(section_text, section_name):
    system = f"""
You extract ATOMIC SCIENTIFIC CLAIMS from {section_name} text.

Rules:
- Each claim must be a single verifiable statement
- The claim should come from the paper and be details enought to build latter a question and answer from it
- No references to papers or authors
- No citations
- No speculation
- No multi-sentence claims

Return JSON ONLY:
{{ "claims": ["...", "..."] }}
"""
    user = section_text

    raw = call_llm(system, user)
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return json.loads(raw)["claims"]


# =========================
# ROUTING CLAIMS
# =========================
def route_claim(claim):
    c = claim.lower()
    if any(x in c for x in ["defined as", "refers to", "is a process", "is the process"]):
        return "factual"
    if any(x in c for x in ["because", "depends on", "affects", "leads to"]):
        return "conceptual"
    return "ignore"


# =========================
# GENERATORS
# =========================
def generate_factual_qa(claim, evidence):
    system = """
Convert the claim into ONE factual Q&A pair.

Rules:
- No interpretation
- No paper references
- JSON only

Format:
{ "type": "factual", "question": "...", "answer": "..." }
"""
    qa = json.loads(call_llm(system, f"""CLAIM:{claim} \nEVIDENCE: {evidence}"""))
    qa["category"] = infer_category(qa["question"])
    return qa


def generate_conceptual_qa(claim, evidence):
    system = """
Convert the claim into ONE conceptual Q&A pair.

Rules:
- Question asks WHY or HOW
- Answer explains causality
- No paper references
- JSON only

Format:
{ "type": "conceptual", "question": "...", "answer": "..." }
"""
    qa = json.loads(call_llm(system, f"""CLAIM:{claim} \nEVIDENCE: {evidence}"""))
    qa["category"] = infer_category(qa["question"])
    return qa

def generate_workflow(methods_text):
    system = """
You extract ONE expert-level scientific workflow.

STRICT RULES (MANDATORY):
- Return EXACTLY the JSON schema below
- steps MUST be an array of STRINGS
- Each step must describe ONE action
- Do NOT number steps (no "1.", "Step 1", etc.)
- constraints MUST be an array of STRINGS
- NO extra fields
- NO prose outside JSON

SCHEMA:
{
  "type": "workflow",
  "instruction": "...",
  "steps": ["...", "..."],
  "constraints": ["...", "..."]
}
"""

    raw = call_llm(system, methods_text, max_tokens=3000)

    # Isolate JSON safely
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)

    # Normalize steps to list[str]
    clean_steps = []
    for s in data.get("steps", []):
        if isinstance(s, str):
            clean_steps.append(s.strip())
        elif isinstance(s, dict) and "description" in s:
            clean_steps.append(str(s["description"]).strip())

    # Normalize constraints to list[str]
    clean_constraints = [str(c).strip() for c in data.get("constraints", [])]

    return {
        "type": "workflow",
        "category": "pipeline_design",
        "instruction": str(data.get("instruction", "")).strip(),
        "steps": clean_steps,
        "constraints": clean_constraints
    }

# =========================
# MAIN PIPELINE
# =========================
def process_paper(pdf_path):
    print(f"\n📄 Processing {pdf_path.name}")

    text, title, pages = extract_pdf_text(pdf_path)
    sections = infer_sections(text)

    dataset = []
    all_claims = []

    for sec in ["introduction", "methods", "discussion"]:
        if sec in sections:
            print(f"  → Extracting claims from {sec}")
            claims = extract_claims(sections[sec], sec)
            all_claims.extend(claims)
            time.sleep(1)

    for claim in all_claims:
        kind = route_claim(claim)
        try:
            if kind == "factual":
                dataset.append(generate_factual_qa(claim, sections[""]))
            elif kind == "conceptual":
                dataset.append(generate_conceptual_qa(claim, sections[""]))
        except Exception:
            continue
        time.sleep(0.5)

    if "methods" in sections:
        try:
            dataset.append(generate_workflow(sections["methods"]))
        except Exception:
            pass

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
    print(f"  ✓ Saved {out_path.name}")

# =========================
# ENTRYPOINT
# =========================

def main():
    print("\n" + "="*60)
    print("Q&A GENERATOR - DATASET-ORIENTED METHOD")
    print("="*60 + "\n")
    stats = {"succes":0};
    
    pdfs = list(Path("pdfs_batch1").glob("*.pdf"))
    pdfs = pdfs[:5]
    if not pdfs:
        print("❌ No PDFs found in ./pdfs_batch1/")
        return

    for i, pdf in enumerate(pdfs):
        print(f"\n[{i}/{len(pdfs)}]")
        try:
            process_paper(pdf)
            stats["succes"] += 1
        except Exception as e:
            print(f"⚠️ Failed {pdf.name}: {e}")

    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")
    print(f" Processed: {stats["succes"]}/{len(pdfs)}")
    
if __name__ == "__main__":
    main()
