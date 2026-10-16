"""Aggregation must preserve, rather than recreate, physical-call authority."""
import base64
import copy
import hashlib

import pytest

from harness.web_fetch.artifacts import materialize_bound_artifacts


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def worker(tmp_path):
    source = tmp_path / 'worker'
    files = {'artifacts/p/c/final.txt': b'final response',
             'artifacts/p/c/first.txt': b'first response',
             'artifacts/p/c/first.bin': b'first binary'}
    for relative, raw in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    record = {'artifact_path': 'artifacts/p/c/final.txt',
              'metrics': {'content_sha256': digest(files['artifacts/p/c/final.txt'])},
              'provider_metadata': {'retry': {'attempts': [{
                  'artifact_path': 'artifacts/p/c/first.txt',
                  'content_sha256': digest(files['artifacts/p/c/first.txt']),
                  'artifact_paths': ['artifacts/p/c/first.txt', 'artifacts/p/c/first.bin'],
                  'artifact_sha256': {key: digest(raw) for key, raw in files.items()
                                      if 'first.' in key},
              }]}}}
    return source, tmp_path / 'aggregate', record, files


@pytest.mark.parametrize('relative', ['artifacts/p/c/first.txt', 'artifacts/p/c/first.bin'])
def test_changed_retry_bytes_cannot_receive_a_new_binding(worker, relative):
    source, target, record, _ = worker
    original = copy.deepcopy(record)
    (source / relative).write_bytes(b'changed after acquisition')
    with pytest.raises(ValueError):
        materialize_bound_artifacts(target, source, [record])
    assert record == original
    assert not target.exists()


def test_unbound_retry_auxiliary_is_rejected(worker):
    source, target, record, _ = worker
    del record['provider_metadata']['retry']['attempts'][0]['artifact_sha256']['artifacts/p/c/first.bin']
    original = copy.deepcopy(record)
    with pytest.raises(ValueError, match='no hash binding'):
        materialize_bound_artifacts(target, source, [record])
    assert record == original


@pytest.mark.parametrize('boundary', ['source', 'destination'])
def test_in_tree_symlinks_are_rejected_even_when_bytes_match(worker, boundary):
    source, target, record, files = worker
    root = source if boundary == 'source' else target
    relative = 'artifacts/p/c/final.txt'
    linked = root / relative
    linked.parent.mkdir(parents=True, exist_ok=True)
    if linked.exists():
        linked.unlink()
    actual = root / 'same-bytes.txt'
    actual.write_bytes(files[relative])
    linked.symlink_to(actual)
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        materialize_bound_artifacts(target, source, [record])


@pytest.mark.parametrize('primary_binding', ['content', 'file-map'])
def test_declared_evidence_copies_without_mutating_records(worker, primary_binding):
    source, target, record, files = worker
    if primary_binding == 'file-map':
        del record['provider_metadata']['retry']['attempts'][0]['content_sha256']
    original = copy.deepcopy(record)
    materialize_bound_artifacts(target, source, [record])
    assert record == original
    assert {key: (target / key).read_bytes() for key in files} == files


@pytest.mark.parametrize('boundary', ['source', 'destination'])
def test_symlinked_evidence_roots_are_rejected(worker, boundary):
    source, target, record, _ = worker
    root = source if boundary == 'source' else target
    actual = root.with_name(root.name + '-actual')
    if root.exists():
        root.rename(actual)
    else:
        actual.mkdir()
    root.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        materialize_bound_artifacts(target, source, [record])


def test_collected_retries_bind_all_auxiliary_bytes_before_aggregation(tmp_path):
    from lexbench_webfetch.models import EvalCase
    from harness.web_fetch.models import ProviderResult
    from harness.web_fetch.providers.base import Provider
    from harness.web_fetch.runner import run_evaluation

    html = b'<main>retained physical call</main>'
    binary = b'raw browser stdout'
    file_bytes = b'\x89PNG\r\n\x1a\n' + b'fixture bytes' * 8

    class Retrying(Provider):
        calls = 0

        def fetch(self, case, timeout):
            self.calls += 1
            return ProviderResult(self.calls == 2, 'Expected' if self.calls == 2 else '',
                error_type=None if self.calls == 2 else 'network',
                error_message=None if self.calls == 2 else 'synthetic transient failure',
                metadata={'_rendered_html': html.decode(),
                          'rendered_html_sha256': digest(html),
                          '_raw_stdout_b64': base64.b64encode(binary).decode(),
                          'raw_stdout_sha256': digest(binary)},
                files=[{'data': file_bytes}])

    source = tmp_path / 'worker'
    records, _ = run_evaluation(output_dir=source, dataset_meta={'id': 'synthetic'},
        dataset_path=tmp_path / 'dataset.json',
        cases=[EvalCase('one', 'One', 'https://example.invalid/', 'synthetic', ['Expected'])],
        providers=[Retrying('retrying', {'type': 'fixture'})],
        provider_config_path=tmp_path / 'providers.json', runs=1,
        timeout=1, delay=0, max_failure_retries=1)
    assert len(records) == 1 and records[0]['metrics']['content_pass']
    attempts = records[0]['provider_metadata']['retry']['attempts']
    assert [attempt['task_pass'] for attempt in attempts] == [False, True]
    for attempt in attempts:
        observed = {(source / path).read_bytes() for path in attempt['artifact_paths']}
        assert {html, binary, file_bytes} <= observed
        assert attempt['artifact_sha256'] == {
            path: digest((source / path).read_bytes()) for path in attempt['artifact_paths']}
    target = tmp_path / 'aggregate'
    materialize_bound_artifacts(target, source, records)
    original = copy.deepcopy(records)
    changed = next(path for path in attempts[0]['artifact_paths']
                   if (source / path).read_bytes() == file_bytes)
    (source / changed).write_bytes(b'changed after acquisition')
    with pytest.raises(ValueError):
        materialize_bound_artifacts(tmp_path / 'second-aggregate', source, records)
    assert records == original
