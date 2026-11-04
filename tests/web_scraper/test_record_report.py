"""A schema-based report must preserve the complete planned matrix and exact evidence."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_scraper.example import create
from harness.web_scraper.pipeline import digest, read_json, save_json
from harness.web_scraper.report import check, record_comparison, record_route


def test_reader_navigation_order_and_labels_apply_to_home_and_evidence(tmp_path):
    from harness.web_scraper.report import PAGES, build

    home = create(tmp_path / "initial", mode="field-extraction-cohort")
    model = read_json(home / "content.json")
    order = ["index", "protocol", *[route for route in PAGES if route not in {"index", "protocol"}]]
    model["navigation"] = [{"route": route, "label": "Read " + route} for route in order]
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile["research_report"]["content"]["sha256"] = digest((home / "content.json").read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    check(output)
    for route in ["protocol", record_route("c", "three")]:
        soup = BeautifulSoup((output / "zh" / (route + ".html")).read_text(), "html.parser")
        links = soup.select("nav a")[: len(PAGES)]
        assert [link["href"] for link in links] == ["zh/" + item + ".html" for item in order]
        assert [link.get_text() for link in links] == ["Read " + item for item in order]
    assert (
        "Read protocol"
        in BeautifulSoup((output / "zh/protocol.html").read_text(), "html.parser").h1.get_text()
    )
    rebind(output, "zh/protocol.html", lambda s: s.replace("Read protocol", "Wrong label"))
    with pytest.raises(ValueError, match="navigation differs"):
        check(output)


@pytest.mark.parametrize("routes", [[], ["index"] * 9, ["index"], ["unknown"] * 9])
def test_navigation_rejects_missing_duplicate_or_unregistered_pages(routes):
    from harness.web_scraper.report import page

    with pytest.raises(ValueError, match="Navigation must name"):
        page("zh", "index", "Example", "", navigation=[{"route": r, "label": r} for r in routes])


def test_external_docs_escape_the_preview_while_report_links_stay_inside():
    from harness.web_scraper.report import page

    soup = BeautifulSoup(
        page(
            "zh",
            "protocol",
            "Example",
            '<a href="https://docs.example.org/page">Official</a>'
            '<a href="//docs.example.org/page">Protocol relative</a>'
            '<a href="results.html#case">Results</a>'
            '<a href="#sample">Sample</a>',
        ),
        "html.parser",
    )
    for link in soup.select('a[href*="docs.example.org"]'):
        assert link.get("target") == "_blank"
        assert {"noopener", "noreferrer"}.issubset(link.get("rel", []))
    for label in ["Results", "Sample"]:
        link = soup.find("a", string=label)
        assert not link.get("target")


def test_grouped_navigation_keeps_secondary_pages_discoverable():
    from harness.web_scraper.report import PAGES, page

    entries = [
        {"route": route, "label": route, "primary": route in {"index", "protocol", "results"}}
        for route in PAGES
    ]
    soup = BeautifulSoup(page("zh", "research", "Example", "", navigation=entries), "html.parser")
    assert len(soup.select("nav > a")) == 3
    assert len(soup.select("nav details a")) == 6
    assert soup.select_one("nav details[open] a[aria-current]").get_text() == "research"
    assert soup.select_one(".report-tools a").get_text() == "English"


def test_native_method_examples_keep_benchmarks_separate_and_reject_changed_outputs(tmp_path):
    from harness.web_scraper.report import build

    home = create(tmp_path / "initial", mode="field-extraction-cohort")
    groups = []
    for benchmark, native in [("products", {"price": 12}), ("books", {"author": ["Ada"]})]:
        groups.append(
            {
                "id": benchmark,
                "label": benchmark,
                "description": "Synthetic evidence",
                "methods": [
                    {
                        "id": "shared-provider",
                        "label": "Shared provider",
                        "implementation": "Hosted",
                        "input_mode": "URL",
                        "field_contract": "Caller fields",
                        "custom_schema": True,
                        "output_field": "data",
                        "output_path": ["data"],
                        "example": {
                            "case_id": benchmark + "-one",
                            "http_status": 200,
                            "elapsed_seconds": 1,
                            "request": {
                                "endpoint": "https://example.org/extract",
                                "fixture_url": "https://example.org/" + benchmark,
                                "input": "url",
                                "schema": {"type": "object"},
                            },
                            "response": {"data": native},
                            "source": {"sha256": "a" * 64},
                        },
                    }
                ],
            }
        )
    save_json(home / "examples.json", {"benchmarks": groups})
    binding = {
        "path": "assets/examples.json",
        "sha256": digest((home / "examples.json").read_bytes()),
    }
    model = read_json(home / "content.json")
    model["method_examples"] = binding
    model["assets"] = [{**binding, "source": "examples.json"}]
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile["research_report"]["content"]["sha256"] = digest((home / "content.json").read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    check(output)
    soup = BeautifulSoup((output / "zh/protocol.html").read_text(), "html.parser")
    assert '"price": 12' in soup.select_one("#examples-products").get_text()
    assert '"author"' in soup.select_one("#examples-books").get_text()
    assert '"author"' not in soup.select_one("#examples-products").get_text()
    assert len(soup.select("[data-method-example]")) == 2
    rebind(output, "zh/protocol.html", lambda s: s.replace('"price": 12', '"price": 99'))
    with pytest.raises(ValueError, match="native input/output evidence"):
        check(output)


def test_record_report_drills_to_source_output_native_error_and_pending(tmp_path):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    manifest = check(output)
    assert manifest["observations"] == 4
    assert len(list(output.rglob("*.html"))) == 17
    assert manifest["languages"] == ["zh"]
    assert manifest["complete_methods"] == []
    assert "data-usage-table" not in (output / "zh/protocol.html").read_text()
    assert "data-usage-table" in (output / "zh/accounts.html").read_text()
    soup = BeautifulSoup((output / "zh/results.html").read_text(), "html.parser")
    assert len(soup.select("tr[data-status]")) == 6
    assert len(soup.select('tr[data-status="pending"]')) == 2
    assert {n["data-result-filter"] for n in soup.select("[data-result-filter]")} >= {
        "status",
        "method",
        "domain",
        "site",
        "field",
        "query",
    }
    source = (output / "zh" / (record_route("d", "one") + ".html")).read_text()
    assert 'sandbox=""' in source
    assert (
        BeautifulSoup(source, "html.parser").select_one("iframe")["src"]
        == "https://example.org/frozen/one"
    )
    failed = BeautifulSoup(
        (output / "zh" / (record_route("c", "three") + ".html")).read_text(),
        "html.parser",
    )
    assert len(failed.select("#fixture-a [data-result-summary]")) == 1
    assert (
        "Synthetic navigation timeout"
        in failed.select_one("#fixture-a [data-result-logs]").get_text()
    )
    assert "data-language-notice" in (output / "en/index.html").read_text()
    with pytest.raises(ValueError, match="Synthetic record evidence"):
        check(output, release=True)


def rebind(output, route, transform):
    path = output / route
    path.write_text(transform(path.read_text()))
    manifest = read_json(output / "report-manifest.json")
    manifest["files"][route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)


@pytest.mark.parametrize(
    "damage,error",
    [
        ("score", "core comparison values"),
        ("pending", "inventory omits"),
        ("error", "native failure error"),
        ("target", "evidence points"),
        ("summary", "exactly one summary"),
        ("receipts", "receipts are incomplete"),
    ],
)
def test_release_checks_reject_semantic_damage_even_after_file_rebinding(tmp_path, damage, error):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    if damage == "receipts":
        manifest = read_json(output / "report-manifest.json")
        manifest["public_evidence_views"].pop()
        save_json(output / "report-manifest.json", manifest)
    elif damage == "score":
        rebind(output, "zh/index.html", lambda s: s.replace("3 / 3", "99 / 3", 1))
    elif damage == "pending":

        def remove(s):
            soup = BeautifulSoup(s, "html.parser")
            soup.select_one('tr[data-status="pending"]').decompose()
            return str(soup)

        rebind(output, "zh/results.html", remove)
    else:
        route = "zh/" + record_route("c", "three") + ".html"
        if damage == "error":
            rebind(
                output,
                route,
                lambda s: s.replace("Synthetic navigation timeout", "Different failure"),
            )
        elif damage == "target":
            rebind(output, route, lambda s: s.replace('data-key="three"', 'data-key="one"'))
        else:
            rebind(
                output,
                route,
                lambda s: s.replace(
                    '<section data-result-summary="">',
                    '<section data-result-summary=""><section data-result-summary></section>',
                    1,
                ),
            )
    with pytest.raises(ValueError, match=error):
        check(output)


def test_record_metrics_highlight_all_exact_ties_and_ignore_unavailable():
    body = record_comparison(
        ["method", "F1"],
        [["a", "0.8"], ["b", "0.8"], ["c", "—"]],
        {1: ("f1", "max", [0.8, 0.8, None])},
    )
    soup = check_presentation(body)
    assert len(soup.select(".comparison-winner")) == 2
    with pytest.raises(ValueError, match="highlight"):
        check_presentation(body.replace('class="comparison-winner"', 'class="ordinary"', 1))


def test_explicit_omission_keeps_scope_and_requires_a_reason():
    from harness.web_scraper.report import record_comparison_scope

    participants = {
        "complete": {"label": "Complete"},
        "paused": {
            "label": "Paused",
            "include_in_comparison": False,
            "exclusion_reason": "User omitted quota-blocked interface; calls unchanged",
        },
    }
    assert record_comparison_scope(participants) == (
        ["complete"],
        {"paused": participants["paused"]["exclusion_reason"]},
    )
    participants["paused"].pop("exclusion_reason")
    with pytest.raises(ValueError, match="requires its reason"):
        record_comparison_scope(participants)
    with pytest.raises(ValueError, match="at least one"):
        record_comparison_scope(
            {"paused": {"include_in_comparison": False, "exclusion_reason": "Not selected"}}
        )


def test_omission_does_not_remove_observations_or_pending_cases(tmp_path):
    from harness.web_scraper.report import build

    home = create(tmp_path / "initial", mode="field-extraction-cohort")
    model = read_json(home / "content.json")
    model["participants"]["fixture-b"].update(
        include_in_comparison=False, exclusion_reason="User omitted the interrupted participant"
    )
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile["research_report"]["content"]["sha256"] = digest((home / "content.json").read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    manifest = check(output)
    assert manifest["comparison_methods"] == ["fixture-a"]
    assert manifest["skipped_methods"] == {"fixture-b": "User omitted the interrupted participant"}
    assert manifest["observations"] == 4
    assert (
        len(
            BeautifulSoup((output / "zh/results.html").read_text(), "html.parser").select(
                "tr[data-status]"
            )
        )
        == 6
    )
    manifest["skipped_methods"].clear()
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="comparison scope"):
        check(output)


def test_skipped_latency_is_retained_without_ranking_it_as_comparison_leader():
    from harness.web_scraper.report import operating_metrics

    methods = ["complete", "skipped"]
    prices = {
        m: {
            "usd_per_1000": "$1",
            "terms": {"en": "Synthetic"},
            "verified_at": "2026-01-01",
            "sources": ["https://example.org/pricing"],
        }
        for m in methods
    }
    rows = [
        {"method": "complete", "elapsed_seconds": 5},
        {"method": "skipped", "elapsed_seconds": 1},
    ]
    soup = check_presentation(
        operating_metrics(
            methods, rows, prices, "en", {m: m for m in methods}, compared=["complete"]
        )
    )
    rendered = soup.select("tbody tr")
    assert len(rendered[0].select(".comparison-winner")) == 3
    assert not rendered[1].select(".comparison-winner")
    assert rendered[1].select_one('[data-metric="home-mean"]').get_text() == "1.000"
