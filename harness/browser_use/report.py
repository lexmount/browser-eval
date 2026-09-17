"""Build the public, drill-down browser-use evidence report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import statistics
import tempfile
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from harness.web_fetch.report_style import STYLE as SHARED_STYLE

from .failure_cause_review import (
    DEFAULT_REVIEW_PATH as FAILURE_CAUSE_REVIEW_PATH,
)
from .failure_cause_review import (
    LAYERED_REVIEW_SCHEMA,
    extract_failure_cause_review_cohort,
    load_failure_cause_review,
    localized_cause,
    validate_failure_cause_review,
)
from .failure_cause_review import (
    sha256 as file_sha256,
)
from .publication import (
    evaluator_summary,
    evidence_metadata,
    read_public_json,
    result_excerpt,
    sanitize,
    task_contract,
    trajectory,
    trajectory_label,
)
from .report_i18n import write_english_report
from .selection import PilotSelection, load_pilot, validate_against_official

ENGINE_LABELS = {"chromium": "Chromium", "moli": "Moli 1.1.3"}
MODEL_LABELS = {"gpt-5.6-sol": "GPT-5.6 Sol"}
BASELINE_ENGINE = "chromium"
TIER_LABELS = {"easy": "简单", "medium": "中等", "hard": "困难"}
SITE_LABELS = {
    "shopping": "Shopping",
    "shopping_admin": "Shopping Admin",
    "reddit": "Reddit",
    "gitlab": "GitLab",
}
MUTATE_IDS = {399, 404, 441, 506}
NAVIGATE_IDS = {44, 45, 118}
RESEARCH_ROOT = Path(__file__).parents[2] / "research"
MAC_EXPANSION = json.loads(
    (RESEARCH_ROOT / "webarena-mac-expansion.json").read_text(encoding="utf-8")
)
MAC_ENVIRONMENT_CANDIDATES = MAC_EXPANSION["environments"]
MAC_COUNTS = MAC_EXPANSION["official_counts"]


def _finding_text(diagnosis: dict[str, Any], field: str) -> str:
    value = diagnosis.get(field, {})
    return str(value.get("zh") or value.get("en") or "") if isinstance(value, dict) else str(value)


def _configure_engine_labels(profile: dict[str, Any]) -> None:
    engines = [str(engine) for engine in profile.get("engines") or []]
    labels = profile.get("engine_labels") or {}
    if not engines or len(engines) != len(set(engines)):
        raise ValueError("current Browser Use profile must register unique browser engines")
    if set(labels) - set(engines):
        raise ValueError("Browser Use engine labels contain an unregistered browser")
    global ENGINE_LABELS
    ENGINE_LABELS = {
        engine: str(labels.get(engine) or engine.replace("_", " ").title()) for engine in engines
    }


def _configure_models(profile: dict[str, Any]) -> None:
    models = [str(model) for model in profile.get("models") or []]
    labels = profile.get("model_labels") or {}
    baseline = str(profile.get("baseline_engine") or "")
    if not models or len(models) != len(set(models)):
        raise ValueError("current Browser Use profile must register unique models")
    if any(not re.fullmatch(r"[A-Za-z0-9._-]+", model) for model in models):
        raise ValueError("Browser Use model IDs must be safe path components")
    if set(labels) - set(models):
        raise ValueError("Browser Use model labels contain an unregistered model")
    if baseline not in ENGINE_LABELS:
        raise ValueError("Browser Use baseline_engine must be a registered browser engine")
    global MODEL_LABELS, BASELINE_ENGINE
    MODEL_LABELS = {model: str(labels.get(model) or model) for model in models}
    BASELINE_ENGINE = baseline


METHODOLOGY_FIGURE = Path(__file__).parent / "assets" / "webarena-autonomous-evaluation-flow.png"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


class Safe(str):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _read_attempts(evidence: Path) -> list[dict[str, Any]]:
    path = evidence / "attempts.jsonl"
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if path.is_file()
        else []
    )


def _evidence_paths(evidence: Path | Iterable[Path]) -> list[Path]:
    paths = [evidence] if isinstance(evidence, Path) else list(evidence)
    resolved = [Path(path).resolve() for path in paths]
    if not resolved:
        raise ValueError("at least one Browser Use evidence directory is required")
    if len(resolved) != len(set(resolved)):
        raise ValueError("Browser Use evidence directories must be unique")
    return resolved


def _load_evidence_bundles(
    evidence: Path | Iterable[Path],
) -> list[dict[str, Any]]:
    """Load isolated model cohorts without ever combining physical attempts on disk."""

    paths = _evidence_paths(evidence)
    bundles: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    for path in paths:
        manifest = _read_json(path / "manifest.json")
        config = _read_json(path / "run-config.json")
        review = _read_json(path / "result-review.json")
        protocol = manifest.get("protocol") or {}
        model = str(protocol.get("model") or "")
        if not model and len(paths) == 1 and len(MODEL_LABELS) == 1:
            model = next(iter(MODEL_LABELS))
        if not model:
            raise ValueError(f"evidence manifest does not identify its model: {path}")
        if model not in MODEL_LABELS:
            raise ValueError(f"evidence model is not registered in the current profile: {model}")
        if model in seen_models:
            raise ValueError(f"more than one evidence directory was supplied for model: {model}")
        seen_models.add(model)
        rows = _read_attempts(path)
        for source in rows:
            row_model = str(source.get("model") or model)
            if row_model != model:
                raise ValueError(
                    f"attempt model differs from its evidence manifest: {row_model} != {model}"
                )
            source["model"] = model
            if str(source.get("engine") or "") not in ENGINE_LABELS:
                raise ValueError(
                    f"attempt browser is not registered in the current profile: {source.get('engine')}"
                )
        bundles.append(
            {
                "path": path,
                "model": model,
                "manifest": manifest,
                "config": config,
                "review": review,
                "source_attempts": rows,
                "attempts_sha256": _sha256(path / "attempts.jsonl"),
                "manifest_sha256": _sha256(path / "manifest.json"),
            }
        )
    expected_models = set(MODEL_LABELS)
    if len(expected_models) > 1 and seen_models != expected_models:
        missing = sorted(expected_models - seen_models)
        extra = sorted(seen_models - expected_models)
        raise ValueError(
            f"model cohort does not match the current profile; missing={missing}, extra={extra}"
        )
    if len(bundles) > 1:
        contract_values = {
            bundle["model"]: (
                bundle["manifest"].get("selection_sha256"),
                ((bundle["manifest"].get("environment") or {}).get("official_evaluator") or {}).get(
                    "data_checksum"
                ),
                (bundle["manifest"].get("environment") or {}).get("webarena_verified_version"),
            )
            for bundle in bundles
        }
        if len(set(contract_values.values())) != 1:
            raise ValueError(
                "model evidence directories do not share one frozen evaluation contract"
            )
        cohorts = {
            bundle["model"]: {
                (str(row["engine"]), int(row["task_id"]))
                for row in bundle["source_attempts"]
                if int(row["attempt"]) == 1
            }
            for bundle in bundles
        }
        reference_model = next(iter(MODEL_LABELS))
        reference = cohorts[reference_model]
        if not reference:
            raise ValueError("multi-model report cannot use an empty primary cohort")
        for model, cohort in cohorts.items():
            if cohort != reference:
                raise ValueError(
                    f"model cohort differs from {reference_model}: {model}; "
                    f"missing={sorted(reference - cohort)}, extra={sorted(cohort - reference)}"
                )
            task_ids = {task_id for _, task_id in cohort}
            expected = {(engine, task_id) for engine in ENGINE_LABELS for task_id in task_ids}
            if cohort != expected:
                raise ValueError(f"model cohort is missing a registered browser: {model}")
    return bundles


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000:.1f} 秒"


def _number(value: float | int | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _best(
    values: dict[str, float | int | None],
    *,
    higher: bool,
    display: Callable[[float], float] | None = None,
) -> set[str]:
    comparable = {
        key: display(float(value)) if display else float(value)
        for key, value in values.items()
        if value is not None
    }
    if len(comparable) < 2:
        return set()
    target = (max if higher else min)(comparable.values())
    winners = {key for key, value in comparable.items() if value == target}
    return winners if len(winners) == 1 else set()


def _winner(value: str, *, won: bool, label: str = "更优") -> Safe:
    if not won:
        return Safe(_e(value))
    return Safe(
        f'<strong class="comparison-winner" title="{_e(label)}">{_e(value)}'
        '<span class="winner-mark" aria-label="更优">优</span></strong>'
    )


def _metric_stack(items: list[tuple[str, str, bool]]) -> Safe:
    lines = []
    for label, value, won in items:
        rendered = _winner(value, won=won)
        lines.append(f"<span><b>{_e(label)}</b>{rendered}</span>")
    return Safe('<span class="metric-stack">' + "".join(lines) + "</span>")


def _probability(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _table(
    headers: list[str],
    rows: Iterable[Iterable[Any]],
    *,
    caption: str = "",
    table_id: str = "",
    classes: str = "",
) -> str:
    head = "".join(f"<th>{_e(x)}</th>" for x in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in row) + "</tr>"
        for row in rows
    )
    cap = f"<caption>{_e(caption)}</caption>" if caption else ""
    attrs = (f' id="{_e(table_id)}"' if table_id else "") + (
        f' class="{_e(classes)}"' if classes else ""
    )
    return f'<div class="table-wrap"><table{attrs}>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _task_type(task_id: int) -> str:
    return (
        "MUTATE" if task_id in MUTATE_IDS else "NAVIGATE" if task_id in NAVIGATE_IDS else "RETRIEVE"
    )


def _attempt_id(row: dict[str, Any]) -> str:
    model = str(row.get("model") or (next(iter(MODEL_LABELS)) if len(MODEL_LABELS) == 1 else ""))
    if not model:
        raise ValueError("logical result does not identify its model")
    return f"{model}-{row['engine']}-{int(row['task_id'])}"


def _row_model(row: dict[str, Any]) -> str:
    model = str(row.get("model") or (next(iter(MODEL_LABELS)) if len(MODEL_LABELS) == 1 else ""))
    if not model:
        raise ValueError("attempt does not identify its model")
    return model


def _execution_tool_metrics(row: dict[str, Any]) -> dict[str, Any]:
    events = read_public_json(Path((row.get("paths") or {}).get("events", "")))
    steps = trajectory(events if isinstance(events, list) else [])
    by_action: dict[str, float] = defaultdict(float)
    count_by_action: dict[str, int] = defaultdict(int)
    total_ms = 0.0
    for step in steps:
        duration_ms = 1000 * float(step.get("duration_seconds") or 0)
        action = str((step.get("arguments") or {}).get("action") or step.get("tool") or "other")
        by_action[action] += duration_ms
        count_by_action[action] += 1
        total_ms += duration_ms
    return {
        "action_count": len(steps),
        "browser_tool_duration_ms": round(total_ms, 3),
        "browser_tool_duration_by_action_ms": {
            action: round(duration, 3) for action, duration in sorted(by_action.items())
        },
        "browser_tool_count_by_action": dict(sorted(count_by_action.items())),
    }


def _logical_results(
    rows: list[dict[str, Any]], protocol: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Project physical executions into one authoritative result per browser and task.

    A failed task may be executed once more. The frozen run protocol chooses the
    authoritative execution; additive cost and latency include every execution.
    """
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        model = str(
            row.get("model") or (next(iter(MODEL_LABELS)) if len(MODEL_LABELS) == 1 else "")
        )
        if not model:
            raise ValueError("attempt does not identify its model")
        row["model"] = model
        grouped[(model, str(row["engine"]), int(row["task_id"]))].append(row)
    results: list[dict[str, Any]] = []
    for key in sorted(grouped):
        executions = sorted(grouped[key], key=lambda row: int(row["attempt"]))
        if [int(row["attempt"]) for row in executions] != list(range(1, len(executions) + 1)):
            raise ValueError(f"non-contiguous execution sequence for {key}")
        if len(executions) > 2:
            raise ValueError(f"more than two executions for {key}")
        legacy_primary = (protocol or {}).get("official_score_uses") == "primary attempt only"
        authoritative = dict(executions[0] if legacy_primary else executions[-1])
        authoritative["duration_ms"] = sum(float(row.get("duration_ms") or 0) for row in executions)
        usage_keys = (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "request_count",
            "request_attempt_count",
        )
        authoritative["model_usage"] = {
            name: sum(int((row.get("model_usage") or {}).get(name) or 0) for row in executions)
            for name in usage_keys
        }
        authoritative["model_transport_errors"] = [
            error for row in executions for error in (row.get("model_transport_errors") or [])
        ]
        resources = [row.get("resource") or {} for row in executions]
        weighted_duration = sum(float(row.get("duration_ms") or 0) for row in executions)
        authoritative["resource"] = {
            **{
                name: sum(float(resource.get(name) or 0) for resource in resources)
                for name in ("cpu_ms", "network_rx_bytes", "network_tx_bytes")
            },
            **{
                name: max((float(resource.get(name) or 0) for resource in resources), default=0)
                for name in ("memory_peak_bytes", "memory_retained_bytes", "pids_current_peak")
            },
        }
        if weighted_duration:
            present_duration = sum(
                float(row.get("duration_ms") or 0)
                for row in executions
                if (row.get("resource") or {}).get("cpu_average_utilization_pct") is not None
            )
            if present_duration:
                authoritative["resource"]["cpu_average_utilization_pct"] = (
                    sum(
                        float((row.get("resource") or {}).get("cpu_average_utilization_pct") or 0)
                        * float(row.get("duration_ms") or 0)
                        for row in executions
                        if (row.get("resource") or {}).get("cpu_average_utilization_pct")
                        is not None
                    )
                    / present_duration
                )
        memory_sample_count = sum(
            int((row.get("resource") or {}).get("sample_count") or 0)
            for row in executions
            if (row.get("resource") or {}).get("memory_mean_bytes") is not None
        )
        if memory_sample_count:
            authoritative["resource"]["memory_mean_bytes"] = (
                sum(
                    float((row.get("resource") or {}).get("memory_mean_bytes") or 0)
                    * int((row.get("resource") or {}).get("sample_count") or 0)
                    for row in executions
                    if (row.get("resource") or {}).get("memory_mean_bytes") is not None
                )
                / memory_sample_count
            )
        tool_metrics = [_execution_tool_metrics(row) for row in executions]
        authoritative["action_count"] = sum(item["action_count"] for item in tool_metrics)
        authoritative["browser_tool_duration_ms"] = round(
            sum(item["browser_tool_duration_ms"] for item in tool_metrics), 3
        )
        by_action: dict[str, float] = defaultdict(float)
        count_by_action: dict[str, int] = defaultdict(int)
        for item in tool_metrics:
            for action, duration in item["browser_tool_duration_by_action_ms"].items():
                by_action[action] += duration
            for action, count in item["browser_tool_count_by_action"].items():
                count_by_action[action] += count
        authoritative["browser_tool_duration_by_action_ms"] = {
            action: round(duration, 3) for action, duration in sorted(by_action.items())
        }
        authoritative["browser_tool_count_by_action"] = dict(sorted(count_by_action.items()))
        authoritative["_source_executions"] = executions
        results.append(authoritative)
    return results


def _public_attempt(row: dict[str, Any]) -> dict[str, Any]:
    usage, resource = row.get("model_usage") or {}, row.get("resource") or {}
    executions = row.get("_source_executions") or [row]
    return {
        "id": _attempt_id(row),
        "run_id": str(row.get("run_id") or ""),
        "model": str(row["model"]),
        "engine": str(row["engine"]),
        "task_id": int(row["task_id"]),
        "tier": str(row["tier"]),
        "score": float(row["score"]),
        "status": str(row.get("status") or ("success" if float(row["score"]) == 1 else "failure")),
        "duration_ms": round(float(row.get("duration_ms") or 0), 3),
        "action_count": int(row.get("action_count") or 0),
        "browser_tool_duration_ms": round(float(row.get("browser_tool_duration_ms") or 0), 3),
        "browser_tool_duration_by_action_ms": row.get("browser_tool_duration_by_action_ms") or {},
        "browser_tool_count_by_action": row.get("browser_tool_count_by_action") or {},
        "model_usage": {
            key: int(usage.get(key) or 0)
            for key in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "request_count",
                "request_attempt_count",
            )
        },
        "transport_error_count": len(row.get("model_transport_errors") or []),
        "execution_count": len(executions),
        "authoritative_execution": int(row["attempt"]),
        "resource": {
            key: round(float(resource[key]), 3)
            for key in (
                "cpu_ms",
                "network_rx_bytes",
                "network_tx_bytes",
                "memory_mean_bytes",
                "memory_peak_bytes",
                "memory_retained_bytes",
                "pids_current_peak",
                "cpu_average_utilization_pct",
            )
            if resource.get(key) is not None
        },
    }


def _public_execution(row: dict[str, Any], *, authoritative: bool) -> dict[str, Any]:
    """Return the measurements needed to recompute one logical result's totals."""

    usage, resource = row.get("model_usage") or {}, row.get("resource") or {}
    tool_metrics = _execution_tool_metrics(row)
    return {
        "execution": int(row["attempt"]),
        "authoritative": authoritative,
        "run_id": str(row.get("run_id") or ""),
        "score": float(row.get("score") or 0),
        "status": str(
            row.get("status") or ("success" if float(row.get("score") or 0) == 1 else "failure")
        ),
        "duration_ms": round(float(row.get("duration_ms") or 0), 3),
        "timings": {
            str(key): round(float(value), 3)
            for key, value in (row.get("timings") or {}).items()
            if isinstance(value, (int, float))
        },
        **tool_metrics,
        "model_usage": {
            key: int(usage.get(key) or 0)
            for key in (
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "request_count",
                "request_attempt_count",
            )
        },
        "transport_error_count": len(row.get("model_transport_errors") or []),
        "resource": {
            str(key): round(float(value), 3)
            for key, value in resource.items()
            if isinstance(value, (int, float))
        },
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] if lo == hi else values[lo] + (values[hi] - values[lo]) * (index - lo)


def _primary_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for model in MODEL_LABELS:
        result[model] = {}
        for engine in ENGINE_LABELS:
            logical = [r for r in rows if r["model"] == model and r["engine"] == engine]
            durations = [float(r.get("duration_ms") or 0) for r in logical]
            tool_durations = [float(r.get("browser_tool_duration_ms") or 0) for r in logical]
            tokens = [int((r.get("model_usage") or {}).get("total_tokens") or 0) for r in logical]
            passed = sum(float(r["score"]) == 1 for r in logical)
            resource_summary = {}
            for name in (
                "cpu_ms",
                "cpu_average_utilization_pct",
                "memory_mean_bytes",
                "memory_peak_bytes",
                "network_rx_bytes",
                "network_tx_bytes",
            ):
                values = [
                    float((row.get("resource") or {})[name])
                    for row in logical
                    if (row.get("resource") or {}).get(name) is not None
                ]
                resource_summary[name] = {
                    "count": len(values),
                    "mean": statistics.fmean(values) if values else None,
                    "p50": _percentile(values, 0.50),
                    "p95": _percentile(values, 0.95),
                }
            result[model][engine] = {
                "completed": len(logical),
                "passed": passed,
                "score": passed / len(logical) if logical else None,
                "mean_duration_ms": statistics.fmean(durations) if durations else None,
                "p50_duration_ms": _percentile(durations, 0.50),
                "p95_duration_ms": _percentile(durations, 0.95),
                "mean_browser_tool_duration_ms": statistics.fmean(tool_durations)
                if tool_durations
                else None,
                "p50_browser_tool_duration_ms": _percentile(tool_durations, 0.50),
                "p95_browser_tool_duration_ms": _percentile(tool_durations, 0.95),
                "mean_tokens": statistics.fmean(tokens) if tokens else None,
                "p50_tokens": _percentile(tokens, 0.50),
                "p95_tokens": _percentile(tokens, 0.95),
                "resources": resource_summary,
            }
    return result


