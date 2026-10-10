"""WCXB readers need complete result inventories and working diagnosis entry points."""

import pytest

from harness.web_fetch.wcxb_report_check import (
    ReportShape,
    require_analysis_controls,
    require_bilingual_shape,
)

ANALYSIS = """
<select id="compare-baseline"></select><input name="compare-provider">
<select id="compare-relation"></select><tbody id="comparison-rows"></tbody>
<dialog id="diagnosis-dialog"><button data-call-log-close>Close</button>
<a data-diagnosis-link href="cases/one.html">Evidence</a></dialog>
<table><tbody><tr data-provider="first"><td><button data-diagnosis-open>Diagnose</button></td></tr>
<tr data-provider="second"><td><button data-diagnosis-open>Diagnose</button></td></tr>
</tbody></table>
"""


def test_result_inventory_exposes_both_results_and_diagnosis_controls():
    soup = require_analysis_controls(ANALYSIS, 2)
    assert {row["data-provider"] for row in soup.select("tr[data-provider]")} == {"first", "second"}


@pytest.mark.parametrize(
    "removed",
    [
        "tr[data-provider]",
        "[data-diagnosis-open]",
        "[data-diagnosis-link]",
        "[data-call-log-close]",
    ],
)
def test_incomplete_result_or_diagnosis_is_rejected(removed):
    soup = require_analysis_controls(ANALYSIS, 2)
    soup.select_one(removed).decompose()
    with pytest.raises(ValueError):
        require_analysis_controls(soup, 2)


def test_localized_number_spacing_does_not_change_measurements():
    zh, en = ReportShape(), ReportShape()
    zh.feed("<p>100题、103次调用，31/100，1,599项，80%</p>")
    en.feed("<p>100 tasks, 103 calls, 31/100, 1,599 items, 80%</p>")
    require_bilingual_shape(zh, en, "index.html")
    wrong = ReportShape()
    wrong.feed("<p>99 tasks, 103 calls, 31/100, 1,599 items, 80%</p>")
    with pytest.raises(ValueError, match="value differs"):
        require_bilingual_shape(zh, wrong, "index.html")
