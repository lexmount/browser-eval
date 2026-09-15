"""Collect read-only facts for the independent dynamic-100 audit.

This script deliberately does not decide whether a case belongs in the benchmark
or whether a provider answer should pass.  It binds the material an independent
reviewer needs: final case definitions, source captures, rule-fixture history,
provider-call identity, immutable answer bytes, and the recorded verdict.  The
recorded verdict is emitted separately so a reviewer can inspect the ``blind``
section first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness.web_fetch.metrics import task_passed


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation/dynamic-content-100-moli-webfetch/dataset.json"
DEFAULT_EVIDENCE = (
    Path.home()
    / ".browser-eval/evidence/research/dynamic-100-moli-webfetch-2026-09-15-v1"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def file_fact(path: Path, root: Path) -> dict[str, Any]:
    value = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "bytes": len(value),
        "sha256": sha256_bytes(value),
    }


def load_results(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_rule_history(history_root: Path | None) -> dict[str, dict[str, Any]]:
    if history_root is None:
        return {}
    histories: dict[str, dict[str, Any]] = {}
    sources = (
        (
            "48-case final rule review",
            history_root / "research/dynamic-48-concrete-rule-review-2026-09-14-v4.json",
        ),
        (
            "42-case rule validation",
            history_root / "research/dynamic-42-rule-validation.json",
        ),
        (
            "14-addition rule review",
            history_root / "research/dynamic-100-additions-rule-review-2026-09-15-v1.json",
        ),
    )
    for label, path in sources:
        if not path.exists():
            continue
        document = read_json(path)
        for row in document.get("cases", []):
            identifier = row.get("id")
            if not identifier:
                continue
            validation = row.get("validation") or {}
            if label == "42-case rule validation":
                validation = {
                    "status": "passed" if (
                        row.get("positive_a_pass")
                        and row.get("positive_b_pass")
                        and row.get("all_negatives_rejected")
                    ) else "failed",
                    "positive": [
                        {"fixture": "independent_reference_A", "pass": row.get("positive_a_pass")},
                        {"fixture": "independent_reference_B", "pass": row.get("positive_b_pass")},
                    ],
                    "negative": row.get("negative_tests", []),
                }
            histories[identifier] = {
                "source": label,
                "source_file": path.name,
                "source_sha256": sha256_bytes(path.read_bytes()),
                "document_status": document.get("status") or (
                    "passed" if document.get("all_passed") else None
                ),
                "validation": validation,
                "rule_mode": row.get("rule_mode") or row.get("mode") or row.get("rule_family"),
            }
    return histories


def load_qualification_history(history_root: Path | None) -> dict[str, dict[str, Any]]:
    if history_root is None:
        return {}
    path = history_root / "research/dynamic-100-selection-v3.json"
    if not path.exists():
        return {}
    document = read_json(path)
    values: dict[str, dict[str, Any]] = {}
    for row in document.get("cases", []):
        identifier = row.get("id")
        evidence = row.get("evidence") or {}
        if not identifier or not evidence:
            continue
        values[identifier] = {
            "source_file": path.name,
            "source_sha256": sha256_bytes(path.read_bytes()),
            "observation_summary": evidence.get("observation_summary"),
            "observation_summary_sha256": evidence.get("observation_summary_sha256"),
            "observation_id": evidence.get("observation_id"),
            "capture_a": evidence.get("capture_a"),
            "capture_b": evidence.get("capture_b"),
            "structural_match": evidence.get("structural_match"),
            "mechanism_evidence_basis": row.get("mechanism_evidence_basis"),
            "target_declaration": row.get("target_declaration"),
        }
    return values


def validation_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {"available": False}
    positives = value.get("positive", [])
    negatives = value.get("negative", [])
    negative_results = []
    for row in negatives:
        rejected = row.get("rejected")
        if rejected is None:
            rejected = not bool(row.get("pass"))
        negative_results.append(bool(rejected))
    return {
        "available": True,
        "status": value.get("status"),
        "positive_count": len(positives),
        "positive_passed": sum(bool(row.get("pass")) for row in positives),
        "negative_count": len(negatives),
        "negative_rejected": sum(negative_results),
        "all_positive_passed": bool(positives) and all(bool(row.get("pass")) for row in positives),
        "all_negative_rejected": bool(negatives) and all(negative_results),
        "negative_fixture_names": [
            row.get("fixture") or row.get("name") for row in negatives
        ],
    }


def source_facts(case: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    identifier = case["id"]
    folder = evidence_root / "sources" / identifier
    verification = case.get("source_verification") or {}
    files = []
    if folder.is_dir():
        files = [file_fact(path, evidence_root) for path in sorted(folder.iterdir()) if path.is_file()]
    names = {row["path"].rsplit("/", 1)[-1] for row in files}
    evidence = None
    evidence_path = folder / "evidence.json"
    if evidence_path.exists():
        evidence = read_json(evidence_path)
    initial_summary = None
    initial_summary_path = folder / "initial-http-evidence.json"
    if initial_summary_path.exists():
        initial_summary = read_json(initial_summary_path)
    render_audit = None
    render_audit_path = folder / "render-audit.json"
    if render_audit_path.exists():
        render_audit = read_json(render_audit_path)
    initial_full_names = sorted(name for name in names if name in {
        "initial.html.txt", "initial.txt", "initial-response.bin", "initial-http.bin"
    })
    rendered_names = sorted(name for name in names if name in {"rendered.txt", "reference.txt"})
    captures = {}
    if isinstance(evidence, dict):
        for key in ("capture_a", "capture_b"):
            if isinstance(evidence.get(key), dict):
                captures[key] = evidence[key]
        for key in ("capture_a_target_sha256", "capture_b_target_sha256"):
            if key in evidence:
                captures[key] = evidence[key]
    if not captures:
        for key in ("capture_a", "capture_b"):
            if isinstance(verification.get(key), dict):
                captures[key] = verification[key]
        for key in ("independent_capture_a_sha256", "independent_capture_b_sha256"):
            if key in verification:
                captures[key] = verification[key]
    return {
        "source_directory_present": folder.is_dir(),
        "files": files,
        "full_initial_response_present": bool(initial_full_names),
        "full_initial_response_files": initial_full_names,
        "rendered_or_reference_present": bool(rendered_names),
        "rendered_or_reference_files": rendered_names,
        "evidence_json_present": evidence is not None,
        "initial_http_summary_present": initial_summary is not None,
        "render_audit_present": render_audit is not None,
        "source_verification_state": verification.get("state"),
        "source_verification_method": verification.get("method"),
        "captures": captures,
        "evidence_summary": evidence,
        "initial_http_summary": initial_summary,
        "render_audit_validation": validation_summary(
            (render_audit or {}).get("positive_and_negative_validation")
        ),
    }


def call_facts(
    case: dict[str, Any], record: dict[str, Any], evidence_root: Path, owners: list[dict[str, Any]]
) -> dict[str, Any]:
    artifact = evidence_root / record["artifact_path"]
    artifact_bytes = artifact.read_bytes() if artifact.exists() else b""
    recorded_hash = (record.get("metrics") or {}).get("content_sha256")
    owner_ids = [batch.get("dataset", {}).get("id") for batch in owners]
    return {
        "attempt_id": record.get("attempt_id"),
        "provider": record.get("provider"),
        "case_id_matches": record.get("case_id") == case["id"],
        "url_matches": record.get("url") == case["url"],
        "run": record.get("run"),
        "started_at": record.get("started_at"),
        "completed_at": record.get("completed_at"),
        "elapsed_ms": record.get("elapsed_ms"),
        "request_ok": record.get("ok"),
        "artifact": file_fact(artifact, evidence_root) if artifact.exists() else None,
        "artifact_hash_matches_record": bool(artifact.exists() and recorded_hash == sha256_bytes(artifact_bytes)),
        "execution_owner_count": len(owners),
        "execution_owner_ids": owner_ids,
        "provider_metadata": record.get("provider_metadata"),
        "deadline_overrun": (record.get("metrics") or {}).get("deadline_overrun"),
        "deadline_budget_ms": (record.get("metrics") or {}).get("deadline_budget_ms"),
    }


def compact_rule(case: dict[str, Any]) -> dict[str, Any]:
    contract = case.get("acceptance_contract") or {}
    return {
        "task_kind": case.get("task_kind"),
        "minimum_content_chars": case.get("min_content_chars"),
        "required_fact_count": len(case.get("required_facts") or []),
        "required_pattern_count": len(case.get("required_patterns") or []),
        "forbidden_count": len(case.get("forbidden_any") or []),
        "body_region_count": len((case.get("body_requirements") or {}).get("regions") or []),
        "body_minimum_region_passes": (case.get("body_requirements") or {}).get(
            "minimum_region_passes"
        ),
        "acceptance_schema": contract.get("schema"),
        "minimum_records": contract.get("minimum_records"),
        "stable_anchor_count": len(contract.get("stable_text_anchors") or []),
        "record_hosts": contract.get("hosts") or [],
        "entry_path": contract.get("entry_path"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--history-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset_bytes = args.dataset.read_bytes()
    dataset = json.loads(dataset_bytes)
    result_bytes = (args.evidence / "results.jsonl").read_bytes()
    records = load_results(args.evidence / "results.jsonl")
    execution = read_json(args.evidence / "execution.json")
    batches = execution.get("batches", [])
    histories = load_rule_history(args.history_root)
    qualification_histories = load_qualification_history(args.history_root)
    indexed_records = {record["case_id"]: record for record in records}
    attempt_owners: dict[str, list[dict[str, Any]]] = {}
    for batch in batches:
        for attempt_id in batch.get("attempt_ids", []):
            attempt_owners.setdefault(attempt_id, []).append(batch)

    cases = []
    for ordinal, case in enumerate(dataset["cases"], 1):
        record = indexed_records[case["id"]]
        source = source_facts(case, args.evidence)
        history = histories.get(case["id"])
        blind = {
            "ordinal": ordinal,
            "case_id": case["id"],
            "name": case["name"],
            "url": case["url"],
            "description": case.get("description"),
            "task_prompt": case.get("task_prompt"),
            "rule": compact_rule(case),
            "ground_truth_chars": len(case.get("ground_truth_main_content") or ""),
            "source": source,
            "qualification_history": qualification_histories.get(case["id"]),
            "rule_fixture_history": {
                **(history or {"available": False}),
                "summary": validation_summary((history or {}).get("validation")),
            },
            "call": call_facts(
                case,
                record,
                args.evidence,
                attempt_owners.get(record["attempt_id"], []),
            ),
        }
        cases.append({
            "blind": blind,
            "recorded_after_blind_review": {
                "verdict": "pass" if task_passed(
                    bool(record.get("ok")), record.get("metrics") or {}, case.get("task_kind")
                ) else "fail",
                "failure_category": record.get("failure_category"),
                "diagnosis": record.get("diagnosis"),
                "metrics": record.get("metrics"),
            },
        })

    case_ids = [case["id"] for case in dataset["cases"]]
    urls = [case["url"] for case in dataset["cases"]]
    attempt_ids = [record["attempt_id"] for record in records]
    artifact_count = sum(bool(row["blind"]["call"]["artifact"]) for row in cases)
    artifact_hash_count = sum(row["blind"]["call"]["artifact_hash_matches_record"] for row in cases)
    owner_count = sum(row["blind"]["call"]["execution_owner_count"] == 1 for row in cases)
    try:
        dataset_binding_path = str(args.dataset.relative_to(ROOT))
    except ValueError:
        dataset_binding_path = args.dataset.name
    output = {
        "schema_version": 1,
        "kind": "dynamic_100_independent_audit_facts",
        "review_protocol": "research/dynamic-100-formal-audit-plan.md",
        "bindings": {
            "dataset_path": dataset_binding_path,
            "dataset_sha256": sha256_bytes(dataset_bytes),
            "results_sha256": sha256_bytes(result_bytes),
        },
        "inventory": {
            "cases": len(case_ids),
            "unique_case_ids": len(set(case_ids)),
            "unique_urls": len(set(urls)),
            "attempts": len(records),
            "unique_attempt_ids": len(set(attempt_ids)),
            "artifact_files": artifact_count,
            "artifact_hashes_verified": artifact_hash_count,
            "single_execution_owner": owner_count,
            "methods": sorted({record.get("provider") for record in records}),
            "runs": sorted({record.get("run") for record in records}),
            "execution_batches": len(batches),
            "declared_retries": [
                batch.get("parameters", {}).get("max_failure_retries") for batch in batches
            ],
        },
        "source_inventory": {
            "source_directories": sum(row["blind"]["source"]["source_directory_present"] for row in cases),
            "full_initial_responses": sum(
                row["blind"]["source"]["full_initial_response_present"] for row in cases
            ),
            "rendered_or_references": sum(
                row["blind"]["source"]["rendered_or_reference_present"] for row in cases
            ),
            "render_audits": sum(row["blind"]["source"]["render_audit_present"] for row in cases),
            "history_rule_validations": sum(
                row["blind"]["rule_fixture_history"].get("summary", {}).get("available", False)
                for row in cases
            ),
        },
        "recorded_verdicts": dict(Counter(
            row["recorded_after_blind_review"]["verdict"] for row in cases
        )),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
