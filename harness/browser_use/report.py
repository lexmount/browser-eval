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
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from harness.web_fetch.report_style import STYLE as SHARED_STYLE

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
SHARED_FAILURE_DIAGNOSES = {
    int(task_id): diagnosis
    for task_id, diagnosis in json.loads(
        (RESEARCH_ROOT / "browser-use-failure-analysis.json").read_text(encoding="utf-8")
    )["tasks"].items()
}
MAC_EXPANSION = json.loads(
    (RESEARCH_ROOT / "webarena-mac-expansion.json").read_text(encoding="utf-8")
)
MAC_ENVIRONMENT_CANDIDATES = MAC_EXPANSION["environments"]
MAC_COUNTS = MAC_EXPANSION["official_counts"]


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
        lines.append(f'<span><b>{_e(label)}</b>{rendered}</span>')
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
    return f"{row['engine']}-{int(row['task_id'])}"


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
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["engine"]), int(row["task_id"]))].append(row)
    results: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: (item[0], item[1])):
        executions = sorted(grouped[key], key=lambda row: int(row["attempt"]))
        if [int(row["attempt"]) for row in executions] != list(range(1, len(executions) + 1)):
            raise ValueError(f"non-contiguous execution sequence for {key}")
        if len(executions) > 2:
            raise ValueError(f"more than two executions for {key}")
        legacy_primary = (protocol or {}).get("official_score_uses") == "primary attempt only"
        authoritative = dict(executions[0] if legacy_primary else executions[-1])
        authoritative["duration_ms"] = sum(
            float(row.get("duration_ms") or 0) for row in executions
        )
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
        weighted_duration = sum(float(row.get("duration_ms") or 0) for row in executions)
        if weighted_duration:
            authoritative["resource"]["cpu_average_utilization_pct"] = sum(
                float((row.get("resource") or {}).get("cpu_average_utilization_pct") or 0)
                * float(row.get("duration_ms") or 0)
                for row in executions
            ) / weighted_duration
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
    return {
        "id": _attempt_id(row),
        "run_id": str(row.get("run_id") or ""),
        "engine": str(row["engine"]),
        "task_id": int(row["task_id"]),
        "tier": str(row["tier"]),
        "score": float(row["score"]),
        "status": str(row.get("status") or ("success" if float(row["score"]) == 1 else "failure")),
        "duration_ms": round(float(row.get("duration_ms") or 0), 3),
        "action_count": int(row.get("action_count") or 0),
        "browser_tool_duration_ms": round(float(row.get("browser_tool_duration_ms") or 0), 3),
        "browser_tool_duration_by_action_ms": row.get(
            "browser_tool_duration_by_action_ms"
        )
        or {},
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
        "resource": {
            key: round(float(resource[key]), 3)
            for key in (
                "cpu_ms",
                "network_rx_bytes",
                "network_tx_bytes",
                "memory_peak_bytes",
                "memory_retained_bytes",
                "pids_current_peak",
                "cpu_average_utilization_pct",
            )
            if resource.get(key) is not None
        },
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] if lo == hi else values[lo] + (values[hi] - values[lo]) * (index - lo)


def _primary_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for engine in ENGINE_LABELS:
        logical = [r for r in rows if r["engine"] == engine]
        durations = [float(r.get("duration_ms") or 0) for r in logical]
        tool_durations = [float(r.get("browser_tool_duration_ms") or 0) for r in logical]
        tokens = [int((r.get("model_usage") or {}).get("total_tokens") or 0) for r in logical]
        passed = sum(float(r["score"]) == 1 for r in logical)
        result[engine] = {
            "completed": len(logical),
            "passed": passed,
            "score": passed / len(logical) if logical else None,
            "mean_duration_ms": statistics.fmean(durations) if durations else None,
            "p50_duration_ms": _percentile(durations, 0.50),
            "p95_duration_ms": _percentile(durations, 0.95),
            "mean_browser_tool_duration_ms": statistics.fmean(tool_durations)
            if tool_durations else None,
            "p50_browser_tool_duration_ms": _percentile(tool_durations, 0.50),
            "p95_browser_tool_duration_ms": _percentile(tool_durations, 0.95),
            "mean_tokens": statistics.fmean(tokens) if tokens else None,
            "p50_tokens": _percentile(tokens, 0.50),
            "p95_tokens": _percentile(tokens, 0.95),
        }
    return result


def _paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary = {
        (str(r["engine"]), int(r["task_id"])): float(r["score"]) == 1
        for r in rows
    }
    ids = sorted(
        task_id
        for task_id in {key[1] for key in primary}
        if ("chromium", task_id) in primary and ("moli", task_id) in primary
    )
    counts = {"both_pass": 0, "chromium_only": 0, "moli_only": 0, "both_fail": 0}
    for task_id in ids:
        c, m = primary[("chromium", task_id)], primary[("moli", task_id)]
        counts[
            "both_pass" if c and m else "chromium_only" if c else "moli_only" if m else "both_fail"
        ] += 1
    discordant = counts["chromium_only"] + counts["moli_only"]
    p = (
        min(
            1.0,
            2
            * sum(
                math.comb(discordant, k)
                for k in range(min(counts["chromium_only"], counts["moli_only"]) + 1)
            )
            / 2**discordant,
        )
        if discordant
        else (1.0 if ids else None)
    )
    return {"paired": len(ids), **counts, "mcnemar_exact_p": p}


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
        tasks = validate_against_official(selection, benchmark)
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
        (str(row["engine"]), int(row["task_id"])): float(row["score"]) == 1
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
                    engine: sum(
                        primary.get((engine, int(task["task_id"])), False) for task in selected
                    )
                    for engine in ENGINE_LABELS
                },
            }
        )
    return result


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
    results = {(str(row["engine"]), int(row["task_id"])): row for row in attempts}
    ordered = sorted(
        ENGINE_LABELS,
        key=lambda engine: (
            -sum(
                float(row["score"]) == 1
                for row in attempts
                if row["engine"] == engine
            ),
            ENGINE_LABELS[engine],
        ),
    )
    if len(ordered) < 2:
        return '<p class="muted-box">至少需要两个参评浏览器才能进行同题比较。</p>'

    rows = []
    for task in tasks:
        task_id = int(task["task_id"])
        present = [engine for engine in ordered if (engine, task_id) in results]
        passed = [engine for engine in present if float(results[(engine, task_id)]["score"]) == 1]
        failed = [engine for engine in present if engine not in passed]

        def outcome_links(engines: list[str], state: str) -> str:
            if not engines:
                return "—"
            return '<span class="outcome-list">' + "".join(
                f'<a class="outcome-pill {state}" href="results/{engine}-{task_id}.html">'
                f'{_e(ENGINE_LABELS[engine])}</a>'
                for engine in engines
            ) + "</span>"

        site = str(task["sites"][0])
        attrs = (
            f' data-pass="{_e(" ".join(passed))}" data-fail="{_e(" ".join(failed))}"'
            f' data-kind="{_e(task["task_type"])}"'
        )
        task_cell = (
            f'<div class="task-cell"><a href="datasets/{task_id}.html">#{task_id} · '
            f'{_e(task["rationale"])}</a><div class="task-meta">'
            f'<span class="track-badge">{_e(TIER_LABELS[task["tier"]])}</span>'
            f'<span class="track-badge">{_e(SITE_LABELS.get(site, site))}</span>'
            f'<span class="track-badge">{_e(task["task_type"])}</span></div></div>'
        )
        rows.append(
            f'<tr{attrs}><td>{task_cell}</td><td>{outcome_links(passed, "pass")}</td>'
            f'<td>{outcome_links(failed, "fail")}</td></tr>'
        )

    options = "".join(
        f'<option value="{_e(engine)}"{" selected" if engine == ordered[0] else ""}>'
        f'{_e(ENGINE_LABELS[engine])}</option>'
        for engine in ordered
    )
    checks = "".join(
        f'<label><input type="checkbox" value="{_e(engine)}" data-compare-engine'
        f'{" checked" if engine == ordered[1] else ""}> {_e(ENGINE_LABELS[engine])}</label>'
        for engine in ordered
    )
    kinds = '<option value="">全部类型</option>' + "".join(
        f'<option value="{kind}">{kind}</option>' for kind in ("RETRIEVE", "NAVIGATE", "MUTATE")
    )
    return (
        '<article class="browser-comparison"><header class="comparison-heading"><div>'
        '<h3>同题结果组合</h3><p>选择一个基准浏览器和一个或多个对比浏览器；浏览器标签可直达该题完整结果。</p>'
        '</div><b class="comparison-count" id="browser-compare-count" role="status" aria-live="polite"></b></header>'
        '<div class="comparison-controls">'
        f'<label>浏览器 A<select id="browser-a" data-browser-compare-control>{options}</select></label>'
        '<label>比较条件<select id="browser-relation" data-browser-compare-control>'
        '<option value="mixed">结果有分歧</option><option value="a_pass_others_fail">A 通过，其余失败</option>'
        '<option value="a_fail_others_pass">A 失败，其余通过</option><option value="all_pass">所选浏览器全部通过</option>'
        '<option value="all_fail" selected>所选浏览器全部失败</option><option value="exact_pass">仅所选浏览器通过</option>'
        f'</select></label><label>任务类型<select id="browser-kind" data-browser-compare-control>{kinds}</select></label></div>'
        '<fieldset class="comparison-browsers"><legend>对比浏览器（可多选）</legend>'
        f'<div class="browser-checks">{checks}</div></fieldset>'
        '<div class="comparison-result"><div class="table-wrap"><table class="comparison-table">'
        '<thead><tr><th>任务</th><th>通过浏览器</th><th>失败浏览器</th></tr></thead>'
        f'<tbody id="browser-compare-rows">{"".join(rows)}</tbody></table></div></div></article>'
    )


