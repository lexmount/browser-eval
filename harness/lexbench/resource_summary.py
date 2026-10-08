"""Verify complete Linux profiles and aggregate the frozen resource populations."""

from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
from pathlib import Path

from .resource_evidence import digest as sha256

from harness.record_io import load_jsonl
from harness.resource_calibration import duration_calibration
from harness.web_fetch.safety import contained_path


def stats(values: list[float]) -> dict:
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("missing or invalid resource measurement")
    return {
        "n": len(values),
        "sum": math.fsum(values),
        "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p95": statistics.quantiles(values, n=20, method="inclusive")[18],
        "min": min(values),
        "max": max(values),
    }


def summarize(directory: Path) -> dict:
    protocol_path = contained_path(directory, "protocol.json", reject_symlinks=True)
    receipt_path = contained_path(directory, "receipt.json", reject_symlinks=True)
    protocol = json.loads(protocol_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    if (
        protocol["schema"] != "moli_linux_resource_profiles/1"
        or receipt["schema"] != "moli_linux_resource_profiles_receipt/1"
        or receipt["protocol_sha256"] != sha256(protocol_path)
        or receipt["orchestrator_sha256"]
        != sha256(contained_path(directory, "orchestrator.py", reject_symlinks=True))
        or protocol["orchestrator_sha256"] != receipt["orchestrator_sha256"]
        or protocol["memory_metric"] != "process_tree_pss"
        or protocol["attempts"] != 5
        or protocol["jobs"] != 1
        or len(protocol["task_ids"]) != 557
        or len(protocol["configs"]) != 4
        or set(receipt["results"])
        != {
            f"{method}:{phase}"
            for method in protocol["configs"]
            for phase in ("baseline", "engine")
        }
    ):
        raise ValueError("incomplete or changed resource protocol")
    runner = Path(protocol["runner_root"])
    for name, expected_hash in protocol["input_files"].items():
        if sha256(contained_path(runner, name, reject_symlinks=True)) != expected_hash:
            raise ValueError(f"resource runner changed: {name}")
    expected = {(task, attempt) for task in protocol["task_ids"] for attempt in range(1, 6)}
    chart = {(item["task_id"], item["attempt"]) for item in protocol["chart_keys"]}
    chart_tasks = {task for task, _ in chart}
    if (
        len(expected) != 2785
        or len(chart) != len(protocol["chart_keys"])
        or len(chart) != 1045
        or len(chart_tasks) != 209
        or chart != {(task, attempt) for task in chart_tasks for attempt in range(1, 6)}
        or not chart.issubset(expected)
    ):
        raise ValueError("resource population mismatch")
    configurations = {}
    measurements = {}
    for method, config in protocol["configs"].items():
        rows_by_phase, manifests, hosts = {}, {}, {}
        for phase in ("baseline", "engine"):
            binding = receipt["results"][f"{method}:{phase}"]
            run = contained_path(directory, binding["run_id"], reject_symlinks=True)
            if run.resolve().parent != directory.resolve() or run.is_symlink():
                raise ValueError("invalid resource run directory")
            for filename, key in (
                ("run_manifest.json", "run_manifest_sha256"),
                ("results.jsonl", "results_sha256"),
                ("host_summary.json", "host_summary_sha256"),
            ):
                if sha256(contained_path(run, filename, reject_symlinks=True)) != binding[key]:
                    raise ValueError("resource run changed after completion")
            manifest = json.loads((run / "run_manifest.json").read_text())
            rows = load_jsonl(run / "results.jsonl")
            if (
                manifest["completion_status"] != "completed"
                or manifest["resource_profile"]["mode"] != phase
                or manifest["k_runs"] != 5
                or manifest["runner"]["jobs"] != 1
                or collections.Counter((r["task_id"], r["attempt"]) for r in rows)
                != collections.Counter(expected)
            ):
                raise ValueError("resource run does not match the frozen scope")
            for row in rows:
                identity = row["engine_provenance"]
                if (
                    identity["binary_sha256"] != config["sha256"]
                    or identity["layout_enabled"] != (config["layout"] == "on")
                    or ("--layout" in identity["launch_command"]) != (config["layout"] == "on")
                ):
                    raise ValueError("resource build or layout mismatch")
            rows_by_phase[phase] = rows
            manifests[phase] = manifest
            hosts[phase] = json.loads((run / "host_summary.json").read_text())
        rows = rows_by_phase["engine"]
        by_key = {(r["task_id"], r["attempt"]): r for r in rows}
        if any(
            row["resource"].get("pss_peak_bytes") is None
            or row["resource"].get("sample_interval_ms") != 250
            or row["resource"].get("measurement_backend", {}).get("memory_pss")
            != "proc_smaps_rollup"
            or row["resource"].get("measurement_backend", {}).get("cpu") != "proc_tree"
            for row in rows
        ):
            raise ValueError("missing PSS or changed sampling interval")
        metrics = {}
        for population, keys in (
            ("all_predeclared_calls", expected),
            ("frozen_historical_common_pass_calls", chart),
        ):
            metrics[population] = {
                name: stats([by_key[key]["resource"][field] / scale for key in sorted(keys)])
                for name, field, scale in (
                    ("pss_peak_mib", "pss_peak_bytes", 1024**2),
                    ("cpu_time_ms", "cpu_total_ms", 1),
                )
            }
        calibration = duration_calibration(
            rows,
            rows_by_phase["baseline"],
            protocol["max_observer_effect_pct"],
            profiled_manifest=manifests["engine"],
            baseline_manifest=manifests["baseline"],
        )
        configurations[method] = {
            "build": {key: value for key, value in config.items() if key != "binary"},
            "metrics": metrics,
            "calibration": calibration,
            "host": hosts,
            "publishable": bool(calibration["acceptable"])
            and all(not h.get("polluted", True) for h in hosts.values()),
        }
        measurements[method] = [
            {
                "task_id": row["task_id"],
                "attempt": row["attempt"],
                "status": row["status"],
                "resource": {
                    key: row["resource"][key]
                    for key in (
                        "pss_peak_bytes",
                        "cpu_total_ms",
                        "measurement_backend",
                        "sample_interval_ms",
                        "samples_seen",
                        "quality_flags",
                    )
                },
            }
            for row in rows
        ]
    return {
        "schema": "moli_linux_resource_summary/1",
        "protocol_sha256": sha256(protocol_path),
        "receipt_sha256": sha256(receipt_path),
        "protocol": protocol,
        "receipt": receipt,
        "population": {
            "task_ids": protocol["task_ids"],
            "chart_keys": protocol["chart_keys"],
            "attempts_per_task": 5,
        },
        "configurations": configurations,
        "measurements": measurements,
        "publishable": all(c["publishable"] for c in configurations.values()),
    }


def validate_summary(summary: dict, builds: dict) -> None:
    """Verify the public values against every retained call, without local files."""
    population = summary["population"]
    expected = {(task, attempt) for task in population["task_ids"] for attempt in range(1, 6)}
    chart = {(r["task_id"], r["attempt"]) for r in population["chart_keys"]}
    receipt = summary["receipt"]
    if (
        summary["schema"] != "moli_linux_resource_summary/1"
        or summary["publishable"] is not True
        or population["attempts_per_task"] != 5
        or len(population["task_ids"]) != 557
        or len(expected) != 2785
        or len(chart) != len(population["chart_keys"])
        or len(chart) != 1045
        or len({task for task, _ in chart}) != 209
        or not chart.issubset(expected)
        or list(summary["configurations"]) != list(builds)
        or len(builds) != 4
        or set(summary["measurements"]) != set(builds)
        or receipt["schema"] != "moli_linux_resource_profiles_receipt/1"
        or receipt["protocol_sha256"] != summary["protocol_sha256"]
        or set(receipt["results"])
        != {f"{method}:{phase}" for method in builds for phase in ("baseline", "engine")}
        or any(run["rows"] != 2785 for run in receipt["results"].values())
    ):
        raise ValueError("incomplete Linux resource summary")
    for method, build in builds.items():
        config = summary["configurations"][method]
        if (
            any(config["build"].get(key) != value for key, value in build.items())
            or config["build"]["target"] != "aarch64-unknown-linux-gnu"
            or config["build"]["profile"] != "release"
            or config["publishable"] is not True
            or config["calibration"]["acceptable"] is not True
            or config["calibration"]["complete_pairing"] is not True
            or set(config["host"]) != {"baseline", "engine"}
            or any(host.get("polluted", True) for host in config["host"].values())
        ):
            raise ValueError("Linux resource identity or calibration mismatch")
        rows = summary["measurements"][method]
        by_key = {(r["task_id"], r["attempt"]): r for r in rows}
        if len(rows) != len(by_key) or set(by_key) != expected:
            raise ValueError("partial or duplicated Linux resource calls")
        for row in rows:
            resource = row["resource"]
            if (
                resource["measurement_backend"]["memory_pss"] != "proc_smaps_rollup"
                or resource["measurement_backend"]["cpu"] != "proc_tree"
                or resource["sample_interval_ms"] != 250
                or any(
                    isinstance(resource[key], bool)
                    or not isinstance(resource[key], (int, float))
                    or not math.isfinite(resource[key])
                    or resource[key] < 0
                    for key in ("pss_peak_bytes", "cpu_total_ms")
                )
            ):
                raise ValueError("invalid Linux resource measurement")
        for scope, keys in (
            ("all_predeclared_calls", expected),
            ("frozen_historical_common_pass_calls", chart),
        ):
            for metric, field, scale in (
                ("pss_peak_mib", "pss_peak_bytes", 1024**2),
                ("cpu_time_ms", "cpu_total_ms", 1),
            ):
                measured = stats([by_key[key]["resource"][field] / scale for key in sorted(keys)])
                reported = config["metrics"][scope][metric]
                if any(
                    not math.isclose(value, reported[key], rel_tol=1e-9, abs_tol=1e-6)
                    for key, value in measured.items()
                ):
                    raise ValueError("Linux resource aggregate differs from the recorded calls")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = summarize(args.directory)
    with args.output.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {"publishable": result["publishable"], "methods": list(result["configurations"])}
        )
    )


if __name__ == "__main__":
    main()
