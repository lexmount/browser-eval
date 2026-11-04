"""Curated Trajectory Replay result aggregation.

A task passes when any declared attempt passes, and latency and resource
comparisons measure first attempts.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from harness.trajectory_replay.core import metrics as core
from harness.trajectory_replay.core.metrics import ResultPolicy, Row


def _first_attempts(rows: list[Row]) -> list[Row]:
    return [row for row in rows if not row.get("is_retry")]


_POLICY = ResultPolicy(
    logical_passed=lambda attempts, dimension: any(
        core.passed(row, dimension) for row in attempts
    ),
    successful_rows=lambda rows: [
        row for row in _first_attempts(rows) if core.passed(row, "result")
    ],
    comparison_rows=_first_attempts,
    duration=lambda row: row["duration_ms"],
)


def summarize_attempts(rows: list[Row], *, engines: tuple[str, ...]) -> dict[str, Any]:
    # paired_ratios describes exactly two engines; any other set needs baseline ratios.
    policy = replace(_POLICY, relative_ratios=len(engines) != 2)
    return core.summarize_attempts(rows, engines=engines, policy=policy)
