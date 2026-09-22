import json
from pathlib import Path

import pytest

from scripts.build_webarena_authored_queue import build_authored_queue


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def _authored_task(task_id: int) -> dict:
    return {
        "id": f"webarena-{task_id}",
        "title": "Open the visible route",
        "trajectory": {
            "schema": "lex_browser_trajectory/1",
            "actions": [
                {"action": "navigate", "url": "http://map.example", "save_as": "page"}
            ],
        },
        "difficulty": "simple",
        "ground_truth": {
            "oracle": "webarena_verified",
            "expected_actions": ["navigate"],
            "response_builder": {"kind": "null_after_verified_path", "task_type": "navigate"},
        },
        "evaluator": {"kind": "webarena_verified", "task_id": task_id, "revision": 1},
        "source": {"url": "https://example.test/official-case"},
        "environment": {"kind": "webarena_verified"},
    }


def test_authored_queue_attempts_every_remaining_case_after_template_sweep(tmp_path: Path) -> None:
    queue = _write(
        tmp_path / "queue.json",
        {
            "schema": "lex_webarena_verified_full_queue/1",
            "human_suite_sha256": "frozen",
            "rows": [
                {"task_id": 0, "status": "pass"},
                {"task_id": 1, "status": "needs_trajectory"},
            ],
        },
    )
    progress = tmp_path / "progress.jsonl"
    progress.write_text(
        '{"task_id":0,"status":"pass"}\n'
        '{"task_id":1,"status":"needs_trajectory"}\n',
        encoding="utf-8",
    )
    authored = _write(
        tmp_path / "authored.json",
        {"schema": "lex_browser_use_tasks/4", "tasks": [_authored_task(1)]},
    )
    result = build_authored_queue(queue, progress, authored)
    assert result["rows"] == [
        {"task_id": 0, "status": "pass", "source": "earlier_pass"},
        {"task_id": 1, "status": "pending_first_sweep", "source": "authored_trajectory"},
    ]

    _write(authored, {"schema": "lex_browser_use_tasks/4", "tasks": [_authored_task(0)]})
    with pytest.raises(ValueError, match="every still-unattempted"):
        build_authored_queue(queue, progress, authored)
