"""Offline score revisions retain the exact saved call population and predecessors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.local_revision import revise_local_snapshot
from harness.web_fetch.report_saved_matrix import build_saved_matrix, check_saved_matrix
from lexbench_webfetch.scoring import score_attempt
from tests.test_saved_matrix_evidence import fixture, write


def revision_fixture(tmp_path, *, multiple=False):
    spec, source, row, _ = fixture(tmp_path)
    case = json.loads((source/'dataset.json').read_text())['cases'][0]
    case['required_facts'] = [{'id':'shell', 'alternatives':['Subscribe']}]
    spec['suites'][0]['dataset'] = write(source/'dataset.json', {'cases':[case]})
    row['metrics'] = score_attempt(case, b'answer', ok=True)
    (source/'results.jsonl').write_text(json.dumps(row)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    if multiple:
        spec['suites'].append({**spec['suites'][0], 'id':'second', 'label':'Second'})
        spec['sources'].append({**spec['sources'][0], 'suite':'second'})
    write(tmp_path/'spec.json', spec)
    revision = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revision').parent
    dataset = json.loads((revision/'dataset.json').read_text())
    changed = deepcopy(dataset['cases'][0])
    changed['required_facts'] = []
    config = revision/'config.json'
    write(config, {'cases':[changed], 'rule_revision':True,
        'authority':{'description':'Remove independently identified subscription control'},
        'regression_ids':['article-primary-scope-complete-without-shell',
                          'article-primary-scope-shell-cannot-replace-body']})
    revise_local_snapshot(revision, config)
    receipt = next((revision/'local-evidence/offline-revisions').glob('*/receipt.json'))
    spec['offline_revision'] = {'root':str(revision),
        'receipt':{'path':str(receipt),'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}}
    write(tmp_path/'spec.json', spec)
    return spec, source, revision


def test_saved_revision_preserves_calls_and_exposes_original_scores(tmp_path):
    spec, source, revision = revision_fixture(tmp_path)
    raw = (source/'results.jsonl').read_bytes()
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    matrix = json.loads((output/'matrix.json').read_text())
    assert matrix['totals']['passed'] == 1
    row = matrix['rows'][0]
    assert (output/row['score_revision']['before_record_path']).read_bytes() == raw
    assert (source/'results.jsonl').read_bytes() == raw
    assert json.loads((output/row['record_path']).read_text())['elapsed_ms'] == 12
    assert json.loads((output/'score-revision.json').read_text())['provider_calls_added'] == 0
    assert check_saved_matrix(output, spec_path=tmp_path/'spec.json')['scores_changed'] is True


@pytest.mark.parametrize('defect', ['answer', 'receipt', 'dataset'])
def test_saved_revision_rejects_tampered_evidence(tmp_path, defect):
    spec, source, revision = revision_fixture(tmp_path)
    if defect == 'answer':
        (revision/'evidence/first/method/same/artifacts/answer.txt').write_text('other answer')
    elif defect == 'receipt':
        Path(spec['offline_revision']['receipt']['path']).write_text('{}')
    else:
        dataset = json.loads((revision/'dataset.json').read_text())
        dataset['cases'][0]['url'] = 'https://other.test/'
        write(revision/'dataset.json', dataset)
    with pytest.raises(ValueError):
        build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised')


def test_revision_keeps_same_id_in_other_suite_unchanged(tmp_path):
    _, source, _ = revision_fixture(tmp_path, multiple=True)
    raw = (source/'results.jsonl').read_bytes()
    output = build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised').parent
    assert (output/'evidence/second/method/same/record.json').read_bytes() == raw
    rows = json.loads((output/'matrix.json').read_text())['rows']
    assert [row['passed'] for row in rows] == [True, False]
    review = (output/'review/index.html').read_text()
    assert 'score-before-record.json' in review and '修订前后证据' in review


def test_revision_rejects_different_frozen_call_even_with_rebound_source(tmp_path):
    spec, source, _ = revision_fixture(tmp_path)
    row = json.loads((source/'results.jsonl').read_text())
    row['elapsed_ms'] = 99
    (source/'results.jsonl').write_text(json.dumps(row)+'\n')
    spec['sources'][0]['results']['sha256'] = hashlib.sha256((source/'results.jsonl').read_bytes()).hexdigest()
    write(tmp_path/'spec.json', spec)
    with pytest.raises(ValueError, match='predecessor result differs'):
        build_saved_matrix(tmp_path/'spec.json', tmp_path/'revised')
