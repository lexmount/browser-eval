"""Edition selection verifies inputs before changing any current binding."""
from harness.distribution import source_path

import hashlib
import io
import json
import tarfile
import copy

import pytest

from harness.benchmarks import data, pinning


def fixture(root, monkeypatch, defect=None, *, jsonl=False):
    raw_dataset = b'{"task_id":"task"}\n' if jsonl else b'{"id":"sample","cases":[{"id":"task"}]}'
    if defect == 'population':
        raw_dataset = b'{"task_id":"first"}\n{"task_id":"second"}\n'
    if defect == 'duplicate-id':
        raw_dataset = b'{"task_id":"task"}\n{"task_id":"task"}\n'
    registry = {'datasets': {'sample': {'path': 'datasets/sample.json', 'count': 1,
                 'sha256': hashlib.sha256(raw_dataset).hexdigest()}}}
    if jsonl:
        registry['datasets']['sample'].update(format='jsonl', id_field='task_id')
    if defect == 'duplicate-id':
        registry['datasets']['sample']['count'] = 2
    files = {'definitions/registry.json': json.dumps(registry).encode(),
             'definitions/datasets/sample.json': raw_dataset}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    raw = stream.getvalue()
    manifest = {'schema': 'browser-eval-dataset-release/1', 'track': 'websearch',
                'source_revision': 'b' * 40, 'datasets': registry['datasets'],
                'archive': {'path': 'definitions.tar.gz', 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
                'files': {name: {'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                          for name, value in files.items()}}
    before = {'repository': 'Lexmount/LexBench-Web-Search', 'revision': '0' * 40,
              'archive': {'sha256': '0' * 64}, 'files': {}}
    if defect == 'legacy-known':
        before['history'] = [{'revision': 'a' * 40, 'archive': manifest.pop('archive')}]
    if defect == 'legacy-unknown':
        manifest.pop('archive')
    lock = source_path('harness/benchmarks/data-lock.json', root=root)
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({'datasets': {'websearch': before}}))
    imported = root / 'evaluation/lexbench-websearch.json'
    imported.parent.mkdir()
    imported.write_text(json.dumps({'repository': 'old', 'revision': 'old', 'datasets': {},
                                   'definition_files': {'registry.json': 'old'}}))
    if defect == 'archive': manifest['archive']['sha256'] = '0' * 64
    if defect == 'file': manifest['files']['definitions/registry.json']['sha256'] = '0' * 64
    if defect == 'identity': manifest['track'] = 'webfetch'
    if defect == 'dataset-path': manifest['datasets']['sample']['path'] = 'missing.json'
    if defect == 'dataset-hash': manifest['datasets']['sample']['sha256'] = '0' * 64
    if defect == 'dataset-count': manifest['datasets']['sample']['count'] = 2
    def fetch(url, timeout):
        assert '/resolve/' + 'a' * 40 + '/' in url
        return io.BytesIO(json.dumps(manifest).encode() if url.endswith('manifest.json') else raw)
    monkeypatch.setattr(data, 'urlopen', fetch)
    return lock, imported


def test_pin_updates_current_bindings_and_retains_prior_snapshot(tmp_path, monkeypatch):
    lock, imported = fixture(tmp_path, monkeypatch)
    pinning.pin(tmp_path, 'websearch', 'a' * 40)
    selected = json.loads(lock.read_text())['datasets']['websearch']
    assert selected['revision'] == 'a' * 40
    assert selected['history'][0]['revision'] == '0' * 40
    assert json.loads(imported.read_text())['datasets']['sample']['count'] == 1
    assert pinning.validate_binding(tmp_path, 'lexbench-websearch') == 'a' * 40
    assert json.loads(imported.read_text())['repository'] == 'https://huggingface.co/datasets/Lexmount/LexBench-Web-Search'


def test_first_selection_registers_a_supported_benchmark_without_fake_history(tmp_path, monkeypatch):
    lock, imported = fixture(tmp_path, monkeypatch)
    lock.write_text(json.dumps({'datasets': {}}))
    imported.unlink()
    pinning.pin(tmp_path, 'websearch', 'a' * 40)
    selected = json.loads(lock.read_text())['datasets']['websearch']
    assert selected['repository'] == 'Lexmount/LexBench-Web-Search'
    assert selected['revision'] == 'a' * 40
    assert selected['datasets']['sample']['count'] == 1
    assert selected['history'] == []
    assert not imported.exists()


def test_unregistered_benchmark_cannot_create_a_selection(tmp_path, monkeypatch):
    lock, _ = fixture(tmp_path, monkeypatch)
    original = lock.read_bytes()
    with pytest.raises(ValueError, match='Unknown benchmark track'):
        pinning.pin(tmp_path, 'unknown-benchmark', 'a' * 40)
    assert lock.read_bytes() == original


@pytest.mark.parametrize('defect', ['archive', 'file', 'identity', 'legacy-unknown',
                                    'dataset-path', 'dataset-hash', 'dataset-count'])
def test_rejected_release_leaves_all_files_unchanged(tmp_path, monkeypatch, defect):
    paths = fixture(tmp_path, monkeypatch, defect)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert {path: path.read_bytes() for path in paths} == before


def test_recorded_historical_archive_supports_an_older_manifest(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch, 'legacy-known')
    pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert pinning.validate_binding(tmp_path, 'lexbench-websearch') == 'a' * 40


@pytest.mark.parametrize('defect', [None, 'population', 'duplicate-id'])
def test_jsonl_population_is_verified_even_without_an_import_binding(tmp_path, monkeypatch, defect):
    lock, imported = fixture(tmp_path, monkeypatch, defect, jsonl=True)
    imported.unlink()
    before = lock.read_bytes()
    if defect:
        with pytest.raises(ValueError, match='population mismatch'):
            pinning.pin(tmp_path, 'websearch', 'a' * 40)
        assert lock.read_bytes() == before
    else:
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
        assert pinning.validate_binding(tmp_path, 'lexbench-websearch') == 'a' * 40
    assert not imported.exists()


def test_jsonl_release_updates_existing_import_binding(tmp_path, monkeypatch):
    lock, imported = fixture(tmp_path, monkeypatch, jsonl=True)
    pinning.pin(tmp_path, 'websearch', 'a' * 40)
    selected = json.loads(lock.read_text())['datasets']['websearch']
    actual = json.loads(imported.read_text())
    assert actual['datasets'] == selected['datasets']
    assert actual['datasets']['sample']['format'] == 'jsonl'
    assert pinning.validate_binding(tmp_path, 'lexbench-websearch') == 'a' * 40


@pytest.mark.parametrize('revision', ['main', 'latest', 'abcd'])
def test_pin_rejects_floating_or_abbreviated_identity(tmp_path, revision):
    with pytest.raises(ValueError, match='full lowercase'):
        pinning.pin(tmp_path, 'websearch', revision)


@pytest.mark.parametrize('default', ['sample', 'retired'])
def test_pin_preserves_only_a_default_present_in_the_new_release(tmp_path, monkeypatch, default):
    paths = fixture(tmp_path, monkeypatch)
    lock = json.loads(paths[0].read_bytes())
    lock['datasets']['websearch']['default_dataset'] = default
    paths[0].write_text(json.dumps(lock))
    before = {path: path.read_bytes() for path in paths}
    if default == 'retired':
        with pytest.raises(ValueError, match='Default dataset is absent'):
            pinning.pin(tmp_path, 'websearch', 'a' * 40)
        assert {path: path.read_bytes() for path in paths} == before
    else:
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
        assert json.loads(paths[0].read_bytes())['datasets']['websearch']['default_dataset'] == default


@pytest.mark.parametrize('direction', ['consumer', 'source'])
@pytest.mark.parametrize('difference', [None, 'path', 'sha256', 'count', 'format', 'id_field', 'missing', 'file'])
def test_pin_checks_composed_sources_in_both_update_directions(tmp_path, monkeypatch, direction, difference):
    paths = fixture(tmp_path, monkeypatch)
    with data.urlopen('https://example.com/resolve/' + 'a' * 40 + '/manifest.json', timeout=60) as response:
        manifest = json.load(response)
    other = {'repository': 'Lexmount/other', 'revision': 'c' * 40,
             'datasets': copy.deepcopy(manifest['datasets']), 'files': copy.deepcopy(manifest['files'])}
    lock = json.loads(paths[0].read_bytes())
    if direction == 'consumer':
        lock['datasets']['websearch']['dataset_sources'] = {'sample': 'other'}
    else:
        other['dataset_sources'] = {'sample': 'websearch'}
    lock['datasets']['other'] = other
    if difference == 'missing':
        other['datasets'].pop('sample')
    elif difference == 'file':
        other['files'].pop('definitions/datasets/sample.json')
        # Only the selected provider's physical file is read by a consumer.
        if direction == 'source':
            difference = None
    elif difference:
        replacements = {'path': 'datasets/replacement.json', 'sha256': 'b' * 64,
                        'count': 2, 'format': 'jsonl', 'id_field': 'task_id'}
        other['datasets']['sample'][difference] = replacements[difference]
    paths[0].write_text(json.dumps(lock))
    before = {path: path.read_bytes() for path in paths}
    if difference:
        with pytest.raises(ValueError, match='dataset source|source file'):
            pinning.pin(tmp_path, 'websearch', 'a' * 40)
        assert {path: path.read_bytes() for path in paths} == before
    else:
        pinning.pin(tmp_path, 'websearch', 'a' * 40)


@pytest.mark.parametrize('failure_at', [1, 2])
@pytest.mark.parametrize('error', [OSError, KeyboardInterrupt])
def test_publication_failure_restores_both_original_bindings(tmp_path, monkeypatch, failure_at, error):
    paths = fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    replace = pinning.os.replace
    calls = []
    def fail_publish(source, destination):
        calls.append(destination)
        if len(calls) == failure_at:
            raise error('Injected publication failure')
        return replace(source, destination)
    monkeypatch.setattr(pinning.os, 'replace', fail_publish)
    with pytest.raises(error, match='Injected publication failure'):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert {path: path.read_bytes() for path in paths} == before
    assert not list(tmp_path.rglob('.dataset-pin-*'))


@pytest.mark.parametrize('failure_at', [1, 2])
@pytest.mark.parametrize('error', [KeyboardInterrupt, OSError])
def test_interruption_after_actual_replacement_restores_entire_binding_transaction(
        tmp_path, monkeypatch, failure_at, error):
    paths = fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    replace = pinning.os.replace
    published = []
    def interrupt_after_replace(source, destination):
        result = replace(source, destination)
        if source.name == 'next':
            published.append(destination)
            if len(published) == failure_at:
                raise error('Interrupted after replacement took effect')
        return result
    monkeypatch.setattr(pinning.os, 'replace', interrupt_after_replace)
    with pytest.raises(error, match='after replacement took effect'):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert {path: path.read_bytes() for path in paths} == before
    assert not list(tmp_path.rglob('.dataset-pin-*'))


def test_staging_failure_never_changes_a_binding(tmp_path, monkeypatch):
    paths = fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    write = type(paths[0]).write_bytes
    staged = []
    def fail_staging(path, raw):
        if path.name == 'next':
            staged.append(path)
            if len(staged) == 2:
                raise OSError('Injected full disk')
        return write(path, raw)
    monkeypatch.setattr(type(paths[0]), 'write_bytes', fail_staging)
    with pytest.raises(OSError, match='Injected full disk'):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert {path: path.read_bytes() for path in paths} == before
    assert not list(tmp_path.rglob('.dataset-pin-*'))


def test_failed_rollback_retains_the_original_for_recovery(tmp_path, monkeypatch):
    paths = fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    replace = pinning.os.replace
    calls = []
    def fail_publication_and_rollback(source, destination):
        calls.append(destination)
        if len(calls) >= 2:
            raise OSError('Storage unavailable')
        return replace(source, destination)
    monkeypatch.setattr(pinning.os, 'replace', fail_publication_and_rollback)
    with pytest.raises(OSError, match='original retained at'):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert paths[0].read_bytes() == before[paths[0]]
    backups = list(tmp_path.rglob('.dataset-pin-*/original'))
    # Every attempted replacement may have taken effect before reporting failure.
    assert len(backups) == len(paths)
    assert {path.read_bytes() for path in backups} == set(before.values())


@pytest.mark.parametrize('after_effect', [False, True])
def test_failed_lock_restore_does_not_skip_recoverable_evaluation_binding(tmp_path, monkeypatch, after_effect):
    lock, imported = fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in (lock, imported)}
    replace = pinning.os.replace
    def lock_failure(source, destination):
        if destination == lock:
            if source.name == 'next' and after_effect:
                replace(source, destination)
            raise OSError('Lock storage unavailable')
        return replace(source, destination)
    monkeypatch.setattr(pinning.os, 'replace', lock_failure)
    with pytest.raises(OSError, match='original retained at'):
        pinning.pin(tmp_path, 'websearch', 'a' * 40)
    assert imported.read_bytes() == before[imported]
    assert (lock.read_bytes() == before[lock]) is not after_effect
    backups = list(tmp_path.rglob('.dataset-pin-*/original'))
    assert len(backups) == 1
    assert backups[0].parent.parent == lock.parent
    assert backups[0].read_bytes() == before[lock]
    assert not list(imported.parent.glob('.dataset-pin-*'))


def coordinated_fixture(root, monkeypatch, defect=None):
    tracks = ['websearch', 'simpleqa-verified']
    lock = {'datasets': {}}
    releases = {}
    paths = []
    for track in tracks:
        old_raw = b'{"cases":[{"id":"qa","question":"old"}]}'
        new_question = 'mismatch' if defect == 'graph' and track == tracks[1] else 'new'
        raw = json.dumps({'cases': [{'id': 'qa', 'question': new_question}]}).encode()
        def contents(payload):
            binding = {'path': 'datasets/factual-v1.json', 'count': 1,
                       'sha256': hashlib.sha256(payload).hexdigest()}
            registry = {'datasets': {'factual-v1': binding}}
            files = {'definitions/registry.json': json.dumps(registry).encode()}
            if track == 'simpleqa-verified':
                files['definitions/datasets/factual-v1.json'] = payload
                files['definitions/factual-source.json'] = b'{"license":"upstream attribution"}'
            return files, registry
        files, registry = contents(raw)
        old_files, old_registry = contents(old_raw)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for name, value in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))
        archive = buffer.getvalue()
        manifest = {'schema': 'browser-eval-dataset-release/1', 'track': track,
                    'source_revision': 'b' * 40, 'datasets': registry['datasets'],
                    'archive': {'path': 'definitions.tar.gz', 'size': len(archive),
                                'sha256': hashlib.sha256(archive).hexdigest()},
                    'files': {name: {'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                              for name, value in files.items()}}
        if defect == 'archive' and track == tracks[1]:
            manifest['files']['definitions/registry.json']['sha256'] = '0' * 64
        releases[track] = (manifest, archive)
        selected = {'repository': 'Lexmount/' + track, 'revision': '0' * 40,
                    'source_revision': 'c' * 40, 'datasets': old_registry['datasets'],
                    'archive': {'sha256': '0' * 64},
                    'files': {name: {'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
                              for name, value in old_files.items()}}
        if track == tracks[0]:
            selected['dataset_sources'] = {'factual-v1': tracks[1]}
            selected['default_dataset'] = 'factual-v1'
        lock['datasets'][track] = selected
        imported = root / 'evaluation' / ('lexbench-' + track + '.json')
        imported.parent.mkdir(exist_ok=True)
        imported.write_text(json.dumps({'repository': 'old', 'revision': 'old',
                                       'datasets': old_registry['datasets'], 'definition_files': {}}))
        paths.append(imported)
    lock_path = source_path('harness/benchmarks/data-lock.json', root=root)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps(lock))
    paths.append(lock_path)
    def fetch(url, timeout):
        url = url if isinstance(url, str) else url.full_url
        track = next(name for name in tracks if '/' + name + '/resolve/' in url)
        manifest, raw = releases[track]
        return io.BytesIO(json.dumps(manifest).encode() if url.endswith('manifest.json') else raw)
    monkeypatch.setattr(data, 'urlopen', fetch)
    monkeypatch.setattr(data, 'binding', lambda track, revision=None: lock['datasets'][track])
    return paths


@pytest.mark.parametrize('first', ['websearch', 'simpleqa-verified'])
def test_composed_editions_cannot_be_pinned_in_either_single_order(tmp_path, monkeypatch, first):
    paths = coordinated_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError, match='Incompatible dataset source'):
        pinning.pin(tmp_path, first, 'a' * 40)
    assert {path: path.read_bytes() for path in paths} == before


def test_coordinated_pin_updates_the_composition_in_one_publication(tmp_path, monkeypatch):
    paths = coordinated_fixture(tmp_path, monkeypatch)
    publish = pinning.publish_bindings
    transactions = []
    def capture(updates):
        transactions.append(set(updates))
        publish(updates)
    monkeypatch.setattr(pinning, 'publish_bindings', capture)
    changes = pinning.pin_many(tmp_path, [('websearch', 'a' * 40), ('simpleqa-verified', 'a' * 40)])
    assert set(changes) == {'websearch', 'simpleqa-verified'}
    assert transactions == [set(paths)]
    selected = json.loads(paths[-1].read_bytes())['datasets']
    assert data.binding('simpleqa-verified')['revision'] == '0' * 40
    pinning.validate_selections(selected)
    for track in changes:
        assert selected[track]['revision'] == 'a' * 40
        assert pinning.validate_binding(tmp_path, 'lexbench-' + track) == 'a' * 40


@pytest.mark.parametrize('defect', ['graph', 'archive'])
def test_coordinated_pin_rejects_invalid_candidate_without_partial_updates(tmp_path, monkeypatch, defect):
    paths = coordinated_fixture(tmp_path, monkeypatch, defect)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError):
        pinning.pin_many(tmp_path, [('websearch', 'a' * 40), ('simpleqa-verified', 'a' * 40)])
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize('second_revision', ['a' * 40, 'b' * 40])
def test_coordinated_pin_rejects_duplicate_or_conflicting_tracks(tmp_path, monkeypatch, second_revision):
    paths = coordinated_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError, match='exactly once'):
        pinning.pin_many(tmp_path, [('websearch', 'a' * 40), ('websearch', second_revision)])
    assert {path: path.read_bytes() for path in paths} == before


