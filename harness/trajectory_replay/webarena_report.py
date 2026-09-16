"""Build the evidence-backed WebArena Browser Trajectory Replay report."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from harness.trajectory_replay.webarena_review import (
    load_validated_failure_review,
    programmatic_failure,
)

LOCAL_PATH_RE = re.compile(r"/(?:Users|home|private|tmp|var|opt)/[^\s\"']+")


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stat(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    ordered = sorted(values)

    def rank(quantile: float) -> float:
        return ordered[min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)]
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "p50": rank(0.5),
        "p95": rank(0.95),
    }


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if key not in {"path", "paths"}
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        value = LOCAL_PATH_RE.sub("[local path removed]", value)
        value = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[secret removed]", value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_clean(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_artifact(ref: dict[str, Any] | None) -> Any:
    if not ref or not ref.get("path"):
        return None
    path = Path(str(ref["path"])).expanduser().resolve()
    if not path.is_file() or _sha256(path) != ref.get("sha256"):
        raise ValueError(f"evidence artifact is missing or changed: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _format_ms(value: float | None) -> str:
    return "—" if value is None else f"{value / 1000:.1f} 秒"


def _format_mib(value: float | None) -> str:
    return "—" if value is None else f"{value / 1024 / 1024:.1f} MiB"


def _format_kib(value: float | None) -> str:
    return "—" if value is None else f"{value / 1024:.1f} KiB"


def _format_mib_seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} MiB·s"


def _format_percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _format_count(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


TIMING_GROUPS = (
    (
        "分段耗时",
        "Timing by phase",
        (
            ("环境准备", "Environment preparation", "environment_prepare_ms", _format_ms, True),
            ("浏览器会话准备", "Browser session setup", "browser_session_setup_ms", _format_ms, True),
            ("轨迹执行", "Trajectory execution", "trajectory_replay_ms", _format_ms, True),
            ("证据整理", "Evidence finalization", "evidence_finalize_ms", _format_ms, True),
            ("官方验收", "Official evaluation", "evaluation_ms", _format_ms, True),
        ),
    ),
)


RESOURCE_GROUPS = (
    (
        "CPU 与计算",
        "CPU and compute",
        (
            ("CPU 时间", "CPU time", "cpu_ms", _format_ms, True),
            ("平均 CPU 使用率", "Mean CPU utilization", "cpu_average_utilization_pct", _format_percent, False),
            ("任务内 CPU P95", "Within-task CPU P95", "cpu_sampled_utilization_p95_pct", _format_percent, False),
            ("任务内 CPU 峰值", "Within-task peak CPU", "cpu_sampled_utilization_peak_pct", _format_percent, False),
        ),
    ),
    (
        "内存与进程",
        "Memory and processes",
        (
            ("平均内存", "Mean memory", "memory_mean_bytes", _format_mib, True),
            ("任务内存 P95", "Within-task memory P95", "memory_p95_bytes", _format_mib, True),
            ("峰值内存", "Peak memory", "memory_peak_bytes", _format_mib, True),
            ("内存占用量", "Memory integral", "memory_mib_seconds", _format_mib_seconds, True),
            ("进程数峰值", "Peak process count", "pids_current_peak", _format_count, True),
        ),
    ),
    (
        "网络",
        "Network",
        (
            ("接收数据", "Data received", "network_rx_bytes", _format_mib, True),
            ("发送数据", "Data sent", "network_tx_bytes", _format_kib, True),
            ("接收速率 P95", "Receive rate P95", "network_rx_rate_p95_bytes_per_second", lambda value: "—" if value is None else f"{value / 1024:.1f} KiB/s", False),
            ("发送速率 P95", "Send rate P95", "network_tx_rate_p95_bytes_per_second", lambda value: "—" if value is None else f"{value / 1024:.1f} KiB/s", False),
        ),
    ),
)


def _metric_lines(
    metric: dict[str, Any],
    formatter: Any,
    method_id: str,
    winners: dict[str, set[str]],
    *,
    lang: str,
) -> str:
    labels = (("mean", "均值"), ("p50", "P50"), ("p95", "P95"))
    if lang == "en":
        labels = (("mean", "Mean"), ("p50", "P50"), ("p95", "P95"))
    return "<br>".join(
        f'<span class="metric-label">{label}</span> '
        + _winner(formatter(metric.get(key)), method_id in winners[key], lang=lang)
        for key, label in labels
    )


def _unique_winners(
    rows: list[dict[str, Any]], key: str, *, higher: bool
) -> set[str]:
    numeric = [(row["id"], row.get(key)) for row in rows if isinstance(row.get(key), (int, float))]
    if not numeric:
        return set()
    best = (max if higher else min)(value for _, value in numeric)
    winners = {name for name, value in numeric if value == best}
    return winners if len(winners) == 1 else set()


def _nav(lang: str, active: str, *, depth: int, current_route: str = "index.html") -> str:
    labels = {
        "zh": (("index", "结论"), ("dataset", "评测集"), ("results", "结果分析"), ("interpretation", "报告解读"), ("environment", "环境与方法"), ("review", "核验记录")),
        "en": (("index", "Summary"), ("dataset", "Dataset"), ("results", "Results analysis"), ("interpretation", "Interpretation"), ("environment", "Environment & method"), ("review", "Review history")),
    }[lang]
    local_prefix = "../" * depth
    switch = ("../../" if lang == "en" and depth else "../" if lang == "en" else "../en/" if depth else "en/") + current_route
    switch_label = "中文" if lang == "en" else "English"
    brand = "Trajectory Replay" if lang == "en" else "轨迹重放"
    theme_label = "Toggle theme" if lang == "en" else "切换主题"
    links = "".join(
        f'<a href="{local_prefix}{slug}.html"'
        + (' aria-current="page"' if slug == active else "")
        + f">{label}</a>"
        for slug, label in labels
    )
    return f'<header class="top"><a class="brand" href="{local_prefix}index.html">{brand}</a><nav>{links}</nav><a class="language" href="{switch}">{switch_label}</a><button class="theme" type="button" aria-label="{theme_label}">◐</button></header>'


def _shell(lang: str, active: str, title: str, body: str, *, depth: int = 0, current_route: str = "index.html") -> str:
    prefix = "../" * depth
    asset_prefix = prefix if lang == "zh" else "../" + prefix
    footer = (
        "WebArena-Verified 1.2.3 · 浏览器轨迹重放"
        if lang == "zh"
        else "WebArena-Verified 1.2.3 · Browser Trajectory Replay"
    )
    preview_bootstrap = r'''<script>(function(){
const parts=location.pathname.split('/');
const at=parts.indexOf('preview');
if(at<1||parts[at-1]!=='api')return;
const node=document.querySelector('base[href]');
if(!node)return;
const signedRoot=new URL(node.href,location.href);
const route=parts.slice(at+2);
if(route.length&&!location.pathname.endsWith('/'))route.pop();
signedRoot.pathname=signedRoot.pathname.replace(/\/?$/,'/')+(route.length?route.join('/')+'/':'');
signedRoot.search='';
signedRoot.hash='';
node.href=signedRoot.href;
})();</script>'''
    return f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>{preview_bootstrap}<link rel="stylesheet" href="{asset_prefix}assets/report.css"></head><body>{_nav(lang, active, depth=depth, current_route=current_route)}<main>{body}</main><footer>{footer}</footer><script src="{asset_prefix}assets/app.js"></script></body></html>'''


def _winner(value: str, won: bool, *, lang: str) -> str:
    badge = "优" if lang == "zh" else "Best"
    return f'<strong class="winner">{value}<span>{badge}</span></strong>' if won else value


def _metrics_table(methods: list[dict[str, Any]], lang: str) -> str:
    pass_winners = _unique_winners(methods, "pass_rate", higher=True)
    metric_winners = {
        metric: {
            stat: _unique_winners(
                [{"id": row["id"], "value": row[metric][stat]} for row in methods],
                "value",
                higher=False,
            )
            for stat in ("mean", "p50", "p95")
        }
        for metric in ("duration", "action_latency", "cpu", "memory")
    }
    headers = (
        ("浏览器", "通过", "通过率", "执行次数", "成功交集端到端耗时", "单次动作耗时", "CPU 时间", "平均内存")
        if lang == "zh"
        else ("Browser", "Passed", "Pass rate", "Executions", "End-to-end time on shared passes", "Per-action latency", "CPU time", "Mean memory")
    )
    body = []

    def time_value(value: float | None) -> str:
        if lang == "zh":
            return _format_ms(value)
        return "—" if value is None else f"{value / 1000:.1f} s"

    for row in methods:
        key = row["id"]
        cells = [
            row["label"],
            _winner(
                f'{row["passed"]}/{row["total"]}',
                key in pass_winners,
                lang=lang,
            ),
            _winner(f'{row["pass_rate"] * 100:.1f}%', key in pass_winners, lang=lang),
            str(row.get("physical_executions", row["total"])),
            _metric_lines(
                row["duration"], time_value, key, metric_winners["duration"], lang=lang
            ),
            _metric_lines(
                row["action_latency"],
                time_value,
                key,
                metric_winners["action_latency"],
                lang=lang,
            ),
            _metric_lines(row["cpu"], time_value, key, metric_winners["cpu"], lang=lang),
            _metric_lines(
                row["memory"], _format_mib, key, metric_winners["memory"], lang=lang
            ),
        ]
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return f'<div class="table-scroll"><table class="metrics"><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _grouped_metric_tables(
    methods: list[dict[str, Any]],
    groups: tuple[Any, ...],
    metric_field: str,
    lang: str,
) -> str:
    zh = lang == "zh"
    sections = []
    for group_zh, group_en, metrics in groups:
        rows = []
        for label_zh, label_en, key, formatter, lower_is_better in metrics:
            localized_formatter = (
                (lambda value: "—" if value is None else f"{value / 1000:.1f} s")
                if not zh and formatter is _format_ms
                else formatter
            )
            metric_winners = {
                stat: (
                    _unique_winners(
                        [
                            {
                                "id": method["id"],
                                "value": (method[metric_field].get(key) or {}).get(stat),
                            }
                            for method in methods
                        ],
                        "value",
                        higher=False,
                    )
                    if lower_is_better
                    else set()
                )
                for stat in ("mean", "p50", "p95")
            }
            cells = [_e(label_zh if zh else label_en)]
            for method in methods:
                cells.append(
                    _metric_lines(
                        method[metric_field].get(key) or _stat([]),
                        localized_formatter,
                        method["id"],
                        metric_winners,
                        lang=lang,
                    )
                )
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
        headers = (
            "指标" if zh else "Metric",
            *(method["label"] for method in methods),
        )
        note = (
            "绿色标记表示该统计项在全部参评浏览器中数值最低。CPU 使用率和网络速率只描述运行形态，不判优。"
            if zh
            else "Green marks the uniquely lowest value across all registered browsers. CPU utilization and network rates describe workload shape and are not ranked."
        )
        sections.append(
            f'<section class="metric-group"><div class="metric-group-head"><h3>{_e(group_zh if zh else group_en)}</h3><p>{note}</p></div>'
            f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
        )
    return '<div class="metric-groups">' + "".join(sections) + "</div>"


def _leader_cards(methods: list[dict[str, Any]], lang: str) -> str:
    zh = lang == "zh"
    specs = (
        ("轨迹执行", "Trajectory execution", "timings", "trajectory_replay_ms", _format_ms),
        ("CPU 时间", "CPU time", "resources", "cpu_ms", _format_ms),
        ("峰值内存", "Peak memory", "resources", "memory_peak_bytes", _format_mib),
        ("接收数据", "Data received", "resources", "network_rx_bytes", _format_mib),
    )
    cards = []
    for label_zh, label_en, field, key, formatter in specs:
        values = [
            (method, (method[field].get(key) or {}).get("mean"))
            for method in methods
        ]
        numeric = [(method, value) for method, value in values if isinstance(value, (int, float))]
        if not numeric:
            continue
        best = min(value for _, value in numeric)
        leaders = [method for method, value in numeric if value == best]
        leader = "、".join(str(item["label"]) for item in leaders) if zh else ", ".join(str(item["label"]) for item in leaders)
        rendered_value = (
            ("—" if best is None else f"{best / 1000:.1f} s")
            if not zh and formatter is _format_ms
            else formatter(best)
        )
        unique = len(leaders) == 1
        badge = ("获胜" if zh else "Winner") if unique else ("并列" if zh else "Tied")
        cards.append(
            f'<article class="{"winner-card" if unique else "tie-card"}"><span>{_e(label_zh if zh else label_en)}</span>'
            f'<strong>{_e(leader)}<em>{badge}</em></strong><b>{_e(rendered_value)}</b>'
            f'<small>{"共同通过任务的均值" if zh else "Mean on shared passes"}</small></article>'
        )
    return '<div class="leader-grid">' + "".join(cards) + "</div>"


def _resource_comparison(methods: list[dict[str, Any]], lang: str) -> str:
    return _leader_cards(methods, lang) + _grouped_metric_tables(
        methods, RESOURCE_GROUPS, "resources", lang
    )


def _timing_comparison(methods: list[dict[str, Any]], lang: str) -> str:
    return _grouped_metric_tables(methods, TIMING_GROUPS, "timings", lang)


def _version_comparison(
    rows: list[dict[str, Any]], methods: dict[str, dict[str, Any]], lang: str
) -> str:
    """Compare registered versions of one browser family without replacing either."""

    zh = lang == "zh"
    families: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for engine, method in methods.items():
        if method.get("version"):
            families[str(method.get("family") or method["id"])].append((engine, method))
    sections = []
    by_key = {(row["engine"], int(row["task_id"])): row for row in rows}
    task_ids = sorted({int(row["task_id"]) for row in rows})
    for versions in families.values():
        if len(versions) < 2:
            continue
        versions.sort(key=lambda item: str(item[1]["version"]))
        old, new = versions[-2], versions[-1]
        transitions = {
            "fixed": [],
            "regressed": [],
            "persistent": [],
            "stable": [],
        }
        for task_id in task_ids:
            if (old[0], task_id) not in by_key or (new[0], task_id) not in by_key:
                continue
            old_pass = by_key[(old[0], task_id)]["status"] == "pass"
            new_pass = by_key[(new[0], task_id)]["status"] == "pass"
            bucket = (
                "stable"
                if old_pass and new_pass
                else "regressed"
                if old_pass
                else "fixed"
                if new_pass
                else "persistent"
            )
            transitions[bucket].append(task_id)
        labels = {
            "fixed": ("新版通过、旧版失败", "New passes, old fails"),
            "regressed": ("新版失败、旧版通过", "New fails, old passes"),
            "persistent": ("两个版本都失败", "Both versions fail"),
            "stable": ("两个版本都通过", "Both versions pass"),
        }
        body = []
        for code in ("fixed", "regressed", "persistent", "stable"):
            ids = transitions[code]
            links = ("、" if zh else ", ").join(
                f'<a href="result/{_e(new[0])}-{task_id}.html">#{task_id}</a>'
                for task_id in ids
            ) or "—"
            body.append(
                f'<tr><td>{labels[code][0 if zh else 1]}</td><td>{len(ids)}</td><td>{links}</td></tr>'
            )
        note = (
            "同一标准轨迹、站点重置和验收规则下比较版本变化。新版通过只能说明该题行为改善；只有结合逐步证据与源码才能判断具体缺陷是否修复。"
            if zh
            else "Versions use the same qualified trajectories, site resets, and evaluator. A new pass shows improved behavior on that task; exact defect resolution still requires step evidence and source analysis."
        )
        sections.append(
            f'<section class="version-comparison"><h3>{_e(old[1]["label"])} → {_e(new[1]["label"])}</h3><p>{note}</p>'
            f'<div class="table-scroll"><table><thead><tr><th>{"结果变化" if zh else "Outcome change"}</th><th>{"任务数" if zh else "Tasks"}</th><th>{"逐题详情" if zh else "Task details"}</th></tr></thead><tbody>{"".join(body)}</tbody></table></div></section>'
        )
    return "".join(sections)


def _metric_leader(
    methods: list[dict[str, Any]],
    value: Callable[[dict[str, Any]], float],
    *,
    higher: bool,
) -> tuple[list[dict[str, Any]], float | None]:
    ranked = [
        (method, float(metric))
        for method in methods
        if isinstance(metric := value(method), (int, float))
    ]
    if not ranked:
        return [], None
    best = (max if higher else min)(metric for _, metric in ranked)
    return [method for method, metric in ranked if metric == best], best


def _leader_sentence(
    leaders: list[dict[str, Any]],
    value: str,
    *,
    lang: str,
    metric_zh: str,
    metric_en: str,
    rank_zh: str,
    rank_en: str,
) -> str:
    names = ("、" if lang == "zh" else ", ").join(_e(item["label"]) for item in leaders)
    if lang == "zh":
        relation = f"并列{rank_zh}" if len(leaders) > 1 else rank_zh
        return f"{names} 的{metric_zh}{relation}，为 {value}。"
    relation = f"tied for the {rank_en}" if len(leaders) > 1 else f"had the {rank_en}"
    return f"{names} {relation} for {metric_en}, at {value}."


def _copy_failure_screenshot(attempt: dict[str, Any], output: Path, key: str) -> str | None:
    ref = (attempt.get("evidence") or {}).get("failure_screenshot")
    if not ref or not ref.get("path"):
        return None
    source = Path(str(ref["path"])).expanduser().resolve()
    if not source.is_file() or _sha256(source) != ref.get("sha256"):
        raise ValueError(f"failure screenshot is missing or changed: {key}")
    target = output / "assets/screenshots" / f"{key}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return f"assets/screenshots/{key}.png"


def _public_evidence(
    attempt: dict[str, Any], review: dict[str, Any] | None, output: Path
) -> dict[str, Any]:
    key = f'{attempt["engine"]}-{attempt["task_id"]}'
    trace = _load_artifact((attempt.get("evidence") or {}).get("trace")) or []
    evaluation = _load_artifact((attempt.get("evidence") or {}).get("evaluation"))
    failed = programmatic_failure(attempt)
    if failed["stage"] == "official_evaluation" and evaluation:
        missing_events = [
            result for result in evaluation.get("evaluators_results") or []
            if result.get("evaluator_name") == "NetworkEventEvaluator"
            and any(
                assertion.get("assertion_name") == "missing_navigation_event"
                for assertion in result.get("assertions") or []
            )
        ]
        if missing_events:
            assertion_messages = [
                str(message)
                for result in missing_events
                for assertion in result.get("assertions") or []
                if assertion.get("assertion_name") == "missing_navigation_event"
                for message in assertion.get("assertion_msgs") or []
            ]
            trigger = next(
                (
                    step for step in reversed(trace)
                    if (step.get("arguments") or {}).get("action") in {"click", "press", "evaluate"}
                ),
                None,
            )
            failed = {
                "stage": "side_effect_not_observed",
                "step": None,
                "trigger_step": trigger.get("index") if trigger else None,
                "message": "NetworkEventEvaluator: missing_navigation_event" + (" — " + " | ".join(assertion_messages) if assertion_messages else ""),
                "expected_network": [result.get("expected") for result in missing_events],
            }
    screenshot = _copy_failure_screenshot(attempt, output, key)
    public = {
        "result": {
            "engine": attempt["engine"],
            "task_id": attempt["task_id"],
            "status": attempt["status"],
            "action_count": attempt["action_count"],
            "completed_action_count": attempt["completed_action_count"],
            "duration_ms": attempt["duration_ms"],
            "execution_count": int(attempt.get("physical_execution_count") or 1),
            "timings": attempt.get("timings") or {},
            "resource": attempt.get("resource") or {},
        },
        "programmatic_failure": failed,
        "review": (
            {
                "cause_code": review["cause_group"]["code"],
                "label_zh": review["cause_group"]["label_zh"],
                "label_en": review["cause_group"]["label_en"],
                "reason_zh": review["reason_zh"],
                "reason_en": review["reason_en"],
                "responsibility": review["cause_group"]["responsibility"],
                "remediation_owner": review["cause_group"]["remediation_owner"],
                "remediation_status": review["cause_group"]["remediation_status"],
            }
            if review
            else None
        ),
        "trajectory": trace,
        "official_evaluation": evaluation,
        "screenshot": screenshot,
    }
    _write_json(output / "data/evidence" / f"{key}.json", public)
    return public


def _detail_page(
    lang: str,
    attempt: dict[str, Any],
    task: dict[str, Any],
    method: dict[str, Any],
    evidence: dict[str, Any],
    peers: list[dict[str, Any]],
) -> str:
    failed = evidence["programmatic_failure"]
    review = evidence.get("review")
    zh = lang == "zh"
    status = ("通过" if attempt["status"] == "pass" else "失败") if zh else attempt["status"].title()
    reason = (review.get("reason_zh") if zh else review.get("reason_en")) if review else ""
    label = (review.get("label_zh") if zh else review.get("label_en")) if review else ""
    key = f'{attempt["engine"]}-{attempt["task_id"]}'
    report_root = "../" if zh else "../../"
    stage_labels = {
        "browser_tool_execution": ("浏览器动作执行", "Browser action execution"),
        "side_effect_not_observed": ("提交请求未出现", "Submission request not observed"),
        "official_evaluation": ("官方验收", "Official evaluation"),
        "pass": ("无", "None"),
    }
    stage = stage_labels.get(failed["stage"], (failed["stage"], failed["stage"]))[0 if zh else 1]
    failed_index = failed.get("step")
    trigger_index = failed.get("trigger_step")
    failed_action = next((str((step.get("arguments") or {}).get("action") or "") for step in evidence["trajectory"] if step.get("index") == failed_index), "")
    peer_cells = []
    for peer in peers:
        peer_key = f'{peer["engine"]}-{peer["task_id"]}'
        peer_status = ("通过" if peer["status"] == "pass" else "失败") if zh else peer["status"].title()
        peer_cells.append(
            f'<a class="peer {peer["status"]}" href="{peer_key}.html"'
            + (' aria-current="page"' if peer_key == key else '')
            + f'><span>{_e(peer["browser"])}</span><strong>{peer_status}</strong></a>'
        )
    actual_by_index = {int(step["index"]): step for step in evidence["trajectory"]}
    steps = []
    for number, planned in enumerate(task.get("tool_calls") or [], 1):
        actual = actual_by_index.get(number)
        planned_target = planned.get("selector") or planned.get("url") or planned.get("key") or ""
        actual_arguments = (actual or {}).get("arguments") or {}
        actual_target = actual_arguments.get("selector") or actual_arguments.get("url") or actual_arguments.get("key") or ""
        actual_result = str((actual or {}).get("result") or "")
        preview = actual_result.splitlines()[0][:160] if actual_result else ""
        failed_step = failed_index == number
        actual_status = str((actual or {}).get("status") or "")
        status_text = (
            ("未执行", "Not executed") if actual is None
            else ("报错", "Error") if actual_status == "error"
            else ("完成", "Completed")
        )[0 if zh else 1]
        status_class = "status-muted" if actual is None else "status-fail" if actual_status == "error" else "status-pass"
        duration = f'{float(actual.get("duration_ms") or 0) / 1000:.2f} s' if actual else "—"
        steps.append(
            f'<article id="step-{number}" class="aligned-step {"failed-step" if failed_step else ""}">'
            f'<div class="aligned-index"><span class="step-number">{number}</span></div>'
            f'<div class="aligned-standard"><span class="mobile-step-label">{"标准轨迹" if zh else "Qualified input"}</span><strong>{_e(planned.get("action") or "")}</strong>'
            f'<code>{_e(planned_target)}</code><button type="button" data-open-step="{number}">'
            f'{"查看标准参数" if zh else "Qualified arguments"}</button></div>'
            f'<div class="aligned-actual"><span class="mobile-step-label">{"实际执行" if zh else "Actual execution"}</span><div class="aligned-actual-head"><strong>{_e(actual_arguments.get("action") or "—")}</strong>'
            f'<span class="{status_class}">{status_text}</span><span>{_e(duration)}</span></div>'
            f'<code>{_e(actual_target) if actual else "—"}</code>'
            f'<p>{_e(preview) if preview else ("没有工具返回" if zh else "No tool response")}</p>'
            f'<button type="button" data-open-step="{number}">'
            f'{"查看实际参数、日志与报错" if zh else "Actual arguments, logs and error"}</button></div></article>'
        )
    screenshot = (
        f'<figure><a href="{report_root}{evidence["screenshot"]}"><img src="{report_root}{evidence["screenshot"]}" alt="{"失败后的最后页面" if zh else "Last page after failure"}"></a><figcaption>{"失败后保存的最后页面；不等同于错误发生瞬间。点击查看原图。" if zh else "Last page saved after failure, not necessarily the error instant. Open the full image."}</figcaption></figure>'
        if evidence.get("screenshot")
        else ""
    )
    failure_panel = ""
    if attempt["status"] != "pass":
        step_link = f'<a href="#step-{failed_index}">{"跳到失败动作" if zh else "Go to failed action"} #{failed_index}</a>' if failed_index else f'<a href="#step-{trigger_index}">{"查看触发动作" if zh else "Inspect trigger action"} #{trigger_index}</a>' if trigger_index else f'<a href="#trajectory">{"查看已完成动作" if zh else "View completed actions"}</a>'
        action_locator = f'{failed_action} #{failed_index}' if failed_index else ((f'{("触发动作" if zh else "Trigger action")} #{trigger_index}; {("请求未出现" if zh else "request absent")}') if trigger_index else ("所有动作执行后" if zh else "After all actions"))
        expected_network = f'<h3>{"未出现的请求" if zh else "Missing required request"}</h3><pre>{_e(json.dumps(failed["expected_network"], ensure_ascii=False, indent=2))}</pre>' if failed.get("expected_network") else ""
        failure_panel = f'''<section id="diagnosis"><h2>{"失败定位与处理" if zh else "Failure diagnosis and treatment"}</h2><div class="diagnosis-panel"><div class="diagnosis-facts"><div><span>{"失败环节" if zh else "Failure stage"}</span><strong>{_e(stage)}</strong></div><div><span>{"失败位置" if zh else "Failure location"}</span><strong>{_e(action_locator)}</strong></div><div><span>{"复核分类" if zh else "Reviewed category"}</span><strong>{_e(label)}</strong></div></div><h3>{"程序原始判定" if zh else "Exact programmatic finding"}</h3><pre>{_e(failed["message"])}</pre>{expected_network}<h3>{"复核判断与依据" if zh else "Reviewed cause and basis"}</h3><p>{_e(reason)}</p><p>{"处理方" if zh else "Owner"}: {_e(review.get("remediation_owner") if review else "—")} · {"状态" if zh else "Status"}: {_e(review.get("remediation_status") if review else "—")}</p>{step_link}</div></section>'''
    facts = [
        (("完成动作", "Actions completed"), f'{attempt["completed_action_count"]}/{attempt["action_count"]}'),
        (("执行次数", "Executions"), f'{int(attempt.get("physical_execution_count") or 1)} {"次" if zh else ""}'.strip()),
        (("端到端耗时", "End-to-end time"), _format_ms(attempt.get("duration_ms")) if zh else f'{attempt.get("duration_ms", 0) / 1000:.1f} s'),
        (("CPU 时间", "CPU time"), _format_ms((attempt.get("resource") or {}).get("cpu_ms")) if zh else f'{((attempt.get("resource") or {}).get("cpu_ms") or 0) / 1000:.1f} s'),
        (("平均内存", "Mean memory"), _format_mib((attempt.get("resource") or {}).get("memory_mean_bytes"))),
    ]
    fact_html = "".join(f'<div><span>{_e(label[0 if zh else 1])}</span><strong>{_e(value)}</strong></div>' for label, value in facts)
    viewer_file = ("data/evidence/" if zh else "../data/evidence/") + key + ".json"
    body = f'''
    <section class="detail-hero"><p class="eyebrow">{_e(key)}</p><h1>{_e(method["label"])} · {"任务" if zh else "Task"} #{attempt["task_id"]}</h1><p class="task-intent">{_e(task["task"]["intent"])}</p><div class="detail-links"><a href="../task/{attempt["task_id"]}.html">{"查看任务定义与标准轨迹" if zh else "Task contract and qualified trajectory"}</a><a href="../results.html">{"返回结果筛选" if zh else "Back to results"}</a></div></section>
    <section class="compact-section"><h2>{"同题浏览器结果" if zh else "All browser results for this task"}</h2><div class="peer-list">{''.join(peer_cells)}</div></section>
    <section class="compact-section"><div class="verdict-line {attempt["status"]}"><strong>{status}</strong><span>{_e(stage) if attempt["status"] != "pass" else ("固定轨迹及官方验收均通过" if zh else "Qualified trajectory and official evaluation passed")}</span></div><div class="fact-strip">{fact_html}</div></section>
    {failure_panel}
    <section id="trajectory"><h2>{"标准轨迹与实际执行" if zh else "Qualified trajectory and actual execution"}</h2><p>{"左侧是所有浏览器共用的已验证固定输入，右侧是本浏览器的实际操作。未执行不等于动作报错；点击任一步读取完整参数、工具返回、日志和原始报错。" if zh else "The left column is the qualified fixed input shared by all browsers; the right column is this browser's execution. Not executed does not mean an action error. Open a step for exact arguments, tool response, logs and errors."}</p><div class="trajectory-columns"><span>#</span><strong>{"标准轨迹" if zh else "Qualified input"}</strong><strong>{"本浏览器实际执行" if zh else "Actual execution in this browser"}</strong></div><div class="trace-list">{''.join(steps)}</div></section>
    <dialog id="step-dialog" data-evidence-url="{report_root}data/evidence/{key}.json" data-standard-url="{report_root}data/trajectory/{attempt['task_id']}.json"><div class="dialog-head"><strong data-step-title></strong><button type="button" data-close-step aria-label="{'关闭逐步证据' if zh else 'Close step evidence'}">×</button></div><div class="dialog-body"><p data-step-status></p><div class="step-dialog-grid"><div><h3>{"标准动作参数" if zh else "Qualified action arguments"}</h3><pre data-step-planned></pre></div><div><h3>{"实际动作参数" if zh else "Actual action arguments"}</h3><pre data-step-actual></pre></div></div><h3>{"工具返回、日志与报错" if zh else "Tool response, logs and error"}</h3><pre data-step-result></pre><div data-step-finding hidden><h3>{"程序原始失败判定" if zh else "Exact programmatic failure"}</h3><pre data-step-error></pre></div><p data-step-image-wrap hidden><a data-step-image target="_blank" rel="noopener">{"查看最后观测页面（并非该步截图）" if zh else "Open last observed page (not a screenshot of this step)"}</a></p><p><a href="../evidence-viewer.html?file={viewer_file}">{"查看完整执行证据" if zh else "Inspect complete execution evidence"}</a></p></div></dialog>
    <section id="evidence"><h2>{"验收、资源与页面证据" if zh else "Evaluation, resources, and page evidence"}</h2><div class="evidence-actions"><a href="../evidence-viewer.html?file={viewer_file}">{"网页查看完整证据" if zh else "Inspect complete evidence in the browser"}</a><a href="{report_root}data/evidence/{key}.json">{"下载净化 JSON" if zh else "Download sanitized JSON"}</a></div>{screenshot}</section>'''
    title = (
        f"{method['label']} · 任务 #{attempt['task_id']}"
        if zh
        else f"{method['label']} · Task #{attempt['task_id']}"
    )
    return _shell(lang, "results", title, body, depth=1, current_route=f"result/{key}.html")


def _task_page(lang: str, task: dict[str, Any], peers: list[dict[str, Any]]) -> str:
    zh = lang == "zh"
    task_id = int(task["task_id"])
    source_kind = str((task.get("source") or {}).get("kind") or "")
    source_label = (
        ("来自已通过的自主浏览器执行", "From a successful autonomous browser run")
        if source_kind == "authoritative_successful_browser_use_execution"
        else ("修复后在基准浏览器重新验证", "Repaired and re-qualified on the reference browser")
    )[0 if zh else 1]
    peer_rows = "".join(
        f'<tr><td><a href="../result/{peer["engine"]}-{task_id}.html">{_e(peer["browser"])}</a></td>'
        f'<td class="status-{peer["status"]}">{("通过" if peer["status"] == "pass" else "失败") if zh else peer["status"].title()}</td>'
        f'<td>{peer["completed_actions"]}/{peer["action_count"]}</td>'
        f'<td>{peer.get("execution_count", 1)}</td>'
        f'<td><a href="../result/{peer["engine"]}-{task_id}.html{("#diagnosis" if peer["status"] != "pass" else "#trajectory")}">{"查看诊断与执行轨迹" if zh else "Diagnosis and executed actions"}</a></td></tr>'
        for peer in peers
    )
    planned_rows = []
    for index, call in enumerate(task.get("tool_calls") or [], 1):
        action = str(call.get("action") or "")
        target = str(call.get("selector") or call.get("url") or call.get("key") or "")
        planned_rows.append(
            f'<tr><td>{index}</td><td><code>{_e(action)}</code></td><td><code>{_e(target)}</code></td>'
            f'<td><details><summary>{"查看完整参数" if zh else "Full arguments"}</summary><pre>{_e(json.dumps(_clean(call), ensure_ascii=False, indent=2))}</pre></details></td></tr>'
        )
    contract = task.get("evaluation_input") or {}
    contract_type = str(contract.get("task_type") or "")
    contract_summary = ({"RETRIEVE": "信息提取", "NAVIGATE": "页面导航", "MUTATE": "页面操作"}.get(contract_type, contract_type) if zh else contract_type) or "—"
    body = f'''<section class="detail-hero"><p class="eyebrow">WebArena-Verified · #{task_id}</p><h1>{"任务定义与标准轨迹" if zh else "Task contract and qualified trajectory"}</h1><p class="task-intent">{_e(task["task"]["intent"])}</p><div class="detail-links"><a href="../dataset.html">{"返回评测集" if zh else "Back to dataset"}</a><a href="../results.html">{"筛选实测结果" if zh else "Explore measured results"}</a></div></section>
        <section class="compact-section"><div class="fact-strip"><div><span>{"站点" if zh else "Sites"}</span><strong>{_e(", ".join(task["task"].get("sites") or []))}</strong></div><div><span>{"轨迹来源" if zh else "Trajectory source"}</span><strong>{source_label}</strong></div><div><span>{"计划动作" if zh else "Planned actions"}</span><strong>{len(task.get("tool_calls") or [])}</strong></div><div><span>{"任务类型" if zh else "Task type"}</span><strong>{contract_summary}</strong></div></div></section>
    <section><h2>{"同题所有浏览器结果" if zh else "All browser outcomes on this task"}</h2><div class="table-scroll"><table class="task-peer-table"><thead><tr><th>{"浏览器" if zh else "Browser"}</th><th>{"结果" if zh else "Verdict"}</th><th>{"完成动作" if zh else "Completed actions"}</th><th>{"执行次数" if zh else "Executions"}</th><th>{"证据" if zh else "Evidence"}</th></tr></thead><tbody>{peer_rows}</tbody></table></div></section>
    <section><h2>{"冻结的操作轨迹" if zh else "Frozen action trajectory"}</h2><p>{"这是所有浏览器共用的输入；下方记录的是预定动作，不是任一浏览器的实测执行结果。" if zh else "The same input is used for every browser. These are planned actions, not any browser's measured execution."}</p><div class="table-scroll"><table class="planned-table"><thead><tr><th>#</th><th>{"动作" if zh else "Action"}</th><th>{"目标" if zh else "Target"}</th><th>{"参数" if zh else "Arguments"}</th></tr></thead><tbody>{''.join(planned_rows)}</tbody></table></div></section>
    <section><h2>{"验收输入" if zh else "Evaluation input"}</h2><p>{"供官方 evaluator 判分；不是 Agent 提示词，也不是唯一正确路径。" if zh else "Used by the official evaluator, not an Agent prompt or the only correct path."}</p><details><summary>{"查看答案与验收字段" if zh else "Inspect answer and acceptance fields"}</summary><pre>{_e(json.dumps(_clean(contract), ensure_ascii=False, indent=2))}</pre></details></section>'''
    title = f"任务 #{task_id} · 评测集" if zh else f"Task #{task_id} · Dataset"
    return _shell(lang, "dataset", title, body, depth=1, current_route=f"task/{task_id}.html")


def _assets(output: Path) -> None:
    css = r''':root{--bg:#f4f7fb;--panel:#fff;--ink:#102235;--muted:#60748a;--line:#d4e0ec;--accent:#0969da;--good:#087f4f;--bad:#c52c45;--wash:#eaf3ff}html[data-theme=dark]{--bg:#0c1520;--panel:#121f2d;--ink:#eef5fb;--muted:#9db0c2;--line:#2b4054;--accent:#62a8ff;--good:#4ed29b;--bad:#ff7d91;--wash:#142d49}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}.top{height:70px;display:flex;align-items:center;gap:28px;padding:0 max(24px,calc((100% - 1440px)/2));background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}.brand{font-weight:900;color:var(--ink);text-decoration:none;white-space:nowrap}.top nav{display:flex;gap:22px;overflow:auto;flex:1}.top nav a,.language{color:var(--accent);text-decoration:none;white-space:nowrap}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;border-bottom:3px solid var(--ink)}.theme{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:50%;width:38px;height:38px}main,footer{width:min(1440px,calc(100% - 48px));margin:auto}footer{padding:36px 0;color:var(--muted);border-top:1px solid var(--line)}section{padding:34px 0}.hero{padding:46px 0 28px}.hero h1,.detail-hero h1{font-size:clamp(36px,5vw,68px);line-height:1.08;margin:8px 0 14px;letter-spacing:-.04em}.hero p,.detail-hero p{max-width:880px;color:var(--muted);font-size:18px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;color:var(--accent)!important;font-size:13px!important;font-weight:800}h2{font-size:30px;margin:0 0 16px}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel)}table{border-collapse:collapse;width:100%;min-width:900px}th,td{text-align:left;vertical-align:top;padding:14px 16px;border-bottom:1px solid var(--line)}th{background:color-mix(in srgb,var(--wash) 70%,var(--panel));font-size:14px;position:sticky;top:0}td{overflow-wrap:anywhere}.metrics td{min-width:150px}.metric-label{display:inline-block;width:42px;color:var(--muted);font-size:13px}.winner{color:var(--good)}.winner>span{display:inline-grid;place-items:center;margin-left:7px;min-width:24px;height:24px;padding:0 6px;border-radius:12px;background:var(--good);color:white;font-size:12px}.callout{padding:16px 18px;border-left:4px solid var(--accent);background:var(--panel);border-radius:0 12px 12px 0}.summary-grid,.diagnosis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.summary-grid article,.diagnosis-grid article{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;min-height:150px}.summary-grid strong,.diagnosis-grid strong{display:block;font-size:24px;margin:8px 0}.summary-grid span,.diagnosis-grid span{color:var(--muted);font-size:13px}.verdict.fail{border-left:5px solid var(--bad)}.verdict.pass{border-left:5px solid var(--good)}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.filters input,.filters select{padding:10px 12px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;min-width:170px}.inventory{height:620px;overflow:auto}.inventory table{min-width:1500px}.inventory tbody tr{cursor:pointer}.inventory tbody tr:hover{background:var(--wash)}.status-pass{color:var(--good);font-weight:800}.status-fail{color:var(--bad);font-weight:800}.failed-step{background:color-mix(in srgb,var(--bad) 10%,var(--panel))}.failed-step td:first-child{border-left:4px solid var(--bad)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#071923;color:#dff4ff;padding:14px;border-radius:10px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}figure{margin:22px 0}figure img{display:block;max-width:100%;border:1px solid var(--line);border-radius:12px}figcaption{color:var(--muted);font-size:13px}.evidence-actions{display:flex;gap:12px;flex-wrap:wrap}.evidence-actions a{padding:10px 14px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--accent);text-decoration:none}.detail-hero{padding-top:40px}.viewer{height:75vh}.viewer pre{height:100%;max-height:none}dialog{width:min(850px,calc(100% - 32px));border:1px solid var(--line);border-radius:16px;background:var(--panel);color:var(--ink);padding:0}dialog::backdrop{background:#07111bbf}.dialog-head{display:flex;justify-content:space-between;padding:18px;border-bottom:1px solid var(--line)}.dialog-body{padding:18px}.dialog-head button{border:0;background:none;color:var(--ink);font-size:24px}.mermaid{width:100%;max-width:100%;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px;overflow:auto}.flow-fallback{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:14px}.flow-node{min-width:150px;padding:14px;border-radius:10px;background:var(--wash)}.flow-node strong{display:block;color:var(--accent);margin-bottom:6px}.flow-arrow{display:grid;place-items:center;color:var(--muted);font-size:24px}@media(max-width:720px){.top{padding:0 16px;gap:14px}.top nav{gap:14px}main,footer{width:calc(100% - 28px)}section{padding:26px 0}.hero h1,.detail-hero h1{font-size:36px}.inventory{height:560px}.language{display:none}}'''
    css += r'''
html{scroll-padding-top:88px}a{color:var(--accent)}button,summary{cursor:pointer}.top nav{min-width:0}
.top nav a{padding:20px 0 15px}.top nav a:hover{text-decoration:underline}
.hero{padding:54px 0 38px;border-bottom:1px solid var(--line)}.hero h1{max-width:1100px}
.compact-section{padding:22px 0}.detail-hero{padding:40px 0 28px;border-bottom:1px solid var(--line)}
.detail-hero h1{font-size:clamp(34px,4vw,56px)}.task-intent{max-width:1040px!important;color:var(--ink)!important;line-height:1.55}
.detail-links,.evidence-actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}
.detail-links a,.evidence-actions a,.dialog-body .evidence-actions a{display:inline-flex;align-items:center;min-height:42px;padding:9px 13px;border:1px solid var(--line);background:var(--panel);border-radius:9px;text-decoration:none;font-weight:700}
.detail-links a:hover,.evidence-actions a:hover{border-color:var(--accent);background:var(--wash)}
.peer-list{display:flex;gap:12px;overflow:auto;padding:3px 1px}.peer{display:flex;flex-direction:column;gap:3px;min-width:170px;padding:12px 16px;border:1px solid var(--line);border-radius:12px;background:var(--panel);text-decoration:none;color:var(--ink)}
.peer strong{color:var(--good)}.peer.fail strong{color:var(--bad)}.peer[aria-current=page]{border:2px solid var(--accent);background:var(--wash)}
.verdict-line{display:flex;align-items:center;gap:14px;padding:15px 18px;border-radius:12px;background:color-mix(in srgb,var(--good) 8%,var(--panel));border-left:5px solid var(--good)}
.verdict-line.fail{background:color-mix(in srgb,var(--bad) 9%,var(--panel));border-color:var(--bad)}.verdict-line strong{font-size:25px;color:var(--good)}.verdict-line.fail strong{color:var(--bad)}
.fact-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:12px}.fact-strip>div{padding:13px 16px;background:var(--panel);border:1px solid var(--line);border-radius:11px;min-height:84px}
.fact-strip span,.diagnosis-facts span{display:block;color:var(--muted);font-size:13px}.fact-strip strong,.diagnosis-facts strong{display:block;margin-top:7px;font-size:16px;line-height:1.35;overflow-wrap:anywhere}
.diagnosis-panel{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--bad);border-radius:14px;padding:20px 24px;max-width:1120px}
.diagnosis-panel h3{margin:20px 0 8px}.diagnosis-panel p{max-width:1000px}.diagnosis-panel pre,.dialog-body pre{max-height:360px;white-space:pre-wrap}
.diagnosis-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.diagnosis-facts>div{padding:12px;background:var(--wash);border-radius:10px}
.trace-list{display:grid;grid-template-columns:minmax(0,1fr);gap:9px;max-width:1220px}.trace-step{min-width:0;padding:13px 18px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.trace-step.failed-step{border:2px solid var(--bad);background:color-mix(in srgb,var(--bad) 6%,var(--panel))}.step-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.step-number{display:grid;place-items:center;width:29px;height:29px;background:var(--wash);border-radius:50%;font-weight:800}.step-heading strong{min-width:90px}.step-heading>span:last-child{margin-left:auto;color:var(--muted)}
.step-target{margin:7px 0 0 41px;color:var(--muted);font:13px/1.55 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.step-preview{margin:6px 0 0 41px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trace-step details{margin:10px 0 0 41px}.trace-step summary,.matrix-disclosure summary{color:var(--accent);font-weight:700}.step-data{display:grid;grid-template-columns:minmax(260px,1fr) minmax(340px,2fr);gap:12px;min-width:0}.step-data>div{min-width:0}.step-data pre{max-width:100%;max-height:380px}
.dataset-table table{min-width:600px}.dataset-table td,.dataset-table th{overflow-wrap:normal;white-space:nowrap}.task-peer-table{min-width:650px}.planned-table{min-width:900px}.planned-table td:nth-child(3){min-width:320px;max-width:500px;overflow-wrap:anywhere}
.matrix-disclosure{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 18px}.matrix-disclosure[open] summary{margin-bottom:13px}.matrix-table{max-height:570px}.matrix-table table{min-width:700px}
.filters{align-items:end;gap:12px}.filters label{display:flex;flex-direction:column;gap:5px;font-size:13px;font-weight:700;color:var(--muted)}.filters input,.filters select{min-width:140px;max-width:260px;font-size:15px}
.inventory{height:610px}.inventory table{min-width:1060px}.inventory th,.inventory td{white-space:nowrap;overflow-wrap:normal;vertical-align:middle}.inventory th:nth-child(7),.inventory td:nth-child(7){min-width:230px;max-width:280px;white-space:normal;overflow-wrap:anywhere}.inventory tbody tr:hover{background:var(--wash)}
.inventory button{border:1px solid var(--accent);color:var(--accent);background:var(--panel);padding:6px 10px;border-radius:7px;font-weight:700;margin-left:10px}.inventory button:hover{background:var(--wash)}.result-count{color:var(--muted);font-size:14px}
dialog{max-height:90vh;overflow:auto}.dialog-head{position:sticky;top:0;background:var(--panel);z-index:1}.dialog-body{padding:20px 24px}.dialog-body h3{margin:18px 0 8px}.dialog-body .evidence-actions{margin-bottom:4px}
.viewer-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.viewer-tabs button{padding:9px 13px;border:1px solid var(--line);background:var(--panel);color:var(--accent);border-radius:8px}.viewer-tabs button[aria-selected=true]{background:var(--accent);color:white}.viewer{height:auto;min-height:50vh}.viewer pre{min-height:45vh;max-height:65vh}
@media(max-width:720px){.hero{padding:30px 0 24px}.top{height:60px}.top nav a{padding:15px 0}.detail-hero h1{font-size:34px}.step-data{grid-template-columns:minmax(0,1fr)}.step-heading>span:last-child{margin-left:0}.trace-step details,.step-target,.step-preview{margin-left:0}.filters label{flex:1 1 145px}.filters input,.filters select{min-width:0;width:100%}.diagnosis-panel{padding:16px}.fact-strip{grid-template-columns:repeat(2,minmax(0,1fr))}}
:root{--bg:#f6f8fb;--ink:#17212b;--muted:#5d6b78;--line:#d9e2ea;--accent:#1268d8;--good:#117a47;--bad:#c22e43;--wash:#e9f2ff;--panel2:#eef3f8;--shadow:0 8px 28px rgba(30,55,80,.08)}html[data-theme=dark]{--bg:#071015;--panel:#0d1a21;--ink:#edf6f7;--muted:#9aadb4;--line:#263c46;--accent:#49d6ed;--good:#67df9f;--bad:#ff6b7a;--wash:#10303a;--panel2:#122630;--shadow:none}
body{background:linear-gradient(180deg,var(--wash) 0,transparent 240px),var(--bg);font-size:15px}.top{height:58px;gap:17px;padding:0 max(20px,calc((100% - 1180px)/2));background:color-mix(in srgb,var(--panel) 92%,transparent);backdrop-filter:blur(14px)}.top nav{gap:17px}.top nav a,.language{font-size:14px}.theme{border-radius:999px;width:auto;height:auto;padding:6px 12px}main,footer{width:min(1180px,calc(100% - 40px))}section{padding:38px 0;border-top:1px solid var(--line)}.hero{padding:38px 0 30px;border:0}.hero h1{font-size:clamp(34px,5vw,58px)}h2{font-size:clamp(25px,3vw,36px);line-height:1.16}.table-scroll{border-radius:13px;box-shadow:var(--shadow)}th{background:var(--panel2);font-size:12px;white-space:nowrap}tbody tr:hover{background:var(--wash)}
.method-comparison{padding:24px;border:1px solid var(--line);border-radius:18px;background:var(--panel);box-shadow:var(--shadow)}.comparison-heading{display:flex;justify-content:space-between;align-items:start;gap:24px;margin-bottom:20px}.comparison-heading h2{margin-bottom:6px;font-size:25px}.comparison-heading p{margin:0;color:var(--muted)}.comparison-count{white-space:nowrap;padding:7px 11px;border-radius:999px;background:var(--wash);color:var(--accent);font-size:13px}.comparison-controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.comparison-controls label{display:grid;gap:5px;font-size:13px;font-weight:700}.comparison-controls select{width:100%;padding:10px 12px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:9px;font:inherit}.comparison-methods{border:0;padding:0;margin:18px 0}.comparison-methods legend{font-weight:700}.method-checks,.outcome-list{display:flex;gap:7px;flex-wrap:wrap}.compare-chip{display:flex;align-items:center;gap:6px;padding:7px 11px;border:1px solid var(--line);border-radius:999px;background:var(--bg);cursor:pointer}.compare-chip.disabled{opacity:.45;cursor:not-allowed}.compare-chip input{accent-color:var(--accent)}.outcome-pill{display:inline-block;padding:5px 9px;border-radius:8px;font-size:13px;white-space:nowrap;text-decoration:none}.outcome-pill.pass{background:color-mix(in srgb,var(--good) 12%,var(--panel));color:var(--good)}.outcome-pill.fail{background:color-mix(in srgb,var(--bad) 12%,var(--panel));color:var(--bad)}.matrix-table table{min-width:760px}.matrix-table th:nth-child(n+3),.matrix-table td:nth-child(n+3){min-width:270px}.matrix-table td{vertical-align:middle;overflow-wrap:normal}
.dataset-browser{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;box-shadow:var(--shadow)}.dataset-browser h2{margin-bottom:4px}.dataset-browser>p{margin:0;color:var(--muted)}.dataset-controls{display:grid;grid-template-columns:minmax(220px,2fr) repeat(3,minmax(145px,1fr));gap:12px;margin:22px 0 10px}.dataset-controls label{display:grid;gap:5px;color:var(--muted);font-size:13px;font-weight:700}.dataset-controls input,.dataset-controls select{width:100%;padding:10px 12px;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:9px;font:inherit}.dataset-count{color:var(--accent);font-weight:700;margin:10px 0}.dataset-table{height:590px}.dataset-table table{min-width:950px}.dataset-table td,.dataset-table th{vertical-align:middle;white-space:normal;overflow-wrap:normal}.dataset-table td:nth-child(2){min-width:190px}.dataset-table td:nth-child(3){min-width:330px;max-width:460px}.intent-preview{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.45}.dataset-table td:last-child{white-space:nowrap}.dataset-table a{font-weight:700}.dataset-actions{display:flex;gap:10px;flex-wrap:wrap}.dataset-actions a{white-space:nowrap}.dataset-table tbody tr:hover{background:var(--wash)}.dataset-empty{color:var(--muted);padding:20px 0}
.leader-grid,.reading-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:18px 0}.leader-grid article,.reading-grid article{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;min-height:148px}.leader-grid article.winner-card{border-color:var(--good);box-shadow:0 0 0 2px color-mix(in srgb,var(--good) 18%,transparent),var(--shadow)}.leader-grid span,.leader-grid small{display:block;color:var(--muted)}.leader-grid strong{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:20px;margin:7px 0 2px}.leader-grid strong em{padding:2px 7px;border-radius:999px;background:var(--good);color:white;font-size:11px;font-style:normal}.leader-grid .tie-card strong em{background:var(--muted)}.leader-grid b{display:block;color:var(--good);font-size:24px;margin-bottom:5px}.reading-grid h3{margin:0 0 8px}.reading-grid p{margin:0;color:var(--muted)}
.metric-groups{display:grid;gap:20px;margin-top:18px}.metric-group{padding:0}.metric-group-head{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:8px}.metric-group-head h3{font-size:22px;margin:0}.metric-group-head p{max-width:720px;margin:0;color:var(--muted);font-size:13px;text-align:right}.metric-group table{min-width:780px}.metric-group td:first-child{min-width:190px;font-weight:700}.compact-table{margin-top:18px}.compact-table table{min-width:560px}.compact-table td:first-child{width:75%}
.metric-groups,.metric-group{min-width:0}.mermaid svg{min-width:850px;height:auto!important}.flow-fallback{font-size:14px}.flow-node{min-width:170px}
.review-table table{min-width:1120px}.review-table td:first-child{min-width:145px;font-weight:700}.review-table td:nth-child(n+2){min-width:230px;max-width:320px}.review-table td{overflow-wrap:anywhere}
.trajectory-columns,.aligned-step{display:grid;grid-template-columns:48px minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start}
.trajectory-columns{padding:0 18px 10px;color:var(--muted);font-size:13px}.aligned-step{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 18px}
.aligned-step.failed-step{border:2px solid var(--bad);background:color-mix(in srgb,var(--bad) 5%,var(--panel))}.aligned-standard,.aligned-actual{min-width:0;display:grid;gap:7px}.aligned-standard{border-right:1px solid var(--line);padding-right:12px}.aligned-step code{color:var(--muted);font:13px/1.45 ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.aligned-step button{justify-self:start;border:0;background:transparent;color:var(--accent);font:inherit;font-weight:700;padding:4px 0;text-align:left}.aligned-actual-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.aligned-actual-head span:last-child{margin-left:auto;color:var(--muted)}.aligned-actual p{margin:0;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.status-muted{color:var(--muted)}
.mobile-step-label{display:none;color:var(--muted);font-size:12px}.step-dialog-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.step-dialog-grid>div{min-width:0}.step-dialog-grid pre{max-height:250px}.step-dialog-grid h3{margin-top:8px}.step-dialog-grid pre,[data-step-result],[data-step-error]{max-width:100%}
@media(max-width:760px){.top{padding:0 14px}.comparison-heading{display:block}.comparison-count{display:inline-block;margin-top:10px}.comparison-controls{grid-template-columns:1fr}.method-comparison{padding:16px}main,footer{width:calc(100% - 28px)}}
@media(max-width:760px){.dataset-browser{padding:16px}.dataset-controls{grid-template-columns:repeat(2,minmax(0,1fr))}.dataset-controls label:first-child{grid-column:1/-1}.dataset-table{height:510px}}
@media(max-width:760px){.metric-group-head{display:block}.metric-group-head p{text-align:left;margin-top:4px}.leader-grid,.reading-grid{grid-template-columns:1fr}}
@media(max-width:760px){.trajectory-columns{display:none}.aligned-step{grid-template-columns:34px minmax(0,1fr);padding:12px}.aligned-index{grid-row:1/3}.aligned-standard{border-right:0;border-bottom:1px solid var(--line);padding:0 0 10px}.mobile-step-label{display:block}.step-dialog-grid{grid-template-columns:1fr}}
'''
    js = r'''const lang=document.documentElement.lang;
const zh=lang==='zh';
const base=zh?'':'../';
document.documentElement.dataset.theme=localStorage.getItem('report-theme')||'light';
document.querySelector('.theme')?.addEventListener('click',()=>{
  const root=document.documentElement;
  root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('report-theme',root.dataset.theme);
});
const fetchJson=async path=>{const response=await fetch(path);if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json()};
const datasetTable=document.querySelector('[data-dataset-table]');
if(datasetTable){
  const rows=[...datasetTable.querySelectorAll('tbody tr')];
  const controls=[...document.querySelectorAll('[data-dataset-filter]')];
  const params=new URLSearchParams(location.search);
  controls.forEach(control=>{const value=params.get(control.dataset.datasetFilter);if(value!==null)control.value=value});
  const applyDataset=()=>{
    const query=(document.querySelector('[data-dataset-filter=q]')?.value||'').trim().toLocaleLowerCase();
    let shown=0;
    rows.forEach(row=>{
      const matches=controls.every(control=>!control.value||control.dataset.datasetFilter==='q'||(control.dataset.datasetFilter==='site'?(row.dataset.sites||'').split(' ').includes(control.value):row.dataset[control.dataset.datasetFilter]===control.value))&&(!query||(row.dataset.search||row.textContent).toLocaleLowerCase().includes(query));
      row.hidden=!matches;if(matches)shown++;
    });
    document.querySelector('[data-dataset-count]').textContent=zh?`显示 ${shown} / ${rows.length} 个任务`:`Showing ${shown} / ${rows.length} tasks`;
    document.querySelector('[data-dataset-empty]').hidden=shown!==0;
    const next=new URLSearchParams();controls.forEach(control=>{if(control.value)next.set(control.dataset.datasetFilter,control.value)});
    history.replaceState(null,'',location.pathname+(next.size?'?'+next:''));
  };
  controls.forEach(control=>control.addEventListener(control.tagName==='INPUT'?'input':'change',applyDataset));
  applyDataset();
}
const matrix=document.querySelector('[data-task-matrix]');
if(matrix){
  const matrixRows=[...matrix.querySelectorAll('tbody tr')];
  const relation=document.querySelector('[data-matrix-relation]');
  const site=document.querySelector('[data-matrix-site]');
  const baseline=document.querySelector('[data-matrix-baseline]');
  const boxes=[...document.querySelectorAll('[data-matrix-method]')];
  const count=document.querySelector('[data-matrix-count]');
  const applyMatrix=()=>{
    boxes.forEach(box=>{box.disabled=box.value===baseline.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});
    const others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value);
    const selected=[baseline.value,...others];
    let shown=0;
    matrixRows.forEach(row=>{
      const pass=new Set((row.dataset.pass||'').split(' ').filter(Boolean));
      const fail=new Set((row.dataset.fail||'').split(' ').filter(Boolean));
      row.querySelectorAll('a[data-engine]').forEach(link=>{link.hidden=!selected.includes(link.dataset.engine)});
      const matchRelation=others.length>0&&(!relation.value||(relation.value==='different'&&selected.some(engine=>pass.has(engine))&&selected.some(engine=>fail.has(engine)))||(relation.value==='baseline-pass-others-fail'&&pass.has(baseline.value)&&others.every(engine=>fail.has(engine)))||(relation.value==='baseline-fail-others-pass'&&fail.has(baseline.value)&&others.every(engine=>pass.has(engine)))||(relation.value==='all-pass'&&selected.every(engine=>pass.has(engine)))||(relation.value==='all-fail'&&selected.every(engine=>fail.has(engine)))||(relation.value==='exact-pass'&&selected.every(engine=>pass.has(engine))&&pass.size===selected.length));
      const matchSite=!site.value||row.dataset.site===site.value;
      row.hidden=!(matchRelation&&matchSite);
      if(!row.hidden)shown++;
    });
    count.textContent=others.length?(zh?`找到 ${shown} / ${matrixRows.length} 个任务`:`Showing ${shown} / ${matrixRows.length} tasks`):(zh?'请至少选择一个对比浏览器':'Select at least one comparison browser');
  };
  baseline.addEventListener('change',()=>{boxes.forEach(box=>box.checked=box.value!==baseline.value);applyMatrix()});
  [...boxes,relation,site].forEach(control=>control.addEventListener('change',applyMatrix));
  applyMatrix();
}
const inventory=document.querySelector('[data-results]');
if(inventory){
  const rows=[...inventory.querySelectorAll('tbody tr')];
  const controls=[...document.querySelectorAll('[data-filter]')];
  const dialog=document.querySelector('#diagnosis-dialog');
  const params=new URLSearchParams(location.search);
  let byKey={};
  controls.forEach(control=>{
    const selected=params.get(control.dataset.filter);
    if(selected!==null)control.value=selected;
  });
  function applyFilters(){
    const query=(document.querySelector('[data-filter=q]')?.value||'').toLowerCase();
    const task=params.get('task');
    let shown=0;
    rows.forEach(row=>{
      const result=byKey[row.dataset.key];
      const haystack=(row.textContent+' '+(result?.intent||'')+' '+(result?.program_error||'')+' '+(result?.reason_zh||'')+' '+(result?.reason_en||'')).toLowerCase();
      const matches=controls.every(control=>!control.value||control.dataset.filter==='q'||row.dataset[control.dataset.filter]===control.value)&&(!task||row.dataset.task===task)&&(!query||haystack.includes(query));
      row.hidden=!matches;
      if(matches)shown++;
    });
    const counter=document.querySelector('[data-result-count]');
    if(counter)counter.textContent=zh?`显示 ${shown} / ${rows.length} 条结果`:`Showing ${shown} / ${rows.length} results`;
  }
  controls.forEach(control=>control.addEventListener('input',applyFilters));
  fetchJson(base+'data/results.json').then(data=>{byKey=Object.fromEntries(data.map(item=>[item.key,item]));inventory.dataset.resultsReady='true';applyFilters()}).catch(()=>applyFilters());
  inventory.addEventListener('click',async event=>{
    if(event.target.closest('a'))return;
    const row=event.target.closest('tr[data-key]');
    if(!row||!dialog)return;
    const result=byKey[row.dataset.key];
    if(!result)return;
    const detail='result/'+result.key+'.html';
    dialog.querySelector('[data-dialog-title]').textContent=result.browser+' · '+(zh?'任务':'Task')+' #'+result.task_id;
    dialog.querySelector('[data-dialog-stage]').textContent=result.failure_stage==='browser_tool_execution'?(zh?'浏览器动作':'Browser action'):result.failure_stage==='side_effect_not_observed'?(zh?'提交请求未出现':'Submission request absent'):result.failure_stage==='official_evaluation'?(zh?'官方验收':'Official evaluation'):(zh?'无':'None');
    dialog.querySelector('[data-dialog-error]').textContent=zh?'正在读取完整证据…':'Loading complete evidence…';
    delete dialog.dataset.loaded;
    dialog.querySelector('[data-dialog-link]').href=detail;
    dialog.querySelector('[data-dialog-step-link]').href=detail+'#trajectory';
    dialog.showModal();
    try{
      const evidence=await fetchJson(base+'data/evidence/'+result.key+'.json');
      const step=evidence.programmatic_failure?.step;
      const trigger=evidence.programmatic_failure?.trigger_step;
      const failedAction=(evidence.trajectory||[]).find(action=>action.index===step);
      dialog.querySelector('[data-dialog-step]').textContent=step?(failedAction?.arguments?.action||'')+' #'+step:trigger?(zh?'触发动作':'Trigger action')+' #'+trigger:(zh?'动作完成后':'After actions completed');
      dialog.querySelector('[data-dialog-step-link]').href=detail+(step?'#step-'+step:trigger?'#step-'+trigger:'#trajectory');
      dialog.querySelector('[data-dialog-category]').textContent=(zh?evidence.review?.label_zh:evidence.review?.label_en)||(zh?'未复核':'Not reviewed');
      const exact=step?(failedAction?.result||evidence.programmatic_failure?.message||''):evidence.programmatic_failure?.stage==='side_effect_not_observed'?(evidence.programmatic_failure.message+'\n\nExpected network event:\n'+JSON.stringify(evidence.programmatic_failure.expected_network,null,2)):evidence.official_evaluation?JSON.stringify(evidence.official_evaluation,null,2):(evidence.programmatic_failure?.message||'');
      dialog.querySelector('[data-dialog-error]').textContent=exact;
      dialog.querySelector('[data-dialog-cause]').textContent=(zh?evidence.review?.reason_zh:evidence.review?.reason_en)||(zh?'尚无复核判断':'No reviewed cause');
      dialog.querySelector('[data-dialog-remediation]').textContent=(zh?'处理方：':'Owner: ')+(evidence.review?.remediation_owner||'—')+' · '+(zh?'状态：':'Status: ')+(evidence.review?.remediation_status||'—');
      dialog.dataset.loaded='true';
    }catch(error){dialog.querySelector('[data-dialog-error]').textContent=(zh?'证据读取失败：':'Evidence loading failed: ')+error.message;dialog.dataset.loaded='error'}
  });
  dialog?.querySelector('.dialog-head button')?.addEventListener('click',()=>dialog.close());
}
const stepDialog=document.querySelector('#step-dialog');
if(stepDialog){
  let requestId=0;
  let evidencePromise;
  let standardPromise;
  const evidence=()=>evidencePromise??=fetchJson(stepDialog.dataset.evidenceUrl);
  const standard=()=>standardPromise??=fetchJson(stepDialog.dataset.standardUrl);
  document.querySelector('#trajectory')?.addEventListener('click',async event=>{
    const button=event.target.closest('button[data-open-step]');
    if(!button)return;
    const index=Number(button.dataset.openStep);
    const current=++requestId;
    stepDialog.querySelector('[data-step-title]').textContent=(zh?'第 ':'Step ')+index+(zh?' 步':'');
    stepDialog.querySelector('[data-step-status]').textContent=zh?'正在读取逐步证据…':'Loading step evidence…';
    for(const key of ['planned','actual','result','error'])stepDialog.querySelector('[data-step-'+key+']').textContent='';
    stepDialog.querySelector('[data-step-finding]').hidden=true;
    stepDialog.querySelector('[data-step-image-wrap]').hidden=true;
    if(!stepDialog.open)stepDialog.showModal();
    try{
      const [frozen,measured]=await Promise.all([standard(),evidence()]);
      if(current!==requestId||!stepDialog.open)return;
      const planned=frozen.tool_calls[index-1];
      const actual=(measured.trajectory||[]).find(item=>item.index===index);
      if(!planned)throw Error(zh?'标准轨迹缺少该步':'Qualified trajectory is missing this step');
      stepDialog.querySelector('[data-step-planned]').textContent=JSON.stringify(planned,null,2);
      stepDialog.querySelector('[data-step-actual]').textContent=actual?JSON.stringify(actual.arguments||{},null,2):(zh?'未执行':'Not executed');
      stepDialog.querySelector('[data-step-result]').textContent=actual?(actual.result||''):(zh?'该步没有工具调用或返回。':'No tool call or response for this step.');
      stepDialog.querySelector('[data-step-status]').textContent=actual
        ?(zh?'实际状态：':'Actual status: ')+(actual.status||'—')+' · '+((Number(actual.duration_ms)||0)/1000).toFixed(2)+' s'
        :(zh?'实际状态：未执行':'Actual status: not executed');
      const finding=measured.programmatic_failure;
      if(finding&&(finding.step===index||finding.trigger_step===index)){
        stepDialog.querySelector('[data-step-finding]').hidden=false;
        stepDialog.querySelector('[data-step-error]').textContent=finding.message||'';
        if(finding.expected_network)stepDialog.querySelector('[data-step-error]').textContent+='\n\n'+JSON.stringify(finding.expected_network,null,2);
      }
      if(measured.screenshot){
        stepDialog.querySelector('[data-step-image-wrap]').hidden=false;
        stepDialog.querySelector('[data-step-image]').href=(zh?'../':'../../')+measured.screenshot;
      }
    }catch(error){
      if(current!==requestId||!stepDialog.open)return;
      stepDialog.querySelector('[data-step-status]').textContent=(zh?'逐步证据读取失败：':'Could not load step evidence: ')+error.message;
      evidencePromise=undefined;standardPromise=undefined;
    }
  });
  stepDialog.querySelector('[data-close-step]')?.addEventListener('click',()=>stepDialog.close());
  stepDialog.addEventListener('close',()=>{requestId++});
}
const viewer=document.querySelector('[data-evidence-viewer]');
if(viewer){
  const selected=new URLSearchParams(location.search).get('file');
  const safe=/^(?:\.\.\/)?data\/evidence\/[a-z0-9.-]+\.json$/.test(selected||'');
  const tabs=[...document.querySelectorAll('[data-evidence-tab]')];
  const heading=document.querySelector('[data-evidence-heading]');
  const raw=document.querySelector('[data-evidence-raw]');
  const image=document.querySelector('[data-evidence-image]');
  const render=(evidence,section)=>{
    const labels={result:zh?'结果与资源':'Result and resources',programmatic_failure:zh?'程序定位':'Programmatic finding',review:zh?'复核原因':'Reviewed cause',trajectory:zh?'执行轨迹与完整日志':'Trajectory and full logs',official_evaluation:zh?'官方验收':'Official evaluation'};
    heading.textContent=labels[section]||section;
    viewer.textContent=JSON.stringify(evidence[section]??null,null,2);
    tabs.forEach(tab=>tab.setAttribute('aria-selected',String(tab.dataset.evidenceTab===section)));
  };
  if(!safe){viewer.textContent=zh?'没有选中证据文件。':'No evidence file selected.'}
  else fetchJson(selected).then(evidence=>{
    raw.href=selected;
    if(evidence.screenshot){image.href=base+evidence.screenshot;image.textContent=zh?'查看最后页面原图':'Open last observed page';image.hidden=false}
    tabs.forEach(tab=>tab.addEventListener('click',()=>render(evidence,tab.dataset.evidenceTab)));
    render(evidence,'result');
  }).catch(error=>viewer.textContent=(zh?'证据读取失败：':'Evidence loading failed: ')+error.message);
}'''
    (output / "assets").mkdir(parents=True, exist_ok=True)
    (output / "assets/report.css").write_text(css, encoding="utf-8")
    (output / "assets/app.js").write_text(js, encoding="utf-8")


def build_webarena_replay_report(
    *,
    results_path: str | Path,
    dataset_path: str | Path,
    review_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    validated = load_validated_failure_review(results_path, review_path)
    results = validated["results"]
    reviews = validated["by_result"]
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    output = Path(output_dir).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    _assets(output)

    tasks = {int(item["task_id"]): item for item in dataset["trajectories"]}
    for task_id, task in tasks.items():
        _write_json(output / "data/trajectory" / f"{task_id}.json", {
            "task_id": task_id,
            "tool_calls": task.get("tool_calls") or [],
        })
    methods = {item["engine"]: item for item in profile["methods"]}
    attempts = results["attempts"]
    if {(item["engine"], int(item["task_id"])) for item in attempts} != {
        (engine, task_id) for engine in methods for task_id in tasks
    }:
        raise ValueError("report requires the complete participant-task matrix")

    action_by_engine: dict[str, list[float]] = defaultdict(list)
    action_by_name: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for item in results["physical_executions"]:
        key = (str(item["engine"]), int(item["task_id"]))
        trace = _load_artifact((item.get("evidence") or {}).get("trace")) or []
        for step in trace:
            value = step.get("duration_ms")
            if isinstance(value, (int, float)):
                action_by_engine[key[0]].append(float(value))
                action = str((step.get("arguments") or {}).get("action") or "unknown")
                action_by_name[action][key[0]].append(float(value))

    passing_tasks = {
        task_id
        for task_id in tasks
        if all(
            next(
                item for item in attempts if item["engine"] == engine and item["task_id"] == task_id
            )["status"]
            == "pass"
            for engine in methods
        )
    }
    method_rows = []
    for engine, method in methods.items():
        rows = [item for item in attempts if item["engine"] == engine]
        passes = sum(item["status"] == "pass" for item in rows)
        duration = _stat(
            [float(item["duration_ms"]) for item in rows if item["task_id"] in passing_tasks]
        )
        action_latency = _stat(action_by_engine[engine])
        comparable_rows = [item for item in rows if item["task_id"] in passing_tasks]
        resource_keys = {
            key
            for _, _, metric_specs in RESOURCE_GROUPS
            for _, _, key, _, _ in metric_specs
        }
        timing_keys = {
            key
            for _, _, metric_specs in TIMING_GROUPS
            for _, _, key, _, _ in metric_specs
        }
        resource_metrics = {
            key: _stat(
                [
                    float(value)
                    for item in comparable_rows
                    if isinstance(
                        value := (item.get("resource") or {}).get(key),
                        (int, float),
                    )
                ]
            )
            for key in resource_keys
        }
        timing_metrics = {
            key: _stat(
                [
                    float(value)
                    for item in comparable_rows
                    if isinstance(
                        value := (item.get("timings") or {}).get(key),
                        (int, float),
                    )
                ]
            )
            for key in timing_keys
        }
        cpu = resource_metrics["cpu_ms"]
        memory = resource_metrics["memory_mean_bytes"]
        method_rows.append(
            {
                "id": method["id"],
                "engine": engine,
                "label": method["label"],
                "passed": passes,
                "total": len(rows),
                "pass_rate": passes / len(rows),
                "duration": duration,
                "duration_mean": duration["mean"],
                "action_latency": action_latency,
                "action_mean": action_latency["mean"],
                "cpu": cpu,
                "cpu_mean": cpu["mean"],
                "memory": memory,
                "memory_mean": memory["mean"],
                "resources": resource_metrics,
                "timings": timing_metrics,
                "physical_executions": len(
                    [item for item in results["physical_executions"] if item["engine"] == engine]
                ),
            }
        )

    public_rows = []
    public_evidence: dict[tuple[str, int], dict[str, Any]] = {}
    for attempt in attempts:
        key = (attempt["engine"], int(attempt["task_id"]))
        task = tasks[key[1]]
        review = reviews.get(key)
        evidence = _public_evidence(attempt, review, output)
        public_evidence[key] = evidence
        failure = evidence["programmatic_failure"]
        cause = review["cause_group"] if review else {}
        row = {
            "key": f"{key[0]}-{key[1]}",
            "engine": key[0],
            "browser": methods[key[0]]["label"],
            "task_id": key[1],
            "intent": task["task"]["intent"],
            "sites": task["task"].get("sites") or [],
            "status": attempt["status"],
            "completed_actions": attempt["completed_action_count"],
            "action_count": attempt["action_count"],
            "execution_count": int(attempt.get("physical_execution_count") or 1),
            "failure_stage": failure["stage"],
            "program_error": failure["message"],
            "cause_zh": cause.get("label_zh", ""),
            "cause_en": cause.get("label_en", ""),
            "reason_zh": review.get("reason_zh", "") if review else "",
            "reason_en": review.get("reason_en", "") if review else "",
        }
        public_rows.append(row)
    rows_by_task = defaultdict(list)
    for row in public_rows:
        rows_by_task[int(row["task_id"])].append(row)
    for attempt in attempts:
        key = (str(attempt["engine"]), int(attempt["task_id"]))
        row = next(item for item in rows_by_task[key[1]] if item["engine"] == key[0])
        for lang in ("zh", "en"):
            target = output / ("result" if lang == "zh" else "en/result") / f"{row['key']}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _detail_page(lang, attempt, tasks[key[1]], methods[key[0]], public_evidence[key], rows_by_task[key[1]]), encoding="utf-8"
            )
    for task_id, task in tasks.items():
        for lang in ("zh", "en"):
            target = output / ("task" if lang == "zh" else "en/task") / f"{task_id}.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_task_page(lang, task, rows_by_task[task_id]), encoding="utf-8")

    _write_json(output / "data/results.json", public_rows)
    _write_json(output / "data/metrics.json", {"methods": method_rows, "passing_intersection": len(passing_tasks)})

    source_counts: dict[str, int] = defaultdict(int)
    for task in tasks.values():
        source_counts[str((task.get("source") or {}).get("kind") or "unknown")] += 1
    browser_use_source_count = source_counts["authoritative_successful_browser_use_execution"]
    repaired_source_count = source_counts["expert_repair_spec"]
    reference_engine = str((profile.get("qualification") or {}).get("reference_engine") or "")
    reference_method = methods.get(reference_engine)
    reference_row = next(
        (item for item in method_rows if item["engine"] == reference_engine),
        None,
    )
    if reference_method is None or reference_row is None:
        raise ValueError("profile qualification reference engine must be a registered participant")
    reference_label = _e(reference_method["label"])
    reviewed_failure_count = len(reviews)

    for lang in ("zh", "en"):
        zh = lang == "zh"
        root = output if zh else output / "en"
        root.mkdir(exist_ok=True)
        action_headers = ["动作" if zh else "Action", *[item["label"] for item in methods.values()]]
        action_rows = []
        for action in sorted(action_by_name):
            action_metrics = {
                engine: _stat(action_by_name[action][engine]) for engine in methods
            }
            action_winners = {
                stat: _unique_winners(
                    [
                        {"id": methods[engine]["id"], "value": metric[stat]}
                        for engine, metric in action_metrics.items()
                    ],
                    "value",
                    higher=False,
                )
                for stat in ("mean", "p50", "p95")
            }
            cells = []
            for engine in methods:
                metric = action_metrics[engine]
                text = _metric_lines(
                    metric,
                    (
                        lambda value, is_zh=zh: _format_ms(value)
                        if is_zh
                        else ("—" if value is None else f"{value / 1000:.1f} s")
                    ),
                    methods[engine]["id"],
                    action_winners,
                    lang=lang,
                )
                calls_label = "次调用" if zh else "calls"
                cells.append(f"<td>{text}<br><small>{metric['n']} {calls_label}</small></td>")
            action_rows.append(f'<tr><td><code>{_e(action)}</code></td>{"".join(cells)}</tr>')
        action_table = f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in action_headers)}</tr></thead><tbody>{"".join(action_rows)}</tbody></table></div>'
        task_count = len(tasks)
        shared_count = len(passing_tasks)
        eyebrow = (
            f"WebArena-Verified · {task_count} 个任务 · 模型调用 0 次"
            if zh
            else f"WebArena-Verified · {task_count} tasks · model calls 0"
        )
        stage_rows = []
        for engine, method in methods.items():
            for code, label_pair in (("browser_tool_execution", ("浏览器动作", "Browser action")), ("side_effect_not_observed", ("提交请求未出现", "Submission request absent")), ("official_evaluation", ("官方验收", "Official evaluation"))):
                count = sum(row["engine"] == engine and row["status"] != "pass" and row["failure_stage"] == code for row in public_rows)
                if count:
                    stage_rows.append(f'<tr><td>{_e(method["label"])}</td><td>{label_pair[0 if zh else 1]}</td><td>{count}</td><td><a href="results.html?engine={_e(engine)}&status=fail&stage={code}">{"下钻到失败结果" if zh else "Inspect failures"} →</a></td></tr>')
        stage_table = f'<div class="table-scroll stage-table"><table><thead><tr><th>{"浏览器" if zh else "Browser"}</th><th>{"失败环节" if zh else "Failure stage"}</th><th>{"结果数" if zh else "Outcomes"}</th><th>{"逐题证据" if zh else "Per-task evidence"}</th></tr></thead><tbody>{"".join(stage_rows)}</tbody></table></div>'
        resource_comparison = _resource_comparison(method_rows, lang)
        timing_comparison = _timing_comparison(method_rows, lang)
        summary_body = f'''<section class="hero"><p class="eyebrow">{eyebrow}</p><h1>{"浏览器轨迹重放评测" if zh else "Browser Trajectory Replay"}</h1><p>{"90 条经过资格验证的固定操作轨迹依次在全部注册浏览器上执行。Agent 与模型不参与；每题重置站点，浏览器方法之间完全销毁环境。" if zh else "The same 90 qualified fixed action trajectories ran serially on every registered browser. No Agent or model participated. Sites were reset per task and torn down between browser methods."}</p><div class="detail-links"><a href="results.html">{"逐题查看结果与报错" if zh else "Explore outcomes and exact errors"}</a><a href="dataset.html">{"查看任务与标准轨迹" if zh else "Task contracts and qualified trajectories"}</a></div></section>
        <section><h2>{"核心指标" if zh else "Core metrics"}</h2><p class="callout">{f"通过率覆盖全部 {task_count} 题；端到端耗时、CPU 和内存只比较所有浏览器共同通过的 {shared_count} 题，避免提前失败缩短工作量。单次动作耗时含实际工具调用，调用次数不判优。" if zh else f"Pass rate covers all {task_count} tasks. End-to-end time, CPU, and memory use the {shared_count} tasks passed by every browser so early failure cannot shorten workload. Per-action latency covers actual tool calls; call counts are not ranked."}</p>{_metrics_table(method_rows, lang)}</section>
        <section><h2>{"速度分解" if zh else "Timing breakdown"}</h2><p>{f"五个阶段均按共同通过的 {shared_count} 题统计。轨迹执行是浏览器实际完成固定动作的时间；环境准备、证据整理和验收单独列出，避免把外围工作混进浏览器执行速度。" if zh else f"All five phases use the {shared_count} shared passes. Trajectory execution is the time spent performing frozen browser actions; environment preparation, evidence finalization, and evaluation remain separate so browser execution is not mixed with surrounding work."}</p>{timing_comparison}</section>
        <section><h2>{"资源与网络" if zh else "Resources and network"}</h2><p>{f"以下统计同样只覆盖共同通过的 {shared_count} 题。绿色标记只用于含义明确的低耗用指标；CPU 使用率和网络速率不按低值判优。资源领先与任务通过率分开判断。" if zh else f"These statistics also cover only the {shared_count} shared passes. Green marks only metrics where lower consumption has a clear meaning; CPU utilization and network rates are not ranked by the lowest value. Resource leadership is separate from task pass rate."}</p>{resource_comparison}</section>
        <section><h2>{"失败发生在哪个环节" if zh else "Where failures occurred"}</h2><p>{"动作报错与提交请求未出现分别统计。后者表示工具调用返回成功，但验收要求的网络请求没有发生；不能据此改判通过。逐题详情给出触发动作、验收规则和复核原因。" if zh else "Action errors and absent submission requests are counted separately. In the latter case the tool call returned success, but the required network request did not occur; this does not justify changing the verdict to pass. Task detail shows the trigger action, evaluator rule, and reviewed cause."}</p>{stage_table}</section>
        <section><h2>{"浏览器动作耗时" if zh else "Browser action latency"}</h2><p>{"均值、P50、P95 按单次调用统计；调用次数只说明工作量。下钻到结果详情可查看具体动作耗时和工具返回。" if zh else "Mean, P50, and P95 are per-call statistics; call counts only describe workload. Open a task result for the individual action timing and full tool response."}</p><details class="matrix-disclosure"><summary>{"展开各类动作耗时" if zh else "Open latency by action type"}</summary>{action_table}</details></section>'''
        (root / "index.html").write_text(_shell(lang, "index", "浏览器轨迹重放" if zh else "Browser Trajectory Replay", summary_body, current_route="index.html"), encoding="utf-8")

        pass_leaders, pass_value = _metric_leader(
            method_rows, lambda item: item["pass_rate"], higher=True
        )
        trajectory_leaders, trajectory_value = _metric_leader(
            method_rows,
            lambda item: item["timings"]["trajectory_replay_ms"]["mean"],
            higher=False,
        )
        memory_leaders, memory_value = _metric_leader(
            method_rows,
            lambda item: item["resources"]["memory_peak_bytes"]["mean"],
            higher=False,
        )
        pass_summary = _leader_sentence(
            pass_leaders,
            f"{pass_leaders[0]['passed']}/{pass_leaders[0]['total']}（{pass_value * 100:.1f}%）" if zh else f"{pass_leaders[0]['passed']}/{pass_leaders[0]['total']} ({pass_value * 100:.1f}%)",
            lang=lang,
            metric_zh="通过率",
            metric_en="pass rate",
            rank_zh="最高",
            rank_en="highest",
        )
        trajectory_summary = (
            _leader_sentence(
                trajectory_leaders,
                _format_ms(trajectory_value) if zh else f"{trajectory_value / 1000:.1f} s",
                lang=lang,
                metric_zh="轨迹执行均值",
                metric_en="mean trajectory execution time",
                rank_zh="最低",
                rank_en="lowest",
            )
            if trajectory_value is not None
            else ("没有所有浏览器共同通过的任务，无法比较速度。" if zh else "No task passed on every browser, so timing cannot be compared.")
        )
        memory_summary = (
            _leader_sentence(
                memory_leaders,
                _format_mib(memory_value),
                lang=lang,
                metric_zh="平均峰值内存",
                metric_en="mean peak memory",
                rank_zh="最低",
                rank_en="lowest",
            )
            if memory_value is not None
            else ("没有所有浏览器共同通过的任务，无法比较资源占用。" if zh else "No task passed on every browser, so resource use cannot be compared.")
        )
        version_comparison = _version_comparison(public_rows, methods, lang)
        cause_counts: dict[tuple[str, str], int] = defaultdict(int)
        for row in public_rows:
            if row["status"] == "pass":
                continue
            label = (row["cause_zh"], row["cause_en"])
            cause_counts[label] += 1
        cause_rows = "".join(
            f'<tr><td>{_e(label_zh if zh else label_en)}</td><td>{count}</td></tr>'
            for (label_zh, label_en), count in sorted(
                cause_counts.items(), key=lambda item: (-item[1], item[0][0])
            )
        )
        cause_table = f'<div class="table-scroll compact-table"><table><thead><tr><th>{"复核分类" if zh else "Reviewed category"}</th><th>{"失败结果数" if zh else "Failed outcomes"}</th></tr></thead><tbody>{cause_rows}</tbody></table></div>'
        interpretation_body = f'''<section class="hero"><p class="eyebrow">{"报告解读" if zh else "Report interpretation"}</p><h1>{"固定轨迹下，差异出现在通过率、执行时间和资源占用" if zh else "Fixed trajectories expose differences in completion, timing, and resource use"}</h1><p>{f"本页解释 {task_count} 个任务、{len(method_rows)} 个浏览器方法的测量结果。通过率使用全部任务；速度与资源只比较 {shared_count} 个所有浏览器都完成的任务。" if zh else f"This page interprets measurements from {task_count} tasks and {len(method_rows)} browser methods. Pass rate uses the full task set; timing and resources use the {shared_count} tasks completed by every browser."}</p></section>
        <section><h2>{"先看任务是否完成" if zh else "Start with task completion"}</h2><p>{pass_summary} {"固定轨迹移除了 Agent 规划和模型输出差异，因此这里测到的是浏览器执行同一组动作后的结果，不代表自主 Browser Use 的综合能力。" if zh else "Frozen trajectories remove Agent planning and model output differences, so this measures browser execution of the same actions rather than overall autonomous Browser Use capability."}</p>{_metrics_table(method_rows, lang)}</section>
        {f'<section><h2>{"同一浏览器家族的版本变化" if zh else "Version changes within one browser family"}</h2>{version_comparison}</section>' if version_comparison else ''}
        <section><h2>{"失败停在哪一步" if zh else "Where failures stop"}</h2><p>{"程序定位回答失败发生在浏览器动作、动作完成后的副作用检查，还是官方验收；复核分类再说明可见证据支持的具体原因。点击结果分析中的“诊断”可直接查看报错、触发动作和处理状态。" if zh else "Programmatic location separates browser-action failures, missing side effects after a completed action, and official evaluation failures. Reviewed categories then state the specific cause supported by evidence. Open Diagnose in Results analysis for the error, trigger action, and treatment status."}</p>{stage_table}{cause_table}<div class="detail-links"><a href="results.html?status=fail">{"查看全部失败与逐步证据" if zh else "Inspect every failure and step evidence"}</a></div></section>
        <section><h2>{"共同完成任务上的速度" if zh else "Timing on tasks every browser completed"}</h2><p>{trajectory_summary} {"均值反映总体工作量，P50 描述典型任务，P95 显示慢任务尾部；三者都需一起看。" if zh else "The mean reflects total workload, P50 describes a typical task, and P95 shows the slow tail; all three matter."}</p>{timing_comparison}</section>
        <section><h2>{"资源结论与通过率分开" if zh else "Resource results are separate from pass rate"}</h2><p>{memory_summary} {"低内存或低网络流量不会抵消失败任务，也不能单独证明浏览器更快；它们只描述共同完成任务时的资源开销。" if zh else "Lower memory or network traffic does not offset failed tasks and does not by itself prove a faster browser; these metrics describe resource cost on shared passes."}</p>{resource_comparison}</section>
        <section><h2>{"这份结果可以回答什么" if zh else "What this result supports"}</h2><div class="reading-grid"><article><h3>{"可以回答" if zh else "Supported"}</h3><p>{"同一批资格轨迹在各浏览器上的通过差异、失败位置、逐步工具返回、共同完成任务上的速度与资源开销。" if zh else "Pass differences, failure location, per-step tool responses, and timing and resource cost on tasks completed by every browser."}</p></article><article><h3>{"不能回答" if zh else "Not established"}</h3><p>{"Agent 会不会选对操作、模型会不会理解任务，以及换一批任务后的总体能力。它们需要单独的自主 Browser Use 评测。" if zh else "Whether an Agent chooses the right action, whether a model understands the task, or capability on a different task population. Those require a separate autonomous Browser Use evaluation."}</p></article></div></section>'''
        (root / "interpretation.html").write_text(
            _shell(
                lang,
                "interpretation",
                "报告解读" if zh else "Report interpretation",
                interpretation_body,
                current_route="interpretation.html",
            ),
            encoding="utf-8",
        )

        headers = ["任务" if zh else "Task", "站点" if zh else "Site", "任务目标" if zh else "Task goal", "类型" if zh else "Type", "轨迹来源" if zh else "Trajectory source", "标准动作" if zh else "Qualified actions", "下钻" if zh else "Drill down"]
        matrix_rows = []
        for task_id, task in tasks.items():
            source = str((task.get("source") or {}).get("kind") or "")
            source_code = "autonomous" if source == "authoritative_successful_browser_use_execution" else "repaired"
            source_text = ("自主执行成功" if zh else "Autonomous success") if source_code == "autonomous" else ("修复后验证" if zh else "Repaired and qualified")
            task_type = str((task.get("evaluation_input") or {}).get("task_type") or "")
            type_text = ({"RETRIEVE": "信息提取", "NAVIGATE": "页面导航", "MUTATE": "页面操作"}.get(task_type, task_type) if zh else task_type) or "—"
            sites = task["task"].get("sites") or []
            intent = str(task["task"].get("intent") or "")
            action_count = len(task.get("tool_calls") or [])
            search = _e(f"{task_id} {', '.join(sites)} {intent} {task_type} {source_text}")
            matrix_rows.append(f'<tr data-task="{task_id}" data-sites="{_e(" ".join(sites))}" data-type="{_e(task_type)}" data-source="{source_code}" data-search="{search}"><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(sites))}</td><td><div class="intent-preview">{_e(intent)}</div></td><td>{_e(type_text)}</td><td>{source_text}</td><td>{action_count}</td><td><div class="dataset-actions"><a href="task/{task_id}.html">{"任务与轨迹" if zh else "Task & trajectory"}</a><a href="results.html?task={task_id}&status=">{"实测结果" if zh else "Measured results"}</a></div></td></tr>')
        dataset_sites = sorted({site for task in tasks.values() for site in (task["task"].get("sites") or [])})
        dataset_site_options = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in dataset_sites)
        dataset_type_options = "".join(f'<option value="{code}">{_e(label_zh if zh else label_en)}</option>' for code, label_zh, label_en in (("RETRIEVE", "信息提取", "Retrieve"), ("NAVIGATE", "页面导航", "Navigate"), ("MUTATE", "页面操作", "Mutate")))
        dataset_body = f'''<section class="hero"><p class="eyebrow">WebArena-Verified · {task_count}</p><h1>{"评测集" if zh else "Dataset"}</h1><p>{f"{task_count} 个任务，每题一条经过资格验证的固定轨迹。这里展示任务目标和验收类型；实测成败在结果分析中查看。" if zh else f"{task_count} tasks, each with one qualified fixed trajectory. This page contains goals and acceptance types; measured verdicts appear in Results analysis."}</p></section><section><div class="dataset-browser"><h2>{"任务与标准轨迹" if zh else "Tasks and qualified trajectories"}</h2><p>{f"{browser_use_source_count} 条来自成功的自主执行，{repaired_source_count} 条经修复后在 {reference_label} 验证。点开任务可查看验收输入、完整轨迹和各浏览器实测证据。" if zh else f"{browser_use_source_count} came from successful autonomous runs; {repaired_source_count} were repaired and qualified on {reference_label}. Open a task for its acceptance input, full trajectory, and evidence from every browser."}</p><div class="dataset-controls"><label>{"搜索任务" if zh else "Search tasks"}<input data-dataset-filter="q" type="search" placeholder="{'编号、站点或任务目标' if zh else 'ID, site, or goal'}"></label><label>{"站点" if zh else "Site"}<select data-dataset-filter="site"><option value="">{"全部" if zh else "All"}</option>{dataset_site_options}</select></label><label>{"任务类型" if zh else "Task type"}<select data-dataset-filter="type"><option value="">{"全部" if zh else "All"}</option>{dataset_type_options}</select></label><label>{"轨迹来源" if zh else "Trajectory source"}<select data-dataset-filter="source"><option value="">{"全部" if zh else "All"}</option><option value="autonomous">{"自主执行成功" if zh else "Autonomous success"}</option><option value="repaired">{"修复后验证" if zh else "Repaired and qualified"}</option></select></label></div><p class="dataset-count" data-dataset-count></p><div class="table-scroll dataset-table" data-dataset-table><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(matrix_rows)}</tbody></table></div><p class="dataset-empty" data-dataset-empty hidden>{"没有符合条件的任务，请调整筛选。" if zh else "No matching tasks. Change the filters."}</p></div></section>'''
        (root / "dataset.html").write_text(_shell(lang, "dataset", "评测集" if zh else "Dataset", dataset_body, current_route="dataset.html"), encoding="utf-8")

        result_rows = []
        for row in public_rows:
            cause = row["cause_zh"] if zh else row["cause_en"]
            status = ("通过" if row["status"] == "pass" else "失败") if zh else row["status"].title()
            stage_label = {"browser_tool_execution": "浏览器动作" if zh else "Browser action", "side_effect_not_observed": "提交请求未出现" if zh else "Submission request absent", "official_evaluation": "官方验收" if zh else "Official evaluation", "pass": "—"}.get(row["failure_stage"], row["failure_stage"])
            result_rows.append(f'<tr data-key="{row["key"]}" data-task="{row["task_id"]}" data-engine="{_e(row["engine"])}" data-status="{row["status"]}" data-site="{_e(row["sites"][0] if row["sites"] else "")}" data-stage="{_e(row["failure_stage"])}" data-cause="{_e((row["cause_zh"] or row["cause_en"]))}"><td><a href="task/{row["task_id"]}.html">#{row["task_id"]}</a><button type="button" data-open-diagnosis>{"诊断" if zh else "Diagnose"}</button></td><td><a href="result/{row["key"]}.html">{_e(row["browser"])}</a></td><td>{_e(row["sites"][0] if row["sites"] else "")}</td><td class="status-{row["status"]}">{status}</td><td>{row["completed_actions"]}/{row["action_count"]}</td><td>{row["execution_count"]}</td><td>{_e(stage_label)}</td><td>{_e(cause) if cause else "—"}</td><td><a href="result/{row["key"]}.html">{"详情页" if zh else "Details"}</a></td></tr>')
        browser_options = "".join(f'<option value="{_e(key)}">{_e(item["label"])}</option>' for key, item in methods.items())
        site_options = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in sorted({s for row in public_rows for s in row["sites"]}))
        stage_labels = {"browser_tool_execution": ("浏览器动作", "Browser action"), "side_effect_not_observed": ("提交请求未出现", "Submission request absent"), "official_evaluation": ("官方验收", "Official evaluation")}
        present_stages = {row["failure_stage"] for row in public_rows if row["status"] == "fail"}
        stage_options = "".join(f'<option value="{code}">{_e(stage_labels[code][0 if zh else 1])}</option>' for code in stage_labels if code in present_stages)
        causes = {row["cause_zh"]: row["cause_zh"] if zh else row["cause_en"] for row in public_rows if row["cause_zh"]}
        cause_options = "".join(f'<option value="{_e(code)}">{_e(label)}</option>' for code, label in sorted(causes.items()))
        result_matrix_rows = []
        for task_id, task in tasks.items():
            pass_links = []
            fail_links = []
            passed_engines = []
            failed_engines = []
            for engine in methods:
                row = next(item for item in rows_by_task[task_id] if item["engine"] == engine)
                label = ("通过" if row["status"] == "pass" else "失败") if zh else row["status"].title()
                link = f'<a class="outcome-pill {row["status"]}" href="result/{row["key"]}.html" data-engine="{_e(engine)}">{_e(row["browser"])} · {label}</a>'
                if row["status"] == "pass":
                    pass_links.append(link)
                    passed_engines.append(engine)
                else:
                    fail_links.append(link)
                    failed_engines.append(engine)
            site = (task["task"].get("sites") or [""])[0]
            result_matrix_rows.append(f'<tr data-site="{_e(site)}" data-pass="{_e(" ".join(passed_engines))}" data-fail="{_e(" ".join(failed_engines))}"><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(task["task"].get("sites") or []))}</td><td><div class="outcome-list">{"".join(pass_links) or "—"}</div></td><td><div class="outcome-list">{"".join(fail_links) or "—"}</div></td></tr>')
        matrix_sites = "".join(f'<option value="{_e(site)}">{_e(site)}</option>' for site in sorted({(task["task"].get("sites") or [""])[0] for task in tasks.values()}))
        matrix_baselines = "".join(f'<option value="{_e(engine)}">{_e(item["label"])}</option>' for engine, item in methods.items())
        matrix_checks = "".join(f'<label class="compare-chip"><input type="checkbox" data-matrix-method value="{_e(engine)}" checked>{_e(item["label"])}</label>' for engine, item in methods.items())
        result_matrix = f'<section class="method-comparison"><div class="comparison-heading"><div><h2>{"同题浏览器对比" if zh else "Same-task browser comparison"}</h2><p>{"按通过与失败分组展示所有参评浏览器；点击浏览器名称查看完整动作和验收证据。" if zh else "All registered browsers are grouped by pass and fail. Open a browser outcome for its actions and evaluator evidence."}</p></div><span class="comparison-count" data-matrix-count></span></div><div class="comparison-controls"><label>{"基准浏览器" if zh else "Baseline browser"}<select data-matrix-baseline>{matrix_baselines}</select></label><label>{"成败关系" if zh else "Outcome relation"}<select data-matrix-relation><option value="different">{"所选浏览器有分歧" if zh else "Selected browsers disagree"}</option><option value="baseline-pass-others-fail">{"基准通过，其余失败" if zh else "Baseline passes; others fail"}</option><option value="baseline-fail-others-pass">{"基准失败，其余通过" if zh else "Baseline fails; others pass"}</option><option value="all-pass">{"所选全部通过" if zh else "All selected pass"}</option><option value="all-fail">{"所选全部失败" if zh else "All selected fail"}</option><option value="exact-pass">{"仅所选浏览器通过" if zh else "Only selected browsers pass"}</option><option value="">{"全部任务" if zh else "All tasks"}</option></select></label><label>{"站点" if zh else "Site"}<select data-matrix-site><option value="">{"全部" if zh else "All"}</option>{matrix_sites}</select></label></div><fieldset class="comparison-methods"><legend>{"参与比较的浏览器（至少选一个）" if zh else "Comparison browsers (select at least one)"}</legend><div class="method-checks">{matrix_checks}</div></fieldset><div class="table-scroll matrix-table" data-task-matrix><table><thead><tr><th>{"任务" if zh else "Task"}</th><th>{"站点" if zh else "Site"}</th><th>{"通过的浏览器" if zh else "Passing browsers"}</th><th>{"失败的浏览器" if zh else "Failing browsers"}</th></tr></thead><tbody>{"".join(result_matrix_rows)}</tbody></table></div></section>'
        results_body = f'''<section class="hero"><p class="eyebrow">{len(public_rows)} {"条逻辑结果" if zh else "logical outcomes"}</p><h1>{"结果与失败诊断" if zh else "Results and failure diagnosis"}</h1><p>{"同题对比支持选择基准和多个浏览器；下方筛选表默认只显示失败。执行次数为 2 表示首次失败后完整重试一次，报告仍只保留一个最终逻辑结果。点击“诊断”查看原始判定、复核原因与执行证据。" if zh else "Choose a baseline and multiple browsers for same-task comparison. The inventory starts with failures. An execution count of 2 means one complete retry followed an initial failure; the report still keeps one final logical outcome. Open Diagnose for the exact finding, reviewed cause, and execution evidence."}</p></section><section class="compact-section">{result_matrix}</section><section><div class="filters"><label>{"搜索" if zh else "Search"}<input data-filter="q" placeholder="{"任务编号、站点或错误" if zh else "Task, site, or error"}"></label><label>{"浏览器" if zh else "Browser"}<select data-filter="engine"><option value="">{"全部" if zh else "All"}</option>{browser_options}</select></label><label>{"结果" if zh else "Verdict"}<select data-filter="status"><option value="fail">{"失败" if zh else "Fail"}</option><option value="pass">{"通过" if zh else "Pass"}</option><option value="">{"全部" if zh else "All"}</option></select></label><label>{"站点" if zh else "Site"}<select data-filter="site"><option value="">{"全部" if zh else "All"}</option>{site_options}</select></label><label>{"失败环节" if zh else "Failure stage"}<select data-filter="stage"><option value="">{"全部" if zh else "All"}</option>{stage_options}</select></label><label>{"复核分类" if zh else "Reviewed category"}<select data-filter="cause"><option value="">{"全部" if zh else "All"}</option>{cause_options}</select></label></div><p class="result-count" data-result-count></p><div class="table-scroll inventory" data-results><table><thead><tr><th>{"任务" if zh else "Task"}</th><th>{"浏览器" if zh else "Browser"}</th><th>{"站点" if zh else "Site"}</th><th>{"判定" if zh else "Verdict"}</th><th>{"动作" if zh else "Actions"}</th><th>{"执行次数" if zh else "Executions"}</th><th>{"失败环节" if zh else "Failure stage"}</th><th>{"复核分类" if zh else "Reviewed category"}</th><th>{"详情" if zh else "Detail"}</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><dialog id="diagnosis-dialog"><div class="dialog-head"><strong data-dialog-title></strong><button type="button" aria-label="{"关闭" if zh else "Close"}">×</button></div><div class="dialog-body"><div class="diagnosis-facts"><div><span>{"失败环节" if zh else "Failure stage"}</span><strong data-dialog-stage></strong></div><div><span>{"动作定位" if zh else "Action locator"}</span><strong data-dialog-step></strong></div><div><span>{"复核分类" if zh else "Reviewed category"}</span><strong data-dialog-category></strong></div></div><h3>{"程序判定与验收规则" if zh else "Programmatic finding and evaluator rule"}</h3><pre data-dialog-error></pre><h3>{"复核原因与处理" if zh else "Reviewed cause and treatment"}</h3><p data-dialog-cause></p><p data-dialog-remediation></p><div class="evidence-actions"><a data-dialog-step-link>{"打开对应动作" if zh else "Open the relevant action"}</a><a data-dialog-link>{"打开完整详情" if zh else "Open full detail"}</a></div></div></dialog>'''
        results_body = results_body.replace("结果与失败诊断", "结果分析").replace("Results and failure diagnosis", "Results analysis")
        (root / "results.html").write_text(_shell(lang, "results", "结果分析" if zh else "Results analysis", results_body, current_route="results.html"), encoding="utf-8")

        flow_labels = (
            ("重放器", "浏览器容器", "本地站点", "逐题重置", "证据采集", "官方验收", "完全销毁")
            if zh
            else ("Replay runner", "Browser container", "Local sites", "Per-task reset", "Evidence", "Official evaluator", "Full teardown")
        )
        fallback = '<div class="flow-fallback" data-topology-fallback>' + "".join(
            f'<div class="flow-node"><strong>{index}</strong>{_e(label)}</div>'
            + ('<div class="flow-arrow" aria-hidden="true">→</div>' if index < len(flow_labels) else "")
            for index, label in enumerate(flow_labels, 1)
        ) + "</div>"
        topology = f'''<pre class="mermaid" hidden>flowchart LR
  R[{"重放器" if zh else "Replay runner"}] -->|{"每次只运行一个浏览器" if zh else "one browser at a time"}| B[{"浏览器容器" if zh else "Browser container"}]
  B -->|{"Colima 网关 192.168.5.2" if zh else "Colima gateway 192.168.5.2"}| S[{"本地 WebArena 站点" if zh else "Local WebArena sites"}]
  S --> D[{"逐题恢复站点数据" if zh else "Reset site data per task"}]
  B --> E[{"轨迹 · 网络记录 · 资源采样" if zh else "Trace · HAR · resource samples"}]
  E --> V[{"官方验收" if zh else "Official evaluator"}]
  V --> X[{"绑定证据的结果" if zh else "Evidence-bound result"}]
  X --> T[{"销毁浏览器与站点" if zh else "Teardown browser and sites"}]
  T --> R</pre>'''
        environment_body = f'''<section class="hero"><h1>{"环境与评测流程" if zh else "Environment and evaluation flow"}</h1><p>{"192.168.5.2 是 Colima 虚拟机中的宿主网关：浏览器容器通过它访问运行在同一台 Mac 虚拟化环境里的 WebArena 站点。它不是公网测试网站。" if zh else "192.168.5.2 is the host gateway inside the Colima VM. Browser containers use it to reach the WebArena sites running in the same Mac virtualization environment; it is not a public test website."}</p></section><section><h2>{"执行拓扑" if zh else "Execution topology"}</h2>{fallback}{topology}</section><section><h2>{"隔离规则" if zh else "Isolation rules"}</h2><div class="summary-grid"><article><strong>1</strong><p>{"一套固定端口站点环境，因此只允许一个方法运行。" if zh else "One fixed-port site stack permits only one running method."}</p></article><article><strong>{task_count}</strong><p>{"一个浏览器完整跑完全部任务，每题前恢复官方夹具。" if zh else "A browser completes all tasks; official fixtures reset before each task."}</p></article><article><strong>0</strong><p>{"没有 Agent 决策和模型请求；输入是冻结的 BrowserTool 动作。" if zh else "No Agent decisions or model calls; inputs are frozen BrowserTool actions."}</p></article></div></section><script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';const node=document.querySelector('.mermaid');try{{mermaid.initialize({{startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral'}});const rendered=await mermaid.render('replay-topology',node.textContent);node.innerHTML=rendered.svg;node.hidden=false;document.querySelector('[data-topology-fallback]').hidden=true}}catch(error){{node.hidden=true}}</script>'''
        environment_body = environment_body.replace("环境与评测流程", "环境与方法").replace("Environment and evaluation flow", "Environment and method")
        (root / "environment.html").write_text(_shell(lang, "environment", "环境与方法" if zh else "Environment and method", environment_body, current_route="environment.html"), encoding="utf-8")

        review_rows = (
            (
                "轨迹资格核验" if zh else "Trajectory qualification",
                f"{task_count} 题；{browser_use_source_count} 条来自自主执行，{repaired_source_count} 条经修复。" if zh else f"{task_count} tasks: {browser_use_source_count} autonomous successes and {repaired_source_count} repaired trajectories.",
                f"{repaired_source_count} 条候选轨迹需要修复后才能作为固定输入。" if zh else f"{repaired_source_count} candidate trajectories required repair before use as frozen inputs.",
                f"逐题资格验证后，{reference_label} 通过 {reference_row['passed']}/{reference_row['total']} 题；固定轨迹不包含 Agent 或模型决策。" if zh else f"Per-task qualification yielded {reference_row['passed']}/{reference_row['total']} passes on {reference_label}. Frozen trajectories contain no Agent or model decisions.",
                "资格验证无未决项；实测浏览器差异不在此处改分。" if zh else "No qualification items remain unresolved. Measured browser differences are not rescored here.",
            ),
            (
                "失败证据复核" if zh else "Failure evidence review",
                f"{reviewed_failure_count} 条最终失败结果。" if zh else f"{reviewed_failure_count} final failed outcomes.",
                f"{sum(row['failure_stage'] == 'browser_tool_execution' for row in public_rows)} 条动作执行失败；{sum(row['failure_stage'] == 'side_effect_not_observed' for row in public_rows)} 条验收要求的提交请求未出现。" if zh else f"{sum(row['failure_stage'] == 'browser_tool_execution' for row in public_rows)} browser-action failures; {sum(row['failure_stage'] == 'side_effect_not_observed' for row in public_rows)} missing required submission requests.",
                f"{reviewed_failure_count}/{reviewed_failure_count} 条绑定当前证据哈希；分别展示程序报错、动作位置、复核原因和处理状态，原始分数未改。" if zh else f"{reviewed_failure_count}/{reviewed_failure_count} reviews are bound to the current evidence hash. Program errors, action locators, reviewed causes and treatment states are separate; official scores did not change.",
                "失败根因的后续修复和重测状态见逐题结果详情。" if zh else "Follow-up remediation and rerun status appear on each failed result page.",
            ),
        )
        review_table_rows = "".join(
            "<tr>" + "".join(f"<td>{_e(cell)}</td>" for cell in (title, scope, finding, correction, remaining)) + "</tr>"
            for title, scope, finding, correction, remaining in review_rows
        )
        review_headers = ("轮次", "范围", "发现", "纠正", "未完成") if zh else ("Round", "Scope", "Finding", "Correction", "Open")
        review_body = f'''<section class="hero"><h1>{"核验记录" if zh else "Review history"}</h1><p>{"只记录影响当前评测集和结果解读的核验轮次；每个失败的完整证据与处理状态在逐题结果页。" if zh else "Rounds that affected the current dataset or result interpretation. Full evidence and treatment status for each failure are on result pages."}</p></section><section><h2>{"当前状态" if zh else "Current status"}</h2><p>{f"标准轨迹 {task_count}/{task_count} 条已资格验证；最终失败 {reviewed_failure_count}/{reviewed_failure_count} 条已完成证据绑定复核，未复核 0 条。" if zh else f"{task_count}/{task_count} qualified trajectories and {reviewed_failure_count}/{reviewed_failure_count} evidence-bound failure reviews; zero unreviewed failures."}</p></section><section><h2>{"核验轮次" if zh else "Review rounds"}</h2><div class="table-scroll review-table"><table><thead><tr>{''.join(f'<th>{_e(label)}</th>' for label in review_headers)}</tr></thead><tbody>{review_table_rows}</tbody></table></div></section>'''
        (root / "review.html").write_text(_shell(lang, "review", "核验记录" if zh else "Review history", review_body, current_route="review.html"), encoding="utf-8")

        viewer_tabs = (("result", "结果与资源", "Result and resources"), ("programmatic_failure", "程序定位", "Programmatic finding"), ("review", "复核原因", "Reviewed cause"), ("trajectory", "动作与日志", "Actions and logs"), ("official_evaluation", "官方验收", "Official evaluation"))
        tab_buttons = "".join(f'<button type="button" data-evidence-tab="{key}" aria-selected="false">{label_zh if zh else label_en}</button>' for key, label_zh, label_en in viewer_tabs)
        viewer_body = f'<section class="hero"><h1>{"证据查看器" if zh else "Evidence viewer"}</h1><p>{"按结果、动作、程序判定和复核原因查看完整净化证据；原始 JSON 仍可下载。" if zh else "Inspect full sanitized evidence by outcome, action, programmatic finding, and review. The raw JSON remains downloadable."}</p></section><section class="viewer"><div class="viewer-tabs" role="tablist">{tab_buttons}</div><h2 data-evidence-heading></h2><div class="evidence-actions"><a data-evidence-raw href="#">{"下载净化 JSON" if zh else "Download sanitized JSON"}</a><a data-evidence-image hidden></a></div><pre data-evidence-viewer></pre></section>'
        (root / "evidence-viewer.html").write_text(_shell(lang, "results", "证据查看器" if zh else "Evidence viewer", viewer_body, current_route="evidence-viewer.html"), encoding="utf-8")

    manifest = {
        "schema": "lex_webarena_trajectory_replay_report/1",
        "results_sha256": _sha256(Path(results_path)),
        "dataset_sha256": _sha256(Path(dataset_path)),
        "review_sha256": _sha256(Path(review_path)),
        "task_count": len(tasks),
        "participant_count": len(methods),
        "participants": list(methods),
        "logical_results": len(public_rows),
        "failed_results": sum(row["status"] != "pass" for row in public_rows),
        "reviewed_failures": len(reviews),
        "routes": 14 + 2 * len(public_rows) + 2 * len(tasks),
    }
    _write_json(output / "report-manifest.json", manifest)
    return manifest
