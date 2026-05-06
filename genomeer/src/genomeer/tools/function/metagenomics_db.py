"""
Genomeer — Metagenomics Database Query Functions
==================================================
Unified access layer for key metagenomics databases.
Follows the Biomni pattern: each function accepts natural language or structured
parameters and returns structured data ready for downstream analysis.

Databases covered:
  - NCBI Taxonomy   : query_ncbi_taxonomy
  - SILVA           : query_silva_sequences  (16S/18S/23S rRNA)
  - GTDB            : query_gtdb_taxonomy
  - CARD            : query_card_resistance
  - MGnify          : query_mgnify_studies, query_mgnify_samples
  - UniProt         : query_uniprot_proteins
  - KEGG            : query_kegg_pathway, query_kegg_orthology
  - NCBI SRA        : fetch_sra_reads
  - VFDB            : download_vfdb
  - CAZy            : query_cazy_families
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from genomeer.agent.v2.utils.cache import get_cache as _get_cache
    _DB_CACHE = _get_cache()
except Exception:
    _DB_CACHE = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_with_retry(url: str, params: Optional[Dict] = None, timeout: int = 60) -> str:
    """HTTP GET via urllib with exponential backoff for 5xx/timeouts. No retry on 4xx."""
    import urllib.request
    import urllib.parse
    import urllib.error
    import time
    
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
        
    delays = [2, 4, 8]
    for attempt in range(len(delays) + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                import logging
                logging.getLogger("genomeer.db").warning(f"HTTP {e.code} for {url}: returning empty data.")
                return "{}"
            if attempt == len(delays):
                raise e
            time.sleep(delays[attempt])
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == len(delays):
                raise e
            time.sleep(delays[attempt])


def _download_file(url: str, dest: str, timeout: int = 3600) -> bool:
    """Download a file via urllib with basic verification."""
    import urllib.request
    import shutil
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, open(dest, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
        return True
    except Exception as e:
        import logging
        logging.getLogger("genomeer.db").error(f"Download failed for {url}: {e}")
        if os.path.exists(dest):
            os.remove(dest)
        return False


def _ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ===========================================================================
# NCBI TAXONOMY
# ===========================================================================

def query_ncbi_taxonomy(
    query: str,
    db: str = "taxonomy",
    retmax: int = 20,
    output_format: str = "json",
) -> Dict[str, Any]:
    """
    Query the NCBI Taxonomy database via Entrez API.
    Accepts a taxon name, NCBI taxonomy ID, or free-text search.
    Returns lineage, scientific name, rank, and associated genome counts.

    Examples:
      - query='Escherichia coli', db='taxonomy'
      - query='9606', db='taxonomy'  (human)
      - query='Bacteroidetes', db='taxonomy'
    """
    import urllib.request
    import urllib.parse

    # Cache check
    if _DB_CACHE:
        cached = _DB_CACHE.api.get(url="ncbi_taxonomy_search", params={"query": query, "db": db, "retmax": retmax})
        if cached:
            return cached

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    # Search step
    search_url = base + "esearch.fcgi"
    search_params = {"db": db, "term": query, "retmax": retmax, "retmode": "json"}
    search_resp = json.loads(_get_with_retry(search_url, search_params))
    ids = search_resp.get("esearchresult", {}).get("idlist", [])

    if not ids:
        return {"query": query, "db": db, "results": [], "n_found": 0}

    # Fetch step
    fetch_url = base + "efetch.fcgi"
    fetch_params = {"db": db, "id": ",".join(ids[:retmax]), "retmode": "xml"}
    xml_text = _get_with_retry(fetch_url, fetch_params)

    # Parse names from XML (TÂCHE 10/Flaw 3: Robust ElementTree parsing)
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
        results = []
        for taxon in root.findall(".//Taxon"):
            tid_node = taxon.find("TaxId")
            name_node = taxon.find("ScientificName")
            rank_node = taxon.find("Rank")
            
            results.append({
                "tax_id": tid_node.text if tid_node is not None else "0",
                "scientific_name": name_node.text if name_node is not None else "unknown",
                "rank": rank_node.text if rank_node is not None else "unknown",
            })
    except Exception as e:
        import logging
        logging.getLogger("genomeer.db").error(f"NCBI Taxonomy XML parsing failed: {e}")
        return {"query": query, "db": db, "results": [], "error": "XML parse error"}

    result = {"query": query, "db": db, "n_found": len(results), "results": results}
    
    # Cache save
    if _DB_CACHE and result:
        _DB_CACHE.api.set(url="ncbi_taxonomy_search", value=result, params={"query": query, "db": db, "retmax": retmax})
        
    return result


# ===========================================================================
# SILVA rRNA DATABASE
# ===========================================================================

def query_silva_sequences(
    query: str,
    target_region: str = "SSU",
    taxon_filter: Optional[str] = None,
    max_results: int = 10,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query the SILVA ribosomal RNA database via its REST API.
    SILVA is the gold standard for 16S (SSU) and 23S (LSU) rRNA classification.
    target_region: 'SSU' (16S/18S) or 'LSU' (23S/28S).
    Returns accessions, taxonomy, and optionally downloads sequences.
    """
    base_url = "https://www.arb-silva.de/api/v1/search/"
    params = {
        "query": query,
        "db": target_region.lower(),
        "page": 1,
        "page_size": max_results,
    }
    if taxon_filter:
        params["taxa"] = taxon_filter

    try:
        resp_text = _get_with_retry(base_url, params, timeout=30)
        data = json.loads(resp_text)
        results = data.get("results", [])
    except Exception as e:
        # Fallback: return metadata about SILVA without live query
        results = []
        return {
            "query": query,
            "target_region": target_region,
            "source": "SILVA rRNA database",
            "note": f"Live query failed ({e}). Download SILVA from https://www.arb-silva.de/download/arb-files/",
            "recommended_files": {
                "SILVA_138_SSURef_NR99_tax_silva.fasta.gz": "Non-redundant 16S/18S reference sequences",
                "SILVA_138_LSURef_NR99_tax_silva.fasta.gz": "Non-redundant 23S/28S reference sequences",
                "tax_slv_ssu_138.txt": "SSU taxonomy mapping file",
            },
            "kraken2_integration": "Use 'kraken2-build --special silva' or download pre-built index",
            "results": [],
        }

    return {
        "query": query,
        "target_region": target_region,
        "n_results": len(results),
        "results": results[:max_results],
    }


