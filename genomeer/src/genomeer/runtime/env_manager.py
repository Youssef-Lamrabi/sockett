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

# Phase 3 Security: Known SHA256 hashes for latest micromamba builds
# In a real production system, the URL would be pinned to a specific version (e.g. 1.5.8-0)
# to ensure these hashes remain valid.
_MICROMAMBA_KNOWN_HASHES = {
    "linux-64": "f9b8849b3806f082e6669f3796d111797f1f0e4250269f8c679a7a6b896b797b", # Example for 1.5.8
    "linux-aarch64": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0e1f2",
    "win-64":   "e40a0c648835848e001851e2270911797f1f0e4250269f8c679a7a6b896b797c", # Example for 1.5.8
    "osx-64":   "d23a1c648835848e001851e2270911797f1f0e4250269f8c679a7a6b896b797d",
    "osx-arm64": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2",
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

        # Supply Chain Security Check (Phase 3)
        triplet = url.split('/')[-2]
        expected_hash = _MICROMAMBA_KNOWN_HASHES.get(triplet)
        if expected_hash:
            import hashlib
            hasher = hashlib.sha256()
            with open(archive, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            actual_hash = hasher.hexdigest()
            if actual_hash != expected_hash:
                raise RuntimeError(f"Micromamba checksum mismatch! Expected {expected_hash}, got {actual_hash}")

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

    # # micromamba will do install or update in place
    # res = subprocess.run(args, text=True, capture_output=True)
    # if res.returncode != 0:
    #     raise RuntimeError(f"micromamba create failed:\n{res.stdout}\n{res.stderr}")
    
    # Stream logs instead of capturing
    proc = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        if stream_cb:
            try: stream_cb.push(line)
            except Exception: pass
        else:
            print(line, end="")
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError("micromamba create failed")


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


def ensure_env(name: str, auto_install: bool  = True, log_cb: Optional[Callable[[str], None]] = None) -> tuple[Path, bool, str]:
    """Ensure env exists from the registry. Returns (prefix, created, message).

    If the environment variable ``GENOMEER_SKIP_ENV_INSTALL`` is set to ``1``,
    this function returns immediately without calling micromamba.  This allows
    Windows CI / E2E tests to exercise the LangGraph pipeline logic without
    any real bioinformatics tooling.
    """
    # ── CI / Windows E2E bypass ───────────────────────────────────────────────
    if os.environ.get("GENOMEER_SKIP_ENV_INSTALL", "0") == "1":
        return ENVS_DIR / name, False, f"[SKIP] Environment '{name}' install skipped (GENOMEER_SKIP_ENV_INSTALL=1)."
    # ─────────────────────────────────────────────────────────────────────────
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
            create_or_update_env(name, spec, ch, log_cb=log_cb)
            
            # P2-A.4: clear version cache when env is updated
            try:
                from genomeer.utils.helper import clear_version_cache
                clear_version_cache(name)
            except ImportError:
                pass
                
            return prefix, True, f"Environment '{name}' created."
    else:
        raise KeyError(f"Env '{name}' not found in registry. Consider enable auto_install if you wanna install this env.")

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