from __future__ import annotations

import json

import pytest

from harness.web_fetch.report_build import copy_browser_use_report


def _fixture(root):
    formal = root / "formal" / "webarena-public-trajectories"
    run = formal / "run"
    reports = formal / "reports"
    run.mkdir(parents=True)
    reports.mkdir()
    provenance = {"attempts_sha256": "a", "summary_sha256": "b"}
    functional = {"by_engine": {}}
    resource = {"by_engine": {}}
    (run / "manifest.json").write_text(
        json.dumps({"suite_sha256": "suite", "artifact_provenance": provenance})
    )
    (run / "summary.json").write_text(
        json.dumps({"functional": functional, "resource_comparison": resource})
    )
    stem = "browser-use-evaluation-latest"
    (reports / f"{stem}.html").write_text("home")
    (reports / f"{stem}-research.html").write_text("research")
    (reports / f"{stem}-data.json").write_text(
        json.dumps(
            {
                "suite_sha256": "suite",
                "artifact_provenance": provenance,
                "functional": functional,
                "resource_comparison": resource,
            }
        )
    )
    return formal


def test_browser_report_copy_requires_exact_formal_result_binding(tmp_path):
    formal = _fixture(tmp_path)
    output = tmp_path / "output"
    assert copy_browser_use_report(tmp_path, output)
    assert (output / "browser-use/browser-use-evaluation-latest.html").read_text() == "home"

    audit = formal / "reports/browser-use-evaluation-latest-data.json"
    row = json.loads(audit.read_text())
    row["functional"] = {"tampered": True}
    audit.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="functional binding mismatch"):
        copy_browser_use_report(tmp_path, tmp_path / "other-output")
