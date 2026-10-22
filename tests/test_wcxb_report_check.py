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


def test_bilingual_navigation_keeps_filters_and_normalizes_language_selection():
    zh, en, broken = ReportShape(), ReportShape(), ReportShape()
    zh.feed('<a href="index.html?report-language=zh&amp;filter=fail#results">中文</a>')
    en.feed('<a href="en/index.html?filter=fail#results">English</a>')
    broken.feed('<a href="en/index.html?filter=pass#results">English</a>')
    require_bilingual_shape(zh, en, "cases/example.html")
    with pytest.raises(ValueError, match="bilingual"):
        require_bilingual_shape(zh, broken, "cases/example.html")


def test_primary_language_entry_script_does_not_change_report_content_shape():
    zh, en, broken = ReportShape(), ReportShape(), ReportShape()
    zh.feed('<p>100</p><script data-report-language-entry>location.replace("en/")</script>')
    en.feed('<p>100</p>')
    broken.feed('<p>100</p><script>unexpected()</script>')
    require_bilingual_shape(zh, en, "index.html")
    with pytest.raises(ValueError, match="bilingual"):
        require_bilingual_shape(zh, broken, "index.html")


@pytest.mark.parametrize("separate_example", [False, True])
def test_guide_checks_bound_content_and_current_methods_without_fixed_cases(separate_example):
    from bs4 import BeautifulSoup
    from harness.web_fetch.wcxb_report_check import require_guide_interpretation

    anchors = ('guide-results', 'guide-score', 'guide-cases', 'guide-methods',
               'guide-timing', 'guide-conclusion')
    html = '<main><nav class="essay-toc">' + ''.join(
        f'<a href="#{name}">{name}</a>' for name in anchors) + '</nav>'
    html += '<section id="guide-results"><table><tbody>'
    for name in ('alpha', 'beta'):
        html += f'<tr><td><a href="providers/{name}.html">{name}</a></td><td>50.00%</td><td>15 ms</td></tr>'
    html += '</tbody></table></section><section id="guide-score">Word overlap</section>'
    html += '<section id="guide-cases"><a href="cases/custom-example.html#alpha">Evidence</a></section>'
    html += '<section id="guide-methods"><table><tbody><tr><td>alpha</td></tr><tr><td>beta</td></tr></tbody></table></section>'
    html += '<section id="guide-timing"><a href="research/wcxb-full-paired-latency/calls.jsonl">Calls</a><a href="research/wcxb-full-paired-latency/certification.json">Certificate</a></section>'
    html += '<section id="guide-conclusion">' + 'This is a synthetic bound finding with independent evidence. ' * 5 + '</section></main>'
    if separate_example:
        html = html.replace('<section id="guide-cases"><a', '<section id="guide-cases">Examples</section><section class="sample"><a')
    summary = {'providers': {p: {'quality_f1': .5, 'mean_latency_ms': 15} for p in ('alpha', 'beta')}}
    require_guide_interpretation(html, summary, ['alpha', 'beta'], 'en', expected_html=html)
    for selector in ('a[href*="custom-example"]', '#guide-conclusion', '#guide-results tbody tr'):
        changed = BeautifulSoup(html, 'html.parser')
        changed.select_one(selector).decompose()
        with pytest.raises(ValueError, match='bound interpretation'):
            require_guide_interpretation(changed, summary, ['alpha', 'beta'], 'en', expected_html=html)
    missing = BeautifulSoup(html, 'html.parser')
    missing.select_one('a[href*="custom-example"]').decompose()
    with pytest.raises(ValueError, match='inspectable source'):
        require_guide_interpretation(missing, summary, ['alpha', 'beta'], 'en', expected_html=str(missing))
    # Even authentic prose must agree with the current measured numbers.
    changed_summary = {'providers': {**summary['providers'], 'alpha': {'quality_f1': .7, 'mean_latency_ms': 15}}}
    with pytest.raises(ValueError, match='alpha evidence'):
        require_guide_interpretation(html, changed_summary, ['alpha', 'beta'], 'en', expected_html=html)

