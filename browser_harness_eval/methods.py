from __future__ import annotations

from typing import Any


METHOD_REGISTRY: dict[str, dict[str, Any]] = {
    "cowagent_builtin": {"endpoint":"repository-local WebFetch.execute","contract":"known URL → readable text/document text","output":"text","freshness":"live request","leaderboard":"readable-content"},
    "lexmount": {"endpoint":"POST https://api.lexmount.com/v1/extract","contract":"known URL → extracted main text + metadata","output":"main_text","freshness":"live managed fetch","leaderboard":"readable-content"},
    "firecrawl": {"endpoint":"POST https://api.firecrawl.dev/v2/scrape","contract":"known URL → Markdown","output":"markdown","freshness":"maxAge=0","leaderboard":"readable-content"},
    "tavily_extract": {"endpoint":"POST https://api.tavily.com/extract","contract":"known URL → extracted raw_content","output":"markdown (basic)","freshness":"provider live extract","leaderboard":"readable-content"},
    "exa_contents": {"endpoint":"POST https://api.exa.ai/contents","contract":"known URL → text","output":"text","freshness":"maxAgeHours=0","leaderboard":"readable-content"},
    "cloudflare_markdown": {"endpoint":"POST /accounts/{id}/browser-rendering/markdown","contract":"known URL → browser-rendered readable Markdown","output":"markdown","freshness":"live; waitUntil=networkidle2","leaderboard":"readable-content"},
    "cloudflare_content": {"endpoint":"POST /accounts/{id}/browser-rendering/content","contract":"known URL → fully rendered HTML including head","output":"rendered HTML","freshness":"live; waitUntil=networkidle2","leaderboard":"rendered-html-diagnostic"},
    "fixture": {"endpoint":"local fixture","contract":"deterministic installation test","output":"text","freshness":"fixed","leaderboard":"offline-diagnostic"},
}


def method_info(provider_type: str) -> dict[str, Any]:
    return METHOD_REGISTRY.get(provider_type, {"endpoint":"not documented","contract":"unknown","output":"unknown","freshness":"unknown","leaderboard":"unclassified"})
