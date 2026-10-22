"""Methodology reports saved run parameters, including absent and mixed evidence."""
from pathlib import PurePosixPath

from harness.web_fetch.report import render_execution_contract
from harness.web_fetch.report_i18n import localize_html


def test_recorded_budget_and_intervals_replace_historical_defaults():
    rows = [{'provider': 'acme-fetch'}]
    batches = [{'providers': [{'name': 'acme-fetch', 'min_interval_seconds': 2.5}],
                'parameters': {'timeout': 30, 'delay': 0.4, 'runs': 2, 'max_failure_retries': 3}}]
    page = render_execution_contract({}, rows, batches)
    for value in ('30 秒', '2.5 秒', '0.4 秒'):
        assert value in page
    for stale in ('45 秒', '6.1 秒', '2.0.0', '每题只调用一次'):
        assert stale not in page
    english = localize_html(page, PurePosixPath('methodology/index.html'))
    for value in ('30 seconds', '2.5 seconds', '0.4 seconds', 'Failure retry limit'):
        assert value in english
    assert '30 秒' not in english


def test_missing_execution_evidence_does_not_invent_defaults():
    page = render_execution_contract({}, [{'provider': 'new-method'}], [])
    assert 'new-method' in page
    assert '未记录' in page
    for stale in ('45', '6.1', '30 秒', '内部串行', '方法并行'):
        assert stale not in page


def test_declared_budget_and_actual_batches_are_not_silently_conflated():
    rows = [{'provider': 'acme'}]
    batches = [{'providers': [{'name': 'acme'}], 'parameters': {'timeout': budget}}
               for budget in (20, 25)]
    page = render_execution_contract({'comparison': {'timeout_seconds': 30}}, rows, batches)
    assert '事前声明的外层预算' in page
    for value in ('20 秒', '25 秒', '30 秒'):
        assert value in page
    from bs4 import BeautifulSoup
    assert [row.select('td')[1].get_text() for row in BeautifulSoup(
        page,'html.parser').select('tbody tr')] == ['acme','acme']


def test_unrelated_batch_providers_do_not_enter_the_report():
    page = render_execution_contract({}, [{'provider': 'included'}], [{
        'providers': [{'name': 'unrelated', 'min_interval_seconds': 7.8}],
        'parameters': {'timeout': 91}}])
    assert 'unrelated' not in page
    assert '91 秒' not in page
    assert 'included' in page
