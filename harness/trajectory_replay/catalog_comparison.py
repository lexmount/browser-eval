"""Compile full-catalog replay evidence for the shared comparison report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from harness.record_io import load_jsonl
from harness.trajectory_replay.catalog_contracts import (
    TRUSTED_RECORDED_HOSTS,
    apply_action_overrides,
    catalog_action_overrides,
    file_sha256,
    load_catalog_inputs,
    row_source_sites,
)
from harness.trajectory_replay.remote_runtime import rebind_remote_calls, remote_benchmark
from harness.trajectory_replay.runtime_identity import (
    assert_observed_runtime,
    request_bridge_identity,
    runtime_identities,
    runtime_identity,
)
from harness.trajectory_replay.webarena_corrections import apply_correction, load_corrections
from harness.trajectory_replay.webarena_replay import _json_sha256
from harness.web_fetch.safety import unlinked_path, write_new_file

RESULTS_SCHEMA = "lex_webarena_catalog_comparison_results/1"
DATASET_SCHEMA = "lex_webarena_catalog_comparison_dataset/1"

_EVIDENCE_FILENAMES = {
    "network_har": "network.har",
    "acceptance": "acceptance.json",
    "actual_response": "actual_response.json",
    "evaluation": "evaluation.json",
    "failure_screenshot": "failure.png",
    "trace": "trace.json",
    "readiness": "readiness.json",
    "outputs": "outputs.json",
}


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked comparison input: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact(path: Path, digest: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_file() or file_sha256(path) != digest:
        raise ValueError(f"catalog evidence is missing or changed: {path}")
    return {"path": str(path.resolve()), "sha256": digest}


def build_catalog_comparison_dataset(
    *,
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path,
    action_overrides_path: Path | None = None,
) -> dict[str, Any]:
    """Materialize all catalog tasks under the same contracts used by execution."""

    catalog, sites, portable = load_catalog_inputs(
        catalog_path,
        suite_path,
        site_map_path,
        action_overrides_path=action_overrides_path,
    )
    benchmark = remote_benchmark(sites)
    corrections = load_corrections()
    trajectories = []
    for frozen in catalog["rows"]:
        task_id = int(frozen["task_id"])
        official = benchmark.get_task(task_id)
        effective = (
            apply_correction(official, corrections).task
            if task_id in corrections.records
            else official
        )
        contract = effective.model_dump(mode="json")
        source_actions = (
            list(portable[task_id].tool_calls)
            if frozen["mode"] == "portable"
            else list(frozen["source_actions"])
        )
        source_actions = apply_action_overrides(
            source_actions,
            catalog_action_overrides(catalog, frozen),
            task_id=task_id,
        )
        actions = rebind_remote_calls(
            source_actions,
            sites,
            set(TRUSTED_RECORDED_HOSTS),
            row_source_sites(catalog, frozen),
        )
        if _json_sha256(actions) != frozen["bound_calls_sha256"]:
            raise ValueError(f"task {task_id}: report actions differ from executed catalog")
        task = {
            key: contract[key]
            for key in (
                "intent",
                "intent_template_id",
                "revision",
                "sites",
                "start_urls",
            )
            if key in contract
        }
        trajectories.append(
            {
                "id": f"webarena-verified-full-{task_id}",
                "task_id": task_id,
                "task": task,
                "tool_calls": actions,
                "evaluation_input": contract["eval"][0]["expected"],
                "source": {
                    "kind": frozen.get("source_kind"),
                    "catalog_row_sha256": _json_sha256(frozen),
                },
                "verification": {
                    "contract": {"kind": "official_or_reviewed_correction"},
                    "model_calls": 0,
                },
            }
        )
    return {
        "schema": DATASET_SCHEMA,
        "benchmark": "WebArena-Verified",
        "selection_sha256": file_sha256(catalog_path),
        "counts": {
            "verified_tasks": len(trajectories),
            "verified_trajectories": len(trajectories),
        },
        "trajectories": trajectories,
    }


def _progress_index(evidence_root: Path) -> dict[str, tuple[Path, dict[int, dict[str, Any]]]]:
    index: dict[str, tuple[Path, dict[int, dict[str, Any]]]] = {}
    for progress in evidence_root.rglob("progress.jsonl"):
        if progress.is_symlink() or progress.parent.is_symlink():
            raise ValueError(f"catalog progress is symlinked: {progress}")
        digest = file_sha256(progress)
        rows = load_jsonl(progress, skip_blank=True)
        by_task = {int(row["task_id"]): row for row in rows}
        if len(by_task) != len(rows):
            raise ValueError(f"catalog progress contains duplicate task ids: {progress}")
        if digest in index and index[digest][0] != progress.parent:
            raise ValueError(f"catalog progress digest is ambiguous: {digest}")
        index[digest] = (progress.parent, by_task)
    return index


def _site_reset_index(evidence_root: Path) -> dict[str, dict[str, str]]:
    index = {}
    for path in evidence_root.rglob("*.json"):
        name = path.name.lower()
        if (
            "reset" not in name
            and "receipt" not in name
            or path.is_symlink()
            or path.parent.is_symlink()
        ):
            continue
        digest = file_sha256(path)
        ref = _artifact(path, digest)
        if digest in index and index[digest] != ref:
            raise ValueError(f"site reset receipt digest is ambiguous: {digest}")
        index[digest] = ref
    return index


def _attempt(
    *,
    participant: dict[str, Any],
    task: dict[str, Any],
    summary: dict[str, Any],
    progress_index: dict[str, tuple[Path, dict[int, dict[str, Any]]]],
    site_resets: dict[str, dict[str, str]],
    ledger_runtimes: dict[str, dict[str, object]],
    ledger_request_bridge: dict[str, object] | None,
) -> dict[str, Any] | None:
    if summary.get("status") == "candidate_setup_failure":
        return None
    task_id = int(summary["task_id"])
    progress_sha256 = str(summary["progress_sha256"])
    try:
        run_dir, rows = progress_index[progress_sha256]
        physical = rows[task_id]
    except KeyError as error:
        raise ValueError(f"task {task_id}: bound physical result is missing") from error
    for key in ("status", "score"):
        if physical.get(key) != summary.get(key):
            raise ValueError(f"task {task_id}: acceptance summary differs from physical result")
    inputs_path = run_dir / "inputs.json"
    inputs = _read(inputs_path)
    if inputs.get("engine") != participant["engine"]:
        raise ValueError(f"task {task_id}: physical browser identity changed")
    if request_bridge_identity(inputs) != ledger_request_bridge:
        raise ValueError(f"task {task_id}: physical request bridge identity changed")
    identity_key, physical_runtime = runtime_identity(inputs)
    if ledger_runtimes.get(identity_key) != physical_runtime:
        raise ValueError(f"task {task_id}: physical browser runtime identity changed")
    assert_observed_runtime(
        physical_runtime, physical.get("runtime_metadata"), task_id=task_id
    )
    binary_sha256 = physical_runtime.get("binary_sha256")
    remote_browser = physical_runtime.get("remote_browser")
    actions = task["tool_calls"]
    uses_current_actions = (
        physical.get("executed_actions_sha256") == _json_sha256(actions)
        and physical.get("action_count") == len(actions)
    )
    evidence = {}
    task_dir = run_dir / f"task-{task_id}"
    for name, digest in (physical.get("evidence_sha256") or {}).items():
        filename = _EVIDENCE_FILENAMES.get(name)
        if filename is None:
            raise ValueError(f"task {task_id}: unknown evidence kind {name}")
        evidence[name] = _artifact(task_dir / filename, digest)
    try:
        evidence["site_reset"] = site_resets[inputs["site_reset_receipt_sha256"]]
    except KeyError as error:
        raise ValueError(f"task {task_id}: run site reset receipt is missing") from error
    evaluation_exists = "evaluation" in evidence
    error = str(physical.get("error") or "")
    process_status = "fail" if error else "pass"
    failure_stage = physical.get("failure_stage")
    if error and not failure_stage:
        failure_stage = (
            "browser_session_setup"
            if "login_context" in error
            else "browser_tool_execution"
        )
    evaluator_status = (
        "pass"
        if physical.get("status") == "pass"
        else "fail"
        if evaluation_exists
        else "not_run"
    )
    contract_sha256 = _json_sha256(
        {"task": task["task"], "evaluation_input": task["evaluation_input"]}
    )
    reference_validity = {
        "status": "valid",
        "reason": (
            "The hash-bound official task and evaluator contract are retained independently "
            "of this browser outcome."
        ),
        "source_sha256": contract_sha256,
    }
    return {
        "participant_id": participant["id"],
        "engine": participant["engine"],
        "task_id": task_id,
        "task_contract_sha256": contract_sha256,
        **(
            {
                "planned_actions": actions,
                "planned_actions_sha256": _json_sha256(actions),
            }
            if uses_current_actions
            else {
                "historical_planned_actions_sha256": physical.get(
                    "executed_actions_sha256"
                )
            }
        ),
        "status": physical["status"],
        "official_rule_score": physical.get("score"),
        "action_count": physical["action_count"],
        "completed_action_count": physical["completed_actions"],
        "duration_ms": physical["duration_ms"],
        "process_status": process_status,
        "evaluator_status": evaluator_status,
        "error": error,
        **({"failure_stage": failure_stage} if failure_stage else {}),
        "binary_sha256": binary_sha256,
        **({"remote_browser": remote_browser} if remote_browser is not None else {}),
        **(
            {"request_bridge": inputs["request_bridge"]}
            if inputs.get("request_bridge") is not None
            else {}
        ),
        **(
            {"runtime_metadata": physical["runtime_metadata"]}
            if isinstance(physical.get("runtime_metadata"), dict)
            else {}
        ),
        "cohort_receipt_sha256": progress_sha256,
        "evidence": evidence,
        "reference_validity": reference_validity,
    }


def build_catalog_comparison_results(
    *,
    dataset: dict[str, Any],
    dataset_sha256: str,
    profile_sha256: str,
    scope_sha256: str,
    site_map_sha256: str,
    participants: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one complete logical matrix and retain every valid physical call."""

    tasks = {int(row["task_id"]): row for row in dataset["trajectories"]}
    attempts = []
    physical_executions = []
    seen_participants = set()
    for participant in participants:
        participant_id = participant.get("id")
        if not participant_id or participant_id in seen_participants:
            raise ValueError("catalog comparison participant ids must be unique")
        seen_participants.add(participant_id)
        ledger_path = Path(participant["ledger_path"]).expanduser().resolve()
        evidence_root = Path(participant["evidence_root"]).expanduser().resolve()
        ledger = _read(ledger_path)
        if [int(row["task_id"]) for row in ledger.get("rows") or []] != list(tasks):
            raise ValueError(f"{participant_id}: acceptance ledger task order changed")
        ledger_participant = ledger.get("participant") or {}
        if ledger_participant.get("engine") != participant["engine"]:
            raise ValueError(f"{participant_id}: acceptance ledger browser identity changed")
        ledger_runtimes = runtime_identities(ledger_participant)
        ledger_request_bridge = request_bridge_identity(ledger_participant)
        index = _progress_index(evidence_root)
        site_resets = _site_reset_index(evidence_root)
        if ledger.get("site_reset_receipt_sha256") not in site_resets:
            raise ValueError(f"{participant_id}: initial site reset receipt is missing")
        for accepted in ledger["rows"]:
            task_id = int(accepted["task_id"])
            physical_for_task = []
            for summary in accepted.get("attempts") or []:
                row = _attempt(
                    participant=participant,
                    task=tasks[task_id],
                    summary={"task_id": task_id, **summary},
                    progress_index=index,
                    site_resets=site_resets,
                    ledger_runtimes=ledger_runtimes,
                    ledger_request_bridge=ledger_request_bridge,
                )
                if row is not None:
                    physical_for_task.append(row)
                    physical_executions.append(row)
            if not physical_for_task:
                raise ValueError(f"{participant_id}/{task_id}: no physical outcome")
            final = physical_for_task[-1]
            if "planned_actions" not in final:
                raise ValueError(
                    f"{participant_id}/{task_id}: final result used another catalog action set"
                )
            if final["status"] != accepted["status"]:
                raise ValueError(f"{participant_id}/{task_id}: final logical outcome changed")
            attempts.append(final)
    expected = {(participant["id"], task_id) for participant in participants for task_id in tasks}
    actual = {(row["participant_id"], row["task_id"]) for row in attempts}
    if actual != expected or len(attempts) != len(expected):
        raise ValueError("catalog comparison matrix is incomplete")
    return {
        "schema": RESULTS_SCHEMA,
        "dataset_sha256": dataset_sha256,
        "profile_sha256": profile_sha256,
        "selection_sha256": scope_sha256,
        "reference_validity_sha256": hashlib.sha256(
            "".join(sorted(row["task_contract_sha256"] for row in attempts)).encode()
        ).hexdigest(),
        "site_map_sha256": site_map_sha256,
        "attempts": attempts,
        "physical_executions": physical_executions,
    }


def inputs_main(argv=None) -> None:
    from harness.trajectory_replay.catalog_contracts import file_sha256

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path, required=True)
    parser.add_argument("--results-output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.dataset_output = unlinked_path(args.dataset_output)
    args.results_output = unlinked_path(args.results_output)
    if args.dataset_output == args.results_output:
        raise ValueError("catalog comparison outputs must be distinct")
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
    dataset_bytes = (json.dumps(dataset, ensure_ascii=False, indent=2) + "\n").encode()
    results = build_catalog_comparison_results(
        dataset=dataset,
        dataset_sha256=hashlib.sha256(dataset_bytes).hexdigest(),
        profile_sha256=file_sha256(Path(config["profile_path"])),
        scope_sha256=file_sha256(Path(config["scope_path"])),
        site_map_sha256=file_sha256(Path(config["site_map_path"])),
        participants=config["participants"],
    )
    write_new_file(args.dataset_output, dataset_bytes)
    write_new_file(args.results_output,
                   (json.dumps(results, ensure_ascii=False, indent=2) + "\n").encode())
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
