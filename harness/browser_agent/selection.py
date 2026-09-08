"""Pre-registered WebArena-Verified pilot selection and validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

PILOT_SCHEMA = "lex_webarena_verified_pilot/1"
ALLOWED_SITES = frozenset({"shopping", "shopping_admin", "reddit", "gitlab"})
TIER_COUNTS = {"easy": 5, "medium": 10, "hard": 15}
TIER_BOUNDS = {"easy": (1, 4), "medium": (5, 7), "hard": (8, None)}


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


def load_pilot(path: str | Path) -> PilotSelection:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema") != PILOT_SCHEMA:
        raise ValueError(f"unsupported pilot schema: {payload.get('schema')!r}")
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
    for task in tasks:
        low, high = TIER_BOUNDS[task.tier]
        if task.estimated_min_steps < low or (
            high is not None and task.estimated_min_steps > high
        ):
            raise ValueError(f"task {task.task_id}: step estimate is outside {task.tier}")
    return PilotSelection(tasks, source, _sha256(source), dict(payload.get("metadata") or {}))


def validate_against_official(selection: PilotSelection, benchmark: Any) -> list[dict[str, Any]]:
    """Resolve task metadata through the official package; never copy evaluator rules."""
    hard_payload = json.loads(
        files("webarena_verified")
        .joinpath("assets/dataset/subsets/webarena-verified-hard.json")
        .read_text(encoding="utf-8")
    )
    official_hard_ids = {int(task_id) for task_id in hard_payload["task_ids"]}
    resolved: list[dict[str, Any]] = []
    for selected in selection.tasks:
        task = benchmark.get_task(selected.task_id)
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
    return resolved
