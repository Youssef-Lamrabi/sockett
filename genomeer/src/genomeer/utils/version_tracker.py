"""
genomeer/src/genomeer/utils/version_tracker.py
================================================
PHASE 2 — Fix 8: Versionnement des outils et bases de données

Collecte et persiste les versions des outils utilisés dans un run,
garantissant la reproductibilité des résultats.

USAGE dans BioAgent._observer (après step done):
    from genomeer.utils.version_tracker import VersionTracker
    
    tracker = VersionTracker()
    tracker.record_tool("kraken2", env_name="meta-env1")
    tracker.record_db("kraken2_db", db_path="/data/kraken2_db", checksum=True)
    
    # Dans _finalizer:
    tracker.save(run_temp_dir)
    manifest["tool_versions"] = tracker.as_dict()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("genomeer.version_tracker")

import threading

# Cache global des versions (évite de re-lancer --version à chaque step)
_VERSION_CACHE: Dict[str, str] = {}
_VERSION_CACHE_LOCK = threading.RLock()
_DB_CHECKSUM_CACHE: Dict[str, str] = {}
_DB_CHECKSUM_CACHE_LOCK = threading.RLock()


@dataclass
class ToolVersion:
    """Version d'un outil CLI."""
    tool_name: str
    env_name: str
    version_string: str
    captured_at: float = field(default_factory=time.time)


@dataclass
class DBRecord:
    """Enregistrement d'une base de données utilisée."""
    db_name: str
    db_path: str
    checksum: Optional[str] = None       # MD5 du fichier principal
    size_gb: float = 0.0
    last_modified: Optional[str] = None
    detected_at: float = field(default_factory=time.time)


