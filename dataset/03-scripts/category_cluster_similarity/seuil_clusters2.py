#definition de seuil optimal pour classifer les out of scope
# Calcule le seuil optimal de similarité cosine
# en se basant sur N=10 rows déjà classifiés correctement.
#
# Méthode :
#   1. Charger N rows dont la catégorie est déjà connue et correcte
#   2. Embedder chaque row (question+answer ou instruction+output)
#   3. Embedder les 25 clusters — tous les keywords concaténés en 1 seule phrase
#   4. Calculer le score cosine entre chaque row et son cluster correct
#   5. Moyenne de ces scores → seuil recommandé
#
# Usage :
#   python calibrate_threshold.py --input paper_non_other.jsonl --n 10
# ─────────────────────────────────────────────────────────────────────────────

import json
import random
import argparse
import torch
# from transformers import AutoTokenizer, AutoModel
# from sentence_transformers import SentenceTransformer  # pritamdeka
from sentence_transformers import SentenceTransformer
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

# ── Config ────────────────────────────────────────────────────────────────────
# MODEL_NAME = "dmis-lab/biobert-base-cased-v1.2"
# MODEL_NAME = "pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


# ── Helpers ───────────────────────────────────────────────────────────────────

# def mean_pool(model_output, attention_mask):
#     token_emb = model_output.last_hidden_state
#     mask = attention_mask.unsqueeze(-1).expand(token_emb.size()).float()
#     return (token_emb * mask).sum(1) / mask.sum(1).clamp(min=1e-9)

def embed_texts(texts, model):
    # def embed_texts(texts, tokenizer, model):
    #     encoded = tokenizer(...)
    #     return mean_pool(output, encoded["attention_mask"]).cpu()
    return torch.tensor(model.encode(texts, normalize_embeddings=True))


def build_cluster_text(category, keywords):
    """
    Concatène tous les keywords d'un cluster en une seule phrase naturelle.
    Ancienne approche : " ".join(keywords)  → mots isolés sans contexte
    Nouvelle approche : phrase complète avec le nom de la catégorie + keywords
    """
    # Ancienne approche — simple join
    # return " ".join(keywords)

    # Nouvelle approche — phrase naturelle avec nom de catégorie + keywords
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

    sample = random.sample(all_rows, args.n)
    print(f"\n{args.n} rows sélectionnés pour calibration\n")

    # 2. Charger le modèle
    print(f"Chargement modèle sur {DEVICE}...")
    # tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    # model     = AutoModel.from_pretrained(MODEL_NAME).to(DEVICE)
    # model.eval()
    model = SentenceTransformer(MODEL_NAME)
    print("Modèle chargé \n")

    # 3. Embedder les 25 clusters — 1 phrase complète par cluster
    print("Embedding des clusters (phrase complète concaténée)...")
    cluster_texts = [
        build_cluster_text(cat, CATEGORY_CLUSTERS[cat]) for cat in CATEGORIES
    ]
    # Afficher un exemple pour vérification
    print(f"  Exemple cluster[0] : {cluster_texts[0][:80]}...\n")

    cluster_embeddings = embed_texts(cluster_texts, model)
    # cluster_embeddings = embed_texts(cluster_texts, tokenizer, model)
    cluster_embeddings = cluster_embeddings / cluster_embeddings.norm(dim=1, keepdim=True)

    # 4. Calculer les scores cosine pour chaque row vs son cluster correct
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE COSINE':<14} {'RANG DU BON CLUSTER'}")
    print("-" * 80)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        row_emb = embed_texts([text], model)
        # row_emb = embed_texts([text], tokenizer, model)
        row_emb = row_emb / row_emb.norm(dim=1, keepdim=True)

        sim_scores    = torch.mm(row_emb, cluster_embeddings.T).squeeze(0)
        correct_idx   = CATEGORIES.index(correct_cat)
        correct_score = sim_scores[correct_idx].item()
        sorted_idx    = sim_scores.argsort(descending=True).tolist()
        rank          = sorted_idx.index(correct_idx) + 1
        best_idx      = sorted_idx[0]
        best_cat      = CATEGORIES[best_idx]
        best_score    = sim_scores[best_idx].item()

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.3f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<14.4f} rang={rank}  {match}")

    # 5. Calcul du seuil
    mean_score = sum(scores_correct) / len(scores_correct)
    min_score  = min(scores_correct)
    max_score  = max(scores_correct)

    print("\n" + "=" * 80)
    print(f"  Score cosine moyen  (→ seuil recommandé) : {mean_score:.4f}")
    print(f"  Score cosine min                         : {min_score:.4f}")
    print(f"  Score cosine max                         : {max_score:.4f}")
    print(f"\n  ➤  Seuil à utiliser dans classify_paper_other.py :")
    print(f"     OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
