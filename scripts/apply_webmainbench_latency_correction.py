"""Command entry for the shared latency correction stage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.webmainbench_latency_correction import main as main

if __name__ == "__main__":
    main()
