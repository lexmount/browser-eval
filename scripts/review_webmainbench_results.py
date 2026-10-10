"""Audit declared WebMainBench sample evidence through the shared diagnostics module."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.webmainbench_diagnostics import sample_review_main


if __name__ == "__main__":
    sample_review_main(root=ROOT)
