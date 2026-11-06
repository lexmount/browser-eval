"""A schema-based report must preserve the complete planned matrix and exact evidence."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import (
    _check_rendered_report,
    check,
    record_comparison,
    record_route,
)


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
@pytest.mark.parametrize("damage", [None, "home-intro", "moved-home-content"])
def test_interpretation_intro_and_core_first_home_rules_hold_together(tmp_path, mode, damage):
    from harness.web_extract.report import build

    home = create(tmp_path / "initial", mode=mode)
    model = read_json(home / "content.json")
    pages = model.setdefault("pages", {})
    pages.setdefault("interpretation", {})["intro"] = {"zh": "<h2>Extraction diagram</h2>"}
    if damage == "home-intro":
        pages.setdefault("index", {})["intro"] = {"zh": "<p>Misplaced home intro.</p>"}
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile.setdefault("research_report", {})["content"] = {
        "path": str(home / "content.json"), "sha256": digest((home / "content.json").read_bytes())}
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    check(output, home=home, profile=home / "profile.json")
    soup = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    if damage == "home-intro":
        from harness.web_extract.report import home_comparison_end

        assert soup.select_one("[data-page-intro]").find_previous_sibling() is home_comparison_end(
            soup
        )
    current = soup.select_one("main table[data-completion-metrics]")
    while current is not soup.main:
        assert not any(str(node).strip() for node in current.previous_siblings)
        current = current.parent
    if damage == "moved-home-content":
        def prepend(raw):
            page = BeautifulSoup(raw, "html.parser")
            node = page.new_tag("p")
            node.string = "Secondary interpretation before the comparison"
            page.main.insert(0, node)
            return str(page)
        rebind(output, "zh/index.html", prepend)
        with pytest.raises(ValueError, match="Core comparison must precede"):
            _check_rendered_report(output)


def test_english_build_requires_a_translated_page_intro(tmp_path):
    from harness.web_extract.report import build

    home = create(tmp_path / "initial")
    content = home / "content.json"
    model = read_json(content)
    model["pages"]["interpretation"]["intro"] = {"zh": "<p>Source-language intro.</p>"}
    save_json(content, model)
    output = tmp_path / "english"
    with pytest.raises(ValueError, match="English content is missing"):
        build(output, content, home=home, profile=home / "profile.json", language="en")
    assert not output.exists()


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
def test_latency_directions_are_checked_against_the_collected_contract(tmp_path, mode):
    output = tmp_path / "report"
    create(output, mode=mode)

    def change_comparison(raw):
        soup = BeautifulSoup(raw, "html.parser")
        cells = soup.select('[data-operating-metrics] [data-metric]')
        direction = ("min" if all(c["data-metric-direction"] == "neutral" for c in cells)
                     else "neutral")
        for cell in cells:
            cell["data-metric-direction"] = direction
            for winner in cell.select(".comparison-winner"):
                winner.unwrap()
        if direction == "min":
            for metric in {cell["data-metric"] for cell in cells}:
                selected = [cell for cell in cells if cell["data-metric"] == metric]
                minimum = min(float(cell["data-sort"]) for cell in selected)
                for cell in selected:
                    if float(cell["data-sort"]) == minimum:
                        winner = soup.new_tag("span", attrs={"class": "comparison-winner"})
                        for child in list(cell.contents):
                            winner.append(child.extract())
                        cell.append(winner)
        return str(soup)

    rebind(output, "zh/index.html", change_comparison)
    with pytest.raises(ValueError, match="latency/pricing|timing or pricing"):
        _check_rendered_report(output)


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
@pytest.mark.parametrize("damage", ["missing", "moved", "changed"])
def test_authored_page_intro_precedes_generated_tables_and_is_release_bound(tmp_path, mode, damage):
    from harness.web_extract.report import build

    initial = tmp_path / "initial"
    home = create(initial, mode=mode)
    content = home / "content.json"
    model = read_json(content)
    model.setdefault("pages", {}).setdefault("interpretation", {})["intro"] = {
        "zh": "<h2>Pipeline overview</h2><p>Frozen input, extraction, structured output.</p>"
    }
    save_json(content, model)
    profile = read_json(home / "profile.json")
    profile.setdefault("research_report", {}).setdefault("content", {})["sha256"] = digest(
        content.read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, content, home=home, profile=home / "profile.json", language="zh")
    _check_rendered_report(output)
    assert (output / "data/completion-metrics.json").read_bytes() == (
        initial / "data/completion-metrics.json").read_bytes()
    soup = BeautifulSoup((output / "zh/interpretation.html").read_text(encoding="utf-8"),
                         "html.parser")
    intro = soup.select_one("main > [data-page-intro]")
    assert intro is not None and soup.main.find(True, recursive=False) is intro

    def alter(raw):
        page = BeautifulSoup(raw, "html.parser")
        node = page.select_one("[data-page-intro]")
        if damage == "missing":
            node.decompose()
        elif damage == "moved":
            page.main.append(node.extract())
        else:
            node.h2.string = "Wrong pipeline"
        return str(page)

    rebind(output, "zh/interpretation.html", alter)
    with pytest.raises(ValueError, match="page intro"):
        _check_rendered_report(output)


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
    _check_rendered_report(output)
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
        _check_rendered_report(output)


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
    _check_rendered_report(output)
    soup = BeautifulSoup((output / "zh/protocol.html").read_text(encoding="utf-8"), "html.parser")
    assert '"price": 12' in soup.select_one("#examples-products").get_text()
    assert '"author"' in soup.select_one("#examples-books").get_text()
    assert '"author"' not in soup.select_one("#examples-products").get_text()
    assert len(soup.select("[data-method-example]")) == 2
    rebind(output, "zh/protocol.html", lambda s: s.replace('"price": 12', '"price": 99'))
    with pytest.raises(ValueError, match="native input/output evidence"):
        _check_rendered_report(output)


def test_record_report_drills_to_source_output_native_error_and_pending(tmp_path):
    output = tmp_path / "report"
    home = create(output, mode="field-extraction-cohort")
    manifest = _check_rendered_report(output)
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
        check(output, release=True, profile=home / "profile.json", home=home)


def rebind(output, route, transform):
    path = output / route
    path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"][route] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)


@pytest.mark.parametrize("change", ["substitute", "remove"])
def test_live_report_requires_reference_bound_original_source_navigation(tmp_path, change):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    truth = read_json(output / "public-evidence/reference.json")
    for path in (output / "zh").glob("*.html"):
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        case = soup.select_one("[data-source-case]")
        if case is None:
            continue
        cid = case["data-source-case"]
        for frame in soup.select("iframe.frozen-preview"):
            frame.decompose()
        link = soup.new_tag("a", href=truth[cid]["url"], target="_blank", rel="noopener noreferrer")
        link["data-live-source"] = ""
        link.string = "Original webpage"
        case.append(link)
        rebind(output, path.relative_to(output).as_posix(), lambda _, s=soup: str(s))
    manifest = read_json(output / "report-manifest.json")
    manifest["preview_snapshot"] = {
        "mode": "live",
        "case_count": len(truth),
        "urls": {cid: item["url"] for cid, item in truth.items()},
    }
    save_json(output / "report-manifest.json", manifest)
    check(output)
    cid = next(iter(truth))
    if change == "substitute":
        manifest["preview_snapshot"]["urls"][cid] = "https://unrelated.example/"
        save_json(output / "report-manifest.json", manifest)
    else:
        for path in (output / "zh").glob("*.html"):
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
            for link in soup.select("a[data-live-source]"):
                link.decompose()
            rebind(output, path.relative_to(output).as_posix(), lambda _, s=soup: str(s))
    with pytest.raises(ValueError, match="Live preview URLs|requires its original source link"):
        check(output)


@pytest.mark.parametrize('button_index', [0, -1])
@pytest.mark.parametrize('source', ['public-evidence/missing.json', '../outside.json'])
def test_inventory_evidence_targets_are_validated_after_rebinding(tmp_path, button_index, source):
    output = tmp_path / 'report'
    create(output, mode='field-extraction-cohort')
    (tmp_path / 'outside.json').write_text('{}', encoding='utf-8')

    def break_evidence(text):
        soup = BeautifulSoup(text, 'html.parser')
        buttons = soup.select('[data-call-log-open]')
        assert len(buttons) > 1
        buttons[button_index]['data-src'] = source
        return str(soup)

    rebind(output, 'zh/results.html', break_evidence)
    with pytest.raises(ValueError, match='Broken record evidence|relative to the hosted site root'):
        _check_rendered_report(output)


def test_evidence_index_keys_must_match_embedded_case_ids(tmp_path):
    output = tmp_path / 'report'
    create(output, mode='field-extraction-cohort')
    method = 'scrapegraph-extract'
    relative = 'public-evidence/' + method + '.json'
    path = output / relative
    bucket = read_json(path)
    bucket['one']['case_id'], bucket['two']['case_id'] = 'two', 'one'
    save_json(path, bucket)
    manifest = read_json(output / 'report-manifest.json')
    manifest['files'][relative] = digest(path.read_bytes())
    for receipt in manifest['public_evidence_views']:
        if receipt['path'] == relative:
            receipt['published_sha256'] = digest(path.read_bytes())
            receipt['published_bytes'] = path.stat().st_size
    save_json(output / 'report-manifest.json', manifest)
    for case_id, other in [('one', 'two'), ('two', 'one')]:
        def swap_target(text, other=other):
            soup = BeautifulSoup(text, 'html.parser')
            soup.find('details', id=method).select_one('[data-call-log-open]')['data-key'] = other
            return str(soup)
        rebind(output, 'zh/' + record_route('c', case_id) + '.html', swap_target)
    with pytest.raises(ValueError, match='evidence.*case identit'):
        _check_rendered_report(output)


@pytest.mark.parametrize(
    "damage",
    [
        "native",
        "field-value",
        "filter",
        "field-filter",
        "coverage",
        "price",
        "price-duplicate",
        "image",
    ],
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
    elif damage in {"filter", "field-filter", "coverage"}:

        def change(text):
            soup = BeautifulSoup(text, "html.parser")
            row = soup.select_one('tr[data-method="scrapegraph-extract"][data-status="pass"]')
            key = {
                "filter": "data-outcome",
                "field-filter": "data-author",
                "coverage": "data-scored-fields",
            }[damage]
            row[key] = "0" if damage == "coverage" else "wrong"
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
        _check_rendered_report(output)


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
        _check_rendered_report(output)


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
    manifest = _check_rendered_report(output)
    assert manifest["comparison_methods"] == ["scrapegraph-extract"]
    assert manifest["skipped_methods"] == {
        "firecrawl-json": "User omitted the interrupted participant"
    }
    assert manifest["observations"] == 4
    for route in ("index", "interpretation"):
        soup = BeautifulSoup(
            (output / "zh" / (route + ".html")).read_text(encoding="utf-8"), "html.parser"
        )
        for row in soup.select("[data-core-metrics] tbody tr"):
            cells = row.select("td")
            assert int(cells[1]["data-sort"]) == int(cells[1].get_text().split("/")[0])
            assert int(cells[2]["data-sort"]) == int(cells[2].get_text())
        reason = soup.select_one('[data-excluded-method="firecrawl-json"]')
        assert reason is not None
        assert "User omitted the interrupted participant" in reason.get_text()
    original = (output / "zh/index.html").read_text(encoding="utf-8")
    rebind(
        output,
        "zh/index.html",
        lambda s: s.replace("User omitted the interrupted participant", "Unrelated reason"),
    )
    with pytest.raises(ValueError, match="exclusion reason"):
        _check_rendered_report(output)
    rebind(output, "zh/index.html", lambda _: original)
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
        _check_rendered_report(output)


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
    _check_rendered_report(tmp_path / "record-demo")


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
        _check_rendered_report(output)


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
        _check_rendered_report(output)


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
    values = {row["data-case"]: row.select("td")[5].get("data-sort") for row in rows}
    assert values == {"one": "10.0", "two": "2.0", "three": ""}
    assert all(row.select("td")[4].get("data-sort").isdigit() for row in rows)


@pytest.mark.parametrize("selection", ["{method}", "runs/{method}", "absolute"])
def test_record_build_resolves_the_same_run_ids_as_review_and_repair(tmp_path, selection):
    from harness.web_extract.pipeline import resolve_run_path
    from harness.web_extract.report import build

    home = create(tmp_path / "example", mode="field-extraction-cohort")
    model = read_json(home / "content.json")
    for item in model["evidence_runs"]:
        method = Path(item["run"]).name
        item["run"] = item["run"] if selection == "absolute" else selection.format(method=method)
        assert resolve_run_path(home, item["run"]) == home / "runs" / method
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile["research_report"]["content"]["sha256"] = digest((home / "content.json").read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    _check_rendered_report(output)


def test_record_example_rejects_unsupported_language_without_leaving_files(tmp_path):
    from harness.web_extract.example import create_record

    for entry in (
        lambda output: create(output, language="en", mode="field-extraction-cohort"),
        lambda output: create_record(output, language="en"),
    ):
        output = tmp_path / "example"
        with pytest.raises(ValueError, match="Chinese only"):
            entry(output)
        assert list(tmp_path.iterdir()) == []
    create(tmp_path / "example", mode="field-extraction-cohort")


@pytest.mark.parametrize("damage", ["remove", "alter"])
def test_record_release_requires_all_retry_time_totals(tmp_path, damage):
    from harness.web_extract.report import build

    home = create(tmp_path / "example", mode="field-extraction-cohort")
    output = tmp_path / "report"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")

    def change(markup):
        soup = BeautifulSoup(markup, "html.parser")
        summary = soup.select_one("[data-retry-summary]")
        if damage == "remove":
            summary.decompose()
        else:
            summary.select("tbody td")[1].string = "999"
        return str(soup)

    rebind(output, "zh/protocol.html", change)
    with pytest.raises(ValueError, match="Retry timing"):
        _check_rendered_report(output)


@pytest.mark.parametrize("invalid", [2024, ["Ada", 1], None, False, {"value": "Ada"}])
def test_complete_record_report_renders_invalid_native_field_types(tmp_path, monkeypatch, invalid):
    import copy

    from harness.web_extract import example

    save = example.save_json

    def write(path, value, **kwargs):
        if path.parent.name == "calls" and value.get("http_status") == 200:
            value = copy.deepcopy(value)
            value["normalized_fields"]["author"] = invalid
            payload = value["response"]
            if "data" in payload:
                payload = payload["data"]
            payload["json"]["author"] = invalid
        return save(path, value, **kwargs)

    monkeypatch.setattr(example, "save_json", write)
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    _check_rendered_report(output)
    evidence = read_json(output / "public-evidence/scrapegraph-extract.json")
    assert evidence["one"]["field_results"]["author"]["status"] == "invalid"
    assert evidence["one"]["field_results"]["author"]["actual"] == invalid


@pytest.mark.parametrize("structured", [True, False])
def test_wholly_unannotated_page_is_unavailable_but_call_failures_remain_failures(structured):
    from harness.web_extract.report import record_inventory_outcome, record_result_fields

    reference = {"domain": "book", "site": "a", "values": {"title": []}}
    row = {
        "method": "firecrawl-parse-json",
        "compatibility": "structured_output" if structured else "no_structured_output",
        "normalized_fields": {"title": ["Example"]},
        "http_status": 200 if structured else 500,
        "response": {},
    }
    result = record_result_fields(
        reference,
        row,
        policy={
            "unavailable_reference_groups": [["book", "a", "title"]],
            "text_matching": "literal",
        },
    )
    assert result["scored_fields"] == 0
    assert result["status"] == ("unavailable" if structured else "call_error")
    assert record_inventory_outcome(result) == (
        ("unavailable", "unavailable") if structured else ("fail", "wrong")
    )


@pytest.mark.parametrize("dependency", ["report.js", "report.css", "report_ui.py", "messages.json"])
def test_record_report_rejects_stale_client_dependencies(tmp_path, monkeypatch, dependency):
    import shutil

    from harness.web_extract import messages, report

    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    current = tmp_path / "current-source"
    current.mkdir()
    if dependency in {"report.js", "report.css"}:
        shutil.copytree(report.ASSETS, current / "assets")
        changed = current / "assets" / dependency
        monkeypatch.setattr(report, "ASSETS", current / "assets")
    elif dependency == "report_ui.py":
        for name in ["report.py", "report_ui.py", "analysis.py"]:
            shutil.copyfile(Path(report.__file__).with_name(name), current / name)
        monkeypatch.setattr(report, "__file__", str(current / "report.py"))
        changed = current / dependency
    else:
        changed = current / dependency
        shutil.copyfile(messages.MESSAGES, changed)
        monkeypatch.setattr(messages, "MESSAGES", changed)
    changed.write_bytes(changed.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="dependencies changed"):
        _check_rendered_report(output)


def test_record_release_rejects_free_text_price_without_numeric_conversion(tmp_path, monkeypatch):
    from harness.web_extract.report import build

    home = create(tmp_path / "initial", mode="field-extraction-cohort")
    model = read_json(home / "content.json")
    for quote in model["prices"].values():
        quote.pop("usd_per_1000_range", None)
        quote.pop("pricing_formula", None)
        quote["usd_per_1000"] = "Contact sales"
    save_json(home / "content.json", model)
    profile = read_json(home / "profile.json")
    profile["research_report"]["content"]["sha256"] = digest((home / "content.json").read_bytes())
    save_json(home / "profile.json", profile)
    output = tmp_path / "free-text"
    build(output, home / "content.json", home=home, profile=home / "profile.json", language="zh")
    manifest = read_json(output / "report-manifest.json")
    manifest["dataset_kind"] = "published-upstream"
    save_json(output / "report-manifest.json", manifest)
    # This control isolates quote validation from the independently tested dataset authority.
    monkeypatch.setattr("harness.web_extract.report.check_input_profile", lambda *args: None)
    with pytest.raises(ValueError, match="normalized price"):
        check(output, release=True, profile=home / "profile.json", home=home)


@pytest.mark.parametrize("segmented", [False, True])
def test_authority_check_reuses_record_assembly_receipts(tmp_path, monkeypatch, segmented):
    from harness.web_extract import report

    home = create(tmp_path / "seed", mode="field-extraction-cohort")
    content, profile = home / "content.json", home / "profile.json"
    model = read_json(content)
    if segmented:
        model["evidence_runs"] = [{"segments": [entry]} for entry in model["evidence_runs"]]
    save_json(content, model)
    authority = read_json(profile)
    authority["research_report"]["content"] = {
        "path": str(content), "sha256": digest(content.read_bytes())
    }
    save_json(profile, authority)
    original = report.record_cohort_inputs

    def assemble(home, model, **options):
        compatible = {**model, "evidence_runs": [
            selection.get("segments", [selection])[0] for selection in model["evidence_runs"]
        ]}
        data = original(home, compatible, **options)
        for method, receipt in list(data["sources"].items()):
            extended = {**receipt, "case_count": len(data["cases"]),
                        "participant": {"method": method}, "scorer_sha256": "1" * 64}
            data["sources"][method] = {"segments": [extended]} if segmented else extended
        return data

    monkeypatch.setattr(report, "record_cohort_inputs", assemble)
    output = tmp_path / "report"
    report.build(output, content, home=home, profile=profile, language="zh")
    report.check(output, profile=profile, home=home)
    published = output / "public-evidence/sources.json"
    sources = read_json(published)
    next(iter(sources.values()))["unmeasured"] = True
    save_json(published, sources)
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["public-evidence/sources.json"] = digest(published.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="Completion audit certification binding"):
        report.check(output, profile=profile, home=home)


@pytest.mark.parametrize("matching", ["literal", "whitespace"])
def test_record_report_uses_declared_adapter_policy_and_rejects_substitution(
    tmp_path, monkeypatch, matching
):
    from harness.web_extract import report
    from harness.web_extract.dataset import benchmark_config

    home = create(tmp_path / "seed", mode="field-extraction-cohort")
    content, profile = home / "content.json", home / "profile.json"
    authority = read_json(profile)
    authority["research_report"]["content"]["path"] = str(content)
    save_json(profile, authority)
    output = tmp_path / "report"
    policy = {"unavailable_reference_groups": [], "text_matching": matching}
    adapter = benchmark_config(home)["adapter"]
    monkeypatch.setattr(adapter, "reference_policy", lambda home: policy, raising=False)
    report.build(output, content, home=home, profile=profile, language="zh")
    path = output / "report-manifest.json"
    manifest = read_json(path)
    assert manifest["reference_policy"] == policy
    report.check(output, profile=profile, home=home)
    manifest["reference_policy"] = {**policy, "text_matching": "undeclared"}
    save_json(path, manifest)
    with pytest.raises(ValueError, match="reference policy differs"):
        report.check(output, profile=profile, home=home)
