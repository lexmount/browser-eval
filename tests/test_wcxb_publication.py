"""WCXB packaging rejects undeclared files and verifies submission before rebinding."""
import io
import json
import zipfile

import pytest

from harness.web_fetch import wcxb_publication as release
from tests.wcxb_fixture import write_json


@pytest.fixture
def report(tmp_path, monkeypatch):
    authority, source = tmp_path / 'authority', tmp_path / 'report'
    source.mkdir()
    (source / 'index.html').write_text('<!doctype html><h1>Synthetic WCXB</h1>')
    (source / '.generated-report').write_text('generated\n')
    profile = {'comparison_kind': 'wcxb_frozen_html_to_markdown', 'case_count': 2,
               'artifact_hub_base_url': 'https://artifacts.example.invalid',
               'artifact_hub_site': 'wcxb-site', 'artifact_hub_expected_version': 'ver_old',
               'artifact_hub_share_url': 'https://artifacts.example.invalid/v/public-token'}
    profile_path = authority / 'evaluation/wcxb/current-report.json'
    write_json(profile_path, profile)
    write_json(profile_path.with_name('report-content-binding.json'), {'synthetic': True})
    release.write_inventory(source, profile)
    checks = []
    monkeypatch.setattr('harness.web_fetch.wcxb_report_check.check',
                        lambda path, **kw: checks.append(('semantic', path, kw)))
    monkeypatch.setattr('harness.reporting.links.check', lambda path: checks.append(('links', path)))
    monkeypatch.setattr(release, 'load_token', lambda **kw: pytest.fail('synthetic transport loaded credentials'))
    return dict(source=source, authority_root=authority, work=tmp_path / 'private'), profile_path, checks


class Site:
    def __init__(self):
        self.version = 'ver_old'
        self.files = {}
        self.calls = []
        self.bad_export = False

    def __call__(self, path, data, content_type, **kw):
        self.calls.append((path, kw, data))
        if path.endswith('/export'):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as bundle:
                for name, payload in self.files.items():
                    bundle.writestr(name, payload + (b'changed' if self.bad_export else b''))
            return 200, stream.getvalue()
        if kw['method'] == 'PATCH':
            assert json.loads(data)['title'] == 'WCXB 主内容提取报告 · 2 页评测'
            return 200, b'{}'
        if path.startswith('/api/operations/'):
            if self.version != 'ver_new':
                return 404, b'{}'
            key = next(kw['headers']['Idempotency-Key'] for name, kw, _ in self.calls
                       if '/commit?' in name)
            assert path.endswith(key)
            return 200, b'{"status":"completed","httpStatus":200,"result":{"slug":"wcxb-site","versionId":"ver_new"}}'
        if path.startswith('/api/sites/'):
            return 200, json.dumps({'version': {'id': self.version, 'entry': 'index.html'}}).encode()
        if path.startswith('/api/uploads?'):
            assert kw['headers']['Idempotency-Key'].startswith('wcxb-start-')
            return 200, b'{"versionId":"stage_one"}'
        if kw['method'] == 'PUT':
            self.files[path.split('/files/', 1)[1]] = data
            return 200, b'{}'
        if '/commit?' in path:
            assert kw['headers']['Idempotency-Key'].startswith('wcxb-commit-')
            self.version = 'ver_new'
            return 200, b'{"slug":"wcxb-site","versionId":"ver_new"}'
        if path == '/v/public-token':
            assert kw['auth'] is False
            assert 'Authorization' not in kw['headers']
            return 200, b'<!doctype html><title>Public report</title>'
        raise AssertionError(path)


def test_profile_bound_submission_uses_declared_files_and_verifies_share(report):
    kwargs, profile_path, checks = report
    before = json.loads(profile_path.read_bytes())
    site = Site()
    result = release.publish(**kwargs, transport=site)
    assert result['versionId'] == 'ver_new'
    assert set(site.files) == {'index.html', release.INVENTORY}
    after = json.loads(profile_path.read_bytes())
    assert after == {**before, 'artifact_hub_expected_version': 'ver_new'}
    assert [row[0] for row in checks] == ['semantic', 'links']
    assert any(path == '/v/public-token' for path, _, _ in site.calls)
    journal = next(kwargs['work'].glob('wcxb-update-*.json'))
    assert json.loads(journal.read_bytes())['verified'] is True
    release.inventory(kwargs['source'], after)


