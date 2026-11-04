"""Chinese paginated evidence-first browser benchmark report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.environments.browser import DEFAULT_BROWSER_IMAGES
from harness.trajectory_replay.core.report import (
    EVIDENCE_DIALOG_JS,
    SUITE_FILTER_JS,
    ComparedValue,
    _action_text,
    _annotate_source_marks,
    _comparison_class,
    _difficulty,
    _display_number,
    _e,
    _numeric_stat,
    _show,
    _source_mark_note,
    _source_references,
    _stat,
    _trace_text,
)
from harness.trajectory_replay.messages import report_text
from harness.trajectory_replay.participants import engine_label, engine_names, summary_engines

ASSETS = Path(__file__).with_name("assets")
REPORT_CSS = (ASSETS / "curated-report.css").read_text(encoding="utf-8")
EXECUTION_FILTER_JS = (ASSETS / "curated-execution-filter.js").read_text(encoding="utf-8")

# This renderer generates only the Chinese edition.
t = report_text("zh")

REPORT_STEM = "trajectory-replay-evaluation-latest"
PAGES = tuple(
    (slug, label, f"{REPORT_STEM}{suffix}.html")
    for slug, label, suffix in (
        ("index", t("replay.page.summary", "Summary"), ""),
        ("suite", t("replay.page.suite", "Suite"), "-suite"),
        ("method", t("replay.page.method", "Environment and method"), "-method"),
        ("analysis", t("curated.page.analysis", "Per-task analysis"), "-analysis"),
        ("appendix", t("replay.page.appendix", "Reproduction and appendix"), "-appendix"),
    )
)


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


def _engines(summary: dict[str, Any]) -> tuple[str, ...]:
    """Participants in evidence order; a summary with no results keeps the registry's."""
    return summary_engines(summary) or tuple(DEFAULT_BROWSER_IMAGES)


def _all_parties(engines: tuple[str, ...]) -> str:
    return t("curated.parties.both", "both providers") if len(engines) == 2 else t("curated.parties.all", "all providers ")


def _provider_count(engines: tuple[str, ...]) -> str:
    return t("curated.count.both", "both") if len(engines) == 2 else t("curated.count.other", "{count}", count=len(engines))


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
        cells = [f'<td data-label="{t("replay.column.metric", "Metric")}">{_e(f"{label} ({unit})")}</td>']
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
        f'<th colspan="{len(engines)}">{label}</th>' for label in ("P50", "P95", t("replay.stat.mean", "Mean"))
    )
    engine_headers = "".join(
        f"<th>{engine_label(engine)}</th>" for _ in range(3) for engine in engines
    )
    return (
        f'<div class="table-wrap resource-summary"><table><caption>{_e(caption)}</caption>'
        f'<thead><tr><th rowspan="2">{t("replay.column.metric", "Metric")}</th>{group_headers}</tr>'
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
        [t("replay.column.metric", "Metric"), t("curated.ratio.p50", "Per-task ratio P50"), t("curated.ratio.p95", "Per-task ratio P95"), t("curated.ratio.mean", "Per-task ratio mean")],
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
                t("curated.audit.p95_value", "P95 value") if rank == p95_rank else "",
            ]
        )
    scope = f"{_pair_label(comparison, baseline)} " if labelled else ""
    return (
        f"<details><summary>{t('curated.audit.summary', 'View the complete {scope}per-task duration ratio audit', scope=scope)}</summary>"
        f"<p>{t('curated.audit.body', 'The script first computes <code>{comparison} duration / {baseline} duration</code> for each task in the success intersection, then sorts. This run has <code>n={count}</code>, and the nearest-rank P95 position is <code>ceil(0.95 × {count}) = {rank}</code>; so the P95 in the table is sorted paired ratio number {rank}.', comparison=engine_label(comparison), baseline=engine_label(baseline), count=len(values), rank=p95_rank)}</p>"
        + _table(
            [t("curated.audit.rank", "Rank"), t("replay.column.task", "Task"), engine_label(baseline), engine_label(comparison), t("curated.ratio.column", "Per-task ratio"), t("curated.audit.position", "Statistic position")],
            rows,
            t("curated.audit.caption", "{scope}Raw sequence of paired duration ratios", scope=scope),
        )
        + "</details>"
    )


