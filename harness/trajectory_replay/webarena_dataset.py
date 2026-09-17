"""Promote replay-proven Browser Use trajectories into the shared dataset."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Any

from webarena_verified.core.utils.checksum import compute_evaluator_checksum

from harness.trajectory_replay.retrieval_acceptance import (
    digest,
    require_current_retrieval,
    verify_attempt,
)
from harness.trajectory_replay.webarena_plan import (
    load_replay_plan,
    replay_calls_binding_sha256,
    replay_site_hosts,
)

SCHEMA = "lex_webarena_verified_trajectory_dataset/1"
RECEIPT_SCHEMA = "lex_webarena_trajectory_replay_suite/1"
OBSERVATION_PROTOCOL = "lex_replay_valid_snapshot_observations/1"


def _validate_observations(trace: list[dict[str, Any]]) -> None:
    """Task acceptance is not proof that every required observation succeeded."""
    for row in trace:
        if row.get("observation_status") == "error":
            raise ValueError("qualification requires valid observations, not observation errors")
        action = (row.get("arguments") or {}).get("action")
        text = row.get("result")
        if action not in {"snapshot", "navigate"}:
            continue
        if not isinstance(text, str) or not text.strip():
            raise ValueError("qualification requires a materialized snapshot observation")
        snapshot_text = text
        if action == "navigate":
            _, separator, snapshot_text = text.partition("--- Page Snapshot ---\n")
            if not separator:
                raise ValueError("qualification requires the navigation's snapshot observation")
        if not snapshot_text.strip() or snapshot_text.startswith("[Snapshot error:"):
            raise ValueError(
                "qualification cannot promote a successful wrapper around snapshot error"
            )


@lru_cache(maxsize=1)
def _official_evaluator_names() -> dict[int, tuple[str, ...]]:
    """Return evaluator sequences after applying registered replay corrections."""

    dataset = json.loads(
        files("webarena_verified")
        .joinpath("assets/dataset/webarena-verified.json")
        .read_text(encoding="utf-8")
    )
    names = {
        int(task["task_id"]): tuple(str(item["evaluator"]) for item in task["eval"])
        for task in dataset
    }
    for task_id, binding in _effective_correction_bindings().items():
        names[task_id] = binding[3]
    return names


@lru_cache(maxsize=1)
def _effective_correction_bindings() -> dict[int, tuple[str, str, str, tuple[str, ...]]]:
    from harness.browser_use.runner import _configure_benchmark  # noqa: PLC0415
    from harness.trajectory_replay.webarena_corrections import (  # noqa: PLC0415
        apply_correction,
        load_corrections,
    )

    benchmark = _configure_benchmark()
    catalog = load_corrections()
    bindings = {}
    for task_id in catalog.records:
        contract = apply_correction(benchmark.get_task(task_id), catalog)
        evaluators = tuple(
            str(item["evaluator"]) for item in contract.task.model_dump(mode="json")["eval"]
        )
        bindings[task_id] = (
            contract.correction_id,
            contract.correction_source_sha256,
            contract.corrected_task_sha256,
            evaluators,
        )
    return bindings


def _validate_evaluation_provenance(
    evaluation: dict[str, Any], attempt: dict[str, Any], expected_data_checksum: str
) -> None:
    """One rule identity gate shared by qualification, resume and aggregation."""
    task_id = int(attempt["task_id"])
    correction = _effective_correction_bindings().get(task_id)
    actual_correction = tuple(
        str(attempt.get(key) or "")
        for key in (
            "contract_correction_id",
            "contract_correction_source_sha256",
            "corrected_task_sha256",
        )
    )
    expected_correction = correction[:3] if correction else ("", "", "")
    if actual_correction != expected_correction:
        raise ValueError("evaluation task correction binding is stale or incomplete")
    evaluated_correction = evaluation.get("replay_contract_correction")
    if correction is None:
        if evaluated_correction is not None:
            raise ValueError("unexpected evaluated task correction")
    elif (
        not isinstance(evaluated_correction, dict)
        or tuple(
            str(evaluated_correction.get(key) or "")
            for key in ("correction_id", "source_sha256", "corrected_task_sha256")
        )
        != expected_correction
    ):
        raise ValueError("evaluation evidence task correction is stale or incomplete")
    if (
        evaluation.get("task_id") != task_id
        or evaluation.get("webarena_verified_version") != version("webarena-verified")
        or evaluation.get("webarena_verified_evaluator_checksum") != compute_evaluator_checksum()
        or not expected_data_checksum
        or evaluation.get("webarena_verified_data_checksum") != expected_data_checksum
        or tuple(row.get("evaluator_name") for row in evaluation.get("evaluators_results", []))
        != _official_evaluator_names().get(task_id)
    ):
        raise ValueError(
            "evaluation rule or official task data provenance differs from current inputs"
        )


def _validate_qualification_receipt(
    receipt: dict[str, Any],
    *,
    receipt_path: Path,
    seed_task_ids: set[int],
    required_engines: tuple[str, ...],
    expected_resources: dict[str, Any],
) -> None:
    def required_int(value: Any, field: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"replay receipt has invalid {field}: {receipt_path}") from exc

    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(f"unsupported replay receipt: {receipt_path}")
    if (
        receipt.get("execution_scope") not in {"qualification", "qualification_shard"}
        or receipt.get("status") != "complete"
    ):
        raise ValueError(f"replay receipt is not a completed qualification proof: {receipt_path}")
    if not receipt.get("source_seed_manifest_sha256") or not receipt.get("profile_sha256"):
        raise ValueError(f"replay receipt is missing source bindings: {receipt_path}")
    runtime = receipt.get("runtime") or {}
    if runtime.get("resources") != expected_resources:
        raise ValueError(f"replay receipt has different runtime resources: {receipt_path}")
    if not runtime.get("browser_eval_commit") or not runtime.get("cowagent_commit"):
        raise ValueError(f"replay receipt is missing code identity: {receipt_path}")
    browser_images = runtime.get("browser_images") or {}
    if set(browser_images) != set(required_engines) or any(
        not (browser_images.get(engine) or {}).get("id") for engine in required_engines
    ):
        raise ValueError(f"replay receipt is missing browser image identity: {receipt_path}")
    if not runtime.get("site_images") or any(
        not image.get("id") for image in runtime["site_images"].values()
    ):
        raise ValueError(f"replay receipt is missing site image identity: {receipt_path}")
    receipt_task_ids = [
        required_int(task_id, "task_id") for task_id in receipt.get("task_ids") or []
    ]
    if (
        not receipt_task_ids
        or len(receipt_task_ids) != len(set(receipt_task_ids))
        or not set(receipt_task_ids) <= seed_task_ids
    ):
        raise ValueError(f"replay receipt has an invalid qualification task scope: {receipt_path}")
    isolation = receipt.get("isolation") or {}
    required_isolation = {
        "environment_pool_count": 1,
        "method_parallelism": 1,
        "task_parallelism": 1,
        "reset_before_every_task": True,
        "teardown_between_methods": True,
    }
    if any(isolation.get(key) != value for key, value in required_isolation.items()):
        raise ValueError(f"replay receipt does not prove the required isolation: {receipt_path}")
    engines = [str(engine) for engine in receipt.get("engines") or []]
    if len(engines) != len(set(engines)) or set(engines) != set(required_engines):
        raise ValueError(f"replay receipt does not cover the required methods: {receipt_path}")
    method_teardown = receipt.get("method_teardown") or {}
    if set(method_teardown) != set(required_engines) or not all(method_teardown.values()):
        raise ValueError(f"replay receipt does not prove completed method teardown: {receipt_path}")
    sessions = receipt.get("sessions") or []
    session_ids = [str(session.get("id") or "") for session in sessions]
    if (
        not sessions
        or any(not session_id for session_id in session_ids)
        or any(not session.get("site_access_host") for session in sessions)
        or len(session_ids) != len(set(session_ids))
    ):
        raise ValueError(f"replay receipt has invalid browser sessions: {receipt_path}")
    sessions_by_id = {str(session["id"]): session for session in sessions}
    attempt_keys = []
    for attempt in receipt.get("attempts") or []:
        if attempt.get("schema") != "lex_webarena_trajectory_replay_attempt/1":
            raise ValueError(f"replay receipt has an invalid attempt schema: {receipt_path}")
        attempt_task_id = required_int(attempt.get("task_id"), "attempt task_id")
        session = sessions_by_id.get(str(attempt.get("browser_session_id") or ""))
        if (
            session is None
            or session.get("engine") != attempt.get("engine")
            or attempt_task_id not in session.get("task_ids", [])
        ):
            raise ValueError(f"replay attempt is not bound to a browser session: {receipt_path}")
        attempt_keys.append((str(attempt.get("engine")), attempt_task_id))
    expected_keys = {
        (engine, task_id) for engine in required_engines for task_id in receipt_task_ids
    }
    if len(attempt_keys) != len(set(attempt_keys)) or set(attempt_keys) != expected_keys:
        raise ValueError(
            f"replay receipt does not contain the complete method-task matrix: {receipt_path}"
        )
    physical_executions = receipt.get("physical_executions") or []
    physical_keys = []
    for item in physical_executions:
        physical_keys.append(
            (
                str(item.get("engine")),
                required_int(item.get("task_id"), "physical task_id"),
                required_int(item.get("physical_execution"), "physical execution number"),
            )
        )
    if (
        not physical_executions
        or any(item.get("status") != "complete" for item in physical_executions)
        or len(physical_keys) != len(set(physical_keys))
    ):
        raise ValueError(f"replay receipt has invalid physical execution history: {receipt_path}")
    physical_by_attempt: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in physical_executions:
        key = (str(item["engine"]), int(item["task_id"]))
        if key not in expected_keys:
            raise ValueError(
                f"replay receipt has a physical execution outside scope: {receipt_path}"
            )
        physical_by_attempt[key].append(item)
    attempts_by_key = {
        (str(item["engine"]), int(item["task_id"])): item for item in receipt.get("attempts") or []
    }
    if set(physical_by_attempt) != expected_keys:
        raise ValueError(f"replay receipt has unbound physical executions: {receipt_path}")
    for key, attempt in attempts_by_key.items():
        physical = physical_by_attempt[key]
        if len(physical) != int(attempt.get("physical_execution_count") or 1) or max(
            int(item["physical_execution"]) for item in physical
        ) != int(attempt.get("physical_execution") or 0):
            raise ValueError(f"replay receipt has inconsistent physical executions: {receipt_path}")


def _validate_passing_attempt_evidence(
    attempt: dict[str, Any],
    candidate: dict[str, Any],
    runtime_site_host: str,
    expected_data_checksum: str,
    trusted_source_site_hosts: set[str],
    retrieval_contract: dict[str, Any] | None = None,
    state_contract: Any = None,
) -> None:
    if attempt.get("process_status") != "pass" or attempt.get("evaluator_status") != "pass":
        return
    evidence = attempt.get("evidence") or {}
    paths = {}
    for kind in ("trace", "network_har", "evaluation"):
        item = evidence.get(kind) or {}
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            raise ValueError(
                f"passing replay attempt has missing or changed {kind} evidence: "
                f"{attempt.get('engine')}/task-{attempt.get('task_id')}"
            )
        paths[kind] = path

    if attempt.get("source_tool_calls_sha256") != _json_sha256(candidate["tool_calls"]):
        raise ValueError("passing replay attempt is not bound to the canonical tool calls")
    source_site_hosts = replay_site_hosts(candidate["tool_calls"])
    if (
        not source_site_hosts <= trusted_source_site_hosts
        or set(attempt.get("source_site_hosts") or []) != source_site_hosts
        or attempt.get("runtime_site_host") != runtime_site_host
        or attempt.get("replay_calls_binding_sha256")
        != replay_calls_binding_sha256(
            candidate["tool_calls"], allowed_hosts=trusted_source_site_hosts
        )
    ):
        raise ValueError("passing replay attempt differs from the canonical replay actions")
    if str(candidate["recorded_response"].get("task_type", "")).upper() == "RETRIEVE":
        if retrieval_contract is None:
            raise ValueError("retrieval qualification requires current-run answer binding")
        verify_attempt(
            attempt,
            retrieval_contract,
            candidate["tool_calls"],
            expected_data_checksum=expected_data_checksum,
        )
    elif attempt.get("evaluation_input_sha256") != _json_sha256(candidate["recorded_response"]):
        raise ValueError("passing replay attempt is not bound to the canonical evaluator input")

    trace = _read_json(paths["trace"])
    trace_arguments = [item.get("arguments") for item in trace] if isinstance(trace, list) else []
    if (
        len(trace_arguments) != int(candidate["action_count"])
        or any(item.get("status") != "success" for item in trace)
        or attempt.get("executed_tool_calls_sha256") != _json_sha256(trace_arguments)
        or attempt.get("replay_calls_binding_sha256")
        != replay_calls_binding_sha256(trace_arguments, allowed_hosts={runtime_site_host})
    ):
        raise ValueError("passing replay trace does not prove every executed action")
    _validate_observations(trace)

    network_har = _read_json(paths["network_har"])
    if not isinstance(network_har, dict) or not isinstance(
        (network_har.get("log") or {}).get("entries"), list
    ):
        raise ValueError("passing replay attempt has an invalid network HAR")

    if state_contract is not None:
        from harness.webarena_state import verify_attempt as verify_state_attempt

        if attempt.get("contract_correction_id"):
            raise ValueError("observed state cannot also apply a legacy replay correction")
        verify_state_attempt(attempt, state_contract)
        return
    evaluation = _read_json(paths["evaluation"])
    task_id = int(attempt["task_id"])
    expected_evaluators = _official_evaluator_names().get(task_id)
    correction = _effective_correction_bindings().get(task_id)
    actual_correction = (
        str(attempt.get("contract_correction_id") or ""),
        str(attempt.get("contract_correction_source_sha256") or ""),
        str(attempt.get("corrected_task_sha256") or ""),
    )
    if correction is None:
        if any(actual_correction):
            raise ValueError(f"task {task_id} has an unexpected replay correction binding")
    elif actual_correction != correction[:3]:
        raise ValueError(f"task {task_id} replay correction binding is stale or incomplete")
    if (
        int(evaluation.get("task_id") or -1) != int(attempt["task_id"])
        or float(evaluation.get("score") or 0) != 1.0
        or str(evaluation.get("status") or "").lower() not in {"pass", "success"}
        or evaluation.get("webarena_verified_version") != version("webarena-verified")
        or evaluation.get("webarena_verified_evaluator_checksum") != compute_evaluator_checksum()
        or not expected_data_checksum
        or evaluation.get("webarena_verified_data_checksum") != expected_data_checksum
    ):
        raise ValueError("passing replay evaluation evidence does not prove a passing score")
    evaluator_rows = evaluation.get("evaluators_results") or []
    if (
        expected_evaluators is None
        or tuple(str(row.get("evaluator_name") or "") for row in evaluator_rows)
        != expected_evaluators
        or any(
            float(row.get("score") or 0) != 1.0
            or str(row.get("status") or "").lower() not in {"pass", "success"}
            for row in evaluator_rows
        )
    ):
        actual_evaluators = tuple(str(row.get("evaluator_name") or "") for row in evaluator_rows)
        raise ValueError(
            "passing replay evaluator evidence does not match the canonical input: "
            f"task {attempt['task_id']}, expected evaluators {expected_evaluators}, "
            f"actual evaluators {actual_evaluators}"
        )
    if any(item.get("status") != "pass" for item in evaluation.get("replay_assertions") or []):
        raise ValueError("passing replay evaluation contains a failed replay assertion")
    _validate_evaluation_provenance(evaluation, attempt, expected_data_checksum)


def _has_stale_correction_binding(attempt: dict[str, Any], task_id: int) -> bool:
    """Return whether a historical proof predates the task's current correction."""

    correction = _effective_correction_bindings().get(task_id)
    actual = (
        str(attempt.get("contract_correction_id") or ""),
        str(attempt.get("contract_correction_source_sha256") or ""),
        str(attempt.get("corrected_task_sha256") or ""),
    )
    return correction is not None and actual != correction[:3]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _candidate_source(candidate: dict[str, Any]) -> dict[str, Any]:
    source = candidate.get("source") or {}
    if candidate.get("candidate_kind") == "expert_repair":
        return {
            "kind": "expert_repair_spec",
            "candidate_id": candidate["id"],
            "repair_spec_sha256": source["repair_spec_sha256"],
            "details": source["details"],
            "repair_basis": candidate["repair_basis"],
        }
    method = candidate["method"]
    return {
        "kind": "authoritative_successful_browser_use_execution",
        "candidate_id": candidate["id"],
        "model": method["model"],
        "browser": method["engine"],
        "execution": candidate["source_execution"],
        "events_sha256": source["events_sha256"],
        "response_sha256": source["response_sha256"],
        "evaluation_sha256": source["evaluation_sha256"],
    }


