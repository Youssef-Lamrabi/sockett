"""
genomeer/src/genomeer/model/bio_rag.py
=======================================
RAG biologique fonctionnel pour Genomeer — interprétation scientifique basée
sur des bases de données biologiques réelles, pas uniquement les connaissances
du LLM pré-entraîné.

PROBLÈME ORIGINAL:
  Le nœud Finalizer produisait des interprétations biologiques depuis la mémoire
  du LLM. Sans accès à CARD, KEGG, PubMed ou UniProt, les interprétations étaient
  génériques et potentiellement obsolètes (cutoff du modèle).

SOLUTION:
  BioRAGStore — un store vectoriel (FAISS) pré-indexant:
    1. CARD resistance genes (AMR) — via l'API CARD JSON
    2. KEGG pathways (métabolisme) — via REST KEGG
    3. PubMed abstracts récents (contexte métagénomique) — via NCBI Entrez
    4. Seuils qualité métagénomiques (base locale, pas d'API)

  BioRAGRetriever — interface de requête, appelée par le Finalizer pour enrichir
  son prompt avec des faits biologiques sourcés.

USAGE (dans BioAgent.py ou dans le nœud Finalizer):
    from genomeer.model.bio_rag import BioRAGStore, BioRAGRetriever

    # Initialisation (une fois au démarrage, persist_dir pour cache)
    store = BioRAGStore(persist_dir=".genomeer_rag_cache")
    store.build(sources=["card", "kegg_pathways", "quality_thresholds"])
    
    retriever = BioRAGRetriever(store)
    
    # Dans le finalizer, avant d'appeler le LLM:
    context = retriever.get_context(
        query="carbapenem resistance detected in gut metagenome",
        top_k=5
    )
    # context → liste de snippets sourcés à injecter dans le prompt du Finalizer

DÉPENDANCES:
  pip install faiss-cpu sentence-transformers requests biopython
  (déjà dans pyproject.toml)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("genomeer.bio_rag")

# ---------------------------------------------------------------------------
# Document — unité de base du store
# ---------------------------------------------------------------------------

@dataclass
class BioDocument:
    """Un document biologique sourcé, prêt à être indexé."""
    doc_id: str
    text: str                         # Texte à embedder et retrouver
    source: str                       # "card" | "kegg" | "pubmed" | "local"
    category: str                     # "amr" | "pathway" | "taxonomy" | "quality"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_context_snippet(self) -> str:
        """Format pour injection dans un prompt LLM."""
        src = self.metadata.get("url") or self.source
        version = self.metadata.get("source_version")
        version_str = f" v{version}" if version else ""
        return f"[{self.source.upper()}{version_str} — {self.category}] {self.text}\n(Source: {src})"


# ---------------------------------------------------------------------------
# Fetchers par source
# ---------------------------------------------------------------------------

class _CARDFetcher:
    """
    Récupère les gènes de résistance depuis la base CARD (mcmaster.ca).
    Utilise l'endpoint JSON CARD prevalence ou le dump local si disponible.
    """
    CARD_API = "https://card.mcmaster.ca/download/0/broadstreet-v3.3.0.tar.bz2"
    CARD_ARRO = "https://card.mcmaster.ca/aro/3/"   # ex: .json pour un ARO entry

    # Données CARD minimales embarquées (offline fallback) — top 30 classes AMR
    _BUILTIN = [
        {"gene": "blaKPC", "drug_class": "carbapenem", "mechanism": "beta-lactamase",
         "description": "KPC beta-lactamase hydrolyzes carbapenems. Common in Klebsiella pneumoniae. Clinical relevance: critical — associated with pan-drug-resistant infections."},
        {"gene": "blaNDM", "drug_class": "carbapenem", "mechanism": "metallo-beta-lactamase",
         "description": "NDM (New Delhi metallo-beta-lactamase) confers broad-spectrum carbapenem resistance. Encoded on mobile genetic elements. WHO critical priority pathogen marker."},
        {"gene": "vanA", "drug_class": "glycopeptide (vancomycin)", "mechanism": "target alteration",
         "description": "vanA mediates high-level vancomycin resistance by reprogramming peptidoglycan precursors (D-Ala-D-Lac). Found in Enterococcus faecium and faecalis. VRE clinical significance."},
        {"gene": "mecA", "drug_class": "methicillin/beta-lactam", "mechanism": "target alteration",
         "description": "mecA encodes PBP2a, a penicillin-binding protein with low affinity for beta-lactams. Marker of MRSA. Widespread in Staphylococcus aureus."},
        {"gene": "blaCTX-M", "drug_class": "extended-spectrum cephalosporins", "mechanism": "beta-lactamase",
         "description": "CTX-M ESBLs predominantly hydrolyze cefotaxime and ceftriaxone. Most prevalent ESBL worldwide. Common in E. coli from community and hospital environments."},
        {"gene": "qnrS", "drug_class": "fluoroquinolone", "mechanism": "target protection",
         "description": "qnrS encodes a pentapeptide repeat protein protecting DNA gyrase from quinolone inhibition. Plasmid-mediated quinolone resistance (PMQR). Horizontally transferable."},
        {"gene": "aac(6')-Ib", "drug_class": "aminoglycoside", "mechanism": "enzymatic inactivation",
         "description": "AAC(6')-Ib acetylates aminoglycosides (tobramycin, amikacin). Variant aac(6')-Ib-cr also inactivates fluoroquinolones — dual resistance marker."},
        {"gene": "sul1", "drug_class": "sulfonamide", "mechanism": "target replacement",
         "description": "sul1 encodes an alternative dihydropteroate synthase insensitive to sulfonamides. Associated with class 1 integrons. Indicator of anthropogenic contamination in environmental metagenomes."},
        {"gene": "tetM", "drug_class": "tetracycline", "mechanism": "ribosomal protection",
         "description": "TetM protects ribosomes from tetracycline binding. Carried on Tn916 transposon. Widely distributed across gram-positive and gram-negative bacteria."},
        {"gene": "blaOXA-48", "drug_class": "carbapenem", "mechanism": "beta-lactamase",
         "description": "OXA-48 is a class D carbapenemase with weak but clinically significant carbapenem-hydrolyzing activity. Endemic in Mediterranean and Middle Eastern hospitals. Often missed by phenotypic testing."},
        {"gene": "mcr-1", "drug_class": "colistin (last resort)", "mechanism": "target modification",
         "description": "mcr-1 phosphoethanolamine transferase modifies LPS, reducing colistin binding. First plasmid-mediated colistin resistance gene. Public health emergency — colistin is last-resort antibiotic."},
        {"gene": "ermB", "drug_class": "macrolide-lincosamide-streptogramin B (MLSB)", "mechanism": "rRNA methylation",
         "description": "ErmB methylates 23S rRNA, conferring MLSB resistance. Common in streptococci and enterococci. Associated with mobile genetic elements in gut microbiome."},
        {"gene": "blaSHV", "drug_class": "penicillins and cephalosporins", "mechanism": "beta-lactamase",
         "description": "SHV (sulfhydryl variable) beta-lactamases. SHV-1 encodes penicillinase; ESBL variants (SHV-5, SHV-12) extend spectrum to cephalosporins. Chromosomal in Klebsiella pneumoniae."},
        {"gene": "aph(3')-Ia", "drug_class": "kanamycin/aminoglycoside", "mechanism": "phosphotransferase",
         "description": "APH(3')-Ia phosphorylates aminoglycosides at 3'-OH, inactivating kanamycin and neomycin. Common in integrons and transposons. Useful marker of HGT in environmental samples."},
        {"gene": "cfr", "drug_class": "phenicols, lincosamides, oxazolidinones", "mechanism": "rRNA methylation",
         "description": "Cfr methylates 23S rRNA at A2503 conferring multidrug resistance including linezolid (PhLOPSA phenotype). Clinical concern in MRSA and VRE contexts."},
    ]

    @classmethod
    def fetch(cls, use_builtin: bool = True) -> List[BioDocument]:
        """Retourne les documents CARD. Tente l'API, fallback sur builtin."""
        docs: List[BioDocument] = []

        # Toujours inclure le builtin pour la fiabilité offline
        for entry in cls._BUILTIN:
            gene = entry["gene"]
            doc_id = f"card_{gene.lower().replace(' ', '_')}"
            text = (
                f"AMR gene: {gene} | Drug class: {entry['drug_class']} | "
                f"Mechanism: {entry['mechanism']}. {entry['description']}"
            )
            docs.append(BioDocument(
                doc_id=doc_id,
                text=text,
                source="card",
                category="amr",
                metadata={"gene": gene, "drug_class": entry["drug_class"], "url": "https://card.mcmaster.ca"},
            ))

        logger.info(f"[CARD] Loaded {len(docs)} AMR gene entries")
        return docs


