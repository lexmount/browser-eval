"""Command-line entry point; implementation lives in harness.web_fetch.wcxb_dataset."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.wcxb_dataset import main

if __name__ == "__main__":
    main()
