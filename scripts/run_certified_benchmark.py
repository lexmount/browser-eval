#!/usr/bin/env python3
"""Command entry for certified benchmark cohorts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.certification import main

if __name__ == "__main__":
    main()
