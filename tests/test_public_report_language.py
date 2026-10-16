"""Language choice at the public run/rebuild boundary, independent of scoring."""
import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_media_family_guides_preserve_numbers_and_translate_every_explanation():
    from collections import Counter
    from pathlib import PurePosixPath
    from harness.web_fetch.report_components import examples, task_contract_guide, task_pass_guide
    from harness.web_fetch.report_i18n import localize_html
    from harness.web_fetch.report_check import EnglishNarrative, PageShape

    source = task_pass_guide('web_media') + task_pass_guide('media_file') + task_contract_guide() + examples()
    english = localize_html(source, PurePosixPath('methodology/index.html'))
    zh_shape, en_shape = PageShape(), PageShape()
    zh_shape.feed(source)
    en_shape.feed(english)
    assert Counter(zh_shape.numbers) == Counter(en_shape.numbers)
    narrative = EnglishNarrative({'中文'})
    narrative.feed(english)
    assert not narrative.untranslated


def test_registered_interfaces_and_pricing_have_complete_english(tmp_path):
    from collections import Counter
    from pathlib import PurePosixPath
    from harness.web_fetch.report import table, cell, link, esc
    from harness.web_fetch.report_interfaces import SPECS, render_interfaces
    from harness.web_fetch.report_pricing import render_pricing
    from harness.web_fetch.report_i18n import localize_html
    from harness.web_fetch.report_check import EnglishNarrative, PageShape

    source = render_pricing(tmp_path, list(SPECS), table=table, cell=cell, link=link, esc=esc)
    source += render_interfaces(tmp_path, [])
    english = localize_html(source, PurePosixPath('adoption/index.html'))
    zh_shape, en_shape = PageShape(), PageShape()
    zh_shape.feed(source)
    en_shape.feed(english)
    assert Counter(zh_shape.numbers) == Counter(en_shape.numbers)
    narrative = EnglishNarrative({'中文'})
    narrative.feed(english)
    assert not narrative.untranslated


def test_scoped_english_refresh_preserves_unselected_pages_and_rejects_escaping_paths(tmp_path, monkeypatch):
    from harness.web_fetch.report_i18n import write_english_report
    (tmp_path/'index.html').write_text('<html lang="zh-CN"><body><h1>评测方法</h1></body></html>')
    (tmp_path/'en/attempts').mkdir(parents=True)
    saved = tmp_path/'en/attempts/saved.html'
    original = b'Exact previously generated result detail'
    saved.write_bytes(original)
    assert write_english_report(tmp_path, source_routes=['index.html']) == 1
    assert 'Methodology' in (tmp_path/'en/index.html').read_text()
    assert saved.read_bytes() == original
    monkeypatch.chdir(tmp_path)
    assert write_english_report(Path('.'), source_routes=['index.html']) == 1
    for route in ('../outside.html', '/outside.html', 'en/index.html', 'results.jsonl'):
        with pytest.raises(ValueError, match='relative Chinese HTML'):
            write_english_report(tmp_path, source_routes=[route])
    assert saved.read_bytes() == original
    (tmp_path/'alias').mkdir()
    (tmp_path/'alias/index.html').write_text('<html></html>')
    (tmp_path/'outside').mkdir()
    (tmp_path/'en/alias').symlink_to(tmp_path/'outside', target_is_directory=True)
    with pytest.raises(ValueError, match='Symlinked evidence'):
        write_english_report(tmp_path, source_routes=['alias/index.html'])
    assert not (tmp_path/'outside/index.html').exists()


