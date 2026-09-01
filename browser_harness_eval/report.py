from __future__ import annotations

import html
import json
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from .accounts import ACCOUNT_REGISTRY
from .metrics import METRIC_DEFINITIONS, summarize
from .methods import method_info


STYLE = r"""
:root{--bg:#071015;--panel:#0d1a21;--panel2:#122630;--ink:#edf6f7;--muted:#9aadb4;--line:#263c46;--cyan:#49d6ed;--violet:#8b7cff;--lime:#c6ed3d;--coral:#ff8168;--good:#67df9f;--bad:#ff6b7a;--warn:#ffd166;--max:1180px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 80% -10%,#18284b 0,transparent 30%),var(--bg);color:var(--ink);font:15px/1.65 Inter,system-ui,-apple-system,"Segoe UI","PingFang SC",sans-serif}a{color:var(--cyan);text-decoration:none}a:hover{text-decoration:underline}.wrap{width:min(var(--max),calc(100% - 34px));margin:auto}.top{border-bottom:1px solid var(--line);background:#071015e8;position:sticky;top:0;z-index:5;backdrop-filter:blur(14px)}nav{display:flex;align-items:center;gap:20px;overflow:auto;padding:12px 0;white-space:nowrap}.brand{font-weight:850;color:#fff;margin-right:auto}.hero{padding:54px 0 34px}.eyebrow{font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--cyan)}h1{font-size:clamp(34px,6vw,66px);line-height:1.03;letter-spacing:-.045em;margin:16px 0}h2{font-size:clamp(26px,4vw,40px);letter-spacing:-.03em;margin:0 0 14px}h3{font-size:20px;margin:28px 0 10px}.lead,.sub{color:var(--muted);max-width:900px}.meta{display:flex;gap:8px;flex-wrap:wrap}.pill,.tag{border:1px solid var(--line);background:#0b171d;border-radius:999px;padding:5px 10px;color:#bdd0d5;font-size:12px}.tag{padding:2px 7px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{background:linear-gradient(145deg,#10212a,#0b171d);border:1px solid var(--line);border-radius:15px;padding:20px}.kpi{grid-column:span 3;border-top:3px solid var(--cyan)}.kpi:nth-child(2){border-top-color:var(--violet)}.kpi:nth-child(3){border-top-color:var(--lime)}.kpi:nth-child(4){border-top-color:var(--coral)}.n{font-size:34px;font-weight:850;letter-spacing:-.04em;margin:8px 0}.section{padding:44px 0;border-top:1px solid var(--line)}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px}table{border-collapse:collapse;width:100%;min-width:760px;background:#0b171d}th,td{padding:12px 13px;border-bottom:1px solid #1d323b;text-align:left;vertical-align:top}th{background:#12242c;font-size:12px;letter-spacing:.04em}td{color:#bfd0d4}.good{color:var(--good);font-weight:750}.bad{color:var(--bad);font-weight:750}.warn{color:var(--warn);font-weight:750}.bar{height:9px;background:#162a33;border-radius:99px;overflow:hidden;min-width:120px}.bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet));border-radius:99px}.callout{border-left:4px solid var(--warn);background:#251f13;padding:14px 17px;border-radius:0 12px 12px 0}.failure{border-left-color:var(--bad);background:#2a1419}.success{border-left-color:var(--good);background:#10271d}pre,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}code{color:#b9f4ff;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#050d11;border:1px solid var(--line);padding:18px;border-radius:13px;color:#d7e8eb;max-height:70vh;overflow:auto}.attempt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.kv{background:#0b171d;border:1px solid var(--line);border-radius:12px;padding:13px}.kv b{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}.footer{padding:40px 0 70px;color:#7e969e}.empty{color:var(--muted);font-style:italic}.tiny{font-size:12px;color:#8299a1}
.visual-summary{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:16px;background:#07162c}details{border:1px solid var(--line);border-radius:12px;background:#0b171d;margin:12px 0}summary{cursor:pointer;padding:13px 16px;font-weight:750;color:#d7e8eb}details>pre,details>div{margin:0;border:0;border-top:1px solid var(--line);border-radius:0}.steps{counter-reset:step}.steps>li{margin:12px 0;padding-left:8px}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.break{overflow-wrap:anywhere}.compare{display:grid;grid-template-columns:1fr 1fr;gap:14px}.compare pre{height:390px}.review-form label{display:block;margin:12px 0 5px;color:var(--muted)}.review-form input,.review-form select,.review-form textarea,.filters input,.filters select{width:100%;background:#071015;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:10px}.review-form textarea{min-height:110px}.button{display:inline-block;border:1px solid var(--cyan);background:#10303a;color:var(--ink);border-radius:8px;padding:9px 13px;cursor:pointer;margin:8px 8px 0 0}.filters{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:10px;margin:14px 0}.flow{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.flow .card{position:relative}.flow .card:not(:last-child):after{content:"→";position:absolute;right:-15px;top:42%;color:var(--cyan);font-size:22px;z-index:2}@media(max-width:800px){.kpi,.grid>[style*="grid-column:span"]{grid-column:span 6!important}.compare{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.flow{grid-template-columns:1fr}.flow .card:after{display:none}}@media(max-width:520px){.kpi,.grid>[style*="grid-column:span"]{grid-column:span 12!important}.filters{grid-template-columns:1fr}h1{overflow-wrap:anywhere}.wrap{width:min(var(--max),calc(100% - 24px))}}
"""

REVIEW_JS = r"""
const key = id => `webfetch-hil:${id}`;
function loadReview(id) {
  const raw = localStorage.getItem(key(id)); if (!raw) return;
  const data = JSON.parse(raw);
  for (const [name, value] of Object.entries(data)) { const el = document.querySelector(`[name="${name}"]`); if (el) el.value = value; }
  const state = document.getElementById('review-state'); if (state) state.textContent = `已保存：${data.decision || '未判定'} · ${data.saved_at || ''}`;
}
function saveReview(id) {
  const form = document.getElementById('hil-form'); const data = Object.fromEntries(new FormData(form).entries());
  data.attempt_id = id; data.saved_at = new Date().toISOString(); localStorage.setItem(key(id), JSON.stringify(data)); loadReview(id);
}
function downloadReview(id) {
  saveReview(id); const blob = new Blob([JSON.stringify(JSON.parse(localStorage.getItem(key(id))), null, 2)], {type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=`${id}.review.json`; a.click(); URL.revokeObjectURL(a.href);
}
function exportAllReviews() {
  const out=[]; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k.startsWith('webfetch-hil:')) out.push(JSON.parse(localStorage.getItem(k)));}
  const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='webfetch-human-reviews.json';a.click();URL.revokeObjectURL(a.href);
}
function filterFailures() {
  const provider=(document.getElementById('failure-provider')?.value||'').toLowerCase();
  const category=(document.getElementById('failure-category')?.value||'').toLowerCase();
  const query=(document.getElementById('failure-query')?.value||'').toLowerCase();
  let visible=0;
  document.querySelectorAll('#failure-rows tr').forEach(row=>{const show=(!provider||row.dataset.provider===provider)&&(!category||row.dataset.category===category)&&(!query||row.textContent.toLowerCase().includes(query));row.hidden=!show;if(show)visible++;});
  const count=document.getElementById('failure-count');if(count)count.textContent=`显示 ${visible} 条`;
}
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
        return "请求失败", "bad"
    if not record["metrics"]["content_pass"]:
        return "机器内容初判未通过", "warn"
    return "机器内容初判通过", "good"


def layout(*, title: str, body: str, base: str, subtitle: str = "") -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><link rel="stylesheet" href="{base}assets/style.css"></head><body>
<header class="top"><nav class="wrap"><a class="brand" href="{base}index.html">Web Fetch 评测</a><a href="{base}datasets/index.html">评测集</a><a href="{base}benchmarks/index.html">基准来源</a><a href="{base}environment/index.html">环境</a><a href="{base}methodology/index.html">方法</a><a href="{base}analysis/index.html">结果分析</a><a href="{base}capabilities/index.html">文件与媒体</a><a href="{base}execution/index.html">复现</a><a href="{base}market/index.html">市场</a><a href="{base}adoption/index.html">开源采用</a><a href="{base}integration/index.html">集成</a><a href="{base}accounts/index.html">账号</a></nav></header>
<main>{body}</main><footer class="footer"><div class="wrap">Web Fetch 可复现评测<br><span class="tiny">报告由持久化原始结果生成，不包含任何凭据。</span></div></footer><script src="{base}assets/review.js"></script></body></html>"""


