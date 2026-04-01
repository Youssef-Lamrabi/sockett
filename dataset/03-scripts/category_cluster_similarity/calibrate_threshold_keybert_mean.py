# Logique de similarité : KeyBERT extrait les keywords du row,
# puis on calcule la moyenne globale de toute la matrice de cosine similarity entre les keywords du row et les keywords de chaque cluster (pénalise les mauvais matches au lieu de récompenser les bons individuels).
# LLM choisi : sentence-transformers/all-MiniLM-L6-v2 + KeyBERT (bi-encoder)

import json
import random
import argparse
import torch
from keybert import KeyBERT
from sentence_transformers import SentenceTransformer
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TOP_N_KW   = 10

# Stopwords custom uniquement — pas de ENGLISH_STOP_WORDS (trop agressif)
CUSTOM_STOPWORDS = [
    # verbes et termes génériques
    "used", "using", "use", "study", "studies", "analysis", "analyses",
    "method", "methods", "approach", "approaches", "result", "results",
    "data", "dataset", "samples", "sample", "performed", "shown",
    "present", "including", "included", "based", "different", "significant",
    "observed", "found", "identified", "detected", "measured", "collected",
    "used study", "study used", "performed using", "based using",
    "known", "well", "high", "low", "new", "total", "specific",
    # unités et nombres
    "mg", "ml", "µl", "kb", "gb", "bp", "ng", "µg",
    "200", "100", "50", "10", "20", "30", "15", "16",
    # fragments inutiles
    "et", "al", "also", "however", "therefore", "thus", "furthermore",
    "moreover", "yes", "no", "many", "time", "rate", "level", "type", "types",
]


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


def embed(texts, model):
    return model.encode(texts, normalize_embeddings=True, convert_to_tensor=True)


def score_row_vs_clusters(row_keywords, cluster_embs_dict, st_model):
    """
    Pour chaque cluster : calcule la moyenne globale de toute la matrice
    de similarité cosine entre keywords du row et keywords du cluster.
    Pénalise les mauvais matches au lieu de récompenser les bons individuels.
    """
    if not row_keywords:
        return {cat: 0.0 for cat in CATEGORIES}

    row_kw_texts = [kw for kw, _ in row_keywords]
    row_kw_embs  = embed(row_kw_texts, st_model)       # (K_row, 384)

    scores = {}
    for cat in CATEGORIES:
        cat_embs   = cluster_embs_dict[cat]             # (K_cat, 384)
        sim_matrix = torch.mm(row_kw_embs, cat_embs.T) # (K_row, K_cat)
        score      = sim_matrix.mean().item()           # moyenne globale
        scores[cat] = score

    return scores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="datasets/category_cluster/paper_non_other.jsonl")
    parser.add_argument("--n", type=int, default=20)
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

    # 2. Charger SentenceTransformer
    print(f"Chargement SentenceTransformer ({MODEL_NAME})...")
    st_model = SentenceTransformer(MODEL_NAME)
    print("SentenceTransformer chargé\n")

    # 3. Charger KeyBERT
    print("Chargement KeyBERT...")
    kw_model = KeyBERT(model=st_model)
    print("KeyBERT chargé\n")

    # 4. Pré-embedder les keywords de chaque cluster
    print("Embedding des keywords clusters...")
    cluster_embs_dict = {}
    for cat in CATEGORIES:
        cluster_embs_dict[cat] = embed(CATEGORY_CLUSTERS[cat], st_model)
    print(f"Clusters embedés — {len(CATEGORIES)} clusters\n")

    # 5. Scorer chaque row
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE':<16} {'RANG'}")
    print("-" * 100)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        # Extraction KeyBERT
        row_keywords = kw_model.extract_keywords(
            text,
            keyphrase_ngram_range=(1, 2),
            stop_words=CUSTOM_STOPWORDS,
            top_n=TOP_N_KW,
            use_maxsum=True,
            nr_candidates=20
        )

       
        scores      = score_row_vs_clusters(row_keywords, cluster_embs_dict, st_model)
        sorted_cats = sorted(scores, key=scores.get, reverse=True)

        correct_score = scores[correct_cat]
        rank          = sorted_cats.index(correct_cat) + 1
        best_cat      = sorted_cats[0]
        best_score    = scores[best_cat]

        scores_correct.append(correct_score)

        kw_list = ", ".join([kw for kw, _ in row_keywords[:5]])
        match   = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.4f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<16.4f} rang={rank}  {match}")
        print(f"       KW: {kw_list}")
        print()

    mean_score = sum(scores_correct) / len(scores_correct)
    print("=" * 100)
    print(f"  Score moyen  (→ seuil) : {mean_score:.4f}")
    print(f"  Score min              : {min(scores_correct):.4f}")
    print(f"  Score max              : {max(scores_correct):.4f}")
    print(f"\n  ➤  OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 100)


if __name__ == "__main__":
    main()