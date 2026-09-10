from __future__ import annotations

import base64
import json
import os
import queue
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..models import EvalCase, ProviderResult
from .base import Provider
from .runtime_identity import validate_runtime_identity


class SubprocessJsonlProvider(Provider):
    """Invoke a persistent repository-native worker over line-delimited JSON."""

    _LOCKED_RUNTIMES = {
        "openclaw_builtin": {
            "revision": "12c4bdf7482f24c85b011e9d858fdec3c245101c",
            "paths": {
                "{repo_path}/src/agents/tools/web-fetch.ts",
                "{repo_path}/pnpm-lock.yaml",
                "{harness_root}/scripts/oss/openclaw_webfetch_worker.mts",
            },
        },
        "deepseek_harness_builtin": {
            "revision": "dd6322d604e00eec1ba5e0c8541159906a21094a",
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
        self._stderr_lines: list[str] = []

    def _resolve_token(self, value: Any) -> str:
        harness_root = Path(__file__).resolve().parents[3]
        repo_path = Path(str(self.config.get("repo_path", "."))).expanduser().resolve()
        return (str(value).replace("{harness_root}", str(harness_root))
                .replace("{repo_path}", str(repo_path)))

    def validate(self) -> list[str]:
        missing, identity = validate_runtime_identity(self.config, self._resolve_token)
        locked = self._LOCKED_RUNTIMES.get(self.type)
        if locked:
            if self.config.get("expected_repo_revision") != locked["revision"]:
                missing.append(f"repo revision {locked['revision']}")
            if self.config.get("expected_node_version") != "v24.15.0":
                missing.append("Node v24.15.0")
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
        self._stderr_lines = []
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
            self._stderr_lines.append(line.rstrip())
            del self._stderr_lines[:-200]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        deadline = time.monotonic() + timeout
        process = self._start()
        assert process.stdin is not None and process.stdout is not None
        request = {"url": case.url, "timeout_seconds": timeout, "task_kind": case.task_kind}
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        worker_logs: list[str] = []
        payload: dict[str, Any] | None = None
        while payload is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                self._process = None
                raise TimeoutError(f"worker timed out after {timeout:.1f}s")
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                code = process.poll()
                self._process = None
                raise RuntimeError(
                    f"worker exited with {code}: {' | '.join(self._stderr_lines[-20:])[-2000:]}\n"
                    f"command={shlex.join([self._resolve_token(part) for part in self.config['command']])}"
                )
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                worker_logs.append(line.rstrip())
                continue
            if not isinstance(candidate, dict) or "ok" not in candidate:
                worker_logs.append(line.rstrip())
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
            metadata={**dict(payload.get("metadata") or {}), "worker_logs": worker_logs[-20:]},
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
