import json
import requests
import time
import PyPDF2
from pathlib import Path
from datetime import datetime


# ===== CONSTANT =====
API_URL = "http://10.52.88.105:11434/api/generate"
CATEGORIES = {
    "pipeline_design": [
        "pipeline", "workflow", "snakemake", "nextflow", "docker", "conda"
    ],
    "sequencing": [
        "sequencing", "fastq", "reads", "illumina", "16s", "rnaseq"
    ],
    "alignment": [
        "alignment", "bam", "sam", "bowtie", "bwa"
    ],
    "taxonomy": [
        "taxonomy", "kraken", "bracken", "metaphlan"
    ],
    "annotation": [
        "annotation", "kegg", "pathways", "clusterprofiler",
        "annotationdbi", "annotationhub", "ensembl", "ensemblvep"
    ],
    "quantification": [
        "counts", "featurecounts", "normalization", "filtering"
    ],
    "visualization": [
        "visualization", "ggtree", "msa", "phyloseq"
    ],
    "metagenomics": [
        "metagenomics", "microbiome", "dada2",
        "curatedmetagenomicdata", "metamsdata"
    ],
    "genomics_infra": [
        "genomicranges", "genomicfeatures", "iranges",
        "genomicfiles", "genomicalignments", "biostrings"
    ]
}


# ===== UTILS =====
def validate_category(question, category):
    text = question.lower()
    for kw in CATEGORIES.get(category, []):
        if kw in text:
            return category
    return "other"

def classify_answer_style(ans):
    if any(k in ans.lower() for k in ["assumption", "interpret", "suggests that"]):
        return "interpretive"
    if len(ans) < 300:
        return "extractive"
    return "abstractive"

def chunk_text(text, chunk_size=2500, overlap=300):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def retrieve_chunks(question, chunks, top_k=3):
    q_words = set(question.lower().split())
    scored = []
    for c in chunks:
        score = sum(1 for w in q_words if w in c.lower())
        scored.append((score, c))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[:top_k]]


# ===== MAIN FUNCTIONS =====
def call_model(system, user):
    full_prompt = f"System Instruction: {system}\n\nUser Query: {user}"
    r = requests.post(
        API_URL, 
        json={
            "model": "gpt-oss:20b",
            "prompt": full_prompt, 
            "temperature": 0.7,
            "max_tokens": 2000
        }, 
        timeout=60, 
        stream=True
    )
    r.raise_for_status() 

    final_response = []
    for line in r.iter_lines():
        if line:
            try:
                # Decode the line and strip common prefixes/suffixes for clean JSON
                line_str = line.decode('utf-8').strip()
                if line_str.startswith('data:'):
                    line_str = line_str[5:].strip()
                
                # Check for an actual JSON object in the stream chunk
                if line_str:
                    chunk = json.loads(line_str)

                    # 3. Concatenate the response tokens/text
                    if 'response' in chunk:
                        final_response.append(chunk['response'])
                        
                    # Stop if the 'done' flag is present (end of stream)
                    if chunk.get('done'):
                        break
                        
            except json.JSONDecodeError:
                # Ignore lines that aren't valid JSON (e.g., streaming markers, newlines)
                continue
            except Exception as e:
                print(f"[STREAM ERROR]: Failed to process chunk: {e}")
                continue
                
    if not final_response:
        raise ValueError("API stream completed but returned an empty response.")
    return "".join(final_response).strip()

