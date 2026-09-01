"""Chinese paginated evidence-first browser benchmark report."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

REPORT_STEM = "browser-use-evaluation-latest"
PAGES = tuple(
    (slug, label, f"{REPORT_STEM}{suffix}.html")
    for slug, label, suffix in (
        ("index", "结论摘要", ""),
        ("suite", "评测集", "-suite"),
        ("method", "环境与方法", "-method"),
        ("results", "总体结果", "-results"),
        ("analysis", "逐任务分析", "-analysis"),
        ("appendix", "复现与附录", "-appendix"),
    )
)
TASK_META = {
    "example-heading": ("读取 Example Domain 标题", "静态页面基线"),
    "hacker-news-top-story": ("读取 Hacker News 当前首条新闻", "动态列表"),
    "pypi-requests-version": ("读取 PyPI requests 当前版本", "包索引"),
    "arxiv-transformer-abstract": ("读取 Transformer 论文摘要", "学术长文本"),
    "github-moli-readme": ("浏览 Moli README", "代码仓库与滚动"),
    "mdn-fetch-guide": ("从 Fetch API 进入 Using Fetch", "站内点击跳转"),
    "python-tutorial-navigation": ("浏览 Python 教程并使用历史记录", "多页前进与后退"),
    "rfc9110-section-navigation": ("浏览 RFC 9110 指定章节", "长文档锚点"),
    "quotes-pagination-history": ("翻页浏览公开语录目录", "分页与历史恢复"),
    "httpbin-form-submit": ("填写并提交 HTTPBin 表单", "输入、选择和提交"),
    "hacker-news-story-context": ("读取 Hacker News 首条新闻及上下文", "动态列表校准任务"),
    "pypi-requests-release-profile": ("核对 requests 版本与发布历史", "包索引校准任务"),
    "arxiv-transformer-paper-profile": ("汇总论文标题、作者、摘要与提交历史", "学术长文本多字段读取"),
    "github-moli-readme-to-issues": ("从 Moli README 浏览到 Issues 并恢复历史", "代码仓库多页面导航"),
    "mdn-fetch-guide-history": ("从 Fetch API 进入指南并验证前进后退", "文档点击与历史恢复"),
    "python-tutorial-two-chapters-history": ("连续浏览两章 Python 教程并恢复历史", "多页文档遍历"),
    "rfc9110-multi-section-history": ("浏览 RFC 9110 多章节并恢复锚点历史", "长标准文档与锚点"),
    "quotes-three-page-history": ("连续翻阅三页语录并恢复两级历史", "多级分页与历史"),
    "httpbin-form-resubmission": ("填写 HTTPBin 表单、返回修改并二次提交", "多字段表单与重提交"),
    "books-mystery-category-traversal": ("浏览分类、两页列表与两本书详情", "目录、分页、详情与历史"),
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


def _table(headers: list[str], rows: list[list[str]], caption: str) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>"
        + "".join(f'<td data-label="{_e(headers[i])}">{_e(v)}</td>' for i, v in enumerate(row))
        + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _linked_table(
    headers: list[str], rows: list[tuple[list[str], str, str]], caption: str
) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td data-label="{_e(headers[i])}">{_e(value)}</td>'
            for i, value in enumerate(values)
        )
        + f'<td data-label="{_e(headers[-1])}"><a href="{_e(href)}">{_e(label)}</a></td></tr>'
        for values, label, href in rows
    )
    return f'<div class="table-wrap"><table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _nav(active: str) -> str:
    return (
        '<nav aria-label="报告分页">'
        + "".join(
            f'<a href="{filename}" {"class=active aria-current=page" if slug == active else ""}>{i}. {label}</a>'
            for i, (slug, label, filename) in enumerate(PAGES, 1)
        )
        + "</nav>"
    )


def _shell(manifest: dict[str, Any], active: str, body: str) -> str:
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
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{label}｜Chromium 与 Moli 评测</title><style>
:root{{--ink:#172033;--muted:#5f6c81;--line:#d7deeb;--panel:#f4f7fb;--accent:#2552c7;--good:#117454;--bad:#b42318}}*{{box-sizing:border-box}}body{{margin:0;background:#edf2f8;color:var(--ink);font:15px/1.68 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}a{{color:var(--accent)}}header{{background:#fff;border-bottom:1px solid var(--line)}}.wrap,main,footer{{max-width:1240px;margin:auto;padding-left:24px;padding-right:24px}}.brand{{padding:22px 0 10px}}.eyebrow{{font-size:13px;color:var(--accent);font-weight:750}}h1{{font-size:30px;line-height:1.25;margin:5px 0}}h2{{font-size:22px;margin:0 0 12px}}h3{{font-size:17px;margin:18px 0 8px}}p,ul,ol{{margin:8px 0}}.muted{{color:var(--muted)}}nav{{display:flex;gap:4px;overflow:auto;padding-bottom:14px}}nav a{{white-space:nowrap;text-decoration:none;color:var(--muted);padding:7px 10px;border-radius:8px}}nav a.active{{background:#e8eeff;color:#173fa8;font-weight:750}}main{{padding-top:20px;padding-bottom:40px}}.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:16px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.metric{{background:var(--panel);border-radius:10px;padding:16px}}.metric strong{{display:block;font-size:25px}}.metric span{{color:var(--muted)}}.call{{border-left:3px solid #9eb2ef;background:var(--panel);padding:10px 12px;margin:8px 0;border-radius:0 8px 8px 0}}.chip{{display:inline-block;font-size:12px;background:#eaf0fb;border-radius:999px;padding:2px 8px;margin:2px}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px;margin:12px 0}}table{{width:100%;border-collapse:collapse;min-width:760px}}caption{{text-align:left;padding:11px 12px;font-weight:750}}th,td{{border-top:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}th{{background:var(--panel);font-size:13px}}details{{border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin:9px 0}}summary{{cursor:pointer;font-weight:750}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5edf8;padding:13px;border-radius:8px;font:12px/1.55 ui-monospace,monospace}}code{{font-family:ui-monospace,monospace}}.trace{{border-left:3px solid #b9c5da;padding-left:12px;margin:10px 0}}.ok{{color:var(--good)}}.bad{{color:var(--bad)}}.note{{border-left:5px solid var(--accent)}}.pager{{display:flex;justify-content:space-between;margin-top:22px}}.filters{{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:10px;background:var(--panel);padding:14px;border-radius:10px;margin:14px 0}}.filters label{{font-size:12px;color:var(--muted);font-weight:700}}.filters select,.filters input{{display:block;width:100%;margin-top:4px;padding:8px 9px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink)}}.filters button{{align-self:end;padding:8px 12px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--accent);font-weight:700;cursor:pointer}}.filter-status{{align-self:end;color:var(--muted)}}.execution-card[hidden]{{display:none}}footer{{padding-bottom:30px;color:var(--muted)}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}.filters{{grid-template-columns:1fr 1fr}}.card{{padding:18px}}h1{{font-size:25px}}}}
</style></head><body><header><div class="wrap"><div class="brand"><div class="eyebrow">Lexmount Browser Provider Evaluation</div><h1>Chromium 与 Moli 浏览器工具调用评测</h1><div class="muted">运行编号：{_e(manifest.get("run_id", "unknown"))} · 第 {index + 1}/{len(PAGES)} 页</div></div>{_nav(active)}</div></header><main>{body}<div class="pager">{prev_link}{next_link}</div></main><footer>报告只陈述本轮可追溯数据；浏览器微基准与 CowAgent 端到端评测分开解释。</footer></body></html>"""


