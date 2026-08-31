"""Chinese paginated evidence-first browser benchmark report."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

REPORT_STEM = "browser-use-evaluation-latest"
PAGES = tuple(
    (slug, label, f"{REPORT_STEM}{suffix}.html")
    for slug, label, suffix in (
        ("index", "结论摘要", ""),
        ("suite", "评测集", "-suite"),
        ("method", "环境与方法", "-method"),
        ("results", "结果与诊断", "-results"),
        ("analysis", "结果分析", "-analysis"),
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
:root{{--ink:#172033;--muted:#5f6c81;--line:#d7deeb;--panel:#f4f7fb;--accent:#2552c7;--good:#117454;--bad:#b42318}}*{{box-sizing:border-box}}body{{margin:0;background:#edf2f8;color:var(--ink);font:15px/1.68 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}a{{color:var(--accent)}}header{{background:#fff;border-bottom:1px solid var(--line)}}.wrap,main,footer{{max-width:1240px;margin:auto;padding-left:24px;padding-right:24px}}.brand{{padding:22px 0 10px}}.eyebrow{{font-size:13px;color:var(--accent);font-weight:750}}h1{{font-size:30px;line-height:1.25;margin:5px 0}}h2{{font-size:22px;margin:0 0 12px}}h3{{font-size:17px;margin:18px 0 8px}}p,ul,ol{{margin:8px 0}}.muted{{color:var(--muted)}}nav{{display:flex;gap:4px;overflow:auto;padding-bottom:14px}}nav a{{white-space:nowrap;text-decoration:none;color:var(--muted);padding:7px 10px;border-radius:8px}}nav a.active{{background:#e8eeff;color:#173fa8;font-weight:750}}main{{padding-top:20px;padding-bottom:40px}}.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:16px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.metric{{background:var(--panel);border-radius:10px;padding:16px}}.metric strong{{display:block;font-size:25px}}.metric span{{color:var(--muted)}}.call{{border-left:3px solid #9eb2ef;background:var(--panel);padding:10px 12px;margin:8px 0;border-radius:0 8px 8px 0}}.chip{{display:inline-block;font-size:12px;background:#eaf0fb;border-radius:999px;padding:2px 8px;margin:2px}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px;margin:12px 0}}table{{width:100%;border-collapse:collapse;min-width:760px}}caption{{text-align:left;padding:11px 12px;font-weight:750}}th,td{{border-top:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}th{{background:var(--panel);font-size:13px}}details{{border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin:9px 0}}summary{{cursor:pointer;font-weight:750}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5edf8;padding:13px;border-radius:8px;font:12px/1.55 ui-monospace,monospace}}code{{font-family:ui-monospace,monospace}}.trace{{border-left:3px solid #b9c5da;padding-left:12px;margin:10px 0}}.ok{{color:var(--good)}}.note{{border-left:5px solid var(--accent)}}.pager{{display:flex;justify-content:space-between;margin-top:22px}}footer{{padding-bottom:30px;color:var(--muted)}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}.card{{padding:18px}}h1{{font-size:25px}}}}
</style></head><body><header><div class="wrap"><div class="brand"><div class="eyebrow">Lexmount Browser Provider Evaluation</div><h1>Chromium 与 Moli 浏览器工具调用评测</h1><div class="muted">运行编号：{_e(manifest.get("run_id", "unknown"))} · 第 {index + 1}/{len(PAGES)} 页</div></div>{_nav(active)}</div></header><main>{body}<div class="pager">{prev_link}{next_link}</div></main><footer>报告只陈述本轮可追溯数据；浏览器微基准与 CowAgent 端到端评测分开解释。</footer></body></html>"""


def _functional_rows(summary: dict[str, Any]) -> list[list[str]]:
    rows = []
    for engine in ("chromium", "moli"):
        values = (summary.get("functional") or {}).get(engine) or {}
        duration = values.get("duration_ms") or {}
        rows.append(
            [
                engine,
                f"{values.get('passes', 0)}/{values.get('attempts', 0)}",
                _rate(values),
                _stat(duration, "p50"),
                _stat(duration, "mean"),
                _stat(duration, "p95"),
            ]
        )
    return rows


