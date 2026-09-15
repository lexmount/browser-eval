"""Propose exact field-value corrections grounded in official network evaluators."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.trajectory_replay.public_webarena.qualification import trajectory_semantic_conflicts

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--task-ids", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    task_ids = [value.strip() for value in args.task_ids.split(",") if value.strip()]
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("task IDs must be non-empty and unique")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for task_id in task_ids:
        proposal = propose_evaluator_fill_repair(suite, task_id)
        (args.output_dir / f"{task_id}.json").write_text(
            json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
