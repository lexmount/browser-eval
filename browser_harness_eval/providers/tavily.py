from __future__ import annotations

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import request


class TavilyExtractProvider(Provider):
    def validate(self) -> list[str]:
        return [] if self.config.get("api_key") else ["api_key"]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        response = request(
            f"{str(self.config.get('base_url', 'https://api.tavily.com')).rstrip('/')}/extract",
            method="POST",
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_key', '')}"},
            json_body={
                "urls": [case.url],
                "extract_depth": self.config.get("extract_depth", "basic"),
                "format": "markdown",
                "include_images": bool(self.config.get("include_images", False)),
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        result = (payload.get("results") or [{}])[0]
        failures = payload.get("failed_results") or []
        ok = 200 <= response.status < 300 and bool(result.get("raw_content"))
        message = failures[0].get("error") if failures else payload.get("detail")
        return ProviderResult(
            ok=ok,
            content=result.get("raw_content") or response.text(),
            status_code=response.status,
            final_url=result.get("url") or case.url,
            error_type=None if ok else ("authentication" if response.status in (401, 403) else "api_error"),
            error_message=None if ok else (message or f"HTTP {response.status}"),
            metadata={"response_time": payload.get("response_time"), "failed_results": failures, "images": result.get("images") or payload.get("images") or []},
        )
