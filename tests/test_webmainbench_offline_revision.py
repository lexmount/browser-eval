"""Offline revisions retain certified calls and publish only complete directories."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.web_fetch import webmainbench_correction as correction


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def certified_case(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    case = {
        "track_id": "one",
        "html": "<p>reference</p>",
        "groundtruth_content": "reference",
        "meta": {},
    }
    dataset = tmp_path / "corrected.jsonl"
    write_rows(dataset, [case])
    rows, calls = [], []
    for provider in ("first", "second"):
        path = source / (provider + ".md")
        path.write_text("reference")
        rows.append(
            {
                "provider": provider,
                "case_id": "one",
                "ok": True,
                "artifact_path": path.name,
                "output_sha256": correction.digest(path),
                "physical_attempt_count": 1,
                "metrics": {"text_edit": {"success": True, "score": 1, "details": {}}},
            }
        )
        calls.append(
            {
                "provider": provider,
                "track_id": "one",
                "attempt_id": provider,
                "physical_artifact_path": path.name,
                "output_sha256": correction.digest(path),
            }
        )
    write_rows(source / "results.jsonl", rows)
    write_rows(source / "physical-attempts.jsonl", calls)
    plan = {
        "case_id": "one",
        "providers": ["first", "second"],
        "input_html_sha256": correction.text_digest(case["html"]),
        "reference_sha256": correction.text_digest(case["groundtruth_content"]),
    }
    certificate = {
        "kind": "webmainbench_origin_correction_v1",
        "preflight": plan,
        "postflight": plan,
    }

    def recertify():
        certificate.update(
            results_sha256=correction.digest(source / "results.jsonl"),
            physical_sha256=correction.digest(source / "physical-attempts.jsonl"),
        )
        (source / "certificate.json").write_text(json.dumps(certificate))

    recertify()
    # Fixed third-party responses isolate evidence handling from scoring mathematics.
    metric = SimpleNamespace(
        calculate=lambda *a, **k: SimpleNamespace(
            score=0.5, success=True, details={}, error_message=None
        )
    )
    scorer = SimpleNamespace(
        metrics=dict.fromkeys(
            ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS"), metric
        )
    )
    monkeypatch.setattr(correction, "official_calculator", lambda *a: scorer)
    monkeypatch.setattr(correction, "validate_controls", lambda *a: None)
    return source, dataset, rows, calls, recertify


def snapshot(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def run_revision(source, dataset, output):
    return correction.rescore_case_correction(source, dataset, Path("unused"), "pinned", output)


def test_case_revision_preserves_source_and_physical_calls(certified_case, tmp_path):
    source, dataset, rows, _, _ = certified_case
    before = snapshot(source)
    output = tmp_path / "revised"
    receipt = run_revision(source, dataset, output)
    assert snapshot(source) == before
    assert (output / "physical-attempts.jsonl").read_bytes() == before[
        Path("physical-attempts.jsonl")
    ]
    revised = correction.read_rows(output / "results.jsonl")
    assert revised[0]["pre_scoring_correction_metrics"] == rows[0]["metrics"]
    assert revised[0]["metrics"]["text_edit"]["score"] == 1
    assert receipt["provider_calls"] == 0
    assert receipt["regression_registry_sha256"] == correction.digest(correction.REGISTRY)
    assert receipt["regression_ids"] == list(correction.CONTROL_IDS)
    for name in ("first.md", "second.md"):
        assert (output / name).read_bytes() == before[Path(name)]


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate_case",
        "changed_html",
        "changed_reference",
        "duplicate_result",
        "duplicate_call",
        "missing_call",
        "changed_physical_output",
        "controls",
    ],
)
def test_invalid_revision_never_creates_output(certified_case, tmp_path, monkeypatch, damage):
    source, dataset, rows, calls, recertify = certified_case
    case = correction.read_rows(dataset)[0]
    if damage == "duplicate_case":
        write_rows(dataset, [case, case])
    elif damage in {"changed_html", "changed_reference"}:
        case["html" if damage == "changed_html" else "groundtruth_content"] += " changed"
        write_rows(dataset, [case])
    elif damage == "duplicate_result":
        write_rows(source / "results.jsonl", rows + rows[:1])
    elif damage == "duplicate_call":
        write_rows(source / "physical-attempts.jsonl", calls + calls[:1])
    elif damage == "missing_call":
        write_rows(source / "physical-attempts.jsonl", calls[:1])
    elif damage == "changed_physical_output":
        (source / "first.md").write_text("changed")
    else:

        def reject(_):
            raise ValueError("scorer control failed")

        monkeypatch.setattr(correction, "validate_controls", reject)
    recertify()
    before = snapshot(source)
    output = tmp_path / "revised"
    with pytest.raises(ValueError):
        run_revision(source, dataset, output)
    assert not output.exists()
    assert snapshot(source) == before
    assert not list(tmp_path.glob(".revised-revision-*"))


def test_late_write_failure_discards_staging(certified_case, tmp_path, monkeypatch):
    source, dataset, *_ = certified_case
    before = snapshot(source)
    original = Path.write_text

    def fail_receipt(path, *args, **kwargs):
        if path.name == "scoring-correction.json":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_receipt)
    output = tmp_path / "revised"
    with pytest.raises(OSError, match="disk full"):
        run_revision(source, dataset, output)
    assert not output.exists()
    assert snapshot(source) == before
    assert not list(tmp_path.glob(".revised-revision-*"))


def test_output_symlink_cannot_redirect_revision(certified_case, tmp_path):
    source, dataset, *_ = certified_case
    destination = tmp_path / "unrelated"
    destination.mkdir()
    (destination / "keep").write_text("untouched")
    output = tmp_path / "revised"
    output.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError):
        run_revision(source, dataset, output)
    assert snapshot(destination) == {Path("keep"): b"untouched"}


def test_output_inside_source_is_rejected_before_copying(certified_case):
    source, dataset, *_ = certified_case
    before = snapshot(source)
    with pytest.raises(ValueError, match="outside"):
        run_revision(source, dataset, source / "revised")
    assert snapshot(source) == before
    assert not list(source.glob(".revised-revision-*"))


@pytest.mark.parametrize("late_failure", [False, True])
def test_full_population_revision_is_atomic(certified_case, tmp_path, monkeypatch, late_failure):
    source, dataset, sample_rows, _, _ = certified_case
    (source / "references").mkdir()
    cases, corrected, results = [], [], []
    for index in range(545):
        identifier = f"case-{index}"
        record = {
            "track_id": identifier,
            "url": "https://example.test/" + identifier,
            "html": "<p>reference</p>",
            "groundtruth_content": "reference",
            "meta": {},
        }
        corrected.append(record)
        cases.append(
            {
                "id": identifier,
                "url": record["url"],
                "meta": {},
                "input_html_sha256": correction.text_digest(record["html"]),
                "reference_sha256": correction.text_digest("reference"),
            }
        )
        (source / "references" / (identifier + ".md")).write_text("reference")
        for original in sample_rows:
            results.append(
                {
                    **original,
                    "case_id": identifier,
                    "attempt_id": original["provider"] + "__" + identifier,
                }
            )
    write_rows(dataset, corrected)
    write_rows(source / "results.jsonl", results)
    (source / "dataset.json").write_text(
        json.dumps({"cases": cases, "coverage_plan": {"static_html": ["first", "second"]}})
    )
    (source / "score-summaries.json").write_text(
        json.dumps(
            {
                name: {"official_aggregate": {"old": True}, "scorer": {}}
                for name in ("first", "second")
            }
        )
    )
    population = {
        "row_count": 545,
        "rows_sha256": correction.json_digest(corrected),
        "ordered_track_ids_sha256": correction.json_digest([row["track_id"] for row in corrected]),
    }
    manifest = tmp_path / "authority.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "corrections": [],
                "input_population": population,
                "output_population": population,
                "decision_contracts": {
                    "reference": {
                        "changed_field": "groundtruth_content",
                        "required_source_evidence": ["task_contract", "frozen_html"],
                    }
                },
            }
        )
    )
    before = snapshot(source)
    output = tmp_path / "full-revised"
    if late_failure:
        original = correction.shutil.copy2

        def fail_registry(src, dst, *args, **kwargs):
            if Path(src) == correction.REGISTRY:
                raise OSError("registry copy failed")
            return original(src, dst, *args, **kwargs)

        monkeypatch.setattr(correction.shutil, "copy2", fail_registry)
        with pytest.raises(OSError, match="registry copy failed"):
            correction.revise_dataset_scoring(source, dataset, manifest, tmp_path, "pinned", output)
        assert not output.exists()
    else:
        receipt = correction.revise_dataset_scoring(
            source, dataset, manifest, tmp_path, "pinned", output
        )
        assert receipt["logical_results"] == 1090
        assert receipt["providers"] == ["first", "second"]
        assert (output / "original-results.jsonl").read_bytes() == before[Path("results.jsonl")]
        assert len(correction.read_rows(output / "results.jsonl")) == 1090
    assert snapshot(source) == before
    assert not list(tmp_path.glob(".full-revised-revision-*"))
