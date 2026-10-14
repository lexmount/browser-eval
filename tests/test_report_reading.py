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
        assert "<style>" not in rendered
    (output / "en.txt").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        render_archived_reading(output, "en")


def test_article_is_optional(tmp_path):
    assert render_archived_reading(tmp_path) is None


def test_archived_html_cannot_import_active_content_or_global_styles():
    from harness.web_fetch.report_reading import sanitize_article_html
    from bs4 import BeautifulSoup
    raw = '<style>body{display:none}</style><script>alert(1)</script><iframe src="https://example.test"></iframe><form action="https://example.test"><input></form><img src="javascript:alert(1)" onerror="alert(1)"><a href="data:text/html,x" onclick="alert(1)">Read</a><p id="rows">Saved content</p>'
    safe = sanitize_article_html(raw)
    soup = BeautifulSoup(safe, 'html.parser')
    assert not soup.find(['style', 'script', 'iframe', 'form', 'input'])
    assert not soup.img.get('src') and not soup.img.get('onerror')
    assert not soup.a.get('href') and not soup.a.get('onclick')
    assert soup.p['id'] == 'archived-rows'
    assert 'Saved content' in safe
    import base64
    png='data:image/png;base64,'+base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()
    preserved=BeautifulSoup(sanitize_article_html('<img src="'+png+'">'),'html.parser')
    assert preserved.img['src']==png
    rejected=BeautifulSoup(sanitize_article_html('<img src="data:image/svg+xml;base64,PHN2Zz4=">'),'html.parser')
    assert not rejected.img.get('src')


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


def test_article_translation_cannot_override_other_routes_or_remove_status(tmp_path):
    from pathlib import PurePosixPath
    from harness.web_fetch.report_i18n import localize_html, write_english_report
    languages = {}
    for language in ('zh', 'en'):
        path = tmp_path / (language + '.txt')
        path.write_text('<main><p>核验记录</p></main>')
        languages[language] = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    translation = tmp_path / 'translations.json'
    translation.write_text(json.dumps({'text': {'核验记录': 'Archive-only label'}}))
    (tmp_path / 'archived-reading.json').write_text(json.dumps({'languages': languages,
        'translations': {'path': translation.name, 'sha256': hashlib.sha256(translation.read_bytes()).hexdigest()}}))
    (tmp_path / 'guide').mkdir()
    banner = '<div data-evidence-state="local_measurement">本地未认证诊断快照 · 不可用于排名</div>'
    article = render_archived_reading(tmp_path)
    (tmp_path / 'guide/index.html').write_text('<html lang="zh-CN"><main>'+banner+article+'</main></html>')
    root = '<html lang="zh-CN"><main><p>核验记录</p></main></html>'
    (tmp_path / 'index.html').write_text(root)
    write_english_report(tmp_path)
    assert (tmp_path / 'en/index.html').read_text() == localize_html(root, PurePosixPath('index.html'))
    guide = (tmp_path / 'en/guide/index.html').read_text()
    assert 'data-evidence-state="local_measurement"' in guide
    assert 'Archive-only label' in guide


def test_model_translation_is_scoped_and_escaped():
    from pathlib import PurePosixPath
    from harness.web_fetch.report import model_text
    from harness.web_fetch.report_i18n import localize_html
    raw='<p>核验记录</p>'+model_text('该结果的说明','Specific explanation <script>bad</script>')
    english=localize_html(raw,PurePosixPath('attempts/example.html'))
    assert 'Specific explanation &lt;script&gt;bad&lt;/script&gt;' in english
    assert '该结果的说明' not in english
    assert '<script>' not in english
