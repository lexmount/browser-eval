"""Bilingual research and input-compatibility reports for structured extraction."""

from __future__ import annotations

import json
import math
import re
import statistics
import struct
from html import escape as esc
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from harness.distribution import repository_root
from harness.env_file import local_setting
from harness.report_languages import resolve_report_language
from harness.reporting.presentation import check_presentation
from harness.reporting.templates import metric_contract
from harness.web_fetch.publication_privacy import project_evidence_view, publication_risk
from harness.web_scraper.analysis import failure_stage, field_results, native_error
from harness.web_scraper.pipeline import (
    PROFILE,
    digest,
    load_run,
    normalize,
    read_json,
    require_external_output,
    save_json,
    score_run,
    validate_dataset,
)
from harness.web_scraper.report_ui import CSS as INTERACTION_CSS
from harness.web_scraper.report_ui import JS

VERSION = "web-scraper-research/1"
STAGES = {
    "client_transport": ("客户端连接", "Client connection"),
    "model_capacity": ("模型容量", "Model capacity"),
    "navigation": ("页面加载", "Page navigation"),
    "provider_schema": ("服务端格式处理", "Provider schema handling"),
    "provider_fetch": ("服务端附加抓取", "Provider fetching"),
    "provider_response": ("服务端响应", "Provider response"),
    "fields": ("字段匹配", "Field matching"),
}
OUTCOMES = {
    "correct": ("符合标注", "Matches reference"),
    "missing": ("漏提取", "Missing"),
    "unexpected": ("多提取", "Unexpected"),
    "wrong": ("值不匹配", "Wrong value"),
}
FIELD_LABELS = {
    "price": ("价格", "Price"),
    "sku": ("SKU", "SKU"),
    "availability": ("库存", "Stock"),
    "OutOfStock": ("缺货识别", "Out of stock"),
}


def image_width(raw: bytes):
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        return struct.unpack(">I", raw[16:20])[0]
    if raw[:2] == b"\xff\xd8":
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 255:
                break
            marker = raw[offset + 1]
            if marker == 255:
                offset += 1
                continue
            offset += 2
            size = int.from_bytes(raw[offset : offset + 2], "big")
            if size < 2:
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and offset + 7 <= len(raw):
                return int.from_bytes(raw[offset + 5 : offset + 7], "big")
            offset += size
    raise ValueError("Unsupported screenshot image header")


LABELS = {
    "en": [
        "Overview",
        "Dataset",
        "Compatibility",
        "Protocol",
        "Candidates",
        "Research",
        "Accounts",
        "Research and compatibility only · no current quality ranking",
        "Theme",
        "Evidence",
        "Close",
        "Blocked",
        "Structured output",
        "All",
        "Case",
        "Method",
        "Status",
        "HTTP",
        "Seconds",
        "Reference",
        "Output",
        "Raw evidence",
        "Historical reproduction · 140 pages",
        "Price F1",
        "SKU F1",
        "Availability F1",
        "Pending",
    ],
    "zh": [
        "概览",
        "数据集",
        "兼容性实测",
        "评测协议",
        "候选选手",
        "相关研究",
        "账号与额度",
        "调研与输入兼容验证 · 尚无当前质量排名",
        "切换主题",
        "查看证据",
        "关闭",
        "受阻",
        "返回结构化字段",
        "全部",
        "题目",
        "方法",
        "状态",
        "HTTP",
        "秒",
        "标注",
        "输出",
        "原始证据",
        "历史结果复现 · 140 页",
        "价格 F1",
        "SKU F1",
        "库存 F1",
        "待验证",
    ],
}
PAGES = (
    "index",
    "dataset",
    "results",
    "protocol",
    "candidates",
    "research",
    "accounts",
    "review",
    "interpretation",
)
EXTRA_LABELS = {
    "review": {"zh": "标注与审查", "en": "Annotations and review"},
    "interpretation": {"zh": "报告解读", "en": "Interpretation"},
}
CSS = """*{box-sizing:border-box}html{color-scheme:light;--bg:#f8f9fb;--panel:white;--ink:#17283b;--muted:#516277;--line:#d7dfe8;--accent:#075f79;--soft:#e1f3ed}html.dark{color-scheme:dark;--bg:#101923;--panel:#172431;--ink:#e8f0f7;--muted:#afbfce;--line:#354b60;--accent:#7cdbed;--soft:#174638}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 system-ui,-apple-system,"PingFang SC",sans-serif}header,main,footer{max-width:1190px;margin:auto;padding:24px 32px}header{padding-bottom:0}.eyebrow{font-size:13px;letter-spacing:.06em;color:var(--muted)}h1{font-size:clamp(28px,4vw,44px);line-height:1.25;letter-spacing:-.025em;margin:18px 0}h2{font-size:25px;margin:34px 0 12px}h3{font-size:19px;margin:26px 0 10px}p{max-width:900px}a{color:var(--accent);text-underline-offset:3px}nav{display:flex;flex-wrap:wrap;gap:7px;margin:20px 0}nav a,button,select{font:inherit;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);padding:7px 12px;cursor:pointer;text-decoration:none}nav a[aria-current]{background:var(--ink);color:var(--bg)}.notice{border-left:4px solid var(--accent);padding:10px 16px;background:var(--panel);font-size:14px}.table-wrap{overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.inventory{max-height:540px}table{border-collapse:collapse;min-width:650px;width:100%;font-size:14px}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--panel);position:sticky;top:0;z-index:1;color:var(--muted);white-space:nowrap}td{max-width:440px}td code{overflow-wrap:anywhere}.comparison-winner{background:var(--soft);font-weight:700;padding:3px 7px;border-radius:5px}section{margin:24px 0 40px}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;list-style:none;padding:0;counter-reset:stage}.flow li{counter-increment:stage;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}.flow li:before{content:counter(stage,decimal-leading-zero);display:block;color:var(--accent);font-size:24px;font-weight:700}code,pre{font:12px/1.6 ui-monospace,monospace}pre{overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:8px;max-height:540px;white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}dialog{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;width:min(950px,94vw);max-height:90vh;overflow:auto}dialog::backdrop{background:#0008}dialog pre{max-height:62vh}details{margin:12px 0}summary{cursor:pointer;color:var(--accent)}footer{font-size:13px;color:var(--muted);border-top:1px solid var(--line)}.controls{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}.status{font-weight:600}small{color:var(--muted)}@media(max-width:650px){header,main,footer{padding:20px 16px}.flow{grid-template-columns:1fr 1fr}nav{gap:5px}nav a{font-size:13px;padding:6px 9px}td,th{padding:10px}}"""
CSS += ".diagram{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}.diagram img{display:block;width:100%;min-width:850px;height:auto}"
CSS += INTERACTION_CSS


def table(headers, rows, *, inventory=False, attrs=""):
    return (
        '<div class="table-wrap'
        + (" inventory" if inventory else "")
        + '"><table '
        + attrs
        + "><thead><tr>"
        + "".join("<th>" + h + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + str(c) + "</td>" for c in row) + "</tr>" for row in rows
        )
        + "</tbody></table></div>"
    )


def page(
    language, route, title, body, *, detail=False, languages=("en", "zh"), state="compatibility"
):
    labels = LABELS[language]
    if state != "compatibility":
        labels = list(labels)
        labels[7] = (
            "冻结商品页字段评测 · 机器评分，调用失败保留在分母中"
            if language == "zh"
            else "Frozen product-page extraction · machine scoring, failed calls retained"
        )
        labels[2] = "结果与失败" if language == "zh" else "Results and failures"
    other = "zh" if language == "en" else "en"
    nav = "".join(
        f'<a href="{p}.html"'
        + (' aria-current="page"' if route == p else "")
        + ">"
        + (EXTRA_LABELS[p][language] if p in EXTRA_LABELS else labels[i])
        + "</a>"
        for i, p in enumerate(PAGES)
    )
    other_route = route if not detail else "results"
    if other not in languages:
        other_route = "index"
    html = f'<!doctype html><html lang="{language}"><head><base href="../"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Scraper</title><link rel="stylesheet" href="../style.css"></head><body><header><div class="eyebrow">WEB SCRAPER / STRUCTURED EXTRACTION</div><h1>{esc(title)}</h1><p class="notice" data-evidence-state="{state}">{labels[7]}</p><nav>{nav}<a href="../{other}/{other_route}.html">{"中文" if other == "zh" else "English"}</a><button id="theme">{labels[8]}</button></nav></header><main>{body}</main><footer>Web Scraper · {VERSION} · <a href="../report-manifest.json">{"输入与文件校验" if language == "zh" else "Input and file bindings"}</a></footer><script src="../app.js"></script></body></html>'
    # Artifact Site injects a site-root base even on nested pages. Use the same
    # root-relative references locally, where our own base supplies that root.
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
        for attr in ("href", "src", "data-src"):
            value = element.get(attr)
            if value is not None and not urlsplit(value).scheme and not urlsplit(value).netloc:
                element[attr] = urljoin(f"/{language}/{route}.html", value).lstrip("/")
    return str(soup)


