"""Latency presentation distinguishes physical-call speed from logical cost."""
from bs4 import BeautifulSoup
import pytest

from harness.web_fetch.report import logical_execution_cost_ms, overall_ranking


def result(method, case, elapsed, passed=True, **extra):
    return {'provider': method, 'case_id': case, 'elapsed_ms': elapsed,
            'ok': passed, 'metrics': {'content_pass': passed}, **extra}


def test_fast_failure_cannot_win_latency_and_retry_cost_is_visible():
    cases = {key: {'id': key, 'track': 'web', 'task_kind': 'body'} for key in ('a', 'b')}
    rows = [result('fails-fast', key, 1, False) for key in cases]
    rows += [result('passes', 'a', 10, provider_metadata={'retry': {'execution_cost_ms': 30010}}),
             result('passes', 'b', 20)]
    doc = BeautifulSoup(overall_ranking(rows, cases), 'html.parser')
    main = doc.select_one('#ranking-overall')
    passed = next(row for row in main.select('tbody tr') if 'passes' in row.get_text())
    assert '15,015 ms' in passed.select('td')[-1].get_text()
    assert '2/2' in passed.select('td')[-1].get_text()
    assert all(not cell.select('.comparison-winner') for row in main.select('tbody tr')
               for cell in row.select('td')[-4:])
    conditional = doc.select_one('#successful-final-call-latency')
    failed = next(row for row in conditional.select('tbody tr') if 'fails-fast' in row.get_text())
    assert '0/2' in failed.get_text()
    assert not failed.select('.comparison-winner')
    passed = next(row for row in conditional.select('tbody tr') if 'passes' in row.get_text())
    assert len(passed.select('.comparison-winner')) == 3
    assert '15 ms' in passed.get_text()


@pytest.mark.parametrize('metadata,expected', [
    ({}, 10),
    ({'provider_metadata': {'call_sequence_elapsed_ms': 12}}, 12),
    ({'provider_metadata': {'retry': {'execution_cost_ms': 35}}}, 35),
    ({'provider_metadata': {'retry': {}}}, None),
    ({'provider_metadata': {'retry_cleanup_wait_ms': 100, 'retry_throttle_wait_ms': 5}}, 115),
    ({'provider_metadata': {'retry_pending': {'remaining_physical_calls': 1},
                           'retry_cleanup_wait_ms': 100}}, None),
    ({'report_retry_continuation': {'execution_cost_ms': 60}}, 60),
    ({'report_retry_continuation': {'execution_cost_ms': None},
      'provider_metadata': {'retry': {'execution_cost_ms': 35}}}, None),
])
def test_execution_cost_uses_complete_saved_evidence(metadata, expected):
    assert logical_execution_cost_ms(result('method', 'case', 10, **metadata)) == expected


def test_missing_prior_cost_is_unavailable_not_a_fast_partial_mean():
    cases = {'a': {'id': 'a', 'track': 'web', 'task_kind': 'body'}}
    rows = [result('method', 'a', 10, report_retry_continuation={'execution_cost_ms': None})]
    doc = BeautifulSoup(overall_ranking(rows, cases), 'html.parser')
    cost = doc.select_one('#ranking-overall tbody tr').select('td')[-1]
    assert cost.get_text(' ', strip=True) == '— 0/1'
    assert not cost.select('.comparison-winner')


def test_methodology_settings_disclose_mixed_groups_and_escape_request_data():
    from harness.web_fetch.report import saved_matrix_scope_section
    settings = [
        {'provider': 'method', 'family': 'body', 'calls': 2, 'verified_calls': 1,
         'uniform': False, 'collection_start': '2026-01-01', 'collection_end': '2026-02-01',
         'settings': [{'dns_mode': 'system', 'timeout_seconds': 30, 'max_failure_retries': 1,
                       'request': {'payload': '<script>unsafe()</script>'}}],
         'errors': ['missing native contract']},
        {'provider': 'other', 'family': 'body', 'calls': 2, 'verified_calls': 2,
         'uniform': True, 'collection_start': '2026-03-01', 'collection_end': '2026-03-01',
         'settings': [{'dns_mode': 'public_doh', 'timeout_seconds': 60, 'max_failure_retries': 0}],
         'errors': []},
    ]
    rendered = saved_matrix_scope_section({'limitations': [], 'execution_settings': settings}, show_completion=False)
    doc = BeautifulSoup(rendered, 'html.parser')
    table = doc.select_one('#saved-execution-settings')
    assert len(table.select('tbody tr')) == 2
    assert not table.select('.comparison-winner')
    assert not table.select('script')
    assert '<script>unsafe()</script>' in table.get_text()
    assert '1/2' in table.get_text()
    assert 'public_doh' in table.get_text()
    assert '2026-02-01' in table.get_text()
    assert doc.select_one('[data-acceptance-quality-limit]')
    assert '不是完整的噪声惩罚' in rendered


def test_new_metric_labels_and_scope_notes_have_english_views():
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html
    cases = {'a': {'id': 'a', 'track': 'web', 'task_kind': 'body'}}
    rendered = overall_ranking([result('method', 'a', 10)], cases)
    english = localize_html(rendered, PurePosixPath('index.html'))
    assert 'Mean total execution cost (known / called)' in english
    assert 'Final-call latency of task-passing results' in english
    assert 'failures cannot win latency highlights' in english
    assert '累计执行耗时均值' not in english
