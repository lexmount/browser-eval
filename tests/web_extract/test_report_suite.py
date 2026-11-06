"""Independent benchmark results share navigation, never a quality denominator."""

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import check


@pytest.mark.parametrize("damage", [None, "quality", "navigation", "child-binding"])
def test_suite_uses_each_cohorts_results_and_bound_details(tmp_path, damage):
    output = tmp_path / "report"
    create(output, mode="field-extraction-suite")
    manifest = check(output)
    assert [c["id"] for c in manifest["cohorts"]] == ["first", "second"]
    path = output / "zh/index.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    assert [section["data-cohort"] for section in soup.select("[data-cohort]")] == [
        "first",
        "second",
    ]
    assert len(soup.select('[data-cohort="first"] tbody tr')) == 4
    assert not soup.select("main > table")
    if damage is None:
        with pytest.raises(ValueError, match="visual|Synthetic"):
            check(output, release=True)
        return
    if damage == "quality":
        soup.select_one('[data-cohort="first"] [data-metric="micro-f1"]').string = "0.999"
    elif damage == "navigation":
        soup.select_one("nav a")["href"] = "missing.html"
    else:
        path = output / "first/report-manifest.json"
        child = read_json(path)
        child["case_count"] += 1
        save_json(path, child)
    if damage != "child-binding":
        path.write_text(str(soup), encoding="utf-8")
    manifest["files"][path.relative_to(output).as_posix()] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="comparison|navigation|cohort manifest"):
        check(output)


def test_suite_example_rejects_repository_output_before_creating_anything():
    from pathlib import Path

    from harness.web_extract.example import create_suite

    output = Path(__file__).resolve().parents[2] / "rejected-suite-example"
    with pytest.raises(ValueError):
        create_suite(output)
    assert not output.exists()
    assert not output.with_name(output.name + "-suite-inputs").exists()


@pytest.mark.parametrize("reserved", ["zh", "en", "public-evidence", "assets"])
def test_suite_rejects_language_directory_collisions_before_writing(tmp_path, reserved):
    from harness.web_extract import report_suite

    content = tmp_path / "content.json"
    profile = tmp_path / "profile.json"
    save_json(content, {"cohorts": [{"id": reserved}]})
    save_json(profile, {"research_report": {"content": {"sha256": digest(content.read_bytes())}}})
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="distinct safe"):
        report_suite.build(output, content, profile=profile)
    assert not output.exists()


def test_quality_exclusion_preserves_operational_comparison(tmp_path):
    from pathlib import Path

    from harness.web_extract import report_suite

    source = create(tmp_path / "seed", mode="field-extraction-suite")
    suite = read_json(source / "content.json")
    entry = suite["cohorts"][0]
    content = Path(entry["content"])
    model = read_json(content)
    method = next(iter(model["participants"]))
    model["participants"][method].update(
        include_in_comparison=False, exclusion_reason="Synthetic account quota"
    )
    save_json(content, model)
    profile = Path(entry["profile"])
    authority = read_json(profile)
    authority["research_report"]["content"]["sha256"] = digest(content.read_bytes())
    save_json(profile, authority)
    output = tmp_path / "report"
    report_suite.build(output, source / "content.json", profile=source / "profile.json")
    soup = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    tables = soup.select('[data-cohort="first"] table')
    quality, operation = [table.select("tbody tr") for table in tables]
    assert len(quality) == len(operation) == 2
    assert "Synthetic account quota" in quality[0].get_text()
    assert [td.get_text() for td in quality[0].select("td")[2:5]] == ["—"] * 3
    assert model["participants"][method]["label"] in operation[0].get_text()
    assert operation[0].select('[data-metric="mean"]')
    assert "$" in operation[0].get_text()
