#!/usr/bin/env python3
"""Run the distribution gate behind `make package-check`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.package_gate import main

if __name__ == "__main__":
    main()
