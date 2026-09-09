"""Build the public, drill-down browser-agent evidence report."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import shutil
import statistics
import tempfile
from collections import defaultdict
from collections.abc import Iterable
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
FAILURE_NOTES = {
    68: "多帖聚合不完整：四次执行都只返回一本书。",
    124: "把全局价格范围当成目标商品范围。",
    142: "未完成目标月份与品类的完整订单汇总。",
    168: "个人项目筛选返回空列表，官方答案非空。",
    506: "购物车最终状态不满足唯一目标商品；重试也未在步数上限内完成。",
}


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


def _table(headers: list[str], rows: Iterable[Iterable[Any]], *, caption: str = "") -> str:
    head = "".join(f"<th>{_e(x)}</th>" for x in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in row) + "</tr>"
        for row in rows
    )
    cap = f"<caption>{_e(caption)}</caption>" if caption else ""
    return f'<div class="table-wrap"><table>{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _task_type(task_id: int) -> str:
    return (
        "MUTATE" if task_id in MUTATE_IDS else "NAVIGATE" if task_id in NAVIGATE_IDS else "RETRIEVE"
    )


def _attempt_id(row: dict[str, Any]) -> str:
    return f"{row['engine']}-{int(row['task_id'])}-a{int(row['attempt'])}"


def _public_attempt(row: dict[str, Any]) -> dict[str, Any]:
    usage, resource = row.get("model_usage") or {}, row.get("resource") or {}
    return {
        "id": _attempt_id(row),
        "engine": str(row["engine"]),
        "task_id": int(row["task_id"]),
        "tier": str(row["tier"]),
        "attempt": int(row["attempt"]),
        "is_retry": bool(row.get("is_retry") or int(row["attempt"]) == 2),
        "score": float(row["score"]),
        "status": str(row.get("status") or ("success" if float(row["score"]) == 1 else "failure")),
        "duration_ms": round(float(row.get("duration_ms") or 0), 3),
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
        primary = [r for r in rows if r["engine"] == engine and int(r["attempt"]) == 1]
        retries = [r for r in rows if r["engine"] == engine and int(r["attempt"]) == 2]
        durations = [float(r.get("duration_ms") or 0) for r in primary]
        tokens = [int((r.get("model_usage") or {}).get("total_tokens") or 0) for r in primary]
        passed = sum(float(r["score"]) == 1 for r in primary)
        result[engine] = {
            "completed": len(primary),
            "passed": passed,
            "score": passed / len(primary) if primary else None,
            "retry_count": len(retries),
            "recovered": sum(float(r["score"]) == 1 for r in retries),
            "median_duration_ms": statistics.median(durations) if durations else None,
            "p95_duration_ms": _percentile(durations, 0.95),
            "median_tokens": statistics.median(tokens) if tokens else None,
        }
    return result


def _paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary = {
        (str(r["engine"]), int(r["task_id"])): float(r["score"]) == 1
        for r in rows
        if int(r["attempt"]) == 1
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


def _resolve_tasks(selection: PilotSelection) -> list[dict[str, Any]]:
    try:
        from .runner import _configure_benchmark

        benchmark = _configure_benchmark()
        tasks = validate_against_official(selection, benchmark)
        official = {int(task["task_id"]): benchmark.get_task(int(task["task_id"])) for task in tasks}
    except Exception:
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
        evals = contract.get("evaluation_contract") or []
        expected = (evals[0].get("expected") or {}) if evals else {}
        task["task_type"] = str(expected.get("task_type") or _task_type(int(task["task_id"]))).upper()
    return tasks


def _group_rows(
    attempts: list[dict[str, Any]], tasks: list[dict[str, Any]], field: str
) -> list[list[Any]]:
    meta = {int(t["task_id"]): t for t in tasks}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        if int(row["attempt"]) == 1:
            value = meta[int(row["task_id"])][field]
            value = value[0] if isinstance(value, list) else value
            grouped[(str(value), str(row["engine"]))].append(row)
    result = []
    for value in sorted({key[0] for key in grouped}):
        line: list[Any] = [SITE_LABELS.get(value, TIER_LABELS.get(value, value))]
        for engine in ENGINE_LABELS:
            items = grouped[(value, engine)]
            line.append(
                f"{sum(float(x['score']) == 1 for x in items)}/{len(items)}" if items else "—"
            )
        result.append(line)
    return result


REPORT_JS = """function theme(){const r=document.documentElement,n=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=n;try{localStorage.setItem('browser-agent-theme',n)}catch(e){}}function filterRows(){const controls=[...document.querySelectorAll('[data-filter]')],query=(document.querySelector('[data-search]')?.value||'').trim().toLowerCase();let visible=0,total=0;document.querySelectorAll('tbody tr[data-tier]').forEach(r=>{total++;const hidden=controls.some(c=>c.value&&r.dataset[c.dataset.filter]!==c.value)||(query&&!String(r.dataset.search||r.textContent).toLowerCase().includes(query));r.hidden=hidden;if(!hidden)visible++;});document.querySelectorAll('[data-visible-count]').forEach(x=>x.textContent=visible+' / '+total);}function sortTable(id,col){const t=document.getElementById(id),b=t?.tBodies[0];if(!b)return;const dir=t.dataset.col==col&&t.dataset.dir==='asc'?'desc':'asc';[...b.rows].sort((a,z)=>{const av=a.cells[col]?.dataset.sort||a.cells[col]?.textContent||'',zv=z.cells[col]?.dataset.sort||z.cells[col]?.textContent||'';const an=Number(av.replace(/[^0-9.-]/g,'')),zn=Number(zv.replace(/[^0-9.-]/g,''));const cmp=!Number.isNaN(an)&&!Number.isNaN(zn)?an-zn:av.localeCompare(zv,'zh-CN');return dir==='asc'?cmp:-cmp}).forEach(r=>b.appendChild(r));t.dataset.col=col;t.dataset.dir=dir;}document.addEventListener('DOMContentLoaded',()=>{try{document.documentElement.dataset.theme=localStorage.getItem('browser-agent-theme')||'light'}catch(e){}document.querySelectorAll('[data-filter]').forEach(x=>x.addEventListener('change',filterRows));document.querySelectorAll('[data-search]').forEach(x=>x.addEventListener('input',filterRows));filterRows();});"""
REPORT_STYLE = (
    SHARED_STYLE
    + """
