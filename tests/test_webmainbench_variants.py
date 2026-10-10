"""Behavioral controls for keeping both Moli repair cohorts distinct."""
import pytest

from harness.web_fetch.webmainbench_evidence import (
    alias_row,
    copy_alias_artifacts,
    require_matrix,
    require_provider_batch,
)


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


def test_method_matrix_rejects_a_missing_or_duplicate_variant() -> None:
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