def cohort_source(source, selection, home, truth):
    """Validate a complete certified run or an immutable journal checkpoint."""
    contract = read_json(source / "contract.json")
    plan_path = Path(selection["plan"])
    plan = read_json(plan_path)
    precall = read_json(source / "certification-precall.json")
    if (
        digest(plan_path.read_bytes()) != selection["plan_sha256"]
        or precall["profile_sha256"] != selection["plan_sha256"]
        or set(contract["case_ids"]) != set(truth)
        or contract["kind"] != "full_archived_extraction"
        or plan["case_count"] != len(truth)
        or plan["case_ids_sha256"] != digest(("\n".join(sorted(truth)) + "\n").encode())
        or contract["methods"] != [precall["method"]]
        or precall["method"] not in plan["participants"]
        or precall["dataset_manifest_sha256"] != contract["dataset_manifest_sha256"]
        or precall["pipeline_sha256"] != contract["pipeline_sha256"]
    ):
        raise ValueError("Full cohort plan, population or execution binding differs")
    for relative, sha in [
        ("execution/pipeline.py", contract["pipeline_sha256"]),
    ]:
        if sha and digest((source / relative).read_bytes()) != sha:
            raise ValueError("Frozen execution changed")
    certificate = source / "certificate.json"
    if certificate.exists():
        contract, rows = load_run(source)
        cert = read_json(certificate)
        for name, key in [
            ("manifest.json", "manifest_sha256"),
            ("scores.json", "scores_sha256"),
            ("certification-precall.json", "precall_sha256"),
        ]:
            if digest((source / name).read_bytes()) != cert[key]:
                raise ValueError("Certified evidence changed")
        if cert["profile_sha256"] != selection["plan_sha256"] or cert["case_count"] != len(truth):
            raise ValueError("Certificate population or profile differs")
        saved = read_json(source / "scores.json")
        # Recompute through the original scoring boundary, without provider calls.
        recalculated = score_run(home, source)
        if saved != recalculated:
            raise ValueError("Certified scores do not reproduce")
        physical = read_json(source / "manifest.json").get("physical_calls", [])
        attempts_by_key = {}
        for entry in physical:
            attempt = read_json(source / entry["path"])
            attempts_by_key.setdefault((attempt["method"], attempt["case_id"]), []).append(attempt)
        for row in rows:
            attempts = attempts_by_key.get((row["method"], row["case_id"]), [])
            if len(attempts) > 1:
                row["physical_attempts"] = attempts
        assessment = None
        if selection.get("assessment"):
            assessment = selection["assessment"]
            assessment_path = Path(assessment["path"])
            if digest(assessment_path.read_bytes()) != assessment["sha256"]:
                raise ValueError("Field projection assessment changed")
            saved = score_run(home, source, reproject=True, output=assessment_path)
            rows = [
                {
                    **r,
                    "collected_normalized_fields": r.get("normalized_fields"),
                    "normalized_fields": normalize(r["method"], r.get("response", {})),
                    "projection_revision": saved["projection_revision"],
                }
                for r in rows
            ]
        repair_binding = []
        if selection.get("repair"):
            from harness.web_scraper.repair import compose

            revisions = selection["repair"]
            revisions = revisions if isinstance(revisions, list) else [revisions]
            for revision in revisions:
                rows, repaired_scores = compose(home, source, rows, revision)
                saved = {**saved, "scores": repaired_scores}
                directory = Path(revision["path"])
                receipt = read_json(directory / "certificate.json")
                repair_binding.append(
                    {
                        "artifact_id": directory.name,
                        "certificate_sha256": revision["sha256"],
                        "profile_sha256": receipt["profile_sha256"],
                        "repaired_cases": receipt["case_count"],
                        "inherited_cases": len(truth) - receipt["case_count"],
                    }
                )
        return (
            contract,
            rows,
            {
                "certified": True,
                "scores": saved["scores"],
                "certificate_sha256": digest(certificate.read_bytes()),
                "repair": repair_binding,
                "assessment": {"artifact_id": assessment_path.name, "sha256": assessment["sha256"]}
                if assessment
                else None,
            },
        )
    journal = source / "calls.journal.jsonl"
    raw = journal.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("Checkpoint contains an unfinished journal entry; retry after the write")
    latest, physical = {}, []
    for line in raw.splitlines():
        entry = json.loads(line)
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe checkpoint evidence path")
        path = source / relative
        row = read_json(path)
        if digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Checkpoint call changed")
        key = (row["method"], row["case_id"])
        if (
            key[0] not in contract["methods"]
            or key[1] not in truth
            or row["input_sha256"]
            != digest((home / "dataset/html" / (key[1] + ".html")).read_bytes())
        ):
            raise ValueError("Checkpoint call is outside the frozen input scope")
        physical.append(row)
        latest[key] = row
    rows = []
    for key, value in latest.items():
        row = dict(value)
        row["physical_attempts"] = [r for r in physical if (r["method"], r["case_id"]) == key]
        rows.append(row)
    return (
        contract,
        rows,
        {
            "access_blocked": read_json(source / "access-blocked.json")
            if (source / "access-blocked.json").exists()
            else None,
            "certified": False,
            "scores": {},
            "checkpoint_sha256": digest(raw),
            "physical_calls": len(physical),
        },
    )


def cohort_metrics(methods, rows, bindings, count, lang, labels=None):
    """One full-population summary, with incomplete quality left unavailable."""
    labels = labels or {}
    scores = {m: b["scores"][m] for b in bindings if b.get("certified") for m in b["scores"]}
    fields = ("price", "sku", "availability", "OutOfStock")
    blocked = {b["contract"]["methods"][0] for b in bindings if b.get("access_blocked")}
    records = []
    for method in methods:
        subset = [r for r in rows if r["method"] == method]
        complete = method in scores
        cells = [
            esc(labels.get(method, method)),
            str(count),
            str(len(subset)),
            str(sum(r.get("compatibility") == "structured_output" for r in subset)),
            ("已完成" if lang == "zh" else "Complete")
            if complete
            else (
                ("额度／权限受限" if lang == "zh" else "Access limited")
                if method in blocked
                else ("尚未完成" if lang == "zh" else "Incomplete")
            ),
        ]
        for field in fields:
            value = scores.get(method, {}).get(field, {}).get("f1")
            cells.append("—" if value is None else f"{value:.3f}")
        records.append(cells)
    result = BeautifulSoup(
        table(
            (
                [
                    "方法",
                    "计划页数",
                    "已执行",
                    "返回字段",
                    "全量状态",
                    "价格 F1",
                    "SKU F1",
                    "库存 F1",
                    "缺货 F1",
                ]
                if lang == "zh"
                else [
                    "Method",
                    "Planned pages",
                    "Executed",
                    "Structured output",
                    "Full scope",
                    "Price F1",
                    "SKU F1",
                    "Stock F1",
                    "Out-of-stock F1",
                ]
            ),
            records,
            attrs='data-core-metrics="full-cohort"',
        ),
        "html.parser",
    )
    for method, row in zip(methods, result.select("tbody tr"), strict=True):
        row["data-method"] = method
        for field, cell in zip(fields, row.select("td")[5:], strict=True):
            if method not in scores:
                continue
            # F1 comes from integer counts; discard only floating arithmetic noise.
            value = round(scores[method][field]["f1"], 12)
            values = [round(score[field]["f1"], 12) for score in scores.values()]
            cell.attrs.update(
                {"data-metric": field, "data-metric-direction": "max", "data-sort": str(value)}
            )
            if value == max(values) and values.count(value) == 1:
                cell.string = ""
                span = result.new_tag("span", attrs={"class": "comparison-winner"})
                span.string = f"{value:.3f}"
                cell.append(span)
            link = result.new_tag(
                "a", href=f"results.html?method={method}&field={field.lower()}&status=all"
            )
            for child in list(cell.contents):
                link.append(child.extract())
            cell.append(link)
    note = (
        "F1 取值 0–1，越高越好；只展示已完成全部页面并通过证据校验的方法。返回字段不代表字段正确。机器评分，尚未进行独立人工复核。"
        if lang == "zh"
        else "F1 ranges from 0 to 1; higher is better. Quality is shown only for complete verified cohorts. Structured output does not establish correctness. Machine scoring; no independent human review."
    )
    latency = []
    for method in methods:
        for successful in (False, True):
            selected = [
                r
                for r in rows
                if r["method"] == method
                and (not successful or r.get("compatibility") == "structured_output")
            ]
            values = sorted(
                r["elapsed_seconds"] for r in selected if r.get("elapsed_seconds") is not None
            )
            label = (
                ("返回字段调用" if successful else "所有已执行调用")
                if lang == "zh"
                else ("Structured-output calls" if successful else "All executed calls")
            )
            retry_time = f"{sum(r.get('retry_execution_seconds', 0) for r in selected):.2f}"
            unknown_retries = sum(r.get("retry_missing_timers", 0) for r in selected)
            if unknown_retries:
                retry_time = (
                    f"≥ {retry_time}（另 {unknown_retries} 次缺计时）"
                    if lang == "zh"
                    else f"≥ {retry_time} ({unknown_retries} additional attempts untimed)"
                )
            latency.append(
                [
                    esc(labels.get(method, method)),
                    label,
                    str(len(values)),
                    str(len(selected) - len(values)),
                    retry_time,
                    f"{statistics.mean(values):.3f}" if values else "—",
                    f"{statistics.median(values):.3f}" if values else "—",
                    f"{values[max(0, math.ceil(len(values) * 0.95) - 1)]:.3f}" if values else "—",
                ]
            )
    timing = BeautifulSoup(
        table(
            [
                "Method",
                "Population",
                "Timed N",
                "Untimed N",
                "Extra retry time (s)",
                "Mean (s)",
                "P50 (s)",
                "P95 (s)",
            ]
            if lang == "en"
            else [
                "方法",
                "统计范围",
                "有计时",
                "无计时",
                "重试额外耗时（秒）",
                "平均（秒）",
                "P50（秒）",
                "P95（秒）",
            ],
            latency,
            attrs='data-latency-table="true"',
        ),
        "html.parser",
    )
    groups = {}
    for ordinal, row in enumerate(timing.select("tbody tr")):
        population = "structured" if ordinal % 2 else "all"
        for metric, cell in zip(("mean", "p50", "p95"), row.select("td")[5:], strict=True):
            method = methods[ordinal // 2]
            selected = [
                r["elapsed_seconds"]
                for r in rows
                if r["method"] == method
                and r.get("elapsed_seconds") is not None
                and (population == "all" or r.get("compatibility") == "structured_output")
            ]
            if not selected:
                continue
            value = (
                statistics.mean(selected)
                if metric == "mean"
                else statistics.median(selected)
                if metric == "p50"
                else sorted(selected)[max(0, math.ceil(len(selected) * 0.95) - 1)]
            )
            name = population + "-" + metric
            cell.attrs.update(
                {"data-metric": name, "data-metric-direction": "min", "data-sort": str(value)}
            )
            groups.setdefault(name, []).append((value, cell))
    for values in groups.values():
        lowest = min(v for v, _ in values)
        if sum(v == lowest for v, _ in values) == 1:
            cell = next(cell for value, cell in values if value == lowest)
            value = cell.get_text()
            cell.clear()
            span = timing.new_tag("span", attrs={"class": "comparison-winner"})
            span.string = value
            cell.append(span)
    return str(result) + "<p>" + note + "</p>", str(timing)


def price_table(methods, prices, lang, labels=None):
    labels = labels or {}
    records = []
    for method in methods:
        value = prices.get(method, {})
        sources = value.get("sources", [])
        if (
            not value.get("usd_per_1000")
            or not value.get("terms", {}).get(lang)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.get("verified_at", ""))
            or not sources
            or any(urlsplit(u).scheme != "https" for u in sources)
        ):
            raise ValueError("Every participant requires sourced cost/pricing terms: " + method)
        records.append(
            [
                esc(labels.get(method, method)),
                esc(value["usd_per_1000"]),
                esc(value["terms"][lang]),
                esc(value["verified_at"]),
                " · ".join(
                    f'<a href="{esc(u, quote=True)}">{i}</a>' for i, u in enumerate(sources, 1)
                ),
            ]
        )
    return table(
        ["方法", "美元／1,000 次", "计费条件与套餐门槛", "核对日期", "官方来源"]
        if lang == "zh"
        else [
            "Method",
            "USD / 1,000 requests",
            "Billing terms and commitment",
            "Verified",
            "Official sources",
        ],
        records,
        attrs='data-price-table="true"',
    )


