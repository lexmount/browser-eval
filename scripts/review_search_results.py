"""Run the complete, frozen independent Search result review."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_search.review import main

if __name__ == "__main__":
    main()
