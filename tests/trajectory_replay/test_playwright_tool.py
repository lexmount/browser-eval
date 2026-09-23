"""Exercise the CowAgent-free path against a real Chromium page."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from harness.browser_use.runner import _prepare_browser_viewport
from harness.trajectory_replay.public_webarena.executor import execute_steps
from harness.trajectory_replay.remote_execution import _browser_command, _wait_cdp
from harness.trajectory_replay.webarena_replay import _execute_replay_call


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the pinned macOS Chromium")
def test_playwright_backend_executes_portable_and_snapshot_ref_calls(tmp_path: Path) -> None:
    binary = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not binary.is_file():
        pytest.skip("Chrome is unavailable")
    page_path = tmp_path / "form.html"
    page_path.write_text(
        """<input id="name"><button id="save" onclick="
        document.querySelector('#result').textContent =
        document.querySelector('#name').value">Save</button><p id="result"></p>""",
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
            status, _ = _execute_replay_call(
                tool, {"action": "navigate", "url": page_path.as_uri()}
            )
            assert status == "success"
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
