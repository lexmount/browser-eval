#!/usr/bin/env python3
"""Build the current WebArena trajectory replay report."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from harness.reporting.assets import copy_bound_assets
from harness.trajectory_replay.report_inputs import bound_dataset_path, verified_dataset_path
from harness.trajectory_replay.webarena_report import build_webarena_replay_report
from harness.web_fetch.storage import data_home

PROFILE = Path("evaluation/trajectory-replay/webarena-verified/current-run.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--profile", type=Path, default=PROFILE)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", default="reports/trajectory-replay")
    parser.add_argument(
        "--content",
        type=Path,
        default=data_home() / "report-assets/trajectory-replay",
        help="Git-external frozen article and illustrations",
    )
    parser.add_argument(
        "--content-bindings",
        type=Path,
        default=Path("research/trajectory-replay-content.json"),
    )
    parser.add_argument("--interpretation-audit", type=Path)
    parser.add_argument(
        "--include-english",
        action="store_true",
        help="Explicitly generate the English report and its assets",
    )
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    dataset_path = args.dataset or bound_dataset_path(profile)
    if args.dataset is not None:
        expected = (profile.get("dataset") or {}).get("sha256")
        if not isinstance(expected, str):
            raise ValueError("explicit report dataset requires a profile hash binding")
        dataset_path = verified_dataset_path(dataset_path, expected)
    review_path = args.review or Path("research/trajectory-replay-failure-review.json")
    bindings = json.loads(args.content_bindings.read_text())
    with tempfile.TemporaryDirectory(prefix="replay-content-") as directory:
        content = Path(directory).resolve()
        copy_bound_assets(args.content, content, bindings)
        manifest = build_webarena_replay_report(
            results_path=args.results,
            dataset_path=dataset_path,
            review_path=review_path,
            profile_path=args.profile,
            output_dir=args.output,
            include_english=args.include_english,
            interpretation_path=content / "trajectory-replay-interpretation.json",
            interpretation_audit_path=(
                args.interpretation_audit
                or Path("research/trajectory-replay-contract-audit.json")
            ),
        )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    from harness.reporting.templates import resolve_template
    resolve_template("trajectory-replay")
    main()