def _functional_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    for engine in ("chromium", "moli"):
        values = (summary.get("functional") or {}).get(engine) or {}
        duration = values.get("duration_ms") or {}
        rows.append(
            [
                engine,
                f"{values.get('result_first_passes', values.get('first_passes', 0))}/{values.get('tasks', 0)}",
                f"{values.get('combined_first_passes', values.get('first_passes', 0))}/{values.get('tasks', 0)}",
                str(values.get("result_recovered", values.get("recovered", 0))),
                f"{values.get('result_final_passes', values.get('final_passes', 0))}/{values.get('tasks', 0)}",
                f"{values.get('combined_final_passes', values.get('final_passes', 0))}/{values.get('tasks', 0)}",
                _stat(duration, "p50"),
                _stat(duration, "mean"),
                _stat(duration, "p95"),
            ]
        )
    return rows


def _correctness_rows(summary: dict[str, Any]) -> list[list[str]]:
    return [row[:6] for row in _functional_rows(summary)]


def _resource_headline_rows(summary: dict[str, Any]) -> list[list[str]]:
    engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    paired_ratios = (
        ((summary.get("resource_comparison") or {}).get("paired_ratios") or {}).get("metrics")
        or {}
    )
    rows = []
    for key, label, divisor, unit in (
        ("duration_ms", "总耗时", 1, "ms"),
        ("cpu_ms", "CPU 时间", 1, "ms"),
        ("memory_mean_bytes", "时间加权平均内存", 1048576, "MiB"),
        ("memory_p95_bytes", "任务内存 P95", 1048576, "MiB"),
        ("network_rx_bytes", "网络接收总量 RX", 1024, "KiB"),
        ("network_tx_bytes", "网络发送总量 TX", 1024, "KiB"),
    ):
        c = (engines.get("chromium") or {}).get(key) or {}
        m = (engines.get("moli") or {}).get(key) or {}
        ratios = paired_ratios.get(key) or {}
        rows.append(
            [
                f"{label} ({unit})",
                _stat(c, "p50", divisor),
                _stat(c, "mean", divisor),
                _stat(c, "p95", divisor),
                _stat(m, "p50", divisor),
                _stat(m, "mean", divisor),
                _stat(m, "p95", divisor),
                _show(ratios.get("p50"), suffix="×"),
                _show(ratios.get("mean"), suffix="×"),
                _show(ratios.get("p95"), suffix="×"),
            ]
        )
    return rows


def _summary_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    f = summary.get("functional") or {}
    c = f.get("chromium") or {}
    m = f.get("moli") or {}
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    moli_failures = max(
        0,
        int(m.get("tasks", 0))
        - int(m.get("result_final_passes", m.get("final_passes", 0))),
    )
    agent_smoke = manifest.get("agent_smoke") or {}
    smoke_html = ""
    if agent_smoke:
        smoke_html = f"""<section class="card"><h2>CowAgent 端到端冒烟测试</h2><p>该测试与资源微基准分开，不参与上表统计。模型 <code>{_e(agent_smoke.get('model'))}</code>，思考强度 <code>{_e(agent_smoke.get('reasoning_effort'))}</code>；同一任务每个浏览器方案只执行 1 次。</p>{_table(["浏览器方案", "结果", "BrowserTool 调用次数", "最终答案"], [[str(item.get('provider')), "通过" if item.get('status') == "pass" else str(item.get('status')), str(item.get('browser_tool_calls')), str(item.get('answer'))] for item in agent_smoke.get('results') or []], "端到端接受性测试")}</section>"""
    recommendation = (
        "本轮建议默认使用 Chromium：它通过了全部任务；Moli 在双方均成功的任务上耗时和内存更低，"
        f"但仍有 {moli_failures} 个任务未通过，可作为重视资源成本且任务已验证兼容时的推荐选项，暂不宜设为默认值。"
    )
    return f"""<section class="card note"><div class="eyebrow">结论摘要</div><h2>结论与产品建议</h2><p><strong>{recommendation}</strong></p><p>这是浏览器工具调用层的确定性微基准，不是 LLM Agent 排名。15 个任务的结果答案唯一，评测程序重放一条预先声明的有效参考路径；结果由程序逐字判定，LLM 不参与成功评分。每个任务先执行 1 次，仅结果失败时最多重试。</p></section><section class="card"><h2>核心指标</h2><div class="grid"><div class="metric"><strong>{c.get("result_final_passes", c.get("final_passes", 0))}/{c.get("tasks", 0)}</strong><span>Chromium 结果正确</span></div><div class="metric"><strong>{m.get("result_final_passes", m.get("final_passes", 0))}/{m.get("tasks", 0)}</strong><span>Moli 结果正确</span></div><div class="metric"><strong>{paired}</strong><span>双方首次结果均正确的配对任务</span></div></div>{_table(["指标", "Chromium P50", "Chromium 平均值", "Chromium P95", "Moli P50", "Moli 平均值", "Moli P95", "Moli / Chromium P50", "Moli / Chromium 平均值", "Moli / Chromium P95"], _resource_headline_rows(summary), "双方成功任务的速度与资源对比")}</section>{smoke_html}<section class="card"><h2>如何解读</h2><ul><li><strong>结果正确率是主指标</strong>；“结果与过程均正确率”单独报告，不与结果正确率混成一个数。</li><li>耗时、内存、CPU、RX/TX 只比较双方首次结果均正确的同任务，重试与失败样本不进入资源基线。</li><li>配对比值先对每个同任务计算 Moli / Chromium，再报告这 {paired} 个比值的 P50、平均值、P95；小于 1 表示 Moli 数值更低。</li><li>本轮只有 {paired} 个成功配对点，且 Moli 失败的 {moli_failures} 个任务被排除，速度结论存在成功样本选择偏差，不能外推为全任务更快。</li><li>完整标准答案、实际输出、原始异常和逐调用执行轨迹均可审计。</li></ul></section>"""


