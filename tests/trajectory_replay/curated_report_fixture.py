"""Synthetic curated Trajectory Replay report for any participant set."""

from __future__ import annotations

from pathlib import Path

from harness.trajectory_replay.core.diagnostics import diagnose_summary
from harness.trajectory_replay.metrics import summarize_attempts
from harness.trajectory_replay.report import write_latest_report

_TASKS = (("static-title", "simple"), ("dynamic-list", "medium"), ("modal-form", "complex"))


def _row(task_id: str, complexity: str, engine: str, position: int, failed: bool) -> dict:
    status = "fail" if failed else "pass"
    scale = position + 1
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "engine": engine,
        "complexity": complexity,
        "step_count": 2,
        "tags": ["fixture"],
        "attempt": 1,
        "is_retry": False,
        "status": status,
        "result_status": status,
        "process_status": status,
        "combined_status": status,
        "duration_ms": 1000.0 * scale + len(task_id),
        "tool_output_tokens": 50 * scale,
        "error": "Timeout waiting for selector #answer" if failed else "",
        "trace": [
            {"action": "navigate", "status": "ok", "duration_ms": 10.0, "output": "opened"},
            {
                "action": "get_text",
                "status": "error" if failed else "ok",
                "duration_ms": 20.0,
                "output": "" if failed else "answer",
            },
        ],
        "validation": [],
        "process_validation": {},
        "resource": {
            "cpu_ms": 100.0 * scale,
            "cpu_average_utilization_pct": 10.0 * scale,
            "cpu_sampled_utilization_p95_pct": 15.0 * scale,
            "memory_mean_bytes": 1048576.0 * 100 * scale,
            "memory_p95_bytes": 1048576.0 * 120 * scale,
            "memory_peak_bytes": 1048576.0 * 130 * scale,
            "memory_retained_bytes": 1048576.0 * 5 * scale,
            "memory_mib_seconds": 90.0 * scale,
            "network_rx_bytes": 1024.0 * 40 * scale,
            "network_tx_bytes": 1024.0 * 4 * scale,
            "network_rx_bytes_per_second": 1024.0 * 20 * scale,
            "network_rx_rate_p95_bytes_per_second": 1024.0 * 30 * scale,
            "network_tx_rate_p95_bytes_per_second": 1024.0 * 3 * scale,
        },
    }


def build_curated_report(
    reports: Path,
    engines: tuple[str, ...],
    failures: dict[str, tuple[str, ...]],
) -> dict:
    """Write the report for ``engines``; ``failures`` names each engine's failed tasks."""
    rows = [
        _row(task_id, complexity, engine, position, task_id in failures.get(engine, ()))
        for task_id, complexity in _TASKS
        for position, engine in enumerate(engines)
    ]
    summary = summarize_attempts(rows, engines=engines)
    diagnose_summary(summary, use_llm=False)
    manifest = {
        "run_id": "curated-fixture",
        "created_at": "2026-01-01T00:00:00+00:00",
        "suite_sha256": "a" * 64,
        "artifact_provenance": {"attempts_sha256": "b" * 64, "summary_sha256": "c" * 64},
        "protocol": {
            "sample_interval_ms": 100,
            "post_reset_settle_ms": 1500,
            "max_failure_retries": 0,
            "cpus_per_browser": 2.0,
            "memory_per_browser": "2g",
            "viewport": {"width": 1280, "height": 900},
        },
        "environment": {
            "runner_platform": "fixture",
            "docker_server_version": "29",
            "docker_os": "fixture",
            "browser_versions": {engine: f"{engine} 1.0" for engine in engines},
        },
        "suite": [
            {
                "task_id": task_id,
                "title": f"Task {task_id}",
                "step_count": 2,
                "complexity": complexity,
                "tool_calls": [
                    {"action": "navigate", "url": "https://example.com/", "save_as": "page"},
                    {"action": "get_text", "selector": "#answer", "save_as": "answer"},
                ],
                "ground_truth": {
                    "expected_outputs": {"answer": "answer"},
                    "expected_actions": ["navigate", "get_text"],
                },
                "source": {
                    "kind": "probe",
                    "scenario": task_id,
                    "url": "https://example.com/",
                    "license": "CC0",
                    "coverage": "fixture",
                    "hypothesis": "fixture",
                },
            }
            for task_id, complexity in _TASKS
        ],
    }
    write_latest_report(reports, manifest=manifest, summary=summary)
    return summary