def _paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare every browser with the profile baseline, always within one model."""

    primary = {
        (str(r["model"]), str(r["engine"]), int(r["task_id"])): float(r["score"]) == 1 for r in rows
    }
    result: dict[str, Any] = {}
    for model in MODEL_LABELS:
        comparisons: dict[str, Any] = {}
        for engine in ENGINE_LABELS:
            if engine == BASELINE_ENGINE:
                continue
            ids = sorted(
                task_id
                for task_id in {key[2] for key in primary if key[0] == model}
                if (model, BASELINE_ENGINE, task_id) in primary
                and (model, engine, task_id) in primary
            )
            counts = {
                "both_pass": 0,
                "baseline_only": 0,
                "comparison_only": 0,
                "both_fail": 0,
            }
            for task_id in ids:
                baseline_passed = primary[(model, BASELINE_ENGINE, task_id)]
                comparison_passed = primary[(model, engine, task_id)]
                key = (
                    "both_pass"
                    if baseline_passed and comparison_passed
                    else "baseline_only"
                    if baseline_passed
                    else "comparison_only"
                    if comparison_passed
                    else "both_fail"
                )
                counts[key] += 1
            discordant = counts["baseline_only"] + counts["comparison_only"]
            p = (
                min(
                    1.0,
                    2
                    * sum(
                        math.comb(discordant, k)
                        for k in range(min(counts["baseline_only"], counts["comparison_only"]) + 1)
                    )
                    / 2**discordant,
                )
                if discordant
                else (1.0 if ids else None)
            )
            comparisons[engine] = {
                "baseline_engine": BASELINE_ENGINE,
                "comparison_engine": engine,
                "paired": len(ids),
                **counts,
                "mcnemar_exact_p": p,
            }
        result[model] = {
            "baseline_engine": BASELINE_ENGINE,
            "comparisons": comparisons,
        }
    return result


def _resolve_tasks(
    selection: PilotSelection, *, expected_data_checksum: str | None = None
) -> list[dict[str, Any]]:
    try:
        from .runner import _configure_benchmark

        benchmark = _configure_benchmark()
        if expected_data_checksum:
            dataset_path = Path(benchmark.config.test_data_file)
            actual_data_checksum = _sha256(dataset_path)
            if actual_data_checksum != expected_data_checksum:
                raise ValueError(
                    "installed WebArena-Verified data checksum differs from measured evidence: "
                    f"{actual_data_checksum} != {expected_data_checksum}"
                )
        tasks = validate_against_official(selection, benchmark, enforce_contract_integrity=False)
        official = {
            int(task["task_id"]): benchmark.get_task(int(task["task_id"])) for task in tasks
        }
    except Exception:
        if expected_data_checksum:
            raise
        tasks = [
            {
                "task_id": t.task_id,
                "tier": t.tier,
                "estimated_min_steps": t.estimated_min_steps,
                "rationale": t.rationale,
                "official_hard": t.official_hard,
                "sites": ["unknown"],
                "intent": t.rationale,
                "intent_template_id": None,
                "revision": None,
                "start_urls": [],
            }
            for t in selection.tasks
        ]
    for task in tasks:
        official_task = official.get(int(task["task_id"])) if "official" in locals() else None
        contract = task_contract(official_task) if official_task is not None else {}
        task["contract"] = contract
        task["contract_sha256"] = hashlib.sha256(
            json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        evals = contract.get("evaluation_contract") or []
        if expected_data_checksum and not evals:
            raise ValueError(f"official task {task['task_id']} has no evaluator contract")
        expected = (evals[0].get("expected") or {}) if evals else {}
        task["task_type"] = str(
            expected.get("task_type") or _task_type(int(task["task_id"]))
        ).upper()
    return tasks


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def _task_family_summary(
    tasks: list[dict[str, Any]], attempts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    primary = {
        (str(row["model"]), str(row["engine"]), int(row["task_id"])): float(row["score"]) == 1
        for row in attempts
    }
    result = []
    for kind in ("RETRIEVE", "NAVIGATE", "MUTATE"):
        selected = [task for task in tasks if task["task_type"] == kind]
        result.append(
            {
                "kind": kind,
                "count": len(selected),
                "scores": {
                    model: {
                        engine: sum(
                            primary.get((model, engine, int(task["task_id"])), False)
                            for task in selected
                        )
                        for engine in ENGINE_LABELS
                    }
                    for model in MODEL_LABELS
                },
            }
        )
    return result


TASK_TYPE_COPY = {
    "RETRIEVE": ("信息提取", "从页面读取、筛选或汇总信息，不修改站点状态"),
    "NAVIGATE": ("页面导航", "打开符合指定条件的页面或列表"),
    "MUTATE": ("页面操作", "提交表单、修改记录、结账等会改变站点状态的任务"),
}


def _task_family_table(families: list[dict[str, Any]]) -> str:
    headers = ["任务类型", "任务数", "测什么"] + [
        f"{MODEL_LABELS[model]} · {ENGINE_LABELS[engine]}"
        for model in MODEL_LABELS
        for engine in ENGINE_LABELS
    ]
    rows: list[list[Any]] = []
    for family in families:
        kind = str(family["kind"])
        copy = TASK_TYPE_COPY.get(kind)
        if copy is None:
            raise ValueError(f"unsupported Browser Use task type: {kind}")
        label, description = copy
        rows.append(
            [
                Safe(
                    f'<a href="datasets/?f_kind={_e(kind)}"><strong>{_e(label)}</strong>'
                    f'<span class="table-kicker">{_e(kind)}</span></a>'
                ),
                family["count"],
                description,
                *[
                    f"{family['scores'][model][engine]}/{family['count']}"
                    for model in MODEL_LABELS
                    for engine in ENGINE_LABELS
                ],
            ]
        )
    return _table(headers, rows, table_id="task-family-table", classes="wide-data-table")


def _failure_cause_table(verified_causes: dict[str, dict[str, Any]]) -> str:
    groups = {
        "agent": ("Agent / 模型行为", "保留", "属于当前被测组合；按轨迹定位检索、选择或提交错误。"),
        "benchmark": (
            "评测实现 / 任务合同",
            "排除并修复",
            "修正任务合同、验收器或证据采集，通过独立预检后重跑四种配置。",
        ),
        "site": ("自托管站点", "排除并修复", "修复依赖、会话或保存链路，通过读写预检后重跑。"),
        "evidence": (
            "浏览器证据接口",
            "核验后修复",
            "先核对站点真实请求与 CDP/HAR；不能把证据缺失直接当成浏览器操作失败。",
        ),
        "environment": (
            "共享测试环境",
            "排除，修复已完成",
            "跨进程串行锁已经上线；受污染任务仍需重跑。",
        ),
        "mixed": (
            "参考状态与 Agent 混合",
            "排除并重建",
            "重新冻结参考状态，再重跑以区分 Agent 错误。",
        ),
    }

    def group_for(code: str) -> str:
        for prefix in ("agent", "benchmark", "site", "environment", "mixed"):
            if code.startswith(prefix + "_"):
                return prefix
        if code.startswith(("evidence_", "browser_evidence_")):
            return "evidence"
        raise ValueError(f"unknown failure-cause scope: {code}")

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for diagnosis in verified_causes.values():
        subcategory = diagnosis.get("subcategory") or {}
        code = str(subcategory.get("code") or "evidence_capture")
        label = str(subcategory.get("zh") or "未分类")
        counts[group_for(code)][label] += 1

    rows = []
    for key in ("agent", "benchmark", "site", "evidence", "environment", "mixed"):
        total = sum(counts[key].values())
        if not total:
            continue
        rows.append([groups[key][0], total, groups[key][1], groups[key][2]])
    return _table(
        ["原因范围", "失败结果数", "本轮处理", "修复与验证"],
        rows,
        table_id="failure-cause-table",
        classes="wide-data-table",
    )


def _external_interference_task_ids(
    verified_causes: dict[str, dict[str, Any]],
) -> set[int]:
    prefixes = ("benchmark_", "site_", "environment_", "mixed_")
    return {
        int(result_id.rsplit("-", 1)[-1])
        for result_id in verified_causes
        if _cause_code(verified_causes, result_id).startswith(prefixes)
    }


def _clean_subset_table(
    attempts: list[dict[str, Any]], verified_causes: dict[str, dict[str, Any]]
) -> str:
    excluded = _external_interference_task_ids(verified_causes)
    kept = [row for row in attempts if int(row["task_id"]) not in excluded]
    summary = _primary_summary(kept)
    return (
        _table(
            ["模型", "浏览器", "通过任务", "通过率"],
            [
                [
                    MODEL_LABELS[model],
                    ENGINE_LABELS[engine],
                    f"{summary[model][engine]['passed']}/{summary[model][engine]['completed']}",
                    _pct(summary[model][engine]["score"]),
                ]
                for model in MODEL_LABELS
                for engine in ENGINE_LABELS
            ],
            table_id="clean-subset-table",
            classes="core-comparison-table",
        )
        + '<p class="fine">这是事后诊断口径，不是官方新分数。它按任务统一剔除任一配置已证实受任务/evaluator、站点、共享环境或参考状态干扰的任务，保证四个配置分母一致。</p>'
        f'<p class="fine">本轮剔除 {len(excluded)} 题：{_e("、".join(f"#{task_id}" for task_id in sorted(excluded)))}。</p>'
    )


def _adoption_top3(evidence: dict[str, Any]) -> str:
    families = sorted(
        (item for item in evidence.get("benchmark_families") or [] if item.get("interaction")),
        key=lambda item: (-len(item.get("adopters") or []), str(item.get("family") or "")),
    )[:3]
    if len(families) != 3 or not evidence.get("verified_at"):
        raise ValueError("benchmark adoption evidence must provide a dated interaction Top 3")
    cards = []
    for item in families:
        adopters = item.get("adopters") or []
        warning = item.get("warning")
        if warning == (
            "Original Mind2Web, Online-Mind2Web, and Mind2Web 2 have different "
            "tasks and graders and must not share a score table."
        ):
            warning = (
                "原版 Mind2Web、Online-Mind2Web 与 Mind2Web 2 的任务和评分器不同，"
                "不能合并到同一成绩表。"
            )
        note = warning or {
            "WebArena": "自托管、可重置、结果验收，更适合浏览器 A/B。",
            "WebVoyager": "真实网站直观，但网页漂移与 LLM judge 降低精确复现性。",
        }.get(str(item.get("family")), "采用数不代表评测质量。")
        cards.append(
            '<article class="rank-item"><div>'
            f"<h3>{_e(item.get('family'))} 家族 · {len(adopters)} 家</h3>"
            f"<p>{_e('、'.join(adopters))}。{_e(note)}</p></div></article>"
        )
    return (
        '<div class="rank-list">'
        + "".join(cards)
        + '</div><p class="fine">采用次数来自公开一手材料中的机构去重计数，不是质量分；'
        f"证据核对日期 {_e(evidence.get('verified_at') or '未记录')}。未找到 DeepSeek、Kimi "
        "公开的一手 UI browser-use benchmark，不等于内部没有评测。</p>"
    )


def _same_task_comparison(tasks: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> str:
    """Render one task-by-configuration matrix; never reduce it to pairwise controls."""

    results = {(_row_model(row), str(row["engine"]), int(row["task_id"])): row for row in attempts}
    engines = [BASELINE_ENGINE, *(engine for engine in ENGINE_LABELS if engine != BASELINE_ENGINE)]
    configurations = [(model, engine) for model in MODEL_LABELS for engine in engines]
    if len(configurations) < 2:
        return '<p class="muted-box">至少需要两个参评配置才能进行同题比较。</p>'

    rows = []
    for task in tasks:
        task_id = int(task["task_id"])
        states = []
        cells = []
        for model, engine in configurations:
            result = results.get((model, engine, task_id))
            if result is None:
                state, label = "missing", "未测"
            elif float(result["score"]) == 1:
                state, label = "pass", "通过"
            else:
                state, label = "fail", "失败"
            states.append(state)
            cells.append(
                f'<td><a class="outcome-pill {state}" href="results/{model}-{engine}-{task_id}.html">'
                f"{label}</a></td>"
                if result is not None
                else '<td><span class="outcome-pill missing">未测</span></td>'
            )
        present = [state for state in states if state != "missing"]
        relation = (
            "all_pass"
            if present and all(state == "pass" for state in present)
            else "all_fail"
            if present and all(state == "fail" for state in present)
            else "mixed"
        )
        site = str(task["sites"][0])
        task_cell = (
            f'<div class="task-cell"><a href="datasets/{task_id}.html">#{task_id} · '
            f'{_e(task["rationale"])}</a><div class="task-meta">'
            f'<span class="track-badge">{_e(TIER_LABELS[task["tier"]])}</span>'
            f'<span class="track-badge">{_e(SITE_LABELS.get(site, site))}</span>'
            f'<span class="track-badge">{_e(task["task_type"])}</span></div></div>'
        )
        rows.append(
            f'<tr data-tier="{_e(task["tier"])}" data-site="{_e(site)}" '
            f'data-kind="{_e(task["task_type"])}" data-relation="{relation}" '
            f'data-search="{_e(str(task.get("intent") or task.get("rationale") or ""))}">'
            f"<td>{task_cell}</td>{''.join(cells)}</tr>"
        )

    headers = "".join(
        f'<th>{_e(MODEL_LABELS[model])}<br><span class="table-kicker">{_e(ENGINE_LABELS[engine])}</span></th>'
        for model, engine in configurations
    )
    return (
        '<article class="browser-comparison" id="configuration-matrix"><header class="comparison-heading"><div>'
        "<h3>同题全配置矩阵</h3><p>每行是同一任务，每列是一个模型×浏览器配置。新增配置只增加列。</p></div>"
        '<b class="comparison-count" data-matrix-count></b></header>'
        '<div class="filters matrix-filters"><label>关系 <select data-matrix-filter="relation"><option value="">全部</option>'
        '<option value="mixed" selected>结果有差异</option><option value="all_pass">全部通过</option>'
        '<option value="all_fail">全部失败</option></select></label>'
        '<label>层级 <select data-matrix-filter="tier"><option value="">全部</option><option value="easy">简单</option><option value="medium">中等</option><option value="hard">困难</option></select></label>'
        '<label>站点 <select data-matrix-filter="site"><option value="">全部</option><option value="shopping">Shopping</option><option value="shopping_admin">Shopping Admin</option><option value="reddit">Reddit</option><option value="gitlab">GitLab</option></select></label>'
        '<label>类型 <select data-matrix-filter="kind"><option value="">全部</option><option>RETRIEVE</option><option>NAVIGATE</option><option>MUTATE</option></select></label></div>'
        '<div class="comparison-result"><div class="table-wrap"><table class="comparison-table configuration-matrix">'
        f"<thead><tr><th>任务</th>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></div></article>"
    )


def _group_rows(
    attempts: list[dict[str, Any]], tasks: list[dict[str, Any]], field: str
) -> list[list[Any]]:
    meta = {int(t["task_id"]): t for t in tasks}
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        value = meta[int(row["task_id"])][field]
        value = value[0] if isinstance(value, list) else value
        grouped[(str(value), str(row["model"]), str(row["engine"]))].append(row)
    result = []
    for value in sorted({key[0] for key in grouped}):
        line: list[Any] = [SITE_LABELS.get(value, TIER_LABELS.get(value, value))]
        for model in MODEL_LABELS:
            passed_by_engine = {
                engine: sum(float(x["score"]) == 1 for x in grouped[(value, model, engine)])
                for engine in ENGINE_LABELS
            }
            winners = _best(passed_by_engine, higher=True)
            for engine in ENGINE_LABELS:
                items = grouped[(value, model, engine)]
                line.append(
                    _winner(
                        f"{passed_by_engine[engine]}/{len(items)}" if items else "—",
                        won=engine in winners,
                        label="同组通过数更高",
                    )
                )
        result.append(line)
    return result


def _action_duration_cards(attempts: list[dict[str, Any]]) -> str:
    actions = sorted(
        {
            action
            for row in attempts
            for action in (row.get("browser_tool_duration_by_action_ms") or {})
        }
    )
    cards: list[str] = []
    for action in actions:
        metrics: dict[str, tuple[float, int, float | None]] = {}
        for model in MODEL_LABELS:
            for engine in ENGINE_LABELS:
                key = f"{model}|{engine}"
                total = sum(
                    float((row.get("browser_tool_duration_by_action_ms") or {}).get(action) or 0)
                    for row in attempts
                    if _row_model(row) == model and row["engine"] == engine
                )
                count = sum(
                    int((row.get("browser_tool_count_by_action") or {}).get(action) or 0)
                    for row in attempts
                    if _row_model(row) == model and row["engine"] == engine
                )
                metrics[key] = (total, count, total / count if count else None)
        average_winners = _best(
            {key: values[2] for key, values in metrics.items()},
            higher=False,
            display=lambda value: round(value / 1000, 3),
        )
        maximum = max((values[2] or 0 for values in metrics.values()), default=0)
        browser_rows = []
        for model in MODEL_LABELS:
            for engine in ENGINE_LABELS:
                key = f"{model}|{engine}"
                total, count, average = metrics[key]
                width = 0 if average is None or maximum <= 0 else max(2.0, average / maximum * 100)
                average_text = "—" if average is None else f"{average / 1000:.3f} 秒"
                browser_rows.append(
                    f'<div class="action-browser-row{" action-winner" if key in average_winners else ""}"><div class="action-browser-head">'
                    f"<b>{_e(MODEL_LABELS[model])} · {_e(ENGINE_LABELS[engine])}</b>{_winner(average_text, won=key in average_winners, label='全部配置中单次均值最低')}"
                    f'</div><div class="action-bar" aria-hidden="true"><i style="width:{width:.1f}%"></i></div>'
                    f'<div class="action-browser-meta">总耗时 {total / 1000:.1f} 秒 · {count} 次调用</div></div>'
                )
        cards.append(
            '<article class="action-card"><header><code>'
            + _e(action)
            + "</code><span>全部配置 · 单次调用墙钟时间</span></header>"
            + "".join(browser_rows)
            + "</article>"
        )
    return '<div class="action-card-grid">' + "".join(cards) + "</div>"


def _summary_comparison_rows(summary: dict[str, dict[str, dict[str, Any]]]) -> list[list[Any]]:
    lower_names = (
        "mean_duration_ms",
        "p50_duration_ms",
        "p95_duration_ms",
        "mean_browser_tool_duration_ms",
        "p50_browser_tool_duration_ms",
        "p95_browser_tool_duration_ms",
        "mean_tokens",
        "p50_tokens",
        "p95_tokens",
    )
    flat = {
        f"{model}|{engine}": values
        for model, model_summary in summary.items()
        for engine, values in model_summary.items()
    }
    higher_metrics = {
        name: _best({key: values[name] for key, values in flat.items()}, higher=True)
        for name in ("passed", "score")
    }
    lower_metrics = {
        name: _best(
            {key: values[name] for key, values in flat.items()},
            higher=False,
            display=(
                (lambda value: round(value / 1000, 1))
                if "duration_ms" in name
                else (lambda value: round(value))
            ),
        )
        for name in lower_names
    }
    rows: list[list[Any]] = []
    for model, model_summary in summary.items():
        for engine, label in ENGINE_LABELS.items():
            key = f"{model}|{engine}"
            current = model_summary[engine]
            rows.append(
                [
                    MODEL_LABELS[model],
                    label,
                    _winner(
                        f"{current['passed']}/{current['completed']}",
                        won=key in higher_metrics["passed"],
                        label="全部注册配置中通过数最高",
                    ),
                    _winner(
                        _pct(current["score"]),
                        won=key in higher_metrics["score"],
                        label="全部注册配置中通过率最高",
                    ),
                    _metric_stack(
                        [
                            (
                                "均值",
                                _seconds(current["mean_duration_ms"]),
                                key in lower_metrics["mean_duration_ms"],
                            ),
                            (
                                "P50",
                                _seconds(current["p50_duration_ms"]),
                                key in lower_metrics["p50_duration_ms"],
                            ),
                            (
                                "P95",
                                _seconds(current["p95_duration_ms"]),
                                key in lower_metrics["p95_duration_ms"],
                            ),
                        ]
                    ),
                    _metric_stack(
                        [
                            (
                                "均值",
                                _seconds(current["mean_browser_tool_duration_ms"]),
                                key in lower_metrics["mean_browser_tool_duration_ms"],
                            ),
                            (
                                "P50",
                                _seconds(current["p50_browser_tool_duration_ms"]),
                                key in lower_metrics["p50_browser_tool_duration_ms"],
                            ),
                            (
                                "P95",
                                _seconds(current["p95_browser_tool_duration_ms"]),
                                key in lower_metrics["p95_browser_tool_duration_ms"],
                            ),
                        ]
                    ),
                    _metric_stack(
                        [
                            (
                                "均值",
                                _number(current["mean_tokens"]),
                                key in lower_metrics["mean_tokens"],
                            ),
                            (
                                "P50",
                                _number(current["p50_tokens"]),
                                key in lower_metrics["p50_tokens"],
                            ),
                            (
                                "P95",
                                _number(current["p95_tokens"]),
                                key in lower_metrics["p95_tokens"],
                            ),
                        ]
                    ),
                ]
            )
    return rows


def _resource_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "ms":
        return f"{value / 1000:.1f} 秒"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "mib":
        return f"{value / 1_048_576:,.1f} MiB"
    if unit == "mib_net":
        return f"{value / 1_048_576:,.2f} MiB"
    raise ValueError(f"unknown resource unit: {unit}")


def _resource_detail_rows(resource: dict[str, Any]) -> list[list[str]]:
    fields = (
        ("cpu_ms", "浏览器 CPU 时间", "ms"),
        ("cpu_average_utilization_pct", "平均单核 CPU 占用", "pct"),
        ("memory_mean_bytes", "时间加权平均内存", "mib"),
        ("memory_peak_bytes", "峰值内存", "mib"),
        ("memory_retained_bytes", "结束时新增常驻内存", "mib"),
        ("network_rx_bytes", "网络接收量", "mib_net"),
        ("network_tx_bytes", "网络发送量", "mib_net"),
    )
    return [
        [label, _resource_value(float(resource[key]), unit)]
        for key, label, unit in fields
        if resource.get(key) is not None
    ]


def _evidence_check_rows(checks: dict[str, Any]) -> list[list[str]]:
    labels = {
        "network_har_exists": "浏览器网络记录存在",
        "agent_trace_exists": "浏览器执行轨迹存在",
        "response_exists": "Agent 最终回答存在",
        "evaluation_exists": "官方判分明细存在",
        "events_exists": "Agent 事件流存在",
        "environment_prepare_exists": "环境准备记录存在",
        "evaluation_json": "判分明细可读取",
        "response_json": "最终回答可读取",
        "response_matches_record": "最终回答与运行记录一致",
        "score_matches_evaluator": "报告分数与官方判分一致",
        "status_matches_evaluator": "报告状态与官方判分一致",
        "official_evaluator_version_present": "官方 evaluator 版本已记录",
        "official_evaluator_checksum_matches": "官方 evaluator 文件校验一致",
        "official_data_checksum_matches": "官方题集文件校验一致",
        "model_request_attempts_accounted": "模型请求次数已完整计入",
    }
    return [
        [labels.get(key, key.replace("_", " ")), "通过" if value else "未通过"]
        for key, value in checks.items()
    ]


def _publication_label(value: Any) -> str:
    return {
        "private_only_session_sensitive": "仅保留哈希与字节数（含登录态）",
        "sanitized_projection": "已脱敏公开副本",
        "public": "公开原文件",
    }.get(str(value), "按证据清单处理")


def _resource_comparison_table(summary: dict[str, dict[str, dict[str, Any]]]) -> str:
    metrics = (
        ("cpu_ms", "CPU 时间", "ms"),
        ("cpu_average_utilization_pct", "平均 CPU 单核占用", "pct"),
        ("memory_mean_bytes", "时间加权平均内存", "mib"),
        ("memory_peak_bytes", "峰值内存", "mib"),
        ("network_rx_bytes", "网络接收量", "mib_net"),
        ("network_tx_bytes", "网络发送量", "mib_net"),
    )
    flat = {
        f"{model}|{engine}": values
        for model, model_summary in summary.items()
        for engine, values in model_summary.items()
    }
    directionally_comparable = {"cpu_ms", "memory_mean_bytes", "memory_peak_bytes"}
    winners = {
        (name, stat): _best(
            {
                key: ((values.get("resources") or {}).get(name) or {}).get(stat)
                for key, values in flat.items()
            },
            higher=False,
            display=lambda value, unit=unit: round(
                value / (1_048_576 if unit in {"mib", "mib_net"} else 1), 2
            ),
        )
        for name, _, unit in metrics
        for stat in ("mean", "p50", "p95")
        if name in directionally_comparable
    }
    rows: list[list[Any]] = []
    for model, model_summary in summary.items():
        for engine, label in ENGINE_LABELS.items():
            key = f"{model}|{engine}"
            resources = model_summary[engine].get("resources") or {}
            rows.append(
                [
                    MODEL_LABELS[model],
                    label,
                    *[
                        _metric_stack(
                            [
                                (
                                    stat.upper() if stat != "mean" else "均值",
                                    _resource_value((resources.get(name) or {}).get(stat), unit),
                                    key in winners.get((name, stat), set()),
                                )
                                for stat in ("mean", "p50", "p95")
                            ]
                        )
                        for name, _, unit in metrics
                    ],
                ]
            )
    return _table(
        ["模型", "浏览器", *[label for _, label, _ in metrics]],
        rows,
        table_id="resource-metrics-table",
        classes="wide-data-table resource-comparison-table",
    )


REPORT_JS = """function theme(){const r=document.documentElement,n=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=n;try{localStorage.setItem('browser-use-theme',n)}catch(e){}}
function openDiagnosis(id){const d=document.getElementById(id);if(d?.showModal)d.showModal()}
function closeDiagnosis(id){const d=document.getElementById(id);if(d?.open)d.close()}
function updateViewUrl(){const u=new URL(location.href),q=document.querySelector('[data-search]');[...u.searchParams.keys()].filter(k=>k==='q'||k.startsWith('f_')).forEach(k=>u.searchParams.delete(k));if(q?.value)u.searchParams.set('q',q.value);document.querySelectorAll('[data-filter]').forEach(c=>{if(c.value)u.searchParams.set('f_'+c.dataset.filter,c.value)});history.replaceState(null,'',u.pathname+(u.search?'?'+u.searchParams.toString():'')+u.hash)}
function filterRows(save=true){const controls=[...document.querySelectorAll('[data-filter]')],query=(document.querySelector('[data-search]')?.value||'').trim().toLowerCase();let visible=0,total=0;document.querySelectorAll('tbody tr[data-tier]:not([data-relation])').forEach(r=>{total++;const hidden=Boolean(controls.some(c=>c.value&&r.dataset[c.dataset.filter]!==c.value)||(query&&!String(r.dataset.search||r.textContent).toLowerCase().includes(query)));r.hidden=hidden;if(!hidden)visible++});document.querySelectorAll('[data-visible-count]').forEach(x=>x.textContent=visible+' / '+total);if(save)updateViewUrl()}
function filterMatrix(){const root=document.getElementById('configuration-matrix');if(!root)return;const controls=[...root.querySelectorAll('[data-matrix-filter]')];let visible=0,total=0;root.querySelectorAll('tbody tr[data-tier]').forEach(r=>{total++;const hidden=controls.some(c=>c.value&&r.dataset[c.dataset.matrixFilter]!==c.value);r.hidden=hidden;if(!hidden)visible++});const count=root.querySelector('[data-matrix-count]');if(count)count.textContent=visible+' / '+total}
function applySort(t,col,dir){const b=t?.tBodies[0];if(!b)return;[...b.rows].sort((a,z)=>{const av=a.cells[col]?.dataset.sort||a.cells[col]?.textContent||'',zv=z.cells[col]?.dataset.sort||z.cells[col]?.textContent||'';const an=Number(av.replace(/[^0-9.-]/g,'')),zn=Number(zv.replace(/[^0-9.-]/g,''));const cmp=!Number.isNaN(an)&&!Number.isNaN(zn)?an-zn:av.localeCompare(zv,'zh-CN');return dir==='asc'?cmp:-cmp}).forEach(r=>b.appendChild(r));t.dataset.col=String(col);t.dataset.dir=dir}
function sortTable(id,col){const t=document.getElementById(id);if(!t)return;const dir=t.dataset.col==col&&t.dataset.dir==='asc'?'desc':'asc';applySort(t,col,dir);const u=new URL(location.href);u.searchParams.set('sort',id+':'+col+':'+dir);history.replaceState(null,'',u.pathname+'?'+u.searchParams.toString()+u.hash)}
function applyAnchor(){if(!location.hash)return;const id=decodeURIComponent(location.hash.slice(1)),target=document.getElementById(id);if(target?.scrollIntoView)requestAnimationFrame(()=>target.scrollIntoView({block:'start'}))}
function hasOption(control,value){return !control?.options||[...control.options].some(option=>option.value===value)}
function restoreView(){const p=new URLSearchParams(location.search),q=document.querySelector('[data-search]');if(q&&p.has('q'))q.value=p.get('q')||'';document.querySelectorAll('[data-filter]').forEach(c=>{const v=p.get('f_'+c.dataset.filter);if(v!==null&&hasOption(c,v))c.value=v});const s=(p.get('sort')||'').split(':');if(s.length===3)applySort(document.getElementById(s[0]),Number(s[1]),s[2]);filterRows(false);filterMatrix();try{if(!location.hash){const y=sessionStorage.getItem('browser-use-scroll:'+location.pathname+location.search);if(y!==null)requestAnimationFrame(()=>scrollTo(0,Number(y)||0))}}catch(e){}}
document.addEventListener('DOMContentLoaded',()=>{try{document.documentElement.dataset.theme=localStorage.getItem('browser-use-theme')||'light'}catch(e){}restoreView();applyAnchor();document.querySelectorAll('[data-filter]').forEach(x=>x.addEventListener('change',()=>filterRows()));document.querySelectorAll('[data-search]').forEach(x=>x.addEventListener('input',()=>filterRows()));document.querySelectorAll('[data-matrix-filter]').forEach(x=>x.addEventListener('change',filterMatrix));document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d)d.close()}))});addEventListener('pagehide',()=>{try{sessionStorage.setItem('browser-use-scroll:'+location.pathname+location.search,String(scrollY))}catch(e){}});"""
REPORT_STYLE = (
    SHARED_STYLE
    + """
