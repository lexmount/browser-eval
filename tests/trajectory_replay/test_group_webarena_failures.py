from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.group_webarena_failures import group_failures


def _write(path: Path, rows: object, *, jsonl: bool = False) -> Path:
    text = "\n".join(json.dumps(row) for row in rows) + "\n" if jsonl else json.dumps(rows)
    path.write_text(text, encoding="utf-8")
    return path


def test_failure_groups_use_latest_evidence_and_exclude_passes(tmp_path: Path) -> None:
    locator_error = "Locator.wait_for: Timeout\n  - waiting for get_by_role('button', name='Save')"
    official = _write(
        tmp_path / "official.json",
        [
            {"task_id": 0, "intent_template_id": 9},
            {"task_id": 1, "intent_template_id": 9},
            {"task_id": 2, "intent_template_id": 9},
        ],
    )
    ledger = _write(
        tmp_path / "ledger.json",
        {
            "schema": "lex_webarena_verified_full_ledger/1",
            "rows": [
                {"task_id": 0, "status": "pass"},
                {"task_id": 1, "status": "fail"},
                {"task_id": 2, "status": "fail"},
            ],
        },
    )
    first = _write(
        tmp_path / "first.jsonl",
        [
            {"task_id": 0, "status": "pass", "action_count": 2},
            {"task_id": 1, "status": "needs_trajectory"},
            {"task_id": 2, "status": "needs_trajectory"},
        ],
        jsonl=True,
    )
    second = _write(
        tmp_path / "second.jsonl",
        [
            {"task_id": 0, "status": "pass"},
            {
                "task_id": 1,
                "status": "fail",
                "action_count": 1,
                "error": locator_error,
            },
            {
                "task_id": 2,
                "status": "fail",
                "action_count": 1,
                "error": locator_error,
            },
        ],
        jsonl=True,
    )
    result = group_failures(official, ledger, [first, second])
    assert result["unresolved"] == 2
    assert result["groups"] == [
        {
            "intent_template_id": 9,
            "failure_signature": "locator_timeout: get_by_role('button', name='Save')",
            "count": 2,
            "task_ids": [1, 2],
        }
    ]
    second.write_text(second.read_text(encoding="utf-8").splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="official order"):
        group_failures(official, ledger, [first, second])
