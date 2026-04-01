# Logique de similarité : Chaque row est comparé directement à la phrase pré-construite (llama3) de chaque cluster via cross-encoder, 
# qui retourne un score brut de pertinence 
# LLM choisi : cross-encoder/ms-marco-MiniLM-L-6-v2 (cross-encoder)

import json
import random
import argparse
import torch
from sentence_transformers.cross_encoder import CrossEncoder
from category_clusters_phrasesV2 import CLUSTER_PHRASES, CATEGORIES

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


def build_text(record):
    t = record.get("type", "")
    if t in ("conceptual", "factual"):
        q = record.get("question", "") or ""
        a = record.get("answer",   "") or ""
        return f"{q} {a}".strip()
    else:
        i = record.get("instruction", "") or ""
        o = record.get("output",      "") or ""
        return f"{i} {o}".strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="datasets/category_cluster/paper_non_other.jsonl")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()

    all_rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                cat = r.get("category", "")
                if cat in CATEGORIES and build_text(r):
                    all_rows.append(r)

    if len(all_rows) < args.n:
        args.n = len(all_rows)

    sample = random.sample(all_rows, args.n)
    print(f"\n{args.n} rows sélectionnés\n")

    print(f"Chargement Cross-Encoder ({MODEL_NAME}) sur {DEVICE}...")
    model = CrossEncoder(MODEL_NAME, device=DEVICE)
    print("Cross-Encoder chargé\n")

    cluster_phrases = [CLUSTER_PHRASES[cat] for cat in CATEGORIES]

    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE BRUT':<16} {'RANG'}")
    print("-" * 80)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        pairs  = [(text, cp) for cp in cluster_phrases]
        scores = model.predict(pairs)

        raw_dict    = {cat: float(scores[j]) for j, cat in enumerate(CATEGORIES)}
        sorted_cats = sorted(raw_dict, key=raw_dict.get, reverse=True)

        correct_score = raw_dict[correct_cat]
        rank          = sorted_cats.index(correct_cat) + 1
        best_cat      = sorted_cats[0]
        best_score    = raw_dict[best_cat]

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.4f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<16.4f} rang={rank}  {match}")

    mean_score = sum(scores_correct) / len(scores_correct)
    print("\n" + "=" * 80)
    print(f"  Score brut moyen  (→ seuil) : {mean_score:.4f}")
    print(f"  Score brut min              : {min(scores_correct):.4f}")
    print(f"  Score brut max              : {max(scores_correct):.4f}")
    print(f"\n  ➤  OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 80)


if __name__ == "__main__":
    main()