:root{--max:1440px}
.hero h1,.publication-article h1,.publication-article h2{overflow-wrap:anywhere;word-break:break-word;max-width:100%}
.callout strong{margin-right:.3em}
.top nav{gap:18px;flex-wrap:nowrap;overflow-x:auto;overscroll-behavior-inline:contain;scrollbar-width:thin}.top nav>a,.top nav>button{flex:0 0 auto}.top nav .brand{font-weight:850;color:var(--ink)}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:8px}.top .theme-toggle{margin-left:auto}.page-main{min-height:78vh}.hero.compact{padding:34px 0 26px}.hero.compact h1{font-size:clamp(34px,4vw,50px);margin:10px 0}.hero.compact .lead{margin:0}.section{padding:32px 0}.fine{font-size:13px;color:var(--muted)}.result-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.result-grid .card{border-top:3px solid var(--accent)}.overview-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.overview-card{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:20px;min-width:0}.overview-card h3{margin:8px 0}.audit-strip{display:grid;gap:10px}.audit-strip .callout{margin:0}.filters,.pair-controls{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin:18px 0}.filters select,.filters input,.pair-controls select{padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink)}.filters input{min-width:260px}.comparison-count{margin-left:auto;padding:8px 12px;border-radius:999px;background:var(--wash);color:var(--accent);font-weight:750}.status-pass{color:var(--good);font-weight:750}.status-fail{color:var(--bad);font-weight:750}.status-unproven{color:var(--warn,var(--bad));font-weight:750}.flow{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px;list-style:none;padding:0}.flow li{padding:14px;border:1px solid var(--line);border-radius:11px;background:var(--panel);font-size:13px;min-width:0}.flow li+li:before{content:'→';color:var(--accent);margin-right:7px}.method-figure{margin:24px 0}.method-figure img{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:16px;background:#f6faff}.method-figure figcaption{margin-top:10px;color:var(--muted);font-size:13px;line-height:1.55}.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.detail-grid .kv{min-height:92px;min-width:0}.page-links{display:flex;gap:10px;flex-wrap:wrap}.page-links a{border:1px solid var(--line);background:var(--panel);padding:8px 12px;border-radius:9px}.rank-list{counter-reset:rank}.rank-item{counter-increment:rank;display:grid;grid-template-columns:54px minmax(0,1fr);gap:16px;padding:20px 0;border-top:1px solid var(--line)}.rank-item:before{content:counter(rank);font-size:34px;font-weight:850;color:var(--accent)}.rank-item h3{margin:4px 0}.article-entry{border-left:4px solid var(--accent)}.source-list li{margin:9px 0}.table-wrap{margin:18px 0}.table-wrap table{min-width:760px}#task-table{min-width:1280px}#pair-table{min-width:1180px}.table-wrap caption{text-align:left;padding:10px 12px;color:var(--muted);caption-side:bottom}.footer{border-top:1px solid var(--line)}.evidence-pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.evidence-card{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:16px;min-width:0}.evidence-card h3{margin-top:0}.json-view,.log-view{white-space:pre-wrap;overflow-wrap:anywhere;max-height:540px;overflow:auto;padding:14px;border:1px solid var(--line);border-radius:9px;background:var(--bg);font-size:12px;line-height:1.55}.trajectory{list-style:none;padding:0;counter-reset:step}.trajectory>li{counter-increment:step;display:grid;grid-template-columns:46px minmax(0,1fr);gap:12px;padding:15px 0;border-top:1px solid var(--line)}.trajectory>li:before{content:counter(step);display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--accent);color:white;font-weight:800}.trajectory summary{cursor:pointer;font-weight:750}.rule-fail{border-left:4px solid var(--bad)}.rule-pass{border-left:4px solid var(--good)}.count-note{margin-left:auto;color:var(--muted);font-size:13px}.badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--wash);font-size:12px;margin-right:6px}.muted-box{padding:14px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}@media(max-width:900px){.result-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.overview-grid{grid-template-columns:1fr}.flow{grid-template-columns:repeat(3,minmax(0,1fr))}.evidence-pair{grid-template-columns:1fr}.grid>.card{grid-column:1/-1!important}}@media(max-width:600px){.wrap{width:calc(100% - 28px)}.top nav{gap:14px}.result-grid,.flow{grid-template-columns:1fr}.flow li+li:before{content:'↓';display:block}.hero{padding-top:24px}.hero.compact h1{font-size:30px;line-height:1.18}.filters input{min-width:0;width:100%}.comparison-count{margin-left:0}.rank-item{grid-template-columns:36px minmax(0,1fr)}}@media print{.top,.filters,.pair-controls{display:none!important}.card,.table-wrap{box-shadow:none}.json-view,.log-view{max-height:none;overflow:visible}.evidence-pair{grid-template-columns:1fr}}
/* Publication QA overrides: readable dense tables, code, and text-heavy figures. */
.table-wrap{overflow-x:auto;max-width:100%;contain:inline-size;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
.table-wrap table{min-width:860px;margin:0;border:0}
.table-wrap th{white-space:nowrap}
.table-wrap th,.table-wrap td{word-break:normal;overflow-wrap:break-word;vertical-align:top}
#task-table{min-width:1500px}
#task-table th:nth-child(1),#task-table td:nth-child(1){min-width:76px;white-space:nowrap}
#task-table th:nth-child(2),#task-table td:nth-child(2),#task-table th:nth-child(4),#task-table td:nth-child(4),#task-table th:nth-child(5),#task-table td:nth-child(5){min-width:88px;white-space:nowrap}
#task-table th:nth-child(3),#task-table td:nth-child(3){min-width:140px;white-space:nowrap}
#task-table th:nth-child(6),#task-table td:nth-child(6){min-width:250px}
#task-table th:nth-child(7),#task-table td:nth-child(7){min-width:430px}
#task-table th:nth-child(8),#task-table td:nth-child(8),#task-table th:nth-child(9),#task-table td:nth-child(9){min-width:112px;white-space:nowrap}
#pair-table{min-width:1320px}
#pair-table th:nth-child(-n+4),#pair-table td:nth-child(-n+4){white-space:nowrap}
#pair-table th:nth-child(1),#pair-table td:nth-child(1){min-width:260px;white-space:normal}
#pair-table th:nth-child(n+5),#pair-table td:nth-child(n+5){min-width:235px}
#result-table{min-width:1580px;table-layout:fixed}
#result-table th,#result-table td{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;vertical-align:middle;height:72px;max-height:72px}
#result-table th:nth-child(1),#result-table td:nth-child(1){width:230px}
#result-table th:nth-child(2),#result-table td:nth-child(2){width:170px}
#result-table th:nth-child(3),#result-table td:nth-child(3){width:80px}
#result-table th:nth-child(4),#result-table td:nth-child(4){width:75px}
#result-table th:nth-child(5),#result-table td:nth-child(5){width:250px}
#result-table th:nth-child(6),#result-table td:nth-child(6){width:180px}
#result-table th:nth-child(7),#result-table td:nth-child(7){width:180px}
#result-table th:nth-child(8),#result-table td:nth-child(8){width:310px}
#result-table th:nth-child(9),#result-table td:nth-child(9){width:170px}
#engine-result-table,#change-table{min-width:1080px}
#result-table td:first-child,#engine-result-table td:first-child{white-space:nowrap;min-width:150px}
.json-view,.log-view{background:var(--code-bg)!important;color:var(--code-ink)!important;border-color:#334b68!important;line-height:1.65}
.json-view code,.log-view code{color:inherit!important;background:transparent!important}
.detail-grid .kv{overflow-wrap:anywhere;word-break:break-word}
.method-figure{overflow-x:auto;border:1px solid var(--line);border-radius:16px;background:#f6faff}
.method-figure img{min-width:960px;border:0;border-radius:0}
.method-figure figcaption{padding:0 16px 14px}
.release-current,.release-archive{border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:22px;margin:16px 0}
.release-current{border-top:4px solid var(--accent)}
.release-history>summary,.release-current details>summary,.release-archive details>summary{cursor:pointer;font-weight:800;padding:8px 0}
.comparison-winner{color:var(--good);font-weight:850}.winner-mark{display:inline-grid;place-items:center;min-width:22px;height:22px;padding:0 5px;margin-left:7px;border-radius:999px;background:var(--good);color:white;font-size:11px;line-height:1}.winner-cell{position:relative;padding:9px 10px;border:1px solid color-mix(in srgb,var(--good) 42%,var(--line));border-radius:10px;background:color-mix(in srgb,var(--good) 9%,var(--panel))}.metric-stack{display:grid;gap:5px;min-width:150px}.metric-stack>span{display:flex;align-items:center;justify-content:space-between;gap:12px;white-space:nowrap}.metric-stack>span>b{font-size:12px;color:var(--muted)}.review-round{border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:22px;margin:16px 0}.review-round header{display:flex;align-items:start;justify-content:space-between;gap:18px}.review-round h3{margin:5px 0}.review-status{flex:0 0 auto;padding:5px 10px;border-radius:999px;background:var(--wash);font-size:12px;font-weight:800}.review-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:16px}.review-fields>div{border-top:1px solid var(--line);padding-top:12px}.review-fields b{display:block;margin-bottom:5px}.review-fields p{margin:0}
.browser-comparison{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:24px;box-shadow:0 12px 32px rgba(25,45,70,.06)}.comparison-heading{display:flex;align-items:start;justify-content:space-between;gap:24px;margin-bottom:10px}.comparison-heading h3{margin:0 0 6px;font-size:22px}.comparison-heading p{margin:0;color:var(--muted);line-height:1.65}.matrix-filters{margin:16px 0}.comparison-result{margin-top:12px}.table-wrap .configuration-matrix{min-width:1120px;table-layout:fixed}.configuration-matrix th:first-child,.configuration-matrix td:first-child{width:420px}.configuration-matrix th:not(:first-child),.configuration-matrix td:not(:first-child){width:165px;text-align:center;vertical-align:middle}.task-cell a{font-weight:750}.task-meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:7px}.track-badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);color:var(--muted);font-size:12px;white-space:nowrap}.outcome-pill{display:inline-block;min-width:62px;padding:6px 10px;border-radius:8px;font-size:13px;text-decoration:none;font-weight:750}.outcome-pill.pass{background:var(--success-bg);color:var(--good)}.outcome-pill.fail{background:var(--failure-bg);color:var(--bad)}.outcome-pill.missing{background:var(--panel2);color:var(--muted)}
.action-card-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.action-card{border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:18px;min-width:0}.action-card>header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.action-card>header code{font-size:16px;font-weight:800}.action-card>header span{font-size:12px;color:var(--muted)}.action-browser-row{padding:12px 0;border-top:1px solid var(--line)}.action-browser-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.action-bar{height:7px;border-radius:999px;background:var(--panel2);overflow:hidden;margin:8px 0}.action-bar i{display:block;height:100%;border-radius:inherit;background:var(--accent)}.action-browser-row.action-winner .action-bar i{background:var(--good)}.action-browser-meta{font-size:12px;color:var(--muted)}
.finding-list{display:grid;gap:8px;margin:0 0 20px;padding:16px 20px 16px 38px;border-left:4px solid var(--accent);border-radius:0 12px 12px 0;background:var(--wash)}.finding-list li{padding-left:4px}.table-kicker{display:block;color:var(--muted);font:700 11px/1.4 ui-monospace,monospace;letter-spacing:.04em;margin-top:3px}.core-comparison-table{min-width:1240px;table-layout:fixed}.core-comparison-table td:nth-child(1),.core-comparison-table td:nth-child(2){font-weight:750}.core-comparison-table th:nth-child(1),.core-comparison-table td:nth-child(1){width:145px}.core-comparison-table th:nth-child(2),.core-comparison-table td:nth-child(2){width:135px}.core-comparison-table th:nth-child(3),.core-comparison-table td:nth-child(3){width:115px}.core-comparison-table th:nth-child(4),.core-comparison-table td:nth-child(4){width:105px}.core-comparison-table th:nth-child(n+5),.core-comparison-table td:nth-child(n+5){width:245px}.pair-outcome-table{min-width:900px}.wide-data-table td{line-height:1.65}.report-entry-links{margin-top:18px}.report-entry-links a{font-weight:750}.source-note a{margin-left:5px}
#task-family-table{min-width:1320px;table-layout:fixed}#task-family-table th:nth-child(1),#task-family-table td:nth-child(1){width:150px}#task-family-table th:nth-child(2),#task-family-table td:nth-child(2){width:90px}#task-family-table th:nth-child(3),#task-family-table td:nth-child(3){width:300px}#task-family-table th:nth-child(n+4),#task-family-table td:nth-child(n+4){width:190px;white-space:nowrap}.metric-note{margin:14px 0 0;border-top:1px solid var(--line);padding-top:12px;color:var(--muted)}.metric-note summary{cursor:pointer;font-weight:750;color:var(--ink)}
.diagnosis-button,.dialog-actions button{border:1px solid var(--accent);background:var(--accent);color:white;padding:8px 12px;border-radius:9px;font:inherit;font-weight:750;cursor:pointer;white-space:nowrap}.diagnosis-button:hover,.dialog-actions button:hover{filter:brightness(.94)}.diagnosis-dialog{width:min(920px,calc(100% - 32px));max-height:88vh;padding:0;border:1px solid var(--line);border-radius:18px;background:var(--panel);color:var(--ink);box-shadow:0 28px 90px rgba(10,25,45,.28)}.diagnosis-dialog::backdrop{background:rgba(5,15,25,.62);backdrop-filter:blur(3px)}.diagnosis-dialog-head{position:sticky;top:0;z-index:1;display:flex;align-items:start;justify-content:space-between;gap:20px;padding:22px 24px 16px;border-bottom:1px solid var(--line);background:var(--panel)}.diagnosis-dialog-head h2{font-size:24px;margin:4px 0}.dialog-close{border:0;background:var(--panel2);color:var(--ink);width:36px;height:36px;border-radius:50%;font-size:24px;line-height:1;cursor:pointer}.diagnosis-dialog-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;padding:18px 24px}.diagnosis-dialog-grid>div{padding:13px;border:1px solid var(--line);border-radius:10px;background:var(--bg);min-width:0;overflow-wrap:anywhere}.diagnosis-dialog-grid b{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}.dialog-error,.dialog-review{padding:0 24px 18px}.dialog-error h3,.dialog-review h3{margin:8px 0}.dialog-error .log-view{max-height:240px}.dialog-review p{margin:6px 0}.dialog-actions{position:sticky;bottom:0;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:16px 24px;border-top:1px solid var(--line);background:var(--panel)}.dialog-actions a{padding:8px 10px;border:1px solid var(--line);border-radius:9px}.dialog-actions button{margin-left:auto}
.failure-lead{padding:20px 22px;margin-bottom:14px;border:1px solid color-mix(in srgb,var(--bad) 35%,var(--line));border-left:6px solid var(--bad);border-radius:14px;background:var(--failure-bg)}.failure-lead .status-line{color:var(--bad);font-size:13px;font-weight:850}.failure-lead h3{font-size:24px;margin:5px 0}.failure-lead p{margin:4px 0}.success-lead{border-color:color-mix(in srgb,var(--good) 35%,var(--line));border-left-color:var(--good);background:var(--success-bg)}.success-lead .status-line{color:var(--good)}.runtime-strip{grid-template-columns:repeat(6,minmax(0,1fr));margin:14px 0}.failure-error{margin-top:16px}.failure-error .log-view{max-height:260px}.trajectory-step.failure-step{margin:8px 0;padding:14px;border:1px solid color-mix(in srgb,var(--bad) 40%,var(--line));border-left:5px solid var(--bad);border-radius:12px;background:var(--failure-bg)}.trajectory-step.failure-step:before{background:var(--bad)}.evidence-viewer-head{display:flex;align-items:center;justify-content:space-between;gap:16px}.evidence-viewer-head h2{margin:0}.evidence-viewer-shell .json-view{max-height:none;min-height:60vh}
.action-card-grid{grid-auto-rows:1fr}.action-card{height:100%}.dialog-step,.step-signature{padding:13px 16px;border-radius:10px;background:var(--wash);overflow-wrap:anywhere}.dialog-step{margin:0 24px 18px}.dialog-step b{margin-right:8px}.step-signature{margin:0 0 14px}
.attempt-summary{display:grid;grid-template-columns:minmax(270px,1.4fr) repeat(4,minmax(135px,.7fr));gap:12px;align-items:stretch}.attempt-verdict{padding:20px 22px;border:1px solid var(--line);border-left:5px solid var(--good);border-radius:12px;background:var(--success-bg)}.attempt-verdict.failure{border-left-color:var(--bad);background:var(--failure-bg)}.attempt-verdict .status-line{display:block;font-size:24px;font-weight:850;margin-bottom:6px}.attempt-verdict p{margin:0;line-height:1.65}.attempt-jumps{display:flex;gap:14px;flex-wrap:wrap;margin-top:14px;font-weight:750}.attempt-fact{padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow-wrap:anywhere}.attempt-fact b{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}.attempt-secondary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px}
.diagnosis-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.diagnosis-grid .attempt-fact{min-height:110px}.diagnosis-reason{margin-top:14px;padding:18px 20px;border-left:5px solid var(--bad);border-radius:10px;background:var(--failure-bg);line-height:1.7}.evidence-links{display:flex;gap:8px}.screenshot-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.screenshot-grid figure{margin:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--panel)}.screenshot-grid img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;object-position:top}.screenshot-grid figcaption{padding:10px 12px;color:var(--muted);font-size:12px}
.task-goal{padding:22px 24px;border-radius:16px;background:var(--wash);border:1px solid var(--line);margin-bottom:18px}.task-goal span,.rule-index{display:block;color:var(--accent);font-size:12px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.task-goal p{font-size:20px;font-weight:750;line-height:1.6;margin:7px 0}.task-goal small{color:var(--muted)}.verification-list{display:grid;gap:14px}.verification-rule{padding:20px 22px;border:1px solid var(--line);border-left:5px solid var(--good);border-radius:14px;background:var(--panel);min-width:0}.verification-rule.rule-fail{border-left-color:var(--bad)}.verification-rule>header{display:flex;align-items:start;justify-content:space-between;gap:18px}.verification-rule h3{margin:4px 0 0;font-size:19px}.rule-verdict{flex:0 0 auto;padding:5px 10px;border-radius:999px;font-size:12px;font-weight:850}.rule-verdict.pass{background:var(--success-bg);color:var(--good)}.rule-verdict.fail{background:var(--failure-bg);color:var(--bad)}.rule-purpose{color:var(--muted);margin:12px 0 6px}.rule-summary{font-size:17px;font-weight:750;margin:0 0 14px}.verification-rule details{border-top:1px solid var(--line);padding-top:12px;margin-top:10px}.verification-rule summary{cursor:pointer;font-weight:750}.compact-evidence{margin-top:12px}.compact-evidence .json-view{max-height:360px}
.page-main{overflow-x:clip}
.section[id]{scroll-margin-top:76px}
.filters label{min-width:0;max-width:100%}.filters select,.filters input{max-width:100%;box-sizing:border-box}
@media(max-width:600px){.filters label{width:100%}.filters select{width:100%}}
@media(max-width:980px){.attempt-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.attempt-verdict{grid-column:1/-1}.attempt-secondary,.diagnosis-grid,.runtime-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.screenshot-grid{grid-template-columns:1fr}}
/* Interpretation article: compact evidence-first reading, without dashboard sprawl. */
.interpretation-v2{max-width:1120px;margin:0 auto;padding:34px 0 72px}.interpretation-v2>.essay-head{padding:22px 0 28px;border-bottom:1px solid var(--line)}.interpretation-v2>.essay-head h1{max-width:950px;font-size:clamp(38px,5vw,64px);line-height:1.08;margin:12px 0 16px;letter-spacing:-.035em}.interpretation-v2>.essay-head .lead{max-width:900px;font-size:20px;line-height:1.75}.interpretation-v2>section{padding:34px 0;border-bottom:1px solid var(--line)}.interpretation-v2>section>h2{font-size:clamp(28px,3vw,38px);margin:0 0 12px}.interpretation-v2>section>h3{font-size:22px;margin:30px 0 8px}.answer-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.answer-strip article,.evidence-examples article,.decision-grid article{min-width:0;padding:20px;border:1px solid var(--line);border-radius:15px;background:var(--panel)}.answer-strip b{display:block;font-size:18px;line-height:1.45}.answer-strip p,.evidence-examples p,.decision-grid p{margin:9px 0 0;color:var(--muted)}.score-flow-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:22px 0}.score-flow-card{padding:18px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}.score-flow-card header,.cause-bar>div{display:flex;justify-content:space-between;align-items:center;gap:14px}.score-flow-card header span{font-weight:850;color:var(--accent)}.score-flow-card p{margin:9px 0 0;color:var(--muted);font-size:13px}.score-flow-card p span{color:var(--accent);padding:0 4px}.score-track{display:flex;height:9px;margin-top:14px;border-radius:999px;background:var(--panel2);overflow:hidden}.score-track i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#4d7cff)}.score-track i.recovered{background:var(--good)}.cause-bars{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:22px 0}.cause-bar{position:relative;overflow:hidden;min-height:126px;padding:17px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}.cause-bar strong{font-size:26px}.cause-bar span{display:block;margin-top:10px;color:var(--muted);font-size:13px;line-height:1.5}.cause-bar i{position:absolute;left:0;bottom:0;height:5px;background:var(--accent);border-radius:0 999px 999px 0}.evidence-examples,.decision-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:18px 0}.evidence-examples h3,.decision-grid h3{margin:0;font-size:18px}.audit-ladder{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;margin:22px 0}.audit-ladder article{position:relative;padding:20px 22px;border:1px solid var(--line);background:var(--panel)}.audit-ladder article:first-child{border-radius:14px 0 0 14px}.audit-ladder article:last-child{border-radius:0 14px 14px 0}.audit-ladder article+article{border-left:0}.audit-ladder span{display:grid;place-items:center;width:30px;height:30px;margin-bottom:12px;border-radius:50%;background:var(--accent);color:white;font-weight:850}.audit-ladder b{font-size:17px}.audit-ladder p{margin:8px 0 0;color:var(--muted);font-size:14px}.interpretation-v2 .essay-note{margin-top:22px}.interpretation-v2 .table-wrap{margin:20px 0}.interpretation-v2 .core-comparison-table{min-width:860px}
.verdict-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0}.verdict-strip article{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 12px;align-items:center;padding:17px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}.verdict-strip strong{grid-row:1/3;font-size:34px;color:var(--accent)}.verdict-strip b{line-height:1.4}.verdict-strip p{margin:0;color:var(--muted);font-size:12px;line-height:1.45}
@media(max-width:900px){.answer-strip,.verdict-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.answer-strip article:last-child,.verdict-strip article:last-child{grid-column:1/-1}.cause-bars{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:760px){.browser-comparison{padding:18px}.comparison-heading{display:block}.comparison-count{display:inline-block;margin-top:12px}.comparison-controls{grid-template-columns:1fr}.action-card-grid{grid-template-columns:1fr}.interpretation-v2{padding-top:18px}.interpretation-v2>.essay-head h1{font-size:36px}.interpretation-v2>.essay-head .lead{font-size:17px}.interpretation-v2>section{padding:27px 0}}
@media(max-width:600px){.hero h1{word-break:break-all;line-break:anywhere;text-wrap:balance}.method-figure img{min-width:900px}.table-wrap{margin-left:-4px;margin-right:-4px}.json-view,.log-view{font-size:13px}.review-round header{display:block}.review-status{display:inline-block;margin-top:8px}.review-fields,.attempt-summary,.attempt-secondary,.diagnosis-dialog-grid,.answer-strip,.score-flow-grid,.cause-bars,.evidence-examples,.decision-grid,.verdict-strip,.audit-ladder{grid-template-columns:1fr}.answer-strip article:last-child,.verdict-strip article:last-child{grid-column:auto}.audit-ladder article:first-child,.audit-ladder article:last-child{border-radius:14px}.audit-ladder article+article{border-left:1px solid var(--line);border-top:0}.diagnosis-dialog-head,.diagnosis-dialog-grid,.dialog-error,.dialog-review,.dialog-actions{padding-left:16px;padding-right:16px}.dialog-actions button{margin-left:0}}
"""
)
NAV = [
    ("", "Browser Use", "home"),
    ("datasets/", "评测集", "datasets"),
    ("methodology/", "评测方法", "methodology"),
    ("analysis/", "结果分析", "analysis"),
    ("review/", "核验记录", "review"),
    ("adoption/", "被测浏览器", "adoption"),
    ("reproduce/", "环境与复现", "reproduce"),
    ("changes/", "变更记录", "changes"),
    ("accounts/", "账号与安装", "accounts"),
    ("en/", "English", "en"),
]


def _page(*, title: str, active: str, body: str, depth: int = 0) -> str:
    local_base = "../" * depth or "./"
    links = "".join(
        f'<a href="{href}"'
        + (' class="brand"' if key == "home" else "")
        + (' aria-current="page"' if key == active else "")
        + f">{_e(label)}</a>"
        for href, label, key in NAV
    )
    return f'<!doctype html><html lang="zh-CN"><head><base href="{local_base}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)} · Browser Use 评测</title><link rel="stylesheet" href="assets/style.css"><script defer src="assets/report.js"></script></head><body><header class="top"><nav class="wrap">{links}<button class="theme-toggle" onclick="theme()">切换主题</button></nav></header><main class="page-main">{body}</main><footer class="footer"><div class="wrap"><p>结论只适用于本轮预注册样本、Agent、模型与运行配置；展示调整不改变测量结果。</p></div></footer></body></html>'


def _evidence_viewer_page() -> str:
    body = _hero(
        "Execution evidence",
        "完整执行证据",
        "按原始结构展示本次物理执行的脱敏回答、evaluator、事件流、轨迹、计量与公开画面索引。",
    )
    body += (
        '<section class="section evidence-viewer-shell"><div class="wrap">'
        '<div class="evidence-viewer-head"><h2>执行证据 JSON</h2>'
        '<a id="evidence-raw-link" class="button-link" hidden download>下载原始 JSON</a></div>'
        '<p id="evidence-viewer-status" class="fine" role="status">正在读取证据…</p>'
        '<pre id="evidence-viewer-content" class="json-view" tabindex="0"></pre>'
        "</div></section><script>(()=>{"
        'const status=document.getElementById("evidence-viewer-status");'
        'const output=document.getElementById("evidence-viewer-content");'
        'const raw=document.getElementById("evidence-raw-link");'
        'const source=new URLSearchParams(location.search).get("src")||"";'
        r"const valid=/^artifacts\/[a-z0-9._-]+\/executions\/[0-9]+\/execution-evidence[.]json$/i.test(source);"
        'const english=document.documentElement.lang==="en";'
        'if(!valid){status.textContent=english?"Invalid evidence path.":"证据路径无效。";return;}'
        "raw.href=source;raw.hidden=false;"
        'fetch(new URL(source,document.baseURI),{credentials:"same-origin"})'
        ".then(response=>{if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();})"
        '.then(value=>{output.textContent=JSON.stringify(value,null,2);status.textContent=english?"Evidence loaded.":"证据已载入。";})'
        '.catch(error=>{status.textContent=(english?"Unable to load evidence: ":"无法读取执行证据：")+error.message;});'
        "})();</script>"
    )
    return _page(title="完整执行证据", active="analysis", body=body)


def _english_page(body: str) -> str:
    links = "".join(
        f'<a href="en/index.html#{anchor}">{label}</a>'
        for anchor, label in (
            ("results", "Results"),
            ("scope", "Scope"),
            ("method", "Method"),
            ("evidence", "Evidence"),
            ("review", "Review"),
            ("access", "Access"),
        )
    )
    return (
        '<!doctype html><html lang="en"><head><base href="../"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>English edition · Browser Use evaluation</title><link rel="stylesheet" href="assets/style.css">'
        '<script defer src="assets/report.js"></script></head><body><header class="top"><nav class="wrap">'
        + links
        + '<a href="">中文</a><button class="theme-toggle" onclick="theme()">Theme</button></nav></header>'
        '<main class="page-main">'
        + body
        + '</main><footer class="footer"><div class="wrap"><p>Conclusions apply only to this preregistered sample, agent, model, and runtime configuration.</p></div></footer></body></html>'
    )


def _hero(eyebrow: str, title: str, lead: str) -> str:
    return f'<section class="hero compact"><div class="wrap"><div class="eyebrow">{_e(eyebrow)}</div><h1>{_e(title)}</h1><p class="lead">{_e(lead)}</p></div></section>'


def _section(title: str, body: str) -> str:
    return f'<section class="section"><div class="wrap"><h2>{_e(title)}</h2>{body}</div></section>'


def _status(row: dict[str, Any]) -> Safe:
    passed = float(row["score"]) == 1
    return Safe(
        f'<span class="status-{"pass" if passed else "fail"}">{"通过" if passed else "失败"}</span>'
    )


def _json_pre(value: Any, *, css_class: str = "json-view") -> str:
    return f'<pre class="{css_class}">{_e(json.dumps(value, ensure_ascii=False, indent=2))}</pre>'


def _task_contract_html(task: dict[str, Any]) -> str:
    contract = task.get("contract") or {}
    rules = contract.get("evaluation_contract") or []
    cards = []
    for index, rule in enumerate(rules, start=1):
        evaluator = rule.get("evaluator") or "UnknownEvaluator"
        expected = rule.get("expected") or {}
        qualifiers = {
            key: value for key, value in rule.items() if key not in {"evaluator", "expected"}
        }
        cards.append(
            '<article class="evidence-card"><h3>'
            + _e(f"规则 {index} · {evaluator}")
            + '</h3><p><span class="badge">程序化验收</span>该规则必须得分 1.0。</p>'
            + _json_pre(expected)
            + (
                f"<details><summary>匹配选项与结果 schema</summary>{_json_pre(qualifiers)}</details>"
                if qualifiers
                else ""
            )
            + "</article>"
        )
    return (
        '<div class="muted-box"><strong>没有唯一参考点击路径。</strong> WebArena‑Verified 允许 Agent 自主选择任意 UI 路径；'
        "ground truth 是以下最终回答与必要网络副作用的合取。报告因此比较结果合同，而不把某条人工路径当作标准答案。</div>"
        + '<div class="evidence-pair">'
        + "".join(cards)
        + "</div>"
    )


def _failure_diagnosis(result: dict[str, Any] | None, row: dict[str, Any]) -> str:
    failed = [item for item in evaluator_summary(result) if float(item.get("score") or 0) < 1]
    if not failed:
        return "全部官方 evaluator 均通过。"
    parts = []
    for item in failed:
        messages: list[str] = []
        for assertion in item.get("assertions") or []:
            assertion_messages = assertion.get("assertion_msgs") or []
            if assertion_messages:
                messages.append(str(assertion_messages[0]))
            if assertion.get("error_msg"):
                messages.append(str(assertion["error_msg"]))
        if item.get("error"):
            messages.append(str(item["error"]))
        if not messages:
            messages.append(
                "最终回答与官方结构化答案不一致"
                if item["name"] == "AgentResponseEvaluator"
                else "未观察到满足合同的必要网络请求或副作用"
                if item["name"] == "NetworkEventEvaluator"
                else "该规则未通过"
            )
        expected, actual = item.get("expected"), item.get("actual")
        if expected is not None or actual is not None:
            comparison = (
                f"expected={json.dumps(expected, ensure_ascii=False, separators=(',', ':'))}; "
                f"actual={json.dumps(actual, ensure_ascii=False, separators=(',', ':'))}"
            )
            messages.append(comparison)
        exact = "；".join(
            dict.fromkeys(str(sanitize(message)).strip().rstrip("。.;； ") for message in messages)
        )
        if len(exact) > 420:
            exact = exact[:417].rstrip() + "…"
        parts.append(f"规则 {item['index']} {item['name']}：{exact}")
    if row.get("error"):
        parts.append(str(sanitize(row["error"])))
    response = row.get("agent_response") or {}
    if response.get("error_details"):
        parts.append(str(sanitize(response["error_details"])))
    return "；".join(dict.fromkeys(x.strip() for x in parts if x.strip())).rstrip("。.;； ") + "。"


def _evaluator_error_text(result: dict[str, Any] | None, row: dict[str, Any]) -> str:
    """Return the exact sanitized evaluator/runtime error without inferring a cause."""

    lines: list[str] = []
    for item in evaluator_summary(result):
        if float(item.get("score") or 0) == 1:
            continue
        messages = [
            str(message)
            for assertion in item.get("assertions") or []
            for message in [*(assertion.get("assertion_msgs") or []), assertion.get("error_msg")]
            if message
        ]
        if item.get("error"):
            messages.append(str(item["error"]))
        if not messages:
            messages.append(_rule_outcome_summary(item))
        for message in dict.fromkeys(messages):
            lines.append(f"规则 {item['index']} · {item['name']}: {sanitize(message)}")
    if row.get("error"):
        lines.append(f"运行错误: {sanitize(row['error'])}")
    response = row.get("agent_response") or {}
    if response.get("error_details"):
        lines.append(f"Agent 错误: {sanitize(response['error_details'])}")
    return "\n".join(lines) or "程序没有提供更具体的错误文本。"


def _failed_rule_labels(result: dict[str, Any] | None) -> list[str]:
    return [
        f"规则 {item['index']} {item['name']}"
        for item in evaluator_summary(result)
        if float(item.get("score") or 0) < 1
    ]


def _automatic_failure_type(result: dict[str, Any] | None) -> tuple[str, str]:
    """Classify the observable evaluator failure without claiming root cause."""

    names = {
        item["name"] for item in evaluator_summary(result) if float(item.get("score") or 0) < 1
    }
    if not names:
        return "", ""
    if names == {"AgentResponseEvaluator"}:
        return "response", "最终回答验收失败"
    if names == {"NetworkEventEvaluator"}:
        return "network", "网络副作用验收失败"
    if names == {"AgentResponseEvaluator", "NetworkEventEvaluator"}:
        return "mixed", "多项验收失败"
    raise ValueError(f"unknown failed evaluator combination: {sorted(names)}")


def _cause_code(verified: dict[str, dict[str, Any]], result_id: str) -> str:
    return str(((verified.get(result_id) or {}).get("subcategory") or {}).get("code") or "")


def _failure_point(code: str, steps: list[dict[str, Any]]) -> tuple[str, int | None]:
    """Locate the evidenced stage without inventing a precise failing click."""

    failed_step = next(
        (
            step
            for step in reversed(steps)
            if str(step.get("status") or "").lower() not in {"", "success"} or step.get("error")
        ),
        None,
    )
    last_step = int(steps[-1].get("step") or len(steps)) if steps else None
    if code.startswith("environment_"):
        return "环境准备后、Agent 读取前", None
    if code == "benchmark_har_capture":
        return "网络响应正文采集", None
    if code.startswith("benchmark_"):
        return "运行结束后的 evaluator 验收", last_step
    if code.startswith(("evidence_", "browser_evidence_")) or code == "evidence_capture":
        return "运行结束后的网络证据验收", last_step
    if code.startswith("site_"):
        return (f"第 {last_step} 步后的站点响应/保存" if last_step else "站点响应/保存"), last_step
    if failed_step:
        number = int(failed_step.get("step") or last_step or 1)
        return f"第 {number} 步 BrowserTool 执行异常", number
    if code == "agent_step_budget":
        return (f"第 {last_step} 步后达到步骤上限" if last_step else "达到步骤上限"), last_step
    return (
        f"第 {last_step} 步后的最终回答/任务状态" if last_step else "最终回答/任务状态"
    ), last_step


def _trajectory_context(
    steps: list[dict[str, Any]], step_number: int | None, failure_point: str
) -> tuple[str, str]:
    """Describe the linked step without claiming a successful action caused evaluator failure."""

    if step_number is None:
        return "", ""
    step = next((item for item in steps if int(item.get("step") or 0) == step_number), None)
    if step is None:
        return "", ""
    label = trajectory_label(step)
    prefix = "失败动作" if "BrowserTool 执行异常" in failure_point else "验收前最后一步"
    return prefix, label


def _remediation(code: str) -> tuple[str, str, bool]:
    """Return owner, current action, and whether this is an external interference."""

    if code == "benchmark_cdp_har_header_loss":
        return "评测证据接口", "站点日志已证实请求头正确；修复 CDP/HAR 漏采后整题重跑", True
    if code == "benchmark_har_capture":
        return "评测证据采集器", "JSON 正文采集已修复；整题隔离后重跑", True
    if code.startswith("benchmark_"):
        return "评测集 / evaluator", "修正合同并重跑；当前仅纳入官方分数", True
    if code.startswith("site_"):
        return "自托管站点", "修复依赖或保存链路，通过写入预检后重跑", True
    if code.startswith("environment_"):
        return "运行环境", "跨进程锁已上线；受污染任务待重跑", True
    if code.startswith("mixed_"):
        return "任务与 Agent", "先冻结参考状态，再重跑区分 Agent 错误", True
    if code.startswith(("evidence_", "browser_evidence_")) or code == "evidence_capture":
        return "浏览器证据接口", "保留为浏览器集成信号；补齐 HAR 后重跑", False
    return "Agent / 模型", "保留为目标变量；按轨迹修正策略或工具合同", False


def _failure_brief(result: dict[str, Any] | None, row: dict[str, Any]) -> str:
    labels = _failed_rule_labels(result)
    if labels:
        return f"{len(labels)} 项规则未通过：{'、'.join(labels)}。"
    if row.get("error"):
        return "执行未完成；错误信息见运行与证据。"
    return "全部官方 evaluator 均通过。"


def _rule_purpose(name: str) -> str:
    if name == "AgentResponseEvaluator":
        return "检查 Agent 最终回答的状态、字段和值是否符合官方答案合同。"
    if name == "NetworkEventEvaluator":
        return "检查浏览器网络记录中是否出现合同要求的页面副作用。"
    return "检查该题官方合同声明的结果条件。"


def _rule_outcome_summary(item: dict[str, Any]) -> str:
    if float(item.get("score") or 0) == 1:
        return (
            "已观察到满足合同的网络事件。"
            if item.get("name") == "NetworkEventEvaluator"
            else "最终回答符合该规则。"
        )
    messages = [
        str(message)
        for assertion in item.get("assertions") or []
        for message in (assertion.get("assertion_msgs") or [])[:1]
    ]
    message = messages[0] if messages else str(item.get("error") or "")
    status = re.fullmatch(r"Expected ([^,]+), got (.+)", message)
    if status:
        return f"最终状态不符：期望 {status.group(1)}，实际为 {status.group(2)}。"
    missing = re.fullmatch(
        r"Array is missing (\d+) expected element\(s\)\. Matched \((\d+)/(\d+)\)",
        message,
    )
    if missing:
        return f"最终数据缺少 {missing.group(1)} 项；官方期望 {missing.group(3)} 项，匹配 {missing.group(2)} 项。"
    if "Array value mismatch" in message:
        return "最终数据与官方期望值不一致。"
    if "No matching network event" in message:
        return "浏览器网络记录中没有出现满足该规则的事件。"
    return message or "该规则未通过。"


def _verification_html(task: dict[str, Any], result: dict[str, Any] | None, response: Any) -> str:
    items = evaluator_summary(result)
    if not items:
        return '<p class="muted-box">没有 evaluator 明细。</p>'
    cards = []
    for item in items:
        passed = float(item.get("score") or 0) == 1
        observed = response if item.get("name") == "AgentResponseEvaluator" else item.get("actual")
        technical = {
            key: item.get(key)
            for key in ("assertions", "error", "should_not_exist")
            if item.get(key) not in (None, [], {})
        }
        cards.append(
            f'<article class="verification-rule rule-{"pass" if passed else "fail"}">'
            '<header><div><span class="rule-index">'
            + _e(f"规则 {item['index']}")
            + "</span><h3>"
            + _e(item["name"])
            + f'</h3></div><span class="rule-verdict {"pass" if passed else "fail"}">'
            + ("通过" if passed else "失败")
            + "</span></header>"
            + f'<p class="rule-purpose">{_e(_rule_purpose(item["name"]))}</p>'
            + f'<p class="rule-summary">{_e(_rule_outcome_summary(item))}</p>'
            + '<details><summary>查看官方期望与本次观察</summary><div class="evidence-pair compact-evidence"><div><h4>官方期望</h4>'
            + _json_pre(item.get("expected"))
            + "</div><div><h4>本次观察</h4>"
            + _json_pre(observed)
            + "</div></div></details>"
            + (
                f"<details><summary>查看 evaluator 技术明细</summary>{_json_pre(technical)}</details>"
                if technical
                else ""
            )
            + "</article>"
        )
    return (
        '<div class="task-goal"><span>任务目标</span><p>'
        + _e(task.get("intent") or "—")
        + "</p><small>Agent 可自主选择 UI 路径；计分只看以下最终回答与必要网络副作用。</small></div>"
        + '<div class="verification-list">'
        + "".join(cards)
        + "</div>"
    )


def _trajectory_html(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return '<p class="muted-box">没有可展示的 BrowserTool 调用。</p>'
    rows = []
    for step in steps:
        step_number = int(step.get("step") or len(rows) + 1)
        failed = str(step.get("status") or "").lower() not in {"", "success"} or bool(
            step.get("error")
        )
        duration = step.get("duration_seconds")
        meta = f"Turn {step.get('turn') or '—'} · {_e(step.get('status') or 'unknown')}"
        if duration is not None:
            meta += f" · {float(duration):.2f} 秒"
        rows.append(
            f'<li id="trajectory-step-{step_number}" class="trajectory-step{" failure-step" if failed else ""}"><div><details{" open" if failed else ""}><summary>'
            + _e(trajectory_label(step))
            + f'</summary><p class="fine">{meta}</p><h4>调用参数</h4>'
            + _json_pre(step.get("arguments"))
            + "<h4>执行输出节选</h4>"
            + f'<pre class="log-view">{_e(result_excerpt(step))}</pre>'
            + "</details></div></li>"
        )
    return '<ol class="trajectory">' + "".join(rows) + "</ol>"


PUBLIC_EXECUTION_ARTIFACTS = {
    "execution_evidence": "execution-evidence.json",
}


def _extract_trace_screenshots(
    trace_path: Path, artifact_dir: Path, *, limit: int = 3
) -> list[dict[str, Any]]:
    """Publish the final Playwright screencast frames, never arbitrary trace resources."""

    if not trace_path.is_file():
        return []
    try:
        with zipfile.ZipFile(trace_path) as archive:
            archive_names = set(archive.namelist())
            frames = []
            for raw_line in archive.read("trace.trace").splitlines():
                try:
                    event = json.loads(raw_line)
                except (TypeError, ValueError):
                    continue
                if event.get("type") != "screencast-frame" or not event.get("sha1"):
                    continue
                name = "resources/" + str(event["sha1"])
                if name not in archive_names:
                    continue
                frames.append((float(event.get("timestamp") or 0), name))
            names = []
            for _timestamp, name in reversed(sorted(frames)):
                if name not in names:
                    names.append(name)
                if len(names) == limit:
                    break
            names.reverse()
            valid_frames = []
            for name in names:
                payload = archive.read(name)
                with Image.open(BytesIO(payload)) as image:
                    width, height = image.size
                    if width < 800 or height < 450:
                        continue
                suffix = ".png" if name.lower().endswith(".png") else ".jpeg"
                valid_frames.append((payload, width, height, suffix))
            output = []
            screenshot_dir = artifact_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            total = len(valid_frames)
            for index, (payload, width, height, suffix) in enumerate(valid_frames, start=1):
                destination = screenshot_dir / f"frame-{index}{suffix}"
                destination.write_bytes(payload)
                output.append(
                    {
                        "path": f"screenshots/{destination.name}",
                        "sha256": _sha256(destination),
                        "label": f"最后页面画面 {index}/{total}",
                        "source": "playwright-screencast-frame",
                        "width": width,
                        "height": height,
                    }
                )
            return output
    except (OSError, KeyError, zipfile.BadZipFile):
        return []


def _artifact_links(result_id: str, execution: int) -> str:
    root = f"artifacts/{result_id}/executions/{execution}"
    viewer = f"evidence-viewer.html?src={root}/execution-evidence.json"
    return (
        '<div class="page-links">'
        f'<a href="{viewer}">在页面中查看完整执行证据</a>'
        f'<a href="{root}/execution-evidence.json" download>下载原始 JSON</a>'
        "</div>"
    )


def _write_execution_artifacts(
    stage: Path,
    result_id: str,
    row: dict[str, Any],
    *,
    authoritative: bool,
) -> tuple[dict[str, Any], Any, Any, list[dict[str, Any]]]:
    paths = row.get("paths") or {}
    response = read_public_json(Path(paths.get("response", "")))
    evaluation = read_public_json(Path(paths.get("evaluation", "")))
    events = read_public_json(Path(paths.get("events", "")))
    steps = trajectory(events if isinstance(events, list) else [])
    execution = int(row["attempt"])
    artifact_dir = stage / "artifacts" / result_id / "executions" / str(execution)
    artifact_dir.mkdir(parents=True)
    measurements = _public_execution(row, authoritative=authoritative)
    screenshots = (
        _extract_trace_screenshots(Path(paths.get("agent_trace", "")), artifact_dir)
        if authoritative and float(row.get("score") or 0) < 1
        else []
    )
    bundle = {
        "schema": "lex_browser_use_public_execution_evidence/3",
        "result_id": result_id,
        "execution": execution,
        "authoritative": authoritative,
        "measurements": measurements,
        "source_evidence": evidence_metadata(row),
        "agent_response": response,
        "evaluation": evaluation,
        "agent_events": events,
        "trajectory": steps,
        "screenshots": screenshots,
    }
    _write_json(artifact_dir / PUBLIC_EXECUTION_ARTIFACTS["execution_evidence"], bundle)
    artifacts = {
        label: {
            "path": f"artifacts/{result_id}/executions/{execution}/{filename}",
            "sha256": _sha256(artifact_dir / filename),
        }
        for label, filename in PUBLIC_EXECUTION_ARTIFACTS.items()
    }
    return (
        {
            **measurements,
            "artifacts": artifacts,
            "screenshots": screenshots,
        },
        response,
        evaluation,
        steps,
    )


def _screenshot_gallery(result_id: str, executions: list[dict[str, Any]]) -> str:
    authoritative = next((item for item in executions if item.get("authoritative")), {})
    execution = int(authoritative.get("execution") or 0)
    screenshots = authoritative.get("screenshots") or []
    if not screenshots:
        return '<p class="muted-box">该执行没有可公开的 trace 页面画面；错误日志与执行证据仍可下载。</p>'
    root = f"artifacts/{result_id}/executions/{execution}"
    figures = "".join(
        '<figure><a href="'
        + _e(f"{root}/{item['path']}")
        + '"><img loading="lazy" src="'
        + _e(f"{root}/{item['path']}")
        + '" alt="'
        + _e(item["label"])
        + '"></a><figcaption>'
        + _e(item["label"])
        + f" · SHA-256 {_e(str(item['sha256'])[:12])}…</figcaption></figure>"
        for item in screenshots
    )
    return (
        "<p>下图是判分权威执行的最后页面画面，用于核对失败前的可见状态；"
        "它们不一定正好对应错误发生的瞬间。</p>"
        f'<div class="screenshot-grid">{figures}</div>'
    )


def _execution_evidence_html(result_id: str, executions: list[dict[str, Any]]) -> str:
    authoritative = [execution for execution in executions if execution["authoritative"]]
    if len(authoritative) != 1:
        raise ValueError("A logical result must have exactly one score-bearing execution")
    execution = authoritative[0]
    return (
        f'<p><b>任务重试次数</b>：<strong>{len(executions) - 1}</strong></p>'
        '<p>页面展示最终判定与对应日志；耗时、Tokens 和动作数包含全部尝试。完整历史保留在证据索引中。</p>'
        + _artifact_links(result_id, int(execution["execution"]))
        + '<details><summary>完整历史与文件校验</summary>'
        '<p><a href="data/evidence-index.json" download>下载全部尝试的证据索引</a></p>'
        + _table(
            ["公开文件", "SHA-256"],
            [[label, artifact["sha256"]] for label, artifact in execution["artifacts"].items()],
        )
        + '</details>'
    )


def _common_final_success_resource_rows(
    attempts: list[dict[str, Any]],
) -> tuple[int, list[list[Any]]]:
    task_ids = {int(row["task_id"]) for row in attempts}
    common = {
        task_id
        for task_id in task_ids
        if all(
            any(
                row["model"] == model
                and row["engine"] == engine
                and int(row["task_id"]) == task_id
                and float(row.get("score") or 0) == 1
                for row in attempts
            )
            for model in MODEL_LABELS
            for engine in ENGINE_LABELS
        )
    }

    def median(values: list[float]) -> float | None:
        return statistics.median(values) if values else None

    metric_values: dict[str, dict[str, float | None]] = {
        "duration": {},
        "browser_tool": {},
        "tokens": {},
        "memory": {},
        "cpu": {},
    }
    configurations: dict[str, tuple[str, str]] = {}
    for model in MODEL_LABELS:
        for engine in ENGINE_LABELS:
            key = f"{model}|{engine}"
            cohort = [
                {
                    **row,
                    **_execution_tool_metrics(row),
                }
                for row in attempts
                if row["model"] == model
                and row["engine"] == engine
                and int(row["task_id"]) in common
            ]
            configurations[key] = (model, engine)
            metric_values["duration"][key] = median(
                [float(row.get("duration_ms") or 0) for row in cohort]
            )
            metric_values["browser_tool"][key] = median(
                [float(row.get("browser_tool_duration_ms") or 0) for row in cohort]
            )
            metric_values["tokens"][key] = median(
                [float((row.get("model_usage") or {}).get("total_tokens") or 0) for row in cohort]
            )
            metric_values["memory"][key] = (
                median([float((row.get("resource") or {}).get("memory_peak_bytes") or 0) for row in cohort])
                if cohort
                else None
            )
            metric_values["cpu"][key] = median(
                [float((row.get("resource") or {}).get("cpu_ms") or 0) for row in cohort]
            )
    rows: list[list[Any]] = []
    for key, (model, engine) in configurations.items():
        rows.append(
            [
                MODEL_LABELS[model],
                ENGINE_LABELS[engine],
                _seconds(metric_values["duration"][key]),
                _seconds(metric_values["browser_tool"][key]),
                _number(metric_values["tokens"][key]),
                "—" if metric_values["memory"][key] is None else f"{metric_values['memory'][key] / 1048576:.1f} MiB",
                _seconds(metric_values["cpu"][key]),
            ]
        )
    return len(common), rows


def _failure_scope_counts(verified_causes: dict[str, dict[str, Any]]) -> list[tuple[str, int, str]]:
    copy = {
        "agent": ("Agent / 模型", "检索不全、选错对象、字段遗漏或流程提前结束"),
        "benchmark": ("评测实现 / 任务合同", "任务合同、验收器或证据采集实现存在缺陷"),
        "site": ("自托管站点", "搜索、会话或保存依赖故障"),
        "evidence": ("浏览器证据接口", "网络记录缺字段；需与站点真实请求核对"),
        "environment": ("共享环境", "并发执行改写了任务初始状态"),
        "mixed": ("参考状态与 Agent 混合", "参考漂移与 Agent 遗漏同时存在"),
    }

    def scope(code: str) -> str:
        for prefix in ("agent", "benchmark", "site", "environment", "mixed"):
            if code.startswith(prefix + "_"):
                return prefix
        if code.startswith(("evidence_", "browser_evidence_")):
            return "evidence"
        raise ValueError(f"unknown failure-cause scope: {code}")

    counts = Counter(
        scope(str((item.get("subcategory") or {}).get("code") or "evidence_capture"))
        for item in verified_causes.values()
    )
    return [
        (copy[key][0], counts[key], copy[key][1])
        for key in ("agent", "benchmark", "site", "evidence", "environment", "mixed")
        if counts[key]
    ]


def _failure_scope_figure(verified_causes: dict[str, dict[str, Any]]) -> str:
    rows = _failure_scope_counts(verified_causes)
    maximum = max((count for _, count, _ in rows), default=1)
    bars = "".join(
        '<article class="cause-bar"><div><b>'
        + _e(label)
        + f'</b><strong>{count}</strong></div><span>{_e(description)}</span><i style="width:{count / maximum * 100:.1f}%"></i></article>'
        for label, count, description in rows
    )
    return f'<div class="cause-bars" aria-label="{sum(count for _, count, _ in rows)} 个失败结果的复核原因分布">' + bars + "</div>"


def _automatic_failure_counts(attempts: list[dict[str, Any]]) -> Counter[str]:
    counts = Counter()
    for row in attempts:
        if float(row.get("score") or 0) == 1:
            continue
        result = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        key, _ = _automatic_failure_type(result if isinstance(result, dict) else None)
        if key:
            counts[key] += 1
    return counts


def _automatic_failure_figure(counts: Counter[str]) -> str:
    items = (
        ("network", "只有网络副作用规则未通过", "evaluator 没找到合同要求的请求或可观察副作用"),
        ("response", "只有最终回答规则未通过", "最终回答与结构化答案合同不一致"),
        ("mixed", "两类规则同时未通过", "最终回答和网络副作用均未满足合同"),
    )
    cards = "".join(
        f'<article><strong>{counts[key]}</strong><b>{_e(label)}</b><p>{_e(description)}</p></article>'
        for key, label, description in items
    )
    return '<div class="verdict-strip" aria-label="官方 evaluator 的失败规则组合">' + cards + "</div>"


def _article_body(
    paired: dict[str, Any],
    attempts: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    verified_causes: dict[str, dict[str, Any]],
    review: dict[str, Any],
    cohort_commits: dict[str, str],
    automatic_failure_counts: Counter[str],
) -> str:
    total_task_count = len(tasks)
    excluded = _external_interference_task_ids(verified_causes)
    eligible_attempts = [
        row for row in attempts if int(row["task_id"]) not in excluded
    ]
    diagnostic_task_count = total_task_count - len(excluded)
    task_count = total_task_count
    summary = _primary_summary(attempts)
    paired = _paired_summary(attempts)
    raw_failed_result_count = sum(float(row.get("score") or 0) < 1 for row in attempts)
    comparison_rows = [
        [
            MODEL_LABELS[model],
            ENGINE_LABELS[item["baseline_engine"]],
            ENGINE_LABELS[item["comparison_engine"]],
            item["paired"],
            item["both_pass"],
            item["baseline_only"],
            item["comparison_only"],
            item["both_fail"],
        ]
        for model, model_pairs in paired.items()
        for item in model_pairs["comparisons"].values()
    ]
    discordant = sum(row[5] + row[6] for row in comparison_rows)
    cause_rows = [
        [
            Safe(f'<a href="results/{_e(result_id)}.html#verification">{_e(result_id)}</a>'),
            (diagnosis.get("subcategory") or {}).get("zh") or "—",
            (diagnosis.get("reason") or {}).get("zh") or "—",
        ]
        for result_id, diagnosis in sorted(verified_causes.items())
    ]
    if raw_failed_result_count:
        cause_section = (
            _failure_cause_table(verified_causes)
            + '<details class="evidence-details"><summary>查看每个失败结果的核验结论</summary>'
            + _table(
                ["结果编号", "原因分类", "核验结论"],
                cause_rows,
                caption="人工核验用于解释官方失败结果，不修改分数。修复后需要重新运行才能产生新成绩。",
            )
            + "</details>"
            if len(cause_rows) == raw_failed_result_count
            else f'<p class="callout">当前有 {raw_failed_result_count} 个原始失败结果，详细原因仍在核验中；未核验结果不展示推测。</p>'
        )
    else:
        cause_section = '<p class="callout success">本轮没有失败结果，无需生成失败根因记录。</p>'
    common_count, resource_rows = _common_final_success_resource_rows(attempts)
    clean = _primary_summary(eligible_attempts)
    clean_rows = [
        [
            MODEL_LABELS[model],
            ENGINE_LABELS[engine],
            f"{values['passed']}/{values['completed']}",
            _pct(values["score"]),
        ]
        for model, model_summary in clean.items()
        for engine, values in model_summary.items()
    ]
    coverage = review.get("coverage") or {}
    reviewed_attempts = int(coverage.get("reviewed_attempts") or 0)
    recorded_attempts = int(coverage.get("recorded_attempts") or 0)
    clean_task_count = diagnostic_task_count
    return (
        '<article class="publication-article interpretation-v2"><header class="essay-head"><div class="eyebrow">Browser Use 能力评测</div>'
        f"<h1>{task_count} 题 Browser Use 完整结果：Chromium 与 Moli 的成功率、代价与失败根因</h1>"
        f'<p class="lead">首页按预注册的 {total_task_count} 题展示四种配置的官方 evaluator 结果。其中 {len(excluded)} 题经复核存在验收合同、站点、共享状态或参考状态干扰；这些题仍保留在 {total_task_count} 题官方结果中，另外提供 {diagnostic_task_count} 题事后诊断子集。</p>'
        '<p class="source-note">WebArena‑Verified 1.2.3 · CowAgent · 模型推理强度固定为低 · 官方程序化 evaluator</p>'
        '<p class="page-links"><a href="datasets/">查看评测集</a><a href="analysis/">下钻全部结果</a><a href="data/interpretation-evidence.json" download>下载论点与证据</a></p></header>'
        f'<section><h2>{total_task_count} 题核心指标</h2>'
        + _table(
            ["模型", "浏览器", "通过任务", "通过率", "Agent 总耗时", "BrowserTool 耗时", "Tokens"],
            _summary_comparison_rows(summary),
            table_id="home-core-metrics",
            classes="core-comparison-table",
        )
        + f'<p class="source-note">每个模型×浏览器配置都保留完整 {task_count} 题。以上是官方 evaluator 决定的最终逻辑结果；物理执行记录可在单题证据页下钻。</p></section>'
        '<section><h2>按同题比较浏览器</h2><p>浏览器对比必须留在同一模型层内。汇总数会隐藏互换的成败任务；下表给出同题对照。</p>'
        + _table(["模型", "浏览器 A", "浏览器 B", "同题数", "两边都通过", "A 通过、B 失败", "A 失败、B 通过", "两边都失败"], comparison_rows, caption=f"每行只比较同一模型下的 {task_count} 个配对任务。")
        + f'<p>两个模型层合计有 {discordant} 个单边通过，可用于定位兼容性问题；样本量和方向都不支持总体优劣判断。<a href="analysis/#same-task">查看 {task_count} 题的全配置矩阵 →</a></p></section>'
        '<section id="failure-findings"><h2>程序判分落点与复核根因</h2><p>程序判分先说明哪类规则未通过；人工复核再沿最终回答、evaluator 断言、HAR 和 Agent 轨迹追到直接原因。这两个层次需要分开。</p><h3>程序判分落点</h3>'
        + _automatic_failure_figure(automatic_failure_counts)
        + '<p class="source-note">“网络副作用未通过”只描述程序判分位置。它可能源于 Agent 没有提交，也可能源于 evaluator、站点或 HAR 采集问题。</p><h3>复核根因</h3>'
        + _failure_scope_figure(verified_causes)
        + cause_section
        + f'<h3>{total_task_count} 题官方结果与事后诊断分开看</h3><p>首页主结果始终使用完整 {total_task_count} 题官方 evaluator 分母。为了观察排除已知外部干扰后的差异，下表再按整题、四种配置一致排除 {len(excluded)} 题，得到 {clean_task_count} 题事后诊断子集。</p>'
        + _table(["模型", "浏览器", f"{clean_task_count} 题通过", "通过率"], clean_rows, table_id="home-qualified-results", classes="core-comparison-table")
        + f'<p class="source-note">这是事后诊断子集，不是正式分数，也不替代 {total_task_count} 题官方结果。{raw_failed_result_count} 个原始失败记录继续保存在结果页和核验记录中。</p></section>'
        '<section><h2>审查覆盖与边界</h2><div class="audit-ladder"><article><span>1</span><b>证据完整性</b><p>'
        + f'{reviewed_attempts}/{recorded_attempts} 个证据包由程序核对文件存在性、哈希、JSON 可读性、回答 / 分数 / 状态一致性和运行身份。</p></article><article><span>2</span><b>官方程序验收</b><p>每题按最终回答和必要网络副作用判分；没有 LLM judge。</p></article><article><span>3</span><b>失败根因复核</b><p>{len(verified_causes)}/{raw_failed_result_count} 个原始最终失败结果逐条阅读错误、断言、最终回答、HAR 和 Agent 轨迹，归类并给出处理边界。</p></article></div>'
        + '<p class="callout"><strong>审查分为完整性检查和失败根因复核。</strong> 前者核对证据包与 evaluator 记录，后者检查失败的直接原因；已通过结果没有再做独立语义盲审，其判定依据仍是官方 evaluator。<a href="review/">查看核验记录 →</a></p></section>'
        f'<section><h2>完整 {task_count} 题的速度与浏览器资源</h2><p>均值、P50、P95 都在全部注册配置中统一比较。CPU、内存和网络只计浏览器容器，不含 CowAgent 或模型服务；模型会改变动作数量和页面停留时间，因此这里比较整套配置，不能单独归因于浏览器内核。</p>'
        + _resource_comparison_table(summary)
        + '<p class="source-note"><span class="winner-mark" aria-hidden="true">优</span> 仅对方向明确的指标标记全部注册配置中数值唯一最低者；占用率与网络传输量不标优劣。CPU 时间与网络总量累计逻辑结果使用的全部执行；内存来自 Browser Use 执行阶段的浏览器容器采样。</p></section>'
        '<section><h2>共同成功任务的性能与资源</h2><p>下表仅供诊断，限定为所有配置最终都通过的 '
        + str(common_count)
        + ' 题，并以每题最终逻辑结果包含的全部物理执行计算 P50。这个事后选出的子集不用于性能排名；完整样本的指标见上表。</p>'
        + _table(["模型", "浏览器", "Agent 链路 P50", "BrowserTool P50", "Tokens P50", "峰值内存 P50", "CPU 时间 P50"], resource_rows, table_id="home-resource-table", classes="core-comparison-table")
        + '<p class="source-note">Agent 链路还包含模型推理和页面等待，因此不能解读为纯内核性能。</p></section>'
        f'<section><h2>工程结论</h2><p>完整 {task_count} 题官方结果是本报告的主口径；{clean_task_count} 题事后诊断子集只用于分离已确认的外部干扰，不改写成绩。</p>'
        '<p>通过率、耗时和资源的具体差异以上表为准。每个配置每题只有一个最终逻辑结果；模型会改变动作数量，站点和验收也可能影响分数，不能把端到端差异单独归因于浏览器。已确认的外部干扰按整题隔离并保留原始分数，修复后须另行完整复测。</p></section></article>'
    )


def _filters(
    *,
    attempts: bool = False,
    dataset: bool = False,
    cause_options: list[tuple[str, str]] | None = None,
) -> str:
    engine_options = "".join(
        f'<option value="{_e(engine)}">{_e(label)}</option>'
        for engine, label in ENGINE_LABELS.items()
    )
    extra = (
        '<label>模型 <select data-filter="model"><option value="">全部</option>'
        + "".join(
            f'<option value="{_e(model)}">{_e(label)}</option>'
            for model, label in MODEL_LABELS.items()
        )
        + "</select></label>"
        + f'<label>浏览器 <select data-filter="engine"><option value="">全部</option>{engine_options}</select></label>'
        '<label>失败环节 <select data-filter="failure"><option value="">全部</option><option value="none">无</option><option value="response">最终回答</option><option value="network">网络副作用</option><option value="mixed">多项</option></select></label>'
        + '<label>复核原因 <select data-filter="cause"><option value="">全部</option>'
        + "".join(
            f'<option value="{_e(code)}">{_e(label)}</option>'
            for code, label in (cause_options or [])
        )
        + '</select></label>'
        if attempts
        else ""
    )
    result_filter = (
        ""
        if dataset
        else (
            '<label>结果 <select data-filter="result"><option value="">全部</option>'
            '<option value="pass">通过</option><option value="fail"'
            + (" selected" if attempts else "")
            + ">失败</option></select></label>"
        )
    )
    placeholder = "任务、意图、验收合同" if dataset else "任务、意图、答案、错误"
    return (
        f'<div class="filters"><label>全文搜索 <input data-search type="search" placeholder="{placeholder}"></label>'
        '<label>层级 <select data-filter="tier"><option value="">全部</option><option value="easy">简单</option><option value="medium">中等</option><option value="hard">困难</option></select></label>'
        '<label>站点 <select data-filter="site"><option value="">全部</option><option value="shopping">Shopping</option><option value="shopping_admin">Shopping Admin</option><option value="reddit">Reddit</option><option value="gitlab">GitLab</option></select></label>'
        '<label>类型 <select data-filter="kind"><option value="">全部</option><option>RETRIEVE</option><option>NAVIGATE</option><option>MUTATE</option></select></label>'
        + result_filter
        + extra
        + '<span class="count-note" data-visible-count></span></div>'
    )


def _task_table(tasks: list[dict[str, Any]]) -> str:
    rows = []
    for task in tasks:
        task_id, site, kind = int(task["task_id"]), task["sites"][0], task["task_type"]
        values = [
            Safe(f'<a href="datasets/{task_id}.html">#{task_id}</a>'),
            TIER_LABELS[task["tier"]],
            SITE_LABELS.get(site, site),
            kind,
            task["estimated_min_steps"],
            task["rationale"],
            task.get("intent") or "—",
            "是" if task["official_hard"] else "否",
            Safe(f'<a href="datasets/{task_id}.html">查看任务合同</a>'),
        ]
        cells = "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in values)
        search = " ".join(
            str(x)
            for x in (
                task_id,
                task.get("intent"),
                task.get("rationale"),
                json.dumps(task.get("contract") or {}, ensure_ascii=False),
            )
        )
        rows.append(
            f'<tr data-tier="{task["tier"]}" data-site="{site}" data-kind="{kind}" data-search="{_e(search)}">{cells}</tr>'
        )
    labels = [
        "任务",
        "层级",
        "站点",
        "类型",
        "最少步",
        "入选理由",
        "官方任务意图",
        "官方 Hard",
        "任务合同",
    ]
    headers = "".join(
        f'<th><button class="sort-button" onclick="sortTable(\'task-table\',{i})">{_e(x)} ↕</button></th>'
        for i, x in enumerate(labels)
    )
    return (
        _filters(dataset=True)
        + f'<div class="table-wrap"><table id="task-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _diagnosis_dialog(
    *,
    result_id: str,
    task_id: int,
    configuration: str,
    failure_point: str,
    automatic_failure: str,
    evaluator_error: str,
    subcategory: str,
    reason: str,
    owner: str,
    action: str,
    step_link: str,
    step_context_label: str = "",
    step_context: str = "",
) -> str:
    dialog_id = f"diagnosis-{result_id}"
    detail_url = f"results/{result_id}.html"
    return (
        f'<dialog class="diagnosis-dialog" id="{_e(dialog_id)}" aria-labelledby="{_e(dialog_id)}-title">'
        '<div class="diagnosis-dialog-head"><div><span class="table-kicker">失败诊断</span>'
        f'<h2 id="{_e(dialog_id)}-title">Task #{task_id} · {_e(configuration)}</h2></div>'
        f'<button class="dialog-close" type="button" onclick="closeDiagnosis(\'{_e(dialog_id)}\')" aria-label="关闭">×</button></div>'
        '<div class="diagnosis-dialog-grid">'
        f'<div><b>发生位置</b><a href="{_e(step_link)}">{_e(failure_point)}</a></div>'
        f"<div><b>程序判定</b>{_e(automatic_failure or '未分类')}</div>"
        f"<div><b>复核根因</b>{_e(subcategory or '待复核')}</div>"
        f"<div><b>负责范围</b>{_e(owner)}</div></div>"
        + (
            f'<div class="dialog-step"><b>{_e(step_context_label)}</b><a href="{_e(step_link)}">{_e(step_context)}</a></div>'
            if step_context
            else ""
        )
        + '<section class="dialog-error"><h3>程序报错</h3>'
        f'<pre class="log-view">{_e(evaluator_error)}</pre></section>'
        '<section class="dialog-review"><h3>复核结论</h3>'
        f"<p>{_e(reason or '尚未完成根因复核。')}</p><p><strong>处理：</strong>{_e(action)}</p></section>"
        '<div class="dialog-actions">'
        f'<a class="button-link" href="{_e(detail_url)}#verification">查看期望与实际值</a>'
        f'<a href="{_e(step_link)}">查看失败附近轨迹</a>'
        f'<a href="{_e(detail_url)}#screenshots">查看页面画面</a>'
        f'<a href="{_e(detail_url)}#runtime">查看日志与完整证据</a>'
        f'<button type="button" onclick="closeDiagnosis(\'{_e(dialog_id)}\')">关闭</button></div></dialog>'
    )


def _attempt_table(
    attempts: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    verified_causes: dict[str, dict[str, Any]] | None = None,
) -> str:
    verified_causes = verified_causes or {}
    task_meta = {int(task["task_id"]): task for task in tasks}
    rows = []
    dialogs = []
    cause_options: dict[str, str] = {}
    for row in attempts:
        p = _public_attempt(row)
        result = "pass" if p["score"] == 1 else "fail"
        result_record = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        failure, automatic_failure = _automatic_failure_type(result_record)
        subcategory, reason = localized_cause(verified_causes, p["id"])
        code = _cause_code(verified_causes, p["id"])
        if result == "fail" and code and subcategory:
            cause_options[code] = subcategory
        event_record = read_public_json(Path((row.get("paths") or {}).get("events", "")))
        steps = trajectory(event_record if isinstance(event_record, list) else [])
        failure_point, step_number = _failure_point(code, steps)
        step_context_label, step_context = _trajectory_context(steps, step_number, failure_point)
        owner, action, _external = _remediation(code)
        task = task_meta.get(p["task_id"], {})
        point_link = (
            f"results/{p['id']}.html#trajectory-step-{step_number}"
            if step_number
            else f"results/{p['id']}.html#diagnosis"
        )
        dialog_id = f"diagnosis-{p['id']}"
        if result == "fail":
            dialogs.append(
                _diagnosis_dialog(
                    result_id=p["id"],
                    task_id=p["task_id"],
                    configuration=f"{MODEL_LABELS[p['model']]} · {ENGINE_LABELS[p['engine']]}",
                    failure_point=failure_point,
                    automatic_failure=automatic_failure,
                    evaluator_error=_evaluator_error_text(result_record, row),
                    subcategory=subcategory,
                    reason=reason,
                    owner=owner,
                    action=action,
                    step_link=point_link,
                    step_context_label=step_context_label,
                    step_context=step_context,
                )
            )
        values = [
            Safe(f'<a href="results/{p["id"]}.html">{p["id"]}</a>'),
            Safe(
                f'<strong>{_e(MODEL_LABELS[p["model"]])}</strong><span class="table-kicker">'
                f"{_e(ENGINE_LABELS[p['engine']])}</span>"
            ),
            Safe(f'<a href="datasets/{p["task_id"]}.html">#{p["task_id"]}</a>'),
            _status(row),
            max(0, len(row.get("_source_executions") or [row]) - 1),
            Safe(f'<a href="{point_link}">{_e(failure_point)}</a>') if result == "fail" else "—",
            automatic_failure or "—",
            subcategory or "待复核" if result == "fail" else "—",
            Safe(f'<strong>{_e(owner)}</strong><span class="table-kicker">{_e(action)}</span>')
            if result == "fail"
            else "—",
            Safe(
                (
                    f'<button class="diagnosis-button" type="button" onclick="openDiagnosis(\'{_e(dialog_id)}\')">查看诊断</button>'
                )
                if result == "fail"
                else f'<a href="results/{p["id"]}.html">详情</a>'
            ),
        ]
        cells = "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in values)
        rows.append(
            f'<tr data-tier="{p["tier"]}" data-site="{(task.get("sites") or [""])[0]}" '
            f'data-kind="{task.get("task_type") or _task_type(p["task_id"])}" data-result="{result}" '
            f'data-model="{p["model"]}" data-engine="{p["engine"]}" data-failure="{failure}" '
            f'data-cause="{_e(code if result == "fail" else "")}" '
            f'data-search="{_e(" ".join([p["id"], str(task.get("intent") or ""), automatic_failure, subcategory, reason, _failure_diagnosis(result_record, row)]))}">{cells}</tr>'
        )
    headers = "".join(
        f"<th>{_e(x)}</th>"
        for x in [
            "结果",
            "运行配置",
            "任务",
            "判定",
            "任务重试次数",
            "失败位置",
            "程序自动定位",
            "复核根因",
            "负责对象与处理",
            "证据",
        ]
    )
    return (
        _filters(attempts=True, cause_options=sorted(cause_options.items(), key=lambda item: item[1]))
        + f'<div class="table-wrap"><table id="result-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        + "".join(dialogs)
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _release_details(release: dict[str, Any]) -> str:
    rows = release.get("changes") or []
    details = (
        "<details><summary>查看修正依据、处理与影响</summary>"
        + _table(
            ["项目", "发现的问题", "正式处理", "结果影响与边界"],
            rows,
            table_id="change-table",
        )
        + "</details>"
        if rows
        else ""
    )
    return (
        f'<div class="eyebrow">{_e(release.get("date") or "未记录")}</div>'
        f"<h3>{_e(release.get('title') or '未命名版本')}</h3>"
        f"<p>{_e(release.get('summary') or '')}</p>{details}"
    )


def _release_history(releases: list[dict[str, Any]], history_scope: str) -> str:
    if not releases:
        return "<p>暂无发布记录。</p>"
    latest, *older = releases
    body = _section(
        "当前版本",
        f'<article class="release-current" id="{_e(latest.get("id") or "current")}">'
        + _release_details(latest)
        + "</article>",
    )
    if older:
        entries = "".join(
            f'<article class="release-archive" id="{_e(release.get("id") or "")}">'
            + _release_details(release)
            + "</article>"
            for release in older
        )
        body += _section(
            "历史版本",
            f'<details class="release-history"><summary>展开 {len(older)} 个较早版本</summary>{entries}</details>',
        )
    body += _section(
        "范围与下载",
        f'<p>{_e(history_scope)}</p><p class="page-links"><a href="report-changes.json" download>下载结构化版本记录</a><a href="https://github.com/lexmount/browser-eval/commits/main">查看仓库变更</a></p>',
    )
    return body


def _review_history(rounds: list[dict[str, Any]]) -> str:
    if not rounds:
        return "<p>暂无核验记录。</p>"
    cards = []
    for item in rounds:
        fields = "".join(
            f"<div><b>{_e(label)}</b><p>{_e(item.get(key) or '—')}</p></div>"
            for label, key in (
                ("核验范围", "scope"),
                ("发现", "findings"),
                ("处理", "corrections"),
                ("遗留事项", "remaining"),
            )
        )
        cards.append(
            f'<article class="review-round" id="{_e(item.get("id") or "")}"><header><div>'
            f'<div class="eyebrow">{_e(item.get("date") or "未记录")}</div>'
            f"<h3>{_e(item.get('title') or '未命名核验')}</h3></div>"
            f'<span class="review-status">{_e(item.get("status") or "未记录")}</span>'
            f'</header><div class="review-fields">{fields}</div></article>'
        )
    return "".join(cards)


def build_report(*, evidence: Path | Iterable[Path], output: Path, selection_path: Path) -> Path:
    profile = _read_json(Path("evaluation/browser-use/current-report.json"))
    _configure_engine_labels(profile)
    _configure_models(profile)
    selection = load_pilot(selection_path)
    bundles = _load_evidence_bundles(evidence)
    source_attempts = [row for bundle in bundles for row in bundle["source_attempts"]]
    manifest, config = bundles[0]["manifest"], bundles[0]["config"]
    review_attempts = []
    for bundle in bundles:
        for item in bundle["review"].get("attempts") or []:
            review_attempts.append({**item, "model": bundle["model"]})
    review = {
        "coverage": {
            "recorded_attempts": sum(
                int((bundle["review"].get("coverage") or {}).get("recorded_attempts") or 0)
                for bundle in bundles
            ),
            "reviewed_attempts": sum(
                int((bundle["review"].get("coverage") or {}).get("reviewed_attempts") or 0)
                for bundle in bundles
            ),
        },
        "attempts": review_attempts,
        "disagreements": [
            item for bundle in bundles for item in bundle["review"].get("disagreements") or []
        ],
        "unresolved": [
            item for bundle in bundles for item in bundle["review"].get("unresolved") or []
        ],
        "bindings": {bundle["model"]: bundle["review"].get("bindings") or {} for bundle in bundles},
    }
    protocol, environment = manifest.get("protocol") or {}, manifest.get("environment") or {}
    attempts = [
        row
        for bundle in bundles
        for row in _logical_results(
            bundle["source_attempts"], bundle["manifest"].get("protocol") or {}
        )
    ]
    public_attempts = [_public_attempt(r) for r in attempts]
    attempts_sha256 = {bundle["model"]: bundle["attempts_sha256"] for bundle in bundles}
    failed_result_ids = {row["id"] for row in public_attempts if float(row.get("score") or 0) < 1}
    default_failure_cause_review = load_failure_cause_review()
    verified_causes: dict[str, dict[str, Any]] = {}
    failure_reviews: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        model_failed_ids = {
            _attempt_id(row)
            for row in attempts
            if row["model"] == bundle["model"] and float(row.get("score") or 0) < 1
        }
        failure_reviews[bundle["model"]] = extract_failure_cause_review_cohort(
            default_failure_cause_review, bundle["model"]
        )
        verified_causes.update(
            validate_failure_cause_review(
                default_failure_cause_review,
                model_id=bundle["model"],
                attempts_sha256=bundle["attempts_sha256"],
                failed_result_ids=model_failed_ids,
                require_complete=False,
            )
        )
    for source, public in zip(attempts, public_attempts, strict=True):
        evaluation = read_public_json(Path((source.get("paths") or {}).get("evaluation", "")))
        public["automatic_failure_type"] = _automatic_failure_type(evaluation)[1]
        subcategory, reason = localized_cause(verified_causes, public["id"])
        public["verified_failure_subcategory"] = subcategory
        public["verified_failure_reason"] = reason
    official = (manifest.get("environment") or {}).get("official_evaluator") or {}
    tasks = _resolve_tasks(
        selection, expected_data_checksum=str(official.get("data_checksum") or "") or None
    )
    task_count = len(tasks)
    example_task_id = int(tasks[0]["task_id"]) if tasks else None
    result_count = len(attempts)
    max_agent_steps = str(
        config.get("max_agent_steps") or protocol.get("max_agent_steps") or "未记录"
    )
    site_access_host = str(
        environment.get("site_access_host") or config.get("site_access_host") or "未记录"
    )
    preparation_receipt_count = sum(
        Path(str((row.get("paths") or {}).get("environment_prepare") or "")).is_file()
        for row in source_attempts
    )
    preparation_receipts_complete = bool(source_attempts) and preparation_receipt_count == len(
        source_attempts
    )
    tier_counts = {
        tier: sum(task["tier"] == tier for task in tasks) for tier in ("easy", "medium", "hard")
    }
    summary_by_model, paired_by_model = _primary_summary(attempts), _paired_summary(attempts)
    excluded_task_ids = _external_interference_task_ids(verified_causes)
    qualified_attempts = [
        row for row in attempts if int(row["task_id"]) not in excluded_task_ids
    ]
    qualified_tasks = [
        task for task in tasks if int(task["task_id"]) not in excluded_task_ids
    ]
    qualified_summary = _primary_summary(qualified_attempts)
    model = "、".join(MODEL_LABELS.values())
    adoption_evidence = _read_json(Path("research/browser-use-benchmark-adoption.json"))
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    marker = output / ".generated-report"
    if output.exists() and (
        not marker.is_file() or marker.read_text(encoding="utf-8") != "browser-use-report/2\n"
    ):
        raise ValueError(f"refusing to replace an unmarked report directory: {output}")
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=parent))
    try:
        for folder in (
            "assets",
            "datasets",
            "results",
            "engines",
            "changes",
            "methodology",
            "analysis",
            "review",
            "adoption",
            "reproduce",
            "accounts",
            "data",
            "artifacts",
        ):
            (stage / folder).mkdir()
        (stage / "assets/style.css").write_text(REPORT_STYLE, encoding="utf-8")
        (stage / "assets/report.js").write_text(REPORT_JS, encoding="utf-8")
        (stage / "evidence-viewer.html").write_text(_evidence_viewer_page(), encoding="utf-8")
        if not METHODOLOGY_FIGURE.is_file():
            raise FileNotFoundError(f"missing methodology figure: {METHODOLOGY_FIGURE}")
        shutil.copy2(
            METHODOLOGY_FIGURE,
            stage / "assets/webarena-autonomous-evaluation-flow.png",
        )
        cohort_commits = {
            bundle["model"]: str(
                (bundle["manifest"].get("protocol") or {}).get("browser_eval_commit")
                or "未记录"
            )
            for bundle in bundles
        }
        automatic_failure_counts = _automatic_failure_counts(attempts)
        article = _article_body(
            paired_by_model,
            attempts,
            tasks,
            verified_causes,
            review,
            cohort_commits,
            automatic_failure_counts,
        )
        home = f'<section class="section"><div class="wrap">{article}</div></section>'
        (stage / "index.html").write_text(
            _page(title="首页", active="home", body=home), encoding="utf-8"
        )
        release_data = _read_json(Path("research/report-changes.json"))
        releases = release_data.get("browser_use_releases") or []
        changes = _hero(
            "Report releases",
            "报告版本说明",
            "每个版本只记录影响读者判断的正式修正；结论、分母、耗时与证据影响直接展示，技术依据按需展开。",
        ) + _release_history(releases, str(release_data.get("history_scope") or ""))
        _write_json(stage / "report-changes.json", release_data)
        (stage / "changes/index.html").write_text(
            _page(title="变更记录", active="changes", body=changes, depth=1), encoding="utf-8"
        )

        datasets = _hero(
            "Dataset",
            f"候选 benchmark、选择依据与 {task_count} 个预注册任务",
            "先区分最终结果、动作过程和模型裁判，再谈采用率；同一家族不同变体的成绩不能合并。",
        )
        datasets += _section(
            "公开采用信号 Top 3",
            _adoption_top3(adoption_evidence),
        )
        datasets += (
            _section(
                "Mind2Web 的拆分不是 easy / medium / hard",
                f"<p>原始 Mind2Web 使用 cross-task、cross-website、cross-domain 衡量泛化；Online‑Mind2Web 没有统一三档；Mind2Web 2 的 short / medium / long 按动作跨度划分。本报告的简单 {tier_counts['easy']}、中等 {tier_counts['medium']}、困难 {tier_counts['hard']} 是预注册的操作长度层级。</p>",
            )
            + _section(
                "怎样阅读任务 ground truth",
                "<p><strong>WebArena‑Verified 的 ground truth 不是一条固定点击脚本。</strong>每题声明一个结构化最终回答合同，并在需要确认页面副作用时附加一个或多个网络事件合同。所有合同都通过才记 1 分。任务页只解释合同；各浏览器的实际回答、失败规则和 CowAgent 自主轨迹统一放在结果页。</p>",
            )
            + _section(
                "题集范围",
                f"<p>本轮在模型执行前固定 {task_count} 个任务，覆盖四个已部署站点以及 RETRIEVE、NAVIGATE、MUTATE 三种最终合同。这里仅描述题目与验收规则；通过率、耗时、轨迹和可完成性观察统一放在结果分析中。</p>",
            )
            + _section(f"{task_count} 个任务", _task_table(tasks))
        )
        (stage / "datasets/index.html").write_text(
            _page(title="评测集", active="datasets", body=datasets, depth=1), encoding="utf-8"
        )

        by_attempt = {(r["model"], r["engine"], int(r["task_id"])): r for r in attempts}
        for task in tasks:
            task_id, site = int(task["task_id"]), task["sites"][0]
            detail = _hero(
                f"Task #{task_id}",
                task["rationale"],
                "官方任务身份、公开意图与程序化验收合同。运行结果位于结果分析，不混入评测集定义。",
            )
            detail += _section(
                "任务身份",
                f'<div class="detail-grid"><div class="kv"><b>站点</b>{_e(SITE_LABELS.get(site, site))}</div><div class="kv"><b>本项目层级</b>{_e(TIER_LABELS[task["tier"]])} · {_e(task["estimated_min_steps"])} 步起</div><div class="kv"><b>任务类型</b>{_e(task["task_type"])}</div><div class="kv"><b>官方 hard subset</b>{"是" if task["official_hard"] else "否"}</div></div><h3>公开任务意图</h3><p>{_e(task["intent"])}</p><p class="fine">官方来源：<a href="https://github.com/ServiceNow/webarena-verified">WebArena‑Verified {_e(environment.get("webarena_verified_version", "—"))}</a> · Task #{task_id} · 模板 ID {task.get("intent_template_id") or "—"} · revision {task.get("revision") or "—"}</p>',
            )
            detail += _section("Ground truth · 官方验收合同", _task_contract_html(task))
            detail += _section(
                "查看本题运行结果",
                '<p>评测集页面保持为冻结输入；实测输出、判分和轨迹在结果页查看。</p><div class="page-links">'
                + "".join(
                    f'<a href="results/{model_id}-{engine}-{task_id}.html">'
                    f"{_e(MODEL_LABELS[model_id])} · {_e(ENGINE_LABELS[engine])} 结果</a>"
                    for model_id in MODEL_LABELS
                    for engine in ENGINE_LABELS
                    if (model_id, engine, task_id) in by_attempt
                )
                + '<a href="analysis/#same-task">全部浏览器对照</a></div>',
            )
            (stage / f"datasets/{task_id}.html").write_text(
                _page(title=f"任务 {task_id}", active="datasets", body=detail, depth=1),
                encoding="utf-8",
            )

        benchmarks = [
            [
                "WebArena‑Verified",
                "自托管真实交互",
                "最终 JSON + 必要网络副作用",
                "程序化；无 LLM judge",
                "部署重，最适合内核 A/B",
            ],
            ["Mind2Web", "离线网页快照", "每步目标元素与操作", "程序化动作指标", "评过程模仿"],
            [
                "Online‑Mind2Web",
                "136 个真实网站",
                "动作历史与最终完成",
                "WebJudge + 人工标签",
                "漂移、CAPTCHA、judge 偏差",
            ],
            [
                "WebVoyager",
                "实时网站视觉交互",
                "最终回答与执行证据",
                "多模态 LLM + 人工核验",
                "精确复现较弱",
            ],
            [
                "Mind2Web 2",
                "长链多站搜索",
                "带引用研究答案",
                "rubric Agent-as-a-Judge",
                "非 UI 内核首选",
            ],
            [
                "BU Bench V1",
                "100 个混合任务",
                "最终结果与轨迹",
                "Google judge LLM",
                "云浏览器条件影响可比性",
            ],
            ["Odysseys", "200 个长链多站任务", "rubric 完成度", "模型评审", "成本高"],
        ]
        methodology = _hero(
            "Methodology",
            "评最终结果，不评参考点击路径",
            "唯一成绩来源是未修改的 WebArena‑Verified 1.2.3 evaluator；确定性证据审计只验证记录一致性。",
        ) + _section(
            "候选 benchmark 的验收边界",
            _table(["Benchmark", "评什么", "验收对象", "判分", "问题"], benchmarks),
        )
        methodology += _section(
            "本轮判分链",
            f'<ol class="flow"><li>预注册 {task_count} 题</li><li>逐题重置站点</li><li>CowAgent 自主规划</li><li>只替换浏览器</li><li>官方 evaluator 验收</li><li>确定性证据复核</li></ol><p>官方数据 812 题全部配置 AgentResponseEvaluator，其中 663 题还配置 NetworkEventEvaluator。所有已配置 evaluator 都返回 1.0 才通过。它检查最终结果和必要副作用，不要求复刻参考动作序列。</p>',
        )
        methodology += _section(
            f"这 {task_count} 题如何部署并由 Agent 执行",
            '<figure id="deployment-flow" class="method-figure"><img src="assets/webarena-autonomous-evaluation-flow.png" alt="WebArena-Verified 本地部署、CowAgent 自主操作、双浏览器对照与程序化验收流程" width="1672" height="941"><figcaption>图 1 · 从官方站点镜像、逐题隔离与健康检查，到 CowAgent 观察—决策—操作闭环，再到 Chromium/Moli 对照和官方程序化验收。窄屏可横向滚动查看。</figcaption></figure>'
            f"<p><strong>站点确实部署在本机，不是公网代站。</strong>macOS 上的 Docker 由 Colima 虚拟机承载；运行配置记录的 <code>{_e(site_access_host)}</code> 是浏览器容器访问 macOS 宿主发布端口所用的 Colima 网关，不是评测网站自身地址。Shopping、Shopping Admin、Reddit、GitLab 四个官方站点镜像通过 Docker 端口发布到这个入口，独立的 Chromium/Moli 浏览器容器据此访问本地站点。</p>"
            f"<p>WebArena‑Verified 当前提供 Shopping、Shopping Admin、Reddit、GitLab、Wikipedia、Map 六类环境。本轮 {task_count} 题只覆盖前四类；Wikipedia 与 Map 需要额外大型数据卷，因此没有部署，也没有任务进入成绩。</p>"
            + (
                f"<p>运行器在每个物理执行前调用 <code>ContainerManager.start(reset=True)</code>，只保留该题所需站点；env-ctrl 报告 ready 后，再做连续应用健康检查、清浏览器状态、登录测试账号并打开起始页。本轮 {preparation_receipt_count}/{len(source_attempts)} 个物理执行都保存了环境准备回执，并与对应执行记录绑定。</p>"
                if preparation_receipts_complete
                else "<p>运行器在每个物理执行前调用 <code>ContainerManager.start(reset=True)</code>，只保留该题所需站点；env-ctrl 报告 ready 后，再做连续应用健康检查、清浏览器状态、登录测试账号并打开起始页。当前证据尚未完整保存逐执行环境准备回执，因此不能把每次准备成功当成已独立核验的事实。</p>"
            )
            + f"<p>预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 {max_agent_steps} 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 分别连接独立 CDP 浏览器容器；每次物理执行都保留最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。</p>"
            + _table(
                ["站点", "本轮访问地址", "应用端口", "环境控制端口", "状态"],
                [
                    ["Shopping", f"http://{site_access_host}:7770", "7770", "7771", "本轮已部署"],
                    [
                        "Shopping Admin",
                        f"http://{site_access_host}:7780/admin",
                        "7780",
                        "7781",
                        "本轮已部署",
                    ],
                    ["Reddit", f"http://{site_access_host}:9999", "9999", "9998", "本轮已部署"],
                    ["GitLab", f"http://{site_access_host}:8023", "8023", "8024", "本轮已部署"],
                    ["Wikipedia", "—", "8888", "8889", f"未部署；不在 {task_count} 题"],
                    ["Map", "—", "3030", "3031", f"未部署；不在 {task_count} 题"],
                ],
            )
            + (
                f'<p class="callout success"><strong>环境准备证据：</strong>{preparation_receipt_count}/{len(source_attempts)} 个物理执行均记录 reset、ready、应用健康检查和登录准备状态；事件流随后证明 CowAgent 自主产生 BrowserTool 调用序列。</p>'
                if preparation_receipts_complete
                else '<p class="callout"><strong>证据边界：</strong>事件流能证明 CowAgent 在准备完成后产生了 BrowserTool 调用序列；镜像 ID、digest、代码提交与网关记录限定了本地运行身份。逐执行环境准备回执尚不完整。</p>'
            )
            + '<p class="page-links">'
            + "".join(
                f'<a href="results/{model_id}-{engine}-{example_task_id}.html#trajectory">查看 {_e(MODEL_LABELS[model_id])} · {_e(ENGINE_LABELS[engine])} 自主轨迹示例</a>'
                for model_id in MODEL_LABELS
                for engine in ENGINE_LABELS
                if example_task_id is not None
                and (model_id, engine, example_task_id) in by_attempt
            )
            + '<a href="reproduce/">核对镜像与运行身份</a></p>',
        )
        methodology += _section(
            "Mac 上可以继续扩展哪些实例",
            f"<p>若另行扩充 Browser Use 评测，当前四个已实跑站点可预检 {MAC_COUNTS['current_four_sites_single_site']} 个单站任务；计入只依赖这四站的跨站任务后，共有 {MAC_COUNTS['current_four_sites_including_cross_site']} 个候选，其中 hard 子集 {MAC_COUNTS['current_four_sites_hard_including_cross_site']} 个。轨迹回放使用独立任务合同，不由本报告决定题量。</p>"
            + _table(
                ["环境", "Apple Silicon Mac 状态", "完整集单站题", "hard 单站题", "约束"],
                MAC_ENVIRONMENT_CANDIDATES,
                caption="Wikipedia 与 Map 的容量来自官方环境说明；状态中的“实跑”只指本项目已有执行证据。",
            )
            + f'<p class="callout"><strong>扩展条件：</strong>先对四个已验证站点的 {MAC_COUNTS["current_four_sites_including_cross_site"]} 个候选做可完成性预检和分层抽样；Wikipedia、Map 等数据环境须先完成独立部署资格验证。</p>'
            '<p class="page-links"><a href="https://github.com/ServiceNow/webarena-verified">官方任务集与安装说明</a><a href="https://servicenow.github.io/webarena-verified/dev/environments/">官方环境容量说明</a></p>',
        )
        methodology += _section(
            "固定协议",
            _table(
                ["项目", "规则"],
                [
                    ["Agent / 模型", f"CowAgent；{model}；模型推理强度为低"],
                    ["唯一变量", "同题、同数据、同模型、同提示和资源上限，只替换浏览器"],
                    ["结果口径", "每题×浏览器一个最终逻辑结果；二元通过率；逐题配对"],
                    [
                        "执行上限",
                        "代码最多允许两次执行；本报告按冻结运行协议选取正式结果，实际执行耗时和模型用量累计",
                    ],
                    [
                        "排除",
                        "不纳入无法稳定验收的实时任务；本轮排除需大型数据卷的 Wikipedia / Map",
                    ],
                    ["裁判", "官方程序化 evaluator；无 LLM judge；人工不得改分"],
                ],
            ),
        )
        methodology += _section(
            "两类程序化 evaluator 到底检查什么",
            _table(
                ["Evaluator", "输入", "比较方式", "能证明什么", "不能证明什么"],
                [
                    [
                        "AgentResponseEvaluator",
                        "Agent 最终 JSON",
                        "先按 JSON schema 和数据类型归一化，再比较 task_type、status 与 retrieved_data；按题可声明列表有序或无序。",
                        "最终回答符合该题公开答案合同。",
                        "没有检查回答是通过哪条 UI 路径得到的，也不评操作效率。",
                    ],
                    [
                        "NetworkEventEvaluator",
                        "本次浏览器 HAR",
                        "在网络事件中匹配 URL、方法、状态码以及按需声明的 query、POST、响应字段或 Cookie 条件。",
                        "导航或页面变更产生了指定的可观察副作用。",
                        "没有要求复刻固定点击序列；未声明的页面状态不在验收范围内。",
                    ],
                ],
            )
            + "<p><strong>合取规则：</strong>一题配置的每个 evaluator 都必须得分 1.0，整题才通过。这里没有 LLM-as-a-judge，也没有人工改分。</p>",
        )
        (stage / "methodology/index.html").write_text(
            _page(title="评测方法", active="methodology", body=methodology, depth=1),
            encoding="utf-8",
        )

        analysis = _hero(
            "Results",
            "结果分析",
            "四种配置先在同一张表和同一任务矩阵中比较；失败结果可直接打开诊断，查看发生位置、程序报错、复核根因、轨迹、画面和日志。",
        )
        analysis += _section(
            f"完整 {len(tasks)} 题结果入口",
            '<p>四种配置的通过率、耗时、Tokens 与浏览器资源总表已放在首页，避免在分析页重复。这里直接进入同题矩阵和逐结果失败诊断。</p>'
            '<p class="page-links"><a href="./#home-core-metrics">查看首页核心指标</a></p>',
        )
        analysis += _section(
            f"同题全配置对比：完整 {len(tasks)} 题",
            "<p>每行是一道预注册任务，四列分别对应模型×浏览器配置；可筛出结果有差异、全部通过或全部失败的任务。</p>"
            + _same_task_comparison(tasks, attempts),
        ).replace('<section class="section">', '<section class="section" id="same-task">', 1)
        analysis += _section(
            "BrowserTool 动作延迟",
            "<p>每个动作把全部模型×浏览器配置放在一起比较单次调用墙钟均值，只标记全局唯一最低值。总耗时和调用数受 Agent 行为量影响，仅用于解释工作量，不参与快慢判定。</p>"
            + _action_duration_cards(attempts),
        )
        analysis += _section(
            f"{result_count} 个结果与失败诊断",
            f"<p>这里保留预注册 {task_count} 题的全部官方结果；其中 {len(excluded_task_ids)} 题已标记外部干扰。默认显示失败；点击“查看诊断”可核对发生位置、程序报错、复核根因、轨迹、画面和日志。</p>"
            + _attempt_table(attempts, tasks, verified_causes),
        )
        (stage / "analysis/index.html").write_text(
            _page(title="结果分析", active="analysis", body=analysis, depth=1), encoding="utf-8"
        )

        review_by_key = {
            (x["model"], x["engine"], int(x["task_id"]), int(x["attempt"])): x
            for x in review.get("attempts") or []
        }
        public_evidence_results: list[dict[str, Any]] = []
        for row, public in zip(attempts, public_attempts, strict=True):
            item = review_by_key.get(
                (public["model"], public["engine"], public["task_id"], int(row["attempt"])),
                {},
            )
            task = next(t for t in tasks if int(t["task_id"]) == public["task_id"])
            source_executions = row.get("_source_executions") or [row]
            execution_records = []
            response_record = evaluation_record = None
            steps: list[dict[str, Any]] = []
            for source_execution in source_executions:
                is_authoritative = int(source_execution["attempt"]) == int(row["attempt"])
                execution_record, response, evaluation, execution_steps = (
                    _write_execution_artifacts(
                        stage,
                        public["id"],
                        source_execution,
                        authoritative=is_authoritative,
                    )
                )
                execution_records.append(execution_record)
                if is_authoritative:
                    response_record, evaluation_record, steps = (
                        response,
                        evaluation,
                        execution_steps,
                    )
            metadata = evidence_metadata(row)
            public_evidence_results.append(
                {
                    "result_id": public["id"],
                    "model": public["model"],
                    "engine": public["engine"],
                    "task_id": public["task_id"],
                    "score": public["score"],
                    "execution_count": len(execution_records),
                    "authoritative_execution": int(row["attempt"]),
                    "task_page": f"datasets/{public['task_id']}.html",
                    "result_page": f"results/{public['id']}.html",
                    "executions": execution_records,
                }
            )
            usage, resource, checks = (
                public["model_usage"],
                public["resource"],
                item.get("checks") or {},
            )
            cause_code = _cause_code(verified_causes, public["id"])
            failure_point, failure_step = _failure_point(cause_code, steps)
            step_context_label, step_context = _trajectory_context(
                steps, failure_step, failure_point
            )
            remediation_owner, remediation_action, external_interference = _remediation(cause_code)
            detail = _hero(
                public["id"],
                f"{MODEL_LABELS[public['model']]} · {ENGINE_LABELS[public['engine']]} · Task #{public['task_id']}",
                (
                    f"失败发生在{failure_point}；下方保留程序报错、复核根因和对应执行证据。"
                    if float(public["score"]) < 1
                    else "该结果通过全部官方程序化验收；下方可核对任务合同、执行轨迹和运行证据。"
                ),
            )
            # Pages declare <base href="../"> so every internal URL is rooted at
            # the report bundle, including same-page fragment links.
            attempt_page = f"results/{public['id']}.html"
            detail += (
                '<nav class="page-links wrap" aria-label="结果详情导航">'
                + (
                    f'<a href="{attempt_page}#diagnosis">失败诊断</a>'
                    if float(public["score"]) < 1
                    else ""
                )
                + f'<a href="{attempt_page}#verification">任务与验收</a>'
                + f'<a href="{attempt_page}#trajectory">执行轨迹</a>'
                + (
                    f'<a href="{attempt_page}#screenshots">页面画面</a>'
                    if float(public["score"]) < 1
                    else ""
                )
                + f'<a href="{attempt_page}#runtime">运行与证据</a>'
                + "</nav>"
            )
            runtime_facts = (
                '<div class="attempt-secondary runtime-strip">'
                f'<div class="attempt-fact"><b>任务重试次数</b>{len(execution_records) - 1}</div>'
                f'<div class="attempt-fact"><b>自主操作</b>{public["action_count"]} 次</div>'
                f'<div class="attempt-fact"><b>Agent 链路</b>{_seconds(public["duration_ms"])}</div>'
                f'<div class="attempt-fact"><b>BrowserTool</b>{_seconds(public["browser_tool_duration_ms"])}</div>'
                f'<div class="attempt-fact"><b>模型 tokens</b>{_number(usage["total_tokens"])}</div>'
                f'<div class="attempt-fact"><b>模型请求</b>{usage["request_count"]} 次 / {usage["request_attempt_count"]} 次传输</div></div>'
            )
            if float(public["score"]) < 1:
                step_href = (
                    f"{attempt_page}#trajectory-step-{failure_step}"
                    if failure_step
                    else f"{attempt_page}#trajectory"
                )
                detail += '<div id="diagnosis"></div>' + _section(
                    "失败诊断",
                    '<article class="failure-lead"><span class="status-line">失败</span>'
                    f'<h3><a href="{step_href}">{_e(failure_point)}</a></h3>'
                    f"<p>{_e(_failure_brief(evaluation_record, row))}</p></article>"
                    + (
                        f'<p class="step-signature"><strong>{_e(step_context_label)}：</strong><a href="{step_href}">{_e(step_context)}</a></p>'
                        if step_context
                        else ""
                    )
                    + '<div class="diagnosis-grid">'
                    f'<div class="attempt-fact"><b>程序判定</b>{_e(public["automatic_failure_type"] or "无")}</div>'
                    f'<div class="attempt-fact"><b>复核根因</b>{_e(public["verified_failure_subcategory"] or "待复核")}</div>'
                    f'<div class="attempt-fact"><b>负责范围</b>{_e(remediation_owner)}</div>'
                    f'<div class="attempt-fact"><b>处理</b>{_e(remediation_action)}</div></div>'
                    '<div class="failure-error"><h3>程序报错</h3>'
                    f'<pre class="log-view">{_e(_evaluator_error_text(evaluation_record, row))}</pre></div>'
                    '<div class="diagnosis-reason"><strong>复核结论：</strong>'
                    + _e(public["verified_failure_reason"] or "当前尚无已核验根因。")
                    + '</div><p class="page-links">'
                    f'<a href="{attempt_page}#verification">查看期望与实际值</a>'
                    f'<a href="{step_href}">查看失败附近轨迹</a>'
                    f'<a href="{attempt_page}#screenshots">查看页面画面</a>'
                    f'<a href="{attempt_page}#runtime">查看完整日志</a></p>'
                    + runtime_facts
                    + (
                        '<p class="callout"><strong>对比口径：</strong>该结果已确认含非模型/浏览器干扰，官方分数保留，但暂不纳入干净子集。</p>'
                        if external_interference
                        else ""
                    ),
                )
            else:
                detail += _section(
                    "结果",
                    '<article class="failure-lead success-lead"><span class="status-line">通过</span>'
                    f"<h3>{_e(_failure_brief(evaluation_record, row))}</h3></article>"
                    + runtime_facts,
                )
            detail += '<div id="verification"></div>' + _section(
                "任务要求与验收结果",
                _verification_html(task, evaluation_record, response_record)
                + '<p class="fine">所有规则均为官方程序化验收，必须全部得分 1.0。页面先给出可读结论；官方期望、实际观察和断言保留在折叠技术明细及下载文件中。</p>',
            )
            detail += '<div id="trajectory"></div>' + _section(
                "CowAgent 自主执行轨迹",
                "<p>以下顺序来自本次执行的 Agent 事件流，不是固定动作回放。展开可查看调用参数和输出摘录；完整脱敏日志可下载。</p>"
                + _trajectory_html(steps),
            )
            if float(public["score"]) < 1:
                detail += '<div id="screenshots"></div>' + _section(
                    "失败执行的最后页面画面",
                    _screenshot_gallery(public["id"], execution_records),
                )
            detail += '<div id="runtime"></div>' + _section(
                "运行、日志与证据",
                _execution_evidence_html(public["id"], execution_records)
                + '<p class="fine">公开包包含脱敏的回答、evaluator、事件流与轨迹。原始 HAR 和 Playwright trace 可能携带登录态、Cookie、页面快照或请求正文，因此不公开；其字节数和 SHA-256 保留用于证明证据绑定。</p>'
                + _table(["浏览器资源指标", "值"], _resource_detail_rows(resource))
                + '<p class="fine">整次 Agent 运行的容器计量只作诊断；不同进程模型下不直接比较内存或 PID。</p>'
                + "<h3>证据完整性检查</h3>"
                + _table(
                    ["检查", "结果"],
                    _evidence_check_rows(checks),
                )
                + "<h3>判分权威执行的原始证据绑定</h3>"
                + _table(
                    ["证据", "存在", "字节", "SHA-256", "公开策略"],
                    [
                        [
                            label,
                            "是" if value.get("present") else "否",
                            value.get("bytes"),
                            value.get("sha256") or "—",
                            _publication_label(value.get("publication")),
                        ]
                        for label, value in (metadata.get("files") or {}).items()
                    ],
                )
                + f'<p class="page-links"><a href="datasets/{public["task_id"]}.html">返回任务页</a></p>',
            )
            (stage / f"results/{public['id']}.html").write_text(
                _page(title=public["id"], active="analysis", body=detail, depth=1), encoding="utf-8"
            )

        review_page = _hero(
            "Review history",
            "核验与修订记录",
            "按轮次记录核验范围、发现、正式处理和遗留事项；单个任务的判定证据位于结果详情页。",
        )
        review_page += _section(
            "当前状态",
            '<p class="callout success"><strong>发布门禁：</strong>证据文件、哈希、分数、状态、官方版本与报告链接已通过程序化一致性检查。</p>'
            "<p>官方 WebArena‑Verified evaluator 是成绩来源；核验记录只说明报告和实现经过了哪些检查与修订，不替代任务判分。</p>",
        )
        review_page += _section(
            "核验轮次",
            _review_history(release_data.get("browser_use_reviews") or []),
        )
        (stage / "review/index.html").write_text(
            _page(title="核验记录", active="review", body=review_page, depth=1), encoding="utf-8"
        )

        participant_versions = environment.get("browser_versions") or {}
        participant_images = environment.get("browser_images") or {}
        adoption = _hero(
            "Participants",
            "被测浏览器与固定实验条件",
            "本报告比较浏览器。每个模型单独统计，CowAgent、任务、资源上限、网站和 evaluator 均保持一致。",
        )
        adoption += _section(
            "浏览器与版本",
            _table(
                ["模型", "浏览器", "运行版本", "镜像 ID", "接入方式", "通过任务"],
                [
                    [
                        MODEL_LABELS[model_id],
                        Safe(f'<a href="engines/{engine}.html">{_e(label)}</a>'),
                        (participant_versions.get(engine) or {}).get("Browser", "—"),
                        (participant_images.get(engine) or {}).get("id", "—"),
                        "独立容器 · CDP · CowAgent BrowserTool",
                        f"{summary_by_model[model_id][engine]['passed']}/{summary_by_model[model_id][engine]['completed']}",
                    ]
                    for model_id in MODEL_LABELS
                    for engine, label in ENGINE_LABELS.items()
                ],
            )
            + "<p>每个模型分别比较各浏览器，不把不同模型的结果合并排名。</p>",
        )
        adoption += _section(
            "共同实验条件",
            _table(
                ["栈字段", "本轮固定值", "角色"],
                [
                    [
                        "Agent",
                        f"CowAgent · commit {protocol.get('cowagent_commit', '—')}",
                        "自主观察、规划并调用 BrowserTool",
                    ],
                    [
                        "模型",
                        ", ".join(MODEL_LABELS.values())
                        + f" · reasoning={protocol.get('reasoning_effort', '—')}",
                        "作为分层变量为各浏览器生成决策",
                    ],
                    [
                        "任务与站点",
                        f"同一 {task_count} 题；同一批本地站点镜像",
                        "提供一致输入与网页状态",
                    ],
                    ["动作接口", "CowAgent BrowserTool · CDP", "把 Agent 动作交给浏览器"],
                    [
                        "验收",
                        "WebArena‑Verified 1.2.3 官方 evaluator",
                        "对最终 JSON 与必要网络副作用判分",
                    ],
                    [
                        "资源与上限",
                        f"相同 CPU、内存、viewport 与最多 {max_agent_steps} 步",
                        "控制运行预算",
                    ],
                ],
            )
            + f"<p><strong>这 {task_count} 题由 CowAgent 自主执行。</strong>仓库中的 Browser Trajectory Replay（轨迹重放）是另一项固定动作测试，不计入这里的成绩。</p>",
        )
        adoption += _section(
            "增加新的浏览器",
            "<p>新增浏览器时，需要注册其容器镜像、版本与 CDP 端点，并在同一冻结任务集和共同条件下形成完整结果。总榜按浏览器逐行扩展；同题矩阵按浏览器增加列；统计比较按浏览器对单独生成，不能把多个浏览器压成一个总胜负。</p>",
        )
        (stage / "adoption/index.html").write_text(
            _page(title="被测浏览器", active="adoption", body=adoption, depth=1), encoding="utf-8"
        )

        versions, images = (
            environment.get("browser_versions") or {},
            environment.get("browser_images") or {},
        )
        engine_rows = []
        for engine in ENGINE_LABELS:
            version, image = versions.get(engine) or {}, images.get(engine) or {}
            engine_rows.append(
                [
                    Safe(f'<a href="engines/{engine}.html">{ENGINE_LABELS[engine]}</a>'),
                    version.get("Browser", "—"),
                    version.get("Protocol-Version", "—"),
                    image.get("architecture", "—"),
                    str(image.get("id") or "—"),
                ]
            )
            body = _hero(
                "Engine", ENGINE_LABELS[engine], "本轮浏览器运行身份、完整任务结果与失败证据入口。"
            ) + _section(
                "运行身份",
                _table(
                    ["字段", "值"],
                    [
                        ["Browser", version.get("Browser", "—")],
                        ["DevTools Protocol", version.get("Protocol-Version", "—")],
                        ["架构", image.get("architecture", "—")],
                        ["镜像 ID", image.get("id", "—")],
                        [
                            "各模型任务成绩",
                            "；".join(
                                f"{MODEL_LABELS[model_id]} {summary_by_model[model_id][engine]['passed']}/{summary_by_model[model_id][engine]['completed']}"
                                for model_id in MODEL_LABELS
                            ),
                        ],
                        [
                            "各模型逻辑结果耗时 P50",
                            "；".join(
                                f"{MODEL_LABELS[model_id]} {_seconds(summary_by_model[model_id][engine]['p50_duration_ms'])}"
                                for model_id in MODEL_LABELS
                            ),
                        ],
                    ],
                )
                + '<p class="fine">版本与镜像身份用于限定复现边界，不参与评分。</p>',
            )
            body += _section(
                "查看该浏览器的全部结果",
                "<p>完整结果只保留一份。下方入口打开结果分析页，并预先筛选当前浏览器；失败诊断、轨迹、页面画面与日志均从该清单进入。</p>"
                f'<div class="report-entry-links"><a class="button-link" href="analysis/index.html?f_engine={engine}#result-table">打开筛选后的结果清单 →</a></div>',
            )
            (stage / f"engines/{engine}.html").write_text(
                _page(title=ENGINE_LABELS[engine], active="adoption", body=body, depth=1),
                encoding="utf-8",
            )

        reproduce = _hero(
            "Reproduction",
            "复现从公开仓库、预注册题集和固定运行身份开始",
            "公开包提供脱敏投影；模型回答、Cookie 和完整网络正文不进入 Git 或站点。",
        )
        reproduce += _section(
            "浏览器与代码身份",
            _table(["浏览器", "版本", "协议", "架构", "镜像 ID"], engine_rows)
            + _table(
                ["项目", "值"],
                [
                    ["browser-eval commit", protocol.get("browser_eval_commit", "—")],
                    ["CowAgent commit", protocol.get("cowagent_commit", "—")],
                    ["模型", f"{model} · reasoning={protocol.get('reasoning_effort', '—')}"],
                    ["WebArena‑Verified", environment.get("webarena_verified_version", "—")],
                    ["运行配置选择集 SHA-256", config.get("selection_sha256", selection.sha256)],
                    ["当前选择集 SHA-256", selection.sha256],
                    [
                        "evaluator checksum",
                        (environment.get("official_evaluator") or {}).get(
                            "evaluator_checksum", "—"
                        ),
                    ],
                ],
            ),
        )
        site_images = environment.get("site_images") or {}
        reproduce += _section(
            "部署环境与网络拓扑",
            "<p>本轮 Docker daemon 运行在 macOS 上的 Colima 虚拟机中。站点容器把应用端口发布到 Docker 宿主；两个浏览器容器通过其容器内 <code>host.docker.internal</code> 对应的网关地址访问这些端口。运行时记录的地址是 <code>"
            + _e(environment.get("site_access_host") or "未记录")
            + "</code>。它属于本轮私有网络，不应复制为其他机器的固定地址。</p>"
            + _table(
                ["层", "本轮身份", "作用"],
                [
                    ["宿主", environment.get("platform", "—"), "运行 Colima 与报告 runner"],
                    [
                        "Docker",
                        f"{environment.get('docker_name', '—')} · {environment.get('docker_architecture', '—')} · {environment.get('docker_server_version', '—')}",
                        "承载站点和浏览器容器",
                    ],
                    [
                        "浏览器到宿主网关",
                        environment.get("site_access_host", "—"),
                        "访问站点容器发布端口",
                    ],
                    [
                        "ARM 兼容模式",
                        (environment.get("site_runtime_compatibility") or {}).get("mode", "未启用"),
                        "运行部分 amd64 PHP 站点镜像",
                    ],
                ],
            )
            + _table(
                ["站点镜像", "镜像引用", "镜像 ID", "Repo digest"],
                [
                    [
                        SITE_LABELS.get(site, site),
                        image.get("reference", "—"),
                        image.get("id", "—"),
                        ", ".join(image.get("repo_digests") or []) or "—",
                    ]
                    for site, image in site_images.items()
                ],
            )
            + (
                f'<p class="fine">本轮 {preparation_receipt_count}/{len(source_attempts)} 个物理执行均保存环境准备回执；公开环境摘要保留逐项状态，原始登录态不发布。</p>'
                if preparation_receipts_complete
                else '<p class="fine">逐执行环境准备回执尚不完整；现有记录只能核对运行器配置与镜像身份。</p>'
            ),
        )
        reproduce += _section(
            "环境隔离与并行边界",
            "<p>修正后的运行器把一个 Docker daemon 及其固定站点端口视为一个环境 lane。同一 lane 内的任务和浏览器必须串行：每次尝试先重置站点，再完成 Agent、证据收尾和 evaluator，整个周期由跨进程锁保护。缺少 Docker daemon 身份时直接停止，不降级为无锁执行。</p>"
            + _table(
                ["运行方式", "是否允许", "隔离要求"],
                [
                    [
                        "一套站点环境，Chromium 与 Moli 串行",
                        "允许，默认方案",
                        "同一 daemon 锁；每次尝试前重置并核对初态",
                    ],
                    [
                        "一套站点环境，两个浏览器并行",
                        "不允许",
                        "账号不同仍会共享对象、索引、缓存和后台任务",
                    ],
                    [
                        "多个任务并行",
                        "允许",
                        "每个 worker 使用独立 daemon、站点容器、网络、端口和可写卷；镜像只读层可复用",
                    ],
                ],
            )
            + f'<p class="fine">账号隔离只用于避免用户级对象冲突，不能替代环境 lane。本轮有 {len(excluded_task_ids)} 题经复核确认受外部干扰；这些任务只在事后诊断子集中按整题排除，官方分数仍完整保留。</p>',
        )
        reproduce += _section(
            "公开复现入口",
            f'<p><a href="https://github.com/lexmount/browser-eval">lexmount/browser-eval</a> 是代码、题集和锁文件的唯一来源。</p><pre><code>./examples/browser_use/run.sh\nmake browser-use-review EVIDENCE="$BROWSER_USE_EVIDENCE"\nmake browser-use-report EVIDENCE="$BROWSER_USE_EVIDENCE"\nmake browser-use-report-check EVIDENCE="$BROWSER_USE_EVIDENCE"</code></pre><div class="page-links"><a href="data/selection.json">selection.json</a><a href="data/results.jsonl">results.jsonl</a><a href="data/evidence-index.json">evidence-index.json</a><a href="data/failure-cause-review.json">failure-cause-review.json</a><a href="data/summary.json">summary.json</a><a href="data/environment.json">environment.json</a><a href="data/review.json">review.json</a><a href="data/publication-privacy.json">publication-privacy.json</a></div><p class="fine">每次物理执行的脱敏回答、evaluator、事件和轨迹位于 <code>artifacts/&lt;result-id&gt;/executions/&lt;n&gt;/</code>；<code>evidence-index.json</code> 给出全部 {result_count} 个逻辑结果及其物理执行、下载路径和公开文件哈希。</p>',
        )
        (stage / "reproduce/index.html").write_text(
            _page(title="环境与复现", active="reproduce", body=reproduce, depth=1), encoding="utf-8"
        )
        accounts = _hero(
            "Accounts",
            "本地站点账号、模型权限与发布权限分开处理",
            "不展示密钥、Cookie 或真实个人信息；官方合同中的内置测试邮箱和用户名作为合成 ground truth 保留。",
        ) + _section(
            "权限与费用",
            _table(
                ["范围", "本轮做法", "外部付费账号"],
                [
                    ["四个自托管站点", "官方 Docker 镜像与内置测试账号；每题前重置", "不需要"],
                    ["模型 API", f"{model}，reasoning=low", "需要相应 API 权限"],
                    ["Artifact Hub", "只发布脱敏静态报告", "发布者需已有站点权限"],
                ],
            ),
        )
        (stage / "accounts/index.html").write_text(
            _page(title="账号", active="accounts", body=accounts, depth=1), encoding="utf-8"
        )

        claims = {
            "schema": "lex_browser_use_interpretation/1",
            "claims": [
                {"claim": "审查合格比较集的模型内浏览器配对结果", "basis": _paired_summary(qualified_attempts)},
                {"claim": "失败根因以当前证据绑定的逐结果核验为准", "basis": verified_causes},
                {
                    "claim": "官方 evaluator 的失败规则组合",
                    "basis": {
                        "denominator": sum(automatic_failure_counts.values()),
                        "authority": "final logical execution",
                        "source": "eval_result.json/evaluators_results where score < 1",
                        "classes": {
                            "network_only": automatic_failure_counts["network"],
                            "response_only": automatic_failure_counts["response"],
                            "both": automatic_failure_counts["mixed"],
                        },
                    },
                },
                {
                    "claim": "已证实外部干扰按整题排除后的正式比较集",
                    "basis": {
                        "excluded_task_ids": sorted(
                            _external_interference_task_ids(verified_causes)
                        ),
                        "summary": _primary_summary(
                            [
                                row
                                for row in attempts
                                if int(row["task_id"])
                                not in _external_interference_task_ids(verified_causes)
                            ]
                        ),
                    },
                },
                {
                    "claim": "全部注册配置最终共同成功任务的性能与资源 P50（仅供诊断）",
                    "basis": dict(
                        zip(
                            ("task_count", "rows"),
                            _common_final_success_resource_rows(qualified_attempts),
                            strict=True,
                        )
                    ),
                },
                {"claim": "执行证据完整性审查覆盖", "basis": review.get("coverage") or {}},
            ],
            "bound_attempts_sha256": attempts_sha256,
            "selection_sha256": selection.sha256,
            "score_authority": {
                bundle["model"]: (
                    (bundle["manifest"].get("protocol") or {}).get("official_score_uses")
                    or "final execution for each browser and task"
                )
                for bundle in bundles
            },
        }
        _write_json(stage / "data/interpretation-evidence.json", claims)
        _write_json(
            stage / "data/selection.json",
            {
                "schema": "lex_browser_use_public_selection/1",
                "selection_sha256": selection.sha256,
                "tasks": tasks,
            },
        )
        (stage / "data/results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in public_attempts),
            encoding="utf-8",
        )
        _write_json(
            stage / "data/failure-cause-review.json",
            {
                "schema": LAYERED_REVIEW_SCHEMA,
                "bindings": {
                    model_id: review_value.get("bindings") or {}
                    for model_id, review_value in failure_reviews.items()
                },
                "coverage": {
                    "failed_results": len(failed_result_ids),
                    "verified_results": len(verified_causes),
                },
                "unresolved": sorted(failed_result_ids - set(verified_causes)),
                "results": verified_causes,
            },
        )
        _write_json(
            stage / "data/evidence-index.json",
            {
                "schema": "lex_browser_use_public_evidence_index/4",
                "results": public_evidence_results,
            },
        )
        audit = {
            "schema": "lex_browser_use_report/3",
            "result_count": len(attempts),
            "score_authority": {
                bundle["model"]: (
                    (bundle["manifest"].get("protocol") or {}).get("official_score_uses")
                    or "final execution for each browser and task"
                )
                for bundle in bundles
            },
            "summary": summary_by_model,
            "paired_summary": paired_by_model,
            "qualified_comparison": {
                "task_count": len(qualified_tasks),
                "excluded_task_ids": sorted(excluded_task_ids),
                "summary": qualified_summary,
                "paired_summary": _paired_summary(qualified_attempts),
            },
            "selection_sha256": selection.sha256,
            "evidence_bindings": {
                "attempts_sha256": attempts_sha256,
                "manifest_sha256": {
                    bundle["model"]: bundle["manifest_sha256"] for bundle in bundles
                },
            },
            "failure_cause_review": {
                "status": "complete" if set(verified_causes) == failed_result_ids else "incomplete",
                "reviewed_failures": len(verified_causes),
                "failed_results": len(failed_result_ids),
                "bindings": {
                    model_id: review_value.get("bindings") or {}
                    for model_id, review_value in failure_reviews.items()
                },
                "source_sha256": file_sha256(FAILURE_CAUSE_REVIEW_PATH),
            },
            "review": {
                "coverage": review.get("coverage"),
                "disagreement_count": len(review.get("disagreements") or []),
                "unresolved_count": len(review.get("unresolved") or []),
                "bindings": review.get("bindings"),
            }
            if review
            else None,
        }
        _write_json(stage / "data/summary.json", audit)
        _write_json(stage / "report-data.json", audit)
        public_env = {
            "schema": "lex_browser_use_public_environment/2",
            "protocol": {
                key: protocol.get(key)
                for key in (
                    "agent",
                    "browser_eval_commit",
                    "cowagent_commit",
                    "reasoning_effort",
                    "evaluator",
                    "engine_order",
                    "sites",
                )
            }
            | {
                "models": list(MODEL_LABELS),
                "model_labels": MODEL_LABELS,
                "baseline_engine": BASELINE_ENGINE,
                "max_task_executions": 2,
                "environment_reset": (
                    "official site containers reset before every execution; "
                    f"{preparation_receipt_count}/{len(source_attempts)} execution-scoped "
                    "preparation receipts verified"
                    if preparation_receipts_complete
                    else "runner configured for reset before every execution; independent "
                    f"preparation receipts available for {preparation_receipt_count}/"
                    f"{len(source_attempts)} executions"
                ),
                "score_uses": "frozen run manifest; report does not override measured score",
                "cost_and_latency": "sum of all executions used for the logical result",
            },
            "cohorts": {
                bundle["model"]: {
                    "protocol": {
                        key: (bundle["manifest"].get("protocol") or {}).get(key)
                        for key in (
                            "agent",
                            "browser_eval_commit",
                            "cowagent_commit",
                            "model",
                            "reasoning_effort",
                            "max_agent_steps",
                            "evaluator",
                        )
                    },
                    "run_id": bundle["manifest"].get("run_id"),
                    "created_at": bundle["manifest"].get("created_at"),
                    "attempts_sha256": bundle["attempts_sha256"],
                    "manifest_sha256": bundle["manifest_sha256"],
                }
                for bundle in bundles
            },
            "environment": {
                "platform": environment.get("platform"),
                "docker_name": environment.get("docker_name"),
                "docker_server_version": environment.get("docker_server_version"),
                "docker_architecture": environment.get("docker_architecture"),
                "site_access_host": environment.get("site_access_host"),
                "site_runtime_compatibility": environment.get("site_runtime_compatibility"),
                "webarena_verified_version": environment.get("webarena_verified_version"),
                "browser_images": images,
                "browser_versions": versions,
                "site_images": environment.get("site_images"),
                "official_evaluator": environment.get("official_evaluator"),
            },
        }
        _write_json(stage / "data/environment.json", public_env)
        _write_json(
            stage / "data/review.json",
            {
                key: review.get(key)
                for key in (
                    "schema",
                    "reviewer",
                    "reviewed_at",
                    "bindings",
                    "protocol",
                    "coverage",
                    "disagreements",
                    "unresolved",
                    "attempts",
                )
            },
        )
        _write_json(
            stage / "data/publication-privacy.json",
            {
                "schema": "lex_browser_use_publication_privacy/1",
                "published": [
                    "official task intent and evaluator contracts",
                    "sanitized response and evaluator evidence for every physical execution",
                    "sanitized CowAgent event stream and BrowserTool trajectory for every physical execution",
                    "per-execution measurements plus source and published-artifact SHA-256 bindings",
                ],
                "private_only": [
                    "raw HAR because it may contain cookies, headers and request bodies",
                    "raw Playwright trace because it may contain session state and page snapshots",
                    "model API credentials and local filesystem paths",
                ],
                "redactions": [
                    "known benchmark credentials",
                    "authorization, cookie, password, secret, token and API-key fields",
                    "local service origins replaced with benchmark URL placeholders",
                    "machine-local filesystem paths",
                ],
                "retained_test_fixtures": [
                    "synthetic usernames and email addresses that are part of official task contracts",
                    "public benchmark task identifiers and expected values",
                ],
            },
        )
        write_english_report(stage)
        (stage / ".generated-report").write_text("browser-use-report/2\n", encoding="utf-8")
        backup: Path | None = None
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{output.name}-backup-", dir=parent))
            backup.rmdir()
            output.rename(backup)
        try:
            stage.rename(output)
        except Exception:
            if backup is not None and backup.exists() and not output.exists():
                backup.rename(output)
            raise
        if backup is not None:
            shutil.rmtree(backup)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence",
        required=True,
        action="append",
        type=Path,
        help="isolated evidence directory for one model; repeat once per model",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("evaluation/browser-use/webarena-verified-pilot.json"),
    )
    args = parser.parse_args()
    print(build_report(evidence=args.evidence, output=args.output, selection_path=args.selection))


if __name__ == "__main__":
    main()
