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


@pytest.mark.parametrize(
    "damage", [None, "coordinated-public-audit", "original", "content", "profile"]
)
def test_publication_uses_frozen_audit_even_when_public_receipts_are_rewritten(
    tmp_path, monkeypatch, damage
):
    from harness.web_extract.report import publication_host_names
    from harness.web_fetch.publication_privacy import project_evidence_view

    source, target, work, visual = setup_report(tmp_path)
    frozen = tmp_path / "frozen"
    audit = frozen / "audit.json"
    save_json(audit, {"result_count": 1, "excluded_fields": {}})
    audit_hash = digest(audit.read_bytes())
    content = frozen / "content.json"
    save_json(content, {"audit": {"path": "audit.json", "sha256": audit_hash}})
    profile = frozen / "profile.json"
    save_json(
        profile,
        {
            "research_report": {
                "content": {"path": "content.json", "sha256": digest(content.read_bytes())}
            }
        },
    )
    profile_hash = digest(profile.read_bytes())
    path = "public-evidence/audit.json"
    raw, receipt = project_evidence_view(
        path,
        audit.read_bytes(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    published = source / path
    published.parent.mkdir()
    published.write_bytes(raw)
    manifest = {
        "profile_sha256": profile_hash,
        "completion_audit": {"path": path, "source_sha256": audit_hash, "sha256": digest(raw)},
        "public_evidence_views": [receipt],
        "completion_reference_exclusions": {},
    }
    if damage == "coordinated-public-audit":
        save_json(published, {"result_count": 1, "excluded_fields": {"page": ["sku"]}})
        manifest["completion_audit"]["sha256"] = digest(published.read_bytes())
        receipt["published_sha256"] = digest(published.read_bytes())
        manifest["completion_reference_exclusions"] = {"page": ["sku"]}
        # Source hashes still claim the untouched, frozen original. Rebuilding
        # the bundle and tables cannot legitimize the forged public audit.
    elif damage:
        changed = {"original": audit, "content": content, "profile": profile}[damage]
        save_json(changed, {"substituted": True})
    save_json(source / "report-manifest.json", manifest)
    bound = read_json(target)
    bound.update(
        {"profile_sha256": profile_hash, "profile": {"path": str(profile), "sha256": profile_hash}}
    )
    save_json(target, bound)
    calls = []
    monkeypatch.setattr(
        publication,
        "package",
        lambda *a, **k: (
            calls.append("package")
            or {"archive_sha256": "archive", "report_manifest_sha256": "manifest"}
        ),
    )

    def update(staged, **kwargs):
        calls.append("network")
        staged.guard()
        staged.bind("ver_after")
        return "ver_after"

    monkeypatch.setattr(publication, "publish_staged_update", update)
    if damage:
        with pytest.raises(ValueError, match="Completion audit"):
            publication.publish(source, target, work, visual)
        assert calls == []
        assert not work.exists()
    else:
        assert publication.publish(source, target, work, visual)["versionId"] == "ver_after"
        assert calls == ["package", "network"]


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


@pytest.mark.parametrize("when", ["package", "upload", "bind"])
def test_approved_profile_cannot_change_during_publication(tmp_path, monkeypatch, when):
    source, target, work, visual = setup_report(tmp_path)
    selected = tmp_path / "successor.json"
    save_json(selected, {"cohort": "approved"})
    expected = digest(selected.read_bytes())
    save_json(source / "report-manifest.json", {"profile_sha256": expected})
    bound = read_json(target)
    bound["approved_profile_successor"] = {
        "from_sha256": "profile",
        "to_sha256": expected,
        "profile_path": str(selected),
    }
    save_json(target, bound)

    def change():
        save_json(selected, {"cohort": "changed"})

    def package(*args, **kwargs):
        if when == "package":
            change()
        return {"archive_sha256": "archive", "report_manifest_sha256": "manifest"}

    def update(staged, **kwargs):
        assert selected in staged.inputs
        change()
        if when == "bind":
            staged.bind("ver_after")
        else:
            staged.guard()
        pytest.fail("changed profile accepted")

    monkeypatch.setattr(publication, "package", package)
    monkeypatch.setattr(publication, "publish_staged_update", update)
    with pytest.raises(ValueError, match="successor profile changed"):
        publication.publish(source, target, work, visual)
    assert read_json(target)["expected_version"] == "ver_before"
    assert read_json(target)["approved_profile_successor"]["to_sha256"] == expected
