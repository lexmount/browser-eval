from scripts.run_dynamic_empty_output_diagnostics import diagnostic_record


def test_diagnostic_record_is_non_scoring_and_links_original_attempt():
    record = {"attempt_id": "diag", "ok": True}
    parent = {"attempt_id": "original", "run_id": "run-original"}

    annotated = diagnostic_record(record, parent)

    assert annotated["diagnostic_only"] is True
    assert annotated["score_bearing"] is False
    assert annotated["corresponding_original_attempt_id"] == "original"
    assert annotated["corresponding_original_run_id"] == "run-original"
    assert annotated["diagnostic_interpretation"] == "not_reproduced"


def test_failed_diagnostic_never_claims_a_confirmed_root_cause():
    annotated = diagnostic_record(
        {"attempt_id": "diag", "ok": False, "error_type": "empty_output"},
        {"attempt_id": "original"},
    )

    assert annotated["diagnostic_interpretation"] == "reproduced_or_other_failure"
