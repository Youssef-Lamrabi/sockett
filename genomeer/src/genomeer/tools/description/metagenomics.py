"""
Metagenomics tool descriptions for ToolRegistry.
All tools here are CLI wrappers — the LLM generates subprocess.run() calls.
Detailed usage snippets live in tools/software/resources.py.
"""

description = [
    # ── READ QC ──────────────────────────────────────────────────────────────
    {
        "name": "run_fastqc",
        "description": (
            "[CLI Tool][TIMEOUT: 120s] FastQC: per-read quality control on raw FASTQ files. "
            "Generates an HTML report and a zip archive with per-base quality scores, "
            "adapter content, GC distribution, duplication levels, and overrepresented sequences. "
            "Command: fastqc sample_R1.fastq.gz sample_R2.fastq.gz -o output_dir -t threads. "
            "Inspect report to decide trimming parameters before assembly or mapping. "
            "Does NOT modify reads — read-only QC diagnostic tool."
        ),
        "required_parameters": [
            {"name": "reads", "type": "list",
             "description": "List of FASTQ file paths (R1 and optionally R2)."},
            {"name": "output_dir", "type": "str",
             "description": "Directory where HTML + zip reports are written."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4,
             "description": "Number of files processed in parallel."},
        ],
        "returns": "dict(report_html, report_zip, summary_txt, per_base_quality_ok)",
    },
    {
        "name": "run_fastp",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] fastp: all-in-one FASTQ adapter trimming, quality filtering, "
            "and QC reporting. Handles paired-end reads natively. "
            "Command: fastp -i R1.fastq.gz -I R2.fastq.gz -o R1_clean.fastq.gz -O R2_clean.fastq.gz "
            "--json fastp.json --html fastp.html -q 20 -l 50 --thread 4. "
            "Outputs: trimmed reads + fastp.json (machine-readable stats) + fastp.html (visual report). "
            "fastp.json always written — parse it for: summary.filtering_result.passed_filter_reads, "
            "summary.before_filtering.q30_rate, summary.after_filtering.q30_rate."
        ),
        "required_parameters": [
            {"name": "read1", "type": "str", "description": "Path to R1 FASTQ (or single-end FASTQ)."},
            {"name": "output1", "type": "str", "description": "Path for trimmed R1 output."},
        ],
        "optional_parameters": [
            {"name": "read2", "type": "str", "default": None,
             "description": "Path to R2 FASTQ (paired-end). Omit for single-end."},
            {"name": "output2", "type": "str", "default": None,
             "description": "Path for trimmed R2 output (required if read2 provided)."},
            {"name": "json_path", "type": "str", "default": "fastp.json"},
            {"name": "html_path", "type": "str", "default": "fastp.html"},
            {"name": "min_quality", "type": "int", "default": 20,
             "description": "Phred quality threshold (-q flag)."},
            {"name": "min_length", "type": "int", "default": 50,
             "description": "Minimum read length after trimming (-l flag)."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(output1, output2, json_path, html_path, passed_reads, q30_rate_after)",
    },

    # ── ASSEMBLY ─────────────────────────────────────────────────────────────
    {
        "name": "run_megahit",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] MEGAHIT: ultra-fast de-novo metagenomic assembler. "
            "Optimized for large, complex metagenomes with variable coverage. "
            "Command: megahit -1 R1.fastq.gz -2 R2.fastq.gz -o output_dir -t threads --min-contig-len 500. "
            "Single-end: megahit -r reads.fastq.gz -o output_dir. "
            "Output: output_dir/final.contigs.fa — assembled contigs FASTA. "
            "Key options: --k-min 21 --k-max 141 --k-step 10 for complex communities; "
            "--min-contig-len 500 to discard very short contigs before downstream steps. "
            "THREADS: use -t N (e.g. -t 4) — MEGAHIT does NOT accept '--threads' "
            "(it raises 'option --threads not recognized'). Also do NOT use '--num-cpu-threads'; "
            "the correct flag is the short form -t."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Output directory (must not exist)."},
        ],
        "optional_parameters": [
            {"name": "read1", "type": "str", "default": None,
             "description": "R1 FASTQ path (paired-end)."},
            {"name": "read2", "type": "str", "default": None,
             "description": "R2 FASTQ path (paired-end)."},
            {"name": "reads", "type": "str", "default": None,
             "description": "Single-end FASTQ path (-r flag)."},
            {"name": "min_contig_len", "type": "int", "default": 500},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "memory", "type": "float", "default": 0.9,
             "description": "Max fraction of RAM to use (0–1)."},
        ],
        "returns": "dict(contigs_fasta, contig_count, summary)",
    },

    # ── READ MAPPING ──────────────────────────────────────────────────────────
    {
        "name": "run_minimap2",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] minimap2: fast read mapping for coverage estimation. "
            "Essential for generating per-contig coverage depth required by MetaBAT2 binning. "
            "Paired-end mapping: minimap2 -ax sr contigs.fa R1.fastq R2.fastq | samtools sort -o mapped.bam. "
            "Then index: samtools index mapped.bam. "
            "Then compute coverage: jgi_summarize_bam_contig_depths --outputDepth depth.txt mapped.bam. "
            "Presets: -ax sr (short reads Illumina), -ax map-ont (Oxford Nanopore), -ax map-pb (PacBio)."
        ),
        "required_parameters": [
            {"name": "reference_fasta", "type": "str",
             "description": "Reference/contigs FASTA to map reads against."},
            {"name": "reads", "type": "list",
             "description": "List of FASTQ paths (1 for single-end, 2 for paired-end)."},
            {"name": "output_bam", "type": "str",
             "description": "Path for sorted, indexed output BAM file."},
        ],
        "optional_parameters": [
            {"name": "preset", "type": "str", "default": "sr",
             "description": "Mapping preset: sr (short reads), map-ont, map-pb."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(bam_path, depth_txt, mapped_reads, summary)",
    },

    # ── BINNING ───────────────────────────────────────────────────────────────
    {
        "name": "run_metabat2",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] MetaBAT2: metagenomic binning using coverage + tetranucleotide frequency. "
            "Requires contigs FASTA + coverage depth file from jgi_summarize_bam_contig_depths. "
            "Workflow: "
            "(1) Map reads: minimap2 -ax sr contigs.fa R1.fq R2.fq | samtools sort -o mapped.bam && samtools index mapped.bam. "
            "(2) Compute depth: jgi_summarize_bam_contig_depths --outputDepth depth.txt mapped.bam. "
            "(3) Bin: metabat2 -i contigs.fa -a depth.txt -o bins_dir/bin -m 1500. "
            "Output: bins_dir/bin.1.fa, bin.2.fa, ... (one FASTA per MAG). "
            "IMPORTANT: -o sets the output PREFIX (not a directory) — MetaBAT2 creates files named <prefix>.N.fa."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str", "description": "Assembled contigs FASTA."},
            {"name": "depth_file", "type": "str",
             "description": "Coverage depth file from jgi_summarize_bam_contig_depths."},
            {"name": "output_prefix", "type": "str",
             "description": "Output prefix path (e.g. bins_dir/bin). NOT a directory."},
        ],
        "optional_parameters": [
            {"name": "min_contig", "type": "int", "default": 1500,
             "description": "Minimum contig length for binning (-m flag)."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(bins_dir, bin_count, bin_fastas, summary)",
    },

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
            "Command: run_MaxBin.pl -contig contigs.fna -out output_prefix -abund coverage.tsv "
            "(NOTE: the MaxBin2 executable is named run_MaxBin.pl — NO '2' — even though the tool is "
            "MaxBin v2.2.7). "
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
    {
        "name": "run_das_tool",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] DAS_Tool 1.1.7 (installed in meta-env1 via wrapper): "
            "CONSENSUS / refinement of bins from MULTIPLE binners into a single, better non-redundant "
            "bin set. Run it AFTER you have binned the SAME assembly with 2+ binners (e.g. MetaBAT2 + "
            "MaxBin2 + SemiBin2 + CONCOCT) — it scores every candidate bin by single-copy genes and keeps "
            "the best, de-duplicated set (usually MORE complete, LESS contaminated than any single binner). "
            "STEP 1 build a contig-to-bin table per binner with the bundled helper: "
            "Fasta_to_Contig2Bin.sh -i metabat_bins/ -e fa > metabat.tsv  (repeat for each binner's bin dir; "
            "-e is the bin FASTA extension, fa/fasta). STEP 2 run DAS_Tool: "
            "DAS_Tool -i metabat.tsv,maxbin.tsv,semibin.tsv -l metabat,maxbin,semibin -c contigs.fa "
            "-o dastool_out/PREFIX --write_bins -t 4 [--score_threshold 0.5]. "
            "The refined bins land in dastool_out/PREFIX_DASTool_bins/ (feed THESE to CheckM2/dRep, not the "
            "raw per-binner bins). Uses diamond (bundled in its isolated env). "
            "INVOKE AS CLI via subprocess — do NOT import it. Needs the SAME assembly FASTA the binners used, "
            "and each -i table must match its -l label order. If only ONE binner was run, DAS_Tool is "
            "optional (nothing to reconcile) — just use that binner's bins directly. "
            "SPECIES-RECOVERY GUARD: the default --score_threshold 0.5 can DROP a real genome when two "
            "closely-related species (same family/genus, e.g. two Enterobacteriaceae) are present at "
            "unequal depth — the shallower one scores below 0.5 and vanishes. ALWAYS compare the refined "
            "bin_count against the EXPECTED community richness (# species the user simulated/expects). If "
            "fewer bins come out than species expected, lower --score_threshold to 0.3 AND/OR recover any "
            "per-binner bin that CheckM2 rates >=90% complete and <=5% contaminated which DAS_Tool discarded "
            "(e.g. a clean SemiBin2 bin), rather than silently reporting the smaller set."
        ),
        "required_parameters": [
            {"name": "contig2bin_tables", "type": "str",
             "description": "Comma-separated contig2bin TSVs (one per binner), built via Fasta_to_Contig2Bin.sh."},
            {"name": "labels", "type": "str", "description": "Comma-separated binner labels, SAME order as tables."},
            {"name": "contigs_fasta", "type": "str", "description": "The assembly FASTA the binners used."},
            {"name": "output_prefix", "type": "str", "description": "Output prefix (…_DASTool_bins/ holds refined bins)."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "score_threshold", "type": "float", "default": 0.5,
             "description": "Minimum bin score to keep (0-1); lower = more bins kept."},
        ],
        "returns": "dict(refined_bins_dir, bin_count, summary_tsv)",
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
    {
        "name": "run_gunc",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] GUNC (installed in meta-env1 via wrapper): CHIMERISM & "
            "contamination detection for MAGs/bins — the complement to CheckM2. CheckM2 uses single-copy "
            "marker genes (can UNDER-estimate contamination when two CLOSELY-RELATED genomes co-bin, e.g. "
            "E. coli + Klebsiella); GUNC instead maps ALL genes to a reference and flags a bin whose genes "
            "come from MULTIPLE divergent lineages = a CHIMERA. Run it right after CheckM2 to catch merged "
            "bins CheckM2 missed. "
            "Command (dir of bins): gunc run --input_dir bins_dir/ --file_suffix .fa -r "
            "/home/workshop/gunc_db/gunc_db_progenomes2.1.dmnd -o gunc_out -t 4 . "
            "Single genome: gunc run -i mag.fa -r <db.dmnd> -o gunc_out. "
            "OUTPUT: gunc_out/GUNC.progenomes_2.1.maxCSS_level.tsv — KEY columns per genome: "
            "`pass.GUNC` (True=clean / False=chimeric), `clade_separation_score` (CSS; >0.45 ≈ chimeric), "
            "`contamination_portion`, `n_effective_surplus_clades`. A bin with pass.GUNC=False or CSS>0.45 "
            "is a likely chimera of 2+ genomes — if an expected taxon is 'missing', it may be MERGED into "
            "that bin. Needs diamond (bundled) + the progenomes DB (~6.7GB, already downloaded). INVOKE AS "
            "A CLI via subprocess — do NOT import it (isolated env). Runs in meta-env1 via the wrapper."
        ),
        "required_parameters": [
            {"name": "input", "type": "str", "description": "A bins DIRECTORY (--input_dir) or a single genome FASTA (-i)."},
            {"name": "output_dir", "type": "str", "description": "Output dir for GUNC.*.maxCSS_level.tsv."},
        ],
        "optional_parameters": [
            {"name": "db_file", "type": "str",
             "default": "/home/workshop/gunc_db/gunc_db_progenomes2.1.dmnd",
             "description": "GUNC reference diamond DB (progenomes2.1)."},
            {"name": "file_suffix", "type": "str", "default": ".fa",
             "description": "Bin FASTA extension when using --input_dir (.fa/.fasta)."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(gunc_tsv, n_chimeric, summary)",
    },

    # ── TAXONOMIC CLASSIFICATION ──────────────────────────────────────────────
    {
        "name": "run_kraken2",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] Kraken2: ultrafast taxonomic classification using exact k-mer matches. "
            "AVAILABLE in meta-env1. The STANDARD-8 database (bacteria + archaea + viral + human, capped 8GB) "
            "is installed at /home/workshop/kraken2_standard8 — use that as --db (this REPLACES the old "
            "viral-only DB; bacterial reads now classify properly). Bracken kmer_distrib files (50-300mers) "
            "are in the SAME dir, so bracken works against the same --db. "
            "Command: kraken2 --db /home/workshop/kraken2_standard8 --threads N --output output.kraken "
            "--report report.txt --gzip-compressed reads.fastq.gz. "
            "Paired-end: add --paired reads_1.fastq reads_2.fastq. "
            "PERFORMANCE (this host is RAM-constrained): by default kraken2 copies the ENTIRE 8GB DB into "
            "process RAM on every call, which on a near-full-RAM machine triggers swapping and makes the "
            "DB LOAD (not the classification) the dominant cost — especially wasteful for a single small "
            "genome/contig input. ALWAYS pass --memory-mapping so kraken2 mmaps the DB (no 8GB allocation, "
            "uses the OS page cache → warm/fast on repeated runs, no swap thrash). Plain FASTA input does "
            "NOT need --gzip-compressed. Example for a genome: "
            "kraken2 --db /home/workshop/kraken2_standard8 --memory-mapping --threads N "
            "--output out.kraken --report report.txt contigs.fna. "
            "REPORTING (CRITICAL — clade vs direct, common silent bug): to state '% assigned to species X', "
            "read the REPORT file (6 tab cols: pct_clade, reads_clade, reads_DIRECT, rank, taxid, name) and "
            "use COLUMN 1 (pct_clade) / COLUMN 2 (reads_clade) of the row whose rank=='S' and name matches X "
            "— pct_clade ALREADY rolls up all descendants (subspecies/strain). Do NOT count only sequences "
            "labelled with the EXACT species taxid in the .kraken output (column 3 = direct reads): a genome's "
            "contigs usually classify at the STRAIN level (e.g. taxid 1125630 HS11286), so the exact-species "
            "tally is near 0 and gives a misleading '0.06%' even though the species CLADE is ~99-100%. "
            "Example: report row ' 71.43  5  1  S  573  Klebsiella pneumoniae' → 71.43% of contigs are in the "
            "K. pneumoniae clade (5 of 7), which is the number to report — NOT the direct count of 1."
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
            "[CLI Tool][TIMEOUT: 1800s] EggNOG-mapper v2.1.13: functional annotation via orthology. "
            "Maps proteins to eggNOG OGs → COG categories, GO terms, KEGG pathways, EC numbers. "
            "AVAILABLE in meta-env1; eggNOG DB v5.0.2 installed at /home/workshop/eggnog_db (do NOT "
            "download it). EXACT command (run via meta-env1): "
            "emapper.py -i <proteins.faa> -o <output_prefix> --cpu N --data_dir /home/workshop/eggnog_db "
            "--output_dir <out_dir> -m diamond --sensmode fast. (-m diamond uses the installed "
            "eggnog_proteins.dmnd; needs a PROTEIN FASTA — use Prokka/Prodigal .faa.) "
            "SPEED: emapper defaults to --sensmode sensitive which is SLOW (many diamond index passes over "
            "the 5M-protein DB); ALWAYS pass --sensmode fast (do NOT add --dmnd_algo ctg — it conflicts with "
            "fast mode's --iterate and aborts). Expect a few minutes regardless (5M-protein DB). "
            "Outputs <output_prefix>.emapper.annotations (TSV)."
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
            "[CLI Tool][TIMEOUT: 3600s] antiSMASH v8.0.4: antibiotic and secondary metabolite biosynthetic "
            "gene cluster (BGC) detection. Full genome or metagenomic contigs input. AVAILABLE in meta-env1; "
            "databases installed at /home/workshop/antismash_db (do NOT download them). EXACT command "
            "(run via meta-env1): antismash --taxon bacteria --output-dir <output_dir> --genefinding-tool "
            "prodigal --cpus N --databases /home/workshop/antismash_db <contigs.fna>. "
            "(Use --genefinding-tool prodigal for a raw FASTA; omit it if the input is annotated GenBank.) "
            "CRITICAL — OUTPUT DIR: antiSMASH ABORTS (exit 1) if <output_dir> already exists and is "
            "NON-EMPTY; there is NO --force/--overwrite flag. So on a RE-RUN/RETRY the leftover dir from "
            "the previous attempt causes the SAME failure forever. ALWAYS make the path clean first: in your "
            "Python, before launching, do `import shutil, os; shutil.rmtree(output_dir, ignore_errors=True)` "
            "(this only removes antiSMASH's OWN previous output, never user data) — OR point --output-dir at a "
            "fresh, not-yet-existing path (e.g. add a unique suffix). Never reuse a populated antiSMASH dir. "
            "Outputs HTML report + regions.js with detected BGC types (NRPS, PKS, terpene, etc.). "
            "COUNTING BGCs (CRITICAL — robust recipe; a fragile regions.js parse produced an EMPTY "
            "bgc_counts.tsv even though 30 BGCs existed): the SIMPLEST and most reliable count of BGCs is "
            "the number of region GenBank files: glob.glob(os.path.join(output_dir, '*.region*.gbk')) — "
            "EACH such file is exactly ONE BGC region. So #BGCs = len(that glob). For the BGC TYPE of each "
            "region, open the .gbk and read the /product= qualifier of the 'region' (or 'cand_cluster') "
            "feature (e.g. `for ln in open(gbk): if '/product=' in ln: ...`). Do NOT depend solely on "
            "parsing regions.js (its JS-object format breaks naive json.load). SANITY CHECK: if you found "
            "region .gbk files but your per-strain count is 0 / the summary TSV has only a header, you "
            "globbed the wrong directory (e.g. used the strain NAME while the dir is named by accession) — "
            "re-glob the ACTUAL output_dir you passed to antiSMASH for this genome."
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
            "[CLI Tool][TIMEOUT: 1800s] geNomad v1.12.0: identification of viruses and plasmids in "
            "metagenomes/genomes (neural-network classifiers). AVAILABLE in meta-env1; database already "
            "installed at /home/workshop/genomad_db (do NOT download it). EXACT command (run via meta-env1): "
            "genomad end-to-end --cleanup --splits 8 <contigs.fna> <output_dir> /home/workshop/genomad_db. "
            "(--splits 8 keeps memory low; positional args are INPUT OUTPUT DATABASE in that order.) "
            "Outputs in <output_dir>: *_summary/<name>_virus_summary.tsv and *_summary/<name>_plasmid_summary.tsv "
            "with scores + gene annotations. Use genomad for virus/plasmid detection instead of asking the user."
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
            "Multi-database: run abricate multiple times and merge with abricate --summary. "
            "INTERPRETATION: flag last-resort resistance by GENE-NAME family (carbapenemase: "
            "KPC/NDM/OXA-48/VIM/IMP/GES...; colistin: mcr-1..mcr-10; vancomycin: vanA/B/...), NOT by a "
            "substring of CARD's drug_class — efflux/porin/regulator genes (KpnG/H, OmpK37, marA, ramA, "
            "acrAB) carry a 'carbapenem' annotation but are INTRINSIC, not acquired carbapenemases. "
            "When merging DBs, de-duplicate gene names (the same gene appears under different aliases) "
            "before reporting a distinct-gene total."
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
            "[CLI Tool][TIMEOUT: 600s] dbCAN v5.2.9: Carbohydrate-Active enZyme (CAZyme) annotation. "
            "AVAILABLE in meta-env1; database already installed at /home/workshop/dbcan_db (do NOT "
            "download it). The binary is 'run_dbcan' (NOT 'run_dbcan.py'); v5 uses SUBCOMMANDS. "
            "EXACT command for protein CAZyme annotation (run via meta-env1): "
            "run_dbcan CAZyme_annotation --mode protein --input_raw_data <proteins.faa> "
            "--output_dir <out> --db_dir /home/workshop/dbcan_db. "
            "(--mode is one of protein|prok|meta; for a genome/contigs use --mode prok and pass the .fna.) "
            "Output: <out>/overview.tsv. PARSING (CRITICAL — column names changed in v5, naive parsers "
            "silently return 0): the v5 overview.tsv has EXACTLY these tab columns: 'Gene ID', 'EC#', "
            "'dbCAN_hmm' (NOT 'HMMER'), 'dbCAN_sub', 'DIAMOND', '#ofTools', 'Recommend Results', 'Substrate'. "
            "Parse with csv.DictReader(delimiter='\\t'). A gene HAS a CAZyme assignment when ANY of the three "
            "tool columns (dbCAN_hmm / dbCAN_sub / DIAMOND) is not '-'/empty; count of unique families = the "
            "set of family tokens in those columns (strip subfamily suffixes like GH13_31 / CBM56_e2(54-139) "
            "→ GH13 / CBM56). 'High-confidence' = int(row['#ofTools']) >= 2. NEVER key on 'HMMER' or "
            "'Signalp' (old-format names absent in v5) → that yields 0. SANITY CHECK: a bacterial genome "
            "normally has dozens-to-hundreds of CAZymes; a parsed count of 0 means you used the wrong "
            "column names — re-read overview.tsv (it has one data row PER assigned gene)."
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
            "[R Package][TIMEOUT: 300s] Phyloseq + vegan: microbiome community analysis in R. "
            "Run via Rscript in the amplicon-env1 env (same env as DADA2). "
            "Alpha diversity (Shannon, Simpson, Chao1), beta diversity (Bray-Curtis, "
            "weighted/unweighted UniFrac), ordination (PCoA, NMDS), differential abundance, "
            "visualization. PERMANOVA — test whether a metadata factor (e.g. elevation, treatment) "
            "significantly explains community differences — uses vegan::adonis2(dist ~ factor, "
            "data=meta, permutations=999) on the beta-diversity distance matrix; report R2 and "
            "p-value. Input: OTU/ASV table TSV + taxonomy TSV + sample metadata TSV (with the "
            "grouping column). Emit the code as a PURE R block (first line `#!R`) — the executor "
            "runs it with Rscript inside amplicon-env1 automatically (no Python wrapper)."
        ),
        "required_parameters": [
            {"name": "otu_table", "type": "str", "description": "OTU/ASV count table TSV (features x samples)."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "tax_table", "type": "str", "default": None,
             "description": "Taxonomy table TSV."},
            {"name": "metadata", "type": "str", "default": None,
             "description": "Sample metadata TSV (required for PERMANOVA grouping factor)."},
            {"name": "analysis", "type": "list",
             "default": ["alpha_diversity", "beta_diversity", "ordination"],
             "description": "Analyses: alpha_diversity, beta_diversity, ordination, permanova."},
            {"name": "permanova_factor", "type": "str", "default": None,
             "description": "Metadata column to test with PERMANOVA (vegan::adonis2)."},
        ],
        "returns": "dict(alpha_div_tsv, beta_div_tsv, ordination_plot, permanova_tsv, summary)",
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

    # ── BIN DEREPLICATION ─────────────────────────────────────────────────────
    # DAS_Tool is now INSTALLED (isolated dastool_env + wrapper in meta-env1/bin) and
    # its ACTIVE run_das_tool description lives near the binners above. This old
    # "disabled" placeholder was removed (2026-07-08). DasToolContract already validates it.

    # ── ABUNDANCE RE-ESTIMATION ───────────────────────────────────────────────
    {
        "name": "run_bracken",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] Bracken: Bayesian re-estimation of species abundances from Kraken2 reports. "
            "Corrects for read length and k-mer classification biases in Kraken2 output. "
            "Requires a Bracken-built database (same as Kraken2 DB). "
            "Command: bracken -d kraken2_db -i kraken2_report.txt -o output.bracken "
            "-w bracken_report.txt -r read_length -l S -t threshold. "
            "CRITICAL — the `-r <read_length>` MUST match an EXISTING `database<R>mers.kmer_distrib` file "
            "in the DB directory; Bracken kmer files exist only at BUILT lengths (for "
            "/home/workshop/kraken2_standard8 these are 50,75,100,150,200,250,300). Do NOT pass the raw "
            "mean read length (e.g. 200.8 -> 201 has NO file -> Bracken fails, real bug). Instead, in "
            "Python, glob `database*mers.kmer_distrib` in the DB dir, parse the available integer R "
            "values, and pass the one NEAREST to the mean read length (200.8 -> 200). Never hard-code 201."
        ),
        "required_parameters": [
            {"name": "kraken2_report", "type": "str",
             "description": "Kraken2 report file (from --report flag)."},
            {"name": "db_path", "type": "str", "description": "Path to Kraken2/Bracken database directory."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "read_length", "type": "int", "default": 150,
             "description": "Average read length in bp."},
            {"name": "level", "type": "str", "default": "S",
             "description": "Taxonomic level: D, P, C, O, F, G, S."},
            {"name": "threshold", "type": "int", "default": 10,
             "description": "Minimum number of reads for a taxon to be counted."},
        ],
        "returns": "dict(bracken_tsv, report_txt, species_count, returncode, stdout, stderr)",
    },

    # ── MARKER-GENE PROFILING ─────────────────────────────────────────────────
    {
        "name": "run_metaphlan4",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] MetaPhlAn 4.2.4: marker-gene taxonomic profiling. "
            "NOT AVAILABLE YET — the binary is installed but its ~20GB database is NOT downloaded "
            "(deferred for disk space). DO NOT use metaphlan: it will fail with a missing-DB error. "
            "For taxonomic profiling / 'what organisms are in this sample' from shotgun reads, use "
            "run_kraken2 instead (Standard-8 DB IS installed) — optionally followed by bracken for "
            "species-level relative abundance."
        ),
        "required_parameters": [
            {"name": "reads", "type": "list",
             "description": "Input FASTQ files (one or two for paired-end)."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "db_path", "type": "str", "default": None,
             "description": "Path to MetaPhlAn bowtie2 database directory."},
            {"name": "analysis_type", "type": "str", "default": "rel_ab_w_read_stats",
             "description": "Analysis type: rel_ab, rel_ab_w_read_stats, reads_map, clade_profiles, marker_ab_table."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(profile_tsv, bowtie2_out, species_count, returncode, stdout, stderr)",
    },

    # ── PHYLOGENETIC CLASSIFICATION ───────────────────────────────────────────
    {
        "name": "run_gtdbtk",
        "description": (
            "[CLI Tool][TIMEOUT: 7200s] GTDB-Tk: phylogenetic classification of MAGs against the GTDB reference tree. "
            "Requires GTDB-Tk reference data (set GTDBTK_DATA_PATH env variable). "
            "Command: gtdbtk classify_wf --genome_dir bins/ --out_dir output/ "
            "--cpus N --pplacer_cpus 1 --extension fna. "
            "Outputs gtdbtk.bac120.summary.tsv and gtdbtk.ar53.summary.tsv with taxonomy assignments."
        ),
        "required_parameters": [
            {"name": "bins_dir", "type": "str",
             "description": "Directory containing bin FASTA files."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "extension", "type": "str", "default": "fna",
             "description": "File extension of bin files (fna, fa, fasta)."},
            {"name": "cpus", "type": "int", "default": 4},
            {"name": "pplacer_cpus", "type": "int", "default": 1,
             "description": "CPUs for pplacer placement step (memory-intensive, keep low)."},
            {"name": "skip_ani_screen", "type": "bool", "default": False,
             "description": "Skip ANI screening step (faster but less accurate)."},
        ],
        "returns": "dict(bac120_summary_tsv, ar53_summary_tsv, classified_count, output_dir, returncode, stdout, stderr)",
    },

    # ── GENOME ANNOTATION ─────────────────────────────────────────────────────
    {
        "name": "run_prokka",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] Prokka: rapid prokaryote whole genome annotation. "
            "Annotates CDS, rRNA, tRNA, tmRNA, signal peptides, non-coding RNA. "
            "Command: prokka --outdir output_dir --prefix prokka --kingdom Bacteria "
            "--cpus N --force contigs.fna. "
            "Outputs: .gff (annotation), .gbk (GenBank), .faa (protein sequences), "
            ".ffn (gene sequences), .txt (summary with CDS count)."
        ),
        "required_parameters": [
            {"name": "contigs_fasta", "type": "str",
             "description": "Input genome/contig FASTA."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "prefix", "type": "str", "default": "prokka",
             "description": "Output file prefix."},
            {"name": "kingdom", "type": "str", "default": "Bacteria",
             "description": "Annotation kingdom: Bacteria, Archaea, Mitochondria, Viruses."},
            {"name": "genus", "type": "str", "default": "",
             "description": "Genus name for better annotation lookup."},
            {"name": "species", "type": "str", "default": "",
             "description": "Species name for better annotation lookup."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(gff, gbk, faa, ffn, summary_txt, cds_count, output_dir, returncode, stdout, stderr)",
    },

    # ── LONG-READ POLISHING ───────────────────────────────────────────────────
    {
        "name": "run_medaka",
        "description": (
            "[CLI Tool][TIMEOUT: 7200s] Medaka: consensus polishing for Oxford Nanopore Technology (ONT) assemblies. "
            "Uses neural network models trained on specific ONT flowcell/basecaller combinations. "
            "Command: medaka_consensus -i reads.fastq -d assembly.fasta -o output_dir -m model -t N. "
            "Common models: r941_min_hac_g507 (MinION HAC), r1041_e82_400bps_sup_v4.2.0 (R10.4.1). "
            "Outputs consensus.fasta with polished sequences."
        ),
        "required_parameters": [
            {"name": "assembly_fasta", "type": "str",
             "description": "Draft assembly FASTA to polish."},
            {"name": "reads_fastq", "type": "str",
             "description": "ONT reads FASTQ used for polishing."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "model", "type": "str", "default": "r941_min_hac_g507",
             "description": "Medaka model matching flowcell and basecaller version."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(consensus_fasta, sequence_count, output_dir, returncode, stdout, stderr)",
    },

    # ── RESISTOME ─────────────────────────────────────────────────────────────
    {
        "name": "run_rgi",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] RGI (Resistance Gene Identifier) v6.0.8: AMR gene prediction "
            "against the CARD database (installed: CARD v4.0.1, already loaded globally — do NOT run "
            "'rgi load'). AVAILABLE in meta-env1. Prefer RGI over abricate when the user explicitly asks "
            "for RGI / CARD / 'perfect and strict' calls or wants resistance MECHANISM + AMR gene family. "
            "EXACT command (run via meta-env1 so diamond/blast are on PATH): "
            "rgi main -i <genome.fna> -o <out_prefix> -t contig --clean -n <threads> -g PYRODIGAL -a DIAMOND. "
            "CRITICAL: use -g PYRODIGAL (the external prodigal path crashes with a missing .temp.draft "
            "FileNotFoundError); threads flag is -n (NOT --num_threads). NEVER pass --local: CARD is "
            "loaded GLOBALLY, so --local makes RGI look for ./localDB/card.json (which does NOT exist) "
            "and fails with \"No such file or directory: localDB/card.json\". Do NOT run 'rgi load' "
            "either (already done). Perfect+Strict are reported by "
            "default; add --include_loose only if low-confidence hits are wanted. "
            "Output: <out_prefix>.txt (TSV) + <out_prefix>.json. Key columns: 'Best_Hit_ARO' (gene), "
            "'Cut_Off' (Perfect/Strict/Loose), 'Drug Class', 'Resistance Mechanism', 'AMR Gene Family'. "
            "PARSING (CRITICAL — common silent bug): ALWAYS parse the FLAT TSV <out_prefix>.txt with "
            "csv.DictReader(delimiter='\\t') and filter on row['Cut_Off'] in ('Perfect','Strict'). NEVER "
            "parse <out_prefix>.json for the gene list — it is a DEEPLY NESTED per-ORF dict with NO "
            "top-level 'Cut_Off'/'Best_Hit_ARO' keys, so a naive json.load()+row.get('Cut_Off') silently "
            "yields 0 genes (this exact mistake made a consensus table report '0 RGI hits' on a genome that "
            "actually had blaKPC-2 Perfect). SANITY CHECK: if your RGI gene count is 0 on a real isolate "
            "genome that AMRFinder/abricate flagged as resistant, you almost certainly parsed the wrong "
            "file/column — re-read the .txt before concluding 0. For a consensus/agreement table, re-read "
            "BOTH callers' on-disk output files at consensus time (do NOT trust an in-memory count from an "
            "earlier or retried step). "
            "Flag true last-resort carbapenemases by gene-name family (KPC/NDM/OXA-48/VIM/IMP/GES), NOT by "
            "a 'carbapenem' substring in Drug Class (efflux/porin genes carry that annotation but are intrinsic)."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str",
             "description": "Input FASTA: protein (.faa), contig (.fna), or reads (.fastq)."},
            {"name": "output_prefix", "type": "str"},
        ],
        "optional_parameters": [
            {"name": "input_type", "type": "str", "default": "protein",
             "description": "Input type: protein, contig, or read."},
            {"name": "alignment_tool", "type": "str", "default": "DIAMOND",
             "description": "Alignment tool: DIAMOND or BLAST."},
            {"name": "include_loose", "type": "bool", "default": False,
             "description": "Include loose hits (lower confidence) in output."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(results_tsv, amr_gene_count, amr_genes_detected, returncode, stdout, stderr)",
    },
    {
        "name": "run_amrfinder",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] NCBI AMRFinderPlus v4.2.7: identification of AMR, stress, and "
            "virulence genes against the curated NCBI AMR database (already installed + updated — do NOT "
            "run 'amrfinder -u'). AVAILABLE in meta-env1. Prefer AMRFinderPlus over abricate when the user "
            "explicitly asks for AMRFinderPlus / the NCBI AMR database, or wants point-mutation detection. "
            "EXACT command (run via meta-env1) on a GENOME FASTA (nucleotide): "
            "amrfinder -n <genome.fna> -o <out.tsv> --plus --threads <n> [--organism Klebsiella_pneumoniae]. "
            "Use -p <proteins.faa> instead of -n for a protein FASTA. --plus adds stress/virulence genes; "
            "--organism <Name> enables species point-mutation calls (allowed names include Klebsiella_pneumoniae, "
            "Escherichia, Salmonella, Acinetobacter_baumannii, Staphylococcus_aureus — omit if unsupported). "
            "Output TSV EXACT column names (v4.2.7 — use these VERBATIM, they were renamed from older "
            "versions): 'Element symbol' (the gene name, e.g. blaCTX-M-14 — NOT 'Gene symbol'), "
            "'Element name' (description), 'Type' (AMR/VIRULENCE/STRESS — NOT 'Element type'), 'Subtype', "
            "'Class' (drug class), 'Subclass', 'Method'. Filter AMR rows with df['Type']=='AMR' (the gene "
            "is df['Element symbol']). Flag last-resort carbapenemases by gene-name family "
            "(blaKPC/blaNDM/blaOXA-48/blaVIM/blaIMP). "
            "CRITICAL when merging with RGI: AMRFinderPlus output is ALREADY curated and high-confidence "
            "— it has NO 'Cut_Off'/'Perfect/Strict' column. NEVER drop AMRFinder hits by an RGI-style "
            "confidence filter, and NEVER look for columns named 'Gene symbol' or 'Element type' (they do "
            "NOT exist → you get 0 genes). Real bug: AMRFinder produced 32 hits but the parser used the "
            "wrong column names and reported 0. Include every AMR-typed row."
        ),
        "required_parameters": [
            {"name": "proteins_faa", "type": "str",
             "description": "Input protein FASTA (.faa)."},
            {"name": "output_file", "type": "str",
             "description": "Output TSV file path."},
        ],
        "optional_parameters": [
            {"name": "organism", "type": "str", "default": None,
             "description": "Organism name for point mutation detection (e.g. Escherichia, Klebsiella)."},
            {"name": "plus", "type": "bool", "default": True,
             "description": "Report stress and virulence genes in addition to AMR."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(results_tsv, amr_gene_count, amr_genes_detected, drug_classes, returncode, stdout, stderr)",
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
    {
        "name": "run_dada2",
        "description": (
            "[R Package][TIMEOUT: 3600s] DADA2: amplicon (16S/18S/ITS) denoising — infers exact "
            "amplicon sequence variants (ASVs) from paired-end Illumina reads. Emit the code as a "
            "PURE R block (first line `#!R`, NOT a Python wrapper) — the executor runs it with "
            "Rscript inside amplicon-env1 automatically. "
            "Pipeline in R: library(dada2); filterAndTrim(fwd, filtF, rev, filtR, truncLen=c(F,R), "
            "maxEE=c(2,2), truncQ=2, rm.phix=TRUE); learnErrors(); dada(); mergePairs(); "
            "makeSequenceTable(); removeBimeraDenovo(); assignTaxonomy(seqtab, 'silva_nr99_*_train_set.fa.gz'). "
            "Outputs: ASV table TSV + taxonomy TSV (feed into run_phyloseq for diversity/PERMANOVA). "
            "DADA2 is for AMPLICON marker-gene reads ONLY — never for shotgun metagenomes or whole genomes. "
            "CRITICAL: paired R1/R2 must OVERLAP for mergePairs — simulate/use a SHORT amplicon "
            "(~250-400 bp V-region, NOT full-length 16S ~1500 bp), else merging yields 0 ASVs."
        ),
        "required_parameters": [
            {"name": "reads_fwd", "type": "str", "description": "Forward (R1) FASTQ path(s)."},
            {"name": "reads_rev", "type": "str", "description": "Reverse (R2) FASTQ path(s)."},
            {"name": "output_dir", "type": "str", "description": "Directory for ASV table + taxonomy outputs."},
        ],
        "optional_parameters": [
            {"name": "trunc_len_f", "type": "int", "default": 0,
             "description": "Truncate forward reads at this length (0 = no truncation)."},
            {"name": "trunc_len_r", "type": "int", "default": 0,
             "description": "Truncate reverse reads at this length."},
            {"name": "silva_train_set", "type": "str", "default": None,
             "description": "Path to SILVA train-set fasta.gz for assignTaxonomy (optional)."},
        ],
        "returns": "dict(asv_table_tsv, taxonomy_tsv, track_reads_tsv, summary)",
    },
    {
        "name": "run_multiqc",
        "description": (
            "[CLI Tool][TIMEOUT: 300s] MultiQC: aggregate QC reports from many tools "
            "(FastQC, fastp, Kraken2, QUAST, samtools, Bowtie2, ...) into ONE interactive HTML. "
            "Command: multiqc <input_dir> -o <output_dir>. Run AFTER QC/mapping steps to "
            "summarize all per-sample reports at once. Output: multiqc_report.html + data dir."
        ),
        "required_parameters": [
            {"name": "input_dir", "type": "str", "description": "Directory containing tool logs/reports to scan."},
            {"name": "output_dir", "type": "str"},
        ],
        "optional_parameters": [],
        "returns": "dict(report_html, data_dir, summary)",
    },
    {
        "name": "run_insilicoseq",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] InSilicoSeq (iss): modern read simulator for amplicon and "
            "shotgun Illumina data — the recommended way to simulate test reads (replaces grinder). "
            "REQUIRED when the simulated reads will feed DADA2: iss produces realistic per-base "
            "quality scores, whereas wgsim's flat/uniform quality makes DADA2 learnErrors fail "
            "('Error matrix is NULL'). For any DADA2/amplicon test data, use iss, NOT wgsim. "
            "COVERAGE: --n_reads is TOTAL reads — for an ASSEMBLY task size it for ~50x depth "
            "(n_reads ≈ 50 * genome_bp / read_len; e.g. ~1.6M for a 5 Mb genome), NOT a fixed 200k "
            "(~6x → fragmented assembly). "
            "Command: iss generate --genomes refs.fa --n_reads 100000 --model miseq "
            "--output out_prefix --cpus 4. Produces out_prefix_R1.fastq + out_prefix_R2.fastq "
            "(paired-end) with realistic error models. Use --abundance to set community proportions. "
            "For 16S amplicon test data, give 16S reference sequences as --genomes. "
            "PERFORMANCE (iss is SLOW — pure-Python, per-read KDE error model: the miseq/hiseq models "
            "take MINUTES per ~1M reads even with --cpus, and ~2M reads can run 10+ min): "
            "  * Reserve --model miseq/hiseq ONLY for data that will feed DADA2 (needs realistic "
            "    quality). For a plain SHOTGUN ASSEMBLY/binning test, the error realism does NOT matter "
            "    — use the MUCH faster `--mode basic` (iss generate --mode basic ...), OR simply use "
            "    wgsim (C, orders of magnitude faster). "
            "  * Keep --n_reads modest: a 3–5 genome mock assembles fine at ~30–50x; do NOT request "
            "    2M+ pairs 'for safety' — that mainly buys a long wait. "
            "(For simple whole-genome shotgun, wgsim is the fast default; use iss only when realistic "
            "Illumina quality is required.)"
        ),
        "required_parameters": [
            {"name": "genomes", "type": "str", "description": "Reference FASTA to simulate reads from."},
            {"name": "output_prefix", "type": "str", "description": "Output prefix (produces <prefix>_R1/_R2.fastq)."},
        ],
        "optional_parameters": [
            {"name": "n_reads", "type": "int", "default": 100000},
            {"name": "model", "type": "str", "default": "miseq",
             "description": "Error model: miseq, hiseq, novaseq, or a custom model file."},
            {"name": "abundance", "type": "str", "default": None,
             "description": "Abundance distribution (uniform, lognormal, ...) or a file."},
            {"name": "cpus", "type": "int", "default": 4},
        ],
        "returns": "dict(reads_r1, reads_r2, abundance_tsv, summary)",
    },
    {
        "name": "run_coverm",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] CoverM: fast per-genome/MAG and per-contig coverage & "
            "RELATIVE ABUNDANCE from short reads or pre-made BAMs. This is the standard way to "
            "quantify how abundant each MAG/genome is across one or MANY samples — the basis for "
            "comparative and time-series metagenomics (cross-sample abundance tables). "
            "TWO MODES: "
            "(1) per-genome/MAG abundance — "
            "coverm genome --coupled S1_R1.fq S1_R2.fq [S2_R1.fq S2_R2.fq ...] "
            "--genome-fasta-directory bins_dir -x fa -m relative_abundance -t 4 -o abundance.tsv ; "
            "(2) per-contig coverage — "
            "coverm contig --coupled R1.fq R2.fq --reference contigs.fa -m mean -t 4 -o contig_cov.tsv . "
            "Use pre-made sorted BAMs with --bam-files a.bam b.bam instead of --coupled to avoid re-mapping. "
            "Methods (-m): relative_abundance, mean, covered_fraction, count, tpm, rpkm, trimmed_mean. "
            "For a multi-sample table pass MULTIPLE --coupled R1 R2 pairs (or multiple --bam-files); "
            "output TSV columns are one per sample, rows are genomes/contigs. "
            "Does NOT modify reads. Single static binary in meta-env1 (no database needed)."
        ),
        "required_parameters": [
            {"name": "mode", "type": "str",
             "description": "'genome' (per-MAG relative abundance) or 'contig' (per-contig coverage)."},
            {"name": "output_path", "type": "str",
             "description": "Path for the output coverage/abundance TSV."},
        ],
        "optional_parameters": [
            {"name": "coupled_reads", "type": "list", "default": None,
             "description": "Flat list of paired FASTQ files R1 R2 [R1 R2 ...]; one pair per sample for multi-sample tables."},
            {"name": "bam_files", "type": "list", "default": None,
             "description": "Pre-made sorted BAM files (alternative to coupled_reads; skips re-mapping)."},
            {"name": "genome_fasta_directory", "type": "str", "default": None,
             "description": "Directory of MAG/genome FASTAs (genome mode). Use with extension below."},
            {"name": "extension", "type": "str", "default": "fa",
             "description": "FASTA extension in the genome directory (genome mode), e.g. fa or fasta."},
            {"name": "reference", "type": "str", "default": None,
             "description": "Contigs/reference FASTA (contig mode)."},
            {"name": "method", "type": "str", "default": "relative_abundance",
             "description": "Coverage metric: relative_abundance|mean|covered_fraction|count|tpm|rpkm|trimmed_mean."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(output_path, summary)",
    },
    {
        "name": "run_irep",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] iRep: estimates the in-situ REPLICATION RATE (index of "
            "replication, iRep value) of a genome/MAG from the coverage skew between origin and "
            "terminus of replication. A higher iRep means more cells were actively replicating when "
            "the sample was taken (growth-activity proxy). "
            "Command: iRep -f genome.fa [genome2.fa ...] -s reads_vs_genome.sam [sample2.sam ...] "
            "-o out_prefix -t 4 . Inputs: (a) one or more genome/MAG FASTAs, (b) a SAM file per sample "
            "of reads mapped to THAT genome (bowtie2/bwa SAM, NOT BAM). Writes out_prefix.tsv with the "
            "iRep value per genome per sample plus a PDF plot. "
            "RELIABILITY CAVEATS (report them): iRep needs a fairly COMPLETE genome (>=75%), LOW "
            "contamination, and EVEN coverage of >=5x; results from coverage <5x, highly fragmented "
            "bins, or degraded/biased libraries are statistically UNRELIABLE and should be flagged, "
            "not reported as growth rates. Runs inside meta-env1. "
            "INVOKE AS A CLI via subprocess (e.g. subprocess.run(['iRep','-f',...,'-s',...,'-o',...])) — "
            "do NOT `import iRep`/`import irep` in Python: it lives in an isolated venv and is NOT "
            "importable from the run environment."
        ),
        "required_parameters": [
            {"name": "genome_fastas", "type": "list",
             "description": "List of genome/MAG FASTA paths to estimate replication for."},
            {"name": "sam_files", "type": "list",
             "description": "List of SAM files (reads mapped to the genome), one per sample. SAM, not BAM."},
            {"name": "output_prefix", "type": "str",
             "description": "Output prefix for the .tsv table and .pdf plot."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "min_coverage", "type": "float", "default": 5.0,
             "description": "Minimum mean coverage to trust an iRep value; below this, flag as unreliable."},
        ],
        "returns": "dict(output_prefix, summary)",
    },
    {
        "name": "run_instrain",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] inStrain 1.10 (installed in meta-env1 via wrapper): STRAIN-LEVEL "
            "microdiversity — per-position SNVs, nucleotide diversity (pi), coverage, and popANI/conANI "
            "between samples on the SAME reference. This is the tool for 'is it the SAME strain across "
            "samples/timepoints?' and 'clonal vs distinct populations?' — e.g. tracking whether a "
            "resistant lineage is one clone spreading vs several. "
            "TWO STEPS: (1) inStrain profile mapping.bam reference.fasta -o out.IS -p 4 [-g genes.fna] "
            "[-s scaffold2bin.stb] — needs a SORTED BAM of reads mapped to the reference (bwa/minimap2 → "
            "samtools sort) and the reference FASTA; optionally a Prodigal genes .fna for dN/dS and a "
            "scaffold-to-bin (.stb) file to profile per-MAG. (2) inStrain compare -i s1.IS s2.IS -o "
            "cmp.IS -p 4 — compares two+ profiles for popANI/conANI (cross-sample strain identity). "
            "This writes only the PER-SCAFFOLD `output/*_comparisonsTable.tsv` (columns scaffold,name1,"
            "name2,...,popANI,conANI — NO `genome` column). "
            "(3) TO GET PER-MAG popANI you MUST run a THIRD step: inStrain genome_wide -i cmp.IS -s "
            "scaffold2bin.stb -p 4 — this aggregates the per-scaffold comparison into PER-GENOME and "
            "writes `cmp.IS/output/*_genomeWide_compare.tsv` WITH a `genome` column + per-genome popANI/"
            "conANI. IMPORTANT: on `compare`, `-s` means a scaffolds LIST (NOT the stb) — do NOT pass the "
            "stb to compare; pass the stb to `genome_wide`. Read 'the Klebsiella MAG' popANI from the "
            "genomeWide table, NOT from comparisonsTable (which has no `genome` column). "
            "KEY OUTPUTS: profile -> output/*_genome_info.tsv (per-genome nucleotide_diversity, coverage, "
            "breadth), *_SNVs.tsv. genome_wide -> output/*_genomeWide_compare.tsv (genome, popANI, conANI, "
            "percent_compared; popANI>0.99999 ≈ same strain). "
            "RELIABILITY: needs decent coverage/breadth per genome — flag low-coverage "
            "genomes as unreliable, same spirit as iRep. INVOKE AS A CLI via subprocess — do NOT import "
            "it (isolated env). Runs in meta-env1 via the wrapper."
        ),
        "required_parameters": [
            {"name": "bam", "type": "str", "description": "Sorted BAM of reads mapped to the reference (profile mode)."},
            {"name": "reference_fasta", "type": "str", "description": "Reference FASTA the reads were mapped to."},
            {"name": "output_dir", "type": "str", "description": "Output .IS directory."},
        ],
        "optional_parameters": [
            {"name": "genes_fna", "type": "str", "default": None,
             "description": "Prodigal gene FASTA (.fna) to enable dN/dS gene-level metrics."},
            {"name": "stb", "type": "str", "default": None,
             "description": "scaffold-to-bin file to profile per-MAG instead of whole reference."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(output_dir, genome_info_tsv, snvs_tsv, comparison_tsv)",
    },
    {
        "name": "run_mob_recon",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] MOB-suite (mob_recon): reconstructs PLASMIDS from a genome "
            "assembly and predicts their MOBILITY. For each contig it decides chromosome vs plasmid, "
            "groups plasmid contigs into individual plasmids, and reports replicon type (Inc group), "
            "relaxase/MOB type, mate-pair formation markers and a predicted_mobility label "
            "(conjugative | mobilizable | non-mobilizable). This is the tool to answer 'is this AMR/"
            "resistance gene carried on a MOBILE plasmid?' — cross the AMR gene's contig id with "
            "mob_recon's contig_report.txt to see if that contig is plasmid-borne and conjugative. "
            "Command: mob_recon -i assembly.fasta -o out_dir -n 4 [--force]. "
            "Outputs in out_dir: contig_report.txt (per-contig chromosome/plasmid + cluster), "
            "mobtyper_results.txt (per-plasmid mobility/replicon/relaxase), and plasmid_*.fasta. "
            "Input is a genome/MAG assembly FASTA (best on a fairly complete isolate or bin). "
            "Needs mash + blast (present) and the MOB-suite DB (mob_init). Runs in meta-env1. "
            "INVOKE AS A CLI via subprocess (e.g. subprocess.run(['mob_recon','-i',...,'-o',...])) — "
            "do NOT `import mob_suite` in Python: it lives in an isolated venv and is NOT importable "
            "from the run environment. "
            "COUNTING PLASMIDS (real bug: an agent reported 125 plasmids by parsing the cluster ID "
            "'AC125'): the plasmid COUNT = the number of `plasmid_*.fasta` files in the output dir "
            "(each file is exactly ONE reconstructed plasmid) — equivalently, the number of DATA rows "
            "in mobtyper_results.txt (or distinct primary_cluster_id in contig_report.txt). NEVER "
            "extract a number from a plasmid/cluster ID like 'AC125'/'AA114' — those are IDs, not counts. "
            "GENE-NAME MATCHING (real bug: mob_recon reported 'NO blaKPC detected' while AMRFinder+RGI "
            "had already found the gene): when you cross-reference a resistance gene from the AMR "
            "outputs (to find which contig carries it), match the gene name FLEXIBLY — the tools spell "
            "the SAME gene differently: AMRFinder='blaKPC-2', RGI/CARD='KPC-2' or 'kpc-2', some tables "
            "'bla_KPC_2'. Normalize BOTH sides before comparing: lowercase, strip a leading 'bla', drop "
            "non-alphanumerics, then substring-match on the core token (e.g. 'kpc'). NEVER require an "
            "exact literal like 'blaKPC' — that yields a FALSE 'not detected' when the AMR file says "
            "'kpc-2'. Same rule for blaNDM/blaOXA-48/blaVIM/blaIMP/mcr. If the AMR consensus flagged a "
            "carbapenemase in a MAG, that gene IS present — mob_recon must then locate ITS contig, not "
            "report absence."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str",
             "description": "Genome/MAG assembly FASTA to reconstruct plasmids from."},
            {"name": "output_dir", "type": "str",
             "description": "Output directory (contig_report.txt, mobtyper_results.txt, plasmid_*.fasta)."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "force", "type": "bool", "default": True,
             "description": "Overwrite the output directory if it already exists (-f)."},
        ],
        "returns": "dict(output_dir, contig_report, mobtyper_results, summary)",
    },
    {
        "name": "run_mob_typer",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] MOB-suite (mob_typer): types a SINGLE already-isolated plasmid "
            "FASTA for mobility (conjugative | mobilizable | non-mobilizable), replicon/Inc type, "
            "relaxase (MOB) cluster, predicted host range and size. Use when you already have a plasmid "
            "sequence (e.g. a plasmid contig from geNomad or mob_recon) and only need its mobility/typing. "
            "Command: mob_typer --infile plasmid.fasta --out_file mobtyper_report.txt -n 4. "
            "Needs mash + blast + the MOB-suite DB (mob_init). Runs in meta-env1. "
            "INVOKE AS A CLI via subprocess (mob_typer --infile ... --out_file ...) — do NOT "
            "`import mob_suite` in Python (isolated venv, not importable from the run environment)."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Single plasmid FASTA to type."},
            {"name": "output_file", "type": "str", "description": "Path for the mobtyper TSV report."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(output_file, summary)",
    },
    {
        "name": "run_drep",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] dRep: DEREPLICATES a set of genomes/MAGs into a "
            "non-redundant set of representatives by genome-wide ANI clustering. Essential when you "
            "recover MAGs from MULTIPLE samples/binners and the same organism appears several times — "
            "dRep keeps the single best-quality representative per cluster (default species-level "
            "threshold 95% ANI). "
            "Command: dRep dereplicate OUT_DIR -g bins/*.fa -p 4 --genomeInfo genomeInfo.csv "
            "-sa 0.95 -comp 50 -con 10 . "
            "CRITICAL — `--genomeInfo` IS MANDATORY here (real bug: without it dRep produced 0 "
            "representatives): dRep's DEFAULT quality step calls CheckM v1 (`checkm`) which is NOT "
            "installed on this machine (only CheckM2 is). So you MUST pass genome quality yourself via "
            "`--genomeInfo genomeInfo.csv`, built from the CheckM2 quality_report.tsv of the prior step. "
            "genomeInfo.csv MUST have the EXACT header `genome,completeness,contamination`, one row per "
            "bin, where `genome` is the bin FILE BASENAME WITH extension (e.g. bin.1.fa) matching the "
            "-g inputs. Build it in Python: read checkm2 quality_report.tsv (columns Name, Completeness, "
            "Contamination), and write genome=<Name>.fa (or the actual bin filename), completeness, "
            "contamination. Then dRep does Mash -> fastANI -> picks representatives using YOUR scores. "
            "Outputs in OUT_DIR: dereplicated_genomes/ (the representative FASTAs), "
            "data_tables/Cdb.csv (cluster membership) and Wdb.csv (winners). "
            "Needs mash + fastANI + checkm2 (all present). Runs in meta-env1. "
            "INVOKE AS A CLI via subprocess (e.g. subprocess.run(['dRep','dereplicate',...])) — "
            "do NOT `import drep` in Python: it lives in an isolated venv and is NOT importable from "
            "the run environment (importing raises ModuleNotFoundError)."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str",
             "description": "dRep work/output directory (contains dereplicated_genomes/ and data_tables/)."},
            {"name": "genomes", "type": "list",
             "description": "List of genome/MAG FASTA paths (or a glob) to dereplicate."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
            {"name": "secondary_ani", "type": "float", "default": 0.95,
             "description": "Secondary ANI threshold for dereplication (-sa); 0.95 = species level."},
            {"name": "min_completeness", "type": "float", "default": 50.0,
             "description": "Minimum CheckM2 completeness to keep a genome (-comp)."},
            {"name": "max_contamination", "type": "float", "default": 10.0,
             "description": "Maximum CheckM2 contamination to keep a genome (-con)."},
            {"name": "genome_info", "type": "str", "default": None,
             "description": "Optional CheckM2 quality CSV (genome,completeness,contamination) to skip re-scoring."},
        ],
        "returns": "dict(output_dir, dereplicated_genomes_dir, summary)",
    },
    {
        "name": "run_blast",
        "description": (
            "[CLI Tool][TIMEOUT: 1800s] NCBI BLAST+ 2.16 — sequence similarity search. "
            "blastn (nucl-vs-nucl), blastp (prot-vs-prot), blastx/tblastn (translated), + makeblastdb "
            "to build a local DB. Typical: makeblastdb -in ref.fasta -dbtype nucl -out mydb ; then "
            "blastn -query q.fasta -db mydb -outfmt 6 -evalue 1e-5 -num_threads 4 -out hits.tsv "
            "(outfmt 6 = tabular). Use to find homologs, verify a gene/contig identity, or screen a "
            "custom reference. A local BLAST database is installed at /home/workshop/tools/BLAST. "
            "Available in meta-env1."
        ),
        "required_parameters": [
            {"name": "program", "type": "str", "description": "blastn | blastp | blastx | tblastn."},
            {"name": "query", "type": "str", "description": "Query FASTA file."},
            {"name": "db", "type": "str", "description": "BLAST database prefix (build with makeblastdb if needed)."},
            {"name": "output", "type": "str", "description": "Output path (use -outfmt 6 for tabular)."},
        ],
        "optional_parameters": [
            {"name": "evalue", "type": "str", "default": "1e-5"},
            {"name": "threads", "type": "int", "default": 4},
            {"name": "max_target_seqs", "type": "int", "default": 25},
        ],
        "returns": "dict(output, hit_count, summary)",
    },
    {
        "name": "run_spades",
        "description": (
            "[CLI Tool][TIMEOUT: 3600s] SPAdes assembler — higher-accuracy assembly than MEGAHIT for "
            "ISOLATES and small/medium metagenomes (more RAM-hungry). Use metaSPAdes for metagenomes, "
            "plasmidSPAdes for plasmids. Command: spades.py --meta -1 R1.fq -2 R2.fq -o out_dir -t 4 "
            "(isolate: drop --meta; plasmids: plasmidspades.py). Output: out_dir/contigs.fasta + "
            "scaffolds.fasta. Prefer MEGAHIT for large/high-complexity metagenomes (RAM); SPAdes when "
            "accuracy matters on a small dataset. Available in meta-env1."
        ),
        "required_parameters": [
            {"name": "read1", "type": "str", "description": "R1 FASTQ."},
            {"name": "read2", "type": "str", "description": "R2 FASTQ."},
            {"name": "output_dir", "type": "str", "description": "Output directory (contigs.fasta inside)."},
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "meta", "description": "meta | isolate | plasmid."},
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(contigs, scaffolds, summary)",
    },
    {
        "name": "run_prodigal",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] Prodigal — fast prokaryotic gene/ORF prediction. Command: "
            "prodigal -i contigs.fasta -a proteins.faa -d genes.fna -o genes.gff -f gff -p meta "
            "(-p meta for metagenomes/contigs, -p single for one genome). CRITICAL: -f gff is ALWAYS "
            "required to get a real GFF (without it prodigal writes its native Genbank-like format and "
            "GFF parsers find 0 CDS). Outputs protein FASTA (.faa), gene nucleotide FASTA (.fna), and "
            "GFF. Available in meta-env1."
        ),
        "required_parameters": [
            {"name": "input_fasta", "type": "str", "description": "Contigs/genome FASTA."},
            {"name": "proteins_out", "type": "str", "description": "Output protein FASTA (-a)."},
            {"name": "gff_out", "type": "str", "description": "Output GFF (-o with -f gff)."},
        ],
        "optional_parameters": [
            {"name": "mode", "type": "str", "default": "meta", "description": "meta | single."},
        ],
        "returns": "dict(proteins, gff, orf_count)",
    },
    {
        "name": "run_wgsim",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] wgsim — fast paired-end short-read SIMULATION from a reference "
            "FASTA (for mock communities / testing). Command: wgsim -N <num_read_pairs> -1 150 -2 150 "
            "genome.fna reads_R1.fastq reads_R2.fastq. NOTE: wgsim reads carry SYNTHETIC quality (~Phred "
            "10-15), so a following fastp step should use --disable_quality_filtering (q20 would drop all "
            "reads). Faster than InSilicoSeq; use iss only when realistic error models are required. "
            "Available in meta-env1."
        ),
        "required_parameters": [
            {"name": "genome_fasta", "type": "str", "description": "Reference FASTA to simulate from."},
            {"name": "read1_out", "type": "str", "description": "Output R1 FASTQ."},
            {"name": "read2_out", "type": "str", "description": "Output R2 FASTQ."},
            {"name": "num_pairs", "type": "int", "description": "Number of read pairs (-N)."},
        ],
        "optional_parameters": [
            {"name": "read_length", "type": "int", "default": 150},
        ],
        "returns": "dict(read1, read2, num_pairs)",
    },
    {
        "name": "run_bedtools",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] bedtools — genome arithmetic on intervals (BED/GFF/VCF/BAM). "
            "Common: bedtools intersect -a A.bed -b B.bed (overlaps); bedtools merge -i sorted.bed "
            "(collapse overlapping); bedtools genomecov -ibam in.bam -bga (per-base coverage); "
            "bedtools getfasta -fi ref.fa -bed regions.bed (extract sequences). Use to overlap AMR/BGC "
            "coordinates with genes, compute coverage over regions, or extract sub-sequences. "
            "Available in meta-env1."
        ),
        "required_parameters": [
            {"name": "subcommand", "type": "str", "description": "intersect | merge | genomecov | getfasta | ..."},
            {"name": "inputs", "type": "list", "description": "Input files for the chosen subcommand (BED/GFF/BAM/FASTA)."},
        ],
        "optional_parameters": [
            {"name": "output", "type": "str", "default": None, "description": "Output path (else stdout)."},
        ],
        "returns": "dict(output, summary)",
    },
]
