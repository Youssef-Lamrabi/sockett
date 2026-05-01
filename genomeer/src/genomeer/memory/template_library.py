"""
genomeer/memory/template_library.py
=====================================
AXE 2.1 — Template Library: persist successful pipeline plans and use them
as few-shot examples for future planning sessions.

Storage: ~/.genomeer/templates.json
Format:  [{task_type, task_summary, steps, tools_used, success_metrics, run_date, embedding}]

Usage:
    from genomeer.memory.template_library import TemplateLibrary
    lib = TemplateLibrary()

    # Save after successful run
    lib.save(task_summary="QC + taxonomy on SRR5926764", steps=plan_steps, tools_used=[...])

    # Retrieve similar templates for planner
    similar = lib.get_similar(user_query, n=3)
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Storage location
# ---------------------------------------------------------------------------
_DEFAULT_STORE = Path.home() / ".genomeer" / "templates.json"


class TemplateLibrary:
    """
    Lightweight template library for metagenomics pipeline plans.

    Stores successful plans with embeddings for semantic retrieval.
    Falls back to keyword matching if embedding libraries are unavailable.
    """

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = Path(store_path or os.getenv("GENOMEER_TEMPLATE_STORE", str(_DEFAULT_STORE)))
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._templates: List[Dict[str, Any]] = self._load()
        self._embedder = None   # lazy-loaded

    # ------------------------------------------------------------------
    def save(
        self,
        task_summary: str,
        steps: List[Dict[str, Any]],
        tools_used: Optional[List[str]] = None,
        success_metrics: Optional[Dict[str, Any]] = None,
        task_type: Optional[str] = None,
    ) -> None:
        """Persist a successful plan for future reuse."""
        t_used = tools_used or self._extract_tools_from_steps(steps)
        seq_type = "unknown"
        txt = (task_summary + " " + " ".join(t_used)).lower()
        if "flye" in txt or "nanopore" in txt or "ont" in txt:
            seq_type = "nanopore"
        elif "metaspades" in txt or "megahit" in txt or "illumina" in txt:
            seq_type = "illumina"

        template = {
            "id": f"tpl_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "run_date": datetime.now().isoformat(),
            "task_type": task_type or self._infer_task_type(task_summary, steps),
            "task_summary": task_summary,
            "steps": [
                {"title": s.get("title", ""), "status": s.get("status", "done")}
                for s in steps
            ],
            "tools_used": t_used,
            "success_metrics": success_metrics or {},
            "sequencer_type": seq_type,
        }
        self._templates.append(template)
        # Keep only last 100 templates
        if len(self._templates) > 100:
            self._templates = self._templates[-100:]
        self._save()

    def get_similar(self, query: str, n: int = 3) -> List[Dict[str, Any]]:
        """Return the n most similar templates to the query."""
        if not self._templates:
            return []

        # Extract sequence type from query
        q_seq_type = "unknown"
        q_txt = query.lower()
        if "nanopore" in q_txt or "ont" in q_txt or "flye" in q_txt:
            q_seq_type = "nanopore"
        elif "illumina" in q_txt or "metaspades" in q_txt or "megahit" in q_txt:
            q_seq_type = "illumina"

        results = []
        # Try embedding-based retrieval
        try:
            embedder = self._get_embedder()
            if embedder is not None:
                results = self._embedding_retrieval(query, len(self._templates), embedder)
        except Exception:
            pass

        # Fallback: keyword overlap
        if not results:
            results = self._keyword_retrieval(query, len(self._templates))
            
        # Re-rank based on sequencer type mismatch penalty
        scored = []
        for i, tpl in enumerate(results):
            base_score = -i # original rank score (higher is better)
            t_seq_type = tpl.get("sequencer_type", "unknown")
            if q_seq_type != "unknown" and t_seq_type != "unknown" and q_seq_type != t_seq_type:
                base_score -= 1000 # heavily penalize mismatch
            scored.append((base_score, tpl))
        
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:n]]

    def format_for_planner(self, query: str, n: int = 3) -> str:
        """Return formatted examples ready to inject into PLANNER_PROMPT."""
        similar = self.get_similar(query, n)
        if not similar:
            return ""
        lines = ["\n=== SIMILAR PAST PIPELINES (use as reference, adapt as needed) ==="]
        for i, tpl in enumerate(similar, 1):
            lines.append(f"\nExample {i}: {tpl['task_summary']}")
            lines.append(f"Type: {tpl['task_type']} | Date: {tpl['run_date'][:10]} | Seq: {tpl.get('sequencer_type', 'unknown')}")
            lines.append(f"Tools used: {', '.join(tpl['tools_used'][:8])}")
            lines.append("Steps:")
            for s in tpl["steps"][:8]:
                status_icon = "✔" if s["status"] == "done" else "✗"
                lines.append(f"  [{status_icon}] {s['title']}")
            if tpl.get("success_metrics"):
                lines.append(f"Key metrics: {_fmt_metrics(tpl['success_metrics'])}")
        lines.append("=== END EXAMPLES ===\n")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._templates)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load(self) -> List[Dict[str, Any]]:
        if self.store_path.exists():
            try:
                with open(self.store_path) as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save(self) -> None:
        try:
            with open(self.store_path, "w") as f:
                json.dump(self._templates, f, indent=2)
        except Exception:
            pass

    def _get_embedder(self):
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
            except ImportError:
                self._embedder = False   # not available
        return self._embedder if self._embedder else None

    def _embedding_retrieval(self, query: str, n: int, embedder) -> List[Dict[str, Any]]:
        import numpy as np
        q_emb = embedder.encode(query, normalize_embeddings=True)
        summaries = [t["task_summary"] for t in self._templates]
        t_embs = embedder.encode(summaries, normalize_embeddings=True, batch_size=64)
        scores = t_embs @ q_emb
        top_idx = np.argsort(scores)[::-1][:n]
        return [self._templates[i] for i in top_idx]

    def _keyword_retrieval(self, query: str, n: int) -> List[Dict[str, Any]]:
        query_words = set(query.lower().split())
        scored = []
        for tpl in self._templates:
            text = (tpl["task_summary"] + " " + " ".join(tpl["tools_used"])).lower()
            overlap = sum(1 for w in query_words if w in text)
            scored.append((overlap, tpl))
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:n]]

    @staticmethod
    def _infer_task_type(summary: str, steps: List[Dict]) -> str:
        s = (summary + " ".join(s.get("title", "") for s in steps)).lower()
        if "assembly" in s or "metaspades" in s or "megahit" in s:
            return "assembly"
        if "binning" in s or "metabat" in s or "checkm" in s:
            return "mag_reconstruction"
        if "taxonomy" in s or "kraken" in s or "metaphlan" in s:
            return "taxonomy"
        if "annotation" in s or "prokka" in s or "diamond" in s:
            return "annotation"
        if "amr" in s or "resistance" in s or "rgi" in s:
            return "amr"
        if "diversity" in s or "shannon" in s or "permanova" in s:
            return "diversity"
        if "qc" in s or "fastp" in s or "trimming" in s:
            return "qc"
        return "full_pipeline"

    @staticmethod
    def _extract_tools_from_steps(steps: List[Dict]) -> List[str]:
        TOOL_KEYWORDS = [
            "fastp", "fastqc", "multiqc", "trimgalore",
            "metaspades", "megahit", "flye",
            "minimap2", "bowtie2", "bwa", "samtools",
            "kraken2", "bracken", "metaphlan", "gtdbtk",
            "metabat", "das_tool", "checkm", "checkm2",
            "prokka", "prodigal", "diamond", "hmmer",
            "humann", "amrfinder", "rgi", "lefse", "permanova", "ancombc",
        ]
        found = []
        for s in steps:
            title = s.get("title", "").lower()
            for t in TOOL_KEYWORDS:
                if t in title and t not in found:
                    found.append(t)
        return found


def _fmt_metrics(m: dict) -> str:
    parts = []
    for k, v in list(m.items())[:4]:
        parts.append(f"{k}={v}")
    return ", ".join(parts)
