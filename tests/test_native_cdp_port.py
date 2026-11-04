"""Server ownership and port recovery, with stand-in servers and real listening sockets.

The real server binaries are not run. The handshake is a real HTTP request to the port,
so a test can see which process answered. Stand-ins that have to outlive a step block on
stdin until the harness releases them. One that loses its port waits before it exits,
because that is the window in which another listener could be reached.
"""

import base64
import json
import socket
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

import pytest

from harness.web_fetch.providers.headless import NATIVE_CDP_PORT_ATTEMPTS, native_cdp_browser

SERVER = """#!{python}
import http.server, json, socketserver, sys, threading, time

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(dict(Browser='launched')).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass

port = int(sys.argv[sys.argv.index('--port') + 1])
try:
    server = socketserver.TCPServer(('127.0.0.1', port), Handler)
except OSError as error:
    print(error, file=sys.stderr, flush=True)
    time.sleep(.3)
    sys.exit(1)
threading.Thread(target=server.serve_forever, daemon=True).start()
sys.stdin.read()
"""
LINGERING_AFTER_LOST_PORT = SERVER.replace("time.sleep(.3)\n    sys.exit(1)", "sys.stdin.read()")
WILDCARD = SERVER.replace("'127.0.0.1'", "'0.0.0.0'")
# Bound but never listening, so the handshake cannot run and the exit is always seen first.
BOUND_THEN_FAILS = """#!{python}
import socket, sys
port = int(sys.argv[sys.argv.index('--port') + 1])
server = socket.socket()
try:
    server.bind(('127.0.0.1', port))
except OSError as error:
    print(error, file=sys.stderr)
    sys.exit(1)
print('unsupported flag', file=sys.stderr)
sys.exit(3)
"""


