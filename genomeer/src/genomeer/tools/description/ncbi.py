description = [
    {
        "name": "download_from_ncbi",
        "description": (
            "[INTERNAL API — DO NOT IMPORT IN GENERATED SCRIPTS] "
            "This function is a Python wrapper around ncbi-genome-download CLI. "
            "It is NOT available in execution environments (bio-agent-env1, meta-env1). "
            "Importing it raises ModuleNotFoundError. "
            "To download from NCBI in generated code, use ncbi-genome-download CLI directly: "
            "ncbi-genome-download -A <accession> -l complete -s refseq -F fasta --flat-output -o <dir> bacteria"
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
    },
    {
        "name": "query_ncbi_entrez",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] NCBI Entrez Direct (esearch/efetch/esummary) — METADATA LOOKUP & "
            "VERIFICATION (NOT a downloader; for genome FASTA use ncbi-genome-download). AVAILABLE in "
            "meta-env1 and bio-agent-env1. Use it to RESOLVE and VERIFY before/around downloads — this "
            "prevents wrong-organism and hallucinated-accession failures. Run via subprocess as a shell pipe. "
            "Key recipes (validated):\n"
            "  * BioProject -> RUN accessions (SRR/ERR), REQUIRED to download SRA reads from a PRJNA/PRJEB: \n"
            "      esearch -db sra -query 'PRJNA288120' | efetch -format runinfo\n"
            "    (CSV; column 1 = Run accession, e.g. SRR2090128. Parse the Run column, then download each run.)\n"
            "  * Verify an assembly accession's ORGANISM (confirm species before analysis): \n"
            "      esearch -db assembly -query 'GCF_000240185.1' | esummary | grep -o '<Organism>[^<]*'\n"
            "  * Taxonomy / lineage for a name: esearch -db taxonomy -query 'Klebsiella pneumoniae' | efetch -format xml.\n"
            "NOTES: needs network (eutils.ncbi.nlm.nih.gov); edirect sometimes prints a transient "
            "'SSL unexpected eof' then retries and still returns results — re-run once on empty output. "
            "Do NOT hit eutils with raw urllib/requests (rate-limited) — use esearch/efetch which handle retries."
        ),
        "required_parameters": [
            {"name": "db", "type": "str", "description": "Entrez database: sra, assembly, taxonomy, nuccore, bioproject, biosample."},
            {"name": "query", "type": "str", "description": "Accession, BioProject ID, organism name, or query term."},
        ],
        "optional_parameters": [
            {"name": "format", "type": "str", "default": "runinfo", "description": "efetch -format (runinfo, xml, docsum, acc) or esummary."},
        ],
        "returns": "dict(ok, cmd, stdout, stderr)"
    }
]
