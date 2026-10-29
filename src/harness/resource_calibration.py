"""Pair profiler-on/off calls and verify their shared experimental provenance.

Derived from Lexmount/Lexbench-Headless-Browser, Apache-2.0:
https://github.com/lexmount/Lexbench-Headless-Browser/blob/fd8d64c7623d46cd165607f216a0e28967d03040/runner/resources.py
Upstream file SHA-256: 728404baad00acde3670ea1d1b86d4f6185dffc1561c7018cab72c3b6f12d963
The extraction retains the upstream calculation and result schema; input
validation and symmetric pairing checks are maintained in this repository.
License: harness/licenses/Lexbench-Apache-2.0.txt
"""

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
import statistics
from typing import Any


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return float(ordered[min(rank - 1, len(ordered) - 1)])


def _declared_calls(manifest: dict[str, Any] | None) -> set[tuple] | None:
    if not manifest:
        return None
    tasks = manifest.get("resolved_tasks")
    engines = manifest.get("selected_engines")
    repeats = manifest.get("k_runs")
    if (
        not isinstance(tasks, list)
        or not tasks
        or not isinstance(engines, list)
        or not engines
        or isinstance(repeats, bool)
        or not isinstance(repeats, int)
        or repeats < 1
    ):
        return None
    task_ids = [task.get("task_id") if isinstance(task, dict) else None for task in tasks]
    if (
        any(not isinstance(value, str) or not value for value in task_ids + engines)
        or len(set(task_ids)) != len(task_ids)
        or len(set(engines)) != len(engines)
    ):
        return None
    return {
        (task_id, engine, attempt)
        for task_id in task_ids
        for engine in engines
        for attempt in range(1, repeats + 1)
    }


