"""Bound-target publishing and resumable package integrity at the public boundary."""

import pytest

from harness.web_extract import publication
from harness.web_extract.pipeline import digest, read_json, save_json


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
            "profile_sha256": "profile",
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


@pytest.mark.parametrize("approved", [False, True])
def test_different_report_profile_needs_explicit_successor(tmp_path, monkeypatch, approved):
    source, target, work, visual = setup_report(tmp_path)
    next_profile = tmp_path / "next-profile.json"
    save_json(next_profile, {"report": "next"})
    next_hash = digest(next_profile.read_bytes())
    save_json(source / "report-manifest.json", {"profile_sha256": next_hash})
    bound = read_json(target)
    if approved:
        bound["approved_profile_successor"] = {
            "from_sha256": "profile",
            "to_sha256": next_hash,
            "profile_path": str(next_profile),
        }
        save_json(target, bound)
    called = []
    monkeypatch.setattr(
        publication,
        "package",
        lambda *a, **k: (
            called.append("package")
            or {"archive_sha256": "archive", "report_manifest_sha256": "manifest"}
        ),
    )

    def update(staged, **kwargs):
        staged.bind("ver_after")
        return "ver_after"

    monkeypatch.setattr(publication, "publish_staged_update", update)
    if approved:
        assert publication.publish(source, target, work, visual)["versionId"] == "ver_after"
        assert called == ["package"]
        assert read_json(target)["profile"] == {"path": str(next_profile), "sha256": next_hash}
        assert "approved_profile_successor" not in read_json(target)
    else:
        with pytest.raises(ValueError, match="profile differs"):
            publication.publish(source, target, work, visual)
        assert not called
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


@pytest.mark.parametrize("damage", ["stale-file", "extra-file", "missing-manifest"])
def test_self_consistent_zip_hash_cannot_replace_checked_contents(tmp_path, monkeypatch, damage):
    import zipfile

    source, target, work, visual = setup_report(tmp_path)
    (source / "index.html").write_text("checked", encoding="utf-8")
    manifest = read_json(source / "report-manifest.json")
    manifest["files"] = {
        "index.html": digest(b"checked"),
        "browser-eval-version.json": digest((source / "browser-eval-version.json").read_bytes()),
    }
    save_json(source / "report-manifest.json", manifest)
    work.mkdir()
    archive = work / "report.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in source.iterdir():
            if damage == "missing-manifest" and path.name == "report-manifest.json":
                continue
            bundle.writestr(
                path.name,
                b"stale"
                if damage == "stale-file" and path.name == "index.html"
                else path.read_bytes(),
            )
        if damage == "extra-file":
            bundle.writestr("private.txt", "private")
    save_json(
        work / "report.receipt.json",
        {
            "archive_sha256": digest(archive.read_bytes()),
            "report_manifest_sha256": digest((source / "report-manifest.json").read_bytes()),
        },
    )
    monkeypatch.setattr(publication, "check", lambda *a, **k: None)
    monkeypatch.setattr(
        publication, "publish_staged_update", lambda *a, **k: pytest.fail("dispatched")
    )
    with pytest.raises(ValueError, match="ZIP inventory"):
        publication.publish(source, target, work, visual)