def write_page(path: Path, content: str) -> None:
    content = content.replace("冻结数据集 v3", "冻结当前版本数据集")
    content = content.replace("等 10 类案例", "等多种案例")
    content = content.replace("Integration from evidence", "基于实测证据的集成")
    content = content.replace("版本化标准答案（Ground truth）", "版本化标准答案")
    content = content.replace(
        "<th>事实覆盖率</th><th>有效 P50</th><th>有效 P95</th>",
        "<th>事实覆盖率</th><th>WCXB 词级 F1</th><th>有效 P50</th><th>有效 P95</th>",
    )
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
    (report_dir / "assets" / "review.js").write_text(REVIEW_JS, encoding="utf-8")
    visual = Path(__file__).resolve().parents[1] / "report-assets" / "webfetch-50-ten-methods-file-media.png"
    if visual.exists():
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
    _write_benchmark_page(report_dir, dataset)
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
    _write_attempt_pages(report_dir, dataset, records)
    _write_capabilities_page(report_dir)
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
    comparable = [r for r in records if method_info(r["provider_type"])["leaderboard"] == "可读正文" and r["provider"] not in blocked]
    score_records = comparable or records
    passing = sum(r["ok"] and r["metrics"]["content_pass"] for r in score_records)
    transport = sum(r["ok"] for r in score_records)
    kpis = f"""<div class="grid">
<div class="card kpi"><div class="eyebrow">调用记录</div><div class="n">{len(records)}</div><div class="sub">完整保存的真实调用次数</div></div>
<div class="card kpi"><div class="eyebrow">参评方法</div><div class="n">{len(providers)}</div><div class="sub">本次实际运行的方法</div></div>
<div class="card kpi"><div class="eyebrow">请求成功</div><div class="n">{pct(transport/len(score_records))}</div><div class="sub">只代表接口返回成功</div></div>
<div class="card kpi"><div class="eyebrow">机器内容初判</div><div class="n">{pct(passing/len(score_records))}</div><div class="sub">按冻结标准答案初判通过</div></div></div>"""
    rows = []
    for name, item in providers.items():
        failures = sum(item["failure_types"].values())
        rows.append(f"""<tr><td><a href="providers/{slug(name)}.html"><b>{esc(name)}</b></a><br><span class="tiny">{esc(item['provider_type'])}</span></td><td>{esc(method_info(item['provider_type'])['leaderboard'])}</td><td>{item['attempts']}</td><td class="{'good' if item['transport_success_rate']==1 else 'warn'}">{pct(item['transport_success_rate'])}</td><td class="{'good' if item['content_pass_rate']==1 else 'warn'}">{pct(item['content_pass_rate'])}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td><td>{failures}</td><td>{item['deadline_overrun_count']}</td></tr>""")
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">正式评测报告 · Web Fetch</div><h1>{esc(manifest['dataset']['name'])}</h1><p class="lead">比较“给定公开 URL，直接返回正确、完整、可供 Agent 使用的正文”这一项能力。每条请求、失败响应、标准答案、指标与环境信息都可下钻核验。</p><div class="meta"><span class="pill">{esc(manifest['started_at'])}</span><span class="pill">{manifest['dataset']['case_count']} 个案例</span><span class="pill">每案例 {manifest['parameters']['runs']} 次</span><span class="pill">Python {esc(manifest['environment']['python'])}</span></div></div></section>
<section class="section"><div class="wrap">{kpis}</div></section>
<section class="section"><div class="wrap"><h2>一图读懂：如何证明“抓到了正确正文”</h2><div class="flow"><div class="card"><div class="eyebrow">1 · 题目</div><h3>相同公开 URL</h3><p class="sub">静态页、长文、技术文档、JS 渲染、PDF、重定向等 10 类案例。</p></div><div class="card"><div class="eyebrow">2 · 标准答案</div><h3>先标注再评分</h3><p class="sub">冻结参考正文、必需事实、错误页标记与人工检查清单。</p></div><div class="card"><div class="eyebrow">3 · 真实调用</div><h3>保存完整输出</h3><p class="sub">供应商输出逐字落盘，并用 SHA-256 防止预览与原文不一致。</p></div><div class="card"><div class="eyebrow">4 · 机器初判</div><h3>四道确定性规则</h3><p class="sub">显著标记、最小长度、全部事实组、无错误页标记。</p></div><div class="card"><div class="eyebrow">5 · 人工复核</div><h3>最终判定由人确认</h3><p class="sub">并排核对标准答案与输出，保存或导出复核 JSON。</p></div></div><div class="callout" style="margin-top:18px"><b>特殊案例：</b>JS 表示正文需执行 JavaScript 后才出现；PDF 表示必须抽取论文文字；重定向表示客户端跟随 HTTP 3xx 的 Location 并返回最终页正文，不是给出一个可点击链接。</div></div></section>
<section class="section"><div class="wrap"><h2>提供方比较</h2><p class="sub">请求成功与机器内容初判分开统计；P50/P95 只使用“请求成功且机器内容初判通过”的有效调用，避免很快返回空壳或错误页反而获得速度优势。</p><div class="table-wrap"><table><thead><tr><th>方法</th><th>结果分组</th><th>调用次数</th><th>请求成功率</th><th>机器内容初判通过率</th><th>有效 P50</th><th>有效 P95</th><th>失败数</th><th>超时越界</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>报告导航</h2><div class="grid"><div class="card" style="grid-column:span 3"><h3>评测集</h3><p class="sub">逐案例查看能力、锚点和结果。</p><a href="datasets/index.html">打开评测集 →</a></div><div class="card" style="grid-column:span 3"><h3>提供方 / 方法</h3><p class="sub">逐方法查看性能与失败。</p><a href="providers/index.html">打开方法页 →</a></div><div class="card" style="grid-column:span 3"><h3>指标定义</h3><p class="sub">查看严格指标契约。</p><a href="metrics/index.html">打开指标页 →</a></div><div class="card" style="grid-column:span 3"><h3>账号准备</h3><p class="sub">注册地址、取 key 位置与环境变量。</p><a href="accounts/index.html">打开账号清单 →</a></div></div></div></section>"""
    visual_path = report_dir / "assets" / "webfetch-50-ten-methods-file-media.png"
    if visual_path.exists():
        visual_section = '<section class="section"><div class="wrap"><h2>一图读懂新版评测</h2><img class="visual-summary" src="assets/webfetch-50-ten-methods-file-media.png" alt="50 条、10 方法 Web Fetch 正文评测，以及文件与媒体三层能力边界"></div></section>'
        body = body.replace("</section>", "</section>" + visual_section, 1)
    write_page(report_dir / "index.html", layout(title="Web Fetch 正式评测报告", body=body, base=""))


def _write_dataset_pages(report_dir: Path, dataset: dict[str, Any], by_case: dict[str, list[dict[str, Any]]]) -> None:
    rows = []
    for case in dataset["cases"]:
        items = by_case.get(case["id"], [])
        passes = sum(item["ok"] and item["metrics"]["content_pass"] for item in items)
        rows.append(f"<tr><td><a href=\"{slug(case['id'])}.html\"><b>{esc(case['name'])}</b></a><br><code>{esc(case['id'])}</code></td><td>{esc(case['category'])}</td><td class=\"break\">{esc(case['url'])}</td><td>{len(case.get('required_facts',[]))}</td><td>{case.get('min_content_chars',1):,}</td><td>{passes}/{len(items)}</td></tr>")
        attempt_rows = []
        for item in items:
            label, cls = status(item)
            attempt_rows.append(f"<tr><td><a href=\"../attempts/{slug(item['attempt_id'])}.html\">{esc(item['provider'])} · 第 {item['run']} 次</a></td><td class=\"{cls}\">{label}</td><td>{ms(item['elapsed_ms'])}</td><td>{item['metrics']['marker_hits']}/{item['metrics']['marker_total']}</td><td>{esc(item['error_type'] or '')}</td></tr>")
        markers = "".join(f"<span class=\"tag\">{esc(marker)}</span>" for marker in case["expected_any"])
        fact_rows = "".join(f"<tr><td><code>{esc(fact.get('id'))}</code></td><td>{' / '.join(esc(x) for x in fact.get('alternatives',[]))}</td></tr>" for fact in case.get("required_facts", []))
        evidence = "".join(f"<li><a href=\"{esc(item.get('source'))}\">原始页面</a>：{esc(item.get('supports'))}</li>" for item in case.get("ground_truth_evidence", []))
        forbidden = "".join(f"<span class=\"tag\">{esc(marker)}</span>" for marker in case.get("forbidden_any", []))
        checklist = "".join(f"<li>{esc(x)}</li>" for x in case.get("human_review_checklist", []))
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">标准答案案例 · {esc(case['category'])}</div><h1>{esc(case['name'])}</h1><p class="lead">{esc(case.get('description',''))}</p><div class="meta"><a class="pill" href="{esc(case['url'])}">{esc(case['url'])}</a><span class="pill">最少 {case.get('min_content_chars',1):,} 字符</span></div></div></section><section class="section"><div class="wrap"><h2>完整题目</h2><div class="card"><p>{esc(case.get('task_prompt',''))}</p></div><h2 style="margin-top:32px">参考答案</h2><pre>{esc(case.get('reference_answer',''))}</pre><h3>人工复核清单</h3><ul>{checklist}</ul><h3>冻结事实组</h3><p><b>显著标记：</b> {markers}</p><div class="table-wrap"><table><thead><tr><th>事实组</th><th>可接受表达（组内任一）</th></tr></thead><tbody>{fact_rows}</tbody></table></div><h3>反向标记</h3><p>{forbidden or '<span class="empty">无</span>'}</p><h3>标注证据</h3><ul>{evidence}</ul><p class="sub">{esc(case.get('annotation_notes',''))}</p><div class="callout">机器初判通过条件：命中显著标记、达到最小长度、每个事实组至少命中一个表达，并且不命中反向标记。机器初判不是最终人工结论。</div></div></section><section class="section"><div class="wrap"><h2>全部方法的调用记录</h2><div class="table-wrap"><table><thead><tr><th>调用记录</th><th>状态</th><th>耗时</th><th>标记命中</th><th>失败分类</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table></div></div></section>"""
        write_page(report_dir / "datasets" / f"{slug(case['id'])}.html", layout(title=case["name"], body=body, base="../"))
    gt = dataset.get("ground_truth", {})
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">版本化标准答案（Ground truth）</div><h1>{esc(dataset['name'])}</h1><p class="lead">{esc(dataset.get('description',''))}</p><div class="meta"><span class="pill">{len(dataset['cases'])} 个案例</span><span class="pill">数据结构 v{dataset.get('schema_version')}</span><span class="pill">核验日期 {esc(gt.get('verified_at'))}</span></div></div></section><section class="section"><div class="wrap"><h2>案例类型怎么理解</h2><div class="grid"><div class="card" style="grid-column:span 4"><h3>JavaScript 渲染</h3><p>页面初始 HTML 只有外壳，正文由脚本插入。只返回导航文字不算成功。</p></div><div class="card" style="grid-column:span 4"><h3>PDF 正文</h3><p>输入直接是 PDF URL，必须返回可读论文文字；二进制或下载说明不算成功。</p></div><div class="card" style="grid-column:span 4"><h3>HTTP 重定向</h3><p>服务端先返回 3xx 与 Location，客户端应自动访问目标并返回最终正文；不是返回一个链接。</p></div></div><h2 style="margin-top:32px">标注协议</h2><p>{esc(gt.get('policy'))}</p><div class="callout"><b>状态：</b>{esc(gt.get('review_status'))}。修改标注必须升级数据集版本并重新运行所有方法。</div></div></section><section class="section"><div class="wrap"><h2>案例清单</h2><div class="table-wrap"><table><thead><tr><th>案例</th><th>类别</th><th>URL</th><th>事实组</th><th>最小字符</th><th>机器初判通过（次）</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "datasets" / "index.html", layout(title="评测集", body=body, base="../"))


