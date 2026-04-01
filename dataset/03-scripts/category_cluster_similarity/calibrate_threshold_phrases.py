# Logique de similarité : Chaque cluster est représenté par une phrase pré-construite (depuis CLUSTER_PHRASES), embedée comme un seul vecteur, 
# puis cosine similarity entre le texte et cette phrase par cluster.
# LLM choisi : sentence-transformers/all-MiniLM-L6-v2 (bi-encoder)



import json
import random
import argparse
import torch
from sentence_transformers import SentenceTransformer
from category_clusters_phrasesV2 import CLUSTER_PHRASES, CATEGORIES

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


def embed(texts, model):
    return torch.tensor(model.encode(texts, normalize_embeddings=True))


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

    print(f"Chargement {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    print("Modèle chargé\n")

    print("Embedding des phrases cluster...")
    cluster_phrases = [CLUSTER_PHRASES[cat] for cat in CATEGORIES]
    cluster_embs    = embed(cluster_phrases, model)
    print(f"Clusters embedés — {len(CATEGORIES)} phrases\n")

    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE COSINE':<16} {'RANG'}")
    print("-" * 80)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        row_emb  = embed([text], model)[0]
        raw      = torch.mv(cluster_embs, row_emb)
        raw_dict = {cat: raw[j].item() for j, cat in enumerate(CATEGORIES)}

        sorted_cats   = sorted(raw_dict, key=raw_dict.get, reverse=True)
        correct_score = raw_dict[correct_cat]
        rank          = sorted_cats.index(correct_cat) + 1
        best_cat      = sorted_cats[0]
        best_score    = raw_dict[best_cat]

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.4f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<16.4f} rang={rank}  {match}")

    mean_score = sum(scores_correct) / len(scores_correct)
    print("\n" + "=" * 80)
    print(f"  Score cosine moyen  (→ seuil) : {mean_score:.4f}")
    print(f"  Score cosine min              : {min(scores_correct):.4f}")
    print(f"  Score cosine max              : {max(scores_correct):.4f}")
    print(f"\n  ➤  OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 80)


if __name__ == "__main__":
    main()