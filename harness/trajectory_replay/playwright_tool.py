"""CowAgent-free execution of frozen BrowserTool replay calls over Chromium CDP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import sync_playwright

from harness.browser_use.runner import BENCHMARK_VIEWPORT
from harness.trajectory_replay.public_webarena.executor import execute_steps


@dataclass
class ReplayResult:
    status: str
    result: str


class PlaywrightReplayTool:
    """Expose the small BrowserTool contract consumed by the shared replay runner.

    The shared snapshot-ref path still dispatches DOM click/fill/select itself,
    preserving its compiled-ref behavior on either backend.
    """

    def __init__(self, endpoint: str):
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

    def execute(self, arguments: dict[str, Any]) -> ReplayResult:
        action = arguments.get("action")
        if action == "snapshot":
            try:
                selector = str(arguments.get("selector") or "body")
                value = self._page.locator(selector).first.aria_snapshot(timeout=30_000)
                return ReplayResult("success", value[: int(arguments.get("max_chars", 40_000))])
            except Exception as exc:
                return ReplayResult("error", f"Snapshot failed: {exc}")
        records: list[dict[str, Any]] = []
        try:
            execute_steps(self._page, [arguments], trace=records)
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
