"""Audit an ordered WebArena catalog sweep and only its failed-case repairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from webarena_verified.core.utils.checksum import compute_evaluator_checksum
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.catalog_contracts import (
    ACCEPTANCE_SCHEMA,
    EXECUTION_CONTRACT_RUN_SCHEMAS,
    RUN_SCHEMAS,
    SCOPE_SCHEMAS,
    file_sha256,
    load_action_overrides,
    validate_passing_evaluation,
    verify_physical_evidence,
)
from harness.trajectory_replay.evaluator_transition import (
    load_receipt,
    load_transition,
    reevaluate_pass,
)
from harness.trajectory_replay.remote_runtime import load_remote_sites, remote_benchmark
from harness.trajectory_replay.runtime_identity import (
    assert_observed_runtime,
    request_bridge_identity,
    runtime_identity,
)
from harness.trajectory_replay.webarena_corrections import load_corrections
from harness.trajectory_replay.webarena_runtime_source import (
    WEBARENA_VERIFIED_REVISION,
    verify_webarena_runtime_source,
)


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
        }
        evaluator_identity = (
            "webarena_revision"
            if binding["schema"] == "lex_trajectory_replay_catalog_run/1"
            else "evaluator_fixes_sha256"
        )
        required.add(evaluator_identity)
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
    run_inputs_sha256: str | None = None,
    run_revision: str | None = None,
    evaluator_transition: dict | None = None,
    transition_receipt: dict[tuple[int, str], dict] | None = None,
    runtime_binding: dict[str, object] | None = None,
) -> tuple[dict, bool]:
    task_id = row["task_id"]
    physical = "action_count" in row
    if physical and runtime_binding is not None:
        assert_observed_runtime(
            runtime_binding, row.get("runtime_metadata"), task_id=task_id
        )
    transition_binding = None
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
        current_evaluator = evaluation.get("webarena_verified_evaluator_checksum")
        current_data = evaluation.get("webarena_verified_data_checksum")
        if (
            current_evaluator == compute_evaluator_checksum()
            and current_data == expected_data_checksum
        ):
            if run_revision != WEBARENA_VERIFIED_REVISION:
                raise ValueError(f"task {task_id} current evaluation has a different revision")
            validate_passing_evaluation(
                evaluation,
                task_id=task_id,
                expected_data_checksum=expected_data_checksum,
                benchmark=benchmark,
                corrections=corrections,
            )
        else:
            if (
                evaluator_transition is None
                or transition_receipt is None
                or run_revision not in evaluator_transition["compatible_from_revisions"]
                or run_inputs_sha256 is None
                or current_evaluator
                not in {
                    evaluator_transition["from_evaluator_checksum"],
                    evaluator_transition["to_evaluator_checksum"],
                }
            ):
                raise ValueError(f"task {task_id} pass lacks evaluator transition evidence")
            validate_passing_evaluation(
                evaluation,
                task_id=task_id,
                expected_data_checksum=evaluator_transition["from_data_checksum"],
                benchmark=benchmark,
                corrections=corrections,
                expected_evaluator_checksum=current_evaluator,
            )
            current, transition_binding = reevaluate_pass(
                directory / f"task-{task_id}",
                task_id=task_id,
                expected_data_checksum=expected_data_checksum,
                benchmark=benchmark,
                corrections=corrections,
            )
            del current
            receipt_row = transition_receipt.get(
                (task_id, transition_binding["old_evaluation_sha256"])
            )
            expected_receipt = {
                "run_inputs_sha256": run_inputs_sha256,
                **transition_binding,
            }
            if receipt_row != expected_receipt:
                raise ValueError(f"task {task_id} evaluator transition receipt changed")
        if any(item.get("status") != "pass" for item in evaluation.get("replay_assertions") or []):
            raise ValueError(f"task {task_id} pass disagrees with replay assertions")
    return (
        {
            "task_id": task_id,
            "status": row.get("status"),
            "score": row.get("score"),
            "progress_sha256": file_sha256(directory / "progress.jsonl"),
            "evidence_sha256": row.get("evidence_sha256"),
            **(
                {"runtime_metadata": row["runtime_metadata"]}
                if isinstance(row.get("runtime_metadata"), dict)
                else {}
            ),
            **(
                {"evaluator_transition": transition_binding}
                if transition_binding is not None
                else {}
            ),
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
    action_overrides_paths: list[Path] | None = None,
    site_map_path: Path | None = None,
    evaluator_transition_path: Path | None = None,
    evaluator_transition_receipt_path: Path | None = None,
) -> dict:
    verify_webarena_runtime_source()
    if (evaluator_transition_path is None) != (evaluator_transition_receipt_path is None):
        raise ValueError("evaluator transition authority and receipt must be supplied together")
    evaluator_transition = (
        load_transition(evaluator_transition_path)
        if evaluator_transition_path is not None
        else None
    )
    transition_receipt = (
        load_receipt(evaluator_transition_receipt_path, evaluator_transition)
        if evaluator_transition_receipt_path is not None and evaluator_transition is not None
        else None
    )
    used_transition_rows: set[tuple[int, str]] = set()
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
    binary_sha256s: set[str] = set()
    remote_browser_identities: dict[str, dict[str, object]] = {}
    preparation_sha256s = set()
    physical_attempts = 0
    benchmark = remote_benchmark(
        load_remote_sites(site_map_path)
        if site_map_path is not None
        else {site: f"http://{site.value}.invalid" for site in WebArenaSite}
    )
    corrections = load_corrections()
    override_sources = {}
    for path in action_overrides_paths or []:
        _amendments, digest = load_action_overrides(path)
        if digest in override_sources:
            raise ValueError("duplicate external action overrides source")
        override_sources[digest] = path
    used_override_sources: set[str] = set()
    for kind, directories in (("segment", segment_dirs), ("repair", repair_dirs)):
        for directory in directories:
            binding, rows = _read_run(directory)
            run_revision = binding.get("webarena_revision")
            if "action_overrides_sha256" in binding:
                override_digest = binding["action_overrides_sha256"]
                used_override_sources.add(override_digest)
            schemas.add(binding["schema"])
            backend = binding.get("backend")
            engine = binding.get("engine", "chromium")
            if backend != expected_backend:
                raise ValueError("acceptance run used a different execution backend")
            runtime_identity_key, runtime_binding = runtime_identity(binding)
            request_bridge = request_bridge_identity(binding)
            binary_sha256 = runtime_binding.get("binary_sha256")
            remote_browser = runtime_binding.get("remote_browser")
            participant = (
                engine,
                backend,
                binding.get("site_map_sha256"),
                (
                    json.dumps(remote_browser, sort_keys=True, separators=(",", ":"))
                    if remote_browser is not None
                    else None
                ),
                json.dumps(
                    request_bridge,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            if participant_contract is None:
                participant_contract = participant
            elif participant != participant_contract:
                raise ValueError("acceptance runs mixed engine, backend, or deployment identities")
            if isinstance(binary_sha256, str):
                binary_sha256s.add(binary_sha256)
            elif isinstance(remote_browser, dict):
                remote_browser_identities[runtime_identity_key] = remote_browser
            segment_contract = (
                binding.get("catalog_sha256"),
                binding.get("portable_suite_sha256"),
                binding.get("site_reset_receipt_sha256"),
                runtime_identity_key,
                binding.get("runner_revision"),
                binding.get("cowagent_revision"),
                binding.get("action_timeout_ms"),
                binding.get("corrections_sha256"),
                binding.get("webarena_revision") or binding.get("evaluator_fixes_sha256"),
                binding.get("action_overrides_sha256"),
                request_bridge,
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
                    }
                )
                run_record.update(runtime_binding)
                if request_bridge is not None:
                    run_record["request_bridge"] = request_bridge
                evaluator_identity = (
                    "webarena_revision"
                    if binding["schema"] == "lex_trajectory_replay_catalog_run/1"
                    else "evaluator_fixes_sha256"
                )
                run_record[evaluator_identity] = binding[evaluator_identity]
            if "action_overrides_sha256" in binding:
                run_record["action_overrides_sha256"] = binding["action_overrides_sha256"]
            bindings.append(run_record)
            run_inputs_sha256 = run_record["inputs_sha256"]
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
                        run_inputs_sha256=run_inputs_sha256,
                        run_revision=run_revision,
                        evaluator_transition=evaluator_transition,
                        transition_receipt=transition_receipt,
                        runtime_binding=runtime_binding,
                    )
                    if "evaluator_transition" in attempt:
                        used_transition_rows.add(
                            (
                                task_id,
                                attempt["evaluator_transition"]["old_evaluation_sha256"],
                            )
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
                    run_inputs_sha256=run_inputs_sha256,
                    run_revision=run_revision,
                    evaluator_transition=evaluator_transition,
                    transition_receipt=transition_receipt,
                    runtime_binding=runtime_binding,
                )
                if "evaluator_transition" in attempt:
                    used_transition_rows.add(
                        (
                            task_id,
                            attempt["evaluator_transition"]["old_evaluation_sha256"],
                        )
                    )
                physical_attempts += int(physical)
                states[task_id]["attempts"].append(attempt)
    if excluded_seen != excluded_requested:
        raise ValueError("declared duplicate repair exclusions were not found")
    if set(override_sources) - used_override_sources:
        raise ValueError("external action overrides are not bound to an acceptance run")
    if transition_receipt is not None and used_transition_rows != set(transition_receipt):
        raise ValueError("evaluator transition receipt contains unused or missing pass evidence")
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
        "binary_sha256s": sorted(binary_sha256s),
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
            "site_map_sha256": participant_contract[2],
            **(
                {"binary_sha256": next(iter(binary_sha256s))}
                if len(binary_sha256s) == 1
                else {"binary_sha256s": sorted(binary_sha256s)}
                if binary_sha256s
                else {
                    "remote_browser": next(iter(remote_browser_identities.values()))
                }
            ),
            **(
                {"request_bridge": json.loads(participant_contract[4])}
                if participant_contract[4] != "null"
                else {}
            ),
        }
    if len(binary_sha256s) == 1:
        result["binary_sha256"] = next(iter(binary_sha256s))
    if remote_browser_identities:
        result["remote_browsers"] = list(remote_browser_identities.values())
    if used_override_sources:
        result["action_overrides_sha256s"] = sorted(used_override_sources)
    if evaluator_transition_path is not None and evaluator_transition_receipt_path is not None:
        result["evaluator_transition"] = {
            "authority_sha256": file_sha256(evaluator_transition_path),
            "receipt_sha256": file_sha256(evaluator_transition_receipt_path),
            "reevaluated_passes": len(used_transition_rows),
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
    parser.add_argument("--action-overrides", type=Path, action="append", default=[])
    parser.add_argument("--site-map", type=Path)
    parser.add_argument("--evaluator-transition", type=Path)
    parser.add_argument("--evaluator-transition-receipt", type=Path)
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
        action_overrides_paths=args.action_overrides,
        site_map_path=args.site_map,
        evaluator_transition_path=args.evaluator_transition,
        evaluator_transition_receipt_path=args.evaluator_transition_receipt,
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
