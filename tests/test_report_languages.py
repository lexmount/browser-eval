import json

import pytest

from harness.report_languages import (
    check_language_contract,
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
    assert "location.origin+location.pathname" in notice
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


def test_default_rejects_accidentally_generated_english(tmp_path):
    (tmp_path / "en").mkdir()
    (tmp_path / "en/index.html").write_text("unrequested")
    with pytest.raises(ValueError, match="must not be generated"):
        write_language_contract(tmp_path, include_english=False)


def test_language_contract_rejects_unknown_modes(tmp_path):
    (tmp_path / "report-languages.json").write_text(json.dumps({"languages": ["en"]}))
    with pytest.raises(ValueError, match="unsupported"):
        generated_languages(tmp_path)
