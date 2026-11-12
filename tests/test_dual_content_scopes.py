"""One acquisition supplies two acceptance views of the same persisted response."""
import json
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup

from tests.benchmarks.webfetch.test_content_scopes import annotated_case
from tests.test_optional_reports import invoke
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.metrics import task_passed, summarize


def test_shared_scoring_emits_two_bound_verdicts_without_changing_bytes():
    metrics = score_attempt(annotated_case(), b'Complete article body', ok=True)
    paired = metrics['content_scope_metrics']
    assert set(paired) == {'main', 'full'}
    assert task_passed(True, paired['main'])
    assert not task_passed(True, paired['full'])
    assert paired['main']['content_sha256'] == paired['full']['content_sha256'] == metrics['content_sha256']
    assert paired['main']['content_scope_requirements_sha256'] != paired['full']['content_scope_requirements_sha256']
    assert all('content_scope_metrics' not in row for row in paired.values())


@pytest.mark.parametrize('scope', ['main', 'full'])
@pytest.mark.parametrize('language', ['zh', 'en'])
def test_one_live_call_builds_switchable_report_and_rescores_offline(tmp_path, scope, language):
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path / 'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': scope, 'cases': [case]}), encoding='utf-8')
    env = tmp_path / 'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path / 'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env, '--delay', '0', '--max-failure-retries', '0', language=language)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in (run / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert set(row['metrics']['content_scope_metrics']) == {'main', 'full'}
    assert len(list((run / 'call-logs').rglob('*.json'))) == 1
    summary = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
    assert summary['content_scopes']['main']['providers']['fixture']['task_pass_rate'] == 1
    assert summary['content_scopes']['full']['providers']['fixture']['task_pass_rate'] == 0
    report = run / 'report'
    other = 'full' if scope == 'main' else 'main'
    alternate = report / 'content-scopes' / other
    assert (alternate / 'index.html').is_file()
    for view, active in [(report, scope), (alternate, other)]:
        for relative in ['index.html', 'analysis/index.html', 'datasets/index.html', f'attempts/{row["attempt_id"]}.html']:
            page = view / relative
            soup = BeautifulSoup(page.read_text(encoding='utf-8'), 'html.parser')
            toggle = soup.select_one('[data-content-scope-switch]')
            assert toggle and toggle['data-active-scope'] == active
            links = toggle.select('a[data-content-scope]')
            assert {a['data-content-scope'] for a in links} == {'main', 'full'}
            for link in links:
                assert (page.parent / urlsplit(link['href']).path).resolve().is_file()
        records = [json.loads(line) for line in (view / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(records) == 1
        assert task_passed(records[0]['ok'], records[0]['metrics']) is (active == 'main')
        assert records[0]['elapsed_ms'] == row['elapsed_ms']
        assert records[0]['metrics']['content_sha256'] == row['metrics']['content_sha256']
        if active == 'main' and view == alternate:
            assert records[0].get('diagnosis') is None
            text = BeautifulSoup((view/'attempts'/f'{row["attempt_id"]}.html').read_text(encoding='utf-8'), 'html.parser')
            explanation = text.select_one('#overview .attempt-verdict').get_text()
            assert '未命中核对点' not in explanation and 'Missed checks' not in explanation
        from harness.report_languages import generated_languages
        assert generated_languages(view) == (('zh',) if language == 'zh' else ('zh', 'en'))
        if language == 'zh':
            assert sorted(path.relative_to(view/'en').as_posix() for path in (view/'en').rglob('*') if path.is_file()) == ['index.html']
    before = len(list((run / 'call-logs').rglob('*.json')))
    result = invoke(tmp_path, 'rescore', run, '--dataset', dataset, '--no-report', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((run / 'call-logs').rglob('*.json'))) == before
    updated = json.loads((run / 'results.jsonl').read_text(encoding='utf-8'))
    assert task_passed(True, updated['metrics']['content_scope_metrics']['main'])
    assert not task_passed(True, updated['metrics']['content_scope_metrics']['full'])


def test_scope_summary_reuses_deadline_evidence_for_both_verdicts():
    metrics = score_attempt(annotated_case(), b'Complete article body Contact publisher', ok=True)
    metrics['deadline_overrun'] = True
    row = dict(provider='fixture', provider_type='fixture', case_id='article', case_name='Article', category='synthetic', ok=True, elapsed_ms=100, metrics=metrics)
    summary = summarize([row])
    assert all(summary['content_scopes'][scope]['providers']['fixture']['task_pass_rate'] == 0 for scope in ('main', 'full'))


@pytest.mark.parametrize('defect', ['missing-view', 'metrics', 'latency', 'requirements', 'summary',
                                  'missing-toggle', 'wrong-target', 'wrong-total', 'missing-failure-evidence',
                                  'stripped-structure', 'missing-main', 'unexpected-english',
                                  'absolute-switch', 'protocol-relative-switch', 'usage-changed', 'usage-removed', 'optional-article'])
def test_scope_release_gate_rejects_inconsistent_views(tmp_path, defect):
    from harness.web_fetch.report_check import check_content_scope_views
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path / 'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path / 'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path / 'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    root = run / 'report'
    full = root / 'content-scopes/full'
    if defect in {'usage-changed', 'usage-removed'}:
        for view in (root, full):
            path = view/'results.jsonl'
            row = json.loads(path.read_text(encoding='utf-8'))
            row['provider_metadata'].update(credits_used=2, browser_ms_used=250)
            path.write_text(json.dumps(row)+'\n', encoding='utf-8')
    if defect == 'optional-article':
        from harness.web_fetch.report_build import _wire_content_scope_views
        for view in (root, full):
            (view/'guide/article.html').write_bytes((view/'guide/index.html').read_bytes())
        _wire_content_scope_views(root)
    check_content_scope_views(root)
    if defect == 'optional-article':
        path = full/'guide/article.html'
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        soup.select_one('meta[name="webstone-report"]').decompose()
        soup.select_one('[data-content-scope-switch]').decompose()
        path.write_text(str(soup), encoding='utf-8')
    elif defect == 'missing-view':
        (root / 'content-scope-views.json').unlink()
    elif defect in {'metrics', 'latency', 'usage-changed', 'usage-removed'}:
        path = full / 'results.jsonl'
        row = json.loads(path.read_text(encoding='utf-8'))
        if defect == 'metrics':
            row['metrics']['acceptance_checks']['facts'] = True
        elif defect == 'latency':
            row['elapsed_ms'] += 1
        elif defect == 'usage-changed':
            row['provider_metadata']['credits_used'] += 1
        else:
            row.pop('provider_metadata')
        path.write_text(json.dumps(row) + '\n', encoding='utf-8')
    elif defect == 'unexpected-english':
        path = full/'report-languages.json'
        model = json.loads(path.read_text(encoding='utf-8'))
        model['languages'] = ['zh', 'en']
        path.write_text(json.dumps(model), encoding='utf-8')
    elif defect == 'requirements':
        path = full / 'dataset.json'
        model = json.loads(path.read_text(encoding='utf-8'))
        model['cases'][0]['required_facts'] = []
        path.write_text(json.dumps(model), encoding='utf-8')
    elif defect == 'summary':
        path = full / 'summary.json'
        model = json.loads(path.read_text(encoding='utf-8'))
        model['web']['fixture']['task_pass'] = 1
        path.write_text(json.dumps(model), encoding='utf-8')
    else:
        path = (full / 'index.html') if defect not in {'missing-failure-evidence', 'stripped-structure', 'missing-main'} else next((full / 'attempts').glob('*.html'))
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        if defect == 'missing-toggle':
            soup.select_one('[data-content-scope-switch]').decompose()
        elif defect == 'wrong-target':
            soup.select_one('a[data-content-scope="main"]')['href'] = 'index.html'
        elif defect in {'absolute-switch', 'protocol-relative-switch'}:
            link = soup.select_one('a[data-content-scope="main"]')
            target = (path.parent / link['href']).resolve()
            link['href'] = ('https:' if defect == 'absolute-switch' else '') + '//example.org' + str(target)
        elif defect == 'wrong-total':
            soup.select_one('[data-scope-pass-count]').string = '通过: 1/1'
        elif defect == 'stripped-structure':
            soup.select_one('meta[name="webstone-report"]').decompose()
            soup.select_one('[data-content-scope-switch]').decompose()
            soup.select_one('#run-log .log-summary').decompose()
        elif defect == 'missing-main':
            soup.main.decompose()
        else:
            soup.select_one('#run-log .log-summary').decompose()
        path.write_text(str(soup), encoding='utf-8')
    with pytest.raises(ValueError):
        check_content_scope_views(root)


def test_report_switching_preserves_delivered_html_bytes(tmp_path):
    import hashlib
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', '--no-report', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8'))
    relative = 'artifacts/fixture/article/capture.html'
    raw = b'<html lang="en"><head><meta name="webstone-report" content="1"></head><body><main>Captured page bytes.</main></body></html>'
    (run/relative).write_bytes(raw)
    row['provider_metadata'].update(rendered_html_path=relative, rendered_html_sha256=hashlib.sha256(raw).hexdigest())
    (run/'results.jsonl').write_text(json.dumps(row)+'\n', encoding='utf-8')
    result = invoke(tmp_path, 'report', run, language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    assert (run/'report'/relative).read_bytes() == raw
    assert (run/'report/content-scopes/full'/relative).read_bytes() == raw


def test_mixed_legacy_tasks_keep_their_report_without_fabricating_other_scores(tmp_path):
    case = annotated_case()
    case.update(name='Complete article body', content_scope='main')
    legacy = annotated_case()
    legacy['id'] = 'legacy'
    legacy.pop('content_scopes')
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'mixed', 'cases': [case, legacy]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in (run/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 2
    assert 'content_scope_metrics' not in next(row for row in rows if row['case_id']=='legacy')['metrics']
    assert not (run/'report/content-scope-views.json').exists()
    assert BeautifulSoup((run/'report/index.html').read_text(encoding='utf-8'), 'html.parser').select_one('[data-content-scope-switch]') is None


@pytest.mark.parametrize('delivered', [True, False])
def test_original_file_delivery_is_required_in_both_scopes(delivered):
    import base64
    import hashlib
    from lexbench_webfetch.evaluation import evaluate_response
    raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAE0lEQVR4nGP8//8/AwMDEwMYAAAkBgMBXaJOiAAAAABJRU5ErkJggg==')
    case = annotated_case()
    case.update(task_kind='file', content_score_applicable=False,
                source_verification={'mime': 'image/png', 'file_sha256': hashlib.sha256(raw).hexdigest()})
    for scope in ('main', 'full'):
        case['content_scopes'][scope]['requirements'] = {'task_prompt': 'Deliver the original image bytes.'}
    result = evaluate_response(case, b'An image caption is not the original file.', files=[raw] if delivered else [])
    for metrics in result['metrics']['content_scope_metrics'].values():
        assert task_passed(True, metrics) is delivered


def test_failed_request_cannot_pass_either_scope_on_matching_text():
    metrics = score_attempt(annotated_case(), b'Complete article body Contact publisher', ok=False)
    assert all(not task_passed(False, item) for item in metrics['content_scope_metrics'].values())


@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('defect', [None, 'number', 'evidence-link', 'localized-evidence', 'narrative'])
def test_complete_release_check_accepts_bilingual_scope_views(tmp_path, language, defect):
    from harness.web_fetch.report_build import (
        _render_content_scope_views, _wire_content_scope_views, _report_summary, copy_methodology_assets,
    )
    from harness.web_fetch.report_check import check
    from harness.web_fetch.reliability import assess_reliability
    from lexbench_webfetch.content_scopes import select_content_scope
    from tests.report_check_fixture import create_report
    root, authority = create_report(tmp_path, english=True)
    source = json.loads((root/'dataset.json').read_text(encoding='utf-8'))
    case = annotated_case()
    case.update(id='one', name='Sample task', track='web',
                source_verification=source['cases'][0]['source_verification'])
    for scope in ('main', 'full'):
        requirements = case['content_scopes'][scope]['requirements']
        requirements.update(min_content_chars=1, ground_truth_main_content='fixture',
                            required_facts=[{'id': 'body', 'alternatives': ['fixture']}])
        if scope == 'full':
            requirements['required_facts'].append({'id': 'footer', 'alternatives': ['Footer content']})
    selected, _ = select_content_scope(case, 'main')
    source['cases'] = [selected]
    source['content_scope'] = 'main'
    row = json.loads((root/'results.jsonl').read_text(encoding='utf-8'))
    row.update(provider_type='fixture', case_name='Sample task', category='synthetic', url=case['url'],
               run=1, elapsed_ms=1, started_at='2026-01-01T00:00:00Z', status_code=200,
               final_url=case['url'], title='Sample task', error_type=None, error_message=None,
               provider_metadata={}, metrics=score_attempt(case, b'fixture', ok=True, content_scope='main'))
    (root/'dataset.json').write_text(json.dumps(source), encoding='utf-8')
    (root/'results.jsonl').write_text(json.dumps(row)+'\n', encoding='utf-8')
    (root/'summary.json').write_text(json.dumps(_report_summary(source, [row])), encoding='utf-8')
    (root/'reliability.json').write_text(json.dumps(assess_reliability(source, [row])), encoding='utf-8')
    copy_methodology_assets(root)
    manifests = json.loads((root/'execution.json').read_text(encoding='utf-8'))['batches']
    _render_content_scope_views(root, source, [row], manifests, include_english=True,
                               language=language, authored_interpretation=False)
    _wire_content_scope_views(root)
    assert check(root, authority_root=authority).startswith('PASS:')
    if defect:
        page = root/('content-scopes/full/en/datasets/one.html' if defect == 'localized-evidence'
                     else 'content-scopes/full/en/guide/index.html')
        original = page.read_text(encoding='utf-8')
        soup = BeautifulSoup(original, 'html.parser')
        if defect in {'number', 'narrative'}:
            extra = '123456789' if defect == 'number' else '这段报告文字没有翻译'
            changed = original.replace('</p>', ' '+extra+'</p>', 1)
        else:
            link = (next(a for a in soup.select('a[href]') if a['href'].endswith('references/one.txt'))
                    if defect == 'localized-evidence' else soup.select_one('main a[href]'))
            target = '../references/one.txt' if defect == 'localized-evidence' else '../../index.html'
            changed = original.replace('href="'+link['href']+'"', 'href="'+target+'"', 1)
            if defect == 'localized-evidence':
                wrong = root/'content-scopes/full/en/references/one.txt'
                wrong.parent.mkdir(parents=True, exist_ok=True)
                wrong.write_text('Different reference', encoding='utf-8')
        assert changed != original
        page.write_text(changed, encoding='utf-8')
        from harness.web_fetch.report_check import check_report_language_parity
        with pytest.raises(ValueError, match='parity|untranslated'):
            check_report_language_parity(root/'content-scopes/full', source, canonical_root=root)
        with pytest.raises(ValueError, match='parity|untranslated'):
            check(root, authority_root=authority)


def test_each_failed_scope_displays_its_own_missing_requirements(tmp_path):
    case = annotated_case()
    case['name'] = 'Unrelated ordinary text'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8'))
    for relative in ['.', 'content-scopes/full']:
        page = run/'report'/relative/'attempts'/f'{row["attempt_id"]}.html'
        soup = BeautifulSoup(page.read_text(encoding='utf-8'), 'html.parser')
        explanation = next(tr.select('td')[-1].get_text() for tr in soup.select('#overview details tr')
                           if any(td.get_text() == '判定依据' for td in tr.select('td')))
        assert 'body' in explanation
        assert ('footer' in explanation) is (relative != '.')


@pytest.mark.parametrize('excluded', [True, False])
def test_scope_views_share_population_exclusions(excluded):
    from lexbench_webfetch.content_scopes import metrics_for_scope
    from lexbench_webfetch.metrics import task_measured
    metrics = score_attempt(annotated_case(), b'Complete article body Contact publisher', ok=True)
    if excluded:
        metrics.update(evaluation_excluded=True, evaluation_exclusion_reason='Unavailable reference')
    else:
        for selected in metrics['content_scope_metrics'].values():
            selected.update(evaluation_excluded=True, evaluation_exclusion_reason='Superseded exclusion')
    for scope in ('main', 'full'):
        selected = metrics_for_scope(metrics, scope)
        assert task_measured(selected) is not excluded
        assert selected.get('evaluation_exclusion_reason') == metrics.get('evaluation_exclusion_reason')
    row = dict(provider='fixture', provider_type='fixture', case_id='article', case_name='Article',
               category='synthetic', ok=True, elapsed_ms=100, metrics=metrics)
    summary = summarize([row])
    for scope in ('main', 'full'):
        assert summary['content_scopes'][scope]['providers']['fixture']['task_evaluated_attempts'] == int(not excluded)


def test_scope_controls_use_evaluated_population(tmp_path):
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', '--no-report', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    path = run/'results.jsonl'
    row = json.loads(path.read_text(encoding='utf-8'))
    row['metrics'].update(evaluation_excluded=True, evaluation_exclusion_reason='Unavailable reference')
    path.write_text(json.dumps(row)+'\n', encoding='utf-8')
    result = invoke(tmp_path, 'report', run, language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    for view in (run/'report', run/'report/content-scopes/full'):
        soup = BeautifulSoup((view/'index.html').read_text(encoding='utf-8'), 'html.parser')
        assert soup.select_one('[data-scope-pass-count]').get_text(strip=True) == '通过: 0/0'


@pytest.mark.parametrize('full_schema', ['named_link', 'body_text'])
def test_scope_download_keeps_its_own_title_assessment(tmp_path, full_schema):
    from harness.web_fetch.title_quality import evaluate_titles
    case = annotated_case()
    case.update(name='[Alpha title](https://example.test/item/1)\n[Beta title](https://example.test/item/2)',
                track='challenge', task_kind='structure')
    for scope in ('main', 'full'):
        case['content_scopes'][scope]['requirements'] = {
            'task_prompt': 'Return the required item titles and links.',
            'acceptance_contract': {'version': 1, 'schema': 'named_link', 'minimum_records': 1,
                                    'hosts': ['example.test'], 'entry_path': r'/item/\d+',
                                    'record_name_exclusions': ['Beta title'] if scope == 'main' else []}}
    if full_schema == 'body_text':
        case['content_scopes']['full']['requirements']['acceptance_contract'] = {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'body', 'alternatives': ['Alpha title']}]}
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', '--no-report', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    reference = {'case_id': 'article', 'items': [
        {'title': 'Alpha title', 'url': 'https://example.test/item/1'},
        {'title': 'Beta title', 'url': 'https://example.test/item/2'}]}
    (run/'list-references').mkdir(exist_ok=True)
    (run/'list-references/article.json').write_text(json.dumps(reference), encoding='utf-8')
    from harness.web_fetch.title_quality import add_title_quality
    from lexbench_webfetch.content_scopes import select_content_scope
    saved = run/'results.jsonl'
    row = json.loads(saved.read_text(encoding='utf-8'))
    add_title_quality(row, select_content_scope(case, 'main')[0], run)
    saved.write_text(json.dumps(row)+'\n', encoding='utf-8')
    result = invoke(tmp_path, 'report', run, language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    for scope, relative, count in [('main', '.', 1), ('full', 'content-scopes/full', 2)]:
        view = run/'report'/relative
        row = json.loads((view/'results.jsonl').read_text(encoding='utf-8'))
        if scope == 'full' and full_schema == 'body_text':
            assert 'title_quality' not in row
            continue
        expected = evaluate_titles(row['metrics']['stable_records'], reference)
        assert expected['returned_titles'] == count, scope
        assert row['title_quality']['checks'] == expected['checks']
        assert row['title_quality']['returned_titles'] == count
        soup = BeautifulSoup((view/'index.html').read_text(encoding='utf-8'), 'html.parser')
        coverage = soup.select_one('#ranking-challenge-titles tbody tr td:last-child')
        assert coverage.get_text(strip=True).startswith(f'{count}/{count}')
    assert (run/'report/results.jsonl').read_bytes() == saved.read_bytes()


@pytest.mark.parametrize('scope,passed', [('main', True), ('full', False)])
def test_selected_scope_scores_only_its_contract(scope, passed):
    metrics = score_attempt(annotated_case(), b'Complete article body', ok=True, scoring_scope=scope)
    assert metrics['evaluation_content_scope'] == scope
    assert task_passed(True, metrics) is passed
    assert 'content_scope_metrics' not in metrics


@pytest.mark.parametrize('scope', ['main', 'full', 'both'])
def test_run_and_offline_rescore_can_select_one_scope_or_both(tmp_path, scope):
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', '--score-scope', scope, language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8'))
    assert bool(row['metrics'].get('content_scope_metrics')) is (scope == 'both')
    assert (run/'report/content-scope-views.json').exists() is (scope == 'both')
    if scope != 'both':
        assert row['metrics']['evaluation_content_scope'] == scope
    calls = {p: p.read_bytes() for p in (run/'call-logs').rglob('*.json')}
    other = 'full' if scope != 'full' else 'main'
    result = invoke(tmp_path, 'rescore', run, '--dataset', dataset, '--score-scope', other, language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8'))
    assert row['metrics']['evaluation_content_scope'] == other
    assert 'content_scope_metrics' not in row['metrics']
    assert {p: p.read_bytes() for p in (run/'call-logs').rglob('*.json')} == calls
    saved_results = (run/'results.jsonl').read_bytes()
    altered = json.loads(dataset.read_text(encoding='utf-8'))
    altered['cases'][0]['url'] = 'https://different.example.test/task'
    dataset.write_text(json.dumps(altered), encoding='utf-8')
    result = invoke(tmp_path, 'rescore', run, '--dataset', dataset, '--score-scope', 'main', language='zh')
    assert result.returncode != 0
    assert 'retrieval task' in result.stderr
    assert (run/'results.jsonl').read_bytes() == saved_results


def test_scopes_do_not_infer_one_verdict_from_the_other():
    case = annotated_case()
    for scope, text in [('main', 'Required core record'), ('full', 'Required full record')]:
        case['content_scopes'][scope]['requirements'].update(
            min_content_chars=1, required_facts=[{'id': scope, 'alternatives': [text]}])
    metrics = score_attempt(case, b'Required full record', ok=True)
    assert not task_passed(True, metrics['content_scope_metrics']['main'])
    assert task_passed(True, metrics['content_scope_metrics']['full'])


def test_explicit_both_rejects_missing_annotations_instead_of_silently_scoring_one():
    case = annotated_case()
    case.pop('content_scopes')
    with pytest.raises(ValueError, match='paired task annotations'):
        score_attempt(case, b'Complete article body', ok=True, scoring_scope='both')
    assert 'content_scope_metrics' not in score_attempt(case, b'Complete article body', ok=True)


def test_all_contestants_switch_scoring_together_without_adding_scope_rows(tmp_path):
    names = ['method-a', 'method-b', 'method-c', 'method-d']
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'multi-contestant', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    config = tmp_path/'providers.json'
    config.write_text(json.dumps({'providers': {name: {'type': 'fixture', 'fail_cases': ['article'] if name == 'method-d' else []} for name in names}}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--provider-config', config,
                    '--providers', ','.join(names), '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '0', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    for scope, relative in [('main', '.'), ('full', 'content-scopes/full')]:
        view = run/'report'/relative
        soup = BeautifulSoup((view/'index.html').read_text(encoding='utf-8'), 'html.parser')
        table = soup.select_one('#ranking-web')
        headers = [cell.get_text(strip=True) for cell in table.select('thead th')]
        column = next(i for i, label in enumerate(headers) if label.startswith('任务通过率'))
        rows = table.select('tbody tr')
        assert len(rows) == len(names)
        actual = {}
        for row in rows:
            link = row.select_one('a[href*="providers/"]')
            name = link['href'].rsplit('/', 1)[-1].removesuffix('.html')
            actual[name] = float(row.select('td')[column]['data-sort'])
        assert set(actual) == set(names)
        assert actual == {name: int(scope == 'main' and name != 'method-d') for name in names}
        assert soup.select_one('[data-content-scope-switch]')['data-active-scope'] == scope
        saved = [json.loads(line) for line in (view/'results.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(saved) == len(names)
        assert {row['provider'] for row in saved} == set(names)
    assert len(list((run/'call-logs').rglob('*.json'))) == len(names)
