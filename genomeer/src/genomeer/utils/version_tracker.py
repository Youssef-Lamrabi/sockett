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

# Cache global des versions (évite de re-lancer --version à chaque step)
_VERSION_CACHE: Dict[str, str] = {}
_DB_CHECKSUM_CACHE: Dict[str, str] = {}


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

        Parameters
        ----------
        db_name          : Nom logique (ex: "kraken2_standard", "card_db")
        db_path          : Chemin vers la base
        compute_checksum : Si True, calcule le MD5 du fichier principal (lent!)
        """
        if db_name in self._recorded_dbs:
            return
        self._recorded_dbs.add(db_name)

        db_path_obj = Path(db_path)
        if not db_path_obj.exists():
            logger.warning(f"[VERSION] DB not found: {db_path}")
            return

        size_gb = 0.0
        last_modified = None
        checksum = None

        try:
            if db_path_obj.is_dir():
                # Optimisation: On évite rglob("*") qui bloque sur les énormes bases (ex: Kraken2)
                # On se base uniquement sur les stats du dossier parent
                size_gb = 0.0 # Skipping deep size calculation for performance
                last_modified = time.strftime(
                    "%Y-%m-%d", time.gmtime(db_path_obj.stat().st_mtime)
                )
            else:
                size_gb = db_path_obj.stat().st_size / (1024 ** 3)
                last_modified = time.strftime(
                    "%Y-%m-%d", time.gmtime(db_path_obj.stat().st_mtime)
                )
        except Exception as e:
            logger.warning(f"[VERSION] DB stat failed for {db_path}: {e}")

        # Checksum optionnel (MD5 du fichier principal seulement)
        if compute_checksum and db_path_obj.is_file():
            cache_key = str(db_path_obj)
            if cache_key in _DB_CHECKSUM_CACHE:
                checksum = _DB_CHECKSUM_CACHE[cache_key]
            else:
                checksum = self._md5_file(db_path_obj)
                _DB_CHECKSUM_CACHE[cache_key] = checksum

        self.databases.append(DBRecord(
            db_name=db_name,
            db_path=str(db_path),
            checksum=checksum,
            size_gb=round(size_gb, 3),
            last_modified=last_modified,
        ))
        logger.info(f"[VERSION] DB={db_name} path={db_path} size={size_gb:.2f}GB mod={last_modified}")

    def _md5_file(self, path: Path) -> str:
        import hashlib
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def auto_record_from_step(
        self,
        step_title: str,
        pending_code: str,
        env_name: str = "meta-env1",
    ) -> None:
        """
        Détecte automatiquement les outils utilisés dans un step
        et enregistreurs versions.
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
        code_lower = pending_code.lower()
        for signal, binary in TOOL_SIGNALS.items():
            if signal in code_lower:
                self.record_tool(binary, env_name)

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
            from genomeer.runtime.env_manager import ENVS_DIR
            mm_bin = "micromamba"
            result = subprocess.run(
                [mm_bin, "run", "-n", env_name, tool_name, "--version"],
                capture_output=True, text=True, timeout=15,
            )
            output = (result.stdout + result.stderr).strip()
            lines = [l.strip() for l in output.splitlines() if l.strip()]
            return lines[0][:100] if lines else "unknown"
        except Exception as e:
            logger.debug(f"[VERSION] Could not get version for {tool_name}: {e}")
            return "unknown"

    @staticmethod
    def _md5_file(path: Path, chunk_size: int = 8192) -> str:
        """Calcule le MD5 d'un fichier par chunks."""
        h = hashlib.md5()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return "error"
