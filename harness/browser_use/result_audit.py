"""Fail-closed consistency audit for a fixed-trajectory result set."""

from __future__ import annotations

from collections import Counter
from typing import Any


class ResultAuditError(ValueError):
    """Raised when published result evidence is internally inconsistent."""


def _passed(check: dict[str, Any]) -> bool:
    return str(check.get("status") or "").lower() in {"success", "pass"} and float(
        check.get("score", 0)
    ) == 1.0


def audit_result_set(manifest: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Audit every physical attempt and final task/browser outcome.

    This verifies evidence structure and score consistency. It does not replace a
    semantic re-annotation of the benchmark's official evaluator contract.
    """

    task_ids = [str(task.get("task_id") or "") for task in manifest.get("suite") or []]
    if not task_ids or any(not task_id for task_id in task_ids):
        raise ResultAuditError("manifest suite must contain non-empty task ids")
    if len(task_ids) != len(set(task_ids)):
        raise ResultAuditError("manifest suite contains duplicate task ids")

    by_task = summary.get("by_task") or {}
    if set(by_task) != set(task_ids):
        missing = sorted(set(task_ids) - set(by_task))
        extra = sorted(set(by_task) - set(task_ids))
        raise ResultAuditError(f"summary task scope mismatch: missing={missing}, extra={extra}")

    protocol = manifest.get("protocol") or {}
    engines = tuple(str(engine) for engine in protocol.get("engines") or ())
    if not engines:
        engines = tuple(str(engine) for engine in (summary.get("functional") or {}))
    if not engines or len(engines) != len(set(engines)):
        raise ResultAuditError("manifest must declare a non-empty unique engine list")
    max_retries = int(protocol.get("max_failure_retries", 0) or 0)
    final_counts: Counter[tuple[str, str]] = Counter()
    physical_attempts = 0
    reviewed: list[dict[str, Any]] = []
    for task_id in task_ids:
        attempts_by_engine = (by_task[task_id].get("attempts") or {})
        for engine in engines:
            attempts = attempts_by_engine.get(engine) or []
            if any(
                index > 1 and str(attempts[index - 2].get("result_status")) == "pass"
                for index in range(1, len(attempts) + 1)
            ):
                raise ResultAuditError(f"{task_id}/{engine} retried after a passing result")
            if not 1 <= len(attempts) <= max_retries + 1:
                raise ResultAuditError(
                    f"{task_id}/{engine} exceeds the declared retry limit"
                )
            physical_attempts += len(attempts)
            for index, attempt in enumerate(attempts, 1):
                if int(attempt.get("attempt") or 0) != index:
                    raise ResultAuditError(f"{task_id}/{engine} attempt numbering is invalid")
                if bool(attempt.get("is_retry")) != (index > 1):
                    raise ResultAuditError(f"{task_id}/{engine} retry flag is invalid")

            for attempt in attempts:
                number = int(attempt.get("attempt") or 0)
                prefix = f"{task_id}/{engine}/attempt-{number}"
                trace = attempt.get("trace") or []
                if not trace:
                    raise ResultAuditError(f"{prefix} has no execution trace")
                browser_trace = [
                    row
                    for row in trace
                    if row.get("action")
                    not in {"evaluate_result", "validate_assertions", "prepare_environment"}
                ]
                process = attempt.get("process_validation") or {}
                actual_actions = [str(row.get("action") or "") for row in browser_trace]
                all_calls_passed = bool(browser_trace) and all(
                    row.get("status") == "pass" for row in browser_trace
                )
                process_pass = (
                    actual_actions == list(process.get("expected_actions") or [])
                    and all_calls_passed
                )
                if list(process.get("actual_actions") or []) != actual_actions:
                    raise ResultAuditError(f"{prefix} process actions contradict trace")
                if bool(process.get("all_calls_passed")) != all_calls_passed:
                    raise ResultAuditError(f"{prefix} call status contradicts trace")
                if bool(process.get("passed")) != process_pass:
                    raise ResultAuditError(f"{prefix} process validation is inconsistent")
                process_status = str(attempt.get("process_status") or "")
                if process_status != ("pass" if process_pass else "fail"):
                    raise ResultAuditError(f"{prefix} process status contradicts evidence")

                checks = attempt.get("validation") or []
                evaluator_pass = bool(checks) and all(_passed(check) for check in checks)
                result_status = str(attempt.get("result_status") or "")
                if result_status != ("pass" if evaluator_pass else "fail"):
                    raise ResultAuditError(f"{prefix} result status contradicts evaluator")
                evaluation_steps = [row for row in trace if row.get("action") == "evaluate_result"]
                if evaluation_steps:
                    if len(evaluation_steps) != 1:
                        raise ResultAuditError(f"{prefix} has multiple evaluator traces")
                    evaluation = evaluation_steps[0]
                    if list(evaluation.get("checks") or []) != checks:
                        raise ResultAuditError(f"{prefix} evaluator trace contradicts validation")
                    if str(evaluation.get("status")) != result_status:
                        raise ResultAuditError(f"{prefix} evaluator trace status is inconsistent")
                elif not (
                    trace[0].get("action") == "prepare_environment" and not process_pass
                ):
                    raise ResultAuditError(f"{prefix} lacks one final evaluator trace")

                evidence_pass = (
                    str((attempt.get("network_har") or {}).get("status")) != "error"
                    and str((attempt.get("final_page_evidence") or {}).get("status"))
                    == "complete"
                )
                if str(attempt.get("evidence_status") or "") != (
                    "pass" if evidence_pass else "fail"
                ):
                    raise ResultAuditError(f"{prefix} evidence status is inconsistent")
                expected_status = (
                    "pass" if process_pass and evaluator_pass and evidence_pass else "fail"
                )
                if str(attempt.get("status")) != expected_status:
                    raise ResultAuditError(f"{prefix} published status is inconsistent")
                if str(attempt.get("combined_status")) != expected_status:
                    raise ResultAuditError(f"{prefix} combined status is inconsistent")
                if not process_pass and not str(attempt.get("error") or "").strip():
                    raise ResultAuditError(f"{prefix} process failure lacks an error log")

            final = attempts[-1]
            process_status = str(final.get("process_status") or "")
            result_status = str(final.get("result_status") or "")

            final_counts[(engine, result_status)] += 1
            reviewed.append(
                {
                    "task_id": task_id,
                    "engine": engine,
                    "attempts": len(attempts),
                    "final_result_status": result_status,
                    "final_process_status": process_status,
                    "evaluator_checks": len(final.get("validation") or []),
                }
            )

    functional = summary.get("functional") or {}
    for engine in engines:
        values = functional.get(engine) or {}
        if int(values.get("physical_attempts") or 0) != sum(
            row["attempts"] for row in reviewed if row["engine"] == engine
        ):
            raise ResultAuditError(f"{engine} physical-attempt total contradicts task evidence")
        if int(values.get("result_final_passes") or 0) != final_counts[(engine, "pass")]:
            raise ResultAuditError(f"{engine} final-pass total contradicts task evidence")

    return {
        "schema": "lex_browser_use_result_audit/1",
        "status": "pass",
        "scope": "all final task/browser outcomes and all physical attempts",
        "logical_results_reviewed": len(reviewed),
        "physical_attempts_reviewed": physical_attempts,
        "final_passes": {
            engine: final_counts[(engine, "pass")] for engine in engines
        },
        "final_failures": {
            engine: final_counts[(engine, "fail")] for engine in engines
        },
        "checks": [
            "task scope and engine pairing",
            "declared retry policy",
            "all-attempt process status and BrowserTool trace evidence",
            "official evaluator status and score",
            "HAR and final-page evidence completeness",
            "result, combined, and published-status consistency",
            "published aggregate totals",
        ],
        "results": reviewed,
    }