def _summary_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    f = summary.get("functional") or {}
    c = f.get("chromium") or {}
    m = f.get("moli") or {}
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    return f"""<section class="card note"><div class="eyebrow">结论摘要</div><h2>结论与产品建议</h2><p>这是浏览器工具调用层的可重复微基准，不是 LLM Agent 排名。两个暖浏览器执行相同、预先声明的 BrowserTool 等价调用；内部等待不计为一步。功能结果使用全部尝试，资源只比较双方均成功的同任务同轮次。</p></section><section class="card"><div class="grid"><div class="metric"><strong>{c.get("passes", 0)}/{c.get("attempts", 0)}</strong><span>Chromium 成功/尝试</span></div><div class="metric"><strong>{m.get("passes", 0)}/{m.get("attempts", 0)}</strong><span>Moli 成功/尝试</span></div><div class="metric"><strong>{paired}</strong><span>双方均成功配对样本</span></div></div>{_table(["内核", "成功/尝试", "成功率", "耗时 P50 (ms)", "mean (ms)", "P95 (ms)"], _functional_rows(summary), "总体功能与耗时")}</section><section class="card"><h2>产品判断边界</h2><ul><li>默认 provider 先看成功率，再看速度与成本。</li><li>Moli 是追求更低计算与内存成本的推荐项；成为默认值之前，真实任务成功率应接近 Chromium。</li><li>完整任务调用、原始异常、逐调用轨迹和规则化诊断均在“结果与诊断”页。</li></ul></section>"""


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
            str(task.get("task_id")), (str(task.get("title")), "公开真实任务")
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
        assertions = "".join(
            f"<li><code>{_e(json.dumps(a, ensure_ascii=False))}</code></li>"
            for a in task.get("assertions") or []
        )
        chips = "".join(f'<span class="chip">{_e(tag)}</span>' for tag in task.get("tags") or [])
        cards.append(
            f'<section class="card"><div class="eyebrow">任务 {index:02d}</div><h2>{_e(title)}</h2><p>{_e(purpose)}</p><span class="chip">{len(calls)} 次 BrowserTool 调用</span>{chips}<details><summary>查看完整调用与断言</summary>{call_html}<h3>成功断言</h3><ul>{assertions}</ul></details></section>'
        )
    return (
        '<section class="card note"><h2>评测集设计</h2><p>10 个任务覆盖静态页、动态列表、包索引、论文、代码仓库、文档跳转、历史记录、长标准文档、分页和表单。主站点与任务目标均不重复；定位器等待只是工具内部实现，不单列、不计步。</p></section>'
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
        ["重复", f"默认 {p.get('repeats', '?')} 次，可配置 BROWSER_USE_EVAL_REPEATS"],
        ["采样", f"每 {p.get('sample_interval_ms', '?')} ms 采样内存、PID、CPU、网络计数"],
    ]
    return f"""<section class="card note"><h2>步骤的严格定义</h2><p><strong>一步 = 一次 CowAgent BrowserTool 等价调用</strong>，例如 navigate、click、get_text、fill、select、scroll、back 或 forward。等待页面就绪和定位器可用不是 Agent 的独立决策，不计步。每次调用的参数、耗时、输出和状态写入 trace。</p></section><section class="card"><h2>模型与执行边界</h2><ul><li><strong>本报告：</strong>不调用 CowAgent、LiteLLM 或 Codex 模型；按固定序列驱动浏览器，只测 provider/工具执行层。</li><li><strong>本机四个 CowAgent 配置：</strong><code>bot_type=openai</code>、<code>model=gpt-5.4-mini</code>、<code>open_ai_api_base=https://litellm.local.lexmount.net/v1</code>。真实对话由你们的 LiteLLM API 执行，不是 Codex 代替 CowAgent。</li><li><strong>Codex：</strong>只开发、运行和检查工具，不是被测 Agent。</li></ul><p>规划步骤、重试与 LLM token 必须由 CowAgent + LiteLLM 端到端层另测，不能与本微基准混算。</p></section><section class="card"><h2>环境</h2>{_table(["项目", "记录值"], rows, "本轮环境")}</section><section class="card"><h2>执行流程</h2><ol><li>分别启动并预热两个浏览器，浏览器与 context 全程不关闭。</li><li>任务前清 Cookie、权限、HTTP 缓存、Cache Storage、Service Worker、IndexedDB、来源存储和历史；替换 page。</li><li>计时外静置 {p.get("post_reset_settle_ms", "?")} ms，按轮次交替先后顺序。</li><li>计时内执行完整工具调用并采样容器；最后验证输出。</li><li>功能统计保留全部尝试；资源只比较双方都成功的配对样本。</li></ol></section><section class="card"><h2>统计口径</h2><ul><li>P50 与中位数是同一个统计量；同时报告 mean 和 nearest-rank P95。</li><li>每任务 n={p.get("repeats", "?")} 时，任务级 P95 等于最大值，仅用于暴露尾部异常。</li><li>内存 mean 是时间加权均值；任务内存 P95 先对单次时序样本求 P95，再跨任务汇总。</li><li>RX = Receive，TX = Transmit；总量含网页资源与 CDP。速率 P95/峰值来自相邻采样点差分。</li></ul></section>"""


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
    engines = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    out = []
    for group, metrics in METRICS:
        rows = []
        for key, label, divisor, unit in metrics:
            c = (engines.get("chromium") or {}).get(key) or {}
            m = (engines.get("moli") or {}).get(key) or {}
            rows.append(
                [
                    f"{label} ({unit})",
                    _stat(c, "p50", divisor),
                    _stat(c, "mean", divisor),
                    _stat(c, "p95", divisor),
                    _stat(m, "p50", divisor),
                    _stat(m, "mean", divisor),
                    _stat(m, "p95", divisor),
                ]
            )
        out.append(
            _table(
                [
                    "指标",
                    "Chromium P50",
                    "Chromium mean",
                    "Chromium P95",
                    "Moli P50",
                    "Moli mean",
                    "Moli P95",
                ],
                rows,
                group,
            )
        )
    return "".join(out)


