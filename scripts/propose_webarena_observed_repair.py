"""Propose retrieve repairs whose answers come only from declared DOM observations."""

from harness.trajectory_replay.public_webarena.qualification import main_repair_proposal

if __name__ == "__main__":
    main_repair_proposal(kind="observed_repair")
