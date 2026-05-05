#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ajouter le src au PYTHONPATH pour pouvoir importer depuis genomeer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from genomeer.model.bio_rag import _CARDFetcher, _KEGGFetcher
except ImportError as e:
    print(f"Error importing genomeer.model.bio_rag: {e}")
    sys.exit(1)

def refresh_card(data_dir: Path):
    print("Fetching CARD entries...")
    docs = _CARDFetcher.fetch()
    entries = []
    for doc in docs:
        entries.append({
            "gene": doc.metadata.get("gene"),
            "drug_class": doc.metadata.get("drug_class"),
            "mechanism": doc.text.split("Mechanism: ")[1].split(".")[0] if "Mechanism: " in doc.text else "",
            "description": doc.text.split(". ", 1)[1] if ". " in doc.text else ""
        })
    
    out_data = {
        "__bundle_date__": datetime.now().isoformat(),
        "source_version": "CARD-Dynamic",
        "entries": entries
    }
    
    out_file = data_dir / "card_top500.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
    print(f"Saved {len(entries)} CARD entries to {out_file}")

def refresh_kegg(data_dir: Path):
    print("Fetching KEGG pathway entries...")
    docs = _KEGGFetcher.fetch()
    entries = []
    for doc in docs:
        entries.append({
            "pathway_id": doc.metadata.get("pathway_id"),
            "name": doc.metadata.get("name"),
            "description": doc.text.split(". ")[1] if ". " in doc.text else ""
        })
    
    out_data = {
        "__bundle_date__": datetime.now().isoformat(),
        "source_version": "KEGG-Dynamic",
        "entries": entries
    }
    
    out_file = data_dir / "kegg_core_pathways.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)
    print(f"Saved {len(entries)} KEGG entries to {out_file}")

def main():
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    refresh_card(data_dir)
    refresh_kegg(data_dir)
    print("Refresh complete.")

if __name__ == "__main__":
    main()
