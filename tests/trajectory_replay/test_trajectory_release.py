from __future__ import annotations

import json

import pytest

from harness.trajectory_replay.report import METRICS
from scripts import release_webarena_trajectory_report as release
from scripts.check_webarena_trajectory_report import check_report_bundle


def test_stage_builds_report_without_mutating_source_evidence(tmp_path):
    source = tmp_path / "source"
    (source / "run").mkdir(parents=True)
    manifest = {
        "run_id": "run",
        "suite_sha256": "suite",
        "protocol": {},
        "environment": {},
        "suite": [{"task_id": "task"}],
    }
    summary = {
        "functional": {
            "chromium": {"physical_attempts": 1, "result_final_passes": 1},
            "moli": {"physical_attempts": 1, "result_final_passes": 1},
        },
        "by_task": {
            "task": {
                "attempts": {
                    engine: [
                        {
                            "attempt": 1,
                            "is_retry": False,
                            "status": "pass",
                            "result_status": "pass",
                            "process_status": "pass",
                            "combined_status": "pass",
                            "evidence_status": "pass",
                            "error": "",
                            "process_validation": {
                                "expected_actions": ["click"],
                                "actual_actions": ["click"],
                                "all_calls_passed": True,
                                "passed": True,
                            },
                            "validation": [{"status": "success", "score": 1.0}],
                            "trace": [
                                {"action": "click", "status": "pass"},
                                {
                                    "action": "evaluate_result",
                                    "status": "pass",
                                    "checks": [{"status": "success", "score": 1.0}],
                                },
                            ],
                            "network_har": {"status": "complete"},
                            "final_page_evidence": {"status": "complete"},
                        }
                    ]
                    for engine in ("chromium", "moli")
                }
            }
        },
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
    assert check_report_bundle(staged) == {
        "pages": 6,
        "metrics": sum(len(metrics) for _, metrics in METRICS),
        "ratio_modules": 0,
    }
    assert (source / "run/summary.json").read_bytes() == before

    homepage = staged / "reports/webarena-trajectory-replay-latest.html"
    broken = homepage.read_text(encoding="utf-8").replace(
        'data-preference="neutral">', 'data-preference="neutral"><b class="better">wrong</b>', 1
    )
    homepage.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="neutral metric must not declare a winner"):
        check_report_bundle(staged)


def test_profile_is_updated_only_with_verified_publication_values(tmp_path, monkeypatch):
    profile = tmp_path / "current-report.json"
    profile.write_text(
        json.dumps({"publication": {"version_id": "old", "submitted_files_verified": 1}})
    )
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
