"""Task-suite schema and validation for browser use evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "lex_browser_use_tasks/2"
SUPPORTED_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "select",
        "press",
        "get_text",
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

    @property
    def step_count(self) -> int:
        return len(self.tool_calls)

    @property
    def complexity(self) -> str:
        return complexity_tier(self.step_count)


@dataclass(frozen=True)
class TaskSuite:
    schema: str
    tasks: tuple[BrowserTask, ...]


def _validate_step(task_id: str, index: int, step: Any) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise ValueError(f"{task_id}: step {index} must be an object")
    action = str(step.get("action") or "")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"{task_id}: unsupported action {action!r} at step {index}")
    if action == "navigate" and not str(step.get("url") or "").startswith(("http://", "https://")):
        raise ValueError(f"{task_id}: navigate step {index} requires an HTTP(S) URL")
    if action in {"click", "fill", "select"} and not (
        str(step.get("selector") or "") or isinstance(step.get("ref"), int)
    ):
        raise ValueError(f"{task_id}: {action} call {index} requires selector or ref")
    if action == "get_text" and not str(step.get("selector") or ""):
        raise ValueError(f"{task_id}: get_text call {index} requires selector")
    if action in {"navigate", "snapshot", "get_text"} and not str(step.get("save_as") or ""):
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
        tasks.append(
            BrowserTask(
                task_id=task_id,
                title=title,
                tool_calls=steps,
                assertions=tuple(dict(item) for item in assertions),
                tags=tuple(str(item) for item in tags),
            )
        )
    return TaskSuite(schema=SCHEMA, tasks=tuple(tasks))
