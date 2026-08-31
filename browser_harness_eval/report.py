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
from .methods import method_info


STYLE = r"""
:root{--bg:#071015;--panel:#0d1a21;--panel2:#122630;--ink:#edf6f7;--muted:#9aadb4;--line:#263c46;--cyan:#49d6ed;--violet:#8b7cff;--lime:#c6ed3d;--coral:#ff8168;--good:#67df9f;--bad:#ff6b7a;--warn:#ffd166;--max:1180px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 80% -10%,#18284b 0,transparent 30%),var(--bg);color:var(--ink);font:15px/1.65 Inter,system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif}a{color:var(--cyan);text-decoration:none}a:hover{text-decoration:underline}.wrap{width:min(var(--max),calc(100% - 34px));margin:auto}.top{border-bottom:1px solid var(--line);background:#071015e8;position:sticky;top:0;z-index:5;backdrop-filter:blur(14px)}nav{display:flex;align-items:center;gap:20px;overflow:auto;padding:12px 0;white-space:nowrap}.brand{font-weight:850;color:#fff;margin-right:auto}.hero{padding:54px 0 34px}.eyebrow{font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan)}h1{font-size:clamp(34px,6vw,66px);line-height:1.03;letter-spacing:-.045em;margin:16px 0}h2{font-size:clamp(26px,4vw,40px);letter-spacing:-.03em;margin:0 0 14px}h3{font-size:20px;margin:28px 0 10px}.lead,.sub{color:var(--muted);max-width:900px}.meta{display:flex;gap:8px;flex-wrap:wrap}.pill,.tag{border:1px solid var(--line);background:#0b171d;border-radius:999px;padding:5px 10px;color:#bdd0d5;font-size:12px}.tag{padding:2px 7px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{background:linear-gradient(145deg,#10212a,#0b171d);border:1px solid var(--line);border-radius:15px;padding:20px}.kpi{grid-column:span 3;border-top:3px solid var(--cyan)}.kpi:nth-child(2){border-top-color:var(--violet)}.kpi:nth-child(3){border-top-color:var(--lime)}.kpi:nth-child(4){border-top-color:var(--coral)}.n{font-size:34px;font-weight:850;letter-spacing:-.04em;margin:8px 0}.section{padding:44px 0;border-top:1px solid var(--line)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{border-collapse:collapse;width:100%;min-width:760px;background:#0b171d}th,td{padding:12px 13px;border-bottom:1px solid #1d323b;text-align:left;vertical-align:top}th{background:#12242c;font-size:12px;letter-spacing:.04em}td{color:#bfd0d4}.good{color:var(--good);font-weight:750}.bad{color:var(--bad);font-weight:750}.warn{color:var(--warn);font-weight:750}.bar{height:9px;background:#162a33;border-radius:99px;overflow:hidden;min-width:120px}.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet));border-radius:99px}.callout{border-left:4px solid var(--warn);background:#251f13;padding:14px 17px;border-radius:0 12px 12px 0}.failure{border-left-color:var(--bad);background:#2a1419}.success{border-left-color:var(--good);background:#10271d}pre,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}code{color:#b9f4ff;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#050d11;border:1px solid var(--line);padding:18px;border-radius:13px;color:#d7e8eb;max-height:70vh;overflow:auto}.attempt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.kv{background:#0b171d;border:1px solid var(--line);border-radius:12px;padding:13px}.kv b{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}.footer{padding:40px 0 70px;color:#7e969e}.empty{color:var(--muted);font-style:italic}.tiny{font-size:12px;color:#8299a1}
.visual-summary{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:16px;background:#07162c}details{border:1px solid var(--line);border-radius:12px;background:#0b171d;margin:12px 0}summary{cursor:pointer;padding:13px 16px;font-weight:750;color:#d7e8eb}details>pre,details>div{margin:0;border:0;border-top:1px solid var(--line);border-radius:0}.steps{counter-reset:step}.steps>li{margin:12px 0;padding-left:8px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.break{overflow-wrap:anywhere}@media(max-width:800px){.kpi,.grid>[style*="grid-column:span"]{grid-column:span 6!important}}@media(max-width:520px){.kpi,.grid>[style*="grid-column:span"]{grid-column:span 12!important}h1{overflow-wrap:anywhere}.wrap{width:min(var(--max),calc(100% - 24px))}}
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
<header class="top"><nav class="wrap"><a class="brand" href="{base}index.html">Browser Harness Eval</a><a href="{base}datasets/index.html">评测集</a><a href="{base}environment/index.html">环境</a><a href="{base}methodology/index.html">方法</a><a href="{base}providers/index.html">结果</a><a href="{base}analysis/index.html">分析</a><a href="{base}execution/index.html">复现</a><a href="{base}market/index.html">市场</a><a href="{base}adoption/index.html">开源采用</a><a href="{base}integration/index.html">集成</a><a href="{base}accounts/index.html">账号</a></nav></header>
<main>{body}</main><footer class="footer"><div class="wrap">Browser Harness Evaluation · Web Fetch Tool<br><span class="tiny">Generated from persisted artifacts. No credentials are embedded in this report.</span></div></footer></body></html>"""


