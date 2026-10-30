"""Public task acceptance uses the selected cutoff and explicit quality scope."""

import pytest

from lexbench_websearch.scoring import task_acceptance, classify, evaluate
from lexbench_websearch.relevance import query_metrics
from lexbench_websearch.qualification import _qualified
from lexbench_websearch.contracts import validate_cases


@pytest.mark.parametrize("cutoff", [5, 10])
@pytest.mark.parametrize("metric", ["answer", "source", "site"])
def test_last_included_result_passes_and_first_excluded_result_fails(cutoff, metric):
    case = {"id": "probe", "family": "site", "domains": ["wanted.test"]}
    if metric == "answer":
        case.update(
            reference_answer="Ada Lovelace", reference_urls=["https://wanted.test/a"]
        )
    elif metric == "source":
        case["anchors"] = ["https://wanted.test/a"]
    blank = {"url": "https://other.test/b", "snippet": "Other text"}
    hit = {"url": "https://wanted.test/a", "snippet": "Ada Lovelace"}
    for position, expected in [(cutoff - 1, True), (cutoff, False)]:
        results = [dict(blank) for _ in range(cutoff + 1)]
        results[position] = hit
        row = {"results": results, "metrics": evaluate(case, results)}
        assert task_acceptance(case, row, metric, cutoff=cutoff) is expected
        assert (
            task_acceptance(case, row, metric, cutoff=cutoff, quality_excluded=True)
            is None
        )


def test_partial_relevance_passes_but_direct_match_metric_requires_top_grade():
    row = {"results": [{"url": "https://example.test/"}]}
    for grade, expected in [(0, False), (1, False), (2, True), (3, True)]:
        assert task_acceptance({}, row, relevance_grades=[grade]) is expected
        assert query_metrics(["a"], {"a": grade})["success"] == int(grade == 3)
    assert classify({}, row, quality_excluded=True) == ("probe", "quality_excluded")


@pytest.mark.parametrize("excluded", [False, True])
def test_reference_exclusion_preserves_delivery_failures(excluded):
    populated = {"results": [{"url": "https://example.test/"}]}
    for change in ({"http_status": 500}, {"error": "Timeout"}):
        assert classify({}, {**populated, **change}, quality_excluded=excluded) == (
            "fail",
            "api_error",
        )
    assert classify({}, {"results": []}, quality_excluded=excluded) == ("fail", "empty")
    if excluded:
        assert classify({}, populated, quality_excluded=True) == (
            "probe",
            "quality_excluded",
        )


def test_benchmark_answer_mirror_does_not_pass_factual_acceptance():
    case = {"query": "Who wrote the algorithm?", "reference_answer": "Ada Lovelace"}
    for title, expected in [
        ("Historical archive", True),
        ("SimpleQA answer archive", False),
    ]:
        row = {
            "results": [
                {
                    "url": "https://example.test/a",
                    "title": title,
                    "snippet": "Ada Lovelace",
                }
            ]
        }
        assert task_acceptance(case, row, "answer") is expected


def test_reference_qualification_and_alias_targets_are_enforced():
    source = {
        "url": "http://example.test/a",
        "title": "A",
        "excerpt": "A",
        "verified_at": "2026-01-01",
    }
    assert not _qualified(
        {"anchors": [source["url"]], "sources": [source], "qualification": "Observed"}
    )
    with pytest.raises(ValueError, match="existing target"):
        validate_cases(
            [
                {
                    "id": "a",
                    "anchors": ["https://example.test/a"],
                    "identity_aliases": {
                        "https://example.test/alias": "https://example.test/b"
                    },
                }
            ]
        )
