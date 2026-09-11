from __future__ import annotations

import hashlib
import json
from pathlib import Path

import scripts.publish_browser_use_artifact_hub as publisher


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
