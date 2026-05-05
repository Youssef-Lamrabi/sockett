"""
genomeer/src/genomeer/utils/metrics.py
========================================
PHASE 2 — Fix 9: Logging structuré + métriques d'observabilité

Remplace les self._log() textuels par des entrées JSON structurées.
Produit run_metrics.json à la fin de chaque run avec durées par step,
outils utilisés, erreurs, cache hits.

USAGE dans BioAgent.py:
    from genomeer.utils.metrics import RunMetrics
    
    # Au début du run:
    self._metrics = RunMetrics(session_id, run_temp_dir)
    
    # Dans chaque nœud:
    self._metrics.record_step_start(step_title)
    self._metrics.record_step_end(step_title, status="done", tool="run_fastp")
    
    # À la fin du run (dans _finalizer):
    self._metrics.save(run_temp_dir)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

logger = logging.getLogger("genomeer.metrics")


@dataclass
class StepMetric:
    """Métriques d'un step individuel."""
    step_idx: int
    step_title: str
    status: str = "pending"          # pending | done | blocked | error
    tool_name: Optional[str] = None
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_sec: float = 0.0
    retry_count: int = 0
    cache_hit: bool = False
    error_summary: Optional[str] = None
    quality_level: Optional[str] = None    # ok | warn | fail
    env_used: Optional[str] = None


@dataclass
class RunMetrics:
    """
    Collecteur de métriques pour un run complet.
    Thread-safe pour les pipelines batch.
    """
    session_id: str
    run_temp_dir: str
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    steps: List[StepMetric] = field(default_factory=list)
    llm_calls: int = 0
    llm_cache_hits: int = 0
    tool_cache_hits: int = 0
    api_cache_hits: int = 0
    total_errors: int = 0
    peak_mem_mb: float = 0.0
    _step_starts: Dict[str, float] = field(default_factory=dict)
    _lock_obj: Any = field(default=None, init=False, repr=False)

    def _get_lock(self):
        import threading
        if self._lock_obj is None:
            self._lock_obj = threading.RLock()
        return self._lock_obj

    def record_step_start(self, step_idx: int, step_title: str) -> None:
        import time
        with self._get_lock():
            key = f"{step_idx}:{step_title}"
            self._step_starts[key] = time.time()
            existing = next((s for s in self.steps if s.step_idx == step_idx), None)
            if not existing:
                self.steps.append(StepMetric(
                    step_idx=step_idx,
                    step_title=step_title,
                    started_at=self._step_starts[key],
                ))
            else:
                existing.started_at = self._step_starts[key]

    def record_step_end(
        self,
        step_idx: int,
        step_title: str,
        status: str,
        tool_name: Optional[str] = None,
        retry_count: int = 0,
        cache_hit: bool = False,
        error_summary: Optional[str] = None,
        quality_level: Optional[str] = None,
        env_used: Optional[str] = None,
    ) -> None:
        import time
        now = time.time()
        with self._get_lock():
            key = f"{step_idx}:{step_title}"
            started = self._step_starts.get(key, now)
    
            # Trouver le step existant ou créer
            sm = next((s for s in self.steps if s.step_idx == step_idx), None)
            if sm is None:
                sm = StepMetric(step_idx=step_idx, step_title=step_title, started_at=started)
                self.steps.append(sm)
    
            sm.status = status
            sm.tool_name = tool_name
            sm.ended_at = now
            sm.duration_sec = round(now - started, 2)
            sm.retry_count = retry_count
            sm.cache_hit = cache_hit
            sm.error_summary = error_summary
            sm.quality_level = quality_level
            sm.env_used = env_used
    
            if status in ("blocked", "error"):
                self.total_errors += 1
            if cache_hit:
                self.tool_cache_hits += 1
    
            # Snapshot mémoire
            if _PSUTIL_OK:
                try:
                    mem = psutil.virtual_memory()
                    used_mb = (mem.total - mem.available) / 1024 / 1024
                    self.peak_mem_mb = max(self.peak_mem_mb, used_mb)
                except Exception:
                    pass

    def record_llm_call(self, cache_hit: bool = False) -> None:
        self.llm_calls += 1
        if cache_hit:
            self.llm_cache_hits += 1

    def finalize(self) -> None:
        self.ended_at = time.time()

    def save(self, output_dir: Optional[str] = None) -> str:
        """
        Sauvegarde les métriques en JSON dans output_dir.
        Retourne le chemin du fichier créé.
        """
        self.finalize()
        out_dir = Path(output_dir or self.run_temp_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "run_metrics.json"

        total_sec = round(self.ended_at - self.started_at, 2)
        done_steps = [s for s in self.steps if s.status == "done"]
        blocked_steps = [s for s in self.steps if s.status == "blocked"]

        report = {
            "session_id": self.session_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.started_at)),
            "ended_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ended_at)),
            "total_duration_sec": total_sec,
            "summary": {
                "steps_total": len(self.steps),
                "steps_done": len(done_steps),
                "steps_blocked": len(blocked_steps),
                "total_errors": self.total_errors,
                "llm_calls": self.llm_calls,
                "llm_cache_hits": self.llm_cache_hits,
                "llm_cache_rate_pct": round(
                    100 * self.llm_cache_hits / max(1, self.llm_calls), 1
                ),
                "tool_cache_hits": self.tool_cache_hits,
                "peak_mem_mb": round(self.peak_mem_mb, 1),
            },
            "steps": [asdict(s) for s in sorted(self.steps, key=lambda x: x.step_idx)],
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(f"[METRICS] Run report saved → {out_path}")
        return str(out_path)

    def summary_str(self) -> str:
        """Résumé texte court pour l'affichage dans le Finalizer."""
        done = len([s for s in self.steps if s.status == "done"])
        blocked = len([s for s in self.steps if s.status == "blocked"])
        total_sec = round((self.ended_at or time.time()) - self.started_at, 0)
        cache_rate = round(100 * self.llm_cache_hits / max(1, self.llm_calls), 0)
        return (
            f"Steps: {done} done / {blocked} blocked | "
            f"Duration: {int(total_sec)}s | "
            f"LLM calls: {self.llm_calls} (cache {cache_rate}%) | "
            f"Peak RAM: {self.peak_mem_mb:.0f} MB"
        )


