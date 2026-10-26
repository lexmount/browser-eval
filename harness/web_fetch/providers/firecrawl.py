from __future__ import annotations

import base64
import binascii
import hashlib
import math
from urllib.parse import urlsplit

from lexbench_webfetch.models import EvalCase

from harness.web_fetch.models import ProviderResult
from harness.web_fetch.native_projection import native_answer_projection
from harness.web_fetch.native_routes import NativeRoute
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.providers.http_client import request
from harness.web_fetch.retrieval_hints import native_default_case, plan_for_case


class FirecrawlProvider(Provider):
    def native_route(self, case) -> NativeRoute:
        fields = (['data.rawBase64'] if case.task_kind == 'file' else
                  ['data.markdown', 'data.links', 'data.images', 'data.videos', 'data.video'])
        return NativeRoute('http', '/v2/scrape', tuple(fields + ['data.metadata.title', 'data.metadata.url']))

    _PROXY_MAX_CREDITS = {"basic": 1, "enhanced": 1, "auto": 1}
    _VIDEO_FORMAT_CREDITS = 5

    def native_http_arguments(self, case, timeout):
        formats = ['rawBase64'] if case.task_kind == 'file' else list(self.config.get('formats', ['markdown']))
        if case.track == 'video' and case.task_kind == 'resource' and 'video' not in formats:
            formats.append('video')
        body = {'url': case.url, 'formats': formats, 'maxAge': int(self.config.get('max_age', 0)),
                'proxy': str(self.config.get('proxy', 'auto'))}
        if 'only_main_content' in self.config:
            body['onlyMainContent'] = self.config['only_main_content']
        if case.task_kind == 'file':
            body['parsers'] = []
        headers = {'Authorization': 'Bearer '+str(self.config['api_key'])} if self.config.get('api_key') else {}
        return (str(self.config.get('base_url', 'https://api.firecrawl.dev')).rstrip('/') + '/v2/scrape',
                self.native_http_options(body, headers, timeout))

    def diagnostic_http_arguments(self, case, timeout):
        endpoint, arguments = self.native_http_arguments(case, timeout)
        arguments['json_body']['timeout'] = min(int(timeout * 1000), 300_000)
        return endpoint, arguments

    def validate(self) -> list[str]:
        if "only_main_content" in self.config and not isinstance(self.config["only_main_content"], bool):
            return ["only_main_content must be boolean"]
        # Hosted Scrape requires Bearer auth. A separately configured local or
        # self-hosted endpoint may intentionally run without an API key.
        host = urlsplit(str(self.config.get("base_url", "https://api.firecrawl.dev"))).hostname
        if host == "api.firecrawl.dev" and not str(self.config.get("api_key") or "").strip():
            return ["api_key"]
        return []

    def capacity_credits_for_cases(
        self,
        cases: list[EvalCase],
        *,
        runs: int,
        max_failure_retries: int,
    ) -> int:
        """Return the declared Firecrawl credit bound for an exact case scope."""
        if (type(runs) is not int or runs < 1
                or type(max_failure_retries) is not int or max_failure_retries < 0):
            raise ValueError("invalid Firecrawl run or retry capacity bound")
        base_credits = self.config.get("capacity_credits_per_call", 1)
        if (
            isinstance(base_credits, bool)
            or not isinstance(base_credits, int)
            or base_credits < 1
        ):
            raise ValueError("invalid Firecrawl capacity_credits_per_call")
        per_run = sum(
            self._VIDEO_FORMAT_CREDITS
            if case.track == "video" and case.task_kind == "resource"
            else base_credits
            for case in cases
        )
        return per_run * runs * (max_failure_retries + 1)

    def preflight_capacity(
        self,
        declared_calls: int,
        timeout: float = 10,
        *,
        required_credits: int | None = None,
    ) -> None:
        """Fail before a hosted cohort when its declared calls exceed capacity.

        The benchmark configuration declares the base Scrape credit bound.
        When the caller supplies an exact case-derived bound, native video
        resource calls include their documented five-credit total.
        File-delivery calls disable PDF parsing below, so they do not add an
        unknown page-count charge to this bound.
        No returned account values are persisted in benchmark evidence.
        """
        if isinstance(declared_calls, bool) or not isinstance(declared_calls, int):
            raise ValueError("Firecrawl declared call count must be a positive integer")
        if declared_calls < 1:
            raise ValueError("Firecrawl declared call count must be a positive integer")
        host = urlsplit(str(self.config.get("base_url", "https://api.firecrawl.dev"))).hostname
        if host != "api.firecrawl.dev":
            return
        api_key = str(self.config.get("api_key") or "").strip()
        if not api_key:
            raise ValueError("Firecrawl capacity preflight requires api_key")
        proxy = str(self.config.get("proxy", "auto")).strip()
        minimum_per_call = self._PROXY_MAX_CREDITS.get(proxy)
        if minimum_per_call is None:
            raise ValueError("Firecrawl proxy must be basic, enhanced or auto")
        configured_per_call = self.config.get(
            "capacity_credits_per_call", minimum_per_call
        )
        if (
            isinstance(configured_per_call, bool)
            or not isinstance(configured_per_call, int)
            or configured_per_call < minimum_per_call
        ):
            raise ValueError(
                "Firecrawl capacity_credits_per_call is below the configured proxy bound"
            )
        try:
            http_timeout = self.http_timeout_budget(timeout)
        except ValueError as error:
            raise ValueError("invalid Firecrawl capacity preflight timeout") from error
        base_url = str(self.config.get("base_url", "https://api.firecrawl.dev")).rstrip("/")
        response = request(
            f"{base_url}/v2/team/credit-usage",
            method="GET",
            timeout=http_timeout,
            max_bytes=100_000,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("Firecrawl capacity preflight returned invalid JSON") from error
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        remaining = data.get("remainingCredits")
        if not (200 <= response.status < 300 and payload.get("success") is True):
            raise ValueError(
                f"Firecrawl capacity preflight failed (HTTP {response.status})"
            )
        if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
            raise ValueError("Firecrawl capacity preflight returned invalid remainingCredits")
        required = (
            declared_calls * configured_per_call
            if required_credits is None
            else required_credits
        )
        if isinstance(required, bool) or not isinstance(required, int) or required < 1:
            raise ValueError("Firecrawl required credit bound must be a positive integer")
        if remaining < required:
            raise ValueError(
                "Firecrawl account capacity is insufficient for the declared cohort; "
                f"requires at least {required} credits"
            )

    def fetch(self, case: EvalCase, timeout: float, *, native_route_ledger=None) -> ProviderResult:
        self.check_native_call(case)
        try:
            http_timeout = self.http_timeout_budget(timeout)
        except ValueError:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message="invalid Firecrawl timeout budget",
            )
        service_timeout_ms = min(int(http_timeout * 1000), 300_000)
        if service_timeout_ms < 1000:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message="Firecrawl timeout budget is below the API minimum",
            )
        file_route = case.task_kind == "file"
        native_video_route = case.track == "video" and case.task_kind == "resource"
        endpoint, arguments = self.request_arguments(case, http_timeout)
        request_body = arguments['json_body']
        route = "v2_scrape_raw_base64" if file_route else "v2_scrape"
        wait_metadata = None
        dynamic_wait_strategy = None
        if case.track == "dynamic" and not native_default_case(case):
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
            effective_timeout_ms = min(service_timeout_ms, plan.timeout_ms)
            if effective_timeout_ms < 1000:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="invalid dynamic retrieval_hints v1",
                )
            request_body["timeout"] = effective_timeout_ms
            dynamic_wait_strategy = str(
                self.config.get("dynamic_wait_strategy", "actions")
            ).strip()
            if dynamic_wait_strategy == "wait_for":
                # Firecrawl documents waitFor as an additional delay after its
                # own smart wait.  It is available where Scrape Actions are
                # region-restricted, but it cannot enforce a selector or count.
                wait_for_ms = min(plan.delay_ms, max(effective_timeout_ms - 1, 0))
                request_body["waitFor"] = wait_for_ms
                route = "v2_scrape_dynamic_wait_for"
                unsupported = ["wait_until"]
                if plan.frame_selector:
                    unsupported.append("child_frame_selector")
                unsupported.extend(["selector", "minimum_count"])
                wait_metadata = {
                    "strategy": "predeclared_settle_delay",
                    "selector": plan.selector,
                    "frame_selector": plan.frame_selector,
                    "minimum_count": plan.minimum_count,
                    "selector_enforced": False,
                    "minimum_count_enforced": False,
                    "completion_support": "approximate",
                    "unsupported": unsupported,
                    "delay_ms": wait_for_ms,
                    "requested_delay_ms": plan.delay_ms,
                    "timeout_ms": effective_timeout_ms,
                    "wait_until": plan.wait_until,
                    "smart_wait": True,
                }
            elif dynamic_wait_strategy == "actions":
                actions = []
                unsupported = ["wait_until"]
                if plan.frame_selector:
                    # A Scrape wait action has no frame target.  Sending the child
                    # selector would falsely claim that the iframe condition was
                    # enforced, so retain only the bounded settle delay.
                    unsupported.extend(["child_frame_selector", "selector"])
                else:
                    actions.append({"type": "wait", "selector": plan.selector})
                if plan.minimum_count > 1:
                    unsupported.append("minimum_count")
                if plan.delay_ms:
                    actions.append({"type": "wait", "milliseconds": plan.delay_ms})
                if actions:
                    request_body["actions"] = actions
                route = "v2_scrape_dynamic"
                selector_enforced = not bool(plan.frame_selector)
                count_enforced = selector_enforced and plan.minimum_count == 1
                wait_metadata = {
                    "strategy": (
                        "selector_action_then_settle_delay"
                        if selector_enforced
                        else "predeclared_settle_delay"
                    ),
                    "selector": plan.selector,
                    "frame_selector": plan.frame_selector,
                    "minimum_count": plan.minimum_count,
                    "selector_requested": selector_enforced,
                    "selector_enforced": selector_enforced,
                    "minimum_count_enforced": count_enforced,
                    # The selector itself may be exact for a single top-level
                    # match, but Scrape Actions do not expose the plan's navigation
                    # lifecycle, so the full completion contract stays approximate.
                    "completion_support": "approximate",
                    "unsupported": unsupported,
                    "delay_ms": plan.delay_ms,
                    "timeout_ms": effective_timeout_ms,
                    "wait_until": plan.wait_until,
                    "smart_wait": True,
                }
            else:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message=(
                        "Firecrawl dynamic_wait_strategy must be actions or wait_for"
                    ),
                )
        response = self.native_http_request(case, request, endpoint, native_route_ledger=native_route_ledger, **arguments)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        api_ok = 200 <= response.status < 300 and payload.get("success") is True
        if wait_metadata is not None and dynamic_wait_strategy == "actions":
            # ``selector_enforced`` describes an action that the service
            # accepted and completed, not merely a field placed in a rejected
            # request. The separate requested bit keeps failed calls
            # diagnosable without overstating their execution semantics.
            wait_metadata["actions_accepted"] = api_ok
            wait_metadata["selector_enforced"] = bool(
                wait_metadata.get("selector_requested") and api_ok
            )
            wait_metadata["minimum_count_enforced"] = bool(
                wait_metadata.get("minimum_count_enforced") and api_ok
            )
        file_bytes = b""
        invalid_file_encoding = False
        if file_route and api_ok:
            raw_base64 = data.get("rawBase64")
            if isinstance(raw_base64, str) and raw_base64:
                try:
                    file_bytes = base64.b64decode(raw_base64, validate=True)
                except (binascii.Error, ValueError):
                    invalid_file_encoding = True
        content = data.get("markdown") if isinstance(data.get("markdown"), str) else ""
        videos: list[object] = []
        if api_ok and native_video_route:
            current_videos = data.get("videos")
            if isinstance(current_videos, list):
                videos.extend(
                    item for item in current_videos
                    if isinstance(item, (str, dict))
                )
            legacy_video = data.get("video")
            if isinstance(legacy_video, str) and legacy_video:
                videos.append(legacy_video)
        ok = api_ok and (
            bool(file_bytes)
            if file_route
            else bool(content) or bool(videos)
        )
        provider_error = (
            payload.get("error") if isinstance(payload.get("error"), str) else ""
        )
        provider_error_normalized = " ".join(provider_error.casefold().split())
        account_capacity_reason = None
        if response.status == 402:
            account_capacity_reason = (
                "insufficient_credits"
                if "insufficient credits" in provider_error_normalized
                else "payment_required"
            )
        rate_limited = (
            response.status == 429
            or (payload.get("reason") == "credits" and response.status != 402)
            or "rate limit" in provider_error_normalized
        )
        if ok:
            error_type = error_message = None
        elif account_capacity_reason == "insufficient_credits":
            error_type = "account_capacity"
            error_message = "Firecrawl account has insufficient credits"
        elif account_capacity_reason:
            error_type = "account_capacity"
            error_message = "Firecrawl account capacity is unavailable (HTTP 402)"
        elif rate_limited:
            error_type, error_message = "rate_limit", f"HTTP {response.status}"
        elif not api_ok:
            error_type, error_message = "api_error", f"HTTP {response.status}"
        elif file_route and invalid_file_encoding:
            error_type = "response_validation"
            error_message = "Firecrawl returned invalid rawBase64"
        elif file_route:
            error_type, error_message = "empty_file", "Firecrawl returned no raw file bytes"
        elif native_video_route:
            error_type = "empty_content"
            error_message = "Firecrawl returned no extractable markdown or video resources"
        else:
            error_type = "empty_content"
            error_message = "Firecrawl returned no extractable markdown"
        source_url = (
            metadata.get("sourceURL") if isinstance(metadata.get("sourceURL"), str) else None
        )
        # sourceURL echoes the input; url records the observed destination.
        final_url = metadata.get("url")
        final_url = final_url if isinstance(final_url, str) and final_url else None
        content_type = (
            metadata.get("contentType")
            if isinstance(metadata.get("contentType"), str) else None
        )
        return ProviderResult(
            ok=ok,
            content=content if ok and not file_route else "",
            status_code=response.status,
            final_url=final_url,
            title=metadata.get("title") if ok and isinstance(metadata.get("title"), str) else None,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "route": route,
                "wait": wait_metadata,
                "requested_formats": request_body["formats"],
                "requested_proxy": request_body["proxy"],
                "requested_dynamic_wait_strategy": dynamic_wait_strategy,
                "requested_cache_max_age_ms": request_body["maxAge"],
                "requested_parsers": request_body.get("parsers"),
                "service_timeout_ms": request_body.get("timeout"),
                "client_http_timeout_ms": int(http_timeout * 1000),
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "upstream_status": (
                    metadata.get("statusCode")
                    if isinstance(metadata.get("statusCode"), int) else None
                ),
                "cache_state": (
                    metadata.get("cacheState")
                    if isinstance(metadata.get("cacheState"), str) and api_ok else None
                ),
                "proxy_used": (
                    metadata.get("proxyUsed")
                    if isinstance(metadata.get("proxyUsed"), str) and api_ok else None
                ),
                "credits_used": (
                    metadata.get("creditsUsed")
                    if isinstance(metadata.get("creditsUsed"), (int, float))
                    and not isinstance(metadata.get("creditsUsed"), bool) else None
                ),
                "reason": payload.get("reason") if payload.get("reason") == "credits" else None,
                "account_capacity_reason": account_capacity_reason,
                "retry_after_seconds": (
                    payload.get("retry_after_seconds")
                    if isinstance(payload.get("retry_after_seconds"), (int, float))
                    and not isinstance(payload.get("retry_after_seconds"), bool) else None
                ),
                "links": data.get("links") if ok and isinstance(data.get("links"), list) else [],
                "images": data.get("images") if ok and isinstance(data.get("images"), list) else [],
                "videos": videos if ok else [],
                **({'native_answer_projection': native_answer_projection('firecrawl', case, payload, response.status)} if ok or final_url is not None else {}),
                **(
                    {
                        "raw_file": {
                            "target_url": case.url,
                            "source_url": source_url or case.url,
                            "content_type": content_type,
                            "bytes": len(file_bytes),
                            "sha256": hashlib.sha256(file_bytes).hexdigest(),
                        }
                    }
                    if file_bytes else {}
                ),
            },
            files=[{
                "data": file_bytes,
                "target_url": case.url,
                "source_url": source_url or case.url,
                "content_type": content_type,
            }] if file_bytes else [],
        )
