#!/usr/bin/env python3
"""Bind independent source captures to a completed or retained partial run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.web_fetch.reference_lifecycle import observations_main

if __name__ == "__main__":
    observations_main()
