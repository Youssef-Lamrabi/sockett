"""
Metagenomics tool descriptions for ToolRegistry.
All tools here are CLI wrappers — the LLM generates subprocess.run() calls.
Detailed usage snippets live in tools/software/resources.py.
"""

description = [
    # ── ASSEMBLY QC ──────────────────────────────────────────────────────────
    {
        "name": "run_quast",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] QUAST: Quality Assessment Tool for Genome Assemblies. "
            "Evaluates assembly quality metrics: N50, L50, total length, misassemblies, "
            "genome fraction. Works on metagenomic assemblies (--meta flag). "
            "Use with subprocess.run(['quast.py', contigs_fasta, '-o', output_dir, '--meta', ...])."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str", "description": "Path to assembled contigs FASTA."},
            {"name": "output_dir", "type": "str", "description": "Directory for QUAST output."},
        ],
        "optional_parameters": [
            {"name": "reference", "type": "str", "default": None, "description": "Reference genome FASTA (optional)."},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "meta", "type": "bool", "default": True, "description": "Enable metagenome mode."},
            {"name": "min_contig", "type": "int", "default": 500},
        ],
        "returns": "dict(report_tsv, report_html, N50, L50, total_length, summary)",
    },

    # ── BINNING ───────────────────────────────────────────────────────────────
    {
        "name": "run_semibin2",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] SemiBin2: deep-learning metagenomic binning. "
            "Requires contigs FASTA + sorted BAM files for coverage. "
            "Command: SemiBin2 single_easy_bin -i contigs.fna -b sorted.bam -o output_dir. "
            "Produces per-bin FASTA files in output_dir/output_bins/."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str", "description": "Assembled contigs FASTA."},
            {"name": "bam_files", "type": "list", "description": "List of sorted BAM files."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "environment", "type": "str", "default": None,
             "description": "Built-in model: human_gut, dog_gut, ocean, soil, cat_gut, etc."},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "min_len", "type": "int", "default": 1000},
        ],
        "returns": "dict(bins_dir, bin_count, summary)",
    },
    {
        "name": "run_concoct",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] CONCOCT: Clustering CONtigs with COverage and ComposiTion. "
            "Three-step pipeline: cut_up_fasta → concoct_coverage_table → concoct → merge_cutup_clustering. "
            "Use with subprocess.run(['concoct', '--composition_file', ...])."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"},
            {"name": "bam_files", "type": "list", "description": "List of sorted indexed BAM files."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "chunk_size", "type": "int", "default": 10000},
            {"name": "overlap_size", "type": "int", "default": 0},
            {"name": "clusters", "type": "int", "default": 400},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(clustering_tsv, bins_dir, summary)",
    },
    {
        "name": "run_maxbin2",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] MaxBin2: binning using marker gene sets and EM algorithm. "
            "Command: run_MaxBin2.pl -contig contigs.fna -out output_prefix -abund coverage.tsv. "
            "Outputs .fasta files per bin + summary."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"},
            {"name": "output_prefix", "type": "str", "description": "Prefix path for bin output files."},
        ],
        "optional_parameters": [
            {"name": "abund_list", "type": "str", "default": None,
             "description": "File listing coverage TSV paths (one per line)."},
            {"name": "reads", "type": "list", "default": None,
             "description": "List of reads files (alternative to coverage)."},
            {"name": "min_contig_length", "type": "int", "default": 1000},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(bins_dir, bin_count, summary_tsv)",
    },

    # ── BIN QUALITY ───────────────────────────────────────────────────────────
    {
        "name": "run_checkm2",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] CheckM2: rapid assessment of genome bin quality using ML. "
            "Predicts completeness and contamination for each bin. "
            "Command: checkm2 predict --threads N --input bins_dir/*.fna --output-directory output_dir. "
            "Produces quality_report.tsv with completeness/contamination per bin."
        ),
        "required_parameters": [
            {"name": "bins_dir", "type": "str", "description": "Directory containing bin FASTA files."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "extension", "type": "str", "default": "fna",
             "description": "File extension of bin files (fna, fa, fasta)."},
            {"name": "min_completeness", "type": "float", "default": 50.0,
             "description": "Filter bins below this completeness threshold."},
        ],
        "returns": "dict(quality_report_tsv, high_quality_bins, medium_quality_bins, summary)",
    },

    # ── TAXONOMIC CLASSIFICATION ──────────────────────────────────────────────
    {
        "name": "run_kraken2",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] Kraken2: ultrafast taxonomic classification using exact k-mer matches. "
            "Requires a Kraken2 database (--db). "
            "Command: kraken2 --db kraken2_db --threads N --output output.kraken "
            "--report report.txt --gzip-compressed reads.fastq.gz. "
            "Paired-end: add --paired reads_1.fastq reads_2.fastq."
        ),
        "required_parameters": [
            {"name": "reads", "type": "list", "description": "Input reads file(s) path(s)."},
            {"name": "db_path", "type": "str", "description": "Path to Kraken2 database directory."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "paired", "type": "bool", "default": False},
            {"name": "gzip_compressed", "type": "bool", "default": False},
            {"name": "confidence", "type": "float", "default": 0.0},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(kraken_output, report_txt, classified_count, unclassified_count, summary)",
    },
    {
        "name": "run_sylph",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] Sylph: ultrafast metagenomic profiling via ANI sketching. "
            "No database required for sketching reads; use pre-built sylph databases for profiling. "
            "Workflow: sylph sketch reads.fastq → sylph profile sketches.sylsp -d database.syldb. "
            "Extremely fast (seconds for profiling). Outputs TSV with ANI, relative abundances."
        ),
        "required_parameters": [
            {"name": "reads", "type": "list", "description": "Input FASTQ read files."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "db_path", "type": "str", "default": None,
             "description": "Sylph database (.syldb) for profiling. If None, only sketching is done."},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "min_ani", "type": "float", "default": 0.95},
        ],
        "returns": "dict(profile_tsv, sketches, summary)",
    },
    {
        "name": "run_kaiju",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] Kaiju: fast taxonomic classification using protein-level alignments. "
            "Better than k-mer methods for divergent sequences. "
            "Command: kaiju -t nodes.dmp -f kaiju_db.fmi -i reads.fastq -o output.txt. "
            "Post-process with kaiju2table for abundance summary."
        ),
        "required_parameters": [
            {"name": "reads", "type": "list"},
            {"name": "db_path", "type": "str", "description": "Directory with nodes.dmp and .fmi database."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "paired", "type": "bool", "default": False},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "taxon_rank", "type": "str", "default": "species",
             "description": "Rank for kaiju2table: phylum, class, order, family, genus, species."},
        ],
        "returns": "dict(classification_txt, summary_tsv, summary)",
    },

    # ── FUNCTIONAL ANNOTATION ─────────────────────────────────────────────────
    {
        "name": "run_hmmer",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] HMMER: profile HMM-based protein family annotation. "
            "hmmscan (query: protein, target: HMM db) or hmmsearch (query: HMM, target: protein db). "
            "Command: hmmscan --tblout hits.tsv --cpu N /path/to/db.hmm proteins.faa. "
            "Common databases: Pfam, TIGRFAMs, Resfams, KEGG."
        ),
        "required_parameters": [
            {"name": "proteins_faa", "type": "str", "description": "Input protein FASTA."},
            {"name": "hmm_db", "type": "str", "description": "Path to pressed HMM database (.h3i/.h3m files)."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "hmmscan",
             "description": "hmmscan (protein vs HMM db) or hmmsearch (HMM vs protein db)."},
            {"name": "evalue", "type": "float", "default": 1e-5},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(tblout_tsv, domtblout_tsv, hit_count, summary)",
    },
    {
        "name": "run_eggnog",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] EggNOG-mapper: functional annotation via orthology. "
            "Maps proteins to eggNOG OGs → COG categories, GO terms, KEGG pathways, EC numbers. "
            "Command: emapper.py -i proteins.faa -o output_prefix --cpu N --data_dir eggnog_data/. "
            "Requires eggnog database (download with download_eggnog_data.py)."
        ),
        "required_parameters": [
            {"name": "proteins_faa", "type": "str"},
            {"name": "output_prefix", "type": "str"},
            {"name": "data_dir", "type": "str", "description": "Path to eggNOG database directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "evalue", "type": "float", "default": 1e-3},
            {"name": "score", "type": "float", "default": 60.0},
            {"name": "tax_scope", "type": "str", "default": "auto"},
        ],
        "returns": "dict(annotations_tsv, summary_tsv, cog_counts, go_terms, kegg_pathways, summary)",
    },
    {
        "name": "run_diamond",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] DIAMOND: fast protein alignment (100x faster than BLAST). "
            "Used for NR/UniRef/custom DB searches. Two modes: blastp (protein vs protein) and "
            "blastx (translated DNA vs protein). "
            "Command: diamond blastp -q proteins.faa -d nr.dmnd -o hits.tsv --outfmt 6 -p N."
        ),
        "required_parameters": [
            {"name": "query", "type": "str", "description": "Query FASTA (protein for blastp, DNA for blastx)."},
            {"name": "db_path", "type": "str", "description": "DIAMOND database (.dmnd)."},
            {"name": "output_file", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "blastp",
             "description": "blastp or blastx."},
            {"name": "evalue", "type": "float", "default": 1e-5},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "top", "type": "int", "default": 1,
             "description": "Report top N alignments per query."},
            {"name": "outfmt", "type": "int", "default": 6,
             "description": "Output format: 6=tabular, 100=DIAMOND binary, 101=SAM."},
        ],
        "returns": "dict(hits_tsv, hit_count, summary)",
    },
    {
        "name": "run_humann3",
        "description": (
            "[CLI Tool][TIMEOUT: 7200s] HUMAnN3: functional profiling of metagenomes and metatranscriptomes. "
            "Maps reads → gene families → pathways using UniRef + MetaCyc. "
            "Command: humann --input reads.fastq --output output_dir --threads N. "
            "Outputs: genefamilies.tsv, pathabundance.tsv, pathcoverage.tsv."
        ),
        "required_parameters": [
            {"name": "input_reads", "type": "str", "description": "Input FASTQ or merged paired-end file."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "nucleotide_db", "type": "str", "default": None,
             "description": "Path to ChocoPhlAn database."},
            {"name": "protein_db", "type": "str", "default": None,
             "description": "Path to UniRef protein database."},
            {"name": "taxonomic_profile", "type": "str", "default": None,
             "description": "MetaPhlAn taxonomic profile (speeds up HUMAnN3)."},
        ],
        "returns": "dict(genefamilies_tsv, pathabundance_tsv, pathcoverage_tsv, summary)",
    },

    # ── SPECIALIZED ANNOTATION ────────────────────────────────────────────────
    {
        "name": "run_antismash",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] antiSMASH: antibiotic and secondary metabolite biosynthetic "
            "gene cluster (BGC) detection. Full genome or metagenomic contigs input. "
            "Command: antismash --taxon bacteria --output-dir output_dir --genefinding-tool prodigal "
            "--cpus N contigs.fna. "
            "Outputs HTML report + regions.js with detected BGC types (NRPS, PKS, terpene, etc.)."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Genome/contig FASTA or GenBank file."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "taxon", "type": "str", "default": "bacteria",
             "description": "bacteria or fungi."},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "genefinding_tool", "type": "str", "default": "prodigal",
             "description": "Gene prediction: prodigal, prodigal-m (metagenomes), glimmerhmm."},
            {"name": "minimal", "type": "bool", "default": False,
             "description": "Minimal mode: skip most analyses for speed."},
        ],
        "returns": "dict(html_report, bgc_regions, bgc_count, bgc_types, summary)",
    },
    {
        "name": "run_genomad",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] geNomad: identification of viruses and plasmids in metagenomes. "
            "Uses neural network classifiers. "
            "Command: genomad end-to-end --cleanup --splits 8 contigs.fna output_dir genomad_db/. "
            "Outputs virus_summary.tsv and plasmid_summary.tsv with scores and gene annotations."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"},
            {"name": "output_dir", "type": "str"},
            {"name": "db_path", "type": "str", "description": "Path to geNomad database directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "splits", "type": "int", "default": 8,
             "description": "Number of data splits (reduce for low-memory machines)."},
            {"name": "min_score", "type": "float", "default": 0.7},
        ],
        "returns": "dict(virus_summary_tsv, plasmid_summary_tsv, virus_count, plasmid_count, summary)",
    },
    {
        "name": "run_abricate",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] ABRicate: mass screening of contigs for antimicrobial resistance "
            "and virulence genes. Databases: resfinder, card, ncbi, argannot, vfdb, plasmidfinder, ecoh. "
            "Command: abricate --db resfinder --minid 80 --mincov 80 contigs.fna > results.tsv. "
            "Multi-database: run abricate multiple times and merge with abricate --summary."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str"},
            {"name": "output_file", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "db", "type": "str", "default": "resfinder",
             "description": "Database: resfinder, card, ncbi, argannot, vfdb, plasmidfinder, ecoh."},
            {"name": "minid", "type": "float", "default": 80.0,
             "description": "Minimum DNA identity %."},
            {"name": "mincov", "type": "float", "default": 80.0,
             "description": "Minimum coverage %."},
        ],
        "returns": "dict(results_tsv, gene_count, resistance_genes, summary)",
    },

    # ── SEQUENCE MANIPULATION ─────────────────────────────────────────────────
    {
        "name": "run_seqkit",
        "description": (
            "[CLI Tool][TIMEOUT: 120s] SeqKit: ultrafast toolkit for FASTA/FASTQ manipulation. "
            "Key subcommands: stats (QC summary), seq (filter/transform), grep (search by ID/pattern), "
            "split2 (split by size/count), sample (subsample), fx2tab (to TSV), rmdup (deduplicate). "
            "Command: seqkit stats -a *.fna → per-file stats with N50, GC%, etc."
        ),
        "required_parameters": [
            {"name": "subcommand", "type": "str",
             "description": "SeqKit subcommand: stats, seq, grep, split2, sample, fx2tab, rmdup, translate."},
            {"name": "input_files", "type": "list"},
        ],
        "optional_parameters": [
            {"name": "output_file", "type": "str", "default": None},
            {"name": "extra_args", "type": "list", "default": None,
             "description": "Extra CLI arguments e.g. ['-a'] for all-stats, ['-m', '500'] for min-len."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(output, summary)",
    },
    {
        "name": "run_bbduk",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] BBDuk (BBTools): adapter trimming, quality filtering, "
            "contamination removal. "
            "Command: bbduk.sh in=reads.fastq.gz out=clean.fastq.gz ref=adapters.fa "
            "ktrim=r k=23 mink=11 hdist=1 tpe tbo qtrim=r trimq=20 minlen=50. "
            "Paired-end: use in1/in2 and out1/out2."
        ),
        "required_parameters": [
            {"name": "input_reads", "type": "list", "description": "Input reads (1 or 2 files for PE)."},
            {"name": "output_reads", "type": "list", "description": "Output reads (1 or 2 files for PE)."},
        ],
        "optional_parameters": [
            {"name": "ref", "type": "str", "default": "adapters",
             "description": "Adapter reference: 'adapters' (BBTools built-in), or path to FASTA."},
            {"name": "ktrim", "type": "str", "default": "r",
             "description": "r=right trim, l=left trim, f=no trim."},
            {"name": "qtrim", "type": "str", "default": "r"},
            {"name": "trimq", "type": "int", "default": 20},
            {"name": "minlen", "type": "int", "default": 50},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(stats, reads_in, reads_out, bases_removed, summary)",
    },

    # ── FUNCTIONAL SPECIALISED ────────────────────────────────────────────────
    {
        "name": "run_dbcan",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] dbCAN: Carbohydrate-Active enZyme (CAZyme) annotation. "
            "Three tools in one: HMMER (dbCAN HMM db), DIAMOND (CAZy db), Hotpep. "
            "Command: run_dbcan.py proteins.faa protein --out_dir output_dir --db_dir db/ --tools hmmer diamond. "
            "Outputs: overview.txt with CAZyme family assignments and confidence."
        ),
        "required_parameters": [
            {"name": "proteins_faa", "type": "str"},
            {"name": "output_dir", "type": "str"},
            {"name": "db_dir", "type": "str", "description": "dbCAN database directory."},
        ],
        "optional_parameters": [
            {"name": "input_type", "type": "str", "default": "protein",
             "description": "protein or meta (metagenome, auto-calls prodigal)."},
            {"name": "tools", "type": "list", "default": ["hmmer", "diamond"],
             "description": "Tools to run: hmmer, diamond, hotpep."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(overview_tsv, cazyme_count, families, summary)",
    },
    {
        "name": "run_pharokka",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] Pharokka: fast phage annotation pipeline. "
            "Combines Phanotate/Prodigal (gene prediction) + CARD/VFDB/PHROGs (annotation). "
            "Command: pharokka.py -i phage_contigs.fna -o output_dir -d pharokka_db/ -t N. "
            "Outputs GFF, GenBank, functional summary."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Phage genome/contigs FASTA."},
            {"name": "output_dir", "type": "str"},
            {"name": "db_dir", "type": "str", "description": "Pharokka database directory."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "gene_predictor", "type": "str", "default": "phanotate",
             "description": "phanotate or prodigal."},
            {"name": "force", "type": "bool", "default": False},
        ],
        "returns": "dict(gff, gbk, phrog_summary, cds_count, summary)",
    },

    # ── COMMUNITY ANALYSIS ────────────────────────────────────────────────────
    {
        "name": "run_phyloseq",
        "description": (
            "[R Package][TIMEOUT: 300s] Phyloseq: R package for microbiome data analysis. "
            "Alpha diversity (Shannon, Simpson, Chao1), beta diversity (Bray-Curtis, UniFrac), "
            "ordination (PCoA, NMDS), differential abundance, visualization. "
            "Use with subprocess.run(['Rscript', '-e', '...R code...']). "
            "Input: OTU/ASV table TSV + taxonomy TSV + optional metadata TSV."
        ),
        "required_parameters": [
            {"name": "otu_table", "type": "str", "description": "OTU/ASV count table TSV (features x samples)."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "tax_table", "type": "str", "default": None,
             "description": "Taxonomy table TSV."},
            {"name": "metadata", "type": "str", "default": None,
             "description": "Sample metadata TSV."},
            {"name": "analysis", "type": "list",
             "default": ["alpha_diversity", "beta_diversity", "ordination"],
             "description": "Analyses to run."},
        ],
        "returns": "dict(alpha_div_tsv, beta_div_tsv, ordination_plot, summary)",
    },
    {
        "name": "run_lefse",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] LEfSe (Linear discriminant analysis Effect Size): "
            "biomarker discovery between two or more groups. "
            "Three-step pipeline: lefse_format_input.py → lefse_run.py → lefse_plot_res.py. "
            "Input: feature table TSV with class/subclass rows. LDA threshold typically 2.0."
        ),
        "required_parameters": [
            {"name": "input_tsv", "type": "str",
             "description": "Input feature table with class row (samples as columns)."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "class_row", "type": "int", "default": 0,
             "description": "Row index for class labels (0-based)."},
            {"name": "lda_threshold", "type": "float", "default": 2.0},
            {"name": "pvalue", "type": "float", "default": 0.05},
        ],
        "returns": "dict(results_tsv, significant_features, plot_png, summary)",
    },

    # ── COVERAGE ESTIMATION ───────────────────────────────────────────────────
    {
        "name": "run_nonpareil",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] Nonpareil: metagenome coverage and sequencing effort estimation. "
            "Estimates redundancy, predicts reads needed for N% coverage. "
            "Command: nonpareil -s reads.fastq -T kmer -f fastq -b output_prefix -t N. "
            "Outputs R object (.npo) → plot with nonpareil_plot.R or Nonpareil::Nonpareil.curve()."
        ),
        "required_parameters": [
            {"name": "reads_file", "type": "str", "description": "Input FASTQ reads file."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "method", "type": "str", "default": "kmer",
             "description": "kmer (fast) or alignment (accurate)."},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "subsample_n", "type": "int", "default": 1000,
             "description": "Number of query reads for estimation."},
        ],
        "returns": "dict(npo_file, coverage_estimate, redundancy, summary)",
    },
]
