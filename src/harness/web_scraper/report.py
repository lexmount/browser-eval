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

from harness.reporting.presentation import check_presentation
from harness.reporting.templates import metric_contract
from harness.web_fetch.publication_privacy import project_evidence_view, publication_risk
from harness.web_scraper.pipeline import (
    PROFILE,
    digest,
    load_run,
    normalize,
    read_json,
    save_json,
    score_run,
    validate_dataset,
)

VERSION = "web-scraper-research/1"


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
PAGES = ("index", "dataset", "results", "protocol", "candidates", "research", "accounts")
CSS = """*{box-sizing:border-box}html{color-scheme:light;--bg:#f8f9fb;--panel:white;--ink:#17283b;--muted:#516277;--line:#d7dfe8;--accent:#075f79;--soft:#e1f3ed}html.dark{color-scheme:dark;--bg:#101923;--panel:#172431;--ink:#e8f0f7;--muted:#afbfce;--line:#354b60;--accent:#7cdbed;--soft:#174638}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 system-ui,-apple-system,"PingFang SC",sans-serif}header,main,footer{max-width:1190px;margin:auto;padding:24px 32px}header{padding-bottom:0}.eyebrow{font-size:13px;letter-spacing:.06em;color:var(--muted)}h1{font-size:clamp(28px,4vw,44px);line-height:1.25;letter-spacing:-.025em;margin:18px 0}h2{font-size:25px;margin:34px 0 12px}h3{font-size:19px;margin:26px 0 10px}p{max-width:900px}a{color:var(--accent);text-underline-offset:3px}nav{display:flex;flex-wrap:wrap;gap:7px;margin:20px 0}nav a,button,select{font:inherit;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);padding:7px 12px;cursor:pointer;text-decoration:none}nav a[aria-current]{background:var(--ink);color:var(--bg)}.notice{border-left:4px solid var(--accent);padding:10px 16px;background:var(--panel);font-size:14px}.table-wrap{overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.inventory{max-height:540px}table{border-collapse:collapse;min-width:650px;width:100%;font-size:14px}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--panel);position:sticky;top:0;z-index:1;color:var(--muted);white-space:nowrap}td{max-width:440px}td code{overflow-wrap:anywhere}.comparison-winner{background:var(--soft);font-weight:700;padding:3px 7px;border-radius:5px}section{margin:24px 0 40px}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;list-style:none;padding:0;counter-reset:stage}.flow li{counter-increment:stage;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}.flow li:before{content:counter(stage,decimal-leading-zero);display:block;color:var(--accent);font-size:24px;font-weight:700}code,pre{font:12px/1.6 ui-monospace,monospace}pre{overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:8px;max-height:540px;white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}dialog{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;width:min(950px,94vw);max-height:90vh;overflow:auto}dialog::backdrop{background:#0008}dialog pre{max-height:62vh}details{margin:12px 0}summary{cursor:pointer;color:var(--accent)}footer{font-size:13px;color:var(--muted);border-top:1px solid var(--line)}.controls{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}.status{font-weight:600}small{color:var(--muted)}@media(max-width:650px){header,main,footer{padding:20px 16px}.flow{grid-template-columns:1fr 1fr}nav{gap:5px}nav a{font-size:13px;padding:6px 9px}td,th{padding:10px}}"""
CSS += ".diagram{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}.diagram img{display:block;width:100%;min-width:850px;height:auto}"
JS = """document.querySelector('#theme').onclick=()=>{document.documentElement.classList.toggle('dark')};for(const b of document.querySelectorAll('[data-call-log-open]'))b.onclick=async()=>{const d=document.getElementById(b.dataset.callLogOpen);d.showModal();const p=d.querySelector('pre');try{const r=await fetch(b.dataset.src);if(!r.ok)throw Error('HTTP '+r.status);const value=await r.json();p.textContent=JSON.stringify(b.dataset.key?value[b.dataset.key]:value,null,2)}catch(e){p.textContent=String(e)}};for(const b of document.querySelectorAll('[data-call-log-close]'))b.onclick=()=>b.closest('dialog').close();const f=document.querySelector('#filter');if(f){const update=()=>{for(const r of document.querySelectorAll('tr[data-status]'))r.hidden=f.value!=='all'&&r.dataset.status!==f.value};f.onchange=update;update()}"""


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
            "全量评测进度 · 未完成的方法不计质量分"
            if language == "zh"
            else "Full-cohort progress · quality unavailable for incomplete methods"
        )
        labels[2] = "结果与失败" if language == "zh" else "Results and failures"
    other = "zh" if language == "en" else "en"
    nav = "".join(
        f'<a href="{p}.html"'
        + (' aria-current="page"' if route == p else "")
        + ">"
        + labels[i]
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
        ("execution/stagehand.py", contract.get("stagehand_sha256")),
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
        return (
            contract,
            rows,
            {
                "certified": True,
                "scores": saved["scores"],
                "certificate_sha256": digest(certificate.read_bytes()),
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
            latency.append(
                [
                    esc(labels.get(method, method)),
                    label,
                    str(len(values)),
                    str(len(selected) - len(values)),
                    f"{sum(r.get('retry_execution_seconds', 0) for r in selected):.2f}",
                    f"{statistics.mean(values):.2f}" if values else "—",
                    f"{statistics.median(values):.2f}" if values else "—",
                    f"{values[max(0, math.ceil(len(values) * 0.95) - 1)]:.2f}" if values else "—",
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


def build(output: Path, content: Path, *, home: Path, run=None, language="zh", profile=PROFILE):
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
        for record in records:
            r = json.loads(json.dumps(record))
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
            host_names=("go-connect.connect.svc.cluster.local", "10.255.255.1"),
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
            p: model["pages"].get(p, {}).get("title", {}).get(lang, lab[i])
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
        if cohort:
            core, timings = cohort_metrics(methods, rows, bindings, len(truth), lang, names)
            bodies["index"] = core + model["pages"].get("index", {}).get("body", {}).get(lang, "")
            bodies["protocol"] += (
                "<h2>" + ("调用耗时" if lang == "zh" else "Call latency") + "</h2>" + timings
            )
            bodies["accounts"] += (
                "<h2>"
                + ("各方法价格" if lang == "zh" else "Prices by method")
                + "</h2>"
                + price_table(methods, model.get("prices", {}), lang, names)
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
                    f"<code>{cid}</code>",
                    f'<a href="{esc(gt["url"], quote=True)}">{esc(urlsplit(gt["url"]).netloc)}</a>',
                    f'<a href="{url}">HTML.gz</a>',
                    f"<details><summary>{lab[19]}</summary><pre>{esc(json.dumps(ref, ensure_ascii=False, indent=2))}</pre></details>",
                ]
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
            inventory.append(
                f'<tr data-status="{"pass" if ok else "fail"}"><td>{esc(names.get(r["method"], r["method"]))}</td><td><a href="{url}"><code>{cid[:12]}</code></a></td><td>{status}</td><td>{r.get("http_status") or "—"}</td><td>{elapsed}</td><td><a href="{url}">{lab[9]}</a></td></tr>'
            )
        if cohort:
            observed = {(r["method"], r["case_id"]) for r in rows}
            for method in methods:
                for cid in sorted(truth):
                    if (method, cid) not in observed:
                        pending = "待执行" if lang == "zh" else "Not executed"
                        inventory.append(
                            f'<tr data-status="pending"><td>{esc(names.get(method, method))}</td><td><code>{cid[:12]}</code></td><td>{pending}</td><td>—</td><td>—</td><td>—</td></tr>'
                        )
        initial = (
            "fail" if any(r.get("compatibility") != "structured_output" for r in rows) else "all"
        )
        filt = f'<label>{lab[16]} <select id="filter"><option value="fail" {"selected" if initial == "fail" else ""}>{lab[11]}</option><option value="pass">{lab[12]}</option><option value="all" {"selected" if initial == "all" else ""}>{lab[13]}</option></select></label>'
        if cohort:
            filt = filt.replace(
                "</select>",
                '<option value="pending">'
                + ("待执行" if lang == "zh" else "Not executed")
                + "</option></select>",
            )
        bodies["results"] += (
            '<div class="controls">'
            + filt
            + '</div><div class="table-wrap inventory"><table><thead><tr>'
            + "".join(
                "<th>" + x + "</th>" for x in [lab[15], lab[14], lab[16], lab[17], lab[18], lab[9]]
            )
            + "</tr></thead><tbody>"
            + "".join(inventory)
            + "</tbody></table></div>"
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
            if len(inventory.select("tr[data-status]")) != len(methods) * manifest["case_count"]:
                raise ValueError("Full planned result inventory is incomplete")
            if (
                len(inventory.select('tr[data-status="pending"]'))
                != len(methods) * manifest["case_count"] - manifest["observations"]
            ):
                raise ValueError("Pending population differs")
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
        required = {
            (lang, route, viewport, theme)
            for lang in manifest["languages"]
            for route in (*PAGES, "success", "failure")
            for viewport in ("desktop", "narrow")
            for theme in ("light", "dark")
        }
        if "en" not in manifest["languages"]:
            required |= {
                ("en", "english-notice", viewport, theme)
                for viewport in ("desktop", "narrow")
                for theme in ("light", "dark")
            }
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
            observed.add((item["language"], item["route_class"], item["viewport"], item["theme"]))
        if observed != required:
            raise ValueError("Incomplete language/theme/viewport/route coverage")
    return {
        "pages_per_language": len(zh),
        "observations": manifest["observations"],
        "state": manifest.get("mode", "compatibility"),
        "files": len(actual),
        "release": release,
    }
