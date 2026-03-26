
import requests
import json
import re
import os
import logging
import argparse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ── Token ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = "" 

# ── Repos ─────────────────────────────────────────────────────────────────────

GITHUB_REPOS = {
    'MetaSPAdes':  'ablab/spades',
    'MEGAHIT':     'voutcn/megahit',
    'CheckM':      'Ecogenomics/CheckM',
    'CheckM2':     'chklovski/CheckM2',
    'FastP':       'OpenGene/fastp',
    'FastQC':      's-andrews/FastQC',
    'Trimmomatic': 'usadellab/Trimmomatic',
    'MultiQC':     'MultiQC/MultiQC',
    'Kraken2':     'DerrickWood/kraken2',
    'Bowtie2':     'BenLangmead/bowtie2',
    'BWA':         'lh3/bwa',
    'Samtools':    'samtools/samtools',
    'QUAST':       'ablab/quast',
    'Micromamba':  'mamba-org/micromamba',
}

SOLUTION_KEYWORDS = [
    'solution', 'fixed in', 'fix in', 'try this', 'resolved', 'workaround',
    'use this flag', 'you can use', 'the fix is', 'closing this', 'this was fixed',
    'use the following', 'should work', 'try running', 'the issue is',
    'the problem is', 'i found the', 'here is a fix', 'this should fix',
    'as a workaround', 'you need to', 'make sure', 'please try',
]

DISCUSSION_KEYWORDS = [
    'what do you think', 'thoughts?', 'opinion', 'discuss', 'proposal',
    'feature request', 'suggestion', 'would it be possible',
]

