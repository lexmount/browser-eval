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
    with subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True, **options) as process:
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

    def stop(self):
        """Refuse new reviewers and kill every running session."""
        with self._lock:
            self._stopping = True
            remaining = list(self._running)
        for process in remaining:
            _kill_session(process)


def validate_schema(value, schema):
    """Validate the deliberately small JSON schema used by both review backends."""
    kind = schema.get("type")
    valid = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
    }
    if kind in valid and not valid[kind]():
        raise ValueError("Review schema type mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Review schema enum mismatch")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError("Review schema fields mismatch")
        for name, child in schema["properties"].items():
            validate_schema(value[name], child)
    elif kind == "array":
        if len(value) > schema.get("maxItems", len(value)):
            raise ValueError("Review schema array exceeds maximum size")
        for child in value:
            validate_schema(child, schema["items"])
    elif kind == "string" and len(value.strip()) < schema.get("minLength", 1):
        raise ValueError("Review schema requires nonempty text")
    elif kind == "integer" and not schema.get("minimum", value) <= value <= schema.get(
        "maximum", value
    ):
        raise ValueError("Review schema integer range mismatch")