def _write_benchmark_page(report_dir: Path, dataset: dict[str, Any]) -> None:
    sources = "".join(
        f"<tr><td><b>{esc(item.get('name'))}</b></td><td>{item.get('count','—')}</td><td>{esc(item.get('role'))}</td><td><code>{esc(item.get('commit','—'))}</code></td><td>{esc(item.get('license','—'))}</td></tr>"
        for item in dataset.get("sources", [])
    ) or '<tr><td colspan="5">该数据集未登记外部来源</td></tr>'
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">数据来源与方法审计</div><h1>为什么是这 50 条，而不是随意堆 URL</h1><p class="lead">主榜需要同时覆盖协议边界、七种页面结构和真实浏览器渲染差异；来源、版本、许可和不适用边界都必须可追溯。</p></div></section>
<section class="section"><div class="wrap"><h2>组成</h2><div class="table-wrap"><table><thead><tr><th>来源</th><th>数量</th><th>在本评测中的作用</th><th>固定提交</th><th>许可</th></tr></thead><tbody>{sources}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>与 lightmount-dark 的差异</h2><div class="table-wrap"><table><thead><tr><th>维度</th><th>lightmount-dark</th><th>本项目</th><th>决策</th></tr></thead><tbody>
<tr><td>被测对象</td><td>Moli / Chromium / Lightpanda 浏览器引擎，输出 DOM/可见文本</td><td>托管 URL→可读正文 Web Fetch 服务</td><td>不把浏览器资源指标直接当供应商服务指标</td></tr>
<tr><td>目标集</td><td>100 个热门站点 + 159 个历史失败长尾；另有 12 条渲染质量种子</td><td>10 条核心题 + 28 条 WCXB + 12 条渲染质量种子</td><td>借用 12 条具体内容页；保护站点另做稳定性分榜</td></tr>
<tr><td>正确性</td><td>标题/关键词/最小文本和 challenge、app shell、timeout 分类；原报告明确不比较正文质量</td><td>完整参考答案、必需事实组、反向样板噪声、原始输出与人工复核</td><td>借用失败分类；补足正文完整性标准答案</td></tr>
<tr><td>时延</td><td>同时展示全部行和成功行；失败/timeout 会抬高全部行尾延迟</td><td>主榜只统计内容通过调用，失败耗时仅作诊断</td><td>不能让快速错误页或 timeout 污染内容时延结论</td></tr>
<tr><td>并发</td><td>Moli/Lightpanda 并发 20、Chromium 并发 2，适合引擎压力调查</td><td>供应商按相同案例顺序串行；套餐限流单列</td><td>先对齐输入与节流，再单独做吞吐压测</td></tr>
</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>业界基准如何影响本设计</h2><div class="grid"><div class="card" style="grid-column:span 6"><h3>WCXB：直接采用</h3><p>2,008 页、1,613 个域名、七种结构类型，标准答案包含完整正文、必须出现片段和必须排除的样板噪声。本轮按类型各取 4 条，共 28 条，并固定到具体提交。</p><a href="https://github.com/Murrough-Foley/web-content-extraction-benchmark">WCXB 源仓库 →</a></div><div class="card" style="grid-column:span 6"><h3>LiveWeb-IE：采用标注原则</h3><p>它强调 live URL、稳定事实、自然语言任务和人工验证。本项目采用“页面漂移单独判定”，但不引入其图片/链接/XPath Agent 任务，因为那超出通用 Web Fetch 正文契约。</p><a href="https://arxiv.org/abs/2603.13773">LiveWeb-IE 论文 →</a></div><div class="card" style="grid-column:span 6"><h3>Readability / Dragnet：只作离线抽取参考</h3><p>二者主要在固定 HTML 上评正文抽取；不能覆盖云服务的网络、重定向、JavaScript、PDF、限流和认证。因此不直接替代端到端 live URL 主榜。</p></div><div class="card" style="grid-column:span 6"><h3>长尾失败集：单独分榜</h3><p>登录墙、CAPTCHA、WAF 和历史 4xx/5xx 很有诊断价值，但目标和合规边界不同。混入主榜会把“正文抽取质量”变成“反爬成功率”，结论不可解释。</p></div></div></div></section>"""
    write_page(report_dir / "benchmarks" / "index.html", layout(title="基准来源", body=body, base="../"))


def _write_provider_pages(report_dir: Path, summary: dict[str, Any], by_provider: dict[str, list[dict[str, Any]]]) -> None:
    index_rows = []
    for name, item in summary["providers"].items():
        attempts = by_provider[name]
        method = method_info(item["provider_type"])
        credit_values = [record["provider_metadata"].get("credits_used") for record in attempts if record["provider_metadata"].get("credits_used") is not None]
        credits = sum(float(value) for value in credit_values) if credit_values else None
        credit_label = "—" if credits is None else f"{credits:g} credits"
        index_rows.append(f"<tr><td><a href=\"{slug(name)}.html\"><b>{esc(name)}</b></a></td><td>{esc(item['provider_type'])}</td><td>{pct(item['transport_success_rate'])}</td><td>{pct(item['content_pass_rate'])}</td><td>{pct(item.get('mean_word_f1'))}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td><td>{credit_label}</td></tr>")
        attempt_rows = []
        for record in attempts:
            label, cls = status(record)
            attempt_rows.append(f"<tr><td><a href=\"../attempts/{slug(record['attempt_id'])}.html\">{esc(record['case_name'])} · 第 {record['run']} 次</a></td><td class=\"{cls}\">{label}</td><td>{ms(record['elapsed_ms'])}</td><td>{record['metrics']['content_chars']:,}</td><td>{esc(record['error_type'] or '')}</td></tr>")
        failures = "".join(f"<li><code>{esc(kind)}</code>：{count}</li>" for kind, count in item["failure_types"].items()) or "<li>无</li>"
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">提供方方法 · {esc(item['provider_type'])}</div><h1>{esc(name)}</h1><p class="lead">{esc(method['contract'])}</p><div class="meta"><span class="pill">{esc(method['output'])}</span><span class="pill">{esc(method['leaderboard'])}</span></div></div></section><section class="section"><div class="wrap"><h2>公平性配置</h2><div class="attempt-grid"><div class="kv"><b>接口</b><code>{esc(method['endpoint'])}</code></div><div class="kv"><b>输出契约</b>{esc(method['output'])}</div><div class="kv"><b>新鲜度 / 等待</b>{esc(method['freshness'])}</div><div class="kv"><b>结果分组</b>{esc(method['leaderboard'])}</div></div></div></section><section class="section"><div class="wrap"><div class="grid"><div class="card kpi"><div class="eyebrow">请求成功率</div><div class="n">{pct(item['transport_success_rate'])}</div></div><div class="card kpi"><div class="eyebrow">机器内容初判</div><div class="n">{pct(item['content_pass_rate'])}</div></div><div class="card kpi"><div class="eyebrow">事实覆盖率</div><div class="n">{pct(item.get('mean_fact_coverage'))}</div></div><div class="card kpi"><div class="eyebrow">有效 P95</div><div class="n">{ms(item['p95_ms'])}</div></div></div><h3>失败分布</h3><ul>{failures}</ul><p>超时越界：<b>{item['deadline_overrun_count']}</b> · 供应商响应暴露用量：<b>{credit_label}</b></p></div></section><section class="section"><div class="wrap"><h2>逐次结果</h2><div class="table-wrap"><table><thead><tr><th>案例 / 次数</th><th>状态</th><th>耗时</th><th>字符</th><th>失败分类</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table></div></div></section>"""
        write_page(report_dir / "providers" / f"{slug(name)}.html", layout(title=f"提供方：{name}", body=body, base="../"))
    index_body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">提供方注册表</div><h1>Web Fetch 提供方 / 方法</h1><p class="lead">提供方是调用实现，和评测集、指标定义相互独立。同一数据集可重复运行不同提供方。</p></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>方法</th><th>类型</th><th>请求成功率</th><th>机器内容初判通过率</th><th>WCXB 词级 F1</th><th>有效 P50</th><th>有效 P95</th><th>响应暴露用量</th></tr></thead><tbody>{''.join(index_rows)}</tbody></table></div></div></section>"""
    write_page(report_dir / "providers" / "index.html", layout(title="提供方", body=index_body, base="../"))


