from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.metrics import summarize_attempts


def test_independent_audit_detects_tampered_aggregate_and_paired_operands(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/verify_metrics.py"
    spec = importlib.util.spec_from_file_location("verify_metrics", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [
        {
            "engine": engine,
            "task_id": "a",
            "attempt": 1,
            "status": "pass",
            "duration_ms": duration,
            "resource": {"memory_peak_bytes": memory},
        }
        for engine, duration, memory in (("chromium", 100, 200), ("moli", 50, 80))
    ]
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    summary = summarize_attempts(rows, engines=("chromium", "moli"))
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    assert module.verify(tmp_path)["status"] == "pass"
    summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p95"] = 99
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="moli.duration_ms.p95"):
        module.verify(tmp_path)
    summary = summarize_attempts(rows, engines=("chromium", "moli"))
    summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"]["values"][0][
        "numerator"
    ] = 49
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="numerator"):
        module.verify(tmp_path)
    summary["resource_comparison"]["by_engine"]["moli"].clear()
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="required metric set"):
        module.verify(tmp_path)


def test_independent_audit_follows_a_larger_participant_set(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/verify_metrics.py"
    spec = importlib.util.spec_from_file_location("verify_metrics", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engines = ("chromium", "moli", "obscura")
    rows = [
        {
            "engine": engine,
            "task_id": "a",
            "attempt": 1,
            "status": "pass",
            "duration_ms": duration,
            "resource": {"memory_peak_bytes": memory},
        }
        for engine, duration, memory in zip(engines, (100, 50, 25), (200, 80, 40), strict=True)
    ]
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    summary = summarize_attempts(rows, engines=engines)
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    assert module.verify(tmp_path)["status"] == "pass"

    relative = summary["resource_comparison"]["relative_ratios"]["by_engine"]
    relative["obscura"]["duration_ms"]["values"][0]["numerator"] = 24
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="numerator"):
        module.verify(tmp_path)

    summary = summarize_attempts(rows[:2], engines=engines[:2])
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="required provider set"):
        module.verify(tmp_path)


def _audit_module():
    script = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/verify_metrics.py"
    spec = importlib.util.spec_from_file_location("verify_metrics", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audited_summary(tmp_path, engines):
    rows = [
        {
            "engine": engine,
            "task_id": "a",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 100 / (position + 1),
            "resource": {"memory_peak_bytes": 200 / (position + 1)},
        }
        for position, engine in enumerate(engines)
    ]
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    summary = summarize_attempts(rows, engines=engines)
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return summary


@pytest.mark.parametrize(
    "engines",
    [("chromium",), ("chromium", "moli"), ("moli", "chromium"), ("chromium", "moli", "obscura")],
    ids=["one", "two", "two-other-baseline", "three"],
)
def test_independent_audit_accepts_the_labels_a_real_summary_publishes(tmp_path, engines):
    _audited_summary(tmp_path, engines)

    assert _audit_module().verify(tmp_path)["status"] == "pass"


def test_independent_audit_rejects_swapped_paired_labels(tmp_path):
    summary = _audited_summary(tmp_path, ("chromium", "moli"))
    paired = summary["resource_comparison"]["paired_ratios"]
    paired["numerator_engine"], paired["denominator_engine"] = "chromium", "moli"
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(ValueError, match="paired ratio labels"):
        _audit_module().verify(tmp_path)


def test_independent_audit_rejects_a_paired_label_on_a_larger_set(tmp_path):
    summary = _audited_summary(tmp_path, ("chromium", "moli", "obscura"))
    paired = summary["resource_comparison"]["paired_ratios"]
    paired["numerator_engine"], paired["denominator_engine"] = "moli", "chromium"
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(ValueError, match="paired ratio labels"):
        _audit_module().verify(tmp_path)


def test_independent_audit_rejects_a_changed_relative_baseline(tmp_path):
    summary = _audited_summary(tmp_path, ("chromium", "moli", "obscura"))
    summary["resource_comparison"]["relative_ratios"]["baseline_engine"] = "moli"
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(ValueError, match="relative ratio baseline"):
        _audit_module().verify(tmp_path)
