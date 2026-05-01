import os, tempfile, shutil, uuid, atexit
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

@contextmanager
def run_workdir(prefix: str = "run", session_id: str | None = None):
    run_id = session_id or str(uuid.uuid4())
    path = os.path.join(BASE_TMP, f"{prefix}-{run_id}")
    os.makedirs(path, exist_ok=True)
    cleaned = {"done": False}

    def _cleanup():
        if not cleaned["done"] and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            cleaned["done"] = True

    atexit.register(_cleanup)
    try:
        yield path
    finally:
        if not KEEP_RUNS:
            _cleanup()
        # else: leave directory for debugging (set BIOAGENT_KEEP_RUNS=1)
