from __future__ import annotations

import json
from pathlib import Path

from harness.trajectory_replay.browser_use_seeds import (
    build_replay_seed_manifest,
    compile_replay_calls,
)


def _write_attempt(
    root: Path, *, model: str, engine: str, task_id: int, attempt: int, score: int, clean: bool
) -> dict:
    folder = root / engine / str(task_id) / f"attempt-{attempt}"
    folder.mkdir(parents=True, exist_ok=True)
    call_id = f"{task_id}-{attempt}"
    events = [
        {
            "type": "tool_execution_start",
            "timestamp": 1,
            "data": {
                "tool_call_id": call_id,
                "tool_name": "browser",
                "arguments": {"action": "navigate", "url": "https://example.com"},
            },
        },
        {
            "type": "tool_execution_end",
            "timestamp": 2,
            "data": {
                "tool_call_id": call_id,
                "status": "success" if clean else "error",
                "execution_time": 1,
                "error": None if clean else "failed",
            },
        },
    ]
    paths = {}
    for name, value in (
        ("events", events),
        ("response", {"task_type": "NAVIGATE", "status": "SUCCESS"}),
        ("evaluation", {"score": score}),
    ):
        path = folder / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = str(path)
    return {
        "engine": engine,
        "task_id": task_id,
        "attempt": attempt,
        "score": score,
        "run_id": "run",
        "paths": paths,
    }


def _write_cohort(root: Path, model: str, rows: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "selection_sha256": "selection",
                "protocol": {"model": model},
                "selection": [
                    {
                        "task_id": task_id,
                        "tier": "easy",
                        "sites": ["gitlab"],
                        "intent": f"task {task_id}",
                    }
                    for task_id in (1, 2, 3)
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "attempts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_seed_manifest_uses_final_authority_and_clean_fixed_priority(tmp_path: Path) -> None:
    roots = []
    for model in ("model-a", "model-b"):
        root = tmp_path / model
        rows = []
        for engine in ("chromium", "moli"):
            rows.extend(
                [
                    _write_attempt(
                        root,
                        model=model,
                        engine=engine,
                        task_id=1,
                        attempt=1,
                        score=1,
                        clean=True,
                    ),
                    _write_attempt(
                        root,
                        model=model,
                        engine=engine,
                        task_id=2,
                        attempt=1,
                        score=1,
                        clean=False,
                    ),
                    _write_attempt(
                        root,
                        model=model,
                        engine=engine,
                        task_id=3,
                        attempt=1,
                        score=0,
                        clean=True,
                    ),
                ]
            )
        _write_cohort(root, model, rows)
        roots.append(root)

    manifest = build_replay_seed_manifest(
        roots,
        {"models": ["model-a", "model-b"], "engines": ["chromium", "moli"]},
    )

    assert manifest["source"]["human_demonstrations_used"] is False
    assert manifest["counts"] == {
        "tasks": 3,
        "tasks_with_authoritative_success": 2,
        "replay_ready_tasks": 1,
        "success_with_failed_actions_tasks": 1,
        "all_methods_failed_tasks": 1,
        "successful_method_trajectories": 8,
        "clean_method_trajectories": 4,
    }
    tasks = {item["task_id"]: item for item in manifest["tasks"]}
    assert tasks[1]["canonical_candidate_id"] == "model-a__chromium__task-1"
    assert tasks[2]["canonical_candidate_id"] is None
    assert tasks[2]["replay_status"] == "success_contains_failed_actions"
    assert tasks[3]["replay_status"] == "all_methods_failed"


def test_seed_manifest_does_not_promote_superseded_success(tmp_path: Path) -> None:
    roots = []
    for model in ("model-a", "model-b"):
        root = tmp_path / model
        rows = []
        for engine in ("chromium", "moli"):
            rows.append(
                _write_attempt(
                    root,
                    model=model,
                    engine=engine,
                    task_id=1,
                    attempt=1,
                    score=1,
                    clean=True,
                )
            )
            rows.append(
                _write_attempt(
                    root,
                    model=model,
                    engine=engine,
                    task_id=1,
                    attempt=2,
                    score=0,
                    clean=True,
                )
            )
        _write_cohort(root, model, rows)
        roots.append(root)

    manifest = build_replay_seed_manifest(
        roots,
        {"models": ["model-a", "model-b"], "engines": ["chromium", "moli"]},
    )

    assert manifest["counts"]["tasks_with_authoritative_success"] == 0
    assert manifest["tasks"][0]["replay_status"] == "all_methods_failed"


def test_compile_replay_calls_replaces_snapshot_refs_with_selectors(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps(
            [
                {
                    "type": "tool_execution_start",
                    "data": {
                        "tool_call_id": "navigate",
                        "tool_name": "browser",
                        "arguments": {"action": "navigate", "url": "https://example.com"},
                    },
                },
                {
                    "type": "tool_execution_end",
                    "data": {
                        "tool_call_id": "navigate",
                        "status": "success",
                        "result": "Page: Example\nInteractive elements: 2\n---\n  [1] a href=\"/one\": One\n  [2] a href=\"/two\": Two",
                    },
                },
                {
                    "type": "tool_execution_start",
                    "data": {
                        "tool_call_id": "click",
                        "tool_name": "browser",
                        "arguments": {"action": "click", "ref": 2},
                    },
                },
                {
                    "type": "tool_execution_end",
                    "data": {"tool_call_id": "click", "status": "success", "result": "ok"},
                },
            ]
        ),
        encoding="utf-8",
    )

    assert compile_replay_calls(events) == [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click", "selector": 'a[href="/two"]:visible'},
    ]


def test_compile_replay_calls_counts_the_full_dom_selector_population(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps(
            [
                {
                    "type": "tool_execution_start",
                    "data": {
                        "tool_call_id": "snapshot",
                        "tool_name": "browser",
                        "arguments": {"action": "snapshot"},
                    },
                },
                {
                    "type": "tool_execution_end",
                    "data": {
                        "tool_call_id": "snapshot",
                        "status": "success",
                        "result": (
                            "Page: Example\nInteractive elements: 3\n---\n"
                            "  [1] button ariaLabel=\"Menu\": Menu\n"
                            "  [2] button\n"
                            "  [3] a href=\"/stable\" ariaLabel=\"Stable link\": Stable"
                        ),
                    },
                },
                {
                    "type": "tool_execution_start",
                    "data": {
                        "tool_call_id": "button",
                        "tool_name": "browser",
                        "arguments": {"action": "click", "ref": 2},
                    },
                },
                {
                    "type": "tool_execution_end",
                    "data": {"tool_call_id": "button", "status": "success"},
                },
                {
                    "type": "tool_execution_start",
                    "data": {
                        "tool_call_id": "link",
                        "tool_name": "browser",
                        "arguments": {"action": "click", "ref": 3},
                    },
                },
                {
                    "type": "tool_execution_end",
                    "data": {"tool_call_id": "link", "status": "success"},
                },
            ]
        ),
        encoding="utf-8",
    )

    assert compile_replay_calls(events)[1:] == [
        {"action": "click", "selector": ":nth-match(button:visible, 2)"},
        {"action": "click", "selector": 'a[href="/stable"]:visible'},
    ]
