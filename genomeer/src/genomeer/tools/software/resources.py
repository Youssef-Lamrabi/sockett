"""
genomeer/src/genomeer/tools/software/resources.py
===================================================
Defines the three dicts imported by BioAgent (v1 + v2):
  - data_lake_dict        : external data sources the agent can reference
  - library_content_dict  : CLI tools and Python/R packages visible in the system prompt
  - runtime_envs_dicts    : micromamba environments the agent can target

IMPORTANT: Every key added here becomes visible in the agent system prompt.
           The agent uses these descriptions to decide which tool / env to use.
"""

# ---------------------------------------------------------------------------
# DATA LAKE
# External data sources the agent can access programmatically.
# Add local paths (once databases are downloaded) or API endpoints.
# ---------------------------------------------------------------------------
data_lake_dict = {
    # ---- NCBI / SRA ----
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/": (
        "NCBI Entrez API — search and fetch sequences, taxonomy, assemblies, and metadata. "
        "Use query_ncbi_taxonomy() or download_from_ncbi() tool functions. "
        "Always use HTTPS. FTP is deprecated."
    ),
    "https://www.ebi.ac.uk/metagenomics/api/v1/": (
        "MGnify REST API — EBI Metagenomics portal with >60,000 analysed metagenomes. "
        "Search by biome (gut, soil, marine, etc.), experiment type, and study accession. "
        "Use query_mgnify_studies() and query_mgnify_samples() tool functions."
    ),

    # ---- Taxonomy ----
    "https://gtdb.ecogenomic.org/api/v2/": (
        "GTDB API — Genome Taxonomy Database. Phylogenomics-based taxonomy for bacteria and archaea. "
        "More accurate than NCBI taxonomy for MAG classification. "
        "Use query_gtdb_taxonomy() or run_gtdbtk() for local MAG classification."
    ),

    # ---- Functional annotation ----
    "https://rest.kegg.jp/": (
        "KEGG REST API — metabolic pathways, KO (KEGG Orthology) entries, enzymes, compounds. "
        "Use query_kegg_pathway(pathway_id) and query_kegg_orthology(ko_id) tool functions. "
        "KEGG pathway IDs: ko00010 (Glycolysis), ko01200 (Carbon metabolism), ko02020 (Two-component system)."
    ),
    "https://rest.uniprot.org/": (
        "UniProt REST API — curated protein sequences, functional annotation, taxonomy. "
        "Supports UniProtKB, UniRef90, UniRef50. "
        "Use query_uniprot_proteins() tool function."
    ),

    # ---- AMR / Virulence ----
    "https://card.mcmaster.ca/": (
        "CARD — Comprehensive Antibiotic Resistance Database. "
        "Reference for AMR gene detection. Use query_card_resistance() for metadata or "
        "download_card_database() + run_rgi_card() for local sequence-based detection."
    ),

    # ---- rRNA ----
    "https://www.arb-silva.de/": (
        "SILVA rRNA database — gold standard for 16S (SSU) and 23S (LSU) rRNA classification. "
        "Used by Kraken2 custom databases and QIIME2. "
        "Use query_silva_sequences() or download_silva_database() tool functions."
    ),

    # ---- Local databases (uncomment and set path once downloaded) ----
    # "/data/databases/kraken2_standard": (
    #     "Kraken2 Standard database (60 GB) — bacterial, archaeal, viral, human genomes. "
    #     "Use as db_path in run_kraken2() and run_bracken()."
    # ),
    # "/data/databases/kraken2_mini": (
    #     "Kraken2 MiniKraken2 v2 (8 GB) — reduced standard database for fast classification. "
    #     "Use as db_path in run_kraken2() for quick taxonomy screening."
    # ),
    # "/data/databases/metaphlan4": (
    #     "MetaPhlAn4 marker gene database. Auto-downloads on first run. "
    #     "Use as db_path in run_metaphlan4()."
    # ),
    # "/data/databases/gtdbtk_r220": (
    #     "GTDB-Tk r220 reference database (85 GB). Required for run_gtdbtk(). "
    #     "Set GTDBTK_DATA_PATH environment variable."
    # ),
    # "/data/databases/card": (
    #     "CARD database (card.json). Required for run_rgi_card(). "
    #     "Download with download_card_database()."
    # ),
    # "/data/databases/uniref90.dmnd": (
    #     "UniRef90 DIAMOND database for protein annotation. "
    #     "Use as db_path in run_diamond(mode='blastp')."
    # ),

    # --- Viromics ---
    "virsorter2": (
        "[CLI · meta-env1] VirSorter2 — discover viral contigs from metagenomic assemblies. "
        "Use run_virsorter2() Python wrapper. "
        "Direct: virsorter run -w vs2_out -i contigs.fa --min-length 1500 -j 4 all"
    ),
    "checkv": (
        "[CLI · meta-env1] CheckV — assess quality and completeness of viral contigs. "
        "Use run_checkv() Python wrapper. "
        "Direct: checkv end_to_end viral_contigs.fa checkv_out -t 4"
    ),
    "deepvirfinder": (
        "[CLI · meta-env1] DeepVirFinder — predict viral sequences using deep learning. "
        "Use run_deepvirfinder() Python wrapper. "
        "Direct: python dvf.py -i contigs.fa -o dvf_out -c 4"
    ),
}


