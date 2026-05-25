"""
Behavioral constraints and output normalization for fine-tuned / LoRA models.

Two independent mechanisms work together:

1. apply(profile)  — appends stricter format rules to every node's system prompt
                      (prompt-level; polite but the model may still ignore them)

2. wrap_llm(llm)   — wraps llm.invoke so every raw response is deterministically
                      repaired BEFORE any node parser sees it
                      (code-level; always fires regardless of model behaviour)

Both are activated automatically when BioAgent(..., behavior_profile="lora").
"""

import copy
import logging
import re

logger = logging.getLogger("genomeer.lora_normalizer")


# ─────────────────────────────────────────────────────────────────────────────
# 1.  PROMPT PATCHES  (appended to instruction strings by apply())
# ─────────────────────────────────────────────────────────────────────────────

_GLOBAL_DISCIPLINE = """
═══════════════════════════════════════════════════════
RESPONSE DISCIPLINE — MANDATORY FOR ALL NODES
═══════════════════════════════════════════════════════
1. WRITE YOUR RESPONSE EXACTLY ONCE.
   Do NOT repeat, rephrase, reformat, or summarise what
   you just wrote.  If you feel the urge to write a second
   version — STOP.  One response per turn.

2. NO PREAMBLE.
   Never open with "Sure!", "Of course!", "Certainly!",
   "As a metagenomics assistant…" or any similar filler.

3. FOLLOW NODE FORMAT STRICTLY.
   Each node has an exact required output format.
   Any text outside that format breaks the pipeline.

4. STOP AFTER YOUR ANSWER.
   After your last sentence / last tag, output NOTHING.
═══════════════════════════════════════════════════════
"""

_PLANNER_DISCIPLINE = """
══════════════════════════════════════════════════════
STRICT FORMAT CHECK — PLANNER ONLY
══════════════════════════════════════════════════════
Your ENTIRE response must be ONE of these two shapes:

  Shape A — simple Q&A:
      <next:QA>
  (one line, no text before or after)

  Shape B — workflow:
      - [ ] Step 1 …
      - [ ] Step 2 …
      <next:ORCHESTRATOR>
  (checklist then tag, nothing else)

MANDATORY ROUTING RULES:
  → <next:QA>           ONLY for: definitions, explanations, comparisons,
                        pros/cons lists, conceptual questions — NO code, NO files.
  → <next:ORCHESTRATOR> for EVERYTHING ELSE, including:
      • Any task that says "parse", "read", "compute", "calculate", "run",
        "generate", "plot", "save", "download", "write code", "execute".
      • Any task involving a file path, accession, FASTA, FASTQ, TSV, etc.
      • Any task that produces output files or figures.

FORBIDDEN:
  ✗ Answering the question yourself.
  ✗ Writing Python/R/Bash code yourself — that is GENERATOR's job.
  ✗ Routing a code/file task to QA.
  ✗ Any sentence before the checklist or after the tag.
  ✗ Repeating the checklist a second time.
══════════════════════════════════════════════════════
"""

_QA_DISCIPLINE = """
══════════════════════════════════════════════════════
STRICT FORMAT CHECK — QA ONLY
══════════════════════════════════════════════════════
Write your answer ONCE in a single continuous block.
After your last word / bullet / table row: STOP.
Do NOT:
  ✗ Write a "formatted" second version of the same content.
  ✗ Add a summary after you already answered.
  ✗ Repeat any heading or section twice.
  ✗ Write Python, R, or Bash code — you are NOT a code generator.
     If the task requires code or file processing, it should never
     have reached QA. Answer only conceptual / factual questions.
══════════════════════════════════════════════════════
"""

_GENERATOR_DISCIPLINE = """
══════════════════════════════════════════════════════
STRICT FORMAT CHECK — CODE GENERATOR ONLY
══════════════════════════════════════════════════════
Output exactly ONE block.  Tags are UPPERCASE:

<EXECUTE>
#!PY
...code...
</EXECUTE>

FORBIDDEN:
  ✗ Any text or markdown outside the <EXECUTE>...</EXECUTE> block.
  ✗ Lowercase tags (<execute> ... </execute>).
  ✗ Two <EXECUTE> blocks.
  ✗ Omitting </EXECUTE>.
  ✗ Putting the <EXECUTE> tag AFTER the code — tag must come FIRST.
══════════════════════════════════════════════════════
"""

