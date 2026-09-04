from __future__ import annotations
from urllib.parse import urlsplit

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import request


class FirecrawlProvider(Provider):
    def validate(self) -> list[str]:
        # Hosted Scrape requires Bearer auth. A separately configured local or
        # self-hosted endpoint may intentionally run without an API key.
        host = urlsplit(str(self.config.get('base_url', 'https://api.firecrawl.dev'))).hostname
        if host == 'api.firecrawl.dev' and not str(self.config.get('api_key') or '').strip():
            return ['api_key']
        return []

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        base_url = str(self.config.get("base_url", "https://api.firecrawl.dev")).rstrip("/")
        headers = {}
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        response = request(
            f"{base_url}/v2/scrape",
            method="POST",
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers=headers,
            json_body={
                "url": case.url,
                "formats": self.config.get("formats", ["markdown"]),
                "maxAge": int(self.config.get("max_age", 0)),
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        data = payload.get("data") or {}
        metadata = data.get("metadata") or {}
        ok = 200 <= response.status < 300 and payload.get("success") is True
        error_message = payload.get("error") or f"HTTP {response.status}"
        rate_limited = (
            response.status == 429
            or payload.get("reason") == "credits"
            or "rate limit" in str(error_message).casefold()
        )
        return ProviderResult(
            ok=ok,
            content=data.get("markdown") or response.text(),
            status_code=response.status,
            final_url=metadata.get("sourceURL") or case.url,
            title=metadata.get("title"),
            error_type=None if ok else ("rate_limit" if rate_limited else "api_error"),
            error_message=None if ok else error_message,
            metadata={
                **({"raw_response": response.text()} if case.task_kind == "file" else {}),
                "upstream_status": metadata.get("statusCode"),
                "cache_state": metadata.get("cacheState"),
                "proxy_used": metadata.get("proxyUsed"),
                "credits_used": metadata.get("creditsUsed"),
                "reason": payload.get("reason"),
                "retry_after_seconds": payload.get("retry_after_seconds"),
                "links": data.get("links") or [],
                "images": data.get("images") or [],
            },
        )
