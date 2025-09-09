import subprocess, tempfile, os
from ..config import settings

def run_shell(script: str) -> str:
    os.makedirs(settings.work_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", dir=settings.work_dir, delete=False) as f:
        f.write(script)
        path = f.name
    proc = subprocess.run(["bash", path], capture_output=True, text=True, timeout=settings.tool_timeout_s)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout
