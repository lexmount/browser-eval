from __future__ import annotations

import html
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .accounts import ACCOUNT_REGISTRY
from .metrics import METRIC_DEFINITIONS, summarize


STYLE = r"""
:root{--bg:#071015;--panel:#0d1a21;--panel2:#122630;--ink:#edf6f7;--muted:#9aadb4;--line:#263c46;--cyan:#49d6ed;--violet:#8b7cff;--lime:#c6ed3d;--coral:#ff8168;--good:#67df9f;--bad:#ff6b7a;--warn:#ffd166;--max:1180px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 80% -10%,#18284b 0,transparent 30%),var(--bg);color:var(--ink);font:15px/1.65 Inter,system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif}a{color:var(--cyan);text-decoration:none}a:hover{text-decoration:underline}.wrap{width:min(var(--max),calc(100% - 34px));margin:auto}.top{border-bottom:1px solid var(--line);background:#071015e8;position:sticky;top:0;z-index:5;backdrop-filter:blur(14px)}nav{display:flex;align-items:center;gap:20px;overflow:auto;padding:12px 0;white-space:nowrap}.brand{font-weight:850;color:#fff;margin-right:auto}.hero{padding:54px 0 34px}.eyebrow{font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan)}h1{font-size:clamp(34px,6vw,66px);line-height:1.03;letter-spacing:-.045em;margin:16px 0}h2{font-size:clamp(26px,4vw,40px);letter-spacing:-.03em;margin:0 0 14px}h3{font-size:20px;margin:28px 0 10px}.lead,.sub{color:var(--muted);max-width:900px}.meta{display:flex;gap:8px;flex-wrap:wrap}.pill,.tag{border:1px solid var(--line);background:#0b171d;border-radius:999px;padding:5px 10px;color:#bdd0d5;font-size:12px}.tag{padding:2px 7px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{background:linear-gradient(145deg,#10212a,#0b171d);border:1px solid var(--line);border-radius:15px;padding:20px}.kpi{grid-column:span 3;border-top:3px solid var(--cyan)}.kpi:nth-child(2){border-top-color:var(--violet)}.kpi:nth-child(3){border-top-color:var(--lime)}.kpi:nth-child(4){border-top-color:var(--coral)}.n{font-size:34px;font-weight:850;letter-spacing:-.04em;margin:8px 0}.section{padding:44px 0;border-top:1px solid var(--line)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{border-collapse:collapse;width:100%;min-width:760px;background:#0b171d}th,td{padding:12px 13px;border-bottom:1px solid #1d323b;text-align:left;vertical-align:top}th{background:#12242c;font-size:12px;letter-spacing:.04em}td{color:#bfd0d4}.good{color:var(--good);font-weight:750}.bad{color:var(--bad);font-weight:750}.warn{color:var(--warn);font-weight:750}.bar{height:9px;background:#162a33;border-radius:99px;overflow:hidden;min-width:120px}.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet));border-radius:99px}.callout{border-left:4px solid var(--warn);background:#251f13;padding:14px 17px;border-radius:0 12px 12px 0}.failure{border-left-color:var(--bad);background:#2a1419}.success{border-left-color:var(--good);background:#10271d}pre,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}code{color:#b9f4ff;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#050d11;border:1px solid var(--line);padding:18px;border-radius:13px;color:#d7e8eb;max-height:70vh;overflow:auto}.attempt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.kv{background:#0b171d;border:1px solid var(--line);border-radius:12px;padding:13px}.kv b{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}.footer{padding:40px 0 70px;color:#7e969e}.empty{color:var(--muted);font-style:italic}.tiny{font-size:12px;color:#8299a1}
.visual-summary{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:16px;background:#07162c}@media(max-width:800px){.kpi,.grid>[style*="grid-column:span"]{grid-column:span 6!important}}@media(max-width:520px){.kpi,.grid>[style*="grid-column:span"]{grid-column:span 12!important}h1{overflow-wrap:anywhere}.wrap{width:min(var(--max),calc(100% - 24px))}}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "item"


def pct(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def ms(value: float | int | None) -> str:
    return "—" if value is None else f"{float(value):,.0f} ms"


def status(record: dict[str, Any]) -> tuple[str, str]:
    if not record["ok"]:
        return "Transport failure", "bad"
    if not record["metrics"]["content_pass"]:
        return "Content failure", "warn"
    return "Pass", "good"


def layout(*, title: str, body: str, base: str, subtitle: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><link rel="stylesheet" href="{base}assets/style.css"></head><body>
<header class="top"><nav class="wrap"><a class="brand" href="{base}index.html">Browser Harness Eval</a><a href="{base}datasets/index.html">评测集</a><a href="{base}providers/index.html">提供方 / 方法</a><a href="{base}metrics/index.html">指标</a><a href="{base}market/index.html">市场边界</a><a href="{base}adoption/index.html">开源采用</a><a href="{base}integration/index.html">集成决策</a><a href="{base}accounts/index.html">账号准备</a><a href="{base}results.jsonl">JSONL</a></nav></header>
<main>{body}</main><footer class="footer"><div class="wrap">Browser Harness Evaluation · Web Fetch Tool<br><span class="tiny">Generated from persisted artifacts. No credentials are embedded in this report.</span></div></footer></body></html>"""


