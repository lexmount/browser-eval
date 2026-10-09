from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from harness.trajectory_replay import webarena_report_package as packager
from harness.trajectory_replay import webarena_report_publish as publisher


@pytest.fixture(autouse=True)
def private_publication_files(monkeypatch, tmp_path):
    for key in ("ATTESTATION", "PROFILE", "REVIEW"):
        path = tmp_path / (key.lower() + "-default.json")
        path.write_text("{}")
        monkeypatch.setattr(publisher, key, path)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "binding-pending.json")


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


def test_staged_file_put_has_longer_socket_timeout() -> None:
    assert publisher.request_timeout("PUT") == 1_200
    assert publisher.request_timeout("POST") == 240
    assert publisher.request_timeout(None) == 240


def test_public_share_must_be_same_origin_and_token_route() -> None:
    base = "https://artifact-hub.app.lexmount.com"
    assert publisher.public_share_context(base, base + "/v/token")[0] == "/v/token"
    assert publisher.public_share_context(base, base + "/v/token") == (
        "/v/token",
        "token",
    )
    for url in (
        "https://other.example/v/token",
        base + "/s/slug",
        base + "/v/",
        base + "/v/token/extra",
        base + "/v/token%2Fextra",
        base + "/v/token?owner=true",
    ):
        try:
            publisher.public_share_context(base, url)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe public share URL must be rejected")


def test_preview_marker_accepts_each_localized_home_title() -> None:
    assert publisher.preview_has_report_marker(b"<title>Browser Trajectory Replay</title>")
    assert publisher.preview_has_report_marker("<title>浏览器轨迹重放</title>".encode())
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


def test_binding_journal_recovers_interrupted_two_file_update(monkeypatch, tmp_path: Path) -> None:
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


