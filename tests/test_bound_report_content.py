import hashlib
import json
import zipfile

import pytest
from bs4 import BeautifulSoup

from harness.report_languages import write_language_contract
from harness.reporting.assets import load_bound_content, restore_bound_content
from harness.reporting.packs import write_packs
from harness.web_fetch.report import _wmb_guide
from harness.web_fetch.webmainbench_report_check import validate_sample_content


@pytest.fixture
def content_inputs(tmp_path):
    source = tmp_path / 'assets'
    evidence = tmp_path / 'evidence'
    authority = tmp_path / 'authority'
    for path in (source, evidence, authority):
        path.mkdir()
    values = [(source / 'content.json', b'{"guide":{"zh":"Synthetic finding"}}'),
              (evidence / 'results.jsonl', b'{"case":"one","score":0}\n'),
              (authority / 'profile.json', b'{"participants":["a"]}')]
    for path, raw in values:
        path.write_bytes(raw)
    binding = {
        'schema_version': 1, 'relative_path': 'content.json',
        'sha256': hashlib.sha256(values[0][1]).hexdigest(),
        'evidence': {'results.jsonl': hashlib.sha256(values[1][1]).hexdigest()},
        'authority': {'profile.json': hashlib.sha256(values[2][1]).hexdigest()},
    }
    return source, evidence, authority, binding


def test_bound_content_requires_exact_inputs(content_inputs):
    source, evidence, authority, binding = content_inputs
    assert load_bound_content(source, binding, evidence_root=evidence,
                              authority_root=authority)['guide']['zh'] == 'Synthetic finding'


@pytest.mark.parametrize('field', ['content', 'evidence', 'authority'])
@pytest.mark.parametrize('change', ['bytes', 'missing', 'symlink'])
def test_stale_missing_or_linked_content_cannot_be_reused(content_inputs, field, change):
    source, evidence, authority, binding = content_inputs
    path = {'content': source / 'content.json', 'evidence': evidence / 'results.jsonl',
            'authority': authority / 'profile.json'}[field]
    if change == 'bytes':
        path.write_text('changed')
    else:
        raw = path.read_bytes()
        path.unlink()
        if change == 'symlink':
            other = path.with_name('other')
            other.write_bytes(raw)
            path.symlink_to(other)
    with pytest.raises((ValueError, FileNotFoundError)):
        load_bound_content(source, binding, evidence_root=evidence, authority_root=authority)


def test_content_cannot_escape_or_omit_required_bindings(content_inputs):
    source, evidence, authority, binding = content_inputs
    for field in ('evidence', 'authority'):
        candidate = {**binding, field: {}}
        with pytest.raises(ValueError, match='lacks'):
            load_bound_content(source, candidate, evidence_root=evidence, authority_root=authority)
    binding['relative_path'] = '../outside.json'
    with pytest.raises(ValueError):
        load_bound_content(source, binding, evidence_root=evidence, authority_root=authority)


def test_existing_hash_only_content_contract_remains_supported(content_inputs):
    source, _, _, binding = content_inputs
    binding = {key: value for key, value in binding.items() if key not in {'evidence', 'authority'}}
    assert load_bound_content(source, binding) == json.loads((source / 'content.json').read_bytes())


@pytest.mark.parametrize('tamper', ['none', 'rendered', 'binding', 'results'])
def test_release_boundary_checks_rendered_content_and_exact_authority(content_inputs, tamper):
    source, evidence, authority, binding = content_inputs
    binding_path = authority / 'evaluation/webmainbench-content-binding.json'
    binding_path.parent.mkdir()
    binding_path.write_text(json.dumps(binding))
    (evidence / 'report-content-binding.json').write_bytes(binding_path.read_bytes())
    (evidence / 'content.json').write_bytes((source / 'content.json').read_bytes())
    write_language_contract(evidence, include_english=False)
    page = evidence / 'guide/index.html'
    page.parent.mkdir()
    page.write_text('<main>Synthetic finding</main>')
    if tamper == 'rendered':
        page.write_text('<main>Stale conclusion</main>')
    elif tamper == 'binding':
        (evidence / 'report-content-binding.json').write_text('{}')
    elif tamper == 'results':
        (evidence / 'results.jsonl').write_text('changed')
    if tamper == 'none':
        validate_sample_content(evidence, authority_root=authority)
    else:
        with pytest.raises(ValueError):
            validate_sample_content(evidence, authority_root=authority)


