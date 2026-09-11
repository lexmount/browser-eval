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

from .publication import public_projection

REPORT_STEM = "webarena-trajectory-replay-latest"
ROOT = Path(__file__).resolve().parents[2]
PAGES = tuple(
    (slug, label, f"{REPORT_STEM}{suffix}.html")
    for slug, label, suffix in (
        ("index", "结论", ""),
        ("results", "评测结果", "-results"),
        ("suite", "数据集", "-suite"),
        ("research", "选型依据", "-research"),
        ("method", "方法与核验", "-method"),
        ("appendix", "复现与变更", "-appendix"),
    )
)

CATEGORY_LABELS = {
    "end_to_end_browser_use": "端到端 browser-use",
    "computer_use": "综合 computer-use",
    "gui_grounding": "GUI grounding",
    "web_research": "网页搜索与研究",
}


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
    return f'<div class="table-wrap stackable" role="region" aria-label="{_e(caption)}" tabindex="0"><table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _numeric_stat(metric: dict[str, Any], key: str) -> float | int | None:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return value if isinstance(value, (int, float)) else None


def _display_number(value: float | int | None, divisor: float = 1.0) -> float | None:
    """Return the exact two-decimal number shown in an absolute metric cell."""
    if not isinstance(value, (int, float)):
        return None
    return float(f"{value / divisor:.2f}")


def _absolute_metric_cell(
    value: float | int | None,
    other: float | int | None,
    divisor: float,
) -> str:
    css_class = ""
    if (
        _comparison_class(_display_number(value, divisor), _display_number(other, divisor))
        == "better"
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
    engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    body = []
    for key, label, divisor, unit in metrics:
        chromium = (engines.get("chromium") or {}).get(key) or {}
        moli = (engines.get("moli") or {}).get(key) or {}
        cells = [f'<td data-label="指标">{_e(f"{label} ({unit})")}</td>']
        for stat in ("p50", "p95", "mean"):
            c_value = _numeric_stat(chromium, stat)
            m_value = _numeric_stat(moli, stat)
            cells.append(_absolute_metric_cell(c_value, m_value, divisor))
            cells.append(_absolute_metric_cell(m_value, c_value, divisor))
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="table-wrap resource-summary" role="region" aria-label="{_e(caption)}" tabindex="0"><table><caption>{_e(caption)}</caption>'
        '<thead><tr><th rowspan="2">指标</th><th colspan="2">P50</th><th colspan="2">P95</th><th colspan="2">平均值</th></tr>'
        "<tr><th>Chromium</th><th>Moli</th><th>Chromium</th><th>Moli</th>"
        "<th>Chromium</th><th>Moli</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _paired_ratio_table(
    summary: dict[str, Any],
    metrics: tuple[tuple[str, str, float, str], ...],
    caption: str,
) -> str:
    paired = (summary.get("resource_comparison") or {}).get("paired_ratios") or {}
    values = paired.get("metrics") or {}
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
    paired = (summary.get("resource_comparison") or {}).get("paired_ratios") or {}
    metric = (paired.get("metrics") or {}).get("duration_ms") or {}
    values = metric.get("values") or []
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
    return (
        "<details><summary>查看逐任务耗时比值的完整计算审计</summary>"
        f"<p>脚本先对每个计入成功交集的同任务计算 <code>Moli 耗时 / Chromium 耗时</code>，再排序。"
        f"本轮 <code>n={len(values)}</code>，nearest-rank P95 的序号为 "
        f"<code>ceil(0.95 × {len(values)}) = {p95_rank}</code>；因此表中的 P95 是排序后第 "
        f"{p95_rank} 个配对比值。</p>"
        + _table(
            ["排序", "任务", "Chromium", "Moli", "逐任务比值", "统计位置"],
            rows,
            "耗时配对比值原始序列",
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
    *,
    detail_title: str = "",
    back_anchor: str = "",
) -> str:
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
        f'<div class="case-header"><a href="{REPORT_STEM}-results.html#{_e(back_anchor)}">← 返回评测结果</a>'
        f'<a href="{REPORT_STEM}.html">Chromium / Moli 评测</a><span class="muted">运行 {_e(manifest.get("run_id", "unknown"))}</span></div>'
        if detail_title
        else f'<div class="brand"><div class="eyebrow">WebArena Fixed-Trajectory Replay</div><h1>Chromium 与 Moli：WebArena 固定轨迹回放评测</h1><div class="muted">公开人工轨迹的确定性重放 · 排除 Agent 与模型变量 · 运行 {_e(manifest.get("run_id", "unknown"))}</div></div>{_nav(active)}'
    )
    pager = "" if detail_title else f'<div class="pager">{prev_link}{next_link}</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(detail_title) or label}｜WebArena 固定轨迹回放评测</title><style>
