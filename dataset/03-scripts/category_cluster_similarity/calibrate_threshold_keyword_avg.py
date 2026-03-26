# Logique de similarité : Chaque keyword du cluster est embedé séparément, 
# puis on calcule la cosine similarity entre le texte et chaque keyword individuellement, et on fait la moyenne de tous ces scores.
# LLM choisi : sentence-transformers/all-MiniLM-L6-v2 (bi-encoder)

import json
import random
import argparse
import torch
# from transformers import AutoTokenizer, AutoModel                         # biobert base
# from sentence_transformers.cross_encoder import CrossEncoder              # cross-encoder
# from transformers import pipeline                                         # NLI
# from transformers import AutoTokenizer, AutoModelForSequenceClassification # NLI inversé
from sentence_transformers import SentenceTransformer
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

# ── Config ────────────────────────────────────────────────────────────────────
# MODEL_NAME = "dmis-lab/biobert-base-cased-v1.2"                           # bi-encoder
# MODEL_NAME = "pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb"   # bi-encoder
# MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"                      # cross-encoder
# MODEL_NAME = "cross-encoder/nli-deberta-v3-small"                        # NLI
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"                       # keyword avg
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"




def embed(texts, model):
    emb = torch.tensor(model.encode(texts, normalize_embeddings=True))
    return emb  # shape (N, 384)


def cosine_sim(a, b):
    """Similarité cosine entre deux vecteurs normalisés"""
    return (a * b).sum().item()


def score_cluster_keyword_avg(row_emb, keyword_embeddings):
    """
    Ancienne approche — embed cluster complet comme 1 phrase :
        cluster_emb = embed([" ".join(keywords)])
        score = cosine_sim(row_emb, cluster_emb)

    Nouvelle approche — embed chaque keyword séparément + moyenne des scores :
        scores = [cosine_sim(row_emb, kw_emb) for kw_emb in keyword_embeddings]
        score  = mean(scores)
    """
    scores = [(row_emb * kw_emb).sum().item() for kw_emb in keyword_embeddings]
    return sum(scores) / len(scores)


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

    # 1. Charger les rows déjà classifiés
    all_rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                cat = r.get("category", "")
                if cat in CATEGORIES and build_text(r):
                    all_rows.append(r)

    if len(all_rows) < args.n:
        print(f"[WARN] Seulement {len(all_rows)} rows valides trouvés (demandé: {args.n})")
        args.n = len(all_rows)

    #sample = random.sample(all_rows, args.n)
    sample = all_rows[60:70]
    print(f"\n{args.n} rows sélectionnés pour calibration\n")

    # 2. Charger le modèle
    print(f"Chargement {MODEL_NAME} sur {DEVICE}...")
    model = SentenceTransformer(MODEL_NAME)
    print("Modèle chargé \n")

    # 3. Embedder chaque keyword séparément pour chaque cluster
    print("Embedding des keywords individuels par cluster...")
    cluster_keyword_embeddings = {}
    for cat in CATEGORIES:
        keywords = CATEGORY_CLUSTERS[cat]
        kw_embs  = embed(keywords, model)  # shape (N_keywords, 384)
        cluster_keyword_embeddings[cat] = kw_embs
    print(f"Keywords embeddings prêts — {sum(len(v) for v in cluster_keyword_embeddings.values())} keywords total ✅\n")

    # 4. Scorer chaque row
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE MOY KW':<20} {'RANG'}")
    print("-" * 85)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        row_emb = embed([text], model)[0]  # vecteur (384,)

        # Score par cluster = moyenne des cosine similarities avec chaque keyword
        cluster_scores = {
            cat: score_cluster_keyword_avg(row_emb, cluster_keyword_embeddings[cat])
            for cat in CATEGORIES
        }

        correct_score = cluster_scores[correct_cat]
        sorted_cats   = sorted(cluster_scores, key=cluster_scores.get, reverse=True)
        rank          = sorted_cats.index(correct_cat) + 1
        best_cat      = sorted_cats[0]
        best_score    = cluster_scores[best_cat]

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.3f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<20.4f} rang={rank}  {match}")

    # 5. Calcul du seuil
    mean_score = sum(scores_correct) / len(scores_correct)
    min_score  = min(scores_correct)
    max_score  = max(scores_correct)

    print("\n" + "=" * 85)
    print(f"  Score moyen  (→ seuil recommandé) : {mean_score:.4f}")
    print(f"  Score min                         : {min_score:.4f}")
    print(f"  Score max                         : {max_score:.4f}")
    print(f"\n  ➤  Seuil à utiliser dans classify_paper_other.py :")
    print(f"     OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 85)


if __name__ == "__main__":
    main()