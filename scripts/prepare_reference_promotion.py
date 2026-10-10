"""Reference promotion command backed by the shared freeze owner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.reference_freeze import prepare_promotion_main

if __name__ == "__main__":
    prepare_promotion_main(root=Path(__file__).resolve().parents[1])