def download_silva_database(
    output_dir: str,
    region: str = "SSU",
    release: str = "138.2",
    subset: str = "NR99",
) -> Dict[str, Any]:
    """
    Download the SILVA rRNA reference database for use with Kraken2 or QIIME2.
    region: 'SSU' (16S/18S) or 'LSU' (23S/28S).
    subset: 'NR99' (non-redundant 99%) or 'Ref' (full reference set).
    Returns dict with local paths to FASTA and taxonomy files.
    """
    out = _ensure_dir(output_dir)
    base = f"https://www.arb-silva.de/fileadmin/silva_databases/release_{release}/Exports/"

    fasta_file = f"SILVA_{release}_{region}Ref_{subset}_tax_silva.fasta.gz"
    tax_file = f"tax_slv_{region.lower()}_{release}.txt"

    fasta_url = base + fasta_file
    tax_url = base + tax_file

    results = {"output_dir": str(out), "files": {}}

    for url, fname in [(fasta_url, fasta_file), (tax_url, tax_file)]:
        dest = str(out / fname)
        if not Path(dest).exists():
            success = _download_file(url, dest)
            if not success:
                results["files"][fname] = f"FAILED: Download error"
            else:
                results["files"][fname] = dest
        else:
            results["files"][fname] = dest

    return results


# ===========================================================================
# GTDB TAXONOMY
# ===========================================================================

