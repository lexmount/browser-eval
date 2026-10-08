from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from harness.trajectory_replay.failure_matrix import (
    browser_failure_profile,
    failure_category_matrix,
    failure_signature_cards,
)


def _row(method, task, code="", status="fail"):
    return dict(participant_id=method, task_id=task, status=status,
                cause_code=code, cause_zh=code + "中文" if code else "",
                cause_en=code + " English" if code else "",
                reason_zh=code + "原因" if code else "",
                reason_en=code + " reason" if code else "",
                key=f"{method}-{task}")


def test_failure_signatures_keep_only_three_leading_causes_and_link_to_analysis():
    methods = {"one": {"label": "One"}}
    rows = [_row("one", task, code) for task, code in enumerate(("a", "a", "b", "c", "d"))]
    soup = BeautifulSoup(
        failure_signature_cards(methods, rows, "zh", participants=("one",)),
        "html.parser",
    )
    assert len(soup.select('[data-method="one"] li:not(.failure-remainder)')) == 3
    assert soup.select_one(".failure-remainder strong").text == "1"
    assert soup.select_one("h2").text == "1 款浏览器的主导错误"
    assert soup.select_one(".section-heading>a").text == "查看 1 章详解"
    assert soup.select_one(".section-heading>a")["href"] == "interpretation.html#browser-diagnoses"


def test_failure_signature_copy_uses_registered_participant_count_in_english():
    methods = {"one": {"label": "One"}, "two": {"label": "Two"}}
    rows = [_row("one", 1, "a"), _row("two", 1, "b")]
    soup = BeautifulSoup(
        failure_signature_cards(
            methods, rows, "en", participants=("one", "two")
        ),
        "html.parser",
    )

    assert soup.select_one("h2").text == "Dominant failures in 2 browsers"
    assert soup.select_one(".section-heading>a").text == "Read 2 browser chapters"


def test_browser_profile_covers_every_reviewed_subtype_with_evidence():
    methods = {"one": {"label": "One"}}
    rows = [_row("one", 0, status="pass"), _row("one", 1, "a"), _row("one", 2, "b")]
    soup = BeautifulSoup(browser_failure_profile(methods, rows, "zh", "one"), "html.parser")
    assert {item["data-cause"] for item in soup.select(".failure-bars [data-cause]")} == {"a", "b"}
    assert {item["data-cause-detail"] for item in soup.select("[data-cause-detail]")} == {"a", "b"}
    assert {link["href"] for link in soup.select("[data-cause-detail] a")} == {
        "result/one-1.html", "result/one-2.html"
    }


def test_article_embedding_keeps_matrix_without_a_duplicate_section_heading():
    methods = {"one": {"label": "One"}}
    rows = [_row("one", 1, "zero_area_target_geometry")]
    regular = BeautifulSoup(failure_category_matrix(methods, rows, "en"), "html.parser")
    embedded = BeautifulSoup(failure_category_matrix(methods, rows, "en", embedded=True), "html.parser")
    assert embedded.select_one("[data-failure-matrix]").name == "div"
    assert not embedded.select("h2, section")
    assert str(embedded.select_one("table")) == str(regular.select_one("table"))


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_matrix_counts_disjoint_families_and_links_to_existing_filters(lang):
    methods = {"baseline": {"label": "Baseline"}, "candidate": {"label": "Candidate"}}
    rows = [_row("baseline", i, status="pass") for i in range(3)] + [
        _row("candidate", 0, "zero_area_target_geometry"),
        _row("candidate", 1, "zero_width_forum_link"),
        _row("candidate", 2, "input_navigation_lifecycle"),
    ]
    soup = BeautifulSoup(failure_category_matrix(methods, rows, lang), "html.parser")
    assert soup.select_one('[data-family="geometry"]')["colspan"] == "2"
    assert soup.select_one('[data-family="interaction"]')["colspan"] == "1"
    for method, total in (("baseline", 0), ("candidate", 3)):
        row = soup.select_one(f'[data-method="{method}"]')
        assert sum(int(cell["data-count"]) for cell in row.select("[data-count]")) == total
        assert row.select_one("[data-failure-total]").text == f"{total} / 3"
    for link in soup.select("tbody a"):
        query = parse_qs(urlsplit(link["href"]).query)
        assert query["participant"] == ["candidate"]
        assert query["status"] == ["fail"]
        assert query["cause"][0].endswith("中文")
        assert urlsplit(link["href"]).fragment == "result-inventory"


def test_matrix_keeps_unknown_and_unreviewed_failures_in_denominator():
    rows = [_row("new", 1, "future_category"), _row("new", 2)]
    soup = BeautifulSoup(failure_category_matrix({"new": {"label": "New"}}, rows, "en"), "html.parser")
    assert soup.select_one('[data-family="other"]')["colspan"] == "2"
    assert soup.select_one("[data-failure-total]").text == "2 / 2"
    assert soup.select_one('td[data-category="unreviewed"]').text == "1"
    assert not soup.select_one('td[data-category="unreviewed"] a')


@pytest.mark.parametrize("rows, message", [
    ([_row("one", 1), _row("one", 1)], "duplicate"),
    ([_row("other", 1)], "unregistered"),
])
def test_matrix_rejects_ambiguous_populations(rows, message):
    with pytest.raises(ValueError, match=message):
        failure_category_matrix({"one": {"label": "One"}}, rows, "zh")


@pytest.mark.parametrize("mutation", ["missing", "wrong-count", "wrong-target", "missing-cell", "wrong-total"])
def test_release_gate_rejects_broken_matrix(mutation):
    from harness.trajectory_replay.webarena_report_check import _check_failure_matrix

    rows = [_row("one", 1, "zero_area_target_geometry")]
    page = failure_category_matrix({"one": {"label": "One"}}, rows, "en") + '<div id="result-inventory"></div>'
    _check_failure_matrix(page, rows, ["one"])
    soup = BeautifulSoup(page, "html.parser")
    if mutation == "missing":
        soup.select_one("[data-failure-matrix]").decompose()
    elif mutation == "wrong-count":
        soup.select_one("[data-count]")["data-count"] = "0"
    elif mutation == "wrong-target":
        soup.select_one("tbody a")["href"] = "results.html?status=pass"
    elif mutation == "missing-cell":
        soup.select_one("[data-count]").decompose()
    else:
        soup.select_one("[data-failure-total]").string = "1 / 90"
    with pytest.raises(ValueError, match="failure"):
        _check_failure_matrix(str(soup), rows, ["one"])
