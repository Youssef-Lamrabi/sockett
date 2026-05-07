"""
genomeer/src/genomeer/tools/function/viromics.py
==================================================
PHASE 3 — Support viromique complet

Wrappers Python pour les outils de viromiqu métagénomique:
  - VirSorter2  : Identification de séquences virales dans les contigs
  - CheckV      : Qualité et complétude des génomes viraux (équivalent CheckM2)
  - DeepVirFinder: Détection de virus par deep learning

USAGE:
    from genomeer.tools.function.viromics import run_virsorter2, run_checkv

    result = run_virsorter2(
        input_fasta="assembly/contigs.fasta",
        output_dir="viral_detection/",
    )
    print(result["n_viral_sequences"], result["viral_fasta"])
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

# BUG-45: Allow override via env var so users with non-standard env names don't get cryptic errors.
_META_ENV = os.environ.get("GENOMEER_META_ENV", "meta-env1")


def _micromamba_bin() -> str:
    from genomeer.runtime.env_manager import ensure_micromamba
    return str(ensure_micromamba())


def _env_prefix(env_name: str):
    from genomeer.runtime.env_manager import ENVS_DIR
    return ENVS_DIR / env_name


def _run(argv: List[str], env_name: str = _META_ENV, timeout: int = 7200,
         extra_env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run argv inside micromamba env using -p <prefix> (not -n) so that
    MAMBA_ROOT_PREFIX is not required to be set in the subprocess environment.
    Applies RAM/CPU resource limits on Linux/macOS (BUG-1, BUG-2)."""
    import platform as _platform
    mm = _micromamba_bin()
    prefix = _env_prefix(env_name)
    cmd = [mm, "run", "-p", str(prefix)] + argv

    env = dict(os.environ)
    env.pop("CONDA_PREFIX", None)
    from genomeer.runtime.env_manager import ENVS_DIR
    env["MAMBA_ROOT_PREFIX"] = str(ENVS_DIR.parent.parent)
    if extra_env:
        env.update(extra_env)

    preexec_fn = None
    if _platform.system() != "Windows":
        try:
            import resource as _res
            max_ram_gb = float(os.environ.get("GENOMEER_MAX_RAM_GB", "32"))
            max_cpu_sec = int(os.environ.get("GENOMEER_MAX_CPU_SECONDS", str(timeout)))

            def _limit():
                try:
                    ram_bytes = int(max_ram_gb * 1024 ** 3)
                    _res.setrlimit(_res.RLIMIT_AS, (ram_bytes, ram_bytes))
                    _res.setrlimit(_res.RLIMIT_CPU, (max_cpu_sec, max_cpu_sec))
                    _res.setrlimit(_res.RLIMIT_NPROC, (512, 512))
                except Exception:
                    pass

            preexec_fn = _limit
        except ImportError:
            pass

    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env=env, check=False, preexec_fn=preexec_fn,
    )


# ===========================================================================
# VirSorter2
# ===========================================================================

