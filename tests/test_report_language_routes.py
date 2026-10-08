"""Generated editions own complete routes; the absent edition owns only its notice."""

import pytest

from harness.report_languages import localized_report_routes, write_language_contract

ROUTES = {"index.html", "analysis/index.html", "cases/one.html"}


def edition(root, prefix=""):
    for route in ROUTES:
        path = root / prefix / route
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html><body>synthetic page</body></html>")


def test_chinese_only_routes_do_not_require_translated_pages(tmp_path):
    edition(tmp_path)
    write_language_contract(tmp_path, include_english=False)
    routes = localized_report_routes(tmp_path, ROUTES)
    assert set(routes) == {"zh"}
    assert set(routes["zh"]) == ROUTES


def test_nested_case_named_en_is_part_of_the_chinese_inventory(tmp_path):
    edition(tmp_path)
    path = tmp_path / "cases/en/index.html"
    path.parent.mkdir(parents=True)
    path.write_text("a case whose identifier is en")
    write_language_contract(tmp_path, include_english=False)
    routes = localized_report_routes(tmp_path, ROUTES | {"cases/en/index.html"})
    assert "cases/en/index.html" in routes["zh"]


def test_requested_english_has_the_same_complete_route_inventory(tmp_path):
    edition(tmp_path)
    edition(tmp_path, "en")
    write_language_contract(tmp_path, include_english=True)
    assert set(localized_report_routes(tmp_path, ROUTES)) == {"zh", "en"}
    (tmp_path / "en/cases/one.html").unlink()
    with pytest.raises(ValueError, match="en report route inventory"):
        localized_report_routes(tmp_path, ROUTES)


@pytest.mark.parametrize("damage", ["missing_chinese", "extra_chinese", "stale_english", "notice"])
def test_incomplete_or_undeclared_editions_are_rejected(tmp_path, damage):
    edition(tmp_path)
    write_language_contract(tmp_path, include_english=False)
    if damage == "missing_chinese":
        (tmp_path / "analysis/index.html").unlink()
    elif damage == "extra_chinese":
        (tmp_path / "old.html").write_text("old")
    elif damage == "stale_english":
        (tmp_path / "en/old.html").write_text("old")
    else:
        (tmp_path / "en/index.html").write_text("missing return link")
    with pytest.raises(ValueError):
        localized_report_routes(tmp_path, ROUTES)
