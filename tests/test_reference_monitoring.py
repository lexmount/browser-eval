"""Live observation and certification follow the declared task/provider scope."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from harness.web_fetch import evidence, reference_freeze
from harness.web_fetch.reference_lifecycle import require_verified_reference_checks
from tests.test_provider_evidence_boundary import dataset, make_run


def live_run(tmp_path, track='web'):
    definition = dataset(True)
    definition['cases'] = definition['cases'][:1]
    definition['cases'][0]['track'] = track
    definition['coverage_plan'] = {track: ['old']}
    if track == 'dynamic':
        case = definition['cases'][0]
        checked = datetime.now(timezone.utc).isoformat()
        case['required_facts'] = [{'id': 'expected', 'alternatives': ['Expected']}]
        case['source_benchmark'] = {'render_evidence': {
            'scope': 'Complete fixture', 'rendered_truncated': False,
            'completion': {'condition': 'Complete fixture visible', 'satisfied': True,
                           'elapsed_ms': 1, 'timeout_ms': 1000, 'checked_at': checked}}}
        folder = tmp_path / 'sources' / case['id']
        folder.mkdir(parents=True)
        texts = {'initial.html.txt': '<main>Loading</main>',
                 'rendered.txt': case['ground_truth_main_content'],
                 'reference.txt': case['ground_truth_main_content']}
        for name, text in texts.items():
            (folder / name).write_text(text)
        digests = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in texts.items()}
        (folder / 'evidence.json').write_text(json.dumps({
            'case_id': case['id'], 'url': case['url'], 'completion': 'Complete fixture',
            'checked_at': checked, 'initial_checked_at': checked,
            'initial_sha256': digests['initial.html.txt'],
            'rendered_sha256': digests['rendered.txt'], 'reference_sha256': digests['reference.txt']}))
    run = tmp_path / 'run'
    records = make_run(run, 'old', definition, certify=False)
    manifest = json.loads((run / 'manifest.json').read_bytes())
    case = definition['cases'][0]
    observation = {'case_id': case['id'], 'independent': True,
        'content_text': case['ground_truth_main_content'],
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'scope_identity': manifest['reference_checks']['cases'][case['id']]['scope_identity'],
        'completion': {'satisfied': True, 'elapsed_ms': 1, 'timeout_ms': 1000}}
    observations = tmp_path / 'observations.json'
    observations.write_text(json.dumps([observation]))
    return run, definition, records, observations


@pytest.mark.parametrize('track', ['web', 'dynamic', 'challenge'])
def test_case_certification_waits_for_complete_calls_in_every_fresh_reference_track(tmp_path, monkeypatch, track):
    run, definition, records, observations = live_run(tmp_path, track)
    before = (run / 'manifest.json').read_bytes()
    output = tmp_path / 'certificates'
    snapshots = iter([[], records])
    monkeypatch.setattr(evidence, 'read_completed_records', lambda _: next(snapshots))
    monkeypatch.setattr(evidence.time, 'sleep', lambda _: None)
    evidence.certification_main(['--run-dir', str(run), '--observations', str(observations),
                                 '--output', str(output), '--timeout', '1'])
    target = output / definition['cases'][0]['id']
    certificate = json.loads((target / 'manifest.json').read_bytes())
    require_verified_reference_checks(certificate, cases=definition, records=records, source=target)
    assert json.loads((target / 'results.jsonl').read_bytes()) == records[0]
    assert (target / 'source-manifest.json').read_bytes() == before
    assert (run / 'manifest.json').read_bytes() == before


@pytest.mark.parametrize('entry', ['certification', 'postcheck'])
@pytest.mark.parametrize('damage', ['duplicate', 'unknown-provider'])
def test_live_monitors_reject_extra_calls_before_observation_or_certificate(tmp_path, monkeypatch, entry, damage):
    run, _, records, observations = live_run(tmp_path)
    extra = dict(records[0])
    if damage == 'unknown-provider':
        extra['provider'] = 'undeclared'
    rows = records + [extra]
    module = evidence if entry == 'certification' else reference_freeze
    monkeypatch.setattr(module, 'read_completed_records', lambda _: rows)
    def forbidden(*args):
        pytest.fail('Invalid call matrix must be rejected before reference capture')
    monkeypatch.setattr(reference_freeze, 'capture', forbidden)
    output = tmp_path / 'output'
    arguments = ['--run-dir', str(run), '--timeout', '0']
    arguments += (['--observations', str(observations), '--output', str(output)]
                  if entry == 'certification' else ['--captures', str(output)])
    with pytest.raises(ValueError, match='Duplicate|Undeclared'):
        (evidence.certification_main if entry == 'certification' else reference_freeze.postcheck_main)(arguments)
    assert not (output / 'one/manifest.json').exists()


def test_certification_with_no_live_reference_scope_needs_no_observation_file(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'dataset.json').write_text(json.dumps(dataset(False)))
    output = tmp_path / 'certificates'
    evidence.certification_main(['--run-dir', str(run), '--observations', str(tmp_path / 'missing'),
                                 '--output', str(output), '--timeout', '0'])
    assert not output.exists()


@pytest.mark.parametrize('provenance', [
    {'observation_route': 'browser'},
    {'method': 'Independent visible browser DOM; complete declared scope'},
    {'method': 'Independent rendered browser DOM; complete declared scope'},
])
def test_arbitrary_browser_reference_requires_observation_before_freeze_or_postcheck(tmp_path, monkeypatch, provenance):
    run, definition, _, _ = live_run(tmp_path)
    definition['cases'][0]['source_verification'].update(provenance)
    (run / 'dataset.json').write_text(json.dumps(definition))
    captures = tmp_path / 'captures'
    captures.mkdir()
    (captures / 'audit.json').write_text('[]')
    with pytest.raises(ValueError, match='requires independent browser observation'):
        reference_freeze.compute_reference_update(definition, captures)
    def forbidden(*args):
        pytest.fail('A browser-bound reference cannot use an HTTP observation')
    monkeypatch.setattr(reference_freeze, 'capture', forbidden)
    with pytest.raises(TimeoutError, match='Post-call observation deadline'):
        reference_freeze.postcheck_main(['--run-dir', str(run), '--captures', str(tmp_path / 'post'),
                                         '--timeout', '0'])


def test_browser_recovery_records_its_acquisition_route_for_future_postchecks(tmp_path):
    _, definition, _, observations = live_run(tmp_path)
    case = definition['cases'][0]
    case['source_verification']['observation_route'] = 'http'
    row = json.loads(observations.read_bytes())[0]
    row['selector'] = 'main'
    captures = tmp_path / 'captures'
    captures.mkdir()
    (captures / 'audit.json').write_text('[]')
    frozen, _, _ = reference_freeze.compute_reference_update(definition, captures, {case['id']: row})
    assert frozen['cases'][0]['source_verification']['observation_route'] == 'browser'
    assert case['source_verification']['observation_route'] == 'http'


def test_existing_certificate_cannot_be_reused_for_changed_calls(tmp_path):
    run, definition, records, observations = live_run(tmp_path)
    output = tmp_path / 'certificates'
    arguments = ['--run-dir', str(run), '--observations', str(observations),
                 '--output', str(output), '--timeout', '0']
    evidence.certification_main(arguments)
    target = output / definition['cases'][0]['id']
    before = (target / 'manifest.json').read_bytes()
    evidence.certification_main(arguments)
    records[0]['elapsed_ms'] += 100
    (run / 'results.jsonl').write_text(json.dumps(records[0]) + '\n')
    with pytest.raises(ValueError, match='different evidence'):
        evidence.certification_main(arguments)
    assert (target / 'manifest.json').read_bytes() == before


def test_resume_retains_certificate_when_live_run_adds_completion_metadata(tmp_path):
    run, _, _, observations = live_run(tmp_path)
    source = json.loads((run / 'manifest.json').read_bytes())
    for key in ('attempt_count', 'attempt_ids', 'completed_at'):
        source.pop(key, None)
    (run / 'manifest.json').write_text(json.dumps(source))
    output = tmp_path / 'certificates'
    arguments = ['--run-dir', str(run), '--observations', str(observations),
                 '--output', str(output), '--timeout', '0']
    evidence.certification_main(arguments)
    before = (output / 'one/source-manifest.json').read_bytes()
    source.update(attempt_count=1, attempt_ids=['old__one__r001'],
                  completed_at=datetime.now(timezone.utc).isoformat())
    (run / 'manifest.json').write_text(json.dumps(source))
    evidence.certification_main(arguments)
    assert (output / 'one/source-manifest.json').read_bytes() == before
    source['reference_checks']['cases']['one']['reference_sha256'] = '0' * 64
    (run / 'manifest.json').write_text(json.dumps(source))
    with pytest.raises(ValueError, match='different evidence'):
        evidence.certification_main(arguments)


def test_certificate_creation_failure_leaves_no_partial_destination(tmp_path, monkeypatch):
    run, definition, records, observations = live_run(tmp_path)
    output = tmp_path / 'certificates'
    def broken(*args):
        raise OSError('synthetic disk failure')
    with monkeypatch.context() as context:
        context.setattr(evidence, 'finalize_reference_checks', broken)
        with pytest.raises(OSError, match='synthetic disk failure'):
            evidence.certify_case(run, output, definition['cases'][0], records,
                                  json.loads(observations.read_bytes())[0])
    assert not list(output.iterdir())
    assert evidence.certify_case(run, output, definition['cases'][0], records,
                                 json.loads(observations.read_bytes())[0])['status'] == 'verified'


@pytest.mark.parametrize('damage', ['record', 'case'])
def test_certificate_refuses_call_or_task_changes_outside_physical_evidence(tmp_path, damage):
    run, definition, records, observations = live_run(tmp_path)
    if damage == 'record':
        records[0]['elapsed_ms'] += 1
    else:
        definition['cases'][0]['name'] = 'Different task definition'
    output = tmp_path / 'certificates'
    with pytest.raises(ValueError, match='physical run'):
        evidence.certify_case(run, output, definition['cases'][0], records,
                              json.loads(observations.read_bytes())[0])
    assert not output.exists()


@pytest.mark.parametrize('after', [False, True])
def test_browser_observation_time_is_compared_as_an_instant_across_timezones(tmp_path, monkeypatch, after):
    run, definition, records, observations = live_run(tmp_path)
    row = json.loads(observations.read_bytes())[0]
    last_call = datetime.fromisoformat(records[0]['completed_at'])
    observed = last_call + timedelta(seconds=1 if after else -1)
    row['checked_at'] = observed.astimezone(timezone(timedelta(hours=-8 if after else 8))).isoformat()
    browser = tmp_path / 'browser.json'
    browser.write_text(json.dumps({definition['cases'][0]['id']: row}))
    def forbidden(*args):
        pytest.fail('Provided browser observations must not trigger HTTP capture')
    monkeypatch.setattr(reference_freeze, 'capture', forbidden)
    output = tmp_path / 'post'
    arguments = ['--run-dir', str(run), '--captures', str(output),
                 '--browser-observations', str(browser), '--timeout', '0']
    if after:
        reference_freeze.postcheck_main(arguments)
        assert json.loads((output / 'observations.json').read_bytes())[0]['checked_at'] == row['checked_at']
    else:
        with pytest.raises(TimeoutError):
            reference_freeze.postcheck_main(arguments)
        assert json.loads((output / 'observations.json').read_bytes()) == []