def _difficulty(value: str) -> str:
    return {"simple": "简单", "medium": "中等", "complex": "复杂"}.get(value, value)


def _action_text(call: dict[str, Any]) -> str:
    action = str(call.get("action") or "")
    return {
        "navigate": f"打开 {call.get('url', '')} 并返回页面观察快照",
        "get_text": f"读取 {call.get('selector', '')} 文本",
        "click": f"点击 {call.get('selector', '')}",
        "fill": f"向 {call.get('selector', '')} 填入测试值",
        "select": f"在 {call.get('selector', '')} 选择 {call.get('value', '')}",
        "snapshot": f"读取 {call.get('selector') or 'body'} 当前快照",
        "scroll": f"向 {call.get('direction', 'down')} 滚动 {call.get('amount', 700)} px",
        "back": "浏览器后退",
        "forward": "浏览器前进",
        "press": f"按键 {call.get('key', '')}",
    }.get(action, action)


def _suite_page(manifest: dict[str, Any]) -> str:
    cards = []
    for index, task in enumerate(manifest.get("suite") or [], 1):
        title, purpose = TASK_META.get(
            str(task.get("task_id")), (str(task.get("title")), "确定性浏览器任务")
        )
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
        expected_rows = [[str(name), str(value), "逐字相等"] for name, value in (truth.get("expected_outputs") or {}).items()]
        source = task.get("source") or {}
        source_html = f'<a href="{_e(source.get("url"))}">{_e(source.get("scenario") or source.get("kind"))}</a> · {_e(source.get("license"))}'
        chips = "".join(f'<span class="chip">{_e(tag)}</span>' for tag in task.get("tags") or [])
        cards.append(
            f'<section class="card"><div class="eyebrow">任务 {index:02d}</div><h2>{_e(title)}</h2><p>{_e(purpose)}</p><span class="chip">{_difficulty(str(task.get("complexity")))}</span><span class="chip">{len(calls)} 次 BrowserTool 调用</span><span class="chip">结果唯一 · 参考路径固定</span>{chips}<details><summary>查看调用、ground truth 与来源</summary>{call_html}<h3>参考执行路径</h3><p>本工具层微基准由程序重放以下路径，并检查全部调用成功：<code>{_e(" → ".join(truth.get("expected_actions") or []))}</code>。这不表示网页任务只有这一种解法；自由规划 Agent 应按必要里程碑、禁止动作和步数预算评价过程。</p><h3>结果 ground truth</h3>{_table(["输出字段", "唯一预期值", "判定"], expected_rows, "机器判定答案")}<p><strong>推导：</strong>{_e(truth.get("derivation"))}</p><p><strong>任务来源：</strong>{source_html}</p></details></section>'
        )
    return (
        '<section class="card note"><h2>评测集设计</h2><p>15 个任务按简单、中等、复杂各 5 个分层，使用固定版本的自托管测试页面，覆盖读取、点击、输入、选择、键盘、异步状态、分页、历史、目录、表单、购物车和结算。任务结构参考并部分改编自 Lexbench-Headless-Browser；每题都标注参考执行路径、唯一预期输出、推导与来源。</p><p><strong>答案唯一，路径未必唯一：</strong>结果 ground truth 是唯一字符串；<code>expected_actions</code> 是本轮由评测程序重放的参考路径，不声称是任务的唯一解。</p><p><strong>成功判定：</strong>程序对实际输出和 ground truth 做严格字符串相等判断，不使用 LLM-as-a-Judge。</p></section>'
        + "".join(cards)
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
        ["执行次数", "每任务每个 Provider 首次 1 次"],
        ["失败重试", f"最多 {p.get('max_failure_retries', '?')} 次，可配置 BROWSER_USE_EVAL_FAILURE_RETRIES"],
        ["采样", f"每 {p.get('sample_interval_ms', '?')} ms 采样内存、PID、CPU、网络计数"],
    ]
    return f"""<section class="card note"><h2>步骤的严格定义</h2><p><strong>一步 = 一次 CowAgent BrowserTool 等价调用</strong>，例如 navigate、click、get_text、fill、select、scroll、back 或 forward。等待页面就绪和目标值出现属于该工具调用内部，不单列、不计步。每次调用的参数、耗时、输出和状态写入 trace。</p></section><section class="card"><h2>成功判定：两个独立程序指标</h2><ol><li><strong>结果正确（主指标）：</strong>每个标注输出与 <code>expected_outputs</code> 做严格字符串相等；不存在最低长度、模糊包含或 LLM 主观评分。</li><li><strong>结果与过程均正确（辅指标）：</strong>在结果正确之外，本轮被重放的参考动作序列还必须与 <code>expected_actions</code> 一致，且全部 BrowserTool 调用成功。</li><li><strong>重试规则：</strong>结果首次失败才重试，最多 {p.get('max_failure_retries', '?')} 次；结果首次通过率、恢复数、结果最终正确率和组合正确率分别统计。</li></ol><p><strong>边界：</strong><code>expected_actions</code> 是工具层微基准的固定参考路径，不代表任务只有一种解法。若评测自由规划 CowAgent，应把过程判定改成必要里程碑、禁止动作和步数预算，不能要求整条序列逐步相等。</p></section><section class="card"><h2>模型与执行边界</h2><ul><li><strong>浏览器工具层（本报告主体）：</strong>成功评分不调用模型；按固定参考路径驱动浏览器，以隔离浏览器方案的正确率、耗时和资源成本。</li><li><strong>失败诊断：</strong>规则引擎自动提取失败方法、步骤、selector 和错误类型，不使用模型。</li><li><strong>CowAgent 端到端控制层：</strong><code>bot_type=openai</code>、<code>model=gpt-5.6-sol</code>、<code>reasoning_effort=low</code>，由 Lexmount LiteLLM API 执行，不是 Codex 代替 CowAgent。</li><li><strong>Codex：</strong>只开发、运行和检查工具，不是被测 Agent，也不负责报告中的自动错误分类。</li></ul></section><section class="card"><h2>环境</h2>{_table(["项目", "记录值"], rows, "本轮环境")}</section><section class="card"><h2>执行流程</h2><ol><li>分别启动并预热两个浏览器，浏览器进程和上下文在整轮评测中保持运行。</li><li>任务前清 Cookie、权限、HTTP 缓存、Cache Storage、Service Worker、IndexedDB、来源存储和历史，并创建新页面。</li><li>计时外静置 {p.get("post_reset_settle_ms", "?")} ms，按任务交替内核先后顺序。</li><li>执行一次完整任务并采样；仅结果失败时清理状态后重试。</li><li>资源基线只比较双方首次结果均正确的同任务，重试与失败样本不混入正常路径。</li></ol></section><section class="card"><h2>统计口径</h2><ul><li>P50 与中位数是同一个统计量；总体跨任务报告 P50、平均值和 nearest-rank P95。</li><li>资源比较严格使用双方首次结果均正确的成功任务，实际样本数见结果页；失败任务完全排除。</li><li>配对比值先逐任务计算 Moli / Chromium，再报告比值分布的 P50、平均值、P95；这比只用两个总体 P50 相除更尊重配对关系。</li><li>每个任务仅有一个首次样本，因此逐任务不计算分位数；当前跨任务点不足以证明普遍性能优势。</li><li>内存平均值是时间加权均值；任务内存 P95 来自单次任务时序采样。</li><li>RX = Receive（接收），TX = Transmit（发送）；总量含页面资源与 CDP，速率来自相邻采样点差分。</li></ul></section>"""


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
    comparison = summary.get("resource_comparison") or {}
    engines = comparison.get("by_engine") or {}
    paired_ratios = ((comparison.get("paired_ratios") or {}).get("metrics") or {})
    out = []
    for group, metrics in METRICS:
        rows = []
        for key, label, divisor, unit in metrics:
            c = (engines.get("chromium") or {}).get(key) or {}
            m = (engines.get("moli") or {}).get(key) or {}
            ratios = paired_ratios.get(key) or {}
            rows.append(
                [
                    f"{label} ({unit})",
                    _stat(c, "p50", divisor),
                    _stat(c, "mean", divisor),
                    _stat(c, "p95", divisor),
                    _stat(m, "p50", divisor),
                    _stat(m, "mean", divisor),
                    _stat(m, "p95", divisor),
                    _show(ratios.get("p50"), suffix="×"),
                    _show(ratios.get("mean"), suffix="×"),
                    _show(ratios.get("p95"), suffix="×"),
                ]
            )
        out.append(
            _table(
                [
                    "指标",
                    "Chromium P50",
                    "Chromium 平均值",
                    "Chromium P95",
                    "Moli P50",
                    "Moli 平均值",
                    "Moli P95",
                    "Moli / Chromium P50",
                    "Moli / Chromium 平均值",
                    "Moli / Chromium P95",
                ],
                rows,
                group,
            )
        )
    return "".join(out)


