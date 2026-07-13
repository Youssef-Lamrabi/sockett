"""
Genomeer — SRA/ENA read-download function.
============================================
Reference implementation matching genomeer.tools.description.sra.

NOTE (same convention as every other tools/function/*.py module): generated
scripts NEVER import this module directly (genomeer.* is not installed in the
execution envs — see instructions.py rule 1.a). The LLM reads the paired
description as a recipe and writes equivalent standalone code. This file exists
for documentation, discoverability, and unit testing of the exact logic the
description prescribes.

Uses the ENA (EBI) filereport REST API — no sra-tools / prefetch / fasterq-dump
binary dependency, no new conda package, works in any env (pure urllib).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

_ENA_FILEREPORT_URL = "https://www.ebi.ac.uk/ena/portal/api/filereport"
_CHUNK_SIZE = 1024 * 1024  # 1 MB streamed reads — never buffer a whole multi-GB file


def _ena_filereport(accession: str, timeout: int = 30) -> Dict[str, Any]:
    """Query ENA's filereport API for exact FASTQ download URLs + sizes."""
    url = (
        f"{_ENA_FILEREPORT_URL}?accession={accession}"
        "&result=read_run&fields=fastq_ftp,fastq_bytes,fastq_md5&format=json"
    )
    with urllib.request.urlopen(url, timeout=timeout) as r:
        rows = json.load(r)
    if not rows or not rows[0].get("fastq_ftp"):
        raise RuntimeError(
            f"No ENA fastq files for {accession} (private/embargoed/not yet mirrored — "
            "try a different run accession or verify with query_ncbi_entrez)."
        )
    return rows[0]


def _ftp_to_https(url: str) -> str:
    """ENA reports bare ftp.* hosts without a scheme, or ftp://; normalize to https://."""
    if "://" in url:
        return "https://" + url.split("://", 1)[1]
    return "https://" + url


def _download_stream(url: str, dest: Path, timeout: int = 120) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)


def _download_with_retry(url: str, dest: Path, max_retries: int = 3, timeout: int = 120) -> None:
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            _download_stream(url, dest, timeout=timeout)
            return
        except Exception as e:
            last_exc = e
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to download {url} after {max_retries} attempts: {last_exc}")


def fetch_sra_reads(
    accession: str,
    output_dir: str,
    max_retries: int = 3,
    verify_size: bool = True,
) -> Dict[str, Any]:
    """
    Download raw FASTQ reads for an SRA/ENA RUN accession (SRR/ERR/DRR) via
    ENA's filereport REST API + HTTPS — no sra-tools binary required.

    accession: SRA/ENA RUN accession (e.g. 'SRR5926764'). NOT a BioProject.
    output_dir: directory to write the downloaded FASTQ(.gz) file(s) into.
    Returns dict(accession, layout, fastq_r1, fastq_r2[optional], total_bytes, output_dir).
    """
    row = _ena_filereport(accession)
    ftp_urls: List[str] = [u for u in row["fastq_ftp"].split(";") if u]
    https_urls = [_ftp_to_https(u) for u in ftp_urls]
    byte_sizes = [int(b) for b in row.get("fastq_bytes", "").split(";") if b]
    total_bytes = sum(byte_sizes)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Disk guard — refuse to start a download that would fill the disk.
    free = shutil.disk_usage(str(out)).free
    if total_bytes and free < total_bytes * 1.2:
        raise RuntimeError(
            f"Insufficient disk space for {accession}: need ~{total_bytes / 1e9:.2f} GB "
            f"(x1.2 margin), only {free / 1e9:.2f} GB free at {out}."
        )

    layout = "paired" if len(https_urls) >= 2 else "single"
    dest_paths: List[Path] = []
    for i, url in enumerate(https_urls):
        suffix = f"_{i + 1}.fastq.gz" if layout == "paired" else ".fastq.gz"
        dest = out / f"{accession}{suffix}"
        _download_with_retry(url, dest, max_retries=max_retries)

        if verify_size and i < len(byte_sizes) and byte_sizes[i]:
            actual = dest.stat().st_size
            if actual != byte_sizes[i]:
                # one redownload attempt on size mismatch, then accept or fail
                _download_with_retry(url, dest, max_retries=1)
                actual = dest.stat().st_size
                if actual != byte_sizes[i]:
                    raise RuntimeError(
                        f"Size mismatch for {dest.name}: expected {byte_sizes[i]} bytes, "
                        f"got {actual} bytes after redownload."
                    )
        dest_paths.append(dest)

    result: Dict[str, Any] = {
        "accession": accession,
        "layout": layout,
        "total_bytes": total_bytes,
        "output_dir": str(out),
    }
    if layout == "paired":
        result["fastq_r1"] = str(dest_paths[0])
        result["fastq_r2"] = str(dest_paths[1])
    else:
        result["fastq_r1"] = str(dest_paths[0])
    return result
