import os
from genomeer.utils.stream.logstream import LogRegistry

# A single, process-wide registry — use absolute path to avoid CWD-dependent bugs
_log_dir = os.path.abspath(os.environ.get("BIOAGENT_LOG_DIR", "./logs"))
REGISTRY = LogRegistry(_log_dir)