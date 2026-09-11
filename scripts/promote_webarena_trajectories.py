#!/usr/bin/env python3
"""Promote replay-proven WebArena trajectories into the repository dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.webarena_dataset import build_verified_trajectory_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--required-engine", action="append", default=[])
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = build_verified_trajectory_dataset(
        args.seeds,
        args.receipt,
        required_engines=tuple(args.required_engine or ("chromium", "moli")),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
