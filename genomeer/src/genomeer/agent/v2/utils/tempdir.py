import os, tempfile, shutil, uuid, atexit, threading
from contextlib import contextmanager

# TODO:
# ------------------------------------------------------------------
# - [x] Create temp home directory for each run
# - [x] Persist the directory accross call for the same session
# - [x] Copy/move the artifacts to a persistent location (or object 
#       storage) and generate download URLs. -- via artifact_servive helper
# - [x] Clean up the temp dir
# ------------------------------------------------------------------

BASE_TMP = os.environ.get("BIOAGENT_TMP_DIR", tempfile.gettempdir())
# Set BIOAGENT_KEEP_RUNS=1 to preserve temp dirs for debugging
KEEP_RUNS = os.environ.get("BIOAGENT_KEEP_RUNS", "0").strip() not in ("", "0", "false", "False")

_CLEANUP_REGISTRY = set()
_REGISTRY_LOCK = threading.Lock()

def _global_cleanup():
    """BUG-52: Single atexit handler for all registered temp dirs."""
    with _REGISTRY_LOCK:
        for p in list(_CLEANUP_REGISTRY):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        _CLEANUP_REGISTRY.clear()

atexit.register(_global_cleanup)

@contextmanager
def run_workdir(prefix: str = "run", session_id: str | None = None):
    run_id = session_id or str(uuid.uuid4())
    path = os.path.join(BASE_TMP, f"{prefix}-{run_id}")

    with _REGISTRY_LOCK:
        os.makedirs(path, exist_ok=True)
        _CLEANUP_REGISTRY.add(path)

    try:
        yield path
    finally:
        if not KEEP_RUNS:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            with _REGISTRY_LOCK:
                _CLEANUP_REGISTRY.discard(path)
