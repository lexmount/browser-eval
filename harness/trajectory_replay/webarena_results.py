"""Build one current logical replay matrix from formal runs and correction shards."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .remote_runtime import (
    COHORT_RESET_SCOPE,
    checked_file_sha256,
    input_digest,
    selected_task_ids,
    validate_remote_cohort_reset_receipt,
)
from .webarena_plan import load_replay_plan
from .webarena_replay import RECEIPT_SCHEMA, validate_qualification_dataset

EFFECTIVE_SCHEMA = "lex_webarena_trajectory_replay_effective_suite/1"


def _read(path: str | Path) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).expanduser().resolve()
    return resolved, json.loads(resolved.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attempt_map(receipt: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    attempts = {
        (str(row["engine"]), int(row["task_id"])): row for row in receipt.get("attempts") or []
    }
    expected = {
        (str(engine), int(task_id))
        for engine in receipt.get("engines") or []
        for task_id in receipt.get("task_ids") or []
    }
    if len(attempts) != len(receipt.get("attempts") or []) or set(attempts) != expected:
        raise ValueError("replay receipt does not contain its complete unique result matrix")
    return attempts


def _validate_receipt(
    receipt: dict[str, Any],
    *,
    scope: str,
    engines: list[str],
    retrieval_task_ids: set[int] | None = None,
) -> dict[tuple[str, int], dict[str, Any]]:
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("execution_scope") != scope
        or receipt.get("status") != "complete"
    ):
        raise ValueError(f"expected a completed {scope} replay receipt")
    if list(receipt.get("engines") or []) != engines:
        raise ValueError("replay receipt browser registry differs from the current profile")
    if not all((receipt.get("method_teardown") or {}).get(engine) for engine in engines):
        raise ValueError("replay receipt does not prove method teardown")
    for attempt in receipt.get("attempts", []):
        if attempt.get("status") == "pass" and not (
            attempt.get("process_status") == attempt.get("evaluator_status") == "pass"
        ):
            raise ValueError("claimed pass contradicts process or evaluator status")
        if attempt.get("status") == "unsupported":
            raise ValueError("unsupported acceptance cannot enter a formal cohort")
        if int(attempt["task_id"]) in (retrieval_task_ids or set()) and (
            not receipt.get("run_id") or attempt.get("run_id") != receipt.get("run_id")
        ):
            raise ValueError("retrieval answer belongs to another cohort run")
    return _attempt_map(receipt)


def build_effective_results(
    *,
    profile_path: str | Path,
    seed_manifest_path: str | Path,
    qualified_dataset_path: str | Path,
    base_receipt_path: str | Path,
    correction_receipt_paths: list[str | Path],
) -> dict[str, Any]:
    """Overlay declared correction shards onto one complete formal base matrix."""

    plan = load_replay_plan(profile_path, seed_manifest_path)
    qualified_sha256 = validate_qualification_dataset(plan, qualified_dataset_path)
    engines = list(plan["engines"])
    base_path, base = _read(base_receipt_path)
    retrieval_task_ids = set(plan.get("retrieval_task_ids", [])) | set(
        plan.get("state_contracts", {})
    )
    effective = _validate_receipt(
        base, scope="formal", engines=engines, retrieval_task_ids=retrieval_task_ids
    )
    expected = {(engine, task_id) for engine in engines for task_id in plan["task_ids"]}
    if set(effective) != expected:
        raise ValueError("base formal receipt does not cover the complete current task selection")

    source_receipts = [
        {
            "role": "base",
            "path": str(base_path),
            "sha256": _sha256(base_path),
            "run_id": str(base.get("run_id") or ""),
            "task_ids": list(base.get("task_ids") or []),
        }
    ]
    physical_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in base.get("physical_executions") or []:
        physical_by_key.setdefault((str(row["engine"]), int(row["task_id"])), []).append(row)
    receipts = [base]

    for raw_path in correction_receipt_paths:
        path, receipt = _read(raw_path)
        correction = _validate_receipt(
            receipt,
            scope="formal_correction_shard",
            engines=engines,
            retrieval_task_ids=retrieval_task_ids,
        )
        if (
            receipt.get("profile_sha256") != plan["profile_sha256"]
            or receipt.get("source_seed_manifest_sha256") != plan["seed_manifest_sha256"]
            or receipt.get("qualification_dataset_sha256") != qualified_sha256
        ):
            raise ValueError("formal correction shard is not bound to the current replay inputs")
        effective.update(correction)
        for key in correction:
            physical_by_key[key] = []
        for row in receipt.get("physical_executions") or []:
            physical_by_key[(str(row["engine"]), int(row["task_id"]))].append(row)
        source_receipts.append(
            {
                "role": "correction",
                "path": str(path),
                "sha256": _sha256(path),
                "run_id": str(receipt.get("run_id") or ""),
                "task_ids": list(receipt.get("task_ids") or []),
            }
        )
        receipts.append(receipt)

    if set(effective) != expected or set(physical_by_key) != expected:
        raise ValueError("effective replay matrix is incomplete")
    candidates = {
        int(task["task_id"]): str(task["canonical_candidate_id"])
        for task in plan["seed_manifest"]["tasks"]
    }
    stale = [
        key
        for key, row in effective.items()
        if str(row.get("source_candidate_id") or "") != candidates[key[1]]
    ]
    if stale:
        raise ValueError(f"effective replay matrix contains stale trajectories: {stale}")

    from .retrieval_acceptance import verify_attempt

    seed_candidates = {
        int(task["task_id"]): next(
            item for item in task["candidates"] if item["id"] == task["canonical_candidate_id"]
        )
        for task in plan["seed_manifest"]["tasks"]
        if int(task["task_id"]) in plan.get("retrieval_task_ids", [])
    }
    from harness.webarena_state import validate_attempt_identities

    validate_attempt_identities(list(effective.values()), set(plan.get("state_contracts", {})))
    for (_, task_id), attempt in effective.items():
        if task_id in plan.get("state_contracts", {}):
            from harness.webarena_state import verify_attempt as verify_state_attempt

            if attempt.get("state_contract_sha256") != plan["state_contracts"][
                task_id
            ].definition_sha256 or attempt.get("contract_correction_id"):
                raise ValueError("legacy result cannot enter observed-state cohort")
            if attempt.get("process_status") == "pass":
                verify_state_attempt(attempt, plan["state_contracts"][task_id])
        if task_id in plan.get("retrieval_task_ids", []):
            contract = plan["retrieval_contracts"][task_id]
            if attempt.get("retrieval_protocol") != contract["schema"]:
                raise ValueError("legacy retrieval result cannot enter a current-run cohort")
            if attempt.get("process_status") == "pass":
                verify_attempt(
                    attempt,
                    contract,
                    seed_candidates[task_id]["tool_calls"],
                    expected_data_checksum=str(
                        plan["seed_manifest"].get("source", {}).get("official_data_checksum") or ""
                    ),
                )

    bindings = json.dumps(source_receipts, sort_keys=True, separators=(",", ":"))
    return {
        "schema": EFFECTIVE_SCHEMA,
        "run_id": f"effective-{hashlib.sha256(bindings.encode()).hexdigest()[:12]}",
        "status": "complete",
        "execution_scope": "formal_effective",
        "profile": plan["profile_path"],
        "profile_sha256": plan["profile_sha256"],
        "source_seed_manifest": plan["seed_manifest_path"],
        "source_seed_manifest_sha256": plan["seed_manifest_sha256"],
        "qualification_dataset_sha256": qualified_sha256,
        "task_ids": list(plan["task_ids"]),
        "engines": engines,
        "isolation": dict(plan["profile"]["execution"]),
        "method_teardown": {engine: True for engine in engines},
        "source_receipts": source_receipts,
        "runtime_receipts": [receipt.get("runtime") or {} for receipt in receipts],
        "attempts": [effective[key] for key in sorted(effective)],
        "physical_executions": [
            row for key in sorted(physical_by_key) for row in physical_by_key[key]
        ],
        "counts": {
            "logical_results": len(effective),
            "physical_executions": sum(len(rows) for rows in physical_by_key.values()),
            "passes": sum(row.get("status") == "pass" for row in effective.values()),
            "failures": sum(row.get("status") != "pass" for row in effective.values()),
        },
    }


EVIDENCE_FILES = {
    "trace": "trace.json",
    "readiness": "readiness.json",
    "network_har": "network.har",
    "actual_response": "actual_response.json",
    "acceptance": "acceptance.json",
    "evaluation": "evaluation.json",
    "failure_screenshot": "failure.png",
}


def _read_cohort_input(path: Path) -> dict[str, Any]:
    checked_file_sha256(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _reference_validity(
    path: Path, task_ids: list[int], dataset_sha256: str
) -> dict[int, dict[str, Any]]:
    audit = _read_cohort_input(path)
    rows = audit.get("tasks")
    if (
        audit.get("schema") != "webarena_reference_validity/1"
        or audit.get("dataset_sha256") != dataset_sha256
        or not isinstance(rows, list)
        or len(rows) != len(task_ids)
        or any(not isinstance(r, dict) or type(r.get("task_id")) is not int for r in rows)
        or sorted(r["task_id"] for r in rows) != task_ids
    ):
        raise ValueError("reference-validity audit is not bound to the exact selected dataset")
    result = {}
    for row in rows:
        task_id = row["task_id"]
        source = Path(row.get("source_path") or "")
        if (
            row.get("status") not in {"valid", "unverifiable"}
            or not isinstance(row.get("reason"), str)
            or not row["reason"].strip()
            or not isinstance(row.get("source_sha256"), str)
            or checked_file_sha256(source) != row["source_sha256"]
        ):
            raise ValueError(f"task {task_id}: reference validity lacks current source proof")
        source_record = _read_cohort_input(source)
        if source_record.get("task_id") != task_id and task_id not in source_record.get(
            "task_ids", []
        ):
            raise ValueError(f"task {task_id}: source proof does not identify this task")
        result[task_id] = {
            "status": row["status"],
            "reason": row["reason"],
            "source_sha256": row["source_sha256"],
        }
    return result


def _verified_method_cohort(
    *,
    method: dict[str, Any],
    manifest_path: Path,
    receipt_path: Path,
    dataset_sha256: str,
    dataset: dict[int, dict[str, Any]],
    task_ids: list[int],
    site_map_sha256: str | None,
    selection_sha256: str,
) -> tuple[list[dict[str, Any]], str]:
    manifest = _read_cohort_input(manifest_path)
    receipt = _read_cohort_input(receipt_path)
    method_id = method["id"]
    binary_sha256 = manifest.get("browser_binary_sha256")
    if (
        manifest.get("schema") != "webarena_three_method_inputs/1"
        or receipt.get("schema") != "webarena_three_method_cohort/1"
        or manifest.get("method_id") != method_id
        or receipt.get("method_id") != method_id
        or manifest.get("engine") != method.get("engine")
        or receipt.get("engine") != method.get("engine")
        or manifest.get("dataset_sha256") != dataset_sha256
        or manifest.get("selection_sha256") != selection_sha256
        or receipt.get("dataset_sha256") != dataset_sha256
        or receipt.get("manifest_sha256") != checked_file_sha256(manifest_path)
        or not isinstance(binary_sha256, str)
        or len(binary_sha256) != 64
        or receipt.get("browser_binary_sha256") != binary_sha256
        or (method.get("binary_sha256") is not None and method["binary_sha256"] != binary_sha256)
        or manifest.get("site_map_sha256") != receipt.get("site_map_sha256")
        or (site_map_sha256 is not None and receipt.get("site_map_sha256") != site_map_sha256)
        or manifest.get("promotable") is not True
        or receipt.get("promotable") is not True
        or receipt.get("certified") is not True
        or receipt.get("certification_independent_of_pass_rate") is not True
        or manifest.get("run_count") != 1
        or receipt.get("run_count") != 1
        or manifest.get("retry_count") != 0
        or receipt.get("retry_count") != 0
        or manifest.get("site_reset_between_tasks") is not False
        or receipt.get("site_reset_between_tasks") is not False
        or receipt.get("browser_context_per_task") != "fresh"
        or type(receipt.get("app_id")) is not int
        or receipt["app_id"] < 1
        or receipt.get("reference_sha256_before") != receipt.get("reference_sha256_after")
    ):
        raise ValueError(f"{method_id}: cohort identity/certification changed")
    frozen = receipt["reference_sha256_before"]
    if (
        not isinstance(frozen, dict)
        or frozen.get("manifest") != checked_file_sha256(manifest_path)
        or frozen.get("frozen_trajectories") != dataset_sha256
        or frozen.get("browser_binary") != binary_sha256
        or frozen.get("site_map") != receipt["site_map_sha256"]
        or frozen.get("task_selection") != manifest.get("selection_sha256")
        or frozen.get("cohort_reset_hook") != receipt.get("cohort_reset_hook_sha256")
    ):
        raise ValueError(f"{method_id}: reference freeze is incomplete")
    manifest_rows = manifest.get("tasks")
    result_rows = receipt.get("results")
    if (
        manifest.get("task_ids") != task_ids
        or receipt.get("declared_task_ids") != task_ids
        or receipt.get("executed_task_ids") != task_ids
        or not isinstance(manifest_rows, list)
        or not isinstance(result_rows, list)
        or len(manifest_rows) != len(task_ids)
        or len(result_rows) != len(task_ids)
        or [r.get("task_id") for r in manifest_rows if isinstance(r, dict)] != task_ids
        or [r.get("task_id") for r in result_rows if isinstance(r, dict)] != task_ids
    ):
        raise ValueError(f"{method_id}: expected exactly one execution of each selected task")
    cohort_root = receipt_path.parent
    if cohort_root.is_symlink():
        raise ValueError(f"{method_id}: symlinked cohort root")
    reset_path = cohort_root / "site-reset.json"
    reset_sha256 = checked_file_sha256(reset_path)
    if reset_sha256 != receipt.get("cohort_reset_sha256"):
        raise ValueError(f"{method_id}: cohort reset hash changed")
    reset = validate_remote_cohort_reset_receipt(
        reset_path,
        site_map_sha256=receipt["site_map_sha256"],
        app_id=receipt["app_id"],
        reset_scope=COHORT_RESET_SCOPE,
        site_reset_between_tasks=False,
        required_readiness_probes=manifest.get("site_readiness"),
    )
    if (
        reset.get("method_id") != method_id
        or reset.get("task_id") is not None
        or type(reset.get("completed_at_unix")) not in {int, float}
        or reset["completed_at_unix"] <= 0
    ):
        raise ValueError(f"{method_id}: site reset belongs to another method")
    attempts = []
    for task_id, plan, row in zip(task_ids, manifest_rows, result_rows, strict=True):
        contract_sha256 = input_digest(
            {
                "task": dataset[task_id]["task"],
                "evaluation_input": dataset[task_id]["evaluation_input"],
            }
        )
        actions = plan.get("actions")
        source_actions = plan.get("source_actions")
        evidence_hashes = row.get("evidence_sha256")
        if (
            plan.get("task_contract_sha256") != contract_sha256
            or not isinstance(actions, list)
            or not actions
            or not isinstance(source_actions, list)
            or not source_actions
            or input_digest(source_actions) != plan.get("source_calls_sha256")
            or any(not isinstance(a, dict) or not a.get("action") for a in actions)
            or input_digest(actions) != plan.get("bound_calls_sha256")
            or row.get("executed_actions_sha256") != plan.get("bound_calls_sha256")
            or row.get("action_count") != len(actions)
            or not isinstance(row.get("completed_actions"), int)
            or not 0 <= row["completed_actions"] <= len(actions)
            or row.get("status") not in {"pass", "fail", "unsupported"}
            or not isinstance(evidence_hashes, dict)
            or not isinstance(row.get("error"), str)
        ):
            raise ValueError(f"{method_id}/{task_id}: execution differs from frozen task/actions")
        required = {"trace", "readiness"}
        if not row["error"]:
            required |= {"network_har", "actual_response"}
            if plan.get("retrieval_contract") is not None:
                required.add("acceptance")
            if row.get("score") is not None:
                required.add("evaluation")
        if not required <= set(evidence_hashes) or set(evidence_hashes) - set(EVIDENCE_FILES):
            raise ValueError(f"{method_id}/{task_id}: required evidence absent")
        directory = cohort_root / f"task-{task_id}"
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"{method_id}/{task_id}: evidence directory absent")
        evidence = {}
        for kind, expected in evidence_hashes.items():
            path = directory / EVIDENCE_FILES[kind]
            if checked_file_sha256(path) != expected:
                raise ValueError(f"{method_id}/{task_id}: {kind} hash changed")
            evidence[kind] = {"path": str(path.resolve()), "sha256": expected}
        evidence["site_reset"] = {"path": str(reset_path.resolve()), "sha256": reset_sha256}
        trace = json.loads((directory / "trace.json").read_text(encoding="utf-8"))
        if (
            not isinstance(trace, list)
            or len(trace) != row["completed_actions"]
            and not row["error"]
            or sum(isinstance(step, dict) and step.get("status") == "success" for step in trace)
            != row["completed_actions"]
        ):
            raise ValueError(f"{method_id}/{task_id}: action trace contradicts result row")
        if "evaluation" in evidence:
            evaluated = json.loads((directory / "evaluation.json").read_text(encoding="utf-8"))
            if evaluated.get("score") != row.get("score"):
                raise ValueError(f"{method_id}/{task_id}: evaluator score contradicts result row")
        if row["status"] == "pass" and (
            row["error"]
            or row.get("score") != 1
            or "evaluation" not in evidence
            or row["completed_actions"] != len(actions)
        ):
            raise ValueError(
                f"{method_id}/{task_id}: passing verdict lacks complete execution/evaluation"
            )
        if row["status"] == "fail" and not row["error"] and "evaluation" not in evidence:
            raise ValueError(
                f"{method_id}/{task_id}: failed verdict has no error or evaluator evidence"
            )
        process_status = (
            "fail" if row["error"] or row["completed_actions"] != len(actions) else "pass"
        )
        evaluator_status = "pass" if row["status"] == "pass" else "fail"
        attempts.append(
            {
                "participant_id": method_id,
                "engine": method["engine"],
                "task_id": task_id,
                "task_contract_sha256": contract_sha256,
                "planned_actions": actions,
                "planned_actions_sha256": plan["bound_calls_sha256"],
                "status": row["status"],
                "official_rule_score": row.get("score"),
                "action_count": len(actions),
                "completed_action_count": row["completed_actions"],
                "duration_ms": row["duration_ms"],
                "process_status": process_status,
                "evaluator_status": evaluator_status,
                "error": row["error"],
                "binary_sha256": binary_sha256,
                "cohort_receipt_sha256": checked_file_sha256(receipt_path),
                "evidence": evidence,
            }
        )
    return attempts, receipt["site_map_sha256"]


def aggregate_method_cohorts(
    *,
    profile_path: Path,
    selection_path: Path,
    dataset_path: Path,
    validity_path: Path,
    cohort_inputs: dict[str, tuple[Path, Path]],
) -> dict[str, Any]:
    profile = _read_cohort_input(profile_path)
    dataset = _read_cohort_input(dataset_path)
    task_ids = selected_task_ids(selection_path)
    dataset_sha256 = checked_file_sha256(dataset_path)
    selection_sha256 = checked_file_sha256(selection_path)
    methods = profile.get("methods")
    rows = dataset.get("trajectories")
    if (profile.get("dataset") or {}).get("sha256") != dataset_sha256 or Path(
        (profile.get("dataset") or {}).get("path") or ""
    ).resolve() != dataset_path.resolve():
        raise ValueError("profile is not bound to the corrected dataset")
    if (profile.get("task_selection") or {}).get("sha256") != selection_sha256 or Path(
        (profile.get("task_selection") or {}).get("path") or ""
    ).resolve() != selection_path.resolve():
        raise ValueError("profile task selection changed")
    if (
        not isinstance(methods, list)
        or len(methods) < 2
        or len({m.get("id") for m in methods if isinstance(m, dict)}) != len(methods)
        or set(cohort_inputs) != {m["id"] for m in methods}
        or not isinstance(rows, list)
        or len(rows) != len(task_ids)
        or any(not isinstance(r, dict) or type(r.get("task_id")) is not int for r in rows)
        or sorted(r["task_id"] for r in rows) != task_ids
    ):
        raise ValueError(
            "complete registered methods and the same corrected task selection are required"
        )
    execution = profile.get("execution") or {}
    if any(
        (execution.get(k) is not v)
        for k, v in {
            "method_parallelism": 1,
            "task_parallelism": 1,
            "reset_before_every_task": False,
            "reset_before_every_method": True,
            "teardown_between_methods": True,
            "retry_failed_task": False,
        }.items()
    ):
        raise ValueError("profile does not declare the common three-method protocol")
    dataset_rows = {row["task_id"]: row for row in rows}
    validity = _reference_validity(validity_path, task_ids, dataset_sha256)
    attempts = []
    common_site_map = None
    reset_receipts: set[str] = set()
    for method in methods:
        manifest_path, receipt_path = cohort_inputs[method["id"]]
        cohort_rows, site_map_sha256 = _verified_method_cohort(
            method=method,
            manifest_path=manifest_path,
            receipt_path=receipt_path,
            dataset_sha256=dataset_sha256,
            dataset=dataset_rows,
            task_ids=task_ids,
            site_map_sha256=common_site_map,
            selection_sha256=selection_sha256,
        )
        common_site_map = site_map_sha256
        reset_sha = cohort_rows[0]["evidence"]["site_reset"]["sha256"]
        if reset_sha in reset_receipts:
            raise ValueError("browser methods reused a site reset receipt")
        reset_receipts.add(reset_sha)
        for row in cohort_rows:
            row["reference_validity"] = validity[row["task_id"]]
        attempts.extend(cohort_rows)
    if len(attempts) != len(methods) * len(task_ids):
        raise ValueError("aggregate requires every method-task result")
    return {
        "schema": "webarena_three_method_results/1",
        "dataset_sha256": dataset_sha256,
        "profile_sha256": checked_file_sha256(profile_path),
        "selection_sha256": selection_sha256,
        "reference_validity_sha256": checked_file_sha256(validity_path),
        "site_map_sha256": common_site_map,
        "attempts": attempts,
        "physical_executions": [
            {
                "participant_id": row["participant_id"],
                "engine": row["engine"],
                "task_id": row["task_id"],
                "evidence": {"trace": row["evidence"]["trace"]},
            }
            for row in attempts
        ],
    }


def _method_paths(values: list[str], label: str) -> dict[str, Path]:
    result = {}
    for value in values:
        method_id, separator, path = value.partition("=")
        if not separator or not method_id or not path or method_id in result:
            raise ValueError(f"invalid or duplicate {label}: {value}")
        result[method_id] = Path(path)
    return result


def main_aggregate(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate complete registered remote replay cohorts"
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reference-validity", type=Path, required=True)
    parser.add_argument(
        "--manifest", action="append", required=True,
        help="METHOD_ID=PATH; one per registered method"
    )
    parser.add_argument(
        "--receipt", action="append", required=True,
        help="METHOD_ID=PATH; one per registered method"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifests = _method_paths(args.manifest, "manifest")
    receipts = _method_paths(args.receipt, "receipt")
    if set(manifests) != set(receipts):
        raise ValueError("matching manifest/receipt pairs are required")
    result = aggregate_method_cohorts(
        profile_path=args.profile,
        selection_path=args.selection,
        dataset_path=args.dataset,
        validity_path=args.reference_validity,
        cohort_inputs={key: (manifests[key], receipts[key]) for key in manifests},
    )
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("aggregate output exists; measured evidence is append-only")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"attempts": len(result["attempts"]),
                      "sha256": checked_file_sha256(args.output)}))
