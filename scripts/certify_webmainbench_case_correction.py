"""Command entry for the shared benchmark stage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.webmainbench_correction import certify_case_correction as main

if __name__ == "__main__":
    main()
