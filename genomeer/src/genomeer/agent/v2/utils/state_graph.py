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
    RX_STATUS_INLINE  = re.compile(r"<STATUS\s*:\s*(done|blocked)\s*>", re.I)


    @staticmethod
    def parse_checklist_and_route(text: str):
        txt = text or ""
        m = StateGraphHelper.RX_NEXT.search(txt)

        steps = []
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("- [ ]"):
                title = line[5:].strip()
                if title:
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

        return "blocked", "Could not parse OBSERVER status."


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
