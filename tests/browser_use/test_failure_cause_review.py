import pytest

from harness.browser_use.failure_cause_review import (
    extract_failure_cause_review_cohort,
    localized_cause,
    validate_failure_cause_review,
)

MODEL_ID = "gpt-5.6-sol"
RESULT_ID = f"{MODEL_ID}-chromium-124"


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
        "schema": "lex_browser_use_layered_failure_cause_review/2",
        "benchmark": "WebArena-Verified 1.2.3",
        "cohorts": {
            MODEL_ID: {
                "bindings": {"attempts_sha256": "attempts-hash"},
                "coverage": {
                    "failed_results": 1,
                    "verified_results": 1 if status == "verified" else 0,
                },
                "unresolved": [],
                "results": {RESULT_ID: cause},
                "reviewer_class": "evidence analyst",
                "reviewed_at": "2026-09-10",
                "review_protocol": {"unit": "one logical result"},
            },
            "gpt-6-astra": {
                "bindings": {"attempts_sha256": "astra-hash"},
                "coverage": {"failed_results": 0, "verified_results": 0},
                "unresolved": [],
                "results": {},
                "reviewer_class": "evidence analyst",
                "reviewed_at": "2026-09-11",
                "review_protocol": {"unit": "one logical result"},
            },
        },
    }


def _validate(review: dict, *, require_complete: bool) -> dict:
    return validate_failure_cause_review(
        review,
        model_id=MODEL_ID,
        attempts_sha256="attempts-hash",
        failed_result_ids={RESULT_ID},
        require_complete=require_complete,
    )


def test_extracts_exact_model_cohort_with_its_metadata() -> None:
    cohort = extract_failure_cause_review_cohort(_review(), MODEL_ID)

    assert cohort["bindings"] == {"attempts_sha256": "attempts-hash"}
    assert cohort["coverage"] == {"failed_results": 1, "verified_results": 1}
    assert cohort["results"] == _review()["cohorts"][MODEL_ID]["results"]
    assert cohort["reviewer_class"] == "evidence analyst"
    assert cohort["review_protocol"] == {"unit": "one logical result"}


def test_unverified_cause_fields_remain_blank() -> None:
    verified = _validate(_review("pending"), require_complete=False)

    assert localized_cause(verified, RESULT_ID) == ("", "")


def test_verified_cause_is_available_per_model_browser_and_task() -> None:
    verified = _validate(_review(), require_complete=True)

    assert localized_cause(verified, RESULT_ID) == (
        "Agent 策略缺陷",
        "候选未核验。",
    )
    assert localized_cause(verified, "gpt-6-astra-chromium-124") == ("", "")


def test_missing_model_cohort_is_blank_for_draft_and_blocks_release() -> None:
    review = _review()

    assert (
        validate_failure_cause_review(
            review,
            model_id="missing-model",
            attempts_sha256="missing-hash",
            failed_result_ids={"missing-model-chromium-124"},
            require_complete=False,
        )
        == {}
    )
    with pytest.raises(ValueError, match="requires a failure-cause review"):
        validate_failure_cause_review(
            review,
            model_id="missing-model",
            attempts_sha256="missing-hash",
            failed_result_ids={"missing-model-chromium-124"},
            require_complete=True,
        )
    with pytest.raises(ValueError, match="requires a failure-cause review"):
        validate_failure_cause_review(
            review,
            model_id="missing-model",
            attempts_sha256="missing-hash",
            failed_result_ids=set(),
            require_complete=True,
        )


def test_stale_cohort_is_blank_for_draft_and_blocks_release() -> None:
    assert (
        validate_failure_cause_review(
            _review(),
            model_id=MODEL_ID,
            attempts_sha256="different-hash",
            failed_result_ids={RESULT_ID},
            require_complete=False,
        )
        == {}
    )
    with pytest.raises(ValueError, match="not bound"):
        validate_failure_cause_review(
            _review(),
            model_id=MODEL_ID,
            attempts_sha256="different-hash",
            failed_result_ids={RESULT_ID},
            require_complete=True,
        )


def test_release_requires_every_failed_result_and_rejects_passes() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        validate_failure_cause_review(
            _review(),
            model_id=MODEL_ID,
            attempts_sha256="attempts-hash",
            failed_result_ids={RESULT_ID, f"{MODEL_ID}-moli-124"},
            require_complete=True,
        )
    with pytest.raises(ValueError, match="non-failed"):
        validate_failure_cause_review(
            _review(),
            model_id=MODEL_ID,
            attempts_sha256="attempts-hash",
            failed_result_ids=set(),
            require_complete=False,
        )


def test_rejects_result_from_another_model_cohort() -> None:
    review = _review()
    review["cohorts"][MODEL_ID]["results"] = {
        "gpt-6-astra-chromium-124": review["cohorts"][MODEL_ID]["results"][RESULT_ID]
    }

    with pytest.raises(ValueError, match="outside its model cohort"):
        _validate(review, require_complete=False)


def test_rejects_failed_result_inventory_from_another_model() -> None:
    with pytest.raises(ValueError, match="inventory includes another model"):
        validate_failure_cause_review(
            _review(),
            model_id=MODEL_ID,
            attempts_sha256="attempts-hash",
            failed_result_ids={"gpt-6-astra-chromium-124"},
            require_complete=False,
        )


def test_current_cohort_requires_reviewer_protocol_metadata() -> None:
    review = _review()
    del review["cohorts"][MODEL_ID]["review_protocol"]

    with pytest.raises(ValueError, match="invalid review protocol"):
        _validate(review, require_complete=False)


def test_unverified_record_cannot_carry_diagnosis_text() -> None:
    review = _review("pending")
    review["cohorts"][MODEL_ID]["results"][RESULT_ID]["reason"] = {
        "zh": "尚未证明。",
        "en": "Unproven.",
    }
    with pytest.raises(ValueError, match="must not contain"):
        _validate(review, require_complete=False)


def test_schema_1_is_not_a_compatibility_input() -> None:
    legacy = {
        "schema": "lex_browser_use_failure_cause_review/1",
        "bindings": {"attempts_sha256": "attempts-hash"},
        "results": {},
    }

    with pytest.raises(ValueError, match="unsupported"):
        extract_failure_cause_review_cohort(legacy, MODEL_ID)
