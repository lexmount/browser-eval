from __future__ import annotations

import pytest

from scripts.supersede_webarena_task_browser_results import _validate_run_binding, supersede_rows


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


def test_rejects_correction_run_binding_drift():
    base = {
        "suite_sha256": "suite",
        "protocol": {
            "sample_interval_ms": 100,
            "post_reset_settle_ms": 200,
            "task_timeout_ms": 300,
            "cpus_per_browser": 1,
            "memory_per_browser": "1g",
        },
        "environment": {
            "images": {"moli": {"id": "sha256:image"}},
            "browser_versions": {"moli": "1.0.6"},
        },
    }
    correction = {
        **base,
        "protocol": dict(base["protocol"]),
        "environment": {
            "images": {"moli": dict(base["environment"]["images"]["moli"])},
            "browser_versions": dict(base["environment"]["browser_versions"]),
        },
    }
    _validate_run_binding(base, correction, engine="moli")
    for field, value, message in (
        ("suite_sha256", "other", "suite differs"),
        ("protocol.task_timeout_ms", 301, "task_timeout_ms"),
        ("environment.images.moli.id", "sha256:other", "image differs"),
        ("environment.browser_versions.moli", "1.0.7", "version differs"),
    ):
        changed = {
            **correction,
            "protocol": dict(correction["protocol"]),
            "environment": {
                "images": {"moli": dict(correction["environment"]["images"]["moli"])},
                "browser_versions": dict(correction["environment"]["browser_versions"]),
            },
        }
        target = changed
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
        with pytest.raises(ValueError, match=message):
            _validate_run_binding(base, changed, engine="moli")
