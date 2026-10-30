"""Command entry point for the public WebArena trajectory report check."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.trajectory_replay.public_webarena.publication import check_main  # noqa: E402

if __name__ == "__main__":
    check_main()
