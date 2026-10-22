#!/usr/bin/env python3
"""The same dependency-free installer entry point on Windows and Linux."""
from pathlib import Path
import os
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required.")

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from qq_aibot.installer import main

if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
