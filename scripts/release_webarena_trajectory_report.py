"""Command entry point for the public WebArena trajectory report release."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "src"))

from harness.trajectory_replay.public_webarena.release import main  # noqa: E402

if __name__ == "__main__":
    main(ROOT)