def _write_metric_page(report_dir: Path) -> None:
    cards = "".join(f"<div class=\"card\" style=\"grid-column:span 6\"><h3><code>{esc(name)}</code></h3><p class=\"sub\">{esc(description)}</p></div>" for name, description in METRIC_DEFINITIONS.items())
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">固定指标契约</div><h1>评测指标定义</h1><p class="lead">指标在运行前固定。HTTP 成功不等于正文成功；主榜速度也不会纳入快速返回空壳、错误页或不完整正文的调用。</p></div></section><section class="section"><div class="wrap"><div class="grid">{cards}</div><h3>聚合规则</h3><div class="callout"><b>主榜 P50/P95：</b>只统计请求成功且机器内容初判通过的有效调用。另在 summary.json 保留 transport_p50_ms/transport_p95_ms 与全部调用 P95，供网络和超时诊断；机器内容初判通过率的分母包含所有调用。</div></div></section>"""
    write_page(report_dir / "metrics" / "index.html", layout(title="指标定义", body=body, base="../"))


def _write_environment_page(report_dir: Path, manifest: dict[str, Any]) -> None:
    env = manifest["environment"]
    params = manifest["parameters"]
    providers = "".join(f"<tr><td>{esc(item['name'])}</td><td><code>{esc(item['type'])}</code></td><td>{esc(method_info(item['type'])['freshness'])}</td><td>{esc(str(item.get('min_interval_seconds') or 0))} 秒</td></tr>" for item in manifest["providers"])
    limit_path = Path(__file__).resolve().parent.parent / "research" / "cloudflare-free-limit-2026-09-01.json"
    limit_note = ""
    if limit_path.exists():
        limit = json.loads(limit_path.read_text(encoding="utf-8"))
        limit_note = f'<div class="callout" style="margin-top:18px"><b>Cloudflare 免费账户长周期配额：</b>响应完成后冷却 {limit["cooldown_after_response_seconds"]} 秒仍在第 {limit["records_before_stop"]} 条已记录请求内出现 HTTP 429；<code>Retry-After: {limit["retry_after_seconds"]}</code>（约 {limit["retry_after_seconds"]/3600:.1f} 小时）。因此不能继续靠增加毫秒级延迟解决。主榜使用此前完整无 429 的 50 条运行，本次更新账号重测只作为吞吐边界证据。</div>'
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">执行环境</div><h1>评测环境</h1><p class="lead">环境信息来自运行时 manifest；凭据值不会写入报告。网络路径、服务负载和源站变化会影响绝对耗时，因此本轮只代表该时间与出口的观测。</p></div></section><section class="section"><div class="wrap"><div class="attempt-grid"><div class="kv"><b>开始时间（UTC）</b>{esc(manifest.get('started_at'))}</div><div class="kv"><b>完成时间（UTC）</b>{esc(manifest.get('completed_at'))}</div><div class="kv"><b>Python</b>{esc(env.get('python'))}</div><div class="kv"><b>平台</b>{esc(env.get('platform'))}</div><div class="kv"><b>主机</b>{esc(env.get('hostname'))}</div><div class="kv"><b>每案例运行次数</b>{params.get('runs')}</div><div class="kv"><b>Socket 超时</b>{params.get('timeout')} 秒</div><div class="kv"><b>全局调用间隔</b>{params.get('delay')} 秒</div></div><h3>运行方法</h3><div class="table-wrap"><table><thead><tr><th>名称</th><th>适配器类型</th><th>新鲜度 / 等待设置</th><th>响应完成后的冷却时间</th></tr></thead><tbody>{providers}</tbody></table></div>{limit_note}<div class="callout" style="margin-top:18px">单轮、小样本适合功能定位，不足以给出供应商 SLA。正式采购结论应至少多时段重复 3–5 次，并在目标客户地域各设一个 runner。</div></div></section><section class="section"><div class="wrap"><h2>开源基线固定版本</h2><div class="table-wrap"><table><thead><tr><th>方法</th><th>固定版本</th><th>真实入口</th></tr></thead><tbody><tr><td>OpenClaw</td><td><code>12c4bdf7482f24c85b011e9d858fdec3c245101c</code><br>Node 24.15.0</td><td><code>createWebFetchTool</code></td></tr><tr><td>DeepSeek Harness</td><td><code>dd6322d604e00eec1ba5e0c8541159906a21094a</code></td><td><code>ctx.tools.execute(web_fetch)</code></td></tr><tr><td>LangChain Community</td><td><code>0.3.30</code></td><td><code>WebBaseLoader.load()</code></td></tr><tr><td>Crawl4AI</td><td><code>0.9.3</code><br><code>862f6bccb9c063f49b9d42701baa0eea17a4993f</code></td><td><code>AsyncWebCrawler.arun()</code></td></tr></tbody></table></div></div></section>"""
    if manifest.get("derived_from"):
        body += f"<section class=\"section\"><div class=\"wrap\"><h2>限流重测合并说明</h2><p>本报告是派生运行：原始六方结果完整保留，只有被账户限流污染的提供方记录由独立节流重测替换。基础运行、重测目录和时间保存在 manifest，不能删除原始运行来伪装成一次无事故执行。</p><details><summary>展开派生运行元数据</summary><pre>{esc(json.dumps(manifest['derived_from'], ensure_ascii=False, indent=2))}</pre></details></div></section>"
    write_page(report_dir / "environment" / "index.html", layout(title="评测环境", body=body, base="../"))


