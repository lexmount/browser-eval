import pytest

from harness.browser_use.failure_cause_review import (
    localized_cause,
    validate_failure_cause_review,
)


def _review(status: str = "verified") -> dict:
    cause = {
        "status": status,
        "subcategory": {
            "code": "agent_strategy",
            "zh": "Agent 策略缺陷",
            "en": "Agent strategy defect",
        },
        "reason": {"zh": "候选未核验。", "en": "Candidates were not verified."},
    }
    if status != "verified":
        cause = {"status": status}
    return {
        "schema": "lex_browser_use_failure_cause_review/1",
        "reviewed_at": "2026-09-10",
        "reviewer_class": "evidence analyst",
        "bindings": {"attempts_sha256": "attempts-hash"},
        "coverage": {
            "failed_results": 1,
            "verified_results": 1 if status == "verified" else 0,
        },
        "unresolved": [],
        "results": {"chromium-124": cause},
    }


def test_unverified_cause_fields_remain_blank() -> None:
    verified = validate_failure_cause_review(
        _review("pending"),
        attempts_sha256="attempts-hash",
        failed_result_ids={"chromium-124"},
        require_complete=False,
    )

    assert localized_cause(verified, "chromium-124") == ("", "")


def test_verified_cause_is_available_per_browser_and_task() -> None:
    verified = validate_failure_cause_review(
        _review(),
        attempts_sha256="attempts-hash",
        failed_result_ids={"chromium-124"},
        require_complete=True,
    )

    assert localized_cause(verified, "chromium-124") == (
        "Agent 策略缺陷",
        "候选未核验。",
    )
    assert localized_cause(verified, "moli-124") == ("", "")


def test_stale_review_is_blank_for_draft_and_blocks_release() -> None:
    assert (
        validate_failure_cause_review(
            _review(),
            attempts_sha256="different-hash",
            failed_result_ids={"chromium-124"},
            require_complete=False,
        )
        == {}
    )
    with pytest.raises(ValueError, match="not bound"):
        validate_failure_cause_review(
            _review(),
            attempts_sha256="different-hash",
            failed_result_ids={"chromium-124"},
            require_complete=True,
        )


def test_release_requires_every_failed_result_and_rejects_passes() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        validate_failure_cause_review(
            _review(),
            attempts_sha256="attempts-hash",
            failed_result_ids={"chromium-124", "moli-124"},
            require_complete=True,
        )
    with pytest.raises(ValueError, match="non-failed"):
        validate_failure_cause_review(
            _review(),
            attempts_sha256="attempts-hash",
            failed_result_ids=set(),
            require_complete=False,
        )


def test_unverified_record_cannot_carry_diagnosis_text() -> None:
    review = _review("pending")
    review["results"]["chromium-124"]["reason"] = {
        "zh": "尚未证明。",
        "en": "Unproven.",
    }
    with pytest.raises(ValueError, match="must not contain"):
        validate_failure_cause_review(
            review,
            attempts_sha256="attempts-hash",
            failed_result_ids={"chromium-124"},
            require_complete=False,
        )
