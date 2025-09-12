import os, tempfile, subprocess, traceback, shlex, threading
from genomeer.config import settings
from typing import Any, Callable, Iterable, Mapping, Optional

# ------------------------------------------------------------------------------------------
# Function: run_r_code
# Desc: Helper function for LLM to run R code while using tools
# TODO: This tool doesn't accept input agrs yet. To be done.
# ------------------------------------------------------------------------------------------
def run_r_code(code: str) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(suffix=".R", mode="w", dir=settings.run_dir, delete=False) as f:
            f.write(code); path = f.name
        res = subprocess.run(["Rscript", path], capture_output=True, text=True, check=False, timeout=settings.timeout_seconds)
        os.unlink(path)
        return res.stdout if res.returncode == 0 else f"Error running R code:\n{res.stderr}"
    except Exception as e:
        return f"Error running R code: {e}"


# ------------------------------------------------------------------------------------------
# Function: run_bash_script
# Desc: Helper function for LLM to run bash_code code while using tools
# TODO: This tool doesn't accept input agrs yet. To be done.
# ------------------------------------------------------------------------------------------
def run_bash_script(script: str) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    try:
        script = (script or "").strip()
        if not script: return "Error: Empty script"
        with tempfile.NamedTemporaryFile(suffix=".sh", mode="w", dir=settings.run_dir, delete=False) as f:
            if not script.startswith("#!/"): f.write("#!/bin/bash\n")
            if "set -e" not in script: f.write("set -e\n")
            f.write(script); path = f.name
        os.chmod(path, 0o755)
        res = subprocess.run([path], shell=True, capture_output=True, text=True, check=False, timeout=settings.timeout_seconds)
        os.unlink(path)
        return res.stdout if res.returncode == 0 else f"Error running Bash script (exit {res.returncode}):\n{res.stderr}"
    except Exception as e:
        traceback.print_exc()
        return f"Error running Bash script: {e}"


# ------------------------------------------------------------------------------------------
# Function: run_cli_command
# Desc: Helper function for LLM to run command in shell while using tools
# TODO: This tool doesn't accept input agrs yet. To be done.
# ------------------------------------------------------------------------------------------
def run_cli_command(command: str) -> str:
    os.makedirs(settings.run_dir, exist_ok=True)
    try:
        command = (command or "").strip()
        if not command: return "Error: Empty command"
        args = shlex.split(command)
        res = subprocess.run(args, capture_output=True, text=True, check=False, timeout=settings.timeout_seconds)
        return res.stdout if res.returncode == 0 else f"Error running command '{command}':\n{res.stderr}"
    except Exception as e:
        return f"Error running command '{command}': {e}"
    
    
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