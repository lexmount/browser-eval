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