def _group_rows(
    attempts: list[dict[str, Any]], tasks: list[dict[str, Any]], field: str
) -> list[list[Any]]:
    meta = {int(t["task_id"]): t for t in tasks}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        value = meta[int(row["task_id"])][field]
        value = value[0] if isinstance(value, list) else value
        grouped[(str(value), str(row["engine"]))].append(row)
    result = []
    for value in sorted({key[0] for key in grouped}):
        line: list[Any] = [SITE_LABELS.get(value, TIER_LABELS.get(value, value))]
        passed_by_engine = {
            engine: sum(float(x["score"]) == 1 for x in grouped[(value, engine)])
            for engine in ENGINE_LABELS
        }
        winners = _best(passed_by_engine, higher=True)
        for engine in ENGINE_LABELS:
            items = grouped[(value, engine)]
            line.append(_winner(
                f"{passed_by_engine[engine]}/{len(items)}" if items else "—",
                won=engine in winners,
                label="同组通过数更高",
            ))
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
        for engine in ENGINE_LABELS:
            total = sum(
                float((row.get("browser_tool_duration_by_action_ms") or {}).get(action) or 0)
                for row in attempts
                if row["engine"] == engine
            )
            count = sum(
                int((row.get("browser_tool_count_by_action") or {}).get(action) or 0)
                for row in attempts
                if row["engine"] == engine
            )
            metrics[engine] = (total, count, total / count if count else None)
        average_winners = _best(
            {engine: values[2] for engine, values in metrics.items()},
            higher=False,
            display=lambda value: round(value / 1000, 3),
        )
        maximum = max((values[2] or 0 for values in metrics.values()), default=0)
        browser_rows = []
        for engine in ENGINE_LABELS:
            total, count, average = metrics[engine]
            width = 0 if average is None or maximum <= 0 else max(2.0, average / maximum * 100)
            average_text = "—" if average is None else f"{average / 1000:.3f} 秒"
            browser_rows.append(
                f'<div class="action-browser-row{" action-winner" if engine in average_winners else ""}"><div class="action-browser-head">'
                f'<b>{_e(ENGINE_LABELS[engine])}</b>{_winner(average_text, won=engine in average_winners, label="单次均值更低")}'
                f'</div><div class="action-bar" aria-hidden="true"><i style="width:{width:.1f}%"></i></div>'
                f'<div class="action-browser-meta">总耗时 {total / 1000:.1f} 秒 · {count} 次调用</div></div>'
            )
        cards.append(
            '<article class="action-card"><header><code>' + _e(action) + '</code>'
            '<span>单次调用墙钟时间</span></header>' + "".join(browser_rows) + "</article>"
        )
    return '<div class="action-card-grid">' + "".join(cards) + "</div>"


