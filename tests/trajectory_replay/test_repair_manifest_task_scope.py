from __future__ import annotations

import json

import pytest

from harness.trajectory_replay.manifest_repair import repair_task_scope


def _write_run(tmp_path, *, skipped=("skip",)):
    manifest = {
        "suite": [{"task_id": "keep"}, {"task_id": "skip"}],
        "protocol": {
            "engines": ["chromium", "moli"],
            "task_filter": ["keep", "skip"],
            "skipped_qualified_tasks": list(skipped),
        },
        "artifact_provenance": {},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    rows = [
        {"task_id": "keep", "engine": engine, "attempt": 1}
        for engine in ("chromium", "moli")
    ]
    (tmp_path / "attempts.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )


def test_repair_preserves_old_manifest_and_limits_denominator(tmp_path):
    _write_run(tmp_path)
    correction = repair_task_scope(tmp_path)

    repaired = json.loads((tmp_path / "manifest.json").read_text())
    original = json.loads((tmp_path / "manifest.superseded-task-scope.json").read_text())
    assert [row["task_id"] for row in repaired["suite"]] == ["keep"]
    assert repaired["protocol"]["task_filter"] == ["keep"]
    assert [row["task_id"] for row in original["suite"]] == ["keep", "skip"]
    assert correction["measured_attempts_changed"] is False


def test_repair_rejects_unexplained_missing_attempts(tmp_path):
    _write_run(tmp_path, skipped=())
    with pytest.raises(ValueError, match="do not exactly match"):
        repair_task_scope(tmp_path)
