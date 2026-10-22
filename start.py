#!/usr/bin/env python3
"""Cross-platform launcher; Windows and Linux share the same entry point."""
from pathlib import Path
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required.")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from qq_aibot.launcher import main

if __name__ == "__main__":
    raise SystemExit(main(root=ROOT))
