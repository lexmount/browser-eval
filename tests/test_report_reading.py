import hashlib
import json

import pytest

from harness.web_fetch.report_reading import copy_archived_reading, render_archived_reading


def test_historical_article_preserves_source_data_and_language(tmp_path):
    source = tmp_path / "evidence"
    source.mkdir()
    languages = {}
    for language, heading in [("zh", "旧文章"), ("en", "Older article")]:
        path = source / (language + ".txt")
        path.write_text(f"<html><style>.essay {{color: black}}</style><main><h1>{heading}</h1><p>94/100</p></main></html>")
        languages[language] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (source / "archived-reading.json").write_text(json.dumps({"languages": languages}))
    output = tmp_path / "report"
    output.mkdir()
    copy_archived_reading(source, output)
    for language, heading in [("zh", "旧文章"), ("en", "Older article")]:
        rendered = render_archived_reading(output, language)
        assert heading in rendered
        assert "94/100" in rendered
        assert "current benchmark results" in rendered if language == "en" else "本轮评测结果" in rendered
        assert "<style>" in rendered
    (output / "en.txt").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        render_archived_reading(output, "en")


def test_article_is_optional(tmp_path):
    assert render_archived_reading(tmp_path) is None


def test_english_chrome_preserves_raw_evidence_and_filter_keys():
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html
    raw = '<pre data-original="true">验证码 &amp; 原始返回 26/26</pre>'
    source = '<html lang="zh-CN"><body><input aria-label="搜索" placeholder="搜索案例、方法或原因"><p data-failure-category="规则不满足">任务规则未满足</p>' + raw + '</body></html>'
    english = localize_html(source, PurePosixPath('analysis/index.html'))
    assert raw in english
    assert 'data-failure-category="规则不满足"' in english
    assert 'aria-label="Search"' in english
    assert 'Task requirements not met' in english


def test_bound_report_translations_are_external_and_tamper_evident(tmp_path):
    from harness.web_fetch.report_reading import archived_translations
    source = tmp_path / "source"
    source.mkdir()
    translations = source / "translations.json"
    translations.write_text(json.dumps({"text": {"核验说明": "Review notes"}}))
    manifest = {"languages": {}, "translations": {
        "path": "translations.json", "sha256": hashlib.sha256(translations.read_bytes()).hexdigest()}}
    (source / "archived-reading.json").write_text(json.dumps(manifest))
    output = tmp_path / "output"
    output.mkdir()
    copy_archived_reading(source, output)
    assert archived_translations(output) == {"核验说明": "Review notes"}
    (output / "translations.json").write_text("changed")
    with pytest.raises(ValueError, match="translations hash mismatch"):
        archived_translations(output)
