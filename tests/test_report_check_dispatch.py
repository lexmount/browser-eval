"""Shared sample filenames must not select a different benchmark's release gate."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command", ["check_report.py", "check_report_revision.py"])
def test_webmainbench_sample_is_not_routed_to_wcxb(tmp_path, command):
    (tmp_path / "sample-manifest.json").write_text(json.dumps({"cases": []}))
    (tmp_path / "dataset.json").write_text(
        json.dumps({"comparison": {"kind": "webmainbench_frozen_html_to_markdown"}, "cases": []})
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / command), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "results.jsonl" in result.stderr
    assert "source-qualified" not in result.stderr


@pytest.mark.parametrize("command", ["check_report.py", "check_report_revision.py"])
def test_wcxb_report_does_not_require_web_fetch_dataset_file(tmp_path, command):
    (tmp_path / "sample-manifest.json").write_text(json.dumps({"cases": []}))
    (tmp_path / "wcxb-score-summary.json").write_text("{}")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / command), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not source-qualified" in result.stderr
    assert "dataset.json" not in result.stderr
