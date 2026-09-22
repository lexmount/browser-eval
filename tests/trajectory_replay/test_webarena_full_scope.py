from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_webarena_full_scope import build_scope


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_full_scope_keeps_official_order_and_independent_sample(tmp_path: Path) -> None:
    official = _write(tmp_path / "official.json", [{"task_id": i} for i in range(4)])
    sample = _write(
        tmp_path / "sample.json",
        {"schema": "lex_webarena_verified_pilot/1", "tasks": [{"task_id": 3}]},
    )
    scope = build_scope(official, sample)
    assert scope["task_ids"] == [0, 1, 2, 3]
    assert scope["independent_sample_split"]["task_count"] == 1
    assert scope["browser"] == "chromium"


@pytest.mark.parametrize("ids", [[0, 2, 1], [0, 1, 1], [0, 2]])
def test_full_scope_rejects_changed_official_order(tmp_path: Path, ids: list[int]) -> None:
    official = _write(tmp_path / "official.json", [{"task_id": i} for i in ids])
    sample = _write(
        tmp_path / "sample.json",
        {"schema": "lex_webarena_verified_pilot/1", "tasks": [{"task_id": 0}]},
    )
    with pytest.raises(ValueError):
        build_scope(official, sample)
