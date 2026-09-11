"""Derive fixed replay candidates from successful autonomous Browser Use evidence."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA = "lex_browser_use_replay_seeds/1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_browser_calls(events_path: str | Path) -> list[dict[str, Any]]:
    """Return complete BrowserTool calls in execution order, including call status."""

    starts: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    for event in _read_json(Path(events_path)):
        event_type = event.get("type")
        data = event.get("data") or {}
        call_id = str(data.get("tool_call_id") or "")
        if event_type == "tool_execution_start":
            if not call_id or call_id in starts:
                raise ValueError(f"invalid or duplicate tool call id in {events_path}: {call_id!r}")
            starts[call_id] = {
                "tool_call_id": call_id,
                "tool_name": str(data.get("tool_name") or ""),
                "arguments": dict(data.get("arguments") or {}),
                "started_at": event.get("timestamp"),
            }
        elif event_type == "tool_execution_end":
            start = starts.pop(call_id, None)
            if start is None:
                raise ValueError(f"tool end event has no start in {events_path}: {call_id!r}")
            start.update(
                {
                    "status": str(data.get("status") or ""),
                    "duration_seconds": data.get("execution_time"),
                    "error": data.get("error"),
                    "ended_at": event.get("timestamp"),
                }
            )
            calls.append(start)
    if starts:
        raise ValueError(f"tool calls have no end event in {events_path}: {sorted(starts)}")
    return calls


def _authoritative_rows(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _read_json(root / "manifest.json")
    model = str((manifest.get("protocol") or {}).get("model") or "")
    if not model:
        raise ValueError(f"evidence manifest does not identify a model: {root}")
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for line in (root / "attempts.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            grouped[(str(row["engine"]), int(row["task_id"]))].append(row)
    rows: list[dict[str, Any]] = []
    for (engine, task_id), executions in sorted(grouped.items()):
        executions.sort(key=lambda row: int(row["attempt"]))
        expected = list(range(1, len(executions) + 1))
        if [int(row["attempt"]) for row in executions] != expected:
            raise ValueError(f"non-contiguous executions for {model}/{engine}/{task_id}")
        if len(executions) > 2:
            raise ValueError(f"more than one retry for {model}/{engine}/{task_id}")
        row = dict(executions[-1])
        row["model"] = model
        rows.append(row)
    return manifest, rows


def build_replay_seed_manifest(
    evidence_dirs: list[str | Path], profile: dict[str, Any]
) -> dict[str, Any]:
    """Build a private replay seed inventory without using human demonstration traces."""

    models = [str(value) for value in profile.get("models") or []]
    engines = [str(value) for value in profile.get("engines") or []]
    method_priority = [(model, engine) for model in models for engine in engines]
    if not method_priority:
        raise ValueError("profile must register models and engines")

    manifests: dict[str, dict[str, Any]] = {}
    authoritative: list[dict[str, Any]] = []
    source_roots: dict[str, Path] = {}
    for raw_root in evidence_dirs:
        root = Path(raw_root).expanduser().resolve()
        manifest, rows = _authoritative_rows(root)
        model = str((manifest.get("protocol") or {}).get("model"))
        if model in manifests:
            raise ValueError(f"duplicate evidence cohort for model: {model}")
        manifests[model] = manifest
        source_roots[model] = root
        authoritative.extend(rows)
    if set(manifests) != set(models):
        raise ValueError(
            f"evidence models differ from profile: expected={models}, actual={sorted(manifests)}"
        )
    selection_hashes = {str(item.get("selection_sha256") or "") for item in manifests.values()}
    if len(selection_hashes) != 1 or "" in selection_hashes:
        raise ValueError("evidence cohorts do not share one frozen task selection")

    task_metadata: dict[int, dict[str, Any]] = {}
    for manifest in manifests.values():
        for item in manifest.get("selection") or []:
            task_id = int(item["task_id"])
            if task_id in task_metadata and task_metadata[task_id] != item:
                raise ValueError(f"task metadata differs between evidence cohorts: {task_id}")
            task_metadata[task_id] = dict(item)

    candidates_by_task: dict[int, list[dict[str, Any]]] = defaultdict(list)
    passed_methods_by_task: dict[int, list[str]] = defaultdict(list)
    for row in authoritative:
        task_id = int(row["task_id"])
        if float(row.get("score") or 0) != 1:
            continue
        model, engine = str(row["model"]), str(row["engine"])
        method_id = f"{model}__{engine}"
        passed_methods_by_task[task_id].append(method_id)
        events_path = Path(str((row.get("paths") or {}).get("events") or ""))
        response_path = Path(str((row.get("paths") or {}).get("response") or ""))
        evaluation_path = Path(str((row.get("paths") or {}).get("evaluation") or ""))
        calls = extract_browser_calls(events_path)
        clean = bool(calls) and all(call["status"] == "success" for call in calls)
        candidate = {
            "id": f"{method_id}__task-{task_id}",
            "method": {"model": model, "engine": engine},
            "source_execution": int(row["attempt"]),
            "source_run_id": str(row.get("run_id") or ""),
            "official_score": 1.0,
            "clean": clean,
            "action_count": len(calls),
            "actions": [str((call.get("arguments") or {}).get("action") or "") for call in calls],
            "tool_calls": [dict(call.get("arguments") or {}) for call in calls],
            "recorded_response": _read_json(response_path),
            "source": {
                "events_path": str(events_path),
                "events_sha256": _sha256(events_path),
                "response_path": str(response_path),
                "response_sha256": _sha256(response_path),
                "evaluation_path": str(evaluation_path),
                "evaluation_sha256": _sha256(evaluation_path),
            },
        }
        candidates_by_task[task_id].append(candidate)

    priority_index = {method: index for index, method in enumerate(method_priority)}
    tasks: list[dict[str, Any]] = []
    for task_id in sorted(task_metadata):
        candidates = sorted(
            candidates_by_task.get(task_id, []),
            key=lambda item: priority_index[(item["method"]["model"], item["method"]["engine"])],
        )
        clean_candidates = [item for item in candidates if item["clean"]]
        tasks.append(
            {
                **task_metadata[task_id],
                "successful_methods": sorted(passed_methods_by_task.get(task_id, [])),
                "candidates": candidates,
                "canonical_candidate_id": clean_candidates[0]["id"] if clean_candidates else None,
                "replay_status": (
                    "ready"
                    if clean_candidates
                    else "success_contains_failed_actions"
                    if candidates
                    else "all_methods_failed"
                ),
            }
        )

    return {
        "schema": SCHEMA,
        "source": {
            "kind": "authoritative successful Browser Use executions",
            "human_demonstrations_used": False,
            "benchmark": str(profile.get("benchmark") or "WebArena-Verified"),
            "benchmark_version": str(profile.get("benchmark_version") or ""),
            "official_data_checksum": str(profile.get("official_data_checksum") or ""),
            "selection_sha256": selection_hashes.pop(),
            "evidence": {
                model: {
                    "root": str(source_roots[model]),
                    "attempts_sha256": _sha256(source_roots[model] / "attempts.jsonl"),
                    "manifest_sha256": _sha256(source_roots[model] / "manifest.json"),
                }
                for model in models
            },
        },
        "selection_policy": {
            "authority": "final execution declared by each frozen Browser Use manifest",
            "eligibility": "official score 1.0 and every BrowserTool call completed successfully",
            "canonical_priority": [f"{model}__{engine}" for model, engine in method_priority],
            "performance_used_for_selection": False,
        },
        "counts": {
            "tasks": len(tasks),
            "tasks_with_authoritative_success": sum(bool(item["candidates"]) for item in tasks),
            "replay_ready_tasks": sum(item["replay_status"] == "ready" for item in tasks),
            "success_with_failed_actions_tasks": sum(
                item["replay_status"] == "success_contains_failed_actions" for item in tasks
            ),
            "all_methods_failed_tasks": sum(
                item["replay_status"] == "all_methods_failed" for item in tasks
            ),
            "successful_method_trajectories": sum(len(item["candidates"]) for item in tasks),
            "clean_method_trajectories": sum(
                sum(bool(candidate["clean"]) for candidate in item["candidates"]) for item in tasks
            ),
        },
        "tasks": tasks,
    }