def get_pdf_text(path):
    with open(path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        title = reader.metadata.get('/Title', 'Unknown') if reader.metadata else 'Unknown'
        return ' '.join(text.split()), title, len(reader.pages)

def process_paper(pdf_path):
    name = Path(pdf_path).stem
    print(f"\n{'='*60}\n{name}\n{'='*60}")
    
    print("📄 Extracting text...")
    text, title, pages = get_pdf_text(pdf_path)
    print(f"✓ {len(text)} chars, {pages} pages")
    
    # Stage 1: Get initial Q&A
    print("📝 Stage 1: Initial Q&A pairs...")
    initial = []
    
    # --- STAGE 1 CALL: ENFORCE JSON AND CALL NEW FUNCTION ---
    # q_response = call_model(
    #     "You are a computational agent that responds ONLY with the requested JSON object. Do not include any explanatory text, preamble, comments, or markdown formatting (like ```json) outside of the JSON block.",
    #     f"Generate 5 broad questions about this paper.\n\nTitle: {title}\n\nContent:\n{text[:8000]}\n\nReturn JSON: {{\"questions\": [\"q1\", \"q2\", ...]}}"
    # )
    q_response = call_model(
    system="""You generate high-quality TRAINING QUESTIONS for an LLM.
The questions must be:
- General and reusable (not about this paper)
- Answerable using the provided content
- Technically precise
- NOT referencing 'this paper', 'the authors', or 'the study'

Return ONLY valid JSON.
""",
    user=f"""
From the domain knowledge contained in the text below, generate 5 GENERAL
technical questions suitable for training a scientific Q&A model.

Each question must:
- Be answerable from the text
- Be framed as domain knowledge
- NOT mention the paper explicitly
- Each question should be different and cover differents part

For each question, assign ONE category from this list:
{list(CATEGORIES.keys())}

Return JSON exactly in this format:
{{
  "questions": [
    {{
      "question": "...",
      "category": "pipeline_design"
    }}
  ]
}}

CONTENT:
{text[:8000]}
"""
)

    
    try:
        # TIGHTER JSON PARSING LOGIC FOR STAGE 1
        # Isolate the JSON string, handling potential pre- or post-amble text.
        json_string = q_response[q_response.find('{'):q_response.rfind('}')+1]
        
        # Strip control characters and newlines
        questions_dict = json.loads(json_string.strip())
        
        # questions = questions_dict['questions']
        # questions = [q.strip().strip('"\'') for q in questions if '?' in q and len(q) > 20][:5]
        questions = questions_dict["questions"]
        questions = [
            q for q in questions
            if len(q["question"]) > 25 and "paper" not in q["question"].lower()
        ]

    except Exception as e:
        # for debugging when JSON parsing fails
        print(f"\n[PARSING ERROR (Stage 1)]: {e} on response:\n{q_response[:200]}...") 
        # Attempt to salvage questions by splitting lines (old fallback)
        questions = [q.strip() for q in q_response.split('\n') if '?' in q and len(q) > 20][:5]

    
    print(f"  ✓ {len(questions)} questions")
    
    # rest of the code is unchanged

    for i, q in enumerate(questions, 1):
        print(f"  → {i}/{len(questions)}...", end=' ')
        try:
            # ans = call_model(
            #     "You are an expert researcher. Answer from papers.",
            #     f"Answer based on the paper.\n\nTitle: {title}\n\nContent:\n{text[:8000]}\n\nQuestion: {q}\n\nAnswer:"
            # )
            
            chunks = chunk_text(text, chunk_size=2500, overlap=300)
            relevant_chunks = retrieve_chunks(q["question"], chunks, top_k=3)
            
            ans = call_model(
    system="""You answer ONLY using the provided text.
Do NOT:
- Add external knowledge
- Invent benchmarks, statistics, or tools
- Mention this paper explicitly

If the answer is not present, say:
"Not specified in the provided text."
""",
    user=f"""
QUESTION:
{q["question"]}

CONTENT:
{' '.join(relevant_chunks)}

ANSWER:
"""
)
            initial.append({
                "question": q["question"],
                "category": q["category"],
                "answer": ans
            })
            print("✓")
            
            # if len(ans) > 50:
            #     # initial.append({"question": q, "answer": ans})
            #     initial.append({
            #         "question": q["question"],
            #         "category": q["category"],
            #         "answer": ans
            #     })
            #     print("✓")
            # else:
            #     print("✗ (Answer too short)")
        except Exception as e:
            print(f"✗ (Call failed: {e})")
        time.sleep(0.5)
    
    print(f"Stage 1: {len(initial)} pairs")
    
    # Stage 2: Follow-up questions
    print("Stage 2: Follow-up questions...")
    all_pairs = []
    
    for i, qa in enumerate(initial, 1):
        all_pairs.append({
            "question": qa["question"],
            "answer": qa["answer"],
            "answer_length": len(qa["answer"]),
            "type": "broad"
        })
        
        print(f"  → Follow-up {i}...", end=' ')
        try:
            # followup = call_model(
            #     "Generate a deeper follow-up question.",
            #     f"Based on this Q&A, ask ONE technical follow-up.\n\nQ: {qa['question']}\nA: {qa['answer']}\n\nFollow-up:"
            # )
            followup = call_model(
    system="""You rewrite questions for LLM fine-tuning.

Your task:
- Refine the given question to be MORE precise and constrained
- Keep the SAME intent
- Avoid open-ended phrasing
- Make it answerable with a short, factual response
- Do NOT introduce new topics
- Do NOT mention papers, studies, or authors

Return ONLY the rewritten question.
""",
    user=f"""
ORIGINAL QUESTION:
{qa["question"]}

ORIGINAL ANSWER:
{qa["answer"]}

Rewrite the question to be:
- More specific
- More technical
- Easier to answer clearly
"""
)
            followup = followup.strip().strip('"\'')
            if '?' not in followup:
                followup += '?'
            print("✓")
            
            print(f"    → Answering...", end=' ')
            # ans = call_model(
            #     "Provide detailed technical answers.",
            #     f"Answer with technical depth.\n\nTitle: {title}\n\nContent:\n{text[:8000]}\n\nContext:\nQ: {qa['question']}\nA: {qa['answer']}\n\nFollow-up: {followup}\n\nAnswer:"
            # )
            ans = call_model(
    system="""You generate answers for LLM fine-tuning.

Rules:
- Answer ONLY the question
- Use concise, factual language
- Do NOT mention papers, authors, or studies
- Do NOT reference the provided text explicitly
- Avoid phrases like "this work shows" or "the paper describes"
- Prefer short paragraphs or bullet points
- Maximum length: ~150 words

If the information is missing, respond exactly:
"Not specified in the provided text."
""",
    user=f"""
QUESTION:
{followup}

CONTENT:
{' '.join(relevant_chunks)}

OLD ANSWER:
{qa["answer"]}

ANSWER:
"""
)

            all_pairs.append({
                "question": followup,
                "category": q["category"],
                "answer": ans,
                "answer_length": len(ans),
                "type": "broad",
                "source": Path(pdf_path).name
            })
            
            # if len(ans) > 50:
            #     # all_pairs.append({
            #     #     "question": followup,
            #     #     "answer": ans,
            #     #     "answer_length": len(ans),
            #     #     "type": "follow-up"
            #     # })
            #     all_pairs.append({
            #         "question": followup,
            #         "category": q["category"],
            #         "answer": ans,
            #         "answer_length": len(ans),
            #         "type": "broad",
            #         "source": Path(pdf_path).name
            #     })

            #     print("✓")
            # else:
            #     print("✗ (Answer too short)")
        except Exception as e:
            print(f"✗ (Call failed: {e})")
        
        time.sleep(0.5)
        
        if len(all_pairs) >= 10:
            break
    
    print(f"  ✓ Stage 2: {len(all_pairs)} total pairs")
    print(f"    • {sum(1 for qa in all_pairs if qa['type'] == 'broad')} broad")
    print(f"    • {sum(1 for qa in all_pairs if qa['type'] == 'follow-up')} follow-up")
    
    result = {
        "metadata": {
            "pdf_filename": Path(pdf_path).name,
            "paper_title": title,
            "num_pages": pages,
            "num_qa_pairs": len(all_pairs),
            "processing_date": datetime.now().isoformat(),
            "method": "Two-Stage"
        },
        "qa_pairs": all_pairs,
        "statistics": {
            "avg_question_length": sum(len(qa['question']) for qa in all_pairs) / len(all_pairs),
            "avg_answer_length": sum(qa['answer_length'] for qa in all_pairs) / len(all_pairs),
            "broad_questions": sum(1 for qa in all_pairs if qa['type'] == 'broad'),
            "followup_questions": sum(1 for qa in all_pairs if qa['type'] == 'follow-up')
        }
    }
    
    output = Path("results") / f"{name}.json"
    output.parent.mkdir(exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f" {len(all_pairs)} pairs → {output}")
    return result

def get_pdf_text(path):
    # No Change
    with open(path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        title = reader.metadata.get('/Title', 'Unknown') if reader.metadata else 'Unknown'
        return ' '.join(text.split()), title, len(reader.pages)

def main():
    print("\n" + "="*60)
    print("Q&A GENERATOR - TWO-STAGE METHOD")
    print("="*60 + "\n")
    
    # current_dir = Path(".")
    papers_dir = Path("./pdfs_batch1")
    pdfs = list(papers_dir.glob("*.pdf"))
    # if not pdfs and papers_dir.exists():
    #     pdfs = list(papers_dir.glob("*.pdf"))
    pdfs = list(set(pdfs))
    # pdfs = pdfs[:2]

    if not pdfs:
        print(" No PDF files found!")
        print("\nPut PDFs in current folder or create papers/ folder:")
        print("  mkdir papers")
        print("  cp your_pdfs/*.pdf papers/")
        return
    
    print(f"Found {len(pdfs)} PDF file(s)\n")
    
    results = []
    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{len(pdfs)}]")
        try:
            result = process_paper(pdf)
            results.append(result)
        except Exception as e:
            print(f" Error: {e}")
        time.sleep(2)
    
    print(f"\n{'='*60}")
    print("COMPLETE")
    print(f"{'='*60}")
    print(f" Processed: {len(results)}/{len(pdfs)}")
    print(f" Total Q&A: {sum(r['metadata']['num_qa_pairs'] for r in results)}")
    print(f" Results: results/")
    
    Path("results").mkdir(exist_ok=True)
    with open("results/_summary.json", 'w') as f:
        json.dump({
            "total_papers": len(pdfs),
            "successful": len(results),
            "total_qa_pairs": sum(r['metadata']['num_qa_pairs'] for r in results),
            "date": datetime.now().isoformat()
        }, f, indent=2)
    
    print(f"\n Done! Check results/ folder\n")

if __name__ == "__main__":
    main()
    