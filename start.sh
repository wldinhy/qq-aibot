#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
export PYTHONUTF8=1
# Preserve the existing WSL terminal / Windows runtime service bridge.
if [[ "${1:-}" != "--native" && "${1:-}" != "--console" ]] &&
   grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null &&
   [[ -f .venv/Scripts/python.exe ]]; then
    if [[ $# -eq 0 ]]; then set -- serve; fi
    echo '[Ubuntu terminal / Windows runtime] Reusing local NapCat and ComfyUI connections.'
    exec .venv/Scripts/python.exe -X utf8 -m qq_aibot "$@"
fi
exec python3 start.py "$@"