def test_coordinated_pin_rolls_back_all_bindings_on_publication_failure(tmp_path, monkeypatch):
    paths = coordinated_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    replace = pinning.os.replace
    def fail_second_import(source, destination):
        if source.name == 'next' and destination == paths[1]:
            raise OSError('coordinated publication interrupted')
        return replace(source, destination)
    monkeypatch.setattr(pinning.os, 'replace', fail_second_import)
    with pytest.raises(OSError, match='coordinated publication interrupted'):
        pinning.pin_many(tmp_path, [('websearch', 'a' * 40), ('simpleqa-verified', 'a' * 40)])
    assert {path: path.read_bytes() for path in paths} == before
    assert not list(tmp_path.rglob('.dataset-pin-*'))


def test_coordinated_selection_cli_preserves_single_selection_usage(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pinning, 'pin_many', lambda root, selections:
                        calls.append((root, selections)) or {name: [] for name, _ in selections})
    with monkeypatch.context() as command:
        command.setattr('sys.argv', ['pin', '--root', str(tmp_path), '--select', 'websearch=' + 'a' * 40,
                                    '--select', 'simpleqa-verified=' + 'b' * 40])
        pinning.main()
    assert calls.pop() == (tmp_path, [('websearch', 'a' * 40), ('simpleqa-verified', 'b' * 40)])
    monkeypatch.setattr('sys.argv', ['pin', '--root', str(tmp_path), '--track', 'websearch',
                                   '--revision', 'c' * 40])
    pinning.main()
    assert calls == [(tmp_path, [('websearch', 'c' * 40)])]


