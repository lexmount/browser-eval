"""Reference promotion command backed by the shared freeze owner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.reference_freeze import promote_main

if __name__ == "__main__":
    promote_main(root=Path(__file__).resolve().parents[1])
