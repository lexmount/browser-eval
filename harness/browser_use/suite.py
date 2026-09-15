"""Task-suite schema and validation for browser use evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "lex_browser_use_tasks/3"
DIFFICULTIES = frozenset({"simple", "medium", "complex"})
SUPPORTED_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "select",
        "press",
        "get_text",
        "get_attribute",
        "get_property",
        "snapshot",
        "back",
        "forward",
        "scroll",
    }
)


def complexity_tier(step_count: int) -> str:
    if step_count < 1:
        raise ValueError("a task must contain at least one step")
    lower = ((step_count - 1) // 5) * 5 + 1
    return f"step_{lower:02d}_{lower + 4:02d}"


@dataclass(frozen=True)
class BrowserTask:
    task_id: str
    title: str
    tool_calls: tuple[dict[str, Any], ...]
    assertions: tuple[dict[str, Any], ...]
    tags: tuple[str, ...]
    difficulty: str
    ground_truth: dict[str, Any]
    source: dict[str, Any]

    @property
    def step_count(self) -> int:
        return len(self.tool_calls)

    @property
    def complexity(self) -> str:
        return self.difficulty


@dataclass(frozen=True)
class TaskSuite:
    schema: str
    tasks: tuple[BrowserTask, ...]


def validate_ground_truth_evidence(
    suite_path: str | Path,
    suite: TaskSuite,
    evidence_path: str | Path,
) -> dict[str, Any]:
    """Bind a reviewed ground-truth record to the exact suite before execution."""
    path = Path(suite_path)
    evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    suite_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if evidence.get("dataset_sha256") != suite_sha256:
        raise ValueError("ground-truth evidence does not match the suite sha256")
    expected_by_task = {task.task_id: task.ground_truth["expected_outputs"] for task in suite.tasks}
    observed_by_task = {str(row.get("task_id")): row for row in evidence.get("results") or []}
    if set(observed_by_task) != set(expected_by_task):
        raise ValueError("ground-truth evidence task set does not match the suite")
    for task_id, expected in expected_by_task.items():
        row = observed_by_task[task_id]
        if row.get("status") != "pass" or row.get("observed") != expected:
            raise ValueError(f"ground-truth evidence mismatch: {task_id}")
    replay = evidence.get("automatic_browser_replay") or {}
    if replay.get("status") != "pass" or replay.get("passed") != len(suite.tasks):
        raise ValueError("ground-truth browser replay is incomplete")
    return evidence


def validate_selector(step: dict[str, Any]) -> None:
    """The deterministic runner uses explicit selectors, not snapshot references."""
    action = step.get("action")
    if action in {"click", "fill", "select", "get_text", "get_attribute", "get_property"}:
        selector = step.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError(f"{action} requires a non-empty selector; ref is not supported")


def _validate_step(task_id: str, index: int, step: Any) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise ValueError(f"{task_id}: step {index} must be an object")
    action = str(step.get("action") or "")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"{task_id}: unsupported action {action!r} at step {index}")
    if action == "navigate" and not str(step.get("url") or "").startswith(
        ("http://", "https://", "{fixture_origin}/")
    ):
        raise ValueError(f"{task_id}: navigate step {index} requires an HTTP(S) URL")
    try:
        validate_selector(step)
    except ValueError as exc:
        raise ValueError(f"{task_id}: call {index}: {exc}") from exc
    if action in {"get_attribute", "get_property"} and not (
        str(step.get("selector") or "") and str(step.get("name") or "")
    ):
        raise ValueError(f"{task_id}: {action} call {index} requires selector and name")
    if action in {
        "navigate",
        "snapshot",
        "get_text",
        "get_attribute",
        "get_property",
    } and not str(step.get("save_as") or ""):
        raise ValueError(f"{task_id}: {action} call {index} requires save_as")
    return dict(step)


def load_suite(path: str | Path) -> TaskSuite:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"task suite schema must be {SCHEMA!r}")
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("task suite must contain a non-empty tasks list")
    seen: set[str] = set()
    tasks: list[BrowserTask] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ValueError("each task must be an object")
        task_id = str(raw.get("id") or "").strip()
        if not task_id:
            raise ValueError("task id is required")
        if task_id in seen:
            raise ValueError(f"duplicate task id: {task_id}")
        seen.add(task_id)
        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValueError(f"{task_id}: title is required")
        raw_steps = raw.get("tool_calls")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"{task_id}: tool_calls must be a non-empty list")
        steps = tuple(
            _validate_step(task_id, index, step) for index, step in enumerate(raw_steps, start=1)
        )
        assertions = raw.get("assertions") or []
        tags = raw.get("tags") or []
        if not isinstance(assertions, list) or not isinstance(tags, list):
            raise ValueError(f"{task_id}: assertions and tags must be lists")
        difficulty = str(raw.get("difficulty") or "").strip()
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"{task_id}: difficulty must be simple, medium or complex")
        ground_truth = raw.get("ground_truth")
        if not isinstance(ground_truth, dict):
            raise ValueError(f"{task_id}: ground_truth must be an object")
        if ground_truth.get("oracle") != "deterministic_exact":
            raise ValueError(f"{task_id}: ground_truth oracle must be deterministic_exact")
        expected_outputs = ground_truth.get("expected_outputs")
        if not isinstance(expected_outputs, dict) or not expected_outputs:
            raise ValueError(f"{task_id}: ground_truth.expected_outputs is required")
        for assertion in assertions:
            if not isinstance(assertion, dict) or not isinstance(assertion.get("equals"), str):
                raise ValueError(
                    f"{task_id}: every assertion must use an exact string equals oracle"
                )
            output = str(assertion.get("output") or "")
            if expected_outputs.get(output) != assertion.get("equals"):
                raise ValueError(f"{task_id}: assertion and ground truth disagree for {output!r}")
        expected_actions = ground_truth.get("expected_actions")
        actual_actions = [step["action"] for step in steps]
        if expected_actions != actual_actions:
            raise ValueError(f"{task_id}: ground truth action sequence does not match tool_calls")
        source = raw.get("source")
        if not isinstance(source, dict) or not source.get("url"):
            raise ValueError(f"{task_id}: source attribution is required")
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
    return TaskSuite(schema=SCHEMA, tasks=tuple(tasks))
