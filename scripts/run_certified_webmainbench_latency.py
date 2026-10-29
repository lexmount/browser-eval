"""Command entry for the shared latency correction stage."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.webmainbench_latency_correction import certify as main

if __name__ == "__main__":
    main()