@pytest.mark.parametrize('values', [(0.2, 0.7), (0.2, 0.2), (None, None)])
def test_generic_guide_uses_current_scores_populations_and_unique_extremes(values, tmp_path):
    from harness.reporting.links import check

    names = ['method-a', 'method-b']
    rows = [{
        'provider': name, 'case_id': 'one', '_body_present': True,
        'elapsed_ms': 20 if index == 0 else 40, 'total_elapsed_ms': 100,
        'metrics': {'formula_edit': {'error': 'Both predicted and groundtruth are empty'}},
    } for index, name in enumerate(names)]
    guide = _wmb_guide(
        english=False, names=names, labels={name: name for name in names},
        cases={'one': {}}, bycase={'one': rows}, case_count=1,
        score=lambda name, metric: values[names.index(name)],
        denom=lambda *args: 1, failures=lambda name: [],
    )
    soup = BeautifulSoup(guide, 'html.parser')
    expected_winners = 6 if values[0] != values[1] else 0
    assert len(soup.select('.wmb-guide-results .comparison-winner')) == expected_winners
    assert len(soup.select('.wmb-guide-latency .comparison-winner')) == 3
    assert '32.4%' not in guide and '148/200' not in guide
    assert '1 页、2 种方法' in soup.get_text()
    root = tmp_path / 'report'
    for route in ['guide/index.html', 'providers/method-a.html', 'providers/method-b.html',
                  'datasets/one.html', 'analysis/index.html']:
        target = root / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(guide if route == 'guide/index.html' else '<p>Synthetic target</p>')
    check(root)


@pytest.mark.parametrize('tamper', [
    'none', 'zip-metadata', 'archive', 'manifest', 'block', 'content', 'source', 'existing',
])
def test_offline_recovery_verifies_pinned_export_and_preserves_existing_content(tmp_path, tamper):
    page = b'<html><main><p>Synthetic result</p></main></html>'
    digest = hashlib.sha256(page).hexdigest()
    packs = tmp_path / 'packs'
    packs.mkdir()
    blocks, _ = write_packs([(digest, page)], packs, limit=1024,
                            pattern='data/{index:03}.gz', compresslevel=6)
    manifest = {'schema_version': 2, 'blocks': blocks, 'files': {'guide.html': {
        'parts': [digest], 'sha256': digest, 'size': len(page)}}}
    manifest_bytes = json.dumps(manifest).encode()
    export = tmp_path / 'export.zip'
    with zipfile.ZipFile(export, 'w') as archive:
        archive.writestr('manifest.json', manifest_bytes)
        archive.write(packs / 'data/000.gz', 'data/000.gz')
    payload = (json.dumps({'schema_version': 1, 'guide': {'zh': '<p>Synthetic result</p>'}},
                          ensure_ascii=False, indent=2) + '\n').encode()
    binding = {'sha256': hashlib.sha256(payload).hexdigest(), 'source': {
        'archive_sha256': hashlib.sha256(export.read_bytes()).hexdigest(),
        'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'files_sha256': {'guide.html': digest},
        'recovery': {'kind': 'html-main-json-v1', 'fields': {'guide': {'zh': 'guide.html'}}},
    }}
    destination = tmp_path / 'content.json'
    if tamper == 'archive':
        export.write_bytes(b'changed')
    elif tamper in {'zip-metadata', 'manifest', 'block'}:
        with zipfile.ZipFile(export, 'w') as archive:
            archive.writestr(zipfile.ZipInfo('manifest.json', (2000, 1, 1, 0, 0, 0)),
                             b'{}' if tamper == 'manifest' else manifest_bytes)
            if tamper == 'block':
                import gzip
                archive.writestr('data/000.gz', gzip.compress(b'changed'))
            else:
                archive.write(packs / 'data/000.gz', 'data/000.gz')
        repacked_hash = hashlib.sha256(export.read_bytes()).hexdigest()
        assert repacked_hash != binding['source']['archive_sha256']
    elif tamper == 'content':
        binding['sha256'] = '0' * 64
    elif tamper == 'source':
        binding['source']['files_sha256']['guide.html'] = '0' * 64
    elif tamper == 'existing':
        destination.write_bytes(b'previous content')
    if tamper in {'none', 'zip-metadata'}:
        assert restore_bound_content(export, binding, destination) == destination
        assert destination.read_bytes() == payload
    else:
        with pytest.raises((ValueError, FileExistsError, zipfile.BadZipFile)):
            restore_bound_content(export, binding, destination)
        if tamper == 'existing':
            assert destination.read_bytes() == b'previous content'
        else:
            assert not destination.exists()
