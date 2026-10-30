"""Acquire independent observations after matched case completion."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.reference_freeze import postcheck_main

if __name__ == "__main__":
    postcheck_main()
