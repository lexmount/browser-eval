"""Chinese paginated evidence-first browser benchmark report."""

from __future__ import annotations

import json
import shlex
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.distribution import bundled_data_path
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
from harness.trajectory_replay.public_webarena.publication import REPORT_STEM, public_projection
from harness.trajectory_replay.public_webarena.result_audit import audit_result_set

ASSETS = Path(__file__).parents[1] / "assets"
REPORT_CSS = (ASSETS / "public-webarena-report.css").read_text(encoding="utf-8")
BENCHMARK_TABS_JS = (ASSETS / "public-webarena-benchmark-tabs.js").read_text(encoding="utf-8")
COMPARISON_JS = (ASSETS / "public-webarena-comparison.js").read_text(encoding="utf-8")
EXECUTION_FILTER_JS = (ASSETS / "public-webarena-execution-filter.js").read_text(encoding="utf-8")

# This renderer generates only the Chinese edition.
t = report_text("zh")

PAGES = tuple(
    (slug, label, f"{REPORT_STEM}{suffix}.html")
    for slug, label, suffix in (
        ("index", t("replay.page.summary", "Summary"), ""),
        ("results", t("public.page.results", "Result analysis"), "-results"),
        ("suite", t("replay.page.suite", "Suite"), "-suite"),
        ("research", t("public.page.research", "Selection rationale"), "-research"),
        ("method", t("replay.page.method", "Environment and method"), "-method"),
        ("appendix", t("replay.page.appendix", "Reproduction and appendix"), "-appendix"),
    )
)

CATEGORY_LABELS = {
    "end_to_end_browser_use": t("public.category.end_to_end", "End-to-end browser-use"),
    "computer_use": t("public.category.computer_use", "General computer-use"),
    "gui_grounding": "GUI grounding",
    "web_research": t("public.category.web_research", "Web search and research"),
    "offline_action_prediction": t("public.category.offline", "Offline web action prediction"),
}

def _public_functional(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        engine: {
            "tasks": int(values.get("tasks") or 0),
            "passes": int(values.get("result_final_passes", values.get("passes", 0)) or 0),
            "pass_rate": values.get("result_final_pass_rate", values.get("pass_rate")),
        }
        for engine, values in (summary.get("functional") or {}).items()
    }


def _public_resource_comparison(summary: dict[str, Any]) -> dict[str, Any]:
    comparison = summary.get("resource_comparison") or {}
    return {
        "scope": comparison.get("scope"),
        "intersection_results": comparison.get(
            "intersection_results", comparison.get("intersection_attempts", 0)
        ),
        "by_engine": comparison.get("by_engine") or {},
    }


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    divisor: float
    unit: str
    preference: str
    interpretation: str


def _metric_policy(spec: MetricSpec) -> tuple[str, str]:
    return {
        "lower": (t("public.policy.lower", "Lower is better"), "lower"),
        "higher": (t("public.policy.higher", "Higher is better"), "higher"),
        "neutral": (t("public.policy.neutral", "Observation"), "neutral"),
    }[spec.preference]


def _validate_metric_contract() -> None:
    specs = [spec for _, metrics in METRICS for spec in metrics]
    keys = [spec.key for spec in specs]
    if len(keys) != len(set(keys)):
        raise ValueError("resource metric keys must be unique")
    for spec in specs:
        if spec.preference not in {"lower", "higher", "neutral"}:
            raise ValueError(f"unknown preference for {spec.key}: {spec.preference}")
        if not spec.interpretation.strip():
            raise ValueError(f"resource metric interpretation is required: {spec.key}")


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


def _grouped_resource_table(
    summary: dict[str, Any],
    metrics: tuple[MetricSpec, ...],
    caption: str,
) -> str:
    values_by_engine = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    engines = summary_engines(summary)
    body = []
    for spec in metrics:
        policy_label, policy_class = _metric_policy(spec)
        cells = [
            f'<td data-label="{t("replay.column.metric", "Metric")}"><span>{_e(f"{spec.label} ({spec.unit})")}</span>'
            f'<small class="metric-policy {policy_class}" title="{_e(spec.interpretation)}">'
            f'{_e(policy_label)}</small></td>'
        ]
        for stat in ("p50", "p95", "mean"):
            displayed = {
                engine: _display_number(
                    _numeric_stat(
                        ((values_by_engine.get(engine) or {}).get(spec.key) or {}), stat
                    ),
                    spec.divisor,
                )
                for engine in engines
            }
            available = [value for value in displayed.values() if value is not None]
            best = (
                min(available)
                if spec.preference == "lower" and available
                else max(available)
                if spec.preference == "higher" and available
                else None
            )
            unique_best = best is not None and available.count(best) == 1
            for engine in engines:
                value = _numeric_stat(
                    ((values_by_engine.get(engine) or {}).get(spec.key) or {}), stat
                )
                shown = displayed[engine]
                css_class = "better" if unique_best and shown == best else ""
                class_attr = f' class="{css_class}"' if css_class else ""
                cells.append(
                    f'<td data-label="{_e(stat.upper())} · {_e(engine_label(engine))}"'
                    f'{class_attr}>{_e(_show(value, spec.divisor))}</td>'
                )
        body.append(
            f'<tr data-metric="{_e(spec.key)}" data-preference="{_e(spec.preference)}">'
            + "".join(cells)
            + "</tr>"
        )
    group_headers = "".join(
        f'<th colspan="{len(engines)}">{label}</th>'
        for label in ("P50", "P95", t("replay.stat.mean", "Mean"))
    )
    engine_headers = "".join(
        f"<th>{_e(engine_label(engine))}</th>"
        for _ in range(3)
        for engine in engines
    )
    return (
        f'<div class="table-wrap resource-summary" role="region" aria-label="{_e(caption)}" tabindex="0"><table><caption>{_e(caption)}</caption>'
        f'<thead><tr><th rowspan="2">{t("replay.column.metric", "Metric")}</th>{group_headers}</tr><tr>{engine_headers}</tr></thead><tbody>'
        + "".join(body)
        + "</tbody></table></div>"
    )


def _relative_ratio_tables(
    summary: dict[str, Any],
    metrics: tuple[MetricSpec, ...],
) -> str:
    comparison = summary.get("resource_comparison") or {}
    relative = comparison.get("relative_ratios") or {}
    baseline = str(relative.get("baseline_engine") or "")
    by_engine = relative.get("by_engine") or {}
    if not baseline or not by_engine:
        paired = comparison.get("paired_ratios") or {}
        baseline = str(paired.get("denominator_engine") or "")
        numerator = str(paired.get("numerator_engine") or "")
        if baseline and numerator:
            by_engine = {numerator: paired.get("metrics") or {}}
    if not baseline or not by_engine:
        return ""

    modules = []
    for engine, ratio_metrics in by_engine.items():
        rows = []
        for spec in metrics:
            values = ratio_metrics.get(spec.key) or {}
            policy_label, _ = _metric_policy(spec)
            cells: list[Any] = [f"{spec.label} · {policy_label}"]
            for stat in ("p50", "p95", "mean"):
                value = _numeric_stat(values, stat)
                css = ""
                if spec.preference != "neutral" and isinstance(value, (int, float)):
                    css = _comparison_class(
                        value, 1, higher=spec.preference == "higher"
                    )
                cells.append(ComparedValue(_show(value, suffix="×"), css))
            rows.append(cells)
        pair = f"{engine_label(str(engine))} / {engine_label(baseline)}"
        modules.append(
            f'<details class="ratio-module" data-formula="per-task-ratio"><summary>{t("public.ratio.summary", "Per-task ratio · {pair}", pair=_e(pair))}</summary>'
            f'<p class="muted">{t("public.ratio.note", "Divide per task, then aggregate; 0.50× is half the baseline and 2.00× is double. Observations pick no winner.")}</p>'
            + _table([t("replay.column.metric", "Metric"), "P50", "P95", t("replay.stat.mean", "Mean")], rows, t("public.ratio.caption", "{pair} per-task ratios", pair=pair))
            + "</details>"
        )
    return "".join(modules)


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
    *,
    detail_title: str = "",
    back_anchor: str = "",
) -> str:
    label = next(p[1] for p in PAGES if p[0] == active)
    index = next(i for i, p in enumerate(PAGES) if p[0] == active)
    if not detail_title:
        current_filename = PAGES[index][2]
        body = body.replace('href="#', f'href="{current_filename}#')
    prev_link = (
        f'<a href="{PAGES[index - 1][2]}">← {PAGES[index - 1][1]}</a>' if index else "<span></span>"
    )
    next_link = (
        f'<a href="{PAGES[index + 1][2]}">{PAGES[index + 1][1]} →</a>'
        if index + 1 < len(PAGES)
        else "<span></span>"
    )
    header = (
        f'<div class="case-header"><a href="{REPORT_STEM}-results.html#{_e(back_anchor)}">← {t("public.back_to_results", "Back to evaluation results")}</a>'
        f'<a href="{REPORT_STEM}.html">{t("public.title.short", "Fixed-trajectory replay evaluation")}</a><span class="muted">{t("replay.run", "Run {run_id}", run_id=_e(manifest.get("run_id", "unknown")))}</span></div>'
        if detail_title
        else f'<div class="brand"><div class="eyebrow">WebArena Fixed-Trajectory Replay</div><h1>{t("public.shell.heading", "WebArena fixed-trajectory replay evaluation")}</h1><div class="muted">{t("public.shell.subtitle", "Deterministic replay of public human trajectories · No Agent or model variables · Run {run_id}", run_id=_e(manifest.get("run_id", "unknown")))}</div></div>{_nav(active)}'
    )
    pager = "" if detail_title else f'<div class="pager">{prev_link}{next_link}</div>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{t("public.shell.title", "{title} | WebArena fixed-trajectory replay evaluation", title=_e(detail_title) or label)}</title><style>{REPORT_CSS}</style></head><body><header><div class="wrap">{header}</div></header><main>{body}{pager}</main><footer>Lexmount WebArena Fixed-Trajectory Replay</footer></body></html>"""


def _functional_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    correctness_by_engine = {row[0]: row for row in _correctness_rows(summary)}
    for engine in summary_engines(summary):
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
    for engine in summary_engines(summary):
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
            ]
        )
    return rows


