"""Command-line entry point; implementation lives in harness.web_fetch.webmainbench_runner."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from harness.web_fetch.webmainbench_runner import main

if __name__ == "__main__":
    main()
