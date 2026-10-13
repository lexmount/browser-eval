from __future__ import annotations

import json
import importlib.metadata
import re
from collections import Counter
from functools import lru_cache, wraps
from contextvars import ContextVar
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def browser_method_registry():
    """Use the execution registry for release and source-build participants."""
    path = Path(__file__).resolve().parents[2] / "configs/providers.headless.json"
    if not path.is_file():
        from harness.distribution import installed_data_path
        try:
            path = installed_data_path("share/browser-harness-eval/providers.headless.json")
        except importlib.metadata.PackageNotFoundError:
            return {}
        except ValueError as error:
            if str(error).startswith('Distribution must declare one data file:'):
                return {}
            raise
    return json.loads(path.read_text())["providers"]


def native_browser_engine(name):
    return browser_method_registry().get(name, {}).get("engine")


def browser_method_label(name):
    return _MEASURED_LABELS.get().get(
        name, browser_method_registry().get(name, {}).get("label", name)
    )


_MEASURED_LABELS = ContextVar("webfetch_measured_labels", default={})


def measured_method_label(name, fallback):
    return _MEASURED_LABELS.get().get(name, fallback)


def with_measured_method_labels(render):
    """Scope Moli version labels to the actual report's hash-bound binaries."""
    @wraps(render)
    def wrapped(out, dataset, records, *args, **kwargs):
        path = Path(out) / "implementation-provenance.json"
        provenance = json.loads(path.read_text()) if path.is_file() else {}
        identities = {
            item["binary_sha256"]: item["version"]
            for item in provenance.get("implementations", [])
            if item.get("version") and item.get("binary_sha256")
        }
        labels = {}
        for name in {r["provider"] for r in records}:
            if native_browser_engine(name) != "moli":
                continue
            subset = [r for r in records if r["provider"] == name]
            versions = [
                identities.get(r.get("provider_metadata", {}).get("binary_sha256"))
                for r in subset
            ]
            if versions and all(versions):
                counts = Counter(versions)
                if len(counts) == 1:
                    labels[name] = "Moli " + next(iter(counts))
                else:
                    labels[name] = "Moli 修复结果（" + "；".join(
                        f"{count} 例 {version}" for version, count in sorted(counts.items())
                    ) + "）"
        token = _MEASURED_LABELS.set(labels)
        try:
            return render(out, dataset, records, *args, **kwargs)
        finally:
            _MEASURED_LABELS.reset(token)
    return wrapped


