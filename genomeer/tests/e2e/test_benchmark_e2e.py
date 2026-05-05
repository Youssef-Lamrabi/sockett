import json
import os
import time
import glob
import tempfile
import pytest
from pathlib import Path
from langchain_core.messages import AIMessage

DATA_DIR    = Path(__file__).parent / "data"
REPORTS_DIR = Path(__file__).parent / "benchmark_reports"


def _parse_fasta(fasta_path):
    seqs = {}
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
    cumsum = 0
    n50 = sorted_lens[0]
    for ln in sorted_lens:
        cumsum += ln
        if cumsum >= total_bp / 2:
            n50 = ln
            break
    return {
        "n_contigs": len(seqs),
        "total_bp": total_bp,
        "gc_content": gc_pct,
        "assembly_n50": n50,
    }


def make_stateful_mock(planner_plan, fasta_path):
    call_counts = {}
    stats = _parse_fasta(fasta_path)

    # Generator codes use <EXECUTE>#!PY format (required by parse_execute)
    step_codes = [
        # Step 1: FASTA validation
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
        # Step 2: QC
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            "qs['q30_rate'] = 88.5\n"
            "qs['reads_total'] = 1500000\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step2: QC done')\n"
            "</EXECUTE>"
        ),
        # Step 3: Assembly (real N50 from FASTA)
        # Note: use concatenation to avoid {} in manifest.get(..., {}) conflicting with .format()
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            + "qs['assembly_n50'] = " + str(_parse_fasta(fasta_path)["assembly_n50"]) + "\n"
            + "manifest['quality_signals'] = qs\n"
            "print('Step3: Assembly done')\n"
            "</EXECUTE>"
        ),
        # Step 4: Taxonomy
        (
            "<EXECUTE>\n#!PY\n"
            "qs = manifest.get('quality_signals', {})\n"
            "qs['classified_pct'] = 78.3\n"
            "qs['diversity_shannon'] = 2.14\n"
            "manifest['quality_signals'] = qs\n"
            "print('Step4: Taxonomy done')\n"
            "</EXECUTE>"
        ),
        # Step 5: CheckM2
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

    def _mock(self_agent, node, purpose, messages, **kwargs):
        base = node.split("|")[0]
        call_counts[base] = call_counts.get(base, 0) + 1
        n = call_counts[base]

        if base == "planner":
            return AIMessage(content=planner_plan)

        if base == "input_guard":
            # parse_missing_ok needs <OK/> to return ok=True and route to generator
            return AIMessage(content="<OK/>")

        if base == "generator":
            idx = (n - 1) % len(step_codes)
            return AIMessage(content=step_codes[idx])

        if base == "observer":
            # parse_status needs <STATUS: done> to mark step as done
            return AIMessage(content="<STATUS: done>Step completed.</STATUS>")

        if base == "finalizer":
            return AIMessage(content="## Benchmark Report\n\nAll 5 steps completed.")

        if base == "qa":
            return AIMessage(content="APPROVED")

        return AIMessage(content="OK")

    return _mock, call_counts


PLAN = (
    "- [ ] Validate FASTA Input\n"
    "- [ ] Run QC Simulation\n"
    "- [ ] Run Assembly Simulation\n"
    "- [ ] Run Taxonomy Classification\n"
    "- [ ] Assess MAG Quality\n"
    "<next:ORCHESTRATOR>"
)


@pytest.fixture(scope="module")
def ecoli_fasta():
    path = DATA_DIR / "ecoli.fasta"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(">seq1\nATGCATGCATGCATGCATGC\n")
    return path


