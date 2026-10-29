"""Task-suite schema and validation for browser use evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.trajectory_replay.public_webarena.targets import validate_target
from harness.web_fetch.safety import unlinked_path, write_new_file

SCHEMA = "lex_browser_use_tasks/4"
LEGACY_SCHEMA = "lex_browser_use_tasks/3"
SUPPORTED_SCHEMAS = frozenset({LEGACY_SCHEMA, SCHEMA})
TRAJECTORY_SCHEMA = "lex_browser_trajectory/1"
DIFFICULTIES = frozenset({"simple", "medium", "complex"})
EVALUATOR_KINDS = frozenset({"exact_outputs", "webarena_verified"})
SUPPORTED_ACTIONS = frozenset(
    {
        "navigate",
        "click",
        "double_click",
        "fill",
        "select",
        "check",
        "uncheck",
        "hover",
        "press",
        "get_text",
        "get_all_text",
        "get_table_column",
        "get_attribute",
        "get_all_attribute",
        "get_property",
        "snapshot",
        "back",
        "forward",
        "scroll",
        "evaluate",
        "wait_for_url",
        "wait",
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
    evaluator: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    trajectory: dict[str, Any] | None = None

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
    """Validate the legacy selector or the portable target used by a trajectory."""
    action = step.get("action")
    targeted = {
        "click",
        "double_click",
        "fill",
        "select",
        "check",
        "uncheck",
        "hover",
        "get_text",
        "get_all_text",
        "get_table_column",
        "get_attribute",
        "get_all_attribute",
        "get_property",
    }
    if action in targeted:
        try:
            validate_target(step)
        except ValueError as exc:
            selector = step.get("selector")
            if (
                step.get("ref") is not None
                and step.get("target") is None
                and (not isinstance(selector, str) or not selector.strip())
            ):
                raise ValueError(
                    f"{action} requires a non-empty selector; ref is not supported"
                ) from exc
            raise ValueError(f"{action} {exc}") from exc


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
    if "new_page" in step and (action != "navigate" or type(step["new_page"]) is not bool):
        raise ValueError(f"{task_id}: new_page is only valid as a boolean on navigate steps")
    if action == "navigate" and step.get("new_page") and "tab_index" in step:
        raise ValueError(f"{task_id}: navigate step {index} cannot combine new_page with tab_index")
    if action == "evaluate" and not str(step.get("script") or "").strip():
        raise ValueError(f"{task_id}: evaluate call {index} requires a non-empty script")
    if action == "wait_for_url" and not str(step.get("url") or "").strip():
        raise ValueError(f"{task_id}: wait_for_url call {index} requires a URL pattern")
    if action == "wait":
        selector = step.get("selector")
        milliseconds = step.get("timeout")
        seconds = step.get("seconds")
        if selector is not None:
            if not isinstance(selector, str) or not selector.strip() or seconds is not None:
                raise ValueError(f"{task_id}: wait call {index} has an invalid selector wait")
            if step.get("wait_state", "visible") not in {
                "attached",
                "detached",
                "visible",
                "hidden",
            }:
                raise ValueError(f"{task_id}: wait call {index} has an invalid wait state")
            if milliseconds is not None and (
                type(milliseconds) not in {int, float} or milliseconds <= 0
            ):
                raise ValueError(f"{task_id}: wait call {index} requires a positive timeout")
        else:
            if (milliseconds is None) == (seconds is None):
                raise ValueError(f"{task_id}: wait call {index} requires exactly one duration")
            duration = milliseconds if milliseconds is not None else seconds
            if type(duration) not in {int, float} or duration <= 0:
                raise ValueError(f"{task_id}: wait call {index} requires a positive duration")
    try:
        validate_selector(step)
    except ValueError as exc:
        raise ValueError(f"{task_id}: call {index}: {exc}") from exc
    if action in {"get_attribute", "get_all_attribute", "get_property"} and not str(
        step.get("name") or ""
    ):
        raise ValueError(f"{task_id}: {action} call {index} requires name")
    if action == "get_table_column" and not str(step.get("header") or "").strip():
        raise ValueError(f"{task_id}: get_table_column call {index} requires header")
    if action == "get_table_column" and step.get("wait_for_value_regex") is not None:
        pattern = step["wait_for_value_regex"]
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(
                f"{task_id}: get_table_column call {index} requires a non-empty value regex"
            )
    if action in {
        "navigate",
        "snapshot",
        "get_text",
        "get_all_text",
        "get_table_column",
        "get_attribute",
        "get_all_attribute",
        "get_property",
    } and not str(step.get("save_as") or ""):
        raise ValueError(f"{task_id}: {action} call {index} requires save_as")
    return dict(step)


def load_suite(path: str | Path | None = None) -> TaskSuite:
    from harness.trajectory_replay.report_inputs import replay_input_path
    path = replay_input_path(path, current='human')
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = payload.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise ValueError(f"task suite schema must be one of {sorted(SUPPORTED_SCHEMAS)!r}")
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
        trajectory = raw.get("trajectory")
        if schema == SCHEMA:
            if not isinstance(trajectory, dict) or trajectory.get("schema") != TRAJECTORY_SCHEMA:
                raise ValueError(f"{task_id}: trajectory schema must be {TRAJECTORY_SCHEMA!r}")
            raw_steps = trajectory.get("actions")
        else:
            raw_steps = raw.get("tool_calls")
        if not isinstance(raw_steps, list):
            raise ValueError(f"{task_id}: tool_calls must be a list")
        if not raw_steps and schema == LEGACY_SCHEMA:
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
        evaluator = raw.get("evaluator")
        if schema == LEGACY_SCHEMA:
            evaluator = {"kind": "exact_outputs", "assertions": assertions}
        if not isinstance(evaluator, dict) or evaluator.get("kind") not in EVALUATOR_KINDS:
            raise ValueError(f"{task_id}: unsupported evaluator")
        if evaluator["kind"] == "exact_outputs":
            if ground_truth.get("oracle") != "deterministic_exact":
                raise ValueError(f"{task_id}: ground_truth oracle must be deterministic_exact")
            expected_outputs = ground_truth.get("expected_outputs")
            if not isinstance(expected_outputs, dict) or not expected_outputs:
                raise ValueError(f"{task_id}: ground_truth.expected_outputs is required")
            evaluator_assertions = evaluator.get("assertions", assertions)
            if not isinstance(evaluator_assertions, list):
                raise ValueError(f"{task_id}: exact evaluator assertions must be a list")
            assertions = evaluator_assertions
            for assertion in assertions:
                if not isinstance(assertion, dict) or not isinstance(assertion.get("equals"), str):
                    raise ValueError(
                        f"{task_id}: every assertion must use an exact string equals oracle"
                    )
                output = str(assertion.get("output") or "")
                if expected_outputs.get(output) != assertion.get("equals"):
                    raise ValueError(
                        f"{task_id}: assertion and ground truth disagree for {output!r}"
                    )
        else:
            if not isinstance(evaluator.get("task_id"), int):
                raise ValueError(f"{task_id}: WebArena evaluator requires integer task_id")
            if not isinstance(evaluator.get("revision"), int):
                raise ValueError(f"{task_id}: WebArena evaluator requires integer revision")
        if not steps:
            qualification = ground_truth.get("qualification") or {}
            if evaluator["kind"] != "webarena_verified" or qualification.get("status") not in {
                "pending",
                "skipped",
            }:
                raise ValueError(
                    f"{task_id}: only pending or skipped WebArena candidates may be empty"
                )
        expected_actions = ground_truth.get("expected_actions")
        actual_actions = [step["action"] for step in steps]
        if expected_actions != actual_actions:
            raise ValueError(f"{task_id}: ground truth action sequence does not match tool_calls")
        source = raw.get("source")
        if not isinstance(source, dict) or not source.get("url"):
            raise ValueError(f"{task_id}: source attribution is required")
        environment = raw.get("environment")
        if schema == LEGACY_SCHEMA:
            environment = {"kind": "public_web"}
        if not isinstance(environment, dict) or not environment.get("kind"):
            raise ValueError(f"{task_id}: environment is required")
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
                evaluator=dict(evaluator),
                environment=dict(environment),
                trajectory=dict(trajectory)
                if isinstance(trajectory, dict)
                else {
                    "schema": TRAJECTORY_SCHEMA,
                    "provenance": {"kind": "repository_curated"},
                    "actions": list(steps),
                },
            )
        )
    return TaskSuite(schema=str(schema), tasks=tuple(tasks))


_RECORDED_PORT_SITES = {
    3000: "map",
    4399: "reddit",
    7770: "shopping",
    7780: "shopping_admin",
    8023: "gitlab",
    8888: "wikipedia",
    9999: "reddit",
}


def _recorded_start_urls(row: dict[str, Any], trajectory: dict[str, Any]) -> list[str]:
    """Align configured start pages with the trace's stable tab-index order."""
    sites = [str(site) for site in row["sites"]]
    start_urls = [str(url) for url in row["start_urls"]]
    if len(sites) != len(start_urls) or len(set(sites)) != len(sites):
        raise ValueError(f"task {row['task_id']}: sites and start_urls must map one-to-one")
    recorded_sites: list[str] = []
    for url in trajectory.get("recorded_tab_urls") or []:
        try:
            port = urlparse(str(url or "")).port
        except ValueError:
            return start_urls
        site = _RECORDED_PORT_SITES.get(port or -1)
        if site is None:
            return start_urls
        recorded_sites.append(site)
    if (
        len(recorded_sites) != len(sites)
        or len(set(recorded_sites)) != len(recorded_sites)
        or set(recorded_sites) != set(sites)
    ):
        return start_urls
    by_site = dict(zip(sites, start_urls, strict=True))
    return [by_site[site] for site in recorded_sites]


