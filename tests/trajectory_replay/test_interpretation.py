import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from harness.trajectory_replay.interpretation import render_interpretation, standalone_document
from harness.trajectory_replay.webarena_report import _add_interpretation_navigation, _shell
from harness.trajectory_replay.webarena_report_check import (
    _check_interpretation,
    _text_without_verified_embedded_images,
)


def test_interpretation_is_reachable_from_summary_and_nested_evidence(tmp_path):
    for lang in ("zh", "en"):
        root = tmp_path if lang == "zh" else tmp_path / "en"
        for route in ("index.html", "interpretation.html", "result/example.html"):
            path = root / route
            path.parent.mkdir(parents=True, exist_ok=True)
            active = "interpretation" if route == "interpretation.html" else "results"
            path.write_text(_shell(lang, active, "Title", "<p>Evidence</p>",
                                   depth=route.count("/"), current_route=route))
        _add_interpretation_navigation(root, lang)
        for route in ("index.html", "interpretation.html", "result/example.html"):
            path = root / route
            page = BeautifulSoup(path.read_text(), "html.parser")
            links = page.select('header nav a[data-report-interpretation]')
            assert len(links) == 1
            target = (root / "interpretation.html").resolve()
            assert (path.parent / links[0]["href"]).resolve() == target
            assert (links[0].get("aria-current") == "page") == (route == "interpretation.html")
            assert links[0].text == ("报告解读" if lang == "zh" else "Interpretation")


def build(tmp_path, specimen=None, sources=None, *,
          report_url="https://example.test/report", failures=False):
    payload = b"\x89PNG\r\n\x1a\nsynthetic-test"
    for lang in ("zh", "en"):
        (tmp_path / f"example-{lang}.png").write_bytes(payload)
    article = {
        "schema": "lex_replay_interpretation/1",
        "results_sha256": "cohort",
        "title": {"zh": "标题", "en": "Title"},
        "intro": {"zh": "说明", "en": "Intro"},
        "sections": [
            {
                "id": "mechanism",
                "title": {"zh": "机制", "en": "Mechanism"},
                "paragraphs": [{"zh": "<script>原文</script>", "en": "<script>Evidence</script>"}],
                "figure": "example",
                "caption": {"zh": "示意", "en": "Illustration"},
            }
        ],
    }
    if specimen is not None:
        article["sections"][0]["specimen"] = specimen
    if sources is not None:
        article["sections"][0]["sources"] = sources
    if failures:
        article["sections"][0]["data"] = "failure_matrix"
    source, audit = tmp_path / "article.json", tmp_path / "audit.json"
    source.write_text(json.dumps(article))
    audit.write_text(json.dumps({"bindings": {"results_sha256": "cohort"}}))
    for lang in ("zh", "en"):
        args = dict(source=source, contract_audit=audit, results_sha256="cohort",
                    methods={"browser": {"label": "浏览器"}} if failures else {},
                    rows=[{"participant_id": "browser", "task_id": 1, "status": "fail",
                           "cause_code": "recorded_referer_mismatch",
                           "cause_zh": "请求头记录", "cause_en": "Headers"}] if failures else [],
                    lang=lang, image_dir=tmp_path, image_prefix="assets/")
        title, body = render_interpretation(**args)
        root = tmp_path if lang == "zh" else tmp_path / "en"
        root.mkdir(exist_ok=True)
        (root / "interpretation.html").write_text(
            '<a href="article.html" download>Download</a>' + body
        )
        _, offline = render_interpretation(**args, standalone=True, report_url=report_url)
        (root / "article.html").write_text(standalone_document(title, offline, lang, ""))
    return payload


