"""Publication freezes uploads and refuses a changed local release authority."""
import json
import zipfile

import pytest

from harness.browser_use import report_publish as autonomous
from harness.reporting.publication import snapshot_publication
from harness.trajectory_replay import webarena_report_publish as replay


def test_private_archive_is_byte_exact_and_removed_on_error(tmp_path):
    archive = tmp_path / 'archive.zip'
    archive.write_bytes(b'frozen content')
    profile = tmp_path / 'profile.json'
    profile.write_text('{}')
    with pytest.raises(ValueError, match='inputs changed'):
        with snapshot_publication(archive, inputs=[profile]) as release:
            frozen = release.archive
            assert frozen.read_bytes() == archive.read_bytes()
            archive.write_bytes(b'changed')
            assert frozen.read_bytes() == b'frozen content'
            release.check()
    assert not frozen.exists()


@pytest.mark.parametrize('track', ['browser-use', 'replay'])
@pytest.mark.parametrize('damage', ['archive', 'profile', 'attestation'])
def test_lookup_input_change_stops_before_version_upload(tmp_path, monkeypatch, track, damage):
    owner = autonomous if track == 'browser-use' else replay
    files = {}
    for key in ('ARCHIVE', 'ATTESTATION', 'PROFILE', 'REVIEW', 'RECEIPT'):
        path = tmp_path / (key.lower() + '.json')
        path.write_text('{}')
        files[key] = path
        if hasattr(owner, key):
            monkeypatch.setattr(owner, key, path)
    with zipfile.ZipFile(files['ARCHIVE'], 'w') as bundle:
        bundle.writestr('index.html', 'Frozen release')
    publication = {'base_url': 'https://example.invalid', 'site_slug': 'site-1',
                   'expected_version': 'ver_old', 'share_url': 'https://example.invalid/v/token'}
    files['PROFILE'].write_text(json.dumps({'publication': publication}))
    files['RECEIPT'].write_text('{"slug":"site-1"}')
    monkeypatch.setattr(owner, 'validate_package', lambda: {})
    if track == 'replay':
        monkeypatch.setattr(owner, 'BINDING_JOURNAL', tmp_path / 'binding.json')
        monkeypatch.setattr(owner, 'OPERATION_JOURNAL', tmp_path / 'operation.json')
    calls = []
    def request(*args, **kwargs):
        path = args[1] if track == 'replay' else args[0]
        calls.append(path)
        assert path == '/api/sites/site-1'
        files[damage.upper()].write_text('Changed during site lookup')
        result = (200, b'{"site":{"currentVersionId":"ver_old"}}')
        return (*result, {}) if track == 'replay' else result
    monkeypatch.setattr(owner, 'request', request)
    with pytest.raises(ValueError, match='inputs changed'):
        owner.main(create=False)
    assert calls == ['/api/sites/site-1']
    assert json.loads(files['RECEIPT'].read_text()) == {'slug': 'site-1'}


def test_staged_upload_stops_before_commit_when_original_changes(tmp_path, monkeypatch):
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', 'Frozen root')
        bundle.writestr('assets/app.js', 'Frozen script')
    monkeypatch.setattr(replay, 'ARCHIVE', archive)
    calls = []
    def request(_base, path, data=None, _type=None, **kwargs):
        calls.append(path)
        if path == '/api/sites/site-1':
            result = {'site': {'currentVersionId': 'ver_old'}}
        elif path.startswith('/api/uploads?'):
            result = {'versionId': 'ver_staging'}
        else:
            assert path.endswith('/files/index.html') and data == b'Frozen root'
            archive.write_bytes(b'Changed during first PUT')
            result = {'ok': True}
        return 200, json.dumps(result).encode(), {}
    monkeypatch.setattr(replay, 'request', request)
    with snapshot_publication(archive, inputs=[]) as release:
        with pytest.raises(ValueError, match='inputs changed'):
            replay.staged_archive_upload('https://example.invalid', title='Replay', slug='site-1',
                                         expected_version='ver_old', identity={}, release=release)
    assert not any('/commit' in path for path in calls)


