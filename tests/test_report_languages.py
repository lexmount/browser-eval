import json
import unittest

import pytest

from harness.report_languages import (
    check_language_contract,
    english_notice,
    generated_languages,
    write_language_contract,
)


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
