"""Browser launch contracts independent of benchmark task or agent implementation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from harness.environments.browser import start_browser_container, wait_cdp


@pytest.mark.parametrize("cpus", [None, 1.5])
def test_launch_supports_an_additional_browser_with_explicit_resources(cpus):
    client = MagicMock()
    start_browser_container(
        client,
        engine="third-browser",
        image="example/browser@sha256:pinned",
        cdp_port=9333,
        run_id="test",
        cpus=cpus,
        memory="2g",
        network="isolated-test",
    )
    args, options = client.containers.run.call_args
    assert args == ("example/browser@sha256:pinned",)
    assert options["ports"] == {"9333/tcp": ("127.0.0.1", None)}
    assert options["mem_limit"] == "2g"
    assert options["network"] == "isolated-test"
    assert options["security_opt"] == ["no-new-privileges"]
    assert options.get("nano_cpus") == (None if cpus is None else 1_500_000_000)


def test_cdp_readiness_uses_the_published_address_and_preserves_websocket_path():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/json/version"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "webSocketDebuggerUrl": "ws://container.internal:9333/devtools/browser/id?token=test",
                    }
                ).encode()
            )

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{server.server_port}"
        assert wait_cdp(f"http://{host}", timeout_seconds=2) == (
            f"ws://{host}/devtools/browser/id?token=test"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cdp_timeout_includes_browser_container_logs():
    container = MagicMock()
    container.logs.return_value = b"browser could not start"
    with pytest.raises(RuntimeError, match="browser could not start"):
        wait_cdp("http://127.0.0.1:1", timeout_seconds=0, container=container)
    container.reload.assert_called_once()
