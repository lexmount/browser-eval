"""Curated Trajectory Replay suite loading on the shared task model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.trajectory_replay.core.suite import (
    CURATED_ACTIONS,
    CURATED_SAVED_OUTPUT_ACTIONS,
    CURATED_SCHEMA,
    BrowserTask,
    TaskSuite,
    exact_expected_outputs,
    iter_task_rows,
    parse_source,
    parse_task_labels,
    validate_action_sequence,
    validate_exact_assertions,
    validate_saved_output,
    validate_step_action,
)


def validate_selector(step: dict[str, Any]) -> None:
    """The deterministic runner uses explicit selectors, not snapshot references."""
    action = step.get("action")
    if action in {"click", "fill", "select", "get_text", "get_attribute", "get_property"}:
        selector = step.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError(f"{action} requires a non-empty selector; ref is not supported")


def _validate_step(task_id: str, index: int, step: Any) -> dict[str, Any]:
    action = validate_step_action(task_id, index, step, supported_actions=CURATED_ACTIONS)
    try:
        validate_selector(step)
    except ValueError as exc:
        raise ValueError(f"{task_id}: call {index}: {exc}") from exc
    if action in {"get_attribute", "get_property"} and not (
        str(step.get("selector") or "") and str(step.get("name") or "")
    ):
        raise ValueError(f"{task_id}: {action} call {index} requires selector and name")
    validate_saved_output(
        task_id, index, step, action=action, saved_output_actions=CURATED_SAVED_OUTPUT_ACTIONS
    )
    return dict(step)


def load_suite(path: str | Path | None = None) -> TaskSuite:
    from harness.trajectory_replay.report_inputs import replay_input_path
    path = replay_input_path(path, current='curated')
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != CURATED_SCHEMA:
        raise ValueError(f"task suite schema must be {CURATED_SCHEMA!r}")
    tasks: list[BrowserTask] = []
    for raw, task_id, title in iter_task_rows(payload):
        raw_steps = raw.get("tool_calls")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"{task_id}: tool_calls must be a non-empty list")
        steps = tuple(
            _validate_step(task_id, index, step) for index, step in enumerate(raw_steps, start=1)
        )
        assertions, tags, difficulty, ground_truth = parse_task_labels(task_id, raw)
        validate_exact_assertions(
            task_id, assertions, exact_expected_outputs(task_id, ground_truth)
        )
        validate_action_sequence(task_id, ground_truth, steps)
        source = parse_source(task_id, raw)
        tasks.append(
            BrowserTask(
                task_id=task_id,
                title=title,
                tool_calls=steps,
                assertions=tuple(dict(item) for item in assertions),
                tags=tuple(str(item) for item in tags),
                difficulty=difficulty,
                ground_truth=dict(ground_truth),
                source=dict(source),
            )
        )
    return TaskSuite(schema=CURATED_SCHEMA, tasks=tuple(tasks))
