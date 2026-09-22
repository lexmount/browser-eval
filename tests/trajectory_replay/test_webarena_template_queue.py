from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_webarena_template_queue import build_template_queue


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_template_queue_runs_only_untried_proposals(tmp_path: Path) -> None:
    queue = _write(
        tmp_path / "queue.json",
        {
            "schema": "lex_webarena_verified_full_queue/1",
            "human_suite_sha256": "human",
            "rows": [
                {"task_id": 0, "status": "pass"},
                {"task_id": 1, "status": "pending_first_sweep"},
                {"task_id": 2, "status": "needs_trajectory"},
            ],
        },
    )
    progress = tmp_path / "progress.jsonl"
    progress.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"task_id": 0, "status": "pass"},
                {"task_id": 1, "status": "fail"},
                {"task_id": 2, "status": "needs_trajectory"},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proposals = _write(
        tmp_path / "proposals.json",
        {
            "schema": "lex_webarena_template_candidates/1",
            "queue_sha256": hashlib.sha256(queue.read_bytes()).hexdigest(),
            "candidates": [{"task_id": 2}],
        },
    )
    result = build_template_queue(queue, progress, proposals)
    assert [row["status"] for row in result["rows"]] == [
        "pass",
        "first_sweep_failed",
        "pending_first_sweep",
    ]
    progress.write_text(json.dumps({"task_id": 0, "status": "pass"}) + "\n")
    with pytest.raises(ValueError, match="complete ordered first sweep"):
        build_template_queue(queue, progress, proposals)
