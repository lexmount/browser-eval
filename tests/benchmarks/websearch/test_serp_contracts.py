"""Shared contracts must preserve saved SERP evidence across package migration."""

import pytest

from lexbench_websearch.answer_rules import reference_rule
from lexbench_websearch.inputs import registry, resolve_profile
from lexbench_websearch.relevance import candidate_packet


def test_composed_datasets_preserve_order_and_reject_duplicate_or_changed_inputs():
    bindings = [
        {"id": key, "sha256": registry()["datasets"][key]["sha256"]}
        for key in ("scenarios-v1", "factual-v1")
    ]
    profile = {"cases": {"field": "cases", "benchmark_datasets": bindings}}
    resolved = resolve_profile(profile)
    assert len(resolved["cases"]) == 980
    assert len({c["id"] for c in resolved["cases"]}) == 980
    for altered in (bindings + bindings[:1], [{**bindings[0], "sha256": "0" * 64}]):
        with pytest.raises(ValueError):
            resolve_profile(
                {"cases": {"field": "cases", "benchmark_datasets": altered}}
            )


@pytest.mark.parametrize(
    "reference,answer",
    [
        ("42[7]", "42"),
        ("Adahttps://source.test/page", "Ada"),
        ("https://source.test/page", "https://source.test/page"),
    ],
)
def test_citation_cleanup_preserves_original_answers_and_url_answers(reference, answer):
    derivation = (
        None
        if reference == answer
        else {
            "reference": reference,
            "derived_answer": answer,
            "kind": "terminal-citation-metadata",
            "qualification": "Independent source confirms answer without citation",
            "sources": [{"url": "https://source.test/", "excerpt": answer}],
        }
    )
    rule = reference_rule(reference, derivation=derivation)
    assert rule["reference"] == reference
    assert rule["alternatives"] == [answer]


def test_serp_packet_preserves_optional_native_text_and_explicit_source_scope():
    case = {
        "id": "official",
        "family": "web",
        "query": "Official documentation",
        "domains": ["diagnostic.test"],
    }
    profile = {"cases": [case], "methods": [{"id": "a"}], "protocol": {"top_k": 5}}
    rows = [
        {
            "case_id": "official",
            "method": "a",
            "results": [
                {"url": "https://official.test/manual", "title": None, "snippet": None}
            ],
        }
    ]
    packet, _ = candidate_packet(
        profile,
        rows,
        {"official": {"domains": ["official.test"]}},
        packet_format="serp/1",
    )
    candidate = packet["blind_candidate_packets"][0]["candidates"][0]
    assert candidate["source_eligible"]
    assert candidate["previews"] == [{"title": "", "snippet": ""}]
    assert packet["blind_candidate_packets"][0]["required_domains"] == []


@pytest.mark.parametrize(
    "reference,text,expected",
    [
        ("v[0]", "v[0]", True),
        ("v[0]", "v[1]", False),
        ("v[0]", "v", False),
        ("tensor[12]", "tensor[13]", False),
        ("42[7]", "42", False),
        ("Adahttps://source.test/page", "Ada", False),
    ],
)
def test_unannotated_answers_keep_literal_brackets_and_urls(reference, text, expected):
    from lexbench_websearch.scoring import evaluate, task_acceptance

    case = {"reference_answer": reference, "answer_match_rule": "published-answers/2"}
    results = [{"url": "https://source.test/result", "snippet": text}]
    row = {"results": results, "metrics": evaluate(case, results)}
    assert bool(row["metrics"]["answer_in_preview_5"]) is expected
    assert task_acceptance(case, row, "answer") is expected


def test_citation_derivation_requires_matching_reference_and_qualified_source():
    good = {
        "reference": "42[7]",
        "derived_answer": "42",
        "kind": "terminal-citation-metadata",
        "qualification": "Source confirms the answer",
        "sources": [{"url": "https://source.test/", "excerpt": "42"}],
    }
    assert reference_rule("42[7]", derivation=good)["alternatives"] == ["42"]
    for change in (
        {"reference": "other[7]"},
        {"derived_answer": "43"},
        {"sources": []},
        {"qualification": ""},
        {"sources": [{"url": "https://source.test/", "excerpt": "43"}]},
    ):
        with pytest.raises(ValueError, match="derivation"):
            reference_rule("42[7]", derivation={**good, **change})


@pytest.mark.parametrize(
    "answer,excerpt,accepted",
    [
        ("42", "The value is 42.", True),
        ("42", "The value is 142.", False),
        ("150", "The value is 150.", True),
        ("150", "The value is 150%.", False),
        ("White", "The stripe is White.", True),
        ("White", "Whitehead wrote this.", False),
    ],
)
def test_derivation_sources_support_complete_answer_phrases(answer, excerpt, accepted):
    from lexbench_websearch.contracts import validate_cases

    case = {
        "id": "source",
        "reference_answer": answer + "[7]",
        "answer_derivation": {
            "reference": answer + "[7]",
            "derived_answer": answer,
            "kind": "terminal-citation-metadata",
            "qualification": "Independently observed source",
            "sources": [{"url": "https://source.test/", "excerpt": excerpt}],
        },
    }
    if accepted:
        validate_cases([case])
    else:
        with pytest.raises(ValueError, match="derivation"):
            validate_cases([case])
