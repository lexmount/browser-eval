"""Directional evidence stays correct with missing, tied and rounded values."""
import json

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_search.example import example
from harness.web_search.report import check, numeric_cell, numeric_winners, factual_panel


def render(values):
    rows = [[v] for v in values]
    winners = numeric_winners(rows, {0:'min'})
    return '<table>' + ''.join('<tr>' + numeric_cell('—' if v is None else f'{v:.2f}', v, 'latency', 'min', v is not None and winners.get(0) == v) + '</tr>' for v in values) + '</table>'


def test_missing_values_do_not_hide_a_unique_raw_extreme():
    doc = check_presentation(render([None, 100.004, 100.003]))
    cells = doc.select('td')
    assert cells[0].get('data-sort') is None
    assert cells[0].select_one('.comparison-winner') is None
    assert cells[1].get_text() == '100.00'
    assert cells[1].select_one('.comparison-winner') is None
    assert cells[2]['data-sort'] == '100.003'
    assert cells[2].select_one('.comparison-winner') is not None


def test_tied_and_unavailable_columns_stay_neutral():
    for values in ([None,None], [None,1,1]):
        doc = check_presentation(render(values))
        assert not doc.select('.comparison-winner')


@pytest.mark.parametrize('mutation', ['missing','wrong','tied'])
def test_shared_checker_rejects_missing_wrong_and_tied_markers(mutation):
    doc = BeautifulSoup(render([1,2]), 'html.parser')
    cells = doc.select('td')
    if mutation == 'missing':
        cells[0].select_one('.comparison-winner').unwrap()
    elif mutation == 'wrong':
        marker = cells[0].select_one('.comparison-winner').extract()
        cells[1].append(marker)
    else:
        cells[1]['data-sort'] = '1'
    with pytest.raises(ValueError,match='extremum'):
        check_presentation(doc)


def test_public_report_check_executes_the_shared_marker_gate(tmp_path):
    example(tmp_path)
    page = tmp_path/'report/index.html'
    doc = BeautifulSoup(page.read_text(), 'html.parser')
    marker = doc.select_one('.comparison-winner')
    assert marker is not None
    marker.unwrap()
    page.write_text(str(doc))
    with pytest.raises(ValueError,match='extremum'):
        check(tmp_path/'report')


def test_factual_reciprocal_rank_is_directional_and_highlighted(tmp_path):
    example(tmp_path)
    profile = json.loads((tmp_path/'fixture/profile.json').read_text())
    rows = [json.loads(line) for line in (tmp_path/'fixture/results.jsonl').read_text().splitlines()]
    for case in profile['cases']:
        case.update(reference_answer='answer',reference_urls=[])
    for row in rows:
        row['metrics'].update(labeled_source_hit_5=0,labeled_source_rr_5=0.5 if row['method']=='lexmount' else 0,answer_in_preview_5=0,answer_in_delivered_text_5=0)
    doc=check_presentation(factual_panel(profile,rows,True))
    cells=doc.select('td[data-metric="factual-1"]')
    assert len(cells) > 1
    assert cells[0]['data-metric-direction'] == 'max'
    assert cells[0].select_one('.comparison-winner') is not None
    assert not cells[1].select_one('.comparison-winner')


@pytest.mark.parametrize("damage", ["summary", "error", "evidence"])
def test_public_report_check_rejects_broken_detail_contract(tmp_path, damage):
    from harness.web_search.example import example
    from harness.web_search.report import check
    example(tmp_path)
    output = tmp_path / "report"
    detail = next((output / "details").glob("*.html"))
    doc = BeautifulSoup(detail.read_text(), "html.parser")
    if damage == "summary":
        import copy
        doc.main.append(copy.copy(doc.select_one("[data-result-summary]")))
    elif damage == "error":
        doc.select_one("[data-error]").decompose()
    else:
        doc.select_one("[data-evidence]")["data-evidence"] = "missing-response.json"
    detail.write_text(str(doc))
    with pytest.raises(ValueError):
        check(output)