def usage_table(methods, rows, lang, labels=None):
    labels = labels or {}
    records = []
    for method in methods:
        subset = [r for r in rows if r["method"] == method]
        physical = [a for r in subset for a in r.get("physical_attempts", [r])]
        observed = []
        for r in physical:
            headers = r.get("provider_usage", {})
            response = r.get("response")
            response = response if isinstance(response, dict) else {}
            data = response.get("data")
            metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
            value = headers.get("X-Scrapfly-Api-Cost", metadata.get("creditsUsed"))
            if value is not None:
                observed.append(float(value))
        records.append(
            [
                esc(labels.get(method, method)),
                str(len(physical)),
                str(len(observed)),
                f"{sum(observed):g} credits" if observed else "—",
            ]
        )
    return table(
        ["Method", "Physical attempts", "Usage receipts", "Reported usage"]
        if lang == "en"
        else ["方法", "物理调用数", "有用量回执", "服务报告用量"],
        records,
        attrs='data-usage-table="true"',
    )


def operating_metrics(methods, rows, prices, lang, labels):
    """Home-page latency and price, with the measured population kept explicit."""
    price_table(methods, prices, lang, labels)
    records = []
    for method in methods:
        selected = [r for r in rows if r["method"] == method]
        values = sorted(
            r["elapsed_seconds"] for r in selected if r.get("elapsed_seconds") is not None
        )
        stats = (
            [
                statistics.mean(values),
                statistics.median(values),
                values[max(0, math.ceil(len(values) * 0.95) - 1)],
            ]
            if values
            else [None] * 3
        )
        records.append(
            [
                esc(labels.get(method, method)),
                str(len(values)),
                str(len(selected) - len(values)),
                *[f"{v:.3f}" if v is not None else "—" for v in stats],
                esc(prices[method]["usd_per_1000"]),
                '<a href="accounts.html">'
                + ("计费条件与来源" if lang == "zh" else "Terms and sources")
                + "</a>",
            ]
        )
    soup = BeautifulSoup(
        table(
            [
                "方法",
                "有计时",
                "缺计时",
                "平均（秒）",
                "P50（秒）",
                "P95（秒）",
                "报价：美元／1,000 次",
                "计费说明",
            ]
            if lang == "zh"
            else [
                "Method",
                "Timed N",
                "Untimed N",
                "Mean (s)",
                "P50 (s)",
                "P95 (s)",
                "Quoted USD / 1,000 calls",
                "Billing",
            ],
            records,
            attrs='data-operating-metrics="true" data-sortable',
        ),
        "html.parser",
    )
    for index, metric in enumerate(("mean", "p50", "p95"), 3):
        observed = []
        for method, row in zip(methods, soup.select("tbody tr"), strict=True):
            cell = row.select("td")[index]
            if cell.get_text() != "—":
                # Use unrounded values for direction and ties.
                values = sorted(
                    r["elapsed_seconds"]
                    for r in rows
                    if r["method"] == method and r.get("elapsed_seconds") is not None
                )
                value = (
                    statistics.mean(values)
                    if metric == "mean"
                    else statistics.median(values)
                    if metric == "p50"
                    else values[max(0, math.ceil(len(values) * 0.95) - 1)]
                )
                cell.attrs.update(
                    {
                        "data-sort": str(value),
                        "data-metric": "home-" + metric,
                        "data-metric-direction": "min",
                    }
                )
                observed.append((value, cell))
        if observed:
            low = min(v for v, _ in observed)
            if sum(v == low for v, _ in observed) == 1:
                cell = next(c for v, c in observed if v == low)
                span = soup.new_tag("span", attrs={"class": "comparison-winner"})
                span.string = cell.get_text()
                cell.clear()
                cell.append(span)
    note = (
        "耗时统计覆盖每个方法全部已执行页面，包括失败；每题取当前最终调用。重试额外耗时另见评测协议。报价保留套餐与用量条件，不能当作本轮实付金额。"
        if lang == "zh"
        else "Latency includes all executed cases, including failures, using the current final call per case. Extra retry time is reported in Protocol. Quotes retain plan and usage conditions and are not actual charges."
    )
    return (
        "<section data-home-operations><h2>"
        + ("耗时与价格" if lang == "zh" else "Latency and pricing")
        + "</h2><p>"
        + note
        + "</p>"
        + str(soup)
        + "</section>"
    )


def field_table(row, lang):
    labels = {"price": "价格", "sku": "SKU", "availability": "库存", "OutOfStock": "缺货识别"}
    outcomes = {
        "correct": "符合标注",
        "missing": "漏提取",
        "unexpected": "多提取",
        "wrong": "值不匹配",
    }
    records = []
    for field, value in row["field_results"].items():
        records.append(
            [
                labels[field] if lang == "zh" else field,
                esc(json.dumps(value["expected"], ensure_ascii=False)),
                esc(json.dumps(value["actual"], ensure_ascii=False)),
                outcomes[value["status"]] if lang == "zh" else value["status"],
                str(value["tp"]),
                str(value["fp"]),
                str(value["fn"]),
            ]
        )
    return table(
        ["字段", "参考值（可接受其一）", "输出", "判定", "命中 TP", "误报 FP", "漏报 FN"]
        if lang == "zh"
        else ["Field", "Accepted reference values", "Output", "Outcome", "TP", "FP", "FN"],
        records,
        attrs='data-field-diagnostics="true"',
    )


def result_controls(methods, rows, names, lang):
    def select(key, title, options):
        return (
            "<label>"
            + title
            + f'<select data-result-filter="{key}">'
            + "".join(
                f'<option value="{esc(value, quote=True)}">{esc(label)}</option>'
                for value, label in options
            )
            + "</select></label>"
        )

    zh = lang == "zh"
    return (
        '<form class="controls" data-result-controls onsubmit="return false">'
        + select(
            "status",
            "结果" if zh else "Outcome",
            [
                ("fail", "有字段差异或调用失败" if zh else "Mismatch or call failure"),
                ("pass", "全部字段符合标注" if zh else "All fields match"),
                ("all", "全部" if zh else "All"),
            ],
        )
        + select(
            "method",
            "方法" if zh else "Method",
            [("", "全部方法" if zh else "All methods"), *[(m, names.get(m, m)) for m in methods]],
        )
        + select(
            "field",
            "字段" if zh else "Field",
            [
                ("", "任一字段" if zh else "Any field"),
                ("price", "价格" if zh else "Price"),
                ("sku", "SKU"),
                ("availability", "库存" if zh else "Stock"),
                ("outofstock", "缺货识别" if zh else "Out of stock"),
            ],
        )
        + select(
            "stage",
            "环节" if zh else "Stage",
            [
                ("", "全部环节" if zh else "All stages"),
                *[
                    (s, STAGES[s][0 if zh else 1])
                    for s in sorted({r["failure_stage"] for r in rows})
                ],
            ],
        )
        + (
            "<label>"
            + ("搜索" if zh else "Search")
            + '<input type="search" data-result-filter="query"'
            + (
                ' aria-label="搜索题号、域名或错误"'
                if zh
                else ' aria-label="Search case, domain or error"'
            )
            + '></label><output data-result-count aria-live="polite"></output></form>'
        )
    )


