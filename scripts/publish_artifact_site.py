"""Command entry for profile-bound Web Fetch package submission."""

from pathlib import Path

from harness.web_fetch.report_publish import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