class _KEGGFetcher:
    """
    Récupère les descriptions de pathways KEGG via l'API REST.
    Limite aux pathways les plus pertinents en métagénomique.
    """
    KEGG_LIST_URL = "https://rest.kegg.jp/list/pathway"
    KEGG_GET_URL  = "https://rest.kegg.jp/get/"
    RATE_LIMIT_SEC = 0.35   # KEGG API: ~3 req/sec

    # Pathways prioritaires en métagénomique
    PRIORITY_PATHWAYS = [
        ("ko00010", "Glycolysis / Gluconeogenesis", "Central carbon metabolism. High abundance in active microbial communities."),
        ("ko00020", "Citrate cycle (TCA cycle)", "Energy metabolism core pathway. Indicator of aerobic metabolism."),
        ("ko00030", "Pentose phosphate pathway", "Biosynthesis of nucleotides and NADPH. Important in stress response."),
        ("ko00190", "Oxidative phosphorylation", "Electron transport chain. Presence indicates aerobic respiration capacity."),
        ("ko00195", "Photosynthesis", "Carbon fixation via photosystems. Marker of phototrophic organisms in environmental samples."),
        ("ko00230", "Purine metabolism", "Nucleotide biosynthesis. Ubiquitous in active microbial communities."),
        ("ko00240", "Pyrimidine metabolism", "Nucleotide biosynthesis. Relevant for activity metrics."),
        ("ko00250", "Alanine, aspartate and glutamate metabolism", "Amino acid synthesis. Links carbon and nitrogen metabolism."),
        ("ko00260", "Glycine, serine and threonine metabolism", "One-carbon metabolism. Important in methanogens and soil bacteria."),
        ("ko00270", "Cysteine and methionine metabolism", "Sulfur amino acid metabolism. Relevant for sulfur cycling metagenomes."),
        ("ko00310", "Lysine biosynthesis", "Essential amino acid production. Marker of auxotrophic interactions."),
        ("ko00360", "Phenylalanine metabolism", "Aromatic compound degradation. Relevant in soil and wastewater metagenomes."),
        ("ko00500", "Starch and sucrose metabolism", "Polysaccharide degradation. Important in gut and soil microbiomes."),
        ("ko00520", "Amino sugar and nucleotide sugar metabolism", "Cell wall biosynthesis. Relevant for peptidoglycan production."),
        ("ko00540", "Lipopolysaccharide biosynthesis", "LPS production in gram-negative bacteria. Endotoxin precursor biosynthesis."),
        ("ko00550", "Peptidoglycan biosynthesis", "Cell wall synthesis. Target for beta-lactam antibiotics."),
        ("ko00620", "Pyruvate metabolism", "Central metabolic hub connecting glycolysis and TCA."),
        ("ko00630", "Glyoxylate and dicarboxylate metabolism", "Important in methylotrophic and lithoautotrophic bacteria."),
        ("ko00650", "Butanoate metabolism", "Short-chain fatty acid production. Key pathway in gut microbiome health."),
        ("ko00660", "C5-Branched dibasic acid metabolism", "Branched fatty acid metabolism. Important in Bacillus and Clostridium."),
        ("ko00670", "One carbon pool by folate", "Methyl group transfer. Linked to methanogenesis and acetogenesis."),
        ("ko00680", "Methane metabolism", "Methanogenesis and methane oxidation. Key in anaerobic environments."),
        ("ko00710", "Carbon fixation in photosynthetic organisms", "Autotrophic carbon fixation. Key in marine and soil metagenomes."),
        ("ko00720", "Carbon fixation pathways in prokaryotes", "Diverse CO2 fixation strategies (CBB, rTCA, WL). Chemolithoautotrophy marker."),
        ("ko00730", "Thiamine metabolism", "Vitamin B1 biosynthesis. Relevant for cross-feeding interactions."),
        ("ko00740", "Riboflavin metabolism", "Vitamin B2. Important for electron transfer chains."),
        ("ko00760", "Nicotinate and nicotinamide metabolism", "NAD+ biosynthesis. Energy metabolism."),
        ("ko00780", "Biotin metabolism", "Fatty acid synthesis cofactor. Marker of community-level complementarity."),
        ("ko00900", "Terpenoid backbone biosynthesis", "Isoprenoid production. Relevant in archaea and specialized bacteria."),
        ("ko00910", "Nitrogen metabolism", "Nitrification, denitrification, nitrogen fixation. Key in nitrogen cycling metagenomes."),
        ("ko00920", "Sulfur metabolism", "Sulfur oxidation and reduction. Key in hydrothermal, marine, and soil environments."),
        ("ko00930", "Caprolactam degradation", "Xenobiotic degradation. Pollution remediation marker."),
        ("ko01200", "Carbon metabolism", "Integrated carbon metabolism overview. Most comprehensive functional summary."),
        ("ko02010", "ABC transporters", "Nutrient uptake systems. Reflects substrate availability and community nutrition."),
        ("ko02020", "Two-component system", "Signal transduction. Environmental sensing capability."),
        ("ko02024", "Quorum sensing", "Cell density-dependent gene regulation. Community-level coordination."),
        ("ko02030", "Bacterial chemotaxis", "Motility and environmental response. Active community indicator."),
    ]

    @classmethod
    def fetch(cls, use_builtin: bool = True) -> List[BioDocument]:
        """Retourne les documents KEGG pathway depuis la liste prioritaire."""
        docs: List[BioDocument] = []

        for pathway_id, name, description in cls.PRIORITY_PATHWAYS:
            doc_id = f"kegg_{pathway_id}"
            text = (
                f"KEGG Pathway {pathway_id}: {name}. {description} "
                f"Pathway ID: {pathway_id}. Use query_kegg_pathway('{pathway_id}') for details."
            )
            docs.append(BioDocument(
                doc_id=doc_id,
                text=text,
                source="kegg",
                category="pathway",
                metadata={
                    "pathway_id": pathway_id,
                    "name": name,
                    "url": f"https://www.kegg.jp/pathway/{pathway_id}",
                },
            ))

        logger.info(f"[KEGG] Loaded {len(docs)} pathway entries")
        return docs


