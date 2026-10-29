"""Publish a checked Search report to its bound Artifact Site."""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_search.publication import main
if __name__ == "__main__":
    main()
