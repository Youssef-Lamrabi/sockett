import requests
import csv
import time
import os
from bs4 import BeautifulSoup

STAGE1_REPORT = "pdf_report_stage1.csv"
OUTPUT_DIR = "pdfs_institutional"
REPORT_FILE = "pdf_report_stage2.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

os.makedirs(OUTPUT_DIR, exist_ok=True)

session = requests.Session()
session.headers.update(HEADERS)


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
    if "plos.org" in url:
        return "PLOS"
    if "springer" in url or "biomedcentral" in url:
        return "Springer/BMC"
    return "Unknown"


def extract_pdf_link(html, base_url, publisher):
    soup = BeautifulSoup(html, "html.parser")

    meta = soup.find("meta", {"name": "citation_pdf_url"})
    if meta:
        return meta.get("content"), "citation_pdf_url"

    if publisher == "MDPI":
        link = soup.find("a", {"class": "UD_ArticlePDF"})
        if link:
            return "https://www.mdpi.com" + link.get("href"), "MDPI PDF button"

    for a in soup.find_all("a", href=True):
        if ".pdf" in a["href"].lower():
            return requests.compat.urljoin(base_url, a["href"]), "PDF link scan"

    return None, "No PDF link exposed"




with open(STAGE1_REPORT, newline="", encoding="utf-8") as infile, \
     open(REPORT_FILE, "w", newline="", encoding="utf-8") as outfile:

    reader = csv.DictReader(infile)
    writer = csv.writer(outfile)
    writer.writerow(["doi", "publisher", "pdf_found", "reason"])

    failed_dois = [row["doi"] for row in reader if row["pdf_found"] == "False"]

    print(f"[INFO] Retrying {len(failed_dois)} DOIs under institutional access")

    for doi in failed_dois:
        doi_url = f"https://doi.org/{doi}"
        print(f"Retrying {doi}")

        try:
            r = session.get(doi_url, timeout=30, allow_redirects=True)
            publisher = detect_publisher(r.url)

            pdf_url, reason = extract_pdf_link(r.text, r.url, publisher)

            if not pdf_url:
                writer.writerow([doi, publisher, False, reason])
                continue

            pdf_resp = session.get(pdf_url, timeout=30)

            if pdf_resp.status_code == 200 and "pdf" in pdf_resp.headers.get("Content-Type", ""):
                filename = doi.replace("/", "_") + ".pdf"
                path = os.path.join(OUTPUT_DIR, filename)

                with open(path, "wb") as f:
                    f.write(pdf_resp.content)

                writer.writerow([doi, publisher, True, "Downloaded via institutional access"])
                print("  ✅ PDF saved")

            else:
                writer.writerow([doi, publisher, False, "Still blocked"])

        except Exception as e:
            writer.writerow([doi, "Unknown", False, str(e)])

        time.sleep(2)