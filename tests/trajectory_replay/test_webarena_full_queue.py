from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_webarena_full_queue import build_queue


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_queue_preserves_order_and_never_schedules_a_proven_pass(tmp_path: Path) -> None:
    sample = _write(
        tmp_path / "sample.json",
        {"schema": "lex_webarena_verified_pilot/1", "tasks": [{"task_id": 1}]},
    )
    scope = _write(
        tmp_path / "scope.json",
        {
            "schema": "lex_webarena_verified_full_scope/1",
            "task_count": 3,
            "task_ids": [0, 1, 2],
            "independent_sample_split": {
                "sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
                "task_count": 1,
            },
        },
    )
    evaluation = _write(tmp_path / "evaluation.json", {"task_id": 1, "score": 1})
    passed = _write(
        tmp_path / "passed.json",
        {
            "status": "complete",
            "task_ids": [1],
            "attempts": [
                {
                    "engine": "chromium",
                    "task_id": 1,
                    "status": "pass",
                    "evaluator_status": "pass",
                    "evidence": {
                        "evaluation": {
                            "path": str(evaluation),
                            "sha256": hashlib.sha256(evaluation.read_bytes()).hexdigest(),
                        }
                    },
                }
            ],
        },
    )
    human = _write(
        tmp_path / "human.json",
        {"tasks": [{"id": "webarena-0", "trajectory": {"actions": [{"action": "click"}]}}]},
    )
    queue = build_queue(scope, sample, passed, human)
    assert [(row["task_id"], row["status"]) for row in queue["rows"]] == [
        (0, "pending_first_sweep"),
        (1, "pass"),
        (2, "needs_trajectory"),
    ]
    _write(evaluation, {"task_id": 1, "score": 0})
    with pytest.raises(ValueError, match="intact evaluator evidence"):
        build_queue(scope, sample, passed, human)
