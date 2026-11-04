"""A schema-based report must preserve the complete planned matrix and exact evidence."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_scraper.example import create
from harness.web_scraper.pipeline import digest, read_json, save_json
from harness.web_scraper.report import check, record_comparison, record_route


def test_record_report_drills_to_source_output_native_error_and_pending(tmp_path):
    output = tmp_path / "report"
    create(output, mode="field-extraction-cohort")
    manifest = check(output)
    assert manifest["observations"] == 4
    assert manifest["languages"] == ["zh"]
    assert manifest["complete_methods"] == []
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
        (output / "zh" / (record_route("r", "fixture-a", "three") + ".html")).read_text(),
        "html.parser",
    )
    assert len(failed.select("[data-result-summary]")) == 1
    assert "Synthetic navigation timeout" in failed.select_one("#result-logs").get_text()
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
        route = "zh/" + record_route("r", "fixture-a", "three") + ".html"
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
