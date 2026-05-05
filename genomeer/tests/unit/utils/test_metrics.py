import pytest
import os
import re
import time
import json
import tempfile
from pathlib import Path

class TestMetricsDuration:
    """BUG 6: record_step_start doit être appelé pour obtenir des durées correctes."""

    def test_duration_is_nonzero_when_start_recorded(self):
        from genomeer.utils.metrics import RunMetrics
        m = RunMetrics("test_session", "/tmp")
        m.record_step_start(0, "fastp_qc_step")
        time.sleep(0.05)
        m.record_step_end(0, "fastp_qc_step", status="done", tool_name="run_fastp")
        assert len(m.steps) >= 1
        step = next(s for s in m.steps if s.step_idx == 0)
        assert step.duration_sec > 0.0, (
            f"Duration is {step.duration_sec}s — record_step_start must be called "
            "in _executor before record_step_end in _observer."
        )
        assert step.duration_sec < 10.0, "Duration seems unrealistically long"

    def test_duration_is_zero_without_start(self):
        """Démontrer le bug original: sans start, duration=0."""
        from genomeer.utils.metrics import RunMetrics
        m = RunMetrics("test_session_bug", "/tmp")
        # Ne PAS appeler record_step_start — simule le bug
        time.sleep(0.05)
        m.record_step_end(0, "assembly_step", status="done")
        step = next((s for s in m.steps if s.step_idx == 0), None)
        if step:
            # duration_sec sera 0 car started=now dans record_step_end
            assert step.duration_sec < 0.01, (
                "Without record_step_start, duration should be ~0 (demonstrating the bug)"
            )

    def test_metrics_save_produces_valid_json(self, tmp_path):
        from genomeer.utils.metrics import RunMetrics
        m = RunMetrics("json_test_session", str(tmp_path))
        m.record_step_start(0, "qc_step")
        time.sleep(0.01)
        m.record_step_end(0, "qc_step", status="done", tool_name="run_fastp")
        m.record_llm_call(cache_hit=False)
        m.record_llm_call(cache_hit=True)
        output_path = m.save(str(tmp_path))
        assert Path(output_path).exists()
        with open(output_path) as f:
            data = json.load(f)
        assert data["session_id"] == "json_test_session"
        assert data["summary"]["steps_done"] == 1
        assert data["summary"]["llm_calls"] == 2
        assert data["summary"]["llm_cache_hits"] == 1
        assert data["steps"][0]["duration_sec"] > 0

    def test_metrics_reset_between_runs(self, tmp_path):
        """WARN 3: _metrics doit être réinitialisé entre runs."""
        from genomeer.utils.metrics import RunMetrics
        m1 = RunMetrics("run1", str(tmp_path))
        m1.record_step_start(0, "step_0")
        m1.record_step_end(0, "step_0", status="done")
        assert len(m1.steps) == 1

        # Simuler un deuxième run — DOIT être une nouvelle instance
        m2 = RunMetrics("run2", str(tmp_path))
        assert len(m2.steps) == 0, (
            "New RunMetrics instance should start with 0 steps. "
            "Use self._metrics = RunMetrics(...) unconditionally in run()."
        )


# ===========================================================================
# WARN #1 — BioRAG join() avant _finalizer
# ===========================================================================

