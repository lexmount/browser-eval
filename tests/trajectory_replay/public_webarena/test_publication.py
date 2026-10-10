from __future__ import annotations

import json

from harness.trajectory_replay.public_webarena.publication import (
    export_public_report_bundle,
    public_projection,
    stage_report,
)
from tests.public_webarena_fixture import write_report_fixture


def test_public_projection_scrubs_private_hosts_and_user_paths():
    value = {"url": "http://10.2.131.41:17770/x", "path": "/data/linkai/run/file"}
    assert public_projection(value) == {
        "url": "http://webarena.local:17770/x",
        "path": "/[local-user]/run/file",
    }


def test_public_bundle_contains_reports_and_minimal_binding(tmp_path):
    source, _ = write_report_fixture(tmp_path)
    manifest_path = source / "run/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact_provenance"] = {"artifact_directory": "/data/linkai/run"}
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
