"""Verify and summarize one complete fixed-native two-build cohort."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.record_io import parse_jsonl
from harness.web_fetch.artifacts import (
    diagnostic_log_binding,
    verify_public_call_log,
    verify_record_artifacts,
)
from harness.web_fetch.reference_lifecycle import (
    is_diagnostic_evidence,
    require_verified_reference_checks,
)
from harness.web_fetch.retrieval_hints import parse_retrieval_hints
from harness.web_fetch.safety import reject_evidence_symlinks
from harness.web_fetch.task_outputs import require_native_task_routes

_IDENTITY_FIELDS = {
    "label",
    "source_url",
    "binary",
    "expected_reported_version",
    "expected_binary_sha256",
}


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Comparison input is missing or unsafe: " + str(path))
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Comparison input must be a JSON object: " + str(path))
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _native_contract(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key not in _IDENTITY_FIELDS}


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Comparison call has no timestamp")
    return datetime.fromisoformat(value)


def _relation(before: bool, after: bool) -> str:
    return {
        (True, True): "pass_to_pass",
        (True, False): "pass_to_fail",
        (False, True): "fail_to_pass",
        (False, False): "fail_to_fail",
    }[before, after]


def _scalar_metric_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys = set(before) | set(after)
    return sorted(
        key
        for key in keys
        if isinstance(before.get(key), (str, int, float, bool, type(None)))
        and isinstance(after.get(key), (str, int, float, bool, type(None)))
        and before.get(key) != after.get(key)
    )


def _method_result(row: dict[str, Any], passed: bool) -> dict[str, Any]:
    metadata = row.get("provider_metadata") or {}
    call_log = metadata.get("call_log") or {}
    metrics = row.get("metrics") or {}
    return {
        "attempt_id": row["attempt_id"],
        "request_ok": row.get("ok") is True,
        "task_pass": passed,
        "status_code": row.get("status_code"),
        "error_type": row.get("error_type"),
        "content_sha256": metrics.get("content_sha256"),
        "content_chars": metrics.get("content_chars"),
        "elapsed_ms": row.get("elapsed_ms"),
        "route": metadata.get("route") or metadata.get("extractor"),
        "artifact_path": row.get("artifact_path"),
        "call_log_path": call_log.get("path"),
    }


def audit_fixed_native_comparison(
    evidence_root: Path,
    *,
    source_dataset: Path,
    provider_config: Path,
    run_receipt: Path,
    output_json: Path,
    output_markdown: Path,
    stable_provider: str,
    candidate_provider: str,
) -> dict[str, Any]:
    """Fail closed on cohort drift, then write an exhaustive paired locator.

    This is an evidence audit, not an independent result review.  It classifies
    deterministic result differences without assigning a product root cause.
    """
    evidence_root = evidence_root.resolve()
    reject_evidence_symlinks(evidence_root)
    for value in (stable_provider, candidate_provider):
        safe_identifier(value)
    if stable_provider == candidate_provider:
        raise ValueError("Comparison methods must be distinct")

    dataset_path = contained_path(evidence_root, "dataset.json", reject_symlinks=True)
    results_path = contained_path(evidence_root, "results.jsonl", reject_symlinks=True)
    execution_path = contained_path(evidence_root, "execution.json", reject_symlinks=True)
    dataset = _read_json(dataset_path)
    source = _read_json(source_dataset)
    config = _read_json(provider_config)
    receipt = _read_json(run_receipt)
    execution = _read_json(execution_path)
    results_raw = results_path.read_bytes()
    records = parse_jsonl(results_raw)

    providers = [stable_provider, candidate_provider]
    source_sha256 = _sha256(source_dataset)
    expected_count = receipt.get("inputs", {}).get("case_count")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("inputs", {}).get("dataset_sha256") != source_sha256
        or not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count < 1
    ):
        raise ValueError("Run receipt does not bind the original source dataset")
    source_ids = [case.get("id") for case in source.get("cases", [])]
    case_ids = [case.get("id") for case in dataset.get("cases", [])]
    if (
        len(source_ids) != expected_count
        or len(set(source_ids)) != expected_count
        or case_ids != source_ids
        or dataset.get("comparison", {}).get("input_dataset_sha256") != source_sha256
        or dataset.get("comparison", {}).get("retrieval_mode")
        != "native_task_family_completion"
    ):
        raise ValueError("Measured dataset differs from the bound native source scope")
    for case in dataset["cases"]:
        hints = case.get("retrieval_hints")
        if case.get("track", "web") == "dynamic":
            try:
                parse_retrieval_hints("dynamic", hints, identifier=case["id"])
            except ValueError as error:
                raise ValueError("Dynamic comparison case did not use a registered native contract") from error
            if hints.get("version") != 3 or hints.get("mode") != "native":
                raise ValueError("Dynamic comparison case did not use native completion")
        elif hints:
            raise ValueError("Fixed-native comparison contains per-case retrieval hints")

    configured = config.get("providers")
    if not isinstance(configured, dict) or set(configured) != set(providers):
        raise ValueError("Provider configuration does not contain the exact comparison pair")
    for provider in providers:
        item = configured[provider]
        if (
            item.get("type") != "moli_webfetch"
            or item.get("wait_until") != "done"
            or item.get("dynamic_wait_until") != "domstable"
            or item.get("uniform_native_parameters") is not True
            or item.get("block_private_networks") is not True
            or item.get("dns_mode", "system") != "system"
            or item.get("log_level") != "debug"
            or item.get("retain_complete_diagnostic_log") is not True
            or item.get("max_diagnostic_bytes") != 100_000_000
        ):
            raise ValueError(provider + ": provider configuration is not fixed native")
    if _native_contract(configured[stable_provider]) != _native_contract(
        configured[candidate_provider]
    ):
        raise ValueError("Stable and candidate use different non-identity parameters")

    declared = receipt.get("execution", {})
    call_order = declared.get("call_order")
    runtime_source_sha256 = declared.get("runtime_source_sha256")
    evidence_state = declared.get("evidence_state")
    if declared != {
        "workers": 1,
        "call_order": call_order,
        "runs": 1,
        "caller_retries": 0,
        "retrieval_mode": "native",
        "runtime_source_sha256": runtime_source_sha256,
        "evidence_state": evidence_state,
    } or call_order not in {"provider", "case-alternating"} or (
        not isinstance(runtime_source_sha256, str)
        or len(runtime_source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in runtime_source_sha256)
    ) or evidence_state not in {"certified", "diagnostic"}:
        raise ValueError("Run receipt does not declare the fixed comparison protocol")
    identities = {
        stable_provider: receipt.get("stable", {}),
        candidate_provider: receipt.get("candidate", {}),
    }
    for provider, identity in identities.items():
        expected_hash = configured[provider].get("expected_binary_sha256")
        expected_version = configured[provider].get("expected_reported_version")
        if (
            identity.get("binary_sha256") != expected_hash
            or identity.get("reported_version") != expected_version
        ):
            raise ValueError(provider + ": receipt and configured identity differ")

    aggregate = execution.get("aggregate", {})
    batches = execution.get("batches")
    expected_batch_count = 1 if call_order == "case-alternating" else 2
    if (
        aggregate.get("providers") != sorted(providers)
        or aggregate.get("case_ids") != case_ids
        or aggregate.get("batch_count") != expected_batch_count
        or aggregate.get("call_order") != call_order
        or not isinstance(batches, list)
        or len(batches) != expected_batch_count
    ):
        raise ValueError("Execution boundary does not describe the declared comparison batches")
    batch_attempt_ids: list[str] = []
    for index, batch in enumerate(batches):
        parameters = batch.get("parameters", {})
        batch_providers = [item.get("name") for item in batch.get("providers", [])]
        expected_batch_providers = providers if call_order == "case-alternating" else [providers[index]]
        expected_scope = (
            "serial_alternating_providers_per_case"
            if call_order == "case-alternating"
            else "providers_parallel_cases_sequential_per_provider"
        )
        if (
            batch_providers != expected_batch_providers
            or parameters.get("runs") != 1
            or parameters.get("max_failure_retries") != 0
            or parameters.get("call_order") != call_order
            or parameters.get("provider_concurrency") != 1
            or parameters.get("concurrency_scope") != expected_scope
            or batch.get("unexecuted")
            or not batch.get("completed_at")
            or batch.get("provider_config_sha256") != _sha256(provider_config)
            or batch.get("runtime_source_sha256") != runtime_source_sha256
        ):
            raise ValueError("Physical batch differs from the declared comparison protocol")
        batch_identities = {
            item["name"]: item.get("implementation_identity")
            for item in batch["providers"]
        }
        for provider in expected_batch_providers:
            if batch_identities[provider] != {
                "reported_version": configured[provider]["expected_reported_version"],
                "binary_sha256": configured[provider]["expected_binary_sha256"],
            }:
                raise ValueError(provider + ": batch implementation identity differs")
        if batch.get("attempt_count") != len(batch.get("attempt_ids", [])):
            raise ValueError("Physical batch attempt inventory is incomplete")
        batch_attempt_ids.extend(batch.get("attempt_ids", []))

    expected_keys = {(provider, case_id, 1) for provider in providers for case_id in case_ids}
    actual_keys = [(row.get("provider"), row.get("case_id"), row.get("run")) for row in records]
    if len(records) != expected_count * 2 or Counter(actual_keys) != Counter(expected_keys):
        raise ValueError("Comparison result matrix is incomplete or duplicated")
    attempt_ids = [row.get("attempt_id") for row in records]
    if (
        len(set(attempt_ids)) != len(attempt_ids)
        or len(batch_attempt_ids) != len(records)
        or Counter(batch_attempt_ids) != Counter(attempt_ids)
    ):
        raise ValueError("Comparison attempt inventory is incomplete or duplicated")

    diagnostic_evidence = is_diagnostic_evidence(dataset) or any(
        is_diagnostic_evidence(batch) for batch in batches
    )
    if diagnostic_evidence != (evidence_state == "diagnostic"):
        raise ValueError("Run receipt and evidence state differ")
    require_verified_reference_checks(
        batches, cases=dataset, records=records, source=evidence_root,
        allow_diagnostic=evidence_state == "diagnostic",
    )
    require_native_task_routes(dataset["cases"], records)
    native_cases = {case['id']: case for case in dataset['cases']}
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in records:
        provider = row["provider"]
        expected = configured[provider]
        metadata = row.get("provider_metadata") or {}
        observed_identity = {
            "binary_sha256": metadata.get("binary_sha256"),
            "reported_version": metadata.get("reported_version"),
        }
        expected_identity = {
            "binary_sha256": expected["expected_binary_sha256"],
            "reported_version": expected["expected_reported_version"],
        }
        # An outer deadline can return before the provider thread yields its
        # metadata.  The completed batch still binds the executable identity;
        # every call that did return an identity must repeat that exact pin.
        missing_timeout_identity = (
            row.get("ok") is False
            and row.get("error_type") in {"timeout", "network"}
            and not any(observed_identity.values())
        )
        diagnostic = diagnostic_log_binding(metadata)
        if (
            row.get("provider_type") != "moli_webfetch"
            or (not missing_timeout_identity and observed_identity != expected_identity)
            or "retry" in metadata
            or diagnostic is None
            or diagnostic["bytes"] > expected["max_diagnostic_bytes"]
            or metadata.get("diagnostic_log_truncated") is True
        ):
            raise ValueError(provider + "/" + row["case_id"] + ": call identity or retry policy differs")
        verify_record_artifacts(evidence_root, row, native_cases=native_cases)
        binding = metadata.get("call_log")
        log = json.loads(verify_public_call_log(evidence_root, binding).read_bytes())
        if log.get("identity", {}).get("physical_attempt") != 1:
            raise ValueError("Comparison contains a retry physical call")
        indexed[provider, row["case_id"]] = row

    chronological = sorted(records, key=lambda row: (_timestamp(row["started_at"]), row["attempt_id"]))
    expected_order: list[tuple[str, str]] = []
    if call_order == "case-alternating":
        for index, case_id in enumerate(case_ids):
            order = providers if index % 2 == 0 else providers[::-1]
            expected_order.extend((provider, case_id) for provider in order)
    else:
        expected_order = [
            (provider, case_id) for provider in providers for case_id in case_ids
        ]
    if [(row["provider"], row["case_id"]) for row in chronological] != expected_order:
        raise ValueError("Recorded calls do not follow the predeclared comparison order")
    for before, after in zip(chronological, chronological[1:]):
        if _timestamp(before["completed_at"]) > _timestamp(after["started_at"]):
            raise ValueError("Comparison calls overlap; serial execution is unproven")

    cases = {case["id"]: case for case in dataset["cases"]}
    relation_counts: Counter[str] = Counter()
    by_track: dict[str, Counter[str]] = defaultdict(Counter)
    paired = []
    for case_id in case_ids:
        case = cases[case_id]
        before = indexed[stable_provider, case_id]
        after = indexed[candidate_provider, case_id]
        kind = case.get("task_kind", "body")
        before_pass = task_passed(before["ok"], before["metrics"], kind)
        after_pass = task_passed(after["ok"], after["metrics"], kind)
        relation = _relation(before_pass, after_pass)
        relation_counts[relation] += 1
        by_track[case.get("track", "web")][relation] += 1
        changes = _scalar_metric_changes(before.get("metrics", {}), after.get("metrics", {}))
        paired.append({
            "case_id": case_id,
            "track": case.get("track", "web"),
            "task_kind": kind,
            "call_order": [
                provider for provider, ordered_case in expected_order if ordered_case == case_id
            ],
            "relation": relation,
            "stable": _method_result(before, before_pass),
            "candidate": _method_result(after, after_pass),
            "differences": {
                "output_changed": before["metrics"].get("content_sha256")
                != after["metrics"].get("content_sha256"),
                "request_outcome_changed": (
                    before.get("ok"), before.get("status_code"), before.get("error_type")
                ) != (
                    after.get("ok"), after.get("status_code"), after.get("error_type")
                ),
                "changed_scalar_metrics": changes,
            },
        })

    summary = {
        "schema_version": 1,
        "kind": "fixed_native_two_build_comparison_audit",
        "scope": {
            "case_count": expected_count,
            "result_count": len(records),
            "stable_provider": stable_provider,
            "candidate_provider": candidate_provider,
            "source_dataset_sha256": source_sha256,
            "call_order": (
                "serial_case_alternating"
                if call_order == "case-alternating"
                else "serial_provider_batches"
            ),
            "runs": 1,
            "caller_retries": 0,
            "evidence_state": evidence_state,
        },
        "bindings": {
            "measured_dataset_sha256": _sha256(dataset_path),
            "results_sha256": hashlib.sha256(results_raw).hexdigest(),
            "execution_sha256": _sha256(execution_path),
            "provider_config_sha256": _sha256(provider_config),
            "run_receipt_sha256": _sha256(run_receipt),
            "runtime_source_sha256": runtime_source_sha256,
        },
        "identities": {
            provider: {
                "reported_version": configured[provider]["expected_reported_version"],
                "binary_sha256": configured[provider]["expected_binary_sha256"],
                **({"revision": identities[provider]["revision"]}
                   if identities[provider].get("revision") else {}),
                **({"source_revision": identities[provider]["source_revision"]}
                   if identities[provider].get("source_revision") else {}),
            }
            for provider in providers
        },
        "verdict_relations": {key: relation_counts[key] for key in (
            "pass_to_pass", "pass_to_fail", "fail_to_pass", "fail_to_fail"
        )},
        "verdict_relations_by_track": {
            track: {key: counts[key] for key in (
                "pass_to_pass", "pass_to_fail", "fail_to_pass", "fail_to_fail"
            )}
            for track, counts in sorted(by_track.items())
        },
        "changed_output_cases": sum(
            item["differences"]["output_changed"] for item in paired
        ),
        "cases": paired,
        "review_state": (
            "Deterministic evidence and difference audit only; product root causes require "
            "separate review of every changed or failed case."
        ),
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    def identifiers(relation: str) -> str:
        values = [item["case_id"] for item in paired if item["relation"] == relation]
        return ", ".join(f"`{value}`" for value in values) if values else "None"

    same_verdict_changed = [
        item["case_id"] for item in paired
        if item["relation"] in {"pass_to_pass", "fail_to_fail"}
        and item["differences"]["output_changed"]
    ]
    markdown = "\n".join([
        "## Web Fetch stable-versus-candidate audit",
        "",
        f"Verified {expected_count} cases × 2 methods ({len(records)} physical calls), "
        "one run, zero caller retries, "
        + (
            "serial case-alternating order."
            if call_order == "case-alternating"
            else "serial provider-batch order."
        ),
        "",
        "| Stable → candidate | Cases |",
        "| --- | ---: |",
        f"| Pass → pass | {relation_counts['pass_to_pass']} |",
        f"| Pass → fail | {relation_counts['pass_to_fail']} |",
        f"| Fail → pass | {relation_counts['fail_to_pass']} |",
        f"| Fail → fail | {relation_counts['fail_to_fail']} |",
        "",
        f"**Regressions:** {identifiers('pass_to_fail')}",
        "",
        f"**Improvements:** {identifiers('fail_to_pass')}",
        "",
        "**Changed outputs with the same verdict:** "
        + (", ".join(f"`{value}`" for value in same_verdict_changed)
           if same_verdict_changed else "None"),
        "",
        f"The exhaustive {expected_count}-case inventory and its evidence hashes are in "
        f"`{output_json.name}`. This is a deterministic locator, not a product root-cause review.",
        "",
    ])
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(markdown)
    return summary
