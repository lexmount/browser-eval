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
from scripts.assemble_webmainbench_seven_methods import drop_inherited_teds_revision
from scripts.compose_webmainbench_seven_method_correction import compose
from scripts.replace_webmainbench_variant import raw_row, replace


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


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_complete_variant_replacement_preserves_other_methods_and_rebinds_evidence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, candidate, output = (tmp_path / name for name in ("source", "candidate", "output"))
    shard = candidate / "moli"
    source.mkdir()
    shard.mkdir(parents=True)
    case_id = "case-1"
    providers = ["moli", "moli-content-fixed"]
    source_rows, source_calls = [], []
    for provider in providers:
        artifact = source / f"artifacts/{provider}/{case_id}.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("old " + provider)
        physical = source / f"artifacts/{provider}/{case_id}-p01.txt"
        physical.write_text("old " + provider)
        source_rows.append({"provider": provider, "case_id": case_id,
                            "attempt_id": f"{provider}__{case_id}__old",
                            "artifact_path": str(artifact.relative_to(source)),
                            "output_sha256": _sha(artifact), "metrics": {}})
        source_calls.append({"provider": provider, "track_id": case_id,
                             "attempt_id": f"{provider}__{case_id}__p01-old",
                             "physical_artifact_path": str(physical.relative_to(source)),
                             "output_sha256": _sha(physical)})
    _write_jsonl(source / "results.jsonl", source_rows)
    _write_jsonl(source / "original-results.jsonl", source_rows)
    _write_jsonl(source / "physical-attempts.jsonl", source_calls)
    (source / "dataset.json").write_text(json.dumps({
        "cases": [{"id": case_id}], "coverage_plan": {"static_html": providers}}))
    (source / "score-summaries.json").write_text(json.dumps({
        provider: {"provider": provider, "official_aggregate": {}} for provider in providers}))
    (source / "execution.json").write_text(json.dumps({
        "batches": [{"provider": provider} for provider in providers]}))
    (source / "seven-method-binding.json").write_text(json.dumps({
        "variants": [{"provider": "moli-content-fixed"}],
        "results_sha256": _sha(source / "results.jsonl"),
        "physical_attempts_sha256": _sha(source / "physical-attempts.jsonl"),
        "physical_calls": len(source_calls),
    }))

    measured_artifact = shard / f"artifacts/moli/{case_id}.md"
    measured_artifact.parent.mkdir(parents=True)
    measured_artifact.write_text("new content")
    measured_physical = shard / f"artifacts/moli/{case_id}-p01.txt"
    measured_physical.write_text("new content")
    measured = {"provider": "moli", "track_id": case_id,
                "attempt_id": f"moli__{case_id}__new",
                "artifact_path": str(measured_artifact.relative_to(shard)),
                "output_sha256": _sha(measured_artifact),
                "metrics": {"overall": {"score": 1}},
                "official_metrics": {"overall": {"score": 0}}}
    call = {"provider": "moli", "track_id": case_id,
            "attempt_id": f"moli__{case_id}__p01-new",
            "physical_artifact_path": str(measured_physical.relative_to(shard)),
            "output_sha256": _sha(measured_physical)}
    _write_jsonl(shard / "scored-results.jsonl", [measured])
    _write_jsonl(shard / "physical-attempts.jsonl", [call])
    (shard / "score-summary.json").write_text(json.dumps({"official_aggregate": {}}))
    (shard / "execution.json").write_text(json.dumps({"attempts": 1}))

    profile_path = Path("evaluation/webmainbench-full/moli-content-fixed-report.json").resolve()
    profile = json.loads(profile_path.read_text())
    cohort = {"profile_sha256": _sha(profile_path), "preflight": {"scope": 545},
              "postflight": {"scope": 545}, "binary_source_commit": profile["source_commit"],
              "measured_binary_sha256": profile["binary_sha256"], "providers": ["cohort-ok"]}
    (candidate / "cohort-certificate.json").write_text(json.dumps(cohort))
    scoring = {"cohort_certificate_sha256": _sha(candidate / "cohort-certificate.json"),
               "providers": ["score-ok"]}
    (candidate / "scoring-certificate.json").write_text(json.dumps(scoring))
    monkeypatch.setattr("scripts.replace_webmainbench_variant.verify_shard",
                        lambda *args: "cohort-ok")
    monkeypatch.setattr("scripts.replace_webmainbench_variant.verify_score",
                        lambda *args: "score-ok")

    replace(source, candidate, tmp_path / "scorer", profile_path,
            "moli-content-fixed", output)

    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    by_provider = {row["provider"]: row for row in rows}
    assert by_provider["moli"]["attempt_id"] == "moli__case-1__old"
    assert by_provider["moli-content-fixed"]["attempt_id"] == "moli-content-fixed__case-1__new"
    assert "official_metrics" not in by_provider["moli-content-fixed"]
    assert (output / "artifacts/moli-content-fixed/case-1.md").read_text() == "new content"
    binding = json.loads((output / "seven-method-binding.json").read_text())
    assert binding["physical_calls"] == 2
    assert binding["results_sha256"] == _sha(output / "results.jsonl")


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
