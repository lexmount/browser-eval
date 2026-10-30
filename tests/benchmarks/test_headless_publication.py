"""Headless releases preserve source protocols and keep scenario populations separate."""
import hashlib
import json
import tarfile
from dataclasses import replace

import pytest

from harness.benchmarks.publication import DatasetRelease
from tests.benchmarks.test_publication import git, checkout


@pytest.fixture
def headless(tmp_path):
    root = tmp_path / 'upstream'
    root.mkdir()
    subsets = [('L1', 'l1.raw', 'raw'), ('L2', 'l2.platform', 'platform')]
    original = {'manifest.json': json.dumps({'bench_id': 'original_id', 'bench_version': '0.4.2',
        'layers': [{'layer_id': layer, 'enabled': True, 'name': layer + '_protocol',
                    'subsets': [{'subset_id': identity, 'driver': 'fixed-driver',
                                 'task_glob': f'tasks/{layer}/{folder}/*.json'}]}
                   for layer, identity, folder in subsets]}).encode(),
        'harness_pins.json': b'{"original_dependency":"fixed-revision"}\n',
        'LICENSE': b'Original Apache-2.0 notice\n',
        'THIRD_PARTY_NOTICES.md': b'Original BSD-3-Clause fixture notice\n',
        'config/browser_driver_bindings.json': b'{"driver_ids":["first","second"]}\n',
        'config/capabilities.json': b'{"unchanged":"capability"}\n',
        'fixtures/page.html': b'<p>$Format:%H$</p>\n',
        'fixtures/binary.bin': b'\x00\xfffixture bytes',
        'tasks/scenarios/README.md': b'Original scenario contract\n'}
    for layer, identity, folder in subsets:
        # The same upstream ID in separate driver/subset populations is valid.
        original[f'tasks/{layer}/{folder}/one.json'] = json.dumps({
            'task_id': 'original-id', 'title': '保留原文 café', 'language': 'zh', 'layer': layer, 'subset_id': identity,
            'driver': {'kind': 'fixed-driver', 'steps': [{'op': 'read', 'expected': '原始值'}]},
            'grader': {'kind': 'inline_assertions', 'checks': [{'kind': 'equals', 'expected': 'Original answer'}]},
        }, ensure_ascii=False).encode() + b'\r\n'
    original['tasks/scenarios/one.scenario.json'] = json.dumps({'scenario_id': 'source-scenario',
        'title': 'Original scenario', 'steps': [{'op': 'navigate'}], 'checks': [{'kind': 'step_ok'}]}).encode()
    for name, raw in original.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (root / '.gitattributes').write_text('fixtures/page.html export-subst\ntasks/L2/platform/one.json export-ignore\n')
    for name in ('.env', 'runs/results.json', 'reports/index.html', 'private_artifacts/raw.json', 'runner/run.py'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('excluded measured/private/runtime bytes')
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Original headless definitions')
    return root, git(root, 'rev-parse', 'HEAD'), original


def test_headless_git_export_preserves_every_original_byte_and_ignores_outputs(headless, tmp_path):
    root, revision, original = headless
    (root / 'tasks/L1/raw/one.json').write_text('uncommitted replacement')
    release = DatasetRelease.from_git(root, 'headless-browser', revision)
    result = release.build(tmp_path / 'first')
    release.build(tmp_path / 'second')
    for name, raw in original.items():
        assert release.files['definitions/' + name] == raw
        assert (tmp_path / 'first/definitions' / name).read_bytes() == raw
    assert not any(name in release.files for name in ('.env', 'runs/results.json', 'reports/index.html', 'runner/run.py'))
    assert result['source_revision'] == revision
    assert result['upstream']['task_count'] == 2 and result['upstream']['scenario_count'] == 1
    assert result['upstream']['fixture_count'] == result['upstream']['config_count'] == 2
    assert result['upstream']['driver_count'] == 2
    assert [(row['config'], row['count']) for row in result['viewer']['configurations']] == [
        ('l1.raw', 1), ('l2.platform', 1)]
    assert not (tmp_path / 'first/data/scenario-specs.jsonl').exists()
    for config in ('l1.raw', 'l2.platform'):
        row = json.loads((tmp_path / 'first/data' / (config + '.jsonl')).read_bytes())
        assert json.loads(row['task']) == json.loads(original[row['source_path']])
        assert row['language'] == 'zh'
    assert (tmp_path / 'first/definitions.tar.gz').read_bytes() == (tmp_path / 'second/definitions.tar.gz').read_bytes()
    with tarfile.open(tmp_path / 'first/definitions.tar.gz') as archive:
        for name, raw in original.items():
            assert archive.extractfile('definitions/' + name).read() == raw
    for name, binding in result['files'].items():
        assert hashlib.sha256((tmp_path / 'first' / name).read_bytes()).hexdigest() == binding['sha256']
    card = (tmp_path / 'first/README.md').read_text()
    assert 'LexBench-Headless-Browser' in card and revision in card
    assert '{{upstream_' not in card
    assert '## Execute with the original runner' in card
    assert 'definitions/tasks/scenarios/' in card
    registry = json.loads(release.files['definitions/registry.json'])
    assert set(registry['source_files']) == set(original)
    # Exercise the card's exact data handoff against the local committed source.
    snippet = card.split('Create a fresh checkout', 1)[1].split('```python\n', 1)[1].split('```', 1)[0]
    snippet = snippet.replace('definitions = definition_path("headless-browser", ".")',
                              'definitions = Path(' + repr(str(tmp_path / 'first/definitions')) + ')')
    snippet = snippet.replace('Path("/tmp/Lexbench-Headless-Browser")',
                              'Path(' + repr(str(tmp_path / 'code-checkout')) + ')')
    snippet = snippet.replace('registry["source"]["repository"]', repr(str(root)))
    exec(compile(snippet, 'dataset-card-handoff', 'exec'), {})
    for name, raw in original.items():
        assert (tmp_path / 'code-checkout' / name).read_bytes() == raw
    assert not (tmp_path / 'code-checkout/viewer').exists()


def test_headless_projection_cannot_rewrite_assertions_even_with_updated_view_hash(headless, tmp_path):
    root, revision, _ = headless
    release = DatasetRelease.from_git(root, 'headless-browser', revision)
    files = dict(release.files)
    registry = json.loads(files['definitions/registry.json'])
    binding = registry['datasets']['l1.raw']
    projection = json.loads(files['definitions/' + binding['path']])
    projection['cases'][0]['grader']['checks'][0]['expected'] = 'Changed answer'
    raw = json.dumps(projection).encode()
    files['definitions/' + binding['path']] = raw
    binding['sha256'] = hashlib.sha256(raw).hexdigest()
    files['definitions/registry.json'] = json.dumps(registry).encode()
    with pytest.raises(ValueError, match='differs from original'):
        replace(release, files=files).build(tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('defect', ['missing-task', 'overlap', 'orphan-task', 'linked-fixture'])
def test_headless_rejects_incomplete_or_ambiguous_source_populations(headless, tmp_path, defect):
    root, _, _ = headless
    manifest = json.loads((root / 'manifest.json').read_bytes())
    if defect == 'missing-task':
        manifest['layers'][0]['subsets'][0]['task_glob'] = 'tasks/L1/absent/*.json'
    elif defect == 'overlap':
        manifest['layers'][1]['subsets'][0]['task_glob'] = 'tasks/L1/raw/*.json'
    elif defect == 'orphan-task':
        (root / 'tasks/orphan.json').write_text('{"task_id":"unregistered"}')
    else:
        (root / 'fixtures/link.html').symlink_to('page.html')
    # Restore the valid committed task before committing the chosen defect.
    (root / 'manifest.json').write_text(json.dumps(manifest))
    git(root, 'add', '.')
    git(root, 'commit', '-qm', defect)
    with pytest.raises(ValueError):
        DatasetRelease.from_git(root, 'headless-browser', git(root, 'rev-parse', 'HEAD')).build(tmp_path / 'bad')


def test_historical_flat_definitions_are_selected_from_commit_tree(tmp_path):
    root, _, cases = checkout(tmp_path, 'websearch')
    (root / 'src/lexbench_websearch').rename(root / 'lexbench_websearch')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Historical flat layout')
    revision = git(root, 'rev-parse', 'HEAD')
    release = DatasetRelease.from_git(root, 'websearch', revision)
    release.build(tmp_path / 'release')
    row = json.loads((tmp_path / 'release/data/sample.jsonl').read_bytes())
    assert json.loads(row['task']) == cases[0]


def test_headless_hub_snapshot_uses_registered_repo_and_keeps_original_source_identity(headless, tmp_path):
    root, upstream_revision, original = headless
    hub = tmp_path / 'hub'
    DatasetRelease.from_git(root, 'headless-browser', upstream_revision).build(hub)
    git(hub, 'init', '-q')
    git(hub, 'add', '.')
    git(hub, 'commit', '-qm', 'Pinned public definitions')
    hub_revision = git(hub, 'rev-parse', 'HEAD')
    release = DatasetRelease.from_git(hub, 'headless-browser', hub_revision)
    result = release.build(tmp_path / 'rebuilt')
    assert result['source_repository'] == 'https://huggingface.co/datasets/Lexmount/LexBench-Headless-Browser'
    assert result['source_revision'] == hub_revision
    assert result['upstream']['revision'] == upstream_revision
    assert json.loads(release.files['definitions/registry.json'])['source_files'].keys() == original.keys()
    for name, raw in original.items():
        assert (tmp_path / 'rebuilt/definitions' / name).read_bytes() == raw
