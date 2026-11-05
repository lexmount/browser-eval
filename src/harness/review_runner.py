"""Run independent reviewer command-line tools as bounded child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import threading


def _kill_child(process):
    process.kill()
    process.wait()


def _kill_session(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _communicate(process, payload, timeout, kill):
    """Wait for a reviewer; any timeout or interruption kills and reaps it before re-raising."""
    try:
        return process.communicate(payload, timeout=timeout)
    except BaseException:
        kill(process)
        raise


def run_reviewer(command, *, timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options):
    """Run one foreground reviewer that shares this terminal's interrupts.

    A timeout kills only the direct child and raises ``subprocess.TimeoutExpired``.
    """
    with subprocess.Popen(
        command, stdout=stdout, stderr=stderr, text=True, encoding="utf-8", **options
    ) as process:
        output, errors = _communicate(process, None, timeout, _kill_child)
        return subprocess.CompletedProcess(process.args, process.poll(), output, errors)


class ReviewerSessions:
    """Reviewers in their own sessions, killed as whole groups on timeout or cancellation.

    Starting and stopping share one lock, so cancellation cannot miss a child that
    is starting and a late worker cannot start another one.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._running = set()
        self._stopping = False

    @property
    def stopping(self):
        return self._stopping

    def start(self, command, **options):
        """Start one reviewer, or return ``None`` once cancellation has begun."""
        with self._lock:
            if self._stopping:
                return None
            process = subprocess.Popen(command, start_new_session=True, **options)
            self._running.add(process)
            return process

    def communicate(self, process, payload, *, timeout):
        """Send ``payload`` and wait, raising ``subprocess.TimeoutExpired`` after a group kill."""
        try:
            return _communicate(process, payload, timeout, _kill_session)
        finally:
            with self._lock:
                self._running.discard(process)

    def kill(self, process):
        """Kill and reap one reviewer's session if it is still running."""
        _kill_session(process)
        with self._lock:
            self._running.discard(process)

    def stop(self):
        """Refuse new reviewers and kill every running session."""
        with self._lock:
            self._stopping = True
            remaining = list(self._running)
        for process in remaining:
            _kill_session(process)