def audit_explanation(audit, rows, methods, labels, lang, *, core_html=None):
    """Reader-facing review scope and source-qualified correction inventory."""
    zh = lang == "zh"
    count = audit["result_count"]
    changed = len(audit["reference_changes"])
    review = (
        f"<section data-complete-review><h2>{'完整审查范围' if zh else 'Complete audit scope'}</h2>"
        + (
            f"<p>{audit['case_count']} 页、{count} 个当前结果全部覆盖；原始 {audit['original_results_reviewed']} 个结果逐字段审查，另审查 {audit['supplemental_results_reviewed']} 个补跑后的响应。未审查放行数为 0。</p>"
            if zh
            else f"<p>All {audit['case_count']} pages and {count} current results are covered. The original {audit['original_results_reviewed']} results were reviewed field by field, with {audit['supplemental_results_reviewed']} changed responses audited separately. No unreviewed passes were accepted.</p>"
        )
        + "<p>"
        + (
            "审查模型为 "
            + esc(audit["reviewer"]["model"])
            + "，依据冻结原文、标注和完整原生响应核查，并按原评测规则裁决争议；这不等于双人人工金标。逐题页面保留原文片段与裁决理由。"
            if zh
            else "The model audited frozen sources, annotations and complete native responses, followed by original-rule adjudication. This is not dual human annotation. Quotations are checked against frozen bytes; literal excerpts are retained separately from original opinions."
        )
        + "</p></section>"
    )
    fields = ("price", "sku", "availability", "OutOfStock")
    comparison = BeautifulSoup(
        table(
            [
                "方法" if zh else "Method",
                *[FIELD_LABELS[f][0 if zh else 1] + " F1" for f in fields],
            ],
            [
                [
                    esc(labels.get(method, method)),
                    *[f"{audit['audited_scores'][method][field]['f1']:.3f}" for field in fields],
                ]
                for method in methods
            ],
            attrs='data-audited-quality="true" data-sortable',
        ),
        "html.parser",
    )
    for method, row in zip(methods, comparison.select("tbody tr"), strict=True):
        for field, cell in zip(fields, row.select("td")[1:], strict=True):
            value = round(audit["audited_scores"][method][field]["f1"], 12)
            values = [round(audit["audited_scores"][m][field]["f1"], 12) for m in methods]
            cell.attrs.update(
                {
                    "data-sort": str(value),
                    "data-metric": "audited-" + field,
                    "data-metric-direction": "max",
                }
            )
            if value == max(values) and values.count(value) == 1:
                span = comparison.new_tag("span", attrs={"class": "comparison-winner"})
                span.string = cell.get_text()
                cell.clear()
                cell.append(span)
    review += (
        "<h2>"
        + ("核查参考值后的诊断 F1" if zh else "Diagnostic F1 after reference audit")
        + "</h2><p>"
    )
    review += (
        (
            f"各方法仍为 {audit['case_count']} 页，使用相同原生输出和同一个官方评分器，仅替换有源证据的参考值。该表属于事后源证据诊断，不替代首页保留原标注的成绩。"
            if zh
            else f"Each method still covers {audit['case_count']} pages. The same native outputs and official scorer use only source-qualified reference amendments. This post-hoc diagnostic does not replace the original-reference scores on the home page."
        )
        + "</p>"
        + str(comparison)
    )
    review += (
        "<h2>"
        + (f"参考值修正：{changed} 个字段" if zh else f"Reference amendments: {changed} fields")
        + "</h2><p>"
    )
    review += (
        "保留原始标注与原始评分；下表列出有源证据的修正，逐题详情同时显示两种口径。空标注和库存默认值均按原规则核对，不能把未知库存直接当成标注错误。"
        if zh
        else "Original annotations and scores are retained. These source-qualified amendments are shown alongside the original result in each detail. Empty labels and stock defaults follow the original benchmark rules."
    ) + "</p>"
    review += table(
        ["题目", "字段", "原参考值", "核查后参考值", "证据与裁决"]
        if zh
        else ["Case", "Field", "Original", "Audited", "Evidence and adjudication"],
        [
            [
                f'<a href="source-{r["case_id"]}.html"><code>{r["case_id"][:12]}</code></a>',
                FIELD_LABELS[r["field"]][0 if zh else 1],
                esc(json.dumps(r["original"], ensure_ascii=False)),
                esc(json.dumps(r["accepted"], ensure_ascii=False)),
                esc(r["reason"]),
            ]
            for r in audit["reference_changes"]
        ],
        inventory=True,
        attrs='data-reference-revisions="true" data-sortable',
    )
    excluded = audit.get("excluded_fields", {})
    if excluded:
        review += (
            "<h2>"
            + ("冻结源内容存在冲突的字段" if zh else "Conflicting source fields")
            + "</h2><p>"
        )
        review += (
            "这些字段在原始分数中仍保留；核查诊断单独标记源内冲突，不把它归为提取器错误。"
            if zh
            else "These fields remain in official scores; audited diagnostics identify source conflicts separately from extractor errors."
        ) + "</p>"
        review += table(
            ["Case", "Field"],
            [
                [f'<a href="source-{cid}.html">{cid[:12]}</a>', ", ".join(fields)]
                for cid, fields in excluded.items()
            ],
        )
    interpretation = (
        "<section data-interpretation-summary><h2>"
        + ("如何读这次比较" if zh else "Reading this comparison")
        + "</h2><p>"
    )
    structured = sum(r.get("compatibility") == "structured_output" for r in rows)
    interpretation += (
        f"{len(methods)} 个方法各执行 {audit['case_count']} 页，共 {count} 个结果；当前 {structured} 个返回结构化字段。原始失败修复后保留成功结果，不能把设置不同、日期不同的调用当作同环境因果速度实验。首页分别给出字段 F1、端到端耗时和有条件的报价。"
        if zh
        else f"{len(methods)} methods each executed {audit['case_count']} pages: {count} results, {structured} structured responses. Failure repairs retain successful originals; settings and dates differ, so latency is observational. Quality, end-to-end latency and conditional quotes are separate."
    ) + "</p></section>"
    if core_html:
        compact = BeautifulSoup(core_html, "html.parser").select_one("table")
        for row in compact.select("tr"):
            for index, cell in enumerate(list(row.find_all(["th", "td"], recursive=False))):
                if index not in (0, 3, 5, 6, 7, 8):
                    cell.decompose()
        compact.attrs = {"data-interpretation-comparison": "true"}
        interpretation += (
            "<h2>"
            + ("原标注下的紧凑比较" if zh else "Compact original-reference comparison")
            + "</h2>"
            + '<div class="table-wrap">'
            + str(compact)
            + "</div>"
        )
    interpretation += (
        "<h2>" + ("评分看的是字段是否匹配" if zh else "Scoring checks field matches") + "</h2><p>"
    )
    interpretation += (
        "每个字段允许多个参考值，但每次只接受一个预测值。价格按 Decimal 比较；错误值会同时造成误报与漏报，空值遇到非空参考值则计漏报。库存没有明确缺货证据时，原规则默认有货。原标注的金额分隔符、商品 ID 和库存问题会影响分数，因此源核查与原始成绩同时保留。"
        if zh
        else "Each field allows multiple accepted reference values but one prediction. Prices use Decimal equality; wrong values add false positives and false negatives, while missing required values add false negatives. The original contract defaults to InStock without clear out-of-stock evidence. Source-qualified reference corrections are retained alongside official scores."
    ) + "</p>"
    if audit["reference_changes"]:
        example = audit["reference_changes"][0]
        interpretation += (
            '<p><a href="source-'
            + example["case_id"]
            + '.html">'
            + (
                "查看原文、原标注与修正示例"
                if zh
                else "Inspect a source, annotation and correction example"
            )
            + '</a> · <a href="case-'
            + example["case_id"]
            + '.html">'
            + ("比较该题全部方法" if zh else "Compare every method on that case")
            + "</a></p>"
        )
    for method in methods:
        selected = [r for r in rows if r["method"] == method]
        counts = []
        for field in ("price", "sku", "availability"):
            missing = sum(r["field_results"][field]["status"] == "missing" for r in selected)
            wrong = sum(
                r["field_results"][field]["status"] in ("wrong", "unexpected") for r in selected
            )
            counts.append(
                [
                    FIELD_LABELS[field][0 if zh else 1],
                    str(missing),
                    str(wrong),
                    f'<a href="results.html?method={method}&field={field}&status=fail">'
                    + ("查看全部差异" if zh else "Inspect all differences")
                    + "</a>",
                ]
            )
        failures = [r for r in selected if r.get("compatibility") != "structured_output"]
        interpretation += (
            '<section data-method-interpretation="'
            + method
            + '"><h2>'
            + esc(labels.get(method, method))
            + "</h2><p>"
        )
        interpretation += (
            f"{len(selected)} 页中 {len(selected) - len(failures)} 页返回字段；下面按原标注列出全部字段差异，调用失败也计入漏提。"
            if zh
            else f"{len(selected) - len(failures)}/{len(selected)} pages returned structured fields. All original-reference differences are counted below; failed calls also contribute missing fields."
        ) + "</p>"
        interpretation += table(
            ["字段", "漏提取", "值错误或多提", "逐题证据"]
            if zh
            else ["Field", "Missing", "Wrong or unexpected", "Cases"],
            counts,
        )
        if method == "extruct":
            interpretation += (
                "<p>"
                + (
                    "上游基线在缺少库存时写入 InStock 辅助键，却没有写回主 availability 字段；原始库存分数因此与 README 的默认规则不一致。本次从同一原生输出中投影唯一明确的库存辅助键，没有新增本地提取调用；原分数仍保留在历史复现中。它只读取嵌入式结构化数据，和托管 API 的运行位置及能力范围不同。"
                    if zh
                    else "The upstream baseline emits an InStock indicator without filling availability when stock is absent. Its original availability score therefore differs from the README default. The current projection uses only an unambiguous native indicator, without new extraction calls; historical scores remain intact. This embedded-data baseline differs from hosted APIs in execution location and scope."
                )
                + "</p>"
            )
        repaired = [r for r in selected if r.get("diagnosis")]
        representative = (
            failures[:1]
            or repaired[:1]
            or [
                r
                for r in selected
                if any(f["status"] != "correct" for f in r["field_results"].values())
            ][:1]
        )
        if representative:
            row = representative[0]
            diagnosis = row.get("diagnosis", {})
            interpretation += (
                "<p>"
                + esc(
                    diagnosis.get("cause")
                    or (
                        "接口已经返回字段，差异发生在提取内容与参考值的匹配；原文与参考值核查见同题详情。"
                        if zh
                        else "Fields were returned; these differences concern extracted content versus references. Inspect source and reference audit in the same-case detail."
                    )
                )
                + "</p><p>"
            )
            interpretation += (
                f'<a href="result-{method}-{row["case_id"]}.html">'
                + ("代表结果与完整响应" if zh else "Representative result and complete response")
                + "</a></p>"
            )
        interpretation += "</section>"
    interpretation += (
        '<p><a href="review.html">'
        + ("完整审查范围与参考值修正" if zh else "Complete audit scope and reference amendments")
        + '</a> · <a href="protocol.html">'
        + ("评分、计时及调用协议" if zh else "Scoring, timing and execution protocol")
        + "</a></p>"
    )
    return review, interpretation


def publication_host_names():
    return tuple(
        host.strip()
        for host in local_setting(
            "WEB_SCRAPER_PUBLICATION_HOSTS", repository_root() / ".env"
        ).split(",")
        if host.strip()
    )


