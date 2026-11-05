"""A schema-based report must preserve the complete planned matrix and exact evidence."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import check, record_comparison, record_route


def test_reader_navigation_order_and_labels_apply_to_home_and_evidence(tmp_path):
    from harness.web_extract.report import PAGES, build

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
        soup = BeautifulSoup(
            (output / "zh" / (route + ".html")).read_text(encoding="utf-8"), "html.parser"
        )
        links = soup.select("nav a")[: len(PAGES)]
        assert [link["href"] for link in links] == ["zh/" + item + ".html" for item in order]
        assert [link.get_text() for link in links] == ["Read " + item for item in order]
    assert (
        "Read protocol"
        in BeautifulSoup(
            (output / "zh/protocol.html").read_text(encoding="utf-8"), "html.parser"
        ).h1.get_text()
    )
    rebind(output, "zh/protocol.html", lambda s: s.replace("Read protocol", "Wrong label"))
    with pytest.raises(ValueError, match="navigation differs"):
        check(output)


@pytest.mark.parametrize("routes", [[], ["index"] * 9, ["index"], ["unknown"] * 9])
def test_navigation_rejects_missing_duplicate_or_unregistered_pages(routes):
    from harness.web_extract.report import page

    with pytest.raises(ValueError, match="Navigation must name"):
        page("zh", "index", "Example", "", navigation=[{"route": r, "label": r} for r in routes])


def test_external_docs_escape_the_preview_while_report_links_stay_inside():
    from harness.web_extract.report import page

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
    from harness.web_extract.report import PAGES, page

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
    from harness.web_extract.report import build

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
    soup = BeautifulSoup((output / "zh/protocol.html").read_text(encoding="utf-8"), "html.parser")
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
    assert "data-usage-table" not in (output / "zh/protocol.html").read_text(encoding="utf-8")
    assert "data-usage-table" in (output / "zh/accounts.html").read_text(encoding="utf-8")
    soup = BeautifulSoup((output / "zh/results.html").read_text(encoding="utf-8"), "html.parser")
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
    source = (output / "zh" / (record_route("d", "one") + ".html")).read_text(encoding="utf-8")
    assert 'sandbox=""' in source
    assert (
        BeautifulSoup(source, "html.parser").select_one("iframe")["src"]
        == "https://example.org/frozen/one"
    )
    failed = BeautifulSoup(
        (output / "zh" / (record_route("c", "three") + ".html")).read_text(encoding="utf-8"),
        "html.parser",
    )
    assert len(failed.select("#scrapegraph-extract [data-result-summary]")) == 1
    assert (
        "Synthetic navigation timeout"
        in failed.select_one("#scrapegraph-extract [data-result-logs]").get_text()
    )
    assert "data-language-notice" in (output / "en/index.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Synthetic record evidence"):
        check(output, release=True)


def rebind(output, route, transform):
    path = output / route
    path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"][route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)


@pytest.mark.parametrize(
    "damage",
    ["native", "field-value", "filter", "field-filter", "price", "price-duplicate", "image"],
)
def test_record_evidence_and_rendered_values_must_agree(tmp_path, damage):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    if damage == "native":
        path = output / "public-evidence/scrapegraph-extract.json"
        bucket = read_json(path)
        bucket["one"]["response"]["json"]["author"] = ["Unsupported answer"]
        save_json(path, bucket)
        manifest = read_json(output / "report-manifest.json")
        relative = path.relative_to(output).as_posix()
        manifest["files"][relative] = digest(path.read_bytes())
        for receipt in manifest["public_evidence_views"]:
            if receipt["path"] == relative:
                receipt["published_sha256"] = digest(path.read_bytes())
                receipt["published_bytes"] = path.stat().st_size
        save_json(output / "report-manifest.json", manifest)
        error = "native response differs"
    elif damage in {"filter", "field-filter"}:

        def change(text):
            soup = BeautifulSoup(text, "html.parser")
            row = soup.select_one('tr[data-method="scrapegraph-extract"][data-status="pass"]')
            row["data-outcome" if damage == "filter" else "data-author"] = "wrong"
            return str(soup)

        rebind(output, "zh/results.html", change)
        error = "filter verdict"
    elif damage == "field-value":

        def change(text):
            soup = BeautifulSoup(text, "html.parser")
            soup.select_one("[data-record-fields] tbody tr td:nth-child(3)").string = "Wrong value"
            return str(soup)

        rebind(output, "zh/" + record_route("c", "one") + ".html", change)
        error = "displayed fields"
    elif damage in {"price", "price-duplicate"}:

        def change(text):
            soup = BeautifulSoup(text, "html.parser")
            if damage == "price-duplicate":
                from copy import deepcopy

                soup.select_one("main").append(deepcopy(soup.select_one("[data-price-table]")))
            else:
                soup.select_one("[data-price-table]").decompose()
            return str(soup)

        rebind(output, "zh/accounts.html", change)
        error = "pricing coverage"
    else:
        rebind(
            output,
            "zh/interpretation.html",
            lambda s: s.replace("</main>", '<img src="assets/missing.png"></main>'),
        )
        error = "Broken record evidence"
    with pytest.raises(ValueError, match=error):
        check(output)


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
    from harness.web_extract.report import record_comparison_scope

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
    from harness.web_extract.report import build

    home = create(tmp_path / "initial", mode="field-extraction-cohort")
    model = read_json(home / "content.json")
    model["participants"]["firecrawl-json"].update(
        include_in_comparison=False, exclusion_reason="User omitted the interrupted participant"
    )
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile["research_report"]["content"]["sha256"] = digest((home / "content.json").read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    manifest = check(output)
    assert manifest["comparison_methods"] == ["scrapegraph-extract"]
    assert manifest["skipped_methods"] == {
        "firecrawl-json": "User omitted the interrupted participant"
    }
    assert manifest["observations"] == 4
    assert (
        len(
            BeautifulSoup(
                (output / "zh/results.html").read_text(encoding="utf-8"), "html.parser"
            ).select("tr[data-status]")
        )
        == 6
    )
    manifest["skipped_methods"].clear()
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="comparison scope"):
        check(output)


def test_skipped_latency_is_retained_without_ranking_it_as_comparison_leader():
    from harness.web_extract.report import operating_metrics

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


@pytest.mark.parametrize("exists", [False, True])
def test_record_report_accepts_absent_or_empty_output(tmp_path, exists):
    output = tmp_path / "report"
    if exists:
        output.mkdir()
    create(output, mode="field-extraction-cohort")
    assert (output / "zh/results.html").is_file()
    with pytest.raises(ValueError, match="empty report directory"):
        from harness.web_extract.report import build

        home = tmp_path / "report-synthetic-inputs"
        build(output, home / "content.json", home=home, profile=home / "profile.json")


def test_record_example_relative_output_outside_working_directory(tmp_path, monkeypatch):
    working = tmp_path / "work"
    working.mkdir()
    monkeypatch.chdir(working)
    home = create(Path("../record-demo"), mode="field-extraction-cohort")
    assert home.is_absolute()
    check(tmp_path / "record-demo")


@pytest.mark.parametrize(
    "route,leak",
    [
        ("zh/interpretation.html", "/" + "Users/private-user/internal-file"),
        ("report-manifest.json", "/" + "Users/private-user/internal-file"),
        ("assets/notes.txt", "/" + "Users/private-user/internal-file"),
    ],
)
def test_record_distribution_scans_all_files_including_metadata(tmp_path, route, leak):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    manifest = read_json(output / "report-manifest.json")
    if route == "report-manifest.json":
        manifest["leaked_note"] = leak
    else:
        path = output / route
        path.parent.mkdir(exist_ok=True)
        raw = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(raw + leak, encoding="utf-8")
        manifest["files"][route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="privacy risk"):
        check(output)


@pytest.mark.parametrize("href", ["#missing-section", "zh/interpretation.html#missing-method"])
def test_record_release_rejects_missing_local_fragment(tmp_path, href):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    rebind(
        output,
        "zh/protocol.html",
        lambda s: s.replace("</main>", f'<a href="{href}">Evidence</a></main>'),
    )
    with pytest.raises(ValueError, match="Broken evidence anchor"):
        check(output)


def test_record_inventory_sorts_exact_counts_and_missing_timers_numerically(tmp_path):
    from harness.web_extract.report import build

    home = create(tmp_path / "initial", mode="field-extraction-cohort")
    run = home / "runs/scrapegraph-extract"
    entries = []
    for cid, seconds in zip(["one", "two", "three"], [10.0, 2.0, None], strict=True):
        path = run / "calls" / (cid + ".json")
        row = read_json(path)
        row["elapsed_seconds"] = seconds
        save_json(path, row)
        entries.append({"path": "calls/" + path.name, "sha256": digest(path.read_bytes())})
    import json

    (run / "calls.journal.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    out = tmp_path / "new"
    build(out, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    soup = BeautifulSoup((out / "zh/results.html").read_text(encoding="utf-8"), "html.parser")
    rows = soup.select('tr[data-method="scrapegraph-extract"]')
    values = {row.select("td")[0].get_text(): row.select("td")[5].get("data-sort") for row in rows}
    assert values == {"one": "10.0", "two": "2.0", "three": ""}
    assert all(row.select("td")[4].get("data-sort").isdigit() for row in rows)
