"""Paired timing honors the declared population and retains every physical call."""
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from harness.record_io import load_jsonl
from harness.web_fetch import wcxb_latency as timing
from harness.web_fetch.models import ProviderResult
from tests.wcxb_fixture import create_cohort, sha, write_json


class LocalMethod:
    def __init__(self, binary):
        self.config = {}
        self.binary = binary

    def validate(self):
        return []

    def implementation_identity(self):
        return {'binary_sha256': sha(self.binary.read_bytes())}


@pytest.fixture
def experiment(tmp_path, monkeypatch):
    options = create_cohort(tmp_path / 'inputs')
    binaries = {name: tmp_path / name for name in ('release', 'pr')}
    for name, binary in binaries.items():
        binary.write_text(name)
    authority = json.loads(options['profile_path'].read_bytes())
    authority.update(release_binary_sha256=sha(binaries['release'].read_bytes()),
                     candidate_binary_sha256=sha(binaries['pr'].read_bytes()), concurrency=1)
    write_json(options['profile_path'], authority)
    providers = {name: LocalMethod(binary) for name, binary in binaries.items()}
    events = []

    @contextmanager
    def server(records):
        yield 'https://fixture.invalid', Path('synthetic-ca.pem')

    def fetch(provider, _, record, url, timeout):
        events.append((record['track_id'], next(k for k, v in providers.items() if v is provider), timeout))
        return ProviderResult(True, 'same output for ' + record['track_id'])

    monkeypatch.setattr('harness.web_fetch.webmainbench_runner.fixture_server', server)
    monkeypatch.setattr('harness.web_fetch.webmainbench_runner.fetch_record', fetch)
    monkeypatch.setattr(timing, 'require_quiet_host', lambda: 1.0)
    monkeypatch.setattr(timing.time, 'sleep', lambda _: None)
    ticks = iter(i / 100 for i in range(1000))
    monkeypatch.setattr(timing.time, 'monotonic', lambda: next(ticks))
    kwargs = dict(dataset=options['source'], manifest=options['sample_manifest'],
                  profile=options['profile_path'], output=tmp_path / 'new-timing',
                  binaries=binaries, providers=providers)
    return kwargs, events, authority


def test_complete_two_case_profile_is_supported_without_frozen_504_constant(experiment):
    kwargs, events, authority = experiment
    result = timing.collect(**kwargs)
    calls = load_jsonl(kwargs['output'] / 'calls.jsonl')
    assert len(events) == len(calls) == result['call_count'] == 8
    assert [(r['case'], r['method']) for r in calls] == [
        ('case-000', 'release'), ('case-000', 'pr'), ('case-001', 'pr'), ('case-001', 'release'),
        ('case-000', 'pr'), ('case-000', 'release'), ('case-001', 'release'), ('case-001', 'pr'),
    ]
    assert all(event[2] == authority['timeout_seconds'] for event in events)
    assert len(result['host_load_1m_checks']) == 4
    bound = {**authority, 'latency_correction_receipt_sha256': sha((kwargs['output'] / 'receipt.json').read_bytes())}
    _, selected = timing.load_full_paired_latency(kwargs['output'], bound, {'case-000', 'case-001'})
    assert all(row['round'] == 1 for row in selected.values())


@pytest.mark.parametrize('change', ['population', 'source', 'manifest', 'binary', 'evaluator', 'concurrency'])
def test_invalid_authority_stops_before_calls(experiment, change):
    kwargs, events, authority = experiment
    key = {'population': 'case_count', 'source': 'source_dataset_sha256',
           'manifest': 'sample_manifest_sha256', 'binary': 'release_binary_sha256',
           'evaluator': 'official_evaluator_sha256', 'concurrency': 'concurrency'}[change]
    authority[key] = 99 if key in ('case_count', 'concurrency') else '0' * 64
    write_json(kwargs['profile'], authority)
    with pytest.raises(ValueError):
        timing.collect(**kwargs)
    assert events == []
    assert not kwargs['output'].exists()


@pytest.mark.parametrize('changed', ['profile', 'binary', 'manifest', 'source'])
def test_mid_call_input_change_cannot_issue_receipt(experiment, monkeypatch, changed):
    kwargs, events, _ = experiment
    from harness.web_fetch import webmainbench_runner as runner
    original = runner.fetch_record

    def mutate(*args):
        response = original(*args)
        path = {'profile': kwargs['profile'], 'binary': kwargs['binaries']['release'],
                'manifest': kwargs['manifest'],
                'source': kwargs['dataset'] / 'test/html/case-000.html.gz'}[changed]
        path.write_bytes(path.read_bytes() + b' ')
        return response

    monkeypatch.setattr(runner, 'fetch_record', mutate)
    with pytest.raises(ValueError, match='changed'):
        timing.collect(**kwargs)
    assert events
    assert (kwargs['output'] / 'calls.jsonl').is_file()
    assert not (kwargs['output'] / 'receipt.json').exists()


def test_failed_call_retains_complete_physical_population_without_receipt(experiment, monkeypatch):
    kwargs, events, _ = experiment
    from harness.web_fetch import webmainbench_runner as runner
    original = runner.fetch_record

    def fail(*args):
        original(*args)
        return ProviderResult(False, '', error_type='tool_error', error_message='synthetic failure')

    monkeypatch.setattr(runner, 'fetch_record', fail)
    with pytest.raises(ValueError, match='failed or invalid calls'):
        timing.collect(**kwargs)
    assert len(events) == 8
    assert len(load_jsonl(kwargs['output'] / 'calls.jsonl')) == 8
    assert not (kwargs['output'] / 'receipt.json').exists()


