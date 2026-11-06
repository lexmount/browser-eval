"""New participants reuse completed reviews while new source claims remain gated."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.review import assemble, prepare, prepare_adjudication, read_packet
from tests.web_extract.test_repair_review import review_payload


def complete(work, manifest, *, claim=None):
    for entry in manifest['packets']:
        payload = review_payload(read_packet(Path(entry['path'])))
        if claim:
            for case in payload['cases']:
                case['reference']['sku'].update(claim)
        save_json(work / 'reviews' / Path(entry['path']).name, payload)
    save_json(work / 'reviewer.json', {'model': work.name})


def product_reviews(tmp_path):
    home = create(tmp_path / 'example')
    source = home / 'runs/example-v1'
    contract = read_json(source / 'contract.json')
    manifest = read_json(source / 'manifest.json')
    works = []
    selections = []
    for method in contract['methods']:
        run = home / 'runs' / method
        rows = []
        for entry in manifest['calls']:
            if read_json(source / entry['path'])['method'] != method:
                continue
            target = run / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / entry['path'], target)
            rows.append(entry)
        save_json(run / 'contract.json', {**contract, 'methods': [method]})
        save_json(run / 'manifest.json', {
            'contract_sha256': digest((run / 'contract.json').read_bytes()), 'calls': rows})
        content = home / (method + '-content.json')
        save_json(content, {'evidence_runs': [{'run': str(run)}]})
        work = tmp_path / ('review-' + method)
        plan = prepare(home, content, work)
        complete(work, plan, claim={'status': 'ambiguous'})
        works.append(work)
        selections.append({'run': str(run)})
    content = home / 'combined.json'
    save_json(content, {'evidence_runs': selections})
    adjudication = tmp_path / 'old-adjudication'
    plan = prepare_adjudication(works[0], adjudication, home / 'profile.json')
    complete(adjudication, plan)
    empty = tmp_path / 'empty'
    save_json(empty / 'manifest.json', {
        'bindings': {}, 'packets': [], 'case_count': 0, 'result_count': 0})
    return home, content, works, adjudication, empty


def test_disjoint_participants_reuse_the_existing_reference_decision_and_old_review(tmp_path):
    home, content, works, adjudication, empty = product_reviews(tmp_path)
    originals = {path: path.read_bytes() for work in works for path in work.rglob('*.json')}
    residual = prepare_adjudication(works[1], tmp_path / 'remaining', home / 'profile.json',
                                    resolved_adjudications=[adjudication])
    assert residual['case_count'] == 0
    from harness.web_extract.cli import main

    with patch('harness.web_extract.review.run', return_value={'packets': 0}) as dispatch:
        main(['adjudicate', '--prior', str(works[1]), '--work', str(tmp_path / 'cli-reuse'),
              '--rules', str(home / 'profile.json'), '--model', 'synthetic',
              '--resolved-adjudication', str(adjudication)])
    assert dispatch.call_count == 1
    assert read_json(tmp_path / 'cli-reuse/manifest.json')['case_count'] == 0
    result = assemble(home, content, works[0], empty, adjudication, tmp_path / 'audit.json',
                      profile=home / 'profile.json', additional_reviews=[works[1]])
    assert result['result_count'] == result['original_results_reviewed'] == 6
    assert result['unresolved'] == result['accepted_unreviewed_passes'] == 0
    assert {r['model'] for r in result['reviewers']} == {work.name for work in works}
    assert len(result['review_receipts']['additional']) == 1
    assert {p: p.read_bytes() for p in originals} == originals
    with pytest.raises(ValueError, match='Duplicate independently reviewed'):
        assemble(home, content, works[0], empty, adjudication, tmp_path / 'duplicate.json',
                 profile=home / 'profile.json', additional_reviews=[works[0], works[1]])


def test_novel_reference_claim_requires_its_own_bound_adjudication(tmp_path):
    home, content, works, old_adjudication, empty = product_reviews(tmp_path)
    manifest = read_json(works[1] / 'manifest.json')
    for entry in manifest['packets']:
        path = works[1] / 'reviews' / Path(entry['path']).name
        value = read_json(path)
        for case in value['cases']:
            case['reference']['sku'].update(status='incorrect', values=['new proposal'])
        save_json(path, value)
    args = (home, content, works[0], empty, old_adjudication)
    with pytest.raises(ValueError, match='Disputed references need complete adjudication'):
        assemble(*args, tmp_path / 'unresolved.json', profile=home / 'profile.json',
                 additional_reviews=[works[1]])
    work = tmp_path / 'new-adjudication'
    manifest = prepare_adjudication(works[1], work, home / 'profile.json',
                                    resolved_adjudications=[old_adjudication])
    assert manifest['case_count'] == 3
    complete(work, manifest)
    audit = assemble(*args, tmp_path / 'resolved.json', profile=home / 'profile.json',
                     additional_reviews=[works[1]], additional_adjudications=[work])
    assert audit['result_count'] == 6 and audit['unresolved'] == 0
    previous = next((old_adjudication / 'reviews').glob('*.json'))
    changed = read_json(previous)
    changed['cases'][0]['reference']['sku']['reason'] = 'Changed after reuse was bound'
    save_json(previous, changed)
    with pytest.raises(ValueError, match='Audit source changed'):
        assemble(*args, tmp_path / 'stale.json', profile=home / 'profile.json',
                 additional_reviews=[works[1]], additional_adjudications=[work])
