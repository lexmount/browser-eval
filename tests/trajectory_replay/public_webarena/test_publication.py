from __future__ import annotations

import json

import pytest

from harness import env_file
from harness.trajectory_replay.public_webarena.publication import (
    build,
    export_public_report_bundle,
    public_projection,
    stage_report,
)
from tests.public_webarena_fixture import write_report_fixture


def test_public_projection_scrubs_private_hosts_and_user_paths():
    value = {"url": "http://10.0.0.41:17770/x", "path": "/data/example/run/file"}
    assert public_projection(value) == {
        "url": "http://webarena.local:17770/x",
        "path": "/[local-user]/run/file",
    }


def test_public_projection_needs_no_site_logins_to_rebuild_saved_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(env_file, "_repository_credentials_file", tmp_path / "absent.env")
    assert public_projection({"note": "unchanged"}) == {"note": "unchanged"}


def test_public_package_rejects_a_site_password_the_projection_missed(tmp_path, monkeypatch):
    source, authority = write_report_fixture(tmp_path)
    staged = tmp_path / "staged"
    stage_report(source, staged)
    page = staged / "reports/webarena-trajectory-replay-latest.html"
    page.write_text(page.read_text(encoding="utf-8") + "leaked-secret", encoding="utf-8")
    secrets = tmp_path / "repository.env"
    secrets.write_text("WEBARENA_REDDIT_PASSWORD=leaked-secret\n", encoding="utf-8")
    monkeypatch.setattr(env_file, "_repository_credentials_file", secrets)
    monkeypatch.setenv("WEBARENA_REDDIT_PASSWORD", "exported-secret")

    with pytest.raises(ValueError, match="site password in public report"):
        build(staged, tmp_path / "a.zip", tmp_path / "a.json", authority=authority)


def test_public_bundle_contains_reports_and_minimal_binding(tmp_path):
    source, _ = write_report_fixture(tmp_path)
    manifest_path = source / "run/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact_provenance"] = {"artifact_directory": "/data/example/run"}
    manifest["private_notes"] = "private reviewer notes"
    manifest_path.write_text(json.dumps(manifest))
    source_summary = json.loads((source / "run/summary.json").read_text())
    formal = tmp_path / "formal"
    stage_report(source, formal)

    target = export_public_report_bundle(formal, tmp_path / "public")

    manifest = json.loads((target / "run/manifest.json").read_text())
    summary = json.loads((target / "run/summary.json").read_text())
    assert manifest == {
        "suite_sha256": "suite",
        "artifact_provenance": {"artifact_directory": "/[local-user]/run"},
    }
    assert summary == {"functional": source_summary["functional"], "resource_comparison": {}}
    assert (target / "reports/webarena-trajectory-replay-latest.html").read_bytes() == (
        formal / "reports/webarena-trajectory-replay-latest.html").read_bytes()
