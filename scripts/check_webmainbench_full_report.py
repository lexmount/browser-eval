"""Command-line entry point; implementation lives in harness.web_fetch.webmainbench_report_check."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.webmainbench_report_check import main

if __name__ == "__main__":
    main()
