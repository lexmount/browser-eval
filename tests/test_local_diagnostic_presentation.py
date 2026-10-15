"""Uncertified local evidence must remain visibly diagnostic, even when complete."""
import re
from pathlib import PurePosixPath

import pytest
from bs4 import BeautifulSoup

from harness.web_fetch.report import generate, local_audit_banner, overall_ranking, ranking
from tests.test_report_body_scope import case, record


def test_diagnostic_tables_do_not_assign_ranks_or_initial_score_order():
    c = case('body', kind='body', track='web')
    high = record(c)
    high['provider'] = 'z-high'
    low = record(c, passed=False)
    low['provider'] = 'a-low'
    for rendered in (overall_ranking([high, low], {c['id']: c}, diagnostic=True),
                     ranking([high, low], {c['id']: c}, 'web', diagnostic=True)):
        soup = BeautifulSoup(rendered, 'html.parser')
        table = soup.select_one('table.ranking')
        assert table.get('data-col') is None
        assert table.select_one('th').get_text(strip=True).startswith('方法')
        rows = table.select('tbody tr')
        assert ['a-low', 'z-high'] == [row.select_one('td a')['href'].split('/')[-1].removesuffix('.html') for row in rows]
        assert len(rows[0].select('td')) == len(table.select('th'))
        assert '0)' in table.select_one('th button')['onclick']
    # Certified comparisons retain their existing score order and ordinal column.
    certified = BeautifulSoup(overall_ranking([high, low], {c['id']: c}), 'html.parser')
    assert certified.select_one('tbody tr td').get_text() == '1'
    assert 'z-high' in certified.select_one('tbody tr').get_text()


def test_uncertified_status_cannot_be_suppressed(tmp_path):
    dataset = {'comparison': {'kind': 'uniform_native_local_measurement',
                             'publication_state': 'local_audit_only_not_for_release'}}
    assert '不可用于排名' in local_audit_banner(dataset)
    with pytest.raises(ValueError, match='cannot be hidden'):
        generate(tmp_path / 'report', dataset, [], [], show_local_status_banners=False)
    assert not (tmp_path / 'report').exists()


def test_diagnostic_status_is_localized():
    from harness.web_fetch.report_i18n import localize_html
    banner = local_audit_banner({'comparison': {'kind': 'uniform_native_local_measurement',
                                'publication_state': 'local_audit_only_not_for_release'}})
    english = localize_html(banner, PurePosixPath('index.html'))
    assert 'uncertified' in english
    assert not re.search('[\u3400-\u9fff]', english)


def test_table_header_gate_accepts_explicit_unranked_methods_only_locally():
    from harness.web_fetch.report_check import Tables, require_table_index_headers
    for english, label in [(False, '方法 ↕'), (True, 'Method ↕')]:
        parser = Tables()
        parser.feed('<table data-row-index="false"><th>'+label+'</th></table>')
        require_table_index_headers(parser, english=english, diagnostic=True)
        with pytest.raises(ValueError, match='index header'):
            require_table_index_headers(parser, english=english, diagnostic=False)
        missing_flag = Tables()
        missing_flag.feed('<table><th>'+label+'</th></table>')
        with pytest.raises(ValueError, match='index header'):
            require_table_index_headers(missing_flag, english=english, diagnostic=True)


def test_home_gate_requires_diagnostic_summary_and_rejects_winner_claims():
    from harness.web_fetch.report_check import require_home_comparison_layout
    home = ('<h2>7 类任务概览</h2><h2>任务判定汇总（非排名）</h2>'
            '<h2>按任务类别查看诊断结果</h2><p>判分完整性</p>')
    require_home_comparison_layout(home, 7, diagnostic=True)
    with pytest.raises(ValueError, match='winner disclosure'):
        require_home_comparison_layout(home+'<p>本类最高：A</p>', 7, diagnostic=True)
    with pytest.raises(ValueError, match='comparison must precede'):
        require_home_comparison_layout(home, 7, diagnostic=False)


def test_release_check_rejects_hidden_status_or_ranked_diagnostic(tmp_path):
    from harness.web_fetch.report_check import check_local_diagnostic_presentation
    page = tmp_path / 'index.html'
    prefix = '<meta name="browser-eval-report" content="1">'
    page.write_text(prefix + '<p>Complete results</p>')
    with pytest.raises(ValueError, match='disclosure missing'):
        check_local_diagnostic_presentation(tmp_path)
    banner = '<div data-evidence-state="local_measurement">Uncertified</div>'
    page.write_text(prefix + banner + '<table id="ranking-overall"><th>Rank</th></table>')
    with pytest.raises(ValueError, match='assigns ranks'):
        check_local_diagnostic_presentation(tmp_path)
    page.write_text(prefix + banner + '<table id="ranking-overall"><th>Method</th></table>')
    check_local_diagnostic_presentation(tmp_path)