STEP_PATTERNS = [
    r'^\s*\d+[\.\)]\s+.+',        # "1. do this"  ou  "1) do this"
    r'^\s*[-*]\s+.+',             # "- do this"  ou  "* do this"
    r'^\s*step\s*\d+',            # "Step 1:"
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_text(text):
    """Nettoie sans troncature."""
    if not text:
        return ''
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def parse_reactions(r):
    if not r:
        return {}
    keys = ['+1', '-1', 'laugh', 'hooray', 'confused', 'heart', 'rocket', 'eyes']
    return {k: r[k] for k in keys if r.get(k, 0) > 0}


def classify_labels(labels):
    names = [l['name'].lower() for l in labels]
    return {
        'is_bug':        any(l in names for l in ['bug', 'defect', 'error']),
        'is_question':   any(l in names for l in ['question', 'help wanted', 'support']),
        'is_discussion': any(l in names for l in ['discussion', 'feature', 'enhancement', 'proposal']),
        'is_wontfix':    any(l in names for l in ['wontfix', "won't fix", 'invalid', 'duplicate']),
        'labels':        [l['name'] for l in labels],
    }


def extract_steps(text):
    """
    Extrait les étapes numérotées ou à puces d'un texte.
    Retourne une liste de strings ou [] si rien trouvé.
    """
    if not text:
        return []
    steps = []
    for line in text.splitlines():
        line = line.strip()
        if any(re.match(p, line) for p in STEP_PATTERNS) and len(line) > 5:
            # Nettoyer le préfixe numérique/puce
            clean = re.sub(r'^[\d\.\)\-\*\s]+', '', line).strip()
            if clean:
                steps.append(clean)
    return steps


def score_comment(comment):
    score = 0
    body  = (comment.get('body') or '').lower()
    role  = comment.get('author_association', '')

    if role in ['OWNER', 'MEMBER', 'COLLABORATOR']:
        score += 50
    if '```' in body or '`' in body:
        score += 20
    if any(kw in body for kw in SOLUTION_KEYWORDS):
        score += 15

    r = comment.get('reactions', {})
    score += r.get('+1', 0) * 10
    score += r.get('heart', 0) * 10
    score += r.get('hooray', 0) * 10

    words = len(body.split())
    if words > 50:
        score += 10
    if words > 150:
        score += 10

    return score


def is_discussion(title, body):
    text = (title + ' ' + body).lower()
    return any(kw in text for kw in DISCUSSION_KEYWORDS)


def fetch_json(url, headers, params=None):
    """Fetch sans pause — retry sur erreur réseau uniquement."""
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)

            # Gérer rate limit uniquement si vraiment atteinte
            if resp.status_code == 403:
                reset = int(resp.headers.get('X-RateLimit-Reset', 0))
                import time
                wait = max(reset - int(time.time()) + 2, 5)
                logging.warning(f"Rate limit 403 — attendre {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp.json()

            logging.error(f"HTTP {resp.status_code} — {url}")
            return None

        except requests.exceptions.RequestException as e:
            logging.warning(f"Tentative {attempt+1}/3 : {e}")
            import time; time.sleep(2)

    return None


def fetch_all_comments(comments_url, headers):
    """Récupère tous les commentaires paginés sans pause."""
    all_comments = []
    page = 1
    while True:
        data = fetch_json(comments_url, headers, params={'per_page': 100, 'page': page})
        if not data:
            break
        all_comments.extend(data)
        if len(data) < 100:
            break
        page += 1
    return all_comments


# ── Extraction principale ─────────────────────────────────────────────────────

def extract_issues(tool_name, repo, headers, max_issues, output_file):
    records = []
    page    = 1
    total   = 0

    logging.info(f"\n{'='*60}")
    logging.info(f"[{tool_name}] → {repo}")
    logging.info(f"{'='*60}")

    while total < max_issues:
        issues = fetch_json(
            f'https://api.github.com/repos/{repo}/issues',
            headers,
            params={
                'state':     'closed',
                'per_page':  50,
                'page':      page,
                'sort':      'updated',
                'direction': 'desc',
            }
        )

        if not issues:
            break

        for issue in issues:
            if total >= max_issues:
                break

            # Ignorer les Pull Requests
            if 'pull_request' in issue:
                continue

            title = clean_text(issue.get('title', ''))
            body  = clean_text(issue.get('body') or '')

            if not title or len(body.split()) < 10:
                continue

            # ── Métadonnées ───────────────────────────────────────────────
            label_info = classify_labels(issue.get('labels', []))

            metadata = {
                "issue_id":       issue.get('number'),
                "tool":           tool_name,
                "repo":           repo,
                "url":            issue.get('html_url', ''),
                "state":          issue.get('state', 'closed'),
                "labels":         label_info['labels'],
                "is_bug":         label_info['is_bug'],
                "is_question":    label_info['is_question'],
                "is_discussion":  label_info['is_discussion'] or is_discussion(title, body),
                "is_wontfix":     label_info['is_wontfix'],
                "author":         issue.get('user', {}).get('login', ''),
                "author_role":    issue.get('author_association', 'NONE'),
                "created_at":     issue.get('created_at', ''),
                "updated_at":     issue.get('updated_at', ''),
                "reactions":      parse_reactions(issue.get('reactions', {})),
                "comments_count": issue.get('comments', 0),
            }

            # ── Commentaires ──────────────────────────────────────────────
            all_responses = []
            best_output   = ''
            best_steps    = []

            if issue.get('comments', 0) > 0:
                raw_comments = fetch_all_comments(issue.get('comments_url', ''), headers)

                if raw_comments:
                    scores   = [score_comment(c) for c in raw_comments]
                    max_score = max(scores)
                    best_idx  = scores.index(max_score)

                    for idx, c in enumerate(raw_comments):
                        c_body = clean_text(c.get('body') or '')
                        c_score = scores[idx]
                        is_sol  = (idx == best_idx and max_score > 0)

                        all_responses.append({
                            "comment_id":     c.get('id'),
                            "author":         c.get('user', {}).get('login', ''),
                            "author_role":    c.get('author_association', 'NONE'),
                            "created_at":     c.get('created_at', ''),
                            "updated_at":     c.get('updated_at', ''),
                            "body":           c_body,
                            "reactions":      parse_reactions(c.get('reactions', {})),
                            "is_solution":    is_sol,
                            "solution_score": c_score,
                        })

                        if is_sol:
                            best_output = c_body
                            best_steps  = extract_steps(c_body)

            # Ignorer les issues sans aucune réponse utilisable
            if not best_output:
                continue

            # ── Record final ──────────────────────────────────────────────
            record = {
                "instruction": f"Solve this bioinformatics issue regarding {tool_name}: {title}",
                "input":       body,
                "output":      best_output,
                "steps":       best_steps,   # [] si pas d'étapes détectées
                "metadata":    metadata,
                "all_responses": all_responses,
            }

            with open(output_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')

            records.append(record)
            total += 1

            if total % 20 == 0:
                logging.info(f"  [{tool_name}] {total} issues extraites...")

        page += 1

    logging.info(f"[{tool_name}] terminé : {total} issues")
    return records


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token',  default=GITHUB_TOKEN or os.environ.get('GITHUB_TOKEN', ''),
                        help='GitHub personal access token')
    parser.add_argument('--max',    type=int, default=1000,
                        help='Nombre max d\'issues par repo (défaut: 1000)')
    parser.add_argument('--output', default='dataset_metagenomics_troubleshooting.jsonl',
                        help='Fichier de sortie JSONL')
    args = parser.parse_args()

    if not args.token:
        logging.warning("Pas de token → limite 60 req/h. Utiliser --token ou $GITHUB_TOKEN")

    headers = {'Accept': 'application/vnd.github.v3+json'}
    if args.token:
        headers['Authorization'] = f'token {args.token}'

    # Créer fichier vide
    open(args.output, 'w').close()
    logging.info(f"Output : {args.output}  |  Max/repo : {args.max}\n")

    total = 0
    for tool, repo in GITHUB_REPOS.items():
        recs   = extract_issues(tool, repo, headers, args.max, args.output)
        total += len(recs)

    logging.info(f"\n{'='*60}")
    logging.info(f"TOTAL : {total} issues extraites → {args.output}")
    logging.info(f"{'='*60}")


if __name__ == "__main__":
    main()