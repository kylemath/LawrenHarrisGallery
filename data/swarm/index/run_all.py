#!/usr/bin/env python3
"""Run all resumable fetchers then build the works index."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
PY = sys.executable

SCRIPTS = [
    "fetch_wikiart.py",
    "fetch_wikidata.py",
    "fetch_web_sources.py",
    "fetch_ngc.py",
    "build_index.py",
]


def main() -> int:
    for name in SCRIPTS:
        print(f"\n=== {name} ===")
        r = subprocess.run([PY, str(DIR / name)], cwd=str(DIR))
        if r.returncode != 0:
            print(f"WARN: {name} exited {r.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
