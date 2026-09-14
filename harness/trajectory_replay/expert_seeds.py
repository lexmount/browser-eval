"""Merge explicitly reviewed expert repairs into Browser Use replay seeds."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

BASE_SCHEMA = "lex_browser_use_replay_seeds/1"
REPAIR_SCHEMA = "lex_webarena_expert_seed_repairs/1"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _contains_ref(value: Any) -> bool:
    if isinstance(value, dict):
        return "ref" in value or any(_contains_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in value)
    return False


def _validate_tool_calls(task_id: int, tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list) or not tool_calls:
        raise ValueError(f"expert repair for task {task_id} must contain tool_calls")
    validated: list[dict[str, Any]] = []
    for index, raw_call in enumerate(tool_calls, start=1):
        if not isinstance(raw_call, dict):
            raise ValueError(f"expert repair for task {task_id} call {index} must be an object")
        call = copy.deepcopy(raw_call)
        if not isinstance(call.get("action"), str) or not call["action"].strip():
            raise ValueError(f"expert repair for task {task_id} call {index} has no action")
        if _contains_ref(call):
            raise ValueError(
                f"expert repair for task {task_id} call {index} contains forbidden ref"
            )
        if call["action"] == "wait":
            seconds = call.get("seconds")
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
                raise ValueError(
                    f"expert repair for task {task_id} call {index} wait requires "
                    "a positive seconds value"
                )
            unsupported = {"timeout", "selector"} & call.keys()
            if unsupported:
                raise ValueError(
                    f"expert repair for task {task_id} call {index} wait contains "
                    f"unsupported fields: {sorted(unsupported)}"
                )
        validated.append(call)
    return validated


def _required_record(repair: dict[str, Any], task_id: int, field: str) -> Any:
    value = repair.get(field)
    if value is None or value == "" or value == {} or value == []:
        raise ValueError(f"expert repair for task {task_id} has no {field}")
    return copy.deepcopy(value)


def _refresh_counts(manifest: dict[str, Any]) -> None:
    tasks = manifest.get("tasks") or []
    counts = manifest.setdefault("counts", {})
    counts["replay_ready_tasks"] = sum(task.get("replay_status") == "ready" for task in tasks)
    counts["success_with_failed_actions_tasks"] = sum(
        task.get("replay_status") == "success_contains_failed_actions" for task in tasks
    )
    counts["all_methods_failed_tasks"] = sum(
        task.get("replay_status") == "all_methods_failed" for task in tasks
    )
    counts["expert_repair_candidates"] = sum(
        candidate.get("candidate_kind") == "expert_repair"
        for task in tasks
        for candidate in task.get("candidates") or []
    )


def merge_expert_seed_manifest(
    base_manifest: dict[str, Any],
    repair_spec: dict[str, Any],
    *,
    base_manifest_sha256: str | None = None,
    repair_spec_sha256: str | None = None,
) -> dict[str, Any]:
    """Append expert candidates without replacing an existing ready candidate."""

    if base_manifest.get("schema") != BASE_SCHEMA:
        raise ValueError("unsupported base replay seed manifest")
    if repair_spec.get("schema") != REPAIR_SCHEMA:
        raise ValueError("unsupported expert repair specification")

    merged = copy.deepcopy(base_manifest)
    tasks = {int(task["task_id"]): task for task in merged.get("tasks") or []}
    repairs = repair_spec.get("repairs")
    if not isinstance(repairs, list) or not repairs:
        raise ValueError("expert repair specification must contain repairs")

    seen_tasks: set[int] = set()
    seen_candidate_ids = {
        str(candidate.get("id"))
        for task in tasks.values()
        for candidate in task.get("candidates") or []
    }
    spec_sha256 = repair_spec_sha256 or _sha256_bytes(_canonical_bytes(repair_spec))

    for repair in repairs:
        if not isinstance(repair, dict) or "task_id" not in repair:
            raise ValueError("every expert repair must be an object with task_id")
        task_id = int(repair["task_id"])
        if task_id in seen_tasks:
            raise ValueError(f"duplicate expert repair for task {task_id}")
        seen_tasks.add(task_id)
        task = tasks.get(task_id)
        if task is None:
            raise ValueError(f"expert repair references task missing from base seeds: {task_id}")
        if task.get("replay_status") == "ready" or task.get("canonical_candidate_id"):
            raise ValueError(f"expert repair cannot replace ready task {task_id}")

        candidate_id = str(repair.get("candidate_id") or f"expert__task-{task_id}")
        if not candidate_id.strip() or candidate_id in seen_candidate_ids:
            raise ValueError(f"duplicate or empty expert candidate id: {candidate_id!r}")
        seen_candidate_ids.add(candidate_id)
        tool_calls = _validate_tool_calls(task_id, repair.get("tool_calls"))
        expected_response = _required_record(repair, task_id, "expected_response")
        if not isinstance(expected_response, dict):
            raise ValueError(
                f"expert repair for task {task_id} expected_response must be an object"
            )

        candidate = {
            "id": candidate_id,
            "candidate_kind": "expert_repair",
            "action_count": len(tool_calls),
            "actions": [str(call["action"]) for call in tool_calls],
            "tool_calls": tool_calls,
            "expected_response": expected_response,
            # The replay runner currently consumes this field as evaluator input. It is an
            # authored expectation, not a response observed from an autonomous Agent run.
            "recorded_response": copy.deepcopy(expected_response),
            "source": {
                "kind": "expert_repair_spec",
                "details": _required_record(repair, task_id, "source"),
                "repair_spec_sha256": spec_sha256,
            },
            "repair_basis": _required_record(repair, task_id, "repair_basis"),
        }
        task.setdefault("candidates", []).append(candidate)
        task["canonical_candidate_id"] = candidate_id
        task["replay_status"] = "ready"

    _refresh_counts(merged)
    merged["expert_repairs"] = {
        "schema": REPAIR_SCHEMA,
        "base_manifest_sha256": base_manifest_sha256
        or _sha256_bytes(_canonical_bytes(base_manifest)),
        "repair_spec_sha256": spec_sha256,
        "candidate_count": len(repairs),
        "policy": {
            "existing_ready_candidates_immutable": True,
            "browser_tool_refs_forbidden": True,
            "status_meaning": "ready for replay; not yet replay-verified",
        },
    }
    return merged


def build_expert_seed_manifest(
    base_manifest_path: str | Path, repair_spec_path: str | Path
) -> dict[str, Any]:
    """Read a base manifest and explicit repair spec, then return their bound merge."""

    base_path = Path(base_manifest_path).expanduser().resolve()
    repair_path = Path(repair_spec_path).expanduser().resolve()
    return merge_expert_seed_manifest(
        _read_json(base_path),
        _read_json(repair_path),
        base_manifest_sha256=_sha256(base_path),
        repair_spec_sha256=_sha256(repair_path),
    )
