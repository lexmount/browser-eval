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

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from ..retrieval_hints import plan_for_case, native_default_case
from ..native_projection import native_answer_projection
from .base import Provider
from .runtime_identity import validate_runtime_identity


class SubprocessJsonlProvider(Provider):
    """Invoke a persistent repository-native worker over line-delimited JSON."""
    def native_route(self, case):
        if self.type not in {'subprocess_jsonl', 'cowagent_builtin'}:
            return super().native_route(case)
        return 'jsonl', 'native_worker_command', [
            'content', 'metadata.links', 'metadata.images', 'metadata.videos',
            'metadata.media', 'metadata.extras', 'files', 'title', 'final_url']

    _RUNTIME_CONTRACTS = {
        "cowagent_builtin": {
            "required_fields": ("expected_repo_revision",),
            "required_paths": {
                "{repo_path}/agent/tools/web_fetch/web_fetch.py",
                "{repo_path}/agent/tools/utils/url_safety.py",
                "{harness_root}/harness/web_fetch/workers/cowagent_worker.py",
            },
        },
        "openclaw_builtin": {
            "required_fields": (
                "expected_repo_revision", "expected_node_version",
                "expected_package_manager_version",
            ),
            "required_paths": {
                "{repo_path}/src/agents/tools/web-fetch.ts",
                "{repo_path}/pnpm-lock.yaml",
                "{harness_root}/harness/web_fetch/workers/openclaw_webfetch_worker.mts",
            },
        },
        "deepseek_harness_builtin": {
            "required_fields": ("expected_repo_revision", "expected_node_version"),
            "identical_files": [(
                "harness/harness/web_fetch/workers/dsh_webfetch_worker.mts",
                "repo/node_modules/.pnpm/.browser-harness-dsh-worker.mts",
            )],
            "required_paths": {
                "{repo_path}/packages/web/web-fetch-http/src/provider.ts",
                "{repo_path}/pnpm-lock.yaml",
                "{repo_path}/node_modules/.pnpm/.browser-harness-dsh-worker.mts",
                "{harness_root}/harness/web_fetch/workers/dsh_output.mjs",
                "{harness_root}/harness/web_fetch/workers/dsh_webfetch_worker.mts",
            },
        },
    }

    def __init__(self, name: str, config: dict[str, Any]):
        super().__init__(name, config)
        self.type = str(config.get("method_type") or self.type)
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str | None] = queue.Queue()
        self._stderr_lock = threading.Condition()
        self._stderr_generation = object()
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
        from ..network_dns import validate_dns_mode
        try:
            mode = validate_dns_mode(self.config.get("dns_mode", "system"))
        except ValueError as error:
            return [str(error)]
        if mode == "public_doh" and self.type not in {"cowagent_builtin", "openclaw_builtin", "deepseek_harness_builtin", "langchain_webbase", "crawl4ai"}:
            return ["public_doh is not supported by this worker"]
        missing, identity = validate_runtime_identity(
            self.config, self._resolve_token, **self._RUNTIME_CONTRACTS.get(self.type, {}))
        if mode == "public_doh":
            required = {"{harness_root}/harness/web_fetch/network_dns.py"} if self.type in {"cowagent_builtin", "langchain_webbase", "crawl4ai"} else {"{harness_root}/harness/web_fetch/workers/public_dns.mjs"}
            if self.type == "deepseek_harness_builtin":
                required.add("{repo_path}/packages/web/web-fetch-http/src/network.ts")
            if not required.issubset(set(self.config.get("required_paths", []))):
                missing.append("locked public DNS helper paths")
        if identity is not None and not missing:
            self._implementation_identity = identity
        return missing

    def _start(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        command = [self._resolve_token(part) for part in self.config["command"]]
        env = os.environ.copy()
        env.update({str(k): self._resolve_token(v) for k, v in self.config.get("env", {}).items()})
        if self.config.get("log_level") == "debug": env["BROWSER_EVAL_LOG_LEVEL"] = "debug"
        env["BROWSER_EVAL_HARNESS_ROOT"] = str(Path(__file__).resolve().parents[3])
        cwd = str(Path(str(self.config.get("repo_path", "."))).expanduser().resolve())
        sink = getattr(self, "_active_diagnostics", None)
        if sink: sink.emit("caller", "worker_start", command=command, cwd=cwd)
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
            self._stderr_generation = object()
            self._stderr_chunks = []
            self._stderr_sequence = 0
            self._stderr_total_bytes = 0
            self._stderr_retained_bytes = 0
        assert self._process.stdout is not None and self._process.stderr is not None
        threading.Thread(target=self._drain_stdout,
                         args=(self._process, self._stdout_queue), daemon=True).start()
        threading.Thread(target=self._drain_stderr,
                         args=(self._process, self._stderr_generation), daemon=True).start()
        return self._process

    def _drain_stdout(self, process: subprocess.Popen[str], output: queue.Queue[str | None]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            sink = getattr(self, "_active_diagnostics", None)
            if sink and process is self._process:
                sink.emit("native_process_stdout", "stream_line", text=line)
            output.put(line)
        output.put(None)

    def _drain_stderr(self, process: subprocess.Popen[str], generation: object) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            size = len(line.encode("utf-8", "replace"))
            limit = max(1, int(self.config.get("max_bytes", 20_000_000)))
            with self._stderr_lock:
                if generation is not self._stderr_generation:
                    return
                sink = getattr(self, "_active_diagnostics", None)
                if sink:
                    sink.emit("native_process_stderr", "stream_line", text=line)
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

    def native_worker_request(self, case, timeout, diagnostic_path=None):
        from ..native_routes import declared_input_media_kind
        media = declared_input_media_kind(case, required=False)
        return {'url': case.url, 'log_level': self.config.get('log_level'),
                'diagnostic_path': diagnostic_path, 'timeout_seconds': timeout,
                'uniform_native_parameters': True,
                'max_bytes': int(self.config.get('max_bytes', 20_000_000)),
                'dns_mode': self.config.get('dns_mode', 'system'),
                'task_kind': case.task_kind, 'track': case.track,
                **({'input_media_kind': media} if media is not None else {}), 'retrieval_plan': None,
                'retrieval_mode': 'native_default' if case.track == 'dynamic' else 'declared_plan'}

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.check_native_call(case)
        from ..native_routes import declared_input_media_kind
        input_media = declared_input_media_kind(case, required=bool(
            self.config.get("uniform_native_parameters") and case.track == "document"))
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
        self._active_diagnostics = getattr(self, "_call_diagnostics", None)
        previous_process = self._process
        stderr_cursor = self._stderr_cursor()
        process = self._start()
        if process is not previous_process:
            # Startup is part of this call, including diagnostics already
            # consumed by the reader before _start returns.
            stderr_cursor = (0, 0)
        assert process.stdin is not None and process.stdout is not None
        request = {
            "url": case.url,
            "log_level": self.config.get("log_level"),
            "diagnostic_path": str(self._active_diagnostics.path) if self._active_diagnostics else None,
            "timeout_seconds": timeout,
            "uniform_native_parameters": bool(self.config.get("uniform_native_parameters")),
            "max_bytes": int(self.config.get("max_bytes", 20_000_000)),
            "dns_mode": self.config.get("dns_mode", "system"),
            "task_kind": case.task_kind,
            "track": case.track,
            **({"input_media_kind": input_media} if input_media is not None else {}),
            "retrieval_plan": retrieval_plan.worker_dict() if retrieval_plan else None,
            "retrieval_mode": "native_default" if native_default_case(case) else "declared_plan",
        }
        if self.config.get('uniform_native_parameters'):
            request = self.native_worker_request(case, timeout, request['diagnostic_path'])
        if getattr(self, '_native_route_ledger', None) is not None:
            from harness.record_io import canonical_json_sha256
            projected = {**request, 'url': '<input-url>',
                         'diagnostic_path': '<diagnostic-output>' if request['diagnostic_path'] else None}
            self.capture_native_request(case, transport='jsonl', interface='native_worker_command',
                parameters={'request': projected,
                            'command_sha256': canonical_json_sha256([self._resolve_token(part) for part in self.config['command']])})
        if self._active_diagnostics: self._active_diagnostics.emit("caller", "worker_request_sent", request=request)
        try:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except OSError as error:
            # A worker may exit or close stdin before accepting a request.
            # Retain the actual transport error and the startup diagnostics.
            self.cancel()
            process.wait(timeout=self.cancellation_cleanup_seconds)
            error.public_metadata = {
                "_answer_elapsed_ms": round((time.perf_counter() - answer_begin) * 1000, 2),
                **self._request_stderr(stderr_cursor),
                "exit_code": process.returncode,
            }
            raise

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
        if payload.get("content") is not None and not isinstance(payload["content"], str):
            raise ValueError("worker content must be text or absent")
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
                **({'native_answer_projection': native_answer_projection('subprocess_jsonl', case, payload)}
                   ),
                "_raw_response_b64": base64.b64encode(line.encode("utf-8")).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
                "raw_response_bytes": len(line.encode("utf-8")),
                "request_summary": {
                    **dict((payload.get("metadata") or {}).get("request_summary") or {}),
                    "caller_timeout_seconds": request["timeout_seconds"],
                    "caller_max_bytes": request["max_bytes"],
                    "caller_dns_mode": request["dns_mode"],
                },
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
                    try:
                        stream.close()
                    except OSError:
                        # Closing buffered stdin can repeat the write failure.
                        pass
