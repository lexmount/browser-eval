"""Local scoring and edition comparison do not require installed datasets."""
import inspect
import json
import os
import subprocess
import sys

import pytest

from lexbench_webfetch.metrics import task_measured, task_passed
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.scoring_regressions import verdict_correction_counts
from lexbench_websearch.evidence_quality import is_benchmark_page
from lexbench_websearch.scoring import evaluate


@pytest.mark.parametrize('track', ['webfetch', 'websearch'])
def test_local_release_and_comparison_work_with_empty_offline_cache(tmp_path, track):
    dataset = tmp_path / 'dataset.json'
    dataset.write_text(json.dumps({'cases': [{'id': 'local', 'url': 'https://example.org',
                                            'query': 'Find the official manual'}]}))
    output = tmp_path / 'release.json'
    cache = tmp_path / 'empty-cache'
    environment = {**os.environ, 'HF_HUB_OFFLINE': '1', 'BROWSER_EVAL_DATASET_CACHE': str(cache)}
    def call(*args):
        result = subprocess.run([sys.executable, '-m', f'lexbench_{track}.cli', *args],
                                cwd=tmp_path, env=environment, text=True,
                                capture_output=True, check=True)
        return json.loads(result.stdout)
    arguments = ['release', '--dataset', str(dataset), '--output', str(output),
                 '--version', '1.0.0', '--scorer-revision', 'a' * 40]
    if track == 'webfetch':
        arguments += ['--benchmark', 'local']
    call(*arguments)
    assert call('compare-releases', str(output), str(output))['mode'] == 'direct'
    assert not cache.exists()


def test_search_local_score_works_with_empty_offline_cache(tmp_path):
    source = tmp_path / 'score.json'
    source.write_text(json.dumps({'ranking': ['a'], 'grades': {'a': 3}}))
    cache = tmp_path / 'empty-cache'
    result = subprocess.run([sys.executable, '-m', 'lexbench_websearch.cli', 'score', str(source)],
                            cwd=tmp_path, env={**os.environ, 'HF_HUB_OFFLINE': '1',
                            'BROWSER_EVAL_DATASET_CACHE': str(cache)},
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)
    assert not cache.exists()


@pytest.mark.parametrize('title,expected', [('Original source', False), ('Dataset mirror', True)])
def test_explicit_contamination_policy_never_loads_default_dataset(monkeypatch, title, expected):
    from lexbench_websearch import evidence_quality
    monkeypatch.setattr(evidence_quality, 'default_patterns', lambda: pytest.fail('Unneeded default download'))
    assert is_benchmark_page({'url': 'https://example.org', 'title': title},
                             case={'contamination_patterns': {'identity_patterns': ['Dataset mirror']}}) is expected


@pytest.mark.parametrize('cutoff', [1, 2, 4, 5, 10])
@pytest.mark.parametrize('inside', [False, True])
def test_factual_retrieval_does_not_count_evidence_beyond_selected_cutoff(cutoff, inside):
    case = {'reference_answer': 'Paris', 'reference_urls': ['https://example.org/answer']}
    count = min(cutoff, 5)
    results = [{'url': f'https://example.org/{i}', 'title': 'Unknown'} for i in range(count + 1)]
    results[count - 1 if inside else count] = {'url': 'https://example.org/answer', 'title': 'Paris'}
    metrics = evaluate(case, results, cutoff=cutoff)
    assert metrics['labeled_source_hit_5'] == int(inside)
    assert metrics['answer_in_preview_5'] == int(inside)


def test_page_identity_option_remains_keyword_only():
    parameter = inspect.signature(EvalCase).parameters['page_identity_required']
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert EvalCase('local', 'Local', 'https://example.org', 'web', [],
                    page_identity_required=True).page_identity_required is True


@pytest.mark.parametrize('before,after', [(0, True), (1, False), (False, 1), (True, 0), ([], True)])
def test_correction_receipts_reject_non_boolean_verdicts(before, after):
    with pytest.raises(ValueError, match='boolean verdict flips'):
        verdict_correction_counts([{'before': before, 'after': after}])


def test_correction_receipts_count_both_error_directions():
    assert verdict_correction_counts([{'before': True, 'after': False}, {'before': False, 'after': True}]) == {
        'false_positives': 1, 'false_negatives': 1, 'total': 2}


@pytest.mark.parametrize('unresolved', [False, True])
def test_truncation_does_not_reactivate_an_unresolved_contract(unresolved):
    case = {'url': 'https://example.org', 'acceptance_contract': {
        'version': 1, 'schema': 'named_link', 'minimum_records': 1,
        'hosts': ['example.org'], 'entry_path': '/item/\\d+', 'stable_text_anchors': []}}
    if unresolved:
        case['rule_review'] = {'status': 'unresolved', 'reason': 'Reference needs qualification'}
    response = b'[A visible item](/item/1)\n[Content truncated: showing 1 of 10 lines]'
    metrics = score_attempt(case, response, ok=True)
    assert task_measured(metrics) is (not unresolved)
    assert task_passed(True, metrics) is False
