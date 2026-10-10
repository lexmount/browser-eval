"""Propose bounded click-timeout corrections when trace proves action completion."""

from harness.trajectory_replay.public_webarena.qualification import main_repair_proposal

if __name__ == "__main__":
    main_repair_proposal(kind="autowait_repairs")