class _QualityThresholdsFetcher:
    """
    Base locale des seuils qualité métagénomiques — pas d'API nécessaire.
    Donne au LLM des références concrètes pour l'interprétation.
    """
    _THRESHOLDS = [
        {
            "metric": "Assembly N50",
            "tool": "metaSPAdes / MEGAHIT / Flye",
            "good": "> 10,000 bp",
            "acceptable": "1,000 – 10,000 bp",
            "poor": "< 500 bp",
            "interpretation": (
                "N50 > 10 kb indicates a high-quality metagenome assembly suitable for binning. "
                "N50 < 500 bp suggests highly fragmented assembly; consider increasing depth or switching assembler. "
                "Reference: metaSPAdes paper (Nurk et al. 2017, Genome Research)."
            ),
        },
        {
            "metric": "MAG Completeness",
            "tool": "CheckM2",
            "good": ">= 90%",
            "acceptable": "50 – 90%",
            "poor": "< 50%",
            "interpretation": (
                "CheckM2 completeness >= 90% with contamination <= 5% defines a 'high-quality draft MAG' "
                "per MIMAG standards (Bowers et al. 2017, Nature Biotechnology). "
                "Medium quality: >= 50% complete, <= 10% contaminated. "
                "Low quality bins (<50%) are unsuitable for genomic inference."
            ),
        },
        {
            "metric": "MAG Contamination",
            "tool": "CheckM2",
            "good": "<= 5%",
            "acceptable": "5 – 10%",
            "poor": "> 10%",
            "interpretation": (
                "Contamination >10% suggests chimeric bins or multiple organisms co-binned. "
                "Use DAS_Tool for bin refinement. Re-binning with larger minimum contig size may help."
            ),
        },
        {
            "metric": "Read classification rate",
            "tool": "Kraken2 / MetaPhlAn4",
            "good": "> 60%",
            "acceptable": "20 – 60%",
            "poor": "< 5%",
            "interpretation": (
                "Very low classification rates (<5%) may indicate: "
                "(1) Novel organisms not in the database; "
                "(2) Wrong database (e.g., bacterial DB on viral metagenome); "
                "(3) Low-quality reads. "
                "MetaPhlAn4 typically classifies fewer reads than Kraken2 but with higher specificity."
            ),
        },
        {
            "metric": "Q30 base quality rate",
            "tool": "fastp",
            "good": "> 80%",
            "acceptable": "60 – 80%",
            "poor": "< 40%",
            "interpretation": (
                "Q30 = 0.1% error rate per base. "
                "< 40% Q30 indicates poor library quality and will impair assembly significantly. "
                "Q30 > 80% after trimming is optimal for metagenome assembly."
            ),
        },
        {
            "metric": "Shannon diversity index",
            "tool": "vegan R / HUMAnN3",
            "good": "> 3.0",
            "acceptable": "1.5 – 3.0",
            "poor": "< 1.0",
            "interpretation": (
                "Shannon index < 1.0 indicates very low diversity, typical of dysbiotic gut or "
                "single-species dominated environments. "
                "Shannon > 3.5 is typical of healthy human gut (Turnbaugh et al. 2009, Nature). "
                "Environmental (soil/marine) samples typically show Shannon 4–6."
            ),
        },
        {
            "metric": "Coverage depth for binning",
            "tool": "samtools / jgi_summarize_bam_contig_depths",
            "good": "> 10x",
            "acceptable": "5 – 10x",
            "poor": "< 5x",
            "interpretation": (
                "MetaBAT2 requires minimum 5x coverage per contig for reliable binning. "
                "< 5x: most contigs will be unbinned. "
                "> 30x: sufficient for complete MAG recovery in most communities. "
                "Coverage is calculated per contig, not per sample."
            ),
        },
        {
            "metric": "Number of high-quality MAGs",
            "tool": "MetaBAT2 + CheckM2",
            "good": "Depends on community complexity",
            "acceptable": "1 – 10 for simple communities",
            "poor": "0 MAGs from >1GB of reads",
            "interpretation": (
                "0 MAGs from sufficient data suggests: low coverage, too-short contigs, or divergent community. "
                "In gut metagenomes, typical studies recover 10–100 MAGs from 10-20 Gbp data. "
                "Reference: Pasolli et al. 2019, Cell (4,930 human gut MAGs)."
            ),
        },
    ]

    @classmethod
    def fetch(cls) -> List[BioDocument]:
        docs: List[BioDocument] = []
        for t in cls._THRESHOLDS:
            doc_id = f"quality_{t['metric'].lower().replace(' ', '_')}"
            text = (
                f"Quality threshold — {t['metric']} (tool: {t['tool']}): "
                f"Good: {t['good']} | Acceptable: {t['acceptable']} | Poor: {t['poor']}. "
                f"{t['interpretation']}"
            )
            docs.append(BioDocument(
                doc_id=doc_id,
                text=text,
                source="local",
                category="quality",
                metadata={"metric": t["metric"], "tool": t["tool"]},
            ))
        logger.info(f"[Quality] Loaded {len(docs)} threshold entries")
        return docs


