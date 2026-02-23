import logging
import requests
from typing import List, Dict, Optional
from extractors.doi_utils import extract_and_normalize_doi


class DimensionsIngestor:

    def __init__(self, retry: int = 3, delay: float = 0.5):
        self.retry = retry
        self.delay = delay
        logging.info("[Dimensions] Ingestor initialized.")

    def _post(self, url: str, query: str, headers: dict) -> Optional[dict]:
        """Safe POST wrapper with retries."""
        attempts = 0
        while attempts < self.retry:
            try:
                response = requests.post(url, data=query, headers=headers, timeout=10)

                if response.status_code == 200:
                    return response.json()
                else:
                    logging.warning(
                        f"[Dimensions] HTTP {response.status_code}: {response.text}"
                    )

            except Exception as e:
                logging.warning(
                    f"[Dimensions] Request failed ({e}) — retry {attempts + 1}/{self.retry}"
                )

            attempts += 1

        logging.error("[Dimensions] Failed after retries.")
        return None

    def fetch(self, query: str, token: str) -> List[Dict]:
        """Fetch metadata from Dimensions API."""

        logging.info(f"[Dimensions] Searching: {query}")
        
        if not token:
            logging.error("[Dimensions] Missing API token.")
            return []

        url = "https://app.dimensions.ai/api/dsl"

        headers = {
            "Authorization": f"JWT {token}",
            "Content-Type": "application/json"
        }

        # DSL Query 
        dsl = f'''
        search publications
        where title ~ "{query}"
        return publications[id, title, abstract, doi]
        '''

        data = self._post(url, dsl, headers=headers)
        if not data or "publications" not in data:
            logging.info("[Dimensions] No publications found.")
            return []

        results = data["publications"]
        papers = []

        for pub in results:
            raw_doi = pub.get("doi", "")
            doi = extract_and_normalize_doi(raw_doi) if raw_doi else ""

            papers.append({
                "source": "dimensions",
                "uid": pub.get("id", ""),
                "title": pub.get("title", "") or "",
                "abstract": pub.get("abstract", "") or "",
                "doi": doi,
            })

        logging.info(f"[Dimensions] Retrieved {len(papers)} papers.")
        return papers

# wrapper function required by ingestion_manager
def fetch(query: str, token: str):
    ingestor = DimensionsIngestor()
    return ingestor.fetch(query, token=token)
