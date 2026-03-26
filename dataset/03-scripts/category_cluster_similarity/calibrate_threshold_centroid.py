#Logique de similarité : Moyenne des vecteurs embeddings des keywords par cluster → centroïde normalisé, 
#puis cosine similarity entre le texte et chaque centroïde pour trouver la catégorie la plus proche et définir le seuil optimal

import json
import random
import argparse
import torch
import os
from sentence_transformers import SentenceTransformer
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

os.environ["HF_HUB_OFFLINE"] = "1"

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


# ── Helpers ───────────────────────────────────────────────────────────────────

def embed(texts, model):
    return torch.tensor(model.encode(texts, normalize_embeddings=True))  # (N, 384)


def build_text(record):
    t = record.get("type", "")
    if t in ("conceptual", "factual"):
        q = record.get("question", "") or ""
        a = record.get("answer", "")   or ""
        return f"{q} {a}".strip()
    else:
        i = record.get("instruction", "") or ""
        o = record.get("output", "")      or ""
        return f"{i} {o}".strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="datasets/category_cluster/paper_non_other.jsonl",
                        help="Fichier contenant des rows déjà classifiés correctement")
    parser.add_argument("--n", type=int, default=10,
                        help="Nombre de rows à utiliser pour la calibration")
    args = parser.parse_args()

    # 1. Charger les rows
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

    # 2. Charger le modèle
    print(f"Chargement {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    print("Modèle chargé \n")

    # 3. Embedder chaque keyword → moyenne des vecteurs → centroide par cluster
    print("Calcul des centroides par cluster...")
    cluster_centroids = {}
    for cat in CATEGORIES:
        keywords    = CATEGORY_CLUSTERS[cat]
        kw_embs     = embed(keywords, model)          # (N_kw, 384)
        centroid    = kw_embs.mean(dim=0)              # (384,)  ← moyenne des vecteurs
        centroid    = centroid / centroid.norm()       # normalisation
        cluster_centroids[cat] = centroid
    print(f"Centroides prêts — {len(cluster_centroids)} clusters \n")

    
    centroid_matrix = torch.stack([cluster_centroids[cat] for cat in CATEGORIES])

   
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE COSINE':<14} {'RANG'}")
    print("-" * 80)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        row_emb  = embed([text], model)[0]             
        row_emb  = row_emb / row_emb.norm()

        sim_scores    = torch.mv(centroid_matrix, row_emb)  

        correct_idx   = CATEGORIES.index(correct_cat)
        correct_score = sim_scores[correct_idx].item()
        sorted_idx    = sim_scores.argsort(descending=True).tolist()
        rank          = sorted_idx.index(correct_idx) + 1
        best_cat      = CATEGORIES[sorted_idx[0]]
        best_score    = sim_scores[sorted_idx[0]].item()

        scores_correct.append(correct_score)

        match = "YES" if rank == 1 else f"NO (prédit: {best_cat} @ {best_score:.3f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<14.4f} rang={rank}  {match}")

    # 5. Seuil
    mean_score = sum(scores_correct) / len(scores_correct)
    print("\n" + "=" * 80)
    print(f"  Score moyen  (→ seuil recommandé) : {mean_score:.4f}")
    print(f"  Score min                         : {min(scores_correct):.4f}")
    print(f"  Score max                         : {max(scores_correct):.4f}")
    print(f"\n  ➤  OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 80)


if __name__ == "__main__":
    main()