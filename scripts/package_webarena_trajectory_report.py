"""Command entry point for the public WebArena trajectory report package."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.trajectory_replay.public_webarena.publication import package_main  # noqa: E402

if __name__ == "__main__":
    package_main(ROOT)
