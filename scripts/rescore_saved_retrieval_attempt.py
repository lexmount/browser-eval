"""Rescore one immutable retrieval attempt against a newer fork without browser calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from webarena_verified.api import WebArenaVerified
from webarena_verified.types.config import WebArenaVerifiedConfig

from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    remote_benchmark,
)


def rescore(
    *, task_dir: Path, task_id: int, dataset: Path, site_map: Path
) -> dict:
    result_path = task_dir.parent / "result.json"
    if result_path.is_symlink():
        raise ValueError("saved diagnostic result is symlinked")
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result_path = task_dir.parent / "receipt.json"
        if result_path.is_symlink() or not result_path.is_file():
            raise ValueError("saved diagnostic receipt is missing or symlinked")
        receipt = json.loads(result_path.read_text(encoding="utf-8"))
        rows = [row for row in receipt.get("results") or [] if row.get("task_id") == task_id]
        if len(rows) != 1:
            raise ValueError("saved diagnostic receipt lacks one task result")
        result = rows[0]
    if result.get("task_id") != task_id or result.get("completed_actions") != result.get(
        "action_count"
    ):
        raise ValueError("saved attempt is incomplete or belongs to another task")
    hashes = result.get("evidence_sha256") or {}
    names = {
        "actual_response": "actual_response.json",
        "network_har": "network.har",
        "trace": "trace.json",
        "acceptance": "acceptance.json",
        "evaluation": "evaluation.json",
    }
    for key, name in names.items():
        path = task_dir / name
        if path.is_symlink() or file_sha256(path) != hashes.get(key):
            raise ValueError(f"saved {key} evidence changed")
    actual = json.loads((task_dir / "actual_response.json").read_text(encoding="utf-8"))
    sites = load_remote_sites(site_map)
    benchmark = WebArenaVerified(
        config=WebArenaVerifiedConfig(
            test_data_file=dataset,
            environments=remote_benchmark(sites).config.environments,
        )
    )
    official = benchmark.get_task(task_id)
    if not official.is_retrieve_task:
        raise ValueError("saved task is not a retrieval task")
    evaluation = benchmark.evaluate_task(
        task_id=task_id, agent_response=actual, network_trace=task_dir / "network.har"
    ).model_dump(mode="json")
    return {
        "schema": "webarena_saved_retrieval_rescore/1",
        "task_id": task_id,
        "browser_calls": 0,
        "source_result_sha256": file_sha256(result_path),
        "source_evaluation_sha256": hashes["evaluation"],
        "dataset_sha256": file_sha256(dataset),
        "site_map_sha256": file_sha256(site_map),
        "score": evaluation["score"],
        "evaluation": evaluation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("rescore output exists; source evidence is append-only")
    value = rescore(
        task_dir=args.task_dir,
        task_id=args.task_id,
        dataset=args.dataset,
        site_map=args.site_map,
    )
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task_id": args.task_id, "score": value["score"], "browser_calls": 0}))


if __name__ == "__main__":
    main()
