"""
tests/integration/test_batch_orchestrator.py
=============================================
Tests d'intégration : concurrence du cache SQLite en mode batch

Valide que le ToolOutputCache (SQLite + threading.Lock) supporte correctement
des accès simultanés venant de plusieurs workers (comme en mode batch_orchestrator).

Scénarios testés :
  - N workers qui écrivent/lisent en parallèle sans corruption
  - Isolation des clés entre tools différents
  - Résistance aux accès concurrent sur la même clé (upsert idempotent)
  - Vérification que le cache persiste sur disque après fermeture + réouverture
"""
import pytest
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_cache(tmp_path):
    """Cache partagé sur disque comme en mode batch réel."""
    from genomeer.agent.v2.utils.cache import ToolOutputCache
    return ToolOutputCache(str(tmp_path / "shared_cache"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBatchOrchestratorConcurrency:

    def test_multi_worker_no_corruption(self, shared_cache):
        """
        8 workers parallèles (simulant un batch_orchestrator) écrivent
        et relisent leurs propres résultats sans corruption.
        """
        errors = []
        n_workers = 8
        ops_per_worker = 30

        def worker(worker_id):
            try:
                for i in range(ops_per_worker):
                    key = shared_cache.make_key(f"tool_w{worker_id}", [], {"step": i})
                    payload = {"output": f"result_{worker_id}_{i}", "step": i}
                    shared_cache.set(key, f"tool_w{worker_id}", payload)
                    result = shared_cache.get(key)
                    if result is None:
                        errors.append(f"Worker {worker_id} step {i}: cache miss")
                    elif result.get("step") != i:
                        errors.append(
                            f"Worker {worker_id} step {i}: data corruption "
                            f"(got step={result.get('step')})"
                        )
            except Exception as e:
                errors.append(f"Worker {worker_id}: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Erreurs de concurrence détectées:\n" + "\n".join(errors)

    def test_same_key_concurrent_upsert(self, shared_cache):
        """
        Plusieurs threads écrivent sur la même clé en parallèle.
        La dernière écriture doit être lisible (pas de deadlock, pas de crash).
        """
        key = shared_cache.make_key("shared_tool", [], {"common": True})
        errors = []

        def write_result(thread_id):
            try:
                shared_cache.set(key, "shared_tool", {"writer": thread_id})
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=write_result, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Erreurs lors des upserts concurrents: {errors}"

        # La clé doit être lisible après tous les upserts
        result = shared_cache.get(key)
        assert result is not None, "La clé doit être lisible après des upserts concurrents"
        assert "writer" in result, "Le contenu doit être présent"

    def test_cache_persists_after_close_reopen(self, tmp_path):
        """
        Le cache SQLite doit persister sur disque :
        données écrites dans une session doivent être disponibles dans une nouvelle.
        """
        from genomeer.agent.v2.utils.cache import ToolOutputCache

        cache_dir = str(tmp_path / "persistent_cache")

        # Session 1 : écriture
        cache1 = ToolOutputCache(cache_dir)
        key = cache1.make_key("run_kraken2", [], {"db": "/data/k2_standard"})
        cache1.set(key, "run_kraken2", {
            "output": "kraken2 completed",
            "classified_pct": 97.3,
        })
        del cache1  # fermeture de la connexion

        # Session 2 : lecture dans une nouvelle instance
        cache2 = ToolOutputCache(cache_dir)
        result = cache2.get(key)

        assert result is not None, "Le cache doit persister entre deux sessions"
        assert result.get("classified_pct") == pytest.approx(97.3)

    def test_key_isolation_between_tools(self, shared_cache):
        """
        Les clés de deux tools différents avec les mêmes paramètres
        ne doivent pas entrer en collision.
        """
        params = {"input": "/data/reads.fq.gz", "threads": 8}
        key_fastp = shared_cache.make_key("run_fastp", [], params)
        key_kraken = shared_cache.make_key("run_kraken2", [], params)

        assert key_fastp != key_kraken, (
            "Les clés de cache de tools différents doivent être distinctes"
        )

        shared_cache.set(key_fastp, "run_fastp", {"output": "fastp_result"})
        shared_cache.set(key_kraken, "run_kraken2", {"output": "kraken2_result"})

        assert shared_cache.get(key_fastp)["output"] == "fastp_result"
        assert shared_cache.get(key_kraken)["output"] == "kraken2_result"

    def test_batch_throughput_acceptable(self, shared_cache):
        """
        Performance : 200 opérations set+get doivent s'exécuter en < 30s.
        Le seuil est généreux pour Windows/NTFS où SQLite est plus lent
        (~30ms/op) que sur Linux/ext4 (~5ms/op) à cause de la synchronisation
        du système de fichiers. Ce test est un guardrail, pas un micro-benchmark.
        """
        import platform
        n_ops = 200
        # Windows NTFS SQLite est ~5-6x plus lent que Linux ext4
        threshold_sec = 30.0 if platform.system() == "Windows" else 5.0

        start = time.perf_counter()

        for i in range(n_ops):
            key = shared_cache.make_key("bench_tool", [], {"i": i})
            shared_cache.set(key, "bench_tool", {"val": i})
            shared_cache.get(key)

        elapsed = time.perf_counter() - start
        ms_per_op = 1000 * elapsed / n_ops
        print(f"\n[PERF] {n_ops} ops en {elapsed:.3f}s ({ms_per_op:.1f}ms/op) — seuil: {threshold_sec}s")

        assert elapsed < threshold_sec, (
            f"{n_ops} ops set+get ont pris {elapsed:.2f}s (seuil : {threshold_sec}s sur {platform.system()}). "
            "Le cache SQLite est trop lent pour le mode batch."
        )