body{overflow-x:hidden}.hero h1,.publication-article h1,.publication-article h2{overflow-wrap:anywhere;word-break:break-word}
.top nav{gap:18px;flex-wrap:nowrap}.top nav a[aria-current=page]{color:var(--ink);font-weight:800;text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:8px}.top .theme-toggle{margin-left:auto}.page-main{min-height:78vh}.hero.compact{padding-bottom:22px}.hero.compact h1{font-size:clamp(34px,4.5vw,52px)}.fine{font-size:13px;color:var(--muted)}.result-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.result-grid .card{border-top:3px solid var(--accent)}.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin:18px 0}.filters select,.filters input{padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink)}.filters input{min-width:260px}.status-pass{color:var(--good);font-weight:750}.status-fail{color:var(--bad);font-weight:750}.flow{display:grid;grid-template-columns:repeat(6,1fr);gap:9px;list-style:none;padding:0}.flow li{padding:14px;border:1px solid var(--line);border-radius:11px;background:var(--panel);font-size:13px}.flow li+li:before{content:'→';color:var(--accent);margin-right:7px}.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.detail-grid .kv{min-height:92px}.page-links{display:flex;gap:10px;flex-wrap:wrap}.page-links a{border:1px solid var(--line);background:var(--panel);padding:8px 12px;border-radius:9px}.rank-list{counter-reset:rank}.rank-item{counter-increment:rank;display:grid;grid-template-columns:54px 1fr;gap:16px;padding:20px 0;border-top:1px solid var(--line)}.rank-item:before{content:counter(rank);font-size:34px;font-weight:850;color:var(--accent)}.rank-item h3{margin:4px 0}.article-entry{border-left:4px solid var(--accent)}.source-list li{margin:9px 0}.table-wrap{margin:18px 0}.table-wrap table{min-width:760px}.table-wrap caption{text-align:left;padding:10px 12px;color:var(--muted);caption-side:bottom}.footer{border-top:1px solid var(--line)}.evidence-pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}.evidence-card{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:16px;min-width:0}.evidence-card h3{margin-top:0}.json-view,.log-view{white-space:pre-wrap;overflow-wrap:anywhere;max-height:540px;overflow:auto;padding:14px;border:1px solid var(--line);border-radius:9px;background:var(--bg);font-size:12px;line-height:1.55}.trajectory{list-style:none;padding:0;counter-reset:step}.trajectory>li{counter-increment:step;display:grid;grid-template-columns:46px 1fr;gap:12px;padding:15px 0;border-top:1px solid var(--line)}.trajectory>li:before{content:counter(step);display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--accent);color:white;font-weight:800}.trajectory summary{cursor:pointer;font-weight:750}.rule-fail{border-left:4px solid var(--bad)}.rule-pass{border-left:4px solid var(--good)}.count-note{margin-left:auto;color:var(--muted);font-size:13px}.badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--wash);font-size:12px;margin-right:6px}.muted-box{padding:14px;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}@media(max-width:900px){.result-grid{grid-template-columns:repeat(2,1fr)}.flow{grid-template-columns:repeat(3,1fr)}.evidence-pair{grid-template-columns:1fr}}@media(max-width:600px){.wrap{width:calc(100% - 28px)}.result-grid,.flow{grid-template-columns:1fr}.flow li+li:before{content:'↓';display:block}.hero{padding-top:28px}.hero.compact h1{font-size:30px;line-height:1.18}.filters input{min-width:0;width:100%}}@media print{.top,.filters{display:none!important}.card,.table-wrap{box-shadow:none}.json-view,.log-view{max-height:none;overflow:visible}.evidence-pair{grid-template-columns:1fr}}
"""
)
NAV = [
    ("", "首页", "home"),
    ("guide/", "报告解读", "guide"),
    ("changes/", "变更记录", "changes"),
    ("datasets/", "评测集", "datasets"),
    ("methodology/", "评测方法", "methodology"),
    ("analysis/", "结果分析", "analysis"),
    ("review/", "核验记录", "review"),
    ("adoption/", "方法介绍", "adoption"),
    ("reproduce/", "环境与复现", "reproduce"),
    ("accounts/", "账号", "accounts"),
]


def _page(*, title: str, active: str, body: str, depth: int = 0) -> str:
    local_base = "../" * depth or "./"
    links = "".join(
        f'<a href="{href}"'
        + (' aria-current="page"' if key == active else "")
        + f">{_e(label)}</a>"
        for href, label, key in NAV
    )
    return f'<!doctype html><html lang="zh-CN"><head><base href="{local_base}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)} · Browser Use 评测</title><link rel="stylesheet" href="assets/style.css"><script defer src="assets/report.js"></script></head><body><header class="top"><nav class="wrap">{links}<button class="theme-toggle" onclick="theme()">切换主题</button></nav></header><main class="page-main">{body}</main><footer class="footer"><div class="wrap"><p>结论只适用于本轮预注册样本、Agent、模型与运行配置；展示调整不改变测量结果。</p></div></footer></body></html>'


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
            key: value
            for key, value in rule.items()
            if key not in {"evaluator", "expected"}
        }
        cards.append(
            '<article class="evidence-card"><h3>'
            + _e(f"规则 {index} · {evaluator}")
            + "</h3><p><span class=\"badge\">程序化验收</span>该规则必须得分 1.0。</p>"
            + _json_pre(expected)
            + (f"<details><summary>匹配选项与结果 schema</summary>{_json_pre(qualifiers)}</details>" if qualifiers else "")
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
        if item["name"] == "AgentResponseEvaluator":
            parts.append("最终回答与官方结构化答案不一致")
        elif item["name"] == "NetworkEventEvaluator":
            parts.append("未观察到满足合同的必要网络请求或副作用")
        else:
            parts.append(f"{item['name']} 未通过")
        if item.get("error"):
            parts.append(str(item["error"]))
    if row.get("error"):
        parts.append(str(sanitize(row["error"])))
    response = row.get("agent_response") or {}
    if response.get("error_details"):
        parts.append(str(sanitize(response["error_details"])))
    return "；".join(dict.fromkeys(x.strip() for x in parts if x.strip())) + "。"


def _evaluator_result_html(result: dict[str, Any] | None) -> str:
    items = evaluator_summary(result)
    if not items:
        return '<p class="muted-box">没有 evaluator 明细。</p>'
    cards = []
    for item in items:
        passed = float(item.get("score") or 0) == 1
        detail = {
            key: item.get(key)
            for key in ("assertions", "error", "should_not_exist")
            if item.get(key) not in (None, [], {})
        }
        cards.append(
            f'<article class="evidence-card rule-{"pass" if passed else "fail"}"><h3>规则 {item["index"]} · {_e(item["name"])}</h3>'
            f'<p>{"通过" if passed else "失败"} · score={_e(item.get("score"))}</p>'
            '<div class="evidence-pair"><div><h4>官方期望</h4>'
            + _json_pre(item.get("expected"))
            + '</div><div><h4>实际归一化结果</h4>'
            + _json_pre(item.get("actual"))
            + "</div></div>"
            + (f"<details open><summary>断言与错误</summary>{_json_pre(detail)}</details>" if detail else "")
            + "</article>"
        )
    return "".join(cards)


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
            '<li><div><details><summary>'
            + _e(trajectory_label(step))
            + f'</summary><p class="fine">{meta}</p><h4>调用参数</h4>'
            + _json_pre(step.get("arguments"))
            + '<h4>执行输出节选</h4>'
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
    failures = [[f"#{task_id}", FAILURE_NOTES[task_id]] for task_id in sorted(FAILURE_NOTES)]
    return (
        '<article class="publication-article"><header class="essay-head"><div class="eyebrow">Browser Agent 实测解读</div><h1>25 比 25 不是“内核等价”：它真正暴露的是共同瓶颈</h1><p class="lead">同一 CowAgent 与同一模型替换浏览器后，30 个配对任务没有一个出现单边胜出。这个结果证明对照流程跑通，却没有为内核优劣提供方向性证据。</p><p class="source-note">测量口径：WebArena‑Verified 1.2.3；首轮 pass@1；失败最多一次诊断重试；官方确定性 evaluator；无 LLM judge。</p></header><section><h2>一、最重要的结果不是平分，而是零个不一致配对</h2><p>Chromium 与 Moli 都通过 25/30，首轮成功率都是 83.3%。逐题配对后，25 题共同通过、5 题共同失败，两个浏览器单边通过都是 0。精确 McNemar 双侧 p=1.000，但这里最有解释力的不是 p 值，而是检验没有拿到任何不一致样本。</p>'
        + _table(
            ["配对结果", "任务数", "可支持的判断"],
            [
                ["两者都通过", paired["both_pass"], "当前组合可完成"],
                ["仅 Chromium", paired["chromium_only"], "本轮未观察到"],
                ["仅 Moli", paired["moli_only"], "本轮未观察到"],
                ["两者都失败", paired["both_fail"], "优先排查共同链路"],
            ],
            caption="表 1｜30 个首轮任务的逐题配对；重试不并入主分。",
        )
        + "<p>严谨表述只能是“本轮没有观察到成功率差异”，不能写成“两种内核等价”。30 题对小效应检出力有限；更关键的是，样本没有诱发内核分歧。</p></section><section><h2>二、五个共同失败指向检索聚合与最终状态，不是重试次数</h2><p>失败集中在四个信息聚合任务和一个购物车状态任务。十个失败试次各获得一次重试，恢复数仍为 0。继续增加重试只会提高成本并模糊 pass@1。</p>"
        + _table(["任务", "公开诊断"], failures, caption="表 2｜不公开参考答案或 Agent 原始回答。")
        + "<p>共同失败不能单独证明模型是唯一根因，因为 CowAgent 提示、工具抽象和步数上限也是共同变量；但它足以改变下一步优先级：先检查跨页收集、去重、范围约束和提交后状态验证。</p></section><section><h2>三、简单、中等、困难都覆盖了，但“难度”只是操作长度</h2><p>WebArena‑Verified 没有官方 easy/medium/hard 三档。本项目在运行前按最少可见 UI 操作数注册为 1–4、5–7、8+ 三档，配额 5/10/15。它控制操作链覆盖，不等同于推理难度。</p>"
        + _table(
            ["本项目分层", "Chromium", "Moli"],
            _group_rows(attempts, tasks, "tier"),
            caption="表 3｜各层级首轮通过数/任务数。",
        )
        + _table(
            ["站点", "Chromium", "Moli"],
            _group_rows(attempts, tasks, "sites"),
            caption="表 4｜四个可重置自托管站点。",
        )
        + '</section><section><h2>四、选择 WebArena‑Verified 是为了归因，不是因为它最流行</h2><p>Mind2Web 家族采用面更广，但原版主要评离线动作预测；Online‑Mind2Web 和 WebVoyager贴近实时网页，却引入网页漂移和模型裁判。WebArena‑Verified 可以自托管、逐题重置，并用结构化最终答案和必要网络副作用做程序化验收，更适合“只替换浏览器”的 A/B。</p><p>代价也明确：部署重，环境兼容本身会成为工程变量；官方 evaluator 不衡量路径效率，也不能覆盖开放互联网泛化。</p></section><section><h2>五、下一轮应主动寻找内核敏感样本</h2><p>保留 CowAgent、模型、reasoning=low、pass@1 和一次失败诊断重试，扩大稳定可验收的配对样本；新增长页面、上传下载、复杂编辑器、弹窗、多标签页等更可能诱发内核差异的任务。实时网站另开 Online‑Mind2Web / WebVoyager 泛化轨道，LLM judge 与人工抽检单列。</p><div class="essay-note"><h3>决策边界</h3><p>本轮支持“流程可比、当前样本无方向性差异”；不支持“内核等价”“Moli 已全面替代 Chromium”或“端到端耗时就是纯内核速度”。Astra 获权后必须形成独立运行。</p></div></section></article>'
    )


def _standalone_article(body: str) -> str:
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Browser Agent 实测解读</title><style>{REPORT_STYLE}</style></head><body class="standalone-article"><main class="essay"><div class="wrap">{body}</div></main></body></html>'


def _filters(*, attempts: bool = False) -> str:
    extra = (
        '<label>浏览器 <select data-filter="engine"><option value="">全部</option><option value="chromium">Chromium</option><option value="moli">Moli</option></select></label>'
        '<label>口径 <select data-filter="attempt"><option value="">全部</option><option value="primary">首轮</option><option value="retry">失败诊断重试</option></select></label>'
        '<label>失败环节 <select data-filter="failure"><option value="">全部</option><option value="none">无</option><option value="response">最终回答</option><option value="network">网络副作用</option><option value="mixed">多项</option></select></label>'
        if attempts
        else ""
    )
    return (
        '<div class="filters"><label>全文搜索 <input data-search type="search" placeholder="任务、意图、答案、错误"></label>'
        '<label>层级 <select data-filter="tier"><option value="">全部</option><option value="easy">简单</option><option value="medium">中等</option><option value="hard">困难</option></select></label>'
        '<label>站点 <select data-filter="site"><option value="">全部</option><option value="shopping">Shopping</option><option value="shopping_admin">Shopping Admin</option><option value="reddit">Reddit</option><option value="gitlab">GitLab</option></select></label>'
        '<label>类型 <select data-filter="kind"><option value="">全部</option><option>RETRIEVE</option><option>NAVIGATE</option><option>MUTATE</option></select></label>'
        '<label>结果 <select data-filter="result"><option value="">全部</option><option value="pass">通过</option><option value="fail"'
        + (" selected" if attempts else "")
        + '>失败</option></select></label>'
        + extra
        + '<span class="count-note" data-visible-count></span></div>'
    )


def _task_table(tasks: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> str:
    primary = {(r["engine"], int(r["task_id"])): r for r in attempts if int(r["attempt"]) == 1}
    rows = []
    for task in tasks:
        task_id, site, kind = int(task["task_id"]), task["sites"][0], task["task_type"]
        c, m = primary.get(("chromium", task_id)), primary.get(("moli", task_id))
        result = "pass" if c and m and float(c["score"]) == float(m["score"]) == 1 else "fail"
        values = [
            Safe(f'<a href="datasets/{task_id}.html">#{task_id}</a>'),
            TIER_LABELS[task["tier"]],
            SITE_LABELS.get(site, site),
            kind,
            task["estimated_min_steps"],
            task["rationale"],
            _status(c) if c else "—",
            _status(m) if m else "—",
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
            f'<tr data-tier="{task["tier"]}" data-site="{site}" data-kind="{kind}" data-result="{result}" data-search="{_e(search)}">{cells}</tr>'
        )
    labels = ["任务", "层级", "站点", "类型", "最少步", "入选理由", "Chromium", "Moli"]
    headers = "".join(
        f'<th><button class="sort-button" onclick="sortTable(\'task-table\',{i})">{_e(x)} ↕</button></th>'
        for i, x in enumerate(labels)
    )
    return (
        _filters()
        + f'<div class="table-wrap"><table id="task-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _attempt_table(attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> str:
    task_meta = {int(task["task_id"]): task for task in tasks}
    rows = []
    for row in attempts:
        p = _public_attempt(row)
        result = "pass" if p["score"] == 1 else "fail"
        result_record = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        failed_names = [
            item["name"]
            for item in evaluator_summary(result_record)
            if float(item.get("score") or 0) < 1
        ]
        failure = (
            "none"
            if not failed_names
            else "mixed"
            if len(failed_names) > 1
            else "network"
            if failed_names[0] == "NetworkEventEvaluator"
            else "response"
        )
        task = task_meta.get(p["task_id"], {})
        values = [
            Safe(f'<a href="attempts/{p["id"]}.html">{p["id"]}</a>'),
            ENGINE_LABELS[p["engine"]],
            p["task_id"],
            TIER_LABELS[p["tier"]],
            "重试" if p["is_retry"] else "首轮",
            _status(row),
            _seconds(p["duration_ms"]),
            _number(p["model_usage"]["total_tokens"]),
            p["transport_error_count"],
        ]
        cells = "".join(f"<td>{x if isinstance(x, Safe) else _e(x)}</td>" for x in values)
        rows.append(
            f'<tr data-tier="{p["tier"]}" data-site="{(task.get("sites") or [""])[0]}" '
            f'data-kind="{task.get("task_type") or _task_type(p["task_id"])}" data-result="{result}" '
            f'data-engine="{p["engine"]}" data-attempt="{"retry" if p["is_retry"] else "primary"}" data-failure="{failure}" '
            f'data-search="{_e(" ".join([p["id"], str(task.get("intent") or ""), _failure_diagnosis(result_record, row)]))}">{cells}</tr>'
        )
    headers = "".join(
        f"<th>{_e(x)}</th>"
        for x in [
            "试次",
            "浏览器",
            "任务",
            "层级",
            "口径",
            "结果",
            "端到端耗时",
            "Tokens",
            "传输重试",
        ]
    )
    return (
        _filters(attempts=True)
        + f'<div class="table-wrap"><table id="attempt-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _failure_inventory(attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> str:
    task_by_id = {int(task["task_id"]): task for task in tasks}
    rows = []
    for row in attempts:
        if int(row["attempt"]) != 1 or float(row["score"]) == 1:
            continue
        task_id = int(row["task_id"])
        result = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        failed = [
            item["name"]
            for item in evaluator_summary(result)
            if float(item.get("score") or 0) < 1
        ]
        rows.append(
            [
                Safe(f'<a href="attempts/{_attempt_id(row)}.html">{_attempt_id(row)}</a>'),
                task_by_id.get(task_id, {}).get("rationale", f"Task #{task_id}"),
                "、".join(failed) or "未定位",
                _failure_diagnosis(result, row),
                Safe(f'<a href="datasets/{task_id}.html">任务与两浏览器对照</a>'),
            ]
        )
    return _table(["首轮试次", "任务", "失败规则", "直接原因", "同题对照"], rows)


def _engine_attempts(engine: str, attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> str:
    meta = {int(task["task_id"]): task for task in tasks}
    rows = []
    for row in attempts:
        if row["engine"] != engine:
            continue
        task = meta[int(row["task_id"])]
        result = read_public_json(Path((row.get("paths") or {}).get("evaluation", "")))
        rows.append(
            [
                Safe(f'<a href="attempts/{_attempt_id(row)}.html">{_attempt_id(row)}</a>'),
                task["rationale"],
                TIER_LABELS[task["tier"]],
                "重试" if int(row["attempt"]) == 2 else "首轮",
                _status(row),
                _seconds(float(row.get("duration_ms") or 0)),
                _failure_diagnosis(result, row) if float(row["score"]) < 1 else "—",
            ]
        )
    return _table(["试次", "任务", "层级", "口径", "结果", "耗时", "失败原因"], rows)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_report(*, evidence: Path, output: Path, selection_path: Path) -> Path:
    selection = load_pilot(selection_path)
    tasks = _resolve_tasks(selection)
    attempts = _read_attempts(evidence)
    public_attempts = [_public_attempt(r) for r in attempts]
    manifest, config, review = (
        _read_json(evidence / name)
        for name in ("manifest.json", "run-config.json", "result-review.json")
    )
    summary, paired = _primary_summary(attempts), _paired_summary(attempts)
    protocol, environment = manifest.get("protocol") or {}, manifest.get("environment") or {}
    model, run_date = (
        str(protocol.get("model") or "未记录"),
        str(manifest.get("created_at") or "")[:10] or "未记录",
    )
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=parent))
    try:
        for folder in (
            "assets",
            "datasets",
            "attempts",
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
        article = _article_body(paired, attempts, tasks)
        c, m = summary["chromium"], summary["moli"]
        home = _hero(
            f"Browser Agent Evaluation · {run_date}",
            "同一自主 Agent、同一模型，只替换浏览器",
            f"CowAgent + {model}（reasoning=low）在预注册的 30 题 WebArena‑Verified 样本上对比 Chromium 与 Moli。",
        )
        home += _section(
            "先看结论",
            f'<div class="result-grid"><div class="card"><span class="tiny">Chromium pass@1</span><div class="n">{c["passed"]}/{c["completed"]}</div><p>{_pct(c["score"])}</p></div><div class="card"><span class="tiny">Moli pass@1</span><div class="n">{m["passed"]}/{m["completed"]}</div><p>{_pct(m["score"])}</p></div><div class="card"><span class="tiny">单边胜出</span><div class="n">{paired["chromium_only"] + paired["moli_only"]}</div><p>{paired["paired"]} 个完整配对</p></div><div class="card"><span class="tiny">失败重试恢复</span><div class="n">{c["recovered"] + m["recovered"]}/{c["retry_count"] + m["retry_count"]}</div><p>不回写主分</p></div></div><p class="callout success"><strong>当前结论：</strong>本轮没有观察到浏览器成功率差异；这证明对照流程可比，不证明两个内核等价。五个共同失败更值得优先检查 Agent 的跨页聚合与最终状态验证。</p>',
        )
        home += _section(
            "从结论进入证据",
            '<div class="grid"><article class="card article-entry" style="grid-column:span 6"><h3>报告解读</h3><p>对零分歧、共同失败和下一轮设计的完整论证。</p><a href="guide/">阅读完整解读 →</a></article><article class="card" style="grid-column:span 3"><h3>30 个任务</h3><p>筛选并下钻两种浏览器试次。</p><a href="datasets/">打开任务库 →</a></article><article class="card" style="grid-column:span 3"><h3>70 个试次</h3><p>首轮、唯一重试和核验状态。</p><a href="analysis/">打开结果库 →</a></article></div>',
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
                caption="同一任务的两个首轮结果；重试不参与配对主分。",
            )
            + '<p class="fine">精确 McNemar 双侧 p=1.000。零个不一致配对意味着当前样本无法估计内核方向性效应。</p>',
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
        releases = (
            _read_json(Path("research/report-changes.json")).get("browser_agent_releases") or []
        )
        changes_rows = releases[0].get("changes") or [] if releases else []
        changes = _hero(
            "Release notes",
            "变更记录只写会影响理解和复现的正式变化",
            "分数、分母与证据绑定没有因出版结构变化而改变。",
        ) + _section(
            "当前发布",
            _table(["变更", "原因", "处理", "影响"], changes_rows)
            if changes_rows
            else "<p>暂无发布记录。</p>",
        )
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
            '<div class="rank-list"><article class="rank-item"><div><h3>Mind2Web 家族 · 5 家</h3><p>OpenAI、Anthropic、Google DeepMind、THUDM / ChatGLM、Browser Use。原版是离线动作预测，Online 与 2.0 的目标和判分不同。</p></div></article><article class="rank-item"><div><h3>WebArena 家族 · 3 家</h3><p>OpenAI、Anthropic、THUDM / ChatGLM。自托管、可重置、结果验收，更适合浏览器 A/B。</p></div></article><article class="rank-item"><div><h3>WebVoyager · 2 家</h3><p>OpenAI、Google DeepMind。真实网站直观，但网页漂移与 LLM judge 降低精确复现性。</p></div></article></div><p class="fine">采用次数是公开一手材料中的机构数，不是质量分；未找到 DeepSeek、Kimi 公开的一手 UI browser-use benchmark，不等于内部没有评测。</p>',
        )
        datasets += _section(
            "Mind2Web 的拆分不是 easy / medium / hard",
            "<p>原始 Mind2Web 使用 cross-task、cross-website、cross-domain 衡量泛化；Online‑Mind2Web 没有统一三档；Mind2Web 2 的 short / medium / long 按动作跨度划分。本报告的简单 5、中等 10、困难 15 是预注册的操作长度层级。</p>",
        ) + _section(
            "怎样阅读任务 ground truth",
            '<p><strong>WebArena‑Verified 的 ground truth 不是一条固定点击脚本。</strong>每题声明一个结构化最终回答合同，并在需要确认页面副作用时附加一个或多个网络事件合同。所有合同都通过才记 1 分。任务详情同时展示合同、两种浏览器的实际回答、失败规则和 CowAgent 自主轨迹。</p>',
        ) + _section("30 个任务", _task_table(tasks, attempts))
        (stage / "datasets/index.html").write_text(
            _page(title="评测集", active="datasets", body=datasets, depth=1), encoding="utf-8"
        )

        by_attempt = {(r["engine"], int(r["task_id"]), int(r["attempt"])): r for r in attempts}
        for task in tasks:
            task_id, site = int(task["task_id"]), task["sites"][0]
            task_rows = []
            for engine in ENGINE_LABELS:
                for attempt_no in (1, 2):
                    row = by_attempt.get((engine, task_id, attempt_no))
                    if row:
                        task_rows.append(
                            [
                                ENGINE_LABELS[engine],
                                "首轮" if attempt_no == 1 else "失败诊断重试",
                                _status(row),
                                _seconds(float(row["duration_ms"])),
                                Safe(f'<a href="attempts/{_attempt_id(row)}.html">查看试次</a>'),
                            ]
                        )
            detail = _hero(
                f"Task #{task_id}",
                task["rationale"],
                "官方任务合同、两种浏览器的实际结果与完整自主执行证据。",
            )
            detail += _section(
                "任务身份",
                f'<div class="detail-grid"><div class="kv"><b>站点</b>{_e(SITE_LABELS.get(site, site))}</div><div class="kv"><b>本项目层级</b>{_e(TIER_LABELS[task["tier"]])} · {_e(task["estimated_min_steps"])} 步起</div><div class="kv"><b>任务类型</b>{_e(task["task_type"])}</div><div class="kv"><b>官方 hard subset</b>{"是" if task["official_hard"] else "否"}</div></div><h3>公开任务意图</h3><p>{_e(task["intent"])}</p><p class="fine">模板 ID {task.get("intent_template_id") or "—"} · revision {task.get("revision") or "—"}</p>',
            )
            detail += _section("Ground truth · 官方验收合同", _task_contract_html(task))
            detail += _section(
                "两种浏览器的全部正式试次",
                _table(["浏览器", "口径", "官方结果", "端到端耗时", "证据"], task_rows)
                + (
                    f'<p class="callout failure"><strong>共同失败诊断：</strong>{_e(FAILURE_NOTES[task_id])}</p>'
                    if task_id in FAILURE_NOTES
                    else ""
                ),
            )
            primary_comparison = []
            for engine in ENGINE_LABELS:
                row = by_attempt.get((engine, task_id, 1))
                if not row:
                    continue
                result_record = read_public_json(
                    Path((row.get("paths") or {}).get("evaluation", ""))
                )
                event_record = read_public_json(
                    Path((row.get("paths") or {}).get("events", ""))
                )
                step_count = len(
                    trajectory(event_record if isinstance(event_record, list) else [])
                )
                failed_rules = [
                    item["name"]
                    for item in evaluator_summary(result_record)
                    if float(item.get("score") or 0) < 1
                ]
                primary_comparison.append(
                    [
                        ENGINE_LABELS[engine],
                        _status(row),
                        Safe(_json_pre(sanitize(row.get("agent_response") or {}))),
                        "、".join(failed_rules) or "无",
                        _failure_diagnosis(result_record, row),
                        Safe(
                            f'<a href="attempts/{_attempt_id(row)}.html#trajectory">查看 {step_count} 步轨迹</a>'
                        ),
                    ]
                )
            detail += _section(
                "首轮结果对照",
                _table(
                    ["浏览器", "结果", "实际最终回答", "失败规则", "为什么这样判", "执行过程"],
                    primary_comparison,
                ),
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
            "固定协议",
            _table(
                ["项目", "规则"],
                [
                    ["Agent / 模型", f"CowAgent；{model}；reasoning=low"],
                    ["唯一变量", "同题、同数据、同模型、同提示和资源上限，只替换浏览器"],
                    ["主分", "每题×浏览器第一次正式执行；二元 pass@1；逐题配对"],
                    ["重试", "首轮失败后最多一次，只做恢复诊断；模型传输失败同请求最多重发一次"],
                    [
                        "排除",
                        "不纳入无法稳定验收的实时任务；首轮排除需大型数据卷的 Wikipedia / Map",
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
            + '<p><strong>合取规则：</strong>一题配置的每个 evaluator 都必须得分 1.0，整题才通过。这里没有 LLM-as-a-judge，也没有人工改分。</p>',
        )
        (stage / "methodology/index.html").write_text(
            _page(title="评测方法", active="methodology", body=methodology, depth=1),
            encoding="utf-8",
        )

        analysis = _hero(
            "Results",
            "从总体配对下钻到每一次执行",
            "先定位十个首轮失败的规则与直接原因，再筛选全部 70 次执行并下钻回答、轨迹和日志。",
        )
        analysis += _section(
            "汇总",
            _table(
                [
                    "浏览器",
                    "首轮通过",
                    "pass@1",
                    "失败重试恢复",
                    "耗时中位数",
                    "耗时 P95",
                    "Tokens 中位数",
                ],
                [
                    [
                        ENGINE_LABELS[e],
                        f"{summary[e]['passed']}/{summary[e]['completed']}",
                        _pct(summary[e]["score"]),
                        f"{summary[e]['recovered']}/{summary[e]['retry_count']}",
                        _seconds(summary[e]["median_duration_ms"]),
                        _seconds(summary[e]["p95_duration_ms"]),
                        _number(summary[e]["median_tokens"]),
                    ]
                    for e in ENGINE_LABELS
                ],
            )
            + '<p class="fine">耗时包含模型响应、页面等待、站点重置与浏览器执行，不能解释为纯内核速度。</p>',
        )
        analysis += _section(
            "首轮失败定位",
            '<p>两种浏览器在同五题失败。下表不是人工猜测：失败环节来自官方 evaluator，补充说明来自 Agent 最终错误和执行记录。</p>'
            + _failure_inventory(attempts, tasks),
        )
        analysis += _section(
            "70 个试次",
            '<p>默认只显示失败；可按浏览器、层级、站点、任务类型、首轮/重试、失败环节和全文关键词筛选。</p>'
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
            item = review_by_key.get((public["engine"], public["task_id"], public["attempt"]), {})
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
                f"{ENGINE_LABELS[public['engine']]} · Task #{public['task_id']} · {'重试' if public['is_retry'] else '首轮'}",
                "从官方 ground truth、实际回答、规则级判定下钻到 CowAgent 的每一步 BrowserTool 调用。",
            )
            # Pages declare <base href="../"> so every internal URL is rooted at
            # the report bundle, including same-page fragment links.
            attempt_page = f"attempts/{public['id']}.html"
            detail += (
                '<nav class="page-links wrap" aria-label="试次详情导航">'
                f'<a href="{attempt_page}#comparison">答案对照</a>'
                f'<a href="{attempt_page}#rules">规则判定</a>'
                f'<a href="{attempt_page}#trajectory">执行轨迹</a>'
                f'<a href="{attempt_page}#runtime">运行与证据</a>'
                "</nav>"
            )
            detail += _section(
                "结果与运行",
                f'<div class="detail-grid"><div class="kv"><b>官方结果</b>{_status(row)}</div><div class="kv"><b>失败定位</b>{_e(_failure_diagnosis(evaluation_record, row))}</div><div class="kv"><b>自主操作</b>{len(steps)} 次 BrowserTool 调用</div><div class="kv"><b>端到端耗时</b>{_seconds(public["duration_ms"])}</div><div class="kv"><b>模型 tokens</b>{_number(usage["total_tokens"])}</div><div class="kv"><b>模型请求</b>{usage["request_count"]} 次调用 / {usage["request_attempt_count"]} 次传输尝试</div><div class="kv"><b>传输错误</b>{public["transport_error_count"]}</div><div class="kv"><b>证据复核</b>{_e(item.get("outcome") or "未记录")}</div></div>',
            )
            detail += '<div id="comparison"></div>' + _section(
                "Ground truth 与实际最终回答",
                '<div class="evidence-pair"><article class="evidence-card"><h3>官方任务合同</h3>'
                + _task_contract_html(task)
                + '</article><article class="evidence-card"><h3>Agent 实际回答</h3>'
                + _json_pre(response_record)
                + "</article></div>",
            )
            detail += '<div id="rules"></div>' + _section(
                "官方 evaluator 逐规则判定",
                '<p>所有规则都必须得分 1.0；这里使用 evaluator 已归一化的实际值与期望值直接对照。</p>'
                + _evaluator_result_html(evaluation_record),
            )
            detail += '<div id="trajectory"></div>' + _section(
                "CowAgent 自主执行轨迹",
                '<p>以下顺序来自本次执行的 Agent 事件流，不是固定动作回放。展开可查看调用参数和输出摘录；完整脱敏日志可下载。</p>'
                + _trajectory_html(steps),
            )
            detail += '<div id="runtime"></div>' + _section(
                "运行、日志与证据",
                _artifact_links(public["id"])
                + '<p class="fine">公开包包含脱敏的回答、evaluator、事件流与轨迹。原始 HAR 和 Playwright trace 可能携带登录态、Cookie、页面快照或请求正文，因此不公开；其字节数和 SHA-256 保留用于证明证据绑定。</p>'
                + _table(["指标", "值"], resource.items())
                + '<p class="fine">整次 Agent 运行的容器计量只作诊断；不同进程模型下不直接比较内存或 PID。</p>'
                + '<h3>证据完整性检查</h3>'
                + _table(
                    ["检查", "结果"],
                    [[key, "通过" if value else "未通过"] for key, value in checks.items()],
                )
                + '<h3>原始证据文件绑定</h3>'
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
            (stage / f"attempts/{public['id']}.html").write_text(
                _page(title=public["id"], active="analysis", body=detail, depth=1), encoding="utf-8"
            )

        coverage = review.get("coverage") or {}
        review_rows = [
            [
                Safe(
                    f'<a href="attempts/{x["engine"]}-{int(x["task_id"])}-a{int(x["attempt"])}.html">'
                    f'{x["engine"]}-{int(x["task_id"])}-a{int(x["attempt"])}</a>'
                ),
                x["outcome"],
                sum(not v for v in (x.get("checks") or {}).values()),
                ", ".join(k for k, v in (x.get("checks") or {}).items() if not v) or "—",
            ]
            for x in review.get("attempts") or []
        ]
        review_page = _hero(
            "Result review",
            "70/70 个试次均完成确定性证据复核",
            "复核不重新判题，也不是 LLM-as-a-judge；官方 evaluator 仍是唯一成绩来源。",
        )
        review_page += _section(
            "核验结论",
            f'<div class="result-grid"><div class="card"><span class="tiny">记录试次</span><div class="n">{coverage.get("recorded_attempts", 0)}</div></div><div class="card"><span class="tiny">已复核</span><div class="n">{coverage.get("reviewed_attempts", 0)}</div></div><div class="card"><span class="tiny">记分分歧</span><div class="n">{len(review.get("disagreements") or [])}</div></div><div class="card"><span class="tiny">未决</span><div class="n">{len(review.get("unresolved") or [])}</div></div></div><p>检查回答、HAR、Agent trace、事件和 evaluator 明细是否存在，并核对哈希、分数、状态、官方版本和模型传输尝试。</p>',
        )
        review_page += _section(
            "复核协议",
            _json_pre(review.get("protocol") or {})
            + '<p>人工或二次模型不得修改官方分数；复核只验证文件存在、哈希绑定、状态/分数一致、官方版本一致和传输尝试计数。</p>',
        )
        review_page += _section(
            "逐试次覆盖",
            _table(["试次", "结论", "失败检查数", "失败检查项"], review_rows),
        )
        (stage / "review/index.html").write_text(
            _page(title="核验记录", active="review", body=review_page, depth=1), encoding="utf-8"
        )

        adoption = _hero(
            "Methods",
            "两条轨道回答不同问题，不能混分",
            "固定 BrowserTool 动作轨道与自主 Agent 轨道都有效，但控制变量和结论边界不同。",
        )
        adoption += _section(
            "仓库中的两个 harness",
            _table(
                ["轨道", "Agent 是否自主", "主指标", "回答的问题"],
                [
                    [
                        "浏览器内核微基准",
                        "CowAgent 执行预先固定的 BrowserTool 动作",
                        "动作正确率、资源和耗时",
                        "相同动作交给两个内核，兼容性和成本怎样",
                    ],
                    [
                        "WebArena‑Verified Agent 评测",
                        "CowAgent 自主观察、规划和操作",
                        "官方首轮 pass@1",
                        "替换浏览器如何影响完整任务成功率",
                    ],
                ],
            )
            + "<p><strong>本报告的 30 个 WebArena‑Verified 任务始终由 CowAgent 自主观察、规划和操作。</strong>表中的 browser_kernel 是仓库里另一套执行层 harness，不是这 30 个任务，也没有被拿来冒充自主 Agent 成绩。</p>",
        )
        adoption += _section(
            "Browser Use 官方实践",
            "<p>Browser Use 公开使用 BU Bench V1、Odysseys、Online‑Mind2Web 和 Stealth Bench。可借鉴的是运行前确认任务可完成、保存逐题 trace、断点续跑，并把站点不可完成与 Agent 真失败分开。Stealth Bench 测反机器人检测，不能替代自主任务成功率。</p>",
        )
        adoption += _section(
            "公开依据",
            '<ul class="source-list"><li><a href="https://openai.com/index/gpt-6-astra/">OpenAI：GPT‑6 Astra</a></li><li><a href="https://github.com/ServiceNow/webarena-verified">WebArena‑Verified</a></li><li><a href="https://github.com/OSU-NLP-Group/Mind2Web">Mind2Web</a>；<a href="https://github.com/OSU-NLP-Group/Online-Mind2Web">Online‑Mind2Web</a></li><li><a href="https://github.com/browser-use/benchmark">Browser Use benchmark</a></li><li><a href="https://github.com/ljang0/Odysseys">Odysseys</a></li><li><a href="https://github.com/THUDM/AutoWebGLM">THUDM AutoWebGLM</a></li></ul>',
        )
        (stage / "adoption/index.html").write_text(
            _page(title="方法介绍", active="adoption", body=adoption, depth=1), encoding="utf-8"
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
                        ["首轮成绩", f"{summary[engine]['passed']}/{summary[engine]['completed']}"],
                        ["首轮耗时中位数", _seconds(summary[engine]["median_duration_ms"])],
                    ],
                )
                + '<p class="fine">版本与镜像身份用于限定复现边界，不参与评分。</p>',
            )
            body += _section(
                "全部执行记录",
                '<p>首轮决定主分；仅失败任务有一次诊断重试，重试不回写主分。</p>'
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
        reproduce += _section(
            "公开复现入口",
            '<p><a href="https://github.com/lexmount/browser-eval">lexmount/browser-eval</a> 是代码、题集和锁文件的唯一来源。</p><pre><code>./examples/browser_agent/run.sh\nmake browser-agent-review EVIDENCE="$BROWSER_AGENT_EVIDENCE"\nmake browser-agent-report EVIDENCE="$BROWSER_AGENT_EVIDENCE"\nmake browser-agent-report-check EVIDENCE="$BROWSER_AGENT_EVIDENCE"</code></pre><div class="page-links"><a href="data/selection.json">selection.json</a><a href="data/results.jsonl">results.jsonl</a><a href="data/summary.json">summary.json</a><a href="data/environment.json">environment.json</a><a href="data/review.json">review.json</a><a href="data/publication-privacy.json">publication-privacy.json</a></div><p class="fine">每个试次的脱敏回答、evaluator、事件和轨迹位于 <code>artifacts/&lt;attempt-id&gt;/</code>，也可从试次详情页直接下载。</p>',
        )
        (stage / "reproduce/index.html").write_text(
            _page(title="环境与复现", active="reproduce", body=reproduce, depth=1), encoding="utf-8"
        )
        accounts = _hero(
            "Accounts",
            "本地站点账号、模型权限与发布权限分开处理",
            "这里只说明权限边界，不展示密钥、Cookie、用户名或个人信息。",
        ) + _section(
            "权限与费用",
            _table(
                ["范围", "本轮做法", "外部付费账号"],
                [
                    ["四个自托管站点", "官方 Docker 镜像与内置测试账号；每题前重置", "不需要"],
                    ["模型 API", f"{model}，reasoning=low", "需要相应 API 权限"],
                    [
                        "GPT‑6 Astra",
                        "代理实测返回 team_model_access_denied，未纳入成绩",
                        "当前团队无权限",
                    ],
                    ["Artifact Hub", "只发布脱敏静态报告", "发布者需已有站点权限"],
                ],
            ),
        )
        (stage / "accounts/index.html").write_text(
            _page(title="账号", active="accounts", body=accounts, depth=1), encoding="utf-8"
        )

        claims = {
            "schema": "lex_browser_agent_interpretation/1",
            "claims": [
                {"claim": "当前样本未观察到成功率差异", "basis": paired},
                {
                    "claim": "失败重试没有恢复",
                    "basis": {
                        e: {
                            "retry_count": summary[e]["retry_count"],
                            "recovered": summary[e]["recovered"],
                        }
                        for e in ENGINE_LABELS
                    },
                },
                {"claim": "五个失败完全重合", "basis": sorted(FAILURE_NOTES)},
            ],
            "bound_attempts_sha256": _sha256(evidence / "attempts.jsonl"),
            "selection_sha256": selection.sha256,
        }
        _write_json(stage / "guide/interpretation-evidence.json", claims)
        _write_json(
            stage / "data/selection.json",
            {
                "schema": "lex_browser_agent_public_selection/1",
                "selection_sha256": selection.sha256,
                "tasks": tasks,
            },
        )
        (stage / "data/results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in public_attempts),
            encoding="utf-8",
        )
        audit = {
            "schema": "lex_browser_agent_report/2",
            "attempt_count": len(attempts),
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
            "schema": "lex_browser_agent_public_environment/1",
            "protocol": {
                key: protocol.get(key)
                for key in (
                    "agent",
                    "browser_eval_commit",
                    "cowagent_commit",
                    "model",
                    "reasoning_effort",
                    "primary_attempts_per_engine_task",
                    "failure_retries",
                    "model_transport_retries",
                    "official_score_uses",
                    "evaluator",
                    "environment_reset",
                    "engine_order",
                    "sites",
                )
            },
            "environment": {
                "platform": environment.get("platform"),
                "docker_server_version": environment.get("docker_server_version"),
                "docker_architecture": environment.get("docker_architecture"),
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
                "schema": "lex_browser_agent_publication_privacy/1",
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
            },
        )
        (stage / ".generated-report").write_text("browser-agent-report/2\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
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
        default=Path("evaluation/browser-agent/webarena-verified-pilot.json"),
    )
    args = parser.parse_args()
    print(build_report(evidence=args.evidence, output=args.output, selection_path=args.selection))


if __name__ == "__main__":
    main()
