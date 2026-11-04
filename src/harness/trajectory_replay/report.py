"""Chinese paginated evidence-first browser benchmark report."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.environments.browser import DEFAULT_BROWSER_IMAGES
from harness.trajectory_replay.participants import engine_label, engine_names, summary_engines

ASSETS = Path(__file__).with_name("assets")
REPORT_CSS = (ASSETS / "curated-report.css").read_text(encoding="utf-8")
SUITE_FILTER_JS = (ASSETS / "suite-filter.js").read_text(encoding="utf-8")
EVIDENCE_DIALOG_JS = (ASSETS / "evidence-dialog.js").read_text(encoding="utf-8")
EXECUTION_FILTER_JS = (ASSETS / "curated-execution-filter.js").read_text(encoding="utf-8")

REPORT_STEM = "trajectory-replay-evaluation-latest"
PAGES = tuple(
    (slug, label, f"{REPORT_STEM}{suffix}.html")
    for slug, label, suffix in (
        ("index", "结论摘要", ""),
        ("suite", "评测集", "-suite"),
        ("method", "环境与方法", "-method"),
        ("analysis", "逐任务分析", "-analysis"),
        ("appendix", "复现与附录", "-appendix"),
    )
)


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _trace_text(value: Any) -> str:
    """Remove machine-identifying IPs and diff-breaking line-end whitespace."""
    text = str(value if value is not None else "")
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP 已脱敏]", text)
    return "\n".join(line.rstrip() for line in text.splitlines())


def _show(value: Any, divisor: float = 1.0, suffix: str = "") -> str:
    return f"{value / divisor:,.2f}{suffix}" if isinstance(value, (int, float)) else "无数据"


def _stat(metric: dict[str, Any], key: str, divisor: float = 1.0) -> str:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return _show(value, divisor)


def _rate(values: dict[str, Any]) -> str:
    value = values.get("pass_rate")
    return _show(value * 100 if isinstance(value, (int, float)) else None, suffix="%")


@dataclass(frozen=True)
class ComparedValue:
    text: str
    css_class: str


def _comparison_class(value: Any, other: Any, *, higher: bool = False) -> str:
    if not isinstance(value, (int, float)) or not isinstance(other, (int, float)):
        return ""
    if value == other:
        return ""
    return "better" if (value > other if higher else value < other) else "worse"


def _table(
    headers: list[str], rows: list[list[Any]], caption: str, *, source_marks: bool = False
) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td data-label="{_e(headers[i])}"'
            + (f' class="{v.css_class}"' if isinstance(v, ComparedValue) and v.css_class else "")
            + ">"
            + (
                _annotate_source_marks(_e(v.text if isinstance(v, ComparedValue) else v))
                if source_marks
                else _e(v.text if isinstance(v, ComparedValue) else v)
            )
            + "</td>"
            for i, v in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _numeric_stat(metric: dict[str, Any], key: str) -> float | int | None:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return value if isinstance(value, (int, float)) else None


def _display_number(value: float | int | None, divisor: float = 1.0) -> float | None:
    """Return the exact two-decimal number shown in an absolute metric cell."""
    if not isinstance(value, (int, float)):
        return None
    return float(f"{value / divisor:.2f}")


def _engines(summary: dict[str, Any]) -> tuple[str, ...]:
    """Participants in evidence order; a summary with no results keeps the registry's."""
    return summary_engines(summary) or tuple(DEFAULT_BROWSER_IMAGES)


def _all_parties(engines: tuple[str, ...]) -> str:
    return "双方" if len(engines) == 2 else "所有 Provider "


def _provider_count(engines: tuple[str, ...]) -> str:
    return "两个" if len(engines) == 2 else f" {len(engines)} 个"


