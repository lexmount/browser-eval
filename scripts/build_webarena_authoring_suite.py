"""Build a warm-site, retrieve-only suite for deterministic trajectory authoring."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness.trajectory_replay.public_webarena.qualification import main_authoring_suite

if __name__ == "__main__":
    main_authoring_suite()