METHOD_REGISTRY: dict[str, dict[str, Any]] = {
    "http": {"endpoint":"HTTP GET","contract":"已知 URL → HTML 文本或文件任务的原始字节","output":"HTML 提取文本；文件任务保留响应字节","freshness":"每次独立请求，不执行页面 JavaScript","leaderboard":"独立 HTTP 对照"},
    "headless_html": {"endpoint":"browser HTML → node-html-markdown 2.0.0","contract":"已知 URL → DOM HTML → Markdown","output":"Markdown；独立保存 HTML 和诊断","freshness":"每次新进程/会话；domcontentloaded；45 秒外层预算","leaderboard":"六类任务分别验收","report":{"observed_routes":True}},
    "headless_markdown": {"endpoint":"各方法最快的正式任务原生产物路径","contract":"已知 URL → 正文 Markdown/文本，或文件任务的原始字节","output":"正文 Markdown/文本；文件任务为原始字节；保留接口与阶段诊断","freshness":"每次新进程/会话；45 秒端到端预算","leaderboard":"六类任务分别验收","report":{"observed_routes":True}},
    "moli_webfetch": {"endpoint":"moli fetch（网页、文档正文、结构与网页媒体信息用原生 Markdown，直接媒体文件用原始响应）","contract":"已知 URL → 原生 Markdown 或原文件字节","output":"stdout Markdown/字节；stderr 独立诊断","freshness":"每次启动独立进程；结构完成条件只控制官方等待参数","leaderboard":"七类任务分别验收","report":{"reported_version":{"pattern":r"^moli\s+","replacement":"Moli "},"profiles":[{"metadata_equals":{"dump":"markdown","route":"dynamic_native_markdown","wait_until":"done","wait.strategy":"native_default_completion"},"endpoint":"moli fetch --dump markdown --wait-until done URL","freshness":"每例启动一个独立进程；由 Moli 的默认 done 完成方式决定何时交付内容。","configuration":"本轮 {record_count} 例统一使用 Moli 原生 Markdown 和默认 done 完成方式。除目标 URL 外，没有逐例等待元素、额外等待时间、提取脚本或特殊参数，也没有启用理解模型、格式回退或付费后备方法。"}]}},
    "cowagent_builtin": {"endpoint":"仓库内 WebFetch.execute","contract":"已知 URL → 可读文本/文档文字","output":"纯文本","freshness":"实时请求","leaderboard":"可读正文"},
    "lexmount": {"endpoint":"POST https://api.lexmount.com/v1/extract","contract":"已知 URL → 主正文 + 元数据","output":"main_text","freshness":"托管实时抓取","leaderboard":"可读正文"},
    "lexmount_cn": {"endpoint":"POST https://api.lexmount.cn/v1/extract","contract":"已知 URL → 主正文 + 元数据","output":"main_text","freshness":"中国区托管实时抓取","leaderboard":"可读正文"},
    "firecrawl": {"endpoint":"POST https://api.firecrawl.dev/v2/scrape","contract":"已知 URL → Markdown/资源地址，或原文件字节","output":"Markdown/资源地址；视频资源题另请求 video；文件题为 rawBase64 解码字节","freshness":"maxAge=0；动态题使用预声明 Actions 等待","leaderboard":"六类任务分别验收","report":{"pricing_anchor":"pricing-firecrawl","configuration":"正文与资源题请求 Markdown、links、images；原文件题改用唯一 rawBase64 格式并验收解码字节。动态顶层选择器使用 wait action，数量与 iframe 条件明确标为未完整执行。未启用额外的理解模型或付费后备方法。"}},
    "tavily_extract": {"endpoint":"POST https://api.tavily.com/extract","contract":"已知 URL → raw_content","output":"Markdown（advanced）","freshness":"供应商实时抽取","leaderboard":"可读正文","report":{"pricing_anchor":"pricing-tavily","configuration":"本轮请求 Markdown；图片资源仅来自同一 Extract 响应的 images 字段。未启用额外的理解模型或付费后备方法。"}},
    "exa_contents": {"endpoint":"POST https://api.exa.ai/contents","contract":"已知 URL → 正文文本","output":"纯文本","freshness":"maxAgeHours=0","leaderboard":"可读正文","report":{"pricing_anchor":"pricing-exa","configuration":"本轮请求正文文本；资源链接仅来自同一 Contents 响应的 extras 字段。未启用额外的理解模型或付费后备方法。"}},
    "cloudflare_markdown": {"endpoint":"POST /accounts/{id}/browser-rendering/markdown 或 /scrape；直接文档 POST /accounts/{id}/ai/tomarkdown","contract":"已知 URL → 网页正文、页面资源地址或直接文档文字","output":"Browser Run Markdown / DOM 资源地址；Workers AI 文档 Markdown","freshness":"Quick Actions 默认最多缓存 5 秒；waitUntil=networkidle2；文档下载与转换同一预算","leaderboard":"六类任务分别验收","report":{"pricing_anchor":"pricing-cloudflare-markdown"}},
    "cloudflare_content": {"endpoint":"POST /accounts/{id}/browser-rendering/content","contract":"已知 URL → 含 head 的完整渲染 HTML","output":"渲染 HTML","freshness":"Quick Actions 默认最多缓存 5 秒；waitUntil=networkidle2","leaderboard":"HTML 诊断"},
    "openclaw_builtin": {"endpoint":"OpenClaw createWebFetchTool","contract":"HTTP GET + Readability 正文抽取（关闭 Firecrawl 回退）","output":"Markdown","freshness":"cacheTtlMinutes=0","leaderboard":"可读正文"},
    "deepseek_harness_builtin": {"endpoint":"DeepSeek Harness ctx.tools.execute(web_fetch)","contract":"匿名 HTTP provider + Turndown","output":"Markdown","freshness":"实时请求","leaderboard":"可读正文","report":{"profiles":[{"metadata_equals":{"output_projection":"declared_body"},"endpoint":"DeepSeek Harness ctx.tools.execute(web_fetch) → value.body.content","contract":"已知 URL → 原生声明 HTML/文本正文 → 共有解析","output":"声明正文；完整工具回包和展示文本独立保存"}]}},
    "langchain_webbase": {"endpoint":"LangChain WebBaseLoader.load()","contract":"HTTP + BeautifulSoup 网页文本加载器","output":"纯文本","freshness":"实时请求","leaderboard":"可读正文"},
    "crawl4ai": {"endpoint":"Crawl4AI AsyncWebCrawler.arun()","contract":"本地浏览器渲染 + Markdown 抽取","output":"Markdown","freshness":"cache_mode=BYPASS","leaderboard":"可读正文"},
    "fixture": {"endpoint":"本地 fixture","contract":"确定性安装测试","output":"纯文本","freshness":"固定","leaderboard":"离线诊断"},
}


