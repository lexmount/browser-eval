"""One acquisition supplies two acceptance views of the same persisted response."""
import json

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




def test_scope_summary_reuses_deadline_evidence_for_both_verdicts():
    metrics = score_attempt(annotated_case(), b'Complete article body Contact publisher', ok=True)
    metrics['deadline_overrun'] = True
    row = dict(provider='fixture', provider_type='fixture', case_id='article', case_name='Article', category='synthetic', ok=True, elapsed_ms=100, metrics=metrics)
    summary = summarize([row])
    assert all(summary['content_scopes'][scope]['providers']['fixture']['task_pass_rate'] == 0 for scope in ('main', 'full'))






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

    previous = (run/'report/index.html').read_bytes()
    result = invoke(tmp_path, 'report', run, '--content-scope', 'main', language='zh')
    assert result.returncode != 0
    assert 'annotated main-content requirements for every task' in result.stderr
    assert (run/'report/index.html').read_bytes() == previous


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
    assert not (run/'report/content-scope-views.json').exists()
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
    for selection in ('main', 'full', 'both'):
        result = invoke(tmp_path, 'rescore', run, '--dataset', dataset, '--score-scope', selection, language='zh')
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




def test_offline_scope_changes_preserve_acquisition_and_accept_annotation_corrections(tmp_path):
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    data = {'tool': 'web_fetch', 'id': 'acquisition', 'content_scope': 'full', 'cases': [case]}
    dataset.write_text(json.dumps(data), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--delay', '0', '--max-failure-retries', '1', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    original = json.loads((run/'manifest.json').read_text(encoding='utf-8'))['acquisition']
    calls = {path: path.read_bytes() for path in (run/'call-logs').rglob('*.json')}
    for args in [('--score-scope', 'main'), ('--score-scope', 'full'),
                 ('--score-scope', 'both'), ('--content-scope', 'full')]:
        result = invoke(tmp_path, 'rescore', run, '--dataset', dataset, '--no-report', *args, language='zh')
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads((run/'manifest.json').read_text(encoding='utf-8'))['acquisition'] == original
    data['cases'][0]['content_scopes']['main']['requirements']['required_facts'][0]['alternatives'] = ['Different required fact']
    dataset.write_text(json.dumps(data), encoding='utf-8')
    result = invoke(tmp_path, 'rescore', run, '--dataset', dataset, '--no-report', '--score-scope', 'main', language='zh')
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8'))
    assert not task_passed(row['ok'], row['metrics'])
    assert {path: path.read_bytes() for path in (run/'call-logs').rglob('*.json')} == calls
    assert not (run/'report/content-scopes').exists()
    assert json.loads((run/'manifest.json').read_text(encoding='utf-8'))['acquisition'] == original


def test_suite_list_identifies_overlapping_annotation_cohorts(monkeypatch, capsys):
    from lexbench_webfetch import cli, benchmark
    rows = [{'id': 'original', 'dataset_id': 'original', 'case_count': 550},
            {'id': 'annotated', 'dataset_id': 'annotated', 'case_count': 530,
             'kind': 'annotation_cohort', 'source_population': 'original'}]
    monkeypatch.setattr(benchmark, 'registry', lambda: {'suites': rows})
    monkeypatch.setattr('sys.argv', ['lexbench-webfetch', 'list'])
    cli.main()
    listed = json.loads(capsys.readouterr().out)
    assert listed == rows


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_one_acquisition_builds_only_the_selected_report(tmp_path, language):
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool': 'web_fetch', 'id': 'paired', 'content_scope': 'main', 'cases': [case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    names = ['method-a', 'method-b', 'method-c', 'method-d']
    config = tmp_path/'providers.json'
    config.write_text(json.dumps({'providers': {name: {'type': 'fixture'} for name in names}}), encoding='utf-8')
    result = invoke(tmp_path, 'run', '--dataset', dataset, '--output', run, '--env-file', env,
                    '--provider-config', config, '--providers', ','.join(names),
                    '--delay', '0', '--max-failure-retries', '0', language=language)
    assert result.returncode == 0, result.stdout + result.stderr
    original = (run/'results.jsonl').read_bytes()
    assert len(original.splitlines()) == 4
    assert all(set(json.loads(row)['metrics']['content_scope_metrics']) == {'main','full'} for row in original.splitlines())
    for scope, args in [('full', []), ('main', ['--content-scope','main']), ('full', ['--content-scope','full'])]:
        result = invoke(tmp_path, 'report', run, *args, language=language)
        assert result.returncode == 0, result.stdout + result.stderr
        report = run/'report'
        assert not (report/'content-scopes').exists()
        assert not (report/'content-scope-views.json').exists()
        selected = json.loads((report/'dataset.json').read_text(encoding='utf-8'))
        assert selected['content_scope'] == scope
        rows = [json.loads(line) for line in (report/'results.jsonl').read_text(encoding='utf-8').splitlines()]
        assert {row['provider'] for row in rows} == set(names)
        assert all(task_passed(row['ok'],row['metrics']) == (scope == 'main') for row in rows)
        home = BeautifulSoup((report/'index.html').read_text(encoding='utf-8'), 'html.parser')
        labels = [a.get_text() for a in home.select('#ranking-web a[href*="providers/"]')]
        assert len(labels) == len(set(labels)) == 4
        for row in rows:
            page = report/'attempts'/f'{row["attempt_id"]}.html'
            soup = BeautifulSoup(page.read_text(encoding='utf-8'), 'html.parser')
            assert not soup.select('[data-content-scope], [data-content-scope-switch]')
            assert ('Contact publisher' in soup.select_one('#evidence-reference').get_text()) == (scope == 'full')
        assert (run/'results.jsonl').read_bytes() == original
    assert len(list((run/'call-logs').rglob('*.json'))) == 4
    original_report = (run/'report/index.html').read_bytes()
    frozen = json.loads((run/'dataset.json').read_text(encoding='utf-8'))
    frozen['cases'][0]['content_scopes']['full']['requirements']['required_facts'][0]['alternatives'] = ['Changed requirement']
    (run/'dataset.json').write_text(json.dumps(frozen), encoding='utf-8')
    result = invoke(tmp_path, 'report', run, language=language)
    assert result.returncode != 0
    assert 'selected requirements' in result.stderr
    assert (run/'results.jsonl').read_bytes() == original
    assert (run/'report/index.html').read_bytes() == original_report


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
    original = saved.read_bytes()
    for scope, count in [('main', 1), ('full', 2)]:
        result = invoke(tmp_path, 'report', run, '--content-scope', scope, language='zh')
        assert result.returncode == 0, result.stdout + result.stderr
        view = run/'report'
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
    assert saved.read_bytes() == original


def test_scope_report_validates_active_edition_and_retains_acquisition_edition(tmp_path):
    from copy import deepcopy
    from harness.record_io import file_sha256
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.report_version import check_release
    from lexbench_webfetch.content_scopes import select_content_scope
    from lexbench_webfetch.versions import freeze_release
    dataset = tmp_path/'dataset.json'
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset.write_text(json.dumps({'tool':'web_fetch','id':'paired','content_scope':'main','cases':[case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path,'run','--dataset',dataset,'--output',run,'--env-file',env,
                    '--delay','0','--max-failure-retries','0','--no-report',language='zh')
    assert result.returncode == 0, result.stdout+result.stderr
    original_results = (run/'results.jsonl').read_bytes()
    original_dataset = (run/'dataset.json').read_bytes()
    acquisition = run/'benchmark-input-release.json'
    freeze_release(run/'dataset.json',acquisition,benchmark='scope-fixture',version='1.0.0')
    (run/'benchmark-release.json').write_bytes(acquisition.read_bytes())
    binding = {'path':acquisition.name,'sha256':file_sha256(acquisition)}
    (run/'execution.json').write_text(json.dumps({'batches':[json.loads((run/'manifest.json').read_text(encoding='utf-8'))],
                                                 'aggregate':{'benchmark_release':binding}}), encoding='utf-8')
    result = invoke(tmp_path,'report',run,'--content-scope','main',language='zh')
    assert result.returncode == 0, result.stdout+result.stderr
    previous = (run/'report/index.html').read_bytes()
    result = invoke(tmp_path,'report',run,'--content-scope','full',language='zh')
    assert result.returncode != 0
    assert 'measurement contract changed' in result.stderr
    assert (run/'report/index.html').read_bytes() == previous
    # A separately bound full-content edition is valid; its input edition stays main.
    full = deepcopy(json.loads(original_dataset))
    full['content_scope'] = 'full'
    full['cases'] = [select_content_scope(c,'full')[0] for c in full['cases']]
    full_dataset = tmp_path/'full-dataset.json'
    full_dataset.write_text(json.dumps(full), encoding='utf-8')
    full_release = tmp_path/'full-release.json'
    freeze_release(full_dataset,full_release,benchmark='scope-fixture-full',version='1.0.0')
    profile = tmp_path/'profile.json'
    profile.write_text(json.dumps({'benchmark_release':{'path':full_release.name,'sha256':file_sha256(full_release)}}), encoding='utf-8')
    report = tmp_path/'full-report'
    build_report(run,report,diagnostic_preview=True,language='zh',profile_path=profile)
    selected = json.loads((report/'dataset.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (report/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    check_release(report,selected,rows,profile=profile)
    assert (report/acquisition.name).read_bytes() == acquisition.read_bytes()
    assert (report/'benchmark-release.json').read_bytes() == full_release.read_bytes()
    assert (run/'results.jsonl').read_bytes() == original_results
    assert (run/'dataset.json').read_bytes() == original_dataset


@pytest.mark.parametrize('scope', ['main', 'full'])
def test_explicit_run_scope_selects_one_report_and_both_scores(tmp_path, scope):
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'tool':'web_fetch','id':'paired','cases':[case]}), encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('', encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path,'run','--dataset',dataset,'--output',run,'--env-file',env,
                    '--delay','0','--max-failure-retries','0','--content-scope',scope,language='zh')
    assert result.returncode == 0, result.stdout+result.stderr
    row = json.loads((run/'results.jsonl').read_text(encoding='utf-8'))
    assert set(row['metrics']['content_scope_metrics']) == {'main','full'}
    assert json.loads((run/'report/dataset.json').read_text(encoding='utf-8'))['content_scope'] == scope
    assert len(list((run/'call-logs').rglob('*.json'))) == 1
    assert not (run/'report/content-scopes').exists()
    result = invoke(tmp_path,'rescore',run,'--dataset',dataset,'--score-scope','both',
                    '--content-scope',scope,language='zh')
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads((run/'report/dataset.json').read_text(encoding='utf-8'))['content_scope'] == scope
    assert len(list((run/'call-logs').rglob('*.json'))) == 1


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_report_rebuild_keeps_scope_and_discloses_original_retry_policy(tmp_path, language):
    from harness.web_fetch.report_version import check_release
    dataset = tmp_path/'dataset.json'
    case = annotated_case()
    case['name'] = 'Complete article body'
    dataset.write_text(json.dumps({'tool':'web_fetch','id':'paired','cases':[case]}),encoding='utf-8')
    env = tmp_path/'empty.env'
    env.write_text('',encoding='utf-8')
    run = tmp_path/'run'
    result = invoke(tmp_path,'run','--dataset',dataset,'--output',run,'--env-file',env,
                    '--delay','0','--max-failure-retries','1','--content-scope','full',language=language)
    assert result.returncode == 0, result.stdout+result.stderr
    result = invoke(tmp_path,'report',run,'--content-scope','main',language=language)
    assert result.returncode == 0, result.stdout+result.stderr
    before = (run/'results.jsonl').read_bytes()
    calls = {p:p.read_bytes() for p in (run/'call-logs').rglob('*.json')}
    result = invoke(tmp_path,'report',run,language=language)
    assert result.returncode == 0, result.stdout+result.stderr
    report = run/'report'
    selected = json.loads((report/'dataset.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (report/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest = json.loads((report/'report-manifest.json').read_text(encoding='utf-8'))
    assert manifest['content_scope'] == selected['content_scope'] == 'main'
    assert manifest['retry_acceptance_scopes'] == ['full']
    check_release(report,selected,rows)
    edition = report/'en' if language == 'en' else report
    for path in [edition/'index.html', edition/'attempts'/f'{rows[0]["attempt_id"]}.html']:
        original = path.read_text(encoding='utf-8')
        soup = BeautifulSoup(original,'html.parser')
        assert soup.select_one('[data-report-content-scope="main"]')
        assert soup.select_one('[data-retry-acceptance-scopes="full"]')
        label = 'Scoring: main content' if language == 'en' else '评分范围：主要内容'
        assert label in original
        for bad in [original.replace(label, 'Wrong visible scoring scope'),
                    original.replace('data-report-content-scope="main"','data-report-content-scope="full"'),
                    original.replace('data-report-content-scope="main"','data-wrong-scope="main"'),
                    original.replace('data-retry-acceptance-scopes="full"','data-retry-acceptance-scopes="main"')]:
            path.write_text(bad,encoding='utf-8')
            with pytest.raises(ValueError,match='scope|retry policy'):
                check_release(report,selected,rows)
        path.write_text(original,encoding='utf-8')
    assert (run/'results.jsonl').read_bytes() == before
    assert {p:p.read_bytes() for p in (run/'call-logs').rglob('*.json')} == calls
    assert not (report/'content-scopes').exists()
