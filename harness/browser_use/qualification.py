"""Pre-register and adjudicate reference trajectories before browser comparison."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .har import HAR_CREATOR_NAME, HAR_CREATOR_VERSION
from .suite import TRAJECTORY_SCHEMA, TaskSuite

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


def _validated_attempt(
    path: Path, task_id: str, *, expected_contract_sha256: str
) -> tuple[str, str, int]:
    if not path.is_file():
        raise ValueError(f"{task_id}: validated attempt evidence is missing")
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    row = json.loads(raw)
    if (
        str(row.get("task_id")) != task_id
        or row.get("engine") != "chromium"
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
        isinstance(pass_index, bool)
        or pass_index not in {1, 2}
        or row.get("qualification_required_passes") != 2
    ):
        raise ValueError(
            f"{task_id}: validated attempt must declare qualification pass 1 or 2 of 2"
        )
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
    paths = [Path(value).resolve() for value in path_values]
    if len(set(paths)) != 2:
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
            or row.get("attempt") != 1
            or row.get("qualification_required_passes") != 2
        ):
            raise ValueError("qualification evidence must contain primary Chromium attempts only")
        by_task.setdefault(str(row["task_id"]), []).append(row)
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
        semantic_conflicts = trajectory_semantic_conflicts(task_payload)
        if semantic_conflicts:
            disposition = "needs_trace_repair"
        elif not task.tool_calls:
            disposition = "needs_trace_repair"
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
            "attempts_per_task": 2,
            "purpose": "bind HAR v3, final screenshot, and final visible page state",
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
    if (
        any(
            (item.get("adjudication") or {}).get("status") == "eligible"
            for item in decisions.values()
        )
        and qualification.get("schema") != QUALIFICATION_SCHEMA
    ):
        raise ValueError(f"eligible tasks require qualification schema {QUALIFICATION_SCHEMA}")
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