@pytest.mark.parametrize('change', ['extra-file', 'changed-file', 'missing-file', 'profile', 'link'])
def test_bad_public_boundary_stops_before_remote_or_overwrite(report, change, tmp_path):
    kwargs, profile_path, _ = report
    source = kwargs['source']
    if change == 'extra-file':
        (source / 'private-note.txt').write_text('internal note')
    elif change == 'changed-file':
        (source / 'index.html').write_text('changed after build')
    elif change == 'missing-file':
        (source / 'index.html').unlink()
    elif change == 'profile':
        value = json.loads(profile_path.read_bytes());value['case_count'] = 3;write_json(profile_path, value)
    else:
        (source / 'linked').symlink_to(tmp_path, target_is_directory=True)
    kwargs['work'].mkdir()
    archive, proof = kwargs['work'] / 'wcxb-current.zip', kwargs['work'] / 'wcxb-package.json'
    archive.write_bytes(b'previous archive');proof.write_bytes(b'previous proof')
    site = Site()
    with pytest.raises(ValueError):
        release.publish(**kwargs, transport=site)
    assert site.calls == []
    assert archive.read_bytes() == b'previous archive'
    assert proof.read_bytes() == b'previous proof'


def test_wrong_remote_version_stops_before_any_upload(report):
    kwargs, profile_path, _ = report
    before = profile_path.read_bytes()
    site = Site();site.version = 'ver_somebody_else'
    with pytest.raises(ValueError, match='version changed'):
        release.publish(**kwargs, transport=site)
    assert len(site.calls) == 2
    assert profile_path.read_bytes() == before


def test_failed_export_verification_resumes_without_reupload(report):
    kwargs, profile_path, _ = report
    before = profile_path.read_bytes()
    site = Site();site.bad_export = True
    with pytest.raises(ValueError, match='Deployed WCXB bytes differ'):
        release.publish(**kwargs, transport=site)
    assert profile_path.read_bytes() == before
    uploads = sum(kw['method'] == 'PUT' for _, kw, _ in site.calls)
    site.bad_export = False
    result = release.publish(**kwargs, transport=site)
    assert result['versionId'] == 'ver_new'
    assert sum(kw['method'] == 'PUT' for _, kw, _ in site.calls) == uploads


@pytest.mark.parametrize('input_kind', ['profile', 'content-binding'])
def test_release_authority_drift_during_remote_read_stops_before_staging(report, input_kind):
    kwargs, profile_path, _ = report
    site = Site()
    path = profile_path if input_kind == 'profile' else profile_path.with_name(
        'report-content-binding.json')

    def drift(*args, **kw):
        result = site(*args, **kw)
        path.write_bytes(path.read_bytes() + b' ')
        return result

    with pytest.raises(ValueError, match='inputs changed'):
        release.publish(**kwargs, transport=drift)
    assert len(site.calls) == 1
    assert not site.files


def test_lost_commit_response_recovers_the_same_operation_without_reupload(report):
    kwargs, profile_path, _ = report
    before = profile_path.read_bytes()
    site = Site()

    def lose_response(path, *args, **kw):
        result = site(path, *args, **kw)
        if '/commit?' in path:
            raise TimeoutError('synthetic lost response after commit')
        return result

    with pytest.raises(TimeoutError, match='lost response'):
        release.publish(**kwargs, transport=lose_response)
    assert profile_path.read_bytes() == before
    uploads = sum(kw['method'] == 'PUT' for _, kw, _ in site.calls)
    result = release.publish(**kwargs, transport=site)
    assert result['versionId'] == 'ver_new'
    assert sum(kw['method'] == 'PUT' for _, kw, _ in site.calls) == uploads


def test_profile_binding_interruption_resumes_without_another_commit(report, monkeypatch):
    kwargs, profile_path, _ = report
    site = Site()
    original = release.atomic_write
    failed = False

    def interrupt(path, payload):
        nonlocal failed
        value = json.loads(payload)
        if path.name.startswith('wcxb-update-') and value.get('verified') and not failed:
            failed = True
            raise OSError('interrupted after profile binding')
        original(path, payload)

    monkeypatch.setattr(release, 'atomic_write', interrupt)
    with pytest.raises(OSError, match='profile binding'):
        release.publish(**kwargs, transport=site)
    assert json.loads(profile_path.read_bytes())['artifact_hub_expected_version'] == 'ver_new'
    result = release.publish(**kwargs, transport=site)
    assert result['versionId'] == 'ver_new'
    assert sum('/commit?' in path for path, _, _ in site.calls) == 1
    journals = list(kwargs['work'].glob('wcxb-update-*.json'))
    assert len(journals) == 1
    assert json.loads(journals[0].read_bytes())['verified'] is True


def test_package_only_cli_never_publishes_or_loads_credentials(report, monkeypatch, capsys):
    paths, _profile, checks = report
    monkeypatch.setattr(release, 'publish', lambda **_kwargs: pytest.fail('package-only published'))
    release.main(['--package-only', '--authority-root', str(paths['authority_root']),
                  '--source', str(paths['source']), '--work', str(paths['work'])])
    result = json.loads(capsys.readouterr().out)
    assert result['files'] == 2
    assert (paths['work'] / 'wcxb-current.zip').is_file()
    assert (paths['work'] / 'wcxb-package.json').is_file()
    assert [item[0] for item in checks] == ['semantic', 'links']
