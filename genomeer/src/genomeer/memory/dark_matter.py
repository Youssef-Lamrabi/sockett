"""
genomeer/memory/dark_matter.py
==============================
Cross-sample BIOLOGICAL memory for microbial "dark matter" (unknown / hypothetical
proteins). Unlike runs_memory / failure_memory (which are OPERATIONAL — they make the
agent orchestrate better), this store accumulates BIOLOGICAL evidence about recurring
families of unannotated proteins across many samples, so that a gene which eggNOG/DRAM
call "hypothetical_protein" can eventually get an evidence-backed functional hypothesis
that no single sample could produce.

Design (see also the discussion notes):
- The UNIT of memory is a CLUSTER (a family of similar unknown proteins), NOT a gene.
- Evidence accumulates against a stable cluster_id run after run.
- ANTI-POLLUTION rule (critical): recurrence is counted over DISTINCT DATASETS
  (dataset_hash), never over runs. Re-running the SAME test data 50× keeps n_datasets=1.
- BOUNDED storage: raw observations are capped per cluster (audit exemplars only);
  the accumulated knowledge lives in stored counters/JSON on the cluster row, so the
  DB grows with the number of protein FAMILIES (sub-linear), not with runs.
- 100% deterministic Python + SQLite; NO LLM is involved here. The LLM only ever sees
  a bounded top-K slice via lookup() — so growing memory never grows the LLM context.

Storage: ~/.genomeer/dark_matter/memory.db  (override: GENOMEER_DARKMATTER_STORE)

Usage:
    from genomeer.memory.dark_matter import DarkMatterMemory
    mem = DarkMatterMemory()
    # WRITE (finalizer, after annotation): unknowns = list of dicts, see record_run()
    mem.record_run(dataset_hash="sha256hex", accession="SRR...", sample_type="human_gut",
                   run_id="run-44", unknowns=[{...}, ...])
    # READ (annotation/finalizer): bounded top-K for the clusters seen this run
    block = mem.lookup(cluster_ids=[...], k=10)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_STORE = Path.home() / ".genomeer" / "dark_matter" / "memory.db"

# k-mer size + Jaccard threshold for the pure-Python fallback clustering (used when
# mmseqs is not available). Two unknown proteins whose 5-mer sets overlap >= 0.5 are
# considered the same family. mmseqs is the scale path (documented seam in _assign_one).
_KMER = 5
_JACCARD_MIN = 0.5

# Hypothesis-confidence weights (deterministic, tunable). Documented so the score is
# never a black box: operon-adjacency signal + cross-dataset recurrence + structure.
_W_NEIGHBOR = 0.4
_W_BREADTH = 0.3
_W_STRUCT = 0.3
_BREADTH_SATURATION = 10        # n_datasets at which the recurrence term maxes out
_HYPOTHESIS_MIN_CONF = 0.6      # below this a cluster stays 'unresolved'
_MAX_OBS_PER_CLUSTER = 20       # audit exemplars kept; aggregates are counters, not these


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kmers(seq: str) -> set:
    s = (seq or "").upper().strip()
    if len(s) < _KMER:
        return {s} if s else set()
    return {s[i:i + _KMER] for i in range(len(s) - _KMER + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


class DarkMatterMemory:
    """SQLite-backed cross-sample memory for unknown protein families."""

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = Path(
            store_path or os.getenv("GENOMEER_DARKMATTER_STORE", str(_DEFAULT_STORE))
        )
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------ DB setup
    def _connect(self) -> sqlite3.Connection:
        # Fresh connection per operation → thread-safe without sharing handles.
        con = sqlite3.connect(str(self.store_path), timeout=30.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_db(self) -> None:
        con = self._connect()
        try:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_hash TEXT PRIMARY KEY,
                    accession    TEXT,
                    sample_type  TEXT,
                    first_seen   TEXT,
                    n_runs       INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS clusters (
                    cluster_id             TEXT PRIMARY KEY,
                    representative_seq     TEXT,
                    rep_len                INTEGER,
                    n_observations         INTEGER DEFAULT 0,
                    n_datasets             INTEGER DEFAULT 0,
                    neighbor_counts_json   TEXT DEFAULT '{}',
                    taxa_counts_json       TEXT DEFAULT '{}',
                    sample_type_counts_json TEXT DEFAULT '{}',
                    best_struct_target     TEXT,
                    best_struct_tmscore    REAL DEFAULT 0,
                    hypothesis_text        TEXT,
                    hypothesis_confidence  REAL DEFAULT 0,
                    hypothesis_evidence_json TEXT DEFAULT '[]',
                    hypothesis_status      TEXT DEFAULT 'unresolved',
                    created                TEXT,
                    updated                TEXT
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    cluster_id     TEXT,
                    dataset_hash   TEXT,
                    gene_id        TEXT,
                    run_id         TEXT,
                    timestamp      TEXT,
                    bin_taxon      TEXT,
                    seq_len        INTEGER,
                    neighbors_json TEXT,
                    signal_peptide INTEGER DEFAULT 0,
                    tm_domains     INTEGER DEFAULT 0,
                    struct_computed INTEGER DEFAULT 0
                );
                -- Distinct (cluster, dataset) membership. NEVER pruned → the accurate
                -- source of n_datasets even after observations are capped.
                CREATE TABLE IF NOT EXISTS cluster_datasets (
                    cluster_id   TEXT,
                    dataset_hash TEXT,
                    PRIMARY KEY (cluster_id, dataset_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_obs_cluster ON observations(cluster_id);
                CREATE INDEX IF NOT EXISTS idx_clu_conf ON clusters(hypothesis_confidence DESC);
                """
            )
            con.commit()
        finally:
            con.close()

    # ------------------------------------------------------------------ clustering
    def _load_reps(self, con: sqlite3.Connection) -> List[Tuple[str, set]]:
        """(cluster_id, kmer_set) for every existing cluster representative."""
        rows = con.execute("SELECT cluster_id, representative_seq FROM clusters").fetchall()
        return [(cid, _kmers(seq)) for cid, seq in rows]

    def _next_cluster_id(self, con: sqlite3.Connection) -> str:
        n = con.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        return f"DKM_{n + 1:06d}"

    def _assign_one(self, con: sqlite3.Connection, reps: List[Tuple[str, set]],
                    seq: str) -> Tuple[str, bool]:
        """Assign a sequence to an existing cluster (best k-mer Jaccard >= threshold)
        or create a new one. Returns (cluster_id, is_new).

        SCALE SEAM: for large stores, replace this k-mer fallback with an mmseqs
        `easy-search` of the new sequence against representatives.faa — same contract
        (return the matched cluster_id or None). The fallback is stdlib-only and needs
        no external binary, so it is the safe default for local/partial installs.
        """
        qk = _kmers(seq)
        best_cid, best_j = None, 0.0
        for cid, rk in reps:
            j = _jaccard(qk, rk)
            if j > best_j:
                best_cid, best_j = cid, j
        if best_cid is not None and best_j >= _JACCARD_MIN:
            return best_cid, False
        # new cluster
        cid = self._next_cluster_id(con)
        con.execute(
            "INSERT INTO clusters (cluster_id, representative_seq, rep_len, created, updated) "
            "VALUES (?,?,?,?,?)",
            (cid, seq, len(seq or ""), _utcnow(), _utcnow()),
        )
        reps.append((cid, qk))  # so later unknowns in THIS run can join it too
        return cid, True

    # ------------------------------------------------------------------ WRITE
    def record_run(
        self,
        dataset_hash: str,
        run_id: str,
        unknowns: List[Dict[str, Any]],
        accession: str = "",
        sample_type: str = "unknown",
    ) -> List[str]:
        """Record all unknown proteins of one run.

        unknowns: list of dicts, each:
            {
              "gene_id": str, "seq": str (aa),           # required
              "bin_taxon": str,                           # gtdbtk classification of the bin
              "neighbors": [str, ...],                    # KNOWN neighbor gene products (operon)
              "signal_peptide": bool, "tm_domains": int,  # cheap biophysics
              "struct": {"target": str, "tm_score": float} | None   # optional (Tier 1)
            }
        Returns the list of cluster_ids touched (for an immediate lookup() if wanted).
        The whole call is serialized (aggregates are read-modify-write).
        """
        if not unknowns:
            return []
        touched: List[str] = []
        now = _utcnow()
        with self._lock:
            con = self._connect()
            try:
                # dataset upsert (n_runs++ if the SAME data is re-run — transparency only)
                row = con.execute(
                    "SELECT dataset_hash FROM datasets WHERE dataset_hash=?", (dataset_hash,)
                ).fetchone()
                if row:
                    con.execute(
                        "UPDATE datasets SET n_runs = n_runs + 1 WHERE dataset_hash=?",
                        (dataset_hash,),
                    )
                else:
                    con.execute(
                        "INSERT INTO datasets (dataset_hash, accession, sample_type, first_seen, n_runs)"
                        " VALUES (?,?,?,?,1)",
                        (dataset_hash, accession, sample_type, now),
                    )

                reps = self._load_reps(con)
                affected: set = set()
                for u in unknowns:
                    seq = u.get("seq") or ""
                    if not seq:
                        continue
                    cid, _is_new = self._assign_one(con, reps, seq)
                    affected.add(cid)
                    # distinct (cluster, dataset) membership (idempotent → anti-pollution)
                    con.execute(
                        "INSERT OR IGNORE INTO cluster_datasets (cluster_id, dataset_hash) VALUES (?,?)",
                        (cid, dataset_hash),
                    )
                    # append observation (capped later)
                    con.execute(
                        "INSERT INTO observations (cluster_id, dataset_hash, gene_id, run_id, timestamp,"
                        " bin_taxon, seq_len, neighbors_json, signal_peptide, tm_domains, struct_computed)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            cid, dataset_hash, u.get("gene_id", ""), run_id, now,
                            u.get("bin_taxon", ""), len(seq),
                            json.dumps(u.get("neighbors", [])),
                            1 if u.get("signal_peptide") else 0,
                            int(u.get("tm_domains", 0) or 0),
                            1 if u.get("struct") else 0,
                        ),
                    )
                    # running aggregates (counters — accurate even after obs pruning)
                    self._bump_aggregates(con, cid, u)

                # recompute derived fields + prune, per affected cluster
                for cid in affected:
                    self._recompute_cluster(con, cid)
                    self._prune_observations(con, cid)
                    touched.append(cid)

                con.commit()
            finally:
                con.close()
        return touched

    def _bump_aggregates(self, con: sqlite3.Connection, cid: str, u: Dict[str, Any]) -> None:
        row = con.execute(
            "SELECT n_observations, neighbor_counts_json, taxa_counts_json, sample_type_counts_json,"
            " best_struct_tmscore FROM clusters WHERE cluster_id=?", (cid,)
        ).fetchone()
        n_obs, nb_json, tx_json, st_json, best_tm = row
        nb = json.loads(nb_json or "{}")
        tx = json.loads(tx_json or "{}")
        for name in (u.get("neighbors") or []):
            if name:
                nb[name] = nb.get(name, 0) + 1
        taxon = u.get("bin_taxon")
        if taxon:
            tx[taxon] = tx.get(taxon, 0) + 1
        best_target = None
        struct = u.get("struct") or None
        new_best_tm = best_tm or 0.0
        if struct and float(struct.get("tm_score", 0) or 0) > new_best_tm:
            new_best_tm = float(struct.get("tm_score", 0))
            best_target = struct.get("target")
        # Build a single clean UPDATE; only touch best_struct_target when it improved.
        set_target = ", best_struct_target=?" if best_target else ""
        params: List[Any] = [n_obs + 1, json.dumps(nb), json.dumps(tx), new_best_tm]
        if best_target:
            params.append(best_target)
        params.append(cid)
        con.execute(
            f"UPDATE clusters SET n_observations=?, neighbor_counts_json=?, taxa_counts_json=?,"
            f" best_struct_tmscore=?{set_target} WHERE cluster_id=?",
            params,
        )
        # NB: sample_type_counts_json is intentionally NOT bumped here — it is derived
        # authoritatively in _recompute_cluster from (cluster_datasets ⋈ datasets), so it
        # reflects DISTINCT datasets, not raw observations (consistent with n_datasets).
        _ = st_json  # unused; kept in the SELECT for clarity of the row shape

    def _recompute_cluster(self, con: sqlite3.Connection, cid: str) -> None:
        # n_datasets = distinct datasets (accurate; unpruned link table)
        n_ds = con.execute(
            "SELECT COUNT(*) FROM cluster_datasets WHERE cluster_id=?", (cid,)
        ).fetchone()[0]
        # sample_type distribution from the datasets this cluster appears in
        st_rows = con.execute(
            "SELECT d.sample_type, COUNT(*) FROM cluster_datasets cd "
            "JOIN datasets d ON d.dataset_hash=cd.dataset_hash "
            "WHERE cd.cluster_id=? GROUP BY d.sample_type", (cid,)
        ).fetchall()
        st_counts = {(s or "unknown"): c for s, c in st_rows}
        row = con.execute(
            "SELECT n_observations, neighbor_counts_json, taxa_counts_json,"
            " best_struct_target, best_struct_tmscore FROM clusters WHERE cluster_id=?", (cid,)
        ).fetchone()
        n_obs, nb_json, tx_json, best_target, best_tm = row
        nb = json.loads(nb_json or "{}")
        tx = json.loads(tx_json or "{}")
        hyp_text, conf, evidence, status = self._hypothesis(
            n_obs, n_ds, nb, tx, best_target, best_tm or 0.0
        )
        con.execute(
            "UPDATE clusters SET n_datasets=?, sample_type_counts_json=?, hypothesis_text=?,"
            " hypothesis_confidence=?, hypothesis_evidence_json=?, hypothesis_status=?, updated=?"
            " WHERE cluster_id=?",
            (n_ds, json.dumps(st_counts), hyp_text, conf, json.dumps(evidence), status,
             _utcnow(), cid),
        )

    @staticmethod
    def _hypothesis(n_obs: int, n_ds: int, nb: Dict[str, int], tx: Dict[str, int],
                    best_target: Optional[str], best_tm: float
                    ) -> Tuple[str, float, List[str], str]:
        """Deterministic, documented confidence: operon-adjacency + cross-dataset
        recurrence + structural match. No LLM. Weights are module constants."""
        evidence: List[str] = []
        # 1) operon signal: fraction of observations sharing the most common known neighbor
        top_neighbor, top_count = (max(nb.items(), key=lambda kv: kv[1]) if nb else (None, 0))
        neighbor_signal = (top_count / n_obs) if n_obs else 0.0
        if top_neighbor:
            evidence.append(
                f"operon-adjacent to '{top_neighbor}' in {top_count}/{n_obs} observations"
            )
        # 2) recurrence across DISTINCT datasets (anti-pollution counter)
        breadth = min(n_ds / _BREADTH_SATURATION, 1.0)
        evidence.append(f"recurs in {n_ds} distinct dataset(s), {len(tx)} taxon(s)")
        # 3) structural match (Tier 1, optional)
        struct = max(0.0, min(best_tm, 1.0))
        if best_target and struct > 0:
            evidence.append(f"structural match to {best_target} (TM={struct:.2f})")
        conf = round(_W_NEIGHBOR * neighbor_signal + _W_BREADTH * breadth + _W_STRUCT * struct, 3)
        status = "hypothesized" if conf >= _HYPOTHESIS_MIN_CONF else "unresolved"
        if status == "hypothesized" and top_neighbor:
            text = f"Putative functional partner of '{top_neighbor}'" + (
                f" (structural fold {best_target})" if best_target and struct > 0 else "")
        elif status == "hypothesized" and best_target:
            text = f"Putative protein with {best_target} fold"
        else:
            text = "Unresolved unknown protein family (insufficient cross-sample evidence)"
        return text, conf, evidence, status

    def _prune_observations(self, con: sqlite3.Connection, cid: str) -> None:
        con.execute(
            "DELETE FROM observations WHERE cluster_id=? AND id NOT IN "
            "(SELECT id FROM observations WHERE cluster_id=? ORDER BY id DESC LIMIT ?)",
            (cid, cid, _MAX_OBS_PER_CLUSTER),
        )

    # ------------------------------------------------------------------ READ
    def lookup(self, cluster_ids: List[str], k: int = 10, min_conf: float = _HYPOTHESIS_MIN_CONF
               ) -> str:
        """Return a BOUNDED top-K advisory block (≈constant size regardless of store size)
        for the given clusters — safe to inject into an LLM prompt. Empty on no hits."""
        if not cluster_ids:
            return ""
        con = self._connect()
        try:
            qmarks = ",".join("?" for _ in cluster_ids)
            rows = con.execute(
                f"SELECT cluster_id, hypothesis_text, hypothesis_confidence, n_datasets,"
                f" hypothesis_evidence_json FROM clusters "
                f"WHERE hypothesis_confidence >= ? AND cluster_id IN ({qmarks}) "
                f"ORDER BY hypothesis_confidence DESC LIMIT ?",
                [min_conf, *cluster_ids, k],
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return ""
        out = ["DARK-MATTER HYPOTHESES (cross-sample evidence, deterministic — not LLM):"]
        for cid, text, conf, n_ds, ev_json in rows:
            ev = "; ".join(json.loads(ev_json or "[]")[:2])
            out.append(f"  • {cid}: {text} (confidence {conf:.2f}, {n_ds} datasets) — {ev}")
        return "\n".join(out)

    # ------------------------------------------------------------------ stats/util
    def stats(self) -> Dict[str, Any]:
        con = self._connect()
        try:
            n_clusters = con.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
            n_hyp = con.execute(
                "SELECT COUNT(*) FROM clusters WHERE hypothesis_status='hypothesized'"
            ).fetchone()[0]
            n_ds = con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
            n_obs = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        finally:
            con.close()
        return {
            "clusters": n_clusters,
            "hypothesized": n_hyp,
            "distinct_datasets": n_ds,
            "stored_observations": n_obs,
        }