def _nav(active: str) -> str:
    return (
        f'<nav aria-label="{t("replay.nav.pages", "Report pages")}">'
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
        f'<div class="case-header"><a href="{REPORT_STEM}-analysis.html#{_e(back_anchor)}">← {t("curated.back_to_analysis", "Back to per-task analysis")}</a>'
        f'<a href="{REPORT_STEM}.html">{t("curated.shell.detail_title", "{engines} evaluation", engines=" / ".join(engine_label(engine) for engine in engines))}</a><span class="muted">{t("replay.run", "Run {run_id}", run_id=_e(manifest.get("run_id", "unknown")))}</span></div>'
        if detail_title
        else f'<div class="brand"><div class="eyebrow">Lexmount Browser Provider Evaluation</div><h1>{t("curated.shell.heading", "{names} browser tool-call evaluation", names=names)}</h1><div class="muted">{t("curated.shell.page_info", "Run ID: {run_id} · Page {page}/{pages}", run_id=_e(manifest.get("run_id", "unknown")), page=index + 1, pages=len(PAGES))}</div></div>{_nav(active)}'
    )
    pager = "" if detail_title else f'<div class="pager">{prev_link}{next_link}</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{t("curated.shell.title", "{title} | {names} evaluation", title=_e(detail_title) or label, names=names)}</title><style>{REPORT_CSS}</style></head><body><header><div class="wrap">{header}</div></header><main>{body}{pager}</main><footer>Lexmount Browser Provider Evaluation</footer></body></html>"""


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
            return t("replay.score.rate", "{passed}/{total} ({rate:.2f}%)", passed=passed, total=task_total, rate=rate)

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
            f'<td class="{"better" if result_winner else ""}">{t("replay.score.rate", "{passed}/{total} ({rate:.2f}%)", passed=result, total=tasks, rate=result_rate)}</td>'
            f'<td class="{"better" if combined_winner else ""}">{t("replay.score.rate", "{passed}/{total} ({rate:.2f}%)", passed=combined, total=tasks, rate=combined_rate)}</td>'
            f'<td><a class="provider-failure-link" href="{REPORT_STEM}-analysis.html#provider={engine}&amp;status=fail" aria-label="{t("replay.failure_link.label", "{engine}: view {count} failed tasks", engine=engine, count=max(0, tasks - result))}">{t("replay.failure_link.count", "{count} failed", count=max(0, tasks - result))} <span aria-hidden="true">→</span></a></td>'
            "</tr>"
        )
    return (
        f'<div class="table-wrap"><table><caption>{t("curated.correctness.title", "Task correctness")}</caption><thead><tr>'
        f"<th>Provider</th><th>{t('curated.correctness.result', 'Result correct')}</th><th>{t('curated.correctness.combined', 'Result and reference process both correct')}</th><th>{t('curated.correctness.analysis', 'Task analysis')}</th>"
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
        return t("curated.recommendation.single", "This run has only one provider, {engines}, with {top} correct results; there is nothing to compare, so no default is recommended.", engines=engine_names(engines), top=top)
    if len(leaders) > 1:
        subject = (
            f"{_provider_count(engines)} Provider" if leaders == engines else f" {engine_names(leaders)}"
        )
        return t("curated.recommendation.tied", "In this run{subject} have the same number of correct results ({top} each); correctness alone cannot select a default, so decide using resource metrics on tasks {parties} passed and the failure types.", subject=subject, top=top, parties=parties)
    leader = leaders[0]
    others = tuple(engine for engine in engines if engine != leader)
    lower = t("replay.separator.list", ", ").join(t("curated.recommendation.lower", "{engine} with {count}", engine=engine_label(engine), count=passes[engine]) for engine in others)
    claim = t("curated.recommendation.claim", "This run recommends {leader} as the default: it has {top} correct results, above {lower}; ", leader=engine_label(leader), top=top, lower=lower)
    if leader == engines[0]:
        return t("curated.recommendation.alternative", "{claim}{others} can be an alternative for verified compatible tasks if it shows lower duration or resource use on tasks {parties} passed.", claim=claim, others=engine_names(others), parties=parties)
    return t("curated.recommendation.boundary", "{claim}Confirm where it applies using resource metrics on tasks {parties} passed and the failure types.", claim=claim, parties=parties)


def _ratio_finding(summary: dict[str, Any], engines: tuple[str, ...]) -> str:
    pairs = _ratio_pairs(summary, engines)
    if not pairs:
        return ""

    def mean(metrics: dict[str, Any], name: str) -> str:
        return _show(_numeric_stat(metrics.get(name) or {}, "mean"), suffix="×")

    clauses = t("replay.separator.clause", "; ").join(
        t("curated.finding.ratio_clause", "per-task {pair} ratios have a mean duration ratio of {duration}, a mean time-weighted memory ratio of {memory} and a mean received-bytes ratio of {network}", pair=_pair_label(comparison, baseline), duration=mean(metrics, "duration_ms"), memory=mean(metrics, "memory_mean_bytes"), network=mean(metrics, "network_rx_bytes"))
        for comparison, baseline, metrics in pairs
    )
    return f"<li>{t('curated.finding.speed', '<strong>Speed and resources:</strong> on tasks {parties} passed, {clauses}.', parties=_all_parties(engines), clauses=clauses)}</li>\n"


def _root_cause_finding(engines: tuple[str, ...]) -> str:
    layers, link = (
        (t("curated.finding.layers_moli", "screencast, renderer or adapter layer"), t("curated.finding.link_moli", "View per-task evidence and the verification plan"))
        if "moli" in engines
        else (t("curated.finding.layers", "renderer or adapter layer"), t("curated.finding.link", "View per-task evidence"))
    )
    return f"""<li>{t("curated.finding.root_cause", '<strong>Root-cause boundary:</strong> layout and actionability evidence only locates the symptom; it cannot yet confirm a specific root cause in the {layers}. <a href="trajectory-replay-evaluation-latest-analysis.html">{link}</a>.', layers=layers, link=link)}</li>"""


def _summary_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    task_count = len(manifest.get("suite") or [])
    engines = _engines(summary)
    parties = _all_parties(engines)
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)

    agent_smoke = manifest.get("agent_smoke") or {}
    smoke_html = ""
    if agent_smoke:
        provenance = agent_smoke.get("provenance") or {}
        smoke_html = f"""<section class="card"><h2>{t("curated.smoke.title", "CowAgent end-to-end smoke test")}</h2><p>{t("curated.smoke.body", "This test is separate from the resource microbenchmark, is not part of the statistics above and was not re-run in run <code>{run_id}</code>. Model <code>{model}</code>, reasoning effort <code>{effort}</code>; independent execution date <code>{executed_at}</code>, source file content hash <code>{source_hash}</code>. Each browser configuration ran the task once.", run_id=_e(manifest.get("run_id")), model=_e(agent_smoke.get("model")), effort=_e(agent_smoke.get("reasoning_effort")), executed_at=_e(agent_smoke.get("executed_at")), source_hash=_e(str(provenance.get("source_sha256") or "")[:12]))}</p>{_table([t("curated.smoke.browser", "Browser configuration"), t("replay.detail.result", "Result"), t("curated.smoke.calls", "BrowserTool calls"), t("curated.smoke.answer", "Final answer")], [[str(item.get("provider")), t("replay.status.pass", "Pass") if item.get("status") == "pass" else str(item.get("status")), str(item.get("browser_tool_calls")), str(item.get("answer"))] for item in agent_smoke.get("results") or []], t("curated.smoke.caption", "Independent end-to-end acceptance test"))}</section>"""
    recommendation = _recommendation(summary, engines)
    correctness = _correctness_table(summary)
    highlight_legend = (
        t("curated.summary.legend", "Winning absolute values are bold green; paired ratios below 1 are green and above 1 are red; equal displayed values stay neutral.")
        if len(engines) > 1
        else ""
    )
    return f"""<section class="card note"><h2>{t("curated.summary.conclusion", "Conclusion and product recommendation")}</h2><p><strong>{recommendation}</strong></p><p>{t("curated.summary.scope", "This is a public-web deterministic microbenchmark at the browser tool-call layer, not an LLM Agent ranking. The suite has {count} tasks with unique results.", count=task_count)}</p></section>
<section class="card"><h2>{t("curated.correctness.title", "Task correctness")}</h2>{correctness}</section>
<section class="card" id="technical-findings"><h2>{t("curated.summary.findings", "Key technical findings")}</h2><ul>
{_ratio_finding(summary, engines)}{MOLI_TIMING_FINDING if "moli" in engines else ""}{_root_cause_finding(engines)}</ul></section>
<section class="card" id="performance"><h2>{t("curated.summary.performance", "Speed and resources on tasks {parties} passed", parties=parties)}</h2><p>{t("curated.summary.performance_scope", "Scope: {count} same tasks {parties} passed, excluding failures and retry samples.", parties=parties, count=paired)}{highlight_legend}</p>{_metric_tables(summary)}{_duration_ratio_audit(summary)}</section>{smoke_html}"""


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
        t("replay.separator.list", ", ").join(
            t("replay.suite.difficulty_count", "{difficulty}: {count}", difficulty=_difficulty(difficulty), count=count)
            for difficulty, count in difficulty_counts.items()
            if count
        )
        or t("replay.suite.no_difficulty", "No difficulty label")
    )
    site_counts = Counter(
        urlparse(str((task.get("source") or {}).get("url") or "")).hostname or t("replay.unlabeled", "Unlabeled")
        for task in suite
    )
    action_counts = Counter(
        str(call.get("action") or "") for task in suite for call in task.get("tool_calls") or []
    )
    distribution = (
        '<div class="grid suite-stats">'
        f'<div class="metric"><strong>{len(suite)}</strong><span>{t("replay.suite.public_web_tasks", "Public-web tasks")}</span></div>'
        f'<div class="metric"><strong>{len(site_counts)}</strong><span>{t("replay.suite.independent_sites", "Independent sites/domains")}</span></div>'
        f'<div class="metric"><strong>{sum(action_counts.values())}</strong><span>{t("replay.suite.reference_calls", "Reference BrowserTool calls")}</span></div>'
        "</div>"
        + _table(
            [t("curated.suite.dimension", "Dimension"), t("curated.suite.distribution", "Distribution")],
            [
                [t("replay.filter.difficulty", "Difficulty"), difficulty_summary],
                [
                    t("replay.filter.site", "Site"),
                    t("replay.separator.clause", "; ").join(f"{host} {count}" for host, count in sorted(site_counts.items())),
                ],
                [
                    t("curated.suite.capability", "Capability"),
                    t("replay.separator.clause", "; ").join(
                        f"{action} {count}" for action, count in sorted(action_counts.items())
                    ),
                ],
            ],
            t("curated.suite.caption", "Suite distribution"),
        )
    )
    cards = []
    for index, task in enumerate(suite, 1):
        title = str(task.get("title") or task.get("task_id"))
        calls = task.get("tool_calls") or []
        call_html = "".join(
            f'<div class="call"><strong>{t("replay.suite.call", "Call {index} · {action}", index=i, action=_e(c.get("action")))}</strong><br>{_e(_action_text(c))}'
            + (
                f'<br><span class="muted">{t("replay.suite.call_output", "Output: {name}", name=_e(c.get("save_as")))}</span>'
                if c.get("save_as")
                else ""
            )
            + "</div>"
            for i, c in enumerate(calls, 1)
        )
        truth = task.get("ground_truth") or {}

        expected_inline = t("replay.separator.clause", "; ").join(
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
            t("replay.suite.search_text", "Task{index:02d} Task {index:02d} {task_id} {title} {target_url} ", index=index, task_id=task.get('task_id'), title=title, target_url=target_url)
            + " ".join(task.get("tags") or [])
            + " "
            + expected_inline
        )
        cards.append(
            f'<section class="card suite-task" data-difficulty="{_e(task.get("complexity"))}" data-site="{_e(urlparse(target_url).hostname or t("replay.unlabeled", "Unlabeled"))}" data-actions="{_e(",".join(sorted({str(c.get("action")) for c in calls})))}" data-search="{_e(search_text)}"><div class="eyebrow">{t("replay.suite.task_eyebrow", "Task {index:02d}", index=index)}</div><h2>{_e(title)}</h2><span class="chip">{_difficulty(str(task.get("complexity")))}</span><span class="chip">{t("replay.suite.tool_calls", "{count} tool calls", count=len(calls))}</span>{chips}'
            '<div class="task-verification">'
            f"<div><strong>{t('replay.suite.public_web_page', 'Public-web test page')}</strong><span>{source_html}</span></div>"
            f"<div><strong>{t('replay.suite.reference_path', 'Reference path')}</strong><span><code>{_e(reference_path)}</code></span></div>"
            f"<div><strong>{t('replay.suite.unique_expected', 'Unique expected result')}</strong>{expected_rows}</div>"
            "</div>"
            f"<details><summary>{t('curated.suite.steps_and_basis', 'Step-by-step actions and answer basis')}</summary>{call_html}<p><strong>{t('replay.suite.answer_basis', 'Answer basis: ')}</strong>{_e(truth.get('derivation'))}</p>{_source_references(source)}</details></section>"
        )
    filters = (
        f'<section class="card suite-filter-panel" aria-label="{t("replay.suite.filter_label", "Task filter")}"><div class="section-heading"><h2>{t("replay.suite.find_tasks", "Find tasks")}</h2>'
        f'<span id="suite-count" class="muted" aria-live="polite">{t("replay.suite.showing", "Showing {shown} / {total} tasks", shown=len(suite), total=len(suite))}</span></div>'
        f'<div class="suite-filter-controls"><label class="suite-search">{t("replay.suite.search", "Search tasks")}<input id="suite-query" type="search" placeholder="{t("replay.suite.search_placeholder", "Task name, ID, site or keyword")}"></label>'
        f'<label>{t("replay.filter.difficulty", "Difficulty")}<select id="suite-difficulty"><option value="">{t("replay.filter.all_difficulties", "All difficulties")}</option>'
        + "".join(
            f'<option value="{_e(d)}">{t("replay.filter.difficulty_option", "{difficulty} ({count})", difficulty=_difficulty(d), count=difficulty_counts[d])}</option>'
            for d in ordered_difficulties
        )
        + f'</select></label><label>{t("replay.filter.site", "Site")}<select id="suite-site"><option value="">{t("replay.filter.all_sites", "All sites")}</option>'
        + "".join(
            f'<option value="{_e(s)}">{t("replay.filter.site_option", "{site} ({count})", site=_e(s), count=n)}</option>'
            for s, n in sorted(site_counts.items())
        )
        + f'</select></label><label>{t("replay.filter.action", "Action capability")}<select id="suite-action"><option value="">{t("replay.filter.all_actions", "All actions")}</option>'
        + "".join(f'<option value="{_e(a)}">{_e(a)}</option>' for a in sorted(action_counts))
        + f'</select></label><button id="suite-reset" type="button">{t("replay.filter.clear", "Clear filters")}</button></div></section>'
        f'<p id="suite-empty" class="card empty-state" hidden>{t("replay.filter.no_match", "No matching tasks. Adjust the keywords or clear the filters.")}</p>'
    )
    script = f"<script>{SUITE_FILTER_JS}</script>"
    return (
        f'<section class="card note"><h2>{t("replay.suite.overview", "Suite overview")}</h2>{distribution}<p class="muted">{t("replay.answer_check", "Answer verification: {validator}, {passed}/{total} verified. Dataset SHA-256: <code>{dataset}</code>.", validator=_e(validation.get("validator") or t("replay.not_recorded", "Not recorded")), passed=_e(replay.get("passed")), total=_e(replay.get("total")), dataset=_e(str(validation.get("dataset_sha256") or "")[:16]))}</p><details><summary>{t("replay.suite.annotation_source", "Annotation source and version")}</summary><p>{_e(validation.get("method") or t("replay.not_recorded", "Not recorded"))}</p></details></section>'
        + filters
        + "".join(cards)
        + script
    )


