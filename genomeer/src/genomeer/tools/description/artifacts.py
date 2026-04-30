description = [
  {
    "name": "create_run",
    "description": "Create a staging run to upload inputs and produce downloadable artifacts. Base URL and auth are handled internally. Utilities for exposing run outputs as shareable links for agent finalization.",
    "usage": "POST {ARTIFACTS_BASE_URL}/runs/create → { run_id }",
    "required_parameters": [
        {"name": "run_id", "type": "str"},],
    "optional_parameters": [],
    "returns": "dict(run_id)"
  },
  {
    "name": "upload_files",
    "description": "Upload one or more files into a run (multipart/form-data). Works with paths or bytes; subdir defaults to 'uploads'. Utilities for exposing run outputs as shareable links for agent finalization.",
    "usage": "POST {ARTIFACTS_BASE_URL}/runs/{run_id}/upload (fields: files=..., subdir='uploads')",
    "required_parameters": [
      {"name": "run_id", "type": "str"},
      {"name": "files", "type": "iterable[path|bytes|buffer]"}
    ],
    "optional_parameters": [
      {"name": "subdir", "type": "str", "default": "uploads"}
    ],
    "returns": "dict(run_id, saved=[{name, size_bytes, mime_type, rel_path}])"
  },
  {
    "name": "list_run_files",
    "description": "List files currently staged in a run directory.",
    "usage": "GET {ARTIFACTS_BASE_URL}/runs/{run_id}/files?under=uploads",
    "required_parameters": [
      {"name": "run_id", "type": "str"}
    ],
    "optional_parameters": [
      {"name": "under", "type": "str", "default": ""}
    ],
    "returns": "dict(run_id, files=[{rel_path, size_bytes, mime_type}])"
  },
  {
    "name": "publish_run",
    "description": "Publish selected paths (files or folders) from the run to artifact storage and get a manifest with permanent download URLs. Utilities for exposing run outputs as shareable links for agent finalization.",
    "usage": "POST {ARTIFACTS_BASE_URL}/runs/{run_id}/publish with JSON {\"expose_paths\":[\"outputs\",\"reports/summary.html\"]}",
    "required_parameters": [
      {"name": "run_id", "type": "str"},
      {"name": "expose_paths", "type": "list[str]"}
    ],
    "optional_parameters": [],
    "returns": "dict(run_id, created_at, artifacts=[{key, display_name, mime_type, size_bytes, sha256, download_url}])"
  },
  {
    "name": "get_manifest",
    "description": "Fetch a previously published manifest for a run. Utilities for exposing run outputs as shareable links for agent finalization.",
    "usage": "GET {ARTIFACTS_BASE_URL}/artifacts/manifest/{run_id}",
    "required_parameters": [
      {"name": "run_id", "type": "str"}
    ],
    "optional_parameters": [],
    "returns": "dict(run_id, created_at, artifacts=[...])"
  },
  {
    "name": "build_download_url",
    "description": "Utility to build the direct download URL for a known artifact key (not needed if you already use 'download_url' from manifest). Utilities for exposing run outputs as shareable links for agent finalization.",
    "usage": "client-side helper, no HTTP call",
    "required_parameters": [
      {"name": "run_id", "type": "str"},
      {"name": "key", "type": "str"}
    ],
    "optional_parameters": [],
    "returns": "str(url)"
  }
]
