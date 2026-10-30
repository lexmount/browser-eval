#!/usr/bin/env python3
"""Merge hash-bound independent review shards through the shared review contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.web_fetch.result_review import merge_shards_main


if __name__ == "__main__":
    merge_shards_main()