def duration_calibration(
    profiled_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    threshold_pct: float,
    *,
    profiled_manifest: dict[str, Any] | None = None,
    baseline_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        isinstance(threshold_pct, bool)
        or not isinstance(threshold_pct, (int, float))
        or not math.isfinite(threshold_pct)
        or threshold_pct < 0
    ):
        raise ValueError("Calibration threshold must be finite and nonnegative")
    for rows in (profiled_rows, baseline_rows):
        for row in rows:
            attempt = row.get("attempt")
            if (
                any(
                    not isinstance(row.get(key), str) or not row[key]
                    for key in ("task_id", "engine")
                )
                or isinstance(attempt, bool)
                or not isinstance(attempt, int)
                or attempt < 1
            ):
                raise ValueError(
                    "Calibration call identity must name a task, engine and positive integer attempt"
                )
            duration = row.get("duration_ms")
            collection = (row.get("resource") or {}).get("collection_wall_ms")
            values = [duration] + ([collection] if collection is not None else [])
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                for value in values
            ):
                raise ValueError("Calibration measurement must be finite and nonnegative")
    baseline = {
        (row.get("task_id"), row.get("engine"), int(row.get("attempt", 0))): row
        for row in baseline_rows
    }
    duration_deltas: list[float] = []
    collection_wall_deltas: list[float] = []
    by_engine_pairs: dict[str, dict[str, Any]] = {}
    status_mismatches = 0
    matched = 0
    for row in profiled_rows:
        key = (row.get("task_id"), row.get("engine"), int(row.get("attempt", 0)))
        other = baseline.get(key)
        if other is None:
            continue
        engine = str(row.get("engine") or "unknown")
        engine_pairs = by_engine_pairs.setdefault(
            engine,
            {
                "matched_attempts": 0,
                "status_mismatches": 0,
                "duration_deltas": [],
                "collection_wall_deltas": [],
            },
        )
        matched += 1
        engine_pairs["matched_attempts"] += 1
        if row.get("status") != other.get("status"):
            status_mismatches += 1
            engine_pairs["status_mismatches"] += 1
        current_ms = float(row.get("duration_ms") or 0)
        baseline_ms = float(other.get("duration_ms") or 0)
        if baseline_ms > 0:
            duration_delta = (current_ms / baseline_ms - 1.0) * 100.0
            duration_deltas.append(duration_delta)
            engine_pairs["duration_deltas"].append(duration_delta)
            collection_ms = (row.get("resource") or {}).get("collection_wall_ms")
            if isinstance(collection_ms, (int, float)):
                collection_delta = (float(collection_ms) / baseline_ms - 1.0) * 100.0
                collection_wall_deltas.append(collection_delta)
                engine_pairs["collection_wall_deltas"].append(collection_delta)
    median_delta = float(statistics.median(duration_deltas)) if duration_deltas else None
    p95_delta = _percentile(duration_deltas, 95)
    median_collection_delta = (
        float(statistics.median(collection_wall_deltas)) if collection_wall_deltas else None
    )
    p95_collection_delta = _percentile(collection_wall_deltas, 95)
    mismatch_rate = status_mismatches / matched if matched else None
    calibration_by_engine: dict[str, Any] = {}
    engine_gates: list[bool] = []
    for engine, pairs in sorted(by_engine_pairs.items()):
        engine_matched = int(pairs["matched_attempts"])
        engine_status_mismatches = int(pairs["status_mismatches"])
        engine_duration = list(pairs["duration_deltas"])
        engine_collection = list(pairs["collection_wall_deltas"])
        engine_duration_median = (
            float(statistics.median(engine_duration)) if engine_duration else None
        )
        engine_collection_median = (
            float(statistics.median(engine_collection)) if engine_collection else None
        )
        engine_mismatch_rate = engine_status_mismatches / engine_matched if engine_matched else None
        engine_acceptable = bool(
            engine_matched
            and len(engine_duration) == engine_matched
            and len(engine_collection) == engine_matched
            and engine_duration_median is not None
            and engine_duration_median <= float(threshold_pct)
            and engine_collection_median is not None
            and engine_collection_median <= float(threshold_pct)
            and (engine_mismatch_rate or 0.0) <= 0.01
        )
        engine_gates.append(engine_acceptable)
        calibration_by_engine[engine] = {
            "matched_attempts": engine_matched,
            "duration_pairs": len(engine_duration),
            "collection_wall_pairs": len(engine_collection),
            "median_duration_delta_pct": engine_duration_median,
            "p95_duration_delta_pct": _percentile(engine_duration, 95),
            "median_collection_wall_delta_pct": engine_collection_median,
            "p95_collection_wall_delta_pct": _percentile(engine_collection, 95),
            "status_mismatches": engine_status_mismatches,
            "status_mismatch_rate": engine_mismatch_rate,
            "acceptable": engine_acceptable,
        }
    expected_calibration_engines = set(
        (profiled_manifest or {}).get("selected_engines")
        or [str(row.get("engine")) for row in profiled_rows]
    )
    complete_pairing = (
        matched == len(profiled_rows)
        and matched == len(baseline_rows)
        and len(baseline) == len(baseline_rows)
        and len(
            {
                (row.get("task_id"), row.get("engine"), int(row.get("attempt", 0)))
                for row in profiled_rows
            }
        )
        == len(profiled_rows)
        and set(by_engine_pairs) == expected_calibration_engines
        and set(baseline) == _declared_calls(profiled_manifest)
        and set(baseline) == _declared_calls(baseline_manifest)
    )
    provenance_mismatches: list[str] = []
    provenance_checks: dict[str, bool] = {}
    if profiled_manifest is None or baseline_manifest is None:
        provenance_mismatches.append("run_manifest")
        provenance_checks["run_manifest"] = False
    else:
        task_signature = lambda manifest: sorted(
            (
                str(task.get("task_id")),
                str(task.get("sha256")),
            )
            for task in (manifest.get("resolved_tasks") or [])
            if isinstance(task, dict)
        )
        checks = {
            "corpus": task_signature(profiled_manifest) == task_signature(baseline_manifest),
            "seed": profiled_manifest.get("seed") == baseline_manifest.get("seed"),
            "k_runs": profiled_manifest.get("k_runs") == baseline_manifest.get("k_runs"),
            "selected_engines": sorted(profiled_manifest.get("selected_engines") or [])
            == sorted(baseline_manifest.get("selected_engines") or []),
            "score_mode": profiled_manifest.get("score_mode")
            == baseline_manifest.get("score_mode"),
            "jobs": (profiled_manifest.get("runner") or {}).get("jobs")
            == (baseline_manifest.get("runner") or {}).get("jobs"),
            "browser_reuse": (profiled_manifest.get("runner") or {}).get("browser_reuse")
            == (baseline_manifest.get("runner") or {}).get("browser_reuse"),
            "engine_pins": profiled_manifest.get("engines") == baseline_manifest.get("engines"),
            "harness_pins": (profiled_manifest.get("runner") or {}).get("harness_pins")
            == (baseline_manifest.get("runner") or {}).get("harness_pins"),
            "runner_source": bool((profiled_manifest.get("runner") or {}).get("source"))
            and (profiled_manifest.get("runner") or {}).get("source")
            == (baseline_manifest.get("runner") or {}).get("source"),
            "host_provenance": profiled_manifest.get("host") == baseline_manifest.get("host"),
            "balanced_order": (
                (profiled_manifest.get("resource_profile") or {}).get("engine_order")
                == "balanced_rotation"
                and (baseline_manifest.get("resource_profile") or {}).get("engine_order")
                == "balanced_rotation"
                and bool(
                    (profiled_manifest.get("resource_profile") or {}).get("engine_order_algorithm")
                )
                and (profiled_manifest.get("resource_profile") or {}).get("engine_order_algorithm")
                == (baseline_manifest.get("resource_profile") or {}).get("engine_order_algorithm")
            ),
            "profile_modes": (
                (profiled_manifest.get("resource_profile") or {}).get("mode") == "engine"
                and (baseline_manifest.get("resource_profile") or {}).get("mode") == "baseline"
            ),
        }
        provenance_checks.update(checks)
        provenance_mismatches.extend(
            name for name, matched_check in checks.items() if not matched_check
        )
    acceptable = bool(
        matched
        and complete_pairing
        and median_delta is not None
        and median_delta <= float(threshold_pct)
        and len(collection_wall_deltas) == matched
        and median_collection_delta is not None
        and median_collection_delta <= float(threshold_pct)
        and (mismatch_rate or 0.0) <= 0.01
        and bool(engine_gates)
        and all(engine_gates)
        and not provenance_mismatches
    )
    return {
        "matched_attempts": matched,
        "profiled_attempts": len(profiled_rows),
        "complete_pairing": complete_pairing,
        "duration_pairs": len(duration_deltas),
        "median_duration_delta_pct": median_delta,
        "p95_duration_delta_pct": p95_delta,
        "collection_wall_pairs": len(collection_wall_deltas),
        "median_collection_wall_delta_pct": median_collection_delta,
        "p95_collection_wall_delta_pct": p95_collection_delta,
        "status_mismatches": status_mismatches,
        "status_mismatch_rate": mismatch_rate,
        "by_engine": calibration_by_engine,
        "provenance_checks": provenance_checks,
        "provenance_mismatches": provenance_mismatches,
        "max_observer_effect_pct": float(threshold_pct),
        "acceptable": acceptable,
    }
