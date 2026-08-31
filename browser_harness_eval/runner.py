from __future__ import annotations

import json
import platform
import socket
import sys
import time
import traceback
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import evaluate_content, summarize
from .models import AttemptRecord, EvalCase, ProviderResult
from .providers import Provider


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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
            {"name": provider.name, "type": provider.type} for provider in providers
        ],
        "parameters": {"runs": runs, "timeout": timeout, "delay": delay},
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

    results_path = output_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as results_file:
        for provider in providers:
            missing = provider.validate()
            if missing:
                raise ValueError(
                    f"provider {provider.name!r} missing configuration: {', '.join(missing)}"
                )
            for run_number in range(1, runs + 1):
                for case in cases:
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
                    artifact_rel = Path("artifacts") / provider.name / case.id / f"run-{run_number:03d}.txt"
                    artifact = output_dir / artifact_rel
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text(result.content, encoding="utf-8", errors="replace")
                    measured = evaluate_content(
                        result.content,
                        case.expected_any,
                        required_facts=case.required_facts,
                        forbidden_any=case.forbidden_any,
                        min_content_chars=case.min_content_chars,
                        fact_coverage_threshold=case.fact_coverage_threshold,
                    )
                    measured["deadline_overrun"] = elapsed_ms > timeout * 1000
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
                        provider_metadata=result.metadata,
                    ).to_dict()
                    records.append(record)
                    results_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    results_file.flush()
                    state = "PASS" if result.ok and record["metrics"]["content_pass"] else "FAIL"
                    print(
                        f"{state:4} {provider.name:14} {case.id:24} "
                        f"{elapsed_ms:9.2f} ms  {result.error_type or ''}",
                        flush=True,
                    )
                    if delay:
                        time.sleep(delay)

    summary = summarize(records)
    manifest["completed_at"] = utc_now()
    manifest["attempt_count"] = len(records)
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
        for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def rescore_evaluation(
    *, run_dir: Path, dataset_meta: dict[str, Any], dataset_path: Path, cases: list[EvalCase]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recompute deterministic metrics from immutable artifacts without supplier calls."""
    case_by_id = {case.id: case for case in cases}
    records = load_records(run_dir)
    for record in records:
        case = case_by_id.get(record["case_id"])
        if case is None:
            raise ValueError(f"run contains case absent from dataset: {record['case_id']}")
        artifact = run_dir / record["artifact_path"]
        content = artifact.read_text(encoding="utf-8", errors="replace")
        previous_deadline = bool(record.get("metrics", {}).get("deadline_overrun"))
        record["metrics"] = evaluate_content(
            content,
            case.expected_any,
            required_facts=case.required_facts,
            forbidden_any=case.forbidden_any,
            min_content_chars=case.min_content_chars,
            fact_coverage_threshold=case.fact_coverage_threshold,
        )
        record["metrics"]["deadline_overrun"] = previous_deadline

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
