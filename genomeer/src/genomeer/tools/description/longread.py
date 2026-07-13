"""
Genomeer — Long-read (Nanopore / PacBio) assembly & QC tool descriptions.
==========================================================================
Isolated in its own module (mirrors viromics.py's own dedicated module) so
these tools can be wired into read_module2api() as a single, self-contained
addition. Fills a previously-empty capability: BIOLOGICAL_GATES already
carried a "run_flye" quality gate (assembly N50 thresholds) with no matching
tool — this module completes that gap end-to-end, plus the QC/filtering
tools needed to make a long-read pipeline actually usable (garbage reads in
= garbage assembly out).

Covers: run_flye, run_unicycler, run_filtlong, run_nanoplot.
run_medaka / run_racon (polishing) already live in metagenomics.py.
"""

description = [
    {
        "name": "run_flye",
        "description": (
            "[CLI Tool][TIMEOUT: 14400s] Flye: de-novo assembler for long reads (Oxford Nanopore / "
            "PacBio), including METAGENOME mode for uneven-coverage microbial communities. AVAILABLE "
            "in meta-env1. No database required. "
            "Command (choose the read_type matching the input, ONE of): "
            "flye --nano-raw reads.fastq.gz --out-dir output_dir --threads N [--meta]  (older/raw ONT); "
            "flye --nano-hq reads.fastq.gz --out-dir output_dir --threads N [--meta]  (modern "
            "high-accuracy ONT, Guppy5+/Dorado — DEFAULT choice when basecaller is unspecified); "
            "flye --nano-corr ... [--meta]  (error-corrected ONT); "
            "flye --pacbio-raw ... [--meta]  (standard/CLR PacBio); "
            "flye --pacbio-hifi ... [--meta]  (PacBio HiFi/CCS — near-perfect reads, no polishing "
            "needed afterward); flye --pacbio-corr ... [--meta]. "
            "ALWAYS add --meta for metagenomic/environmental samples (multi-organism, uneven coverage) "
            "— WITHOUT it Flye assumes a SINGLE genome and can silently misassemble a mixed community. "
            "Omit --meta only for a single-isolate genome (or prefer run_unicycler for that case). "
            "Output: <output_dir>/assembly.fasta (contigs) and <output_dir>/assembly_info.txt "
            "(tab-separated: seq_name, length, coverage, circular Y/N, repeat, mult, alt_group, "
            "graph_path) — read assembly_info.txt for per-contig coverage/circularity/N50; do NOT "
            "re-derive coverage by mapping unless per-SAMPLE (not per-assembly) depth is needed. "
            "POLISHING: raw/nano-hq ONT assemblies benefit from 1-2 rounds of run_racon (a read-to-"
            "assembly overlap/alignment step feeds racon) followed by run_medaka. PacBio HiFi "
            "assemblies are already >99.9% accurate and do NOT need polishing. "
            "Do NOT confuse with run_unicycler (single-isolate bacterial hybrid assembler, not "
            "metagenome-capable) or the short-read-only assemblers (which cannot take long reads)."
        ),
        "required_parameters": [
            {"name": "reads_fastq", "type": "str", "description": "Long-read FASTQ(.gz) file (Nanopore or PacBio)."},
            {"name": "output_dir", "type": "str", "description": "Output directory (created if absent)."},
        ],
        "optional_parameters": [
            {"name": "read_type", "type": "str", "default": "nano-hq",
             "description": "One of: nano-raw, nano-hq, nano-corr, pacbio-raw, pacbio-hifi, pacbio-corr."},
            {"name": "meta", "type": "bool", "default": True,
             "description": "Metagenome mode (uneven coverage, multi-organism). Set False only for a single isolate."},
            {"name": "threads", "type": "int", "default": 8},
        ],
        "returns": "dict(assembly_fasta, assembly_info, n_contigs, n50_bp, output_dir)"
    },
    {
        "name": "run_unicycler",
        "description": (
            "[CLI Tool][TIMEOUT: 7200s] Unicycler: bacterial genome assembler optimized for a SINGLE "
            "ISOLATE (NOT a metagenome — use run_flye with meta=True for mixed communities). Supports "
            "three modes. AVAILABLE in meta-env1. No database required. "
            "Hybrid (best — short reads for base accuracy + long reads for contiguity/circularization): "
            "unicycler -1 R1.fastq.gz -2 R2.fastq.gz -l long_reads.fastq.gz -o output_dir -t N. "
            "Long-read-only: unicycler -l long_reads.fastq.gz -o output_dir -t N. "
            "Short-read-only (uses the same underlying short-read assembly engine as the dedicated "
            "short-read-only assembler tool — no benefit over that tool for pure short-read data): "
            "unicycler -1 R1.fastq.gz -2 R2.fastq.gz -o output_dir -t N. "
            "Output: <output_dir>/assembly.fasta — often includes CIRCULARIZED chromosome/plasmids "
            "(check FASTA headers for 'circular=true') — and <output_dir>/assembly.gfa (assembly graph). "
            "USE CASE: complete/closed single-genome assembly ('assemble this bacterial isolate to "
            "completion', 'get a closed genome with plasmids'). For community/metagenome samples use "
            "run_flye (meta=True) followed by the standard binning + bin-consensus tools instead."
        ),
        "required_parameters": [
            {"name": "output_dir", "type": "str", "description": "Output directory (created if absent)."},
        ],
        "optional_parameters": [
            {"name": "read1", "type": "str", "default": None, "description": "Short-read R1 FASTQ (paired-end, for hybrid or short-only mode)."},
            {"name": "read2", "type": "str", "default": None, "description": "Short-read R2 FASTQ (paired-end, for hybrid or short-only mode)."},
            {"name": "long_reads", "type": "str", "default": None, "description": "Long-read FASTQ (for hybrid or long-only mode)."},
            {"name": "threads", "type": "int", "default": 8},
        ],
        "returns": "dict(assembly_fasta, assembly_gfa, n_contigs, n_circular, output_dir)"
    },
    {
        "name": "run_filtlong",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] Filtlong: quality/length filtering for long reads "
            "(Nanopore/PacBio) BEFORE assembly — removes short and low-quality reads that hurt "
            "assembly contiguity. AVAILABLE in meta-env1. No database required. "
            "CRITICAL: Filtlong writes the filtered FASTQ to STDOUT — there is no -o flag; redirect "
            "stdout to a file (never use shell=True; open the destination file and pass it as the "
            "subprocess stdout=). "
            "Command: filtlong --min_length 1000 --keep_percent 90 input.fastq  (stdout -> filtered.fastq). "
            "Common flags: --min_length N (discard reads shorter than N bp; 1000 is a reasonable "
            "default for assembly), --keep_percent P (keep only the best P% of reads by a combined "
            "length/quality score — 90 is a common default), --target_bases N (cap total output bases "
            "— useful to subsample very deep runs to a target coverage). "
            "WORKFLOW: run this BEFORE run_flye/run_unicycler on raw ONT/PacBio-CLR reads; it is safe "
            "to SKIP for already-clean PacBio HiFi data (already high-accuracy, filtering adds little)."
        ),
        "required_parameters": [
            {"name": "input_fastq", "type": "str", "description": "Raw long-read FASTQ(.gz) to filter."},
            {"name": "output_fastq", "type": "str", "description": "Path to write the filtered FASTQ."},
        ],
        "optional_parameters": [
            {"name": "min_length", "type": "int", "default": 1000, "description": "Discard reads shorter than this (bp)."},
            {"name": "keep_percent", "type": "float", "default": 90.0, "description": "Keep only the best P% of reads by length/quality score."},
            {"name": "target_bases", "type": "int", "default": None, "description": "Cap total output bases (subsample deep runs to a target coverage)."},
        ],
        "returns": "dict(output_fastq, read_count)"
    },
    {
        "name": "run_nanoplot",
        "description": (
            "[CLI Tool][TIMEOUT: 600s] NanoPlot: QC report for long reads (Nanopore/PacBio) — the "
            "long-read equivalent of the standard short-read QC report tool. AVAILABLE in meta-env1. "
            "No database required. "
            "Command: NanoPlot --fastq reads.fastq.gz --outdir output_dir -t N  (or --summary "
            "sequencing_summary.txt instead of --fastq if a Guppy/Dorado basecaller summary file is "
            "available — faster and gives richer per-read stats). "
            "Output: <output_dir>/NanoStats.txt (plain-text 'Metric: value' pairs — mean/median read "
            "length, read length N50, mean/median read quality, number of reads, total bases) and "
            "<output_dir>/NanoPlot-report.html (interactive plots, for humans — do NOT parse the HTML; "
            "parse NanoStats.txt for numeric QC metrics). "
            "WORKFLOW: run on RAW long reads first to decide filtering thresholds for run_filtlong, "
            "and optionally again on the filtered output to confirm the QC improvement."
        ),
        "required_parameters": [
            {"name": "reads_fastq", "type": "str", "description": "Long-read FASTQ(.gz) file to QC."},
            {"name": "output_dir", "type": "str", "description": "Output directory (created if absent)."},
        ],
        "optional_parameters": [
            {"name": "threads", "type": "int", "default": 4},
        ],
        "returns": "dict(nanostats_txt, report_html, stats[dict of parsed metrics], output_dir)"
    },
]
