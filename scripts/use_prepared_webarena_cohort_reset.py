"""Bind a freshly completed external Rainbond reset to one replay method.

The reset itself is performed through the Rainbond control plane. This hook
copies its independently recorded receipt into the immutable method evidence.
"""
from harness.trajectory_replay.remote_runtime import main_prepared_reset


if __name__ == "__main__":
    main_prepared_reset()