def _method_page(manifest: dict[str, Any], engines: tuple[str, ...]) -> str:
    p = manifest.get("protocol") or {}
    env = manifest.get("environment") or {}
    versions = env.get("browser_versions") or {}
    rows = [
        [t("replay.env.platform", "Runner platform"), str(env.get("runner_platform", t("replay.not_recorded", "Not recorded")))],
        [
            t("replay.env.container", "Container"),
            f"Docker {env.get('docker_server_version', t('replay.not_recorded', 'Not recorded'))} / {env.get('docker_os', t('replay.not_recorded', 'Not recorded'))}",
        ],
        *[[engine_label(engine), str(versions.get(engine, t("replay.not_recorded", "Not recorded")))] for engine in engines],
        [
            t("replay.env.quota", "Per-browser quota"),
            t("replay.env.quota_value", "{cpus} CPU / {memory} / isolated network", cpus=p.get('cpus_per_browser', '?'), memory=p.get('memory_per_browser', '?')),
        ],
        [t("curated.method.executions", "Executions"), t("curated.method.executions_value", "One formal result per task and provider")],
        [t("replay.env.viewport", "Viewport"), json.dumps(p.get("viewport") or {}, ensure_ascii=False)],
        [
            t("curated.method.failure_review", "Failure review"),
            t("curated.method.failure_review_value", "On failure, up to {retries} more same-task evidence records are added, not counted as new tasks", retries=p.get('max_failure_retries', '?')),
        ],
        [t("replay.env.sampling", "Sampling"), t("replay.env.sampling_value", "Memory, PID, CPU and network counters sampled every {interval} ms", interval=p.get('sample_interval_ms', '?'))],
    ]
    alternation = (
        t("curated.method.alternation", ", alternating the execution order of {count} providers per task", count=_provider_count(engines)) if len(engines) > 1 else ""
    )
    ratio_definition = (
        f'<li>{t("curated.method.ratio_definition", """The paired ratio definition and per-item computation are in the <a href="trajectory-replay-evaluation-latest.html#performance">home performance tables and audit</a>.""")}</li>'
        if len(engines) > 1
        else ""
    )
    return f"""<section class="card note"><h2>{t("curated.method.step_title", "Strict definition of a step")}</h2><p>{t("curated.method.step_body", "<strong>One step = one CowAgent BrowserTool-equivalent call</strong>, such as navigate, click, get_text, fill, select, scroll, back or forward. Waiting for page readiness and target values happens inside that tool call and is not listed or counted separately. The arguments, duration, output and status of each call are written to the execution trace.")}</p></section><section class="card" id="scoring"><h2>{t("curated.method.scoring_title", "Success verdict: two independent programmatic metrics")}</h2><ol><li>{t("curated.method.scoring_result", "<strong>Result correct (primary):</strong> each annotated output must exactly equal <code>expected_outputs</code> as a string; no fuzzy matching or model scoring; Codex only took part in annotation review before the evaluation.")}</li><li>{t("curated.method.scoring_combined", "<strong>Result and reference process both correct (secondary):</strong> beyond a correct result, the replayed reference action sequence must match <code>expected_actions</code> and every BrowserTool call must succeed.")}</li></ol><p>{t("curated.method.scoring_boundary", "<strong>Boundary:</strong> <code>expected_actions</code> is the fixed reference path of a tool-layer microbenchmark and does not mean the task has only one solution. To evaluate a freely planning CowAgent, change the process verdict to required milestones, forbidden actions and a step budget instead of requiring the whole sequence to match step by step.")}</p></section><section class="card"><h2>{t("replay.section.environment", "Environment")}</h2>{_table([t("replay.column.item", "Item"), t("replay.column.recorded", "Recorded value")], rows, t("replay.env.this_round", "Environment of this run"))}</section><section class="card"><h2>{t("replay.section.flow", "Execution flow")}</h2><ol><li>{t("curated.method.flow_start", "Start and warm up {count} browsers separately; browser processes and contexts stay running for the whole evaluation.", count=_provider_count(engines))}</li><li>{t("replay.method.flow_clear", "Before each task, clear cookies, permissions, HTTP cache, Cache Storage, Service Workers, IndexedDB, origin storage and history, and create a new page.")}</li><li>{t("curated.method.flow_settle", "Settle for {settle} ms outside timing{alternation}.", settle=p.get("post_reset_settle_ms", "?"), alternation=alternation)}</li><li>{t("curated.method.flow_run", "Run the complete task with sampling; failure review belongs to the same task detail.")}</li><li>{t("curated.method.flow_baseline", "The resource baseline only compares same tasks {parties} passed; failure and review samples are kept out of the normal path.", parties=_all_parties(engines))}</li></ol></section><section class="card"><h2>{t("replay.section.statistics", "Statistical scope")}</h2><ul><li>{t("curated.method.stats_p50", "P50 and the median are the same statistic; cross-task results report P50, mean and nearest-rank P95.")}</li>{ratio_definition}<li>{t("curated.method.stats_single", "Each task has only one formal sample, so no per-task quantiles are computed; the current cross-task points cannot prove a general performance advantage.")}</li><li>{t("curated.method.stats_memory", "Mean memory is time-weighted; task memory P95 comes from time-series samples of one task run.")}</li><li>{t("curated.method.stats_network", "RX = Receive, TX = Transmit; totals include page resources and CDP, and rates come from differences of adjacent samples.")}</li></ul></section>"""


