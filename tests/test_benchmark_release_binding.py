"""Reports preserve an edition and reject mismatched tasks, scorers or labels."""

import json

import pytest
from lexbench_webfetch.versions import freeze_release

from harness.record_io import file_sha256
from harness.web_fetch.published_snapshot import build_snapshot_audit, check_snapshot_audit
from harness.web_fetch.report_version import bind_release, check_release
from tests.test_published_snapshot_report import example


def configured(tmp_path):
    source, profile = example(tmp_path)
    data = json.loads(profile.read_text())
    dataset = profile.parent / data["inputs"]["dataset"]["path"]
    release = tmp_path / "edition.json"
    freeze_release(dataset, release, benchmark="sample-benchmark", version="1.0.0")
    data["benchmark_release"] = {"path": release.name, "sha256": file_sha256(release)}
    profile.write_text(json.dumps(data))
    return source, profile, release


def test_versioned_snapshot_uses_existing_build_check_and_package(tmp_path):
    source, profile, release = configured(tmp_path)
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    output = tmp_path / "report"
    build_snapshot_audit(source, output, profile=profile)
    assert (output / "benchmark-release.json").read_bytes() == release.read_bytes()
    assert 'data-benchmark-release="1.0.0"' in (output / "index.html").read_text()
    assert "2 saved calls" in check_snapshot_audit(output)
    dataset = json.loads((output / "dataset.json").read_text())
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    check_release(output, dataset, rows, profile=profile)
    from harness.web_fetch.publication_check import check
    from harness.web_fetch.report_package import build

    archive = tmp_path / "report.zip"
    build(output, archive, snapshot_audit=True)
    check(archive, output, snapshot_audit=True)
    assert before == {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize(
    "defect", ["missing-file", "wrong-version", "removed-label", "wrong-scorer", "dropped-binding"]
)
def test_report_gate_rejects_release_drift(tmp_path, defect):
    source, profile, _ = configured(tmp_path)
    output = tmp_path / "report"
    build_snapshot_audit(source, output, profile=profile)
    if defect == "missing-file":
        (output / "benchmark-release.json").unlink()
    elif defect in {"wrong-version", "removed-label"}:
        page = output / "index.html"
        old = 'data-benchmark-release="1.0.0"'
        page.write_text(
            page.read_text().replace(
                old,
                'data-benchmark-release="2.0.0"'
                if defect == "wrong-version"
                else 'data-removed="1.0.0"',
            )
        )
    elif defect == "wrong-scorer":
        rows = output / "results.jsonl"
        records = [json.loads(line) for line in rows.read_text().splitlines()]
        records[0]["metrics"]["scoring_source_sha256"] = "0" * 64
        rows.write_text("".join(json.dumps(row) + "\n" for row in records))
        manifest = output / "report-manifest.json"
        data = json.loads(manifest.read_text())
        data["results_sha256"] = file_sha256(rows)
        manifest.write_text(json.dumps(data))
    else:
        manifest = output / "report-manifest.json"
        data = json.loads(manifest.read_text())
        del data["benchmark_release"]
        manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="[Bb]enchmark|scoring sources"):
        check_snapshot_audit(output)


def test_wrong_task_edition_is_rejected_before_rendering(tmp_path):
    source, profile, release = configured(tmp_path)
    data = json.loads(profile.read_text())
    dataset = json.loads((profile.parent / data["inputs"]["dataset"]["path"]).read_text())
    dataset["cases"][0]["task_prompt"] = "Return a different scope."
    records = [
        json.loads(line)
        for line in (profile.parent / data["inputs"]["results"]["path"]).read_text().splitlines()
    ]
    with pytest.raises(ValueError, match="dataset"):
        bind_release(source, tmp_path, dataset, records, profile=profile)
    assert release.is_file()


def test_legacy_hash_bound_reports_do_not_acquire_an_invented_version(tmp_path):
    source, profile = example(tmp_path)
    output = tmp_path / "report"
    build_snapshot_audit(source, output, profile=profile)
    assert not (output / "benchmark-release.json").exists()
    assert "data-benchmark-release" not in (output / "index.html").read_text()
    assert "2 saved calls" in check_snapshot_audit(output)


