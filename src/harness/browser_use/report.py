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
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from harness.browser_use.failure_cause_review import (
    LAYERED_REVIEW_SCHEMA,
    extract_failure_cause_review_cohort,
    load_failure_cause_review,
    localized_cause,
    validate_failure_cause_review,
)
from harness.browser_use.failure_cause_review import (
    review_path as failure_review_path,
)
from harness.browser_use.publication import (
    evaluator_summary,
    evidence_metadata,
    read_public_json,
    result_excerpt,
    sanitize,
    task_contract,
    trajectory,
    trajectory_label,
)
from harness.browser_use.report_i18n import (
    bilingual_text,
    english_rationale,
    report_text,
    write_english_report,
)
from harness.browser_use.selection import (
    PilotSelection,
    load_pilot,
    report_selection_path,
    validate_against_official,
)
from harness.record_io import load_jsonl, optional_file_sha256, read_json, write_json
from harness.report_languages import (
    add_language_argument,
    includes_english,
    primary_report_language,
    select_report_language,
    write_language_contract,
)
from harness.reporting.assets import bound_json_asset
from harness.reporting.templates import template_identity
from harness.web_fetch.report_style import STYLE as SHARED_STYLE
from lexbench_webfetch.metrics import percentile

_ZH, _english = report_text("zh"), report_text("en")


class _Authored(str):
    """English report text, which keeps its apostrophes literal in page text."""


def _EN(key: str, source: str, /, **values: Any) -> str:
    return _Authored(_english(key, source, **values))


# The edition being rendered. Page functions read it; `_write_english_edition`
# switches it for one pass over the pages.
t = _ZH


def _language() -> str:
    return "en" if t is _EN else "zh"


def _translated(key: str, source: str, /, **values: Any) -> Safe:
    """Keep dynamic text and its English form beside the same values."""
    return Safe(bilingual_text(_ZH(key, source, **values), _EN(key, source, **values)))


def _rationale(task: dict[str, Any]) -> str:
    """Read the frozen selection rationale, which has no English field of its own."""
    raw = task["rationale"]
    english = english_rationale(raw) if _language() == "en" else raw
    return _Authored(english) if english != raw else _raw(raw)


def _raw(value: Any) -> Safe:
    """Mark measured or dataset text that keeps its source language."""
    return Safe(f"<span data-raw-text>{_e(value)}</span>")


def _searchable_rationale(task: dict[str, Any]) -> str:
    """The search index of the English edition has always rewritten this one word."""
    rationale = str(task.get("rationale"))
    return rationale.replace(_ZH("search.rule_word", "Rule"), "Rule") if _language() == "en" else rationale


def _tier_label(tier: str) -> str:
    return {"easy": t("tier_label.easy", "Easy"), "medium": t("tier_label.medium", "Medium"), "hard": t("tier_label.hard", "Hard")}[tier]


def _recorded(value: str) -> str:
    """Show a value that was missing at load time in the edition being rendered."""
    return t("common.not_recorded", "Not recorded") if value == _ZH("common.not_recorded", "Not recorded") else value


def _record_text(record: dict[str, Any], field: str) -> Any:
    """Read one release or review field in the edition being rendered."""
    english = ((record.get("i18n") or {}).get("en") or {}).get(field)
    return english if english and _language() == "en" else record.get(field)


SITE_LABELS = {
    "shopping": "Shopping",
    "shopping_admin": "Shopping Admin",
    "reddit": "Reddit",
    "gitlab": "GitLab",
}


@dataclass(frozen=True)
class Participants:
    """The browsers and models one report compares, read from its profile."""

    engines: dict[str, str]
    models: dict[str, str]
    baseline_engine: str

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> Participants:
        engines = [str(engine) for engine in profile.get("engines") or []]
        engine_labels = profile.get("engine_labels") or {}
        if not engines or len(engines) != len(set(engines)):
            raise ValueError("current Browser Use profile must register unique browser engines")
        if set(engine_labels) - set(engines):
            raise ValueError("Browser Use engine labels contain an unregistered browser")
        models = [str(model) for model in profile.get("models") or []]
        model_labels = profile.get("model_labels") or {}
        baseline = str(profile.get("baseline_engine") or "")
        if not models or len(models) != len(set(models)):
            raise ValueError("current Browser Use profile must register unique models")
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", model) for model in models):
            raise ValueError("Browser Use model IDs must be safe path components")
        if set(model_labels) - set(models):
            raise ValueError("Browser Use model labels contain an unregistered model")
        if baseline not in engines:
            raise ValueError("Browser Use baseline_engine must be a registered browser engine")
        return cls(
            engines={
                engine: str(engine_labels.get(engine) or _engine_name(engine))
                for engine in engines
            },
            models={model: str(model_labels.get(model) or model) for model in models},
            baseline_engine=baseline,
        )

    @property
    def engines_baseline_first(self) -> list[str]:
        others = (engine for engine in self.engines if engine != self.baseline_engine)
        return [self.baseline_engine, *others]

    @property
    def engine_names(self) -> str:
        """Join registered browser names, without the versions a label may carry."""

        names = [_engine_name(engine) for engine in self.engines]
        return (" " + t("participants.and", "and") + " ").join(filter(None, (t("list.separator", ", ").join(names[:-1]), names[-1])))


def _engine_name(engine: str) -> str:
    return engine.replace("_", " ").title()


METHODOLOGY_FIGURE = Path(__file__).parent / "assets" / "webarena-autonomous-evaluation-flow.png"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=not isinstance(value, _Authored))


class Safe(str):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.is_file() else {}


def _read_attempts(evidence: Path) -> list[dict[str, Any]]:
    path = evidence / "attempts.jsonl"
    return (
        load_jsonl(path, skip_blank=True)
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
    participants: Participants,
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
        if not model and len(paths) == 1 and len(participants.models) == 1:
            model = next(iter(participants.models))
        if not model:
            raise ValueError(f"evidence manifest does not identify its model: {path}")
        if model not in participants.models:
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
            if str(source.get("engine") or "") not in participants.engines:
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
                "attempts_sha256": optional_file_sha256(path / "attempts.jsonl"),
                "manifest_sha256": optional_file_sha256(path / "manifest.json"),
            }
        )
    expected_models = set(participants.models)
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
        reference_model = next(iter(participants.models))
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
            expected = {(engine, task_id) for engine in participants.engines for task_id in task_ids}
            if cohort != expected:
                raise ValueError(f"model cohort is missing a registered browser: {model}")
    return bundles


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _seconds(value: float | None) -> str:
    return "—" if value is None else t("seconds.seconds", "{value:.1f} seconds", value=value / 1000)


def _seconds_short(value: float | None) -> str:
    """Seconds inside a longer line; the English edition abbreviates the unit there."""
    return "—" if value is None else t("seconds.short", "{value:.1f} s", value=value / 1000)


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


def _winner(value: str, *, won: bool, label: str | None = None) -> Safe:
    if not won:
        return Safe(_e(value))
    better = t("winner.better", "Better")
    return Safe(
        f'<strong class="comparison-winner" title="{_e(label or better)}">{_e(value)}<span class="winner-mark" aria-label="{better}">' + t("winner.best", "Best") + "</span></strong>"
    )


def _metric_stack(items: list[tuple[str, str, bool]]) -> Safe:
    lines = []
    for label, value, won in items:
        rendered = _winner(value, won=won)
        lines.append(f"<span><b>{_e(label)}</b>{rendered}</span>")
    return Safe('<span class="metric-stack">' + "".join(lines) + "</span>")


def _table(
    headers: list[str],
    rows: Iterable[Iterable[Any]],
    *,
    caption: str = "",
    table_id: str = "",
    classes: str = "",
) -> str:
    head = "".join(f"<th>{x if isinstance(x, Safe) else _e(x)}</th>" for x in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in row) + "</tr>"
        for row in rows
    )
    cap = f"<caption>{_e(caption)}</caption>" if caption else ""
    attrs = (f' id="{_e(table_id)}"' if table_id else "") + (
        f' class="{_e(classes)}"' if classes else ""
    )
    return f'<div class="table-wrap"><table{attrs}>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _attempt_id(row: dict[str, Any]) -> str:
    return f"{_row_model(row)}-{row['engine']}-{int(row['task_id'])}"


