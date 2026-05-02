"""
genomeer/src/genomeer/agent/v2/utils/state_graph.py
=====================================================
StateGraphHelper — parsers and utilities for the v2 agent graph.

CHANGES vs previous version:
  - parse_execute(): now also extracts optional `env` attribute from <EXECUTE env="..."> tag
  - sanitize_execute_block(): unchanged
  - All other methods unchanged
"""

import re


class StateGraphHelper:
    # ---------- PARSERS ----------
    RX_NEXT    = re.compile(r"<next:(QA|ORCHESTRATOR)>", re.I)
    RX_EXEC    = re.compile(r"<EXECUTE[^>]*>(.*?)</EXECUTE>", re.S | re.I)
    RX_EXEC_ENV = re.compile(r'<EXECUTE[^>]*\benv=["\']([^"\']+)["\']', re.I)
    RX_LANG    = re.compile(r"^\s*#!(PY|R|BASH|CLI)\s*$", re.I | re.M)
    RX_MISSING = re.compile(r"<MISSING>(.*?)</MISSING>", re.S | re.I)
    RX_PRESENT = re.compile(r"<PRESENT>(.*?)</PRESENT>", re.S | re.I)
    RX_OK      = re.compile(r"<OK\s*/\s*>", re.I)
    RX_STATUS_WRAPPED = re.compile(r"<STATUS\s*:\s*(done|blocked)\s*>(.*?)</STATUS>", re.S | re.I)
    RX_STATUS_INLINE = re.compile(r'<\s*STATUS\s*:\s*(done|blocked)\s*>', re.IGNORECASE)
    _RX_STATUS_DONE = re.compile(r'<\s*STATUS\s*:\s*(?:done|success|completed?)\s*>', re.IGNORECASE)
    _RX_STATUS_BLOCKED = re.compile(r'<\s*STATUS\s*:\s*(?:blocked|failed|error)\s*>', re.IGNORECASE)

    @staticmethod
    def parse_checklist_and_route(text: str):
        m = StateGraphHelper.RX_NEXT.search(text or "")
        route = m.group(1).upper() if m else "ORCHESTRATOR"
        steps = []
        for line in (text or "").splitlines():
            line = line.strip()
            if line.startswith("- [ ]"):
                title = line[5:].strip()
                if title:
                    steps.append({"title": title, "status": "todo", "notes": ""})
        return steps, ("qa" if route == "QA" else "orchestrator")

    @staticmethod
    def sanitize_execute_block(raw: str) -> str:
        if not raw:
            return ""
        text = raw
        text = re.sub(r"<\s*/\s*execute\s*>", "</EXECUTE>", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*execute\b([^>]*)>", lambda m: "<EXECUTE" + m.group(1) + ">", text, flags=re.IGNORECASE)
        end = text.upper().find("</EXECUTE>")
        if end == -1:
            text = text + "</EXECUTE>"
        else:
            text = text[: end + len("</EXECUTE>")]
        return text

    @staticmethod
    def parse_execute(text: str):
        """
        Parse code, language, and optional env from an <EXECUTE> block.
        Returns (code, lang, env_hint) where:
          - code: str | None
          - lang: 'PY' | 'R' | 'BASH' | 'CLI' | None
          - env_hint: str | None  — value of env="..." attribute if present
        """
        code = None
        lang = None
        env_hint = None

        m = StateGraphHelper.RX_EXEC.search(text or "")
        if m:
            code = m.group(1).strip()
            lm = StateGraphHelper.RX_LANG.search(code or "")
            if lm:
                lang = lm.group(1).upper()

        # Check for env="..." attribute on the tag
        em = StateGraphHelper.RX_EXEC_ENV.search(text or "")
        if em:
            env_hint = em.group(1).strip()

        return code, lang, env_hint

    @staticmethod
    def parse_missing_ok(text: str):
        if StateGraphHelper.RX_OK.search(text or ""):
            mp = StateGraphHelper.RX_PRESENT.search(text or "")
            if not mp:
                return [], True
            items = [x.strip("- ").strip() for x in mp.group(1).strip().splitlines() if x.strip()]
            return items, True
        mm = StateGraphHelper.RX_MISSING.search(text or "")
        if not mm:
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
        for _m in StateGraphHelper.RX_STATUS_INLINE.finditer(txt):
            m2 = _m
        if m2:
            status = m2.group(1).lower()
            summary = txt[:m2.start()].strip()
            summary = re.sub(r"</?[^>]+>", "", summary).strip()
            return status, summary

        if StateGraphHelper._RX_STATUS_DONE.search(txt):
            summary = re.sub(r"</?[^>]+>", "", txt).strip()
            return "done", summary
            
        if StateGraphHelper._RX_STATUS_BLOCKED.search(txt):
            summary = re.sub(r"</?[^>]+>", "", txt).strip()
            return "blocked", summary

        return "unknown", "Could not parse OBSERVER status."