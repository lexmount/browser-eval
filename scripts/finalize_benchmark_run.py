#!/usr/bin/env python3
"""Finalize every worker reference certificate in an aggregated benchmark run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.web_fetch.evidence import finalize_main

if __name__ == "__main__":
    finalize_main()
