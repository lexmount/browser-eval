"""Audit an ordered WebArena catalog sweep and only its failed-case repairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.catalog_contracts import (
    ACCEPTANCE_SCHEMA,
    EXECUTION_CONTRACT_RUN_SCHEMAS,
    RUN_SCHEMAS,
    SCOPE_SCHEMAS,
    file_sha256,
    validate_passing_evaluation,
    verify_physical_evidence,
)
from harness.trajectory_replay.remote_runtime import remote_benchmark
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_runtime_source import verify_webarena_runtime_source


def _read_run(directory: Path) -> tuple[dict, list[dict]]:
    binding_path = directory / "inputs.json"
    progress_path = directory / "progress.jsonl"
    if any(path.is_symlink() or not path.is_file() for path in (binding_path, progress_path)):
        raise ValueError(f"run binding or progress is missing: {directory}")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    order = binding.get("execution_order")
    if (
        binding.get("schema") not in RUN_SCHEMAS
        or not isinstance(order, list)
        or [row.get("task_id") for row in rows] != order[: len(rows)]
        or binding.get("run_count_per_task") != 1
    ):
        raise ValueError(f"run order or protocol changed: {directory}")
    if binding["schema"] in EXECUTION_CONTRACT_RUN_SCHEMAS:
        required = {
            "engine",
            "backend",
            "action_timeout_ms",
            "environment_profile_sha256",
            "corrections_sha256",
            "evaluator_fixes_sha256",
        }
        if not required <= set(binding):
            raise ValueError(f"run execution contract is incomplete: {directory}")
    return binding, rows


def _verified_attempt(
    directory: Path,
    row: dict,
    *,
    expected_data_checksum: str,
    benchmark: object,
    corrections: object,
) -> tuple[dict, bool]:
    task_id = row["task_id"]
    physical = "action_count" in row
    if physical:
        evaluation_path = verify_physical_evidence(directory, row)
    elif row.get("status") != "candidate_setup_failure" or not row.get("error"):
        raise ValueError(f"task {task_id} has neither physical evidence nor setup error")
    else:
        evaluation_path = None
    if row.get("status") == "pass":
        if evaluation_path is None:
            raise ValueError(f"task {task_id} pass lacks evaluation evidence")
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        validate_passing_evaluation(
            evaluation,
            task_id=task_id,
            expected_data_checksum=expected_data_checksum,
            benchmark=benchmark,
            corrections=corrections,
        )
        if any(item.get("status") != "pass" for item in evaluation.get("replay_assertions") or []):
            raise ValueError(f"task {task_id} pass disagrees with replay assertions")
    return (
        {
            "task_id": task_id,
            "status": row.get("status"),
            "score": row.get("score"),
            "progress_sha256": file_sha256(directory / "progress.jsonl"),
            "evidence_sha256": row.get("evidence_sha256"),
        },
        physical,
    )


def audit(
    scope_path: Path,
    segment_dirs: list[Path],
    repair_dirs: list[Path],
    *,
    excluded_duplicate_repair_tasks: set[int] | None = None,
    expected_backend: str = "browsertool",
) -> dict:
    verify_webarena_runtime_source()
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    ids = scope.get("task_ids")
    data_checksum = str(scope.get("official_dataset_sha256") or "")
    if (
        scope.get("schema") not in SCOPE_SCHEMAS
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(task_id, int) for task_id in ids)
        or len(set(ids)) != len(ids)
        or scope.get("task_count") != len(ids)
        or not data_checksum
    ):
        raise ValueError("the declared WebArena task scope changed")
    if not segment_dirs:
        raise ValueError("acceptance segments are required")
    states = {task_id: {"task_id": task_id, "attempts": []} for task_id in ids}
    excluded_requested = set(excluded_duplicate_repair_tasks or ())
    excluded_seen: set[int] = set()
    excluded_attempts = []
    ordered_ids: list[int] = []
    bindings = []
    schemas = set()
    sweep_contract = None
    participant_contract = None
    preparation_sha256s = set()
    physical_attempts = 0
    benchmark = remote_benchmark({site: f"http://{site.value}.invalid" for site in WebArenaSite})
    corrections = load_corrections()
    for kind, directories in (("segment", segment_dirs), ("repair", repair_dirs)):
        for directory in directories:
            binding, rows = _read_run(directory)
            schemas.add(binding["schema"])
            backend = binding.get("backend")
            engine = binding.get("engine", "chromium")
            if backend != expected_backend:
                raise ValueError("acceptance run used a different execution backend")
            participant = (
                engine,
                backend,
                binding.get("binary_sha256"),
                binding.get("site_map_sha256"),
            )
            if participant_contract is None:
                participant_contract = participant
            elif participant != participant_contract:
                raise ValueError("acceptance runs mixed participant or deployment identities")
            segment_contract = (
                binding.get("catalog_sha256"),
                binding.get("portable_suite_sha256"),
                binding.get("site_reset_receipt_sha256"),
                binding.get("runner_revision"),
                binding.get("cowagent_revision"),
                binding.get("action_timeout_ms"),
                binding.get("corrections_sha256"),
                binding.get("evaluator_fixes_sha256"),
            )
            if kind == "segment":
                if sweep_contract is None:
                    sweep_contract = segment_contract
                elif segment_contract != sweep_contract:
                    raise ValueError("ordered sweep changed its execution contract")
            preparation_sha256s.add(binding["site_reset_receipt_sha256"])
            run_record = {
                "kind": kind,
                "inputs_sha256": file_sha256(directory / "inputs.json"),
                "progress_sha256": file_sha256(directory / "progress.jsonl"),
                "catalog_sha256": binding["catalog_sha256"],
                "site_reset_receipt_sha256": binding["site_reset_receipt_sha256"],
                "site_map_sha256": binding.get("site_map_sha256"),
                "runner_revision": binding["runner_revision"],
                "task_ids": [row["task_id"] for row in rows],
            }
            if binding["schema"] in EXECUTION_CONTRACT_RUN_SCHEMAS:
                run_record.update(
                    {
                        "engine": engine,
                        "backend": backend,
                        "environment_profile_sha256": binding["environment_profile_sha256"],
                        "action_timeout_ms": binding["action_timeout_ms"],
                        "corrections_sha256": binding["corrections_sha256"],
                        "evaluator_fixes_sha256": binding["evaluator_fixes_sha256"],
                    }
                )
            bindings.append(run_record)
            for row in rows:
                task_id = row["task_id"]
                if kind == "segment":
                    ordered_ids.append(task_id)
                if kind == "repair" and task_id in excluded_requested:
                    if (
                        not states[task_id]["attempts"]
                        or states[task_id]["attempts"][-1]["status"] != "pass"
                    ):
                        raise ValueError(f"task {task_id} is not a duplicate pass")
                    if task_id in excluded_seen:
                        raise ValueError(f"task {task_id} was excluded more than once")
                    excluded_seen.add(task_id)
                    attempt, physical = _verified_attempt(
                        directory,
                        row,
                        expected_data_checksum=data_checksum,
                        benchmark=benchmark,
                        corrections=corrections,
                    )
                    physical_attempts += int(physical)
                    excluded_attempts.append(
                        {
                            "reason": "duplicate_execution_after_prior_pass",
                            "run_inputs_sha256": file_sha256(directory / "inputs.json"),
                            **attempt,
                        }
                    )
                    continue
                if kind == "repair" and (
                    not states[task_id]["attempts"]
                    or states[task_id]["attempts"][-1]["status"] == "pass"
                ):
                    raise ValueError(f"task {task_id} repair does not follow a failure")
                if (
                    states[task_id]["attempts"]
                    and states[task_id]["attempts"][-1]["status"] == "pass"
                ):
                    raise ValueError(f"task {task_id} repeated after passing")
                attempt, physical = _verified_attempt(
                    directory,
                    row,
                    expected_data_checksum=data_checksum,
                    benchmark=benchmark,
                    corrections=corrections,
                )
                physical_attempts += int(physical)
                states[task_id]["attempts"].append(attempt)
    if excluded_seen != excluded_requested:
        raise ValueError("declared duplicate repair exclusions were not found")
    if ordered_ids != ids:
        raise ValueError("acceptance sweep is incomplete or changed official order")
    rows = [
        {
            "task_id": task_id,
            "status": states[task_id]["attempts"][-1]["status"],
            "attempts": states[task_id]["attempts"],
        }
        for task_id in ids
    ]
    result = {
        "schema": ACCEPTANCE_SCHEMA,
        "scope_sha256": file_sha256(scope_path),
        "site_reset_receipt_sha256": sweep_contract[2],
        "site_preparation_receipt_sha256s": sorted(preparation_sha256s),
        "binary_sha256": participant_contract[2],
        "runs": bindings,
        "excluded_attempts": excluded_attempts,
        "counts": {
            "total": len(ids),
            "pass": sum(row["status"] == "pass" for row in rows),
            "unresolved": sum(row["status"] != "pass" for row in rows),
            "physical_attempts": physical_attempts,
            "excluded_duplicate_attempts": len(excluded_attempts),
        },
        "rows": rows,
    }
    if schemas and schemas <= EXECUTION_CONTRACT_RUN_SCHEMAS:
        result["participant"] = {
            "engine": participant_contract[0],
            "backend": participant_contract[1],
            "binary_sha256": participant_contract[2],
            "site_map_sha256": participant_contract[3],
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        type=Path,
        default=Path("evaluation/trajectory-replay/webarena-verified-full/scope.json"),
    )
    parser.add_argument("--segment", type=Path, action="append", required=True)
    parser.add_argument("--repair", type=Path, action="append", default=[])
    parser.add_argument("--exclude-duplicate-repair-task", type=int, action="append", default=[])
    parser.add_argument(
        "--expected-backend", choices=("browsertool", "playwright"), default="browsertool"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-all-pass", action="store_true")
    args = parser.parse_args()
    result = audit(
        args.scope,
        args.segment,
        args.repair,
        excluded_duplicate_repair_tasks=set(args.exclude_duplicate_repair_task),
        expected_backend=args.expected_backend,
    )
    if args.require_all_pass and result["counts"]["unresolved"]:
        raise ValueError("the acceptance sweep has unresolved cases")
    if args.output.exists():
        raise ValueError("acceptance ledger output already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
