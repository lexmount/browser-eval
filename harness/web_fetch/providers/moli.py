"""Official one-shot Moli CLI, with stdout kept separate from diagnostics."""

from __future__ import annotations

import base64
from contextlib import ExitStack
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from ..models import EvalCase, ProviderResult, TRACKS
from ..retrieval_hints import native_contract_for_case, plan_for_case
from ..safety import validate_http_url
from .base import Provider, terminate_owned_process_tree
from .http_client import inner_timeout_budget

_WAIT_UNTIL_VALUES = {
    "done",
    "load",
    "domcontentloaded",
    "networkidle",
    "domstable",
}
_PUBLIC_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_MARKDOWN_HTTP_ERROR_PAGE = re.compile(
    r"^\s*(?:\*\*)?error\s*:\s*(?:\*\*)?\s*forbidden\s*"
    r"(?:\r?\n)+\s*(?:\*\*)?(?P<status>4\d\d|5\d\d)(?:\*\*)?\s*:\s*forbidden"
    r"(?:\s*id\s*:.*)?\s*$",
    re.IGNORECASE,
)

_MOLI_FETCH_FAILURE = re.compile(
    r"(?:^|\n)Error: failed to fetch `[^`\r\n]+`\r?\n"
    r"Reason: (?P<reason>[^\r\n]+)\s*\Z"
)
_MOLI_READINESS_TIMEOUT = re.compile(
    r"fetch readiness timed out after [1-9]\d* ms while waiting for [^\r\n]+"
)
_MOLI_DNS_FAILURE = re.compile(
    r"failed to resolve [^\s:]+:\d+: failed to lookup address information: .+"
)
_MOLI_CURL_FAILURE = re.compile(
    r"curl request failed for \S+ after active=\d+ms queued=\d+ms: "
    r"\[(?P<code>\d+)\] (?P<detail>.+)"
)
_CURL_DNS_FAILURES = {5, 6}
_CURL_TIMEOUT_FAILURES = {28}
_CURL_TRANSIENT_TRANSFER_FAILURES = {7, 16, 18, 35, 52, 55, 56, 92, 95}
_CURL_CERTIFICATE_FAILURES = {51, 58, 60, 77, 82, 83, 90, 91}
_CERTIFICATE_FAILURE_DETAIL = re.compile(
    r"certificate|cert(?:ificate)? verify|issuer|CA cert|SSH remote key|CRL|OCSP",
    re.IGNORECASE,
)


def _markdown_http_error_status(content: str) -> int | None:
    """Recognize Moli's whole-response HTTP error page, not status prose."""
    if len(content) > 1024:
        return None
    match = _MARKDOWN_HTTP_ERROR_PAGE.fullmatch(content)
    return int(match.group("status")) if match else None


def _moli_native_error_type(stderr: str | bytes) -> str:
    """Classify only explicit, retry-safe Moli failure envelopes.

    The shared runner owns the single declared retry.  This adapter exposes a
    transient type only when Moli's final error envelope identifies a bounded
    readiness timeout, DNS failure, or known curl transport failure.  All
    ambiguous, content, certificate, and configuration failures stay terminal.
    """
    diagnostic = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    failure = _MOLI_FETCH_FAILURE.search(diagnostic)
    if failure is None:
        return "tool_error"
    reason = failure.group("reason")
    if _MOLI_READINESS_TIMEOUT.fullmatch(reason):
        return "timeout"
    if _MOLI_DNS_FAILURE.fullmatch(reason):
        return "dns"
    curl = _MOLI_CURL_FAILURE.fullmatch(reason)
    if curl is None:
        return "tool_error"
    code = int(curl.group("code"))
    detail = curl.group("detail")
    if code in _CURL_CERTIFICATE_FAILURES or _CERTIFICATE_FAILURE_DETAIL.search(detail):
        return "tool_error"
    if code in _CURL_DNS_FAILURES:
        return "dns"
    if code in _CURL_TIMEOUT_FAILURES:
        return "timeout"
    if code in _CURL_TRANSIENT_TRANSFER_FAILURES:
        return "network"
    return "tool_error"


