from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_webarena_authored_suite import navigate, task_definition
from scripts.build_webarena_repair_queue import build_repair_queue


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    scope = _write(
        tmp_path / "scope.json",
        {
            "schema": "lex_webarena_verified_full_scope/1",
            "task_count": 3,
            "task_ids": [0, 1, 2],
        },
    )
    ledger = _write(
        tmp_path / "ledger.json",
        {
            "schema": "lex_webarena_verified_full_ledger/1",
            "scope_sha256": hashlib.sha256(scope.read_bytes()).hexdigest(),
            "counts": {"total": 3, "pass": 1},
            "rows": [
                {"task_id": 0, "status": "pass"},
                {"task_id": 1, "status": "fail"},
                {"task_id": 2, "status": "needs_trajectory"},
            ],
        },
    )
    human = _write(tmp_path / "human.json", {"schema": "lex_browser_use_tasks/4", "tasks": []})
    repair = _write(
        tmp_path / "repair.json",
        {
            "schema": "lex_browser_use_tasks/4",
            "tasks": [
                task_definition(
                    {"task_id": 1, "intent": "Create the requested issue", "revision": 1},
                    [navigate("http://gitlab.example.com:8023", "page")],
                )
            ],
        },
    )
    return scope, ledger, human, repair


def test_repair_queue_preserves_passes_and_official_order(tmp_path: Path) -> None:
    scope, ledger, human, repair = _inputs(tmp_path)
    queue = build_repair_queue(scope, ledger, human, repair)
    assert [row["status"] for row in queue["rows"]] == [
        "pass",
        "pending_first_sweep",
        "first_sweep_failed",
    ]
    assert [row["task_id"] for row in queue["rows"]] == [0, 1, 2]


def test_repair_queue_rejects_replaying_a_pass(tmp_path: Path) -> None:
    scope, ledger, human, repair = _inputs(tmp_path)
    suite = json.loads(repair.read_text(encoding="utf-8"))
    suite["tasks"][0]["id"] = "webarena-0"
    _write(repair, suite)
    with pytest.raises(ValueError, match="already passed"):
        build_repair_queue(scope, ledger, human, repair)


def test_repair_queue_rejects_partial_ledger(tmp_path: Path) -> None:
    scope, ledger, human, repair = _inputs(tmp_path)
    value = json.loads(ledger.read_text(encoding="utf-8"))
    value["rows"].pop()
    _write(ledger, value)
    with pytest.raises(ValueError, match="complete audited official scope"):
        build_repair_queue(scope, ledger, human, repair)
