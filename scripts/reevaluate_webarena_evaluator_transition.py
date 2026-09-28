"""Build a receipt proving old passing evidence still passes the pinned evaluator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.catalog_contracts import file_sha256, verify_physical_evidence
from harness.trajectory_replay.evaluator_transition import (
    RECEIPT_SCHEMA,
    compatible_source,
    json_sha256,
    load_transition,
    reevaluate_pass,
)
from harness.trajectory_replay.remote_runtime import remote_benchmark
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_runtime_source import verify_webarena_runtime_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transition", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--exclude-task", type=int, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    verify_webarena_runtime_source()
    transition = load_transition(args.transition)
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    sites = json.loads(args.site_map.read_text(encoding="utf-8"))["sites"]
    benchmark = remote_benchmark(sites)
    corrections = load_corrections()
    rows = []
    identities = set()
    excluded = set(args.exclude_task)
    observed_excluded = set()
    for directory in args.run:
        inputs_path = directory / "inputs.json"
        progress_path = directory / "progress.jsonl"
        inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
        source = compatible_source(
            transition,
            revision=inputs.get("webarena_revision"),
            evaluator_fixes_sha256=inputs.get("evaluator_fixes_sha256"),
        )
        if source is None:
            continue
        for row in map(json.loads, progress_path.read_text(encoding="utf-8").splitlines()):
            if row.get("status") != "pass":
                continue
            if row["task_id"] in excluded:
                observed_excluded.add(row["task_id"])
                continue
            evaluation_path = verify_physical_evidence(directory, row)
            if evaluation_path is None:
                raise ValueError(f"task {row['task_id']} pass lacks evaluation evidence")
            old = json.loads(evaluation_path.read_text(encoding="utf-8"))
            if (
                old.get("webarena_verified_evaluator_checksum")
                != source["evaluator_checksum"]
                or old.get("webarena_verified_data_checksum")
                != source["data_checksum"]
            ):
                raise ValueError(f"task {row['task_id']} has an unexpected old evaluator")
            current, binding = reevaluate_pass(
                directory / f"task-{row['task_id']}",
                task_id=row["task_id"],
                expected_data_checksum=scope["official_dataset_sha256"],
                benchmark=benchmark,
                corrections=corrections,
            )
            del current
            identity = (binding["task_id"], binding["old_evaluation_sha256"])
            if identity in identities:
                continue
            identities.add(identity)
            rows.append(
                {
                    "run_inputs_sha256": file_sha256(inputs_path),
                    "source": source,
                    **binding,
                }
            )
    rows.sort(key=lambda row: (row["task_id"], row["old_evaluation_sha256"]))
    if observed_excluded != excluded:
        raise ValueError("excluded transition tasks were not found as old passes")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "transition_sha256": json_sha256(transition),
        "from_revision": transition["from_revision"],
        "compatible_from_revisions": transition["compatible_from_revisions"],
        "compatible_sources": transition["compatible_sources"],
        "from_evaluator_checksum": transition["from_evaluator_checksum"],
        "from_data_checksum": transition["from_data_checksum"],
        "to_revision": transition["to_revision"],
        "to_evaluator_checksum": transition["to_evaluator_checksum"],
        "to_data_checksum": transition["to_data_checksum"],
        "row_count": len(rows),
        "excluded_task_ids": sorted(excluded),
        "rows": rows,
    }
    if args.output.exists():
        raise ValueError("transition receipt output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "output": str(args.output)}))


if __name__ == "__main__":
    main()
