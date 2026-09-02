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

from ..models import EvalCase, ProviderResult
from ..safety import validate_http_url
from .base import Provider


class MoliWebFetchProvider(Provider):
    def validate(self) -> list[str]:
        errors = []
        binary = str(self.config.get("binary") or "moli")
        if not shutil.which(os.path.expanduser(binary)):
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

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        errors = self.validate()
        if errors:
            return ProviderResult(
                False, "", error_type="configuration", error_message="; ".join(errors)
            )
        binary = shutil.which(os.path.expanduser(str(self.config.get("binary") or "moli")))
        wait = self.config.get("wait_until", "done")
        command = [
            binary,
            "fetch",
            "--dump",
            "markdown",
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
            "dump": "markdown",
            "wait_until": wait,
            "binary": Path(binary).name,
            "block_private_networks": bool(self.config.get("block_private_networks", True)),
        }
        if not hasattr(self, "_binary_sha256"):
            with open(binary, "rb") as source:
                self._binary_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        metadata["binary_sha256"] = self._binary_sha256
        # Spool artifacts to files rather than retaining arbitrary CLI output in RAM.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostic:
            with subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=diagnostic,
                start_new_session=os.name == "posix",
            ) as process:
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
