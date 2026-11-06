"""Nested JSON reviews must cover all retained results and cite the frozen source."""

import copy

import pytest

from harness.web_extract.pipeline import save_json
from harness.web_extract.review import (
    json_leaf_paths,
    review_schema,
    validate,
)


@pytest.fixture
def audit(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("Alice: red mug, 12 EUR. Bob: blue mug, 15 EUR.\n")
    reference = {"items": [{"name": "Alice", "price": 12}, {"name": "Bob", "price": 15}]}
    native = {"items": [{"name": "Bob", "price": 15}, {"name": "Alice", "price": 12}]}
    item = {
        "audit_kind": "schema-json-audit/1", "case_id": "one",
        "source_outline": str(source), "source_html": str(source),
        "reference": reference, "reference_leaf_paths": json_leaf_paths(reference),
        "reference_field_inventory": ["items"], "outputs": [{
            "method": "native", "leaf_paths": json_leaf_paths(native),
            "field_inventory": ["items"],
        }],
    }
    group = {"field": "items", "status": "supported", "source_quote": "Alice: red mug, 12 EUR.",
             "reason": "Both named records and their associated prices are supported.",
             "findings": []}
    result = {"cases": [{"case_id": "one", "reference": {
        "checked_paths": item["reference_leaf_paths"], "fields": [group], "summary": "Supported",
    }, "outputs": [{"method": "native", "verdict": "correct", "summary": "Reordered records",
        "checked_paths": item["outputs"][0]["leaf_paths"], "fields": [copy.deepcopy(group)]}]}]}
    return {"cases": [item]}, result


def test_nested_leaf_paths_preserve_empty_values_and_escaped_keys():
    assert json_leaf_paths({"a/b": [None, {}, []], "~": False}) == [
        "/a~1b/0", "/a~1b/1", "/a~1b/2", "/~0",
    ]


def test_complete_source_grounded_review_is_accepted(audit):
    packet, result = audit
    assert review_schema(packet["cases"])["properties"]["cases"]
    assert validate(result, packet) == result


@pytest.mark.parametrize("change", ["missing_method", "missing_leaf", "duplicate_leaf",
                                    "missing_field", "invented_quote"])
def test_incomplete_or_ungrounded_reviews_fail_closed(audit, change):
    packet, result = audit
    value = copy.deepcopy(result)
    output = value["cases"][0]["outputs"][0]
    if change == "missing_method":
        value["cases"][0]["outputs"] = []
    elif change == "missing_leaf":
        output["checked_paths"].pop()
    elif change == "duplicate_leaf":
        output["checked_paths"][1] = output["checked_paths"][0]
    elif change == "missing_field":
        output["fields"] = []
    else:
        output["fields"][0]["source_quote"] = "The source never said this"
    with pytest.raises(ValueError):
        validate(value, packet)


def test_packet_persistence_does_not_embed_machine_scores(tmp_path, audit):
    from harness.web_extract.review import read_packet, save_packet

    packet, _ = audit
    save_json(tmp_path / "manifest.json", {"root": "."})
    path = tmp_path / "packets/one.json"
    save_packet(path, packet, tmp_path)
    restored = read_packet(path)
    assert restored == packet
    assert "value_score" not in path.read_text()


def test_reconciliation_recovers_only_unambiguous_structure_and_literal_text(audit):
    from harness.web_extract.review import reconcile_citations

    packet, result = audit
    result = copy.deepcopy(result)
    original = copy.deepcopy(result)
    output = result['cases'][0]['outputs'][0]
    output['checked_paths'][0] = '/0/name'
    output['fields'][0]['source_quote'] = 'Alice:  red mug, 12 EUR.'
    restored = reconcile_citations(result, packet)
    assert validate(restored, packet) == original
    assert output['checked_paths'][0] == '/0/name'  # Raw review remains untouched.
    output['checked_paths'][0] = '/name'
    with pytest.raises(ValueError, match='leaf path'):
        validate(reconcile_citations(result, packet), packet)


def test_runtime_error_is_evidence_only_for_an_absent_output(audit):
    packet, result = audit
    native = packet['cases'][0]['outputs'][0]
    native['normalized_fields'] = None
    native['runtime_error'] = 'Schema generation rejected'
    native['leaf_paths'] = []
    output = result['cases'][0]['outputs'][0]
    output['verdict'] = 'no_output'
    output['checked_paths'] = []
    output['fields'][0]['status'] = 'missing'
    output['fields'][0]['source_quote'] = 'Schema generation rejected'
    assert validate(result, packet)
    native['normalized_fields'] = {}
    with pytest.raises(ValueError, match='literal'):
        validate(result, packet)