_OBSERVER_DISCIPLINE = """
══════════════════════════════════════════════════════
STRICT FORMAT CHECK — OBSERVER ONLY
══════════════════════════════════════════════════════
End with EXACTLY ONE of these tags on its own line:
  <STATUS:done>
  <STATUS:blocked>
No text after the tag.
══════════════════════════════════════════════════════
"""

_PROFILES = {
    "lora": {
        "GLOBAL_SYSTEM":    _GLOBAL_DISCIPLINE,
        "PLANNER_PROMPT":   _PLANNER_DISCIPLINE,
        "QA_PROMPT":        _QA_DISCIPLINE,
        "GENERATOR_PROMPT": _GENERATOR_DISCIPLINE,
        "OBSERVER_PROMPT":  _OBSERVER_DISCIPLINE,
    }
}


def apply(profile: str = "lora") -> None:
    """Append constraint blocks to the live instructions module strings."""
    from genomeer.agent.v2.utils import instructions as _inst

    flag = f"_behavior_profile_{profile}_applied"
    if getattr(_inst, flag, False):
        return

    patches = _PROFILES.get(profile)
    if patches is None:
        raise ValueError(f"Unknown behavior_profile '{profile}'. Available: {list(_PROFILES)}")

    for attr, addon in patches.items():
        current = getattr(_inst, attr, "")
        setattr(_inst, attr, current + addon)

    setattr(_inst, flag, True)
    print(f"[BioAgent] behavior_profile='{profile}' applied — stricter format rules active.")


# ─────────────────────────────────────────────────────────────────────────────
# 2.  OUTPUT NORMALIZER  (wraps llm.invoke deterministically)
# ─────────────────────────────────────────────────────────────────────────────

