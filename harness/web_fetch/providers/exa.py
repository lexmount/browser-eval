from __future__ import annotations

import base64
import hashlib

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from ..native_projection import native_answer_projection
from .base import Provider
from .http_client import inner_timeout_budget, request


# Exa's per-URL failure envelope contains free-form JSON.  Publish only the
# stable diagnostic enum values observed in the API contract; the complete
# envelope remains hash-bound private evidence below.
_PUBLIC_ERROR_TAGS = frozenset({
    "CRAWL_EMPTY_CONTENT",
    "CRAWL_HTTP_401",
    "CRAWL_LIVECRAWL_TIMEOUT",
    "CRAWL_NON_CANONICAL",
    "CRAWL_NOT_FOUND",
    "CRAWL_UNEXPECTED_CONTENT_TYPE",
    "CRAWL_UNKNOWN_ERROR",
})


def _rich_answer_links(extras: object) -> list[dict[str, str]]:
    """Project only Exa's explicit anchor/destination pairs into answer input."""
    if not isinstance(extras, dict) or not isinstance(extras.get("richLinks"), list):
        return []
    links = []
    for item in extras["richLinks"]:
        if not isinstance(item, dict):
            continue
        anchor, url = item.get("anchor"), item.get("url")
        if (isinstance(anchor, str) and anchor.strip()
                and isinstance(url, str) and url.strip()):
            links.append({"text": anchor, "href": url})
    return links


def _public_url_error(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    tag = value.get("tag")
    if tag in _PUBLIC_ERROR_TAGS:
        result["tag"] = tag
    http_status = value.get("httpStatusCode")
    if (
        isinstance(http_status, int)
        and not isinstance(http_status, bool)
        and 100 <= http_status <= 599
    ):
        result["httpStatusCode"] = http_status
    return result


class ExaContentsProvider(Provider):
    def native_route(self, case):
        return 'http', '/contents', ['results[].text', 'results[].extras', 'results[].title', 'results[].url']

    def native_http_arguments(self, case, timeout):
        body = {'urls': [case.url], 'text': {'verbosity': self.config.get('text_verbosity', 'full')},
                'maxAgeHours': int(self.config.get('max_age_hours', 0))}
        extras = {'links': 20, 'imageLinks': 20} if self.config.get('include_resource_links') else {}
        if self.config.get('rich_links_limit'):
            extras['richLinks'] = int(self.config.get('rich_links_limit', 100))
        if extras:
            body['extras'] = extras
        return (str(self.config.get('base_url', 'https://api.exa.ai')).rstrip('/') + '/contents',
                self.native_http_options(body, {'x-api-key': str(self.config.get('api_key', ''))}, timeout))

    def validate(self) -> list[str]:
        return [] if self.config.get("api_key") else ["api_key"]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.check_native_call(case)
        http_timeout = inner_timeout_budget(timeout, native_default=bool(self.config.get("uniform_native_parameters")))
        livecrawl_timeout_ms = min(
            int(self.config.get("livecrawl_timeout_ms", 15_000)),
            max(1, int(http_timeout * 1000)),
        )
        endpoint, arguments = self.native_http_arguments(case, http_timeout)
        request_body = arguments['json_body']
        if not self.config.get("uniform_native_parameters"):
            # Diagnostic calls keep the documented service deadline inside the
            # HTTP budget, plus the task's explicit named-link output contract.
            request_body['livecrawlTimeout'] = livecrawl_timeout_ms
            if case.acceptance_contract.get("schema") == "named_link":
                request_body.setdefault('extras', {})['richLinks'] = int(self.config.get("rich_links_limit", 100))
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
        statuses = payload.get("statuses")
        status = statuses[0] if isinstance(statuses, list) and statuses else {}
        if not isinstance(status, dict):
            status = {}
        status_name = status.get("status")
        public_status = status_name if status_name in {"success", "error"} else "unknown"
        public_url_error = _public_url_error(status.get("error"))
        content = result.get("text") if isinstance(result.get("text"), str) else ""
        ok = (
            200 <= response.status < 300
            and bool(content)
            and status_name in (None, "success")
        )
        if ok:
            error_type = error_message = None
        elif response.status in (401, 403):
            error_type, error_message = "authentication", f"HTTP {response.status}"
        elif not 200 <= response.status < 300:
            error_type, error_message = "api_error", f"HTTP {response.status}"
        elif status_name not in (None, "success"):
            error_type, error_message = "api_error", "Exa reported an unsuccessful URL status"
        else:
            error_type, error_message = "empty_content", "Exa returned no extractable text"
        result_extras = (result.get("extras") or {}) if ok else {}
        # Current API declares a breakdown object; retain legacy numeric totals.
        cost = payload.get("costDollars")
        if isinstance(cost, dict):
            cost = cost.get("total")
        return ProviderResult(
            ok=ok,
            content=content,
            status_code=response.status,
            final_url=result.get("url") or case.url,
            title=result.get("title") if ok else None,
            error_type=error_type,
            error_message=error_message,
            metadata={
                # This is private runner input, not report metadata. Retain the
                # exact envelope bytes so its identity can be independently
                # checked without changing the normalized answer artifact.
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "empty_content": 200 <= response.status < 300 and not content,
                "request_id": payload.get("requestId"),
                "url_status": public_status,
                "url_error": public_url_error,
                "livecrawl_timeout_ms": None if self.config.get("uniform_native_parameters") else livecrawl_timeout_ms,
                "cost_dollars": (
                    cost
                    if isinstance(cost, (int, float))
                    and not isinstance(cost, bool)
                    else None
                ),
                "extras": result_extras,
                "links": _rich_answer_links(result_extras),
                **({'native_answer_projection': native_answer_projection('exa_contents', case, payload, response.status)}
                   if ok or content or result.get('url') else {}),
            },
        )
