from __future__ import annotations

import base64
import hashlib
import json
import math
import platform
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.record_io import load_jsonl

from .artifacts import (
    PRIVATE_ARTIFACT_ROOT,
    PUBLIC_CALL_LOG_ROOT,
    diagnostic_log_binding,
    raw_response_binding,
    verify_record_artifacts,
    verify_record_private_artifacts,
    verify_record_public_call_logs,
)
from .call_boundary import fetch_with_deadline
from .metrics import summarize, task_passed
from .models import AttemptRecord, EvalCase, ProviderResult, validate_case
from .providers import Provider
from .reference_inputs import read_dataset_bytes
from .reference_lifecycle import (
    ReferenceLifecycleError,
    check_case_reference_binding,
    check_case_reference_fresh,
    is_diagnostic_evidence,
    preflight_reference_checks,
)
from .diagnostics import CallDiagnostics
from .response_input import apply_request_status
from .safety import contained_path, safe_identifier
from .scoring import load_list_reference, score_attempt


class IncompleteEvaluationError(RuntimeError):
    """The saved run is inspectable, but it is not a complete benchmark."""


def preflight_declared_capacity(
    providers: list[Provider], *, declared_calls: int, timeout: float = 10,
    cases: list[EvalCase] | None = None, runs: int = 1,
    max_failure_retries: int = 0,
) -> None:
    """Check optional account capacity once per logical provider.

    ``providers`` may contain separately configured instances for multiple task
    scopes.  Capacity, however, is declared for the logical provider's entire
    cohort, so only the first instance for each provider name receives the
    complete worst-case physical-call bound.
    """
    if isinstance(declared_calls, bool) or not isinstance(declared_calls, int):
        raise ValueError("declared call count must be a positive integer")
    if declared_calls < 1:
        raise ValueError("declared call count must be a positive integer")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("capacity preflight timeout must be positive and finite")
    if cases is not None:
        expected_calls = len(cases) * runs * (max_failure_retries + 1)
        if declared_calls != expected_calls:
            raise ValueError(
                "declared call count does not match cases, runs and retry bound"
            )
    checked: set[str] = set()
    for provider in providers:
        if provider.name in checked:
            continue
        checked.add(provider.name)
        hook = getattr(provider, "preflight_capacity", None)
        if hook is not None:
            if not callable(hook):
                raise ValueError(
                    f"provider {provider.name!r} has an invalid capacity preflight hook"
                )
            estimator = getattr(provider, "capacity_credits_for_cases", None)
            if cases is not None and estimator is not None:
                if not callable(estimator):
                    raise ValueError(
                        f"provider {provider.name!r} has an invalid capacity estimator"
                    )
                required_credits = estimator(
                    cases,
                    runs=runs,
                    max_failure_retries=max_failure_retries,
                )
                hook(
                    declared_calls,
                    timeout=timeout,
                    required_credits=required_credits,
                )
            else:
                hook(declared_calls, timeout=timeout)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_exception(error: Exception) -> tuple[str, str]:
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout", str(error)
    if isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        if isinstance(reason, socket.gaierror):
            return "dns", str(error)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "timeout", str(error)
        return "network", str(error)
    if isinstance(error, ValueError):
        return "response_validation", str(error)
    return "provider_exception", f"{type(error).__name__}: {error}"


_SENSITIVE_KEY = (
    r"(?:(?:[a-z0-9]+[-_])*(?:proxy[-_]?authorization|authorization|(?:set[-_])?cookie|"
    r"(?:access|refresh|id)[-_]?token|x[-_]?auth[-_]?token|private[-_]?token|token|"
    r"(?:aws[-_])?secret[-_]?access[-_]?key|client[-_]?secret|secret|credentials?|"
    r"x[-_]?api[-_]?key|api[-_]?key|password|passwd))"
)
_JSON_SECRET_VALUE = re.compile(
    r"(?i)([\"']" + _SENSITIVE_KEY + r"[\"']\s*:\s*[\"'])(?!<|\[)"
    r"((?:\\.|[^\"'\r\n])*)([\"'])"
)
_LINE_SECRET_VALUE = re.compile(
    r"(?im)(^|[ \t])(" + _SENSITIVE_KEY + r"[ \t]*[:=])"
    r"(?![ \t]*(?:<|\[))[ \t]*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_AUTH_SECRET_VALUE = re.compile(
    r"(?im)((?:proxy[-_]?authorization|authorization)[ \t]*[:=][ \t]*)"
    r"(?![A-Za-z][A-Za-z0-9_-]*[ \t]+(?:<|\[)|(?:<|\[))"
    r"(?:[A-Za-z][A-Za-z0-9_-]*[ \t]+)?[^\s,;]+"
)
_POSIX_LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var|opt|etc)/[^\s'\"<>]+"
)
_WINDOWS_LOCAL_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\s'\"<>]+")