# ===========================================================================
# FIX 6 — Timeouts adaptatifs (ajouter dans config.py et _generator)
# ===========================================================================

def compute_adaptive_timeout(
    base_seconds: int,
    input_files: list[str],
    tool_name: str = "",
    available_ram_gb: float = 8.0,
) -> int:
    """
    Calcule un timeout adaptatif selon la taille des fichiers d'entrée.

    Formule: timeout = base + alpha * size_gb
    Avec alpha variable par outil (SPAdes > Kraken2 > fastp).

    Parameters
    ----------
    base_seconds  : Timeout de base (ex: 600s)
    input_files   : Liste des chemins de fichiers d'entrée
    tool_name     : Nom de l'outil (pour alpha spécifique)
    available_ram_gb : RAM disponible (réduit le timeout si mémoire limitée)

    Returns
    -------
    Timeout en secondes (min: base, max: 24h)
    """
    # Alpha en secondes par GB d'entrée
    TOOL_ALPHA = {
        "metaspades":  3600,   # SPAdes: lent, 1h/GB
        "megahit":     1200,   # MEGAHIT: rapide, 20min/GB
        "flye":        2400,   # Flye: lent, 40min/GB
        "humann":      1800,   # HUMAnN3: 30min/GB
        "gtdbtk":      2400,   # GTDB-Tk: 40min/GB
        "checkm2":     600,    # CheckM2: rapide, 10min/GB
        "kraken2":     300,    # Kraken2: très rapide, 5min/GB
        "fastp":       120,    # fastp: ultra-rapide, 2min/GB
        "diamond":     600,    # DIAMOND: 10min/GB
        "prokka":      900,    # Prokka: 15min/GB
    }

    # Calculer la taille totale des fichiers d'entrée
    total_bytes = 0
    for fpath in (input_files or []):
        try:
            if os.path.exists(fpath):
                total_bytes += os.path.getsize(fpath)
        except OSError:
            pass

    size_gb = total_bytes / (1024 ** 3)

    # Alpha selon l'outil
    tool_lower = tool_name.lower()
    alpha = base_seconds  # défaut: 1x base par GB
    for key, val in TOOL_ALPHA.items():
        if key in tool_lower:
            alpha = val
            break

    # Pénalité si RAM faible (outils memory-intensive peuvent swap)
    ram_factor = 1.0
    if available_ram_gb < 8 and any(t in tool_lower for t in ["metaspades", "megahit", "flye"]):
        ram_factor = 1.5  # 50% de temps supplémentaire si RAM < 8GB

    timeout = int(base_seconds + alpha * size_gb * ram_factor)

    # Bornes: min=base, max=24h
    timeout = max(base_seconds, min(timeout, 86400))

    logger.info(
        f"[TIMEOUT] tool={tool_name} size={size_gb:.2f}GB "
        f"alpha={alpha} ram_factor={ram_factor:.1f} → {timeout}s"
    )
    return timeout


def get_available_ram_gb() -> float:
    """Retourne la RAM disponible en GB (via psutil si disponible)."""
    if _PSUTIL_OK:
        try:
            return psutil.virtual_memory().available / (1024 ** 3)
        except Exception:
            pass
    return 8.0  # Valeur par défaut conservatrice
