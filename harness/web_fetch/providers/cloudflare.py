from __future__ import annotations

import base64
import hashlib
import math
from urllib.parse import urlencode

from ..models import EvalCase, ProviderResult
from ..retrieval_hints import plan_for_case
from .base import Provider
from .http_client import inner_timeout_budget, request


def _origin_status(value: object) -> int | None:
    """Normalize Cloudflare's target-page status without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _interface_error(*, response_status: int,
                     envelope_success: bool,
                     origin_status: int | None) -> tuple[str | None, str | None]:
    """Classify target retrieval without using answer presence as success."""
    if response_status in (401, 403):
        return "authentication", f"HTTP {response_status}"
    if response_status == 429:
        return "rate_limit", f"HTTP {response_status}"
    if not 200 <= response_status < 300:
        return "api_error", f"HTTP {response_status}"
    if not envelope_success:
        return "api_error", "Cloudflare reported an unsuccessful response"
    if origin_status is not None and not 200 <= origin_status < 300:
        return "http_status", f"Origin returned HTTP {origin_status}"
    return None, None


class CloudflareMarkdownProvider(Provider):
    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        http_timeout = inner_timeout_budget(timeout)
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        query = urlencode({"browser": self.config["browser"]}) if self.config.get("browser") else ""
        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/markdown"
        )
        request_body = {
            "url": case.url,
            "gotoOptions": {
                "waitUntil": self.config.get("wait_until", "networkidle2"),
                "timeout": service_timeout_ms,
            },
            "actionTimeout": service_timeout_ms,
        }
        route = "browser_rendering_markdown"
        wait_metadata = None
        if case.track == "dynamic":
            try:
                if not math.isfinite(timeout) or timeout <= 0:
                    raise ValueError
                plan = plan_for_case(case)
                assert plan is not None
            except (KeyError, TypeError, ValueError):
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="invalid dynamic retrieval_hints v1",
                )

            # The Markdown endpoint exposes waitForSelector, but no selector-count
            # or arbitrary-function wait. Use only independently declared,
            # answer-free readiness signals and disclose unsupported semantics.
            effective_timeout_ms = min(service_timeout_ms, plan.timeout_ms)
            wait_until = {
                "networkidle": "networkidle2",
                "domcontentloaded": "domcontentloaded",
                "load": "load",
            }.get(plan.wait_until)
            if wait_until is None or effective_timeout_ms < 1:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="invalid dynamic retrieval_hints v1",
                )
            request_body["gotoOptions"] = {
                "waitUntil": wait_until,
                "timeout": effective_timeout_ms,
            }
            request_body["actionTimeout"] = effective_timeout_ms
            # Quick Actions documents these as independent readiness timers, not
            # an ordered selector-then-delay chain. Supplying both can let the
            # shorter timer win, so choose the strongest single native signal.
            if plan.frame_selector:
                # The endpoint cannot target a child browsing context. For this
                # route a predeclared delay is stronger than the outer iframe,
                # which is normally present before its child content is ready.
                request_body["waitForTimeout"] = plan.delay_ms
                strategy = "predeclared_delay"
            else:
                request_body["waitForSelector"] = {
                    "selector": plan.selector,
                    "timeout": effective_timeout_ms,
                }
                strategy = "selector"
            count_enforced = plan.frame_selector is None and plan.minimum_count == 1
            exact = count_enforced and plan.delay_ms == 0
            unsupported = []
            if plan.frame_selector:
                unsupported.extend(["child_frame_selector", "selector"])
            if plan.minimum_count != 1:
                unsupported.append("minimum_count")
            if plan.delay_ms and not plan.frame_selector:
                unsupported.append("post_selector_delay")
            route = "browser_rendering_markdown_dynamic"
            wait_metadata = {
                "strategy": strategy,
                "selector": plan.selector,
                "frame_selector": plan.frame_selector,
                "minimum_count": plan.minimum_count,
                "minimum_count_enforced": count_enforced,
                "completion_support": "exact" if exact else "approximate",
                "unsupported": unsupported,
                "delay_ms": plan.delay_ms,
                "timeout_ms": effective_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
                "wait_until": wait_until,
            }
        response = request(
            endpoint + ("?" + query if query else ""),
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body=request_body,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        content = payload.get("result") if isinstance(payload.get("result"), str) else ""
        errors = payload.get("errors")
        errors = errors if isinstance(errors, list) else []
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        origin_headers = meta.get("headers")
        origin_headers = origin_headers if isinstance(origin_headers, dict) else {}
        upstream_status = _origin_status(meta.get("status"))
        envelope_success = payload.get("success") is True
        error_type, error_message = _interface_error(
            response_status=response.status,
            envelope_success=envelope_success,
            origin_status=upstream_status,
        )
        ok = error_type is None
        return ProviderResult(
            ok=ok,
            # The API envelope remains private, but its declared result is answer
            # evidence even when the origin is blocked or the result is empty.
            # Task scoring, not request classification, decides its usefulness.
            content=content if envelope_success else "",
            status_code=response.status,
            final_url=meta.get("finalUrl") or case.url,
            title=meta.get("title"),
            error_type=error_type,
            error_message=error_message,
            metadata={
                # The runner removes this internal value into hash-bound private
                # evidence before writing public results.
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "route": route,
                "wait": wait_metadata,
                "browser": self.config.get("browser"),
                "service_timeout_ms": service_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
                "upstream_status": upstream_status,
                "empty_content": envelope_success and not content,
                "content_type": origin_headers.get("content-type"),
                "redirect_chain": meta.get("redirectChain"),
                "error_count": len(errors),
                "retry_after": response.headers.get("retry-after"),
                "browser_ms_used": response.headers.get("x-browser-ms-used"),
            },
        )


class CloudflareContentProvider(Provider):
    """Rendered HTML diagnostic. It is deliberately excluded from the readable-text leaderboard."""

    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        http_timeout = inner_timeout_budget(timeout)
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        response = request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/content",
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body={
                "url": case.url,
                "gotoOptions": {
                    "waitUntil": self.config.get("wait_until", "networkidle2"),
                    "timeout": service_timeout_ms,
                },
                "actionTimeout": service_timeout_ms,
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        content = payload.get("result") if isinstance(payload.get("result"), str) else ""
        errors = payload.get("errors")
        errors = errors if isinstance(errors, list) else []
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        upstream_status = _origin_status(meta.get("status"))
        envelope_success = payload.get("success") is True
        error_type, error_message = _interface_error(
            response_status=response.status,
            envelope_success=envelope_success,
            origin_status=upstream_status,
        )
        ok = error_type is None
        return ProviderResult(
            ok=ok,
            content=content if envelope_success else "",
            status_code=response.status,
            final_url=case.url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "output_contract": "rendered_html",
                "upstream_status": upstream_status,
                "empty_content": envelope_success and not content,
                "error_count": len(errors),
                "service_timeout_ms": service_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
            },
        )
