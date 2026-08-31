from __future__ import annotations

from harness.browser_use.report import write_latest_report


def test_report_uses_fixed_latest_filename_and_removes_legacy_versions(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "browser-use-evaluation-20260101.md").write_text("old", encoding="utf-8")

    path = write_latest_report(
        reports,
        manifest={
            "run_id": "run-1",
            "environment": {"docker": "29"},
            "protocol": {"repeats": 5, "sample_interval_ms": 100},
            "suite": [
                {
                    "task_id": "example",
                    "title": "Example task",
                    "step_count": 2,
                    "complexity": "step_01_05",
                }
            ],
        },
        summary={
            "functional": {},
            "by_complexity": {},
            "resource_comparison": {
                "intersection_attempts": 0,
                "by_engine": {},
            },
        },
    )

    assert path.name == "browser-use-evaluation-latest.md"
    assert path.exists()
    assert not (reports / "browser-use-evaluation-20260101.md").exists()
    rendered = (reports / "browser-use-evaluation-latest.html").read_text(encoding="utf-8")
    assert "Methodology" in rendered
    assert "Observed findings and decision" in rendered
    assert "Example task" in rendered
    assert "<table" in rendered
    assert "<pre># Browser Use Evaluation" not in rendered
