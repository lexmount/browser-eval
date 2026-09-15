from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import scripts.package_webarena_replay_artifact_site as packager
import scripts.publish_webarena_replay_artifact_site as publisher


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_response_json_keeps_http_failure_details() -> None:
    assert publisher.response_json(200, b'{"ok":true}', operation="upload") == {"ok": True}
    for status, payload, expected in (
        (413, b'{"error":"too large"}', "HTTP 413"),
        (502, b"", "empty response"),
        (200, b"", "without valid JSON"),
    ):
        try:
            publisher.response_json(status, payload, operation="upload")
        except RuntimeError as error:
            assert expected in str(error)
        else:
            raise AssertionError("invalid response must fail")


def test_public_share_must_be_same_origin_and_token_route() -> None:
    base = "https://artifact-hub.app.lexmount.com"
    assert publisher.public_share_path(base, base + "/v/token") == "/v/token"
    for url in (
        "https://other.example/v/token",
        base + "/s/slug",
        base + "/v/token?owner=true",
    ):
        try:
            publisher.public_share_path(base, url)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe public share URL must be rejected")


def test_preview_marker_accepts_each_localized_home_title() -> None:
    assert publisher.preview_has_report_marker(b"<title>Browser Trajectory Replay</title>")
    assert publisher.preview_has_report_marker(
        "<title>浏览器轨迹重放</title>".encode()
    )
    assert not publisher.preview_has_report_marker(b"<title>Unrelated site</title>")


def test_bind_publication_updates_profile_and_attestation(monkeypatch, tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"publication": {"base_url": "https://app.example"}}),
        encoding="utf-8",
    )
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"profile_sha256": "old"}), encoding="utf-8")
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "binding-pending.json")

    publisher.bind_publication(
        json.loads(profile.read_text()),
        slug="abc",
        share_url="https://app.example/v/token",
        version="ver_new",
    )

    publication = json.loads(profile.read_text())["publication"]
    assert publication == {
        "base_url": "https://app.example",
        "site_slug": "abc",
        "share_url": "https://app.example/v/token",
        "expected_version": "ver_new",
        "state": "verified",
    }
    assert json.loads(attestation.read_text())["profile_sha256"] == _sha256(profile)


def test_binding_journal_recovers_interrupted_two_file_update(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"publication": {"base_url": "https://app.example"}}),
        encoding="utf-8",
    )
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"profile_sha256": "old"}), encoding="utf-8")
    journal = tmp_path / "binding-pending.json"
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", journal)
    original_atomic_write = publisher.atomic_write
    interrupted = False

    def interrupt_attestation(path: Path, payload: bytes) -> None:
        nonlocal interrupted
        if path == attestation and not interrupted:
            interrupted = True
            raise OSError("simulated interruption")
        original_atomic_write(path, payload)

    monkeypatch.setattr(publisher, "atomic_write", interrupt_attestation)
    with pytest.raises(OSError, match="simulated interruption"):
        publisher.bind_publication(
            json.loads(profile.read_text()),
            slug="abc",
            share_url="https://app.example/v/token",
            version="ver_new",
        )
    assert journal.is_file()

    monkeypatch.setattr(publisher, "atomic_write", original_atomic_write)
    publisher.recover_publication_binding()

    assert not journal.exists()
    assert json.loads(profile.read_text())["publication"]["expected_version"] == "ver_new"
    assert json.loads(attestation.read_text())["profile_sha256"] == _sha256(profile)


def test_pending_created_site_is_bound_to_release_package(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    base = "https://app.example"
    publisher.save_receipt(
        {
            "base_url": base,
            "slug": "site-1",
            "archive_sha256": publisher.sha256(archive),
            "verified": False,
        }
    )

    assert publisher.pending_created_site(base) == "site-1"
    with pytest.raises(ValueError, match="differs"):
        publisher.pending_created_site("https://other.example")
    archive.write_bytes(b"different")
    with pytest.raises(ValueError, match="differs"):
        publisher.pending_created_site(base)


@pytest.mark.parametrize("retry_create", [True, False])
def test_share_failure_resumes_same_site_without_second_creation(
    monkeypatch, tmp_path: Path, retry_create: bool
) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    monkeypatch.setattr(publisher, "validate_package", lambda: {})
    monkeypatch.setattr(
        publisher,
        "publication_target",
        lambda **_kwargs: (
            {"publication": {"title": "Replay"}},
            "https://app.example",
            "",
            "",
        ),
    )
    monkeypatch.setattr(publisher, "multipart_archive", lambda _title: (b"zip", "zip"))
    monkeypatch.setattr(publisher, "verify_export", lambda *_args: None)
    calls = []
    fail_share_once = True

    def fake_request(_base, path, *_args, **_kwargs):
        nonlocal fail_share_once
        calls.append(path)
        if path == "/api/sites":
            return 200, b'{"slug":"site-1"}', {}
        if path.endswith("/shares"):
            if fail_share_once:
                fail_share_once = False
                return 503, b'{"error":"temporary"}', {}
            return 200, b'{"url":"https://app.example/v/token"}', {}
        if path == "/api/sites/site-1":
            return 200, b'{"site":{"currentVersionId":"ver_1"}}', {}
        if path == "/v/token" or path == "/api/preview/site-1":
            return 200, "<title>浏览器轨迹重放</title>".encode(), {}
        raise AssertionError(path)

    monkeypatch.setattr(publisher, "request", fake_request)
    monkeypatch.setattr(publisher, "bind_publication", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="public share creation"):
        publisher.main(create=True)

    publisher.main(create=retry_create)

    assert calls.count("/api/sites") == 1
    assert calls.count("/api/sites/site-1/shares") == 2


def test_package_binds_report_evidence_and_release_gate(monkeypatch, tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    evidence = tmp_path / "effective-results.json"
    evidence.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    dataset = tmp_path / "dataset.json"
    review = tmp_path / "review.json"
    for path in (profile, dataset, review):
        path.write_text("{}", encoding="utf-8")
    output = tmp_path / "report.zip"
    attestation = tmp_path / "attestation.json"
    monkeypatch.setattr(packager, "PROFILE", profile)
    monkeypatch.setattr(packager, "DATASET", dataset)
    monkeypatch.setattr(packager, "REVIEW", review)
    monkeypatch.setattr(packager, "ATTESTATION", attestation)
    monkeypatch.setattr(
        packager,
        "validate_release",
        lambda _report, _receipt: {"html_pages": 1, "checked_links": 0},
    )

    result = packager.package(report, evidence, output, tmp_path / "visual.json")

    assert result["archive_sha256"] == _sha256(output)
    assert result["evidence_sha256"] == _sha256(evidence)
    assert result["file_count"] == 1
