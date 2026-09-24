"""Validate a WebArena catalog report profile and its frozen local artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from harness.trajectory_replay.catalog_contracts import validate_catalog_report_profile
from harness.trajectory_replay.webarena_runtime_source import verify_webarena_runtime_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified-full/current-report.json"),
    )
    parser.add_argument(
        "--data-home",
        type=Path,
        default=Path(os.environ.get("BROWSER_EVAL_DATA_HOME", Path.home() / ".browser-eval"))
        / "evidence/current",
    )
    args = parser.parse_args()
    verify_webarena_runtime_source()
    profile = validate_catalog_report_profile(args.profile, args.data_home)
    print(profile["publication"]["share_url"])


if __name__ == "__main__":
    main()