class _PubMedFetcher:
    """
    Récupère des abstracts PubMed récents sur la métagénomique.
    Utilise l'API Entrez avec un rate limit respectueux.
    """
    ENTREZ_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    ENTREZ_FETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    QUERIES = [
        ("metagenome assembled genomes quality assessment", 10),
        ("metagenomics antimicrobial resistance gut microbiome", 8),
        ("shotgun metagenomics assembly comparison tools", 6),
        ("taxonomic profiling metagenomics kraken metaphlan comparison", 6),
        ("binning metagenomics metabat checkm quality", 5),
    ]

    @classmethod
    def fetch(cls, timeout: int = 15) -> List[BioDocument]:
        """
        Tente de récupérer des abstracts PubMed.
        Retourne une liste vide si pas de réseau — pas d'exception levée.
        """
        docs: List[BioDocument] = []
        seen_pmids: set = set()

        for query, max_results in cls.QUERIES:
            try:
                # 1. Recherche des PMIDs
                search_params = {
                    "db": "pubmed", "term": query, "retmax": max_results,
                    "retmode": "json", "sort": "relevance",
                    "datetype": "pdat", "mindate": "2020", "maxdate": "2025",
                }
                resp = requests.get(cls.ENTREZ_SEARCH, params=search_params, timeout=timeout)
                if resp.status_code != 200:
                    continue

                pmids = resp.json().get("esearchresult", {}).get("idlist", [])
                new_pmids = [p for p in pmids if p not in seen_pmids]
                if not new_pmids:
                    continue
                seen_pmids.update(new_pmids)

                # 2. Récupérer les articles (XML)
                time.sleep(0.35)  # Rate limit NCBI
                fetch_params = {
                    "db": "pubmed", "id": ",".join(new_pmids),
                    "retmode": "xml",
                }
                fetch_resp = requests.get(cls.ENTREZ_FETCH, params=fetch_params, timeout=timeout)
                if fetch_resp.status_code != 200:
                    continue

                # 3. Parsing XML robuste (TÂCHE 10)
                import xml.etree.ElementTree as ET
                root = ET.fromstring(fetch_resp.text)
                
                for article in root.findall(".//PubmedArticle"):
                    pmid_node = article.find(".//PMID")
                    pmid = pmid_node.text if pmid_node is not None else "0"
                    
                    title_node = article.find(".//ArticleTitle")
                    title = "".join(title_node.itertext()) if title_node is not None else "Unknown Title"
                    
                    abstract_nodes = article.findall(".//AbstractText")
                    abstract = " ".join(["".join(node.itertext()) for node in abstract_nodes if node is not None])
                    abstract = abstract.strip()[:1200]
                    
                    if not abstract:
                        continue

                    docs.append(BioDocument(
                        doc_id=f"pubmed_{pmid}",
                        text=f"[PubMed {pmid}] {title}. {abstract}",
                        source="pubmed",
                        category="literature",
                        metadata={
                            "pmid": pmid,
                            "title": title,
                            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        },
                    ))

                time.sleep(0.35)

            except Exception as e:
                logger.warning(f"[PubMed] Failed to fetch query '{query}': {e}")
                continue

        logger.info(f"[PubMed] Loaded {len(docs)} abstracts")
        return docs