# ---------------------------------------------------------------------------
# LIBRARY CONTENT
# Every key here appears in the agent system prompt under "Software Library".
# The agent uses these descriptions to write correct import/call statements.
# ---------------------------------------------------------------------------
library_content_dict = {

    # =========================================================================
    # PYTHON PACKAGES (available in bio-agent-env1)
    # =========================================================================
    "biopython": (
        "[Python · bio-agent-env1] Biological computation toolkit. "
        "Parsers for FASTA/FASTQ/GenBank/GFF, NCBI Entrez access, sequence analysis. "
        "Usage: from Bio import SeqIO, Entrez"
    ),
    "pandas": (
        "[Python · bio-agent-env1] Data manipulation and analysis. "
        "Usage: import pandas as pd; df = pd.read_csv('file.tsv', sep='\\t')"
    ),
    "matplotlib": (
        "[Python · bio-agent-env1] Plotting library. "
        "Usage: import matplotlib.pyplot as plt; plt.savefig('plot.png', dpi=150)"
    ),
    "scipy": (
        "[Python · bio-agent-env1] Scientific computing. Statistics, clustering, distance metrics. "
        "Usage: from scipy.spatial.distance import braycurtis"
    ),
    "scikit-learn": (
        "[Python · bio-agent-env1] Machine learning. PCA, clustering, dimensionality reduction. "
        "Usage: from sklearn.decomposition import PCA"
    ),
    "seaborn": (
        "[Python · bio-agent-env1] Statistical data visualization built on matplotlib. "
        "Usage: import seaborn as sns; sns.heatmap(df)"
    ),

    # =========================================================================
    # R PACKAGES (available in bio-agent-env1 via Rscript)
    # =========================================================================
    "ggplot2": (
        "[R · bio-agent-env1] Grammar of graphics plotting. "
        "Usage in #!PY: subprocess.run(['Rscript', '-e', 'library(ggplot2); ...'])"
    ),
    "vegan": (
        "[R · bio-agent-env1] Community ecology analysis. Alpha/beta diversity, ordination (NMDS, PCoA). "
        "Usage: library(vegan); diversity(otu_table, index='shannon')"
    ),
    "phyloseq": (
        "[R · bio-agent-env1] Microbiome data analysis and visualization. "
        "Handles OTU tables, taxonomy, and sample metadata in one object."
    ),

    # =========================================================================
    # CLI TOOLS — bio-agent-env1 (general)
    # =========================================================================
    "ncbi-genome-download": (
        "[CLI · bio-agent-env1] Download genomes from NCBI RefSeq/GenBank. "
        "Use download_from_ncbi() Python wrapper or directly: "
        "ncbi-genome-download bacteria --assembly-accessions GCF_000001735.4 -o ./output"
    ),
    "samtools": (
        "[CLI · bio-agent-env1 + meta-env1] BAM/SAM/CRAM manipulation. "
        "sort, index, flagstat, coverage, view. "
        "Usage: samtools sort -@ 4 -o sorted.bam input.bam && samtools index sorted.bam"
    ),
    "bowtie2": (
        "[CLI · bio-agent-env1 + meta-env1] Short-read aligner. "
        "Usage: bowtie2 -x ref_index -1 R1.fq -2 R2.fq -S output.sam -p 4"
    ),

    # =========================================================================
    # CLI TOOLS — meta-env1 (METAGENOMICS SPECIALIZED)
    # Use env_name='meta-env1' in code execution for all tools below.
    # =========================================================================

    # --- QC ---
    "fastp": (
        "[CLI · meta-env1] Ultra-fast adapter trimming and QC for Illumina FASTQ. "
        "Preferred over Trimmomatic. Use run_fastp() Python wrapper. "
        "Direct: fastp -i R1.fq.gz -I R2.fq.gz -o R1_clean.fq.gz -O R2_clean.fq.gz -j stats.json -h report.html -w 4"
    ),
    "fastqc": (
        "[CLI · meta-env1] Per-file quality assessment for FASTQ. "
        "Use run_fastqc() Python wrapper. "
        "Direct: fastqc --outdir ./qc_out --threads 4 R1.fq.gz R2.fq.gz"
    ),
    "multiqc": (
        "[CLI · meta-env1] Aggregate QC reports from fastp/FastQC/Kraken2 into one HTML. "
        "Use run_multiqc() Python wrapper. "
        "Direct: multiqc ./qc_dir --outdir ./multiqc_out --force"
    ),
    "NanoStat": (
        "[CLI · meta-env1] QC statistics for Oxford Nanopore long reads. "
        "Reports N50, mean quality, total bases. Use run_nanostat() Python wrapper. "
        "Direct: NanoStat --fastq reads.fastq --outdir ./nanostat_out --threads 4"
    ),

    # --- Assembly ---
    "metaspades.py": (
        "[CLI · meta-env1] metaSPAdes de-novo metagenome assembler. Best quality for complex communities. "
        "Use run_metaspades() Python wrapper. "
        "Direct: metaspades.py -1 R1.fq -2 R2.fq -o ./assembly -t 8 -m 16"
    ),
    "megahit": (
        "[CLI · meta-env1] Fast, memory-efficient metagenome assembler. Use for large datasets or low RAM. "
        "Use run_megahit() Python wrapper. "
        "Direct: megahit -1 R1.fq -2 R2.fq -o ./megahit_out -t 8 --min-contig-len 500"
    ),
    "flye": (
        "[CLI · meta-env1] Long-read assembler optimized for Nanopore/PacBio metagenomes. "
        "Use run_flye() Python wrapper. "
        "Direct: flye --nano-raw reads.fastq --out-dir ./flye_out --threads 8 --meta"
    ),

    # --- Mapping ---
    "minimap2": (
        "[CLI · meta-env1] Fast aligner for short reads (sr), Nanopore (map-ont), PacBio (map-pb). "
        "Use run_minimap2() Python wrapper. "
        "Direct: minimap2 -ax sr -t 4 ref.fa reads.fq | samtools sort -o output.bam"
    ),

    # --- Taxonomic classification ---
    "kraken2": (
        "[CLI · meta-env1] K-mer based taxonomic classifier. Requires a pre-built database. "
        "Use run_kraken2() Python wrapper. "
        "Direct: kraken2 --db /path/to/db --paired R1.fq R2.fq --report report.txt --output output.txt --confidence 0.1"
    ),
    "bracken": (
        "[CLI · meta-env1] Bayesian re-estimation of species abundances from Kraken2 reports. "
        "Run after Kraken2. Use run_bracken() Python wrapper. "
        "Direct: bracken -d /path/to/db -i kraken2_report.txt -o bracken_out.txt -r 150 -l S"
    ),
    "metaphlan": (
        "[CLI · meta-env1] MetaPhlAn4 — marker-gene based taxonomic profiler. "
        "More specific than Kraken2; good for relative abundance. Use run_metaphlan4() Python wrapper. "
        "Direct: metaphlan reads.fastq --input_type fastq --nproc 4 --output_file profile.tsv"
    ),
    "gtdbtk": (
        "[CLI · meta-env1] GTDB-Tk — classify MAGs using GTDB phylogenomics. "
        "Requires GTDB reference database (set GTDBTK_DATA_PATH). Use run_gtdbtk() Python wrapper. "
        "Direct: gtdbtk classify_wf --genome_dir ./bins --out_dir ./gtdbtk_out --cpus 8"
    ),
    "ktImportTaxonomy": (
        "[CLI · meta-env1] Krona — interactive taxonomy pie chart from Kraken2/Bracken reports. "
        "Use run_krona() Python wrapper. "
        "Direct: ktImportTaxonomy -t 5 -m 3 -o krona.html kraken2_report.txt"
    ),

    # --- Binning ---
    "metabat2": (
        "[CLI · meta-env1] MetaBAT2 — bin assembled contigs into MAGs using coverage + composition. "
        "Requires sorted BAM files. Use run_metabat2() Python wrapper. "
        "Direct: jgi_summarize_bam_contig_depths --outputDepth depth.txt *.bam && "
        "metabat2 -i contigs.fa -a depth.txt -o ./bins/bin -m 2500 -t 8"
    ),
    "DAS_Tool": (
        "[CLI · meta-env1] DAS_Tool — dereplicate and refine bins from multiple binners. "
        "Use run_das_tool() Python wrapper. "
        "Direct: DAS_Tool -i metabat.tsv,maxbin.tsv -l metabat,maxbin -c contigs.fa -o ./dastool --write_bins"
    ),
    "checkm2": (
        "[CLI · meta-env1] CheckM2 — ML-based MAG quality assessment (completeness + contamination). "
        "Faster than CheckM1. Use run_checkm2() Python wrapper. "
        "Direct: checkm2 predict --input ./bins --output-directory ./checkm2_out --threads 8"
    ),

    # --- Annotation ---
    "prokka": (
        "[CLI · meta-env1] Prokka — rapid prokaryotic genome annotation. "
        "Produces GFF, protein FASTA (FAA), CDS nucleotides (FFN). Use run_prokka() Python wrapper. "
        "Direct: prokka --outdir ./prokka_out --prefix sample --metagenome --cpus 4 contigs.fa"
    ),
    "prodigal": (
        "[CLI · meta-env1] Prodigal — ab-initio gene prediction for prokaryotes. "
        "Recommended before DIAMOND/HMMER annotation. Use run_prodigal() Python wrapper. "
        "Direct: prodigal -i contigs.fa -p meta -f gff -o genes.gff -a proteins.faa -d genes.fna"
    ),
    "diamond": (
        "[CLI · meta-env1] DIAMOND — ultra-fast protein alignment (blastp/blastx). "
        "100x faster than BLAST. Use run_diamond() Python wrapper. "
        "Direct: diamond blastp -d uniref90.dmnd -q proteins.faa -o hits.tsv -p 8 -k 5 -e 1e-5"
    ),
    "hmmsearch": (
        "[CLI · meta-env1] HMMER hmmsearch — annotate proteins against HMM profiles (Pfam, TIGRFAM, COG). "
        "Use run_hmmer() Python wrapper. "
        "Direct: hmmsearch --cpu 8 -E 1e-5 --tblout tblout.txt Pfam-A.hmm proteins.faa"
    ),
    "humann": (
        "[CLI · meta-env1] HUMAnN3 — functional pathway profiling from metagenomes. "
        "Produces pathway abundance, pathway coverage, gene families (UniRef90). "
        "Use run_humann3() Python wrapper. "
        "Direct: humann --input reads.fastq --output ./humann_out --threads 4"
    ),

    # --- AMR / Virulence ---
    "amrfinder": (
        "[CLI · meta-env1] AMRFinderPlus — NCBI tool for AMR gene, stress, and virulence factor detection. "
        "Use run_amrfinderplus() Python wrapper. "
        "Direct: amrfinder -p proteins.faa -o amr_report.tsv --threads 4 --plus"
    ),
    "rgi": (
        "[CLI · meta-env1] RGI — Resistance Gene Identifier against CARD database. "
        "Detects AMR genes in contigs or proteins. Use run_rgi_card() Python wrapper. "
        "Direct: rgi main -i contigs.fa -o rgi_output -t contig -a BLAST -n 4 --clean"
    ),

    # --- Viromics ---
    "virsorter2": (
        "[CLI · meta-env1] VirSorter2 — discover viral contigs from metagenomic assemblies. "
        "Use run_virsorter2() Python wrapper. "
        "Direct: virsorter run -w vs2_out -i contigs.fa --min-length 1500 -j 4 all"
    ),
    "checkv": (
        "[CLI · meta-env1] CheckV — assess quality and completeness of viral contigs. "
        "Use run_checkv() Python wrapper. "
        "Direct: checkv end_to_end viral_contigs.fa checkv_out -t 4"
    ),
    "deepvirfinder": (
        "[CLI · meta-env1] DeepVirFinder — predict viral sequences using deep learning. "
        "Use run_deepvirfinder() Python wrapper. "
        "Direct: python dvf.py -i contigs.fa -o dvf_out -c 4"
    ),
}


