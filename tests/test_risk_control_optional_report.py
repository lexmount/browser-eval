"""Risk-control collection retains certification without requiring local HTML."""
import hashlib
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.web_fetch import certification, report_build, risk_control, runner


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    profile_path = tmp_path / 'evaluation/risk/profile.json'
    profile_path.parent.mkdir(parents=True)
    config_path = tmp_path / 'provider.json'
    config_path.write_text('{}')
    dataset = {'cases': [{'id': f'case-{index}'} for index in range(100)]}
    dataset_path = tmp_path / 'dataset.json'
    dataset_path.write_text(json.dumps(dataset))
    profile = {'status': 'ready', 'unresolved_case_ids': [], 'source_bindings': {},
               'dataset': {'path': 'dataset.json', 'sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest()},
               'provider': {'name': 'fixture', 'config_path': 'provider.json',
                            'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest()},
               'execution': {'runs_per_case': 1, 'timeout_seconds': 1, 'delay_seconds': 0,
                             'concurrency': 1, 'max_failure_retries': 0}}
    profile_path.write_text(json.dumps(profile))
    provider = SimpleNamespace(_implementation_identity={'version': 'fixture-v1'}, validate=lambda: [])
    monkeypatch.setattr(risk_control, 'ROOT', tmp_path)
    monkeypatch.setattr(risk_control, 'validate', lambda path: (profile, dataset, [SimpleNamespace(id=row['id']) for row in dataset['cases']], provider, config_path))
    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path / 'data'))
    # main changes process environment for reproducible measurements; keep the
    # fixture's environment isolation while exercising those real assignments.
    for key in (*risk_control.PROXY_VARIABLES, 'LANG', 'LC_ALL'):
        monkeypatch.setenv(key, '')
    state = {'builds': [], 'records': [], 'runner_error': None, 'report_error': None, 'truncated': False}

    def collect(**kwargs):
        if state['runner_error']:
            raise ValueError(state['runner_error'])
        output = kwargs['output_dir']
        output.mkdir(parents=True)
        rows = []
        for case in dataset['cases']:
            log = output / f"private_artifacts/{case['id']}.log"
            log.parent.mkdir(exist_ok=True)
            log.write_bytes(b'fixture diagnostics\n')
            rows.append({'case_id': case['id'], 'provider_metadata': {
                'diagnostic_log_path': log.relative_to(output).as_posix(),
                'diagnostic_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
                'diagnostic_log_bytes': log.stat().st_size,
                'diagnostic_log_truncated': state['truncated']}})
        state['records'] = rows
        (output / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        (output / 'manifest.json').write_text(json.dumps({'started_at': '2026-01-01T00:00:00Z'}))
        (output / 'dataset.json').write_text(json.dumps(kwargs['dataset_meta']))
        (output / 'summary.json').write_text(json.dumps({'attempts': len(rows)}))
        return rows, {}

    def build(source, destination):
        state['builds'].append((source, destination))
        if state['report_error']:
            raise ValueError(state['report_error'])
        destination.mkdir()
        page = destination / 'index.html'
        page.write_text('Local fixture report')
        return page

    monkeypatch.setattr(runner, 'run_evaluation', collect)
    monkeypatch.setattr(report_build, 'build_report', build)
    return profile_path, tmp_path / 'data/run', state


def execute(cohort, *extra):
    profile, output, _ = cohort
    certification.main(['risk-control', '--profile', str(profile), '--output', str(output), '--execute', *extra])


def assert_certified_data(cohort):
    _, output, state = cohort
    assert len(state['records']) == 100
    receipt = json.loads((output / 'certification.json').read_text())
    assert receipt['logical_results'] == receipt['physical_calls'] == 100
    for name in ('results', 'manifest', 'execution', 'frozen_input_verification'):
        filename = {'results': 'results.jsonl', 'frozen_input_verification': 'frozen-input-verification.json'}.get(name, name + '.json')
        assert receipt[name + '_sha256'] == hashlib.sha256((output / filename).read_bytes()).hexdigest()
    assert (output / 'dataset.json').is_file()
    assert (output / 'summary.json').is_file()


def test_certified_collection_can_skip_report_without_losing_evidence(cohort, capsys, monkeypatch):
    monkeypatch.setenv('BROWSER_EVAL_REPORT_LANGUAGE', 'unsupported-but-unrequested')
    cohort[2]['report_error'] = 'Unrequested renderer must not run'
    execute(cohort, '--no-report')
    assert_certified_data(cohort)
    assert cohort[2]['builds'] == []
    assert not (cohort[1] / 'report').exists()
    printed = json.loads(capsys.readouterr().out)
    assert printed['evidence'] == str(cohort[1])
    assert printed['report_status'] == 'not_built'
    assert 'report' not in printed


def test_default_collection_keeps_local_report_convenience(cohort, capsys):
    execute(cohort)
    assert_certified_data(cohort)
    assert cohort[2]['builds'] == [(cohort[1], cohort[1] / 'report')]
    assert Path(json.loads(capsys.readouterr().out)['report']).is_file()


def test_no_report_does_not_swallow_collection_failure(cohort):
    cohort[2]['runner_error'] = 'Collector failure sentinel'
    with pytest.raises(ValueError, match='Collector failure sentinel'):
        execute(cohort, '--no-report')
    assert not cohort[1].exists()
    assert cohort[2]['builds'] == []


def test_no_report_preserves_complete_diagnostic_requirement(cohort):
    cohort[2]['truncated'] = True
    with pytest.raises(ValueError, match='complete hash-bound raw stderr'):
        execute(cohort, '--no-report')
    assert (cohort[1] / 'results.jsonl').is_file()
    assert not (cohort[1] / 'certification.json').exists()
    assert cohort[2]['builds'] == []


def test_default_renderer_failure_keeps_certified_data_and_propagates(cohort):
    cohort[2]['report_error'] = 'Renderer failure sentinel'
    with pytest.raises(ValueError, match='Renderer failure sentinel'):
        execute(cohort)
    assert_certified_data(cohort)


def test_certified_cli_accepts_no_report_for_call_free_validation(cohort, monkeypatch, capsys):
    profile, _, state = cohort
    monkeypatch.setattr(sys, 'argv', ['run_certified_benchmark.py', 'risk-control',
                                      '--profile', str(profile), '--no-report'])
    script = Path(__file__).resolve().parents[1] / 'scripts/run_certified_benchmark.py'
    runpy.run_path(str(script), run_name='__main__')
    assert json.loads(capsys.readouterr().out)['provider_calls'] == 0
    assert state['records'] == state['builds'] == []