def _write_methodology_page(report_dir: Path, manifest: dict[str, Any]) -> None:
    methods = "".join(f"<tr><td><b>{esc(item['name'])}</b></td><td><code>{esc(method_info(item['type'])['endpoint'])}</code></td><td>{esc(method_info(item['type'])['output'])}</td><td>{esc(method_info(item['type'])['freshness'])}</td><td>{esc(method_info(item['type'])['leaderboard'])}</td></tr>" for item in manifest["providers"])
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">预先登记的评测方法</div><h1>评测方法与公平性</h1><p class="lead">主问题是“给定公开 URL，能否稳定返回正确、完整、可供 Agent 使用的正文”。搜索召回、登录交互、反机器人解锁和结构化 AI 抽取不混入主分数。</p></div></section>
<section class="section"><div class="wrap"><h2>执行顺序</h2><ol class="steps"><li>冻结数据集 v3 标准答案、参考正文与人工检查清单，再运行任何供应商。</li><li>每个方法按相同 URL、超时和运行次数执行；保存完整响应或异常。</li><li>先判请求，再按显著标记、最小长度、事实组和反向标记做机器内容初判。</li><li>主榜耗时只取“请求成功且机器初判通过”的有效内容调用；失败耗时另存用于诊断。</li><li>每条调用页并排展示题目、标准答案、供应商输出和机器判定，由人工完成最终复核。</li><li>HTML 只由 JSONL 与原始结果生成；不手工填写供应商分数。</li></ol></div></section>
<section class="section"><div class="wrap"><h2>方法契约</h2><div class="table-wrap"><table><thead><tr><th>方法</th><th>实际 endpoint</th><th>输出</th><th>新鲜度/等待</th><th>分组</th></tr></thead><tbody>{methods}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>Cloudflare 为什么只测 <code>/markdown</code></h2><div class="grid"><div class="card" style="grid-column:span 7"><h3>本轮参评：<code>/markdown</code></h3><p>它把浏览器加载后的页面转为 Markdown，并去除样式和脚本；与 Lexmount <code>main_text</code>、Firecrawl Markdown、Tavily Extract、Exa text 的“可读正文”目标最接近。</p><a href="https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/">官方 Markdown 文档 →</a></div><div class="card" style="grid-column:span 5"><h3>不参评：<code>/content</code></h3><p>它返回执行 JavaScript 后的完整 HTML（包含 head），适合检查 DOM 渲染，不是清洗后的 Agent 正文。把它并列计分会同时改变输出契约和噪声水平。</p><a href="https://developers.cloudflare.com/browser-run/quick-actions/content-endpoint/">官方 Content 文档 →</a></div></div></div></section>
<section class="section"><div class="wrap"><h2>当前内容裁判到底是什么</h2><p><b>第一层是确定性机器初判：</b>先确认请求成功，再要求输出命中显著标记、达到最小长度、覆盖每个预登记事实组，并且不含错误页或挑战页标记。字符串比较不调用 LLM，任何人用相同结果都能复算。</p><p><b>第二层是人工最终复核：</b>调用页同时展示完整题目、参考答案、事实组、供应商输出和机器命中。复核者按正确性、完整性、可读性重新判断，可保存到浏览器或导出 JSON。机器初判可能因同义改写产生假阴性，也可能因关键词散落产生假阳性，所以不能直接冒充最终结论。</p><p><b>LLM judge 当前不进入主榜：</b>若后续启用，只作为盲化的二级辅助审阅，必须固定模型、提示、量表和 JSON 结构，并由人工抽检。LLM 不能反向修改冻结标准答案。</p><div class="callout">因此本报告证明的是：评测过程可重算、每个判断可下钻、人工可以覆盖机器初判；不是声称单次关键词规则天然等同于最终质量。</div></div></section>"""
    write_page(report_dir / "methodology" / "index.html", layout(title="评测方法", body=body, base="../"))


def _write_analysis_page(report_dir: Path, summary: dict[str, Any], by_case: dict[str, list[dict[str, Any]]]) -> None:
    comparable = [(name, item) for name, item in summary["providers"].items() if method_info(item["provider_type"])["leaderboard"] == "可读正文" and item["failure_types"].get("authentication") != item["attempts"]]
    ranked = sorted(comparable, key=lambda pair: (-pair[1]["content_pass_rate"], -pair[1].get("mean_fact_coverage", 0), pair[1]["p50_ms"] or float("inf")))
    rows = "".join(f"<tr><td>{index}</td><td><a href=\"../providers/{slug(name)}.html\"><b>{esc(name)}</b></a></td><td>{pct(item['transport_success_rate'])}</td><td>{pct(item['content_pass_rate'])}</td><td>{pct(item.get('mean_fact_coverage'))}</td><td>{pct(item.get('mean_word_f1'))}</td><td>{ms(item['p50_ms'])}</td><td>{ms(item['p95_ms'])}</td></tr>" for index, (name, item) in enumerate(ranked, 1))
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
            forbidden = ", ".join(item["metrics"].get("forbidden_hits", []))
            if item.get("error_message"):
                reason = item["error_message"]
            elif misses and forbidden:
                reason = f"缺失事实组：{misses}；仍含样板/错误噪声：{forbidden}"
            elif misses:
                reason = f"缺失事实组：{misses}"
            elif forbidden:
                reason = f"正文已取回，但仍含样板/错误噪声：{forbidden}"
            elif not item["metrics"].get("length_pass", True):
                reason = "输出短于该案例的最低正文长度"
            else:
                reason = "确定性内容规则未通过"
            category = item.get("error_type") or "content_validation"
            failure_rows.append(f"<tr data-provider=\"{esc(item['provider'].lower())}\" data-category=\"{esc(category.lower())}\"><td><a href=\"../attempts/{slug(item['attempt_id'])}.html\">{esc(item['provider'])}</a></td><td>{esc(item['case_name'])}</td><td><code>{esc(category)}</code></td><td>{esc(reason)}</td></tr>")
    auth_failures = [r for items in by_case.values() for r in items if r.get("error_type") == "authentication"]
    auth_note = f"<div class=\"callout failure\"><b>认证失败也是实测结果：</b>{len(auth_failures)} 次。它只说明当前环境凭据不可用，不证明供应商产品能力差；修复凭据后必须重跑，不能人工补分。</div>" if auth_failures else ""
    rate_limited = [r for items in by_case.values() for r in items if r.get("status_code") == 429]
    rate_note = f"<div class=\"callout\"><b>账户限流污染本轮可用性：</b>{len(rate_limited)} 次 HTTP 429。当前报告按真实请求计为失败，但该比例不能直接外推为供应商内容能力；应在额度充足或限额重置后创建新 run 重测，不能删除本轮记录。</div>" if rate_limited else ""
    provider_options = "".join(f'<option value="{esc(name.lower())}">{esc(name)}</option>' for name in sorted({item["provider"] for items in by_case.values() for item in items}))
    failure_categories = sorted({item.get("error_type") or "content_validation" for items in by_case.values() for item in items if not (item["ok"] and item["metrics"]["content_pass"])})
    category_options = "".join(f'<option value="{esc(name.lower())}">{esc(name)}</option>' for name in failure_categories)
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">结果分析</div><h1>评测结果分析</h1><p class="lead">排序优先机器内容初判通过率，其次事实覆盖率，再其次有效内容 P50。这样可以避免“很快返回错误页”胜出。人工复核不设孤立队列：点击任一案例即可并排查看标准答案与实际输出并提交复核。</p></div></section><section class="section"><div class="wrap">{auth_note}{rate_note}<h2>主榜（可读正文契约）</h2><div class="table-wrap"><table><thead><tr><th>排名</th><th>方法</th><th>请求成功率</th><th>机器内容初判通过率</th><th>事实覆盖率</th><th>有效 P50</th><th>有效 P95</th></tr></thead><tbody>{rows}</tbody></table></div><p class="tiny">最终结论仍需人工复核；主榜耗时只统计机器内容初判通过的有效调用。</p></div></section><section class="section"><div class="wrap"><h2>失败根因明细</h2><div class="filters"><select id="failure-provider" onchange="filterFailures()"><option value="">全部方法</option>{provider_options}</select><select id="failure-category" onchange="filterFailures()"><option value="">全部失败分类</option>{category_options}</select><input id="failure-query" type="search" placeholder="搜索案例或原因" oninput="filterFailures()"><div class="kv" id="failure-count">显示 {len(failure_rows)} 条</div></div><div class="table-wrap"><table><thead><tr><th>方法</th><th>案例</th><th>分类</th><th>可操作原因</th></tr></thead><tbody id="failure-rows">{''.join(failure_rows)}</tbody></table></div></div></section><section class="section"><div class="wrap"><h2>逐案例复核入口</h2><p class="sub">点击案例，再进入具体方法调用；详情页同时展示完整题目、冻结标准答案、机器事实组和方法实际输出。</p><div class="table-wrap"><table><thead><tr><th>案例</th><th>方法状态与事实覆盖率</th></tr></thead><tbody>{''.join(case_rows)}</tbody></table></div></div></section><section class="section"><div class="wrap"><h2>结论边界</h2><ul><li>内容正确性由冻结标注和人工复核共同决定，输出越长不等于越完整；字符数只作诊断。</li><li>一次运行用于发现功能缺口，不代表长期稳定性或 SLA。</li><li>供应商公开 credits 与实际美元成本不是同一概念；没有公开价时不做价格胜负断言。</li><li>登录、保护站点与搜索召回需要独立数据集，不能从本轮外推。</li></ul></div></section>"""
    drift_rows = []
    for case_id, items in sorted(by_case.items()):
        all_transport_failed = all(not item["ok"] for item in items)
        no_provider_hit_fact = all(item["metrics"].get("fact_coverage", 0) == 0 for item in items)
        if all_transport_failed or no_provider_hit_fact:
            signal = "全部方法请求失败" if all_transport_failed else "全部方法事实覆盖率为 0"
            drift_rows.append(f"<tr><td><a href=\"../datasets/{slug(case_id)}.html\">{esc(items[0]['case_name'])}</a></td><td>{signal}</td><td>人工打开源页；若内容已变选择“标准答案/页面已漂移”，若题目无效选择“案例无效”</td></tr>")
    if drift_rows:
        body += f"<section class=\"section\"><div class=\"wrap\"><h2>标准答案漂移候选队列</h2><p>这些信号不能自动证明供应商失败，也不能自动证明题目失效；它们只提高 HIL 复核优先级。</p><div class=\"table-wrap\"><table><thead><tr><th>案例</th><th>触发信号</th><th>复核动作</th></tr></thead><tbody>{''.join(drift_rows)}</tbody></table></div></div></section>"
    segment_names = (
        ("核心能力题", lambda value: not value.startswith(("wcxb-", "lm-"))),
        ("WCXB 七类页面", lambda value: value.startswith("wcxb-")),
        ("lightmount-dark 渲染题", lambda value: value.startswith("lm-")),
    )
    segment_rows = []
    provider_names = sorted({item["provider"] for items in by_case.values() for item in items})
    for provider in provider_names:
        for segment_name, predicate in segment_names:
            selected = [item for case_id, items in by_case.items() if predicate(case_id) for item in items if item["provider"] == provider]
            passed = sum(item["ok"] and item["metrics"]["content_pass"] for item in selected)
            transported = sum(item["ok"] for item in selected)
            word_scores = [item["metrics"].get("word_f1") for item in selected if item["metrics"].get("word_f1") is not None]
            mean_word = round(statistics.mean(word_scores), 4) if word_scores else None
            segment_rows.append(f"<tr><td>{esc(provider)}</td><td>{segment_name}</td><td>{transported}/{len(selected)}</td><td>{passed}/{len(selected)}</td><td>{pct(mean_word)}</td></tr>")
    body += f"<section class=\"section\"><div class=\"wrap\"><h2>按数据来源拆分结果</h2><p>核心题测协议与特殊能力；WCXB 重点测正文完整性和样板噪声；lightmount-dark 子集测渲染与文档模板。三者不能只看一个总分。</p><div class=\"table-wrap\"><table><thead><tr><th>方法</th><th>来源分段</th><th>请求成功</th><th>机器内容初判通过</th><th>词级 F1</th></tr></thead><tbody>{''.join(segment_rows)}</tbody></table></div></div></section>"
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
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">复现操作手册</div><h1>详细执行过程与复现</h1><p class="lead">一个脚本会验证配置、运行固定数据集和供应商、保存所有原始结果、生成分页 HTML 并检查本地链接。脚本能复现评测过程和报告结构；实时网站与云服务会变化，因此不能保证未来逐字节复现本次数值。</p></div></section><section class="section"><div class="wrap"><h2>新环境完整步骤</h2><pre>{esc(command)}</pre><h3>两种复现语义</h3><div class="grid"><div class="card" style="grid-column:span 6"><h3>重放已保存结果</h3><pre>python3 -m browser_harness_eval report runs/&lt;run-id&gt;</pre><p class="sub">不发网络请求；从 JSONL 和原始结果重新生成相同指标与 HTML。</p></div><div class="card" style="grid-column:span 6"><h3>重新实测</h3><pre>./scripts/reproduce_report.sh runs/new-run</pre><p class="sub">重新调用真实服务；页面内容、时延、credits 与供应商行为可能变化。</p></div></div></div></section><section class="section"><div class="wrap"><h2>失败定位顺序</h2><ol><li>看提供方页，区分请求失败与机器内容初判失败。</li><li>下钻调用详情页，检查 HTTP 状态、error_type、事实组未命中、反向标记和供应商元数据。</li><li>用 SHA-256 对照完整输出，避免网页预览截断造成误判。</li><li>认证失败先运行 token 官方验证；内容失败再对照标准答案源页面。</li><li>人工复核并导出 JSON；修复后创建新 run 目录，禁止覆盖原结果。</li></ol></div></section>"""
    write_page(report_dir / "execution" / "index.html", layout(title="复现步骤", body=body, base="../"))


