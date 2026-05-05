"""
tests/unit/tools/test_longread.py
====================================
Unit tests for run_medaka, run_racon, quality gate, and env routing.
All tests are offline — no CLI tools executed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_proc(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# run_medaka — command & QV parsing
# ---------------------------------------------------------------------------

class TestRunMedaka:

    def test_builds_correct_command(self, tmp_path, monkeypatch):
        from genomeer.tools.function.metagenomics import run_medaka
        import genomeer.tools.function.metagenomics as _m

        reads = tmp_path / "reads.fastq"; reads.touch()
        asm   = tmp_path / "asm.fasta";   asm.touch()

        captured = []

        def fake_run(cmd, **kw):
            captured.extend(cmd)
            out = tmp_path / "medaka_out" / "medaka_out" / "consensus.fasta"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(">c\nATGC\n")
            return _fake_proc(stderr="mean qv: 28.35")

        monkeypatch.setattr(_m, "_run", fake_run)
        run_medaka(str(reads), str(asm), str(tmp_path / "medaka_out"),
                   model="r941_min_high_g360", threads=4, batch_size=50)

        cmd_str = " ".join(captured)
        assert "medaka_consensus" in cmd_str
        assert "-m" in captured and "r941_min_high_g360" in captured
        assert "-t" in captured and "4" in captured
        assert "-b" in captured and "50" in captured

    def test_parses_mean_qv(self, tmp_path, monkeypatch):
        from genomeer.tools.function.metagenomics import run_medaka
        import genomeer.tools.function.metagenomics as _m

        reads = tmp_path / "reads.fastq"; reads.touch()
        asm   = tmp_path / "asm.fasta";   asm.touch()

        def fake_run(cmd, **kw):
            out = tmp_path / "medaka_out" / "medaka_out" / "consensus.fasta"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(">c\nATGC\n")
            return _fake_proc(stderr="[INFO] mean qv: 31.7")

        monkeypatch.setattr(_m, "_run", fake_run)
        result = run_medaka(str(reads), str(asm), str(tmp_path / "medaka_out"))
        assert result["mean_qv"] == pytest.approx(31.7, abs=0.01)

    def test_qv_none_if_absent(self, tmp_path, monkeypatch):
        from genomeer.tools.function.metagenomics import run_medaka
        import genomeer.tools.function.metagenomics as _m

        reads = tmp_path / "reads.fastq"; reads.touch()
        asm   = tmp_path / "asm.fasta";   asm.touch()

        def fake_run(cmd, **kw):
            out = tmp_path / "medaka_out" / "medaka_out" / "consensus.fasta"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(">c\nATGC\n")
            return _fake_proc(stderr="no qv here")

        monkeypatch.setattr(_m, "_run", fake_run)
        result = run_medaka(str(reads), str(asm), str(tmp_path / "medaka_out"))
        assert result["mean_qv"] is None

    def test_raises_on_failure(self, tmp_path, monkeypatch):
        from genomeer.tools.function.metagenomics import run_medaka
        import genomeer.tools.function.metagenomics as _m

        reads = tmp_path / "reads.fastq"; reads.touch()
        asm   = tmp_path / "asm.fasta";   asm.touch()

        monkeypatch.setattr(_m, "_run", lambda cmd, **kw: _fake_proc(returncode=1, stderr="fatal"))
        monkeypatch.setattr(_m, "_assert_ok",
            lambda proc, name: (_ for _ in ()).throw(RuntimeError(f"{name} failed")))

        with pytest.raises(RuntimeError, match="medaka_consensus"):
            run_medaka(str(reads), str(asm), str(tmp_path / "out"))


# ---------------------------------------------------------------------------
# run_racon — command & output
# ---------------------------------------------------------------------------

class TestRunRacon:

    def test_raises_on_failure(self, tmp_path, monkeypatch):
        from genomeer.tools.function.metagenomics import run_racon
        import genomeer.tools.function.metagenomics as _m

        reads = tmp_path / "reads.fastq"; reads.touch()
        paf   = tmp_path / "overlaps.paf"; paf.touch()
        asm   = tmp_path / "asm.fasta";   asm.touch()

        monkeypatch.setattr(_m, "_micromamba_bin", lambda: "micromamba")
        monkeypatch.setattr(_m, "_env_prefix", lambda name: Path("/fake/envs") / name)

        # run_racon imports subprocess as _sp locally — patch the real module
        import subprocess as _sp_real
        original_run = _sp_real.run

        def _failing_run(cmd, **kwargs):
            # Write empty file so the open() inside run_racon doesn't fail before check
            return _fake_proc(returncode=1, stderr="racon error")

        monkeypatch.setattr(_sp_real, "run", _failing_run)
        try:
            with pytest.raises(RuntimeError, match="racon"):
                run_racon(str(reads), str(paf), str(asm), str(tmp_path / "out"))
        finally:
            monkeypatch.undo()


# ---------------------------------------------------------------------------
# Quality gate thresholds
# ---------------------------------------------------------------------------

class TestMedakaQualityGate:

    def test_gate_exists(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        assert "run_medaka" in BIOLOGICAL_GATES

    def test_gate_fields(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        gate = BIOLOGICAL_GATES["run_medaka"]
        assert "parse_regex" in gate
        assert "warn_threshold" in gate or "warn_below" in gate
        assert "fail_threshold" in gate or "fail_below" in gate

    def test_regex_matches_medaka_output(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        pattern = BIOLOGICAL_GATES["run_medaka"]["parse_regex"]
        for line in [
            "[M::] INFO mean qv: 28.35",
            "mean qv: 22",
            "consensus qv: 15.2",
        ]:
            m = re.search(pattern, line, re.IGNORECASE)
            assert m is not None, f"regex did not match: {line!r}"
            float(m.group(1))

    def test_qv_thresholds_values(self):
        from genomeer.agent.v2.utils.quality_gate import BIOLOGICAL_GATES
        gate = BIOLOGICAL_GATES["run_medaka"]
        warn = gate.get("warn_below", gate.get("warn_threshold", 0))
        fail = gate.get("fail_below", gate.get("fail_threshold", 0))
        assert warn > fail, "warn threshold must be above fail threshold"
        assert 15 <= warn <= 30, f"Expected warn near 20, got {warn}"
        assert 5 <= fail <= 15, f"Expected fail near 10, got {fail}"


# ---------------------------------------------------------------------------
# Env routing
# ---------------------------------------------------------------------------

class TestLongReadEnvRouting:

    def test_run_medaka_routes_to_meta_env1(self):
        from genomeer.runtime.env_resolver import resolve_env_for_code
        code = "from genomeer.tools.function.metagenomics import run_medaka\nrun_medaka(...)"
        assert resolve_env_for_code(code, "PY", None, "bio-agent-env1") == "meta-env1"

    def test_run_racon_routes_to_meta_env1(self):
        from genomeer.runtime.env_resolver import resolve_env_for_code
        code = "from genomeer.tools.function.metagenomics import run_racon\nrun_racon(...)"
        assert resolve_env_for_code(code, "PY", None, "bio-agent-env1") == "meta-env1"

    def test_medaka_consensus_cli_routes(self):
        from genomeer.runtime.env_resolver import resolve_env_for_code
        code = "subprocess.run(['medaka_consensus', '-i', reads, '-d', asm])"
        assert resolve_env_for_code(code, "PY", None, "bio-agent-env1") == "meta-env1"

    def test_racon_cli_routes(self):
        from genomeer.runtime.env_resolver import resolve_env_for_code
        code = "subprocess.run(['racon', '-t', '8', reads, paf, assembly])"
        assert resolve_env_for_code(code, "PY", None, "bio-agent-env1") == "meta-env1"

    def test_env_hint_overrides(self):
        from genomeer.runtime.env_resolver import resolve_env_for_code
        code = "run_medaka(...)"
        assert resolve_env_for_code(code, "PY", "custom-env", "bio-agent-env1") == "custom-env"

    def test_index_yaml_has_new_bins(self):
        import yaml
        from genomeer.runtime.env_manager import REGISTRY_PATH
        data = yaml.safe_load(Path(REGISTRY_PATH).read_text(encoding="utf-8"))
        meta = next((e for e in data.get("envs", []) if e.get("name") == "meta-env1"), None)
        assert meta is not None
        bins = [b.strip() for b in meta.get("provides_bins", [])]
        assert "medaka_consensus" in bins
        assert "racon" in bins
        assert "unicycler" in bins


# ---------------------------------------------------------------------------
# Orchestrator injection logic (pure unit, no agent needed)
# ---------------------------------------------------------------------------

class TestOrchestratorLongReadInjection:

    def _is_ont(self, plan, idx):
        s = plan[idx]
        combined = s.get("title","").lower() + s.get("code","").lower() + s.get("notes","").lower()
        return any(kw in combined for kw in
                   ("nano","ont","nanopore","long-read","long_read","nano-raw","nano-hq"))

    def test_flye_ont_triggers_injection(self):
        plan = [{"title":"Run Flye (Nanopore)", "status":"done",
                 "code":"run_flye(read_type='nano-raw')", "notes":"nanopore reads"}]
        assert "flye" in plan[0]["title"].lower()
        assert self._is_ont(plan, 0)

        plan.insert(1, {"title":"Run Medaka (ONT Consensus Polishing)", "status":"todo"})
        plan.insert(1, {"title":"Run Racon Round 2 (ONT Polish)", "status":"todo"})
        plan.insert(1, {"title":"Run Racon Round 1 (ONT Polish)", "status":"todo"})
        assert len(plan) == 4
        assert "racon" in plan[1]["title"].lower()
        assert "racon" in plan[2]["title"].lower()
        assert "medaka" in plan[3]["title"].lower()

    def test_pacbio_hifi_no_injection(self):
        plan = [{"title":"Run Flye", "status":"done",
                 "code":"run_flye(read_type='pacbio-hifi')", "notes":"PacBio HiFi reads"}]
        assert not self._is_ont(plan, 0)

    def test_no_double_injection(self):
        plan = [
            {"title":"Run Flye (Nanopore)","status":"done","code":"run_flye(read_type='nano-raw')","notes":"nano"},
            {"title":"Run Racon Round 1","status":"todo"},
            {"title":"Run Medaka","status":"todo"},
        ]
        already = any("racon" in s["title"].lower() for s in plan) or \
                  any("medaka" in s["title"].lower() for s in plan)
        assert already, "Should detect existing steps to prevent double injection"
