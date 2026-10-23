"""Search report language selection, available editions and unavailable entry behavior."""
import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search.example import example
from harness.web_search.report import build, check, main


@pytest.fixture
def report(tmp_path):
    example(tmp_path)
    return tmp_path / "report", tmp_path / "fixture"


def test_chinese_only_report_links_to_unavailable_english_entry(report):
    output, _ = report
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["languages"] == ["zh"]
    assert manifest["language_notices"] == ["en/index.html"]
    notice = BeautifulSoup((output / "en/index.html").read_text(), "html.parser")
    assert notice.select_one("[data-english-not-generated]")
    assert notice.select_one('a[href="../index.html"]')
    for route in manifest["routes"]:
        assert BeautifulSoup((output / route).read_text(), "html.parser").select_one('nav a[href="en/index.html"]')
    assert check(output)


@pytest.mark.parametrize("damage", ["missing", "bad_return", "stale_translation"])
def test_chinese_release_rejects_broken_notice_or_stale_english(report, damage):
    output, _ = report
    notice = output / "en/index.html"
    if damage == "missing":
        notice.unlink()
    elif damage == "bad_return":
        notice.write_text(notice.read_text().replace('href="../index.html"', 'href="/"'))
    else:
        (output / "en/quality.html").write_text("Stale translated report")
    with pytest.raises(ValueError, match="notice|stale English"):
        check(output)


def test_measured_rebuild_preserves_language_and_explicit_english_generates_chinese(report, monkeypatch):
    output, source = report
    monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "en")
    main(["--source", str(source), "--output", str(output)])
    assert json.loads((output / "manifest.json").read_text())["primary_language"] == "zh"
    main(["--source", str(source), "--output", str(output), "--language", "en"])
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["primary_language"] == "en"
    assert set(manifest["languages"]) == {"en", "zh"}
    assert manifest["language_notices"] == []
    assert BeautifulSoup((output / "index.html").read_text(), "html.parser").html["lang"] == "zh"
    assert BeautifulSoup((output / "en/index.html").read_text(), "html.parser").html["lang"] == "en"
    assert check(output)
    build(output, source=source, language="zh")
    assert not list((output / "zh").rglob("*.html"))
    assert {p.name for p in (output / "en").rglob("*.html")} == {"index.html"}
    assert check(output)


def test_chinese_notice_is_preserved_in_delivery_and_verified(report, tmp_path):
    from harness.web_search.delivery import export, verify
    output, _ = report
    destination = tmp_path / "delivery"
    export(output, destination)
    assert verify(output, destination)
    notice = destination / "en/index.html"
    assert "data-english-not-generated" in notice.read_text()
    notice.write_text("Broken English entry")
    with pytest.raises(ValueError, match="Delivery page changed"):
        verify(output, destination)
