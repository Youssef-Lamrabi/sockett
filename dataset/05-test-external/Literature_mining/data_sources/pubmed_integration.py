from typing import List, Dict, Optional
from Bio import Entrez, Medline
import time
import logging
from xml.etree import ElementTree as ET
from extractors.doi_utils import extract_and_normalize_doi
import re


# Entrez config
Entrez.email = "email@example.com"
Entrez.tool = "literature_mining_pipeline"

# PUBMED INGESTOR
class PubMedIngestor:
    def __init__(self, retry=3, delay=0.5):
        self.retry = retry
        self.delay = delay

    
    # SAFE NETWORK CALL
    def _safe_request(self, func, **kwargs):
        for attempt in range(self.retry):
            try:
                handle = func(**kwargs)
                return handle
            except Exception as e:
                logging.warning(
                    f"[PubMed] Request failed: {e} (attempt {attempt+1}/{self.retry})"
                )
                time.sleep(self.delay)

        logging.error("[PubMed] All retries failed.")
        return None
   
    # MAIN FETCH
    def fetch(self, query: str, retmax: int = 200) -> List[Dict]:
        logging.info(f"[PubMed] Searching: {query}")

        search_handle = self._safe_request(
            Entrez.esearch, db="pubmed", term=query, retmax=retmax
        )
        if not search_handle:
            return []

        search_res = Entrez.read(search_handle)
        pmids = search_res.get("IdList", [])

        logging.info(f"[PubMed] Found {len(pmids)} PMIDs")
        results = []
        
        # PROCESS EACH PMID
        for pmid in pmids:
            med_handle = self._safe_request(
                Entrez.efetch,
                db="pubmed",
                id=pmid,
                rettype="medline",
                retmode="text",
            )
            if not med_handle:
                continue

            try:
                record = Medline.read(med_handle)
            except Exception:
                logging.warning(f"[PubMed] Failed to read MEDLINE for {pmid}")
                continue
            
            # print(record)
            # print("--\n\n--")
            
            # Extract basic fields
            title = record.get("TI", "")
            abstract = record.get("AB", "")
            journal = record.get("JT", "")
            pub_date = record.get("DP", "")
            mesh_terms = record.get("MH", [])
            raw_doi = None
            so_field = record.get("SO", "")
            match = re.search(r"doi:\s*([^\s]+)", so_field, flags=re.IGNORECASE)
            if match:
                raw_doi = match.group(1)
            if not raw_doi:
                for aid in record.get("AID", []):
                    if "[doi]" in aid.lower():
                        raw_doi = aid.split()[0].strip()
                        break
            doi = raw_doi.strip()
            doi = doi.replace("https://doi.org/", "")
            doi = doi.replace("http://doi.org/", "")
            if doi[-1] == ".": doi = doi[:-1]

            # SAVE RESULT
            results.append(
                {
                    "source": "pubmed",
                    "pmid": pmid,
                    "title": title,
                    "abstract": abstract,
                    "doi": doi,
                    "journal": journal,
                    "pub_date": pub_date,
                    "mesh_terms": mesh_terms,
                }
            )

        return results


# Wrapper
def fetch(query: str, retmax: int = 200):
    return PubMedIngestor().fetch(query, retmax=retmax)
