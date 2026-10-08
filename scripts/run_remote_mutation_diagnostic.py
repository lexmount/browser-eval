"""Command entry for a receipt-bound remote mutation diagnostic."""

from harness.trajectory_replay.remote_execution import main_mutation_diagnostic

if __name__ == "__main__":
    raise SystemExit(main_mutation_diagnostic())
