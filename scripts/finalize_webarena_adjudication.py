"""Finalize complete WebArena adjudication from independently validated repair runs."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.browser_use.qualification import task_contract_sha256


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _default_skip_reason(decision: dict[str, Any]) -> str:
    disposition = str(decision.get("disposition") or "")
    if disposition == "needs_response_annotation":
        return (
            "The official trajectory did not produce an evaluator-compatible answer, and no "
            "bounded DOM-observation repair passed independent Chromium qualification."
        )
    if disposition == "needs_evidence_revalidation":
        return (
            "The provisional result was not reproduced under the complete HAR and final-page "
            "evidence contract."
        )
    return (
        "The official trajectory failed isolated Chromium replay or conflicted with the task "
        "and evaluator; no bounded correction passed independent qualification."
    )


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "title": task["title"],
        "trajectory": task.get("trajectory"),
        "evaluator": task.get("evaluator"),
        "environment": task.get("environment"),
        "ground_truth": task.get("ground_truth"),
    }


def overlay_validated_run(
    canonical_tasks: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    validated_suite: dict[str, Any],
    attempts_path: Path,
) -> None:
    validated_tasks = {
        str(task["id"]): task for task in validated_suite.get("tasks") or []
    }
    rows = _rows(attempts_path)
    if not rows or len({str(row.get("task_id")) for row in rows}) != len(rows):
        raise ValueError(f"validated run has empty or duplicate task rows: {attempts_path}")
    for row in rows:
        task_id = str(row.get("task_id") or "")
        task = validated_tasks.get(task_id)
        if task is None or task_id not in canonical_tasks or task_id not in decisions:
            raise ValueError(f"validated run contains unknown task {task_id}")
        if (
            row.get("engine") != "chromium"
            or row.get("qualification_run") is not True
            or row.get("is_retry") is not False
            or row.get("attempt") != 1
        ):
            raise ValueError(f"{task_id}: validated run violates cold primary policy")
        if not all(
            row.get(key) == "pass"
            for key in ("process_status", "result_status", "evidence_status")
        ):
            continue
        if row.get("task_contract_sha256") != task_contract_sha256(_task_payload(task)):
            raise ValueError(f"{task_id}: validated run does not match task contract")
        evidence_directory = str(row.get("evidence_directory") or "").strip()
        if not evidence_directory:
            raise ValueError(f"{task_id}: validated pass lacks evidence_directory")
        original = canonical_tasks[task_id]
        adjudication: dict[str, Any] = {
            "status": "eligible",
            "validated_attempt_path": str(Path(evidence_directory) / "attempt.json"),
        }
        reason = str(
            ((task.get("ground_truth") or {}).get("qualification") or {}).get("reason")
            or ""
        ).strip()
        if task.get("trajectory") != original.get("trajectory"):
            adjudication["corrected_trajectory"] = deepcopy(task["trajectory"])
        validated_builder = (task.get("ground_truth") or {}).get("response_builder")
        original_builder = (original.get("ground_truth") or {}).get("response_builder")
        if validated_builder != original_builder:
            adjudication["response_builder"] = deepcopy(validated_builder)
        if (
            adjudication.get("corrected_trajectory") is not None
            or adjudication.get("response_builder") is not None
            or decisions[task_id].get("disposition") != "eligible"
        ):
            if not reason:
                raise ValueError(f"{task_id}: repaired validated pass lacks reason")
            adjudication["reason"] = reason
        decisions[task_id]["adjudication"] = adjudication


def finalize_adjudication(
    suite: dict[str, Any],
    qualification: dict[str, Any],
    validated_runs: list[tuple[dict[str, Any], Path]],
    invalidations: dict[str, str],
) -> dict[str, Any]:
    result = deepcopy(qualification)
    canonical_tasks = {str(task["id"]): task for task in suite.get("tasks") or []}
    decisions = {str(row["task_id"]): row for row in result.get("tasks") or []}
    if set(decisions) != set(canonical_tasks):
        raise ValueError("qualification task set does not match canonical suite")
    unknown_invalidations = set(invalidations) - set(decisions)
    if unknown_invalidations:
        raise ValueError(f"invalidations contain unknown tasks: {sorted(unknown_invalidations)}")
    for task_id, reason in invalidations.items():
        if not str(reason).strip():
            raise ValueError(f"{task_id}: invalidation reason is empty")
        decisions[task_id]["adjudication"] = {
            "status": "skipped",
            "reason": str(reason).strip(),
        }
    for validated_suite, attempts_path in validated_runs:
        overlay_validated_run(canonical_tasks, decisions, validated_suite, attempts_path)
    for decision in decisions.values():
        adjudication = decision.get("adjudication")
        if not isinstance(adjudication, dict) or adjudication.get("status") not in {
            "eligible",
            "skipped",
        }:
            decision["adjudication"] = {
                "status": "skipped",
                "reason": _default_skip_reason(decision),
            }
    result["tasks"] = [decisions[str(task["id"])] for task in suite["tasks"]]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument(
        "--validated-run",
        action="append",
        default=[],
        metavar="SUITE::ATTEMPTS",
    )
    parser.add_argument("--invalidations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs: list[tuple[dict[str, Any], Path]] = []
    for value in args.validated_run:
        parts = value.split("::", 1)
        if len(parts) != 2:
            raise ValueError("validated run must be SUITE::ATTEMPTS")
        suite_path, attempts_path = map(Path, parts)
        runs.append(
            (
                json.loads(suite_path.read_text(encoding="utf-8")),
                attempts_path,
            )
        )
    invalidations = (
        json.loads(args.invalidations.read_text(encoding="utf-8"))
        if args.invalidations
        else {}
    )
    if not isinstance(invalidations, dict):
        raise ValueError("invalidations must be a task-to-reason object")
    payload = finalize_adjudication(
        json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.qualification.read_text(encoding="utf-8")),
        runs,
        {str(key): str(value) for key, value in invalidations.items()},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
