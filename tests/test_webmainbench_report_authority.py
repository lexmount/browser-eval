"""Release commands read explicitly selected authority outside the checkout."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.report_languages import write_language_contract
from harness.web_fetch.webmainbench_report_check import check, check_sample_report

ROOT = Path(__file__).resolve().parents[1]


def document(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "command", ["check_webmainbench_report.py", "check_webmainbench_full_report.py"]
)
def test_release_commands_expose_authority_and_reject_missing_authority(tmp_path, command):
    script = ROOT / "scripts" / command
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"], cwd=tmp_path, capture_output=True, text=True
    )
    assert help_result.returncode == 0
    assert "--authority-root" in help_result.stdout
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), "--authority-root", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "release authority is missing" in result.stderr


def test_sample_uses_selected_profile_before_releasing_without_declared_diagnostic(tmp_path):
    authority, report = tmp_path / "authority", tmp_path / "report"
    report.mkdir()
    write_language_contract(report, include_english=False)
    document(report, "dataset.json", {"cases": []})
    (report / "results.jsonl").write_text("")
    document(authority, "evaluation/current-report.json",
             {"latency_diagnostic": {"required": True}})
    with pytest.raises(ValueError, match="lacks the declared latency diagnostic"):
        check_sample_report(report, authority_root=authority)


def test_sample_uses_selected_history_after_profile_validation(tmp_path):
    authority, report = tmp_path / "authority", tmp_path / "report"
    report.mkdir()
    write_language_contract(report, include_english=False)
    document(report, "dataset.json", {"cases": []})
    (report / "results.jsonl").write_text("")
    document(authority, "evaluation/current-report.json", {})
    release = {
        "id": "fixture", "date": "2026-01-01", "title": "合成变更",
        "summary": "合成说明", "changes": [],
        "comparison_kinds": ["webmainbench_frozen_html_to_markdown"],
        "i18n": {"en": {"title": "Fixture", "summary": "Fixture", "changes": []}},
    }
    document(authority, "research/report-changes.json", {"releases": [release]})
    history = document(report, "report-changes.json", {"releases": [{"id": "extra"}]})
    with pytest.raises(ValueError, match="release history differs"):
        check_sample_report(report, authority_root=authority)
    history.write_text(json.dumps({"releases": [release]}))
    with pytest.raises(FileNotFoundError, match="changes/index.html"):
        check_sample_report(report, authority_root=authority)


def test_complete_report_checks_selected_profile_and_target_binding(tmp_path):
    authority, report = tmp_path / "authority", tmp_path / "report"
    report.mkdir()
    profile = document(authority, "evaluation/webmainbench-full/current-report.json",
                       {"providers": ["custom"], "case_count": 1})
    document(authority, "evaluation/webmainbench-full/moli-variants.json", {"variants": []})
    target = {
        "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        "variant_profile_sha256": {}, "expected_remote_version": "ver_fixture",
        "host": "https://example.invalid", "share_url": "https://example.invalid/v/fixture",
        "site_slug": "fixture",
    }
    document(authority, "evaluation/webmainbench-full/publication-target.json", target)
    document(report, "method-binding.json",
             {"schema_version": 2, "kind": "webmainbench_method_cohort_binding"})
    with pytest.raises(ValueError, match="scoring-correction.json missing"):
        check(report, authority_root=authority)
    profile.write_text('{"providers": ["changed"], "case_count": 1}')
    with pytest.raises(ValueError, match="publication target is not bound"):
        check(report, authority_root=authority)
