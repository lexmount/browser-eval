"""Language choice at the public run/rebuild boundary, independent of scoring."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "harness.web_fetch", *args],
                          cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


@pytest.mark.parametrize("language", [None, "en", "zh"])
def test_run_selects_language_and_rebuild_preserves_it(tmp_path, language):
    run = tmp_path / "run"
    options = ["--language", language] if language else []
    result = cli("run", "--delay", "0", "--output", str(run), *options)
    assert result.returncode == 0, result.stdout + result.stderr
    selected = language or "en"
    contract = json.loads((run / "report/report-languages.json").read_text())
    assert contract["primary_language"] == selected
    assert contract["languages"] == (["zh", "en"] if selected == "en" else ["zh"])
    expected = run / "report" / ("en/index.html" if selected == "en" else "index.html")
    assert str(expected) in result.stdout
    assert ('lang="en"' if selected == "en" else 'lang="zh-CN"') in expected.read_text()
    measured = (run / "results.jsonl").read_bytes()
    rebuilt = cli("report", str(run))
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert str(expected) in rebuilt.stdout
    assert (run / "results.jsonl").read_bytes() == measured
    other = "zh" if selected == "en" else "en"
    changed = cli("report", str(run), "--language", other)
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert json.loads((run / "report/report-languages.json").read_text())["primary_language"] == other
    assert (run / "results.jsonl").read_bytes() == measured
    from harness.reporting.links import check
    check(run / "report")


def test_unsupported_language_fails_before_calls_or_output(tmp_path):
    run = tmp_path / "run"
    result = cli("run", "--language", "fr", "--output", str(run))
    assert result.returncode == 2
    assert "en" in result.stderr and "zh" in result.stderr
    assert not run.exists()


@pytest.mark.parametrize("script", ["build_report.py", "build_browser_use_report.py", "build_webarena_replay_report.py"])
def test_report_entrypoints_reject_unknown_language_before_loading_evidence(tmp_path, script):
    if script == "build_webarena_replay_report.py":
        pytest.importorskip("webarena_verified")
    output = tmp_path / "report"
    arguments = ["--language", "fr", "--output", str(output)]
    arguments += (["--results", "missing"] if "replay" in script else ["--evidence", "missing"])
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *arguments],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert not output.exists()


def test_default_english_has_localized_ui_and_preserved_task_text(tmp_path):
    from harness.web_fetch.report_i18n import untranslated_english_narrative
    run = tmp_path / "run"
    result = cli("run", "--delay", "0", "--output", str(run))
    assert result.returncode == 0, result.stderr
    dataset = json.loads((run / "dataset.json").read_text())
    # Task wording and participant metadata are source inputs, not report copy.
    source_text = {"中文", "确定性安装测试", "固定"}
    source_text.update(case["name"] for case in dataset["cases"])
    source_text.update(case["task_prompt"] for case in dataset["cases"])
    for page in (run / "report/en").rglob("*.html"):
        assert not untranslated_english_narrative(page.read_text(), source_text), page
    for case in dataset["cases"]:
        assert case["task_prompt"] in (run / f"report/en/datasets/{case['id']}.html").read_text()


def test_release_translation_uses_each_reports_records_without_global_state(tmp_path):
    from harness.web_fetch.report_i18n import TEXT, write_english_report
    title, summary = "独立版本标题", "独立版本说明"
    for edition in ("First", "Second"):
        root = tmp_path / edition
        root.mkdir()
        (root / "index.html").write_text(f'<html lang="zh-CN"><head></head><body><h1>{title}</h1><p>{summary}</p></body></html>')
        history = {"releases": [{"id": "example", "title": title, "summary": summary, "changes": [],
                                "i18n": {"en": {"title": edition, "summary": edition + " release", "changes": []}}}]}
        path = root / "report-changes.json"
        path.write_text(json.dumps(history))
        original = path.read_bytes()
        write_english_report(root)
        english = (root / "en/index.html").read_text()
        assert f'<h1>{edition}</h1>' in english
        assert f'<p>{edition} release</p>' in english
        assert path.read_bytes() == original
    assert title not in TEXT and summary not in TEXT