def query_gtdb_taxonomy(
    query: str,
    search_type: str = "all",
    max_results: int = 20,
) -> Dict[str, Any]:
    """
    Query the GTDB (Genome Taxonomy Database) API for genome taxonomy information.
    GTDB provides phylogenomics-based taxonomy for bacteria and archaea.
    search_type: 'all', 'ncbi', 'gtdb', 'species_cluster', 'strain'.
    Returns GTDB lineage, NCBI accession, species cluster assignment.
    """
    base_url = "https://gtdb.ecogenomic.org/api/v2/taxonomy/search"
    params = {"search": query, "limit": max_results}

    try:
        resp = _get_with_retry(base_url, params, timeout=30)
        data = json.loads(resp)
        rows = data.get("rows", []) if isinstance(data, dict) else data
        return {
            "query": query,
            "n_results": len(rows),
            "results": rows[:max_results],
            "source": "GTDB r220",
        }
    except Exception as e:
        return {
            "query": query,
            "source": "GTDB — Genome Taxonomy Database",
            "note": f"Live query failed ({e}). Browse at https://gtdb.ecogenomic.org",
            "download": "https://gtdb.ecogenomic.org/downloads",
            "gtdbtk_classify": "Use run_gtdbtk() to classify your MAGs against GTDB",
        }


# ===========================================================================
# CARD — ANTIMICROBIAL RESISTANCE DATABASE
# ===========================================================================

def query_card_resistance(
    query: str,
    search_type: str = "gene_name",
    max_results: int = 20,
) -> Dict[str, Any]:
    """
    Query the CARD (Comprehensive Antibiotic Resistance Database) for resistance genes.
    search_type: 'gene_name', 'antibiotic', 'mechanism', 'drug_class', 'organism'.
    Returns gene name, drug class, resistance mechanism, and AMR family.
    """
    base_url = "https://card.mcmaster.ca/search"
    params = {"q": query, "source": "external"}

    try:
        resp = _get_with_retry(base_url, params, timeout=30)
        # CARD returns HTML; extract JSON if present or return metadata
        if "application/json" in resp[:50]:
            data = json.loads(resp)
            return {"query": query, "results": data, "source": "CARD"}
    except Exception:
        pass

    # Return structured metadata about CARD for agent use
    return {
        "query": query,
        "source": "CARD — Comprehensive Antibiotic Resistance Database",
        "version": "CARD 3.3.0",
        "web_search": f"https://card.mcmaster.ca/search?q={query}",
        "download": "https://card.mcmaster.ca/download",
        "local_analysis": "Use run_rgi_card() to analyze sequences against CARD locally",
        "note": (
            "CARD contains >6,000 reference sequences for resistance genes. "
            "For programmatic access, download card.json and use RGI."
        ),
    }


def download_card_database(output_dir: str) -> Dict[str, Any]:
    """
    Download the CARD database for use with RGI (Resistance Gene Identifier).
    Downloads card.json and the RGI-compatible database package.
    Returns dict with card_json path.
    """
    out = _ensure_dir(output_dir)
    card_url = "https://card.mcmaster.ca/latest/data"
    card_tar = str(out / "card_data.tar.bz2")

    success = _download_file(card_url, card_tar)
    if not success:
        return {"status": "failed", "error": "Download error"}

    subprocess.run(["tar", "-xjf", card_tar, "-C", str(out)], check=True)
    card_json = str(out / "card.json")

    return {
        "card_json": card_json if Path(card_json).exists() else str(out),
        "output_dir": str(out),
        "status": "downloaded",
    }


# ===========================================================================
# MGnify — METAGENOMICS PORTAL (EBI)
# ===========================================================================

