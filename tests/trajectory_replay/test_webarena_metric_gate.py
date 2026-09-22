import copy

import pytest

from harness.trajectory_replay.webarena_report import _outcome_metrics
from scripts.check_webarena_replay_report import _check_metric_populations


def fixture():
    rows = [
        {"participant_id": "a", "status": "pass", "duration_ms": 10, "resource": {"cpu_ms": 0}},
        {"participant_id": "a", "status": "fail", "duration_ms": 2},
    ]
    method = {
        "participant_id": "a",
        "total": 2,
        "passed": 1,
        **_outcome_metrics(rows),
        "successful": _outcome_metrics(rows[:1]),
    }
    return rows, {
        "distribution_scope": "all_declared_tasks",
        "successful_scope": "per_method_success",
        "passing_intersection_scope": "diagnostic_only",
        "methods": [method],
    }


def test_gate_accepts_failures_and_distinguishes_missing_from_real_zero():
    rows, metrics = fixture()
    _check_metric_populations(metrics, rows, ["a"])
    assert metrics["methods"][0]["cpu"]["n"] == 1
    assert metrics["methods"][0]["cpu"]["mean"] == 0
    assert metrics["methods"][0]["memory"]["mean"] is None


@pytest.mark.parametrize(
    "change",
    [
        "drop_failure",
        "missing_zero",
        "wrong_success",
        "rank_intersection",
        "nonfinite",
        "boolean_n",
    ],
)
def test_gate_rejects_misleading_distributions(change):
    rows, original = fixture()
    metrics = copy.deepcopy(original)
    method = metrics["methods"][0]
    if change == "drop_failure":
        method["duration"].update(n=1, missing=0, population=1)
    elif change == "missing_zero":
        method["memory"]["mean"] = 0
    elif change == "wrong_success":
        method["successful"]["duration"].update(n=2, missing=0, population=2)
    elif change == "rank_intersection":
        metrics["distribution_scope"] = "shared_passes"
    elif change == "nonfinite":
        method["cpu"]["mean"] = float("nan")
    else:
        method["cpu"]["n"] = True
    with pytest.raises(ValueError):
        _check_metric_populations(metrics, rows, ["a"])
