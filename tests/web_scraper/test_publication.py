"""Bound-target publishing and resumable package integrity at the public boundary."""

import pytest

from harness.web_scraper import publication
from harness.web_scraper.pipeline import digest, read_json, save_json


def setup_report(tmp_path):
    source = tmp_path / "report"
    source.mkdir()
    save_json(source / "report-manifest.json", {"profile_sha256": "profile"})
    save_json(source / "browser-eval-version.json", {"revision": "a" * 40})
    target = tmp_path / "target.json"
    save_json(
        target,
        {
            "host": "https://reports.example",
            "slug": "existing",
            "expected_version": "ver_before",
            "title": "Existing report",
            "visibility": "private",
            "profile": {"path": "profile.json"},
        },
    )
    return source, target, tmp_path / "upload", tmp_path / "visual.json"


def test_existing_target_is_preserved_and_rebound_only_after_success(tmp_path, monkeypatch):
    source, target, work, visual = setup_report(tmp_path)

    def package(report, archive, *, visual_receipt):
        archive.write_bytes(b"checked-package")
        return {
            "archive_sha256": digest(archive.read_bytes()),
            "report_manifest_sha256": digest((report / "report-manifest.json").read_bytes()),
        }

    def update(staged, *, transport):
        assert staged.slug == "existing"
        assert staged.expected_version == "ver_before"
        assert read_json(target)["expected_version"] == "ver_before"
        staged.guard()
        staged.bind("ver_after")
        return "ver_after"

    monkeypatch.setattr(publication, "package", package)
    monkeypatch.setattr(publication, "publish_staged_update", update)
    result = publication.publish(source, target, work, visual)
    bound = read_json(target)
    assert result["versionId"] == "ver_after"
    assert bound["visibility"] == "private"
    assert bound["slug"] == "existing"
    assert bound["profile"]["path"] == "profile.json"
    assert bound["renderer_source_revision"] == "a" * 40


def test_incomplete_target_never_packages_or_dispatches(tmp_path, monkeypatch):
    source, target, work, visual = setup_report(tmp_path)
    save_json(target, {"host": "https://reports.example", "slug": "existing"})
    monkeypatch.setattr(publication, "package", lambda *a, **k: pytest.fail("packaged"))
    with pytest.raises(ValueError, match="complete existing target"):
        publication.publish(source, target, work, visual)
    assert not work.exists()


def test_tampered_resume_package_never_dispatches(tmp_path, monkeypatch):
    source, target, work, visual = setup_report(tmp_path)
    work.mkdir()
    (work / "report.zip").write_bytes(b"tampered")
    save_json(
        work / "report.receipt.json",
        {
            "archive_sha256": digest(b"original"),
            "report_manifest_sha256": digest((source / "report-manifest.json").read_bytes()),
        },
    )
    monkeypatch.setattr(publication, "check", lambda *a, **k: None)
    monkeypatch.setattr(
        publication, "publish_staged_update", lambda *a, **k: pytest.fail("dispatched")
    )
    with pytest.raises(ValueError, match="Retained publication package differs"):
        publication.publish(source, target, work, visual)