def _diagnostic_main_document_url(stderr: str | bytes, requested_url: str) -> str | None:
    """Read Moli's effective main-document URL from the same call's debug log."""
    pattern = re.compile(
        rb"fetch request complete method=(?:GET|POST) url=(?P<url>\S+) "
        rb"final_url=(?P<final_url>\S+) status=(?P<status>\d{3})\b"
    )

    diagnostic = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
    navigation_pattern = re.compile(
        rb"following pending location navigation asynchronously[^\n]* url=(?P<url>\S+)"
    )

    def field(row: re.Match[bytes], name: str) -> str:
        return row.group(name).decode("utf-8", "replace")

    def identity(value: str) -> tuple | None:
        try:
            validate_http_url(value)
            parts = urlsplit(value)
            return (
                parts.scheme.casefold(),
                (parts.hostname or "").casefold(),
                parts.port if parts.port is not None else (443 if parts.scheme.casefold() == "https" else 80),
                re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(),
                       quote(parts.path, safe="/%:@!$&'()*+,;=")).rstrip("/") or "/",
                sorted(parse_qsl(parts.query, keep_blank_values=True)),
            )
        except ValueError:
            return None

    requested_identity = identity(requested_url)
    if requested_identity is None:
        return None
    rows = list(pattern.finditer(diagnostic))

    # Legacy completion records do not identify redirect hops or request owners.
    # Never infer the document from GET ordering or challenge query parameters.
    row = next((candidate for candidate in rows
                if identity(field(candidate, "url")) == requested_identity), None)
    if row is None or 300 <= int(field(row, "status")) < 400:
        return None
    navigations = list(navigation_pattern.finditer(diagnostic))
    for index, navigation in enumerate(navigations):
        boundary = navigations[index + 1].start() if index + 1 < len(navigations) else len(diagnostic)
        commits = [diagnostic.find(marker, navigation.end(), boundary) for marker in (
            b"committed main navigation before replacement realm bootstrap",
            b"detached committed main WindowProxy for replacement context",
        )]
        committed = next((position for position in commits if position >= 0), -1)
        if committed < 0:
            continue
        navigation_identity = identity(field(navigation, "url"))
        if navigation_identity is None:
            return None
        matches = [candidate for candidate in rows
                   if navigation.end() <= candidate.start() < boundary
                   and identity(field(candidate, "url")) == navigation_identity]
        if len(matches) != 1 or 300 <= int(field(matches[0], "status")) < 400:
            return None
        row = matches[0]
    candidate = field(row, "final_url")
    if identity(candidate) is None:
        return None
    try:
        validate_http_url(candidate)
    except ValueError:
        return None
    return candidate




