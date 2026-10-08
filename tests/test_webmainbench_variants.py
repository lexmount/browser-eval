"""Behavioral controls for keeping both Moli repair cohorts distinct."""
import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.webmainbench_evidence import (
    alias_row,
    copy_alias_artifacts,
    require_matrix,
    require_provider_batch,
)
from scripts.compose_webmainbench_seven_method_correction import compose


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


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freeze_case_correction(root: Path, source: Path, providers: list[str],
                            originals: dict[str, dict], case_id: str) -> None:
    rows, calls = [], []
    for provider in providers:
        artifact = root / f"artifacts/{provider}/{case_id}.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("corrected " + provider)
        physical = root / f"artifacts/{provider}/{case_id}-p01.txt"
        physical.write_text("corrected " + provider)
        old = originals[provider]
        rows.append({**old, "attempt_id": f"{provider}__{case_id}__new",
                     "supersedes_attempt_id": old["attempt_id"],
                     "artifact_path": str(artifact.relative_to(root)),
                     "output_sha256": _sha(artifact), "physical_attempt_count": 1})
        calls.append({"provider": provider, "track_id": case_id,
                      "attempt_id": f"{provider}__{case_id}__p01-new",
                      "physical_artifact_path": str(physical.relative_to(root)),
                      "output_sha256": _sha(physical)})
    _write_jsonl(root / "results.jsonl", rows)
    _write_jsonl(root / "physical-attempts.jsonl", calls)
    plan = {"providers": providers, "case_id": case_id,
            "parent_results_sha256": _sha(source / "results.jsonl")}
    (root / "certificate.json").write_text(json.dumps({
        "kind": "webmainbench_origin_correction_v1", "preflight": plan,
        "postflight": plan, "results_sha256": _sha(root / "results.jsonl"),
        "physical_sha256": _sha(root / "physical-attempts.jsonl")}))


def test_variant_only_case_correction_composes_with_shared_and_prior_methods(
        tmp_path: Path) -> None:
    base = ["moli", "lightpanda", "obscura", "chromium", "kitesurf"]
    providers = base + ["moli-pr785-fixed", "moli-content-fixed"]
    case_id = "case-1"
    originals = {
        provider: {"provider": provider, "case_id": case_id,
                   "attempt_id": f"{provider}__{case_id}__old",
                   "input_html_sha256": "input", "reference_sha256": "reference",
                   "provider_metadata": {"binary_sha256": provider}}
        for provider in providers
    }
    seven = tmp_path / "seven"
    prior_source = tmp_path / "prior-source"
    content_source = tmp_path / "content-source"
    for root, selected in ((seven, providers),
                           (prior_source, base + ["moli-pr785-fixed"]),
                           (content_source, ["moli-content-fixed"])):
        root.mkdir()
        _write_jsonl(root / "results.jsonl", [originals[p] for p in selected])
    prior = tmp_path / "prior-correction"
    content = tmp_path / "content-correction"
    prior.mkdir(); content.mkdir()
    _freeze_case_correction(prior, prior_source, base + ["moli-pr785-fixed"], originals, case_id)
    _freeze_case_correction(content, content_source, ["moli-content-fixed"], originals, case_id)

    output = tmp_path / "composite"
    compose(seven, prior_source, prior, content_source, content, output)

    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    calls = [json.loads(line) for line in (output / "physical-attempts.jsonl").read_text().splitlines()]
    assert {row["provider"] for row in rows} == set(providers)
    assert {row["provider"] for row in calls} == set(providers)
    assert len(rows) == len(calls) == 7
    assert json.loads((output / "certificate.json").read_text())["preflight"]["providers"] == providers
