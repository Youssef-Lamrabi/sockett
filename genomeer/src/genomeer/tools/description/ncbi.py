description = [
    {
        "name": "download_from_ncbi",
        "description": (
            "Download genomes or annotations from NCBI using ncbi-genome-download. "
            "Supports selection by groups (e.g. 'bacteria', 'plant', 'all'), specific accessions "
            "(e.g. 'GCF_000001735.4'), species/taxids (e.g. '-T 3702'), assembly level filters, formats "
            "('fasta', 'gff', 'genbank', etc.), and optional post-download decompression."
        ),
        "required_parameters": [
            {"name": "groups", "type": "str", "description": "Taxonomic group(s) (comma-separated). Examples: 'bacteria', 'plant', 'all'."}
        ],
        "optional_parameters": [
            {"name": "section", "type": "str", "default": "refseq", "description": "NCBI section: 'refseq' or 'genbank'."},
            {"name": "formats", "type": "str", "default": "fasta", "description": "Comma-separated formats. Example: 'fasta,assembly-report'."},
            {"name": "assembly_levels", "type": "str", "default": "all", "description": "Assembly levels: 'all|complete|chromosome|scaffold|contig'."},
            {"name": "genera", "type": "str", "default": None, "description": "Comma-separated genera names to include."},
            {"name": "strains", "type": "str", "default": None, "description": "Comma-separated strain names or a file path with one name per line."},
            {"name": "species_taxids", "type": "str", "default": None, "description": "Species taxids, comma-separated (e.g., '3702' or '9606,9685')."},
            {"name": "taxids", "type": "str", "default": None, "description": "NCBI taxids (any rank), comma-separated."},
            {"name": "assembly_accessions", "type": "str", "default": None, "description": "Assembly accessions, comma-separated (e.g., 'GCF_000001735.4')."},
            {"name": "refseq_categories", "type": "str", "default": None, "description": "RefSeq categories (e.g., 'reference,representative')."},
            {"name": "type_materials", "type": "str", "default": None, "description": "Type material relation filter (e.g., 'any', 'all', 'reference')."},
            {"name": "fuzzy_genus", "type": "bool", "default": False, "description": "Enable fuzzy matching on genus names."},
            {"name": "fuzzy_accessions", "type": "bool", "default": False, "description": "Enable fuzzy matching on accessions."},
            {"name": "output_folder", "type": "str", "default": None, "description": "Output directory. Defaults to a safe temporary folder if not provided."},
            {"name": "flat_output", "type": "bool", "default": False, "description": "Dump all files directly into the output folder."},
            {"name": "human_readable", "type": "bool", "default": False, "description": "Create human-readable symlink hierarchy."},
            {"name": "progress_bar", "type": "bool", "default": False, "description": "Display a progress bar."},
            {"name": "uri", "type": "str", "default": None, "description": "Override NCBI base URI (e.g., 'https://ftp.ncbi.nlm.nih.gov/genomes')."},
            {"name": "parallel", "type": "int", "default": 1, "description": "Number of parallel downloads."},
            {"name": "retries", "type": "int", "default": 0, "description": "Number of retry attempts on connection failure."},
            {"name": "metadata_table", "type": "str", "default": None, "description": "Path for saving a tab-delimited metadata table."},
            {"name": "dry_run", "type": "bool", "default": False, "description": "Only show what would be downloaded (no files written)."},
            {"name": "no_cache", "type": "bool", "default": False, "description": "Disable assembly summary file cache."},
            {"name": "verbose", "type": "bool", "default": False, "description": "Increase output verbosity."},
            {"name": "debug", "type": "bool", "default": False, "description": "Print debugging information."},
            {"name": "decompress", "type": "bool", "default": False, "description": "Gunzip FASTA/GFF/GBFF files after download."},
            {"name": "timeout_sec", "type": "int", "default": 1800, "description": "Subprocess timeout in seconds."}
        ],
        "returns": "dict(ok, cmd, stdout, stderr, output_folder, downloaded_files, decompressed_files, note)"
    }
]