class VersionTracker:
    """
    Collecte les versions des outils et bases de données utilisés dans un run.
    """

    def __init__(self):
        self.tools: List[ToolVersion] = []
        self.databases: List[DBRecord] = []
        self._recorded_tools: set = set()
        self._recorded_dbs: set = set()

    def record_tool(self, tool_name: str, env_name: str) -> Optional[str]:
        """
        Enregistre la version d'un outil (via --version).
        Utilise le cache global pour éviter les appels répétés.

        Returns la version string ou None en cas d'échec.
        """
        key = f"{env_name}::{tool_name}"
        if key in self._recorded_tools:
            return None  # déjà enregistré

        self._recorded_tools.add(key)

        # Cache global
        with _VERSION_CACHE_LOCK:
            if key in _VERSION_CACHE:
                version = _VERSION_CACHE[key]
            else:
                version = self._get_tool_version(tool_name, env_name)
                _VERSION_CACHE[key] = version

        self.tools.append(ToolVersion(
            tool_name=tool_name,
            env_name=env_name,
            version_string=version,
        ))
        logger.debug(f"[VERSION] {tool_name} ({env_name}): {version}")
        return version

    def record_db(
        self,
        db_name: str,
        db_path: str,
        compute_checksum: bool = False,
    ) -> None:
        """
        Enregistre une base de données utilisée.
        Le calcul du checksum est effectué en arrière-plan (non-bloquant).
        """
        if db_name in self._recorded_dbs:
            return
        self._recorded_dbs.add(db_name)

        db_path_obj = Path(db_path)
        if not db_path_obj.exists():
            logger.warning(f"[VERSION] DB not found: {db_path}")
            return

        try:
            if db_path_obj.is_dir():
                size_gb = 0.0 
                last_modified = time.strftime("%Y-%m-%d", time.gmtime(db_path_obj.stat().st_mtime))
            else:
                size_gb = db_path_obj.stat().st_size / (1024 ** 3)
                last_modified = time.strftime("%Y-%m-%d", time.gmtime(db_path_obj.stat().st_mtime))
        except Exception:
            size_gb, last_modified = 0.0, "unknown"

        record = DBRecord(
            db_name=db_name,
            db_path=str(db_path),
            checksum="calculating...",
            size_gb=round(size_gb, 3),
            last_modified=last_modified,
        )
        self.databases.append(record)

        if compute_checksum and db_path_obj.is_file():
            import threading
            def _async_md5(rec: DBRecord, path: Path):
                cache_key = str(path)
                with _DB_CHECKSUM_CACHE_LOCK:
                    if cache_key in _DB_CHECKSUM_CACHE:
                        rec.checksum = _DB_CHECKSUM_CACHE[cache_key]
                    else:
                        c = self._md5_file(path)
                        _DB_CHECKSUM_CACHE[cache_key] = c
                        rec.checksum = c

            t = threading.Thread(target=_async_md5, args=(record, db_path_obj), daemon=True)
            t.start()
            if not hasattr(self, "_threads"): 
                self._threads = []
            if not hasattr(self, "_threads_lock"):
                self._threads_lock = threading.Lock()
            
            with self._threads_lock:
                self._threads.append(t)

        logger.info(f"[VERSION] DB={db_name} tracking started")

    def compute_db_checksums_async(self, output_dir: str) -> None:
        """
        Calcule les checksums de toutes les DBs suivies en arrière-plan
        et les écrit dans db_checksums.json sans bloquer le thread principal.
        """
        import threading
        
        def _run_checksums():
            results = {}
            for db in self.databases:
                path = Path(db.db_path)
                if not path.exists():
                    continue
                    
                logger.debug(f"[VERSION] Computing async checksum for {db.db_name}...")
                if path.is_file():
                    # MD5 for file
                    results[db.db_name] = self._md5_file(path)
                else:
                    # Quick fingerprint for directories
                    try:
                        st = path.stat()
                        results[db.db_name] = f"dir_mtime_{st.st_mtime}_size_{st.st_size}"
                    except Exception:
                        results[db.db_name] = "dir_access_error"
            
            out_path = Path(output_dir) / "db_checksums.json"
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2)
                logger.info(f"[VERSION] Async DB checksums saved → {out_path}")
            except Exception as e:
                logger.warning(f"[VERSION] Failed to save async checksums: {e}")

        t = threading.Thread(target=_run_checksums, daemon=True)
        t.start()

    def _md5_file(self, path: Path) -> str:
        # TÂCHE: Utiliser le helper statique partagé pour éviter la duplication
        return self._md5_file_static(path)

    def auto_record_from_step(
        self,
        step_title: str,
        pending_code: str,
        env_name: str = "meta-env1",
    ) -> None:
        """
        Détecte automatiquement les outils ET les bases de données utilisés dans un step.
        """
        TOOL_SIGNALS = {
            "fastp": "fastp",
            "kraken2": "kraken2",
            "bracken": "bracken",
            "metaspades": "metaspades.py",
            "megahit": "megahit",
            "flye": "flye",
            "minimap2": "minimap2",
            "metabat2": "metabat2",
            "checkm2": "checkm2",
            "prokka": "prokka",
            "prodigal": "prodigal",
            "diamond": "diamond",
            "hmmsearch": "hmmsearch",
            "humann": "humann",
            "amrfinder": "amrfinder",
            "rgi": "rgi",
            "gtdbtk": "gtdbtk",
            "metaphlan": "metaphlan",
        }
        code_lower = (pending_code or "").lower()
        
        # 1. Outils
        for signal, binary in TOOL_SIGNALS.items():
            if signal in code_lower:
                self.record_tool(binary, env_name)

        # 2. Bases de données (détection par regex)
        import re
        db_patterns = {
            "kraken2_db": r"kraken2.*--db\s+([^\s]+)",
            "bracken_db": r"bracken.*-d\s+([^\s]+)",
            "gtdbtk_db": r"GTDBTK_DATA_PATH=([^\s]+)",
            "amrfinder_db": r"amrfinder.*--database\s+([^\s]+)",
            "card_json": r"rgi.*--card_json\s+([^\s]+)",
        }
        for db_name, pattern in db_patterns.items():
            m = re.search(pattern, pending_code)
            if m:
                path = m.group(1).strip("'\"")
                self.record_db(db_name, path, compute_checksum=False)

    def wait_for_completion(self, timeout: float = 30.0):
        """Attend la fin des threads de checksum."""
        threads = []
        if hasattr(self, "_threads_lock"):
            with self._threads_lock:
                threads = list(self._threads)
        else:
            threads = getattr(self, "_threads", [])

        for t in threads:
            if t.is_alive():
                t.join(timeout=timeout)

    def as_dict(self) -> Dict[str, Any]:
        """Retourne un dict JSON-sérialisable pour le manifest."""
        return {
            "tools": [asdict(t) for t in self.tools],
            "databases": [asdict(d) for d in self.databases],
        }

    def save(self, output_dir: str) -> str:
        """Sauvegarde les versions dans genomeer_run_metadata.json."""
        out_path = Path(output_dir) / "genomeer_run_metadata.json"
        data = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool_versions": [asdict(t) for t in self.tools],
            "databases_used": [asdict(d) for d in self.databases],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"[VERSION] Metadata saved → {out_path}")
        return str(out_path)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_tool_version(tool_name: str, env_name: str) -> str:
        """Exécute <tool> --version dans l'environnement micromamba."""
        try:
            from genomeer.runtime.env_manager import ensure_micromamba, env_prefix
            mm_bin = str(ensure_micromamba())
            prefix = str(env_prefix(env_name))
            
            result = subprocess.run(
                [mm_bin, "run", "-p", prefix, tool_name, "--version"],
                capture_output=True, text=True, timeout=15,
            )
            output = (result.stdout + result.stderr).strip()
            lines = [l.strip() for l in output.splitlines() if l.strip()]
            return lines[0][:100] if lines else "unknown"
        except Exception as e:
            logger.debug(f"[VERSION] Could not get version for {tool_name}: {e}")
            return "unknown"

    @staticmethod
    def _md5_file_static(path: Path, chunk_size: int = 8192) -> str:
        """Calcule le MD5 d'un fichier par chunks."""
        import hashlib
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return "error"
