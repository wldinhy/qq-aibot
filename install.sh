#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Python 3.10+ is required. Debian/Ubuntu: sudo apt install python3 python3-venv' >&2
    exit 1
fi
exec python3 "$root/install.py" "$@"
