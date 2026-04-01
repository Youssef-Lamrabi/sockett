import requests
import csv
import time
import os
import random
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin



DOI_FILE      = "C:/Users/PC/Downloads/genomeer/datasets/script2/dois.txt"
OUTPUT_DIR    = "C:/Users/PC/Downloads/genomeer/datasets/script2/PDFs"
REPORT_OUTPUT = "C:/Users/PC/Downloads/genomeer/datasets/script2/pdf_report.csv"

os.makedirs(OUTPUT_DIR, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]



WILEY_DOI_PREFIXES     = ["10.1111/", "10.1002/", "10.1155/", "10.1046/", "10.1890/", "10.1029/", "10.1196/", "10.1359/"]
ELSEVIER_DOI_PREFIXES  = ["10.1016/", "10.1053/", "10.1067/", "10.1054/", "10.1006/", "10.1078/", "10.1383/"]
ASM_DOI_PREFIXES       = ["10.1128/"]
OUP_DOI_PREFIXES       = ["10.1093/"]
TF_DOI_PREFIXES        = ["10.1080/", "10.3109/"]
LWW_DOI_PREFIXES       = ["10.1097/", "10.1213/"]
PNAS_DOI_PREFIXES      = ["10.1073/"]
SAGE_DOI_PREFIXES      = ["10.1177/"]
ACS_DOI_PREFIXES       = ["10.1021/"]



def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    })
    return s

session = make_session()

PDF_HEADERS = {
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}



def detect_publisher_by_doi(doi):
    for p in WILEY_DOI_PREFIXES:
        if doi.startswith(p): return "Wiley"
    for p in ELSEVIER_DOI_PREFIXES:
        if doi.startswith(p): return "Elsevier"
    for p in ASM_DOI_PREFIXES:
        if doi.startswith(p): return "ASM"
    for p in OUP_DOI_PREFIXES:
        if doi.startswith(p): return "OUP"
    for p in TF_DOI_PREFIXES:
        if doi.startswith(p): return "TaylorFrancis"
    for p in LWW_DOI_PREFIXES:
        if doi.startswith(p): return "LWW"
    for p in PNAS_DOI_PREFIXES:
        if doi.startswith(p): return "PNAS"
    for p in SAGE_DOI_PREFIXES:
        if doi.startswith(p): return "Sage"
    for p in ACS_DOI_PREFIXES:
        if doi.startswith(p): return "ACS"
    return None

def detect_publisher(url, doi=""):
    if "frontiersin.org" in url:                                return "Frontiers"
    if "mdpi.com" in url:                                       return "MDPI"
    if "nature.com" in url:                                     return "Nature"
    if "elsevier" in url or "sciencedirect" in url:             return "Elsevier"
    if "onlinelibrary.wiley.com" in url:                        return "Wiley"
    if "wiley.com" in url:                                      return "Wiley"
    if "link.springer.com" in url:                              return "Springer"
    if "biomedcentral.com" in url or "springeropen.com" in url: return "BioMedCentral"
    if "journals.asm.org" in url:                               return "ASM"
    if "academic.oup.com" in url:                               return "OUP"
    if "tandfonline.com" in url:                                return "TaylorFrancis"
    if "peerj.com" in url:                                      return "PeerJ"
    if "biorxiv.org" in url or "medrxiv.org" in url:           return "BioRxiv"
    if "journals.lww.com" in url or "lww.com" in url:          return "LWW"
    if "pnas.org" in url:                                       return "PNAS"
    if "bmj.com" in url:                                        return "BMJ"
    if "iwaponline.com" in url:                                 return "IWA"
    if "f1000research.com" in url or "wellcomeopenresearch.org" in url: return "F1000"
    if "journals.sagepub.com" in url:                           return "Sage"
    if "karger.com" in url:                                     return "Karger"
    if "jstage.jst.go.jp" in url:                               return "JStage"
    if "wjgnet.com" in url:                                     return "Baishideng"
    if "pubs.acs.org" in url:                                   return "ACS"
    if "rsc.org" in url:                                        return "RSC"
    if "springer.com" in url:                                   return "Springer"
    if "gut.bmj.com" in url:                                    return "BMJ"
    if doi:
        pub = detect_publisher_by_doi(doi)
        if pub: return pub
    return "Unknown"


def get_citation_pdf_url(soup):
    for tag in soup.find_all("meta"):
        name = tag.get("name", "") or tag.get("property", "")
        if "citation_pdf_url" in name.lower():
            return tag.get("content")
    return None

