# generate_cluster_phrases.py
# Génère 1 phrase dense par cluster en utilisant llama3:8b via Ollama local
# La phrase doit contenir tous les keywords du cluster de manière naturelle
#
# Usage :
#   python generate_cluster_phrases.py
# Output :
#   category_clusters_phrases.py — dictionnaire {category: phrase}
# ─────────────────────────────────────────────────────────────────────────────

import requests
import json
import time
from category_clusters import CATEGORY_CLUSTERS, CATEGORIES

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL_NAME  = "llama3:8b"
OUTPUT_FILE = "category_clusters_phrases.py"
#SLEEP_SEC   = 1  # pause entre chaque requête pour ne pas surcharger



def build_prompt(category, keywords):
    cat_label = category.replace("_", " ")
    kw_list   = ", ".join(keywords)
    return f"""Write exactly ONE sentence (max 60 words) that naturally contains ALL of these terms: {kw_list}
The sentence must make sense. Do not describe or explain anything.
Write only the sentence, nothing else."""




def generate_phrase(category, keywords):
    prompt = build_prompt(category, keywords)
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model":  MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 120}
            },
            timeout=60
        )
        response.raise_for_status()
        text = response.json().get("response", "").strip()
        # Nettoyer les guillemets éventuels
        text = text.strip('"').strip("'").strip()
        return text
    except Exception as e:
        print(f"  [ERROR] {e}")
        return " ".join(keywords)  



def main():
    print(f"Génération des phrases via {MODEL_NAME}...\n")

    cluster_phrases = {}

    for i, cat in enumerate(CATEGORIES):
        keywords = CATEGORY_CLUSTERS[cat]
        print(f"[{i+1:02d}/{len(CATEGORIES)}] {cat}...")

        phrase = generate_phrase(cat, keywords)
        cluster_phrases[cat] = phrase

        print(f"  → {phrase[:100]}{'...' if len(phrase) > 100 else ''}\n")
        #time.sleep(SLEEP_SEC)

    # Sauvegarder dans category_clusters_phrases.py
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# category_clusters_phrases.py\n")
        f.write("# Généré automatiquement par generate_cluster_phrases.py via llama3:8b\n")
        f.write("# Chaque catégorie est représentée par 1 phrase dense contenant tous les keywords\n\n")
        f.write("CLUSTER_PHRASES = {\n")
        for cat, phrase in cluster_phrases.items():
            # Échapper les guillemets dans la phrase
            phrase_escaped = phrase.replace('"', '\\"')
            f.write(f'    "{cat}": "{phrase_escaped}",\n')
        f.write("}\n\n")
        f.write("CATEGORIES = list(CLUSTER_PHRASES.keys())\n")

    print(f"\n Fichier généré : {OUTPUT_FILE}")
    print(f"   {len(cluster_phrases)} catégories traitées")
    print(f"\nVérifier le fichier avant de l'utiliser pour l'embedding !")


if __name__ == "__main__":
    main()