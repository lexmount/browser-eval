"""Shared sample filenames must not select a different benchmark's release gate."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATES = [[str(ROOT / "scripts/check_report.py")], ["-m", "harness.web_fetch", "report-revision-check"]]


@pytest.mark.parametrize("command", GATES, ids=lambda gate: gate[-1].rsplit("/", 1)[-1])
def test_webmainbench_sample_is_not_routed_to_wcxb(tmp_path, command):
    (tmp_path / "sample-manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    (tmp_path / "dataset.json").write_text(
        json.dumps({"comparison": {"kind": "webmainbench_frozen_html_to_markdown"}, "cases": []}),
        encoding="utf-8"
    )
    profile = tmp_path / 'selected-profile.json'
    profile.write_text(json.dumps({'comparison_kind': 'webmainbench_frozen_html_to_markdown'}), encoding="utf-8")
    (tmp_path / 'index.html').write_text('<html lang="zh-CN"></html>', encoding="utf-8")
    from harness.report_languages import write_language_contract
    write_language_contract(tmp_path, include_english=False)
    result = subprocess.run(
        [sys.executable, *command, str(tmp_path), "--profile", str(profile)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "results.jsonl" in result.stderr
    assert "source-qualified" not in result.stderr


@pytest.mark.parametrize("command", GATES, ids=lambda gate: gate[-1].rsplit("/", 1)[-1])
def test_wcxb_report_does_not_require_web_fetch_dataset_file(tmp_path, command):
    from harness.web_fetch.wcxb_publication import write_inventory
    (tmp_path / "sample-manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    (tmp_path / "wcxb-score-summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "index.html").write_text('<h1>Synthetic WCXB report</h1>', encoding="utf-8")
    profile = tmp_path / "selected-profile.json"
    profile.write_text(json.dumps({"comparison_kind": "wcxb_frozen_html_to_markdown"}), encoding="utf-8")
    (tmp_path / "report-content-binding.json").write_text("{}", encoding="utf-8")
    write_inventory(tmp_path, json.loads(profile.read_bytes()))
    result = subprocess.run(
        [sys.executable, *command, str(tmp_path), "--profile", str(profile)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "guide/index.html" in result.stderr
    assert "dataset.json" not in result.stderr
