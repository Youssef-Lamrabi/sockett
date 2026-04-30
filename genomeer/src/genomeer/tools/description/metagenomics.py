"""
Genomeer — Metagenomics Tool Descriptions
==========================================
API schema list for genomeer.tools.function.metagenomics
Follows the exact same structure as description/basic.py and description/ncbi.py.
28 tools covering the full metagenomics pipeline.
"""

description = [

    # =========================================================================
    # QC & PREPROCESSING
    # =========================================================================
    {
        "name": "run_fastp",
        "description": (
            "Run fastp for adapter trimming and quality control on Illumina FASTQ reads. "
            "Supports both single-end and paired-end inputs (optionally gzipped). "
            "Produces trimmed FASTQ files, a JSON stats report, and an interactive HTML QC report. "
            "Recommended first step for all short-read metagenomics pipelines."
        ),
        "required_parameters": [
            {"name": "input_r1", "type": "str", "description": "Path to R1 FASTQ file (can be .gz)."},
            {"name": "output_dir", "type": "str", "description": "Directory where trimmed reads and reports are written."},
        ],
        "optional_parameters": [
            {"name": "input_r2", "type": "str", "default": None, "description": "Path to R2 FASTQ file for paired-end mode."},
            {"name": "threads", "type": "int", "default": 4, "description": "Number of CPU threads."},
            {"name": "min_quality", "type": "int", "default": 20, "description": "Minimum phred quality score."},
            {"name": "min_length", "type": "int", "default": 50, "description": "Minimum read length after trimming."},
            {"name": "detect_adapter_for_pe", "type": "bool", "default": True, "description": "Auto-detect adapters for paired-end data."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra fastp CLI flags as a string."},
        ],
        "returns": "dict(out_r1, out_r2, json_report, html_report, summary)",
    },
    {
        "name": "run_fastqc",
        "description": (
            "Run FastQC to assess per-base quality, GC content, adapter contamination, "
            "and sequence duplication on one or more FASTQ files. "
            "Produces an HTML report per file. Complementary to fastp for initial QC."
        ),
        "required_parameters": [
            {"name": "input_files", "type": "list", "description": "List of paths to FASTQ files."},
            {"name": "output_dir", "type": "str", "description": "Directory for HTML/ZIP reports."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4, "description": "Number of threads."},
        ],
        "returns": "dict(output_dir, html_reports)",
    },
    {
        "name": "run_multiqc",
        "description": (
            "Run MultiQC to aggregate QC results from fastp, FastQC, Kraken2, samtools, "
            "and other tools into a single interactive HTML report. "
            "Scans input_dir recursively for recognized log/report files."
        ),
        "required_parameters": [
            {"name": "input_dir", "type": "str", "description": "Directory to scan for QC reports."},
            {"name": "output_dir", "type": "str", "description": "Directory for the MultiQC HTML output."},
        ],
        "optional_parameters": [
            {"name": "report_name", "type": "str", "default": "multiqc_report", "description": "HTML report filename (without extension)."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra MultiQC CLI flags."},
        ],
        "returns": "dict(html_report, data_dir)",
    },
    {
        "name": "run_nanostat",
        "description": (
            "Run NanoStat to compute quality statistics on Oxford Nanopore long reads: "
            "N50, mean read length, mean quality score, total bases, and length distribution. "
            "Essential QC step before long-read assembly with Flye."
        ),
        "required_parameters": [
            {"name": "input_fastq", "type": "str", "description": "Path to Nanopore FASTQ file."},
            {"name": "output_dir", "type": "str", "description": "Output directory for the stats report."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4, "description": "Number of threads."},
        ],
        "returns": "dict(stats_file, stdout)",
    },

    # =========================================================================
    # ASSEMBLY
    # =========================================================================
    {
        "name": "run_metaspades",
        "description": (
            "Run metaSPAdes for de-novo metagenome assembly from Illumina short reads. "
            "Recommended for complex communities; produces high-quality contigs and scaffolds. "
            "Supports paired-end (reads_r1 + reads_r2) or single-end (reads_single) input. "
            "More memory-intensive but more accurate than MEGAHIT."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Directory for assembly output."},
        ],
        "optional_parameters": [
            {"name": "reads_r1", "type": "str", "default": None, "description": "Path to R1 paired-end reads."},
            {"name": "reads_r2", "type": "str", "default": None, "description": "Path to R2 paired-end reads."},
            {"name": "reads_single", "type": "str", "default": None, "description": "Path to single-end reads."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "memory_gb", "type": "int", "default": 16, "description": "Memory limit in GB."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra SPAdes CLI flags."},
        ],
        "returns": "dict(contigs_fasta, scaffolds_fasta, assembly_graph, log, output_dir)",
    },
    {
        "name": "run_megahit",
        "description": (
            "Run MEGAHIT for fast, memory-efficient metagenome assembly. "
            "More suitable than metaSPAdes for very large datasets or low-memory systems. "
            "Supports paired-end and single-end Illumina reads."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Directory for assembly output."},
        ],
        "optional_parameters": [
            {"name": "reads_r1", "type": "str", "default": None, "description": "Path to R1 reads."},
            {"name": "reads_r2", "type": "str", "default": None, "description": "Path to R2 reads."},
            {"name": "reads_single", "type": "str", "default": None, "description": "Path to single-end reads."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "memory_fraction", "type": "float", "default": 0.5, "description": "Fraction of system RAM to use."},
            {"name": "min_contig_len", "type": "int", "default": 500, "description": "Minimum output contig length."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra MEGAHIT CLI flags."},
        ],
        "returns": "dict(contigs_fasta, output_dir, log)",
    },
    {
        "name": "run_flye",
        "description": (
            "Run Flye assembler optimized for Oxford Nanopore or PacBio long reads. "
            "Use --meta mode (enabled by default) for metagenomics. "
            "read_type options: 'nano-raw', 'nano-hq', 'nano-corr', 'pacbio-raw', 'pacbio-hifi'. "
            "genome_size: estimated metagenome size (e.g. '5m', '100m', '1g')."
        ),
        "required_parameters": [
            {"name": "input_reads", "type": "str", "description": "Path to long-read FASTQ file."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "read_type", "type": "str", "default": "nano-raw", "description": "Read type: 'nano-raw', 'nano-hq', 'pacbio-raw', 'pacbio-hifi'."},
            {"name": "genome_size", "type": "str", "default": "5m", "description": "Estimated metagenome size (e.g. '100m', '1g')."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra Flye CLI flags."},
        ],
        "returns": "dict(assembly_fasta, assembly_info, log, output_dir)",
    },

    # =========================================================================
    # MAPPING & COVERAGE
    # =========================================================================
    {
        "name": "run_minimap2",
        "description": (
            "Align reads to a reference genome or assembly using minimap2. "
            "Supports short reads (preset='sr'), Nanopore reads ('map-ont'), "
            "PacBio ('map-pb'), and assembly alignment ('asm5'). "
            "Optionally produces a sorted and indexed BAM file via samtools."
        ),
        "required_parameters": [
            {"name": "reads", "type": "str", "description": "Path to reads FASTQ/FASTA."},
            {"name": "reference", "type": "str", "description": "Path to reference genome or assembly FASTA."},
            {"name": "output_bam", "type": "str", "description": "Output BAM file path."},
        ],
        "optional_parameters": [
            {"name": "preset", "type": "str", "default": "sr", "description": "Minimap2 preset: 'sr', 'map-ont', 'map-pb', 'asm5'."},
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "sort_and_index", "type": "bool", "default": True, "description": "Sort and index BAM output."},
        ],
        "returns": "dict(bam_path, index_path, flagstat)",
    },
    {
        "name": "run_bowtie2",
        "description": (
            "Align Illumina paired-end or single-end reads to a reference using Bowtie2. "
            "reference_index: path prefix of Bowtie2 index (without .bt2 extension). "
            "Use 'bowtie2-build ref.fa ref_index' to build the index first."
        ),
        "required_parameters": [
            {"name": "reads_r1", "type": "str", "description": "Path to R1 reads."},
            {"name": "reference_index", "type": "str", "description": "Bowtie2 index prefix."},
            {"name": "output_bam", "type": "str", "description": "Output BAM path."},
        ],
        "optional_parameters": [
            {"name": "reads_r2", "type": "str", "default": None, "description": "Path to R2 reads for paired-end."},
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "sort_and_index", "type": "bool", "default": True, "description": "Sort and index BAM."},
        ],
        "returns": "dict(bam_path, index_path, alignment_rate)",
    },
    {
        "name": "compute_coverage_samtools",
        "description": (
            "Compute per-contig/chromosome coverage statistics from a sorted BAM file using samtools coverage. "
            "Produces a TSV with coverage%, mean depth, mean base quality, and mean mapping quality per reference sequence. "
            "Essential input for MetaBAT2 binning."
        ),
        "required_parameters": [
            {"name": "bam_path", "type": "str", "description": "Path to sorted BAM file."},
            {"name": "output_tsv", "type": "str", "description": "Output TSV file path."},
        ],
        "optional_parameters": [
            {"name": "min_mapping_quality", "type": "int", "default": 20, "description": "Minimum mapping quality threshold."},
        ],
        "returns": "dict(coverage_tsv, mean_coverage_across_contigs, n_contigs)",
    },
    {
        "name": "sort_index_bam",
        "description": (
            "Sort and index a BAM file using samtools. "
            "Required before coverage computation, variant calling, or MetaBAT2 binning."
        ),
        "required_parameters": [
            {"name": "bam_path", "type": "str", "description": "Path to unsorted BAM file."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads for sorting."},
        ],
        "returns": "dict(sorted_bam, index_path)",
    },

    # =========================================================================
    # TAXONOMIC CLASSIFICATION
    # =========================================================================
    {
        "name": "run_kraken2",
        "description": (
            "Run Kraken2 for k-mer based taxonomic classification of metagenomic reads. "
            "Requires a pre-built Kraken2 database (db_path). "
            "Use the Kraken2 MiniDB (8 GB) for testing or the Standard DB (60+ GB) for production. "
            "Produces a per-read classification file and a summary report compatible with Bracken. "
            "Confidence threshold controls false-positive rate."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Directory for Kraken2 output files."},
            {"name": "reads_r1", "type": "str", "description": "Path to R1 reads (or single-end reads)."},
            {"name": "db_path", "type": "str", "description": "Path to Kraken2 database directory."},
        ],
        "optional_parameters": [
            {"name": "reads_r2", "type": "str", "default": None, "description": "Path to R2 reads for paired-end."},
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "confidence", "type": "float", "default": 0.1, "description": "Classification confidence threshold (0.0–1.0)."},
            {"name": "report_minimizer_data", "type": "bool", "default": False, "description": "Include minimizer statistics in report."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra Kraken2 CLI flags."},
        ],
        "returns": "dict(report, output, db_used, classification_summary)",
    },
    {
        "name": "run_bracken",
        "description": (
            "Run Bracken to re-estimate species or genus level abundances from Kraken2 reports "
            "using Bayesian re-estimation for improved accuracy. "
            "level: 'S' (species), 'G' (genus), 'F' (family), 'P' (phylum). "
            "Requires the same Kraken2 database used for classification."
        ),
        "required_parameters": [
            {"name": "kraken2_report", "type": "str", "description": "Path to Kraken2 report file."},
            {"name": "db_path", "type": "str", "description": "Path to Kraken2/Bracken database."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "level", "type": "str", "default": "S", "description": "Taxonomic level: 'S', 'G', 'F', 'O', 'C', 'P'."},
            {"name": "read_length", "type": "int", "default": 150, "description": "Average read length."},
            {"name": "threshold", "type": "int", "default": 10, "description": "Minimum read count threshold."},
        ],
        "returns": "dict(bracken_output, bracken_report, level)",
    },
    {
        "name": "run_metaphlan4",
        "description": (
            "Run MetaPhlAn4 for marker-gene based taxonomic profiling of metagenomes. "
            "Uses a curated database of ~1.1M clade-specific marker genes. "
            "Produces relative abundance profiles at all taxonomic levels. "
            "More specific than Kraken2 but slower; ideal for relative quantification."
        ),
        "required_parameters": [
            {"name": "input_reads", "type": "str", "description": "Path to FASTQ reads or Bowtie2 output file."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "db_path", "type": "str", "default": None, "description": "Custom MetaPhlAn4 database index path."},
            {"name": "input_type", "type": "str", "default": "fastq", "description": "Input type: 'fastq', 'fasta', 'bowtie2out', 'sam'."},
            {"name": "analysis_type", "type": "str", "default": "rel_ab_w_read_stats", "description": "Analysis type: 'rel_ab', 'rel_ab_w_read_stats', 'reads_map', 'clade_profiles'."},
        ],
        "returns": "dict(profile_tsv, bowtie2out, output_dir)",
    },
    {
        "name": "run_gtdbtk",
        "description": (
            "Run GTDB-Tk to classify metagenome-assembled genomes (MAGs) using "
            "the Genome Taxonomy Database (GTDB), which provides phylogenomics-based taxonomy. "
            "bins_dir: directory containing MAG FASTA files (one genome per file). "
            "Produces species-level classification, placement trees, and ANI distances."
        ),
        "required_parameters": [
            {"name": "bins_dir", "type": "str", "description": "Directory containing MAG FASTA files."},
            {"name": "output_dir", "type": "str", "description": "Output directory for GTDB-Tk results."},
            {"name": "db_path", "type": "str", "description": "Path to GTDB-Tk reference database (GTDBTK_DATA_PATH)."},
        ],
        "optional_parameters": [
            {"name": "extension", "type": "str", "default": "fa", "description": "Extension of MAG FASTA files (e.g., 'fa', 'fasta', 'fna')."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "skip_ani_screen", "type": "bool", "default": False, "description": "Skip ANI screening step (faster but less accurate)."},
        ],
        "returns": "dict(summary_tsv, ar53_summary, classify_dir)",
    },
    {
        "name": "run_krona",
        "description": (
            "Generate an interactive Krona HTML visualization from a Kraken2 or Bracken report. "
            "Produces a zoomable radial pie chart showing taxonomic composition at all levels."
        ),
        "required_parameters": [
            {"name": "kraken2_report", "type": "str", "description": "Path to Kraken2 or Bracken report file."},
            {"name": "output_html", "type": "str", "description": "Output HTML file path."},
        ],
        "optional_parameters": [
            {"name": "input_type", "type": "str", "default": "kraken2", "description": "Input type: 'kraken2' or 'text'."},
        ],
        "returns": "dict(html_path)",
    },

    # =========================================================================
    # BINNING
    # =========================================================================
    {
        "name": "run_metabat2",
        "description": (
            "Run MetaBAT2 to bin assembled contigs into metagenome-assembled genomes (MAGs). "
            "Uses tetranucleotide frequencies and coverage depth (from BAM files) for binning. "
            "bam_paths: list of sorted+indexed BAM files mapped against the assembly. "
            "min_contig: minimum contig length to include (recommended: 1500–2500 bp)."
        ),
        "required_parameters": [
            {"name": "assembly_fasta", "type": "str", "description": "Path to assembled contigs FASTA."},
            {"name": "output_dir", "type": "str", "description": "Output directory for bins."},
        ],
        "optional_parameters": [
            {"name": "bam_paths", "type": "list", "default": None, "description": "List of sorted BAM files for coverage-based binning."},
            {"name": "min_contig", "type": "int", "default": 2500, "description": "Minimum contig length in bp."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
        ],
        "returns": "dict(bins_dir, n_bins, bin_files, depth_file)",
    },
    {
        "name": "run_das_tool",
        "description": (
            "Run DAS_Tool to dereplicate and refine bins from multiple binning algorithms "
            "(e.g., MetaBAT2 + CONCOCT + MaxBin2). "
            "Selects the best non-redundant set of MAGs using a scoring function. "
            "bins_scaffolds_tsv_list: list of scaffold-to-bin TSV files (one per binner). "
            "binner_names: corresponding binner name labels."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str", "description": "Path to assembled contigs FASTA."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
            {"name": "bins_scaffolds_tsv_list", "type": "list", "description": "List of scaffold-to-bin TSV files from each binner."},
            {"name": "binner_names", "type": "list", "description": "Binner name labels matching bins_scaffolds_tsv_list order."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "score_threshold", "type": "float", "default": 0.5, "description": "Minimum DAS_Tool score to retain a bin."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra DAS_Tool CLI flags."},
        ],
        "returns": "dict(refined_bins_dir, summary_tsv, n_refined_bins)",
    },
    {
        "name": "run_checkm2",
        "description": (
            "Run CheckM2 to assess completeness and contamination of MAGs using ML models. "
            "Much faster than CheckM1 and does not require a marker gene reference database. "
            "bins_dir: directory of MAG FASTA files to assess."
        ),
        "required_parameters": [
            {"name": "bins_dir", "type": "str", "description": "Directory containing MAG FASTA files."},
            {"name": "output_dir", "type": "str", "description": "Output directory for quality report."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "db_path", "type": "str", "default": None, "description": "Path to CheckM2 database (auto-downloads if not provided)."},
            {"name": "extension", "type": "str", "default": "fa", "description": "Extension of MAG files."},
        ],
        "returns": "dict(quality_report_tsv, n_bins_assessed, mean_completeness, mean_contamination, output_dir)",
    },

    # =========================================================================
    # FUNCTIONAL ANNOTATION
    # =========================================================================
    {
        "name": "run_prokka",
        "description": (
            "Run Prokka for rapid prokaryotic genome annotation of assembled contigs or MAGs. "
            "Identifies coding sequences (CDS), rRNAs, tRNAs, and tmRNAs. "
            "metagenome=True enables metagenome mode with shorter minimum ORF length. "
            "Outputs GFF, GenBank, protein FASTA, and annotation TSV."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str", "description": "Path to assembled contigs or MAG FASTA."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "sample_name", "type": "str", "default": "metagenome", "description": "Output file prefix."},
            {"name": "kingdom", "type": "str", "default": "Bacteria", "description": "Kingdom: 'Bacteria', 'Archaea', 'Viruses'."},
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "metagenome", "type": "bool", "default": True, "description": "Enable Prokka metagenome mode."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra Prokka CLI flags."},
        ],
        "returns": "dict(gff, faa, ffn, tsv, gbk, txt_stats, output_dir)",
    },
    {
        "name": "run_prodigal",
        "description": (
            "Run Prodigal for ab-initio gene prediction in prokaryotic sequences. "
            "Recommended for individual MAGs or assembled contigs before DIAMOND/HMMER annotation. "
            "mode: 'meta' (metagenomics), 'single' (isolated genome), 'anon' (anonymous)."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Path to contig/MAG FASTA."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "meta", "description": "Prediction mode: 'meta', 'single', 'anon'."},
            {"name": "output_format", "type": "str", "default": "gff", "description": "Output format: 'gff' or 'gbk'."},
        ],
        "returns": "dict(coords_file, proteins_faa, genes_fna)",
    },
    {
        "name": "run_diamond",
        "description": (
            "Run DIAMOND for ultra-fast protein or translated nucleotide sequence alignment "
            "against a protein database (e.g., NR, UniRef90, UniProt, KEGG). "
            "mode: 'blastp' (protein vs protein DB) or 'blastx' (nucleotide vs protein DB). "
            "db_path: pre-built DIAMOND database (.dmnd). Use 'diamond makedb' to create one."
        ),
        "required_parameters": [
            {"name": "query_fasta", "type": "str", "description": "Path to query protein or nucleotide FASTA."},
            {"name": "db_path", "type": "str", "description": "Path to DIAMOND database (.dmnd)."},
            {"name": "output_dir", "type": "str", "description": "Output directory for hits TSV."},
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "blastp", "description": "Alignment mode: 'blastp' or 'blastx'."},
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "max_target_seqs", "type": "int", "default": 5, "description": "Max target sequences per query."},
            {"name": "evalue", "type": "float", "default": 1e-5, "description": "E-value cutoff."},
            {"name": "output_format", "type": "str", "default": "6 qseqid sseqid pident length evalue bitscore stitle", "description": "DIAMOND output format string."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra DIAMOND CLI flags."},
        ],
        "returns": "dict(hits_tsv, n_hits)",
    },
    {
        "name": "run_hmmer",
        "description": (
            "Run HMMER for protein family annotation using hidden Markov model profiles. "
            "Commonly used to annotate proteins against Pfam, TIGRFAM, or COG databases. "
            "program: 'hmmsearch' (query=HMM profiles, target=protein sequences). "
            "hmm_db: path to HMM database file (e.g., Pfam-A.hmm)."
        ),
        "required_parameters": [
            {"name": "query_fasta", "type": "str", "description": "Path to protein FASTA for annotation."},
            {"name": "hmm_db", "type": "str", "description": "Path to HMM profile database file."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 8, "description": "CPU threads."},
            {"name": "evalue", "type": "float", "default": 1e-5, "description": "E-value cutoff."},
            {"name": "program", "type": "str", "default": "hmmsearch", "description": "HMMER program: 'hmmsearch' or 'hmmscan'."},
        ],
        "returns": "dict(tblout, domtblout, n_hits)",
    },
    {
        "name": "run_humann3",
        "description": (
            "Run HUMAnN3 for functional profiling of metagenomes: "
            "pathway abundance, pathway coverage, and gene family (UniRef90) tables. "
            "Combines MetaPhlAn4 taxonomic profiling with UniRef/ChocoPhlAn nucleotide and protein searches. "
            "Input can be raw FASTQ reads or pre-classified MetaPhlAn output."
        ),
        "required_parameters": [
            {"name": "input_reads", "type": "str", "description": "Path to FASTQ reads or MetaPhlAn output."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "nucleotide_db", "type": "str", "default": None, "description": "Path to ChocoPhlAn nucleotide database."},
            {"name": "protein_db", "type": "str", "default": None, "description": "Path to UniRef protein database."},
            {"name": "bypass_nucleotide_search", "type": "bool", "default": False, "description": "Skip nucleotide search (faster, less sensitive)."},
            {"name": "extra_args", "type": "str", "default": "", "description": "Extra HUMAnN3 CLI flags."},
        ],
        "returns": "dict(pathabundance_tsv, pathcoverage_tsv, genefamilies_tsv, output_dir)",
    },

    # =========================================================================
    # AMR & VIRULENCE
    # =========================================================================
    {
        "name": "run_amrfinderplus",
        "description": (
            "Run NCBI AMRFinderPlus to identify antimicrobial resistance genes (ARGs), "
            "stress response genes, and virulence factors in protein or nucleotide sequences. "
            "Uses the NCBI Bacterial Antimicrobial Resistance Reference Gene Database. "
            "organism: restrict point mutation detection (e.g. 'Escherichia', 'Klebsiella')."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Path to protein FASTA (protein=True) or nucleotide FASTA (protein=False)."},
            {"name": "output_dir", "type": "str", "description": "Output directory for AMR report."},
        ],
        "optional_parameters": [
            {"name": "organism", "type": "str", "default": None, "description": "Organism name for point mutation detection."},
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "db_path", "type": "str", "default": None, "description": "Path to AMRFinderPlus database."},
            {"name": "protein", "type": "bool", "default": True, "description": "True if input is protein FASTA, False for nucleotide."},
        ],
        "returns": "dict(amr_report_tsv, n_hits)",
    },
    {
        "name": "run_rgi_card",
        "description": (
            "Run RGI (Resistance Gene Identifier) against the CARD database to detect "
            "antimicrobial resistance genes in assembled contigs or protein sequences. "
            "Detects perfect matches, strict matches, and loose matches to resistance genes. "
            "input_type: 'contig', 'protein', or 'read'."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Path to contig FASTA or protein FASTA."},
            {"name": "output_dir", "type": "str", "description": "Output directory."},
        ],
        "optional_parameters": [
            {"name": "input_type", "type": "str", "default": "contig", "description": "Input type: 'contig', 'protein', 'read'."},
            {"name": "alignment_tool", "type": "str", "default": "BLAST", "description": "Alignment tool: 'BLAST' or 'DIAMOND'."},
            {"name": "db_path", "type": "str", "default": None, "description": "Path to CARD database JSON."},
            {"name": "threads", "type": "int", "default": 4, "description": "CPU threads."},
            {"name": "low_quality", "type": "bool", "default": False, "description": "Enable low quality/coverage reporting."},
        ],
        "returns": "dict(rgi_tsv, json_report, n_hits)",
    },

    # =========================================================================
    # STATISTICS & VISUALIZATION
    # =========================================================================
    {
        "name": "run_microbiome_diversity",
        "description": (
            "Compute alpha diversity (Shannon index, observed OTUs) and beta diversity "
            "(Bray-Curtis dissimilarity matrix) from a microbial abundance table. "
            "Produces a TSV of alpha metrics, a Bray-Curtis distance matrix, and "
            "publication-quality PNG plots (bar chart and heatmap). "
            "abundance_table: TSV/CSV with taxa as rows and samples as columns."
        ),
        "required_parameters": [
            {"name": "abundance_table", "type": "str", "description": "Path to abundance TSV/CSV (taxa x samples)."},
            {"name": "output_dir", "type": "str", "description": "Output directory for diversity results and plots."},
        ],
        "optional_parameters": [
            {"name": "sample_metadata", "type": "str", "default": None, "description": "Optional metadata TSV for group comparisons."},
            {"name": "grouping_column", "type": "str", "default": None, "description": "Column in metadata for group-level comparisons."},
            {"name": "metrics", "type": "list", "default": ["shannon", "observed_otus", "bray_curtis"], "description": "List of diversity metrics to compute."},
        ],
        "returns": "dict(files[alpha_tsv, beta_tsv, alpha_plot, beta_heatmap], output_dir)",
    },
]