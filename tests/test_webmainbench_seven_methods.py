"""Behavioral controls for keeping both Moli repair cohorts distinct."""
from scripts.assemble_webmainbench_seven_methods import (
    alias_row,
    copy_alias_artifacts,
    drop_inherited_teds_revision,
    require_matrix,
    require_provider_batch,
)
from scripts.replace_webmainbench_variant import raw_row

import pytest


def test_alias_row_rewrites_every_provider_owned_identity() -> None:
    row = {
        "provider": "moli-fixed",
        "attempt_id": "moli-fixed__case-1__r001",
        "artifact_path": "artifacts/moli-fixed/case-1.md",
        "physical_artifact_path": "artifacts/moli-fixed/case-1-p01.txt",
        "supersedes_attempt_id": "moli-fixed__case-1__old",
    }
    aliased = alias_row(row, "moli-fixed", "moli-content-fixed")
    assert row["provider"] == "moli-fixed"
    assert aliased == {
        "provider": "moli-content-fixed",
        "attempt_id": "moli-content-fixed__case-1__r001",
        "artifact_path": "artifacts/moli-content-fixed/case-1.md",
        "physical_artifact_path": "artifacts/moli-content-fixed/case-1-p01.txt",
        "supersedes_attempt_id": "moli-content-fixed__case-1__old",
    }


def test_seven_method_matrix_rejects_a_missing_or_duplicate_variant() -> None:
    providers = ["moli", "moli-pr785-fixed", "moli-content-fixed"]
    case_ids = {"case-1", "case-2"}
    complete = [
        {"provider": provider, "case_id": case_id,
         "attempt_id": f"{provider}__{case_id}__r001"}
        for provider in providers for case_id in case_ids
    ]
    require_matrix(complete, providers, case_ids)
    with pytest.raises(ValueError, match="incomplete"):
        require_matrix(complete[:-1], providers, case_ids)
    duplicated = [dict(row) for row in complete]
    duplicated[-1]["attempt_id"] = duplicated[0]["attempt_id"]
    with pytest.raises(ValueError, match="identity"):
        require_matrix(duplicated, providers, case_ids)


def test_alias_copy_requires_a_bound_output_hash(tmp_path) -> None:
    with pytest.raises(ValueError, match="missing output_sha256"):
        copy_alias_artifacts(
            tmp_path,
            tmp_path / "out",
            [{"attempt_id": "moli-fixed__case-1__p01", "physical_artifact_path": "x"}],
            "moli-fixed",
            "moli-content-fixed",
        )


def test_execution_requires_exactly_one_batch_for_each_variant() -> None:
    batch = {"provider": "moli-fixed", "attempts": 545}
    selected = require_provider_batch({"batches": [batch]}, "moli-fixed")
    assert selected == batch
    assert selected is not batch
    with pytest.raises(ValueError, match="found 0"):
        require_provider_batch({"batches": []}, "moli-fixed")
    with pytest.raises(ValueError, match="found 2"):
        require_provider_batch({"batches": [batch, dict(batch)]}, "moli-fixed")


def test_new_matrix_drops_partial_scope_teds_receipts(tmp_path) -> None:
    for name in ("teds-correction.json", "teds-correction-review.json",
                 "original-results.jsonl", "keep.json"):
        (tmp_path / name).write_text("frozen")
    (tmp_path / "correction-source").mkdir()
    (tmp_path / "correction-source/scorer.py").write_text("frozen")

    drop_inherited_teds_revision(tmp_path)

    assert (tmp_path / "keep.json").read_text() == "frozen"
    assert not (tmp_path / "teds-correction.json").exists()
    assert not (tmp_path / "teds-correction-review.json").exists()
    assert not (tmp_path / "original-results.jsonl").exists()
    assert not (tmp_path / "correction-source").exists()


def test_replacement_raw_row_discards_every_prior_score_snapshot() -> None:
    row = {
        "metrics": {"overall": {"score": 0.5}},
        "official_metrics": {"overall": {"score": 0.9}},
        "pre_scoring_correction_metrics": {"overall": {"score": 0.1}},
        "provider": "moli-content-fixed",
    }
    cleaned = raw_row(row)
    assert cleaned == {
        "metrics": {"overall": {"score": 0.5}},
        "provider": "moli-content-fixed",
    }
    assert "official_metrics" in row
