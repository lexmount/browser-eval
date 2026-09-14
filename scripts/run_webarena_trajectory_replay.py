#!/usr/bin/env python3
"""Run the frozen model-free WebArena trajectory-replay suite."""

from __future__ import annotations

import argparse

from harness.trajectory_replay.webarena_replay import run_replay_suite

CURRENT_PROFILE = "evaluation/trajectory-replay/webarena-verified/current-run.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cowagent-repo", required=True)
    parser.add_argument(
        "--diagnostic-task",
        action="append",
        type=int,
        help="run an explicitly non-promotable subset instead of the frozen full suite",
    )
    parser.add_argument(
        "--diagnostic-engine",
        action="append",
        help="run only selected registered browsers as an explicitly non-promotable diagnostic",
    )
    parser.add_argument(
        "--qualification-engine",
        help="qualify the complete frozen trajectory set on the registered reference browser",
    )
    parser.add_argument(
        "--qualified-dataset",
        help="complete qualified trajectory dataset required by a formal comparison",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_replay_suite(
        profile_path=CURRENT_PROFILE,
        seed_manifest_path=args.seeds,
        output_dir=args.output,
        cowagent_repo=args.cowagent_repo,
        diagnostic_task_ids=args.diagnostic_task,
        diagnostic_engines=args.diagnostic_engine,
        qualification_engine=args.qualification_engine,
        qualified_dataset_path=args.qualified_dataset,
    )


if __name__ == "__main__":
    main()
