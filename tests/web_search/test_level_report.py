"""The maintained report entry builds and checks isolated output-level pages."""

import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search.example import example
from harness.web_search.report import check


def test_public_example_builds_level_tables_findings_and_bound_assessments(tmp_path):
    example(tmp_path, levels=True)
    root = tmp_path / "report"
    assert check(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["level_assessments"]["path"] == "level-assessments/level-assessments.json"
    assert {item["path"] for item in manifest["bundle"]} >= {
        "level-assessments/level-assessments.json",
        "level-assessments/level-l1.json",
    }
    for prefix in ["", "en/"]:
        home = BeautifulSoup((root / (prefix + "index.html")).read_text(), "html.parser")
        assert [s["data-output-level"] for s in home.select("[data-output-level]")] == ["l1"]
        assert {s["data-level-benchmark"] for s in home.select("[data-level-benchmark]")} == {
            "scenario",
            "factual",
        }
        assert not home.select("[data-executive], [data-discovery]")
        quality = BeautifulSoup((root / (prefix + "quality.html")).read_text(), "html.parser")
        assert {
            s["data-findings-benchmark"] for s in quality.select("[data-findings-benchmark]")
        } == {"scenario", "factual"}
        assert len(quality.select("[data-primary-chart]")) == 2
        assert not quality.select("[data-factual]")
        assert (
            len(
                BeautifulSoup(
                    (root / (prefix + "capabilities.html")).read_text(), "html.parser"
                ).select('[data-capability-level="l1"]')
            )
            == 1
        )


@pytest.mark.parametrize(
    "damage",
    ["missing-level", "missing-chart", "wrong-value", "missing-filter", "wrong-participant"],
)
def test_public_check_rejects_missing_or_incorrect_level_presentation(tmp_path, damage):
    example(tmp_path, levels=True)
    root = tmp_path / "report"
    path = root / ("quality.html" if damage == "missing-chart" else "index.html")
    soup = BeautifulSoup(path.read_text(), "html.parser")
    if damage == "missing-level":
        soup.select_one("[data-output-level]").decompose()
    elif damage == "missing-chart":
        soup.select_one("[data-primary-chart]").decompose()
    elif damage == "wrong-value":
        soup.select_one("[data-output-level] td[data-metric]")["data-sort"] = "999"
    elif damage == "missing-filter":
        soup.select_one("[data-level-summary-filter]").decompose()
    else:
        soup.select_one("[data-output-level] tbody tr td").string = "An unsupported provider"
    path.write_text(str(soup))
    with pytest.raises(ValueError):
        check(root)


def test_level_results_and_details_keep_text_and_grade_sources_separate():
    from harness.web_search.comparison import render_level_results, render_level_detail
    from harness.web_search.output_levels import assessed_levels
    from tests.web_search.test_level_scores import complete_levels

    profile, rows, admissions, artifacts, evidence = complete_levels()
    views = assessed_levels(
        profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
    )
    for view in views.values():
        for method in view['profile']['methods']:
            method['label'] = 'Fixture ' + method['id']
    for method in profile['methods']:
        method['label'] = 'Fixture ' + method['id']
    row = rows[0]
    native = dict(
        row,
        answer="The generated answer [引用:1]",
        results=[dict(row["results"][0], citation_id=1)],
    )
    answer_score = dict(
        case_id=row["case_id"],
        method=row["method"],
        answer_grade=2,
        citation_count=1,
        supported_citations=1,
    )
    from copy import deepcopy

    answer_profile = deepcopy(profile)
    answer_profile["protocol"]["relevance_level"] = "l3"
    raw_grade = json.dumps(dict(grade=2, reason='Useful but incomplete'))
    call = dict(response={'choices': [{'message': {'content': raw_grade}}]})
    views["l3"] = dict(profile=answer_profile, rows=[native], answer_scores=[answer_score],
                       answers={'calls': {row['method'] + '/' + row['case_id'] + '/scenario': call}})
    soup = BeautifulSoup(render_level_results(views), "html.parser")
    assert [s["data-level-results"] for s in soup.select("[data-level-results]")] == [
        "l1",
        "l2",
        "l3",
    ]
    data = [json.loads(node.string) for node in soup.select("[data-comparison-data]")]
    assert data[0]["cases"][0]["outputs"]["web"]["items"][0]["snippet"] == "Primary preview"
    assert data[1]["cases"][0]["outputs"]["web"]["items"][0]["snippet"].startswith(
        "An independently published paragraph"
    )
    assert not soup.select_one('[data-level-results="l2"] option[value="source"]')
    assert soup.select_one("[data-answer-result] td:nth-of-type(4)").get_text() == "2 / 3"
    assert not soup.select_one('[data-level-results="l3"] .comparison-winner')
    detail = BeautifulSoup(render_level_detail(views, native), "html.parser")
    assert len(detail.select("[data-detail-level]")) == 3
    assert (
        detail.select_one('[data-detail-level="l1"] .native-answer').get_text() == "Primary preview"
    )
    assert (
        detail.select_one('[data-detail-level="l3"] .native-answer').get_text() == native["answer"]
    )
    assert "1 / 1" in detail.select_one('[data-detail-level="l3"]').get_text()

    from harness.web_search.report import _resolve_owned_links

    for prefix in ('', 'en/'):
        for site in ('', 'reports'):
            rendered = render_level_results(views, detail_link=lambda r, site=site, prefix=prefix: '/'.join(
                part for part in (site, prefix.rstrip('/'), 'details', r['id'] + '.html') if part))
            doc = BeautifulSoup(rendered, 'html.parser')
            _resolve_owned_links(doc, prefix + 'results.html', site, set())
            expected = '/'.join(part for part in (site, prefix.rstrip('/'), 'details',
                                                 native['id'] + '.html#l3') if part)
            assert doc.select_one('[data-answer-result] a')['href'] == expected


@pytest.mark.parametrize("damage", ["missing-results-level", "mixed-preview", "wrong-detail"])
def test_public_check_rejects_mixed_or_missing_level_evidence_views(tmp_path, damage):
    example(tmp_path, levels=True)
    root = tmp_path / "report"
    path = (
        next((root / "details").glob("*.html"))
        if damage == "wrong-detail"
        else root / "results.html"
    )
    soup = BeautifulSoup(path.read_text(), "html.parser")
    if damage == "missing-results-level":
        soup.select_one("[data-level-results]").decompose()
    elif damage == "mixed-preview":
        node = soup.select_one("[data-comparison-data]")
        value = json.loads(node.string)
        value["cases"][0]["outputs"]["lexmount"]["items"][0]["snippet"] = (
            "A body from another output level"
        )
        node.string = json.dumps(value)
    else:
        soup.select_one("[data-detail-level] .native-answer").string = "Wrong scored text"
    path.write_text(str(soup))
    with pytest.raises(ValueError):
        check(root)
