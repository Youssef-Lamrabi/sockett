import pytest
import os
import re
import time
import json
import tempfile
from pathlib import Path

class TestToolCacheSignature:
    """BUG 1: Vérifier que ToolOutputCache.set() est appelé avec la bonne signature."""

    def test_set_accepts_correct_signature(self, tmp_path):
        from genomeer.agent.v2.utils.cache import ToolOutputCache
        cache = ToolOutputCache(str(tmp_path))
        key = cache.make_key("run_fastp", [], {})
        # Ne doit PAS lever TypeError
        cache.set(key, "run_fastp", {"output": "test output", "step_title": "QC"})

    def test_set_rejects_ttl_seconds_kwarg(self, tmp_path):
        """L'ancien appel cassé doit lever TypeError — confirme le bug original."""
        from genomeer.agent.v2.utils.cache import ToolOutputCache
        cache = ToolOutputCache(str(tmp_path))
        key = cache.make_key("run_fastp", [], {})
        with pytest.raises(TypeError):
            cache.set(key, {"output": "wrong"}, ttl_seconds=999)  # mauvaise signature

    def test_get_returns_dict_with_output_key(self, tmp_path):
        from genomeer.agent.v2.utils.cache import ToolOutputCache
        cache = ToolOutputCache(str(tmp_path))
        key = cache.make_key("run_fastp", [], {})
        cache.set(key, "run_fastp", {"output": "fastp completed OK", "n_reads": 1000000})
        result = cache.get(key)
        assert result is not None
        assert "output" in result
        assert result["output"] == "fastp completed OK"

    def test_get_with_output_dir_does_not_crash(self, tmp_path):
        from genomeer.agent.v2.utils.cache import ToolOutputCache
        cache = ToolOutputCache(str(tmp_path))
        key = cache.make_key("run_kraken2", [], {})
        cache.set(key, "run_kraken2", {"output": "kraken2 done"})
        # output_dir inexistant — ne doit pas crasher
        result = cache.get(key, output_dir=str(tmp_path / "nonexistent"))
        assert result is not None


# ===========================================================================
# BUG #2 — Pas de référence à 'status' dans _planner / _generator
# ===========================================================================

class TestSQLiteThreadSafety:
    """Tâche 2.4: Vérifier que le cache est accessible de manière concurrente (batch mode)."""

    def test_cache_concurrent_writes(self, tmp_path):
        import threading
        from genomeer.agent.v2.utils.cache import ToolOutputCache
        cache = ToolOutputCache(str(tmp_path))
        
        errors = []
        def worker(thread_id):
            try:
                for i in range(20):
                    key = cache.make_key(f"tool_{thread_id}", [], {"i": i})
                    cache.set(key, f"tool_{thread_id}", {"res": i})
                    val = cache.get(key)
                    if not val or val.get("res") != i:
                        errors.append(f"Thread {thread_id} failed to get valid data")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert not errors, f"Exceptions occurred during concurrent access: {errors}"

# ===========================================================================
# NOUVEAU — Tâche 3.4 : Test Tool Cache Cross-Session
# ===========================================================================

class TestToolCacheCrossSession:
    """Tâche 3.4: Le cache d'outils doit être récupérable d'une session à l'autre avec un run_dir différent."""

    def test_cross_session_hit(self, tmp_path):
        from genomeer.agent.v2.utils.cache import ToolOutputCache
        cache_dir = tmp_path / "global_cache"
        cache = ToolOutputCache(str(cache_dir))
        
        # Session 1: Run and cache
        run1_dir = tmp_path / "run_1"
        run1_dir.mkdir()
        (run1_dir / "output.txt").write_text("hello result")
        
        key = cache.make_key("mock_tool", [], {"param": "1"})
        cache.set(key, "mock_tool", {"step": "test"}, output_dir=str(run1_dir))
        
        # Session 2: Retrieve cache into a new run_dir
        run2_dir = tmp_path / "run_2"
        run2_dir.mkdir()
        result = cache.get(key, output_dir=str(run2_dir))
        
        assert result is not None, "Expected cache hit in session 2"
        assert (run2_dir / "output.txt").exists(), "Cached output file was not restored to new session run_dir"
        assert (run2_dir / "output.txt").read_text() == "hello result"