def _write_market_page(report_dir: Path, by_provider: dict[str, list[dict[str, Any]]]) -> None:
    direct = [
        ("CowAgent 内置 WebFetch", "仓库内 HTTP + 可读文本/文档解析", "本轮已实测", "WebFetch.execute({url})", "https://github.com/zhayujie/CowAgent/blob/main/agent/tools/web_fetch/web_fetch.py"),
        ("Lexmount WebFetch Global", "托管 URL → Markdown/元数据", "本轮已实测", "POST /v1/extract", "https://browser.lexmount.com/docs/webfetch"),
        ("Firecrawl Scrape", "托管/开源 URL → Markdown", "本轮已实测", "POST /v2/scrape", "https://docs.firecrawl.dev/features/scrape"),
        ("Tavily Extract", "已知 URL → Markdown/纯文本", "本轮已实测", "POST /extract", "https://docs.tavily.com/documentation/api-reference/endpoint/extract"),
        ("Exa Contents", "已知 URL → 正文/摘要/高亮", "本轮已实测", "POST /contents", "https://exa.ai/docs/reference/contents-retrieval"),
        ("Cloudflare Browser Rendering", "浏览器渲染 URL → Markdown", "本轮只测 /markdown", "POST /browser-rendering/markdown", "https://developers.cloudflare.com/browser-run/quick-actions/markdown-endpoint/"),
        ("OpenClaw 内置 web_fetch", "HTTP GET + Readability；本轮关闭 Firecrawl 回退；无 JS", "本轮已实测", "web_fetch({url, extractMode})", "https://github.com/openclaw/openclaw/blob/main/docs/tools/web-fetch.md"),
        ("DeepSeek Harness web_fetch", "匿名 HTTP provider + Turndown；同源跳转；无 PDF", "本轮已实测", "ctx.tools.execute(web_fetch)", "https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/web"),
        ("LangChain WebBaseLoader", "HTTP + BeautifulSoup 网页文本加载器", "本轮已实测", "WebBaseLoader(url).load()", "https://python.langchain.com/docs/integrations/document_loaders/web_base/"),
        ("Crawl4AI", "自托管浏览器渲染 + Markdown；返回媒体和链接元数据", "本轮已实测", "AsyncWebCrawler.arun(url)", "https://docs.crawl4ai.com/api/crawl-result/"),
    ]
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
    }
    for name, attempts in sorted(by_provider.items()):
        credits = [item["provider_metadata"].get("credits_used") for item in attempts if item["provider_metadata"].get("credits_used") is not None]
        usage = "—" if not credits else f"{sum(float(value) for value in credits):g} credits"
        observed_rows.append(f"<tr><td><b>{esc(name)}</b></td><td>{len(attempts)} 次</td><td>{esc(usage)}</td><td>{esc(pricing.get(name,'查看供应商账户账单'))}</td></tr>")
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">供应商与实现边界</div><h1>真正可对齐的 Web Fetch 方法</h1><p class="lead">这里只列接受一个已知 URL、直接返回可持久化网页正文的方法。Web Search、浏览器交互、模型 URL Context、反机器人解锁和仅有产品宣传但未确认接口的候选项不进入这张表。</p></div></section>
<section class="section"><div class="wrap"><h2>可用方法与著名自建基线</h2><div class="table-wrap"><table><thead><tr><th>提供方 / 实现</th><th>实际能力</th><th>证据状态</th><th>接口面</th></tr></thead><tbody>{direct_rows}</tbody></table></div><div class="callout" style="margin-top:18px"><b>国内供给结论：</b>在本轮核验范围内，Lexmount 是已确认可直接按 URL 返回正文并完成实测的国内供应商。博查、百度、腾讯主要公开的是 Web Search；阿里和 Google 的相关能力偏模型工具或 URL Context，输入输出与独立 Web Fetch API 不同，所以不塞进供应商主表。</div><div class="callout" style="margin-top:18px"><b>Lexmount 地域说明：</b>本轮只调用 Global 的 <code>api.lexmount.com</code>。文档同时出现中国区域 API/控制台只能证明存在两套区域入口，不能据此断言底层只有两个物理服务节点。</div></div></section>
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
    groups = [
        ("Firecrawl", {"firecrawl-sdk", "firecrawl-v1", "firecrawl-v2"}, "托管与开源正文抓取", "已实测"),
        ("Tavily Extract", {"tavily-extract", "tavily-sdk-extract"}, "已知 URL 正文抽取", "已实测"),
        ("Exa Contents", {"exa-contents"}, "已知 URL 内容读取", "已实测"),
        ("Cloudflare Markdown", {"cloudflare-markdown"}, "浏览器渲染后转 Markdown", "已实测"),
        ("LangChain URL loaders", {"langchain-web-base-loader", "langchain-unstructured-url-loader"}, "库内 HTTP/解析加载器", "已实测 WebBaseLoader"),
        ("Crawl4AI", {"crawl4ai"}, "自托管浏览器抓取与 Markdown", "已实测"),
        ("Trafilatura", {"trafilatura"}, "开源正文抽取库", "可执行开源候选"),
        ("Bright Data", {"brightdata-request"}, "托管解锁/抓取 API", "需账号后单独数据集"),
        ("ZenRows", {"zenrows"}, "托管抓取 API", "需账号后候选"),
        ("ScrapingBee", {"scrapingbee", "scrapingbee-sdk"}, "托管抓取 API", "需账号后候选"),
        ("ScraperAPI", {"scraperapi"}, "托管抓取 API", "需账号后候选"),
        ("Zyte", {"zyte-sdk"}, "托管抓取 API", "需账号后候选"),
        ("Scrapfly", {"scrapfly"}, "托管抓取 API", "需账号后候选"),
        ("Oxylabs", {"oxylabs"}, "托管抓取 API", "需账号后候选"),
    ]
    supplier_rows = []
    for name, signatures, capability, status_text in groups:
        repos = []
        for item in data["included"]:
            matched = {match["signature"] for match in item.get("matches", [])}
            if matched & signatures:
                repos.append(item)
        examples = "、".join(f'<a href="{esc(item["url"])}">{esc(item["repo"])}</a>' for item in sorted(repos, key=lambda x: -x["stars"])[:3]) or "—"
        supplier_rows.append(f"<tr><td><b>{esc(name)}</b></td><td>{len(repos)}</td><td>{esc(capability)}</td><td>{examples}</td><td>{esc(status_text)}</td></tr>")
    named = {item["repo"]: item for item in data["included"]}
    openclaw = named.get("openclaw/openclaw", {})
    dsh = named.get("deepseek-ai/deepseek-harness", {})
    own_rows = [
        ("OpenClaw", openclaw, "自建 HTTP GET + Readability；默认不执行 JS；抽取失败可选 Firecrawl fallback；15 分钟缓存。", "静态正文基线；JS/PDF 需其它工具或 fallback", "https://github.com/openclaw/openclaw/blob/main/docs/tools/web-fetch.md"),
        ("DeepSeek Harness", dsh, "自建匿名 HTTP(S) provider；公共地址校验、连接固定、同源重定向；Turndown 转 Markdown。", "当前明确拒绝 PDF 与跨源重定向", "https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/web"),
    ]
    own_html = "".join(f'<tr><td><a href="{esc(url)}"><b>{esc(name)}</b></a></td><td>{int(item.get("stars",0)):,}</td><td>{esc(impl)}</td><td>{esc(boundary)}</td><td>{esc(item.get("default_branch_head_at") or "—")}</td></tr>' for name, item, impl, boundary, url in own_rows)
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">GitHub 默认分支证据</div><h1>著名 Agent 如何实现 Web Fetch</h1><p class="lead">原始仓库检索的目的不是堆名单，而是识别可比较方案：第三方方案按供应商去重；自建实现只保留 OpenClaw 和 DeepSeek Harness 两个高影响力基线。</p><div class="meta"><span class="pill">{len(data['signatures'])} 个检索签名</span><span class="pill">检查 {data['unique_repositories_examined']:,} 个去重仓库</span><span class="pill">{len(data['included'])} 个满足 stars 与活跃度条件</span><span class="pill">stars ≥ {data['minimum_stars']:,}</span></div></div></section>
<section class="section"><div class="wrap"><h2>去重后的第三方方案</h2><p class="sub">采用仓库数是本次签名检索样本内的去重计数，不代表全 GitHub 市占率；示例按 stars 排序。</p><div class="table-wrap"><table><thead><tr><th>第三方方案</th><th>命中仓库数</th><th>与 Web Fetch 的关系</th><th>高 stars 示例</th><th>本项目状态</th></tr></thead><tbody>{''.join(supplier_rows)}</tbody></table></div></div></section>
<section class="section"><div class="wrap"><h2>两个著名自建实现</h2><div class="table-wrap"><table><thead><tr><th>仓库</th><th>星标数</th><th>源码核验的实现</th><th>已知边界</th><th>默认分支最新提交</th></tr></thead><tbody>{own_html}</tbody></table></div><div class="callout" style="margin-top:18px">两种实现均已通过仓库原生入口运行同一 50 条评测集并进入“结果分析”主榜。OpenClaw 关闭 Firecrawl 回退、缓存设为 0；DeepSeek Harness 使用默认匿名 HTTP provider。</div></div></section>
<section class="section"><div class="wrap"><h2>证据与完备性边界</h2><p>检索覆盖供应商 endpoint、SDK 方法、通用工具类、URL Context 和开源正文抽取库，并按仓库去重、核验默认分支 HEAD 时间。<a href="evidence.json">下载完整 evidence.json</a> 查看每条命中、文件和查询。</p><div class="callout">“全部找出”在工程上指：列出的 {len(data['signatures'])} 个检索签名、每签名前 {data['per_signature_cap']} 条 GitHub Code Search 结果中，全部满足 stars 与最近 7 天默认分支活跃条件的仓库；不声称对全 GitHub 数学完备。</div></div></section>"""
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
        readiness = "适配器已实现" if item["implemented"] else "适配器待实现"
        rows.append(
            f"<tr><td><b>{esc(item['provider'])}</b><br><span class=\"tiny\">{esc(item['priority'])} · {readiness}</span></td>"
            f"<td><a href=\"{esc(item['registration_url'])}\">注册 / 登录</a><br><a href=\"{esc(item['key_url'])}\">获取凭据</a></td>"
            f"<td>{variables}</td><td>{esc(item['steps'])}</td><td>{esc(item['free_note'])}</td></tr>"
        )
    body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">账号准备</div><h1>账号注册与凭据清单</h1><p class="lead">按能力类别列出官方注册入口、API key 获取位置和需要填写的环境变量。报告不保存实际凭据。</p><div class="callout"><b>当前主评测：</b>Lexmount Global、Firecrawl、Tavily Extract、Exa Contents 与 Cloudflare <code>/markdown</code>。<code>/content</code> 不再作为参评方法；搜索 API 与模型绑定工具使用独立数据集。</div></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>提供方</th><th>官方入口</th><th>环境变量</th><th>取得方式</th><th>免费/准入说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><h3>终端打印同一清单</h3><pre>python3 -m browser_harness_eval accounts --env-file ~/.env</pre></div></section>"""
    write_page(report_dir / "accounts" / "index.html", layout(title="账号准备", body=body, base="../"))


