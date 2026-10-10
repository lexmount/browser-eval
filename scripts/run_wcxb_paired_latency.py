"""Command entry for the shared WCXB paired timing collector."""
from pathlib import Path
from harness.web_fetch.wcxb_latency import main

if __name__ == '__main__':
    main(root=Path(__file__).resolve().parents[1])
