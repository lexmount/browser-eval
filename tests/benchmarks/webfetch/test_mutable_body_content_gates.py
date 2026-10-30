"""Body identity must not override independently declared content requirements."""
from copy import deepcopy

import pytest

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt


def body_case():
    return {
        "id": "mutable-document", "url": "https://example.test/manual",
        "expected_any": [], "min_content_chars": 10,
        "required_facts": [{"id": "installation", "alternatives": ["Install the client"]}],
        "acceptance_contract": {
            "version": 1, "schema": "body_text", "minimum_records": 0,
            "stable_text_anchors": [{"id": "identity", "alternatives": ["Client manual"]}],
        },
    }


@pytest.mark.parametrize("answer,passed", [
    ("Client manual\nInstall the client to start retrieving pages.", True),
    ("Client manual\nUnrelated filler that exceeds the length floor.", False),
])
def test_body_identity_cannot_replace_required_facts(answer, passed):
    metrics = score_attempt(body_case(), answer.encode(), ok=True)
    assert metrics["stable_anchor_coverage"] == 1
    assert metrics["length_pass"]
    assert metrics["content_pass"] is passed
    assert metrics["stable_task_pass"] is passed
    assert task_passed(True, metrics) is passed
    assert ("required_facts_missing" in metrics["stable_task_errors"]) is not passed


@pytest.mark.parametrize("count,passed", [(5, True), (4, True), (3, False)])
def test_body_facts_preserve_the_declared_fraction(count, passed):
    case = body_case()
    case["required_facts"] = [
        {"id": str(i), "alternatives": [f"Section {i} explains its subject"]} for i in range(5)
    ]
    case["fact_coverage_threshold"] = 0.8
    answer = "Client manual\n" + "\n".join(f["alternatives"][0] for f in case["required_facts"][:count])
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is passed


@pytest.mark.parametrize("marker,passed", [("Install", True), ("Absent topic", False)])
def test_body_identity_preserves_expected_content_markers(marker, passed):
    case = body_case()
    case["expected_any"] = [marker]
    metrics = score_attempt(case, b"Client manual\nInstall the client.", ok=True)
    assert task_passed(True, metrics) is passed
    assert ("expected_content_missing" in metrics["stable_task_errors"]) is not passed


def test_body_length_floor_remains_independently_required():
    case = body_case()
    case["min_content_chars"] = 1000
    metrics = score_attempt(case, b"Client manual\nInstall the client.", ok=True)
    assert metrics["fact_coverage"] == 1
    assert not task_passed(True, metrics)
    assert "content_too_short" in metrics["stable_task_errors"]


def test_no_declared_facts_does_not_create_a_new_requirement():
    case = body_case()
    case.pop("required_facts")
    assert task_passed(True, score_attempt(case, b"Client manual", ok=True))


@pytest.mark.parametrize("answer,passed", [
    ("[A newly published item](https://example.test/item/7)", True),
    ("There are no records here.", False),
])
def test_structured_records_obey_declared_gates_until_explicitly_removed(answer, passed):
    case = deepcopy(body_case())
    case["task_kind"] = "structure"
    case["expected_any"] = ["A retired entry"]
    case["min_content_chars"] = 10000
    case["acceptance_contract"] = {
        "version": 1, "schema": "named_link", "minimum_records": 1,
        "hosts": ["example.test"], "entry_path": r"/item/\d+",
    }
    metrics = score_attempt(case, answer.encode(), ok=True)
    assert metrics["fact_coverage"] == 0
    assert not metrics["length_pass"]
    assert not task_passed(True, metrics)
    assert metrics['acceptance_checks']['facts'] is False
    assert metrics['acceptance_checks']['length'] is False
    case.pop('required_facts')
    case['expected_any'] = []
    case['min_content_chars'] = 1
    assert task_passed(True, score_attempt(case, answer.encode(), ok=True)) is passed


def test_document_metadata_does_not_supply_a_missing_body_fact():
    metrics = score_attempt(body_case(), b"Client manual\nOther prose.", ok=True,
                            title="Install the client")
    assert not task_passed(True, metrics)
