import json
import logging
import os
import argparse
import re
from pathlib import Path
from openai import OpenAI
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ── Config LLM ────────────────────────────────────────────────────────────────
API_BASE   = os.getenv("LLM_API_BASE",   "http://localhost:11434/v1")
API_KEY    = os.getenv("LLM_API_KEY",    "ollama")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-oss:20b")

INPUT_FILE      = "dataset_filtered.jsonl"
OUTPUT_FILE     = "dataset_llm.jsonl"
TOP_N_RESPONSES = 3


# ── Nettoyage texte ───────────────────────────────────────────────────────────
IMAGE_MD        = re.compile(r'!\[.*?\]\(.*?\)')
ATTACHMENT_LINK = re.compile(r'\[.*?\]\(https://github\.com/user-attachments/.*?\)')
QUOTE_BLOCK     = re.compile(r'^>.*$', re.MULTILINE)
HTML_TAGS       = re.compile(r'<[^>]+>')
GITHUB_NOISE    = re.compile(
    r'###[^\n]*\n+_No response_\s*',
    re.MULTILINE
)

def clean_body(text):
    if not text:
        return ''
    text = ATTACHMENT_LINK.sub('', text)
    text = IMAGE_MD.sub('', text)
    text = HTML_TAGS.sub('', text)
    text = QUOTE_BLOCK.sub('', text)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = GITHUB_NOISE.sub('', text)
    text = re.sub(r'\t', '    ', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── Prompt ────────────────────────────────────────────────────────────────────
def build_prompt(issue_text, responses, labels, tool):
    responses_block = ""
    for i, r in enumerate(responses, 1):
        role  = r.get('author_association', 'NONE')
        score = r.get('score_calculer', 0)
        body  = r.get('reponse', '')
        responses_block += f"RESPONSE {i} (role: {role}, score: {score}/100):\n{body}\n\n"

    tags_str = ', '.join(labels) if labels else 'none'

    return f"""You are a STRICT data-cleaning and data-structuring model for bioinformatics training data.
You must ONLY use information explicitly present in the ISSUE and RESPONSES below.
You MUST NOT add, infer, explain, guess, or hallucinate ANY extra information.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — DOMAIN CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If the issue is NOT about bioinformatics, genomics, metagenomics, sequencing,
computational biology, bioinformatics tools, or pipelines:
→ output ONLY: {{"valid": false, "reason": "out_of_domain"}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — QUALITY CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
If ALL responses are empty, off-topic, or do not address the issue at all:
→ output ONLY: {{"valid": false, "reason": "low_quality"}}



━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — FIELD CONSTRUCTION (STRICT RULES, NO INVENTION)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"instruction":
  - ONE clear sentence describing the task.
  - Derived ONLY from the issue title and description.
  - NO personal info, NO GitHub noise, NO "the user wants".
  - Example good: "Fix mmap memory allocation failure in MetaSPAdes."
  - Example bad:  "Run PREFIX=... spades_compile.sh" ← this is a solution, not an instruction

"input":
  CASE A — Issue contains technical details (errors, logs, commands, parameters, files):
    → Extract and include EXACTLY:
      • Tool name + version (if present)
      • OS / environment / installation method (if present)
      • Error messages, stack traces, exit codes (word for word)
      • Commands or scripts mentioned (word for word)
      • File paths, parameter values, configuration values (if present)
      • Log file contents if attached (word for word)
    → Format as clean structured text, NOT full prose paragraphs.
    → REMOVE: personal paths like /home/username/, email addresses, GitHub usernames.
    → REMOVE: GitHub template noise (### sections with _No response_).

  CASE B — Issue is a question with no technical details:
    → Include ONLY: tool names + key technical terms from the issue.
    → Keep SHORT (1-2 lines max).
    → Example: "MetaSPAdes, de Bruijn graph assembler, scaffold/contigs.fasta"

"output":
  - Write the best answer using ONLY ideas present in the responses.
  - Prefer the highest-scored response (MEMBER/OWNER = more reliable).
  - Keep ALL code blocks, commands, flags EXACTLY as written in the responses.
  - Clean technical prose. NO greetings, NO "I hope this helps", NO meta-commentary.
  - If no response actually solves the issue → set output to "".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — OUTPUT FORMAT (NON-NEGOTIABLE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Respond with EXACTLY ONE valid JSON object.
- NO markdown. NO code fences. NO text before or after the JSON.
- Escape ALL special characters properly inside JSON strings.

{{"valid": true,  "instruction": "...", "input": "...", "output": "..."}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ISSUE ({tool}):
{issue_text}

RESPONSES:
{responses_block}
LABELS: {tags_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return ONLY the JSON."""



def extract_json_robust(raw):
    text = raw.strip()

    
    if text.startswith("```"):
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    
    try:
        start = text.index('{')
        end   = text.rindex('}') + 1
        text  = text[start:end]
    except ValueError:
        pass

   
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    
    try:
        fixed = re.sub(r'(?<!\\)\n', r'\\n', text)
        fixed = re.sub(r'(?<!\\)\t', r'\\t', fixed)
        fixed = re.sub(r'(?<!\\)\r', r'\\r', fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass


    try:
        valid_match  = re.search(r'"valid"\s*:\s*(true|false)',   text)
        cat_match    = re.search(r'"category"\s*:\s*"([^"]+)"',   text)
        instr_match  = re.search(r'"instruction"\s*:\s*"(.*?)"(?=\s*,\s*"input")',  text, re.DOTALL)
        input_match  = re.search(r'"input"\s*:\s*"(.*?)"(?=\s*,\s*"output")',       text, re.DOTALL)
        output_match = re.search(r'"output"\s*:\s*"(.*?)"(?=\s*[,}])',              text, re.DOTALL)
        if input_match:
            return {
                "valid":       valid_match.group(1) == 'true' if valid_match else True,
                "category":    cat_match.group(1)   if cat_match   else "technical_troubleshooting",
                "instruction": instr_match.group(1).replace('\\"', '"') if instr_match else "",
                "input":       input_match.group(1).replace('\\"', '"'),
                "output":      output_match.group(1).replace('\\"', '"') if output_match else "",
                "reason":      ""
            }
    except Exception:
        pass

    return None



def call_llm(client, prompt, retries=0):
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=8192,
            )
            raw    = response.choices[0].message.content.strip()
            result = extract_json_robust(raw)
            if result is not None:
                return result
            if attempt < retries:
                logging.warning(f"JSON parse failed (attempt {attempt+1}/{retries+1}), retry...")
        except Exception as e:
            if attempt < retries:
                logging.warning(f"LLM error (attempt {attempt+1}/{retries+1}): {e}, retry...")
            else:
                logging.warning(f"LLM failed definitively: {e}")
    return None



def get_top_responses(all_responses, n=TOP_N_RESPONSES):
    return sorted(
        all_responses,
        key=lambda r: r.get('score_calculer', 0),
        reverse=True
    )[:n]



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input',  default=INPUT_FILE)
    parser.add_argument('--output', default=OUTPUT_FILE)
    args = parser.parse_args()

    if not Path(args.input).exists():
        logging.error(f"Fichier introuvable : {args.input}")
        return

    client = OpenAI(base_url=API_BASE, api_key=API_KEY)

    stats = {
        'total':        0,
        'success':      0,
        'invalid':      0,
        'out_of_domain':0,
        'low_quality':  0,
        'no_output':    0,
        'failed':       0,
    }

    open(args.output, 'w', encoding='utf-8').close()
    logging.info(f"Lecture      : {args.input}")
    logging.info(f"Output       : {args.output}")
    logging.info(f"Modèle       : {MODEL_NAME}")
    logging.info(f"Top réponses : {TOP_N_RESPONSES}\n")

    total_lines = sum(1 for l in open(args.input, 'r', encoding='utf-8') if l.strip())

    with open(args.input,  'r', encoding='utf-8') as f_in, \
         open(args.output, 'a', encoding='utf-8') as f_out:

        pbar = tqdm(total=total_lines, desc="LLM filter", unit="issue")

        for line in f_in:
            line = line.strip()
            if not line:
                continue
            stats['total'] += 1

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                stats['failed'] += 1
                pbar.update(1)
                continue

            issue_text    = record.get('input', '')
            all_responses = record.get('all_responses', [])
            metadata      = record.get('metadata', {})
            labels        = metadata.get('labels', [])

            top_responses = get_top_responses(all_responses)
            if not top_responses:
                stats['failed'] += 1
                pbar.update(1)
                continue

            # Appel LLM
            prompt = build_prompt(issue_text, top_responses, labels, metadata.get('tool', ''))
            result = call_llm(client, prompt)

            if result is None:
                stats['failed'] += 1
                logging.warning(f"  JSON parse failed — {metadata.get('tool','?')} #{metadata.get('issue_id','?')}")
                pbar.update(1)
                pbar.set_postfix(ok=stats['success'], fail=stats['failed'])
                continue

            # Vérifier validité
            if not result.get('valid', True):
                reason = result.get('reason', 'unknown')
                logging.info(f"  Rejeté [{reason}] — {metadata.get('tool','?')} #{metadata.get('issue_id','?')}")
                if reason == 'out_of_domain':
                    stats['out_of_domain'] += 1
                elif reason == 'low_quality':
                    stats['low_quality'] += 1
                else:
                    stats['invalid'] += 1
                pbar.update(1)
                pbar.set_postfix(ok=stats['success'], fail=stats['failed'])
                continue

            llm_instruction = result.get('instruction', '').strip()
            llm_input = clean_body(str(result.get('input', '') or ''))
            llm_input = re.sub(r'\s*\n\s*', ', ', llm_input).strip(', ')  
            llm_output = clean_body(str(result.get('output', '') or ''))
            category   = result.get('category', 'technical_troubleshooting')

            if not llm_instruction:
                llm_instruction = f"Solve this bioinformatics issue regarding {metadata.get('tool', 'unknown')}: {metadata.get('title', '')}"

            if not llm_input:
                stats['failed'] += 1
                pbar.update(1)
                continue

            if not llm_output:
                stats['no_output'] += 1

            final = {
                "instruction": llm_instruction,
                "input":    llm_input,
                "output":   llm_output,
                "category": category,
                "metadata": {
                    "tool":           metadata.get('tool', ''),
                    "url":            metadata.get('url', ''),
                    "is_closed":      metadata.get('is_closed', True),
                    "labels":         labels,
                    "raw_input":      issue_text,        
                    "raw_responses":  top_responses,     
                }
            }

            f_out.write(json.dumps(final, ensure_ascii=False) + '\n')
            stats['success'] += 1
            pbar.update(1)
            pbar.set_postfix(ok=stats['success'], fail=stats['failed'], inv=stats['invalid'])

        pbar.close()

    total = max(stats['total'], 1)
    print(f"\n{'='*55}")
    print(f"  RAPPORT LLM FILTER")
    print(f"{'='*55}")
    print(f"  Total traités   : {stats['total']}")
    print(f"  Succès          : {stats['success']} ({stats['success']/total*100:.1f}%)")
    print(f"  Out of domain   : {stats['out_of_domain']}")
    print(f"  Low quality     : {stats['low_quality']}")
    print(f"  Invalid autres  : {stats['invalid']}")
    print(f"  Output vide     : {stats['no_output']}")
    print(f"  Échecs LLM      : {stats['failed']}")
    print(f"{'─'*55}")
    print(f"  Output          : {args.output}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()