"""Port recovery with a stand-in server; the fake handshake forces each ordering under test.

The real server binaries are not run. A foreign CDP server that answers before this
server has exited cannot be told apart at the handshake, so it is only caught when this
server's exit is seen after the caller finishes.
"""

import base64
import socket
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from harness.web_fetch.providers import headless
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

    def __init__(
        self, tmp_path, script=SERVER, *, foreign_is_cdp=False, held=1, reject_all=False,
        server_exits_first=True,
    ):
        self.binary = tmp_path / "server"
        self.binary.write_text(script.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.foreign_is_cdp = foreign_is_cdp
        self.reject_all = reject_all
        self.server_exits_first = server_exits_first
        self.listeners = [socket.create_server(("127.0.0.1", 0)) for _ in range(held)]
        self.forced = [listener.getsockname()[1] for listener in self.listeners]
        self.taken = set(self.forced)
        self.processes, self.browsers = [], []
        self.playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=self.connect))

    def connect(self, url, timeout):
        if self.reject_all:
            raise ConnectionError("handshake rejected")
        if int(url.rsplit(":", 1)[1]) in self.taken:
            if self.server_exits_first:
                # Force the order under test: this server exits before the handshake returns.
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

    def run(self, engine="obscura", metadata=None, during=None):
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
                    if during is not None:
                        during()
                    else:
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


def test_handshake_rejected_before_the_server_exits_still_restarts_on_a_new_port(tmp_path):
    harness = Harness(tmp_path)
    taken = set(harness.taken)
    calls = []
    connect = harness.connect

    def reject_first(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("not a CDP endpoint")
        return connect(url, timeout)

    harness.playwright.chromium.connect_over_cdp = reject_first
    with patch.object(headless, "NATIVE_CDP_BIND_EXIT_GRACE_SECONDS", 20):
        browser = harness.run()
    assert len(harness.processes) == 2
    assert int(calls[0].rsplit(":", 1)[1]) in taken
    assert browser.url == calls[1] and int(calls[1].rsplit(":", 1)[1]) not in taken


def test_handshake_failure_from_a_running_server_is_raised_without_a_restart(tmp_path):
    harness = Harness(tmp_path, held=0, reject_all=True)
    with patch.object(headless, "NATIVE_CDP_BIND_EXIT_GRACE_SECONDS", 0.05):
        with pytest.raises(ConnectionError, match="handshake rejected"):
            harness.run()
    assert len(harness.processes) == 1


@pytest.mark.parametrize(
    "output", ["", "print('unrelated', file=sys.stderr)"], ids=["silent", "unrelated-log"]
)
def test_handshake_error_survives_a_server_exit_without_a_port_conflict(tmp_path, output):
    script = SERVER.replace("time.sleep(60)", f"{output}\ntime.sleep(.2)\nsys.exit(0)")
    harness = Harness(tmp_path, script, held=0, reject_all=True)
    with patch.object(headless, "NATIVE_CDP_BIND_EXIT_GRACE_SECONDS", 20):
        with pytest.raises(ConnectionError, match="handshake rejected"):
            harness.run()
    assert len(harness.processes) == 1
    assert harness.processes[0].returncode == 0


def test_silent_exit_before_any_handshake_reports_the_exit_code(tmp_path):
    harness = Harness(tmp_path, "#!{python}\nimport sys\nsys.exit(3)\n", held=0)
    with pytest.raises(RuntimeError, match="exited with code 3"):
        harness.run()
    assert len(harness.processes) == 1


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


def wait_for_server_exit(harness):
    return lambda: harness.processes[-1].wait(20)


def test_foreign_cdp_server_that_answers_first_fails_the_call(tmp_path):
    harness = Harness(tmp_path, foreign_is_cdp=True, server_exits_first=False)
    metadata = {}
    with pytest.raises(RuntimeError, match="(?s)exited with code 1 while.*(?i:in use)"):
        harness.run(engine="moli", metadata=metadata, during=wait_for_server_exit(harness))
    assert len(harness.processes) == 1
    assert harness.browsers[0].closed
    assert metadata["diagnostic_log_process_completed"] is True


def test_caller_error_is_kept_when_the_server_also_exited(tmp_path):
    harness = Harness(tmp_path, foreign_is_cdp=True, server_exits_first=False)

    def fail():
        harness.processes[-1].wait(20)
        raise LookupError("caller failure")

    with pytest.raises(LookupError, match="caller failure"):
        harness.run(during=fail)
    assert harness.browsers[0].closed


def test_silent_exit_while_the_browser_is_in_use_fails_the_call(tmp_path):
    script = SERVER.replace("time.sleep(60)", "time.sleep(.2)")
    harness = Harness(tmp_path, script, held=0)
    with pytest.raises(RuntimeError, match="exited with code 0 while"):
        harness.run(during=wait_for_server_exit(harness))
    assert harness.browsers[0].closed


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
