import pytest
from genomeer.agent.v2.utils.quality_gate import check_quality

def test_quality_gate_ok():
    level, msg = check_quality("run_fastp", {"q30_rate": 0.85})
    assert level == "ok"
    assert "0.85" in msg

def test_quality_gate_fail_on_zero():
    level, msg = check_quality("run_metabat2", {"n_bins": 0})
    assert level == "fail"

def test_quality_gate_missing_metric():
    level, msg = check_quality("run_fastp", None)
    assert level == "fail"
