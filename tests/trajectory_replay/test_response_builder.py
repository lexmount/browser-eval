from __future__ import annotations

import pytest

from harness.trajectory_replay.response_builder import build_agent_response
from harness.trajectory_replay.suite import BrowserTask


def _task(builder):
    return BrowserTask(
        "webarena-1",
        "Task",
        (),
        (),
        (),
        "simple",
        {"response_builder": builder},
        {"url": "https://example.com"},
        {"kind": "webarena_verified", "task_id": 1, "revision": 2},
        {"kind": "webarena_verified"},
    )


def test_retrieve_response_is_built_only_from_observed_outputs():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "first"}, {"$output": "second"}],
            }
        ),
        {"first": "Alpha", "second": "Beta"},
    )
    assert response == {
        "task_type": "retrieve",
        "status": "SUCCESS",
        "retrieved_data": ["Alpha", "Beta"],
    }


def test_retrieve_response_cannot_claim_success_without_observation():
    with pytest.raises(ValueError, match="retrieve responses must be built"):
        build_agent_response(
            _task({"kind": "null_after_verified_path", "task_type": "retrieve"}),
            {},
        )


def test_response_builder_rejects_literal_answer_injection():
    with pytest.raises(ValueError, match="only output references"):
        build_agent_response(
            _task(
                {
                    "kind": "observed_shape",
                    "task_type": "retrieve",
                    "shape": ["expected answer"],
                }
            ),
            {},
        )


def test_response_builder_extracts_and_coerces_observed_number():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [
                    {
                        "title": {"$output": "title"},
                        "rating": {
                            "$output": "rating_id",
                            "regex": r"_(\d+)$",
                            "coerce": "number",
                        },
                    }
                ],
            }
        ),
        {"title": "Good but not perfect", "rating_id": "Rating_4"},
    )

    assert response is not None
    assert response["retrieved_data"] == [{"title": "Good but not perfect", "rating": 4}]


def test_response_builder_normalizes_observed_string_case():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [{"$output": "status", "case": "lower"}],
            }
        ),
        {"status": "Canceled"},
    )

    assert response is not None
    assert response["retrieved_data"] == ["canceled"]


@pytest.mark.parametrize(("observed", "expected"), (("Closed", True), ("Open", False)))
def test_response_builder_derives_closed_boolean_from_visible_issue_state(observed, expected):
    response = build_agent_response(
        _task(
            {
                "kind": "observed_shape",
                "task_type": "retrieve",
                "shape": [
                    {
                        "$output": "issue",
                        "regex": r"Issue state: (Open|Closed)",
                        "coerce": "closed_boolean",
                    }
                ],
            }
        ),
        {"issue": f"Issue state: {observed}"},
    )

    assert response is not None
    assert response["retrieved_data"] == [expected]


def test_response_builder_rejects_unmatched_or_unsupported_transforms():
    for output in (
        {"$output": "value", "regex": r"_(\d+)$", "coerce": "integer"},
        {"$output": "value", "coerce": "boolean"},
        {"$output": "value", "literal": "answer"},
    ):
        with pytest.raises(ValueError):
            build_agent_response(
                _task(
                    {
                        "kind": "observed_shape",
                        "task_type": "retrieve",
                        "shape": [output],
                    }
                ),
                {"value": "not-a-number"},
            )


def test_response_builder_concatenates_observed_json_arrays_in_declared_order():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_json_concat",
                "task_type": "retrieve",
                "outputs": ["page_two", "page_one"],
            }
        ),
        {"page_one": '["T. Gannon"]', "page_two": '["Rachel"]'},
    )

    assert response is not None
    assert response["retrieved_data"] == ["Rachel", "T. Gannon"]


def test_response_builder_concat_rejects_non_array_output():
    with pytest.raises(ValueError, match="JSON arrays"):
        build_agent_response(
            _task(
                {
                    "kind": "observed_json_concat",
                    "task_type": "retrieve",
                    "outputs": ["value"],
                }
            ),
            {"value": '"not-an-array"'},
        )


def test_response_builder_zips_observed_columns_without_literal_values():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_json_zip",
                "task_type": "retrieve",
                "fields": {"name": "names", "email": "emails"},
            }
        ),
        {
            "names": '["Ada Lovelace", "Grace Hopper"]',
            "emails": '["ada@example.test", "grace@example.test"]',
        },
    )

    assert response is not None
    assert response["retrieved_data"] == [
        {"name": "Ada Lovelace", "email": "ada@example.test"},
        {"name": "Grace Hopper", "email": "grace@example.test"},
    ]


