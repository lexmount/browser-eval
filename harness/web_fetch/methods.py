from __future__ import annotations

from typing import Any


METHOD_REGISTRY: dict[str, dict[str, Any]] = {
    "headless_html": {"endpoint":"browser HTML → pyhtml2md 1.7.0","contract":"已知 URL → DOM HTML → 统一 Markdown","output":"Markdown；独立保存 HTML 和诊断","freshness":"每次新进程/会话；domcontentloaded；45 秒外层预算","leaderboard":"六类任务分别验收"},
    "headless_markdown": {"endpoint":"各浏览器最快官方 Markdown 路径","contract":"已知 URL → 方法原生或必要转换 → Markdown","output":"Markdown；保留接口与阶段诊断","freshness":"每次新进程/会话；45 秒端到端预算","leaderboard":"六类任务分别验收"},
    "moli_webfetch": {"endpoint":"moli fetch --dump markdown","contract":"已知 URL → JS 渲染后的 Markdown","output":"stdout Markdown；stderr 独立诊断","freshness":"每次启动独立进程；wait-until=done","leaderboard":"可读正文"},
    "cowagent_builtin": {"endpoint":"仓库内 WebFetch.execute","contract":"已知 URL → 可读文本/文档文字","output":"纯文本","freshness":"实时请求","leaderboard":"可读正文"},
    "lexmount": {"endpoint":"POST https://api.lexmount.com/v1/extract","contract":"已知 URL → 主正文 + 元数据","output":"main_text","freshness":"托管实时抓取","leaderboard":"可读正文"},
    "lexmount_cn": {"endpoint":"POST https://api.lexmount.cn/v1/extract","contract":"已知 URL → 主正文 + 元数据","output":"main_text","freshness":"中国区托管实时抓取","leaderboard":"可读正文"},
    "firecrawl": {"endpoint":"POST https://api.firecrawl.dev/v2/scrape","contract":"已知 URL → Markdown","output":"Markdown","freshness":"maxAge=0","leaderboard":"可读正文"},
    "tavily_extract": {"endpoint":"POST https://api.tavily.com/extract","contract":"已知 URL → raw_content","output":"Markdown（basic）","freshness":"供应商实时抽取","leaderboard":"可读正文"},
    "exa_contents": {"endpoint":"POST https://api.exa.ai/contents","contract":"已知 URL → 正文文本","output":"纯文本","freshness":"maxAgeHours=0","leaderboard":"可读正文"},
    "cloudflare_markdown": {"endpoint":"POST /accounts/{id}/browser-rendering/markdown","contract":"已知 URL → 浏览器渲染后的可读 Markdown","output":"Markdown","freshness":"实时；waitUntil=networkidle2","leaderboard":"可读正文"},
    "cloudflare_content": {"endpoint":"POST /accounts/{id}/browser-rendering/content","contract":"已知 URL → 含 head 的完整渲染 HTML","output":"渲染 HTML","freshness":"实时；waitUntil=networkidle2","leaderboard":"HTML 诊断"},
    "openclaw_builtin": {"endpoint":"OpenClaw createWebFetchTool","contract":"HTTP GET + Readability 正文抽取（关闭 Firecrawl 回退）","output":"Markdown","freshness":"cacheTtlMinutes=0","leaderboard":"可读正文"},
    "deepseek_harness_builtin": {"endpoint":"DeepSeek Harness ctx.tools.execute(web_fetch)","contract":"匿名 HTTP provider + Turndown","output":"Markdown","freshness":"实时请求","leaderboard":"可读正文"},
    "langchain_webbase": {"endpoint":"LangChain WebBaseLoader.load()","contract":"HTTP + BeautifulSoup 网页文本加载器","output":"纯文本","freshness":"实时请求","leaderboard":"可读正文"},
    "crawl4ai": {"endpoint":"Crawl4AI AsyncWebCrawler.arun()","contract":"本地浏览器渲染 + Markdown 抽取","output":"Markdown","freshness":"cache_mode=BYPASS","leaderboard":"可读正文"},
    "fixture": {"endpoint":"本地 fixture","contract":"确定性安装测试","output":"纯文本","freshness":"固定","leaderboard":"离线诊断"},
}


def method_info(provider_type: str) -> dict[str, Any]:
    return METHOD_REGISTRY.get(provider_type, {"endpoint":"未记录","contract":"未知","output":"未知","freshness":"未知","leaderboard":"未分类"})
