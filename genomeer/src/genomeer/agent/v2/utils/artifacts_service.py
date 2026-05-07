# ------------------------------------------------------------------------------
# Single-file, drop-in artifact service (server + minimal client helpers)
# - Create runs
# - Upload files
# - List files in a run
# - Publish selected files as downloadable "artifacts" with a manifest
# - Download endpoint
#
# How to use (inside your FastAPI backend):
#
#   from genomeer.agent.v2.utils.artifacts_service import create_artifacts_router
#   app.include_router(create_artifacts_router(prefix="/api/v1/artifacts"))
#
# Or run as a tiny standalone server:
#
#   if __name__ == "__main__":
#       start_artifacts_server(host="0.0.0.0", port=8080, prefix="/api/v1/artifacts")
#
# In your agent finalizer, you can call the HTTP API (or import client helpers below):
#   from genomeer.agent.v2.utils.artifacts_service import ArtifactClient
#   client = ArtifactClient(base_url="http://localhost:8080/api/v1/artifacts")
#   client.create_run(run_id)
#   client.upload_files(run_id, ["/abs/path/file1", b"bytes here", io.BytesIO(...)]
#   client.publish_run(run_id, ["outputs/report.html", "outputs/plots/"])
#
# Env vars (optional):
#   RUNS_ROOT=/tmp/bioagent
#   ARTIFACT_ROOT=/tmp/bioagent-app/artifacts
#   PUBLIC_ARTIFACTS_URL=http://localhost:8080/api/v1/artifacts
#   OPEN_MODE=true            # disable auth dependency
#   ARTIFACTS_TIMEOUT_SEC=30  # client default timeout
#   AGENT_API_KEY=...         # optional simple header auth for client → server
#   AGENT_API_HEADER=X-Agent-Key
# ------------------------------------------------------------------------------

from __future__ import annotations
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Union, Dict, Tuple

# ----------------------------- Server-side deps -------------------------------
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ----------------------------- Client-side deps -------------------------------
import requests

# ============================== CONFIG (ENV) ==================================
RUNS_ROOT = Path(os.getenv("RUNS_ROOT", "/tmp/bioagent"))
ARTIFACT_ROOT = Path(os.getenv("ARTIFACT_ROOT", "/tmp/bioagent-app/artifacts"))
PUBLIC_ARTIFACTS_URL = os.getenv("PUBLIC_ARTIFACTS_URL", "http://localhost:8080/api/v1/artifacts")
OPEN_MODE = os.getenv("OPEN_MODE", "false").lower() == "true"

ARTIFACTS_TIMEOUT_SEC = int(os.getenv("ARTIFACTS_TIMEOUT_SEC", "30"))
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")
AGENT_API_HEADER = os.getenv("AGENT_API_HEADER", "X-Agent-Key")


# ============================== AUTH (optional) ===============================
async def maybe_auth(request: Request):
    """
    Plug your real auth here. When OPEN_MODE=true, returns None (no auth).
    Otherwise, validate request.headers or session. Raise HTTPException(401) if invalid.
    """
    if OPEN_MODE:
        return None
    # Example stub:
    token = request.headers.get("Authorization")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"sub": "user"}  # or your user object


# ============================ FILESYSTEM HELPERS ==============================
def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel_key(rel_key: str) -> str:
    key = Path(rel_key).as_posix().lstrip("/")
    if ".." in Path(key).parts:
        raise HTTPException(400, "Invalid relative path traversal")
    return key


def ensure_run_dir(run_id: str) -> Path:
    import uuid as _uuid
    try:
        _uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(400, "Invalid run_id: must be a valid UUID")
    p = RUNS_ROOT / f"run-{run_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_artifact_dir(run_id: str) -> Path:
    import uuid as _uuid
    try:
        _uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(400, "Invalid run_id: must be a valid UUID")
    p = ARTIFACT_ROOT / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _guard_under(base: Path, target: Path) -> Path:
    t = target.resolve()
    if not (t == base or base in t.parents):
        raise HTTPException(400, "Invalid path")
    return t