METRICS = (
    (
        t("replay.metric_group.time", "Time and compute"),
        (
            ("duration_ms", t("curated.metric.duration", "Total duration"), 1, "ms"),
            ("cpu_ms", t("replay.metric.cpu_time", "CPU time"), 1, "ms"),
            ("cpu_average_utilization_pct", t("replay.metric.cpu_average", "Average CPU per core"), 1, "%"),
            ("cpu_sampled_utilization_p95_pct", t("replay.metric.cpu_p95", "Within-task CPU P95"), 1, "%"),
        ),
    ),
    (
        t("replay.metric_group.memory", "Memory"),
        (
            ("memory_mean_bytes", t("replay.metric.memory_mean", "Time-weighted mean memory"), 1048576, "MiB"),
            ("memory_p95_bytes", t("replay.metric.memory_p95", "Task memory P95"), 1048576, "MiB"),
            ("memory_peak_bytes", t("replay.metric.memory_peak", "Peak memory"), 1048576, "MiB"),
            ("memory_retained_bytes", t("replay.metric.memory_retained", "Retained at end"), 1048576, "MiB"),
            ("memory_mib_seconds", t("replay.metric.memory_integral", "Memory-time integral"), 1, "MiB·s"),
        ),
    ),
    (
        t("replay.metric_group.network", "Network"),
        (
            ("network_rx_bytes", t("replay.metric.rx_total", "Total received RX"), 1024, "KiB"),
            ("network_tx_bytes", t("replay.metric.tx_total", "Total sent TX"), 1024, "KiB"),
            ("network_rx_bytes_per_second", t("replay.metric.rx_rate", "Mean receive rate"), 1024, "KiB/s"),
            ("network_rx_rate_p95_bytes_per_second", t("replay.metric.rx_rate_p95", "Within-task receive rate P95"), 1024, "KiB/s"),
            ("network_tx_rate_p95_bytes_per_second", t("replay.metric.tx_rate_p95", "Within-task send rate P95"), 1024, "KiB/s"),
        ),
    ),
)


