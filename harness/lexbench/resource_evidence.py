"""Bind a complete serialized resource matrix to report summary provenance."""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path

from .resource_matrix import verify_calls

from harness.record_io import load_jsonl


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("resource evidence must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_measurements(rows: list[dict], protocol: dict, metrics: dict) -> None:
    by_key = {(r["task_id"], r["attempt"]): r for r in rows}
    expected = {(task, attempt) for task in protocol["task_ids"] for attempt in range(1, 6)}
    chart = {(r["task_id"], r["attempt"]) for r in protocol["chart_keys"]}
    chart_tasks = {task for task, _ in chart}
    if (
        len(rows) != len(by_key)
        or set(by_key) != expected
        or len(chart) != 1045
        or len(chart_tasks) != 209
        or chart != {(task, attempt) for task in chart_tasks for attempt in range(1, 6)}
        or not chart.issubset(expected)
    ):
        raise ValueError("resource measurement population mismatch")
    for row in rows:
        if row["memory_backend"] != "darwin_ps_process_tree_rss":
            raise ValueError("resource measurement backend mismatch")
        for key in ("rss_peak_bytes", "cpu_total_ms"):
            value = row[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("invalid resource measurement")
    for scope, keys in (
        ("all_predeclared_calls", expected),
        ("frozen_historical_common_pass_calls", chart),
    ):
        for name, field, scale in (
            ("rss_peak_mib", "rss_peak_bytes", 1024**2),
            ("cpu_time_ms", "cpu_total_ms", 1),
        ):
            values = [by_key[key][field] / scale for key in sorted(keys)]
            actual = {
                "n": len(values),
                "sum": math.fsum(values),
                "mean": statistics.mean(values),
                "p50": statistics.median(values),
                "p95": statistics.quantiles(values, n=20, method="inclusive")[18],
                "min": min(values),
                "max": max(values),
            }
            if any(
                not math.isclose(value, metrics[scope][name][key], rel_tol=1e-9, abs_tol=1e-6)
                for key, value in actual.items()
            ):
                raise ValueError("resource aggregate differs from the physical measurements")


def validate_matrix(bundle: dict, builds: dict, summaries: dict) -> None:
    protocol, receipt, calls = (bundle[k] for k in ("protocol", "receipt", "calls"))
    if (
        protocol["schema"] != "moli_resource_matrix_protocol/1"
        or receipt["schema"] != "moli_resource_matrix_receipt/1"
        or protocol["selected_round"] != 1
        or receipt["selected_round"] != 1
        or len(protocol["task_ids"]) != 557
        or protocol["attempts"] != 5
        or protocol["sampler_interval_ms"] != 250
        or receipt["physical_calls"] != 11140
        or list(protocol["methods"]) != list(builds)
        or len(builds) != 4
        or set(receipt["results"]) != set(builds)
        or protocol["orchestrator_sha256"] != receipt["orchestrator_sha256"]
    ):
        raise ValueError("incomplete or unbound resource matrix")
    verify_calls(protocol, calls)
    for method, build in builds.items():
        source = protocol["methods"][method]
        if any(source[k] != v for k, v in build.items()):
            raise ValueError("resource build differs from the declared identity")
        summary = summaries["official" if "official" in method else "patched"]
        if (
            summary["provenance"]["comparison_protocol_sha256"]
            != protocol["reference_protocol_sha256"]
        ):
            raise ValueError("resource comparison population binding mismatch")
        candidate = summary["candidate"]
        for key in ("source_commit", "profile", "target"):
            if candidate[key] != build[key]:
                raise ValueError("resource summary build mismatch")
        if candidate["binary_sha256"] != build["sha256"]:
            raise ValueError("resource summary binary mismatch")
        provenance = summary["configurations"][source["layout"]]["provenance"]["profiled"]
        if provenance["run_id"] != source["run_id"]:
            raise ValueError("resource run identity mismatch")
        for file, key in (
            ("run_manifest.json", "manifest_sha256"),
            ("results.jsonl", "results_sha256"),
            ("host_summary.json", "host_summary_sha256"),
        ):
            if receipt["results"][method][file] != provenance[key]:
                raise ValueError("resource summary is not from the matched matrix")
        validate_measurements(
            bundle["measurements"][method],
            protocol,
            summary["configurations"][source["layout"]]["metrics"],
        )


def load_matrix(directory: Path, builds: dict, summaries: dict) -> dict:
    if directory.is_symlink():
        raise ValueError("resource matrix directory must not be a symlink")
    protocol_path = directory / "protocol.json"
    calls_path = directory / "physical-calls.jsonl"
    receipt_path = directory / "receipt.json"
    for path in (protocol_path, calls_path, receipt_path):
        digest(path)
    protocol = json.loads(protocol_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    calls = load_jsonl(calls_path)
    if receipt["protocol_sha256"] != digest(protocol_path) or receipt[
        "physical_calls_sha256"
    ] != digest(calls_path):
        raise ValueError("resource matrix evidence changed")
    if receipt["orchestrator_sha256"] != digest(directory / "orchestrator.py"):
        raise ValueError("resource orchestrator source changed")
    root = Path(protocol["runner_root"])
    measurements = {}
    for method, identity in protocol["methods"].items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identity["run_id"]):
            raise ValueError("invalid resource run identifier")
        run = root / "runs" / identity["run_id"]
        if run.is_symlink():
            raise ValueError("resource run must not be a symlink")
        for filename, expected in receipt["results"][method].items():
            if digest(run / filename) != expected:
                raise ValueError("resource run changed after certification")
        rows = load_jsonl(run / "results.jsonl")
        hashes = {
            (r["task_id"], r["attempt"]): hashlib.sha256(
                json.dumps(r, sort_keys=True).encode()
            ).hexdigest()
            for r in rows
        }
        if (
            len(rows) != 2785
            or len(hashes) != 2785
            or any(
                hashes.get((c["task_id"], c["attempt"])) != c["row_sha256"]
                for c in calls
                if c["method"] == method
            )
        ):
            raise ValueError("physical resource calls differ from saved results")
        measurements[method] = [
            {
                "task_id": row["task_id"],
                "attempt": row["attempt"],
                "rss_peak_bytes": row["resource"]["rss_peak_bytes"],
                "cpu_total_ms": row["resource"]["cpu_total_ms"],
                "memory_backend": row["resource"]["measurement_backend"]["memory_rss"],
                "root_pid": row["engine_provenance"]["pid"],
                "process_count_peak": row["resource"]["process_count_peak"],
            }
            for row in rows
        ]
    bundle = {
        "measurements": measurements,
        "protocol": protocol,
        "receipt": receipt,
        "calls": calls,
    }
    validate_matrix(bundle, builds, summaries)
    # Public evidence retains identities and calls, excluding local filesystem paths.
    protocol.pop("runner_root")
    for identity in protocol["methods"].values():
        identity.pop("binary")
    bundle["public_protocol_redactions"] = ["runner_root", "methods.*.binary"]
    return bundle