def test_receipt_replace_interruption_preserves_previous_receipt(
    monkeypatch, tmp_path: Path
) -> None:
    receipt = tmp_path / "receipt.json"
    original = b'{"slug":"site-1"}\n'
    receipt.write_bytes(original)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)

    def interrupt_replace(_source, _destination) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(publisher.os, "replace", interrupt_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        publisher.save_receipt({"slug": "site-2"})

    assert receipt.read_bytes() == original


@pytest.mark.parametrize(
    ("path", "kind"),
    (("/api/sites", "create"), ("/api/sites/site-1/versions", "version")),
)
def test_upload_recovers_lost_response_with_same_persisted_operation(
    monkeypatch, tmp_path: Path, path: str, kind: str
) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    operation_journal = tmp_path / "operation-pending.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "OPERATION_JOURNAL", operation_journal)
    requests = []

    def fake_request(_base, request_path, *_args, **kwargs):
        requests.append((request_path, kwargs))
        if request_path.startswith("/api/operations/"):
            return (
                200,
                json.dumps(
                    {
                        "status": "completed",
                        "httpStatus": 200,
                        "result": {"slug": "site-1", "versionId": "ver_2"},
                    }
                ).encode(),
                {},
            )
        raise OSError("response was lost after commit")

    monkeypatch.setattr(publisher, "request", fake_request)
    identity = publisher.operation_identity(
        base="https://app.example",
        title="Replay",
        kind=kind,
        slug="site-1" if kind == "version" else "",
        expected_version="ver_1" if kind == "version" else "",
    )
    with pytest.raises(OSError, match="response was lost"):
        publisher.idempotent_upload(
            "https://app.example",
            path,
            b"payload",
            "multipart/form-data; boundary=fixed",
            identity,
            allow_create=True,
        )
    pending = json.loads(operation_journal.read_text())
    operation_key = pending["key"]
    assert requests[0][1]["extra_headers"] == {"Idempotency-Key": operation_key}

    status, raw = publisher.idempotent_upload(
        "https://app.example",
        path,
        b"payload",
        "multipart/form-data; boundary=fixed",
        identity,
        allow_create=False,
    )

    assert status == 200
    assert json.loads(raw)["slug"] == "site-1"
    assert [item[0] for item in requests].count(path) == 1
    assert requests[-1][0] == f"/api/operations/{operation_key}"


def test_pending_operation_rejects_changed_release(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"first")
    operation_journal = tmp_path / "operation-pending.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "OPERATION_JOURNAL", operation_journal)
    original = publisher.operation_identity(
        base="https://app.example", title="Replay", kind="create"
    )
    publisher.pending_operation(original, allow_create=True)
    archive.write_bytes(b"changed")
    changed = publisher.operation_identity(
        base="https://app.example", title="Replay", kind="create"
    )

    with pytest.raises(ValueError, match="differs from this release"):
        publisher.pending_operation(changed, allow_create=True)


def test_running_operation_does_not_resubmit_upload(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    operation_journal = tmp_path / "operation-pending.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "OPERATION_JOURNAL", operation_journal)
    identity = publisher.operation_identity(
        base="https://app.example", title="Replay", kind="create"
    )
    publisher.pending_operation(identity, allow_create=True)
    requests = []

    def fake_request(_base, path, *_args, **_kwargs):
        requests.append(path)
        return 200, b'{"status":"running"}', {}

    monkeypatch.setattr(publisher, "request", fake_request)
    with pytest.raises(RuntimeError, match="still running"):
        publisher.idempotent_upload(
            "https://app.example",
            "/api/sites",
            b"payload",
            "multipart/form-data; boundary=fixed",
            identity,
            allow_create=False,
        )

    assert requests == [f"/api/operations/{json.loads(operation_journal.read_text())['key']}"]


def test_multipart_archive_is_stable_for_operation_replay(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    monkeypatch.setattr(publisher, "ARCHIVE", archive)

    assert publisher.multipart_archive("Replay") == publisher.multipart_archive("Replay")


def test_large_replay_bundle_uses_staged_file_upload(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("index.html", "<title>Replay</title>")
        bundle.writestr("assets/figure.png", b"figure-bytes")
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    calls = []

    def fake_request(_base, path, data=None, _content_type=None, **kwargs):
        calls.append((path, data, kwargs))
        if path == "/api/sites/site-1":
            payload = {"site": {"currentVersionId": "ver_old"}}
        elif path.startswith("/api/uploads?"):
            payload = {"versionId": "ver_upload"}
        elif path.endswith("/commit?expected_version=ver_old"):
            payload = {"slug": "site-1", "versionId": "ver_new"}
        else:
            payload = {"ok": True}
        return 200, json.dumps(payload).encode(), {}

    monkeypatch.setattr(publisher, "request", fake_request)
    identity = publisher.operation_identity(
        base="https://app.example",
        title="Replay",
        kind="version",
        slug="site-1",
        expected_version="ver_old",
    )

    result = json.loads(
        publisher.staged_archive_upload(
            "https://app.example",
            title="Replay",
            slug="site-1",
            expected_version="ver_old",
            identity=identity,
        )
    )

    assert result["versionId"] == "ver_new"
    puts = [call for call in calls if call[2].get("method") == "PUT"]
    assert [call[0] for call in puts] == [
        "/api/uploads/ver_upload/files/index.html",
        "/api/uploads/ver_upload/files/assets/figure.png",
    ]
    assert puts[1][1] == b"figure-bytes"
    start = next(call for call in calls if call[0].startswith("/api/uploads?"))
    commit = next(call for call in calls if "/commit" in call[0])
    assert json.loads(start[1]) == {"slug": "site-1"}
    assert start[2]["extra_headers"]["Idempotency-Key"].startswith("trajectory-staged-")
    assert commit[2]["extra_headers"]["Idempotency-Key"].startswith("trajectory-staged-")


def test_pending_receipt_rejects_mismatched_operation_before_remote_write(
    monkeypatch, tmp_path: Path
) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    receipt = tmp_path / "receipt.json"
    operation_journal = tmp_path / "operation-pending.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    monkeypatch.setattr(publisher, "OPERATION_JOURNAL", operation_journal)
    publisher.save_receipt(
        {
            "base_url": "https://app.example",
            "slug": "site-1",
            "archive_sha256": publisher.sha256(archive),
            "verified": False,
        }
    )
    publisher.pending_operation(
        publisher.operation_identity(
            base="https://app.example", title="Different title", kind="create"
        ),
        allow_create=True,
    )
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
    monkeypatch.setattr(publisher, "multipart_archive", lambda _title, **_kwargs: (b"zip", "zip"))

    def unexpected_remote_call(*_args, **_kwargs):
        raise AssertionError("mismatched operation must fail before any remote call")

    monkeypatch.setattr(publisher, "request", unexpected_remote_call)
    monkeypatch.setattr(publisher, "verify_export", unexpected_remote_call)

    with pytest.raises(ValueError, match="differs from this release"):
        publisher.main(create=False)


@pytest.mark.parametrize("retry_create", [True, False])
def test_share_failure_resumes_same_site_without_second_creation(
    monkeypatch, tmp_path: Path, retry_create: bool
) -> None:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"package")
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    monkeypatch.setattr(publisher, "OPERATION_JOURNAL", tmp_path / "operation-pending.json")
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
    monkeypatch.setattr(publisher, "multipart_archive", lambda _title, **_kwargs: (b"zip", "zip"))
    monkeypatch.setattr(publisher, "verify_export", lambda *_args, **_kwargs: None)
    calls = []
    fail_share_once = True

    def fake_request(_base, path, *_args, **kwargs):
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
        if path == "/v/token":
            assert kwargs["authenticated"] is False
            assert "extra_headers" not in kwargs
            return 200, "<title>浏览器轨迹重放</title>".encode(), {}
        if path == "/api/preview/site-1":
            assert kwargs["authenticated"] is False
            assert kwargs["extra_headers"] == {"X-Artifact-Share": "token"}
            return 200, "<title>浏览器轨迹重放</title>".encode(), {}
        raise AssertionError(path)

    monkeypatch.setattr(publisher, "request", fake_request)

    def bind_after_operation_is_settled(*_args, **_kwargs):
        assert not publisher.OPERATION_JOURNAL.exists()

    monkeypatch.setattr(publisher, "bind_publication", bind_after_operation_is_settled)
    with pytest.raises(RuntimeError, match="public share creation"):
        publisher.main(create=True)

    publisher.main(create=retry_create)

    assert calls.count("/api/sites") == 1
    assert calls.count("/api/sites/site-1/shares") == 2
    assert not publisher.OPERATION_JOURNAL.exists()


def test_package_binds_report_evidence_and_release_gate(monkeypatch, tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    evidence = tmp_path / "effective-results.json"
    evidence.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    dataset = tmp_path / "dataset.json"
    review = tmp_path / "review.json"
    dataset.write_text("{}", encoding="utf-8")
    profile.write_text(
        json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}),
        encoding="utf-8",
    )
    review.write_text("{}", encoding="utf-8")
    (report / "report-manifest.json").write_text(json.dumps({
        "dataset_sha256": _sha256(dataset), "results_sha256": _sha256(evidence),
    }), encoding="utf-8")
    output = tmp_path / "report.zip"
    attestation = tmp_path / "attestation.json"
    monkeypatch.setattr(packager, "PROFILE", profile)
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
    assert result["file_count"] == 2
    profile.write_text(json.dumps({
        "dataset": {"path": str(dataset), "sha256": _sha256(dataset)},
        "scoring": {"require_all_reference_validity": True},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="registered methods and the declared task set"):
        packager.package(report, evidence, output, tmp_path / "visual.json")


@pytest.mark.parametrize("old_schema", [
    "lex_webarena_replay_visual_qa/1",
    "lex_webarena_replay_visual_qa/2",
    "lex_webarena_replay_visual_qa/3",
])
def test_package_reruns_private_preview_gate_for_old_receipt(
    monkeypatch, tmp_path: Path, old_schema: str
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("<html></html>", encoding="utf-8")
    receipt = tmp_path / "visual.json"
    receipt.write_text(json.dumps({
        "schema": old_schema,
        "report_tree_sha256": packager.tree_sha256(report),
        "states_checked": 48,
        "failures": [],
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr(packager, "check_report", lambda _report: {"html_pages": 1})
    monkeypatch.setattr(packager, "check_visual", lambda *_args: calls.append("private preview"))

    packager.validate_release(report, receipt)

    assert calls == ["private preview"]


def test_package_rejects_diagnostic_appendix_and_mixed_dataset(
    monkeypatch, tmp_path: Path
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    dataset = tmp_path / "corrected.json"
    dataset.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}))
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(packager, "PROFILE", profile)
    monkeypatch.setattr(packager, "REVIEW", review)
    monkeypatch.setattr(packager, "ATTESTATION", tmp_path / "attestation.json")
    monkeypatch.setattr(packager, "validate_release", lambda *_args: {})
    manifest = {"dataset_sha256": _sha256(dataset), "results_sha256": _sha256(evidence),
                "diagnostic_closure_sha256": "a" * 64}
    (report / "report-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="diagnostic-only"):
        packager.package(report, evidence, tmp_path / "report.zip", tmp_path / "visual.json")
    manifest.pop("diagnostic_closure_sha256")
    manifest["dataset_sha256"] = "0" * 64
    (report / "report-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="different result or dataset"):
        packager.package(report, evidence, tmp_path / "report.zip", tmp_path / "visual.json")


def test_publication_rejects_diagnostic_appendix_and_stale_corrected_dataset(
    monkeypatch, tmp_path: Path
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"package")
    dataset = tmp_path / "corrected.json"
    dataset.write_text("{}", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}))
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "REVIEW", review)
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    (report / "report-manifest.json").write_text(json.dumps({
        "dataset_sha256": _sha256(dataset), "results_sha256": _sha256(evidence),
    }))
    attestation = {
        "schema": "lex_webarena_replay_publication_attestation/1",
        "archive_sha256": _sha256(archive),
        "report": str(report),
        "report_tree_sha256": publisher.tree_sha256(report),
        "profile_sha256": _sha256(profile),
        "dataset_sha256": _sha256(dataset),
        "review_sha256": _sha256(review),
        "evidence": str(evidence),
        "evidence_sha256": _sha256(evidence),
    }
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation_path)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "pending.json")
    assert publisher.validate_package()["dataset_sha256"] == _sha256(dataset)
    profile.write_text(json.dumps({
        "dataset": {"path": str(dataset), "sha256": _sha256(dataset)},
        "scoring": {"require_all_reference_validity": True},
    }), encoding="utf-8")
    attestation["profile_sha256"] = _sha256(profile)
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    with pytest.raises(ValueError, match="registered methods and the declared task set"):
        publisher.validate_package()
    profile.write_text(json.dumps({"dataset": {"path": str(dataset), "sha256": _sha256(dataset)}}))
    attestation["profile_sha256"] = _sha256(profile)
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    attestation["diagnostic_closure_sha256"] = "a" * 64
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    with pytest.raises(ValueError, match="no_diagnostic_closure"):
        publisher.validate_package()
    attestation.pop("diagnostic_closure_sha256")
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    dataset.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="dataset"):
        publisher.validate_package()
