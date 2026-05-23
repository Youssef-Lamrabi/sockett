# Data lake dictionary with detailed descriptions
data_lake_dict = {
    # "https://ftp.ncbi.nlm.nih.gov/": "NCBI FTP  -  the central repository of the National Center for Biotechnology Information. Provides access to genomes (GCF/GCA assemblies), RefSeq, GenBank, SRA datasets, annotations (GFF/GTF), protein databases, and supporting metadata files.",
    # "https://www.ebi.ac.uk/ena/": "European Nucleotide Archive (ENA)  -  comprehensive archive of nucleotide sequencing information, including raw sequencing reads, assembled genomes, and functional annotations.",
    # "https://www.uniprot.org/": "UniProt Knowledgebase  -  curated protein sequence and functional information, including UniProtKB/Swiss-Prot (manually reviewed) and UniProtKB/TrEMBL (automatically annotated).",

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
    "samtools": "[CLI Tool][TIMEOUT: 300s] A suite of programs for interacting with high-throughput sequencing data. Use with subprocess.run(['samtools', ...]).",
    "bowtie2": "[CLI Tool][TIMEOUT: 600s] An ultrafast and memory-efficient tool for aligning sequencing reads to long reference sequences. Use with subprocess.run(['bowtie2', ...]).",
    "ncbi-genome-download": (
        "[CLI Tool][TIMEOUT: 1800s] Downloads genomes from NCBI. "
        "PREFERRED: use --assembly-accessions when a known accession is available - it is fast, deterministic, and never mass-lists:\n"
        "  ncbi-genome-download --assembly-accessions GCF_000005845.2 --formats fasta --flat-output --output-folder <dir> bacteria\n"
        "FALLBACK (by organism name) - ALWAYS include --assembly-levels complete or the tool lists ALL assemblies for the whole kingdom (thousands, hangs for hours):\n"
        '  ncbi-genome-download --genera "Escherichia coli" --assembly-levels complete --section refseq --formats fasta --flat-output --output-folder <dir> bacteria\n'
        "  # By taxon ID:\n"
        "  ncbi-genome-download --taxids 562 --assembly-levels complete --section refseq --formats fasta --flat-output --output-folder <dir> bacteria\n"
        "  Valid group args (POSITIONAL, at the END): bacteria fungi plant viral archaea all\n"
        "  WRONG flags (do not exist): --genus  --species  --organism  --name\n"
        "  Downloaded files are .fna.gz  -  decompress with gzip before SeqIO.parse.\n"
        "  Always do a --dry-run first to verify the organism name matches NCBI before the real download."
    ),
    
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


    "gget": """[TIMEOUT: 120s] A toolkit for accessing genomic databases and retrieving sequences, annotations, and other genomic data.""",

    # ── ASSEMBLY QC ───────────────────────────────────────────────────────────
    "quast": (
        "[CLI Tool][TIMEOUT: 300s] QUAST: Quality Assessment Tool for Genome Assemblies. "
        "Use with subprocess.run(['quast.py', contigs_fasta, '-o', output_dir, '--meta', '-t', '4'], ...).\n"
        "  # Basic metagenomic assembly QC:\n"
        "  res = subprocess.run(['quast.py', 'contigs.fna', '-o', 'quast_out', '--meta', '-t', '4'],\n"
        "                       capture_output=True, text=True, timeout=300)\n"
        "  # Key output: quast_out/report.tsv → N50, L50, total_length, num_contigs\n"
        "  import pandas as pd\n"
        "  report = pd.read_csv('quast_out/report.tsv', sep='\\t', index_col=0)\n"
    ),

    # ── BINNING ───────────────────────────────────────────────────────────────
    "semibin2": (
        "[CLI Tool][TIMEOUT: 3600s] SemiBin2: deep-learning metagenomic binning.\n"
        "  # Single sample with BAM coverage:\n"
        "  res = subprocess.run(['SemiBin2', 'single_easy_bin',\n"
        "                        '-i', 'contigs.fna', '-b', 'sorted.bam',\n"
        "                        '-o', 'semibin_out', '--threads', '4'],\n"
        "                       capture_output=True, text=True, timeout=3600)\n"
        "  # Bins are in semibin_out/output_bins/*.fna\n"
        "  # With built-in environment model (no BAM needed):\n"
        "  res = subprocess.run(['SemiBin2', 'single_easy_bin',\n"
        "                        '-i', 'contigs.fna', '--environment', 'human_gut',\n"
        "                        '-o', 'semibin_out', '--threads', '4'], ...)\n"
    ),
    "concoct": (
        "[CLI Tool][TIMEOUT: 3600s] CONCOCT: metagenomic binning using composition + coverage.\n"
        "  # Step 1: cut contigs into chunks\n"
        "  subprocess.run(['cut_up_fasta.py', 'contigs.fna', '-c', '10000', '-o', '0',\n"
        "                  '--merge_last', '-b', 'contigs_10k.bed'],\n"
        "                 stdout=open('contigs_10k.fna','w'), timeout=120)\n"
        "  # Step 2: coverage table from BAM\n"
        "  subprocess.run(['concoct_coverage_table.py', 'contigs_10k.bed', 'sorted.bam'],\n"
        "                 stdout=open('coverage_table.tsv','w'), timeout=300)\n"
        "  # Step 3: cluster\n"
        "  subprocess.run(['concoct', '--composition_file', 'contigs_10k.fna',\n"
        "                  '--coverage_file', 'coverage_table.tsv', '-b', 'concoct_out/',\n"
        "                  '-t', '4'], capture_output=True, text=True, timeout=3600)\n"
        "  # Step 4: merge clustering\n"
        "  subprocess.run(['merge_cutup_clustering.py', 'concoct_out/clustering_gt1000.csv'],\n"
        "                 stdout=open('concoct_out/clustering_merged.csv','w'), timeout=60)\n"
        "  # Step 5: extract bins\n"
        "  subprocess.run(['extract_fasta_bins.py', 'contigs.fna',\n"
        "                  'concoct_out/clustering_merged.csv', '--output_path', 'concoct_bins/'], timeout=120)\n"
    ),
    "maxbin2": (
        "[CLI Tool][TIMEOUT: 3600s] MaxBin2: binning using marker gene EM algorithm.\n"
        "  res = subprocess.run(['run_MaxBin2.pl', '-contig', 'contigs.fna',\n"
        "                        '-out', 'maxbin_out/bin', '-abund', 'coverage.tsv',\n"
        "                        '-thread', '4'],\n"
        "                       capture_output=True, text=True, timeout=3600)\n"
        "  # OR with reads directly:\n"
        "  res = subprocess.run(['run_MaxBin2.pl', '-contig', 'contigs.fna',\n"
        "                        '-out', 'maxbin_out/bin', '-reads', 'reads.fastq',\n"
        "                        '-thread', '4'], capture_output=True, text=True, timeout=3600)\n"
        "  # Bins: maxbin_out/bin.001.fasta, .002.fasta, ...\n"
    ),

    # ── BIN QUALITY ───────────────────────────────────────────────────────────
    "checkm2": (
        "[CLI Tool][TIMEOUT: 1800s] CheckM2: ML-based genome bin quality assessment.\n"
        "  res = subprocess.run(['checkm2', 'predict',\n"
        "                        '--threads', '4',\n"
        "                        '--input', 'bins_dir/*.fna',\n"
        "                        '--output-directory', 'checkm2_out',\n"
        "                        '--extension', 'fna'],\n"
        "                       capture_output=True, text=True, timeout=1800)\n"
        "  # Output: checkm2_out/quality_report.tsv\n"
        "  # Columns: Name, Completeness, Contamination, Completeness_Model_Used\n"
        "  # High-quality bins: Completeness >= 90%, Contamination <= 5%\n"
        "  # Medium-quality: Completeness >= 50%, Contamination <= 10%\n"
    ),

    # ── TAXONOMIC CLASSIFICATION ──────────────────────────────────────────────
    "kraken2": (
        "[CLI Tool][TIMEOUT: 3600s] Kraken2: ultrafast k-mer taxonomic classification.\n"
        "  # Single-end:\n"
        "  res = subprocess.run(['kraken2', '--db', 'kraken2_db/', '--threads', '4',\n"
        "                        '--output', 'output.kraken', '--report', 'report.txt',\n"
        "                        '--gzip-compressed', 'reads.fastq.gz'],\n"
        "                       capture_output=True, text=True, timeout=3600)\n"
        "  # Paired-end:\n"
        "  res = subprocess.run(['kraken2', '--db', 'kraken2_db/', '--paired', '--threads', '4',\n"
        "                        '--output', 'output.kraken', '--report', 'report.txt',\n"
        "                        'reads_1.fastq.gz', 'reads_2.fastq.gz'],\n"
        "                       capture_output=True, text=True, timeout=3600)\n"
        "  # report.txt: mpa-style with % reads, clade counts, taxid, rank, name\n"
    ),
    "sylph": (
        "[CLI Tool][TIMEOUT: 300s] Sylph: ultrafast metagenomic profiling via ANI sketching.\n"
        "  # Sketch reads:\n"
        "  subprocess.run(['sylph', 'sketch', 'reads.fastq', '-o', 'reads.sylsp'],\n"
        "                 capture_output=True, text=True, timeout=120)\n"
        "  # Profile against database:\n"
        "  res = subprocess.run(['sylph', 'profile', 'reads.sylsp', '-d', 'database.syldb',\n"
        "                        '-t', '4', '-o', 'profile.tsv'],\n"
        "                       capture_output=True, text=True, timeout=300)\n"
        "  # Output TSV: genome_file, ANI, relative_abundance, ...\n"
    ),
    "kaiju": (
        "[CLI Tool][TIMEOUT: 1800s] Kaiju: protein-level taxonomic classification.\n"
        "  # Classify:\n"
        "  subprocess.run(['kaiju', '-t', 'db/nodes.dmp', '-f', 'db/kaiju_db.fmi',\n"
        "                  '-i', 'reads.fastq', '-o', 'kaiju_out.txt', '-z', '4'],\n"
        "                 capture_output=True, text=True, timeout=1800)\n"
        "  # Summarize to table:\n"
        "  subprocess.run(['kaiju2table', '-t', 'db/nodes.dmp', '-n', 'db/names.dmp',\n"
        "                  '-r', 'species', '-o', 'summary.tsv', 'kaiju_out.txt'],\n"
        "                 capture_output=True, text=True, timeout=120)\n"
    ),

    # ── FUNCTIONAL ANNOTATION ─────────────────────────────────────────────────
    "hmmer": (
        "[CLI Tool][TIMEOUT: 600s] HMMER: profile HMM protein family annotation.\n"
        "  # hmmscan (protein query vs HMM database):\n"
        "  res = subprocess.run(['hmmscan', '--tblout', 'hits.tsv', '--cpu', '4',\n"
        "                        '-E', '1e-5', 'Pfam-A.hmm', 'proteins.faa'],\n"
        "                       capture_output=True, text=True, timeout=600)\n"
        "  # hmmsearch (HMM query vs protein database):\n"
        "  res = subprocess.run(['hmmsearch', '--tblout', 'hits.tsv', '--cpu', '4',\n"
        "                        'query.hmm', 'proteins.faa'],\n"
        "                       capture_output=True, text=True, timeout=600)\n"
        "  # The HMM db must be pressed first: hmmpress Pfam-A.hmm\n"
    ),
    "eggnog-mapper": (
        "[CLI Tool][TIMEOUT: 1800s] EggNOG-mapper: orthology-based functional annotation.\n"
        "  res = subprocess.run(['emapper.py', '-i', 'proteins.faa',\n"
        "                        '-o', 'eggnog_out', '--output_dir', 'eggnog_dir/',\n"
        "                        '--cpu', '4', '--data_dir', 'eggnog_data/'],\n"
        "                       capture_output=True, text=True, timeout=1800)\n"
        "  # Output: eggnog_dir/eggnog_out.emapper.annotations (TSV)\n"
        "  # Columns: query, seed_ortholog, evalue, score, eggNOG_OGs, COG_cat,\n"
        "  #          Description, Preferred_name, GOs, EC, KEGG_ko, KEGG_Pathway\n"
    ),
    "diamond": (
        "[CLI Tool][TIMEOUT: 1800s] DIAMOND: fast protein alignment (100x faster than BLAST).\n"
        "  # blastp (protein vs protein db):\n"
        "  res = subprocess.run(['diamond', 'blastp', '-q', 'proteins.faa',\n"
        "                        '-d', 'nr.dmnd', '-o', 'hits.tsv',\n"
        "                        '--outfmt', '6', '-p', '4', '--evalue', '1e-5'],\n"
        "                       capture_output=True, text=True, timeout=1800)\n"
        "  # blastx (DNA vs protein db):\n"
        "  res = subprocess.run(['diamond', 'blastx', '-q', 'contigs.fna',\n"
        "                        '-d', 'nr.dmnd', '-o', 'hits.tsv',\n"
        "                        '--outfmt', '6', '-p', '4'],\n"
        "                       capture_output=True, text=True, timeout=1800)\n"
        "  # outfmt 6 columns: qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore\n"
    ),
    "humann3": (
        "[CLI Tool][TIMEOUT: 7200s] HUMAnN3: functional profiling of metagenomic reads.\n"
        "  res = subprocess.run(['humann', '--input', 'reads.fastq.gz',\n"
        "                        '--output', 'humann_out/', '--threads', '4'],\n"
        "                       capture_output=True, text=True, timeout=7200)\n"
        "  # Key outputs:\n"
        "  #   humann_out/reads_genefamilies.tsv  → UniRef gene family abundances\n"
        "  #   humann_out/reads_pathabundance.tsv → MetaCyc pathway abundances\n"
        "  #   humann_out/reads_pathcoverage.tsv  → pathway coverage\n"
        "  # Normalization:\n"
        "  subprocess.run(['humann_renorm_table', '--input', 'reads_genefamilies.tsv',\n"
        "                  '--output', 'reads_genefamilies_relab.tsv', '--units', 'relab'], timeout=120)\n"
    ),

    # ── SPECIALIZED ANNOTATION ────────────────────────────────────────────────
    "antismash": (
        "[CLI Tool][TIMEOUT: 3600s] antiSMASH: biosynthetic gene cluster (BGC) detection.\n"
        "  res = subprocess.run(['antismash', '--taxon', 'bacteria',\n"
        "                        '--output-dir', 'antismash_out/',\n"
        "                        '--genefinding-tool', 'prodigal-m',  # prodigal-m for metagenomes\n"
        "                        '--cpus', '4', 'contigs.fna'],\n"
        "                       capture_output=True, text=True, timeout=3600)\n"
        "  # For metagenomes: --genefinding-tool prodigal-m (NOT prodigal)\n"
        "  # Output: antismash_out/*.region*.gbk + index.html\n"
        "  # BGC types: NRPS, PKS, terpene, RiPP, siderophore, other\n"
    ),
    "genomad": (
        "[CLI Tool][TIMEOUT: 1800s] geNomad: virus/plasmid identification in metagenomes.\n"
        "  res = subprocess.run(['genomad', 'end-to-end', '--cleanup',\n"
        "                        '--splits', '8', 'contigs.fna',\n"
        "                        'genomad_out/', 'genomad_db/'],\n"
        "                       capture_output=True, text=True, timeout=1800)\n"
        "  # Outputs:\n"
        "  #   genomad_out/contigs_virus_summary.tsv → virus scores, taxonomy, AMGs\n"
        "  #   genomad_out/contigs_plasmid_summary.tsv → plasmid scores, conjugation\n"
        "  # Score threshold: >= 0.7 for high-confidence\n"
    ),
    "abricate": (
        "[CLI Tool][TIMEOUT: 300s] ABRicate: AMR/virulence gene screening in contigs.\n"
        "  res = subprocess.run(['abricate', '--db', 'resfinder',\n"
        "                        '--minid', '80', '--mincov', '80',\n"
        "                        'contigs.fna'],\n"
        "                       capture_output=True, text=True, timeout=300)\n"
        "  with open('abricate_results.tsv', 'w') as f:\n"
        "      f.write(res.stdout)\n"
        "  # Available databases: resfinder, card, ncbi, argannot, vfdb, plasmidfinder, ecoh\n"
        "  # Run multiple databases and merge:\n"
        "  subprocess.run(['abricate', '--summary', 'resfinder.tsv', 'card.tsv'],\n"
        "                 stdout=open('summary.tsv', 'w'), timeout=60)\n"
    ),

    # ── SEQUENCE MANIPULATION ─────────────────────────────────────────────────
    "seqkit": (
        "[CLI Tool][TIMEOUT: 120s] SeqKit: ultrafast FASTA/FASTQ toolkit.\n"
        "  # Assembly stats with N50 (all stats with -a):\n"
        "  res = subprocess.run(['seqkit', 'stats', '-a', '-T', 'contigs.fna'],\n"
        "                       capture_output=True, text=True, timeout=60)\n"
        "  # Filter by min length:\n"
        "  subprocess.run(['seqkit', 'seq', '-m', '500', 'contigs.fna', '-o', 'filtered.fna'], timeout=120)\n"
        "  # Grep by ID:\n"
        "  subprocess.run(['seqkit', 'grep', '-n', '-f', 'ids.txt', 'contigs.fna', '-o', 'subset.fna'], timeout=120)\n"
        "  # Subsample 10% of reads:\n"
        "  subprocess.run(['seqkit', 'sample', '-p', '0.1', '-s', '42', 'reads.fastq.gz',\n"
        "                  '-o', 'subset.fastq.gz'], timeout=120)\n"
    ),
    "bbduk": (
        "[CLI Tool][TIMEOUT: 300s] BBDuk (BBTools): adapter trimming and quality filtering.\n"
        "  # Single-end:\n"
        "  res = subprocess.run(['bbduk.sh', 'in=reads.fastq.gz', 'out=clean.fastq.gz',\n"
        "                        'ref=adapters', 'ktrim=r', 'k=23', 'mink=11', 'hdist=1',\n"
        "                        'qtrim=r', 'trimq=20', 'minlen=50', 'threads=4'],\n"
        "                       capture_output=True, text=True, timeout=300)\n"
        "  # Paired-end:\n"
        "  res = subprocess.run(['bbduk.sh', 'in1=r1.fastq.gz', 'in2=r2.fastq.gz',\n"
        "                        'out1=clean_r1.fastq.gz', 'out2=clean_r2.fastq.gz',\n"
        "                        'ref=adapters', 'ktrim=r', 'k=23', 'mink=11', 'hdist=1',\n"
        "                        'tpe', 'tbo', 'qtrim=r', 'trimq=20', 'minlen=50'],\n"
        "                       capture_output=True, text=True, timeout=300)\n"
    ),

    # ── CAZYME ANNOTATION ─────────────────────────────────────────────────────
    "dbcan": (
        "[CLI Tool][TIMEOUT: 600s] dbCAN: CAZyme annotation pipeline.\n"
        "  res = subprocess.run(['run_dbcan.py', 'proteins.faa', 'protein',\n"
        "                        '--out_dir', 'dbcan_out/', '--db_dir', 'db/',\n"
        "                        '--tools', 'hmmer', 'diamond', '-t', '4'],\n"
        "                       capture_output=True, text=True, timeout=600)\n"
        "  # Output: dbcan_out/overview.txt → CAZyme families per protein\n"
        "  # Family format: GH (glycoside hydrolase), GT (glycosyl transferase),\n"
        "  #   PL (polysaccharide lyase), CE (carbohydrate esterase), CBM, AA\n"
    ),

    # ── PHAGE ANNOTATION ──────────────────────────────────────────────────────
    "pharokka": (
        "[CLI Tool][TIMEOUT: 1800s] Pharokka: fast phage genome annotation.\n"
        "  res = subprocess.run(['pharokka.py', '-i', 'phage.fna',\n"
        "                        '-o', 'pharokka_out/', '-d', 'pharokka_db/',\n"
        "                        '-t', '4', '-f'],  # -f to force overwrite\n"
        "                       capture_output=True, text=True, timeout=1800)\n"
        "  # Outputs: pharokka_out/pharokka.gff, .gbk, _top_hits_card.tsv\n"
        "  # PHROGs database: functional annotation of phage proteins\n"
    ),

    # ── COMMUNITY ANALYSIS ────────────────────────────────────────────────────
    "lefse": (
        "[CLI Tool][TIMEOUT: 300s] LEfSe: linear discriminant analysis for biomarker discovery.\n"
        "  # Step 1: format input\n"
        "  subprocess.run(['lefse_format_input.py', 'input.tsv', 'formatted.in',\n"
        "                  '-c', '1', '-s', '-1', '-u', '2', '-o', '1000000'],\n"
        "                 capture_output=True, text=True, timeout=60)\n"
        "  # Step 2: run LEfSe\n"
        "  subprocess.run(['lefse_run.py', 'formatted.in', 'results.res',\n"
        "                  '-l', '2.0'],  # LDA threshold\n"
        "                 capture_output=True, text=True, timeout=120)\n"
        "  # Step 3: plot\n"
        "  subprocess.run(['lefse_plot_res.py', 'results.res', 'lefse_barplot.png',\n"
        "                  '--format', 'png'],\n"
        "                 capture_output=True, text=True, timeout=60)\n"
    ),

    # ── COVERAGE ESTIMATION ───────────────────────────────────────────────────
    "phyloseq": (
        "[R Package][TIMEOUT: 300s] Phyloseq: R microbiome data analysis.\n"
        "  # Alpha + beta diversity from OTU table:\n"
        "  r_code = '''\n"
        "  library(phyloseq); library(ggplot2)\n"
        "  otu <- read.table(\"otu_table.tsv\", sep=\"\\t\", header=TRUE, row.names=1)\n"
        "  OTU <- otu_table(as.matrix(otu), taxa_are_rows=TRUE)\n"
        "  ps  <- phyloseq(OTU)\n"
        "  # Alpha diversity:\n"
        "  alpha <- estimate_richness(ps, measures=c(\"Shannon\",\"Simpson\",\"Chao1\"))\n"
        "  write.table(alpha, \"alpha_div.tsv\", sep=\"\\t\", quote=FALSE)\n"
        "  # Beta diversity + PCoA:\n"
        "  bc  <- distance(ps, method=\"bray\")\n"
        "  ord <- ordinate(ps, method=\"PCoA\", distance=\"bray\")\n"
        "  p   <- plot_ordination(ps, ord)\n"
        "  ggsave(\"pcoa.png\", p, width=8, height=6)\n"
        "  '''\n"
        "  subprocess.run([\"Rscript\", \"-e\", r_code], capture_output=True, text=True, timeout=300)\n"
    ),

    "nonpareil": (
        "[CLI Tool][TIMEOUT: 600s] Nonpareil: metagenome coverage and sequencing effort estimation.\n"
        "  res = subprocess.run(['nonpareil', '-s', 'reads.fastq',\n"
        "                        '-T', 'kmer', '-f', 'fastq',\n"
        "                        '-b', 'nonpareil_out', '-t', '4'],\n"
        "                       capture_output=True, text=True, timeout=600)\n"
        "  # Output: nonpareil_out.npo (R object)\n"
        "  # Plot in R:\n"
        "  #   Nonpareil::Nonpareil.curve('nonpareil_out.npo')\n"
        "  # Key metrics: C (coverage 0-1), LR (sequencing effort for 95% coverage)\n"
    )
    
    
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