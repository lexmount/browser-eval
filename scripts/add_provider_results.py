"""Validate and add a complete provider run to canonical evidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.evidence import addition_main

if __name__ == "__main__":
    addition_main()
