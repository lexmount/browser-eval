"""Final rankings require real native evidence, not only selection-path checks."""
import hashlib
import json

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


def test_unchanged_groups_get_verified_and_public_settings(tmp_path):
    report = build(tmp_path, matrix(tmp_path)).parent
    model = json.loads((report/'matrix.json').read_text(encoding='utf-8'))
    assert len(model['execution_settings']) == 2
    assert all(group['uniform'] and group['verified_calls'] == 1 for group in model['execution_settings'])
    assert 'system' in (report/'methodology/index.html').read_text(encoding='utf-8')


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
        from pathlib import Path
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
