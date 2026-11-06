"""Full reviews and official comparisons reject mixed or incomplete evidence."""

import json
from pathlib import Path

import pytest

from harness.web_extract import pipeline as p
from harness.web_extract import report, review


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    home, run, work = tmp_path / 'home', tmp_path / 'run', tmp_path / 'review'
    source = home / 'dataset/markdown/one.md'
    source.parent.mkdir(parents=True)
    source.write_text('Amber mug: 12 EUR.', encoding='utf-8')
    (home / 'dataset/html').mkdir()
    (home / 'dataset/html/one.html').write_text('<pre>Amber mug: 12 EUR.</pre>', encoding='utf-8')
    schema = {'type': 'object', 'properties': {'name': {'type': 'string'}}}
    native = {'name': 'Amber mug'}
    p.save_json(home / 'dataset/manifest.json', {'benchmark': 'human-eval'})
    manifest_sha = p.digest((home / 'dataset/manifest.json').read_bytes())
    p.save_json(home / 'dataset/dataset/ground-truth.json', {
        'one': {'response': native, 'schema_text': json.dumps(schema)},
    })
    (home / 'dataset/evaluate.py').write_text(
        '# A synthetic external scorer dependency.\n', encoding='utf-8')
    row = {'method': 'native', 'case_id': 'one', 'normalized_fields': native,
           'http_status': 200, 'response': {'json': native}}
    contract = {'methods': ['native'], 'case_ids': ['one'],
                'kind': 'full_archived_extraction', 'pipeline_sha256': 'frozen-pipeline',
                'dataset_manifest_sha256': manifest_sha}
    p.save_json(run / 'manifest.json', {'synthetic_calls': ['one']})
    p.save_json(run / 'contract.json', contract)
    call_sha = p.digest((run / 'manifest.json').read_bytes())
    plan = tmp_path / 'plan.json'
    case_sha = p.digest(b'one\n')
    p.save_json(plan, {'case_count': 1, 'case_ids_sha256': case_sha,
                      'participants': {'native': {}}})
    plan_sha = p.digest(plan.read_bytes())
    p.save_json(run / 'certification-precall.json', {
        'profile_sha256': plan_sha, 'method': 'native', 'case_ids_sha256': case_sha,
        'dataset_manifest_sha256': manifest_sha, 'pipeline_sha256': 'frozen-pipeline'})
    score = {'population': 1, 'delivery_count': 1, 'mean': {}, 'per_case': {'one': {}}}
    p.save_json(run / 'scores.json', {'scores': {'native': score},
        'call_manifest_sha256': call_sha,
        'scorer_sha256': p.digest((home / 'dataset/evaluate.py').read_bytes())})
    p.save_json(run / 'certificate.json', {
        'manifest_sha256': call_sha, 'scores_sha256': p.digest((run / 'scores.json').read_bytes()),
        'precall_sha256': p.digest((run / 'certification-precall.json').read_bytes()),
        'profile_sha256': plan_sha, 'case_count': 1, 'method': 'native',
        'dataset_manifest_sha256': manifest_sha})
    field = {'field': 'name', 'status': 'supported', 'source_quote': 'Amber mug',
             'reason': 'The source names the mug.', 'findings': []}
    opinion = {'method': 'native', 'verdict': 'correct', 'summary': 'Correct mug.',
               'fields': [field], 'checked_paths': ['/name']}
    reference = {'checked_paths': ['/name'], 'fields': [field], 'summary': 'Supported mug.'}
    audit = {'review_scope': 'all', 'accepted_unreviewed_passes': 0,
             'dataset_manifest_sha256': manifest_sha,
             'results': {'native:one': {**opinion,
                                      'execution_identity': p.execution_identity(row)}},
             'reference_reviews': {'one': reference}}
    p.save_json(tmp_path / 'audit.json', audit)
    model = {'participants': {'native': {'label': 'Native'}},
             'audit': {'path': str(tmp_path / 'audit.json'),
                       'sha256': p.digest((tmp_path / 'audit.json').read_bytes())},
             'evidence_runs': [{'path': str(run), 'plan': str(plan), 'plan_sha256': plan_sha,
                 'scores': {'path': str(run / 'scores.json'),
                 'sha256': p.digest((run / 'scores.json').read_bytes())}}]}
    monkeypatch.setattr(p, 'validate_dataset', lambda *a, **kw: None)
    monkeypatch.setattr(report, 'validate_dataset', lambda *a, **kw: None)
    monkeypatch.setattr(p, 'load_run', lambda path: (contract, [row]))
    monkeypatch.setattr(report, 'load_run', lambda path: (contract, [row]))
    monkeypatch.setattr(report, 'score_rows', lambda *a, **kw: {'native': score})
    monkeypatch.setattr(review, 'completed', lambda path: (
        {'one': {'outputs': [{'method': 'native',
                            'execution_identity': p.execution_identity(row)}]}},
        {'one': {'reference': reference, 'outputs': [opinion]}}, [],
    ))
    p.save_json(work / 'manifest.json', {})
    p.save_json(work / 'reviewer.json', {'model': 'synthetic'})
    return home, run, work, model, contract