def _summary_comparison_rows(summary: dict[str, dict[str, Any]]) -> list[list[Any]]:
    higher_metrics = {
        "passed": _best({engine: summary[engine]["passed"] for engine in ENGINE_LABELS}, higher=True),
        "score": _best({engine: summary[engine]["score"] for engine in ENGINE_LABELS}, higher=True),
    }
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
    lower_metrics = {
        name: _best(
            {engine: summary[engine][name] for engine in ENGINE_LABELS},
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
    for engine, label in ENGINE_LABELS.items():
        current = summary[engine]
        rows.append(
            [
                label,
                _winner(
                    f'{current["passed"]}/{current["completed"]}',
                    won=engine in higher_metrics["passed"],
                    label="通过数更高",
                ),
                _winner(
                    _pct(current["score"]),
                    won=engine in higher_metrics["score"],
                    label="通过率更高",
                ),
                _metric_stack(
                    [
                        ("均值", _seconds(current["mean_duration_ms"]), engine in lower_metrics["mean_duration_ms"]),
                        ("P50", _seconds(current["p50_duration_ms"]), engine in lower_metrics["p50_duration_ms"]),
                        ("P95", _seconds(current["p95_duration_ms"]), engine in lower_metrics["p95_duration_ms"]),
                    ]
                ),
                _metric_stack(
                    [
                        ("均值", _seconds(current["mean_browser_tool_duration_ms"]), engine in lower_metrics["mean_browser_tool_duration_ms"]),
                        ("P50", _seconds(current["p50_browser_tool_duration_ms"]), engine in lower_metrics["p50_browser_tool_duration_ms"]),
                        ("P95", _seconds(current["p95_browser_tool_duration_ms"]), engine in lower_metrics["p95_browser_tool_duration_ms"]),
                    ]
                ),
                _metric_stack(
                    [
                        ("均值", _number(current["mean_tokens"]), engine in lower_metrics["mean_tokens"]),
                        ("P50", _number(current["p50_tokens"]), engine in lower_metrics["p50_tokens"]),
                        ("P95", _number(current["p95_tokens"]), engine in lower_metrics["p95_tokens"]),
                    ]
                ),
            ]
        )
    return rows


REPORT_JS = """function theme(){const r=document.documentElement,n=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=n;try{localStorage.setItem('browser-use-theme',n)}catch(e){}}
function updateViewUrl(){const u=new URL(location.href),q=document.querySelector('[data-search]');[...u.searchParams.keys()].filter(k=>k==='q'||k.startsWith('f_')).forEach(k=>u.searchParams.delete(k));if(q?.value)u.searchParams.set('q',q.value);document.querySelectorAll('[data-filter]').forEach(c=>{if(c.value)u.searchParams.set('f_'+c.dataset.filter,c.value)});history.replaceState(null,'',u.pathname+(u.search?'?'+u.searchParams.toString():'')+u.hash)}
function filterRows(save=true){const controls=[...document.querySelectorAll('[data-filter]')],query=(document.querySelector('[data-search]')?.value||'').trim().toLowerCase();let visible=0,total=0;document.querySelectorAll('tbody tr[data-tier]').forEach(r=>{total++;const hidden=Boolean(controls.some(c=>c.value&&r.dataset[c.dataset.filter]!==c.value)||(query&&!String(r.dataset.search||r.textContent).toLowerCase().includes(query)));r.hidden=hidden;if(!hidden)visible++});document.querySelectorAll('[data-visible-count]').forEach(x=>x.textContent=visible+' / '+total);if(save)updateViewUrl()}
function applySort(t,col,dir){const b=t?.tBodies[0];if(!b)return;[...b.rows].sort((a,z)=>{const av=a.cells[col]?.dataset.sort||a.cells[col]?.textContent||'',zv=z.cells[col]?.dataset.sort||z.cells[col]?.textContent||'';const an=Number(av.replace(/[^0-9.-]/g,'')),zn=Number(zv.replace(/[^0-9.-]/g,''));const cmp=!Number.isNaN(an)&&!Number.isNaN(zn)?an-zn:av.localeCompare(zv,'zh-CN');return dir==='asc'?cmp:-cmp}).forEach(r=>b.appendChild(r));t.dataset.col=String(col);t.dataset.dir=dir}
function sortTable(id,col){const t=document.getElementById(id);if(!t)return;const dir=t.dataset.col==col&&t.dataset.dir==='asc'?'desc':'asc';applySort(t,col,dir);const u=new URL(location.href);u.searchParams.set('sort',id+':'+col+':'+dir);history.replaceState(null,'',u.pathname+'?'+u.searchParams.toString()+u.hash)}
function browserOutcomes(row,key){return new Set((row.dataset[key]||'').split(' ').filter(Boolean))}
function compareBrowsers(save=true){const a=document.getElementById('browser-a'),relation=document.getElementById('browser-relation'),kind=document.getElementById('browser-kind'),count=document.getElementById('browser-compare-count');if(!a||!relation||!kind||!count)return;const boxes=[...document.querySelectorAll('[data-compare-engine]')];boxes.forEach(box=>{box.disabled=box.value===a.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});const others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value),selected=[a.value,...others];let shown=0;document.querySelectorAll('#browser-compare-rows tr').forEach(row=>{const pass=browserOutcomes(row,'pass'),fail=browserOutcomes(row,'fail');let match=others.length>0&&(!kind.value||row.dataset.kind===kind.value);if(match)match={mixed:selected.some(x=>pass.has(x))&&selected.some(x=>fail.has(x)),a_pass_others_fail:pass.has(a.value)&&others.every(x=>fail.has(x)),a_fail_others_pass:fail.has(a.value)&&others.every(x=>pass.has(x)),all_pass:selected.every(x=>pass.has(x)),all_fail:selected.every(x=>fail.has(x)),exact_pass:selected.every(x=>pass.has(x))&&pass.size===selected.length}[relation.value];row.hidden=!match;if(match)shown++});count.textContent=others.length?(document.documentElement.lang==='en'?shown+' tasks found':'找到 '+shown+' 个任务'):(document.documentElement.lang==='en'?'Select at least one comparison browser':'请至少选择一个对比浏览器');if(save){const u=new URL(location.href);['b_a','b_relation','b_kind','b_others'].forEach(k=>u.searchParams.delete(k));u.searchParams.set('b_a',a.value);u.searchParams.set('b_relation',relation.value);if(kind.value)u.searchParams.set('b_kind',kind.value);if(others.length)u.searchParams.set('b_others',others.join(','));history.replaceState(null,'',u.pathname+(u.search?'?'+u.searchParams.toString():'')+u.hash)}}
function applyAnchor(){if(!location.hash)return;const id=decodeURIComponent(location.hash.slice(1)),target=document.getElementById(id);if(target?.scrollIntoView)requestAnimationFrame(()=>target.scrollIntoView({block:'start'}))}
function hasOption(control,value){return !control?.options||[...control.options].some(option=>option.value===value)}
function restoreView(){const p=new URLSearchParams(location.search),q=document.querySelector('[data-search]');if(q&&p.has('q'))q.value=p.get('q')||'';document.querySelectorAll('[data-filter]').forEach(c=>{const v=p.get('f_'+c.dataset.filter);if(v!==null&&hasOption(c,v))c.value=v});const a=document.getElementById('browser-a'),relation=document.getElementById('browser-relation'),kind=document.getElementById('browser-kind'),boxes=[...document.querySelectorAll('[data-compare-engine]')],allowed=new Set(boxes.map(box=>box.value)),others=new Set((p.get('b_others')||'').split(',').filter(value=>allowed.has(value)));if(a&&p.has('b_a')&&hasOption(a,p.get('b_a')))a.value=p.get('b_a');if(relation&&p.has('b_relation')&&hasOption(relation,p.get('b_relation')))relation.value=p.get('b_relation');if(kind&&p.has('b_kind')&&hasOption(kind,p.get('b_kind')))kind.value=p.get('b_kind');boxes.forEach(box=>{if(p.has('b_others'))box.checked=others.has(box.value)});if(boxes.length>1&&!boxes.some(box=>box.checked&&box.value!==a?.value)){const fallback=boxes.find(box=>box.value!==a?.value);if(fallback)fallback.checked=true}const s=(p.get('sort')||'').split(':');if(s.length===3)applySort(document.getElementById(s[0]),Number(s[1]),s[2]);filterRows(false);compareBrowsers(false);try{if(!location.hash){const y=sessionStorage.getItem('browser-use-scroll:'+location.pathname+location.search);if(y!==null)requestAnimationFrame(()=>scrollTo(0,Number(y)||0))}}catch(e){}}
document.addEventListener('DOMContentLoaded',()=>{try{document.documentElement.dataset.theme=localStorage.getItem('browser-use-theme')||'light'}catch(e){}restoreView();applyAnchor();document.querySelectorAll('[data-filter]').forEach(x=>x.addEventListener('change',()=>filterRows()));document.querySelectorAll('[data-search]').forEach(x=>x.addEventListener('input',()=>filterRows()));document.querySelectorAll('[data-browser-compare-control],[data-compare-engine]').forEach(x=>x.addEventListener('change',()=>compareBrowsers()))});addEventListener('pagehide',()=>{try{sessionStorage.setItem('browser-use-scroll:'+location.pathname+location.search,String(scrollY))}catch(e){}});"""
REPORT_STYLE = (
    SHARED_STYLE
    + """
.hero h1,.publication-article h1,.publication-article h2{overflow-wrap:break-word;word-break:normal}
.callout strong{margin-right:.3em}
.top nav{gap:18px;flex-wrap:nowrap;overflow-x:auto;overscroll-behavior-inline:contain;scrollbar-width:thin}.top nav>a,.top nav>button{flex:0 0 auto}.top nav .brand{font-weight:850;color:var(--ink)}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:8px}.top .theme-toggle{margin-left:auto}.page-main{min-height:78vh}.hero.compact{padding-bottom:22px}.hero.compact h1{font-size:clamp(34px,4.5vw,52px)}.fine{font-size:13px;color:var(--muted)}.result-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.result-grid .card{border-top:3px solid var(--accent)}.overview-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.overview-card{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:20px;min-width:0}.overview-card h3{margin:8px 0}.audit-strip{display:grid;gap:10px}.audit-strip .callout{margin:0}.filters,.pair-controls{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin:18px 0}.filters select,.filters input,.pair-controls select{padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink)}.filters input{min-width:260px}.comparison-count{margin-left:auto;padding:8px 12px;border-radius:999px;background:var(--wash);color:var(--accent);font-weight:750}.status-pass{color:var(--good);font-weight:750}.status-fail{color:var(--bad);font-weight:750}.status-unproven{color:var(--warn,var(--bad));font-weight:750}.flow{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px;list-style:none;padding:0}.flow li{padding:14px;border:1px solid var(--line);border-radius:11px;background:var(--panel);font-size:13px;min-width:0}.flow li+li:before{content:'→';color:var(--accent);margin-right:7px}.method-figure{margin:24px 0}.method-figure img{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:16px;background:#f6faff}.method-figure figcaption{margin-top:10px;color:var(--muted);font-size:13px;line-height:1.55}.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.detail-grid .kv{min-height:92px;min-width:0}.page-links{display:flex;gap:10px;flex-wrap:wrap}.page-links a{border:1px solid var(--line);background:var(--panel);padding:8px 12px;border-radius:9px}.rank-list{counter-reset:rank}.rank-item{counter-increment:rank;display:grid;grid-template-columns:54px minmax(0,1fr);gap:16px;padding:20px 0;border-top:1px solid var(--line)}.rank-item:before{content:counter(rank);font-size:34px;font-weight:850;color:var(--accent)}.rank-item h3{margin:4px 0}.article-entry{border-left:4px solid var(--accent)}.source-list li{margin:9px 0}.table-wrap{margin:18px 0}.table-wrap table{min-width:760px}#task-table{min-width:1280px}#pair-table{min-width:1180px}.table-wrap caption{text-align:left;padding:10px 12px;color:var(--muted);caption-side:bottom}.footer{border-top:1px solid var(--line)}.evidence-pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.evidence-card{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:16px;min-width:0}.evidence-card h3{margin-top:0}.json-view,.log-view{white-space:pre-wrap;overflow-wrap:anywhere;max-height:540px;overflow:auto;padding:14px;border:1px solid var(--line);border-radius:9px;background:var(--bg);font-size:12px;line-height:1.55}.trajectory{list-style:none;padding:0;counter-reset:step}.trajectory>li{counter-increment:step;display:grid;grid-template-columns:46px minmax(0,1fr);gap:12px;padding:15px 0;border-top:1px solid var(--line)}.trajectory>li:before{content:counter(step);display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--accent);color:white;font-weight:800}.trajectory summary{cursor:pointer;font-weight:750}.rule-fail{border-left:4px solid var(--bad)}.rule-pass{border-left:4px solid var(--good)}.count-note{margin-left:auto;color:var(--muted);font-size:13px}.badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--wash);font-size:12px;margin-right:6px}.muted-box{padding:14px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}@media(max-width:900px){.result-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.overview-grid{grid-template-columns:1fr}.flow{grid-template-columns:repeat(3,minmax(0,1fr))}.evidence-pair{grid-template-columns:1fr}.grid>.card{grid-column:1/-1!important}}@media(max-width:600px){.wrap{width:calc(100% - 28px)}.top nav{gap:14px}.result-grid,.flow{grid-template-columns:1fr}.flow li+li:before{content:'↓';display:block}.hero{padding-top:28px}.hero.compact h1{font-size:30px;line-height:1.18}.filters input{min-width:0;width:100%}.comparison-count{margin-left:0}.rank-item{grid-template-columns:36px minmax(0,1fr)}}@media print{.top,.filters,.pair-controls{display:none!important}.card,.table-wrap{box-shadow:none}.json-view,.log-view{max-height:none;overflow:visible}.evidence-pair{grid-template-columns:1fr}}
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
#result-table,#engine-result-table,#change-table{min-width:1080px}
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
.browser-comparison{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:24px;box-shadow:0 12px 32px rgba(25,45,70,.06)}.comparison-heading{display:flex;align-items:start;justify-content:space-between;gap:24px;margin-bottom:22px}.comparison-heading h3{margin:0 0 6px;font-size:22px}.comparison-heading p{margin:0;color:var(--muted);line-height:1.65}.comparison-controls{display:grid;grid-template-columns:minmax(170px,1fr) minmax(240px,1.4fr) minmax(170px,1fr);gap:14px}.comparison-controls label{display:grid;gap:6px;font-weight:750}.comparison-controls select{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink);font:inherit}.comparison-browsers{border:0;padding:0;margin:20px 0 0}.comparison-browsers legend{font-weight:750;margin-bottom:10px}.browser-checks{display:flex;gap:8px;flex-wrap:wrap}.browser-checks label{display:flex;align-items:center;gap:7px;padding:8px 11px;border:1px solid var(--line);border-radius:999px;background:var(--bg);cursor:pointer}.browser-checks label.disabled{opacity:.45;cursor:not-allowed}.browser-checks input{accent-color:var(--accent)}.comparison-result{margin-top:20px}.table-wrap .comparison-table{min-width:720px;table-layout:fixed}.comparison-table th:first-child,.comparison-table td:first-child{width:48%}.comparison-table th:nth-child(2),.comparison-table td:nth-child(2),.comparison-table th:nth-child(3),.comparison-table td:nth-child(3){width:26%}.task-cell a{font-weight:750}.task-meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:7px}.track-badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);color:var(--muted);font-size:12px;white-space:nowrap}.outcome-list{display:flex;gap:6px;flex-wrap:wrap}.outcome-pill{display:inline-block;padding:5px 8px;border-radius:8px;font-size:13px;text-decoration:none}.outcome-pill.pass{background:var(--success-bg);color:var(--good)}.outcome-pill.fail{background:var(--failure-bg);color:var(--bad)}
.action-card-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.action-card{border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:18px;min-width:0}.action-card>header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.action-card>header code{font-size:16px;font-weight:800}.action-card>header span{font-size:12px;color:var(--muted)}.action-browser-row{padding:12px 0;border-top:1px solid var(--line)}.action-browser-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.action-bar{height:7px;border-radius:999px;background:var(--panel2);overflow:hidden;margin:8px 0}.action-bar i{display:block;height:100%;border-radius:inherit;background:var(--accent)}.action-browser-row.action-winner .action-bar i{background:var(--good)}.action-browser-meta{font-size:12px;color:var(--muted)}
.attempt-summary{display:grid;grid-template-columns:minmax(270px,1.4fr) repeat(4,minmax(135px,.7fr));gap:12px;align-items:stretch}.attempt-verdict{padding:20px 22px;border:1px solid var(--line);border-left:5px solid var(--good);border-radius:12px;background:var(--success-bg)}.attempt-verdict.failure{border-left-color:var(--bad);background:var(--failure-bg)}.attempt-verdict .status-line{display:block;font-size:24px;font-weight:850;margin-bottom:6px}.attempt-verdict p{margin:0;line-height:1.65}.attempt-jumps{display:flex;gap:14px;flex-wrap:wrap;margin-top:14px;font-weight:750}.attempt-fact{padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow-wrap:anywhere}.attempt-fact b{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}.attempt-secondary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:12px}
.task-goal{padding:22px 24px;border-radius:16px;background:var(--wash);border:1px solid var(--line);margin-bottom:18px}.task-goal span,.rule-index{display:block;color:var(--accent);font-size:12px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.task-goal p{font-size:20px;font-weight:750;line-height:1.6;margin:7px 0}.task-goal small{color:var(--muted)}.verification-list{display:grid;gap:14px}.verification-rule{padding:20px 22px;border:1px solid var(--line);border-left:5px solid var(--good);border-radius:14px;background:var(--panel);min-width:0}.verification-rule.rule-fail{border-left-color:var(--bad)}.verification-rule>header{display:flex;align-items:start;justify-content:space-between;gap:18px}.verification-rule h3{margin:4px 0 0;font-size:19px}.rule-verdict{flex:0 0 auto;padding:5px 10px;border-radius:999px;font-size:12px;font-weight:850}.rule-verdict.pass{background:var(--success-bg);color:var(--good)}.rule-verdict.fail{background:var(--failure-bg);color:var(--bad)}.rule-purpose{color:var(--muted);margin:12px 0 6px}.rule-summary{font-size:17px;font-weight:750;margin:0 0 14px}.verification-rule details{border-top:1px solid var(--line);padding-top:12px;margin-top:10px}.verification-rule summary{cursor:pointer;font-weight:750}.compact-evidence{margin-top:12px}.compact-evidence .json-view{max-height:360px}
.page-main{overflow-x:clip}
.section[id]{scroll-margin-top:76px}
@media(max-width:980px){.attempt-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.attempt-verdict{grid-column:1/-1}.attempt-secondary{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:760px){.browser-comparison{padding:18px}.comparison-heading{display:block}.comparison-count{display:inline-block;margin-top:12px}.comparison-controls{grid-template-columns:1fr}.action-card-grid{grid-template-columns:1fr}}
@media(max-width:600px){.method-figure img{min-width:900px}.table-wrap{margin-left:-4px;margin-right:-4px}.json-view,.log-view{font-size:13px}.review-round header{display:block}.review-status{display:inline-block;margin-top:8px}.review-fields,.attempt-summary,.attempt-secondary{grid-template-columns:1fr}}
"""
)
NAV = [
    ("", "Browser Use", "home"),
    ("datasets/", "评测集", "datasets"),
    ("methodology/", "评测方法", "methodology"),
    ("analysis/", "结果分析", "analysis"),
    ("review/", "核验记录", "review"),
    ("guide/", "报告解读", "guide"),
    ("adoption/", "参评浏览器", "adoption"),
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


def _failed_rule_labels(result: dict[str, Any] | None) -> list[str]:
    return [
        f"规则 {item['index']} {item['name']}"
        for item in evaluator_summary(result)
        if float(item.get("score") or 0) < 1
    ]


def _failure_brief(result: dict[str, Any] | None, row: dict[str, Any]) -> str:
    labels = _failed_rule_labels(result)
    if labels:
        return f'{len(labels)} 项规则未通过：{"、".join(labels)}。'
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


def _verification_html(
    task: dict[str, Any], result: dict[str, Any] | None, response: Any
) -> str:
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
                f'<details><summary>查看 evaluator 技术明细</summary>{_json_pre(technical)}</details>'
                if technical
                else ""
            )
            + "</article>"
        )
    return (
        '<div class="task-goal"><span>任务目标</span><p>'
        + _e(task.get("intent") or "—")
        + '</p><small>Agent 可自主选择 UI 路径；计分只看以下最终回答与必要网络副作用。</small></div>'
        + '<div class="verification-list">'
        + "".join(cards)
        + "</div>"
    )


def _trajectory_html(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return '<p class="muted-box">没有可展示的 BrowserTool 调用。</p>'
    rows = []
    for step in steps:
        duration = step.get("duration_seconds")
        meta = f"Turn {step.get('turn') or '—'} · {_e(step.get('status') or 'unknown')}"
        if duration is not None:
            meta += f" · {float(duration):.2f} 秒"
        rows.append(
            "<li><div><details><summary>"
            + _e(trajectory_label(step))
            + f'</summary><p class="fine">{meta}</p><h4>调用参数</h4>'
            + _json_pre(step.get("arguments"))
            + "<h4>执行输出节选</h4>"
            + f'<pre class="log-view">{_e(result_excerpt(step))}</pre>'
            + "</details></div></li>"
        )
    return '<ol class="trajectory">' + "".join(rows) + "</ol>"


def _artifact_links(attempt_id: str) -> str:
    root = f"artifacts/{attempt_id}"
    return (
        '<div class="page-links">'
        f'<a href="{root}/agent-response.json" download>最终回答 JSON</a>'
        f'<a href="{root}/evaluation.json" download>Evaluator 明细 JSON</a>'
        f'<a href="{root}/trajectory.json" download>动作轨迹 JSON</a>'
        f'<a href="{root}/agent-events.json" download>脱敏事件日志 JSON</a>'
        f'<a href="{root}/evidence-metadata.json" download>原始证据哈希</a>'
        "</div>"
    )


def _article_body(
    paired: dict[str, Any], attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]
) -> str:
    summary = _primary_summary(attempts)
    chromium, moli = summary["chromium"], summary["moli"]
    failures = [
        [
            f"#{task_id}",
            diagnosis["mismatch"],
            diagnosis["confirmed_failure_point"],
            diagnosis["direct_evidence"],
            diagnosis["root_cause_boundary"],
        ]
        for task_id, diagnosis in SHARED_FAILURE_DIAGNOSES.items()
    ]
    return (
        '<article class="publication-article"><header class="essay-head"><div class="eyebrow">Browser Use 实测解读</div>'
        f"<h1>{chromium['passed']} 比 {moli['passed']} 不是“内核等价”：当前样本没有产生分歧</h1>"
        f'<p class="lead">同一 CowAgent 与同一模型替换浏览器后，{paired["paired"]} 个配对任务没有一个出现单边胜出。这个结果证明对照流程跑通，却没有为内核优劣提供方向性证据。</p>'
        '<p class="source-note">测量口径：WebArena‑Verified 1.2.3；每题×浏览器一个最终逻辑结果；官方确定性 evaluator；无 LLM judge。</p></header>'
        "<section><h2>一、最重要的结果不是平分，而是零个不一致配对</h2>"
        f"<p>Chromium 与 Moli 分别通过 {chromium['passed']}/{chromium['completed']} 和 {moli['passed']}/{moli['completed']}。逐题配对后，{paired['both_pass']} 题共同通过、{paired['both_fail']} 题共同失败，两个浏览器单边通过都是 0。由于固定样本里没有不一致任务，这批数据不能估计浏览器的方向性效应，也不对 WebArena‑Verified 全集作统计外推。</p>"
        + _table(
            ["配对结果", "任务数", "可支持的判断"],
            [
                ["两者都通过", paired["both_pass"], "当前组合可完成"],
                ["仅 Chromium", paired["chromium_only"], "本轮未观察到"],
                ["仅 Moli", paired["moli_only"], "本轮未观察到"],
                ["两者都失败", paired["both_fail"], "优先排查共同链路"],
            ],
            caption="表 1｜30 个任务的逐题最终结果配对。",
        )
        + f"<p>严谨表述只能是“本轮没有观察到成功率差异”，不能写成“两种内核等价”。{paired['paired']} 题对小效应检出力有限；更关键的是，样本没有诱发内核分歧。</p></section>"
        "<section><h2>二、五个共同失败，具体失败在哪里</h2>"
        f"<p>两个浏览器共享 CowAgent、模型、提示与工具合同、站点状态、30 步上限和 evaluator，只有浏览器实现不同。逐条证据只能确认验收链路中的首个失败点；除 506 题明确触发步数上限外，记录不足以把错误进一步归因到模型、CowAgent 策略或提示合同中的某一项。</p>"
        + _table(
            ["任务", "官方判定差异", "已确认失败点", "直接证据", "尚不能判定"],
            failures,
            caption="表 2｜只陈述最终回答、官方 evaluator 与执行轨迹能够直接证明的事实；不修改官方分数。",
        )
        + "<p><strong>结论：</strong>#68、#124、#142 和 #168 的确定事实是最终答案构造或状态协议错误；#506 的确定事实是达到 30 步上限时尚未产生付款提交和成功页事件。站点错误、evaluator 实现错误、答案合同错误以及模型内部决策原因，在这批证据中均未被证明。因而本轮不能单独判定为“模型能力不行”，只能判定当前 CowAgent、模型、提示与工具合同的组合在这些验收点失败。</p></section><section><h2>三、简单、中等、困难都覆盖了，但“难度”只是操作长度</h2><p>WebArena‑Verified 没有官方 easy/medium/hard 三档。本项目在运行前按最少可见 UI 操作数注册为 1–4、5–7、8+ 三档，配额 5/10/15。它控制操作链覆盖，不等同于推理难度。</p>"
        + _table(
            ["本项目分层", "Chromium", "Moli"],
            _group_rows(attempts, tasks, "tier"),
            caption="表 3｜各层级最终通过数/任务数。",
        )
        + _table(
            ["站点", "Chromium", "Moli"],
            _group_rows(attempts, tasks, "sites"),
            caption="表 4｜四个可重置自托管站点。",
        )
        + f'</section><section><h2>四、选择 WebArena‑Verified 是为了归因，不是因为它最流行</h2><p>Mind2Web 家族采用面更广，但原版主要评离线动作预测；Online‑Mind2Web 和 WebVoyager贴近实时网页，却引入网页漂移和模型裁判。WebArena‑Verified 可以自托管、逐题重置，并用结构化最终答案和必要网络副作用做程序化验收，更适合“只替换浏览器”的 A/B。</p><p>代价也明确：部署重，环境兼容本身会成为工程变量；官方 evaluator 不衡量路径效率，也不能覆盖开放互联网泛化。</p></section><section><h2>五、下一轮先扩展四个已验证站点</h2><p>官方包有 {MAC_COUNTS["full"]} 题和 {MAC_COUNTS["hard"]} 题 hard 子集，没有官方 179 题版本；179 只能是另行声明的自定义样本。当前 Mac 已实跑的四个站点可覆盖 {MAC_COUNTS["current_four_sites_single_site"]} 个单站任务；再计入只依赖这四站的跨站任务，共有 {MAC_COUNTS["current_four_sites_including_cross_site"]} 个候选，其中 hard 子集有 {MAC_COUNTS["current_four_sites_hard_including_cross_site"]} 个。下一轮应在这些候选中先做登录、重置、健康检查与 evaluator 预检，再按简单、中等、困难和内核敏感交互分层抽样。</p><p>Wikipedia 需要约 100 GB 数据，Map 需要约 60 GB 数据且当前公开镜像为 amd64；二者可作为后续扩展，但没有理由成为本机下一批的前置阻塞。</p><p>保留 CowAgent、模型、reasoning=low 和每题最多两次执行的固定协议，优先加入长页面、复杂编辑器、弹窗、多标签页、上传下载等更可能诱发浏览器差异的任务。实时网站另开 Online‑Mind2Web / WebVoyager 泛化轨道，LLM judge 与人工抽检单列。</p><div class="essay-note"><h3>决策边界</h3><p>本轮支持“流程可比、当前样本无方向性差异”；不支持“内核等价”“Moli 已全面替代 Chromium”或“端到端耗时就是纯内核速度”。Astra 获权后必须形成独立运行。</p></div></section></article>'
    )


def _standalone_article(body: str) -> str:
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Browser Use 实测解读</title><style>{REPORT_STYLE}</style></head><body class="standalone-article"><main class="essay"><div class="wrap">{body}</div></main></body></html>'


def _filters(*, attempts: bool = False, dataset: bool = False) -> str:
    engine_options = "".join(
        f'<option value="{_e(engine)}">{_e(label)}</option>'
        for engine, label in ENGINE_LABELS.items()
    )
    extra = (
        f'<label>浏览器 <select data-filter="engine"><option value="">全部</option>{engine_options}</select></label>'
        '<label>失败环节 <select data-filter="failure"><option value="">全部</option><option value="none">无</option><option value="response">最终回答</option><option value="network">网络副作用</option><option value="mixed">多项</option></select></label>'
        if attempts
        else ""
    )
    result_filter = "" if dataset else (
        '<label>结果 <select data-filter="result"><option value="">全部</option>'
        '<option value="pass">通过</option><option value="fail"'
        + (" selected" if attempts else "")
        + ">失败</option></select></label>"
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


def _attempt_table(attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> str:
    task_meta = {int(task["task_id"]): task for task in tasks}
    rows = []
    for row in attempts:
        p = _public_attempt(row)
        result = "pass" if p["score"] == 1 else "fail"
        result_record = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        failed_names = {
            item["name"]
            for item in evaluator_summary(result_record)
            if float(item.get("score") or 0) < 1
        }
        failure = (
            "none"
            if not failed_names
            else "mixed"
            if len(failed_names) > 1
            else "network"
            if "NetworkEventEvaluator" in failed_names
            else "response"
        )
        task = task_meta.get(p["task_id"], {})
        values = [
            Safe(f'<a href="results/{p["id"]}.html">{p["id"]}</a>'),
            ENGINE_LABELS[p["engine"]],
            p["task_id"],
            TIER_LABELS[p["tier"]],
            _status(row),
            _seconds(p["duration_ms"]),
            _seconds(p["browser_tool_duration_ms"]),
            p["action_count"],
            _number(p["model_usage"]["total_tokens"]),
        ]
        cells = "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in values)
        rows.append(
            f'<tr data-tier="{p["tier"]}" data-site="{(task.get("sites") or [""])[0]}" '
            f'data-kind="{task.get("task_type") or _task_type(p["task_id"])}" data-result="{result}" '
            f'data-engine="{p["engine"]}" data-failure="{failure}" '
            f'data-search="{_e(" ".join([p["id"], str(task.get("intent") or ""), _failure_diagnosis(result_record, row)]))}">{cells}</tr>'
        )
    headers = "".join(
        f"<th>{_e(x)}</th>"
        for x in [
            "结果",
            "浏览器",
            "任务",
            "层级",
            "判定",
            "Agent 链路累计耗时",
            "BrowserTool 累计耗时",
            "动作数",
            "Tokens",
        ]
    )
    return (
        _filters(attempts=True)
        + f'<div class="table-wrap"><table id="result-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _engine_attempts(
    engine: str, attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]
) -> str:
    meta = {int(task["task_id"]): task for task in tasks}
    rows = []
    for row in attempts:
        if row["engine"] != engine:
            continue
        task = meta[int(row["task_id"])]
        result = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        rows.append(
            [
                Safe(f'<a href="results/{_attempt_id(row)}.html">{_attempt_id(row)}</a>'),
                task["rationale"],
                TIER_LABELS[task["tier"]],
                _status(row),
                _seconds(float(row.get("duration_ms") or 0)),
                _seconds(float(row.get("browser_tool_duration_ms") or 0)),
                int(row.get("action_count") or 0),
                _failure_diagnosis(result, row) if float(row["score"]) < 1 else "—",
            ]
        )
    return _table(
        ["结果", "任务", "层级", "判定", "Agent 链路耗时", "BrowserTool 耗时", "动作数", "失败原因"],
        rows,
        table_id="engine-result-table",
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _release_details(release: dict[str, Any]) -> str:
    rows = release.get("changes") or []
    details = (
        '<details><summary>查看修正依据、处理与影响</summary>'
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
        f'<h3>{_e(release.get("title") or "未命名版本")}</h3>'
        f'<p>{_e(release.get("summary") or "")}</p>{details}'
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
            f'<div><b>{_e(label)}</b><p>{_e(item.get(key) or "—")}</p></div>'
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
            f'<h3>{_e(item.get("title") or "未命名核验")}</h3></div>'
            f'<span class="review-status">{_e(item.get("status") or "未记录")}</span>'
            f'</header><div class="review-fields">{fields}</div></article>'
        )
    return "".join(cards)


def build_report(*, evidence: Path, output: Path, selection_path: Path) -> Path:
    profile = _read_json(Path("evaluation/browser-use/current-report.json"))
    _configure_engine_labels(profile)
    selection = load_pilot(selection_path)
    source_attempts = _read_attempts(evidence)
    manifest, config, review = (
        _read_json(evidence / name)
        for name in ("manifest.json", "run-config.json", "result-review.json")
    )
    protocol, environment = manifest.get("protocol") or {}, manifest.get("environment") or {}
    attempts = _logical_results(source_attempts, protocol)
    public_attempts = [_public_attempt(r) for r in attempts]
    official = (manifest.get("environment") or {}).get("official_evaluator") or {}
    tasks = _resolve_tasks(
        selection, expected_data_checksum=str(official.get("data_checksum") or "") or None
    )
    summary, paired = _primary_summary(attempts), _paired_summary(attempts)
    measured_solvable_ids = {
        int(row["task_id"]) for row in attempts if float(row.get("score") or 0) == 1
    }
    unproven_ids = sorted(
        int(task["task_id"]) for task in tasks if int(task["task_id"]) not in measured_solvable_ids
    )
    model, run_date = (
        str(protocol.get("model") or "未记录"),
        str(manifest.get("created_at") or "")[:10] or "未记录",
    )
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
            "guide",
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
        if not METHODOLOGY_FIGURE.is_file():
            raise FileNotFoundError(f"missing methodology figure: {METHODOLOGY_FIGURE}")
        shutil.copy2(
            METHODOLOGY_FIGURE,
            stage / "assets/webarena-autonomous-evaluation-flow.png",
        )
        article = _article_body(paired, attempts, tasks)
        c, m = summary["chromium"], summary["moli"]
        families = _task_family_summary(tasks, attempts)
        coverage = review.get("coverage") or {}
        family_cards = "".join(
            '<article class="overview-card">'
            f'<span class="tiny">{_e(family["kind"])}</span>'
            f"<h3>{family['count']} 个任务</h3>"
            f"<p>Chromium {family['scores']['chromium']}/{family['count']} · "
            f"Moli {family['scores']['moli']}/{family['count']}</p>"
            f'<a href="datasets/?f_kind={_e(family["kind"])}">查看这类任务 →</a></article>'
            for family in families
        )
        home = _hero(
            f"Browser Use Capability Evaluation · {run_date}",
            "Chromium 与 Moli：Browser Use 能力实测",
            f"在同一 CowAgent、同一 {model}（reasoning=low）和同一 WebArena‑Verified 30 题样本下，对比两种浏览器完成真实网页任务的能力。",
        )
        home += _section(
            "先看结论",
            f'<div class="result-grid"><div class="card"><span class="tiny">Chromium 任务通过</span><div class="n">{c["passed"]}/{c["completed"]}</div><p>{_pct(c["score"])}</p></div><div class="card"><span class="tiny">Moli 任务通过</span><div class="n">{m["passed"]}/{m["completed"]}</div><p>{_pct(m["score"])}</p></div><div class="card"><span class="tiny">单边胜出</span><div class="n">{paired["chromium_only"] + paired["moli_only"]}</div><p>{paired["paired"]} 个完整配对</p></div><div class="card"><span class="tiny">共同失败</span><div class="n">{paired["both_fail"]}</div><p>已确认失败点</p></div></div><p class="callout success"><strong>当前结论：</strong>本轮没有观察到浏览器成功率差异；这证明对照流程可比，不证明两个内核等价。</p><p class="callout"><strong>失败结论：</strong>四题的首个可证失败点位于最终答案构造或状态协议，一题在 30 步上限时仍未完成付款提交。现有证据不能把前四题进一步归因到模型、CowAgent 或提示合同中的某一项。</p><p class="fine">这是固定选择的 30 题试验，不是从全部 WebArena‑Verified 任务随机抽样；比例只描述本样本，不外推总体能力。</p>',
        )
        home += _section(
            "任务族覆盖",
            "<p>样本同时包含信息提取、页面导航和状态变更任务；类别来自每题官方最终答案合同。</p>"
            f'<div class="overview-grid">{family_cards}</div>',
        )
        home += _section(
            "运行与证据是否完整",
            '<div class="audit-strip">'
            f'<p class="callout success"><strong>逻辑结果：</strong>{len(attempts)} 个任务×浏览器结果均保留最终回答、官方 evaluator 结果、Agent 事件和证据哈希。</p>'
            f'<p class="callout success"><strong>证据完整性审计：</strong>{len(attempts)}/{len(attempts)} 个逻辑结果已覆盖，{len(review.get("unresolved") or [])} 个未决。它不重新判题，也不是独立语义复核。</p>'
            f'<p class="callout"><strong>可完成性覆盖：</strong>{len(measured_solvable_ids)}/{len(tasks)} 题至少有一条自主成功轨迹；其余 {len(unproven_ids)} 题仅有官方验收合同。</p>'
            "</div>",
        )
        home += _section(
            "从结论进入证据",
            '<div class="grid"><article class="card article-entry" style="grid-column:span 6"><h3>报告解读</h3><p>对零分歧、共同失败和下一轮设计的完整论证。</p><a href="guide/">阅读完整解读 →</a></article><article class="card" style="grid-column:span 3"><h3>30 个任务</h3><p>筛选并下钻两种浏览器结果。</p><a href="datasets/">打开任务库 →</a></article><article class="card" style="grid-column:span 3"><h3>60 个逻辑结果</h3><p>查看最终判定、耗时、轨迹和核验状态。</p><a href="analysis/">打开结果库 →</a></article></div>',
        )
        home += _section(
            "逐题配对比总分更重要",
            _table(
                ["配对结果", "任务数"],
                [
                    ["两者都通过", paired["both_pass"]],
                    ["仅 Chromium", paired["chromium_only"]],
                    ["仅 Moli", paired["moli_only"]],
                    ["两者都失败", paired["both_fail"]],
                ],
                caption="同一任务的两个浏览器最终逻辑结果。",
            )
            + '<p class="fine">零个不一致任务意味着当前固定样本无法估计内核方向性效应；不对全部 Browser Use 任务作统计外推。</p><p class="page-links"><a href="analysis/#same-task">打开逐题横向对照 →</a></p>',
        )
        (stage / "index.html").write_text(
            _page(title="首页", active="home", body=home), encoding="utf-8"
        )

        guide = (
            _hero(
                "Report interpretation",
                "从 25 比 25 读出下一轮该怎么做",
                "完整文章与首页共用同一份测量数据；另提供无 JavaScript 的独立导出版。",
            )
            + _section(
                "独立导出",
                '<p class="page-links"><a href="guide/article.html">打开独立文章</a><a href="guide/interpretation-evidence.json">下载论点证据</a></p>',
            )
            + f'<section class="section"><div class="wrap">{article}</div></section>'
        )
        (stage / "guide/index.html").write_text(
            _page(title="报告解读", active="guide", body=guide, depth=1), encoding="utf-8"
        )
        (stage / "guide/article.html").write_text(_standalone_article(article), encoding="utf-8")
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
            "候选 benchmark、选择依据与 30 个预注册任务",
            "先区分最终结果、动作过程和模型裁判，再谈采用率；同一家族不同变体的成绩不能合并。",
        )
        datasets += _section(
            "公开采用信号 Top 3",
            _adoption_top3(adoption_evidence),
        )
        datasets += (
            _section(
                "Mind2Web 的拆分不是 easy / medium / hard",
                "<p>原始 Mind2Web 使用 cross-task、cross-website、cross-domain 衡量泛化；Online‑Mind2Web 没有统一三档；Mind2Web 2 的 short / medium / long 按动作跨度划分。本报告的简单 5、中等 10、困难 15 是预注册的操作长度层级。</p>",
            )
            + _section(
                "怎样阅读任务 ground truth",
                "<p><strong>WebArena‑Verified 的 ground truth 不是一条固定点击脚本。</strong>每题声明一个结构化最终回答合同，并在需要确认页面副作用时附加一个或多个网络事件合同。所有合同都通过才记 1 分。任务页只解释合同；各浏览器的实际回答、失败规则和 CowAgent 自主轨迹统一放在结果页。</p>",
            )
            + _section(
                "题集范围",
                "<p>本轮在模型执行前固定 30 个任务，覆盖四个已部署站点以及 RETRIEVE、NAVIGATE、MUTATE 三种最终合同。这里仅描述题目与验收规则；通过率、耗时、轨迹和可完成性观察统一放在结果分析中。</p>",
            )
            + _section("30 个任务", _task_table(tasks))
        )
        (stage / "datasets/index.html").write_text(
            _page(title="评测集", active="datasets", body=datasets, depth=1), encoding="utf-8"
        )

        by_attempt = {(r["engine"], int(r["task_id"])): r for r in attempts}
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
                    f'<a href="results/{engine}-{task_id}.html">{_e(ENGINE_LABELS[engine])} 结果</a>'
                    for engine in ENGINE_LABELS
                    if (engine, task_id) in by_attempt
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
            '<ol class="flow"><li>预注册 30 题</li><li>逐题重置站点</li><li>CowAgent 自主规划</li><li>只替换浏览器</li><li>官方 evaluator 验收</li><li>确定性证据复核</li></ol><p>官方数据 812 题全部配置 AgentResponseEvaluator，其中 663 题还配置 NetworkEventEvaluator。所有已配置 evaluator 都返回 1.0 才通过。它检查最终结果和必要副作用，不要求复刻参考动作序列。</p>',
        )
        methodology += _section(
            "这 30 题如何部署并由 Agent 执行",
            '<figure id="deployment-flow" class="method-figure"><img src="assets/webarena-autonomous-evaluation-flow.png" alt="WebArena-Verified 本地部署、CowAgent 自主操作、双浏览器对照与程序化验收流程" width="1672" height="941"><figcaption>图 1 · 从官方站点镜像、逐题隔离与健康检查，到 CowAgent 观察—决策—操作闭环，再到 Chromium/Moli 对照和官方程序化验收。窄屏可横向滚动查看。</figcaption></figure>'
            "<p><strong>站点确实部署在本机，不是公网代站。</strong>macOS 上的 Docker 由 Colima 虚拟机承载；本轮 Colima VM 地址是 <code>192.168.5.3</code>，其默认路由指向 <code>192.168.5.2</code>。后者是 VM 视角下的 macOS 宿主网关，不是虚拟机地址，也不是评测网站。Shopping、Shopping Admin、Reddit、GitLab 四个官方站点镜像通过 Docker 端口发布到这个宿主入口，独立的 Chromium/Moli 浏览器容器据此访问本地站点。</p>"
            "<p>WebArena‑Verified 当前提供 Shopping、Shopping Admin、Reddit、GitLab、Wikipedia、Map 六类环境。本轮 30 题只覆盖前四类；Wikipedia 与 Map 需要额外大型数据卷，因此没有部署，也没有任务进入成绩。</p>"
            "<p>运行器代码在每个执行前调用 <code>ContainerManager.start(reset=True)</code>，只保留该题所需站点；env-ctrl 报告 ready 后，再做连续应用健康检查、清浏览器状态、登录测试账号并打开起始页。当前冻结证据保存了代码提交、运行协议和镜像身份；事件流从环境准备完成后开始，因此逐次 reset、ready 与 health 结果未形成独立绑定的准备回执。新版运行协议已要求保存该回执。</p>"
            "<p>预注册文件只含任务 ID、层级和入选理由，不含点击序列。CowAgent 收到自然语言目标后，在最多 30 步内循环读取页面观察、请求模型决策并生成下一次 BrowserTool 调用。Chromium 与 Moli 分别连接独立 CDP 浏览器容器；每个逻辑结果保留最终 JSON、Agent 事件流、网络 HAR 和 Playwright trace。</p>"
            + _table(
                ["站点", "本轮访问地址", "应用端口", "环境控制端口", "状态"],
                [
                    ["Shopping", "http://192.168.5.2:7770", "7770", "7771", "本轮已部署"],
                    ["Shopping Admin", "http://192.168.5.2:7780/admin", "7780", "7781", "本轮已部署"],
                    ["Reddit", "http://192.168.5.2:9999", "9999", "9998", "本轮已部署"],
                    ["GitLab", "http://192.168.5.2:8023", "8023", "8024", "本轮已部署"],
                    ["Wikipedia", "—", "8888", "8889", "未部署；不在 30 题"],
                    ["Map", "—", "3030", "3031", "未部署；不在 30 题"],
                ],
            )
            + '<p class="callout"><strong>证据边界：</strong>事件流能证明 CowAgent 在准备完成后产生了 BrowserTool 调用序列；镜像 ID、digest、代码提交与网关记录限定了本地运行身份。当前证据没有独立的逐次环境准备 receipt，因此报告不把“每次重置均成功”当成已经独立核验的事实。</p>'
            '<p class="page-links"><a href="results/chromium-44.html#trajectory">查看 Chromium 自主轨迹示例</a><a href="results/moli-44.html#trajectory">查看 Moli 自主轨迹示例</a><a href="reproduce/">核对镜像与运行身份</a></p>',
        )
        methodology += _section(
            "Mac 上可以继续扩展哪些实例",
            f'<p>官方完整集有 {MAC_COUNTS["full"]} 题，hard 子集有 {MAC_COUNTS["hard"]} 题；官方没有 179 题版本。若运行 179 题，必须把它标注为本项目自定义样本并冻结任务清单。当前四个已实跑站点可直接进入下一轮预检：覆盖 {MAC_COUNTS["current_four_sites_single_site"]} 个单站任务，计入只依赖这四站的跨站任务后为 {MAC_COUNTS["current_four_sites_including_cross_site"]} 个候选；hard 子集中可覆盖 {MAC_COUNTS["current_four_sites_hard_including_cross_site"]} 个。</p>'
            + _table(
                ["环境", "Apple Silicon Mac 状态", "完整集单站题", "hard 单站题", "约束"],
                MAC_ENVIRONMENT_CANDIDATES,
                caption="Wikipedia 与 Map 的容量来自官方环境说明；状态中的“实跑”只指本项目已有执行证据。",
            )
            + '<p class="callout"><strong>最小扩展方案：</strong>先从四个已验证站点的 678 个候选中做可完成性预检和分层抽样；Wikipedia、Map 等大数据环境后置。这样扩容不会先引入新的部署变量。</p>'
            '<p class="page-links"><a href="https://github.com/ServiceNow/webarena-verified">官方任务集与安装说明</a><a href="https://servicenow.github.io/webarena-verified/dev/environments/">官方环境容量说明</a></p>',
        )
        methodology += _section(
            "固定协议",
            _table(
                ["项目", "规则"],
                [
                    ["Agent / 模型", f"CowAgent；{model}；reasoning=low"],
                    ["唯一变量", "同题、同数据、同模型、同提示和资源上限，只替换浏览器"],
                    ["结果口径", "每题×浏览器一个最终逻辑结果；二元通过率；逐题配对"],
                    ["执行上限", "代码最多允许两次执行；本报告按冻结运行协议选取正式结果，实际执行耗时和模型用量累计"],
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
            "从总体配对下钻到每个逻辑结果",
            "先看总体与同题比较，再通过筛选下钻到每个结果的官方规则、实际回答、轨迹和日志。",
        )
        analysis += _section(
            "汇总",
            _table(
                [
                    "浏览器",
                    "任务通过",
                    "通过率",
                    "Agent 链路耗时",
                    "BrowserTool 耗时",
                    "Tokens",
                ],
                _summary_comparison_rows(summary),
            )
            + '<p class="fine"><span class="winner-mark" aria-hidden="true">优</span> 标记方向明确且数值更优的指标：通过数与通过率越高越优，耗时与 Tokens 越低越优；数值相同时不标记。</p>'
            + '<p class="fine"><strong>Agent 链路累计耗时</strong>把获得该任务结果实际发生的执行相加；每次从浏览器会话准备开始，到 CowAgent 完成、验收所需证据落盘前结束，包含登录、模型推理、调度、页面等待、BrowserTool 与收尾，不包含此前的 Docker 站点重置。<strong>BrowserTool 累计耗时</strong>是事件流中全部 browser 工具调用墙钟时间之和，包含工具封装、CDP 往返与页面等待，仍不是纯内核 CPU 时间。</p>',
        )
        analysis += _section(
            "BrowserTool 动作延迟",
            "<p>每张卡对应一种 CowAgent action。以单次调用墙钟均值作为主要比较值并标记较低者；总耗时与调用数用于解释工作量，不判优。该指标仍包含工具封装、CDP 往返和页面等待，不是纯内核 CPU 时间。</p>"
            + _action_duration_cards(attempts),
        )
        analysis += _section(
            "浏览器组合对比",
            "<p>按同一任务比较所选浏览器，不用各浏览器不同的成功样本拼接结论。基准浏览器单选，对比浏览器可多选；新增浏览器只增加选项和结果标签，不改变页面结构。</p>"
            + _same_task_comparison(tasks, attempts),
        ).replace('<section class="section">', '<section class="section" id="same-task">', 1)
        analysis += _section(
            "60 个逻辑结果",
            "<p>默认只显示失败；可按浏览器、层级、站点、任务类型、失败环节和全文关键词筛选。</p>"
            + _attempt_table(attempts, tasks),
        )
        (stage / "analysis/index.html").write_text(
            _page(title="结果分析", active="analysis", body=analysis, depth=1), encoding="utf-8"
        )

        review_by_key = {
            (x["engine"], int(x["task_id"]), int(x["attempt"])): x
            for x in review.get("attempts") or []
        }
        for row, public in zip(attempts, public_attempts, strict=True):
            item = review_by_key.get(
                (public["engine"], public["task_id"], int(row["attempt"])), {}
            )
            task = next(t for t in tasks if int(t["task_id"]) == public["task_id"])
            paths = row.get("paths") or {}
            response_record = read_public_json(Path(paths.get("response", "")))
            evaluation_record = read_public_json(Path(paths.get("evaluation", "")))
            events_record = read_public_json(Path(paths.get("events", "")))
            steps = trajectory(events_record if isinstance(events_record, list) else [])
            metadata = evidence_metadata(row)
            artifact_dir = stage / "artifacts" / public["id"]
            artifact_dir.mkdir(parents=True)
            _write_json(artifact_dir / "agent-response.json", response_record)
            _write_json(artifact_dir / "evaluation.json", evaluation_record)
            _write_json(artifact_dir / "agent-events.json", events_record)
            _write_json(artifact_dir / "trajectory.json", steps)
            _write_json(artifact_dir / "evidence-metadata.json", metadata)
            usage, resource, checks = (
                public["model_usage"],
                public["resource"],
                item.get("checks") or {},
            )
            detail = _hero(
                public["id"],
                f"{ENGINE_LABELS[public['engine']]} · Task #{public['task_id']}",
                "先看任务目标与验收结论，再按需展开技术明细和 CowAgent 的每一步 BrowserTool 调用。",
            )
            # Pages declare <base href="../"> so every internal URL is rooted at
            # the report bundle, including same-page fragment links.
            attempt_page = f"results/{public['id']}.html"
            detail += (
                '<nav class="page-links wrap" aria-label="结果详情导航">'
                f'<a href="{attempt_page}#verification">任务与验收</a>'
                f'<a href="{attempt_page}#trajectory">执行轨迹</a>'
                f'<a href="{attempt_page}#runtime">运行与证据</a>'
                "</nav>"
            )
            detail += _section(
                "结果与运行",
                f'<div class="attempt-summary"><article class="attempt-verdict{" failure" if float(row["score"]) < 1 else ""}">'
                f'<span class="status-line">{_status(row)}</span><p>{_e(_failure_brief(evaluation_record, row))}</p>'
                f'<div class="attempt-jumps"><a href="{attempt_page}#verification">查看任务与验收</a></div></article>'
                f'<div class="attempt-fact"><b>累计自主操作</b>{public["action_count"]} 次 BrowserTool 调用</div>'
                f'<div class="attempt-fact"><b>Agent 链路累计耗时</b>{_seconds(public["duration_ms"])}</div>'
                f'<div class="attempt-fact"><b>BrowserTool 累计耗时</b>{_seconds(public["browser_tool_duration_ms"])}</div>'
                f'<div class="attempt-fact"><b>模型 tokens</b>{_number(usage["total_tokens"])}</div></div>'
                f'<div class="attempt-secondary"><div class="attempt-fact"><b>模型请求</b>{usage["request_count"]} 次调用 / {usage["request_attempt_count"]} 次传输尝试</div>'
                f'<div class="attempt-fact"><b>传输错误</b>{public["transport_error_count"]}</div>'
                f'<div class="attempt-fact"><b>Run ID</b>{_e(public["run_id"] or "未记录")}</div>'
                f'<div class="attempt-fact"><b>证据完整性审计</b>{_e(item.get("outcome") or "未记录")}</div></div>'
                '<p class="fine">Agent 链路耗时包含会话准备、登录、模型推理、调度、工具执行和收尾，不包含 Docker 站点重置；BrowserTool 耗时是全部 browser 工具调用的墙钟时间之和，不等同于纯内核时间。</p>',
            )
            detail += '<div id="verification"></div>' + _section(
                "任务要求与验收结果",
                _verification_html(task, evaluation_record, response_record)
                + (
                    '<div class="callout"><strong>已确认失败点：</strong>'
                    + _e(SHARED_FAILURE_DIAGNOSES[public["task_id"]]["confirmed_failure_point"])
                    + " <strong>直接证据：</strong>"
                    + _e(SHARED_FAILURE_DIAGNOSES[public["task_id"]]["direct_evidence"])
                    + "</div>"
                    if public["task_id"] in SHARED_FAILURE_DIAGNOSES
                    else ""
                )
                + '<p class="fine">所有规则均为官方程序化验收，必须全部得分 1.0。页面先给出可读结论；官方期望、实际观察和断言保留在折叠技术明细及下载文件中。</p>',
            )
            detail += '<div id="trajectory"></div>' + _section(
                "CowAgent 自主执行轨迹",
                "<p>以下顺序来自本次执行的 Agent 事件流，不是固定动作回放。展开可查看调用参数和输出摘录；完整脱敏日志可下载。</p>"
                + _trajectory_html(steps),
            )
            detail += '<div id="runtime"></div>' + _section(
                "运行、日志与证据",
                _artifact_links(public["id"])
                + '<p class="fine">公开包包含脱敏的回答、evaluator、事件流与轨迹。原始 HAR 和 Playwright trace 可能携带登录态、Cookie、页面快照或请求正文，因此不公开；其字节数和 SHA-256 保留用于证明证据绑定。</p>'
                + _table(["指标", "值"], resource.items())
                + '<p class="fine">整次 Agent 运行的容器计量只作诊断；不同进程模型下不直接比较内存或 PID。</p>'
                + "<h3>证据完整性检查</h3>"
                + _table(
                    ["检查", "结果"],
                    [[key, "通过" if value else "未通过"] for key, value in checks.items()],
                )
                + "<h3>原始证据文件绑定</h3>"
                + _table(
                    ["证据", "存在", "字节", "SHA-256", "公开策略"],
                    [
                        [
                            label,
                            "是" if value.get("present") else "否",
                            value.get("bytes"),
                            value.get("sha256") or "—",
                            value.get("publication"),
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
            '<p>官方 WebArena‑Verified evaluator 是成绩来源；核验记录只说明报告和实现经过了哪些检查与修订，不替代任务判分。</p>',
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
            "参评浏览器与统一实验条件",
            "Chromium 与 Moli 作为浏览器后端参与同题对照；Agent、模型、任务、资源上限、站点与 evaluator 保持一致。",
        )
        adoption += _section(
            "两个参评浏览器",
            _table(
                ["参评浏览器", "运行版本", "镜像 ID", "接入方式", "本轮成绩"],
                [
                    [
                        Safe(f'<a href="engines/{engine}.html">{_e(label)}</a>'),
                        (participant_versions.get(engine) or {}).get("Browser", "—"),
                        (participant_images.get(engine) or {}).get("id", "—"),
                        "独立容器 · CDP · CowAgent BrowserTool",
                        f'{summary[engine]["passed"]}/{summary[engine]["completed"]}',
                    ]
                    for engine, label in ENGINE_LABELS.items()
                ],
            )
            + '<p>参评浏览器是本轮唯一实验变量；完整 Agent 栈的其余组成列为统一实验条件。</p>',
        )
        adoption += _section(
            "共同实验条件",
            _table(
                ["栈字段", "本轮固定值", "角色"],
                [
                    ["Agent", f'CowAgent · commit {protocol.get("cowagent_commit", "—")}', "自主观察、规划并调用 BrowserTool"],
                    ["模型", f'{model} · reasoning={protocol.get("reasoning_effort", "—")}', "为两个选手生成决策"],
                    ["任务与站点", "同一 30 题；同一批本地站点镜像", "提供一致输入与网页状态"],
                    ["动作接口", "CowAgent BrowserTool · CDP", "把 Agent 动作交给浏览器"],
                    ["验收", "WebArena‑Verified 1.2.3 官方 evaluator", "对最终 JSON 与必要网络副作用判分"],
                    ["资源与上限", "相同 CPU、内存、viewport 与最多 30 步", "控制运行预算"],
                ],
            )
            + "<p><strong>这 30 题由 CowAgent 自主执行。</strong>仓库中的 Browser Trajectory Replay（轨迹重放）固定动作微基准属于另一条实验轨道，不是本报告的参评选手，也不计入这 30 题成绩。</p>",
        )
        adoption += _section(
            "如何增加下一名选手",
            "<p>新增浏览器时，需要注册其容器镜像、版本与 CDP 端点，并在同一冻结任务集和共同条件下形成完整结果。总榜按浏览器逐行扩展；同题矩阵按浏览器增加列；统计比较按浏览器对单独生成，不能把多个浏览器压成一个总胜负。</p>",
        )
        (stage / "adoption/index.html").write_text(
            _page(title="参评浏览器", active="adoption", body=adoption, depth=1), encoding="utf-8"
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
                        ["任务成绩", f"{summary[engine]['passed']}/{summary[engine]['completed']}"],
                        ["逻辑结果耗时 P50", _seconds(summary[engine]["p50_duration_ms"])],
                    ],
                )
                + '<p class="fine">版本与镜像身份用于限定复现边界，不参与评分。</p>',
            )
            body += _section(
                "全部逻辑结果",
                "<p>每题只展示一个最终逻辑结果；端到端耗时与模型用量包含获得该结果所需的全部执行。</p>"
                + _engine_attempts(engine, attempts, tasks),
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
            '<p>本轮 Docker daemon 运行在 macOS 上的 Colima 虚拟机中。站点容器把应用端口发布到 Docker 宿主；两个浏览器容器通过其容器内 <code>host.docker.internal</code> 对应的网关地址访问这些端口。运行时记录的地址是 <code>'
            + _e(environment.get("site_access_host") or "未记录")
            + '</code>。它属于本轮私有网络，不应复制为其他机器的固定地址。</p>'
            + _table(
                ["层", "本轮身份", "作用"],
                [
                    ["宿主", environment.get("platform", "—"), "运行 Colima 与报告 runner"],
                    ["Docker", f'{environment.get("docker_name", "—")} · {environment.get("docker_architecture", "—")} · {environment.get("docker_server_version", "—")}', "承载站点和浏览器容器"],
                    ["浏览器到宿主网关", environment.get("site_access_host", "—"), "访问站点容器发布端口"],
                    ["ARM 兼容模式", (environment.get("site_runtime_compatibility") or {}).get("mode", "未启用"), "运行部分 amd64 PHP 站点镜像"],
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
            + '<p class="fine">逐执行的环境准备 receipt 尚未包含在这批冻结证据中；现有记录可核对运行器配置与镜像身份，但不能独立重放每次 reset/ready/health 的完整证明链。</p>',
        )
        reproduce += _section(
            "公开复现入口",
            '<p><a href="https://github.com/lexmount/browser-eval">lexmount/browser-eval</a> 是代码、题集和锁文件的唯一来源。</p><pre><code>./examples/browser_use/run.sh\nmake browser-use-review EVIDENCE="$BROWSER_USE_EVIDENCE"\nmake browser-use-report EVIDENCE="$BROWSER_USE_EVIDENCE"\nmake browser-use-report-check EVIDENCE="$BROWSER_USE_EVIDENCE"</code></pre><div class="page-links"><a href="data/selection.json">selection.json</a><a href="data/results.jsonl">results.jsonl</a><a href="data/evidence-index.json">evidence-index.json</a><a href="data/summary.json">summary.json</a><a href="data/environment.json">environment.json</a><a href="data/review.json">review.json</a><a href="data/publication-privacy.json">publication-privacy.json</a></div><p class="fine">每个逻辑结果的脱敏回答、evaluator、事件和轨迹位于 <code>artifacts/&lt;result-id&gt;/</code>；<code>evidence-index.json</code> 给出全部 60 个逻辑结果的页面与下载路径。</p>',
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
                {"claim": "当前样本未观察到成功率差异", "basis": paired},
                {
                    "claim": "失败任务完全重合；证据确认四题止于最终答案或状态协议，一题止于步数上限前的结账流程",
                    "basis": SHARED_FAILURE_DIAGNOSES,
                },
            ],
            "bound_attempts_sha256": _sha256(evidence / "attempts.jsonl"),
            "selection_sha256": selection.sha256,
            "score_authority": protocol.get("official_score_uses")
            or "final execution for each browser and task",
        }
        _write_json(stage / "guide/interpretation-evidence.json", claims)
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
            stage / "data/evidence-index.json",
            {
                "schema": "lex_browser_use_public_evidence_index/2",
                "results": [
                    {
                        "result_id": row["id"],
                        "engine": row["engine"],
                        "task_id": row["task_id"],
                        "score": row["score"],
                        "task_page": f"datasets/{row['task_id']}.html",
                        "result_page": f"results/{row['id']}.html",
                        "artifacts": {
                            "agent_response": f"artifacts/{row['id']}/agent-response.json",
                            "evaluation": f"artifacts/{row['id']}/evaluation.json",
                            "agent_events": f"artifacts/{row['id']}/agent-events.json",
                            "trajectory": f"artifacts/{row['id']}/trajectory.json",
                            "evidence_metadata": f"artifacts/{row['id']}/evidence-metadata.json",
                        },
                    }
                    for row in public_attempts
                ],
            },
        )
        audit = {
            "schema": "lex_browser_use_report/2",
            "result_count": len(attempts),
            "score_authority": protocol.get("official_score_uses")
            or "final execution for each browser and task",
            "summary": summary,
            "paired_summary": paired,
            "selection_sha256": selection.sha256,
            "evidence_bindings": {
                "attempts_sha256": _sha256(evidence / "attempts.jsonl"),
                "manifest_sha256": _sha256(evidence / "manifest.json"),
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
            "schema": "lex_browser_use_public_environment/1",
            "protocol": {
                key: protocol.get(key)
                for key in (
                    "agent",
                    "browser_eval_commit",
                    "cowagent_commit",
                    "model",
                    "reasoning_effort",
                    "evaluator",
                    "engine_order",
                    "sites",
                )
            }
                | {
                    "max_task_executions": 2,
                    "environment_reset": "runner configured for reset before each execution; legacy evidence has no independent per-execution preparation receipt",
                    "score_uses": "frozen run manifest; report does not override measured score",
                    "cost_and_latency": "sum of all executions used for the logical result",
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
                    "sanitized final agent response",
                    "sanitized evaluator expected/actual/assertions/errors",
                    "sanitized CowAgent event stream and BrowserTool trajectory",
                    "artifact byte sizes and SHA-256 bindings",
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
    parser.add_argument("--evidence", required=True, type=Path)
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
