from __future__ import annotations

import base64
import json
import hashlib
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..models import EvalCase, ProviderResult
from ..retrieval_hints import plan_for_case, native_default_case
from .base import Provider
from .runtime_identity import validate_runtime_identity


class SubprocessJsonlProvider(Provider):
    """Invoke a persistent repository-native worker over line-delimited JSON."""

    _LOCKED_RUNTIMES = {
        "openclaw_builtin": {
            "revision": "12c4bdf7482f24c85b011e9d858fdec3c245101c",
            "node_version": "v24.15.0",
            "package_manager_version": "12.1.0",
            "paths": {
                "{repo_path}/src/agents/tools/web-fetch.ts",
                "{repo_path}/pnpm-lock.yaml",
                "{harness_root}/scripts/oss/openclaw_webfetch_worker.mts",
            },
        },
        "deepseek_harness_builtin": {
            "revision": "dd6322d604e00eec1ba5e0c8541159906a21094a",
            "node_version": "v24.15.0",
            "paths": {
                "{repo_path}/packages/web/web-fetch-http/src/provider.ts",
                "{repo_path}/pnpm-lock.yaml",
                "{repo_path}/node_modules/.pnpm/.browser-harness-dsh-worker.mts",
                "{harness_root}/scripts/oss/dsh_webfetch_worker.mts",
            },
        },
    }

    def __init__(self, name: str, config: dict[str, Any]):
        super().__init__(name, config)
        self.type = str(config.get("method_type") or self.type)
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str | None] = queue.Queue()
        self._stderr_lock = threading.Condition()
        self._stderr_chunks: list[tuple[int, int, str]] = []
        self._stderr_sequence = 0
        self._stderr_total_bytes = 0
        self._stderr_retained_bytes = 0

    def _resolve_token(self, value: Any) -> str:
        harness_root = Path(__file__).resolve().parents[3]
        repo_path = Path(str(self.config.get("repo_path", "."))).expanduser().resolve()
        return (
            str(value)
            .replace("{harness_root}", str(harness_root))
            .replace("{repo_path}", str(repo_path))
        )

    def validate(self) -> list[str]:
        if hasattr(self, "_implementation_identity"):
            del self._implementation_identity
        missing, identity = validate_runtime_identity(self.config, self._resolve_token)
        locked = self._LOCKED_RUNTIMES.get(self.type)
        if locked:
            if self.config.get("expected_repo_revision") != locked["revision"]:
                missing.append(f"repo revision {locked['revision']}")
            if self.config.get("expected_node_version") != locked["node_version"]:
                missing.append(f"Node {locked['node_version']}")
            package_manager_version = locked.get("package_manager_version")
            if (
                package_manager_version is not None
                and self.config.get("expected_package_manager_version")
                != package_manager_version
            ):
                missing.append(f"package manager {package_manager_version}")
            if not locked["paths"].issubset(set(self.config.get("required_paths", []))):
                missing.append("locked required paths")
        if identity is not None:
            self._implementation_identity = identity
        return missing

    def _start(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        command = [self._resolve_token(part) for part in self.config["command"]]
        env = os.environ.copy()
        env.update({str(k): self._resolve_token(v) for k, v in self.config.get("env", {}).items()})
        cwd = str(Path(str(self.config.get("repo_path", "."))).expanduser().resolve())
        self._process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stdout_queue = queue.Queue()
        with self._stderr_lock:
            self._stderr_chunks = []
            self._stderr_sequence = 0
            self._stderr_total_bytes = 0
            self._stderr_retained_bytes = 0
        assert self._process.stdout is not None and self._process.stderr is not None
        threading.Thread(target=self._drain_stdout, args=(self._process,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._process,), daemon=True).start()
        return self._process

    def _drain_stdout(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self._stdout_queue.put(line)
        self._stdout_queue.put(None)

    def _drain_stderr(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            size = len(line.encode("utf-8", "replace"))
            limit = max(1, int(self.config.get("max_bytes", 20_000_000)))
            with self._stderr_lock:
                self._stderr_sequence += 1
                self._stderr_total_bytes += size
                self._stderr_chunks.append(
                    (self._stderr_sequence, self._stderr_total_bytes, line)
                )
                self._stderr_retained_bytes += size
                while self._stderr_chunks and self._stderr_retained_bytes > limit:
                    removed = self._stderr_chunks.pop(0)
                    self._stderr_retained_bytes -= len(removed[2].encode("utf-8", "replace"))
                self._stderr_lock.notify_all()

    def _stderr_cursor(self) -> tuple[int, int]:
        with self._stderr_lock:
            return self._stderr_sequence, self._stderr_total_bytes

    def _request_stderr(self, cursor: tuple[int, int]) -> dict[str, Any]:
        """Return only stderr observed after this request began, with honest bounds."""
        start_sequence, start_bytes = cursor
        # stdout and stderr are separate pipes. Once a response arrives, allow
        # its stderr drainer a short quiet period before closing the slice.
        quiet_deadline = time.monotonic() + 0.05
        with self._stderr_lock:
            previous = self._stderr_sequence
            while time.monotonic() < quiet_deadline:
                self._stderr_lock.wait(timeout=0.01)
                if self._stderr_sequence == previous:
                    break
                previous = self._stderr_sequence
            actual_bytes = self._stderr_total_bytes - start_bytes
            chunks = [line for sequence, _, line in self._stderr_chunks
                      if sequence > start_sequence]
        text = "".join(chunks)
        retained_bytes = len(text.encode("utf-8", "replace"))
        return {
            "stderr": text,
            "stderr_bytes": actual_bytes,
            "stderr_truncated": retained_bytes < actual_bytes,
        }

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        answer_begin = time.perf_counter()
        try:
            retrieval_plan = plan_for_case(case)
        except ValueError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=str(error),
            )
        deadline = time.monotonic() + timeout
        process = self._start()
        assert process.stdin is not None and process.stdout is not None
        stderr_cursor = self._stderr_cursor()
        request = {
            "url": case.url,
            "timeout_seconds": timeout,
            "max_bytes": int(self.config.get("max_bytes", 20_000_000)),
            "task_kind": case.task_kind,
            "track": case.track,
            "retrieval_plan": retrieval_plan.worker_dict() if retrieval_plan else None,
            "retrieval_mode": "native_default" if native_default_case(case) else "declared_plan",
        }
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        worker_logs: list[str] = []
        worker_log_bytes = 0
        worker_logs_truncated = False
        log_limit = max(1, int(self.config.get("max_bytes", 20_000_000)))
        payload: dict[str, Any] | None = None
        while payload is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                self._process = None
                answer_elapsed_ms = round((time.perf_counter() - answer_begin) * 1000, 2)
                error = TimeoutError(f"worker timed out after {timeout:.1f}s")
                error.public_metadata = {
                    "_answer_elapsed_ms": answer_elapsed_ms,
                    **self._request_stderr(stderr_cursor),
                    "stdout": "".join(worker_logs),
                    "stdout_bytes": worker_log_bytes,
                    "stdout_truncated": worker_logs_truncated,
                }
                raise error
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                code = process.poll()
                self._process = None
                answer_elapsed_ms = round((time.perf_counter() - answer_begin) * 1000, 2)
                error = RuntimeError(f"worker exited with {code}")
                error.public_metadata = {
                    "_answer_elapsed_ms": answer_elapsed_ms,
                    **self._request_stderr(stderr_cursor),
                    "exit_code": code,
                    "stdout": "".join(worker_logs),
                    "stdout_bytes": worker_log_bytes,
                    "stdout_truncated": worker_logs_truncated,
                }
                raise error
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                encoded = len(line.encode("utf-8", "replace"))
                worker_log_bytes += encoded
                if sum(len(item.encode("utf-8", "replace")) for item in worker_logs) + encoded <= log_limit:
                    worker_logs.append(line)
                else:
                    worker_logs_truncated = True
                continue
            if not isinstance(candidate, dict) or "ok" not in candidate:
                encoded = len(line.encode("utf-8", "replace"))
                worker_log_bytes += encoded
                if sum(len(item.encode("utf-8", "replace")) for item in worker_logs) + encoded <= log_limit:
                    worker_logs.append(line)
                else:
                    worker_logs_truncated = True
                continue
            payload = candidate
        ok = bool(payload.get("ok"))
        files = []
        for item in payload.get("files") or []:
            raw = base64.b64decode(item["base64"], validate=True)
            if len(raw) > int(self.config.get("max_bytes", 20000000)):
                raise ValueError("worker file exceeds max_bytes")
            files.append({"data": raw})
        return ProviderResult(
            ok=ok,
            files=files,
            content=str(payload.get("content") or ""),
            status_code=payload.get("status_code"),
            final_url=payload.get("final_url") or case.url,
            title=payload.get("title"),
            error_type=None if ok else str(payload.get("error_type") or "tool_error"),
            error_message=None if ok else str(payload.get("error_message") or "worker failed"),
            metadata={
                **dict(payload.get("metadata") or {}),
                "_raw_response_b64": base64.b64encode(line.encode("utf-8")).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                "raw_response_bytes": len(line.encode("utf-8")),
                "_answer_elapsed_ms": round((time.perf_counter() - answer_begin) * 1000, 2),
                **self._request_stderr(stderr_cursor),
                "stdout": "".join(worker_logs),
                "stdout_bytes": worker_log_bytes,
                "stdout_truncated": worker_logs_truncated,
                "worker_logs": [line.rstrip() for line in worker_logs],
            },
        )

    def cancel(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    @property
    def cancellation_cleanup_seconds(self) -> float:
        # SIGKILL is synchronous to send, but the fetch and pipe-drain threads
        # need a bounded moment to observe process exit and unwind.
        return 1.0

    def __del__(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
