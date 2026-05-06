"""
genomeer/src/genomeer/utils/cache.py
======================================
Cache multi-couche pour Genomeer — inspiré de Biomni et des agents LLM compétitifs.

POURQUOI C'EST CRITIQUE:
  Un pipeline métagénomique complet génère:
  - 15-40 appels LLM (chaque nœud × chaque étape)
  - N appels API externes (NCBI, KEGG, CARD) répétés sur des données identiques
  - Des exécutions d'outils lourds (Kraken2, CheckM2) rejouées sur retry

  Sans cache:
  - Coût LLM x3-5 par re-run (même données, même code généré)
  - Temps d'exécution x2 sur retry (outil re-lancé sur les mêmes reads)
  - Rate-limiting NCBI/KEGG sur requêtes répétées

ARCHITECTURE (3 couches, indépendantes):

  Layer 1 — LLMResponseCache (SQLite)
    Clé: hash(system_prompt + user_message + model_name)
    TTL: 24h (prompts techniques stables)
    Hit rate attendu: 30-60% sur re-runs du même pipeline
    → Économie: ~$0.05-0.20 par run sur GPT-4

  Layer 2 — ToolOutputCache (disk JSON)
    Clé: hash(tool_name + input_files_hash + params_hash)
    TTL: configurable par outil (fastp 7j, Kraken2 30j si même DB)
    Hit rate attendu: 80%+ sur retry (même reads, même outil)
    → Économie: 20-40 min par re-run sur un pipeline complet

  Layer 3 — APIResponseCache (SQLite avec TTL)
    Clé: hash(url + params)
    TTL: 7j pour NCBI/KEGG (données stables), 1j pour CARD (mises à jour fréquentes)
    → Évite le rate-limiting Entrez (3 req/s sans API key)

USAGE:
    from genomeer.utils.cache import get_cache

    cache = get_cache()  # singleton, initialisé depuis les env vars

    # LLM cache (dans _llm_invoke de BioAgent)
    cached = cache.llm.get(prompt_hash)
    if cached:
        return cached
    resp = self.llm.invoke(msgs)
    cache.llm.set(prompt_hash, resp.content)

    # Tool cache (dans _executor de BioAgent)
    tool_key = cache.tool.make_key("run_kraken2", input_files, params)
    cached_result = cache.tool.get(tool_key)
    if cached_result:
        return cached_result   # skip execution entirely

    # API cache (dans bio_rag.py et wrappers NCBI)
    @cache.api.cached(ttl_seconds=7*86400)
    def fetch_kegg_pathway(pathway_id):
        return requests.get(f"https://rest.kegg.jp/get/{pathway_id}").text
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("genomeer.cache")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_CACHE_DIR = os.environ.get(
    "GENOMEER_CACHE_DIR",
    str(Path.home() / ".genomeer" / "cache"),
)

# TTL par défaut par couche (secondes)
_LLM_TTL    = int(os.environ.get("GENOMEER_LLM_CACHE_TTL",    str(24 * 3600)))      # 24h
_TOOL_TTL   = int(os.environ.get("GENOMEER_TOOL_CACHE_TTL",   str(7  * 24 * 3600))) # 7j
_API_TTL    = int(os.environ.get("GENOMEER_API_CACHE_TTL",    str(7  * 24 * 3600))) # 7j

# Désactiver complètement le cache (debug)
_CACHE_DISABLED = os.environ.get("GENOMEER_CACHE_DISABLED", "0").strip() in ("1", "true", "True")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(*parts: str) -> str:
    """SHA-256 tronqué à 16 chars — lisible, collision-résistant pour notre usage."""
    combined = "\x00".join(str(p) for p in parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _hash_file(path: str) -> str:
    """Hash d'un fichier par (taille, mtime) — pas de lecture complète pour les gros fichiers."""
    try:
        st = os.stat(path)
        return _hash(str(st.st_size), str(st.st_mtime), path)
    except OSError:
        return _hash(path)


def _hash_files(paths: list[str]) -> str:
    """Hash combiné de plusieurs fichiers d'entrée."""
    return _hash(*[_hash_file(p) for p in sorted(paths)])


