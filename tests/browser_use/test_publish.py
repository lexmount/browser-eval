from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import scripts.package_browser_use_artifact_hub as packager
import scripts.publish_browser_use_artifact_hub as publisher


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_response_json_preserves_http_failure_context() -> None:
    assert publisher.response_json(200, b'{"ok": true}', operation="upload") == {"ok": True}
    for status, payload, expected in (
        (502, b"", "HTTP 502: empty response"),
        (413, b"too large", "HTTP 413: too large"),
        (200, b"", "HTTP 200 without a valid JSON response"),
    ):
        try:
            publisher.response_json(status, payload, operation="upload")
        except RuntimeError as error:
            assert expected in str(error)
        else:
            raise AssertionError("invalid response must fail with diagnostic context")


def test_public_preview_uses_stable_report_shell_not_editorial_headline() -> None:
    publisher.validate_public_preview(
        200,
        "<title>首页 · Browser Use 评测</title><h1>76 题审查合格比较集</h1>",
    )

    for status, body in ((503, publisher.PUBLIC_REPORT_MARKER), (200, "old page")):
        try:
            publisher.validate_public_preview(status, body)
        except RuntimeError as error:
            assert "report shell" in str(error)
        else:
            raise AssertionError("invalid public preview must fail")


def test_validate_package_requires_and_checks_every_evidence_root(
    monkeypatch, tmp_path: Path
) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("report", encoding="utf-8")
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"archive")
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    evidence_paths = []
    evidence_files = {}
    for model in ("sol", "astra"):
        evidence = tmp_path / model
        evidence.mkdir()
        files = {}
        for name in ("manifest.json", "attempts.jsonl"):
            path = evidence / name
            path.write_text(f"{model}:{name}", encoding="utf-8")
            files[name] = _sha256(path)
        evidence_paths.append(str(evidence))
        evidence_files[str(evidence)] = files
    attestation = tmp_path / "attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_publication_attestation/2",
                "archive_sha256": _sha256(archive),
                "report": str(report),
                "report_tree_sha256": publisher.tree_sha256(report),
                "profile_sha256": _sha256(profile),
                "evidence": evidence_paths,
                "evidence_files": evidence_files,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation)
    monkeypatch.setattr(publisher, "PROFILE", profile)

    publisher.validate_package()

    (tmp_path / "astra" / "attempts.jsonl").write_text("changed", encoding="utf-8")
    try:
        publisher.validate_package()
    except ValueError as error:
        assert "evidence" in str(error)
    else:
        raise AssertionError("changed secondary evidence must invalidate the package")


def test_bind_publication_advances_profile_and_attestation(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {"publication": {"base_url": "https://old.example", "expected_version": "ver_old"}}
        ),
        encoding="utf-8",
    )
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"profile_sha256": "stale"}), encoding="utf-8")
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "pending.json")

    publisher.bind_publication(
        json.loads(profile.read_text()),
        base="https://artifact-site.lexmount.cn",
        slug="new-site",
        share_url="https://artifact-site.lexmount.cn/v/capability",
        report_url="https://artifact-site.lexmount.cn/api/preview/new-site?share=capability",
        version="ver_new",
    )

    publication = json.loads(profile.read_text())["publication"]
    assert publication["base_url"] == "https://artifact-site.lexmount.cn"
    assert publication["site_slug"] == "new-site"
    assert publication["expected_version"] == "ver_new"
    assert json.loads(attestation.read_text())["profile_sha256"] == _sha256(profile)


def test_uncertain_create_recovers_same_site_from_operation(
    monkeypatch, tmp_path: Path
) -> None:
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"report")
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    calls = []
    started = False

    def fake_request(_base, path, *_args, **kwargs):
        nonlocal started
        calls.append((path, kwargs.get("idempotency_key", "")))
        if path.startswith("/api/operations/"):
            if started:
                return 200, b'{"status":"completed","result":{"slug":"site-1"}}'
            return 404, b'{}'
        if path == "/api/sites":
            started = True
            raise TimeoutError("upload response lost")
        raise AssertionError(path)

    monkeypatch.setattr(publisher, "request", fake_request)
    with pytest.raises(TimeoutError, match="response lost"):
        publisher.create_site("https://site.example", b"zip", "application/zip")
    pending = json.loads(receipt.read_text())
    assert pending["idempotency_key"].startswith("browser-use-create-")
    assert pending["slug"] == ""
    assert publisher.create_site("https://site.example", b"zip", "application/zip") == "site-1"
    assert [path for path, _ in calls].count("/api/sites") == 1
    assert json.loads(receipt.read_text())["slug"] == "site-1"


def test_create_reuses_pending_known_slug_without_network(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"report")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "base_url": "https://site.example",
        "archive_sha256": _sha256(archive),
        "slug": "site-1",
        "verified": False,
    }))
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    monkeypatch.setattr(
        publisher, "request", lambda *args, **kwargs: pytest.fail("unexpected network call")
    )
    assert publisher.create_site("https://site.example", b"report", "application/zip") == "site-1"


