# category_clusters_phrases_v2.py
# Phrases générées manuellement depuis les keywords de CATEGORY_CLUSTERS
# Chaque phrase est naturelle et couvre tous les keywords importants

CLUSTER_PHRASES = {
    "pipeline_design": (
        "Designing a bioinformatics pipeline involves orchestrating multi-step analysis workflows "
        "using tools like Snakemake, Nextflow, or CWL, where each step is connected through a "
        "directed acyclic graph (DAG) to ensure reproducible workflow execution and pipeline automation."
    ),
    "qc_preprocessing": (
        "Quality control and preprocessing of sequencing data involves running FastQC and MultiQC "
        "to assess Phred scores, followed by adapter trimming with Cutadapt or Trimmomatic, "
        "quality trimming, filtering low quality reads, deduplication, chimera removal, and read merging using BBDuk."
    ),
    "sequencing": (
        "Sequencing technologies including Illumina, Nanopore, and PacBio generate FASTQ files from "
        "paired-end or single-end library preparations, enabling 16S rRNA amplicon, ITS amplicon, "
        "shotgun, and RNA-seq experiments, with DADA2 and QIIME2 used for ASV calling and OTU clustering at appropriate sequencing depth."
    ),
    "host_decontamination": (
        "Host decontamination removes human contamination and host reads from metagenomic samples "
        "using tools like KneadData, Bowtie2, BBMap, or BMTool, applying Kraken2 host filtering "
        "to enrich microbial reads by eliminating non-host reads through host read removal and human genome decontamination."
    ),
    "alignment": (
        "Read alignment maps sequenced reads to a reference genome using aligners such as Bowtie2, "
        "BWA, Minimap2, HISAT2, or STAR, producing SAM and BAM files with CIGAR strings that describe "
        "aligned reads, mapping rate, and coverage depth across genomic positions."
    ),
    "assembly": (
        "Metagenome assembly reconstructs genomes de novo from sequencing reads using assemblers like "
        "MEGAHIT, MetaSPAdes, SPAdes, or IDBA-UD, producing contigs and scaffolds evaluated by N50 "
        "statistics, through k-mer assembly and read overlap approaches including co-assembly strategies."
    ),
    "assembly_qc": (
        "Assembly quality control evaluates assembly statistics such as N50, L50, and contig length "
        "using QUAST or MetaQUAST, while CheckM assesses completeness and detects misassemblies, "
        "and BUSCO completeness scores measure assembly fragmentation and genome recovery quality."
    ),
    "binning": (
        "Metagenomic binning groups contigs into metagenome-assembled genomes (MAGs) using tools like "
        "MetaBAT2, MaxBin, or CONCOCT, leveraging tetranucleotide frequency and coverage-based binning, "
        "with bin refinement via DAS Tool to maximize MAG recovery from differential coverage signals."
    ),
    "bin_qc": (
        "Bin quality assessment measures completeness and contamination of metagenome-assembled genomes "
        "using CheckM and GTDB-Tk, relying on single copy marker genes to apply completeness and "
        "contamination thresholds that define high quality MAGs suitable for downstream analysis."
    ),
    "taxonomy": (
        "Taxonomic profiling assigns taxonomic classification to OTUs and ASVs from 16S rRNA sequencing "
        "using tools like Kraken2, Bracken, MetaPhlAn, Centrifuge, and GTDB, referencing SILVA, NCBI, "
        "and GTDB databases for species identification and species abundance estimation."
    ),
    "annotation": (
        "Genome annotation predicts genes including ORFs and CDS using Prokka or DRAM, then assigns "
        "functional annotation through KEGG, EggNOG, COG, Pfam, and InterPro databases, generating "
        "GO terms and BLAST annotation to characterize predicted genes across the genome."
    ),
    "functional_profiling": (
        "Functional profiling reconstructs metabolic pathways and quantifies enzyme abundance and "
        "gene family abundance from metagenomic data using HUMAnN and HUMAnN3, with MinPath used "
        "to identify the minimal set of functional pathways explaining observed metabolic function."
    ),
    "quantification": (
        "Quantification estimates the abundance of genomic features by counting mapped reads, "
        "normalizing read counts using RPKM, TPM, or FPKM, and computing relative abundance "
        "and coverage depth from count matrices to enable abundance estimation across samples."
    ),
    "diversity_analysis": (
        "Diversity analysis characterizes microbial community diversity through alpha diversity metrics "
        "such as Shannon, Simpson, species richness, and evenness, and beta diversity distances like "
        "Bray-Curtis, visualized through ordination methods including PCoA and NMDS."
    ),
    "statistical_analysis": (
        "Statistical analysis identifies differential abundance between groups using ANOVA, Wilcoxon "
        "tests, LEfSe, DESeq2, or ALDEx2, applying multiple testing correction to compute reliable "
        "p-values and assess significance in statistical comparisons of microbial features."
    ),
    "visualization": (
        "Visualization of microbiome data involves generating heatmaps, barplots, boxplots, PCA plots, "
        "ordination plots, scatter plots, volcano plots, and abundance plots using ggplot2, ggtree, "
        "phyloseq, and matplotlib to create interactive visualizations of complex biological patterns."
    ),
    "machine_learning_metagenomics": (
        "Machine learning and deep learning approaches including random forest, SVM, and neural networks "
        "are applied to metagenomics for ML taxonomic classification and microbiome prediction, using "
        "k-mer embedding, CNN metagenomics, transformer genomics, and supervised microbiome learning to build predictive microbiome models."
    ),
    "multiomics": (
        "Multi-omics integration combines metagenomics with metabolomics, proteomics, transcriptomics, "
        "metatranscriptomics, and metaproteomics in a systems biology framework to discover cross-omics "
        "correlations and achieve comprehensive microbiome multi-omics data integration."
    ),
    "genomics_infra": (
        "Genomics infrastructure relies on containerization with Docker and Singularity, environment "
        "management via Conda, versioning for reproducibility, and HPC cluster computing with SLURM "
        "scheduler to optimize CPU threads, memory usage, parallel processing, and cloud computing genomics workflows."
    ),
    "association_analysis": (
        "Microbiome association studies investigate host-microbiome associations and phenotype associations "
        "using MaAsLin2 for multivariate association and linear mixed models, alongside GWAS microbiome "
        "approaches, QTL mapping, and correlation analysis to identify trait-microbiome links and taxa associations."
    ),
    "dna_extraction": (
        "DNA extraction protocols isolate nucleic acids from samples using lysis buffer, bead beating, "
        "and phenol-chloroform extraction or commercial kits like the PowerSoil kit, monitoring DNA yield, "
        "DNA purity, and A260/A280 ratio as part of a DNA quality check to ensure extraction efficiency."
    ),
    "bioinformatic_algorithm_optimization": (
        "Bioinformatic algorithm optimization tunes parameters such as k-mer size, word size, window size, "
        "seed length, e-value, minimum coverage, and scoring matrix using heuristic methods to find the "
        "optimal threshold and default parameter settings for a given alignment or search algorithm."
    ),
    "errors_&_debugging": (
        "Debugging bioinformatics pipelines involves identifying pipeline errors, tool errors, memory errors, "
        "format errors, and dependency errors from crash logs and error messages, resolving segmentation faults "
        "and runtime errors that cause unexpected output through a systematic debugging strategy."
    ),
    "experiment_metadata": (
        "Experiment metadata captures experimental annotation, sample annotation, run metadata, and protocol "
        "metadata following standards like MIxS, MIMARKS, and SRA metadata, ensuring metadata completeness "
        "and adherence to metadata standards across all experimental variables in the study metadata."
    ),
    "reference_database_usage": (
        "Reference database usage involves querying NCBI, GTDB, and KEGG through sequence database searches, "
        "BLAST searches, and functional database queries to perform data retrieval in bioinformatics, "
        "including taxon information queries, KEGG lookups, GTDB queries, and SRA metadata retrieval."
    ),
}

CATEGORIES = list(CLUSTER_PHRASES.keys())