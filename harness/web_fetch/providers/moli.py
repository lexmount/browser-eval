"""Official one-shot Moli CLI, with stdout kept separate from diagnostics."""

from __future__ import annotations

import math
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import threading

from ..models import EvalCase, ProviderResult
from ..safety import validate_http_url
from .base import Provider


class MoliWebFetchProvider(Provider):
    def __init__(self, name, config):
        super().__init__(name, config)
        self._process_lock = threading.Lock()
        self._cancel_requested = False
        self._active_process = None

    def begin_call(self):
        with self._process_lock:
            self._cancel_requested = False
            self._active_process = None

    def _resolve_binary(self) -> str | None:
        return shutil.which(os.path.expanduser(str(self.config.get("binary") or "moli")))

    def _configuration_errors(self, binary: str | None) -> list[str]:
        errors = []
        if not binary:
            errors.append("binary (set MOLI_BINARY or install moli on PATH)")
        if self.config.get("wait_until", "done") not in {
            "done",
            "load",
            "domcontentloaded",
            "networkidle",
            "domstable",
        }:
            errors.append("wait_until")
        if int(self.config.get("max_bytes", 20_000_000)) <= 0:
            errors.append("max_bytes")
        return errors

    def validate(self) -> list[str]:
        binary = self._resolve_binary()
        errors = self._configuration_errors(binary)
        if not errors:
            try:
                with open(binary, 'rb') as source:
                    self._binary_sha256 = hashlib.file_digest(source, 'sha256').hexdigest()
                stat = Path(binary).stat()
                self._binary_identity = (binary, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            except OSError:
                errors.append('binary (unreadable)')
        return errors

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        binary = self._resolve_binary()
        errors = self._configuration_errors(binary)
        if errors:
            return ProviderResult(
                False, "", error_type="configuration", error_message="; ".join(errors)
            )
        wait = self.config.get("wait_until", "done")
        file_task = case.task_kind == "file"
        if file_task:
            # Direct file delivery uses Moli's automatic response mode. Forcing
            # --dump html/markdown can reject or serialize a non-HTML response
            # before the evaluator receives the original bytes.
            dump = None
            command = [
                binary,
                "fetch",
                "--timeout",
                str(max(1, int(timeout * 1000))),
            ]
        else:
            dump = "markdown"
            command = [
                binary,
                "fetch",
                "--dump",
                dump,
                "--wait-until",
                wait,
                "--timeout",
                str(max(1, int(timeout * 1000))),
            ]
        if self.config.get("block_private_networks", True):
            command.append("--block-private-networks")
        command.append(case.url)
        limit = int(self.config.get("max_bytes", 20_000_000))
        metadata = {
            "dump": dump,
            "wait_until": None if file_task else wait,
            "binary": Path(binary).name,
            "block_private_networks": bool(self.config.get("block_private_networks", True)),
        }
        try:
            if hasattr(self, '_binary_identity'):
                stat = Path(binary).stat()
                if (binary, stat.st_ino, stat.st_size, stat.st_mtime_ns) != self._binary_identity:
                    return ProviderResult(False, '', error_type='configuration', error_message='Moli binary changed; validate runtime before measurement')
            metadata['binary_sha256'] = getattr(self, '_binary_sha256', None)
        except OSError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=f"Cannot read Moli binary: {error}",
                metadata=metadata,
            )
        # Spool artifacts to files rather than retaining arbitrary CLI output in RAM.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostic:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=diagnostic,
                    start_new_session=os.name == "posix",
                )
                with self._process_lock:
                    self._active_process = process
                    if self._cancel_requested:
                        self._kill(process)
            except OSError as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message=f"Cannot start Moli: {error}",
                    metadata=metadata,
                )
            with process:
                stopped = None
                deadline = time.monotonic() + timeout
                while True:
                    if (
                        os.fstat(output.fileno()).st_size > limit
                        or os.fstat(diagnostic.fileno()).st_size > limit
                    ):
                        stopped = "output_too_large"
                        break
                    if process.poll() is not None:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        stopped = "timeout"
                        break
                    try:
                        process.wait(timeout=min(0.05, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                if stopped:
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()
                output.seek(0)
                raw = output.read(limit + 1)
                diagnostic.seek(0)
                stderr = diagnostic.read(4096).decode("utf-8", "replace")
                metadata.update(exit_code=process.returncode, stderr=stderr)
        if stopped == "timeout":
            return ProviderResult(
                False,
                "",
                error_type="timeout",
                error_message="Moli fetch exceeded time budget",
                metadata=metadata,
            )
        if stopped == "output_too_large" or len(raw) > limit:
            return ProviderResult(
                False,
                "",
                error_type="output_too_large",
                error_message="Moli stdout exceeds max_bytes",
                metadata=metadata,
            )
        if case.task_kind == "file":
            ok = process.returncode == 0 and bool(raw)
            return ProviderResult(ok, "", files=[{"data": raw}] if raw else [],
                error_type=None if ok else "tool_error",
                error_message=None if ok else stderr or "Moli returned no file bytes",
                metadata=metadata)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ProviderResult(
                False,
                "",
                error_type="invalid_output",
                error_message="Moli markdown output is not UTF-8",
                metadata=metadata,
            )
        ok = process.returncode == 0 and bool(content.strip())
        # Markdown mode does not expose HTTP status/final URL. Never invent them,
        # or make a second request whose metadata might belong to different content.
        return ProviderResult(
            ok,
            content,
            error_type=None if ok else "empty_output" if process.returncode == 0 else "tool_error",
            error_message=None if ok else stderr or "Moli returned no usable output",
            metadata=metadata,
        )

    def cancel(self) -> None:
        # Remember cancellation during Popen without blocking the deadline
        # thread on process creation. The launching thread kills a late child.
        with self._process_lock:
            self._cancel_requested = True
            self._kill(self._active_process)

    @staticmethod
    def _kill(process):
        if process is not None and process.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
