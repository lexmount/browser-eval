"""Capture independent source evidence for the selected dataset scope."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.source_capture import main

if __name__ == "__main__":
    main()
