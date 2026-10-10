"""Command entry for the lazy Web Fetch publication bundle."""

from pathlib import Path

from harness.web_fetch.report_package import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
