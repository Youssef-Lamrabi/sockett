import json
import os
from pathlib import Path

# init
INPUT_DIR = "./dataset_output"
OUTPUT_DIR = "./cluster_data"
OUTPUT_FILES = {
    "factual": "factual.jsonl",
    "conceptual": "conceptual.jsonl",
    "workflow": "workflow.jsonl"
}

# some utils
os.makedirs(OUTPUT_DIR, exist_ok=True)
output_handles = {
    key: open(os.path.join(OUTPUT_DIR, fname), "w", encoding="utf-8")
    for key, fname in OUTPUT_FILES.items()
}
def write_jsonl(handle, obj):
    handle.write(json.dumps(obj, ensure_ascii=False) + "\n")


# main ---
for json_file in Path(INPUT_DIR).glob("*.json"):
    with open(json_file, "r", encoding="utf-8") as f:
        content = json.load(f)

    metadata = content.get("metadata", {})
    data_items = content.get("data", [])

    for item in data_items:
        item_type = item.get("type")

        if item_type not in OUTPUT_FILES:
            continue 

        enriched_item = {
            **item,
            "metadata": metadata
        }
        write_jsonl(output_handles[item_type], enriched_item)

for f in output_handles.values():
    f.close()
print("Done :)")
