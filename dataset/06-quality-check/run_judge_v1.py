# v2: ONE METRIC, SINGLE DATA AT TIME

import json
import requests
import os
from tqdm import tqdm
import time
import utils as prompt_helper

# ------------------------------------------------
# CONFIG
# ------------------------------------------------
API_URL = "http://10.52.88.105:11434/api/generate"
MODEL_NAME = "gpt-oss:20b"
TIMEOUT = 60
MAX_RETRIES = 3

INPUT_PATHS = [
    "./input/dataset/biostackexchange.jsonl",
    # "./input/dataset/conceptual.jsonl"
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
    source_dict = {}

    for name, path in source_map.items():
        value = get_nested_value(sample, path)
        if value:
            source_dict[name] = value

    return source_dict

def format_source(source_dict, include_keys=None):
    parts = []
    if include_keys:
        include_keys = [k.upper() for k in include_keys]

    for k, v in source_dict.items():
        if include_keys and k.upper() not in include_keys:
            continue
        
        parts.append(f"{k}:\n{v}")
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

def count_lines(file_path):
    with open(file_path, "r") as f:
        return sum(1 for _ in f)


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
    scores = {}
    reasons = {}
    prompts = {
        "question_faithfulness": prompt_helper.question_faithfulness(sample, format_source),
        "question_clarity": prompt_helper.question_clarity(sample, format_source),
        "answer_faithfulness": prompt_helper.answer_faithfulness(sample, format_source),
        "answer_completeness": prompt_helper.answer_completeness(sample, format_source),
    }

    for key, prompt in prompts.items():
        output = call_llm(prompt)
        parsed = parse_json(output) if output else None
        if parsed:
            scores[key] = parsed.get("score", -1)
            reasons[key] = parsed.get("reason", "")
        else:
            scores[key] = -1
            reasons[key] = "parse_error"

    return {
        "scores": scores,
        "reasons": reasons,
        "jugde_id": "automated-review-by:gpt"
    }

# -----------------------------------------------
# MAIN PIPELINE
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

    total_lines = count_lines(input_path)
    with open(input_path, "r") as infile, open(out_path, "a") as outfile:
        for line in tqdm(infile, total=total_lines, desc=f"Processing {filename}"):
            try:
                raw = json.loads(line)
            except:
                continue

            sample = normalize_sample(raw, config)
            print("\n\n-- sample --")
            print(json.dumps(sample, indent=2, ensure_ascii=False))
            if not sample["question"] or not sample["answer"]:
                continue

            scores = evaluate_sample(sample)
            print("\n\n-- score --")
            print(json.dumps(scores, indent=2, ensure_ascii=False))
            raw["quality_assessment"] = scores

            outfile.write(json.dumps(raw) + "\n")
            
            # break # for debug
            outfile.flush()
            
    print(f"Saved: {out_path}")

# -----------------------------------------------
# RUN
# -----------------------------------------------
if __name__ == "__main__":
    for path in INPUT_PATHS:
        process_file(path)