def test_downloaded_article_links_keep_share_access_and_failure_filters(tmp_path):
    url = "https://example.test/api/preview/site/interpretation.html?__artifact_share=public-share"
    build(tmp_path, report_url=url, failures=True)
    page = BeautifulSoup((tmp_path / "article.html").read_text(), "html.parser")
    link = page.select_one("[data-category] a")
    target = urlsplit(link["href"])
    assert target.path == "/api/preview/site/results.html"
    assert parse_qs(target.query) == {
        "__artifact_share": ["public-share"], "participant": ["browser"],
        "status": ["fail"], "cause": ["请求头记录"],
    }
    assert target.fragment == "result-inventory"
    image = urlsplit(page.select_one("figure a[download]")["href"])
    assert image.path == "/api/preview/site/assets/example-zh.png"
    assert parse_qs(image.query) == {"__artifact_share": ["public-share"]}
    _check_interpretation(tmp_path)


@pytest.mark.parametrize("mutation", ["share", "filter", "route"])
def test_release_rejects_broken_downloaded_failure_links(tmp_path, mutation):
    url = "https://example.test/api/preview/site/interpretation.html?__artifact_share=public-share"
    build(tmp_path, report_url=url, failures=True)
    path = tmp_path / "article.html"
    page = BeautifulSoup(path.read_text(), "html.parser")
    link = page.select_one("[data-category] a")
    if mutation == "share":
        link["href"] = link["href"].replace("__artifact_share=public-share&", "")
    elif mutation == "filter":
        link["href"] = link["href"].replace("status=fail", "status=pass")
    else:
        link["href"] = link["href"].replace("/api/preview/site/", "/v/public-share/")
    path.write_text(str(page))
    with pytest.raises(ValueError, match="standalone article failure link"):
        _check_interpretation(tmp_path)


def test_article_views_share_content_and_escape_source(tmp_path):
    build(tmp_path)
    _check_interpretation(tmp_path)
    offline = (tmp_path / "article.html").read_text()
    assert "<head>" in offline and "</head><body>" in offline
    assert offline.count("data:image/png;base64,") == 1
    assert not BeautifulSoup(offline, "html.parser").select("script")


@pytest.mark.parametrize("mutation", ["missing", "wrong_depth", "duplicate", "wrong_current"])
def test_release_rejects_inaccessible_interpretation_navigation(tmp_path, mutation):
    build(tmp_path)
    path = tmp_path / "result/example.html"
    path.parent.mkdir()
    path.write_text(_shell("zh", "results", "结果", "<p>证据</p>", depth=1))
    _add_interpretation_navigation(tmp_path, "zh")
    _check_interpretation(tmp_path)
    page = BeautifulSoup(path.read_text(), "html.parser")
    link = page.select_one("a[data-report-interpretation]")
    if mutation == "missing":
        link.decompose()
    elif mutation == "wrong_depth":
        link["href"] = "interpretation.html"
    elif mutation == "duplicate":
        page.select_one("nav").append(BeautifulSoup(str(link), "html.parser"))
    else:
        link["aria-current"] = "page"
    path.write_text(str(page))
    with pytest.raises(ValueError, match="interpretation navigation"):
        _check_interpretation(tmp_path)


def test_sources_are_localized_and_preserved_in_offline_article(tmp_path):
    build(tmp_path, sources=[{"url": "https://example.test/source?x=1&y=2",
                              "label": {"zh": "<b>源码</b>", "en": "<b>Source</b>"}}])
    _check_interpretation(tmp_path)
    for prefix, label in (("", "<b>源码</b>"), ("en/", "<b>Source</b>")):
        page = BeautifulSoup((tmp_path / f"{prefix}article.html").read_text(), "html.parser")
        link = page.select_one(".article-sources a")
        assert link.text == label
        assert link["href"] == "https://example.test/source?x=1&y=2"
        assert not link.select("b")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///private/source", "https:///missing",
                                  "https://user:secret@example.test/source"])
def test_article_rejects_unsafe_source_links(tmp_path, url):
    with pytest.raises(ValueError, match="HTTPS"):
        build(tmp_path, sources=[{"url": url, "label": {"zh": "源码", "en": "Source"}}])


def test_article_rejects_unlocalized_source_links(tmp_path):
    with pytest.raises(ValueError, match="both languages"):
        build(tmp_path, sources=[{"url": "https://example.test/source", "label": {"zh": "源码"}}])


