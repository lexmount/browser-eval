"""Release archives must contain exactly the validated bytes or preserve the prior release."""
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from harness.reporting.publication import package_archive, snapshot_publication


@pytest.mark.skipif(os.name == 'nt', reason='Windows lacks Unix change-time semantics')
def test_packet_guards_do_not_reread_unchanged_report_payloads(tmp_path, monkeypatch):
    report, authority, output, _ = fixture(tmp_path)
    with snapshot_publication(output, inputs=[report, authority]) as snapshot:
        with monkeypatch.context() as guards:
            guards.setattr(Path, 'read_bytes', lambda _: pytest.fail('unchanged payload reread'))
            for _ in range(100):
                snapshot.check()
        snapshot.verify()


@pytest.mark.parametrize('damage', ['page', 'profile', 'source-archive', 'frozen-archive',
                                  'new-file', 'deleted-file', 'replacement', 'symlink'])
def test_packet_guards_detect_input_edits_even_with_restored_mtime(tmp_path, damage):
    report, authority, output, _ = fixture(tmp_path)
    with snapshot_publication(output, inputs=[report, authority]) as snapshot:
        target = {'page': report / 'index.html', 'profile': authority,
                  'source-archive': output, 'frozen-archive': snapshot.archive}.get(damage)
        if target is not None:
            before = target.stat()
            target.write_bytes(target.read_bytes()[::-1])
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif damage == 'new-file':
            (report / 'extra.txt').write_text('unexpected')
        elif damage == 'deleted-file':
            (report / 'index.html').unlink()
        elif damage == 'replacement':
            page = report / 'index.html'
            page.unlink()
            page.write_text('different page')
        else:
            (report / 'linked').symlink_to(authority)
        with pytest.raises(ValueError):
            snapshot.check()


def fixture(tmp_path):
    report = tmp_path / 'report'
    report.mkdir()
    (report / 'index.html').write_text('validated page')
    authority = tmp_path / 'profile.json'
    authority.write_text('{}')
    output = tmp_path / 'release.zip'
    receipt = tmp_path / 'release.json'
    output.write_bytes(b'previous archive')
    receipt.write_bytes(b'previous receipt')
    return report, authority, output, receipt


def run(report, authority, output, receipt, validate=lambda: None):
    return package_archive(report, output, receipt, inputs=[authority], validate=validate,
                           attest=lambda archive: {'archive_bytes': archive.stat().st_size})


def test_package_installs_complete_archive_and_receipt(tmp_path):
    report, authority, output, receipt = fixture(tmp_path)
    result = run(report, authority, output, receipt)
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ['index.html']
        assert archive.read('index.html') == b'validated page'
    assert json.loads(receipt.read_text()) == result
    assert result['archive_bytes'] == output.stat().st_size


@pytest.mark.parametrize('damage', ['page', 'profile', 'new-file', 'symlink', 'gate'])
def test_changed_or_rejected_inputs_preserve_prior_release(tmp_path, damage):
    report, authority, output, receipt = fixture(tmp_path)
    def validate():
        if damage == 'page':
            (report / 'index.html').write_text('not validated')
        elif damage == 'profile':
            authority.write_text('{"changed":true}')
        elif damage == 'new-file':
            (report / 'extra.html').write_text('not validated')
        elif damage == 'symlink':
            (report / 'secret').symlink_to(authority)
        else:
            raise ValueError('release gate rejected')
    with pytest.raises(ValueError):
        run(report, authority, output, receipt, validate)
    assert output.read_bytes() == b'previous archive'
    assert receipt.read_bytes() == b'previous receipt'


@pytest.mark.parametrize('destination', ['archive', 'receipt'])
def test_install_failure_restores_both_prior_files(tmp_path, monkeypatch, destination):
    report, authority, output, receipt = fixture(tmp_path)
    original = Path.replace
    failed = False
    def replace(source, target):
        nonlocal failed
        selected = output if destination == 'archive' else receipt
        if Path(target) == selected and not failed:
            failed = True
            raise OSError('simulated installation failure')
        return original(source, target)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(OSError, match='installation failure'):
        run(report, authority, output, receipt)
    assert output.read_bytes() == b'previous archive'
    assert receipt.read_bytes() == b'previous receipt'


@pytest.mark.parametrize(
    'target', ['nested', 'same', 'linked', 'dot-file', 'backslash', 'control', 'oversize']
)
def test_unsafe_package_is_rejected_before_replacing_release(tmp_path, target):
    report, authority, output, receipt = fixture(tmp_path)
    options = {}
    if target == 'nested':
        output = report / 'release.zip'
    elif target == 'same':
        receipt = output
    elif target == 'linked':
        (report / 'assets').symlink_to(tmp_path, target_is_directory=True)
    elif target == 'dot-file':
        (report / '.env').write_text('private')
    elif target == 'backslash':
        (report / 'folder\\private.html').write_text('unsafe cross-platform path')
    elif target == 'control':
        (report / 'private\n.html').write_text('unsafe control character')
    else:
        options['max_bytes'] = 1
    with pytest.raises(ValueError):
        package_archive(report, output, receipt, inputs=[authority], validate=lambda: None,
                        attest=lambda _: {}, **options)
    if target != 'nested':
        assert output.read_bytes() == b'previous archive'


