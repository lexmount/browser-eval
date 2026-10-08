"""Provider append and projection preserve physical calls without partial output."""
import base64
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from harness.web_fetch.artifacts import artifact_bindings, verify_record_artifacts
from harness.web_fetch.assembly import assemble
from harness.web_fetch.evidence import add_provider_run, project_provider_run, projection_main
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.reference_lifecycle import (
    finalize_reference_checks,
    require_verified_reference_checks,
)
from harness.web_fetch.runner import run_evaluation
from scripts.certify_headless_cases import certify_case


def make_run(root, provider, dataset, *, certify=True):
    class Fixture(Provider):
        calls = {}

        def fetch(self, case, timeout):
            self.calls[case.id] = self.calls.get(case.id, 0) + 1
            private = b'{"synthetic_private_response":true}'
            return ProviderResult(True, 'Expected fixture' if self.calls[case.id] == 2 else 'Wrong',
                metadata={'_rendered_html': '<main>fixture</main>',
                          'rendered_html_sha256': hashlib.sha256(b'<main>fixture</main>').hexdigest(),
                          '_raw_stdout_b64': base64.b64encode(b'fixture stdout').decode(),
                          'raw_stdout_sha256': hashlib.sha256(b'fixture stdout').hexdigest(),
                          '_raw_response_b64': base64.b64encode(private).decode(),
                          'raw_response_sha256': hashlib.sha256(private).hexdigest(),
                          'raw_response_bytes': len(private)})

    cases = [EvalCase(**case) for case in dataset['cases']]
    records, _ = run_evaluation(output_dir=root, dataset_meta=dataset,
        dataset_path=root.parent / 'input.json', cases=cases,
        providers=[Fixture(provider, {'type': 'fixture'})],
        provider_config_path=root.parent / 'providers.json', runs=1, timeout=1, delay=0,
        max_failure_retries=1)
    manifest = json.loads((root / 'manifest.json').read_text())
    if certify and manifest.get('reference_checks'):
        now = datetime.now(timezone.utc)
        observations = root.parent / (provider + '-observations.json')
        observations.write_text(json.dumps([{'case_id': case.id, 'independent': True,
            'content_text': case.ground_truth_main_content, 'checked_at': now.isoformat(),
            'scope_identity': manifest['reference_checks']['cases'][case.id]['scope_identity'],
            'completion': {'satisfied': True, 'elapsed_ms': 1, 'timeout_ms': 1000}}
            for case in cases]))
        finalize_reference_checks(root, observations, now=now + timedelta(seconds=1))
        manifest = json.loads((root / 'manifest.json').read_text())
    (root / 'execution.json').write_text(json.dumps({'batches': [manifest]}))
    return records


def dataset(fresh=False):
    cases = [EvalCase(identifier, identifier, 'https://example.invalid/' + identifier,
                     'synthetic', ['Expected']) for identifier in ('one', 'two')]
    if fresh:
        cases = [replace(case, ground_truth_main_content='Expected fixture',
            source_verification={'state': 'valid', 'method': 'independent source capture',
                'checked_at': datetime.now(timezone.utc).isoformat(),
                'reference_scope': 'Complete article',
                'reference_sha256': hashlib.sha256(b'Expected fixture').hexdigest()}) for case in cases]
    return {'id': 'synthetic', 'cases': [asdict(case) for case in cases],
            'coverage_plan': {'web': ['old']}, 'comparison': {}}


@pytest.mark.parametrize('fresh', [False, True])
def test_append_retains_every_physical_call_and_can_be_reassembled(tmp_path, fresh):
    definition = dataset(fresh)
    canonical, incoming = tmp_path / 'canonical', tmp_path / 'incoming'
    old = make_run(canonical, 'old', definition)
    new = make_run(incoming, 'new', definition)
    old_bytes = (canonical / 'results.jsonl').read_bytes()
    new_bytes = (incoming / 'results.jsonl').read_bytes()
    assert add_provider_run(canonical, incoming, 'new') == 2
    assert (canonical / 'results.jsonl').read_bytes() == old_bytes + new_bytes
    for row in old + new:
        verify_record_artifacts(canonical, row)
    current = json.loads((canonical / 'dataset.json').read_text())
    assert current['coverage_plan'] == {'web': ['new', 'old']}
    assert current['cases'] == definition['cases']
    batches = json.loads((canonical / 'execution.json').read_text())['batches']
    require_verified_reference_checks(batches, cases=current, records=old + new, source=canonical)
    output = tmp_path / 'assembled'
    assemble(canonical, tmp_path / 'certificates', [], output)
    assert {row['provider'] for row in map(json.loads, (output / 'results.jsonl').read_text().splitlines())} == {'old', 'new'}


