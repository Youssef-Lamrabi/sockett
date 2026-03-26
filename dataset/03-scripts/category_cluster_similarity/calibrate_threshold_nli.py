# Logique de similarité : Chaque row est comparé à chaque cluster via zero-shot NLI — le modèle évalue
# l'hypothèse "This text is about [catégorie + keywords]" et retourne un score d'entailment sans embedding explicite.
# LLM choisi : cross-encoder/nli-deberta-v3-small (zero-shot NLI)

import json
import random
import argparse
import torch
# from transformers import AutoTokenizer, AutoModel                         # biobert base
# from sentence_transformers import SentenceTransformer                     # bi-encoder
# from sentence_transformers.cross_encoder import CrossEncoder              # cross-encoder
from transformers import pipeline
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

# ── Config ────────────────────────────────────────────────────────────────────
# MODEL_NAME = "dmis-lab/biobert-base-cased-v1.2"                           # bi-encoder
# MODEL_NAME = "pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb"   # bi-encoder
# MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"                    # bi-encoder
# MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"                      # cross-encoder
MODEL_NAME = "cross-encoder/nli-deberta-v3-small"                           # zero-shot NLI
DEVICE     = 0 if torch.cuda.is_available() else -1  # 0=GPU, -1=CPU




def build_candidate_labels(category, keywords):
    """
    Construit le label NLI pour chaque catégorie :
    nom de catégorie + keywords comme contexte
    """
    cat_label = category.replace("_", " ")
    kw_phrase = ", ".join(keywords[:6]) 
    return f"{cat_label}: {kw_phrase}"


def build_text(record): 
    t = record.get("type", "")
    if t in ("conceptual", "factual"):
        q = record.get("question", "") or ""
        a = record.get("answer", "")   or ""
        text = f"{q} {a}".strip()
    else:
        i = record.get("instruction", "") or ""
        o = record.get("output", "")      or ""
        text = f"{i} {o}".strip()
    # Tronquer à 500 chars pour NLI (limite de tokens)
    return text[:500]


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

    # 2. Charger le pipeline NLI zero-shot
    print(f"Chargement NLI zero-shot ({MODEL_NAME})...")
    # model = SentenceTransformer(MODEL_NAME)                               # bi-encoder
    # model = CrossEncoder(MODEL_NAME, device=DEVICE)                       # cross-encoder
    classifier = pipeline(
        "zero-shot-classification",
        model=MODEL_NAME,
        device=DEVICE
    )
    print("Modèle NLI chargé \n")

    # 3. Préparer les labels candidats pour les 19 catégories
    candidate_labels = [
        build_candidate_labels(cat, CATEGORY_CLUSTERS[cat]) for cat in CATEGORIES
    ]
    print(f"Exemple label[0] : {candidate_labels[0]}\n")

    # 4. Scorer chaque row contre les 19 catégories
    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE NLI':<20} {'RANG DU BON CLUSTER'}")
    print("-" * 85)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        # NLI zero-shot : le modèle évalue chaque label comme hypothèse
        # "This text is about [label]" → score entailment
        result = classifier(text, candidate_labels, multi_label=False)

        # Réconstruire le ranking par catégorie
        label_to_score = dict(zip(result["labels"], result["scores"]))
        correct_label  = build_candidate_labels(correct_cat, CATEGORY_CLUSTERS[correct_cat])
        correct_score  = label_to_score.get(correct_label, 0.0)

        # Rang du bon cluster
        sorted_labels = sorted(label_to_score, key=label_to_score.get, reverse=True)
        rank          = sorted_labels.index(correct_label) + 1
        best_label    = sorted_labels[0]
        best_cat      = CATEGORIES[candidate_labels.index(best_label)]
        best_score    = label_to_score[best_label]

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.3f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<20.4f} rang={rank}  {match}")

    # 5. Calcul du seuil
    mean_score = sum(scores_correct) / len(scores_correct)
    min_score  = min(scores_correct)
    max_score  = max(scores_correct)

    print("\n" + "=" * 85)
    print(f"  Score NLI moyen  (→ seuil recommandé) : {mean_score:.4f}")
    print(f"  Score NLI min                         : {min_score:.4f}")
    print(f"  Score NLI max                         : {max_score:.4f}")
    print(f"\n  ➤  Seuil à utiliser dans classify_paper_other.py :")
    print(f"     OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 85)


if __name__ == "__main__":
    main()