@pytest.mark.parametrize("mode", ["--diagnostic-cohorts", "--webmainbench-full"])
@pytest.mark.parametrize("language", [None, "zh", "en"])
@pytest.mark.parametrize("saved", [False, True])
def test_chinese_only_commands_preserve_omitted_language(tmp_path, monkeypatch, mode, language, saved):
    from harness.web_fetch import report_build
    output = tmp_path / "report"
    if saved:
        output.mkdir()
        (output / "report-languages.json").write_text(json.dumps({"languages": ["zh"], "primary_language": "zh"}))
    calls = []
    builder = "build_diagnostic_cohorts" if mode == "--diagnostic-cohorts" else "build_webmainbench_full_report"
    monkeypatch.setattr(report_build, builder, lambda *args, **kwargs: calls.append((args, kwargs)) or output / "index.html")
    script = str(ROOT / "scripts/build_report.py")
    argv = [script, mode, "--evidence", str(tmp_path / "evidence"), "--output", str(output)]
    if language:
        argv += ["--language", language]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(script, run_name="__main__")
    assert result.value.code == (2 if language == "en" else 0)
    assert len(calls) == (0 if language == "en" else 1)


@pytest.mark.parametrize("language", [None, "zh"])
def test_historical_diagnostic_command_builds_actual_report(tmp_path, language):
    from tests.test_report_templates import diagnostic_example
    evidence = tmp_path / "evidence"
    diagnostic_example(evidence)
    output = tmp_path / "report"
    command = [sys.executable, str(ROOT / "scripts/build_report.py"), "--diagnostic-cohorts",
               "--evidence", str(evidence), "--output", str(output)]
    if language:
        command += ["--language", language]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((output / "report-manifest.json").read_text())["languages"] == ["zh"]
    original = (output / "index.html").read_bytes()
    english = subprocess.run(command + ["--language", "en"], cwd=ROOT, capture_output=True, text=True)
    assert english.returncode == 2
    assert "supports only zh" in english.stderr
    assert (output / "index.html").read_bytes() == original
    # Historical diagnostics deliberately require a new directory, never overwrite.
    again = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert again.returncode != 0
    assert "new output" in again.stderr
    assert (output / "index.html").read_bytes() == original


@pytest.mark.parametrize("target", ["report", "popular-sites-report", "browser-use-report", "trajectory-replay-report", "public-benchmark-report", "webfetch-diagnostic-report"])
@pytest.mark.parametrize("language", [None, "en", "zh"])
def test_make_language_override_is_only_explicit(tmp_path, monkeypatch, target, language):
    make = shutil.which("make")
    if not make or subprocess.run([make, "--version"], capture_output=True).returncode:
        pytest.skip("Make is not installed")
    args = [make, "--no-print-directory", "-n", "-f", str(ROOT / "Makefile"), target, "EVIDENCE=" + str(tmp_path), "OUTPUT=" + str(tmp_path / "report")]
    if language:
        args += ["REPORT_LANGUAGE=" + language]
    env = {key: value for key, value in os.environ.items() if key not in {"REPORT_LANGUAGE", "MAKEFLAGS"}}
    result = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    command = next(shlex.split(line) for line in result.stdout.splitlines() if "scripts/build_" in line)
    if language:
        assert command[command.index("--language") + 1] == language
    else:
        assert "--language" not in command
    if target == "report":
        # Exercise the expanded public command with a saved Chinese selection.
        # Evidence validation/rendering are covered by the real CLI scenarios below.
        from unittest.mock import patch
        from harness.web_fetch import report_build, storage
        output = tmp_path / "reports/webfetch"
        output.mkdir(parents=True)
        (output / "report-languages.json").write_text(json.dumps({"primary_language": "zh", "languages": ["zh"]}))
        command = command[command.index("scripts/build_report.py"):]
        command[0] = str(ROOT / command[0])
        captured = []
        monkeypatch.chdir(tmp_path)
        with patch.object(sys, "argv", command), \
             patch.object(storage, "require_canonical_inputs"), patch.object(storage, "require_current_report_profile"), \
             patch.object(storage, "require_evidence", return_value=tmp_path), \
             patch.object(report_build, "build_report", side_effect=lambda *a, **kw: captured.append(kw) or output / "index.html"):
            runpy.run_path(command[0], run_name="__main__")
        assert captured[0]["language"] == (language or "zh")



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
    untranslated = {
        str(page.relative_to(run / "report/en")): fragments
        for page in (run / "report/en").rglob("*.html")
        if (fragments := untranslated_english_narrative(page.read_text(), source_text))
    }
    assert not untranslated, untranslated
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
