from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