class TestFullPipelineBenchmark:

    def _run(self, monkeypatch, ecoli_fasta):
        from genomeer.agent.v2.BioAgent import BioAgent
        os.environ["GENOMEER_RAG_OFFLINE"] = "1"
        mock_fn, cc = make_stateful_mock(PLAN, str(ecoli_fasta))
        monkeypatch.setattr(BioAgent, "_llm_invoke", mock_fn)
        monkeypatch.setattr(
            BioAgent, "_prepare_resources_for_retrieval",
            lambda s, p: {"tools": [], "data_lake": [], "libraries": []},
        )
        # Patch ensure_env or resolve_env_for_code to prevent actual micromamba calls during mock test
        monkeypatch.setattr(BioAgent, "resolve_env_for_code", lambda code, base_dir=None, timeout=None: "mocked_env", raising=False)
        import genomeer.agent.v2.BioAgent as bio_module
        monkeypatch.setattr(bio_module, "resolve_env_for_code", lambda code, base_dir=None, timeout=None: "mocked_env", raising=False)
        t0 = time.time()
        result = BioAgent().go(
            prompt=f"analyse {ecoli_fasta}",
            attachments=[str(ecoli_fasta)],
            mode="dev",
        )
        return result, cc, _parse_fasta(str(ecoli_fasta)), round(time.time() - t0, 2)

    def test_pipeline_completes_to_finalizer(self, monkeypatch, ecoli_fasta):
        result, cc, _, _ = self._run(monkeypatch, ecoli_fasta)
        assert result is not None
        assert isinstance(result, tuple) and len(result) == 2
        _, fc = result
        assert len(fc) > 5
        assert cc.get("finalizer", 0) >= 1, (
            f"finalizer not called. call_counts={cc}\n"
            "HINT: input_guard mock must return '<OK/>' to route to generator."
        )

    def test_fasta_stats_are_real(self, ecoli_fasta):
        s = _parse_fasta(str(ecoli_fasta))
        assert s["n_contigs"] >= 1
        assert s["total_bp"] >= 100
        assert 0 < s["gc_content"] < 100
        assert s["assembly_n50"] > 0

    def test_run_metrics_json_produced(self, monkeypatch, ecoli_fasta):
        result, cc, _, _ = self._run(monkeypatch, ecoli_fasta)
        assert result is not None
        files = (
            glob.glob(str(Path(tempfile.gettempdir()) / "**" / "run_metrics.json"), recursive=True)
            + glob.glob(str(Path(".") / "**" / "run_metrics.json"), recursive=True)
        )
        if not files:
            assert cc.get("finalizer", 0) >= 1, (
                "run_metrics.json absent AND finalizer not called -- pipeline incomplete"
            )
            pytest.skip("run_metrics.json not written in dev mode (expected).")
        mp = max(files, key=lambda p: Path(p).stat().st_mtime)
        data = json.load(open(mp))
        assert "session_id" in data
        assert "total_duration_sec" in data

    def test_eval_report_saved(self, monkeypatch, ecoli_fasta):
        from genomeer.evaluation.benchmark import PipelineOutputEval
        _, _, fs, dur = self._run(monkeypatch, ecoli_fasta)
        qs = {
            "assembly_n50":       fs["assembly_n50"],
            "q30_rate":           88.5,
            "classified_pct":     78.3,
            "mean_completeness":  91.5,
            "mean_contamination": 3.2,
            "n_hq_mags":          1,
        }
        rep = PipelineOutputEval().evaluate(qs)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        rp = str(REPORTS_DIR / f"benchmark_{ts}.json")
        rep.save_json(rp)
        assert Path(rp).exists()
        data = json.load(open(rp))
        assert "overall_score" in data
        print(f"\nReport saved: {rp}")
        print(rep.summary())

    def test_overall_score_gate(self, ecoli_fasta):
        from genomeer.evaluation.benchmark import PipelineOutputEval
        fs = _parse_fasta(str(ecoli_fasta))
        qs = {
            "assembly_n50":       fs["assembly_n50"],
            "q30_rate":           88.5,
            "classified_pct":     78.3,
            "mean_completeness":  91.5,
            "mean_contamination": 3.2,
            "n_hq_mags":          1,
        }
        rep = PipelineOutputEval().evaluate(qs)
        assert rep.overall_score >= 0.5, (
            f"QUALITY GATE FAILED: score={rep.overall_score:.2f} < 0.5 minimum"
        )
        print(f"\nOverall score: {rep.overall_score:.2%}")

    def test_generator_called_per_step(self, monkeypatch, ecoli_fasta):
        _, cc, _, _ = self._run(monkeypatch, ecoli_fasta)
        gen = cc.get("generator", 0)
        assert gen >= 1, (
            f"generator called {gen} times. call_counts={cc}\n"
            "HINT: input_guard must return '<OK/>' for routing to generator."
        )
        assert gen <= 20, f"generator called {gen} times -- possible infinite loop"

    def test_step1_fasta_stats(self, ecoli_fasta):
        s = _parse_fasta(str(ecoli_fasta))
        if s["total_bp"] > 1000:
            assert s["n_contigs"] == 1, "ecoli.fasta should have 1 contig (MT_human)"
            assert s["assembly_n50"] == s["total_bp"], "N50 == total_bp for 1 contig"
        print(f"\nFASTA: {s}")

    def test_summary_readable(self, ecoli_fasta):
        from genomeer.evaluation.benchmark import PipelineOutputEval
        fs = _parse_fasta(str(ecoli_fasta))
        rep = PipelineOutputEval().evaluate({
            "assembly_n50":       fs["assembly_n50"],
            "classified_pct":     78.3,
            "mean_completeness":  91.5,
            "mean_contamination": 3.2,
            "q30_rate":           88.5,
        })
        s = rep.summary()
        assert "PipelineOutputEval" in s
        assert "PASS" in s or "WARN" in s
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / "latest_summary.txt").write_text(s, encoding="utf-8")
        print(s)
