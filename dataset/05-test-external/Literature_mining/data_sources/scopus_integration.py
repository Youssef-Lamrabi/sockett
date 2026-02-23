import logging
from typing import List, Dict
from pybliometrics.scopus import ScopusSearch
from extractors.doi_utils import extract_and_normalize_doi


class ScopusIngestor:

    def __init__(self):
        logging.info("[Scopus] Ingestor initialized.")

    def fetch(self, query: str) -> List[Dict]:
        """Fetch Scopus papers using pybliometrics and clean output."""

        logging.info(f"[Scopus] Searching: {query}")

        # Execute the Scopus query
        try:
            search = ScopusSearch(query, refresh=False)
        except Exception as e:
            logging.error(f"[Scopus] Query failed: {e}")
            return []

        results = search.results
        if not results:
            logging.info("[Scopus] No results found.")
            return []

        print(results, "--\n\n--")
            
        papers = []
        for item in results:

            # DOI extraction + normalization
            raw_doi = getattr(item, "doi", None) or ""
            doi = extract_and_normalize_doi(raw_doi) or ""

            papers.append({
                "source": "scopus",
                "uid": getattr(item, "eid", ""),
                "title": getattr(item, "title", "") or "",
                "abstract": getattr(item, "description", "") or "",
                "doi": doi,
                "authors": getattr(item, "author_names", "") or "",
                "publication_name": getattr(item, "publicationName", "") or "",
                "cover_date": getattr(item, "coverDate", "") or "",
                "cited_by": getattr(item, "citedby_count", 0) or 0,
            })

        logging.info(f"[Scopus] Retrieved {len(papers)} papers.")
        return papers


# Wrapper for ingestion manager
def fetch(query: str):
    return ScopusIngestor().fetch(query)
