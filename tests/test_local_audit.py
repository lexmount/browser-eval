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


def test_new_local_matrix_is_complete_and_keeps_logs_without_claiming_review(tmp_path, monkeypatch):
    from pathlib import Path
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers import build_provider
    from harness.web_fetch.runner import run_evaluation
    from harness.web_fetch.scoring import SCORING_SOURCE_SHA256
    from harness.web_fetch.report_build import build_report
    canonical = tmp_path / "repo/evaluation/dataset.json"
    # Use a genuine committed contract to exercise the production scorer.
    actual = json.loads(Path("evaluation/dynamic-content-100-moli-webfetch/dataset.json").read_text())["cases"][0]
    canonical_data = {"tool": "web_fetch", "cases": [actual]}
    _write(canonical, canonical_data)
    dataset = {**canonical_data, "id": "local-fixture", "name": "Local fixture",
               "coverage_plan": {"dynamic": ["fixture"]},
               "comparison": {"kind": "uniform_native_local_measurement",
                   "publication_state": "local_audit_only_not_for_release"}}
    inputs = tmp_path / "inputs/dataset.json"
    _write(inputs, dataset)
    config = tmp_path / "inputs/providers.json"
    _write(config, {"providers": {"fixture": {"type": "fixture"}}})
    _, cases = load_dataset(inputs)
    evidence = tmp_path / "evidence"
    records, _ = run_evaluation(output_dir=evidence, dataset_meta=dataset,
        dataset_path=inputs, cases=cases, providers=[build_provider("fixture", {"type": "fixture"})],
        provider_config_path=config, runs=1, timeout=45, delay=0)
    batch = json.loads((evidence / "manifest.json").read_text())
    _write(evidence / "execution.json", {"batches": [batch]})
    _write(evidence / "local-run-contract.json", {
        "schema_version": 1, "kind": "uniform_native_local_measurement",
        "dataset_path": "evaluation/dataset.json", "dataset_sha256": _digest(canonical),
        "scoring_source_sha256": SCORING_SOURCE_SHA256, "providers": ["fixture"],
        "runs": 1, "timeout_seconds": 45, "max_failure_retries": 0})
    contract_path = evidence / "local-run-contract.json"
    contract = json.loads(contract_path.read_text())
    contract.update(provider_config_sha256=batch["provider_config_sha256"],
                    runtime_source_sha256=batch["runtime_source_sha256"])
    _write(contract_path, contract)
    dataset["comparison"]["local_run_contract_sha256"] = _digest(contract_path)
    _write(evidence / "dataset.json", dataset)
    monkeypatch.setattr(local_audit, "ROOT", tmp_path / "repo")
    assert local_audit.require_local_audit(evidence, dataset, records) is None
    output = tmp_path / "report"
    build_report(evidence, output, local_audit=True)
    assert "本地实测版" in (output / "index.html").read_text()
    detail = (output / "attempts" / (records[0]["attempt_id"] + ".html")).read_text()
    assert "运行日志与证据" in detail
    assert not (output / "result-review.json").exists()
    assert json.loads((output / "report-manifest.json").read_text())["certified"] is False
    with pytest.raises(ValueError, match="incomplete"):
        local_audit.require_local_audit(evidence, dataset, [])
    changed = json.loads(json.dumps(dataset))
    changed["cases"][0]["url"] = "https://other.test/"
    with pytest.raises(ValueError, match="changed the committed cases"):
        local_audit.require_local_audit(evidence, changed, records)
