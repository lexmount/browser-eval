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
                                  'absolute-switch', 'protocol-relative-switch', 'usage-changed', 'usage-removed'])
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
    check_content_scope_views(root)
    if defect == 'missing-view':
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
@pytest.mark.parametrize('defect', [None, 'number', 'evidence-link', 'narrative'])
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
        page = root/'content-scopes/full/en/guide/index.html'
        soup = BeautifulSoup(page.read_text(encoding='utf-8'), 'html.parser')
        if defect == 'number':
            tag = soup.new_tag('p')
            tag.string = '123456789'
            soup.main.append(tag)
        elif defect == 'evidence-link':
            soup.select_one('main a[href]')['href'] = '../../index.html'
        else:
            tag = soup.new_tag('p')
            tag.string = '这段报告文字没有翻译'
            soup.main.append(tag)
        page.write_text(str(soup), encoding='utf-8')
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
