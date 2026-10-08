"""Public assembly preserves all declared evidence from each physical call."""
import base64
import hashlib
import json
from dataclasses import asdict

import pytest

from harness.web_fetch.artifacts import artifact_bindings
from harness.web_fetch.assembly import assemble
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.reference_inputs import import_inputs
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
             'list-references/one.json': b'{"scope":"approved"}'}
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
            assert (target / relative).read_bytes() == raw
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
