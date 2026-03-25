import json
import requests
import os
from tqdm import tqdm

# -------------------------------
# CONFIG
# -------------------------------
API_URL = "http://10.52.88.30:11434/api/generate"
MODEL_NAME = "gpt-oss:20b"
TIMEOUT = 60

INPUT_PATHS = [
    "./data/dataset/qa.jsonl",
    # "./data/instruction.jsonl",
    # "./data/workflow.jsonl"
]
OUTPUT_DIR = "./data/scored"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Map your dataset schema
FIELD_MAP = {
    "question": ["question", "instruction"],
    "answer": ["answer", "output"],
    "source": ["source", "evidence", "context"]
}

# -------------------------------
# HELPERS
# -------------------------------
def extract_field(sample, keys):
    for k in keys:
        if k in sample:
            return sample[k]
    return ""

def normalize_sample(sample):
    return {
        "question": extract_field(sample, FIELD_MAP["question"]),
        "answer": extract_field(sample, FIELD_MAP["answer"]),
        "source": extract_field(sample, FIELD_MAP["source"]),
    }

def build_prompt(sample):
    return f"""
You are a STRICT evaluation model for dataset quality.

You MUST ONLY use the provided SOURCE.

Evaluate:

1. answer_correctness (0-2)
2. answer_completeness (0-2)
3. question_clarity (0-2)
4. faithfulness (0-2)

Return ONLY JSON:

{{
  "answer_correctness": int,
  "answer_completeness": int,
  "question_clarity": int,
  "faithfulness": int
}}

QUESTION:
{sample['question']}

ANSWER:
{sample['answer']}

SOURCE:
{sample['source']}
"""

def call_ollama(prompt):
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
        return response.json()["response"]
    except Exception as e:
        return None

def parse_json(text):
    try:
        return json.loads(text)
    except:
        return None

# -------------------------------
# MAIN PIPELINE
# -------------------------------
def process_file(input_path):
    results = []

    with open(input_path, "r") as f:
        lines = f.readlines()

    for line in tqdm(lines, desc=f"Processing {input_path}"):
        raw = json.loads(line)
        sample = normalize_sample(raw)

        prompt = build_prompt(sample)
        output = call_ollama(prompt)

        if output is None:
            continue

        scores = parse_json(output)

        if scores is None:
            continue

        raw["quality_scores"] = scores
        results.append(raw)

    out_path = os.path.join(
        OUTPUT_DIR,
        os.path.basename(input_path).replace(".jsonl", "_scored.jsonl")
    )

    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    print(f"Saved: {out_path}")

# -------------------------------
# RUN
# -------------------------------
if __name__ == "__main__":
    for path in INPUT_PATHS:
        process_file(path)