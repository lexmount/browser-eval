"""Catalog-driven messages do not require a branch for each language."""
import json

import pytest

from harness.reporting.locales import catalog_text, translate


def test_messages_select_languages_and_preserve_model_values(tmp_path):
    path = tmp_path / "messages.json"
    path.write_text(json.dumps({
        "en": {"count": "{passed}/{total} tasks", "columns": ["Task", "Result"]},
        "zh": {"count": "{passed}/{total} 题", "columns": ["题目", "结果"]},
        "fr": {"count": "{passed}/{total} tâches", "columns": ["Tâche", "Résultat"]},
    }), encoding="utf-8")
    assert translate(path, "count", "en", passed=2, total=3) == "2/3 tasks"
    assert translate(path, "count", "zh", passed=2, total=3) == "2/3 题"
    assert translate(path, "count", "fr", passed=2, total=3) == "2/3 tâches"
    assert translate(path, "columns", "fr") == ["Tâche", "Résultat"]
    with pytest.raises(ValueError, match="de/count"):
        translate(path, "count", "de", passed=2, total=3)
    with pytest.raises(ValueError, match="en/unknown"):
        translate(path, "unknown", "en")
    with pytest.raises(KeyError):
        translate(path, "count", "en", passed=2)


def test_english_source_text_needs_no_catalog_entry(tmp_path):
    path = tmp_path / "messages.json"
    path.write_text(json.dumps({"zh": {"result.count": "{passed} 题"}}), encoding="utf-8")
    assert catalog_text(path, "en")("result.count", "{passed} tasks", passed=2) == "2 tasks"
    assert catalog_text(path, "zh")("result.count", "{passed} tasks", passed=2) == "2 题"
    with pytest.raises(ValueError, match="zh/result.missing"):
        catalog_text(path, "zh")("result.missing", "Absent")
    with pytest.raises(KeyError):
        catalog_text(path, "en")("result.count", "{passed} tasks")
