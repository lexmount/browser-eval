"""Command entry for report acceptance checks."""
from pathlib import Path

from harness.web_fetch.report_check import main

if __name__ == "__main__":
    main(authority_root=Path(__file__).resolve().parents[1])
