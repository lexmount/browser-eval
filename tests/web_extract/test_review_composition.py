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


def test_ready_case_adjudication_can_start_before_other_review_packets_finish(tmp_path):
    home, _, works, _, _ = product_reviews(tmp_path)
    method = works[0].name.removeprefix('review-')
    prior = tmp_path / 'partial'
    manifest = prepare(home, home / (method + '-content.json'), prior, batch_size=1)
    complete(prior, {**manifest, 'packets': manifest['packets'][:1]},
             claim={'status': 'ambiguous'})
    ready = read_packet(Path(manifest['packets'][0]['path']))['cases'][0]['case_id']
    pending = read_packet(Path(manifest['packets'][1]['path']))['cases'][0]['case_id']
    result = prepare_adjudication(prior, tmp_path / 'ready', home / 'profile.json',
                                  case_ids=[ready])
    assert result['case_count'] == 1 and result['selected_case_ids'] == [ready]
    assert len(list((prior / 'reviews').glob('*.json'))) == 1
    with pytest.raises(FileNotFoundError):
        prepare_adjudication(prior, tmp_path / 'pending', home / 'profile.json', case_ids=[pending])
    with pytest.raises(ValueError, match='unknown case'):
        prepare_adjudication(prior, tmp_path / 'unknown', home / 'profile.json',
                             case_ids=['unknown'])
    assert not (tmp_path / 'unknown/manifest.json').exists()


@pytest.mark.parametrize('changed', ['source', 'reference', 'rules'])
def test_same_claim_on_changed_evidence_is_not_reused_as_an_old_source_decision(tmp_path, changed):
    _, _, _, old_adjudication, _ = product_reviews(tmp_path / 'old')
    home = create(tmp_path / 'new')
    if changed == 'source':
        path = next((home / 'dataset/html').glob('*.html'))
        path.write_text(path.read_text(encoding='utf-8') + '<p>New edition</p>', encoding='utf-8')
    elif changed == 'reference':
        path = home / 'dataset/dataset/ground-truth.json'
        truth = read_json(path)
        truth[next(iter(truth))]['sku'] = ['different annotation']
        save_json(path, truth)
    if changed in {'source', 'reference'}:
        dataset = read_json(home / 'dataset/manifest.json')
        for name, item in dataset['files'].items():
            file = home / 'dataset' / name
            item.update(sha256=digest(file.read_bytes()), bytes=file.stat().st_size)
        save_json(home / 'dataset/manifest.json', dataset)
        run = home / 'runs/example-v1'
        contract = read_json(run / 'contract.json')
        contract['dataset_manifest_sha256'] = digest((home / 'dataset/manifest.json').read_bytes())
        save_json(run / 'contract.json', contract)
        manifest = read_json(run / 'manifest.json')
        manifest['contract_sha256'] = digest((run / 'contract.json').read_bytes())
        for entry in manifest['calls']:
            file = run / entry['path']
            row = read_json(file)
            row['input_sha256'] = digest(
                (home / 'dataset/html' / (row['case_id'] + '.html')).read_bytes())
            save_json(file, row)
            entry['sha256'] = digest(file.read_bytes())
        save_json(run / 'manifest.json', manifest)
    prior = tmp_path / 'current'
    plan = prepare(home, home / 'content.json', prior)
    complete(prior, plan, claim={'status': 'ambiguous'})
    rules = home / 'profile.json'
    if changed == 'rules':
        rules = tmp_path / 'different-rules.txt'
        rules.write_text('Different interpretation contract', encoding='utf-8')
    result = prepare_adjudication(prior, tmp_path / 'pending', rules,
                                  resolved_adjudications=[old_adjudication])
    assert result['case_count'] == (3 if changed == 'rules' else 1)


def change_reference_claim(work, field, status, values):
    for path in (work / 'reviews').glob('*.json'):
        value = read_json(path)
        for case in value['cases']:
            case['reference'][field].update(status=status, values=values)
        save_json(path, value)


