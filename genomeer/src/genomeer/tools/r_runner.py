import subprocess, tempfile, os, json
from ..config import settings

def run_rscript(code: str, inputs: dict) -> dict:
    os.makedirs(settings.work_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".R", dir=settings.work_dir, delete=False) as f:
        f.write(f"""
        args_json <- '{json.dumps(inputs)}'
        library(jsonlite)
        user_inputs <- fromJSON(args_json)
        # --- BEGIN USER CODE ---
        {code}
        # must print single-line JSON to stdout:
        # cat(toJSON(list(result="ok")))
        """)
        path = f.name
    proc = subprocess.run([settings.rscript_path, path], capture_output=True, text=True, timeout=settings.tool_timeout_s)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout.strip() or "{}")
