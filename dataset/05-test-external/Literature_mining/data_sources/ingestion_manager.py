from data_sources.pubmed_integration import fetch as fetch_pubmed
from data_sources.scopus_integration import fetch as fetch_scopus
from data_sources.dimensions_integration import fetch as fetch_dimensions

import hashlib
import re

# Helpers
def extract_pubmed_journal(p):
    """
    Extract journal title using multiple fallback locations.
    """
    if "fulljournalname" in p and p["fulljournalname"]:
        return p["fulljournalname"].strip()

    if "journal" in p and p["journal"]:
        return p["journal"].strip()

    if "isoabbreviation" in p and p["isoabbreviation"]:
        return p["isoabbreviation"].strip()

    if "medline_journal_info" in p:
        mji = p["medline_journal_info"]
        if isinstance(mji, dict):
            if "medlineta" in mji and mji["medlineta"]:
                return mji["medlineta"].strip()

    if "source" in p and p["source"]:
        return p["source"].strip()

    return ""

def fix_pubmed_like_doi(doi_value: str):
    """
    Remove fake DOIs of the form 10.xxxx/pmid:xxxxxx.
    """
    if not doi_value:
        return None

    doi_value = str(doi_value).strip()

    # Detect pattern: 10.xxx/pmid:xxxxxxx
    if re.match(r"10\.\d{4,9}/pmid:\d+$", doi_value.lower()):
        return None  # replace with None or "" so processing doesn't break

    return doi_value

def normalize_text(t):
    if not t:
        return ""
    return " ".join(t.replace("\n", " ").split()).strip()

def extract_pubmed_doi(p):
    """Try multiple known PubMed locations for DOI."""
    # 1. direct DOI
    if p.get("doi"):
        return normalize_text(p["doi"])

    # 2. elocationid sometimes contains DOI
    eloc = p.get("elocationid", "")
    if eloc and eloc.lower().startswith("doi:"):
        return eloc.split("doi:")[-1].strip()

    # 3. articleids → list of dicts
    ids = p.get("articleids", [])
    for item in ids:
        if item.get("idtype") == "doi":
            return normalize_text(item.get("value"))

    return ""

def compute_uid_from_doi_or_title(title, doi):
    """Deduplicate across Scopus + Dimensions using DOI or hashed title."""
    if doi:
        return doi.lower().strip()
    return hashlib.md5(title.lower().encode()).hexdigest()

# Unified ingestion manager
def ingest_all_sources(query, dimensions_token=None, max_pubmed=200, include=["PubMed"]):

    all_papers = []
    seen_uids = set()

    # PubMed 
    if "PubMed" in include:
        try:
            pubmed_results = fetch_pubmed(query, retmax=max_pubmed)
            for p in pubmed_results:

                pmid = p.get("pmid")              
                title = normalize_text(p.get("title"))
                abstract = normalize_text(p.get("abstract"))
                doi = extract_pubmed_doi(p)
                doi = fix_pubmed_like_doi(doi)  
                journal = extract_pubmed_journal(p)

                # fallback if abstract missing
                if not abstract:
                    abstract = normalize_text(p.get("other_abstract", ""))

                if not pmid:
                    # fallback but should not happen
                    uid = compute_uid_from_doi_or_title(title, doi)
                else:
                    uid = f"pmid:{pmid}"

                if uid not in seen_uids:
                    seen_uids.add(uid)
                    all_papers.append({
                        "source": "pubmed",
                        "uid": uid,
                        "pmid": pmid,
                        "journal": journal,
                        "title": title,
                        "abstract": abstract,
                        "doi": doi
                    })
        except Exception as e:
            print(f"[ERROR] PubMed ingestion failed: {e}")
            pubmed_results = []


    # Scopus
    if "Scopus" in include:
        try:
            scopus_results = fetch_scopus(query)
            print(scopus_results,  '--\n\n--')
            for p in scopus_results:
                title = normalize_text(p.get("title"))
                abstract = normalize_text(p.get("abstract"))
                doi = normalize_text(p.get("doi"))

                uid = compute_uid_from_doi_or_title(title, doi)

                if uid not in seen_uids:
                    seen_uids.add(uid)
                    all_papers.append({
                        "source": "scopus",
                        "uid": uid,
                        "title": title,
                        "abstract": abstract,
                        "doi": doi
                    })
        except Exception as e:
            print(f"[ERROR] Scopus ingestion failed: {e}")
            scopus_results = []


    # Dimensions 
    if "Dimensions" in include:
        dim_results = []
        if dimensions_token:
            try:
                dim_results = fetch_dimensions(query, token=dimensions_token)
                for p in dim_results:
                    title = normalize_text(p.get("title"))
                    abstract = normalize_text(p.get("abstract"))
                    doi = normalize_text(p.get("doi"))

                    uid = compute_uid_from_doi_or_title(title, doi)

                    if uid not in seen_uids:
                        seen_uids.add(uid)
                        all_papers.append({
                            "source": "dimensions",
                            "uid": uid,
                            "title": title,
                            "abstract": abstract,
                            "doi": doi
                        })
            except Exception as e:
                print(f"[ERROR] Dimensions ingestion failed: {e}")

    #Summary
    print(f"[INFO] Total papers collected: {len(all_papers)}")
    if "PubMed" in include: print(f"[INFO] Sources: PubMed ({len(pubmed_results)})")
    if "Scopus" in include: print(f"[INFO] Sources: Scopus ({len(scopus_results)})")
    if "Dimensions" in include: print(f"[INFO] Sources: Dimensions ({len(dim_results)})")
    return all_papers