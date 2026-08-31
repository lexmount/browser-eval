from __future__ import annotations

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import request


class CloudflareMarkdownProvider(Provider):
    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        response = request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/markdown",
            method="POST",
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body={"url": case.url, "gotoOptions": {"waitUntil": self.config.get("wait_until", "networkidle2")}},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        content = payload.get("result") if isinstance(payload.get("result"), str) else ""
        errors = payload.get("errors") or []
        ok = 200 <= response.status < 300 and payload.get("success") is True and bool(content)
        meta = payload.get("meta") or {}
        return ProviderResult(
            ok=ok,
            content=content or response.text(),
            status_code=response.status,
            final_url=meta.get("finalUrl") or case.url,
            title=meta.get("title"),
            error_type=None if ok else ("authentication" if response.status in (401, 403) else "api_error"),
            error_message=None if ok else ((errors[0].get("message") if errors else None) or f"HTTP {response.status}"),
            metadata={"upstream_status": meta.get("status"), "redirect_chain": meta.get("redirectChain"), "errors": errors},
        )


class CloudflareContentProvider(Provider):
    """Rendered HTML diagnostic. It is deliberately excluded from the readable-text leaderboard."""

    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        response = request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/content",
            method="POST",
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body={"url": case.url, "gotoOptions": {"waitUntil": self.config.get("wait_until", "networkidle2")}},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        content = payload.get("result") if isinstance(payload.get("result"), str) else ""
        errors = payload.get("errors") or []
        ok = 200 <= response.status < 300 and payload.get("success") is True and bool(content)
        return ProviderResult(
            ok=ok,
            content=content or response.text(),
            status_code=response.status,
            final_url=case.url,
            error_type=None if ok else ("authentication" if response.status in (401, 403) else "api_error"),
            error_message=None if ok else ((errors[0].get("message") if errors else None) or f"HTTP {response.status}"),
            metadata={"output_contract": "rendered_html", "errors": errors},
        )
