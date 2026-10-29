"""Build or validate a Web Search API report."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_search.report import main

if __name__ == "__main__":
    main()
