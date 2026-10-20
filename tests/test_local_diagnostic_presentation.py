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
    home = ('<h2>任务判定汇总（非排名）</h2><h2>7 类任务概览</h2>'
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


def test_review_display_distinguishes_fresh_reviews_from_retained_history(tmp_path):
    import hashlib
    import json
    from harness.web_fetch.report import current_local_review_findings, review_summary
    audit = {'reviewed_attempts': 100, 'expected_attempts': 100,
             'fresh_reviewed_attempts': 10, 'retained_reviewed_attempts': 90,
             'retained_reviews': {'path': 'retention.json'},
             'supplemental_reviews': [{'path': 'supplement.json'}],
             'attempts': [{'review_origin': 'fresh', 'agent_verdict': 'fail', 'final_verdict': 'pass'}]}
    (tmp_path/'result-review.json').write_text(json.dumps(audit))
    summary = BeautifulSoup(review_summary(tmp_path, highlight=False), 'html.parser')
    assert '本次重新检查 10 条' in summary.get_text()
    assert '90 条沿用' in summary.get_text()
    assert not summary.select('.callout')
    receipt = tmp_path/'replacement.json'
    receipt.write_text(json.dumps({'provider_calls_added': 1, 'inherited_results': 99}))
    dataset = {'comparison': {'task_replacement': {'path': receipt.name,
        'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}}}
    c = case('body', kind='body', track='web')
    dataset['cases'] = [c]
    findings = current_local_review_findings(tmp_path, dataset, [record(c)], audit)
    text = ' '.join(x['text'] for x in findings)
    assert '重新检查 10 条' in text and '90 条沿用原始复核' in text
    assert '初判与程序有 1 条分歧' in text
    assert '复核前已知机器汇总' not in text
    assert '新增调用中 10 条' not in text


@pytest.mark.parametrize('defect', [None, 'unbound', 'hash', 'scope', 'count',
                                   'option', 'unknown_option', 'missing_option', 'type'])
def test_displayed_native_options_require_actual_configuration_and_call_scope(tmp_path, defect):
    import hashlib
    import json
    from harness.web_fetch.report import load_frozen_method_options
    config = {'providers': {'fixture': {'max_bytes': 100, 'block_private_networks': True}}}
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    document = {'schema_version': 2, 'source_config': {'path': path.name,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
                'case_ids': ['a'], 'result_count': 1,
                'scope_title': '已记录的选项', 'scope_title_en': 'Recorded options',
                'scope_description': '仅对应这个任务。',
                'scope_description_en': 'Only for this task.',
                'providers': {'fixture': {'max_bytes': 100, 'block_private_networks': True}}}
    document['provider_config_sha256'] = document['source_config']['sha256']
    if defect == 'unbound': document['schema_version'] = 1
    elif defect == 'hash': document['source_config']['sha256'] = 'x'*64
    elif defect == 'scope': document['case_ids'] = ['other']
    elif defect == 'count': document['result_count'] = 2
    elif defect == 'option': document['providers']['fixture']['max_bytes'] = 200
    elif defect == 'unknown_option': document['providers']['fixture']['api_key'] = 'not-for-display'
    elif defect == 'missing_option': document['providers']['fixture']['wait_until'] = None
    elif defect == 'type': document['providers']['fixture']['block_private_networks'] = 1
    (tmp_path/'frozen-method-options.json').write_text(json.dumps(document))
    rows = [{'provider': 'fixture', 'case_id': 'a'}]
    if defect:
        with pytest.raises(ValueError):
            load_frozen_method_options(tmp_path, rows)
    else:
        assert load_frozen_method_options(tmp_path, rows)['result_count'] == 1


@pytest.mark.parametrize('defect', [None, 'original_hash', 'unsafe_value', 'unknown_key', 'projection_kind', 'changed_bytes'])
def test_public_method_option_projection_keeps_private_config_identity(tmp_path, defect):
    import hashlib
    import json
    from harness.web_fetch.report import load_frozen_method_options
    original_hash = 'a' * 64
    (tmp_path/'local-run-contract.json').write_text(json.dumps({'provider_config_sha256': original_hash}))
    config = {'providers': {'fixture': {'max_bytes': 100}},
              'projection': 'allowlisted_method_options',
              'original_provider_config_sha256': original_hash}
    if defect == 'original_hash': config['original_provider_config_sha256'] = 'b' * 64
    elif defect == 'unsafe_value': config['providers']['fixture']['proxy'] = 'https://user:password@example.invalid'
    elif defect == 'unknown_key': config['providers']['fixture']['api_key'] = 'private'
    elif defect == 'projection_kind': config['projection'] = 'unverified'
    path = tmp_path/'public-options.json'
    path.write_text(json.dumps(config))
    document = {'schema_version': 2, 'source_config': {'path': path.name,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
                'provider_config_sha256': original_hash,
                'case_ids': ['a'], 'result_count': 1, 'providers': config['providers'],
                'scope_title': '选项', 'scope_title_en': 'Options',
                'scope_description': '仅这次调用。', 'scope_description_en': 'Only this call.'}
    (tmp_path/'frozen-method-options.json').write_text(json.dumps(document))
    if defect == 'changed_bytes': path.write_text('{}')
    rows = [{'provider': 'fixture', 'case_id': 'a'}]
    if defect:
        with pytest.raises(ValueError): load_frozen_method_options(tmp_path, rows)
    else:
        assert load_frozen_method_options(tmp_path, rows)['providers'] == config['providers']
