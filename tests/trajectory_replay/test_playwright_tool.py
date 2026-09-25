"""Exercise the CowAgent-free path against a real Chromium page."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness.browser_use.runner import _prepare_browser_viewport
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.remote_execution import _browser_command, _wait_cdp
from harness.trajectory_replay.webarena_replay import _execute_replay_call


def _unit_tool(page, context, *, engine="chromium"):
    from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

    tool = PlaywrightReplayTool.__new__(PlaywrightReplayTool)
    tool._page = page
    tool._context = context
    tool._engine = engine
    return tool


def test_moli_layout_publication_uses_raw_cdp_and_detaches():
    events = []

    class Session:
        def send(self, method, params):
            events.append(("send", method, params))
            return {"data": "png-data"}

        def detach(self):
            events.append(("detach",))

    page = object()
    context = type(
        "Context",
        (),
        {"new_cdp_session": lambda self, selected: (
            events.append(("session", selected)) or Session()
        )},
    )()

    _unit_tool(page, context, engine="moli")._publish_page_layout(page)

    assert events == [
        ("session", page),
        ("send", "Page.captureScreenshot", {"format": "png"}),
        ("detach",),
    ]


def test_chromium_layout_publication_is_a_strict_noop():
    class Context:
        def new_cdp_session(self, _page):
            raise AssertionError("Chromium must not receive Moli layout publication")

    _unit_tool(object(), Context())._publish_page_layout(object())


def test_moli_layout_publication_fails_when_renderer_returns_no_image():
    class Session:
        def send(self, _method, _params):
            return {}

        def detach(self):
            pass

    context = type(
        "Context", (), {"new_cdp_session": lambda self, _page: Session()}
    )()

    with pytest.raises(RuntimeError, match="returned no screenshot data"):
        _unit_tool(object(), context, engine="moli")._publish_page_layout(object())


def test_snapshot_retries_native_observation_after_navigation_settles():
    events = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            events.append(("visible", kwargs))

        def aria_snapshot(self, **kwargs):
            events.append(("aria_snapshot", kwargs))
            if sum(event[0] == "aria_snapshot" for event in events) == 1:
                raise RuntimeError("Execution context was destroyed because of a navigation")
            return "- main: New profile document"

    class Page:
        def locator(self, selector):
            events.append(("locator", selector))
            return Locator()

        def wait_for_load_state(self, state, **kwargs):
            events.append(("load_state", state, kwargs))

        def wait_for_timeout(self, milliseconds):
            events.append(("wait", milliseconds))

    result = _unit_tool(Page(), object()).execute({"action": "snapshot"})

    assert result.status == "success"
    assert result.result == "- main: New profile document"
    assert sum(event[0] == "aria_snapshot" for event in events) == 2
    assert any(event[:2] == ("load_state", "domcontentloaded") for event in events)


def test_snapshot_never_returns_old_document_when_navigation_does_not_settle():
    events = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            events.append("visible")

        def aria_snapshot(self, **kwargs):
            events.append("aria")
            raise RuntimeError("Execution context was destroyed because of a navigation")

    class Page:
        def locator(self, selector):
            return Locator()

        def wait_for_load_state(self, state, **kwargs):
            events.append("load_state")
            raise TimeoutError("new document did not reach DOMContentLoaded")

    result = _unit_tool(Page(), object()).execute({"action": "snapshot"})

    assert result.status == "error"
    assert "did not reach DOMContentLoaded" in result.result
    assert events == ["visible", "aria", "load_state"]


def test_snapshot_does_not_hide_a_non_navigation_observation_error():
    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            pass

        def aria_snapshot(self, **kwargs):
            raise RuntimeError("Accessibility tree serialization failed")

    class Page:
        def locator(self, selector):
            return Locator()

    result = _unit_tool(Page(), object()).execute({"action": "snapshot"})
    assert result.status == "error"
    assert "Accessibility tree serialization failed" in result.result


def test_snapshot_hidden_selector_fails_before_observation():
    events = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            events.append(("visible", kwargs))
            raise TimeoutError("element remained hidden")

        def aria_snapshot(self, **kwargs):
            events.append(("aria", kwargs))
            return "old hidden document"

    class Page:
        def locator(self, selector):
            return Locator()

    result = _unit_tool(Page(), object()).execute(
        {"action": "snapshot", "selector": "#hidden", "timeout_ms": 20}
    )
    assert result.status == "error"
    assert "remained hidden" in result.result
    assert events
    assert all(event[0] == "visible" for event in events)


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_playwright_backend_executes_portable_and_snapshot_ref_calls(tmp_path: Path) -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    page_path = tmp_path / "form.html"
    page_path.write_text(
        """<input id="name"><button id="save" onclick="
        document.querySelector('#result').textContent =
        document.querySelector('#name').value">Save</button><p id="result"></p>
        <p id="hidden" hidden>must not be observed</p>""",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        _browser_command(
            argparse.Namespace(engine="chromium", binary=binary),
            port,
            str(tmp_path / "chrome-profile"),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_cdp(endpoint, process)
        from harness.trajectory_replay.playwright_tool import PlaywrightReplayTool

        tool = PlaywrightReplayTool(endpoint)
        try:
            assert _prepare_browser_viewport(tool)["verified"]
            started = time.monotonic()
            status, _ = _execute_replay_call(
                tool,
                {"action": "navigate", "url": page_path.as_uri(), "timeout_ms": 1_000},
            )
            assert status == "success"
            assert time.monotonic() - started <= 1.5
            status, result = _execute_replay_call(
                tool,
                {
                    "action": "snapshot",
                    "selector": "#hidden",
                    "timeout_ms": 100,
                },
            )
            assert status == "error"
            assert "hidden" in result.lower()
            status, _ = _execute_replay_call(
                tool,
                {
                    "action": "fill",
                    "selector": "#name",
                    "text": "Chromium replay",
                    "replay_mode": "snapshot_ref_semantics",
                },
            )
            assert status == "success"
            status, _ = _execute_replay_call(
                tool,
                {
                    "action": "click",
                    "selector": "#save",
                    "replay_mode": "snapshot_ref_semantics",
                },
            )
            assert status == "success"
            status, result = _execute_replay_call(
                tool,
                {"action": "evaluate", "script": "document.querySelector('#result').textContent"},
            )
            assert (status, result) == ("success", "Chromium replay")
            status, result = _execute_replay_call(
                tool,
                {"action": "evaluate", "script": "({saved:true,count:1})"},
            )
            assert status == "success"
            assert json.loads(result) == {"saved": True, "count": 1}
            output: dict[str, str] = {}
            tool._submit(
                lambda: execute_steps(
                    tool._page,
                    [{"action": "get_text", "selector": "#result", "save_as": "saved"}],
                    outputs=output,
                )
            )
            assert output == {"saved": "Chromium replay"}
        finally:
            tool.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
