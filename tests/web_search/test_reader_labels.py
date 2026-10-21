"""Reader-facing grading units and annotator identity remain explicit."""
from bs4 import BeautifulSoup
import pytest
from harness.web_search.relevance import query_metrics
from harness.web_search.report import relevance_panel

@pytest.mark.parametrize('zh', [True, False])
@pytest.mark.parametrize('kind', ['model', 'human'])
def test_relevance_labels_identify_the_actual_annotator_and_metric(zh, kind):
    profile = {'methods': [{'id': 'search', 'label': 'Search'}], 'cases': [{'id': 'q', 'query': 'Question'}]}
    result = {'case_id': 'q', 'method': 'search', 'family': 'web', **query_metrics(['a'], {'a': 3})}
    evidence = {'judge': {'kind': kind, 'name': 'Reviewer'}, 'plan': {'case_ids': ['q']}}
    text = BeautifulSoup(relevance_panel(profile, [result], evidence, zh), 'html.parser').get_text(' ', strip=True)
    assert ('nDCG@5' in text and '100' in text)
    if kind == 'model':
        assert ('模型裁判' if zh else 'Model judge') in text
        assert ('人工标注者' if zh else 'Human annotator') not in text
        assert ('没有核验网页全文' if zh else 'without verifying full pages') in text
        assert ('重新标注可能产生差异' if zh else 'new labeling run may differ') in text
    else:
        assert ('人工标注者' if zh else 'Human annotator') in text
        assert ('模型裁判' if zh else 'Model judge') not in text

def test_cost_budget_counts_retries_and_keeps_unknown_prices_unknown():
    from harness.web_search.report import cost_panel
    profile = {'methods': [
        {'id': 'priced', 'label': 'Priced', 'currency': 'CNY', 'price_per_1000': 5, 'billing': 'http-200-nonempty'},
        {'id': 'unknown', 'label': 'Unknown', 'currency': 'CNY', 'price_per_1000': None, 'billing': 'unknown'}]}
    rows = [{'method': 'priced', 'attempts': [{'cost': 0}, {'cost': .005}]}, {'method': 'unknown', 'cost': None}]
    doc = BeautifulSoup(cost_panel(profile, rows, True), 'html.parser')
    data = [[c.get_text(' ', strip=True) for c in tr.select('td')] for tr in doc.select('tbody tr')]
    assert data[0][4:7] == ['1', '2', '1']
    assert data[1][1] == '未定价' and data[1][6:] == ['待定', '未定价']
