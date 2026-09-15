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
        "zh": (("index", "结论"), ("dataset", "评测集"), ("results", "结果与诊断"), ("environment", "环境与方法"), ("review", "核验记录")),
        "en": (("index", "Summary"), ("dataset", "Dataset"), ("results", "Results & diagnosis"), ("environment", "Environment & method"), ("review", "Review history")),
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
const slug=(parts[at+1]||'').split('~')[0];
if(!/^[A-Za-z0-9_-]+$/.test(slug))return;
const route=parts.slice(at+2).join('/');
const node=document.querySelector('base[href]')||document.createElement('base');
node.href=location.origin+'/api/preview/'+slug+'/'+route;
if(!node.isConnected)document.head.prepend(node);
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
        ("浏览器", "通过", "通过率", "成功交集端到端耗时", "单次动作耗时", "CPU 时间", "平均内存")
        if lang == "zh"
        else ("Browser", "Passed", "Pass rate", "End-to-end time on shared passes", "Per-action latency", "CPU time", "Mean memory")
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


def _resource_table(methods: list[dict[str, Any]], lang: str) -> str:
    zh = lang == "zh"
    metrics = (
        ("memory_peak", "峰值内存", "Peak memory", _format_mib),
        ("network_rx", "接收数据", "Network received", _format_mib),
        ("network_tx", "发送数据", "Network sent", _format_mib),
        ("cpu_utilization", "CPU 平均使用率", "Mean CPU utilization", lambda value: "—" if value is None else f"{value:.1f}%"),
    )
    rows = []
    for key, label_zh, label_en, formatter in metrics:
        cells = [_e(label_zh if zh else label_en)]
        for method in methods:
            cells.append(_metric_lines(method[key], formatter, method["id"], {stat: set() for stat in ("mean", "p50", "p95")}, lang=lang))
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    headers = ("资源指标" if zh else "Resource metric", *(method["label"] for method in methods))
    return f'<div class="table-scroll"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


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
    steps = []
    for step in evidence["trajectory"]:
        arguments = step.get("arguments") or {}
        action = arguments.get("action") or ""
        target = arguments.get("selector") or arguments.get("url") or arguments.get("key") or ""
        number = step.get("index")
        failed_step = failed_index == number
        result = str(step.get("result") or "")
        first_line = result.splitlines()[0] if result else ""
        steps.append(
            f'<article id="step-{number}" class="trace-step {"failed-step" if failed_step else ""}">'
            f'<div class="step-heading"><span class="step-number">{number}</span><strong>{_e(action)}</strong>'
            f'<span class="step-status {"status-fail" if step.get("status") == "error" else "status-pass"}">{_e(step.get("status"))}</span>'
            f'<span>{_e(round(float(step.get("duration_ms") or 0) / 1000, 2))} s</span></div>'
            f'<p class="step-target">{_e(target)}</p><p class="step-preview">{_e(first_line[:240])}</p>'
            f'<details {"open" if failed_step else ""}><summary>{"完整动作参数与返回" if zh else "Full action arguments and response"}</summary>'
            f'<div class="step-data"><div><h4>{"动作参数" if zh else "Arguments"}</h4><pre>{_e(json.dumps(_clean(arguments), ensure_ascii=False, indent=2))}</pre></div>'
            f'<div><h4>{"完整返回与报错" if zh else "Full response and error"}</h4><pre>{_e(_clean(result))}</pre></div></div></details></article>'
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
    <section id="trajectory"><h2>{"本浏览器的动作执行记录" if zh else "Executed actions in this browser"}</h2><p>{"逐项展开可查看完整参数、页面快照、工具返回和原始报错；红色标注的是实际失败动作。" if zh else "Expand an action for its full arguments, page snapshot, tool response, and exact error. Red marks the failed action."}</p><div class="trace-list">{''.join(steps)}</div></section>
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
    <section><h2>{"同题所有浏览器结果" if zh else "All browser outcomes on this task"}</h2><div class="table-scroll"><table class="task-peer-table"><thead><tr><th>{"浏览器" if zh else "Browser"}</th><th>{"结果" if zh else "Verdict"}</th><th>{"完成动作" if zh else "Completed actions"}</th><th>{"证据" if zh else "Evidence"}</th></tr></thead><tbody>{peer_rows}</tbody></table></div></section>
    <section><h2>{"冻结的操作轨迹" if zh else "Frozen action trajectory"}</h2><p>{"这是所有浏览器共用的输入；下方记录的是预定动作，不是任一浏览器的实测执行结果。" if zh else "The same input is used for every browser. These are planned actions, not any browser's measured execution."}</p><div class="table-scroll"><table class="planned-table"><thead><tr><th>#</th><th>{"动作" if zh else "Action"}</th><th>{"目标" if zh else "Target"}</th><th>{"参数" if zh else "Arguments"}</th></tr></thead><tbody>{''.join(planned_rows)}</tbody></table></div></section>
    <section><h2>{"验收输入" if zh else "Evaluation input"}</h2><p>{"供官方 evaluator 判分；不是 Agent 提示词，也不是唯一正确路径。" if zh else "Used by the official evaluator, not an Agent prompt or the only correct path."}</p><details><summary>{"查看答案与验收字段" if zh else "Inspect answer and acceptance fields"}</summary><pre>{_e(json.dumps(_clean(contract), ensure_ascii=False, indent=2))}</pre></details></section>'''
    title = f"任务 #{task_id} · 评测集" if zh else f"Task #{task_id} · Dataset"
    return _shell(lang, "dataset", title, body, depth=1, current_route=f"task/{task_id}.html")


def _assets(output: Path) -> None:
    css = r''':root{--bg:#f4f7fb;--panel:#fff;--ink:#102235;--muted:#60748a;--line:#d4e0ec;--accent:#0969da;--good:#087f4f;--bad:#c52c45;--wash:#eaf3ff}html[data-theme=dark]{--bg:#0c1520;--panel:#121f2d;--ink:#eef5fb;--muted:#9db0c2;--line:#2b4054;--accent:#62a8ff;--good:#4ed29b;--bad:#ff7d91;--wash:#142d49}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}.top{height:70px;display:flex;align-items:center;gap:28px;padding:0 max(24px,calc((100% - 1440px)/2));background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}.brand{font-weight:900;color:var(--ink);text-decoration:none;white-space:nowrap}.top nav{display:flex;gap:22px;overflow:auto;flex:1}.top nav a,.language{color:var(--accent);text-decoration:none;white-space:nowrap}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;border-bottom:3px solid var(--ink)}.theme{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:50%;width:38px;height:38px}main,footer{width:min(1440px,calc(100% - 48px));margin:auto}footer{padding:36px 0;color:var(--muted);border-top:1px solid var(--line)}section{padding:34px 0}.hero{padding:46px 0 28px}.hero h1,.detail-hero h1{font-size:clamp(36px,5vw,68px);line-height:1.08;margin:8px 0 14px;letter-spacing:-.04em}.hero p,.detail-hero p{max-width:880px;color:var(--muted);font-size:18px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;color:var(--accent)!important;font-size:13px!important;font-weight:800}h2{font-size:30px;margin:0 0 16px}.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel)}table{border-collapse:collapse;width:100%;min-width:900px}th,td{text-align:left;vertical-align:top;padding:14px 16px;border-bottom:1px solid var(--line)}th{background:color-mix(in srgb,var(--wash) 70%,var(--panel));font-size:14px;position:sticky;top:0}td{overflow-wrap:anywhere}.metrics td{min-width:150px}.metric-label{display:inline-block;width:42px;color:var(--muted);font-size:13px}.winner{color:var(--good)}.winner>span{display:inline-grid;place-items:center;margin-left:7px;min-width:24px;height:24px;padding:0 6px;border-radius:12px;background:var(--good);color:white;font-size:12px}.callout{padding:16px 18px;border-left:4px solid var(--accent);background:var(--panel);border-radius:0 12px 12px 0}.summary-grid,.diagnosis-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.summary-grid article,.diagnosis-grid article{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;min-height:150px}.summary-grid strong,.diagnosis-grid strong{display:block;font-size:24px;margin:8px 0}.summary-grid span,.diagnosis-grid span{color:var(--muted);font-size:13px}.verdict.fail{border-left:5px solid var(--bad)}.verdict.pass{border-left:5px solid var(--good)}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.filters input,.filters select{padding:10px 12px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;min-width:170px}.inventory{height:620px;overflow:auto}.inventory table{min-width:1500px}.inventory tbody tr{cursor:pointer}.inventory tbody tr:hover{background:var(--wash)}.status-pass{color:var(--good);font-weight:800}.status-fail{color:var(--bad);font-weight:800}.failed-step{background:color-mix(in srgb,var(--bad) 10%,var(--panel))}.failed-step td:first-child{border-left:4px solid var(--bad)}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#071923;color:#dff4ff;padding:14px;border-radius:10px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}figure{margin:22px 0}figure img{display:block;max-width:100%;border:1px solid var(--line);border-radius:12px}figcaption{color:var(--muted);font-size:13px}.evidence-actions{display:flex;gap:12px;flex-wrap:wrap}.evidence-actions a{padding:10px 14px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--accent);text-decoration:none}.detail-hero{padding-top:40px}.viewer{height:75vh}.viewer pre{height:100%;max-height:none}dialog{width:min(850px,calc(100% - 32px));border:1px solid var(--line);border-radius:16px;background:var(--panel);color:var(--ink);padding:0}dialog::backdrop{background:#07111bbf}.dialog-head{display:flex;justify-content:space-between;padding:18px;border-bottom:1px solid var(--line)}.dialog-body{padding:18px}.dialog-head button{border:0;background:none;color:var(--ink);font-size:24px}.mermaid{width:100%;max-width:100%;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px;overflow:auto}.flow-fallback{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:14px}.flow-node{min-width:150px;padding:14px;border-radius:10px;background:var(--wash)}.flow-node strong{display:block;color:var(--accent);margin-bottom:6px}.flow-arrow{display:grid;place-items:center;color:var(--muted);font-size:24px}@media(max-width:720px){.top{padding:0 16px;gap:14px}.top nav{gap:14px}main,footer{width:calc(100% - 28px)}section{padding:26px 0}.hero h1,.detail-hero h1{font-size:36px}.inventory{height:560px}.language{display:none}}'''
    css += r'''
html{scroll-padding-top:88px}a{color:var(--accent)}button,summary{cursor:pointer}
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
@media(max-width:760px){.top{padding:0 14px}.comparison-heading{display:block}.comparison-count{display:inline-block;margin-top:10px}.comparison-controls{grid-template-columns:1fr}.method-comparison{padding:16px}main,footer{width:calc(100% - 28px)}}
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
    let shown=0;
    rows.forEach(row=>{
      const result=byKey[row.dataset.key];
      const haystack=(row.textContent+' '+(result?.intent||'')+' '+(result?.program_error||'')+' '+(result?.reason_zh||'')+' '+(result?.reason_en||'')).toLowerCase();
      const matches=controls.every(control=>!control.value||control.dataset.filter==='q'||row.dataset[control.dataset.filter]===control.value)&&(!query||haystack.includes(query));
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
        cpu = _stat(
            [float((item.get("resource") or {}).get("cpu_ms") or 0) for item in comparable_rows]
        )
        memory = _stat(
            [
                float((item.get("resource") or {}).get("memory_mean_bytes") or 0)
                for item in comparable_rows
            ]
        )
        memory_peak = _stat([float((item.get("resource") or {}).get("memory_peak_bytes") or 0) for item in comparable_rows])
        network_rx = _stat([float((item.get("resource") or {}).get("network_rx_bytes") or 0) for item in comparable_rows])
        network_tx = _stat([float((item.get("resource") or {}).get("network_tx_bytes") or 0) for item in comparable_rows])
        cpu_utilization = _stat([float((item.get("resource") or {}).get("cpu_average_utilization_pct") or 0) for item in comparable_rows])
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
                "memory_peak": memory_peak,
                "network_rx": network_rx,
                "network_tx": network_tx,
                "cpu_utilization": cpu_utilization,
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
        summary_body = f'''<section class="hero"><p class="eyebrow">{eyebrow}</p><h1>{"浏览器轨迹重放评测" if zh else "Browser Trajectory Replay"}</h1><p>{"90 条经过资格验证的固定操作轨迹依次在全部注册浏览器上执行。Agent 与模型不参与；每题重置站点，浏览器方法之间完全销毁环境。" if zh else "The same 90 qualified fixed action trajectories ran serially on every registered browser. No Agent or model participated. Sites were reset per task and torn down between browser methods."}</p><div class="detail-links"><a href="results.html">{"逐题查看结果与报错" if zh else "Explore outcomes and exact errors"}</a><a href="dataset.html">{"查看任务与标准轨迹" if zh else "Task contracts and qualified trajectories"}</a></div></section>
        <section><h2>{"核心指标" if zh else "Core metrics"}</h2><p class="callout">{f"通过率覆盖全部 {task_count} 题；端到端耗时、CPU 和内存只比较所有浏览器共同通过的 {shared_count} 题，避免提前失败缩短工作量。单次动作耗时含实际工具调用，调用次数不判优。" if zh else f"Pass rate covers all {task_count} tasks. End-to-end time, CPU, and memory use the {shared_count} tasks passed by every browser so early failure cannot shorten workload. Per-action latency covers actual tool calls; call counts are not ranked."}</p>{_metrics_table(method_rows, lang)}</section>
        <section><h2>{"资源与网络" if zh else "Resources and network"}</h2><p>{f"下表也只统计共同通过的 {shared_count} 题；每个数值按单题采样。网络传输量和 CPU 使用率不直接判优，低数值可能来自缓存或等待，而非浏览器更快。" if zh else f"These per-task samples also cover the {shared_count} shared passes. Network bytes and CPU utilization are not ranked: lower values can reflect caching or waiting, not a faster browser."}</p><details class="matrix-disclosure"><summary>{"展开资源与网络统计" if zh else "Open resource and network statistics"}</summary>{_resource_table(method_rows, lang)}</details></section>
        <section><h2>{"失败发生在哪个环节" if zh else "Where failures occurred"}</h2><p>{"动作报错与提交请求未出现分别统计。后者表示工具调用返回成功，但验收要求的网络请求没有发生；不能据此改判通过。逐题详情给出触发动作、验收规则和复核原因。" if zh else "Action errors and absent submission requests are counted separately. In the latter case the tool call returned success, but the required network request did not occur; this does not justify changing the verdict to pass. Task detail shows the trigger action, evaluator rule, and reviewed cause."}</p>{stage_table}</section>
        <section><h2>{"浏览器动作耗时" if zh else "Browser action latency"}</h2><p>{"均值、P50、P95 按单次调用统计；调用次数只说明工作量。下钻到结果详情可查看具体动作耗时和工具返回。" if zh else "Mean, P50, and P95 are per-call statistics; call counts only describe workload. Open a task result for the individual action timing and full tool response."}</p><details class="matrix-disclosure"><summary>{"展开各类动作耗时" if zh else "Open latency by action type"}</summary>{action_table}</details></section>'''
        (root / "index.html").write_text(_shell(lang, "index", "浏览器轨迹重放" if zh else "Browser Trajectory Replay", summary_body, current_route="index.html"), encoding="utf-8")

        headers = ["任务" if zh else "Task", "站点" if zh else "Site", "来源" if zh else "Source", "标准动作" if zh else "Qualified actions"]
        matrix_rows = []
        for task_id, task in tasks.items():
            source = str((task.get("source") or {}).get("kind") or "")
            source_text = ("自主执行成功" if zh else "Autonomous success") if source == "authoritative_successful_browser_use_execution" else ("修复后验证" if zh else "Repaired and qualified")
            matrix_rows.append(f'<tr><td><a href="task/{task_id}.html">#{task_id}</a></td><td>{_e(", ".join(task["task"].get("sites") or []))}</td><td>{source_text}</td><td>{len(task.get("tool_calls") or [])}</td></tr>')
        dataset_body = f'<section class="hero"><p class="eyebrow">WebArena-Verified · {task_count}</p><h1>{"评测集与标准轨迹" if zh else "Dataset and qualified trajectories"}</h1><p>{f"每题一条固定操作轨迹。{browser_use_source_count} 条来自成功的自主浏览器执行，{repaired_source_count} 条经修复并在 {reference_label} 重新验证。点击任务查看目标、验收输入和全部预定动作。" if zh else f"One qualified path per task: {browser_use_source_count} from successful autonomous runs and {repaired_source_count} repaired and re-qualified on {reference_label}. Open a task for its goal, evaluation input, and full planned actions."}</p></section><section><h2>{"90 题任务定义" if zh else "Task definitions"}</h2><div class="table-scroll dataset-table"><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{"".join(matrix_rows)}</tbody></table></div><p><a href="results.html">{"查看同题实测结果 →" if zh else "Compare measured outcomes →"}</a></p></section>'
        (root / "dataset.html").write_text(_shell(lang, "dataset", "评测集" if zh else "Dataset", dataset_body, current_route="dataset.html"), encoding="utf-8")

        result_rows = []
        for row in public_rows:
            cause = row["cause_zh"] if zh else row["cause_en"]
            status = ("通过" if row["status"] == "pass" else "失败") if zh else row["status"].title()
            stage_label = {"browser_tool_execution": "浏览器动作" if zh else "Browser action", "side_effect_not_observed": "提交请求未出现" if zh else "Submission request absent", "official_evaluation": "官方验收" if zh else "Official evaluation", "pass": "—"}.get(row["failure_stage"], row["failure_stage"])
            result_rows.append(f'<tr data-key="{row["key"]}" data-engine="{_e(row["engine"])}" data-status="{row["status"]}" data-site="{_e(row["sites"][0] if row["sites"] else "")}" data-stage="{_e(row["failure_stage"])}" data-cause="{_e((row["cause_zh"] or row["cause_en"]))}"><td><a href="task/{row["task_id"]}.html">#{row["task_id"]}</a><button type="button" data-open-diagnosis>{"诊断" if zh else "Diagnose"}</button></td><td><a href="result/{row["key"]}.html">{_e(row["browser"])}</a></td><td>{_e(row["sites"][0] if row["sites"] else "")}</td><td class="status-{row["status"]}">{status}</td><td>{row["completed_actions"]}/{row["action_count"]}</td><td>{_e(stage_label)}</td><td>{_e(cause) if cause else "—"}</td><td><a href="result/{row["key"]}.html">{"证据页" if zh else "Evidence page"}</a></td></tr>')
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
        results_body = f'''<section class="hero"><p class="eyebrow">{len(public_rows)} {"条逻辑结果" if zh else "logical outcomes"}</p><h1>{"结果与失败诊断" if zh else "Results and failure diagnosis"}</h1><p>{"同题对比支持选择基准和多个浏览器；下方筛选表默认只显示失败，点击“诊断”查看原始判定、复核原因与执行证据。" if zh else "Choose a baseline and multiple browsers for same-task comparison. The inventory starts with failures; open Diagnose for the exact finding, reviewed cause, and execution evidence."}</p></section><section class="compact-section">{result_matrix}</section><section><div class="filters"><label>{"搜索" if zh else "Search"}<input data-filter="q" placeholder="{"任务编号、站点或错误" if zh else "Task, site, or error"}"></label><label>{"浏览器" if zh else "Browser"}<select data-filter="engine"><option value="">{"全部" if zh else "All"}</option>{browser_options}</select></label><label>{"结果" if zh else "Verdict"}<select data-filter="status"><option value="fail">{"失败" if zh else "Fail"}</option><option value="pass">{"通过" if zh else "Pass"}</option><option value="">{"全部" if zh else "All"}</option></select></label><label>{"站点" if zh else "Site"}<select data-filter="site"><option value="">{"全部" if zh else "All"}</option>{site_options}</select></label><label>{"失败环节" if zh else "Failure stage"}<select data-filter="stage"><option value="">{"全部" if zh else "All"}</option>{stage_options}</select></label><label>{"复核分类" if zh else "Reviewed category"}<select data-filter="cause"><option value="">{"全部" if zh else "All"}</option>{cause_options}</select></label></div><p class="result-count" data-result-count></p><div class="table-scroll inventory" data-results><table><thead><tr><th>{"任务" if zh else "Task"}</th><th>{"浏览器" if zh else "Browser"}</th><th>{"站点" if zh else "Site"}</th><th>{"判定" if zh else "Verdict"}</th><th>{"动作" if zh else "Actions"}</th><th>{"失败环节" if zh else "Failure stage"}</th><th>{"复核分类" if zh else "Reviewed category"}</th><th>{"详情" if zh else "Detail"}</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section><dialog id="diagnosis-dialog"><div class="dialog-head"><strong data-dialog-title></strong><button type="button" aria-label="{"关闭" if zh else "Close"}">×</button></div><div class="dialog-body"><div class="diagnosis-facts"><div><span>{"失败环节" if zh else "Failure stage"}</span><strong data-dialog-stage></strong></div><div><span>{"动作定位" if zh else "Action locator"}</span><strong data-dialog-step></strong></div><div><span>{"复核分类" if zh else "Reviewed category"}</span><strong data-dialog-category></strong></div></div><h3>{"程序判定与验收规则" if zh else "Programmatic finding and evaluator rule"}</h3><pre data-dialog-error></pre><h3>{"复核原因与处理" if zh else "Reviewed cause and treatment"}</h3><p data-dialog-cause></p><p data-dialog-remediation></p><div class="evidence-actions"><a data-dialog-step-link>{"打开对应动作" if zh else "Open the relevant action"}</a><a data-dialog-link>{"打开完整详情" if zh else "Open full detail"}</a></div></div></dialog>'''
        (root / "results.html").write_text(_shell(lang, "results", "结果与诊断" if zh else "Results", results_body, current_route="results.html"), encoding="utf-8")

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
        topology = '''<pre class="mermaid" hidden>flowchart LR
  R[Replay runner] -->|one method at a time| B[Browser container]
  B -->|gateway 192.168.5.2| S[Self-hosted WebArena sites]
  S --> D[(Per-task reset data)]
  B --> E[Trace · HAR · resource samples]
  E --> V[Official evaluator]
  V --> X[Evidence-bound result]
  X --> T[Teardown browser and sites]
  T --> R</pre>'''
        environment_body = f'''<section class="hero"><h1>{"环境与评测流程" if zh else "Environment and evaluation flow"}</h1><p>{"192.168.5.2 是 Colima 虚拟机中的宿主网关：浏览器容器通过它访问运行在同一台 Mac 虚拟化环境里的 WebArena 站点。它不是公网测试网站。" if zh else "192.168.5.2 is the host gateway inside the Colima VM. Browser containers use it to reach the WebArena sites running in the same Mac virtualization environment; it is not a public test website."}</p></section><section><h2>{"执行拓扑" if zh else "Execution topology"}</h2>{fallback}{topology}</section><section><h2>{"隔离规则" if zh else "Isolation rules"}</h2><div class="summary-grid"><article><strong>1</strong><p>{"一套固定端口站点环境，因此只允许一个方法运行。" if zh else "One fixed-port site stack permits only one running method."}</p></article><article><strong>{task_count}</strong><p>{"一个浏览器完整跑完全部任务，每题前恢复官方夹具。" if zh else "A browser completes all tasks; official fixtures reset before each task."}</p></article><article><strong>0</strong><p>{"没有 Agent 决策和模型请求；输入是冻结的 BrowserTool 动作。" if zh else "No Agent decisions or model calls; inputs are frozen BrowserTool actions."}</p></article></div></section><script type="module">import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';const node=document.querySelector('.mermaid');try{{mermaid.initialize({{startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral'}});const rendered=await mermaid.render('replay-topology',node.textContent);node.innerHTML=rendered.svg;node.hidden=false;document.querySelector('[data-topology-fallback]').hidden=true}}catch(error){{node.hidden=true}}</script>'''
        (root / "environment.html").write_text(_shell(lang, "environment", "环境与方法" if zh else "Environment", environment_body, current_route="environment.html"), encoding="utf-8")

        review_body = f'''<section class="hero"><h1>{"核验记录" if zh else "Review history"}</h1><p>{"记录影响当前数据集与结论的核验轮次；逐题证据位于结果详情页。" if zh else "Review rounds that changed the current dataset or conclusions. Per-result evidence remains on result pages."}</p></section><section><div class="summary-grid"><article><span>2026-09-15</span><strong>{f"{task_count} 条轨迹资格核验" if zh else f"{task_count}-trajectory qualification"}</strong><p>{f"修复已确认的非浏览器问题，重新验证后 {reference_label} {reference_row['passed']}/{reference_row['total']}。" if zh else f"Confirmed non-browser defects were repaired; {reference_label} then qualified {reference_row['passed']}/{reference_row['total']}."}</p></article><article><span>2026-09-15</span><strong>{f"{reviewed_failure_count} 条失败根因复核" if zh else f"{reviewed_failure_count}-failure root-cause review"}</strong><p>{f"自动失败位置与复核根因分离；{reviewed_failure_count}/{reviewed_failure_count} 个失败结果绑定当前证据哈希，没有未复核的失败。后续修复状态见结果详情。" if zh else f"Programmatic locations remain separate from reviewed causes; {reviewed_failure_count}/{reviewed_failure_count} failures are bound to the current evidence hash. No failures remain unreviewed; remediation states are shown on result pages."}</p></article></div></section>'''
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
        "routes": 12 + 2 * len(public_rows) + 2 * len(tasks),
    }
    _write_json(output / "report-manifest.json", manifest)
    return manifest
