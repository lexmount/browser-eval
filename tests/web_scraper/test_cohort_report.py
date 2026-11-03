"""Full planned populations, checkpoint evidence and certified quality presentation."""

import pytest
from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_scraper.example import create
from harness.web_scraper.pipeline import digest, read_json, save_json
from harness.web_scraper.report import build, check, cohort_metrics


def test_progress_includes_unexecuted_cases_without_inventing_calls(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    manifest = read_json(output / "report-manifest.json")
    assert manifest["observations"] == 4
    assert len(manifest["planned_methods"]) == 2
    inventory = BeautifulSoup((output / "zh/results.html").read_text(), "html.parser")
    assert len(inventory.select("tr[data-status]")) == 6
    assert len(inventory.select('tr[data-status="pending"]')) == 2
    home = BeautifulSoup((output / "zh/index.html").read_text(), "html.parser")
    assert all(
        c.get_text(strip=True) == "—"
        for c in home.select("[data-core-metrics] tbody td:nth-child(n+6)")
    )
    assert check(output)["state"] == "full-cohort-progress"
    # Evidence is grouped by method, loaded on demand, rather than copied per detail.
    assert len(list((output / "evidence").glob("*.json"))) == 2
    assert "data-key=" in next((output / "zh").glob("result-*.html")).read_text()


def test_checkpoint_rejects_modified_calls_and_missing_participants(tmp_path):
    home = create(tmp_path / "first", mode="full-cohort-progress")
    content = read_json(home / "content.json")
    source = home / "runs/synthetic-success"
    call = next((source / "calls").glob("*.json"))
    original = call.read_bytes()
    call.write_text("{}")
    with pytest.raises(ValueError, match="Checkpoint call changed"):
        build(
            tmp_path / "tampered", home / "content.json", home=home, profile=home / "profile.json"
        )
    call.write_bytes(original)
    content["evidence_runs"].pop()
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="missing a planned method"):
        build(tmp_path / "missing", home / "content.json", home=home, profile=home / "profile.json")


def test_progress_release_check_rejects_changed_counts_even_with_new_file_hash(tmp_path):
    output = tmp_path / "report"
    create(output, mode="full-cohort-progress")
    page = output / "zh/index.html"
    soup = BeautifulSoup(page.read_text(), "html.parser")
    soup.select_one("[data-core-metrics] tbody tr td:nth-child(3)").string = "99"
    page.write_text(str(soup))
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/index.html"] = digest(page.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="progress count differs"):
        check(output)


def test_quality_highlights_raw_unique_extrema_but_leaves_ties_neutral():
    def metrics(n):
        return {f: {"f1": n} for f in ("price", "sku", "availability", "OutOfStock")}

    bindings = [{"certified": True, "scores": {"a": metrics(0.81), "b": metrics(0.80)}}]
    body, _ = cohort_metrics(["a", "b", "pending"], [], bindings, 140, "zh")
    soup = check_presentation(body)
    assert len(soup.select(".comparison-winner")) == 4
    assert soup.select("tbody tr")[-1].select("td")[-1].get_text() == "—"
    bindings[0]["scores"]["b"] = metrics(0.81)
    body, _ = cohort_metrics(["a", "b"], [], bindings, 140, "zh")
    assert not check_presentation(body).select(".comparison-winner")


def test_requested_english_requires_matching_authored_translation(tmp_path):
    home = create(tmp_path / "first")
    content = read_json(home / "content.json")
    content["pages"]["index"]["body"].pop("en")
    save_json(home / "content.json", content)
    with pytest.raises(ValueError, match="English content is missing"):
        build(
            tmp_path / "english",
            home / "content.json",
            home=home,
            profile=home / "profile.json",
            language="en",
        )
