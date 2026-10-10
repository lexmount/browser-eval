"""Propose replacing one stale recorded element target while preserving the action path."""

from harness.trajectory_replay.public_webarena.qualification import main_repair_proposal

if __name__ == "__main__":
    main_repair_proposal(kind="replace_target")