def test_explicit_candidate_sources_never_fall_back_to_global_pins(tmp_path, monkeypatch):
    paths = coordinated_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    selected = json.loads(paths[-1].read_bytes())['datasets']['websearch']
    release = pinning.DatasetRelease('websearch', 'c' * 40, {
        'definitions/registry.json': json.dumps({'datasets': selected['datasets']}).encode(),
    })
    assert list(release.records()) == []  # Existing publication uses the global source normally.
    with pytest.raises(ValueError, match='External dataset descriptor differs'):
        list(release.records(selections={}))
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize('complete', [False, True])
def test_new_composed_release_build_uses_explicit_candidate_before_pinning(tmp_path, monkeypatch, complete):
    paths = coordinated_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in paths}
    selected = copy.deepcopy(json.loads(paths[-1].read_bytes())['datasets'])
    for track, item in selected.items():
        manifest = json.loads(pinning.read_hf_file(item['repository'], 'a' * 40, 'manifest.json', 2_000_000))
        item.update(revision='a' * 40, datasets=manifest['datasets'], files=manifest['files'])
    release = pinning.DatasetRelease('websearch', 'b' * 40, {
        'definitions/registry.json': json.dumps({'datasets': selected['websearch']['datasets']}).encode(),
    }, card_body='Task inputs\n{{dataset_summary}}', language_annotations={})
    output = tmp_path / 'new-release'
    if complete:
        built = release.build(output, selections=selected)
        assert built['dataset_sources'] == {'factual-v1': 'simpleqa-verified'}
        assert (output / 'README.md').is_file()
    else:
        with pytest.raises(ValueError, match='External dataset descriptor differs'):
            release.build(output, selections={})
        assert not output.exists()
    assert data.binding('simpleqa-verified')['revision'] == '0' * 40
    assert {path: path.read_bytes() for path in paths} == before
