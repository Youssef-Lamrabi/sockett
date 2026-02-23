import re
from typing import Optional, Iterable

DOI_REGEX = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"

def normalize_doi(doi: str) -> str:
    if not doi:
        return ""
    doi = doi.strip()
    doi = doi.replace("https://doi.org/", "")
    doi = doi.replace("http://doi.org/", "")
    doi = doi.replace("doi:", "").replace("DOI:", "")
    return doi.rstrip(" .;,)")
    

def extract_doi_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(DOI_REGEX, text)
    if match:
        return normalize_doi(match.group(0))
    return None


def extract_and_normalize_doi(record_fields: Iterable) -> Optional[str]:
    for field in record_fields:
        if not field:
            continue
        if isinstance(field, list):
            for v in field:
                doi = extract_doi_from_text(str(v))
                if doi:
                    return doi
        else:
            doi = extract_doi_from_text(str(field))
            if doi:
                return doi
    return None
