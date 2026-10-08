"""Command entry for certified WCXB scoring."""
from pathlib import Path

from harness.web_fetch.wcxb_scoring import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
