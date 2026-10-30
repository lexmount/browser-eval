"""Freeze independent references with explicit promotion for scope changes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.reference_freeze import main

if __name__ == "__main__":
    main()
