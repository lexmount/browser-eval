"""Public assembly preserves all declared evidence from each physical call."""
import base64
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from harness.web_fetch.artifacts import artifact_bindings
from harness.web_fetch.assembly import assemble
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.reference_inputs import import_inputs
from harness.web_fetch.reference_lifecycle import finalize_reference_checks
from harness.web_fetch.report_build import build_report
from harness.web_fetch.runner import run_evaluation


@pytest.fixture
def source(tmp_path):
    class Retrying(Provider):
        calls = 0

        def fetch(self, case, timeout):
            self.calls += 1
            return ProviderResult(True, 'Expected' if self.calls == 2 else 'Wrong',
                metadata={'_rendered_html': '<main>retained HTML</main>',
                          'rendered_html_sha256': hashlib.sha256(b'<main>retained HTML</main>').hexdigest(),
                          'raw_stdout_sha256': hashlib.sha256(b'retained stdout').hexdigest(),
                          '_raw_stdout_b64': base64.b64encode(b'retained stdout').decode()})

    root = tmp_path / 'source'
    case = EvalCase('one', 'One', 'https://example.invalid/', 'synthetic', ['Expected'])
    dataset = {'id': 'synthetic', 'cases': [asdict(case)],
               'coverage_plan': {'web': ['retrying']}, 'comparison': {}}
    records, _ = run_evaluation(output_dir=root, dataset_meta=dataset,
        dataset_path=tmp_path / 'dataset.json', cases=[case],
        providers=[Retrying('retrying', {'type': 'fixture'})],
        provider_config_path=tmp_path / 'providers.json', runs=1,
        timeout=1, delay=0, max_failure_retries=1)
    for folder in ('sources', 'list-references'):
        (root / folder).mkdir(exist_ok=True)
    return root, records


def test_assembly_preserves_all_physical_call_evidence(source, tmp_path):
    root, records = source
    target = tmp_path / 'assembled'
    assemble(root, tmp_path / 'certificates', [], target)
    assert [json.loads(line) for line in (target / 'results.jsonl').read_text().splitlines()] == records
    for relative, _ in artifact_bindings(records[0]):
        assert (target / relative).read_bytes() == (root / relative).read_bytes()


def test_assembly_and_report_include_only_current_task_references(source, tmp_path):
    root, _ = source
    files = {'sources/one/reference.txt': b'selected source',
             'list-references/one.json': b'{"case_id":"one"}',
             'sources/other/reference.txt': b'other task source',
             'list-references/other.json': b'{"case_id":"other"}',
             'sources/internal-note.txt': b'synthetic private note'}
    for relative, raw in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    output = tmp_path / 'assembled'
    assemble(root, tmp_path / 'certificates', [], output)
    for relative in ('sources/other/reference.txt', 'list-references/other.json',
                     'sources/internal-note.txt'):
        assert not (output / relative).exists()
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[relative])
    report = build_report(output, tmp_path / 'report').parent
    for relative, raw in files.items():
        if '/one' in relative:
            assert (output / relative).read_bytes() == raw
            assert (report / relative).read_bytes() == raw
        else:
            assert not (report / relative).exists()


@pytest.mark.parametrize('operation', ['addition', 'replacement'])
def test_assembly_uses_reference_inputs_from_the_declared_task_owner(source, tmp_path, operation):
    class Fixture(Provider):
        def fetch(self, case, timeout):
            return ProviderResult(True, 'Expected')

    root, _ = source
    dataset = json.loads((root / 'dataset.json').read_text())
    original = dataset['cases'][0]
    case = EvalCase(**original) if operation == 'replacement' else EvalCase(
        'two', 'Two', 'https://example.invalid/two', 'synthetic', ['Expected'])
    incoming = tmp_path / 'incoming'
    run_evaluation(output_dir=incoming, dataset_meta={**dataset, 'cases': [asdict(case)]},
        dataset_path=tmp_path / 'input.json', cases=[case],
        providers=[Fixture('retrying', {'type': 'fixture'})],
        provider_config_path=tmp_path / 'providers.json', runs=1, timeout=1, delay=0)
    for owner, text in ((root, 'old source'), (incoming, 'new source')):
        folder = owner / 'sources' / case.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'reference.txt').write_text(text)
        reference = owner / 'list-references' / (case.id + '.json')
        reference.parent.mkdir(exist_ok=True)
        reference.write_text(json.dumps({'case_id': case.id, 'source': text}))
    output = tmp_path / 'assembled'
    assemble(root, tmp_path / 'certificates', [incoming] if operation == 'replacement' else [],
             output, additions=[incoming] if operation == 'addition' else [])
    assert (output / 'sources' / case.id / 'reference.txt').read_text() == 'new source'
    assert json.loads((output / 'list-references' / (case.id + '.json')).read_text())['source'] == 'new source'


