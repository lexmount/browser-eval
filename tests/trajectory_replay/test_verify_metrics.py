from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.metrics import summarize_attempts


def test_independent_audit_detects_tampered_aggregate_and_intersection(tmp_path):
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
    summary["resource_comparison"]["intersection_results"] = 0
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="successful task intersection mismatch"):
        module.verify(tmp_path)
    summary["resource_comparison"]["intersection_results"] = 1
    summary["resource_comparison"]["by_engine"]["moli"].clear()
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="required metric set"):
        module.verify(tmp_path)


def test_independent_audit_enforces_retry_matrix_and_success_evidence(tmp_path):
    script = Path(__file__).resolve().parents[2] / "examples/trajectory_replay/verify_metrics.py"
    spec = importlib.util.spec_from_file_location("verify_metrics_contract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = []
    for engine, status in (("chromium", "pass"), ("moli", "fail")):
        evidence = tmp_path / "tasks" / "a" / engine / "attempt-1"
        evidence.mkdir(parents=True)
        if status == "pass":
            for name in ("attempt.json", "network.har", "final.png", "final-page.json"):
                (evidence / name).write_text("ok")
        rows.append(
            {
                "engine": engine,
                "task_id": "a",
                "attempt": 1,
                "is_retry": False,
                "status": status,
                "evidence_status": status,
                "evidence_directory": str(evidence),
                "duration_ms": 100,
                "resource": {},
            }
        )
    retry_evidence = tmp_path / "tasks" / "a" / "moli" / "attempt-2"
    retry_evidence.mkdir()
    rows.append(
        {
            **rows[-1],
            "attempt": 2,
            "is_retry": True,
            "evidence_directory": str(retry_evidence),
        }
    )
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
    (tmp_path / "summary.json").write_text(
        json.dumps(summarize_attempts(rows, engines=("chromium", "moli")))
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "suite": [{"task_id": "a"}],
                "protocol": {
                    "engines": ["chromium", "moli"],
                    "max_failure_retries": 1,
                    "required_attempt_evidence": [
                        "attempt.json",
                        "network.har",
                        "final.png",
                        "final-page.json",
                    ],
                },
            }
        )
    )
    assert module.verify(tmp_path)["retry_samples"] == 1

    rows[0]["evidence_directory"] = str(retry_evidence)
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
    with pytest.raises(ValueError, match="evidence path differs"):
        module.verify(tmp_path)
    rows[0]["evidence_directory"] = str(tmp_path / "tasks" / "a" / "chromium" / "attempt-1")
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))

    rows[-1]["is_retry"] = False
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
    with pytest.raises(ValueError, match="duplicate primary sample"):
        module.verify(tmp_path)

    rows[-1]["is_retry"] = True
    (tmp_path / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
    (tmp_path / "tasks" / "a" / "chromium" / "attempt-1" / "network.har").unlink()
    with pytest.raises(ValueError, match="missing network.har"):
        module.verify(tmp_path)
