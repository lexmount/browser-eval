"""URL-only hosted Markdown retrieval; no Agent or browser-session fallback."""
from __future__ import annotations

import base64
import hashlib
import uuid

from lexbench_webfetch.models import EvalCase
from ..models import ProviderResult
from ..native_routes import NativeRoute
from ..native_projection import native_answer_projection
from ..network_dns import public_dns_context, validate_dns_mode
from .base import Provider
from .http_client import inner_timeout_budget, request


ENDPOINTS = {
    "crawl4ai_cloud": "https://api.crawl4ai.com/scrape",
    "brightdata_unlocker": "https://api.brightdata.com/request",
    "browser_use_fetch": "https://fetch.browser-use.com/fetch",
    "browserbase_fetch": "https://api.browserbase.com/v1/fetch",
    "deepseek_fetch": "https://api.deepseek.com/v1/tools/fetch",
}


def _http_status(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else None


class HostedFetchProvider(Provider):
    # The shared runner owns declared failure retries; the adapter calls once.
    native_configuration_fields = (*Provider.native_configuration_fields,
        'render', 'debug', 'proxies', 'proxy_country', 'engine', 'proxy', 'output_format')

    def __init__(self, name, config):
        super().__init__(name, config)
        self._session_id = str(uuid.uuid4())

    def begin_call(self):
        self._session_id = str(uuid.uuid4())

    def native_route(self, case):
        fields = {'brightdata_unlocker': ('response_body',), 'browser_use_fetch': ('body', 'final_url'),
                  'browserbase_fetch': ('content',), 'deepseek_fetch': ('content', 'title'),
                  'crawl4ai_cloud': ('markdown',)}
        return NativeRoute('http', ENDPOINTS[self.type], fields[self.type])

    def validate(self) -> list[str]:
        errors = [] if self.config.get("api_key") else ["api_key"]
        if self.type == "brightdata_unlocker" and not self.config.get("zone"):
            errors.append("zone")
        if self.type == "brightdata_unlocker" and "render" in self.config and not isinstance(self.config["render"], bool):
            errors.append("render must be boolean")
        if self.type == "brightdata_unlocker" and "debug" in self.config and not isinstance(self.config["debug"], bool):
            errors.append("debug must be boolean")
        try:
            validate_dns_mode(self.config.get("dns_mode", "system"))
        except ValueError as error:
            errors.append(str(error))
        if self.type == "browserbase_fetch" and not isinstance(self.config.get("proxies", False), bool):
            errors.append("proxies must be boolean")
        if self.type == "crawl4ai_cloud":
            if self.config.get("engine", "browser") not in ("auto", "browser-light", "browser"):
                errors.append("unsupported engine")
            if self.config.get("proxy", "none") not in ("none", "isp", "residential"):
                errors.append("unsupported proxy")
        output_format = self.config.get("output_format", "markdown")
        if output_format != "markdown":
            errors.append("unsupported output_format; this adapter requires markdown")
        return errors

    def native_http_arguments(self, case, timeout):
        budget = inner_timeout_budget(timeout, native_default=True)
        output_format = self.config.get("output_format", "markdown")
        if output_format != "markdown":
            raise ValueError("This adapter requires markdown output")
        key = str(self.config.get("api_key", ""))
        body: dict = {"url": case.url}
        if self.type == "brightdata_unlocker":
            headers = {"Authorization": f"Bearer {key}"}
            body.update(zone=self.config["zone"], format="raw", data_format="markdown")
            # Native Web Unlocker option, applied uniformly from the declared
            # config. It adds no caller-side browser, wait, or follow-up fetch.
            if "render" in self.config:
                body["render"] = "true" if self.config["render"] else "false"
            if "debug" in self.config:
                body["debug"] = self.config["debug"]
        elif self.type == "browserbase_fetch":
            headers = {"X-BB-API-Key": key}
            body.update(format=output_format, allowRedirects=True,
                        proxies=self.config.get("proxies", True))
        elif self.type == "crawl4ai_cloud":
            headers = {"Authorization": f"Bearer {key}"}
            body.update(format="md", engine=self.config.get("engine", "browser"),
                        proxy=self.config.get("proxy", "none"))
        elif self.type == "deepseek_fetch":
            headers = {"Authorization": f"Bearer {key}"}
        else:
            headers = {"X-Browser-Use-API-Key": key}
            body.update(method="GET", output_format=output_format,
                        follow_redirects=True, max_redirects=10,
                        proxy_country=self.config.get("proxy_country", "US"),
                        session_id=self._session_id)
        return self.config.get("endpoint", ENDPOINTS[self.type]), self.native_http_options(body, headers, budget)

    def fetch(self, case: EvalCase, timeout: float, *, native_route_ledger=None) -> ProviderResult:
        self.check_native_call(case)
        if case.task_kind not in {"body", "structure"} or case.track not in {"web", "dynamic", "challenge", "risk_control", "articles"}:
            return ProviderResult(False, "", error_type="unsupported_task",
                                  error_message="This adapter supports webpage text tasks only")
        # Direct diagnostic invocations also get a fresh session.
        if self.type == "browser_use_fetch":
            self.begin_call()
        budget = inner_timeout_budget(timeout, native_default=True)
        endpoint, arguments = self.native_http_arguments(case, budget)
        body = arguments['json_body']
        output_format = self.config.get("output_format", "markdown")
        dns_mode = self.config.get("dns_mode", "system")
        with public_dns_context(dns_mode, budget) as dns_trace:
            response = self.native_http_request(case, request, endpoint,
                native_route_ledger=native_route_ledger, **arguments)
        metadata = {
            "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
            "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
            "raw_response_bytes": len(response.body),
            "api_http_status": response.status, "output_contract": output_format,
            "dns_mode": dns_mode, "dns_trace": dns_trace,
            "client_retries": 0, "retrieval_hints_applied": False,
            "service_retry_policy": "provider_default_not_overridden",
            "request_timeout_seconds": budget,
        }
        # API envelopes are evidence, never page content. Bright Data's raw
        # mode deliberately returns the selected Markdown directly.
        api_ok = 200 <= response.status < 300
        content, status, final_url, title = "", None, None, None
        malformed = False
        service_error = False
        if self.type == "brightdata_unlocker":
            try:
                content = response.body.decode("utf-8") if api_ok else ""
            except UnicodeDecodeError:
                malformed = True
            metadata["native_answer_projection"] = native_answer_projection(
                self.type, case, {}, response.status)
            metadata["upstream_http_status_available"] = False
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                malformed = True
            else:
                if self.type == "browserbase_fetch":
                    content = payload.get("content")
                    status = _http_status(payload.get("statusCode", payload.get("status_code")))
                    service_error = payload.get("encoding") == "base64" or bool(payload.get("error"))
                elif self.type == "crawl4ai_cloud":
                    content = payload.get("markdown")
                    status = _http_status(payload.get("origin_status"))
                    service_error = (bool(payload.get("error")) or payload.get("success") is False
                                     or payload.get("ok") is False)
                    metadata["upstream_http_status_available"] = status is not None
                elif self.type == "deepseek_fetch":
                    content = payload.get("content")
                    title = payload.get("title")
                    service_error = bool(payload.get("error"))
                    metadata["upstream_http_status_available"] = False
                else:
                    content = payload.get("body")
                    status = _http_status(payload.get("status_code"))
                    final_url = payload.get("final_url") if isinstance(payload.get("final_url"), str) else None
                    service_error = bool(payload.get("error")) or bool(payload.get("is_binary"))
                metadata["request_id"] = payload.get("id") if isinstance(payload.get("id"), str) else None
                malformed = (not isinstance(content, str) or
                             (not isinstance(title, str) if self.type == "deepseek_fetch" else
                              False if self.type == "crawl4ai_cloud" else status is None))
                content = content if isinstance(content, str) else ""
                if self.type == "deepseek_fetch" and isinstance(payload.get("error"), dict):
                    metadata["service_error"] = payload["error"]
        if not api_ok:
            # Keep error envelopes in raw evidence, not in scored page text.
            content = ""
            error_type = ("access_denied" if response.status == 403 and self.type == "crawl4ai_cloud" else
                          "security_policy" if response.status == 403 and self.type == "deepseek_fetch" else
                          "authentication" if response.status in {401, 403} else
                          "rate_limit" if response.status == 429 else
                          "quota" if response.status == 402 else "api_error")
            message = f"Fetch API returned HTTP {response.status}; inspect retained response"
        elif service_error:
            error_type, message = "api_error", "Fetch service reported an error or binary output; inspect retained response"
        elif malformed:
            error_type, message = "invalid_response", "Fetch API returned an invalid text response envelope"
        elif status is not None and not 200 <= status < 300:
            error_type, message = "http_error", f"Target page returned HTTP {status}"
        elif not content.strip():
            error_type, message = "empty_content", "Fetch API returned empty Markdown"
        else:
            error_type = message = None
        if self.type != "brightdata_unlocker" and isinstance(payload, dict):
            metadata['native_answer_projection'] = native_answer_projection(
                self.type, case, payload, response.status, interface=output_format)
        if self.type == "crawl4ai_cloud":
            metadata["request_parameters"] = body
            metadata["native_browser_logs_available"] = False
        if self.type == "browserbase_fetch":
            metadata["proxies"] = self.config.get("proxies", True)
        if self.type == "brightdata_unlocker":
            metadata["render"] = self.config.get("render")
        if self.type == "browser_use_fetch":
            metadata["proxy_country"] = body["proxy_country"]
        return ProviderResult(
            ok=error_type is None, content=content, status_code=status,
            final_url=final_url, title=title if isinstance(title, str) else None,
            error_type=error_type, error_message=message, metadata=metadata,
        )
