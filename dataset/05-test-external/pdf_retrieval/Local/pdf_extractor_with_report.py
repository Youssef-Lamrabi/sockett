import requests
import csv
import time
import os, subprocess
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ------------------------------
# Fatima use this for you window
# ------------------------------
# DOI_FILE = "C:/Users/fatyr/Downloads/dois_full.txt"
# OUTPUT_DIR = "pdfs"
# REPORT_FILE = "pdf_report.csv"
# ------------------------------
DOI_FILE = "./dois.txt"
OUTPUT_DIR = "./pdfs"
REPORT_FILE = "./pdf_report.csv"
# ------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
os.makedirs(OUTPUT_DIR, exist_ok=True)
session = requests.Session()
session.headers.update(HEADERS)


def get_wiley_base(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

def detect_publisher(url):
    if "frontiersin.org" in url:
        return "Frontiers"
    if "mdpi.com" in url:
        return "MDPI"
    if "nature.com" in url:
        return "Nature"
    if "elsevier" in url or "sciencedirect" in url:
        return "Elsevier"
    if "wiley.com" in url:
        return "Wiley"
    return "Unknown"


def extract_pdf_link(html, base_url, publisher):
    soup = BeautifulSoup(html, "html.parser")

    # --- Frontiers ---
    if publisher == "Frontiers":
        meta = soup.find("meta", {"name": "citation_pdf_url"})
        if meta:
            return meta.get("content"), "Frontiers citation_pdf_url"

    # --- MDPI ---
    if publisher == "MDPI":
        link = soup.find("a", {"class": "UD_ArticlePDF"})
        if link:
            return "https://www.mdpi.com" + link.get("href"), "MDPI PDF button"
        
    # --- Wiley ---
    if publisher == "Wiley":
        base = get_wiley_base(base_url)
        if "/doi/" in base_url:
            doi = base_url.split("/doi/")[-1].split("?")[0]
            pdf_url = f"{base}/doi/pdfdirect/{doi}?download=true"
            return pdf_url, "Wiley pdfdirect (constructed)"
        
    # https://enviromicro-journals.onlinelibrary.wiley.com/doi/pdfdirect/10.1111/1758-2229.70249?download=true

    # --- Generic meta tag ---
    meta = soup.find("meta", {"name": "citation_pdf_url"})
    if meta:
        return meta.get("content"), "Generic citation_pdf_url"

    # --- Fallback: any .pdf link ---
    for a in soup.find_all("a", href=True):
        if ".pdf" in a["href"].lower():
            return requests.compat.urljoin(base_url, a["href"]), "Generic PDF link"

    return None, "No PDF link exposed"


with open(DOI_FILE) as f, open(REPORT_FILE, "w", newline="", encoding="utf-8") as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(["doi", "publisher", "pdf_found", "reason"])
    t = 0
    s = 0
    sm = 0
    
    for doi in f:
        t += 1
        doi = doi.strip()
        if not doi:
            continue

        doi_url = f"https://doi.org/{doi}"
        print(f"Processing {doi}")

        try:
            r = session.get(doi_url, timeout=30, allow_redirects=True)
            final_url = r.url
            publisher = detect_publisher(final_url)
            # print(publisher)

            pdf_url, reason = extract_pdf_link(r.text, final_url, publisher)
            # print(pdf_url, reason)

            if not pdf_url:
                writer.writerow([doi, publisher, False, reason])
                print("  ❌ No PDF found")
                continue
            
            if "onlinelibrary.wiley.com" in pdf_url:
                MANUAL_FILE = os.path.join(OUTPUT_DIR, "MANUAL_DOWNLOADS.txt")
                with open(MANUAL_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{pdf_url}\n")
                print("  ✅ PDF saved - manual")
                sm += 1

            pdf_resp = session.get(pdf_url, timeout=30)
            if pdf_resp.status_code == 200 and "pdf" in pdf_resp.headers.get("Content-Type", ""):
                pdf_path = os.path.join(OUTPUT_DIR, doi.replace("/", "_") + ".pdf")
                with open(pdf_path, "wb") as pdf_file:
                    pdf_file.write(pdf_resp.content)

                writer.writerow([doi, publisher, True, reason])
                print("  ✅ PDF saved")
                s += 1
            else:
                writer.writerow([doi, publisher, False, "PDF request blocked"])

        except Exception as e:
            writer.writerow([doi, "Unknown", False, str(e)])
            print("  ⚠️ Error:", e)

        time.sleep(2)

print('Total dowload : ', s)
print('Total manual  : ', sm)
print('Total         : ', t)