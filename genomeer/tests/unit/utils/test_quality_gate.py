import pytest
import os
import re
import time
import json
import tempfile
from pathlib import Path

class TestQualityGateRegex:
    """BUG 5: Toutes les parse_regex dans BIOLOGICAL_GATES doivent compiler."""

    def test_all_parse_regex_compile_without_error(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        errors = []
        for tool_name, gate in BIOLOGICAL_GATES.items():
            regex_str = gate.get("parse_regex")
            if regex_str:
                try:
                    re.compile(regex_str, re.IGNORECASE)
                except re.error as e:
                    errors.append(f"  - {tool_name}: re.error: {e} (regex={regex_str!r})")
        assert not errors, "Invalid regex patterns found:\\n" + "\\n".join(errors)

    def test_virsorter2_gate_exists(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        assert "run_virsorter2" in BIOLOGICAL_GATES, (
            "run_virsorter2 missing from BIOLOGICAL_GATES — add viromics quality gate"
        )

    def test_checkv_gate_exists(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        assert "run_checkv" in BIOLOGICAL_GATES, (
            "run_checkv missing from BIOLOGICAL_GATES — add viral completeness gate"
        )

    def test_virsorter2_regex_matches_expected_output(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        gate = BIOLOGICAL_GATES.get("run_virsorter2")
        if not gate or not gate.get("parse_regex"):
            pytest.skip("run_virsorter2 gate not configured")
        # Simuler un output type VirSorter2
        test_text = 'n_viral_sequences: 42'
        m = re.search(gate["parse_regex"], test_text, re.IGNORECASE)
        assert m is not None, f"Regex {gate['parse_regex']!r} did not match '{test_text}'"
        assert m.group(1) == "42"

    def test_checkv_regex_matches_expected_output(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        gate = BIOLOGICAL_GATES.get("run_checkv")
        if not gate or not gate.get("parse_regex"):
            pytest.skip("run_checkv gate not configured")
        test_text = "mean_completeness: 75.3"
        m = re.search(gate["parse_regex"], test_text, re.IGNORECASE)
        assert m is not None, f"Regex {gate['parse_regex']!r} did not match '{test_text}'"
        assert float(m.group(1)) == pytest.approx(75.3)

    def test_check_quality_returns_fail_on_none_result_dict(self):
        """Fix 7: result_dict=None avec metric_key défini → fail"""
        from genomeer.agent.v2.utils.quality_gate import check_quality
        level, msg = check_quality("run_fastp", result_dict=None, stdout_text="")
        assert level == "fail", f"Expected 'fail' for None result_dict, got '{level}'"
        assert "QA-FAIL" in msg

    def test_check_quality_returns_warn_on_missing_metric(self):
        """Fix 7: result_dict={} sans clé métrique → warn (pas ok silencieux)"""
        from genomeer.agent.v2.utils.quality_gate import check_quality
        level, msg = check_quality("run_fastp", result_dict={}, stdout_text="no metrics here")
        assert level in ("warn", "fail"), (
            f"Expected 'warn' or 'fail' when metric missing, got '{level}'. "
            "The gate should never return 'ok' silently."
        )


# ===========================================================================
# BUG #6 — record_step_start produit des durées non nulles
# ===========================================================================