def query_mgnify_studies(
    query: Optional[str] = None,
    biome: Optional[str] = None,
    experiment_type: str = "metagenomic",
    max_results: int = 20,
) -> Dict[str, Any]:
    """
    Query the MGnify API (EBI Metagenomics Portal) to search publicly available
    metagenomic studies by biome, keywords, or experiment type.
    biome examples: 'root:Environmental:Terrestrial:Soil',
                    'root:Host-associated:Human:Gut'.
    experiment_type: 'metagenomic', 'metatranscriptomic', 'amplicon', 'assembly'.
    Returns study accessions, names, biomes, and sample counts.
    """
    base_url = "https://www.ebi.ac.uk/metagenomics/api/v1/studies"
    params = {"experiment_type": experiment_type, "page_size": max_results}
    if query:
        params["search"] = query
    if biome:
        params["lineage"] = biome

    try:
        resp = _get_with_retry(base_url, params, timeout=30)
        data = json.loads(resp)
        results = data.get("data", [])
        return {
            "query": query,
            "biome": biome,
            "n_results": len(results),
            "results": [
                {
                    "accession": r.get("id"),
                    "name": r.get("attributes", {}).get("study-name", ""),
                    "biome": r.get("attributes", {}).get("biome-id", ""),
                    "samples_count": r.get("attributes", {}).get("samples-count", 0),
                }
                for r in results[:max_results]
            ],
            "source": "MGnify API v1",
        }
    except Exception as e:
        return {
            "query": query,
            "source": "MGnify — EBI Metagenomics Portal",
            "note": f"Query failed ({e}). Browse at https://www.ebi.ac.uk/metagenomics",
            "api_docs": "https://www.ebi.ac.uk/metagenomics/api/v1/",
        }


def query_mgnify_samples(
    study_accession: str,
    max_results: int = 50,
) -> Dict[str, Any]:
    """
    Retrieve samples and associated metadata from a specific MGnify study.
    study_accession: MGnify study ID (e.g., 'MGYS00005116').
    Returns sample accessions, metadata, and download URLs for analysis results.
    """
    base_url = f"https://www.ebi.ac.uk/metagenomics/api/v1/studies/{study_accession}/samples"
    params = {"page_size": max_results}

    try:
        resp = _get_with_retry(base_url, params, timeout=30)
        data = json.loads(resp)
        results = data.get("data", [])
        return {
            "study_accession": study_accession,
            "n_samples": len(results),
            "samples": [
                {
                    "accession": r.get("id"),
                    "biome": r.get("attributes", {}).get("biome-id", ""),
                    "geo_loc": r.get("attributes", {}).get("geo-loc-name", ""),
                    "sample_name": r.get("attributes", {}).get("sample-name", ""),
                }
                for r in results[:max_results]
            ],
        }
    except Exception as e:
        return {"study_accession": study_accession, "error": str(e)}


# ===========================================================================
# NCBI SRA — SEQUENCE READ ARCHIVE
# ===========================================================================

def fetch_sra_reads(
    accession: str,
    output_dir: str,
    threads: int = 4,
    max_size: str = "20G",
    split_files: bool = True,
) -> Dict[str, Any]:
    """
    Download raw sequencing reads from the NCBI Sequence Read Archive (SRA).
    Uses fasterq-dump for fast parallel download and FASTQ conversion.
    accession: SRA run accession (e.g., 'SRR5926764', 'ERR1234567').
    Returns dict with fastq_r1, fastq_r2 (paired) or fastq_single paths.
    """
    out = _ensure_dir(output_dir)

    # Step 1: prefetch
    prefetch_dir = str(out / "prefetch")
    _ensure_dir(prefetch_dir)
    proc_pre = subprocess.run(
        ["prefetch", accession, "-O", prefetch_dir, "--max-size", max_size],
        capture_output=True, text=True, timeout=7200
    )
    if proc_pre.returncode != 0:
        # Try direct fasterq-dump without prefetch
        pass

    # Step 2: fasterq-dump
    cmd = ["fasterq-dump", accession, "-O", str(out),
           "--threads", str(threads), "--temp", str(out / "tmp")]
    if split_files:
        cmd += ["--split-files"]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                          cwd=str(prefetch_dir) if Path(prefetch_dir).exists() else str(out))
    if proc.returncode != 0:
        return {
            "accession": accession,
            "status": "failed",
            "error": proc.stderr[:500],
            "note": "Ensure sra-tools is installed: conda install -c bioconda sra-tools",
        }

    fastq_files = sorted(Path(out).glob(f"{accession}*.fastq"))
    r1 = next((str(f) for f in fastq_files if "_1.fastq" in f.name or f.name == f"{accession}.fastq"), None)
    r2 = next((str(f) for f in fastq_files if "_2.fastq" in f.name), None)

    return {
        "accession": accession,
        "fastq_r1": r1,
        "fastq_r2": r2,
        "all_files": [str(f) for f in fastq_files],
        "output_dir": str(out),
    }