def _execution_details(summary: dict[str, Any]) -> str:
    blocks = []
    for task_id, task in (summary.get("by_task") or {}).items():
        title = TASK_META.get(str(task_id), (str(task.get("title") or task_id), ""))[0]
        executions = task.get("attempts") or task.get("failures") or {}
        for engine, attempts in executions.items():
            for attempt in attempts:
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
                blocks.append(
                    f"<details><summary>{_e(title)} · {_e(engine)} · 第 {_e(attempt.get('attempt'))} 次 · {_e(attempt.get('status') or 'fail')} · {_show(attempt.get('duration_ms'), suffix=' ms')}</summary>"
                    + (
                        f"<h3>完整错误</h3><pre>{_e(_trace_text(attempt.get('error')))}</pre>"
                        if attempt.get("error")
                        else ""
                    )
                    + f"<h3>完整执行轨迹</h3>{traces or '<p>该样本未记录 trace。</p>'}</details>"
                )
    return "".join(blocks) or "<p>没有可展示的执行样本。</p>"


def _results_page(summary: dict[str, Any]) -> str:
    rows = []
    for task_id, task in (summary.get("by_task") or {}).items():
        title = TASK_META.get(str(task_id), (str(task.get("title") or task_id), ""))[0]
        for engine in ("chromium", "moli"):
            v = (task.get("functional") or {}).get(engine) or {}
            d = v.get("duration_ms") or {}
            failures = (task.get("failures") or {}).get(engine) or []
            rows.append(
                [
                    title,
                    engine,
                    f"{v.get('passes', 0)}/{v.get('attempts', 0)}",
                    _stat(d, "p50"),
                    _stat(d, "mean"),
                    _stat(d, "p95"),
                    str(len(failures)),
                ]
            )
    paired = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    return f"""<section class="card"><h2>总体结果</h2>{_table(["内核", "成功/尝试", "成功率", "P50 (ms)", "mean (ms)", "P95 (ms)"], _functional_rows(summary), "功能结果")}</section><section class="card"><h2>配对资源结果</h2><p>只包含双方均成功的同任务同轮次，共 {paired} 对；所有指标给出跨样本 P50、mean、P95。</p>{_metric_tables(summary)}</section><section class="card"><h2>逐任务结果</h2>{_table(["任务", "内核", "成功/尝试", "P50 (ms)", "mean (ms)", "P95 (ms)", "失败数"], rows, "逐任务功能与耗时")}</section><section class="card"><h2>完整执行步骤、输出与错误</h2><p>60 个执行样本默认折叠。展开任一条后，可看到每次 BrowserTool 调用的参数、耗时、状态与完整输出；失败样本还会展示未截断错误和调用错误。</p>{_execution_details(summary)}</section>"""


def _classify(error: str) -> str:
    text = error.lower()
    if "timeout" in text:
        return "超时/可见性"
    if "does not contain" in text or "expected at least" in text:
        return "输出断言"
    if "net::" in text or "navigation" in text:
        return "导航/网络"
    if "selector" in text or "locator" in text:
        return "定位器"
    return "其他"