def build(output: Path, content: Path, *, home: Path, run=None, language=None, profile=PROFILE):
    language = resolve_report_language(output, language)
    if language not in LABELS:
        raise ValueError("Unsupported language")
    languages = ("zh",) if language == "zh" else ("en", "zh")
    model = read_json(content)
    names = model.get("method_labels", {})
    if language == "en" and any(
        page.get("body", {}).get("zh") and not page.get("body", {}).get("en")
        for page in model["pages"].values()
    ):
        raise ValueError("English content is missing; generate the English edition first")
    cohort = model.get("mode") == "full-cohort-progress"
    state = "full-cohort-progress" if cohort else "compatibility"
    cohort_plan = None
    if cohort:
        cohort_plan = read_json(Path(model["cohort_plan"]["path"]))
        if (
            digest(Path(model["cohort_plan"]["path"]).read_bytes())
            != model["cohort_plan"]["sha256"]
        ):
            raise ValueError("Report cohort plan changed")
    validate_dataset(home, profile)
    binding = read_json(profile).get("research_report", {}).get("content", {}).get("sha256")
    if binding and digest(content.read_bytes()) != binding:
        raise ValueError("Report prose differs from the profile-bound source")
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    history = read_json(home / "historical-reproduction.json")
    if history["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Reproduction belongs to different inputs")
    selections = (
        model.get("evidence_runs", []) if run is None else [{"run": str(run), "methods": None}]
    )
    rows = []
    bindings = []
    for selection in selections:
        source = Path(selection["run"])
        source = source if source.is_absolute() else home / "runs" / source
        extra = {}
        if cohort:
            if selection.get("methods") is not None and set(selection["methods"]) != set(
                read_json(source / "contract.json")["methods"]
            ):
                raise ValueError("Full-cohort filters must retain the declared method")
            selected_plan = selection["plan_sha256"]
            for method in read_json(source / "contract.json")["methods"]:
                authorized_plan = model.get("source_cohorts", {}).get(
                    method, model["cohort_plan"]["sha256"]
                )
                if selected_plan != authorized_plan:
                    raise ValueError(
                        "Selected source cohort is not bound by the report composition"
                    )
            contract, records, extra = cohort_source(source, selection, home, truth)
        else:
            contract, records = load_run(source)
        if not cohort and contract.get("kind") not in (
            "input_compatibility_only",
            "input_size_boundary_only",
        ):
            raise ValueError(
                "Research report accepts compatibility evidence only; full quality cohorts need their own certified report mode"
            )
        if contract["dataset_manifest_sha256"] != history["dataset_manifest_sha256"]:
            raise ValueError("Run belongs to different inputs")
        records = [
            r
            for r in records
            if selection.get("methods") is None or r["method"] in selection["methods"]
        ]
        if (
            cohort
            and extra.get("certified")
            and (len(records) != len(truth) or {r["case_id"] for r in records} != set(truth))
        ):
            raise ValueError("Certified report observations must cover every case")
        for record in records:
            r = json.loads(json.dumps(record))
            r["source_logical_sha256"] = digest(
                json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
            )
            r["run_id"] = source.name
            if "request" in r:
                r["request"]["endpoint"] = re.sub(
                    r"/accounts/[^/]+/", "/accounts/[REDACTED]/", r["request"]["endpoint"]
                )
            rows.append(r)
        bindings.append(
            {
                "run_id": source.name,
                "manifest_sha256": digest((source / "manifest.json").read_bytes())
                if (source / "manifest.json").exists()
                else None,
                **extra,
                "source_plan_sha256": selection.get("plan_sha256"),
                "methods": selection.get("methods"),
                "contract": contract,
            }
        )
    if cohort:
        expected_cases = digest(("\n".join(sorted(truth)) + "\n").encode())
        if (
            cohort_plan["case_count"] != len(truth)
            or cohort_plan["case_ids_sha256"] != expected_cases
            or set(cohort_plan["participants"])
            != {m for b in bindings for m in b["contract"]["methods"]}
            or len(bindings) != len(cohort_plan["participants"])
        ):
            raise ValueError("Report is missing a planned method or uses a substituted population")
    keys = [(r["method"], r["case_id"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Select exactly one compatibility observation per method and case")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    diagnoses = {}
    if model.get("diagnoses"):
        binding = model["diagnoses"]
        source = content.parent / binding["path"]
        if digest(source.read_bytes()) != binding["sha256"]:
            raise ValueError("Failure diagnoses changed")
        diagnoses = read_json(source)["results"]
    audit = None
    if model.get("audit"):
        binding = model["audit"]
        source = content.parent / binding["path"]
        if digest(source.read_bytes()) != binding["sha256"]:
            raise ValueError("Complete review binding changed")
        audit = read_json(source)
        expected = {r["method"] + ":" + r["case_id"] for r in rows}
        if (
            audit["review_scope"] != "all"
            or audit["unresolved"] != 0
            or audit["accepted_unreviewed_passes"] != 0
            or set(audit["results"]) != expected
            or audit["case_count"] != len(truth)
        ):
            raise ValueError("Full review is incomplete")
    if read_json(profile).get("independent_result_review_scope") == "all" and audit is None:
        raise ValueError("Full-review profile requires its completed audit")
    for row in rows:
        row["field_results"] = field_results(home, truth[row["case_id"]], row, profile=profile)
        row["failure_stage"] = failure_stage(row)
        row["native_error"] = native_error(row)
        row["method_label"] = names.get(row["method"], row["method"])
        key = row["method"] + ":" + row["case_id"]
        if key in diagnoses:
            if diagnoses[key]["current_call_sha256"] != row["source_logical_sha256"]:
                raise ValueError("Failure diagnosis belongs to a different current call")
            row["diagnosis"] = diagnoses[key]
        if audit:
            row["review"] = audit["results"][key]
            if row["review"]["native_response_sha256"] != digest(
                json.dumps(row.get("response"), sort_keys=True, ensure_ascii=False).encode()
            ):
                raise ValueError("Review belongs to a different native response")
    output.mkdir(parents=True, exist_ok=True)
    (output / "style.css").write_text(CSS)
    (output / "app.js").write_text(JS)
    for asset in model.get("assets", []):
        source = content.parent / asset["source"]
        target = output / asset["path"]
        if (
            Path(asset["path"]).is_absolute()
            or ".." in Path(asset["path"]).parts
            or source.is_symlink()
            or digest(source.read_bytes()) != asset["sha256"]
        ):
            raise ValueError("Unsafe or changed report asset")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    buckets = (
        {
            m: {r["case_id"]: r for r in rows if r["method"] == m}
            for m in cohort_plan["participants"]
        }
        if cohort
        else {f"{r['method']}-{r['case_id']}": r for r in rows}
    )
    public_receipts, public_rows = [], []
    for key, bucket in buckets.items():
        name = f"public-evidence/{key}.json"
        raw = json.dumps(bucket, ensure_ascii=False, indent=2, allow_nan=False).encode()
        public, receipt = project_evidence_view(
            name,
            raw,
            role="native_response",
            content_format="json",
            host_names=publication_host_names(),
        )
        projected = json.loads(public)
        before = list(bucket.values()) if cohort else [bucket]
        after = list(projected.values()) if cohort else [projected]
        for original, view in zip(before, after, strict=True):
            method = original["method"]
            if normalize(method, original.get("response", {})) != normalize(
                method, view.get("response", {})
            ):
                raise ValueError("Public evidence projection changed extracted fields")
        target = output / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(public)
        public_receipts.append(receipt)
        public_rows.extend(after)
    rows = public_rows
    for lang in languages:
        lab = LABELS[lang]
        directory = output / lang
        directory.mkdir()
        bodies = {p: model["pages"].get(p, {}).get("body", {}).get(lang, "") for p in PAGES}
        titles = {
            p: model["pages"]
            .get(p, {})
            .get("title", {})
            .get(lang, EXTRA_LABELS[p][lang] if p in EXTRA_LABELS else lab[i])
            for i, p in enumerate(PAGES)
        }
        # Scope and observed current interfaces lead; historical scores remain separate.
        methods = (
            list(cohort_plan["participants"]) if cohort else sorted({r["method"] for r in rows})
        )
        summary = []
        for method in methods:
            subset = [r for r in rows if r["method"] == method]
            called = sum(r["state"] in ("response_received", "call_error") for r in subset)
            structured = sum(r.get("compatibility") == "structured_output" for r in subset)
            summary.append(
                [
                    esc(method),
                    str(len(subset)),
                    str(called),
                    str(structured),
                    '<a href="results.html">' + lab[2] + "</a>",
                ]
            )
        bodies["index"] = (
            table(
                [
                    lab[15],
                    "计划样本" if lang == "zh" else "Planned cases",
                    "实际调用" if lang == "zh" else "API calls",
                    lab[12],
                    lab[9],
                ],
                summary,
                attrs='data-core-metrics="compatibility"',
            )
            + bodies["index"]
        )
        if not cohort:
            bodies["index"] += operating_metrics(
                methods, rows, model.get("prices", {}), lang, names
            )
            bodies["accounts"] += price_table(methods, model.get("prices", {}), lang, names)
        if cohort:
            core, timings = cohort_metrics(methods, rows, bindings, len(truth), lang, names)
            bodies["index"] = (
                core
                + operating_metrics(methods, rows, model.get("prices", {}), lang, names)
                + model["pages"].get("index", {}).get("body", {}).get(lang, "")
            )
            bodies["protocol"] += (
                "<h2>" + ("调用耗时" if lang == "zh" else "Call latency") + "</h2>" + timings
            )
            bodies["accounts"] += (
                "<h2>"
                + ("各方法价格" if lang == "zh" else "Prices by method")
                + "</h2>"
                + price_table(methods, model.get("prices", {}), lang, names)
            )
        if audit:
            review_body, interpretation_body = audit_explanation(
                audit, rows, methods, names, lang, core_html=core if cohort else None
            )
            bodies["review"] = review_body
            bodies["interpretation"] = interpretation_body
            bodies["index"] += (
                '<p><a href="interpretation.html">'
                + ("阅读报告解读" if lang == "zh" else "Read the interpretation")
                + '</a> · <a href="review.html">'
                + (
                    "查看完整审查和参考值修正"
                    if lang == "zh"
                    else "Inspect complete audit and reference changes"
                )
                + "</a></p>"
            )
        if cohort:
            bodies["protocol"] += (
                "<h2>"
                + ("接口用量" if lang == "zh" else "Reported usage")
                + "</h2>"
                + usage_table(methods, rows, lang, names)
            )
            bodies["protocol"] += (
                "<p>用量来自响应中的 creditsUsed 或原生计费响应头，包含保留的重试。不同服务的 credit 单价不同，不能直接横向比较。空白表示接口没有提供逐次用量；这不表示免费。账号余额与额度条件见账号页。</p>"
                if lang == "zh"
                else "<p>Usage comes from native response metadata or billing headers, including retained retries. Credit prices differ between services. An unavailable receipt does not mean zero cost; see Accounts for balances and access requirements.</p>"
            )
        hr = []
        fields = ("price", "sku", "availability")
        historical_fields = (*fields, "OutOfStock")
        for method, scores in history["scores"].items():
            cells = [esc(method)]
            for field in historical_fields:
                v = scores[field]["f1"]
                best = max(s[field]["f1"] for s in history["scores"].values())
                unique = sum(s[field]["f1"] == best for s in history["scores"].values()) == 1
                text = f"{v:.3f}"
                if unique and v == best:
                    text = '<span class="comparison-winner">' + text + "</span>"
                cells.append(f'<span data-history="{field}">{text}</span>')
            hr.append(cells)
        historical_names = [*lab[23:26], "缺货 F1" if lang == "zh" else "Out-of-stock F1"]
        headers = [
            lab[15],
            *[
                f"{name}<br><small>{sum(bool(gt.get(field)) for gt in truth.values())}/{len(truth)} annotated</small>"
                for name, field in zip(historical_names, historical_fields, strict=True)
            ],
        ]
        if lang == "zh":
            headers = [x.replace("annotated", "有标注") for x in headers]
        hist = table(headers, hr, attrs='data-history-table="true"')
        soup = BeautifulSoup(hist, "html.parser")
        for row in soup.select("tbody tr"):
            method = row.select_one("td").get_text()
            for field, cell in zip(historical_fields, row.select("td")[1:], strict=True):
                cell.attrs.update(
                    {
                        "data-metric": field,
                        "data-metric-direction": "max",
                        "data-sort": str(history["scores"][method][field]["f1"]),
                    }
                )
        historical_title = lab[22].replace("140", str(len(truth)))
        bodies["research" if cohort else "index"] += (
            "<section><h2>" + historical_title + "</h2>" + str(soup) + "</section>"
        )
        dr = []
        pin = read_json(profile)["dataset"]
        for cid, gt in sorted(truth.items()):
            url = f"https://github.com/{pin['repository']}/blob/{pin['commit']}/dataset/html/{cid}.html.gz"
            ref = {f: gt.get(f, []) for f in fields}
            dr.append(
                [
                    f'<a href="source-{cid}.html"><code>{cid}</code></a>',
                    f'<a href="{esc(gt["url"], quote=True)}">{esc(urlsplit(gt["url"]).netloc)}</a>',
                    f'<a href="{url}">HTML.gz</a>',
                    f"<details><summary>{lab[19]}</summary><pre>{esc(json.dumps(ref, ensure_ascii=False, indent=2))}</pre></details>",
                ]
            )
            raw_html = (home / "dataset/html" / (cid + ".html")).read_text()
            source_soup = BeautifulSoup(raw_html, "html.parser")
            structured = [
                str(e)
                for e in source_soup.select('script[type="application/ld+json"],meta[content]')
            ]
            outline = source_soup.get_text("\n", strip=True) + "\n\n" + "\n".join(structured)
            source_body = (
                f'<article data-source-case="{cid}"><p><code>{cid}</code></p>'
                f'<p><a href="{esc(gt["url"], quote=True)}">{esc(gt["url"])}</a> · '
                f'<a href="{url}">HTML.gz</a> · <a href="case-{cid}.html">'
                + ("同题全部方法" if lang == "zh" else "Compare all methods")
                + "</a></p>"
                + (
                    "<p>以下是原始标注。空数组表示该字段没有给出可接受值，并非整题未标注。库存沿用原评测规则：没有明确缺货证据时按有货处理。</p>"
                    if lang == "zh"
                    else "<p>Original annotations follow. An empty array contains no accepted value; it does not mean the whole case is unannotated. The original benchmark assumes InStock unless clearly out of stock.</p>"
                )
                + "<pre data-source-reference>"
                + esc(json.dumps(ref, ensure_ascii=False, indent=2))
                + "</pre>"
            )
            image = model.get("source_screenshots", {}).get(cid)
            if image:
                source = content.parent / image["source"]
                target = output / "source-images" / (cid + ".jpeg")
                if source.is_symlink() or digest(source.read_bytes()) != image["sha256"]:
                    raise ValueError("Source screenshot changed")
                if not target.exists():
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(source.read_bytes())
                source_body += (
                    "<details><summary>"
                    + (
                        "原作者截图（早于 HTML 采集）"
                        if lang == "zh"
                        else "Author screenshot (captured before HTML)"
                    )
                    + "</summary>"
                )
                source_body += f'<figure><img class="source-screenshot" loading="lazy" src="../source-images/{cid}.jpeg" alt="'
                source_body += (
                    "原数据集作者保存的商品页面截图"
                    if lang == "zh"
                    else "Product screenshot saved by the dataset author"
                ) + '"><figcaption>'
                source_body += (
                    "作者截图早于 HTML 快照采集，可能与下方 HTML 内容有差异；字段核查以冻结 HTML 为依据。"
                    if lang == "zh"
                    else "The author captured this screenshot before the HTML snapshot. It may differ; field checks use the frozen HTML."
                ) + "</figcaption></figure></details>"
            if audit:
                source_review = audit["sources"][cid]
                source_body += (
                    "<section data-source-audit><h2>"
                    + (
                        "原文证据与参考值核查"
                        if lang == "zh"
                        else "Source evidence and reference audit"
                    )
                    + "</h2>"
                )
                source_body += table(
                    ["字段", "核查后参考值", "原文证据", "原标注裁决"]
                    if lang == "zh"
                    else ["Field", "Audited values", "Source quote", "Reference adjudication"],
                    [
                        [
                            FIELD_LABELS[field][0 if lang == "zh" else 1],
                            esc(
                                json.dumps(
                                    audit["audited_reference"][cid].get(field, []),
                                    ensure_ascii=False,
                                )
                            ),
                            "<blockquote>"
                            + esc(source_review["source_fields"][field]["source_quote"])
                            + "</blockquote>",
                            esc(source_review["reference_review"][field]["reason"]),
                        ]
                        for field in fields
                    ],
                )
                source_body += "</section>"
            source_body += (
                "<h2>冻结 HTML 的文本与结构化数据</h2><p>包含隐藏 DOM 文本，不代表浏览器可见区域；脚本仅展示 JSON-LD，不执行原站代码。</p>"
                if lang == "zh"
                else "<h2>Frozen HTML text and structured data</h2><p>Includes hidden DOM text, not a rendered visibility claim. Only JSON-LD scripts are shown; source code is not executed.</p>"
            )
            source_body += (
                '<pre class="source-preview" data-source-outline>'
                + esc(outline)
                + "</pre></article>"
            )
            (directory / ("source-" + cid + ".html")).write_text(
                page(
                    lang,
                    "dataset",
                    ("页面与标注 · " if lang == "zh" else "Source and annotations · ") + cid[:12],
                    source_body,
                    detail=True,
                    languages=languages,
                    state=state,
                )
            )
            compared = []
            for method in methods:
                row = next((r for r in rows if r["case_id"] == cid and r["method"] == method), None)
                if row is None:
                    continue
                cells = [
                    f'<a href="result-{method}-{cid}.html">{esc(names.get(method, method))}</a>'
                ]
                for field in fields:
                    result = row["field_results"][field]
                    cells.append(
                        '<span class="field-'
                        + result["status"]
                        + '">'
                        + esc(json.dumps(result["actual"], ensure_ascii=False))
                        + "</span><br><small>"
                        + OUTCOMES[result["status"]][0 if lang == "zh" else 1]
                        + "</small>"
                    )
                cells.extend(
                    [
                        esc(STAGES[row["failure_stage"]][0 if lang == "zh" else 1]),
                        f"{row['elapsed_seconds']:.2f}"
                        if row.get("elapsed_seconds") is not None
                        else "—",
                    ]
                )
                compared.append(cells)
            case_body = (
                f'<article data-same-case="{cid}"><p><a href="source-{cid}.html">'
                + ("原页面与标注" if lang == "zh" else "Source and annotations")
                + "</a></p>"
            )
            case_body += "<pre>" + esc(json.dumps(ref, ensure_ascii=False, indent=2)) + "</pre>"
            case_body += (
                table(
                    [
                        lab[15],
                        "价格" if lang == "zh" else "Price",
                        "SKU",
                        "库存" if lang == "zh" else "Stock",
                        "环节" if lang == "zh" else "Stage",
                        "秒" if lang == "zh" else "Seconds",
                    ],
                    compared,
                    attrs='data-case-comparison="true" data-sortable',
                )
                + "</article>"
            )
            (directory / ("case-" + cid + ".html")).write_text(
                page(
                    lang,
                    "results",
                    ("同题方法比较 · " if lang == "zh" else "Same-case comparison · ") + cid[:12],
                    case_body,
                    detail=True,
                    languages=languages,
                    state=state,
                )
            )
        bodies["dataset"] += table(
            [lab[14], "来源" if lang == "zh" else "Source", "Snapshot", lab[19]], dr, inventory=True
        )
        inventory = []
        for r in rows:
            cid = r["case_id"]
            key = r["method"] + "-" + cid
            ok = r.get("compatibility") == "structured_output"
            status = lab[12] if ok else lab[11]
            url = "result-" + key + ".html"
            error = r.get("error") or ""
            payload = r.get("response") or {}
            if not isinstance(payload, dict):
                payload = {"message": json.dumps(payload, ensure_ascii=False)}
            if not error and not ok:
                value = (
                    payload.get("error")
                    or payload.get("message")
                    or payload.get("errors")
                    or payload.get("non_json_response")
                )
                if isinstance(value, dict):
                    error = str(value.get("message") or value.get("type") or value)
                elif value:
                    error = (
                        json.dumps(value, ensure_ascii=False)
                        if not isinstance(value, str)
                        else value
                    )
                elif r.get("missing_variables"):
                    error = ("缺少凭据：" if lang == "zh" else "Missing credentials: ") + ", ".join(
                        r["missing_variables"]
                    )
                else:
                    error = (
                        "未返回可识别的商品字段"
                        if lang == "zh"
                        else "No recognizable product fields returned"
                    )
            if ok:
                error = ""
            evidence = "../public-evidence/" + (r["method"] if cohort else key) + ".json"
            evidence_key = f' data-key="{cid}"' if cohort else ""
            detail = f'<article data-report-detail="{"passed" if ok else "failed"}"><div data-result-summary><h2>{status}</h2><p>{esc(names.get(r["method"], r["method"]))} · {esc(r["state"])} · HTTP {r.get("http_status", "—")}</p><p><code>{cid}</code></p><small>{esc(r["run_id"])}</small></div>'
            detail += (
                f'<p class="case-links"><a href="case-{cid}.html">'
                + ("同题全部方法" if lang == "zh" else "Compare all methods")
                + f'</a><a href="source-{cid}.html">'
                + ("页面、标注与来源" if lang == "zh" else "Source and annotations")
                + "</a></p>"
            )
            detail += field_table(r, lang)
            if r.get("review"):
                reviewed = {**r, "field_results": r["review"]["audited_fields"]}
                detail += (
                    "<section data-audited-result><h2>"
                    + (
                        "核查参考值后的字段诊断"
                        if lang == "zh"
                        else "Field diagnosis after reference audit"
                    )
                    + "</h2>"
                )
                detail += (
                    "<p>"
                    + (
                        "原始评分保留不变；这里使用已裁决的参考值作诊断。来源证据和标注改动见页面与标注。"
                        if lang == "zh"
                        else "Original scores remain unchanged. This diagnostic uses adjudicated references; see source and annotations for evidence and changes."
                    )
                    + "</p>"
                    + field_table(reviewed, lang).replace(
                        'data-field-diagnostics="true"', 'data-audited-fields="true"'
                    )
                    + "</section>"
                )
            if r.get("diagnosis"):
                diagnosis = r["diagnosis"]
                detail += (
                    "<section data-reviewed-diagnosis><h2>"
                    + ("原始失败的原因与处理" if lang == "zh" else "Original failure and treatment")
                    + "</h2><p>"
                    + esc(diagnosis["cause"])
                    + "</p><p>"
                    + esc(diagnosis["treatment"])
                    + "</p></section>"
                )
            if not ok:
                detail += f'<p data-failure-diagnosis>{"未返回可评分字段；程序错误见下方。" if lang == "zh" else "No scoreable fields returned; see the program error below."}</p><details id="result-logs" open><summary>{lab[21]}</summary><pre data-kind="error">{esc(error)}</pre></details>'
            ref = {f: truth[cid].get(f, []) for f in fields}
            detail += (
                table(
                    [lab[19], lab[20]],
                    [
                        [
                            f"<pre>{esc(json.dumps(ref, ensure_ascii=False, indent=2))}</pre>",
                            f"<pre>{esc(json.dumps(r.get('normalized_fields'), ensure_ascii=False, indent=2))}</pre>",
                        ]
                    ],
                )
                .replace("<pre>", "<details open><summary>JSON</summary><pre>")
                .replace("</pre>", "</pre></details>")
            )
            detail += f'<p><button data-call-log-open="evidence" data-src="{evidence}"{evidence_key}>{lab[9]}</button> · <a href="{evidence}">JSON</a></p><dialog id="evidence" aria-labelledby="evidence-title"><h2 id="evidence-title">{lab[21]}</h2><button data-call-log-close>{lab[10]}</button><pre></pre></dialog></article>'
            (directory / url).write_text(
                page(
                    lang,
                    "results",
                    names.get(r["method"], r["method"]) + " · " + cid[:10],
                    detail,
                    detail=True,
                    languages=languages,
                    state=state,
                )
            )
            elapsed = f"{r['elapsed_seconds']:.2f}" if r.get("elapsed_seconds") is not None else "—"
            field_cells = ""
            outcomes = {"correct": "符合", "missing": "漏提", "unexpected": "多提", "wrong": "错误"}
            for field in fields:
                outcome = r["field_results"][field]["status"]
                field_cells += (
                    f'<td><button class="field-button field-{outcome}" data-call-log-open="diagnosis" data-src="{evidence}"{evidence_key} data-field="{field}">'
                    + (outcomes[outcome] if lang == "zh" else outcome)
                    + "</button></td>"
                )
            data_fields = " ".join(
                f'data-{field.lower()}="{value["status"]}"'
                for field, value in r["field_results"].items()
            )
            correct = all(v["status"] == "correct" for v in r["field_results"].values())
            search = cid + " " + truth[cid]["url"] + " " + error
            inventory.append(
                f'<tr data-status="{"pass" if ok else "fail"}" data-method="{r["method"]}" data-stage="{r["failure_stage"]}" data-outcome="{"correct" if correct and ok else "wrong"}" data-search="{esc(search, quote=True)}" {data_fields}><td>{esc(names.get(r["method"], r["method"]))}</td><td><a href="{url}"><code>{cid[:12]}</code></a></td><td>{status}</td>{field_cells}<td>{r.get("http_status") or "—"}</td><td data-sort="{r.get("elapsed_seconds", "")}">{elapsed}</td><td><button data-call-log-open="diagnosis" data-src="{evidence}"{evidence_key}>{lab[9]}</button> · <a href="{url}">'
                + ("完整结果" if lang == "zh" else "Full result")
                + "</a></td></tr>"
            )
        if cohort:
            observed = {(r["method"], r["case_id"]) for r in rows}
            for method in methods:
                for cid in sorted(truth):
                    if (method, cid) not in observed:
                        pending = "待执行" if lang == "zh" else "Not executed"
                        inventory.append(
                            f'<tr data-status="pending" data-method="{method}" data-outcome="pending"><td>{esc(names.get(method, method))}</td><td><code>{cid[:12]}</code></td><td>{pending}</td>'
                            + "<td>—</td>" * 6
                            + "</tr>"
                        )
        bodies["results"] += (
            result_controls(methods, rows, names, lang)
            + '<div class="table-wrap inventory"><table data-sortable><thead><tr>'
            + "".join(
                "<th>" + x + "</th>"
                for x in [
                    lab[15],
                    lab[14],
                    lab[16],
                    "价格" if lang == "zh" else "Price",
                    "SKU",
                    "库存" if lang == "zh" else "Stock",
                    lab[17],
                    lab[18],
                    lab[9],
                ]
            )
            + "</tr></thead><tbody>"
            + "".join(inventory)
            + "</tbody></table></div>"
            + '<dialog id="diagnosis" aria-labelledby="diagnosis-title"><h2 id="diagnosis-title">'
            + ("字段与调用证据" if lang == "zh" else "Field and call evidence")
            + "</h2><button data-call-log-close>"
            + lab[10]
            + "</button><div data-diagnosis-body></div></dialog>"
        )
        for p in PAGES:
            (directory / (p + ".html")).write_text(
                page(lang, p, titles[p], bodies[p], languages=languages, state=state)
            )
    if "en" not in languages:
        (output / "en").mkdir()
        (output / "en/index.html").write_text(
            '<!doctype html><html lang="en"><head><base href="../"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>English edition not generated</title><link rel="stylesheet" href="style.css">'
            "</head><body><main data-language-notice><h1>English edition not generated</h1>"
            "<p>This report is available in Chinese. Ask to generate the English edition when needed.</p>"
            '<p><a href="zh/index.html">Return to the Chinese report / 返回中文报告</a></p>'
            '<button id="theme">Theme</button></main><script src="app.js"></script></body></html>'
        )
    (output / "index.html").write_text(
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={language}/index.html"><title>Web Scraper</title></head><body><a href="{language}/index.html">Web Scraper</a></body></html>'
    )
    files = {
        p.relative_to(output).as_posix(): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    public_bindings, metadata_receipt = project_evidence_view(
        "public-evidence/metadata.json",
        json.dumps(bindings, ensure_ascii=False).encode(),
        role="native_response",
        content_format="json",
    )
    save_json(
        output / "report-manifest.json",
        {
            "template": VERSION,
            "metric_contract": metric_contract(),
            "mode": state,
            "planned_methods": list(cohort_plan["participants"]) if cohort else None,
            "method_labels": names,
            "prices": model.get("prices", {}),
            "renderer_sha256": digest(Path(__file__).read_bytes()),
            "interaction_sha256": digest(Path(__file__).with_name("report_ui.py").read_bytes()),
            "analysis_sha256": digest(Path(__file__).with_name("analysis.py").read_bytes()),
            "review": {
                k: audit[k]
                for k in (
                    "review_scope",
                    "result_count",
                    "case_count",
                    "original_results_reviewed",
                    "supplemental_results_reviewed",
                    "accepted_unreviewed_passes",
                    "unresolved",
                )
            }
            if audit
            else None,
            "required_review_scope": read_json(profile).get("independent_result_review_scope"),
            "audited_scores": audit.get("audited_scores") if audit else None,
            "content_sha256": digest(content.read_bytes()),
            "profile_sha256": digest(profile.read_bytes()),
            "dataset_manifest_sha256": history["dataset_manifest_sha256"],
            "primary_language": language,
            "languages": list(languages),
            "case_count": len(truth),
            "observations": len(rows),
            "source_runs": json.loads(public_bindings),
            "source_metadata_view": metadata_receipt,
            "public_evidence_views": public_receipts,
            "files": files,
        },
        exclusive=True,
    )
    check(output)


def check(output: Path, *, release=False, visual_receipt=None):
    manifest = read_json(output / "report-manifest.json")
    if publication_risk((output / "report-manifest.json").read_bytes()):
        raise ValueError("Report metadata has a publication privacy risk")
    if manifest["template"] != VERSION:
        raise ValueError("Wrong template")
    if manifest.get("metric_contract") != metric_contract():
        raise ValueError("Report does not bind the shared required metric contract")
    if manifest["renderer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Renderer identity changed; rebuild required")
    if manifest.get("interaction_sha256") != digest(
        Path(__file__).with_name("report_ui.py").read_bytes()
    ):
        raise ValueError("Interaction identity changed; rebuild required")
    if manifest.get("analysis_sha256") != digest(
        Path(__file__).with_name("analysis.py").read_bytes()
    ):
        raise ValueError("Field analysis identity changed; rebuild required")
    review = manifest.get("review")
    if manifest.get("required_review_scope") == "all" and (
        not review
        or review["review_scope"] != "all"
        or review["unresolved"] != 0
        or review["accepted_unreviewed_passes"] != 0
        or review["result_count"] != manifest["observations"]
        or review["original_results_reviewed"] != manifest["observations"]
    ):
        raise ValueError("Required full review is incomplete")
    actual = {
        p.relative_to(output).as_posix()
        for p in output.rglob("*")
        if p.is_file() and p.name != "report-manifest.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("Report file inventory differs")
    views = manifest.get("public_evidence_views", [])
    if {v["path"] for v in views} != {n for n in actual if n.startswith("public-evidence/")}:
        raise ValueError("Public evidence view receipts are incomplete")
    for view in views:
        public = (output / view["path"]).read_bytes()
        if digest(public) != view["published_sha256"] or publication_risk(public):
            raise ValueError("Public evidence privacy receipt failed")
    for name, sha in manifest["files"].items():
        path = output / name
        if path.is_symlink() or digest(path.read_bytes()) != sha:
            raise ValueError("Report file changed: " + name)
        if path.suffix.lower() in {
            ".html",
            ".json",
            ".js",
            ".css",
            ".txt",
            ".md",
            ".csv",
        } and publication_risk(path.read_bytes()):
            raise ValueError("Report textual artifact has a publication privacy risk: " + name)
        if path.suffix != ".html":
            continue
        soup = check_presentation(path.read_text())
        if soup.head is None:
            raise ValueError("Missing explicit head for hosted preview")
        if name != "index.html" and not soup.select_one('base[href="../"]'):
            raise ValueError("Missing local site-root base")
        notice = bool(soup.select_one("[data-language-notice]"))
        if notice and (name != "en/index.html" or "en" in manifest["languages"]):
            raise ValueError("Unexpected language notice")
        if (
            name != "index.html"
            and not notice
            and not soup.select_one(
                '[data-evidence-state="' + manifest.get("mode", "compatibility") + '"]'
            )
        ):
            raise ValueError("Missing evidence state")
        if (
            name.endswith("/index.html")
            and not notice
            and not soup.select_one("[data-core-metrics]")
        ):
            raise ValueError("Missing core metrics")
        for element in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
            link = element.get("href") or element.get("src") or element.get("data-src")
            u = urlsplit(link)
            if u.scheme or u.netloc:
                continue
            if u.path.startswith("/") or ".." in Path(unquote(u.path)).parts:
                raise ValueError("Reference must be relative to the hosted site root: " + link)
            target = output / unquote(u.path) if u.path else path
            if not target.resolve().is_relative_to(output.resolve()) or not target.is_file():
                raise ValueError("Broken local target: " + link)
            if u.fragment and not BeautifulSoup(target.read_text(), "html.parser").find(
                id=u.fragment
            ):
                raise ValueError("Broken evidence anchor")
    if manifest.get("mode") != "full-cohort-progress":
        rows = [read_json(output / view["path"]) for view in views]
        methods = sorted({row["method"] for row in rows})
        names = manifest.get("method_labels", {})
        for lang in manifest["languages"]:
            home_view = BeautifulSoup((output / lang / "index.html").read_text(), "html.parser")
            expected = BeautifulSoup(
                operating_metrics(methods, rows, manifest.get("prices", {}), lang, names),
                "html.parser",
            ).select_one("[data-operating-metrics]")
            for anchor in expected.select('a[href="accounts.html"]'):
                anchor["href"] = lang + "/accounts.html"
            if home_view.select_one("[data-operating-metrics]") != expected:
                raise ValueError("Home latency/pricing differs from retained calls")
            account_view = BeautifulSoup(
                (output / lang / "accounts.html").read_text(), "html.parser"
            )
            expected_price = BeautifulSoup(
                price_table(methods, manifest.get("prices", {}), lang, names), "html.parser"
            ).select_one("[data-price-table]")
            if account_view.select_one("[data-price-table]") != expected_price:
                raise ValueError("Cost/pricing coverage differs from bound source")
    if manifest.get("mode") == "full-cohort-progress":
        methods = manifest["planned_methods"]
        names = manifest.get("method_labels", {})
        data = {m: read_json(output / "public-evidence" / (m + ".json")) for m in methods}
        scores = {
            m: b["scores"][m]
            for b in manifest["source_runs"]
            if b.get("certified")
            for m in b["scores"]
        }
        if sum(len(v) for v in data.values()) != manifest["observations"]:
            raise ValueError("Published observations differ from evidence")
        for lang in manifest["languages"]:
            soup = BeautifulSoup((output / lang / "index.html").read_text(), "html.parser")
            summary = soup.select('[data-core-metrics="full-cohort"] tbody tr')
            if [r.get("data-method") for r in summary] != methods:
                raise ValueError("Missing or reordered planned participant")
            for method, row in zip(methods, summary, strict=True):
                cells = row.select("td")
                expected = [
                    manifest["case_count"],
                    len(data[method]),
                    sum(
                        r.get("compatibility") == "structured_output" for r in data[method].values()
                    ),
                ]
                if [c.get_text(strip=True) for c in cells[1:4]] != [str(x) for x in expected]:
                    raise ValueError("Full cohort progress count differs")
                for field, cell in zip(
                    ("price", "sku", "availability", "OutOfStock"), cells[5:], strict=True
                ):
                    value = scores.get(method, {}).get(field, {}).get("f1")
                    if cell.get_text(strip=True) != ("—" if value is None else f"{value:.3f}"):
                        raise ValueError("Displayed quality differs from certified scores")
            protocol = BeautifulSoup((output / lang / "protocol.html").read_text(), "html.parser")
            accounts = BeautifulSoup((output / lang / "accounts.html").read_text(), "html.parser")
            expected_prices = BeautifulSoup(
                price_table(methods, manifest.get("prices", {}), lang, names), "html.parser"
            )
            price_views = accounts.select("[data-price-table]")
            if len(price_views) != 1 or price_views[0] != expected_prices.select_one(
                "[data-price-table]"
            ):
                raise ValueError("Cost/pricing coverage or terms differ from the bound source")
            rows = [r for method in methods for r in data[method].values()]
            expected_home = BeautifulSoup(
                operating_metrics(methods, rows, manifest.get("prices", {}), lang, names),
                "html.parser",
            ).select_one("[data-operating-metrics]")
            for anchor in expected_home.select('a[href="accounts.html"]'):
                anchor["href"] = lang + "/accounts.html"
            if soup.select_one("[data-operating-metrics]") != expected_home:
                raise ValueError("Home latency/pricing differs from retained calls")
            _, expected_time = cohort_metrics(
                methods, rows, manifest["source_runs"], manifest["case_count"], lang, names
            )
            for selector, expected_html in [
                ("[data-latency-table]", expected_time),
                ("[data-usage-table]", usage_table(methods, rows, lang, names)),
            ]:
                observed_tables = protocol.select(selector)
                expected_table = BeautifulSoup(expected_html, "html.parser").select_one(selector)
                if len(observed_tables) != 1 or [
                    c.get_text(strip=True) for c in observed_tables[0].select("td")
                ] != [c.get_text(strip=True) for c in expected_table.select("td")]:
                    raise ValueError("Timing or usage values differ from retained calls")
            inventory = BeautifulSoup((output / lang / "results.html").read_text(), "html.parser")
            if manifest.get("review"):
                reviewed = BeautifulSoup((output / lang / "review.html").read_text(), "html.parser")
                interpretations = BeautifulSoup(
                    (output / lang / "interpretation.html").read_text(), "html.parser"
                )
                quality_rows = reviewed.select("[data-audited-quality] tbody tr")
                compact_rows = interpretations.select("[data-interpretation-comparison] tbody tr")
                if [r.get("data-method") for r in compact_rows] != methods:
                    raise ValueError("Missing compact interpretation comparison")
                for method, row in zip(methods, compact_rows, strict=True):
                    if [c.get_text(strip=True) for c in row.select("td")[2:]] != [
                        f"{scores[method][field]['f1']:.3f}"
                        for field in ("price", "sku", "availability", "OutOfStock")
                    ]:
                        raise ValueError(
                            "Interpretation scores differ from original-reference scores"
                        )
                if not reviewed.select_one("[data-complete-review]") or len(quality_rows) != len(
                    methods
                ):
                    raise ValueError("Missing complete audit summary or diagnostic quality")
                for method, row in zip(methods, quality_rows, strict=True):
                    expected = [
                        f"{manifest['audited_scores'][method][field]['f1']:.3f}"
                        for field in ("price", "sku", "availability", "OutOfStock")
                    ]
                    if [cell.get_text(strip=True) for cell in row.select("td")[1:]] != expected:
                        raise ValueError("Audited F1 differs from source-qualified scores")
                if {
                    s["data-method-interpretation"]
                    for s in interpretations.select("[data-method-interpretation]")
                } != set(methods):
                    raise ValueError("Incomplete method interpretation coverage")
            if not inventory.select_one("[data-result-controls]") or not inventory.select_one(
                "[data-diagnosis-body]"
            ):
                raise ValueError("Missing result filters or diagnosis drilldown")
            for field in ("status", "method", "field", "stage", "query"):
                if not inventory.select_one(f'[data-result-filter="{field}"]'):
                    raise ValueError("Incomplete result filters")
            if len(inventory.select("tr[data-status]")) != len(methods) * manifest["case_count"]:
                raise ValueError("Full planned result inventory is incomplete")
            if (
                len(inventory.select('tr[data-status="pending"]'))
                != len(methods) * manifest["case_count"] - manifest["observations"]
            ):
                raise ValueError("Pending population differs")
            case_ids = {cid for values in data.values() for cid in values}
            for cid in case_ids:
                source = BeautifulSoup(
                    (output / lang / f"source-{cid}.html").read_text(), "html.parser"
                )
                comparison = BeautifulSoup(
                    (output / lang / f"case-{cid}.html").read_text(), "html.parser"
                )
                if not source.select_one("[data-source-reference]") or not source.select_one(
                    "[data-source-outline]"
                ):
                    raise ValueError("Missing inspectable source or annotation")
                expected_methods = [m for m in methods if cid in data[m]]
                if len(comparison.select("[data-case-comparison] tbody tr")) != len(
                    expected_methods
                ):
                    raise ValueError("Incomplete same-case method comparison")
                for method in expected_methods:
                    row = data[method][cid]
                    detail = BeautifulSoup(
                        (output / lang / f"result-{method}-{cid}.html").read_text(), "html.parser"
                    )
                    expected_fields = BeautifulSoup(
                        field_table(row, lang), "html.parser"
                    ).select_one("[data-field-diagnostics]")
                    if detail.select_one("[data-field-diagnostics]") != expected_fields:
                        raise ValueError("Field diagnostics differ from retained result")
                    if manifest.get("review"):
                        expected_audited = BeautifulSoup(
                            field_table(
                                {**row, "field_results": row["review"]["audited_fields"]}, lang
                            ),
                            "html.parser",
                        ).select_one("[data-field-diagnostics]")
                        expected_audited.attrs.pop("data-field-diagnostics")
                        expected_audited["data-audited-fields"] = "true"
                        if detail.select_one("[data-audited-fields]") != expected_audited:
                            raise ValueError("Audited result details differ")
    en = {p.relative_to(output / "en").as_posix() for p in (output / "en").glob("*.html")}
    zh = {p.relative_to(output / "zh").as_posix() for p in (output / "zh").glob("*.html")}
    if "en" in manifest["languages"] and en != zh:
        raise ValueError("Language route mismatch")
    if "en" not in manifest["languages"]:
        if en != {"index.html"} or not BeautifulSoup(
            (output / "en/index.html").read_text(), "html.parser"
        ).select_one("[data-language-notice] a[href='zh/index.html']"):
            raise ValueError("Missing English notice or stale English pages")
    if release:
        if visual_receipt is None:
            raise ValueError("Release requires actual visual coverage")
        visual = read_json(visual_receipt)
        if visual["report_manifest_sha256"] != digest(
            (output / "report-manifest.json").read_bytes()
        ):
            raise ValueError("Visual receipt belongs to a different report")
        required = {(lang, route, "desktop") for lang in manifest["languages"] for route in PAGES}
        if "en" not in manifest["languages"]:
            required.add(("en", "english-notice", "desktop"))
        observed = set()
        for item in visual["observations"]:
            width = item["viewport_width"]
            if (item["viewport"] == "narrow" and not 320 <= width <= 480) or (
                item["viewport"] == "desktop" and width < 1000
            ):
                raise ValueError("Visual viewport dimensions do not match the declared class")
            screenshot = Path(item["screenshot"])
            if (
                screenshot.is_symlink()
                or digest(screenshot.read_bytes()) != item["screenshot_sha256"]
            ):
                raise ValueError("Visual evidence changed")
            raw = screenshot.read_bytes()
            if image_width(raw) != width:
                raise ValueError("Screenshot width does not match the observed viewport")
            if not item["heading"] or item["document_width"] > item["viewport_width"] + 1:
                raise ValueError("Missing heading or overflowing report viewport")
            observed.add((item["language"], item["route_class"], item["viewport"]))
        if not required.issubset(observed):
            raise ValueError("Incomplete top-level reader coverage")
    return {
        "pages_per_language": len(zh),
        "observations": manifest["observations"],
        "state": manifest.get("mode", "compatibility"),
        "files": len(actual),
        "release": release,
    }


def package(output, archive, *, visual_receipt):
    """Package only the release-checked report inventory, with no private siblings."""
    from harness.reporting.publication import package_archive

    require_external_output(archive)
    manifest = read_json(output / "report-manifest.json")
    names = [*sorted(manifest["files"]), "report-manifest.json"]
    return package_archive(
        output,
        archive,
        archive.with_suffix(".receipt.json"),
        inputs=(visual_receipt,),
        validate=lambda: check(output, release=True, visual_receipt=visual_receipt),
        attest=lambda path: {
            "files": len(names),
            "archive_bytes": path.stat().st_size,
            "expanded_bytes": sum((output / name).stat().st_size for name in names),
            "archive_sha256": digest(path.read_bytes()),
            "report_manifest_sha256": digest((output / "report-manifest.json").read_bytes()),
        },
    )
