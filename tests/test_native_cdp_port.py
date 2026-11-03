"""A native CDP server that loses its port is restarted, never impersonated."""

import base64
import socket
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harness.web_fetch.providers.headless import NATIVE_CDP_PORT_ATTEMPTS, native_cdp_browser

SERVER = """#!{python}
import socket, sys, time
port = int(sys.argv[sys.argv.index('--port') + 1])
server = socket.socket()
try:
    server.bind(('127.0.0.1', port))
except OSError as error:
    print(error, file=sys.stderr)
    sys.exit(1)
server.listen()
time.sleep(60)
"""


class Browser:
    def __init__(self, url):
        self.url, self.closed = url, False

    def close(self):
        self.closed = True


class Harness:
    """Run a real server binary, with candidate ports forced onto held sockets."""

    def __init__(self, tmp_path, script=SERVER, *, foreign_is_cdp=False, held=1):
        self.binary = tmp_path / "server"
        self.binary.write_text(script.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.foreign_is_cdp = foreign_is_cdp
        self.listeners = [socket.create_server(("127.0.0.1", 0)) for _ in range(held)]
        self.forced = [listener.getsockname()[1] for listener in self.listeners]
        self.taken = set(self.forced)
        self.processes, self.browsers = [], []
        self.playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=self.connect))

    def connect(self, url, timeout):
        if int(url.rsplit(":", 1)[1]) in self.taken:
            # The real handshake outlives a server that exits on its failed bind.
            self.processes[-1].wait(20)
            if not self.foreign_is_cdp:
                raise ConnectionError("not a CDP endpoint")
        self.browsers.append(Browser(url))
        return self.browsers[-1]

    def candidate_socket(self):
        harness = self

        class Candidate(socket.socket):
            def getsockname(self):
                return (
                    ("127.0.0.1", harness.forced.pop(0)) if harness.forced else super().getsockname()
                )

        return SimpleNamespace(socket=Candidate, create_connection=socket.create_connection)

    def run(self, engine="obscura", metadata=None):
        popen = subprocess.Popen

        def start(*args, **kwargs):
            self.processes.append(popen(*args, **kwargs))
            return self.processes[-1]

        request = {"binary": str(self.binary), "engine": engine}
        with (patch("harness.web_fetch.providers.headless.socket", self.candidate_socket()),
              patch("harness.web_fetch.providers.headless.subprocess.Popen", start)):
            try:
                with native_cdp_browser(
                    self.playwright, request, time.monotonic() + 30, metadata=metadata
                ) as browser:
                    assert all(process.poll() is not None for process in self.processes[:-1])
                    assert self.processes[-1].poll() is None
                    return browser
            finally:
                for listener in self.listeners:
                    listener.close()
                assert all(process.poll() is not None for process in self.processes)


def test_server_on_a_free_port_starts_once(tmp_path):
    harness = Harness(tmp_path, held=0)
    browser = harness.run()
    assert len(harness.processes) == 1
    assert browser.closed


def test_foreign_listener_that_rejects_cdp_causes_a_restart_on_a_new_port(tmp_path):
    harness = Harness(tmp_path)
    taken = set(harness.taken)
    browser = harness.run()
    assert len(harness.processes) == 2
    assert int(browser.url.rsplit(":", 1)[1]) not in taken


def test_foreign_cdp_server_is_not_used_after_this_server_lost_the_port(tmp_path):
    harness = Harness(tmp_path, foreign_is_cdp=True)
    taken = set(harness.taken)
    metadata = {}
    browser = harness.run(engine="moli", metadata=metadata)
    foreign, used = harness.browsers
    assert int(foreign.url.rsplit(":", 1)[1]) in taken and foreign.closed
    assert used is browser and int(used.url.rsplit(":", 1)[1]) not in taken
    log = base64.b64decode(metadata["_diagnostic_log_b64"]).decode()
    assert "in use" in log.lower() and metadata["diagnostic_log_process_completed"] is True


def test_restart_attempts_are_bounded_and_report_the_server_log(tmp_path):
    harness = Harness(tmp_path, held=NATIVE_CDP_PORT_ATTEMPTS)
    with pytest.raises(RuntimeError, match="(?i)in use"):
        harness.run()
    assert len(harness.processes) == NATIVE_CDP_PORT_ATTEMPTS
    assert harness.browsers == []


def test_other_startup_failure_is_not_retried(tmp_path):
    harness = Harness(
        tmp_path,
        "#!{python}\nimport sys\nprint('unsupported flag', file=sys.stderr)\nsys.exit(3)\n",
        held=0,
    )
    with pytest.raises(RuntimeError, match="unsupported flag"):
        harness.run()
    assert len(harness.processes) == 1
    assert harness.browsers == []


def test_missing_binary_reports_no_process_log(tmp_path):
    harness = Harness(tmp_path, held=0)
    harness.binary.unlink()
    metadata = {}
    with pytest.raises(FileNotFoundError):
        harness.run(engine="moli", metadata=metadata)
    assert "_diagnostic_log_b64" not in metadata
