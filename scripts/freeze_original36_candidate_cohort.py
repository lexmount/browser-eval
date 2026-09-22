"""Freeze all 36 original-failure candidate inputs before a remote cohort.

This freezes action inputs only. It does not promote historical diagnostic scores,
answers or observations, and it does not certify a benchmark result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.retrieval_acceptance import validate_contract

TRUSTED_SOURCE_HOSTS = {"127.0.0.1", "192.168.5.2", "host.docker.internal"}
UNREBOUND_SOURCE_HOST = re.compile(r"127\.0\.0\.1|192\.168\.5\.2|host\.docker\.internal")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _input(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("source_arguments") or row.get("arguments")
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        raise ValueError(f"trace step {row.get('index')} has no executable input")
    return value


def _bound_trace(task: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = task["action_evidence"]
    path = Path(evidence["path"])
    if path.is_symlink() or not path.is_file() or _hash(path) != evidence["sha256"]:
        raise ValueError(f"task {task['task_id']} action evidence is missing or changed")
    trace = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(trace, list):
        raise ValueError(f"task {task['task_id']} action evidence is not a list")
    return trace


def _validate_candidate_revision(candidate: dict[str, Any]) -> None:
    if "retrieval_contract" in candidate:
        validate_contract(candidate["retrieval_contract"], candidate["tool_calls"])
    elif (
        set(candidate) - {"repair"}
        != {"schema", "task_id", "canonical_calls_sha256", "tool_calls"}
        or (
            "repair" in candidate
            and (
                not isinstance(candidate["repair"], dict)
                or set(candidate["repair"]) != {"cause", "change"}
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in candidate["repair"].values()
                )
            )
        )
        or candidate["schema"] != "webarena_qualified_trajectory_revision/1"
        or candidate["canonical_calls_sha256"] != _canonical_hash(candidate["tool_calls"])
    ):
        raise ValueError("trajectory revision is unbound")


def freeze(
    index: dict[str, Any],
    *,
    site_map_path: Path,
    step_roles: dict[str, Any],
    formal: bool = False,
) -> dict[str, Any]:
    if index.get("schema") != "original36_latest_moli_diagnostic_index/7":
        raise ValueError("unsupported original-failure diagnostic index")
    if step_roles.get("schema") != "webarena_qualified_cohort_step_roles/1":
        raise ValueError("unsupported step-role contract")
    original_ids = index.get("original_failed_task_ids")
    if (
        not isinstance(original_ids, list)
        or len(original_ids) != 36
        or len(set(original_ids)) != 36
    ):
        raise ValueError("the original-failure inventory must contain exactly 36 unique tasks")
    source_tasks = index.get("tasks") or []
    by_task = {item["task_id"]: item for item in source_tasks}
    if len(source_tasks) != 36 or set(by_task) != set(original_ids):
        raise ValueError("diagnostic task inventory differs from the original failures")
    exceptions = step_roles.get("exceptions") or []
    roles = {(row["task_id"], row["trace_index"]): row for row in exceptions}
    if len(roles) != len(exceptions):
        raise ValueError("duplicate supplemental step role")
    used_roles: set[tuple[int, int | str]] = set()
    overrides = step_roles.get("candidate_overrides") or []
    overrides_by_task = {row["task_id"]: row for row in overrides}
    if len(overrides_by_task) != len(overrides):
        raise ValueError("duplicate candidate override")
    used_overrides: set[int] = set()
    sites = load_remote_sites(site_map_path)
    site_readiness = []
    seen_readiness: set[tuple[str, str]] = set()
    for probe in step_roles.get("site_readiness") or []:
        site_name = probe.get("site")
        path = probe.get("path")
        max_stable_ms = probe.get("max_stable_ms")
        if (
            site_name not in {site.value for site in sites}
            or not isinstance(path, str)
            or not path.startswith("/")
            or not isinstance(max_stable_ms, int)
            or not 0 < max_stable_ms <= 30_000
            or (site_name, path) in seen_readiness
        ):
            raise ValueError("invalid or duplicate site readiness probe")
        parsed_path = urlsplit(path)
        if parsed_path.scheme or parsed_path.netloc or parsed_path.fragment:
            raise ValueError("site readiness probe must be a site-local GET path")
        seen_readiness.add((site_name, path))
        target = urlsplit(sites[WebArenaSite(site_name)])
        site_readiness.append({
            "site": site_name,
            "path": path,
            "url": urlunsplit((target.scheme, target.netloc, parsed_path.path,
                               parsed_path.query, "")),
            "max_stable_ms": max_stable_ms,
        })
    candidates = []
    for task_id in original_ids:
        task = by_task[task_id]
        reference = task.get("reference_validity") or {}
        if reference.get("status") == "unverifiable":
            raise ValueError(f"task {task_id} has an unverifiable reference")
        if (
            task.get("evaluation_mode") != "actual_trajectory_output"
            or (task.get("semantic_verification") or {}).get("status") != "verified"
        ):
            raise ValueError(f"task {task_id} lacks verified actual trajectory output")
        trace = _bound_trace(task)
        count = task.get("qualified_action_count")
        if type(count) is not int or count <= 0:
            raise ValueError(f"task {task_id} has invalid action count")
        while roles.get((task_id, count), {}).get("role") == "diagnostic_post_observer":
            count -= 1
        if count <= 0:
            raise ValueError(f"task {task_id} has no qualified actions after observers")
        numbered: dict[int, dict[str, Any]] = {}
        readiness = []
        omitted_observers = []
        for row in trace:
            index_value = row.get("index")
            if type(index_value) is int and 1 <= index_value <= count:
                if index_value in numbered or row.get("status", row.get("tool_status")) != "success":
                    raise ValueError(f"task {task_id} has duplicate or failed action {index_value}")
                numbered[index_value] = _input(row)
                continue
            role = roles.get((task_id, index_value))
            if role is None:
                raise ValueError(f"task {task_id} has unclassified supplemental step {index_value}")
            used_roles.add((task_id, index_value))
            if role["role"] == "readiness":
                before_action = role.get("before_action")
                if (type(before_action) is not int or not 1 <= before_action <= count
                        or row.get("status") != "success"):
                    raise ValueError(f"task {task_id} has invalid readiness step")
                readiness.append({
                    "before_action": before_action,
                    "input": _input(row),
                    "input_sha256": _canonical_hash(_input(row)),
                })
            elif role["role"] == "diagnostic_post_observer":
                omitted_observers.append({
                    "trace_index": index_value,
                    "historical_status": row.get("status"),
                    "excluded_from_replay": True,
                })
            else:
                raise ValueError(f"task {task_id} has unsupported supplemental role")
        if set(numbered) != set(range(1, count + 1)):
            raise ValueError(f"task {task_id} is missing a qualified action")
        source_calls = [numbered[number] for number in range(1, count + 1)]
        source_revision = None
        override = overrides_by_task.get(task_id)
        if override is not None:
            path = Path(__file__).resolve().parents[1] / override["path"]
            if (path.is_symlink() or not path.is_file() or _hash(path) != override["sha256"]
                    or not isinstance(override.get("reason"), str) or not override["reason"]):
                raise ValueError(f"task {task_id} candidate override is unbound")
            candidate = json.loads(path.read_text(encoding="utf-8"))
            expected_count = override.get("action_count", count)
            if (
                candidate.get("task_id") != task_id
                or type(expected_count) is not int
                or expected_count <= 0
                or len(candidate.get("tool_calls") or []) != expected_count
            ):
                raise ValueError(f"task {task_id} candidate override changed the action scope")
            if expected_count != count:
                matrix_path = Path(__file__).resolve().parents[1] / (
                    "evaluation/trajectory-replay/webarena-verified/"
                    "retrieval-candidates/qualification-matrix.json"
                )
                matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
                matched = [
                    row for row in matrix["rows"] if row["task_id"] == task_id
                ]
                qualification = (
                    matched[0].get("affected_task_qualification") if len(matched) == 1 else None
                )
                paired = override.get("paired_qualification")
                qualified_candidate_sha = override.get(
                    "stabilization_parent_sha256", override["sha256"]
                )
                if task_id == 108 and qualified_candidate_sha != override["sha256"]:
                    parent = json.loads(json.dumps(candidate))
                    parent["tool_calls"][11] = {"action": "press", "key": "Tab"}
                    parent["tool_calls"][14] = {"action": "snapshot"}
                    parent["retrieval_contract"]["canonical_calls_sha256"] = _canonical_hash(
                        parent["tool_calls"]
                    )
                    parent_bytes = (json.dumps(parent, ensure_ascii=False, indent=2) + "\n").encode()
                    if (hashlib.sha256(parent_bytes).hexdigest() != qualified_candidate_sha
                            or candidate["tool_calls"][11].get("action") != "evaluate"
                            or candidate["tool_calls"][14].get("action") != "evaluate"):
                        raise ValueError("task 108 stabilization differs beyond the two UI gates")
                if (
                    not isinstance(qualification, dict)
                    or not isinstance(paired, dict)
                    or qualification.get("candidate_sha256") != qualified_candidate_sha
                    or qualification.get("action_count_per_browser") != expected_count
                    or qualification.get("retrieval_status") != "supported"
                    or qualification.get("chromium_official_score") != 1.0
                    or qualification.get("moli_official_score") != 1.0
                    or any(
                        qualification.get(key) != paired.get(key)
                        for key in ("chromium_receipt_sha256", "moli_receipt_sha256")
                    )
                ):
                    raise ValueError(f"task {task_id} action-scope override lacks paired proof")
            _validate_candidate_revision(candidate)
            if _canonical_hash(candidate["tool_calls"]) == _canonical_hash(source_calls):
                raise ValueError(f"task {task_id} candidate override has no input revision")
            source_calls = candidate["tool_calls"]
            source_revision = {
                "path": override["path"],
                "sha256": override["sha256"],
                "reason": override["reason"],
                "action_count": expected_count,
                "paired_qualification": override.get("paired_qualification"),
            }
            used_overrides.add(task_id)
        bound_calls = rebind_remote_calls(source_calls, sites, TRUSTED_SOURCE_HOSTS)
        if UNREBOUND_SOURCE_HOST.search(json.dumps(bound_calls, ensure_ascii=False)):
            raise ValueError(f"task {task_id} retains a local source host")
        for guard in readiness:
            bound_guard = rebind_remote_calls([guard["input"]], sites, TRUSTED_SOURCE_HOSTS)[0]
            if UNREBOUND_SOURCE_HOST.search(json.dumps(bound_guard, ensure_ascii=False)):
                raise ValueError(f"task {task_id} readiness retains a local source host")
            guard["source_input_sha256"] = guard.pop("input_sha256")
            guard["bound_input_sha256"] = _canonical_hash(bound_guard)
            guard["input"] = bound_guard
        candidates.append({
            "task_id": task_id,
            "source_trace_sha256": task["action_evidence"]["sha256"],
            "source_calls_sha256": _canonical_hash(source_calls),
            "source_revision": source_revision,
            "bound_calls_sha256": _canonical_hash(bound_calls),
            "actions": bound_calls,
            "readiness": readiness,
            "omitted_diagnostic_observers": omitted_observers,
        })
    if used_roles != set(roles):
        raise ValueError("step-role contract contains an unused exception")
    if used_overrides != set(overrides_by_task):
        raise ValueError("step-role contract contains an unused candidate override")
    if len(candidates) != 36 or set(item["task_id"] for item in candidates) != set(original_ids):
        raise ValueError("the original 36-task scope changed")
    return {
        "schema": (
            "original36_affected_cohort_inputs/1"
            if formal else "original36_qualified_candidate_cohort_inputs/1"
        ),
        "promotable": formal,
        "source_index_schema": index["schema"],
        "moli_binary_sha256": index["moli_binary_sha256"],
        "site_map_sha256": _hash(site_map_path),
        "original_failed_task_ids": original_ids,
        "candidate_task_ids": [item["task_id"] for item in candidates],
        "quarantined": [],
        "site_readiness": site_readiness,
        "candidates": candidates,
        "input_contract": {
            "site_preparation": "once_before_browser_cohort",
            "task_order": "original_failure_order",
            "retry_failed_task": False,
            "response_policy": "derive_from_current_run_only",
            "historical_scores_and_answers_included": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--roles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--formal", action="store_true", help="freeze the final affected-task cohort")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("cohort input manifest already exists; do not overwrite it")
    index = json.loads(args.index.read_text(encoding="utf-8"))
    roles = json.loads(args.roles.read_text(encoding="utf-8"))
    result = freeze(index, site_map_path=args.site_map, step_roles=roles, formal=args.formal)
    result["source_index_sha256"] = _hash(args.index)
    result["step_roles_sha256"] = _hash(args.roles)
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "candidate_tasks": len(result["candidates"]),
        "quarantined_tasks": result["quarantined"],
        "sha256": _hash(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
