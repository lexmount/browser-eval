#!/usr/bin/env python3
"""Build the current WebArena trajectory replay report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.webarena_report import build_webarena_replay_report


PROFILE = Path("evaluation/trajectory-replay/webarena-verified/current-run.json")


def _bound_dataset_path(profile: dict) -> Path:
    dataset = profile.get("dataset") or {}
    path_value = dataset.get("path")
    digest = dataset.get("sha256")
    if not isinstance(path_value, str) or not path_value or not isinstance(digest, str):
        raise ValueError("three-method report profile requires a bound corrected dataset")
    path = Path(path_value)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents) or not path.is_file():
        raise ValueError("bound corrected dataset is missing or symlinked")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("bound corrected dataset changed")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", default="reports/trajectory-replay")
    args = parser.parse_args()
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    dataset_path = _bound_dataset_path(profile)
    manifest = build_webarena_replay_report(
        results_path=args.results,
        dataset_path=dataset_path,
        review_path="research/trajectory-replay-failure-review.json",
        profile_path=PROFILE,
        output_dir=args.output,
        interpretation_path="research/trajectory-replay-interpretation.json",
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
