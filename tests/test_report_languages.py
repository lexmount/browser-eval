import json
import unittest

import pytest

from harness.report_languages import (
    check_language_contract,
    english_notice,
    generated_languages,
    write_language_contract,
)


@pytest.mark.parametrize("supported", [("en", "zh"), ("zh",)])
@pytest.mark.parametrize("saved", [None, "en", "zh", "legacy"])
@pytest.mark.parametrize("requested", [None, "en", "zh"])
def test_language_resolution_respects_choice_and_renderer_capability(tmp_path, supported, saved, requested):
    from harness.report_languages import resolve_report_language
    if saved:
        contract = {"languages": ["zh"]} if saved == "legacy" else {"primary_language": saved}
        (tmp_path / "report-languages.json").write_text(json.dumps(contract))
    expected = requested or ("zh" if saved == "legacy" else saved) or supported[0]
    if expected in supported:
        assert resolve_report_language(tmp_path, requested, supported=supported) == expected
    else:
        with pytest.raises(ValueError, match="supports only zh"):
            resolve_report_language(tmp_path, requested, supported=supported)


def test_chinese_output_has_notice_without_translated_routes(tmp_path):
    write_language_contract(tmp_path, include_english=False)
    assert generated_languages(tmp_path) == ("zh",)
    check_language_contract(tmp_path)
    notice = (tmp_path / "en/index.html").read_text()
    assert "英文报告未生成" in notice
    assert 'href="../index.html"' in notice
    assert 'new URL("en/",b.href)' in notice
    assert "location.origin+location.pathname" not in notice
    (tmp_path / "en/old.html").write_text("stale")
    with pytest.raises(ValueError, match="stale English"):
        check_language_contract(tmp_path)


def test_explicit_english_retains_generated_pages(tmp_path):
    (tmp_path / "en").mkdir()
    (tmp_path / "en/index.html").write_text("English edition")
    write_language_contract(tmp_path, include_english=True)
    assert generated_languages(tmp_path) == ("zh", "en")
    assert (tmp_path / "en/index.html").read_text() == "English edition"
    check_language_contract(tmp_path)


def test_flat_notice_returns_within_the_report_root():
    notice = english_notice(nested=False)
    assert 'href="index.html"' in notice
    assert 'new URL("./",b.href)' in notice
    assert "../index.html" not in notice


def test_default_rejects_accidentally_generated_english(tmp_path):
    (tmp_path / "en").mkdir()
    (tmp_path / "en/index.html").write_text("unrequested")
    with pytest.raises(ValueError, match="must not be generated"):
        write_language_contract(tmp_path, include_english=False)


def test_language_contract_rejects_unknown_modes(tmp_path):
    (tmp_path / "report-languages.json").write_text(json.dumps({"languages": ["en"]}))
    with pytest.raises(ValueError, match="unsupported"):
        generated_languages(tmp_path)


class VisibleNumbersTests(unittest.TestCase):
    def test_chinese_spacing_preserves_numeric_parity_and_detects_changes(self):
        from harness.report_languages import visible_numbers
        expected = ["100", "103", "31", "100", "1,599", "80%"]
        self.assertEqual(visible_numbers("100题、103次调用，31/100，1,599项，80%"), expected)
        self.assertEqual(
            visible_numbers("100 tasks, 103 calls, 31/100, 1,599 items, 80%"), expected
        )
        self.assertNotEqual(visible_numbers("100题"), visible_numbers("99 tasks"))
        self.assertNotEqual(visible_numbers("100题"), visible_numbers("all tasks"))


@pytest.mark.parametrize("base_href,home_href", [(None, "../../index.html"), ("../../", "index.html"), ("../../", ""), ("../../", "./")])
def test_primary_english_keeps_explicit_chinese_navigation_and_evidence(tmp_path, base_href, home_href):
    from harness.report_languages import REPORT_PAGE_MARKER, select_report_language
    base = f'<base href="{base_href}">' if base_href else ""
    def page(content, head=""):
        return f'<html><head>{REPORT_PAGE_MARKER}{head}</head><body>{content}</body></html>'
    (tmp_path / "index.html").write_text(page('<header><a href="index.html">Home</a></header>'))
    (tmp_path / "en").mkdir()
    (tmp_path / "en/index.html").write_text(page('<header><a href="../index.html">中文</a></header>'))
    detail = tmp_path / "results/task/index.html"
    detail.parent.mkdir(parents=True)
    detail.write_text(page(f'<header><a href="{home_href}?token=abc#overview">Home</a></header>', base))
    evidence = tmp_path / "artifacts/original.html"
    evidence.parent.mkdir()
    evidence.write_text(page('<header><a href="../index.html">Original</a></header>'))
    evidence_bytes = evidence.read_bytes()
    write_language_contract(tmp_path, include_english=True)
    assert select_report_language(tmp_path, "en") == tmp_path / "en/index.html"
    assert 'href="../index.html?report-language=zh"' in (tmp_path / "en/index.html").read_text()
    assert 'token=abc&amp;report-language=zh#overview' in detail.read_text()
    assert 'data-report-language-entry' in (tmp_path / "index.html").read_text()
    assert evidence.read_bytes() == evidence_bytes
    contract = json.loads((tmp_path / "report-languages.json").read_text())
    assert contract["entrypoint"] == "en/index.html"


def test_rebuild_language_recognizes_older_chinese_only_reports(tmp_path):
    from harness.report_languages import primary_report_language
    assert primary_report_language(tmp_path) == "en"
    write_language_contract(tmp_path, include_english=False)
    assert primary_report_language(tmp_path) == "zh"
    write_language_contract(tmp_path, include_english=True)
    assert primary_report_language(tmp_path) == "zh"


@pytest.mark.parametrize("damage", ["unsupported", "missing_edition", "wrong_entry", "missing_redirect"])
def test_release_rejects_a_wrong_primary_language(tmp_path, damage):
    from harness.report_languages import REPORT_PAGE_MARKER, select_report_language
    (tmp_path / "index.html").write_text(f'<html><head>{REPORT_PAGE_MARKER}</head></html>')
    (tmp_path / "en").mkdir()
    (tmp_path / "en/index.html").write_text('English')
    write_language_contract(tmp_path, include_english=True)
    select_report_language(tmp_path, "en")
    check_language_contract(tmp_path)
    path = tmp_path / "report-languages.json"
    contract = json.loads(path.read_text())
    if damage == "unsupported": contract["primary_language"] = "fr"
    elif damage == "missing_edition": contract["languages"] = ["zh"]
    elif damage == "wrong_entry": contract["entrypoint"] = "index.html"
    else: (tmp_path / "index.html").write_text('Chinese root without selected-language redirect')
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match="language|entrypoint"):
        check_language_contract(tmp_path)
