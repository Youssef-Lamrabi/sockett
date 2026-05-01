"""
genomeer/tools/parsers/parsers.py
===================================
Intelligent output parsers for major metagenomics tools.

Instead of blindly truncating stdout at 12000 chars, these parsers extract
the key biological metrics the OBSERVER needs, giving a compact, informative
summary even from a multi-MB output file.

AXE 3.3 implementation: each parser extracts ONLY metrics relevant to the
biological interpretation:
- Assembly:  N50, #contigs, max contig, total bases
- QC:        reads in/out, Q30%, duplication%
- Taxonomy:  % classified, top-5 organisms, sample type
- Binning:   #bins, % high-quality bins
- CheckM2:   completeness/contamination per bin
- Annotation: #genes, #annotated, AMR genes

Usage:
    from genomeer.tools.parsers import parse_tool_output
    short = parse_tool_output("fastp", raw_stdout, result_dict)
"""

from __future__ import annotations
import re
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Registry: keyword in step title → parser function
# ---------------------------------------------------------------------------
_PARSERS: Dict[str, Any] = {}

def _register(*keywords):
    """Decorator: register parser for one or more keywords."""
    def _deco(fn):
        for kw in keywords:
            _PARSERS[kw.lower()] = fn
        return fn
    return _deco


def get_parser_for_step(step_title: str):
    """Return the best parser function for a step title (or None)."""
    title_lower = (step_title or "").lower()
    for kw, fn in _PARSERS.items():
        if kw in title_lower:
            return fn
    return None


def parse_tool_output(tool_name_or_step: str, stdout: str, result_dict: Optional[dict] = None, output_dir: Optional[str] = None) -> str:
    """
    Parse tool output and return a compact, observer-friendly summary.

    Parameters
    ----------
    tool_name_or_step : Tool name or step title (used to select parser)
    stdout            : Raw stdout from tool execution
    result_dict       : Optional result dict returned by wrapper function
    output_dir        : Optional output directory to scan for output files

    Returns compact summary string (always < 2000 chars).
    """
    parser = get_parser_for_step(tool_name_or_step)
    if parser:
        try:
            summary = parser(stdout or "", result_dict or {}, output_dir)
            if summary:
                return _cap(summary, 2000)
        except Exception as e:
            pass  # Fall through to truncation on error

    # Default: intelligent truncation — keep first 1000 and last 500 chars
    if not stdout:
        return "(no output)"
    if len(stdout) <= 2000:
        return stdout
    return stdout[:1000] + f"\n\n... [{len(stdout) - 1500} chars omitted] ...\n\n" + stdout[-500:]


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 20] + "\n...<truncated>"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