def extract_pdf_link(html, base_url, publisher, doi):
    soup = BeautifulSoup(html, "html.parser")
    meta_pdf = get_citation_pdf_url(soup)

    if publisher == "Frontiers":
        if meta_pdf: return meta_pdf, "Frontiers citation_pdf_url"

    if publisher == "MDPI":
        link = soup.find("a", {"class": "UD_ArticlePDF"})
        if link: return "https://www.mdpi.com" + link.get("href"), "MDPI PDF button"
        if meta_pdf: return meta_pdf, "MDPI citation_pdf_url"

    if publisher == "Wiley":
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if "/doi/" in base_url:
            doi_part = base_url.split("/doi/")[-1].split("?")[0]
            return f"{base}/doi/pdfdirect/{doi_part}?download=true", "Wiley pdfdirect"
        return f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true", "Wiley pdfdirect (DOI)"

    if publisher == "Elsevier":
        return None, "Elsevier requires authentication"

    if publisher == "Nature":
        if meta_pdf: return meta_pdf, "Nature citation_pdf_url"
        article_id = doi.split("/")[-1]
        return f"https://www.nature.com/articles/{article_id}.pdf", "Nature .pdf"

    if publisher in ("Springer", "BioMedCentral"):
        if meta_pdf: return meta_pdf, f"{publisher} citation_pdf_url"
        if publisher == "BioMedCentral":
            return base_url.rstrip("/") + "/pdf", "BMC /pdf suffix"

    if publisher == "ASM":
        if meta_pdf: return meta_pdf, "ASM citation_pdf_url"
        return f"https://journals.asm.org/doi/pdf/{doi}", "ASM /doi/pdf/"

    if publisher == "OUP":
        if meta_pdf: return meta_pdf, "OUP citation_pdf_url"
        return f"https://academic.oup.com/doi/pdf/{doi}", "OUP /doi/pdf/"

    if publisher == "TaylorFrancis":
        if meta_pdf: return meta_pdf, "T&F citation_pdf_url"
        return f"https://www.tandfonline.com/doi/pdf/{doi}?download=true", "T&F /doi/pdf/"

    if publisher == "PeerJ":
        if meta_pdf: return meta_pdf, "PeerJ citation_pdf_url"
        article_id = doi.split("/")[-1]
        return f"https://peerj.com/articles/{article_id}.pdf", "PeerJ .pdf"

    if publisher == "BioRxiv":
        if meta_pdf: return meta_pdf, "BioRxiv citation_pdf_url"
        return base_url.rstrip("/") + ".full.pdf", "BioRxiv .full.pdf"

    if publisher == "PNAS":
        if meta_pdf: return meta_pdf, "PNAS citation_pdf_url"
        return f"https://www.pnas.org/doi/pdf/{doi}", "PNAS /doi/pdf/"

    if publisher == "BMJ":
        if meta_pdf: return meta_pdf, "BMJ citation_pdf_url"
        return f"https://gut.bmj.com/content/doi/pdf/{doi}", "BMJ /doi/pdf/"

    if publisher == "IWA":
        if meta_pdf: return meta_pdf, "IWA citation_pdf_url"
        return f"https://iwaponline.com/doi/pdf/{doi}", "IWA /doi/pdf/"

    if publisher == "F1000":
        if meta_pdf: return meta_pdf, "F1000 citation_pdf_url"
        return base_url.rstrip("/") + "/pdf", "F1000 /pdf"

    if publisher == "Sage":
        if meta_pdf: return meta_pdf, "Sage citation_pdf_url"
        return f"https://journals.sagepub.com/doi/pdf/{doi}", "Sage /doi/pdf/"

    if publisher == "LWW":
        if meta_pdf: return meta_pdf, "LWW citation_pdf_url"
        return f"https://journals.lww.com/{doi}/pdf", "LWW /pdf"

    if publisher == "ACS":
        if meta_pdf: return meta_pdf, "ACS citation_pdf_url"
        return f"https://pubs.acs.org/doi/pdf/{doi}", "ACS /doi/pdf/"

    if publisher == "RSC":
        if meta_pdf: return meta_pdf, "RSC citation_pdf_url"
        return f"https://pubs.rsc.org/en/content/articlepdf/{doi.split('/')[-1]}", "RSC articlepdf"

    if publisher == "Karger":
        if meta_pdf: return meta_pdf, "Karger citation_pdf_url"
        return f"https://www.karger.com/Article/Pdf/{doi.split('/')[-1]}", "Karger /Article/Pdf/"

    if publisher == "JStage":
        if meta_pdf: return meta_pdf, "JStage citation_pdf_url"
        return base_url.replace("_article/", "_pdf/") + "/-char/en", "JStage PDF"

    if publisher == "Baishideng":
        if meta_pdf: return meta_pdf, "Baishideng citation_pdf_url"
        soup2 = BeautifulSoup(html, "html.parser")
        for a in soup2.find_all("a", href=True):
            if "/full-text/pdf/" in a["href"]:
                return urljoin(base_url, a["href"]), "Baishideng PDF link"

    if meta_pdf: return meta_pdf, "Generic citation_pdf_url"
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = a.get("href", "")
        if ".pdf" in href.lower():
            return urljoin(base_url, href), "Generic PDF link"

    return None, "No PDF link exposed"



