# Logique de similarité : Chaque cluster est converti en une phrase complète (nom + keywords concaténés),
# embedée comme un seul vecteur, puis cosine similarity entre le texte et ce vecteur-phrase par cluster.
# LLM choisi : sentence-transformers/all-MiniLM-L6-v2 (bi-encoder

import json
import random
import argparse
import torch
import os
# from transformers import AutoTokenizer, AutoModel                         # biobert base
# from transformers import AutoModelForSequenceClassification               # NLI inversé
# from sentence_transformers.cross_encoder import CrossEncoder              # cross-encoder
# from transformers import pipeline                                         # NLI direct
from sentence_transformers import SentenceTransformer
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

os.environ["HF_HUB_OFFLINE"] = "1"

# ── Config ────────────────────────────────────────────────────────────────────
# MODEL_NAME = "dmis-lab/biobert-base-cased-v1.2"                           # biobert base     1/10
# MODEL_NAME = "pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb"   # biobert sim      1/10
# MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"                      # cross-encoder    2/10
# MODEL_NAME = "cross-encoder/nli-deberta-v3-small"                        # NLI              2/10
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"                     
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


# ── Helpers ───────────────────────────────────────────────────────────────────

def embed_texts(texts, model):
    # def embed_texts(texts, tokenizer, model):                             # biobert
    #     encoded = tokenizer(texts, padding=True, truncation=True,
    #                         max_length=512, return_tensors="pt").to(DEVICE)
    #     with torch.no_grad():
    #         output = model(**encoded)
    #     return mean_pool(output, encoded["attention_mask"]).cpu()
    return torch.tensor(model.encode(texts, normalize_embeddings=True))


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

    # Sélection aléatoire — meilleure approche vs rows fixes 60-70
    sample = random.sample(all_rows, args.n)
    # sample = all_rows[60:70]                                              # rows fixes — 0/10
    print(f"\n{args.n} rows sélectionnés pour calibration\n")

    # 2. Charger le modèle
    print(f"Chargement {MODEL_NAME} sur {DEVICE}...")
    # tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)                 # biobert
    # model     = AutoModel.from_pretrained(MODEL_NAME).to(DEVICE)          # biobert
    # model     = CrossEncoder(MODEL_NAME, device=DEVICE)                   # cross-encoder
    model = SentenceTransformer(MODEL_NAME)
    print("Modèle chargé \n")

    # 3. Embedder les clusters comme phrase complète
    print("Embedding des clusters...")
    cluster_texts = [
        build_cluster_text(cat, CATEGORY_CLUSTERS[cat]) for cat in CATEGORIES
    ]
    cluster_embeddings = embed_texts(cluster_texts, model)
    # cluster_embeddings = embed_texts(cluster_texts, tokenizer, model)     # biobert
    cluster_embeddings = cluster_embeddings / cluster_embeddings.norm(dim=1, keepdim=True)

    # 4. Calculer les scores cosine pour chaque row vs son cluster correct
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE COSINE':<14} {'RANG DU BON CLUSTER'}")
    print("-" * 80)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        row_emb = embed_texts([text], model)
        # row_emb = embed_texts([text], tokenizer, model)                   # biobert
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