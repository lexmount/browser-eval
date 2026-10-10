"""Prepare supplemental review evidence through the shared review contract."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.web_fetch.result_review import prepare_subset_main


if __name__ == "__main__":
    prepare_subset_main()
