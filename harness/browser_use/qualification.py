"""Pre-register and adjudicate reference trajectories before browser comparison."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from .har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION
from .suite import TRAJECTORY_SCHEMA, TaskSuite

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _json_sha256(value: Any) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()


def task_contract_sha256(task: dict[str, Any]) -> str:
    """Bind qualification evidence to the exact executable task contract."""
    return _json_sha256(
        {
            "id": task.get("id"),
            "title": task.get("title"),
            "trajectory_actions": (task.get("trajectory") or {}).get("actions"),
            "evaluator": task.get("evaluator"),
            "environment": task.get("environment"),
            "response_builder": (task.get("ground_truth") or {}).get("response_builder"),
        }
    )


def _validated_attempt(
    adjudication: dict[str, Any], task_id: str, *, expected_contract_sha256: str
) -> str:
    path_value = str(adjudication.get("validated_attempt_path") or "").strip()
    if not path_value:
        raise ValueError(f"{task_id}: repaired eligibility requires validated_attempt_path")
    path = Path(path_value)
    if not path.is_file():
        raise ValueError(f"{task_id}: validated attempt evidence is missing")
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    declared = str(adjudication.get("validated_run_sha256") or "").strip()
    if declared and (not _SHA256_RE.fullmatch(declared) or declared != sha256):
        raise ValueError(f"{task_id}: validated attempt sha256 mismatch")
    row = json.loads(raw)
    if (
        str(row.get("task_id")) != task_id
        or row.get("engine") != "chromium"
        or row.get("is_retry") is not False
        or row.get("qualification_run") is not True
        or row.get("status") != "pass"
        or row.get("combined_status") != "pass"
        or row.get("evidence_status") != "pass"
    ):
        raise ValueError(f"{task_id}: validated attempt is not a passing primary qualification run")
    if row.get("task_contract_sha256") != expected_contract_sha256:
        raise ValueError(f"{task_id}: validated attempt does not match corrected task contract")
    evidence_directory = Path(str(row.get("evidence_directory") or "")).resolve()
    if evidence_directory != path.parent.resolve():
        raise ValueError(f"{task_id}: validated attempt evidence directory mismatch")
    har_path = evidence_directory / "network.har"
    if not har_path.is_file():
        raise ValueError(f"{task_id}: validated attempt network HAR is missing")
    har = json.loads(har_path.read_text(encoding="utf-8"))
    expected_creator = {"name": HAR_CREATOR_NAME, "version": HAR_CREATOR_VERSION}
    if (har.get("log") or {}).get("creator") != expected_creator:
        raise ValueError(f"{task_id}: validated attempt requires HAR creator {expected_creator}")
    final_evidence = row.get("final_page_evidence") or {}
    if final_evidence.get("status") != "complete":
        raise ValueError(f"{task_id}: validated attempt final-page evidence is incomplete")
    for key, expected_name in (("screenshot", "final.png"), ("page", "final-page.json")):
        metadata = final_evidence.get(key) or {}
        if metadata.get("path") != expected_name:
            raise ValueError(f"{task_id}: validated attempt {key} evidence path is invalid")
        evidence_path = evidence_directory / expected_name
        if not evidence_path.is_file():
            raise ValueError(f"{task_id}: validated attempt {key} evidence is missing")
        expected_sha256 = str(metadata.get("sha256") or "")
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise ValueError(f"{task_id}: validated attempt {key} sha256 is invalid")
        if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError(f"{task_id}: validated attempt {key} sha256 mismatch")
    return sha256


def build_repair_queue(
    suite: TaskSuite, attempts_path: str | Path | Iterable[str | Path]
) -> dict[str, Any]:
    paths = (
        [Path(attempts_path)]
        if isinstance(attempts_path, (str, Path))
        else [Path(path) for path in attempts_path]
    )
    if not paths:
        raise ValueError("qualification evidence paths are required")
    rows = [
        json.loads(line)
        for path in paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if (
            row.get("engine") != "chromium"
            or row.get("is_retry") is not False
            or row.get("qualification_run") is not True
        ):
            raise ValueError("qualification evidence must contain one primary Chromium attempt")
        by_task.setdefault(str(row["task_id"]), []).append(row)
    expected = {task.task_id for task in suite.tasks}
    if set(by_task) != expected or any(len(items) != 1 for items in by_task.values()):
        raise ValueError("qualification evidence must cover every task exactly once")

    tasks: list[dict[str, Any]] = []
    for task in suite.tasks:
        row = by_task[task.task_id][0]
        contract = task_contract_sha256(
            {
                "id": task.task_id,
                "title": task.title,
                "trajectory": task.trajectory,
                "evaluator": task.evaluator,
                "environment": task.environment,
                "ground_truth": task.ground_truth,
            }
        )
        if row.get("task_contract_sha256") != contract:
            raise ValueError(f"{task.task_id}: qualification attempt does not match task contract")
        if not task.tool_calls:
            disposition = "needs_trace_repair"
        elif (
            row.get("combined_status") == "pass"
            and row.get("status") == "pass"
            and row.get("evidence_status") == "pass"
        ):
            disposition = "eligible"
        elif row.get("combined_status") == "pass":
            disposition = "needs_evidence_revalidation"
        elif row.get("process_status") == "pass" and not task.ground_truth.get("response_builder"):
            disposition = "needs_response_annotation"
        else:
            disposition = "needs_trace_repair"
        failed_step = next(
            (item for item in row.get("trace") or [] if item.get("status") == "fail"),
            None,
        )
        adjudication = None
        if disposition == "eligible":
            evidence_directory = str(row.get("evidence_directory") or "").strip()
            if not evidence_directory:
                raise ValueError(f"{task.task_id}: passing qualification lacks evidence_directory")
            adjudication = {
                "status": "eligible",
                "validated_attempt_path": str(Path(evidence_directory) / "attempt.json"),
            }
        tasks.append(
            {
                "task_id": task.task_id,
                "disposition": disposition,
                "process_status": row.get("process_status"),
                "result_status": row.get("result_status"),
                "failed_step": failed_step,
                "evidence_directory": row.get("evidence_directory"),
                "adjudication": adjudication,
            }
        )
    return {
        "schema": "lex_browser_use_qualification/1",
        "attempts_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths],
        "policy": {
            "qualification_browser": "upstream Chromium",
            "task_and_evaluator_are_immutable": True,
            "browser_comparison_results_must_not_be_used": True,
            "allowed_final_statuses": ["eligible", "skipped"],
        },
        "tasks": tasks,
    }


def _apply_material_change(task: dict[str, Any], adjudication: dict[str, Any]) -> bool:
    material_change = (
        adjudication.get("corrected_trajectory") is not None
        or adjudication.get("response_builder") is not None
    )
    corrected = adjudication.get("corrected_trajectory")
    if corrected is not None:
        if (
            not isinstance(corrected, dict)
            or corrected.get("schema") != TRAJECTORY_SCHEMA
            or not isinstance(corrected.get("actions"), list)
            or not corrected["actions"]
        ):
            raise ValueError(f"{task['id']}: corrected trajectory is invalid")
        original_sha256 = _json_sha256(task["trajectory"])
        corrected = deepcopy(corrected)
        provenance = dict(corrected.get("provenance") or {})
        provenance.update(
            {
                "kind": "codex_corrected_webarena_trajectory",
                "original_trajectory_sha256": original_sha256,
            }
        )
        corrected["provenance"] = provenance
        task["trajectory"] = corrected
        task["ground_truth"]["expected_actions"] = [
            action.get("action") for action in corrected["actions"]
        ]
    response_builder = adjudication.get("response_builder")
    if response_builder is not None:
        if not isinstance(response_builder, dict) or response_builder.get("kind") not in {
            "observed_shape",
            "observed_json",
            "observed_json_concat",
            "observed_currency_range",
            "observed_not_found",
        }:
            raise ValueError(f"{task['id']}: response builder must use observed outputs")
        task["ground_truth"]["response_builder"] = deepcopy(response_builder)
    return material_change


def build_repair_validation_suite(
    suite_payload: dict[str, Any], qualification: dict[str, Any]
) -> dict[str, Any]:
    """Materialize proposed corrections for their mandatory Chromium revalidation run."""
    result = deepcopy(suite_payload)
    tasks = result.get("tasks") or []
    decisions = {str(item["task_id"]): item for item in qualification.get("tasks") or []}
    if set(decisions) != {str(task["id"]) for task in tasks}:
        raise ValueError("qualification task set does not match suite")
    selected: list[dict[str, Any]] = []
    for task in tasks:
        decision = decisions[str(task["id"])]
        adjudication = decision.get("adjudication")
        if not isinstance(adjudication, dict) or adjudication.get("status") != "eligible":
            continue
        disposition = decision.get("disposition")
        if disposition == "eligible":
            continue
        if disposition == "needs_trace_repair" and adjudication.get("corrected_trajectory") is None:
            raise ValueError(f"{task['id']}: trace repair requires corrected_trajectory")
        if (
            disposition == "needs_response_annotation"
            and adjudication.get("response_builder") is None
        ):
            raise ValueError(f"{task['id']}: response annotation requires response_builder")
        if not str(adjudication.get("reason") or "").strip():
            raise ValueError(f"{task['id']}: repaired eligibility requires reason")
        material_change = _apply_material_change(task, adjudication)
        if disposition != "needs_evidence_revalidation" and not material_change:
            raise ValueError(f"{task['id']}: repaired validation requires a material change")
        task["ground_truth"]["qualification"] = {"status": "pending"}
        selected.append(task)
    if not selected:
        raise ValueError("qualification contains no proposed repairs to validate")
    result["tasks"] = selected
    benchmark = dict(result.get("benchmark") or {})
    benchmark["repair_validation"] = {
        "qualification_schema": qualification.get("schema"),
        "task_count": len(selected),
        "browser": "chromium",
        "attempts_per_task": 1,
    }
    result["benchmark"] = benchmark
    return result


def build_evidence_revalidation_suite(
    suite_payload: dict[str, Any], qualification: dict[str, Any]
) -> dict[str, Any]:
    """Re-run every provisionally eligible task under the current evidence contract."""
    result = deepcopy(suite_payload)
    tasks = result.get("tasks") or []
    decisions = {str(item["task_id"]): item for item in qualification.get("tasks") or []}
    if set(decisions) != {str(task["id"]) for task in tasks}:
        raise ValueError("qualification task set does not match suite")
    selected: list[dict[str, Any]] = []
    for task in tasks:
        decision = decisions[str(task["id"])]
        adjudication = decision.get("adjudication")
        if not isinstance(adjudication, dict) or adjudication.get("status") != "eligible":
            continue
        _apply_material_change(task, adjudication)
        if not (task.get("trajectory") or {}).get("actions"):
            raise ValueError(f"{task['id']}: evidence revalidation requires a trajectory")
        task["ground_truth"]["qualification"] = {"status": "pending"}
        selected.append(task)
    if not selected:
        raise ValueError("qualification contains no eligible tasks to revalidate")
    result["tasks"] = selected
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "evidence_revalidation": {
            "task_count": len(selected),
            "browser": "chromium",
            "attempts_per_task": 1,
            "purpose": "bind HAR v2, final screenshot, and final visible page state",
        },
    }
    return result


def apply_adjudication(
    suite_payload: dict[str, Any], qualification: dict[str, Any]
) -> dict[str, Any]:
    decisions = {str(item["task_id"]): item for item in qualification.get("tasks") or []}
    tasks = suite_payload.get("tasks") or []
    if set(decisions) != {str(task["id"]) for task in tasks}:
        raise ValueError("qualification task set does not match suite")
    for task in tasks:
        decision = decisions[str(task["id"])]
        adjudication = decision.get("adjudication")
        if not isinstance(adjudication, dict):
            raise ValueError(f"{task['id']}: adjudication is missing")
        status = adjudication.get("status")
        if status not in {"eligible", "skipped"}:
            raise ValueError(f"{task['id']}: adjudication status must be eligible or skipped")
        if status == "skipped" and not str(adjudication.get("reason") or "").strip():
            raise ValueError(f"{task['id']}: skipped adjudication requires reason")
        public_adjudication = {
            key: value
            for key, value in adjudication.items()
            if key not in {"validated_attempt_path", "corrected_trajectory", "response_builder"}
        }
        material_change = (
            adjudication.get("corrected_trajectory") is not None
            or adjudication.get("response_builder") is not None
        )
        if status == "skipped" and material_change:
            raise ValueError(f"{task['id']}: skipped task cannot contain corrections")
        if (
            status == "eligible"
            and isinstance(task.get("trajectory"), dict)
            and not task["trajectory"].get("actions")
            and not (adjudication.get("corrected_trajectory") or {}).get("actions")
        ):
            raise ValueError(f"{task['id']}: eligible task requires a non-empty trajectory")
        requires_validation = status == "eligible"
        if (
            requires_validation
            and (decision.get("disposition") != "eligible" or material_change)
            and not str(adjudication.get("reason") or "").strip()
        ):
            raise ValueError(f"{task['id']}: repaired eligibility requires reason")
        _apply_material_change(task, adjudication)
        if adjudication.get("corrected_trajectory") is not None:
            public_adjudication["corrected_trajectory_sha256"] = _json_sha256(task["trajectory"])
        if requires_validation:
            public_adjudication["validated_run_sha256"] = _validated_attempt(
                adjudication,
                str(task["id"]),
                expected_contract_sha256=task_contract_sha256(task),
            )
        task["ground_truth"]["qualification"] = public_adjudication
    suite_payload["benchmark"]["qualification"] = {
        "schema": qualification.get("schema"),
        "qualified_count": sum(
            item["adjudication"]["status"] == "eligible" for item in decisions.values()
        ),
        "skipped_count": sum(
            item["adjudication"]["status"] == "skipped" for item in decisions.values()
        ),
    }
    return suite_payload