def is_valid_pdf(response):
    ct = response.headers.get("Content-Type", "").lower()
    if "pdf" in ct: return True
    if response.content[:4] == b"%PDF": return True
    return False

def download_pdf_with_retry(pdf_url, max_retries=3):
    for attempt in range(max_retries):
        try:
            headers = {**PDF_HEADERS, "User-Agent": random.choice(USER_AGENTS)}
            resp = session.get(pdf_url, timeout=30, allow_redirects=True, headers=headers)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429) and attempt < max_retries - 1:
                time.sleep(4 + attempt * 3)
                continue
            return resp
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(2)
    return None

def add_to_manual(doi, pdf_url=None):
    MANUAL_FILE = os.path.join(OUTPUT_DIR, "MANUAL_DOWNLOADS.txt")
    with open(MANUAL_FILE, "a", encoding="utf-8") as mf:
        url = pdf_url if pdf_url else f"https://doi.org/{doi}"
        mf.write(url + "\n")


print("Lecture des DOIs depuis dois.txt...")

all_dois = []
with open(DOI_FILE, encoding="utf-8") as f:
    for line in f:
        doi = line.strip()
        if doi:
            all_dois.append(doi)

print(f"   Total DOIs a traiter : {len(all_dois)}")
print()

t = 0
s = 0
sm = 0
blocked = 0
no_pdf = 0
rows = []

for doi in all_dois:
    t += 1
    print(f"[{t}/{len(all_dois)}] {doi}")

    # Refresh session every 50 requests
    if t % 50 == 0:
        session = make_session()

    doi_url = f"https://doi.org/{doi}"

    try:
        r = session.get(doi_url, timeout=30, allow_redirects=True)
        final_url = r.url
        publisher = detect_publisher(final_url, doi)

        pdf_url, reason = extract_pdf_link(r.text, final_url, publisher, doi)

        # Elsevier → manual
        if publisher == "Elsevier" or not pdf_url:
            if publisher == "Elsevier":
                add_to_manual(doi)
                rows.append({"doi": doi, "publisher": publisher, "pdf_found": "manual", "reason": "Elsevier requires auth"})
                print(f"   Manual (Elsevier)")
                sm += 1
            else:
                rows.append({"doi": doi, "publisher": publisher, "pdf_found": False, "reason": reason})
                print(f"   No PDF — {reason}")
                no_pdf += 1
            time.sleep(random.uniform(1.5, 3.0))
            continue

        # Wiley → manual
        if publisher == "Wiley" or "onlinelibrary.wiley.com" in pdf_url:
            add_to_manual(doi, pdf_url)
            rows.append({"doi": doi, "publisher": "Wiley", "pdf_found": "manual", "reason": "Wiley manual download"})
            print(f"   Manual (Wiley)")
            sm += 1
            time.sleep(random.uniform(1.0, 2.0))
            continue

        # Download PDF
        pdf_resp = download_pdf_with_retry(pdf_url)

        if pdf_resp and pdf_resp.status_code == 200 and is_valid_pdf(pdf_resp):
            pdf_path = os.path.join(OUTPUT_DIR, doi.replace("/", "_") + ".pdf")
            with open(pdf_path, "wb") as pf:
                pf.write(pdf_resp.content)
            rows.append({"doi": doi, "publisher": publisher, "pdf_found": True, "reason": reason})
            print(f"   Saved ({reason})")
            s += 1
        else:
            status = pdf_resp.status_code if pdf_resp else "N/A"
            if status in (403, 401):
                add_to_manual(doi)
                rows.append({"doi": doi, "publisher": publisher, "pdf_found": "manual", "reason": f"HTTP {status} — manual"})
                print(f"   Manual (HTTP {status})")
                sm += 1
            else:
                rows.append({"doi": doi, "publisher": publisher, "pdf_found": False, "reason": f"HTTP {status}"})
                print(f"   Blocked HTTP {status}")
                blocked += 1

    except Exception as e:
        rows.append({"doi": doi, "publisher": "Unknown", "pdf_found": False, "reason": str(e)[:120]})
        print(f"   Error: {e}")
        no_pdf += 1

    time.sleep(random.uniform(1.5, 3.5))



print("\nEcriture du rapport final...")
with open(REPORT_OUTPUT, "w", newline="", encoding="utf-8") as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=["doi", "publisher", "pdf_found", "reason"])
    writer.writeheader()
    writer.writerows(rows)

p
print(f"\n Rapport  -> {REPORT_OUTPUT}")
print(f" Manuel   -> {os.path.join(OUTPUT_DIR, 'MANUAL_DOWNLOADS.txt')}")