"""Custom users receive only their own tasks, labels and external approvals."""
import json
import pytest
from bs4 import BeautifulSoup

from harness.web_search.comparison import render_comparison
from harness.web_search.review import row_state
from harness.web_search.revisions import validate_revision_binding
from harness.web_search.core import digest


def test_unlabelled_custom_report_does_not_claim_public_or_graded_results():
    profile = {"protocol": {"top_k": 5}, "methods": [{"id": "custom", "label": "Custom engine"}],
               "cases": [{"id": "probe", "query": "A new user's query", "family": "web"}]}
    row = {"id": "r", "case_id": "probe", "method": "custom", "results": [], "http_status": 200}
    for zh in (False, True):
        page = render_comparison(profile, [row], zh)
        dom = BeautifulSoup(page, "html.parser")
        assert {o['value'] for o in dom.select('[data-compare-metric] option')} == {'primary', 'availability'}
        assert "自建集按同一套四级" not in page


def test_review_selection_agrees_with_relevance_and_quarantine():
    from lexbench_websearch.scoring import candidate_identity
    case = {"id": "q", "anchors": ["https://wanted.test/a"]}
    profile = {"cases": [case], "protocol": {"top_k": 5}}
    row = {"case_id": "q", "results": [{"url": "https://other.test/b"}]}
    relevance = {"labels": {"q": {candidate_identity("q", "https://other.test/b"): 3}}}
    assert row_state(profile, row, relevance) == ('pass', 'relevance_hit')
    profile['factual_quality_exclusions'] = ['q']
    assert row_state(profile, row, relevance) == ('probe', 'quality_excluded')


def test_revision_authority_is_explicit_and_binds_cause_and_controls():
    revision = {"root_cause": "A synthetic missing reference", "regression_ids": ["unqualified-reference-rejected"], "provider_calls": 0}
    identity = digest(json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
    record = dict(revision, sha256=identity)
    authority = {'assessment_revisions': {'schema': 'search-assessment-authority/1', 'approved_sha256': [identity], 'provenance': [record]}}
    validate_revision_binding(revision, authority=authority)
    with pytest.raises(ValueError):
        validate_revision_binding(revision)
    record['root_cause'] = 'A different decision'
    with pytest.raises(ValueError, match='provenance'):
        validate_revision_binding(revision, authority=authority)


@pytest.mark.parametrize('field', ['source_bindings', 'packets'])
@pytest.mark.parametrize('relative', ['/absolute/outside.json', '../outside.json'])
def test_review_rejects_paths_outside_frozen_bundle(tmp_path, field, relative):
    from harness.web_search import review
    from tests.web_search.test_review import example
    _, root, contract = example(tmp_path)
    contract[field][0]['path'] = relative
    (root / 'contract.json').write_text(json.dumps(contract))
    with pytest.raises(ValueError, match='inside its bundle'):
        review.load_frozen(root)


def test_native_billing_transform_and_missing_telemetry_are_preserved():
    from harness.web_search.adapters import adapter_for
    from harness.web_search.diagnostics import operational
    payload = {'code': 200, 'data': {'webPages': {'value': [{'url': 'https://example.test/a'}]}}}
    method = {'adapter': 'bocha', 'id': 'engine', 'billing': 'http-200-nonempty', 'price_per_1000': 12}
    assert adapter_for('bocha').cost(method, 200, 0, payload) == 0.012
    row = {'id': 'a', 'method': 'engine', 'case_id': 'q', 'round': 0, 'elapsed_ms': 1}
    stats = operational([row], {'a': {'attempts': [{'elapsed_ms': 10}]}}, [method])
    assert stats['methods']['engine']['attempts'] == {'n': 0, 'mean': None}


def test_new_adapter_envelope_survives_collection_report_and_review(tmp_path, monkeypatch):
    from harness.web_search import runner, report, review, adapters
    from tests.web_search.test_runner import CollectionScenarios
    path = CollectionScenarios().profile(tmp_path)
    profile = json.loads(path.read_bytes())
    profile['methods'] = profile['methods'][:1]
    profile['protocol']['top_k'] = 10
    profile['cases'][0]['reference_answer'] = 'needleanswer'
    profile['methods'][0].update(adapter='custom-envelope', key_env='CUSTOM_TEST_KEY')
    path.write_text(json.dumps(profile))
    monkeypatch.setitem(adapters.ADAPTERS, 'custom-envelope', adapters.Adapter(
        adapters.lexmount_request, response=lambda payload: {'results': payload['items']}))
    monkeypatch.setattr(runner, 'load_runtime_env', lambda: None)
    monkeypatch.setenv('CUSTOM_TEST_KEY', 'fixture')
    target = profile['cases'][0]['anchors'][0]
    def http(url, body=None, header=None, timeout=20):
        receipt = {'http_status': 200, 'elapsed_ms': 1, 'transport_exit': 0, 'transport_error': ''}
        items = [{'url': target, 'title': 'Custom result', 'snippet': 'Useful content'} for _ in range(5)]
        items.append({'url': target, 'title': 'Answer page', 'snippet': 'needleanswer'})
        raw = json.dumps({'items': items}).encode() if body else b'reference ' * 20
        return receipt, raw
    monkeypatch.setattr(runner, 'http_call', http)
    source = tmp_path / 'calls'
    runner.run(path, source, certified=True)
    output = tmp_path / 'report'
    report.build(output, source=source)
    selected, rows = report.check(output, return_data=True, evidence_only=True)
    assert rows[0]['results'][0]['url'] == target
    sensitivity = json.loads((output / 'factual-sensitivity.json').read_bytes())
    assert sensitivity['results'][rows[0]['id']]['metrics']['answer_in_delivered_text_10'] == 1
    cases, _ = review.make_cases(selected, rows, output)
    assert cases[0]['outputs'][0]['items'][0]['url'] == target
