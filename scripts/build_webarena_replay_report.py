#!/usr/bin/env python3
"""Build the current WebArena trajectory replay report."""

from __future__ import annotations

import argparse
import json

from harness.trajectory_replay.webarena_report import build_webarena_replay_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", default="reports/trajectory-replay")
    parser.add_argument(
        "--diagnostic-closure", help="Optional hash-bound original-36 diagnostic appendix"
    )
    args = parser.parse_args()
    manifest = build_webarena_replay_report(
        results_path=args.results,
        dataset_path="evaluation/trajectory-replay/webarena-verified/trajectories.json",
        review_path="research/trajectory-replay-failure-review.json",
        profile_path="evaluation/trajectory-replay/webarena-verified/current-run.json",
        output_dir=args.output,
        diagnostic_closure_path=args.diagnostic_closure,
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