def test_specimen_preserves_values_and_localizes_explanations(tmp_path):
    build(tmp_path, {"headers": [{"zh": "事件", "en": "Event"}, "Moli"],
                     "rows": [[{"zh": "<b>提交</b>", "en": "<b>Submit</b>"}, 1]],
                     "caption": {"zh": "独立实验", "en": "Isolated fixture"}})
    _check_interpretation(tmp_path)
    for prefix, label in (("", "事件"), ("en/", "Event")):
        page = BeautifulSoup((tmp_path / f"{prefix}article.html").read_text(), "html.parser")
        assert page.select_one(".article-specimen th").text == label
        assert page.select(".article-specimen td")[-1].text == "1"
        assert not page.select(".article-specimen b")
        assert page.select_one(".diagram-scroll")["tabindex"] == "0"


@pytest.mark.parametrize("specimen", [
    {"headers": ["A", "B"], "rows": [[1]], "caption": {"zh": "说明", "en": "Scope"}},
    {"headers": [{"zh": "缺少英文"}], "rows": [[1]], "caption": {"zh": "说明", "en": "Scope"}},
])
def test_specimen_rejects_ragged_or_unlocalized_content(tmp_path, specimen):
    with pytest.raises(ValueError):
        build(tmp_path, specimen)


@pytest.mark.parametrize(
    "mutation", ["script", "missing_image", "drift", "no_download", "localization"]
)
def test_article_release_gate_rejects_broken_views(tmp_path, mutation):
    build(tmp_path)
    path = tmp_path / ("interpretation.html" if mutation == "no_download" else "article.html")
    text = path.read_text()
    if mutation == "script":
        text += "<script>alert(1)</script>"
    if mutation == "missing_image":
        text = text.replace("data:image/png;base64,", "external.png?")
    if mutation == "drift":
        text = text.replace("标题", "不一致")
    if mutation == "no_download":
        text = text.replace("download", "data-removed")
    if mutation == "localization":
        path = tmp_path / "en/interpretation.html"
        text = path.read_text().replace('id="mechanism"', 'id="other"')
    path.write_text(text)
    with pytest.raises(ValueError):
        _check_interpretation(tmp_path)


def test_secret_scan_only_excludes_exact_shipped_png_bytes():
    image = b"\x89PNG\r\n\x1a\n/home/not-a-text-path"
    text = (
        '<img src="data:image/png;base64,'
        + base64.b64encode(image).decode()
        + '"><p>sk-still-detect-me</p>'
    )
    result = _text_without_verified_embedded_images(text, {hashlib.sha256(image).hexdigest()})
    assert "sk-still-detect-me" in result
    assert "base64" not in result
    with pytest.raises(ValueError):
        _text_without_verified_embedded_images(text, set())


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_outcome_chart_uses_each_selected_method_population(tmp_path, lang):
    source = tmp_path / "article.json"
    source.write_text(
        json.dumps(
            {
                "schema": "lex_replay_interpretation/1",
                "results_sha256": "cohort",
                "title": {"zh": "结果", "en": "Results"},
                "intro": {"zh": "任务比较", "en": "Task comparison"},
                "sections": [
                    {
                        "id": "outcomes",
                        "title": {"zh": "结果", "en": "Outcomes"},
                        "data": "outcomes",
                        "paragraphs": [],
                    }
                ],
            }
        )
    )
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"bindings": {"results_sha256": "cohort"}}))
    rows = [
        {"participant_id": method, "task_id": index, "status": status}
        for method, states in {
            "first": ["pass", "fail", "pass"],
            "second": ["pass", "fail"],
        }.items()
        for index, status in enumerate(states)
    ]
    _, body = render_interpretation(
        source=source,
        contract_audit=audit,
        results_sha256="cohort",
        methods={"first": {"label": "First"}, "second": {"label": "Second"}},
        rows=rows,
        lang=lang,
        image_dir=tmp_path,
        image_prefix="assets/",
    )
    assert "2/3" in body and "1/2" in body
    assert "812" not in body
