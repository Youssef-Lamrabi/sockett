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
]
