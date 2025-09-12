from __future__ import annotations
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
PACKAGE_ENVS_DIR = Path(__file__).with_suffix("").parent / "envs"

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

def ensure_micromamba() -> Path:
    """Download micromamba if missing; return its path."""
    _ensure_dirs()
    exe = _micromamba_target_path()
    if exe.exists():
        return exe

    sysname = platform.system()
    url = _MICROMAMBA_URLS.get(sysname)
    if not url:
        raise RuntimeError(f"Unsupported OS: {sysname}")

    # Micromamba “latest” is a tar.(bz2|zst) or zip containing ./micromamba
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "micromamba.tar.bz2"
        _download(url, tmp)
        try:
            with tarfile.open(tmp, "r:*") as tf:
                member = next(m for m in tf.getmembers() if m.name.endswith("micromamba") or m.name.endswith("micromamba.exe"))
                tf.extract(member, BIN_DIR)
                extracted = BIN_DIR / Path(member.name).name
                extracted.rename(exe)
        except Exception:
            # Some builds deliver the binary directly (no tar). Try a direct move.
            shutil.move(tmp, exe)

    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe

def env_prefix(name: str) -> Path:
    """Absolute path to the env prefix directory."""
    return ENVS_DIR / name

def has_env(name: str) -> bool:
    return (env_prefix(name) / "conda-meta").exists()

def load_registry() -> dict:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"envs": []}

def spec_path(spec_filename: str) -> Path:
    return PACKAGE_ENVS_DIR / spec_filename

@contextmanager
def _install_lock(name: str, timeout_sec: int = 1800):
    """Simple file lock to avoid concurrent installs."""
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
        try: lock.unlink()
        except FileNotFoundError: pass

def create_or_update_env(name: str, spec_file: Path, channels: list[str] | None = None) -> None:
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

    # micromamba will do install or update in place
    res = subprocess.run(args, text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"micromamba create failed:\n{res.stdout}\n{res.stderr}")

def ensure_env(name: str, auto_install: bool  = True) -> tuple[Path, bool, str]:
    """Ensure env exists from the registry. Returns (prefix, created, message)."""
    reg = load_registry()
    rec = next((e for e in reg.get("envs", []) if e.get("name") == name), None)
    if not rec:
        raise KeyError(f"Env '{name}' not found in registry")

    prefix = env_prefix(name)
    if has_env(name):
        return prefix, False, f"Environment '{name}' ready."

    if auto_install:
        with _install_lock(name):
            if has_env(name):
                return prefix, False, f"Environment '{name}' became ready."

            spec = spec_path(rec["spec"])
            ch = rec.get("channels")
            create_or_update_env(name, spec, ch)
            return prefix, True, f"Environment '{name}' created."
    else:
        raise KeyError(f"Env '{name}' not found in registry. Consider enable auto_install if you wanna install this env.")

# def run_in_env(name: str, argv: list[str], env: dict[str, str] | None = None, check: bool = False) -> subprocess.CompletedProcess:
#     """Run a command inside a named env. Ensures env exists."""
#     prefix, created, _ = ensure_env(name)
#     mm = ensure_micromamba()
#     cmd = [str(mm), "run", "-p", str(prefix), "--"] + argv
#     return subprocess.run(cmd, text=True, capture_output=True, env=env, check=check)

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
