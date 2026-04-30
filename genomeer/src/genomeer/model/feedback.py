import re
from dataclasses import dataclass
from typing import Optional, Any

try:
    from langchain_core.messages import HumanMessage
except Exception:
    HumanMessage = None


@dataclass
class FeedbackResult:
    approved: bool                 # True if explicit approval
    intent: str                    # "approve" | "revise" | "skip" | "question" | "ambiguous"
    text: str                      # raw user text
    summary: Optional[str] = None  # optional short summary if LLM provided one
    reasons: Optional[str] = None  # why we classified this way (regex/llm)


class FeedbackParser:
    """
    Parse a user's reply into "approve" vs "feedback/corrections".
    Strategy:
      1) Deterministic regex (fast path).
      2) If still ambiguous AND llm provided, ask ONLY for {"approved": true|false}.
    """

    # starts-with approvals (fast path)
    _approve_rx = re.compile(
        r"""
        ^\s*(?:y|yes|yeah|yep|ok|okay|alright|looks\ ?good|lgtm|go|run|proceed|continue|approved|ship\ it|sounds\ good|fine|works)\b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    # anywhere-in-sentence approvals (fallback if no revise hints)
    _approve_any_rx = re.compile(
        r"\b(?:yes|yeah|yep|ok|okay|alright|looks\ ?good|lgtm|go|run|proceed|continue|approved|ship\ it|sounds\ good|fine|works)\b",
        re.IGNORECASE,
    )

    _skip_rx = re.compile(
        r"""
        ^\s*(?:skip|skip\ this|move\ on|next\ step)\b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    _question_rx = re.compile(r"\?\s*$", re.IGNORECASE | re.VERBOSE)

    # phrases that strongly indicate corrections/changes
    _revise_hints = re.compile(
        r"""
        (?:change|fix|edit|modify|adjust|revise|rewrite|update|tweak|different|instead|but|however|
           add|remove|replace|use|prefer|constraint|limit|avoid|must|should|cannot|don't)\b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def parse(self, user_text: str, llm: Optional[Any] = None) -> FeedbackResult:
        text = (user_text or "").strip()
        if not text:
            return FeedbackResult(False, "ambiguous", text, reasons="empty text")

        # 1) Fast regex classification
        if self._approve_rx.match(text):
            return FeedbackResult(True, "approve", text, reasons="regex:approve_start")

        if self._skip_rx.match(text):
            return FeedbackResult(False, "skip", text, reasons="regex:skip")

        # Approval token anywhere *without* correction-y signals
        if self._approve_any_rx.search(text) and not self._revise_hints.search(text):
            return FeedbackResult(True, "approve", text, reasons="regex:approve_any")

        # correction-y verbs/constraints override weak approvals
        if self._revise_hints.search(text):
            return FeedbackResult(False, "revise", text, reasons="regex:revise_hints")

        # trailing '?' likely means a clarification request
        if self._question_rx.search(text):
            return FeedbackResult(False, "question", text, reasons="regex:question_mark")

        # 2) Ambiguous -> optionally ask LLM to classify ONLY approved true/false
        if llm is not None and HumanMessage is not None:
            prompt = f"""Decide if the user's reply is an unconditional approval to proceed.
Return EXACTLY one of the following JSON objects (no prose, no code fences):
{{"approved": true}}
or
{{"approved": false}}

Guidelines:
- Treat phrases like "it's okay", "looks good", "go ahead", "continue" as approval
  UNLESS the message also contains words like "but", "however", or edit verbs like "change", "increase", "use X instead".
- If the user asks a question or is unsure, return false.

USER_REPLY:
{text}
"""
            try:
                msg = [HumanMessage(content=prompt)]
                resp = llm.invoke(msg) if hasattr(llm, "invoke") else llm(prompt)
                content = getattr(resp, "content", str(resp)).strip()

                # Extract approved flag safely from minimal JSON
                approved_raw = self._extract_field(content, "approved")
                approved = str(approved_raw).lower() in ("true", "1", "yes") if approved_raw is not None else False

                intent = "approve" if approved else "ambiguous"
                return FeedbackResult(approved, intent, text, summary=None, reasons="llm:approved_only")
            except Exception as e:
                # Fail safe to ambiguous; regex will still have caught most approvals
                return FeedbackResult(False, "ambiguous", text, reasons=f"llm:error:{type(e).__name__}")

        # 3) Still ambiguous without LLM
        return FeedbackResult(False, "ambiguous", text, reasons="regex:default")

    # --- helper to pull "approved" from tiny JSON like {"approved": true} ---
    _field_rx_tpl = r'"{key}"\s*:\s*(?P<val>true|false|"[^"]*"|\d+|\[[^\]]*\]|\{[^\}]*\})'

    def _extract_field(self, s: str, key: str) -> Optional[str]:
        if not s:
            return None
        m = re.search(self._field_rx_tpl.format(key=re.escape(key)), s, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        val = m.group("val").strip()
        if val.lower() in ("true", "false"):
            return val.lower()
        if val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        return val
