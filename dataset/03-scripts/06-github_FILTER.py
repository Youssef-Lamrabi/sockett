import json
import re
import hashlib
import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


INPUT_FILE  = "github_issue.jsonl"
OUTPUT_FILE = "github_filtred.jsonl"



NOISE_PATTERNS = [
    r'^\s*\+1\s*$',
    r'^\s*thanks?\s*[!\.]*\s*$',
    r'^\s*thank you\s*[!\.]*\s*$',
    r'^\s*closing\s+(as|this)\s+(duplicate|resolved|fixed|wontfix)',
    r'^\s*duplicate\s+of\s+#\d+',
    r'^\s*see\s+#\d+\s*$',
    r'^\s*fixed\s+in\s+#\d+\s*$',
    r'^\s*@\w+\s*$',
    r'^\s*same\s+(issue|problem|here)\s*[!\.]*\s*$',
    r'^\s*me\s+too\s*[!\.]*\s*$',
    r'^\s*any\s+update\s+(on\s+this)?\s*\??\s*$',
    r'^\s*ping\s*$',
    r'^\s*bump\s*$',
    r'^\s*noted\s*[\.!]*\s*$',
    r'^\s*acknowledged\s*[\.!]*\s*$',
]


LINK_ONLY = re.compile(r'^\s*(https?://\S+\s*)+$')

REDIRECT_ONLY = re.compile(
    r'^(this is the same( issue)? as|'
    r'see (discussion|issue|comment|pr|#\d+)|'
    r'duplicate of|'
    r'refer to|'
    r'check (out\s+)?https?://|'
    r'fixed in https?://|'
    r'closing (as )?duplicate|'
    r'same as https?://)'
    r'\s*(https?://\S+|#\d+)?\s*[\.!]*\s*$',
    re.IGNORECASE
)

# Réponse courte avec lien (< 15 mots)
SHORT_REDIRECT = re.compile(r'^.{0,80}(https?://\S+|#\d+)\s*$', re.IGNORECASE)

# Nettoyage
IMAGE_MD        = re.compile(r'!\[.*?\]\(.*?\)')
ATTACHMENT_LINK = re.compile(r'\[.*?\]\(https://github\.com/user-attachments/.*?\)')
QUOTE_BLOCK     = re.compile(r'^>.*$', re.MULTILINE)
HTML_TAGS       = re.compile(r'<[^>]+>')


# ── Nettoyage texte ───────────────────────────────────────────────────────────

def clean_body(text):
    if not text:
        return ''
    text = ATTACHMENT_LINK.sub('', text)
    text = IMAGE_MD.sub('', text)
    text = HTML_TAGS.sub('', text)
    text = QUOTE_BLOCK.sub('', text)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'\t', '    ', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def word_count(text):
    return len(text.split()) if text else 0


def has_code(text):
    return '```' in text or bool(re.search(r'`[^`]+`', text))


def has_command(text):
    return bool(re.search(
        r'(\$\s+\w+|^\s{4}\w|\bpython\b|\bbash\b|\bcat\b|\bgrep\b|\bawk\b|'
        r'\.py\b|\.sh\b|/usr/|/home/)',
        text, re.MULTILINE
    ))


def is_useless_output(text):
    """
    Détecte toutes les formes de réponses inutiles pour le fine-tuning :
      - Bruit pur (+1, thanks, bump...)
      - Lien seul (toute la réponse = URL)
      - Redirection "same as #123", "see issue #456", "duplicate of..."
      - Réponse très courte (< 15 mots) contenant juste un lien/référence
    """
    if not text or len(text.strip()) < 5:
        return True, "empty"

    t     = text.strip()
    t_low = t.lower()

    # Bruit pur
    for p in NOISE_PATTERNS:
        if re.match(p, t_low, re.IGNORECASE):
            return True, "noise"

    # Lien seul
    if LINK_ONLY.match(t_low):
        return True, "link_only"

    # Redirection explicite
    if REDIRECT_ONLY.match(t_low):
        return True, "redirect_only"

    # Court + lien = inutile
    if word_count(t) < 15 and SHORT_REDIRECT.match(t):
        return True, "short_redirect"

    return False, "ok"


# ── Scoring qualité ───────────────────────────────────────────────────────────

def compute_quality_score(record):
    score  = 0
    output = record.get('output', '')
    inp    = record.get('input', '')
    resp   = record.get('_best_response', {})

    author_role = resp.get('author_role', 'NONE')
    reactions   = resp.get('reactions', {})

    if author_role in ['OWNER', 'MEMBER', 'COLLABORATOR']: score += 3
    elif author_role == 'CONTRIBUTOR':                      score += 1

    pos = reactions.get('+1', 0) + reactions.get('heart', 0) + reactions.get('hooray', 0)
    if pos >= 3:   score += 2
    elif pos >= 1: score += 1

    if has_code(output):         score += 2
    if has_command(output):      score += 1
    if word_count(output) > 100: score += 1
    if word_count(inp) > 50:     score += 1

    return score


