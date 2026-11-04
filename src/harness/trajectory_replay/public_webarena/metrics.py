"""Public WebArena Trajectory Replay result aggregation.

The final declared attempt is each task's result, latency measures only the
trajectory, and every comparison engine has a ratio against the baseline.
"""

from __future__ import annotations

from typing import Any

from harness.trajectory_replay.core import metrics as core
from harness.trajectory_replay.core.metrics import ResultPolicy, Row


def _execution_duration(row: Row) -> float | int:
    """Return only measured trajectory time, excluding reset, login, and page setup."""
    value = row.get("execution_duration_ms", row.get("duration_ms"))
    if not isinstance(value, (int, float)):
        raise ValueError("attempt is missing execution duration")
    return value


def _passing_final_attempts(rows: list[Row]) -> list[Row]:
    final_by_task = {str(row["task_id"]): row for row in rows}
    return [row for row in final_by_task.values() if core.passed(row, "result")]


_POLICY = ResultPolicy(
    logical_passed=lambda attempts, dimension: core.passed(attempts[-1], dimension),
    successful_rows=_passing_final_attempts,
    comparison_rows=lambda rows: rows,
    duration=_execution_duration,
    attempt_timing=lambda row: {
        "execution_duration_ms": row.get("execution_duration_ms", row.get("duration_ms"))
    },
    attempt_evidence=lambda row: {
        "evidence_status": row.get("evidence_status", row.get("status")),
        "network_har": row.get("network_har") or {},
        "final_page_evidence": row.get("final_page_evidence") or {},
        "environment_reset": row.get("environment_reset"),
    },
    relative_ratios=True,
)


def summarize_attempts(rows: list[Row], *, engines: tuple[str, ...]) -> dict[str, Any]:
    return core.summarize_attempts(rows, engines=engines, policy=_POLICY)
