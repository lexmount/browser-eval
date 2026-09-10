"""Load hash-bound, per-result Browser Use failure-cause reviews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_REVIEW_PATH = (
    Path(__file__).parents[2] / "research/browser-use-failure-cause-review.json"
)


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def load_failure_cause_review(path: Path = DEFAULT_REVIEW_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def validate_failure_cause_review(
    review: dict[str, Any],
    *,
    attempts_sha256: str | None,
    failed_result_ids: set[str],
    require_complete: bool,
) -> dict[str, dict[str, Any]]:
    """Return only verified causes; never promote draft or stale diagnoses."""

    if not review:
        if require_complete and failed_result_ids:
            raise ValueError("complete publication requires a failure-cause review")
        return {}
    if review.get("schema") != "lex_browser_use_failure_cause_review/1":
        raise ValueError("unsupported Browser Use failure-cause review schema")
    bound_hash = (review.get("bindings") or {}).get("attempts_sha256")
    if not attempts_sha256 or bound_hash != attempts_sha256:
        if require_complete:
            raise ValueError("failure-cause review is not bound to the current attempts")
        return {}

    results = review.get("results") or {}
    verified: dict[str, dict[str, Any]] = {}
    for result_id, item in results.items():
        if result_id not in failed_result_ids:
            raise ValueError(f"failure-cause review includes a non-failed result: {result_id}")
        status = str(item.get("status") or "")
        category = item.get("subcategory") or {}
        reason = item.get("reason") or {}
        if status != "verified":
            if category or reason:
                raise ValueError(
                    f"unverified failure cause must not contain a subcategory or reason: {result_id}"
                )
            continue
        if not all(category.get(language) for language in ("code", "zh", "en")):
            raise ValueError(f"verified failure cause has an incomplete subcategory: {result_id}")
        if not all(reason.get(language) for language in ("zh", "en")):
            raise ValueError(f"verified failure cause has an incomplete reason: {result_id}")
        verified[result_id] = item

    if require_complete and set(verified) != failed_result_ids:
        missing = sorted(failed_result_ids - set(verified))
        raise ValueError(f"failure-cause review is incomplete: {missing}")
    if require_complete:
        expected_coverage = {
            "failed_results": len(failed_result_ids),
            "verified_results": len(verified),
        }
        if review.get("coverage") != expected_coverage:
            raise ValueError("failure-cause review coverage is inconsistent")
        if review.get("unresolved"):
            raise ValueError("failure-cause review contains unresolved items")
        if not review.get("reviewer_class") or not review.get("reviewed_at"):
            raise ValueError("failure-cause review must identify its reviewer class and date")
    return verified


def localized_cause(
    verified: dict[str, dict[str, Any]], result_id: str, language: str = "zh"
) -> tuple[str, str]:
    """Return blank fields until a result has a verified, current review."""

    item = verified.get(result_id) or {}
    category = item.get("subcategory") or {}
    reason = item.get("reason") or {}
    return str(category.get(language) or ""), str(reason.get(language) or "")
