"""Provider append and projection preserve physical calls without partial output."""
import base64
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from harness.web_fetch.artifacts import artifact_bindings, verify_record_artifacts
from harness.web_fetch.assembly import _copy_speed_bundle, assemble, copy_projected_review
from harness.web_fetch.evidence import (
    add_provider_run,
    certify_case,
    project_provider_run,
    projection_main,
)
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.reference_lifecycle import (
    finalize_reference_checks,
    latency_correction_original_elapsed,
    require_verified_reference_checks,
)
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.speed_cohort import build_summary, derive_aggregate


def reviewed_corrected_matrix(tmp_path, *, fresh=True):
    definition = dataset(fresh)
    source, incoming = tmp_path / 'source', tmp_path / 'incoming'
    make_run(source, 'old', definition)
    make_run(incoming, 'new', definition)
    add_provider_run(source, incoming, 'new')
    rows = [json.loads(line) for line in (source / 'results.jsonl').read_bytes().splitlines()]
    changes = []
    for row in rows:
        before = row['elapsed_ms']
        row['elapsed_ms'] += 60000
        row['provider_metadata']['latency_correction'] = {
            'kind': 'documented_final_round_supersession',
            'original_elapsed_ms': before, 'corrected_elapsed_ms': row['elapsed_ms'],
            'diagnostic_results_sha256': 'a' * 64}
        changes.append({'provider': row['provider'], 'case_id': row['case_id'],
                        'before_ms': before, 'after_ms': row['elapsed_ms']})
    (source / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (source / 'latency-corrections.json').write_text(json.dumps({
        'schema_version': 1,
        'selection': 'same four cases for all three Moli versions; final round always selected, never best-of',
        'diagnostic_results_sha256': 'a' * 64,
        'output_results_sha256': hashlib.sha256((source / 'results.jsonl').read_bytes()).hexdigest(),
        'task_decision_changes': 0, 'superseded_logical_results': len(changes),
        'changes': changes, 'diagnostic_summary': {'old': {'calls': 2}, 'new': {'calls': 2}}}))
    (source / 'result-review.json').write_text(json.dumps({
        'schema_version': 1,
        'bindings': {name + '_sha256': hashlib.sha256((source / filename).read_bytes()).hexdigest()
                     for name, filename in [('dataset', 'dataset.json'), ('results', 'results.jsonl')]},
        'review_protocol': {'scope': 'all', 'machine_judgments_withheld': True},
        'attempts': [{'attempt_id': row['attempt_id'], 'machine_verdict': 'pass',
                      'agent_verdict': 'pass', 'final_verdict': 'pass'} for row in rows]}))
    return source, rows


@pytest.mark.parametrize('excluded', [[], ['new']])
def test_assembly_preserves_corrected_timing_chronology_and_review_scope(tmp_path, excluded):
    source, rows = reviewed_corrected_matrix(tmp_path)
    before = {path.relative_to(source): path.read_bytes()
              for path in source.rglob('*') if path.is_file()}
    (source / 'worker-notes.txt').write_text('private synthetic note')
    output = tmp_path / 'output'
    assemble(source, tmp_path / 'certificates', [], output, exclude_providers=excluded)
    retained = [row for row in rows if row['provider'] not in excluded]
    assert [json.loads(line) for line in (output / 'results.jsonl').read_bytes().splitlines()] == retained
    originals = latency_correction_original_elapsed(output)
    assert set(originals) == {(row['provider'], row['case_id']) for row in retained}
    batches = json.loads((output / 'execution.json').read_text())['batches']
    assert {attempt for batch in batches for attempt in batch['attempt_ids']} == {
        row['attempt_id'] for row in retained}
    require_verified_reference_checks(batches,
        cases=json.loads((output / 'dataset.json').read_text()), records=retained, source=output)
    assert not (output / 'worker-notes.txt').exists()
    for row in retained:
        verify_record_artifacts(output, row)
    review = json.loads((output / 'result-review.json').read_text())
    assert {row['attempt_id'] for row in review['attempts']} == {row['attempt_id'] for row in retained}
    for relative, raw in before.items():
        assert (source / relative).read_bytes() == raw
    receipt = json.loads((output / 'latency-corrections.json').read_text())
    assert set(receipt['diagnostic_summary']) == {row['provider'] for row in retained}


@pytest.mark.parametrize('damage', ['wrong-original', 'missing-receipt', 'missing-entry', 'non-object',
                                  'negative', 'infinite', 'boolean'])
def test_assembly_rejects_tampered_timing_receipt_without_output(tmp_path, damage):
    source, _ = reviewed_corrected_matrix(tmp_path)
    receipt = json.loads((source / 'latency-corrections.json').read_text())
    if damage == 'wrong-original':
        receipt['changes'][0]['before_ms'] += 1
    elif damage == 'missing-entry':
        receipt['changes'].pop()
        receipt['superseded_logical_results'] -= 1
    elif damage == 'non-object':
        receipt['changes'][0] = None
    elif damage != 'missing-receipt':
        invalid = {'negative': -1, 'infinite': float('inf'), 'boolean': True}[damage]
        receipt['changes'][0]['before_ms'] = invalid
        rows = [json.loads(line) for line in (source / 'results.jsonl').read_bytes().splitlines()]
        rows[0]['provider_metadata']['latency_correction']['original_elapsed_ms'] = invalid
        (source / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        receipt['output_results_sha256'] = hashlib.sha256((source / 'results.jsonl').read_bytes()).hexdigest()
    (source / 'latency-corrections.json').write_text(json.dumps(receipt))
    if damage == 'missing-receipt':
        (source / 'latency-corrections.json').unlink()
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='Invalid latency correction|Call outside batch time bounds'):
        assemble(source, tmp_path / 'certificates', [], output, exclude_providers=['new'])
    assert not output.exists()
    assert not list(tmp_path.glob('.output-*'))


def test_review_projection_rejects_changed_attempt_even_with_same_identity(tmp_path):
    source, rows = reviewed_corrected_matrix(tmp_path)
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'dataset.json').write_bytes((source / 'dataset.json').read_bytes())
    rows[0]['metrics']['content_pass'] = False
    (output / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(ValueError, match='changed attempt evidence'):
        copy_projected_review(source, output, [])
    assert not (output / 'result-review.json').exists()


@pytest.mark.parametrize('excluded', [[], ['new']])
def test_speed_copy_preserves_timing_corrections_for_retained_calls(tmp_path, excluded):
    source, rows = reviewed_corrected_matrix(tmp_path, fresh=False)
    definition = json.loads((source / 'dataset.json').read_text())
    execution = json.loads((source / 'execution.json').read_text())
    batches = execution['batches']
    # A synthetic common-window cohort, without running real browser methods.
    start = min(batch['started_at'] for batch in batches)
    end = max(batch['completed_at'] for batch in batches)
    for batch in batches:
        batch.update(started_at=start, completed_at=end)
    aggregate = derive_aggregate(definition, batches)
    execution.update(schema_version=1, aggregate=aggregate)
    (source / 'execution.json').write_text(json.dumps(execution))
    summary = build_summary(definition, rows, batches, aggregate)
    summary['bindings'] = {name + '_sha256': hashlib.sha256((source / filename).read_bytes()).hexdigest()
                           for name, filename in [('dataset', 'dataset.json'), ('results', 'results.jsonl')]}
    (source / 'speed-comparison.json').write_text(json.dumps(summary))
    output = tmp_path / 'speed'
    _copy_speed_bundle(source, output, definition, rows, batches, excluded_providers=excluded)
    assert set(latency_correction_original_elapsed(output)) == {
        (row['provider'], row['case_id']) for row in rows if row['provider'] not in excluded}
    retained = [json.loads(line) for line in (output / 'results.jsonl').read_bytes().splitlines()]
    require_verified_reference_checks(json.loads((output / 'execution.json').read_text())['batches'],
                                       cases=json.loads((output / 'dataset.json').read_text()),
                                       records=retained, source=output)


def make_run(root, provider, dataset, *, certify=True):
    class Fixture(Provider):
        calls = {}

        def fetch(self, case, timeout):
            self.calls[case.id] = self.calls.get(case.id, 0) + 1
            private = b'{"synthetic_private_response":true}'
            return ProviderResult(
                self.calls[case.id] == 2,
                'Expected fixture' if self.calls[case.id] == 2 else '',
                error_type=None if self.calls[case.id] == 2 else 'network',
                error_message=(
                    None if self.calls[case.id] == 2 else 'synthetic transient failure'
                ),
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
            source_verification={'reference_mode': 'live_exact', 'state': 'valid', 'method': 'independent source capture',
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
    with pytest.raises(ValueError, match='requires certificate evidence'):
        assemble(source, None, [], tmp_path / 'missing-certificates')
    output = tmp_path / 'assembled'
    assemble(source, certificates, [], output)
    saved = [json.loads(line) for line in (output / 'results.jsonl').read_text().splitlines()]
    assert saved == rows
    batches = json.loads((output / 'execution.json').read_text())['batches']
    require_verified_reference_checks(batches, cases=definition, records=rows, source=output)
    assert json.loads((source / 'manifest.json').read_text())['reference_checks']['status'] == 'awaiting_postcheck'
