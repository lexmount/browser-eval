from __future__ import annotations

import hashlib
import json
import sys
import zipfile

import pytest

from harness.trajectory_replay.public_webarena import release
from harness.trajectory_replay.public_webarena.publication import (
    build,
    check_report_bundle,
    stage_report,
)
from harness.trajectory_replay.public_webarena.report import METRICS
from tests.public_webarena_fixture import write_report_fixture


def test_stage_builds_report_without_mutating_source_evidence(tmp_path, monkeypatch):
    source, profile = write_report_fixture(tmp_path)
    before = (source / "run/summary.json").read_bytes()

    staged = tmp_path / "staged"
    stage_report(source, staged)

    assert (staged / "reports/webarena-trajectory-replay-latest.html").is_file()
    assert (staged / "reports/webarena-trajectory-replay-latest-results.html").is_file()
    method = staged / "reports/webarena-trajectory-replay-latest-method.html"
    assert method.is_file()
    assert "核验结论" in method.read_text(encoding="utf-8")
    assert check_report_bundle(staged) == {
        "pages": 6,
        "metrics": sum(len(metrics) for _, metrics in METRICS),
        "ratio_modules": 0,
    }
    assert (source / "run/summary.json").read_bytes() == before

    authority = json.loads(profile.read_text())
    archive, receipt = tmp_path / "report.zip", tmp_path / "attestation.json"
    result = build(staged, archive, receipt, authority=profile)
    assert result["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("index.html") == bundle.read("webarena-trajectory-replay-latest.html")
    assert result["results"] == {
        engine: {"tasks": 1, "passes": 1} for engine in ("chromium", "moli")
    }
    monkeypatch.setattr(sys, "argv", ["release", "--formal-root", str(source)])
    release.main(tmp_path)
    assert (tmp_path / "publish/webarena-fixed-trajectory.zip").is_file()
    assert json.loads(profile.read_text()) == authority
    authority["suite_sha256"] = "wrong"
    profile.write_text(json.dumps(authority))
    rejected = tmp_path / "rejected.zip"
    with pytest.raises(ValueError, match="current report authority mismatch: suite hash"):
        build(staged, rejected, receipt, authority=profile)
    assert not rejected.exists()

    homepage = staged / "reports/webarena-trajectory-replay-latest.html"
    broken = homepage.read_text(encoding="utf-8").replace(
        'data-preference="neutral">', 'data-preference="neutral"><b class="better">wrong</b>', 1
    )
    homepage.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="neutral metric must not declare a winner"):
        check_report_bundle(staged)


def test_profile_is_updated_only_with_verified_publication_values(tmp_path):
    profile = tmp_path / "current-report.json"
    profile.write_text(
        json.dumps({"publication": {"version_id": "old", "submitted_files_verified": 1}})
    )
    release._update_profile(profile, "ver_new", 71)

    current = json.loads(profile.read_text())
    assert current["publication"] == {
        "version_id": "ver_new",
        "submitted_files_verified": 71,
    }


def test_verification_command_uses_required_slug_option(tmp_path):
    command = release._verification_command(
        {"site_slug": "site", "base_url": "https://hub.example"},
        tmp_path / "report.zip",
        tmp_path,
    )
    assert command[2:4] == ["--slug", "site"]
    assert command[-2:] == ["--expected-archive", str(tmp_path / "report.zip")]
