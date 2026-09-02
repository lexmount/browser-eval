import json

import pytest

from harness.browser_use.report import METRICS
from harness.browser_use.version_comparison import compare_runs, render


def make_run(path, *, failed=False, chromium="same", duration=100):
    path.mkdir()
    manifest = {
        "suite_sha256": "same",
        "suite": [{"task_id": "one", "title": "One"}, {"task_id": "two", "title": "Two"}],
        "protocol": {"sample_interval_ms": 100},
        "environment": {
            "docker_architecture": "arm64",
            "docker_cpus": 4,
            "docker_memory_bytes": 6000,
            "images": {"chromium": {"id": chromium}},
            "browser_versions": {"moli": "moli test"},
        },
    }
    rows = []
    for task in ("one", "two"):
        for engine in ("chromium", "moli"):
            row = {
                "task_id": task,
                "engine": engine,
                "attempt": 1,
                "status": "pass",
                "duration_ms": duration,
                "resource": {k: 10 for _, g in METRICS for k, *_ in g},
            }
            if failed and task == "two" and engine == "moli":
                row["status"] = "fail"
                rows.append(row.copy())
                row = {**row, "attempt": 2, "status": "pass", "duration_ms": 1}
            rows.append(row)
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / "attempts.jsonl").write_text("\n".join(json.dumps(row) for row in rows))


def test_common_cohort_excludes_retry_recovery_and_ratios_are_paired(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    make_run(old, failed=True, duration=100)
    make_run(new, duration=50)
    before = (old / "attempts.jsonl").read_bytes()
    result = compare_runs(old, new)
    assert result["correctness"] == [[2, 2], [2, 2]]
    assert result["common_task_ids"] == ["one"]
    assert result["metrics"]["duration_ms"]["new_over_old_moli"]["p95"] == 0.5
    assert (old / "attempts.jsonl").read_bytes() == before
    page = render(result)
    assert "moli-1.0.6-rerun/" in page and "moli-1.1.1/" in page
    assert "<small>one</small>" in page


def test_rejects_chromium_drift(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    make_run(old)
    make_run(new, chromium="different")
    with pytest.raises(ValueError, match="Chromium"):
        compare_runs(old, new)