def is_browser_comparison(records: list[dict[str, Any]]) -> bool:
    """Recognize browser cohorts independently of their task-specific adapters."""
    types = {row.get("provider_type") for row in records}
    return bool(types & {"headless_html", "headless_markdown"}) and types <= {
        "headless_html", "headless_markdown", "moli_webfetch"}


def method_info(provider_type: str) -> dict[str, Any]:
    return METHOD_REGISTRY.get(provider_type, {"endpoint":"未记录","contract":"未知","output":"未知","freshness":"未知","leaderboard":"未分类"})


ROUTE_LABELS = {
    "cloudflare_native_markdown": "Cloudflare Browser Run /markdown 直接返回 Markdown",
    "lightpanda_native_markdown": "Lightpanda CLI 直接返回 Markdown",
    "obscura_native_markdown": "Obscura CLI 直接返回 Markdown",
    "moli_native_markdown": "Moli CLI 直接返回 Markdown",
    "node-html-markdown": "浏览器渲染 DOM → node-html-markdown 2.0.0",
}


def _metadata_matches(metadata: dict[str, Any], expected: dict[str, Any]) -> bool:
    for path, value in expected.items():
        current: Any = metadata
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                return False
            current = current[key]
        if current != value:
            return False
    return True


def method_page_model(name: str, label: str, records: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve method-specific evidence into provider-neutral page fields."""
    if not records:
        raise ValueError("method page requires at least one record")
    info = method_info(records[0].get("provider_type", ""))
    report = info.get("report", {})
    versions = sorted({
        str(record.get("provider_metadata", {}).get("reported_version", "")).strip()
        for record in records
        if str(record.get("provider_metadata", {}).get("reported_version", "")).strip()
    })
    title = label
    version_rule = report.get("reported_version")
    if version_rule and len(versions) == 1:
        version = re.sub(
            version_rule["pattern"], version_rule.get("replacement", ""), versions[0],
            flags=re.I,
        )
        if version not in title:
            title += f" · {version}"
    model = {
        "title": title,
        "contract": info["contract"],
        "endpoint": info["endpoint"],
        "freshness": info["freshness"],
        "configuration": report.get(
            "configuration",
            "调用参数与交付路线以本次配置和逐例记录为准。",
        ),
        "pricing_anchor": report.get("pricing_anchor", "method-guide-pricing"),
    }
    metadata = [record.get("provider_metadata", {}) for record in records]
    if report.get("observed_routes"):
        extractors = sorted({
            ROUTE_LABELS.get(item.get("extractor"), item.get("extractor"))
            for item in metadata if item.get("extractor")
        })
        contracts = sorted({str(item.get("output_contract")) for item in metadata
                            if item.get("output_contract")})
        route_count = sum(bool(item.get("extractor")) for item in metadata)
        output_count = sum(bool(item.get("output_contract")) for item in metadata)
        route = "；".join(extractors) or "未记录"
        output = "、".join(contracts) or "未记录"
        model["configuration"] = (
            f"交付路线（{route_count}/{len(records)} 条记录）：{route}；"
            f"输出格式（{output_count}/{len(records)} 条记录）：{output}。"
        )
    for profile in report.get("profiles", []):
        if metadata and all(_metadata_matches(item, profile["metadata_equals"])
                            for item in metadata):
            for field in ("endpoint", "freshness", "configuration"):
                if field in profile:
                    model[field] = profile[field].format(record_count=len(records))
            break
    return model