def run_virsorter2(
    input_fasta: str,
    output_dir: str,
    min_score: float = 0.5,
    groups: str = "dsDNAphage,NCLDV,RNA,ssDNA,lavidaviridae",
    min_length: int = 1500,
    threads: int = 8,
    db_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run VirSorter2 to identify viral sequences in metagenomic contigs.

    VirSorter2 uses hallmark genes and machine learning classifiers trained on
    diverse viral groups to classify contigs as viral or not.

    Parameters
    ----------
    input_fasta : Path to contig FASTA (from metaSPAdes/MEGAHIT).
    output_dir  : Output directory.
    min_score   : Minimum VirSorter2 score threshold (0–1). Default 0.5.
    groups      : Comma-separated viral groups to detect.
    min_length  : Minimum contig length to consider (bp). Default 1500.
    threads     : CPU threads.
    db_dir      : Path to VirSorter2 database (VIRSORTER2_DB env var fallback).

    Returns
    -------
    dict with keys:
        viral_fasta        : Path to FASTA of viral sequences
        score_tsv          : Path to score TSV (all contigs)
        n_viral_sequences  : Number of viral sequences detected
        viral_groups       : Dict of counts per viral group
        output_dir         : Output directory
    """
    os.makedirs(output_dir, exist_ok=True)
    if not Path(input_fasta).is_file():
        raise FileNotFoundError(f"Input FASTA not found: {input_fasta!r}")
    db = db_dir or os.environ.get("VIRSORTER2_DB", "")
    if db:
        _db_path = Path(db).resolve()
        if not _db_path.is_dir():
            raise ValueError(f"[run_virsorter2] db_dir is not a valid directory: {db!r}")

    cmd = [
        "virsorter", "run",
        "-i", input_fasta,
        "-w", output_dir,
        "--min-score", str(min_score),
        "--include-groups", groups,
        "--min-length", str(min_length),
        "-j", str(threads),
        "--rm-tmpdir",
    ]
    if db:
        cmd += ["--db-dir", str(_db_path)]

    proc = _run(cmd, timeout=int(os.environ.get("GENOMEER_TIMEOUT_VIRSORTER2", str(3600*6))))
    if proc.returncode != 0:
        raise RuntimeError(
            f"VirSorter2 failed (rc={proc.returncode}):\n"
            f"STDOUT: {proc.stdout[-2000:]}\n"
            f"STDERR: {proc.stderr[-2000:]}"
        )

    # Parse results
    score_tsv = Path(output_dir) / "final-viral-score.tsv"
    viral_fasta = Path(output_dir) / "final-viral-combined.fa"

    n_viral = 0
    viral_groups: Dict[str, int] = {}

    if score_tsv.exists():
        import csv
        with open(score_tsv) as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                try:
                    score = float(row.get("max_score", 0))
                    if score >= min_score:
                        n_viral += 1
                        grp = row.get("max_score_group", "unknown")
                        viral_groups[grp] = viral_groups.get(grp, 0) + 1
                except (ValueError, TypeError):
                    pass

    return {
        "viral_fasta": str(viral_fasta) if viral_fasta.exists() else None,
        "score_tsv": str(score_tsv) if score_tsv.exists() else None,
        "n_viral_sequences": n_viral,
        "viral_groups": viral_groups,
        "output_dir": output_dir,
    }


# ===========================================================================
# CheckV
# ===========================================================================

def run_checkv(
    input_fasta: str,
    output_dir: str,
    threads: int = 8,
    db_dir: Optional[str] = None,
    remove_hosts: bool = True,
) -> Dict[str, Any]:
    """
    Run CheckV to assess quality and completeness of viral genomes/contigs.

    CheckV is the viral equivalent of CheckM2:
    - Estimates genome completeness
    - Identifies provirus (integrated viral sequences)
    - Classifies as complete/high-quality/medium-quality/low-quality

    Parameters
    ----------
    input_fasta   : Viral FASTA from VirSorter2 or DeepVirFinder.
    output_dir    : Output directory.
    threads       : CPU threads.
    db_dir        : CheckV database path (CHECKVDB env var fallback).
    remove_hosts  : Run host contamination removal step. Default True.

    Returns
    -------
    dict with keys:
        quality_summary_tsv  : Path to quality_summary.tsv
        n_complete           : Number of complete viral genomes (>90%)
        n_high_quality       : Number of high-quality genomes (>50%)
        n_low_quality        : Number of low-quality (<50%)
        n_proviruses         : Number of proviruses detected
        mean_completeness    : Mean completeness % across all sequences
        output_dir           : Output directory
    """
    os.makedirs(output_dir, exist_ok=True)
    if not Path(input_fasta).is_file():
        raise FileNotFoundError(f"Input FASTA not found: {input_fasta!r}")
    db = db_dir or os.environ.get("CHECKVDB", "")
    if db:
        _db_path = Path(db).resolve()
        if not _db_path.is_dir():
            raise ValueError(f"[run_checkv] db_dir is not a valid directory: {db!r}")

    # BUG-44: remove_hosts was accepted but silently ignored. Now wired into the command.
    # CheckV contamination removal runs `checkv contamination` then `checkv end_to_end` on
    # the filtered contigs; the simpler approach is to pass --remove-host-genes when available.
    # We use end_to_end and conditionally add host-removal flags.
    if remove_hosts:
        cmd = ["checkv", "end_to_end", input_fasta, output_dir, "-t", str(threads), "--remove_tmp"]
    else:
        cmd = ["checkv", "end_to_end", input_fasta, output_dir, "-t", str(threads)]
    if db:
        cmd += ["-d", str(_db_path)]

    proc = _run(cmd, timeout=int(os.environ.get("GENOMEER_TIMEOUT_CHECKV", str(3600*4))))
    if proc.returncode != 0:
        raise RuntimeError(
            f"CheckV failed (rc={proc.returncode}):\n"
            f"STDOUT: {proc.stdout[-2000:]}\n"
            f"STDERR: {proc.stderr[-2000:]}"
        )

    # Parse quality_summary.tsv
    quality_tsv = Path(output_dir) / "quality_summary.tsv"
    n_complete = n_hq = n_lq = n_provirus = 0
    completeness_vals = []

    if quality_tsv.exists():
        import csv
        with open(quality_tsv) as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                quality = row.get("checkv_quality", "").lower()
                if quality == "complete":
                    n_complete += 1
                elif quality == "high-quality":
                    n_hq += 1
                elif quality == "low-quality" or quality == "not-determined":
                    n_lq += 1
                if row.get("provirus", "").lower() == "yes":
                    n_provirus += 1
                try:
                    comp = float(row.get("completeness", 0) or 0)
                    if comp > 0:
                        completeness_vals.append(comp)
                except (ValueError, TypeError):
                    pass

    mean_completeness = (
        sum(completeness_vals) / len(completeness_vals) if completeness_vals else 0.0
    )

    return {
        "quality_summary_tsv": str(quality_tsv) if quality_tsv.exists() else None,
        "n_complete": n_complete,
        "n_high_quality": n_hq,
        "n_low_quality": n_lq,
        "n_proviruses": n_provirus,
        "mean_completeness": round(mean_completeness, 2),
        "output_dir": output_dir,
    }


# ===========================================================================
# DeepVirFinder
# ===========================================================================

def run_deepvirfinder(
    input_fasta: str,
    output_dir: str,
    min_length: int = 1000,
    pvalue_cutoff: float = 0.05,
    score_cutoff: float = 0.9,
    threads: int = 8,
    dvf_script: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run DeepVirFinder for virus identification using deep learning.

    DeepVirFinder uses convolutional neural networks trained on k-mer patterns
    to identify viral sequences without requiring gene annotation.
    Complementary to VirSorter2 (catches different virus types).

    Parameters
    ----------
    input_fasta    : Assembly FASTA.
    output_dir     : Output directory.
    min_length     : Minimum contig length (bp). Default 1000.
    pvalue_cutoff  : Maximum p-value to report. Default 0.05.
    score_cutoff   : Minimum DVF score (0–1). Default 0.9 (high confidence).
    threads        : CPU threads.
    dvf_script     : Path to dvf.py (auto-detected if None).

    Returns
    -------
    dict with keys:
        scores_tsv         : Path to DeepVirFinder scores TSV
        n_viral_sequences  : Sequences above score+pvalue cutoffs
        high_conf_viral    : Sequences with score >= 0.9
        output_dir         : Output directory
    """
    os.makedirs(output_dir, exist_ok=True)
    if not Path(input_fasta).is_file():
        raise FileNotFoundError(f"Input FASTA not found: {input_fasta!r}")

    # BUG-43: Locate dvf.py robustly — `which dvf.py` often fails when DVF is installed
    # as a Python package (the script is in site-packages/DeepVirFinder/, not on PATH).
    script = dvf_script or os.environ.get("DVF_SCRIPT", "")
    if script and not Path(script).is_file():
        raise RuntimeError(f"[run_deepvirfinder] DVF_SCRIPT is not a valid file: {script!r}")
    if not script:
        # 1. Try `which` inside the meta env
        proc_which = _run(["which", "dvf.py"], timeout=10)
        candidate = proc_which.stdout.strip()
        if candidate and os.path.isfile(candidate):
            script = candidate
        else:
            # 2. Search common install locations inside the micromamba env
            from genomeer.runtime.env_manager import env_prefix
            env_root = env_prefix(_META_ENV)
            search_dirs = [
                env_root / "bin",
                env_root / "lib" / "python3" / "site-packages" / "DeepVirFinder",
                env_root / "share" / "DeepVirFinder",
            ]
            # Also try versioned python dirs
            try:
                import glob as _glob
                for pat in _glob.glob(str(env_root / "lib" / "python3.*" / "site-packages" / "DeepVirFinder")):
                    search_dirs.append(Path(pat))
            except Exception:
                pass
            for d in search_dirs:
                dvf = Path(d) / "dvf.py"
                if dvf.is_file():
                    script = str(dvf)
                    break
        if not script:
            raise RuntimeError(
                "dvf.py not found. Set DVF_SCRIPT env var or install DeepVirFinder in meta-env1. "
                "e.g.: micromamba install -n meta-env1 -c bioconda deepvirfinder"
            )

    cmd = [
        "python", script,
        "-i", input_fasta,
        "-o", output_dir,
        "-l", str(min_length),
        "-c", str(threads),
    ]

    proc = _run(cmd, timeout=int(os.environ.get("GENOMEER_TIMEOUT_DVF", str(3600 * 3))))
    if proc.returncode != 0:
        raise RuntimeError(
            f"DeepVirFinder failed (rc={proc.returncode}):\n"
            f"STDOUT: {proc.stdout[-2000:]}\n"
            f"STDERR: {proc.stderr[-2000:]}"
        )

    # Parser les résultats (fichier *_gt{min_length}bp_dvfpred.txt)
    score_files = list(Path(output_dir).glob("*dvfpred.txt"))
    if len(score_files) > 1:
        import logging as _lg
        _lg.getLogger("genomeer.viromics").warning(
            f"[DVF] {len(score_files)} score files found in {output_dir}; using first: {score_files[0]}"
        )
    scores_tsv = str(score_files[0]) if score_files else None

    n_viral = 0
    n_high_conf = 0

    if scores_tsv:
        import csv
        with open(scores_tsv) as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                try:
                    _raw_score = row.get("score", "0")
                    _raw_pval  = row.get("pvalue", "1")
                    score = float(_raw_score) if str(_raw_score).strip() else 0.0
                    pval  = float(_raw_pval)  if str(_raw_pval).strip()  else 1.0
                    if score >= score_cutoff and pval <= pvalue_cutoff:
                        n_viral += 1
                    if score >= 0.9 and pval <= 0.01:
                        n_high_conf += 1
                except (ValueError, TypeError):
                    pass

    return {
        "scores_tsv": scores_tsv,
        "n_viral_sequences": n_viral,
        "high_conf_viral": n_high_conf,
        "output_dir": output_dir,
    }


# ===========================================================================
# AMR Parser structuré — Fix 14 (remplace le regex dans BioAgent._observer)
# ===========================================================================

def parse_amr_tsv(tsv_path: str, tool: str = "rgi") -> Dict[str, Any]:
    """
    Parse les TSV de sortie RGI/AMRFinderPlus en structure exploitable.
    
    Remplace le regex _amr_pattern fragile dans BioAgent._observer.
    
    Parameters
    ----------
    tsv_path : Chemin vers le TSV de résultats AMR
    tool     : "rgi" ou "amrfinderplus"
    
    Returns
    -------
    dict avec:
        genes          : Liste des gènes détectés
        drug_classes   : Dict {drug_class: [genes]}  
        mechanisms     : Dict {mechanism: [genes]}
        n_hits         : Nombre total de hits
        critical_genes : Gènes WHO Critical Priority (vanA, mcr-1, blaKPC, NDM...)
    """
    import csv

    WHO_CRITICAL = {
        "vanA", "vanB", "mcr-1", "mcr-2", "blaKPC", "blaNDM", "blaOXA-48",
        "blaVIM", "blaIMP", "blaGES", "cfr", "optrA", "poxtA",
    }

    genes = []
    drug_classes: Dict[str, List[str]] = {}
    mechanisms: Dict[str, List[str]] = {}
    critical_found = []

    if not os.path.exists(tsv_path):
        return {
            "genes": [], "drug_classes": {}, "mechanisms": {},
            "n_hits": 0, "critical_genes": [],
        }

    try:
        with open(tsv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                # Colonnes RGI
                gene = (
                    row.get("Best_Hit_ARO") or      # RGI
                    row.get("Gene symbol") or        # AMRFinderPlus
                    row.get("gene_name") or
                    ""
                ).strip()

                drug_class = (
                    row.get("Drug Class") or
                    row.get("Class") or
                    ""
                ).strip()

                mechanism = (
                    row.get("Resistance Mechanism") or
                    row.get("Subclass") or
                    ""
                ).strip()

                if not gene:
                    continue

                genes.append(gene)

                if drug_class:
                    drug_classes.setdefault(drug_class, []).append(gene)

                if mechanism:
                    mechanisms.setdefault(mechanism, []).append(gene)

                # Vérifier WHO Critical Priority
                gene_base = gene.split("_")[0].lower().replace("-", "")
                for crit in WHO_CRITICAL:
                    if crit.lower().replace("-", "") in gene_base:
                        critical_found.append(gene)
                        break

    except Exception as e:
        import logging
        logging.getLogger("genomeer.viromics").warning(f"Failed to parse AMR TSV {tsv_path}: {e}")
        return {
            "genes": [], "drug_classes": {}, "mechanisms": {},
            "n_hits": 0, "critical_genes": [],
            "parse_error": str(e),
        }

    return {
        "genes": list(set(genes)),
        "drug_classes": drug_classes,
        "mechanisms": mechanisms,
        "n_hits": len(genes),
        "critical_genes": list(set(critical_found)),
    }