def _ratio_pairs(
    summary: dict[str, Any], engines: tuple[str, ...]
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (comparison, baseline, per-task ratio metrics) for every compared participant."""
    comparison = summary.get("resource_comparison") or {}
    if len(engines) == 2:
        paired = comparison.get("paired_ratios") or {}
        return [(engines[1], engines[0], paired.get("metrics") or {})]
    by_engine = (comparison.get("relative_ratios") or {}).get("by_engine") or {}
    return [(engine, engines[0], by_engine.get(engine) or {}) for engine in engines[1:]]


def _pair_label(comparison: str, baseline: str) -> str:
    return f"{engine_label(comparison)} / {engine_label(baseline)}"


def _absolute_metric_cell(
    value: float | int | None,
    others: tuple[float | int | None, ...],
    divisor: float,
) -> str:
    css_class = ""
    shown = _display_number(value, divisor)
    if others and all(
        _comparison_class(shown, _display_number(other, divisor)) == "better" for other in others
    ):
        css_class = ' class="better"'
    return f"<td{css_class}>{_e(_show(value, divisor))}</td>"


def _ratio_metric_cell(value: float | int | None) -> str:
    css_class = _comparison_class(_display_number(value), 1)
    class_attr = f' class="{css_class}"' if css_class else ""
    return f"<td{class_attr}>{_e(_show(value, suffix='×'))}</td>"


def _grouped_resource_table(
    summary: dict[str, Any],
    metrics: tuple[tuple[str, str, float, str], ...],
    caption: str,
) -> str:
    by_engine = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    engines = _engines(summary)
    body = []
    for key, label, divisor, unit in metrics:
        cells = [f'<td data-label="指标">{_e(f"{label} ({unit})")}</td>']
        for stat in ("p50", "p95", "mean"):
            values = {
                engine: _numeric_stat((by_engine.get(engine) or {}).get(key) or {}, stat)
                for engine in engines
            }
            for engine in engines:
                others = tuple(values[other] for other in engines if other != engine)
                cells.append(_absolute_metric_cell(values[engine], others, divisor))
        body.append("<tr>" + "".join(cells) + "</tr>")
    group_headers = "".join(
        f'<th colspan="{len(engines)}">{label}</th>' for label in ("P50", "P95", "平均值")
    )
    engine_headers = "".join(
        f"<th>{engine_label(engine)}</th>" for _ in range(3) for engine in engines
    )
    return (
        f'<div class="table-wrap resource-summary"><table><caption>{_e(caption)}</caption>'
        f'<thead><tr><th rowspan="2">指标</th>{group_headers}</tr>'
        f"<tr>{engine_headers}</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _paired_ratio_table(
    values: dict[str, Any],
    metrics: tuple[tuple[str, str, float, str], ...],
    caption: str,
) -> str:
    rows = []
    for key, label, _divisor, _unit in metrics:
        metric = values.get(key) or {}
        rows.append(
            [
                label,
                *[
                    ComparedValue(
                        _show(_numeric_stat(metric, stat), suffix="×"),
                        _comparison_class(_display_number(_numeric_stat(metric, stat)), 1),
                    )
                    for stat in ("p50", "p95", "mean")
                ],
            ]
        )
    return _table(
        ["指标", "逐任务比值 P50", "逐任务比值 P95", "逐任务比值平均值"],
        rows,
        caption,
    )


def _duration_ratio_audit(summary: dict[str, Any]) -> str:
    pairs = _ratio_pairs(summary, _engines(summary))
    return "".join(
        _duration_pair_audit(summary, comparison, baseline, metrics, labelled=len(pairs) > 1)
        for comparison, baseline, metrics in pairs
    )


def _duration_pair_audit(
    summary: dict[str, Any],
    comparison: str,
    baseline: str,
    metrics: dict[str, Any],
    *,
    labelled: bool,
) -> str:
    values = (metrics.get("duration_ms") or {}).get("values") or []
    if not values:
        return ""
    p95_rank = max(1, (95 * len(values) + 99) // 100)
    rows = []
    for rank, item in enumerate(values, 1):
        task_id = str(item.get("task_id") or "")
        title = str(((summary.get("by_task") or {}).get(task_id) or {}).get("title") or task_id)
        rows.append(
            [
                str(rank),
                title,
                *[
                    ComparedValue(
                        _show(item.get(key), suffix=" ms"),
                        "better"
                        if _comparison_class(
                            _display_number(item.get(key)), _display_number(item.get(other))
                        )
                        == "better"
                        else "",
                    )
                    for key, other in (("denominator", "numerator"), ("numerator", "denominator"))
                ],
                ComparedValue(
                    _show(item.get("ratio"), suffix="×"),
                    _comparison_class(_display_number(item.get("ratio")), 1),
                ),
                "P95 取值" if rank == p95_rank else "",
            ]
        )
    scope = f"{_pair_label(comparison, baseline)} " if labelled else ""
    return (
        f"<details><summary>查看{scope}逐任务耗时比值的完整计算审计</summary>"
        "<p>脚本先对每个计入成功交集的同任务计算 "
        f"<code>{engine_label(comparison)} 耗时 / {engine_label(baseline)} 耗时</code>，再排序。"
        f"本轮 <code>n={len(values)}</code>，nearest-rank P95 的序号为 "
        f"<code>ceil(0.95 × {len(values)}) = {p95_rank}</code>；因此表中的 P95 是排序后第 "
        f"{p95_rank} 个配对比值。</p>"
        + _table(
            ["排序", "任务", engine_label(baseline), engine_label(comparison), "逐任务比值", "统计位置"],
            rows,
            f"{scope}耗时配对比值原始序列",
        )
        + "</details>"
    )


def _nav(active: str) -> str:
    return (
        '<nav aria-label="报告分页">'
        + "".join(
            f'<a href="{filename}" {"class=active aria-current=page" if slug == active else ""}>{i}. {label}</a>'
            for i, (slug, label, filename) in enumerate(PAGES, 1)
        )
        + "</nav>"
    )


def _shell(
    manifest: dict[str, Any],
    active: str,
    body: str,
    engines: tuple[str, ...],
    *,
    detail_title: str = "",
    back_anchor: str = "",
) -> str:
    names = engine_names(engines)
    label = next(p[1] for p in PAGES if p[0] == active)
    index = next(i for i, p in enumerate(PAGES) if p[0] == active)
    prev_link = (
        f'<a href="{PAGES[index - 1][2]}">← {PAGES[index - 1][1]}</a>' if index else "<span></span>"
    )
    next_link = (
        f'<a href="{PAGES[index + 1][2]}">{PAGES[index + 1][1]} →</a>'
        if index + 1 < len(PAGES)
        else "<span></span>"
    )
    header = (
        f'<div class="case-header"><a href="{REPORT_STEM}-analysis.html#{_e(back_anchor)}">← 返回逐任务分析</a>'
        f'<a href="{REPORT_STEM}.html">{" / ".join(engine_label(engine) for engine in engines)} 评测</a><span class="muted">运行 {_e(manifest.get("run_id", "unknown"))}</span></div>'
        if detail_title
        else f'<div class="brand"><div class="eyebrow">Lexmount Browser Provider Evaluation</div><h1>{names} 浏览器工具调用评测</h1><div class="muted">运行编号：{_e(manifest.get("run_id", "unknown"))} · 第 {index + 1}/{len(PAGES)} 页</div></div>{_nav(active)}'
    )
    pager = "" if detail_title else f'<div class="pager">{prev_link}{next_link}</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(detail_title) or label}｜{names} 评测</title><style>{REPORT_CSS}</style></head><body><header><div class="wrap">{header}</div></header><main>{body}{pager}</main><footer>Lexmount Browser Provider Evaluation</footer></body></html>"""


def _correctness_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for engine in _engines(summary):
        values = (summary.get("functional") or {}).get(engine) or {}
        tasks = int(values.get("tasks") or 0)

        def score(
            key: str,
            fallback: str,
            engine_values: dict[str, Any] = values,
            task_total: int = tasks,
        ) -> str:
            passed = int(engine_values.get(key, engine_values.get(fallback, 0)) or 0)
            rate = passed / task_total * 100 if task_total else 0
            return f"{passed}/{task_total}（{rate:.2f}%）"

        rows.append(
            [
                engine,
                score("result_final_passes", "final_passes"),
                score("combined_final_passes", "final_passes"),
            ]
        )
    return rows


def _correctness_table(summary: dict[str, Any]) -> str:
    functional = summary.get("functional") or {}
    scores = {
        engine: (
            int((functional.get(engine) or {}).get("result_final_passes", 0) or 0),
            int((functional.get(engine) or {}).get("combined_final_passes", 0) or 0),
            int((functional.get(engine) or {}).get("tasks", 0) or 0),
        )
        for engine in _engines(summary)
    }
    rows = []
    for engine, (result, combined, tasks) in scores.items():
        result_rate = result / tasks * 100 if tasks else 0
        combined_rate = combined / tasks * 100 if tasks else 0
        others = [score for other, score in scores.items() if other != engine]
        result_winner = (
            tasks
            and others
            and all(
                other[2]
                and _comparison_class(result / tasks, other[0] / other[2], higher=True) == "better"
                for other in others
            )
        )
        combined_winner = (
            tasks
            and others
            and all(
                other[2]
                and _comparison_class(combined / tasks, other[1] / other[2], higher=True)
                == "better"
                for other in others
            )
        )
        rows.append(
            "<tr>"
            f"<td>{_e(engine)}</td>"
            f'<td class="{"better" if result_winner else ""}">{result}/{tasks}（{result_rate:.2f}%）</td>'
            f'<td class="{"better" if combined_winner else ""}">{combined}/{tasks}（{combined_rate:.2f}%）</td>'
            f'<td><a class="provider-failure-link" href="{REPORT_STEM}-analysis.html#provider={engine}&amp;status=fail" aria-label="{engine}：查看 {max(0, tasks - result)} 个失败任务">{max(0, tasks - result)} 个失败 <span aria-hidden="true">→</span></a></td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><caption>任务正确率</caption><thead><tr>'
        "<th>Provider</th><th>结果正确</th><th>结果与参考过程均正确</th><th>任务分析</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _detail_filename(task_id: str, engine: str) -> str:
    return f"{REPORT_STEM}-task-{task_id.encode().hex()}-{engine.encode().hex()}.html"


MOLI_TIMING_FINDING = """<li><strong>时序边界：</strong>Moli 的 1 Hz 是 CDP screencast 下采样，不等于 JavaScript 或 <code>requestAnimationFrame</code> 只有 1 fps；源码中的 rAF 目标间隔为 16 ms。</li>
"""
MOLI_TIMING_STUDY = """<section class="card"><h2>时序机制研究</h2><div class="hypothesis-grid"><div><strong>研究假设</strong><span>低频 screencast 或其触发的布局快照，可能放大动态 DOM 更新后几何、可见性或命中测试状态的滞后。</span></div><div><strong>对照设计</strong><span>固定页面、选择器和调用，比较立即显现、双 rAF、定时器、DOM 替换、动画与视口进入；延迟覆盖 16 ms 到 1 s。</span></div><div><strong>验证指标</strong><span>记录 DOM/rAF 完成时间与 actionability 恢复时间，检验其差值是否随 screencast 条件系统性变化。</span></div></div></section>"""


def _recommendation(summary: dict[str, Any], engines: tuple[str, ...]) -> str:
    functional = summary.get("functional") or {}
    passes = {}
    for engine in engines:
        values = functional.get(engine) or {}
        passes[engine] = int(values.get("result_final_passes", values.get("final_passes", 0)))
    top = max(passes.values())
    parties = _all_parties(engines)
    leaders = tuple(engine for engine in engines if passes[engine] == top)
    if len(engines) == 1:
        return (
            f"本轮只有 {engine_names(engines)} 一个 Provider，结果正确任务数为 {top}；"
            "没有可比较的方案，不给出默认建议。"
        )
    if len(leaders) > 1:
        subject = (
            f"{_provider_count(engines)} Provider" if leaders == engines else f" {engine_names(leaders)}"
        )
        return (
            f"本轮{subject} 的结果正确任务数相同（均为 {top}）；"
            f"仅凭正确性无法确定默认方案，应结合{parties}成功任务上的资源指标和失败类型决策。"
        )
    leader = leaders[0]
    others = tuple(engine for engine in engines if engine != leader)
    lower = "、".join(f"{engine_label(engine)} 的 {passes[engine]}" for engine in others)
    claim = f"本轮建议默认使用 {engine_label(leader)}：其结果正确任务数为 {top}，高于 {lower}；"
    if leader == engines[0]:
        return (
            f"{claim}{engine_names(others)} 在{parties}均成功的任务上若表现出更低耗时或资源占用，"
            "可作为任务已验证兼容时的备选。"
        )
    return f"{claim}仍需结合{parties}成功任务上的资源指标和失败类型确认适用边界。"


def _ratio_finding(summary: dict[str, Any], engines: tuple[str, ...]) -> str:
    pairs = _ratio_pairs(summary, engines)
    if not pairs:
        return ""

    def mean(metrics: dict[str, Any], name: str) -> str:
        return _show(_numeric_stat(metrics.get(name) or {}, "mean"), suffix="×")

    clauses = "；".join(
        f"逐任务 {_pair_label(comparison, baseline)} 比值中，耗时比平均值为 "
        f'{mean(metrics, "duration_ms")}，时间加权平均内存比平均值为 '
        f'{mean(metrics, "memory_mean_bytes")}，网络接收量比平均值为 '
        f'{mean(metrics, "network_rx_bytes")}'
        for comparison, baseline, metrics in pairs
    )
    return f"<li><strong>速度与资源：</strong>{_all_parties(engines)}成功任务的{clauses}。</li>\n"


def _root_cause_finding(engines: tuple[str, ...]) -> str:
    layers, link = (
        (" screencast、渲染器或适配层", "查看逐任务证据与验证方案")
        if "moli" in engines
        else ("渲染器或适配层", "查看逐任务证据")
    )
    return f"""<li><strong>根因边界：</strong>布局与 actionability 证据只能定位现象，尚不能确认{layers}中的具体根因。<a href="trajectory-replay-evaluation-latest-analysis.html">{link}</a>。</li>"""


def _summary_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    task_count = len(manifest.get("suite") or [])
    engines = _engines(summary)
    parties = _all_parties(engines)
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)

    agent_smoke = manifest.get("agent_smoke") or {}
    smoke_html = ""
    if agent_smoke:
        provenance = agent_smoke.get("provenance") or {}
        smoke_html = f"""<section class="card"><h2>CowAgent 端到端冒烟测试</h2><p>该测试与资源微基准分开，不参与上表统计，也没有在运行编号 <code>{_e(manifest.get("run_id"))}</code> 中重新执行。模型 <code>{_e(agent_smoke.get("model"))}</code>，思考强度 <code>{_e(agent_smoke.get("reasoning_effort"))}</code>；独立执行日期 <code>{_e(agent_smoke.get("executed_at"))}</code>，来源文件内容哈希 <code>{_e(str(provenance.get("source_sha256") or "")[:12])}</code>。同一任务每个浏览器方案只执行 1 次。</p>{_table(["浏览器方案", "结果", "BrowserTool 调用次数", "最终答案"], [[str(item.get("provider")), "通过" if item.get("status") == "pass" else str(item.get("status")), str(item.get("browser_tool_calls")), str(item.get("answer"))] for item in agent_smoke.get("results") or []], "独立端到端接受性测试")}</section>"""
    recommendation = _recommendation(summary, engines)
    correctness = _correctness_table(summary)
    highlight_legend = (
        "绝对值优胜项为绿色粗体；配对比值低于 1 为绿色、高于 1 为红色，显示值相同保持中性。"
        if len(engines) > 1
        else ""
    )
    return f"""<section class="card note"><h2>结论与产品建议</h2><p><strong>{recommendation}</strong></p><p>这是浏览器工具调用层的公网确定性微基准，不是 LLM Agent 排名。评测集包含 {task_count} 个结果唯一的任务。</p></section>
<section class="card"><h2>任务正确率</h2>{correctness}</section>
<section class="card" id="technical-findings"><h2>关键技术结论</h2><ul>
{_ratio_finding(summary, engines)}{MOLI_TIMING_FINDING if "moli" in engines else ""}{_root_cause_finding(engines)}</ul></section>
<section class="card" id="performance"><h2>{parties}成功任务的速度与资源</h2><p>统计范围：{parties}都成功的 {paired} 个同任务，不含失败与重试样本。{highlight_legend}</p>{_metric_tables(summary)}{_duration_ratio_audit(summary)}</section>{smoke_html}"""


def _difficulty(value: str) -> str:
    named = {"simple": "简单", "medium": "中等", "complex": "复杂"}
    if value in named:
        return named[value]
    match = re.fullmatch(r"step_(\d+)_(\d+)", value)
    if match:
        return f"{int(match.group(1))}–{int(match.group(2))} 步"
    return value or "未标注"


def _action_text(call: dict[str, Any]) -> str:
    action = str(call.get("action") or "")
    return {
        "navigate": f"打开 {call.get('url', '')} 并返回页面观察快照",
        "get_text": f"读取 {call.get('selector', '')} 文本",
        "get_attribute": f"读取 {call.get('selector', '')} 的 {call.get('name', '')} 属性",
        "get_property": f"读取 {call.get('selector', '')} 的 {call.get('name', '')} DOM 属性",
        "click": f"点击 {call.get('selector', '')}",
        "fill": f"向 {call.get('selector', '')} 填入 {call.get('text', '')}",
        "select": f"在 {call.get('selector', '')} 选择 {call.get('value', '')}",
        "snapshot": f"读取 {call.get('selector') or 'body'} 当前快照",
        "scroll": f"向 {call.get('direction', 'down')} 滚动 {call.get('amount', 700)} px",
        "back": "浏览器后退",
        "forward": "浏览器前进",
        "press": f"按键 {call.get('key', '')}",
    }.get(action, action)


def _source_references(source: dict[str, Any]) -> str:
    links = []
    for url in source.get("design_basis") or []:
        parsed = urlparse(str(url))
        if parsed.scheme in {"https", "http"}:
            label = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.hostname
            links.append(f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(label)}</a>')
    return '<p class="muted">设计依据：' + " · ".join(links) + "</p>" if links else ""


def _annotate_source_marks(escaped_text: str) -> str:
    """Annotate the literal heading permalink character without changing the oracle."""
    return escaped_text.replace(
        "¶", '<span class="source-mark" title="原网页标题锚点字符 U+00B6">¶</span>'
    )


def _source_mark_note(values: Any) -> str:
    return (
        '<p class="source-mark-note">¶ 为原网页标题的锚点字符，参与逐字判定。</p>'
        if "¶" in str(values)
        else ""
    )


def _suite_page(manifest: dict[str, Any]) -> str:
    suite = manifest.get("suite") or []
    validation = manifest.get("ground_truth_validation") or {}
    replay = validation.get("automatic_browser_replay") or {}
    difficulty_values = [str(task.get("complexity") or "") for task in suite]
    ordered_difficulties = [
        difficulty
        for difficulty in ("simple", "medium", "complex")
        if difficulty in difficulty_values
    ] + sorted(
        {
            difficulty
            for difficulty in difficulty_values
            if difficulty not in {"simple", "medium", "complex"}
        }
    )
    difficulty_counts = {
        difficulty: difficulty_values.count(difficulty) for difficulty in ordered_difficulties
    }
    difficulty_summary = (
        "、".join(
            f"{_difficulty(difficulty)} {count} 个"
            for difficulty, count in difficulty_counts.items()
            if count
        )
        or "未标注难度"
    )
    site_counts = Counter(
        urlparse(str((task.get("source") or {}).get("url") or "")).hostname or "未标注"
        for task in suite
    )
    action_counts = Counter(
        str(call.get("action") or "") for task in suite for call in task.get("tool_calls") or []
    )
    distribution = (
        '<div class="grid suite-stats">'
        f'<div class="metric"><strong>{len(suite)}</strong><span>公网任务</span></div>'
        f'<div class="metric"><strong>{len(site_counts)}</strong><span>独立网站/域名</span></div>'
        f'<div class="metric"><strong>{sum(action_counts.values())}</strong><span>参考 BrowserTool 调用</span></div>'
        "</div>"
        + _table(
            ["维度", "分布"],
            [
                ["难度", difficulty_summary],
                [
                    "网站",
                    "；".join(f"{host} {count}" for host, count in sorted(site_counts.items())),
                ],
                [
                    "能力",
                    "；".join(
                        f"{action} {count}" for action, count in sorted(action_counts.items())
                    ),
                ],
            ],
            "评测集分布",
        )
    )
    cards = []
    for index, task in enumerate(suite, 1):
        title = str(task.get("title") or task.get("task_id"))
        calls = task.get("tool_calls") or []
        call_html = "".join(
            f'<div class="call"><strong>调用 {i} · {_e(c.get("action"))}</strong><br>{_e(_action_text(c))}'
            + (
                f'<br><span class="muted">输出：{_e(c.get("save_as"))}</span>'
                if c.get("save_as")
                else ""
            )
            + "</div>"
            for i, c in enumerate(calls, 1)
        )
        truth = task.get("ground_truth") or {}

        expected_inline = "；".join(
            f"{name} = {value}" for name, value in (truth.get("expected_outputs") or {}).items()
        )
        expected_rows = (
            '<dl class="expected-values">'
            + "".join(
                f"<div><dt>{_e(name)}</dt><dd>{_annotate_source_marks(_e(value))}</dd></div>"
                for name, value in (truth.get("expected_outputs") or {}).items()
            )
            + "</dl>"
            + _source_mark_note(truth.get("expected_outputs"))
        )
        reference_path = " → ".join(truth.get("expected_actions") or [])
        source = task.get("source") or {}
        target_url = str(source.get("url") or "")
        source_html = (
            f'<a target="_blank" rel="noopener" href="{_e(target_url)}">{_e(target_url)}</a>'
        )
        shared_tags = {"simple", "medium", "complex", "public-web", "exact-oracle"}
        chips = "".join(
            f'<span class="chip">{_e(tag)}</span>'
            for tag in task.get("tags") or []
            if tag not in shared_tags
        )
        search_text = (
            f"任务{index:02d} 任务 {index:02d} {task.get('task_id')} {title} {target_url} "
            + " ".join(task.get("tags") or [])
            + " "
            + expected_inline
        )
        cards.append(
            f'<section class="card suite-task" data-difficulty="{_e(task.get("complexity"))}" data-site="{_e(urlparse(target_url).hostname or "未标注")}" data-actions="{_e(",".join(sorted({str(c.get("action")) for c in calls})))}" data-search="{_e(search_text)}"><div class="eyebrow">任务 {index:02d}</div><h2>{_e(title)}</h2><span class="chip">{_difficulty(str(task.get("complexity")))}</span><span class="chip">{len(calls)} 次工具调用</span>{chips}'
            '<div class="task-verification">'
            f"<div><strong>公网测试页面</strong><span>{source_html}</span></div>"
            f"<div><strong>参考路径</strong><span><code>{_e(reference_path)}</code></span></div>"
            f"<div><strong>唯一预期结果</strong>{expected_rows}</div>"
            "</div>"
            f"<details><summary>逐步操作与答案依据</summary>{call_html}<p><strong>答案依据：</strong>{_e(truth.get('derivation'))}</p>{_source_references(source)}</details></section>"
        )
    filters = (
        '<section class="card suite-filter-panel" aria-label="任务筛选"><div class="section-heading"><h2>查找任务</h2>'
        f'<span id="suite-count" class="muted" aria-live="polite">显示 {len(suite)} / {len(suite)} 个任务</span></div>'
        '<div class="suite-filter-controls"><label class="suite-search">搜索任务<input id="suite-query" type="search" placeholder="任务名称、编号、网站或关键词"></label>'
        '<label>难度<select id="suite-difficulty"><option value="">全部难度</option>'
        + "".join(
            f'<option value="{_e(d)}">{_difficulty(d)}（{difficulty_counts[d]}）</option>'
            for d in ordered_difficulties
        )
        + '</select></label><label>网站<select id="suite-site"><option value="">全部网站</option>'
        + "".join(
            f'<option value="{_e(s)}">{_e(s)}（{n}）</option>'
            for s, n in sorted(site_counts.items())
        )
        + '</select></label><label>操作能力<select id="suite-action"><option value="">全部操作</option>'
        + "".join(f'<option value="{_e(a)}">{_e(a)}</option>' for a in sorted(action_counts))
        + '</select></label><button id="suite-reset" type="button">清除筛选</button></div></section>'
        '<p id="suite-empty" class="card empty-state" hidden>没有匹配的任务。请调整关键词或清除筛选。</p>'
    )
    script = f"<script>{SUITE_FILTER_JS}</script>"
    return (
        f'<section class="card note"><h2>评测集概览</h2>{distribution}<p class="muted">答案核验：{_e(validation.get("validator") or "未记录")}，{_e(replay.get("passed"))}/{_e(replay.get("total"))} 已核验。数据集 SHA-256：<code>{_e(str(validation.get("dataset_sha256") or "")[:16])}</code>。</p><details><summary>标注来源与版本</summary><p>{_e(validation.get("method") or "未记录")}</p></details></section>'
        + filters
        + "".join(cards)
        + script
    )


def _method_page(manifest: dict[str, Any], engines: tuple[str, ...]) -> str:
    p = manifest.get("protocol") or {}
    env = manifest.get("environment") or {}
    versions = env.get("browser_versions") or {}
    rows = [
        ["运行平台", str(env.get("runner_platform", "未记录"))],
        [
            "容器",
            f"Docker {env.get('docker_server_version', '未记录')} / {env.get('docker_os', '未记录')}",
        ],
        *[[engine_label(engine), str(versions.get(engine, "未记录"))] for engine in engines],
        [
            "单浏览器配额",
            f"{p.get('cpus_per_browser', '?')} CPU / {p.get('memory_per_browser', '?')} / 独立网络",
        ],
        ["执行次数", "每任务每个 Provider 形成 1 个正式结果"],
        ["视口", json.dumps(p.get("viewport") or {}, ensure_ascii=False)],
        [
            "失败复核",
            f"失败时最多补充 {p.get('max_failure_retries', '?')} 条同任务证据，不另计为新任务",
        ],
        ["采样", f"每 {p.get('sample_interval_ms', '?')} ms 采样内存、PID、CPU、网络计数"],
    ]
    alternation = (
        f"，按任务交替{_provider_count(engines)} Provider 的执行顺序" if len(engines) > 1 else ""
    )
    ratio_definition = (
        '<li>配对比值的定义和逐项计算过程见<a href="trajectory-replay-evaluation-latest.html#performance">首页性能表与计算审计</a>。</li>'
        if len(engines) > 1
        else ""
    )
    return f"""<section class="card note"><h2>步骤的严格定义</h2><p><strong>一步 = 一次 CowAgent BrowserTool 等价调用</strong>，例如 navigate、click、get_text、fill、select、scroll、back 或 forward。等待页面就绪和目标值出现属于该工具调用内部，不单列、不计步。每次调用的参数、耗时、输出和状态写入执行轨迹。</p></section><section class="card" id="scoring"><h2>成功判定：两个独立程序指标</h2><ol><li><strong>结果正确（主指标）：</strong>每个标注输出与 <code>expected_outputs</code> 做严格字符串相等；不使用模糊匹配或模型评分；Codex 只参与评测前的标注核对。</li><li><strong>结果与参考过程均正确（辅指标）：</strong>在结果正确之外，本轮被重放的参考动作序列还必须与 <code>expected_actions</code> 一致，且全部 BrowserTool 调用成功。</li></ol><p><strong>边界：</strong><code>expected_actions</code> 是工具层微基准的固定参考路径，不代表任务只有一种解法。若评测自由规划 CowAgent，应把过程判定改成必要里程碑、禁止动作和步数预算，不能要求整条序列逐步相等。</p></section><section class="card"><h2>环境</h2>{_table(["项目", "记录值"], rows, "本轮环境")}</section><section class="card"><h2>执行流程</h2><ol><li>分别启动并预热{_provider_count(engines)}浏览器，浏览器进程和上下文在整轮评测中保持运行。</li><li>任务前清 Cookie、权限、HTTP 缓存、Cache Storage、Service Worker、IndexedDB、来源存储和历史，并创建新页面。</li><li>计时外静置 {p.get("post_reset_settle_ms", "?")} ms{alternation}。</li><li>执行完整任务并采样；失败复核归入同一任务详情。</li><li>资源基线只比较{_all_parties(engines)}都成功的同任务，失败与复核样本不混入正常路径。</li></ol></section><section class="card"><h2>统计口径</h2><ul><li>P50 与中位数是同一个统计量；总体跨任务报告 P50、平均值和 nearest-rank P95。</li>{ratio_definition}<li>每个任务仅有一个正式样本，因此逐任务不计算分位数；当前跨任务点不足以证明普遍性能优势。</li><li>内存平均值是时间加权均值；任务内存 P95 来自单次任务时序采样。</li><li>RX = Receive（接收），TX = Transmit（发送）；总量含页面资源与 CDP，速率来自相邻采样点差分。</li></ul></section>"""


METRICS = (
    (
        "时间与计算",
        (
            ("duration_ms", "总耗时", 1, "ms"),
            ("cpu_ms", "CPU 时间", 1, "ms"),
            ("cpu_average_utilization_pct", "平均 CPU 占单核", 1, "%"),
            ("cpu_sampled_utilization_p95_pct", "任务内 CPU P95", 1, "%"),
        ),
    ),
    (
        "内存",
        (
            ("memory_mean_bytes", "时间加权平均内存", 1048576, "MiB"),
            ("memory_p95_bytes", "任务内存 P95", 1048576, "MiB"),
            ("memory_peak_bytes", "峰值内存", 1048576, "MiB"),
            ("memory_retained_bytes", "结束保留增量", 1048576, "MiB"),
            ("memory_mib_seconds", "内存占用积分", 1, "MiB·s"),
        ),
    ),
    (
        "网络",
        (
            ("network_rx_bytes", "接收总量 RX", 1024, "KiB"),
            ("network_tx_bytes", "发送总量 TX", 1024, "KiB"),
            ("network_rx_bytes_per_second", "平均接收速率", 1024, "KiB/s"),
            ("network_rx_rate_p95_bytes_per_second", "任务内接收速率 P95", 1024, "KiB/s"),
            ("network_tx_rate_p95_bytes_per_second", "任务内发送速率 P95", 1024, "KiB/s"),
        ),
    ),
)


def _metric_tables(summary: dict[str, Any]) -> str:
    absolute = "".join(
        _grouped_resource_table(summary, metrics, f"{group}绝对值（均为越低越好）")
        for group, metrics in METRICS
    )
    pairs = _ratio_pairs(summary, _engines(summary))
    if not pairs:
        return absolute
    paired = "".join(
        _paired_ratio_table(
            values,
            metrics,
            f"{group}逐任务 {_pair_label(comparison, baseline)} 配对比值（均为越低越好）",
        )
        for comparison, baseline, values in pairs
        for group, metrics in METRICS
    )
    formulas = "、".join(
        f"<code>{_pair_label(comparison, baseline)}</code>" for comparison, baseline, _ in pairs
    )
    return (
        absolute
        + f"<h3>逐任务配对比值分布</h3><p>先对每个同任务计算 {formulas}，再汇总这些比值；P50、P95 与平均值都来自同一批逐任务比值，不是两个 Provider 边际分位数相除。</p>"
        + paired
    )


def _task_detail_body(
    task_id: str,
    title: str,
    engine: str,
    spec: dict[str, Any],
    attempts: list[dict[str, Any]],
    diagnostic: dict[str, Any] | None,
) -> str:
    """One definition, one comparison matrix, one copy of each raw payload."""
    evidence: dict[str, tuple[str, list[str]]] = {}

    def evidence_link(value: Any, label: str, display: str = "") -> str:
        if value is None or value == "":
            return ""
        text = _trace_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            if isinstance(value, (dict, list))
            else value
        )
        if text not in evidence:
            evidence[text] = (f"evidence-{len(evidence) + 1}", [])
        anchor, labels = evidence[text]
        if label not in labels:
            labels.append(label)
        return f'<a class="evidence-link" href="#{anchor}" data-evidence="{anchor}" aria-label="{_e(label)}">{_e(display or label)}</a>'

    def status_cell(value: Any) -> str:
        return (
            "通过"
            if value == "pass" or value is True
            else "失败"
            if value == "fail" or value is False
            else "未记录"
        )

    final = attempts[-1]
    source = spec.get("source") or {}
    url = str(source.get("url") or "")
    safe_url = url if urlparse(url).scheme in {"http", "https"} else "#"
    labels = [f"执行记录 {a.get('attempt', i)}" for i, a in enumerate(attempts, 1)]
    total = sum(float(a.get("duration_ms") or 0) for a in attempts)
    result = final.get("result_status") or final.get("status")
    process = final.get("process_status") or final.get("status")
    overview = (
        '<section class="card case-overview" id="case-result">'
        f'<div class="section-heading"><h1>{_e(title)}</h1><span class="chip">{_e(engine)}</span></div>'
        f'<p class="muted case-id">{_e(task_id)}</p>'
        '<div class="case-facts">'
        f'<div><span>结果</span><strong class="{"ok" if result == "pass" else "bad"}">{status_cell(result)}</strong></div>'
        f'<div><span>参考过程</span><strong class="{"ok" if process == "pass" else "bad"}">{status_cell(process)}</strong></div>'
        f"<div><span>累计耗时 · {len(attempts)} 次执行</span><strong>{_show(total, suffix=' ms')}</strong></div></div>"
    )
    if diagnostic:
        overview += (
            '<div class="diagnosis-panel">'
            f"<strong>{_e(diagnostic.get('category') or '其他')}</strong>"
            f"<span>第 {_e(diagnostic.get('failed_step'))} 步 · {_e(diagnostic.get('failed_action'))}"
            f" · <code>{_e(diagnostic.get('selector') or '—')}</code></span>"
            + (
                f"<p>{_e(diagnostic['observed_mechanism'])}</p>"
                if diagnostic.get("observed_mechanism")
                else ""
            )
            + evidence_link(diagnostic.get("actionability_probe"), "Actionability probe")
            + "</div>"
        )
    overview += f'<p class="case-start">起始页面 <a href="{_e(safe_url)}" target="_blank" rel="noopener">{_e(url)}</a></p></section>'

    expected = (spec.get("ground_truth") or {}).get("expected_outputs") or {}
    validations = [{str(v.get("output")): v for v in a.get("validation") or []} for a in attempts]
    outputs = list(dict.fromkeys([*expected, *(k for values in validations for k in values)]))
    answer_rows = []
    for output in outputs:
        expected_value = expected.get(
            output, next((v[output].get("expected") for v in validations if output in v), "未声明")
        )
        cells = [output, str(expected_value)]
        for values in validations:
            item = values.get(output)
            cells.append(
                ComparedValue(
                    f"{'（空字符串）' if item.get('actual') == '' else item.get('actual')} · {status_cell(item.get('passed'))}",
                    "ok"
                    if item.get("passed") is True
                    else "bad"
                    if item.get("passed") is False
                    else "",
                )
                if item
                else "未产生（过程失败） · 未判定"
            )
        answer_rows.append(cells)
    answers = (
        _table(["输出", "标准答案", *labels], answer_rows, "答案对照", source_marks=True)
        if outputs
        else '<p class="muted">未声明标准答案。</p>'
    )
    answers += _source_mark_note(expected)
    derivation = (spec.get("ground_truth") or {}).get("derivation")
    answers += f'<p class="muted">答案依据：{_e(derivation)}</p>' if derivation else ""

    calls = spec.get("tool_calls") or []
    traces = [a.get("trace") or [] for a in attempts]
    steps = []
    for i in range(max([len(calls), *(len(t) for t in traces)], default=0)):
        call = calls[i] if i < len(calls) else {}
        reference = (
            f'<code class="action-name">{_e(call.get("action"))}</code><span>{_e(_action_text(call))}</span>'
            if call
            else "额外调用"
        )
        parameters = {k: v for k, v in call.items() if k != "save_as"}
        if call:
            reference += evidence_link(parameters, f"步骤 {i + 1} 参数", "参数")
            if call.get("save_as"):
                reference += f"<small>保存为 <code>{_e(call['save_as'])}</code></small>"
        cells = [f"<td>{i + 1}</td>", f"<td>{reference}</td>"]
        for label, trace, attempt in zip(labels, traces, attempts, strict=True):
            if i >= len(trace):
                cells.append('<td class="muted">未执行</td>')
                continue
            actual = trace[i]
            passed = actual.get("status") == "pass"
            cell = f'<code class="action-name" aria-label="实际浏览器动作">{_e(actual.get("action"))}</code><strong class="{"ok" if passed else "bad"}">{status_cell(actual.get("status"))}</strong> <span class="muted">{_show(actual.get("duration_ms"), suffix=" ms")}</span>'
            if actual.get("action") != call.get("action"):
                cell += '<small class="bad">与参考动作不同</small>'
            if (actual.get("arguments") or {}) != parameters:
                cell += evidence_link(
                    actual.get("arguments") or {}, f"{label} · 步骤 {i + 1} 参数", "实际参数"
                )
            cell += evidence_link(actual.get("output"), f"{label} · 步骤 {i + 1} 输出", "输出")
            if actual.get("error"):
                # The full exception contains the call error; keep the full payload once.
                error = attempt.get("error")
                if not error or _trace_text(actual["error"]) not in _trace_text(error):
                    cell += evidence_link(actual["error"], f"{label} · 步骤 {i + 1} 错误", "异常")
                else:
                    cell += evidence_link(error, f"{label} · 完整异常", "完整异常")
            cells.append(f"<td>{cell}</td>")
        steps.append("<tr>" + "".join(cells) + "</tr>")
    process_cells = []
    for label, attempt in zip(labels, attempts, strict=True):
        pv = attempt.get("process_validation") or {}
        process_cells.append(
            "<td>"
            + status_cell(pv.get("passed"))
            + evidence_link(pv, f"{label} · 过程判定", "判定详情")
            + "</td>"
        )
    steps.append("<tr><td>—</td><td>过程判定</td>" + "".join(process_cells) + "</tr>")
    step_table = (
        '<div class="table-wrap step-comparison"><table><caption>参考操作与执行对照</caption><thead><tr><th>步骤</th><th>浏览器参考动作</th>'
        + "".join(f"<th>{_e(label)}</th>" for label in labels)
        + "</tr></thead><tbody>"
        + "".join(steps)
        + "</tbody></table></div>"
    )

    execution_rows = []
    for label, attempt in zip(labels, attempts, strict=True):
        execution_rows.append(
            "<tr><td>"
            + _e(label)
            + "</td><td>"
            + _show(attempt.get("duration_ms"), suffix=" ms")
            + "</td><td>"
            + (evidence_link(attempt.get("error"), f"{label} · 完整异常") or "无异常")
            + "</td></tr>"
        )
    execution_table = (
        '<div class="table-wrap"><table><caption>执行记录</caption><thead><tr><th>记录</th><th>耗时</th><th>异常证据</th></tr></thead><tbody>'
        + "".join(execution_rows)
        + "</tbody></table></div>"
    )
    resource_rows = []
    for _, metrics in METRICS:
        for key, label, divisor, unit in metrics:
            if key == "duration_ms" or not any(key in (a.get("resource") or {}) for a in attempts):
                continue
            resource_rows.append(
                [
                    f"{label} ({unit})",
                    *[_show((a.get("resource") or {}).get(key), divisor) for a in attempts],
                ]
            )
    resources = (
        _table(["指标", *labels], resource_rows, "资源对照")
        if resource_rows
        else '<p class="muted">未记录资源采样。</p>'
    )
    raw = "".join(
        f'<article class="evidence-item" id="{anchor}"><div class="section-heading"><h3>{_e(anchor.replace("evidence-", "证据 "))}</h3><a href="#case-steps">回到步骤</a></div><p class="muted">{_e(" / ".join(refs))}</p><pre tabindex="0" aria-label="{_e(refs[0])}">{_e(text)}</pre></article>'
        for text, (anchor, refs) in evidence.items()
    )
    evidence_index = (
        '<div class="evidence-index">'
        + "".join(
            f'<a class="evidence-link" href="#{anchor}" data-evidence="{anchor}" aria-label="{_e(refs[0])}"><strong>{_e(anchor.replace("evidence-", "证据 "))}</strong><span>{_e(" / ".join(refs))}</span><span>查看</span></a>'
            for _, (anchor, refs) in evidence.items()
        )
        + "</div>"
    )
    dialog = (
        '<dialog id="evidence-dialog" aria-labelledby="evidence-title" aria-describedby="evidence-context">'
        '<div class="dialog-heading"><div><h2 id="evidence-title">原始证据</h2>'
        f'<p class="muted" id="evidence-context">{_e(engine)} · {_e(title)}</p></div>'
        '<form method="dialog"><button type="submit" autofocus aria-label="关闭证据窗口">关闭</button></form></div>'
        '<p id="evidence-action" class="muted"></p><pre id="evidence-content" tabindex="0" aria-label="完整证据内容"></pre></dialog>'
    )
    script = f"<script>{EVIDENCE_DIALOG_JS}</script>"
    return (
        '<div class="case-detail">'
        + overview
        + '<nav class="case-nav" aria-label="任务详情导航"><a href="#case-result">结果</a><a href="#case-steps">操作与答案</a><a href="#case-resources">执行资源</a><a href="#case-evidence">原始证据</a></nav>'
        + '<section class="card" id="case-steps"><h2>操作与答案</h2>'
        + answers
        + step_table
        + _source_references(source)
        + "</section>"
        + '<section class="card" id="case-resources"><h2>执行资源</h2>'
        + execution_table
        + resources
        + "</section>"
        + '<section class="card" id="case-evidence"><h2>原始证据</h2>'
        + (evidence_index + raw if raw else "<p>未记录执行轨迹。</p>")
        + "</section>"
        + dialog
        + script
        + "</div>"
    )


