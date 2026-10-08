"""Exercise the installed application's contract without repository resources."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_base_cli_does_not_load_optional_execution_dependencies(tmp_path):
    program = '''
import importlib.abc
import sys
class BaseInstallation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        optional = {'docker', 'playwright', 'openai', 'webarena_verified', 'pypdf', 'requests'}
        if fullname.split('.')[0] in optional:
            raise AssertionError('Base CLI loaded optional dependency: ' + fullname)
sys.meta_path.insert(0, BaseInstallation())
from harness.web_fetch.cli import main
sys.argv = ['browser-harness-eval', 'validate']
main()
'''
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("unbound_diagnostics", [
    json.dumps([{"case_id": "fixture-failure", "url": "https://unrelated.invalid/",
                 "status": 200, "title": "Unrelated historical source"}]),
    "invalid historical JSON",
])
def test_offline_example_runs_without_a_repository(tmp_path, unbound_diagnostics):
    root = Path(__file__).resolve().parents[1]
    installation = tmp_path / "installation"
    shutil.copytree(root / "harness", installation / "harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # A shared case ID does not bind another dataset's source observation.
    (installation / "research").mkdir()
    (installation / "research/failure-sources.json").write_text(unbound_diagnostics)
    env = {**os.environ, "PYTHONPATH": str(installation)}
    for arguments, expected_code in [
        (["validate"], 0),
        (["run", "--delay", "0", "--output", str(tmp_path / "run"),
          "--fail-on-failure"], 2),
    ]:
        result = subprocess.run([sys.executable, "-m", "harness.web_fetch", *arguments],
                                cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == expected_code, result.stdout + result.stderr
    run = tmp_path / "run"
    rows = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert {row["ok"] for row in rows} == {True, False}
    reported = [json.loads(line) for line in
                (run / "report/results.jsonl").read_text().splitlines()]
    assert reported == rows
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
    from harness.reporting.links import check
    check(run / "report")
    for row in rows:
        assert (run / "report/attempts" / (row["attempt_id"] + ".html")).is_file()
