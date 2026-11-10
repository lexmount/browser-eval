"""Published model tables must follow retained case judgments and timers."""

import json
import zipfile

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.analysis import summarize_model_rows
from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import build, check
from tests.web_extract.test_record_report import rebind

pytestmark = pytest.mark.usefixtures("stubbed_serial_collection")


def model_report(tmp_path, mode):
    home = create(tmp_path / "initial", mode=mode)
    rows = [
        {"model_id": model, "case_id": case, "dataset": "catalog", "schema_valid": True,
         "reference_complete": True, "page_exact": good, "tp": int(good),
         "fp": int(not good), "fn": int(not good), "retry_execution_seconds": 0,
         "timings": {key: seconds for key in (
             "first_any_output_seconds", "first_answer_seconds",
             "complete_json_seconds", "elapsed_seconds")}}
        for model, seconds in [("accurate", 3), ("fast", 1)]
        for case, good in [("one", True), ("two", model == "accurate")]
    ]
    scores = {"stage": "validation", "case_ids": ["one", "two"], "details": rows,
              "groups": summarize_model_rows(rows, ["accurate", "fast"])}
    raw = json.dumps(scores).encode()
    archive = home / "model-scores.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("validation-scores.json", raw)
    content = home / "content.json"
    model = read_json(content)
    page = model.setdefault("pages", {}).setdefault("interpretation", {})
    body = page.setdefault("body", "")
    placeholder = '<div data-model-validation>Authored numbers must be replaced</div>'
    if isinstance(body, dict):
        body["zh"] += placeholder
    else:
        model["pages"]["interpretation"]["body"] += placeholder
    model["model_comparison"] = {"dataset": "catalog", "tables": {"validation": {
        "cohorts": [{"archive": {"path": "assets/model-scores.zip",
                                    "sha256": digest(archive.read_bytes())},
                     "scores": {"path": "validation-scores.json", "sha256": digest(raw)}}]}}}
    model.setdefault("assets", []).append({"path": "assets/model-scores.zip",
                                           "source": str(archive),
                                           "sha256": digest(archive.read_bytes())})
    save_json(content, model)
    profile = read_json(home / "profile.json")
    profile.setdefault("research_report", {})["content"] = {
        "path": str(content), "sha256": digest(content.read_bytes())}
    save_json(home / "profile.json", profile)
    output = tmp_path / "report"
    build(output, content, home=home, profile=home / "profile.json", language="zh")
    return output


@pytest.mark.parametrize("mode", ["full-cohort-progress", "field-extraction-cohort"])
def test_model_table_is_generated_from_case_scores_in_both_report_modes(tmp_path, mode):
    output = model_report(tmp_path, mode)
    check(output)
    soup = BeautifulSoup(
        (output / "zh/interpretation.html").read_text(encoding="utf-8"), "html.parser")
    rows = soup.select("[data-model-validation] tbody tr")
    assert [[c.get_text() for c in row.select("td")[:4]] for row in rows] == [
        ["accurate", "100.00%", "2/2", "2/2"], ["fast", "50.00%", "2/2", "1/2"]]
    assert [float(row.select("td")[7]["data-sort"]) for row in rows] == [3, 1]
    assert not soup.select('[data-model-validation] [data-metric-direction="neutral"] '
                           '.comparison-winner')


@pytest.mark.parametrize("damage", ["quality", "timer", "missing-row", "missing-binding"])
def test_release_rejects_model_table_drift_after_file_hashes_are_updated(tmp_path, damage):
    output = model_report(tmp_path, "full-cohort-progress")
    if damage == "missing-binding":
        manifest = read_json(output / "report-manifest.json")
        manifest.pop("model_comparison")
        save_json(output / "report-manifest.json", manifest)
    else:
        def alter(raw):
            soup = BeautifulSoup(raw, "html.parser")
            row = soup.select_one("[data-model-validation] tbody tr")
            if damage == "missing-row":
                row.decompose()
            else:
                cell = row.select("td")[1 if damage == "quality" else 7]
                cell.clear()
                cell.append("Wrong number")
            return str(soup)
        rebind(output, "zh/interpretation.html", alter)
    with pytest.raises(ValueError, match="Model comparison"):
        check(output)


@pytest.mark.parametrize("incorrect", [0.1, 0.9, "missing-case", "duplicate-case"])
def test_model_summary_cannot_override_case_judgments_in_either_direction(tmp_path, incorrect):
    output = model_report(tmp_path, "field-extraction-cohort")
    archive = output / "assets/model-scores.zip"
    with zipfile.ZipFile(archive) as bundle:
        scores = json.loads(bundle.read("validation-scores.json"))
    if incorrect == "missing-case":
        scores["details"].pop()
        scores["groups"] = summarize_model_rows(scores["details"], scores["groups"])
    elif incorrect == "duplicate-case":
        scores["details"].append(scores["details"][0])
        scores["groups"] = summarize_model_rows(scores["details"], scores["groups"])
    else:
        scores["groups"]["fast"]["f1"] = incorrect
    raw = json.dumps(scores).encode()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("validation-scores.json", raw)
    manifest = read_json(output / "report-manifest.json")
    binding = manifest["model_comparison"]["tables"]["validation"]["cohorts"][0]
    binding["archive"]["sha256"] = digest(archive.read_bytes())
    binding["scores"]["sha256"] = digest(raw)
    manifest["files"]["assets/model-scores.zip"] = digest(archive.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="aggregates differ|case population"):
        check(output)
