from io import StringIO
from contextlib import redirect_stdout, redirect_stderr
import os, tempfile, subprocess, traceback, shlex, threading, importlib, ast, sys
from typing import Any, Callable, Iterable, Mapping, Optional
from pydantic import BaseModel, Field, ValidationError
from langchain_core.messages.base import get_msg_title_repr
from langchain_core.utils.interactive_env import is_interactive_env

from genomeer.config import settings
from genomeer.runtime.env_manager import (
    ensure_micromamba,
    ensure_env,
    ENVS_DIR
)
_persistent_namespace = {}

# Cached result: True if this micromamba binary accepts --no-rc, False otherwise.
_MICROMAMBA_NO_RC_SUPPORTED: Optional[bool] = None

def _micromamba_supports_no_rc() -> bool:
    """Return True if the installed micromamba supports the --no-rc flag.
    Result is cached after the first call."""
    global _MICROMAMBA_NO_RC_SUPPORTED
    if _MICROMAMBA_NO_RC_SUPPORTED is not None:
        return _MICROMAMBA_NO_RC_SUPPORTED
    try:
        exe = ensure_micromamba()
        result = subprocess.run(
            [str(exe), "run", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        _MICROMAMBA_NO_RC_SUPPORTED = "--no-rc" in (result.stdout + result.stderr)
    except Exception:
        _MICROMAMBA_NO_RC_SUPPORTED = False
    return _MICROMAMBA_NO_RC_SUPPORTED

class api_schema(BaseModel):
    """api schema specification."""
    api_schema: str | None = Field(description="The api schema as a dictionary")

# ------------------------------------------------------------------------------------------
# internal utility to run in env
# ------------------------------------------------------------------------------------------
def _run_in_env(
    env_name: str,
    argv: list[str],
    *,
    timeout: float,
    extra_env: Optional[Mapping[str, str]] = None,
    check: bool = False,
    input_text: Optional[str] = None,
    cancel_event: Any = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run argv inside micromamba env <env_name> and capture output.

    Windows-safe: uses CREATE_NEW_PROCESS_GROUP + taskkill /F /T on timeout so
    the entire process tree (micromamba -> python -> ncbi-genome-download ...) is
    killed. Without this, subprocess.run timeout leaves child processes running
    and then hangs forever draining a full pipe buffer (classic Windows deadlock).
    """
    import platform as _platform

    exe = ensure_micromamba()
    prefix = ENVS_DIR / env_name

    # --no-rc suppresses shell-init PATH warnings but is not available on all
    # micromamba versions — check support once and cache the result.
    _extra = ["--no-rc"] if _platform.system() != "Windows" and _micromamba_supports_no_rc() else []
    cmd = [str(exe), "run", *_extra, "-p", str(prefix), *argv]

    env = dict(os.environ)
    env.pop("CONDA_PREFIX", None)
    env["MAMBA_ROOT_PREFIX"] = str(ENVS_DIR.parent.parent)
    if extra_env:
        env.update(extra_env)

    if _platform.system() == "Windows":
        # On Windows we must create a new process group so that taskkill can
        # kill the entire tree (not just the top-level micromamba process).
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            stdout_b, stderr_b = proc.communicate(
                input=input_text.encode() if input_text else None,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # Kill every process in the tree, then drain the now-dead pipes.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
            )
            try:
                proc.kill()
            except Exception:
                pass
            try:
                stdout_b, stderr_b = proc.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                stdout_b = proc.stdout.read() if proc.stdout else b""
                stderr_b = proc.stderr.read() if proc.stderr else b""
            raise subprocess.TimeoutExpired(
                cmd, timeout,
                output=stdout_b,
                stderr=stderr_b,
            )

        rc = proc.returncode
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        result = subprocess.CompletedProcess(cmd, rc, stdout, stderr)
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd, stdout, stderr)
        return result

    # Linux / macOS: run in a NEW SESSION (own process group) so a user Stop or a
    # timeout can kill the WHOLE tree (micromamba -> python -> wget/...). A plain
    # subprocess.run() blocks and cannot be interrupted mid-run, so a long download
    # kept running after the user clicked Stop. Output pipes are drained in threads
    # to avoid the classic fill-the-buffer deadlock.
    import signal as _signal, time as _time, threading as _threading
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    _out_chunks: list = []
    _err_chunks: list = []

    def _drain(pipe, buf):
        try:
            for chunk in iter(lambda: pipe.read(65536), b""):
                buf.append(chunk)
        except Exception:
            pass

    _t_out = _threading.Thread(target=_drain, args=(proc.stdout, _out_chunks), daemon=True)
    _t_err = _threading.Thread(target=_drain, args=(proc.stderr, _err_chunks), daemon=True)
    _t_out.start(); _t_err.start()
    if input_text is not None and proc.stdin:
        try:
            proc.stdin.write(input_text.encode()); proc.stdin.close()
        except Exception:
            pass

    def _killpg():
        try:
            os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), _signal.SIGKILL)
            except Exception:
                pass

    _deadline = (_time.monotonic() + timeout) if timeout else None
    _cancelled = False
    while True:
        try:
            proc.wait(timeout=0.4)
            break                      # process finished on its own
        except subprocess.TimeoutExpired:
            pass
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            _cancelled = True
            _killpg()
            break
        if _deadline is not None and _time.monotonic() > _deadline:
            _killpg()
            _t_out.join(2); _t_err.join(2)
            raise subprocess.TimeoutExpired(
                cmd, timeout,
                output=b"".join(_out_chunks),
                stderr=b"".join(_err_chunks),
            )

    _t_out.join(2); _t_err.join(2)
    stdout = b"".join(_out_chunks).decode("utf-8", errors="replace")
    stderr = b"".join(_err_chunks).decode("utf-8", errors="replace")
    rc = proc.returncode if proc.returncode is not None else -15
    if _cancelled:
        stderr = (stderr + "\n[cancelled by user]").strip()
    result = subprocess.CompletedProcess(cmd, rc, stdout, stderr)
    if check and rc != 0 and not _cancelled:
        raise subprocess.CalledProcessError(rc, cmd, stdout, stderr)
    return result
    
def _tail(text: str, limit: int = 20000) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else (text[-limit:] + "\n...<truncated tail>")

def _format_proc_error(title: str, cmd: list[str] | str, rc: int, stdout: str, stderr: str) -> str:
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    parts = [
        f"{title}",
        f"Exit code: {rc}",
        f"Command: {cmd_str}",
        "--- STDOUT (tail) ---\n" + _tail(stdout),
        "--- STDERR (tail) ---\n" + _tail(stderr),
    ]
    return "\n".join(parts).strip()
    
# ------------------------------------------------------------------------------------------
# Function: run_r_code
# Desc: Helper function for LLM to run R code while using tools
# TODO: This tool doesn't accept input agrs yet. To be done.
# ------------------------------------------------------------------------------------------
def run_r_code(code: str, *, env_name: Optional[str] = None, log_cb=None, timeout: Optional[float] = None, extra_env: Optional[Mapping[str, str]] = None, cancel_event: Any = None) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    code = (code or "").strip()
    if not code: 
        return "Error: Empty script"
    
    try:
        with tempfile.NamedTemporaryFile(suffix=".R", mode="w", encoding="utf-8", dir=settings.run_dir, delete=False) as f:
            f.write(code); path = f.name
            
        if env_name:
            try:
                ensure_env(env_name, auto_install=True, log_cb=log_cb)
            except Exception as _env_err:
                return f"Environment '{env_name}' is not available: {_env_err}" 
            proc = _run_in_env(env_name, ["Rscript", path], timeout=timeout or settings.timeout_seconds, extra_env=extra_env, cancel_event=cancel_event)
            cmd_display = proc.args if hasattr(proc, "args") else ["bash", path]
            if proc.returncode == 0:
                return proc.stdout or ""
            return _format_proc_error(
                "Error running this script",
                cmd_display,
                proc.returncode,
                proc.stdout or "",
                proc.stderr or "",
            )

        # fallback: host R
        res = subprocess.run(
            ["Rscript", path],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or settings.timeout_seconds,
        )
        if res.returncode == 0:
            return res.stdout or ""
        return _format_proc_error(
            "Error running Bash script",
            [path],
            res.returncode,
            res.stdout or "",
            res.stderr or "",
        )
    except Exception as e:
        # return f"Error running R code: {e}"
        tb = traceback.format_exc()
        return f"Error running Bash script: {tb}"
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


# ------------------------------------------------------------------------------------------
# Function: run_bash_script
# Desc: Helper function for LLM to run bash_code code while using tools
# TODO: This tool doesn't accept input agrs yet. To be done.
# ------------------------------------------------------------------------------------------
def run_bash_script(script: str, *, env_name: Optional[str] = None, log_cb=None, timeout: Optional[float] = None, extra_env: Optional[Mapping[str, str]] = None, cancel_event: Any = None) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    script = (script or "").strip()
    if not script: 
        return "Error: Empty script"
    
    # Inject run_dir from RUN_TEMP_DIR into every bash script unconditionally.
    # This defines $run_dir as a shell variable before any user script content runs,
    # preventing "unbound variable" errors (set -u) when the Generator uses $run_dir
    # without defining it. helper.py owns this injection — the Generator must never
    # write run_dir= or mkdir -p "$run_dir" itself.
    _run_tmp = (extra_env or {}).get("RUN_TEMP_DIR") or os.environ.get("RUN_TEMP_DIR", "")
    _run_dir_preamble = ""
    if _run_tmp:
        _run_dir_preamble = (
            f'run_dir="{_run_tmp}"\n'
            f'RUN_DIR="$run_dir"\n'   # uppercase alias — model sometimes uses $RUN_DIR
            f'mkdir -p "$run_dir"\n'
        )

    with tempfile.NamedTemporaryFile(suffix=".sh", mode="w", encoding="utf-8", dir=settings.run_dir, delete=False) as f:
        if not script.startswith("#!/"): f.write("#!/bin/bash\n")
        if "set -e" not in script: f.write("set -euo pipefail\n")
        if _run_dir_preamble: f.write(_run_dir_preamble)
        f.write(script); path = f.name
    os.chmod(path, 0o755)
    
    try:
        if env_name:
            try:
                ensure_env(env_name, auto_install=True, log_cb=log_cb)
            except Exception as _env_err:
                return f"Environment '{env_name}' is not available: {_env_err}"

            proc = _run_in_env(env_name, ["bash", path], timeout=timeout or settings.timeout_seconds, extra_env=extra_env, cancel_event=cancel_event)
            cmd_display = proc.args if hasattr(proc, "args") else ["bash", path]
            if proc.returncode == 0:
                return proc.stdout or ""
            return _format_proc_error(
                "Error running this script",
                cmd_display,
                proc.returncode,
                proc.stdout or "",
                proc.stderr or "",
            )

        # fallback: host bash
        res = subprocess.run(
            [path],
            shell=False,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout or settings.timeout_seconds,
        )
        if res.returncode == 0:
            return res.stdout or ""
        return _format_proc_error(
            "Error running Bash script",
            [path],
            res.returncode,
            res.stdout or "",
            res.stderr or "",
        )
    except Exception as e:
        # traceback.print_exc()
        tb = traceback.format_exc()
        # return f"Error running Bash script: {e}"
        return f"Error running Bash script: {tb}"
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


# ------------------------------------------------------------------------------------------
# Function: run_cli_command
# Desc: Helper function for LLM to run command in shell while using tools
# TODO: This tool doesn't accept input agrs yet. To be done.
# UPDATE: Stop maintaining this helper 25.09.25
# ------------------------------------------------------------------------------------------
def run_cli_command(command: str, *, env_name: Optional[str] = None, log_cb=None) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    try:
        command = (command or "").strip()
        if not command: 
            return "Error: Empty command"
        argv = shlex.split(command)
        
        if env_name:
            try:
                ensure_env(env_name, auto_install=True, log_cb=log_cb)
            except Exception as _env_err:
                return f"Environment '{env_name}' is not available: {_env_err}"
            proc = _run_in_env(env_name, argv, timeout=settings.timeout_seconds)
            return proc.stdout if proc.returncode == 0 else f"Error running command in '{env_name}':\n{proc.stderr}"

        # fallback: host
        res = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=settings.timeout_seconds)
        return res.stdout if res.returncode == 0 else f"Error running command '{command}':\n{res.stderr}"
    except Exception as e:
        return f"Error running command '{command}': {e}"
    
    
# ------------------------------------------------------------------------------------------
# Function: run_python_code
# Desc: Executes Python code inside a micromamba env if provided, otherwise in a persistent REPL.
# ------------------------------------------------------------------------------------------
def run_python_code(code: str, *, env_name: Optional[str] = None, log_cb=None, timeout: Optional[float] = None, extra_env: Optional[Mapping[str, str]] = None, cancel_event: Any = None) -> str:
    """
    Executes the provided Python code.
    - If env_name is provided: runs it in that micromamba env (fresh process).
    - If no env_name: runs in a persistent REPL namespace in the current process.
    """
    path = None  # must be initialised before try so finally never hits NameError
    code = code.strip("```").strip()

    # --- Pre-execution GENOMEER-IMPORT GUARD (GENERAL, tool-agnostic) -------------------------
    # Executable scripts run in an ISOLATED micromamba env (e.g. meta-env1) that does NOT have
    # the genomeer package or its deps (langchain_core, etc.) installed. Any `import genomeer` /
    # `from genomeer ...` therefore crashes deep inside genomeer's own __init__ chain with a
    # MISLEADING error like "ModuleNotFoundError: No module named 'langchain_core'", which sends
    # the repair loop chasing a phantom missing-dependency instead of the real mistake (importing
    # the package at all). Catch it here for EVERY tool with a precise, actionable message so the
    # model rewrites the logic inline (subprocess/CLI call) instead of importing the package.
    if env_name:  # only relevant when running in a separate env, never for the in-process REPL
        for _raw in code.split("\n"):
            _s = _raw.lstrip()
            if _s.startswith("from genomeer") or _s.startswith("import genomeer"):
                return (
                    "Error running this script\nExit code: 1\n"
                    "--- IMPORT GUARD (the code was NOT executed) ---\n"
                    f"Forbidden import detected: {_s.strip()!r}\n"
                    "The `genomeer` package is NOT installed in the execution environment "
                    f"({env_name}); importing it fails with a misleading 'No module named "
                    "langchain_core' error. Do NOT import genomeer.* in an executable script.\n"
                    "FIX: implement the needed action INLINE — call the underlying CLI tool via "
                    "subprocess (e.g. prefetch/fasterq-dump, or an ENA/EBI download URL with "
                    "urllib/requests), or inline the plain-Python logic. Keep the rest of the "
                    "working script; only replace the genomeer import + its usage."
                )

    # --- Pre-execution SYNTAX CHECK + safe auto-fix (GENERAL: catches ANY IndentationError/
    # SyntaxError BEFORE spawning a process, so a broken script never wastes a full micromamba
    # run + timeout, and the repair loop gets a PRECISE line-anchored error instead of a slow
    # runtime traceback that the model kept re-introducing across retries). ---
    import ast as _ast
    def _syn_err(_src):
        try:
            _ast.parse(_src); return None
        except (SyntaxError, IndentationError) as _e:
            return _e
    _se = _syn_err(code)
    if _se is not None:
        # Safe deterministic fix: convert LEADING tabs (only) to 4 spaces — the #1 cause of
        # IndentationError — without touching tabs inside string literals. Re-check.
        def _fix_leading_tabs(_src):
            _out = []
            for _l in _src.split("\n"):
                _i = 0
                while _i < len(_l) and _l[_i] in " \t":
                    _i += 1
                _out.append(_l[:_i].replace("\t", "    ") + _l[_i:])
            return "\n".join(_out)
        _fixed = _fix_leading_tabs(code)
        if _syn_err(_fixed) is None:
            code = _fixed  # auto-fixed a tab/space indentation problem → proceed to run
        else:
            _se2 = _syn_err(code)
            # BLOCK ONLY on IndentationError — whitespace structure is IDENTICAL across Python
            # versions, so this is safe. A plain SyntaxError is NOT blocked: it may be perfectly
            # valid syntax in the TARGET env (e.g. Python 3.12 f-strings / PEP 695 generics) that
            # THIS backend interpreter (an older Python) simply cannot parse — false-blocking it
            # would fail steps that used to pass. Let such code run in its real env instead.
            if isinstance(_se2, IndentationError):
                _ln = getattr(_se2, "lineno", 0) or 0
                _rows = code.split("\n")
                _ctx = f">>> offending line {_ln}: {_rows[_ln - 1]!r}" if 1 <= _ln <= len(_rows) else ""
                return (
                    "Error running this script\nExit code: 1\n"
                    "--- SYNTAX PRE-CHECK (the code was NOT executed) ---\n"
                    f"IndentationError: {getattr(_se2, 'msg', _se2)} (line {_ln})\n{_ctx}\n"
                    "FIX: correct ONLY the indentation of that exact line and keep the rest of the "
                    "working script — do not regenerate everything from scratch."
                )
            # non-indentation SyntaxError → do not block (possible version-specific valid syntax);
            # fall through and execute in the target env exactly as before this pre-check existed.

    try:
        # --- Case 1: run in a micromamba environment ---
        if env_name:
            try:
                ensure_env(env_name, auto_install=True, log_cb=log_cb)
            except Exception as _env_err:
                return f"Environment '{env_name}' is not available: {_env_err}"

            # Write the code to a temp file
            os.makedirs(settings.run_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", dir=settings.run_dir, delete=False) as f:
                f.write("# -*- coding: utf-8 -*-\n" + code)
                path = f.name

            try:
                proc = _run_in_env(env_name, ["python", path], timeout=timeout or settings.timeout_seconds, extra_env=extra_env, cancel_event=cancel_event)
            except subprocess.TimeoutExpired as te:
                cmd_display = getattr(te, "cmd", ["python", path])
                return (
                    "Timeout running Python script in environment\n"
                    f"Timeout (s): {settings.timeout_seconds}\n"
                    f"Command: {' '.join(cmd_display) if isinstance(cmd_display, (list, tuple)) else cmd_display}\n"
                    f"\n--- STDERR (tail) ---\n{_tail(getattr(te, 'stderr', '') or '')}"
                    f"\n--- STDOUT (tail) ---\n{_tail(getattr(te, 'stdout', '') or '')}"
                ).strip()

            cmd_display = proc.args if hasattr(proc, "args") else ["python", path]
            if proc.returncode == 0:
                return proc.stdout or ""
            return _format_proc_error(
                "Error running this script",
                cmd_display,
                proc.returncode,
                proc.stdout or "",
                proc.stderr or "",
            )


        # --- Case 2: run in persistent in-process REPL ---
        stdout_buf, stderr_buf = StringIO(), StringIO()
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            try:
                # compile first to get better syntax errors with filename "<repl>"
                compiled = compile(code, "<repl>", "exec")
                exec(compiled, _persistent_namespace)
            except Exception:
                # include full traceback + whatever was printed so far
                tb = traceback.format_exc()
                out = stdout_buf.getvalue()
                err = stderr_buf.getvalue()
                return (
                    "Error running Python code (REPL)\n"
                    f"\n--- TRACEBACK ---\n{tb}"
                    f"\n--- STDOUT (so far) ---\n{_tail(out)}"
                    f"\n--- STDERR (so far) ---\n{_tail(err)}"
                ).strip()

        # success: return combined stdout+stderr (stderr might contain warnings/prints)
        out = stdout_buf.getvalue()
        err = stderr_buf.getvalue()
        return (out + (("\n" + err) if err else "")).rstrip("\n")
    
        # else:
        #     def execute_in_repl(command: str) -> str:
        #         """Helper to execute inside persistent namespace."""
        #         old_stdout = sys.stdout
        #         sys.stdout = mystdout = StringIO()

        #         global _persistent_namespace
        #         try:
        #             exec(command, _persistent_namespace)
        #             output = mystdout.getvalue()
        #         except Exception as e:
        #             output = f"Error: {str(e)}"
        #         finally:
        #             sys.stdout = old_stdout
        #         return output
        #     return execute_in_repl(code)
        
    except Exception as e:
        tb = traceback.format_exc()
        return f"Error running Python script: {tb}"
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass
    
# ------------------------------------------------------------------------------------------
# Function: run_with_timeout
# Desc: Helper function for LLM to run a function with a timeout while using tools
# ------------------------------------------------------------------------------------------
def run_with_timeout(
    func: Callable[..., Any],
    args: Optional[Iterable[Any]] = None,
    kwargs: Optional[Mapping[str, Any]] = None,
    timeout: float = settings.timeout_seconds,
    cancel_event: Optional[threading.Event] = None,
) -> Any:
    """
    Run `func(*args, **kwargs)` in a thread and wait up to `timeout` seconds.
    - Globals persist (threads, not processes).
    - Exceptions from `func` are propagated.
    - On timeout, raises TimeoutErrorWithContext (no unsafe thread killing).
    - Optionally pass a `cancel_event` the function can check to stop itself.
    """
    
    import concurrent.futures as cf
    class TimeoutErrorWithContext(TimeoutError):
        pass
    
    args = [] if args is None else list(args)
    kwargs = {} if kwargs is None else dict(kwargs)
    if cancel_event is not None:
        kwargs.setdefault("cancel_event", cancel_event)

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(func, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        except cf.TimeoutError as e:
            if cancel_event is not None:
                cancel_event.set()
            raise TimeoutErrorWithContext(f"Timed out after {timeout} seconds") from e


# ------------------------------------------------------------------------------------------
# Function: read_module2api
# Desc: This helper helps user to retrieve the tool description
# ------------------------------------------------------------------------------------------
def read_module2api():
    fields = [
        "ncbi",
        "basic",
        "metagenomics",
        "viromics",
        "genomics",
        "sra",           # fetch_sra_reads only — the other 13 metagenomics_db.py
                         # tools stay unwired (duplicates of local run_rgi/run_gtdbtk/
                         # run_dbcan, or live-network dependent); see sra.py docstring.
        "longread",      # run_flye, run_unicycler, run_filtlong, run_nanoplot —
                         # completes the long-read assembly/QC/polishing pipeline
                         # (run_medaka/run_racon already live in metagenomics).
        # "artifacts",
        # "literature",
        # "biochemistry",
        # "bioengineering",
        # "biophysics",
        # "cancer_biology",
        # "cell_biology",
        # "molecular_biology",
        # "genetics",
        # "immunology",
        # "microbiology",
        # "pathology",
        # "pharmacology",
        # "physiology",
        # "synthetic_biology",
        # "systems_biology",
        # "support_tools",
        # "database",
    ]

    module2api = {}
    for field in fields:
        module_name = f"genomeer.tools.description.{field}"
        module = importlib.import_module(module_name)
        module2api[f"genomeer.tools.function.{field}"] = module.description
    return module2api


# ------------------------------------------------------------------------------------------
# Function: function_to_api_schema
# Desc: This helper writes an API docstring for a giving  code snippet
# ------------------------------------------------------------------------------------------
def function_to_api_schema(function_string, llm):
    prompt = """
    Based on a code snippet and help me write an API docstring in the format like this:

    {{'name': 'get_gene_set_enrichment',
    'description': 'Given a list of genes, identify a pathway that is enriched for this gene set. Return a list of pathway name, p-value, z-scores.',
    'required_parameters': [{{'name': 'genes', 'type': 'List[str]', 'description': 'List of gene symbols to analyze', 'default': None}}],
    'optional_parameters': [
        {{'name': 'top_k', 'type': 'int', 'description': 'Top K pathways to return', 'default': 10}},  
        {{'name': 'database', 'type': 'str', 'description': 'Name of the database to use for enrichment analysis', 'default': "gene_ontology"}}
    ]}}

    Strictly follow the input from the function - don't create fake optional parameters.
    For variable without default values, set them as None, not null.
    For variable with boolean values, use capitalized True or False, not true or false.
    Do not add any return type in the docstring.
    Be as clear and succint as possible for the descriptions. Please do not make it overly verbose.
    Here is the code snippet:
    {code}
    """
    llm = llm.with_structured_output(api_schema)

    for _ in range(7):
        try:
            api = llm.invoke(prompt.format(code=function_string)).dict()["api_schema"]
            return ast.literal_eval(api)  # -> prefer "default": None
            # return json.loads(api) # -> prefer "default": null
        except Exception as e:
            print("API string:", api)
            print("Error parsing the API string:", e)
            continue
    return "Error: Could not parse the API schema"
    

# ------------------------------------------------------------------------------------------
# Function: textify_api_dict
# Desc: Convert a nested API dictionary to a nicely formatted string.
# ------------------------------------------------------------------------------------------
def textify_api_dict(api_dict):
    """Convert a nested API dictionary to a nicely formatted string."""
    lines = []
    for category, methods in api_dict.items():
        lines.append(f"Import file: {category}")
        lines.append("=" * (len("Import file: ") + len(category)))
        for method in methods:
            lines.append(f"Method: {method.get('name', 'N/A')}")
            lines.append(f"  Description: {method.get('description', 'No description provided.')}")

            # Process required parameters
            req_params = method.get("required_parameters", [])
            if req_params:
                lines.append("  Required Parameters:")
                for param in req_params:
                    param_name = param.get("name", "N/A")
                    param_type = param.get("type", "N/A")
                    param_desc = param.get("description", "No description")
                    param_default = param.get("default", "None")
                    lines.append(f"    - {param_name} ({param_type}): {param_desc} [Default: {param_default}]")

            # Process optional parameters
            opt_params = method.get("optional_parameters", [])
            if opt_params:
                lines.append("  Optional Parameters:")
                for param in opt_params:
                    param_name = param.get("name", "N/A")
                    param_type = param.get("type", "N/A")
                    param_desc = param.get("description", "No description")
                    param_default = param.get("default", "None")
                    lines.append(f"    - {param_name} ({param_type}): {param_desc} [Default: {param_default}]")

            lines.append("")  # Empty line between methods
        lines.append("")  # Extra empty line after each category
    return "\n".join(lines)

# ------------------------------------------------------------------------------------------
# Function: pretty_print
# Desc: TRIVIAL
# ------------------------------------------------------------------------------------------
def pretty_print(message, printout=True):
    if isinstance(message, tuple):
        title = message
    elif isinstance(message.content, list):
        title = get_msg_title_repr(message.type.title().upper() + " Message", bold=is_interactive_env())
        if message.name is not None:
            title += f"\nName: {message.name}"

        for i in message.content:
            if i["type"] == "text":
                title += f"\n{i['text']}\n"
            elif i["type"] == "tool_use":
                title += f"\nTool: {i['name']}"
                title += f"\nInput: {i['input']}"
        if printout:
            print(f"{title}")
    else:
        title = get_msg_title_repr(message.type.title() + " Message", bold=is_interactive_env())
        if message.name is not None:
            title += f"\nName: {message.name}"
        title += f"\n\n{message.content}"
        if printout:
            print(f"{title}")
    return title

# ------------------------------------------------------------------------------------------
# Function: get_tool_decorated_functions
# Desc: TODO: To be docuemented
# ------------------------------------------------------------------------------------------
def get_tool_decorated_functions(relative_path):
    import ast
    import importlib.util
    import os

    # Get the directory of the current file (__init__.py)
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Construct the absolute path from the relative path
    file_path = os.path.join(current_dir, relative_path)

    with open(file_path) as file:
        tree = ast.parse(file.read(), filename=file_path)

    tool_function_names = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Name)
                    and decorator.id == "tool"
                    or (
                        isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Name)
                        and decorator.func.id == "tool"
                    )
                ):
                    tool_function_names.append(node.name)

    # Calculate the module name from the relative path
    package_path = os.path.relpath(file_path, start=current_dir)
    module_name = package_path.replace(os.path.sep, ".").rsplit(".", 1)[0]

    # Import the module and get the function objects
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tool_functions = [getattr(module, name) for name in tool_function_names]

    return tool_functions