# ===========================================================================
# UNIPROT — PROTEIN DATABASE
# ===========================================================================

def query_uniprot_proteins(
    query: str,
    database: str = "uniprotkb",
    max_results: int = 20,
    reviewed_only: bool = False,
    output_fields: str = "accession,id,protein_name,organism_name,length,sequence",
) -> Dict[str, Any]:
    """
    Query UniProt (UniRef, UniProtKB, UniParc) for protein information.
    Useful for functional annotation of predicted ORFs or MAG proteins.
    database: 'uniprotkb', 'uniref90', 'uniref50', 'uniparc'.
    reviewed_only: True to restrict to Swiss-Prot curated entries.
    Returns accession, protein name, organism, length, and sequence.
    """
    base_url = f"https://rest.uniprot.org/{database}/search"
    params = {
        "query": query + (" AND (reviewed:true)" if reviewed_only else ""),
        "format": "json",
        "size": max_results,
        "fields": output_fields,
    }

    try:
        resp = _get_with_retry(base_url, params, timeout=30)
        data = json.loads(resp)
        results = data.get("results", [])
        return {
            "query": query,
            "database": database,
            "n_results": len(results),
            "results": results[:max_results],
            "source": "UniProt REST API",
        }
    except Exception as e:
        return {
            "query": query,
            "source": "UniProt",
            "note": f"Query failed ({e}). Browse at https://www.uniprot.org",
        }


# ===========================================================================
# KEGG — METABOLIC PATHWAYS
# ===========================================================================

def query_kegg_pathway(
    pathway_id: str,
) -> Dict[str, Any]:
    """
    Query the KEGG REST API for information about a metabolic pathway.
    pathway_id: KEGG pathway ID (e.g., 'ko00010' for Glycolysis,
                                   'map00190' for Oxidative phosphorylation).
    Returns pathway name, associated genes/KOs, and organisms.
    """
    base_url = f"https://rest.kegg.jp/get/{pathway_id}"
    try:
        resp = _get_with_retry(base_url, timeout=30)
        lines = resp.splitlines()
        info: Dict[str, Any] = {"pathway_id": pathway_id, "source": "KEGG REST API"}
        section = None
        genes = []
        for line in lines:
            if line.startswith("NAME"):
                info["name"] = line.replace("NAME", "").strip()
            elif line.startswith("DESCRIPTION"):
                info["description"] = line.replace("DESCRIPTION", "").strip()
            elif line.startswith("CLASS"):
                info["class"] = line.replace("CLASS", "").strip()
            elif line.startswith("GENE"):
                section = "GENE"
                genes.append(line.replace("GENE", "").strip())
            elif section == "GENE" and line.startswith(" "):
                genes.append(line.strip())
            elif not line.startswith(" "):
                section = None
        info["genes"] = genes[:50]
        return info
    except Exception as e:
        return {"pathway_id": pathway_id, "error": str(e), "source": "KEGG"}


def query_kegg_orthology(
    ko_id: str,
) -> Dict[str, Any]:
    """
    Query the KEGG REST API for information about a KEGG Orthology (KO) entry.
    KO IDs are used by HUMAnN3 and KofamKOALA for functional annotation.
    ko_id: KO identifier (e.g., 'K00844' for hexokinase).
    Returns gene name, definition, pathway associations, and EC number.
    """
    base_url = f"https://rest.kegg.jp/get/{ko_id}"
    try:
        resp = _get_with_retry(base_url, timeout=30)
        lines = resp.splitlines()
        info: Dict[str, Any] = {"ko_id": ko_id, "source": "KEGG REST API"}
        for line in lines:
            if line.startswith("NAME"):
                info["name"] = line.replace("NAME", "").strip()
            elif line.startswith("DEFINITION"):
                info["definition"] = line.replace("DEFINITION", "").strip()
            elif line.startswith("PATHWAY"):
                info["pathway"] = line.replace("PATHWAY", "").strip()
            elif line.startswith("BRITE"):
                info["brite"] = line.replace("BRITE", "").strip()
        return info
    except Exception as e:
        return {"ko_id": ko_id, "error": str(e)}


