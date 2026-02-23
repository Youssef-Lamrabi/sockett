#!/usr/bin/env python3

import json
import requests
import time
import PyPDF2
from pathlib import Path
from datetime import datetime

API_URL = "http://10.52.88.105:11434/api/generate"

# --- MODIFIED CALL MODEL FUNCTION FOR STREAMING API ---
def call_model(system, user):
    # Combine system and user prompt for the single 'prompt' field
    full_prompt = f"System Instruction: {system}\n\nUser Query: {user}"
    
    # 1. Use stream=True to enable streaming response handling and replace messages array with prompt
    r = requests.post(API_URL, json={
        "model": "gpt-oss:20b",
        "prompt": full_prompt, 
        "temperature": 0.7,
        "max_tokens": 2000
    }, timeout=60, stream=True)
    
    r.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

    final_response = []
    
    # 2. Iterate through the stream line by line
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
                # Log other errors, but continue processing the stream
                print(f"[STREAM ERROR]: Failed to process chunk: {e}")
                continue
                
    if not final_response:
        # If the stream finished but returned nothing, raise an error
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
    q_response = call_model(
        "You are a computational agent that responds ONLY with the requested JSON object. Do not include any explanatory text, preamble, comments, or markdown formatting (like ```json) outside of the JSON block.",
        f"Generate 5 broad questions about this paper.\n\nTitle: {title}\n\nContent:\n{text[:8000]}\n\nReturn JSON: {{\"questions\": [\"q1\", \"q2\", ...]}}"
    )
    
    try:
        # TIGHTER JSON PARSING LOGIC FOR STAGE 1
        # Isolate the JSON string, handling potential pre- or post-amble text.
        json_string = q_response[q_response.find('{'):q_response.rfind('}')+1]
        
        # Strip control characters and newlines
        questions_dict = json.loads(json_string.strip())
        
        questions = questions_dict['questions']
        questions = [q.strip().strip('"\'') for q in questions if '?' in q and len(q) > 20][:5]
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
            ans = call_model(
                "You are an expert researcher. Answer from papers.",
                f"Answer based on the paper.\n\nTitle: {title}\n\nContent:\n{text[:8000]}\n\nQuestion: {q}\n\nAnswer:"
            )
            if len(ans) > 50:
                initial.append({"question": q, "answer": ans})
                print("✓")
            else:
                print("✗ (Answer too short)")
        except Exception as e:
            print(f"✗ (Call failed: {e})")
        time.sleep(0.5)
    
    print(f"  Stage 1: {len(initial)} pairs")
    
    # Stage 2: Follow-up questions
    print(" Stage 2: Follow-up questions...")
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
            followup = call_model(
                "Generate a deeper follow-up question.",
                f"Based on this Q&A, ask ONE technical follow-up.\n\nQ: {qa['question']}\nA: {qa['answer']}\n\nFollow-up:"
            )
            followup = followup.strip().strip('"\'')
            if '?' not in followup:
                followup += '?'
            print("✓")
            
            print(f"    → Answering...", end=' ')
            ans = call_model(
                "Provide detailed technical answers.",
                f"Answer with technical depth.\n\nTitle: {title}\n\nContent:\n{text[:8000]}\n\nContext:\nQ: {qa['question']}\nA: {qa['answer']}\n\nFollow-up: {followup}\n\nAnswer:"
            )
            
            if len(ans) > 50:
                all_pairs.append({
                    "question": followup,
                    "answer": ans,
                    "answer_length": len(ans),
                    "type": "follow-up"
                })
                print("✓")
            else:
                print("✗ (Answer too short)")
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
    
    current_dir = Path(".")
    papers_dir = Path("./pdfs_batch1")
    
    pdfs = list(current_dir.glob("*.pdf"))
    
    if not pdfs and papers_dir.exists():
        pdfs = list(papers_dir.glob("*.pdf"))
    
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
    