def _open_sqlite_conn(db_path: str) -> sqlite3.Connection:
    """Ouvre une connexion SQLite avec WAL et timeout pour éviter les verrous en mode batch."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------------------------------------------------------------------------
# Layer 1 — LLMResponseCache
# ---------------------------------------------------------------------------

class LLMResponseCache:
    """
    Cache SQLite pour les réponses LLM.
    Clé = hash(model + system_prompt + user_message).
    Ne cache PAS les réponses des nœuds non-déterministes (planner initial).
    """

    # Nœuds dont les réponses NE doivent PAS être cachées
    _SKIP_NODES = {"planner"}

    def __init__(self, db_path: str, ttl: int = _LLM_TTL):
        self.db_path = db_path
        self.ttl = ttl
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()

    def _conn_get(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _open_sqlite_conn(self.db_path)
        return self._conn

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._conn_get()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                key       TEXT PRIMARY KEY,
                value     TEXT NOT NULL,
                model     TEXT,
                node      TEXT,
                created   REAL NOT NULL,
                hits      INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_created ON llm_cache(created)")
        conn.commit()

    def make_key(self, model: str, system_prompt: str, user_message: str) -> str:
        # FIX B7: hash the FULL content — truncating to 2000 chars caused hash collisions
        # when two prompts differed only after char 2000.
        import hashlib as _hl
        combined = "\x00".join([model, system_prompt, user_message])
        return _hl.sha256(combined.encode("utf-8", errors="replace")).hexdigest()[:32]

    def get(self, key: str, node: str = "") -> Optional[str]:
        # T5.1: Added node parameter to prevent cross-node cache leakage
        if _CACHE_DISABLED:
            return None
        try:
            conn = self._conn_get()
            with self._lock:
                row = conn.execute(
                    "SELECT value, created, node FROM llm_cache WHERE key=?", (key,)
                ).fetchone()
            if row is None:
                return None
            value, created, cached_node = row
            # If node was provided, ensure it matches the cached node
            if node and cached_node and node != cached_node:
                logger.debug(f"[LLMCache] Miss node mismatch (wanted {node}, got {cached_node}) for key={key[:8]}")
                return None

            if time.time() - created > self.ttl:
                with self._lock:
                    conn.execute("DELETE FROM llm_cache WHERE key=?", (key,))
                    conn.commit()
                return None
            # Update hit count
            with self._lock:
                conn.execute("UPDATE llm_cache SET hits=hits+1 WHERE key=?", (key,))
                conn.commit()
            logger.debug(f"[LLMCache] HIT key={key[:8]}")
            return value
        except Exception as e:
            logger.warning(f"[LLMCache] get failed: {e}")
            return None

    def set(self, key: str, value: str, model: str = "", node: str = ""):
        if _CACHE_DISABLED:
            return
        if node in self._SKIP_NODES:
            return   # Ne pas cacher ces nœuds
        try:
            with self._lock:
                conn = self._conn_get()
                conn.execute(
                    "INSERT OR REPLACE INTO llm_cache(key,value,model,node,created) VALUES(?,?,?,?,?)",
                    (key, value, model, node, time.time()),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"[LLMCache] set failed: {e}")

    def invalidate(self, key: str):
        try:
            with self._lock:
                conn = self._conn_get()
                conn.execute("DELETE FROM llm_cache WHERE key=?", (key,))
                conn.commit()
        except Exception:
            pass

    def invalidate_node(self, node: str):
        # T5.4: Utility to invalidate all cached responses for a specific node
        try:
            with self._lock:
                conn = self._conn_get()
                deleted = conn.execute("DELETE FROM llm_cache WHERE node=?", (node,)).rowcount
                conn.commit()
            if deleted:
                logger.info(f"[LLMCache] Invalidate: purged {deleted} entries for node '{node}'")
        except Exception as e:
            logger.warning(f"[LLMCache] invalidate_node failed: {e}")

    def stats(self) -> dict:
        try:
            conn = self._conn_get()
            rows = conn.execute(
                "SELECT COUNT(*), SUM(hits), AVG(hits) FROM llm_cache"
            ).fetchone()
            return {"entries": rows[0], "total_hits": rows[1] or 0, "avg_hits": round(rows[2] or 0, 2)}
        except Exception:
            return {}

    def purge_expired(self):
        try:
            with self._lock:
                conn = self._conn_get()
                cutoff = time.time() - self.ttl
                deleted = conn.execute(
                    "DELETE FROM llm_cache WHERE created < ?", (cutoff,)
                ).rowcount
                conn.commit()
            if deleted:
                logger.info(f"[LLMCache] Purged {deleted} expired entries")
        except Exception as e:
            logger.warning(f"[LLMCache] purge failed: {e}")


# ---------------------------------------------------------------------------
# Layer 2 — ToolOutputCache
# ---------------------------------------------------------------------------

class ToolOutputCache:
    """
    Cache disque pour les outputs d'outils bioinformatiques lourds.

    Clé = hash(tool_name + input_files_hash + params).
    Value = dict de résultats + chemins d'output copiés dans le cache dir.

    Logique de hit:
      Si le cache hit, on copie les fichiers de sortie dans le run_temp_dir
      actuel et on retourne le dict de résultats comme si l'outil venait de tourner.
      L'agent ne voit aucune différence.
    """

    # TTL par outil (secondes) — surcharge le TTL global
    TOOL_TTL_OVERRIDES = {
        "run_fastp":         7  * 24 * 3600,   # 7j — outputs déterministes
        "run_kraken2":       30 * 24 * 3600,   # 30j — si même DB version
        "run_metaphlan4":    30 * 24 * 3600,
        "run_metaspades":    14 * 24 * 3600,   # 14j — assemblage
        "run_megahit":       14 * 24 * 3600,
        "run_flye":          14 * 24 * 3600,
        "run_metabat2":      7  * 24 * 3600,
        "run_checkm2":       7  * 24 * 3600,
        "run_prokka":        14 * 24 * 3600,
        "run_diamond":       14 * 24 * 3600,
        "run_amrfinderplus": 7  * 24 * 3600,
        "run_humann3":       14 * 24 * 3600,
        "download_from_ncbi": 90 * 24 * 3600,  # 90j — données NCBI stables
    }

    def __init__(self, cache_dir: str, ttl: int = _TOOL_TTL):
        self.cache_dir = Path(cache_dir) / "tools"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = ttl
        self._meta_db = str(self.cache_dir / "tool_meta.db")
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = _open_sqlite_conn(self._meta_db)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_cache (
                    key        TEXT PRIMARY KEY,
                    tool_name  TEXT,
                    result_json TEXT NOT NULL,
                    output_dir TEXT,
                    created    REAL NOT NULL,
                    hits       INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()

    def make_key(
        self,
        tool_name: str,
        input_files: list[str],
        params: Optional[dict] = None,
        db_version: Optional[str] = None,
    ) -> str:
        """
        Construit la clé de cache pour un appel d'outil.

        Parameters
        ----------
        tool_name   : nom de la fonction wrapper (ex: "run_kraken2")
        input_files : liste des chemins de fichiers d'entrée
        params      : dict des paramètres de l'outil (threads exclus)
        db_version  : version de la base de données si applicable
        """
        # Exclure les params non-déterministes
        _EXCLUDE_PARAMS = {"threads", "output_dir", "tmp_dir", "verbose", "quiet"}
        clean_params = {
            k: v for k, v in (params or {}).items()
            if k not in _EXCLUDE_PARAMS
        }
        files_hash = _hash_files([f for f in input_files if f and os.path.exists(f)])
        params_str = json.dumps(clean_params, sort_keys=True)
        return _hash(tool_name, files_hash, params_str, db_version or "")

    def get(
        self,
        key: str,
        output_dir: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Retourne le dict de résultats si cache hit.
        Si output_dir fourni, copie les fichiers de sortie dans ce dossier.
        """
        if _CACHE_DISABLED:
            return None
        try:
            with self._lock:
                conn = _open_sqlite_conn(self._meta_db)
                row = conn.execute(
                    "SELECT result_json, output_dir, created, tool_name FROM tool_cache WHERE key=?",
                    (key,)
                ).fetchone()
                conn.close()

            if row is None:
                return None

            result_json, cached_output_dir, created, tool_name = row
            ttl = self.TOOL_TTL_OVERRIDES.get(tool_name, self.default_ttl)

            if time.time() - created > ttl:
                self._delete(key)
                return None

            result = json.loads(result_json)

            # T9.2/9.3: Vérification stricte des fichiers de sortie
            _verify_files = os.environ.get("GENOMEER_TOOL_CACHE_VERIFY_FILES", "1").strip() in ("1", "true", "True")
            if _verify_files:
                expected_files = result.get("__cached_files__", [])
                if cached_output_dir:
                    missing_files = [f for f in expected_files if not (Path(cached_output_dir) / f).exists()]
                else:
                    missing_files = []
                if missing_files:
                    logger.info(f"[ToolCache] MISS key={key[:8]} — {len(missing_files)} missing files (e.g. {missing_files[0]})")
                    self._delete(key)
                    return None

            # TÂCHE 5.2: Vérification de l'existence physique du dossier de cache
            if cached_output_dir:
                if not Path(cached_output_dir).exists():
                    logger.info(f"[ToolCache] MISS key={key[:8]} — cache.miss.stale_dir (path={cached_output_dir} no longer exists)")
                    self._delete(key)
                    return None
                
                # Copier les fichiers de sortie si output_dir fourni
                if output_dir:
                    self._restore_outputs(cached_output_dir, output_dir, result)

            # Update hits
            with self._lock:
                conn2 = _open_sqlite_conn(self._meta_db)
                conn2.execute("UPDATE tool_cache SET hits=hits+1 WHERE key=?", (key,))
                conn2.commit()
                conn2.close()

            logger.info(f"[ToolCache] HIT {tool_name} key={key[:8]}")
            return result

        except Exception as e:
            logger.warning(f"[ToolCache] get failed: {e}")
            return None

    def set(
        self,
        key: str,
        tool_name: str,
        result: dict,
        output_dir: Optional[str] = None,
    ):
        """
        Sauvegarde le résultat d'un outil.
        Si output_dir fourni, copie les fichiers de sortie dans le cache.
        """
        if _CACHE_DISABLED:
            return
        try:
            # T9.1: Enregistrement des chemins relatifs des fichiers présents
            produced_files = []
            if output_dir and Path(output_dir).exists():
                produced_files = [str(p.relative_to(Path(output_dir))) for p in Path(output_dir).rglob("*") if p.is_file()]
            result["__cached_files__"] = produced_files

            # Copier les outputs dans le cache
            cached_output_dir = None
            if output_dir and Path(output_dir).exists():
                cached_output_dir = str(self.cache_dir / f"outputs_{key}")
                success = self._archive_outputs(output_dir, cached_output_dir)
                if not success:
                    logger.warning(f"[ToolCache] Archival failed for key={key[:8]}. Skipping cache entry.")
                    return

            with self._lock:
                conn = _open_sqlite_conn(self._meta_db)
                conn.execute(
                    """INSERT OR REPLACE INTO tool_cache
                       (key, tool_name, result_json, output_dir, created)
                       VALUES (?,?,?,?,?)""",
                    (key, tool_name, json.dumps(result, default=str), cached_output_dir, time.time()),
                )
                conn.commit()
                conn.close()
            logger.info(f"[ToolCache] SET {tool_name} key={key[:8]}")
        except Exception as e:
            logger.warning(f"[ToolCache] set failed: {e}")

    def _archive_outputs(self, src_dir: str, dst_dir: str):
        """Copie les fichiers de sortie dans le cache (avec limite de taille)."""
        import shutil
        MAX_SIZE_GB = float(os.environ.get("GENOMEER_TOOL_CACHE_MAX_GB", "5"))
        src = Path(src_dir)
        dst = Path(dst_dir)
        dst.mkdir(parents=True, exist_ok=True)

        total_size = 0
        try:
            for f in src.rglob("*"):
                if f.is_file():
                    size_gb = f.stat().st_size / (1024**3)
                    if total_size + size_gb > MAX_SIZE_GB:
                        logger.warning(f"[ToolCache] Output too large to cache ({total_size:.1f}GB), skipping files")
                        break
                    rel = f.relative_to(src)
                    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(f), str(dst / rel))
                    total_size += size_gb
            return True
        except Exception as e:
            logger.error(f"[ToolCache] Archival error: {e}")
            if dst.exists():
                shutil.rmtree(str(dst), ignore_errors=True)
            return False

    def _restore_outputs(self, cached_dir: str, target_dir: str, result: dict):
        """Copie les fichiers cachés vers le target_dir du run actuel."""
        import shutil
        src = Path(cached_dir)
        tgt = Path(target_dir)
        tgt.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src)
                dest = tgt / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(f), str(dest))

        # Mettre à jour les chemins dans le result dict
        self._remap_paths(result, str(cached_dir), str(target_dir))

    def _remap_paths(self, obj: Any, old_prefix: str, new_prefix: str):
        """Remplace récursivement les chemins dans un dict de résultats."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v.startswith(old_prefix):
                    obj[k] = v.replace(old_prefix, new_prefix, 1)
                elif isinstance(v, (dict, list)):
                    self._remap_paths(v, old_prefix, new_prefix)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item.startswith(old_prefix):
                    obj[i] = item.replace(old_prefix, new_prefix, 1)
                elif isinstance(item, (dict, list)):
                    self._remap_paths(item, old_prefix, new_prefix)

    def _delete(self, key: str):
        try:
            with self._lock:
                conn = _open_sqlite_conn(self._meta_db)
                conn.execute("DELETE FROM tool_cache WHERE key=?", (key,))
                conn.commit()
                conn.close()
        except Exception:
            pass

    def stats(self) -> dict:
        try:
            conn = _open_sqlite_conn(self._meta_db)
            rows = conn.execute(
                "SELECT tool_name, COUNT(*), SUM(hits) FROM tool_cache GROUP BY tool_name"
            ).fetchall()
            conn.close()
            return {r[0]: {"entries": r[1], "hits": r[2] or 0} for r in rows}
        except Exception:
            return {}

    def purge_expired(self):
        try:
            conn = _open_sqlite_conn(self._meta_db)
            all_rows = conn.execute(
                "SELECT key, tool_name, created FROM tool_cache"
            ).fetchall()
            now = time.time()
            to_delete = []
            for key, tool_name, created in all_rows:
                ttl = self.TOOL_TTL_OVERRIDES.get(tool_name, self.default_ttl)
                if now - created > ttl:
                    to_delete.append(key)
            if to_delete:
                with self._lock:
                    conn.executemany("DELETE FROM tool_cache WHERE key=?", [(k,) for k in to_delete])
                    conn.commit()
                logger.info(f"[ToolCache] Purged {len(to_delete)} expired tool outputs")
            conn.close()
        except Exception as e:
            logger.warning(f"[ToolCache] purge failed: {e}")


# ---------------------------------------------------------------------------
# Layer 3 — APIResponseCache
# ---------------------------------------------------------------------------

class APIResponseCache:
    """
    Cache SQLite pour les réponses d'API externes (NCBI, KEGG, CARD, UniProt).
    TTL différencié par domaine.
    Supporte un décorateur @cached pour wrapper n'importe quelle fonction.
    """

    DOMAIN_TTL = {
        "eutils.ncbi.nlm.nih.gov": 7  * 24 * 3600,
        "rest.kegg.jp":             14 * 24 * 3600,
        "card.mcmaster.ca":         3  * 24 * 3600,
        "rest.uniprot.org":         14 * 24 * 3600,
        "www.ebi.ac.uk":            7  * 24 * 3600,
        "gtdb.ecogenomic.org":      30 * 24 * 3600,
    }

    def __init__(self, db_path: str, default_ttl: int = _API_TTL):
        self.db_path = db_path
        self.default_ttl = default_ttl
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = _open_sqlite_conn(self.db_path)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                key      TEXT PRIMARY KEY,
                url      TEXT,
                value    BLOB NOT NULL,
                created  REAL NOT NULL,
                hits     INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_url ON api_cache(url)")
        conn.commit()
        conn.close()

    def _get_ttl(self, url: str) -> int:
        for domain, ttl in self.DOMAIN_TTL.items():
            if domain in url:
                return ttl
        return self.default_ttl

    def make_key(self, url: str, params: Optional[dict] = None) -> str:
        return _hash(url, json.dumps(params or {}, sort_keys=True))

    def get(self, url: str, params: Optional[dict] = None) -> Optional[Any]:
        if _CACHE_DISABLED:
            return None
        key = self.make_key(url, params)
        try:
            with self._lock:
                conn = _open_sqlite_conn(self.db_path)
                row = conn.execute(
                    "SELECT value, created FROM api_cache WHERE key=?", (key,)
                ).fetchone()
                conn.close()
            if row is None:
                return None
            value_bytes, created = row
            ttl = self._get_ttl(url)
            if time.time() - created > ttl:
                self._delete(key)
                return None
            with self._lock:
                conn2 = _open_sqlite_conn(self.db_path)
                conn2.execute("UPDATE api_cache SET hits=hits+1 WHERE key=?", (key,))
                conn2.commit()
                conn2.close()
            return pickle.loads(value_bytes)
        except Exception as e:
            logger.warning(f"[APICache] get failed: {e}")
            return None

    def set(self, url: str, value: Any, params: Optional[dict] = None):
        if _CACHE_DISABLED:
            return
        key = self.make_key(url, params)
        try:
            with self._lock:
                conn = _open_sqlite_conn(self.db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO api_cache(key,url,value,created) VALUES(?,?,?,?)",
                    (key, url, pickle.dumps(value), time.time()),
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"[APICache] set failed: {e}")

    def cached(self, ttl_seconds: Optional[int] = None):
        """
        Décorateur pour cacher automatiquement une fonction qui appelle une API.

        Usage:
            @cache.api.cached(ttl_seconds=7*86400)
            def fetch_kegg(pathway_id: str) -> str:
                return requests.get(f"https://rest.kegg.jp/get/{pathway_id}").text
        """
        def decorator(fn: Callable) -> Callable:
            import functools

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                # Construire une clé depuis le nom de fonction + args
                key = _hash(fn.__name__, str(args), json.dumps(kwargs, sort_keys=True, default=str))

                if not _CACHE_DISABLED:
                    try:
                        with self._lock:
                            conn = _open_sqlite_conn(self.db_path)
                            row = conn.execute(
                                "SELECT value, created FROM api_cache WHERE key=?", (key,)
                            ).fetchone()
                            conn.close()
                        if row:
                            value_bytes, created = row
                            effective_ttl = ttl_seconds or self.default_ttl
                            if time.time() - created <= effective_ttl:
                                with self._lock:
                                    conn2 = _open_sqlite_conn(self.db_path)
                                    conn2.execute("UPDATE api_cache SET hits=hits+1 WHERE key=?", (key,))
                                    conn2.commit()
                                    conn2.close()
                                logger.debug(f"[APICache] HIT {fn.__name__}")
                                return pickle.loads(value_bytes)
                    except Exception:
                        pass

                result = fn(*args, **kwargs)

                try:
                    with self._lock:
                        conn = _open_sqlite_conn(self.db_path)
                        conn.execute(
                            "INSERT OR REPLACE INTO api_cache(key,url,value,created) VALUES(?,?,?,?)",
                            (key, fn.__name__, pickle.dumps(result), time.time()),
                        )
                        conn.commit()
                        conn.close()
                except Exception:
                    pass

                return result
            return wrapper
        return decorator

    def _delete(self, key: str):
        try:
            with self._lock:
                conn = _open_sqlite_conn(self.db_path)
                conn.execute("DELETE FROM api_cache WHERE key=?", (key,))
                conn.commit()
                conn.close()
        except Exception:
            pass

    def purge_expired(self):
        try:
            with self._lock:
                conn = _open_sqlite_conn(self.db_path)
                deleted = conn.execute(
                    "DELETE FROM api_cache WHERE created < ?",
                    (time.time() - self.default_ttl,)
                ).rowcount
                conn.commit()
                conn.close()
            if deleted:
                logger.info(f"[APICache] Purged {deleted} expired entries")
        except Exception as e:
            logger.warning(f"[APICache] purge failed: {e}")


# ---------------------------------------------------------------------------
# GenoCache — façade singleton
# ---------------------------------------------------------------------------

class GenoCache:
    """
    Façade singleton pour les 3 couches de cache.
    Toujours accéder via get_cache() pour partager l'instance.
    """

    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.llm  = LLMResponseCache(str(self.cache_dir / "llm_cache.db"))
        self.tool = ToolOutputCache(str(self.cache_dir))
        self.api  = APIResponseCache(str(self.cache_dir / "api_cache.db"))

        # Purge auto au démarrage (non-bloquant)
        import threading
        threading.Thread(target=self._purge_all, daemon=True).start()

    def _purge_all(self):
        try:
            self.llm.purge_expired()
            self.tool.purge_expired()
            self.api.purge_expired()
        except Exception:
            pass

    def stats(self) -> dict:
        return {
            "llm": self.llm.stats(),
            "tools": self.tool.stats(),
            "cache_dir": str(self.cache_dir),
            "disabled": _CACHE_DISABLED,
        }

    def clear_all(self):
        """Vider tout le cache — utile pour les tests."""
        import shutil
        shutil.rmtree(str(self.cache_dir), ignore_errors=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.llm  = LLMResponseCache(str(self.cache_dir / "llm_cache.db"))
        self.tool = ToolOutputCache(str(self.cache_dir))
        self.api  = APIResponseCache(str(self.cache_dir / "api_cache.db"))


# Singleton global et son lock (Fix A1: thread-safe singleton)
_CACHE_INSTANCE: Optional[GenoCache] = None
_CACHE_LOCK = threading.Lock()

def get_cache(cache_dir: Optional[str] = None) -> GenoCache:
    """Retourne le singleton GenoCache. Thread-safe."""
    global _CACHE_INSTANCE
    with _CACHE_LOCK:
        if _CACHE_INSTANCE is None:
            _CACHE_INSTANCE = GenoCache(cache_dir or _DEFAULT_CACHE_DIR)
        return _CACHE_INSTANCE