@pytest.mark.parametrize('track', ['browser-use', 'replay'])
@pytest.mark.parametrize('damage', ['none', 'report', 'profile', 'write'])
def test_track_package_uses_frozen_gates_and_preserves_previous_release(
    tmp_path, monkeypatch, track, damage
):
    from harness.browser_use import report_package as autonomous
    from harness.trajectory_replay import webarena_report_package as replay

    report, profile, output, receipt = fixture(tmp_path)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    for name in ('manifest.json', 'run-config.json', 'attempts.jsonl', 'result-review.json'):
        (evidence / name).write_text('{}')
    authority = tmp_path / 'authority'
    current = authority / "evaluation/browser-use/current-report.json"
    current.parent.mkdir(parents=True)
    selection_hash = hashlib.sha256(b"{}").hexdigest()
    current.write_text(json.dumps({
        "task_selection": {
            "path": str(current.parent / "webarena-verified-pilot.json"),
            "sha256": selection_hash,
        },
        "selection_sha256": selection_hash,
    }))
    (current.parent / "webarena-verified-pilot.json").write_text("{}")
    (authority / 'research').mkdir()
    dataset = tmp_path / 'dataset.json'
    dataset.write_text('{}')
    from harness.reporting.visual import file_sha256
    profile.write_text(json.dumps({
        'dataset': {'path': str(dataset), 'sha256': file_sha256(dataset)}
    }))
    review = tmp_path / 'review.json'
    review.write_text('{}')
    results = evidence / 'manifest.json'
    (report / 'report-manifest.json').write_text(json.dumps({
        'dataset_sha256': file_sha256(dataset), 'results_sha256': file_sha256(results)
    }))
    if track == 'browser-use':
        (report / '.generated-report').write_text('browser-use-report/2\n')
    def validate(*args, **kwargs):
        if damage == 'report':
            (report / 'index.html').write_text('changed after gate')
        elif damage == 'profile':
            (current if track == 'browser-use' else profile).write_text('{} changed')
        return {}
    monkeypatch.setattr(
        autonomous if track == 'browser-use' else replay, 'validate_release', validate
    )
    original = zipfile.ZipFile.write
    def write(archive, *args, **kwargs):
        original(archive, *args, **kwargs)
        raise OSError('simulated archive write failure')
    if damage == 'write':
        monkeypatch.setattr(zipfile.ZipFile, 'write', write)
    def package():
        if track == 'browser-use':
            return autonomous.package(report, [evidence], output, tmp_path / 'visual.json',
                                      authority_root=authority, attestation_path=receipt)
        return replay.package(report, results, output, tmp_path / 'visual.json',
                              profile_path=profile, review_path=review, attestation_path=receipt)
    if damage != 'none':
        with pytest.raises((ValueError, OSError)):
            package()
        assert output.read_bytes() == b'previous archive'
        assert receipt.read_bytes() == b'previous receipt'
    else:
        result = package()
        assert result['archive_sha256'] == file_sha256(output)
        with zipfile.ZipFile(output) as archive:
            assert '.generated-report' not in archive.namelist()
        assert result['report_tree_sha256'] == (
            autonomous.tree_sha256(report) if track == 'browser-use' else replay.tree_sha256(report)
        )


def test_failed_rollback_retains_recoverable_original(tmp_path, monkeypatch):
    report, authority, output, receipt = fixture(tmp_path)
    original = Path.replace
    def replace(source, target):
        if Path(target) == receipt or source.name == 'previous':
            raise OSError('simulated inaccessible destination')
        return original(source, target)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(RuntimeError, match='original retained at'):
        run(report, authority, output, receipt)
    saved = list(tmp_path.glob('.release.zip-recovery-*'))
    assert len(saved) == 1 and saved[0].read_bytes() == b'previous archive'
    assert receipt.read_bytes() == b'previous receipt'


def test_stable_parent_alias_is_supported_but_retargeting_is_rejected(tmp_path):
    report, authority, output, receipt = fixture(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    run(report, authority, alias / output.name, alias / receipt.name)
    before = (output.read_bytes(), receipt.read_bytes())
    other = tmp_path / 'other'
    other.mkdir()
    def retarget():
        alias.unlink()
        alias.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='parent changed'):
        run(report, authority, alias / output.name, alias / receipt.name, retarget)
    assert (output.read_bytes(), receipt.read_bytes()) == before
    assert not list(other.iterdir())


@pytest.mark.parametrize('status', [404, 403, 410, 500])
def test_operation_recovery_handles_real_http_errors_without_replacing_key(tmp_path, status):
    from urllib.error import HTTPError

    from harness.reporting.publication import PublicationOperation
    operation = PublicationOperation(tmp_path / 'pending.json', 'test-')
    identity = {'slug': 'existing', 'archive_sha256': 'bound'}
    pending, _ = operation.pending(identity, allow_create=True)
    before = operation.journal.read_bytes()
    error = HTTPError('https://example.test/api/operations/key', status, 'status', {}, None)
    def request(*args, **kwargs):
        raise error
    if status == 404:
        assert operation.completed(pending['key'], request=request, decode=lambda *_: None) is None
    else:
        with pytest.raises(HTTPError) as caught:
            operation.completed(pending['key'], request=request, decode=lambda *_: None)
        assert caught.value.code == status
    assert operation.journal.read_bytes() == before