def _task_type(row: dict[str, Any]) -> str:
    evaluators = row["verified_evaluators"]
    response = next(item for item in evaluators if item["evaluator"] == "AgentResponseEvaluator")
    return str(response["expected"]["task_type"])


def _difficulty(step_count: int, lower: int, upper: int) -> str:
    if step_count <= lower:
        return "simple"
    if step_count <= upper:
        return "medium"
    return "complex"


def _tertile_bounds(counts: list[int]) -> tuple[int, int]:
    ordered = sorted(counts)
    return ordered[(len(ordered) - 1) // 3], ordered[(len(ordered) - 1) * 2 // 3]


def build_suite(catalog: dict[str, Any], trace_dir: Path) -> dict[str, Any]:
    from harness.trajectory_replay.public_webarena.webarena import (
        import_playwright_trace,
        validate_trace_catalog,
    )

    validate_trace_catalog(catalog)
    imported: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in catalog["traces"]:
        trace_path = trace_dir / row["file_name"]
        if not trace_path.is_file():
            raise FileNotFoundError(f"missing trace: {trace_path}")
        imported.append((row, import_playwright_trace(trace_path, strict=True)))
    lower, upper = _tertile_bounds([len(trace["actions"]) for _, trace in imported])
    action_counts = Counter(
        str(action["action"]) for _, trajectory in imported for action in trajectory["actions"]
    )
    recovered_input_counts = Counter()
    for _, trajectory in imported:
        for action in trajectory["actions"]:
            source = action.get("source") or {}
            if source.get("recovered_manual_input_from_snapshot"):
                recovered_input_counts["manual_typing"] += 1
            if source.get("recovered_from_input_snapshot"):
                recovered_input_counts["clipboard_paste"] += 1

    tasks: list[dict[str, Any]] = []
    for row, trajectory in imported:
        actions = trajectory["actions"]
        start_urls = _recorded_start_urls(row, trajectory)
        stored_trajectory = {
            key: value for key, value in trajectory.items() if key != "recorded_tab_urls"
        }
        task_type = _task_type(row)
        ground_truth: dict[str, Any] = {
            "oracle": "webarena_verified",
            "expected_actions": [action["action"] for action in actions],
            "reference_provenance": "official_human_recording",
            "qualification": {
                "status": "pending",
                "reason": (
                    "official recorded does not imply successful; replay and evaluator "
                    "validation are required"
                ),
            },
        }
        if task_type != "retrieve":
            ground_truth["response_builder"] = {
                "kind": "null_after_verified_path",
                "task_type": task_type,
            }
        tasks.append(
            {
                "id": f"webarena-{row['task_id']}",
                "title": row["verified_intent"],
                "difficulty": _difficulty(len(actions), lower, upper),
                "tags": [
                    "public-benchmark",
                    "webarena",
                    task_type,
                    *[f"site:{site}" for site in row["sites"]],
                ],
                "trajectory": stored_trajectory,
                "assertions": [],
                "evaluator": {
                    "kind": "webarena_verified",
                    "task_id": row["task_id"],
                    "revision": row["verified_revision"],
                },
                "environment": {
                    "kind": "webarena_verified",
                    "sites": row["sites"],
                    "start_urls": start_urls,
                    "reset_required": True,
                },
                "ground_truth": ground_truth,
                "source": {
                    "kind": "public_benchmark",
                    "url": row["download_url"],
                    "benchmark_url": catalog["source"]["folder_url"],
                    "webarena_commit": catalog["source"]["webarena_commit"],
                    "webarena_verified_commit": catalog["source"]["webarena_verified_commit"],
                    "intent_template_id": row["intent_template_id"],
                    "verified_evaluators": row["verified_evaluators"],
                },
            }
        )
    return {
        "schema": "lex_browser_use_tasks/4",
        "benchmark": {
            "id": "public-webarena-human-trajectories",
            "trace_count": len(tasks),
            "selection": (
                "all official human recordings"
                if (catalog.get("scope") or {}).get("all_recorded_trajectories_included") is True
                else "declared trace catalog population"
            ),
            "step_count_tertiles": {"simple_max": lower, "medium_max": upper},
            "structural_import": {
                "action_count": sum(action_counts.values()),
                "action_counts": dict(sorted(action_counts.items())),
                "recovered_input_counts": dict(sorted(recovered_input_counts.items())),
                "unmapped_api_counts": {},
                "zero_action_task_ids": [
                    row["task_id"] for row, trajectory in imported if not trajectory["actions"]
                ],
                "replay_qualified_count": 0,
            },
            "qualification_required": True,
        },
        "tasks": tasks,
    }


def main_build_suite(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
    )
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    args.output = unlinked_path(args.output)
    if args.output.exists():
        raise ValueError("task suite output already exists")
    from harness.trajectory_replay.report_inputs import replay_input_path
    args.catalog = replay_input_path(args.catalog, current='human-traces')
    catalog_bytes = args.catalog.read_bytes()
    catalog = json.loads(catalog_bytes)
    suite = build_suite(catalog, args.trace_dir)
    if args.catalog.read_bytes() != catalog_bytes:
        raise ValueError("trace catalog changed during suite construction")
    suite["benchmark"]["catalog_sha256"] = hashlib.sha256(catalog_bytes).hexdigest()
    write_new_file(
        args.output,
        (json.dumps(suite, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
