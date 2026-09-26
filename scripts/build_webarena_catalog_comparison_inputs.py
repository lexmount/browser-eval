#!/usr/bin/env python3
"""Build shared report inputs from full-catalog acceptance ledgers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.catalog_comparison import (
    build_catalog_comparison_dataset,
    build_catalog_comparison_results,
)
from harness.trajectory_replay.catalog_contracts import file_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path, required=True)
    parser.add_argument("--results-output", type=Path, required=True)
    args = parser.parse_args()
    if args.dataset_output.exists() or args.results_output.exists():
        raise FileExistsError("catalog comparison output already exists")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    dataset = build_catalog_comparison_dataset(
        catalog_path=Path(config["catalog_path"]),
        suite_path=Path(config["suite_path"]),
        site_map_path=Path(config["site_map_path"]),
        action_overrides_path=(
            Path(config["action_overrides_path"])
            if config.get("action_overrides_path")
            else None
        ),
    )
    args.dataset_output.parent.mkdir(parents=True, exist_ok=True)
    args.dataset_output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    results = build_catalog_comparison_results(
        dataset=dataset,
        dataset_sha256=file_sha256(args.dataset_output),
        profile_sha256=file_sha256(Path(config["profile_path"])),
        scope_sha256=file_sha256(Path(config["scope_path"])),
        site_map_sha256=file_sha256(Path(config["site_map_path"])),
        participants=config["participants"],
    )
    args.results_output.parent.mkdir(parents=True, exist_ok=True)
    args.results_output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "tasks": len(dataset["trajectories"]),
                "logical_results": len(results["attempts"]),
                "physical_executions": len(results["physical_executions"]),
                "dataset_sha256": file_sha256(args.dataset_output),
                "results_sha256": file_sha256(args.results_output),
            }
        )
    )


if __name__ == "__main__":
    main()
