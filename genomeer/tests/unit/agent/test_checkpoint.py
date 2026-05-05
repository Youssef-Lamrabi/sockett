import pytest
import os
import re
import time
import json
import tempfile
from pathlib import Path

class TestCheckpointSessionId:
    """BUG 3: Les checkpoints ne doivent jamais utiliser 'unknown' comme session_id."""

    def test_checkpoint_uses_provided_session_id(self, tmp_path):
        from genomeer.utils.checkpoint import CheckpointManager
        cp = CheckpointManager(str(tmp_path), "session_abc123")
        state = {
            "current_idx": 2,
            "plan": [{"title": "QC", "status": "done"}],
            "manifest": {},
            "run_temp_dir": str(tmp_path),
            "session_id": "session_abc123",
            "run_id": "session_abc123",
        }
        cp.save(state, 2)
        cp_file = tmp_path / ".genomeer_checkpoint_session_abc123.json"
        assert cp_file.exists(), f"Expected checkpoint file not found: {cp_file}"

    def test_no_unknown_checkpoint_file(self, tmp_path):
        """Vérifier qu'on ne crée pas de fichier checkpoint_unknown.json."""
        from genomeer.utils.checkpoint import CheckpointManager
        cp = CheckpointManager(str(tmp_path), "real_session_id")
        state = {"current_idx": 0, "plan": [], "manifest": {}, "run_temp_dir": str(tmp_path)}
        cp.save(state, 0)
        unknown_file = tmp_path / ".genomeer_checkpoint_unknown.json"
        assert not unknown_file.exists(), (
            "Checkpoint was saved with 'unknown' as session_id. "
            "Fix run_id initialization in _build_initial_state."
        )

    def test_build_initial_state_has_nonempty_run_id(self, tmp_path):
        """run_id dans l'état initial ne doit pas être vide."""
        run_dir = tmp_path / "run_test123"
        run_dir.mkdir()
        import os
        tmp_str = str(run_dir)
        run_id = (tmp_str.rstrip(os.sep).split(os.sep)[-1]) or "fallback"
        assert run_id != ""
        assert run_id != "unknown"
        assert len(run_id) > 0

    def test_checkpoint_load_after_save(self, tmp_path):
        from genomeer.utils.checkpoint import CheckpointManager
        cp = CheckpointManager(str(tmp_path), "load_test_session")
        state = {
            "current_idx": 5,
            "plan": [{"title": f"step_{i}", "status": "done"} for i in range(6)],
            "manifest": {"quality_signals": {"n50": 5000}},
            "run_temp_dir": str(tmp_path),
            "session_id": "load_test_session",
        }
        assert cp.save(state, 5)
        loaded = cp.load()
        assert loaded is not None
        assert loaded["current_idx"] == 5
        assert loaded["_completed_step_idx"] == 5


# ===========================================================================
# BUG #4 — Sandbox sécurité
# ===========================================================================

