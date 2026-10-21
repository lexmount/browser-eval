"""Load model-scoped Browser Use failure-cause reviews from one layered source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

LAYERED_REVIEW_SCHEMA = "lex_browser_use_layered_failure_cause_review/2"
DEFAULT_REVIEW_PATH = (
    Path(__file__).parents[2] / "research/browser-use-failure-cause-review.json"
)


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def review_path(profile: dict[str, Any], *, authority_root: Path | None = None) -> Path:
    """Resolve an explicitly bound external review or a repository-relative review."""
    from lexbench_webfetch.paths import contained_path

    from harness.web_fetch.safety import unlinked_path
    value = (profile.get("failure_cause_review") or {}).get("source_path")
    if value is None:
        value = "research/browser-use-failure-cause-review.json"
    path = Path(value)
    if path.is_absolute():
        return unlinked_path(path)
    return contained_path(authority_root or Path(__file__).parents[2], value, reject_symlinks=True)


def load_failure_cause_review(path: Path = DEFAULT_REVIEW_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def extract_failure_cause_review_cohort(
    review: dict[str, Any], model_id: str
) -> dict[str, Any]:
    """Extract one model cohort from the authoritative layered review document."""

    if not review:
        return {}
    if review.get("schema") != LAYERED_REVIEW_SCHEMA:
        raise ValueError("unsupported Browser Use failure-cause review schema")
    cohorts = review.get("cohorts")
    if not isinstance(cohorts, dict):
        raise ValueError("layered failure-cause review must contain model cohorts")
    cohort = cohorts.get(model_id)
    if cohort is None:
        return {}
    if not isinstance(cohort, dict):
        raise ValueError(f"failure-cause review cohort must be an object: {model_id}")
    return cohort


def validate_failure_cause_review(
    review: dict[str, Any],
    *,
    model_id: str,
    attempts_sha256: str | None,
    failed_result_ids: set[str],
    require_complete: bool,
) -> dict[str, dict[str, Any]]:
    """Return verified causes for one model; reject stale or cross-model records."""

    result_prefix = f"{model_id}-"
    foreign_failures = sorted(
        result_id for result_id in failed_result_ids if not result_id.startswith(result_prefix)
    )
    if foreign_failures:
        raise ValueError(
            f"failed-result inventory includes another model for {model_id}: {foreign_failures}"
        )
    cohort = extract_failure_cause_review_cohort(review, model_id)
    if not cohort:
        if require_complete:
            raise ValueError(f"complete publication requires a failure-cause review: {model_id}")
        return {}

    bindings = cohort.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError(f"failure-cause review cohort has invalid bindings: {model_id}")
    bound_hash = bindings.get("attempts_sha256")
    if not attempts_sha256 or bound_hash != attempts_sha256:
        if require_complete:
            raise ValueError(
                f"failure-cause review is not bound to the current attempts: {model_id}"
            )
        return {}

    coverage = cohort.get("coverage")
    results = cohort.get("results")
    unresolved = cohort.get("unresolved")
    review_protocol = cohort.get("review_protocol")
    if not isinstance(coverage, dict):
        raise ValueError(f"failure-cause review cohort has invalid coverage: {model_id}")
    if not isinstance(results, dict):
        raise ValueError(f"failure-cause review cohort has invalid results: {model_id}")
    if not isinstance(unresolved, list):
        raise ValueError(f"failure-cause review cohort has invalid unresolved items: {model_id}")
    if not isinstance(review_protocol, dict):
        raise ValueError(f"failure-cause review cohort has invalid review protocol: {model_id}")

    verified: dict[str, dict[str, Any]] = {}
    for result_id, item in results.items():
        if not isinstance(result_id, str) or not result_id.startswith(result_prefix):
            raise ValueError(
                f"failure-cause review includes a result outside its model cohort: {result_id}"
            )
        if result_id not in failed_result_ids:
            raise ValueError(f"failure-cause review includes a non-failed result: {result_id}")
        if not isinstance(item, dict):
            raise ValueError(f"failure-cause review result must be an object: {result_id}")
        status = str(item.get("status") or "")
        category = item.get("subcategory") or {}
        reason = item.get("reason") or {}
        if status != "verified":
            if category or reason:
                raise ValueError(
                    "unverified failure cause must not contain a subcategory or reason: "
                    f"{result_id}"
                )
            continue
        if not isinstance(category, dict) or not all(
            category.get(language) for language in ("code", "zh", "en")
        ):
            raise ValueError(f"verified failure cause has an incomplete subcategory: {result_id}")
        if not isinstance(reason, dict) or not all(
            reason.get(language) for language in ("zh", "en")
        ):
            raise ValueError(f"verified failure cause has an incomplete reason: {result_id}")
        verified[result_id] = item

    if require_complete and set(verified) != failed_result_ids:
        missing = sorted(failed_result_ids - set(verified))
        raise ValueError(f"failure-cause review is incomplete for {model_id}: {missing}")
    if require_complete:
        expected_coverage = {
            "failed_results": len(failed_result_ids),
            "verified_results": len(verified),
        }
        if coverage != expected_coverage:
            raise ValueError(f"failure-cause review coverage is inconsistent: {model_id}")
        if unresolved:
            raise ValueError(f"failure-cause review contains unresolved items: {model_id}")
        if not cohort.get("reviewer_class") or not cohort.get("reviewed_at"):
            raise ValueError(
                f"failure-cause review must identify its reviewer class and date: {model_id}"
            )
    return verified


def localized_cause(
    verified: dict[str, dict[str, Any]], result_id: str, language: str = "zh"
) -> tuple[str, str]:
    """Return blank fields until a result has a verified, current review."""

    item = verified.get(result_id) or {}
    category = item.get("subcategory") or {}
    reason = item.get("reason") or {}
    return str(category.get(language) or ""), str(reason.get(language) or "")
