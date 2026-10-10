"""Project a complete provider cohort without changing retained physical calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.evidence import projection_main

if __name__ == "__main__":
    projection_main()
