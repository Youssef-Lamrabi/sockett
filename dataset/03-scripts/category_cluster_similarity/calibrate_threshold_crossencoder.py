# Logique de similarité : Chaque row est comparé directement à chaque cluster (paire texte + phrase cluster) passée ensemble dans le cross-encoder,
# qui retourne un score de pertinence global sans passer par des vecteurs séparés.
# LLM choisi : cross-encoder/ms-marco-MiniLM-L-6-v2 (cross-encoder)
#
import json
import random
import argparse
import torch
# from transformers import AutoTokenizer, AutoModel                         # biobert base
# from sentence_transformers import SentenceTransformer                     # bi-encoder pritamdeka
# from sentence_transformers import SentenceTransformer  # all-MiniLM      # bi-encoder MiniLM
from sentence_transformers.cross_encoder import CrossEncoder
from category_clusters1 import CATEGORY_CLUSTERS, CATEGORIES

# ── Config ────────────────────────────────────────────────────────────────────
# MODEL_NAME = "dmis-lab/biobert-base-cased-v1.2"                           # bi-encoder
# MODEL_NAME = "pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb"   # bi-encoder
# MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"                    # bi-encoder
MODEL_NAME = "ncbi/MedCPT-Cross-Encoder"                        # cross-encoder
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


# ── Helpers ───────────────────────────────────────────────────────────────────

# def mean_pool(model_output, attention_mask):                              # bi-encoder only
#     token_emb = model_output.last_hidden_state
#     mask = attention_mask.unsqueeze(-1).expand(token_emb.size()).float()
#     return (token_emb * mask).sum(1) / mask.sum(1).clamp(min=1e-9)

# def embed_texts(texts, model):                                            # bi-encoder only
#     return torch.tensor(model.encode(texts, normalize_embeddings=True))

def build_cluster_text(category, keywords):
    """Phrase complète : nom catégorie + tous les keywords concaténés"""
    cat_label = category.replace("_", " ")
    kw_phrase = ", ".join(keywords)
    return f"{cat_label}: {kw_phrase}"


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
    parser.add_argument("--n", type=int, default=30,
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

    sample = random.sample(all_rows, args.n)
    print(f"\n{args.n} rows sélectionnés pour calibration\n")

    # 2. Charger le cross-encoder
    print(f"Chargement Cross-Encoder sur {DEVICE}...")
    # tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)                 # bi-encoder
    # model     = AutoModel.from_pretrained(MODEL_NAME).to(DEVICE)          # bi-encoder
    # model = SentenceTransformer(MODEL_NAME)                               # bi-encoder
    model = CrossEncoder(MODEL_NAME, device=DEVICE)
    print("Cross-Encoder chargé \n")

    # 3. Préparer les textes des 25 clusters
    cluster_texts = [
        build_cluster_text(cat, CATEGORY_CLUSTERS[cat]) for cat in CATEGORIES
    ]

    # 4. Scorer chaque row contre les 25 clusters
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE BON CLUSTER':<20} {'RANG DU BON CLUSTER'}")
    print("-" * 85)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        # Cross-encoder : paires (row_text, cluster_text) pour les 25 clusters
        # Différence clé vs bi-encoder : les deux textes sont vus ensemble
        pairs  = [(text, ct) for ct in cluster_texts]
        scores = model.predict(pairs)                   # array de 25 scores

        correct_idx   = CATEGORIES.index(correct_cat)
        correct_score = float(scores[correct_idx])

        sorted_idx = sorted(range(len(scores)), key=lambda x: scores[x], reverse=True)
        rank       = sorted_idx.index(correct_idx) + 1
        best_idx   = sorted_idx[0]
        best_cat   = CATEGORIES[best_idx]
        best_score = float(scores[best_idx])

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.3f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<20.4f} rang={rank}  {match}")

    
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