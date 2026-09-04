from __future__ import annotations

import hashlib
import base64
import math
import json
import platform
import socket
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import summarize, task_passed
from .scoring import load_list_reference, score_attempt
from .models import AttemptRecord, EvalCase, ProviderResult
from .providers import Provider
from .reference_lifecycle import (
    ReferenceLifecycleError, check_case_reference_binding,
    check_case_reference_fresh, preflight_reference_checks,
)
from .safety import contained_path, safe_identifier
from .call_boundary import fetch_with_deadline
from .response_input import apply_request_status


class IncompleteEvaluationError(RuntimeError):
    """The saved run is inspectable, but it is not a complete benchmark."""


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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_dir = output_dir.resolve()
    if provider_concurrency < 1:
        raise ValueError("provider_concurrency must be at least 1")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be positive and finite')
    for provider in providers:
        safe_identifier(provider.name)
    for case in cases:
        safe_identifier(case.id)
    for filename in ['artifacts', 'manifest.json', 'dataset.json', 'results.jsonl', 'summary.json']:
        contained_path(output_dir, filename)
    reference_checks = preflight_reference_checks(cases, dataset_path)
    output_dir.mkdir(parents=True, exist_ok=True)
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
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest() if dataset_path.is_file() else None,
        "unexecuted": [],
        "deadline_policy": "end_to_end_answer; non_cancellable_adapter_quarantined",
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
            }
            for provider in providers
        ],
        "parameters": {
            "runs": runs,
            "timeout": timeout,
            "delay": delay,
            "provider_concurrency": min(provider_concurrency, max(1, len(providers))),
            "concurrency_scope": "providers_parallel_cases_sequential_per_provider",
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
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "dataset.json").write_text(
        json.dumps(dataset_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for provider in providers:
        missing = provider.validate()
        if missing:
            raise ValueError(
                f"provider {provider.name!r} missing configuration: {', '.join(missing)}"
            )

    results_path = output_dir / "results.jsonl"
    result_lock = threading.Lock()
    with results_path.open("w", encoding="utf-8") as results_file:
        def run_provider(provider: Provider) -> None:
            previous_request_completed: float | None = None
            for run_number in range(1, runs + 1):
                for case in cases:
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
                    begin = time.perf_counter()
                    started = utc_now()
                    pending = None
                    try:
                        result, pending = fetch_with_deadline(provider, case, timeout)
                    except Exception as error:  # each failure must remain inspectable
                        error_type, error_message = classify_exception(error)
                        result = ProviderResult(
                            ok=False,
                            content=traceback.format_exc(),
                            error_type=error_type,
                            error_message=error_message,
                        )
                    elapsed_ms = round((time.perf_counter() - begin) * 1000, 2)
                    finished = utc_now()
                    previous_request_completed = time.monotonic()
                    if case.task_kind == 'file':
                        result.metadata.setdefault('file_evaluation', {'files': [], 'pass': False,
                            'scope': 'returned_bytes_signature_length_sha256'})
                    artifact_rel = Path("artifacts") / provider.name / case.id / f"run-{run_number:03d}.txt"
                    artifact = contained_path(output_dir, artifact_rel)
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text(result.content, encoding="utf-8", errors="replace")
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
                    measured = score_attempt(
                        case, artifact.read_bytes(), ok=result.ok, metadata=result.metadata,
                        list_reference=load_list_reference(case, dataset_path.parent),
                        status_code=result.status_code,
                        prepared_answer=result.prepared_answer,
                    )
                    measured["deadline_overrun"] = elapsed_ms > timeout * 1000
                    measured['deadline_budget_ms'] = timeout * 1000
                    for index, item in enumerate(result.files):
                        binary = artifact.with_name(f'run-{run_number:03d}-file-{index:03d}.bin')
                        binary.write_bytes(item['data'])
                        result.metadata['file_evaluation']['files'][index]['artifact_path'] = binary.relative_to(output_dir).as_posix()
                    if binding is not None:
                        measured["reference_sha256"] = binding["reference_sha256"]
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
                    if result.error_type == 'timeout' and pending is not None and pending.is_alive():
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
    return [
        json.loads(line)
        for line in contained_path(run_dir, "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
        contained_path(run_dir, record["artifact_path"])
        expected_hash = record.get("metrics", {}).get("content_sha256")
        if expected_hash and hashlib.sha256(contained_path(run_dir, record["artifact_path"]).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Output integrity failure: " + record["attempt_id"])
    manifest = json.loads(contained_path(run_dir, "manifest.json").read_text(encoding="utf-8"))
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
