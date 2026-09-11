#!/usr/bin/env python3
"""Run a small model-free WebArena trajectory replay canary."""

from __future__ import annotations

import argparse

from harness.trajectory_replay.webarena_replay import run_canary_replay


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--task", action="append", required=True, type=int)
    parser.add_argument("--engine", action="append", choices=("chromium", "moli"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cowagent-repo", required=True)
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--sample-interval-ms", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_canary_replay(
        seed_manifest_path=args.seeds,
        task_ids=args.task,
        engines=args.engine,
        output_dir=args.output,
        cowagent_repo=args.cowagent_repo,
        cpus=args.cpus,
        memory=args.memory,
        sample_interval_ms=args.sample_interval_ms,
    )


if __name__ == "__main__":
    main()
