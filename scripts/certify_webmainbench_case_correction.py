"""Command entry for the shared benchmark stage."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.webmainbench_correction import certify_case_correction as main

if __name__ == "__main__":
    main()
