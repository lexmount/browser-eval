"""Validate evidence-bound root-cause review for trajectory replay results."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .participants import bind_participant, participant_registry, resolve_participant_id

REVIEW_SCHEMA = "lex_webarena_trajectory_replay_failure_review/1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def programmatic_failure(attempt: dict[str, Any]) -> dict[str, Any]:
    """Return the automatic failure location without asserting a root cause."""

    if attempt.get("process_status") != "pass":
        message = str(attempt.get("error") or "BrowserTool execution failed")
        match = re.search(r"RuntimeError: step (\d+) failed: ([^\n]+)", message)
        return {
            "stage": "browser_tool_execution",
            "step": int(match.group(1)) if match else None,
            "message": match.group(2) if match else message.strip(),
        }
    if attempt.get("evaluator_status") != "pass":
        return {
            "stage": "official_evaluation",
            "step": None,
            "message": "Official evaluator rejected the completed trajectory",
        }
    return {"stage": "pass", "step": None, "message": ""}


def load_validated_failure_review(
    results_path: str | Path, review_path: str | Path,
    *, methods: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load a review only when it exactly covers the bound final failures."""

    results_file = Path(results_path).expanduser().resolve()
    review_file = Path(review_path).expanduser().resolve()
    results = json.loads(results_file.read_text(encoding="utf-8"))
    registry = participant_registry(methods or results.get("methods") or [
        {"id": engine, "engine": engine} for engine in results.get("engines", [])
    ])
    review = json.loads(review_file.read_text(encoding="utf-8"))
    if review.get("schema") != REVIEW_SCHEMA:
        raise ValueError("unsupported trajectory replay failure review")
    if (review.get("bindings") or {}).get("effective_results_sha256") != _sha256(
        results_file
    ):
        raise ValueError("failure review is not bound to the effective result bytes")

    attempts = [bind_participant(row, registry) for row in results.get("attempts") or []]
    failed = {
        (item["participant_id"], int(item["task_id"]))
        for item in attempts
        if item.get("status") != "pass"
    }
    reviewed: dict[tuple[str, int], dict[str, Any]] = {}
    revisions = {item["id"]: item for item in review.get("review_revisions") or []}
    for group in review.get("cause_groups") or []:
        required_group_fields = {
            "code",
            "label_zh",
            "label_en",
            "responsibility",
            "remediation_owner",
            "remediation_status",
            "results",
        }
        if required_group_fields - set(group):
            raise ValueError("failure review cause group is incomplete")
        for item in group["results"]:
            key = (resolve_participant_id(item, registry), int(item["task_id"]))
            if key in reviewed:
                raise ValueError(f"duplicate failure review result: {key}")
            if not str(item.get("reason_zh") or "").strip() or not str(
                item.get("reason_en") or ""
            ).strip():
                raise ValueError(f"failure review reason is incomplete: {key}")
            reviewed[key] = {**bind_participant(item, registry), "cause_group": group}
            if item.get("supplemental_diagnostic"):
                ref = item["supplemental_diagnostic"]
                source = Path(ref["path"])
                if (not source.is_absolute() or not source.is_file()
                        or any(part.is_symlink() for part in (source, *source.parents))
                        or _sha256(source) != ref.get("sha256")):
                    raise ValueError("supplemental diagnostic evidence is missing or changed")
                diagnostic = json.loads(source.read_text(encoding="utf-8"))
                if (diagnostic.get("task_id") != key[1]
                        or diagnostic.get("cohort_results_sha256") != review["bindings"]["effective_results_sha256"]):
                    raise ValueError("supplemental diagnostic is not bound to the reviewed cohort")
                binaries = {attempt.get("binary_sha256") for attempt in attempts if int(attempt["task_id"]) == key[1]}
                if not set(diagnostic.get("binary_sha256", {}).values()).issubset(binaries):
                    raise ValueError("supplemental diagnostic browser identity differs")
            if item.get("review_revision"):
                revision = revisions.get(item["review_revision"])
                if (
                    revision is None
                    or key not in {
                        (resolve_participant_id(row, registry), int(row["task_id"]))
                        for row in revision.get("scope", [])
                    }
                    or revision.get("evidence_bindings", {}).get("effective_results_sha256")
                    != review["bindings"]["effective_results_sha256"]
                ):
                    raise ValueError(f"failure review revision is not evidence-bound: {key}")
                reviewed[key]["revision_evidence"] = revision

    if set(reviewed) != failed:
        missing = sorted(failed - set(reviewed))
        extra = sorted(set(reviewed) - failed)
        raise ValueError(f"failure review coverage mismatch: missing={missing}, extra={extra}")
    coverage = review.get("coverage") or {}
    if coverage != {"failed_results": len(failed), "verified_results": len(reviewed)}:
        raise ValueError("failure review coverage summary is incorrect")
    return {"review": review, "results": results, "by_result": reviewed}
