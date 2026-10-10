"""Command entry for validated WCXB report publication."""
from pathlib import Path
from harness.web_fetch.wcxb_publication import main

if __name__ == '__main__':
    main(root=Path(__file__).resolve().parents[1])