def _correctness_table(summary: dict[str, Any]) -> str:
    functional = summary.get("functional") or {}
    scores = {
        engine: {
            "result": int((functional.get(engine) or {}).get("result_final_passes", 0) or 0),
            "tasks": int((functional.get(engine) or {}).get("tasks", 0) or 0),
        }
        for engine in summary_engines(summary)
    }
    rates = [value["result"] / value["tasks"] for value in scores.values() if value["tasks"]]
    best_rate = max(rates) if rates else None
    unique_best = best_rate is not None and rates.count(best_rate) == 1
    rows = []
    for engine, values in scores.items():
        result, tasks = values["result"], values["tasks"]
        result_rate = result / tasks * 100 if tasks else 0
        result_class = "better" if unique_best and result / tasks == best_rate else ""
        rows.append(
            "<tr>"
            f'<td data-label="{t("public.correctness.browser", "Browser")}">{_e(engine_label(engine))}</td>'
            f'<td data-label="{t("replay.status.pass", "Pass")}" class="{result_class}">{t("replay.score.rate", "{passed}/{total} ({rate:.2f}%)", passed=result, total=tasks, rate=result_rate)}</td>'
            f'<td data-label="{t("public.correctness.failed_tasks", "Failed tasks")}"><a class="provider-failure-link" href="{REPORT_STEM}-results.html#provider={engine}&amp;status=fail" aria-label="{t("replay.failure_link.label", "{engine}: view {count} failed tasks", engine=engine, count=max(0, tasks - result))}">{t("replay.failure_link.count", "{count} failed", count=max(0, tasks - result))} <span aria-hidden="true">→</span></a></td>'
            "</tr>"
        )
    return (
        f'<div class="table-wrap stackable" role="region" aria-label="{t("public.correctness.caption", "Programmatic acceptance results")}" tabindex="0"><table><caption>{t("public.correctness.caption", "Programmatic acceptance results")}</caption><thead><tr>'
        f"<th>{t('public.correctness.browser', 'Browser')}</th><th>{t('replay.status.pass', 'Pass')}</th><th>{t('public.correctness.failed_tasks', 'Failed tasks')}</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _detail_filename(task_id: str, engine: str) -> str:
    return f"{REPORT_STEM}-task-{task_id.encode().hex()}-{engine.encode().hex()}.html"


def report_file_names(summary: dict[str, Any]) -> set[str]:
    """Declare every output owned by this renderer from its result population."""
    details = {
        _detail_filename(str(task_id), str(engine))
        for task_id, task in (summary.get("by_task") or {}).items()
        for engine, attempts in (task.get("attempts") or task.get("failures") or {}).items()
        if attempts
    }
    return {f"{REPORT_STEM}.md", f"{REPORT_STEM}-data.json",
            *(filename for _, _, filename in PAGES), *details}


def _is_webarena_manifest(manifest: dict[str, Any]) -> bool:
    return any(
        ((task.get("evaluator") or {}).get("kind") == "webarena_verified")
        for task in manifest.get("suite") or []
    )


def _report_input(name: str) -> Path:
    return bundled_data_path("evaluation/browser-use-public/" + name)


def _load_public_research() -> tuple[dict[str, Any], dict[str, Any]]:
    adoption = json.loads(_report_input("adoption-evidence.json").read_text(encoding="utf-8"))
    catalog = json.loads(_report_input("benchmark-catalog.json").read_text(encoding="utf-8"))
    return adoption, catalog


def _research_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    compared_engines = engine_names(
        summary_engines(summary) or tuple(DEFAULT_BROWSER_IMAGES), f"{t('public.research.and', 'and')} "
    )
    adoption, catalog = _load_public_research()
    records = adoption.get("records") or []
    browser_records = [
        record for record in records
        if str(record.get("benchmark_family")) != "Mind2Web"
        or "Online-Mind2Web" in str(record.get("benchmark_version"))
    ]
    browser_families: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for record in browser_records:
        family = str(record.get("benchmark_family"))
        organization = str(record.get("organization"))
        browser_families.setdefault(family, {}).setdefault(organization, []).append(record)
    tie_order = {"WebArena": 0, "Mind2Web": 1, "WebVoyager": 2, "MiniWoB++": 3}
    adoption_rows = []
    for rank, family in enumerate(
        sorted(browser_families, key=lambda name: (-len(browser_families[name]), tie_order.get(name, 99))), 1
    ):
        family_organizations = browser_families[family]
        adoption_rows.append(
            [
                str(rank),
                "Online-Mind2Web" if family == "Mind2Web" else family,
                str(len(family_organizations)),
                t("replay.separator.list", ", ").join(family_organizations),
                t("replay.separator.clause", "; ").join(
                    f"{row.get('organization')}: {row.get('benchmark_version')}"
                    for organization_records in family_organizations.values()
                    for row in organization_records
                ),
            ]
        )
    adoption_cards = "".join(
        f'<article class="rank-card"><span class="rank-number">{_e(row[0])}</span><h3>{_e(row[1])}</h3>'
        f'<strong>{t("public.research.organizations", "{count} organizations adopt it", count=_e(row[2]))}</strong><p>{_e(row[3])}</p>'
        f'<details><summary>{t("public.research.versions", "Specific versions")}</summary><p>{_e(row[4])}</p></details></article>'
        for row in adoption_rows[:3]
    )

    benchmarks = catalog.get("benchmarks") or []
    benchmark_cards = []
    for row in benchmarks:
        category = str(row.get("category"))
        sources = " · ".join(
            f'<a href="{_e(url)}" target="_blank" rel="noopener">{t("public.research.source", "Source {index}", index=index)}</a>'
            for index, url in enumerate(row.get("source_urls") or [], 1)
        )
        benchmark_cards.append(
            f'<article class="benchmark-card" data-category="{_e(category)}">'
            f'<div class="benchmark-title"><div><h3>{_e(row.get("name"))}</h3><p>{_e(row.get("version"))}</p></div>'
            f'<span class="category-chip">{_e(CATEGORY_LABELS.get(category, category))}</span></div>'
            f'<div class="benchmark-kpis"><span><strong>{_e(row.get("task_count"))}</strong>{t("public.research.task_count", "Task count")}</span>'
            f'<span><strong>{_e(row.get("online"))}</strong>{t("public.research.online", "Online")}</span>'
            f'<span><strong>{_e(row.get("reference_trajectory"))}</strong>{t("public.research.reference", "Reference trajectory")}</span></div>'
            '<dl class="benchmark-facts">'
            f'<div><dt>{t("replay.section.environment", "Environment")}</dt><dd>{_e(row.get("environment"))}</dd></div>'
            f'<div><dt>{t("public.research.deployment", "Deployment and accounts")}</dt><dd>{t("public.research.deployment_value", "{deployment}; {account}", deployment=_e(row.get("local_deployment")), account=_e(row.get("account_requirement")))}</dd></div>'
            f'<div><dt>{t("public.research.input", "Input")}</dt><dd>{_e(row.get("input_and_trajectory"))}</dd></div>'
            f'<div><dt>{t("public.research.acceptance", "Acceptance and scoring")}</dt><dd>{t("public.research.acceptance_value", "{acceptance}; {scoring}", acceptance=_e(row.get("final_acceptance")), scoring=_e(row.get("scoring")))}</dd></div>'
            f'</dl><details><summary>{t("public.research.limits", "Difficulty, cost and reproduction limits")}</summary><dl class="benchmark-more">'
            f'<div><dt>{t("replay.filter.difficulty", "Difficulty")}</dt><dd>{_e(row.get("difficulty"))}</dd></div>'
            f'<div><dt>{t("public.research.license", "License")}</dt><dd>{_e(row.get("license"))}</dd></div>'
            f'<div><dt>{t("public.research.cost", "Cost")}</dt><dd>{_e(row.get("cost"))}</dd></div>'
            f'<div><dt>{t("public.research.stability", "Stability")}</dt><dd>{_e(row.get("stability"))}</dd></div>'
            f'<div><dt>{t("public.research.known_issues", "Known issues")}</dt><dd>{_e(row.get("known_issues"))}</dd></div>'
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
    top_families = {"WebArena", "Mind2Web", "WebVoyager"}
    for record in browser_records:
        if str(record.get("benchmark_family")) not in top_families:
            continue
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
    catalog_script = f"<script>{BENCHMARK_TABS_JS}</script>"
    return (
        '<section class="card note"><h2>为什么选择 WebArena-Verified</h2>'
        '<p>本轮要隔离浏览器变量：同一条公开轨迹、可重建网页、程序验收最终状态。'
        'WebArena-Verified 能同时提供这三点。厂商采用数另列，不能代替工程适用性判断。</p>'
        '<p>候选集按测量对象分类，不跨类排名；原始 Mind2Web 的离线动作预测不算端到端网页操作。</p></section>'
        '<section class="card"><h2>端到端 browser-use 采用 Top 3</h2>'
        '<p>只统计 OpenAI、Anthropic、Google、DeepSeek、智谱/ChatGLM、Moonshot/Kimi 的第一方材料；同一机构同一家族只计一次。此处 Mind2Web 只计在线版，不把离线动作预测算作端到端网页操作。平票再按最新官方采用、公开获取和可复现性排序。</p>'
        + f'<div class="rank-grid">{adoption_cards}</div>'
        + '<p class="muted">本次限定的一方材料中没有检出可计入 DeepSeek 或 Moonshot/Kimi 的端到端 browser-use benchmark 记录。这不等于这些机构从未做过内部评测。</p>'
        + f'<details><summary>{t("public.research.adoption_links", "View adoption evidence links")}</summary><ul>'
        + "".join(source_links)
        + '</ul></details></section>'
        '<section class="card"><h2>工程适用性 Top 3</h2>'
        + f'<div class="rank-grid">{engineering_cards}</div>'
        + '<p>WebVoyager 只保留为历史参照。OSWorld 2.0 太宽，ScreenSpot-Pro 只测单步定位；它们都不会进入本轮固定轨迹正式分母。</p></section>'
        '<section class="card"><h2>Mind2Web 家族的三套评测</h2><ul>'
        '<li>原始 Mind2Web 是离线 HTML 和动作预测数据集。它适合比较元素选择与动作预测，不能真正比较 ' + _e(compared_engines) + '。</li>'
        '<li>Online-Mind2Web 在 300 个真实网站任务上判断最终任务是否完成。截图、URL 和轨迹是 judge 证据，并不要求复现固定动作序列；WebJudge 是 LLM-as-a-judge。</li>'
        '<li>Online-Mind2Web 的 easy、medium、hard 按人工参考路径长度划分，分别为不超过 5 步、6 到 10 步、至少 11 步。这个标签不是网站难度，也不是 agent 实际执行步数。</li>'
        '<li>Mind2Web 2 有 130 个长程研究任务，评估实时搜索、综合回答和引用归因，属于网页研究类。</li></ul></section>'
        '<section class="card"><h2>Browser Use 官方实践</h2>'
        '<p>Browser Use 当前公开仓库同时维护 BU Bench V1 和 Online-Mind2Web 运行结果。前者有 100 个经过筛选的混合任务，后者便于与公开生态比较。两者都保留运行轨迹，但都没有可直接当作标准操作路径的 gold trajectory。</p>'
        '<p>本项目采用它们对任务失效、验证码、外部认证和不可验收任务的处理原则，也保留完整轨迹；不照搬多次采样或 bootstrapping，因为这里测的是固定路径与浏览器兼容性。</p></section>'
        '<section class="card"><h2>运行架构</h2>'
        '<p>这些用例不要求 AMD。远端 Intel x86-64 主机可以运行官方 amd64 镜像。179 条官方 WebArena 人类录制中，本项目在运行前排除了 4 条只适配 arm64 Wikipedia 环境的任务，并排除了 3 条已确认的 evaluator 缺陷；172 条进入 amd64 资格验证。最终只有冷 Chromium 重放、程序 evaluator 和证据完整性同时通过的 '
        f'{len(manifest.get("suite") or [])} 条轨迹进入正式任务分母。</p></section>'
        + f'<section class="card"><div class="section-heading"><div><h2>{t("public.research.candidates", "Candidate suites")}</h2><p class="muted">{t("public.research.candidates_note", "Filter by category first, then expand the reproduction limits of each item; different categories are not ranked against each other.")}</p></div><span id="benchmark-count" class="muted"></span></div>'
        + f'<div class="benchmark-tabs" role="group" aria-label="{t("public.research.category_label", "Benchmark category")}">{category_buttons}</div>'
        + '<div class="benchmark-grid">' + "".join(benchmark_cards) + '</div></section>'
        + catalog_script
        + f'<section class="card"><p class="muted">{t("public.research.verified_on", "Sources verified on: {date}. The Cost entries only describe where costs come from and do not state unmeasured prices as numbers.", date=_e(catalog.get("verified_on")))}</p></section>'
    )


def _summary_page(
    manifest: dict[str, Any], summary: dict[str, Any], audit: dict[str, Any]
) -> str:
    task_count = len(manifest.get("suite") or [])
    functional = summary.get("functional") or {}
    engines = summary_engines(summary)
    engine_list = t("replay.separator.list", ", ").join(engine_label(engine) for engine in engines)
    comparison = summary.get("resource_comparison") or {}
    paired = int(comparison.get("intersection_results", comparison.get("intersection_attempts", 0)) or 0)
    relative = comparison.get("relative_ratios") or {}
    legacy_ratios = comparison.get("paired_ratios") or {}
    if relative.get("by_engine"):
        ratio_baseline = str(relative.get("baseline_engine") or "")
    elif legacy_ratios.get("numerator_engine") and legacy_ratios.get("denominator_engine"):
        ratio_baseline = str(legacy_ratios["denominator_engine"])
    else:
        ratio_baseline = ""
    ratio_intro = (
        t("public.summary.ratio_intro", "Ratios are computed per common task as evaluated browser / {baseline}, then aggregated. ", baseline=engine_label(ratio_baseline))
        if ratio_baseline
        else ""
    )
    score_cards = "".join(
        f'<article class="score-card {engine}"><h3>{_e(engine_label(engine))}</h3>'
        f'<div class="score-line"><span>{t("public.summary.pass_rate", "Pass rate")}</span><div class="bar" role="progressbar" aria-label="{t("public.summary.pass_rate_label", "{engine} pass rate", engine=_e(engine_label(engine)))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{values.get("result_final_pass_rate", 0) * 100:.2f}"><i style="width:{values.get("result_final_pass_rate", 0) * 100:.2f}%"></i></div><strong>{values.get("result_final_passes", 0)}/{values.get("tasks", 0)}</strong></div></article>'
        for engine in engines
        if (values := functional.get(engine) or {})
    )
    failure_rows, _ = _failure_review(summary)
    final_failure_count = sum(int(row[1]) for row in failure_rows)
    failure_breakdown = t("replay.separator.comma", ", ").join(t("public.summary.breakdown", "{count} {category}", count=row[1], category=row[0]) for row in failure_rows)
    failure_summary = (
        t("public.summary.failure_summary", "The {count} results that did not pass include {breakdown}. ", count=final_failure_count, breakdown=failure_breakdown)
        if failure_rows
        else t("public.summary.no_failures", "No result failed in this run. ")
    )
    stage_labels = {
        t("public.failure.interaction", "Page interaction interrupted"): t("public.stage.interaction", "Page interaction"),
        t("public.failure.environment", "Environment preparation failed"): t("public.stage.environment", "Environment preparation"),
        t("public.failure.acceptance", "Final acceptance mismatch"): t("public.stage.acceptance", "Final acceptance"),
    }
    failure_stages = t("replay.separator.list", ", ").join(stage_labels.get(row[0], row[0]) for row in failure_rows)
    failure_boundary = (
        t("public.summary.failure_boundary", "The current evidence places the failed results in the {stages} stages; ", stages=failure_stages)
        if failure_rows
        else t("public.summary.no_failure_boundary", "No failed result needs attribution in this run; ")
    )
    failure_cards = "".join(
        f'<article><strong>{_e(row[1])}</strong><span>{_e(row[0])}</span><p>{_e(row[2])}</p></article>'
        for row in failure_rows
    )
    pass_counts = {engine: int((functional.get(engine) or {}).get("result_final_passes") or 0) for engine in engines}
    best_pass = max(pass_counts.values(), default=0)
    pass_leaders = [engine for engine, value in pass_counts.items() if value == best_pass]
    compatibility_lead = (
        t("public.summary.lead_one", "{engine} has the most passes", engine=engine_label(pass_leaders[0]))
        if len(pass_leaders) == 1
        else t("replay.separator.list", ", ").join(engine_label(engine) for engine in pass_leaders) + t("public.summary.lead_tied", " tie for the most passes")
    )
    resource_values = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    memory_p50 = {
        engine: _numeric_stat(((resource_values.get(engine) or {}).get("memory_mean_bytes") or {}), "p50")
        for engine in engines
    }
    available_memory = {engine: value for engine, value in memory_p50.items() if isinstance(value, (int, float))}
    lowest_memory = min(available_memory.values()) if available_memory else None
    memory_leaders = [engine for engine, value in available_memory.items() if value == lowest_memory]
    resource_lead = (
        t("public.summary.memory_lead", "{engine} has the lowest memory P50", engine=engine_label(memory_leaders[0]))
        if len(memory_leaders) == 1
        else t("public.summary.memory_none", "No unique lowest memory P50")
    )
    audit_strip = (
        f'<div class="audit-strip"><strong>{t("public.summary.audit_count", "{reviewed}/{total} results passed the consistency audit", reviewed=audit["logical_results_reviewed"], total=task_count * len(engines))}</strong><span>{t("public.summary.audit_note", "Underlying execution records, programmatic acceptance and aggregates agree; {count} failed results are classified from logs and call fields.", count=sum(audit["final_failures"].values()))}</span><a href="{REPORT_STEM}-method.html#verification">{t("public.summary.audit_link", "View verification scope")}</a></div>'
    )
    return f"""<section class="card hero"><div class="eyebrow">{t("replay.page.summary", "Summary")}</div><h2>{t("public.summary.hero", "In this run, {compatibility}; {resource}. Speed, CPU, memory and network are listed separately and not merged into one score.", compatibility=compatibility_lead, resource=resource_lead)}</h2><p>{t("public.summary.hero_note", "This is a fixed replay of WebArena public human trajectories, with no Agent planning, model calls or LLM judge at run time. The numbers apply only to these {count} frozen trajectories and this environment.", count=task_count)}</p>{audit_strip}</section>
<section class="card"><div class="section-heading"><div><h2>{t("public.summary.correctness", "Task correctness")}</h2><p class="muted">{t("public.summary.one_result", "Each task and browser publishes one result.")}</p></div><a href="{REPORT_STEM}-results.html#task-comparison">{t("public.summary.view_task_results", "View per-task results")}</a></div><div class="score-grid">{score_cards}</div>{_correctness_table(summary)}<p>{t("public.summary.call_chain_note", "{failure_summary}A completed tool-call chain can still fail programmatic acceptance: a call return only means the path finished, and the final answer, service state or HAR may still not satisfy the evaluator.", failure_summary=failure_summary)}</p></section>
<section class="card" id="performance"><div class="section-heading"><div><h2>{t("public.summary.speed", "Speed and resources")}</h2><p class="muted">{t("public.summary.speed_scope", "Only the {count} same tasks that {engines} all passed count, using the unique public result of each.", engines=engine_list, count=paired)}</p></div></div><p>{t("public.summary.speed_note", "Absolute tables list P50, P95 and mean side by side. {ratio_intro}Only values with a clear direction and a unique winner are bold green; Observations pick no winner.", ratio_intro=ratio_intro)}</p>{_metric_tables(summary)}</section>
<section class="card"><div class="section-heading"><div><h2>{t("public.summary.failures", "Failure overview")}</h2><p class="muted">{t("public.summary.failures_note", "The {count} failed results are grouped by the direct mechanism closest to the evidence, without presenting symptoms as browser-internal root causes.", count=final_failure_count)}</p></div><a href="{REPORT_STEM}-results.html#failure-review">{t("public.summary.view_failure_analysis", "View failure analysis")}</a></div><div class="failure-story">{failure_cards}</div><p>{t("public.summary.failures_boundary", "{boundary}page interaction evidence is still not enough to tell whether the difference comes from browser rendering, layout or the adapter layer.", boundary=failure_boundary)}</p></section>
<section class="card"><h2>{t("public.summary.conclusion_scope", "Scope of the conclusions")}</h2><div class="reading-grid"><article><h3>{t("public.summary.compatibility", "Compatibility")}</h3><p>{t("public.summary.compatibility_note", "To cover these fixed business paths, the browser with more passes is currently the safer choice.")}</p></article><article><h3>{t("public.summary.resources", "Resources")}</h3><p>{t("public.summary.resources_note", "For tasks verified as compatible, a memory advantage has engineering value; still check the duration and network cost of that task before going live.")}</p></article><article><h3>{t("public.summary.applicability", "Applicability")}</h3><p>{t("public.summary.applicability_note", "{count} trajectories cannot represent overall browser capability or explain Agent success rates. The suite, selection rationale and method are on the following pages.", count=task_count)}</p></article></div></section>"""


def _results_page(
    manifest: dict[str, Any],
    summary: dict[str, Any],
    detail_pages: dict[str, str] | None = None,
) -> str:
    engines = summary_engines(summary)
    complexity_rows = []
    for complexity in ("simple", "medium", "complex"):
        group = (summary.get("by_complexity") or {}).get(complexity) or {}
        values = group.get("functional") or {}
        metrics = {engine: values.get(engine) or {} for engine in engines}
        counts = {
            engine: {
                "tasks": int(metric.get("tasks") or 0),
                "result": int(metric.get("result_final_passes", 0) or 0),
            }
            for engine, metric in metrics.items()
        }

        rates = [row["result"] / row["tasks"] for row in counts.values() if row["tasks"]]
        best_rate = max(rates) if rates else None
        unique_best = best_rate is not None and rates.count(best_rate) == 1

        def compared(
            engine: str,
            counts: dict[str, dict[str, int]] = counts,
            unique_best: bool = unique_best,
            best_rate: float | None = best_rate,
        ) -> ComparedValue:
            own = counts[engine]
            own_rate = own["result"] / own["tasks"] if own["tasks"] else None
            return ComparedValue(
                f'{own["result"]}/{own["tasks"]}',
                "better" if unique_best and own_rate == best_rate else "",
            )

        row = [
            _difficulty(complexity),
            *(compared(engine) for engine in engines),
        ]
        complexity_rows.append(row)

    site_counts: dict[str, dict[str, list[int]]] = {}
    specs = {str(task.get("task_id")): task for task in manifest.get("suite") or []}
    for task_id, task in (summary.get("by_task") or {}).items():
        sites = (specs.get(str(task_id), {}).get("environment") or {}).get("sites") or [t("replay.unlabeled", "Unlabeled")]
        attempts = task.get("attempts") or {}
        for site in sites:
            bucket = site_counts.setdefault(str(site), {engine: [0, 0] for engine in engines})
            for engine in engines:
                rows = attempts.get(engine) or []
                bucket[engine][0] += 1
                if rows and str(rows[-1].get("result_status") or rows[-1].get("status")) == "pass":
                    bucket[engine][1] += 1
    site_rows = []
    for site, values in sorted(site_counts.items()):
        rates = [value[1] / value[0] for value in values.values() if value[0]]
        best_rate = max(rates) if rates else None
        unique_best = best_rate is not None and rates.count(best_rate) == 1
        site_rows.append(
            [
                site,
                *[
                    ComparedValue(
                        f"{values[engine][1]}/{values[engine][0]}",
                        "better"
                        if unique_best and values[engine][0] and values[engine][1] / values[engine][0] == best_rate
                        else "",
                    )
                    for engine in engines
                ],
            ]
        )
    result_overview = (
        f'<section class="card result-guide" aria-label="{t("public.results.guide_label", "Result analysis modules")}">'
        f'<div style="grid-column:1/-1"><h2>{t("public.page.results", "Result analysis")}</h2><p>{t("public.results.guide", "Start with browser differences on the same task, then read site slices, failure mechanisms and per-item acceptance evidence.")}</p></div>'
        f'<a href="#task-comparison"><strong>{t("public.results.combination", "Browser combinations")}</strong><span>{t("public.results.combination_note", "Filter by outcome relation")}</span></a>'
        f'<a href="#task-slices"><strong>{t("public.results.groups", "Task groups")}</strong><span>{t("public.results.groups_note", "Site and sampling groups")}</span></a>'
        f'<a href="#failure-review"><strong>{t("public.results.failure_analysis", "Failure analysis")}</strong><span>{t("public.results.failure_analysis_note", "Categories and logs")}</span></a>'
        f'<a href="#execution-results"><strong>{t("replay.detail.execution_records", "Execution records")}</strong><span>{t("public.results.per_task_evidence", "Per-task evidence")}</span></a></section>'
    )
    slices = (
        f'<div id="task-slices"><section class="card"><h2>{t("public.results.groups", "Task groups")}</h2><p class="muted">{t("public.results.groups_body", "Simple, medium and complex are the sampling groups of this manifest, not official WebArena difficulty labels, and have no common action-count threshold. They only describe the sample and do not support comparing browser capability across groups.")}</p>'
        + _table([t("public.results.trajectory_group", "Trajectory group"), *[engine_label(engine) for engine in engines]], complexity_rows, t("public.results.group_caption", "Correctness by group")) + '</section>'
        f'<section class="card"><h2>{t("public.results.site_groups", "Site groups")}</h2>'
        + _table([t("public.results.site_column", "Site"), *[engine_label(engine) for engine in engines]], site_rows, t("public.results.site_caption", "Compatibility by site")) + '</section></div>'
    )
    details = detail_pages if detail_pages is not None else {}
    return (
        result_overview
        + _task_comparison(summary)
        + slices
        + _execution_explorer(manifest, summary, details)
        + f'<section class="card"><h2>{t("public.results.root_cause", "Root-cause boundary")}</h2><p>{t("public.results.root_cause_body", "Failure logs can already separate environment preparation, page interaction and programmatic acceptance, but cannot yet prove in which browser-internal layer a difference arises. Later controlled experiments should fix the page, selector and call, and measure DOM update, layout, visibility and actionability recovery times; until that evidence exists, screencast, renderer or adapter layer are not stated as confirmed root causes.")}</p></section>'
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
        t("replay.separator.list", ", ").join(
            t("replay.suite.difficulty_count", "{difficulty}: {count}", difficulty=_difficulty(difficulty), count=count)
            for difficulty, count in difficulty_counts.items()
            if count
        )
        or t("replay.suite.no_difficulty", "No difficulty label")
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
                    or t("replay.unlabeled", "Unlabeled")
                ]
            )
    action_counts = Counter(
        str(call.get("action") or "") for task in suite for call in task.get("tool_calls") or []
    )
    distribution = (
        '<div class="grid suite-stats">'
        f'<div class="metric"><strong>{len(suite)}</strong><span>{t("public.suite.trajectory_tasks", "Public trajectory tasks") if webarena else t("replay.suite.public_web_tasks", "Public-web tasks")}</span></div>'
        f'<div class="metric"><strong>{len(site_counts)}</strong><span>{t("public.suite.self_hosted_sites", "Self-hosted sites") if webarena else t("replay.suite.independent_sites", "Independent sites/domains")}</span></div>'
        f'<div class="metric"><strong>{sum(action_counts.values())}</strong><span>{t("replay.suite.reference_calls", "Reference BrowserTool calls")}</span></div>'
        "</div>"
        + '<div class="distribution-grid compact-two">'
        + f'<div class="distribution-item"><span>{t("public.suite.sampling_group", "Sampling group")}</span><strong>{_e(difficulty_summary)}</strong></div>'
        + f'<div class="distribution-item"><span>{t("public.suite.site_distribution", "Site distribution")}</span><strong>'
        + _e(t("replay.separator.clause", "; ").join(f"{host} {count}" for host, count in sorted(site_counts.items())))
        + f'</strong></div></div><details><summary>{t("public.suite.action_distribution", "View action type distribution")}</summary><p>'
        + _e(t("replay.separator.clause", "; ").join(f"{action} {count}" for action, count in sorted(action_counts.items())))
        + '</p></details>'
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
                    str(item.get("evaluator") or t("public.suite.programmatic_evaluator", "Programmatic evaluator"))
                    for item in verified_evaluators
                )
                or t("public.suite.programmatic_evaluator", "Programmatic evaluator")
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
                    f'<br><a target="_blank" rel="noopener" href="{_e(target_url)}">{t("public.suite.official_source", "Official trajectory source")}</a>'
                )
            environment_heading = t("public.suite.self_hosted_environment", "Self-hosted test environment")
            oracle_heading = t("public.suite.programmatic_acceptor", "Programmatic acceptor")
        else:
            source_html = (
                f'<a target="_blank" rel="noopener" href="{_e(target_url)}">{_e(target_url)}</a>'
            )
            environment_heading = t("replay.suite.public_web_page", "Public-web test page")
            oracle_heading = t("replay.suite.unique_expected", "Unique expected result")
        shared_tags = {
            "simple", "medium", "complex", "public-web", "exact-oracle",
            "public-benchmark", "webarena",
        }
        chips = "".join(
            f'<span class="chip">{_e({"retrieve": t("public.suite.tag_retrieve", "Retrieve"), "navigate": t("public.suite.tag_navigate", "Navigate"), "mutate": t("public.suite.tag_mutate", "State change")}.get(tag, tag))}</span>'
            for tag in task.get("tags") or []
            if tag not in shared_tags and not str(tag).startswith("site:")
        )
        search_text = (
            t("replay.suite.search_text", "Task{index:02d} Task {index:02d} {task_id} {title} {target_url} ", index=index, task_id=task.get('task_id'), title=title, target_url=target_url)
            + " ".join(task.get("tags") or [])
            + " "
            + expected_inline
        )
        cards.append(
            f'<section class="card suite-task" data-difficulty="{_e(task.get("complexity"))}" data-site="{_e(urlparse(target_url).hostname or t("replay.unlabeled", "Unlabeled"))}" data-actions="{_e(",".join(sorted({str(c.get("action")) for c in calls})))}" data-search="{_e(search_text)}"><div class="eyebrow">{t("replay.suite.task_eyebrow", "Task {index:02d}", index=index)} · {_e(task.get("task_id"))}</div><h2>{_e(title)}</h2><span class="chip">{_difficulty(str(task.get("complexity")))}</span><span class="chip">{t("replay.suite.tool_calls", "{count} tool calls", count=len(calls))}</span>{chips}'
            f'<p class="muted">{t("public.suite.card_facts", "Sites: {sites} · Acceptance: {acceptance}", sites=_e(t("replay.separator.list", ", ").join(environment.get("sites") or []) or t("replay.unlabeled", "Unlabeled")), acceptance=_e(" + ".join(str(item.get("evaluator") or t("public.suite.programmatic_acceptance", "Programmatic acceptance")) for item in verified_evaluators) or t("public.suite.programmatic_acceptance", "Programmatic acceptance")))}</p>'
            f'<details><summary>{t("public.suite.card_summary", "Reference path, acceptance and step-by-step actions")}</summary><div class="task-verification">'
            f"<div><strong>{environment_heading}</strong><span>{source_html}</span></div>"
            f"<div><strong>{t('replay.suite.reference_path', 'Reference path')}</strong><span><code>{_e(reference_path)}</code></span></div>"
            f"<div><strong>{oracle_heading}</strong>{expected_rows}</div>"
            "</div>"
            f"{call_html}<p><strong>{t('replay.suite.answer_basis', 'Answer basis: ')}</strong>{_e(truth.get('derivation'))}</p>{_source_references(source)}</details></section>"
        )
    filters = (
        f'<section class="card suite-filter-panel" aria-label="{t("replay.suite.filter_label", "Task filter")}"><div class="section-heading"><h2>{t("replay.suite.find_tasks", "Find tasks")}</h2>'
        f'<span id="suite-count" class="muted" aria-live="polite">{t("replay.suite.showing", "Showing {shown} / {total} tasks", shown=len(suite), total=len(suite))}</span></div>'
        f'<div class="suite-filter-controls"><label class="suite-search">{t("replay.suite.search", "Search tasks")}<input id="suite-query" type="search" placeholder="{t("replay.suite.search_placeholder", "Task name, ID, site or keyword")}"></label>'
        f'<label>{t("public.suite.sampling_group", "Sampling group")}<select id="suite-difficulty"><option value="">{t("public.suite.all_groups", "All groups")}</option>'
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
    if webarena:
        evaluator_count = sum(1 for task in suite if task.get("evaluator"))
        validation_note = (
            f'<p class="muted">{t("public.suite.evaluator_note", "Programmatic evaluator: {bound}/{total} tasks bound.", bound=evaluator_count, total=len(suite))}'
            f'{t("public.suite.manifest_sha", "Task manifest SHA-256: <code>{sha}</code>.", sha=_e(str(manifest.get("suite_sha256") or "")[:16]))}</p>'
        )
    else:
        validation_note = (
            f'<p class="muted">{t("replay.answer_check", "Answer verification: {validator}, {passed}/{total} verified. Dataset SHA-256: <code>{dataset}</code>.", validator=_e(validation.get("validator") or t("replay.not_recorded", "Not recorded")), passed=_e(replay.get("passed")), total=_e(replay.get("total")), dataset=_e(str(validation.get("dataset_sha256") or "")[:16]))}</p>'
        )
    return (
        f'<section class="card note"><h2>{t("replay.suite.overview", "Suite overview")}</h2>{distribution}{validation_note}<details><summary>{t("replay.suite.annotation_source", "Annotation source and version")}</summary><p>{_e(validation.get("method") or t("public.suite.default_method", "WebArena official tasks, public human recordings and programmatic evaluators; this manifest is frozen from the qualification results."))}</p></details></section>'
        + filters
        + "".join(cards)
        + script
    )


def _environment_execution_counts(summary: dict[str, Any]) -> dict[str, int]:
    counts = {"rebuild": 0, "warm": 0, "health": 0, "missing": 0, "prepare_fail": 0}
    for task in (summary.get("by_task") or {}).values():
        for attempts in (task.get("attempts") or {}).values():
            for attempt in attempts:
                receipt = attempt.get("environment_reset")
                if not receipt:
                    counts["missing"] += 1
                elif receipt.get("mode") == "warm_non_mutating_site_reuse":
                    counts["warm"] += 1
                else:
                    reset_modes = {
                        str(site.get("reset") or "")
                        for site in (receipt.get("sites") or {}).values()
                        if isinstance(site, dict)
                    }
                    if "site_health_check_only" in reset_modes:
                        counts["health"] += 1
                    elif any(mode.startswith("container_recreated") for mode in reset_modes):
                        counts["rebuild"] += 1
                    else:
                        counts["missing"] += 1
                trace = attempt.get("trace") or []
                if (
                    trace
                    and trace[0].get("action") == "prepare_environment"
                    and trace[0].get("status") == "fail"
                ):
                    counts["prepare_fail"] += 1
    return counts


def _method_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    p = manifest.get("protocol") or {}
    env = manifest.get("environment") or {}
    versions = env.get("browser_versions") or {}
    engines = summary_engines(summary)
    viewport = p.get("viewport") or {}
    if isinstance(viewport, dict) and isinstance(viewport.get("webarena_verified"), dict):
        viewport = viewport["webarena_verified"]
    viewport_label = (
        f'{viewport.get("width")} × {viewport.get("height")} CSS px'
        if isinstance(viewport, dict) and viewport.get("width") and viewport.get("height")
        else t("replay.not_recorded", "Not recorded")
    )
    rows = [
        [t("replay.env.platform", "Runner platform"), str(env.get("runner_platform", t("replay.not_recorded", "Not recorded")))],
        [
            t("replay.env.container", "Container"),
            f"Docker {env.get('docker_server_version', t('replay.not_recorded', 'Not recorded'))} / {env.get('docker_os', t('replay.not_recorded', 'Not recorded'))}",
        ],
        *[[t("public.method.browser_version", "Browser version · ") + engine_label(engine), str(versions.get(engine, t("replay.not_recorded", "Not recorded")))] for engine in engines],
        [
            t("replay.env.quota", "Per-browser quota"),
            t("replay.env.quota_value", "{cpus} CPU / {memory} / isolated network", cpus=p.get('cpus_per_browser', '?'), memory=p.get('memory_per_browser', '?')),
        ],
        [t("public.method.result_unit", "Result unit"), t("public.method.result_unit_value", "One public result per task and browser")],
        [t("replay.env.viewport", "Viewport"), viewport_label],
        [t("replay.env.sampling", "Sampling"), t("replay.env.sampling_value", "Memory, PID, CPU and network counters sampled every {interval} ms", interval=p.get('sample_interval_ms', '?'))],
        [
            t("public.method.site_isolation", "Site isolation"),
            t("public.method.site_isolation_value", "Browsers run serially in this run; state-changing tasks rebuild the site every time, and non-mutating tasks reuse it continuously per browser and site. Parallel runs would need separate containers, networks, ports and writable volumes; separate test accounts alone do not isolate shared databases, caches and background jobs."),
        ],
    ]
    if _is_webarena_manifest(manifest):
        environment_counts = _environment_execution_counts(summary)
        reset_summary = (
            t("public.method.reset_summary", "{rebuild} image rebuilds, {warm} warm reuses, {health} Map health checks and {missing} without a reset receipt; {prepare_fail} of them failed during environment preparation before the trajectory began", rebuild=environment_counts["rebuild"], warm=environment_counts["warm"], health=environment_counts["health"], missing=environment_counts["missing"], prepare_fail=environment_counts["prepare_fail"])
        )
        scoring = f"""<p>{t("public.method.scoring_webarena", "<strong>Task score:</strong> the WebArena-Verified evaluator accepts answers, final service state and HAR network events programmatically, with no LLM judge and no step-by-step scoring against reference actions.")}</p><p>{t("public.method.scoring_chain", "<strong>Call chain:</strong> Uninterrupted only means every tool call returned; the answer, state or HAR may still fail, so it does not mean the task is complete.")}</p><p>{t("public.method.scoring_duration", "<strong>Duration:</strong> only <code>execution_duration_ms</code> from fixed-trajectory start to end counts. Site rebuild, initialization, login and page preparation do not count toward browser speed; overall wall clock is for troubleshooting only.")}</p><details><summary>{t("public.method.scoring_patch_summary", "View trajectory patch boundary")}</summary><p>{t("public.method.scoring_patch", "Official human trajectories are fixed input paths. Qualification may only correct stale targets while the task, environment and evaluator stay unchanged; a corrected trajectory must pass independent cold Chromium verification again.")}</p></details>"""
        flow = f"""<ol><li>{t("public.method.flow_rebuild", "Tasks that change service state rebuild the sites involved from fixed images every time; non-mutating tasks continuously reuse initialized environments per site and browser, and read-only Map gets a health check. This run recorded {reset_summary}.", reset_summary=reset_summary)}</li><li>{t("public.method.flow_login", "Wait for initialization of the official entry point to exit and verify external origins, then log in with test credentials; these preparation steps do not count toward trajectory duration.")}</li><li>{t("public.method.flow_page", "Create a clean page with a fixed 1280×720 viewport and settle for {settle} ms outside timing; task order alternates across the evaluated browsers.", settle=p.get("post_reset_settle_ms", "?"))}</li><li>{t("public.method.flow_run", "Run the fixed trajectory while saving the answer, actions, final page, screenshots, HAR, resources and stop reason.")}</li><li>{t("public.method.flow_publish", "Publish one result per task and browser; resource comparison only uses same tasks every browser passed.")}</li></ol>"""
    else:
        scoring = f"""<ol><li>{t("public.method.scoring_result", "<strong>Result correct (the only task score):</strong> each annotated output must exactly equal <code>expected_outputs</code> as a string; no fuzzy matching or model scoring; Codex only took part in annotation review before the evaluation.")}</li><li>{t("public.method.scoring_diagnostic", "<strong>Tool-call chain (diagnostic, not scored):</strong> records whether action types match the preregistered trajectory and whether every BrowserTool call succeeded, to locate an interrupted step.")}</li></ol><p>{t("public.method.scoring_boundary", "<strong>Boundary:</strong> <code>expected_actions</code> is the fixed input path of a tool-layer microbenchmark and does not mean the task has only one solution. To evaluate a freely planning agent, use required milestones, forbidden actions and a step budget instead of requiring the whole sequence to match step by step.")}</p>"""
        flow = f"""<ol><li>{t("public.method.flow_start", "Start and warm up every evaluated browser separately; browser processes and contexts stay running for the whole evaluation.")}</li><li>{t("replay.method.flow_clear", "Before each task, clear cookies, permissions, HTTP cache, Cache Storage, Service Workers, IndexedDB, origin storage and history, and create a new page.")}</li><li>{t("public.method.flow_settle", "Settle for {settle} ms outside timing and rotate the browser execution order per task.", settle=p.get("post_reset_settle_ms", "?"))}</li><li>{t("public.method.flow_sample", "Run the complete task with sampling; failure analysis belongs to the same task detail.")}</li><li>{t("public.method.flow_baseline", "The resource baseline only compares same tasks all evaluated browsers passed; failure samples are kept out of the normal path.")}</li></ol>"""
    return f"""<section class="card note"><h2>{t("public.method.step_title", "Step definition")}</h2><p>{t("public.method.step_body", "<strong>One step is one fixed BrowserTool call</strong>, such as click, fill or snapshot. Waiting for the page and elements happens inside the call and adds no step; no Agent or model picks the next step at run time.")}</p></section><section class="card" id="scoring"><h2>{t("public.method.verdict_rules", "Verdict rules")}</h2>{scoring}</section><section class="card"><h2>{t("public.method.test_environment", "Test environment")}</h2>{_table([t("replay.column.item", "Item"), t("replay.column.recorded", "Recorded value")], rows, t("replay.env.this_round", "Environment of this run"))}</section><section class="card"><h2>{t("replay.section.flow", "Execution flow")}</h2>{flow}</section><section class="card"><h2>{t("replay.section.statistics", "Statistical scope")}</h2><ul><li>{t("public.method.stats_p50", "P50 is the median; cross-task results report P50, mean and nearest-rank P95.")}</li><li>{t("public.method.stats_single", "Each task has one public result, so no per-task quantiles are computed; this run cannot prove a general performance advantage.")}</li><li>{t("public.method.stats_memory", "Mean memory is time-weighted; task memory P95 comes from samples of one task run.")}</li><li>{t("public.method.stats_network", "RX is received and TX is sent; totals include page resources and CDP, and rates come from differences of adjacent samples.")}</li></ul></section>"""


METRICS = (
    (
        t("replay.metric_group.time", "Time and compute"),
        (
            MetricSpec("duration_ms", t("public.metric.duration", "Trajectory execution duration"), 1, "ms", "lower", t("public.metric.duration_note", "Time to finish the same trajectory; lower means faster")),
            MetricSpec("cpu_ms", t("replay.metric.cpu_time", "CPU time"), 1, "ms", "lower", t("public.metric.cpu_note", "CPU time to finish the same trajectory; lower means less compute cost")),
            MetricSpec("cpu_average_utilization_pct", t("replay.metric.cpu_average", "Average CPU per core"), 1, "%", "neutral", t("public.metric.cpu_average_note", "Utilization depends on both parallelism and task speed, so it cannot decide a winner alone")),
            MetricSpec("cpu_sampled_utilization_p95_pct", t("replay.metric.cpu_p95", "Within-task CPU P95"), 1, "%", "neutral", t("public.metric.cpu_p95_note", "Tail utilization reflects momentary pressure and cannot decide a winner apart from duration and workload")),
        ),
    ),
    (
        t("replay.metric_group.memory", "Memory"),
        (
            MetricSpec("memory_mean_bytes", t("replay.metric.memory_mean", "Time-weighted mean memory"), 1048576, "MiB", "lower", t("public.metric.memory_mean_note", "Mean resident memory while finishing the same trajectory; lower means less use")),
            MetricSpec("memory_p95_bytes", t("replay.metric.memory_p95", "Task memory P95"), 1048576, "MiB", "lower", t("public.metric.memory_p95_note", "Tail memory while finishing the same trajectory; lower means less tail use")),
            MetricSpec("memory_peak_bytes", t("replay.metric.memory_peak", "Peak memory"), 1048576, "MiB", "lower", t("public.metric.memory_peak_note", "Peak memory while finishing the same trajectory; lower means a smaller peak")),
            MetricSpec("memory_retained_bytes", t("replay.metric.memory_retained", "Retained at end"), 1048576, "MiB", "lower", t("public.metric.memory_retained_note", "Growth retained over the baseline after the trajectory ends; lower means less residue")),
            MetricSpec("memory_mib_seconds", t("replay.metric.memory_integral", "Memory-time integral"), 1, "MiB·s", "lower", t("public.metric.memory_integral_note", "Integral of memory use over duration; lower means less overall resource cost")),
        ),
    ),
    (
        t("replay.metric_group.network", "Network"),
        (
            MetricSpec("network_rx_bytes", t("replay.metric.rx_total", "Total received RX"), 1024, "KiB", "lower", t("public.metric.rx_note", "Total bytes received to finish the same trajectory; lower means less network volume")),
            MetricSpec("network_tx_bytes", t("replay.metric.tx_total", "Total sent TX"), 1024, "KiB", "lower", t("public.metric.tx_note", "Total bytes sent to finish the same trajectory; lower means less network volume")),
            MetricSpec("network_rx_bytes_per_second", t("replay.metric.rx_rate", "Mean receive rate"), 1024, "KiB/s", "neutral", t("public.metric.rate_note", "Rate depends on both transfer volume and duration, so it cannot decide a winner alone")),
            MetricSpec("network_rx_rate_p95_bytes_per_second", t("replay.metric.rx_rate_p95", "Within-task receive rate P95"), 1024, "KiB/s", "neutral", t("public.metric.rx_rate_p95_note", "Tail receive rate reflects traffic concentration and cannot decide a winner alone")),
            MetricSpec("network_tx_rate_p95_bytes_per_second", t("replay.metric.tx_rate_p95", "Within-task send rate P95"), 1024, "KiB/s", "neutral", t("public.metric.tx_rate_p95_note", "Tail send rate reflects traffic concentration and cannot decide a winner alone")),
        ),
    ),
)


def _metric_tables(summary: dict[str, Any]) -> str:
    _validate_metric_contract()
    modules = []
    for index, (group, metrics) in enumerate(METRICS):
        modules.append(
            f'<details class="metric-module"{" open" if index == 0 else ""}><summary><span>{_e(group)}</span><small>{t("public.metric.count", "{count} metrics", count=len(metrics))}</small></summary>'
            + _grouped_resource_table(summary, metrics, t("public.metric.absolute_caption", "{group} · Absolute values on the same tasks", group=group))
            + _relative_ratio_tables(summary, metrics)
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
    labels = [t("public.detail.adopted_result", "Adopted result") for _ in attempts]
    result = final.get("result_status") or final.get("status")
    process = final.get("process_status") or final.get("status")
    attempt_label = t("public.detail.one_public_result", "1 public result")
    overview = (
        '<section class="card case-overview" id="case-result">'
        f'<div class="section-heading"><h1>{_e(title)}</h1><span class="chip">{_e(engine)}</span></div>'
        f'<p class="muted case-id">{_e(task_id)}</p>'
        '<div class="case-facts">'
        f'<div><span>{t("replay.detail.result", "Result")}</span><strong class="{"ok" if result == "pass" else "bad"}">{status_cell(result)}</strong></div>'
        f'<div><span>{t("public.detail.call_chain", "Tool-call chain")}</span><strong class="{"ok" if process == "pass" else "bad"}">{t("public.detail.uninterrupted", "Uninterrupted") if process == "pass" else t("public.detail.interrupted", "Interrupted")}</strong></div>'
        f"<div><span>{t('public.detail.result_record', 'Result record')}</span><strong>{_e(attempt_label)}</strong></div></div>"
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
    start_urls = list((spec.get("environment") or {}).get("start_urls") or [])
    if start_urls:
        overview += f'<p class="case-start">{t("public.detail.start_point", "Test start: {urls}", urls=_e(t("replay.separator.list", ", ").join(str(value) for value in start_urls)))}</p>'
    if url:
        source_label = (
            t("public.detail.official_reference", "Official reference trajectory") if (spec.get("environment") or {}).get("kind") == "webarena_verified"
            else t("public.detail.test_page", "Test page")
        )
        overview += f'<p class="case-start">{t("public.detail.source_link", """{label}: <a href="{url}" target="_blank" rel="noopener">View source</a>""", label=source_label, url=_e(safe_url))}</p>'
    overview += '</section>'

    expected = (spec.get("ground_truth") or {}).get("expected_outputs") or {}

    def validation_key(item: dict[str, Any], index: int) -> str:
        output = item.get("output")
        if output is not None:
            return str(output)
        return str(item.get("evaluator_name") or t("public.detail.rule_fallback", "Rule {index}", index=index + 1))

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
            return t("public.detail.purpose_agent", "Check task type, completion status and the answer to return")
        if evaluator == "NetworkEventEvaluator":
            return t("public.detail.purpose_network", "Check request URL, method, request data and response conditions in the HAR")
        if (item or {}).get("output") is not None or rule in expected:
            return t("public.detail.purpose_output", "Check that output {rule} exactly equals the fixed answer", rule=rule)
        return t("public.detail.purpose_declared", "Run the programmatic acceptance rule this task declares")

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
            return t("replay.detail.empty_string", "(empty string)")
        if isinstance(value, list):
            return t("public.detail.record_count", "{count} records", count=len(value))
        if isinstance(value, dict):
            preferred = [
                key
                for key in ("task_type", "status", "url", "http_method")
                if key in value
            ]
            keys = preferred or list(value)[:3]
            return t("replay.separator.clause", "; ").join(f"{key}={compact(value[key])}" for key in keys)
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
        expected_value = expected.get(rule, (first or {}).get("expected", t("replay.detail.undeclared", "Not declared")))
        expected_html = _annotate_source_marks(_e(compact(expected_value)))
        if isinstance(expected_value, (dict, list)):
            expected_html += evidence_link(expected_value, t("public.detail.full_expected_label", "{rule} · Full expected", rule=rule), t("public.detail.full_expected", "Full expected"))
        cells = [
            f"<td>{_e(rule)}</td>",
            f"<td>{_e(rule_purpose(rule, first))}</td>",
            f"<td>{expected_html}</td>",
        ]
        for label, values in zip(labels, validation_maps, strict=True):
            item = values.get(rule)
            if item is None:
                cells.append(f'<td class="muted">{t("replay.detail.not_produced", "Not produced (process failed) · Not judged")}</td>')
                continue
            passed = rule_passed(item)
            verdict = t("replay.status.pass", "Pass") if passed is True else t("replay.status.fail", "Fail") if passed is False else t("public.detail.not_judged", "Not judged")
            css_class = "ok" if passed is True else "bad" if passed is False else ""
            reason = failure_reason(item)
            cell = f'<strong class="{css_class}">{verdict}</strong>' if css_class else verdict
            if reason:
                cell += f'<span class="rule-reason">{_e(reason)}</span>'
            cell += f'<span class="rule-actual">{t("public.detail.actual", "Actual: {value}", value=_e(compact(item.get("actual"))))}</span>'
            if isinstance(item.get("actual"), (dict, list)):
                cell += evidence_link(
                    item["actual"], t("public.detail.actual_label", "{label} · {rule} · Actual value", label=label, rule=rule), t("public.detail.full_actual", "Full actual value")
                )
            if item.get("assertions"):
                cell += evidence_link(
                    item["assertions"], t("public.detail.assertions_label", "{label} · {rule} · Assertions", label=label, rule=rule), t("public.detail.assertions", "Assertions")
                )
            cells.append(f"<td>{cell}</td>")
        answer_rows.append("<tr>" + "".join(cells) + "</tr>")
    answers = (
        f'<div class="table-wrap rule-comparison"><table><caption>{t("public.detail.rule_caption", "Acceptance rules: expected, actual and verdict")}</caption>'
        f'<thead><tr><th>{t("public.detail.rule", "Rule")}</th><th>{t("public.detail.rule_purpose", "Acceptance purpose")}</th><th>{t("public.detail.expected", "Expected")}</th>'
        + "".join(f"<th>{_e(label)}</th>" for label in labels)
        + "</tr></thead><tbody>"
        + "".join(answer_rows)
        + "</tbody></table></div>"
        if rules
        else (
            f'<p class="muted">{t("public.detail.interrupted_note", "The trajectory was interrupted and programmatic acceptance did not run; the rules of the task are still bound in advance.")}</p>'
            if process != "pass"
            else f'<p class="muted">{t("public.detail.no_rule_details", "No per-rule acceptance details were recorded for this run.")}</p>'
        )
    )
    answers += _source_mark_note(expected)
    derivation = (spec.get("ground_truth") or {}).get("derivation")
    answers += f'<p class="muted">{t("replay.detail.answer_basis", "Answer basis: {derivation}", derivation=_e(derivation))}</p>' if derivation else ""

    calls = spec.get("tool_calls") or []
    traces = [
        [
            row
            for row in (attempt.get("trace") or [])
            if row.get("action") not in {"evaluate_result", "validate_assertions", "prepare_environment"}
        ]
        for attempt in attempts
    ]
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
            + _show(attempt.get("execution_duration_ms", attempt.get("duration_ms")), suffix=" ms")
            + "</td><td>"
            + _show(attempt.get("duration_ms"), suffix=" ms")
            + "</td><td>"
            + (evidence_link(attempt.get("error"), t("replay.detail.label_full_exception", "{label} · Full exception", label=label)) or t("replay.detail.no_exception", "No exception"))
            + "</td></tr>"
        )
    execution_table = (
        f'<div class="table-wrap"><table><caption>{t("replay.detail.execution_records", "Execution records")}</caption><thead><tr><th>{t("replay.detail.record", "Record")}</th><th>{t("public.metric.duration", "Trajectory execution duration")}</th><th>{t("public.detail.wall_clock", "Overall wall clock (operations)")}</th><th>{t("replay.detail.exception_evidence", "Exception evidence")}</th></tr></thead><tbody>'
        + "".join(execution_rows)
        + "</tbody></table></div>"
    )
    resource_rows = []
    for _, metrics in METRICS:
        for spec in metrics:
            if spec.key == "duration_ms" or not any(
                spec.key in (a.get("resource") or {}) for a in attempts
            ):
                continue
            resource_rows.append(
                [
                    f"{spec.label} ({spec.unit})",
                    *[
                        _show((a.get("resource") or {}).get(spec.key), spec.divisor)
                        for a in attempts
                    ],
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


def _failed_validation_reason(attempt: dict[str, Any]) -> str:
    failed = [
        item
        for item in (attempt.get("validation") or [])
        if str(item.get("status") or "").lower() in {"failure", "fail", "failed", "error"}
        or item.get("passed") is False
        or item.get("score") == 0
    ]
    if not failed:
        return t("public.reason.final_mismatch", "Tool calls were uninterrupted, but final programmatic acceptance failed.")
    item = failed[0]
    evaluator = str(item.get("evaluator_name") or t("public.reason.acceptor", "Programmatic acceptor"))
    actual = item.get("actual")
    if evaluator == "AgentResponseEvaluator":
        return t("public.reason.answer", "Tool calls were uninterrupted, but the returned answer differs from the answer the evaluator expects.")
    if evaluator == "NetworkEventEvaluator" and not actual:
        return t("public.reason.no_network_event", "Tool calls were uninterrupted, but the HAR has no network event the evaluator requires.")
    if evaluator == "NetworkEventEvaluator":
        return t("public.reason.network_mismatch", "Tool calls were uninterrupted and the HAR has candidate events, but URL, parameters or request content did not satisfy the evaluator.")
    return t("public.reason.evaluator", "Tool calls were uninterrupted, but {evaluator} did not pass.", evaluator=evaluator)


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
                category = t("public.failure.acceptance", "Final acceptance mismatch")
                reason = _failed_validation_reason(final)
            elif action == "prepare_environment":
                category = t("public.failure.environment", "Environment preparation failed")
                reason = t("public.reason.environment", "Timed out during environment reset, initialization or login; the task actions did not finish.")
            else:
                category = t("public.failure.interaction", "Page interaction interrupted")
                if "intercepts pointer events" in lowered:
                    reason = t("public.reason.intercepted", "The target was located but covered or intercepted by another page element, and the click wait timed out.")
                elif "element is not visible" in lowered:
                    reason = t("public.reason.not_visible", "The target was located but not visible; the action wait timed out and later steps did not run.")
                elif "waiting for" in lowered:
                    reason = t("public.reason.element_timeout", "Waiting for the page element timed out; later steps did not run.")
                else:
                    reason = t("public.reason.tool_exception", "BrowserTool call raised an exception; later steps did not run.")
            reviewed[key] = {"category": category, "reason": reason}
            counts[category] += 1
            examples.setdefault(category, []).append(f"{task_id} · {engine}")
    descriptions = {
        t("public.failure.interaction", "Page interaction interrupted"): t("public.failure.interaction_meaning", "Element missing, not visible, covered or wait timed out; the call chain did not finish."),
        t("public.failure.environment", "Environment preparation failed"): t("public.failure.environment_meaning", "Reset, initialization or login failed; the task actions did not fully begin."),
        t("public.failure.acceptance", "Final acceptance mismatch"): t("public.failure.acceptance_meaning", "All tool calls returned, but the answer or HAR evidence did not satisfy the evaluator."),
    }
    rows = [
        [category, str(counts[category]), descriptions[category], t("replay.separator.clause", "; ").join(examples[category][:4])]
        for category in (t("public.failure.interaction", "Page interaction interrupted"), t("public.failure.environment", "Environment preparation failed"), t("public.failure.acceptance", "Final acceptance mismatch"))
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
            error_category = review["category"] if review else t("replay.execution.no_error", "No error")
            actions = sorted(
                {
                    str(call.get("action"))
                    for attempt in attempts
                    for call in (attempt.get("trace") or [])
                    if call.get("action")
                    not in {"evaluate_result", "validate_assertions", "prepare_environment"}
                }
            )
            tags = task.get("tags") or []
            final_duration = float(
                final_attempt.get("execution_duration_ms", final_attempt.get("duration_ms")) or 0
            )
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
                [final_attempt],
                diagnostic,
            )
            # Artifact Site injects a site-root base URL; fragment links need the file.
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
                    str(final_attempt.get("error") or ""),
                    json.dumps(final_attempt.get("validation") or [], ensure_ascii=False),
                    json.dumps(final_attempt.get("trace") or [], ensure_ascii=False),
                ]
            )
            blocks.append(
                f'<div id="{_e(anchor)}" class="execution-card" data-task="{_e(task_id)}" data-engine="{_e(engine)}" data-status="{_e(final_result_status)}" data-combined-status="{_e(final_combined_status)}" data-complexity="{_e(task.get("complexity"))}" data-error="{_e(error_category)}" data-actions="{_e(",".join(actions))}" data-tags="{_e(",".join(tags))}" data-search="{_e(_trace_text(search_text))}" data-duration="{_e(final_duration)}"><a href="{detail_name}" class="execution-summary"><span data-label="{t("replay.column.task", "Task")}">{_e(title)}</span><span data-label="{t("public.correctness.browser", "Browser")}">{_e(engine)}</span><span data-label="{t("public.suite.programmatic_acceptance", "Programmatic acceptance")}" class="{"ok" if final_result_status == "pass" else "bad"}">{t("replay.status.pass", "Pass") if final_result_status == "pass" else t("public.execution.not_passed", "Did not pass")}</span><span data-label="{t("public.execution.tool_call", "Tool call")}" class="{"ok" if final_process_status == "pass" else "bad"}">{t("public.detail.uninterrupted", "Uninterrupted") if final_process_status == "pass" else t("public.detail.interrupted", "Interrupted")}</span><span data-label="{t("replay.filter.error_category", "Error category")}">{_e(error_category)}</span><span data-label="{t("public.metric.duration", "Trajectory execution duration")}">{_show(final_duration, suffix=" ms")}</span></a></div>'
            )
    return "".join(blocks) or f"<p>{t('replay.execution.none', 'No execution samples to show.')}</p>"


def _task_comparison(summary: dict[str, Any]) -> str:
    engines = summary_engines(summary)
    rows = []
    for index, (task_id, task) in enumerate((summary.get("by_task") or {}).items(), 1):
        attempts_by_engine = task.get("attempts") or task.get("failures") or {}
        statuses: dict[str, str] = {}
        passed = []
        failed = []
        for engine in engines:
            attempts = attempts_by_engine.get(engine) or []
            final = attempts[-1] if attempts else {}
            status = str(final.get("result_status") or final.get("status") or "missing")
            statuses[engine] = status
            label = t("replay.status.pass", "Pass") if status == "pass" else t("public.execution.not_passed", "Did not pass") if status == "fail" else t("public.comparison.missing", "Record missing")
            css = "ok" if status == "pass" else "bad"
            link = (
                (
                    f'<a class="outcome-pill {css}" href="{_detail_filename(str(task_id), engine)}">'
                    f'{_e(engine_label(engine))} · {label}<span aria-hidden="true">→</span></a>'
                )
                if attempts
                else f'<span class="outcome-pill {css}">{_e(engine_label(engine))} · {label}</span>'
            )
            (passed if status == "pass" else failed).append(link)
        title = str(task.get("title") or task_id)
        rows.append(
            f'<tr class="comparison-row" data-statuses="{_e(json.dumps(statuses, ensure_ascii=False))}" '
            f'data-complexity="{_e(task.get("complexity") or "")}" '
            f'data-search="{_e(_trace_text(f"{task_id} {title}"))}"><td>{index}</td>'
            f'<td>{_e(title)}<small class="muted">{_e(task_id)}</small></td>'
            f'<td><div class="outcome-list">{"".join(passed) or "—"}</div></td>'
            f'<td><div class="outcome-list">{"".join(failed) or "—"}</div></td></tr>'
        )
    engine_options = "".join(
        f'<option value="{_e(engine)}">{_e(engine_label(engine))}</option>' for engine in engines
    )
    engine_checks = "".join(
        f'<label><input type="checkbox" value="{_e(engine)}" checked>{_e(engine_label(engine))}</label>'
        for engine in engines
    )
    engine_json = json.dumps(list(engines), ensure_ascii=False).replace("</", "<\\/")
    script = f"""<script>{COMPARISON_JS.replace("__ENGINE_LIST__", engine_json)}</script>"""
    return (
        f'<section class="card" id="task-comparison"><div class="section-heading"><div><h2>{t("public.comparison.title", "Browser combination comparison")}</h2>'
        f'<p class="muted">{t("public.comparison.note", "First choose baseline browser A and the compared browsers, then filter tasks by outcome relation. The table always keeps the result of every browser; open a status to reach the complete evidence.")}</p></div>'
        '<span id="comparison-count" class="muted"></span></div><div class="comparison-panel"><div class="matrix-controls">'
        f'<label>{t("public.comparison.browser_a", "Browser A")}<select id="compare-a">{engine_options}</select></label>'
        f'<label>{t("public.comparison.relation", "Outcome relation")}<select id="compare-relation"><option value="mixed">{t("public.comparison.mixed", "Results differ")}</option><option value="a_pass_others_fail">{t("public.comparison.a_pass", "A passed, the others did not")}</option><option value="a_fail_others_pass">{t("public.comparison.a_fail", "A did not pass, the others did")}</option><option value="all_pass">{t("public.comparison.all_pass", "All selected browsers passed")}</option><option value="all_fail">{t("public.comparison.all_fail", "No selected browser passed")}</option><option value="exact_pass">{t("public.comparison.exact_pass", "Only the selected browsers passed")}</option></select></label>'
        f'<label>{t("public.comparison.complexity", "Trajectory complexity")}<select id="compare-complexity"><option value="">{t("public.comparison.all_complexity", "All complexities")}</option><option value="simple">{t("replay.filter.simple", "Simple")}</option><option value="medium">{t("replay.filter.medium", "Medium")}</option><option value="complex">{t("replay.filter.complex", "Complex")}</option></select></label>'
        f'<label>{t("replay.suite.search", "Search tasks")}<input id="comparison-query" type="search" placeholder="{t("public.comparison.search_placeholder", "Task name or ID")}"></label></div>'
        f'<fieldset class="comparison-methods" id="compare-engines"><legend>{t("public.comparison.other_browsers", "Other compared browsers")}</legend><div class="engine-checks">{engine_checks}</div></fieldset></div>'
        f'<div class="table-wrap comparison-table" role="region" aria-label="{t("public.comparison.title", "Browser combination comparison")}" tabindex="0"><table><thead><tr><th>{t("public.comparison.index", "No.")}</th><th>{t("replay.column.task", "Task")}</th><th>{t("public.comparison.passing", "Passing browsers")}</th><th>{t("public.comparison.failing", "Failing browsers")}</th></tr></thead><tbody>'
        + "".join(rows)
        + f'</tbody></table></div><p class="muted">{t("public.comparison.footer", "Combination comparison locates differences; failure analysis explains direct mechanisms, and execution records keep actions, logs and programmatic acceptance details.")}</p></section>'
        + script
    )


def _execution_explorer(
    manifest: dict[str, Any], summary: dict[str, Any], detail_pages: dict[str, str]
) -> str:
    filter_anchors = "".join(
        f'<span id="provider={_e(engine)}&amp;status=fail" hidden></span>'
        for engine in (summary.get("functional") or {})
    )
    engines = summary_engines(summary)
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
            if c.get("action")
            not in {"evaluate_result", "validate_assertions", "prepare_environment"}
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
    error_options = f'<option value="{t("replay.execution.no_error", "No error")}">{t("replay.execution.no_error", "No error")}</option>' + "".join(
        f'<option value="{_e(category)}">{_e(category)}</option>'
        for category in diagnostic_categories
    )
    filters = f"""<div class="filters" id="execution-filters" aria-label="{t("replay.execution.filters_label", "Execution sample filters")}">
      <label>{t("replay.column.task", "Task")}<select id="f-task"><option value="">{t("replay.filter.all_tasks", "All tasks")}</option>{task_options}</select></label>
      <label>{t("public.correctness.browser", "Browser")}<select id="f-engine"><option value="">{t("public.filter.all_browsers", "All browsers")}</option>{''.join(f'<option value="{_e(engine)}">{_e(engine_label(engine))}</option>' for engine in engines)}</select></label>
      <label>{t("replay.detail.result", "Result")}<select id="f-status"><option value="">{t("replay.filter.all_results", "All results")}</option><option value="pass">{t("replay.execution.correct", "Correct")}</option><option value="fail" selected>{t("replay.execution.incorrect", "Incorrect")}</option></select></label>
      <label>{t("public.filter.acceptance_chain", "Acceptance and call chain")}<select id="f-combined"><option value="">{t("replay.filter.all", "All")}</option><option value="pass">{t("public.filter.both_ok", "Accepted with uninterrupted calls")}</option><option value="fail">{t("public.filter.one_failed", "At least one did not pass")}</option></select></label>
      <label>{t("replay.filter.difficulty", "Difficulty")}<select id="f-complexity"><option value="">{t("replay.filter.all_difficulties", "All difficulties")}</option><option value="simple">{t("replay.filter.simple", "Simple")}</option><option value="medium">{t("replay.filter.medium", "Medium")}</option><option value="complex">{t("replay.filter.complex", "Complex")}</option></select></label>
      <label>{t("replay.filter.browser_tool_method", "BrowserTool method")}<select id="f-action"><option value="">{t("replay.filter.all_methods", "All methods")}</option>{action_options}</select></label>
      <label>{t("replay.filter.task_tag", "Task tag")}<select id="f-tag"><option value="">{t("replay.filter.all_tags", "All tags")}</option>{tag_options}</select></label>
      <label>{t("replay.filter.error_category", "Error category")}<select id="f-error"><option value="">{t("replay.filter.all_categories", "All categories")}</option>{error_options}</select></label>
      <label>{t("public.metric.duration", "Trajectory execution duration")}<select id="f-duration"><option value="">{t("replay.filter.all_durations", "All durations")}</option><option value="lt1000">{t("replay.filter.under_1s", "Under 1 s")}</option><option value="1000-5000">{t("replay.filter.between_1_and_5s", "1-5 s")}</option><option value="gte5000">{t("replay.filter.over_5s", "5 s or more")}</option></select></label>
      <label>{t("replay.filter.full_text", "Full-text search")}<input id="f-query" type="search" placeholder="{t("replay.filter.full_text_placeholder", "Task, output or error")}"></label>
      <button type="button" id="f-all">{t("replay.filter.show_all", "Show all")}</button><button type="button" id="f-reset">{t("replay.filter.reset_to_errors", "Reset to errors")}</button><span class="filter-status" id="f-count" aria-live="polite"></span>
    </div>"""
    script = f"<script>{EXECUTION_FILTER_JS}</script>".replace("__ENGINE_LIST__", json.dumps(list(engines), ensure_ascii=False))
    final_failures = len(reviewed_failures)
    review_table = _table(
        [t("public.review.main_cause", "Main cause"), t("public.review.failed_results", "Failed results"), t("public.review.meaning", "Evidence meaning"), t("public.review.representative", "Representative tasks")],
        category_rows or [[t("public.review.none", "No failed results"), "0", "—", "—"]],
        t("public.review.caption", "Failure log review conclusions"),
    )
    grid_head = f'<div class="record-grid-head" aria-hidden="true"><span>{t("replay.column.task", "Task")}</span><span>{t("public.correctness.browser", "Browser")}</span><span>{t("public.suite.programmatic_acceptance", "Programmatic acceptance")}</span><span>{t("public.execution.tool_call", "Tool call")}</span><span>{t("replay.filter.error_category", "Error category")}</span><span>{t("public.metric.duration", "Trajectory execution duration")}</span></div>'
    return f"""{filter_anchors}<section class="card" id="failure-review"><div class="section-heading"><h2>{t("public.results.failure_analysis", "Failure analysis")}</h2><a href="#execution-results">{t("public.review.view_records", "View execution records")}</a></div><p>{t("public.review.body", "Groups <strong>{count}</strong> failed results by direct evidence in error logs, BrowserTool traces and evaluator output.", count=final_failures)}</p>{review_table}<p class="muted">{t("public.review.note", "This is not a second scoring. Categories only summarize direct failure mechanisms; Element not visible, HAR not matched and the like do not automatically mean a browser-internal root cause.")}</p></section><section class="card" id="execution-results"><h2>{t("replay.detail.execution_records", "Execution records")}</h2><p>{t("public.review.records_body", "{count} task × browser results. Failed results show by default; open a task to see the actions, answers, verdicts, error stack and BrowserTool trace of the adopted result.", count=logical_executions)}</p>{filters}<div id="execution-list" class="record-grid" role="region" aria-label="{t("public.review.records_label", "Per-task execution records")}" tabindex="0">{grid_head}{_execution_details(manifest, summary, detail_pages)}</div><p id="execution-empty" class="empty-state" role="status" hidden></p>{script}</section>"""


def _review_page(
    manifest: dict[str, Any], summary: dict[str, Any], result_audit: dict[str, Any]
) -> str:
    provenance = manifest.get("artifact_provenance") or {}
    engines = summary_engines(summary)
    evidence_fields = (manifest.get("protocol") or {}).get("required_attempt_evidence") or []
    rows = [
        ["候选来源", "179 条 WebArena 官方人类录制", "来源轨迹哈希逐条登记"],
        ["架构与 evaluator 预筛", "排除 4 条 arm64-only Wikipedia 与 3 条已证实 evaluator 缺陷", "规则在浏览器结果产生前冻结"],
        ["轨迹资格验证", "冷 Chromium 重放 + 官方程序 evaluator + 证据完整性", "仅通过任务进入正式 30 条分母"],
        [t("public.audit.task_matrix", "Formal task matrix"), t("public.audit.task_matrix_value", "{tasks} tasks × {browsers} browsers", tasks=len(manifest.get('suite') or []), browsers=len(engines)), t("public.audit.same_inputs", "Same tasks, trajectories, viewport, timeouts and acceptors")],
        [t("public.method.result_unit", "Result unit"), t("public.audit.result_unit_value", "Each task and browser publishes one result"), t("public.audit.same_scope", "Aggregates, task comparisons and details use the same scope")],
        [t("public.audit.result_scoring", "Result scoring"), t("public.audit.scoring_value", "AgentResponse / final service state / HAR network events"), t("public.audit.no_judge", "No LLM judge; missing required evidence fails")],
    ]
    checks = [
        [t("public.audit.manifest_binding", "Task manifest binding"), str(manifest.get("suite_sha256") or t("replay.not_recorded", "Not recorded")), t("replay.status.pass", "Pass")],
        [t("public.audit.attempts_binding", "attempts binding"), str(provenance.get("attempts_sha256") or t("replay.not_recorded", "Not recorded")), t("replay.status.pass", "Pass")],
        [t("public.audit.summary_binding", "summary binding"), str(provenance.get("summary_sha256") or t("replay.not_recorded", "Not recorded")), t("replay.status.pass", "Pass")],
        [t("public.audit.required_evidence", "Required evidence"), t("replay.separator.list", ", ").join(str(item) for item in evidence_fields), t("public.audit.missing_fails", "Missing means failure")],
        [t("public.audit.runtime_agent", "Run-time Agent / model"), t("public.audit.not_configured", "Not configured"), t("replay.status.pass", "Pass")],
        [t("public.audit.architecture", "Browser architecture"), str((manifest.get("environment") or {}).get("docker_architecture") or t("replay.not_recorded", "Not recorded")), t("replay.status.pass", "Pass")],
    ]
    checks.insert(
        3,
        [t("public.audit.consistency", "Result consistency audit"), t("public.audit.public_results", "{count} public results", count=result_audit["logical_results_reviewed"]), t("public.audit.all_passed", "All passed")],
    )
    return (
        f'<section class="card note" id="verification"><h2>{t("public.audit.conclusion", "Verification conclusion")}</h2><p><strong>{t("public.audit.conclusion_lead", "{count} public results passed the automatic consistency audit.", count=result_audit["logical_results_reviewed"])}</strong>{t("public.audit.conclusion_body", "The checks cover the task matrix, BrowserTool action sequences, official evaluator status, HAR and final page evidence status, and aggregate consistency. Failed results are classified programmatically from error stacks and call fields, with the original text kept in task details. The audit confirms internal record consistency; it is not a manual re-annotation of all answers, and error categories cannot directly imply a browser-internal root cause. Complete HAR, screenshots and page artifacts are kept in evidence outside the repository.")}</p></section>'
        f'<section class="card"><h2>{t("public.audit.to_denominator", "From public recordings to the formal denominator")}</h2>'
        + _table([t("public.audit.stage", "Stage"), t("public.audit.scope", "Scope"), t("public.audit.rule", "Acceptance rule")], rows, t("public.audit.caption", "Qualification and formal run audit")) + '</section>'
        f'<section class="card"><h2>{t("public.audit.data_integrity", "Data integrity")}</h2>'
        + _table([t("public.audit.check", "Check"), t("public.audit.bound_value", "Bound value"), t("public.audit.status", "Status")], checks, t("public.audit.current_binding", "Current run binding")) + '</section>'
        '<section class="card"><h2>仍然存在的边界</h2><ul><li>每个任务和浏览器只有一个公开结果，不构成置信区间。</li><li>任务来自一个冻结的 30 条子集；站点分组样本更少。</li><li>失败审查读取错误栈、BrowserTool trace 和 evaluator 输出，只能概括直接机制，不自动证明浏览器内部根因。</li></ul></section>'
    )


def _appendix_page(manifest: dict[str, Any]) -> str:
    p = manifest.get("protocol") or {}
    provenance = manifest.get("artifact_provenance") or {}
    fields = [
        ["task", "ground_truth / source / expected_actions", t("replay.appendix.field.task", "Unique result, fixed reference path and source")],
        [
            "attempt",
            "result_status / process_status / combined_status",
            t("public.appendix.status_fields", "Programmatic acceptance score and tool-call chain diagnostics"),
        ],
        ["attempt", "validation / trace / error", t("replay.appendix.field.validation", "Verdict and complete execution evidence of one task")],
        ["resource", "memory mean/p95/peak/retained/MiB·s", t("replay.appendix.field.memory", "Memory level, tail, peak, retention and integral")],
        ["resource", "CPU / network total/average/p95/peak", t("replay.appendix.field.cpu_network", "Compute and network timing pressure")],
        ["summary", "all-engine success intersection", t("public.appendix.intersection", "Absolute statistics on tasks every browser passed")],
        ["diagnostics", "rule_engine", t("replay.appendix.field.diagnostics", "Programmatic failure classification, evidence and raw execution record entry point")],
    ]
    provenance_rows = [
        [t("replay.appendix.run_id", "Run ID"), str(manifest.get("run_id") or t("replay.not_recorded", "Not recorded"))],
        [t("replay.appendix.generated_at", "Generated at"), str(manifest.get("created_at") or t("replay.not_recorded", "Not recorded"))],
        [t("replay.appendix.suite_sha", "Suite SHA-256"), str(manifest.get("suite_sha256") or t("replay.not_recorded", "Not recorded"))],
        ["attempts.jsonl SHA-256", str(provenance.get("attempts_sha256") or t("replay.not_recorded", "Not recorded"))],
        ["summary.json SHA-256", str(provenance.get("summary_sha256") or t("replay.not_recorded", "Not recorded"))],
    ]
    change_path = _report_input("report-changes.json")
    if not change_path.is_file():
        raise ValueError(f"trajectory report change log is missing: {change_path}")
    change_log = json.loads(change_path.read_text(encoding="utf-8"))
    if change_log.get("schema") != "lexmount_webarena_trajectory_report_changes/1":
        raise ValueError("trajectory report change log schema is invalid")
    change_records = change_log.get("releases")
    if not isinstance(change_records, list) or not change_records:
        raise ValueError("trajectory report change log must contain at least one release")
    changes = [[row.get("date"), row.get("type"), row.get("effect")] for row in change_records]
    from harness.trajectory_replay.report_inputs import replay_suite_binding
    digest = str(manifest.get("suite_sha256") or "")
    selected = manifest.get("suite_dataset")
    if selected is not None:
        if replay_suite_binding(digest, family="human", revision=selected["revision"]) != selected:
            raise ValueError("Replay suite dataset binding differs from the run input hash")
    else:
        selected = replay_suite_binding(digest, family="human") if digest else None
    if selected:
        expression = (
            "from harness.benchmarks.data import definition_path; "
            f"print(definition_path({selected['benchmark_dataset']!r}, "
            f"{selected['definition']!r}, {selected['revision']!r}))"
        )
        suite_argument = _e('"$(uv run --frozen python -c ' + shlex.quote(expression) + ')"')
    else:
        suite_argument = '&lt;frozen-suite.json&gt;'
    engine_arguments = _e(",".join(
        (manifest.get("protocol") or {}).get("engines") or DEFAULT_BROWSER_IMAGES
    ))
    run_command = (
        "uv run --frozen python -m harness.trajectory_replay.public_webarena.cli "
        f"--suite {suite_argument} "
        "--webarena-config &lt;runtime-config&gt; --env-file .env "
        "--browser-docker-host unix:///var/run/docker.sock "
        f"--engines {engine_arguments} --failure-retries 1 "
        "--output &lt;evidence&gt;/formal/webarena-public-trajectories/run "
        "--reports &lt;evidence&gt;/formal/webarena-public-trajectories/reports"
    )
    audit_command = (
        "uv run --frozen python examples/trajectory_replay/public_webarena/verify_metrics.py "
        "--artifacts &lt;evidence&gt;/formal/webarena-public-trajectories/run"
    )
    rebuild_command = (
        "uv run --frozen python examples/trajectory_replay/public_webarena/rebuild_report.py "
        "--artifacts &lt;evidence&gt;/formal/webarena-public-trajectories/run "
        "--reports &lt;evidence&gt;/formal/webarena-public-trajectories/reports"
    )
    return (
        f'<section class="card"><h2>{t("public.appendix.reproduce", "Reproduce this evaluation")}</h2>'
        f'<p>{t("public.appendix.reproduce_body", "Requires Docker, fixed site and browser images, WebArena test credentials and the locked Python dependencies of the repository. Using the task manifest bound by the SHA-256 of this run, the runner only executes tasks marked eligible in advance. A custom manifest needs its own frozen-suite.json and a check against the full hashes in the table above.")}</p>'
        f'<details><summary>{t("public.appendix.commands", "Expand run and verification commands")}</summary><pre>{run_command}\n\n{audit_command}\n\n{rebuild_command}</pre></details>'
        f'<p><a href="webarena-trajectory-replay-latest-data.json">{t("public.appendix.download", "Download the machine-readable audit summary")}</a></p></section>'
        f'<section class="card"><h2>{t("replay.appendix.binding", "Run and raw data binding")}</h2>'
        + _table([t("replay.column.item", "Item"), t("replay.column.value", "Value")], provenance_rows, t("replay.appendix.verifiable_source", "Verifiable source")) + '</section>'
        f'<section class="card"><h2>{t("replay.appendix.data_fields", "Data fields")}</h2>'
        + _table([t("replay.column.object", "Object"), t("replay.column.key_fields", "Key fields"), t("replay.column.purpose", "Purpose")], fields, t("replay.appendix.data_structure", "Data structure")) + '</section>'
        f'<section class="card"><h2>{t("replay.appendix.limits", "Reproduction limits")}</h2><ul>'
        f'<li>{t("public.appendix.limit_self_hosted", "Self-hosted sites reduce page drift, but images, source, credentials and initial state must stay fixed.")}</li>'
        f'<li>{t("public.appendix.limit_sampling", "The resource sampling interval is {interval} ms; shorter spikes can be missed.", interval=p.get("sample_interval_ms", "?"))}</li>'
        f'<li>{t("public.appendix.limit_memory", "Process-retained memory is not state pollution between tasks.")}</li></ul></section>'
        f'<section class="card"><h2>{t("public.appendix.changes", "Change log")}</h2><details><summary>{t("public.appendix.changes_summary", "Expand all reader-visible changes")}</summary>'
        + _table([t("public.appendix.date", "Date"), t("public.appendix.type", "Type"), t("public.appendix.impact", "Reader-visible impact")], changes, t("public.appendix.changes_caption", "Report changes"))
        + '</details></section>'
        f'<section class="card"><h2>{t("replay.appendix.raw_environment", "Raw environment JSON")}</h2><details><summary>{t("replay.appendix.expand", "Expand")}</summary><pre>'
        + _e(json.dumps(manifest.get("environment") or {}, ensure_ascii=False, indent=2, sort_keys=True))
        + '</pre></details></section>'
    )


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    paired_engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}

    def paired_duration(engine: str, stat: str) -> str:
        return _stat(((paired_engines.get(engine) or {}).get("duration_ms") or {}), stat)

    lines = [
        t("public.markdown.title", "# WebArena fixed-trajectory replay evaluation"),
        "",
        t("replay.markdown.run_id", "Run ID: `{run_id}`", run_id=manifest.get('run_id', 'unknown')),
        "",
        t("public.markdown.body", "This is RPA-style fixed-trajectory replay: it replays WebArena public human success trajectories and only compares browser and adapter compatibility, not Agent planning ability; success verdicts and failure classification are both programmatic."),
        "",
        t("public.markdown.header", "| Browser | Programmatic acceptance passes | P50 ms | Mean ms | P95 ms |"),
        "|---|---:|---:|---:|---:|",
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
    lines.append("")
    return "\n".join(lines)


def write_latest_report(
    reports_dir: str | Path, *, manifest: dict[str, Any], summary: dict[str, Any]
) -> Path:
    # Fail closed: an inconsistent result set must not render as a report.
    result_audit = audit_result_set(manifest, summary)
    manifest = public_projection(manifest)
    summary = public_projection(summary)
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    audit_name = f"{REPORT_STEM}-data.json"
    detail_pages: dict[str, str] = {}
    results = _results_page(manifest, summary, detail_pages)
    keep = report_file_names(summary)
    if set(detail_pages) != keep.difference(
        {f"{REPORT_STEM}.md", audit_name, *(filename for _, _, filename in PAGES)}
    ):
        raise ValueError("report detail inventory differs from the declared result population")
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
        "functional": _public_functional(summary),
        "resource_comparison": _public_resource_comparison(summary),
        "result_audit": {
            "schema": result_audit.get("schema"),
            "status": result_audit.get("status"),
            "logical_results_reviewed": result_audit.get("logical_results_reviewed"),
            "passes": result_audit.get("final_passes"),
            "failures": result_audit.get("final_failures"),
            "checks": [
                check
                for check in (result_audit.get("checks") or [])
                if "retry" not in str(check).lower()
            ],
            "scope": "all published task/browser results",
        },
    }
    (root / audit_name).write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    renderers = {
        "index": lambda: _summary_page(manifest, summary, result_audit),
        "results": lambda: results,
        "suite": lambda: _suite_page(manifest),
        "research": lambda: _research_page(manifest, summary),
        "method": lambda: _method_page(manifest, summary) + _review_page(manifest, summary, result_audit),
        "appendix": lambda: _appendix_page(manifest),
    }
    for slug, _, filename in PAGES:
        (root / filename).write_text(_shell(manifest, slug, renderers[slug]()), encoding="utf-8")
    for filename, document in detail_pages.items():
        (root / filename).write_text(document, encoding="utf-8")
    return md_path
