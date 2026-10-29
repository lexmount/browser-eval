"""Check report links through the shared report boundary."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.reporting.links import main

if __name__ == "__main__":
    raise SystemExit(main())
