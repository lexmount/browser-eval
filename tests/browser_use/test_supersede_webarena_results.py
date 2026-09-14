from __future__ import annotations

import pytest

from scripts.supersede_webarena_task_browser_results import supersede_rows


def _row(task: str, engine: str, attempt: int, status: str, contract: str = "same"):
    return {
        "task_id": task,
        "engine": engine,
        "attempt": attempt,
        "is_retry": attempt == 2,
        "status": status,
        "task_contract_sha256": contract,
    }


def test_supersedes_the_complete_task_browser_scope():
    base = [
        _row("a", "chromium", 1, "pass"),
        _row("a", "moli", 1, "fail"),
        _row("a", "moli", 2, "fail"),
        _row("b", "chromium", 1, "pass"),
    ]
    correction = [_row("a", "moli", 1, "pass")]

    merged = supersede_rows(base, correction, {("a", "moli")})

    assert [(row["task_id"], row["engine"], row["attempt"]) for row in merged] == [
        ("a", "chromium", 1),
        ("a", "moli", 1),
        ("b", "chromium", 1),
    ]
    assert merged[1]["status"] == "pass"


def test_rejects_partial_or_best_attempt_corrections():
    base = [_row("a", "moli", 1, "fail"), _row("a", "moli", 2, "fail")]

    with pytest.raises(ValueError, match="attempt numbering"):
        supersede_rows(base, [_row("a", "moli", 2, "pass")], {("a", "moli")})

    with pytest.raises(ValueError, match="retried after a passing result"):
        supersede_rows(
            base,
            [_row("a", "moli", 1, "pass"), _row("a", "moli", 2, "pass")],
            {("a", "moli")},
        )


def test_rejects_contract_or_scope_drift():
    base = [_row("a", "moli", 1, "fail")]

    with pytest.raises(ValueError, match="scope mismatch"):
        supersede_rows(base, [_row("b", "moli", 1, "pass")], {("a", "moli")})

    with pytest.raises(ValueError, match="task contract changed"):
        supersede_rows(
            base,
            [_row("a", "moli", 1, "pass", contract="changed")],
            {("a", "moli")},
        )
