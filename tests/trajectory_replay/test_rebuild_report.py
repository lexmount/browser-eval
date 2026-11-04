"""The rebuild command chooses a saved run's participants and ratio baseline."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.trajectory_replay.core.suite import BrowserTask, TaskSuite
from tests.trajectory_replay.curated_report_fixture import _row

ROOT = Path(__file__).resolve().parents[2]
TASKS = ("static-title", "dynamic-list")


def _rebuild(tmp_path, monkeypatch, row_engines, protocol):
    """Run the public command on saved attempts; return its summary and report writer."""
    spec = importlib.util.spec_from_file_location(
        "rebuild_report_under_test", ROOT / "examples/trajectory_replay/rebuild_report.py"
    )
    rebuild = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rebuild)

    # Each engine's position sets its scale, so every row keeps its engine's speed
    # however the rows are ordered: chromium is the fastest, moli takes twice as long.
    scales = {"chromium": 0, "moli": 1, "obscura": 2, "lightpanda": 3}
    rows = [
        _row(task_id, "simple", engine, scales[engine], False)
        for task_id in TASKS
        for engine in row_engines
    ]
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "attempts.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}", encoding="utf-8")
    suite_sha256 = hashlib.sha256(suite_path.read_bytes()).hexdigest()
    manifest = {
        "suite_sha256": suite_sha256,
        "suite": [{"task_id": task_id} for task_id in TASKS],
        "protocol": protocol,
    }
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    evidence = tmp_path / "ground-truth.json"
    evidence.write_text(
        json.dumps(
            {
                "dataset_sha256": suite_sha256,
                "results": [
                    {"task_id": task_id, "status": "pass", "observed": {}} for task_id in TASKS
                ],
                "automatic_browser_replay": {"status": "pass", "passed": len(TASKS)},
            }
        ),
        encoding="utf-8",
    )
    tasks = tuple(
        BrowserTask(task_id, task_id, (), (), (), "simple", {"expected_outputs": {}}, {})
        for task_id in TASKS
    )
    monkeypatch.setattr(rebuild, "load_suite", lambda _: TaskSuite("test", tasks))
    report = MagicMock()
    monkeypatch.setattr(rebuild, "write_latest_report", report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rebuild_report",
            "--artifacts", str(artifacts),
            "--suite", str(suite_path),
            "--reports", str(tmp_path / "reports"),
            "--ground-truth-evidence", str(evidence),
        ],
    )
    rebuild.main()
    return json.loads((artifacts / "summary.json").read_text(encoding="utf-8")), report


def test_legacy_manifest_keeps_the_registered_baseline_when_moli_rows_come_first(
    tmp_path, monkeypatch
):
    summary, report = _rebuild(tmp_path, monkeypatch, ("moli", "chromium"), {})

    assert list(summary["functional"]) == ["chromium", "moli"]
    ratios = summary["resource_comparison"]["paired_ratios"]
    assert ratios["denominator_engine"] == "chromium"
    assert ratios["numerator_engine"] == "moli"
    assert ratios["metrics"]["duration_ms"]["mean"] > 1.9
    report.assert_called_once()


def test_legacy_manifest_orders_unregistered_engines_after_registered_ones(tmp_path, monkeypatch):
    row_engines = ("obscura", "moli", "lightpanda", "chromium")
    summary, _ = _rebuild(tmp_path, monkeypatch, row_engines, {})

    assert list(summary["functional"]) == ["chromium", "moli", "obscura", "lightpanda"]
    assert summary["resource_comparison"]["relative_ratios"]["baseline_engine"] == "chromium"


def test_declared_engines_set_the_order_regardless_of_row_order(tmp_path, monkeypatch):
    protocol = {"engines": ["chromium", "obscura", "moli"]}
    summary, _ = _rebuild(tmp_path, monkeypatch, ("moli", "obscura", "chromium"), protocol)

    assert list(summary["functional"]) == ["chromium", "obscura", "moli"]


@pytest.mark.parametrize(
    "declared",
    [["chromium"], ["chromium", "moli", "obscura"], ["chromium", "moli", "moli"]],
    ids=["row-engine-unlisted", "listed-engine-without-rows", "duplicate-engine"],
)
def test_manifest_that_disagrees_with_its_rows_is_rejected(tmp_path, monkeypatch, declared):
    with pytest.raises(SystemExit, match="protocol.engines"):
        _rebuild(tmp_path, monkeypatch, ("chromium", "moli"), {"engines": declared})

    assert not (tmp_path / "artifacts/summary.json").exists()
