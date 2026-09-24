import hashlib
import json

import pytest

from harness.web_fetch import local_audit


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_local_audit_requires_complete_inventory_and_review_lineage(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = tmp_path / "evidence"
    source.mkdir()
    receipt_relative = "research/local-audit.json"
    dataset = {
        "comparison": {
            "publication_state": "local_audit_only_not_for_release",
            "local_audit_receipt": receipt_relative,
        },
        "cases": [{"id": "case-1", "task_kind": "body"}],
    }
    record = {
        "attempt_id": "attempt-1", "case_id": "case-1", "ok": True,
        "metrics": {"content_pass": True},
    }
    _write(source / "dataset.json", dataset)
    (source / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    supplement_dataset = "a" * 64
    supplement_results = "b" * 64
    review = {
        "schema_version": 1,
        "review_protocol": {"scope": "supplement-25-local-audit"},
        "bindings": {
            "dataset_sha256": _digest(source / "dataset.json"),
            "results_sha256": _digest(source / "results.jsonl"),
            "supplement_dataset_sha256": supplement_dataset,
            "supplement_results_sha256": supplement_results,
        },
        "attempts": [{
            "attempt_id": "attempt-1", "machine_verdict": "pass",
            "agent_verdict": "pass", "final_verdict": "pass",
        }],
        "total_attempts": 1, "expected_attempts": 1, "reviewed_attempts": 1,
        "accepted_unreviewed_passes": 0, "disagreements": 0, "unresolved": 0,
        "prior_75_audit_records_retained": True,
    }
    _write(source / "result-review.json", review)
    input_dataset = "c" * 64
    input_results = "d" * 64
    lineage = {
        "local-evidence/merge-receipt.json": {
            "sources": {
                "75": {"dataset_sha256": "e" * 64, "results_sha256": "f" * 64,
                       "cases": 0},
                "25": {"dataset_sha256": supplement_dataset,
                       "results_sha256": supplement_results, "cases": 1},
            },
            "output": {"dataset.json": input_dataset, "results.jsonl": input_results,
                       "cases": 1, "results": 1},
            "checks": {"complete": True},
        },
        "local-evidence/dynamic-25-full-result-audit.json": {
            "bindings": {"dataset_sha256": supplement_dataset,
                         "results_sha256": supplement_results},
            "cases": [{}], "provider_calls_during_review": 0,
            "verdict_changes_after_current_scorer": 0,
        },
        "local-evidence/failure-classification-correction.json": {
            "bindings": {"dataset_sha256": input_dataset,
                         "results_after_sha256": input_results},
            "invariants": {"verdicts_changed": False},
        },
        "local-evidence/cohorts/75/native-failure-audit.json": {
            "dataset_sha256": "e" * 64, "results_sha256": "f" * 64,
            "original_calls_added": 0,
        },
        "local-evidence/cohorts/75/qualification-review.json": {
            "summary": {"supports_dynamic_qualification": 0},
        },
        "local-evidence/local-audit-correction.json": {
            "output_dataset_sha256": _digest(source / "dataset.json"),
            "output_results_sha256": _digest(source / "results.jsonl"),
            "provider_calls": 0, "task_verdict_changes": [],
        },
        "local-evidence/scoring-plumbing-correction.json": {
            "input_dataset_sha256": _digest(source / "dataset.json"),
            "input_results_sha256": _digest(source / "results.jsonl"),
            "output_dataset_sha256": _digest(source / "dataset.json"),
            "output_results_sha256": _digest(source / "results.jsonl"),
            "scoring_source_sha256": "g" * 64,
            "provider_calls": 0, "task_verdict_changes": [],
        },
    }
    bound_receipts = {}
    for relative, document in lineage.items():
        path = source / relative
        _write(path, document)
        bound_receipts[relative] = _digest(path)
    manifest_path = source / local_audit.MANIFEST_NAME
    _write(manifest_path, local_audit.local_audit_manifest(source))
    receipt = {
        "status": "local_audit_complete",
        "bindings": {
            "input_dataset_sha256": input_dataset,
            "input_results_sha256": input_results,
            "dataset_sha256": _digest(source / "dataset.json"),
            "results_sha256": _digest(source / "results.jsonl"),
            "scoring_source_sha256": "g" * 64,
        },
        "scope": {"cases": 1, "results": 1},
        "evidence": {
            "manifest_path": local_audit.MANIFEST_NAME,
            "manifest_sha256": _digest(manifest_path),
            "result_review_sha256": _digest(source / "result-review.json"),
        },
        "review": {
            "supplement_cases_reviewed": 1,
            "supplement_dataset_sha256": supplement_dataset,
            "supplement_results_sha256": supplement_results,
            "post_correction_receipt": "local-evidence/scoring-plumbing-correction.json",
            "bound_receipts": bound_receipts,
        },
    }
    _write(root / receipt_relative, receipt)
    monkeypatch.setattr(local_audit, "ROOT", root)
    monkeypatch.setattr(local_audit, "_require_repository_bindings", lambda *_: None)

    validated = local_audit.require_local_audit(source, dataset, [record])
    assert validated["reviewed_attempts"] == 1

    prior = source / "local-evidence/cohorts/75/qualification-review.json"
    _write(prior, {"reviewed": False})
    with pytest.raises(ValueError, match="inventory or hashes changed"):
        local_audit.require_local_audit(source, dataset, [record])
