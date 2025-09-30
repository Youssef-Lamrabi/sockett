import os, tempfile, shutil, uuid, atexit
from contextlib import contextmanager

# TODO:
# ------------------------------------------------------------------
# - [x] Create temp home directory for each run
# - [ ] Copy/move the artifacts to a persistent location (or object 
#       storage) and generate download URLs.
# - [x] Clean up the temp dir
# ------------------------------------------------------------------
 
BASE_TMP = os.environ.get("BIOAGENT_TMP_DIR", tempfile.gettempdir())

@contextmanager
def run_workdir(prefix: str = "run"):
    run_id = str(uuid.uuid4())
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
        # DEBUG: keep it now for dev purpose
        # _cleanup()
        pass