@pytest.mark.parametrize('target', ['profile', 'manifest', 'binary'])
def test_linked_inputs_stop_before_calls(experiment, tmp_path, target):
    kwargs, events, _ = experiment
    path = kwargs['binaries']['release'] if target == 'binary' else kwargs[target]
    link = tmp_path / 'linked-input'
    path.rename(link)
    path.symlink_to(link)
    with pytest.raises(ValueError, match='symlink'):
        timing.collect(**kwargs)
    assert not events


@pytest.mark.parametrize('change', ['nan-limit', 'negative-load', 'boolean-load',
                                  'boolean-latency', 'boolean-round', 'boolean-order', 'nonhex-output'])
def test_malformed_timing_receipt_cannot_become_authoritative(tmp_path, change):
    options = create_cohort(tmp_path, paired=True)
    root = options['evidence_root'] / 'latency-correction'
    receipt = json.loads((root / 'receipt.json').read_bytes())
    calls = load_jsonl(root / 'calls.jsonl')
    if change == 'nan-limit':
        receipt['quiet_host_load_limit'] = float('nan')
    elif change == 'negative-load':
        receipt['host_load_1m_checks'][0] = -1.0
    elif change == 'boolean-load':
        receipt['host_load_1m_checks'][0] = True
    elif change == 'boolean-latency':
        calls[0]['elapsed_ms'] = True
    elif change == 'boolean-round':
        calls[-1]['round'] = True
    elif change == 'boolean-order':
        calls[0]['order'] = True
    else:
        for row in calls:
            row['output_sha256'] = 'g' * 64
    from tests.wcxb_fixture import write_jsonl
    write_jsonl(root / 'calls.jsonl', calls)
    receipt['calls_sha256'] = sha((root / 'calls.jsonl').read_bytes())
    write_json(root / 'receipt.json', receipt)
    authority = json.loads(options['profile_path'].read_bytes())
    authority['latency_correction_receipt_sha256'] = sha((root / 'receipt.json').read_bytes())
    with pytest.raises(ValueError):
        timing.load_full_paired_latency(root, authority, {'case-000', 'case-001'})


def test_certification_binds_complete_calls_and_never_overwrites(experiment):
    kwargs, _, authority = experiment
    timing.collect(**kwargs)
    authority['latency_correction_receipt_sha256'] = sha((kwargs['output'] / 'receipt.json').read_bytes())
    write_json(kwargs['profile'], authority)
    args = ['--dataset', str(kwargs['dataset']), '--sample-manifest', str(kwargs['manifest']),
            '--evidence-root', str(kwargs['output']), '--profile', str(kwargs['profile'])]
    result = timing.certify(args)
    assert result['physical_call_count'] == 8
    assert result['selected_round'] == 1
    assert result['complete'] is True
    previous = (kwargs['output'] / 'certification.json').read_bytes()
    with pytest.raises(ValueError, match='already certified'):
        timing.certify(args)
    assert (kwargs['output'] / 'certification.json').read_bytes() == previous


def test_certification_rejects_calls_changed_during_source_recheck(experiment, monkeypatch):
    kwargs, _, authority = experiment
    timing.collect(**kwargs)
    authority['latency_correction_receipt_sha256'] = sha((kwargs['output'] / 'receipt.json').read_bytes())
    write_json(kwargs['profile'], authority)
    from harness.web_fetch import webmainbench_runner as runner
    original = runner.load_wcxb_source
    seen = []

    def mutate(*args):
        result = original(*args)
        seen.append(1)
        if len(seen) == 2:
            path = kwargs['output'] / 'calls.jsonl'
            path.write_bytes(path.read_bytes() + b'\n')
        return result

    monkeypatch.setattr(runner, 'load_wcxb_source', mutate)
    with pytest.raises(ValueError, match='changed during certification'):
        timing.certify(['--dataset', str(kwargs['dataset']), '--sample-manifest', str(kwargs['manifest']),
                        '--evidence-root', str(kwargs['output']), '--profile', str(kwargs['profile'])])
    assert not (kwargs['output'] / 'certification.json').exists()


@pytest.mark.parametrize('repository_default', [False, True])
def test_collector_cli_preserves_repository_default_and_supports_caller_default(tmp_path, monkeypatch, repository_default):
    monkeypatch.chdir(tmp_path)
    captured = []
    monkeypatch.setattr(timing, 'collect', lambda **kw: captured.append(kw) or {})
    repository = tmp_path / 'repository' if repository_default else None
    timing.main(['--dataset', 'dataset', '--manifest', 'manifest.json',
                 '--release', 'released', '--candidate', 'candidate', '--output', 'new', '--profile', str(tmp_path/'selected.json')], root=repository)
    assert captured[0]['profile'] == tmp_path / 'selected.json'


@pytest.mark.parametrize('options', [[], ['--profile', '/explicit/profile.json'], ['--profile=/explicit/profile.json']])
def test_existing_certification_command_keeps_its_repository_authority(monkeypatch, options):
    from harness.web_fetch import certification
    captured = []
    monkeypatch.setattr(timing, 'certify', lambda argv: captured.append(argv))
    certification.main(['wcxb-latency', *options])
    assert captured == [options]
