# Data lake dictionary with detailed descriptions
data_lake_dict = {
    # "affinity_capture-ms.parquet": "Protein-protein interactions detected via affinity capture and mass spectrometry.",
    # "affinity_capture-rna.parquet": "Protein-RNA interactions detected by affinity capture.",
}

# Updated library_content as a dictionary with detailed descriptions
library_content_dict = {
    # === PYTHON PACKAGES ===
    # Core Bioinformatics Libraries (Python)
    "biopython": "[Python Package] A set of tools for biological computation including parsers for bioinformatics files, access to online services, and interfaces to common bioinformatics programs.",
    "gget": "[Python Package] A toolkit for accessing genomic databases and retrieving sequences, annotations, and other genomic data.",
    
    
    # === R PACKAGES ===
    # Core R Packages for Data Analysis
    "ggplot2": "[R Package] A system for declaratively creating graphics, based on The Grammar of Graphics. Use with subprocess.run(['Rscript', '-e', 'library(ggplot2); ...']).",
    
    # === CLI TOOLS ===
    # Sequence Analysis Tools
    "samtools": "[CLI Tool] A suite of programs for interacting with high-throughput sequencing data. Use with subprocess.run(['samtools', ...]).",
    "bowtie2": "[CLI Tool] An ultrafast and memory-efficient tool for aligning sequencing reads to long reference sequences. Use with subprocess.run(['bowtie2', ...]).",
    "ncbi-genome-download": "[CLI Tool] Python package specifically designed for downloading a genome from NCBI."
}

#  Runetime environement: name and description
runtime_envs_dicts = {
    # "bio-agent-env1": "A general-purpose Conda environment for building and running agentic bioinformatics assistants. If for any tools there is not more best candidate choose this by default.",
    # "hla-tools": "HLA typing stack (optitype, samtools, bwa).",
}