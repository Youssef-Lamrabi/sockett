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


def remove_duplicates(data, keys):
    seen = set()
    filtered = []
    duplicate_count = 0

    for item in data:
        identifier = tuple(item.get(k) for k in keys)

        if identifier not in seen:
            seen.add(identifier)
            filtered.append(item)
        else:
            duplicate_count += 1

    return filtered, duplicate_count


def main():
    if len(sys.argv) < 3:
        print("Usage: python script.py <input_path> <key1> [key2 ...]")
        sys.exit(1)

    input_path = sys.argv[1]
    keys = sys.argv[2:]

    # Detect format
    if input_path.endswith(".jsonl"):
        data = load_jsonl(input_path)
        is_jsonl = True
    else:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        is_jsonl = False

    initial_count = len(data)

    filtered_data, duplicate_count = remove_duplicates(data, keys)
    filtered_count = len(filtered_data)

    # Output path
    base, ext = os.path.splitext(input_path)
    output_path = f"{base}_filtered{ext}"

    # Save
    if is_jsonl:
        save_jsonl(output_path, filtered_data)
    else:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(filtered_data, f, indent=4, ensure_ascii=False)

    # Print stats
    print("---- Summary ----")
    print(f"Input file: {input_path}")
    print(f"Keys used: {keys}")
    print(f"Initial records: {initial_count}")
    print(f"Duplicates removed: {duplicate_count}")
    print(f"Final records: {filtered_count}")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()