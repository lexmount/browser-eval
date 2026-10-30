"""Select a provider-blind ordinary-web speed cohort."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.speed_cohort import selection_main

if __name__ == "__main__":
    selection_main()
