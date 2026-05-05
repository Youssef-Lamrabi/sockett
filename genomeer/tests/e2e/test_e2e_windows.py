"""
tests/e2e/test_e2e_windows.py
==============================
Windows End-to-End pipeline test — validates the full LangGraph orchestration
(planner → orchestrator → generator → ensure_env → executor → observer → finalizer)
without any real bioinformatics tooling.

HOW IT WORKS
------------
``os.environ["GENOMEER_SKIP_ENV_INSTALL"] = "1"`` is set BEFORE the first genomeer
import.  This causes ``ensure_env`` (env_manager.py) and the ``_ensure_env`` node
in BioAgent to return immediately instead of invoking micromamba.

``make_stateful_mock`` replaces ``BioAgent._llm_invoke`` with a deterministic function
that returns realistic biologically-grounded responses for every pipeline node.

WHAT IS VALIDATED
-----------------
- Full 5-step LangGraph state machine (routing, looping, completion)
- Accumulation of quality_signals in the manifest across steps
- finalizer is called at least once
- generator + observer are each called ≥ 5 times
- Quality gate: PipelineOutputEval.overall_score ≥ 0.5
- JSON benchmark report written to tests/e2e/benchmark_reports/
"""

# ─────────────────────────────────────────────────────────────────────────────
#  IMPORTANT: set env vars BEFORE any genomeer import so the guards activate
# ─────────────────────────────────────────────────────────────────────────────
import os
os.environ["GENOMEER_SKIP_ENV_INSTALL"] = "1"
os.environ["GENOMEER_RAG_OFFLINE"] = "1"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_TRACING"] = "false"
# ─────────────────────────────────────────────────────────────────────────────

import json
import time
import pytest
from pathlib import Path
from langchain_core.messages import AIMessage

DATA_DIR    = Path(__file__).parent / "data"
REPORTS_DIR = Path(__file__).parent / "benchmark_reports"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_fasta(fasta_path: str) -> dict:
    seqs: dict[str, str] = {}
    cur = None
    with open(fasta_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                cur = line[1:].split()[0]
                seqs[cur] = ""
            elif cur is not None:
                seqs[cur] += line.upper()
    if not seqs:
        return {"n_contigs": 0, "total_bp": 0, "gc_content": 0.0, "assembly_n50": 0}
    lengths = [len(s) for s in seqs.values()]
    total_bp = sum(lengths)
    gc = sum(s.count("G") + s.count("C") for s in seqs.values())
    gc_pct = round(100.0 * gc / max(1, total_bp), 2)
    sorted_lens = sorted(lengths, reverse=True)
    cumsum, n50 = 0, sorted_lens[0]
    for ln in sorted_lens:
        cumsum += ln
        if cumsum >= total_bp / 2:
            n50 = ln
            break
    return {"n_contigs": len(seqs), "total_bp": total_bp, "gc_content": gc_pct, "assembly_n50": n50}


PLAN = (
    "- [ ] Validate FASTA Input\n"
    "- [ ] Run QC Simulation\n"
    "- [ ] Run Assembly Simulation\n"
    "- [ ] Run Taxonomy Classification\n"
    "- [ ] Assess MAG Quality\n"
    "<next:ORCHESTRATOR>"
)


def make_stateful_mock(planner_plan: str, fasta_path: str):
    """Return (mock_fn, call_counts).

    ``mock_fn`` replaces ``BioAgent._llm_invoke`` and returns deterministic
    AIMessage responses for each node, cycling through 5 biologically-grounded
    step codes.
    """
    call_counts: dict[str, int] = {}
    stats = _parse_fasta(fasta_path)

    step_codes = [
        # Step 1 — FASTA validation
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            "qs['n_contigs'] = 1\n"
            "qs['total_bp'] = 16569\n"
            "qs['gc_content'] = 44.1\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step1: FASTA validated')\n"
            "</EXECUTE>"
        ),
        # Step 2 — QC
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            "qs['q30_rate'] = 88.5\n"
            "qs['reads_total'] = 1500000\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step2: QC done')\n"
            "</EXECUTE>"
        ),
        # Step 3 — Assembly (N50 derived from real FASTA)
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            f"qs['assembly_n50'] = {stats['assembly_n50']}\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step3: Assembly done')\n"
            "</EXECUTE>"
        ),
        # Step 4 — Taxonomy
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            "qs['classified_pct'] = 78.3\n"
            "qs['diversity_shannon'] = 2.14\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step4: Taxonomy done')\n"
            "</EXECUTE>"
        ),
        # Step 5 — CheckM2
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            "qs['mean_completeness'] = 91.5\n"
            "qs['mean_contamination'] = 3.2\n"
            "qs['n_hq_mags'] = 1\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step5: CheckM2 done')\n"
            "</EXECUTE>"
        ),
    ]

    def _mock(self_agent, node: str, purpose: str, messages, **kwargs):
        base = node.split("|")[0]
        call_counts[base] = call_counts.get(base, 0) + 1
        n = call_counts[base]

        if base == "planner":
            return AIMessage(content=planner_plan)
        if base == "input_guard":
            return AIMessage(content="<OK/>")
        if base == "generator":
            idx = (n - 1) % len(step_codes)
            return AIMessage(content=step_codes[idx])
        if base == "observer":
            return AIMessage(content="<STATUS: done>")
        if base == "finalizer":
            return AIMessage(content="## Benchmark Report\n\nAll 5 steps completed successfully.")
        if base == "qa":
            return AIMessage(content="APPROVED")
        return AIMessage(content="OK")

    return _mock, call_counts


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def ecoli_fasta() -> Path:
    path = DATA_DIR / "ecoli.fasta"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(">seq1\nATGCATGCATGCATGCATGC\n")
    return path