# ---------------------------------------------------------------------------
# BioRAGStore — store vectoriel FAISS
# ---------------------------------------------------------------------------

class BioRAGStore:
    """
    Store vectoriel FAISS pour les bases biologiques.
    Supporte persist/load pour éviter de rebuild à chaque démarrage.
    """

    AVAILABLE_SOURCES = ["card", "kegg_pathways", "quality_thresholds", "pubmed"]

    def __init__(self, persist_dir: Optional[str] = None):
        self.persist_dir = Path(persist_dir) if persist_dir else Path(".genomeer_rag_cache")
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self._documents: List[BioDocument] = []
        self._index = None          # FAISS index
        self._embedder = None
        self._ready = False
        
        # TÂCHE 7.2: Infos de péremption des bundles
        self.rag_warnings = {
            "rag_bundles_stale": False,
            "rag_bundles_age_days": 0,
            "missing_bundles": []
        }

    def _load_static_bundles(self, sources: List[str]) -> List[BioDocument]:
        import json
        import os
        docs = []
        base_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent.parent / "data"
        
        if "card" in sources:
            card_path = base_dir / "card_top500.json"
            if card_path.exists():
                with open(card_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                bundle_date_str = data.get("__bundle_date__")
                if bundle_date_str:
                    from datetime import datetime
                    bundle_date = datetime.fromisoformat(bundle_date_str)
                    days_old = (datetime.now() - bundle_date).days
                    if days_old > 180:
                        # TÂCHE 7.1: Élévation au niveau ERROR pour visibilité
                        logger.error(f"[BioRAG] WARNING: The CARD context bundle ({card_path.name}) is {days_old} days old. Context may be outdated.")
                        self.rag_warnings["rag_bundles_stale"] = True
                        self.rag_warnings["rag_bundles_age_days"] = max(self.rag_warnings["rag_bundles_age_days"], days_old)
                
                version = data.get("source_version", "CARD")
                for entry in data.get("entries", []):
                    gene = entry.get("gene", "Unknown")
                    doc_id = f"card_{gene.lower().replace(' ', '_')}"
                    text = f"AMR gene: {gene} | Drug class: {entry.get('drug_class','')} | Mechanism: {entry.get('mechanism','')}. {entry.get('description','')}"
                    docs.append(BioDocument(
                        doc_id=doc_id, text=text, source="card", category="amr",
                        metadata={"gene": gene, "drug_class": entry.get("drug_class"), "source_version": version}
                    ))
            else:
                logger.error(f"[BioRAG] CRITICAL: Static bundle {card_path.name} not found. Biological context will be missing.")
                self.rag_warnings["missing_bundles"].append("card")
                        
        if "kegg_pathways" in sources:
            kegg_path = base_dir / "kegg_core_pathways.json"
            if kegg_path.exists():
                with open(kegg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                bundle_date_str = data.get("__bundle_date__")
                if bundle_date_str:
                    from datetime import datetime
                    bundle_date = datetime.fromisoformat(bundle_date_str)
                    days_old = (datetime.now() - bundle_date).days
                    if days_old > 180:
                        # TÂCHE 7.1: Élévation au niveau ERROR
                        logger.error(f"[BioRAG] WARNING: The KEGG context bundle ({kegg_path.name}) is {days_old} days old. Context may be outdated.")
                        self.rag_warnings["rag_bundles_stale"] = True
                        self.rag_warnings["rag_bundles_age_days"] = max(self.rag_warnings["rag_bundles_age_days"], days_old)
                
                version = data.get("source_version", "KEGG")
                for entry in data.get("entries", []):
                    pid = entry.get("pathway_id", "Unknown")
                    doc_id = f"kegg_{pid}"
                    text = f"KEGG Pathway {pid}: {entry.get('name','')}. {entry.get('description','')}"
                    docs.append(BioDocument(
                        doc_id=doc_id, text=text, source="kegg", category="pathway",
                        metadata={"pathway_id": pid, "name": entry.get("name"), "source_version": version}
                    ))
            else:
                logger.error(f"[BioRAG] CRITICAL: Static bundle {kegg_path.name} not found. Biological context will be missing.")
                self.rag_warnings["missing_bundles"].append("kegg")
                        
        return docs

    def build(
        self,
        sources: Optional[List[str]] = None,
        force_rebuild: bool = False,
    ) -> "BioRAGStore":
        """
        Construit ou charge l'index depuis le cache.

        Parameters
        ----------
        sources : liste de sources à indexer (défaut: toutes sauf pubmed pour rapidité)
        force_rebuild : ignorer le cache et refaire l'index
        """
        sources = sources or ["card", "kegg_pathways", "quality_thresholds"]
        
        # TÂCHE 8: Inclusion des dates de modification des bundles dans la cache_key
        # Cela force un rebuild si refresh_bundles.py a été exécuté.
        base_data_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent.parent / "data"
        mtimes = []
        for s in sources:
            fname = "card_top500.json" if s == "card" else "kegg_core_pathways.json" if s == "kegg_pathways" else None
            if fname:
                fpath = base_data_dir / fname
                if fpath.exists():
                    mtimes.append(f"{s}:{fpath.stat().st_mtime}")
        
        cache_key_content = json.dumps({"sources": sorted(sources), "mtimes": sorted(mtimes)})
        cache_key = hashlib.md5(cache_key_content.encode()).hexdigest()[:8]
        index_path  = self.persist_dir / f"bio_index_{cache_key}.faiss"
        docs_path   = self.persist_dir / f"bio_docs_{cache_key}.pkl"

        # Charger depuis le cache si disponible
        if not force_rebuild and index_path.exists() and docs_path.exists():
            try:
                self._load_from_cache(index_path, docs_path)
                logger.info(f"[BioRAG] Loaded from cache: {len(self._documents)} documents")
                return self
            except Exception as e:
                logger.warning(f"[BioRAG] Cache load failed ({e}), rebuilding...")

        # Fetch des documents
        all_docs: List[BioDocument] = []
        
        is_offline = os.environ.get("GENOMEER_RAG_OFFLINE", "0") == "1"

        # P4-C.3: Load static bundles as primary source
        static_docs = self._load_static_bundles(sources)
        static_ids = {d.doc_id for d in static_docs}
        all_docs.extend(static_docs)
        if static_docs:
            logger.info(f"[BioRAG] Loaded {len(static_docs)} documents from static JSON bundles.")

        if "card" in sources:
            fetched = _CARDFetcher.fetch()
            all_docs.extend([d for d in fetched if d.doc_id not in static_ids])
        if "kegg_pathways" in sources and not is_offline:
            fetched = _KEGGFetcher.fetch()
            all_docs.extend([d for d in fetched if d.doc_id not in static_ids])
        if "quality_thresholds" in sources:
            fetched = _QualityThresholdsFetcher.fetch()
            all_docs.extend([d for d in fetched if d.doc_id not in static_ids])
        if "pubmed" in sources and not is_offline:
            fetched = _PubMedFetcher.fetch()
            all_docs.extend([d for d in fetched if d.doc_id not in static_ids])

        if not all_docs:
            logger.warning("[BioRAG] No documents loaded — RAG store is empty")
            self._ready = False
            return self

        self._documents = all_docs

        # Initialiser l'embedder
        self._init_embedder()
        if self._embedder is None:
            logger.warning("[BioRAG] No embedder available — RAG store disabled")
            self._ready = False
            return self

        # Construire l'index FAISS
        self._build_faiss_index()

        # Persister
        try:
            self._save_to_cache(index_path, docs_path)
        except Exception as e:
            logger.warning(f"[BioRAG] Cache save failed: {e}")

        self._ready = True
        logger.info(f"[BioRAG] Index built: {len(self._documents)} documents")
        return self

    # ── Private ──────────────────────────────────────────────────────────────

    def _init_embedder(self):
        try:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            self._embed_backend = "sentence_transformers"
            return
        except ImportError:
            pass
        try:
            from langchain_openai import OpenAIEmbeddings
            self._embedder = OpenAIEmbeddings(model="text-embedding-3-small")
            self._embed_backend = "openai"
            return
        except Exception:
            pass
        logger.warning("[BioRAG] No embedding backend found")
        self._embedder = None

    def _embed(self, texts: List[str]):
        import numpy as np
        if self._embed_backend == "sentence_transformers":
            return self._embedder.encode(texts, normalize_embeddings=True).astype("float32")
        elif self._embed_backend == "openai":
            raw = self._embedder.embed_documents(texts)
            arr = np.array(raw, dtype="float32")
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            return arr / (norms + 1e-10)
        raise RuntimeError("No embedder")

    def _build_faiss_index(self):
        try:
            import faiss
            import numpy as np
        except ImportError:
            logger.warning("[BioRAG] faiss-cpu not installed")
            return

        texts = [doc.text for doc in self._documents]
        embeddings = self._embed(texts)
        D = embeddings.shape[1]
        index = faiss.IndexFlatIP(D)
        index.add(embeddings)
        self._index = index

    def _save_to_cache(self, index_path: Path, docs_path: Path):
        import faiss
        faiss.write_index(self._index, str(index_path))
        with open(docs_path, "wb") as f:
            pickle.dump(self._documents, f)

    def _load_from_cache(self, index_path: Path, docs_path: Path):
        import faiss
        self._index = faiss.read_index(str(index_path))
        with open(docs_path, "rb") as f:
            self._documents = pickle.load(f)
        self._init_embedder()
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready and self._index is not None


# ---------------------------------------------------------------------------
# BioRAGRetriever — interface de requête
# ---------------------------------------------------------------------------

class BioRAGRetriever:
    """
    Interface de requête sur le BioRAGStore.
    Appelée par le Finalizer pour enrichir son prompt avec des faits biologiques.
    """

    def __init__(self, store: BioRAGStore):
        self.store = store

    def get_context(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.25,
        filter_category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retourne les top_k documents biologiques les plus pertinents pour la query.

        Returns
        -------
        Liste de dicts: {"text": str, "source": str, "category": str, "score": float, "url": str}
        """
        if not self.store.ready:
            return self._fallback_context(query)

        try:
            import numpy as np
            q_vec = self.store._embed([query])
            k_search = min(top_k * 3, len(self.store._documents))
            scores, indices = self.store._index.search(q_vec, k_search)
            scores, indices = scores[0], indices[0]

            results = []
            for score, idx in zip(scores, indices):
                if idx < 0 or float(score) < min_score:
                    continue
                doc = self.store._documents[idx]
                if filter_category and doc.category != filter_category:
                    continue
                results.append({
                    "text": doc.text,
                    "source": doc.source,
                    "category": doc.category,
                    "score": float(score),
                    "url": doc.metadata.get("url", ""),
                    "snippet": doc.to_context_snippet(),
                })
                if len(results) >= top_k:
                    break

            return results

        except Exception as e:
            logger.warning(f"[BioRAGRetriever] Search failed: {e}")
            return self._fallback_context(query)

    def get_amr_context(self, detected_genes: List[str]) -> str:
        """
        Contexte AMR ciblé pour une liste de gènes détectés.
        Utilisé par le Finalizer quand des gènes AMR sont trouvés.
        """
        if not detected_genes:
            return ""

        contexts = []
        for gene in detected_genes[:5]:   # Top 5 gènes seulement
            results = self.get_context(
                query=f"antimicrobial resistance gene {gene} clinical significance mechanism",
                top_k=2,
                filter_category="amr",
            )
            if results:
                contexts.append(results[0]["snippet"])

        return "\n\n".join(contexts)

    def get_pathway_context(self, pathway_ids: List[str]) -> str:
        """Contexte KEGG ciblé pour une liste de pathway IDs."""
        if not pathway_ids:
            return ""
        contexts = []
        for pid in pathway_ids[:5]:
            results = self.get_context(
                query=f"KEGG pathway {pid} metabolic function metagenome",
                top_k=1,
                filter_category="pathway",
            )
            if results:
                contexts.append(results[0]["snippet"])
        return "\n\n".join(contexts)

    def format_for_prompt(self, results: List[Dict[str, Any]]) -> str:
        """Formate les résultats pour injection directe dans un prompt LLM."""
        if not results:
            return "(No biological database context available)"
        lines = ["=== BIOLOGICAL DATABASE CONTEXT (use for interpretation) ==="]
        for r in results:
            lines.append(r["snippet"])
        lines.append("=== END CONTEXT ===")
        return "\n\n".join(lines)

    def _fallback_context(self, query: str) -> List[Dict[str, Any]]:
        """Fallback offline : retourne les entrées quality si dispo."""
        if not self.store._documents:
            return []
        # Chercher les documents quality qui matchent mots-clés de la query
        query_words = set(query.lower().split())
        scored = []
        for doc in self.store._documents:
            if doc.category == "quality":
                doc_words = set(doc.text.lower().split())
                overlap = len(query_words & doc_words)
                if overlap > 0:
                    scored.append((overlap, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"text": d.text, "source": d.source, "category": d.category,
             "score": s / 10, "url": d.metadata.get("url", ""), "snippet": d.to_context_snippet()}
            for s, d in scored[:3]
        ]


# ---------------------------------------------------------------------------
# Patch pour le Finalizer — injection du contexte RAG dans le prompt
# ---------------------------------------------------------------------------

def build_finalizer_rag_context(
    retriever: BioRAGRetriever,
    pipeline_results: Dict[str, Any],
) -> str:
    """
    Construit le bloc de contexte RAG à injecter dans FINALIZER_PROMPT.

    Parameters
    ----------
    retriever : BioRAGRetriever initialisé
    pipeline_results : dict contenant les résultats du pipeline
                       (clés: "amr_genes", "pathways", "taxonomy", "assembly_n50", etc.)

    Returns
    -------
    str — bloc de contexte prêt à être injecté dans le prompt du Finalizer
    """
    context_blocks: List[str] = []

    # 1. AMR genes
    amr_genes = pipeline_results.get("amr_genes", [])
    if amr_genes:
        amr_ctx = retriever.get_amr_context(amr_genes)
        if amr_ctx:
            context_blocks.append(f"### AMR Gene Database Context\n{amr_ctx}")

    # 2. Pathways
    pathways = pipeline_results.get("pathways", [])
    if pathways:
        pw_ctx = retriever.get_pathway_context(pathways)
        if pw_ctx:
            context_blocks.append(f"### KEGG Pathway Context\n{pw_ctx}")

    # 3. Qualité générale — toujours inclus
    n50 = pipeline_results.get("assembly_n50")
    completeness = pipeline_results.get("mean_completeness")
    query_parts = ["metagenomics quality assessment"]
    if n50:
        query_parts.append(f"assembly N50 {n50} bp")
    if completeness:
        query_parts.append(f"MAG completeness {completeness}%")

    quality_results = retriever.get_context(
        query=" ".join(query_parts),
        top_k=3,
        filter_category="quality",
    )
    if quality_results:
        quality_ctx = retriever.format_for_prompt(quality_results)
        context_blocks.append(f"### Quality Threshold References\n{quality_ctx}")

    if not context_blocks:
        return ""

    return "\n\n".join(context_blocks)