@pytest.mark.parametrize('damage', ['changed-auxiliary', 'missing-auxiliary', 'linked-root'])
def test_projection_rejects_invalid_evidence_without_leaving_output(tmp_path, damage):
    source = tmp_path / 'source'
    rows = make_run(source, 'old', dataset())
    auxiliary = source / rows[0]['provider_metadata']['rendered_html_path']
    if damage == 'changed-auxiliary':
        auxiliary.write_text('changed')
    elif damage == 'missing-auxiliary':
        auxiliary.unlink()
    else:
        alias = tmp_path / 'source-link'
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    output = tmp_path / 'projected'
    with pytest.raises((ValueError, OSError)):
        project_provider_run(source, output, 'old')
    assert not output.exists()
    assert not list(tmp_path.glob('.projected-*'))


def test_projection_keeps_only_selected_task_calls_and_references(tmp_path):
    source = tmp_path / 'source'
    rows = make_run(source, 'old', dataset())
    for identifier in ('one', 'two'):
        folder = source / 'sources' / identifier
        folder.mkdir(parents=True)
        (folder / 'reference.txt').write_text(identifier)
    (source / 'workers').mkdir()
    (source / 'workers/private-note.txt').write_text('synthetic private note')
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob('*') if path.is_file()}
    output = tmp_path / 'projected'
    project_provider_run(source, output, 'old', case_ids={'one'})
    assert not (output / 'sources/two').exists()
    assert not (output / 'workers').exists()
    for row in rows:
        if row['case_id'] == 'one':
            verify_record_artifacts(output, row)
        else:
            for relative, _ in artifact_bindings(row):
                assert not (output / relative).exists()
    assert {path.relative_to(source): path.read_bytes()
            for path in source.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('damage', ['changed-retry', 'missing-retry', 'linked-root',
                                   'missing-observation', 'auxiliary-collision'])
def test_invalid_append_preserves_the_entire_canonical_bundle(tmp_path, damage):
    definition = dataset(damage == 'missing-observation')
    canonical, incoming = tmp_path / 'canonical', tmp_path / 'incoming'
    old = make_run(canonical, 'old', definition)
    rows = make_run(incoming, 'new', definition)
    before = {path.relative_to(canonical): path.read_bytes()
              for path in canonical.rglob('*') if path.is_file()}
    retry = rows[0]['provider_metadata']['retry']['attempts'][0]
    if damage == 'changed-retry':
        (incoming / retry['artifact_path']).write_text('changed')
    elif damage == 'missing-retry':
        (incoming / retry['artifact_path']).unlink()
    elif damage == 'linked-root':
        link = tmp_path / 'incoming-link'
        link.symlink_to(incoming, target_is_directory=True)
        incoming = link
    elif damage == 'missing-observation':
        batch = json.loads((incoming / 'execution.json').read_text())['batches'][0]
        (incoming / batch['reference_checks']['postcheck']['evidence_path']).unlink()
    else:
        relative = old[0]['provider_metadata']['raw_stdout_path']
        collision = incoming / relative
        collision.parent.mkdir(parents=True, exist_ok=True)
        collision.write_bytes(b'different auxiliary bytes')
        rows[0]['provider_metadata'].update(raw_stdout_path=relative,
            raw_stdout_sha256=hashlib.sha256(collision.read_bytes()).hexdigest())
        (incoming / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises((ValueError, OSError)):
        add_provider_run(canonical, incoming, 'new')
    assert {path.relative_to(canonical): path.read_bytes()
            for path in canonical.rglob('*') if path.is_file()} == before
    assert not list(tmp_path.glob('.evidence-add-*'))
    assert not list(tmp_path.glob('.evidence-swap-*'))


@pytest.mark.parametrize('selection', ['one,one', 'one,,two', ' '])
def test_projection_command_rejects_ambiguous_task_scope_before_writing(tmp_path, selection):
    output = tmp_path / 'projected'
    with pytest.raises(SystemExit):
        projection_main(['--source', str(tmp_path / 'missing-source'),
                         '--output', str(output), '--provider', 'old', '--case-ids', selection])
    assert not output.exists()


def test_case_certificates_remain_the_authority_for_an_unfinalized_physical_run(tmp_path):
    definition = dataset(True)
    source = tmp_path / 'source'
    rows = make_run(source, 'old', definition, certify=False)
    manifest = json.loads((source / 'manifest.json').read_text())
    certificates = tmp_path / 'certificates'
    for case in definition['cases']:
        observation = {'case_id': case['id'], 'independent': True,
            'content_text': case['ground_truth_main_content'],
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'scope_identity': manifest['reference_checks']['cases'][case['id']]['scope_identity'],
            'completion': {'satisfied': True, 'elapsed_ms': 1, 'timeout_ms': 1000}}
        certify_case(source, certificates, case,
                     [row for row in rows if row['case_id'] == case['id']], observation)
    output = tmp_path / 'assembled'
    assemble(source, certificates, [], output)
    saved = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
    assert saved == rows
    batches = json.loads((output / 'execution.json').read_text())['batches']
    require_verified_reference_checks(batches, cases=definition, records=rows, source=output)
    assert json.loads((source / 'manifest.json').read_text())['reference_checks']['status'] == 'awaiting_postcheck'
