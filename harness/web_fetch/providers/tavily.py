from __future__ import annotations

import base64
import hashlib

from ..models import EvalCase, ProviderResult
from .base import Provider
from .http_client import inner_timeout_budget, request


# Tavily's failed_results entries are free-form.  Keep the useful public API
# diagnostics, but never project arbitrary response text into public evidence.
_PUBLIC_FAILURE_ERRORS = frozenset({
    "404 page not found",
    "Failed to fetch url",
})


def _public_failure_errors(values: list[object]) -> list[str]:
    return [
        error
        for value in values
        if isinstance(value, dict)
        and isinstance((error := value.get("error")), str)
        and error in _PUBLIC_FAILURE_ERRORS
    ]


class TavilyExtractProvider(Provider):
    def validate(self) -> list[str]:
        return [] if self.config.get("api_key") else ["api_key"]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        http_timeout = inner_timeout_budget(timeout)
        configured_extract_timeout = float(
            self.config.get("extract_timeout_seconds", 60.0)
        )
        extract_timeout = min(configured_extract_timeout, http_timeout, 60.0)
        request_body = {
            "urls": [case.url],
            "extract_depth": self.config.get("extract_depth", "advanced"),
            "format": "markdown",
            "include_images": bool(self.config.get("include_images", False)),
        }
        # Tavily accepts 1-60 seconds. Production benchmark budgets are above
        # that lower bound; for unusually short diagnostic runs, the client
        # deadline remains authoritative and we omit an invalid API value.
        if extract_timeout >= 1.0:
            request_body["timeout"] = round(extract_timeout, 3)
        response = request(
            f"{str(self.config.get('base_url', 'https://api.tavily.com')).rstrip('/')}/extract",
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_key', '')}"},
            json_body=request_body,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        results = payload.get("results")
        result = results[0] if isinstance(results, list) and results else {}
        if not isinstance(result, dict):
            result = {}
        failures = payload.get("failed_results")
        failures = failures if isinstance(failures, list) else []
        public_failure_errors = _public_failure_errors(failures)
        content = result.get("raw_content") if isinstance(result.get("raw_content"), str) else ""
        ok = 200 <= response.status < 300 and bool(content)
        if ok:
            error_type = error_message = None
        elif response.status in (401, 403):
            error_type, error_message = "authentication", f"HTTP {response.status}"
        elif not 200 <= response.status < 300:
            error_type, error_message = "api_error", f"HTTP {response.status}"
        else:
            error_type, error_message = "empty_content", "Tavily returned no extractable content"
        return ProviderResult(
            ok=ok,
            content=content,
            status_code=response.status,
            final_url=(result.get("url") or case.url) if ok else case.url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                # The runner consumes this internal field into private evidence.
                # Keep the exact response bytes until that persistence boundary;
                # decoded text is not a byte-stable provider-call artifact.
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "empty_content": 200 <= response.status < 300 and not content,
                "response_time": (
                    payload.get("response_time")
                    if isinstance(payload.get("response_time"), (int, float))
                    and not isinstance(payload.get("response_time"), bool)
                    else None
                ),
                "failed_result_count": len(failures),
                "failed_result_errors": public_failure_errors,
                "extract_timeout_seconds": (
                    round(extract_timeout, 3) if extract_timeout >= 1.0 else None
                ),
                "images": (result.get("images") or payload.get("images") or []) if ok else [],
            },
        )
