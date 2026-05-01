"""
genomeer/model/retriever.py
============================
ToolRetriever — Semantic tool selection using FAISS embeddings.

Strategy:
  1. At agent init, build a FAISS index over all tool/data/library descriptions (once).
  2. At every step, run cosine-similarity search (sub-millisecond, zero LLM tokens).
  3. Graceful fallback to LLM-based retrieval if FAISS or embeddings unavailable.

This replaces the original prompt_based_retrieval() which called an LLM for every
single tool selection query (~2000 tokens × N steps per run = expensive).
"""

from __future__ import annotations

import contextlib
import os
import re
import warnings
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Optional FAISS + embeddings imports (graceful fallback if not installed)
# ──────────────────────────────────────────────────────────────────────────────
try:
    import numpy as np

    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

_FAISS_OK = False
_faiss = None
try:
    import faiss as _faiss  # type: ignore

    _FAISS_OK = True
except ImportError:
    pass

# We support two embedding backends (priority order):
#   1. sentence-transformers (local, free, fast on CPU)
#   2. OpenAI text-embedding-3-small (cloud, requires OPENAI_API_KEY)
_EMBED_BACKEND: Optional[str] = None
_embed_model: Any = None

def _lazy_init_embedder() -> None:
    """Try to load a local sentence-transformer first, then fall back to OpenAI."""
    global _EMBED_BACKEND, _embed_model
    if _EMBED_BACKEND is not None:
        return  # already initialised

    # Option A: sentence-transformers (local)
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        _EMBED_BACKEND = "sentence_transformers"
        return
    except Exception:
        pass

    # Option B: OpenAI embeddings via langchain-openai
    try:
        from langchain_openai import OpenAIEmbeddings  # type: ignore
        _embed_model = OpenAIEmbeddings(model="text-embedding-3-small")
        _EMBED_BACKEND = "openai"
        return
    except Exception:
        pass

    warnings.warn(
        "[ToolRetriever] No embedding backend found (sentence-transformers or OpenAI). "
        "Falling back to LLM-based retrieval.",
        stacklevel=3,
    )
    _EMBED_BACKEND = "none"


def _embed(texts: List[str]) -> "np.ndarray":
    """Return an (N, D) float32 numpy array of embeddings for the given texts."""
    assert _NUMPY_OK, "numpy required for FAISS retrieval"
    if _EMBED_BACKEND == "sentence_transformers":
        return _embed_model.encode(texts, normalize_embeddings=True).astype("float32")
    elif _EMBED_BACKEND == "openai":
        raw = _embed_model.embed_documents(texts)
        arr = np.array(raw, dtype="float32")
        # normalise for cosine similarity
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        arr = arr / (norms + 1e-10)
        return arr
    else:
        raise RuntimeError("No embedding backend available")


# ──────────────────────────────────────────────────────────────────────────────
# ToolRetriever
# ──────────────────────────────────────────────────────────────────────────────