def assemble(evidence, tmp_path):
    home, _, work, model, _ = evidence
    content, profile = tmp_path / 'content.json', tmp_path / 'profile.json'
    p.save_json(content, model)
    p.save_json(profile, {'independent_result_review_scope': 'all',
        'research_report': {'content': {'sha256': p.digest(content.read_bytes())}}})
    return review.assemble_schema_audit(home, content, work, tmp_path / 'assembled.json',
                                        profile=profile)


@pytest.mark.parametrize('boundary', ['prepare', 'assemble', 'report'])
def test_same_case_ids_from_another_dataset_revision_are_rejected(evidence, tmp_path, boundary):
    home, _, work, model, contract = evidence
    contract['dataset_manifest_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='different dataset'):
        if boundary == 'prepare':
            content = tmp_path / 'content.json'
            p.save_json(content, model)
            review.prepare_schema_audit(home, content, work)
        elif boundary == 'assemble':
            assemble(evidence, tmp_path)
        else:
            report.schema_report_inputs(home, model)


@pytest.mark.parametrize('boundary', ['prepare', 'assemble'])
def test_full_audit_requires_every_declared_participant(evidence, tmp_path, boundary):
    home, _, work, model, _ = evidence
    model['participants']['unselected'] = {'label': 'Unselected'}
    with pytest.raises(ValueError, match='declared participant'):
        if boundary == 'prepare':
            content = tmp_path / 'content.json'
            p.save_json(content, model)
            review.prepare_schema_audit(home, content, work)
        else:
            assemble(evidence, tmp_path)
    assert not (tmp_path / 'assembled.json').exists()


@pytest.mark.parametrize('revision', ['reference_revision', 'projection_revision'])
def test_official_columns_reject_revised_reference_or_projection_scores(evidence, revision):
    home, run, _, model, _ = evidence
    assert report.schema_report_inputs(home, model)[2]['native']['population'] == 1
    scored = p.read_json(run / 'scores.json')
    scored[revision] = {}
    selected = run / 'scores-revised.json'
    p.save_json(selected, scored)
    model['evidence_runs'][0]['scores'] = {
        'path': str(selected), 'sha256': p.digest(selected.read_bytes())}
    with pytest.raises(ValueError, match='original-reference'):
        report.schema_report_inputs(home, model)


def test_report_revalidates_field_and_verdict_consistency(evidence):
    home, _, _, model, _ = evidence
    audit_path = Path(model['audit']['path'])
    audit = p.read_json(audit_path)
    audit['results']['native:one']['fields'][0]['status'] = 'incorrect'
    p.save_json(audit_path, audit)
    model['audit']['sha256'] = p.digest(audit_path.read_bytes())
    with pytest.raises(ValueError, match='contradicts'):
        report.schema_report_inputs(home, model)


@pytest.mark.parametrize('defect', ['missing_precall', 'precall_profile', 'certificate_method',
                                  'certificate_population', 'certificate_profile'])
def test_schema_report_requires_complete_original_certification(evidence, defect):
    home, run, _, model, _ = evidence
    assert report.schema_report_inputs(home, model)[2]['native']['population'] == 1
    if defect == 'missing_precall':
        (run / 'certification-precall.json').unlink()
    elif defect == 'precall_profile':
        path = run / 'certification-precall.json'
        value = p.read_json(path)
        value['profile_sha256'] = 'wrong'
        p.save_json(path, value)
    else:
        path = run / 'certificate.json'
        value = p.read_json(path)
        key = {'certificate_method': 'method', 'certificate_population': 'case_count',
               'certificate_profile': 'profile_sha256'}[defect]
        value[key] = 'wrong'
        p.save_json(path, value)
    with pytest.raises((ValueError, FileNotFoundError)):
        report.schema_report_inputs(home, model)
