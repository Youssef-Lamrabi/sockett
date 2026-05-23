from __future__ import annotations
from typing import Callable, Optional
import os, sys, stat, json, tarfile, platform, hashlib, shutil, subprocess, tempfile, time
from pathlib import Path
from urllib.request import urlopen
from contextlib import contextmanager
import yaml

APP_DIR = Path(os.getenv("RUNTIME_PKG_HOME", Path.home() / ".bioagentpkg")).resolve()
RUNTIME_DIR = APP_DIR / "runtime" / "pkgs"
BIN_DIR = RUNTIME_DIR / "bin"
MAMBA_DIR = RUNTIME_DIR / "micromamba"
ENVS_DIR = RUNTIME_DIR / "envs"
REGISTRY_PATH = Path(__file__).with_suffix("").parent / "registry" / "index.yaml"
PACKAGE_ENVS_DIR = Path(__file__).with_suffix("").parent / "registry"

# Static micromamba URLs (linux/mac/win)
_MICROMAMBA_URLS = {
    "Linux":   "https://micro.mamba.pm/api/micromamba/linux-64/latest",
    "Darwin":  "https://micro.mamba.pm/api/micromamba/osx-64/latest",
    "Windows": "https://micro.mamba.pm/api/micromamba/win-64/latest",
}

def _micromamba_target_path() -> Path:
    exe = "micromamba.exe" if platform.system() == "Windows" else "micromamba"
    return BIN_DIR / exe

def _ensure_dirs() -> None:
    for p in (RUNTIME_DIR, BIN_DIR, MAMBA_DIR, ENVS_DIR):
        p.mkdir(parents=True, exist_ok=True)

def _download(url: str, dst: Path) -> None:
    with urlopen(url) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)

def _sys_arch() -> tuple[str, str]:
    """Return (system, normalized_machine)."""
    sysname = platform.system()   # "Linux", "Darwin", "Windows"
    mach = platform.machine().lower()  # "x86_64", "aarch64", "arm64", ...
    # normalize
    if mach in ("x86_64", "amd64"):
        mach = "x86_64"
    elif mach in ("aarch64", "arm64"):
        mach = "arm64"
    return sysname, mach

def _micromamba_download_url() -> str:
    sysname, mach = _sys_arch()
    # Map to micromamba API “triplets”
    if sysname == "Linux":
        triplet = "linux-64" if mach == "x86_64" else "linux-aarch64"
    elif sysname == "Darwin":
        triplet = "osx-64" if mach == "x86_64" else "osx-arm64"
    elif sysname == "Windows":
        triplet = "win-64"   # Windows on ARM is rare; micromamba ARM Win not generally provided
    else:
        raise RuntimeError(f"Unsupported OS: {sysname}")

    return f"https://micro.mamba.pm/api/micromamba/{triplet}/latest"

def _is_executable_binary(p: Path) -> bool:
    """Lightweight magic-number check (ELF/Mach-O/PE) + exec bit."""
    try:
        with open(p, "rb") as f:
            head = f.read(8)
    except Exception:
        return False
    # ELF
    if head.startswith(b"\x7fELF"):
        return os.access(p, os.X_OK)
    # Mach-O (32/64, big/little)
    if head in (b"\xFE\xED\xFA\xCE", b"\xCE\xFA\xED\xFE", b"\xFE\xED\xFA\xCF", b"\xCF\xFA\xED\xFE"):
        return os.access(p, os.X_OK)
    # PE (Windows)
    if head.startswith(b"MZ"):
        return True  # Windows doesn’t use exec bit
    return False

# def ensure_micromamba() -> Path:
#     """Download micromamba if missing; return its path."""
#     _ensure_dirs()
#     exe = _micromamba_target_path()
#     if exe.exists():
#         return exe

#     sysname = platform.system()
#     url = _MICROMAMBA_URLS.get(sysname)
#     if not url:
#         raise RuntimeError(f"Unsupported OS: {sysname}")

#     # Micromamba “latest” is a tar.(bz2|zst) or zip containing ./micromamba
#     with tempfile.TemporaryDirectory() as td:
#         tmp = Path(td) / "micromamba.tar.bz2"
#         _download(url, tmp)
#         try:
#             with tarfile.open(tmp, "r:*") as tf:
#                 member = next(m for m in tf.getmembers() if m.name.endswith("micromamba") or m.name.endswith("micromamba.exe"))
#                 tf.extract(member, BIN_DIR)
#                 extracted = BIN_DIR / Path(member.name).name
#                 extracted.rename(exe)
#         except Exception:
#             # Some builds deliver the binary directly (no tar). Try a direct move.
#             shutil.move(tmp, exe)

#     exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
#     return exe


