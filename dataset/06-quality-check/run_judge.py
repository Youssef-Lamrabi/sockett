import json
import requests
import os
from tqdm import tqdm
import time

# ------------------------------------------------
# CONFIG
# ------------------------------------------------
API_URL = "http://10.52.88.30:11434/api/generate"
MODEL_NAME = "gpt-oss:20b"
TIMEOUT = 60
MAX_RETRIES = 3

INPUT_PATHS = [
    "./input/dataset/biostackexchange.jsonl",
    "./input/dataset/conceptual.jsonl"
]
OUTPUT_DIR = "./input/scored"

# -----------------------------------------------
# DATASET-SPECIFIC FIELD MAP
# -----------------------------------------------
FIELD_MAP = {
    "biostackexchange.jsonl": {
        "question": ["instruction", "input"],
        "answer": ["output"],
        "source": {
            "question": "metadata.source.question",
            "answer": "metadata.source.answer",
        }
    },
    "conceptual.jsonl": {
        "question": ["question"],
        "answer": ["answer"],
        "source": {
            "evidence": "extracted_from.evidence",
            "claim": "extracted_from.claim",
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
    for k in keys:
        if k in sample and sample[k]:
            values.append(str(sample[k]))
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
# PROMPT
# -----------------------------------------------
def build_prompt(sample):
    return f"""
You are a STRICT evaluation model for dataset quality.

You MUST ONLY use the provided SOURCE.
DO NOT use external knowledge.

Evaluate:
1. answer_correctness (0-2)
2. answer_completeness (0-2)
3. question_clarity (0-2)
4. faithfulness (0-2)

Scoring rules:
0 = bad
1 = partial
2 = good

Return STRICT JSON ONLY:
{{
  "answer_correctness": int,
  "answer_completeness": int,
  "question_clarity": int,
  "faithfulness": int,
  "reasoning": {{
    "correctness": "...",
    "completeness": "...",
    "clarity": "...",
    "faithfulness": "..."
  }}
}}

QUESTION:
{sample['question']}

ANSWER:
{sample['answer']}

SOURCE:
{sample['source']}
"""

# -----------------------------------------------
# LLM CALL
# -----------------------------------------------
def call_llm_judge(prompt):
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
# MAIN PIPELINE 
# -----------------------------------------------
def process_file(input_path):
    filename = os.path.basename(input_path)
    if filename not in FIELD_MAP:
        print(f"Skipping {filename} (no config)")
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
            print("---- DEBUG ---")
            print(sample)
            print("--------------")
            if not sample["question"] or not sample["answer"]:
                continue

            prompt = build_prompt(sample)
            output = call_llm_judge(prompt)

            if not output:
                continue
            scores = parse_json(output)
            if not scores:
                continue

            raw["quality_scores"] = scores
            outfile.write(json.dumps(raw) + "\n")
            outfile.flush()
            break #debug

    print(f"Saved: {out_path}")


# -----------------------------------------------
# RUN
# -----------------------------------------------
if __name__ == "__main__":
    for path in INPUT_PATHS:
        process_file(path)