class ForeignCdpListener:
    """A real second CDP listener that answers any client and counts those requests."""

    def __init__(self):
        listener = self
        self.requests = 0

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                listener.requests += 1
                body = json.dumps({"Browser": "foreign"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *arguments):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def getsockname(self):
        return self.server.server_address

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Browser:
    def __init__(self, url, answered_by):
        self.url, self.answered_by, self.closed = url, answered_by, False

    def close(self):
        self.closed = True


class Harness:
    """Run a real server binary, with candidate ports forced onto held sockets."""

    def __init__(self, tmp_path, script=SERVER, *, foreign_cdp=False, held=1, reject_all=False):
        self.binary = tmp_path / "server"
        self.binary.write_text(script.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.reject_all = reject_all
        self.listeners = [
            ForeignCdpListener() if foreign_cdp else socket.create_server(("127.0.0.1", 0))
            for _ in range(held)
        ]
        self.forced = [listener.getsockname()[1] for listener in self.listeners]
        self.taken = set(self.forced)
        self.processes, self.browsers, self.handshakes = [], [], []
        self.playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=self.connect))

    def connect(self, url, timeout):
        self.handshakes.append(url)
        if self.reject_all:
            raise ConnectionError("handshake rejected")
        with urlopen(url + "/json/version", timeout=timeout / 1000) as response:
            self.browsers.append(Browser(url, json.load(response)["Browser"]))
        return self.browsers[-1]

    def candidate_socket(self):
        harness = self

        class Candidate(socket.socket):
            def getsockname(self):
                return (
                    ("127.0.0.1", harness.forced.pop(0)) if harness.forced else super().getsockname()
                )

        return SimpleNamespace(socket=Candidate, inet_ntoa=socket.inet_ntoa)

    def run(self, engine="obscura", metadata=None, during=None, budget=30):
        popen = subprocess.Popen

        def start(command, **kwargs):
            if command[0] != str(self.binary):
                return popen(command, **kwargs)
            self.processes.append(popen(command, stdin=subprocess.PIPE, **kwargs))
            return self.processes[-1]

        request = {"binary": str(self.binary), "engine": engine}
        with (patch("harness.web_fetch.providers.headless.socket", self.candidate_socket()),
              patch("harness.web_fetch.providers.headless.subprocess.Popen", start)):
            try:
                with native_cdp_browser(
                    self.playwright, request, time.monotonic() + budget, metadata=metadata
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
                for process in self.processes:
                    process.stdin.close()
                assert all(process.poll() is not None for process in self.processes)


def exit_server(harness):
    def release_and_wait():
        harness.processes[-1].stdin.close()
        harness.processes[-1].wait(20)

    return release_and_wait


def test_server_on_a_free_port_starts_once(tmp_path):
    harness = Harness(tmp_path, held=0)
    browser = harness.run()
    assert len(harness.processes) == 1
    assert browser.answered_by == "launched" and browser.closed


def test_second_cdp_listener_that_answers_first_is_never_reached(tmp_path):
    harness = Harness(tmp_path, foreign_cdp=True)
    foreign = harness.listeners[0]
    taken = set(harness.taken)
    metadata = {}
    browser = harness.run(engine="moli", metadata=metadata)
    assert foreign.requests == 0
    assert browser.answered_by == "launched" and browser.closed
    assert len(harness.processes) == 2
    assert harness.handshakes == [browser.url] and int(browser.url.rsplit(":", 1)[1]) not in taken
    log = base64.b64decode(metadata["_diagnostic_log_b64"]).decode()
    assert "in use" in log.lower() and metadata["diagnostic_log_process_completed"] is True


def test_second_cdp_listener_is_not_used_when_the_server_keeps_running_without_the_port(tmp_path):
    harness = Harness(tmp_path, LINGERING_AFTER_LOST_PORT, foreign_cdp=True)
    foreign = harness.listeners[0]
    with pytest.raises(TimeoutError, match="exceeded the call deadline"):
        harness.run(budget=1)
    assert foreign.requests == 0
    assert harness.handshakes == [] and len(harness.processes) == 1


def test_silent_listener_on_the_port_does_not_hold_the_call(tmp_path):
    harness = Harness(tmp_path)
    taken = set(harness.taken)
    browser = harness.run(budget=8)
    assert len(harness.processes) == 2
    assert int(browser.url.rsplit(":", 1)[1]) not in taken


def test_wildcard_listener_does_not_prove_the_exact_address(tmp_path):
    harness = Harness(tmp_path, WILDCARD, held=0)
    with pytest.raises(TimeoutError, match="exceeded the call deadline"):
        harness.run(budget=1)
    assert harness.handshakes == []


def test_handshake_failure_from_this_server_is_raised_without_a_restart(tmp_path):
    harness = Harness(tmp_path, held=0, reject_all=True)
    with pytest.raises(ConnectionError, match="handshake rejected"):
        harness.run()
    assert len(harness.processes) == 1


def test_handshake_failure_is_kept_when_the_server_also_exits(tmp_path):
    harness = Harness(tmp_path, held=0)

    def reject(url, timeout):
        exit_server(harness)()
        raise ConnectionError("handshake rejected")

    harness.playwright.chromium.connect_over_cdp = reject
    with pytest.raises(ConnectionError, match="handshake rejected"):
        harness.run()
    assert len(harness.processes) == 1


def test_server_that_exits_during_the_handshake_is_not_used(tmp_path):
    harness = Harness(tmp_path, held=0)
    connect = harness.connect

    def connect_then_exit(url, timeout):
        browser = connect(url, timeout)
        exit_server(harness)()
        return browser

    harness.playwright.chromium.connect_over_cdp = connect_then_exit
    with pytest.raises(RuntimeError, match="exited with code 0"):
        harness.run()
    assert harness.browsers[0].closed
    assert len(harness.processes) == 1


def test_silent_exit_before_any_handshake_reports_the_exit_code(tmp_path):
    harness = Harness(tmp_path, "#!{python}\nimport sys\nsys.exit(3)\n", held=0)
    with pytest.raises(RuntimeError, match="exited with code 3"):
        harness.run()
    assert len(harness.processes) == 1


def test_exit_while_the_browser_is_in_use_fails_the_call(tmp_path):
    harness = Harness(tmp_path, held=0)
    with pytest.raises(RuntimeError, match="exited with code 0 while"):
        harness.run(during=exit_server(harness))
    assert harness.browsers[0].closed


def test_caller_error_is_kept_when_the_server_also_exited(tmp_path):
    harness = Harness(tmp_path, held=0)

    def fail():
        exit_server(harness)()
        raise LookupError("caller failure")

    with pytest.raises(LookupError, match="caller failure"):
        harness.run(during=fail)
    assert harness.browsers[0].closed


def test_port_check_ignores_the_log_of_an_earlier_attempt(tmp_path):
    harness = Harness(tmp_path, BOUND_THEN_FAILS)
    with pytest.raises(RuntimeError) as failure:
        harness.run()
    assert str(failure.value) == "unsupported flag\n"
    assert len(harness.processes) == 2
    assert harness.browsers == []


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
