from __future__ import annotations

import json

from scripts import release_webarena_trajectory_report as release


def test_stage_builds_report_without_mutating_source_evidence(tmp_path):
    source = tmp_path / "source"
    (source / "run").mkdir(parents=True)
    manifest = {
        "run_id": "run",
        "suite_sha256": "suite",
        "protocol": {},
        "environment": {},
        "suite": [],
    }
    summary = {
        "functional": {"chromium": {}, "moli": {}},
        "by_task": {},
        "by_complexity": {},
        "resource_comparison": {},
    }
    (source / "run/manifest.json").write_text(json.dumps(manifest))
    (source / "run/summary.json").write_text(json.dumps(summary))
    before = (source / "run/summary.json").read_bytes()

    staged = tmp_path / "staged"
    release._stage(source, staged)

    assert (staged / "reports/webarena-trajectory-replay-latest.html").is_file()
    assert (staged / "reports/webarena-trajectory-replay-latest-results.html").is_file()
    method = staged / "reports/webarena-trajectory-replay-latest-method.html"
    assert method.is_file()
    assert "核验结论" in method.read_text(encoding="utf-8")
    assert (source / "run/summary.json").read_bytes() == before


def test_profile_is_updated_only_with_verified_publication_values(tmp_path, monkeypatch):
    profile = tmp_path / "current-report.json"
    profile.write_text(json.dumps({"publication": {"version_id": "old", "submitted_files_verified": 1}}))
    monkeypatch.setattr(release, "CURRENT", profile)

    release._update_profile("ver_new", 71)

    current = json.loads(profile.read_text())
    assert current["publication"] == {
        "version_id": "ver_new",
        "submitted_files_verified": 71,
    }


def test_verification_command_uses_required_slug_option(tmp_path):
    command = release._verification_command(
        {"site_slug": "site", "base_url": "https://hub.example"}, tmp_path / "report.zip"
    )
    assert command[2:4] == ["--slug", "site"]
    assert command[-2:] == ["--expected-archive", str(tmp_path / "report.zip")]
