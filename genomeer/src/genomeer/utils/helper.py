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
) -> subprocess.CompletedProcess[str]:
    """
    Run argv inside micromamba env <env_name> and capture output.
    """
    exe = ensure_micromamba()
    prefix = ENVS_DIR / env_name

    # micromamba run -p <prefix> -- <argv...>
    cmd = [str(exe), "run", "-p", str(prefix), *argv]
    print('---------------------------------')
    print(cmd)
    print('---------------------------------')

    env = dict(os.environ)
    env.pop("CONDA_PREFIX", None)
    env["MAMBA_ROOT_PREFIX"] = str(ENVS_DIR.parent.parent)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )
    
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
def run_r_code(code: str, *, env_name: Optional[str] = None, log_cb=None) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    code = (code or "").strip()
    if not code: 
        return "Error: Empty script"
    
    try:
        with tempfile.NamedTemporaryFile(suffix=".R", mode="w", dir=settings.run_dir, delete=False) as f:
            f.write(code); path = f.name
            
        if env_name:
            if not ensure_env(env_name, auto_install=True, log_cb=log_cb):
                return f"Environment '{env_name}' is not available." 
            proc = _run_in_env(env_name, ["Rscript", path], timeout=settings.timeout_seconds)
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
            timeout=settings.timeout_seconds,
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
def run_bash_script(script: str, *, env_name: Optional[str] = None, log_cb=None) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    script = (script or "").strip()
    if not script: 
        return "Error: Empty script"
    
    with tempfile.NamedTemporaryFile(suffix=".sh", mode="w", dir=settings.run_dir, delete=False) as f:
        if not script.startswith("#!/"): f.write("#!/bin/bash\n")
        if "set -e" not in script: f.write("set -euo pipefail\n")
        f.write(script); path = f.name
    os.chmod(path, 0o755)
    
    try:
        if env_name:
            if not ensure_env(env_name, auto_install=True, log_cb=log_cb):
                return f"Environment '{env_name}' is not available."

            proc = _run_in_env(env_name, ["bash", path], timeout=settings.timeout_seconds)
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
            timeout=settings.timeout_seconds,
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
            if not ensure_env(env_name, auto_install=True, log_cb=log_cb):
                return f"Environment '{env_name}' is not available."
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
def run_python_code(code: str, *, env_name: Optional[str] = None, log_cb=None) -> str:
    """
    Executes the provided Python code.
    - If env_name is provided: runs it in that micromamba env (fresh process).
    - If no env_name: runs in a persistent REPL namespace in the current process.
    """

    code = code.strip("```").strip()
    try: 
        # --- Case 1: run in a micromamba environment ---
        if env_name:
            if not ensure_env(env_name, auto_install=True, log_cb=log_cb):
                return f"Environment '{env_name}' is not available."

            # Write the code to a temp file
            os.makedirs(settings.run_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=".py", mode="w", dir=settings.run_dir, delete=False) as f:
                f.write(code)
                path = f.name

            try:
                proc = _run_in_env(env_name, ["python", path], timeout=settings.timeout_seconds)
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
        # "artifacts",
        # "literature",
        # "biochemistry",
        # "bioengineering",
        # "biophysics",
        # "cancer_biology",
        # "cell_biology",
        # "molecular_biology",
        # "genetics",
        # "genomics",
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