# ── Filtres durs ──────────────────────────────────────────────────────────────

def passes_hard_filters(record, min_input_words, min_output_words):
    meta   = record.get('metadata', {})
    output = record.get('output', '')
    inp    = record.get('input', '')

    if meta.get('is_wontfix', False):
        return False, "wontfix"

    useless, reason = is_useless_output(output)
    if useless:
        return False, f"output_{reason}"

    if word_count(output) < min_output_words:
        return False, "output_too_short"

    if word_count(inp) < min_input_words:
        return False, "input_too_short"

    if meta.get('is_discussion', False) and not has_code(output):
        return False, "discussion_no_code"

    return True, "ok"


# ── Déduplication ─────────────────────────────────────────────────────────────

def fingerprint(text):
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()


# ── Construction record final ─────────────────────────────────────────────────

def build_clean_record(record, quality_score):
    meta = record.get('metadata', {})
    resp = record.get('_best_response', {})

    return {
        "instruction": record.get('instruction', ''),
        "input":       clean_body(record.get('input', '')),
        "output":      clean_body(record.get('output', '')),
        "metadata": {
            "tool":          meta.get('tool', ''),
            "url":           meta.get('url', ''),
            "is_bug":        meta.get('is_bug', False),
            "is_question":   meta.get('is_question', False),
            "author_role":   resp.get('author_role', 'NONE'),
            "quality_score": quality_score,
            "has_code":      has_code(record.get('output', '')),
        }
    }


# ── Pipeline principal ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min_score',        type=int, default=4)
    parser.add_argument('--min_input_words',  type=int, default=20)
    parser.add_argument('--min_output_words', type=int, default=30)
    args = parser.parse_args()

    if not Path(INPUT_FILE).exists():
        logging.error(f"Fichier introuvable : {INPUT_FILE}")
        return

    stats = {
        'total':        0,
        'wontfix':      0,
        'output_noise': 0,
        'just_link':    0,
        'too_short':    0,
        'discussion':   0,
        'low_score':    0,
        'duplicate':    0,
        'kept':         0,
    }

    seen_inputs  = set()
    seen_outputs = set()

    open(OUTPUT_FILE, 'w', encoding='utf-8').close()
    logging.info(f"Lecture : {INPUT_FILE}")
    logging.info(f"Score minimum : {args.min_score}/10\n")

    with open(INPUT_FILE, 'r', encoding='utf-8') as f_in, \
         open(OUTPUT_FILE, 'a', encoding='utf-8') as f_out:

        for line in f_in:
            line = line.strip()
            if not line:
                continue
            stats['total'] += 1

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            all_resp  = record.get('all_responses', [])
            best_resp = next(
                (r for r in all_resp if r.get('is_solution', False)),
                all_resp[0] if all_resp else {}
            )
            record['_best_response'] = best_resp

            # ── Filtres durs ──────────────────────────────────────────────
            passed, reason = passes_hard_filters(
                record, args.min_input_words, args.min_output_words
            )
            if not passed:
                if   'wontfix'     in reason: stats['wontfix']      += 1
                elif 'link'        in reason: stats['just_link']    += 1
                elif 'redirect'    in reason: stats['just_link']    += 1
                elif 'noise'       in reason: stats['output_noise'] += 1
                elif 'discussion'  in reason: stats['discussion']   += 1
                else:                         stats['too_short']    += 1
                continue

            # ── Score qualité ─────────────────────────────────────────────
            quality = compute_quality_score(record)
            if quality < args.min_score:
                stats['low_score'] += 1
                continue

            # ── Déduplication ─────────────────────────────────────────────
            fp_in  = fingerprint(record.get('input', ''))
            fp_out = fingerprint(record.get('output', ''))
            if fp_in in seen_inputs or fp_out in seen_outputs:
                stats['duplicate'] += 1
                continue
            seen_inputs.add(fp_in)
            seen_outputs.add(fp_out)

            # ── Record final ──────────────────────────────────────────────
            clean = build_clean_record(record, quality)
            f_out.write(json.dumps(clean, ensure_ascii=False) + '\n')
            stats['kept'] += 1

    total = max(stats['total'], 1)
    print(f"\n{'='*55}")
    print(f"  RAPPORT DE FILTRAGE")
    print(f"{'='*55}")
    print(f"  Total lu               : {stats['total']}")
    print(f"  Wontfix/invalid        : {stats['wontfix']}")
    print(f"  Output bruit           : {stats['output_noise']}")
    print(f"  Output lien/redirection: {stats['just_link']}")
    print(f"  Trop court             : {stats['too_short']}")
    print(f"  Discussion sans code   : {stats['discussion']}")
    print(f"  Score trop bas         : {stats['low_score']}")
    print(f"  Doublons               : {stats['duplicate']}")
    print(f"{'─'*55}")
    print(f"  Gardés              : {stats['kept']} ({stats['kept']/total*100:.1f}%)")
    print(f"  Output                 : {OUTPUT_FILE}")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()