def _execution_details(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    task_specs = {
        str(task.get("task_id")): task for task in manifest.get("suite") or []
    }
    blocks = []
    for task_id, task in (summary.get("by_task") or {}).items():
        title = TASK_META.get(str(task_id), (str(task.get("title") or task_id), ""))[0]
        executions = task.get("attempts") or task.get("failures") or {}
        for engine, attempts in executions.items():
            for attempt in attempts:
                attempt_number = int(attempt.get("attempt") or 1)
                anchor = f"execution-{task_id}-{engine}-{attempt_number}"
                validation = attempt.get("validation") or []
                if not validation:
                    expected_outputs = (
                        (task_specs.get(str(task_id)) or {}).get("ground_truth") or {}
                    ).get("expected_outputs") or {}
                    validation = [
                        {
                            "output": output,
                            "expected": expected,
                            "actual": "未产生（过程失败）",
                            "passed": None,
                        }
                        for output, expected in expected_outputs.items()
                    ]
                validation_html = _table(
                    ["输出", "预期值", "实际值", "结果"],
                    [
                        [
                            str(item.get("output")),
                            str(item.get("expected")),
                            str(item.get("actual")),
                            (
                                "通过"
                                if item.get("passed") is True
                                else "失败"
                                if item.get("passed") is False
                                else "未判定"
                            ),
                        ]
                        for item in validation
                    ],
                    "ground truth 判定明细",
                ) if validation else "<p>该任务未声明结果真值。</p>"
                traces = "".join(
                    f'<div class="trace"><strong>调用 {_e(c.get("index"))} · {_e(c.get("action"))} · {_e(c.get("status"))} · {_show(c.get("duration_ms"), suffix=" ms")}</strong><div>参数</div><pre>{_e(json.dumps(c.get("arguments") or {}, ensure_ascii=False, indent=2))}</pre><div>完整输出</div><pre>{_e(_trace_text(c.get("output") or "（无输出）"))}</pre>'
                    + (
                        f"<div>调用错误</div><pre>{_e(_trace_text(c.get('error')))}</pre>"
                        if c.get("error")
                        else ""
                    )
                    + "</div>"
                    for c in attempt.get("trace") or []
                )
                status = str(attempt.get("status") or "fail")
                result_status = str(attempt.get("result_status") or status)
                process_status = str(attempt.get("process_status") or status)
                combined_status = str(attempt.get("combined_status") or status)
                process_validation = attempt.get("process_validation") or {}
                process_html = _table(
                    ["参考动作序列", "实际动作序列", "全部调用成功", "过程结果"],
                    [[
                        " → ".join(process_validation.get("expected_actions") or []),
                        " → ".join(process_validation.get("actual_actions") or []),
                        "是" if process_validation.get("all_calls_passed") else "否",
                        "通过" if process_validation.get("passed") else "失败",
                    ]],
                    "参考路径执行判定",
                ) if process_validation else "<p>该历史样本未记录独立过程判定。</p>"
                error_category = _classify(str(attempt.get("error") or "")) if status == "fail" else "无错误"
                actions = sorted({str(c.get("action")) for c in attempt.get("trace") or [] if c.get("action") != "validate_assertions"})
                tags = task.get("tags") or []
                result_label = "正确" if result_status == "pass" else "错误"
                process_label = "正确" if process_status == "pass" else "错误"
                blocks.append(
                    f'<div id="{_e(anchor)}" class="execution-card" data-task="{_e(task_id)}" data-engine="{_e(engine)}" data-status="{_e(result_status)}" data-combined-status="{_e(combined_status)}" data-complexity="{_e(task.get("complexity"))}" data-attempt-kind="{"retry" if attempt.get("is_retry") else "primary"}" data-error="{_e(error_category)}" data-actions="{_e(",".join(actions))}" data-tags="{_e(",".join(tags))}" data-duration="{_e(attempt.get("duration_ms") or 0)}"><details><summary>{_e(title)} · {_e(engine)} · {"失败重试" if attempt.get("is_retry") else "首次执行"} · 结果{result_label} / 过程{process_label} · {_show(attempt.get("duration_ms"), suffix=" ms")}</summary>'
                    + (
                        f"<h3>完整错误</h3><pre>{_e(_trace_text(attempt.get('error')))}</pre>"
                        if attempt.get("error")
                        else ""
                    )
                    + f"<h3>结果 ground truth 判定</h3>{validation_html}<h3>参考路径执行判定</h3>{process_html}<h3>完整执行轨迹</h3>{traces or '<p>该样本未记录 trace。</p>'}</details></div>"
                )
    return "".join(blocks) or "<p>没有可展示的执行样本。</p>"


def _task_comparison_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for task_id, task in (summary.get("by_task") or {}).items():
        title = TASK_META.get(str(task_id), (str(task.get("title") or task_id), ""))[0]
        engine_values: dict[str, dict[str, Any]] = {}
        retry_notes = []
        for engine in ("chromium", "moli"):
            attempts = ((task.get("attempts") or {}).get(engine) or [])
            functional = (task.get("functional") or {}).get(engine) or {}
            duration = functional.get("duration_ms") or {}
            result_passed = (
                any(a.get("result_status", a.get("status")) == "pass" for a in attempts)
                if attempts
                else bool(functional.get("result_final_passes", functional.get("final_passes", 0)))
            )
            process_passed = (
                any(a.get("process_status", a.get("status")) == "pass" for a in attempts)
                if attempts
                else bool(functional.get("combined_final_passes", functional.get("final_passes", 0)))
            )
            retries = sum(bool(a.get("is_retry")) for a in attempts)
            recovered = functional.get("result_recovered", functional.get("recovered", 0))
            engine_values[engine] = {
                "status": f"{'通过' if result_passed else '失败'} / {'通过' if process_passed else '失败'}",
                "duration": duration,
            }
            if retries:
                retry_notes.append(
                    f"{engine.capitalize()} 重试 {retries} 次，{'恢复' if recovered else '未恢复'}"
                )
        c50 = engine_values["chromium"]["duration"].get("p50")
        m50 = engine_values["moli"]["duration"].get("p50")
        ratio = (
            m50 / c50
            if isinstance(c50, (int, float))
            and c50
            and isinstance(m50, (int, float))
            else None
        )
        rows.append(
            [
                title,
                _difficulty(str(task.get("complexity") or "")),
                engine_values["chromium"]["status"],
                engine_values["moli"]["status"],
                _stat(engine_values["chromium"]["duration"], "p50"),
                _stat(engine_values["moli"]["duration"], "p50"),
                _show(ratio, suffix="×"),
                "；".join(retry_notes) or "无",
            ]
        )
    return rows


def _execution_explorer(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    total_attempts = sum(
        (values or {}).get("physical_attempts", 0)
        for values in (summary.get("functional") or {}).values()
    )
    task_options = "".join(f'<option value="{_e(task_id)}">{_e(task.get("title") or task_id)}</option>' for task_id, task in (summary.get("by_task") or {}).items())
    action_values = sorted({str(c.get("action")) for task in (summary.get("by_task") or {}).values() for attempts in (task.get("attempts") or {}).values() for attempt in attempts for c in attempt.get("trace") or [] if c.get("action") != "validate_assertions"})
    action_options = "".join(f'<option value="{_e(action)}">{_e(action)}</option>' for action in action_values)
    tag_values = sorted({str(tag) for task in (summary.get("by_task") or {}).values() for tag in task.get("tags") or []})
    tag_options = "".join(f'<option value="{_e(tag)}">{_e(tag)}</option>' for tag in tag_values)
    filters = f'''<div class="filters" aria-label="执行样本筛选">
      <label>任务<select id="f-task"><option value="">全部任务</option>{task_options}</select></label>
      <label>Provider<select id="f-engine"><option value="">全部 Provider</option><option>chromium</option><option>moli</option></select></label>
      <label>结果<select id="f-status"><option value="">全部结果</option><option value="pass">正确</option><option value="fail">错误</option></select></label>
      <label>结果与过程<select id="f-combined"><option value="">全部</option><option value="pass">均正确</option><option value="fail">未同时正确</option></select></label>
      <label>执行类型<select id="f-attempt"><option value="">首次与重试</option><option value="primary">首次执行</option><option value="retry">失败重试</option></select></label>
      <label>难度<select id="f-complexity"><option value="">全部难度</option><option value="simple">简单</option><option value="medium">中等</option><option value="complex">复杂</option></select></label>
      <label>BrowserTool 方法<select id="f-action"><option value="">全部方法</option>{action_options}</select></label>
      <label>任务标签<select id="f-tag"><option value="">全部标签</option>{tag_options}</select></label>
      <label>错误类别<select id="f-error"><option value="">全部类别</option><option value="无错误">无错误</option><option value="交互目标超时/不可见">交互目标超时/不可见</option><option value="超时">超时</option><option value="结果不匹配">结果不匹配</option><option value="导航/网络">导航/网络</option><option value="定位器">定位器</option><option value="其他">其他</option></select></label>
      <label>耗时<select id="f-duration"><option value="">全部耗时</option><option value="lt1000">少于 1 秒</option><option value="1000-5000">1–5 秒</option><option value="gte5000">5 秒及以上</option></select></label>
      <label>全文搜索<input id="f-query" type="search" placeholder="任务、输出或错误"></label>
      <button type="button" id="f-failures">仅看错误</button><button type="button" id="f-reset">重置筛选</button><span class="filter-status" id="f-count" aria-live="polite"></span>
    </div>'''
    script = '''<script>(()=>{const cards=[...document.querySelectorAll('.execution-card')];const ids=['task','engine','status','combined','attempt','complexity','action','tag','error','duration','query'];const el=Object.fromEntries(ids.map(x=>[x,document.getElementById('f-'+x)]));function apply(){let n=0;for(const c of cards){const d=Number(c.dataset.duration||0),duration=el.duration.value;const ok=(!el.task.value||c.dataset.task===el.task.value)&&(!el.engine.value||c.dataset.engine===el.engine.value)&&(!el.status.value||c.dataset.status===el.status.value)&&(!el.combined.value||c.dataset.combinedStatus===el.combined.value)&&(!el.attempt.value||c.dataset.attemptKind===el.attempt.value)&&(!el.complexity.value||c.dataset.complexity===el.complexity.value)&&(!el.action.value||c.dataset.actions.split(',').includes(el.action.value))&&(!el.tag.value||c.dataset.tags.split(',').includes(el.tag.value))&&(!el.error.value||c.dataset.error===el.error.value)&&(!duration||(duration==='lt1000'&&d<1000)||(duration==='1000-5000'&&d>=1000&&d<5000)||(duration==='gte5000'&&d>=5000))&&(!el.query.value||c.textContent.toLowerCase().includes(el.query.value.toLowerCase()));c.hidden=!ok;if(ok)n++;}document.getElementById('f-count').textContent=`显示 ${n} / ${cards.length} 个样本`;}function reveal(){const card=location.hash?document.querySelector(location.hash):null;if(card&&card.classList.contains('execution-card')){for(const x of ids)el[x].value='';apply();card.querySelector('details').open=true;card.style.outline='3px solid #f4b400';card.scrollIntoView({block:'center'});}}for(const x of ids)el[x].addEventListener(x==='query'?'input':'change',apply);document.getElementById('f-failures').onclick=()=>{el.status.value='fail';apply()};document.getElementById('f-reset').onclick=()=>{for(const x of ids)el[x].value='';apply()};addEventListener('hashchange',reveal);apply();reveal()})()</script>'''
    return f"""<section class="card"><h2>执行记录与原始证据</h2><p>共 {total_attempts} 次实际执行，默认折叠。可按任务、Provider、结果、执行类型、难度、BrowserTool 方法、标签、错误类别、耗时和全文内容组合筛选。</p>{filters}<div id="execution-list">{_execution_details(manifest, summary)}</div>{script}</section>"""


def _results_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    return f"""<section class="card"><h2>总体正确性</h2><p>结果正确是主指标；“结果与过程均正确”要求结果正确、参考路径完整且全部调用成功，两项分开统计。本表不混入各自成功集合上的耗时，性能只在下方双方成功的同任务中比较。</p>{_table(["Provider", "首次结果正确", "首次结果与过程均正确", "重试恢复", "最终结果正确", "最终结果与过程均正确"], _correctness_rows(summary), "正确性结果")}</section><section class="card"><h2>双方成功任务的资源对比</h2><p>只包含双方首次结果均正确的同任务，共 {paired} 对；失败任务和重试记录不参与资源比较。各指标先分别统计两个 Provider 的 P50、平均值、P95，再统计同任务 Moli / Chromium 比值的 P50、平均值、P95。除比值外，各资源指标均为越低越好；比值小于 1 表示 Moli 较低。</p>{_metric_tables(summary)}</section>"""


def _classify(error: str) -> str:
    text = error.lower()
    if "timeout" in text and ("visible" in text or "click" in text):
        return "交互目标超时/不可见"
    if "timeout" in text:
        return "超时"
    if "exact mismatch" in text:
        return "结果不匹配"
    if "net::" in text or "navigation" in text:
        return "导航/网络"
    if "selector" in text or "locator" in text:
        return "定位器"
    return "其他"


def _analysis_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    functional = summary.get("functional") or {}
    comparison = summary.get("resource_comparison") or {}
    paired_ratios = ((comparison.get("paired_ratios") or {}).get("metrics") or {})
    c_func, m_func = functional.get("chromium") or {}, functional.get("moli") or {}
    paired_count = int(comparison.get("intersection_attempts") or 0)
    moli_failures = int(m_func.get("tasks", 0)) - int(
        m_func.get("result_final_passes", m_func.get("final_passes", 0))
    )

    def paired(key: str, stat: str) -> float | None:
        value = (paired_ratios.get(key) or {}).get(stat)
        return value if isinstance(value, (int, float)) else None

    diagnostics = summary.get("failure_diagnostics") or {}
    rule = diagnostics.get("rule_engine") or {}
    groups = rule.get("groups") or []
    counts: dict[str, list[int]] = {}
    diagnosis_rows: list[tuple[list[str], str, str]] = []
    diagnosis_details: list[str] = []
    for group in groups:
        category = str(group.get("category") or "其他")
        counts.setdefault(category, [0, 0])
        counts[category][0] += 1
        counts[category][1] += len(group.get("attempts") or [])
        attempts = group.get("attempts") or []
        first_attempt = int((attempts[0] if attempts else {}).get("attempt") or 1)
        anchor = (
            "#execution-"
            f"{group.get('task_id')}-{group.get('engine')}-{first_attempt}"
        )
        diagnosis_rows.append(
            (
                [
                    str(group.get("task_title") or group.get("task_id")),
                    str(group.get("engine")),
                    str(len(attempts)),
                    f"第 {group.get('failed_step')} 步 · {group.get('failed_action')}",
                    str(group.get("selector") or "—"),
                    category,
                ],
                "查看原始错误与 trace",
                anchor,
            )
        )
        diagnosis_details.append(
            f'<details><summary>{_e(group.get("task_title"))} · {_e(group.get("engine"))} · '
            f'{_e(group.get("category"))}</summary><p><strong>程序证据：</strong>'
            f'{_e(group.get("evidence") or "无")}</p><p><strong>定位结论：</strong>'
            f'失败发生在第 {_e(group.get("failed_step"))} 次 BrowserTool 调用 '
            f'<code>{_e(group.get("failed_action"))}</code>；目标 '
            f'<code>{_e(group.get("selector") or "未记录")}</code> 已被定位，但在等待时间内不可见。'
            f'首次执行和失败重试共复现 {len(attempts)} 次。该证据可定位兼容性问题，'
            '但不足以单独证明根因位于浏览器内核、适配层或测试页面。</p><p><a href="'
            f'{_e(anchor)}">'
            "查看完整执行步骤、输出与原始错误</a></p></details>"
        )
    category_rows = [[name, str(values[0]), str(values[1])] for name, values in counts.items()]
    if not category_rows:
        category_rows = [["无失败", "0", "0"]]
    ratio_rows = [
        [
            "总耗时",
            _show(paired("duration_ms", "p50"), suffix="×"),
            _show(paired("duration_ms", "mean"), suffix="×"),
            _show(paired("duration_ms", "p95"), suffix="×"),
        ],
        [
            "CPU 时间",
            _show(paired("cpu_ms", "p50"), suffix="×"),
            _show(paired("cpu_ms", "mean"), suffix="×"),
            _show(paired("cpu_ms", "p95"), suffix="×"),
        ],
        [
            "时间加权平均内存",
            _show(paired("memory_mean_bytes", "p50"), suffix="×"),
            _show(paired("memory_mean_bytes", "mean"), suffix="×"),
            _show(paired("memory_mean_bytes", "p95"), suffix="×"),
        ],
    ]
    details_html = "".join(diagnosis_details) or "<p>本轮没有失败样本。</p>"
    task_comparison = _table(
        ["任务", "难度", "Chromium（结果 / 过程）", "Moli（结果 / 过程）", "Chromium 耗时 (ms)", "Moli 耗时 (ms)", "Moli / Chromium", "重试"],
        _task_comparison_rows(summary),
        "逐任务正确性、耗时与重试",
    )
    return f"""<section class="card note"><h2>本轮结论</h2><ul><li><strong>结果正确：</strong>Chromium {c_func.get('result_final_passes', c_func.get('final_passes', 0))}/{c_func.get('tasks', 0)}；Moli {m_func.get('result_final_passes', m_func.get('final_passes', 0))}/{m_func.get('tasks', 0)}。结果正确是主指标。</li><li><strong>结果与过程均正确：</strong>Chromium {c_func.get('combined_final_passes', c_func.get('final_passes', 0))}/{c_func.get('tasks', 0)}；Moli {m_func.get('combined_final_passes', m_func.get('final_passes', 0))}/{m_func.get('tasks', 0)}。</li><li><strong>性能：</strong>只分析双方首次结果均正确的 {paired_count} 对任务；失败与重试完全排除。</li></ul>{_table(["Moli / Chromium 配对指标", "P50", "平均值", "P95"], ratio_rows, "同任务配对比值分布")}</section><section class="card"><h2>逐任务对比</h2><p>每个任务只占一行，同时展示两个 Provider 的结果、过程、耗时和重试，避免在不同表格中重复列出同一任务。</p>{task_comparison}</section><section class="card"><h2>为什么本轮 Moli 看起来更快</h2><p>当前结果与早期公网报告不能直接比较：本轮使用本地固定测试页面，两个浏览器进程均在预热后复用，任务之间清理浏览状态；同时使用较短的确定性调用链，并修正了早期执行器与 CowAgent <code>get_text</code> 不一致的问题。更重要的是，资源表只保留双方成功任务：Moli 未通过的 {moli_failures} 个动态控件任务被排除，形成成功样本选择偏差。</p><p>因此应表述为“在本轮 {paired_count} 个双方成功任务中，Moli 的同任务耗时比值 P50、平均值、P95 如上”，不能表述为“Moli 对全部任务普遍更快”。每个任务只有一个首次执行样本，也不足以建立稳定的性能置信区间。</p></section><section class="card"><h2>失败分布与可追溯证据</h2><p>错误类别由评测程序根据异常文本、失败步骤、BrowserTool 方法和选择器自动生成，不是人工或 LLM 判断；这里记录的是失败现象与定位线索，不冒充已经验证的根因。</p>{_table(["规则类别", "去重失败组", "实际失败次数"], category_rows, "程序化失败分类")}{_linked_table(["任务", "Provider", "失败次数", "失败方法", "选择器", "规则类别", "证据"], diagnosis_rows, "去重后的失败现象与证据入口")}<h3>定位依据</h3>{details_html}</section>{_execution_explorer(manifest, summary)}<section class="card"><h2>产品决策边界</h2><ol><li>默认 Provider 先看首次结果正确率，再看结果与过程均正确率、重试恢复、时延和成本。</li><li>资源结论只来自双方首次结果正确的同任务，不能外推成所有网站上的固定倍数。</li><li>当前固定参考路径适合隔离 Provider；自由规划 Agent 的过程评价必须改为里程碑和约束。</li><li>程序化错误分类是定位线索，不替代针对浏览器实现和适配层的最小复现。</li></ol></section>"""


def _appendix_page(manifest: dict[str, Any]) -> str:
    p = manifest.get("protocol") or {}
    fields = [
        ["task", "ground_truth / source / expected_actions", "唯一结果、固定参考路径与来源"],
        ["attempt", "result_status / process_status / combined_status", "结果主指标与结果+过程辅指标"],
        ["attempt", "is_retry / validation / trace / error", "首次/重试、判定与完整证据"],
        ["resource", "memory mean/p95/peak/retained/MiB·s", "内存水平、尾部、峰值、保留量和积分"],
        ["resource", "CPU / network total/average/p95/peak", "计算与网络时序压力"],
        ["summary", "paired intersection / paired ratios", "双方成功任务及逐任务比值 P50/平均值/P95"],
        ["diagnostics", "rule_engine", "程序化失败分类、证据与原始执行记录入口"],
    ]
    return f"""<section class="card"><h2>复现步骤</h2><ol><li>准备 Docker Engine、Python 3.11+、至少 4 CPU/6 GiB 内存。</li><li>运行 <code>./examples/browser_use/run.sh</code>；每任务每内核首次执行 1 次。</li><li>改变失败重试次数：<code>BROWSER_USE_EVAL_FAILURE_RETRIES=1 ./examples/browser_use/run.sh</code>。</li><li>检查 <code>attempts.jsonl</code>、<code>manifest.json</code>、<code>summary.json</code> 与分页 HTML。</li></ol></section><section class="card"><h2>数据字段</h2>{_table(["对象", "关键字段", "用途"], fields, "数据结构")}</section><section class="card"><h2>限制</h2><ul><li>固定测试页面提高可重复性，但不能代替真实公网兼容性评测；两类测试应分开报告。</li><li>每任务只有一个首次样本，逐任务不报告分位数；总体分位数是跨任务分布。</li><li>双方成功任务会排除任一内核失败的任务，资源对比存在选择偏差，必须与正确率一起阅读。</li><li>采样间隔 {p.get("sample_interval_ms", "?")} ms，短尖峰可能遗漏。</li><li>浏览器进程保持运行且任务间清理状态，但分配器保留内存不等于缓存污染。</li><li>这是工具调用层，不是 CowAgent + LiteLLM 的 Agent 规划质量评测。</li></ul></section><section class="card"><h2>原始环境 JSON</h2><details><summary>展开</summary><pre>{_e(json.dumps(manifest.get("environment") or {}, ensure_ascii=False, indent=2, sort_keys=True))}</pre></details></section>"""


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# Chromium 与 Moli 浏览器工具调用评测",
        "",
        f"运行编号：`{manifest.get('run_id', 'unknown')}`",
        "",
        "这是确定性的 BrowserTool 等价调用微基准；成功判定和失败分类均由程序完成。",
        "",
        "| 内核 | 首次结果正确 | 首次结果与过程均正确 | 结果重试恢复 | 最终结果正确 | 最终结果与过程均正确 | P50 ms | 平均值 ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in _functional_rows(summary))
    lines.extend(
        ["", "```bash", "BROWSER_USE_EVAL_FAILURE_RETRIES=1 ./examples/browser_use/run.sh", "```", ""]
    )
    return "\n".join(lines)


def write_latest_report(
    reports_dir: str | Path, *, manifest: dict[str, Any], summary: dict[str, Any]
) -> Path:
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    keep = {f"{REPORT_STEM}.md", *(filename for _, _, filename in PAGES)}
    for pattern in ("browser-use-evaluation-*.md", "browser-use-evaluation-*.html"):
        for old in root.glob(pattern):
            if old.name not in keep:
                old.unlink()
    md_path = root / f"{REPORT_STEM}.md"
    md_path.write_text(_markdown(manifest, summary), encoding="utf-8")
    renderers = {
        "index": lambda: _summary_page(manifest, summary),
        "suite": lambda: _suite_page(manifest),
        "method": lambda: _method_page(manifest),
        "results": lambda: _results_page(manifest, summary),
        "analysis": lambda: _analysis_page(manifest, summary),
        "appendix": lambda: _appendix_page(manifest),
    }
    for slug, _, filename in PAGES:
        (root / filename).write_text(_shell(manifest, slug, renderers[slug]()), encoding="utf-8")
    return md_path