def _analysis_page(summary: dict[str, Any]) -> str:
    failures = [
        (task_id, engine, _classify(str(f.get("error") or "")))
        for task_id, task in (summary.get("by_task") or {}).items()
        for engine, items in (task.get("failures") or {}).items()
        for f in items
    ]
    counts = Counter(category for _, _, category in failures)
    categories = [[k, str(v)] for k, v in counts.most_common()] or [["无失败", "0"]]
    samples = [[task, engine, category] for task, engine, category in failures] or [
        ["—", "—", "无失败"]
    ]
    return f"""<section class="card note"><h2>分析原则</h2><p>先看全部尝试成功率，再看成功交集资源。下面分类由异常文本规则生成，是定位线索，不替代逐条 trace。</p></section><section class="card"><h2>错误分布</h2>{_table(["类别", "次数"], categories, "规则化错误分类")}{_table(["任务 ID", "内核", "类别"], samples, "失败样本")}</section><section class="card"><h2>决策依据</h2><ol><li>默认 provider 选择成功率达到门槛且失败模式可控者，而不是只看最快一次或最低峰值。</li><li>生产密度优先看时间加权平均内存、任务内存 P95、CPU 与 MiB·s，峰值只是补充。</li><li>网络总量回答“传了多少”，平均/P95 速率回答“传输压力”；两者都含 CDP。</li><li>本轮没有模型规划，不能回答哪个内核让 CowAgent 少思考、少重试或少用 LLM token。</li></ol></section>"""


def _appendix_page(manifest: dict[str, Any]) -> str:
    p = manifest.get("protocol") or {}
    return f"""<section class="card"><h2>复现步骤</h2><ol><li>准备 Docker Engine、Python 3.11+、至少 4 CPU/6 GiB 内存及公网。</li><li>运行 <code>./examples/browser_use/run.sh</code>；默认每任务 {p.get("repeats", 3)} 次。</li><li>改变重复次数：<code>BROWSER_USE_EVAL_REPEATS=3 ./examples/browser_use/run.sh</code>。</li><li>检查 <code>attempts.jsonl</code>、<code>manifest.json</code>、<code>summary.json</code> 与分页 HTML。</li></ol></section><section class="card"><h2>数据字段</h2>{_table(["对象", "关键字段", "用途"], [["attempt", "trace / error / duration_ms", "未截断错误与逐工具调用证据"], ["resource", "memory mean/p95/peak/retained/MiB·s", "内存水平、尾部、峰值、保留量和积分"], ["resource", "CPU total/average/p95/peak", "CPU 总量与时序压力"], ["resource", "network total/average/p95/peak", "网络总量与速率压力"], ["summary", "functional / paired intersection", "功能全样本与成功交集分离"]], "数据结构")}</section><section class="card"><h2>限制</h2><ul><li>公网会波动；交替与重复不能完全消除外部变化。</li><li>每任务 {p.get("repeats", 3)} 次，任务级 P95 等于最大值。</li><li>采样间隔 {p.get("sample_interval_ms", "?")} ms，短尖峰可能遗漏。</li><li>浏览器不关闭且状态清理，但分配器保留内存不等于缓存污染。</li><li>这是工具调用层，不是 CowAgent + LiteLLM 端到端层。</li></ul></section><section class="card"><h2>原始环境 JSON</h2><details><summary>展开</summary><pre>{_e(json.dumps(manifest.get("environment") or {}, ensure_ascii=False, indent=2, sort_keys=True))}</pre></details></section>"""


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        "# Chromium 与 Moli 浏览器工具调用评测",
        "",
        f"运行编号：`{manifest.get('run_id', 'unknown')}`",
        "",
        "这是确定性的 BrowserTool 等价调用微基准，不调用 CowAgent、LiteLLM 或 Codex 模型。",
        "",
        "| 内核 | 成功/尝试 | 成功率 | P50 ms | mean ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in _functional_rows(summary))
    lines.extend(
        ["", "```bash", "BROWSER_USE_EVAL_REPEATS=3 ./examples/browser_use/run.sh", "```", ""]
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
        "results": lambda: _results_page(summary),
        "analysis": lambda: _analysis_page(summary),
        "appendix": lambda: _appendix_page(manifest),
    }
    for slug, _, filename in PAGES:
        (root / filename).write_text(_shell(manifest, slug, renderers[slug]()), encoding="utf-8")
    return md_path
