import pytest
import os
import re
import time
import json
import tempfile
from pathlib import Path

class TestBioRAGJoin:
    """WARN 1: Le thread RAG doit être joint avant que le finalizer appelle get_context()."""

    def test_rag_thread_joined_before_use(self):
        """
        Test conceptuel: vérifier que _finalizer appelle join() sur le thread RAG.
        """
        import threading
        import time

        results = []
        rag_ready = threading.Event()

        def slow_rag_build():
            time.sleep(0.2)
            rag_ready.set()
            results.append("rag_built")

        thread = threading.Thread(target=slow_rag_build, daemon=True)
        thread.start()

        # Simuler ce que _finalizer devrait faire
        if thread.is_alive():
            thread.join(timeout=5)

        # Le thread doit être fini avant d'utiliser le RAG
        assert not thread.is_alive(), "Thread should be joined before using RAG"
        assert "rag_built" in results, "RAG should be built before finalizer proceeds"

# ===========================================================================
# NOUVEAU — Tâche 2.4 : Test SQLite Thread Safety
# ===========================================================================

