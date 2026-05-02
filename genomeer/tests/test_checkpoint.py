import pytest
import os
import tempfile
from genomeer.utils.checkpoint import CheckpointManager

def test_checkpoint_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = CheckpointManager(tmpdir, "test_session_1")
        test_state = {"current_idx": 3, "plan": [{"title": "test"}]}
        cm.save(test_state, 3)
        assert cm.exists()
        loaded = cm.load()
        assert loaded["current_idx"] == 3
        assert loaded["plan"][0]["title"] == "test"
