from __future__ import annotations

from typing import Any


METHOD_REGISTRY: dict[str, dict[str, Any]] = {
    "cowagent_builtin": {"endpoint":"仓库内 WebFetch.execute","contract":"已知 URL → 可读文本/文档文字","output":"纯文本","freshness":"实时请求","leaderboard":"可读正文"},
    "lexmount": {"endpoint":"POST https://api.lexmount.com/v1/extract","contract":"已知 URL → 主正文 + 元数据","output":"main_text","freshness":"托管实时抓取","leaderboard":"可读正文"},
    "firecrawl": {"endpoint":"POST https://api.firecrawl.dev/v2/scrape","contract":"已知 URL → Markdown","output":"Markdown","freshness":"maxAge=0","leaderboard":"可读正文"},
    "tavily_extract": {"endpoint":"POST https://api.tavily.com/extract","contract":"已知 URL → raw_content","output":"Markdown（basic）","freshness":"供应商实时抽取","leaderboard":"可读正文"},
    "exa_contents": {"endpoint":"POST https://api.exa.ai/contents","contract":"已知 URL → 正文文本","output":"纯文本","freshness":"maxAgeHours=0","leaderboard":"可读正文"},
    "cloudflare_markdown": {"endpoint":"POST /accounts/{id}/browser-rendering/markdown","contract":"已知 URL → 浏览器渲染后的可读 Markdown","output":"Markdown","freshness":"实时；waitUntil=networkidle2","leaderboard":"可读正文"},
    "cloudflare_content": {"endpoint":"POST /accounts/{id}/browser-rendering/content","contract":"已知 URL → 含 head 的完整渲染 HTML","output":"渲染 HTML","freshness":"实时；waitUntil=networkidle2","leaderboard":"HTML 诊断"},
    "fixture": {"endpoint":"本地 fixture","contract":"确定性安装测试","output":"纯文本","freshness":"固定","leaderboard":"离线诊断"},
}


def method_info(provider_type: str) -> dict[str, Any]:
    return METHOD_REGISTRY.get(provider_type, {"endpoint":"未记录","contract":"未知","output":"未知","freshness":"未知","leaderboard":"未分类"})
