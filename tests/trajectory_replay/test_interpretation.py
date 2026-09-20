import base64
import hashlib
import json

import pytest
from bs4 import BeautifulSoup

from harness.trajectory_replay.interpretation import render_interpretation, standalone_document
from scripts.check_webarena_replay_report import _check_interpretation, _text_without_verified_embedded_images


def build(tmp_path):
    payload = b"\x89PNG\r\n\x1a\nsynthetic-test"
    for lang in ("zh", "en"):
        (tmp_path / f"example-{lang}.png").write_bytes(payload)
    article = {"schema": "lex_replay_interpretation/1", "results_sha256": "cohort",
               "title": {"zh": "标题", "en": "Title"}, "intro": {"zh": "说明", "en": "Intro"},
               "sections": [{"id": "mechanism", "title": {"zh": "机制", "en": "Mechanism"},
                             "paragraphs": [{"zh": "<script>原文</script>", "en": "<script>Evidence</script>"}],
                             "figure": "example", "caption": {"zh": "示意", "en": "Illustration"}}]}
    source, audit = tmp_path / "article.json", tmp_path / "audit.json"
    source.write_text(json.dumps(article))
    audit.write_text(json.dumps({"bindings": {"results_sha256": "cohort"}}))
    for lang in ("zh", "en"):
        args = dict(source=source, contract_audit=audit, results_sha256="cohort", methods={},
                    rows=[], lang=lang, image_dir=tmp_path, image_prefix="assets/")
        title, body = render_interpretation(**args)
        root = tmp_path if lang == "zh" else tmp_path / "en"
        root.mkdir(exist_ok=True)
        (root / "interpretation.html").write_text('<a href="article.html" download>Download</a>' + body)
        _, offline = render_interpretation(**args, standalone=True, report_url="https://example.test/report")
        (root / "article.html").write_text(standalone_document(title, offline, lang, ""))
    return payload


def test_article_views_share_content_and_escape_source(tmp_path):
    build(tmp_path)
    _check_interpretation(tmp_path)
    assert not BeautifulSoup((tmp_path / "article.html").read_text(), "html.parser").select("script")


@pytest.mark.parametrize("mutation", ["script", "missing_image", "drift", "no_download", "localization"])
def test_article_release_gate_rejects_broken_views(tmp_path, mutation):
    build(tmp_path)
    path = tmp_path / ("interpretation.html" if mutation == "no_download" else "article.html")
    text = path.read_text()
    if mutation == "script": text += "<script>alert(1)</script>"
    if mutation == "missing_image": text = text.replace("data:image/png;base64,", "external.png?")
    if mutation == "drift": text = text.replace("标题", "不一致")
    if mutation == "no_download": text = text.replace("download", "data-removed")
    if mutation == "localization":
        path = tmp_path / "en/interpretation.html"
        text = path.read_text().replace('id="mechanism"', 'id="other"')
    path.write_text(text)
    with pytest.raises(ValueError): _check_interpretation(tmp_path)


def test_secret_scan_only_excludes_exact_shipped_png_bytes():
    image = b"\x89PNG\r\n\x1a\n/home/not-a-text-path"
    text = '<img src="data:image/png;base64,' + base64.b64encode(image).decode() + '"><p>sk-still-detect-me</p>'
    result = _text_without_verified_embedded_images(text, {hashlib.sha256(image).hexdigest()})
    assert "sk-still-detect-me" in result
    assert "base64" not in result
    with pytest.raises(ValueError): _text_without_verified_embedded_images(text, set())
