from __future__ import annotations

import hashlib
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

from .metrics import evaluate_content, evaluate_resources, summarize, task_passed
from .dynamic_lists import evaluate_dynamic_list
from .list_verification import verify_list
from .models import AttemptRecord, EvalCase, ProviderResult
from .providers import Provider
from .safety import contained_path, safe_identifier


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
    if provider_concurrency < 1:
        raise ValueError("provider_concurrency must be at least 1")
    for provider in providers:
        safe_identifier(provider.name)
    for case in cases:
        safe_identifier(case.id)
    for filename in ['artifacts', 'manifest.json', 'dataset.json', 'results.jsonl', 'summary.json']:
        contained_path(output_dir, filename)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    started_at = utc_now()
    records: list[dict[str, Any]] = []

    manifest = {
        "schema_version": 1,
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
                    begin = time.perf_counter()
                    started = utc_now()
                    try:
                        result = provider.fetch(case, timeout)
                    except Exception as error:  # each failure must remain inspectable
                        error_type, error_message = classify_exception(error)
                        result = ProviderResult(
                            ok=False,
                            content=traceback.format_exc(),
                            error_type=error_type,
                            error_message=error_message,
                        )
                    elapsed_ms = round((time.perf_counter() - begin) * 1000, 2)
                    previous_request_completed = time.monotonic()
                    artifact_rel = Path("artifacts") / provider.name / case.id / f"run-{run_number:03d}.txt"
                    artifact = contained_path(output_dir, artifact_rel)
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text(result.content, encoding="utf-8", errors="replace")
                    measured = evaluate_content(
                        result.content,
                        case.expected_any,
                        required_facts=case.required_facts,
                        forbidden_any=case.forbidden_any,
                        min_content_chars=case.min_content_chars,
                        fact_coverage_threshold=case.fact_coverage_threshold,
                        ground_truth_main_content=case.ground_truth_main_content,
                        required_patterns=case.required_patterns,
                    )
                    measured["content_score_applicable"] = case.content_score_applicable
                    if not case.content_score_applicable:
                        measured["content_pass"] = False
                    if not result.ok:
                        measured['content_pass'] = False
                        if measured['word_f1'] is not None:
                            measured.update(word_precision=0.0, word_recall=0.0, word_f1=0.0)
                    measured.update(evaluate_resources(
                        result.content,
                        result.metadata,
                        case.expected_resources,
                        base_url=case.url,
                        eligible=result.ok,
                    ))
                    measured["deadline_overrun"] = elapsed_ms > timeout * 1000
                    if case.source_benchmark.get('mode')=='dynamic_list_structure':
                        measured.update(evaluate_dynamic_list(result.content,case.source_benchmark,case.url,result.ok))
                        if case.source_benchmark.get('verification_scope')=='first_three_name_detail_pairs':
                            reference=json.loads((dataset_path.parent/'list-references'/f'{case.id}.json').read_text())
                            measured.update(verify_list(result.content,case.source_benchmark,case.url,reference,result.ok))
                    measured["content_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
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
                    from .models import execution_id
                    record['run_id'] = execution_id(record)
                    passed = task_passed(record['ok'],record['metrics'],case.task_kind)
                    state = "PASS" if passed else "FAIL"
                    with result_lock:
                        records.append(record)
                        results_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                        results_file.flush()
                        print(
                            f"{state:4} {provider.name:14} {case.id:24} "
                            f"{elapsed_ms:9.2f} ms  {result.error_type or ''}",
                            flush=True,
                        )
                    if delay:
                        time.sleep(delay)

        workers = min(provider_concurrency, max(1, len(providers)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="webfetch-provider") as executor:
            list(executor.map(run_provider, providers))

    provider_order = {provider.name: index for index, provider in enumerate(providers)}
    case_order = {case.id: index for index, case in enumerate(cases)}
    records.sort(key=lambda item: (provider_order[item["provider"]], item["run"], case_order[item["case_id"]]))
    results_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
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
        case = case_by_id.get(record["case_id"])
        if case is None:
            raise ValueError(f"run contains case absent from dataset: {record['case_id']}")
        artifact = contained_path(run_dir,record["artifact_path"])
        content = artifact.read_text(encoding="utf-8", errors="replace")
        previous_deadline = bool(record.get("metrics", {}).get("deadline_overrun"))
        record["metrics"] = evaluate_content(
            content,
            case.expected_any,
            required_facts=case.required_facts,
            forbidden_any=case.forbidden_any,
            min_content_chars=case.min_content_chars,
            fact_coverage_threshold=case.fact_coverage_threshold,
            ground_truth_main_content=case.ground_truth_main_content,
            required_patterns=case.required_patterns,
        )
        record["metrics"]["content_score_applicable"] = case.content_score_applicable
        if not case.content_score_applicable:
            record["metrics"]["content_pass"] = False
        if not record['ok']:
            record['metrics']['content_pass'] = False
            if record['metrics']['word_f1'] is not None:
                record['metrics'].update(word_precision=0.0,word_recall=0.0,word_f1=0.0)
        record["metrics"].update(evaluate_resources(
            content,
            record.get("provider_metadata", {}),
            case.expected_resources,
            base_url=case.url,
            eligible=bool(record.get("ok")),
        ))
        record["metrics"]["deadline_overrun"] = previous_deadline
        if case.source_benchmark.get('mode')=='dynamic_list_structure':
            record['metrics'].update(evaluate_dynamic_list(content,case.source_benchmark,case.url,bool(record['ok'])))
            if case.source_benchmark.get('verification_scope')=='first_three_name_detail_pairs':
                reference=json.loads((dataset_path.parent/'list-references'/f'{case.id}.json').read_text())
                record['metrics'].update(verify_list(content,case.source_benchmark,case.url,reference,bool(record['ok'])))
        record["metrics"]["content_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()

    tmp = run_dir / "results.jsonl.tmp"
    tmp.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
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
