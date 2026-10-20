from __future__ import annotations

import base64
import hashlib

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from ..native_projection import native_answer_projection
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
    def native_route(self, case):
        return 'http', '/extract', ['results[].raw_content', 'results[].images', 'images', 'results[].url']

    def native_http_arguments(self, case, timeout):
        body = {'urls': [case.url], 'extract_depth': self.config.get('extract_depth', 'advanced'),
                'format': 'markdown', 'include_images': bool(self.config.get('include_images', False))}
        return (str(self.config.get('base_url', 'https://api.tavily.com')).rstrip('/') + '/extract',
                self.native_http_options(body, {'Authorization': 'Bearer '+str(self.config.get('api_key', ''))}, timeout))

    def validate(self) -> list[str]:
        return [] if self.config.get("api_key") else ["api_key"]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.check_native_call(case)
        http_timeout = inner_timeout_budget(timeout, native_default=bool(self.config.get("uniform_native_parameters")))
        configured_extract_timeout = float(
            self.config.get("extract_timeout_seconds", 60.0)
        )
        extract_timeout = min(configured_extract_timeout, http_timeout, 60.0)
        endpoint, arguments = self.native_http_arguments(case, http_timeout)
        request_body = arguments['json_body']
        # Diagnostic calls bound the service timeout; native calls retain the
        # same declared API defaults used by the pre-call route contract.
        if extract_timeout >= 1.0 and not self.config.get("uniform_native_parameters"):
            request_body["timeout"] = round(extract_timeout, 3)
        response = self.native_http_request(case, request, endpoint, **arguments)
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
            final_url=result.get("url") or case.url,
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
                "requested_extract_depth": request_body["extract_depth"],
                "requested_include_images": request_body["include_images"],
                "requested_format": request_body["format"],
                "extract_timeout_seconds": (
                    request_body.get("timeout")
                ),
                "images": (result.get("images") or payload.get("images") or []) if ok else [],
                **({'native_answer_projection': native_answer_projection('tavily_extract', case, payload, response.status)} if ok or content or result.get('url') else {}),
            },
        )
