import json
import sys
import os

def load_jsonl(path):
    data = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def save_jsonl(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def add_type_field(data):
    updated = []
    for item in data:
        new_item = {"type": "troubleshooting"}
        new_item.update(item)
        updated.append(new_item)
    return updated


def main():
    if len(sys.argv) < 2:
        print("Usage: python script.py <input_jsonl_path>")
        sys.exit(1)

    input_path = sys.argv[1]

    if not input_path.endswith(".jsonl"):
        print("This script expects a .jsonl file")
        sys.exit(1)

    data = load_jsonl(input_path)
    initial_count = len(data)

    updated_data = add_type_field(data)

    base, ext = os.path.splitext(input_path)
    output_path = f"{base}_with_type{ext}"

    save_jsonl(output_path, updated_data)

    print("---- Summary ----")
    print(f"Input file: {input_path}")
    print(f"Total records processed: {initial_count}")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()