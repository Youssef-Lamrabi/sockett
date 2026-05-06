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
        template["quality_score"] = self._compute_quality_score(template)
        
        self._templates.append(template)
        # Keep top 100 templates based on combined quality and recency score
        if len(self._templates) > 100:
            now = datetime.now()
            scored_templates = []
            for t in self._templates:
                q_score = t.get("quality_score", 0.5)
                try:
                    run_date = datetime.fromisoformat(t.get("run_date", now.isoformat()))
                    days_ago = max(0, (now - run_date).days)
                    r_score = 1.0 / (1.0 + days_ago)
                except Exception:
                    r_score = 0.0
                
                combined = (q_score * 0.7) + (r_score * 0.3)
                scored_templates.append((combined, t))
            
            scored_templates.sort(key=lambda x: -x[0])
            self._templates = [t for _, t in scored_templates[:100]]
            
        self._save()

    def get_similar(self, query: str, n: int = 3, embed_fn: Optional[Any] = None) -> List[Dict[str, Any]]:
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
            if embed_fn is not None:
                results = self._embedding_retrieval(query, len(self._templates), embed_fn=embed_fn)
            else:
                embedder = self._get_embedder()
                if embedder is not None:
                    results = self._embedding_retrieval(query, len(self._templates), embedder=embedder)
        except Exception:
            pass

        # Fallback: keyword overlap
        if not results:
            results = self._keyword_retrieval(query, len(self._templates))
            
        # Re-rank based on combined similarity, quality, and sequencer type mismatch penalty
        scored = []
        for i, tpl in enumerate(results):
            # approximate semantic similarity score by index
            sim_score = 1.0 - (i / max(1, len(results)))
            q_score = tpl.get("quality_score", 0.5)
            
            base_score = (sim_score * 0.6) + (q_score * 0.4)
            
            t_seq_type = tpl.get("sequencer_type", "unknown")
            if q_seq_type != "unknown" and t_seq_type != "unknown" and q_seq_type != t_seq_type:
                base_score -= 1000 # heavily penalize mismatch
            scored.append((base_score, tpl))
        
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:n]]

    def format_for_planner(self, query: str, n: int = 3, embed_fn: Optional[Any] = None) -> str:
        """Return formatted examples ready to inject into PLANNER_PROMPT."""
        similar = self.get_similar(query, n, embed_fn=embed_fn)
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

    def stats(self) -> dict:
        """P3-B.4: Provide stats including average quality score and >0.7 threshold counts."""
        if not self._templates:
            return {"count": 0}
            
        scores = [t.get("quality_score", 0.5) for t in self._templates]
        avg_score = sum(scores) / len(scores)
        best_template = max(self._templates, key=lambda t: t.get("quality_score", 0.5))
        
        seq_types = {}
        for t in self._templates:
            st = t.get("sequencer_type", "unknown")
            seq_types[st] = seq_types.get(st, 0) + 1
            
        high_quality = sum(1 for s in scores if s > 0.7)
        
        return {
            "count": len(self._templates),
            "average_quality_score": round(avg_score, 3),
            "best_template_id": best_template.get("id"),
            "best_template_score": round(best_template.get("quality_score", 0.5), 3),
            "high_quality_count": high_quality,
            "sequencer_types": seq_types,
        }

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
            tmp_path = self.store_path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(self._templates, f, indent=2)
            os.replace(tmp_path, self.store_path)
        except Exception:
            try:
                tmp_path = self.store_path.with_suffix(".tmp")
                if tmp_path.exists():
                    tmp_path.unlink()
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

    def _embedding_retrieval(self, query: str, n: int, embedder=None, embed_fn=None) -> List[Dict[str, Any]]:
        import numpy as np
        
        summaries = [t["task_summary"] for t in self._templates]
        
        if embed_fn is not None:
            q_emb = embed_fn([query])[0]
            t_embs = embed_fn(summaries)
        else:
            q_emb = embedder.encode(query, normalize_embeddings=True)
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
    def _compute_quality_score(template: dict) -> float:
        """P3-B.1: Compute composite score from success_metrics."""
        metrics = template.get("success_metrics")
        if not metrics:
            return 0.5
            
        try:
            def _get_val(substring):
                for k, v in metrics.items():
                    if substring in k:
                        if isinstance(v, str):
                            v = v.replace('%', '').strip()
                        return float(v)
                return 0.0

            c_pct = _get_val("classified_pct") / 100.0 * 0.3
            n50 = _get_val("n50")
            n_score = min(n50 / 50000.0, 1.0) * 0.3
            comp = _get_val("completeness") / 100.0 * 0.2
            
            contam_raw = _get_val("contamination")
            contam_score = max(0.0, 1.0 - (contam_raw / 10.0)) * 0.2
            
            score = c_pct + n_score + comp + contam_score
            return score if score > 0 else 0.5
        except Exception:
            return 0.5

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