def _public_text(value: Any, *, limit: int = 4096) -> str | None:
    """Sanitize one allowlisted diagnostic scalar for public evidence."""
    if value is None:
        return None
    text = str(value).replace("\x00", "")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _AUTH_SECRET_VALUE.sub(lambda match: match.group(1) + "[REDACTED]", text)
    text = _JSON_SECRET_VALUE.sub(lambda match: match.group(1) + "[REDACTED]" + match.group(3), text)
    text = _LINE_SECRET_VALUE.sub(lambda match: match.group(1) + match.group(2) + " [REDACTED]", text)
    from .publication_privacy import redact_url_queries
    text, _ = redact_url_queries(text)
    text = _POSIX_LOCAL_PATH.sub("[local-path]", text)
    text = _WINDOWS_LOCAL_PATH.sub("[local-path]", text)
    return text[:limit]


def _public_url(value: Any) -> str | None:
    """Keep public HTTP(S) targets while redacting credentials and secret query values."""
    if value is None:
        return None
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return "[invalid-url]"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "[non-public-url]"
    host = parsed.hostname
    if parsed.port is not None:
        host += f":{parsed.port}"
    secret_markers = ("authorization", "cookie", "key", "token", "secret", "password",
                      "signature", "sig")
    query = urlencode([
        (key, "[REDACTED]" if any(marker in key.casefold() for marker in secret_markers)
         else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ])
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


def _exception_stack(error: Exception | None) -> list[dict[str, Any]]:
    if error is None:
        return []
    return [{"file": Path(frame.filename).name, "line": frame.lineno,
             "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)]


_REQUEST_SUMMARY_FORBIDDEN = (
    "authorization", "header", "cookie", "token", "secret", "password", "credential",
    "environment", "env", "raw", "body", "payload", "key", "signature",
)


def _public_request_summary(value: Any) -> dict[str, Any] | None:
    """Project an adapter-declared summary; never infer it from request internals."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    public: dict[str, Any] = {}
    for raw_key in sorted(value, key=str)[:32]:
        key = str(raw_key)
        folded = key.casefold()
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key)
                or any(marker in folded for marker in _REQUEST_SUMMARY_FORBIDDEN)):
            continue
        item = value[raw_key]
        if item is None or isinstance(item, (bool, int, float)):
            public[key] = item
        elif isinstance(item, str):
            public[key] = _public_url(item) if key.casefold().endswith("url") else _public_text(
                item, limit=512
            )
        elif isinstance(item, list) and len(item) <= 32 and all(
            element is None or isinstance(element, (bool, int, float, str)) for element in item
        ):
            public[key] = [
                _public_text(element, limit=256) if isinstance(element, str) else element
                for element in item
            ]
    return public


def persist_public_call_log(
    output_dir: Path, *, provider: Provider, case: EvalCase, attempt_id: str,
    run_number: int, physical_attempt: int, started_at: str, completed_at: str,
    elapsed_ms: float, retry_throttle_wait_ms: float, timeout: float,
    artifact_rel: Path, content_sha256: str, result: ProviderResult,
    exception: Exception | None,
) -> dict[str, Any]:
    """Persist a closed-schema public diagnostic record for one physical call."""
    output_bytes = contained_path(output_dir, artifact_rel).stat().st_size
    metadata = result.metadata
    upstream = metadata.get("upstream_status")
    if not isinstance(upstream, int) or isinstance(upstream, bool):
        upstream = None
    exit_code = metadata.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        exit_code = None
    content_type = metadata.get("content_type")
    request_id = metadata.get("request_id")
    request_method = metadata.get("request_method") or metadata.get("method")
    request_method = _public_text(request_method, limit=32)
    request_summary = _public_request_summary(metadata.get("request_summary"))
    process_text_limit = 1_000_000
    raw_process_stdout = metadata.get("stdout") if "stdout" in metadata else None
    raw_process_stderr = metadata.get("stderr") if "stderr" in metadata else None
    process_stdout = (_public_text(raw_process_stdout, limit=process_text_limit)
                      if raw_process_stdout is not None else None)
    process_stderr = (_public_text(raw_process_stderr, limit=process_text_limit)
                      if raw_process_stderr is not None else None)
    stdout_bytes = metadata.get("stdout_bytes")
    if not isinstance(stdout_bytes, int) or isinstance(stdout_bytes, bool) or stdout_bytes < 0:
        stdout_bytes = (len(str(raw_process_stdout).encode("utf-8", "replace"))
                        if raw_process_stdout is not None else None)
    stderr_bytes = metadata.get("stderr_bytes")
    if not isinstance(stderr_bytes, int) or isinstance(stderr_bytes, bool) or stderr_bytes < 0:
        stderr_bytes = (len(str(raw_process_stderr).encode("utf-8", "replace"))
                        if raw_process_stderr is not None else None)
    stdout_truncated = bool(metadata.get("stdout_truncated")) or (
        raw_process_stdout is not None
        and len(str(raw_process_stdout).encode("utf-8", "replace")) > process_text_limit
    )
    stderr_truncated = bool(metadata.get("stderr_truncated")) or (
        raw_process_stderr is not None
        and len(str(raw_process_stderr).encode("utf-8", "replace")) > process_text_limit
    )
    response_values = (result.status_code, upstream, result.final_url, content_type, request_id)
    raw_retained = raw_response_binding(metadata) is not None
    diagnostic_retained = diagnostic_log_binding(metadata) is not None
    call_id = f"{attempt_id}__p{physical_attempt:03d}"
    payload = {
        "schema_version": 1,
        "identity": {
            "call_id": call_id, "attempt_id": attempt_id, "provider": provider.name,
            "provider_type": provider.type, "case_id": case.id, "run": run_number,
            "physical_attempt": physical_attempt,
        },
        "timing": {
            "started_at": started_at, "completed_at": completed_at,
            "elapsed_ms": elapsed_ms, "retry_throttle_wait_ms": retry_throttle_wait_ms,
            "deadline_budget_ms": timeout * 1000,
            "deadline_overrun": elapsed_ms > timeout * 1000,
        },
        "request": {
            "method": request_method,
            "route": _public_text(metadata.get("route") or provider.type, limit=256),
            "url": _public_url(case.url),
            "summary": request_summary,
        },
        "response": {
            "status_code": result.status_code,
            "upstream_status_code": upstream,
            "final_url": _public_url(result.final_url),
            "content_type": _public_text(content_type, limit=256),
            "request_id": _public_text(request_id, limit=512),
        },
        "process": {
            "exit_code": exit_code,
            "stdout": process_stdout,
            "stdout_bytes": stdout_bytes,
            "stdout_truncated": stdout_truncated,
            "stderr": process_stderr,
            "stderr_bytes": stderr_bytes,
            "stderr_truncated": stderr_truncated,
        },
        "output": {
            "path": artifact_rel.as_posix(), "bytes": output_bytes,
            "sha256": content_sha256, "empty": output_bytes == 0,
        },
        "error": {
            "type": _public_text(result.error_type, limit=256),
            "message": _public_text(result.error_message),
            "exception_class": type(exception).__name__ if exception is not None else None,
            "stack": _exception_stack(exception),
        },
        "events": [
            {"stage": "call_started", "at": started_at, "status_code": None,
             "error_type": None},
            {"stage": "exception_raised" if exception is not None else "response_received",
             "at": completed_at, "status_code": result.status_code,
             "error_type": _public_text(result.error_type, limit=256)},
            {"stage": "call_completed", "at": completed_at,
             "status_code": result.status_code,
             "error_type": _public_text(result.error_type, limit=256)},
        ],
        "capture": {
            "record": "complete",
            "response_metadata": "captured" if any(value is not None for value in response_values)
            else "not_exposed",
            "process_metadata": "captured" if exit_code is not None else "not_exposed",
            "process_stdout": (
                "captured_truncated" if stdout_truncated else
                "captured_complete" if raw_process_stdout is not None else
                "bound_as_output" if stdout_bytes is not None else "not_exposed"
            ),
            "process_stderr": (
                "captured_truncated" if stderr_truncated else
                "captured_complete" if raw_process_stderr is not None else "not_exposed"
            ),
            "request_summary": "captured" if "request_summary" in metadata else "not_exposed",
            "events": "complete",
            "raw_response": "retained_private_hash_bound" if raw_retained else "not_retained",
            "diagnostic_log": (
                "retained_private_hash_bound_complete"
                if diagnostic_retained and not metadata.get("diagnostic_log_truncated")
                else "retained_private_hash_bound_truncated"
                if diagnostic_retained else "not_retained"
            ),
            "output": "complete_hash_bound",
        },
    }
    rel = Path(PUBLIC_CALL_LOG_ROOT) / provider.name / case.id / (
        f"run-{run_number:03d}-physical-{physical_attempt:03d}.json"
    )
    path = contained_path(output_dir, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(binary)
    return {"path": rel.as_posix(), "sha256": hashlib.sha256(binary).hexdigest(),
            "bytes": len(binary)}


def persist_result_artifacts(
    output_dir: Path, artifact_rel: Path, result: ProviderResult
) -> Path:
    """Persist one physical call without changing its scored answer bytes."""
    artifact = contained_path(output_dir, artifact_rel)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(result.content, encoding='utf-8', errors='replace')
    rendered_html = result.metadata.pop('_rendered_html', None)
    if rendered_html is not None:
        html_rel = artifact_rel.with_suffix('.html.txt')
        contained_path(output_dir, html_rel).write_text(rendered_html, encoding='utf-8')
        result.metadata['rendered_html_path'] = html_rel.as_posix()
    raw_stdout = result.metadata.pop('_raw_stdout_b64', None)
    if raw_stdout is not None:
        binary_rel = artifact_rel.with_suffix('.bin')
        binary = base64.b64decode(raw_stdout, validate=True)
        if hashlib.sha256(binary).hexdigest() != result.metadata['raw_stdout_sha256']:
            raise ValueError('Raw stdout hash mismatch')
        contained_path(output_dir, binary_rel).write_bytes(binary)
        result.metadata['raw_stdout_path'] = binary_rel.as_posix()
    raw_response = result.metadata.pop('_raw_response_b64', None)
    if raw_response is not None:
        binary = base64.b64decode(raw_response, validate=True)
        expected_hash = result.metadata.get('raw_response_sha256')
        expected_bytes = result.metadata.get('raw_response_bytes')
        if hashlib.sha256(binary).hexdigest() != expected_hash:
            raise ValueError('Raw response hash mismatch')
        if len(binary) != expected_bytes:
            raise ValueError('Raw response byte size mismatch')
        private_rel = (Path(PRIVATE_ARTIFACT_ROOT, *artifact_rel.parts[1:])
                       .with_suffix('.response.bin'))
        private_path = contained_path(output_dir, private_rel)
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_bytes(binary)
        result.metadata['raw_response_path'] = private_rel.as_posix()
        raw_response_binding(result.metadata)
    elif any(result.metadata.get(key) is not None for key in
             ('raw_response_path', 'raw_response_sha256', 'raw_response_bytes')):
        raise ValueError('Raw response binding supplied without raw response bytes')
    diagnostic_log = result.metadata.pop('_diagnostic_log_b64', None)
    if diagnostic_log is not None:
        binary = base64.b64decode(diagnostic_log, validate=True)
        expected_hash = result.metadata.get('diagnostic_log_sha256')
        expected_bytes = result.metadata.get('diagnostic_log_bytes')
        if hashlib.sha256(binary).hexdigest() != expected_hash:
            raise ValueError('Diagnostic log hash mismatch')
        if len(binary) != expected_bytes:
            raise ValueError('Diagnostic log byte size mismatch')
        private_rel = (Path(PRIVATE_ARTIFACT_ROOT, *artifact_rel.parts[1:])
                       .with_suffix('.stderr.log'))
        private_path = contained_path(output_dir, private_rel)
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_bytes(binary)
        result.metadata['diagnostic_log_path'] = private_rel.as_posix()
        diagnostic_log_binding(result.metadata)
    elif any(result.metadata.get(key) is not None for key in
             ('diagnostic_log_path', 'diagnostic_log_sha256', 'diagnostic_log_bytes')):
        raise ValueError('Diagnostic log binding supplied without diagnostic log bytes')
    delivered = result.metadata.get('file_evaluation', {}).get('files', [])
    if len(delivered) != len(result.files):
        raise ValueError(
            'File evaluation does not match returned files: '
            f'evaluated={len(delivered)}, returned={len(result.files)}'
        )
    for index, item in enumerate(result.files):
        binary = artifact.with_name(artifact.stem + f'-file-{index:03d}.bin')
        binary.write_bytes(item['data'])
        delivered[index]['artifact_path'] = binary.relative_to(output_dir).as_posix()
    return artifact


def run_evaluation(
    *,
    output_dir: Path,
    dataset_meta: dict[str, Any],
    dataset_path: Path,
    cases: list[EvalCase],
    providers: list[Provider],
    provider_config_path: Path,
    runs: int,
    timeout: float,
    delay: float,
    provider_concurrency: int = 1,
    max_failure_retries: int = 0,
    resource_sample_interval_ms: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir = output_dir.resolve()
    for name, value, minimum in (
        ('runs', runs, 1), ('provider_concurrency', provider_concurrency, 1),
        ('max_failure_retries', max_failure_retries, 0),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(f'{name} must be an integer of at least {minimum}')
    for name, value, minimum in (('timeout', timeout, 0), ('delay', delay, 0)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < minimum or (name == 'timeout' and value == 0)):
            raise ValueError(f'{name} must be finite and {"positive" if name == "timeout" else "nonnegative"}')
    if max_failure_retries > 1:
        raise ValueError('max_failure_retries must be 0 or 1')
    if resource_sample_interval_ms is not None and (
        type(resource_sample_interval_ms) is not int or resource_sample_interval_ms < 10
    ):
        raise ValueError('resource_sample_interval_ms must be an integer of at least 10')
    if not cases or not providers:
        raise ValueError('Execution requires at least one task and provider')
    for provider in providers:
        if provider.config.get("uniform_native_parameters"):
            if runs != 1 or max_failure_retries != 0:
                raise ValueError("fixed native calls require one run and zero caller retries")
            for case in cases:
                provider.check_native_call(case)
        safe_identifier(provider.name)
    for case in cases:
        validate_case(case)
    if len({provider.name for provider in providers}) != len(providers):
        raise ValueError('Provider names must be unique within an execution')
    if len({case.id for case in cases}) != len(cases):
        raise ValueError('Task IDs must be unique within an execution')
    for filename in ['artifacts', PUBLIC_CALL_LOG_ROOT, 'manifest.json', 'dataset.json',
                     'results.jsonl', 'summary.json']:
        contained_path(output_dir, filename)
    reference_checks = preflight_reference_checks(cases, dataset_path)
    for provider in providers:
        missing = provider.validate()
        if missing:
            raise ValueError(
                f"provider {provider.name!r} missing configuration: {', '.join(missing)}"
            )
    from .network_preflight import preflight_guarded_providers
    network_audit = preflight_guarded_providers(providers, cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    if network_audit is not None:
        (output_dir / 'network-preflight.json').write_text(
            json.dumps(network_audit, ensure_ascii=False, indent=2) + '\n')
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    started_at = utc_now()
    records: list[dict[str, Any]] = []

    manifest = {
        "schema_version": 1,
        "runtime_source_sha256": hashlib.sha256(b'\0'.join(
            p.relative_to(Path(__file__).parent).as_posix().encode()+b'\0'+p.read_bytes()
            for p in sorted(Path(__file__).parent.rglob('*.py')))).hexdigest(),
        "provider_config_sha256": hashlib.sha256(provider_config_path.read_bytes()).hexdigest() if provider_config_path.is_file() else None,
        "dataset_sha256": hashlib.sha256(read_dataset_bytes(dataset_path)).hexdigest() if dataset_path.is_file() or (dataset_path.parent / 'source-inputs.json').is_file() else None,
        "network_preflight": ({"dns_mode": network_audit["dns_mode"],
                               "sha256": hashlib.sha256((output_dir / 'network-preflight.json').read_bytes()).hexdigest()}
                              if network_audit is not None else None),
        "unexecuted": [],
        "deadline_policy": "end_to_end_answer; non_cancellable_adapter_quarantined",
        "public_call_log_schema_version": 1,
        "tool": "web_fetch",
        "started_at": started_at,
        "dataset": {
            "id": dataset_meta.get("id"),
            "name": dataset_meta.get("name"),
            "source": str(dataset_path.resolve()),
            "case_count": len(cases),
        },
        "providers": [
            {
                "name": provider.name,
                "type": provider.type,
                "min_interval_seconds": provider.min_interval_seconds,
                **({"implementation_identity": provider._implementation_identity}
                   if isinstance(getattr(provider, '_implementation_identity', None), dict) else {}),
            }
            for provider in providers
        ],
        "parameters": {
            "runs": runs,
            "timeout": timeout,
            "delay": delay,
            "provider_concurrency": min(provider_concurrency, max(1, len(providers))),
            "concurrency_scope": "providers_parallel_cases_sequential_per_provider",
            "max_failure_retries": max_failure_retries,
            "retry_selection": "retry_only_after_failed_task; final_physical_attempt_is_authoritative",
            "resource_sample_interval_ms": resource_sample_interval_ms,
            "resource_scope": (
                "disposable Linux client container cgroup and dedicated network namespace; "
                "host controller and remote provider service excluded"
                if resource_sample_interval_ms is not None else None
            ),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "hostname": socket.gethostname(),
        },
        "provider_config_source": str(provider_config_path.resolve()),
    }
    if reference_checks is not None:
        manifest["reference_checks"] = reference_checks
    comparison = dataset_meta.get('comparison', {})
    if is_diagnostic_evidence(dataset_meta):
        manifest['evidence_state'] = 'diagnostic'
        manifest['unqualified_reference_cases'] = sorted(
            set(comparison.get('unqualified_reference_cases', [])) & {case.id for case in cases})
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "dataset.json").write_text(
        json.dumps(dataset_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    results_path = output_dir / "results.jsonl"
    result_lock = threading.Lock()
    with results_path.open("w", encoding="utf-8") as results_file:
        def run_provider(provider: Provider) -> None:
            previous_request_completed: float | None = None
            for run_number in range(1, runs + 1):
                for case_index, case in enumerate(cases, 1):
                    throttle_wait_ms = 0.0
                    if previous_request_completed is not None and provider.min_interval_seconds:
                        remaining = provider.min_interval_seconds - (time.monotonic() - previous_request_completed)
                        if remaining > 0:
                            throttle_wait_ms = round(remaining * 1000, 2)
                            time.sleep(remaining)
                    attempt_id = f"{provider.name}__{case.id}__r{run_number:03d}"
                    binding = reference_checks["cases"].get(case.id) if reference_checks else None
                    # Outside provider exception handling: expired references
                    # stop measurement, never create a supplier failure record.
                    check_case_reference_fresh(case, dataset_path, binding)
                    started = utc_now()
                    physical_attempts = []
                    stopped_provider = False
                    for physical_attempt in range(1, max_failure_retries + 2):
                        retry_throttle_wait_ms = 0.0
                        if physical_attempt > 1 and provider.min_interval_seconds:
                            remaining = provider.min_interval_seconds - (time.monotonic() - previous_request_completed)
                            if remaining > 0:
                                retry_throttle_wait_ms = round(remaining * 1000, 2)
                                time.sleep(remaining)
                        physical_started = utc_now()
                        physical_begin = time.perf_counter()
                        pending = None
                        caught_exception = None
                        diagnostics = None
                        if provider.config.get("log_level") == "debug":
                            diagnostics = CallDiagnostics(output_dir / "private_artifacts" / "live-logs" / provider.name / case.id / f"run-{run_number:03d}-physical-{physical_attempt:03d}.jsonl", attempt_id)
                            diagnostics.emit("caller", "call_started", provider=provider.name, case_id=case.id, url=case.url, timeout_seconds=timeout)
                        provider._call_diagnostics = diagnostics
                        try:
                            result, pending = fetch_with_deadline(provider, case, timeout)
                        except Exception as error:  # each physical failure remains inspectable
                            caught_exception = error
                            error_type, error_message = classify_exception(error)
                            public_metadata = getattr(error, "public_metadata", {})
                            public_metadata = public_metadata if isinstance(public_metadata, dict) else {}
                            allowed_diagnostic = {
                                key: public_metadata[key]
                                for key in ("_answer_elapsed_ms", "exit_code", "stdout",
                                            "stdout_bytes", "stdout_truncated",
                                            "stderr", "stderr_bytes", "stderr_truncated")
                                if key in public_metadata
                            }
                            result = ProviderResult(
                                ok=False,
                                # Adapter exceptions are diagnostics, never a
                                # supplier answer for the public scorer.
                                content="",
                                error_type=error_type,
                                error_message=_public_text(error_message),
                                metadata=allowed_diagnostic,
                            )
                        wall_elapsed_ms = round((time.perf_counter() - physical_begin) * 1000, 2)
                        if diagnostics is not None:
                            diagnostics.finish(result)
                        # Cancellable adapters may need a bounded post-deadline
                        # unwind. That cleanup determines quarantine safety but
                        # is not part of the answer latency.
                        physical_elapsed_ms = result.metadata.pop(
                            '_answer_elapsed_ms', wall_elapsed_ms
                        )
                        previous_request_completed = time.monotonic()
                        if case.task_kind == 'file':
                            result.metadata.setdefault('file_evaluation', {'files': [], 'pass': False,
                                'scope': 'returned_bytes_signature_length_sha256'})
                        suffix = (f'run-{run_number:03d}.txt' if max_failure_retries == 0 else
                                  f'run-{run_number:03d}-physical-{physical_attempt:03d}.txt')
                        artifact_rel = Path('artifacts') / provider.name / case.id / suffix
                        artifact = persist_result_artifacts(output_dir, artifact_rel, result)
                        artifact_bytes = result.content.encode('utf-8', errors='replace')
                        measured = score_attempt(
                            case, artifact_bytes, ok=result.ok, metadata=result.metadata,
                            title=result.title, final_url=result.final_url,
                            list_reference=load_list_reference(case, dataset_path.parent),
                            status_code=result.status_code,
                            prepared_answer=result.prepared_answer,
                        )
                        measured['deadline_overrun'] = physical_elapsed_ms > timeout * 1000
                        measured['deadline_budget_ms'] = timeout * 1000
                        if binding is not None:
                            measured['reference_sha256'] = binding['reference_sha256']
                        physical_passed = task_passed(result.ok, measured, case.task_kind)
                        physical_completed = utc_now()
                        call_log = persist_public_call_log(
                            output_dir, provider=provider, case=case, attempt_id=attempt_id,
                            run_number=run_number, physical_attempt=physical_attempt,
                            started_at=physical_started, completed_at=physical_completed,
                            elapsed_ms=physical_elapsed_ms,
                            retry_throttle_wait_ms=retry_throttle_wait_ms, timeout=timeout,
                            artifact_rel=artifact_rel,
                            content_sha256=measured['content_sha256'], result=result,
                            exception=caught_exception,
                        )
                        artifact_paths = [artifact_rel.as_posix()]
                        for key in ('rendered_html_path', 'raw_stdout_path'):
                            if result.metadata.get(key):
                                artifact_paths.append(result.metadata[key])
                        artifact_paths.extend(
                            item['artifact_path']
                            for item in result.metadata.get('file_evaluation', {}).get('files', [])
                            if item.get('artifact_path')
                        )
                        artifact_hashes = {
                            relative: hashlib.sha256(contained_path(
                                output_dir, relative, reject_symlinks=True).read_bytes()).hexdigest()
                            for relative in artifact_paths
                        }
                        if artifact_hashes[artifact_rel.as_posix()] != measured['content_sha256']:
                            raise ValueError('Persisted output differs from scored bytes')
                        private_artifacts = []
                        private_binding = raw_response_binding(result.metadata)
                        if private_binding:
                            private_artifacts.append(private_binding)
                        diagnostic_binding = diagnostic_log_binding(result.metadata)
                        if diagnostic_binding:
                            private_artifacts.append(diagnostic_binding)
                        physical_attempts.append({
                            'physical_attempt': physical_attempt,
                            'started_at': physical_started,
                            'completed_at': physical_completed,
                            'elapsed_ms': physical_elapsed_ms,
                            'retry_throttle_wait_ms': retry_throttle_wait_ms,
                            'ok': result.ok,
                            'status_code': result.status_code,
                            'error_type': result.error_type,
                            'error_message': result.error_message,
                            'task_pass': physical_passed,
                            'content_sha256': measured['content_sha256'],
                            'artifact_path': artifact_rel.as_posix(),
                            'artifact_paths': artifact_paths,
                            'artifact_sha256': artifact_hashes,
                            'call_log': call_log,
                            **({'private_artifacts': private_artifacts} if private_artifacts else {}),
                        })
                        stopped_provider = (result.error_type == 'timeout' and pending is not None
                                            and pending.is_alive())
                        if physical_passed or stopped_provider or physical_attempt > max_failure_retries:
                            break
                    elapsed_ms = round(sum(item['elapsed_ms'] + item['retry_throttle_wait_ms']
                                           for item in physical_attempts), 2)
                    result.metadata['call_log'] = physical_attempts[-1]['call_log']
                    if len(physical_attempts) > 1:
                        result.metadata['retry'] = {
                            'physical_attempts': len(physical_attempts),
                            'retries_used': len(physical_attempts) - 1,
                            'trigger': 'previous physical attempt failed the task predicate',
                            'selection': 'final physical attempt is authoritative; no best-of selection',
                            'attempts': physical_attempts,
                        }
                    finished = utc_now()
                    if hashlib.sha256(artifact.read_bytes()).hexdigest() != measured['content_sha256']:
                        raise ValueError('Persisted output differs from scored bytes')
                    record = AttemptRecord(
                        attempt_id=attempt_id,
                        provider=provider.name,
                        provider_type=provider.type,
                        case_id=case.id,
                        case_name=case.name,
                        category=case.category,
                        url=case.url,
                        run=run_number,
                        started_at=started,
                        elapsed_ms=elapsed_ms,
                        ok=result.ok,
                        status_code=result.status_code,
                        final_url=result.final_url,
                        title=result.title,
                        error_type=result.error_type,
                        error_message=result.error_message,
                        artifact_path=artifact_rel.as_posix(),
                        metrics=measured,
                        provider_metadata={
                            **result.metadata,
                            "configured_min_interval_seconds": provider.min_interval_seconds,
                            "client_throttle_wait_ms": throttle_wait_ms,
                        },
                    ).to_dict()
                    apply_request_status(record)
                    record["completed_at"] = finished
                    from .models import execution_id
                    record['run_id'] = execution_id(record)
                    passed = task_passed(record['ok'],record['metrics'],case.task_kind)
                    state = "PASS" if passed else "FAIL"
                    with result_lock:
                        records.append(record)
                        results_file.write(json.dumps(record, ensure_ascii=True) + "\n")
                        results_file.flush()
                        print(
                            f"{state:4} {provider.name:14} {case.id:24} "
                            f"{elapsed_ms:9.2f} ms  {result.error_type or ''}",
                            flush=True,
                        )
                    if delay:
                        time.sleep(delay)
                    if stopped_provider:
                        remaining_attempts = ((runs - run_number) * len(cases)
                                              + len(cases) - case_index)
                        if remaining_attempts:
                            with result_lock:
                                manifest['unexecuted'].append({'provider': provider.name,
                                    'after_attempt_id': attempt_id,
                                    'reason': 'non_cancellable_call_still_running; remaining provider cases not executed'})
                            return

        workers = min(provider_concurrency, max(1, len(providers)))
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="webfetch-provider") as executor:
                list(executor.map(run_provider, providers))
        except ReferenceLifecycleError as error:
            if reference_checks is not None:
                reference_checks.update(status=error.status, callcheck_error=str(error))
                manifest.update(completed_at=utc_now(), attempt_count=len(records),
                                attempt_ids=[record["attempt_id"] for record in records])
                (output_dir / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise

    provider_order = {provider.name: index for index, provider in enumerate(providers)}
    case_order = {case.id: index for index, case in enumerate(cases)}
    records.sort(key=lambda item: (provider_order[item["provider"]], item["run"], case_order[item["case_id"]]))
    results_path.write_text(
        "".join(json.dumps(record, ensure_ascii=True) + "\n" for record in records),
        encoding="utf-8",
    )

    summary = summarize(records)
    manifest["completed_at"] = utc_now()
    manifest["attempt_count"] = len(records)
    manifest["attempt_ids"] = [r['attempt_id'] for r in records]
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    expected_attempts = len(cases) * len(providers) * runs
    if len(records) != expected_attempts or manifest["unexecuted"]:
        raise IncompleteEvaluationError(
            f"Evaluation incomplete: {len(records)}/{expected_attempts} attempts finished; "
            f"evidence retained at {output_dir}"
        )
    return records, summary


def load_records(run_dir: Path) -> list[dict[str, Any]]:
    return load_jsonl(contained_path(run_dir, "results.jsonl"), skip_blank=True)


def rescore_evaluation(
    *, run_dir: Path, dataset_meta: dict[str, Any], dataset_path: Path, cases: list[EvalCase]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recompute deterministic metrics from immutable artifacts without supplier calls."""
    for case in cases:
        safe_identifier(case.id)
    for filename in ['results.jsonl.tmp', 'results.jsonl', 'summary.json', 'dataset.json', 'manifest.json']:
        contained_path(run_dir,filename)
    case_by_id = {case.id: case for case in cases}
    records = load_records(run_dir)
    for record in records:
        if record.get("metrics", {}).get("content_sha256"):
            verify_record_artifacts(run_dir, record)
        else:
            # Historical records without a primary hash keep their legacy contract.
            verify_record_private_artifacts(run_dir, record)
            verify_record_public_call_logs(run_dir, record)
        contained_path(run_dir, record["artifact_path"])
        expected_hash = record.get("metrics", {}).get("content_sha256")
        if expected_hash and hashlib.sha256(contained_path(run_dir, record["artifact_path"]).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Output integrity failure: " + record["attempt_id"])
    manifest = json.loads(contained_path(run_dir, "manifest.json").read_text(encoding="utf-8"))
    saved_dataset = json.loads(contained_path(run_dir, "dataset.json").read_text(encoding="utf-8"))
    if is_diagnostic_evidence(saved_dataset) or is_diagnostic_evidence(manifest):
        # Scoring inputs may change; the executed population and intent may not.
        selected_ids = [case['id'] for case in saved_dataset['cases']]
        revised = {case['id']: case for case in dataset_meta['cases']}
        if len(revised) != len(dataset_meta['cases']) or not set(selected_ids) <= revised.keys():
            raise ValueError('Rescore dataset omits or duplicates executed tasks')
        for original in saved_dataset['cases']:
            if any(original.get(key) != revised[original['id']].get(key)
                   for key in ('url', 'track', 'task_kind')):
                raise ValueError('Rescore changed executed task scope')
        dataset_meta = deepcopy(dataset_meta)
        dataset_meta['cases'] = [revised[identifier] for identifier in selected_ids]
        dataset_meta['coverage_plan'] = deepcopy(saved_dataset['coverage_plan'])
        dataset_meta['comparison'] = deepcopy(saved_dataset.get('comparison', {}))
        dataset_meta['comparison']['evidence_state'] = 'diagnostic'
        cases = [case_by_id[identifier] for identifier in selected_ids]
    # Validate the entire batch before touching metrics, datasets or results.
    # Historical records without a binding retain the legacy rescore behavior.
    bindings = manifest.get("reference_checks", {}).get("cases", {})
    for record in records:
        if "reference_sha256" in record.get("metrics", {}) or record["case_id"] in bindings:
            binding = bindings.get(record["case_id"])
            case = case_by_id.get(record["case_id"])
            if (binding is None or case is None
                    or record.get("metrics", {}).get("reference_sha256") != binding["reference_sha256"]):
                raise ReferenceLifecycleError("Rescore has no matching original reference binding")
            check_case_reference_binding(case, binding)
    for record in records:
        case = case_by_id.get(record["case_id"])
        if case is None:
            raise ValueError(f"run contains case absent from dataset: {record['case_id']}")
        artifact = contained_path(run_dir,record["artifact_path"])
        previous_deadline = bool(record.get("metrics", {}).get("deadline_overrun"))
        record["metrics"] = score_attempt(
            case, artifact.read_bytes(), ok=bool(record.get("ok")),
            title=record.get('title'), final_url=record.get('final_url'),
            metadata=record.get("provider_metadata", {}), previous_metrics=record["metrics"],
            list_reference=load_list_reference(case, dataset_path.parent),
            status_code=record.get('status_code'),
        )
        apply_request_status(record)
        record["metrics"]["deadline_overrun"] = previous_deadline

    tmp = run_dir / "results.jsonl.tmp"
    tmp.write_text("".join(json.dumps(record, ensure_ascii=True) + "\n" for record in records), encoding="utf-8")
    tmp.replace(run_dir / "results.jsonl")
    summary = summarize(records)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "dataset.json").write_text(json.dumps(dataset_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset"].update({"id": dataset_meta.get("id"), "name": dataset_meta.get("name"), "source": str(dataset_path.resolve()), "case_count": len(cases)})
    manifest["rescored_at"] = utc_now()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return records, summary
