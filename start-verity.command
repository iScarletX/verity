#!/bin/bash
# Double-clickable macOS launcher for Verity's local Web MVP.
# Runs in the foreground; press Ctrl+C in the Terminal window that opens
# to stop the server. This script does NOT install anything.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Prefer a python3 from PATH; do not modify PATH or install anything.
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found in PATH." >&2
  echo "Install a system Python 3.9+ and try again." >&2
  read -r -p "Press Return to close..." _ || true
  exit 3
fi

echo "[verity] project: $HERE"
echo "[verity] python : $($PY --version 2>&1)"
exec "$PY" tools/start_local_web.py "$@"
