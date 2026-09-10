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
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
    summary = summarize_attempts(rows, engines=("chromium", "moli"))
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))
    assert module.verify(tmp_path)["status"] == "pass"
    summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p95"] = 99
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="moli.duration_ms.p95"):
        module.verify(tmp_path)
    summary = summarize_attempts(rows, engines=("chromium", "moli"))
    summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"]["values"][0][
        "numerator"
    ] = 49
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="numerator"):
        module.verify(tmp_path)
    summary["resource_comparison"]["by_engine"]["moli"].clear()
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="required metric set"):
        module.verify(tmp_path)
