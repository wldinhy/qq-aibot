#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
native=0
if [[ "${1:-}" == "--native" ]]; then native=1; shift; fi
console=0
if [[ "${1:-}" == "--console" ]]; then console=1; shift; fi
if [[ $# -eq 0 && $console -eq 0 ]]; then set -- serve; fi
export PYTHONUTF8=1
if [[ $native -eq 0 && $console -eq 0 ]] && grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null && [[ -f .venv/Scripts/python.exe ]]; then
    echo '[Ubuntu terminal / Windows runtime] Reusing local NapCat and ComfyUI connections.'
    exec .venv/Scripts/python.exe -X utf8 -m qq_aibot "$@"
fi
if [[ ! -x .venv-ubuntu/bin/python ]]; then
    echo 'Please run: bash install.sh' >&2
    exit 1
fi
exec .venv-ubuntu/bin/python -m qq_aibot "$@"
