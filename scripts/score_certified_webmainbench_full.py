"""Command-line entry point; implementation lives in harness.web_fetch.webmainbench_certification."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.webmainbench_certification import main_score

if __name__ == "__main__":
    main_score()
