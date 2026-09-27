"""CowAgent-free execution of frozen BrowserTool replay calls over Chromium CDP."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from playwright.sync_api import sync_playwright

from harness.browser_use.runner import BENCHMARK_VIEWPORT
from harness.trajectory_replay.public_webarena.executor import execute_steps

SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS = 1_500
SNAPSHOT_NAVIGATION_GRACE_MS = 300


def _navigation_interrupted_snapshot(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "navigation to finish",
            "execution context was destroyed",
            "cannot find context with specified id",
            "frame was detached",
            "inspected target navigated or closed",
        )
    )


@dataclass
class ReplayResult:
    status: str
    result: str


class PlaywrightReplayTool:
    """Expose the small BrowserTool contract consumed by the shared replay runner.

    The shared snapshot-ref path still dispatches DOM click/fill/select itself,
    preserving its compiled-ref behavior on either backend.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        engine: str = "chromium",
        headers: dict[str, str] | None = None,
        request_bridge_origins: set[str] | None = None,
    ):
        if engine not in {"chromium", "moli", "lightpanda", "obscura", "kitesurf"}:
            raise ValueError(f"unknown Playwright replay engine: {engine}")
        self._engine = engine
        self._supports_multiple_pages = engine not in {"lightpanda", "obscura", "kitesurf"}
        self._supports_network_reset_cdp = engine not in {"obscura", "kitesurf"}
        self._supports_storage_cdp = engine not in {"lightpanda", "obscura", "kitesurf"}
        self._native_login_form_submit = engine == "kitesurf"
        self._fresh_context_per_task = True
        self._supports_context_request_cookie_sharing = engine not in {
            "lightpanda",
            "kitesurf",
        }
        self.har_capture_transport = "playwright" if engine == "kitesurf" else "cdp"
        self._layout_sessions: dict[Any, Any] = {}
        self._viewport_sessions: dict[Any, Any] = {}
        self._layout_connection_poisoned = False
        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        self.runtime_metadata: dict[str, Any] | None = None
        self.request_bridge_stats = {"requests": 0, "failures": 0}
        self._request_bridge_session = None
        self._request_bridge_origins: frozenset[str] = frozenset()
        try:
            connect_options: dict[str, Any] = {"timeout": 90_000}
            if headers is not None:
                connect_options["headers"] = dict(headers)
            self._browser = self._playwright.chromium.connect_over_cdp(
                endpoint, **connect_options
            )
            if engine == "kitesurf":
                browser_session = self._browser.new_browser_cdp_session()
                try:
                    version = browser_session.send("Browser.getVersion")
                finally:
                    browser_session.detach()
                self.runtime_metadata = {
                    key: version.get(key)
                    for key in (
                        "protocolVersion",
                        "product",
                        "revision",
                        "userAgent",
                        "jsVersion",
                    )
                    if version.get(key) is not None
                }
                if len(self._browser.contexts) != 1:
                    raise RuntimeError("Kitesurf did not expose exactly one default context")
                self._context = self._browser.contexts[0]
                if len(self._context.pages) != 1:
                    raise RuntimeError("Kitesurf did not expose exactly one default page")
                self._page = self._context.pages[0]
                self._install_request_bridge(request_bridge_origins or set())
                self._page.set_viewport_size(dict(BENCHMARK_VIEWPORT))
                # Kitesurf applies emulation to the next document; its initial
                # provider-owned about:blank retains the service default height.
                self._page.goto("about:blank", wait_until="load", timeout=10_000)
            else:
                self._context = self._browser.new_context(
                    viewport=dict(BENCHMARK_VIEWPORT), locale="en-US"
                )
                self._page = self._context.new_page()
        except Exception:
            self.close()
            raise
        self._owns_cdp_context = True

    def _install_request_bridge(self, origins: set[str]) -> None:
        """Fetch selected private origins from the local Playwright driver."""

        parsed_origins = [urlsplit(origin) for origin in origins]
        if any(
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            for parsed in parsed_origins
        ):
            raise ValueError("request bridge origins must be bare HTTP(S) origins")
        normalized = {
            f"{parsed.scheme}://{parsed.netloc}" for parsed in parsed_origins
        }
        if not normalized:
            return
        self._request_bridge_origins = frozenset(normalized)
        self._request_bridge_session = requests.Session()
        self._request_bridge_session.trust_env = False

        def selected(url: str) -> bool:
            parsed = urlsplit(url)
            return f"{parsed.scheme}://{parsed.netloc}" in normalized

        def bridge(route) -> None:
            self.request_bridge_stats["requests"] += 1
            try:
                request = route.request
                headers = {
                    name: value
                    for name, value in request.all_headers().items()
                    if name.lower()
                    not in {
                        "accept-encoding",
                        "connection",
                        "content-length",
                        "host",
                        "proxy-connection",
                        "signature",
                        "signature-agent",
                        "signature-input",
                        "transfer-encoding",
                    }
                }
                response = self._request_bridge_session.request(
                    method=request.method,
                    url=request.url,
                    headers=headers,
                    data=request.post_data_buffer,
                    allow_redirects=False,
                    timeout=60,
                )
                self._sync_request_bridge_cookies(
                    str(response.url), response.cookies
                )
                response_headers = {
                    name: value
                    for name, value in response.headers.items()
                    if name.lower()
                    not in {
                        "connection",
                        "content-encoding",
                        "content-length",
                        "set-cookie",
                        "transfer-encoding",
                    }
                }
                route.fulfill(
                    status=response.status_code,
                    headers=response_headers,
                    body=response.content,
                )
            except Exception:
                self.request_bridge_stats["failures"] += 1
                try:
                    route.abort("failed")
                except Exception:
                    pass
                raise

        self._context.route(selected, bridge)

    def _sync_request_bridge_cookies(
        self, response_url: str, response_cookies: Any
    ) -> None:
        parsed = urlsplit(response_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._request_bridge_origins:
            raise RuntimeError("request bridge cookie response origin is not allowed")
        host = (parsed.hostname or "").lower()
        cookies = []
        for cookie in response_cookies:
            item: dict[str, Any] = {
                "name": cookie.name,
                "value": cookie.value,
                "secure": bool(cookie.secure),
                "httpOnly": any(
                    str(name).lower() == "httponly" for name in cookie._rest
                ),
            }
            if cookie.domain_specified:
                cookie_domain = str(cookie.domain).lstrip(".").lower()
                if host != cookie_domain and not host.endswith(f".{cookie_domain}"):
                    raise RuntimeError("request bridge rejected an invalid cookie domain")
                item["domain"] = cookie.domain
                item["path"] = cookie.path or "/"
            else:
                if (cookie.path or "/") != "/":
                    raise RuntimeError(
                        "request bridge cannot preserve a non-root host-only cookie path"
                    )
                item["url"] = f"{origin}/"
            if cookie.expires is not None:
                item["expires"] = float(cookie.expires)
            same_site = next(
                (
                    value
                    for name, value in cookie._rest.items()
                    if str(name).lower() == "samesite"
                ),
                None,
            )
            if isinstance(same_site, str) and same_site.capitalize() in {
                "Strict",
                "Lax",
                "None",
            }:
                item["sameSite"] = same_site.capitalize()
            cookies.append(item)
        if cookies:
            self._context.add_cookies(cookies)

    def _request_login_form(
        self,
        *,
        source_origin: str,
        method: str,
        url: str,
        fields: list[Any],
        referer: str,
    ) -> Any:
        current_method = method
        current_url = url
        current_fields: list[Any] | None = fields
        current_referer = referer
        for _ in range(11):
            parsed = urlsplit(current_url)
            if f"{parsed.scheme}://{parsed.netloc}" != source_origin:
                raise RuntimeError("login form bridge rejected a cross-origin redirect")
            kwargs: dict[str, Any] = {
                "method": current_method,
                "url": current_url,
                "headers": {"referer": current_referer},
                "allow_redirects": False,
                "timeout": 60,
            }
            if current_fields is not None:
                kwargs["params" if current_method == "get" else "data"] = current_fields
            response = self._request_bridge_session.request(**kwargs)
            self._sync_request_bridge_cookies(str(response.url), response.cookies)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location:
                raise RuntimeError("login form bridge redirect has no location")
            current_referer = str(response.url)
            current_url = urljoin(str(response.url), location)
            if response.status_code == 303 or (
                response.status_code in {301, 302} and current_method == "post"
            ):
                current_method = "get"
                current_fields = None
        raise RuntimeError("login form bridge exceeded ten redirects")

    def _submit_login_form(self, page: Any, submitter: Any) -> str:
        form = submitter.evaluate(
            """element => {
                const form = element.form;
                if (!form) throw new Error('login submitter has no form');
                const fields = Array.from(new FormData(form), ([name, value]) => [
                    name,
                    typeof value === 'string' ? value : value.name,
                ]);
                if (element.name) fields.push([element.name, element.value || '']);
                return {action: form.action, method: form.method || 'get', fields};
            }"""
        )
        action = str(form.get("action") or "")
        method = str(form.get("method") or "get").lower()
        fields = form.get("fields")
        source = urlsplit(page.url)
        source_origin = f"{source.scheme}://{source.netloc}"
        target = urlsplit(action)
        if (
            method not in {"get", "post"}
            or not isinstance(fields, list)
            or target.scheme != source.scheme
            or target.netloc != source.netloc
        ):
            raise RuntimeError("login form bridge rejected an invalid submission")
        response = self._request_login_form(
            source_origin=source_origin,
            method=method,
            url=action,
            fields=fields,
            referer=page.url,
        )
        final = urlsplit(str(response.url))
        if (
            not 200 <= response.status_code < 300
            or final.scheme != source.scheme
            or final.netloc != source.netloc
        ):
            raise RuntimeError(
                f"login form bridge returned invalid status or origin: {response.status_code}"
            )
        page.goto(str(response.url), wait_until="domcontentloaded", timeout=90_000)
        return str(response.url)

    def _wait_for_login_ready(
        self,
        page: Any,
        selector: str,
        *,
        exact_text: str | None = None,
        timeout_ms: int = 60_000,
    ) -> None:
        """Wait for a login marker without Kitesurf's broken Locator visibility path."""

        if self._engine != "kitesurf":
            if exact_text is not None:
                page.get_by_role(
                    "button", name=exact_text, exact=True
                ).wait_for(timeout=timeout_ms)
                return
            locator = page.locator(selector)
            if "," in selector:
                locator = locator.first
            locator.wait_for(timeout=timeout_ms)
            return
        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            ready = page.evaluate(
                """({selector, exactText}) => Array.from(
                    document.querySelectorAll(selector)
                ).some(element => {
                    if (exactText !== null && element.textContent.trim() !== exactText) {
                        return false;
                    }
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                })""",
                {"selector": selector, "exactText": exact_text},
            )
            if ready:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"login readiness marker did not appear: {selector}")
            page.wait_for_timeout(100)

    def _get_service(self) -> PlaywrightReplayTool:
        return self

    def _submit(self, callback):
        return callback()

    @property
    def layout_observer(self):
        return self._publish_page_layout if self._engine == "moli" else None

    @property
    def viewport_observer(self):
        if self._engine != "obscura":
            return None
        return self._observe_page_viewport

    def _observe_page_viewport(self, page: Any) -> dict[str, int | float]:
        """Read renderer state through CDP instead of echoing Playwright config."""

        sessions = getattr(self, "_viewport_sessions", None)
        if sessions is None:
            sessions = self._viewport_sessions = {}
        session = sessions.get(page)
        if session is None:
            session = self._context.new_cdp_session(page)
            sessions[page] = session
        metrics = session.send("Page.getLayoutMetrics")
        viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport")
        if not isinstance(viewport, dict):
            raise RuntimeError("Obscura returned no observed visual viewport")
        runtime = session.send(
            "Runtime.evaluate",
            {"expression": "devicePixelRatio", "returnByValue": True},
        )
        scale = (runtime.get("result") or {}).get("value")
        width = viewport.get("clientWidth")
        height = viewport.get("clientHeight")
        if not all(isinstance(value, (int, float)) for value in (width, height, scale)):
            raise RuntimeError("Obscura returned incomplete viewport metrics")
        return {
            "width": width,
            "height": height,
            "device_scale_factor": scale,
        }

    @property
    def failure_capture_available(self) -> bool:
        """Whether another renderer command can still complete reliably."""

        return not self._layout_connection_poisoned

    def _publish_page_layout(self, page: Any, timeout_ms: int = 5_000) -> None:
        """Publish Moli's current layout before Playwright consumes geometry.

        Moli keeps ordinary geometry reads side effect free. Its explicit CDP
        extension publishes the same layout tree without paint, rasterization,
        or screenshot encoding.
        """

        if getattr(self, "_engine", "chromium") != "moli":
            return
        if timeout_ms < 1:
            raise TimeoutError("Moli layout publication budget exhausted")
        sessions = getattr(self, "_layout_sessions", None)
        if sessions is None:
            sessions = self._layout_sessions = {}
        session = sessions.get(page)
        if session is None:
            session = self._context.new_cdp_session(page)
            sessions[page] = session

        async def publish() -> dict[str, Any]:
            return await asyncio.wait_for(
                session._impl_obj.send("Page.getLayoutMetrics", {"publishLayout": True}),
                timeout=timeout_ms / 1_000,
            )

        try:
            result = session._sync(publish())
        except TimeoutError as exc:
            sessions.pop(page, None)
            # The renderer still owns the timed-out CDP command.  Sending a
            # screenshot or graceful close behind it can block forever, so let
            # the catalog runner terminate the isolated browser process.
            self._layout_connection_poisoned = True
            raise TimeoutError("Moli layout publication timed out") from exc
        if not isinstance(result, dict) or not isinstance(result.get("layoutViewport"), dict):
            raise RuntimeError("Moli layout publication returned no layout metrics")

    def _snapshot(self, selector: str, timeout: int) -> str:
        deadline = time.monotonic() + timeout / 1_000

        def remaining(maximum: int | None = None) -> int:
            value = int((deadline - time.monotonic()) * 1_000)
            if value < 1:
                raise TimeoutError("snapshot action budget exhausted")
            return value if maximum is None else min(value, maximum)

        def observe(maximum: int | None = None) -> str:
            observation_deadline = deadline
            if maximum is not None:
                observation_deadline = min(observation_deadline, time.monotonic() + maximum / 1_000)
            while True:
                try:
                    self._page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=min(remaining(maximum), SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS),
                    )
                except Exception:
                    pass
                self._publish_page_layout(self._page, remaining())
                locator = self._page.locator(selector).first
                wait_ms = int((observation_deadline - time.monotonic()) * 1_000)
                if wait_ms < 1:
                    raise TimeoutError("snapshot selector did not become visible")
                try:
                    locator.wait_for(state="visible", timeout=min(wait_ms, 1_000))
                except Exception as exc:
                    if _navigation_interrupted_snapshot(exc):
                        raise
                    if (
                        "timeout" not in str(exc).lower()
                        or time.monotonic() >= observation_deadline
                    ):
                        raise
                    continue
                return locator.aria_snapshot(timeout=remaining(maximum))

        try:
            return observe()
        except Exception as exc:
            if not _navigation_interrupted_snapshot(exc):
                raise
            self._page.wait_for_load_state(
                "domcontentloaded",
                timeout=remaining(SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS),
            )
            self._page.wait_for_timeout(min(remaining(), SNAPSHOT_NAVIGATION_GRACE_MS))
            return observe()

    def execute(self, arguments: dict[str, Any]) -> ReplayResult:
        action = arguments.get("action")
        if action == "snapshot":
            try:
                selector = str(arguments.get("selector") or "body")
                timeout = int(arguments.get("timeout_ms", 30_000))
                if timeout < 1:
                    raise ValueError("snapshot timeout_ms must be positive")
                value = self._snapshot(selector, timeout)
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError("snapshot returned an empty observation")
                return ReplayResult("success", value[: int(arguments.get("max_chars", 40_000))])
            except Exception as exc:
                return ReplayResult("error", f"Snapshot failed: {exc}")
        records: list[dict[str, Any]] = []
        try:
            execute_steps(
                self._page,
                [arguments],
                trace=records,
                layout_observer=self.layout_observer,
            )
        except Exception as exc:
            return ReplayResult("error", f"{type(exc).__name__}: {exc}")
        value = records[0]["output"]
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return ReplayResult("success", value)

    def close(self) -> None:
        bridge_close_errors: list[BaseException] = []
        if getattr(self, "_request_bridge_session", None) is not None:
            try:
                unroute_all = getattr(self._context, "unroute_all", None)
                if unroute_all is not None:
                    unroute_all(behavior="wait")
            except BaseException as exc:
                bridge_close_errors.append(exc)
            try:
                self._request_bridge_session.close()
            except BaseException as exc:
                bridge_close_errors.append(exc)
        try:
            if not getattr(self, "_layout_connection_poisoned", False):
                try:
                    if self._context is not None and self._engine != "kitesurf":
                        self._context.close()
                finally:
                    if self._browser is not None:
                        self._browser.close()
        except BaseException as exc:
            for bridge_error in bridge_close_errors:
                exc.add_note(f"request bridge close also failed: {bridge_error}")
            raise
        finally:
            self._playwright.stop()
        if bridge_close_errors:
            raise bridge_close_errors[0]
