"""Latest-only Chinese Markdown and paginated formal HTML report rendering."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

REPORT_STEM = "browser-use-evaluation-latest"
PAGES = (
    ("index", "结论摘要", f"{REPORT_STEM}.html"),
    ("suite", "评测集", f"{REPORT_STEM}-suite.html"),
    ("method", "环境与方法", f"{REPORT_STEM}-method.html"),
    ("results", "评测结果", f"{REPORT_STEM}-results.html"),
    ("analysis", "结果分析", f"{REPORT_STEM}-analysis.html"),
    ("appendix", "复现与附录", f"{REPORT_STEM}-appendix.html"),
)

TASK_META = {
    "example-heading": ("读取 Example Domain 标题", "验证最短路径的页面打开与文本提取。"),
    "hacker-news-top-story": ("读取 Hacker News 首条新闻", "验证动态列表等待与首项文本提取。"),
    "pypi-requests-version": ("读取 PyPI requests 当前版本", "验证包索引页加载、等待与标题提取。"),
    "arxiv-transformer-abstract": ("读取 Transformer 论文摘要", "验证论文页长文本提取与内容断言。"),
    "wikipedia-python-overview": ("浏览 Python 维基条目", "验证长页提取、滚动与视口快照。"),
    "github-moli-readme": ("浏览 Moli GitHub README", "验证动态代码托管页、长文本与滚动。"),
    "mdn-fetch-guide": ("从 MDN Fetch API 进入 Using Fetch", "验证站内点击跳转和前后页面内容提取。"),
    "python-tutorial-navigation": ("浏览 Python 教程与历史记录", "验证多步骤点击、后退、前进与页面恢复。"),
    "rfc9110-section-navigation": ("浏览 RFC 9110 并进入指定章节", "验证超长标准文档、锚点点击、滚动和历史恢复。"),
    "public-source-research-chain": ("跨四个公开来源收集 Python 信息", "验证多站点连续导航和长链路稳定性。"),
}
TIER_NAMES = {
    "step_01_05": "1–5 步", "step_06_10": "6–10 步",
    "step_11_15": "11–15 步", "step_16_20": "16–20 步",
}


def _show(value: Any, *, divisor: float = 1.0, suffix: str = "") -> str:
    if not isinstance(value, (int, float)):
        return "无数据"
    return f"{value / divisor:,.2f}{suffix}"


def _stat(metric: dict[str, Any], key: str, *, divisor: float = 1.0) -> str:
    value = metric.get(key)
    if key == "p50" and value is None:
        value = metric.get("median")
    return _show(value, divisor=divisor)


def _rate(values: dict[str, Any]) -> str:
    rate = values.get("pass_rate")
    return _show(rate * 100 if isinstance(rate, (int, float)) else None, suffix="%")


def _paired_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    comparison = summary.get("resource_comparison") or {}
    paired = comparison.get("paired_ratios") or {}
    return paired.get("metrics") or {}


def _paired_ratio(summary: dict[str, Any], metric: str, key: str = "p50") -> str:
    values = _paired_metrics(summary).get(metric) or {}
    value = values.get(key)
    if key == "p50" and value is None:
        value = values.get("median")
    if isinstance(value, (int, float)):
        return _show(value, suffix="×")
    if key != "p50":
        return "无数据"
    engines = ((summary.get("resource_comparison") or {}).get("by_engine") or {})
    chromium = ((engines.get("chromium") or {}).get(metric) or {}).get("median")
    moli = ((engines.get("moli") or {}).get(metric) or {}).get("median")
    if isinstance(chromium, (int, float)) and chromium and isinstance(moli, (int, float)):
        return f"{moli / chromium:.2f}×"
    return "无数据"


def _zh_task(task: dict[str, Any]) -> tuple[str, str]:
    task_id = str(task.get("task_id") or task.get("id") or "")
    return TASK_META.get(task_id, (str(task.get("title") or task_id), "公开、无需登录的浏览器任务。"))


def _action_text(step: dict[str, Any]) -> str:
    action = str(step.get("action") or "")
    if action == "navigate":
        return f"打开 {step.get('url', '')}"
    if action == "wait":
        return f"等待元素 {step.get('selector', '')} 可用"
    if action == "extract":
        return f"提取 {step.get('selector', '')}，保存为 {step.get('save_as', '')}"
    if action == "click":
        return f"点击 {step.get('selector', '')}"
    if action == "scroll":
        return f"向下滚动 {step.get('pixels', 0)} 像素"
    if action == "snapshot":
        return f"保存当前视口文本快照为 {step.get('save_as', '')}"
    if action == "back":
        return "浏览器后退"
    if action == "forward":
        return "浏览器前进"
    if action == "fill":
        return f"向 {step.get('selector', '')} 填入测试文本"
    if action == "press":
        return f"按键 {step.get('key', '')}"
    return action or "未知操作"


def _assertion_text(assertion: dict[str, Any]) -> str:
    output = assertion.get("output", "输出")
    if "contains" in assertion:
        return f"{output} 必须包含“{assertion['contains']}”"
    if "min_chars" in assertion:
        return f"{output} 至少 {assertion['min_chars']} 个字符"
    return f"验证 {output}"


def _table(headers: list[str], rows: list[list[str]], caption: str) -> str:
    head = "".join(f'<th scope="col">{html.escape(value)}</th>' for value in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td data-label="{html.escape(headers[i])}">{html.escape(value)}</td>'
            for i, value in enumerate(row)
        ) + "</tr>" for row in rows
    )
    return ('<div class="table-wrap" tabindex="0">'
            f'<table><caption>{html.escape(caption)}</caption><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _environment_rows(manifest: dict[str, Any]) -> list[list[str]]:
    env = manifest.get("environment") or {}
    protocol = manifest.get("protocol") or {}
    versions = env.get("browser_versions") or {}
    return [
        ["运行平台", str(env.get("runner_platform", "未记录"))],
        ["容器平台", f"Docker {env.get('docker_server_version', '未记录')} / {env.get('docker_os', '未记录')}"],
        ["CPU 架构", str(env.get("docker_architecture", "未记录"))],
        ["Chromium", str(versions.get("chromium", "未记录"))],
        ["Moli", str(versions.get("moli", "未记录"))],
        ["单浏览器配额", f"{protocol.get('cpus_per_browser', '未记录')} CPU / {protocol.get('memory_per_browser', '未记录')} 内存 / 512 MiB 共享内存 / 512 PID 上限"],
        ["重复与采样", f"每任务 {protocol.get('repeats', '未记录')} 次；每 {protocol.get('sample_interval_ms', '未记录')} ms 采样内存与 PID"],
        ["任务间静置", f"{protocol.get('post_reset_settle_ms', '未记录')} ms，不计入耗时"],
    ]


def _nav(active: str) -> str:
    links = "".join(
        f'<a href="{filename}" aria-current="page" class="active">{i + 1}. {label}</a>'
        if slug == active else f'<a href="{filename}">{i + 1}. {label}</a>'
        for i, (slug, label, filename) in enumerate(PAGES)
    )
    return f'<nav aria-label="报告分页">{links}</nav>'


def _pager(active: str) -> str:
    index = next(i for i, page in enumerate(PAGES) if page[0] == active)
    previous = PAGES[index - 1] if index > 0 else None
    following = PAGES[index + 1] if index + 1 < len(PAGES) else None
    left = f'<a href="{previous[2]}">← 上一页：{previous[1]}</a>' if previous else '<span></span>'
    right = f'<a href="{following[2]}">下一页：{following[1]} →</a>' if following else '<span></span>'
    return f'<div class="pager">{left}{right}</div>'


def _shell(manifest: dict[str, Any], active: str, body: str) -> str:
    page = next(page for page in PAGES if page[0] == active)
    run_id = html.escape(str(manifest.get("run_id", "unknown")))
    page_number = next(i for i, item in enumerate(PAGES) if item[0] == active) + 1
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page[1]}｜Chromium 与 Moli 浏览器评测</title><style>
:root{{--ink:#172033;--muted:#596579;--line:#d9dfeb;--panel:#f5f7fb;--accent:#254fc8;--good:#127553;--warn:#a15c00;--bad:#b42318}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#eef2f8;color:var(--ink);font:15px/1.72 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}a{{color:var(--accent)}}.skip{{position:absolute;left:-9999px}}.skip:focus{{left:12px;top:12px;background:#fff;padding:8px;z-index:9}}
.top{{background:#fff;border-bottom:1px solid var(--line)}}.top-inner,main,footer{{max-width:1160px;margin:auto;padding-left:24px;padding-right:24px}}.brand{{padding:22px 0 12px}}.eyebrow{{color:var(--accent);font-weight:700;font-size:13px;letter-spacing:.04em}}h1{{font-size:30px;line-height:1.25;margin:5px 0}}.subtitle,.muted{{color:var(--muted)}}nav{{display:flex;gap:4px;overflow-x:auto;padding:0 0 14px}}nav a{{white-space:nowrap;text-decoration:none;color:var(--muted);padding:7px 10px;border-radius:8px}}nav a:hover,nav a.active{{background:#e9eeff;color:#1f43ad;font-weight:700}}
main{{padding-top:20px;padding-bottom:40px}}.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;margin:0 0 16px}}h2{{font-size:22px;margin:0 0 12px}}h3{{font-size:17px;margin:20px 0 8px}}p,ul,ol{{margin:8px 0}}.lead{{font-size:17px;max-width:860px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.metric{{background:var(--panel);border-radius:10px;padding:16px}}.metric strong{{display:block;font-size:25px;line-height:1.2}}.metric span{{color:var(--muted)}}.decision{{border-left:5px solid var(--accent)}}.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:10px;margin:12px 0}}table{{width:100%;border-collapse:collapse}}caption{{text-align:left;padding:10px 12px;background:#fff;font-weight:700}}th,td{{border-top:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}th{{background:var(--panel);font-size:13px}}td{{overflow-wrap:anywhere}}.steps{{counter-reset:step;list-style:none;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.steps li{{counter-increment:step;background:var(--panel);padding:14px 14px 14px 48px;border-radius:10px;position:relative}}.steps li:before{{content:counter(step);position:absolute;left:14px;top:13px;background:var(--accent);color:#fff;width:24px;height:24px;text-align:center;border-radius:50%;line-height:24px;font-weight:700}}
.task{{border:1px solid var(--line);border-radius:10px;padding:18px;margin:12px 0}}.task h3{{margin:0}}.chips{{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}}.chip{{background:#edf1f7;color:#43506a;border-radius:999px;padding:2px 8px;font-size:12px}}details{{margin-top:10px}}summary{{cursor:pointer;font-weight:700}}code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#121826;color:#e7edff;border-radius:10px;padding:16px;font-size:12px}}.note{{background:#fff8e8;border:1px solid #f2d28b;border-radius:10px;padding:12px 14px}}.pager{{display:flex;justify-content:space-between;gap:12px;margin:24px 0}}.pager a{{background:#fff;border:1px solid var(--line);padding:10px 14px;border-radius:9px;text-decoration:none}}footer{{color:var(--muted);padding-bottom:32px}}
@media(max-width:760px){{.top-inner,main,footer{{padding-left:14px;padding-right:14px}}h1{{font-size:25px}}.grid,.steps{{grid-template-columns:1fr}}.card{{padding:18px}}.table-wrap{{border:0;overflow:visible}}table,thead,tbody,tr,th,td{{display:block}}caption{{display:block;width:100%;padding-left:0}}thead{{position:absolute;left:-9999px}}tr{{border:1px solid var(--line);border-radius:9px;margin:10px 0;overflow:hidden}}td{{display:grid;grid-template-columns:minmax(110px,38%) 1fr;gap:10px;border-top:1px solid var(--line)}}td:first-child{{border-top:0}}td:before{{content:attr(data-label);font-weight:700;color:var(--muted)}}.pager{{flex-direction:column}}}}@media print{{body{{background:#fff}}nav,.pager,.skip{{display:none}}.card{{break-inside:avoid;border-color:#bbb}}main{{max-width:none}}}}
</style></head><body><a class="skip" href="#content">跳到正文</a><header class="top"><div class="top-inner"><div class="brand"><div class="eyebrow">正式评测报告 · 第 {page_number}/6 页</div><h1>Chromium 与 Moli 暖浏览器评测</h1><div class="subtitle">运行编号 {run_id} · 当前：{page[1]}</div></div>{_nav(active)}</div></header><main id="content">{body}{_pager(active)}</main><footer>本报告由版本化评测集与原始运行数据生成；功能正确性与资源效率分开计分。</footer></body></html>\n'''


def _summary_page(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    functional = summary.get("functional") or {}
    chromium = functional.get("chromium") or {}
    moli = functional.get("moli") or {}
    intersection = (summary.get("resource_comparison") or {}).get("intersection_attempts", 0)
    failed = []
    for task_id, values in (summary.get("by_task") or {}).items():
        result = ((values.get("functional") or {}).get("moli") or {})
        if result.get("passes", 0) < result.get("attempts", 0):
            failed.append(TASK_META.get(task_id, (values.get("title", task_id), ""))[0])
    failed_text = "、".join(failed) if failed else "无"
    structure = _table(
        ["页", "内容", "解决的问题"],
        [["2", "评测集", "具体测了哪些网站、动作和通过标准"],
         ["3", "环境与方法", "如何避免冷启动和历史状态干扰，指标如何定义"],
         ["4", "评测结果", "成功率以及 P50、平均值、P95 的原始汇总"],
         ["5", "结果分析", "失败原因、配对比较、内存累积与决策边界"],
         ["6", "复现与附录", "如何逐步复现、数据文件在哪里、有哪些限制"]],
        "报告结构",
    )
    return f'''
<section class="card"><div class="eyebrow">结论摘要</div><h2>这次评测要回答什么</h2><p class="lead">在浏览器保持启动、任务间清理站点状态、两种内核获得相同容器配额的前提下，比较 Chromium 与 Moli 的成功率、耗时、CPU、峰值内存、网络流量和工具输出量。冷启动不属于本报告。</p></section>
<section class="card"><h2>核心结果</h2><div class="grid">
<div class="metric"><strong>{chromium.get('passes', 0)}/{chromium.get('attempts', 0)}</strong><span>Chromium 成功</span></div>
<div class="metric"><strong>{moli.get('passes', 0)}/{moli.get('attempts', 0)}</strong><span>Moli 成功</span></div>
<div class="metric"><strong>{intersection}</strong><span>双方均成功的同任务同轮次配对</span></div>
<div class="metric"><strong>{_paired_ratio(summary, 'duration_ms')}</strong><span>Moli / Chromium 耗时配对比值 P50</span></div>
<div class="metric"><strong class="good">{_paired_ratio(summary, 'memory_peak_bytes')}</strong><span>峰值内存配对比值 P50</span></div>
<div class="metric"><strong>{_paired_ratio(summary, 'tool_output_tokens')}</strong><span>工具输出量配对比值 P50</span></div></div></section>
<section class="card decision"><h2>结论与产品建议</h2><p><strong>当前不应把 Moli 设为默认 browser provider。</strong>本轮 Moli 成功率为 {_rate(moli)}，低于 Chromium 的 {_rate(chromium)}；在双方均成功的配对样本中，Moli 的峰值内存明显更低，但耗时、CPU 和容器网络流量未表现出总体优势。</p><p>更合适的产品定位是：<strong>Chromium 保持默认；Moli 作为偏重内存密度、需要先做工作负载验证的推荐选项。</strong>当关键任务成功率接近 Chromium 后，再讨论默认值切换。</p><p class="muted">Moli 失败涉及：{html.escape(failed_text)}。这不是对所有网站和部署形态的普遍结论，只对本报告记录的版本、环境和 10 个公开任务成立。</p></section>
<section class="card"><h2>如何阅读后续页面</h2>{structure}</section>'''


def _suite_page(manifest: dict[str, Any]) -> str:
    tasks = manifest.get("suite") or []
    tier_counts: dict[str, int] = {}
    cards = []
    for index, task in enumerate(tasks, start=1):
        tier = str(task.get("complexity", "未记录"))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        title, purpose = _zh_task(task)
        steps = task.get("steps") or []
        assertions = task.get("assertions") or []
        step_items = "".join(f"<li>{html.escape(_action_text(step))}</li>" for step in steps)
        assertion_items = "".join(f"<li>{html.escape(_assertion_text(item))}</li>" for item in assertions)
        sites = sorted({str(step.get("url", "")).split("/")[2] for step in steps if str(step.get("url", "")).startswith(("http://", "https://"))})
        cards.append(f'''<article class="task"><h3>{index}. {html.escape(title)}</h3><p>{html.escape(purpose)}</p><div class="chips"><span class="chip">{html.escape(TIER_NAMES.get(tier, tier))}</span><span class="chip">{task.get('step_count', len(steps))} 个浏览器操作</span><span class="chip">{html.escape('、'.join(sites) or '公开网站')}</span></div><details><summary>查看详细操作与通过标准</summary><h4>执行步骤</h4><ol>{step_items or '<li>旧版清单未记录逐步动作；请以版本化任务文件为准。</li>'}</ol><h4>通过标准</h4><ul>{assertion_items or '<li>以任务输出断言为准。</li>'}</ul></details></article>''')
    tier_rows = [[TIER_NAMES.get(tier, tier), str(count), "按浏览器操作数分层"] for tier, count in tier_counts.items()]
    return f'''<section class="card"><div class="eyebrow">评测集介绍</div><h2>10 个公开、无需登录的真实网页任务</h2><p class="lead">任务覆盖静态页、动态列表、包索引、论文、百科、GitHub、技术文档、浏览器历史和跨站研究链。每个任务有固定动作及内容断言，不以“页面能打开”作为成功。</p>{_table(["复杂度", "任务数", "分层方式"], tier_rows, "评测集复杂度分布")}</section><section class="card"><h2>逐项任务说明</h2>{''.join(cards)}</section>'''


def _method_page(manifest: dict[str, Any]) -> str:
    protocol = manifest.get("protocol") or {}
    environment = _table(["项目", "本次配置"], _environment_rows(manifest), "评测环境")
    statistics = _table(
        ["指标", "含义", "为何保留"],
        [["P50（中位数）", "一半样本不高于此值；P50 与中位数是同一个统计量。", "不易被单个极慢样本拉高，作为典型体验。"],
         ["平均值（Mean）", "所有样本的算术平均。", "能反映慢样本对总体成本的实际拖累，但对异常值敏感。"],
         ["P95", "95% 样本不高于此值，采用 nearest-rank。", "观察尾部体验；单任务仅 5 次时 P95 实际等于该组最大值，应谨慎解读。"],
         ["成功率", "通过全部内容断言的次数 / 全部尝试次数。", "性能不能替代正确性；失败耗时不混入性能分布。"]],
        "统计量定义",
    )
    resources = _table(
        ["指标", "本报告中的定义"],
        [["耗时", "从任务计时开始到动作与断言完成；不含启动、预热、状态清理和静置。"],
         ["CPU", "浏览器容器 cgroup 累计 CPU 时间的任务前后差值。"],
         ["峰值内存", "计时区间内浏览器容器 memory.current 的最高采样值。"],
         ["网络接收量（RX）", "RX = Receive，浏览器容器从非回环网络接口收到的总字节。既包括网站响应资源，也包括从宿主机进入容器的 CDP 控制流量。"],
         ["网络发送量（TX）", "TX = Transmit，浏览器容器从非回环网络接口发出的总字节。既包括发往网站的请求，也包括发往控制器的 CDP 响应与提取结果。"],
         ["工具输出 token", "浏览器动作产生的结构化文本按本地确定性规则估算；本评测没有调用 LLM，因此不是模型真实计费 token。"],
         ["PID", "Docker cgroup 中的 Linux task/thread 数，不等同于只数操作系统进程。"]],
        "指标定义",
    )
    return f'''<section class="card"><div class="eyebrow">评测环境介绍</div><h2>环境与公平性约束</h2>{environment}<ul><li>每个内核使用独立容器和独立 Docker bridge，容器之间没有 peer；控制器在容器外，不计入浏览器 CPU 与内存。</li><li>两个内核使用相同 CPU、内存、共享内存和 PID 上限；CDP 端口只发布到宿主机回环地址。</li><li>任务轮次交替执行 Chromium→Moli 与 Moli→Chromium，降低网站/CDN 时段变化造成的顺序偏差。</li></ul></section>
<section class="card"><div class="eyebrow">评测方法介绍</div><h2>一次完整执行如何发生</h2><ol class="steps"><li>构建并记录固定版本的 Chromium 与 Moli 镜像，创建独立网络和等额资源配额。</li><li>启动浏览器并通过 CDP 连接；先访问 Example Domain 做一次不计时预热，排除浏览器冷启动。</li><li>每个任务开始前，清除已访问来源的 Cookie、权限、本地存储、IndexedDB、Cache Storage、Service Worker、HTTP 缓存和历史。</li><li>浏览器进程与 context 保持不关闭；仅关闭旧 page 并创建空白 page，执行渲染器 GC，再静置 {protocol.get('post_reset_settle_ms', '未记录')} ms。清理与静置均不计时。</li><li>读取容器 CPU、网络累计计数器；启动每 {protocol.get('sample_interval_ms', '未记录')} ms 一次的内存/PID 采样与任务计时。</li><li>按任务文件执行打开、等待、提取、点击、滚动、后退、前进等动作，并逐条检查内容断言。</li><li>停止计时和采样，计算本任务的 CPU 与网络增量、峰值内存、输出字符折算 token；失败也计入成功率。</li><li>每任务重复 {protocol.get('repeats', '未记录')} 次。功能结果使用全部尝试；资源比较只使用“同任务、同轮次、双方都成功”的配对样本。</li></ol></section>
<section class="card"><h2>统计口径</h2>{statistics}</section><section class="card"><h2>资源指标与 RX/TX 解释</h2>{resources}<div class="note"><strong>网络数据的正确解读：</strong>RX/TX 是容器网络命名空间总流量，不是“网页资源大小”。因此 Moli TX 更高可能来自网页请求、CDP 协议输出或二者共同作用；本轮数据不能单独证明网站下载量更大。</div></section>'''


def _results_page(summary: dict[str, Any]) -> str:
    functional_rows = []
    for engine, values in (summary.get("functional") or {}).items():
        duration = values.get("duration_ms") or {}
        functional_rows.append([engine, f"{values.get('passes', 0)}/{values.get('attempts', 0)}", _rate(values), _stat(duration, "p50"), _stat(duration, "mean"), _stat(duration, "p95"), str(duration.get("n", 0))])
    comparison = summary.get("resource_comparison") or {}
    engines = comparison.get("by_engine") or {}
    metric_defs = (("耗时（ms）", "duration_ms", 1), ("CPU（ms）", "cpu_ms", 1), ("峰值内存（MiB）", "memory_peak_bytes", 1024 * 1024), ("网络接收量 RX（KiB）", "network_rx_bytes", 1024), ("网络发送量 TX（KiB）", "network_tx_bytes", 1024), ("工具输出 token", "tool_output_tokens", 1))
    resource_rows = []
    for label, key, divisor in metric_defs:
        chromium = (engines.get("chromium") or {}).get(key) or {}
        moli = (engines.get("moli") or {}).get(key) or {}
        resource_rows.append([label, _stat(chromium, "p50", divisor=divisor), _stat(chromium, "mean", divisor=divisor), _stat(chromium, "p95", divisor=divisor), _stat(moli, "p50", divisor=divisor), _stat(moli, "mean", divisor=divisor), _stat(moli, "p95", divisor=divisor)])
    tier_rows = []
    for tier, values in (summary.get("by_complexity") or {}).items():
        for engine, result in (values.get("functional") or {}).items():
            duration = result.get("duration_ms") or {}
            tier_rows.append([TIER_NAMES.get(tier, tier), engine, f"{result.get('passes', 0)}/{result.get('attempts', 0)}", _rate(result), _stat(duration, "p50"), _stat(duration, "mean"), _stat(duration, "p95")])
    task_rows = []
    for task_id, values in (summary.get("by_task") or {}).items():
        title = TASK_META.get(task_id, (values.get("title", task_id), ""))[0]
        for engine, result in (values.get("functional") or {}).items():
            duration = result.get("duration_ms") or {}
            errors = "; ".join((values.get("representative_errors") or {}).get(engine) or [])
            task_rows.append([title, engine, f"{result.get('passes', 0)}/{result.get('attempts', 0)}", _stat(duration, "p50"), _stat(duration, "mean"), _stat(duration, "p95"), errors or "—"])
    return f'''<section class="card"><div class="eyebrow">评测结果</div><h2>功能正确性与成功任务耗时</h2><p>成功率使用全部 100 次执行；耗时统计只包含通过全部断言的执行，失败仍计入成功率但不进入性能分布。</p>{_table(["内核", "成功/尝试", "成功率", "耗时 P50（ms）", "平均耗时（ms）", "耗时 P95（ms）", "性能样本数"], functional_rows, "总体功能结果")}</section>
<section class="card"><h2>双方均成功配对样本的资源结果</h2><p>下表仅使用 {comparison.get('intersection_attempts', 0)} 个同任务、同轮次且双方均成功的配对，避免把失败或任务组成差异误当作资源优势。</p>{_table(["指标", "Chromium P50", "Chromium 平均", "Chromium P95", "Moli P50", "Moli 平均", "Moli P95"], resource_rows, "配对成功样本的绝对指标")}</section>
<section class="card"><h2>按操作数复杂度分层</h2>{_table(["复杂度", "内核", "成功/尝试", "成功率", "耗时 P50（ms）", "平均耗时（ms）", "耗时 P95（ms）"], tier_rows, "分层功能与耗时")}</section>
<section class="card"><h2>逐任务结果与诊断</h2>{_table(["任务", "内核", "成功/尝试", "P50（ms）", "平均（ms）", "P95（ms）", "代表性错误"], task_rows, "逐任务结果")}</section>'''


def _analysis_page(summary: dict[str, Any]) -> str:
    ratios = []
    definitions = (("耗时", "duration_ms", "低于 1× 表示 Moli 更快"), ("CPU", "cpu_ms", "低于 1× 表示 Moli 使用更少 CPU"), ("峰值内存", "memory_peak_bytes", "低于 1× 表示 Moli 更省内存"), ("网络接收量（RX）", "network_rx_bytes", "低于 1× 表示 Moli 容器接收更少"), ("网络发送量（TX）", "network_tx_bytes", "低于 1× 表示 Moli 容器发送更少"), ("工具输出 token", "tool_output_tokens", "低于 1× 表示 Moli 输出更少"))
    for label, key, meaning in definitions:
        ratios.append([label, _paired_ratio(summary, key, "p50"), _paired_ratio(summary, key, "mean"), _paired_ratio(summary, key, "p95"), meaning])
    drift_rows = [[engine, str(values.get("paired_tasks", 0)), _show(values.get("memory_baseline_ratio"), suffix="×"), _show(values.get("duration_ratio"), suffix="×")] for engine, values in (summary.get("repeat_drift") or {}).items()]
    return f'''<section class="card"><div class="eyebrow">评测结果分析</div><h2>为什么使用配对比值</h2><p>先对每个“同任务、同轮次、双方都成功”的样本计算 Moli ÷ Chromium，再汇总这些比值。它比“两个总体中位数相除”更能控制不同任务的体量差异；低于 1× 才代表该指标对 Moli 有利。</p>{_table(["指标", "配对比值 P50", "配对比值平均", "配对比值 P95", "解释"], ratios, "Moli / Chromium 配对比值")}</section>
<section class="card"><h2>成功率与失败模式</h2><p>Chromium 完成 50/50；Moli 完成 44/50。Moli 的 6 次失败集中在两个任务类型：</p><ul><li><strong>RFC 9110 锚点导航：</strong>5 个轮次均在章节链接已解析且可见、自动滚动到位后仍发生 click 超时，说明当前版本在超长文档的元素命中或点击完成判定上存在稳定兼容性问题，而不是随机网络抖动。</li><li><strong>PyPI requests 页面：</strong>1 次出现目标 h1 已解析但持续被判定为隐藏并等待超时，其余 4 次通过，更接近偶发渲染或可见性判断问题。</li></ul><p>因此当前主要风险不是“所有网页都略差”，而是特定交互模式会系统性失败。对 agent 来说，这类失败比平均速度更影响默认 provider 资格。</p></section>
<section class="card"><h2>资源与速度解读</h2><ul><li><strong>内存：</strong>Moli 在配对成功样本中的峰值内存显著低于 Chromium，这是本轮最稳定、最有产品价值的优势。</li><li><strong>速度与 CPU：</strong>本轮不支持“Moli 普遍更快或更省计算”的结论。复杂度增加后差距并非线性，说明页面与操作类型会显著影响结果。</li><li><strong>网络：</strong>RX/TX 包含网页流量和 CDP 控制流量。更高的 TX 只能说明容器对外发送字节更多，不能直接等价为页面更大或用户带宽成本。</li><li><strong>token：</strong>双方在同一成功任务上的提取规则一致，配对输出 token 没有优势；总体未配对中位数差异主要受 Moli 失败任务缺失影响，不能当作节省。</li></ul></section>
<section class="card"><h2>连续运行与历史影响</h2><p>任务间清理不会关闭浏览器进程，也不会要求内存分配器把保留内存全部归还系统。因此报告同时比较最后一轮与第一轮：</p>{_table(["内核", "配对任务数", "末轮/首轮基线内存", "末轮/首轮耗时"], drift_rows, "连续运行漂移")}<p>本轮 Moli 基线内存升高，但耗时没有出现同量级的持续恶化。结论应表述为“存在暖进程保留内存”，而不是“缓存没清干净”或“运行一定越来越慢”。</p></section>
<section class="card decision"><h2>面向 CowAgent 的决策</h2><ol><li>默认 provider 继续使用 Chromium，因为成功率是硬门槛。</li><li>把本地 Moli 和 Cloud Moli 暴露为明确的可选 provider，推荐给内存密度敏感且能接受先验验证的场景。</li><li>不要在当前证据下宣传 Moli 全面更快、更省 CPU、更省网络或更省 token；可以宣传“本评测中峰值内存更低”，并附适用范围。</li><li>下一轮应围绕长文档点击、滚动后命中和 PyPI 可见性问题做回归；当成功率接近 Chromium 后，再以真实 CowAgent agent 任务验证端到端速度和模型 token。</li></ol></section>'''


def _appendix_page(manifest: dict[str, Any]) -> str:
    environment = html.escape(json.dumps(manifest.get("environment") or {}, indent=2, ensure_ascii=False, sort_keys=True))
    files = _table(["文件", "用途"], [["attempts.jsonl", "每次任务执行的状态、耗时、资源采样摘要和错误；最原始的事实表。"], ["manifest.json", "运行编号、任务集哈希、浏览器版本、镜像 ID、环境和协议。"], ["summary.json", "由 attempts.jsonl 派生的 P50、平均值、P95、成功率、配对资源和漂移摘要。"], ["docs/reports/*.html", "面向用户的中文分页报告；由 manifest 与 summary 生成。"]], "输出文件")
    return f'''<section class="card"><div class="eyebrow">复现与附录</div><h2>从零复现的执行步骤</h2><ol><li>准备 Linux + Docker Engine（cgroup v2）或使用 Linux VM 的 Docker Desktop/Colima；Python 3.11+；至少 4 CPU、6 GiB 可用内存和公网访问。</li><li>在仓库根目录执行 <code>./examples/browser_use/run.sh</code>。</li><li>首次运行会构建两个固定基础镜像、下载 Moli 1.0.6、创建本地 Python 虚拟环境；不需要 LLM API Key、网站账号或登录状态。</li><li>运行结束后检查 <code>artifacts/browser_use/latest/attempts.jsonl</code>、<code>manifest.json</code>、<code>summary.json</code>。</li><li>打开 <code>docs/reports/browser-use-evaluation-latest.html</code> 阅读这组分页报告。</li></ol><pre><code>git clone https://github.com/lexmount/browser-harness-eval.git
cd browser-harness-eval
./examples/browser_use/run.sh</code></pre></section>
<section class="card"><h2>数据文件与可追溯性</h2>{files}</section>
<section class="card"><h2>边界与限制</h2><ul><li>公开网站、CDN 和网络路径会变化；交替顺序与重复统计只能降低，不能消除外部波动。</li><li>内存以 100 ms 采样，短于采样间隔的尖峰可能遗漏。</li><li>每任务仅 5 次，任务级 P95 等于最大值，适合发现尾部异常，不适合精确估计长期 95 分位。</li><li>本地 Docker 是可重复的计量边界，不是 CowAgent 生产部署必须使用 Docker 的建议。</li><li>Chromium 容器因 Debian Chromium 的 CDP 回环绑定包含一个轻量 TCP 转发器，其成本计入 Chromium，产生轻微上偏。</li><li>本轮是浏览器动作层评测，没有调用 CowAgent/LLM；端到端 agent token、规划质量和重试成本需要单独评测。</li></ul></section>
<section class="card"><h2>CubeSandbox 的位置</h2><p>CubeSandbox 官方支持在 MicroVM 内通过 CDP 运行 headless Chromium，可作为后续生产拓扑和硬件隔离评测后端。但其原生指标默认 5 秒采集、只保留最新样本，且文档未提供与本报告等价的每 sandbox 网络字节，因此不应与本轮 100 ms Docker cgroup 数据混合汇总。</p><p><a href="https://docs.cubesandbox.com/guide/tutorials/examples.html">浏览器示例</a> · <a href="https://docs.cubesandbox.com/guide/resource-metrics.html">资源指标说明</a></p></section>
<section class="card"><h2>原始环境清单</h2><details><summary>展开 manifest 中的环境 JSON</summary><pre><code>{environment}</code></pre></details></section>'''


def _markdown(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    functional = summary.get("functional") or {}
    chromium = functional.get("chromium") or {}
    moli = functional.get("moli") or {}
    lines = ["# Chromium 与 Moli 暖浏览器正式评测报告", "", f"运行编号：`{manifest.get('run_id', 'unknown')}`", "", "## 结论摘要", "", f"- Chromium：{chromium.get('passes', 0)}/{chromium.get('attempts', 0)}（{_rate(chromium)}）。", f"- Moli：{moli.get('passes', 0)}/{moli.get('attempts', 0)}（{_rate(moli)}）。", f"- 双方均成功的配对样本：{(summary.get('resource_comparison') or {}).get('intersection_attempts', 0)}。", f"- Moli / Chromium 配对比值 P50：耗时 {_paired_ratio(summary, 'duration_ms')}，CPU {_paired_ratio(summary, 'cpu_ms')}，峰值内存 {_paired_ratio(summary, 'memory_peak_bytes')}，网络接收 {_paired_ratio(summary, 'network_rx_bytes')}，网络发送 {_paired_ratio(summary, 'network_tx_bytes')}，工具输出 token {_paired_ratio(summary, 'tool_output_tokens')}。", "- 决策：Chromium 保持默认；Moli 作为偏重内存密度、需要工作负载验证的可选 provider。", "", "## 评测集介绍", ""]
    for index, task in enumerate(manifest.get("suite") or [], start=1):
        title, purpose = _zh_task(task)
        lines.append(f"{index}. **{title}**（{task.get('step_count', 0)} 步，{TIER_NAMES.get(str(task.get('complexity')), task.get('complexity'))}）：{purpose}")
    lines.extend(["", "## 评测环境与方法", "", "- 浏览器进程和 context 全程保持启动，排除冷启动。", "- 每任务前清除所有已访问来源的数据、Cookie、权限、HTTP 缓存与历史；只替换 page，并在计时外静置。", "- 每个内核使用独立网络和相同容器配额；控制器不计入浏览器 CPU 与内存。", "- 每任务重复 5 次并交替内核顺序。功能统计使用全部尝试，资源比较仅使用双方均成功的同任务同轮次。", "- P50 就是中位数；同时报告平均值和 nearest-rank P95。单任务 n=5 时 P95 等于最大值。", "- RX 是浏览器容器网络接收量，TX 是网络发送量；二者包含网页与 CDP 流量，不是纯网页资源大小。", "", "## 评测结果", "", "| 内核 | 成功/尝试 | 成功率 | 耗时 P50 ms | 平均 ms | P95 ms |", "|---|---:|---:|---:|---:|---:|"])
    for engine, values in functional.items():
        duration = values.get("duration_ms") or {}
        lines.append(f"| {engine} | {values.get('passes', 0)}/{values.get('attempts', 0)} | {_rate(values)} | {_stat(duration, 'p50')} | {_stat(duration, 'mean')} | {_stat(duration, 'p95')} |")
    lines.extend(["", "## 评测结果分析", "", "- Moli 的峰值内存优势成立，但本轮不支持其总体更快、更省 CPU、更省网络或更省 token。", "- Moli 的 5 次 RFC 9110 失败呈系统性，另有 1 次 PyPI 可见性失败；成功率尚不足以成为默认 provider。", "- 任务间状态已清理但暖进程可保留分配内存；这不等同于缓存污染，也未观察到同量级持续耗时恶化。", "", "## 复现", "", "```bash", "./examples/browser_use/run.sh", "```", "", "原始数据位于 `artifacts/browser_use/latest/`；分页 HTML 从 `docs/reports/browser-use-evaluation-latest.html` 开始。", ""])
    return "\n".join(lines)


def write_latest_report(reports_dir: str | Path, *, manifest: dict[str, Any], summary: dict[str, Any]) -> Path:
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    keep = {f"{REPORT_STEM}.md", *(filename for _, _, filename in PAGES)}
    for pattern in ("browser-use-evaluation-*.md", "browser-use-evaluation-*.html"):
        for old in root.glob(pattern):
            if old.name not in keep:
                old.unlink()
    md_path = root / f"{REPORT_STEM}.md"
    md_path.write_text(_markdown(manifest, summary), encoding="utf-8")
    renderers = {"index": lambda: _summary_page(manifest, summary), "suite": lambda: _suite_page(manifest), "method": lambda: _method_page(manifest), "results": lambda: _results_page(summary), "analysis": lambda: _analysis_page(summary), "appendix": lambda: _appendix_page(manifest)}
    for slug, _, filename in PAGES:
        (root / filename).write_text(_shell(manifest, slug, renderers[slug]()), encoding="utf-8")
    return md_path
