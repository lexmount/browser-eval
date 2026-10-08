"""Command entry for Web Fetch publication bundle validation."""

from pathlib import Path

from harness.web_fetch.publication_check import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
