"""Excluded-label audit evidence must not enter record counts or verification."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response
from tests.benchmarks.webfetch.test_composite_tasks import named, contract
from tests.benchmarks.webfetch.test_review_record_formats import record_task


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('metadata_only', [False, True])
def test_verified_dynamic_list_counts_only_admitted_names(reverse, metadata_only):
    names = ['Alpha Show', 'FreeTV']
    if reverse:
        names.reverse()
    routing = {'mode': 'dynamic_list_structure', 'minimum_entries': 1,
               'verification_scope': 'first_three_name_detail_pairs',
               'hosts': ['example.test'], 'entry_path': r'/item/\d+',
               'record_name_exclusions': ['FreeTV']}
    case = {'url': 'https://example.test/', 'task_kind': 'structure',
            'content_score_applicable': False, 'source_benchmark': routing}
    reference = {'items': [{'url': 'https://example.test/item/1', 'title': 'Alpha Show'}]}
    artifact = 'Useful introduction' if metadata_only else '\n'.join(f'[{name}](/item/1)' for name in names)
    metadata = {'links': [{'url': '/item/1', 'title': name} for name in names]} if metadata_only else None
    result = evaluate_response(case, artifact.encode(), metadata=metadata, list_reference=reference)
    assert result['passed']
    assert result['metrics']['named_entry_count'] == 1
    assert result['metrics']['list_checks'][0]['title'] == 'Alpha Show'
    routing['minimum_entries'] = 2
    assert not evaluate_response(case, artifact.encode(), metadata=metadata, list_reference=reference)['passed']


def test_stable_metadata_only_keeps_exclusion_audit_without_count_inflation():
    case = record_task(record_name_exclusions=['FreeTV'])
    result = evaluate_response(case, b'Useful introduction', metadata={
        'links': [{'url': '/item/1', 'title': 'FreeTV'}, {'url': '/item/1', 'title': 'Alpha Show'}]})
    assert result['passed']
    assert result['metrics']['stable_complete_record_count'] == 1
    assert result['metrics']['stable_excluded_record_names'] == ['FreeTV']


@pytest.mark.parametrize('reverse', [False, True])
def test_composite_exclusion_policy_retains_admitted_record(reverse):
    names = ['Alpha Show', 'FreeTV']
    if reverse:
        names.reverse()
    case = {'url': 'https://example.test/', 'task_kind': 'structure', 'content_score_applicable': False,
            'composite_acceptance': contract(named('shows', r'^# Shows$', r'^# End$', 1,
                                                 record_name_exclusions=['FreeTV']))}
    body = '# Shows\n' + '\n'.join(f'[{name}](/item/1)' for name in names) + '\n# End'
    result = evaluate_response(case, body.encode())
    assert result['passed']
    assert result['metrics']['composite_group_results'][0]['count'] == 1
