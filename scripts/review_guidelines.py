"""Synchronize shared guidance and generate the repository's complete OCR entry."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.review_guidelines import main


if __name__ == "__main__":
    raise SystemExit(main())