def test_uncertain_update_recovers_same_version_from_operation(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "report.zip"
    archive.write_bytes(b"new report")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps({"base_url": "https://site.example", "slug": "site-1", "verified": True})
    )
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    calls = []
    started = False

    def fake_request(_base, path, *_args, **kwargs):
        nonlocal started
        calls.append((path, kwargs.get("idempotency_key", "")))
        if path.startswith("/api/operations/"):
            return (200, b'{"status":"completed"}') if started else (404, b'{}')
        if path.startswith("/api/sites/site-1/versions?"):
            started = True
            raise TimeoutError("upload response lost")
        raise AssertionError(path)

    monkeypatch.setattr(publisher, "request", fake_request)
    with pytest.raises(TimeoutError, match="response lost"):
        publisher.update_site(
            "https://site.example", "site-1", "ver_old", b"zip", "application/zip"
        )
    pending = json.loads(receipt.read_text())["pending_update"]
    assert pending["idempotency_key"].startswith("browser-use-update-")
    publisher.update_site("https://site.example", "site-1", "ver_old", b"zip", "application/zip")
    assert sum(path.startswith("/api/sites/site-1/versions?") for path, _ in calls) == 1


@pytest.mark.parametrize("crash_after_binding", [False, True])
def test_identical_published_package_replays_without_new_version(
    monkeypatch, tmp_path: Path, crash_after_binding: bool
) -> None:
    base = "https://site.example"
    share_url = base + "/v/capability"
    report_path = "/api/preview/site-1?share=capability"
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("index.html", publisher.PUBLIC_REPORT_MARKER)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"publication": {
        "base_url": base, "site_slug": "site-1", "expected_version": "ver_new",
        "share_url": share_url, "report_url": base + report_path, "state": "verified",
    }}), encoding="utf-8")
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"profile_sha256": _sha256(profile)}), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    previous = {"base_url": base, "slug": "site-1", "verified": True}
    if crash_after_binding:
        previous["pending_update"] = {
            "archive_sha256": _sha256(archive), "expected_version": "ver_old",
            "idempotency_key": "browser-use-update-previous",
        }
        previous["version"] = "ver_old"
    receipt.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(publisher, "ARCHIVE", archive)
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation)
    monkeypatch.setattr(publisher, "RECEIPT", receipt)
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "binding-pending.json")
    monkeypatch.setattr(publisher, "validate_package", lambda: None)
    calls = []

    def fake_request(_base, path, *args, **kwargs):
        calls.append((path, kwargs.get("method"), kwargs.get("authenticated", True)))
        if path == "/api/sites/site-1":
            return 200, b'{"site":{"currentVersionId":"ver_new"}}'
        if path == "/api/sites/site-1/export":
            return 200, archive.read_bytes()
        if path == "/v/capability":
            return 200, b"public share"
        if path == report_path:
            return 200, publisher.PUBLIC_REPORT_MARKER.encode()
        raise AssertionError(f"unexpected mutating or unrelated request: {path}")

    monkeypatch.setattr(publisher, "request", fake_request)
    publisher.main()

    assert not any(method == "POST" or "/versions" in path for path, method, _ in calls)
    assert ("/v/capability", None, False) in calls
    assert (report_path, None, False) in calls
    assert json.loads(profile.read_text())["publication"]["expected_version"] == "ver_new"
    assert json.loads(attestation.read_text())["profile_sha256"] == _sha256(profile)
    saved = json.loads(receipt.read_text())
    assert saved["version"] == "ver_new"
    assert saved["verified"] is True
    assert "pending_update" not in saved


def test_packaging_reuses_visual_gate_bound_to_current_report(monkeypatch, tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "index.html").write_text("report", encoding="utf-8")
    evidence = [tmp_path / "evidence-a", tmp_path / "evidence-b"]
    receipt = tmp_path / "visual.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_visual_qa/1",
                "report_tree_sha256": packager.tree_sha256(report),
                "states_checked": 112,
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(
        packager,
        "check",
        lambda report_dir, evidence_dirs, selection, *, require_complete: calls.append(
            ("report", report_dir, evidence_dirs, selection, require_complete)
        ),
    )
    monkeypatch.setattr(
        packager, "check_links", lambda report_dir: calls.append(("links", report_dir))
    )
    monkeypatch.setattr(
        packager,
        "check_visual",
        lambda report_dir, receipt_path: calls.append(("visual", report_dir, receipt_path)),
    )

    packager.validate_release(report, evidence, receipt)

    assert [call[0] for call in calls] == ["report", "links"]
    assert calls[0][-1] is True

    (report / "index.html").write_text("changed", encoding="utf-8")
    packager.validate_release(report, evidence, receipt)
    assert calls[-1] == ("visual", report, receipt)


def test_packaging_excludes_local_marker_and_rejects_other_hidden_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text("<head></head>", encoding="utf-8")
    (tmp_path / ".generated-report").write_text("marker", encoding="utf-8")
    assert [path.name for path in packager.publication_files(tmp_path)] == ["index.html"]
    (tmp_path / ".private").write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden site path"):
        packager.publication_files(tmp_path)
