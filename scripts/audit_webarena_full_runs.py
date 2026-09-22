"""Verify one effective Chromium outcome per official WebArena-Verified case."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from harness.trajectory_replay.remote_execution import _verify_result_evidence

LEGACY_EVIDENCE_FILES = frozenset(
    {
        "actions.json",
        "evaluation.json",
        "failure.png",
        "final.png",
        "network.har",
        "outputs.json",
        "response.json",
        "trace.json",
    }
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_physical_evidence(directory: Path, row: dict) -> Path:
    task_id = row["task_id"]
    evidence = row.get("evidence_sha256") or {}
    task_dir = directory / f"task-{task_id}"
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise ValueError(f"task {task_id} evidence directory is missing")
    if "trace.json" in evidence:
        if not set(evidence) <= LEGACY_EVIDENCE_FILES:
            raise ValueError(f"task {task_id} has unknown legacy evidence files")
        for name, expected in evidence.items():
            path = task_dir / name
            if path.is_symlink() or not path.is_file() or _hash(path) != expected:
                raise ValueError(f"task {task_id} evidence hash changed: {name}")
        required = {"evaluation.json", "network.har", "response.json", "trace.json"}
        evaluation_path = task_dir / "evaluation.json"
    else:
        _verify_result_evidence(directory, [row])
        required = {"evaluation", "network_har", "actual_response", "trace"}
        evaluation_path = task_dir / "evaluation.json"
    if row["status"] == "pass" and not required <= evidence.keys():
        raise ValueError(f"task {task_id} pass lacks official evaluation")
    return evaluation_path


def build_ledger(
    scope_path: Path,
    initial_queue_path: Path,
    inherited_results_path: Path,
    sweep_dirs: list[Path],
) -> dict:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    queue = json.loads(initial_queue_path.read_text(encoding="utf-8"))
    inherited = json.loads(inherited_results_path.read_text(encoding="utf-8"))
    ids = scope["task_ids"]
    if (
        scope.get("schema") != "lex_webarena_verified_full_scope/1"
        or ids != list(range(len(ids)))
        or queue.get("scope_sha256") != _hash(scope_path)
        or queue.get("inherited_results_sha256") != _hash(inherited_results_path)
        or [row["task_id"] for row in queue["rows"]] != ids
    ):
        raise ValueError("full-split scope or inherited result binding changed")
    sample = {
        row["task_id"]: row
        for row in inherited["attempts"]
        if row["engine"] == "chromium" and row["status"] == "pass"
    }
    if len(sample) != sum(row["status"] == "pass" for row in queue["rows"]):
        raise ValueError("inherited Chromium pass population changed")
    states = []
    for row in queue["rows"]:
        task_id = row["task_id"]
        passed = row["status"] == "pass"
        if passed:
            source = sample[task_id]
            evaluation = source["evidence"]["evaluation"]
            if evaluation["sha256"] != row["evaluation_sha256"]:
                raise ValueError(f"task {task_id} inherited evaluation changed")
            path = Path(evaluation["path"])
            if path.is_symlink() or not path.is_file() or _hash(path) != evaluation["sha256"]:
                raise ValueError(f"task {task_id} inherited evaluation evidence changed")
        states.append(
            {
                "task_id": task_id,
                "status": "pass" if passed else row["status"],
                "pass_source": "independent_90_case_split" if passed else None,
                "physical_attempts": [],
            }
        )
    for sweep_index, directory in enumerate(sweep_dirs, 1):
        progress_path = directory / "progress.jsonl"
        rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
        if [row["task_id"] for row in rows] != ids:
            raise ValueError(f"ordered sweep {sweep_index} is incomplete or reordered")
        for row in rows:
            state = states[row["task_id"]]
            if "action_count" not in row:
                continue
            if state["status"] == "pass":
                raise ValueError(f"task {row['task_id']} repeated after its first pass")
            evidence = row.get("evidence_sha256") or {}
            if "trace" not in evidence and "trace.json" not in evidence:
                raise ValueError(f"task {row['task_id']} lacks physical replay evidence")
            evaluation_path = _verify_physical_evidence(directory, row)
            if row["status"] == "pass":
                evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
                if float(evaluation.get("score") or 0) != 1 or any(
                    assertion.get("status") != "pass"
                    for assertion in evaluation.get("replay_assertions") or []
                ):
                    raise ValueError(f"task {row['task_id']} pass disagrees with evaluator")
                state["pass_source"] = f"sweep_{sweep_index}"
            state["status"] = row["status"]
            state["physical_attempts"].append(
                {
                    "sweep": sweep_index,
                    "status": row["status"],
                    "score": row.get("score"),
                    "duration_ms": row.get("duration_ms"),
                    "evidence_sha256": row["evidence_sha256"],
                }
            )
    return {
        "schema": "lex_webarena_verified_full_ledger/1",
        "scope_sha256": _hash(scope_path),
        "initial_queue_sha256": _hash(initial_queue_path),
        "inherited_results_sha256": _hash(inherited_results_path),
        "sweep_progress_sha256": [
            _hash(directory / "progress.jsonl") for directory in sweep_dirs
        ],
        "counts": {
            "total": len(ids),
            "pass": sum(row["status"] == "pass" for row in states),
            "unresolved": sum(row["status"] != "pass" for row in states),
            "physical_attempts": sum(len(row["physical_attempts"]) for row in states),
        },
        "rows": states,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--initial-queue", type=Path, required=True)
    parser.add_argument("--inherited-results", type=Path, required=True)
    parser.add_argument("--sweep", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-all-pass", action="store_true")
    args = parser.parse_args()
    result = build_ledger(
        args.scope, args.initial_queue, args.inherited_results, args.sweep
    )
    if args.require_all_pass and result["counts"]["unresolved"]:
        raise ValueError("full Chromium split still has unresolved cases")
    if args.output.exists():
        raise ValueError("ledger output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
