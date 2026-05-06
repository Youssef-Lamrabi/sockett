"""
bioagent_tools.py
------------------
A lightweight, dependency-minimal toolbox for agentic workflows in bioinformatics & metagenomics.

This module contains TWO categories of functions:

[SAFE — Use freely]
  Sequence I/O and parsing, FASTA/FASTQ readers/writers, k-mer profiling, GC content,
  ORF translation, deduplication, interval/BED operations, overlap analysis, reporting.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple, Iterable, Dict, Optional
from dataclasses import dataclass
from collections import Counter, defaultdict
import random
import gzip
import os
import math
from datetime import datetime


# -----------------------------
# Helpers
# -----------------------------

def _open_maybe_gz(path: str, mode: str = "rt"):
    return gzip.open(path, mode) if str(path).endswith(".gz") else open(path, mode)

def _is_fasta_header(line: str) -> bool:
    return line.startswith(">")

def _is_fastq_header(line: str) -> bool:
    return line.startswith("@")

def _detect_seq_format(path: str) -> str:
    with _open_maybe_gz(path, "rt") as fh:
        for _ in range(10):
            line = fh.readline()
            if not line:
                break
            line = line.strip()
            if line.startswith(">"):
                return "fasta"
            if line.startswith("@"):
                return "fastq"
    return "fasta"  # default

def _iter_fasta(path: str):
    header = None
    seq_chunks = []
    with _open_maybe_gz(path, "rt") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(seq_chunks)
                header = line[1:].strip()
                seq_chunks = []
            else:
                if line and not line.startswith(";"):
                    seq_chunks.append(line.strip())
        if header is not None:
            yield header, "".join(seq_chunks)

def _iter_fastq(path: str):
    with _open_maybe_gz(path, "rt") as fh:
        while True:
            h = fh.readline()
            if not h:
                break
            s = fh.readline()
            plus = fh.readline()
            q = fh.readline()
            if not q:
                break
            yield h.strip()[1:], s.strip(), q.strip()

def _write_fasta(records: Iterable[Tuple[str, str]], path: str):
    with _open_maybe_gz(path, "wt") as out:
        for h, s in records:
            out.write(f">{h}\n")
            for i in range(0, len(s), 80):
                out.write(s[i:i+80] + "\n")

def _write_fastq(records: Iterable[Tuple[str, str, str]], path: str):
    with _open_maybe_gz(path, "wt") as out:
        for h, s, q in records:
            out.write(f"@{h}\n{s}\n+\n{q}\n")

def _gc_content(seq: str) -> float:
    seq = seq.upper()
    if not seq:
        return 0.0
    gc = sum(1 for c in seq if c in "GC")
    valid = sum(1 for c in seq if c in "ACGT")
    return (gc / valid * 100.0) if valid else 0.0

def _revcomp(seq: str) -> str:
    tab = str.maketrans("ACGTacgtNn", "TGCAtgcaNn")
    return seq.translate(tab)[::-1]

def _sliding_windows(seq: str, window: int, step: int):
    for i in range(0, max(1, len(seq) - window + 1), step):
        yield i, seq[i:i+window]

def _write_tsv(rows: List[Dict], path: str):
    if not rows:
        Path(path).write_text("")
        return
    keys = list(rows[0].keys())
    with open(path, "w") as out:
        out.write("\t".join(keys) + "\n")
        for r in rows:
            out.write("\t".join(str(r.get(k, "")) for k in keys) + "\n")


# -----------------------------
# Core I/O & Utilities
# -----------------------------

def load_sequences(paths: List[str], format: Optional[str] = None, max_records: Optional[int] = None):
    """
    Load FASTA/FASTQ files into line-delimited JSON-like TSVs (id, seq[, qual]).
    Returns temp paths for downstream tasks to avoid memory pressure.
    """
    artifacts = []
    total = 0
    detected = None
    for p in paths:
        fmt = format or _detect_seq_format(p)
        detected = fmt
        out = str(Path(p).with_suffix(Path(p).suffix + ".ld.tsv"))
        rows = []
        count = 0
        if fmt == "fasta":
            for h, s in _iter_fasta(p):
                rows.append({"id": h, "seq": s})
                count += 1
                total += 1
                if max_records and count >= max_records:
                    break
        else:
            for h, s, q in _iter_fastq(p):
                rows.append({"id": h, "seq": s, "qual": q})
                count += 1
                total += 1
                if max_records and count >= max_records:
                    break
        _write_tsv(rows, out)
        artifacts.append(out)
    return {"records_count": total, "format": detected or "auto", "temp_paths": artifacts}


def write_sequences(records: Iterable, path: str, format: str = "fasta", compress: bool = False):
    """
    Write sequences to FASTA/FASTQ.
    'records' should yield (id, seq) for FASTA or (id, seq, qual) for FASTQ.
    """
    target = path + (".gz" if compress else "")
    if format == "fasta":
        _write_fasta(((r[0], r[1]) for r in records), target)
        return {"written_count": sum(1 for _ in _iter_fasta(target)), "path": target}
    else:
        _write_fastq(((r[0], r[1], r[2]) for r in records), target)
        # count fastq reads
        count = 0
        for _ in _iter_fastq(target):
            count += 1
        return {"written_count": count, "path": target}


def subsample_reads(input_path: str, output_path: str, fraction: float, seed: int = 42, paired: bool = False):
    random.seed(seed)
    fmt = _detect_seq_format(input_path)
    kept = 0
    if fmt == "fasta":
        chosen = []
        for h, s in _iter_fasta(input_path):
            if random.random() < fraction:
                chosen.append((h, s))
        _write_fasta(chosen, output_path)
        kept = len(chosen)
    else:
        chosen = []
        for h, s, q in _iter_fastq(input_path):
            if random.random() < fraction:
                chosen.append((h, s, q))
        _write_fastq(chosen, output_path)
        kept = len(chosen)
    return {"written_count": kept, "output_path": output_path}


def convert_format(input_path: str, output_path: str, target_format: str, params: Optional[Dict] = None):
    src_fmt = _detect_seq_format(input_path)
    if src_fmt == "fasta" and target_format == "fastq":
        # create dummy quality
        recs = []
        for h, s in _iter_fasta(input_path):
            recs.append((h, s, "I" * len(s)))
        _write_fastq(recs, output_path)
    elif src_fmt == "fastq" and target_format == "fasta":
        recs = []
        for h, s, q in _iter_fastq(input_path):
            recs.append((h, s))
        _write_fasta(recs, output_path)
    else:
        raise ValueError(f"Unsupported conversion: {src_fmt} -> {target_format}")
    return {"output_path": output_path, "summary": f"Converted {src_fmt} to {target_format}."}


# -----------------------------
# QC & Stats
# -----------------------------

def read_quality_report(paths: List[str], sample_names: Optional[List[str]] = None):
    rows = []
    for i, p in enumerate(paths):
        name = sample_names[i] if (sample_names and i < len(sample_names)) else Path(p).stem
        fmt = _detect_seq_format(p)
        n_reads = 0
        total_len = 0
        gc_sum = 0.0
        n_content = 0
        if fmt == "fasta":
            for _, s in _iter_fasta(p):
                n_reads += 1
                total_len += len(s)
                gc_sum += _gc_content(s)
                n_content += s.upper().count("N")
        else:
            for _, s, _ in _iter_fastq(p):
                n_reads += 1
                total_len += len(s)
                gc_sum += _gc_content(s)
                n_content += s.upper().count("N")
        avg_len = (total_len / n_reads) if n_reads else 0
        avg_gc = (gc_sum / n_reads) if n_reads else 0
        rows.append({
            "sample": name, "format": fmt, "reads": n_reads,
            "avg_len": round(avg_len, 2), "avg_gc": round(avg_gc, 2),
            "total_bases": total_len, "N_bases": n_content
        })
    out = str(Path(paths[0]).with_suffix(".qc.tsv"))
    _write_tsv(rows, out)
    return {"per_sample_stats": out, "plots": []}


def trim_filter_reads(input_path: str, output_path: str, min_len: int = 50, min_q: int = 20,
                      adapter_5p: Optional[str] = None, adapter_3p: Optional[str] = None, paired: bool = False):
    fmt = _detect_seq_format(input_path)
    kept = 0
    if fmt == "fasta":
        out_recs = []
        for h, s in _iter_fasta(input_path):
            ss = s
            if adapter_5p and ss.startswith(adapter_5p):
                ss = ss[len(adapter_5p):]
            if adapter_3p and ss.endswith(adapter_3p):
                ss = ss[:-len(adapter_3p)]
            if len(ss) >= min_len:
                out_recs.append((h, ss))
                kept += 1
        _write_fasta(out_recs, output_path)
    else:
        out_recs = []
        for h, s, q in _iter_fastq(input_path):
            ss, qq = s, q
            if adapter_5p and ss.startswith(adapter_5p):
                trim = len(adapter_5p)
                ss, qq = ss[trim:], qq[trim:]
            if adapter_3p and ss.endswith(adapter_3p):
                trim = len(adapter_3p)
                ss, qq = ss[:-trim], qq[:-trim]
            if len(ss) >= min_len:
                out_recs.append((h, ss, qq))
                kept += 1
        _write_fastq(out_recs, output_path)
    return {"kept": kept, "discarded": None, "output_path": output_path}


def kmer_profile(paths: List[str], k: int, canonical: bool = True, max_records: Optional[int] = None):
    counts = Counter()
    for p in paths:
        fmt = _detect_seq_format(p)
        seen = 0
        if fmt == "fasta":
            it = ((s,) for _, s in _iter_fasta(p))
        else:
            it = ((s,) for _, s, _ in _iter_fastq(p))
        for (s,) in it:
            s = s.upper()
            for i in range(0, len(s) - k + 1):
                kmer = s[i:i+k]
                if canonical:
                    rc = _revcomp(kmer)
                    kmer = min(kmer, rc)
                counts[kmer] += 1
            seen += 1
            if max_records and seen >= max_records:
                break
    out = str(Path(paths[0]).with_suffix(f".k{k}.tsv"))
    rows = [{"kmer": k, "count": c} for k, c in counts.items()]
    _write_tsv(rows, out)
    return {"counts_path": out, "summary": f"Counted {len(counts)} distinct k-mers (k={k})."}


# -----------------------------
# Sequence Operations
# -----------------------------

def translate_orfs(fasta_path: str, min_aa: int = 30, genetic_code: int = 11, strand: str = "both"):
    # Simple standard table (no stops inside ORFs)
    table = {
        'TTT':'F','TTC':'F','TTA':'L','TTG':'L','CTT':'L','CTC':'L','CTA':'L','CTG':'L',
        'ATT':'I','ATC':'I','ATA':'I','ATG':'M','GTT':'V','GTC':'V','GTA':'V','GTG':'V',
        'TCT':'S','TCC':'S','TCA':'S','TCG':'S','CCT':'P','CCC':'P','CCA':'P','CCG':'P',
        'ACT':'T','ACC':'T','ACA':'T','ACG':'T','GCT':'A','GCC':'A','GCA':'A','GCG':'A',
        'TAT':'Y','TAC':'Y','TAA':'*','TAG':'*','CAT':'H','CAC':'H','CAA':'Q','CAG':'Q',
        'AAT':'N','AAC':'N','AAA':'K','AAG':'K','GAT':'D','GAC':'D','GAA':'E','GAG':'E',
        'TGT':'C','TGC':'C','TGA':'*','TGG':'W','CGT':'R','CGC':'R','CGA':'R','CGG':'R',
        'AGT':'S','AGC':'S','AGA':'R','AGG':'R','GGT':'G','GGC':'G','GGA':'G','GGG':'G'
    }
    def find_orfs(seq, plus=True):
        orfs = []
        s = seq.upper()
        for frame in range(3):
            i = frame
            while i+3 <= len(s):
                codon = s[i:i+3]
                if codon == "ATG":
                    j = i
                    protein = []
                    while j+3 <= len(s):
                        cod = s[j:j+3]
                        aa = table.get(cod, "X")
                        if aa == "*":
                            if len(protein) >= min_aa:
                                start = i if plus else len(seq) - (j+3)
                                end = j+3 if plus else len(seq) - i
                                orfs.append((start, end, "".join(protein)))
                            break
                        protein.append(aa)
                        j += 3
                    i = j
                i += 1
        return orfs

    proteins = []
    bed_rows = []
    for h, s in _iter_fasta(fasta_path):
        seqs = [(s, True)]
        if strand in ("both", "minus"):
            seqs.append((_revcomp(s), False))
        idx = 0
        for seq, plus in seqs:
            orfs = find_orfs(seq, plus=plus)
            for start, end, prot in orfs:
                idx += 1
                pid = f"{h}|orf{idx}|{'+' if plus else '-'}:{start}-{end}"
                proteins.append((pid, prot))
                bed_rows.append({"chrom": h, "start": start, "end": end, "name": f"orf{idx}", "strand": "+" if plus else "-"})
    faa = str(Path(fasta_path).with_suffix(".orfs.faa"))
    bed = str(Path(fasta_path).with_suffix(".orfs.bed.tsv"))
    _write_fasta(proteins, faa)
    _write_tsv(bed_rows, bed)
    return {"orfs_bed": bed, "proteins_faa": faa, "summary": f"Predicted {len(proteins)} ORFs (>= {min_aa} aa)."}


def gc_content_windows(fasta_path: str, window: int, step: int, mask_ns: bool = True):
    rows = []
    for h, s in _iter_fasta(fasta_path):
        for i, wseq in _sliding_windows(s, window, step):
            seq = "".join(c for c in wseq if (c.upper() in "ACGTN")) if mask_ns else wseq
            gc = _gc_content(seq)
            rows.append({"contig": h, "start": i, "end": i+len(wseq), "gc_pct": round(gc, 2)})
    out = str(Path(fasta_path).with_suffix(".gc.tsv"))
    _write_tsv(rows, out)
    return {"gc_tsv": out, "plots": []}


def deduplicate_sequences(input_path: str, output_path: str, min_count: int = 1):
    fmt = _detect_seq_format(input_path)
    counter = Counter()
    if fmt == "fasta":
        for _, s in _iter_fasta(input_path):
            counter[s] += 1
        uniques = [(f"seq{i}|count={c}", s) for i, (s, c) in enumerate(counter.items(), 1) if c >= min_count]
        _write_fasta(uniques, output_path)
    else:
        seq_to_first = {}
        for h, s, q in _iter_fastq(input_path):
            counter[s] += 1
            if s not in seq_to_first:
                seq_to_first[s] = (h, s, q)
        uniques = [seq_to_first[s] for s, c in counter.items() if c >= min_count]
        _write_fastq(uniques, output_path)
    map_tsv = str(Path(output_path).with_suffix(".map.tsv"))
    _write_tsv([{"seq": s, "count": c} for s, c in counter.items()], map_tsv)
    return {"unique_count": len(uniques), "mapping_tsv": map_tsv, "output_path": output_path}


# -----------------------------
# Mapping, Coverage & Variants (toy)
# -----------------------------

    return {"diversity_tsv": out, "summary": f"{metric}={round(val,3)}", "plots": []}


def estimate_complexity(assignments_tsv: str, metric: str = "shannon"):
    # Expect TSV columns: sample?, read, taxon, score. We'll compute richness per taxon overall.
    taxa = []
    with open(assignments_tsv) as fh:
        header = fh.readline().strip().split("\t")
        idx = header.index("taxon") if "taxon" in header else None
        for line in fh:
            if idx is None:
                break
            taxa.append(line.strip().split("\t")[idx])
    counts = Counter(taxa)
    N = sum(counts.values()) or 1
    if metric == "shannon":
        H = -sum((c/N) * math.log((c/N)+1e-12) for c in counts.values())
        val = H
    else:
        val = len(counts)
    out = str(Path(assignments_tsv).with_suffix(".div.tsv"))
    _write_tsv([{"metric": metric, "value": round(val, 6), "categories": len(counts)}], out)
    return {"diversity_tsv": out, "summary": f"{metric}={round(val,3)}", "plots": []}


def contamination_screen(input_path: str, contaminant_ref: str, clean_path: str, mode: str = "keep_unmapped"):
    # Toy: copy input to clean_path
    fmt = _detect_seq_format(input_path)
    if fmt == "fasta":
        _write_fasta(list(_iter_fasta(input_path)), clean_path)
    else:
        _write_fastq(list(_iter_fastq(input_path)), clean_path)
    return {"clean_path": clean_path, "removed_count": 0, "summary": "Toy contig/read screen (no removal)."}


# -----------------------------
# Gene Finding & Annotation (toy)
# -----------------------------

# -----------------------------
# Genomic Regions & Intervals
# -----------------------------


# -----------------------------
# Genomic Regions & Intervals
# -----------------------------

def _load_bed_like(obj):
    """Accept path to BED (3 or more cols) or list of tuples (chrom,start,end[,name])"""
    if isinstance(obj, str) and os.path.exists(obj):
        regions = []
        with open(obj) as fh:
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                chrom = parts[0]; start = int(parts[1]); end = int(parts[2])
                name = parts[3] if len(parts) > 3 else "."
                regions.append((chrom, start, end, name))
        return regions
    # assume list-like
    reg = []
    for r in obj:
        if len(r) >= 3:
            chrom, start, end = r[0], int(r[1]), int(r[2])
            name = r[3] if len(r) > 3 else "."
            reg.append((chrom, start, end, name))
    return reg

def analyze_genomic_region_overlap(region_sets, output_prefix="overlap_analysis"):
    """Pure-Python overlap analysis (pairwise)."""
    sets = []
    set_names = []
    for i, s in enumerate(region_sets):
        set_names.append(f"Region_Set_{i+1}")
        sets.append(_load_bed_like(s))

    # basic stats
    def total_bp(regs):
        return sum(max(0, e - st) for _, st, e, _ in regs)

    stats = [dict(Set=set_names[i], Regions=len(sets[i]), Total_BP=total_bp(sets[i])) for i in range(len(sets))]

    results = []
    log = "# Genomic Region Overlap Analysis (pure-python)\n"
    log += f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    for i in range(len(sets)):
        for j in range(i+1, len(sets)):
            A = sets[i]; B = sets[j]
            # Build by chrom
            chromA = defaultdict(list); chromB = defaultdict(list)
            for c, s, e, n in A: chromA[c].append((s,e,n))
            for c, s, e, n in B: chromB[c].append((s,e,n))
            overlap_bp = 0
            unique_pairs = set()
            for c in chromA:
                if c not in chromB: continue
                for s1,e1,n1 in chromA[c]:
                    for s2,e2,n2 in chromB[c]:
                        ovl = max(0, min(e1,e2) - max(s1,s2))
                        if ovl > 0:
                            overlap_bp += ovl
                            unique_pairs.add((c, s1, e1, s2, e2))
            pct1 = (overlap_bp / stats[i]["Total_BP"] * 100.0) if stats[i]["Total_BP"] else 0.0
            pct2 = (overlap_bp / stats[j]["Total_BP"] * 100.0) if stats[j]["Total_BP"] else 0.0
            results.append({
                "Set1": set_names[i], "Set2": set_names[j],
                "Overlap_Regions": len(unique_pairs), "Overlap_BP": overlap_bp,
                "Pct_of_Set1": pct1, "Pct_of_Set2": pct2
            })
            log += f"- Between {set_names[i]} and {set_names[j]}: unique_pairs={len(unique_pairs)}, bp={overlap_bp}, pct1={pct1:.2f}%, pct2={pct2:.2f}%\n"

    summary_file = f"{output_prefix}_summary.tsv"
    _write_tsv(results, summary_file)
    log += f"\nSummary saved: {summary_file}\n"
    return log


def merge_regions(bed_path: str, output_path: str, distance: int = 0, keep_names: bool = False):
    regs = sorted(_load_bed_like(bed_path), key=lambda x: (x[0], x[1], x[2]))
    merged = []
    for c, s, e, n in regs:
        if not merged or (c != merged[-1][0]) or (s > merged[-1][2] + distance):
            merged.append([c, s, e, n if keep_names else "."])
        else:
            merged[-1][2] = max(merged[-1][2], e)
    rows = [{"chrom": c, "start": s, "end": e, "name": n} for c, s, e, n in merged]
    _write_tsv(rows, output_path)
    return {"output_path": output_path, "merged_count": len(rows), "summary": "Merged overlapping/adjacent intervals."}


def intersect_regions(a_bed: str, b_bed: str, output_path: str, mode: str = "wo"):
    A = _load_bed_like(a_bed); B = _load_bed_like(b_bed)
    rows = []
    for ca, sa, ea, na in A:
        for cb, sb, eb, nb in B:
            if ca != cb: continue
            ovl = max(0, min(ea, eb) - max(sa, sb))
            if ovl <= 0: continue
            if mode == "wa":
                rows.append({"chrom": ca, "start": sa, "end": ea, "name": na})
            elif mode == "wb":
                rows.append({"chrom": cb, "start": sb, "end": eb, "name": nb})
            else:  # wo
                rows.append({"chrom": ca, "a_start": sa, "a_end": ea, "b_start": sb, "b_end": eb, "overlap_bp": ovl})
    _write_tsv(rows, output_path)
    return {"output_path": output_path, "overlap_count": len(rows), "summary": f"Intersected {len(A)}x{len(B)} intervals."}



# -----------------------------
# Reporting & Visualization (minimal)
# -----------------------------

def multi_sample_summary_report(artifacts: Dict[str, str], format: str = "html"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if format == "html":
        out = "summary_report.html"
        with open(out, "w") as fh:
            fh.write(f"<html><body><h1>Summary Report</h1><p>Generated: {ts}</p><ul>")
            for k, v in artifacts.items():
                fh.write(f"<li><b>{k}</b>: {v}</li>")
            fh.write("</ul></body></html>")
    else:
        out = "summary_report.md"
        with open(out, "w") as fh:
            fh.write(f"# Summary Report\nGenerated: {ts}\n\n")
            for k, v in artifacts.items():
                fh.write(f"- **{k}**: {v}\n")
    return {"report_path": out, "assets_dir": None}


def plot_stackbar_taxa(assignments_list: List[str], rank: str = "genus", top_n: int = 15, normalize: bool = True):
    # No plotting libs: produce a TSV ready for plotting elsewhere.
    combined = Counter()
    for path in assignments_list:
        with open(path) as fh:
            header = fh.readline().strip().split("\t")
            idx = header.index("taxon") if "taxon" in header else None
            for line in fh:
                if idx is None:
                    break
                tax = line.strip().split("\t")[idx]
                combined[tax] += 1
    total = sum(combined.values()) or 1
    top = combined.most_common(top_n)
    rows = [{"taxon": t, "count": c, "fraction": round(c/total, 6)} for t, c in top]
    out = "taxa_stack.tsv"
    _write_tsv(rows, out)
    return {"plot_path": out, "summary": f"Prepared top-{top_n} taxa table (TSV)."}
