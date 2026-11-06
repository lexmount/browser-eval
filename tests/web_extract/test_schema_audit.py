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
    source.write_text("Alice: red mug, 12 EUR. Bob: blue mug, 15 EUR.\n", encoding='utf-8')
    reference = {"items": [{"name": "Alice", "price": 12}, {"name": "Bob", "price": 15}]}
    native = {"items": [{"name": "Bob", "price": 15}, {"name": "Alice", "price": 12}]}
    item = {
        "audit_kind": "schema-json-audit/1", "case_id": "one",
        "source_outline": str(source), "source_html": str(source),
        "reference": reference, "reference_leaf_paths": json_leaf_paths(reference),
        "reference_field_inventory": ["items"], "outputs": [{
            "method": "native", "leaf_paths": json_leaf_paths(native),
            "field_inventory": ["items"], "normalized_fields": native, "http_status": 200,
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
    assert "value_score" not in path.read_text(encoding='utf-8')


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


@pytest.mark.parametrize('defect', [
    'absent', 'http_error', 'incorrect_field', 'ambiguous', 'error_finding'])
def test_correct_verdict_cannot_contradict_native_evidence(audit, defect):
    packet, result = audit
    output = result['cases'][0]['outputs'][0]
    if defect == 'absent':
        packet['cases'][0]['outputs'][0]['normalized_fields'] = None
    elif defect == 'http_error':
        packet['cases'][0]['outputs'][0]['http_status'] = 400
    elif defect in {'incorrect_field', 'ambiguous'}:
        output['fields'][0]['status'] = 'ambiguous' if defect == 'ambiguous' else 'incorrect'
    else:
        output['fields'][0]['findings'] = [{
            'path': '/items', 'kind': 'extraction_error',
            'reason': 'A requested record is missing.',
            'source_quote': 'Bob: blue mug, 15 EUR.', 'proposed_json': '',
        }]
    with pytest.raises(ValueError):
        validate(result, packet)


@pytest.mark.parametrize('defect', ['outputs', 'reference', 'fields', 'findings'])
def test_malformed_cached_model_containers_are_retryable_value_errors(audit, defect):
    from harness.web_extract.review import reconcile_citations

    packet, result = audit
    case = result['cases'][0]
    if defect in {'outputs', 'reference'}:
        case.pop(defect)
    elif defect == 'fields':
        case['outputs'][0]['fields'] = None
    else:
        case['outputs'][0]['fields'][0]['findings'] = [None]
    with pytest.raises(ValueError):
        reconcile_citations(result, packet)


@pytest.mark.parametrize('defect', ['outputs', 'case', 'method', 'finding_kind'])
def test_resume_quarantines_a_malformed_raw_reply_and_retries_once(
    tmp_path, monkeypatch, audit, defect
):
    from harness.web_extract import review
    from harness.web_extract.pipeline import digest, read_json

    packet, valid = audit
    work = tmp_path / 'work'
    path = work / 'packets/one.json'
    review.save_packet(path, packet, tmp_path)
    review.save_manifest(work, {'schema': 'schema-json-audit/1', 'case_count': 1,
        'result_count': 1, 'bindings': {}, 'packets': [{'path': str(path),
            'sha256': digest(path.read_bytes())}]}, tmp_path)
    save_json(work / 'schema.json', review.review_schema(packet['cases']))
    malformed = copy.deepcopy(valid)
    if defect == 'outputs':
        malformed['cases'][0].pop('outputs')
    elif defect == 'case':
        malformed['cases'][0]['case_id'] = 'unknown-case'
    elif defect == 'method':
        malformed['cases'][0]['outputs'][0]['method'] = 'unknown-method'
    else:
        malformed['cases'][0]['outputs'][0]['fields'][0]['findings'] = [{
            'kind': [], 'path': '/items', 'reason': 'Malformed reply.',
            'source_quote': 'Alice', 'proposed_json': '',
        }]
    replies = iter([malformed, valid])
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return copy.deepcopy(next(replies)), {'requested_model': 'one-model'}

    monkeypatch.setattr('harness.review_runner.run_chat_reviewer', chat)
    credentials = tmp_path / 'gateway.env'
    credentials.write_text(
        'OPENAI_BASE_URL=https://example.org/v1\nOPENAI_API_KEY=test-only\n', encoding='utf-8')
    options = {'model': 'one-model', 'workers': 1, 'backend': 'litellm',
               'credential_env_file': credentials}
    assert len(review.run(work, **options)['needs_correction']) == 1
    assert review.run(work, **options)['packets'] == 1
    assert len(list((work / 'invalid').glob('*.json'))) == 1
    assert read_json(work / 'reviews/one.json') == valid
    assert review.run(work, **options)['packets'] == 1
    assert len(calls) == 2


def test_gateway_rejects_an_html_audit_without_a_complete_inline_source_contract(tmp_path):
    from harness.web_extract.review import run

    work = tmp_path / 'html-review'
    save_json(work / 'manifest.json', {'schema': 'product-audit/1', 'packets': [], 'bindings': {}})
    with pytest.raises(ValueError, match='complete frozen Markdown'):
        run(work, model='one-model', backend='litellm')
