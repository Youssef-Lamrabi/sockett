# Data lake dictionary with detailed descriptions
data_lake_dict = {
    # "https://ftp.ncbi.nlm.nih.gov/": "NCBI FTP — the central repository of the National Center for Biotechnology Information. Provides access to genomes (GCF/GCA assemblies), RefSeq, GenBank, SRA datasets, annotations (GFF/GTF), protein databases, and supporting metadata files.",
    # "https://www.ebi.ac.uk/ena/": "European Nucleotide Archive (ENA) — comprehensive archive of nucleotide sequencing information, including raw sequencing reads, assembled genomes, and functional annotations.",
    # "https://www.uniprot.org/": "UniProt Knowledgebase — curated protein sequence and functional information, including UniProtKB/Swiss-Prot (manually reviewed) and UniProtKB/TrEMBL (automatically annotated).",

    # "affinity_capture-ms.parquet": "Protein-protein interactions detected via affinity capture and mass spectrometry.",
    # "affinity_capture-rna.parquet": "Protein-RNA interactions detected by affinity capture.",
}

# Updated library_content as a dictionary with detailed descriptions
library_content_dict = {
    # === PYTHON PACKAGES ===
    # Core Bioinformatics Libraries (Python)
    "biopython": "[Python Package] A set of tools for biological computation including parsers for bioinformatics files, access to online services, and interfaces to common bioinformatics programs.",
    # "gget": "[Python Package] A toolkit for accessing genomic databases and retrieving sequences, annotations, and other genomic data.",
    
    
    # === R PACKAGES ===
    # Core R Packages for Data Analysis
    "ggplot2": "[R Package] A system for declaratively creating graphics, based on The Grammar of Graphics. Use with subprocess.run(['Rscript', '-e', 'library(ggplot2); ...']).",
    
    # === CLI TOOLS ===
    # Sequence Analysis Tools
    "samtools": "[CLI Tool] A suite of programs for interacting with high-throughput sequencing data. Use with subprocess.run(['samtools', ...]).",
    "bowtie2": "[CLI Tool] An ultrafast and memory-efficient tool for aligning sequencing reads to long reference sequences. Use with subprocess.run(['bowtie2', ...]).",
    "ncbi-genome-download": """[CLI Tool] Python package specifically designed for downloading a genome from NCBI.""",
    
        # usage: ncbi-genome-download [-h] [-s {refseq,genbank}] [-F FILE_FORMATS] [-l ASSEMBLY_LEVELS] [-g GENERA] [--genus GENERA] [--fuzzy-genus] [-S STRAINS]
        #                     [-T SPECIES_TAXIDS] [-t TAXIDS] [-A ASSEMBLY_ACCESSIONS] [--fuzzy-accessions] [-R REFSEQ_CATEGORIES]
        #                     [--refseq-category REFSEQ_CATEGORIES] [-o OUTPUT] [--flat-output] [-H] [-P] [-u URI] [-p N] [-r N] [-m METADATA_TABLE] [-n] [-N] [-v]
        #                     [-d] [-V] [-M TYPE_MATERIALS]
        #                     groups
        # ------
        # positional arguments:
        #     groups  The NCBI taxonomic groups to download (default: all). A comma-separated list of taxonomic groups is also possible. For example:
        #             "bacteria,viral"Choose from: ['all', 'archaea', 'bacteria', 'fungi', 'invertebrate', 'metagenomes', 'plant', 'protozoa',
        #             'vertebrate_mammalian', 'vertebrate_other', 'viral']

        # options:
        #     -h, --help            show this help message and exit
        #     -s {refseq,genbank}, --section {refseq,genbank}
        #                             NCBI section to download (default: refseq)
        #     -F FILE_FORMATS, --formats FILE_FORMATS
        #                             Which formats to download (default: genbank).A comma-separated list of formats is also possible. For example: "fasta,assembly-report".
        #                             Choose from: ['genbank', 'fasta', 'rm', 'features', 'gff', 'protein-fasta', 'genpept', 'wgs', 'cds-fasta', 'rna-fna', 'rna-fasta',
        #                             'assembly-report', 'assembly-stats', 'translated-cds', 'all']
        #     -l ASSEMBLY_LEVELS, --assembly-levels ASSEMBLY_LEVELS
        #                             Assembly levels of genomes to download (default: all). A comma-separated list of assembly levels is also possible. For example:
        #                             "complete,chromosome". Choose from: ['all', 'complete', 'chromosome', 'scaffold', 'contig']
        #     -g GENERA, --genera GENERA
        #                             Only download sequences of the provided genera. A comma-seperated list of genera is also possible. For example: "Streptomyces
        #                             coelicolor,Escherichia coli". (default: [])
        #     --genus GENERA        Deprecated alias of --genera
        #     --fuzzy-genus         Use a fuzzy search on the organism name instead of an exact match.
        #     -S STRAINS, --strains STRAINS
        #                             Only download sequences of the given strain(s). A comma-separated list of strain names is possible, as well as a path to a filename
        #                             containing one name per line.
        #     -T SPECIES_TAXIDS, --species-taxids SPECIES_TAXIDS
        #                             Only download sequences of the provided species NCBI taxonomy IDs. A comma-separated list of species taxids is also possible. For
        #                             example: "52342,12325". (default: [])
        #     -t TAXIDS, --taxids TAXIDS
        #                             Only download sequences of the provided NCBI taxonomy IDs. A comma-separated list of taxids is also possible. For example: "9606,9685".
        #                             (default: [])
        #     -A ASSEMBLY_ACCESSIONS, --assembly-accessions ASSEMBLY_ACCESSIONS
        #                             Only download sequences matching the provided NCBI assembly accession(s). A comma-separated list of accessions is possible, as well as a
        #                             path to a filename containing one accession per line.
        #     --fuzzy-accessions    Use a fuzzy search on the entry accession instead of an exact match.
        #     -R REFSEQ_CATEGORIES, --refseq-categories REFSEQ_CATEGORIES
        #                             Only download sequences of the provided refseq categories [refrerence, representative, na]. A comma-separated list of categories is also
        #                             possible. (default: download all categories)
        #     --refseq-category REFSEQ_CATEGORIES
        #                             Deprecated alias for --refseq-categories
        #     -o OUTPUT, --output-folder OUTPUT
        #                             Create output hierarchy in specified folder (default: /)
        #     --flat-output         Dump all files right into the output folder without creating any subfolders.
        #     -H, --human-readable  Create links in human-readable hierarchy (might fail on Windows)
        #     -P, --progress-bar    Create a progress bar for indicating the download progress
        #     -u URI, --uri URI     NCBI base URI to use (default: https://ftp.ncbi.nih.gov/genomes)
        #     -p N, --parallel N    Run N downloads in parallel (default: 1)
        #     -r N, --retries N     Retry download N times when connection to NCBI fails (default: 0)
        #     -m METADATA_TABLE, --metadata-table METADATA_TABLE
        #                             Save tab-delimited file with genome metadata
        #     -n, --dry-run         Only check which files to download, don't download genome files.
        #     -N, --no-cache        Don't cache the assembly summary file in /home/biolab-office-1/.cache/ncbi-genome-download.
        #     -v, --verbose         increase output verbosity
        #     -d, --debug           print debugging information
        #     -V, --version         print version information
        #     -M TYPE_MATERIALS, --type-materials TYPE_MATERIALS
        #                             Specifies the relation to type material for the assembly (default: any). "any" will include assemblies with no relation to type material
        #                             value defined, "all" will download only assemblies with a defined value. A comma-separated list of relatons. For example:
        #                             "reference,synonym". Choose from: ['any', 'all', 'type', 'reference', 'synonym', 'proxytype', 'neotype'] .


    "gget": """A toolkit for accessing genomic databases and retrieving sequences, annotations, and other genomic data."""
    
    
        # ----------
        # usage: gget [-h] [-v]
        #     {ref,search,elm,diamond,info,seq,muscle,blast,blat,enrichr,archs4,setup,alphafold,pdb,gpt,cellxgene,cosmic,mutate,opentargets,cbio,bgee} ...
        #     gget v0.29.3
        #     positional arguments:
        #     {ref,search,elm,diamond,info,seq,muscle,blast,blat,enrichr,archs4,setup,alphafold,pdb,gpt,cellxgene,cosmic,mutate,opentargets,cbio,bgee}
        #         ref                 Fetch FTPs for reference genomes and annotations by species.
        #         search              Fetch gene and transcript IDs from Ensembl using free-form search terms.
        #         elm                 Locally predicts Eukaryotic Linear Motifs from an amino acid sequence or UniProt Acc using data from the ELM database
        #                             (http://elm.eu.org/media/Elm_academic_license.pdf).
        #         diamond             Align multiple protein or translated DNA sequences using DIAMOND.
        #         info                Fetch gene and transcript metadata using Ensembl IDs.
        #         seq                 Fetch nucleotide or amino acid sequence (FASTA) of a gene (and all isoforms) or transcript by Ensembl, WormBase or FlyBase ID.
        #         muscle              Align multiple nucleotide or amino acid sequences against each other (using the Muscle v5 algorithm).
        #         blast               BLAST a nucleotide or amino acid sequence against any BLAST database.
        #         blat                BLAT a nucleotide or amino acid sequence against any BLAT UCSC assembly.
        #         enrichr             Perform an enrichment analysis on a list of genes using Enrichr.
        #         archs4              Find the most correlated genes or the tissue expression atlas of a gene using data from the human and mouse RNA-seq database ARCHS4
        #                             (https://maayanlab.cloud/archs4/).
        #         setup               Install third-party dependencies for a specified gget module.
        #         alphafold           Predicts the structure of a protein using a simplified version of AlphaFold v2.3.0 (https://doi.org/10.1038/s41586-021-03819-2).
        #         pdb                 Query RCSB PDB for the protein structutre/metadata of a given PDB ID.
        #         gpt                 Generates natural language text based on a given prompt using the OpenAI API's 'openai.ChatCompletion.create' endpoint.
        #         cellxgene           Query data from CZ CELLxGENE Discover (https://cellxgene.cziscience.com/).
        #         cosmic              Query information about genes, mutations, etc. associated with cancers from the COSMIC database.
        #         mutate              Mutate nucleotide sequences based on provided mutations.
        #         opentargets         Query the Open Targets Platform with a gene for associated drugs, diseases, tractability stats, pharmacogenetic responses, expression
        #                             data, DepMap effects, and protein-protein interaction data.
        #         cbio                Plot cancer genomics heatmaps using data from cBioPortal using Ensembl IDs or gene names
        #         bgee                Query the Bgee database for orthology and gene expression data using Ensembl IDs.

        #     options:
        #     -h, --help            Print manual.
        #     -v, --version         Print version.
        #     -------
        #     you can plan to run cli with option -h to gather more information about tools before traying to use it.
    
}

#  Runetime environement: name and description
runtime_envs_dicts = {
    # "bio-agent-env1": "A general-purpose Conda environment for building and running agentic bioinformatics assistants. If for any tools there is not more best candidate choose this by default.",
    # "hla-tools": "HLA typing stack (optitype, samtools, bwa).",
}