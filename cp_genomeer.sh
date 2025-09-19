#!/usr/bin/env bash
set -euo pipefail
SRC="genomeer"
DST="agent-ui/backend/open_webui/agent"

mkdir -p "$DST"
rsync -a --info=progress2 \
  --exclude=".venv/" \
  --exclude="/src/genomeer.egg-info/" \
  "$SRC" "$DST"

echo "Done"