"""Publication consumes declared regular inputs and exposes only report-owned files."""
import json
import zipfile

import pytest

from harness.trajectory_replay.public_webarena import report
from harness.trajectory_replay.public_webarena.publication import (
    REPORT_STEM,
    build,
    export_public_report_bundle,
    stage_report,
)
from tests.public_webarena_fixture import write_report_fixture


def test_stage_consumes_only_manifest_and_summary(tmp_path):
    source, _ = write_report_fixture(tmp_path)
    (source / 'run/private-notes.txt').write_text('private diagnosis')
    (source / 'run/worker').mkdir()
    (source / 'run/worker/raw-response.json').write_text('private raw response')
    staged = tmp_path / 'staged'
    stage_report(source, staged)
    assert {path.name for path in (staged / 'run').iterdir()} == {'manifest.json', 'summary.json'}
    assert (source / 'run/private-notes.txt').read_text() == 'private diagnosis'


def test_failed_result_validation_leaves_no_staged_bundle(tmp_path):
    source, _ = write_report_fixture(tmp_path)
    (source / 'run/summary.json').write_text('{}')
    staged = tmp_path / 'staged'
    with pytest.raises(ValueError):
        stage_report(source, staged)
    assert not staged.exists()


@pytest.mark.parametrize('name', ['manifest.json', 'summary.json'])
def test_stage_rejects_linked_declared_inputs(tmp_path, name):
    source, _ = write_report_fixture(tmp_path)
    path = source / 'run' / name
    retained = tmp_path / name
    path.rename(retained)
    path.symlink_to(retained)
    staged = tmp_path / 'staged'
    with pytest.raises(ValueError):
        stage_report(source, staged)
    assert not staged.exists()


def test_stage_rejects_input_changes_during_rendering(tmp_path, monkeypatch):
    source, _ = write_report_fixture(tmp_path)
    original = report.write_latest_report

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (source / 'run/manifest.json').write_text('{}')
        return result

    monkeypatch.setattr(report, 'write_latest_report', changed)
    staged = tmp_path / 'staged'
    with pytest.raises(ValueError):
        stage_report(source, staged)
    assert not staged.exists()


@pytest.mark.parametrize('operation', ['package', 'export'])
def test_publication_excludes_undeclared_files_with_a_report_prefix(tmp_path, operation):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / 'staged'
    stage_report(source, staged)
    declared = {path.name for path in (staged / 'reports').iterdir()}
    name = REPORT_STEM + '-private-notes.txt'
    (staged / 'reports' / name).write_text('private investigation')
    if operation == 'package':
        archive = tmp_path / 'public.zip'
        build(staged, archive, tmp_path / 'receipt.json', authority=authority)
        with zipfile.ZipFile(archive) as bundle:
            assert name not in bundle.namelist()
            assert set(bundle.namelist()) == declared | {'index.html'}
    else:
        output = export_public_report_bundle(staged, tmp_path / 'public')
        assert not (output / 'reports' / name).exists()
        assert {path.name for path in (output / 'reports').iterdir()} == declared
        manifest = json.loads((output / 'run/manifest.json').read_text())
        assert set(manifest) == {'suite_sha256', 'artifact_provenance'}


@pytest.mark.parametrize('operation', ['package', 'export'])
def test_publication_rejects_a_linked_reports_directory(tmp_path, operation):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / 'staged'
    stage_report(source, staged)
    retained = tmp_path / 'retained-reports'
    (staged / 'reports').rename(retained)
    (staged / 'reports').symlink_to(retained, target_is_directory=True)
    with pytest.raises(ValueError):
        if operation == 'package':
            build(staged, tmp_path / 'public.zip', tmp_path / 'receipt.json', authority=authority)
        else:
            export_public_report_bundle(staged, tmp_path / 'public')


@pytest.mark.parametrize('role', ['archive', 'attestation'])
def test_package_rejects_linked_outputs_before_replacing_any_file(tmp_path, role):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / 'staged'
    stage_report(source, staged)
    target = tmp_path / 'retained-evidence.txt'
    target.write_text('retained evidence')
    archive, receipt = tmp_path / 'public.zip', tmp_path / 'receipt.json'
    selected = archive if role == 'archive' else receipt
    selected.symlink_to(target)
    with pytest.raises(ValueError):
        build(staged, archive, receipt, authority=authority)
    assert selected.is_symlink()
    assert target.read_text() == 'retained evidence'
    if role == 'attestation':
        assert not archive.exists()


@pytest.mark.parametrize('role', ['manifest', 'summary', 'authority', 'homepage'])
def test_package_outputs_cannot_replace_consumed_inputs(tmp_path, role):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / 'staged'
    stage_report(source, staged)
    targets = {'manifest': staged / 'run/manifest.json', 'summary': staged / 'run/summary.json',
               'authority': authority, 'homepage': staged / 'reports' / (REPORT_STEM + '.html')}
    target = targets[role]
    before = target.read_bytes()
    with pytest.raises(ValueError):
        build(staged, target, tmp_path / 'receipt.json', authority=authority)
    assert target.read_bytes() == before


def test_package_archive_and_receipt_must_be_different_files(tmp_path):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / 'staged'
    stage_report(source, staged)
    output = tmp_path / 'public.zip'
    with pytest.raises(ValueError):
        build(staged, output, output, authority=authority)
    assert not output.exists()


def test_stage_cannot_write_inside_source_evidence(tmp_path):
    source, _ = write_report_fixture(tmp_path)
    output = source / 'derived'
    with pytest.raises(ValueError):
        stage_report(source, output)
    assert not output.exists()
