"""Final rankings require real native evidence, not only selection-path checks."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_saved_matrix_evidence import build, write
from tests.test_saved_matrix_native_execution import execution, saved_answer


def matrix(tmp_path, *, other_dns='system', same_method=False):
    spec = dict(schema_version=1, title='Comparison', presentation='final',
                providers=[], suites=[], sources=[], missing=[])
    names = ['method'] if same_method else ['method', 'other']
    spec['providers'] = [dict(id=name, label=name) for name in names]
    suites = ['first', 'second'] if same_method else ['first']
    spec['report_families'] = [dict(id='web', label='Web')]
    for suite in suites:
        for name in names:
            dns = other_dns if name == 'other' or suite == 'second' else 'system'
            root = tmp_path/suite/name
            record, case, _ = execution(root, dict(type='tavily_extract',
                uniform_native_parameters=True, api_key='fixture', dns_mode=dns), dns=dns, name=name)
            answer = root/'answer.txt'
            answer.write_text('answer', encoding='utf-8')
            record.update(attempt_id=name+'-run', elapsed_ms=12, artifact_path='answer.txt',
                          metrics=dict(content_sha256=hashlib.sha256(answer.read_bytes()).hexdigest(),
                                       content_pass=True, request_ok=True, scoring_source_sha256='a'*64))
            saved_answer(root, record, case)
            dataset = write(root/'dataset.json', {'cases': [case]})
            if name == names[0]:
                spec['suites'].append(dict(id=suite, label=suite, dataset=dataset))
            spec['sources'].append(dict(suite=suite, provider=name, root=str(root),
                case_ids=[case['id']], results=write(root/'results.jsonl', record)))
    return spec


def split_matrix(tmp_path):
    """Retain two real native executions with a frozen remaining-retry launch."""
    spec = matrix(tmp_path)
    source = spec['sources'][0]
    current_root = Path(source['root'])
    current = json.loads((current_root/'results.jsonl').read_bytes())
    current['started_at'] = '2030-01-01T00:00:02+00:00'
    source['results'] = write(current_root/'results.jsonl', current)
    manifest = json.loads((current_root/'manifest.json').read_bytes())
    manifest['parameters']['max_failure_retries'] = 0
    write(current_root/'manifest.json', manifest)
    prior_root = tmp_path/'prior'
    prior, case, _ = execution(prior_root, dict(type='tavily_extract',
        uniform_native_parameters=True, api_key='fixture', dns_mode='system'), dns='system')
    saved_answer(prior_root, prior, case)
    prior.update(attempt_id='prior-run', elapsed_ms=12, started_at='2030-01-01T00:00:00+00:00')
    prior['metrics']['content_pass'] = False
    dataset = write(prior_root/'dataset.json', {'cases': [case]})
    prior_results = write(prior_root/'results.jsonl', prior)
    launch = write(tmp_path/'continuation.json', {
        'started_at': '2030-01-01T00:00:01+00:00', 'continuation_kind': 'remaining_one_retry',
        'case_ids': [case['id']], 'max_failure_retries': 0,
        'prior_physical_calls': {case['id']: 1}, 'prior_sources': {case['id']: str(prior_root)}})
    spec['retry_continuations'] = [{
        'suite': 'first', 'provider': 'method', 'case_id': case['id'],
        'current_record_sha256': source['results']['sha256'], 'launch_contract': launch,
        'prior_source': {'root': str(prior_root), 'results': prior_results, 'dataset': dataset}}]
    return spec, {'prior': prior_root, 'current': current_root}


def test_split_retry_uses_proved_remaining_and_original_budgets(tmp_path):
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    spec, _ = split_matrix(tmp_path)
    report = build(tmp_path, spec).parent
    group = next(item for item in json.loads((report/'matrix.json').read_bytes())['execution_settings']
                 if item['provider'] == 'method')
    assert group['uniform'] and group['calls'] == group['verified_calls'] == 2
    assert {setting['max_failure_retries'] for setting in group['settings']} == {1}
    assert check_saved_matrix(report, spec_path=tmp_path/'spec.json')['valid']


@pytest.mark.parametrize('role', ['prior', 'current'])
@pytest.mark.parametrize('budget', ['missing', -1, 'conflicting', True])
def test_split_retry_cannot_replace_unproved_manifest_budgets(tmp_path, role, budget):
    spec, roots = split_matrix(tmp_path)
    manifest_path = roots[role]/'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    if budget == 'missing':
        del manifest['parameters']['max_failure_retries']
    else:
        manifest['parameters']['max_failure_retries'] = (
            0 if role == 'prior' else 1) if budget == 'conflicting' else budget
    write(manifest_path, manifest)
    with pytest.raises(ValueError, match='uniform settings'):
        build(tmp_path, spec)
    spec['presentation'] = 'audit'
    report = build(tmp_path, spec).parent
    group = next(item for item in json.loads((report/'matrix.json').read_bytes())['execution_settings']
                 if item['provider'] == 'method')
    assert not group['uniform'] and group['errors']


@pytest.mark.parametrize(('baseline_history', 'conflicting_role'), [
    ('valid', None), ('valid', 'prior'), ('valid', 'current'),
    ('unexhausted', None), ('retry_after_pass', None), ('exhausted', None),
    ('scorer_change', None), ('missing_created', None), ('late_created', None)])
def test_parameter_selection_verifies_split_baseline_budgets(tmp_path, conflicting_role, baseline_history):
    from harness.web_fetch.report_saved_matrix import _load_saved_matrix, _parameter_reruns, _parameter_group_decisions
    spec, roots = split_matrix(tmp_path)
    spec['presentation'] = 'audit'
    root = tmp_path/'candidate'
    record, case, policy = execution(root, dict(type='tavily_extract',
        uniform_native_parameters=True, api_key='fixture', dns_mode='system'), dns='system')
    saved_answer(root, record, case)
    record['metrics']['content_pass'] = False
    record.update(attempt_id='candidate-run', elapsed_ms=12, started_at='2030-01-01T00:00:04+00:00')
    manifest = json.loads((root/'manifest.json').read_bytes())
    manifest['parameters']['max_failure_retries'] = 0
    write(root/'manifest.json', manifest)
    policy.update(max_failure_retries=0, created_at='2030-01-01T00:00:03+00:00',
        jobs=[{'id': 'job', 'suite': 'first', 'provider': 'method', 'case_ids': ['case']}])
    spec['parameter_reruns'] = {
        'selection': 'replace_family_if_pass_count_improves',
        'contract': write(tmp_path/'rerun-contract.json', policy),
        'sources': [{'job_id': 'job', 'root': str(root),
            'dataset': write(root/'dataset.json', {'cases': [case]}),
            'results': write(root/'results.jsonl', record)}]}
    if baseline_history in {'unexhausted', 'retry_after_pass', 'exhausted', 'scorer_change'}:
        from copy import deepcopy
        spec.pop('retry_continuations')
        source = spec['sources'][0]
        baseline = json.loads((roots['current']/'results.jsonl').read_bytes())
        baseline['metrics']['content_pass'] = baseline_history in {'retry_after_pass', 'scorer_change'}
        if baseline_history in {'retry_after_pass', 'exhausted'}:
            attempts = []
            for ordinal in (1, 2):
                receipt = deepcopy(baseline['provider_metadata']['native_route_receipt'])
                receipt['physical_attempt'] = ordinal
                attempts.append({'ok': True, 'task_pass': baseline_history == 'retry_after_pass',
                    'started_at': f'2030-01-01T00:00:0{ordinal}+00:00',
                    'elapsed_ms': baseline['elapsed_ms'],
                    'artifact_path': baseline['artifact_path'],
                    'content_sha256': baseline['metrics']['content_sha256'],
                    'native_route_receipt': receipt})
            baseline['provider_metadata']['native_route_receipt'] = attempts[-1]['native_route_receipt']
            baseline['provider_metadata']['retry'] = {
                'attempts': attempts, 'physical_attempts': 2, 'retries_used': 1}
        source['results'] = write(roots['current']/'results.jsonl', baseline)
        manifest = json.loads((roots['current']/'manifest.json').read_bytes())
        manifest['parameters']['max_failure_retries'] = 1
        write(roots['current']/'manifest.json', manifest)
    elif baseline_history in {'missing_created', 'late_created'}:
        path = roots['prior']/'manifest.json'
        manifest = json.loads(path.read_bytes())
        if baseline_history == 'missing_created':
            manifest.pop('created_at', None)
            manifest.pop('started_at', None)
        else:
            manifest['created_at'] = '2030-01-01T00:00:01+00:00'
        write(path, manifest)
    if conflicting_role:
        path = roots[conflicting_role]/'manifest.json'
        manifest = json.loads(path.read_bytes())
        manifest['parameters']['max_failure_retries'] = 2
        write(path, manifest)
    write(tmp_path/'spec.json', spec)
    inputs = _load_saved_matrix(tmp_path/'spec.json')
    if baseline_history == 'scorer_change':
        revised = deepcopy(inputs['selected'][('first', 'method', 'case')][1])
        revised['metrics']['content_pass'] = False
        inputs['revision'] = {'cases': {}, 'records': {('first', 'method', 'case'): revised}}
    if conflicting_role:
        with pytest.raises(ValueError, match='retry budget'):
            _parameter_reruns(inputs)
    else:
        candidates = _parameter_reruns(inputs)
        assert len(candidates) == 1
        settings = inputs['parameter_settings'][('first', 'method', 'case')]
        assert {value['max_failure_retries'] for value in settings['before']} == {1}
        assert {value['max_failure_retries'] for value in settings['candidate']} == {0}
        eligible = baseline_history in {'valid', 'exhausted', 'scorer_change'}
        assert settings['before_retry_policy_valid'] is eligible
        decision = _parameter_group_decisions(inputs, candidates)[('first', 'method', 'case')]
        assert decision['before_uniform'] is True
        assert decision['selected_new'] is not eligible


def test_unchanged_groups_get_verified_and_public_settings(tmp_path):
    report = build(tmp_path, matrix(tmp_path)).parent
    model = json.loads((report/'matrix.json').read_text(encoding='utf-8'))
    assert len(model['execution_settings']) == 2
    assert all(group['uniform'] and group['verified_calls'] == 1 for group in model['execution_settings'])
    assert 'system' in (report/'methodology/index.html').read_text(encoding='utf-8')
    for group in model['execution_settings']:
        assert (datetime.fromisoformat(group['collection_end']) -
                datetime.fromisoformat(group['collection_start'])).total_seconds() >= .012


@pytest.mark.parametrize('same_method', [False, True])
def test_dns_asymmetry_cannot_bypass_final_gate_without_reruns(tmp_path, same_method):
    spec = matrix(tmp_path, other_dns='public_doh', same_method=same_method)
    with pytest.raises(ValueError, match='uniform settings|equal DNS'):
        build(tmp_path, spec)
    assert not (tmp_path/'report').exists()
    spec['presentation'] = 'audit'
    report = build(tmp_path, spec).parent
    model = json.loads((report/'matrix.json').read_text(encoding='utf-8'))
    assert {s['dns_mode'] for g in model['execution_settings'] for s in g['settings']} == {'system', 'public_doh'}


def test_final_rejects_changed_prompt_and_post_outcome_selection():
    from harness.web_fetch.report_saved_matrix import _require_final_selection
    inputs = dict(spec={}, missing={}, cases={'suite': {'case': {'task_prompt': 'old'}}},
                  revision={'cases': {('suite', 'case'): {'task_prompt': 'new'}}})
    with pytest.raises(ValueError, match='Changed task contracts'):
        _require_final_selection(inputs)
    inputs['revision'] = None
    inputs['spec']['parameter_reruns'] = {'selection': 'replace_family_if_pass_count_improves'}
    with pytest.raises(ValueError, match='Post-outcome'):
        _require_final_selection(inputs)


def test_final_rejects_adapter_rejection_before_denominator_projection(tmp_path):
    from harness.web_fetch.report_saved_matrix import _saved_renderer_projection
    model = dict(presentation='final', rows=[dict(called=True, passed=False,
        record_path='record.json', status='adapter_rejected')])
    with pytest.raises(ValueError, match='denominator'):
        _saved_renderer_projection(tmp_path, model)


@pytest.mark.parametrize('mutation', ['negative_budget', 'missing_retry'])
def test_unchanged_groups_cannot_bypass_retry_policy(tmp_path, mutation):
    spec = matrix(tmp_path)
    for source in spec['sources']:
        root = Path(source['root'])
        if mutation == 'negative_budget':
            manifest = json.loads((root/'manifest.json').read_bytes())
            manifest['parameters']['max_failure_retries'] = -1
            write(root/'manifest.json', manifest)
        else:
            record = json.loads((root/'results.jsonl').read_bytes())
            record['metrics']['content_pass'] = False
            source['results'] = write(root/'results.jsonl', record)
    with pytest.raises(ValueError, match='uniform settings'):
        build(tmp_path, spec)


@pytest.mark.parametrize('mutation', ['coverage', 'errors', 'request', 'count'])
def test_portable_final_gate_rechecks_derived_settings(tmp_path, mutation):
    from harness.web_fetch.report_saved_matrix import _require_comparable_matrix
    report = build(tmp_path, matrix(tmp_path)).parent
    settings = json.loads((report/'matrix.json').read_bytes())['execution_settings']
    if mutation == 'coverage':
        settings.pop()
    elif mutation == 'errors':
        settings[0]['errors'] = ['unverified']
    elif mutation == 'count':
        settings[0]['verified_calls'] = 0
    else:
        from copy import deepcopy
        extra = deepcopy(settings[0]['settings'][0])
        extra['request']['new_option'] = True
        settings[0]['settings'].append(extra)
    with pytest.raises(ValueError, match='settings'):
        _require_comparable_matrix(settings, {('method', 'web'), ('other', 'web')})


def test_collection_end_includes_an_earlier_longer_call(tmp_path):
    spec = matrix(tmp_path, same_method=True)
    for source, start, elapsed in zip(spec['sources'],
            ['2030-01-01T00:00:00+00:00', '2030-01-01T00:00:01+00:00'], [30000, 1000]):
        path = Path(source['results']['path'])
        record = json.loads(path.read_bytes())
        record.update(started_at=start, elapsed_ms=elapsed)
        source['results'] = write(path, record)
    report = build(tmp_path, spec).parent
    model = json.loads((report/'matrix.json').read_bytes())
    assert model['execution_settings'][0]['collection_end'] == '2030-01-01T00:00:30+00:00'


@pytest.mark.parametrize('provenance', ['parameter_rerun', 'result_adoption', 'changed_prompt'])
def test_public_renderer_cannot_promote_post_outcome_audit(tmp_path, provenance):
    from harness.web_fetch.report import generate_saved_matrix
    spec = matrix(tmp_path)
    spec['presentation'] = 'audit'
    report = build(tmp_path, spec).parent
    model = json.loads((report/'matrix.json').read_bytes())
    model['presentation'] = 'final'
    row = model['rows'][0]
    if provenance == 'changed_prompt':
        case = json.loads((report/row['case_path']).read_bytes())
        write(report/'original-case.json', {**case, 'task_prompt': 'different original task'})
        row['score_revision'] = {'before_case_path': 'original-case.json'}
    else:
        row[provenance] = {'selected_new': True}
    with pytest.raises(ValueError, match='portable final report'):
        generate_saved_matrix(report, model)


def test_audit_exposes_settings_without_cost_column(tmp_path):
    from bs4 import BeautifulSoup
    spec = matrix(tmp_path, same_method=True)
    spec['presentation'] = 'audit'
    spec['sources'].pop()
    receipt = write(tmp_path/'interrupted.json', dict(suite='second', provider='method',
        case_id='case', state='called_but_uncommitted'))
    spec['missing'] = [dict(suite='second', provider='method', case_id='case',
        status='uncommitted', reason='Interrupted after dispatch', receipt=receipt)]
    report = build(tmp_path, spec).parent
    page = BeautifulSoup((report/'index.html').read_text(encoding='utf-8'), 'html.parser')
    table = page.select_one('#ranking-overall')
    assert '累计执行耗时' not in table.get_text()
    assert '12 ms' in table.get_text()
    methodology = BeautifulSoup((report/'methodology/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert methodology.select_one('#saved-execution-settings') is not None


@pytest.mark.parametrize('missing_manifest', [False, True])
def test_uniform_summaries_cannot_promote_unverified_native_evidence(tmp_path, missing_manifest):
    from copy import deepcopy
    from harness.web_fetch.report import generate_saved_matrix
    spec = matrix(tmp_path, other_dns='public_doh')
    spec['presentation'] = 'audit'
    if missing_manifest:
        (Path(spec['sources'][1]['root'])/'manifest.json').unlink()
    output = build(tmp_path, spec).parent
    model = json.loads((output/'matrix.json').read_bytes())
    model['presentation'] = 'final'
    # Forge the summary only; retain the actual missing/mixed native evidence.
    for summary in model['execution_settings']:
        summary.update(verified_calls=summary['calls'], errors=[], uniform=True,
                       settings=deepcopy(model['execution_settings'][0]['settings']))
    with pytest.raises(ValueError, match='source-backed admission'):
        generate_saved_matrix(output, model)


def test_generic_renderer_cannot_self_authorize_final_results(tmp_path):
    from harness.web_fetch.report import generate
    output = build(tmp_path, matrix(tmp_path)).parent
    dataset = json.loads((output/'dataset.json').read_bytes())
    records = [json.loads(line) for line in (output/'results.jsonl').read_bytes().splitlines()]
    with pytest.raises(ValueError, match='source-backed admission'):
        generate(output, dataset, records, [])


def test_final_checker_requires_original_sources(tmp_path):
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    output = build(tmp_path, matrix(tmp_path)).parent
    with pytest.raises(ValueError, match='original final source specification'):
        check_saved_matrix(output)
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['valid']


@pytest.mark.parametrize('presentation', ['audit', 'final'])
@pytest.mark.parametrize('combined', [False, True])
def test_saved_ranks_preserve_diagnostic_evidence_disclosure(tmp_path, presentation, combined):
    from bs4 import BeautifulSoup
    spec = matrix(tmp_path)
    spec['presentation'] = presentation
    spec['combined_ranking'] = combined
    output = build(tmp_path, spec).parent
    dataset = json.loads((output/'dataset.json').read_bytes())
    page = BeautifulSoup((output/'index.html').read_text(encoding='utf-8'), 'html.parser')
    diagnostic = presentation == 'audit'
    assert dataset['comparison']['presentation']['ranking'] == ('diagnostic' if diagnostic else 'saved_task_pass_rate')
    for selector in ['#ranking-overall', '#ranking-web']:
        table = page.select_one(selector)
        assert (table.get('data-row-index') is None) == (combined or selector != '#ranking-overall')
        assert table['data-dir'] == ('desc' if combined or selector != '#ranking-overall' else 'asc')
    assert bool(page.select_one('[data-evidence-state="saved_diagnostic"]')) is diagnostic
    if diagnostic:
        assert '名次按最终选用的结果和当前验收规则计算' in page.get_text()
        assert '未认证' in page.get_text()
        assert '不提供名次' not in page.get_text()
        if combined:
            assert '本轮任务通过率排名' in page.get_text()
        assert '任务判定汇总（非排名）' not in page.get_text()


@pytest.mark.parametrize('presentation,source_check', [('audit', False), ('audit', True), ('final', True)])
def test_checker_accepts_relative_report_root(tmp_path, monkeypatch, presentation, source_check):
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    spec = matrix(tmp_path)
    spec['presentation'] = presentation
    output = build(tmp_path, spec).parent
    monkeypatch.chdir(tmp_path)
    result = check_saved_matrix(Path(output.name),
        spec_path=Path('spec.json') if source_check else None)
    assert result['valid']


@pytest.mark.parametrize('linked_parent', [False, True])
def test_checker_rejects_symlinked_report_root(tmp_path, monkeypatch, linked_parent):
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    spec = matrix(tmp_path)
    spec['presentation'] = 'audit'
    output = build(tmp_path, spec).parent
    link = tmp_path/'linked'
    link.symlink_to(tmp_path if linked_parent else output, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='Symlinked'):
        check_saved_matrix(Path('linked')/output.name if linked_parent else Path('linked'))


@pytest.mark.parametrize('identity', ['b'*64, None, '', 'invalid', 1])
def test_final_rejects_mixed_or_unverifiable_selected_scorers(tmp_path, identity):
    spec = matrix(tmp_path)
    source = spec['sources'][1]
    path = Path(source['results']['path'])
    record = json.loads(path.read_bytes())
    if identity is None:
        record['metrics'].pop('scoring_source_sha256')
    else:
        record['metrics']['scoring_source_sha256'] = identity
    source['results'] = write(path, record)
    with pytest.raises(ValueError, match='one valid scoring identity'):
        build(tmp_path, spec, 'final')
    assert not (tmp_path/'final').exists()
    spec['presentation'] = 'audit'
    output = build(tmp_path, spec, 'audit').parent
    retained = [json.loads(line) for line in (output/'results.jsonl').read_bytes().splitlines()]
    assert retained[1]['metrics'].get('scoring_source_sha256') == identity


def test_final_accepts_uniform_historical_scoring_identity(tmp_path):
    spec = matrix(tmp_path)
    for source in spec['sources']:
        path = Path(source['results']['path'])
        record = json.loads(path.read_bytes())
        record['metrics']['scoring_source_sha256'] = 'b'*64
        source['results'] = write(path, record)
    output = build(tmp_path, spec).parent
    assert json.loads((output/'dataset.json').read_bytes())['comparison']['publication_state'] == 'final_saved_results'


@pytest.mark.parametrize('field', [
    'body_requirements', 'acceptance_contract', 'required_patterns', 'required_facts',
    'expected_resources', 'resource_output_contract', 'composite_acceptance',
    'forbidden_any', 'page_identity_required', 'reference_answer',
    'mutable_body_policy', 'acceptance_policy_version',
])
def test_same_prompt_does_not_hide_a_changed_task_requirement(tmp_path, field):
    from copy import deepcopy
    from harness.web_fetch.report import generate_saved_matrix
    from harness.web_fetch.report_saved_matrix import _require_final_selection
    before = {'task_prompt': 'Keep the original task', field: 'original requirement'}
    after = {**before, field: 'changed requirement'}
    inputs = {'spec': {}, 'missing': {}, 'cases': {'suite': {'case': before}},
              'revision': {'cases': {('suite', 'case'): after}}}
    with pytest.raises(ValueError, match='Changed task contracts'):
        _require_final_selection(inputs)
    spec = matrix(tmp_path)
    spec['presentation'] = 'audit'
    output = build(tmp_path, spec).parent
    model = json.loads((output/'matrix.json').read_bytes())
    model['presentation'] = 'final'
    row = model['rows'][0]
    current = json.loads((output/row['case_path']).read_bytes())
    original = deepcopy(current)
    original[field] = 'different original requirement'
    write(output/'before-case.json', original)
    row['score_revision_history'] = {'before_case_path': 'before-case.json'}
    with pytest.raises(ValueError, match='Changed task contracts'):
        generate_saved_matrix(output, model)


def test_diagnostic_annotations_do_not_change_task_acceptance():
    from harness.web_fetch.report_saved_matrix import _require_final_selection, _same_task_rules
    from lexbench_webfetch.scoring import score_attempt
    before = {'id': 'case', 'name': 'Case', 'url': 'https://example.test/',
              'category': 'web', 'expected_any': ['answer'], 'task_prompt': 'Return the answer'}
    after = {**before, 'diagnostic_any': ['answer']}
    assert _same_task_rules(before, after)
    old_metrics = score_attempt(before, b'answer', ok=True)
    new_metrics = score_attempt(after, b'answer', ok=True)
    assert old_metrics['diagnostic_hits'] == [] and new_metrics['diagnostic_hits'] == ['answer']
    assert old_metrics['content_pass'] == new_metrics['content_pass']
    _require_final_selection({'spec': {}, 'missing': {}, 'cases': {'suite': {'case': before}},
        'revision': {'cases': {('suite', 'case'): after}}})


def test_uncommitted_first_call_counts_in_native_settings_coverage(tmp_path):
    from bs4 import BeautifulSoup
    spec, _ = split_matrix(tmp_path)
    spec['presentation'] = 'audit'
    entry = spec['retry_continuations'][0]
    entry.pop('prior_source')
    started = '2030-01-01T00:00:00+00:00'
    event = {'event': 'http_request_started', 'call_id': 'method__case__r001', 'at': started}
    log = write(tmp_path/'uncommitted-log.jsonl', event)
    entry['prior_log'] = log
    entry['prior_uncommitted'] = write(tmp_path/'uncommitted.json', {
        'suite': 'first', 'provider': 'method', 'case_id': 'case',
        'state': 'called_but_uncommitted', 'elapsed_ms': None})
    entry['launch_contract'] = write(tmp_path/'uncommitted-launch.json', {
        'started_at': '2030-01-01T00:00:01+00:00', 'case_ids': ['case'], 'provider': 'method',
        'total_authorized_physical_calls_per_case': 2,
        'first_uncommitted': {'case_id': 'case', 'sha256': log['sha256']}})
    output = build(tmp_path, spec).parent
    model = json.loads((output/'matrix.json').read_bytes())
    settings = next(group for group in model['execution_settings'] if group['provider'] == 'method')
    assert (settings['verified_calls'], settings['calls']) == (1, 2)
    assert settings['collection_start'] == started
    assert settings['errors'] and settings['uniform'] is False
    row = next(row for row in model['rows'] if row['provider'] == 'method')
    assert row['retry_continuation']['total_physical_calls'] == 2
    assert row['retry_continuation']['execution_cost_ms'] is None
    page = BeautifulSoup((output/'methodology/index.html').read_text(encoding='utf-8'), 'html.parser')
    assert '1/2' in page.get_text()
    spec['presentation'] = 'final'
    with pytest.raises(ValueError, match='uniform settings'):
        build(tmp_path, spec, 'final')


@pytest.mark.parametrize('presentation', ['audit', 'final'])
@pytest.mark.parametrize('route', ['index.html', 'providers/method.html', 'attempt'])
@pytest.mark.parametrize('defect', ['remove', 'hide'])
def test_saved_disclosure_is_required_on_home_and_detail_routes(tmp_path, presentation, route, defect):
    from bs4 import BeautifulSoup
    from harness.web_fetch.report_saved_matrix import check_saved_matrix
    spec = matrix(tmp_path)
    spec['presentation'] = presentation
    root = build(tmp_path, spec).parent
    path = next((root/'attempts').glob('*.html')) if route == 'attempt' else root/route
    page = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    scope = page.select_one('[data-ranking-scope="saved-task-pass-rate"]')
    assert scope and '认证' in scope.get_text()
    if defect == 'remove':
        scope.decompose()
    else:
        scope['hidden'] = ''
    path.write_text(str(page), encoding='utf-8')
    # Rebinding bytes cannot turn a missing or hidden disclosure into valid UI.
    audit_path = root/'saved-matrix-audit.json'
    audit = json.loads(audit_path.read_bytes())
    audit['files'][path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    audit_path.write_text(json.dumps(audit), encoding='utf-8')
    with pytest.raises(ValueError, match='disclosure missing|Rendered page differs from validated source'):
        check_saved_matrix(root, spec_path=tmp_path/'spec.json' if presentation == 'final' else None)


@pytest.mark.parametrize('only_missing', [False, True])
@pytest.mark.parametrize('telemetry', ['receipt', 'events', 'invalid'])
def test_uncommitted_settings_keep_counts_and_observed_windows(tmp_path, only_missing, telemetry):
    spec = matrix(tmp_path, same_method=True)
    spec['presentation'] = 'audit'
    spec['sources'].pop()
    if only_missing:
        spec['sources'].clear()
        spec['suites'] = spec['suites'][1:]
    started, ended = '2020-01-01T00:00:00+00:00', '2030-01-01T00:00:00+00:00'
    receipt = dict(suite='second', provider='method', case_id='case', state='called_but_uncommitted')
    if telemetry == 'receipt':
        receipt.update(physical_calls=2, started_at=started, completed_at=ended)
    if telemetry == 'invalid':
        receipt.update(physical_calls=True, started_at='not a date', completed_at='2030-01-01')
    entry = dict(suite='second', provider='method', case_id='case', status='uncommitted',
                 reason='Lost result', receipt=write(tmp_path/'lost.json', receipt))
    if telemetry == 'events':
        events = [{'event':'http_request_started','call_id':'method__case__r001__p001','at':started},
                  {'event':'http_request_started','call_id':'method__case__r001__p002','at':ended},
                  {'event':'done','call_id':'method__case__r001__p002','completed_at':ended},
                  {'event':'http_request_started','call_id':'other__case__r001','at':'1990-01-01T00:00:00Z'}]
        entry['diagnostic_sources'] = [write(tmp_path/'events.json', events)]
    spec['missing'] = [entry]
    root = build(tmp_path, spec).parent
    settings = json.loads((root/'matrix.json').read_bytes())['execution_settings'][0]
    assert settings['calls'] == (1 if telemetry == 'invalid' else 2) + int(not only_missing)
    assert settings['verified_calls'] == int(not only_missing)
    assert settings['uniform'] is False
    assert 'Uncommitted invocation has no verified native settings' in settings['errors']
    if telemetry == 'invalid':
        assert 'Uncommitted call has an invalid collection timestamp' in settings['errors']
    else:
        assert settings['collection_start'] == started
        assert settings['collection_end'] == ended
