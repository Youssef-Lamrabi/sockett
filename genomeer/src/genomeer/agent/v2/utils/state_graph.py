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
        m = StateGraphHelper.RX_NEXT.search(text or "")
        route = m.group(1).upper() if m else "ORCHESTRATOR"
        # steps = lines starting with "- [ ]" (todo only at first)
        steps = []
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith("- [ ]"):
                title = line[5:].strip()
                if title:
                    steps.append({"title": title, "status": "todo", "notes": ""})
        return steps, ("qa" if route=="QA" else "orchestrator")

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

    @staticmethod
    def parse_execute(text: str):
        code = None
        lang = None
        m = StateGraphHelper.RX_EXEC.search(text or "")
        if m:
            code = m.group(1).strip()
            lm = StateGraphHelper.RX_LANG.search(code or "")
            if lm:
                lang = lm.group(1).upper()
        return code, lang

    @staticmethod
    def parse_missing_ok(text: str):
        if StateGraphHelper.RX_OK.search(text or ""):
            # return [], True
            mp = StateGraphHelper.RX_PRESENT.search(text or "")
            if not mp:
                return [], True
            items = [x.strip("- ").strip() for x in mp.group(1).strip().splitlines() if x.strip()]
            return items, True
        mm = StateGraphHelper.RX_MISSING.search(text or "")
        if not mm:
            # treat as missing if we couldn't parse
            return ["Could not parse INPUT_VALIDATOR response."], False
        items = [x.strip("- ").strip() for x in mm.group(1).strip().splitlines() if x.strip()]
        return items, False


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
