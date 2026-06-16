import re

class StateGraphHelper:
    # ---------- PARSERS ----------
    RX_NEXT = re.compile(r"<next:(QA|ORCHESTRATOR)>", re.I)
    RX_EXEC = re.compile(r"<EXECUTE[^>]*>(.*?)</EXECUTE>", re.S | re.I)
    RX_LANG = re.compile(r"^\s*#!(PY|R|BASH|CLI)\s*$", re.I | re.M)
    RX_MISSING = re.compile(r"<MISSING>(.*?)</MISSING>", re.S | re.I)
    RX_PRESENT = re.compile(r"<PRESENT>(.*?)</PRESENT>", re.S | re.I)
    RX_OK = re.compile(r"<OK\s*/\s*>", re.I)
    RX_STATUS_WRAPPED = re.compile(r"<STATUS\s*:\s*(done|blocked)\s*>(.*?)</STATUS>", re.S | re.I)
    RX_STATUS_INLINE  = re.compile(r"<STATUS\s*:\s*(done|blocked)>", re.I)


    # Regex used by the tolerant fallback below — compiled once at import time.
    # Numbered (1. X, 1) X) OR simple bullet (* X, - X without [ ]).
    _RX_NUMBERED_OR_BULLET = re.compile(r"^\s*(?:\d+[\.\)]|[-*])\s+(.+)$")

    # Action verbs that commonly start a bioinformatics planning step.
    # Used as a defensive trigger: tolerant fallback ONLY activates when the
    # line starts with one of these (or when <next:ORCHESTRATOR> is explicit).
    # Prevents accidental step-extraction from QA answers like
    # "1. What is N50?  2. How does Prodigal work?".
    _RX_PLAN_ACTION_VERB = re.compile(
        r"^(?:download|run|execute|compute|parse|generate|extract|"
        r"decompress|annotate|align|map|sort|index|filter|trim|assemble|"
        r"call|classify|cluster|merge|split|convert|verify|validate|"
        r"build|create|write|save|load|read|fetch|query|search|process|"
        r"analyze|analyse|predict|estimate|calculate|count|summari[sz]e|"
        r"visuali[sz]e|plot|chart|report|setup|set\s+up|configure|"
        r"install|prepare|copy|move|rename|remove|delete|deploy|launch|"
        r"start|stop|test|benchmark|profile|cluster|bin|polish|scaffold)\b",
        re.IGNORECASE,
    )

    # Reject lines that look like CODE (avoids treating snippets in narrative
    # responses as plan steps). Two layers:
    #   (1) Line STARTING with code (import, def, etc.)
    #   (2) Line CONTAINING obvious Python tokens anywhere
    #       (function calls like subprocess.run(, glob.glob(, etc.)
    _RX_LOOKS_LIKE_CODE = re.compile(
        r"```"
        r"|^(?:import\s|from\s+\w+\s+import\b|def\s+\w|class\s+\w|"
            r"return\b|res\s*=|cmd\s*=|subprocess\.|os\.|sys\.|shutil\.)"
        r"|\b(?:subprocess|os|sys|shutil|glob|json|re|csv|pathlib|Path|"
            r"open|gzip|tarfile|zipfile|tempfile)\.\w+\("
        r"|\['[\w\-.]+'"        # ['emapper.py', ...] arg lists
        r"|=\s*\['"            # var = ['...'] assignment
    )

    @staticmethod
    def parse_checklist_and_route(text: str):
        """
        Extract planning steps from the LLM response and decide routing.

        PRIMARY format (strict): '- [ ] <title>' — original behavior, untouched.

        TOLERANT FALLBACK (only when PRIMARY found 0 steps): also accept
        numbered lists ('1. X', '2) X') and simple bullets ('* X', '- X'),
        BUT only when:
          - the response also has an explicit '<next:ORCHESTRATOR>' tag, OR
          - the line's title starts with an action verb (download/run/...)
        AND the line does NOT look like code (no `import`, `subprocess.`, etc).

        Why: when the conversation history pushes the LLM into a narrative
        style (e.g. after a redirection like "skip, use Prokka instead"),
        it sometimes emits a numbered list instead of '- [ ]'. The fallback
        captures the plan without affecting strict QA answers.
        """
        txt = text or ""
        m = StateGraphHelper.RX_NEXT.search(txt)
        _explicit_orchestrator = bool(m and m.group(1).upper() == "ORCHESTRATOR")

        steps = []
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("- [ ]"):
                title = line[5:].strip()
                if title:
                    steps.append({"title": title, "status": "todo", "notes": ""})

        # Tolerant fallback ONLY if strict format found nothing. Never overrides.
        if not steps:
            for raw in txt.splitlines():
                ls = raw.strip()
                if not ls:
                    continue
                if StateGraphHelper._RX_LOOKS_LIKE_CODE.search(ls):
                    continue
                nm = StateGraphHelper._RX_NUMBERED_OR_BULLET.match(ls)
                if not nm:
                    continue
                title = nm.group(1).strip()
                # Strip leading bold/italic markdown that some LLMs add
                title = re.sub(r"^[*_`]+|[*_`]+$", "", title).strip()
                # Length guard: real plan steps are usually 15+ chars
                if len(title) < 12:
                    continue
                # Code-in-title guard: if the title itself has code tokens, skip
                if StateGraphHelper._RX_LOOKS_LIKE_CODE.search(title):
                    continue
                # Activate fallback ONLY when intent is clearly a plan
                if _explicit_orchestrator or StateGraphHelper._RX_PLAN_ACTION_VERB.match(title):
                    steps.append({"title": title, "status": "todo", "notes": ""})

        if m:
            route = m.group(1).upper()
        else:
            # Heuristic fallback — no explicit tag:
            # steps present → ORCHESTRATOR; nothing → QA
            route = "ORCHESTRATOR" if steps else "QA"

        return steps, ("qa" if route == "QA" else "orchestrator")

    @staticmethod
    def sanitize_execute_block(raw: str) -> str:
        if not raw:
            return ""
        text = raw

        # normalize tags to uppercase
        text = re.sub(r"<\s*/\s*execute\s*>", "</EXECUTE>", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*execute\b([^>]*)>", lambda m: "<EXECUTE" + m.group(1) + ">", text, flags=re.IGNORECASE)

        # kkeep only up to the first closing tag
        end = text.upper().find("</EXECUTE>")
        if end == -1:
            text = text + "</EXECUTE>"
        else:
            text = text[: end + len("</EXECUTE>")]

        # Fix multi-line binary-operator splits that cause SyntaxError / IndentationError.
        #
        # Two failure modes the LLM produces:
        #   1. Trailing +/|  (no enclosing parens)  →  SyntaxError on line ending with operator
        #      fasta = glob("*.fna") +
        #              glob("*.fna.gz")       ← Python rejects this without parens
        #
        #   2. Leading +/| with indent  →  IndentationError (unexpected indent)
        #      fasta = glob("*.fna")
        #              + glob("*.fna.gz")     ← IndentationError at module level
        #
        # Fix: join the continuation line onto its predecessor so the full expression
        # sits on one line.  Works for both cases and preserves quoted content.

        # Pass 1 — absorb lines that START with + or | into the previous line.
        _p1 = []
        for _line in text.splitlines():
            _s = _line.lstrip()
            if _p1 and _s and _s[0] in ('+', '|') and (len(_s) < 2 or _s[1] in (' ', '\t', '(')):
                _p1[-1] = _p1[-1].rstrip() + ' ' + _s
            else:
                _p1.append(_line)

        # Pass 2 — absorb lines whose TAIL is + or | into the following line.
        # GUARD: skip joining when inside open brackets (, [, { — Python's implicit
        # line-continuation already handles those, and stripping the indent of the
        # next line with .lstrip() would produce an IndentationError in the file.
        _p2: list = []
        _j = 0
        _depth = 0  # bracket depth: (, [, {
        while _j < len(_p1):
            _ln = _p1[_j]
            _rt = _ln.rstrip()
            # Update bracket depth for this line (simplified: ignore strings)
            for _ch in _ln:
                if _ch in '([{':
                    _depth += 1
                elif _ch in ')]}':
                    _depth = max(0, _depth - 1)
            # Only join when NOT inside implicit-continuation brackets
            while (
                _depth == 0
                and _j + 1 < len(_p1)
                and _rt
                and _rt[-1] in ('+', '|')
                and not _rt.endswith(('"""', "'''"))
            ):
                _j += 1
                _next = _p1[_j]
                for _ch in _next:
                    if _ch in '([{':
                        _depth += 1
                    elif _ch in ')]}':
                        _depth = max(0, _depth - 1)
                _ln = _rt + ' ' + _next.lstrip()
                _rt = _ln.rstrip()
            _p2.append(_ln)
            _j += 1

        text = '\n'.join(_p2)

        return text

    # Fallback: markdown fenced code blocks  ```python / ```py / ```bash / ```r
    RX_MARKDOWN = re.compile(
        r"```(?P<fence_lang>python|py|bash|sh|r|)\s*\n(?P<body>.*?)```",
        re.S | re.I,
    )
    _FENCE_LANG_MAP = {"python": "PY", "py": "PY", "bash": "BASH", "sh": "BASH", "r": "R", "": "PY"}

    @staticmethod
    def parse_execute(text: str):
        code = None
        lang = None

        # Primary: <EXECUTE>...</EXECUTE> tags
        m = StateGraphHelper.RX_EXEC.search(text or "")
        if m:
            code = m.group(1).strip()
            lm = StateGraphHelper.RX_LANG.search(code or "")
            if lm:
                lang = lm.group(1).upper()
            return code, lang

        # Fallback: markdown code fence — used by models that ignore the tag format
        mf = StateGraphHelper.RX_MARKDOWN.search(text or "")
        if mf:
            body = mf.group("body").strip()
            fence_lang = mf.group("fence_lang").lower()
            lang = StateGraphHelper._FENCE_LANG_MAP.get(fence_lang, "PY")
            # Prepend the lang header so downstream logic stays consistent
            code = f"#!{lang}\n{body}"
            return code, lang

        return code, lang

    # Items the LLM hallucinates as MISSING for network/download steps but that are
    # NEVER valid blockers — the code can always derive them from context or tool defaults.
    _FALSE_POSITIVE_MISSING = re.compile(
        r"^(accession[_\s]?id|accession|ncbi[_\s]?accession"
        r"|url|download[_\s]?url|ncbi[_\s]?url|ftp[_\s]?url"
        r"|database[_\s]?id|api[_\s]?key|network[_\s]?resource"
        r"|internet[_\s]?connection|web[_\s]?access)\b",
        re.I,
    )

    @staticmethod
    def parse_missing_ok(text: str):
        """
        Determine whether the input_guard LLM response signals OK or MISSING.

        Decision rule (deterministic, model-independent):
          - <MISSING> present  →  NOT OK  (explicit failure signal)
          - <MISSING> absent   →  OK      (regardless of <OK/> presence)

        False-positive filter: known hallucinated items (accession_id, url, …) are
        stripped from MISSING lists. If stripping empties the list → force OK.
        This is the model-agnostic layer on top of the prompt rule.
        """
        txt = text or ""

        # <MISSING> is the only authoritative failure signal.
        mm = StateGraphHelper.RX_MISSING.search(txt)
        if mm:
            raw = [x.strip("- ").strip() for x in mm.group(1).strip().splitlines() if x.strip()]
            # Strip the item name (before '::') for matching; keep the full line for reporting.
            items = [
                item for item in raw
                if not StateGraphHelper._FALSE_POSITIVE_MISSING.match(item.split("::")[0].strip())
            ]
            if not items:
                # All declared-missing items were known false positives → treat as OK.
                return [], True
            return items, False

        # No <MISSING> found → treat as OK.
        mp = StateGraphHelper.RX_PRESENT.search(txt)
        if mp:
            items = [x.strip("- ").strip() for x in mp.group(1).strip().splitlines() if x.strip()]
            return items, True

        return [], True


    @staticmethod
    def parse_status(text: str):
        txt = text or ""

        m = StateGraphHelper.RX_STATUS_WRAPPED.search(txt)
        if m:
            status = m.group(1).lower()
            summary = m.group(2).strip()
            return status, summary

        m2 = None
        # find the LAST inline status if multiple appear
        for _m in StateGraphHelper.RX_STATUS_INLINE.finditer(txt):
            m2 = _m
        if m2:
            status = m2.group(1).lower()
            summary = txt[:m2.start()].strip()
            summary = re.sub(r"</?[^>]+>", "", summary).strip()
            return status, summary

        # No STATUS tag found — re-prompt the observer with the full text as context
        # rather than wasting retries with empty repair_feedback.
        _parse_fail_msg = (
            "OBSERVER_FORMAT_ERROR: The previous observer response contained no "
            "<STATUS:done> or <STATUS:blocked> tag.\n"
            "Observer raw output was:\n"
            f"{txt[:600]}\n\n"
            "Re-examine the execution result and emit exactly one of:\n"
            "  <STATUS:done>   if execution succeeded\n"
            "  <STATUS:blocked> <explanation of what failed and how to fix it>"
        )
        return "blocked", _parse_fail_msg


    # # ---------- VALIDATORS (example: alignment, generic fallback) ----------
    # def detect_alignment_step(title: str) -> bool:
    #     t = title.lower()
    #     return any(k in t for k in ["align", "alignment", "bwa", "minimap", "map reads"])

    # def validate_alignment(manifest: Dict[str,Any]) -> List[str]:
    #     missing = []
    #     reads = manifest.get("reads", {})
    #     ref   = manifest.get("reference", {})
    #     if not reads.get("r1"): missing.append("Reads R1 (FASTQ or accession)")
    #     if reads.get("paired") and not reads.get("r2"): missing.append("Reads R2 (paired)")
    #     if not (ref.get("fasta") or ref.get("accession")): missing.append("Reference (FASTA or accession)")
    #     if not manifest.get("read_type"): missing.append("Read type (short/long)")
    #     return missing

    # def compute_missing_for_step(step: Step, manifest: Dict[str,Any]) -> List[str]:
    #     title = step["title"]
    #     if detect_alignment_step(title):
    #         return validate_alignment(manifest)
    #     # add more per-step validators here
    #     return []  # default: no special requirements