# ---------------------------------------------------------------------------
# RUNTIME ENVIRONMENTS
# The agent uses these descriptions to decide which env to target
# in the #!PY / #!BASH / #!CLI code blocks it generates.
# ---------------------------------------------------------------------------
runtime_envs_dicts = {
    "bio-agent-env1": (
        "General-purpose Python 3.11 environment. Contains: numpy, pandas, matplotlib, scipy, "
        "statsmodels, scikit-learn, seaborn, networkx, biopython, langchain, langgraph, "
        "transformers, Rscript (ggplot2, vegan, phyloseq), ncbi-genome-download, samtools, bowtie2. "
        "Use this for: Python scripting, R analysis, general bioinformatics, NCBI downloads, "
        "data processing and visualization. DEFAULT environment if no specific CLI tool is needed."
    ),
    "meta-env1": (
        "METAGENOMICS SPECIALIZED environment. Contains ALL metagenomics CLI tools: "
        "fastp, FastQC, MultiQC, NanoStat (QC), "
        "metaSPAdes, MEGAHIT, Flye (assembly), "
        "minimap2, Bowtie2, samtools (mapping), "
        "Kraken2, Bracken, MetaPhlAn4, GTDB-Tk, Krona (taxonomy), "
        "MetaBAT2, DAS_Tool, CheckM2 (binning), "
        "Prokka, Prodigal, DIAMOND, HMMER, HUMAnN3 (annotation), "
        "AMRFinderPlus, RGI/CARD (AMR detection), "
        "VirSorter2, CheckV, DeepVirFinder (viromics). "
        "USE THIS ENVIRONMENT for any metagenomics pipeline step involving these tools. "
        "In code blocks: use #!BASH or #!CLI, or in #!PY call the wrapper functions from "
        "genomeer.tools.function.metagenomics"
    ),
    "btools_env_py310": (
        "Clinical genomics environment (Python 3.10). Contains: CNVkit, BWA, samtools, bedtools, OptiType. "
        "Use for: HLA typing, CNV analysis, genomic clinical tasks."
    ),
}