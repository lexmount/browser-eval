"""Command entry for report presentation and adoption checks."""
from pathlib import Path

from harness.web_fetch.report_check import revision_main

if __name__ == "__main__":
    revision_main(authority_root=Path(__file__).resolve().parents[1])
