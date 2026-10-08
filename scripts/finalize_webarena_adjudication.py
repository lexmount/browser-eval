"""Finalize complete WebArena adjudication from independently validated repair runs."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness.record_io import load_jsonl
from harness.trajectory_replay.public_webarena.qualification import (
    QUALIFICATION_SCHEMA,
    task_contract_sha256,
    validated_attempt_sha256s,
)


def _rows(path: Path) -> list[dict[str, Any]]:
    return load_jsonl(path, skip_blank=True)


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
        "source": task.get("source"),
    }


def qualification_from_applied_suite(suite: dict[str, Any]) -> dict[str, Any]:
    """Rehydrate adjudication state without requiring old private evidence paths."""
    decisions: list[dict[str, Any]] = []
    for task in suite.get("tasks") or []:
        qualification = deepcopy((task.get("ground_truth") or {}).get("qualification") or {})
        status = qualification.get("status")
        if status not in {"eligible", "skipped"}:
            raise ValueError(f"{task.get('id')}: applied suite has unresolved qualification")
        decisions.append(
            {
                "task_id": str(task["id"]),
                "disposition": "eligible" if status == "eligible" else "needs_trace_repair",
                "adjudication": qualification,
            }
        )
    return {
        "schema": QUALIFICATION_SCHEMA,
        "policy": {
            "qualification_browser": "upstream Chromium",
            "independent_passes_per_task": 2,
            "task_and_evaluator_are_immutable": True,
            "browser_comparison_results_must_not_be_used": True,
            "allowed_final_statuses": ["eligible", "skipped"],
        },
        "tasks": decisions,
    }


def _collect_validated_run(
    canonical_tasks: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    validated_suite: dict[str, Any],
    attempts_path: Path,
    candidates: dict[tuple[str, str], dict[str, Any]],
) -> None:
    validated_tasks = {str(task["id"]): task for task in validated_suite.get("tasks") or []}
    rows = _rows(attempts_path)
    identities = {
        (
            str(row.get("task_id")),
            str(row.get("run_id")),
            row.get("qualification_pass_index"),
        )
        for row in rows
    }
    if not rows or len(identities) != len(rows):
        raise ValueError(f"validated run has empty or duplicate attempt rows: {attempts_path}")
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
            or row.get("qualification_pass_index") not in {1, 2}
            or row.get("qualification_required_passes") != 2
        ):
            raise ValueError(f"{task_id}: validated run violates cold primary policy")
        if row.get("task_contract_sha256") != task_contract_sha256(_task_payload(task)):
            raise ValueError(f"{task_id}: validated run does not match task contract")
        evidence_directory = str(row.get("evidence_directory") or "").strip()
        if not evidence_directory:
            raise ValueError(f"{task_id}: validated run lacks evidence_directory")
        contract = task_contract_sha256(_task_payload(task))
        candidate = candidates.setdefault(
            (task_id, contract),
            {
                "task_id": task_id,
                "contract": contract,
                "task": deepcopy(task),
                "attempt_paths": [],
                "passing_paths": [],
            },
        )
        attempt_path = str(Path(evidence_directory) / "attempt.json")
        candidate["attempt_paths"].append(attempt_path)
        if all(
            row.get(key) == "pass"
            for key in (
                "process_status",
                "result_status",
                "evidence_status",
                "status",
                "combined_status",
            )
        ):
            candidate["passing_paths"].append(attempt_path)


def _overlay_two_pass_candidates(
    canonical_tasks: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    candidates: dict[tuple[str, str], dict[str, Any]],
) -> set[str]:
    overlaid: set[str] = set()
    for candidate in candidates.values():
        task_id = candidate["task_id"]
        attempt_paths = candidate["attempt_paths"]
        passing_paths = candidate["passing_paths"]
        if len(attempt_paths) > 2:
            raise ValueError(f"{task_id}: more than two validation attempts")
        if len(attempt_paths) != 2 or len(passing_paths) != 2:
            continue
        if task_id in overlaid:
            raise ValueError(f"{task_id}: multiple task contracts passed qualification")
        task = candidate["task"]
        contract = candidate["contract"]
        adjudication: dict[str, Any] = {
            "status": "eligible",
            "validated_attempt_paths": list(passing_paths),
        }
        # Validate the immutable attempt files, not only their JSONL projections.
        validated_attempt_sha256s(
            adjudication,
            task_id,
            expected_contract_sha256=contract,
        )
        original = canonical_tasks[task_id]
        reason = str(
            ((task.get("ground_truth") or {}).get("qualification") or {}).get("reason")
            or ((decisions[task_id].get("adjudication") or {}).get("reason"))
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
            or decisions[task_id].get("disposition")
            not in {"eligible", "needs_evidence_revalidation"}
        ):
            if not reason:
                raise ValueError(f"{task_id}: repaired validated pass lacks reason")
            adjudication["reason"] = reason
        decisions[task_id]["adjudication"] = adjudication
        overlaid.add(task_id)
    return overlaid


def overlay_validated_run(
    canonical_tasks: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    validated_suite: dict[str, Any],
    attempts_path: Path,
) -> None:
    """Compatibility wrapper requiring both passes in one attempts projection."""
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    _collect_validated_run(
        canonical_tasks,
        decisions,
        validated_suite,
        attempts_path,
        candidates,
    )
    _overlay_two_pass_candidates(canonical_tasks, decisions, candidates)


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
    # Preserve prior repair reasons while overlaying, but remember which
    # eligibility decisions must be replaced by fresh two-pass evidence.
    previous_eligible = {
        task_id
        for task_id, decision in decisions.items()
        if (decision.get("adjudication") or {}).get("status") == "eligible"
    }
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for validated_suite, attempts_path in validated_runs:
        _collect_validated_run(
            canonical_tasks,
            decisions,
            validated_suite,
            attempts_path,
            candidates,
        )
    overlaid = _overlay_two_pass_candidates(canonical_tasks, decisions, candidates)
    # A partial repair-validation batch is an incremental overlay. Only a task
    # that was actually present in fresh evidence may replace its prior
    # eligibility; otherwise an unrelated small batch would silently erase all
    # previously qualified tasks. A complete revalidation still has replacement
    # semantics because every task is present in ``candidates``.
    observed_task_ids = {task_id for task_id, _contract in candidates}
    for task_id in (previous_eligible & observed_task_ids) - overlaid:
        decisions[task_id]["adjudication"] = None
    # An explicit invalidation records later evidence and therefore wins over
    # every provisional or independently reproduced pass.
    for task_id, reason in invalidations.items():
        if not str(reason).strip():
            raise ValueError(f"{task_id}: invalidation reason is empty")
        decisions[task_id]["adjudication"] = {
            "status": "skipped",
            "reason": str(reason).strip(),
        }
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
    result["schema"] = QUALIFICATION_SCHEMA
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument(
        "--qualification",
        type=Path,
        help="prior private adjudication; omit to rehydrate from an already applied suite",
    )
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
        json.loads(args.invalidations.read_text(encoding="utf-8")) if args.invalidations else {}
    )
    if not isinstance(invalidations, dict):
        raise ValueError("invalidations must be a task-to-reason object")
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    qualification = (
        json.loads(args.qualification.read_text(encoding="utf-8"))
        if args.qualification
        else qualification_from_applied_suite(suite)
    )
    payload = finalize_adjudication(
        suite,
        qualification,
        runs,
        {str(key): str(value) for key, value in invalidations.items()},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