class MoliWebFetchProvider(Provider):
    def __init__(self, name, config):
        super().__init__(name, config)
        self._process_lock = threading.Lock()
        self._cancel_requested = False
        self._active_process = None
        self._active_native_provider = None

    def begin_call(self):
        with self._process_lock:
            self._cancel_requested = False
            self._active_process = None

    def _resolve_binary(self) -> str | None:
        configured = os.path.expandvars(
            os.path.expanduser(str(self.config.get("binary") or "moli"))
        )
        return shutil.which(configured)

    def _configuration_errors(self, binary: str | None) -> list[str]:
        errors = []
        from ..network_dns import validate_dns_mode, validate_native_dns_library
        try:
            if validate_dns_mode(self.config.get("dns_mode", "system")) == "public_doh":
                validate_native_dns_library(self.config.get("native_dns_library"))
        except ValueError as error:
            errors.append(str(error))
        if not binary:
            errors.append("binary (set MOLI_BINARY or install moli on PATH)")
        expected_version = self.config.get("expected_reported_version")
        if not isinstance(expected_version, str) or not expected_version.strip():
            errors.append("expected_reported_version")
        expected_sha256 = self.config.get("expected_binary_sha256")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            errors.append("expected_binary_sha256")
        if self.config.get("wait_until", "done") not in _WAIT_UNTIL_VALUES:
            errors.append("wait_until")
        if self.config.get("dynamic_wait_until", "domstable") not in _WAIT_UNTIL_VALUES:
            errors.append("dynamic_wait_until")
        if self.config.get("wait_option", "wait-until") not in {"wait", "wait-until"}:
            errors.append("wait_option")
        track_waits = self.config.get("wait_until_by_track", {})
        if (not isinstance(track_waits, dict)
                or any(not isinstance(track, str) or track not in TRACKS
                       or not isinstance(wait, str) or wait not in _WAIT_UNTIL_VALUES
                       for track, wait in track_waits.items())):
            errors.append("wait_until_by_track")
        if int(self.config.get("max_bytes", 20_000_000)) <= 0:
            errors.append("max_bytes")
        retain_diagnostic = self.config.get("retain_complete_diagnostic_log", False)
        if not isinstance(retain_diagnostic, bool):
            errors.append("retain_complete_diagnostic_log")
        if retain_diagnostic and int(self.config.get("max_diagnostic_bytes", 100_000_000)) <= 0:
            errors.append("max_diagnostic_bytes")
        if not retain_diagnostic and "max_diagnostic_bytes" in self.config:
            errors.append("max_diagnostic_bytes (requires retain_complete_diagnostic_log)")
        if self.config.get("log_level") not in {
            None, "debug", "info", "warn", "error", "fatal"
        }:
            errors.append("log_level")
        if self.config.get("trace_network") not in {None, False}:
            errors.append("trace_network (requires JSON output and is not allowed for Markdown scoring)")
        if not isinstance(
            self.config.get("local_report_complete_diagnostic_links", False), bool
        ):
            errors.append("local_report_complete_diagnostic_links")
        elif self.config.get("local_report_complete_diagnostic_links") and not retain_diagnostic:
            errors.append(
                "local_report_complete_diagnostic_links (requires retain_complete_diagnostic_log)"
            )
        allowlist = self.config.get("private_network_allowlist_hosts", [])
        if self.config.get("uniform_native_parameters") and allowlist:
            errors.append("private_network_allowlist_hosts cannot vary uniform native parameters")
        if (
            not isinstance(allowlist, list)
            or any(
                not isinstance(host, str)
                or host != host.casefold()
                or not _PUBLIC_HOSTNAME.fullmatch(host)
                for host in allowlist
            )
            or len(set(allowlist)) != len(allowlist)
        ):
            errors.append("private_network_allowlist_hosts")
        return errors

    def validate(self) -> list[str]:
        binary = self._resolve_binary()
        return self._validate_resolved_binary(binary)

    def _validate_resolved_binary(self, binary: str | None) -> list[str]:
        for attribute in (
            "_binary_sha256",
            "_reported_version",
            "_binary_identity",
            "_implementation_identity",
        ):
            if hasattr(self, attribute):
                delattr(self, attribute)
        errors = self._configuration_errors(binary)
        if not errors:
            assert binary is not None
            try:
                with open(binary, "rb") as source:
                    binary_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
                stat = Path(binary).stat()
                reported_version = subprocess.check_output(
                    [binary, "--version"],
                    text=True,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                ).strip()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                errors.append("Moli version probe")
            except OSError:
                errors.append("binary (unreadable)")
            else:
                expected_version = self.config["expected_reported_version"].strip()
                expected_sha256 = self.config["expected_binary_sha256"]
                if reported_version != expected_version:
                    errors.append(f"Moli reported version {expected_version}")
                if binary_sha256 != expected_sha256:
                    errors.append(f"Moli binary SHA-256 {expected_sha256}")
                if not errors:
                    self._binary_sha256 = binary_sha256
                    self._reported_version = reported_version
                    self._binary_identity = (
                        binary,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                    self._implementation_identity = {
                        "reported_version": reported_version,
                        "binary_sha256": binary_sha256,
                    }
                    if self.config.get("dns_mode", "system") == "public_doh":
                        from ..network_dns import validate_native_dns_library
                        _, binding = validate_native_dns_library(self.config.get("native_dns_library"))
                        self._implementation_identity["native_dns"] = binding
        return errors

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.check_native_call(case)
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        try:
            # Validate the dynamic contract before selecting any content route.
            # A future dynamic file/document case must not bypass fail-closed
            # retrieval planning merely because it takes the raw-byte path.
            if (
                case.track == "dynamic"
                and isinstance(case.retrieval_hints, dict)
                and case.retrieval_hints.get("version") == 3
                and case.retrieval_hints != {"version": 3, "mode": "native"}
            ):
                raise ValueError(
                    f"{case.id}: Moli native dynamic tasks require one family marker"
                )
            retrieval_plan = plan_for_case(case)
            native_contract = native_contract_for_case(case)
        except ValueError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=str(error),
            )
        completion_plan = retrieval_plan or (
            native_contract.completion_plan(max(1, math.ceil(timeout * 1000)))
            if native_contract else None
        )
        if completion_plan and case.task_kind == "file":
            return ProviderResult(
                False, "", error_type="configuration",
                error_message="DOM completion cannot be applied to raw file delivery",
            )
        call_deadline = time.monotonic() + timeout
        binary = self._resolve_binary()
        errors = self._configuration_errors(binary)
        if not errors and not hasattr(self, "_implementation_identity"):
            errors = self._validate_resolved_binary(binary)
        if errors:
            return ProviderResult(
                False, "", error_type="configuration", error_message="; ".join(errors)
            )
        from .headless import HeadlessProvider
        # Moli has one content interface for every webpage and document-body
        # task.  Only a direct-media-file deliverable takes the raw-byte path.
        # This avoids changing the participant's output format within a report
        # family based on the URL suffix or the expected answer.
        if case.task_kind == 'file':
            native = HeadlessProvider(self.name, {**self.config, 'type':'headless_markdown',
                                                  'engine':'moli', 'binary':binary,
                                                  'max_original_file_bytes':int(self.config.get('max_bytes',20_000_000))})
            with self._process_lock:
                self._active_native_provider = native
                if self._cancel_requested: native.cancel()
            try:
                result = native.fetch(case, max(.001, call_deadline-time.monotonic()))
                result.metadata.update(route='raw_file',
                    reported_version=self._reported_version, binary_sha256=self._binary_sha256)
                return result
            finally:
                with self._process_lock: self._active_native_provider = None
        wait = self.config.get("wait_until", "done")
        family_wait = (
            self.config.get("dynamic_wait_until", "domstable")
            if case.track == "dynamic"
            else wait
        )
        track_waits = self.config.get("wait_until_by_track", {})
        family_wait = track_waits.get(case.track, family_wait)
        native_dynamic_task = (
            case.track == "dynamic"
            and case.retrieval_hints == {"version": 3, "mode": "native"}
        )
        wait_option = "--" + self.config.get("wait_option", "wait-until")
        timeout_ms = str(max(1, int(inner_timeout_budget(timeout) * 1000)))
        dump = "markdown"
        try:
            if case.task_kind == "resource":
                resource_wait = family_wait
                wait_arguments = [wait_option, resource_wait]
                wait_metadata = {
                    "wait_until": resource_wait,
                    "resource_discovery": "native_markdown_common_url_scoring",
                }
            elif completion_plan is not None:
                plan = completion_plan
                wait_arguments = [
                    "--wait-selector" if plan.wait_kind == "selector" else "--wait-script",
                    plan.wait_value,
                    wait_option,
                    plan.wait_until or family_wait,
                ]
                wait_metadata = {
                    "strategy": (
                        "frame_selector_count_script"
                        if plan.frame_selector
                        else "selector"
                        if plan.wait_kind == "selector"
                        else "selector_count_script"
                    ),
                    "selector": plan.selector,
                    "minimum_count": plan.minimum_count,
                    "frame_selector": plan.frame_selector,
                    "wait_until": plan.wait_until or family_wait,
                    "completion_support": "exact",
                    "output_extraction": "native_markdown",
                }
                if plan.minimum_text_fragments:
                    wait_metadata["minimum_text_fragments"] = plan.minimum_text_fragments
            else:
                wait_arguments = [wait_option, family_wait]
                wait_metadata = {"wait_until": family_wait}
                if case.track == "dynamic":
                    wait_metadata.update(
                        scope="dynamic_task_family",
                        strategy="native_task_family_completion",
                    )
            if completion_plan is None and case.track in track_waits:
                wait_metadata.update(
                    scope=f"{case.track}_task_family",
                    strategy="native_task_family_completion",
                )
        except ValueError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=str(error),
            )
        command = [binary, "fetch"]
        if dump is not None:
            command.extend(["--dump", dump])
        layout = native_dynamic_task
        if layout:
            command.append("--layout")
            wait_metadata["layout"] = {
                "requested": True,
                "support": "native",
                "interface": "--layout",
                "scope": "dynamic_task_family",
            }
        include_subdocuments = (
            native_dynamic_task
            or case.task_kind == "resource"
            or native_contract is not None and native_contract.include_subdocuments
        )
        if include_subdocuments:
            command.append("--with-frames")
            wait_metadata["subdocuments"] = {
                "requested": True,
                "support": "native",
                "interface": "--with-frames",
                "harness_content_assembly": False,
                "scope": (
                    "dynamic_task_family"
                    if native_dynamic_task
                    else "resource_task_family"
                    if case.task_kind == "resource"
                    else "declared_native_contract"
                ),
            }
        command.extend(
            [
                *wait_arguments,
                "--timeout",
                timeout_ms,
                "--http-timeout",
                timeout_ms,
            ]
        )
        # The retained answer limit must not restrict page or subresource downloads.
        command.extend(["--http-connect-timeout", timeout_ms])
        if self.config.get("uniform_native_parameters"):
            for flag in ("--timeout", "--http-timeout", "--http-connect-timeout"):
                if flag in command:
                    index = command.index(flag)
                    del command[index:index + 2]
        block_private_networks = bool(self.config.get("block_private_networks", True))
        requested_host = (urlsplit(case.url).hostname or "").casefold()
        private_network_guard_exception = block_private_networks and requested_host in set(
            self.config.get("private_network_allowlist_hosts", [])
        )
        if block_private_networks and not private_network_guard_exception:
            command.append("--block-private-networks")
        log_level = self.config.get("log_level")
        if log_level:
            command.extend(["--log-level", str(log_level)])
        command.append(case.url)
        limit = int(self.config.get("max_bytes", 20_000_000))
        retain_diagnostic = bool(self.config.get("retain_complete_diagnostic_log", False))
        diagnostic_limit = int(self.config.get("max_diagnostic_bytes", 100_000_000))
        diagnostic_read_limit = diagnostic_limit if retain_diagnostic else limit
        metadata = {
            "dump": dump,
            "layout": layout,
            "with_frames": include_subdocuments,
            "http_connect_timeout_ms": None if self.config.get("uniform_native_parameters") else int(timeout_ms),
            "internal_timeout_policy": "native_default" if self.config.get("uniform_native_parameters") else "explicit",
            "http_max_response_bytes": None,
            "retained_output_max_bytes": limit,
            "route": (
                "resource_native_markdown"
                if case.task_kind == "resource"
                else "dynamic_native_markdown"
                if case.track == "dynamic"
                else "ordinary_markdown"
            ),
            "output": (
                "native_markdown_resource_urls"
                if case.task_kind == "resource"
                else "markdown"
            ),
            "wait": wait_metadata,
            "wait_until": wait_metadata.get("wait_until"),
            "wait_interface": wait_option,
            "binary": Path(binary).name,
            "block_private_networks": block_private_networks,
            "private_network_guard_exception": private_network_guard_exception,
            "private_network_guard_exception_host": (
                requested_host if private_network_guard_exception else None
            ),
            "log_level": log_level,
            "network_trace_requested": False,
            "local_report_complete_diagnostic_links": bool(
                self.config.get("local_report_complete_diagnostic_links", False)
            ),
        }
        if case.task_kind == "resource":
            metadata["output_contract"] = "native_markdown_resource_urls"
            metadata["extractor"] = "moli_native_markdown"
        else:
            metadata["output_contract"] = "markdown"
            metadata["extractor"] = "moli_native_markdown"
        try:
            if hasattr(self, "_binary_identity"):
                stat = Path(binary).stat()
                if (binary, stat.st_ino, stat.st_size, stat.st_mtime_ns) != self._binary_identity:
                    return ProviderResult(
                        False,
                        "",
                        error_type="configuration",
                        error_message="Moli binary changed; validate runtime before measurement",
                    )
            if (
                getattr(self, "_reported_version", None)
                != self.config["expected_reported_version"].strip()
                or getattr(self, "_binary_sha256", None) != self.config["expected_binary_sha256"]
            ):
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="Moli runtime identity does not match the configured pins",
                )
            metadata["binary_sha256"] = getattr(self, "_binary_sha256", None)
            metadata["reported_version"] = getattr(self, "_reported_version", None)
        except OSError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=f"Cannot read Moli binary: {error}",
                metadata=metadata,
            )
        # Spool artifacts to files rather than retaining arbitrary CLI output in RAM.
        with ExitStack() as dns_stack, ExitStack() as spool_stack:
            sink = getattr(self, "_call_diagnostics", None)
            output = spool_stack.enter_context(
                Path(str(sink.path) + ".moli.stdout").open("x+b") if sink else tempfile.TemporaryFile())
            diagnostic = spool_stack.enter_context(
                Path(str(sink.path) + ".moli.stderr").open("x+b") if sink else tempfile.TemporaryFile())
            if sink: sink.emit("caller", "native_cli_start", command=command)
            from ..network_dns import NativeDnsBroker, validate_native_dns_library
            dns_broker = None
            process_environment = None
            metadata["dns_mode"] = self.config.get("dns_mode", "system")
            if metadata["dns_mode"] == "public_doh":
                library, binding = validate_native_dns_library(self.config.get("native_dns_library"))
                dns_broker = dns_stack.enter_context(NativeDnsBroker(diagnostics=sink))
                remaining = call_deadline - time.monotonic()
                if remaining <= 0:
                    return ProviderResult(False, "", error_type="timeout", error_message="Moli fetch exceeded time budget", metadata=metadata)
                dns_broker.begin_call(remaining)
                process_environment = dns_broker.environment(library)
                metadata["native_dns_binding"] = binding
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=diagnostic,
                    start_new_session=os.name == "posix",
                    env=process_environment,
                )
                with self._process_lock:
                    self._active_process = process
                    if self._cancel_requested:
                        terminate_owned_process_tree(process)
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
                while True:
                    if os.fstat(output.fileno()).st_size > limit:
                        stopped = "output_too_large"
                        break
                    if os.fstat(diagnostic.fileno()).st_size > diagnostic_read_limit:
                        stopped = (
                            "diagnostic_log_too_large" if retain_diagnostic
                            else "output_too_large"
                        )
                        break
                    if process.poll() is not None:
                        break
                    remaining = call_deadline - time.monotonic()
                    if remaining <= 0:
                        stopped = "timeout"
                        break
                    try:
                        process.wait(timeout=min(0.05, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                if stopped:
                    terminate_owned_process_tree(process)
                    process.wait()
                if dns_broker is not None:
                    metadata["dns_queries"] = list(dns_broker.traces)
                stdout_bytes = os.fstat(output.fileno()).st_size
                stderr_bytes = os.fstat(diagnostic.fileno()).st_size
                output.seek(0)
                raw = output.read(limit + 1)
                diagnostic.seek(0)
                diagnostic_raw = diagnostic.read(diagnostic_read_limit + 1)
                public_preview_limit = 1_000_000 if retain_diagnostic else limit
                stderr = diagnostic_raw[:public_preview_limit].decode("utf-8", "replace")
                metadata.update(
                    exit_code=process.returncode,
                    stdout_bytes=stdout_bytes,
                    stdout_truncated=stdout_bytes > limit,
                    stderr=stderr,
                    stderr_bytes=stderr_bytes,
                    stderr_truncated=stderr_bytes > public_preview_limit,
                )
                if retain_diagnostic:
                    metadata.update(
                        diagnostic_log_sha256=hashlib.sha256(diagnostic_raw).hexdigest(),
                        diagnostic_log_bytes=len(diagnostic_raw),
                        diagnostic_log_truncated=stopped == "diagnostic_log_too_large",
                        diagnostic_log_process_completed=stopped is None,
                        _diagnostic_log_b64=base64.b64encode(diagnostic_raw).decode("ascii"),
                    )
        if stopped == "timeout":
            return ProviderResult(
                False,
                "",
                error_type="timeout",
                error_message="Moli fetch exceeded time budget",
                metadata=metadata,
            )
        if stopped == "diagnostic_log_too_large":
            return ProviderResult(
                False,
                "",
                error_type="diagnostic_log_too_large",
                error_message=(
                    "Moli diagnostic log exceeded max_diagnostic_bytes; "
                    "the cohort must not be certified with a truncated log"
                ),
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
        if time.monotonic() >= call_deadline:
            return ProviderResult(
                False,
                "",
                error_type="timeout",
                error_message="Moli fetch exceeded time budget",
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
        if process.returncode == 0 and not content.strip():
            return ProviderResult(
                False,
                content,
                error_type="empty_response",
                error_message="Moli returned no Markdown content",
                metadata=metadata,
            )
        parsed_error_status = _markdown_http_error_status(content)
        ok = process.returncode == 0 and parsed_error_status is None
        native_error_type = _moli_native_error_type(diagnostic_raw)
        final_url = _diagnostic_main_document_url(diagnostic_raw, case.url)
        if final_url:
            metadata["final_url_source"] = "same_call_debug_log"
        # The debug trace is part of this same provider call, so page identity
        # does not require a second request. Moli's compact whole-response error
        # page remains the only status envelope recognized in Markdown output.
        return ProviderResult(
            ok,
            content,
            status_code=parsed_error_status,
            final_url=final_url,
            error_type=(
                None
                if ok
                else "api_error"
                if parsed_error_status is not None
                else native_error_type
            ),
            error_message=(
                None
                if ok
                else f"Moli returned HTTP {parsed_error_status}"
                if parsed_error_status is not None
                else stderr or "Moli request failed"
            ),
            metadata=metadata,
        )

    def cancel(self) -> None:
        # Remember cancellation during Popen without blocking the deadline
        # thread on process creation. The launching thread kills a late child.
        with self._process_lock:
            self._cancel_requested = True
            terminate_owned_process_tree(self._active_process)
            if self._active_native_provider is not None: self._active_native_provider.cancel()

    @property
    def cancellation_cleanup_seconds(self) -> float:
        return 1.0
