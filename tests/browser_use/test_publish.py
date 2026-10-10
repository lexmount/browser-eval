from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.browser_use import report_publish as publisher


@pytest.fixture(autouse=True)
def private_binding_journal(monkeypatch, tmp_path):
    monkeypatch.setattr(publisher, "BINDING_JOURNAL", tmp_path / "binding.json")
    monkeypatch.setattr(publisher, "OPERATION_JOURNAL", tmp_path / "operation.json")


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
        for name in ("manifest.json", "run-config.json", "attempts.jsonl", "result-review.json"):
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


def test_bind_published_version_advances_profile_and_attestation(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"publication": {"expected_version": "ver_old"}}), encoding="utf-8"
    )
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({"profile_sha256": "stale"}), encoding="utf-8")
    monkeypatch.setattr(publisher, "PROFILE", profile)
    monkeypatch.setattr(publisher, "ATTESTATION", attestation)

    publisher.bind_published_version("ver_new")

    assert json.loads(profile.read_text())["publication"]["expected_version"] == "ver_new"
    assert json.loads(attestation.read_text())["profile_sha256"] == _sha256(profile)