# ============================= PUBLISH / MANIFEST =============================
def publish_artifacts(run_id: str, temp_dir: str, expose_paths: Iterable[str], public_base: str) -> dict:
    """
    Copy selected files/dirs from temp_dir -> artifacts/run_id and build a manifest.
    """
    out_dir = ensure_artifact_dir(run_id)
    base = Path(temp_dir)
    artifacts: List[Dict] = []

    def add_file(abs_src: Path, rel_key: str):
        rel_key = _safe_rel_key(rel_key.replace("\\", "/"))
        abs_dst = out_dir / rel_key
        abs_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_src, abs_dst)

        mime = mimetypes.guess_type(abs_dst.name)[0] or "application/octet-stream"
        artifacts.append({
            "key": rel_key,
            "display_name": abs_dst.name,
            "mime_type": mime,
            "size_bytes": abs_dst.stat().st_size,
            "sha256": _sha256_of(abs_dst),
            "download_url": f"{public_base.rstrip('/')}/download/{run_id}/{rel_key}",
        })

    for p in expose_paths:
        src = (base / p)
        if src.is_file():
            add_file(src, Path(p).as_posix())
        elif src.is_dir():
            for sub in src.rglob("*"):
                if sub.is_file():
                    rel_key = (Path(p) / sub.relative_to(src)).as_posix()
                    add_file(sub, rel_key)
        else:
            # skip missing paths silently
            continue

    # Optional bundle — with size cap to prevent disk exhaustion (C-NEW-03)
    if artifacts:
        _MAX_BUNDLE_GB = float(os.environ.get("GENOMEER_MAX_BUNDLE_GB", "5"))
        _bundle_total = sum(
            (out_dir / a["key"]).stat().st_size
            for a in artifacts
            if (out_dir / a["key"]).exists()
        )
        if _bundle_total > _MAX_BUNDLE_GB * 1024 ** 3:
            import logging as _lg
            _lg.getLogger("genomeer.artifacts").warning(
                f"[artifacts] Bundle too large ({_bundle_total / 1e9:.1f} GB > {_MAX_BUNDLE_GB} GB); skipping ZIP"
            )
        else:
            bundle_key = "bundle/all_artifacts.zip"
            bundle_path = out_dir / bundle_key
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as z:
                for a in artifacts:
                    z.write(out_dir / a["key"], arcname=a["key"])
            artifacts.append({
                "key": bundle_key,
                "display_name": "all_artifacts.zip",
                "mime_type": "application/zip",
                "size_bytes": bundle_path.stat().st_size,
                "sha256": _sha256_of(bundle_path),
                "download_url": f"{public_base.rstrip('/')}/download/{run_id}/{bundle_key}",
            })

    manifest = {
        "run_id": run_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "artifacts": artifacts,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


# ============================ ROUTER / ENDPOINTS ==============================
class PublishRequest(BaseModel):
    expose_paths: List[str]


def create_artifacts_router(prefix: str = "") -> APIRouter:
    """
    Returns an APIRouter with:
      POST   {prefix}/runs/create/{run_id}
      POST   {prefix}/runs/{run_id}/upload
      GET    {prefix}/runs/{run_id}/files
      POST   {prefix}/runs/{run_id}/publish
      GET    {prefix}/download/{run_id}/{path}
      GET    {prefix}/manifest/{run_id}
    """
    router = APIRouter(prefix=prefix, tags=["artifacts"])

    @router.post("/runs/create/{run_id}")
    def create_run(run_id: str, user=Depends(maybe_auth)):
        import uuid as _uuid_mod
        try:
            _uuid_mod.UUID(run_id)
        except ValueError:
            raise HTTPException(400, "run_id must be a valid UUID v4")
        run_dir = ensure_run_dir(run_id)
        (run_dir / "uploads").mkdir(parents=True, exist_ok=True)
        return {"run_id": run_id}

    @router.post("/runs/{run_id}/upload")
    def upload_to_run(
        run_id: str,
        files: List[UploadFile] = File(...),
        subdir: str = Form(default="uploads"),
        user=Depends(maybe_auth),
    ):
        import uuid as _uuid_mod
        try:
            _uuid_mod.UUID(run_id)
        except ValueError:
            raise HTTPException(400, "run_id must be a valid UUID v4")
        run_dir = ensure_run_dir(run_id)
        target = _guard_under(run_dir, run_dir / subdir)
        target.mkdir(parents=True, exist_ok=True)

        _ALLOWED_UPLOAD_EXT = {
            '.fasta', '.fastq', '.fq', '.fa', '.gz', '.bz2', '.zst',
            '.txt', '.log', '.json', '.tsv', '.csv', '.html', '.md',
            '.bed', '.vcf', '.gff', '.gff3', '.gtf', '.sam', '.bam',
            '.bai', '.cram', '.crai', '.nwk', '.xml', '.yaml', '.yml',
        }
        _MAX_FILE_BYTES = int(os.environ.get("GENOMEER_MAX_UPLOAD_GB", "10")) * 1024 ** 3
        saved = []
        for f in files:
            ext = Path(f.filename).suffix.lower()
            if ext not in _ALLOWED_UPLOAD_EXT:
                raise HTTPException(400, f"File type '{ext}' is not permitted")
            safe_name = re.sub(r'[^a-zA-Z0-9._\-]', '_', Path(f.filename).name)
            dest = target / safe_name
            written = 0
            with dest.open("wb") as out:
                while True:
                    chunk = f.file.read(1024 * 1024)  # 1 MB chunks
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_FILE_BYTES:
                        dest.unlink(missing_ok=True)
                        raise HTTPException(413, f"File '{f.filename}' exceeds upload size limit ({_MAX_FILE_BYTES // (1024**3)} GB)")
                    out.write(chunk)
            saved.append({
                "name": f.filename,
                "size_bytes": dest.stat().st_size,
                "mime_type": mimetypes.guess_type(f.filename)[0] or "application/octet-stream",
                "rel_path": dest.relative_to(run_dir).as_posix(),
            })
        return {"run_id": run_id, "saved": saved}

    @router.get("/runs/{run_id}/files")
    def list_run_files(
        run_id: str,
        under: str = "",
        limit: int = Query(default=1000, le=10000, ge=1),
        offset: int = Query(default=0, ge=0),
        user=Depends(maybe_auth),
    ):
        import uuid as _uuid_mod
        try:
            _uuid_mod.UUID(run_id)
        except ValueError:
            raise HTTPException(400, "run_id must be a valid UUID v4")
        run_dir = ensure_run_dir(run_id)
        base = _guard_under(run_dir, run_dir / under)
        out = []
        if base.exists():
            for p in base.rglob("*"):
                if p.is_file():
                    out.append({
                        "rel_path": p.relative_to(run_dir).as_posix(),
                        "size_bytes": p.stat().st_size,
                        "mime_type": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                    })
        total = len(out)
        return {"run_id": run_id, "files": out[offset:offset + limit], "total": total, "offset": offset, "limit": limit}

    @router.post("/runs/{run_id}/publish")
    def publish_run(
        run_id: str,
        req: PublishRequest,
        user=Depends(maybe_auth),
    ):
        import uuid as _uuid_mod
        try:
            _uuid_mod.UUID(run_id)
        except ValueError:
            raise HTTPException(400, "run_id must be a valid UUID v4")
        run_dir = ensure_run_dir(run_id)
        manifest = publish_artifacts(run_id, str(run_dir), req.expose_paths, PUBLIC_ARTIFACTS_URL)
        return JSONResponse(content=manifest)

    @router.get("/download/{run_id}/{path:path}")
    def download(run_id: str, path: str):
        base = ensure_artifact_dir(run_id).resolve()
        file_path = (base / path).resolve()
        try:
            file_path.relative_to(base)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")
        if not (file_path.exists() and file_path.is_file()):
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path=str(file_path), filename=file_path.name)

    @router.get("/manifest/{run_id}")
    def get_manifest(run_id: str):
        path = ARTIFACT_ROOT / run_id / "manifest.json"
        if not path.exists():
            raise HTTPException(404, "Manifest not found")
        return JSONResponse(content=json.loads(path.read_text()))

    return router


