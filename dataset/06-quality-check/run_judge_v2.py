import json
import requests
import os
from tqdm import tqdm
import time

# ------------------------------------------------
# CONFIG
# ------------------------------------------------
API_URL = "http://10.52.88.105:11434/api/generate"
MODEL_NAME = "gpt-oss:20b"
TIMEOUT = 60
MAX_RETRIES = 3

INPUT_PATHS = [
    "./input/dataset/biostackexchange.jsonl",
    "./input/dataset/conceptual.jsonl"
]

OUTPUT_DIR = "./input/scored"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# -----------------------------------------------
# DATASET-SPECIFIC FIELD MAP
# -----------------------------------------------
FIELD_MAP = {
    "biostackexchange.jsonl": {
        "question": ["instruction", "input"],
        "answer": ["output"],
        "source": {
            "initial_question": "metadata.source.question",
            "initial_answer": "metadata.source.answer",
        }
    },
    "conceptual.jsonl": {
        "question": ["question"],
        "answer": ["answer"],
        "source": {
            "pdf_evidence": "extracted_from.evidence",
            "pdf_claim": "extracted_from.claim",
        }
    }
}

# -----------------------------------------------
# HELPERS
# -----------------------------------------------
def get_nested_value(d, path):
    keys = path.split(".")
    for k in keys:
        if isinstance(d, dict) and k in d:
            d = d[k]
        else:
            return ""
    return d

def extract_fields(sample, keys):
    values = []
    for i, k in enumerate(keys):
        if k in sample and sample[k]:
            text = str(sample[k]).strip()
            if i == 0:
                values.append(text)
            else:
                values.append(f"\nContext:\n{text}")
    return "\n".join(values)

def extract_source(sample, source_map):
    parts = []
    for name, path in source_map.items():
        value = get_nested_value(sample, path)
        if value:
            parts.append(f"{name.upper()}:\n{value}")
    return "\n\n".join(parts)

def normalize_sample(sample, config):
    return {
        "question": extract_fields(sample, config["question"]),
        "answer": extract_fields(sample, config["answer"]),
        "source": extract_source(sample, config["source"]),
    }

def parse_json(text):
    try:
        return json.loads(text)
    except:
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            return json.loads(text[start:end])
        except:
            return None

# -----------------------------------------------
# PROMPT BUILDERS (ONE PER METRIC)
# -----------------------------------------------

def base_rules():
    return """
You are a STRICT evaluator for dataset quality.

RULES:
- Use ONLY the provided SOURCE
- NO external knowledge
- If not supported → treat as incorrect
- Be strict and critical
"""

def prompt_correctness(sample):
    return f"""
{base_rules()}

TASK: Evaluate ANSWER CORRECTNESS

Definition:
- 0 = incorrect or contradicts SOURCE
- 1 = partially correct OR missing support
- 2 = fully correct and supported by SOURCE

Return JSON:
{{"score": int, "reason": "..."}}

QUESTION:
{sample['question']}

ANSWER:
{sample['answer']}

SOURCE:
{sample['source']}
"""

def prompt_completeness(sample):
    return f"""
{base_rules()}

TASK: Evaluate ANSWER COMPLETENESS

Definition:
- 0 = does not answer question
- 1 = partial / lacks reasoning
- 2 = complete and well explained

Return JSON:
{{"score": int, "reason": "..."}}

QUESTION:
{sample['question']}

ANSWER:
{sample['answer']}
"""

def prompt_clarity(sample):
    return f"""
{base_rules()}

TASK: Evaluate QUESTION CLARITY

Definition:
- 0 = unclear / ambiguous / missing context
- 1 = somewhat clear but incomplete
- 2 = clear and self-contained

Return JSON:
{{"score": int, "reason": "..."}}

QUESTION:
{sample['question']}
"""

def prompt_faithfulness(sample):
    return f"""
{base_rules()}

TASK: Evaluate FAITHFULNESS TO SOURCE

Definition:
- 0 = hallucinated / unsupported claims
- 1 = minor unsupported details
- 2 = fully grounded in SOURCE

Return JSON:
{{"score": int, "reason": "..."}}

ANSWER:
{sample['answer']}

SOURCE:
{sample['source']}
"""

# -----------------------------------------------
# LLM CALL
# -----------------------------------------------
def call_llm(prompt):
    for _ in range(MAX_RETRIES):
        try:
            response = requests.post(
                API_URL,
                json={
                    "model": MODEL_NAME,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0}
                },
                timeout=TIMEOUT
            )

            text = response.json().get("response", "").strip()
            if text:
                return text

        except Exception:
            time.sleep(1)

    return None

# -----------------------------------------------
# METRIC EVALUATION
# -----------------------------------------------
def evaluate_sample(sample):
    results = {}

    prompts = {
        "answer_correctness": prompt_correctness(sample),
        "answer_completeness": prompt_completeness(sample),
        "question_clarity": prompt_clarity(sample),
        "faithfulness": prompt_faithfulness(sample),
    }

    for key, prompt in prompts.items():
        output = call_llm(prompt)
        parsed = parse_json(output) if output else None

        if parsed:
            results[key] = parsed.get("score", -1)
            results[f"{key}_reason"] = parsed.get("reason", "")
        else:
            results[key] = -1
            results[f"{key}_reason"] = "parse_error"

    return results

# -----------------------------------------------
# MAIN PIPELINE (STREAMING)
# -----------------------------------------------
def process_file(input_path):
    filename = os.path.basename(input_path)

    if filename not in FIELD_MAP:
        print(f"Skipping {filename}")
        return

    config = FIELD_MAP[filename]

    out_path = os.path.join(
        OUTPUT_DIR,
        filename.replace(".jsonl", "_scored.jsonl")
    )

    with open(input_path, "r") as infile, open(out_path, "a") as outfile:
        for line in tqdm(infile, desc=f"Processing {filename}"):
            try:
                raw = json.loads(line)
            except:
                continue

            sample = normalize_sample(raw, config)

            if not sample["question"] or not sample["answer"]:
                continue

            scores = evaluate_sample(sample)

            raw["quality_scores"] = scores

            outfile.write(json.dumps(raw) + "\n")
            outfile.flush()

    print(f"Saved: {out_path}")

# -----------------------------------------------
# RUN
# -----------------------------------------------
if __name__ == "__main__":
    for path in INPUT_PATHS:
        process_file(path)