def _metric_tables(summary: dict[str, Any]) -> str:
    absolute = "".join(
        _grouped_resource_table(summary, metrics, t("curated.metric.absolute_caption", "{group} absolute values (lower is better for all)", group=group))
        for group, metrics in METRICS
    )
    pairs = _ratio_pairs(summary, _engines(summary))
    if not pairs:
        return absolute
    paired = "".join(
        _paired_ratio_table(
            values,
            metrics,
            t("curated.metric.paired_caption", "{group} per-task {pair} paired ratios (lower is better for all)", group=group, pair=_pair_label(comparison, baseline)),
        )
        for comparison, baseline, values in pairs
        for group, metrics in METRICS
    )
    formulas = t("replay.separator.list", ", ").join(
        f"<code>{_pair_label(comparison, baseline)}</code>" for comparison, baseline, _ in pairs
    )
    return (
        absolute
        + f"<h3>{t('curated.metric.paired_title', 'Distribution of per-task paired ratios')}</h3><p>{t('curated.metric.paired_body', 'First compute {formulas} for each same task, then aggregate those ratios; P50, P95 and mean all come from the same per-task ratios and are not ratios of the marginal quantiles of two providers.', formulas=formulas)}</p>"
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
            t("replay.status.pass", "Pass")
            if value == "pass" or value is True
            else t("replay.status.fail", "Fail")
            if value == "fail" or value is False
            else t("replay.not_recorded", "Not recorded")
        )

    final = attempts[-1]
    source = spec.get("source") or {}
    url = str(source.get("url") or "")
    safe_url = url if urlparse(url).scheme in {"http", "https"} else "#"
    labels = [t("curated.detail.record_label", "Execution record {attempt}", attempt=a.get('attempt', i)) for i, a in enumerate(attempts, 1)]
    total = sum(float(a.get("duration_ms") or 0) for a in attempts)
    result = final.get("result_status") or final.get("status")
    process = final.get("process_status") or final.get("status")
    overview = (
        '<section class="card case-overview" id="case-result">'
        f'<div class="section-heading"><h1>{_e(title)}</h1><span class="chip">{_e(engine)}</span></div>'
        f'<p class="muted case-id">{_e(task_id)}</p>'
        '<div class="case-facts">'
        f'<div><span>{t("replay.detail.result", "Result")}</span><strong class="{"ok" if result == "pass" else "bad"}">{status_cell(result)}</strong></div>'
        f'<div><span>{t("curated.detail.reference_process", "Reference process")}</span><strong class="{"ok" if process == "pass" else "bad"}">{status_cell(process)}</strong></div>'
        f"<div><span>{t('curated.detail.total_duration', 'Total duration · {count} executions', count=len(attempts))}</span><strong>{_show(total, suffix=' ms')}</strong></div></div>"
    )
    if diagnostic:
        overview += (
            '<div class="diagnosis-panel">'
            f"<strong>{_e(diagnostic.get('category') or t('replay.detail.other', 'Other'))}</strong>"
            f"<span>{t('replay.detail.failed_step', 'Step {step} · {action}', step=_e(diagnostic.get('failed_step')), action=_e(diagnostic.get('failed_action')))}"
            f" · <code>{_e(diagnostic.get('selector') or '—')}</code></span>"
            + (
                f"<p>{_e(diagnostic['observed_mechanism'])}</p>"
                if diagnostic.get("observed_mechanism")
                else ""
            )
            + evidence_link(diagnostic.get("actionability_probe"), "Actionability probe")
            + "</div>"
        )
    overview += f'<p class="case-start">{t("curated.detail.start_page", "Start page")} <a href="{_e(safe_url)}" target="_blank" rel="noopener">{_e(url)}</a></p></section>'

    expected = (spec.get("ground_truth") or {}).get("expected_outputs") or {}
    validations = [{str(v.get("output")): v for v in a.get("validation") or []} for a in attempts]
    outputs = list(dict.fromkeys([*expected, *(k for values in validations for k in values)]))
    answer_rows = []
    for output in outputs:
        expected_value = expected.get(
            output, next((v[output].get("expected") for v in validations if output in v), t("replay.detail.undeclared", "Not declared"))
        )
        cells = [output, str(expected_value)]
        for values in validations:
            item = values.get(output)
            cells.append(
                ComparedValue(
                    f"{t('replay.detail.empty_string', '(empty string)') if item.get('actual') == '' else item.get('actual')} · {status_cell(item.get('passed'))}",
                    "ok"
                    if item.get("passed") is True
                    else "bad"
                    if item.get("passed") is False
                    else "",
                )
                if item
                else t("replay.detail.not_produced", "Not produced (process failed) · Not judged")
            )
        answer_rows.append(cells)
    answers = (
        _table([t("replay.detail.output", "Output"), t("curated.detail.answer_column", "Standard answer"), *labels], answer_rows, t("curated.detail.answer_caption", "Answer comparison"), source_marks=True)
        if outputs
        else f'<p class="muted">{t("curated.detail.no_answer", "No standard answer declared.")}</p>'
    )
    answers += _source_mark_note(expected)
    derivation = (spec.get("ground_truth") or {}).get("derivation")
    answers += f'<p class="muted">{t("replay.detail.answer_basis", "Answer basis: {derivation}", derivation=_e(derivation))}</p>' if derivation else ""

    calls = spec.get("tool_calls") or []
    traces = [a.get("trace") or [] for a in attempts]
    steps = []
    for i in range(max([len(calls), *(len(t) for t in traces)], default=0)):
        call = calls[i] if i < len(calls) else {}
        reference = (
            f'<code class="action-name">{_e(call.get("action"))}</code><span>{_e(_action_text(call))}</span>'
            if call
            else t("replay.detail.extra_call", "Extra call")
        )
        parameters = {k: v for k, v in call.items() if k != "save_as"}
        if call:
            reference += evidence_link(parameters, t("replay.detail.step_arguments", "Step {step} arguments", step=i + 1), t("replay.detail.arguments", "Arguments"))
            if call.get("save_as"):
                reference += f"<small>{t('replay.detail.saved_as', 'Saved as <code>{name}</code>', name=_e(call['save_as']))}</small>"
        cells = [f"<td>{i + 1}</td>", f"<td>{reference}</td>"]
        for label, trace, attempt in zip(labels, traces, attempts, strict=True):
            if i >= len(trace):
                cells.append(f'<td class="muted">{t("replay.detail.not_executed", "Not executed")}</td>')
                continue
            actual = trace[i]
            passed = actual.get("status") == "pass"
            cell = f'<code class="action-name" aria-label="{t("replay.detail.actual_action", "Actual browser action")}">{_e(actual.get("action"))}</code><strong class="{"ok" if passed else "bad"}">{status_cell(actual.get("status"))}</strong> <span class="muted">{_show(actual.get("duration_ms"), suffix=" ms")}</span>'
            if actual.get("action") != call.get("action"):
                cell += f'<small class="bad">{t("replay.detail.differs_from_reference", "Differs from the reference action")}</small>'
            if (actual.get("arguments") or {}) != parameters:
                cell += evidence_link(
                    actual.get("arguments") or {}, t("replay.detail.label_step_arguments", "{label} · Step {step} arguments", label=label, step=i + 1), t("replay.detail.actual_arguments", "Actual arguments")
                )
            cell += evidence_link(actual.get("output"), t("replay.detail.label_step_output", "{label} · Step {step} output", label=label, step=i + 1), t("replay.detail.output", "Output"))
            if actual.get("error"):
                # The full exception contains the call error; keep the full payload once.
                error = attempt.get("error")
                if not error or _trace_text(actual["error"]) not in _trace_text(error):
                    cell += evidence_link(actual["error"], t("replay.detail.label_step_error", "{label} · Step {step} error", label=label, step=i + 1), t("replay.detail.exception", "Exception"))
                else:
                    cell += evidence_link(error, t("replay.detail.label_full_exception", "{label} · Full exception", label=label), t("replay.detail.full_exception", "Full exception"))
            cells.append(f"<td>{cell}</td>")
        steps.append("<tr>" + "".join(cells) + "</tr>")
    process_cells = []
    for label, attempt in zip(labels, attempts, strict=True):
        pv = attempt.get("process_validation") or {}
        process_cells.append(
            "<td>"
            + status_cell(pv.get("passed"))
            + evidence_link(pv, t("replay.detail.label_process_verdict", "{label} · Process verdict", label=label), t("replay.detail.verdict_details", "Verdict details"))
            + "</td>"
        )
    steps.append(f"<tr><td>—</td><td>{t('replay.detail.process_verdict', 'Process verdict')}</td>" + "".join(process_cells) + "</tr>")
    step_table = (
        f'<div class="table-wrap step-comparison"><table><caption>{t("replay.detail.step_comparison", "Reference actions against execution")}</caption><thead><tr><th>{t("replay.detail.step", "Step")}</th><th>{t("replay.detail.reference_action", "Browser reference action")}</th>'
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
            + (evidence_link(attempt.get("error"), t("replay.detail.label_full_exception", "{label} · Full exception", label=label)) or t("replay.detail.no_exception", "No exception"))
            + "</td></tr>"
        )
    execution_table = (
        f'<div class="table-wrap"><table><caption>{t("replay.detail.execution_records", "Execution records")}</caption><thead><tr><th>{t("replay.detail.record", "Record")}</th><th>{t("curated.detail.duration", "Duration")}</th><th>{t("replay.detail.exception_evidence", "Exception evidence")}</th></tr></thead><tbody>'
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
        _table([t("replay.column.metric", "Metric"), *labels], resource_rows, t("replay.detail.resource_comparison", "Resource comparison"))
        if resource_rows
        else f'<p class="muted">{t("replay.detail.no_resource_samples", "No resource samples recorded.")}</p>'
    )
    raw = "".join(
        f'<article class="evidence-item" id="{anchor}"><div class="section-heading"><h3>{_e(anchor.replace("evidence-", t("replay.evidence.label_prefix", "Evidence ")))}</h3><a href="#case-steps">{t("replay.evidence.back_to_steps", "Back to steps")}</a></div><p class="muted">{_e(" / ".join(refs))}</p><pre tabindex="0" aria-label="{_e(refs[0])}">{_e(text)}</pre></article>'
        for text, (anchor, refs) in evidence.items()
    )
    evidence_index = (
        '<div class="evidence-index">'
        + "".join(
            f'<a class="evidence-link" href="#{anchor}" data-evidence="{anchor}" aria-label="{_e(refs[0])}"><strong>{_e(anchor.replace("evidence-", t("replay.evidence.label_prefix", "Evidence ")))}</strong><span>{_e(" / ".join(refs))}</span><span>{t("replay.evidence.view", "View")}</span></a>'
            for _, (anchor, refs) in evidence.items()
        )
        + "</div>"
    )
    dialog = (
        '<dialog id="evidence-dialog" aria-labelledby="evidence-title" aria-describedby="evidence-context">'
        f'<div class="dialog-heading"><div><h2 id="evidence-title">{t("replay.evidence.raw", "Raw evidence")}</h2>'
        f'<p class="muted" id="evidence-context">{_e(engine)} · {_e(title)}</p></div>'
        f'<form method="dialog"><button type="submit" autofocus aria-label="{t("replay.evidence.close_label", "Close evidence window")}">{t("replay.evidence.close", "Close")}</button></form></div>'
        f'<p id="evidence-action" class="muted"></p><pre id="evidence-content" tabindex="0" aria-label="{t("replay.evidence.full_content", "Complete evidence content")}"></pre></dialog>'
    )
    script = f"<script>{EVIDENCE_DIALOG_JS}</script>"
    return (
        '<div class="case-detail">'
        + overview
        + f'<nav class="case-nav" aria-label="{t("replay.detail.nav_label", "Task detail navigation")}"><a href="#case-result">{t("replay.detail.result", "Result")}</a><a href="#case-steps">{t("replay.detail.actions_and_answers", "Actions and answers")}</a><a href="#case-resources">{t("replay.detail.execution_resources", "Execution resources")}</a><a href="#case-evidence">{t("replay.evidence.raw", "Raw evidence")}</a></nav>'
        + f'<section class="card" id="case-steps"><h2>{t("replay.detail.actions_and_answers", "Actions and answers")}</h2>'
        + answers
        + step_table
        + _source_references(source)
        + "</section>"
        + f'<section class="card" id="case-resources"><h2>{t("replay.detail.execution_resources", "Execution resources")}</h2>'
        + execution_table
        + resources
        + "</section>"
        + f'<section class="card" id="case-evidence"><h2>{t("replay.evidence.raw", "Raw evidence")}</h2>'
        + (evidence_index + raw if raw else f"<p>{t('replay.detail.no_trace', 'No execution trace recorded.')}</p>")
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
        ): str(group.get("category") or t("replay.detail.other", "Other"))
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
            error_category = ",".join(categories) if categories else t("replay.execution.no_error", "No error")
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
                f'<div id="{_e(anchor)}" class="execution-card" data-task="{_e(task_id)}" data-engine="{_e(engine)}" data-status="{_e(final_result_status)}" data-combined-status="{_e(final_combined_status)}" data-complexity="{_e(task.get("complexity"))}" data-error="{_e(error_category)}" data-actions="{_e(",".join(actions))}" data-tags="{_e(",".join(tags))}" data-search="{_e(_trace_text(search_text))}" data-duration="{_e(total_duration)}"><a href="{detail_name}" class="execution-summary"><span>{_e(title)}</span><span>{_e(engine)}</span><span class="{"ok" if final_result_status == "pass" else "bad"}">{t("replay.execution.correct", "Correct") if final_result_status == "pass" else t("replay.execution.incorrect", "Incorrect")}</span><span class="{"ok" if final_process_status == "pass" else "bad"}">{t("replay.execution.correct", "Correct") if final_process_status == "pass" else t("replay.execution.incorrect", "Incorrect")}</span><span>{_e(error_category)}</span><span>{_show(total_duration, suffix=" ms")}</span></a></div>'
            )
    return "".join(blocks) or f"<p>{t('replay.execution.none', 'No execution samples to show.')}</p>"


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
            str(group.get("category") or t("replay.detail.other", "Other"))
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
        category = str(group.get("category") or t("replay.detail.other", "Other"))
        category_counts.setdefault(category, [0, 0])
        category_counts[category][0] += 1
        category_counts[category][1] += len(group.get("attempts") or [])
    category_rows = [
        [name, str(values[0]), str(values[1])] for name, values in category_counts.items()
    ] or [[t("curated.analysis.no_failures", "No failures"), "0", "0"]]
    error_options = f'<option value="{t("replay.execution.no_error", "No error")}">{t("replay.execution.no_error", "No error")}</option>' + "".join(
        f'<option value="{_e(category)}">{_e(category)}</option>'
        for category in diagnostic_categories
    )
    engines = _engines(summary)
    engine_options = "".join(f"<option>{engine}</option>" for engine in engines)
    filters = f"""<div class="filters" id="execution-filters" aria-label="{t("replay.execution.filters_label", "Execution sample filters")}">
      <label>{t("replay.column.task", "Task")}<select id="f-task"><option value="">{t("replay.filter.all_tasks", "All tasks")}</option>{task_options}</select></label>
      <label>Provider<select id="f-engine"><option value="">{t("curated.analysis.all_providers", "All providers")}</option>{engine_options}</select></label>
      <label>{t("replay.detail.result", "Result")}<select id="f-status"><option value="">{t("replay.filter.all_results", "All results")}</option><option value="pass">{t("replay.execution.correct", "Correct")}</option><option value="fail" selected>{t("replay.execution.incorrect", "Incorrect")}</option></select></label>
      <label>{t("curated.analysis.result_and_process", "Result and process")}<select id="f-combined"><option value="">{t("replay.filter.all", "All")}</option><option value="pass">{t("curated.analysis.all_correct", "Both correct")}</option><option value="fail">{t("curated.analysis.not_all_correct", "Not both correct")}</option></select></label>
      <label>{t("replay.filter.difficulty", "Difficulty")}<select id="f-complexity"><option value="">{t("replay.filter.all_difficulties", "All difficulties")}</option><option value="simple">{t("replay.filter.simple", "Simple")}</option><option value="medium">{t("replay.filter.medium", "Medium")}</option><option value="complex">{t("replay.filter.complex", "Complex")}</option></select></label>
      <label>{t("replay.filter.browser_tool_method", "BrowserTool method")}<select id="f-action"><option value="">{t("replay.filter.all_methods", "All methods")}</option>{action_options}</select></label>
      <label>{t("replay.filter.task_tag", "Task tag")}<select id="f-tag"><option value="">{t("replay.filter.all_tags", "All tags")}</option>{tag_options}</select></label>
      <label>{t("replay.filter.error_category", "Error category")}<select id="f-error"><option value="">{t("replay.filter.all_categories", "All categories")}</option>{error_options}</select></label>
      <label>{t("curated.detail.duration", "Duration")}<select id="f-duration"><option value="">{t("replay.filter.all_durations", "All durations")}</option><option value="lt1000">{t("replay.filter.under_1s", "Under 1 s")}</option><option value="1000-5000">{t("replay.filter.between_1_and_5s", "1-5 s")}</option><option value="gte5000">{t("replay.filter.over_5s", "5 s or more")}</option></select></label>
      <label>{t("replay.filter.full_text", "Full-text search")}<input id="f-query" type="search" placeholder="{t("replay.filter.full_text_placeholder", "Task, output or error")}"></label>
      <button type="button" id="f-all">{t("replay.filter.show_all", "Show all")}</button><button type="button" id="f-reset">{t("replay.filter.reset_to_errors", "Reset to errors")}</button><span class="filter-status" id="f-count" aria-live="polite"></span>
    </div>"""
    provider_list = "[" + ",".join(f"'{engine}'" for engine in engines) + "]"
    script = f"<script>{EXECUTION_FILTER_JS}</script>".replace("__PROVIDER_LIST__", provider_list)
    grid_head = f'<div class="record-grid-head" aria-hidden="true"><span>{t("replay.column.task", "Task")}</span><span>Provider</span><span>{t("replay.detail.result", "Result")}</span><span>{t("curated.analysis.process", "Process")}</span><span>{t("replay.filter.error_category", "Error category")}</span><span>{t("curated.metric.duration", "Total duration")}</span></div>'
    return f"""<section class="card" id="execution-results"><h2>{t("curated.analysis.title", "Per-task analysis and raw evidence")}</h2><p>{t("curated.analysis.body", "{records} task × provider records, {executions} executions. Open a task to see its complete actions, answers, verdicts and trace.", records=logical_executions, executions=physical_attempts)}</p>{_table([t("curated.analysis.rule_category", "Rule category"), t("curated.analysis.failure_groups", "Deduplicated failure groups"), t("curated.analysis.failure_count", "Actual failures")], category_rows, t("curated.analysis.failure_caption", "Programmatic failure distribution"))}{filters}<div id="execution-list" class="record-grid">{grid_head}{_execution_details(manifest, summary, detail_pages)}</div><p id="execution-empty" class="empty-state" role="status" hidden></p>{script}</section>"""


