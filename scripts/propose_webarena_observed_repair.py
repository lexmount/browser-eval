"""Propose retrieve repairs whose answers come only from declared DOM observations."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.browser_use.suite import validate_selector

OBSERVATION_ACTIONS = frozenset(
    {
        "get_text",
        "get_all_text",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proposal = propose_observed_repair(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.config.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