def _execution_details(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    task_specs = {str(task.get("task_id")): task for task in manifest.get("suite") or []}
    diagnostic_groups = ((summary.get("failure_diagnostics") or {}).get("rule_engine") or {}).get(
        "groups"
    ) or []
    diagnostic_by_attempt = {
        (
            str(group.get("task_id")),
            str(group.get("engine")),
            int(attempt_ref.get("attempt") or 1),
        ): str(group.get("category") or "其他")
        for group in diagnostic_groups
        for attempt_ref in (group.get("attempts") or [])
    }
    diagnostic_by_execution = {
        (str(group.get("task_id")), str(group.get("engine"))): group for group in diagnostic_groups
    }
    blocks = []
    for task_id, task in (summary.get("by_task") or {}).items():
        title = str(task.get("title") or task_id)
        executions = task.get("attempts") or task.get("failures") or {}
        for engine, attempts in executions.items():
            if not attempts:
                continue
            anchor = f"execution-{task_id}-{engine}"
            # Hex encoding is injective and keeps arbitrary task IDs out of paths.
            detail_name = _detail_filename(str(task_id), str(engine))
            final_attempt = attempts[-1]
            final_status = str(final_attempt.get("status") or "fail")
            final_result_status = str(final_attempt.get("result_status") or final_status)
            final_process_status = str(final_attempt.get("process_status") or final_status)
            final_combined_status = str(final_attempt.get("combined_status") or final_status)
            categories = sorted(
                {
                    diagnostic_by_attempt.get(
                        (str(task_id), str(engine), int(attempt.get("attempt") or 1)),
                        _classify(str(attempt.get("error") or "")),
                    )
                    for attempt in attempts
                    if str(attempt.get("status") or "fail") == "fail"
                }
            )
            error_category = ",".join(categories) if categories else "无错误"
            actions = sorted(
                {
                    str(call.get("action"))
                    for attempt in attempts
                    for call in (attempt.get("trace") or [])
                    if call.get("action") != "validate_assertions"
                }
            )
            tags = task.get("tags") or []
            total_duration = sum(float(attempt.get("duration_ms") or 0) for attempt in attempts)
            diagnostic = diagnostic_by_execution.get((str(task_id), str(engine)))
            detail_body = _task_detail_body(
                str(task_id),
                title,
                str(engine),
                task_specs.get(str(task_id)) or {},
                attempts,
                diagnostic,
            )
            # Artifact Site injects a site-root base URL; fragment links need the file.
            detail_body = detail_body.replace('href="#', f'href="{detail_name}#')
            detail_pages[detail_name] = _shell(
                manifest,
                "analysis",
                detail_body,
                _engines(summary),
                detail_title=title,
                back_anchor=anchor,
            )
            search_text = " ".join(
                [
                    title,
                    str(engine),
                    error_category,
                    *[
                        str(a.get("error") or "")
                        + " "
                        + json.dumps(a.get("validation") or [], ensure_ascii=False)
                        + " "
                        + json.dumps(a.get("trace") or [], ensure_ascii=False)
                        for a in attempts
                    ],
                ]
            )
            blocks.append(
                f'<div id="{_e(anchor)}" class="execution-card" data-task="{_e(task_id)}" data-engine="{_e(engine)}" data-status="{_e(final_result_status)}" data-combined-status="{_e(final_combined_status)}" data-complexity="{_e(task.get("complexity"))}" data-error="{_e(error_category)}" data-actions="{_e(",".join(actions))}" data-tags="{_e(",".join(tags))}" data-search="{_e(_trace_text(search_text))}" data-duration="{_e(total_duration)}"><a href="{detail_name}" class="execution-summary"><span>{_e(title)}</span><span>{_e(engine)}</span><span class="{"ok" if final_result_status == "pass" else "bad"}">{"正确" if final_result_status == "pass" else "错误"}</span><span class="{"ok" if final_process_status == "pass" else "bad"}">{"正确" if final_process_status == "pass" else "错误"}</span><span>{_e(error_category)}</span><span>{_show(total_duration, suffix=" ms")}</span></a></div>'
            )
    return "".join(blocks) or "<p>没有可展示的执行样本。</p>"


def _execution_explorer(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    physical_attempts = sum(
        (values or {}).get("physical_attempts", 0)
        for values in (summary.get("functional") or {}).values()
    )
    logical_executions = sum(
        1
        for task in (summary.get("by_task") or {}).values()
        for attempts in ((task.get("attempts") or task.get("failures") or {}).values())
        if attempts
    )
    task_options = "".join(
        f'<option value="{_e(task_id)}">{_e(task.get("title") or task_id)}</option>'
        for task_id, task in (summary.get("by_task") or {}).items()
    )
    action_values = sorted(
        {
            str(c.get("action"))
            for task in (summary.get("by_task") or {}).values()
            for attempts in (task.get("attempts") or {}).values()
            for attempt in attempts
            for c in attempt.get("trace") or []
            if c.get("action") != "validate_assertions"
        }
    )
    action_options = "".join(
        f'<option value="{_e(action)}">{_e(action)}</option>' for action in action_values
    )
    tag_values = sorted(
        {
            str(tag)
            for task in (summary.get("by_task") or {}).values()
            for tag in task.get("tags") or []
        }
    )
    tag_options = "".join(f'<option value="{_e(tag)}">{_e(tag)}</option>' for tag in tag_values)
    diagnostic_categories = sorted(
        {
            str(group.get("category") or "其他")
            for group in (
                ((summary.get("failure_diagnostics") or {}).get("rule_engine") or {}).get("groups")
                or []
            )
        }
    )
    diagnostic_groups = ((summary.get("failure_diagnostics") or {}).get("rule_engine") or {}).get(
        "groups"
    ) or []
    category_counts: dict[str, list[int]] = {}
    for group in diagnostic_groups:
        category = str(group.get("category") or "其他")
        category_counts.setdefault(category, [0, 0])
        category_counts[category][0] += 1
        category_counts[category][1] += len(group.get("attempts") or [])
    category_rows = [
        [name, str(values[0]), str(values[1])] for name, values in category_counts.items()
    ] or [["无失败", "0", "0"]]
    error_options = '<option value="无错误">无错误</option>' + "".join(
        f'<option value="{_e(category)}">{_e(category)}</option>'
        for category in diagnostic_categories
    )
    engines = _engines(summary)
    engine_options = "".join(f"<option>{engine}</option>" for engine in engines)
    filters = f"""<div class="filters" id="execution-filters" aria-label="执行样本筛选">
      <label>任务<select id="f-task"><option value="">全部任务</option>{task_options}</select></label>
      <label>Provider<select id="f-engine"><option value="">全部 Provider</option>{engine_options}</select></label>
      <label>结果<select id="f-status"><option value="">全部结果</option><option value="pass">正确</option><option value="fail" selected>错误</option></select></label>
      <label>结果与过程<select id="f-combined"><option value="">全部</option><option value="pass">均正确</option><option value="fail">未同时正确</option></select></label>
      <label>难度<select id="f-complexity"><option value="">全部难度</option><option value="simple">简单</option><option value="medium">中等</option><option value="complex">复杂</option></select></label>
      <label>BrowserTool 方法<select id="f-action"><option value="">全部方法</option>{action_options}</select></label>
      <label>任务标签<select id="f-tag"><option value="">全部标签</option>{tag_options}</select></label>
      <label>错误类别<select id="f-error"><option value="">全部类别</option>{error_options}</select></label>
      <label>耗时<select id="f-duration"><option value="">全部耗时</option><option value="lt1000">少于 1 秒</option><option value="1000-5000">1–5 秒</option><option value="gte5000">5 秒及以上</option></select></label>
      <label>全文搜索<input id="f-query" type="search" placeholder="任务、输出或错误"></label>
      <button type="button" id="f-all">展示全部</button><button type="button" id="f-reset">重置为错误</button><span class="filter-status" id="f-count" aria-live="polite"></span>
    </div>"""
    provider_list = "[" + ",".join(f"'{engine}'" for engine in engines) + "]"
    script = f"<script>{EXECUTION_FILTER_JS}</script>".replace("__PROVIDER_LIST__", provider_list)
    grid_head = '<div class="record-grid-head" aria-hidden="true"><span>任务</span><span>Provider</span><span>结果</span><span>过程</span><span>错误类别</span><span>总耗时</span></div>'
    return f"""<section class="card" id="execution-results"><h2>逐任务分析与原始证据</h2><p>{logical_executions} 条任务 × Provider 记录，{physical_attempts} 次执行。点击任务查看完整操作、答案、判定和执行轨迹。</p>{_table(["规则类别", "去重失败组", "实际失败次数"], category_rows, "程序化失败分布")}{filters}<div id="execution-list" class="record-grid">{grid_head}{_execution_details(manifest, summary, detail_pages)}</div><p id="execution-empty" class="empty-state" role="status" hidden></p>{script}</section>"""


def _classify(error: str) -> str:
    text = error.lower()
    if "timeout" in text and ("visible" in text or "click" in text):
        return "交互目标超时/不可见"
    if "timeout" in text:
        return "超时"
    if "exact mismatch" in text:
        return "结果不匹配"
    if "net::" in text or "navigation" in text:
        return "导航/网络"
    if "selector" in text or "locator" in text:
        return "定位器"
    return "其他"


def _analysis_page(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    timing_study = MOLI_TIMING_STUDY if "moli" in _engines(summary) else ""
    return f"""{timing_study}{_execution_explorer(manifest, summary, detail_pages)}"""


def _appendix_page(manifest: dict[str, Any], engines: tuple[str, ...]) -> str:
    p = manifest.get("protocol") or {}
    provenance = manifest.get("artifact_provenance") or {}
    fields = [
        ["task", "ground_truth / source / expected_actions", "唯一结果、固定参考路径与来源"],
        [
            "attempt",
            "result_status / process_status / combined_status",
            "结果主指标与结果+过程辅指标",
        ],
        ["attempt", "validation / trace / error", "同一任务的判定与完整执行证据"],
        ["resource", "memory mean/p95/peak/retained/MiB·s", "内存水平、尾部、峰值、保留量和积分"],
        ["resource", "CPU / network total/average/p95/peak", "计算与网络时序压力"],
        (
            [
                "summary",
                "paired intersection / paired ratios",
                f"{_all_parties(engines)}成功任务与逐任务配对比值审计",
            ]
            if len(engines) > 1
            else ["summary", "paired intersection", f"{_all_parties(engines)}成功任务"]
        ),
        ["diagnostics", "rule_engine", "程序化失败分类、证据与原始执行记录入口"],
    ]
    provenance_rows = [
        ["运行编号", str(manifest.get("run_id") or "未记录")],
        ["生成时间", str(manifest.get("created_at") or "未记录")],
        ["评测集 SHA-256", str(manifest.get("suite_sha256") or "未记录")],
        ["attempts.jsonl SHA-256", str(provenance.get("attempts_sha256") or "未记录")],
        ["summary.json SHA-256", str(provenance.get("summary_sha256") or "未记录")],
    ]
    return f"""<section class="card"><h2>复现步骤</h2><ol><li>准备 Docker Engine、Python 3.11、至少 4 CPU/6 GiB 内存。</li><li>运行 <code>./examples/trajectory_replay/run.sh</code>；脚本执行完整基准，并依次生成原始记录、汇总数据和分页 HTML。</li><li>如需为失败任务补充一次同任务证据，设置 <code>TRAJECTORY_REPLAY_FAILURE_RETRIES=1</code>；该记录不会另计为新任务。</li><li>如只需从已有 <code>attempts.jsonl</code> 重算全部指标和报告，运行 <code>uv run python examples/trajectory_replay/rebuild_report.py</code>。</li><li>检查 <code>attempts.jsonl</code>、<code>manifest.json</code>、<code>summary.json</code> 与分页 HTML；所有{"比值、" if len(engines) > 1 else ""}分位数和均值均由仓库脚本计算，Codex 不参与统计。</li></ol><p><a href="trajectory-replay-evaluation-latest-data.json">下载本轮机器可读审计摘要</a></p></section><section class="card"><h2>运行与原始数据绑定</h2>{_table(["项目", "值"], provenance_rows, "可复核来源")}</section><section class="card"><h2>数据字段</h2>{_table(["对象", "关键字段", "用途"], fields, "数据结构")}</section><section class="card"><h2>复现限制</h2><ul><li>全部任务来自公网，页面内容、网络状态或站点策略变化可能导致数据集漂移；运行前需复核数据集 SHA 与标注证据。</li><li>采样间隔 {p.get("sample_interval_ms", "?")} ms，持续时间短于采样间隔的尖峰可能遗漏。</li><li>浏览器进程保持运行且任务间清理状态；分配器保留内存不等于缓存污染。</li></ul></section><section class="card"><h2>原始环境 JSON</h2><details><summary>展开</summary><pre>{_e(json.dumps(manifest.get("environment") or {}, ensure_ascii=False, indent=2, sort_keys=True))}</pre></details></section>"""


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    paired_engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}

    def paired_duration(engine: str, stat: str) -> str:
        return _stat(((paired_engines.get(engine) or {}).get("duration_ms") or {}), stat)

    lines = [
        f"# {engine_names(_engines(summary))} 浏览器工具调用评测",
        "",
        f"运行编号：`{manifest.get('run_id', 'unknown')}`",
        "",
        "这是确定性的 BrowserTool 等价调用微基准；成功判定和失败分类均由程序完成。",
        "",
        "| Provider | 结果正确 | 结果与参考过程均正确 | P50 ms | 平均值 ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in _correctness_rows(summary):
        engine = row[0]
        lines.append(
            "| "
            + " | ".join(
                row
                + [
                    paired_duration(engine, "p50"),
                    paired_duration(engine, "mean"),
                    paired_duration(engine, "p95"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "```bash",
            "TRAJECTORY_REPLAY_FAILURE_RETRIES=1 ./examples/trajectory_replay/run.sh",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_latest_report(
    reports_dir: str | Path, *, manifest: dict[str, Any], summary: dict[str, Any]
) -> Path:
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    audit_name = f"{REPORT_STEM}-data.json"
    detail_pages: dict[str, str] = {}
    analysis = _analysis_page(manifest, summary, detail_pages)
    keep = {f"{REPORT_STEM}.md", audit_name, *(filename for _, _, filename in PAGES), *detail_pages}
    for pattern in (
        "trajectory-replay-evaluation-*.md",
        "trajectory-replay-evaluation-*.html",
        "trajectory-replay-evaluation-*.json",
    ):
        for old in root.glob(pattern):
            if old.name not in keep:
                old.unlink()
    md_path = root / f"{REPORT_STEM}.md"
    md_path.write_text(_markdown(manifest, summary), encoding="utf-8")
    audit = {
        "schema": "lex_browser_use_report_audit/1",
        "run_id": manifest.get("run_id"),
        "created_at": manifest.get("created_at"),
        "suite_sha256": manifest.get("suite_sha256"),
        "artifact_provenance": manifest.get("artifact_provenance") or {},
        "ground_truth_validation": {
            key: value
            for key, value in (manifest.get("ground_truth_validation") or {}).items()
            if key != "human_review"
        },
        "functional": summary.get("functional") or {},
        "resource_comparison": summary.get("resource_comparison") or {},
    }
    (root / audit_name).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    engines = _engines(summary)
    renderers = {
        "index": lambda: _summary_page(manifest, summary),
        "suite": lambda: _suite_page(manifest),
        "method": lambda: _method_page(manifest, engines),
        "analysis": lambda: analysis,
        "appendix": lambda: _appendix_page(manifest, engines),
    }
    for slug, _, filename in PAGES:
        (root / filename).write_text(_shell(manifest, slug, renderers[slug](), engines), encoding="utf-8")
    for filename, document in detail_pages.items():
        (root / filename).write_text(document, encoding="utf-8")
    return md_path
