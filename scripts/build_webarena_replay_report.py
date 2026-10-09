"""Build the current WebArena trajectory replay report."""
from harness.trajectory_replay.webarena_report import build_main


if __name__ == "__main__":
    from harness.reporting.templates import resolve_template
    resolve_template("trajectory-replay")
    build_main()
