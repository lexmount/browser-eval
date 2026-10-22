"""Implementation language and localized content have different boundaries."""
from pathlib import Path

from harness.reporting.source_policy import check_source_languages


def test_repository_implementation_language():
    assert check_source_languages(Path(__file__).resolve().parents[1]) == []


def test_comments_identifiers_and_docstrings_fail_but_source_text_survives(tmp_path):
    source = tmp_path / 'harness'
    source.mkdir()
    path = source / 'adapter.py'
    path.write_text('"""维护说明"""\n# 实现注释\n结果 = "原文引述"\n', encoding='utf-8')
    findings = check_source_languages(tmp_path)
    assert len(findings) == 3
    path.write_text('"""Read source evidence."""\n# Preserve the original text.\nvalue = "原文引述"\n')
    assert check_source_languages(tmp_path) == []


def test_catalog_renderer_rejects_inline_messages_without_banning_chinese_catalogs(tmp_path):
    source = tmp_path / 'scripts/moli_report'
    source.mkdir(parents=True)
    (source / 'app.js').write_text('const label = "结果";')
    assert len(check_source_languages(tmp_path)) == 1
    (source / 'app.js').write_text('const label = messages.result;')
    (source / 'messages.json').write_text('{"zh": {"result": "结果"}}')
    assert check_source_languages(tmp_path) == []


def test_report_instances_stay_external_but_task_definitions_remain_versioned(tmp_path):
    inputs = tmp_path / "evaluation/example"
    inputs.mkdir(parents=True)
    (inputs / "tasks.json").write_text('{"cases": []}')
    assert check_source_languages(tmp_path) == []
    (inputs / "current-report.json").write_text('{}')
    assert check_source_languages(tmp_path) == [
        "evaluation/example/current-report.json: keep report instances outside Git"]
