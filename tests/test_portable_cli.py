"""Exercise the installed application's contract without repository resources."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from harness.web_fetch.cli import default_run_dir
from harness.web_fetch.report import run_log_section


def test_call_evidence_labels_a_non_moli_tool_version(tmp_path):
    record = {"attempt_id": "other__page", "run_id": "example-run", "ok": True,
              "elapsed_ms": 10, "provider_metadata": {"browser_version": "OtherBrowser 2.0"}}
    page = run_log_section(tmp_path, record, "example content")
    assert "工具版本" in page
    assert "OtherBrowser 2.0" in page
    assert "Moli 版本" not in page


def test_default_evidence_is_outside_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "data"))
    assert default_run_dir().is_relative_to(tmp_path / "data" / "runs")


def test_offline_example_runs_without_a_repository(tmp_path):
    root = Path(__file__).resolve().parents[1]
    installation = tmp_path / "installation"
    shutil.copytree(root / "harness", installation / "harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    env = {**os.environ, "PYTHONPATH": str(installation)}
    for arguments, expected_code in [
        (["validate"], 0),
        (["run", "--delay", "0", "--output", str(tmp_path / "run"),
          "--fail-on-failure"], 2),
    ]:
        result = subprocess.run([sys.executable, "-m", "harness.web_fetch", *arguments],
                                cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == expected_code, result.stdout + result.stderr
    import json
    run = tmp_path / "run"
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert {row["ok"] for row in rows} == {True, False}
    reliability = json.loads((run / "report/reliability.json").read_text())
    assert reliability["complete"]
    assert reliability["expected_calls"] == 2
    home = (run / "report/index.html").read_text()
    assert "合成示例" in home
    assert "非正式评测" in home
    assert "实测报告" not in home
    assert "网页抓取成功之后，内容离可用还有多远" not in home
    guide = (run / "report/guide/index.html").read_text()
    assert "本报告的任务范围与验收规则" in guide
    from scripts.check_report_links import check
    check(run / "report")
    for row in rows:
        assert (run / "report/attempts" / (row["attempt_id"] + ".html")).is_file()
