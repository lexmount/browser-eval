"""Independently audit published aggregates against raw attempts (stdlib only)."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

REQUIRED_METRICS = frozenset(
    """
duration_ms tool_output_tokens cpu_ms cpu_average_utilization_pct
cpu_sampled_utilization_p95_pct cpu_sampled_utilization_peak_pct
memory_baseline_bytes memory_mean_bytes memory_p50_bytes memory_p95_bytes
memory_peak_bytes memory_end_bytes memory_end_delta_bytes memory_retained_bytes
memory_peak_delta_bytes memory_mib_seconds network_rx_bytes network_tx_bytes
network_rx_bytes_per_second network_tx_bytes_per_second
network_rx_rate_p95_bytes_per_second network_rx_rate_peak_bytes_per_second
network_tx_rate_p95_bytes_per_second network_tx_rate_peak_bytes_per_second
pids_current_mean pids_current_p95 pids_current_peak
""".split()
)


def verify(artifacts: Path) -> dict:
    rows = [
        json.loads(line)
        for line in (artifacts / "attempts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    summary = json.loads((artifacts / "summary.json").read_text())
    primary = {}
    for row in rows:
        if not row.get("is_retry"):
            key = (row["task_id"], row["engine"])
            if key in primary:
                raise ValueError(f"duplicate primary sample: {key}")
            primary[key] = row
    tasks = sorted({r["task_id"] for r in rows})
    paired_tasks = [
        task
        for task in tasks
        if all(
            (row := primary.get((task, engine))) is not None
            and row.get("result_status", row["status"]) == "pass"
            for engine in ("chromium", "moli")
        )
    ]
    comparison = summary["resource_comparison"]
    if set(comparison.get("by_engine", {})) != {"chromium", "moli"} or set(
        summary.get("functional", {})
    ) != {"chromium", "moli"}:
        raise ValueError("required provider set mismatch")
    for engine in ("chromium", "moli"):
        if set(comparison["by_engine"][engine]) != REQUIRED_METRICS:
            raise ValueError(f"{engine}: required metric set mismatch")
    if set(comparison.get("paired_ratios", {}).get("metrics", {})) != REQUIRED_METRICS:
        raise ValueError("paired: required metric set mismatch")
    if len(paired_tasks) != comparison["intersection_attempts"]:
        raise ValueError("successful task intersection mismatch")

    def equal(actual, expected, label):
        if actual is None or expected is None:
            match = actual is expected
        else:
            match = math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9)
        if not match:
            raise ValueError(f"{label}: {actual!r} != {expected!r}")

    def check(values, published, label):
        ordered = sorted(values)
        calculated = {
            "n": len(values),
            "p50": statistics.median(values) if values else None,
            "p95": ordered[math.ceil(len(values) * 0.95) - 1] if values else None,
            "mean": statistics.fmean(values) if values else None,
        }
        for key, value in calculated.items():
            equal(published.get(key), value, label + "." + key)

    def value(task, engine, metric):
        row = primary[(task, engine)]
        return (
            row.get(metric)
            if metric in {"duration_ms", "tool_output_tokens"}
            else row.get("resource", {}).get(metric)
        )

    checked = 0
    for engine, metrics in comparison["by_engine"].items():
        for metric, published in metrics.items():
            values = [
                v
                for task in paired_tasks
                if isinstance((v := value(task, engine, metric)), (int, float))
            ]
            check(values, published, f"{engine}.{metric}")
            checked += 1
    for metric, published in comparison["paired_ratios"]["metrics"].items():
        ratios = []
        for task in paired_tasks:
            c, m = value(task, "chromium", metric), value(task, "moli", metric)
            if isinstance(c, (int, float)) and c > 0 and isinstance(m, (int, float)):
                ratios.append((m / c, task, c, m))
        check([r[0] for r in ratios], published, "paired." + metric)
        records = published.get("values", [])
        if len(records) != len(ratios):
            raise ValueError(f"paired.{metric}: sample count mismatch")
        for expected, actual in zip(sorted(ratios), records, strict=True):
            ratio, task, c, m = expected
            if actual["task_id"] != task:
                raise ValueError(f"paired.{metric}: task identity mismatch")
            for key, number in (("ratio", ratio), ("denominator", c), ("numerator", m)):
                equal(actual[key], number, f"paired.{metric}.{task}.{key}")
        checked += 1
    for engine, published in summary["functional"].items():
        equal(published["tasks"], len(tasks), engine + ".tasks")
        for dimension in ("result", "combined"):
            count = sum(
                any(
                    r["task_id"] == task
                    and r["engine"] == engine
                    and r.get(dimension + "_status", r["status"]) == "pass"
                    for r in rows
                )
                for task in tasks
            )
            equal(published[dimension + "_final_passes"], count, engine + "." + dimension)
    return {
        "status": "pass",
        "raw_attempts": len(rows),
        "paired_tasks": len(paired_tasks),
        "metric_groups_checked": checked,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().artifacts)))