class _LoRANormalizerLLM:
    """
    Transparent LangChain LLM wrapper that repairs every response from a
    LoRA fine-tuned model before any BioAgent node parser sees it.

    Covers all known deviation patterns:
      GENERATOR  — code placed before <EXECUTE>, markdown fences, bare code
      PLANNER    — missing routing tag, inline answer instead of checklist
      INPUT_GUARD— missing/malformed <OK/> or <MISSING> tags
      OBSERVER   — missing or duplicated <STATUS:…> tags
      QA / ALL   — paragraph-level deduplication of repeated content
    """

    def __init__(self, llm):
        self._llm = llm

    # Forward every attribute access (temperature, model_name, bind, …) to wrapped LLM
    def __getattr__(self, name):
        return getattr(self._llm, name)

    # ── LangChain interface ───────────────────────────────────────────────

    def invoke(self, messages, **kwargs):
        response = self._llm.invoke(messages, **kwargs)
        return self._process(response, messages)

    def stream(self, messages, **kwargs):
        # Collect streaming chunks, normalize the assembled result, yield once
        chunks = list(self._llm.stream(messages, **kwargs))
        if not chunks:
            return iter([])
        full = chunks[0]
        for c in chunks[1:]:
            full = full + c
        return iter([self._process(full, messages)])

    # ── Core processing ───────────────────────────────────────────────────

    def _process(self, response, messages):
        if not hasattr(response, "content") or not response.content:
            return response

        text = response.content
        node = self._detect_node(messages)

        if node in ("GENERATOR", "GENERATOR_REPAIR"):
            # GENERATOR_REPAIR uses the same structural fixer as plain GENERATOR.
            text = self._fix_generator(text, node)
        elif node == "PLANNER":
            text = self._fix_planner(text, messages, node)
        elif node == "INPUT_GUARD":
            text = self._fix_input_guard(text, node)
        elif node == "OBSERVER":
            text = self._fix_observer(text, node)
        elif node == "DIAGNOSTICS_PLANNER":
            text = self._fix_diagnostics_planner(text, node)
        # QA and others: only global dedup applies

        text = self._dedup(text)
        text = text.strip()

        if text == (response.content or "").strip():
            return response  # unchanged — return original object

        result = copy.copy(response)
        result.content = text
        return result

    # ── Node detection ────────────────────────────────────────────────────
    # Each entry: (node_name, [unique strings that appear in that node's system prompt])

    _NODE_SIGNALS = [
        # Sub-modes listed BEFORE plain GENERATOR (both share "CODE_GENERATOR")
        # so their more-specific signals match first.
        ("GENERATOR_REPAIR",    ["IN REPAIR MODE", "CODE_GENERATOR IN REPAIR"]),
        ("DIAGNOSTICS_PLANNER", ["YOU ARE DIAGNOSTICS_PLANNER", "DIAGNOSTICS_PLANNER."]),
        # Standard nodes
        ("GENERATOR",   ["CODE_GENERATOR", "YOU ARE CODE_GENERATOR"]),
        ("PLANNER",     ["YOU ARE THE PLANNER", "THE PLANNER."]),
        ("INPUT_GUARD", ["YOU ARE INPUT_VALIDATOR", "INPUT_VALIDATOR."]),
        ("OBSERVER",    ["YOU ARE OBSERVER", "YOU ARE OBSERVER."]),
        ("QA",          ["YOU ARE QA", "YOU ARE QA."]),
    ]

    def _detect_node(self, messages) -> str | None:
        # BioAgent passes node-specific prompts as HumanMessage (not SystemMessage),
        # and self.system_prompt is a plain str.  Scan ALL message types so the
        # node-specific signals ("THE PLANNER", "CODE_GENERATOR", etc.) are found.
        all_upper = ""
        for msg in (messages or []):
            if isinstance(msg, str):
                all_upper += msg.upper()
            elif hasattr(msg, "content"):
                all_upper += (msg.content or "").upper()

        for node, signals in self._NODE_SIGNALS:
            if any(sig in all_upper for sig in signals):
                return node
        return None

    # ── GENERATOR fixer ───────────────────────────────────────────────────

    _RX_LANG_SHEBANG = re.compile(r"^\s*(#!(PY|R|BASH|CLI))\s*$", re.M | re.I)
    _RX_EXEC_OPEN    = re.compile(r"<\s*execute\b[^>]*>", re.I)
    _RX_EXEC_CLOSE   = re.compile(r"<\s*/\s*execute\s*>", re.I)
    _RX_FENCE        = re.compile(
        r"```(?P<lang>python|py|bash|sh|r|)\s*\n(?P<body>.*?)```", re.S | re.I
    )
    _FENCE_MAP = {"python": "PY", "py": "PY", "bash": "BASH", "sh": "BASH", "r": "R", "": "PY"}

    def _fix_generator(self, text: str, node: str = "GENERATOR") -> str:
        lang_m = self._RX_LANG_SHEBANG.search(text)
        exec_m = self._RX_EXEC_OPEN.search(text)

        # Case 1: #!LANG ... <EXECUTE [at end or wrong position]>
        # The LoRA puts the code BEFORE the opening <EXECUTE> tag.
        if lang_m and exec_m and lang_m.start() < exec_m.start():
            code_part = text[lang_m.start(): exec_m.start()].strip()
            if code_part:
                result = f"<EXECUTE>\n{code_part}\n</EXECUTE>"
                logger.debug(
                    "[%s] code-before-tag → rewrapped | before: %.100r | after: %.100r",
                    node, text, result,
                )
                return result

        # Case 2: Proper <EXECUTE> already present — normalize casing, keep first block
        if exec_m:
            out = re.sub(r"<\s*/\s*execute\s*>", "</EXECUTE>", text, flags=re.I)
            out = re.sub(
                r"<\s*execute\b([^>]*)>",
                lambda m: "<EXECUTE" + m.group(1) + ">",
                out, flags=re.I,
            )
            # Truncate after first </EXECUTE>
            end = out.upper().find("</EXECUTE>")
            if end != -1:
                out = out[: end + len("</EXECUTE>")]
            if out != text:
                logger.debug(
                    "[%s] tag-casing/truncated | before: %.100r | after: %.100r",
                    node, text, out,
                )
            return out

        # Case 3: Markdown fence, no <EXECUTE>
        mf = self._RX_FENCE.search(text)
        if mf:
            lang = self._FENCE_MAP.get(mf.group("lang").lower(), "PY")
            body = mf.group("body").strip()
            result = f"<EXECUTE>\n#!{lang}\n{body}\n</EXECUTE>"
            logger.debug(
                "[%s] markdown-fence → EXECUTE | before: %.100r | after: %.100r",
                node, text, result,
            )
            return result

        # Case 4: Bare #!LANG code, no tags at all
        if lang_m:
            code_part = text[lang_m.start():].strip()
            if code_part:
                result = f"<EXECUTE>\n{code_part}\n</EXECUTE>"
                logger.debug(
                    "[%s] bare-shebang → EXECUTE | before: %.100r | after: %.100r",
                    node, text, result,
                )
                return result

        return text

    # ── PLANNER fixer ─────────────────────────────────────────────────────

    _RX_CHECKLIST   = re.compile(r"^-\s*\[[ x]\]", re.M)
    _RX_ROUTING_TAG = re.compile(r"<next:(QA|ORCHESTRATOR)>", re.I)
    _CODE_SIGNALS   = re.compile(
        r"\b(execute|run|write\s+code|script|parse|compute|calculate|"
        r"plot|visuali[sz]e|download|install|align|generate\s+code|"
        r"create.*script|save\s+to|output\s+to)\b"
        r"|\.(fasta|fastq|fa|fna|bam|sam|vcf|gff|tsv|csv|gz)\b"
        r"|\bSRR\d{6,}\b|\bGC[FA]_\d+\b",
        re.I | re.X,
    )

    def _fix_planner(self, text: str, messages, node: str = "PLANNER") -> str:
        has_steps = bool(self._RX_CHECKLIST.search(text))
        has_tag   = bool(self._RX_ROUTING_TAG.search(text))

        if has_tag:
            return text  # Well-formed; trust the model's routing decision

        # Extract user prompt first — needed by both the has_steps and fallback branches.
        user_text = ""
        for msg in (messages or []):
            if msg.__class__.__name__ == "HumanMessage":
                user_text += msg.content or ""

        # Use StateGraphHelper.is_code_or_file_task for user-prompt checks: it applies
        # negation-stripping so "Do NOT run any code" and "No code" are not false positives.
        from genomeer.agent.v2.utils.state_graph import StateGraphHelper as _SGH
        _is_code_task = _SGH.is_code_or_file_task(user_text)

        if has_steps:
            # The LoRA model sometimes creates - [ ] steps even for pure Q&A questions.
            # Gate ORCHESTRATOR routing on whether the USER PROMPT indicates a code task.
            if _is_code_task:
                result = text.rstrip() + "\n<next:ORCHESTRATOR>"
                logger.debug(
                    "[%s] steps+code-task → added <next:ORCHESTRATOR> | before: %.100r | after: %.100r",
                    node, text, result,
                )
                return result
            else:
                # Model created a step list for a Q&A question — override to QA.
                result = text.rstrip() + "\n<next:QA>"
                logger.debug(
                    "[%s] steps-but-QA-prompt → overriding to <next:QA> | before: %.100r | after: %.100r",
                    node, text, result,
                )
                return result

        # No steps, no tag — check if user prompt implies a code/file task.
        # Only check the USER prompt for code signals, never the model's own response.
        # The PLANNER's Q&A answer naturally contains "run FastQC", "align sequences" etc.
        if _is_code_task:
            first_line = (text.strip().splitlines() or ["Execute the task"])[0][:120]
            result = f"- [ ] {first_line}\n<next:ORCHESTRATOR>"
            logger.debug(
                "[%s] inline-answer → single-step plan | before: %.100r | after: %.100r",
                node, text, result,
            )
            return result

        result = text.rstrip() + "\n<next:QA>"
        logger.debug(
            "[%s] missing-routing-tag → added <next:QA> | before: %.100r | after: %.100r",
            node, text, result,
        )
        return result

    # ── INPUT_GUARD fixer ─────────────────────────────────────────────────

    _RX_MISSING_TAG = re.compile(r"<MISSING>", re.I)
    _RX_OK_TAG      = re.compile(r"<\s*OK\s*/?\s*>|<\s*/\s*OK\s*>", re.I)

    def _fix_input_guard(self, text: str, node: str = "INPUT_GUARD") -> str:
        has_missing = bool(self._RX_MISSING_TAG.search(text))

        if has_missing:
            return text  # <MISSING> is the authoritative failure signal; don't touch

        # Normalize any OK variant to canonical <OK/>
        if self._RX_OK_TAG.search(text):
            normalized = re.sub(self._RX_OK_TAG, "", text).strip()
            result = normalized + "\n<OK/>"
            logger.debug(
                "[%s] ok-variant → <OK/> | before: %.100r | after: %.100r",
                node, text, result,
            )
            return result

        # Neither tag found: default to OK (matches INPUT_VALIDATOR rule "if in doubt → OK")
        result = text.rstrip() + "\n<OK/>"
        logger.debug(
            "[%s] no-tag → <OK/> added | before: %.100r | after: %.100r",
            node, text, result,
        )
        return result

    # ── OBSERVER fixer ────────────────────────────────────────────────────

    _RX_STATUS_INLINE = re.compile(r"<STATUS\s*:\s*(done|blocked)>", re.I)
    _SUCCESS_WORDS    = re.compile(
        r"\b(success|succeed|completed?|output|result|found|produced|written|saved)\b", re.I
    )
    _FAILURE_WORDS    = re.compile(
        r"\b(error|traceback|exception|fail(ed|ure)?|not\s+found|cannot|could\s+not|timeout)\b",
        re.I,
    )

    def _fix_observer(self, text: str, node: str = "OBSERVER") -> str:
        tags = list(self._RX_STATUS_INLINE.finditer(text))

        if len(tags) > 1:
            last_status = tags[-1].group(1).lower()
            clean = re.sub(self._RX_STATUS_INLINE, "", text).strip()
            result = f"{clean}\n<STATUS:{last_status}>"
            logger.debug(
                "[%s] %d duplicate STATUS tags → kept last <%s> | before: %.100r | after: %.100r",
                node, len(tags), last_status, text, result,
            )
            return result

        if len(tags) == 1:
            return text  # Well-formed

        # No STATUS tag — infer from execution output content
        if self._FAILURE_WORDS.search(text):
            result = text.rstrip() + "\n<STATUS:blocked>"
            logger.debug(
                "[%s] no-STATUS-tag → inferred <STATUS:blocked> | before: %.100r | after: %.100r",
                node, text, result,
            )
            return result

        result = text.rstrip() + "\n<STATUS:done>"
        logger.debug(
            "[%s] no-STATUS-tag → inferred <STATUS:done> | before: %.100r | after: %.100r",
            node, text, result,
        )
        return result

    # ── DIAGNOSTICS_PLANNER fixer ─────────────────────────────────────────
    # DIAGNOSTICS_PLANNER must emit a routing tag so the graph knows where to send
    # the diagnostic instruction (generator for a probing code run, qa if truly stuck).

    _RX_DIAG_ROUTING = re.compile(r"<next:(generator|qa)>", re.I)
    _BLOCKED_WORDS   = re.compile(
        r"\b(cannot\s+fix|cannot\s+resolve|blocked|escalate|unsolvable|give\s+up)\b", re.I
    )

    def _fix_diagnostics_planner(self, text: str, node: str = "DIAGNOSTICS_PLANNER") -> str:
        if self._RX_DIAG_ROUTING.search(text):
            return text  # Already has routing tag — well-formed

        if self._BLOCKED_WORDS.search(text):
            result = text.rstrip() + "\n<next:qa>"
            logger.debug(
                "[%s] missing routing → inferred <next:qa> (blocked signal) | before: %.100r | after: %.100r",
                node, text, result,
            )
            return result

        result = text.rstrip() + "\n<next:generator>"
        logger.debug(
            "[%s] missing routing → inferred <next:generator> | before: %.100r | after: %.100r",
            node, text, result,
        )
        return result

    # ── Global deduplication ──────────────────────────────────────────────

    def _dedup(self, text: str) -> str:
        """
        Remove repeated paragraph blocks that LoRA models produce.
        Works at paragraph level to avoid false positives on short repeated words.
        """
        if not text or len(text) < 200:
            return text

        paras = re.split(r"\n{2,}", text.strip())
        if len(paras) < 2:
            return text

        seen: list[frozenset] = []
        result: list[str] = []
        for para in paras:
            fp = self._fingerprint(para)
            if fp and any(self._jaccard(fp, s) > 0.75 for s in seen):
                continue  # duplicate — skip
            result.append(para)
            if fp:
                seen.append(fp)

        return "\n\n".join(result)

    @staticmethod
    def _fingerprint(text: str) -> frozenset:
        words = re.sub(r"[^\w\s]", "", text.lower()).split()
        return frozenset(words) if len(words) >= 5 else frozenset()

    @staticmethod
    def _jaccard(a: frozenset, b: frozenset) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def wrap_llm(llm, profile: str = "lora"):
    """
    Return an output-normalizing wrapper around *llm* for the given profile.
    Currently only "lora" is supported.

    Example::
        agent.llm = wrap_llm(agent.llm, "lora")
    """
    if profile == "lora":
        return _LoRANormalizerLLM(llm)
    return llm
