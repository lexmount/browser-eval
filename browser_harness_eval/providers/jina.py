from __future__ import annotations

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import request


class JinaReaderProvider(Provider):
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        base_url = str(self.config.get("base_url", "https://r.jina.ai")).rstrip("/")
        headers = {"Accept": "text/markdown"}
        if self.config.get("no_cache", True):
            headers["X-No-Cache"] = "true"
        if self.config.get("api_key"):
            headers["Authorization"] = f"Bearer {self.config['api_key']}"
        response = request(
            f"{base_url}/{case.url}",
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers=headers,
        )
        ok = 200 <= response.status < 300
        return ProviderResult(
            ok=ok,
            content=response.text(),
            status_code=response.status,
            final_url=case.url,
            error_type=None if ok else "http_error",
            error_message=None if ok else f"HTTP {response.status}",
            metadata={"content_type": response.headers.get("content-type")},
        )
