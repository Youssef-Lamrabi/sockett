description = [
    {
        "name": "load_sequences",
        "description": "Load FASTA/FASTQ files into lightweight line-delimited TSVs (id, seq[, qual]).",
        "required_parameters": [
            {"name": "paths", "type": "list", "description": "List of input file paths."}
        ],
        "optional_parameters": [
            {"name": "format", "type": "str", "default": None, "description": "Force 'fasta' or 'fastq'. If None, auto-detect."},
            {"name": "max_records", "type": "int", "default": None, "description": "Limit records per file."}
        ],
        "returns": "dict(records_count, format, temp_paths)"
    },
    {
        "name": "write_sequences",
        "description": "Write sequences to FASTA/FASTQ (optionally gz).",
        "required_parameters": [
            {"name": "records", "type": "iterable", "description": "FASTA: (id, seq). FASTQ: (id, seq, qual)."},
            {"name": "path", "type": "str", "description": "Output path."}
        ],
        "optional_parameters": [
            {"name": "format", "type": "str", "default": "fasta", "description": "'fasta' or 'fastq'."},
            {"name": "compress", "type": "bool", "default": False, "description": "Write .gz if True."}
        ],
        "returns": "dict(written_count, path)"
    },
    {
        "name": "subsample_reads",
        "description": "Randomly subsample reads from FASTA/FASTQ.",
        "required_parameters": [
            {"name": "input_path", "type": "str"},
            {"name": "output_path", "type": "str"},
            {"name": "fraction", "type": "float", "description": "0–1 fraction to keep."}
        ],
        "optional_parameters": [
            {"name": "seed", "type": "int", "default": 42},
            {"name": "paired", "type": "bool", "default": False}
        ],
        "returns": "dict(written_count, output_path)"
    },
    {
        "name": "convert_format",
        "description": "Convert FASTA<->FASTQ (dummy qualities for FASTA->FASTQ).",
        "required_parameters": [
            {"name": "input_path", "type": "str"},
            {"name": "output_path", "type": "str"},
            {"name": "target_format", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "params", "type": "dict", "default": None}
        ],
        "returns": "dict(output_path, summary)"
    },
    {
        "name": "read_quality_report",
        "description": "Compute simple QC metrics (read count, avg length, GC%, Ns).",
        "required_parameters": [
            {"name": "paths", "type": "list"}
        ],
        "optional_parameters": [
            {"name": "sample_names", "type": "list", "default": None}
        ],
        "returns": "dict(per_sample_stats, plots)"
    },
    {
        "name": "trim_filter_reads",
        "description": "Adapter/quality trimming (toy) and min length filter.",
        "required_parameters": [
            {"name": "input_path", "type": "str"},
            {"name": "output_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "min_len", "type": "int", "default": 50},
            {"name": "min_q", "type": "int", "default": 20},
            {"name": "adapter_5p", "type": "str", "default": None},
            {"name": "adapter_3p", "type": "str", "default": None},
            {"name": "paired", "type": "bool", "default": False}
        ],
        "returns": "dict(kept, discarded, output_path)"
    },
    {
        "name": "kmer_profile",
        "description": "Count k-mers across reads/contigs and write a TSV spectrum.",
        "required_parameters": [
            {"name": "paths", "type": "list"},
            {"name": "k", "type": "int"}
        ],
        "optional_parameters": [
            {"name": "canonical", "type": "bool", "default": True},
            {"name": "max_records", "type": "int", "default": None}
        ],
        "returns": "dict(counts_path, summary)"
    },
    {
        "name": "translate_orfs",
        "description": "Find ORFs and translate to proteins (both strands optional).  The output includes structured results with ORF coordinates, translated protein sequences, and a summary of findings as a dictionary liek this `dict(orfs_bed, proteins_faa, summary)`",
        "required_parameters": [
            {"name": "fasta_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "min_aa", "type": "int", "default": 30},
            {"name": "genetic_code", "type": "int", "default": 11},
            {"name": "strand", "type": "str", "default": "both"}
        ],
        "returns": "dict(orfs_bed, proteins_faa, summary)"
    },
    {
        "name": "gc_content_windows",
        "description": "Sliding-window GC% over FASTA sequences.",
        "required_parameters": [
            {"name": "fasta_path", "type": "str"},
            {"name": "window", "type": "int"},
            {"name": "step", "type": "int"}
        ],
        "optional_parameters": [
            {"name": "mask_ns", "type": "bool", "default": True}
        ],
        "returns": "dict(gc_tsv, plots)"
    },
    {
        "name": "deduplicate_sequences",
        "description": "Collapse identical sequences and output uniques.",
        "required_parameters": [
            {"name": "input_path", "type": "str"},
            {"name": "output_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "min_count", "type": "int", "default": 1}
        ],
        "returns": "dict(unique_count, mapping_tsv, output_path)"
    },
    {
        "name": "align_reads_minimap2_like",
        "description": "Toy alignment wrapper that emits a placeholder BAM (replace in prod).",
        "required_parameters": [
            {"name": "reads", "type": "list"},
            {"name": "reference_fasta", "type": "str"},
            {"name": "output_bam", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "preset", "type": "str", "default": "sr"},
            {"name": "threads", "type": "int", "default": 2}
        ],
        "returns": "dict(bam_path, index_created, summary)"
    },
    {
        "name": "compute_coverage",
        "description": "Compute per-base/window coverage from BAM (toy: zero coverage).",
        "required_parameters": [
            {"name": "bam_path", "type": "str"},
            {"name": "reference_fasta", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "window", "type": "int", "default": None},
            {"name": "bed_regions", "type": "str", "default": None}
        ],
        "returns": "dict(coverage_tsv, plots)"
    },
    {
        "name": "call_variants_simple",
        "description": "Heuristic pileup-based variant calling (toy: empty VCF header).",
        "required_parameters": [
            {"name": "bam_path", "type": "str"},
            {"name": "reference_fasta", "type": "str"},
            {"name": "output_vcf", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "min_depth", "type": "int", "default": 5},
            {"name": "min_alt_frac", "type": "float", "default": 0.2}
        ],
        "returns": "dict(vcf_path, summary)"
    },
    {
        "name": "classify_reads_kmer",
        "description": "Naive k-mer–style taxonomic assignment against a toy DB.",
        "required_parameters": [
            {"name": "reads", "type": "list"},
            {"name": "db_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "k", "type": "int", "default": 31},
            {"name": "min_hits", "type": "int", "default": 3},
            {"name": "top_n", "type": "int", "default": 1}
        ],
        "returns": "dict(assignments_tsv, unclassified_path, summary)"
    },
    {
        "name": "bin_contigs_basic",
        "description": "Simple binning by length threshold (toy baseline).",
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "coverage_tsv", "type": "str", "default": None},
            {"name": "min_len", "type": "int", "default": 1500},
            {"name": "clusters", "type": "int", "default": None}
        ],
        "returns": "dict(bins_fasta_dir, bin_map_tsv, summary)"
    },
    {
        "name": "estimate_complexity",
        "description": "Alpha diversity estimate (Shannon or richness) from assignments.",
        "required_parameters": [
            {"name": "assignments_tsv", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "metric", "type": "str", "default": "shannon"}
        ],
        "returns": "dict(diversity_tsv, summary, plots)"
    },
    {
        "name": "contamination_screen",
        "description": "Screen sequences vs contaminant reference (toy: passthrough).",
        "required_parameters": [
            {"name": "input_path", "type": "str"},
            {"name": "contaminant_ref", "type": "str"},
            {"name": "clean_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "keep_unmapped"}
        ],
        "returns": "dict(clean_path, removed_count, summary)"
    },
    {
        "name": "predict_genes_baseline",
        "description": "Toy gene finder using ORFs as CDS features.",
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "min_aa", "type": "int", "default": 60},
            {"name": "genetic_code", "type": "int", "default": 11}
        ],
        "returns": "dict(genes_gff, proteins_faa, summary)"
    },
    {
        "name": "annotate_functions_hmm",
        "description": "[STUB — DO NOT USE IN PRODUCTION PIPELINES] Toy functional annotation stub: labels ALL proteins as 'unknown_function' regardless of input. For real HMM annotation use run_hmmer() (HMMER against Pfam/TIGRFAM) or run_prokka() instead.",
        "required_parameters": [
            {"name": "proteins_faa", "type": "str"},
            {"name": "db_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "evalue", "type": "float", "default": 1e-5},
            {"name": "top_n", "type": "int", "default": 1}
        ],
        "returns": "dict(annotations_tsv, summary)"
    },
    {
        "name": "analyze_genomic_region_overlap",
        "description": "Pairwise overlap metrics for multiple region sets (pure-Python baseline).",
        "required_parameters": [
            {"name": "region_sets", "type": "list", "description": "Each item is path to BED or list of (chrom,start,end[,name])."}
        ],
        "optional_parameters": [
            {"name": "output_prefix", "type": "str", "default": "overlap_analysis"}
        ],
        "returns": "str research log + saves summary TSV"
    },
    {
        "name": "merge_regions",
        "description": "Merge overlapping/adjacent intervals per chromosome.",
        "required_parameters": [
            {"name": "bed_path", "type": "str"},
            {"name": "output_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "distance", "type": "int", "default": 0},
            {"name": "keep_names", "type": "bool", "default": False}
        ],
        "returns": "dict(output_path, merged_count, summary)"
    },
    {
        "name": "intersect_regions",
        "description": "Intersect two BED-like sets with -wa/-wb/-wo behaviors (baseline).",
        "required_parameters": [
            {"name": "a_bed", "type": "str"},
            {"name": "b_bed", "type": "str"},
            {"name": "output_path", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "wo"}
        ],
        "returns": "dict(output_path, overlap_count, summary)"
    },
    {
        "name": "assemble_greedy_baseline",
        "description": "[STUB — DO NOT USE IN PRODUCTION PIPELINES] Toy assembler that simply concatenates reads into fake 'contigs' without any De Bruijn graph construction. Produces biologically meaningless output. For real assembly use run_metaspades() (Illumina) or run_megahit() (large datasets) or run_flye() (Nanopore).",
        "required_parameters": [
            {"name": "reads", "type": "list"},
            {"name": "output_fasta", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "min_overlap", "type": "int", "default": 30},
            {"name": "max_reads", "type": "int", "default": None}
        ],
        "returns": "dict(contigs_fasta, n_contigs, summary)"
    },
    {
        "name": "scaffold_gc_link",
        "description": "Crude scaffolding (toy passthrough).",
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"}
        ],
        "optional_parameters": [
            {"name": "coverage_tsv", "type": "str", "default": None},
            {"name": "link_threshold", "type": "float", "default": 0.8}
        ],
        "returns": "dict(scaffolds_fasta, summary)"
    },
    {
        "name": "multi_sample_summary_report",
        "description": "Collate artifacts into a small HTML/Markdown report.",
        "required_parameters": [
            {"name": "artifacts", "type": "dict", "description": "key->path mapping of generated artifacts"}
        ],
        "optional_parameters": [
            {"name": "format", "type": "str", "default": "html"}
        ],
        "returns": "dict(report_path, assets_dir)"
    },
    {
        "name": "plot_stackbar_taxa",
        "description": "Prepare a TSV for stacked bar taxonomic composition (no plotting libs).",
        "required_parameters": [
            {"name": "assignments_list", "type": "list"}
        ],
        "optional_parameters": [
            {"name": "rank", "type": "str", "default": "genus"},
            {"name": "top_n", "type": "int", "default": 15},
            {"name": "normalize", "type": "bool", "default": True}
        ],
        "returns": "dict(plot_path, summary)"
    }
]