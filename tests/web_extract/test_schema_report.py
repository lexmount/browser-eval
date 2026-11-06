"""Arbitrary-schema reports retain measured values and inspectable evidence."""

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.example import create_schema
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import check


@pytest.fixture
def report(tmp_path):
    output = tmp_path / 'report'
    create_schema(output, language='en')
    return output


def test_schema_report_has_complete_matrix_and_source_only_dataset(report):
    manifest = check(report)
    assert manifest['observations'] == manifest['reviewed_results'] == 6
    assert 'Relations compare model-reviewed correctness' in (
        report / 'en/results.html'
    ).read_text(encoding='utf-8')
    for language in ('en', 'zh'):
        results = BeautifulSoup(
            (report / language / 'results.html').read_text(encoding='utf-8'), 'html.parser')
        assert len(results.select('tr[data-result]')) == 6
        detail = BeautifulSoup(
            (report / language / 'c-two.html').read_text(encoding='utf-8'), 'html.parser')
        assert len(detail.select('[data-result-panel]')) == 3
        assert detail.select_one('[data-result-logs] [data-kind="error"]').get_text() == (
            'Synthetic schema rejection'
        )
        task = (report / language / 't-one.html').read_text(encoding='utf-8')
        assert 'Synthetic schema rejection' not in task
        assert 'Amber mug' in task
    with pytest.raises(ValueError, match='Synthetic'):
        check(report, release=True)


def mutate(report, route, transform):
    path = report / route
    path.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8')
    manifest = read_json(report / 'report-manifest.json')
    manifest['files'][route] = digest(path.read_bytes())
    save_json(report / 'report-manifest.json', manifest)


@pytest.mark.parametrize('defect', ['evidence', 'summary', 'logs', 'highlight'])
def test_schema_semantics_reject_invalid_reports_even_with_rebound_files(report, defect):
    if defect == 'evidence':
        mutate(report, 'en/c-two.html', lambda text: text.replace(
            'public-evidence/r-cloudflare-json-two.json', 'public-evidence/absent.json'))
    elif defect == 'summary':
        mutate(report, 'en/c-two.html', lambda text: text.replace(
            'data-failure-diagnosis="">',
            'data-failure-diagnosis=""><summary data-result-summary>duplicate</summary>', 1))
    elif defect == 'logs':
        mutate(report, 'en/c-two.html', lambda text: text.replace(
            'data-result-logs', 'removed-logs'))
    else:
        mutate(report, 'en/index.html', lambda text: text.replace(
            'comparison-winner', 'removed-winner'))
    with pytest.raises(ValueError):
        check(report)


def test_schema_metric_text_cannot_disagree_with_evidence(report):
    def change(text):
        soup = BeautifulSoup(text, 'html.parser')
        cell = soup.select_one('td[data-metric="value_score"]')
        (cell.select_one('span') or cell).string = '0.1234'
        return str(soup)

    mutate(report, 'en/index.html', change)
    with pytest.raises(ValueError, match='displayed metric'):
        check(report)


def test_linear_latency_percentile_matches_its_raw_comparison_value():
    from harness.web_extract.report import latency_p95

    assert latency_p95([1, 3, 9], 'linear') == pytest.approx(8.4)
    assert latency_p95([1, 3, 9]) == 9