# ============================== CLIENT HELPERS ================================
def _normalize_base(base_url: str) -> str:
    return base_url.rstrip("/")


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


@dataclass
class ArtifactClient:
    """
    Minimal HTTP client for the artifact service.
    """
    base_url: str
    timeout: int = ARTIFACTS_TIMEOUT_SEC
    extra_headers: Optional[Dict[str, str]] = None

    def _b(self) -> str:
        return _normalize_base(self.base_url)

    def create_run(self, run_id: str) -> Dict:
        url = f"{self._b()}/runs/create/{run_id}"
        r = requests.post(url, headers=_merge_headers(self.extra_headers), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def upload_files(
        self,
        run_id: str,
        files: Iterable[Union[str, Path, bytes, io.BufferedReader, io.BytesIO]],
        *,
        subdir: str = "uploads",
    ) -> Dict:
        url = f"{self._b()}/runs/{run_id}/upload"
        mp = [_to_file_tuple("files", f) for f in files]
        data = {"subdir": subdir}
        r = requests.post(url, files=mp, data=data, headers=_merge_headers(self.extra_headers), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def list_run_files(self, run_id: str, under: str = "") -> Dict:
        url = f"{self._b()}/runs/{run_id}/files"
        params = {"under": under} if under else {}
        r = requests.get(url, params=params, headers=_merge_headers(self.extra_headers), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def publish_run(self, run_id: str, expose_paths: List[str]) -> Dict:
        url = f"{self._b()}/runs/{run_id}/publish"
        payload = {"expose_paths": list(expose_paths)}
        r = requests.post(url, json=payload, headers=_merge_headers(self.extra_headers), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def build_download_url(self, run_id: str, key: str) -> str:
        key = str(key).lstrip("/")
        return f"{self._b()}/download/{run_id}/{key}"

    def get_manifest(self, run_id: str) -> Dict:
        url = f"{self._b()}/manifest/{run_id}"
        r = requests.get(url, headers=_merge_headers(self.extra_headers), timeout=self.timeout)
        r.raise_for_status()
        return r.json()


# ============================== BOOTSTRAP UTILS ===============================
def start_artifacts_server(host: str = "127.0.0.1", port: int = 8080, prefix: str = "/api/v1/artifacts"):
    """
    Convenience launcher for a tiny standalone artifacts server.
    Useful for local dev or running outside your main API.
    """
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title="Genomeer Artifacts Service")
    app.include_router(create_artifacts_router(prefix=prefix))
    print(f"Artifacts service on http://{host}:{port}/docs")
    uvicorn.run(app, host=host, port=port)


# ================================ RE-EXPORTS ==================================
# To keep backward-compat function names used in finalizer code:
def create_run(run_id: str, base_url: str = PUBLIC_ARTIFACTS_URL, timeout: int = ARTIFACTS_TIMEOUT_SEC) -> Dict:
    return ArtifactClient(base_url=base_url, timeout=timeout).create_run(run_id)

def upload_files(run_id: str, files: Iterable[Union[str, Path, bytes, io.BufferedReader, io.BytesIO]], *, subdir: str = "uploads",
                 base_url: str = PUBLIC_ARTIFACTS_URL, timeout: int = ARTIFACTS_TIMEOUT_SEC) -> Dict:
    return ArtifactClient(base_url=base_url, timeout=timeout).upload_files(run_id, files, subdir=subdir)

def publish_run_http(run_id: str, expose_paths: List[str], base_url: str = PUBLIC_ARTIFACTS_URL,
                     timeout: int = ARTIFACTS_TIMEOUT_SEC) -> Dict:
    # Named differently to avoid shadowing the server route function above
    return ArtifactClient(base_url=base_url, timeout=timeout).publish_run(run_id, expose_paths)

def get_manifest_http(run_id: str, base_url: str = PUBLIC_ARTIFACTS_URL, timeout: int = ARTIFACTS_TIMEOUT_SEC) -> Dict:
    return ArtifactClient(base_url=base_url, timeout=timeout).get_manifest(run_id)

def build_download_url(run_id: str, key: str, base_url: str = PUBLIC_ARTIFACTS_URL) -> str:
    return ArtifactClient(base_url=base_url).build_download_url(run_id, key)


# ================================ __main__ ====================================
if __name__ == "__main__":
    # CLI use: python artifacts_service.py
    start_artifacts_server()