# ===========================================================================
# VFDB — VIRULENCE FACTOR DATABASE
# ===========================================================================

def download_vfdb(
    output_dir: str,
    subset: str = "core",
) -> Dict[str, Any]:
    """
    Download the VFDB (Virulence Factor Database) for local virulence gene detection.
    subset: 'core' (core dataset) or 'full' (full dataset with hypothetical proteins).
    Returns dict with fasta_path and info_tsv path.
    """
    out = _ensure_dir(output_dir)
    urls = {
        "core": "http://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz",
        "full": "http://www.mgc.ac.cn/VFs/Down/VFDB_setB_pro.fas.gz",
    }
    url = urls.get(subset, urls["core"])
    fname = f"VFDB_{subset}_proteins.fas.gz"
    dest = str(out / fname)

    proc = subprocess.run(["wget", "-q", "-O", dest, url],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        return {"status": "failed", "error": proc.stderr[:300],
                "manual_url": "http://www.mgc.ac.cn/VFs/main.htm"}

    # Decompress
    decompressed = dest.replace(".gz", "")
    subprocess.run(["gunzip", "-f", dest], check=False)

    return {
        "fasta_path": decompressed if Path(decompressed).exists() else dest,
        "subset": subset,
        "usage": "Use run_diamond(mode='blastp', db_path=<diamond_db>) after building: diamond makedb --in vfdb.fas -d vfdb",
    }


# ===========================================================================
# CAZy — CARBOHYDRATE-ACTIVE ENZYMES DATABASE
# ===========================================================================

def query_cazy_families(
    query: str,
    family_type: Optional[str] = None,
    max_results: int = 20,
) -> Dict[str, Any]:
    """
    Query the CAZy database for carbohydrate-active enzyme families.
    Useful for soil/environmental metagenomics to detect carbon cycling genes.
    family_type: 'GH' (glycoside hydrolases), 'GT' (glycosyltransferases),
                 'PL' (polysaccharide lyases), 'CE' (carbohydrate esterases),
                 'CBM' (carbohydrate-binding modules), 'AA' (auxiliary activities).
    Returns family IDs, activities, and substrate information.
    """
    # CAZy doesn't have a public REST API; return curated metadata + DIAMOND approach
    families = {
        "GH": "Glycoside Hydrolases — cleave glycosidic bonds (cellulose, starch degradation)",
        "GT": "Glycosyltransferases — form glycosidic bonds (cell wall biosynthesis)",
        "PL": "Polysaccharide Lyases — cleave uronic acid-containing polysaccharides",
        "CE": "Carbohydrate Esterases — remove ester substituents from polysaccharides",
        "CBM": "Carbohydrate-Binding Modules — non-catalytic modules aiding substrate binding",
        "AA": "Auxiliary Activities — redox enzymes acting on lignocellulose",
    }

    result = {
        "query": query,
        "source": "CAZy Database",
        "web": f"http://www.cazy.org/search?ps=20&debut_R=0&lang=en&S={query}",
        "families_overview": families,
        "recommended_approach": (
            "1. Download dbCAN HMM profiles: https://bcb.unl.edu/dbCAN2/download/Databases/"
            "\n2. Use run_hmmer(program='hmmscan', hmm_db='dbCAN.hmm') on predicted proteins"
            "\n3. Or use run_diamond with CAZy sequences as database"
        ),
    }

    if family_type and family_type in families:
        result["selected_family"] = {
            "type": family_type,
            "description": families[family_type],
            "download_url": f"http://www.cazy.org/{family_type}.html",
        }

    return result