def _write_capabilities_page(report_dir: Path) -> None:
    source = Path(__file__).resolve().parent.parent / "research" / "file-media-capabilities.json"
    if not source.exists():
        body = '<section class="hero"><div class="wrap"><h1>文件与媒体能力结果尚未生成</h1><p class="lead">运行独立能力集后再生成本页；它不与网页正文主榜混算。</p></div></section>'
        write_page(report_dir / "capabilities" / "index.html", layout(title="文件与媒体", body=body, base="../"))
        return
    data = json.loads(source.read_text(encoding="utf-8"))
    target = report_dir / "capabilities"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target / "evidence.json")
    columns = data["columns"]
    head = "".join(f"<th>{esc(item['label'])}</th>" for item in columns)
    rows = []
    for method in data["methods"]:
        cells = []
        for column in columns:
            value = method["capabilities"].get(column["id"], {})
            state = value.get("state", "未知")
            cls = "good" if state == "支持" else ("warn" if state in {"部分支持", "另有接口"} else "bad")
            cells.append(f'<td><span class="{cls}">{esc(state)}</span><br><span class="tiny">{esc(value.get("detail", "—"))}</span></td>')
        rows.append(f'<tr><td><b>{esc(method["name"])}</b><br><span class="tiny">{esc(method.get("tested_surface", ""))}</span></td>{"".join(cells)}</tr>')
    sources = "".join(f'<li><a href="{esc(item["url"])}">{esc(item["label"])}</a>：{esc(item["claim"])}</li>' for item in data.get("sources", []))
    examples = "".join(f'<details><summary>{esc(item["method"])} · {esc(item["case"])} · {esc(item["judgement"])}</summary><div style="padding:16px"><p>{esc(item["reason"])}</p><pre>{esc(item["output"])}</pre></div></details>' for item in data.get("examples", []))
    body = f'''<section class="hero"><div class="wrap"><div class="eyebrow">独立能力评测</div><h1>文件、图片与视频：Web Fetch 到底返回什么</h1><p class="lead">“支持”拆成三件不同的事：①抽取为可读文字；②返回资源 URL/元数据；③下载原始二进制。正文主榜只测第一项，不能因为 Markdown 中出现一张图的链接就宣称已抓取图片，更不能因为接口能返回 PDF 文件就宣称已理解 PDF 正文。</p><div class="meta"><span class="pill">能力集 {esc(data.get("dataset_id"))}</span><span class="pill">{len(data["methods"])} 种方法</span><span class="pill">核验 {esc(data.get("verified_at"))}</span></div></div></section>
<section class="section"><div class="wrap"><h2>先给结论</h2><div class="grid"><div class="card" style="grid-column:span 4"><h3>网页与文档</h3><p>TXT 通常能直接作为正文；PDF、DOCX 需要文档解析器。把二进制按 UTF-8 解码或只返回下载链接，都不算正文抽取成功。</p></div><div class="card" style="grid-column:span 4"><h3>图片与视频</h3><p>主流 Web Fetch 通常不把媒体二进制塞进正文。常见结果是保留图片/视频 URL 或元数据；视觉理解、OCR、视频转写属于另一个工具面。</p></div><div class="card" style="grid-column:span 4"><h3>Agent 的稳定契约</h3><p>正文放 <code>content</code>；资源放 <code>resources[]</code>；需要二进制时再调用受限的 <code>download_resource</code>，避免 token、带宽和安全风险。</p></div></div></div></section>
<section class="section"><div class="wrap"><h2>方法能力矩阵</h2><p class="sub">“另有接口”表示当前参评的正文接口本身不返回该能力，但同一产品提供独立 endpoint；不能把两个 endpoint 的能力合并成一次 Web Fetch 成功。</p><div class="table-wrap"><table><thead><tr><th>方法 / 本轮接口</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div><p><a href="evidence.json">下载结构化能力证据 JSON</a></p></div></section>
<section class="section"><div class="wrap"><h2>推荐输出模型</h2><pre>{{
  "content": "抽取后的正文；图片/视频不转成乱码",
  "content_type": "text/html | application/pdf | ...",
  "resources": [{{"kind":"image|video|audio|document", "url":"...", "mime_type":"..."}}],
  "attachments": [{{"kind":"download", "url":"受控下载地址", "sha256":"...", "bytes":123}}]
}}</pre><div class="callout">默认只返回正文与资源引用。只有调用方明确请求、大小与 MIME 在白名单内、下载 URL 通过 SSRF/重定向检查时，才获取原始二进制；下载不应偷偷计入正文抓取耗时。</div></div></section>
<section class="section"><div class="wrap"><h2>真实输出与判定示例</h2><p class="sub">以下均来自持久化调用输出或供应商元数据；折叠展示结论、原始片段和为什么这样判。</p>{examples}</div></section>
<section class="section"><div class="wrap"><h2>官方与源码证据</h2><ul>{sources}</ul></div></section>'''
    write_page(target / "index.html", layout(title="文件与媒体能力", body=body, base="../"))


