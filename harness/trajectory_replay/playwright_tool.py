"""CowAgent-free execution of frozen BrowserTool replay calls over Chromium CDP."""

from __future__ import annotations

import json
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import sync_playwright

from harness.browser_use.runner import BENCHMARK_VIEWPORT
from harness.trajectory_replay.public_webarena.executor import execute_steps

SNAPSHOT_PRIMARY_TIMEOUT_MS = 5_000
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

    def __init__(self, endpoint: str, *, engine: str = "chromium"):
        if engine not in {"chromium", "moli"}:
            raise ValueError(f"unknown Playwright replay engine: {engine}")
        self._engine = engine
        self._layout_sessions: dict[Any, Any] = {}
        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(endpoint, timeout=90_000)
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

    def _publish_page_layout(self, page: Any, timeout_ms: int = 5_000) -> None:
        """Publish Moli's current layout before Playwright consumes geometry.

        Moli intentionally computes layout on demand.  A raw CDP screenshot is
        the renderer boundary that publishes that layout without first asking
        Playwright to perform the geometry checks which depend on it.
        """

        if getattr(self, "_engine", "chromium") != "moli":
            return
        if timeout_ms < 1:
            raise TimeoutError("Moli layout publication budget exhausted")
        if threading.current_thread() is not threading.main_thread() or not hasattr(
            signal, "setitimer"
        ):
            raise RuntimeError("bounded Moli layout publication requires the main POSIX thread")
        sessions = getattr(self, "_layout_sessions", None)
        if sessions is None:
            sessions = self._layout_sessions = {}
        session = sessions.get(page)
        if session is None:
            session = self._context.new_cdp_session(page)
            sessions[page] = session
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        started = time.monotonic()

        def expire(_signum, _frame):
            raise TimeoutError("Moli layout publication timed out")

        try:
            signal.signal(signal.SIGALRM, expire)
            signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1_000)
            try:
                result = session.send("Page.captureScreenshot", {"format": "png"})
            except TimeoutError:
                sessions.pop(page, None)
                raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0] > 0:
                elapsed = time.monotonic() - started
                signal.setitimer(
                    signal.ITIMER_REAL,
                    max(0.001, previous_timer[0] - elapsed),
                    previous_timer[1],
                )
        if not isinstance(result, dict) or not result.get("data"):
            raise RuntimeError("Moli layout publication returned no screenshot data")

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
                self._publish_page_layout(self._page, remaining(1_000))
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
            return observe(SNAPSHOT_PRIMARY_TIMEOUT_MS)
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
                layout_observer=self._publish_page_layout,
            )
        except Exception as exc:
            return ReplayResult("error", f"{type(exc).__name__}: {exc}")
        value = records[0]["output"]
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return ReplayResult("success", value)

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                self._playwright.stop()