def test_frozen_download_summary_uses_current_scores_and_exclusions(tmp_path):
    import copy
    import json
    from harness.web_fetch.report_build import write_frozen_result_summary
    from harness.web_fetch.reliability import assess_reliability
    c = case('body', kind='body', track='web')
    included = record(c)
    excluded = copy.deepcopy(included)
    excluded['attempt_id'] = 'excluded'
    excluded['provider'] = 'excluded-method'
    excluded['metrics']['evaluation_excluded'] = True
    rows = [included, excluded]
    dataset = {'cases': [c], 'coverage_plan': {'web': [row['provider'] for row in rows]}}
    saved = copy.deepcopy(rows)
    (tmp_path / 'summary.json').write_text('{"stale":999}')
    (tmp_path / 'reliability.json').write_text('{"stale":true}')
    write_frozen_result_summary(tmp_path, dataset, rows)
    summary = json.loads((tmp_path / 'summary.json').read_text())['web']
    assert summary[included['provider']]['task_pass'] == 1
    assert summary['excluded-method']['task_pass'] == 0
    assert summary['excluded-method']['task_evaluated_attempts'] == 0
    assert summary['excluded-method']['task_excluded_attempts'] == 1
    assert json.loads((tmp_path / 'reliability.json').read_text()) == assess_reliability(dataset, rows)
    assert rows == saved
    from harness.web_fetch.report_check import check_saved_result_summary
    check_saved_result_summary(tmp_path, dataset, rows)
    summary['excluded-method']['task_pass'] = 1
    (tmp_path / 'summary.json').write_text(json.dumps({'web': summary}))
    with pytest.raises(ValueError, match='summary differs'):
        check_saved_result_summary(tmp_path, dataset, rows)


def test_explicit_sample_ranking_reuses_existing_ranked_table_and_keeps_scope(tmp_path):
    from harness.web_fetch.report import local_pass_rate_ranking
    from harness.web_fetch.report_check import check_local_diagnostic_presentation
    dataset = {'comparison': {'kind': 'uniform_native_local_measurement',
               'publication_state': 'local_audit_only_not_for_release',
               'presentation': {'ranking': 'saved_task_pass_rate'}}}
    c = case('body', kind='body', track='web')
    high, low = record(c), record(c, passed=False)
    high['provider'], low['provider'] = 'z-high', 'a-low'
    markup = overall_ranking([low, high], {c['id']: c}, diagnostic=not local_pass_rate_ranking(dataset))
    soup = BeautifulSoup(markup, 'html.parser')
    assert soup.select_one('tbody tr td').get_text() == '1'
    assert 'z-high' in soup.select_one('tbody tr').get_text()
    banner = local_audit_banner(dataset)
    assert 'data-ranking-scope="saved-task-pass-rate"' in banner
    assert '未完成仓库认证流程' in BeautifulSoup(banner, 'html.parser').get_text()
    assert 'hidden' not in banner
    assert 'callout' not in banner
    page = tmp_path/'index.html'
    page.write_text('<meta name="browser-eval-report" content="1">'+banner+markup)
    check_local_diagnostic_presentation(tmp_path, ranked=True)
    with pytest.raises(ValueError, match='assigns ranks'):
        check_local_diagnostic_presentation(tmp_path)
    page.write_text('<meta name="browser-eval-report" content="1"><div data-evidence-state="local_measurement">Uncertified</div>'+markup)
    with pytest.raises(ValueError, match='scope missing'):
        check_local_diagnostic_presentation(tmp_path, ranked=True)


def test_unreviewed_new_calls_are_explicit_and_cannot_hide_reviewed_rows():
    from harness.web_fetch.adjudication_evidence import require_current_review_state
    c = case('body', kind='body', track='web')
    r = record(c)
    audit = {'attempts': [], 'unreviewed_attempt_ids': [r['attempt_id']],
             'reviewed_attempts': 0, 'accepted_unreviewed_passes': 1, 'unresolved': 0}
    require_current_review_state([r], audit)
    audit['accepted_unreviewed_passes'] = 0
    with pytest.raises(ValueError, match='coverage differs'):
        require_current_review_state([r], audit)


@pytest.mark.parametrize('track', ['dynamic', 'document'])
def test_contract_prose_localizes_without_full_snapshot_or_fixed_field_counts(track):
    from harness.web_fetch.report_components import task_pass_guide
    from harness.web_fetch.report_i18n import localize_html
    from harness.web_fetch.report_check import EnglishNarrative
    cases = {'example': {'id': 'example', 'name': 'Example', 'track': track,
                        'acceptance_contract': {'field_minimum_records': {'shipping': 2}}}}
    english = localize_html(task_pass_guide(track, cases=cases), PurePosixPath('index.html'))
    parser = EnglishNarrative({'Example'})
    parser.feed(english)
    assert parser.untranslated == []
    assert 'shipping: at least 2 records' in english


@pytest.mark.parametrize('wrapper', [
    '<span hidden {attrs}>Uncertified</span>',
    '<p style="display: none" {attrs}>Uncertified</p>',
    '<section hidden><p {attrs}>Uncertified</p></section>',
    '<p {attrs}></p>'])
def test_sample_ranking_rejects_invisible_scope_disclosure(tmp_path, wrapper):
    from harness.web_fetch.report_check import check_local_diagnostic_presentation
    attrs = 'data-evidence-state="local_measurement" data-ranking-scope="saved-task-pass-rate"'
    (tmp_path/'index.html').write_text('<meta name="browser-eval-report" content="1">'+wrapper.format(attrs=attrs))
    with pytest.raises(ValueError, match='disclosure missing'):
        check_local_diagnostic_presentation(tmp_path, ranked=True)
