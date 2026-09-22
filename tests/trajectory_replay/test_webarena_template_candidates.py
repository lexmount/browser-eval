from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.build_webarena_template_candidates import build_candidates


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_same_template_transfer_is_a_proposal_with_bound_source(tmp_path: Path) -> None:
    official = _write(
        tmp_path / "official.json",
        [
            {
                "task_id": 0,
                "intent_template_id": 2,
                "sites": ["reddit"],
                "instantiation_dict": {"forum": "books"},
                "start_urls": ["__REDDIT__"],
            },
            {
                "task_id": 1,
                "intent_template_id": 2,
                "sites": ["reddit"],
                "instantiation_dict": {"forum": "science"},
                "start_urls": ["__REDDIT__"],
            },
            {
                "task_id": 2,
                "intent_template_id": 3,
                "sites": ["gitlab"],
                "instantiation_dict": {},
                "start_urls": ["__GITLAB__"],
            },
        ],
    )
    human = _write(
        tmp_path / "human.json",
        {
            "tasks": [
                {
                    "id": "webarena-0",
                    "trajectory": {
                        "actions": [
                            {"action": "click", "selector": "a[href='/f/books']"},
                        ]
                    },
                }
            ]
        },
    )
    queue = _write(
        tmp_path / "queue.json",
        {
            "schema": "lex_webarena_verified_full_queue/1",
            "human_suite_sha256": hashlib.sha256(human.read_bytes()).hexdigest(),
            "rows": [
                {"task_id": 0, "status": "pending_first_sweep"},
                {"task_id": 1, "status": "needs_trajectory"},
                {"task_id": 2, "status": "needs_trajectory"},
            ],
        },
    )
    result = build_candidates(official, queue, human)
    assert result["unmatched_task_ids"] == [2]
    assert result["candidates"][0]["source_task_id"] == 0
    assert result["candidates"][0]["actions"] == [
        {"action": "click", "selector": "a[href='/f/science']"}
    ]
    assert result["qualification"] == "pending_chromium_replay_and_official_evaluator"


def test_curated_pass_supplies_missing_template_without_reusing_its_score(tmp_path: Path) -> None:
    official = _write(
        tmp_path / "official.json",
        [
            {
                "task_id": task_id,
                "intent_template_id": 7,
                "sites": ["shopping"],
                "instantiation_dict": {"category": category},
                "start_urls": ["__SHOPPING__"],
            }
            for task_id, category in enumerate(("Books", "Shoes"))
        ],
    )
    human = _write(tmp_path / "human.json", {"tasks": []})
    curated = _write(
        tmp_path / "curated.json",
        {
            "trajectories": [
                {"task_id": 0, "tool_calls": [{"action": "click", "selector": "Books"}]}
            ]
        },
    )
    queue = _write(
        tmp_path / "queue.json",
        {
            "schema": "lex_webarena_verified_full_queue/1",
            "human_suite_sha256": hashlib.sha256(human.read_bytes()).hexdigest(),
            "rows": [
                {"task_id": 0, "status": "pass"},
                {"task_id": 1, "status": "needs_trajectory"},
            ],
        },
    )
    result = build_candidates(official, queue, human, curated)
    assert result["unmatched_task_ids"] == []
    assert result["candidates"][0]["source_kind"] == "curated"
    assert result["candidates"][0]["actions"] == [
        {"action": "click", "selector": "Shoes"}
    ]
    assert result["curated_trajectories_sha256"] == hashlib.sha256(
        curated.read_bytes()
    ).hexdigest()

    retrieval = json.loads(official.read_text(encoding="utf-8"))
    retrieval[1]["eval"] = [
        {"evaluator": "AgentResponseEvaluator", "expected": {"task_type": "retrieve"}}
    ]
    official = _write(official, retrieval)
    result = build_candidates(official, queue, human, curated)
    assert result["candidates"] == []
    assert result["unmatched_task_ids"] == [1]
