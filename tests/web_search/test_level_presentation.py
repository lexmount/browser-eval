"""Layered tables separate observed extrema, statistical support and absolute results."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_search.comparison import render_level_summary
from harness.web_search.output_levels import level_benchmarks
from lexbench_websearch.scoring import evaluate


def test_level_latency_uses_all_frozen_rounds_while_quality_stays_on_score_round():
    from copy import deepcopy

    selected = view(n=2)
    selected['timing_rows'] = deepcopy(selected['rows'])
    for row in selected['rows']:
        selected['timing_rows'].append(dict(row, elapsed_ms=900, round=1))
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    values = {row['data-summary-method']: row for row in doc.select('tbody tr[data-summary-method]')}
    assert float(values['a'].select_one('[data-metric$="-mean_ms"]')['data-sort']) == 500
    assert float(values['b'].select_one('[data-metric$="-mean_ms"]')['data-sort']) == 550
    assert len(selected['benchmarks']['factual']['scores']) == 4


def test_collection_context_is_available_without_expanding_comparison_rows():
    selected = view(n=2)
    selected['profile']['method_collections'] = [dict(id='old', methods=['a', 'b'],
        started_at='2026-11-13T10:00:00Z', protocol={'concurrency': 4})]
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    contexts = doc.select('details.measurement-context')
    assert len(contexts) == 2
    assert all(not node.has_attr('open') for node in contexts)
    assert all('2026-11-13' in node.get_text() and 'concurrency 4' in node.get_text() for node in contexts)


def test_context_size_counts_titles_when_the_provider_has_no_primary_excerpt():
    selected = view(n=2)
    for row in selected['rows']:
        row['results'][0].update(title='Only a title', snippet='')
    doc = BeautifulSoup(render_level_summary(selected), 'html.parser')
    values = [float(cell['data-sort']) for cell in doc.select('[data-metric$="-context_characters"]')]
    assert values == [len('Only a title')] * 2


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


def test_factual_chart_counts_joint_outcomes_and_shows_measured_english_sources():
    from harness.web_search.comparison import factual_outcome_chart

    output = view()
    case = output["profile"]["cases"][0]
    case["reference_urls"] = ["https://en.wikipedia.org/wiki/Example"]
    case["url_equivalence"] = "document-url/2"
    by_case = {c["id"]: c for c in output["profile"]["cases"]}
    for row in output["rows"]:
        if row["case_id"] == case["id"] and row["method"] == "a":
            row["results"][0]["url"] = "https://en.wikipedia.com/wiki/Example"
        if row["case_id"] == "q1" and row["method"] == "b":
            row["results"][0]["title"] = "needle"
        if row["case_id"] == "q2" and row["method"] == "a":
            row["results"][0]["title"] = "An unrelated title"
        row["metrics"] = evaluate(by_case[row["case_id"]], row["results"])
    output["benchmarks"] = level_benchmarks(output)
    doc = BeautifulSoup(factual_outcome_chart(output), "html.parser")
    counts = {
        row["data-factual-outcome-method"]: [
            int(n["data-count"]) for n in row.select("[data-count]")
        ]
        for row in doc.select("[data-factual-outcome-method]")
    }
    assert counts == {"a": [39, 1, 0, 0], "b": [0, 0, 1, 39]}
    example = doc.select_one("[data-english-wikipedia-example]")
    assert [a["href"] for a in example.select("a")] == [
        "https://en.wikipedia.com/wiki/Example",
        "https://en.wikipedia.org/wiki/Example",
    ]


def test_unavailable_factual_reference_coverage_has_no_zero_score_chart():
    from harness.web_search.comparison import factual_outcome_chart, source_matching_chart

    output = view()
    output["profile"]["factual_quality_exclusions"] = [c["id"] for c in output["profile"]["cases"]]
    output["benchmarks"] = level_benchmarks(output)
    assert factual_outcome_chart(output) == source_matching_chart(output) == ""
    doc = check_presentation(render_level_summary(output))
    assert not doc.select('[data-metric$="-source_rr"] .comparison-extremum')