@pytest.mark.parametrize('outcome', [
    'success', 'wrong-export', 'bad-preview', 'duplicate-export', 'wrong-share',
    'missing-field', 'empty-map',
    'linked-report', 'linked-evidence', 'linked-archive',
])
def test_browser_publication_verifies_full_export_before_binding(tmp_path, monkeypatch, outcome):
    import io

    report = tmp_path / 'report'
    report.mkdir()
    (report / 'index.html').write_text(autonomous.PUBLIC_REPORT_MARKER)
    archive = tmp_path / 'release.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.write(report / 'index.html', 'index.html')
    profile = tmp_path / 'profile.json'
    share_url = 'https://example.invalid/v/token'
    if outcome == 'wrong-share':
        share_url = 'https://other.invalid/v/token'
    profile.write_text(json.dumps({'publication': {
        'base_url': 'https://example.invalid', 'site_slug': 'site-1',
        'expected_version': 'ver_old', 'title': 'Registry-driven report',
    }}))
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'slug': 'site-1', 'share_url': share_url}))
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    evidence_files = {}
    for name in ('manifest.json', 'run-config.json', 'attempts.jsonl', 'result-review.json'):
        (evidence / name).write_text('{}')
        evidence_files[name] = autonomous.sha256(evidence / name)
    if outcome == 'missing-field':
        evidence_files.pop('result-review.json')
    elif outcome == 'empty-map':
        evidence_files = {}
    proof = tmp_path / 'proof.json'
    proof.write_text(json.dumps({
        'schema': 'lex_browser_use_publication_attestation/2',
        'archive_sha256': autonomous.sha256(archive), 'profile_sha256': autonomous.sha256(profile),
        'report': str(report), 'report_tree_sha256': autonomous.tree_sha256(report),
        'evidence': [str(evidence)], 'evidence_files': {str(evidence): evidence_files},
    }))
    if outcome.startswith('linked-'):
        path = {'linked-report': report, 'linked-evidence': evidence,
                'linked-archive': archive}[outcome]
        backing = path.with_name(path.name + '-original')
        path.rename(backing)
        path.symlink_to(backing, target_is_directory=backing.is_dir())
    for key, path in [('ARCHIVE', archive), ('PROFILE', profile),
                      ('RECEIPT', receipt), ('ATTESTATION', proof)]:
        monkeypatch.setattr(autonomous, key, path)
    calls = []
    def request(path, data=None, content_type=None, **kwargs):
        assert kwargs['base_url'] == 'https://example.invalid'
        calls.append(path)
        if path == '/api/sites/site-1' and data is None:
            return 200, b'{"site":{"currentVersionId":"ver_old"}}'
        if '/versions?' in path:
            assert archive.read_bytes() in data and b'Registry-driven report' in data
            return 200, b'{"versionId":"ver_new"}'
        if path == '/api/sites/site-1' and kwargs.get('method') == 'PATCH':
            assert json.loads(data)['title'] == 'Registry-driven report'
            return 200, b'{}'
        assert path == '/api/sites/site-1/export'
        if outcome in ('wrong-export', 'duplicate-export'):
            exported = io.BytesIO()
            with zipfile.ZipFile(exported, 'w') as bundle:
                bundle.writestr('index.html', 'wrong bytes')
                if outcome == 'duplicate-export':
                    with pytest.warns(UserWarning, match='Duplicate name'):
                        bundle.writestr('index.html', autonomous.PUBLIC_REPORT_MARKER)
            return 200, exported.getvalue()
        return 200, archive.read_bytes()
    monkeypatch.setattr(autonomous, 'request', request)
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return (b'bad preview' if outcome == 'bad-preview'
                    else autonomous.PUBLIC_REPORT_MARKER.encode())
    def urlopen(request, **kwargs):
        assert request.full_url.startswith('https://example.invalid/')
        assert request.get_header('Authorization') is None
        if '/api/preview/' in request.full_url:
            assert request.get_header('X-artifact-share') == 'token'
        return Response()
    monkeypatch.setattr(autonomous.urllib.request, 'urlopen', urlopen)
    before = (profile.read_bytes(), proof.read_bytes(), receipt.read_bytes())
    if outcome == 'success':
        autonomous.main(create=False)
        publication = json.loads(profile.read_text())['publication']
        assert publication['expected_version'] == 'ver_new'
        assert publication['site_slug'] == 'site-1' and publication['share_url'] == share_url
        assert json.loads(proof.read_text())['profile_sha256'] == autonomous.sha256(profile)
    else:
        with pytest.raises((ValueError, RuntimeError)):
            autonomous.main(create=False)
        assert (profile.read_bytes(), proof.read_bytes(), receipt.read_bytes()) == before
        if outcome in ('missing-field', 'empty-map') or outcome.startswith('linked-'):
            assert not calls
