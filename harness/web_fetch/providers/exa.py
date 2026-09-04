from __future__ import annotations

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import request


class ExaContentsProvider(Provider):
    def validate(self) -> list[str]:
        return [] if self.config.get("api_key") else ["api_key"]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        response = request(
            f"{str(self.config.get('base_url', 'https://api.exa.ai')).rstrip('/')}/contents",
            method="POST",
            timeout=timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"x-api-key": str(self.config.get("api_key", ""))},
            json_body={
                "urls": [case.url],
                "text": True,
                "maxAgeHours": int(self.config.get("max_age_hours", 0)),
                **({"extras": {"links": 20, "imageLinks": 20}} if self.config.get("include_resource_links") else {}),
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        result = (payload.get("results") or [{}])[0]
        statuses = payload.get("statuses") or []
        status = statuses[0] if statuses else {}
        ok = 200 <= response.status < 300 and bool(result.get("text")) and status.get("status", "success") == "success"
        error = status.get("error") or {}
        return ProviderResult(
            ok=ok,
            content=result.get("text") or response.text(),
            status_code=response.status,
            final_url=result.get("url") or case.url,
            title=result.get("title"),
            error_type=None if ok else ("authentication" if response.status in (401, 403) else error.get("tag", "api_error")),
            error_message=None if ok else (error.get("tag") or payload.get("error") or f"HTTP {response.status}"),
            metadata={**({"raw_response": response.text()} if case.task_kind == "file" else {}), "request_id": payload.get("requestId"), "url_status": status, "cost_dollars": payload.get("costDollars"), "extras": result.get("extras") or {}},
        )