@pytest.mark.parametrize('damage', [None, 'changed', 'missing'])
def test_assembly_copies_only_certificate_bound_reference_checks(tmp_path, damage):
    class Fixture(Provider):
        def fetch(self, case, timeout):
            return ProviderResult(True, 'Expected independent article')

    root = tmp_path / 'fresh-run'
    text = 'Expected independent article'
    case = EvalCase('one', 'One', 'https://example.invalid/', 'synthetic', ['Expected'],
                    ground_truth_main_content=text,
                    source_verification={'state': 'valid', 'method': 'independent source capture',
                        'checked_at': datetime.now(timezone.utc).isoformat(),
                        'reference_sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'reference_scope': 'Complete article'})
    dataset = {'id': 'synthetic', 'cases': [asdict(case)],
               'coverage_plan': {'web': ['fixture']}, 'comparison': {}}
    run_evaluation(output_dir=root, dataset_meta=dataset, dataset_path=tmp_path / 'dataset.json',
        cases=[case], providers=[Fixture('fixture', {'type': 'fixture'})],
        provider_config_path=tmp_path / 'providers.json', runs=1, timeout=1, delay=0)
    manifest = json.loads((root / 'manifest.json').read_text())
    now = datetime.now(timezone.utc)
    observations = tmp_path / 'observations.json'
    observations.write_text(json.dumps([{'case_id': 'one', 'independent': True,
        'content_text': text, 'checked_at': now.isoformat(),
        'scope_identity': manifest['reference_checks']['cases']['one']['scope_identity'],
        'completion': {'satisfied': True, 'elapsed_ms': 1, 'timeout_ms': 1000}}]))
    finalize_reference_checks(root, observations, now=now + timedelta(seconds=1))
    manifest = json.loads((root / 'manifest.json').read_text())
    (root / 'execution.json').write_text(json.dumps({'batches': [manifest]}))
    (root / 'manifest.json').unlink()
    for folder in ('sources', 'list-references'):
        (root / folder).mkdir()
    (root / 'reference-checks/private-note.txt').write_text('synthetic private note')
    output = tmp_path / 'assembled'
    relative = manifest['reference_checks']['postcheck']['evidence_path']
    if damage:
        if damage == 'changed':
            (root / relative).write_text('[]')
        else:
            (root / relative).unlink()
        with pytest.raises((ValueError, OSError)):
            assemble(root, tmp_path / 'certificates', [], output)
        assert not output.exists()
        assert not list(tmp_path.glob('.assembled-*'))
        return
    assemble(root, tmp_path / 'certificates', [], output)
    assert (output / relative).read_bytes() == (root / relative).read_bytes()
    assert not (output / 'reference-checks/private-note.txt').exists()
    (output / 'reference-checks/private-note.txt').write_text('synthetic private note')
    report = build_report(output, tmp_path / 'report').parent
    assert (report / relative).read_bytes() == (root / relative).read_bytes()
    assert not (report / 'reference-checks/private-note.txt').exists()


@pytest.mark.parametrize('tree', ['sources', 'list-references', 'reference-checks'])
@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_assembly_rejects_links_inside_reference_trees(source, tmp_path, tree, kind):
    root, _ = source
    folder = root / tree
    folder.mkdir(exist_ok=True)
    actual = root / 'same-bytes'
    if kind == 'directory':
        actual.mkdir()
        (actual / 'reference.txt').write_text('synthetic source')
    else:
        actual.write_text('synthetic source')
    (folder / 'linked').symlink_to(actual, target_is_directory=kind == 'directory')
    output = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        assemble(root, tmp_path / 'certificates', [], output)
    assert not output.exists()
    assert not list(tmp_path.glob('.rejected-*'))


def test_assembly_rejects_links_in_custom_canonical_sources(source, tmp_path):
    root, _ = source
    canonical = tmp_path / 'canonical/dataset.json'
    canonical.parent.mkdir()
    canonical.write_bytes((root / 'dataset.json').read_bytes())
    (canonical.parent / 'sources').mkdir()
    (canonical.parent / 'list-references').mkdir()
    actual = canonical.parent / 'reference.txt'
    actual.write_text('synthetic source')
    (canonical.parent / 'sources/linked').symlink_to(actual)
    output = tmp_path / 'rejected'
    with pytest.raises(ValueError, match='[Ss]ymlink'):
        assemble(root, tmp_path / 'certificates', [], output, canonical_dataset=canonical)
    assert not output.exists()
    assert not list(tmp_path.glob('.rejected-*'))


@pytest.mark.parametrize('damage', [False, True])
def test_assembly_uses_verified_external_reference_trees(source, tmp_path, monkeypatch, damage):
    root, _ = source
    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path / 'data-home'))
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    canonical = checkout / 'dataset.json'
    canonical.write_bytes((root / 'dataset.json').read_bytes())
    approved = tmp_path / 'approved'
    files = {'sources/one/reference.txt': b'approved reference',
             'list-references/one.json': b'{"scope":"approved"}',
             'sources/other/reference.txt': b'approved other task',
             'list-references/other.json': b'{"scope":"other"}'}
    trees = {'sources': {}, 'list-references': {}}
    for relative, raw in files.items():
        path = approved / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        tree, child = relative.split('/', 1)
        trees[tree][child] = hashlib.sha256(raw).hexdigest()
    manifest = checkout / 'source-inputs.json'
    manifest.write_text(json.dumps({'schema_version': 1, 'kind': 'frozen_reference_inputs',
        'id': 'assembly-fixture', 'dataset': 'dataset.json',
        'dataset_sha256': hashlib.sha256(canonical.read_bytes()).hexdigest(), 'trees': trees}))
    external = import_inputs(manifest, approved)
    target = tmp_path / 'external-assembly'
    if damage:
        (external / 'sources/one/reference.txt').write_bytes(b'changed reference')
        with pytest.raises(ValueError):
            assemble(root, tmp_path / 'certificates', [], target, canonical_dataset=canonical)
        assert not target.exists()
    else:
        assemble(root, tmp_path / 'certificates', [], target, canonical_dataset=canonical)
        for relative, raw in files.items():
            if '/one' in relative:
                assert (target / relative).read_bytes() == raw
            else:
                assert not (target / relative).exists()
    assert not (checkout / 'sources').exists()


@pytest.mark.parametrize('change', ['changed-retry', 'missing-retry', 'linked-primary', 'linked-root'])
def test_assembly_rejects_changed_missing_or_linked_evidence(source, tmp_path, change):
    root, records = source
    record = records[0]
    primary = root / record['artifact_path']
    first = record['provider_metadata']['retry']['attempts'][0]
    retry_aux = root / next(path for path in first['artifact_paths'] if path != first['artifact_path'])
    if change == 'changed-retry':
        retry_aux.write_bytes(b'changed after collection')
    elif change == 'missing-retry':
        retry_aux.unlink()
    elif change == 'linked-primary':
        actual = root / 'same-output.txt'
        actual.write_bytes(primary.read_bytes())
        primary.unlink()
        primary.symlink_to(actual)
    else:
        alias = tmp_path / 'source-link'
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    target = tmp_path / 'rejected'
    with pytest.raises(ValueError):
        assemble(root, tmp_path / 'certificates', [], target)
    assert not target.exists()
    assert not list(tmp_path.glob('.rejected-*'))
