"""Verify the repository WebArena deployment and evaluation contract."""

from pathlib import Path

from harness.environments.webarena.config import deployment_check_main


if __name__ == "__main__":
    deployment_check_main(default_root=Path(__file__).resolve().parents[1])
