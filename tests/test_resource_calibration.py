"""Calibration compares the declared matched experiment, without browser calls."""

from copy import deepcopy

import pytest

from harness.resource_calibration import duration_calibration


def experiment(engines=("first",), count=3):
    baseline = [
        {
            "task_id": f"task-{i}",
            "engine": engine,
            "attempt": 1,
            "status": "pass",
            "duration_ms": 100,
        }
        for engine in engines
        for i in range(count)
    ]
    measured = [
        dict(row, duration_ms=105, resource={"collection_wall_ms": 108}) for row in baseline
    ]
    shared = {
        "resolved_tasks": [{"task_id": f"task-{i}", "sha256": "a" * 64} for i in range(count)],
        "seed": "frozen",
        "k_runs": 1,
        "selected_engines": list(engines),
        "score_mode": "official",
        "engines": {engine: {"sha256": "b" * 64} for engine in engines},
        "runner": {
            "jobs": 1,
            "browser_reuse": "none",
            "harness_pins": {"harness": "c" * 64},
            "source": {"tree_sha256": "d" * 64},
        },
        "host": {"os": "Linux", "architecture": "aarch64"},
    }
    before, after = deepcopy(shared), deepcopy(shared)
    for manifest, mode in [(before, "baseline"), (after, "engine")]:
        manifest["resource_profile"] = {
            "mode": mode,
            "engine_order": "balanced_rotation",
            "engine_order_algorithm": "forward-reverse",
        }
    return measured, baseline, after, before


def calibrate(rows, baseline, profiled, original, threshold=10):
    return duration_calibration(
        rows, baseline, threshold, profiled_manifest=profiled, baseline_manifest=original
    )


def test_complete_pairing_reproduces_expected_overhead_for_multiple_engines():
    result = calibrate(*experiment(("first", "second")))
    assert result["acceptable"] and result["complete_pairing"]
    assert (
        result["matched_attempts"]
        == result["duration_pairs"]
        == result["collection_wall_pairs"]
        == 6
    )
    assert result["median_duration_delta_pct"] == pytest.approx(5)
    assert result["p95_collection_wall_delta_pct"] == pytest.approx(8)
    assert set(result["by_engine"]) == {"first", "second"}


@pytest.mark.parametrize(
    "damage",
    [
        "missing-profile",
        "missing-baseline",
        "duplicate-profile",
        "duplicate-baseline",
        "unknown-profile",
    ],
)
def test_partial_duplicate_or_unpaired_calls_cannot_pass_calibration(damage):
    rows, baseline, profiled, original = experiment()
    if damage == "missing-profile":
        rows.pop()
    elif damage == "missing-baseline":
        baseline.pop()
    elif damage == "duplicate-profile":
        rows.append(dict(rows[0]))
    elif damage == "duplicate-baseline":
        baseline.append(dict(baseline[0]))
    else:
        rows[0]["task_id"] = "undeclared"
    assert not calibrate(rows, baseline, profiled, original)["acceptable"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "100"])
@pytest.mark.parametrize("field", ["duration_ms", "collection_wall_ms"])
def test_invalid_duration_measurements_fail_closed(value, field):
    rows, baseline, profiled, original = experiment()
    if field == "duration_ms":
        rows[0][field] = value
    else:
        rows[0]["resource"][field] = value
    with pytest.raises(ValueError, match="measurement"):
        calibrate(rows, baseline, profiled, original)


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -1, True, "10"])
def test_invalid_observer_effect_budget_is_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        calibrate(*experiment(), threshold=threshold)


def test_per_engine_gate_prevents_fast_engine_masking_a_slow_engine():
    rows, baseline, profiled, original = experiment(("fast", "slow"))
    for row in rows:
        if row["engine"] == "slow":
            row["duration_ms"] = 130
    result = calibrate(rows, baseline, profiled, original)
    assert result["by_engine"]["fast"]["acceptable"]
    assert not result["by_engine"]["slow"]["acceptable"]
    assert not result["acceptable"]


@pytest.mark.parametrize(
    "field", ["seed", "k_runs", "selected_engines", "score_mode", "engines", "host"]
)
def test_changed_shared_controls_cannot_supply_a_calibration_baseline(field):
    rows, baseline, profiled, original = experiment()
    original.pop(field)
    result = calibrate(rows, baseline, profiled, original)
    assert not result["acceptable"] and result["provenance_mismatches"]


def test_missing_manifests_do_not_gain_a_provenance_claim():
    rows, baseline, _, _ = experiment()
    result = duration_calibration(rows, baseline, 10)
    assert not result["acceptable"]
    assert result["provenance_mismatches"] == ["run_manifest"]


@pytest.mark.parametrize("damage", ["matched-subset", "undeclared-task", "extra-repeat"])
def test_matching_rows_must_cover_exactly_the_manifest_population(damage):
    rows, baseline, profiled, original = experiment()
    if damage == "matched-subset":
        rows.pop()
        baseline.pop()
    elif damage == "undeclared-task":
        rows[0]["task_id"] = baseline[0]["task_id"] = "undeclared"
    else:
        rows[0]["attempt"] = baseline[0]["attempt"] = 2
    result = calibrate(rows, baseline, profiled, original)
    assert not result["complete_pairing"]
    assert not result["acceptable"]


@pytest.mark.parametrize("attempt", [True, 1.9, "1", 0, -1])
def test_call_identity_cannot_coerce_an_invalid_repeat_into_a_declared_call(attempt):
    rows, baseline, profiled, original = experiment()
    rows[0]["attempt"] = baseline[0]["attempt"] = attempt
    with pytest.raises(ValueError, match="identity"):
        calibrate(rows, baseline, profiled, original)
