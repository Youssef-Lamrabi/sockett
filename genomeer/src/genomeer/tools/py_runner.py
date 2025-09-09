import subprocess, tempfile, json, os, sys, textwrap
from typing import Dict, Any
from ..config import settings

def run_python(code: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    os.makedirs(settings.work_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".py", dir=settings.work_dir, delete=False) as f:
        f.write(textwrap.dedent(f"""
        import json, sys
        user_inputs = json.loads({json.dumps(json.dumps(inputs))})
        # --- BEGIN USER CODE ---
        {code}
        # Must print a single JSON object to stdout
        """))
        path = f.name

    proc = subprocess.run(
        [sys.executable, path],
        capture_output=True, text=True, timeout=settings.tool_timeout_s
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    out = proc.stdout.strip()
    return json.loads(out or "{}")
