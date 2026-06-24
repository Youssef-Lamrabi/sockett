"""
Genomeer — Viromics Tool Descriptions
=====================================
API schema list for genomeer.tools.function.viromics
"""

description = [
    {
        "name": "run_virsorter2",
        "description": (
            "Run VirSorter2 to identify viral sequences in metagenomic contigs. "
            "VirSorter2 uses hallmark genes and machine learning classifiers trained on "
            "diverse viral groups to classify contigs as viral or not."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Path to contig FASTA (from metaSPAdes/MEGAHIT)."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "min_score", "type": "float", "default": 0.5, "description": "Minimum VirSorter2 score threshold (0–1)."},
            {"name": "groups", "type": "str", "default": "dsDNAphage,NCLDV,RNA,ssDNA,lavidaviridae", "description": "Comma-separated viral groups to detect."},
            {"name": "min_length", "type": "int", "default": 1500, "description": "Minimum contig length to consider (bp)."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "db_dir", "type": "str", "default": None, "description": "Path to VirSorter2 database."},
        ],
        "returns": "dict(viral_fasta, score_tsv, n_viral_sequences, viral_groups, output_dir)",
    },
    {
        "name": "run_checkv",
        "description": (
            "Run CheckV to assess quality and completeness of viral genomes/contigs. "
            "CheckV is the viral equivalent of CheckM2: estimates genome completeness, "
            "identifies provirus, and classifies quality."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Viral FASTA from VirSorter2 or DeepVirFinder."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "db_dir", "type": "str", "default": None, "description": "CheckV database path."},
            {"name": "remove_hosts", "type": "bool", "default": True, "description": "Run host contamination removal step."},
        ],
        "returns": "dict(quality_summary_tsv, n_complete, n_high_quality, n_low_quality, n_proviruses, mean_completeness, output_dir)",
    },
    {
        "name": "run_deepvirfinder",
        "description": (
            "Run DeepVirFinder for virus identification using deep learning. "
            "Complementary to VirSorter2, uses k-mer patterns without gene annotation."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Assembly FASTA."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "min_length", "type": "int", "default": 1000, "description": "Minimum contig length (bp)."},
            {"name": "pvalue_cutoff", "type": "float", "default": 0.05, "description": "Maximum p-value to report."},
            {"name": "score_cutoff", "type": "float", "default": 0.9, "description": "Minimum DVF score (0–1)."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "dvf_script", "type": "str", "default": None, "description": "Path to dvf.py (auto-detected if None)."},
        ],
        "returns": "dict(scores_tsv, n_viral_sequences, high_conf_viral, output_dir)",
    },
    {
        "name": "run_gget_virus",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] gget virus (gget v0.30.6): DETERMINISTIC RETRIEVAL of viral "
            "genome sequences + GenBank metadata from the NCBI Virus database. AVAILABLE in meta-env1. "
            "This is a DATA-RETRIEVAL tool (the viral counterpart of ncbi-genome-download); it does NOT "
            "detect/analyze viruses — for that use geNomad/VirSorter2/CheckV on contigs. Use it to fetch "
            "reference viral genomes for a host/lineage/region or to pull specific accessions. EXACT "
            "command (run via meta-env1; needs network → NCBI Virus REST API): "
            "by taxon: gget virus 'SARS-CoV-2' -o <out_dir> --nuc_completeness complete  ; "
            "by accession(s): gget virus -a 'NC_045512.2' -o <out_dir>  (-a = --is_accession; accepts a "
            "single accession, space-separated accessions, or a path to a one-per-line .txt). "
            "Useful filters: --host, --min_seq_length/--max_seq_length, --geographic_location, "
            "--min_release_date/--max_release_date, --annotated, --has_proteins, --source_database. "
            "Outputs in <out_dir>: <name>_sequences.fasta (genomes), <name>_metadata.csv and "
            "<name>_metadata.jsonl (per-record metadata), command_summary.txt. "
            "NOTE: a bare taxon query with no filters can return MANY sequences — always constrain with "
            "--nuc_completeness complete and/or length/date filters (or use -a with explicit accessions)."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Virus taxon name/ID (e.g. 'zika virus', '1335626') OR, with is_accession, an accession / list / file path."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "is_accession", "type": "bool", "default": False, "description": "Treat query as accession(s) (-a flag)."},
            {"name": "nuc_completeness", "type": "str", "default": None, "description": "complete or partial."},
            {"name": "host", "type": "str", "default": None, "description": "Host organism filter (e.g. 'homo sapiens')."},
            {"name": "max_seq_length", "type": "int", "default": None, "description": "Maximum sequence length (bp) — constrain broad taxon queries."},
        ],
        "returns": "dict(sequences_fasta, metadata_csv, metadata_jsonl, n_sequences, output_dir)",
    },
]
