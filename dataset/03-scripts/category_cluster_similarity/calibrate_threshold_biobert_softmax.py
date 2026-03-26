# Logique de similarité : Chaque cluster est représenté par une phrase pré-construite (llama3), 
# embedée via BioBERT avec mean pooling, puis cosine similarity entre le texte et chaque cluster normalisé par softmax (au lieu de min-max) pour obtenir des probabilités réelles.
# LLM choisi : dmis-lab/biobert-base-cased-v1.2 (bi-encoder BioBERT)

import json
import random
import argparse
import torch
import math
import os
from transformers import AutoTokenizer, AutoModel
from category_clusters_phrases import CLUSTER_PHRASES, CATEGORIES

os.environ["HF_HUB_OFFLINE"] = "1"

MODEL_NAME = "dmis-lab/biobert-base-cased-v1.2"
# MODEL_NAME = "pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb"
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"


def mean_pool(model_output, attention_mask):
    token_emb = model_output.last_hidden_state
    mask = attention_mask.unsqueeze(-1).expand(token_emb.size()).float()
    return (token_emb * mask).sum(1) / mask.sum(1).clamp(min=1e-9)


def embed_texts(texts, tokenizer, model):
    encoded = tokenizer(texts, padding=True, truncation=True,
                        max_length=512, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        output = model(**encoded)
    return mean_pool(output, encoded["attention_mask"]).cpu()


def softmax_scores(scores_dict):
    """
    Softmax au lieu de min-max — probabilités réelles qui somment à 1
    Min-max forçait max=1.000 ce qui biaisait tout
    """
    vals     = list(scores_dict.values())
    exp_vals = [math.exp(s) for s in vals]
    total    = sum(exp_vals)
    return {cat: exp_vals[i] / total for i, cat in enumerate(scores_dict)}


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="datasets/category_cluster/paper_non_other.jsonl")
    parser.add_argument("--n", type=int, default=0)
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

    print(f"Chargement BioBERT ({MODEL_NAME})...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model     = AutoModel.from_pretrained(MODEL_NAME).to(DEVICE)
    model.eval()
    print("BioBERT chargé \n")

    # Embedder les phrases llama3 — 1 phrase par cluster
    print("Embedding des phrases cluster (llama3)...")
    cluster_phrases = [CLUSTER_PHRASES[cat] for cat in CATEGORIES]
    cluster_embs    = embed_texts(cluster_phrases, tokenizer, model)
    cluster_embs    = cluster_embs / cluster_embs.norm(dim=1, keepdim=True)
    print(f"Clusters embedés \n")

    print(f"\n{'IDX':<6} {'CATEGORIE CORRECTE':<40} {'SCORE SOFTMAX':<16} {'RANG'}")
    print("-" * 80)

    scores_correct = []

    for i, row in enumerate(sample):
        correct_cat = row.get("category", "")
        text        = build_text(row)

        row_emb  = embed_texts([text], tokenizer, model)
        row_emb  = row_emb / row_emb.norm(dim=1, keepdim=True)

        raw      = torch.mm(row_emb, cluster_embs.T).squeeze(0)
        raw_dict = {cat: raw[j].item() for j, cat in enumerate(CATEGORIES)}

        # Softmax — remplace min-max biaisé
        soft_dict = softmax_scores(raw_dict)

        correct_score = soft_dict[correct_cat]
        sorted_cats   = sorted(soft_dict, key=soft_dict.get, reverse=True)
        rank          = sorted_cats.index(correct_cat) + 1
        best_cat      = sorted_cats[0]
        best_score    = soft_dict[best_cat]

        scores_correct.append(correct_score)

        match = "Y" if rank == 1 else f"N (prédit: {best_cat} @ {best_score:.4f})"
        print(f"{i:<6} {correct_cat:<40} {correct_score:<16.4f} rang={rank}  {match}")

    mean_score = sum(scores_correct) / len(scores_correct)
    print("\n" + "=" * 80)
    print(f"  Score softmax moyen  (→ seuil) : {mean_score:.4f}")
    print(f"  Score softmax min              : {min(scores_correct):.4f}")
    print(f"  Score softmax max              : {max(scores_correct):.4f}")
    print(f"\n  ➤  OUT_OF_SCOPE_THR = {mean_score:.4f}")
    print("=" * 80)


if __name__ == "__main__":
    main()