def change_adjudicated_value(work, field, values):
    for path in (work / 'reviews').glob('*.json'):
        value = read_json(path)
        for case in value['cases']:
            case['reference'][field].update(status='incorrect', values=values)
            case['source_fields'][field]['values'] = values
        save_json(path, value)


def test_new_price_adjudication_preserves_the_previously_resolved_sku(tmp_path):
    home, content, works, old_adjudication, empty = product_reviews(tmp_path)
    change_adjudicated_value(old_adjudication, 'sku', ['synthetic product'])
    change_reference_claim(works[1], 'sku', 'supported', [])
    change_reference_claim(works[1], 'price', 'ambiguous', [])
    new = tmp_path / 'price-adjudication'
    complete(new, prepare_adjudication(works[1], new, home / 'profile.json'))
    audit = assemble(home, content, works[0], empty, old_adjudication, tmp_path / 'audit.json',
                     profile=home / 'profile.json', additional_reviews=[works[1]],
                     additional_adjudications=[new])
    assert all(ref['sku'] == ['synthetic product'] for ref in audit['audited_reference'].values())


def test_conflicting_field_adjudications_require_an_explicit_source_decision(tmp_path):
    home, content, works, old, empty = product_reviews(tmp_path)
    change_adjudicated_value(old, 'sku', ['synthetic product'])
    new = tmp_path / 'new-adjudication'
    complete(new, prepare_adjudication(works[1], new, home / 'profile.json'))
    change_adjudicated_value(new, 'sku', ['product'])
    for first, second in [(old, new), (new, old)]:
        with pytest.raises(ValueError, match='Conflicting adjudications'):
            assemble(home, content, works[0], empty, first, tmp_path / 'conflict.json',
                     profile=home / 'profile.json', additional_reviews=[works[1]],
                     additional_adjudications=[second])

    truth = read_json(home / 'dataset/dataset/ground-truth.json')
    decisions = tmp_path / 'source-decisions.json'
    save_json(decisions, {'decisions': [
        {'case_id': cid, 'field': 'sku', 'accepted': ['synthetic product'],
         'source_sha256': digest((home / 'dataset/html' / (cid + '.html')).read_bytes()),
         'source_quote': 'synthetic product', 'source_locator': 'p',
         'reason': 'Explicit source decision for the conflicting proposals'} for cid in truth]})
    for index, (first, second) in enumerate([(old, new), (new, old)]):
        value = assemble(home, content, works[0], empty, first, tmp_path / f'resolved-{index}.json',
                         profile=home / 'profile.json', additional_reviews=[works[1]],
                         additional_adjudications=[second], decisions=decisions)
        assert all(ref['sku'] == ['synthetic product']
                   for ref in value['audited_reference'].values())


def test_packet_bindings_do_not_credit_a_reference_claim_absent_from_adjudication_inputs(tmp_path):
    home, content, works, _, empty = product_reviews(tmp_path)
    change_reference_claim(works[1], 'sku', 'supported', [])
    change_reference_claim(works[1], 'price', 'ambiguous', [])
    work = tmp_path / 'incomplete-claims'
    prepare_adjudication(works[0], work, home / 'profile.json', additional_priors=[works[1]])
    stored = read_json(work / 'manifest.json')
    for entry in stored['packets']:
        path = work / entry['path']
        packet = read_json(path)
        for case in packet['cases']:
            case.pop('additional_prior_reference_findings')
        save_json(path, packet)
        entry['sha256'] = digest(path.read_bytes())
    save_json(work / 'manifest.json', stored)
    from harness.web_extract.review import read_manifest
    complete(work, read_manifest(work))
    with pytest.raises(ValueError, match='complete adjudication'):
        assemble(home, content, works[0], empty, work, tmp_path / 'uncovered.json',
                 profile=home / 'profile.json', additional_reviews=[works[1]])
