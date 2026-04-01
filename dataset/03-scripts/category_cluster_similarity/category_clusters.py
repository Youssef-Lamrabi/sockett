CATEGORY_CLUSTERS = {
    # "pipeline_design": [
    #     "pipeline", "workflow", "steps", "orchestration",
    #     "snakemake", "nextflow", "cwl", "workflow management"
    # ],
    "pipeline_design": [
    "pipeline", "workflow", "steps", "orchestration",
    "snakemake", "nextflow", "cwl", "workflow management",
    "directed acyclic graph", "DAG", "workflow execution",
    "pipeline automation", "bioinformatics pipeline", "multi-step analysis"
],
    "qc_preprocessing": [
        "quality control", "qc", "fastqc", "multiqc", "adapter trimming",
        "quality trimming", "filtering", "low quality reads", "cutadapt",
        "trimmomatic", "bbduk", "adapter removal", "deduplication",
        "chimera removal", "read merging", "Phred score", "duplicate removal"
    ],
    "sequencing": [
        "sequencing", "fastq", "illumina", "nanopore", "pacbio", "16s",
        "amplicon", "shotgun", "paired-end", "single-end", "library preparation",
        "16S rRNA amplicon", "ITS amplicon", "DADA2", "QIIME2", "ASV calling",
        "OTU clustering", "sequencing depth", "RNA-seq", "sequencing platform"
    ],
    "host_decontamination": [
        "host removal", "decontamination", "host filtering", "human contamination",
        "bowtie2 host", "kneaddata", "bmtool", "bbmap", "host read removal",
        "human genome decontamination", "Kraken2 host filter", "non-host reads",
        "microbial read enrichment", "host contamination removal"
    ],
    "alignment": [
        "alignment", "reference alignment", "mapping", "bam", "sam",
        "bowtie", "bowtie2", "bwa", "minimap2", "reference genome",
        "HISAT2", "SAM file", "BAM file", "CIGAR string", "mapping rate",
        "aligned reads", "STAR aligner", "coverage depth"
    ],
    "assembly": [
        "assembly", "contigs", "scaffolds", "de novo assembly", "co-assembly",
        "megahit", "metaspades", "spades", "idba-ud", "N50 statistic",
        "SPAdes assembler", "metagenome assembly", "genome reconstruction",
        "k-mer assembly", "read overlap"
    ],
    "assembly_qc": [
        "assembly quality", "n50", "l50", "contig length", "assembly statistics",
        "quast", "metaquast", "checkm", "QUAST evaluation", "N50 score",
        "assembly fragmentation", "misassembly detection", "BUSCO completeness"
    ],
    "binning": [
        "binning", "metagenome bins", "mag", "metagenome-assembled genomes",
        "metabat", "maxbin", "concoct", "das tool", "MAG recovery",
        "MetaBAT2", "bin refinement", "tetranucleotide frequency",
        "coverage-based binning", "differential coverage"
    ],
    "bin_qc": [
        "bin quality", "completeness", "contamination", "checkm", "gtdb-tk",
        "mag quality", "bin completeness", "bin contamination", "CheckM quality",
        "MAG quality assessment", "single copy marker gene",
        "completeness threshold", "contamination threshold", "high quality MAG"
    ],
    "taxonomy": [
        "taxonomy", "taxonomic profiling", "otu", "asv", "species abundance",
        "kraken", "kraken2", "bracken", "metaphlan", "centrifuge", "gtdb",
        "taxonomic classification", "16S rRNA taxonomy", "SILVA taxonomy",
        "NCBI taxonomy", "species identification", "taxonomic assignment"
    ],
    "annotation": [
        "annotation", "functional annotation", "gene prediction", "orfs", "cds",
        "kegg", "eggnog", "cog", "pfam", "interpro", "prokka", "dram",
        "gene annotation", "genome annotation", "GO terms",
        "BLAST annotation", "CDS prediction", "predicted gene"
    ],
    "functional_profiling": [
        "pathway analysis", "functional profiling", "metabolic pathways",
        "enzyme abundance", "humann", "humann3", "minpath",
        "functional pathway", "metabolic function", "gene family abundance"
    ],
    "quantification": [
        "abundance", "counts", "normalization", "relative abundance",
        "coverage", "rpkm", "tpm", "fpkm", "depth",
        "read counts", "abundance estimation", "count matrix"
    ],
    "diversity_analysis": [
        "alpha diversity", "beta diversity", "shannon", "simpson",
        "bray curtis", "ordination", "pcoa", "nmds",
        "species richness", "evenness", "diversity index", "community diversity"
    ],
    "statistical_analysis": [
        "differential abundance", "statistical testing", "significance",
        "anova", "wilcoxon", "lefse", "deseq2", "aldex2",
        "p-value", "multiple testing", "statistical comparison"
    ],
    "visualization": [
        "visualization", "plot", "heatmap", "barplot", "boxplot",
        "ordination plot", "phyloseq", "ggplot", "ggtree", "PCA plot",
        "scatter plot", "volcano plot", "ggplot2", "matplotlib",
        "abundance plot", "interactive visualization"
    ],
    "machine_learning_metagenomics": [
        "machine learning", "deep learning", "classification", "prediction",
        "random forest", "svm", "neural network", "ML taxonomic classification",
        "microbiome prediction model", "random forest microbiome",
        "CNN metagenomics", "transformer genomics", "k-mer embedding",
        "supervised microbiome learning", "predictive microbiome model"
    ],
    "multiomics": [
        "multi-omics", "integration", "metabolomics", "proteomics",
        "transcriptomics", "systems biology", "metagenomics integration",
        "metatranscriptomics", "metaproteomics", "multi-omics data integration",
        "cross-omics correlation", "microbiome multi-omics"
    ],
    "genomics_infra": [
        "container", "docker", "singularity", "conda", "environment",
        "reference genome", "versioning", "reproducibility",
        "HPC cluster", "memory usage", "CPU threads", "parallel processing",
        "SLURM scheduler", "cloud computing genomics", "job scheduler"
    ],
    "association_analysis": [
        "microbiome association study", "GWAS microbiome", "host-microbiome association",
        "QTL mapping", "correlation analysis", "trait-microbiome link",
        "linear mixed model", "MaAsLin2", "multivariate association",
        "phenotype association", "taxa association"
    ],
    "dna_extraction": [
        "DNA extraction", "nucleic acid extraction", "lysis buffer",
        "phenol-chloroform extraction", "extraction kit", "PowerSoil kit",
        "DNA yield", "DNA purity", "A260/A280 ratio", "DNA quality check",
        "bead beating", "extraction efficiency", "extraction protocol"
    ],
    "bioinformatic_algorithm_optimization": [
        "algorithm optimization", "heuristic method", "threshold",
        "k-mer size", "word size", "window size", "seed length",
        "e-value", "minimum coverage", "scoring matrix", "default parameter"
    ],
    "errors_&_debugging": [
        "pipeline error", "debugging bioinformatics", "error message",
        "crash log", "tool error", "memory error", "format error",
        "dependency error", "segmentation fault", "runtime error",
        "unexpected output", "debugging strategy"
    ],
    "experiment_metadata": [
        "experiment metadata", "experimental annotation", "sample annotation",
        "run metadata", "protocol metadata", "SRA metadata",
        "MIxS metadata", "MIMARKS", "experimental variable",
        "metadata completeness", "metadata standard", "study metadata"
    ],
    "reference_database_usage": [
        "database information query", "NCBI query", "sequence database search",
        "data retrieval bioinformatics", "reference database query",
        "functional database query", "taxon information query",
        "BLAST search", "GTDB query", "KEGG lookup", "SRA metadata"
    ],
    
    
    
}

CATEGORIES = list(CATEGORY_CLUSTERS.keys())