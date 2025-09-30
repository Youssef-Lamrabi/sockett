# tools_artifacts.py
from __future__ import annotations
from typing import Dict, Iterable, List, Optional, Tuple, Union
from pathlib import Path
import requests
import io
import os

# ── Config (change via env, no need to pass per call) ──────────────────────────
# Include the full prefix to your router, e.g. http(s)://host/api/v1/artifacts
ARTIFACTS_BASE_URL = os.getenv("ARTIFACTS_BASE_URL", "http://localhost:8080/api/v1/artifacts")

# Optional global auth header (kept super simple)
# If set, every request will include:  X-Agent-Key: <value>
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
AGENT_API_HEADER = os.getenv("AGENT_API_HEADER", "X-Agent-Key")

DEFAULT_TIMEOUT = int(os.getenv("ARTIFACTS_TIMEOUT_SEC", "30"))

# ── Internal helpers ───────────────────────────────────────────────────────────
def _normalize_base() -> str:
    return ARTIFACTS_BASE_URL.rstrip("/")

def _default_headers() -> Dict[str, str]:
    if AGENT_API_KEY:
        return {AGENT_API_HEADER: AGENT_API_KEY}
    return {}

def _merge_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = dict(_default_headers())
    if extra:
        h.update(extra)
    return h

def _to_file_tuple(
    key: str,
    value: Union[str, Path, bytes, io.BufferedReader, io.BytesIO]
) -> Tuple[str, tuple]:
    """
    Convert a path or bytes-like object into ('files', (filename, fileobj)) for multipart.
    """
    if isinstance(value, (str, Path)):
        p = Path(value)
        return (key, (p.name, p.open("rb")))
    if isinstance(value, io.BytesIO):
        value.seek(0)
        return (key, ("blob", value))
    if isinstance(value, io.BufferedReader):
        return (key, (getattr(value, "name", "blob"), value))
    if isinstance(value, (bytes, bytearray)):
        return (key, ("blob", io.BytesIO(value)))
    raise TypeError(f"Unsupported file value type for key '{key}': {type(value)}")

# ── Public API (no base_url arg anywhere) ──────────────────────────────────────
def create_run(
    run_id: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """
    POST /runs/create → {"run_id": "..."}
    """
    base = _normalize_base()
    url = base + f"/runs/create/{run_id}"
    r = requests.post(url, headers=_merge_headers(headers), timeout=timeout)
    r.raise_for_status()
    return r.json()

def upload_files(
    run_id: str,
    files: Iterable[Union[str, Path, bytes, io.BufferedReader, io.BytesIO]],
    *,
    subdir: str = "uploads",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """
    POST /runs/{run_id}/upload (multipart/form-data)
    - files: iterable of paths or bytes-like objects.
    - subdir: optional subdirectory under the run root.

    Returns: {"run_id": "...", "saved": [{"name","size_bytes","mime_type","rel_path"}]}
    """
    base = _normalize_base()
    url = base + f"/runs/{run_id}/upload"
    mp = [_to_file_tuple("files", f) for f in files]  # repeated 'files' fields
    data = {"subdir": subdir}
    r = requests.post(url, files=mp, data=data, headers=_merge_headers(headers), timeout=timeout)
    r.raise_for_status()
    return r.json()

def list_run_files(
    run_id: str,
    *,
    under: str = "",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """
    GET /runs/{run_id}/files?under=...
    Returns: {"run_id":"...","files":[{"rel_path","size_bytes","mime_type"}]}
    """
    base = _normalize_base()
    url = base + f"/runs/{run_id}/files"
    params = {"under": under} if under else {}
    r = requests.get(url, params=params, headers=_merge_headers(headers), timeout=timeout)
    r.raise_for_status()
    return r.json()

def publish_run(
    run_id: str,
    expose_paths: List[str],
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """
    POST /runs/{run_id}/publish with JSON {"expose_paths":[...]}
    Returns manifest: {"run_id","created_at","artifacts":[{"key","download_url",...}]}
    """
    base = _normalize_base()
    url = base + f"/runs/{run_id}/publish"
    payload = {"expose_paths": list(expose_paths)}
    r = requests.post(url, json=payload, headers=_merge_headers(headers), timeout=timeout)
    r.raise_for_status()
    return r.json()

def get_manifest(
    run_id: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """
    GET /artifacts/manifest/{run_id}
    """
    base = _normalize_base()
    url = base + f"/artifacts/manifest/{run_id}"
    r = requests.get(url, headers=_merge_headers(headers), timeout=timeout)
    r.raise_for_status()
    return r.json()

def build_download_url(run_id: str, key: str) -> str:
    """
    Build /artifacts/download URL for a given artifact key
    (usually you'll just use the 'download_url' returned in the manifest).
    """
    base = _normalize_base()
    key = str(key).lstrip("/")
    return f"{base}/artifacts/download/{run_id}/{key}"