def write_page(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_report(run_dir: Path, dataset: dict[str, Any], records: list[dict[str, Any]]) -> Path:
    summary = summarize(records)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_dir = run_dir / "report"
    if report_dir.exists():
        shutil.rmtree(report_dir)
    (report_dir / "assets").mkdir(parents=True)
    (report_dir / "assets" / "style.css").write_text(STYLE, encoding="utf-8")
    assets_source = Path(__file__).resolve().parent.parent / "report-assets"
    visual = assets_source / "webfetch-benchmark-v3.png"
    visual_meta = assets_source / "webfetch-benchmark-v3.meta.json"
    if visual.exists() and visual_meta.exists() and _visual_matches_summary(visual_meta, manifest, summary):
        shutil.copy2(visual, report_dir / "assets" / visual.name)
    shutil.copy2(run_dir / "results.jsonl", report_dir / "results.jsonl")
    shutil.copy2(run_dir / "summary.json", report_dir / "summary.json")
    shutil.copy2(run_dir / "manifest.json", report_dir / "manifest.json")
    shutil.copy2(run_dir / "dataset.json", report_dir / "dataset.json")
    artifacts_target = report_dir / "artifacts"
    if artifacts_target.exists():
        shutil.rmtree(artifacts_target)
    shutil.copytree(run_dir / "artifacts", artifacts_target)

    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_provider[record["provider"]].append(record)
        by_case[record["case_id"]].append(record)

    _write_index(report_dir, manifest, summary, records)
    _write_dataset_pages(report_dir, dataset, by_case)
    _write_provider_pages(report_dir, summary, by_provider)
    _write_metric_page(report_dir)
    _write_environment_page(report_dir, manifest)
    _write_methodology_page(report_dir, manifest)
    _write_analysis_page(report_dir, summary, by_case)
    _write_execution_page(report_dir, manifest)
    _write_market_page(report_dir, by_provider)
    _write_adoption_page(report_dir)
    _write_integration_page(report_dir)
    _write_account_page(report_dir)
    _write_attempt_pages(report_dir, records)
    return report_dir / "index.html"


def _visual_matches_summary(meta_path: Path, manifest: dict[str, Any], summary: dict[str, Any]) -> bool:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("dataset_id") != manifest.get("dataset", {}).get("id"):
        return False
    expected = meta.get("providers", {})
    actual = summary.get("providers", {})
    if set(expected) != set(actual):
        return False
    fields = ("content_pass_rate", "p50_ms", "p95_ms")
    return all(expected[name].get(field) == actual[name].get(field) for name in expected for field in fields)


def _write_index(report_dir: Path, manifest: dict[str, Any], summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    providers = summary["providers"]
    blocked = {
        name for name in providers
        if providers[name]["attempts"] > 0
        and providers[name]["failure_types"].get("authentication") == providers[name]["attempts"]
    }
    comparable = [r for r in records if method_info(r["provider_type"])["leaderboard"] == "readable-content" and r["provider"] not in blocked]
    score_records = comparable or records
    passing = sum(r["ok"] and r["metrics"]["content_pass"] for r in score_records)
    transport = sum(r["ok"] for r in score_records)
    kpis = f"""<div class="grid">
<div class="card kpi"><div class="eyebrow">Attempts</div><div class="n">{len(records)}</div><div class="sub">完整保存的调用次数</div></div>
<div class="card kpi"><div class="eyebrow">Providers</div><div class="n">{len(providers)}</div><div class="sub">本次实际运行的方法</div></div>
<div class="card kpi"><div class="eyebrow">Transport</div><div class="n">{pct(transport/len(score_records))}</div><div class="sub">可比方法请求成功率</div></div>
<div class="card kpi"><div class="eyebrow">Strict content</div><div class="n">{pct(passing/len(score_records))}</div><div class="sub">可比方法 ground-truth 通过率</div></div></div>"""
    rows = []
    for name, item in providers.items():
        failures = sum(item["failure_types"].values())
        rows.append(f"""<tr><td><a href="providers/{slug(name)}.html"><b>{esc(name)}</b></a><br><span class="tiny">{esc(item['provider_type'])}</span></td><td>{esc(method_info(item['provider_type'])['leaderboard'])}</td><td>{item['attempts']}</td><td class="{'good' if item['transport_success_rate']==1 else 'warn'}">{pct(item['transport_success_rate'])}</td><td class="{'good' if item['content_pass_rate']==1 else 'warn'}">{pct(item['content_pass_rate'])}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td><td>{failures}</td><td>{item['deadline_overrun_count']}</td></tr>""")
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Formal evaluation report · web_fetch</div><h1>{esc(manifest['dataset']['name'])}</h1><p class="lead">对 URL → 内容方法进行可复现比较。请求结果、失败响应、指标与环境信息均由本次运行 artifact 生成。</p><div class="meta"><span class="pill">{esc(manifest['started_at'])}</span><span class="pill">{manifest['dataset']['case_count']} cases</span><span class="pill">{manifest['parameters']['runs']} runs</span><span class="pill">Python {esc(manifest['environment']['python'])}</span></div></div></section>
<section class="section"><div class="wrap">{kpis}</div></section>
{('<section class="section"><div class="wrap"><h2>一图读懂能力边界与本轮结果</h2><p class="sub">同一总分只比较相同输入输出契约；Cloudflare 因无效 token 不评分，/content 也不与清洗正文混算。</p><img class="visual-summary" src="assets/webfetch-benchmark-v3.png" alt="70 attempts across seven Web Fetch methods, strict scores, latency and failure roots"></div></section>' if (report_dir / 'assets' / 'webfetch-benchmark-v3.png').exists() else '')}
<section class="section"><div class="wrap"><h2>提供方比较</h2><p class="sub">Transport success 与 strict content 分开统计；score group 明确 rendered HTML 诊断不进入正文主榜。</p><div class="table-wrap"><table><thead><tr><th>方法</th><th>Score group</th><th>Attempts</th><th>Transport</th><th>Content</th><th>P50</th><th>P95</th><th>Failures</th><th>Deadline overruns</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>报告导航</h2><div class="grid"><div class="card" style="grid-column:span 3"><h3>评测集</h3><p class="sub">逐案例查看能力、锚点和结果。</p><a href="datasets/index.html">打开评测集 →</a></div><div class="card" style="grid-column:span 3"><h3>提供方 / 方法</h3><p class="sub">逐方法查看性能与失败。</p><a href="providers/index.html">打开方法页 →</a></div><div class="card" style="grid-column:span 3"><h3>指标定义</h3><p class="sub">查看严格指标契约。</p><a href="metrics/index.html">打开指标页 →</a></div><div class="card" style="grid-column:span 3"><h3>账号准备</h3><p class="sub">注册地址、取 key 位置与环境变量。</p><a href="accounts/index.html">打开账号清单 →</a></div></div></div></section>"""
    write_page(report_dir / "index.html", layout(title="Web Fetch Evaluation", body=body, base=""))


def _write_dataset_pages(report_dir: Path, dataset: dict[str, Any], by_case: dict[str, list[dict[str, Any]]]) -> None:
    rows = []
    for case in dataset["cases"]:
        items = by_case.get(case["id"], [])
        passes = sum(item["ok"] and item["metrics"]["content_pass"] for item in items)
        rows.append(f"<tr><td><a href=\"{slug(case['id'])}.html\"><b>{esc(case['name'])}</b></a><br><code>{esc(case['id'])}</code></td><td>{esc(case['category'])}</td><td class=\"break\">{esc(case['url'])}</td><td>{len(case.get('required_facts',[]))}</td><td>{case.get('min_content_chars',1):,}</td><td>{passes}/{len(items)}</td></tr>")
        attempt_rows = []
        for item in items:
            label, cls = status(item)
            attempt_rows.append(f"<tr><td><a href=\"../attempts/{slug(item['attempt_id'])}.html\">{esc(item['provider'])} · run {item['run']}</a></td><td class=\"{cls}\">{label}</td><td>{ms(item['elapsed_ms'])}</td><td>{item['metrics']['marker_hits']}/{item['metrics']['marker_total']}</td><td>{esc(item['error_type'] or '')}</td></tr>")
        markers = "".join(f"<span class=\"tag\">{esc(marker)}</span>" for marker in case["expected_any"])
        fact_rows = "".join(f"<tr><td><code>{esc(fact.get('id'))}</code></td><td>{' / '.join(esc(x) for x in fact.get('alternatives',[]))}</td></tr>" for fact in case.get("required_facts", []))
        evidence = "".join(f"<li><a href=\"{esc(item.get('source'))}\">原始页面</a>：{esc(item.get('supports'))}</li>" for item in case.get("ground_truth_evidence", []))
        forbidden = "".join(f"<span class=\"tag\">{esc(marker)}</span>" for marker in case.get("forbidden_any", []))
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Ground-truth case · {esc(case['category'])}</div><h1>{esc(case['name'])}</h1><p class="lead">{esc(case.get('description',''))}</p><div class="meta"><a class="pill" href="{esc(case['url'])}">{esc(case['url'])}</a><span class="pill">min {case.get('min_content_chars',1):,} chars</span></div></div></section><section class="section"><div class="wrap"><h2>冻结标注</h2><p><b>显著标记：</b> {markers}</p><div class="table-wrap"><table><thead><tr><th>事实组</th><th>可接受表达（组内任一）</th></tr></thead><tbody>{fact_rows}</tbody></table></div><h3>反向标记</h3><p>{forbidden or '<span class="empty">None</span>'}</p><h3>标注证据</h3><ul>{evidence}</ul><p class="sub">{esc(case.get('annotation_notes',''))}</p><div class="callout">通过条件：命中显著标记、达到最小长度、每个事实组至少命中一个表达，并且不命中反向标记。</div></div></section><section class="section"><div class="wrap"><h2>所有方法与运行</h2><div class="table-wrap"><table><thead><tr><th>Attempt</th><th>状态</th><th>耗时</th><th>Markers</th><th>失败分类</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table></div></div></section>"""
        write_page(report_dir / "datasets" / f"{slug(case['id'])}.html", layout(title=case["name"], body=body, base="../"))
    gt = dataset.get("ground_truth", {})
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Versioned ground truth</div><h1>{esc(dataset['name'])}</h1><p class="lead">{esc(dataset.get('description',''))}</p><div class="meta"><span class="pill">{len(dataset['cases'])} cases</span><span class="pill">schema v{dataset.get('schema_version')}</span><span class="pill">verified {esc(gt.get('verified_at'))}</span></div></div></section><section class="section"><div class="wrap"><h2>标注协议</h2><p>{esc(gt.get('policy'))}</p><div class="callout"><b>状态：</b>{esc(gt.get('review_status'))}。参评输出不能定义 ground truth；修改标注必须升级数据集版本并重新跑所有方法。</div></div></section><section class="section"><div class="wrap"><h2>案例清单</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>类别</th><th>URL</th><th>事实组</th><th>最小字符</th><th>通过 attempts</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "datasets" / "index.html", layout(title="评测集", body=body, base="../"))


def _write_provider_pages(report_dir: Path, summary: dict[str, Any], by_provider: dict[str, list[dict[str, Any]]]) -> None:
    index_rows = []
    for name, item in summary["providers"].items():
        attempts = by_provider[name]
        method = method_info(item["provider_type"])
        credit_values = [record["provider_metadata"].get("credits_used") for record in attempts if record["provider_metadata"].get("credits_used") is not None]
        credits = sum(float(value) for value in credit_values) if credit_values else None
        credit_label = "—" if credits is None else f"{credits:g} credits"
        index_rows.append(f"<tr><td><a href=\"{slug(name)}.html\"><b>{esc(name)}</b></a></td><td>{esc(item['provider_type'])}</td><td>{pct(item['transport_success_rate'])}</td><td>{pct(item['content_pass_rate'])}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td><td>{credit_label}</td></tr>")
        attempt_rows = []
        for record in attempts:
            label, cls = status(record)
            attempt_rows.append(f"<tr><td><a href=\"../attempts/{slug(record['attempt_id'])}.html\">{esc(record['case_name'])} · run {record['run']}</a></td><td class=\"{cls}\">{label}</td><td>{ms(record['elapsed_ms'])}</td><td>{record['metrics']['content_chars']:,}</td><td>{esc(record['error_type'] or '')}</td></tr>")
        failures = "".join(f"<li><code>{esc(kind)}</code>: {count}</li>" for kind, count in item["failure_types"].items()) or "<li>None</li>"
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Provider method · {esc(item['provider_type'])}</div><h1>{esc(name)}</h1><p class="lead">{esc(method['contract'])}</p><div class="meta"><span class="pill">{esc(method['output'])}</span><span class="pill">{esc(method['leaderboard'])}</span></div></div></section><section class="section"><div class="wrap"><h2>公平性配置</h2><div class="attempt-grid"><div class="kv"><b>Endpoint</b><code>{esc(method['endpoint'])}</code></div><div class="kv"><b>Output contract</b>{esc(method['output'])}</div><div class="kv"><b>Freshness / wait</b>{esc(method['freshness'])}</div><div class="kv"><b>Score group</b>{esc(method['leaderboard'])}</div></div></div></section><section class="section"><div class="wrap"><div class="grid"><div class="card kpi"><div class="eyebrow">Transport</div><div class="n">{pct(item['transport_success_rate'])}</div></div><div class="card kpi"><div class="eyebrow">Strict content</div><div class="n">{pct(item['content_pass_rate'])}</div></div><div class="card kpi"><div class="eyebrow">Fact coverage</div><div class="n">{pct(item.get('mean_fact_coverage'))}</div></div><div class="card kpi"><div class="eyebrow">P95</div><div class="n">{ms(item['p95_ms'])}</div></div></div><h3>失败分布</h3><ul>{failures}</ul><p>Deadline overruns: <b>{item['deadline_overrun_count']}</b> · Observed supplier usage: <b>{credit_label}</b></p></div></section><section class="section"><div class="wrap"><h2>逐次结果</h2><div class="table-wrap"><table><thead><tr><th>Case / run</th><th>状态</th><th>耗时</th><th>字符</th><th>失败分类</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table></div></div></section>"""
        write_page(report_dir / "providers" / f"{slug(name)}.html", layout(title=f"Provider: {name}", body=body, base="../"))
    index_body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Provider registry</div><h1>Web Fetch 提供方 / 方法</h1><p class="lead">Provider 是调用实现，和评测集、指标定义相互独立。同一数据集可重复运行不同 provider。</p></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>方法</th><th>类型</th><th>Transport</th><th>Content</th><th>P50</th><th>P95</th><th>Observed usage</th></tr></thead><tbody>{''.join(index_rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "providers" / "index.html", layout(title="提供方", body=index_body, base="../"))


def _write_metric_page(report_dir: Path) -> None:
    cards = "".join(f"<div class=\"card\" style=\"grid-column:span 6\"><h3><code>{esc(name)}</code></h3><p class=\"sub\">{esc(description)}</p></div>" for name, description in METRIC_DEFINITIONS.items())
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Metric contract</div><h1>评测指标定义</h1><p class="lead">指标在运行前固定定义。报告不会用 HTTP 成功替代正文成功，也不会从失败样本中删除慢请求。</p></div></section><section class="section"><div class="wrap"><div class="grid">{cards}</div><h3>聚合规则</h3><div class="callout">P50/P95 仅在 transport success 样本上计算；all-attempts P95 同时保留，用于暴露超时和慢失败。Content pass 的分母包含所有 attempts。</div></div></section>"""
    write_page(report_dir / "metrics" / "index.html", layout(title="指标定义", body=body, base="../"))


def _write_environment_page(report_dir: Path, manifest: dict[str, Any]) -> None:
    env = manifest["environment"]
    params = manifest["parameters"]
    providers = "".join(f"<tr><td>{esc(item['name'])}</td><td><code>{esc(item['type'])}</code></td><td>{esc(method_info(item['type'])['freshness'])}</td></tr>" for item in manifest["providers"])
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Execution environment</div><h1>评测环境</h1><p class="lead">环境信息来自运行时 manifest；凭据值不会写入报告。网络路径、服务负载和源站变化会影响绝对耗时，因此本轮只代表该时间与出口的观测。</p></div></section><section class="section"><div class="wrap"><div class="attempt-grid"><div class="kv"><b>Started UTC</b>{esc(manifest.get('started_at'))}</div><div class="kv"><b>Completed UTC</b>{esc(manifest.get('completed_at'))}</div><div class="kv"><b>Python</b>{esc(env.get('python'))}</div><div class="kv"><b>Platform</b>{esc(env.get('platform'))}</div><div class="kv"><b>Host</b>{esc(env.get('hostname'))}</div><div class="kv"><b>Runs / case</b>{params.get('runs')}</div><div class="kv"><b>Socket timeout</b>{params.get('timeout')} s</div><div class="kv"><b>Inter-call delay</b>{params.get('delay')} s</div></div><h3>运行方法</h3><div class="table-wrap"><table><thead><tr><th>Name</th><th>Adapter type</th><th>Freshness / wait setting</th></tr></thead><tbody>{providers}</tbody></table></div><div class="callout" style="margin-top:18px">单轮、小样本适合功能定位，不足以给出供应商 SLA。正式采购结论应至少多时段重复 3–5 次，并在目标客户地域各设一个 runner。</div></div></section>"""
    write_page(report_dir / "environment" / "index.html", layout(title="评测环境", body=body, base="../"))


def _write_methodology_page(report_dir: Path, manifest: dict[str, Any]) -> None:
    methods = "".join(f"<tr><td><b>{esc(item['name'])}</b></td><td><code>{esc(method_info(item['type'])['endpoint'])}</code></td><td>{esc(method_info(item['type'])['output'])}</td><td>{esc(method_info(item['type'])['freshness'])}</td><td>{esc(method_info(item['type'])['leaderboard'])}</td></tr>" for item in manifest["providers"])
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Pre-registered method</div><h1>评测方法与公平性</h1><p class="lead">主问题是“给定公开 URL，能否稳定返回正确、完整、可供 Agent 使用的正文”。搜索召回、登录交互、反机器人解锁和结构化 AI extraction 不混入主分数。</p></div></section>
<section class="section"><div class="wrap"><h2>执行顺序</h2><ol class="steps"><li>冻结数据集 v2 ground truth，再运行任何供应商。</li><li>每个方法按相同 URL、timeout、run 数执行；保存完整响应或异常。</li><li>先判 transport，再按事实组、最小长度和反向标记判 strict content。</li><li>聚合成功率、事实覆盖率、P50/P95、输出长度、失败类型和可观测 credits。</li><li>HTML 仅由 artifacts/JSONL 生成；不手工填写供应商分数。</li></ol></div></section>
<section class="section"><div class="wrap"><h2>方法契约</h2><div class="table-wrap"><table><thead><tr><th>方法</th><th>实际 endpoint</th><th>输出</th><th>新鲜度/等待</th><th>分组</th></tr></thead><tbody>{methods}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>Cloudflare 端点为何这样分</h2><div class="grid"><div class="card" style="grid-column:span 6"><h3><code>/markdown</code> 进入主表</h3><p>官方定义是把浏览器加载后的页面转为 Markdown，并去掉样式和脚本；与 Lexmount main_text、Firecrawl Markdown、Tavily Extract、Exa text 的“可读正文”输出目标最接近。</p><a href="https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/">官方 Markdown 文档 →</a></div><div class="card" style="grid-column:span 6"><h3><code>/content</code> 单列诊断</h3><p>官方定义是 JavaScript 执行后的完整 HTML（包括 head）。它适合判断浏览器是否渲染成功，但 HTML 标签、脚本和模板噪声不能和清洗正文的字符数直接比较。</p><a href="https://developers.cloudflare.com/browser-run/quick-actions/content-endpoint/">官方 Content 文档 →</a></div></div><div class="callout"><code>/json</code> 需要 AI schema/prompt，属于结构化 extraction；它的质量、时延和模型成本应在另一数据集评测。</div></div></section>
<section class="section"><div class="wrap"><h2>正确性与完整性</h2><p><b>主裁判：</b>版本化人工 ground truth。每个事实组代表一个必须保留的语义单元，组内同义字符串均可接受；所有组命中才通过。另设最小长度防止只返回标题，反向标记防止 CAPTCHA/Access denied 被算作正文。</p><p><b>LLM judge：</b>不进入主榜。若后续启用，只做二级语义审阅：供应商名盲化、固定 rubric 与 JSON schema、记录模型/提示版本、引用 ground truth 和原始 artifact，并抽样人工复核。单一 LLM 的分数不可替代冻结标注。</p></div></section>"""
    write_page(report_dir / "methodology" / "index.html", layout(title="评测方法", body=body, base="../"))


def _write_analysis_page(report_dir: Path, summary: dict[str, Any], by_case: dict[str, list[dict[str, Any]]]) -> None:
    comparable = [(name, item) for name, item in summary["providers"].items() if method_info(item["provider_type"])["leaderboard"] == "readable-content" and item["failure_types"].get("authentication") != item["attempts"]]
    ranked = sorted(comparable, key=lambda pair: (-pair[1]["content_pass_rate"], -pair[1].get("mean_fact_coverage", 0), pair[1]["p50_ms"] or float("inf")))
    rows = "".join(f"<tr><td>{index}</td><td><a href=\"../providers/{slug(name)}.html\"><b>{esc(name)}</b></a></td><td>{pct(item['transport_success_rate'])}</td><td>{pct(item['content_pass_rate'])}</td><td>{pct(item.get('mean_fact_coverage'))}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td></tr>" for index, (name, item) in enumerate(ranked, 1))
    case_rows = []
    for case_id, items in sorted(by_case.items()):
        cells = []
        for item in items:
            label, cls = status(item)
            cells.append(f"<span class=\"{cls}\">{esc(item['provider'])}: {label}</span> ({pct(item['metrics'].get('fact_coverage'))})")
        case_rows.append(f"<tr><td><a href=\"../datasets/{slug(case_id)}.html\"><b>{esc(items[0]['case_name'])}</b></a></td><td>{'<br>'.join(cells)}</td></tr>")
    failure_rows = []
    for items in by_case.values():
        for item in items:
            if item["ok"] and item["metrics"]["content_pass"]:
                continue
            misses = ", ".join(fact["id"] for fact in item["metrics"].get("fact_results", []) if not fact["pass"])
            reason = item.get("error_message") or (f"missing facts: {misses}" if misses else "strict content validation failed")
            failure_rows.append(f"<tr><td><a href=\"../attempts/{slug(item['attempt_id'])}.html\">{esc(item['provider'])}</a></td><td>{esc(item['case_name'])}</td><td><code>{esc(item.get('error_type') or 'content_validation')}</code></td><td>{esc(reason)}</td></tr>")
    auth_failures = [r for items in by_case.values() for r in items if r.get("error_type") == "authentication"]
    auth_note = f"<div class=\"callout failure\"><b>Authentication failure is a measured result:</b> {len(auth_failures)} attempts。它反映当前环境凭据不可用，不证明供应商产品能力差；修复凭据后必须重跑，不能人工补分。</div>" if auth_failures else ""
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Result analysis</div><h1>评测结果分析</h1><p class="lead">排序优先 strict content，其次事实覆盖率，再其次成功样本 P50。这个顺序避免“很快返回错误页”胜出。</p></div></section><section class="section"><div class="wrap">{auth_note}<h2>主榜（可读正文契约）</h2><div class="table-wrap"><table><thead><tr><th>#</th><th>方法</th><th>Transport</th><th>Strict content</th><th>Fact coverage</th><th>P50</th><th>P95</th></tr></thead><tbody>{rows}</tbody></table></div><p class="tiny">rendered-html-diagnostic 不进入该排序。</p></div></section><section class="section"><div class="wrap"><h2>失败根因明细</h2><div class="table-wrap"><table><thead><tr><th>方法</th><th>案例</th><th>分类</th><th>可操作原因</th></tr></thead><tbody>{''.join(failure_rows)}</tbody></table></div></div></section><section class="section"><div class="wrap"><h2>逐案例根因入口</h2><div class="table-wrap"><table><thead><tr><th>案例</th><th>方法状态与事实覆盖率</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table></div></div></section><section class="section"><div class="wrap"><h2>结论边界</h2><ul><li>内容正确性由冻结标注决定，输出越长不等于越完整；字符数只作诊断。</li><li>一次运行用于发现功能缺口，不代表长期稳定性或 SLA。</li><li>供应商公开 credits 与实际美元成本不是同一概念；没有公开价时不做价格胜负断言。</li><li>登录、保护站点与搜索召回需要独立数据集，不能从本轮外推。</li></ul></div></section>"""
    write_page(report_dir / "analysis" / "index.html", layout(title="结果分析", body=body, base="../"))


def _write_execution_page(report_dir: Path, manifest: dict[str, Any]) -> None:
    names = ",".join(item["name"] for item in manifest["providers"])
    command = f"""git clone https://github.com/lexmount/browser-harness-eval.git
cd browser-harness-eval
cp .env.example ~/.env  # fill keys; never commit this file
python3 -m unittest discover -s tests -v
python3 -m browser_harness_eval validate --env-file ~/.env --providers {names}
WEBFETCH_REAL_SMOKE=1 WEBFETCH_SMOKE_ENV_FILE=~/.env python3 -m unittest tests.test_real_smoke -v
./scripts/reproduce_report.sh runs/reproduced
open runs/reproduced/report/index.html"""
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Reproduction runbook</div><h1>详细执行过程与复现</h1><p class="lead">一个脚本会验证配置、运行固定数据集和供应商、保存所有 artifacts、生成分页 HTML 并检查本地链接。脚本能复现评测过程和报告结构；实时网站与云服务会变化，因此不能保证未来逐字节复现本次数值。</p></div></section><section class="section"><div class="wrap"><h2>新环境完整步骤</h2><pre>{esc(command)}</pre><h3>两种复现语义</h3><div class="grid"><div class="card" style="grid-column:span 6"><h3>重放已保存结果</h3><pre>python3 -m browser_harness_eval report runs/&lt;run-id&gt;</pre><p class="sub">不发网络请求；从 JSONL/artifacts 重新生成相同指标和 HTML。</p></div><div class="card" style="grid-column:span 6"><h3>重新实测</h3><pre>./scripts/reproduce_report.sh runs/new-run</pre><p class="sub">重新调用真实服务；页面内容、时延、credits 与供应商行为可能变化。</p></div></div></div></section><section class="section"><div class="wrap"><h2>失败定位顺序</h2><ol><li>看 providers 页区分 transport 与 strict content。</li><li>下钻 attempt 页，检查 HTTP 状态、error_type、事实组 MISS、反向标记和 provider metadata。</li><li>用 SHA-256 对照完整 artifact，避免网页预览截断造成误判。</li><li>认证失败先运行 token 官方验证；内容失败再对照 ground-truth 原始证据。</li><li>修复后创建新 run 目录，禁止覆盖原结果，以保留审计链。</li></ol></div></section>"""
    write_page(report_dir / "execution" / "index.html", layout(title="复现步骤", body=body, base="../"))


def _write_market_page(report_dir: Path, by_provider: dict[str, list[dict[str, Any]]]) -> None:
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
    observed_rows = []
    pricing = {
        "cowagent-built-in":"无供应商 API 账单；自付网络、CPU、依赖和维护",
        "lexmount-global":"官方文档标注 internal testing；未发现公开价格",
        "firecrawl":"HTML 通常按 credit；PDF 随页数增加，须看本次 metadata",
        "tavily":"basic Extract 通常按成功 URL 分组计 credit",
        "exa":"Contents 以账户套餐与所选内容能力计费",
        "cloudflare-markdown":"Browser Rendering 按账户计划/浏览器资源计费",
        "cloudflare-content":"同属 Browser Rendering；仅诊断 rendered HTML",
    }
    for name, attempts in sorted(by_provider.items()):
        credits = [item["provider_metadata"].get("credits_used") for item in attempts if item["provider_metadata"].get("credits_used") is not None]
        usage = "—" if not credits else f"{sum(float(value) for value in credits):g} credits"
        observed_rows.append(f"<tr><td><b>{esc(name)}</b></td><td>{len(attempts)} calls</td><td>{esc(usage)}</td><td>{esc(pricing.get(name,'查看供应商账户账单'))}</td></tr>")
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Supplier capability boundary</div><h1>哪些产品能放进同一张 Web Fetch 表</h1><p class="lead">“Agent-native”不是可测指标，本报告改用输入输出契约分类。只有接受已知 URL、直接返回可持久化页面正文的产品进入主表；搜索结果、模型答案和反机器人解锁分别评测。</p></div></section>
<section class="section"><div class="wrap"><h2>已知 URL → 可持久化正文</h2><div class="table-wrap"><table><thead><tr><th>提供方</th><th>能力</th><th>进入哪张表</th><th>接口面</th></tr></thead><tbody>{direct_rows}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>相邻能力，不与主表混算</h2><div class="table-wrap"><table><thead><tr><th>产品</th><th>实际输入输出</th><th>评测规则</th></tr></thead><tbody>{rows(adjacent)}</tbody></table></div><div class="callout" style="margin-top:18px"><b>Browserbase 文档冲突：</b>当前 REST reference 仍描述 raw/no-JS/1 MB，而较新的官方 changelog、产品介绍和 pricing 已描述 Markdown/Extract。账号实测确认请求字段和响应格式前，只列为候选，不给它虚构分数。</div><div class="callout" style="margin-top:18px"><b>Lexmount 地域说明：</b>官方文档列出 <code>api.lexmount.com</code> 与 <code>api.lexmount.cn</code> 两套区域 API/控制台和各自凭据。这里不能据此推断物理上只有两个“服务节点”；本仓库按需求只调用 Global。</div></div></section>
<section class="section"><div class="wrap"><h2>成本：本轮可观测单位</h2><div class="table-wrap"><table><thead><tr><th>方法</th><th>调用</th><th>响应暴露 usage</th><th>计费解释</th></tr></thead><tbody>{''.join(observed_rows)}</tbody></table></div><div class="callout" style="margin-top:18px">未在 API response 暴露 credits 不等于免费；美元成本应从同一账户账单导出，并固定套餐、区域、缓存和附加解析选项后再比较。</div></div></section>"""
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
    routing = """known public URL  -> one stable web_fetch tool; provider chosen by policy
PDF/document       -> route only to a method that passes the PDF ground truth
JavaScript page    -> route only to a rendering-capable method that passes the JS case
login/interaction  -> browser tool, never lightweight Web Fetch
search query       -> web_search first, then web_fetch selected result URLs"""
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
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Integration from evidence</div><h1>接入 CowAgent 与其它 Agent 的最小可用路径</h1><p class="lead">CowAgent 当前是仓库内置 HTTP + 文本/文档解析，不依赖外部 Web Fetch 供应商。接入点应在现有 <code>WebFetch</code> 类内部，通过 provider 配置切换，模型仍只看到一个 <code>web_fetch</code>。</p></div></section><section class="section"><div class="wrap"><h2>建议路由原则</h2><pre>{esc(routing)}</pre><div class="callout">具体默认 provider 必须以本报告“结果分析”和逐案例事实覆盖率为准。认证失败的方法不能被判为产品能力失败，也不能在修复凭据前获得人工补分。</div></div></section><section class="section"><div class="wrap"><h2>CowAgent 实现细节</h2><ol><li>保留现有 URL scheme 与 SSRF 检查，再调用供应商。</li><li>凭据只从环境配置读取；不进入 tool schema、artifact 或模型上下文。</li><li>Global rollout 只调用 <code>https://api.lexmount.com/v1/extract</code>，401 不跨域重试。</li><li>区分 authentication、rate_limit、upstream、content failure；HTTP 200 空正文不能算成功。</li><li>fallback 同时记录 primary 与 fallback 的耗时、费用和 trace，否则指标会被隐藏。</li></ol></div></section><section class="section"><div class="wrap"><h2>跨 Agent 稳定契约</h2><pre>{esc(contract)}</pre><p class="sub"><code>web_search(query)</code> 负责发现 URL，<code>web_fetch(url)</code> 负责读取正文，browser 负责点击、登录和交互。供应商变化不应制造新的模型可见同义工具。</p></div></section>"""
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
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Account preparation</div><h1>账号注册与凭据清单</h1><p class="lead">按能力类别列出官方注册入口、API key 获取位置和需要填写的环境变量。报告不保存实际凭据。</p><div class="callout"><b>当前主评测：</b>Lexmount Global、Firecrawl、Tavily Extract、Exa Contents 与 Cloudflare /markdown；Cloudflare /content 仅作 rendered HTML 诊断。搜索 API 与模型绑定工具使用独立数据集。</div></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>提供方</th><th>官方入口</th><th>环境变量</th><th>取得方式</th><th>免费/准入说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><h3>终端打印同一清单</h3><pre>python3 -m browser_harness_eval accounts --env-file ~/.env</pre></div></section>"""
    write_page(report_dir / "accounts" / "index.html", layout(title="账号准备", body=body, base="../"))


def _write_attempt_pages(report_dir: Path, records: list[dict[str, Any]]) -> None:
    for record in records:
        label, cls = status(record)
        artifact = report_dir / record["artifact_path"]
        content = artifact.read_text(encoding="utf-8", errors="replace")
        # HTML previews are presentation copies. Strip line-end spaces so generated
        # reports stay reviewable with git diff --check; the raw artifact remains exact.
        display = "\n".join(line.rstrip() for line in content[:200_000].splitlines())
        truncation = "<p class=\"warn\">页面预览截断至 200,000 字符；原始 artifact 保留完整内容。</p>" if len(content) > len(display) else ""
        matched = "".join(f"<span class=\"tag\">{esc(x)}</span>" for x in record["metrics"]["matched_markers"]) or "<span class=\"empty\">None</span>"
        expected = "".join(f"<span class=\"tag\">{esc(x)}</span>" for x in record["metrics"]["expected_markers"])
        error = ""
        if label != "Pass":
            error = f"<div class=\"callout failure\"><b>{esc(label)}</b><br>Error type: <code>{esc(record['error_type'] or 'content_validation_failure')}</code><br>{esc(record['error_message'] or 'Expected content markers were not found.')}</div>"
        metadata = esc(json.dumps(record["provider_metadata"], ensure_ascii=False, indent=2))
        fact_rows = "".join(f"<tr><td><code>{esc(item['id'])}</code></td><td class=\"{'good' if item['pass'] else 'bad'}\">{'PASS' if item['pass'] else 'MISS'}</td><td>{' / '.join(esc(x) for x in item['alternatives'])}</td><td>{' / '.join(esc(x) for x in item['matched']) or '—'}</td></tr>" for item in record["metrics"].get("fact_results", []))
        preview = display[:1800]
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">Attempt detail · {esc(record['provider'])}</div><h1>{esc(record['case_name'])}</h1><div class="meta"><span class="pill {cls}">{label}</span><span class="pill">run {record['run']}</span><span class="pill">{ms(record['elapsed_ms'])}</span></div></div></section><section class="section"><div class="wrap">{error}<div class="attempt-grid" style="margin-top:16px"><div class="kv"><b>Attempt ID</b><code>{esc(record['attempt_id'])}</code></div><div class="kv"><b>HTTP status</b>{esc(record['status_code']) or '—'}</div><div class="kv"><b>Output size</b>{record['metrics']['content_chars']:,} chars</div><div class="kv"><b>Fact coverage</b>{pct(record['metrics'].get('fact_coverage'))}</div><div class="kv"><b>Minimum length</b>{'PASS' if record['metrics'].get('length_pass',True) else 'FAIL'}</div><div class="kv"><b>SHA-256</b><code>{esc(record['metrics'].get('content_sha256',''))}</code></div></div><h3>事实组逐条判定</h3><div class="table-wrap"><table><thead><tr><th>Fact</th><th>状态</th><th>可接受表达</th><th>实际命中</th></tr></thead><tbody>{fact_rows or '<tr><td colspan="4">Legacy dataset: no fact groups</td></tr>'}</tbody></table></div><h3>Expected / matched prominent markers</h3><p>{expected}</p><p>{matched}</p><details><summary>Provider metadata</summary><pre>{metadata}</pre></details></div></section><section class="section"><div class="wrap"><h2>获取到的内容</h2><p><a href="../{esc(record['artifact_path'])}">打开完整原始 artifact →</a> · 完整性校验 <code>sha256:{esc(record['metrics'].get('content_sha256',''))}</code></p><h3>快速预览（前 1,800 字符）</h3><pre>{esc(preview)}</pre>{truncation}<details><summary>展开报告内完整内容（{len(display):,} / {len(content):,} 字符）</summary><pre>{esc(display)}</pre></details></div></section>"""
        write_page(report_dir / "attempts" / f"{slug(record['attempt_id'])}.html", layout(title=record["attempt_id"], body=body, base="../"))