class ToolRetriever:
    """
    Retrieve the most relevant tools, data-lake items, and libraries for a query.

    Usage
    -----
    retriever = ToolRetriever()
    retriever.build_index(tools_list, data_lake_list, libraries_list)
    results = retriever.semantic_retrieval(query, k=12)
    # → {"tools": [...], "data_lake": [...], "libraries": [...]}
    """

    def __init__(self) -> None:
        self._index: Any = None            # FAISS IndexFlatIP
        self._items: List[Tuple[str, str, int]] = []  # (category, name, orig_idx)
        self._raw_resources: Dict[str, List] = {}     # original resource lists
        self._faiss_available = _FAISS_OK and _NUMPY_OK

    # ── Public: build index (call once at agent startup) ─────────────────────

    def build_index(
        self,
        tools: List[Any],
        data_lake: Optional[List[Any]] = None,
        libraries: Optional[List[Any]] = None,
    ) -> None:
        """
        Vectorise all resources and build a FAISS inner-product index.

        Parameters
        ----------
        tools      : list of tool dicts with 'name' and 'description'
        data_lake  : list of data lake items (strings or dicts)
        libraries  : list of library items (strings or dicts)
        """
        data_lake = data_lake or []
        libraries = libraries or []

        self._raw_resources = {
            "tools": list(tools),
            "data_lake": list(data_lake),
            "libraries": list(libraries),
        }

        if not self._faiss_available:
            warnings.warn(
                "[ToolRetriever.build_index] FAISS not available — index not built. "
                "Will use LLM-based fallback.",
                stacklevel=2,
            )
            return

        _lazy_init_embedder()
        if _EMBED_BACKEND == "none":
            return  # no embedder, keep fallback

        texts: List[str] = []
        meta: List[Tuple[str, str, int]] = []

        for cat, items in [("tools", tools), ("data_lake", data_lake), ("libraries", libraries)]:
            for i, item in enumerate(items):
                text = self._item_to_text(item)
                texts.append(text)
                name = item.get("name", f"item_{i}") if isinstance(item, dict) else str(item)[:60]
                meta.append((cat, name, i))

        if not texts:
            return

        try:
            embeddings = _embed(texts)          # (N, D) float32
            D = embeddings.shape[1]
            index = _faiss.IndexFlatIP(D)       # inner product = cosine (for normalised vecs)
            index.add(embeddings)               # type: ignore[arg-type]
            self._index = index
            self._items = meta
        except Exception as e:
            warnings.warn(f"[ToolRetriever.build_index] Failed to build FAISS index: {e}", stacklevel=2)
            self._index = None

    # ── Public: semantic retrieval ────────────────────────────────────────────

    def semantic_retrieval(
        self,
        query: str,
        k: int = 12,
        min_score: float = 0.30,  # FIX G13: raised from 0.15 — all-MiniLM-L6-v2 scores are generous
    ) -> Dict[str, List]:
        """
        Find the top-k most relevant resources for `query` using FAISS cosine search.

        Falls back to `prompt_based_retrieval()` if FAISS index is unavailable.

        Returns
        -------
        {"tools": [...], "data_lake": [...], "libraries": [...]}
        """
        if self._index is None or not self._faiss_available or _EMBED_BACKEND == "none":
            # Graceful fallback: use LLM
            return self.prompt_based_retrieval(query, self._raw_resources)

        try:
            q_vec = _embed([query])             # (1, D)
            k_search = min(k * 2, len(self._items))  # over-fetch then filter
            scores, indices = self._index.search(q_vec, k_search)  # type: ignore
            scores = scores[0]
            indices = indices[0]

            selected: Dict[str, List] = {"tools": [], "data_lake": [], "libraries": []}
            seen: Dict[str, set] = {"tools": set(), "data_lake": set(), "libraries": set()}

            for score, idx in zip(scores, indices):
                if idx < 0:
                    continue
                if float(score) < min_score:
                    continue
                cat, name, orig_idx = self._items[idx]
                if orig_idx in seen[cat]:
                    continue
                seen[cat].add(orig_idx)
                raw = self._raw_resources[cat]
                if orig_idx < len(raw):
                    selected[cat].append(raw[orig_idx])

            return selected

        except Exception as e:
            warnings.warn(f"[ToolRetriever.semantic_retrieval] FAISS search failed: {e}. Using LLM fallback.", stacklevel=2)
            return self.prompt_based_retrieval(query, self._raw_resources)

    # ── Public: LLM-based fallback (original logic, kept intact) ─────────────

    def prompt_based_retrieval(self, query: str, resources: dict, llm=None) -> dict:
        """
        LLM-based resource selection — used as fallback when FAISS unavailable.
        Original implementation preserved for compatibility.
        """
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        prompt = f"""
            You are an expert biomedical research assistant. Your task is to select the relevant resources to help answer a user's query.

            USER QUERY: {query}

            Below are the available resources. For each category, select items that are directly or indirectly relevant to answering the query.
            Be generous in your selection - include resources that might be useful for the task, even if they're not explicitly mentioned in the query.

            AVAILABLE TOOLS:
            {self._format_resources_for_prompt(resources.get("tools", []))}

            AVAILABLE DATA LAKE ITEMS:
            {self._format_resources_for_prompt(resources.get("data_lake", []))}

            AVAILABLE SOFTWARE LIBRARIES:
            {self._format_resources_for_prompt(resources.get("libraries", []))}

            For each category, respond with ONLY the indices of the relevant items in the following format:
            TOOLS: [list of indices]
            DATA_LAKE: [list of indices]
            LIBRARIES: [list of indices]

            IMPORTANT GUIDELINES:
            1. Be generous but not excessive
            2. ALWAYS prioritize database tools for general queries
            3. Include all literature search tools
            4. For wet lab sequence queries, ALWAYS include molecular biology tools
        """

        # FIX G14: removed hardcoded ChatOpenAI(gpt-4o) fallback — callers must pass llm=
        if llm is None:
            warnings.warn(
                "[ToolRetriever] prompt_based_retrieval called without an LLM. "
                "Pass llm= argument. Returning empty resource selection.",
                stacklevel=2,
            )
            return {"tools": [], "data_lake": [], "libraries": []}

        if hasattr(llm, "invoke"):
            response = llm.invoke([HumanMessage(content=prompt)])
            response_content = response.content
        else:
            response_content = str(llm(prompt))

        selected_indices = self._parse_llm_response(response_content)

        return {
            "tools": [
                resources["tools"][i] for i in selected_indices.get("tools", [])
                if i < len(resources.get("tools", []))
            ],
            "data_lake": [
                resources["data_lake"][i] for i in selected_indices.get("data_lake", [])
                if i < len(resources.get("data_lake", []))
            ],
            "libraries": [
                resources["libraries"][i] for i in selected_indices.get("libraries", [])
                if i < len(resources.get("libraries", []))
            ],
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _item_to_text(self, item: Any) -> str:
        if isinstance(item, dict):
            name = item.get("name", "")
            desc = item.get("description", "")
            return f"{name}: {desc}".strip()
        return str(item)

    def _format_resources_for_prompt(self, resources: list) -> str:
        formatted = []
        for i, resource in enumerate(resources):
            if isinstance(resource, dict):
                name = resource.get("name", f"Resource {i}")
                description = resource.get("description", "")
                formatted.append(f"{i}. {name}: {description}")
            elif isinstance(resource, str):
                formatted.append(f"{i}. {resource}")
            else:
                name = getattr(resource, "name", str(resource))
                desc = getattr(resource, "description", "")
                formatted.append(f"{i}. {name}: {desc}")
        return "\n".join(formatted) if formatted else "None available"

    def _parse_llm_response(self, response: str) -> dict:
        selected_indices: Dict[str, List[int]] = {"tools": [], "data_lake": [], "libraries": []}
        tools_match = re.search(r"TOOLS:\s*\[(.*?)\]", response, re.IGNORECASE)
        if tools_match and tools_match.group(1).strip():
            with contextlib.suppress(ValueError):
                selected_indices["tools"] = [int(x.strip()) for x in tools_match.group(1).split(",") if x.strip()]
        data_lake_match = re.search(r"DATA_LAKE:\s*\[(.*?)\]", response, re.IGNORECASE)
        if data_lake_match and data_lake_match.group(1).strip():
            with contextlib.suppress(ValueError):
                selected_indices["data_lake"] = [int(x.strip()) for x in data_lake_match.group(1).split(",") if x.strip()]
        libraries_match = re.search(r"LIBRARIES:\s*\[(.*?)\]", response, re.IGNORECASE)
        if libraries_match and libraries_match.group(1).strip():
            with contextlib.suppress(ValueError):
                selected_indices["libraries"] = [int(x.strip()) for x in libraries_match.group(1).split(",") if x.strip()]
        return selected_indices

    # ── Convenience: names only (used by BioAgent) ────────────────────────────
    def get_resource_names(self, resources: Dict[str, List]) -> List[str]:
        """Flatten retrieved resources to a list of names."""
        names = []
        for items in resources.values():
            for item in items:
                if isinstance(item, dict):
                    names.append(item.get("name", str(item)))
                else:
                    names.append(str(item))
        return names