# TODO: To be tested
#----------------------------------------------------------------------------------------------
def ensure_micromamba() -> Path:
    """
    Download and extract micromamba for the correct OS/arch; return its path.
    Guarantees the file is a real executable (not an archive) and matches platform.
    """
    _ensure_dirs()
    exe = _micromamba_target_path()

    # If present but clearly wrong (e.g., stale archive), nuke it first
    if exe.exists() and not _is_executable_binary(exe):
        try: exe.unlink()
        except Exception: pass

    if exe.exists():
        return exe

    url = _micromamba_download_url()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Use an extension so tar's -a (auto-compress) can detect format if we need it
        archive = td / "micromamba.tar.zst"
        _download(url, archive)

        extracted_bin: Path | None = None

        # First try Python tarfile (works for .tar, .tar.gz, .tar.bz2)
        try:
            with tarfile.open(archive, "r:*") as tf:
                member = next(
                    m for m in tf.getmembers()
                    if os.path.basename(m.name) in ("micromamba", "micromamba.exe")
                )
                tf.extract(member, td)
                extracted_bin = td / member.name
        except tarfile.ReadError:
            # Probably zstd; use system tar with explicit zstd program
            # Prefer -I zstd; fallback to --use-compress-program=unzstd if needed
            cmds = [
                ["tar", "-I", "zstd", "-xf", str(archive), "-C", str(td)],
                ["tar", "--use-compress-program=unzstd", "-xf", str(archive), "-C", str(td)],
                ["tar", "-xf", str(archive), "-C", str(td)],  # last-ditch
            ]
            ok = False
            for cmd in cmds:
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    ok = True
                    break
                except Exception:
                    continue
            if not ok:
                raise RuntimeError("Failed to extract micromamba archive (zstd/tar not available).")

            # common locations after extraction
            for cand in (
                td / "micromamba",
                td / "bin" / "micromamba",
                td / "Library" / "bin" / "micromamba.exe",
            ):
                if cand.exists():
                    extracted_bin = cand
                    break

        if not extracted_bin or not extracted_bin.exists():
            raise RuntimeError("micromamba binary not found in the downloaded archive")

        exe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted_bin, exe)

    # Ensure executable bit on Unix
    try:
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

    # Final sanity check: it must be a real executable and runnable
    if not _is_executable_binary(exe):
        try: exe.unlink()
        except Exception: pass
        raise RuntimeError("Downloaded micromamba is not a valid executable for this platform.")

    try:
        subprocess.run([str(exe), "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        # Wrong arch will surface here as Exec format error — clear cache and surface a helpful message
        try: exe.unlink()
        except Exception: pass
        raise RuntimeError(f"micromamba not runnable on this platform ({platform.system()} {platform.machine()}): {e}")

    return exe


_PIP_SENTINEL = ".genomeer_pip_ok"

def env_prefix(name: str) -> Path:
    """Absolute path to the env prefix directory."""
    return ENVS_DIR / name

def has_env(name: str) -> bool:
    return (env_prefix(name) / "conda-meta").exists()

def has_pip_installed(name: str) -> bool:
    """True only after the explicit pip post-install step completed successfully."""
    return (env_prefix(name) / "conda-meta" / _PIP_SENTINEL).exists()

def _pip_packages_from_spec(spec_file: Path) -> list[str]:
    """Return the pip package list from a conda YAML spec, skipping broken editable installs."""
    try:
        with open(spec_file, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f) or {}
    except Exception:
        return []
    packages = []
    for dep in spec.get("dependencies", []):
        if isinstance(dep, dict) and "pip" in dep:
            for pkg in (dep["pip"] or []):
                if not isinstance(pkg, str):
                    continue
                # Skip editable installs whose local path does not exist on this machine
                if pkg.startswith("-e "):
                    local_path = pkg[3:].strip()
                    if not Path(local_path).exists():
                        continue
                packages.append(pkg)
    return packages

def _pip_install_from_spec(
    prefix: Path,
    spec_file: Path,
    stream_cb=None,
) -> None:
    """
    Explicitly install all pip: packages from the YAML spec into the given prefix.
    Writes a sentinel file on success so the install is not repeated.
    This works around the Windows micromamba bug where pip: sub-sections are silently skipped.
    """
    packages = _pip_packages_from_spec(spec_file)
    if not packages:
        (prefix / "conda-meta" / _PIP_SENTINEL).touch()
        return

    python_exe = prefix / ("python.exe" if platform.system() == "Windows" else "bin/python")
    if not python_exe.exists():
        raise RuntimeError(f"python not found in env prefix: {python_exe}")

    args = [str(python_exe), "-m", "pip", "install", "--quiet"] + packages
    proc = subprocess.Popen(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1
    )
    rc = _drain_proc(proc, stream_cb=stream_cb, timeout_sec=600)
    if rc != 0:
        raise RuntimeError(f"pip install failed (exit {rc}) for env at {prefix}")

    (prefix / "conda-meta" / _PIP_SENTINEL).touch()

def load_registry() -> dict:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"envs": []}

def spec_path(spec_filename: str) -> Path:
    return PACKAGE_ENVS_DIR / spec_filename

@contextmanager
def _install_lock(name: str, timeout_sec: int = 1800):
    """Simple file lock to avoid concurrent installs."""
    _ensure_dirs()
    ENVS_DIR.mkdir(parents=True, exist_ok=True)
    
    lock = ENVS_DIR / f"{name}.lock"
    t0 = time.time()
    while lock.exists():
        if time.time() - t0 > timeout_sec:
            raise TimeoutError(f"Timed out waiting for lock: {lock}")
        time.sleep(1)
    try:
        lock.touch()
        yield
    finally:
        try: 
            lock.unlink()
        except FileNotFoundError: 
            pass

def _drain_proc(proc: subprocess.Popen, stream_cb=None, timeout_sec: int = 1800) -> int:
    """
    Drain proc.stdout line by line with a hard wall-clock timeout.
    Kills the process if it produces no output and hangs beyond timeout_sec.
    Returns the process return code.
    """
    import threading, queue as _queue

    lines_q: _queue.Queue = _queue.Queue()
    sentinel = object()

    def _reader():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines_q.put(line)
        finally:
            lines_q.put(sentinel)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    deadline = time.time() + timeout_sec
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            proc.kill()
            raise TimeoutError(f"Process timed out after {timeout_sec}s")
        try:
            item = lines_q.get(timeout=min(remaining, 30))
        except _queue.Empty:
            # No output for 30 s — check if process is still alive
            if proc.poll() is not None:
                break
            continue
        if item is sentinel:
            break
        line = item
        if stream_cb:
            try:
                stream_cb.push(line)
            except Exception:
                pass
        else:
            print(line, end="")

    t.join(timeout=5)
    return proc.wait(timeout=10)


# [deprecated]
def create_or_update_env(name: str, spec_file: Path, channels: list[str] | None = None, stream_cb: Optional[Callable[[str], None]] = None,) -> None:
    mm = ensure_micromamba()
    prefix = env_prefix(name)
    prefix.parent.mkdir(parents=True, exist_ok=True)

    args = [
        str(mm),
        "create",
        "-y",
        "-p", str(prefix),
        "-f", str(spec_file),
    ]
    if channels:
        for ch in channels:
            args += ["-c", ch]

    proc = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
    rc = _drain_proc(proc, stream_cb=stream_cb, timeout_sec=1800)
    if rc != 0:
        raise RuntimeError("micromamba create failed")

    # Explicitly install pip packages (workaround: micromamba on Windows silently skips pip: sections)
    _pip_install_from_spec(prefix, spec_file, stream_cb=stream_cb)


def install_env_iter(name: str, spec_file: Path, channels: list[str] | None = None):
    """
    Start micromamba create and yield stdout lines progressively.
    Yields (line: str) and finally raises StopIteration when done.
    """
    mm = ensure_micromamba()
    prefix = env_prefix(name)
    prefix.parent.mkdir(parents=True, exist_ok=True)

    args = [str(mm), "create", "-y", "-p", str(prefix), "-f", str(spec_file)]
    if channels:
        for ch in channels:
            args += ["-c", ch]

    proc = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
    finally:
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError("micromamba create failed")


def ensure_env(name: str, auto_install: bool = True, log_cb: Optional[Callable[[str], None]] = None) -> tuple[Path, bool, str]:
    """Ensure env exists and pip packages are installed. Returns (prefix, created, message)."""
    reg = load_registry()
    rec = next((e for e in reg.get("envs", []) if e.get("name") == name), None)
    if not rec:
        raise KeyError(f"Env '{name}' not found in registry")

    prefix = env_prefix(name)

    if has_env(name):
        # Env directory exists but pip packages may be absent (Windows micromamba bug:
        # conda create silently skips pip: sections). Check the sentinel and repair if needed.
        if not has_pip_installed(name):
            if auto_install:
                _pip_install_from_spec(prefix, spec_path(rec["spec"]), stream_cb=log_cb)
            else:
                raise RuntimeError(
                    f"Env '{name}' exists but pip packages are not installed "
                    f"(sentinel {_PIP_SENTINEL} missing). Re-run with auto_install=True."
                )
        return prefix, False, f"Environment '{name}' ready."

    if auto_install:
        with _install_lock(name):
            if has_env(name):
                # Another thread just created it — still check pip sentinel.
                if not has_pip_installed(name):
                    _pip_install_from_spec(prefix, spec_path(rec["spec"]), stream_cb=log_cb)
                return prefix, False, f"Environment '{name}' became ready."

            spec = spec_path(rec["spec"])
            ch = rec.get("channels")
            create_or_update_env(name, spec, ch, stream_cb=log_cb)
            return prefix, True, f"Environment '{name}' created."
    else:
        raise KeyError(
            f"Env '{name}' not found. Set auto_install=True to create it automatically."
        )

def list_envs() -> list[dict]:
    reg = load_registry()
    out = []
    for e in reg.get("envs", []):
        name = e["name"]
        out.append({
            "name": name,
            "description": e.get("description", ""),
            "present": has_env(name),
            "prefix": str(env_prefix(name)),
            "spec": e.get("spec"),
        })
    return out