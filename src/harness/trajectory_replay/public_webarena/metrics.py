"""Public WebArena Trajectory Replay result aggregation.

Resource collection lives in harness.resource_accounting so every isolated
benchmark uses one accounting contract.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import Any

from harness.resource_accounting import (
    CounterSnapshot,
    DockerStatsResources,
    LinuxContainerResources,
    ResourceSample,
    ResourceSampler,
    delta_counters,
    parse_cpu_stat,
    parse_network_dev,
)

def _metric(values: Iterable[int | float]) -> dict[str, int | float | None]:
    numbers = [float(value) for value in values]
    if not numbers:
        return {"n": 0, "p50": None, "median": None, "mean": None, "p95": None}
    ordered = sorted(numbers)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95 + 0.999) - 1))
    p50 = statistics.median(numbers)
    return {
        "n": len(numbers),
        "p50": p50,
        "median": p50,
        "mean": statistics.fmean(numbers),
        "p95": ordered[index],
    }


def _passed(row: dict[str, Any], dimension: str) -> bool:
    field = f"{dimension}_status"
    value = row.get(field)
    if value is not None:
        return value == "pass"
    return row.get("status") == "pass"


def _execution_duration(row: dict[str, Any]) -> float | int:
    """Return only measured trajectory time, excluding reset, login, and page setup."""
    value = row.get("execution_duration_ms", row.get("duration_ms"))
    if not isinstance(value, (int, float)):
        raise ValueError("attempt is missing execution duration")
    return value


def _summarize_core(rows: list[dict[str, Any]], *, engines: tuple[str, ...]) -> dict[str, Any]:
    functional: dict[str, Any] = {}
    for engine in engines:
        engine_rows = [row for row in rows if row.get("engine") == engine]
        task_ids = list(dict.fromkeys(str(row["task_id"]) for row in engine_rows))
        first_rows = [row for row in engine_rows if not row.get("is_retry")]
        result_first_passes = sum(_passed(row, "result") for row in first_rows)
        combined_first_passes = sum(_passed(row, "combined") for row in first_rows)
        result_final_passes = 0
        combined_final_passes = 0
        result_recovered = 0
        combined_recovered = 0
        for task_id in task_ids:
            attempts = [row for row in engine_rows if str(row["task_id"]) == task_id]
            result_passed = _passed(attempts[-1], "result")
            combined_passed = _passed(attempts[-1], "combined")
            result_final_passes += int(result_passed)
            combined_final_passes += int(combined_passed)
            result_recovered += int(
                bool(attempts) and not _passed(attempts[0], "result") and result_passed
            )
            combined_recovered += int(
                bool(attempts) and not _passed(attempts[0], "combined") and combined_passed
            )
        functional[engine] = {
            "tasks": len(task_ids),
            "attempts": len(task_ids),
            "physical_attempts": len(engine_rows),
            "retries": sum(bool(row.get("is_retry")) for row in engine_rows),
            "result_first_passes": result_first_passes,
            "result_first_pass_rate": result_first_passes / len(task_ids) if task_ids else None,
            "result_recovered": result_recovered,
            "result_final_passes": result_final_passes,
            "result_final_pass_rate": (result_final_passes / len(task_ids) if task_ids else None),
            "combined_first_passes": combined_first_passes,
            "combined_first_pass_rate": (
                combined_first_passes / len(task_ids) if task_ids else None
            ),
            "combined_recovered": combined_recovered,
            "combined_final_passes": combined_final_passes,
            "combined_final_pass_rate": (
                combined_final_passes / len(task_ids) if task_ids else None
            ),
            # Backward-compatible aliases use the primary, result-only metric.
            "first_passes": result_first_passes,
            "first_pass_rate": result_first_passes / len(task_ids) if task_ids else None,
            "recovered": result_recovered,
            "final_passes": result_final_passes,
            "passes": result_final_passes,
            "final_pass_rate": result_final_passes / len(task_ids) if task_ids else None,
            "pass_rate": result_final_passes / len(task_ids) if task_ids else None,
            "duration_ms": _metric(
                _execution_duration(attempts[-1])
                for task_id in task_ids
                if (attempts := [row for row in engine_rows if str(row["task_id"]) == task_id])
                and _passed(attempts[-1], "result")
            ),
            "tool_output_tokens": _metric(
                attempts[-1]["tool_output_tokens"]
                for task_id in task_ids
                if (attempts := [row for row in engine_rows if str(row["task_id"]) == task_id])
                and _passed(attempts[-1], "result")
                and isinstance(attempts[-1].get("tool_output_tokens"), (int, float))
            ),
        }
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["task_id"]), {})[str(row["engine"])] = row
    intersection_items = [
        (task_id, bucket)
        for task_id, bucket in grouped.items()
        if all(engine in bucket and _passed(bucket[engine], "result") for engine in engines)
    ]
    intersection = [bucket for _, bucket in intersection_items]
    metric_names = (
        "cpu_ms",
        "cpu_average_utilization_pct",
        "cpu_sampled_utilization_p95_pct",
        "cpu_sampled_utilization_peak_pct",
        "memory_baseline_bytes",
        "memory_mean_bytes",
        "memory_p50_bytes",
        "memory_p95_bytes",
        "memory_peak_bytes",
        "memory_end_bytes",
        "memory_end_delta_bytes",
        "memory_retained_bytes",
        "memory_peak_delta_bytes",
        "memory_mib_seconds",
        "network_rx_bytes",
        "network_tx_bytes",
        "network_rx_bytes_per_second",
        "network_tx_bytes_per_second",
        "network_rx_rate_p95_bytes_per_second",
        "network_rx_rate_peak_bytes_per_second",
        "network_tx_rate_p95_bytes_per_second",
        "network_tx_rate_peak_bytes_per_second",
        "pids_current_mean",
        "pids_current_p95",
        "pids_current_peak",
    )
    by_engine: dict[str, Any] = {}
    for engine in engines:
        engine_rows = [bucket[engine] for bucket in intersection]
        values = {
            name: _metric(
                resource[name]
                for row in engine_rows
                if isinstance((resource := row.get("resource") or {}).get(name), (int, float))
            )
            for name in metric_names
        }
        values["duration_ms"] = _metric(_execution_duration(row) for row in engine_rows)
        values["tool_output_tokens"] = _metric(
            row["tool_output_tokens"]
            for row in engine_rows
            if isinstance(row.get("tool_output_tokens"), (int, float))
        )
        by_engine[engine] = values
    baseline_engine = engines[0] if engines else None
    relative_ratios_by_engine: dict[str, Any] = {}
    for numerator_engine in engines[1:]:
        engine_ratios: dict[str, Any] = {}
        ratio_metric_names = (*metric_names, "duration_ms", "tool_output_tokens")
        for name in ratio_metric_names:
            ratios: list[float] = []
            details: list[dict[str, float | str]] = []
            for task_id, bucket in intersection_items:
                denominator_row = bucket[baseline_engine]
                numerator_row = bucket[numerator_engine]
                if name == "duration_ms":
                    denominator = _execution_duration(denominator_row)
                    numerator = _execution_duration(numerator_row)
                elif name == "tool_output_tokens":
                    denominator = denominator_row.get(name)
                    numerator = numerator_row.get(name)
                else:
                    denominator = (denominator_row.get("resource") or {}).get(name)
                    numerator = (numerator_row.get("resource") or {}).get(name)
                if (
                    isinstance(denominator, (int, float))
                    and denominator > 0
                    and isinstance(numerator, (int, float))
                ):
                    ratio = float(numerator) / float(denominator)
                    ratios.append(ratio)
                    details.append(
                        {
                            "task_id": task_id,
                            "denominator": float(denominator),
                            "numerator": float(numerator),
                            "ratio": ratio,
                        }
                    )
            engine_ratios[name] = {
                **_metric(ratios),
                "values": sorted(
                    details, key=lambda item: (float(item["ratio"]), str(item["task_id"]))
                ),
            }
        relative_ratios_by_engine[numerator_engine] = engine_ratios
    paired_ratios = (
        relative_ratios_by_engine.get(engines[1], {}) if len(engines) == 2 else {}
    )
    return {
        "functional": functional,
        "resource_comparison": {
            "scope": "warm browser container only",
            "functional_score_combined": False,
            "intersection_results": len(intersection),
            # Kept for older evidence readers. Each item is now the final declared result.
            "intersection_attempts": len(intersection),
            "by_engine": by_engine,
            "relative_ratios": {
                "baseline_engine": baseline_engine,
                "formula": "per_task_ratio = comparison_engine / baseline_engine",
                "p50_method": "statistics.median",
                "p95_method": "nearest-rank: sorted_values[ceil(0.95*n)-1]",
                "mean_method": "statistics.fmean",
                "by_engine": relative_ratios_by_engine,
            },
            "paired_ratios": {
                "numerator_engine": engines[1] if len(engines) == 2 else None,
                "denominator_engine": engines[0] if len(engines) == 2 else None,
                "formula": "per_task_ratio = numerator / denominator",
                "p50_method": "statistics.median",
                "p95_method": "nearest-rank: sorted_values[ceil(0.95*n)-1]",
                "mean_method": "statistics.fmean",
                "metrics": paired_ratios,
            },
        },
    }


def summarize_attempts(rows: list[dict[str, Any]], *, engines: tuple[str, ...]) -> dict[str, Any]:
    summary = _summarize_core(rows, engines=engines)
    complexities = sorted({str(row["complexity"]) for row in rows if row.get("complexity")})
    summary["by_complexity"] = {
        complexity: _summarize_core(
            [row for row in rows if row.get("complexity") == complexity],
            engines=engines,
        )
        for complexity in complexities
    }
    task_ids = list(dict.fromkeys(str(row["task_id"]) for row in rows))
    summary["by_task"] = {}
    for task_id in task_ids:
        task_rows = [row for row in rows if str(row.get("task_id")) == task_id]
        first = task_rows[0]
        task_summary = _summarize_core(task_rows, engines=engines)
        task_summary.update(
            {
                "title": first.get("title", task_id),
                "complexity": first.get("complexity"),
                "step_count": first.get("step_count"),
                "tags": first.get("tags") or [],
                "representative_errors": {
                    engine: list(
                        dict.fromkeys(
                            str(row.get("error"))
                            for row in task_rows
                            if row.get("engine") == engine and row.get("error")
                        )
                    )[:3]
                    for engine in engines
                },
                "failures": {
                    engine: [
                        {
                            "attempt": row.get("attempt"),
                            "is_retry": row.get("is_retry", False),
                            "error": row.get("error", ""),
                            "trace": row.get("trace") or [],
                            "validation": row.get("validation") or [],
                            "process_validation": row.get("process_validation") or {},
                            "result_status": row.get("result_status", row.get("status")),
                            "process_status": row.get("process_status", row.get("status")),
                            "combined_status": row.get("combined_status", row.get("status")),
                        }
                        for row in task_rows
                        if row.get("engine") == engine and row.get("status") == "fail"
                    ]
                    for engine in engines
                },
                "attempts": {
                    engine: [
                        {
                            "attempt": row.get("attempt"),
                            "is_retry": row.get("is_retry", False),
                            "status": row.get("status"),
                            "duration_ms": row.get("duration_ms"),
                            "execution_duration_ms": row.get(
                                "execution_duration_ms", row.get("duration_ms")
                            ),
                            "resource": row.get("resource") or {},
                            "tool_output_tokens": row.get("tool_output_tokens"),
                            "error": row.get("error", ""),
                            "trace": row.get("trace") or [],
                            "validation": row.get("validation") or [],
                            "process_validation": row.get("process_validation") or {},
                            "result_status": row.get("result_status", row.get("status")),
                            "process_status": row.get("process_status", row.get("status")),
                            "combined_status": row.get("combined_status", row.get("status")),
                            "evidence_status": row.get("evidence_status", row.get("status")),
                            "network_har": row.get("network_har") or {},
                            "final_page_evidence": row.get("final_page_evidence") or {},
                            "environment_reset": row.get("environment_reset"),
                        }
                        for row in task_rows
                        if row.get("engine") == engine
                    ]
                    for engine in engines
                },
            }
        )
        summary["by_task"][task_id] = task_summary

    summary["retry_policy"] = {
        engine: {
            "retries": summary["functional"][engine]["retries"],
            "result_recovered": summary["functional"][engine]["result_recovered"],
            "combined_recovered": summary["functional"][engine]["combined_recovered"],
        }
        for engine in engines
    }
    return summary
