"""Multi-value field evidence must agree with the public benchmark scorer."""

import pytest

from harness.web_extract.analysis import record_field_results
from harness.web_extract.swde import score


@pytest.mark.parametrize(
    "gold,actual,status,hit,exact",
    [
        (["A"], ["A"], "correct", True, True),
        (["A", "B"], ["A"], "partial", True, False),
        (["A"], ["A", "C"], "partial", True, False),
        (["A"], [], "missing", False, False),
        ([], ["C"], "unexpected", False, False),
        (["A"], ["C"], "wrong", False, False),
        (["A"], "A", "invalid", False, False),
        (["A"], ["A", 1], "invalid", True, False),
        (["A"], ["A", "A"], "partial", True, False),
        ([], [], "correct", False, True),
        *[
            (gold, actual, "invalid", False, False)
            for gold in [[], ["A"]]
            for actual in [None, False, 0, "", {}]
        ],
    ],
)
def test_record_diagnosis_preserves_official_hit_and_micro_counts(gold, actual, status, hit, exact):
    reference = {"domain": "arbitrary", "site": "source", "values": {"field": gold}}
    row = {"http_status": 200, "normalized_fields": {"field": actual}}
    result = record_field_results(reference, row)["field"]
    scores = score({"case": reference}, {"case": {"field": actual}})
    assert (result["status"], result["hit"], result["exact"]) == (status, hit, exact)
    assert scores["site_fields"][0]["hit_pages"] == int(hit)
    assert scores["exact_field_sets"]["correct"] == int(exact)
    assert [result[key] for key in ("tp", "fp", "fn")] == [
        scores["value_micro"][key] for key in ("true_positive", "false_positive", "false_negative")
    ]


def test_failed_call_retains_gold_as_missing_even_if_error_body_has_values():
    result = record_field_results(
        {"values": {"field": ["A"]}}, {"http_status": 500, "normalized_fields": {"field": ["A"]}}
    )["field"]
    assert result["status"] == "missing"
    assert result["fn"] == 1


def test_partial_cohort_has_progress_and_timers_but_no_quality_ranking():
    from harness.web_extract.analysis import record_statistics

    truth = {
        str(i): {"domain": "domain", "site": "site", "values": {"field": ["A"]}} for i in range(3)
    }
    rows = [
        {
            "case_id": "0",
            "http_status": 200,
            "normalized_fields": {"field": ["A"]},
            "compatibility": "structured_output",
            "elapsed_seconds": 2,
        }
    ]
    result = record_statistics(
        truth,
        rows,
        certified=False,
        scorer=lambda *args: pytest.fail("An incomplete method was ranked"),
    )
    assert result["scores"] is None
    assert result["unexecuted"] == 2
    assert result["latency"] == {"timed": 1, "missing": 0, "mean": 2, "p50": 2, "p95": 2}


def test_failed_calls_stay_in_complete_scores_and_latency_missing_is_not_zero():
    from harness.web_extract.analysis import record_statistics

    truth = {
        str(i): {"domain": "domain", "site": "site", "values": {"field": ["A"]}} for i in range(2)
    }
    rows = [
        {
            "case_id": "0",
            "http_status": 200,
            "normalized_fields": {"field": ["A"]},
            "compatibility": "structured_output",
            "elapsed_seconds": 2,
        },
        {
            "case_id": "1",
            "http_status": None,
            "normalized_fields": None,
            "elapsed_seconds": None,
            "state": "call_error",
        },
    ]
    result = record_statistics(truth, rows, certified=True, scorer=score)
    assert result["scores"]["value_micro"]["recall"] == 0.5
    assert result["latency"]["mean"] == 2
    assert result["latency"]["missing"] == 1
    assert result["failure_stages"]["client_transport"] == 1


@pytest.mark.parametrize("prediction", [{}, {"field": []}])
def test_absent_and_empty_array_fields_do_not_count_as_invalid_predictions(prediction):
    reference = {"domain": "arbitrary", "site": "source", "values": {"field": []}}
    result = record_field_results(reference, {"http_status": 200, "normalized_fields": prediction})[
        "field"
    ]
    assert result["fp"] == 0
    assert result["status"] == "correct"
    assert score({"case": reference}, {"case": prediction})["value_micro"]["false_positive"] == 0