@_register("fastp", "trimming", "adapter", "qc")
def _parse_fastp(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract key QC metrics from fastp stdout or JSON report."""
    # Try reading the JSON report first (most accurate)
    json_path = result.get("json_report") or (
        str(Path(output_dir) / "fastp.json") if output_dir else None
    )
    if json_path and Path(json_path).exists():
        try:
            with open(json_path) as f:
                d = json.load(f)
            s = d.get("summary", {})
            bf  = s.get("before_filtering", {})
            af  = s.get("after_filtering", {})
            filt = d.get("filtering_result", {})
            q30_before = bf.get("q30_rate", 0) * 100
            q30_after  = af.get("q30_rate", 0) * 100
            reads_in   = bf.get("total_reads", "?")
            reads_out  = af.get("total_reads", "?")
            bases_in   = bf.get("total_bases", "?")
            dup_rate   = bf.get("duplication_rate", 0) * 100 if "duplication_rate" in bf else None
            low_q      = filt.get("low_quality_reads", 0)
            too_short  = filt.get("too_short_reads", 0)
            lines = [
                f"[fastp QC Summary]",
                f"  Reads: {reads_in:,} → {reads_out:,} (filtered {low_q + too_short:,})",
                f"  Bases: {bases_in:,}",
                f"  Q30: {q30_before:.1f}% → {q30_after:.1f}%",
            ]
            if dup_rate is not None:
                lines.append(f"  Duplication: {dup_rate:.1f}%")
            lines.append(f"  Low quality reads removed: {low_q:,} | Too short: {too_short:,}")
            return "\n".join(lines)
        except Exception:
            pass

    # Fallback: parse stdout
    lines_out = []
    for line in (stdout or "").splitlines():
        if any(kw in line.lower() for kw in ["read", "q30", "base", "duplication", "filter", "pass", "result"]):
            lines_out.append(line.strip())
    return "\n".join(lines_out[:30]) or stdout[:1000]


@_register("metaspades", "megahit", "flye", "assembly", "assembl")
def _parse_assembly(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract N50, #contigs, max contig from assembly stdout or scaffolds.fasta."""
    # Try to compute from FASTA file
    fasta = result.get("contigs") or result.get("scaffolds") or result.get("assembly_fasta")
    if not fasta and output_dir:
        for candidate in ["scaffolds.fasta", "contigs.fasta", "final.contigs.fa", "assembly.fasta"]:
            p = Path(output_dir) / candidate
            if p.exists():
                fasta = str(p)
                break

    if fasta and Path(fasta).exists():
        lengths = []
        try:
            with open(fasta) as f:
                cur_len = 0
                for line in f:
                    line = line.strip()
                    if line.startswith(">"):
                        if cur_len > 0:
                            lengths.append(cur_len)
                        cur_len = 0
                    else:
                        cur_len += len(line)
                if cur_len > 0:
                    lengths.append(cur_len)
        except Exception:
            pass

        if lengths:
            lengths.sort(reverse=True)
            total = sum(lengths)
            half  = total / 2
            cum   = 0
            n50   = 0
            for l in lengths:
                cum += l
                if cum >= half:
                    n50 = l
                    break
            n_contigs = len(lengths)
            max_contig = lengths[0]
            n_gt1k = sum(1 for l in lengths if l >= 1000)
            return (
                f"[Assembly Stats — from FASTA]\n"
                f"  Contigs: {n_contigs:,} total, {n_gt1k:,} ≥1kb\n"
                f"  N50: {n50:,} bp\n"
                f"  Max contig: {max_contig:,} bp\n"
                f"  Total assembled: {total:,} bp ({total/1e6:.1f} Mb)"
            )

    # Fallback: parse stdout for SPAdes/MEGAHIT stats lines
    stats_lines = []
    for line in (stdout or "").splitlines():
        low = line.lower()
        if any(k in low for k in ["n50", "contigs", "total length", "max", "scaffolds", "assembly", "k-mer", "result"]):
            stats_lines.append(line.strip())
    return "\n".join(stats_lines[:20]) or stdout[:1000]


@_register("kraken2", "bracken", "taxonom", "classif")
def _parse_taxonomy(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract classification rate and top taxa from Kraken2/Bracken report."""
    # Look for report file
    # FIX: run_kraken2 returns key "report"; also check "report_file" and "kraken_report" for compatibility
    report = result.get("report") or result.get("report_file") or result.get("kraken_report")
    if not report and output_dir:
        for cand in ["kraken2_report.txt", "classification_report.txt", "report.txt"]:
            p = Path(output_dir) / cand
            if p.exists():
                report = str(p)
                break

    if report and Path(report).exists():
        try:
            top_taxa = []
            classified_pct = None
            unclassified_pct = None
            with open(report) as f:
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) < 6:
                        continue
                    pct = float(parts[0].strip())
                    rank = parts[3].strip()
                    name = parts[5].strip()
                    if name == "unclassified":
                        unclassified_pct = pct
                    elif name == "root":
                        classified_pct = 100 - (unclassified_pct or 0)
                    elif rank in ("S", "G") and pct > 0.1:
                        top_taxa.append((pct, rank, name))
            top_taxa.sort(reverse=True)
            lines = ["[Taxonomy Report]"]
            if classified_pct is not None:
                lines.append(f"  Classified: {classified_pct:.1f}% | Unclassified: {unclassified_pct:.1f}%")
            lines.append("  Top taxa:")
            for pct, rank, name in top_taxa[:8]:
                lines.append(f"    [{rank}] {name}: {pct:.2f}%")
            return "\n".join(lines)
        except Exception:
            pass

    # Parse stdout for % classified line
    lines_out = []
    for line in (stdout or "").splitlines():
        if any(k in line.lower() for k in ["classified", "sequences", "report", "loaded", "processing"]):
            lines_out.append(line.strip())
    return "\n".join(lines_out[:25]) or stdout[:1000]


@_register("metaphlan", "metaphlan4")
def _parse_metaphlan(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract top species and unclassified rate from MetaPhlAn4."""
    profile = result.get("profile_txt")
    if not profile and output_dir:
        for cand in ["metaphlan_profile.txt", "taxonomic_profile.txt"]:
            p = Path(output_dir) / cand
            if p.exists():
                profile = str(p)
                break

    if profile and Path(profile).exists():
        try:
            species = []
            unclassified = None
            with open(profile) as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) < 3:
                        continue
                    clade, pct = parts[0], float(parts[-1])
                    if "UNCLASSIFIED" in clade.upper():
                        unclassified = pct
                    elif "|s__" in clade and pct > 0:
                        sname = clade.split("|s__")[-1].replace("_", " ")
                        species.append((pct, sname))
            species.sort(reverse=True)
            lines = ["[MetaPhlAn4 Profile]"]
            if unclassified is not None:
                lines.append(f"  Unclassified: {unclassified:.1f}%")
            lines.append("  Top species:")
            for pct, name in species[:8]:
                lines.append(f"    {name}: {pct:.2f}%")
            return "\n".join(lines)
        except Exception:
            pass

    return "\n".join(
        l.strip() for l in (stdout or "").splitlines()
        if any(k in l.lower() for k in ["species", "classified", "relative", "abundance", "processing"])
    )[:1200]


@_register("metabat", "binning")
def _parse_binning(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Count bins from MetaBAT2 output directory."""
    bins_dir = result.get("bins_dir") or output_dir
    n_bins = 0
    if bins_dir and Path(bins_dir).exists():
        n_bins = len([f for f in Path(bins_dir).glob("*.fa")] +
                     [f for f in Path(bins_dir).glob("*.fasta")])
    lines = [f"[Binning Summary]", f"  Bins produced: {n_bins}"]
    # Parse stdout for coverage info
    for line in (stdout or "").splitlines():
        if any(k in line.lower() for k in ["bin", "contig", "depth", "coverage", "cluster"]):
            lines.append("  " + line.strip())
    return "\n".join(lines[:20])


@_register("checkm", "checkm2", "quality")
def _parse_checkm(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Parse CheckM2 quality statistics."""
    # Look for quality_report.tsv
    report = result.get("quality_report") or result.get("report")
    if not report and output_dir:
        for cand in ["quality_report.tsv", "checkm2_results.tsv", "results.tsv"]:
            p = Path(output_dir) / cand
            if p.exists():
                report = str(p)
                break

    if report and Path(report).exists():
        try:
            import csv
            hq, mq, lq = [], [], []
            with open(report) as f:
                reader = csv.DictReader(f, delimiter="\t")
                for row in reader:
                    comp = float(row.get("Completeness", 0))
                    cont = float(row.get("Contamination", 0))
                    name = row.get("Name", "?")
                    if comp >= 90 and cont <= 5:
                        hq.append((name, comp, cont))
                    elif comp >= 50 and cont <= 10:
                        mq.append((name, comp, cont))
                    else:
                        lq.append((name, comp, cont))
            all_bins = hq + mq + lq
            lines = [
                f"[CheckM2 Quality Assessment]",
                f"  Total bins: {len(all_bins)}",
                f"  High quality (≥90% comp, ≤5% cont): {len(hq)}",
                f"  Medium quality (≥50% comp, ≤10% cont): {len(mq)}",
                f"  Low quality: {len(lq)}",
                "  Top bins:",
            ]
            for name, comp, cont in sorted(all_bins, key=lambda x: -x[1])[:5]:
                lines.append(f"    {name}: {comp:.1f}% comp, {cont:.1f}% cont")
            return "\n".join(lines)
        except Exception:
            pass

    return "\n".join(
        l.strip() for l in (stdout or "").splitlines()
        if any(k in l.lower() for k in ["completeness", "contamination", "bin", "quality"])
    )[:1200]


@_register("prokka", "annotation", "prodigal")
def _parse_annotation(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract gene counts from Prokka/Prodigal output."""
    # Try GFF count
    gff = result.get("gff_file")
    if not gff and output_dir:
        for cand in Path(output_dir).rglob("*.gff"):
            gff = str(cand)
            break

    n_genes = 0
    n_cds = 0
    if gff and Path(gff).exists():
        try:
            with open(gff) as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) >= 3:
                        n_genes += 1
                        if parts[2] == "CDS":
                            n_cds += 1
        except Exception:
            pass

    lines = [f"[Annotation Summary]"]
    if n_genes:
        lines.append(f"  Features in GFF: {n_genes:,} (CDS: {n_cds:,})")

    for line in (stdout or "").splitlines():
        if any(k in line.lower() for k in ["cds", "gene", "trna", "rrna", "annotated", "hypothetical"]):
            lines.append("  " + line.strip())
    return "\n".join(lines[:20])


@_register("diamond", "blast", "protein")
def _parse_diamond(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract alignment stats from DIAMOND output."""
    out_tsv = result.get("output_file") or result.get("tsv")
    n_hits = 0
    if out_tsv and Path(out_tsv).exists():
        try:
            with open(out_tsv) as f:
                n_hits = sum(1 for _ in f if not _.startswith("#"))
        except Exception:
            pass

    lines = [f"[DIAMOND Results]"]
    if n_hits:
        lines.append(f"  Alignments found: {n_hits:,}")

    for line in (stdout or "").splitlines():
        if any(k in line.lower() for k in ["query", "target", "score", "aligned", "hits", "total", "runtime"]):
            lines.append("  " + line.strip())
    return "\n".join(lines[:20])


@_register("amrfinder", "rgi", "amr", "resistance")
def _parse_amr(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract AMR gene counts and drug classes from AMRFinderPlus/RGI."""
    tsv = result.get("output_file") or result.get("amr_tsv")
    if not tsv and output_dir:
        for cand in ["amrfinderplus.tsv", "rgi_main.txt", "amr_results.tsv"]:
            p = Path(output_dir) / cand
            if p.exists():
                tsv = str(p)
                break

    if tsv and Path(tsv).exists():
        try:
            import csv
            genes = []
            drug_classes = set()
            with open(tsv) as f:
                reader = csv.DictReader(f, delimiter="\t")
                for row in reader:
                    gene = row.get("Gene symbol") or row.get("Best_Hit_ARO") or row.get("element_symbol", "?")
                    drug = row.get("Drug class") or row.get("Drug Class") or row.get("drug_class", "?")
                    genes.append(gene)
                    if drug and drug != "?":
                        drug_classes.update(drug.split(", "))
            # Persister les gènes dans result_dict pour remontée dans le manifest
            if isinstance(result, dict):
                result["amr_genes_detected"] = genes
                result["amr_drug_classes"] = sorted(drug_classes)
            lines = [
                f"[AMR Detection]",
                f"  AMR genes detected: {len(genes)}",
                f"  Drug classes affected: {', '.join(sorted(drug_classes)) or 'none'}",
                f"  Genes: {', '.join(genes[:10])}{'...' if len(genes)>10 else ''}",
            ]
            return "\n".join(lines)
        except Exception:
            pass

    return "\n".join(
        l.strip() for l in (stdout or "").splitlines()
        if any(k in l.lower() for k in ["gene", "resistance", "drug", "class", "amr", "found"])
    )[:1000]


@_register("humann", "humann3", "pathway", "functional")
def _parse_humann(stdout: str, result: dict, output_dir: Optional[str]) -> str:
    """Extract pathway/gene family counts from HUMAnN3."""
    lines = [f"[HUMAnN3 Results]"]
    # Count pathway rows in output
    pathways_tsv = result.get("pathabundance")
    if not pathways_tsv and output_dir:
        for cand in Path(output_dir).glob("*pathabundance*"):
            pathways_tsv = str(cand)
            break
    if pathways_tsv and Path(pathways_tsv).exists():
        try:
            n_paths = sum(1 for l in open(pathways_tsv) if not l.startswith("#") and l.strip())
            lines.append(f"  Pathways quantified: {n_paths:,}")
        except Exception:
            pass

    for line in (stdout or "").splitlines():
        if any(k in line.lower() for k in ["total", "mapped", "unmapped", "pathway", "gene families", "aligned"]):
            lines.append("  " + line.strip())
    return "\n".join(lines[:20])