def write_page(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_report(run_dir: Path, dataset: dict[str, Any], records: list[dict[str, Any]]) -> Path:
    summary = summarize(records)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_dir = run_dir / "report"
    if report_dir.exists():
        shutil.rmtree(report_dir)
    (report_dir / "assets").mkdir(parents=True)
    (report_dir / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    visual = Path(__file__).resolve().parent.parent / "report-assets" / "webfetch-capability-map-v2.png"
    if visual.exists():
        shutil.copy2(visual, report_dir / "assets" / visual.name)
    shutil.copy2(run_dir / "results.jsonl", report_dir / "results.jsonl")
    shutil.copy2(run_dir / "summary.json", report_dir / "summary.json")
    shutil.copy2(run_dir / "manifest.json", report_dir / "manifest.json")
    artifacts_target = report_dir / "artifacts"
    if artifacts_target.exists():
        shutil.rmtree(artifacts_target)
    shutil.copytree(run_dir / "artifacts", artifacts_target)

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_provider[record["provider"]].append(record)
        by_case[record["case_id"]].append(record)

    _write_index(report_dir, manifest, summary, records)
    _write_dataset_pages(report_dir, dataset, by_case)
    _write_provider_pages(report_dir, summary, by_provider)
    _write_metric_page(report_dir)
    _write_market_page(report_dir)
    _write_adoption_page(report_dir)
    _write_integration_page(report_dir)
    _write_account_page(report_dir)
    _write_attempt_pages(report_dir, records)
    return report_dir / "index.html"


def _write_index(report_dir: Path, manifest: dict[str, Any], summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    providers = summary["providers"]
    passing = sum(r["ok"] and r["metrics"]["content_pass"] for r in records)
    transport = sum(r["ok"] for r in records)
    kpis = f"""<div class="grid">
<div class="card kpi"><div class="eyebrow">Attempts</div><div class="n">{len(records)}</div><div class="sub">完整保存的调用次数</div></div>
<div class="card kpi"><div class="eyebrow">Providers</div><div class="n">{len(providers)}</div><div class="sub">本次实际运行的方法</div></div>
<div class="card kpi"><div class="eyebrow">Transport</div><div class="n">{pct(transport/len(records))}</div><div class="sub">请求层成功率</div></div>
<div class="card kpi"><div class="eyebrow">Content pass</div><div class="n">{pct(passing/len(records))}</div><div class="sub">正文锚点通过率</div></div></div>"""
    rows = []
    for name, item in providers.items():
        failures = sum(item["failure_types"].values())
        rows.append(f"""<tr><td><a href="providers/{slug(name)}.html"><b>{esc(name)}</b></a><br><span class="tiny">{esc(item['provider_type'])}</span></td><td>{item['attempts']}</td><td class="{'good' if item['transport_success_rate']==1 else 'warn'}">{pct(item['transport_success_rate'])}</td><td class="{'good' if item['content_pass_rate']==1 else 'warn'}">{pct(item['content_pass_rate'])}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td><td>{failures}</td><td>{item['deadline_overrun_count']}</td></tr>""")
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Formal evaluation report · web_fetch</div><h1>{esc(manifest['dataset']['name'])}</h1><p class="lead">对 URL → 内容方法进行可复现比较。请求结果、失败响应、指标与环境信息均由本次运行 artifact 生成。</p><div class="meta"><span class="pill">{esc(manifest['started_at'])}</span><span class="pill">{manifest['dataset']['case_count']} cases</span><span class="pill">{manifest['parameters']['runs']} runs</span><span class="pill">Python {esc(manifest['environment']['python'])}</span></div></div></section>
<section class="section"><div class="wrap">{kpis}</div></section>
<section class="section"><div class="wrap"><h2>一图读懂能力边界与本轮结果</h2><p class="sub">同一总分只比较相同输入输出契约；搜索、模型绑定和原始 HTTP/云浏览器能力分别建评测集。</p><img class="visual-summary" src="assets/webfetch-capability-map-v2.png" alt="Web retrieval capability taxonomy and benchmark summary"></div></section>
<section class="section"><div class="wrap"><h2>提供方比较</h2><p class="sub">Transport success 与 content pass 分开统计，避免 HTTP 200 掩盖空正文或错误页面。</p><div class="table-wrap"><table><thead><tr><th>方法</th><th>Attempts</th><th>Transport</th><th>Content</th><th>P50</th><th>P95</th><th>Failures</th><th>Deadline overruns</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>报告导航</h2><div class="grid"><div class="card" style="grid-column:span 3"><h3>评测集</h3><p class="sub">逐案例查看能力、锚点和结果。</p><a href="datasets/index.html">打开评测集 →</a></div><div class="card" style="grid-column:span 3"><h3>提供方 / 方法</h3><p class="sub">逐方法查看性能与失败。</p><a href="providers/index.html">打开方法页 →</a></div><div class="card" style="grid-column:span 3"><h3>指标定义</h3><p class="sub">查看严格指标契约。</p><a href="metrics/index.html">打开指标页 →</a></div><div class="card" style="grid-column:span 3"><h3>账号准备</h3><p class="sub">注册地址、取 key 位置与环境变量。</p><a href="accounts/index.html">打开账号清单 →</a></div></div></div></section>"""
    write_page(report_dir / "index.html", layout(title="Web Fetch Evaluation", body=body, base=""))


def _write_dataset_pages(report_dir: Path, dataset: dict[str, Any], by_case: dict[str, list[dict[str, Any]]]) -> None:
    rows = []
    for case in dataset["cases"]:
        items = by_case.get(case["id"], [])
        passes = sum(item["ok"] and item["metrics"]["content_pass"] for item in items)
        rows.append(f"<tr><td><a href=\"{slug(case['id'])}.html\"><b>{esc(case['name'])}</b></a><br><code>{esc(case['id'])}</code></td><td>{esc(case['category'])}</td><td>{esc(case['url'])}</td><td>{passes}/{len(items)}</td></tr>")
        attempt_rows = []
        for item in items:
            label, cls = status(item)
            attempt_rows.append(f"<tr><td><a href=\"../attempts/{slug(item['attempt_id'])}.html\">{esc(item['provider'])} · run {item['run']}</a></td><td class=\"{cls}\">{label}</td><td>{ms(item['elapsed_ms'])}</td><td>{item['metrics']['marker_hits']}/{item['metrics']['marker_total']}</td><td>{esc(item['error_type'] or '')}</td></tr>")
        markers = "".join(f"<span class=\"tag\">{esc(marker)}</span>" for marker in case["expected_any"])
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Dataset case · {esc(case['category'])}</div><h1>{esc(case['name'])}</h1><p class="lead">{esc(case.get('description',''))}</p><div class="meta"><a class="pill" href="{esc(case['url'])}">{esc(case['url'])}</a></div></div></section><section class="section"><div class="wrap"><h2>验收条件</h2><p>{markers}</p><p class="sub">Content pass 要求至少命中一个预登记锚点；marker recall 展示覆盖比例。</p></div></section><section class="section"><div class="wrap"><h2>所有方法与运行</h2><div class="table-wrap"><table><thead><tr><th>Attempt</th><th>状态</th><th>耗时</th><th>Markers</th><th>失败分类</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table></div></div></section>"""
        write_page(report_dir / "datasets" / f"{slug(case['id'])}.html", layout(title=case["name"], body=body, base="../"))
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Evaluation dataset</div><h1>{esc(dataset['name'])}</h1><p class="lead">{esc(dataset.get('description',''))}</p><div class="meta"><span class="pill">{len(dataset['cases'])} cases</span><span class="pill">Tool: web_fetch</span></div></div></section><section class="section"><div class="wrap"><h2>案例清单</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>类别</th><th>URL</th><th>通过 attempts</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "datasets" / "index.html", layout(title="评测集", body=body, base="../"))


def _write_provider_pages(report_dir: Path, summary: dict[str, Any], by_provider: dict[str, list[dict[str, Any]]]) -> None:
    index_rows = []
    for name, item in summary["providers"].items():
        attempts = by_provider[name]
        credit_values = [record["provider_metadata"].get("credits_used") for record in attempts if record["provider_metadata"].get("credits_used") is not None]
        credits = sum(float(value) for value in credit_values) if credit_values else None
        credit_label = "—" if credits is None else f"{credits:g} credits"
        index_rows.append(f"<tr><td><a href=\"{slug(name)}.html\"><b>{esc(name)}</b></a></td><td>{esc(item['provider_type'])}</td><td>{pct(item['transport_success_rate'])}</td><td>{pct(item['content_pass_rate'])}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td><td>{credit_label}</td></tr>")
        attempt_rows = []
        for record in attempts:
            label, cls = status(record)
            attempt_rows.append(f"<tr><td><a href=\"../attempts/{slug(record['attempt_id'])}.html\">{esc(record['case_name'])} · run {record['run']}</a></td><td class=\"{cls}\">{label}</td><td>{ms(record['elapsed_ms'])}</td><td>{record['metrics']['content_chars']:,}</td><td>{esc(record['error_type'] or '')}</td></tr>")
        failures = "".join(f"<li><code>{esc(kind)}</code>: {count}</li>" for kind, count in item["failure_types"].items()) or "<li>None</li>"
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Provider method · {esc(item['provider_type'])}</div><h1>{esc(name)}</h1><p class="lead">本页只呈现该方法在当前运行中的可观察结果；所有正文与失败响应均可下钻到 attempt 页面。</p></div></section><section class="section"><div class="wrap"><div class="grid"><div class="card kpi"><div class="eyebrow">Transport</div><div class="n">{pct(item['transport_success_rate'])}</div></div><div class="card kpi"><div class="eyebrow">Content</div><div class="n">{pct(item['content_pass_rate'])}</div></div><div class="card kpi"><div class="eyebrow">P50</div><div class="n">{ms(item['p50_ms'])}</div></div><div class="card kpi"><div class="eyebrow">P95</div><div class="n">{ms(item['p95_ms'])}</div></div></div><h3>失败分布</h3><ul>{failures}</ul><p>Deadline overruns: <b>{item['deadline_overrun_count']}</b> · Observed supplier usage: <b>{credit_label}</b></p></div></section><section class="section"><div class="wrap"><h2>逐次结果</h2><div class="table-wrap"><table><thead><tr><th>Case / run</th><th>状态</th><th>耗时</th><th>字符</th><th>失败分类</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table></div></div></section>"""
        write_page(report_dir / "providers" / f"{slug(name)}.html", layout(title=f"Provider: {name}", body=body, base="../"))
    index_body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Provider registry</div><h1>Web Fetch 提供方 / 方法</h1><p class="lead">Provider 是调用实现，和评测集、指标定义相互独立。同一数据集可重复运行不同 provider。</p></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>方法</th><th>类型</th><th>Transport</th><th>Content</th><th>P50</th><th>P95</th><th>Observed usage</th></tr></thead><tbody>{''.join(index_rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "providers" / "index.html", layout(title="提供方", body=index_body, base="../"))


def _write_metric_page(report_dir: Path) -> None:
    cards = "".join(f"<div class=\"card\" style=\"grid-column:span 6\"><h3><code>{esc(name)}</code></h3><p class=\"sub\">{esc(description)}</p></div>" for name, description in METRIC_DEFINITIONS.items())
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Metric contract</div><h1>评测指标定义</h1><p class="lead">指标在运行前固定定义。报告不会用 HTTP 成功替代正文成功，也不会从失败样本中删除慢请求。</p></div></section><section class="section"><div class="wrap"><div class="grid">{cards}</div><h3>聚合规则</h3><div class="callout">P50/P95 仅在 transport success 样本上计算；all-attempts P95 同时保留，用于暴露超时和慢失败。Content pass 的分母包含所有 attempts。</div></div></section>"""
    write_page(report_dir / "metrics" / "index.html", layout(title="指标定义", body=body, base="../"))


def _write_market_page(report_dir: Path) -> None:
    direct = [
        ("CowAgent built-in WebFetch", "Repository-local HTTP + readable-text/document parsing", "Local baseline", "WebFetch.execute({url})", "https://github.com/zhayujie/CowAgent/blob/main/agent/tools/web_fetch/web_fetch.py"),
        ("Lexmount WebFetch", "Managed URL → Markdown/metadata", "Direct", "POST /v1/extract", "https://browser.lexmount.com/docs/webfetch"),
        ("Firecrawl Scrape", "Managed + open-source URL extraction", "Direct", "POST /v2/scrape", "https://docs.firecrawl.dev/features/scrape"),
        ("Tavily Extract", "Known URL → Markdown/plain text", "Direct", "POST /extract", "https://docs.tavily.com/documentation/api-reference/endpoint/extract"),
        ("Exa Contents", "Known URL → Markdown/highlights/summary", "Direct", "POST /contents; cache or forced live crawl", "https://exa.ai/docs/reference/contents-retrieval"),
        ("Cloudflare Browser Rendering", "Browser-rendered URL → Markdown", "Direct", "POST /browser-rendering/markdown", "https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/"),
        ("Browserbase Fetch / Extract", "Known URL → raw or clean Markdown depending current surface", "Candidate: verify current REST schema", "POST /v1/fetch", "https://www.browserbase.com/changelog"),
        ("Bright Data Web Unlocker", "Unlocked fetch with Markdown option", "Separate protected-site dataset", "Managed scrape/unlock request", "https://docs.brightdata.com/scraping-automation/web-unlocker"),
        ("Zyte / ScrapingBee / ZenRows / Scrapfly / Oxylabs", "Managed scraping APIs", "Separate protected-site dataset", "HTTP/browser/proxy extraction", "https://docs.zyte.com/zyte-api/"),
        ("Crawl4AI / ScrapeGraphAI / Browserless", "Self-hostable extraction stack", "Direct, but operator-cost model", "Library/service APIs", "https://github.com/unclecode/crawl4ai"),
    ]
    adjacent = [
        ("Browserbase Browser", "Interactive cloud browser / Stagehand extraction", "Browser-harness dataset; not lightweight fetch latency", "https://docs.browserbase.com/use-cases/web-data-retrieval"),
        ("Bocha Web Search", "Query → ranked pages/snippets/optional summary", "Search dataset; not arbitrary URL extraction", "https://open.bochaai.com/"),
        ("Baidu Qianfan AI Search", "Query → summaries, URLs and snippets", "Search dataset; not arbitrary URL extraction", "https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5"),
        ("Tencent Cloud Web Search API", "Query → titles, snippets and source URLs", "Search dataset; not arbitrary URL extraction", "https://cloud.tencent.com/document/product/1806/121811"),
        ("Alibaba Model Studio", "Model tool: web_search + web_extractor", "Model answer/citation dataset; extraction currently accompanies search", "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses"),
        ("Google Gemini URL Context", "Model consumes supplied public URLs", "Model answer/citation dataset; retrieved content is billed as model input", "https://ai.google.dev/gemini-api/docs/url-context"),
        ("Google Search / Bing / Brave Search", "Query → search results", "Discovery/search benchmark, not Web Fetch benchmark", "https://developers.google.com/custom-search/v1/overview"),
    ]
    def rows(items: list[tuple[str, str, str, str]]) -> str:
        return "".join(
            f'<tr><td><a href="{esc(url)}"><b>{esc(name)}</b></a></td><td>{esc(surface)}</td><td>{esc(rule)}</td></tr>'
            for name, surface, rule, url in items
        )
    direct_rows = "".join(
        f'<tr><td><a href="{esc(url)}"><b>{esc(name)}</b></a></td><td>{esc(surface)}</td><td>{esc(fit)}</td><td><code>{esc(api)}</code></td></tr>'
        for name, surface, fit, api, url in direct
    )
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Supplier capability boundary</div><h1>哪些产品能放进同一张 Web Fetch 表</h1><p class="lead">“Agent-native”不是可测指标，本报告改用输入输出契约分类。只有接受已知 URL、直接返回可持久化页面正文的产品进入主表；搜索结果、模型答案和反机器人解锁分别评测。</p></div></section>
<section class="section"><div class="wrap"><h2>已知 URL → 可持久化正文</h2><div class="table-wrap"><table><thead><tr><th>提供方</th><th>能力</th><th>进入哪张表</th><th>接口面</th></tr></thead><tbody>{direct_rows}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>相邻能力，不与主表混算</h2><div class="table-wrap"><table><thead><tr><th>产品</th><th>实际输入输出</th><th>评测规则</th></tr></thead><tbody>{rows(adjacent)}</tbody></table></div><div class="callout" style="margin-top:18px"><b>Browserbase 文档冲突：</b>当前 REST reference 仍描述 raw/no-JS/1 MB，而较新的官方 changelog、产品介绍和 pricing 已描述 Markdown/Extract。账号实测确认请求字段和响应格式前，只列为候选，不给它虚构分数。</div><div class="callout" style="margin-top:18px"><b>Lexmount 地域说明：</b>官方文档列出 <code>api.lexmount.com</code> 与 <code>api.lexmount.cn</code> 两套区域 API/控制台和各自凭据。这里不能据此推断物理上只有两个“服务节点”；本仓库按需求只调用 Global。</div></div></section>
<section class="section"><div class="wrap"><h2>成本：公开单价与本轮可观测单位</h2><div class="table-wrap"><table><thead><tr><th>方法</th><th>本轮</th><th>公开计费口径</th><th>可比较结论</th></tr></thead><tbody><tr><td><b>CowAgent 内置</b></td><td>10 calls；供应商 API 费用 0</td><td>自付网络、CPU、维护和文档解析依赖</td><td>不能把“无 API 账单”误写为零总成本</td></tr><tr><td><b>Lexmount Global</b></td><td>10 calls</td><td>官方文档标注 internal testing，未发现公开价格</td><td>价格待 Lexmount 给出；当前不能声称比竞品便宜</td></tr><tr><td><b>Firecrawl</b></td><td>10 successful URLs = 24 credits；PDF 单次 15</td><td>HTML base scrape 通常 1 credit/page；PDF parsing 随页数增加；Free 1,000 credits/月；官方页面列出 1,000 credits $5 one-time</td><td>本轮在免费额度内为 $0；按 $5/1k 折算约 $0.12</td></tr><tr><td><b>Tavily basic Extract</b></td><td>未实测</td><td>每 5 个成功 URL = 1 credit；每月 1,000 free credits</td><td>有 key 后进入主表</td></tr><tr><td><b>Exa Contents text</b></td><td>未实测</td><td>官方 x402 示例为 $0.001/page</td><td>需同时固定 maxAgeHours 才能比较时延/新鲜度</td></tr><tr><td><b>Browserbase</b></td><td>未实测</td><td>Pricing: raw Fetch $1/1k；Extract $4/1k（代理更高）</td><td>先确认当前 API contract，再选择 raw 或 Extract 表</td></tr></tbody></table></div></div></section>"""
    write_page(report_dir / "market" / "index.html", layout(title="市场与能力边界", body=body, base="../"))


def _write_adoption_page(report_dir: Path) -> None:
    source = Path(__file__).resolve().parent.parent / "research" / "webfetch-adopters-2026-08-31.json"
    if not source.exists():
        body = '<section class="hero"><div class="wrap"><h1>开源采用证据集尚未生成</h1><pre>python3 research/discover_webfetch_adopters.py --stars 1000 --days 7 --per-signature 100 --output research/webfetch-adopters-2026-08-31.json</pre></div></section>'
        write_page(report_dir / "adoption" / "index.html", layout(title="开源采用", body=body, base="../"))
        return
    data = json.loads(source.read_text(encoding="utf-8"))
    (report_dir / "adoption").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, report_dir / "adoption" / "evidence.json")
    rows = []
    for item in data["included"]:
        matches = item.get("matches") or []
        signatures = sorted({match["signature"] for match in matches})
        files = "<br>".join(f'<a href="{esc(match["url"])}"><code>{esc(match["path"])}</code></a>' for match in matches[:4])
        if len(matches) > 4:
            files += f'<br><span class="tiny">+{len(matches)-4} more in evidence.json</span>'
        rows.append(f'<tr><td><a href="{esc(item["url"])}"><b>{esc(item["repo"])}</b></a><br><span class="tiny">{esc(item.get("description") or "")}</span></td><td>{item["stars"]:,}</td><td>{esc(item["default_branch_head_at"])}</td><td>{esc(", ".join(signatures))}</td><td>{files}</td></tr>')
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">GitHub default-branch evidence</div><h1>高 stars、近 7 天活跃的 Web Fetch / Read / Search 采用仓库</h1><p class="lead">检索不依赖字面量 web fetch；覆盖供应商 endpoint、SDK 方法、通用工具类、搜索/URL Context 和开源正文提取库。结果按仓库去重，并由 GraphQL 核验默认分支 HEAD 提交时间。</p><div class="meta"><span class="pill">{len(data['signatures'])} signatures</span><span class="pill">{data['unique_repositories_examined']:,} unique repositories inspected</span><span class="pill">{len(data['included'])} included</span><span class="pill">stars ≥ {data['minimum_stars']:,}</span></div></div></section><section class="section"><div class="wrap"><div class="callout">“全部”指列出的 {len(data['signatures'])} 个签名、每签名前 {data['per_signature_cap']} 条 GitHub Code Search 结果内全部满足条件的仓库；不是对全 GitHub 的数学完备枚举。原始命中和查询保存在 <a href="evidence.json">evidence.json</a>。</div><div class="table-wrap" style="margin-top:18px"><table><thead><tr><th>仓库</th><th>Stars</th><th>默认分支 HEAD</th><th>命中签名</th><th>证据文件</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "adoption" / "index.html", layout(title="开源采用", body=body, base="../"))


def _write_integration_page(report_dir: Path) -> None:
    routing = """public static HTML -> Lexmount Global beta route
PDF/document       -> CowAgent local parser until PDF content passes
JavaScript page    -> browser tool or explicitly selected rendering engine
login/interaction  -> browser tool, never WebFetch"""
    contract = """{
  "name": "web_fetch",
  "input": {"url": "https://example.com"},
  "output": {
    "title": "Example Domain",
    "content": "...",
    "final_url": "https://example.com/",
    "provider": "lexmount-global",
    "trace_id": "..."
  }
}"""
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Integration from evidence</div><h1>接入 CowAgent 与其它 Agent 的最小可用路径</h1><p class="lead">CowAgent 当前是仓库内置 HTTP + 文本/文档解析，不依赖外部 Web Fetch 供应商。接入点应在现有 <code>WebFetch</code> 类内部，通过 provider 配置切换，模型仍只看到一个 <code>web_fetch</code>。</p></div></section><section class="section"><div class="wrap"><h2>当前建议路由</h2><pre>{esc(routing)}</pre><div class="callout">依据本轮实测：Lexmount 静态页快且请求稳定，但 JS/PDF 正文未过；CowAgent PDF 受本地解析依赖影响；Firecrawl 全过但更慢且 PDF 单次 15 credits。</div></div></section><section class="section"><div class="wrap"><h2>CowAgent 实现细节</h2><ol><li>保留现有 URL scheme 与 SSRF 检查，再调用供应商。</li><li>只从环境配置读取 <code>LEXMOUNT_API_KEY</code> / <code>LEXMOUNT_PROJECT_ID</code>；不进入 tool schema、artifact 或模型上下文。</li><li>本次 rollout 只调用 <code>https://api.lexmount.com/v1/extract</code>，401 不跨域重试。</li><li>区分 authentication、rate_limit、upstream、content failure；HTTP 200 空正文不能算成功。</li><li>fallback 同时记录 primary 与 fallback 的耗时、费用和 trace，否则指标会被隐藏。</li></ol></div></section><section class="section"><div class="wrap"><h2>跨 Agent 稳定契约</h2><pre>{esc(contract)}</pre><p class="sub"><code>web_search(query)</code> 负责发现 URL，<code>web_fetch(url)</code> 负责读取正文，browser 负责点击、登录和交互。供应商变化不应制造新的模型可见同义工具。</p></div></section>"""
    write_page(report_dir / "integration" / "index.html", layout(title="集成决策", body=body, base="../"))


def _write_account_page(report_dir: Path) -> None:
    rows = []
    for item in ACCOUNT_REGISTRY:
        variables = "<br>".join(f"<code>{esc(name)}</code>" for name in item["env_vars"])
        readiness = "Adapter ready" if item["implemented"] else "Adapter planned"
        rows.append(
            f"<tr><td><b>{esc(item['provider'])}</b><br><span class=\"tiny\">{esc(item['priority'])} · {readiness}</span></td>"
            f"<td><a href=\"{esc(item['registration_url'])}\">注册 / 登录</a><br><a href=\"{esc(item['key_url'])}\">获取凭据</a></td>"
            f"<td>{variables}</td><td>{esc(item['steps'])}</td><td>{esc(item['free_note'])}</td></tr>"
        )
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Account preparation</div><h1>账号注册与凭据清单</h1><p class="lead">按能力类别列出官方注册入口、API key 获取位置和需要填写的环境变量。报告不保存实际凭据。</p><div class="callout"><b>当前主评测：</b>只使用 Lexmount Global 与 Firecrawl。Tavily、Exa、Cloudflare 可进入已知 URL→正文主表；搜索 API 与模型绑定工具必须使用各自数据集和指标。</div></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>提供方</th><th>官方入口</th><th>环境变量</th><th>取得方式</th><th>免费/准入说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><h3>终端打印同一清单</h3><pre>python3 -m browser_harness_eval accounts --env-file ~/.env</pre></div></section>"""
    write_page(report_dir / "accounts" / "index.html", layout(title="账号准备", body=body, base="../"))


def _write_attempt_pages(report_dir: Path, records: list[dict[str, Any]]) -> None:
    for record in records:
        label, cls = status(record)
        artifact = report_dir / record["artifact_path"]
        content = artifact.read_text(encoding="utf-8", errors="replace")
        display = content[:200_000]
        truncation = "<p class=\"warn\">页面预览截断至 200,000 字符；原始 artifact 保留完整内容。</p>" if len(content) > len(display) else ""
        matched = "".join(f"<span class=\"tag\">{esc(x)}</span>" for x in record["metrics"]["matched_markers"]) or "<span class=\"empty\">None</span>"
        expected = "".join(f"<span class=\"tag\">{esc(x)}</span>" for x in record["metrics"]["expected_markers"])
        error = ""
        if label != "Pass":
            error = f"<div class=\"callout failure\"><b>{esc(label)}</b><br>Error type: <code>{esc(record['error_type'] or 'content_validation_failure')}</code><br>{esc(record['error_message'] or 'Expected content markers were not found.')}</div>"
        metadata = esc(json.dumps(record["provider_metadata"], ensure_ascii=False, indent=2))
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Attempt detail · {esc(record['provider'])}</div><h1>{esc(record['case_name'])}</h1><div class="meta"><span class="pill {cls}">{label}</span><span class="pill">run {record['run']}</span><span class="pill">{ms(record['elapsed_ms'])}</span></div></div></section><section class="section"><div class="wrap">{error}<div class="attempt-grid" style="margin-top:16px"><div class="kv"><b>Attempt ID</b><code>{esc(record['attempt_id'])}</code></div><div class="kv"><b>HTTP status</b>{esc(record['status_code']) or '—'}</div><div class="kv"><b>Output size</b>{record['metrics']['content_chars']:,} chars</div><div class="kv"><b>Marker recall</b>{pct(record['metrics']['marker_recall'])}</div><div class="kv"><b>Deadline overrun</b>{'Yes' if record['metrics'].get('deadline_overrun') else 'No'}</div></div><h3>Expected markers</h3><p>{expected}</p><h3>Matched markers</h3><p>{matched}</p><h3>Provider metadata</h3><pre>{metadata}</pre></div></section><section class="section"><div class="wrap"><h2>获取到的内容</h2><p><a href="../{esc(record['artifact_path'])}">打开完整原始 artifact →</a></p>{truncation}<pre>{esc(display)}</pre></div></section>"""
        write_page(report_dir / "attempts" / f"{slug(record['attempt_id'])}.html", layout(title=record["attempt_id"], body=body, base="../"))
