"""Command entry for report metadata and optional byte verification."""

from pathlib import Path

from harness.web_fetch.publication_verify import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
