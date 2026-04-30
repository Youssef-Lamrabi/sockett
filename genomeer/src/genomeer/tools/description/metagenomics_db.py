"""
Genomeer — Metagenomics Database Tool Descriptions
====================================================
API schema list for genomeer.tools.function.metagenomics_db
Covers NCBI Taxonomy, SILVA, GTDB, CARD, MGnify, SRA, UniProt, KEGG, VFDB, CAZy.
"""

description = [
    {
        "name": "query_ncbi_taxonomy",
        "description": (
            "Query the NCBI Taxonomy database via the Entrez API. "
            "Accepts a taxon name (e.g., 'Escherichia coli'), NCBI taxonomy ID (e.g., '9606'), "
            "or free-text search. Returns scientific name, rank, lineage, and taxonomy IDs."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Taxon name, taxonomy ID, or search term."},
        ],
        "optional_parameters": [
            {"name": "db", "type": "str", "default": "taxonomy", "description": "NCBI Entrez database."},
            {"name": "retmax", "type": "int", "default": 20, "description": "Maximum number of results."},
        ],
        "returns": "dict(query, db, n_found, results[tax_id, scientific_name, rank])",
    },
    {
        "name": "query_silva_sequences",
        "description": (
            "Query the SILVA ribosomal RNA database — the gold standard reference for 16S (SSU) "
            "and 23S (LSU) rRNA classification used in amplicon metagenomics. "
            "Returns accessions, taxonomy paths, and download guidance."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Search term (taxon name, keyword, or accession)."},
        ],
        "optional_parameters": [
            {"name": "target_region", "type": "str", "default": "SSU", "description": "rRNA region: 'SSU' (16S/18S) or 'LSU' (23S/28S)."},
            {"name": "taxon_filter", "type": "str", "default": None, "description": "Restrict to a specific taxon (e.g., 'Bacteria')."},
            {"name": "max_results", "type": "int", "default": 10, "description": "Maximum number of results."},
            {"name": "output_dir", "type": "str", "default": None, "description": "Optional directory to save downloaded sequences."},
        ],
        "returns": "dict(query, target_region, n_results, results, recommended_files)",
    },
    {
        "name": "download_silva_database",
        "description": (
            "Download the SILVA rRNA reference database (FASTA + taxonomy files) for "
            "use with Kraken2 custom database building or QIIME2 amplicon analysis. "
            "Downloads the non-redundant 99% identity subset (NR99) by default."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Directory to save SILVA database files."},
        ],
        "optional_parameters": [
            {"name": "region", "type": "str", "default": "SSU", "description": "rRNA region: 'SSU' or 'LSU'."},
            {"name": "release", "type": "str", "default": "138.2", "description": "SILVA release version."},
            {"name": "subset", "type": "str", "default": "NR99", "description": "Subset: 'NR99' (non-redundant) or 'Ref'."},
        ],
        "returns": "dict(output_dir, files[fasta_gz, taxonomy_txt])",
    },
    {
        "name": "query_gtdb_taxonomy",
        "description": (
            "Query the GTDB (Genome Taxonomy Database) API for phylogenomics-based taxonomy. "
            "GTDB uses genome-resolved phylogenetics rather than 16S for more accurate classification. "
            "Returns GTDB lineage, NCBI accession, species cluster, and representative genome."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Species name, NCBI accession, or genome ID."},
        ],
        "optional_parameters": [
            {"name": "search_type", "type": "str", "default": "all", "description": "Search type: 'all', 'ncbi', 'gtdb', 'species_cluster'."},
            {"name": "max_results", "type": "int", "default": 20, "description": "Maximum results."},
        ],
        "returns": "dict(query, n_results, results, source)",
    },
    {
        "name": "query_card_resistance",
        "description": (
            "Query the CARD (Comprehensive Antibiotic Resistance Database) for resistance genes. "
            "Returns gene name, drug class (e.g., 'beta-lactam', 'aminoglycoside'), "
            "resistance mechanism, and AMR gene family. "
            "Use run_rgi_card() for local sequence-based detection."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Gene name, antibiotic, drug class, or organism."},
        ],
        "optional_parameters": [
            {"name": "search_type", "type": "str", "default": "gene_name", "description": "Search type: 'gene_name', 'antibiotic', 'mechanism', 'drug_class', 'organism'."},
            {"name": "max_results", "type": "int", "default": 20, "description": "Maximum results."},
        ],
        "returns": "dict(query, results, source, web_search, local_analysis)",
    },
    {
        "name": "download_card_database",
        "description": (
            "Download the CARD database (card.json) for use with RGI (Resistance Gene Identifier). "
            "Required for local resistance gene detection with run_rgi_card()."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Directory to save CARD database files."},
        ],
        "optional_parameters": [],
        "returns": "dict(card_json, output_dir, status)",
    },
    {
        "name": "query_mgnify_studies",
        "description": (
            "Search the MGnify (EBI Metagenomics Portal) for publicly available metagenomic studies. "
            "Filter by biome (e.g., 'Human gut', 'Soil', 'Marine'), experiment type, or keywords. "
            "Returns study accessions, names, biomes, and sample counts. "
            "Provides access to >60,000 analysed metagenomes."
        ),
        "required_parameters": [],
        "optional_parameters": [
            {"name": "query", "type": "str", "default": None, "description": "Keyword search term."},
            {"name": "biome", "type": "str", "default": None, "description": "Biome filter (e.g., 'root:Environmental:Terrestrial:Soil')."},
            {"name": "experiment_type", "type": "str", "default": "metagenomic", "description": "Type: 'metagenomic', 'metatranscriptomic', 'amplicon', 'assembly'."},
            {"name": "max_results", "type": "int", "default": 20, "description": "Maximum studies to return."},
        ],
        "returns": "dict(query, biome, n_results, results[accession, name, biome, samples_count])",
    },
    {
        "name": "query_mgnify_samples",
        "description": (
            "Retrieve sample metadata and download links from a specific MGnify study. "
            "Returns sample accessions, geographic location, environmental metadata, "
            "and links to pre-analysed taxonomy and functional profiles."
        ),
        "required_parameters": [
            {"name": "study_accession", "type": "str", "description": "MGnify study accession (e.g., 'MGYS00005116')."},
        ],
        "optional_parameters": [
            {"name": "max_results", "type": "int", "default": 50, "description": "Maximum samples to return."},
        ],
        "returns": "dict(study_accession, n_samples, samples[accession, biome, geo_loc, sample_name])",
    },
    {
        "name": "fetch_sra_reads",
        "description": (
            "Download raw sequencing reads from the NCBI Sequence Read Archive (SRA) "
            "using fasterq-dump for fast parallel download and FASTQ conversion. "
            "accession: SRA run accession (e.g., 'SRR5926764', 'ERR1234567'). "
            "Automatically splits paired-end reads into R1 and R2 files."
        ),
        "required_parameters": [
            {"name": "accession", "type": "str", "description": "SRA run accession (e.g., 'SRR5926764')."},
            {"name": "output_dir", "type": "str", "description": "Directory to save downloaded FASTQ files."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4, "description": "Download/conversion threads."},
            {"name": "max_size", "type": "str", "default": "20G", "description": "Maximum download size limit."},
            {"name": "split_files", "type": "bool", "default": True, "description": "Split paired-end reads into separate files."},
        ],
        "returns": "dict(accession, fastq_r1, fastq_r2, all_files, output_dir)",
    },
    {
        "name": "query_uniprot_proteins",
        "description": (
            "Query UniProt (UniProtKB, UniRef90, UniRef50) for protein annotation information. "
            "Useful for functional characterization of predicted ORFs or MAG proteins. "
            "Returns protein name, organism, length, reviewed status, and sequence."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Protein name, gene name, EC number, or accession."},
        ],
        "optional_parameters": [
            {"name": "database", "type": "str", "default": "uniprotkb", "description": "UniProt database: 'uniprotkb', 'uniref90', 'uniref50'."},
            {"name": "max_results", "type": "int", "default": 20, "description": "Maximum results."},
            {"name": "reviewed_only", "type": "bool", "default": False, "description": "Restrict to Swiss-Prot manually reviewed entries."},
        ],
        "returns": "dict(query, database, n_results, results[accession, protein_name, organism, length])",
    },
    {
        "name": "query_kegg_pathway",
        "description": (
            "Query the KEGG REST API for metabolic pathway information. "
            "Returns pathway name, class, associated KO entries, and enzyme links. "
            "pathway_id examples: 'ko00010' (Glycolysis), 'ko01200' (Carbon metabolism), "
            "'ko02020' (Two-component system), 'ko00250' (Alanine/Aspartate metabolism)."
        ),
        "required_parameters": [
            {"name": "pathway_id", "type": "str", "description": "KEGG pathway ID (e.g., 'ko00010', 'map00190')."},
        ],
        "optional_parameters": [],
        "returns": "dict(pathway_id, name, description, class, genes)",
    },
    {
        "name": "query_kegg_orthology",
        "description": (
            "Query the KEGG REST API for a KEGG Orthology (KO) entry. "
            "KO IDs are the functional unit used by HUMAnN3 and KofamKOALA. "
            "Returns gene name, definition, EC number, and associated pathways. "
            "ko_id examples: 'K00844' (hexokinase), 'K00850' (6-phosphofructokinase)."
        ),
        "required_parameters": [
            {"name": "ko_id", "type": "str", "description": "KEGG KO identifier (e.g., 'K00844')."},
        ],
        "optional_parameters": [],
        "returns": "dict(ko_id, name, definition, pathway, brite)",
    },
    {
        "name": "download_vfdb",
        "description": (
            "Download the VFDB (Virulence Factor Database) protein sequences for local "
            "virulence gene detection via DIAMOND. "
            "subset: 'core' (experimentally verified) or 'full' (includes predicted). "
            "After download, build DIAMOND database: diamond makedb --in vfdb.fas -d vfdb_db"
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Directory to save VFDB FASTA files."},
        ],
        "optional_parameters": [
            {"name": "subset", "type": "str", "default": "core", "description": "Dataset: 'core' (verified) or 'full' (all)."},
        ],
        "returns": "dict(fasta_path, subset, usage)",
    },
    {
        "name": "query_cazy_families",
        "description": (
            "Query the CAZy (Carbohydrate-Active Enzymes) database for enzyme families. "
            "Important for soil/environmental metagenomics: identifies carbon cycling enzymes. "
            "family_type: 'GH' (glycoside hydrolases), 'GT' (glycosyltransferases), "
            "'PL' (polysaccharide lyases), 'CE' (esterases), 'CBM' (binding modules), 'AA' (auxiliary). "
            "Returns family descriptions and recommended annotation approach with dbCAN/HMMER."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Enzyme name, substrate, or CAZy family ID."},
        ],
        "optional_parameters": [
            {"name": "family_type", "type": "str", "default": None, "description": "CAZy family type: 'GH', 'GT', 'PL', 'CE', 'CBM', 'AA'."},
            {"name": "max_results", "type": "int", "default": 20, "description": "Maximum results."},
        ],
        "returns": "dict(query, families_overview, recommended_approach, selected_family)",
    },
]