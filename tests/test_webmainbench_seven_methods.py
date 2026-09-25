"""Behavioral controls for keeping both Moli repair cohorts distinct."""
from scripts.assemble_webmainbench_seven_methods import alias_row, require_matrix

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