def _write_attempt_pages(report_dir: Path, dataset: dict[str, Any], records: list[dict[str, Any]]) -> None:
    cases = {item["id"]: item for item in dataset["cases"]}
    for record in records:
        case = cases[record["case_id"]]
        label, cls = status(record)
        artifact = report_dir / record["artifact_path"]
        content = artifact.read_text(encoding="utf-8", errors="replace")
        # HTML previews are presentation copies. Strip line-end spaces so generated
        # reports stay reviewable with git diff --check; the raw artifact remains exact.
        display = "\n".join(line.rstrip() for line in content[:200_000].splitlines())
        truncation = "<p class=\"warn\">页面预览截断至 200,000 字符；原始 artifact 保留完整内容。</p>" if len(content) > len(display) else ""
        matched = "".join(f"<span class=\"tag\">{esc(x)}</span>" for x in record["metrics"]["matched_markers"]) or "<span class=\"empty\">无</span>"
        expected = "".join(f"<span class=\"tag\">{esc(x)}</span>" for x in record["metrics"]["expected_markers"])
        error = ""
        if not (record["ok"] and record["metrics"]["content_pass"]):
            error = f"<div class=\"callout failure\"><b>{esc(label)}</b><br>失败分类：<code>{esc(record['error_type'] or 'content_validation_failure')}</code><br>{esc(record['error_message'] or '机器规则未找到全部必需内容；请人工对照标准答案复核。')}</div>"
        metadata = esc(json.dumps(record["provider_metadata"], ensure_ascii=False, indent=2))
        fact_rows = "".join(f"<tr><td><code>{esc(item['id'])}</code></td><td class=\"{'good' if item['pass'] else 'bad'}\">{'命中' if item['pass'] else '未命中'}</td><td>{' / '.join(esc(x) for x in item['alternatives'])}</td><td>{' / '.join(esc(x) for x in item['matched']) or '—'}</td></tr>" for item in record["metrics"].get("fact_results", []))
        preview = display[:1800]
        checklist = "".join(f"<li>{esc(item)}</li>" for item in case.get("human_review_checklist", []))
        aid = json.dumps(record["attempt_id"])
        body = f"""<section class="hero"><div class="wrap"><div class="eyebrow">调用详情 · {esc(record['provider'])}</div><h1>{esc(record['case_name'])}</h1><div class="meta"><span class="pill {cls}">{label}</span><span class="pill">第 {record['run']} 次</span><span class="pill">{ms(record['elapsed_ms'])}</span><span class="pill" id="review-state">人工复核：未完成</span></div></div></section>
<section class="section"><div class="wrap"><h2>1. 完整题目与标准答案</h2><div class="card"><h3>给参评方法的任务</h3><p>{esc(case.get('task_prompt',''))}</p><p><b>输入 URL：</b><a href="{esc(record['url'])}">{esc(record['url'])}</a></p></div><div class="compare" style="margin-top:14px"><div><h3>参考答案</h3><pre>{esc(case.get('reference_answer',''))}</pre></div><div><h3>人工检查清单</h3><div class="card"><ul>{checklist}</ul><p class="tiny">参考答案用于人工理解正确结果；机器初判实际使用下方冻结事实组与规则。</p></div></div></div></div></section>
<section class="section"><div class="wrap"><h2>2. 供应商实际输出</h2><p><a href="../{esc(record['artifact_path'])}" download>下载本次完整输出（.txt）</a> · <a href="{esc(record['url'])}">打开标准答案源网页</a> · 完整性校验 <code>sha256:{esc(record['metrics'].get('content_sha256',''))}</code></p><div class="callout">这里的 .txt 是供应商返回内容，不是原网页文件。浏览器直接打开时会按纯文本显示；请用下方并排区域或“展开完整输出”核验。</div><div class="compare"><div><h3>标准答案</h3><pre>{esc(case.get('reference_answer',''))}</pre></div><div><h3>供应商输出预览（前 1,800 字符）</h3><pre>{esc(preview)}</pre></div></div>{truncation}<details><summary>展开本页中的完整供应商输出（{len(display):,} / {len(content):,} 字符）</summary><pre>{esc(display)}</pre></details></div></section>
<section class="section"><div class="wrap"><h2>3. 机器初判（不是最终结论）</h2>{error}<div class="attempt-grid" style="margin-top:16px"><div class="kv"><b>调用 ID</b><code>{esc(record['attempt_id'])}</code></div><div class="kv"><b>HTTP 状态</b>{esc(record['status_code']) or '—'}</div><div class="kv"><b>最终 URL</b><span class="break">{esc(record['final_url']) or '—'}</span></div><div class="kv"><b>输出大小</b>{record['metrics']['content_chars']:,} 字符</div><div class="kv"><b>事实覆盖率</b>{pct(record['metrics'].get('fact_coverage'))}</div><div class="kv"><b>最小长度</b>{'通过' if record['metrics'].get('length_pass',True) else '未通过'}</div></div><h3>事实组逐条判定</h3><div class="table-wrap"><table><thead><tr><th>事实组</th><th>机器状态</th><th>可接受表达</th><th>实际命中</th></tr></thead><tbody>{fact_rows or '<tr><td colspan="4">没有事实组</td></tr>'}</tbody></table></div><h3>显著标记：预期 / 实际命中</h3><p>{expected}</p><p>{matched}</p><details><summary>供应商元数据</summary><pre>{metadata}</pre></details></div></section>
<section class="section"><div class="wrap"><h2>4. 人工二次复核（HIL）</h2><p class="lead">请先并排阅读标准答案与完整输出，再独立作出最终判断。人工结论可以推翻机器初判，但必须填写理由。</p><form id="hil-form" class="review-form"><label>复核人</label><input name="reviewer" placeholder="姓名或工号"><label>最终结论</label><select name="decision"><option value="">请选择</option><option value="pass">通过</option><option value="fail">不通过</option><option value="dataset_drift">标准答案/页面已漂移，排除本条</option><option value="invalid_case">案例无效，排除本条</option><option value="uncertain">不确定，需升级复核</option></select><label>正确性</label><select name="correctness"><option value="">请选择</option><option value="complete">正确</option><option value="partial">部分正确</option><option value="wrong">错误</option></select><label>完整性</label><select name="completeness"><option value="">请选择</option><option value="complete">完整</option><option value="partial">部分缺失</option><option value="empty">严重缺失</option></select><label>可读性</label><select name="readability"><option value="">请选择</option><option value="good">可直接供 Agent 使用</option><option value="noisy">可用但噪声较多</option><option value="unusable">不可用</option></select><label>复核说明（必填）</label><textarea name="notes" placeholder="说明命中、缺失或误判的具体内容"></textarea><button class="button" type="button" onclick='saveReview({aid})'>保存到当前浏览器</button><button class="button" type="button" onclick='downloadReview({aid})'>导出本条复核 JSON</button></form></div></section><script>window.addEventListener('DOMContentLoaded',()=>loadReview({aid}));</script>"""
        write_page(report_dir / "attempts" / f"{slug(record['attempt_id'])}.html", layout(title=record["attempt_id"], body=body, base="../"))
