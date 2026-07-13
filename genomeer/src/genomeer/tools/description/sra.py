"""
Genomeer — SRA/ENA read-download tool description.
====================================================
Isolated in its own module (NOT part of metagenomics_db.py) so it can be wired
into read_module2api() independently — activating ONLY fetch_sra_reads without
re-enabling the other metagenomics_db.py query tools (query_silva, query_card,
query_gtdb, etc.), which stay dormant/unwired on purpose (duplicates of local
tools already active: run_rgi/CARD, run_gtdbtk, run_dbcan — see metagenomics_db.py
docstring for the full catalog).
"""

description = [
    {
        "name": "fetch_sra_reads",
        "description": (
            "[Python/API Tool][TIMEOUT: 3600s] Download raw sequencing reads (FASTQ) for an SRA/ENA "
            "RUN accession (SRR/ERR/DRR — NOT a BioProject/PRJNA; resolve BioProject -> run accessions "
            "first via NCBI's Entrez Direct metadata-lookup tool: search the 'sra' database by the "
            "BioProject ID, then fetch runinfo for that query). AVAILABLE in any env (pure urllib, no "
            "CLI dependency, no separate SRA-toolkit install needed). Uses the ENA (EBI) "
            "filereport REST API to resolve the exact FASTQ download URLs, then downloads over HTTPS — "
            "this is the SAME allowed exception as the literature/general-web lookup tools: ENA "
            "(ebi.ac.uk) is NOT NCBI E-utilities, so urllib here is fine (do NOT hit NCBI's raw "
            "eutils.ncbi.nlm.nih.gov endpoints with urllib — that restriction is specific to that host). "
            "PREFERRED — DO NOT hand-roll the download: just call the provided helper, which already "
            "handles the ftp->https scheme fix, per-file retries, size verification vs ENA fastq_bytes, "
            "and gunzip for you:\n"
            "  from genomeer.tools.function.sra import fetch_sra_reads\n"
            "  paths = fetch_sra_reads(acc, out_dir=os.getcwd())   # returns the local FASTQ file paths\n"
            "Only hand-roll the urllib recipe below if that import is unavailable in the current env.\n"
            "EXACT recipe (Python, run as a #!PY step):\n"
            "  import urllib.request, json, os, shutil\n"
            "  acc = 'SRR5926764'   # RUN accession (the 'Run' column from the NCBI runinfo lookup)\n"
            "  url = ('https://www.ebi.ac.uk/ena/portal/api/filereport?accession=' + acc +\n"
            "         '&result=read_run&fields=fastq_ftp,fastq_bytes,fastq_md5&format=json')\n"
            "  with urllib.request.urlopen(url, timeout=30) as r: rows = json.load(r)\n"
            "  if not rows or not rows[0].get('fastq_ftp'):\n"
            "      raise SystemExit(f'No ENA fastq files for {acc} (private/embargoed/not yet mirrored — '\n"
            "                       f'try a different run accession or re-verify the accession).')\n"
            "  ftp_urls = rows[0]['fastq_ftp'].split(';')      # 1 entry = single-end, 2 = paired (_1/_2)\n"
            "  https_urls = ['https://' + u.split('://')[-1] for u in ftp_urls]  # ftp -> https (this host's convention)\n"
            "  # ⚠ CRITICAL: download from https_urls, NEVER from the raw ftp_urls. ENA's fastq_ftp\n"
            "  #   values are SCHEME-LESS bare hosts ('ftp.sra.ebi.ac.uk/vol1/...'); passing one directly\n"
            "  #   to urllib.request.urlopen() raises \"unknown url type: 'ftp.sra.ebi.ac.uk...'\" and every\n"
            "  #   retry fails identically. The 'https://' + u.split('://')[-1] line above is MANDATORY,\n"
            "  #   and the download loop MUST iterate over https_urls (not ftp_urls).\n"
            "  total_bytes = sum(int(b) for b in rows[0].get('fastq_bytes', '0').split(';') if b)\n"
            "DISK GUARD (mandatory before downloading): check free disk (shutil.disk_usage) >= ~1.2x "
            "total_bytes; if not, STOP and report rather than filling the disk (a full disk breaks the "
            "whole machine — same rule as the BioProject download-size guard documented for the NCBI "
            "metadata-lookup tool). "
            "DOWNLOAD each URL with a STREAMED write (urllib.request.urlopen(...).read(chunk) in a loop, "
            "or urlretrieve) — files can be multi-GB, never load the whole response into memory with a "
            "single .read(). "
            "RESILIENCE: ENA can be slow/flaky under bursts — wrap each file download in a retry loop "
            "(max_retries attempts, short sleep between attempts), and treat a fully-exhausted retry as "
            "a hard failure (sys.exit with a clear message) — never silently continue with a partial or "
            "missing file. "
            "NAMING: single-end -> one file named after the accession (e.g. SRR5926764.fastq.gz); "
            "paired-end -> two files ending _1.fastq.gz / _2.fastq.gz. ENA already serves gzip-compressed "
            "FASTQ — do NOT regzip. "
            "VERIFY (default on): after download, compare each file's size on disk against the matching "
            "fastq_bytes entry; on mismatch, redownload that file once before giving up. "
            "This tool downloads REAL EXPERIMENTAL reads (not simulated) — always report the accession, "
            "layout (single/paired), and total size in the step summary so downstream steps and the "
            "final report can clearly distinguish real experimental data from any synthetic/simulated "
            "reads generated elsewhere in the pipeline."
        ),
        "required_parameters": [
            {"name": "accession", "type": "str", "description": "SRA/ENA RUN accession (e.g. 'SRR5926764', 'ERR1234567') — NOT a BioProject/Study accession."},
            {"name": "output_dir", "type": "str", "description": "Directory to write the downloaded FASTQ(.gz) file(s) into."},
        ],
        "optional_parameters": [
            {"name": "max_retries", "type": "int", "default": 3, "description": "Retry attempts per file on a network/timeout failure."},
            {"name": "verify_size", "type": "bool", "default": True, "description": "Compare downloaded file size against ENA-reported fastq_bytes; redownload once on mismatch."},
        ],
        "returns": "dict(accession, layout['single'|'paired'], fastq_r1, fastq_r2[optional], total_bytes, output_dir)"
    },
]
