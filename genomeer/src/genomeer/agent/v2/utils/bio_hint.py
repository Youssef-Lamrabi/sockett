"""
bio_hint.py — Optional domain-hint node for BioAgent.

Architecture
────────────
This node calls a secondary LLM (typically a fine-tuned 8B bioinformatics model)
to extract raw domain knowledge about the current pipeline step.  The output is
treated as unverified "junior expert notes" and injected into the Generator's
context block with an explicit disclaimer.

The generalist LLM (Generator) receives these hints with instructions to:
  • Extract the causal/biological reasoning
  • Verify or ignore any specific CLI flags, column names, or tool versions
  • Give absolute priority to explicit rules and observed errors over these hints

Design decisions
────────────────
• No structured output is requested from the 8B — it cannot reliably produce it.
  Free-text prompts aligned with the 8B's training distribution (workflow +
  troubleshooting + factual) are used instead.
• The output is never parsed: it is passed raw (after length-capping and safety
  rejection) to the generalist which does all the filtering.
• Two modes: pre_gen (before first generation attempt) and debug (after a block).
• Completely optional: if bio_hint_llm is None the graph skips this node via the
  conditional edges and behaves identically to the unmodified version.
• Dedup guard: called at most once per step (tracked by bio_hint_step_idx).
• Triage in debug mode: skip when the error is purely technical (SyntaxError,
  ImportError, FileNotFoundError…) with no biological tool names present.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
from typing import Any, Callable, Dict, Optional

from langchain_core.messages import HumanMessage

logger = logging.getLogger("genomeer.bio_hint")

# ── Triage: errors that are purely technical and do not benefit from 8B ───────
_PURE_TECHNICAL_ERRORS = re.compile(
    r"\b(SyntaxError|IndentationError|TabError|ImportError|ModuleNotFoundError"
    r"|FileNotFoundError|FileExistsError|IsADirectoryError|PermissionError"
    r"|TimeoutExpired|TimeoutError|MemoryError|RecursionError|OSError"
    r"|ConnectionError|subprocess\.CalledProcessError|CalledProcessError)\b",
    re.I,
)
_BIO_TOOL_NAMES = re.compile(
    r"\b(prodigal|seqkit|quast|prokka|kraken2|megahit|spades|samtools"
    r"|abricate|checkm2|diamond|hmmer|fastp|semibin2|concoct|kaiju"
    r"|sylph|antismash|genomad|bbduk|humann|bracken|busco|infernal"
    r"|ncbi.genome.download|prodigal|bowtie|minimap|bwa|trimmomatic)\b",
    re.I,
)

# ── Output safety: reject if it looks like the 8B produced code/YAML ─────────
_REJECT_PATTERNS = re.compile(
    r"(^```|^\{[\s\n]*\"|^---\s*$|^import\s+\w|^def\s+\w|^class\s+\w"
    r"|^#!PY|^#!BASH|^#!R|^<EXECUTE|^\[.*\]\s*:)",
    re.M,
)

# ── Prompts aligned with the 8B's training distribution ──────────────────────
_PROMPT_PRE_GEN = (
    "In metagenomics bioinformatics, for the following pipeline step, briefly explain:\n"
    "- Why this step matters and what it should produce\n"
    "- Common biological or computational pitfalls and why they happen\n"
    "- Important considerations about the data at this stage\n\n"
    "Step: {step_title}\n\n"
    "Write 3-5 sentences. Focus on biological reasoning. Do not write code."
)

_PROMPT_DEBUG = (
    "A bioinformatics pipeline step has failed. Based on your knowledge of "
    "metagenomics tools and common errors, briefly explain:\n"
    "- The most likely biological or computational cause of this failure\n"
    "- What type of issue typically produces this error pattern\n"
    "- Whether this suggests a data quality problem or a tool configuration problem\n\n"
    "Step that failed: {step_title}\n"
    "Error observed: {error_summary}\n\n"
    "Write 3-5 sentences focused on the underlying cause. Do not write code."
)


class BioHintNode:
    """
    LangGraph-compatible callable node that enriches Generator context with 8B hints.

    Usage
    -----
    node = BioHintNode(llm=my_8b_llm, log_fn=self._log)
    workflow.add_node("bio_hint", node)
    """

    MAX_OUTPUT_CHARS: int = 500
    CALL_TIMEOUT_SEC: int = 45

    def __init__(
        self,
        llm: Any,
        log_fn: Optional[Callable[..., None]] = None,
    ) -> None:
        self.llm = llm
        self._log = log_fn or (lambda *a, **kw: None)

    # ── LangGraph entry point ─────────────────────────────────────────────────
    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        node = "bio_hint"
        current_idx: int = state.get("current_idx", 0)
        manifest: Dict[str, Any] = dict(state.get("manifest") or {})
        last_result: str = state.get("last_result") or ""
        repair_feedback: Optional[str] = manifest.get("repair_feedback")

        # ── Dedup guard ───────────────────────────────────────────────────────
        if state.get("bio_hint_step_idx") == current_idx:
            self._log(
                "BIO_HINT SKIP",
                body="Already called for this step (dedup guard)",
                node=node,
            )
            return {"next_step": "generator", "bio_hint_skipped": True}

        # ── Mode detection ────────────────────────────────────────────────────
        is_debug: bool = bool(repair_feedback)

        # ── Triage in debug mode ──────────────────────────────────────────────
        if is_debug and not self._is_bio_relevant(last_result):
            self._log(
                "BIO_HINT SKIP",
                body=f"Pure technical error — 8B adds no value: {last_result[:100]}",
                node=node,
            )
            return {
                "next_step": "generator",
                "bio_hint_step_idx": current_idx,
                "bio_hint_skipped": True,
            }

        # ── Resolve step title (use raw_title to preserve full instruction) ───
        plan = state.get("plan") or []
        step: Dict[str, Any] = plan[current_idx] if current_idx < len(plan) else {}
        step_title: str = (
            step.get("raw_title") or step.get("title") or "Unknown step"
        )

        # ── Build prompt ──────────────────────────────────────────────────────
        if is_debug:
            error_summary = (last_result[-300:] if last_result else repair_feedback[:300])
            prompt_text = _PROMPT_DEBUG.format(
                step_title=step_title,
                error_summary=error_summary,
            )
            mode = "debug"
        else:
            prompt_text = _PROMPT_PRE_GEN.format(step_title=step_title)
            mode = "pre_gen"

        self._log(
            "BIO_HINT CALL",
            body=f"mode={mode}\nstep={step_title[:80]}",
            node=node,
        )

        # ── Call 8B ───────────────────────────────────────────────────────────
        raw_output = self._call_8b(prompt_text, node)

        # ── Validate & clean ──────────────────────────────────────────────────
        validated = self._validate(raw_output)

        if validated:
            manifest["bio_hint"] = validated
            manifest["bio_hint_mode"] = mode
            self._log(
                "BIO_HINT OK",
                body=f"mode={mode} len={len(validated)}\n---\n{validated[:300]}\n---",
                node=node,
            )
        else:
            manifest.pop("bio_hint", None)
            manifest.pop("bio_hint_mode", None)
            self._log(
                "BIO_HINT EMPTY",
                body=f"mode={mode} — 8B output rejected or empty",
                node=node,
            )

        return {
            "next_step": "generator",
            "manifest": manifest,
            "bio_hint": validated or None,
            "bio_hint_step_idx": current_idx,
            "bio_hint_mode": mode,
            "bio_hint_skipped": not bool(validated),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _call_8b(self, prompt: str, node: str) -> str:
        """
        Call the 8B LLM with a hard timeout.  Returns empty string on any failure.
        Uses a thread-pool so we never block the LangGraph event loop beyond
        CALL_TIMEOUT_SEC seconds.
        """
        try:
            messages = [HumanMessage(content=prompt)]

            def _invoke() -> str:
                resp = self.llm.invoke(messages)
                return getattr(resp, "content", str(resp)) or ""

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_invoke)
                try:
                    return fut.result(timeout=self.CALL_TIMEOUT_SEC)
                except concurrent.futures.TimeoutError:
                    self._log(
                        "BIO_HINT TIMEOUT",
                        body=f"8B call exceeded {self.CALL_TIMEOUT_SEC}s — skipping",
                        node=node,
                    )
                    return ""
        except Exception as exc:  # noqa: BLE001
            self._log(
                "BIO_HINT ERROR",
                body=f"8B invocation failed: {exc}",
                node=node,
            )
            return ""

    def _validate(self, text: str) -> str:
        """
        Validate and clean 8B output.

        Returns the cleaned string if acceptable, empty string otherwise.
        Rejection criteria:
          • Too short (< 20 chars)
          • Contains code blocks, YAML, or structured data markers
        """
        if not text or len(text.strip()) < 20:
            return ""
        if _REJECT_PATTERNS.search(text.strip()):
            return ""
        return text.strip()[: self.MAX_OUTPUT_CHARS]

    @staticmethod
    def _is_bio_relevant(error_text: str) -> bool:
        """
        Return True when the error likely benefits from 8B biological domain knowledge.

        Logic:
          • If a known bio-tool name appears → relevant (tool-specific error)
          • If the error is purely technical (SyntaxError, ImportError…) with no
            bio-tool context → NOT relevant (8B won't help)
          • Otherwise (unknown / ambiguous error) → relevant by default
        """
        if not error_text:
            return False
        has_bio_tool = bool(_BIO_TOOL_NAMES.search(error_text))
        has_pure_tech_only = bool(_PURE_TECHNICAL_ERRORS.search(error_text)) and not has_bio_tool
        return not has_pure_tech_only


# ── Routing factory ───────────────────────────────────────────────────────────

def make_bio_hint_router(bio_hint_node_name: str = "bio_hint") -> Callable[[Dict[str, Any]], str]:
    """
    Return a LangGraph routing function for use with add_conditional_edges.

    The returned function intercepts next_step="generator" transitions and
    redirects them through bio_hint — but only when bio_hint has not yet been
    called for the current step (dedup) and a direct path is not forced.

    Usage in configure():
        route = make_bio_hint_router()
        workflow.add_conditional_edges(
            "input_guard",
            route,
            {"bio_hint": "bio_hint", "generator": "generator", "qa": "qa"},
        )
    """
    def _route(state: Dict[str, Any]) -> str:
        raw_next: str = state.get("next_step", "generator")

        # Only intercept generator-bound transitions
        if raw_next != "generator":
            return raw_next

        current_idx: int = state.get("current_idx", 0)

        # Dedup: already called for this step → go straight to generator
        if state.get("bio_hint_step_idx") == current_idx:
            return "generator"

        # Diagnostics mode: always skip bio_hint
        if state.get("diagnostic_mode"):
            return "generator"

        return bio_hint_node_name

    return _route
