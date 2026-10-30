"""Command-line entry point; implementation lives in harness.web_fetch.webmainbench_scoring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.web_fetch.webmainbench_scoring import main

if __name__ == "__main__":
    main()
