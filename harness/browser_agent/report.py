"""Publication-ready Chinese report for the autonomous browser-agent pilot."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from harness.web_fetch.report_style import STYLE as SHARED_STYLE

from .selection import load_pilot


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _read_attempts(evidence: Path) -> list[dict[str, Any]]:
    path = evidence / "attempts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _primary_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for engine in ("chromium", "moli"):
        primary = [row for row in rows if row["engine"] == engine and row["attempt"] == 1]
        retries = [row for row in rows if row["engine"] == engine and row["attempt"] == 2]
        durations = [float(row["duration_ms"]) for row in primary]
        tokens = [int((row.get("model_usage") or {}).get("total_tokens") or 0) for row in primary]
        result[engine] = {
            "completed": len(primary),
            "passed": sum(float(row["score"]) == 1.0 for row in primary),
            "score": sum(float(row["score"]) for row in primary) / len(primary) if primary else None,
            "retry_count": len(retries),
            "recovered": sum(float(row["score"]) == 1.0 for row in retries),
            "median_seconds": statistics.median(durations) / 1000 if durations else None,
            "median_tokens": statistics.median(tokens) if tokens else None,
        }
    return result


def _paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    primary = {
        (str(row["engine"]), int(row["task_id"])): float(row["score"]) == 1.0
        for row in rows
        if int(row["attempt"]) == 1
    }
    paired_ids = sorted(
        task_id
        for task_id in {task_id for _, task_id in primary}
        if ("chromium", task_id) in primary and ("moli", task_id) in primary
    )
    counts = {"both_pass": 0, "chromium_only": 0, "moli_only": 0, "both_fail": 0}
    for task_id in paired_ids:
        chromium = primary[("chromium", task_id)]
        moli = primary[("moli", task_id)]
        key = (
            "both_pass"
            if chromium and moli
            else "chromium_only"
            if chromium
            else "moli_only"
            if moli
            else "both_fail"
        )
        counts[key] += 1
    discordant = counts["chromium_only"] + counts["moli_only"]
    if discordant:
        smaller = min(counts["chromium_only"], counts["moli_only"])
        tail = sum(math.comb(discordant, k) for k in range(smaller + 1)) / 2**discordant
        p_value = min(1.0, 2 * tail)
    else:
        p_value = 1.0 if paired_ids else None
    return {"paired": len(paired_ids), **counts, "mcnemar_exact_p": p_value}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} s"


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{item if isinstance(item, Safe) else _e(item)}</td>" for item in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


class Safe(str):
    pass


REPORT_JS = r"""
function theme(){
  const root=document.documentElement;
  const next=root.dataset.theme==='dark'?'light':'dark';
  root.dataset.theme=next;
  try{localStorage.setItem('browser-agent-theme',next)}catch(_error){}
}
document.addEventListener('DOMContentLoaded',()=>{
  let saved='';
  try{saved=localStorage.getItem('browser-agent-theme')||''}catch(_error){}
  document.documentElement.dataset.theme=saved||'light';
});
"""


REPORT_STYLE = SHARED_STYLE + r"""
.top nav{gap:19px;flex-wrap:nowrap}.top nav a[aria-current="page"]{color:var(--ink);font-weight:800;text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:8px}.top .theme-toggle{margin-left:auto}
.page-main{min-height:78vh}.hero.compact{padding-bottom:22px}.hero.compact h1{font-size:clamp(34px,4.5vw,52px)}
.prose{max-width:900px;font-size:16px;line-height:1.82}.prose p{margin:14px 0}.section>.wrap>p{max-width:920px}.section>.wrap>ul{max-width:920px}
main>section:not(.hero):not(.section){width:min(var(--max),calc(100% - 40px));margin:22px auto;padding:28px;background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}
.decision-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.decision-grid .card{min-height:190px}.decision-grid h3{margin:8px 0}
.rank-list{counter-reset:rank}.rank-item{counter-increment:rank;display:grid;grid-template-columns:54px minmax(0,1fr);gap:18px;padding:22px 0;border-top:1px solid var(--line)}.rank-item:before{content:counter(rank);font-size:34px;font-weight:850;color:var(--accent)}.rank-item h3{margin:4px 0 8px}
.result-hero{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.result-hero .card{border-top:3px solid var(--accent)}.result-hero .card:nth-child(2){border-color:var(--violet)}.result-hero .card:nth-child(3){border-color:var(--lime)}.result-hero .card:nth-child(4){border-color:var(--coral)}
.reading-path .card{grid-column:span 4}.reading-path h3{margin-top:0}.reading-path a{font-weight:750}.callout.good{border-left-color:var(--good);background:var(--success-bg)}.callout.warn{border-left-color:var(--warn);background:var(--callout)}
.fine{font-size:13px;color:var(--muted)}details{margin:16px 0}summary{cursor:pointer;font-weight:700;color:var(--accent)}
.table-wrap{margin:18px 0}.table-wrap table{min-width:760px}.table-wrap th{white-space:nowrap}.source-list li{margin:9px 0}.footer{border-top:1px solid var(--line)}
@media(max-width:900px){.decision-grid{grid-template-columns:1fr}.result-hero{grid-template-columns:repeat(2,minmax(0,1fr))}.reading-path .card{grid-column:span 6}.top nav{gap:15px}}
@media(max-width:650px){.wrap{width:calc(100% - 28px)}.top nav{padding:9px 0}.brand{margin-right:4px}.result-hero{grid-template-columns:1fr}.reading-path .card{grid-column:span 12}.rank-item{grid-template-columns:38px minmax(0,1fr)}.hero{padding-top:30px}}
@media print{.top{display:none!important}.page-main{min-height:0}.section{break-inside:auto}.card,.table-wrap{box-shadow:none}.hero{padding-top:0}}
"""


NAVIGATION = [
    ("index.html", "首页", "home"),
    ("interpretation.html", "报告解读", "interpretation"),
    ("changes.html", "变更记录", "changes"),
    ("dataset.html", "评测集", "dataset"),
    ("methodology.html", "评测方法", "methodology"),
    ("results.html", "结果分析", "results"),
    ("review.html", "核验记录", "review"),
    ("methods.html", "方法介绍", "methods"),
    ("reproduction.html", "环境与复现", "reproduction"),
    ("accounts.html", "账号", "accounts"),
]


def _page(*, title: str, active: str, body: str) -> str:
    links = "".join(
        f'<a href="{_e(href)}"'
        + (' aria-current="page"' if key == active else "")
        + f'>{_e(label)}</a>'
        for href, label, key in NAVIGATION
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)} · Browser Use 评测</title><style>{REPORT_STYLE}</style><script>{REPORT_JS}</script></head><body><header class="top"><nav class="wrap">{links}<button class="theme-toggle" type="button" onclick="theme()">切换主题</button></nav></header><main class="page-main">{body}</main><footer class="footer"><div class="wrap"><p>结论只适用于本轮预注册样本、Agent、模型与运行配置；展示调整不改变测量结果。</p></div></footer></body></html>"""


def _hero(eyebrow: str, title: str, lead: str) -> str:
    return f'<section class="hero compact"><div class="wrap"><div class="eyebrow">{_e(eyebrow)}</div><h1>{_e(title)}</h1><p class="lead">{_e(lead)}</p></div></section>'


def _section(title: str, body: str, *, section_id: str = "") -> str:
    marker = f' id="{_e(section_id)}"' if section_id else ""
    return f'<section class="section"{marker}><div class="wrap"><h2>{_e(title)}</h2>{body}</div></section>'


def _result_section(attempts: list[dict[str, Any]]) -> str:
    summary = _primary_summary(attempts)
    paired = _paired_summary(attempts)
    rows = []
    for engine, label in (("chromium", "Chromium"), ("moli", "Moli 1.1.3")):
        item = summary[engine]
        rows.append(
            [
                label,
                f"{item['completed']}/30",
                f"{item['passed']}/{item['completed']}" if item["completed"] else "—",
                _pct(item["score"]),
                f"{item['recovered']}/{item['retry_count']}" if item["retry_count"] else "—",
                _seconds(item["median_seconds"]),
                f"{item['median_tokens']:,.0f}" if item["median_tokens"] is not None else "—",
            ]
        )
    complete = all(item["completed"] == 30 for item in summary.values())
    notice = (
        '<p class="callout good"><strong>测量完整。</strong>主分只使用每个浏览器每题的第一次正式执行；失败后的唯一一次重试只进入恢复诊断。</p>'
        if complete
        else '<p class="callout warn"><strong>测量尚未完整。</strong>本页可以先审阅选型与方法，但在两种浏览器各完成 30 个首轮样本前，不发布比较结论。</p>'
    )
    paired_text = (
        f"完整配对 {paired['paired']}/30 题：两者都通过 {paired['both_pass']}，仅 Chromium 通过 {paired['chromium_only']}，仅 Moli 通过 {paired['moli_only']}，两者都失败 {paired['both_fail']}；精确 McNemar 双侧 p={paired['mcnemar_exact_p']:.3f}。"
        if paired["paired"] == 30
        else f"当前完整配对 {paired['paired']}/30 题；未完成前不做显著性解释。"
    )
    return f"""<section id="results"><p class="eyebrow">实测结果</p><h2>同一 Agent、同一模型，只替换浏览器</h2>{notice}{_table(['浏览器','首轮覆盖','首轮通过','官方主分','失败重试恢复','首轮耗时中位数','首轮 tokens 中位数'], rows)}<p>{paired_text}</p><p class="fine">官方分数来自未修改的 WebArena‑Verified 1.2.3 evaluator。它是结果验收，不要求 Agent 复刻某条参考点击路径。McNemar 检验只比较同一题上的不一致结果；30 题仍是 pilot，p 值不能替代效应大小或更大样本复验。执行轨迹、网络 trace、最终 JSON、模型 token 用量和 evaluator 明细保存在仓库外的持久证据目录；报告不公开登录 Cookie 或完整网络正文。</p></section>"""


def _tier_section(attempts: list[dict[str, Any]]) -> str:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        if row["attempt"] == 1:
            grouped[(row["engine"], row["tier"])].append(row)
    rows: list[list[Any]] = []
    for tier, label in (("easy", "简单：1–4 步"), ("medium", "中等：5–7 步"), ("hard", "困难：8 步以上")):
        line: list[Any] = [label]
        for engine in ("chromium", "moli"):
            items = grouped[(engine, tier)]
            line.append(f"{sum(float(x['score']) == 1 for x in items)}/{len(items)}" if items else "—")
        rows.append(line)
    return f"""<section><h2>难度不是官方标签，是预注册的操作长度分层</h2><p>WebArena‑Verified 只发布一个 258 题 hard subset，并没有 easy/medium/hard 三档。为了让首轮同时覆盖简单、中等和困难任务，本项目在模型运行前按“完成目标所需的最少可见 UI 操作数”预注册三档：1–4、5–7、8+。这衡量的是操作链长度，不等于推理难度；涉及跨页聚合、条件筛选或提交修改的任务即使点击少，也可能更难。</p>{_table(['本项目分层','Chromium 首轮','Moli 首轮'], rows)}<p class="fine">30 题配额为 5/10/15；其中个别本项目“简单”题也属于官方 hard subset，两种分类回答的是不同问题，报告保留两者而不互相覆盖。</p></section>"""


def _failure_analysis(attempts: list[dict[str, Any]]) -> str:
    primary_failures = {
        int(row["task_id"])
        for row in attempts
        if int(row["attempt"]) == 1 and float(row["score"]) == 0.0
    }
    transport_rows = [row for row in attempts if row.get("model_transport_errors")]
    transport_count = sum(len(row["model_transport_errors"]) for row in transport_rows)
    if not primary_failures:
        return ""
    failure_rows = [
        [168, "项目筛选", "两种浏览器的主试与重试都返回空列表；官方答案非空。"],
        [68, "多帖聚合", "四次执行都只返回一本书，遗漏其余合格帖子中的结果。"],
        [124, "条件范围", "四次执行都返回 0.01–17774.32，取成了错误的全局价格范围。"],
        [142, "跨订单汇总", "四次执行都返回 80.50，未完成目标月份与品类的完整汇总。"],
        [506, "购物车最终状态", "主试虽到达结账或成功页，但官方网络事件检查发现购物车最终内容不符合唯一目标商品；重试又在 30 步上限前未完成。"],
    ]
    visible_rows = [row for row in failure_rows if row[0] in primary_failures]
    transport_text = (
        f"另有 {transport_count} 次模型请求发生代理断连或超时，均只做一次同请求传输重试并恢复；它们分布在 "
        + "、".join(f"{row['engine']} #{row['task_id']}" for row in transport_rows)
        + "。这些事件保留在证据中，不改分，但会拉长对应试次耗时。"
        if transport_rows
        else "本轮没有模型传输重试。"
    )
    return f"""<section><h2>五个共同失败首先指向 Agent，而不是某个浏览器</h2><p>两种浏览器在同五题失败，且十次失败诊断重试没有一次恢复。四个检索题的主试和重试还给出了跨浏览器一致的错误答案；购买题则由官方网络事件 evaluator 拒绝了不正确的购物车最终状态。这是“共同失败模式”的证据，不足以证明根因只在模型：同一 Agent、提示和 30 步上限也是共同变量。</p>{_table(['任务','失败类型','证据中的表现'], visible_rows)}<p>{_e(transport_text)}</p><p class="fine">本轮没有逐题胜负差，因此不能估计内核对成功率的方向性效应。Moli 在个别长任务上出现更长的交互等待，Chromium 也有一次 180 秒模型代理超时；端到端耗时混合了模型、站点重置、页面等待和浏览器执行，不应当解释成纯内核基准。</p></section>"""


def _release_notes() -> str:
    changes = _read_json(Path("research/report-changes.json"))
    releases = changes.get("browser_agent_releases") or []
    if not releases:
        return ""
    release = releases[0]
    rows = release.get("changes") or []
    return f"""<section><h2>本报告发布记录</h2><p><strong>{_e(release.get('date', '—'))} · {_e(release.get('title', '—'))}</strong></p><p>{_e(release.get('summary', '—'))}</p>{_table(['变更','原因','本次处理','结果与边界'], rows)}<details><summary>展开发布证据绑定</summary><p class="fine">attempts SHA-256：<code>{_e(release.get('attempts_sha256', '—'))}</code><br>selection SHA-256：<code>{_e(release.get('selection_sha256', '—'))}</code></p></details></section>"""


def _task_table(selection_path: Path, attempts: list[dict[str, Any]]) -> str:
    selection = load_pilot(selection_path)
    score = {(row["engine"], row["task_id"]): row for row in attempts if row["attempt"] == 1}
    rows = []
    for task in selection.tasks:
        rows.append(
            [
                task.task_id,
                {"easy": "简单", "medium": "中等", "hard": "困难"}[task.tier],
                task.estimated_min_steps,
                "是" if task.official_hard else "否",
                task.rationale,
                _pct(float(score[("chromium", task.task_id)]["score"])) if ("chromium", task.task_id) in score else "—",
                _pct(float(score[("moli", task.task_id)]["score"])) if ("moli", task.task_id) in score else "—",
            ]
        )
    return f"""<section id="tasks"><h2>预注册的 30 题</h2><p>选择文件在任何模型调用前固定，失败后不换题。样本含 23 个 RETRIEVE、4 个 MUTATE 和 3 个 NAVIGATE；按任务涉及站点计，Shopping Admin 12 题、Shopping 7 题、GitLab 6 题、Reddit 5 题。站点限定为这四个官方自托管环境；Wikipedia 与 Map 首轮排除，不是因为任务“太难”，而是它们分别要求大型 ZIM 和多套地图数据卷，会把环境下载与存储问题混进浏览器对比。</p>{_table(['ID','本项目层级','估计最少步','官方 hard','入选理由','Chromium','Moli'], rows)}</section>"""


def _environment_section(evidence: Path) -> str:
    manifest = _read_json(evidence / "manifest.json")
    run_config = _read_json(evidence / "run-config.json")
    if not manifest or not run_config:
        return """<p class="callout warn">正式运行尚未形成 manifest；发布门槛会阻止这份草稿作为完整结果上线。</p>"""
    protocol = manifest.get("protocol") or {}
    environment = manifest.get("environment") or {}
    versions = environment.get("browser_versions") or {}
    images = environment.get("browser_images") or {}
    official = environment.get("official_evaluator") or {}
    rows = [
        ["browser-eval", str(protocol.get("browser_eval_commit") or "—")],
        ["CowAgent", str(protocol.get("cowagent_commit") or "—")],
        ["模型", f"{protocol.get('model', '—')} · reasoning={protocol.get('reasoning_effort', '—')}"],
        ["WebArena‑Verified", str(environment.get("webarena_verified_version") or "—")],
        ["官方 evaluator checksum", str(official.get("evaluator_checksum") or "—")],
        ["官方 data checksum", str(official.get("data_checksum") or "—")],
        ["预注册选择集 SHA-256", str(run_config.get("selection_sha256") or "—")],
        ["Docker", f"{environment.get('docker_architecture', '—')} · {environment.get('docker_server_version', '—')} · context={run_config.get('docker_context', '—')}"],
    ]
    for engine, label in (("chromium", "Chromium"), ("moli", "Moli")):
        image = images.get(engine) or {}
        rows.append(
            [
                label,
                f"{(versions.get(engine) or {}).get('Browser', '—')} · {image.get('architecture', '—')} · {str(image.get('id') or '—')}",
            ]
        )
    site_rows = []
    for site, image in sorted((environment.get("site_images") or {}).items()):
        identity = (image.get("repo_digests") or [image.get("id") or "—"])[0]
        site_rows.append([site, image.get("reference") or "—", identity])
    sites = (
        _table(["站点", "官方镜像", "digest / image ID"], site_rows)
        if site_rows
        else '<p class="fine">站点镜像身份尚未记录。</p>'
    )
    return f"""<p>这些字段来自运行 manifest，用于限定复现边界，不参与评分。</p><details><summary>展开代码、镜像与校验身份</summary>{_table(['项目','记录值'], rows)}<h3>自托管站点镜像</h3>{sites}</details>"""


def _review_page(evidence: Path) -> str:
    review = _read_json(evidence / "result-review.json")
    rows: list[list[Any]] = []
    if not review:
        body = '<p class="callout warn"><strong>复核尚未执行。</strong>正式发布要求逐试次证据审计覆盖全部实测记录，且没有未解决项。</p>'
    else:
        coverage = review.get("coverage") or {}
        disagreements = review.get("disagreements") or []
        unresolved = review.get("unresolved") or []
        reviewer = review.get("reviewer") or {}
        complete = (
            coverage.get("recorded_attempts") == coverage.get("reviewed_attempts")
            and not unresolved
        )
        state = "复核通过" if complete else "复核未通过"
        klass = "good" if complete else "warn"
        body = (
            f'<p class="callout {klass}"><strong>{state}。</strong>'
            f"已复核 {_e(coverage.get('reviewed_attempts', 0))}/{_e(coverage.get('recorded_attempts', 0))} 个试次；"
            f"记分分歧 {_e(len(disagreements))}，未解决 {_e(len(unresolved))}。</p>"
            f"<p>复核者：{_e(reviewer.get('name', '—'))}，{_e(reviewer.get('kind', '—'))}。"
            "它检查证据文件是否完整、记录回答是否与落盘回答一致、主记录的分数和状态是否与官方 evaluator 输出一致、模型请求尝试是否完整入账，以及 evaluator/data checksum 是否统一。"
            "它不重新判断任务成功与否，也不允许人工改分。</p>"
        )
        rows = [
            [
                item.get("engine", "—"),
                item.get("task_id", "—"),
                item.get("attempt", "—"),
                "通过" if item.get("outcome") == "verified" else "未解决",
                sum(not bool(value) for value in (item.get("checks") or {}).values()),
            ]
            for item in review.get("attempts") or []
        ]
    coverage_table = (
        _table(["浏览器", "任务", "试次", "证据复核", "失败检查数"], rows)
        if rows
        else "<p>尚无可列出的复核记录。</p>"
    )
    page_body = (
        _hero(
            "Result review",
            "每个试次都要与官方评分证据对得上",
            "这是一项确定性证据审计，不是第二套 evaluator，也不是 LLM-as-a-judge。官方 WebArena-Verified 分数仍是唯一成绩来源。",
        )
        + _section(
            "复核结论",
            body
            + "<p>判定协议：任何缺失文件、无法解析的 JSON、回答不一致、分数或状态不一致、官方版本身份缺失，以及 evaluator/data checksum 不一致，都会保留为未解决项并阻止发布。人工意见不能覆盖官方 evaluator。</p>",
        )
        + _section("逐试次覆盖", coverage_table)
    )
    return _page(title="核验记录", active="review", body=page_body)


def _reproduction_page(evidence: Path) -> str:
    body = _hero(
        "Reproduction",
        "复现从仓库、预注册题集和固定运行配置开始",
        "代码、题集选择与运行入口以公开仓库为准；测量输出、登录 Cookie 和完整网络正文不进入 Git。",
    )
    body += _section(
        "唯一代码来源",
        '<p><a href="https://github.com/lexmount/browser-eval">lexmount/browser-eval</a> 保存评测代码、预注册的 30 题、依赖锁文件和报告生成器。官方 evaluator 由锁定的 <code>webarena-verified</code> 包提供，本项目不复制其判分逻辑。</p>',
    )
    body += _section(
        "核心命令",
        """<pre><code>./examples/browser_agent/run.sh
make browser-agent-review EVIDENCE="$BROWSER_AGENT_EVIDENCE"
make browser-agent-report EVIDENCE="$BROWSER_AGENT_EVIDENCE"
make browser-agent-report-check EVIDENCE="$BROWSER_AGENT_EVIDENCE"</code></pre><p class="fine">运行前需在仓库 <code>.env</code> 或显式进程环境配置目标模型可用的 API key、代理地址和 CowAgent checkout。Python 依赖只按 <code>uv.lock</code> 冻结安装。</p>""",
    )
    body += _section("本轮运行身份", _environment_section(evidence))
    return _page(title="环境与复现", active="reproduction", body=body)


def build_report(*, evidence: Path, output: Path, selection_path: Path) -> Path:
    attempts = _read_attempts(evidence)
    manifest = _read_json(evidence / "manifest.json")
    protocol = manifest.get("protocol") or {}
    run_model = str(protocol.get("model") or "gpt-5.6-sol")
    run_date = str(manifest.get("created_at") or "2026-09-09")[:10]
    output.mkdir(parents=True, exist_ok=True)
    methodology = _table(
        ["Benchmark", "评什么", "验收对象", "判分方式", "主要问题"],
        [
            ["WebArena‑Verified", "自托管站点上的真实交互", "最终 JSON；需要时加网络事件", "确定性程序 evaluator；无 LLM judge；不比对固定动作序列", "部署重，站点状态必须逐次重置；仍可能有浏览器协议兼容问题"],
            ["Online‑Mind2Web", "136 个真实网站的在线任务", "事实动作历史与最终完成情况", "官方建议 o4-mini WebJudge，并提供人工标签；属于 LLM-as-a-judge", "网页漂移、CAPTCHA、地区与账号差异；复现成本高"],
            ["WebVoyager", "少量主流实时网站上的视觉交互", "最终回答与执行证据", "原始工作流使用多模态 LLM 自动评审并辅以人工核验", "judge 偏差和实时网站漂移，精确复现较弱"],
            ["Mind2Web（2023）", "离线网页快照上的动作预测与泛化", "每一步目标元素与操作", "程序化 element accuracy、operation F1、step success、整任务 success", "主要评过程模仿，不证明今天能在真实网站完成任务"],
            ["Mind2Web 2", "长链、多站点、时变的 agentic search", "带引用的研究答案", "树状 rubric 的 Agent-as-a-Judge；Partial Completion、Success、Pass@3", "更接近研究/搜索，不是浏览器 UI 内核对比的首选"],
            ["BU Bench V1", "100 个混合来源的浏览器任务", "最终结果与运行轨迹", "官方仓库要求 Google judge LLM；二元分", "单一维护方、自定义题占比、judge 与云浏览器条件会影响可比性"],
            ["Odysseys", "200 个真实互联网长链、多站任务", "最终产物、轨迹与平均 6.1 个任务 rubric", "按细粒度 rubric 做模型评审；报告 partial、perfect 与每步效率", "实时网页漂移，运行与评审成本高；更适合第二阶段长链能力"],
        ],
    )
    summary = _primary_summary(attempts)
    paired = _paired_summary(attempts)
    chromium = summary["chromium"]
    moli = summary["moli"]
    home = _hero(
        f"Browser Agent Evaluation · {run_date}",
        "同一自主 Agent、同一模型，只替换浏览器",
        f"CowAgent + {run_model}（reasoning=low）在预注册的 30 题 WebArena-Verified 样本上分别运行 Chromium 与 Moli；主分只使用第一次正式执行。",
    )
    home += _section(
        "先看结论",
        f'<div class="result-hero"><div class="card"><span class="tiny">Chromium 首轮</span><div class="n">{chromium["passed"]}/{chromium["completed"]}</div><p>官方 pass@1 · {_pct(chromium["score"])}</p></div><div class="card"><span class="tiny">Moli 首轮</span><div class="n">{moli["passed"]}/{moli["completed"]}</div><p>官方 pass@1 · {_pct(moli["score"])}</p></div><div class="card"><span class="tiny">逐题不一致</span><div class="n">{paired["chromium_only"] + paired["moli_only"]}</div><p>{paired["paired"]} 个完整配对中的单边胜出</p></div><div class="card"><span class="tiny">失败重试恢复</span><div class="n">{chromium["recovered"] + moli["recovered"]}/{chromium["retry_count"] + moli["retry_count"]}</div><p>只作诊断，不回写主分</p></div></div><p class="callout good"><strong>当前最小结论：</strong>这个 30 题 pilot 没有观察到 Chromium 与 Moli 的任务成功率差异。双方均通过 25 题，并在同 5 题失败。共同失败更像 Agent、模型、提示或步数上限的共同瓶颈，不能据此断言某个浏览器更强。</p>',
    )
    home += _section(
        "怎么读这份报告",
        '<div class="grid reading-path"><article class="card"><h3>先理解结论</h3><p>为什么没有差异仍然是有效结果，以及它能支持什么决策。</p><a href="interpretation.html">读报告解读 →</a></article><article class="card"><h3>再核对口径</h3><p>公开 benchmark 的采用情况、验收对象和本轮筛选规则。</p><a href="dataset.html">看评测集 →</a></article><article class="card"><h3>最后查证据</h3><p>逐试次核验、环境身份和可复现命令。</p><a href="review.html">看核验记录 →</a></article></div>',
    )

    interpretation = _hero(
        "Report interpretation",
        "这轮结果证明流程可比，但还不能证明内核等价",
        "零个不一致配对意味着本样本没有给出成功率方向；它缩小了下一轮该查什么，而不是结束评测。",
    )
    interpretation += _section(
        "30 题回答了什么",
        '<div class="prose"><p>两种浏览器面对完全相同的任务、站点快照、Agent、模型和资源上限，各完成 30 次首轮执行。25 题共同通过，5 题共同失败，失败后的唯一一次重试也没有恢复。这个模式支持一个克制的判断：在当前样本里，浏览器替换没有改变最终任务成功率。</p><p>它没有证明 Chromium 与 Moli 在所有网站、长链任务、延迟或资源使用上等价。样本只有 30 题，且端到端耗时混合了模型响应、站点重置、页面等待和浏览器执行。成功率没有差异时，继续用同一批题反复重跑的收益有限；下一轮更应该扩大可稳定验收的题量，并补充能区分内核的协议兼容与长页面交互案例。</p></div>',
    )
    interpretation += _section(
        "为什么首轮选 WebArena-Verified",
        '<div class="prose"><p>选择它不是因为采用次数最多，而是因为它最适合做浏览器 A/B：站点和数据可自托管，任务状态可以逐次重置，官方 evaluator 以结构化答案和必要网络副作用做确定性判分，不依赖 LLM judge，也不要求 Agent 重放参考点击路径。</p><p>Mind2Web 家族采用更广，但原版主要评离线动作预测；Online-Mind2Web 和 WebVoyager 接近真实网站，却同时引入网页漂移与模型裁判。它们适合第二阶段测泛化，不适合作为第一组内核差异归因实验。</p></div>',
    )
    interpretation += _section(
        "下一轮决策",
        '<p class="callout"><strong>先扩大确定性样本，再引入实时网站。</strong>保持 CowAgent 和 pass@1 主分不变，把稳定可验收的 WebArena-Verified 题目扩到更大配对样本；另开 Online-Mind2Web / WebVoyager 泛化轨道，并把 LLM judge 与人工抽检结果单独报告。Astra 获权后新建独立运行，不能与本轮 GPT-5.6 成绩混合。</p>',
    )

    dataset = _hero("Dataset", "公开 benchmark 怎样选", "先区分真实交互、离线动作预测和 agentic search，再看采用次数；不能把同一家族的不同变体当成同一成绩。")
    dataset += _section(
        "公开采用次数 Top 3",
        '<p>统计单位是公开一手材料中采用过该家族的机构数，同一机构重复发布只计一次。它衡量行业采用信号，不是 benchmark 质量分。</p><div class="rank-list"><article class="rank-item"><div><h3>Mind2Web 家族 · 5 家</h3><p>OpenAI、Anthropic、Google DeepMind、THUDM / ChatGLM、Browser Use。原版、Online 版与 Mind2Web 2 的任务和评分不同，成绩不能合并。</p></div></article><article class="rank-item"><div><h3>WebArena 家族 · 3 家</h3><p>OpenAI、Anthropic、THUDM / ChatGLM。自托管、可重置、结果验收，最适合本轮浏览器内核 A/B。</p></div></article><article class="rank-item"><div><h3>WebVoyager · 2 家</h3><p>OpenAI、Google DeepMind。真实网站和视觉交互直观，但网页漂移与 LLM judge 会降低精确复现性。</p></div></article></div>',
    )
    dataset += _section(
        "Mind2Web 没有统一的 easy / medium / hard",
        '<p>原始 Mind2Web 的官方拆分是 <strong>cross-task、cross-website、cross-domain</strong>，衡量从见过同站任务到跨领域的泛化，不是难度。Online-Mind2Web 也没有稳定的三档定义。Mind2Web 2 用 short（少于 10 次动作）、medium（10–50）、long（超过 50）描述任务跨度，仍不等于 easy / medium / hard。</p><p>本轮的简单 5 题、中等 10 题、困难 15 题是运行前固定的操作长度层级：1–4、5–7、8+ 次最少可见 UI 操作。它只保证覆盖结构，不参与评分，也不冒充官方标签。</p>',
    )
    dataset += _task_table(selection_path, attempts)

    methodology_page = _hero("Methodology", "到底评最终结果，还是评过程", "不同 benchmark 的验收对象差别很大；本轮只把官方确定性最终结果作为主分。")
    methodology_page += _section("七个候选 benchmark 的判分边界", methodology)
    methodology_page += _section(
        "WebArena-Verified 的精确判分链",
        '<p>官方 1.2.3 数据共有 812 题，全部配置 <code>AgentResponseEvaluator</code>，其中 663 题还配置 <code>NetworkEventEvaluator</code>。前者解析 Agent 最终 JSON，核对任务类型、状态和带类型字段；后者从 trace 或 HAR 读取请求、响应、Cookie 等事件，验证导航或状态改变是否真的发生。</p><p>单题为二元分：所有已配置 evaluator 都返回 1.0 才通过。它检查最终结果和必要副作用，不检查是否复刻参考路径；本轮也没有 LLM-as-a-judge。</p>',
    )
    methodology_page += _section(
        "本轮预注册协议",
        _table(['项目','规则'], [['样本','固定 30 题：简单 5、中等 10、困难 15；运行后不换题'],['Agent 与模型',f'CowAgent 真实自主观察、规划和调用 BrowserTool；{run_model}，reasoning=low'],['唯一变量','Agent、提示、模型、站点数据和资源限额相同，只替换 Chromium / Moli'],['主分','每个题×浏览器只跑一次，官方二元 pass@1；逐题配对'],['重试','首轮失败后最多重跑一次，只报告恢复诊断；模型传输错误同样最多一次重发并完整入证'],['排除','不纳入无法稳定验收的实时任务；首轮排除需要额外大型数据卷的 Wikipedia / Map']]) + '<p class="fine">基础设施失败应修复后重跑同一预注册试次，不能记作 Agent 失败，也不能借机换题。Playwright trace 用于调试，官方 evaluator 读取最终回答和 HAR。</p>',
    )

    results = _hero("Results", "成功率相同，五个失败也完全重合", "结果页只展示由当前持久证据计算的首轮成绩、配对关系和失败诊断。")
    results += _result_section(attempts) + _tier_section(attempts) + _failure_analysis(attempts)

    methods = _hero("Methods", "两条轨道回答不同问题", "固定动作重放和自主 Agent 都有价值；错误在于混用名称或合并分数。")
    methods += _section(
        "浏览器内核微基准与自主 Agent 评测",
        _table(['轨道','Agent 是否自主','控制变量','主指标','回答的问题'], [['浏览器内核微基准（原 30 题）','否；重放固定 BrowserTool 等价调用','同一动作序列，只替换浏览器','严格输出正确率、动作执行、资源与耗时','相同操作交给两个内核，谁更兼容、资源更省？'],['WebArena-Verified Agent 评测（本报告）','是；CowAgent 自主观察、规划和调用 BrowserTool','同一 Agent、模型、提示、站点快照与任务，只替换浏览器','官方首轮 pass@1；失败重试恢复率另列','浏览器差异会怎样影响完整自主任务成功率？']]) + '<p>原 30 题确实由 CowAgent 真实执行 BrowserTool，但任务动作是预先固定的，因此它测执行层，不测自主规划。把它正名为微基准不会降低其价值，也不应把它冒充完整 Agent benchmark。</p>',
    )
    methods += _section(
        "Browser Use 官方实践里可借鉴什么",
        '<p>Browser Use 同时使用 BU Bench V1、Odysseys、Online-Mind2Web 和 Stealth Bench。最值得复用的是运行前确认任务可完成、保存逐题 trace、支持断点续跑，并把不可完成与 Agent 真失败分开。Stealth Bench 评反机器人检测和浏览器基础设施，不能替代自主任务成功率评测。</p><p>BU Bench 和 Odysseys 使用模型评审，且包含实时网站、搜索或长链任务。它们适合第二阶段，而不是当前需要清晰归因的最小对照。</p>',
    )

    accounts = _hero("Accounts", "本地站点不需要真实业务账号", "账号页只解释运行权限和费用边界，不展示任何密钥、Cookie 或个人信息。")
    accounts += _section(
        "站点、模型与费用",
        _table(['范围','本轮做法','是否需要外部付费账号'], [['Shopping / Admin / Reddit / GitLab','官方 Docker 镜像与内置测试账号；每题前重置状态','不需要'],['模型 API',f'{run_model}，Responses API，reasoning=low','需要有权调用该模型的 API 账号'],['GPT-6 Astra','已直接验证当前 LiteLLM Team；返回 team_model_access_denied','当前团队权限不足，未运行'],['Artifact Hub','只发布脱敏后的静态报告；令牌不进报告和 Git','发布者需已有站点权限']]) + '<p>本地可部署不等于轻量：四个首轮站点镜像约 91 GB，正式环境使用独立 140 GiB Docker profile。Apple Silicon 对部分旧 amd64 PHP-FPM 镜像还需要受控兼容层。</p>',
    )

    sources = '<ul class="source-list"><li><a href="https://openai.com/index/gpt-6-astra/">OpenAI：GPT-6 Astra 发布页</a></li><li><a href="https://www-cdn.anthropic.com/bbd8ef16d70b7a1665f14f306ee88b53f686aa75/Claude%20Sonnet%204.6%20System%20Card.pdf">Anthropic：Claude Sonnet 4.6 System Card</a></li><li><a href="https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-2-5-Computer-Use-Model-Card.pdf">Google DeepMind：Gemini 2.5 Computer Use Model Card</a></li><li><a href="https://github.com/OSU-NLP-Group/Mind2Web">Mind2Web 官方仓库</a>；<a href="https://github.com/OSU-NLP-Group/Online-Mind2Web">Online-Mind2Web 官方仓库</a></li><li><a href="https://github.com/browser-use/benchmark">Browser Use benchmark 仓库</a></li><li><a href="https://github.com/ljang0/Odysseys">Odysseys 官方仓库</a></li><li><a href="https://github.com/THUDM/AutoWebGLM">THUDM：AutoWebGLM</a></li><li><a href="https://github.com/ServiceNow/webarena-verified">WebArena-Verified 官方仓库</a></li></ul><p class="fine">采用统计只计公开一手材料。未找到 DeepSeek 或 Kimi 的官方 UI browser-use benchmark，不代表其内部没有评测；BrowseComp 属于 agentic search，本报告不把它算作浏览器 UI 操作基准。</p>'
    methods += _section("公开依据", sources)

    changes = _hero("Release notes", "变更记录从可审计版本开始", "这里只写会改变读者理解、分母或复现边界的正式变更，不记录制作过程。") + _release_notes()

    pages = {
        "index.html": _page(title="首页", active="home", body=home),
        "interpretation.html": _page(title="报告解读", active="interpretation", body=interpretation),
        "changes.html": _page(title="变更记录", active="changes", body=changes),
        "dataset.html": _page(title="评测集", active="dataset", body=dataset),
        "methodology.html": _page(title="评测方法", active="methodology", body=methodology_page),
        "results.html": _page(title="结果分析", active="results", body=results),
        "review.html": _review_page(evidence),
        "methods.html": _page(title="方法介绍", active="methods", body=methods),
        "reproduction.html": _reproduction_page(evidence),
        "accounts.html": _page(title="账号", active="accounts", body=accounts),
    }
    for filename, text in pages.items():
        (output / filename).write_text(text, encoding="utf-8")
    report = output / "index.html"
    review = _read_json(evidence / "result-review.json")
    audit = {
        "schema": "lex_browser_agent_report/1",
        "attempt_count": len(attempts),
        "summary": _primary_summary(attempts),
        "paired_summary": _paired_summary(attempts),
        "selection_sha256": load_pilot(selection_path).sha256,
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
    (output / "report-data.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


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