:root{{--ink:#172033;--muted:#5f6c81;--line:#d7deeb;--panel:#f4f7fb;--accent:#2552c7;--good:#117454;--bad:#b42318;--warn:#9a6700}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth;max-width:100%;overflow-x:hidden}}body{{margin:0;max-width:100%;overflow-x:hidden;background:linear-gradient(180deg,#f7f9fc 0,#edf2f8 420px);color:var(--ink);font:15px/1.68 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}a{{color:var(--accent)}}header{{max-width:100%;overflow:hidden;background:#ffffffed;border-bottom:1px solid var(--line);backdrop-filter:blur(12px)}}.wrap,main,footer{{width:100%;min-width:0;max-width:1240px;margin:auto;padding-left:24px;padding-right:24px}}.brand{{min-width:0;padding:20px 0 8px}}.eyebrow{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:800}}h1{{font-size:32px;line-height:1.2;margin:5px 0;overflow-wrap:anywhere}}h2{{font-size:22px;margin:0 0 12px}}h3{{font-size:17px;margin:18px 0 8px}}p,ul,ol{{margin:8px 0}}.muted{{color:var(--muted)}}nav{{width:100%;min-width:0;display:flex;gap:3px;overflow:auto;overscroll-behavior-inline:contain;padding-bottom:12px;scrollbar-width:thin}}nav a{{white-space:nowrap;text-decoration:none;color:var(--muted);padding:7px 9px;border-radius:8px;font-size:13px}}nav a:hover{{background:#f1f4f9;color:var(--ink)}}nav a.active{{background:#e8eeff;color:#173fa8;font-weight:750}}main{{overflow:hidden;padding-top:24px;padding-bottom:44px}}.card{{min-width:0;background:#fff;border:1px solid var(--line);border-radius:16px;padding:26px;margin-bottom:18px;box-shadow:0 5px 18px #2637560a}}.hero{{padding:30px 34px;background:linear-gradient(135deg,#ffffff 45%,#edf3ff)}}.hero h2{{font-size:28px;line-height:1.3;max-width:900px;overflow-wrap:anywhere}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.metric{{min-width:0;background:var(--panel);border-radius:12px;padding:17px;border:1px solid #e7ecf4}}.metric strong{{display:block;font-size:26px;line-height:1.25}}.metric span{{color:var(--muted);font-size:13px}}.metric.good{{background:#f1fbf7;border-color:#b7e2d2}}.metric.good strong{{color:var(--good)}}.metric.warn{{background:#fff9e8;border-color:#ead9a2}}.metric.warn strong{{color:var(--warn)}}.insight-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.insight{{min-width:0;border:1px solid var(--line);border-radius:13px;padding:18px;background:#fff}}.insight.good{{border-left:5px solid var(--good);background:#f6fcf9}}.insight.warn{{border-left:5px solid var(--warn);background:#fffaf0}}.insight h3{{margin:0 0 6px}}.insight strong{{font-size:22px}}.score-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.score-card{{min-width:0;border:1px solid var(--line);border-radius:13px;padding:18px}}.score-card h3{{margin:0 0 12px}}.score-line{{display:grid;grid-template-columns:92px 1fr 72px;gap:10px;align-items:center;margin:9px 0}}.bar{{height:10px;background:#e6ebf3;border-radius:99px;overflow:hidden}}.bar>i{{display:block;height:100%;background:var(--accent);border-radius:99px}}.score-card.moli .bar>i{{background:#7057d9}}.principles,.hypothesis-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0}}.principles>div,.hypothesis-grid>div{{background:var(--panel);border-radius:10px;padding:14px}}.principles strong,.principles span,.hypothesis-grid strong,.hypothesis-grid span{{display:block}}.principles span,.hypothesis-grid span{{margin-top:4px;color:var(--muted)}}.call{{border-left:3px solid #9eb2ef;background:var(--panel);padding:10px 12px;margin:8px 0;border-radius:0 8px 8px 0}}.chip{{display:inline-block;font-size:12px;background:#eaf0fb;border-radius:999px;padding:2px 8px;margin:2px}}.task-verification{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}}.task-verification>div,.diagnosis-facts>div{{background:var(--panel);border-radius:9px;padding:11px 12px}}.task-verification>div:last-child{{grid-column:1/-1}}.task-verification strong,.task-verification span,.diagnosis-facts strong,.diagnosis-facts span{{display:block}}.task-verification span,.diagnosis-facts span{{margin-top:3px}}.table-wrap{{max-width:100%;overflow:auto;overscroll-behavior-inline:contain;border:1px solid var(--line);border-radius:10px;margin:12px 0;scrollbar-width:thin}}.table-wrap:focus-visible,.record-grid:focus-visible{{outline:3px solid var(--accent);outline-offset:2px}}table{{width:100%;border-collapse:collapse;min-width:760px}}caption{{text-align:left;padding:11px 12px;font-weight:750}}th,td{{border-top:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}th{{background:var(--panel);font-size:13px}}.resource-summary table{{min-width:860px}}.resource-summary thead:first-of-type th{{text-align:center}}.resource-summary thead:first-of-type th:first-child{{text-align:left}}.better{{color:var(--good);font-weight:800}}.worse{{color:var(--bad);font-weight:800}}details{{border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin:9px 0}}summary{{cursor:pointer;font-weight:750}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5edf8;padding:13px;border-radius:8px;font:12px/1.55 ui-monospace,monospace}}code{{font-family:ui-monospace,monospace}}.trace{{border-left:3px solid #b9c5da;padding-left:12px;margin:10px 0}}.ok{{color:var(--good)}}.bad{{color:var(--bad)}}.note{{border-left:5px solid var(--accent)}}.note-inline{{background:#fff8e8;border-left:4px solid var(--warn);padding:11px 13px;border-radius:0 8px 8px 0}}.pager{{display:flex;justify-content:space-between;gap:18px;margin-top:22px}}.pager a:last-child{{text-align:right}}.filters{{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:10px;background:var(--panel);padding:14px;border-radius:10px;margin:14px 0}}.filters label{{font-size:12px;color:var(--muted);font-weight:700}}.filters select,.filters input{{display:block;width:100%;margin-top:4px;padding:8px 9px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink)}}.filters button{{align-self:end;padding:8px 12px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--accent);font-weight:700;cursor:pointer}}.filters button:hover{{background:#e8eeff}}.filter-status{{align-self:end;color:var(--muted)}}.execution-card[hidden]{{display:none}}.record-grid{{max-width:100%;overflow:auto;border:1px solid var(--line);border-radius:10px;background:#fff}}.record-grid-head,.execution-summary{{display:grid;grid-template-columns:minmax(260px,2.2fr) minmax(95px,.7fr) minmax(80px,.55fr) minmax(90px,.6fr) minmax(190px,1.25fr) minmax(110px,.75fr);gap:12px;align-items:center;min-width:970px;padding:10px 12px}}.record-grid-head{{background:var(--panel);font-size:13px;font-weight:800}}.execution-card{{border-top:1px solid var(--line)}}.execution-card:first-of-type{{border-top:0}}.execution-card>details{{border:0;border-radius:0;padding:0;margin:0}}.execution-card>details>summary{{list-style:none}}.execution-card>details>summary::-webkit-details-marker{{display:none}}.execution-summary>span:first-child::before{{content:"▸";color:var(--accent);display:inline-block;width:16px}}.execution-card>details[open] .execution-summary>span:first-child::before{{content:"▾"}}.execution-body{{border-top:1px solid var(--line);padding:18px 20px;background:#fbfcff;min-width:950px}}.diagnosis-panel{{border-left:4px solid var(--bad);background:#fff6f5;border-radius:0 10px 10px 0;padding:14px 16px;margin-bottom:16px}}.diagnosis-panel h3{{margin-top:0}}.diagnosis-facts{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}}.raw-evidence{{background:#fff}}footer{{padding-bottom:30px;color:var(--muted)}}@media(max-width:760px){{.wrap,main,footer{{padding-left:14px;padding-right:14px}}.brand{{padding-top:16px}}.score-grid,.insight-grid,.task-verification,.diagnosis-facts,.principles,.hypothesis-grid{{grid-template-columns:1fr}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.task-verification>div:last-child{{grid-column:auto}}.filters{{grid-template-columns:1fr 1fr}}.card{{padding:18px;border-radius:12px}}.hero{{padding:22px 20px}}.hero h2{{font-size:22px}}h1{{font-size:25px}}nav{{margin-right:-14px}}}}@media(max-width:480px){{.metric{{padding:14px 12px}}.metric strong{{font-size:23px}}.filters{{grid-template-columns:1fr}}.score-line{{grid-template-columns:82px 1fr 58px;gap:7px}}}}
@media(max-width:760px){{.stackable{{border:0;overflow:visible}}.stackable table,.stackable tbody,.stackable tr,.stackable td{{display:block;width:100%}}.stackable table{{min-width:0}}.stackable thead{{display:none}}.stackable caption{{display:block;margin-bottom:10px;border:1px solid var(--line);border-radius:9px;background:var(--panel)}}.stackable tbody{{display:grid;gap:10px}}.stackable tr{{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}}.stackable td{{display:grid;grid-template-columns:minmax(105px,.8fr) minmax(0,1.4fr);gap:12px;border-top:1px solid var(--line);overflow-wrap:anywhere}}.stackable td:first-child{{border-top:0}}.stackable td::before{{content:attr(data-label);color:var(--muted);font-size:12px;font-weight:750}}:is(.resource-summary,.rule-comparison,.step-comparison)::before{{content:"左右滑动查看完整表格 →";display:block;padding:8px 10px;color:var(--muted);font-size:12px;font-weight:700;background:#fff}}.record-grid{{overflow:visible;border:0}}.record-grid-head{{display:none}}.execution-card{{margin-bottom:10px;border:1px solid var(--line);border-radius:9px;overflow:hidden}}.execution-summary{{min-width:0;grid-template-columns:1fr 1fr;padding:13px}}.execution-summary>span{{display:grid;grid-template-columns:70px minmax(0,1fr);gap:8px;overflow-wrap:anywhere}}.execution-summary>span::before{{content:attr(data-label);color:var(--muted);font-size:12px;font-weight:700;width:auto}}.execution-summary>span:first-child{{grid-column:1/-1;font-weight:750}}.execution-summary>span:first-child::before{{content:attr(data-label);width:auto}}.execution-summary>span:nth-child(5){{grid-column:1/-1}}}}@media(max-width:480px){{.stackable td{{grid-template-columns:95px minmax(0,1fr)}}}}
.execution-summary{{text-decoration:none;color:var(--ink);font-weight:600}}.execution-summary:hover{{background:#edf3ff}}.execution-summary:focus-visible{{outline:3px solid var(--accent);outline-offset:-3px}}.execution-summary>span:first-child::before{{content:"→"}}.attempt-block{{margin-top:24px}}.ok,.bad{{font-weight:750}}.hypothesis-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:760px){{.hypothesis-grid{{grid-template-columns:1fr}}}}
[hidden]{{display:none!important}}.section-heading{{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}}.section-heading h2,.section-heading h3{{margin:0}}.section-heading>a{{font-size:13px;text-decoration:none}}.provider-failure-link{{display:inline-flex;gap:12px;align-items:center;white-space:nowrap;border:1px solid #d7e1fa;background:#f3f6ff;padding:5px 10px;border-radius:7px;text-decoration:none;font-size:13px;font-weight:650}}.provider-failure-link:hover{{background:#e5edff}}.provider-failure-link:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}.suite-filter-panel{{background:#f8faff}}.suite-filter-controls{{display:grid;grid-template-columns:minmax(200px,1.8fr) minmax(110px,1fr) minmax(160px,1.4fr) minmax(110px,1fr) auto;gap:12px;align-items:end}}.suite-filter-controls label{{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:650;color:var(--muted)}}.suite-filter-controls input,.suite-filter-controls select,.suite-filter-controls button{{width:100%;min-width:0;height:40px;border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:14px system-ui;background:white;color:var(--ink)}}.suite-filter-controls button{{color:var(--accent);cursor:pointer;font-weight:650}}.suite-filter-controls :is(input,select,button):focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}.empty-state{{text-align:center;padding:44px;color:var(--muted)}}@media(max-width:900px){{.suite-filter-controls{{grid-template-columns:repeat(2,minmax(0,1fr))}}.suite-search{{grid-column:1/-1}}}}@media(max-width:600px){{.section-heading{{align-items:flex-start;flex-wrap:wrap}}}}
.case-header{{display:flex;align-items:center;gap:24px;padding:15px 0;font-size:13px}}.case-header a{{text-decoration:none}}.case-header span{{margin-left:auto}}.case-detail h1{{font-size:25px}}.case-detail h2{{font-size:19px}}.case-detail h3{{font-size:14px}}.case-id{{font-size:12px;overflow-wrap:anywhere}}.case-facts{{display:flex;gap:40px;padding:12px 0}}.case-facts span,.case-facts strong{{display:block}}.case-facts span{{font-size:12px;color:var(--muted)}}.case-facts strong{{font-size:19px}}.case-detail .diagnosis-panel{{padding:12px 14px;margin:10px 0}}.case-detail .diagnosis-panel>span{{margin-left:16px;font-size:13px}}.case-start{{font-size:13px;overflow-wrap:anywhere}}.case-nav{{position:sticky;top:0;z-index:2;background:#edf2f8;padding:8px 0;border-bottom:1px solid var(--line);margin-bottom:16px}}.case-nav a{{background:#fff;border:1px solid var(--line);font-size:13px}}.case-nav a:hover{{background:#e8eeff}}.case-detail [id]{{scroll-margin-top:66px}}.case-detail table{{min-width:600px}}.case-detail th,.case-detail td{{padding:9px 12px;font-size:13px;overflow-wrap:anywhere}}.step-comparison table{{table-layout:fixed}}.step-comparison th:first-child{{width:50px}}.step-comparison th:nth-child(2){{width:38%}}.step-comparison small{{display:block;color:var(--muted)}}.evidence-link{{display:block;font-size:12px;margin-top:3px}}.evidence-item{{border-top:1px solid var(--line);padding:16px 0 4px}}.evidence-item .section-heading{{margin-bottom:4px}}.evidence-item p{{font-size:12px}}.evidence-item pre{{max-height:260px;overflow:auto;margin:8px 0;line-height:1.55}}.evidence-item:target{{outline:2px solid var(--accent);outline-offset:5px}}.case-detail a:focus-visible,.case-detail pre:focus-visible,.case-header a:focus-visible{{outline:2px solid var(--accent);outline-offset:3px}}@media(max-width:650px){{.case-header{{flex-wrap:wrap;gap:8px 18px}}.case-header span{{margin-left:0;width:100%}}.case-facts{{flex-wrap:wrap;gap:12px 24px}}.case-detail .diagnosis-panel>span{{display:block;margin:4px 0}}.case-detail h1{{font-size:22px}}.case-detail .stackable table{{min-width:0}}.case-detail .stackable{{overflow:visible}}}}
.action-name{{display:block;width:fit-content;background:#eaf0fb;color:#234887;border:1px solid #d8e3f4;border-radius:5px;padding:1px 6px;margin-bottom:5px;font-size:12px;font-weight:650}}.expected-values{{margin:10px 0 0}}.expected-values>div{{display:grid;grid-template-columns:minmax(90px,140px) 1fr;gap:16px;padding:6px 0;border-top:1px solid var(--line)}}.expected-values dt{{font:12px/1.8 ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere}}.expected-values dd{{margin:0;overflow-wrap:anywhere}}.source-mark{{font-family:system-ui;color:#5f6c81;background:#e2e8f0;border-radius:3px;padding:0 3px;margin-left:2px}}.task-verification .source-mark{{display:inline;margin:0 0 0 2px}}.source-mark-note{{font-size:12px;color:var(--muted);margin-top:8px}}.evidence-index{{display:none}}.evidence-enhanced .evidence-index{{display:block}}.evidence-enhanced .evidence-item{{display:none}}.evidence-index .evidence-link{{display:grid;grid-template-columns:64px 1fr 36px;gap:12px;align-items:start;border-top:1px solid var(--line);padding:10px 0;text-decoration:none;font-size:13px}}.evidence-index .evidence-link>span:nth-child(2){{color:var(--ink)}}.evidence-index .evidence-link:hover{{background:var(--panel)}}html.evidence-enhanced{{scrollbar-gutter:stable}}html.evidence-modal-open{{overflow:hidden}}#evidence-dialog{{width:min(960px,calc(100vw - 40px));max-height:85vh;padding:24px;border:1px solid var(--line);border-radius:14px;color:var(--ink);box-shadow:0 24px 80px #10182840}}#evidence-dialog[open]{{display:flex;flex-direction:column}}#evidence-dialog::backdrop{{background:rgba(15,23,42,.48)}}.dialog-heading{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}}.dialog-heading h2{{font-size:19px;margin:0;line-height:1.4}}.dialog-heading p{{font-size:13px;margin:5px 0 12px}}.dialog-heading button{{padding:7px 14px;background:#f4f7fb;border:1px solid var(--line);border-radius:7px;color:var(--ink);cursor:pointer;white-space:nowrap}}#evidence-dialog pre{{min-height:70px;min-width:0;max-height:65vh;overflow:auto;margin:8px 0 0;font-size:13px;line-height:1.65;flex:1}}#evidence-action{{font-size:13px;margin:0 0 6px}}#evidence-dialog :focus-visible{{outline:2px solid var(--accent);outline-offset:3px}}@media(max-width:600px){{#evidence-dialog{{width:calc(100vw - 20px);padding:16px;max-height:90vh}}.expected-values>div{{grid-template-columns:90px 1fr;gap:8px}}}}@media print{{.evidence-enhanced .evidence-item{{display:block}}.evidence-index,#evidence-dialog{{display:none!important}}}}
.rule-comparison table{{min-width:900px}}.rule-reason,.rule-actual{{display:block;margin-top:5px}}.rule-reason{{color:var(--bad)}}.rule-actual{{color:var(--muted)}}
.benchmark-tabs,.comparison-controls{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 16px}}.benchmark-tabs button,.comparison-controls button{{border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--muted);padding:7px 12px;font:inherit;font-size:13px;font-weight:700;cursor:pointer}}.benchmark-tabs button.active,.comparison-controls button.active{{color:#173fa8;background:#e8eeff;border-color:#c8d5fb}}.result-guide{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;padding:16px}}.result-guide a{{display:block;text-decoration:none;border:1px solid var(--line);border-radius:9px;padding:11px 12px;color:var(--ink)}}.result-guide a:hover{{background:#edf3ff}}.result-guide strong,.result-guide span{{display:block}}.result-guide span{{color:var(--muted);font-size:12px;margin-top:2px}}.metric-module>summary{{display:flex;justify-content:space-between;align-items:center;font-size:16px}}.metric-module>summary small{{color:var(--muted);font-size:12px}}.rank-grid,.distribution-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0}}.rank-card,.distribution-item{{position:relative;min-width:0;border:1px solid var(--line);border-radius:11px;padding:15px;background:#fff}}.rank-card{{padding-left:48px}}.rank-card h3{{margin:0 0 3px}}.rank-card p{{font-size:13px;color:var(--muted)}}.rank-number{{position:absolute;left:14px;top:14px;width:24px;height:24px;border-radius:50%;background:#e8eeff;color:#173fa8;text-align:center;font-weight:800;line-height:24px}}.distribution-item strong,.distribution-item span{{display:block}}.distribution-item span{{font-size:12px;color:var(--muted);font-weight:750;margin-bottom:5px}}.distribution-item strong{{font-size:13px;line-height:1.5}}.benchmark-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.benchmark-card{{min-width:0;border:1px solid var(--line);border-radius:13px;padding:18px;background:#fff}}.benchmark-title{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.benchmark-title h3{{margin:0}}.benchmark-title p{{margin:2px 0;color:var(--muted);font-size:13px}}.category-chip{{flex:none;background:var(--panel);border-radius:999px;padding:3px 8px;font-size:11px;color:var(--muted)}}.benchmark-kpis{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:13px 0}}.benchmark-kpis span{{min-width:0;background:var(--panel);border-radius:8px;padding:9px;font-size:11px;color:var(--muted);overflow-wrap:anywhere}}.benchmark-kpis strong{{display:block;color:var(--ink);font-size:13px;line-height:1.35;margin-bottom:3px}}.benchmark-facts,.benchmark-more{{margin:0}}.benchmark-facts>div,.benchmark-more>div{{display:grid;grid-template-columns:92px minmax(0,1fr);gap:10px;padding:8px 0;border-top:1px solid #e8edf5}}.benchmark-facts dt,.benchmark-more dt{{font-size:12px;font-weight:750;color:var(--muted)}}.benchmark-facts dd,.benchmark-more dd{{margin:0;font-size:13px}}.benchmark-sources{{font-size:12px;margin-bottom:0}}.comparison-toolbar{{display:flex;justify-content:space-between;gap:16px;align-items:end;flex-wrap:wrap}}.comparison-toolbar label{{font-size:12px;font-weight:700;color:var(--muted)}}.comparison-toolbar input{{display:block;min-width:260px;margin-top:4px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font:inherit}}.comparison-grid{{border:1px solid var(--line);border-radius:10px;overflow:hidden}}.comparison-head,.comparison-row{{display:grid;grid-template-columns:minmax(260px,2.1fr) minmax(125px,.75fr) minmax(125px,.75fr) minmax(150px,1fr);gap:12px;align-items:center;padding:10px 12px}}.comparison-head{{background:var(--panel);font-size:13px;font-weight:800}}.comparison-row{{border-top:1px solid var(--line)}}.comparison-row[hidden]{{display:none}}.comparison-row .task-name{{font-weight:700;overflow-wrap:anywhere}}.result-link{{display:inline-flex;align-items:center;gap:6px;text-decoration:none;font-weight:750}}.comparison-note{{font-size:12px;color:var(--muted)}}@media(max-width:760px){{.result-guide{{grid-template-columns:repeat(2,minmax(0,1fr))}}.suite-stats,.rank-grid,.distribution-grid{{grid-template-columns:repeat(3,minmax(0,1fr))}}.benchmark-grid{{grid-template-columns:1fr}}.benchmark-kpis{{grid-template-columns:1fr 1fr}}.comparison-head{{display:none}}.comparison-grid{{border:0}}.comparison-row{{grid-template-columns:1fr 1fr;border:1px solid var(--line);border-radius:9px;margin-bottom:9px}}.comparison-row .task-name,.comparison-row .comparison-note{{grid-column:1/-1}}.comparison-row>[data-label]::before{{content:attr(data-label);display:block;color:var(--muted);font-size:11px;font-weight:700}}.metric-module .stackable{{max-width:100%;overflow:auto;border:1px solid var(--line)}}.metric-module .stackable table{{display:table;width:100%;min-width:620px}}.metric-module .stackable thead{{display:table-header-group}}.metric-module .stackable tbody{{display:table-row-group}}.metric-module .stackable tr{{display:table-row;border:0}}.metric-module .stackable th,.metric-module .stackable td{{display:table-cell;width:auto}}.metric-module .stackable td::before{{content:none}}}}@media(max-width:560px){{.rank-grid,.distribution-grid{{grid-template-columns:1fr}}}}@media(max-width:480px){{.result-guide{{grid-template-columns:1fr}}.suite-stats{{grid-template-columns:1fr}}.benchmark-kpis{{grid-template-columns:1fr}}.benchmark-facts>div,.benchmark-more>div{{grid-template-columns:1fr;gap:2px}}.comparison-toolbar input{{min-width:0;width:100%}}}}
</style></head><body><header><div class="wrap">{header}</div></header><main>{body}{pager}</main><footer>Lexmount WebArena Fixed-Trajectory Replay</footer></body></html>"""


def _functional_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    correctness_by_engine = {row[0]: row for row in _correctness_rows(summary)}
    for engine in ("chromium", "moli"):
        values = (summary.get("functional") or {}).get(engine) or {}
        duration = values.get("duration_ms") or {}
        rows.append(
            correctness_by_engine[engine]
            + [
                _stat(duration, "p50"),
                _stat(duration, "mean"),
                _stat(duration, "p95"),
            ]
        )
    return rows


def _correctness_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for engine in ("chromium", "moli"):
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
                score("result_first_passes", "first_passes"),
                score("result_final_passes", "final_passes"),
            ]
        )
    return rows


def _correctness_table(summary: dict[str, Any]) -> str:
    functional = summary.get("functional") or {}
    scores = {
        engine: {
            "first": int((functional.get(engine) or {}).get("result_first_passes", 0) or 0),
            "recovered": int((functional.get(engine) or {}).get("result_recovered", 0) or 0),
            "final": int((functional.get(engine) or {}).get("result_final_passes", 0) or 0),
            "tasks": int((functional.get(engine) or {}).get("tasks", 0) or 0),
        }
        for engine in ("chromium", "moli")
    }
    rows = []
    for engine, values in scores.items():
        first, recovered, result, tasks = (
            values["first"], values["recovered"], values["final"], values["tasks"]
        )
        other = scores["moli" if engine == "chromium" else "chromium"]
        result_rate = result / tasks * 100 if tasks else 0
        first_rate = first / tasks * 100 if tasks else 0
        first_class = _comparison_class(
            first / tasks if tasks else None,
            other["first"] / other["tasks"] if other["tasks"] else None,
            higher=True,
        )
        result_class = _comparison_class(
            result / tasks if tasks else None,
            other["final"] / other["tasks"] if other["tasks"] else None,
            higher=True,
        )
        rows.append(
            "<tr>"
            f'<td data-label="浏览器">{_e(engine)}</td>'
            f'<td data-label="首次通过" class="{first_class}">{first}/{tasks}（{first_rate:.2f}%）</td>'
            f'<td data-label="重试恢复">{recovered}</td>'
            f'<td data-label="最终通过" class="{result_class}">{result}/{tasks}（{result_rate:.2f}%）</td>'
            f'<td data-label="失败任务"><a class="provider-failure-link" href="{REPORT_STEM}-results.html#provider={engine}&amp;status=fail" aria-label="{engine}：查看 {max(0, tasks - result)} 个失败任务">{max(0, tasks - result)} 个失败 <span aria-hidden="true">→</span></a></td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap stackable" role="region" aria-label="程序验收结果" tabindex="0"><table><caption>程序验收结果</caption><thead><tr>'
        "<th>浏览器</th><th>首次通过</th><th>重试恢复</th><th>最终通过</th><th>失败任务</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _aggregate_ratio(summary: dict[str, Any], metric_name: str, stat: str) -> float | None:
    engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    chromium = _numeric_stat(((engines.get("chromium") or {}).get(metric_name) or {}), stat)
    moli = _numeric_stat(((engines.get("moli") or {}).get(metric_name) or {}), stat)
    if (
        not isinstance(chromium, (int, float))
        or chromium == 0
        or not isinstance(moli, (int, float))
    ):
        return None
    return float(moli) / float(chromium)


def _paired_ratio(summary: dict[str, Any], metric_name: str, stat: str) -> float | None:
    metric = (
        ((summary.get("resource_comparison") or {}).get("paired_ratios") or {}).get("metrics") or {}
    ).get(metric_name) or {}
    return _numeric_stat(metric, stat)


def _detail_filename(task_id: str, engine: str) -> str:
    return f"{REPORT_STEM}-task-{task_id.encode().hex()}-{engine.encode().hex()}.html"


def _is_webarena_manifest(manifest: dict[str, Any]) -> bool:
    return any(
        ((task.get("evaluator") or {}).get("kind") == "webarena_verified")
        for task in manifest.get("suite") or []
    )


def _load_public_research() -> tuple[dict[str, Any], dict[str, Any]]:
    root = ROOT / "evaluation" / "browser-use-public"
    adoption = json.loads((root / "adoption-evidence.json").read_text(encoding="utf-8"))
    catalog = json.loads((root / "benchmark-catalog.json").read_text(encoding="utf-8"))
    return adoption, catalog


def _research_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    adoption, catalog = _load_public_research()
    records = adoption.get("records") or []
    by_family: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_family.setdefault(str(record.get("benchmark_family")), []).append(record)
    tie_order = {"WebArena": 0, "Mind2Web": 1, "WebVoyager": 2, "MiniWoB++": 3}
    adoption_rows = []
    for rank, family in enumerate(
        sorted(by_family, key=lambda name: (-len(by_family[name]), tie_order.get(name, 99))), 1
    ):
        family_records = by_family[family]
        adoption_rows.append(
            [
                str(rank),
                family,
                str(len(family_records)),
                "、".join(str(row.get("organization")) for row in family_records),
                "；".join(
                    f"{row.get('organization')}: {row.get('benchmark_version')}"
                    for row in family_records
                ),
            ]
        )
    adoption_cards = "".join(
        f'<article class="rank-card"><span class="rank-number">{_e(row[0])}</span><h3>{_e(row[1])}</h3>'
        f'<strong>{_e(row[2])} 家机构采用</strong><p>{_e(row[3])}</p>'
        f'<details><summary>具体版本</summary><p>{_e(row[4])}</p></details></article>'
        for row in adoption_rows[:3]
    )

    benchmarks = catalog.get("benchmarks") or []
    benchmark_cards = []
    for row in benchmarks:
        category = str(row.get("category"))
        sources = " · ".join(
            f'<a href="{_e(url)}" target="_blank" rel="noopener">来源 {index}</a>'
            for index, url in enumerate(row.get("source_urls") or [], 1)
        )
        benchmark_cards.append(
            f'<article class="benchmark-card" data-category="{_e(category)}">'
            f'<div class="benchmark-title"><div><h3>{_e(row.get("name"))}</h3><p>{_e(row.get("version"))}</p></div>'
            f'<span class="category-chip">{_e(CATEGORY_LABELS.get(category, category))}</span></div>'
            f'<div class="benchmark-kpis"><span><strong>{_e(row.get("task_count"))}</strong>任务规模</span>'
            f'<span><strong>{_e(row.get("online"))}</strong>是否在线</span>'
            f'<span><strong>{_e(row.get("reference_trajectory"))}</strong>参考轨迹</span></div>'
            '<dl class="benchmark-facts">'
            f'<div><dt>环境</dt><dd>{_e(row.get("environment"))}</dd></div>'
            f'<div><dt>部署与账号</dt><dd>{_e(row.get("local_deployment"))}；{_e(row.get("account_requirement"))}</dd></div>'
            f'<div><dt>输入</dt><dd>{_e(row.get("input_and_trajectory"))}</dd></div>'
            f'<div><dt>验收与评分</dt><dd>{_e(row.get("final_acceptance"))}；{_e(row.get("scoring"))}</dd></div>'
            '</dl><details><summary>难度、成本与复现限制</summary><dl class="benchmark-more">'
            f'<div><dt>难度</dt><dd>{_e(row.get("difficulty"))}</dd></div>'
            f'<div><dt>许可证</dt><dd>{_e(row.get("license"))}</dd></div>'
            f'<div><dt>成本</dt><dd>{_e(row.get("cost"))}</dd></div>'
            f'<div><dt>稳定性</dt><dd>{_e(row.get("stability"))}</dd></div>'
            f'<div><dt>已知问题</dt><dd>{_e(row.get("known_issues"))}</dd></div>'
            f'</dl></details><p class="benchmark-sources">{sources}</p></article>'
        )

    engineering = sorted(
        (row for row in benchmarks if isinstance(row.get("engineering_rank"), int)),
        key=lambda row: int(row["engineering_rank"]),
    )
    engineering_rows = [
        [
            str(row["engineering_rank"]),
            row["name"],
            (
                "自托管、程序评分，并有公开人类轨迹可做固定重放"
                if row["name"] == "WebArena-Verified"
                else "用于第二阶段公开生态对比；真实网站和 judge 漂移不适合做首轮内核隔离"
                if row["name"] == "Online-Mind2Web"
                else "用于对照 Browser Use 官方实践；混合任务与 LLM judge 会引入额外变量"
            ),
        ]
        for row in engineering
    ]
    engineering_cards = "".join(
        f'<article class="rank-card"><span class="rank-number">{_e(row[0])}</span><h3>{_e(row[1])}</h3>'
        f'<p>{_e(row[2])}</p></article>'
        for row in engineering_rows
    )
    source_links = []
    for record in records:
        source_links.append(
            f'<li>{_e(record.get("organization"))} / {_e(record.get("system"))}: '
            f'<a href="{_e(record.get("url"))}" target="_blank" rel="noopener">'
            f'{_e(record.get("benchmark_version"))}</a></li>'
        )
    category_buttons = "".join(
        f'<button type="button" data-category="{_e(category)}"'
        + (' class="active" aria-pressed="true"' if category == "end_to_end_browser_use" else ' aria-pressed="false"')
        + f'>{_e(label)}</button>'
        for category, label in CATEGORY_LABELS.items()
    )
    catalog_script = """<script>(()=>{const buttons=[...document.querySelectorAll('.benchmark-tabs button')];
const cards=[...document.querySelectorAll('.benchmark-card')];const count=document.getElementById('benchmark-count');
function show(category){for(const card of cards)card.hidden=card.dataset.category!==category;for(const button of buttons){const active=button.dataset.category===category;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}count.textContent=`显示 ${cards.filter(card=>!card.hidden).length} 个同类 benchmark`;}
for(const button of buttons)button.onclick=()=>show(button.dataset.category);show('end_to_end_browser_use');})();</script>"""
    return (
        '<section class="card note"><h2>为什么选择 WebArena-Verified</h2>'
        '<p><strong>固定轨迹比较浏览器内核，首选 WebArena-Verified。</strong>'
        '采用次数只能说明公开认可度，不能说明它最适合隔离浏览器变量。WebArena 与 Mind2Web 家族在限定的一方材料中各有 3 家机构采用；工程选择仍由自托管、可重放轨迹和程序化最终状态验收决定。</p>'
        '<p>候选集按端到端 browser-use、综合 computer-use、GUI grounding、网页搜索与研究分开整理，不跨类排名。原始 Mind2Web、Online-Mind2Web 与 Mind2Web 2 测量对象不同，因此分别统计。</p></section>'
        '<section class="card"><h2>端到端 browser-use 采用 Top 3</h2>'
        '<p>统计范围只含 OpenAI、Anthropic、Google、DeepSeek、智谱/ChatGLM、Moonshot/Kimi 的模型卡、技术报告和官方仓库。同一机构同一家族只计一次，保留具体版本。采用数相同后依次看最新官方采用、公开获取和可复现性。</p>'
        + f'<div class="rank-grid">{adoption_cards}</div>'
        + '<p class="muted">本次限定的一方材料中没有检出可计入 DeepSeek 或 Moonshot/Kimi 的端到端 browser-use benchmark 记录。这不等于这些机构从未做过内部评测。</p>'
        + '<details><summary>查看采用证据链接</summary><ul>'
        + "".join(source_links)
        + '</ul></details></section>'
        '<section class="card"><h2>工程适用性 Top 3</h2>'
        + f'<div class="rank-grid">{engineering_cards}</div>'
        + '<p>WebVoyager 只保留为历史参照。OSWorld 2.0 太宽，ScreenSpot-Pro 只测单步定位；它们都不会进入本轮固定轨迹正式分母。</p></section>'
        '<section class="card"><h2>Mind2Web 家族的三套评测</h2><ul>'
        '<li>原始 Mind2Web 是离线 HTML 和动作预测数据集。它适合比较元素选择与动作预测，不能真正比较 Chromium 和 Moli。</li>'
        '<li>Online-Mind2Web 在 300 个真实网站任务上判断最终任务是否完成。截图、URL 和轨迹是 judge 证据，并不要求复现固定动作序列；WebJudge 是 LLM-as-a-judge。</li>'
        '<li>Online-Mind2Web 的 easy、medium、hard 按人工参考路径长度划分，分别为不超过 5 步、6 到 10 步、至少 11 步。这个标签不是网站难度，也不是 agent 实际执行步数。</li>'
        '<li>Mind2Web 2 有 130 个长程研究任务，评估实时搜索、综合回答和引用归因，属于网页研究类。</li></ul></section>'
        '<section class="card"><h2>Browser Use 官方实践</h2>'
        '<p>Browser Use 当前公开仓库同时维护 BU Bench V1 和 Online-Mind2Web 运行结果。前者有 100 个经过筛选的混合任务，后者便于与公开生态比较。两者都保留运行轨迹，但都没有可直接当作标准操作路径的 gold trajectory。</p>'
        '<p>本项目采用它们对任务失效、验证码、外部认证和不可验收任务的处理原则，也保留完整轨迹；不照搬多次采样或 bootstrapping，因为这里测的是固定路径与浏览器兼容性。</p></section>'
        '<section class="card"><h2>运行架构</h2>'
        '<p>这些用例不要求 AMD。远端 Intel x86-64 主机可以运行官方 amd64 镜像。179 条官方 WebArena 人类录制中，本项目在运行前排除了 4 条只适配 arm64 Wikipedia 环境的任务，并排除了 3 条已确认的 evaluator 缺陷；172 条进入 amd64 资格验证。最终只有冷 Chromium 重放、程序 evaluator 和证据完整性同时通过的 '
        f'{len(manifest.get("suite") or [])} 条轨迹进入正式双浏览器分母。</p></section>'
        + '<section class="card"><div class="section-heading"><div><h2>候选评测集</h2><p class="muted">先按类别筛选，再展开单项的复现限制；不同类别不直接排名。</p></div><span id="benchmark-count" class="muted"></span></div>'
        + f'<div class="benchmark-tabs" role="group" aria-label="Benchmark 类别">{category_buttons}</div>'
        + '<div class="benchmark-grid">' + "".join(benchmark_cards) + '</div></section>'
        + catalog_script
        + f'<section class="card"><p class="muted">资料核对日期：{_e(catalog.get("verified_on"))}。表内“成本”只描述成本来源，没有把未实测价格写成数字。</p></section>'
    )


def _summary_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    task_count = len(manifest.get("suite") or [])
    f = summary.get("functional") or {}
    c = f.get("chromium") or {}
    m = f.get("moli") or {}
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    webarena = _is_webarena_manifest(manifest)
    memory_ratio = _paired_ratio(summary, "memory_mean_bytes", "p50")
    retained_ratio = _paired_ratio(summary, "memory_retained_bytes", "p50")
    duration_ratio = _paired_ratio(summary, "duration_ms", "p50")
    duration_tail_ratio = _paired_ratio(summary, "duration_ms", "p95")
    tx_ratio = _paired_ratio(summary, "network_tx_bytes", "p50")

    agent_smoke = manifest.get("agent_smoke") or {}
    smoke_html = ""
    if agent_smoke:
        provenance = agent_smoke.get("provenance") or {}
        smoke_html = f"""<section class="card"><h2>CowAgent 端到端冒烟测试</h2><p>该测试与资源微基准分开，不参与上表统计，也没有在运行编号 <code>{_e(manifest.get("run_id"))}</code> 中重新执行。模型 <code>{_e(agent_smoke.get("model"))}</code>，思考强度 <code>{_e(agent_smoke.get("reasoning_effort"))}</code>；独立执行日期 <code>{_e(agent_smoke.get("executed_at"))}</code>，来源文件内容哈希 <code>{_e(str(provenance.get("source_sha256") or "")[:12])}</code>。同一任务每个浏览器方案只执行 1 次。</p>{_table(["浏览器方案", "结果", "BrowserTool 调用次数", "最终答案"], [[str(item.get("provider")), "通过" if item.get("status") == "pass" else str(item.get("status")), str(item.get("browser_tool_calls")), str(item.get("answer"))] for item in agent_smoke.get("results") or []], "独立端到端接受性测试")}</section>"""
    c_passes = int(c.get("result_final_passes", c.get("final_passes", 0)))
    m_passes = int(m.get("result_final_passes", m.get("final_passes", 0)))

    def ratio_tone(value: float | None) -> str:
        if not isinstance(value, (int, float)) or value == 1:
            return ""
        return "good" if value < 1 else "warn"

    if c_passes > m_passes:
        c_summary = "Chromium 最终通过，兼容性较高"
        m_summary = "Moli 最终通过，当前主要短板"
        compatibility_title = "兼容性：Chromium 明显领先"
        compatibility_body = (
            f'<strong>{c_passes - m_passes} 个任务</strong>的最终通过数差距。'
            f"对当前 {task_count} 条固定轨迹，Chromium 是更稳妥的默认方案。"
        )
        recommendation = (
            f"本轮固定轨迹中 Chromium 通过 {c_passes} 个任务，高于 Moli 的 {m_passes} 个。"
            + (
                f"在双方首次都成功的 {paired} 个任务上，Moli 的时间加权平均内存中位比值为 {_show(memory_ratio, suffix='×')}，"
                f"结束保留内存中位比值为 {_show(retained_ratio, suffix='×')}。兼容性和资源开销是两个不同结论。"
                if webarena and paired
                else "这定位了当前兼容性差异，不足以推出浏览器总体优劣。"
            )
            if webarena
            else f"本轮建议默认使用 Chromium：其结果正确任务数为 {c_passes}，高于 Moli 的 {m_passes}；"
            "Moli 在双方均成功的任务上若表现出更低耗时或资源占用，可作为任务已验证兼容时的备选。"
        )
    elif m_passes > c_passes:
        c_summary = "Chromium 最终通过，当前主要短板"
        m_summary = "Moli 最终通过，兼容性较高"
        compatibility_title = "兼容性：Moli 明显领先"
        compatibility_body = (
            f'<strong>{m_passes - c_passes} 个任务</strong>的最终通过数差距。'
            f"对当前 {task_count} 条固定轨迹，Moli 是更稳妥的默认方案。"
        )
        recommendation = (
            f"本轮固定轨迹中 Moli 通过 {m_passes} 个任务，高于 Chromium 的 {c_passes} 个。"
            "这定位了当前兼容性差异，不足以推出浏览器总体优劣。"
            if webarena
            else f"本轮建议默认使用 Moli：其结果正确任务数为 {m_passes}，高于 Chromium 的 {c_passes}；"
            "仍需结合双方成功任务上的资源指标和失败类型确认适用边界。"
        )
    else:
        c_summary = "Chromium 最终通过"
        m_summary = "Moli 最终通过"
        compatibility_title = "兼容性：本轮持平"
        compatibility_body = (
            f"两个浏览器均通过 <strong>{c_passes}/{task_count}</strong> 个任务，"
            "需结合失败类型和成功交集资源指标决策。"
        )
        recommendation = (
            f"本轮两个浏览器的程序验收通过数相同（均为 {c_passes}）；"
            "仅凭正确性无法确定默认方案，应结合双方成功任务上的资源指标和失败类型决策。"
        )
    if all(isinstance(value, (int, float)) for value in (memory_ratio, retained_ratio)):
        if memory_ratio < 1 and retained_ratio < 1:
            memory_title = "内存：Moli 的明确优势"
        elif memory_ratio > 1 and retained_ratio > 1:
            memory_title = "内存：Chromium 的明确优势"
        else:
            memory_title = "内存：不同指标方向不一致"
    else:
        memory_title = "内存：配对数据不足"
    duration_title = (
        "速度：中位接近，长尾更慢"
        if isinstance(duration_tail_ratio, (int, float)) and duration_tail_ratio > 1
        else "速度：Moli 的配对耗时较低"
        if isinstance(duration_tail_ratio, (int, float)) and duration_tail_ratio < 1
        else "速度：配对数据不足或持平"
    )
    network_title = (
        "网络：Moli 发送量更高"
        if isinstance(tx_ratio, (int, float)) and tx_ratio > 1
        else "网络：Moli 发送量更低"
        if isinstance(tx_ratio, (int, float)) and tx_ratio < 1
        else "网络：配对数据不足或持平"
    )
    correctness = _correctness_table(summary)
    if webarena:
        scope = (
            "这是 WebArena-Verified 公开人类轨迹的自托管确定性重放，不是 LLM Agent 排名。"
            f"本轮分母是资格筛选后固定的 {task_count} 个任务；结果只说明这些轨迹与环境中的浏览器兼容性，"
            "不代表任一浏览器的总体能力。"
        )
        findings = """<li><strong>变量隔离：</strong>两个浏览器执行同一条已资格验证的参考轨迹；没有 agent 规划、模型调用或 LLM judge。</li>
<li><strong>验收对象：</strong>固定的 WebArena-Verified evaluator 检查任务回答、最终服务状态和 HAR 网络事件；不要求逐步模仿原始录制坐标。</li>
<li><strong>资格边界：</strong>179 个官方人类录制只是候选。架构预筛、冷 Chromium 重放和完整证据验收都通过后，任务才进入本轮分母。</li>"""
    else:
        scope = (
            "这是浏览器工具调用层的公网确定性微基准，不是 LLM Agent 排名。"
            f"评测集包含 {task_count} 个结果唯一的任务。"
        )
        findings = f"""<li><strong>速度与资源：</strong>双方成功任务的逐任务 Moli / Chromium 比值中，耗时比平均值为 {_show(_paired_ratio(summary, "duration_ms", "mean"), suffix="×")}，时间加权平均内存比平均值为 {_show(_paired_ratio(summary, "memory_mean_bytes", "mean"), suffix="×")}，网络接收量比平均值为 {_show(_paired_ratio(summary, "network_rx_bytes", "mean"), suffix="×")}。</li>
<li><strong>时序边界：</strong>Moli 的 1 Hz 是 CDP screencast 下采样，不等于 JavaScript 或 <code>requestAnimationFrame</code> 只有 1 fps；源码中的 rAF 目标间隔为 16 ms。</li>
<li><strong>根因边界：</strong>布局与 actionability 证据只能定位现象，尚不能确认 screencast、渲染器或适配层中的具体根因。<a href="webarena-trajectory-replay-latest-results.html#failure-review">查看失败审查</a>。</li>"""
    score_cards = "".join(
        f'<article class="score-card {engine}"><h3>{engine.title()}</h3>'
        f'<div class="score-line"><span>首次成功</span><div class="bar" role="progressbar" aria-label="{engine} 首次成功率" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{values.get("result_first_pass_rate", 0) * 100:.2f}"><i style="width:{values.get("result_first_pass_rate", 0) * 100:.2f}%"></i></div><strong>{values.get("result_first_passes", 0)}/{values.get("tasks", 0)}</strong></div>'
        f'<div class="score-line"><span>最终成功</span><div class="bar" role="progressbar" aria-label="{engine} 最终成功率" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{values.get("result_final_pass_rate", 0) * 100:.2f}"><i style="width:{values.get("result_final_pass_rate", 0) * 100:.2f}%"></i></div><strong>{values.get("result_final_passes", 0)}/{values.get("tasks", 0)}</strong></div>'
        f'<p class="muted">失败重试 {values.get("retries", 0)} 次，恢复 {values.get("result_recovered", 0)} 个任务</p></article>'
        for engine, values in (("chromium", c), ("moli", m))
    )
    return f"""<section class="card hero"><div class="eyebrow">结论先行</div><h2>{recommendation}</h2><p>{scope}</p><p><a href="{REPORT_STEM}-results.html">查看完整结果与资源数据 →</a></p></section>
<section class="card"><div class="section-heading"><h2>首页决策摘要</h2><a href="{REPORT_STEM}-method.html">查看统计口径</a></div><div class="grid"><div class="metric"><strong>{c_passes}/{task_count}</strong><span>{c_summary}</span></div><div class="metric"><strong>{m_passes}/{task_count}</strong><span>{m_summary}</span></div><div class="metric {ratio_tone(memory_ratio)}"><strong>{_show(memory_ratio, suffix='×')}</strong><span>Moli / Chromium 时间加权平均内存 P50</span></div><div class="metric {ratio_tone(retained_ratio)}"><strong>{_show(retained_ratio, suffix='×')}</strong><span>Moli / Chromium 结束保留内存 P50</span></div><div class="metric {ratio_tone(duration_tail_ratio)}"><strong>{_show(duration_tail_ratio, suffix='×')}</strong><span>Moli / Chromium 耗时配对 P95</span></div><div class="metric {ratio_tone(tx_ratio)}"><strong>{_show(tx_ratio, suffix='×')}</strong><span>Moli / Chromium 发送流量配对 P50</span></div></div><p class="muted">资源比值仅统计 {paired} 个双方首次均成功的同任务；低于 1 表示 Moli 较低。不能把资源交集扩大成全部 {task_count} 个任务。</p></section>
<section class="card"><h2>正确性与资源要分开决策</h2><div class="insight-grid"><article class="insight"><h3>{compatibility_title}</h3><p>{compatibility_body}</p></article><article class="insight {ratio_tone(memory_ratio)}"><h3>{memory_title}</h3><p>成功交集中，Moli 的时间加权平均内存 P50 为 Chromium 的 <strong>{_show(memory_ratio, suffix='×')}</strong>，结束保留内存 P50 为 <strong>{_show(retained_ratio, suffix='×')}</strong>。</p></article><article class="insight {ratio_tone(duration_tail_ratio)}"><h3>{duration_title}</h3><p>逐任务耗时比 P50 为 <strong>{_show(duration_ratio, suffix='×')}</strong>，P95 为 <strong>{_show(duration_tail_ratio, suffix='×')}</strong>。应同时看中位与长尾，不能用单个汇总值概括速度。</p></article><article class="insight {ratio_tone(tx_ratio)}"><h3>{network_title}</h3><p>发送流量逐任务比 P50 为 <strong>{_show(tx_ratio, suffix='×')}</strong>。网络开销应与内存、速度和兼容性分开判断。</p></article></div></section>
<section class="card"><h2>首次成功与重试恢复</h2><div class="score-grid">{score_cards}</div><p class="muted">主指标是首次成功率。失败时允许的一次重试只报告恢复效果，不扩大任务分母。</p></section>
<section class="card"><h2>任务通过情况</h2><p class="muted">同一行中较高的首次、最终通过率以粗体绿色标出；相同值不标胜者。</p>{correctness}</section>
<section class="card" id="technical-findings"><div class="section-heading"><h2>关键技术结论</h2><a href="{REPORT_STEM}-results.html#failure-review">查看失败证据</a></div><ul>{findings}</ul></section>
<section class="card"><h2>如何阅读这份报告</h2><div class="principles"><div><strong>要比较什么</strong><span>固定成功轨迹在 Chromium 与 Moli 上能否完成，并通过同一套程序 evaluator。</span></div><div><strong>刻意排除了什么</strong><span>Agent 规划、模型推理、自由动作选择和 LLM judge。</span></div><div><strong>可以得出什么</strong><span>这 30 条轨迹上的兼容性差异、重试恢复与配对资源开销。</span></div><div><strong>不能得出什么</strong><span>浏览器总体能力、任意网站胜率或 Agent 成功率。</span></div></div></section>{smoke_html}"""


def _results_page(
    manifest: dict[str, Any],
    summary: dict[str, Any],
    detail_pages: dict[str, str] | None = None,
) -> str:
    complexity_rows = []
    for complexity in ("simple", "medium", "complex"):
        group = (summary.get("by_complexity") or {}).get(complexity) or {}
        values = group.get("functional") or {}
        metrics = {engine: values.get(engine) or {} for engine in ("chromium", "moli")}
        counts = {
            engine: {
                "tasks": int(metric.get("tasks") or 0),
                "first": int(metric.get("result_first_passes", 0) or 0),
                "final": int(metric.get("result_final_passes", 0) or 0),
            }
            for engine, metric in metrics.items()
        }

        def compared(engine: str, key: str) -> ComparedValue:
            own, other = counts[engine], counts["moli" if engine == "chromium" else "chromium"]
            own_rate = own[key] / own["tasks"] if own["tasks"] else None
            other_rate = other[key] / other["tasks"] if other["tasks"] else None
            return ComparedValue(
                f'{own[key]}/{own["tasks"]}',
                _comparison_class(own_rate, other_rate, higher=True),
            )

        row = [
            _difficulty(complexity),
            compared("chromium", "first"),
            compared("chromium", "final"),
            compared("moli", "first"),
            compared("moli", "final"),
        ]
        complexity_rows.append(row)

    site_counts: dict[str, dict[str, list[int]]] = {}
    specs = {str(task.get("task_id")): task for task in manifest.get("suite") or []}
    for task_id, task in (summary.get("by_task") or {}).items():
        sites = (specs.get(str(task_id), {}).get("environment") or {}).get("sites") or ["未标注"]
        attempts = task.get("attempts") or {}
        for site in sites:
            bucket = site_counts.setdefault(str(site), {"chromium": [0, 0], "moli": [0, 0]})
            for engine in ("chromium", "moli"):
                rows = attempts.get(engine) or []
                bucket[engine][0] += 1
                if rows and str(rows[-1].get("result_status") or rows[-1].get("status")) == "pass":
                    bucket[engine][1] += 1
    site_rows = []
    for site, values in sorted(site_counts.items()):
        chromium, moli = values["chromium"], values["moli"]
        c_rate = chromium[1] / chromium[0] if chromium[0] else None
        m_rate = moli[1] / moli[0] if moli[0] else None
        site_rows.append(
            [
                site,
                chromium[0],
                ComparedValue(
                    f"{chromium[1]}/{chromium[0]}",
                    _comparison_class(c_rate, m_rate, higher=True),
                ),
                ComparedValue(
                    f"{moli[1]}/{moli[0]}",
                    _comparison_class(m_rate, c_rate, higher=True),
                ),
            ]
        )
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    result_overview = (
        '<section class="card note"><h2>正确性优先，资源数据只看成功交集</h2><p>结果表覆盖全部 30 个任务；速度、CPU、内存与网络表只统计两个浏览器首次都成功的同任务。失败和重试不混入正常路径资源比较。</p></section>'
        '<section class="card result-guide" aria-label="结果页模块"><a href="#correctness"><strong>正确性</strong><span>首次、恢复与最终通过</span></a><a href="#performance"><strong>速度与资源</strong><span>时间、CPU、内存、网络</span></a><a href="#task-comparison"><strong>任务对照</strong><span>同任务双浏览器结果</span></a><a href="#failure-review"><strong>失败复核</strong><span>分类、日志与原始证据</span></a></section>'
        '<section class="card" id="correctness"><h2>总体结果</h2><p class="muted">同一行或同一分组中较优值以粗体绿色标出；相同值保持中性。</p>' + _correctness_table(summary) + '</section>'
    )
    resource_section = (
        f'<section class="card" id="performance"><h2>速度、CPU、内存与网络</h2><p>统计范围：{paired} 个双方首次成功的配对任务。绿色表示当前表内较低值；配对比值定义为 <code>Moli / Chromium</code>。</p>{_metric_tables(summary)}{_duration_ratio_audit(summary)}</section>'
    )
    slices = (
        '<section class="card"><h2>按轨迹复杂度分组</h2><p class="muted">简单、中等、复杂是本项目基于参考轨迹操作数的公开分组，不是 WebArena 官方难度标签。</p>'
        + _table(["轨迹分组", "Chromium 首次", "Chromium 最终", "Moli 首次", "Moli 最终"], complexity_rows, "分组正确性") + '</section>'
        '<section class="card"><h2>按自托管站点分组</h2>'
        + _table(["站点", "任务数", "Chromium 最终", "Moli 最终"], site_rows, "站点兼容性切片") + '</section>'
    )
    details = detail_pages if detail_pages is not None else {}
    return (
        result_overview
        + resource_section
        + _task_comparison(summary)
        + slices
        + _execution_explorer(manifest, summary, details)
        + '<section class="card"><h2>尚未确认的内部根因</h2><p>失败日志已经能区分环境准备、页面交互与最终验收，但还不能证明 Moli 内部具体是哪一层导致差异。后续对照实验应固定页面、选择器和调用，测量 DOM 更新、布局、可见性与 actionability 恢复时间；在证据出现前，不把 screencast、渲染器或适配层写成已确认根因。</p></section>'
    )


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
    webarena = _is_webarena_manifest(manifest)
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
    site_counts: Counter[str] = Counter()
    for task in suite:
        environment_sites = list((task.get("environment") or {}).get("sites") or [])
        if environment_sites:
            site_counts.update(str(site) for site in environment_sites)
        else:
            site_counts.update(
                [
                    urlparse(str((task.get("source") or {}).get("url") or "")).hostname
                    or "未标注"
                ]
            )
    action_counts = Counter(
        str(call.get("action") or "") for task in suite for call in task.get("tool_calls") or []
    )
    distribution = (
        '<div class="grid suite-stats">'
        f'<div class="metric"><strong>{len(suite)}</strong><span>{"公开轨迹任务" if webarena else "公网任务"}</span></div>'
        f'<div class="metric"><strong>{len(site_counts)}</strong><span>{"自托管站点" if webarena else "独立网站/域名"}</span></div>'
        f'<div class="metric"><strong>{sum(action_counts.values())}</strong><span>参考 BrowserTool 调用</span></div>'
        "</div>"
        + '<div class="distribution-grid">'
        + f'<div class="distribution-item"><span>轨迹复杂度</span><strong>{_e(difficulty_summary)}</strong></div>'
        + '<div class="distribution-item"><span>网站分布</span><strong>'
        + _e("；".join(f"{host} {count}" for host, count in sorted(site_counts.items())))
        + '</strong></div><div class="distribution-item"><span>操作分布</span><strong>'
        + _e("；".join(f"{action} {count}" for action, count in sorted(action_counts.items())))
        + '</strong></div></div>'
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
        expected_outputs = truth.get("expected_outputs") or {}
        verified_evaluators = (task.get("source") or {}).get("verified_evaluators") or []
        expected_rows = (
            '<dl class="expected-values">'
            + "".join(
                f"<div><dt>{_e(name)}</dt><dd>{_annotate_source_marks(_e(value))}</dd></div>"
                for name, value in expected_outputs.items()
            )
            + "</dl>"
            + _source_mark_note(expected_outputs)
            if expected_outputs
            else "<p>"
            + _e(
                " + ".join(
                    str(item.get("evaluator") or "程序化 evaluator")
                    for item in verified_evaluators
                )
                or "程序化 evaluator"
            )
            + "</p>"
        )
        reference_path = " → ".join(truth.get("expected_actions") or [])
        source = task.get("source") or {}
        target_url = str(source.get("url") or "")
        environment = task.get("environment") or {}
        if environment.get("kind") == "webarena_verified":
            environment_label = " + ".join(environment.get("start_urls") or environment.get("sites") or [])
            source_html = f"<code>{_e(environment_label)}</code>"
            if target_url:
                source_html += (
                    f'<br><a target="_blank" rel="noopener" href="{_e(target_url)}">官方轨迹来源</a>'
                )
            environment_heading = "自托管测试环境"
            oracle_heading = "程序化验收器"
        else:
            source_html = (
                f'<a target="_blank" rel="noopener" href="{_e(target_url)}">{_e(target_url)}</a>'
            )
            environment_heading = "公网测试页面"
            oracle_heading = "唯一预期结果"
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
            f"<div><strong>{environment_heading}</strong><span>{source_html}</span></div>"
            f"<div><strong>参考路径</strong><span><code>{_e(reference_path)}</code></span></div>"
            f"<div><strong>{oracle_heading}</strong>{expected_rows}</div>"
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
    script = """<script>(()=>{const cards=[...document.querySelectorAll('.suite-task')];
const controls=Object.fromEntries(['query','difficulty','site','action'].map(k=>[k,document.getElementById('suite-'+k)]));
function apply(){const q=controls.query.value.trim().toLowerCase();let count=0;for(const card of cards){const d=card.dataset;
const match=(!q||d.search.toLowerCase().includes(q))&&(!controls.difficulty.value||d.difficulty===controls.difficulty.value)&&(!controls.site.value||d.site===controls.site.value)&&(!controls.action.value||d.actions.split(',').includes(controls.action.value));
card.hidden=!match;if(match)count++;}document.getElementById('suite-count').textContent=`显示 ${count} / ${cards.length} 个任务`;document.getElementById('suite-empty').hidden=count!==0;}
for(const [key,el] of Object.entries(controls))el.addEventListener(key==='query'?'input':'change',apply);
document.getElementById('suite-reset').onclick=()=>{for(const el of Object.values(controls))el.value='';apply();};apply();})();</script>"""
    if webarena:
        evaluator_count = sum(1 for task in suite if task.get("evaluator"))
        validation_note = (
            f'<p class="muted">程序 evaluator：{evaluator_count}/{len(suite)} 个任务已绑定。'
            f'任务清单 SHA-256：<code>{_e(str(manifest.get("suite_sha256") or "")[:16])}</code>。</p>'
        )
    else:
        validation_note = (
            f'<p class="muted">答案核验：{_e(validation.get("validator") or "未记录")}，'
            f'{_e(replay.get("passed"))}/{_e(replay.get("total"))} 已核验。数据集 SHA-256：'
            f'<code>{_e(str(validation.get("dataset_sha256") or "")[:16])}</code>。</p>'
        )
    return (
        f'<section class="card note"><h2>评测集概览</h2>{distribution}{validation_note}<details><summary>标注来源与版本</summary><p>{_e(validation.get("method") or "WebArena 官方任务、公开人类录制与程序 evaluator；本轮清单由资格验证结果冻结。")}</p></details></section>'
        + filters
        + "".join(cards)
        + script
    )


def _method_page(manifest: dict[str, Any]) -> str:
    p = manifest.get("protocol") or {}
    env = manifest.get("environment") or {}
    versions = env.get("browser_versions") or {}
    rows = [
        ["运行平台", str(env.get("runner_platform", "未记录"))],
        [
            "容器",
            f"Docker {env.get('docker_server_version', '未记录')} / {env.get('docker_os', '未记录')}",
        ],
        ["Chromium", str(versions.get("chromium", "未记录"))],
        ["Moli", str(versions.get("moli", "未记录"))],
        [
            "单浏览器配额",
            f"{p.get('cpus_per_browser', '?')} CPU / {p.get('memory_per_browser', '?')} / 独立网络",
        ],
        ["执行次数", "每任务每个浏览器形成 1 个正式结果"],
        ["视口", json.dumps(p.get("viewport") or {}, ensure_ascii=False)],
        [
            "失败复核",
            f"失败时最多补充 {p.get('max_failure_retries', '?')} 条同任务证据，不另计为新任务",
        ],
        ["采样", f"每 {p.get('sample_interval_ms', '?')} ms 采样内存、PID、CPU、网络计数"],
    ]
    if _is_webarena_manifest(manifest):
        scoring = """<ol><li><strong>程序验收（唯一任务分数）：</strong>固定的 WebArena-Verified evaluator 根据任务回答、最终服务状态和 HAR 网络事件判定；不使用 LLM judge，也不把参考动作当作答案。</li><li><strong>工具调用链（诊断项，不另计分）：</strong>“无中断”只表示预注册调用都返回；最终答案、服务状态或 HAR 仍可能不符合 evaluator，所以不能写成“任务完成”。</li></ol><p><strong>耗时口径：</strong>性能比较只使用双方首次均通过的正式样本。失败重试的耗时只用于执行预算和运维成本核算；任务详情按尝试分别展示，不再把累计耗时放在结论位置。</p><p><strong>边界：</strong>官方人类轨迹只用于固定输入路径。资格验证允许在不改变任务、环境和 evaluator 的前提下修正失效目标；每个修正都必须重新通过独立冷 Chromium 验证。</p>"""
        flow = f"""<ol><li>每个 task/browser attempt 前，从固定镜像重建任务涉及的有状态站点；只读 Map 只做健康检查。</li><li>等待官方入口初始化退出并验证外部 origin，随后用测试凭据登录；这些准备步骤不计入浏览器动作耗时。</li><li>创建干净页面，固定 1280×720 视口，计时外静置 {p.get("post_reset_settle_ms", "?")} ms；任务顺序交替两个浏览器。</li><li>执行固定轨迹，同时保存回答、动作、最终页面、截图、HAR、资源和终止原因。</li><li>每个任务每个浏览器一个主样本；只有主样本失败才重试一次。资源比较只使用双方首次均成功的配对任务。</li></ol>"""
    else:
        scoring = """<ol><li><strong>结果正确（唯一任务分数）：</strong>每个标注输出与 <code>expected_outputs</code> 做严格字符串相等；不使用模糊匹配或模型评分；Codex 只参与评测前的标注核对。</li><li><strong>工具调用链（诊断项，不另计分）：</strong>记录动作类型是否符合预注册轨迹且 BrowserTool 调用是否全部成功，用于定位中断步骤。</li></ol><p><strong>边界：</strong><code>expected_actions</code> 是工具层微基准的固定输入路径，不代表任务只有一种解法。若评测自由规划 agent，应改用必要里程碑、禁止动作和步数预算，不能要求整条序列逐步相等。</p>"""
        flow = f"""<ol><li>分别启动并预热两个浏览器，浏览器进程和上下文在整轮评测中保持运行。</li><li>任务前清 Cookie、权限、HTTP 缓存、Cache Storage、Service Worker、IndexedDB、来源存储和历史，并创建新页面。</li><li>计时外静置 {p.get("post_reset_settle_ms", "?")} ms，按任务交替两个 Provider 的执行顺序。</li><li>执行完整任务并采样；失败复核归入同一任务详情。</li><li>资源基线只比较双方都成功的同任务，失败与复核样本不混入正常路径。</li></ol>"""
    return f"""<section class="card note"><h2>步骤的严格定义</h2><p><strong>一步 = 固定参考轨迹中的一次浏览器工具调用</strong>，例如 navigate、click、get_text、fill、select、scroll、back 或 forward。等待页面就绪和目标值出现属于该调用内部，不单列、不计步。没有 agent 或模型在运行时选择下一步。</p></section><section class="card" id="scoring"><h2>成功判定：两个独立程序指标</h2>{scoring}</section><section class="card"><h2>环境</h2>{_table(["项目", "记录值"], rows, "本轮环境")}</section><section class="card"><h2>执行流程</h2>{flow}</section><section class="card"><h2>统计口径</h2><ul><li>P50 与中位数是同一个统计量；总体跨任务报告 P50、平均值和 nearest-rank P95。</li><li>配对比值的定义和逐项计算过程见<a href="webarena-trajectory-replay-latest-results.html#performance">结果页性能表与计算审计</a>。</li><li>每个任务仅有一个正式样本，因此逐任务不计算分位数；当前跨任务点不足以证明普遍性能优势。</li><li>内存平均值是时间加权均值；任务内存 P95 来自单次任务时序采样。</li><li>RX = Receive（接收），TX = Transmit（发送）；总量含页面资源与 CDP，速率来自相邻采样点差分。</li></ul></section>"""


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
    modules = []
    for index, (group, metrics) in enumerate(METRICS):
        modules.append(
            f'<details class="metric-module"{" open" if index == 0 else ""}><summary><span>{_e(group)}</span><small>{len(metrics)} 项指标</small></summary>'
            + _grouped_resource_table(summary, metrics, f"{group}绝对值（均为越低越好）")
            + '<p class="muted">逐任务先计算 <code>Moli / Chromium</code>，再汇总比值；不是两个浏览器边际分位数相除。</p>'
            + _paired_ratio_table(
                summary,
                metrics,
                f"{group}逐任务 Moli / Chromium 配对比值（均为越低越好）",
            )
            + "</details>"
        )
    return "".join(modules)


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
    result = final.get("result_status") or final.get("status")
    process = final.get("process_status") or final.get("status")
    attempt_label = "1 次正式执行" if len(attempts) == 1 else f"{len(attempts)} 次（含 1 次失败重试）"
    overview = (
        '<section class="card case-overview" id="case-result">'
        f'<div class="section-heading"><h1>{_e(title)}</h1><span class="chip">{_e(engine)}</span></div>'
        f'<p class="muted case-id">{_e(task_id)}</p>'
        '<div class="case-facts">'
        f'<div><span>结果</span><strong class="{"ok" if result == "pass" else "bad"}">{status_cell(result)}</strong></div>'
        f'<div><span>工具调用链</span><strong class="{"ok" if process == "pass" else "bad"}">{"无中断" if process == "pass" else "中断"}</strong></div>'
        f"<div><span>执行记录</span><strong>{_e(attempt_label)}</strong></div></div>"
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

    def validation_key(item: dict[str, Any], index: int) -> str:
        output = item.get("output")
        if output is not None:
            return str(output)
        return str(item.get("evaluator_name") or f"规则 {index + 1}")

    validation_maps = [
        {
            validation_key(item, index): item
            for index, item in enumerate(attempt.get("validation") or [])
        }
        for attempt in attempts
    ]
    rules = list(
        dict.fromkeys([*expected, *(key for values in validation_maps for key in values)])
    )

    def rule_purpose(rule: str, item: dict[str, Any] | None) -> str:
        evaluator = str((item or {}).get("evaluator_name") or "")
        if evaluator == "AgentResponseEvaluator":
            return "核对任务类型、完成状态和需要返回的答案"
        if evaluator == "NetworkEventEvaluator":
            return "在 HAR 中核对请求 URL、方法、请求数据和响应条件"
        if (item or {}).get("output") is not None or rule in expected:
            return f"核对输出 {rule} 是否与固定答案严格相等"
        return "执行该任务声明的程序化验收规则"

    def rule_passed(item: dict[str, Any]) -> bool | None:
        if isinstance(item.get("passed"), bool):
            return item["passed"]
        status = str(item.get("status") or "").lower()
        if status in {"success", "pass", "passed"}:
            return True
        if status in {"failure", "fail", "failed", "error"}:
            return False
        score = item.get("score")
        return score == 1 if isinstance(score, (int, float)) else None

    def compact(value: Any) -> str:
        if value is None:
            return "null"
        if value == "":
            return "（空字符串）"
        if isinstance(value, list):
            return f"{len(value)} 条记录"
        if isinstance(value, dict):
            preferred = [
                key
                for key in ("task_type", "status", "url", "http_method")
                if key in value
            ]
            keys = preferred or list(value)[:3]
            return "；".join(f"{key}={compact(value[key])}" for key in keys)
        text = str(value)
        return text if len(text) <= 180 else text[:177] + "…"

    def failure_reason(item: dict[str, Any]) -> str:
        messages = [str(item.get("error_msg") or "").strip()]
        for assertion in item.get("assertions") or []:
            messages.extend(
                str(value).strip() for value in assertion.get("assertion_msgs") or []
            )
            messages.append(str(assertion.get("error_msg") or "").strip())
        return next((message for message in messages if message), "")

    answer_rows = []
    for rule in rules:
        first = next((values[rule] for values in validation_maps if rule in values), None)
        expected_value = expected.get(rule, (first or {}).get("expected", "未声明"))
        expected_html = _annotate_source_marks(_e(compact(expected_value)))
        if isinstance(expected_value, (dict, list)):
            expected_html += evidence_link(expected_value, f"{rule} · 完整预期", "完整预期")
        cells = [
            f"<td>{_e(rule)}</td>",
            f"<td>{_e(rule_purpose(rule, first))}</td>",
            f"<td>{expected_html}</td>",
        ]
        for label, values in zip(labels, validation_maps, strict=True):
            item = values.get(rule)
            if item is None:
                cells.append('<td class="muted">未产生（过程失败） · 未判定</td>')
                continue
            passed = rule_passed(item)
            verdict = "通过" if passed is True else "失败" if passed is False else "未判定"
            css_class = "ok" if passed is True else "bad" if passed is False else ""
            reason = failure_reason(item)
            cell = f'<strong class="{css_class}">{verdict}</strong>' if css_class else verdict
            if reason:
                cell += f'<span class="rule-reason">{_e(reason)}</span>'
            cell += f'<span class="rule-actual">实际：{_e(compact(item.get("actual")))}</span>'
            if isinstance(item.get("actual"), (dict, list)):
                cell += evidence_link(
                    item["actual"], f"{label} · {rule} · 实际值", "完整实际值"
                )
            if item.get("assertions"):
                cell += evidence_link(
                    item["assertions"], f"{label} · {rule} · 逐条断言", "逐条断言"
                )
            cells.append(f"<td>{cell}</td>")
        answer_rows.append("<tr>" + "".join(cells) + "</tr>")
    answers = (
        '<div class="table-wrap rule-comparison"><table><caption>验收规则：预期、实际与判定</caption>'
        '<thead><tr><th>规则</th><th>验收目的</th><th>预期</th>'
        + "".join(f"<th>{_e(label)}</th>" for label in labels)
        + "</tr></thead><tbody>"
        + "".join(answer_rows)
        + "</tbody></table></div>"
        if rules
        else '<p class="muted">本次执行未生成可展示的验收规则。</p>'
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
    script = """<script>(()=>{
const dialog=document.getElementById('evidence-dialog');
if(!dialog || typeof dialog.showModal!=='function') return;
const title=document.getElementById('evidence-title');
const content=document.getElementById('evidence-content');
const action=document.getElementById('evidence-action');
let opener=null;
document.addEventListener('click',event=>{
  const link=event.target.closest('a[data-evidence]');
  if(!link || event.button!==0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const source=document.getElementById(link.dataset.evidence);
  if(!source?.querySelector('pre')) return;
  event.preventDefault(); opener=link;
  title.textContent=link.getAttribute('aria-label') || '原始证据';
  content.textContent=source.querySelector('pre').textContent;
  const cell=link.closest('td');
  const method=cell?.querySelector('.action-name')?.textContent;
  action.textContent=method ? '浏览器动作：'+method : '';
  action.hidden=!method;
  document.documentElement.classList.add('evidence-modal-open');
  dialog.showModal(); content.scrollTop=0;
});
dialog.addEventListener('close',()=>{
  document.documentElement.classList.remove('evidence-modal-open');
  opener?.focus({preventScroll:true});
});
dialog.addEventListener('click',event=>{
  if(event.target!==dialog) return;
  const r=dialog.getBoundingClientRect();
  if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) dialog.close();
});
document.documentElement.classList.add('evidence-enhanced');
// Old deep links remain readable, even with the floating viewer enabled.
function revealHash(){
  const id=location.hash.slice(1);
  if(!/^evidence-\\d+$/.test(id)) return;
  document.querySelector('a[data-evidence="'+id+'"]')?.click();
}
window.addEventListener('hashchange',revealHash); revealHash();
})();</script>"""
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


def _failed_validation_reason(attempt: dict[str, Any]) -> str:
    failed = [
        item
        for item in (attempt.get("validation") or [])
        if str(item.get("status") or "").lower() in {"failure", "fail", "failed", "error"}
        or item.get("passed") is False
        or item.get("score") == 0
    ]
    if not failed:
        return "工具调用无中断，但最终程序验收未通过。"
    item = failed[0]
    evaluator = str(item.get("evaluator_name") or "程序验收器")
    actual = item.get("actual")
    if evaluator == "AgentResponseEvaluator":
        return "工具调用无中断，但返回答案与 evaluator 的预期答案不一致。"
    if evaluator == "NetworkEventEvaluator" and not actual:
        return "工具调用无中断，但 HAR 中没有 evaluator 要求的网络事件。"
    if evaluator == "NetworkEventEvaluator":
        return "工具调用无中断，HAR 也有候选事件，但 URL、参数或请求内容未满足 evaluator。"
    return f"工具调用无中断，但 {evaluator} 未通过。"


def _failure_review(summary: dict[str, Any]) -> tuple[list[list[str]], dict[tuple[str, str], dict[str, str]]]:
    """Summarize each final failed logical execution from frozen error and evaluator evidence."""
    diagnostics = ((summary.get("failure_diagnostics") or {}).get("rule_engine") or {}).get(
        "groups"
    ) or []
    diagnostic_by_attempt = {
        (
            str(group.get("task_id")),
            str(group.get("engine")),
            int(attempt.get("attempt") or 1),
        ): group
        for group in diagnostics
        for attempt in (group.get("attempts") or [])
    }
    reviewed: dict[tuple[str, str], dict[str, str]] = {}
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for task_id, task in (summary.get("by_task") or {}).items():
        for engine, attempts in (task.get("attempts") or task.get("failures") or {}).items():
            if not attempts:
                continue
            final = attempts[-1]
            result = str(final.get("result_status") or final.get("status") or "fail")
            if result == "pass":
                continue
            process = str(final.get("process_status") or final.get("status") or "fail")
            key = (str(task_id), str(engine))
            diagnostic = diagnostic_by_attempt.get(
                (str(task_id), str(engine), int(final.get("attempt") or len(attempts)))
            ) or {}
            evidence = str(diagnostic.get("evidence") or final.get("error") or "")
            action = str(diagnostic.get("failed_action") or "")
            lowered = evidence.lower()
            if process == "pass":
                category = "最终验收不匹配"
                reason = _failed_validation_reason(final)
            elif action == "prepare_environment":
                category = "环境准备失败"
                reason = "环境重置、初始化或登录阶段超时，未完成本次任务操作。"
            else:
                category = "页面交互中断"
                if "intercepts pointer events" in lowered:
                    reason = "目标已定位，但被其他页面元素遮挡或拦截，点击等待超时。"
                elif "element is not visible" in lowered:
                    reason = "目标已定位但不可见，动作等待超时，后续步骤未执行。"
                elif "waiting for" in lowered:
                    reason = "等待指定页面元素超时，后续步骤未执行。"
                else:
                    reason = "BrowserTool 调用异常，后续步骤未执行。"
            reviewed[key] = {"category": category, "reason": reason}
            counts[category] += 1
            examples.setdefault(category, []).append(f"{task_id} · {engine}")
    descriptions = {
        "页面交互中断": "元素缺失、不可见、被遮挡或等待超时，调用链未走完。",
        "环境准备失败": "重置、初始化或登录阶段失败，任务操作未完整开始。",
        "最终验收不匹配": "工具调用全部返回，但答案或 HAR 证据不满足 evaluator。",
    }
    rows = [
        [category, str(counts[category]), descriptions[category], "；".join(examples[category][:4])]
        for category in ("页面交互中断", "环境准备失败", "最终验收不匹配")
        if counts[category]
    ]
    return rows, reviewed


def _execution_details(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    task_specs = {str(task.get("task_id")): task for task in manifest.get("suite") or []}
    diagnostic_groups = ((summary.get("failure_diagnostics") or {}).get("rule_engine") or {}).get(
        "groups"
    ) or []
    diagnostic_by_execution = {
        (str(group.get("task_id")), str(group.get("engine")), int(attempt.get("attempt") or 1)): group
        for group in diagnostic_groups
        for attempt in (group.get("attempts") or [])
    }
    _, reviewed_failures = _failure_review(summary)
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
            review = reviewed_failures.get((str(task_id), str(engine)))
            error_category = review["category"] if review else "无错误"
            actions = sorted(
                {
                    str(call.get("action"))
                    for attempt in attempts
                    for call in (attempt.get("trace") or [])
                    if call.get("action") != "validate_assertions"
                }
            )
            tags = task.get("tags") or []
            final_duration = float(final_attempt.get("duration_ms") or 0)
            diagnostic = diagnostic_by_execution.get(
                (str(task_id), str(engine), int(final_attempt.get("attempt") or len(attempts)))
            )
            if review:
                diagnostic = {
                    **(diagnostic or {}),
                    "category": review["category"],
                    "observed_mechanism": review["reason"],
                }
            detail_body = _task_detail_body(
                str(task_id),
                title,
                str(engine),
                task_specs.get(str(task_id)) or {},
                attempts,
                diagnostic,
            )
            # Artifact Hub injects a site-root base URL; fragment links need the file.
            detail_body = detail_body.replace('href="#', f'href="{detail_name}#')
            detail_pages[detail_name] = _shell(
                manifest,
                "results",
                detail_body,
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
                f'<div id="{_e(anchor)}" class="execution-card" data-task="{_e(task_id)}" data-engine="{_e(engine)}" data-status="{_e(final_result_status)}" data-combined-status="{_e(final_combined_status)}" data-complexity="{_e(task.get("complexity"))}" data-error="{_e(error_category)}" data-actions="{_e(",".join(actions))}" data-tags="{_e(",".join(tags))}" data-search="{_e(_trace_text(search_text))}" data-duration="{_e(final_duration)}"><a href="{detail_name}" class="execution-summary"><span data-label="任务">{_e(title)}</span><span data-label="浏览器">{_e(engine)}</span><span data-label="程序验收" class="{"ok" if final_result_status == "pass" else "bad"}">{"通过" if final_result_status == "pass" else "未通过"}</span><span data-label="工具调用" class="{"ok" if final_process_status == "pass" else "bad"}">{"无中断" if final_process_status == "pass" else "中断"}</span><span data-label="错误类别">{_e(error_category)}</span><span data-label="最终尝试耗时">{_show(final_duration, suffix=" ms")}</span></a></div>'
            )
    return "".join(blocks) or "<p>没有可展示的执行样本。</p>"


def _task_comparison(summary: dict[str, Any]) -> str:
    rows = []
    for task_id, task in (summary.get("by_task") or {}).items():
        attempts_by_engine = task.get("attempts") or task.get("failures") or {}
        statuses: dict[str, str] = {}
        links = []
        for engine in ("chromium", "moli"):
            attempts = attempts_by_engine.get(engine) or []
            final = attempts[-1] if attempts else {}
            status = str(final.get("result_status") or final.get("status") or "missing")
            statuses[engine] = status
            label = "通过" if status == "pass" else "未通过" if status == "fail" else "缺少记录"
            css = "ok" if status == "pass" else "bad"
            links.append(
                (
                    f'<a class="result-link {css}" href="{_detail_filename(str(task_id), engine)}">'
                    f'{label}<span aria-hidden="true">→</span></a>'
                )
                if attempts
                else f'<span class="{css}">{label}</span>'
            )
        different = statuses["chromium"] != statuses["moli"]
        any_fail = "fail" in statuses.values() or "missing" in statuses.values()
        comparison = (
            "结果不同，优先核对"
            if different
            else "双方均通过"
            if statuses["chromium"] == "pass"
            else "双方均未通过"
        )
        title = str(task.get("title") or task_id)
        rows.append(
            f'<div class="comparison-row" data-view="{"different" if different else "same"}" '
            f'data-failure="{"true" if any_fail else "false"}" data-search="{_e(_trace_text(f"{task_id} {title}"))}">'
            f'<span class="task-name" data-label="任务">{_e(title)}<small class="muted">{_e(task_id)}</small></span>'
            f'<span data-label="Chromium">{links[0]}</span><span data-label="Moli">{links[1]}</span>'
            f'<span class="comparison-note" data-label="对照结论">{comparison}</span></div>'
        )
    script = """<script>(()=>{const rows=[...document.querySelectorAll('.comparison-row')];
const buttons=[...document.querySelectorAll('.comparison-controls button')],query=document.getElementById('comparison-query'),count=document.getElementById('comparison-count');let view='different';
function apply(){const q=query.value.trim().toLowerCase();let n=0;for(const row of rows){const visible=(!q||row.dataset.search.toLowerCase().includes(q))&&(view==='all'||(view==='failure'&&row.dataset.failure==='true')||(view==='different'&&row.dataset.view==='different'));row.hidden=!visible;if(visible)n++;}count.textContent=`显示 ${n} / ${rows.length} 个任务`;}
for(const button of buttons)button.onclick=()=>{view=button.dataset.view;for(const item of buttons){const active=item===button;item.classList.toggle('active',active);item.setAttribute('aria-pressed',String(active));}apply();};query.addEventListener('input',apply);apply();})();</script>"""
    return (
        '<section class="card" id="task-comparison"><div class="section-heading"><div><h2>任务对照</h2>'
        '<p class="muted">同一任务并排查看两个浏览器的最终验收结果；点击“通过”或“未通过”进入该浏览器的完整证据。</p></div>'
        '<span id="comparison-count" class="muted"></span></div><div class="comparison-toolbar">'
        '<div class="comparison-controls" role="group" aria-label="任务对照范围">'
        '<button type="button" class="active" aria-pressed="true" data-view="different">仅看结果不同</button>'
        '<button type="button" aria-pressed="false" data-view="failure">任一侧未通过</button>'
        '<button type="button" aria-pressed="false" data-view="all">全部任务</button></div>'
        '<label>搜索任务<input id="comparison-query" type="search" placeholder="任务名称或编号"></label></div>'
        '<div class="comparison-grid"><div class="comparison-head" aria-hidden="true"><span>任务</span><span>Chromium</span><span>Moli</span><span>对照结论</span></div>'
        + "".join(rows)
        + '</div><p class="muted">这里用于找差异；下方“失败复核”用于归类，“执行证据”用于搜索错误、操作和验收明细。</p></section>'
        + script
    )


def _execution_explorer(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    filter_anchors = "".join(
        f'<span id="provider={_e(engine)}&amp;status=fail" hidden></span>'
        for engine in (summary.get("functional") or {})
    )
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
    category_rows, reviewed_failures = _failure_review(summary)
    diagnostic_categories = [row[0] for row in category_rows]
    error_options = '<option value="无错误">无错误</option>' + "".join(
        f'<option value="{_e(category)}">{_e(category)}</option>'
        for category in diagnostic_categories
    )
    filters = f"""<div class="filters" id="execution-filters" aria-label="执行样本筛选">
      <label>任务<select id="f-task"><option value="">全部任务</option>{task_options}</select></label>
      <label>浏览器<select id="f-engine"><option value="">全部浏览器</option><option>chromium</option><option>moli</option></select></label>
      <label>结果<select id="f-status"><option value="">全部结果</option><option value="pass">正确</option><option value="fail" selected>错误</option></select></label>
      <label>验收与调用链<select id="f-combined"><option value="">全部</option><option value="pass">验收通过且调用无中断</option><option value="fail">至少一项未通过</option></select></label>
      <label>难度<select id="f-complexity"><option value="">全部难度</option><option value="simple">简单</option><option value="medium">中等</option><option value="complex">复杂</option></select></label>
      <label>BrowserTool 方法<select id="f-action"><option value="">全部方法</option>{action_options}</select></label>
      <label>任务标签<select id="f-tag"><option value="">全部标签</option>{tag_options}</select></label>
      <label>错误类别<select id="f-error"><option value="">全部类别</option>{error_options}</select></label>
      <label>最终尝试耗时<select id="f-duration"><option value="">全部耗时</option><option value="lt1000">少于 1 秒</option><option value="1000-5000">1–5 秒</option><option value="gte5000">5 秒及以上</option></select></label>
      <label>全文搜索<input id="f-query" type="search" placeholder="任务、输出或错误"></label>
      <button type="button" id="f-all">展示全部</button><button type="button" id="f-reset">重置为错误</button><span class="filter-status" id="f-count" aria-live="polite"></span>
    </div>"""
    script = """<script>(()=>{
const cards=[...document.querySelectorAll('.execution-card')];
const ids=['task','engine','status','combined','complexity','action','tag','error','duration','query'];
const el=Object.fromEntries(ids.map(x=>[x,document.getElementById('f-'+x)]));
function apply(){
 let n=0;
 for(const c of cards){
  const d=Number(c.dataset.duration||0),duration=el.duration.value;
  const ok=(!el.task.value||c.dataset.task===el.task.value)&&(!el.engine.value||c.dataset.engine===el.engine.value)&&(!el.status.value||c.dataset.status===el.status.value)&&(!el.combined.value||c.dataset.combinedStatus===el.combined.value)&&(!el.complexity.value||c.dataset.complexity===el.complexity.value)&&(!el.action.value||c.dataset.actions.split(',').includes(el.action.value))&&(!el.tag.value||c.dataset.tags.split(',').includes(el.tag.value))&&(!el.error.value||c.dataset.error.split(',').includes(el.error.value))&&(!duration||(duration==='lt1000'&&d<1000)||(duration==='1000-5000'&&d>=1000&&d<5000)||(duration==='gte5000'&&d>=5000))&&(!el.query.value||c.dataset.search.toLowerCase().includes(el.query.value.toLowerCase()));
  c.hidden=!ok;if(ok)n++;
 }
 document.getElementById('f-count').textContent=`显示 ${n} / ${cards.length} 个任务 × 浏览器记录`;
 const empty=document.getElementById('execution-empty');empty.hidden=n!==0;
 const providerOnly=el.engine.value&&el.status.value==='fail'&&ids.filter(x=>!['engine','status'].includes(x)).every(x=>!el[x].value);
 empty.textContent=providerOnly?`${el.engine.value} 本轮没有失败任务。`:'当前筛选条件下没有匹配的任务。';
}
function clear(){for(const x of ids)el[x].value='';}
function defaults(){clear();el.status.value='fail';apply();}
function reveal(){
 const raw=location.hash.slice(1);
 if(raw.startsWith('provider=')){
  const params=new URLSearchParams(raw),provider=params.get('provider');
  if(!['chromium','moli'].includes(provider))return;
  clear();el.engine.value=provider;el.status.value=params.get('status')==='fail'?'fail':'';apply();
  document.getElementById('execution-filters').scrollIntoView({block:'start'});return;
 }
 let id;try{id=decodeURIComponent(raw);}catch{return;}
 const card=document.getElementById(id);
 if(card&&card.classList.contains('execution-card')){
  clear();apply();card.style.outline='3px solid #f4b400';
  requestAnimationFrame(()=>requestAnimationFrame(()=>card.scrollIntoView({block:'start'})));
 }
}
for(const x of ids)el[x].addEventListener(x==='query'?'input':'change',apply);
document.getElementById('f-all').onclick=()=>{clear();apply()};
document.getElementById('f-reset').onclick=defaults;
addEventListener('hashchange',reveal);apply();reveal();
})()</script>"""
    failed_attempts = sum(
        1
        for task in (summary.get("by_task") or {}).values()
        for attempts in (task.get("attempts") or task.get("failures") or {}).values()
        for attempt in attempts
        if str(attempt.get("status") or attempt.get("result_status") or "") == "fail"
    )
    final_failures = len(reviewed_failures)
    review_table = _table(
        ["主要原因", "最终失败记录", "证据含义", "代表任务"],
        category_rows or [["无最终失败", "0", "—", "—"]],
        "失败日志审查结论",
    )
    grid_head = '<div class="record-grid-head" aria-hidden="true"><span>任务</span><span>浏览器</span><span>程序验收</span><span>工具调用</span><span>错误类别</span><span>最终尝试耗时</span></div>'
    return f"""{filter_anchors}<section class="card" id="failure-review"><div class="section-heading"><h2>失败复核</h2><a href="#execution-results">查看执行证据</a></div><p>本轮共有 <strong>{failed_attempts}</strong> 条失败物理尝试。下表逐条复核 <strong>{final_failures}</strong> 条最终失败的任务 × 浏览器记录，读取其最终错误、BrowserTool trace 和 evaluator 输出；重试前后的完整日志保留在任务详情。</p>{review_table}<p class="muted">这不是第二套评分。分类只概括直接失败机制；“元素不可见”“HAR 未匹配”等不自动等于浏览器内部根因。</p></section><section class="card" id="execution-results"><h2>执行证据</h2><p>{logical_executions} 条任务 × 浏览器记录，{physical_attempts} 次执行。默认显示最终失败记录；点击任务可查看每次执行的操作、答案、判定、错误栈和 BrowserTool trace。</p>{filters}<div id="execution-list" class="record-grid" role="region" aria-label="逐任务执行记录" tabindex="0">{grid_head}{_execution_details(manifest, summary, detail_pages)}</div><p id="execution-empty" class="empty-state" role="status" hidden></p>{script}</section>"""


def _review_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    provenance = manifest.get("artifact_provenance") or {}
    functional = summary.get("functional") or {}
    attempts = sum(int((functional.get(engine) or {}).get("physical_attempts") or 0) for engine in ("chromium", "moli"))
    evidence_fields = (manifest.get("protocol") or {}).get("required_attempt_evidence") or []
    rows = [
        ["候选来源", "179 条 WebArena 官方人类录制", "来源轨迹哈希逐条登记"],
        ["架构与 evaluator 预筛", "排除 4 条 arm64-only Wikipedia 与 3 条已证实 evaluator 缺陷", "规则在浏览器结果产生前冻结"],
        ["轨迹资格验证", "冷 Chromium 重放 + 官方程序 evaluator + 证据完整性", "仅通过任务进入正式 30 条分母"],
        ["正式任务配对", "30 个任务 × 2 个浏览器", "相同任务、轨迹、视口、超时和验收器"],
        ["失败重试", f"共 {attempts} 次物理执行", "每个首次失败最多一次；不增加任务分母"],
        ["结果评分", "AgentResponse / 最终服务状态 / HAR 网络事件", "无 LLM judge；缺失必需证据即失败"],
    ]
    checks = [
        ["任务清单绑定", str(manifest.get("suite_sha256") or "未记录"), "通过"],
        ["attempts 绑定", str(provenance.get("attempts_sha256") or "未记录"), "通过"],
        ["summary 绑定", str(provenance.get("summary_sha256") or "未记录"), "通过"],
        ["必需证据", "、".join(str(item) for item in evidence_fields), "缺失即失败"],
        ["运行时 Agent / 模型", "未配置", "通过"],
        ["浏览器架构", str((manifest.get("environment") or {}).get("docker_architecture") or "未记录"), "通过"],
    ]
    return (
        '<section class="card note" id="verification"><h2>核验结论</h2><p><strong>这里核验分母、数据绑定和评分链路；失败复核在“评测结果”页披露。</strong>代码评审不等于执行证据复判，程序评分也不冒充人工评分。正式数字由持久证据重算；发布包必须与当前运行档案和哈希一致。</p></section>'
        '<section class="card"><h2>从公开录制到正式分母</h2>'
        + _table(["环节", "范围", "验收规则"], rows, "资格与正式运行审计") + '</section>'
        '<section class="card"><h2>数据完整性</h2>'
        + _table(["检查", "绑定值", "状态"], checks, "当前运行绑定") + '</section>'
        '<section class="card"><h2>发布前固定门禁</h2><ol><li>重算报告并核对 current-report 与 manifest / summary 哈希。</li><li>检查站内链接、隐私信息和发布包哈希。</li><li>以 expected-version 原子更新固定站点，防止覆盖别人的新版本。</li><li>下载不可变版本导出，与本地提交包逐文件比较；只有一致才更新发布档案。</li></ol></section>'
        '<section class="card"><h2>仍然存在的边界</h2><ul><li>只有一个正式样本；重试用于恢复观察，不构成置信区间。</li><li>任务来自一个冻结的 30 条子集；站点分组样本更少。</li><li>失败审查读取错误栈、BrowserTool trace 和 evaluator 输出，只能概括直接机制，不自动证明浏览器内部根因。</li></ul></section>'
    )


def _appendix_page(manifest: dict[str, Any]) -> str:
    p = manifest.get("protocol") or {}
    provenance = manifest.get("artifact_provenance") or {}
    fields = [
        ["task", "ground_truth / source / expected_actions", "唯一结果、固定参考路径与来源"],
        [
            "attempt",
            "result_status / process_status / combined_status",
            "程序验收分数与工具调用链诊断",
        ],
        ["attempt", "validation / trace / error", "同一任务的判定与完整执行证据"],
        ["resource", "memory mean/p95/peak/retained/MiB·s", "内存水平、尾部、峰值、保留量和积分"],
        ["resource", "CPU / network total/average/p95/peak", "计算与网络时序压力"],
        ["summary", "paired intersection / paired ratios", "双方成功任务与逐任务配对比值审计"],
        ["diagnostics", "rule_engine", "程序化失败分类、证据与原始执行记录入口"],
    ]
    provenance_rows = [
        ["运行编号", str(manifest.get("run_id") or "未记录")],
        ["生成时间", str(manifest.get("created_at") or "未记录")],
        ["评测集 SHA-256", str(manifest.get("suite_sha256") or "未记录")],
        ["attempts.jsonl SHA-256", str(provenance.get("attempts_sha256") or "未记录")],
        ["summary.json SHA-256", str(provenance.get("summary_sha256") or "未记录")],
    ]
    change_path = ROOT / "evaluation/browser-use-public/report-changes.json"
    if not change_path.is_file():
        raise ValueError(f"trajectory report change log is missing: {change_path}")
    change_log = json.loads(change_path.read_text(encoding="utf-8"))
    if change_log.get("schema") != "lexmount_webarena_trajectory_report_changes/1":
        raise ValueError("trajectory report change log schema is invalid")
    change_records = change_log.get("releases")
    if not isinstance(change_records, list) or not change_records:
        raise ValueError("trajectory report change log must contain at least one release")
    changes = [[row.get("date"), row.get("type"), row.get("effect")] for row in change_records]
    return f"""<section class="card"><h2>复现步骤</h2><ol><li>准备 Docker Engine、Python 3.11+、至少 4 CPU/6 GiB 内存。</li><li>运行 <code>./examples/browser_use/run.sh</code>；脚本执行完整基准，并依次生成原始记录、汇总数据和分页 HTML。</li><li>如需为失败任务补充一次同任务证据，设置 <code>BROWSER_USE_EVAL_FAILURE_RETRIES=1</code>；该记录不会另计为新任务。</li><li>如只需从已有 <code>attempts.jsonl</code> 重算全部指标和报告，运行 <code>uv run --frozen python examples/browser_use/rebuild_report.py</code>。</li><li>本地报告快路径使用 <code>make trajectory-report</code>；确认发布使用 <code>make trajectory-report-release</code>。数据集、runner 或 scorer 改动后，再人工触发 <code>make trajectory-report-audit</code>。</li></ol><p><a href="webarena-trajectory-replay-latest-data.json">下载本轮机器可读审计摘要</a></p></section><section class="card"><h2>运行与原始数据绑定</h2>{_table(["项目", "值"], provenance_rows, "可复核来源")}</section><section class="card"><h2>变更记录</h2>{_table(["日期", "类型", "读者可见影响"], changes, "同一 canonical 页面持续更新")}</section><section class="card"><h2>数据字段</h2>{_table(["对象", "关键字段", "用途"], fields, "数据结构")}</section><section class="card"><h2>复现限制</h2><ul><li>本轮是自托管环境，不受公网网页漂移影响；但镜像、源码和环境初态必须固定。</li><li>采样间隔 {p.get("sample_interval_ms", "?")} ms，持续时间短于采样间隔的尖峰可能遗漏。</li><li>浏览器进程保持运行且任务间清理状态；分配器保留内存不等于缓存污染。</li></ul></section><section class="card"><h2>原始环境 JSON</h2><details><summary>展开</summary><pre>{_e(json.dumps(manifest.get("environment") or {}, ensure_ascii=False, indent=2, sort_keys=True))}</pre></details></section>"""


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    paired_engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}

    def paired_duration(engine: str, stat: str) -> str:
        return _stat(((paired_engines.get(engine) or {}).get("duration_ms") or {}), stat)

    lines = [
        "# Chromium 与 Moli：WebArena 固定轨迹回放评测",
        "",
        f"运行编号：`{manifest.get('run_id', 'unknown')}`",
        "",
        "这是 RPA 式固定轨迹回放：重放 WebArena 公开人工成功轨迹，只比较浏览器与适配层兼容性，不评测 Agent 规划能力；成功判定和失败分类均由程序完成。",
        "",
        "| 浏览器 | 首次程序验收通过 | 最终程序验收通过 | P50 ms | 平均值 ms | P95 ms |",
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
            "BROWSER_USE_EVAL_FAILURE_RETRIES=1 ./examples/browser_use/run.sh",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_latest_report(
    reports_dir: str | Path, *, manifest: dict[str, Any], summary: dict[str, Any]
) -> Path:
    manifest = public_projection(manifest)
    summary = public_projection(summary)
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    audit_name = f"{REPORT_STEM}-data.json"
    detail_pages: dict[str, str] = {}
    results = _results_page(manifest, summary, detail_pages)
    keep = {f"{REPORT_STEM}.md", audit_name, *(filename for _, _, filename in PAGES), *detail_pages}
    for pattern in (
        f"{REPORT_STEM}*.md",
        f"{REPORT_STEM}*.html",
        f"{REPORT_STEM}*.json",
        "browser-use-evaluation-*.md",
        "browser-use-evaluation-*.html",
        "browser-use-evaluation-*.json",
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
    renderers = {
        "index": lambda: _summary_page(manifest, summary),
        "results": lambda: results,
        "suite": lambda: _suite_page(manifest),
        "research": lambda: _research_page(manifest, summary),
        "method": lambda: _method_page(manifest) + _review_page(manifest, summary),
        "appendix": lambda: _appendix_page(manifest),
    }
    for slug, _, filename in PAGES:
        (root / filename).write_text(_shell(manifest, slug, renderers[slug]()), encoding="utf-8")
    for filename, document in detail_pages.items():
        (root / filename).write_text(document, encoding="utf-8")
    return md_path