def _row_model(row: dict[str, Any]) -> str:
    model = str(row.get("model") or "")
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
        grouped[(_row_model(row), str(row["engine"]), int(row["task_id"]))].append(row)
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
        # A metric any execution lacks is unavailable: an aggregate never fills it with 0.
        measured = {
            name: all(resource.get(name) is not None for resource in resources)
            for name in (
                "cpu_ms",
                "cpu_average_utilization_pct",
                "memory_mean_bytes",
                "memory_peak_bytes",
                "memory_retained_bytes",
                "network_rx_bytes",
                "network_tx_bytes",
                "pids_current_peak",
            )
        }
        authoritative["resource"] = {
            **{
                name: sum(float(resource[name]) for resource in resources)
                if measured[name]
                else None
                for name in ("cpu_ms", "network_rx_bytes", "network_tx_bytes")
            },
            **{
                name: max(float(resource[name]) for resource in resources)
                if measured[name]
                else None
                for name in ("memory_peak_bytes", "memory_retained_bytes", "pids_current_peak")
            },
        }
        weighted_duration = sum(float(row.get("duration_ms") or 0) for row in executions)
        if weighted_duration and measured["cpu_average_utilization_pct"]:
            authoritative["resource"]["cpu_average_utilization_pct"] = (
                sum(
                    float(resource["cpu_average_utilization_pct"])
                    * float(row.get("duration_ms") or 0)
                    for row, resource in zip(executions, resources, strict=True)
                )
                / weighted_duration
            )
        memory_sample_count = sum(int(resource.get("sample_count") or 0) for resource in resources)
        if memory_sample_count and measured["memory_mean_bytes"]:
            authoritative["resource"]["memory_mean_bytes"] = (
                sum(
                    float(resource["memory_mean_bytes"]) * int(resource.get("sample_count") or 0)
                    for resource in resources
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


def _primary_summary(
    rows: list[dict[str, Any]], participants: Participants
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for model in participants.models:
        result[model] = {}
        for engine in participants.engines:
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
                # A partial population is unavailable, not a smaller sample of the same scope.
                complete = bool(values) and len(values) == len(logical)
                resource_summary[name] = {
                    "count": len(values),
                    "mean": statistics.fmean(values) if complete else None,
                    "p50": percentile(values, 0.50) if complete else None,
                    "p95": percentile(values, 0.95) if complete else None,
                }
            result[model][engine] = {
                "completed": len(logical),
                "passed": passed,
                "score": passed / len(logical) if logical else None,
                "mean_duration_ms": statistics.fmean(durations) if durations else None,
                "p50_duration_ms": percentile(durations, 0.50),
                "p95_duration_ms": percentile(durations, 0.95),
                "mean_browser_tool_duration_ms": statistics.fmean(tool_durations)
                if tool_durations
                else None,
                "p50_browser_tool_duration_ms": percentile(tool_durations, 0.50),
                "p95_browser_tool_duration_ms": percentile(tool_durations, 0.95),
                "mean_tokens": statistics.fmean(tokens) if tokens else None,
                "p50_tokens": percentile(tokens, 0.50),
                "p95_tokens": percentile(tokens, 0.95),
                "resources": resource_summary,
            }
    return result


def _paired_summary(rows: list[dict[str, Any]], participants: Participants) -> dict[str, Any]:
    """Compare every browser with the profile baseline, always within one model."""

    primary = {
        (str(r["model"]), str(r["engine"]), int(r["task_id"])): float(r["score"]) == 1 for r in rows
    }
    result: dict[str, Any] = {}
    for model in participants.models:
        comparisons: dict[str, Any] = {}
        for engine in participants.engines:
            if engine == participants.baseline_engine:
                continue
            ids = sorted(
                task_id
                for task_id in {key[2] for key in primary if key[0] == model}
                if (model, participants.baseline_engine, task_id) in primary
                and (model, engine, task_id) in primary
            )
            counts = {
                "both_pass": 0,
                "baseline_only": 0,
                "comparison_only": 0,
                "both_fail": 0,
            }
            for task_id in ids:
                baseline_passed = primary[(model, participants.baseline_engine, task_id)]
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
                "baseline_engine": participants.baseline_engine,
                "comparison_engine": engine,
                "paired": len(ids),
                **counts,
                "mcnemar_exact_p": p,
            }
        result[model] = {
            "baseline_engine": participants.baseline_engine,
            "comparisons": comparisons,
        }
    return result


def _resolve_tasks(
    selection: PilotSelection, *, expected_data_checksum: str | None = None,
    dataset_path: Path | None = None
) -> list[dict[str, Any]]:
    try:
        from harness.environments.webarena.runtime import (
            configure_benchmark,
        )

        benchmark = configure_benchmark()
        if dataset_path is not None:
            from webarena_verified import WebArenaVerified
            if dataset_path.is_symlink() or not dataset_path.is_file():
                raise ValueError("Frozen official dataset is missing or symlinked")
            benchmark = WebArenaVerified(config=benchmark.config.model_copy(
                update={"test_data_file": dataset_path}))
        if expected_data_checksum:
            dataset_path = Path(benchmark.config.test_data_file)
            actual_data_checksum = optional_file_sha256(dataset_path)
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
            expected.get("task_type") or "UNKNOWN"
        ).upper()
    return tasks


def _failure_cause_table(verified_causes: dict[str, dict[str, Any]]) -> str:
    groups = {
        "agent": (t("failure_cause_table.agent_model_behavior", "Agent / model behavior"), t("failure_cause_table.retain", "Retain"), t("failure_cause_table.part_of_the_configuration_under_test", "Part of the configuration under test; use the trajectory to locate retrieval, selection, or submission errors.")),
        "benchmark": (
            t("failure_cause_table.task_evaluator", "Task / evaluator"),
            t("failure_cause_table.exclude_and_fix", "Exclude and fix"),
            t("failure_cause_table.correct_the_acceptance_contract_complete_independent", "Correct the acceptance contract, complete independent preflight checks, then rerun every configuration."),
        ),
        "site": (t("failure_cause_table.self_hosted_site", "Self-hosted site"), t("failure_cause_table.exclude_and_fix", "Exclude and fix"), t("failure_cause_table.fix_the_dependency_session_or_save", "Fix the dependency, session, or save path, then rerun after read/write preflight checks.")),
        "evidence": (
            t("failure_cause_table.browser_evidence_interface", "Browser evidence interface"),
            t("failure_cause_table.retain_and_fix", "Retain and fix"),
            t("failure_cause_table.this_is_part_of_browser_integration", "This is part of browser integration capability; complete response-body or Referer capture and rerun."),
        ),
        "environment": (
            t("failure_cause_table.shared_test_environment", "Shared test environment"),
            t("failure_cause_table.exclude_fix_completed", "Exclude; fix completed"),
            t("failure_cause_table.the_cross_process_serialization_lock_is", "The cross-process serialization lock is deployed; contaminated tasks still require reruns."),
        ),
        "mixed": (
            t("failure_cause_table.reference_state_mixed_with_agent_behavior", "Reference state mixed with Agent behavior"),
            t("failure_cause_table.exclude_and_rebuild", "Exclude and rebuild"),
            t("failure_cause_table.refreeze_the_reference_state_then_rerun", "Refreeze the reference state, then rerun to distinguish Agent errors."),
        ),
    }

    def group_for(code: str) -> str:
        for prefix in ("agent", "benchmark", "site", "environment", "mixed"):
            if code.startswith(prefix + "_"):
                return prefix
        if code.startswith("evidence_"):
            return "evidence"
        raise ValueError(f"unknown failure-cause scope: {code}")

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for diagnosis in verified_causes.values():
        subcategory = diagnosis.get("subcategory") or {}
        code = str(subcategory.get("code") or "evidence_capture")
        label = str(subcategory.get("zh") or _ZH("failure_cause_table.unclassified", "Unclassified"))
        counts[group_for(code)][label] += 1

    rows = []
    for key in ("agent", "benchmark", "site", "evidence", "environment", "mixed"):
        total = sum(counts[key].values())
        if not total:
            continue
        rows.append([groups[key][0], total, groups[key][1], groups[key][2]])
    return _table(
        [t("failure_cause_table.cause_scope", "Cause scope"), t("failure_cause_table.failed_results", "Failed results"), t("failure_cause_table.treatment_in_this_run", "Treatment in this run"), t("failure_cause_table.fix_and_verification", "Fix and verification")],
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


def _adopter_names(adopters: list[str]) -> str:
    if _language() == "zh":
        return t("list.separator", ", ").join(adopters)
    if len(adopters) < 2:
        return "".join(adopters)
    last = ", and " if len(adopters) == 3 else " and "
    return ", ".join(adopters[:-1]) + last + adopters[-1]


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
                t("adoption_top3.original_mind2web_online_mind2web_and_mind2web", "Original Mind2Web, Online-Mind2Web and Mind2Web 2 use different tasks and evaluators; their scores cannot be combined in one table.")
            )
        note = warning or {
            "WebArena": t("adoption_top3.self_hosting_resettable_state_and_outcome", "Self-hosting, resettable state, and outcome evaluation make it a stronger fit for browser A/B tests."),
            "WebVoyager": t("adoption_top3.live_websites_are_intuitive_but_page", "Live websites are intuitive, but page drift and LLM judging reduce exact reproducibility."),
        }.get(str(item.get("family")), t("adoption_top3.adoption_counts_do_not_indicate_evaluation", "Adoption counts do not indicate evaluation quality."))
        cards.append(
            '<article class="rank-item"><div><h3>' + t("adoption_top3.family_organizations", "{family} family · {adopters_count} organizations", family=_e(item.get('family')), adopters_count=len(adopters)) + "</h3><p>" + t("adoption_top3.text", "{adopters}. {note}", adopters=_e(_adopter_names(adopters)), note=_e(note)) + "</p></div></article>"
        )
    return (
        '<div class="rank-list">'
        + "".join(cards)
        + ('</div><p class="fine">' + t("adoption_top3.adoption_counts_are_deduplicated_organizations_in", "Adoption counts are deduplicated organizations in public primary sources, not quality scores; evidence was checked on {value}. No primary public UI browser-use benchmark was found for DeepSeek or Kimi, which does not imply that they perform no internal evaluation.", value=_e(evidence.get('verified_at') or t('common.not_recorded', 'Not recorded'))) + "</p>")
    )


def _same_task_comparison(
    tasks: list[dict[str, Any]], attempts: list[dict[str, Any]], participants: Participants
) -> str:
    """Render one task-by-configuration matrix; never reduce it to pairwise controls."""

    results = {(_row_model(row), str(row["engine"]), int(row["task_id"])): row for row in attempts}
    engines = participants.engines_baseline_first
    configurations = [(model, engine) for model in participants.models for engine in engines]
    if len(configurations) < 2:
        return ('<p class="muted-box">' + t("same_task_comparison.at_least_two_configurations_are_required", "At least two configurations are required for a same-task comparison.") + "</p>")

    rows = []
    for task in tasks:
        task_id = int(task["task_id"])
        states = []
        cells = []
        for model, engine in configurations:
            result = results.get((model, engine, task_id))
            if result is None:
                state, label = "missing", t("same_task_comparison.not_measured", "Not measured")
            elif float(result["score"]) == 1:
                state, label = "pass", t("same_task_comparison.pass", "Pass")
            else:
                state, label = "fail", t("same_task_comparison.fail", "Fail")
            states.append(state)
            cells.append(
                f'<td><a class="outcome-pill {state}" href="results/{model}-{engine}-{task_id}.html">'
                f"{label}</a></td>"
                if result is not None
                else ('<td><span class="outcome-pill missing">' + t("same_task_comparison.not_measured", "Not measured") + "</span></td>")
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
            f'{_raw(task["rationale"])}</a><div class="task-meta">'
            f'<span class="track-badge">{_e(_tier_label(task["tier"]))}</span>'
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
        f'<th>{_e(participants.models[model])}<br><span class="table-kicker">{_e(participants.engines[engine])}</span></th>'
        for model, engine in configurations
    )
    return (
        '<article class="browser-comparison" id="configuration-matrix"><header class="comparison-heading"><div><h3>' + t("same_task_comparison.same_task_all_configuration_matrix", "Same-task all-configuration matrix") + "</h3><p>" + t("same_task_comparison.each_row_is_one_task_and", "Each row is one task and each column is one model-by-browser configuration. New configurations add columns only.") + '</p></div><b class="comparison-count" data-matrix-count></b></header><div class="filters matrix-filters"><label>' + t("same_task_comparison.outcome_pattern", "Outcome pattern") + ' <select data-matrix-filter="relation"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="mixed" selected>' + t("same_task_comparison.outcomes_differ", "Outcomes differ") + '</option><option value="all_pass">' + t("same_task_comparison.all_pass", "All pass") + '</option><option value="all_fail">' + t("same_task_comparison.all_fail", "All fail") + "</option></select></label><label>" + t("same_task_comparison.tier", "Tier") + ' <select data-matrix-filter="tier"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="easy">' + t("tier_label.easy", "Easy") + '</option><option value="medium">' + t("tier_label.medium", "Medium") + '</option><option value="hard">' + t("tier_label.hard", "Hard") + "</option></select></label><label>" + t("same_task_comparison.site", "Site") + ' <select data-matrix-filter="site"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="shopping">Shopping</option><option value="shopping_admin">Shopping Admin</option><option value="reddit">Reddit</option><option value="gitlab">GitLab</option></select></label><label>' + t("same_task_comparison.type", "Type") + ' <select data-matrix-filter="kind"><option value="">' + t("same_task_comparison.all", "All") + '</option><option>RETRIEVE</option><option>NAVIGATE</option><option>MUTATE</option></select></label></div><div class="comparison-result"><div class="table-wrap"><table class="comparison-table configuration-matrix"><thead><tr><th>' + t("same_task_comparison.task", "Task") + f"</th>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></article>"
    )


def _action_duration_cards(attempts: list[dict[str, Any]], participants: Participants) -> str:
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
        for model in participants.models:
            for engine in participants.engines:
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
        for model in participants.models:
            for engine in participants.engines:
                key = f"{model}|{engine}"
                total, count, average = metrics[key]
                width = 0 if average is None or maximum <= 0 else max(2.0, average / maximum * 100)
                average_text = "—" if average is None else t("action_duration_cards.seconds", "{average:.3f} seconds", average=average / 1000)
                browser_rows.append(
                    f'''<div class="action-browser-row{' action-winner' if key in average_winners else ''}"><div class="action-browser-head"><b>{_e(participants.models[model])} · {_e(participants.engines[engine])}</b>{_winner(average_text, won=key in average_winners, label=t('action_duration_cards.lowest_mean_per_call_latency_across', 'Lowest mean per-call latency across all configurations'))}</div><div class="action-bar" aria-hidden="true"><i style="width:{width:.1f}%"></i></div><div class="action-browser-meta">''' + t("action_duration_cards.total_s_calls", "Total {total:.1f} s · {count} calls", total=total / 1000, count=count) + "</div></div>"
                )
        cards.append(
            '<article class="action-card"><header><code>'
            + _e(action)
            + ("</code><span>" + t("action_duration_cards.all_configurations_per_call_wall_time", "All configurations · per-call wall time") + "</span></header>")
            + "".join(browser_rows)
            + "</article>"
        )
    return '<div class="action-card-grid">' + "".join(cards) + "</div>"


def _summary_comparison_rows(
    summary: dict[str, dict[str, dict[str, Any]]], participants: Participants
) -> list[list[Any]]:
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
        for engine, label in participants.engines.items():
            key = f"{model}|{engine}"
            current = model_summary[engine]
            rows.append(
                [
                    participants.models[model],
                    label,
                    _winner(
                        f"{current['passed']}/{current['completed']}",
                        won=key in higher_metrics["passed"],
                        label=t("summary_comparison_rows.highest_pass_count_across_all_configurations", "Highest pass count across all configurations"),
                    ),
                    _winner(
                        _pct(current["score"]),
                        won=key in higher_metrics["score"],
                        label=t("summary_comparison_rows.highest_pass_rate_across_all_configurations", "Highest pass rate across all configurations"),
                    ),
                    _metric_stack(
                        [
                            (
                                t("summary_comparison_rows.mean", "Mean"),
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
                                t("summary_comparison_rows.mean", "Mean"),
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
                                t("summary_comparison_rows.mean", "Mean"),
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
        return t("seconds.seconds", "{value:.1f} seconds", value=value / 1000)
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "mib":
        return f"{value / 1_048_576:,.1f} MiB"
    if unit == "mib_net":
        return f"{value / 1_048_576:,.2f} MiB"
    raise ValueError(f"unknown resource unit: {unit}")


def _resource_detail_rows(resource: dict[str, Any]) -> list[list[str]]:
    fields = (
        ("cpu_ms", t("resource_detail_rows.browser_cpu_time", "Browser CPU time"), "ms"),
        ("cpu_average_utilization_pct", t("resource_detail_rows.average_single_core_cpu_utilization", "Average single-core CPU utilization"), "pct"),
        ("memory_mean_bytes", t("resource_detail_rows.time_weighted_mean_memory", "Time-weighted mean memory"), "mib"),
        ("memory_peak_bytes", t("resource_detail_rows.peak_memory", "Peak memory"), "mib"),
        ("memory_retained_bytes", t("resource_detail_rows.additional_resident_memory_at_completion", "Additional resident memory at completion"), "mib"),
        ("network_rx_bytes", t("resource_detail_rows.network_received", "Network received"), "mib_net"),
        ("network_tx_bytes", t("resource_detail_rows.network_transmitted", "Network transmitted"), "mib_net"),
    )
    return [
        [label, _resource_value(float(resource[key]), unit)]
        for key, label, unit in fields
        if resource.get(key) is not None
    ]


def _evidence_check_rows(checks: dict[str, Any]) -> list[list[str]]:
    labels = {
        "network_har_exists": t("evidence_check_rows.browser_network_record_exists", "Browser network record exists"),
        "agent_trace_exists": t("evidence_check_rows.browser_execution_trace_exists", "Browser execution trace exists"),
        "response_exists": t("evidence_check_rows.agent_final_response_exists", "Agent final response exists"),
        "evaluation_exists": t("evidence_check_rows.official_evaluation_details_exist", "Official evaluation details exist"),
        "events_exists": t("evidence_check_rows.agent_event_stream_exists", "Agent event stream exists"),
        "environment_prepare_exists": t("evidence_check_rows.environment_preparation_record_exists", "Environment preparation record exists"),
        "evaluation_json": t("evidence_check_rows.evaluation_details_are_readable", "Evaluation details are readable"),
        "response_json": t("evidence_check_rows.final_response_is_readable", "Final response is readable"),
        "response_matches_record": t("evidence_check_rows.final_response_matches_the_run_record", "Final response matches the run record"),
        "score_matches_evaluator": t("evidence_check_rows.reported_score_matches_official_evaluation", "Reported score matches official evaluation"),
        "status_matches_evaluator": t("evidence_check_rows.reported_status_matches_official_evaluation", "Reported status matches official evaluation"),
        "official_evaluator_version_present": t("evidence_check_rows.official_evaluator_version_is_recorded", "Official evaluator version is recorded"),
        "official_evaluator_checksum_matches": t("evidence_check_rows.official_evaluator_checksum_matches", "Official evaluator checksum matches"),
        "official_data_checksum_matches": t("evidence_check_rows.official_dataset_checksum_matches", "Official dataset checksum matches"),
        "model_request_attempts_accounted": t("evidence_check_rows.all_model_request_attempts_are_accounted", "All model request attempts are accounted for"),
    }
    return [
        [labels.get(key, key.replace("_", " ")), t("same_task_comparison.pass", "Pass") if value else t("evidence_check_rows.fail", "Fail")]
        for key, value in checks.items()
    ]


def _publication_label(value: Any) -> str:
    return {
        "private_only_session_sensitive": t("publication_label.hash_and_byte_count_only_session", "Hash and byte count only (session-sensitive)"),
        "sanitized_projection": t("publication_label.sanitized_public_copy", "Sanitized public copy"),
        "public": t("publication_label.public_original_file", "Public original file"),
    }.get(str(value), t("publication_label.handled_according_to_the_evidence_inventory", "Handled according to the evidence inventory"))


def _resource_comparison_table(
    summary: dict[str, dict[str, dict[str, Any]]], participants: Participants
) -> str:
    metrics = (
        ("cpu_ms", t("resource_comparison_table.cpu_time", "CPU time"), "ms"),
        ("cpu_average_utilization_pct", t("resource_comparison_table.mean_single_core_cpu_utilization", "Mean single-core CPU utilization"), "pct"),
        ("memory_mean_bytes", t("resource_detail_rows.time_weighted_mean_memory", "Time-weighted mean memory"), "mib"),
        ("memory_peak_bytes", t("resource_detail_rows.peak_memory", "Peak memory"), "mib"),
        ("network_rx_bytes", t("resource_detail_rows.network_received", "Network received"), "mib_net"),
        ("network_tx_bytes", t("resource_detail_rows.network_transmitted", "Network transmitted"), "mib_net"),
    )
    flat = {
        f"{model}|{engine}": values
        for model, model_summary in summary.items()
        for engine, values in model_summary.items()
    }
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
    }
    rows: list[list[Any]] = []
    for model, model_summary in summary.items():
        for engine, label in participants.engines.items():
            key = f"{model}|{engine}"
            resources = model_summary[engine].get("resources") or {}
            rows.append(
                [
                    participants.models[model],
                    label,
                    *[
                        _metric_stack(
                            [
                                (
                                    stat.upper() if stat != "mean" else t("summary_comparison_rows.mean", "Mean"),
                                    _resource_value((resources.get(name) or {}).get(stat), unit),
                                    key in winners[(name, stat)],
                                )
                                for stat in ("mean", "p50", "p95")
                            ]
                        )
                        for name, _, unit in metrics
                    ],
                ]
            )
    return _table(
        [t("resource_comparison_table.model", "Model"), t("resource_comparison_table.browser", "Browser"), *[label for _, label, _ in metrics]],
        rows,
        table_id="resource-metrics-table",
        classes="wide-data-table resource-comparison-table",
    )


REPORT_JS = (Path(__file__).parent / "assets" / "report.js").read_text(encoding="utf-8")
REPORT_STYLE = (
    SHARED_STYLE
    + (Path(__file__).parent / "assets" / "report.css").read_text(encoding="utf-8")
)
def _nav() -> list[tuple[str, str, str]]:
    return [
    ("", "Browser Use", "home"),
    ("datasets/", t("nav.dataset", "Dataset"), "datasets"),
    ("methodology/", t("nav.methodology", "Methodology"), "methodology"),
    ("analysis/", t("nav.results_analysis", "Results analysis"), "analysis"),
    ("review/", t("nav.review", "Review"), "review"),
    ("fixes/", t("nav.fix_log", "Fix log"), "fixes"),
    ("adoption/", t("nav.browsers_tested", "Browsers tested"), "adoption"),
    ("reproduce/", t("nav.environment_and_reproduction", "Environment and reproduction"), "reproduce"),
    ("changes/", t("nav.changes", "Changes"), "changes"),
    ("accounts/", t("nav.accounts_and_setup", "Accounts and setup"), "accounts"),
    ("en/", "English", "en"),
    ]


def _page(*, title: str, active: str, body: str, depth: int = 0) -> str:
    local_base = "../" * depth or "./"
    links = "".join(
        f'<a href="{href}"'
        + (' class="brand"' if key == "home" else "")
        + (' aria-current="page"' if key == active else "")
        + f">{_e(label)}</a>"
        for href, label, key in _nav()
    )
    return (f'<!doctype html><html lang="zh-CN"><head><base href="{local_base}"><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + t("page.browser_use_evaluation", "{title} · Browser Use evaluation", title=_e(title)) + f'</title><link rel="stylesheet" href="assets/style.css"><script defer src="assets/report.js"></script></head><body><header class="top"><nav class="wrap">{links}<button class="theme-toggle" onclick="theme()">' + t("page.toggle_theme", "Toggle theme") + f'</button></nav></header><main class="page-main">{body}</main><footer class="footer"><div class="wrap"><p>' + t("page.conclusions_apply_only_to_this_preregistered", "Conclusions apply only to this preregistered sample, agent, model, and runtime configuration. Presentation changes do not alter measured results.") + "</p></div></footer></body></html>")


def _evidence_viewer_page() -> str:
    body = _hero(
        "Execution evidence",
        t("evidence_viewer_page.complete_execution_evidence", "Complete execution evidence"),
        t("evidence_viewer_page.shows_the_sanitized_response_evaluator_record", "Shows the sanitized response, evaluator record, event stream, trajectory, measurements, and public-frame index for this physical execution in their original structure."),
    )
    body += (
        ('<section class="section evidence-viewer-shell"><div class="wrap"><div class="evidence-viewer-head"><h2>' + t("evidence_viewer_page.execution_evidence_json", "Execution evidence JSON") + '</h2><a id="evidence-raw-link" class="button-link" hidden download>' + t("evidence_viewer_page.download_raw_json", "Download raw JSON") + '</a></div><p id="evidence-viewer-status" class="fine" role="status">' + t("evidence_viewer_page.loading_evidence", "Loading evidence…") + '</p><pre id="evidence-viewer-content" class="json-view" tabindex="0"></pre></div></section><script>')
        + (Path(__file__).parent / "assets" / "evidence-viewer.js").read_text(encoding="utf-8")
        + "</script>"
    )
    return _page(title=t("evidence_viewer_page.complete_execution_evidence", "Complete execution evidence"), active="analysis", body=body)


def _hero(eyebrow: str, title: str, lead: str) -> str:
    return f'<section class="hero compact"><div class="wrap"><div class="eyebrow">{_e(eyebrow)}</div><h1>{title if isinstance(title, Safe) else _e(title)}</h1><p class="lead">{lead if isinstance(lead, Safe) else _e(lead)}</p></div></section>'


def _section(title: str, body: str) -> str:
    return f'<section class="section"><div class="wrap"><h2>{_e(title)}</h2>{body}</div></section>'


def _status(row: dict[str, Any]) -> Safe:
    passed = float(row["score"]) == 1
    return Safe(
        f"<span class=\"status-{'pass' if passed else 'fail'}\">{t('same_task_comparison.pass', 'Pass') if passed else t('same_task_comparison.fail', 'Fail')}</span>"
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
            + _e(t("task_contract_html.rule", "Rule {index} · {evaluator}", index=index, evaluator=evaluator))
            + ('</h3><p><span class="badge">' + t("task_contract_html.programmatic_acceptance", "Programmatic acceptance") + "</span>" + t("task_contract_html.this_rule_must_score_1_0", "This rule must score 1.0.") + "</p>")
            + _json_pre(expected)
            + (
                ("<details><summary>" + t("task_contract_html.matching_options_and_result_schema", "Matching options and result schema") + f"</summary>{_json_pre(qualifiers)}</details>")
                if qualifiers
                else ""
            )
            + "</article>"
        )
    return (
        ('<div class="muted-box"><strong>' + t("task_contract_html.there_is_no_single_reference_click", "There is no single reference click path.") + "</strong> " + t("task_contract_html.webarena_verified_lets_the_agent_choose", "WebArena-Verified lets the agent choose any UI path. Ground truth is the conjunction of the final-answer and required network-side-effect contracts below. The report therefore compares outcome contracts rather than treating a human path as the answer key.") + "</div>")
        + '<div class="evidence-pair">'
        + "".join(cards)
        + "</div>"
    )


def _failure_diagnosis(result: dict[str, Any] | None, row: dict[str, Any]) -> str:
    failed = [item for item in evaluator_summary(result) if float(item.get("score") or 0) < 1]
    if not failed:
        return t("failure_diagnosis.all_official_evaluators_passed", "All official evaluators passed.")
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
                t("failure_diagnosis.the_final_answer_does_not_match", "The final answer does not match the official structured answer")
                if item["name"] == "AgentResponseEvaluator"
                else t("failure_diagnosis.no_required_network_request_or_side", "No required network request or side effect satisfying the contract was observed")
                if item["name"] == "NetworkEventEvaluator"
                else t("failure_diagnosis.this_rule_failed", "This rule failed")
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
        parts.append(t("failure_diagnosis.rule", "Rule {index} {name}: {exact}", index=item['index'], name=item['name'], exact=exact))
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
            messages.append(_rule_outcome_summary(item, t))
        for message in dict.fromkeys(messages):
            lines.append(t("evaluator_error_text.rule", "Rule {index} · {name}: {message}", index=item['index'], name=item['name'], message=sanitize(message)))
    if row.get("error"):
        lines.append(t("evaluator_error_text.runtime_error", "Runtime error: {error}", error=sanitize(row['error'])))
    response = row.get("agent_response") or {}
    if response.get("error_details"):
        lines.append(t("evaluator_error_text.agent_error", "Agent error: {error_details}", error_details=sanitize(response['error_details'])))
    return "\n".join(lines) or t("evaluator_error_text.the_program_did_not_provide_a", "The program did not provide a more specific error message.")


def _failed_rule_labels(result: dict[str, Any] | None) -> list[str]:
    return [
        t("failed_rule_labels.rule", "Rule {index} {name}", index=item['index'], name=item['name'])
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
        return "response", t("automatic_failure_type.final_answer_acceptance_failure", "Final-answer acceptance failure")
    if names == {"NetworkEventEvaluator"}:
        return "network", t("automatic_failure_type.network_side_effect_acceptance_failure", "Network-side-effect acceptance failure")
    if names == {"AgentResponseEvaluator", "NetworkEventEvaluator"}:
        return "mixed", t("automatic_failure_type.multiple_acceptance_failures", "Multiple acceptance failures")
    raise ValueError(f"unknown failed evaluator combination: {sorted(names)}")


def _cause_code(verified: dict[str, dict[str, Any]], result_id: str) -> str:
    return str(((verified.get(result_id) or {}).get("subcategory") or {}).get("code") or "")


def _tool_error_point(step: int) -> str:
    return t("tool_error_point.step_browsertool_execution_error", "Step {step}: BrowserTool execution error", step=step)


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
        return t("failure_point.after_environment_preparation_and_before_the", "After environment preparation and before the Agent read"), None
    if code.startswith("benchmark_"):
        return t("failure_point.evaluator_acceptance_after_execution", "Evaluator acceptance after execution"), last_step
    if code.startswith("evidence_") or code == "evidence_capture":
        return t("failure_point.network_evidence_acceptance_after_execution", "Network-evidence acceptance after execution"), last_step
    if code.startswith("site_"):
        return (t("failure_point.after_steps_site_response_save", "After {last_step} steps: site response/save", last_step=last_step) if last_step else t("failure_point.site_response_save", "Site response/save")), last_step
    if failed_step:
        number = int(failed_step.get("step") or last_step or 1)
        return _tool_error_point(number), number
    if code == "agent_step_budget":
        return (t("failure_point.after_steps_step_limit_reached", "After {last_step} steps: step limit reached", last_step=last_step) if last_step else t("failure_point.step_limit_reached", "Step limit reached")), last_step
    return (
        t("failure_point.after_steps_final_answer_task_state", "After {last_step} steps: final answer/task state", last_step=last_step) if last_step else t("failure_point.final_answer_task_state", "Final answer/task state")
    ), last_step


def _trajectory_context(
    steps: list[dict[str, Any]], step_number: int | None, failure_point: str, t: Callable[..., str]
) -> tuple[str, str]:
    """Describe the linked step without claiming a successful action caused evaluator failure."""

    if step_number is None:
        return "", ""
    step = next((item for item in steps if int(item.get("step") or 0) == step_number), None)
    if step is None:
        return "", ""
    label = trajectory_label(step)
    prefix = t("trajectory_context.failed_action", "Failed action") if failure_point == _tool_error_point(step_number) else t("trajectory_context.last_step_before_evaluation", "Last step before evaluation")
    return prefix, label


def _remediation(code: str) -> tuple[str, str, bool]:
    """Return owner, current action, and whether this is an external interference."""

    if code.startswith("benchmark_"):
        return t("remediation.benchmark_evaluator", "Benchmark / evaluator"), t("remediation.fix_the_contract_and_rerun_retain", "Fix the contract and rerun; retain only the official score for now"), True
    if code.startswith("site_"):
        return t("failure_cause_table.self_hosted_site", "Self-hosted site"), t("remediation.fix_the_dependency_or_save_path", "Fix the dependency or save path, then rerun after a write preflight"), True
    if code.startswith("environment_"):
        return t("remediation.runtime_environment", "Runtime environment"), t("remediation.the_cross_process_lock_is_implemented", "The cross-process lock is implemented; contaminated tasks await rerun"), True
    if code.startswith("mixed_"):
        return t("remediation.task_and_agent", "Task and Agent"), t("remediation.freeze_reference_state_then_rerun_to", "Freeze reference state, then rerun to distinguish Agent errors"), True
    if code.startswith("evidence_") or code == "evidence_capture":
        return t("failure_cause_table.browser_evidence_interface", "Browser evidence interface"), t("remediation.keep_as_a_browser_integration_signal", "Keep as a browser-integration signal; rerun after completing HAR capture"), False
    return t("remediation.agent_model", "Agent / model"), t("remediation.keep_as_a_target_variable_signal", "Keep as a target-variable signal; improve the strategy or tool contract from the trajectory"), False


def _failure_brief(result: dict[str, Any] | None, row: dict[str, Any]) -> str:
    labels = _failed_rule_labels(result)
    if labels:
        joined = t("list.separator", ", ").join(labels)
        return t("failure_brief.rules_failed", "{labels_count} rules failed: {joined}.", labels_count=len(labels), joined=joined)
    if row.get("error"):
        return t("failure_brief.execution_did_not_complete_see_runtime", "Execution did not complete; see runtime and evidence for the error.")
    return t("failure_diagnosis.all_official_evaluators_passed", "All official evaluators passed.")


def _rule_purpose(name: str) -> str:
    if name == "AgentResponseEvaluator":
        return t("rule_purpose.checks_whether_the_status_fields_and", "Checks whether the status, fields, and values in the agent's final answer satisfy the official answer contract.")
    if name == "NetworkEventEvaluator":
        return t("rule_purpose.checks_whether_the_browser_network_record", "Checks whether the browser network record contains the side effect required by the contract.")
    return t("rule_purpose.checks_an_outcome_condition_declared_by", "Checks an outcome condition declared by the task's official contract.")


def _rule_outcome_summary(item: dict[str, Any], t: Callable[..., str]) -> str:
    if float(item.get("score") or 0) == 1:
        return (
            t("rule_outcome_summary.a_network_event_satisfying_the_contract", "A network event satisfying the contract was observed.")
            if item.get("name") == "NetworkEventEvaluator"
            else t("rule_outcome_summary.the_final_answer_satisfies_this_rule", "The final answer satisfies this rule.")
        )
    messages = [
        str(message)
        for assertion in item.get("assertions") or []
        for message in (assertion.get("assertion_msgs") or [])[:1]
    ]
    message = messages[0] if messages else str(item.get("error") or "")
    status = re.fullmatch(r"Expected ([^,]+), got (.+)", message)
    if status:
        return Safe(t("rule_outcome_summary.final_status_mismatch_expected_observed", "Final status mismatch: expected {value}, observed {value_2}.", value=_raw(status.group(1)), value_2=_raw(status.group(2))))
    missing = re.fullmatch(
        r"Array is missing (\d+) expected element\(s\)\. Matched \((\d+)/(\d+)\)",
        message,
    )
    if missing:
        return t("rule_outcome_summary.the_final_data_is_missing_item", "The final data is missing {value} item(s); {value_3} of {value_2} expected item(s) matched.", value=missing.group(1), value_2=missing.group(3), value_3=missing.group(2))
    if "Array value mismatch" in message:
        return t("rule_outcome_summary.the_final_data_differs_from_the", "The final data differs from the official expectation.")
    if "No matching network event" in message:
        return t("rule_outcome_summary.no_browser_network_event_satisfying_this", "No browser network event satisfying this rule was observed.")
    return _raw(message) if message else t("rule_outcome_summary.this_rule_failed", "This rule failed.")


def _verification_html(task: dict[str, Any], result: dict[str, Any] | None, response: Any) -> str:
    items = evaluator_summary(result)
    if not items:
        return ('<p class="muted-box">' + t("verification_html.no_evaluator_details_are_available", "No evaluator details are available.") + "</p>")
    cards = []
    for item in items:
        passed = float(item.get("score") or 0) == 1
        observed = response if item.get("name") == "AgentResponseEvaluator" else item.get("actual")
        technical = {
            key: item.get(key)
            for key in ("assertions", "error", "should_not_exist")
            if item.get(key) not in (None, [], {})
        }
        summary = _rule_outcome_summary(item, t)
        cards.append(
            f'<article class="verification-rule rule-{"pass" if passed else "fail"}">'
            '<header><div><span class="rule-index">'
            + _e(t("verification_html.rule", "Rule {index}", index=item['index']))
            + "</span><h3>"
            + _e(item["name"])
            + f'</h3></div><span class="rule-verdict {"pass" if passed else "fail"}">'
            + (t("same_task_comparison.pass", "Pass") if passed else t("same_task_comparison.fail", "Fail"))
            + "</span></header>"
            + f'<p class="rule-purpose">{_e(_rule_purpose(item["name"]))}</p>'
            + f'<p class="rule-summary">{summary if isinstance(summary, Safe) else _e(summary)}</p>'
            + ("<details><summary>" + t("verification_html.view_the_official_expectation_and_this", "View the official expectation and this run's observation") + '</summary><div class="evidence-pair compact-evidence"><div><h4>' + t("verification_html.official_expectation", "Official expectation") + "</h4>")
            + _json_pre(item.get("expected"))
            + ("</div><div><h4>" + t("verification_html.observed_in_this_run", "Observed in this run") + "</h4>")
            + _json_pre(observed)
            + "</div></div></details>"
            + (
                ("<details><summary>" + t("verification_html.view_evaluator_technical_details", "View evaluator technical details") + f"</summary>{_json_pre(technical)}</details>")
                if technical
                else ""
            )
            + "</article>"
        )
    return (
        ('<div class="task-goal"><span>' + t("verification_html.task_objective", "Task objective") + "</span><p>")
        + _e(task.get("intent") or "—")
        + ("</p><small>" + t("verification_html.the_agent_may_choose_any_ui", "The agent may choose any UI path. Scoring uses only the final answer and required network side effects below.") + "</small></div>")
        + '<div class="verification-list">'
        + "".join(cards)
        + "</div>"
    )


def _trajectory_html(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return ('<p class="muted-box">' + t("trajectory_html.no_browsertool_calls_are_available_to", "No BrowserTool calls are available to display.") + "</p>")
    rows = []
    for step in steps:
        step_number = int(step.get("step") or len(rows) + 1)
        failed = str(step.get("status") or "").lower() not in {"", "success"} or bool(
            step.get("error")
        )
        duration = step.get("duration_seconds")
        meta = f"Turn {step.get('turn') or '—'} · {_e(step.get('status') or 'unknown')}"
        if duration is not None:
            meta += (" " + t("trajectory_html.s", "· {duration:.2f} s", duration=float(duration)))
        rows.append(
            f'<li id="trajectory-step-{step_number}" class="trajectory-step{" failure-step" if failed else ""}"><div><details{" open" if failed else ""}><summary>'
            + _raw(trajectory_label(step))
            + (f'</summary><p class="fine">{meta}</p><h4>' + t("trajectory_html.call_arguments", "Call arguments") + "</h4>")
            + _json_pre(step.get("arguments"))
            + ("<h4>" + t("trajectory_html.execution_output_excerpt", "Execution output excerpt") + "</h4>")
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
    from PIL import Image

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
                        "sha256": optional_file_sha256(destination),
                        "label": _ZH("extract_trace_screenshots.final_page_frame", "Final page frame {index}/{total}", index=index, total=total),
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
        f'<div class="page-links"><a href="{viewer}">' + t("artifact_links.view_complete_execution_evidence_on_this", "View complete execution evidence on this page") + f'</a><a href="{root}/execution-evidence.json" download>' + t("evidence_viewer_page.download_raw_json", "Download raw JSON") + "</a></div>"
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
    write_json(artifact_dir / PUBLIC_EXECUTION_ARTIFACTS["execution_evidence"], bundle, parents=True)
    artifacts = {
        label: {
            "path": f"artifacts/{result_id}/executions/{execution}/{filename}",
            "sha256": optional_file_sha256(artifact_dir / filename),
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
        return ('<p class="muted-box">' + t("screenshot_gallery.this_execution_has_no_publishable_trace", "This execution has no publishable trace page frame; the error log and execution evidence remain available.") + "</p>")
    root = f"artifacts/{result_id}/executions/{execution}"
    figures = "".join(
        '<figure><a href="'
        + _e(f"{root}/{item['path']}")
        + '"><img loading="lazy" src="'
        + _e(f"{root}/{item['path']}")
        + '" alt="'
        + _e(t("extract_trace_screenshots.final_page_frame", "Final page frame {index}/{total}", index=index, total=len(screenshots)))
        + '"></a><figcaption>'
        + _e(t("extract_trace_screenshots.final_page_frame", "Final page frame {index}/{total}", index=index, total=len(screenshots)))
        + f" · SHA-256 {_e(str(item['sha256'])[:12])}…</figcaption></figure>"
        for index, item in enumerate(screenshots, start=1)
    )
    return (
        "<p>" + t("screenshot_gallery.these_are_the_final_page_frames", "These are the final page frames from the scoring execution. They help verify visible state before failure but may not capture the exact instant the error occurred.") + f'</p><div class="screenshot-grid">{figures}</div>'
    )


def _execution_evidence_html(result_id: str, executions: list[dict[str, Any]]) -> str:
    cards = []
    for execution in executions:
        usage = execution["model_usage"]
        authority = (" " + t("execution_evidence_html.scoring_authority", "· scoring authority")) if execution["authoritative"] else ""
        hashes = _table(
            [t("execution_evidence_html.public_files", "Public files"), "SHA-256"],
            [[label, artifact["sha256"]] for label, artifact in execution["artifacts"].items()],
        )
        cards.append(
            ('<article class="review-round"><h3>' + t("execution_evidence_html.physical_execution", "Physical execution {execution}{authority}", execution=execution['execution'], authority=authority) + "</h3><p>" + t("execution_evidence_html.score_agent_path_browsertool_calls_model", "Score {score} · Agent path {duration_ms} · BrowserTool {action_count} calls / {browser_tool_duration_ms} · model tokens {total_tokens}", score=_number(execution['score']), duration_ms=_seconds_short(execution['duration_ms']), action_count=execution['action_count'], browser_tool_duration_ms=_seconds_short(execution['browser_tool_duration_ms']), total_tokens=_number(usage['total_tokens'])) + "</p>")
            + _artifact_links(result_id, int(execution["execution"]))
            + hashes
            + "</article>"
        )
    return (
        ("<p>" + t("execution_evidence_html.this_logical_result_contains", "This logical result contains") + f" <strong>{len(executions)}</strong> " + t("execution_evidence_html.physical_execution_s_the_scoring_authority", "physical execution(s). The scoring-authority execution determines the final score; cumulative latency, tokens, and BrowserTool metrics sum all executions shown below.") + '</p><div class="review-rounds">') + "".join(cards) + "</div>"
    )


def _common_final_success_resource_rows(
    attempts: list[dict[str, Any]], participants: Participants
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
            for model in participants.models
            for engine in participants.engines
        )
    }

    def median(values: list[float]) -> float | None:
        return statistics.median(values) if values else None

    def resource_median(cohort: list[dict[str, Any]], name: str) -> float | None:
        values = [(row.get("resource") or {}).get(name) for row in cohort]
        return None if None in values else median([float(value) for value in values])

    rows: list[list[Any]] = []
    for model in participants.models:
        for engine in participants.engines:
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
            rows.append(
                [
                    participants.models[model],
                    participants.engines[engine],
                    _seconds(median([float(row.get("duration_ms") or 0) for row in cohort])),
                    _seconds(
                        median([float(row.get("browser_tool_duration_ms") or 0) for row in cohort])
                    ),
                    _number(
                        median(
                            [float((row.get("model_usage") or {}).get("total_tokens") or 0) for row in cohort]
                        )
                    ),
                    _resource_value(resource_median(cohort, "memory_peak_bytes"), "mib"),
                    _seconds(resource_median(cohort, "cpu_ms")),
                ]
            )
    return len(common), rows


def _failure_scope_counts(verified_causes: dict[str, dict[str, Any]]) -> list[tuple[str, int, str]]:
    copy = {
        "agent": (t("remediation.agent_model", "Agent / model"), t("failure_scope_counts.incomplete_retrieval_wrong_selection_omitted_fields", "Incomplete retrieval, wrong selection, omitted fields, or premature termination")),
        "benchmark": (t("failure_cause_table.task_evaluator", "Task / evaluator"), t("failure_scope_counts.acceptance_contracts_disagree_with_page_semantics", "Acceptance contracts disagree with page semantics, or the evaluator itself errors")),
        "site": (t("failure_cause_table.self_hosted_site", "Self-hosted site"), t("failure_scope_counts.search_session_or_persistence_dependency_failure", "Search, session, or persistence dependency failure")),
        "evidence": (t("failure_cause_table.browser_evidence_interface", "Browser evidence interface"), t("failure_scope_counts.har_lacks_referer_or_response_bodies", "HAR lacks Referer or response bodies, or contains duplicate records")),
        "environment": (t("failure_scope_counts.shared_environment", "Shared environment"), t("failure_scope_counts.concurrent_execution_changed_the_task_s", "Concurrent execution changed the task's initial state")),
        "mixed": (t("failure_cause_table.reference_state_mixed_with_agent_behavior", "Reference state mixed with Agent behavior"), t("failure_scope_counts.reference_drift_and_an_agent_omission", "Reference drift and an agent omission both contributed")),
    }

    def scope(code: str) -> str:
        for prefix in ("agent", "benchmark", "site", "environment", "mixed"):
            if code.startswith(prefix + "_"):
                return prefix
        if code.startswith("evidence_"):
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
    return ('<div class="cause-bars" aria-label="' + t("failure_scope_figure.reviewed_cause_distribution_for_failed_results", "Reviewed cause distribution for {value} failed results", value=sum((count for _, count, _ in rows))) + '">') + bars + "</div>"


def _replay_engine_field(bridge: dict[str, Any], suffix: str) -> tuple[str, dict[str, Any]]:
    """Return the engine and value of the one frozen ``<engine><suffix>`` bridge field."""

    fields = [
        (key.removesuffix(suffix), value) for key, value in bridge.items() if key.endswith(suffix)
    ]
    if len(fields) > 1:
        raise ValueError(f"trajectory replay bridge has more than one {suffix} field")
    return fields[0] if fields else ("", {})


def _replay_results(bridge: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        engine: value
        for engine, value in (bridge.get("effective_results") or {}).items()
        if isinstance(value, dict) and "tasks" in value and "passes" in value
    }


def _replay_counts_are_valid(value: dict[str, Any]) -> bool:
    tasks, passes = value["tasks"], value["passes"]
    return type(tasks) is int and type(passes) is int and 0 <= passes <= tasks


def _validate_replay_contract_definitions(bridge: dict[str, Any]) -> None:
    replay_engine, provenance = _replay_engine_field(bridge, "_trajectory_provenance")
    definitions = {}
    for name in ("trajectories", "corrections"):
        binding = (bridge.get("contract_definitions") or {}).get(name) or {}
        path = Path(str(binding.get("path") or ""))
        if path.is_symlink() or not path.is_file() or optional_file_sha256(path) != binding.get("sha256"):
            raise ValueError("trajectory replay contract definitions are not hash-bound")
        definitions[name] = _read_json(path)
    dataset = definitions["trajectories"]
    if dataset.get("selection_sha256") != bridge.get("selection_sha256"):
        raise ValueError("trajectory replay contract definitions use a different selection")
    rows = dataset["trajectories"]
    if len({row["task_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate trajectory task in contract definitions")
    counts: Counter[str] = Counter()
    cross: dict[str, Counter[str]] = {}
    origins: Counter[str] = Counter()
    corrected = set()
    for row in rows:
        kind = row["verification"]["contract"]["kind"]
        counts[kind] += 1
        cross.setdefault(row["source"]["kind"], Counter())[kind] += 1
        if row["source"]["kind"] == "authoritative_successful_browser_use_execution":
            origins[f'{row["source"]["model"]}_{row["source"]["browser"]}'] += 1
        if kind == "corrected_replay_contract":
            corrected.add(row["task_id"])
    categories: dict[str, list[int]] = {
        "event_selection": [], "event_selection_and_expected": [], "reference_or_acceptance": []
    }
    corrections = definitions["corrections"]["corrections"]
    if {row["task_id"] for row in corrections} != corrected:
        raise ValueError("trajectory replay correction scope differs from qualified definitions")
    for row in corrections:
        operations = row["operations"]
        selection = any(op["path"].endswith("/last_event_only") for op in operations)
        other = any(not op["path"].endswith("/last_event_only") for op in operations)
        category = "event_selection_and_expected" if selection and other else (
            "event_selection" if selection else "reference_or_acceptance"
        )
        categories[category].append(row["task_id"])
    categories = {key: sorted(value) for key, value in categories.items()}
    if (dict(counts) != bridge.get("contract_counts")
        or {key: dict(value) for key, value in cross.items()} != bridge.get("source_contract_counts")
        or categories != bridge.get("correction_categories")
        or len(rows) != _replay_results(bridge).get(replay_engine, {}).get("tasks")):
        raise ValueError("trajectory replay contract counts differ from qualified definitions")
    if dict(origins) != provenance.get("successful_browser_use_execution_by_origin"):
        raise ValueError("trajectory replay model-browser origins differ from qualified definitions")
    bridge["original_contract_task_groups"] = {
        source: sorted(row["task_id"] for row in rows
                       if row["source"]["kind"] == source
                       and row["verification"]["contract"]["kind"] == "official_task_contract")
        for source in cross
    }


def _load_trajectory_replay_bridge(
    profile: dict[str, Any], *, selection_sha256: str, task_count: int
) -> dict[str, Any] | None:
    """Load the hash-bound cross-track control without treating it as an Agent score."""

    config = profile.get("trajectory_replay_bridge") or {}
    if not config:
        return None
    source = Path(str(config.get("source_path") or ""))
    expected_hash = str(config.get("source_sha256") or "")
    if source.is_symlink() or not source.is_file() or optional_file_sha256(source) != expected_hash:
        raise ValueError("trajectory replay bridge is missing or is not hash-bound by the profile")
    bridge = _read_json(source)
    if bridge.get("schema") != "lex_browser_use_trajectory_replay_bridge/1":
        raise ValueError("unsupported trajectory replay bridge schema")
    if bridge.get("selection_sha256") != selection_sha256:
        raise ValueError("trajectory replay bridge uses a different task selection")
    trajectory_profile = bridge.get("trajectory_profile") or {}
    trajectory_profile_path = Path(str(trajectory_profile.get("path") or ""))
    if (
        trajectory_profile_path.is_symlink()
        or not trajectory_profile_path.is_file()
        or optional_file_sha256(trajectory_profile_path) != trajectory_profile.get("sha256")
    ):
        raise ValueError("trajectory replay profile is missing or is not hash-bound")
    _validate_replay_contract_definitions(bridge)
    result = bridge.get("effective_results") or {}
    measured = _replay_results(bridge)
    # The bound profile states which browsers ran; the results are what it checks.
    registered = {
        str(method.get("engine") or "")
        for method in _read_json(trajectory_profile_path).get("methods") or []
    }
    if {key for key, value in result.items() if isinstance(value, dict)} != registered or (
        set(measured) != registered
    ):
        raise ValueError("trajectory replay results differ from the browsers in the bound profile")
    if not all(_replay_counts_are_valid(value) for value in measured.values()):
        raise ValueError("trajectory replay bridge has an invalid current control cardinality")
    replay_engine, provenance = _replay_engine_field(bridge, "_trajectory_provenance")
    reviewed_engine, review = _replay_engine_field(bridge, "_failure_review")
    source_tasks = {
        source: sum(counts.values())
        for source, counts in (bridge.get("source_contract_counts") or {}).items()
    }
    failures = {
        engine: value["tasks"] - value["passes"] for engine, value in measured.items()
    }
    if (
        task_count < 1
        or replay_engine not in measured
        or reviewed_engine not in measured
        or int(result.get("logical_results") or 0) != task_count * len(measured)
        or any(value["tasks"] != task_count for value in measured.values())
        or any(count != 0 for engine, count in failures.items() if engine != reviewed_engine)
        or int(provenance.get("expert_repair_spec") or 0) != source_tasks.get("expert_repair_spec")
        or int(provenance.get("successful_browser_use_execution") or 0)
        != source_tasks.get("authoritative_successful_browser_use_execution")
    ):
        raise ValueError("trajectory replay bridge has an invalid current control cardinality")
    review_path = Path(str(review.get("path") or ""))
    if (
        review_path.is_symlink()
        or not review_path.is_file()
        or optional_file_sha256(review_path) != review.get("sha256")
        or int(review.get("failed_results") or 0) != failures[reviewed_engine]
        or int(review.get("verified_results") or 0) != failures[reviewed_engine]
    ):
        raise ValueError("trajectory replay browser-failure review is missing or inconsistent")
    review_data = _read_json(review_path)
    responsibilities: Counter[str] = Counter()
    reviewed_keys: set[tuple[str, int]] = set()
    for group in review_data.get("cause_groups") or []:
        for item in group.get("results") or []:
            key = (str(item.get("engine") or ""), int(item["task_id"]))
            if key in reviewed_keys or key[0] != reviewed_engine:
                raise ValueError("trajectory replay responsibility inventory is inconsistent")
            reviewed_keys.add(key)
            responsibilities[str(group.get("responsibility") or "")] += 1
    if (
        len(reviewed_keys) != review["verified_results"]
        or review_data.get("coverage") != {
            "failed_results": review["failed_results"], "verified_results": len(reviewed_keys)
        }
        or (review_data.get("bindings") or {}).get("effective_results_sha256")
        != result.get("sha256")
        or dict(responsibilities) != review.get("responsibility_counts")
    ):
        raise ValueError("trajectory replay responsibility counts differ from the bound review")
    return bridge


def _replay_original_contract_rows(
    bridge: dict[str, Any], attempts: list[dict[str, Any]], participants: Participants
) -> list[list[str]]:
    groups = bridge["original_contract_task_groups"]
    historical = set(groups["authoritative_successful_browser_use_execution"])
    expert = set(groups["expert_repair_spec"])
    populations = (historical | expert, historical, expert)
    indexed = {(row["model"], row["engine"], int(row["task_id"])): row for row in attempts}
    if len(indexed) != len(attempts):
        raise ValueError("replay diagnostic requires authoritative logical results")
    rows = []
    engines = participants.engines_baseline_first
    for model in participants.models:
        for engine in engines:
            cells = [f"{participants.models[model]} × {participants.engines[engine]}"]
            for population in populations:
                keys = [(model, engine, task_id) for task_id in population]
                if any(key not in indexed for key in keys):
                    raise ValueError("replay diagnostic is missing registered configuration results")
                passes = sum(float(indexed[key].get("score") or 0) == 1 for key in keys)
                cells.append(f"{passes}/{len(population)}")
            rows.append(cells)
    return rows


def _trajectory_replay_control(
    bridge: dict[str, Any] | None,
    participants: Participants,
    attempts: list[dict[str, Any]] | None = None,
) -> str:
    """Explain the selected replay comparison without inheriting a past article."""
    if bridge is None:
        return ""

    result_rows = [
        [str(value.get("label") or key.capitalize()), f"{value['passes']}/{value['tasks']}"]
        for key, value in _replay_results(bridge).items()
    ]
    _, provenance = _replay_engine_field(bridge, "_trajectory_provenance")
    source_labels = {
        "authoritative_successful_browser_use_execution": _translated("trajectory_replay_control.historical_successful_executions", "Historical successful executions"),
        "expert_repair_spec": _translated("trajectory_replay_control.expert_repaired_trajectories", "Expert-repaired trajectories"),
    }
    source_rows = [
        [source_labels["authoritative_successful_browser_use_execution"], provenance["successful_browser_use_execution"]],
        [source_labels["expert_repair_spec"], provenance["expert_repair_spec"]],
        *[[Safe(f'<span data-origin="{_e(origin)}">{_e(origin.replace("_", " × "))}</span>'), count]
          for origin, count in provenance["successful_browser_use_execution_by_origin"].items()],
    ]
    contract_rows = [
        [source_labels.get(source, source.replace("_", " ")), counts["official_task_contract"], counts["corrected_replay_contract"]]
        for source, counts in bridge["source_contract_counts"].items()
    ]
    body = '<section id="trajectory-replay-control"><h2>' + _translated("trajectory_replay_control.fixed_trajectory_control", "Fixed-trajectory control") + '</h2><p>' + _translated("trajectory_replay_control.replay_executes_fixed_actions_and_evaluates", "Replay executes fixed actions and evaluates the recorded_response and current HAR. It does not measure autonomous Agent planning; an accepted recorded answer does not establish a fresh reading. Contracts requiring network events still inspect the current HAR.") + '</p>'
    body += _table([_translated("trajectory_replay_control.configuration", "Configuration"), _translated("trajectory_replay_control.passes_tasks", "Passes / tasks")], result_rows)
    body += '<h3>' + _translated("trajectory_replay_control.trajectory_provenance", "Trajectory provenance") + '</h3>'
    body += _table([_translated("trajectory_replay_control.source", "Source"), _translated("trajectory_replay_control.tasks", "Tasks")], source_rows)
    body += '<h3>' + _translated("trajectory_replay_control.trajectory_source_and_acceptance_contract", "Trajectory source and acceptance contract") + '</h3>'
    body += _table([_translated("trajectory_replay_control.source", "Source"), _translated("trajectory_replay_control.original_official_contract", "Original official contract"), _translated("trajectory_replay_control.revised_contract", "Revised contract")], contract_rows)
    if attempts is not None:
        body += '<p>' + _translated("trajectory_replay_control.the_following_post_hoc_diagnostic_uses", "The following post-hoc diagnostic uses original-contract tasks. It does not replace the full autonomous-run population or official outcomes.") + '</p>'
        body += _table([_translated("trajectory_replay_control.configuration", "Configuration"), _translated("trajectory_replay_control.all_original_contract_tasks", "All original-contract tasks"), _translated("trajectory_replay_control.historical_success_paths", "Historical-success paths"), _translated("trajectory_replay_control.expert_repaired_paths", "Expert-repaired paths")], _replay_original_contract_rows(bridge, attempts, participants))
    body += '<p class="source-note"><a href="evidence-viewer.html?src=data/trajectory-replay-bridge.json">' + _translated("trajectory_replay_control.cross_track_control_evidence", "Cross-track control evidence") + '</a>'
    if _replay_engine_field(bridge, "_failure_review")[1]:
        body += ' · <a href="evidence-viewer.html?src=data/trajectory-replay-failure-review.json">' + _translated("trajectory_replay_control.replay_failure_review", "Replay failure review") + '</a>'
    return body + '</p></section>'


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
        ("network", t("automatic_failure_figure.only_the_network_side_effect_rule", "Only the network-side-effect rule failed"), t("automatic_failure_figure.the_evaluator_did_not_find_the", "The evaluator did not find the required request or observable side effect")),
        ("response", t("automatic_failure_figure.only_the_final_answer_rule_failed", "Only the final-answer rule failed"), t("automatic_failure_figure.the_final_answer_disagreed_with_the", "The final answer disagreed with the structured answer contract")),
        ("mixed", t("automatic_failure_figure.both_rule_classes_failed", "Both rule classes failed"), t("automatic_failure_figure.neither_the_final_answer_nor_the", "Neither the final answer nor the network side effect satisfied the contract")),
    )
    cards = "".join(
        f'<article><strong>{counts[key]}</strong><b>{_e(label)}</b><p>{_e(description)}</p></article>'
        for key, label, description in items
    )
    return ('<div class="verdict-strip" aria-label="' + t("automatic_failure_figure.failed_rule_combinations_from_the_official", "Failed rule combinations from the official evaluator") + '">') + cards + "</div>"


def _article_body(
    attempts: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    verified_causes: dict[str, dict[str, Any]],
    review: dict[str, Any],
    automatic_failure_counts: Counter[str],
    trajectory_replay_bridge: dict[str, Any] | None,
    participants: Participants,
) -> str:
    task_count = len(tasks)
    configurations = {(row['model'], row['engine']) for row in attempts}
    configuration_count = len(configurations)
    expected_count = task_count * configuration_count
    complete = {(row['model'], row['engine'], int(row['task_id'])) for row in attempts} == {
        (model, engine, int(task['task_id'])) for model, engine in configurations for task in tasks}
    scope = t("article_body.complete_results", "Complete results") if complete else t("article_body.recorded_results", "Recorded results")
    excluded = _external_interference_task_ids(verified_causes)
    eligible = [row for row in attempts if int(row['task_id']) not in excluded]
    clean_count = task_count - len(excluded)
    failed = [row for row in attempts if float(row.get('score') or 0) < 1]
    summary = _primary_summary(attempts, participants)
    pairs = _paired_summary(attempts, participants)
    comparison_rows = [
        [participants.models[model], participants.engines[item['baseline_engine']],
         participants.engines[item['comparison_engine']], item['paired'], item['both_pass'],
         item['baseline_only'], item['comparison_only'], item['both_fail']]
        for model, group in pairs.items() for item in group['comparisons'].values()
    ]
    clean_rows = [
        [participants.models[model], participants.engines[engine], f"{value['passed']}/{value['completed']}", _pct(value['score'])]
        for model, group in _primary_summary(eligible, participants).items() for engine, value in group.items()
    ]
    chapters = []
    for model, engine in sorted(configurations):
        failures = [row for row in failed if row['model'] == model and row['engine'] == engine]
        if not failures:
            continue
        ids = {_attempt_id(row) for row in failures}
        causes = {key: value for key, value in verified_causes.items() if key in ids}
        label = f'{participants.models[model]} × {participants.engines[engine]}'
        specimen = next(iter(causes), _attempt_id(failures[0]))
        cause = causes.get(specimen) or {}
        reason = cause.get('reason') or {}
        explanation = bilingual_text(
            str(reason.get('zh') or _ZH('article_body.no_verified_cause', 'This result has no verified root cause.')),
            str(reason.get('en') or _EN('article_body.no_verified_cause', 'This result has no verified root cause.')),
        )
        chapters.append(
            f'<section data-browser-failure-profile="{_e(model)}-{_e(engine)}"><h3>{_e(label)}</h3>'
            + _translated("article_body.failed_results_have_a_verified_cause", "{failures_count} failed results; {causes_count} have a verified cause.", failures_count=len(failures), causes_count=len(causes))
            + _failure_cause_table(causes)
            + f'<p><a href="results/{_e(specimen)}.html#verification">{_e(specimen)}</a> · {explanation}</p></section>'
        )
    common_count, resource_rows = _common_final_success_resource_rows(attempts, participants)
    coverage = review.get('coverage') or {}
    reviewed = int(coverage.get('reviewed_attempts') or 0)
    recorded = int(coverage.get('recorded_attempts') or 0)
    return (
        '<article class="publication-article interpretation-v2"><header class="essay-head"><div class="eyebrow">Browser Use</div><h1>'
        + _translated("article_body.browser_use_results_across_declared_tasks", "Browser Use results across {task_count} declared tasks", task_count=task_count, scope=scope)
        + '</h1><p class="lead">'
        + _translated("article_body.the_declared_scope_contains_tasks_and", "The declared scope contains {task_count} tasks and {configuration_count} configurations; {attempts_count}/{expected_count} logical results are recorded. Official evaluator results remain unchanged. A separate {clean_count}-task diagnostic subset excludes {excluded_count} tasks with verified external interference.", task_count=task_count, configuration_count=configuration_count, attempts_count=len(attempts), expected_count=expected_count, excluded_count=len(excluded), clean_count=clean_count)
        + ('</p><p class="page-links"><a href="datasets/">' + t("article_body.view_dataset", "View dataset") + '</a><a href="analysis/">' + t("article_body.inspect_all_results", "Inspect all results") + '</a><a href="data/interpretation-evidence.json" download>' + t("article_body.download_claims_and_evidence", "Download claims and evidence") + "</a></p></header><section><h2>")
        + _translated("article_body.core_metrics_across_tasks", "Core metrics across {task_count} tasks", task_count=task_count) + '</h2>'
        + _table([t("resource_comparison_table.model", "Model"), t("resource_comparison_table.browser", "Browser"), t("article_body.tasks_passed", "Tasks passed"), t("article_body.pass_rate", "Pass rate"), t("article_body.agent_total_time", "Agent total time"), t("article_body.browsertool_latency", "BrowserTool latency"), 'Tokens'],
                 _summary_comparison_rows(summary, participants), table_id='home-core-metrics', classes='core-comparison-table')
        + '<p class="source-note">' + _translated("article_body.every_configuration_uses_the_same_task", "Every configuration uses the same task scope; result pages retain all physical executions.") + '</p></section>'
        + ("<section><h2>" + t("article_body.compare_browsers_task_by_task", "Compare browsers task by task") + "</h2>")
        + _table([t("resource_comparison_table.model", "Model"), t("article_body.browser_a", "Browser A"), t("article_body.browser_b", "Browser B"), t("article_body.matched_tasks", "Matched tasks"), t("article_body.both_passed", "Both passed"), t("article_body.a_passed_b_failed", "A passed, B failed"), t("article_body.a_failed_b_passed", "A failed, B passed"), t("article_body.both_failed", "Both failed")], comparison_rows)
        + _trajectory_replay_control(trajectory_replay_bridge, participants, attempts) + '</section>'
        + ('<section id="failure-findings"><h2>' + t("article_body.programmatic_failure_location_and_reviewed_root", "Programmatic failure location and reviewed root cause") + "</h2>")
        + _translated("article_body.programmatic_findings_identify_failed_rules_cause", "Programmatic findings identify failed rules; cause reviews explain the evidence-supported cause. They serve different purposes.")
        + _automatic_failure_figure(automatic_failure_counts) + _failure_scope_figure(verified_causes)
        + ''.join(chapters) + '<h3>' + _translated("article_body.official_results_and_post_hoc_diagnostics", "Official results and post-hoc diagnostics") + '</h3>'
        + _table([t("resource_comparison_table.model", "Model"), t("resource_comparison_table.browser", "Browser"), bilingual_text(t("article_body.passes_on_tasks", "Passes on {clean_count} tasks", clean_count=clean_count), f'Passes across {clean_count} tasks'), t("article_body.pass_rate", "Pass rate")], clean_rows,
                 table_id='home-qualified-results', classes='core-comparison-table')
        + '<p class="source-note">' + _translated("article_body.the_diagnostic_subset_does_not_replace", "The diagnostic subset does not replace official scores. Original failure evidence remains available.") + '</p></section>'
        + ("<section><h2>" + t("article_body.review_coverage_and_boundaries", "Review coverage and boundaries") + '</h2><div class="audit-ladder"><article><h3>' + t("article_body.evidence_integrity", "Evidence integrity") + "</h3>")
        + _translated("article_body.integrity_checks_cover_physical_execution_evidence", "Integrity checks cover {reviewed}/{recorded} physical-execution evidence bundles.", reviewed=reviewed, recorded=recorded)
        + ("</article><article><h3>" + t("article_body.official_programmatic_evaluation", "Official programmatic evaluation") + "</h3>")
        + _translated("article_body.each_task_checks_the_final_answer", "Each task checks the final answer and declared network effects, without an LLM judge.")
        + ("</article><article><h3>" + t("article_body.failure_root_cause_review", "Failure root-cause review") + "</h3>")
        + _translated("article_body.bound_cause_reviews_cover_failed_logical", "Bound cause reviews cover {verified_causes_count}/{failed_count} failed logical results.", verified_causes_count=len(verified_causes), failed_count=len(failed))
        + '</article></div><p>' + _translated("article_body.passed_results_have_not_received_an", "Passed results have not received an independent blind semantic review.") + ('</p><a href="review/">' + t("article_body.view_review_records", "View review records →") + "</a></section>")
        + '<section><h2>' + _translated("article_body.latency_and_browser_resources_across_tasks", "Latency and browser resources across {task_count} tasks", task_count=task_count) + '</h2>'
        + _translated("article_body.mean_p50_and_p95_compare_all", "Mean, P50 and P95 compare all {configuration_count} configurations. CPU, memory and network cover the browser, excluding the Agent and model service.", configuration_count=configuration_count)
        + _resource_comparison_table(summary, participants)
        + _translated("article_body.minimum_markers_describe_the_recorded_configuration", "Minimum markers describe the recorded configuration; they do not establish resource efficiency at a common viewport.")
        + ("</section><section><h2>" + t("article_body.performance_and_resources_on_common_final", "Performance and resources on common final successes") + "</h2>")
        + _translated("article_body.all_configurations_finally_passed_tasks_the", "All configurations finally passed {common_count} tasks. The table computes P50 over all physical executions within each logical result.", common_count=common_count)
        + _table([t("resource_comparison_table.model", "Model"), t("resource_comparison_table.browser", "Browser"), t("article_body.agent_path_p50", "Agent path P50"), 'BrowserTool P50', 'Tokens P50', t("article_body.peak_memory_p50", "Peak memory P50"), t("article_body.cpu_time_p50", "CPU time P50")], resource_rows,
                 table_id='home-resource-table', classes='core-comparison-table')
        + _translated("article_body.the_common_success_subset_retains_each", "The common-success subset retains each configuration’s recorded environment; it does not establish resource efficiency under identical conditions.")
        + '</section></article>'
    )


def _filters(participants: Participants, *, attempts: bool = False, dataset: bool = False) -> str:
    engine_options = "".join(
        f'<option value="{_e(engine)}">{_e(label)}</option>'
        for engine, label in participants.engines.items()
    )
    extra = (
        ("<label>" + t("resource_comparison_table.model", "Model") + ' <select data-filter="model"><option value="">' + t("same_task_comparison.all", "All") + "</option>")
        + "".join(
            f'<option value="{_e(model)}">{_e(label)}</option>'
            for model, label in participants.models.items()
        )
        + "</select></label>"
        + ("<label>" + t("resource_comparison_table.browser", "Browser") + ' <select data-filter="engine"><option value="">' + t("same_task_comparison.all", "All") + f"</option>{engine_options}</select></label><label>" + t("filters.failure_stage", "Failure stage") + ' <select data-filter="failure"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="none">' + t("filters.none", "None") + '</option><option value="response">' + t("filters.final_answer", "Final answer") + '</option><option value="network">' + t("filters.network_side_effect", "Network side effect") + '</option><option value="mixed">' + t("filters.multiple_stages", "Multiple stages") + "</option></select></label>")
        if attempts
        else ""
    )
    result_filter = (
        ""
        if dataset
        else (
            "<label>" + t("filters.result", "Result") + ' <select data-filter="result"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="pass">' + t("same_task_comparison.pass", "Pass") + f"</option><option value=\"fail\"{' selected' if attempts else ''}>" + t("same_task_comparison.fail", "Fail") + "</option></select></label>"
        )
    )
    placeholder = t("filters.task_intent_or_acceptance_contract", "Task, intent, or acceptance contract") if dataset else t("filters.task_intent_answer_or_error", "Task, intent, answer, or error")
    return (
        ('<div class="filters"><label>' + t("filters.full_text_search", "Full-text search") + f' <input data-search type="search" placeholder="{placeholder}"></label><label>' + t("same_task_comparison.tier", "Tier") + ' <select data-filter="tier"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="easy">' + t("tier_label.easy", "Easy") + '</option><option value="medium">' + t("tier_label.medium", "Medium") + '</option><option value="hard">' + t("tier_label.hard", "Hard") + "</option></select></label><label>" + t("same_task_comparison.site", "Site") + ' <select data-filter="site"><option value="">' + t("same_task_comparison.all", "All") + '</option><option value="shopping">Shopping</option><option value="shopping_admin">Shopping Admin</option><option value="reddit">Reddit</option><option value="gitlab">GitLab</option></select></label><label>' + t("same_task_comparison.type", "Type") + ' <select data-filter="kind"><option value="">' + t("same_task_comparison.all", "All") + "</option><option>RETRIEVE</option><option>NAVIGATE</option><option>MUTATE</option></select></label>")
        + result_filter
        + extra
        + '<span class="count-note" data-visible-count></span></div>'
    )


def _task_table(tasks: list[dict[str, Any]], participants: Participants) -> str:
    rows = []
    for task in tasks:
        task_id, site, kind = int(task["task_id"]), task["sites"][0], task["task_type"]
        values = [
            Safe(f'<a href="datasets/{task_id}.html">#{task_id}</a>'),
            _tier_label(task["tier"]),
            SITE_LABELS.get(site, site),
            kind,
            task["estimated_min_steps"],
            _rationale(task),
            task.get("intent") or "—",
            t("task_table.yes", "Yes") if task["official_hard"] else t("task_table.no", "No"),
            Safe(f'<a href="datasets/{task_id}.html">' + t("task_table.view_task_contract", "View task contract") + "</a>"),
        ]
        cells = "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in values)
        search = " ".join(
            str(x)
            for x in (
                task_id,
                task.get("intent"),
                _searchable_rationale(task),
                json.dumps(task.get("contract") or {}, ensure_ascii=False),
            )
        )
        rows.append(
            f'<tr data-tier="{task["tier"]}" data-site="{site}" data-kind="{kind}" data-search="{_e(search)}">{cells}</tr>'
        )
    labels = [
        t("same_task_comparison.task", "Task"),
        t("same_task_comparison.tier", "Tier"),
        t("same_task_comparison.site", "Site"),
        t("task_table.family", "Family"),
        t("task_table.minimum_steps", "Minimum steps"),
        t("task_table.selection_rationale", "Selection rationale"),
        t("task_table.official_task_intent", "Official task intent"),
        t("task_table.official_hard_subset", "Official hard subset"),
        t("task_table.task_contract", "Task contract"),
    ]
    headers = "".join(
        f'<th><button class="sort-button" onclick="sortTable(\'task-table\',{i})">{_e(x)} ↕</button></th>'
        for i, x in enumerate(labels)
    )
    return (
        _filters(participants, dataset=True)
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
        (f'<dialog class="diagnosis-dialog" id="{_e(dialog_id)}" aria-labelledby="{_e(dialog_id)}-title"><div class="diagnosis-dialog-head"><div><span class="table-kicker">' + t("diagnosis_dialog.failure_diagnosis", "Failure diagnosis") + f'</span><h2 id="{_e(dialog_id)}-title">Task #{task_id} · {_e(configuration)}</h2></div><button class="dialog-close" type="button" onclick="closeDiagnosis(\'{_e(dialog_id)}\')" aria-label="' + t("diagnosis_dialog.close", "Close") + '">×</button></div><div class="diagnosis-dialog-grid"><div><b>' + t("diagnosis_dialog.location", "Location") + f'</b><a href="{_e(step_link)}">{_e(failure_point)}</a></div><div><b>' + t("diagnosis_dialog.program_verdict", "Program verdict") + f"</b>{_e(automatic_failure or t('failure_cause_table.unclassified', 'Unclassified'))}</div><div><b>" + t("diagnosis_dialog.reviewed_root_cause", "Reviewed root cause") + f"</b>{_e(subcategory or t('diagnosis_dialog.awaiting_review', 'Awaiting review'))}</div><div><b>" + t("diagnosis_dialog.owning_scope", "Owning scope") + f"</b>{_e(owner)}</div></div>")
        + (
            f'<div class="dialog-step"><b>{_e(step_context_label)}</b><a href="{_e(step_link)}">{_raw(step_context)}</a></div>'
            if step_context
            else ""
        )
        + ('<section class="dialog-error"><h3>' + t("diagnosis_dialog.program_error", "Program error") + f'</h3><pre class="log-view">{_e(evaluator_error)}</pre></section><section class="dialog-review"><h3>' + t("diagnosis_dialog.reviewed_conclusion", "Reviewed conclusion") + f"</h3><p>{_e(reason or t('diagnosis_dialog.root_cause_review_is_not_complete', 'Root-cause review is not complete.'))}</p><p><strong>" + t("diagnosis_dialog.treatment", "Treatment:") + f'</strong>{_e(action)}</p></section><div class="dialog-actions"><a class="button-link" href="{_e(detail_url)}#verification">' + t("diagnosis_dialog.view_expected_and_actual_values", "View expected and actual values") + f'</a><a href="{_e(step_link)}">' + t("diagnosis_dialog.view_trajectory_near_the_failure", "View trajectory near the failure") + f'</a><a href="{_e(detail_url)}#screenshots">' + t("diagnosis_dialog.view_page_screenshots", "View page screenshots") + f'</a><a href="{_e(detail_url)}#runtime">' + t("diagnosis_dialog.view_logs_and_complete_evidence", "View logs and complete evidence") + f'</a><button type="button" onclick="closeDiagnosis(\'{_e(dialog_id)}\')">' + t("diagnosis_dialog.close", "Close") + "</button></div></dialog>")
    )


def _attempt_table(
    attempts: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    participants: Participants,
    verified_causes: dict[str, dict[str, Any]] | None = None,
) -> str:
    verified_causes = verified_causes or {}
    task_meta = {int(task["task_id"]): task for task in tasks}
    rows = []
    dialogs = []
    for row in attempts:
        p = _public_attempt(row)
        result = "pass" if p["score"] == 1 else "fail"
        result_record = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        failure, automatic_failure = _automatic_failure_type(result_record)
        subcategory, reason = localized_cause(verified_causes, p["id"], _language())
        code = _cause_code(verified_causes, p["id"])
        event_record = read_public_json(Path((row.get("paths") or {}).get("events", "")))
        steps = trajectory(event_record if isinstance(event_record, list) else [])
        failure_point, step_number = _failure_point(code, steps)
        step_context_label, step_context = _trajectory_context(steps, step_number, failure_point, t)
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
                    configuration=f"{participants.models[p['model']]} · {participants.engines[p['engine']]}",
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
                f'<strong>{_e(participants.models[p["model"]])}</strong><span class="table-kicker">'
                f"{_e(participants.engines[p['engine']])}</span>"
            ),
            Safe(f'<a href="datasets/{p["task_id"]}.html">#{p["task_id"]}</a>'),
            _status(row),
            Safe(f'<a href="{point_link}">{_e(failure_point)}</a>') if result == "fail" else "—",
            automatic_failure or "—",
            subcategory or t("diagnosis_dialog.awaiting_review", "Awaiting review") if result == "fail" else "—",
            Safe(f'<strong>{_e(owner)}</strong><span class="table-kicker">{_e(action)}</span>')
            if result == "fail"
            else "—",
            Safe(
                (
                    (f'<button class="diagnosis-button" type="button" onclick="openDiagnosis(\'{_e(dialog_id)}\')">' + t("attempt_table.open_diagnosis", "Open diagnosis") + "</button>")
                )
                if result == "fail"
                else (f"<a href=\"results/{p['id']}.html\">" + t("attempt_table.details", "Details") + "</a>")
            ),
        ]
        cells = "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in values)
        rows.append(
            f'<tr data-tier="{p["tier"]}" data-site="{(task.get("sites") or [""])[0]}" '
            f'data-kind="{task.get("task_type") or "UNKNOWN"}" data-result="{result}" '
            f'data-model="{p["model"]}" data-engine="{p["engine"]}" data-failure="{failure}" '
            f'data-search="{_e(" ".join([p["id"], str(task.get("intent") or ""), automatic_failure, subcategory, reason, _failure_diagnosis(result_record, row)]))}">{cells}</tr>'
        )
    headers = "".join(
        f"<th>{_e(x)}</th>"
        for x in [
            t("filters.result", "Result"),
            t("attempt_table.run_configuration", "Run configuration"),
            t("same_task_comparison.task", "Task"),
            t("attempt_table.verdict", "Verdict"),
            t("attempt_table.failure_stage", "Failure stage"),
            t("attempt_table.automatic_failure_locator", "Automatic failure locator"),
            t("diagnosis_dialog.reviewed_root_cause", "Reviewed root cause"),
            t("attempt_table.owner_and_treatment", "Owner and treatment"),
            t("attempt_table.evidence", "Evidence"),
        ]
    )
    return (
        _filters(participants, attempts=True)
        + f'<div class="table-wrap"><table id="result-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        + "".join(dialogs)
    )


def _release_details(release: dict[str, Any]) -> str:
    rows = _record_text(release, "changes") or []
    details = (
        ("<details><summary>" + t("release_details.view_findings_treatment_and_impact", "View findings, treatment, and impact") + "</summary>")
        + _table(
            [t("release_details.item", "Item"), t("release_details.issue_found", "Issue found"), t("release_details.formal_treatment", "Formal treatment"), t("release_details.result_impact_and_limits", "Result impact and limits")],
            rows,
            table_id="change-table",
        )
        + "</details>"
        if rows
        else ""
    )
    return (
        f"<div class=\"eyebrow\">{_e(release.get('date') or t('common.not_recorded', 'Not recorded'))}</div><h3>{_e(_record_text(release, 'title') or t('release_details.untitled_release', 'Untitled release'))}</h3><p>{_e(_record_text(release, 'summary') or '')}</p>{details}"
    )


def _release_history(releases: list[dict[str, Any]], history_scope: str) -> str:
    if not releases:
        return ("<p>" + t("release_history.no_release_has_been_recorded", "No release has been recorded.") + "</p>")
    latest, *older = releases
    body = _section(
        t("release_history.current_release", "Current release"),
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
            t("release_history.earlier_releases", "Earlier releases"),
            ('<details class="release-history"><summary>' + t("release_history.expand_earlier_releases", "Expand {older_count} earlier releases", older_count=len(older)) + f"</summary>{entries}</details>"),
        )
    body += _section(
        t("release_history.scope_and_downloads", "Scope and downloads"),
        (f'<p>{_e(history_scope)}</p><p class="page-links"><a href="report-changes.json" download>' + t("release_history.download_structured_release_history", "Download structured release history") + '</a><a href="https://github.com/lexmount/browser-eval/commits/main">' + t("release_history.view_repository_changes", "View repository changes") + "</a></p>"),
    )
    return body


def _review_history(rounds: list[dict[str, Any]]) -> str:
    if not rounds:
        return ("<p>" + t("review_history.no_review_records", "No review records.") + "</p>")
    cards = []
    for item in rounds:
        fields = "".join(
            f"<div><b>{_e(label)}</b><p>{_e(_record_text(item, key) or '—')}</p></div>"
            for label, key in (
                (t("review_history.scope", "Scope"), "scope"),
                (t("review_history.findings", "Findings"), "findings"),
                (t("review_history.treatment", "Treatment"), "corrections"),
                (t("review_history.remaining_items", "Remaining items"), "remaining"),
            )
        )
        cards.append(
            f"<article class=\"review-round\" id=\"{_e(item.get('id') or '')}\"><header><div><div class=\"eyebrow\">{_e(item.get('date') or t('common.not_recorded', 'Not recorded'))}</div><h3>{_e(_record_text(item, 'title') or t('review_history.untitled_review', 'Untitled review'))}</h3></div><span class=\"review-status\">{_e(_record_text(item, 'status') or t('common.not_recorded', 'Not recorded'))}</span></header><div class=\"review-fields\">{fields}</div></article>"
        )
    return "".join(cards)


def _load_fix_log(profile: dict[str, Any]) -> dict[str, Any]:
    """Load the shared fix-log model with Browser Use publication sanitization."""

    from harness.fix_log import load_fix_log

    return load_fix_log(profile, sanitize_value=sanitize)


def _write_fix_log(stage: Path, model: dict[str, Any]) -> None:
    """Render one edition from the manifest; screenshots never imply pixel change."""
    lang = _language()
    folder = stage / "fixes"
    folder.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for item in model["artifacts"]:
        suffix = ".json" if item["kind"] == "receipt" else item["extension"]
        relative = f'artifacts/fixes/{item["sha256"]}{suffix}'
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item["path"], destination)
        if optional_file_sha256(destination) != item["sha256"]:
            raise ValueError("fix artifact changed during materialization")
        artifacts[item["id"]] = {**{k: v for k, v in item.items() if k != "path"}, "href": relative}

    def link(identifier: str, label: str) -> str:
        return f'<a href="{_e(artifacts[identifier]["href"])}">{_e(label)}</a>'

    def save(name: str, title: str, body: str) -> None:
        page = _page(title=title, active="fixes", body=body, depth=1)
        (folder / name).write_text(page, encoding="utf-8")

    title = t("nav.fix_log", "Fix log")
    intro = t("fix_log.fix_evidence_is_separate_from_historical", "Fix evidence is separate from historical scores: root causes, changes, and validation limits do not replace official results.")
    rows = []
    for record in model["records"]:
        scope = t("fix_log.diagnostic_validation", "Diagnostic validation") if record["scope"] == "diagnostic" else t("fix_log.formal_scope_validation", "Formal-scope validation")
        rows.append([Safe(f'<a href="fixes/{record["id"]}.html">{_e(record["title"][lang])}</a>'), scope])
        body = _hero("Fix evidence", record["title"][lang], scope)
        for key, label in (("symptom", t("fix_log.symptom", "Symptom")), ("root_cause", t("fix_log.code_and_process_root_cause", "Code and process root cause")), ("principle", t("fix_log.fix_principle", "Fix principle"))):
            body += _section(label, f'<p>{_e(record[key][lang])}</p>')
        body += _section(t("fix_log.code_locations", "Code locations"), "".join(
            f'<p><code>{_e(code["repository"])} · {_e(code["path"])} · {_e(code["symbol"])}</code></p>' for code in record["code_paths"]
        ))
        basis = record["comparison_basis"]
        if basis["kind"] == "identical_inputs":
            comparison = (
                f"<p>{t('fix_log.shared_input_sha_256', 'Shared input SHA-256')}: <code>{record['input_sha256']}</code></p>"
            )
        else:
            comparison = (
                f"<p class=\"callout\">{t('fix_log.the_acceptance_contract_is_unchanged_the', 'The acceptance contract is unchanged; the trajectory was revised.')}</p><p>{t('fix_log.task_contract_sha_256', 'Task contract SHA-256')}: <code>{basis['task_contract_sha256']}</code></p>"
                + _table(
                    [t("fix_log.binding", "Binding"), t("fix_log.before", "Before"), t("fix_log.after", "After")],
                    [
                        [t("fix_log.input_sha_256", "Input SHA-256"), Safe(f'<code>{record["before"]["input_sha256"]}</code>'), Safe(f'<code>{record["after"]["input_sha256"]}</code>')],
                        [t("fix_log.trajectory_sha_256", "Trajectory SHA-256"), Safe(f'<code>{record["before"]["trajectory_sha256"]}</code>'), Safe(f'<code>{record["after"]["trajectory_sha256"]}</code>')],
                        [t("fix_log.action_count", "Action count"), record["before"]["action_count"], record["after"]["action_count"]],
                    ],
                )
            )
        body += _section(t("fix_log.before_and_after", "Before and after"), comparison)
        for side, side_label in (("before", t("fix_log.before", "Before")), ("after", t("fix_log.after", "After"))):
            run = record[side]
            content = f'<p>{_e(run["observation"][lang])}</p><pre>{_e(json.dumps({key: run[key] for key in ("method", "version", "source_base", "source_diff_sha256", "binary_sha256", "run_id")}, indent=2))}</pre>'
            if run.get("source_diff_sha256") is None:
                content += f"<p>{t('fix_log.this_build_did_not_record_a', 'This build did not record a source-diff hash. Identity is bound only to the recorded binary; the current source cannot retrospectively establish its build inputs.')}</p>"
            content += link(run["receipt"], t("fix_log.comparison_receipt_json", "Comparison receipt JSON"))
            if run.get("image"):
                artifact = artifacts[run["image"]]
                provenance = artifact["provenance"]
                label = t("fix_log.last_observed_page", "Last observed page") if provenance["observation"] == "last_observed_page" else t("fix_log.exact_error_time_frame", "Exact error-time frame")
                content += f'<figure><a href="{artifact["href"]}"><img loading="lazy" style="max-width:100%;height:auto" src="{artifact["href"]}" alt="{_e(label)}"></a><figcaption>{_e(label)} · {_e(provenance["timing_note"][lang])}</figcaption></figure>'
            else:
                content += f"<p>{t('fix_log.no_public_screenshot_is_available_for', 'No public screenshot is available for this observation.')}</p>"
            body += _section(side_label, content)
        if record["before"].get("image") and record["after"].get("image") and artifacts[record["before"]["image"]]["sha256"] == artifacts[record["after"]["image"]]["sha256"]:
            body += f"<p class=\"callout\">{t('fix_log.the_before_after_screenshots_are_byte', 'The before/after screenshots are byte-identical and do not demonstrate a pixel change; consult the comparison receipts for state or geometry differences.')}</p>"
        body += _section(t("fix_log.validation_tests", "Validation tests"), "".join(
            f"<pre>{_e(test['command'])}</pre><p>{_e(test['result'])} · {link(test['receipt'], t('fix_log.test_receipt_json', 'Test receipt JSON'))}</p>" for test in record["tests"]
        ))
        body += _section(t("fix_log.validation_limits", "Validation limits"), f'<p>{_e(record["limitations"][lang])}</p><p>{intro}</p>')
        if record.get("pr_url"):
            body += f"<p><a href=\"{_e(record['pr_url'])}\">{t('fix_log.change_pr', 'Change PR')}</a></p>"
        body += f"<p><a href=\"fixes/\">{title}</a> · <a href=\"data/fix-log.json\">{t('fix_log.structured_evidence_index', 'Structured evidence index')}</a></p>"
        save(f'{record["id"]}.html', record["title"][lang], body)
    body = _hero("Fix evidence", title, intro)
    body += _table([t("fix_log.fix", "Fix"), t("fix_log.validation_scope", "Validation scope")], rows) if rows else f"<p>{t('fix_log.no_fix_records_with_bound_validation', 'No fix records with bound validation evidence are available.')}</p>"
    save("index.html", title, body)
    write_json(stage / "data/fix-log.json", {**model, "artifacts": list(artifacts.values())}, parents=True)


@dataclass(frozen=True)
class ReportInputs:
    """Loaded and validated evidence that every page and data stage reads."""

    profile: dict[str, Any]
    profile_path: Path
    fix_log: dict[str, Any]
    participants: Participants
    selection: PilotSelection
    trajectory_replay_bridge: dict[str, Any] | None
    bundles: list[dict[str, Any]]
    source_attempts: list[dict[str, Any]]
    config: dict[str, Any]
    review: dict[str, Any]
    protocol: dict[str, Any]
    environment: dict[str, Any]
    attempts: list[dict[str, Any]]
    public_attempts: list[dict[str, Any]]
    by_attempt: dict[tuple[str, str, int], dict[str, Any]]
    attempts_sha256: dict[str, str]
    failed_result_ids: set[str]
    verified_causes: dict[str, dict[str, Any]]
    failure_reviews: dict[str, dict[str, Any]]
    tasks: list[dict[str, Any]]
    task_count: int
    result_count: int
    reasoning_effort: str
    max_agent_steps: str
    site_access_host: str
    preparation_receipt_count: int
    preparation_receipts_complete: bool
    tier_counts: dict[str, int]
    summary_by_model: dict[str, dict[str, dict[str, Any]]]
    paired_by_model: dict[str, Any]
    excluded_task_ids: set[int]
    qualified_attempts: list[dict[str, Any]]
    qualified_tasks: list[dict[str, Any]]
    qualified_summary: dict[str, dict[str, dict[str, Any]]]
    model_names: str
    adoption_evidence: dict[str, Any]


def _merge_review(bundles: list[dict[str, Any]]) -> dict[str, Any]:
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
    return review


def _load_failure_causes(
    profile: dict[str, Any], bundles: list[dict[str, Any]], attempts: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    default_failure_cause_review = load_failure_cause_review(failure_review_path(profile))
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
    return verified_causes, failure_reviews


def _annotate_failure_causes(
    attempts: list[dict[str, Any]],
    public_attempts: list[dict[str, Any]],
    verified_causes: dict[str, dict[str, Any]],
) -> None:
    for source, public in zip(attempts, public_attempts, strict=True):
        evaluation = read_public_json(Path((source.get("paths") or {}).get("evaluation", "")))
        public["automatic_failure_type"] = _automatic_failure_type(evaluation)[1]
        subcategory, reason = localized_cause(verified_causes, public["id"])
        public["verified_failure_subcategory"] = subcategory
        public["verified_failure_reason"] = reason


def _resolve_measured_tasks(
    profile: dict[str, Any], selection: PilotSelection, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    official = (manifest.get("environment") or {}).get("official_evaluator") or {}
    if profile.get("official_dataset"):
        frozen = profile["official_dataset"]
        if frozen.get("sha256") != official.get("data_checksum"):
            raise ValueError("Frozen official dataset does not match the measured task source")
    return _resolve_tasks(
        selection, expected_data_checksum=str(official.get("data_checksum") or "") or None,
        dataset_path=(Path(profile["official_dataset"]["path"]) if profile.get("official_dataset") else None),
    )


def _load_report_inputs(
    evidence: Path | Iterable[Path], selection_path: Path, profile_path: Path | None
) -> ReportInputs:
    if profile_path is None:
        raise ValueError("An explicit report profile is required; pass --profile")
    if profile_path.is_symlink():
        raise ValueError("Symlinked report profile is forbidden")
    profile = _read_json(profile_path)
    fix_log = _load_fix_log(profile)
    participants = Participants.from_profile(profile)
    selection_path = report_selection_path(profile, selection_path)
    selection = load_pilot(selection_path)
    trajectory_replay_bridge = _load_trajectory_replay_bridge(
        profile, selection_sha256=selection.sha256, task_count=len(selection.tasks)
    )
    bundles = _load_evidence_bundles(evidence, participants)
    source_attempts = [row for bundle in bundles for row in bundle["source_attempts"]]
    manifest, config = bundles[0]["manifest"], bundles[0]["config"]
    review = _merge_review(bundles)
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
    verified_causes, failure_reviews = _load_failure_causes(profile, bundles, attempts)
    _annotate_failure_causes(attempts, public_attempts, verified_causes)
    tasks = _resolve_measured_tasks(profile, selection, manifest)
    task_count = len(tasks)
    result_count = len(attempts)
    reasoning_effort = str(config.get("reasoning_effort") or protocol.get("reasoning_effort") or profile.get("reasoning_effort") or t("common.not_recorded", "Not recorded"))
    max_agent_steps = str(
        config.get("max_agent_steps") or protocol.get("max_agent_steps") or t("common.not_recorded", "Not recorded")
    )
    site_access_host = str(
        environment.get("site_access_host") or config.get("site_access_host") or t("common.not_recorded", "Not recorded")
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
    summary_by_model = _primary_summary(attempts, participants)
    paired_by_model = _paired_summary(attempts, participants)
    excluded_task_ids = _external_interference_task_ids(verified_causes)
    qualified_attempts = [
        row for row in attempts if int(row["task_id"]) not in excluded_task_ids
    ]
    qualified_tasks = [
        task for task in tasks if int(task["task_id"]) not in excluded_task_ids
    ]
    qualified_summary = _primary_summary(qualified_attempts, participants)
    return ReportInputs(
        profile=profile,
        profile_path=profile_path,
        fix_log=fix_log,
        participants=participants,
        selection=selection,
        trajectory_replay_bridge=trajectory_replay_bridge,
        bundles=bundles,
        source_attempts=source_attempts,
        config=config,
        review=review,
        protocol=protocol,
        environment=environment,
        attempts=attempts,
        public_attempts=public_attempts,
        by_attempt={(r["model"], r["engine"], int(r["task_id"])): r for r in attempts},
        attempts_sha256=attempts_sha256,
        failed_result_ids=failed_result_ids,
        verified_causes=verified_causes,
        failure_reviews=failure_reviews,
        tasks=tasks,
        task_count=task_count,
        result_count=result_count,
        reasoning_effort=reasoning_effort,
        max_agent_steps=max_agent_steps,
        site_access_host=site_access_host,
        preparation_receipt_count=preparation_receipt_count,
        preparation_receipts_complete=preparation_receipts_complete,
        tier_counts=tier_counts,
        summary_by_model=summary_by_model,
        paired_by_model=paired_by_model,
        excluded_task_ids=excluded_task_ids,
        qualified_attempts=qualified_attempts,
        qualified_tasks=qualified_tasks,
        qualified_summary=qualified_summary,
        model_names="、".join(participants.models.values()),
        adoption_evidence=_read_json(Path("research/browser-use-benchmark-adoption.json")),
    )


def _open_stage(output: Path) -> Path:
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    marker = output / ".generated-report"
    if output.exists() and (
        not marker.is_file() or marker.read_text(encoding="utf-8") != "browser-use-report/2\n"
    ):
        raise ValueError(f"refusing to replace an unmarked report directory: {output}")
    return Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=parent))


def _write_stage_assets(stage: Path) -> None:
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


def _write_home_page(
    stage: Path,
    inputs: ReportInputs,
    automatic_failure_counts: Counter[str],
) -> None:
    participants = inputs.participants
    trajectory_replay_bridge = inputs.trajectory_replay_bridge
    review = inputs.review
    attempts = inputs.attempts
    verified_causes = inputs.verified_causes
    tasks = inputs.tasks
    article = _article_body(
        attempts,
        tasks,
        verified_causes,
        review,
        automatic_failure_counts,
        trajectory_replay_bridge,
        participants,
    )
    home = f'<section class="section"><div class="wrap">{article}</div></section>'
    (stage / "index.html").write_text(
        _page(title=t("home_page.home", "Home"), active="home", body=home), encoding="utf-8"
    )


def _write_changes_page(stage: Path, inputs: ReportInputs) -> dict[str, Any]:
    profile = inputs.profile
    profile_path = inputs.profile_path
    _, release_data = bound_json_asset(
        profile.get("report_history"), root=profile_path.parent)
    releases = release_data.get("browser_use_releases") or []
    changes = _hero(
        "Report releases",
        t("changes_page.changelog", "Changelog"),
        t("changes_page.each_release_records_only_formal_corrections", "Each release records only formal corrections that affect reader interpretation. Effects on conclusions, denominators, latency, and evidence are shown directly, with supporting details available on demand."),
    ) + _release_history(releases, str(_record_text({
        "history_scope": release_data.get("history_scope"),
        "i18n": {"en": {"history_scope": (release_data.get("history_scope_i18n") or {}).get("en")}},
    }, "history_scope") or ""))
    write_json(stage / "report-changes.json", release_data, parents=True)
    (stage / "changes/index.html").write_text(
        _page(title=t("nav.changes", "Changes"), active="changes", body=changes, depth=1), encoding="utf-8"
    )
    return release_data


def _write_dataset_index(stage: Path, inputs: ReportInputs) -> None:
    participants = inputs.participants
    tasks = inputs.tasks
    task_count = inputs.task_count
    tier_counts = inputs.tier_counts
    adoption_evidence = inputs.adoption_evidence
    datasets = _hero(
        "Dataset",
        t("dataset_index.candidate_benchmarks_selection_rationale_and_preregistered", "Candidate benchmarks, selection rationale and {task_count} preregistered tasks", task_count=task_count),
        t("dataset_index.first_separate_final_outcome_evaluation_action", "First separate final-outcome evaluation, action prediction, and model judging. Scores from different variants of one benchmark family are not interchangeable."),
    )
    datasets += _section(
        t("dataset_index.top_three_public_adoption_signals", "Top three public adoption signals"),
        _adoption_top3(adoption_evidence),
    )
    datasets += (
        _section(
            t("dataset_index.mind2web_splits_are_not_easy_medium", "Mind2Web splits are not easy, medium, and hard"),
            ("<p>" + t("dataset_index.original_mind2web_measures_cross_task_cross", "Original Mind2Web measures cross-task, cross-website and cross-domain generalization. Online-Mind2Web has no standard three-tier split; Mind2Web 2 groups short, medium and long tasks by action span. This report preregisters {easy} easy, {medium} medium and {hard} hard tasks by operation length.", easy=tier_counts['easy'], medium=tier_counts['medium'], hard=tier_counts['hard']) + "</p>"),
        )
        + _section(
            t("dataset_index.how_to_read_task_ground_truth", "How to read task ground truth"),
            ("<p><strong>" + t("dataset_index.webarena_verified_ground_truth_is_not", "WebArena-Verified ground truth is not a fixed click script.") + "</strong>" + t("dataset_index.each_task_declares_a_structured_final", "Each task declares a structured final-answer contract and, where required, one or more network-event contracts for page side effects. Every contract must pass for a score of 1. Task pages explain only the contract; browser answers, failed rules, and CowAgent trajectories appear on result pages.") + "</p>"),
        )
        + _section(
            t("dataset_index.dataset_scope", "Dataset scope"),
            ("<p>" + t("dataset_index.all_tasks_were_fixed_before_model", "All {task_count} tasks were fixed before model execution, covering four deployed sites and RETRIEVE, NAVIGATE and MUTATE outcome contracts. This page describes tasks and acceptance rules. Pass rates, timings, trajectories and feasibility observations belong in results analysis.", task_count=task_count) + "</p>"),
        )
        + _section(t("dataset_index.tasks", "{task_count} tasks", task_count=task_count), _task_table(tasks, participants))
    )
    (stage / "datasets/index.html").write_text(
        _page(title=t("nav.dataset", "Dataset"), active="datasets", body=datasets, depth=1), encoding="utf-8"
    )


def _write_task_pages(stage: Path, inputs: ReportInputs) -> None:
    participants = inputs.participants
    environment = inputs.environment
    by_attempt = inputs.by_attempt
    tasks = inputs.tasks
    for task in tasks:
        task_id, site = int(task["task_id"]), task["sites"][0]
        detail = _hero(
            f"Task #{task_id}",
            _rationale(task),
            t("task_pages.official_task_identity_public_intent_and", "Official task identity, public intent, and programmatic acceptance contract. Measured outcomes appear in Results and are kept separate from the dataset definition."),
        )
        detail += _section(
            t("task_pages.task_identity", "Task identity"),
            ('<div class="detail-grid"><div class="kv"><b>' + t("same_task_comparison.site", "Site") + f'</b>{_e(SITE_LABELS.get(site, site))}</div><div class="kv"><b>' + t("task_pages.project_tier", "Project tier") + "</b>" + t("task_pages.minimum_steps", "{tier} · {estimated_min_steps} minimum steps", tier=_e(_tier_label(task['tier'])), estimated_min_steps=_e(task['estimated_min_steps'])) + '</div><div class="kv"><b>' + t("task_pages.task_type", "Task type") + f"</b>{_e(task['task_type'])}</div><div class=\"kv\"><b>" + t("task_pages.official_hard_subset", "Official hard subset") + f"</b>{t('task_table.yes', 'Yes') if task['official_hard'] else t('task_table.no', 'No')}</div></div><h3>" + t("task_pages.public_task_intent", "Public task intent") + f"</h3><p>{_e(task['intent'])}</p><p class=\"fine\">" + t("task_pages.official_source", "Official source:") + f"<a href=\"https://github.com/ServiceNow/webarena-verified\">WebArena‑Verified {_e(environment.get('webarena_verified_version', '—'))}</a> " + t("task_pages.task_template_id_revision", "· Task #{task_id} · template ID {value} · revision {value_2}", task_id=task_id, value=task.get('intent_template_id') or '—', value_2=task.get('revision') or '—') + "</p>"),
        )
        detail += _section(t("task_pages.ground_truth_official_acceptance_contract", "Ground truth · official acceptance contract"), _task_contract_html(task))
        detail += _section(
            t("task_pages.view_measured_results_for_this_task", "View measured results for this task"),
            ("<p>" + t("task_pages.dataset_pages_remain_frozen_inputs_measured", "Dataset pages remain frozen inputs. Measured outputs, verdicts, and trajectories appear on result pages.") + '</p><div class="page-links">')
            + "".join(
                (f'<a href="results/{model_id}-{engine}-{task_id}.html">' + t("task_pages.result", "{models} · {engines} result", models=_e(participants.models[model_id]), engines=_e(participants.engines[engine])) + "</a>")
                for model_id in participants.models
                for engine in participants.engines
                if (model_id, engine, task_id) in by_attempt
            )
            + ('<a href="analysis/#same-task">' + t("task_pages.compare_all_browsers", "Compare all browsers") + "</a></div>"),
        )
        (stage / f"datasets/{task_id}.html").write_text(
            _page(title=t("task_pages.task", "Task {task_id}", task_id=task_id), active="datasets", body=detail, depth=1),
            encoding="utf-8",
        )


def _methodology_intro() -> str:
    benchmarks = [
        [
            "WebArena‑Verified",
            t("methodology_intro.self_hosted_interactive_websites", "Self-hosted interactive websites"),
            t("methodology_intro.final_json_plus_required_network_side", "Final JSON plus required network side effects"),
            t("methodology_intro.programmatic_no_llm_judge", "Programmatic; no LLM judge"),
            t("methodology_intro.heavy_deployment_strongest_fit_for_browser", "Heavy deployment; strongest fit for browser A/B testing"),
        ],
        ["Mind2Web", t("methodology_intro.offline_webpage_snapshots", "Offline webpage snapshots"), t("methodology_intro.target_element_and_action_at_each", "Target element and action at each step"), t("methodology_intro.programmatic_action_metrics", "Programmatic action metrics"), t("methodology_intro.evaluates_process_imitation", "Evaluates process imitation")],
        [
            "Online‑Mind2Web",
            t("methodology_intro.136_live_websites", "136 live websites"),
            t("methodology_intro.action_history_and_final_completion", "Action history and final completion"),
            t("methodology_intro.webjudge_plus_human_labels", "WebJudge plus human labels"),
            t("methodology_intro.drift_captcha_and_judge_bias", "Drift, CAPTCHA, and judge bias"),
        ],
        [
            "WebVoyager",
            t("methodology_intro.visual_interaction_with_live_websites", "Visual interaction with live websites"),
            t("methodology_intro.final_answer_and_execution_evidence", "Final answer and execution evidence"),
            t("methodology_intro.multimodal_llm_plus_human_verification", "Multimodal LLM plus human verification"),
            t("methodology_intro.weaker_exact_reproducibility", "Weaker exact reproducibility"),
        ],
        [
            "Mind2Web 2",
            t("methodology_intro.long_horizon_multi_site_search", "Long-horizon multi-site search"),
            t("methodology_intro.research_answer_with_citations", "Research answer with citations"),
            t("methodology_intro.rubric_agent_as_a_judge", "Rubric Agent-as-a-Judge"),
            t("methodology_intro.not_the_first_choice_for_ui", "Not the first choice for UI browser cores"),
        ],
        [
            "BU Bench V1",
            t("methodology_intro.100_mixed_tasks", "100 mixed tasks"),
            t("methodology_intro.final_outcomes_and_trajectories", "Final outcomes and trajectories"),
            "Google judge LLM",
            t("methodology_intro.cloud_browser_conditions_affect_comparability", "Cloud-browser conditions affect comparability"),
        ],
        ["Odysseys", t("methodology_intro.200_long_horizon_multi_site_tasks", "200 long-horizon multi-site tasks"), t("methodology_intro.rubric_completion", "Rubric completion"), t("methodology_intro.model_grading", "Model grading"), t("methodology_intro.high_cost", "High cost")],
    ]
    return _hero(
        "Methodology",
        t("methodology_intro.evaluate_final_outcomes_not_a_reference", "Evaluate final outcomes, not a reference click path"),
        _translated("methodology_intro.scores_come_from_the_official_evaluator", "Scores come from the official evaluator bound by the execution records; deterministic auditing checks evidence consistency."),
    ) + _section(
        t("methodology_intro.acceptance_boundaries_of_candidate_benchmarks", "Acceptance boundaries of candidate benchmarks"),
        _table(["Benchmark", t("methodology_intro.what_it_evaluates", "What it evaluates"), t("methodology_intro.acceptance_target", "Acceptance target"), t("methodology_intro.scoring", "Scoring"), t("methodology_intro.limit", "Limit")], benchmarks),
    )


def _methodology_scoring_chain(inputs: ReportInputs) -> str:
    tasks = inputs.tasks
    task_count = inputs.task_count
    evaluator_counts = Counter(
        rule.get("evaluator", "UnknownEvaluator")
        for task in tasks
        for rule in (task.get("contract") or {}).get("evaluation_contract", [])
    )
    return _section(
        t("methodology_scoring_chain.scoring_chain_for_this_run", "Scoring chain for this run"),
        ('<ol class="flow"><li>' + t("methodology_scoring_chain.preregister_tasks", "Preregister {task_count} tasks", task_count=task_count) + "</li><li>" + t("methodology_scoring_chain.cowagent_plans_autonomously", "CowAgent plans autonomously") + "</li><li>BrowserTool</li><li>" + t("methodology_scoring_chain.run_official_evaluators", "Run official evaluators") + "</li><li>" + t("methodology_scoring_chain.review_deterministic_evidence", "Review deterministic evidence") + "</li></ol>")
        + '<p>' + _translated("methodology_scoring_chain.the_counts_below_come_from_the", "The counts below come from the selected tasks' frozen acceptance contracts. Every configured evaluator must return 1.0 for a task to pass.") + '</p>'
        + _table(["Evaluator", _translated("trajectory_replay_control.tasks", "Tasks")], sorted(evaluator_counts.items()), table_id="methodology-evaluators"),
    )


def _methodology_deployment(inputs: ReportInputs) -> str:
    participants = inputs.participants
    source_attempts = inputs.source_attempts
    config = inputs.config
    environment = inputs.environment
    by_attempt = inputs.by_attempt
    tasks = inputs.tasks
    task_count = inputs.task_count
    max_agent_steps = _recorded(inputs.max_agent_steps)
    site_access_host = _recorded(inputs.site_access_host)
    preparation_receipt_count = inputs.preparation_receipt_count
    selected_sites = Counter(site for task in tasks for site in task.get("sites", []))
    site_images = environment.get("site_images") or config.get("site_images") or {}
    return _section(
        t("methodology_deployment.how_the_tasks_were_deployed_and", "How the {task_count} tasks were deployed and run by the agent", task_count=task_count),
        '<figure id="deployment-flow" class="method-figure"><img src="assets/webarena-autonomous-evaluation-flow.png" alt="Browser Use evaluation flow: tasks, recorded environment, autonomous actions, registered browsers and official evaluators"><figcaption>'
        + _translated("methodology_deployment.the_diagram_explains_responsibilities_the_recorded", "The diagram explains responsibilities. The recorded deployment and preparation coverage are specified below and in each execution's evidence.")
        + '</figcaption></figure><p>'
        + _translated("methodology_deployment.the_selection_contains_no_click_sequence", "The selection contains no click sequence. CowAgent observes pages, requests model decisions and generates BrowserTool calls within a {max_agent_steps}-step budget. Browser versions and environment identity follow the execution records.", max_agent_steps=max_agent_steps) + '</p>'
        + _table([_translated("methodology_deployment.environment_field", "Environment field"), _translated("methodology_deployment.recorded_identity", "Recorded identity")], [
            [_translated("methodology_deployment.host", "Host"), environment.get("platform", "—")],
            [_translated("methodology_deployment.docker_context", "Docker context"), config.get("docker_context", "—")],
            [_translated("methodology_deployment.site_access_host", "Site access host"), site_access_host],
        ])
        + _table([_translated("same_task_comparison.site", "Site"), _translated("methodology_deployment.selected_tasks", "Selected tasks"), _translated("methodology_deployment.recorded_image", "Recorded image")], [
            [SITE_LABELS.get(site, site), count, (site_images.get(site) or {}).get("reference", "—")]
            for site, count in sorted(selected_sites.items())
        ], table_id="methodology-sites")
        + '<p>' + _translated("methodology_deployment.environment_preparation_receipts_physical_executions_reset", "Environment preparation receipts: {preparation_receipt_count}/{source_attempts_count} physical executions. Reset, readiness and health-check states follow the corresponding receipts.", preparation_receipt_count=preparation_receipt_count, source_attempts_count=len(source_attempts)) + '</p>'
        + '<p class="page-links">'
        + "".join(
            f'<a href="results/{model_id}-{engine}-{example_id}.html#trajectory">'
            + _translated("methodology_deployment.autonomous_trajectory", "{models} · {engines} autonomous trajectory", models=participants.models[model_id], engines=participants.engines[engine]) + '</a>'
            for model_id in participants.models for engine in participants.engines
            for example_id in [next((int(task["task_id"]) for task in tasks
                                     if (model_id, engine, int(task["task_id"])) in by_attempt), None)]
            if example_id is not None
        ) + ('<a href="reproduce/">' + t("methodology_deployment.verify_image_and_runtime_identities", "Verify image and runtime identities") + "</a></p>"),
    )


def _methodology_protocol(inputs: ReportInputs) -> str:
    profile = inputs.profile
    protocol = inputs.protocol
    reasoning_effort = _recorded(inputs.reasoning_effort)
    limit = protocol.get('max_task_executions') or profile.get('max_task_executions')
    model_names = inputs.model_names
    return _section(
        t("methodology_protocol.fixed_protocol", "Fixed protocol"),
        _table(
            [t("release_details.item", "Item"), t("methodology_protocol.rule", "Rule")],
            [
                [t("remediation.agent_model", "Agent / model"), _translated("methodology_protocol.cowagent_reasoning_effort", "CowAgent; {model_names}; reasoning effort {reasoning_effort}", model_names=model_names, reasoning_effort=reasoning_effort)],
                [t("methodology_protocol.planned_and_actual_controls", "Planned and actual controls"), _translated("methodology_protocol.model_prompt_tool_resource_and_environment", "Model, prompt, tool, resource and environment controls follow the frozen configuration and recorded executions.")],
                [t("methodology_protocol.logical_results", "Logical results"), _translated("methodology_protocol.one_retained_logical_result_per_task", "One retained logical result per task, model and browser; binary acceptance and task-paired comparison.")],
                [
                    t("methodology_protocol.execution_limit", "Execution limit"),
                    Safe(bilingual_text(
                        _ZH('protocol.execution_limit', 'Frozen execution limit per task: {limit}; execution latency and model usage are cumulative.', limit=limit or _ZH('protocol.limit_not_recorded', 'not recorded')),
                        _EN('protocol.execution_limit', 'Frozen execution limit per task: {limit}; execution latency and model usage are cumulative.', limit=limit or 'not recorded'))),
                ],
                [
                    t("methodology_protocol.exclusions", "Exclusions"),
                    _translated("methodology_protocol.only_tasks_and_acceptance_contracts_in", "Only tasks and acceptance contracts in the frozen selection are reported."),
                ],
                [t("methodology_protocol.judge", "Judge"), t("methodology_protocol.official_programmatic_evaluators_no_llm_judge", "Official programmatic evaluators; no LLM judge; no manual score changes")],
            ],
        ),
    )


def _methodology_evaluators() -> str:
    return _section(
        t("methodology_evaluators.what_the_two_programmatic_evaluators_check", "What the two programmatic evaluators check"),
        _table(
            ["Evaluator", t("methodology_evaluators.input", "Input"), t("methodology_evaluators.comparison", "Comparison"), t("methodology_evaluators.what_it_establishes", "What it establishes"), t("methodology_evaluators.what_it_does_not_establish", "What it does not establish")],
            [
                [
                    "AgentResponseEvaluator",
                    t("methodology_evaluators.agent_final_json", "Agent final JSON"),
                    t("methodology_evaluators.normalize_by_json_schema_and_data", "Normalize by JSON schema and data type, then compare task_type, status, and retrieved_data; each task may declare ordered or unordered lists."),
                    t("methodology_evaluators.the_final_answer_satisfies_the_task", "The final answer satisfies the task's public answer contract."),
                    t("methodology_evaluators.it_does_not_inspect_which_ui", "It does not inspect which UI path produced the answer or evaluate action efficiency."),
                ],
                [
                    "NetworkEventEvaluator",
                    t("methodology_evaluators.har_from_this_browser_attempt", "HAR from this browser attempt"),
                    t("methodology_evaluators.match_url_method_status_code_and", "Match URL, method, status code, and, when declared, query, POST, response-field, or cookie conditions in network events."),
                    t("methodology_evaluators.navigation_or_state_changes_produced_the", "Navigation or state changes produced the specified observable side effect."),
                    t("methodology_evaluators.it_does_not_require_a_fixed", "It does not require a fixed click sequence; undeclared page state is outside the acceptance scope."),
                ],
            ],
        )
        + ("<p><strong>" + t("methodology_evaluators.conjunction_rule", "Conjunction rule:") + "</strong>" + t("methodology_evaluators.every_evaluator_configured_for_a_task", "Every evaluator configured for a task must score 1.0 for the task to pass. There is no LLM-as-a-judge and no manual score override.") + "</p>"),
    )


def _write_methodology_page(stage: Path, inputs: ReportInputs) -> None:
    methodology = _methodology_intro()
    methodology += _methodology_scoring_chain(inputs)
    methodology += _methodology_deployment(inputs)
    methodology += _methodology_protocol(inputs)
    methodology += _methodology_evaluators()
    (stage / "methodology/index.html").write_text(
        _page(title=t("nav.methodology", "Methodology"), active="methodology", body=methodology, depth=1),
        encoding="utf-8",
    )


def _write_analysis_page(stage: Path, inputs: ReportInputs) -> None:
    participants = inputs.participants
    attempts = inputs.attempts
    verified_causes = inputs.verified_causes
    tasks = inputs.tasks
    task_count = inputs.task_count
    result_count = inputs.result_count
    excluded_task_ids = inputs.excluded_task_ids
    analysis = _hero(
        "Results",
        t("nav.results_analysis", "Results analysis"),
        t("analysis_page.all_configurations_appear_in_one_comparison", "All configurations appear in one comparison and task matrix; failed results open their diagnosis and evidence."),
    )
    analysis += _section(
        t("analysis_page.complete_task_results_entry", "Complete {tasks_count}-task results entry", tasks_count=len(tasks)),
        '<p>' + _translated("analysis_page.the_homepage_compares_pass_rates_latency", "The homepage compares pass rates, latency, tokens and browser resources across all configurations. This page owns the same-task matrix and result diagnosis.") + ('</p><p class="page-links"><a href="./#home-core-metrics">' + t("analysis_page.view_core_metrics_on_the_home", "View core metrics on the home page") + "</a></p>"),
    )
    analysis += _section(
        t("analysis_page.same_task_comparison_across_all_configurations", "Same-task comparison across all configurations: complete {tasks_count}-task set", tasks_count=len(tasks)),
        "<p>" + _translated("analysis_page.each_row_is_a_registered_task", "Each row is a registered task, with {models_count} columns for model-by-browser configurations. Filter for disagreement, all-pass or all-fail tasks.", models_count=len(participants.models) * len(participants.engines)) + "</p>"
        + _same_task_comparison(tasks, attempts, participants),
    ).replace('<section class="section">', '<section class="section" id="same-task">', 1)
    analysis += _section(
        t("analysis_page.browsertool_action_latency", "BrowserTool action latency"),
        ("<p>" + t("analysis_page.for_each_action_every_model_by", "For each action, every model-by-browser configuration is compared together by mean per-call wall time, and only a unique global minimum is marked. Total time and call count depend on agent workload and provide context rather than a speed ranking.") + "</p>")
        + _action_duration_cards(attempts, participants),
    )
    analysis += _section(
        t("analysis_page.results_and_failure_diagnoses", "{result_count} results and failure diagnoses", result_count=result_count),
        ("<p>" + t("analysis_page.all_official_results_for_the_preregistered", "All official results for the {task_count} preregistered tasks are retained; {excluded_task_ids_count} tasks are marked as affected by external interference. Failures are shown by default. Open a diagnosis to inspect the failure stage, program error, reviewed cause, trajectory, screenshots and logs.", task_count=task_count, excluded_task_ids_count=len(excluded_task_ids)) + "</p>")
        + _attempt_table(attempts, tasks, participants, verified_causes),
    )
    (stage / "analysis/index.html").write_text(
        _page(title=t("nav.results_analysis", "Results analysis"), active="analysis", body=analysis, depth=1), encoding="utf-8"
    )


def _write_result_executions(
    stage: Path, public: dict[str, Any], row: dict[str, Any]
) -> tuple[list[dict[str, Any]], Any, Any, list[dict[str, Any]]]:
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
    return execution_records, response_record, evaluation_record, steps


def _evidence_index_entry(
    public: dict[str, Any], row: dict[str, Any], execution_records: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
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


def _result_hero(public: dict[str, Any], participants: Participants, failure_point: str) -> str:
    return _hero(
        public["id"],
        f"{participants.models[public['model']]} · {participants.engines[public['engine']]} · Task #{public['task_id']}",
        (
            t("result_hero.the_failure_occurred_at_the_exact", "The failure occurred at {failure_point}. The exact program error, reviewed root cause, and corresponding execution evidence are provided below.", failure_point=failure_point)
            if float(public["score"]) < 1
            else t("result_hero.this_result_passed_every_official_programmatic", "This result passed every official programmatic evaluator. The task contract, execution trajectory, and runtime evidence are available below.")
        ),
    )


def _result_navigation(public: dict[str, Any], attempt_page: str) -> str:
    return (
        ('<nav class="page-links wrap" aria-label="' + t("result_navigation.result_detail_navigation", "Result detail navigation") + '">')
        + (
            (f'<a href="{attempt_page}#diagnosis">' + t("diagnosis_dialog.failure_diagnosis", "Failure diagnosis") + "</a>")
            if float(public["score"]) < 1
            else ""
        )
        + (f'<a href="{attempt_page}#verification">' + t("result_navigation.task_and_acceptance", "Task and acceptance") + "</a>")
        + (f'<a href="{attempt_page}#trajectory">' + t("result_navigation.execution_trajectory", "Execution trajectory") + "</a>")
        + (
            (f'<a href="{attempt_page}#screenshots">' + t("result_navigation.page_frames", "Page frames") + "</a>")
            if float(public["score"]) < 1
            else ""
        )
        + (f'<a href="{attempt_page}#runtime">' + t("result_navigation.runtime_and_evidence", "Runtime and evidence") + "</a>")
        + "</nav>"
    )


def _result_runtime_facts(public: dict[str, Any], usage: dict[str, Any]) -> str:
    return (
        '<div class="attempt-secondary runtime-strip"><div class="attempt-fact"><b>' + t("result_runtime_facts.autonomous_actions", "Autonomous actions") + "</b>" + t("result_runtime_facts.times", "{action_count} times", action_count=public['action_count']) + '</div><div class="attempt-fact"><b>' + t("result_runtime_facts.agent_path", "Agent path") + f"</b>{_seconds(public['duration_ms'])}</div><div class=\"attempt-fact\"><b>BrowserTool</b>{_seconds(public['browser_tool_duration_ms'])}</div><div class=\"attempt-fact\"><b>" + t("result_runtime_facts.model_tokens", "Model tokens") + f"</b>{_number(usage['total_tokens'])}</div><div class=\"attempt-fact\"><b>" + t("result_runtime_facts.model_requests", "Model requests") + "</b>" + t("result_runtime_facts.calls_transport_attempts", "{request_count} calls / {request_attempt_count} transport attempts", request_count=usage['request_count'], request_attempt_count=usage['request_attempt_count']) + "</div></div>"
    )


def _result_failure_section(
    *,
    public: dict[str, Any],
    row: dict[str, Any],
    evaluation_record: Any,
    attempt_page: str,
    failure_point: str,
    failure_step: int | None,
    step_context_label: str,
    step_context: str,
    remediation_owner: str,
    remediation_action: str,
    external_interference: bool,
    runtime_facts: str,
    subcategory: str,
    reason: str,
) -> str:
    step_href = (
        f"{attempt_page}#trajectory-step-{failure_step}"
        if failure_step
        else f"{attempt_page}#trajectory"
    )
    return '<div id="diagnosis"></div>' + _section(
        t("diagnosis_dialog.failure_diagnosis", "Failure diagnosis"),
        ('<article class="failure-lead"><span class="status-line">' + t("same_task_comparison.fail", "Fail") + f'</span><h3><a href="{step_href}">{_e(failure_point)}</a></h3><p>{_e(_failure_brief(evaluation_record, row))}</p></article>')
        + (
            f'<p class="step-signature"><strong>{_e(t("result_failure_section.step_signature", "{label}: ", label=step_context_label))}</strong><a href="{step_href}">{_raw(step_context)}</a></p>'
            if step_context
            else ""
        )
        + ('<div class="diagnosis-grid"><div class="attempt-fact"><b>' + t("diagnosis_dialog.program_verdict", "Program verdict") + f"</b>{_e(_automatic_failure_type(evaluation_record)[1] or t('filters.none', 'None'))}</div><div class=\"attempt-fact\"><b>" + t("diagnosis_dialog.reviewed_root_cause", "Reviewed root cause") + f"</b>{_e(subcategory or t('diagnosis_dialog.awaiting_review', 'Awaiting review'))}</div><div class=\"attempt-fact\"><b>" + t("diagnosis_dialog.owning_scope", "Owning scope") + f'</b>{_e(remediation_owner)}</div><div class="attempt-fact"><b>' + t("review_history.treatment", "Treatment") + f'</b>{_e(remediation_action)}</div></div><div class="failure-error"><h3>' + t("diagnosis_dialog.program_error", "Program error") + f'</h3><pre class="log-view">{_e(_evaluator_error_text(evaluation_record, row))}</pre></div><div class="diagnosis-reason"><strong>' + t("result_failure_section.reviewed_conclusion", "Reviewed conclusion:") + "</strong>")
        + _e(reason or t("result_failure_section.no_verified_root_cause_is_available", "No verified root cause is available yet."))
        + (f'</div><p class="page-links"><a href="{attempt_page}#verification">' + t("diagnosis_dialog.view_expected_and_actual_values", "View expected and actual values") + f'</a><a href="{step_href}">' + t("diagnosis_dialog.view_trajectory_near_the_failure", "View trajectory near the failure") + f'</a><a href="{attempt_page}#screenshots">' + t("diagnosis_dialog.view_page_screenshots", "View page screenshots") + f'</a><a href="{attempt_page}#runtime">' + t("result_failure_section.view_complete_logs", "View complete logs") + "</a></p>")
        + runtime_facts
        + (
            ('<p class="callout"><strong>' + t("result_failure_section.comparison_treatment", "Comparison treatment:") + "</strong>" + t("result_failure_section.this_result_contains_confirmed_interference_outside", "This result contains confirmed interference outside the model/browser variables. Its official score is retained, but it is temporarily excluded from the clean subset.") + "</p>")
            if external_interference
            else ""
        ),
    )


def _result_success_section(evaluation_record: Any, row: dict[str, Any], runtime_facts: str) -> str:
    return _section(
        t("filters.result", "Result"),
        ('<article class="failure-lead success-lead"><span class="status-line">' + t("same_task_comparison.pass", "Pass") + f"</span><h3>{_e(_failure_brief(evaluation_record, row))}</h3></article>")
        + runtime_facts,
    )


def _result_evidence_section(
    public: dict[str, Any],
    execution_records: list[dict[str, Any]],
    resource: dict[str, Any],
    checks: dict[str, Any],
    metadata: dict[str, Any],
) -> str:
    return '<div id="runtime"></div>' + _section(
        t("result_evidence_section.runtime_logs_and_evidence", "Runtime, logs, and evidence"),
        _execution_evidence_html(public["id"], execution_records)
        + ('<p class="fine">' + t("result_evidence_section.the_public_bundle_contains_sanitized_answers", "The public bundle contains sanitized answers, evaluator records, event streams, and trajectories. Raw HAR and Playwright traces may contain authenticated state, cookies, page snapshots, or request bodies, so they are withheld; byte counts and SHA-256 hashes retain the evidence binding.") + "</p>")
        + _table([t("result_evidence_section.browser_resource_metric", "Browser resource metric"), t("result_evidence_section.value", "Value")], _resource_detail_rows(resource))
        + ('<p class="fine">' + t("result_evidence_section.whole_run_container_measurements_are_diagnostic", "Whole-run container measurements are diagnostic only; memory and PID are not compared directly across different process models.") + "</p>")
        + ("<h3>" + t("result_evidence_section.evidence_completeness_checks", "Evidence completeness checks") + "</h3>")
        + _table(
            [t("result_evidence_section.check", "Check"), t("filters.result", "Result")],
            _evidence_check_rows(checks),
        )
        + ("<h3>" + t("result_evidence_section.source_evidence_binding_for_the_authoritative", "Source-evidence binding for the authoritative execution") + "</h3>")
        + _table(
            [t("attempt_table.evidence", "Evidence"), t("result_evidence_section.present", "Present"), t("result_evidence_section.bytes", "Bytes"), "SHA-256", t("result_evidence_section.publication_policy", "Publication policy")],
            [
                [
                    label,
                    t("task_table.yes", "Yes") if value.get("present") else t("task_table.no", "No"),
                    value.get("bytes"),
                    value.get("sha256") or "—",
                    _publication_label(value.get("publication")),
                ]
                for label, value in (metadata.get("files") or {}).items()
            ],
        )
        + (f"<p class=\"page-links\"><a href=\"datasets/{public['task_id']}.html\">" + t("result_evidence_section.return_to_the_task_page", "Return to the task page") + "</a></p>"),
    )


def _write_result_page(
    stage: Path,
    inputs: ReportInputs,
    row: dict[str, Any],
    public: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    participants = inputs.participants
    verified_causes = inputs.verified_causes
    tasks = inputs.tasks
    task = next(t for t in tasks if int(t["task_id"]) == public["task_id"])
    execution_records, response_record, evaluation_record, steps = _write_result_executions(
        stage, public, row
    )
    metadata = evidence_metadata(row)
    usage, resource, checks = (
        public["model_usage"],
        public["resource"],
        item.get("checks") or {},
    )
    cause_code = _cause_code(verified_causes, public["id"])
    failure_point, failure_step = _failure_point(cause_code, steps)
    step_context_label, step_context = _trajectory_context(
        steps, failure_step, failure_point, t
    )
    remediation_owner, remediation_action, external_interference = _remediation(cause_code)
    subcategory, reason = localized_cause(verified_causes, public["id"], _language())
    detail = _result_hero(public, participants, failure_point)
    # Pages declare <base href="../"> so every internal URL is rooted at
    # the report bundle, including same-page fragment links.
    attempt_page = f"results/{public['id']}.html"
    detail += _result_navigation(public, attempt_page)
    runtime_facts = _result_runtime_facts(public, usage)
    if float(public["score"]) < 1:
        detail += _result_failure_section(
            public=public,
            row=row,
            evaluation_record=evaluation_record,
            attempt_page=attempt_page,
            failure_point=failure_point,
            failure_step=failure_step,
            step_context_label=step_context_label,
            step_context=step_context,
            remediation_owner=remediation_owner,
            remediation_action=remediation_action,
            external_interference=external_interference,
            runtime_facts=runtime_facts,
            subcategory=subcategory,
            reason=reason,
        )
    else:
        detail += _result_success_section(evaluation_record, row, runtime_facts)
    detail += '<div id="verification"></div>' + _section(
        t("result_page.task_requirements_and_acceptance_result", "Task requirements and acceptance result"),
        _verification_html(task, evaluation_record, response_record)
        + ('<p class="fine">' + t("result_page.all_rules_are_official_programmatic_checks", "All rules are official programmatic checks and every rule must score 1.0. The page leads with a readable conclusion; expectations, observations, and assertions remain in collapsed technical details and downloadable files.") + "</p>"),
    )
    detail += '<div id="trajectory"></div>' + _section(
        t("result_page.cowagent_autonomous_trajectory", "CowAgent autonomous trajectory"),
        ("<p>" + t("result_page.this_sequence_comes_from_the_run", "This sequence comes from the run's agent event stream, not a fixed action replay. Expand a step for arguments and an output excerpt; the complete sanitized log is downloadable.") + "</p>")
        + _trajectory_html(steps),
    )
    if float(public["score"]) < 1:
        detail += '<div id="screenshots"></div>' + _section(
            t("result_page.final_page_frames_from_the_failed", "Final page frames from the failed execution"),
            _screenshot_gallery(public["id"], execution_records),
        )
    detail += _result_evidence_section(public, execution_records, resource, checks, metadata)
    (stage / f"results/{public['id']}.html").write_text(
        _page(title=public["id"], active="analysis", body=detail, depth=1), encoding="utf-8"
    )
    return _evidence_index_entry(public, row, execution_records)


def _write_result_pages(stage: Path, inputs: ReportInputs) -> list[dict[str, Any]]:
    review = inputs.review
    attempts = inputs.attempts
    public_attempts = inputs.public_attempts
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
        public_evidence_results.append(_write_result_page(stage, inputs, row, public, item))
    return public_evidence_results


def _write_review_page(stage: Path, release_data: dict[str, Any]) -> None:
    review_page = _hero(
        "Review history",
        t("review_page.review_and_revision_history", "Review and revision history"),
        t("review_page.each_round_records_its_scope_findings", "Each round records its scope, findings, formal corrections, and remaining items. Per-task verdict evidence remains on result detail pages."),
    )
    review_page += _section(
        t("review_page.current_status", "Current status"),
        ('<p class="callout success"><strong>' + t("review_page.release_gate", "Release gate:") + "</strong>" + t("review_page.evidence_files_hashes_scores_statuses_the", "Evidence files, hashes, scores, statuses, the official version, and report links passed programmatic consistency checks.") + "</p><p>" + t("review_page.the_official_webarena_verified_evaluator_is", "The official WebArena-Verified evaluator is the scoring authority. This history records checks and revisions to the implementation and report; it does not replace task scoring.") + "</p>"),
    )
    review_page += _section(
        t("review_page.review_rounds", "Review rounds"),
        _review_history(release_data.get("browser_use_reviews") or []),
    )
    (stage / "review/index.html").write_text(
        _page(title=t("nav.review", "Review"), active="review", body=review_page, depth=1), encoding="utf-8"
    )


def _adoption_versions_section(inputs: ReportInputs) -> str:
    participants = inputs.participants
    environment = inputs.environment
    summary_by_model = inputs.summary_by_model
    participant_versions = environment.get("browser_versions") or {}
    participant_images = environment.get("browser_images") or {}
    return _section(
        t("adoption_versions_section.browsers_and_versions", "Browsers and versions"),
        _table(
            [t("resource_comparison_table.model", "Model"), t("resource_comparison_table.browser", "Browser"), t("adoption_versions_section.runtime_version", "Runtime version"), t("adoption_versions_section.image_id", "Image ID"), t("adoption_versions_section.integration", "Integration"), t("article_body.tasks_passed", "Tasks passed")],
            [
                [
                    participants.models[model_id],
                    Safe(f'<a href="engines/{engine}.html">{_e(label)}</a>'),
                    (participant_versions.get(engine) or {}).get("Browser", "—"),
                    (participant_images.get(engine) or {}).get("id", "—"),
                    t("adoption_versions_section.dedicated_container_cdp_cowagent_browsertool", "Dedicated container · CDP · CowAgent BrowserTool"),
                    f"{summary_by_model[model_id][engine]['passed']}/{summary_by_model[model_id][engine]['completed']}",
                ]
                for model_id in participants.models
                for engine, label in participants.engines.items()
            ],
        )
        + ("<p>" + t("adoption_versions_section.browsers_are_compared_separately_within_each", "Browsers are compared separately within each model; results from different models are not merged into one ranking.") + "</p>"),
    )


def _adoption_conditions_section(inputs: ReportInputs) -> str:
    participants = inputs.participants
    protocol = inputs.protocol
    task_count = inputs.task_count
    max_agent_steps = inputs.max_agent_steps
    if max_agent_steps == _ZH("common.not_recorded", "Not recorded"):
        max_agent_steps = t("conditions.unrecorded_steps", "an unrecorded number of")
    return _section(
        t("adoption_conditions_section.shared_experimental_conditions", "Shared experimental conditions"),
        _table(
            [t("adoption_conditions_section.stack_field", "Stack field"), t("adoption_conditions_section.fixed_value_in_this_run", "Fixed value in this run"), t("adoption_conditions_section.role", "Role")],
            [
                [
                    "Agent",
                    f"CowAgent · commit {protocol.get('cowagent_commit', '—')}",
                    t("adoption_conditions_section.observe_plan_and_call_browsertool_autonomously", "Observe, plan, and call BrowserTool autonomously"),
                ],
                [
                    t("resource_comparison_table.model", "Model"),
                    ", ".join(participants.models.values())
                    + f" · reasoning={protocol.get('reasoning_effort', '—')}",
                    t("adoption_conditions_section.generates_browser_decisions_within_each_separately", "Generates browser decisions within each separately reported model run"),
                ],
                [
                    t("adoption_conditions_section.tasks_and_sites", "Tasks and sites"),
                    t("adoption_conditions_section.same_tasks_same_local_site_images", "Same {task_count} tasks; same local site images", task_count=task_count),
                    t("adoption_conditions_section.provide_identical_inputs_and_website_state", "Provide identical inputs and website state"),
                ],
                [t("adoption_conditions_section.action_interface", "Action interface"), "CowAgent BrowserTool · CDP", t("adoption_conditions_section.deliver_agent_actions_to_the_browser", "Deliver agent actions to the browser")],
                [
                    t("adoption_conditions_section.evaluation", "Evaluation"),
                    t("adoption_conditions_section.official_webarena_verified_1_2_3", "Official WebArena-Verified 1.2.3 evaluator"),
                    t("adoption_conditions_section.score_final_json_and_required_network", "Score final JSON and required network side effects"),
                ],
                [
                    t("adoption_conditions_section.resources_and_limits", "Resources and limits"),
                    t("adoption_conditions_section.same_cpu_memory_and_up_to", "Same CPU, memory, and up to {max_agent_steps} steps; historical viewports differed", max_agent_steps=max_agent_steps),
                    t("adoption_conditions_section.control_the_execution_budget", "Control the execution budget"),
                ],
            ],
        )
        + ("<p><strong>" + t("adoption_conditions_section.cowagent_executes_all_tasks_autonomously", "CowAgent executes all {task_count} tasks autonomously.", task_count=task_count) + "</strong>" + t("adoption_conditions_section.the_repository_s_browser_trajectory_replay", "The repository's Browser Trajectory Replay track is a separate fixed-action test and is not included in these scores.") + "</p>"),
    )


def _write_adoption_page(stage: Path, inputs: ReportInputs) -> None:
    adoption = _hero(
        "Participants",
        t("adoption_page.browsers_tested_and_fixed_experimental_controls", "Browsers tested and fixed experimental controls"),
        t("adoption_page.this_report_compares_browsers_results_are", "This report compares browsers. Results are calculated separately for each model, while CowAgent, tasks, resource limits, sites, and evaluators remain fixed."),
    )
    adoption += _adoption_versions_section(inputs)
    adoption += _adoption_conditions_section(inputs)
    adoption += _section(
        t("adoption_page.adding_another_browser", "Adding another browser"),
        ("<p>" + t("adoption_page.register_the_new_browser_s_container", "Register the new browser's container image, version, and CDP endpoint, then complete the same frozen task set under the shared controls. The leaderboard adds a browser row, the task matrix adds a browser column, and statistical comparisons are generated per browser pair rather than collapsing multiple browsers into one win/loss label.") + "</p>"),
    )
    (stage / "adoption/index.html").write_text(
        _page(title=t("nav.browsers_tested", "Browsers tested"), active="adoption", body=adoption, depth=1), encoding="utf-8"
    )


def _write_engine_pages(stage: Path, inputs: ReportInputs) -> list[list[Any]]:
    participants = inputs.participants
    environment = inputs.environment
    summary_by_model = inputs.summary_by_model
    versions, images = (
        environment.get("browser_versions") or {},
        environment.get("browser_images") or {},
    )
    engine_rows = []
    for engine in participants.engines:
        version, image = versions.get(engine) or {}, images.get(engine) or {}
        engine_rows.append(
            [
                Safe(f'<a href="engines/{engine}.html">{participants.engines[engine]}</a>'),
                version.get("Browser", "—"),
                version.get("Protocol-Version", "—"),
                image.get("architecture", "—"),
                str(image.get("id") or "—"),
            ]
        )
        body = _hero(
            "Engine", participants.engines[engine], t("engine_pages.runtime_identity_complete_task_results_and", "Runtime identity, complete task results, and failure evidence for this browser.")
        ) + _section(
            t("engine_pages.runtime_identity", "Runtime identity"),
            _table(
                [t("engine_pages.field", "Field"), t("result_evidence_section.value", "Value")],
                [
                    ["Browser", version.get("Browser", "—")],
                    ["DevTools Protocol", version.get("Protocol-Version", "—")],
                    [t("engine_pages.architecture", "Architecture"), image.get("architecture", "—")],
                    [t("adoption_versions_section.image_id", "Image ID"), image.get("id", "—")],
                    [
                        t("engine_pages.task_scores_by_model_cohort", "Task scores by model cohort"),
                        "；".join(
                            f"{participants.models[model_id]} {summary_by_model[model_id][engine]['passed']}/{summary_by_model[model_id][engine]['completed']}"
                            for model_id in participants.models
                        ),
                    ],
                    [
                        t("engine_pages.p50_logical_result_latency_by_model", "P50 logical-result latency by model cohort"),
                        "；".join(
                            f"{participants.models[model_id]} {_seconds_short(summary_by_model[model_id][engine]['p50_duration_ms'])}"
                            for model_id in participants.models
                        ),
                    ],
                ],
            )
            + ('<p class="fine">' + t("engine_pages.version_and_image_identity_bound_reproducibility", "Version and image identity bound reproducibility and do not affect scoring.") + "</p>"),
        )
        body += _section(
            t("engine_pages.view_all_results_for_this_browser", "View all results for this browser"),
            ("<p>" + t("engine_pages.the_report_keeps_one_result_inventory", "The report keeps one result inventory. The link below opens it with this browser preselected; failure diagnoses, trajectories, page frames, and logs are all reached from that inventory.") + f'</p><div class="report-entry-links"><a class="button-link" href="analysis/index.html?f_engine={engine}#result-table">' + t("engine_pages.open_the_filtered_result_inventory", "Open the filtered result inventory →") + "</a></div>"),
        )
        (stage / f"engines/{engine}.html").write_text(
            _page(title=participants.engines[engine], active="adoption", body=body, depth=1),
            encoding="utf-8",
        )
    return engine_rows


def _reproduce_identity_section(inputs: ReportInputs, engine_rows: list[list[Any]]) -> str:
    selection = inputs.selection
    config = inputs.config
    protocol = inputs.protocol
    environment = inputs.environment
    model_names = inputs.model_names
    return _section(
        t("reproduce_identity_section.browser_and_code_identities", "Browser and code identities"),
        _table([t("resource_comparison_table.browser", "Browser"), t("reproduce_identity_section.version", "Version"), t("reproduce_identity_section.protocol", "Protocol"), t("engine_pages.architecture", "Architecture"), t("adoption_versions_section.image_id", "Image ID")], engine_rows)
        + _table(
            [t("release_details.item", "Item"), t("result_evidence_section.value", "Value")],
            [
                ["browser-eval commit", protocol.get("browser_eval_commit", "—")],
                ["CowAgent commit", protocol.get("cowagent_commit", "—")],
                [t("resource_comparison_table.model", "Model"), f"{model_names} · reasoning={protocol.get('reasoning_effort', '—')}"],
                ["WebArena‑Verified", environment.get("webarena_verified_version", "—")],
                [t("reproduce_identity_section.run_config_selection_sha_256", "Run-config selection SHA-256"), config.get("selection_sha256", selection.sha256)],
                [t("reproduce_identity_section.current_selection_sha_256", "Current selection SHA-256"), selection.sha256],
                [
                    "evaluator checksum",
                    (environment.get("official_evaluator") or {}).get(
                        "evaluator_checksum", "—"
                    ),
                ],
            ],
        ),
    )


def _reproduce_deployment_section(inputs: ReportInputs) -> str:
    source_attempts = inputs.source_attempts
    environment = inputs.environment
    preparation_receipt_count = inputs.preparation_receipt_count
    preparation_receipts_complete = inputs.preparation_receipts_complete
    site_images = environment.get("site_images") or {}
    return _section(
        t("reproduce_deployment_section.deployment_environment_and_network_topology", "Deployment environment and network topology"),
        "<p>" + _translated("reproduce_deployment_section.the_table_shows_the_recorded_host", "The table shows the recorded host, container runtime, site access entry and image identities. These describe the selected evidence and do not prescribe deployment defaults for another instance.") + "</p>"
        + _table(
            [t("reproduce_deployment_section.layer", "Layer"), t("reproduce_deployment_section.identity_in_this_run", "Identity in this run"), t("reproduce_deployment_section.purpose", "Purpose")],
            [
                [t("methodology_deployment.host", "Host"), environment.get("platform", "—"), _translated("reproduce_deployment_section.recorded_execution_host", "Recorded execution host")],
                [
                    "Docker",
                    f"{environment.get('docker_name', '—')} · {environment.get('docker_architecture', '—')} · {environment.get('docker_server_version', '—')}",
                    t("reproduce_deployment_section.hosts_site_and_browser_containers", "Hosts site and browser containers"),
                ],
                [
                    t("reproduce_deployment_section.browser_to_host_gateway", "Browser-to-host gateway"),
                    environment.get("site_access_host", "—"),
                    t("reproduce_deployment_section.accesses_published_ports_from_site_containers", "Accesses published ports from site containers"),
                ],
                [
                    t("reproduce_deployment_section.arm_compatibility_mode", "ARM compatibility mode"),
                    (environment.get("site_runtime_compatibility") or {}).get("mode", t("reproduce_deployment_section.disabled", "Disabled")),
                    _translated("reproduce_deployment_section.recorded_runtime_compatibility", "Recorded runtime compatibility"),
                ],
            ],
        )
        + _table(
            [t("reproduce_deployment_section.site_image", "Site image"), t("reproduce_deployment_section.image_reference", "Image reference"), t("adoption_versions_section.image_id", "Image ID"), "Repo digest"],
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
            ('<p class="fine">' + t("reproduce_deployment_section.all_physical_executions_retain_environment_preparation", "All {preparation_receipt_count}/{source_attempts_count} physical executions retain environment-preparation receipts. The public summary preserves each status while authenticated session state remains private.", preparation_receipt_count=preparation_receipt_count, source_attempts_count=len(source_attempts)) + "</p>")
            if preparation_receipts_complete
            else ('<p class="fine">' + t("reproduce_deployment_section.per_execution_environment_preparation_receipts_are", "Per-execution environment-preparation receipts are incomplete; current records can verify only runner configuration and image identity.") + "</p>")
        ),
    )


def _reproduce_isolation_section(participants: Participants) -> str:
    return _section(
        t("reproduce_isolation_section.environment_isolation_and_parallelism_boundary", "Environment isolation and parallelism boundary"),
        ("<p>" + t("reproduce_isolation_section.the_corrected_runner_treats_one_docker", "The corrected runner treats one Docker daemon and its fixed site ports as one environment lane. Tasks and browsers in the same lane run serially: each attempt resets the sites first, then completes the Agent run, evidence finalization, and evaluation while one cross-process lock protects the full lifecycle. If the Docker daemon identity is unavailable, execution stops instead of falling back to an unlocked run.") + "</p>")
        + _table(
            [t("reproduce_isolation_section.execution_mode", "Execution mode"), t("reproduce_isolation_section.allowed", "Allowed"), t("reproduce_isolation_section.isolation_requirement", "Isolation requirement")],
            [
                [
                    t("reproduce_isolation_section.one_site_stack_run_serially", "One site stack; {engine_names} run serially", engine_names=participants.engine_names),
                    t("reproduce_isolation_section.allowed_default", "Allowed; default"),
                    t("reproduce_isolation_section.one_daemon_scoped_lock_reset_and", "One daemon-scoped lock; reset and verify initial state before every attempt"),
                ],
                [
                    t("reproduce_isolation_section.one_site_stack_browsers_run_in", "One site stack; {value} browsers run in parallel", value=t('reproduce_isolation_section.two', 'two') if len(participants.engines) == 2 else t('reproduce_isolation_section.multiple', 'multiple')),
                    t("reproduce_isolation_section.not_allowed", "Not allowed"),
                    t("reproduce_isolation_section.separate_accounts_still_share_objects_indexes", "Separate accounts still share objects, indexes, caches, and background jobs"),
                ],
                [
                    t("reproduce_isolation_section.multiple_tasks_in_parallel", "Multiple tasks in parallel"),
                    t("reproduce_isolation_section.allowed_2", "Allowed"),
                    t("reproduce_isolation_section.each_worker_uses_an_independent_daemon", "Each worker uses an independent daemon, site containers, network, ports, and writable volumes; read-only image layers may be shared"),
                ],
            ],
        )
        + '<p class="fine">' + _translated("reproduce_isolation_section.account_isolation_prevents_user_object_collisions", "Account isolation prevents user-object collisions; it does not replace environment isolation. The homepage identifies verified external interference and its diagnostic subset.") + '</p>',
    )


def _write_reproduce_page(stage: Path, inputs: ReportInputs, engine_rows: list[list[Any]]) -> None:
    result_count = inputs.result_count
    reproduce = _hero(
        "Reproduction",
        t("reproduce_page.reproduction_starts_with_the_public_repository", "Reproduction starts with the public repository, preregistered cohort, and fixed runtime identities"),
        t("reproduce_page.the_public_bundle_provides_sanitized_projections", "The public bundle provides sanitized projections. Model credentials, cookies, and complete network bodies do not enter Git or the site."),
    )
    reproduce += _reproduce_identity_section(inputs, engine_rows)
    reproduce += _reproduce_deployment_section(inputs)
    reproduce += _reproduce_isolation_section(inputs.participants)
    reproduce += _section(
        t("reproduce_page.public_reproduction_entry_points", "Public reproduction entry points"),
        ('<p><a href="https://github.com/lexmount/browser-eval">lexmount/browser-eval</a> ' + t("reproduce_page.is_the_canonical_source_for_code", "is the canonical source for code, task definitions, and lockfiles.") + '</p><pre><code>./examples/browser_use/run.sh\nmake browser-use-review EVIDENCE="$BROWSER_USE_EVIDENCE"\nmake browser-use-report EVIDENCE="$BROWSER_USE_EVIDENCE"\nmake browser-use-report-check EVIDENCE="$BROWSER_USE_EVIDENCE"</code></pre><div class="page-links"><a href="data/selection.json">selection.json</a><a href="data/results.jsonl">results.jsonl</a><a href="data/evidence-index.json">evidence-index.json</a><a href="data/failure-cause-review.json">failure-cause-review.json</a><a href="data/summary.json">summary.json</a><a href="data/environment.json">environment.json</a><a href="data/review.json">review.json</a><a href="data/publication-privacy.json">publication-privacy.json</a></div><p class="fine">' + t("reproduce_page.the_sanitized_answer_evaluator_output_events", "The sanitized answer, evaluator output, events, and trajectory for each physical execution are located under") + " <code>artifacts/&lt;result-id&gt;/executions/&lt;n&gt;/</code>；<code>evidence-index.json</code> " + t("reproduce_page.lists_all_logical_results_their_physical", "lists all {result_count} logical results, their physical executions, download paths, and public-file hashes.", result_count=result_count) + "</p>"),
    )
    (stage / "reproduce/index.html").write_text(
        _page(title=t("nav.environment_and_reproduction", "Environment and reproduction"), active="reproduce", body=reproduce, depth=1), encoding="utf-8"
    )


def _write_accounts_page(stage: Path, inputs: ReportInputs) -> None:
    reasoning_effort = _recorded(inputs.reasoning_effort)
    model_names = inputs.model_names
    accounts = _hero(
        "Accounts",
        t("accounts_page.local_site_accounts_model_access_and", "Local site accounts, model access, and publication access are separate"),
        t("accounts_page.keys_cookies_and_real_personal_information", "Keys, cookies, and real personal information are not shown. Built-in test emails and usernames in official contracts remain as synthetic ground truth."),
    ) + _section(
        t("accounts_page.access_and_cost", "Access and cost"),
        _table(
            [t("accounts_page.scope", "Scope"), t("accounts_page.configuration_in_this_run", "Configuration in this run"), t("accounts_page.paid_external_account", "Paid external account")],
            [
                [t("accounts_page.four_self_hosted_sites", "Four self-hosted sites"), t("accounts_page.official_docker_images_and_built_in", "Official Docker images and built-in test accounts; reset before each task"), t("accounts_page.not_required", "Not required")],
                [t("accounts_page.model_api", "Model API"), f"{model_names}, reasoning={reasoning_effort}", t("accounts_page.corresponding_api_access_required", "Corresponding API access required")],
                ["Artifact Site", t("accounts_page.publishes_only_the_sanitized_static_report", "Publishes only the sanitized static report"), t("accounts_page.publisher_must_already_have_site_access", "Publisher must already have site access")],
            ],
        ),
    )
    (stage / "accounts/index.html").write_text(
        _page(title=t("accounts_page.accounts", "Accounts"), active="accounts", body=accounts, depth=1), encoding="utf-8"
    )


def _write_interpretation_evidence(
    stage: Path, inputs: ReportInputs, automatic_failure_counts: Counter[str]
) -> None:
    participants = inputs.participants
    selection = inputs.selection
    bundles = inputs.bundles
    review = inputs.review
    attempts = inputs.attempts
    attempts_sha256 = inputs.attempts_sha256
    verified_causes = inputs.verified_causes
    qualified_attempts = inputs.qualified_attempts
    configuration_count = len(participants.models) * len(participants.engines)
    claims = {
        "schema": "lex_browser_use_interpretation/1",
        "claims": [
            {"claim": t("interpretation_evidence.within_model_browser_pairs_on_the", "Within-model browser pairs on the review-qualified comparison set"), "basis": _paired_summary(qualified_attempts, participants)},
            {"claim": t("interpretation_evidence.failure_causes_follow_the_per_result", "Failure causes follow the per-result reviews bound to the current evidence"), "basis": verified_causes},
            {
                "claim": t("automatic_failure_figure.failed_rule_combinations_from_the_official", "Failed rule combinations from the official evaluator"),
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
                "claim": t("interpretation_evidence.formal_comparison_set_after_excluding_whole", "Formal comparison set after excluding whole tasks with verified external interference"),
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
                        ],
                        participants,
                    ),
                },
            },
            {
                "claim": (
                    t("interpretation_evidence.p50_performance_and_resources_on_tasks", "P50 performance and resources on tasks that all {value} configurations finally passed", value=t('interpretation_evidence.four', 'four') if configuration_count == 4 else t('interpretation_evidence.text', '{configuration_count}', configuration_count=configuration_count))
                ),
                "basis": dict(
                    zip(
                        ("task_count", "rows"),
                        _common_final_success_resource_rows(qualified_attempts, participants),
                        strict=True,
                    )
                ),
            },
            {"claim": t("interpretation_evidence.execution_evidence_integrity_review_coverage", "Execution-evidence integrity review coverage"), "basis": review.get("coverage") or {}},
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
    write_json(stage / "data/interpretation-evidence.json", claims, parents=True)


def _write_selection_and_results(stage: Path, inputs: ReportInputs) -> None:
    selection = inputs.selection
    public_attempts = inputs.public_attempts
    tasks = inputs.tasks
    write_json(
        stage / "data/selection.json",
        {
            "schema": "lex_browser_use_public_selection/1",
            "selection_sha256": selection.sha256,
            "tasks": tasks,
        }, parents=True,
    )
    (stage / "data/results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in public_attempts),
        encoding="utf-8",
    )


def _write_failure_cause_data(stage: Path, inputs: ReportInputs) -> None:
    failed_result_ids = inputs.failed_result_ids
    verified_causes = inputs.verified_causes
    failure_reviews = inputs.failure_reviews
    write_json(
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
        }, parents=True,
    )


def _write_trajectory_replay_data(stage: Path, inputs: ReportInputs) -> None:
    trajectory_replay_bridge = inputs.trajectory_replay_bridge
    if trajectory_replay_bridge is not None:
        write_json(stage / "data/trajectory-replay-bridge.json", trajectory_replay_bridge, parents=True)
        write_json(
            stage / "data/trajectory-replay-failure-review.json",
            _read_json(Path(str(_replay_engine_field(trajectory_replay_bridge, "_failure_review")[1].get("path") or ""))), parents=True,
        )


def _write_evidence_index(stage: Path, public_evidence_results: list[dict[str, Any]]) -> None:
    write_json(
        stage / "data/evidence-index.json",
        {
            "schema": "lex_browser_use_public_evidence_index/4",
            "results": public_evidence_results,
        }, parents=True,
    )


def _write_audit(stage: Path, inputs: ReportInputs) -> dict[str, Any]:
    profile = inputs.profile
    profile_path = inputs.profile_path
    participants = inputs.participants
    selection = inputs.selection
    bundles = inputs.bundles
    review = inputs.review
    attempts = inputs.attempts
    attempts_sha256 = inputs.attempts_sha256
    failed_result_ids = inputs.failed_result_ids
    verified_causes = inputs.verified_causes
    failure_reviews = inputs.failure_reviews
    summary_by_model = inputs.summary_by_model
    paired_by_model = inputs.paired_by_model
    excluded_task_ids = inputs.excluded_task_ids
    qualified_attempts = inputs.qualified_attempts
    qualified_tasks = inputs.qualified_tasks
    qualified_summary = inputs.qualified_summary
    audit = {
        "schema": "lex_browser_use_report/3",
        "profile_sha256": optional_file_sha256(profile_path),
        "template": template_identity("browser-use"),
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
            "paired_summary": _paired_summary(qualified_attempts, participants),
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
            "source_sha256": optional_file_sha256(failure_review_path(profile)),
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
    write_json(stage / "data/summary.json", audit, parents=True)
    write_json(stage / "report-data.json", audit, parents=True)
    return audit


def _write_public_environment(stage: Path, inputs: ReportInputs) -> None:
    profile = inputs.profile
    participants = inputs.participants
    bundles = inputs.bundles
    source_attempts = inputs.source_attempts
    protocol = inputs.protocol
    environment = inputs.environment
    preparation_receipt_count = inputs.preparation_receipt_count
    preparation_receipts_complete = inputs.preparation_receipts_complete
    versions, images = (
        environment.get("browser_versions") or {},
        environment.get("browser_images") or {},
    )
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
            "models": list(participants.models),
            "model_labels": participants.models,
            "baseline_engine": participants.baseline_engine,
            "max_task_executions": protocol.get("max_task_executions") or profile.get("max_task_executions"),
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
    write_json(stage / "data/environment.json", public_env, parents=True)


def _write_review_data(stage: Path, inputs: ReportInputs) -> None:
    review = inputs.review
    write_json(
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
        }, parents=True,
    )


def _write_publication_privacy(stage: Path) -> None:
    write_json(
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
        }, parents=True,
    )


def _write_pages(
    stage: Path, inputs: ReportInputs, automatic_failure_counts: Counter[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Write every page of the edition `t` selects, with the files those pages link to."""
    _write_stage_assets(stage)
    _write_home_page(stage, inputs, automatic_failure_counts)
    release_data = _write_changes_page(stage, inputs)
    _write_dataset_index(stage, inputs)
    _write_task_pages(stage, inputs)
    _write_methodology_page(stage, inputs)
    _write_analysis_page(stage, inputs)
    public_evidence_results = _write_result_pages(stage, inputs)
    _write_review_page(stage, release_data)
    _write_adoption_page(stage, inputs)
    engine_rows = _write_engine_pages(stage, inputs)
    _write_reproduce_page(stage, inputs, engine_rows)
    _write_accounts_page(stage, inputs)
    _write_fix_log(stage, inputs.fix_log)
    return release_data, public_evidence_results


def _write_english_edition(
    stage: Path, inputs: ReportInputs, automatic_failure_counts: Counter[str]
) -> None:
    """Render the pages again in English; only their HTML joins the report."""
    global t
    t = _EN
    try:
        with tempfile.TemporaryDirectory(dir=stage.parent) as scratch:
            _write_pages(Path(scratch), inputs, automatic_failure_counts)
            write_english_report(Path(scratch), stage)
    finally:
        t = _ZH


def _bind_build_version(stage: Path, audit: dict[str, Any]) -> None:
    from harness.reporting.versions import bind_build_version
    audit['browser_eval'] = bind_build_version(stage)
    write_json(stage / "data/summary.json", audit, parents=True)
    write_json(stage / "report-data.json", audit, parents=True)
    (stage / ".generated-report").write_text("browser-use-report/2\n", encoding="utf-8")


def _publish_stage(stage: Path, output: Path) -> None:
    parent = output.parent
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


def build_report(*, evidence: Path | Iterable[Path], output: Path, selection_path: Path, include_english: bool = False, language: str | None = None, profile_path: Path | None = None) -> Path:
    include_english = includes_english(language, include_english)
    inputs = _load_report_inputs(evidence, selection_path, profile_path)
    stage = _open_stage(output)
    try:
        automatic_failure_counts = _automatic_failure_counts(inputs.attempts)
        _, public_evidence_results = _write_pages(stage, inputs, automatic_failure_counts)
        _write_interpretation_evidence(stage, inputs, automatic_failure_counts)
        _write_selection_and_results(stage, inputs)
        _write_failure_cause_data(stage, inputs)
        _write_trajectory_replay_data(stage, inputs)
        _write_evidence_index(stage, public_evidence_results)
        audit = _write_audit(stage, inputs)
        _write_public_environment(stage, inputs)
        _write_review_data(stage, inputs)
        _write_publication_privacy(stage)
        if include_english:
            _write_english_edition(stage, inputs, automatic_failure_counts)
        write_language_contract(stage, include_english=include_english)
        if language is not None:
            select_report_language(stage, language)
        _bind_build_version(stage, audit)
        _publish_stage(stage, output)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return output / ("en/index.html" if language == "en" else "index.html")


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
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument(
        "--selection",
        type=Path,
        default=None,
    )
    add_language_argument(parser)
    parser.add_argument("--include-english", action="store_true", help="Generate both editions; --language selects the homepage")
    args = parser.parse_args()
    configuration = _read_json(args.profile)
    selection_path = report_selection_path(configuration, args.selection)
    print(build_report(evidence=args.evidence, output=args.output, selection_path=selection_path, include_english=args.include_english, language=args.language or primary_report_language(args.output), profile_path=args.profile))
