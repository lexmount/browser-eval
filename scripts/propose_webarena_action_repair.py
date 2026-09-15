"""Build an auditable trajectory repair from exact, guarded action splices."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.public_webarena.suite import validate_selector


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
                and _sha256(source_slice) == expected_sha256
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
                "before_sha256": _sha256(source_slice),
                "after_sha256": _sha256(replacement),
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proposal = propose_action_repair(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.config.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
