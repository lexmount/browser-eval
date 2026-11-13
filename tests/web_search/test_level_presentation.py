"""Layered tables separate observed extrema, statistical support and absolute results."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_search.comparison import render_level_summary
from harness.web_search.output_levels import level_benchmarks
from lexbench_websearch.scoring import evaluate


def view(n=40, single=False):
    cases = [
        dict(
            id="q" + str(i),
            query="Who wrote it?",
            family="web",
            reference_answer="needle",
            reference_urls=["https://source.test/" + str(i)],
            evaluation_level="l1",
        )
        for i in range(n)
    ]
    methods = [
        dict(id=mid, label="Method " + mid.upper(), currency="USD", price_per_1000=1)
        for mid in (["a"] if single else ["b", "a"])
    ]
    profile = dict(cases=cases, methods=methods, protocol={"top_k": 5, "relevance_level": "l1"})
    rows = []
    for case in cases:
        for method in methods:
            hit = method["id"] == "a"
            results = [
                dict(
                    url=case["reference_urls"][0] if hit else "https://other.test/",
                    title="needle" if hit else "Unrelated",
                    snippet="Source excerpt",
                )
            ]
            rows.append(
                dict(
                    case_id=case["id"],
                    method=method["id"],
                    results=results,
                    cost=0.001,
                    http_status=200,
                    error="",
                    elapsed_ms=100 if hit else 200,
                    metrics=evaluate(case, results),
                )
            )
    output = dict(profile=profile, rows=rows)
    output["benchmarks"] = level_benchmarks(output)
    return output


@pytest.mark.parametrize("zh", [False, True])
def test_layer_table_has_correct_sort_order_raw_highlights_and_supported_stars(zh):
    doc = check_presentation(render_level_summary(view(), zh))
    table = doc.select_one("table")
    assert table.select_one("th[data-primary-metric]")["aria-sort"] == "descending"
    assert [r["data-summary-method"] for r in table.select("tbody tr")] == ["a", "b"]
    first = table.select("tbody tr")[0]
    for key in ["source_rr", "source_hit", "answer_match", "mean_ms", "p50_ms", "p95_ms"]:
        assert first.select_one('[data-metric$="-' + key + '"] .comparison-extremum') is not None
    assert first.select_one('[data-metric$="-source_rr"] .comparison-winner')
    assert not first.select_one('[data-metric$="-context_characters"] .comparison-extremum')
    assert doc.select_one('input[type="search"][data-level-summary-filter]')
    assert doc.select_one('.scroll.inventory[tabindex="0"]')


def test_small_subgroups_keep_observed_highlights_without_stars():
    doc = check_presentation(render_level_summary(view(n=20)))
    assert doc.select(".comparison-extremum")
    assert not doc.select(".comparison-winner")


def test_one_participant_has_absolute_results_without_winner_styling():
    doc = check_presentation(render_level_summary(view(single=True)))
    assert not doc.select(".comparison-winner, .comparison-extremum")
    assert len(doc.select("tbody tr")) == 1


@pytest.mark.parametrize(
    "damage", ["missing-extremum", "wrong-extremum", "missing-star", "unsupported-star"]
)
def test_shared_gate_rejects_wrong_raw_highlights_or_statistical_stars(damage):
    doc = BeautifulSoup(
        render_level_summary(view(n=20 if damage == "unsupported-star" else 40)), "html.parser"
    )
    cells = doc.select('[data-metric$="-source_rr"]')
    if damage == "missing-extremum":
        cells[0].select_one(".comparison-extremum").unwrap()
    elif damage == "wrong-extremum":
        cells[1].append(cells[0].select_one(".comparison-extremum").extract())
    elif damage == "missing-star":
        cells[0].select_one(".comparison-winner").decompose()
    else:
        marker = doc.new_tag("span", attrs={"class": "comparison-winner"})
        marker.string = "★"
        cells[0].append(marker)
    with pytest.raises(ValueError):
        check_presentation(doc)


def test_close_observed_leader_stays_green_without_a_significance_star():
    output = view(n=149)
    cases = {c["id"]: c for c in output["profile"]["cases"]}
    for row in output["rows"]:
        cid = row["case_id"]
        hit = (int(cid[1:]) % 2 == 0) == (row["method"] == "a")
        row["results"][0].update(
            url=cases[cid]["reference_urls"][0] if hit else "https://other.test/",
            title="needle" if hit else "Unrelated",
        )
        row["metrics"] = evaluate(cases[cid], row["results"])
    output["benchmarks"] = level_benchmarks(output)
    doc = check_presentation(render_level_summary(output))
    cells = doc.select('[data-metric$="-source_rr"]')
    assert cells[0].select_one(".comparison-extremum") is not None
    assert not doc.select(".comparison-winner")
