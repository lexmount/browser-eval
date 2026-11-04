"""Search report language selection, available editions and unavailable entry behavior."""
import json

import pytest
from bs4 import BeautifulSoup
from lexbench_websearch.scoring import evaluate

from harness.record_io import bytes_sha256
from harness.web_search import review
from harness.web_search.example import example
from harness.web_search.report import build, check, main
from tests.web_search.test_review_release import audited_fixture


@pytest.fixture
def report(tmp_path):
    example(tmp_path)
    return tmp_path / "report", tmp_path / "fixture"


def _tamper(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def _site_scoped_source(source):
    """Rebind the fixture's second query as a two-site query written in Chinese."""
    profile = json.loads((source / "profile.json").read_bytes())
    case = profile["cases"][1]
    del case["anchors"]
    case.update(domains=["one.invalid", "two.invalid"], query="\u5b98\u7f51\uff1a\u4e00\u3001\u4e8c\uff1b\u4e09")
    (source / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    rows = [json.loads(line) for line in (source / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["case_id"] == case["id"]:
            row["metrics"] = evaluate(case, row["results"])
    (source / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    receipt = json.loads((source / "receipt.json").read_bytes())
    receipt.update(profile_sha256=bytes_sha256((source / "profile.json").read_bytes()),
                   results_sha256=bytes_sha256((source / "results.jsonl").read_bytes()))
    (source / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def test_requested_sites_use_each_editions_separator_and_measured_queries_stay_verbatim(report):
    output, source = report
    _site_scoped_source(source)
    build(output, source=source, include_english=True)
    detail = "details/fixture-1-lexmount-r0.html"
    assert "one.invalid\u3001two.invalid" in (output / detail).read_text(encoding="utf-8")
    english = output / "en" / detail
    assert "one.invalid, two.invalid" in english.read_text(encoding="utf-8")
    assert "\u5b98\u7f51\uff1a\u4e00\u3001\u4e8c\uff1b\u4e09" in english.read_text(encoding="utf-8")
    assert check(output)
    _tamper(english, "one.invalid, two.invalid", "one.invalid\u3001two.invalid")
    with pytest.raises(ValueError, match="Chinese punctuation"):
        check(output)


def test_population_scopes_use_each_editions_separator(report, tmp_path):
    output, source = report
    profile = json.loads((source / "profile.json").read_bytes())
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"presentation": "comparison-report", "independent_benchmarks": True, "metric_populations": [
        {"id": case["id"], "benchmark_root": True, "label": {"en": case["id"], "zh": case["id"]}, "case_ids": [case["id"]]}
        for case in profile["cases"]
    ]}), encoding="utf-8")
    build(output, source=source, content=content, include_english=True)
    home = BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert "\uff1b" in home.select_one("[data-populations] > .population-note").get_text()
    english = output / "en/index.html"
    assert "fixture-0 1 queries; fixture-1 1 queries" in english.read_text(encoding="utf-8")
    assert check(output)
    _tamper(english, "fixture-0 1 queries; fixture-1", "fixture-0 1 queries\uff1bfixture-1")
    with pytest.raises(ValueError, match="Chinese punctuation"):
        check(output)


def test_review_findings_use_each_editions_separator(tmp_path):
    _, evidence = audited_fixture(tmp_path)
    path = tmp_path / "review.json"
    review.save(path, evidence)
    output = tmp_path / "released"
    build(output, source=tmp_path / "fixture", review=path, include_english=True)
    assert check(output)
    evidence["adjudications"] = {"q:dataset": {"resolution": "fixed"}}
    finding = "[data-independent-review] details > ul > li"
    summary = {zh: BeautifulSoup(review.render_summary(evidence, zh), "html.parser").select_one(finding).get_text() for zh in (True, False)}
    assert summary == {True: "\u5df2\u4fee\u6b63\uff1a1 \u6761\u53d1\u73b0", False: "Corrected: 1 findings"}
    _tamper(output / "en/review.html", "</summary><ul>", "</summary><ul><li>Corrected\uff1a1 findings</li>")
    with pytest.raises(ValueError, match="Chinese punctuation"):
        check(output)


def test_chinese_only_report_links_to_unavailable_english_entry(report):
    output, _ = report
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["languages"] == ["zh"]
    assert manifest["language_notices"] == ["en/index.html"]
    notice = BeautifulSoup((output / "en/index.html").read_text(encoding="utf-8"), "html.parser")
    assert notice.select_one("[data-english-not-generated]")
    assert notice.select_one('a[href="../index.html"]')
    for route in manifest["routes"]:
        assert BeautifulSoup((output / route).read_text(encoding="utf-8"), "html.parser").select_one('nav a[href="en/index.html"]')
    assert check(output)


@pytest.mark.parametrize("damage", ["missing", "bad_return", "stale_translation"])
def test_chinese_release_rejects_broken_notice_or_stale_english(report, damage):
    output, _ = report
    notice = output / "en/index.html"
    if damage == "missing":
        notice.unlink()
    elif damage == "bad_return":
        notice.write_text(notice.read_text(encoding="utf-8").replace('href="../index.html"', 'href="/"'), encoding="utf-8")
    else:
        (output / "en/quality.html").write_text("Stale translated report", encoding="utf-8")
    with pytest.raises(ValueError, match="notice|stale English"):
        check(output)


def test_measured_rebuild_preserves_language_and_explicit_english_generates_chinese(report, monkeypatch):
    output, source = report
    monkeypatch.setenv("BROWSER_EVAL_REPORT_LANGUAGE", "en")
    main(["--source", str(source), "--output", str(output)])
    assert json.loads((output / "manifest.json").read_text(encoding="utf-8"))["primary_language"] == "zh"
    main(["--source", str(source), "--output", str(output), "--language", "en"])
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["primary_language"] == "en"
    assert set(manifest["languages"]) == {"en", "zh"}
    assert manifest["language_notices"] == []
    assert BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser").html["lang"] == "zh"
    assert BeautifulSoup((output / "en/index.html").read_text(encoding="utf-8"), "html.parser").html["lang"] == "en"
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
    assert "data-english-not-generated" in notice.read_text(encoding="utf-8")
    notice.write_text("Broken English entry", encoding="utf-8")
    with pytest.raises(ValueError, match="Delivery page changed"):
        verify(output, destination)


def test_search_rebuild_records_builder_version_without_changing_measurements(report):
    from harness.web_fetch import __version__
    output, source = report
    before = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    build(output, source=source, language="en")
    after = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert after["rows"] == before["rows"]
    assert after["collection_profile"] == before["collection_profile"]
    assert after["browser_eval"]["version"] == __version__
    for route in ("index.html", "en/index.html"):
        dom = BeautifulSoup((output / route).read_text(encoding="utf-8"), "html.parser")
        assert dom.select_one('[data-browser-eval-release]').get_text() == "Browser Eval v" + __version__
    assert check(output)


@pytest.mark.parametrize("defect", ["file", "label", "binding"])
def test_search_release_rejects_lost_builder_identity(report, defect):
    output, _ = report
    if defect == "file":
        (output / "browser-eval-version.json").unlink()
    elif defect == "label":
        page = output / "index.html"
        page.write_text(page.read_text(encoding="utf-8").replace("data-browser-eval-release=", "data-lost-version="), encoding="utf-8")
    else:
        manifest = output / "manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        del value["browser_eval"]
        manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="Browser Eval"):
        check(output)


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_delivery_shell_keeps_requested_detail_instead_of_home_redirect(report, tmp_path, language):
    from harness.web_search.delivery import export
    output, source = report
    build(output, source=source, language=language)
    destination = tmp_path / 'delivery-shell'
    export(output, destination)
    for route in ('detail.html', 'evidence.html'):
        shell = BeautifulSoup((destination / route).read_text(encoding="utf-8"), 'html.parser')
        assert not shell.select('[data-report-language-entry]')
        assert shell.select_one('[data-delivery-target]')
