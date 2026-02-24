from datasets import load_dataset
import json

dataset = load_dataset(
    "json",
    data_files="C:/Users/PC/.cache/huggingface/hub/datasets--Genereux-akotenou--Metagenomics-Instruction-QA/snapshots/68586bb7117af31c6466cc23e31aacd801533438/qa_conceptual/conceptual.jsonl"
)

with open("metagenomics_instruction.jsonl", "w", encoding="utf-8") as f:
    for example in dataset["train"]:
        new_format = {
            "instruction": example["question"],
            "input": "",
            "output": example["answer"]
        }
        f.write(json.dumps(new_format, ensure_ascii=False) + "\n")