def test_response_builder_zip_fails_closed_on_unequal_or_non_array_columns():
    builder = {
        "kind": "observed_json_zip",
        "task_type": "retrieve",
        "fields": {"name": "names", "email": "emails"},
    }
    with pytest.raises(ValueError, match="equal lengths"):
        build_agent_response(
            _task(builder),
            {"names": '["Ada", "Grace"]', "emails": '["ada@example.test"]'},
        )
    with pytest.raises(ValueError, match="JSON arrays"):
        build_agent_response(
            _task(builder),
            {"names": '"Ada"', "emails": '["ada@example.test"]'},
        )


def test_response_builder_filters_aligned_observed_rows_by_requested_value():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_json_filter_zip",
                "task_type": "retrieve",
                "fields": {"name": "names", "email": "emails"},
                "filter_output": "phones",
                "filter_regex": r"^8015551212$",
            }
        ),
        {
            "names": '["Ada", "Grace"]',
            "emails": '["ada@example.test", "grace@example.test"]',
            "phones": '["8015551212", "2125550100"]',
        },
    )

    assert response is not None
    assert response["retrieved_data"] == [
        {"name": "Ada", "email": "ada@example.test"}
    ]


def test_response_builder_ranks_observed_labels_by_numeric_score():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_ranked_labels",
                "task_type": "retrieve",
                "labels_output": "labels",
                "scores_output": "uses",
                "limit": 3,
            }
        ),
        {
            "labels": '["third", "first", "second", "fourth"]',
            "uses": '["30", "100 uses", "55", "2"]',
        },
    )

    assert response is not None
    assert response["retrieved_data"] == ["first", "second", "third"]


def test_response_builder_extracts_authors_only_from_matching_observed_reviews():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_review_authors_matching",
                "task_type": "retrieve",
                "output": "reviews",
                "match_regex": r"quality|poorly made",
                "author_regex": r"Review by\s+([^\n]+)",
            }
        ),
        {
            "reviews": '["Review by Ada\\nPoorly made shell", '
            '"Review by Grace\\nExcellent fit", '
            '"Review by Ada\\nLow quality buttons"]'
        },
    )

    assert response is not None
    assert response["retrieved_data"] == ["Ada"]


def test_response_builder_deduplicates_review_authors_across_observed_pages():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_review_authors_matching",
                "task_type": "retrieve",
                "outputs": ["page_two", "page_one"],
                "match_regex": r"quality",
                "author_regex": r"Review by\s+([^\n]+)",
            }
        ),
        {
            "page_two": '["Review by Ada\\nLow quality"]',
            "page_one": '["Review by Grace\\nQuality issue", '
            '"Review by Ada\\nQuality issue"]',
        },
    )

    assert response is not None
    assert response["retrieved_data"] == ["Ada", "Grace"]


def test_response_builder_review_matching_fails_closed_without_observed_match():
    with pytest.raises(ValueError, match="no observed reviews matched"):
        build_agent_response(
            _task(
                {
                    "kind": "observed_review_authors_matching",
                    "task_type": "retrieve",
                    "output": "reviews",
                    "match_regex": r"quality",
                    "author_regex": r"Review by\s+([^\n]+)",
                }
            ),
            {"reviews": '["Review by Ada\\nExcellent fit"]'},
        )


def test_response_builder_computes_currency_range_from_observed_pages():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_currency_range",
                "task_type": "retrieve",
                "outputs": ["page_three", "page_two", "page_one"],
            }
        ),
        {
            "page_one": '["$1.46", "$9.99"]',
            "page_two": '["$34.50"]',
            "page_three": '["$179.99", "$1,002.00"]',
        },
    )

    assert response is not None
    assert response["retrieved_data"] == [{"min": 1.46, "max": 1002}]


def test_response_builder_reports_not_found_only_from_explicit_observed_evidence():
    response = build_agent_response(
        _task(
            {
                "kind": "observed_not_found",
                "task_type": "retrieve",
                "output": "page",
                "evidence_regex": r"no (?:items|records) found",
            }
        ),
        {"page": "Search complete: No records found."},
    )

    assert response == {
        "task_type": "retrieve",
        "status": "NOT_FOUND_ERROR",
        "retrieved_data": None,
    }


@pytest.mark.parametrize(
    ("builder", "outputs"),
    (
        (
            {
                "kind": "observed_not_found",
                "task_type": "retrieve",
                "output": "page",
                "evidence_regex": r"no records found",
            },
            {"page": "The result list is empty."},
        ),
        (
            {
                "kind": "observed_not_found",
                "task_type": "retrieve",
                "output": "page",
                "evidence_regex": "",
            },
            {"page": "No records found."},
        ),
    ),
)
def test_response_builder_not_found_fails_closed_without_matching_evidence(builder, outputs):
    with pytest.raises(ValueError, match="observed_not_found"):
        build_agent_response(_task(builder), outputs)
