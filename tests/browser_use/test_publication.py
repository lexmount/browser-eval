from __future__ import annotations

import json

from harness.browser_use.publication import export_public_report_bundle, public_projection


def test_public_projection_scrubs_private_hosts_and_user_paths():
    value = {"url": "http://10.2.131.41:17770/x", "path": "/data/linkai/run/file"}
    assert public_projection(value) == {
        "url": "http://webarena.local:17770/x",
        "path": "/[local-user]/run/file",
    }


def test_public_bundle_contains_reports_and_minimal_binding(tmp_path):
    formal = tmp_path / "source"
    (formal / "run").mkdir(parents=True)
    (formal / "reports").mkdir()
    (formal / "run/manifest.json").write_text(
        json.dumps(
            {
                "suite_sha256": "suite",
                "suite": [{"private": True}],
                "artifact_provenance": {"artifact_directory": "/data/linkai/run"},
            }
        )
    )
    (formal / "run/summary.json").write_text(
        json.dumps({"functional": {"moli": {"tasks": 1}}, "by_task": {"private": True}})
    )
    (formal / "reports/browser-use-evaluation-latest.html").write_text("report")

    target = export_public_report_bundle(formal, tmp_path / "public")

    manifest = json.loads((target / "run/manifest.json").read_text())
    summary = json.loads((target / "run/summary.json").read_text())
    assert manifest == {
        "suite_sha256": "suite",
        "artifact_provenance": {"artifact_directory": "/[local-user]/run"},
    }
    assert summary == {"functional": {"moli": {"tasks": 1}}, "resource_comparison": {}}
    assert (target / "reports/browser-use-evaluation-latest.html").read_text() == "report"
