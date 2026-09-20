from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from harness.trajectory_replay.failure_matrix import failure_category_matrix


def _row(method, task, code="", status="fail"):
    return dict(participant_id=method, task_id=task, status=status,
                cause_code=code, cause_zh=code + "中文" if code else "",
                cause_en=code + " English" if code else "")


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
    from scripts.check_webarena_replay_report import _check_failure_matrix

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
