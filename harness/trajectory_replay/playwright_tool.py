"""CowAgent-free execution of frozen BrowserTool replay calls over Chromium CDP."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import sync_playwright

from harness.browser_use.runner import BENCHMARK_VIEWPORT
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.public_webarena.webarena_environment import wall_clock_timeout

SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS = 1_500
SNAPSHOT_NAVIGATION_GRACE_MS = 300
SESSION_SETUP_TIMEOUT_SECONDS = 120


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
        setup_timeout_seconds: float = SESSION_SETUP_TIMEOUT_SECONDS,
    ):
        if engine not in {"chromium", "moli", "lightpanda", "obscura"}:
            raise ValueError(f"unknown Playwright replay engine: {engine}")
        self._engine = engine
        self._supports_multiple_pages = engine != "lightpanda"
        self._supports_network_reset_cdp = engine != "obscura"
        self._supports_storage_cdp = engine not in {"lightpanda", "obscura"}
        self._fresh_context_per_task = True
        self._supports_context_request_cookie_sharing = engine != "lightpanda"
        self._layout_sessions: dict[Any, Any] = {}
        self._layout_connection_poisoned = False
        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        try:
            with wall_clock_timeout(
                setup_timeout_seconds, operation="Playwright replay session setup"
            ):
                self._browser = self._playwright.chromium.connect_over_cdp(
                    endpoint, timeout=90_000
                )
                self._context = self._browser.new_context(
                    viewport=dict(BENCHMARK_VIEWPORT), locale="en-US"
                )
                self._page = self._context.new_page()
        except Exception:
            self.close()
            raise
        self._owns_cdp_context = True

    def _get_service(self) -> PlaywrightReplayTool:
        return self

    def _submit(self, callback):
        return callback()

    @property
    def layout_observer(self):
        return self._publish_page_layout if self._engine == "moli" else None

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
        if getattr(self, "_layout_connection_poisoned", False):
            self._playwright.stop()
            return
        try:
            if self._context is not None:
                self._context.close()
        finally:
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                self._playwright.stop()
