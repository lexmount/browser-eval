"""A resumed cohort keeps its original dataset and scoring-resource versions."""
import json
from pathlib import Path

import pytest

from harness.benchmarks import data
from harness.web_search import runner
from tests.web_search.fixtures import execution_profile


@pytest.mark.parametrize('changed', [None, 'websearch', 'simpleqa-verified', 'missing'])
@pytest.mark.parametrize('entry', ['resume', 'execution-revision', 'revised-collector'])
def test_resume_checks_dataset_selection_before_requests_or_saved_changes(tmp_path, monkeypatch, changed, entry):
    profile = execution_profile()
    profile['cases'] = profile['cases'][:1]
    profile['protocol'].update(rounds=1, interval_s=0)
    profile['protocol'].pop('reference_scope', None)
    profile.pop('reference_checks', None)
    source = tmp_path / 'profile.json'
    source.write_text(json.dumps(profile))
    output = tmp_path / 'run'
    monkeypatch.setattr(runner, 'load_runtime_env', lambda: None)
    for method in profile['methods']:
        monkeypatch.setenv(method['key_env'], 'fixture')
    interrupted = False
    def http(url, body=None, header=None, timeout=20):
        nonlocal interrupted
        if body is not None and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return {'http_status': 200, 'elapsed_ms': 1, 'transport_exit': 0,
                'transport_error': ''}, b'reference ' * 20 if body is None else b'{"results":[]}'
    monkeypatch.setattr(runner, 'http_call', http)
    with pytest.raises(KeyboardInterrupt):
        runner.run(source, output, certified=True)
    contract = json.loads((output / 'run-contract.json').read_bytes())
    assert contract['installed_benchmark_datasets'] == data.dependencies('websearch')
    if changed == 'missing':
        contract.pop('installed_benchmark_datasets')
        (output / 'run-contract.json').write_text(json.dumps(contract))
    if entry != 'resume':
        new = json.loads(source.read_bytes())
        new['methods'][0]['endpoint'] = 'https://replacement.invalid/search'
        revised = tmp_path / 'revised-profile.json'
        revised.write_text(json.dumps(new))
        proof = tmp_path / 'request-contract.txt'
        proof.write_text('The native request endpoint changed; retained methods stay frozen.')
        revision = tmp_path / 'revision.json'
        revision.write_text(json.dumps({
            'schema': 'web-search-execution-revision/1', 'root_cause': 'Native request endpoint changed',
            'superseded_methods': [new['methods'][0]['id']],
            'previous_profile_sha256': runner.digest(source.read_bytes()),
            'profile_sha256': runner.digest(revised.read_bytes()),
            'evidence': [{'path': proof.name, 'sha256': runner.digest(proof.read_bytes())}]}))
    if entry == 'revised-collector':
        collector = tmp_path / 'collector-revision.py'
        collector.write_text('request_serializer_revision = 2\n')
        implementation = runner.collection_implementation()
        implementation['collector-revision.py'] = runner.digest(collector.read_bytes())
        original_source = runner.implementation_source
        monkeypatch.setattr(runner, 'collection_implementation', lambda: implementation)
        monkeypatch.setattr(runner, 'implementation_source', lambda name: (
            collector if name == 'collector-revision.py' else original_source(name)))
    lock = json.loads(Path(data.__file__).with_name('data-lock.json').read_bytes())
    if changed not in (None, 'missing'):
        lock['datasets'][changed]['revision'] = 'f' * 40
    selected = tmp_path / 'selection'
    selected.mkdir()
    (selected / 'data-lock.json').write_text(json.dumps(lock))
    monkeypatch.setattr(data, '__file__', str(selected / 'data.py'))
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    def resume():
        if entry != 'resume':
            return runner.prepare_execution_revision(revised, output, revision)
        return runner.run(source, output, resume=True, certified=True)
    if changed:
        monkeypatch.setattr(runner, 'http_call', lambda *a, **kw: pytest.fail('Unexpected request'))
        with pytest.raises(ValueError, match='dataset'):
            resume()
        assert {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()} == before
        assert not list(output.glob('resume-*'))
    else:
        resume()
        if entry != 'resume':
            assert (output / 'profile.json').read_bytes() == revised.read_bytes()
            assert (output / 'resume-1').is_dir()
            if entry == 'revised-collector':
                current = json.loads((output / 'run-contract.json').read_bytes())
                assert current['implementation']['collector-revision.py'] == runner.digest(collector.read_bytes())
                assert (output / 'implementation/collector-revision.py').read_bytes() == collector.read_bytes()
        else:
            assert (output / 'receipt.json').is_file()
