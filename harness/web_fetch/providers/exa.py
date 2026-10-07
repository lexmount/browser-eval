from __future__ import annotations

import base64
import hashlib

from ..models import EvalCase, ProviderResult
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
    def validate(self) -> list[str]:
        return [] if self.config.get("api_key") else ["api_key"]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        http_timeout = inner_timeout_budget(timeout)
        livecrawl_timeout_ms = min(
            int(self.config.get("livecrawl_timeout_ms", 15_000)),
            max(1, int(http_timeout * 1000)),
        )
        extras = ({"links": 20, "imageLinks": 20}
                  if self.config.get("include_resource_links") else {})
        if self.config.get("rich_links_limit") or (case.acceptance_contract.get("schema") == "named_link" and not self.config.get("uniform_native_parameters")):
            # Bare links and surrounding text do not prove record ownership.
            # Exa's richLinks route returns the explicit anchor/URL pair needed
            # by the shared named-link answer contract.
            extras["richLinks"] = int(self.config.get("rich_links_limit", 100))
        response = request(
            f"{str(self.config.get('base_url', 'https://api.exa.ai')).rstrip('/')}/contents",
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"x-api-key": str(self.config.get("api_key", ""))},
            json_body={
                "urls": [case.url],
                "text": {"verbosity": self.config.get("text_verbosity", "full")},
                "maxAgeHours": int(self.config.get("max_age_hours", 0)),
                # Exa recommends 10-15 seconds when maxAgeHours can trigger a
                # live crawl. Keep that provider-side deadline inside the
                # runner's shorter HTTP budget rather than relying on Exa's
                # default and letting the client abandon a still-running job.
                "livecrawlTimeout": livecrawl_timeout_ms,
                **({"extras": extras} if extras else {}),
            },
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
        return ProviderResult(
            ok=ok,
            content=content,
            status_code=response.status,
            final_url=(result.get("url") or case.url) if ok else case.url,
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
                "livecrawl_timeout_ms": livecrawl_timeout_ms,
                "cost_dollars": (
                    payload.get("costDollars")
                    if isinstance(payload.get("costDollars"), (int, float))
                    and not isinstance(payload.get("costDollars"), bool)
                    else None
                ),
                "extras": result_extras,
                "links": _rich_answer_links(result_extras),
            },
        )
