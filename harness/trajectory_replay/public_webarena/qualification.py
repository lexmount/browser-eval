"""Pre-register and adjudicate reference trajectories before browser comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.record_io import load_jsonl, same_json_record
from harness.trajectory_replay.public_webarena.har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION
from harness.trajectory_replay.public_webarena.suite import (
    TRAJECTORY_SCHEMA,
    TaskSuite,
    load_suite,
    validate_selector,
)
from harness.trajectory_replay.public_webarena.targets import validate_target
from harness.web_fetch.safety import unlinked_path, write_new_file
from lexbench_webfetch.paths import contained_path, safe_identifier

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUALIFICATION_SCHEMA = "lex_browser_use_qualification/2"
_PASS_STATUS_FIELDS = (
    "process_status",
    "result_status",
    "evidence_status",
    "status",
    "combined_status",
)
_LEGACY_URL_PORT_SITES = {
    3000: "map",
    4399: "reddit",
    7770: "shopping",
    7780: "shopping_admin",
    8023: "gitlab",
    8888: "wikipedia",
    9999: "reddit",
}


def _semantic_field_value(field: str, value: str) -> str:
    normalized = " ".join(value.strip().casefold().split())
    if field != "url":
        return normalized
    token = re.match(r"^__([a-z_]+)__(/.*)?$", normalized)
    if token:
        return f"{token.group(1)}:{token.group(2) or '/'}"
    try:
        parsed = urlparse(normalized)
        site = _LEGACY_URL_PORT_SITES.get(parsed.port or -1)
    except ValueError:
        site = None
        parsed = None
    if site is not None and parsed is not None:
        suffix = parsed.path or "/"
        if parsed.query:
            suffix += "?" + parsed.query
        return f"{site}:{suffix}"
    return normalized


def trajectory_semantic_conflicts(task: dict[str, Any]) -> tuple[str, ...]:
    """Return task/trajectory contradictions that must block eligibility.

    Only frozen public task text and action contracts are inspected. Evaluator
    answers and browser-comparison results are deliberately out of scope.
    """
    title = str(task.get("title") or "").casefold()
    actions = (task.get("trajectory") or {}).get("actions") or []
    targets = "\n".join(
        json.dumps(action.get("target"), ensure_ascii=False, sort_keys=True).casefold()
        for action in actions
        if action.get("target") is not None
    )
    conflicts: list[str] = []
    if "new branch" in title and "commit to main branch" in targets:
        conflicts.append("new_branch_required_but_main_branch_selected")
    requested_name = re.search(r'(?:called|named)\s+["“]([^"”]+)["”]', title)
    project_name_fills = [
        str(action.get("text") or "").casefold()
        for action in actions
        if action.get("action") == "fill"
        and "project name"
        in json.dumps(action.get("target"), ensure_ascii=False, sort_keys=True).casefold()
    ]
    if (
        requested_name is not None
        and project_name_fills
        and requested_name.group(1).strip().casefold() not in project_name_fills
    ):
        conflicts.append("required_project_name_not_filled")
    requested_file = re.search(r"file named\s+([a-z0-9_.-]+)", title)
    file_name_fills = [
        str(action.get("text") or "").casefold()
        for action in actions
        if action.get("action") == "fill"
        and "file name"
        in json.dumps(action.get("target"), ensure_ascii=False, sort_keys=True).casefold()
    ]
    if (
        requested_file is not None
        and file_name_fills
        and not any(
            value.rsplit("/", 1)[-1] == requested_file.group(1).strip().casefold()
            for value in file_name_fills
        )
    ):
        conflicts.append("required_file_name_not_filled")
    field_tokens = {
        "title": ("title",),
        "body": ("body",),
        "comment": ("comment",),
        "url": ("url",),
        "email": ("email", "newsletter"),
    }
    checked_fields: set[tuple[str, str]] = set()
    for evaluator in (task.get("source") or {}).get("verified_evaluators") or []:
        if evaluator.get("evaluator") != "NetworkEventEvaluator":
            continue
        post_data = (evaluator.get("expected") or {}).get("post_data") or {}
        if not isinstance(post_data, dict):
            continue
        for key, expected in post_data.items():
            if not isinstance(expected, str) or not expected.strip():
                continue
            key_name = str(key).casefold()
            field = next(
                (
                    name
                    for name in field_tokens
                    if key_name == name or key_name.endswith(f"[{name}]")
                ),
                None,
            )
            if field is None:
                continue
            expected_value = _semantic_field_value(field, expected)
            signature = (field, expected_value)
            if signature in checked_fields:
                continue
            checked_fields.add(signature)
            fills = [
                _semantic_field_value(field, str(action.get("text") or ""))
                for action in actions
                if action.get("action") == "fill"
                and any(
                    token
                    in json.dumps(
                        action.get("target"), ensure_ascii=False, sort_keys=True
                    ).casefold()
                    for token in field_tokens[field]
                )
            ]
            if fills and expected_value not in fills:
                conflicts.append(f"evaluator_{field}_value_not_filled")
            elif not fills and any(
                _semantic_field_value(field, str(action.get("text") or "")) == expected_value
                for action in actions
                if action.get("action") == "fill"
            ):
                conflicts.append(f"evaluator_{field}_value_filled_in_wrong_field")
    return tuple(conflicts)


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
            # WebArena-Verified evaluator contracts are immutable public benchmark
            # inputs.  Binding them prevents a passing trajectory from being
            # reused after an expected answer, network event, or result schema
            # changes without changing the executable evaluator adapter.
            "verified_evaluators": (task.get("source") or {}).get("verified_evaluators"),
        }
    )


def _qualification_rows(rows: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} must contain a non-empty task inventory")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get(key), str)
                or not row[key].strip() or row[key] in result):
            raise ValueError(f"{label} contains invalid or duplicate task identities")
        result[row[key]] = row
    return result


def _verify_attempt_projection(row: dict[str, Any], *, allow_missing: bool = False) -> bool:
    directory_value = row.get("evidence_directory")
    if not isinstance(directory_value, str) or not directory_value.strip():
        if allow_missing:
            return False
        raise ValueError("qualification attempt lacks evidence_directory")
    directory = Path(directory_value)
    path = contained_path(directory, "attempt.json", reject_symlinks=True)
    if not path.is_file():
        if allow_missing:
            return False
        raise ValueError("qualification attempt source evidence is missing")
    if not same_json_record(json.loads(path.read_bytes()), row):
        raise ValueError("qualification projection differs from immutable attempt evidence")
    return True


def _validated_attempt(
    path: Path, task_id: str, *, expected_contract_sha256: str
) -> tuple[str, str, int]:
    path = contained_path(path.parent, path.name, reject_symlinks=True)
    if not path.is_file():
        raise ValueError(f"{task_id}: validated attempt evidence is missing")
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    row = json.loads(raw)
    if (
        str(row.get("task_id")) != task_id
        or row.get("engine") != "chromium"
        or type(row.get("attempt")) is not int
        or row.get("attempt") != 1
        or row.get("is_retry") is not False
        or row.get("qualification_run") is not True
        or any(row.get(field) != "pass" for field in _PASS_STATUS_FIELDS)
    ):
        raise ValueError(f"{task_id}: validated attempt is not a passing primary qualification run")
    run_id = str(row.get("run_id") or "").strip()
    pass_index = row.get("qualification_pass_index")
    if not run_id:
        raise ValueError(f"{task_id}: validated attempt run_id is missing")
    if (
        type(pass_index) is not int
        or pass_index not in {1, 2}
        or type(row.get("qualification_required_passes")) is not int
        or row.get("qualification_required_passes") != 2
    ):
        raise ValueError(
            f"{task_id}: validated attempt must declare qualification pass 1 or 2 of 2"
        )
    if row.get("task_contract_sha256") != expected_contract_sha256:
        raise ValueError(f"{task_id}: validated attempt does not match corrected task contract")
    evidence_directory = Path(str(row.get("evidence_directory") or ""))
    if evidence_directory.is_symlink():
        raise ValueError("qualification evidence directory is symlinked")
    evidence_directory = evidence_directory.resolve()
    if evidence_directory != path.parent.resolve():
        raise ValueError(f"{task_id}: validated attempt evidence directory mismatch")
    har_path = contained_path(evidence_directory, "network.har", reject_symlinks=True)
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
        evidence_path = contained_path(evidence_directory, expected_name, reject_symlinks=True)
        if not evidence_path.is_file():
            raise ValueError(f"{task_id}: validated attempt {key} evidence is missing")
        expected_sha256 = str(metadata.get("sha256") or "")
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise ValueError(f"{task_id}: validated attempt {key} sha256 is invalid")
        if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError(f"{task_id}: validated attempt {key} sha256 mismatch")
    return sha256, run_id, int(pass_index)


def validated_attempt_sha256s(
    adjudication: dict[str, Any], task_id: str, *, expected_contract_sha256: str
) -> tuple[str, str]:
    """Validate and hash the two independent runs required for eligibility."""
    path_values = adjudication.get("validated_attempt_paths")
    if not isinstance(path_values, list) or len(path_values) != 2:
        raise ValueError(f"{task_id}: eligibility requires exactly two validated_attempt_paths")
    if any(not isinstance(value, str) or not value.strip() for value in path_values):
        raise ValueError(f"{task_id}: validated attempt paths must be non-empty strings")
    paths = [Path(value) for value in path_values]
    if len({path.resolve() for path in paths}) != 2:
        raise ValueError(f"{task_id}: validated attempt paths must be unique")
    validated = [
        _validated_attempt(
            path,
            task_id,
            expected_contract_sha256=expected_contract_sha256,
        )
        for path in paths
    ]
    run_ids = {run_id for _, run_id, _ in validated}
    pass_indices = {pass_index for _, _, pass_index in validated}
    if len(run_ids) != 2:
        raise ValueError(f"{task_id}: validated attempts must have distinct run_id values")
    if pass_indices != {1, 2}:
        raise ValueError(f"{task_id}: validated attempts must cover pass_index 1 and 2")
    ordered = sorted(validated, key=lambda item: item[2])
    return ordered[0][0], ordered[1][0]


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
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("qualification evidence paths must be existing non-symlinked files")
    rows = [row for path in paths for row in load_jsonl(path, skip_blank=True)]
    by_task: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if (
            row.get("engine") != "chromium"
            or row.get("is_retry") is not False
            or row.get("qualification_run") is not True
            or type(row.get("attempt")) is not int
            or row.get("attempt") != 1
            or type(row.get("qualification_pass_index")) is not int
            or type(row.get("qualification_required_passes")) is not int
            or row.get("qualification_required_passes") != 2
        ):
            raise ValueError("qualification evidence must contain primary Chromium attempts only")
        by_task.setdefault(str(row["task_id"]), []).append(row)
    _qualification_rows([{"id": task.task_id} for task in suite.tasks], "id", "suite")
    expected = {task.task_id for task in suite.tasks}
    if set(by_task) != expected or any(len(items) != 2 for items in by_task.values()):
        raise ValueError("qualification evidence must cover every task exactly twice")

    tasks: list[dict[str, Any]] = []
    for task in suite.tasks:
        task_rows = by_task[task.task_id]
        run_ids = {str(row.get("run_id") or "").strip() for row in task_rows}
        pass_indices = {row.get("qualification_pass_index") for row in task_rows}
        if "" in run_ids or len(run_ids) != 2:
            raise ValueError(f"{task.task_id}: qualification run_id values must be distinct")
        if pass_indices != {1, 2}:
            raise ValueError(
                f"{task.task_id}: qualification evidence must cover pass_index 1 and 2"
            )
        task_payload = {
            "id": task.task_id,
            "title": task.title,
            "trajectory": task.trajectory,
            "evaluator": task.evaluator,
            "environment": task.environment,
            "ground_truth": task.ground_truth,
            "source": task.source,
        }
        contract = task_contract_sha256(task_payload)
        if any(row.get("task_contract_sha256") != contract for row in task_rows):
            raise ValueError(f"{task.task_id}: qualification attempt does not match task contract")
        sources_present = [_verify_attempt_projection(row, allow_missing=True) for row in task_rows]
        semantic_conflicts = trajectory_semantic_conflicts(task_payload)
        if semantic_conflicts:
            disposition = "needs_trace_repair"
        elif not task.tool_calls:
            disposition = "needs_trace_repair"
        elif not all(sources_present):
            disposition = "needs_evidence_revalidation"
        elif all(
            all(row.get(field) == "pass" for field in _PASS_STATUS_FIELDS) for row in task_rows
        ):
            disposition = "eligible"
        elif all(
            row.get("process_status") == "pass"
            and row.get("result_status") == "pass"
            and row.get("status") == "pass"
            and row.get("combined_status") == "pass"
            for row in task_rows
        ):
            disposition = "needs_evidence_revalidation"
        elif all(
            row.get("process_status") == "pass" for row in task_rows
        ) and not task.ground_truth.get("response_builder"):
            disposition = "needs_response_annotation"
        else:
            disposition = "needs_trace_repair"
        failed_step = next(
            (
                item
                for row in task_rows
                for item in row.get("trace") or []
                if item.get("status") == "fail"
            ),
            None,
        )
        adjudication = None
        if disposition == "eligible":
            validated_paths = [
                str(Path(str(row.get("evidence_directory") or "")) / "attempt.json")
                for row in sorted(task_rows, key=lambda item: item["qualification_pass_index"])
            ]
            adjudication = {
                "status": "eligible",
                "validated_attempt_paths": validated_paths,
            }
            validated_attempt_sha256s(
                adjudication,
                task.task_id,
                expected_contract_sha256=contract,
            )
        tasks.append(
            {
                "task_id": task.task_id,
                "disposition": disposition,
                "process_status": [row.get("process_status") for row in task_rows],
                "result_status": [row.get("result_status") for row in task_rows],
                "semantic_conflicts": list(semantic_conflicts),
                "failed_step": failed_step,
                "task_contract_sha256": contract,
                "evidence_directories": [
                    row.get("evidence_directory")
                    for row in sorted(task_rows, key=lambda item: item["qualification_pass_index"])
                ],
                "adjudication": adjudication,
            }
        )
    return {
        "schema": QUALIFICATION_SCHEMA,
        "attempts_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths],
        "policy": {
            "qualification_browser": "upstream Chromium",
            "independent_passes_per_task": 2,
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
            "observed_json_zip",
            "observed_json_filter_zip",
            "observed_ranked_labels",
            "observed_review_authors_matching",
            "observed_currency_range",
            "observed_not_found",
            "observed_action_not_allowed",
            "observed_action_blocked",
            "observed_permission_denied",
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
    _qualification_rows(tasks, "id", "suite")
    decisions = _qualification_rows(qualification.get("tasks"), "task_id", "qualification")
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
        "attempts_per_task": 2,
    }
    result["benchmark"] = benchmark
    return result


def build_evidence_revalidation_suite(
    suite_payload: dict[str, Any], qualification: dict[str, Any]
) -> dict[str, Any]:
    """Re-run every provisionally eligible task under the current evidence contract."""
    result = deepcopy(suite_payload)
    tasks = result.get("tasks") or []
    _qualification_rows(tasks, "id", "suite")
    decisions = _qualification_rows(qualification.get("tasks"), "task_id", "qualification")
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
            "attempts_per_task": 2,
            "purpose": "bind HAR v3, final screenshot, and final visible page state",
        },
    }
    return result


def apply_adjudication(
    suite_payload: dict[str, Any],
    qualification: dict[str, Any],
    *,
    task_ids: set[str] | None = None,
) -> dict[str, Any]:
    suite_payload = deepcopy(suite_payload)
    decisions = _qualification_rows(qualification.get("tasks"), "task_id", "qualification")
    tasks = suite_payload.get("tasks") or []
    _qualification_rows(tasks, "id", "suite")
    suite_ids = {str(task["id"]) for task in tasks}
    selected_ids = suite_ids if task_ids is None else set(task_ids)
    if not selected_ids or not selected_ids <= suite_ids:
        raise ValueError("incremental qualification contains unknown or empty task selection")
    if task_ids is None and set(decisions) != suite_ids:
        raise ValueError("qualification task set does not match suite")
    if task_ids is not None and not selected_ids <= set(decisions):
        raise ValueError("incremental qualification lacks selected task decisions")
    if (
        any(
            (item.get("adjudication") or {}).get("status") == "eligible"
            for item in decisions.values()
        )
        and qualification.get("schema") != QUALIFICATION_SCHEMA
    ):
        raise ValueError(f"eligible tasks require qualification schema {QUALIFICATION_SCHEMA}")
    for task in tasks:
        if str(task["id"]) not in selected_ids:
            continue
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
            if key
            not in {
                "validated_attempt_path",
                "validated_attempt_paths",
                "validated_run_sha256",
                "validated_run_sha256s",
                "corrected_trajectory",
                "response_builder",
            }
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
            public_adjudication["validated_run_sha256s"] = list(
                validated_attempt_sha256s(
                    adjudication,
                    str(task["id"]),
                    expected_contract_sha256=task_contract_sha256(task),
                )
            )
        task["ground_truth"]["qualification"] = public_adjudication
    final_statuses = [
        str(((task.get("ground_truth") or {}).get("qualification") or {}).get("status"))
        for task in tasks
    ]
    if any(status not in {"eligible", "skipped"} for status in final_statuses):
        raise ValueError("applied suite contains unresolved qualification states")
    suite_payload.setdefault("benchmark", {})["qualification"] = {
        "schema": qualification.get("schema"),
        "qualified_count": final_statuses.count("eligible"),
        "skipped_count": final_statuses.count("skipped"),
    }
    return suite_payload


def task_ids_from_scope(
    scope_path: str | Path,
    suite_path: str | Path,
    batch_id: str,
    *,
    shard_count: int | None = None,
    shard_index: int | None = None,
) -> tuple[str, ...]:
    scope_file = Path(scope_path)
    suite_file = Path(suite_path)
    scope = json.loads(scope_file.read_text(encoding="utf-8"))
    if scope.get("schema") != "lex_browser_use_native_scope/1":
        raise ValueError("native scope schema is invalid")
    if scope.get("suite_sha256") != hashlib.sha256(suite_file.read_bytes()).hexdigest():
        raise ValueError("native scope does not match suite sha256")
    matches = [
        row for row in scope.get("qualification_batches") or [] if row.get("id") == batch_id
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate native scope batch: {batch_id}")
    batch = matches[0]
    task_ids = tuple(str(value) for value in batch.get("task_ids") or [])
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError(f"native scope batch is empty or contains duplicates: {batch_id}")
    if not set(task_ids) <= set(scope.get("included_task_ids") or []):
        raise ValueError(f"native scope batch contains excluded tasks: {batch_id}")
    if (shard_count is None) != (shard_index is None):
        raise ValueError("batch shard count and index must be provided together")
    if shard_count is not None and shard_index is not None:
        declared_shard_count = int(batch.get("shard_count") or 1)
        if shard_count != declared_shard_count:
            raise ValueError(
                f"batch shard count must match pre-registered value {declared_shard_count}"
            )
        if shard_count < 1 or shard_count > len(task_ids):
            raise ValueError("batch shard count must be between 1 and the batch task count")
        if shard_index < 0 or shard_index >= shard_count:
            raise ValueError("batch shard index must be zero-based and smaller than shard count")
        task_ids = task_ids[shard_index::shard_count]
    return task_ids


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualification_shards(scope_path: Path, suite_path: Path) -> tuple[dict[str, Any], ...]:
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if scope.get("schema") != "lex_browser_use_native_scope/1":
        raise ValueError("native scope schema is invalid")
    suite_sha256 = _sha256(suite_path)
    if scope.get("suite_sha256") != suite_sha256:
        raise ValueError("native scope does not match suite sha256")
    shards: list[dict[str, Any]] = []
    for batch in scope.get("qualification_batches") or []:
        batch_id = batch.get("id")
        if not isinstance(batch_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", batch_id):
            raise ValueError("qualification batch identifier must be a safe filename segment")
        shard_count = batch.get("shard_count")
        if type(shard_count) is not int or shard_count < 1:
            raise ValueError("every qualification batch requires id and shard_count")
        for shard_index in range(shard_count):
            task_ids = task_ids_from_scope(
                scope_path,
                suite_path,
                batch_id,
                shard_count=shard_count,
                shard_index=shard_index,
            )
            shards.append(
                {
                    "id": f"{batch_id}-{shard_index}-of-{shard_count}",
                    "batch_id": batch_id,
                    "shard_count": shard_count,
                    "shard_index": shard_index,
                    "task_ids": task_ids,
                }
            )
    included = tuple(str(value) for value in scope.get("included_task_ids") or [])
    flattened = tuple(task_id for shard in shards for task_id in shard["task_ids"])
    if (not included or len(included) != len(set(included))
            or len(flattened) != len(set(flattened)) or set(flattened) != set(included)):
        raise ValueError("qualification shards do not partition the included task set exactly")
    return tuple(shards)


def qualification_waves(
    shards: tuple[dict[str, Any], ...],
    scope: dict[str, Any],
    *,
    max_workers: int,
    isolated_instances: int = 1,
) -> tuple[tuple[tuple[dict[str, Any], ...], ...], ...]:
    """Group complete site batches into deterministic, conflict-free parallel waves."""
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if isolated_instances < 1:
        raise ValueError("isolated_instances must be positive")
    sites_by_batch = {
        str(batch["id"]): frozenset(str(site) for site in batch.get("sites") or [])
        for batch in scope.get("qualification_batches") or []
    }
    shards_by_batch: dict[str, list[dict[str, Any]]] = {}
    for shard in shards:
        batch_id = str(shard["batch_id"])
        if batch_id not in sites_by_batch or not sites_by_batch[batch_id]:
            raise ValueError(f"qualification batch has no declared sites: {batch_id}")
        shards_by_batch.setdefault(batch_id, []).append(shard)
    remaining = list(shards_by_batch)
    waves: list[tuple[tuple[dict[str, Any], ...], ...]] = []
    while remaining:
        occupied: set[str] = set()
        selected: list[str] = []
        for batch_id in remaining:
            sites = sites_by_batch[batch_id]
            capacity = min(max_workers, isolated_instances)
            isolated = isolated_instances > 1
            if len(selected) < capacity and (isolated or not occupied.intersection(sites)):
                selected.append(batch_id)
                occupied.update(sites)
        waves.append(tuple(tuple(shards_by_batch[batch_id]) for batch_id in selected))
        selected_set = set(selected)
        remaining = [batch_id for batch_id in remaining if batch_id not in selected_set]
    return tuple(waves)


def validate_completed_shard(
    output: Path,
    *,
    suite_sha256: str,
    scope_sha256: str,
    shard: dict[str, Any],
    qualification_pass_index: int,
    qualification_required_passes: int = 2,
) -> None:
    required = {
        name: contained_path(output, name, reject_symlinks=True)
        for name in ("attempts.jsonl", "summary.json", "manifest.json")
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ValueError(f"{shard['id']}: incomplete qualification output; missing {missing}")
    manifest = json.loads(required["manifest.json"].read_text(encoding="utf-8"))
    provenance = manifest.get("artifact_provenance") or {}
    if provenance.get("attempts_sha256") != _sha256(required["attempts.jsonl"]) or provenance.get(
        "summary_sha256"
    ) != _sha256(required["summary.json"]):
        raise ValueError(f"{shard['id']}: qualification artifact hash mismatch")
    protocol = manifest.get("protocol") or {}
    selection = protocol.get("task_selection") or {}
    expected_selection = {
        "kind": "native_scope_batch",
        "scope_sha256": scope_sha256,
        "batch_id": shard["batch_id"],
        "shard_count": shard["shard_count"],
        "shard_index": shard["shard_index"],
        "qualification_pass_index": qualification_pass_index,
        "qualification_required_passes": qualification_required_passes,
    }
    expected_ids = list(shard["task_ids"])
    if (
        manifest.get("suite_sha256") != suite_sha256
        or protocol.get("qualification_run") is not True
        or protocol.get("engines") != ["chromium"]
        or protocol.get("max_failure_retries") != 0
        or protocol.get("task_filter") != expected_ids
        or selection != expected_selection
        or protocol.get("qualification_pass_index") != qualification_pass_index
        or protocol.get("qualification_required_passes") != qualification_required_passes
    ):
        raise ValueError(f"{shard['id']}: qualification manifest contract mismatch")
    rows = load_jsonl(required["attempts.jsonl"], skip_blank=True)
    if len(rows) != len(expected_ids) or {str(row.get("task_id")) for row in rows} != set(
        expected_ids
    ):
        raise ValueError(f"{shard['id']}: qualification attempts do not cover the shard exactly")
    if any(
        row.get("engine") != "chromium"
        or row.get("qualification_run") is not True
        or row.get("is_retry") is not False
        or type(row.get("attempt")) is not int
        or row.get("attempt") != 1
        or row.get("qualification_pass_index") != qualification_pass_index
        or row.get("qualification_required_passes") != qualification_required_passes
        for row in rows
    ):
        raise ValueError(f"{shard['id']}: qualification attempts violate primary-run policy")
    expected_creator = {"name": HAR_CREATOR_NAME, "version": HAR_CREATOR_VERSION}
    for row in rows:
        evidence_directory = Path(str(row.get("evidence_directory") or "")).absolute()
        try:
            relative = evidence_directory.relative_to(output.absolute())
        except ValueError as exc:
            raise ValueError(
                f"{shard['id']}: attempt evidence directory escapes the shard"
            ) from exc
        har_path = contained_path(output, relative / "network.har", reject_symlinks=True)
        if not har_path.is_file():
            raise ValueError(f"{shard['id']}: attempt network HAR is missing")
        _verify_attempt_projection(row)
        har = json.loads(har_path.read_text(encoding="utf-8"))
        if (har.get("log") or {}).get("creator") != expected_creator:
            raise ValueError(
                f"{shard['id']}: attempt network HAR creator must be {expected_creator}"
            )


def build_scoped_repair_queue(
    suite: TaskSuite,
    attempts_paths: list[Path],
    scope: dict[str, Any],
    *,
    scope_sha256: str,
) -> dict[str, Any]:
    included = {str(value) for value in scope.get("included_task_ids") or []}
    excluded_rows = scope.get("excluded_tasks") or []
    excluded = {str(row.get("task_id")) for row in excluded_rows}
    suite_ids = {task.task_id for task in suite.tasks}
    if included & excluded or included | excluded != suite_ids:
        raise ValueError("native scope must partition the complete suite")
    measured_suite = TaskSuite(
        schema=suite.schema,
        tasks=tuple(task for task in suite.tasks if task.task_id in included),
    )
    queue = build_repair_queue(measured_suite, attempts_paths)
    decisions = {str(row["task_id"]): row for row in queue["tasks"]}
    for row in excluded_rows:
        task_id = str(row["task_id"])
        reason = str(row.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"{task_id}: native scope exclusion requires reason")
        decisions[task_id] = {
            "task_id": task_id,
            "disposition": "excluded_by_native_scope",
            "process_status": None,
            "result_status": None,
            "failed_step": None,
            "evidence_directory": None,
            "adjudication": {
                "status": "skipped",
                "reason": reason,
                "scope_exclusion": {
                    "architecture": scope.get("architecture"),
                    "sites": row.get("sites") or [],
                    "incompatible_sites": row.get("incompatible_sites") or [],
                    **({"kind": row["exclusion_kind"]} if row.get("exclusion_kind") else {}),
                    **({"evidence_id": row["evidence_id"]} if row.get("evidence_id") else {}),
                },
            },
        }
    queue["tasks"] = [decisions[task.task_id] for task in suite.tasks]
    queue["native_scope"] = {
        "sha256": scope_sha256,
        "architecture": scope.get("architecture"),
        "included_count": len(included),
        "excluded_count": len(excluded),
    }
    return queue


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path.is_symlink():
        raise ValueError(f"refusing to overwrite symlinked derived evidence: {path}")
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite different derived evidence: {path}")
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(content)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_text(encoding="utf-8") != content:
                raise ValueError(
                    f"refusing to overwrite different derived evidence: {path}"
                ) from None
    finally:
        temporary.unlink(missing_ok=True)


def main_qualification(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--native-scope", type=Path, required=True)
    parser.add_argument(
        "--webarena-config",
        type=Path,
        action="append",
        required=True,
        help=(
            "runtime config; repeat with independently provisioned Rainbond slice configs "
            "to run overlapping site batches concurrently"
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="dotenv file passed to every qualification shard; defaults to repository .env",
    )
    parser.add_argument(
        "--browser-docker-host",
        help="absolute unix:// socket used for measured browser containers",
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--batch-id", action="append", dest="batch_ids")
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--memory", default="2g")
    parser.add_argument("--sample-interval-ms", type=int, default=50)
    parser.add_argument("--settle-ms", type=int, default=1500)
    parser.add_argument(
        "--task-timeout-ms",
        type=int,
        default=660_000,
        help="full wall-clock budget per task, including the bounded WebArena site reset",
    )
    parser.add_argument(
        "--max-site-workers",
        type=int,
        default=1,
        help="parallel site batches; batches sharing a site always remain serialized",
    )
    parser.add_argument("--dry-run", action="store_true")
    run_qualification(parser.parse_args(argv))


def run_qualification(args: argparse.Namespace) -> None:
    suite_path = args.suite.resolve()
    scope_path = args.native_scope.resolve()
    config_paths = tuple(path.resolve() for path in args.webarena_config)
    evidence_root = args.evidence_root.absolute()
    missing_configs = [str(path) for path in config_paths if not path.is_file()]
    if missing_configs:
        raise ValueError(f"WebArena runtime configs are missing: {missing_configs}")
    if len(set(config_paths)) != len(config_paths):
        raise ValueError("WebArena runtime configs must be distinct isolated instances")
    # Rainbond refreshes runtime gateway URLs after a receipt-bound site reset.
    # Only the task suite and declared scope are static orchestration inputs.
    frozen_inputs = {path: _sha256(path) for path in (suite_path, scope_path)}

    def check_inputs() -> None:
        if any(_sha256(path) != digest for path, digest in frozen_inputs.items()):
            raise ValueError("qualification inputs changed during orchestration")

    all_shards = qualification_shards(scope_path, suite_path)
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    selected_batches = set(args.batch_ids or [])
    known_batches = {str(shard["batch_id"]) for shard in all_shards}
    if selected_batches - known_batches:
        unknown = sorted(selected_batches - known_batches)
        raise ValueError(f"unknown qualification batches: {unknown}")
    selected_shards = tuple(
        shard
        for shard in all_shards
        if not selected_batches or shard["batch_id"] in selected_batches
    )
    suite_sha256 = frozen_inputs[suite_path]
    scope_sha256 = frozen_inputs[scope_path]
    waves = qualification_waves(
        selected_shards,
        scope,
        max_workers=args.max_site_workers,
        isolated_instances=len(config_paths),
    )

    def run_shard(shard: dict[str, Any], qualification_pass_index: int, config_path: Path) -> Path:
        check_inputs()
        output = contained_path(
            evidence_root, f"pass-{qualification_pass_index}/{shard['id']}", reject_symlinks=True
        )
        if output.exists() and not args.dry_run:
            try:
                validate_completed_shard(
                    output,
                    suite_sha256=suite_sha256,
                    scope_sha256=scope_sha256,
                    shard=shard,
                    qualification_pass_index=qualification_pass_index,
                )
            except Exception:
                failed = contained_path(
                    evidence_root,
                    "_failed/" + (
                        f"pass-{qualification_pass_index}-{shard['id']}-"
                        f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
                    ),
                    reject_symlinks=True,
                )
                failed.parent.mkdir(parents=True, exist_ok=True)
                output.replace(failed)
                print(f"PRESERVE incomplete {shard['id']} at {failed}", flush=True)
            else:
                print(
                    f"SKIP verified pass-{qualification_pass_index} {shard['id']}",
                    flush=True,
                )
                return output / "attempts.jsonl"
        command = [
            sys.executable,
            "-m",
            "harness.trajectory_replay.public_webarena.cli",
            "--suite",
            str(suite_path),
            "--native-scope",
            str(scope_path),
            "--batch-id",
            str(shard["batch_id"]),
            "--batch-shard-count",
            str(shard["shard_count"]),
            "--batch-shard-index",
            str(shard["shard_index"]),
            "--webarena-config",
            str(config_path),
            "--qualification-run",
            "--qualification-pass-index",
            str(qualification_pass_index),
            "--qualification-required-passes",
            "2",
            "--qualification-lock-key",
            str(shard["batch_id"]),
            "--engines",
            "chromium",
            "--failure-retries",
            "0",
            "--cpus",
            str(args.cpus),
            "--memory",
            args.memory,
            "--sample-interval-ms",
            str(args.sample_interval_ms),
            "--settle-ms",
            str(args.settle_ms),
            "--task-timeout-ms",
            str(args.task_timeout_ms),
            "--output",
            str(output),
            "--reports",
            str(evidence_root / "_qualification_reports_unused"),
        ]
        if args.env_file is not None:
            command.extend(["--env-file", str(args.env_file.resolve())])
        if args.browser_docker_host is not None:
            command.extend(["--browser-docker-host", args.browser_docker_host])
        if args.dry_run:
            print(" ".join(command), flush=True)
            return output / "attempts.jsonl"
        subprocess.run(command, check=True, env=os.environ.copy())
        check_inputs()
        validate_completed_shard(
            output,
            suite_sha256=suite_sha256,
            scope_sha256=scope_sha256,
            shard=shard,
            qualification_pass_index=qualification_pass_index,
        )
        return output / "attempts.jsonl"

    attempts_by_id: dict[tuple[int, str], Path] = {}
    for qualification_pass_index in (1, 2):
        for wave_index, wave in enumerate(waves, start=1):
            print(
                f"PASS {qualification_pass_index}/2 WAVE {wave_index}/{len(waves)}: "
                + ", ".join(str(batch[0]["batch_id"]) for batch in wave),
                flush=True,
            )

            def run_batch(
                batch: tuple[dict[str, Any], ...],
                config_path: Path,
                qualification_pass_index: int = qualification_pass_index,
            ) -> list[tuple[tuple[int, str], Path]]:
                return [
                    (
                        (qualification_pass_index, str(shard["id"])),
                        run_shard(shard, qualification_pass_index, config_path),
                    )
                    for shard in batch
                ]

            # Extra ready slices are allowed; only a shortage is an error.
            assigned = tuple(zip(wave, config_paths, strict=False))
            if len(assigned) != len(wave):
                raise RuntimeError("parallel wave exceeds isolated WebArena slice count")
            if args.dry_run or len(wave) == 1:
                completed = [run_batch(batch, config) for batch, config in assigned]
            else:
                with ThreadPoolExecutor(max_workers=len(wave)) as executor:
                    completed = list(
                        executor.map(
                            lambda item: run_batch(item[0], item[1]),
                            assigned,
                        )
                    )
            for batch_rows in completed:
                attempts_by_id.update(batch_rows)
    attempts_paths = [
        attempts_by_id[(pass_index, str(shard["id"]))]
        for pass_index in (1, 2)
        for shard in selected_shards
    ]
    if not args.dry_run and not selected_batches:
        check_inputs()
        queue = build_scoped_repair_queue(
            load_suite(suite_path),
            attempts_paths,
            scope,
            scope_sha256=scope_sha256,
        )
        _write_immutable(
            contained_path(evidence_root, "qualification-queue.json", reject_symlinks=True), queue
        )


ALLOWED_DISPOSITIONS = {
    "eligible",
    "needs_evidence_revalidation",
    "needs_response_annotation",
    "needs_trace_repair",
}
ALLOWED_TASK_TYPES = {"retrieve", "navigate", "mutate"}


def build_qualification_subset(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    *,
    dispositions: set[str],
    task_types: set[str],
    site: str | None = None,
    sites_exact: set[str] | None = None,
) -> dict[str, Any]:
    if not dispositions or not dispositions <= ALLOWED_DISPOSITIONS:
        raise ValueError("qualification subset has an unsupported disposition")
    if not task_types or not task_types <= ALLOWED_TASK_TYPES:
        raise ValueError("qualification subset has an unsupported task type")
    if site is not None and sites_exact is not None:
        raise ValueError("site and sites_exact are mutually exclusive")
    if sites_exact is not None and not sites_exact:
        raise ValueError("sites_exact must not be empty")
    disposition_by_id = {
        str(row.get("task_id")): str(row.get("disposition"))
        for row in qualification.get("tasks") or []
    }
    selected = []
    for source_task in suite.get("tasks") or []:
        task_id = str(source_task.get("id"))
        if disposition_by_id.get(task_id) not in dispositions:
            continue
        if not task_types.intersection(source_task.get("tags") or []):
            continue
        sites = (source_task.get("environment") or {}).get("sites") or []
        if site is not None and site not in sites:
            continue
        if sites_exact is not None and set(sites) != sites_exact:
            continue
        task = deepcopy(source_task)
        task.setdefault("ground_truth", {})["qualification"] = {"status": "pending"}
        selected.append(task)
    if not selected:
        raise ValueError("qualification subset is empty")

    result = deepcopy(suite)
    result["tasks"] = selected
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "qualification_subset": {
            "non_canonical": True,
            "dispositions": sorted(dispositions),
            "task_types": sorted(task_types),
            "site": site,
            "sites_exact": sorted(sites_exact) if sites_exact is not None else None,
            "task_count": len(selected),
        },
    }
    return result


def _csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _write_qualified_suite(path: Path, payload: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="webarena-qualification-") as directory:
        staged = Path(directory) / "suite.json"
        staged.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        load_suite(staged)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_immutable(path, payload)


def main_subset(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--dispositions", required=True)
    parser.add_argument("--task-types", required=True)
    parser.add_argument("--site")
    parser.add_argument("--sites-exact")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_qualification_subset(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        dispositions=_csv(args.dispositions),
        task_types=_csv(args.task_types),
        site=args.site,
        sites_exact=_csv(args.sites_exact) if args.sites_exact is not None else None,
    )
    _write_qualified_suite(args.output, result)


def build_requalification_suite(
    suite: dict[str, Any], scope: dict[str, Any], *, predecessor_sha256: str
) -> dict[str, Any]:
    if suite.get("schema") != "lex_browser_use_tasks/4":
        raise ValueError("suite schema is invalid")
    if scope.get("schema") != "lex_browser_use_native_scope/1":
        raise ValueError("native scope schema is invalid")
    included = {str(value) for value in scope.get("included_task_ids") or []}
    excluded = {str(row["task_id"]) for row in scope.get("excluded_tasks") or []}
    task_ids = {str(task["id"]) for task in suite.get("tasks") or []}
    if included & excluded or included | excluded != task_ids:
        raise ValueError("native scope must partition the complete suite")
    result = deepcopy(suite)
    previous = Counter()
    for task in result["tasks"]:
        task_id = str(task["id"])
        qualification = (task.get("ground_truth") or {}).get("qualification") or {}
        previous[str(qualification.get("status") or "missing")] += 1
        if task_id in included:
            task["ground_truth"]["qualification"] = {
                "status": "pending",
                "reason": (
                    "full candidate requalification with complete failure-page evidence; "
                    "prior decisions remain bound by the predecessor suite hash"
                ),
            }
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "requalification": {
            "predecessor_suite_sha256": predecessor_sha256,
            "included_count": len(included),
            "excluded_count": len(excluded),
            "previous_status_counts": dict(sorted(previous.items())),
            "trajectory_policy": "preserve every current canonical action and annotation",
        },
    }
    return result


def main_requalification_suite(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--native-scope", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    suite_bytes = args.suite.read_bytes()
    result = build_requalification_suite(
        json.loads(suite_bytes),
        json.loads(args.native_scope.read_text(encoding="utf-8")),
        predecessor_sha256=hashlib.sha256(suite_bytes).hexdigest(),
    )
    _write_qualified_suite(args.output, result)


def build_probe(suite: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(proposal, dict)
            or proposal.get("schema") != "lex_browser_use_repair_proposal/1"):
        raise ValueError("repair proposal schema is invalid")
    task_id = str(proposal.get("task_id") or "")
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError("repair proposal must identify exactly one suite task")
    append_actions = proposal.get("append_actions") or []
    replace_actions = proposal.get("replace_actions") or []
    response_builder = proposal.get("response_builder")
    reason = proposal.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""
    if any(not isinstance(actions, list) or any(not isinstance(row, dict) for row in actions)
           for actions in (append_actions, replace_actions)):
        raise ValueError("repair proposal actions must be lists of objects")
    harness_revalidation = proposal.get("harness_revalidation") is True
    if harness_revalidation and (
        append_actions or replace_actions or response_builder is not None
    ):
        raise ValueError("harness revalidation cannot modify actions or response builder")
    if not harness_revalidation and (
        bool(append_actions) == bool(replace_actions)
        or not isinstance(response_builder, dict)
    ):
        raise ValueError(
            "repair proposal requires exactly one of append_actions or replace_actions, "
            "plus response builder and reason"
        )
    if not reason:
        raise ValueError("repair proposal requires a reason")

    task = deepcopy(matches[0])
    original = deepcopy(task["trajectory"])
    if (proposal.get("source_task_contract_sha256") is not None
            and proposal["source_task_contract_sha256"] != task_contract_sha256(task)):
        raise ValueError("repair proposal source task contract changed")
    if (proposal.get("source_trajectory_sha256") is not None
            and proposal["source_trajectory_sha256"] != _json_sha256(original)):
        raise ValueError("repair proposal source trajectory changed")
    if not harness_revalidation:
        if replace_actions:
            task["trajectory"]["actions"] = deepcopy(replace_actions)
        else:
            task["trajectory"]["actions"].extend(deepcopy(append_actions))
    provenance = dict(task["trajectory"].get("provenance") or {})
    provenance.update(
        {
            "kind": (
                "harness_revalidation_probe"
                if harness_revalidation
                else "codex_repair_probe"
            ),
            "original_trajectory_sha256": _proposal_digest(original),
            "proposal_sha256": _proposal_digest(proposal),
        }
    )
    task["trajectory"]["provenance"] = provenance
    task["ground_truth"]["expected_actions"] = [
        action["action"] for action in task["trajectory"]["actions"]
    ]
    if not harness_revalidation:
        task["ground_truth"]["response_builder"] = deepcopy(response_builder)
    task["ground_truth"]["qualification"] = {
        "status": "pending",
        "reason": reason,
    }
    result = deepcopy(suite)
    result["tasks"] = [task]
    benchmark = dict(result.get("benchmark") or {})
    benchmark["repair_probe"] = {
        "task_id": task_id,
        "proposal_sha256": _proposal_digest(proposal),
        "non_canonical": True,
        "harness_revalidation": harness_revalidation,
    }
    result["benchmark"] = benchmark
    return result


def _proposal_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_proposal_suite(
    suite: dict[str, Any], proposals: list[dict[str, Any]], *,
    kind: str = "repair_probe_batch",
) -> dict[str, Any]:
    if kind not in {"repair_probe_batch", "codex_repair_validation"}:
        raise ValueError("unsupported repair batch protocol")
    if not proposals:
        raise ValueError("repair batch requires at least one proposal")
    task_ids = [str(item.get("task_id") or "") for item in proposals]
    if not all(task_ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError("repair proposals must have unique non-empty task IDs")
    result = deepcopy(suite)
    result["tasks"] = [build_probe(suite, proposal)["tasks"][0] for proposal in proposals]
    metadata = {"non_canonical": True, "task_count": len(task_ids), "task_ids": task_ids}
    if kind == "codex_repair_validation":
        metadata.update({
            "qualification_required_passes": 2,
            "proposal_effect": "none_until_both_qualification_passes_succeed",
        })
    else:
        metadata["harness_revalidation"] = False
    result["benchmark"] = {**(result.get("benchmark") or {}), kind: metadata}
    return result


def main_repair_probe(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build a pending one-task repair probe")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = build_probe(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.proposal.read_text(encoding="utf-8")),
    )
    _write_qualified_suite(args.output, payload)


def _main_proposal_suite(argv, kind: str) -> None:
    parser = argparse.ArgumentParser(description="Build a pending repair validation batch")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_proposal_suite(
        json.loads(args.suite.read_text(encoding="utf-8")),
        [json.loads(path.read_text(encoding="utf-8")) for path in args.proposal],
        kind=kind,
    )
    _write_qualified_suite(args.output, result)


def main_repair_batch(argv=None) -> None:
    _main_proposal_suite(argv, "repair_probe_batch")


def main_codex_repair_suite(argv=None) -> None:
    _main_proposal_suite(argv, "codex_repair_validation")


def _default_skip_reason(decision: dict[str, Any]) -> str:
    disposition = str(decision.get("disposition") or "")
    if disposition == "needs_response_annotation":
        return (
            "The official trajectory did not produce an evaluator-compatible answer, and no "
            "bounded DOM-observation repair passed independent Chromium qualification."
        )
    if disposition == "needs_evidence_revalidation":
        return (
            "The provisional result was not reproduced under the complete HAR and final-page "
            "evidence contract."
        )
    return (
        "The official trajectory failed isolated Chromium replay or conflicted with the task "
        "and evaluator; no bounded correction passed independent qualification."
    )


def qualification_from_applied_suite(suite: dict[str, Any]) -> dict[str, Any]:
    """Rehydrate adjudication state without requiring old private evidence paths."""
    decisions: list[dict[str, Any]] = []
    tasks = _qualification_rows(suite.get("tasks"), "id", "applied suite")
    for task in tasks.values():
        qualification = deepcopy((task.get("ground_truth") or {}).get("qualification") or {})
        status = qualification.get("status")
        if status not in {"eligible", "skipped"}:
            raise ValueError(f"{task.get('id')}: applied suite has unresolved qualification")
        if status == "eligible":
            hashes = qualification.get("validated_run_sha256s")
            if (not isinstance(hashes, list) or len(hashes) != 2
                    or any(not isinstance(value, str) or not _SHA256_RE.fullmatch(value)
                           for value in hashes) or len(set(hashes)) != 2):
                raise ValueError(
                    f"{task['id']}: applied eligibility requires two distinct proof hashes"
                )
        decisions.append(
            {
                "task_id": str(task["id"]),
                "disposition": "eligible" if status == "eligible" else "needs_trace_repair",
                "adjudication": qualification,
            }
        )
    return {
        "schema": QUALIFICATION_SCHEMA,
        "policy": {
            "qualification_browser": "upstream Chromium",
            "independent_passes_per_task": 2,
            "task_and_evaluator_are_immutable": True,
            "browser_comparison_results_must_not_be_used": True,
            "allowed_final_statuses": ["eligible", "skipped"],
        },
        "tasks": decisions,
    }


def _collect_validated_run(
    canonical_tasks: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    validated_suite: dict[str, Any],
    attempts_path: Path,
    candidates: dict[tuple[str, str], dict[str, Any]],
) -> None:
    validated_tasks = _qualification_rows(validated_suite.get("tasks"), "id", "validated suite")
    if attempts_path.is_symlink():
        raise ValueError("qualification attempt projection is symlinked")
    rows = load_jsonl(attempts_path, skip_blank=True)
    identities = {
        (
            str(row.get("task_id")),
            str(row.get("run_id")),
            row.get("qualification_pass_index"),
        )
        for row in rows
    }
    if not rows or len(identities) != len(rows):
        raise ValueError(f"validated run has empty or duplicate attempt rows: {attempts_path}")
    for row in rows:
        task_id = str(row.get("task_id") or "")
        task = validated_tasks.get(task_id)
        if task is None or task_id not in canonical_tasks or task_id not in decisions:
            raise ValueError(f"validated run contains unknown task {task_id}")
        if (
            row.get("engine") != "chromium"
            or row.get("qualification_run") is not True
            or row.get("is_retry") is not False
            or type(row.get("attempt")) is not int
            or row.get("attempt") != 1
            or type(row.get("qualification_pass_index")) is not int
            or row.get("qualification_pass_index") not in {1, 2}
            or type(row.get("qualification_required_passes")) is not int
            or row.get("qualification_required_passes") != 2
        ):
            raise ValueError(f"{task_id}: validated run violates cold primary policy")
        _verify_attempt_projection(row)
        if not (task.get("trajectory") or {}).get("actions"):
            raise ValueError(f"{task_id}: qualified candidate requires a non-empty trajectory")
        original = canonical_tasks[task_id]
        for key in ("id", "title", "evaluator", "environment"):
            if task.get(key) != original.get(key):
                raise ValueError(f"{task_id}: validated suite changed immutable {key}")
        if ((task.get("source") or {}).get("verified_evaluators")
                != (original.get("source") or {}).get("verified_evaluators")):
            raise ValueError(f"{task_id}: validated suite changed immutable evaluator contracts")
        if row.get("task_contract_sha256") != task_contract_sha256(task):
            raise ValueError(f"{task_id}: validated run does not match task contract")
        evidence_directory = str(row.get("evidence_directory") or "").strip()
        if not evidence_directory:
            raise ValueError(f"{task_id}: validated run lacks evidence_directory")
        contract = task_contract_sha256(task)
        candidate = candidates.setdefault(
            (task_id, contract),
            {
                "task_id": task_id,
                "contract": contract,
                "task": deepcopy(task),
                "attempt_paths": [],
                "passing_paths": [],
            },
        )
        attempt_path = str(Path(evidence_directory) / "attempt.json")
        candidate["attempt_paths"].append(attempt_path)
        if all(
            row.get(key) == "pass"
            for key in (
                "process_status",
                "result_status",
                "evidence_status",
                "status",
                "combined_status",
            )
        ):
            candidate["passing_paths"].append(attempt_path)


def _overlay_two_pass_candidates(
    canonical_tasks: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    candidates: dict[tuple[str, str], dict[str, Any]],
) -> set[str]:
    overlaid: set[str] = set()
    for candidate in candidates.values():
        task_id = candidate["task_id"]
        attempt_paths = candidate["attempt_paths"]
        passing_paths = candidate["passing_paths"]
        if len(attempt_paths) > 2:
            raise ValueError(f"{task_id}: more than two validation attempts")
        if len(attempt_paths) != 2 or len(passing_paths) != 2:
            continue
        if task_id in overlaid:
            raise ValueError(f"{task_id}: multiple task contracts passed qualification")
        task = candidate["task"]
        contract = candidate["contract"]
        adjudication: dict[str, Any] = {
            "status": "eligible",
            "validated_attempt_paths": list(passing_paths),
        }
        # Validate the immutable attempt files, not only their JSONL projections.
        validated_attempt_sha256s(
            adjudication,
            task_id,
            expected_contract_sha256=contract,
        )
        original = canonical_tasks[task_id]
        reason = str(
            ((task.get("ground_truth") or {}).get("qualification") or {}).get("reason")
            or ((decisions[task_id].get("adjudication") or {}).get("reason"))
            or ""
        ).strip()
        if task.get("trajectory") != original.get("trajectory"):
            adjudication["corrected_trajectory"] = deepcopy(task["trajectory"])
        validated_builder = (task.get("ground_truth") or {}).get("response_builder")
        original_builder = (original.get("ground_truth") or {}).get("response_builder")
        if validated_builder != original_builder:
            adjudication["response_builder"] = deepcopy(validated_builder)
        if (
            adjudication.get("corrected_trajectory") is not None
            or adjudication.get("response_builder") is not None
            or decisions[task_id].get("disposition")
            not in {"eligible", "needs_evidence_revalidation"}
        ):
            if not reason:
                raise ValueError(f"{task_id}: repaired validated pass lacks reason")
            adjudication["reason"] = reason
        decisions[task_id]["adjudication"] = adjudication
        overlaid.add(task_id)
    return overlaid


def finalize_adjudication(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    validated_runs: list[tuple[dict[str, Any], Path]],
    invalidations: dict[str, str],
) -> dict[str, Any]:
    result = deepcopy(qualification)
    canonical_tasks = _qualification_rows(suite.get("tasks"), "id", "canonical suite")
    decisions = _qualification_rows(result.get("tasks"), "task_id", "qualification")
    if set(decisions) != set(canonical_tasks):
        raise ValueError("qualification task set does not match canonical suite")
    unknown_invalidations = set(invalidations) - set(decisions)
    if unknown_invalidations:
        raise ValueError(f"invalidations contain unknown tasks: {sorted(unknown_invalidations)}")
    # Preserve prior repair reasons while overlaying, but remember which
    # eligibility decisions must be replaced by fresh two-pass evidence.
    previous_eligible = {
        task_id
        for task_id, decision in decisions.items()
        if (decision.get("adjudication") or {}).get("status") == "eligible"
    }
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for validated_suite, attempts_path in validated_runs:
        _collect_validated_run(
            canonical_tasks,
            decisions,
            validated_suite,
            attempts_path,
            candidates,
        )
    overlaid = _overlay_two_pass_candidates(canonical_tasks, decisions, candidates)
    # A partial repair-validation batch is an incremental overlay. Only a task
    # that was actually present in fresh evidence may replace its prior
    # eligibility; otherwise an unrelated small batch would silently erase all
    # previously qualified tasks. A complete revalidation still has replacement
    # semantics because every task is present in ``candidates``.
    observed_task_ids = {task_id for task_id, _contract in candidates}
    for task_id in (previous_eligible & observed_task_ids) - overlaid:
        decisions[task_id]["adjudication"] = None
    # An explicit invalidation records later evidence and therefore wins over
    # every provisional or independently reproduced pass.
    for task_id, reason in invalidations.items():
        if not str(reason).strip():
            raise ValueError(f"{task_id}: invalidation reason is empty")
        decisions[task_id]["adjudication"] = {
            "status": "skipped",
            "reason": str(reason).strip(),
        }
    for decision in decisions.values():
        adjudication = decision.get("adjudication")
        if not isinstance(adjudication, dict) or adjudication.get("status") not in {
            "eligible",
            "skipped",
        }:
            decision["adjudication"] = {
                "status": "skipped",
                "reason": _default_skip_reason(decision),
            }
    result["tasks"] = [decisions[str(task["id"])] for task in suite["tasks"]]
    result["schema"] = QUALIFICATION_SCHEMA
    return result


def main_finalize_adjudication(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Finalize complete two-pass qualification decisions",
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument(
        "--qualification",
        type=Path,
        help="prior private adjudication; omit to rehydrate from an already applied suite",
    )
    parser.add_argument(
        "--validated-run",
        action="append",
        default=[],
        metavar="SUITE::ATTEMPTS",
    )
    parser.add_argument("--invalidations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    runs: list[tuple[dict[str, Any], Path]] = []
    for value in args.validated_run:
        parts = value.split("::", 1)
        if len(parts) != 2:
            raise ValueError("validated run must be SUITE::ATTEMPTS")
        suite_path, attempts_path = map(Path, parts)
        load_suite(suite_path)
        runs.append(
            (
                json.loads(suite_path.read_text(encoding="utf-8")),
                attempts_path,
            )
        )
    invalidations = (
        json.loads(args.invalidations.read_text(encoding="utf-8")) if args.invalidations else {}
    )
    if not isinstance(invalidations, dict):
        raise ValueError("invalidations must be a task-to-reason object")
    load_suite(args.suite)
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    qualification = (
        json.loads(args.qualification.read_text(encoding="utf-8"))
        if args.qualification
        else qualification_from_applied_suite(suite)
    )
    payload = finalize_adjudication(
        suite,
        qualification,
        runs,
        {str(key): str(value) for key, value in invalidations.items()},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_immutable(args.output, payload)


def _main_adjudicated_suite(argv, *, evidence_only: bool) -> None:
    parser = argparse.ArgumentParser(description="Build a pending qualification validation suite")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    builder = build_evidence_revalidation_suite if evidence_only else build_repair_validation_suite
    result = builder(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
    )
    _write_qualified_suite(args.output, result)


def main_repair_validation_suite(argv=None) -> None:
    _main_adjudicated_suite(argv, evidence_only=False)


def main_evidence_revalidation_suite(argv=None) -> None:
    _main_adjudicated_suite(argv, evidence_only=True)


def main_apply_adjudication(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Apply complete or selected qualification decisions",
    )
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--task-id", action="append", dest="task_ids")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = apply_adjudication(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        task_ids=set(args.task_ids) if args.task_ids else None,
    )
    _write_qualified_suite(args.output, result)


def main_repair_queue(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build the complete two-pass correction queue")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, required=True, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = build_repair_queue(load_suite(args.suite), args.attempts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_immutable(args.output, payload)


def build_observation_suite(
    suite: dict[str, Any], task_ids: list[str], *, output_name: str = "repair_page"
) -> dict[str, Any]:
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("task ids must be a non-empty unique list")
    if not isinstance(output_name, str) or not output_name.strip():
        raise ValueError("observation output name must be non-empty")
    selected = []
    by_id = _qualification_rows(suite.get("tasks"), "id", "source suite")
    missing = [task_id for task_id in task_ids if task_id not in by_id]
    if missing:
        raise ValueError(f"unknown task ids: {missing}")
    for task_id in task_ids:
        task = deepcopy(by_id[task_id])
        if any(action.get("save_as") == output_name for action in task["trajectory"]["actions"]):
            raise ValueError("observation output already exists in trajectory: " + output_name)
        task["trajectory"]["actions"].append(
            {
                "action": "snapshot",
                "selector": "body",
                "save_as": output_name,
                "max_chars": 40_000,
            }
        )
        task["trajectory"]["provenance"] = {
            **(task["trajectory"].get("provenance") or {}),
            "kind": "codex_response_observation_probe",
            "non_canonical": True,
        }
        task["ground_truth"]["expected_actions"] = [
            action["action"] for action in task["trajectory"]["actions"]
        ]
        task["ground_truth"]["response_builder"] = {
            "kind": "observed_shape",
            "task_type": "retrieve",
            "shape": [{"$output": output_name}],
        }
        task["ground_truth"]["qualification"] = {"status": "pending"}
        selected.append(task)
    result = deepcopy(suite)
    result["tasks"] = selected
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "response_observation_probe": {
            "non_canonical": True,
            "task_count": len(selected),
            "output_name": output_name,
            "affects_qualification": False,
        },
    }
    return result


def main_observation_suite(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--task-ids", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
    result = build_observation_suite(
        json.loads(args.suite.read_text(encoding="utf-8")), task_ids
    )
    _write_qualified_suite(args.output, result)


def build_authoring_suite(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    *,
    site: str | None = None,
    target_timeout_ms: int = 5_000,
    task_ids: set[str] | None = None,
    task_types: set[str] | None = None,
    source_dispositions: set[str] | None = None,
) -> dict[str, Any]:
    if type(target_timeout_ms) is not int or target_timeout_ms < 1:
        raise ValueError("authoring target timeout must be positive")
    allowed_task_types = {"retrieve"} if task_types is None else task_types
    if not allowed_task_types or not allowed_task_types <= {"retrieve", "navigate"}:
        raise ValueError("authoring task types must be retrieve and/or navigate")
    allowed_dispositions = ({"needs_trace_repair"} if source_dispositions is None
                            else source_dispositions)
    if not allowed_dispositions or not allowed_dispositions <= {
        "needs_trace_repair",
        "needs_response_annotation",
    }:
        raise ValueError("unsupported authoring source disposition")
    qualification_rows = _qualification_rows(
        qualification.get("tasks"), "task_id", "qualification")
    dispositions = {identifier: row.get("disposition")
                    for identifier, row in qualification_rows.items()}
    selected_ids = []
    for task in suite.get("tasks") or []:
        task_id = str(task.get("id"))
        if dispositions.get(task_id) not in allowed_dispositions:
            continue
        if not allowed_task_types.intersection(task.get("tags") or []):
            continue
        sites = (task.get("environment") or {}).get("sites") or []
        if site is not None and site not in sites:
            continue
        if task_ids is not None and task_id not in task_ids:
            continue
        selected_ids.append(task_id)
    if task_ids is not None and set(selected_ids) != task_ids:
        missing = sorted(task_ids.difference(selected_ids))
        raise ValueError(f"requested authoring tasks are outside the eligible scope: {missing}")
    if not selected_ids:
        raise ValueError("no non-mutating trace repairs match the requested authoring scope")

    result = build_observation_suite(suite, selected_ids, output_name="authoring_page")
    source_by_id = {str(task.get("id")): task for task in suite.get("tasks") or []}
    for task in result["tasks"]:
        for action in task["trajectory"]["actions"]:
            if action.get("selector") is not None or isinstance(action.get("target"), dict):
                action.setdefault("timeout_ms", target_timeout_ms)
            if isinstance(action.get("target"), dict):
                action.setdefault("target_timeout_ms", target_timeout_ms)
        if "navigate" in (task.get("tags") or []):
            source_builder = (
                source_by_id[task["id"]].get("ground_truth") or {}
            ).get("response_builder")
            task["ground_truth"]["response_builder"] = deepcopy(
                source_builder
                or {
                    "kind": "null_after_verified_path",
                    "task_type": "navigate",
                }
            )
    result["benchmark"] = {
        **(result.get("benchmark") or {}),
        "trajectory_authoring": {
            "non_canonical": True,
            "diagnostic_only": True,
            "source_dispositions": sorted(allowed_dispositions),
            "task_types": sorted(allowed_task_types),
            "site": site,
            "task_count": len(selected_ids),
            "diagnostic_target_timeout_ms": target_timeout_ms,
            "qualification_evidence": False,
        },
    }
    return result


def main_authoring_suite(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--site")
    parser.add_argument("--target-timeout-ms", type=int, default=5_000)
    parser.add_argument("--task-ids")
    parser.add_argument("--task-types", default="retrieve")
    parser.add_argument("--source-dispositions", default="needs_trace_repair")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_authoring_suite(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        site=args.site,
        target_timeout_ms=args.target_timeout_ms,
        task_ids=(
            {item.strip() for item in args.task_ids.split(",") if item.strip()}
            if args.task_ids is not None
            else None
        ),
        task_types={
            item.strip() for item in args.task_types.split(",") if item.strip()
        },
        source_dispositions={
            item.strip()
            for item in args.source_dispositions.split(",")
            if item.strip()
        },
    )
    _write_qualified_suite(args.output, result)


def propose_replace_target(
    suite: dict[str, Any],
    task_id: str,
    *,
    action_index: int,
    target: dict[str, Any],
    allow_many: bool = False,
    reason: str,
) -> dict[str, Any]:
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    task = matches[0]
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if action_index < 1 or action_index > len(actions):
        raise ValueError("action index is outside the trajectory")
    action = actions[action_index - 1]
    if action.get("action") not in {"click", "double_click", "press"}:
        raise ValueError("target replacement requires a targetable pointer or key action")
    if not reason.strip():
        raise ValueError("target replacement requires a reason")
    validate_target({"target": target})
    action.pop("selector", None)
    action["target"] = deepcopy(target)
    if allow_many:
        action["allow_many"] = True
    else:
        action.pop("allow_many", None)
    response_builder = (task.get("ground_truth") or {}).get("response_builder")
    if not isinstance(response_builder, dict):
        raise ValueError("target replacement requires an existing response builder")
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(response_builder),
        "reason": reason.strip(),
    }

def propose_capture_target(
    suite: dict[str, Any],
    task_id: str,
    *,
    action_index: int,
    output_name: str,
    regex: str | None = None,
) -> dict[str, Any]:
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    task = matches[0]
    if "retrieve" not in (task.get("tags") or []):
        raise ValueError("capture-target repair requires a retrieve task")
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if action_index < 1 or action_index > len(actions):
        raise ValueError("action index is outside the trajectory")
    clicked = actions[action_index - 1]
    if clicked.get("action") != "click" or not isinstance(clicked.get("target"), dict):
        raise ValueError("selected action must click a structured target")
    if not output_name.strip():
        raise ValueError("output name must be non-empty")
    validate_target({"target": clicked["target"]})
    if any(action.get("save_as") == output_name for action in actions):
        raise ValueError("output name already exists in the trajectory")
    capture = {
        "action": "get_text",
        "target": deepcopy(clicked["target"]),
        "save_as": output_name,
        "strip": True,
    }
    if "tab_index" in clicked:
        capture["tab_index"] = clicked["tab_index"]
    actions.insert(action_index - 1, capture)
    output_reference: dict[str, Any] = {"$output": output_name}
    if regex is not None:
        if not regex:
            raise ValueError("regex must be non-empty when provided")
        if re.compile(regex).groups < 1:
            raise ValueError("capture regex requires a capture group")
        output_reference.update({"regex": regex, "group": 1})
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": {
            "kind": "observed_shape",
            "task_type": "retrieve",
            "shape": [output_reference],
        },
        "reason": (
            f"Capture the visible text of recorded click target at step {action_index} before "
            "navigation; the official trajectory already identifies the answer-bearing element."
        ),
    }

def propose_autowait_repair(
    suite: dict[str, Any], qualification: dict[str, Any], task_id: str
) -> dict[str, Any]:
    task_matches = [task for task in suite.get("tasks") or [] if task.get("id") == task_id]
    decision_matches = [
        item for item in qualification.get("tasks") or [] if item.get("task_id") == task_id
    ]
    if len(task_matches) != 1 or len(decision_matches) != 1:
        raise ValueError(f"task and qualification must uniquely contain {task_id}")
    task = task_matches[0]
    decision = decision_matches[0]
    if decision.get("task_contract_sha256") != task_contract_sha256(task):
        raise ValueError(f"{task_id}: qualification task contract changed or is unbound; "
                         "regenerate the qualification queue")
    failed = decision.get("failed_step") or {}
    error = str(failed.get("error") or "")
    index = int(failed.get("index") or 0)
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if (
        decision.get("disposition") != "needs_trace_repair"
        or index < 1
        or index > len(actions)
        or actions[index - 1].get("action") != failed.get("action")
    ):
        raise ValueError(f"{task_id}: evidence does not prove a supported auto-wait timeout")
    response_builder = (task.get("ground_truth") or {}).get("response_builder")
    if not isinstance(response_builder, dict):
        raise ValueError(f"{task_id}: an existing response builder is required")
    if (
        failed.get("action") == "click"
        and "click action done" in error
        and "waiting for scheduled navigations to finish" in error
    ):
        actions[index - 1]["accept_completed_action_navigation_timeout"] = True
        repair_reason = (
            "accept only this proven post-action timeout and retain the full wait so the request "
            "remains observable to the official evaluator"
        )
    elif (
        failed.get("action") == "press"
        and actions[index - 1].get("key") == "Enter"
        and 'elementHandle.press("Enter")' in error
        and index < len(actions)
        and isinstance(actions[index].get("target"), dict)
    ):
        actions[index - 1]["no_wait_after"] = True
        repair_reason = (
            "disable only the Enter key's implicit navigation wait; the next declared structured "
            "target remains the readiness and path gate"
        )
    else:
        raise ValueError(f"{task_id}: evidence does not prove a supported auto-wait timeout")
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(response_builder),
        "reason": (
            f"Step {index} timed out in Playwright's implicit post-action wait; {repair_reason}."
        ),
    }

def propose_action_repair(
    suite: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    if config.get("schema") != "lex_browser_use_action_repair/1":
        raise ValueError("action repair config schema is invalid")
    task_id = str(config.get("task_id") or "")
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    reason = str(config.get("reason") or "").strip()
    if not reason:
        raise ValueError("action repair requires a reason")
    edits = config.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("action repair requires edits")

    task = matches[0]
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    offset = 0
    audit: list[dict[str, Any]] = []
    prior_end = 0
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("every action repair edit must be an object")
        start = int(edit.get("start_index") or 0)
        expected = edit.get("expected_actions")
        expected_sha256 = str(edit.get("expected_actions_sha256") or "")
        delete_count = int(edit.get("delete_count") or 0)
        replacement = edit.get("replacement_actions")
        if start < 1 or start <= prior_end:
            raise ValueError("action repair edits must be ordered and non-overlapping")
        if expected is not None and expected_sha256:
            raise ValueError("use expected_actions or expected_actions_sha256, not both")
        if expected is None:
            if len(expected_sha256) != 64 or delete_count < 1:
                raise ValueError(
                    "hashed action repair requires expected_actions_sha256 and delete_count"
                )
        elif not isinstance(expected, list) or not expected:
            raise ValueError("action repair edit requires non-empty expected_actions")
        if not isinstance(replacement, list):
            raise ValueError("action repair edit requires replacement_actions")
        source_start = start - 1
        original_actions = (task.get("trajectory") or {}).get("actions") or []
        expected_count = len(expected) if isinstance(expected, list) else delete_count
        source_end = source_start + expected_count
        source_slice = original_actions[source_start:source_end]
        if isinstance(expected, list):
            source_matches = source_slice == expected
        else:
            source_matches = (
                len(source_slice) == expected_count
                and _proposal_digest(source_slice) == expected_sha256
            )
        if not source_matches:
            raise ValueError(f"{task_id}: source actions changed at index {start}")
        for action in replacement:
            if not isinstance(action, dict) or not str(action.get("action") or ""):
                raise ValueError("replacement action is invalid")
            validate_selector(action)
        current_start = source_start + offset
        current_end = current_start + expected_count
        actions[current_start:current_end] = deepcopy(replacement)
        offset += len(replacement) - expected_count
        prior_end = source_end
        audit.append(
            {
                "start_index": start,
                "removed_count": expected_count,
                "inserted_count": len(replacement),
                "before_sha256": _proposal_digest(source_slice),
                "after_sha256": _proposal_digest(replacement),
            }
        )
    if not actions:
        raise ValueError("action repair cannot remove the complete trajectory")
    response_builder = (task.get("ground_truth") or {}).get("response_builder")
    if not isinstance(response_builder, dict):
        raise ValueError(f"{task_id}: existing response builder is required")
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(response_builder),
        "reason": reason,
        "repair_audit": audit,
    }

FIELD_TOKENS = {
    "title": ("title",),
    "body": ("body",),
    "comment": ("comment",),
    "url": ("url",),
    "email": ("email", "newsletter"),
}

REPAIRABLE_CONFLICT = re.compile(r"^evaluator_([a-z]+)_value_not_filled$")

def _field_for_key(key: str) -> str | None:
    name = key.casefold()
    return next(
        (
            field
            for field in FIELD_TOKENS
            if name == field or name.endswith(f"[{field}]")
        ),
        None,
    )

def _expected_values(task: dict[str, Any]) -> dict[str, str]:
    values: dict[str, set[str]] = {}
    for evaluator in (task.get("source") or {}).get("verified_evaluators") or []:
        if evaluator.get("evaluator") != "NetworkEventEvaluator":
            continue
        post_data = (evaluator.get("expected") or {}).get("post_data") or {}
        if not isinstance(post_data, dict):
            continue
        for key, value in post_data.items():
            field = _field_for_key(str(key))
            if field is not None and isinstance(value, str) and value.strip():
                values.setdefault(field, set()).add(value)
    ambiguous = sorted(field for field, candidates in values.items() if len(candidates) != 1)
    if ambiguous:
        raise ValueError(f"ambiguous evaluator values for fields: {ambiguous}")
    return {field: next(iter(candidates)) for field, candidates in values.items()}

def propose_evaluator_fill_repair(
    suite: dict[str, Any], task_id: str
) -> dict[str, Any]:
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    task = matches[0]
    conflicts = trajectory_semantic_conflicts(task)
    fields: list[str] = []
    unsupported: list[str] = []
    for conflict in conflicts:
        match = REPAIRABLE_CONFLICT.fullmatch(conflict)
        if match is None:
            unsupported.append(conflict)
        else:
            fields.append(match.group(1))
    if unsupported:
        raise ValueError(f"{task_id}: repair requires unsupported judgment: {unsupported}")
    if not fields:
        raise ValueError(f"{task_id}: no exact evaluator fill mismatch to repair")

    expected = _expected_values(task)
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    repaired: list[dict[str, Any]] = []
    for field in fields:
        if field not in expected:
            raise ValueError(f"{task_id}: evaluator value for {field} is unavailable")
        owned = [
            index
            for index, action in enumerate(actions)
            if action.get("action") == "fill"
            and any(
                token
                in json.dumps(action.get("target"), ensure_ascii=False, sort_keys=True).casefold()
                for token in FIELD_TOKENS[field]
            )
        ]
        if not owned:
            raise ValueError(f"{task_id}: no explicit {field} fill action")
        index = owned[-1]
        previous = str(actions[index].get("text") or "")
        actions[index]["text"] = expected[field]
        repaired.append(
            {
                "field": field,
                "action_index": index + 1,
                "previous": previous,
                "replacement": expected[field],
            }
        )

    candidate = deepcopy(task)
    candidate["trajectory"]["actions"] = actions
    remaining = trajectory_semantic_conflicts(candidate)
    if remaining:
        raise ValueError(f"{task_id}: proposed repair leaves semantic conflicts: {remaining}")
    response_builder = (task.get("ground_truth") or {}).get("response_builder")
    if not isinstance(response_builder, dict):
        raise ValueError(f"{task_id}: existing response builder is required")
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(response_builder),
        "reason": (
            "Replace only the final explicit fills whose values contradict the exact official "
            "NetworkEventEvaluator payload; field ownership and all other actions are unchanged."
        ),
        "repair_audit": repaired,
    }

OBSERVATION_ACTIONS = frozenset(
    {
        "get_text",
        "get_all_text",
        "get_table_column",
        "get_attribute",
        "get_all_attribute",
        "get_property",
        "snapshot",
    }
)

def _builder_outputs(builder: dict[str, Any]) -> set[str]:
    kind = builder.get("kind")
    if builder.get("task_type") != "retrieve":
        raise ValueError("observed repair requires retrieve task_type")
    if kind == "observed_json":
        names = {str(builder.get("output") or "")}
    elif kind == "observed_json_zip":
        fields = builder.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("observed_json_zip requires fields")
        names = {str(value) for value in fields.values()}
    elif kind == "observed_json_filter_zip":
        fields = builder.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("observed_json_filter_zip requires fields")
        names = {str(value) for value in fields.values()}
        names.add(str(builder.get("filter_output") or ""))
        if not isinstance(builder.get("filter_regex"), str) or not builder["filter_regex"]:
            raise ValueError("observed_json_filter_zip requires filter_regex")
    elif kind == "observed_ranked_labels":
        names = {
            str(builder.get("labels_output") or ""),
            str(builder.get("scores_output") or ""),
        }
        if int(builder.get("limit") or 0) < 1:
            raise ValueError("observed_ranked_labels requires positive limit")
    elif kind == "observed_review_authors_matching":
        raw_names = builder.get("outputs")
        if raw_names is None:
            raw_names = [builder.get("output")]
        if not isinstance(raw_names, list) or not raw_names:
            raise ValueError(f"{kind} requires output or outputs")
        names = {str(value or "") for value in raw_names}
        for key in ("match_regex", "author_regex"):
            if not isinstance(builder.get(key), str) or not builder[key]:
                raise ValueError(f"{kind} requires {key}")
    else:
        raise ValueError("observed repair builder kind is not allowed")
    if not names or "" in names:
        raise ValueError("observed repair builder requires output names")
    return names

def propose_observed_repair(
    suite: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    task_id = str(config.get("task_id") or "")
    matches = [task for task in suite.get("tasks") or [] if str(task.get("id")) == task_id]
    if len(matches) != 1:
        raise ValueError(f"suite must uniquely contain {task_id}")
    observations = config.get("observation_actions")
    if not isinstance(observations, list) or not observations:
        raise ValueError("observed repair requires observation_actions")
    declared_outputs: set[str] = set()
    validated: list[tuple[int | None, dict[str, Any]]] = []
    for raw in observations:
        if not isinstance(raw, dict) or raw.get("action") not in OBSERVATION_ACTIONS:
            raise ValueError("observed repair may append only read-only observation actions")
        action = deepcopy(raw)
        raw_after = action.pop("after_action_index", None)
        after_action_index = int(raw_after) if raw_after is not None else None
        save_as = str(action.get("save_as") or "")
        if not save_as or save_as in declared_outputs:
            raise ValueError("observation save_as values must be non-empty and unique")
        validate_selector(action)
        declared_outputs.add(save_as)
        validated.append((after_action_index, action))
    builder = config.get("response_builder")
    if not isinstance(builder, dict):
        raise ValueError("observed repair requires response_builder")
    referenced_outputs = _builder_outputs(builder)
    if referenced_outputs != declared_outputs:
        raise ValueError("response builder outputs must exactly match appended observations")
    reason = str(config.get("reason") or "").strip()
    if not reason:
        raise ValueError("observed repair requires reason")
    task = matches[0]
    actions = deepcopy((task.get("trajectory") or {}).get("actions") or [])
    if declared_outputs.intersection(action.get("save_as") for action in actions):
        raise ValueError("observation output name already exists in the trajectory")
    official_action_count = len(actions)
    for position, (after_action_index, action) in enumerate(validated):
        if after_action_index is None:
            actions.append(action)
            continue
        if after_action_index < 0 or after_action_index > official_action_count:
            raise ValueError("after_action_index is outside the official trajectory")
        prior_insertions = sum(
            1
            for prior_index, _ in validated[:position]
            if prior_index is not None and prior_index <= after_action_index
        )
        actions.insert(after_action_index + prior_insertions, action)
    return {
        "schema": "lex_browser_use_repair_proposal/1",
        "task_id": task_id,
        "replace_actions": actions,
        "response_builder": deepcopy(builder),
        "reason": reason,
    }


def main_repair_proposal(argv=None, *, kind: str) -> None:
    """Generate pending proposals with shared input and publication boundaries."""
    config_kinds = {"action_repair", "observed_repair"}
    batch_kinds = {"autowait_repairs", "evaluator_fill_repair"}
    supported = config_kinds | batch_kinds | {"capture_target", "replace_target"}
    if kind not in supported:
        raise ValueError("unknown repair proposal kind")
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    if kind in config_kinds:
        parser.add_argument("--config", type=Path, required=True)
    elif kind in batch_kinds:
        parser.add_argument("--task-ids", required=True)
        if kind == "autowait_repairs":
            parser.add_argument("--qualification", type=Path, required=True)
    else:
        parser.add_argument("--task-id", required=True)
        parser.add_argument("--action-index", type=int, required=True)
        if kind == "capture_target":
            parser.add_argument("--output-name", required=True)
            parser.add_argument("--regex")
        else:
            parser.add_argument("--target-json", required=True)
            parser.add_argument("--allow-many", action="store_true")
            parser.add_argument("--reason", required=True)
    parser.add_argument("--output-dir" if kind in batch_kinds else "--output",
                        type=Path, required=True)
    args = parser.parse_args(argv)
    inputs = {}
    payloads = {}
    for name in ("suite", "config", "qualification"):
        if hasattr(args, name):
            path = unlinked_path(getattr(args, name))
            content = path.read_bytes()
            inputs[name] = (path, content)
            payloads[name] = json.loads(content)
    output = unlinked_path(args.output_dir if kind in batch_kinds else args.output)
    if output.exists() or output.is_symlink():
        raise ValueError("proposal output already exists")
    if any(output == path or path.is_relative_to(output) for path, _ in inputs.values()):
        raise ValueError("proposal output overlaps an input")
    suite = payloads["suite"]
    if kind in batch_kinds:
        task_ids = [item.strip() for item in args.task_ids.split(",") if item.strip()]
        if not task_ids or len(set(task_ids)) != len(task_ids):
            raise ValueError("task IDs must be non-empty and unique")
        for task_id in task_ids:
            safe_identifier(task_id)
        proposals = [
            propose_autowait_repair(suite, payloads["qualification"], task_id)
            if kind == "autowait_repairs" else propose_evaluator_fill_repair(suite, task_id)
            for task_id in task_ids
        ]
    elif kind in config_kinds:
        proposals = [(propose_action_repair if kind == "action_repair"
                      else propose_observed_repair)(suite, payloads["config"])]
    elif kind == "capture_target":
        proposals = [propose_capture_target(
            suite, args.task_id, action_index=args.action_index,
            output_name=args.output_name, regex=args.regex)]
    else:
        target = json.loads(args.target_json)
        if not isinstance(target, dict):
            raise ValueError("target JSON must be an object")
        proposals = [propose_replace_target(
            suite, args.task_id, action_index=args.action_index, target=target,
            allow_many=args.allow_many, reason=args.reason)]
    for proposal in proposals:
        task = next(task for task in suite["tasks"] if str(task["id"]) == proposal["task_id"])
        proposal["source_trajectory_sha256"] = _json_sha256(task["trajectory"])
        proposal["source_task_contract_sha256"] = task_contract_sha256(task)
        proposal["input_sha256"] = {
            name: hashlib.sha256(content).hexdigest()
            for name, (_, content) in inputs.items()
        }
        build_probe(suite, proposal)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".repair-proposals-", dir=output.parent) as work:
        staging = Path(work) / "proposals"
        staging.mkdir()
        for proposal in proposals:
            filename = f'{proposal["task_id"]}.json' if kind in batch_kinds else "proposal.json"
            _write_immutable(staging / filename, proposal)
        for path, content in inputs.values():
            if unlinked_path(path).read_bytes() != content:
                raise ValueError("proposal input changed during generation")
        unlinked_path(output)
        if output.exists() or output.is_symlink():
            raise ValueError("proposal output appeared during generation")
        if kind in batch_kinds:
            staging.rename(output)
        else:
            os.link(staging / "proposal.json", output)


MAX_TASKS_PER_QUALIFICATION_SHARD = 12
_CURRENT_EVALUATOR_EXCLUSIONS = object()


def build_native_scope(
    suite_path: Path,
    image_lock_path: Path,
    architecture: str,
    evaluator_exclusions_path: Path | None | object = _CURRENT_EVALUATOR_EXCLUSIONS,
    *,
    max_tasks_per_qualification_shard: int = MAX_TASKS_PER_QUALIFICATION_SHARD,
) -> dict:
    if type(max_tasks_per_qualification_shard) is not int or max_tasks_per_qualification_shard < 1:
        raise ValueError("qualification shard size must be a positive integer")
    if evaluator_exclusions_path is _CURRENT_EVALUATOR_EXCLUSIONS:
        from harness.trajectory_replay.report_inputs import replay_input_path
        evaluator_exclusions_path = replay_input_path(current='human-exclusions')
    suite_path, image_lock_path = unlinked_path(suite_path), unlinked_path(image_lock_path)
    if evaluator_exclusions_path is not None:
        evaluator_exclusions_path = unlinked_path(evaluator_exclusions_path)
    paths = [suite_path, image_lock_path]
    if evaluator_exclusions_path is not None:
        paths.append(evaluator_exclusions_path)
    sources = {path: path.read_bytes() for path in paths}
    suite = json.loads(sources[suite_path])
    image_lock = json.loads(sources[image_lock_path])
    tasks = suite.get("tasks") or []
    task_ids = [safe_identifier(task["id"]) for task in tasks]
    if not task_ids or len(task_ids) != len(set(task_ids)):
        raise ValueError("native scope task IDs must be nonempty and unique")
    evaluator_exclusions: dict[str, dict] = {}
    if evaluator_exclusions_path is not None:
        payload = json.loads(sources[evaluator_exclusions_path])
        if payload.get("schema") != "lex_webarena_evaluator_exclusions/1":
            raise ValueError("WebArena evaluator exclusions schema is invalid")
        rows = payload.get("excluded_tasks") or []
        evaluator_exclusions = {str(row["task_id"]): row for row in rows}
        if len(evaluator_exclusions) != len(rows):
            raise ValueError("WebArena evaluator exclusions contain duplicate task IDs")
        suite_ids = {str(task["id"]) for task in suite["tasks"]}
        unknown = sorted(set(evaluator_exclusions).difference(suite_ids))
        if unknown:
            raise ValueError(f"WebArena evaluator exclusions contain unknown tasks: {unknown}")
        for task_id, row in evaluator_exclusions.items():
            if not str(row.get("reason") or "").strip() or not str(
                row.get("evidence_id") or ""
            ).strip():
                raise ValueError(f"WebArena evaluator exclusion {task_id} lacks evidence")
    site_architectures = {
        site: row["architecture"] for site, row in image_lock["images"].items()
    }
    if not isinstance(architecture, str) or not architecture or any(
        not isinstance(value, str) or not value for value in site_architectures.values()
    ):
        raise ValueError("native scope requires declared architectures")
    included: list[str] = []
    batch_tasks: dict[tuple[str, ...], list[str]] = defaultdict(list)
    excluded: list[dict] = []
    for task in suite["tasks"]:
        sites = [str(site) for site in task["environment"]["sites"]]
        if not sites or len(sites) != len(set(sites)) or not set(sites) <= set(site_architectures):
            raise ValueError(f"task {task['id']} has missing or duplicate site image bindings")
        incompatible = [site for site in sites if site_architectures[site] != architecture]
        evaluator_exclusion = evaluator_exclusions.get(str(task["id"]))
        if evaluator_exclusion is not None:
            excluded.append(
                {
                    "task_id": task["id"],
                    "sites": sites,
                    "incompatible_sites": [],
                    "exclusion_kind": "confirmed_upstream_evaluator_defect",
                    "reason": str(evaluator_exclusion["reason"]),
                    "evidence_id": str(evaluator_exclusion["evidence_id"]),
                }
            )
        elif incompatible:
            excluded.append(
                {
                    "task_id": task["id"],
                    "sites": sites,
                    "incompatible_sites": incompatible,
                    "exclusion_kind": "non_native_site_image",
                    "reason": (
                        f"official site image is not native {architecture}; "
                        "cross-architecture emulation is excluded"
                    ),
                }
            )
        else:
            task_id = str(task["id"])
            included.append(task_id)
            batch_tasks[tuple(sorted(sites))].append(task_id)
    if any(unlinked_path(path).read_bytes() != raw for path, raw in sources.items()):
        raise ValueError("native scope input changed during selection")
    return {
        "schema": "lex_browser_use_native_scope/1",
        "architecture": architecture,
        "selection_stage": "before_trajectory_qualification_and_browser_measurement",
        "policy": {
            "engine_independent": True,
            "native_site_images_only": True,
            "post_result_deletion_forbidden": True,
            "qualification_shard_algorithm": "stable_round_robin",
            "max_tasks_per_qualification_shard": max_tasks_per_qualification_shard,
        },
        "suite_sha256": hashlib.sha256(sources[suite_path]).hexdigest(),
        "image_lock_sha256": hashlib.sha256(sources[image_lock_path]).hexdigest(),
        "evaluator_exclusions_sha256": (
            hashlib.sha256(sources[evaluator_exclusions_path]).hexdigest()
            if evaluator_exclusions_path is not None
            else None
        ),
        "candidate_count": len(suite["tasks"]),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "included_task_ids": included,
        "qualification_batches": [
            {
                "id": "+".join(sites),
                "sites": list(sites),
                "task_count": len(task_ids),
                "shard_count": (
                    len(task_ids) + max_tasks_per_qualification_shard - 1
                )
                // max_tasks_per_qualification_shard,
                "task_ids": task_ids,
            }
            for sites, task_ids in sorted(batch_tasks.items())
        ],
        "excluded_tasks": excluded,
    }


def main_native_scope(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--image-lock",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-environment-images.json"),
    )
    parser.add_argument("--architecture", default="amd64")
    parser.add_argument(
        "--max-tasks-per-shard", type=int, default=MAX_TASKS_PER_QUALIFICATION_SHARD
    )
    parser.add_argument(
        "--evaluator-exclusions",
        type=Path,
        default=None,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from harness.trajectory_replay.report_inputs import replay_input_path
    args.output = unlinked_path(args.output)
    if args.output.exists():
        raise ValueError("native scope output already exists")
    args.suite = replay_input_path(args.suite, current='human-candidates')
    args.evaluator_exclusions = replay_input_path(
        args.evaluator_exclusions, current="human-exclusions"
    )
    payload = build_native_scope(
        args.suite,
        args.image_lock,
        args.architecture,
        args.evaluator_exclusions,
        max_tasks_per_qualification_shard=args.max_tasks_per_shard,
    )
    write_new_file(args.output, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode())