def test_wrong_edition_stops_before_capture_or_provider_calls(tmp_path, monkeypatch):
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256

    from harness.web_fetch.certification import main

    source = tmp_path / "dataset.json"
    data = {
        "cases": [
            {
                "id": "sample",
                "url": "https://example.test/",
                "task_prompt": "Return the complete article.",
            }
        ]
    }
    source.write_text(json.dumps(data))
    edition = tmp_path / "benchmark-release.json"
    freeze_release(source, edition, benchmark="sample-benchmark", version="1.0.0")
    data["cases"][0]["task_prompt"] = "Return only its title."
    source.write_text(json.dumps(data))
    calls = []
    monkeypatch.setattr("harness.web_fetch.certification.run", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="dataset"):
        main(
            [
                "--dataset",
                str(source),
                "--output",
                str(tmp_path / "run"),
                "--work-root",
                str(tmp_path / "work"),
                "--providers",
                "fixture",
                "--case-ids",
                "sample",
            ]
        )
    assert not calls
    assert not (tmp_path / "work").exists()
    assert SCORING_SOURCE_SHA256


def test_explicit_old_edition_preserves_historical_scores_with_new_renderer(tmp_path):
    source, profile, edition = configured(tmp_path)
    data = json.loads(profile.read_text())
    old_source = "a" * 64
    result_path = profile.parent / data["inputs"]["results"]["path"]
    rows = [json.loads(line) for line in result_path.read_text().splitlines()]
    for row in rows:
        row["metrics"]["scoring_source_sha256"] = old_source
        row.setdefault("scoring", {})["source_sha256"] = old_source
    result_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    data["inputs"]["results"]["sha256"] = file_sha256(result_path)
    receipt_path = profile.parent / data["inputs"]["receipt"]["path"]
    receipt = json.loads(receipt_path.read_text())
    receipt["scoring_source_sha256"] = old_source
    receipt_path.write_text(json.dumps(receipt))
    data["inputs"]["receipt"]["sha256"] = file_sha256(receipt_path)
    release = json.loads(edition.read_text())
    release["scoring_source_sha256"] = old_source
    edition.write_text(json.dumps(release))
    data["benchmark_release"]["sha256"] = file_sha256(edition)
    from harness.record_io import canonical_json_sha256
    from harness.web_fetch.report_explainer import _headless_article_input

    dataset_path = profile.parent / data["inputs"]["dataset"]["path"]
    cases = {case["id"]: case for case in json.loads(dataset_path.read_text())["cases"]}
    article_path = profile.parent / data["inputs"]["interpretation"]["path"]
    article = json.loads(article_path.read_text())
    article["evidence_sha256"] = canonical_json_sha256(
        _headless_article_input(cases, rows, ["chromium"])
    )
    article_path.write_text(json.dumps(article))
    data["inputs"]["interpretation"]["sha256"] = file_sha256(article_path)
    profile.write_text(json.dumps(data))
    output = tmp_path / "historical-report"
    build_snapshot_audit(source, output, profile=profile)
    assert (output / "results.jsonl").read_bytes() == result_path.read_bytes()
    assert "2 saved calls" in check_snapshot_audit(output)


def test_rescoring_edition_keeps_the_original_call_edition(tmp_path):
    from lexbench_webfetch.versions import create_release

    source, profile, edition = configured(tmp_path)
    data = json.loads(profile.read_text())
    dataset = json.loads((profile.parent / data["inputs"]["dataset"]["path"]).read_text())
    rows = [
        json.loads(line)
        for line in (profile.parent / data["inputs"]["results"]["path"]).read_text().splitlines()
    ]
    original = create_release(
        dataset, benchmark="sample-benchmark", version="1.0.0", scoring_source_sha256="f" * 64
    )
    original_path = source / "benchmark-input-release.json"
    original_path.write_text(json.dumps(original))
    execution = json.loads((source / "execution.json").read_text())
    execution["aggregate"] = {
        "benchmark_release": {"path": original_path.name, "sha256": file_sha256(original_path)}
    }
    (source / "execution.json").write_text(json.dumps(execution))
    corrected = create_release(
        dataset,
        benchmark="sample-benchmark",
        version="1.1.0",
        previous=original,
        change_note="Correct the saved-response scorer",
    )
    edition.write_text(json.dumps(corrected))
    data["benchmark_release"]["sha256"] = file_sha256(edition)
    profile.write_text(json.dumps(data))
    output = tmp_path / "revised"
    output.mkdir()
    selected = bind_release(source, output, dataset, rows, profile=profile)
    assert selected["version"] == "1.1.0"
    assert json.loads((output / "benchmark-release.json").read_text()) == corrected
    assert (output / original_path.name).read_bytes() == original_path.read_bytes()
    assert json.loads(original_path.read_text())["version"] == "1.0.0"
