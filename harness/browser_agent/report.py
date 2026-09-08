"""Publication-ready Chinese report for the autonomous browser-agent pilot."""

from __future__ import annotations

import argparse
import html
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

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
        return """<section><h2>实测环境与证据身份</h2><p class="callout warn">正式运行尚未形成 manifest；发布门槛会阻止这份草稿作为完整结果上线。</p></section>"""
    protocol = manifest.get("protocol") or {}
    environment = manifest.get("environment") or {}
    versions = environment.get("browser_versions") or {}
    images = environment.get("browser_images") or {}
    official = environment.get("official_evaluator") or {}
    rows = [
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
    return f"""<section><h2>实测环境与证据身份</h2>{_table(['项目','记录值'], rows)}<h3>自托管站点镜像</h3>{sites}<p class="fine">报告从运行 manifest 读取这些值，不按产品名推测版本。镜像 digest、浏览器 CDP 版本、代码 commit、数据 checksum 与选择集哈希共同限定本轮可复现边界。</p></section>"""


def build_report(*, evidence: Path, output: Path, selection_path: Path) -> Path:
    attempts = _read_attempts(evidence)
    output.mkdir(parents=True, exist_ok=True)
    css = """
:root{--ink:#17202a;--muted:#667085;--paper:#fbfaf7;--card:#fff;--line:#dedbd2;--accent:#155eef;--soft:#eef4ff;--good:#eaf8ef;--warn:#fff6df}*{box-sizing:border-box}html,body{overflow-x:hidden}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.72 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{width:100%;max-width:1100px;margin:auto;padding:64px 28px 100px}header{border-bottom:1px solid var(--line);padding-bottom:42px;margin-bottom:42px}h1{font:700 clamp(38px,6vw,72px)/1.06 ui-serif,Georgia,serif;letter-spacing:-.035em;max-width:920px;margin:10px 0 22px}h2{font:700 30px/1.25 ui-serif,Georgia,serif;margin:0 0 16px}h1,h2,h3,p,li{overflow-wrap:anywhere}h3{margin:28px 0 8px}section{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:30px;margin:22px 0;box-shadow:0 8px 30px rgba(31,41,55,.035)}p{max-width:88ch}.lede{font-size:20px;color:#344054}.eyebrow{color:var(--accent);font-weight:750;letter-spacing:.08em;text-transform:uppercase;font-size:13px}.meta,.fine{color:var(--muted);font-size:14px}.callout{border-left:4px solid var(--accent);padding:13px 16px;background:var(--soft);border-radius:6px}.callout.good{background:var(--good)}.callout.warn{background:var(--warn);border-color:#d68a00}.table-wrap{max-width:100%;overflow:auto;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:11px 12px}th{background:#f5f3ee;white-space:nowrap}a{color:var(--accent)}code{background:#f1f2f4;padding:2px 5px;border-radius:5px}.rank{display:grid;grid-template-columns:48px minmax(0,1fr);gap:14px;padding:18px 0;border-top:1px solid var(--line)}.rank>div{min-width:0}.rank b{font:700 30px/1 Georgia,serif;color:var(--accent)}nav{display:flex;gap:16px;flex-wrap:wrap;margin-top:24px}nav a{text-decoration:none;font-size:14px}@media(max-width:650px){main{padding:36px 14px}section{padding:21px}h1{font-size:34px}.lede{font-size:18px}h2{font-size:25px}}@media print{body{background:#fff}main{max-width:none;padding:0}section{break-inside:avoid;box-shadow:none}nav{display:none}a{color:inherit;text-decoration:none}}
"""
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
    html_text = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Browser Use 公开评测选型与 WebArena‑Verified 首轮对比</title><style>{css}</style></head><body><main><header><p class="eyebrow">Browser Agent Evaluation · 2026-09-08</p><h1>Browser Use 公开评测怎么选，以及为什么首轮用 WebArena‑Verified</h1><p class="lede">结论先说：原有 30 题应保留，但它测的是固定动作下浏览器内核能否正确、快速执行，不是 CowAgent 的自主规划能力。新增轨道用真实 CowAgent + GPT‑6 Astra（reasoning=low），在同一组 WebArena‑Verified 任务上只替换 Chromium/Moli，以官方程序化 evaluator 判最终结果。</p><nav><a href="#decision">选型结论</a><a href="#methods">评测方法</a><a href="#results">实测结果</a><a href="#tasks">30 题</a></nav></header>
<section id="decision"><p class="eyebrow">采用统计与决策</p><h2>公开采用次数 Top 3：Mind2Web、WebArena、WebVoyager</h2><p>统计单位是公开一手材料中采用过该家族的机构数，而不是论文引用量；同一机构重复发布只计一次。Mind2Web 家族为 5 家（OpenAI、Anthropic、Google DeepMind、THUDM / ChatGLM、Browser Use），WebArena 家族为 3 家（OpenAI、Anthropic、THUDM / ChatGLM），WebVoyager 为 2 家（OpenAI、Google DeepMind）。不同变体的成绩不能合并，这里的聚合只回答“有多少机构采用过这个家族”。</p><div class="rank"><b>1</b><div><h3>Mind2Web 家族 · 5 家</h3><p>采用最广，但家族内部差异最大：原版是离线动作预测，Online 版是真实网站 + WebJudge，Mind2Web 2 是长链研究任务 + Agent-as-a-Judge。适合作为第二阶段真实互联网泛化评测，不能把三者成绩合并。</p></div></div><div class="rank"><b>2</b><div><h3>WebArena 家族 · 3 家</h3><p>覆盖导航、检索和改写状态；自托管让 Chromium/Moli 面对同一份站点数据。首轮仍选它的 Verified 版本，因为它重新审计任务、答案和 evaluator，并使用确定性程序判分。这里是“实验适配性”胜过“采用数量”。</p></div></div><div class="rank"><b>3</b><div><h3>WebVoyager · 2 家</h3><p>经典视觉浏览器基准，优势是直观、接近用户网站；问题是实时网页和 LLM judge 同时引入波动，作为内核 A/B 首轮不如 WebArena‑Verified 稳定。</p></div></div><p class="callout"><strong>为什么不是照着 Astra 发布页抄 benchmark：</strong>GPT‑6 Astra 的公开 computer-use 主表是 Agents’ Last Exam、OSWorld 2.0 和 ScreenSpot‑Pro；页面只给出 Mind2Web 的 1.9× 演示耗时对比，没有公开 WebArena‑Verified 分数。这些基准能说明模型的通用电脑操作能力，但 OSWorld/ScreenSpot 并不是纯 browser use，也不适合直接比较两个浏览器内核。</p></section>
<section id="methods"><p class="eyebrow">Benchmark taxonomy</p><h2>它们评最终结果，还是评过程？</h2>{methodology}<h3>WebArena‑Verified 的精确判分链</h3><p>官方 1.2.3 数据共有 812 题，812 题都配置 <code>AgentResponseEvaluator</code>，其中 663 题还配置 <code>NetworkEventEvaluator</code>。前者解析 Agent 最终 JSON，核对任务类型、状态和带类型的返回字段；后者从 Playwright trace 或 HAR 中提取请求、响应、Cookie 等事件，核对导航或状态改变是否实际发生。字段先按官方 schema 做 URL、字符串、数字、布尔、数组和对象规范化，再做结构比较；需要有序时保留顺序，允许的备选答案由任务定义声明。没有模糊子串放行，也没有 LLM judge。</p><p>单题是二元分：该题配置的每一个 evaluator 都得到 1.0 才通过，任何一项失败或 evaluator 报错都记 0。因此它评的是最终结果与必要的可观察副作用，不是“按参考路径逐步打分”。Agent 可以走不同点击路径，只要完成的结果和必要事件符合任务契约即可。</p></section>
<section><h2>Mind2Web 的 easy / medium / hard 要先纠正</h2><p>原始 Mind2Web 的官方拆分不是难度档，而是 <strong>cross-task、cross-website、cross-domain</strong>：测试任务是否见过同站任务、是否换了网站、是否换了整个领域。Online‑Mind2Web 官方也没有稳定的 easy/medium/hard 定义；有些第三方按参考动作长度再分档，那是第三方分析口径。Mind2Web 2 论文把任务 horizon 定义为 short（少于 10 次动作）、medium（10–50）、long（超过 50），仍不等同于 easy/medium/hard。</p><p>所以本轮的 5/10/15 是我们自己的预注册操作长度层级。它只用于保证样本覆盖，不改写官方字段，也不把它当成跨论文可比较的难度标签。</p></section>
<section><h2>Browser Use 官方现在更偏向什么</h2><p>要区分三种信号。Browser Use 产品首页强调开放的 100 题 <strong>BU Bench V1</strong>，同时展示 <strong>Odysseys</strong> 的 200 题长链排行榜成绩；其 benchmark 仓库当前把 71 题 <strong>Stealth Bench V1</strong> 放在最前。Stealth Bench 测的是反机器人检测与浏览器基础设施，不是 CowAgent 能否自主完成业务任务，因此不能代替本报告的 agent 评测。</p><p>BU Bench 由 Custom、WebBench、Mind2Web 2、GAIA、BrowseComp 各 20 题组成，另有独立的 300 题 Online‑Mind2Web 运行仓库。最值得借鉴的是：运行前确认任务可完成、保存逐题 trace、支持断点续跑、把“不可完成”与 Agent 真失败分开。Odysseys 还把长任务拆成平均 6.1 个可检查 rubric，并同时报告完整成功率、部分进度和每步效率。</p><p>首轮不直接照搬的原因也很具体：这些工作流都包含模型评审，BU Bench 还混入搜索与问答任务，Odysseys 又是实时多站长链任务。它们适合第二阶段测真实互联网泛化和长链能力，但会把网页漂移、judge 波动和浏览器内核差异同时混进结果。</p></section>
<section><h2>WebArena‑Verified 本地部署要不要账号付费</h2><p>四个首轮站点使用官方 Docker 镜像和内置测试账号，本地运行不需要向 GitLab、Reddit 或电商站点申请真实账号，也没有按题平台费用。成本来自本机 CPU、内存、磁盘和 GPT‑6 Astra API。</p><p>Astra 通过 Responses API 调用，模型 ID 是 <code>gpt-6-astra</code>，本轮固定 <code>reasoning.effort=low</code>。OpenAI 官方模型页把 Free API tier 标为不支持，因此需要一个已开通 API 计费且有该模型权限的 OpenAI 账号和项目密钥；Codex 不能代替团队注册账号、开通计费或承担费用。</p><p>“本地可部署”也不等于轻量。本轮核对的官方 GitLab 镜像 manifest 含一个约 22.0 GB 的压缩主层，官方文档建议至少 4 GB 内存；四站点与两浏览器因此放在独立的 100 GB Docker profile 中。Wikipedia 与 Map 还需要额外数据卷，所以首轮不纳入。自托管能消除网站内容漂移、封号和真实支付风险，但不会自动消除镜像下载、环境初始化、登录兼容、浏览器 CDP 实现差异或 evaluator 缺陷。</p></section>
<section><h2>本轮怎样保证是可比较的自主 Agent 实验</h2>{_table(['项目','预注册规则'], [['样本','固定 30 题：简单 5、中等 10、困难 15；运行后不换题，不重复抽样'],['Agent 与模型','CowAgent 真实自主观察、规划和调用 BrowserTool；GPT‑6 Astra，reasoning=low；每个试次清空模型会话'],['工具接口','沿用 CowAgent 的页面快照与 browser 函数工具，不调用 Astra 原生 computer tool；测的是既有 CowAgent 接入方式下的自主 browser use'],['唯一变量','Agent、提示、模型、任务、站点数据和资源限额相同，只替换 Chromium / Moli'],['环境','每个试次都从官方镜像重置该题站点并等待应用连续稳定；Shopping、Shopping Admin、Reddit 使用官方头登录，GitLab 使用官方 UI 登录'],['顺序','逐题交替先跑 Chromium 或 Moli，减轻固定先后顺序偏差'],['主分','每个“题 × 浏览器”只跑一次，官方二元 pass@1；两边构成逐题配对'],['重试','只对首轮失败项再试一次，最多一次；重试仅报告恢复率，不回填或抬高主分'],['留证','保存最终 JSON、CowAgent 事件、标准网络 HAR、可视 Playwright trace、官方 evaluator 明细、镜像 ID、代码 commit、token 与资源指标'],['排除','首轮不选 Wikipedia / Map；不选无法由官方确定性 evaluator 验收的外部实时站点任务']])}<p class="fine">“操作步数层级”只决定覆盖结构，不参与得分。环境准备失败应标成基础设施错误并修复后重跑同一预注册试次，不能计作 Agent 失败，也不能借机换题。官方 evaluator 读取 HAR；可视 trace 只用于调试，不参与评分。由于工具接口不同，本轮结果不能直接代表 Astra 发布页中原生 computer-use harness 的成绩。</p></section>
{_result_section(attempts)}{_tier_section(attempts)}{_task_table(selection_path, attempts)}{_environment_section(evidence)}
<section><h2>两条轨道必须分开读</h2>{_table(['轨道','Agent 是否自主','控制变量','主指标','回答的问题'], [['浏览器内核微基准（原 30 题）','否；重放固定 BrowserTool 等价调用','同一动作序列，只替换浏览器','严格输出正确率、动作执行、资源与耗时','相同操作交给两个内核，谁更兼容/省资源？'],['WebArena‑Verified Agent 评测（本报告）','是；CowAgent 自主观察、规划和调用 BrowserTool','同一 Agent、模型、提示、站点快照与任务，只替换浏览器','官方首轮 pass@1；失败重试恢复率另列','浏览器差异会怎样影响完整自主任务成功率？']])}<p>固定动作轨道不是“有问题的假 Agent”，它是有价值的内核微基准；问题只是旧命名把它说成了自主 browser use。两条轨道互补，但分数不能相加，也不能用一条替代另一条。</p></section>
<section><h2>公开证据与复现边界</h2><ul><li><a href="https://openai.com/index/gpt-6-astra/">OpenAI：GPT‑6 Astra 发布页</a>；<a href="https://developers.openai.com/api/docs/models/gpt-6-astra">OpenAI API：GPT‑6 Astra 模型页</a>；<a href="https://developers.openai.com/api/docs/guides/latest-model">OpenAI API：Astra 使用指南</a></li><li><a href="https://openai.com/index/computer-using-agent/">OpenAI：Computer-Using Agent 评测说明</a></li><li><a href="https://www-cdn.anthropic.com/bbd8ef16d70b7a1665f14f306ee88b53f686aa75/Claude%20Sonnet%204.6%20System%20Card.pdf">Anthropic：Claude Sonnet 4.6 System Card</a></li><li><a href="https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-2-5-Computer-Use-Model-Card.pdf">Google DeepMind：Gemini 2.5 Computer Use Model Card</a></li><li><a href="https://github.com/OSU-NLP-Group/Mind2Web">Mind2Web 官方仓库</a>；<a href="https://github.com/OSU-NLP-Group/Online-Mind2Web">Online‑Mind2Web 官方仓库</a></li><li><a href="https://github.com/browser-use/benchmark">Browser Use 官方 benchmark 仓库</a>；<a href="https://github.com/browser-use/browser-use">Browser Use 产品仓库</a></li><li><a href="https://github.com/ljang0/Odysseys">Odysseys 官方仓库</a></li><li><a href="https://github.com/THUDM/AutoWebGLM">THUDM：AutoWebGLM</a></li><li><a href="https://github.com/ServiceNow/webarena-verified">WebArena‑Verified 官方仓库</a>；<a href="https://github.com/ServiceNow/webarena-verified/blob/main/docs/environments/gitlab.md">官方 GitLab 环境说明</a></li></ul><p class="fine">采用统计只计公开一手材料。未找到 DeepSeek 或 Kimi 的官方浏览器交互 benchmark，不代表其内部没有评测；Kimi 公布的 BrowseComp 属于 agentic search，本报告不把它算成 UI browser use。</p></section>
</main></body></html>"""
    report = output / "index.html"
    report.write_text(html_text, encoding="utf-8")
    audit = {
        "schema": "lex_browser_agent_report/1",
        "evidence": str(evidence),
        "attempt_count": len(attempts),
        "summary": _primary_summary(attempts),
        "paired_summary": _paired_summary(attempts),
        "selection_sha256": load_pilot(selection_path).sha256,
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