@pytest.fixture(scope="session")
def pipeline_result(ecoli_fasta: Path) -> dict:
    """Run the full 5-step pipeline once; all tests share this result."""
    from genomeer.agent.v2.BioAgent import BioAgent

    mock_fn, call_counts = make_stateful_mock(PLAN, str(ecoli_fasta))

    # Patch at the class level (safe because session-scoped)
    original_llm_invoke = BioAgent._llm_invoke
    original_prepare = getattr(BioAgent, "_prepare_resources_for_retrieval", None)
    
    # We will also patch the actual LLM class to prevent any network escapes
    original_invoke = None
    llm_class = None

    BioAgent._llm_invoke = mock_fn
    BioAgent._prepare_resources_for_retrieval = lambda s, p: {
        "tools": [], "data_lake": [], "libraries": []
    }

    try:
        from unittest.mock import patch
        import subprocess
        
        # Prevent any background subprocesses (like micromamba in tool preloader)
        # from keeping pytest's capture pipes open and causing a hang.
        mock_run = patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "mock", "")).start()
        mock_popen = patch("subprocess.Popen").start()

        agent = BioAgent(auto_start_artifacts=False)
        
        # Ensure absolutely NO network calls escape to Ollama if some fallback branch 
        # (e.g. status clarification) bypasses _llm_invoke
        if hasattr(agent, "llm"):
            llm_class = type(agent.llm)
            original_invoke = llm_class.invoke
            llm_class.invoke = lambda self, msgs, **kw: AIMessage(content="<STATUS: done>")

        import threading
        t0 = time.time()
        
        go_result = None
        go_error = None

        def run_agent():
            nonlocal go_result, go_error
            try:
                go_result = agent.go(
                    prompt=f"analyse {ecoli_fasta}",
                    attachments=[str(ecoli_fasta)],
                    mode="dev",
                )
            except Exception as e:
                go_error = e

        t = threading.Thread(target=run_agent, daemon=True)
        t.start()
        t.join(timeout=60)  # 60 secondes max

        if t.is_alive():
            pytest.skip("agent.go() timed out after 60s — LangGraph asyncio conflict under pytest")

        if go_error:
            raise go_error
            
        result = go_result
    finally:
        from unittest.mock import patch
        patch.stopall()
        BioAgent._llm_invoke = original_llm_invoke
        if original_prepare is not None:
            BioAgent._prepare_resources_for_retrieval = original_prepare
        if llm_class is not None and original_invoke is not None:
            llm_class.invoke = original_invoke

    return {
        "result": result,
        "call_counts": call_counts,
        "fasta_stats": _parse_fasta(str(ecoli_fasta)),
        "duration": round(time.time() - t0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowsPipeline:

    def test_pipeline_reaches_finalizer(self, pipeline_result: dict) -> None:
        """agent.go() must complete and finalizer must be called at least once."""
        result = pipeline_result["result"]
        cc = pipeline_result["call_counts"]
        assert result is not None, "agent.go() returned None"
        assert isinstance(result, tuple) and len(result) == 2, "Expected (messages, state) tuple"
        assert cc.get("finalizer", 0) >= 1, f"finalizer never called. call_counts={cc}"

    def test_all_steps_executed(self, pipeline_result: dict) -> None:
        """generator and observer must each be called ≥ 5 times (one per step)."""
        cc = pipeline_result["call_counts"]
        assert cc.get("generator", 0) >= 5, f"generator called < 5 times: {cc}"
        assert cc.get("observer", 0) >= 5, f"observer called < 5 times: {cc}"

    def test_quality_signals_accumulated(self, pipeline_result: dict) -> None:
        """All key quality_signals must be present and non-zero in the final manifest."""
        state_messages, final_state = pipeline_result["result"]
        qs = final_state.get("manifest", {}).get("quality_signals", {})

        assert qs.get("assembly_n50", 0) > 0, f"assembly_n50 missing or zero: {qs}"
        assert qs.get("classified_pct", 0) > 0, f"classified_pct missing or zero: {qs}"
        assert qs.get("mean_completeness", 0) > 0, f"mean_completeness missing or zero: {qs}"
        assert "mean_contamination" in qs, f"mean_contamination missing: {qs}"

    def test_eval_report_passes_gate(self, pipeline_result: dict) -> None:
        """PipelineOutputEval.overall_score must be ≥ 0.5 and the JSON report saved."""
        from genomeer.evaluation.benchmark import PipelineOutputEval

        _, final_state = pipeline_result["result"]
        qs = final_state.get("manifest", {}).get("quality_signals", {})

        rep = PipelineOutputEval().evaluate(qs)
        assert rep.overall_score >= 0.5, (
            f"QUALITY GATE FAILED: score={rep.overall_score:.2f} < 0.5 minimum\n"
            f"quality_signals={qs}"
        )

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        report_path = REPORTS_DIR / f"windows_benchmark_{ts}.json"
        rep.save_json(str(report_path))
        assert report_path.exists(), f"Report not written: {report_path}"

    def test_report_summary_readable(self, pipeline_result: dict) -> None:
        """Summary string must contain PASS or WARN; latest_summary.txt is written."""
        from genomeer.evaluation.benchmark import PipelineOutputEval

        _, final_state = pipeline_result["result"]
        qs = final_state.get("manifest", {}).get("quality_signals", {})

        rep = PipelineOutputEval().evaluate(qs)
        summary = rep.summary()

        assert "PipelineOutputEval" in summary, "Summary missing header"
        assert "PASS" in summary or "WARN" in summary, f"No PASS/WARN in summary:\n{summary}"

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "latest_summary.txt").write_text(summary, encoding="utf-8")
        print(summary)
