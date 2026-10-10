"""TEDS audits bind the declared matrix and accept empty metric populations."""

import copy
import json
import sys
from pathlib import Path

import pytest

from harness.web_fetch import webmainbench_diagnostics as diagnostics
from harness.web_fetch.scoring_regressions import build_correction_provenance
from harness.web_fetch.webmainbench_scoring import aggregate

METRICS = ("text_edit", "code_edit", "formula_edit", "table_edit", "table_TEDS")


@pytest.fixture
def revision(tmp_path):
    source, corrected = tmp_path / "source", tmp_path / "corrected"
    source.mkdir()
    corrected.mkdir()
    rows = []
    for provider in ("first", "second"):
        for case_id in ("one", "two"):
            name = provider + "-" + case_id + ".md"
            for root in (source, corrected):
                (root / name).write_text("synthetic response")
            rows.append(
                {
                    "attempt_id": provider + "__" + case_id,
                    "provider": provider,
                    "case_id": case_id,
                    "ok": True,
                    "artifact_path": name,
                    "output_sha256": diagnostics.digest(source / name),
                    "metrics": {
                        name: {"score": 0.5, "success": True, "details": {}}
                        for name in (*METRICS, "overall")
                    },
                }
            )
    dataset = {"cases": [{"id": "one"}, {"id": "two"}]}
    for root in (source, corrected):
        (root / "dataset.json").write_text(json.dumps(dataset))
    receipt = {
        "schema_version": 2,
        "provider_calls": 0,
        "correction_provenance": build_correction_provenance(
            "structured-extraction", diagnostics.ROOT_CAUSE, diagnostics.CONTROLS
        ),
        "source_code_sha256": {},
        "changed_scores": [],
        "scope": {"providers": ["first", "second"], "logical_results": 4},
    }
    summaries = {}

    def save():
        original = "".join(json.dumps(row) + "\n" for row in rows)
        after = [{**row, "official_metrics": copy.deepcopy(row["metrics"])} for row in rows]
        (source / "results.jsonl").write_text(original)
        (corrected / "original-results.jsonl").write_text(original)
        (corrected / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in after))
        receipt.update(
            input_results_sha256=diagnostics.digest(source / "results.jsonl"),
            output_results_sha256=diagnostics.digest(corrected / "results.jsonl"),
            dataset_sha256=diagnostics.digest(corrected / "dataset.json"),
        )
        (corrected / "teds-correction.json").write_text(json.dumps(receipt))
        for provider in ("first", "second"):
            current = aggregate([row for row in rows if row["provider"] == provider])
            summaries[provider] = {
                "official_aggregate": current,
                "original_official_aggregate": copy.deepcopy(current),
            }
        (corrected / "score-summaries.json").write_text(json.dumps(summaries))

    save()
    return source, corrected, rows, receipt, summaries, save


def test_complete_revision_passes_without_provider_calls(revision):
    source, corrected, *_ = revision
    result = diagnostics.review_teds_correction(source, corrected)
    assert result["status"] == "passed"
    assert result["logical_results"] == 4
    assert result["provider_calls"] == 0
    assert set(result["impacts"]) == {"first", "second"}


@pytest.mark.parametrize(
    "damage",
    ["duplicate_pair", "wrong_scope", "wrong_count", "missing_summary", "nan", "nan_result"],
)
def test_count_alone_cannot_certify_a_revision(revision, damage):
    source, corrected, rows, receipt, summaries, save = revision
    if damage == "duplicate_pair":
        rows[1]["case_id"] = "one"
        save()
    elif damage == "wrong_scope":
        receipt["scope"]["providers"] = ["first", "ghost"]
        save()
    elif damage == "wrong_count":
        receipt["scope"]["logical_results"] = 3
        save()
    elif damage == "nan_result":
        rows[0]["metrics"]["overall"]["score"] = float("nan")
        save()
    else:
        if damage == "missing_summary":
            del summaries["second"]
        else:
            summaries["first"]["official_aggregate"]["scores"]["table_TEDS"] = float("nan")
        (corrected / "score-summaries.json").write_text(json.dumps(summaries))
    with pytest.raises(ValueError):
        diagnostics.review_teds_correction(source, corrected)


def test_reference_inapplicable_metrics_have_zero_population(revision):
    source, corrected, rows, _, _, save = revision
    for row in rows:
        for name in METRICS[1:]:
            row["metrics"][name] = {
                "score": 0,
                "success": False,
                "details": {"availability": "reference_not_applicable"},
            }
    save()
    result = diagnostics.review_teds_correction(source, corrected)
    scores = result["impacts"]["first"]["corrected"]
    assert scores["denominators"]["table_TEDS"] == 0
    assert scores["scores"]["table_TEDS"] == 0


def test_internal_symlink_does_not_count_as_preserved_evidence(revision):
    source, corrected, *_ = revision
    path = corrected / "first-one.md"
    target = corrected / "backing.md"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        diagnostics.review_teds_correction(source, corrected)


def run_cli(monkeypatch, source, corrected):
    monkeypatch.setattr(
        sys, "argv", ["review", "--source", str(source), "--corrected", str(corrected)]
    )
    diagnostics.teds_review_main()


def test_cli_installs_an_audit_readable_by_the_revision_validator(revision, monkeypatch):
    source, corrected, *_ = revision
    run_cli(monkeypatch, source, corrected)
    receipt, audit = diagnostics.validate_teds_revision(corrected)
    assert receipt["provider_calls"] == audit["provider_calls"] == 0
    assert audit["review_script_sha256"] == diagnostics.digest(Path(diagnostics.__file__))
    before = (corrected / "teds-correction-review.json").read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        run_cli(monkeypatch, source, corrected)
    assert (corrected / "teds-correction-review.json").read_bytes() == before


def test_cli_does_not_install_an_audit_if_evidence_changes_during_validation(revision, monkeypatch):
    source, corrected, *_ = revision
    writer = diagnostics.write_review_document

    def concurrent_change(target, result, **kwargs):
        (corrected / "first-one.md").write_text("changed during validation")
        return writer(target, result, **kwargs)

    monkeypatch.setattr(diagnostics, "write_review_document", concurrent_change)
    with pytest.raises(ValueError, match="raw output"):
        run_cli(monkeypatch, source, corrected)
    assert not (corrected / "teds-correction-review.json").exists()
    assert not list(corrected.glob(".result-review-*"))


def test_revision_reader_rejects_a_hash_consistent_but_incomplete_matrix(revision, monkeypatch):
    source, corrected, rows, _, _, save = revision
    run_cli(monkeypatch, source, corrected)
    rows[1]["case_id"] = "one"
    save()
    path = corrected / "teds-correction-review.json"
    audit = json.loads(path.read_text())
    audit.update(
        output_results_sha256=diagnostics.digest(corrected / "results.jsonl"),
        correction_receipt_sha256=diagnostics.digest(corrected / "teds-correction.json"),
    )
    path.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match="matrix"):
        diagnostics.validate_teds_revision(corrected)
