"""Pre-registered WebArena-Verified pilot selection and validation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

PILOT_SCHEMA = "lex_webarena_verified_pilot/1"
ALLOWED_SITES = frozenset({"shopping", "shopping_admin", "reddit", "gitlab"})
TIER_COUNTS = {"easy": 15, "medium": 30, "hard": 45}
TIER_BOUNDS = {"easy": (1, 4), "medium": (5, 7), "hard": (8, None)}
TASK_EXCLUSIONS_PATH = Path(__file__).parents[2] / "evaluation/browser-use/task-exclusions.json"


@dataclass(frozen=True)
class PilotTask:
    task_id: int
    tier: str
    estimated_min_steps: int
    rationale: str
    official_hard: bool


@dataclass(frozen=True)
class PilotSelection:
    tasks: tuple[PilotTask, ...]
    source_path: Path
    sha256: str
    metadata: dict[str, Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without treating booleans as numbers."""
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return bool(left == right)


def _validate_json_schema(value: Any, schema: Any, *, path: str) -> None:
    """Validate the JSON Schema subset used by official response contracts.

    The current official dataset uses structural keywords plus ``format`` as a
    normalization hint. Unknown validation keywords fail closed so a future
    contract cannot silently bypass this preflight.
    """
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: schema must be an object")
    supported = {
        "anyOf",
        "oneOf",
        "const",
        "enum",
        "type",
        "required",
        "properties",
        "items",
        "format",
        "title",
    }
    unsupported = sorted(set(schema) - supported)
    if unsupported:
        raise ValueError(f"{path}: unsupported schema keyword {unsupported[0]!r}")
    if "format" in schema and not isinstance(schema["format"], str):
        raise ValueError(f"{path}: format must be a string")
    if "title" in schema and not isinstance(schema["title"], str):
        raise ValueError(f"{path}: title must be a string")

    for keyword in ("anyOf", "oneOf"):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        if not isinstance(branches, list) or not branches:
            raise ValueError(f"{path}: {keyword} must be a non-empty array")
        failures: list[str] = []
        matches = 0
        for branch in branches:
            try:
                _validate_json_schema(value, branch, path=path)
            except ValueError as exc:
                failures.append(str(exc))
            else:
                matches += 1
        if keyword == "anyOf" and matches == 0:
            raise ValueError(f"{path}: does not match anyOf ({'; '.join(failures)})")
        if keyword == "oneOf" and matches != 1:
            detail = f"; {'; '.join(failures)}" if failures else ""
            raise ValueError(
                f"{path}: must match exactly one oneOf branch, matched {matches}{detail}"
            )

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ValueError(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list):
            raise ValueError(f"{path}: enum must be an array")
        if not any(_json_equal(value, option) for option in options):
            raise ValueError(f"{path}: expected one of {options!r}, got {value!r}")

    declared_types = schema.get("type")
    if declared_types is not None:
        if isinstance(declared_types, str):
            type_names = [declared_types]
        elif isinstance(declared_types, list) and all(
            isinstance(item, str) for item in declared_types
        ):
            type_names = declared_types
        else:
            raise ValueError(f"{path}: type must be a string or array of strings")
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        unknown = [name for name in type_names if name not in checks]
        if unknown:
            raise ValueError(f"{path}: unsupported schema type {unknown[0]!r}")
        if not any(checks[name](value) for name in type_names):
            actual = "null" if value is None else type(value).__name__
            expected = " or ".join(type_names)
            raise ValueError(f"{path}: expected {expected}, got {actual}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
            raise ValueError(f"{path}: required must be an array of strings")
        for key in required:
            if key not in value:
                raise ValueError(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"{path}: properties must be an object")
        for key, child_schema in properties.items():
            if key in value:
                _validate_json_schema(value[key], child_schema, path=f"{path}.{key}")

    if isinstance(value, list) and "items" in schema:
        item_schema = schema["items"]
        for index, item in enumerate(value):
            _validate_json_schema(item, item_schema, path=f"{path}[{index}]")


def _validate_agent_response_contract(task_id: int, task: Any) -> None:
    evaluators = [item for item in task.eval if item.evaluator == "AgentResponseEvaluator"]
    if not evaluators:
        raise ValueError(f"task {task_id}: missing AgentResponseEvaluator")
    for evaluator in evaluators:
        payload = evaluator.model_dump(mode="json")
        expected = payload.get("expected")
        schema = payload.get("results_schema")
        if not isinstance(expected, dict) or "retrieved_data" not in expected:
            raise ValueError(
                f"task {task_id}: AgentResponseEvaluator is missing expected.retrieved_data"
            )
        if schema is None:
            raise ValueError(f"task {task_id}: AgentResponseEvaluator is missing results_schema")
        try:
            _validate_json_schema(
                expected["retrieved_data"], schema, path="expected.retrieved_data"
            )
        except ValueError as exc:
            raise ValueError(
                f"task {task_id}: AgentResponseEvaluator expected.retrieved_data "
                f"violates results_schema: {exc}"
            ) from exc


@lru_cache(maxsize=1)
def _task_exclusions() -> dict[str, dict[str, str]]:
    payload = json.loads(TASK_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "lex_browser_use_task_exclusions/1":
        raise ValueError("unsupported Browser Use task-exclusion schema")
    return {
        str(task_id): {str(key): str(value) for key, value in item.items()}
        for task_id, item in (payload.get("tasks") or {}).items()
    }


def _task_exclusion(task_id: int) -> dict[str, str] | None:
    return _task_exclusions().get(str(task_id))


def load_pilot(path: str | Path) -> PilotSelection:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema") != PILOT_SCHEMA:
        raise ValueError(f"unsupported pilot schema: {payload.get('schema')!r}")
    metadata = dict(payload.get("metadata") or {})
    tasks = tuple(
        PilotTask(
            task_id=int(item["task_id"]),
            tier=str(item["tier"]),
            estimated_min_steps=int(item["estimated_min_steps"]),
            rationale=str(item["rationale"]),
            official_hard=bool(item["official_hard"]),
        )
        for item in payload.get("tasks", [])
    )
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("pilot task IDs must be unique")
    counts = {tier: sum(task.tier == tier for task in tasks) for tier in TIER_COUNTS}
    if counts != TIER_COUNTS:
        raise ValueError(f"pilot tier counts must be {TIER_COUNTS}, got {counts}")
    if metadata.get("tier_counts") != TIER_COUNTS:
        raise ValueError("selection metadata tier_counts must match the frozen tier counts")
    for task in tasks:
        low, high = TIER_BOUNDS[task.tier]
        if task.estimated_min_steps < low or (high is not None and task.estimated_min_steps > high):
            raise ValueError(f"task {task.task_id}: step estimate is outside {task.tier}")
    return PilotSelection(tasks, source, _sha256(source), metadata)


def validate_against_official(
    selection: PilotSelection,
    benchmark: Any,
    *,
    enforce_contract_integrity: bool = True,
) -> list[dict[str, Any]]:
    """Resolve official metadata and, for new runs, reject invalid response contracts.

    Historical report reconstruction may disable the prospective integrity gate because
    its immutable cohort can contain the very contract defect being documented. It must
    still resolve the exact official package and checksum used by that evidence.
    """
    hard_payload = json.loads(
        files("webarena_verified")
        .joinpath("assets/dataset/subsets/webarena-verified-hard.json")
        .read_text(encoding="utf-8")
    )
    official_hard_ids = {int(task_id) for task_id in hard_payload["task_ids"]}
    resolved: list[dict[str, Any]] = []
    for selected in selection.tasks:
        task = benchmark.get_task(selected.task_id)
        if enforce_contract_integrity:
            exclusion = _task_exclusion(selected.task_id)
            if exclusion:
                raise ValueError(
                    f"task {selected.task_id}: excluded from new Browser Use runs: "
                    f"{exclusion['reason']}; {exclusion['disposition']}"
                )
            _validate_agent_response_contract(selected.task_id, task)
        sites = [getattr(site, "value", str(site)) for site in task.sites]
        if not set(sites).issubset(ALLOWED_SITES):
            raise ValueError(f"task {selected.task_id}: unsupported pilot sites {sites}")
        if len(sites) != 1:
            raise ValueError(
                f"task {selected.task_id}: pilot requires one resettable site, got {sites}"
            )
        if selected.official_hard != (selected.task_id in official_hard_ids):
            raise ValueError(
                f"task {selected.task_id}: official_hard does not match package subset"
            )
        resolved.append(
            {
                "task_id": selected.task_id,
                "tier": selected.tier,
                "estimated_min_steps": selected.estimated_min_steps,
                "rationale": selected.rationale,
                "official_hard": selected.official_hard,
                "sites": sites,
                "intent": task.intent,
                "intent_template_id": task.intent_template_id,
                "revision": task.revision,
                "start_urls": list(task.start_urls),
            }
        )
    site_counts = Counter(row["sites"][0] for row in resolved)
    expected_site_counts = selection.metadata.get("site_counts")
    if expected_site_counts is not None and expected_site_counts != dict(site_counts):
        raise ValueError(
            f"selection site counts must be {expected_site_counts}, got {dict(site_counts)}"
        )
    expected_task_type_counts = selection.metadata.get("task_type_counts")
    if expected_task_type_counts is not None:
        task_type_counts: Counter[str] = Counter()
        for selected in selection.tasks:
            task = benchmark.get_task(selected.task_id)
            response_evaluator = next(
                item for item in task.eval if item.evaluator == "AgentResponseEvaluator"
            )
            task_type = response_evaluator.expected.task_type
            task_type_counts[getattr(task_type, "value", str(task_type))] += 1
        if expected_task_type_counts != dict(task_type_counts):
            raise ValueError(
                "selection task type counts must be "
                f"{expected_task_type_counts}, got {dict(task_type_counts)}"
            )
    return resolved