def build_verified_trajectory_dataset(
    seed_manifest_path: str | Path,
    replay_receipt_paths: list[str | Path],
    *,
    profile_path: str | Path,
) -> dict[str, Any]:
    """Return trajectories qualified before the measured browser comparison."""

    seed_path = Path(seed_manifest_path).expanduser().resolve()
    seed_sha256 = _sha256(seed_path)
    seeds = _read_json(seed_path)
    if seeds.get("schema") != "lex_browser_use_replay_seeds/2":
        raise ValueError("unsupported replay seed manifest")
    seed_rows = seeds.get("tasks") or []
    seed_tasks = {int(item["task_id"]): item for item in seed_rows}
    if len(seed_tasks) != len(seed_rows):
        raise ValueError("replay seed manifest contains duplicate task IDs")
    seed_task_ids = set(seed_tasks)
    plan = load_replay_plan(profile_path, seed_path)
    require_current_retrieval(plan)
    profile = plan["profile"]
    qualification_engine = str(plan["qualification_engine"])
    required_engines = (qualification_engine,)
    profile_sha256 = plan["profile_sha256"]

    attempts_by_key: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    receipt_runs_by_key: dict[tuple[int, str], set[str]] = defaultdict(set)
    receipts: list[dict[str, str]] = []
    for raw_path in replay_receipt_paths:
        receipt_path = Path(raw_path).expanduser().resolve()
        receipt = _read_json(receipt_path)
        _validate_qualification_receipt(
            receipt,
            receipt_path=receipt_path,
            seed_task_ids=seed_task_ids,
            required_engines=required_engines,
            expected_resources=profile["resources"],
        )
        receipts.append(
            {
                "sha256": _sha256(receipt_path),
                "source_seed_manifest_sha256": str(
                    receipt.get("source_seed_manifest_sha256") or ""
                ),
                "profile_sha256": str(receipt.get("profile_sha256") or ""),
            }
        )
        from harness.webarena_state import validate_attempt_identities

        validate_attempt_identities(
            receipt.get("attempts") or [], set(plan.get("state_contracts", {}))
        )
        for attempt in receipt.get("attempts") or []:
            task_id = int(attempt["task_id"])
            if (
                task_id in plan.get("retrieval_task_ids", [])
                or task_id in plan.get("state_contracts", {})
            ) and attempt.get("run_id") != receipt.get("run_id"):
                raise ValueError("retrieval evidence belongs to another qualification run")
            candidate_id = str(attempt["source_candidate_id"])
            if candidate_id != seed_tasks[task_id].get("canonical_candidate_id"):
                if receipt.get("source_seed_manifest_sha256") == seed_sha256:
                    raise ValueError(f"receipt uses a non-canonical trajectory for task {task_id}")
                continue
            candidate = next(
                (
                    item
                    for item in seed_tasks[task_id].get("candidates") or []
                    if item.get("id") == candidate_id
                ),
                None,
            )
            if candidate is None:
                if receipt.get("source_seed_manifest_sha256") == seed_sha256:
                    raise ValueError(
                        f"receipt references candidate missing from seeds: {candidate_id}"
                    )
                continue
            if (
                task_id not in plan.get("state_contracts", {})
                and receipt.get("source_seed_manifest_sha256") != seed_sha256
                and _has_stale_correction_binding(attempt, task_id)
            ):
                continue
            session = {str(item["id"]): item for item in receipt.get("sessions") or []}[
                str(attempt["browser_session_id"])
            ]
            _validate_passing_attempt_evidence(
                attempt,
                candidate,
                str(session.get("site_access_host") or ""),
                str((seeds.get("source") or {}).get("official_data_checksum") or ""),
                plan["trusted_source_site_hosts"],
                plan.get("retrieval_contracts", {}).get(task_id),
                plan.get("state_contracts", {}).get(task_id),
            )
            if attempt.get("process_status") != "pass" or attempt.get("evaluator_status") != "pass":
                continue
            key = (task_id, candidate_id)
            attempts_by_key[key].append(attempt)
            receipt_runs_by_key[key].add(str(receipt.get("run_id") or ""))

    trajectories: list[dict[str, Any]] = []
    for (task_id, candidate_id), attempts in sorted(attempts_by_key.items()):
        task = seed_tasks.get(task_id)
        if task is None:
            raise ValueError(f"receipt references task missing from seeds: {task_id}")
        candidate = next(
            (item for item in task.get("candidates") or [] if item.get("id") == candidate_id),
            None,
        )
        if candidate is None:
            raise ValueError(f"receipt references candidate missing from seeds: {candidate_id}")
        by_engine: dict[str, dict[str, Any]] = {}
        for item in attempts:
            engine = str(item["engine"])
            if engine in by_engine:
                raise ValueError(f"duplicate replay proof for {candidate_id}/{engine}")
            by_engine[engine] = item
        if set(by_engine) != set(required_engines):
            continue
        if any(
            item.get("process_status") != "pass"
            or item.get("evaluator_status") != "pass"
            or int(item.get("completed_action_count") or 0) != int(candidate["action_count"])
            for item in by_engine.values()
        ):
            continue
        correction_proofs = {
            (
                str(item["contract_correction_id"]),
                str(item.get("contract_correction_source_sha256") or ""),
                str(item.get("corrected_task_sha256") or ""),
            )
            for item in by_engine.values()
            if item.get("contract_correction_id")
        }
        if correction_proofs and (
            sum(bool(item.get("contract_correction_id")) for item in by_engine.values())
            != len(required_engines)
            or len(correction_proofs) > 1
            or any(not value for value in next(iter(correction_proofs)))
        ):
            raise ValueError(f"inconsistent replay contracts for {candidate_id}")
        correction_proof = next(iter(correction_proofs), None)
        trajectories.append(
            {
                "id": f"webarena-verified-{task_id}-trajectory-1",
                "task_id": task_id,
                "task": {
                    key: task[key]
                    for key in (
                        "intent",
                        "intent_template_id",
                        "revision",
                        "sites",
                        "start_urls",
                    )
                    if key in task
                },
                "tool_calls": candidate["tool_calls"],
                "evaluation_input": candidate["recorded_response"],
                "source": _candidate_source(candidate),
                "verification": {
                    "state_contract_sha256": plan["state_contracts"][task_id].definition_sha256
                    if task_id in plan.get("state_contracts", {})
                    else None,
                    "retrieval_contract_sha256": (
                        digest(plan["retrieval_contracts"][task_id])
                        if task_id in plan.get("retrieval_task_ids", [])
                        else None
                    ),
                    "retrieval_protocol": (
                        plan["retrieval_contracts"][task_id]["schema"]
                        if task_id in plan.get("retrieval_task_ids", [])
                        else None
                    ),
                    "browsers": list(required_engines),
                    "process_status": "pass",
                    "evaluator_status": "pass",
                    "contract": (
                        {
                            "kind": "observed_form_state_contract",
                            "contract_sha256": plan["state_contracts"][task_id].definition_sha256,
                        }
                        if task_id in plan.get("state_contracts", {})
                        else {
                            "kind": "corrected_replay_contract",
                            "correction_id": correction_proof[0],
                            "correction_source_sha256": correction_proof[1],
                            "corrected_task_sha256": correction_proof[2],
                        }
                        if correction_proof
                        else {"kind": "official_task_contract"}
                    ),
                    "model_calls": 0,
                    "receipt_run_ids": sorted(receipt_runs_by_key[(task_id, candidate_id)] - {""}),
                },
            }
        )

    return {
        "schema": SCHEMA,
        "benchmark": seeds["source"].get("benchmark") or "WebArena-Verified",
        "benchmark_version": seeds["source"].get("benchmark_version") or None,
        "official_data_checksum": seeds["source"].get("official_data_checksum") or None,
        "selection_sha256": seeds["source"]["selection_sha256"],
        "semantics": {
            "trajectory_is_one_verified_feasible_path": True,
            "trajectory_is_the_only_correct_path": False,
            "browser_use_scoring_authority": "official task outcome contract",
            "browser_use_agent_visibility": "hidden",
            "trajectory_replay_role": "fixed BrowserTool action input",
            "evaluation_input_role": (
                "frozen historical response only; RETRIEVE evaluator input "
                "comes from current-run observations"
            ),
        },
        "promotion_policy": {
            "qualification_browser": qualification_engine,
            "measured_browsers_are_not_promotion_gates": True,
            "process_status": "pass",
            "evaluator_status": "pass",
            "model_calls": 0,
        },
        "provenance": {
            **(
                {
                    "retrieval_protocol": next(
                        iter(
                            {
                                contract["schema"]
                                for contract in plan["retrieval_contracts"].values()
                            }
                        )
                    )
                }
                if len({contract["schema"] for contract in plan["retrieval_contracts"].values()})
                == 1
                else {}
            ),
            "retrieval_protocols": {
                str(task_id): contract["schema"]
                for task_id, contract in sorted(plan["retrieval_contracts"].items())
            },
            "observation_protocol": OBSERVATION_PROTOCOL,
            "seed_manifest_sha256": seed_sha256,
            "profile_sha256": profile_sha256,
            "replay_receipts": receipts,
        },
        "counts": {
            "verified_tasks": len({item["task_id"] for item in trajectories}),
            "verified_trajectories": len(trajectories),
        },
        "trajectories": trajectories,
    }
