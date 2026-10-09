"""Assemble a declared WebMainBench sample through the shared evidence pipeline."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.webmainbench_assembly import sample_main


if __name__ == "__main__":
    sample_main(root=ROOT)