def _classify(error: str) -> str:
    text = error.lower()
    if "timeout" in text and ("visible" in text or "click" in text):
        return t("curated.classify.interaction", "Interaction target timeout/not visible")
    if "timeout" in text:
        return t("curated.classify.timeout", "Timeout")
    if "exact mismatch" in text:
        return t("curated.classify.mismatch", "Result mismatch")
    if "net::" in text or "navigation" in text:
        return t("curated.classify.navigation", "Navigation/network")
    if "selector" in text or "locator" in text:
        return t("curated.classify.locator", "Locator")
    return t("replay.detail.other", "Other")


def _analysis_page(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    timing_study = MOLI_TIMING_STUDY if "moli" in _engines(summary) else ""
    return f"""{timing_study}{_execution_explorer(manifest, summary, detail_pages)}"""


def _appendix_page(manifest: dict[str, Any], engines: tuple[str, ...]) -> str:
    p = manifest.get("protocol") or {}
    provenance = manifest.get("artifact_provenance") or {}
    fields = [
        ["task", "ground_truth / source / expected_actions", t("replay.appendix.field.task", "Unique result, fixed reference path and source")],
        [
            "attempt",
            "result_status / process_status / combined_status",
            t("curated.appendix.status_fields", "Primary result metric and secondary result+process metric"),
        ],
        ["attempt", "validation / trace / error", t("replay.appendix.field.validation", "Verdict and complete execution evidence of one task")],
        ["resource", "memory mean/p95/peak/retained/MiB·s", t("replay.appendix.field.memory", "Memory level, tail, peak, retention and integral")],
        ["resource", "CPU / network total/average/p95/peak", t("replay.appendix.field.cpu_network", "Compute and network timing pressure")],
        (
            [
                "summary",
                "paired intersection / paired ratios",
                t("curated.appendix.paired", "Tasks {parties} passed and per-task paired ratio audit", parties=_all_parties(engines)),
            ]
            if len(engines) > 1
            else ["summary", "paired intersection", t("curated.appendix.intersection", "Tasks {parties} passed", parties=_all_parties(engines))]
        ),
        ["diagnostics", "rule_engine", t("replay.appendix.field.diagnostics", "Programmatic failure classification, evidence and raw execution record entry point")],
    ]
    provenance_rows = [
        [t("replay.appendix.run_id", "Run ID"), str(manifest.get("run_id") or t("replay.not_recorded", "Not recorded"))],
        [t("replay.appendix.generated_at", "Generated at"), str(manifest.get("created_at") or t("replay.not_recorded", "Not recorded"))],
        [t("replay.appendix.suite_sha", "Suite SHA-256"), str(manifest.get("suite_sha256") or t("replay.not_recorded", "Not recorded"))],
        ["attempts.jsonl SHA-256", str(provenance.get("attempts_sha256") or t("replay.not_recorded", "Not recorded"))],
        ["summary.json SHA-256", str(provenance.get("summary_sha256") or t("replay.not_recorded", "Not recorded"))],
    ]
    return f"""<section class="card"><h2>{t("curated.appendix.steps_title", "Reproduction steps")}</h2><ol><li>{t("curated.appendix.step_docker", "Prepare Docker Engine, Python 3.11 and at least 4 CPU/6 GiB memory.")}</li><li>{t("curated.appendix.step_run", "Run <code>./examples/trajectory_replay/run.sh</code>; the script runs the full benchmark and generates raw records, summary data and paged HTML in turn.")}</li><li>{t("curated.appendix.step_retry", "To add one more same-task evidence record for failed tasks, set <code>TRAJECTORY_REPLAY_FAILURE_RETRIES=1</code>; that record is not counted as a new task.")}</li><li>{t("curated.appendix.step_rebuild", "To recompute all metrics and the report from an existing <code>attempts.jsonl</code>, run <code>uv run python examples/trajectory_replay/rebuild_report.py</code>.")}</li><li>{t("curated.appendix.step_check", "Inspect <code>attempts.jsonl</code>, <code>manifest.json</code>, <code>summary.json</code> and the paged HTML; all {ratios}quantiles and means are computed by repository scripts, and Codex takes no part in the statistics.", ratios=t("curated.appendix.ratios", "ratios, ") if len(engines) > 1 else "")}</li></ol><p><a href="trajectory-replay-evaluation-latest-data.json">{t("curated.appendix.download", "Download the machine-readable audit summary of this run")}</a></p></section><section class="card"><h2>{t("replay.appendix.binding", "Run and raw data binding")}</h2>{_table([t("replay.column.item", "Item"), t("replay.column.value", "Value")], provenance_rows, t("replay.appendix.verifiable_source", "Verifiable source"))}</section><section class="card"><h2>{t("replay.appendix.data_fields", "Data fields")}</h2>{_table([t("replay.column.object", "Object"), t("replay.column.key_fields", "Key fields"), t("replay.column.purpose", "Purpose")], fields, t("replay.appendix.data_structure", "Data structure"))}</section><section class="card"><h2>{t("replay.appendix.limits", "Reproduction limits")}</h2><ul><li>{t("curated.appendix.limit_public", "All tasks come from the public web, so changes in page content, network state or site policy can make the dataset drift; verify the dataset SHA and annotation evidence before running.")}</li><li>{t("curated.appendix.limit_sampling", "The sampling interval is {interval} ms; spikes shorter than the interval can be missed.", interval=p.get("sample_interval_ms", "?"))}</li><li>{t("curated.appendix.limit_memory", "Browser processes keep running and state is cleared between tasks; allocator-retained memory is not cache pollution.")}</li></ul></section><section class="card"><h2>{t("replay.appendix.raw_environment", "Raw environment JSON")}</h2><details><summary>{t("replay.appendix.expand", "Expand")}</summary><pre>{_e(json.dumps(manifest.get("environment") or {}, ensure_ascii=False, indent=2, sort_keys=True))}</pre></details></section>"""


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    paired_engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}

    def paired_duration(engine: str, stat: str) -> str:
        return _stat(((paired_engines.get(engine) or {}).get("duration_ms") or {}), stat)

    lines = [
        t("curated.markdown.title", "# {names} browser tool-call evaluation", names=engine_names(_engines(summary))),
        "",
        t("replay.markdown.run_id", "Run ID: `{run_id}`", run_id=manifest.get('run_id', 'unknown')),
        "",
        t("curated.markdown.body", "This is a deterministic BrowserTool-equivalent call microbenchmark; success verdicts and failure classification are both programmatic."),
        "",
        t("curated.markdown.header", "| Provider | Result correct | Result and reference process both correct | P50 ms | Mean ms | P95 ms |"),
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
