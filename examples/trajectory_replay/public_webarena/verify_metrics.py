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
    artifacts = artifacts.resolve()
    rows = [
        json.loads(line)
        for line in (artifacts / "attempts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    summary = json.loads((artifacts / "summary.json").read_text())
    manifest_path = artifacts / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    primary = {}
    for row in rows:
        if not row.get("is_retry"):
            key = (row["task_id"], row["engine"])
            if key in primary:
                raise ValueError(f"duplicate primary sample: {key}")
            primary[key] = row
    tasks = sorted({r["task_id"] for r in rows})
    if manifest is not None:
        engines = tuple((manifest.get("protocol") or {}).get("engines") or ())
        declared_tasks = [str(row["task_id"]) for row in manifest.get("suite") or []]
        if not declared_tasks or len(set(declared_tasks)) != len(declared_tasks):
            raise ValueError("manifest suite is empty or contains duplicate tasks")
        if set(declared_tasks) != set(tasks):
            raise ValueError("manifest and attempt task sets differ")
        expected_primary = {(task, engine) for task in declared_tasks for engine in engines}
        if set(primary) != expected_primary:
            raise ValueError("primary task/browser matrix is incomplete")
        max_retries = int((manifest.get("protocol") or {}).get("max_failure_retries", 0))
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault((row["task_id"], row["engine"]), []).append(row)
        for key, attempts in grouped.items():
            attempts.sort(key=lambda row: int(row["attempt"]))
            if attempts[0].get("attempt") != 1 or attempts[0].get("is_retry") is True:
                raise ValueError(f"invalid primary attempt metadata: {key}")
            if len(attempts) > max_retries + 1:
                raise ValueError(f"retry count exceeds the declared limit: {key}")
            for index, row in enumerate(attempts, 1):
                if row.get("attempt") != index or bool(row.get("is_retry")) != (index > 1):
                    raise ValueError(f"invalid retry sequence: {key}")
                if index > 1 and attempts[index - 2].get("status") == "pass":
                    raise ValueError(f"retry follows a passing result: {key}")
                evidence_dir = Path(str(row.get("evidence_directory") or "")).resolve()
                expected_dir = (
                    artifacts / "tasks" / key[0] / key[1] / f"attempt-{index}"
                ).resolve()
                if (
                    not expected_dir.is_relative_to(artifacts / "tasks")
                    or evidence_dir != expected_dir
                ):
                    raise ValueError(f"attempt evidence path differs from manifest scope: {key}")
                if row.get("status") == "pass":
                    if row.get("evidence_status") != "pass":
                        raise ValueError(f"passing attempt lacks complete evidence: {key}")
                    if not evidence_dir.is_dir():
                        raise ValueError(f"passing attempt evidence directory is missing: {key}")
                    for name in (manifest.get("protocol") or {}).get(
                        "required_attempt_evidence", ()
                    ):
                        evidence_file = (evidence_dir / name).resolve()
                        if evidence_file.parent != evidence_dir or not evidence_file.is_file():
                            raise ValueError(f"passing attempt is missing {name}: {key}")
    else:
        engines = tuple(summary.get("functional") or {})
    final = {}
    for row in rows:
        final[(row["task_id"], row["engine"])] = row
    intersection_tasks = [
        task
        for task in tasks
        if all(
            (row := final.get((task, engine))) is not None
            and row.get("result_status", row["status"]) == "pass"
            for engine in engines
        )
    ]
    comparison = summary["resource_comparison"]
    if tuple(comparison.get("by_engine", {})) != engines or tuple(
        summary.get("functional", {})
    ) != engines:
        raise ValueError("required provider set mismatch")
    for engine in engines:
        if set(comparison["by_engine"][engine]) != REQUIRED_METRICS:
            raise ValueError(f"{engine}: required metric set mismatch")
    if len(intersection_tasks) != comparison.get(
        "intersection_results", comparison["intersection_attempts"]
    ):
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
        row = final[(task, engine)]
        if metric == "duration_ms":
            return row.get("execution_duration_ms", row.get("duration_ms"))
        return (
            row.get(metric)
            if metric == "tool_output_tokens"
            else row.get("resource", {}).get(metric)
        )

    checked = 0
    for engine, metrics in comparison["by_engine"].items():
        for metric, published in metrics.items():
            values = [
                v
                for task in intersection_tasks
                if isinstance((v := value(task, engine, metric)), (int, float))
            ]
            check(values, published, f"{engine}.{metric}")
            checked += 1
    for engine, published in summary["functional"].items():
        equal(published["tasks"], len(tasks), engine + ".tasks")
        for dimension in ("result", "combined"):
            count = sum(
                final[(task, engine)].get(dimension + "_status", final[(task, engine)]["status"])
                == "pass"
                for task in tasks
            )
            equal(published[dimension + "_final_passes"], count, engine + "." + dimension)
    return {
        "status": "pass",
        "raw_attempts": len(rows),
        "intersection_tasks": len(intersection_tasks),
        "metric_groups_checked": checked,
        "primary_samples": len(primary),
        "retry_samples": len(rows) - len(primary),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/browser_use/latest"))
    print(json.dumps(verify(parser.parse_args().artifacts)))
