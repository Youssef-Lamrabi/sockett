import os, tempfile, shutil, uuid, atexit, threading
from contextlib import contextmanager

BASE_TMP = os.environ.get("BIOAGENT_TMP_DIR", tempfile.gettempdir())

# Module-level registry of active temp dirs.
# A single atexit handler iterates it instead of one handler per run_workdir call,
# preventing handler accumulation on long-running servers (BUG-52).
_active_dirs: dict[str, bool] = {}   # path -> cleaned flag
_dirs_lock = threading.Lock()
_atexit_registered = False


def _cleanup_all():
    with _dirs_lock:
        paths = list(_active_dirs.keys())
    for path in paths:
        with _dirs_lock:
            if _active_dirs.get(path):
                continue
            _active_dirs[path] = True
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


@contextmanager
def run_workdir(prefix: str = "run", session_id: str | None = None):
    global _atexit_registered
    run_id = session_id or str(uuid.uuid4())
    path = os.path.join(BASE_TMP, f"{prefix}-{run_id}")
    os.makedirs(path, exist_ok=True)

    with _dirs_lock:
        _active_dirs[path] = False
        if not _atexit_registered:
            atexit.register(_cleanup_all)
            _atexit_registered = True

    try:
        yield path
    finally:
        # DEBUG: keep dirs for dev; flip to True to enable immediate cleanup.
        # with _dirs_lock:
        #     _active_dirs[path] = True
        # if os.path.isdir(path):
        #     shutil.rmtree(path, ignore_errors=True)
        pass
