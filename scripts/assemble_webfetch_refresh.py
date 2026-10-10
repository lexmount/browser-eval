"""Assemble a declared provider-cohort refresh without selecting results by score."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.refresh_assembly import main

if __name__ == "__main__":
    main()
