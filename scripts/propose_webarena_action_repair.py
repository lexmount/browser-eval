"""Build an auditable trajectory repair from exact, guarded action splices."""

from harness.trajectory_replay.public_webarena.qualification import main_repair_proposal

if __name__ == "__main__":
    main_repair_proposal(kind="action_repair")
