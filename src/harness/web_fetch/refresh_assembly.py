"""Assemble a declared provider-cohort refresh without selecting results by score."""

from __future__ import annotations


from harness.distribution import repository_root

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.scoring import load_list_reference, score_attempt
from lexbench_webfetch.scoring_regressions import (
    build_correction_provenance,
    load_scoring_regressions,
    valid_correction_provenance,
)

from harness.record_io import load_jsonl
from harness.web_fetch.artifacts import (
    materialize_bound_artifacts,
    materialize_reference_checks,
    materialize_task_references,
    record_public_call_log_bindings,
    verify_public_call_log,
    verify_record_artifacts,
)
from harness.web_fetch.assembly import (
    _copy_speed_bundle,
    _prune_shadowed_batch_attempts,
)
from harness.web_fetch.native_routes import native_cases_by_id
from harness.web_fetch.reference_inputs import input_tree, read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    reference_revision_cases,
    require_verified_reference_checks,
    valid_scoring_revision,
)
from harness.web_fetch.safety import reject_evidence_symlinks, validate_report_ids
from harness.web_fetch.speed_cohort import (
    build_summary as build_speed_summary,
)

ROOT = repository_root()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(read_dataset_bytes(path))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_records(root: Path) -> list[dict]:
    path = contained_path(root, "results.jsonl")
    rows = load_jsonl(path, skip_blank=True)
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid result rows: {path}")
    return rows


def _reject_account_capacity_results(rows: list[dict], *, label: str) -> None:
    """Keep provider account exhaustion out of publishable benchmark evidence."""
    blocked = [
        row
        for row in rows
        if row.get("error_type") == "account_capacity"
        or (row.get("provider") == "firecrawl" and row.get("status_code") == 402)
    ]
    if not blocked:
        return
    attempt_ids = ", ".join(
        str(row.get("attempt_id") or "unknown-attempt") for row in blocked[:3]
    )
    suffix = "" if len(blocked) <= 3 else f", and {len(blocked) - 3} more"
    raise ValueError(
        f"{label} contains {len(blocked)} non-publishable provider account-capacity "
        f"failure(s); replenish the account and rerun the declared scope: "
        f"{attempt_ids}{suffix}"
    )


def _provider_order(dataset: dict) -> list[str]:
    coverage = dataset.get("coverage_plan")
    if not isinstance(coverage, dict) or not coverage:
        raise ValueError("Canonical dataset has no provider coverage plan")
    orders = [names for names in coverage.values() if isinstance(names, list)]
    if (
        len(orders) != len(coverage)
        or not orders
        or any(names != orders[0] for names in orders[1:])
    ):
        raise ValueError("Every track must use the same ordered provider cohort")
    providers = orders[0]
    if not providers or len(providers) != len(set(providers)):
        raise ValueError("Canonical provider cohort is empty or duplicated")
    return providers


def _require_reason(value: object, *, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"category", "reason"}:
        raise ValueError(f"Refresh plan has invalid {label}")
    if not all(isinstance(value[field], str) and value[field].strip() for field in value):
        raise ValueError(f"Refresh plan has empty {label}")


def _same_window_speed_not_measured(plan: dict) -> bool:
    speed = plan.get("same_window_speed_cohort")
    return (
        plan.get("schema_version") == 3
        and isinstance(speed, dict)
        and speed.get("evidence_state") == "not_measured_same_window"
    )


def _expected_v2_refresh_pairs(plan: dict, canonical: dict) -> set[tuple[str, str]]:
    """Return the exact main refresh scope, rejecting an internally inconsistent plan."""
    matrix = plan.get("main_matrix")
    if plan.get("schema_version") not in {2, 3} or not isinstance(matrix, dict):
        raise ValueError("Unsupported refresh plan")
    expected_plan_fields = {
        "schema_version", "baseline", "main_matrix", "same_window_speed_cohort",
        "total_planned_new_calls",
    }
    if plan.get("schema_version") == 3:
        expected_plan_fields.update({"implementation_upgrades", "correction_layers"})
    if set(plan) != expected_plan_fields or not isinstance(plan.get("baseline"), dict) or set(plan["baseline"]) != {
        "dataset_sha256", "results_sha256", "logical_attempts",
    }:
        raise ValueError("Refresh plan has unknown or missing top-level fields")
    if set(matrix) != {
        "full_rerun_providers", "full_rerun_reason_by_provider", "matched_case_ids",
        "matched_case_reason_groups", "pair_replacements", "offline_rescore_cases",
        "planned_new_calls", "final_logical_attempts",
    }:
        raise ValueError("Refresh plan has unknown or missing main-matrix fields")
    providers = _provider_order(canonical)
    cases = [case.get("id") for case in canonical.get("cases", [])]
    if not cases or len(cases) != len(set(cases)) or any(not case for case in cases):
        raise ValueError("Canonical case inventory is empty or duplicated")
    full_providers = matrix.get("full_rerun_providers")
    matched_cases = matrix.get("matched_case_ids")
    pair_replacements = matrix.get("pair_replacements")
    for label, values in (
        ("full_rerun_providers", full_providers),
        ("matched_case_ids", matched_cases),
    ):
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) and value for value in values)
            or len(values) != len(set(values))
        ):
            raise ValueError(f"Refresh plan has invalid {label}")
    if not isinstance(pair_replacements, list):
        raise ValueError("Refresh plan has invalid pair_replacements")
    if not set(matched_cases) <= set(cases):
        raise ValueError("Refresh plan names a noncanonical case")
    correction_reruns = (
        canonical.get("comparison", {}).get("case_corrections", {}).get(
            "rerun_all_methods", []
        )
    )
    if (
        not isinstance(correction_reruns, list)
        or len(correction_reruns) != len(set(correction_reruns))
        or not set(correction_reruns) <= set(matched_cases)
    ):
        raise ValueError(
            "Every input-changing case correction must rerun all methods"
        )
    if not set(full_providers) <= set(providers):
        raise ValueError("Refresh plan names a noncanonical provider")
    reasons = matrix.get("full_rerun_reason_by_provider")
    if not isinstance(reasons, dict) or set(reasons) != set(full_providers):
        raise ValueError("Refresh plan must explain every full provider rerun exactly once")
    for provider, reason in reasons.items():
        _require_reason(reason, label=f"full provider reason for {provider}")
    matched_reason_groups = matrix.get("matched_case_reason_groups")
    if not isinstance(matched_reason_groups, list):
        raise ValueError("Refresh plan has invalid matched case reason groups")
    if bool(matched_reason_groups) != bool(matched_cases):
        raise ValueError(
            "Refresh plan matched case IDs and reason groups must both be empty or nonempty"
        )
    explained_cases = []
    for index, group in enumerate(matched_reason_groups):
        if not isinstance(group, dict) or set(group) != {"case_ids", "category", "reason"}:
            raise ValueError("Refresh plan has invalid matched case reason group")
        group_cases = group["case_ids"]
        if (
            not isinstance(group_cases, list)
            or not group_cases
            or not all(isinstance(case_id, str) and case_id for case_id in group_cases)
            or len(group_cases) != len(set(group_cases))
        ):
            raise ValueError("Refresh plan has invalid matched reason case inventory")
        _require_reason(
            {field: group[field] for field in ("category", "reason")},
            label=f"matched case reason group {index}",
        )
        explained_cases.extend(group_cases)
    if len(explained_cases) != len(set(explained_cases)):
        raise ValueError("Matched case reason groups overlap")
    if set(explained_cases) != set(matched_cases):
        raise ValueError("Matched case reason groups must explain every matched case exactly once")

    offline_rescore = matrix.get("offline_rescore_cases")
    if not isinstance(offline_rescore, list):
        raise ValueError("Refresh plan has invalid offline rescore cases")
    offline_ids = []
    for entry in offline_rescore:
        if not isinstance(entry, dict) or set(entry) != {
            "case_id", "category", "reason", "attempts", "full_review"
        }:
            raise ValueError("Refresh plan has invalid offline rescore entry")
        case_id = entry["case_id"]
        if not isinstance(case_id, str) or case_id not in cases:
            raise ValueError("Offline rescore names a noncanonical case")
        _require_reason(
            {field: entry[field] for field in ("category", "reason")},
            label=f"offline rescore reason for {case_id}",
        )
        if entry["attempts"] != len(providers) or entry["full_review"] is not True:
            raise ValueError("Offline rescore must cover every method and require full review")
        offline_ids.append(case_id)
    if len(offline_ids) != len(set(offline_ids)):
        raise ValueError("Refresh plan has duplicate offline rescore cases")

    case_pairs = {(provider, case_id) for case_id in matched_cases for provider in providers}
    provider_pairs = {(provider, case_id) for provider in full_providers for case_id in cases}
    explicit_pairs = set()
    for replacement in pair_replacements:
        if not isinstance(replacement, dict) or set(replacement) != {
            "provider", "case_id", "category", "reason"
        }:
            raise ValueError("Refresh plan has invalid pair replacement")
        provider = replacement["provider"]
        case_id = replacement["case_id"]
        if not isinstance(provider, str) or not isinstance(case_id, str):
            raise ValueError("Refresh plan has invalid pair replacement identity")
        _require_reason(
            {field: replacement[field] for field in ("category", "reason")},
            label=f"pair replacement reason for {provider}/{case_id}",
        )
        pair = (provider, case_id)
        if provider not in providers or case_id not in cases:
            raise ValueError("Pair replacement lies outside the canonical matrix")
        if pair in explicit_pairs:
            raise ValueError("Refresh plan has duplicate pair replacements")
        explicit_pairs.add(pair)
    if explicit_pairs & (case_pairs | provider_pairs):
        raise ValueError("Pair replacements must not overlap broader refresh scopes")

    expected = case_pairs | provider_pairs | explicit_pairs
    if plan.get("schema_version") == 2 and matrix.get("planned_new_calls") != len(expected):
        raise ValueError("Refresh plan call count does not match its declared scopes")
    speed = plan.get("same_window_speed_cohort", {})
    if not isinstance(speed, dict) or type(speed.get("required")) is not bool:
        raise ValueError("Refresh plan has invalid speed cohort")
    speed_common = {"required", "reuse_existing", "reason", "planned_new_calls"}
    if _same_window_speed_not_measured(plan):
        expected_speed_fields = speed_common | {"evidence_state"}
        if (
            speed["required"] is not False
            or speed.get("reuse_existing") is not False
            or speed.get("planned_new_calls") != 0
        ):
            raise ValueError(
                "Absent same-window speed evidence cannot be required, reused, or scheduled"
            )
    else:
        expected_speed_fields = (
            speed_common | {"case_ids", "provider_count"}
            if speed["required"]
            else speed_common | {
                "dataset_sha256", "results_sha256", "execution_sha256",
                "speed_comparison_sha256", "auxiliary_sha256",
            }
        )
    if set(speed) != expected_speed_fields:
        raise ValueError("Refresh plan has unknown or missing speed-cohort fields")
    if not isinstance(speed.get("reason"), str) or not speed["reason"].strip():
        raise ValueError("Refresh plan has empty speed-cohort reason")
    speed_calls = speed.get("planned_new_calls")
    if type(speed_calls) is not int or speed_calls < 0:
        raise ValueError("Refresh plan has invalid speed call count")
    if plan.get("schema_version") == 2 and plan.get("total_planned_new_calls") != len(expected) + speed_calls:
        raise ValueError("Refresh plan total call count is inconsistent")
    return expected


def _case_ids_sha256(case_ids: list[str]) -> str:
    payload = json.dumps(sorted(case_ids), ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _implementation_upgrades(plan: dict, canonical: dict) -> list[dict]:
    if plan.get("schema_version") != 3:
        return []
    upgrades = plan.get("implementation_upgrades")
    if not isinstance(upgrades, list) or not upgrades:
        raise ValueError("Schema v3 refresh plan requires implementation upgrades")
    canonical_providers = set(_provider_order(canonical))
    canonical_cases = {case.get("id") for case in canonical.get("cases", [])}
    seen_logical, seen_baseline, seen_rerun = set(), set(), set()
    expected_fields = {
        "logical_provider", "baseline_provider", "rerun_provider", "affected_case_ids",
        "reason", "impact_receipt", "baseline_implementation_identity",
        "rerun_implementation_identity",
    }
    for upgrade in upgrades:
        if not isinstance(upgrade, dict) or set(upgrade) != expected_fields:
            raise ValueError("Refresh plan has invalid implementation upgrade")
        logical = upgrade.get("logical_provider")
        baseline = upgrade.get("baseline_provider")
        rerun = upgrade.get("rerun_provider")
        if not all(isinstance(value, str) and value for value in (logical, baseline, rerun)):
            raise ValueError("Implementation upgrade has an invalid provider")
        if logical not in canonical_providers or baseline == logical or rerun != logical:
            raise ValueError("Implementation upgrade provider mapping is invalid")
        if logical in seen_logical or baseline in seen_baseline or rerun in seen_rerun:
            raise ValueError("Implementation upgrades have overlapping providers")
        seen_logical.add(logical)
        seen_baseline.add(baseline)
        seen_rerun.add(rerun)
        case_ids = upgrade.get("affected_case_ids")
        if (
            not isinstance(case_ids, list) or not case_ids
            or not all(isinstance(case_id, str) and case_id for case_id in case_ids)
            or len(case_ids) != len(set(case_ids))
            or not set(case_ids) <= canonical_cases
        ):
            raise ValueError("Implementation upgrade has invalid affected case IDs")
        if set(case_ids) != canonical_cases:
            raise ValueError(
                "Implementation upgrades require a complete canonical rerun; "
                "partial version upgrades cannot produce a fair aggregate"
            )
        _require_reason(upgrade.get("reason"), label=f"upgrade reason for {logical}")
        receipt = upgrade.get("impact_receipt")
        if not isinstance(receipt, dict) or set(receipt) != {
            "path", "sha256", "case_ids_sha256"
        }:
            raise ValueError("Implementation upgrade has invalid impact receipt binding")
        if (
            not isinstance(receipt["path"], str) or not receipt["path"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(receipt["sha256"]))
            or receipt["case_ids_sha256"] != _case_ids_sha256(case_ids)
        ):
            raise ValueError("Implementation upgrade impact receipt binding is inconsistent")
        for label in ("baseline_implementation_identity", "rerun_implementation_identity"):
            identity = upgrade.get(label)
            if not isinstance(identity, dict) or not identity:
                raise ValueError(f"Implementation upgrade has invalid {label}")
    return upgrades


def _parameters_sha256(parameters: dict) -> str:
    return hashlib.sha256(json.dumps(
        parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _correction_layers(
    plan: dict, canonical: dict, expected_pairs: set[tuple[str, str]]
) -> list[dict]:
    if plan.get("schema_version") != 3:
        return []
    layers = plan.get("correction_layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("Schema v3 refresh plan requires correction layers")
    providers = set(_provider_order(canonical)) | {
        upgrade["baseline_provider"] for upgrade in _implementation_upgrades(plan, canonical)
    }
    cases = {case.get("id") for case in canonical.get("cases", [])}
    layer_ids, declared_pairs = set(), set()
    for layer in layers:
        if not isinstance(layer, dict) or set(layer) != {
            "id", "provider", "case_ids", "source_bundle", "execution_contract",
            "scope_receipt",
        }:
            raise ValueError("Refresh plan has an invalid correction layer")
        layer_id, provider, case_ids = layer["id"], layer["provider"], layer["case_ids"]
        if (
            not isinstance(layer_id, str) or not layer_id or layer_id in layer_ids
            or not isinstance(provider, str) or provider not in providers
            or not isinstance(case_ids, list) or not case_ids
            or len(case_ids) != len(set(case_ids))
            or not all(isinstance(case_id, str) and case_id in cases for case_id in case_ids)
        ):
            raise ValueError("Correction layer has an invalid identity or case scope")
        layer_ids.add(layer_id)
        pairs = {(provider, case_id) for case_id in case_ids}
        if declared_pairs & pairs:
            raise ValueError("Correction layers assign a provider/case pair more than once")
        declared_pairs |= pairs
        bundle = layer["source_bundle"]
        if not isinstance(bundle, dict) or set(bundle) != {
            "dataset_sha256", "results_sha256", "execution_file", "execution_sha256"
        } or not all(
            isinstance(bundle.get(field), str) and (
                field == "execution_file" or re.fullmatch(r"[0-9a-f]{64}", bundle[field])
            )
            for field in bundle
        ):
            raise ValueError("Correction layer has an invalid source bundle binding")
        contract = layer["execution_contract"]
        if not isinstance(contract, dict) or set(contract) != {
            "runtime_source_sha256", "provider_config_sha256", "parameters_sha256",
            "implementation_identity",
        } or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(contract.get(field, "")))
            for field in ("runtime_source_sha256", "provider_config_sha256", "parameters_sha256")
        ) or not isinstance(contract["implementation_identity"], dict) or not contract[
            "implementation_identity"
        ]:
            raise ValueError("Correction layer has an invalid execution contract")
        receipt = layer["scope_receipt"]
        if not isinstance(receipt, dict) or set(receipt) != {
            "path", "sha256", "case_ids_sha256"
        } or not isinstance(receipt["path"], str) or not receipt["path"] or not re.fullmatch(
            r"[0-9a-f]{64}", str(receipt["sha256"])
        ) or receipt["case_ids_sha256"] != _case_ids_sha256(case_ids):
            raise ValueError("Correction layer has an invalid scope receipt binding")
    if declared_pairs != expected_pairs:
        missing, extra = expected_pairs - declared_pairs, declared_pairs - expected_pairs
        raise ValueError(
            "Correction layers differ from the exact refresh scope: "
            f"missing={len(missing)}, extra={len(extra)}"
        )
    return layers


def expected_refresh_pairs(plan: dict, canonical: dict) -> set[tuple[str, str]]:
    """Return exact physical rerun pairs for either refresh-plan schema."""
    regular = _expected_v2_refresh_pairs(plan, canonical)
    upgrades = _implementation_upgrades(plan, canonical)
    if not upgrades:
        return regular
    upgrade_pairs = {
        (upgrade["rerun_provider"], case_id)
        for upgrade in upgrades for case_id in upgrade["affected_case_ids"]
    }
    if regular & upgrade_pairs:
        raise ValueError("Implementation upgrade overlaps another refresh scope")
    expected = regular | upgrade_pairs
    matrix = plan["main_matrix"]
    if matrix.get("planned_new_calls") != len(expected):
        raise ValueError("Refresh plan call count does not include implementation upgrades")
    speed_calls = plan["same_window_speed_cohort"]["planned_new_calls"]
    if plan.get("total_planned_new_calls") != len(expected) + speed_calls:
        raise ValueError("Refresh plan total call count is inconsistent")
    _correction_layers(plan, canonical, expected)
    return expected


def _pair_scope_reasons(plan: dict, provider: str, case_id: str) -> list[dict]:
    matrix = plan["main_matrix"]
    scopes = []
    if provider in matrix["full_rerun_providers"]:
        scopes.append({
            "scope": "full_rerun_provider",
            **matrix["full_rerun_reason_by_provider"][provider],
        })
    if case_id in matrix["matched_case_ids"]:
        reason = next(
            group for group in matrix["matched_case_reason_groups"]
            if case_id in group["case_ids"]
        )
        scopes.append({
            "scope": "matched_case", "category": reason["category"],
            "reason": reason["reason"],
        })
    for replacement in matrix["pair_replacements"]:
        if (replacement["provider"], replacement["case_id"]) == (provider, case_id):
            scopes.append({
                "scope": "pair_replacement",
                "category": replacement["category"],
                "reason": replacement["reason"],
            })
    for upgrade in plan.get("implementation_upgrades", []):
        if provider == upgrade["rerun_provider"] and case_id in upgrade["affected_case_ids"]:
            scopes.append({
                "scope": "implementation_upgrade",
                **upgrade["reason"],
                "logical_provider": upgrade["logical_provider"],
                "baseline_provider": upgrade["baseline_provider"],
            })
    if not scopes:
        raise ValueError(f"Rerun pair has no declared scope reason: {provider}/{case_id}")
    return scopes


def _validate_record_identity(
    rows: list[dict], *, allowed_pairs: set[tuple[str, str]], label: str,
    allow_physical_projection: bool = False,
) -> None:
    validate_report_ids({"cases": [{"id": case_id} for _, case_id in allowed_pairs]}, rows)
    pairs = [(row.get("provider"), row.get("case_id")) for row in rows]
    if len(pairs) != len(set(pairs)):
        raise ValueError(f"{label} contains duplicate provider/case results")
    if set(pairs) != allowed_pairs:
        missing = len(allowed_pairs - set(pairs))
        extra = len(set(pairs) - allowed_pairs)
        raise ValueError(f"{label} differs from its exact matrix: missing={missing}, extra={extra}")
    attempts = [row.get("attempt_id") for row in rows]
    if len(attempts) != len(set(attempts)):
        raise ValueError(f"{label} contains duplicate attempt IDs")
    for row in rows:
        physical = row.get("physical_execution")
        if physical is not None and not allow_physical_projection:
            raise ValueError(f"{label} contains an undeclared physical projection")
        physical_provider = row["provider"]
        physical_attempt = row.get("attempt_id")
        if physical is not None:
            if not isinstance(physical, dict) or set(physical) != {
                "provider", "attempt_id", "implementation_identity", "source_bundle"
            }:
                raise ValueError(f"{label} has an invalid physical execution identity")
            physical_provider = physical.get("provider")
            physical_attempt = physical.get("attempt_id")
            if row.get("attempt_id") != physical_attempt:
                raise ValueError(f"{label} rewrote a physical attempt identity")
        expected = f"{physical_provider}__{row['case_id']}__r001"
        if row.get("run") != 1 or physical_attempt != expected:
            raise ValueError(
                f"{label} has a noncanonical attempt identity: {row.get('attempt_id')}"
            )


def _batch_identity_by_attempt(
    batches: list[dict], rows: list[dict], root: Path, *, required_providers: set[str]
) -> dict[str, dict]:
    rows_by_attempt = {row["attempt_id"]: row for row in rows}
    required_attempts = {
        row["attempt_id"] for row in rows if row["provider"] in required_providers
    }
    identities = {}
    for batch in batches:
        providers = {
            provider.get("name"): provider.get("implementation_identity")
            for provider in batch.get("providers", []) if isinstance(provider, dict)
        }
        for attempt_id in batch.get("attempt_ids", []):
            row = rows_by_attempt.get(attempt_id)
            if row is None:
                continue
            if row["provider"] not in required_providers:
                continue
            identity = providers.get(row["provider"])
            metadata = row.get("provider_metadata", {})
            if not isinstance(identity, dict) or not identity:
                identity = _recorded_implementation_identity(row)
            if not isinstance(identity, dict) or not identity:
                raise ValueError(f"Execution batch lacks implementation identity: {attempt_id}")
            for field in ("binary_sha256", "reported_version"):
                if metadata.get(field) is not None and identity.get(field) != metadata[field]:
                    raise ValueError(
                        f"Result and execution implementation identity differ: {attempt_id}/{field}"
                    )
            for binding in record_public_call_log_bindings(row):
                payload = json.loads(verify_public_call_log(root, binding).read_bytes())
                call_identity = payload["identity"]
                if (
                    call_identity.get("attempt_id") != attempt_id
                    or call_identity.get("provider") != row["provider"]
                    or call_identity.get("case_id") != row["case_id"]
                    or call_identity.get("run") != row["run"]
                ):
                    raise ValueError(f"Public call log physical identity differs: {attempt_id}")
            identities[attempt_id] = deepcopy(identity)
    if set(identities) != required_attempts:
        raise ValueError(
            f"Execution identities do not cover required implementation results: {root}"
        )
    return identities


def _recorded_implementation_identity(row: dict) -> dict | None:
    metadata = row.get("provider_metadata", {})
    if metadata.get("binary_sha256"):
        return {
            "binary_sha256": metadata["binary_sha256"],
            "reported_version": metadata.get("reported_version")
            or metadata.get("browser_version"),
        }
    if metadata.get("adapter_sha256"):
        return {
            "adapter_sha256": metadata["adapter_sha256"],
            "deployment": metadata.get("deployment"),
            "service": metadata.get("browser") or metadata.get("engine"),
        }
    return None


def _validate_impact_receipts(plan: dict, plan_path: Path, canonical: dict) -> list[dict]:
    validated = []
    for upgrade in _implementation_upgrades(plan, canonical):
        binding = upgrade["impact_receipt"]
        path = contained_path(plan_path.parent, binding["path"], reject_symlinks=True)
        if not path.is_file() or _sha256(path) != binding["sha256"]:
            raise ValueError("Implementation impact receipt bytes differ from refresh plan")
        receipt = _read_json(path)
        required = {
            "schema_version", "kind", "provider_calls", "baseline_provider",
            "logical_provider", "rerun_provider", "affected_case_ids",
            "baseline_implementation_identity", "rerun_implementation_identity",
        }
        if (
            not required <= set(receipt)
            or receipt.get("schema_version") != 1
            or receipt.get("kind") != "implementation_upgrade_impact"
            or receipt.get("provider_calls") != 0
            or receipt.get("baseline_provider") != upgrade["baseline_provider"]
            or receipt.get("logical_provider") != upgrade["logical_provider"]
            or receipt.get("rerun_provider") != upgrade["rerun_provider"]
            or receipt.get("affected_case_ids") != upgrade["affected_case_ids"]
            or receipt.get("baseline_implementation_identity")
            != upgrade["baseline_implementation_identity"]
            or receipt.get("rerun_implementation_identity")
            != upgrade["rerun_implementation_identity"]
        ):
            raise ValueError("Implementation impact receipt differs from refresh plan")
        validated.append({
            "path": binding["path"], "sha256": binding["sha256"],
            "case_ids_sha256": binding["case_ids_sha256"],
        })
    return validated


def _validate_correction_scope_receipts(
    layers: list[dict], plan_path: Path
) -> list[dict]:
    validated = []
    for layer in layers:
        binding = layer["scope_receipt"]
        path = contained_path(plan_path.parent, binding["path"], reject_symlinks=True)
        if not path.is_file() or _sha256(path) != binding["sha256"]:
            raise ValueError("Correction-layer scope receipt bytes differ from refresh plan")
        receipt = _read_json(path)
        entries = receipt.get("layers") if isinstance(receipt.get("layers"), list) else []
        entry = next(
            (item for item in entries if isinstance(item, dict)
             and item.get("layer_id") == layer["id"]),
            None,
        )
        if (
            receipt.get("schema_version") != 1
            or receipt.get("kind") != "correction_layer_scope_bindings"
            or receipt.get("provider_calls") != 0
            or entry is None
            or set(entry) != {"layer_id", "provider", "case_ids"}
            or entry.get("provider") != layer["provider"]
            or entry.get("case_ids") != layer["case_ids"]
        ):
            raise ValueError("Correction-layer scope receipt differs from refresh plan")
        validated.append({
            "layer_id": layer["id"], "path": binding["path"],
            "sha256": binding["sha256"],
            "case_ids_sha256": binding["case_ids_sha256"],
        })
    return validated


def _bundle_matches_layer(root: Path, execution_path: Path, layer: dict) -> bool:
    binding = layer["source_bundle"]
    return binding == {
        "dataset_sha256": _sha256(root / "dataset.json"),
        "results_sha256": _sha256(root / "results.jsonl"),
        "execution_file": execution_path.name,
        "execution_sha256": _sha256(execution_path),
    }


def _validate_layer_execution(
    layer: dict, rows_by_pair: dict[tuple[str, str], dict], batches: list[dict], root: Path
) -> None:
    provider = layer["provider"]
    contract = layer["execution_contract"]
    batches_by_attempt = {}
    for batch in batches:
        for attempt_id in batch.get("attempt_ids", []):
            batches_by_attempt[attempt_id] = batch
    for case_id in layer["case_ids"]:
        row = rows_by_pair.get((provider, case_id))
        if row is None:
            raise ValueError(f"Correction layer is missing its selected pair: {layer['id']}/{case_id}")
        batch = batches_by_attempt.get(row["attempt_id"])
        if batch is None:
            raise ValueError(f"Correction layer selected pair has no execution batch: {row['attempt_id']}")
        provider_identity = next(
            (item.get("implementation_identity") for item in batch.get("providers", [])
             if isinstance(item, dict) and item.get("name") == provider),
            None,
        )
        if provider_identity is None:
            provider_identity = _recorded_implementation_identity(row)
        actual = {
            "runtime_source_sha256": batch.get("runtime_source_sha256"),
            "provider_config_sha256": batch.get("provider_config_sha256"),
            "parameters_sha256": _parameters_sha256(batch.get("parameters", {})),
            "implementation_identity": provider_identity,
        }
        if actual != contract:
            changed = ", ".join(
                field for field in contract if contract[field] != actual[field]
            )
            raise ValueError(
                f"Correction layer execution differs from its contract: {layer['id']}: {changed}"
            )
        selected_providers = [
            ({**item, "implementation_identity": provider_identity}
             if isinstance(item, dict) and item.get("name") == provider else item)
            for item in batch.get("providers", [])
        ]
        selected_batch = {
            **batch,
            "providers": selected_providers,
            "attempt_ids": [row["attempt_id"]],
            "attempt_count": 1,
        }
        _batch_identity_by_attempt(
            [selected_batch], [row], root, required_providers={provider}
        )


def _verify_artifacts(root: Path, rows: list[dict]) -> None:
    native_cases = native_cases_by_id(root)
    for row in rows:
        verify_record_artifacts(root, row, native_cases=native_cases)


def _source_batches(root: Path) -> tuple[list[dict], Path]:
    execution = root / "execution.json"
    manifest = root / "manifest.json"
    if execution.is_file():
        value = _read_json(execution).get("batches")
        if not isinstance(value, list) or not value:
            raise ValueError(f"Invalid execution batches: {root}")
        return value, execution
    if manifest.is_file():
        return [_read_json(manifest)], manifest
    raise ValueError(f"Evidence has no execution or manifest: {root}")


def _validate_source_batches(
    root: Path, dataset: dict, rows: list[dict], *, references: bool
) -> tuple[list[dict], Path]:
    batches, source_path = _source_batches(root)
    ids = Counter(attempt for batch in batches for attempt in batch.get("attempt_ids", []))
    if ids != Counter(row["attempt_id"] for row in rows):
        raise ValueError(f"Execution batches do not exactly cover results: {root}")
    if references:
        require_verified_reference_checks(
            batches,
            cases=dataset,
            records=rows,
            source=root,
            equivalent_capture_bindings=dataset.get("comparison", {}).get("reference_binding_mode")
            == "equivalent_reference_bytes_with_independent_batch_captures",
        )
    return batches, source_path


def _validate_consistent_provider_run_identities(
    rerun_batches: list[tuple[list[dict], list[dict], Path, Path]],
) -> None:
    """Reject provider shards produced with different code, config, or parameters."""
    identities: dict[str, list[tuple[dict, Path]]] = defaultdict(list)
    config_identities: dict[tuple[str, str | None], list[tuple[str | None, Path]]] = defaultdict(list)
    for batches, rows, root, _source_path in rerun_batches:
        rows_by_attempt = {row["attempt_id"]: row for row in rows}
        for batch in batches:
            providers = batch.get("providers")
            if not isinstance(providers, list):
                raise ValueError(f"Main rerun batch has invalid providers: {root}")
            parameters = batch.get("parameters")
            if not isinstance(parameters, dict):
                raise ValueError(f"Main rerun batch has invalid parameters: {root}")
            if not all(
                isinstance(provider, dict) and isinstance(provider.get("name"), str)
                for provider in providers
            ):
                raise ValueError(f"Main rerun batch has invalid provider identity: {root}")
            provider_names = [provider["name"] for provider in providers]
            if len(provider_names) != len(set(provider_names)):
                raise ValueError(f"Main rerun batch has duplicate provider identities: {root}")
            attempt_providers = {
                rows_by_attempt[attempt_id]["provider"]
                for attempt_id in batch.get("attempt_ids", [])
            }
            if set(provider_names) != attempt_providers:
                raise ValueError(
                    f"Main rerun batch provider identities do not match its attempts: {root}"
                )
            for provider in providers:
                identities[provider["name"]].append(({
                    "runtime_source_sha256": batch.get("runtime_source_sha256"),
                    "implementation_identity": provider.get("implementation_identity"),
                    "parameters": parameters,
                }, root))
                source = batch.get("provider_config_source")
                route = Path(source).name if isinstance(source, str) else None
                config_identities[(provider["name"], route)].append((
                    batch.get("provider_config_sha256"), root
                ))
    for provider, entries in identities.items():
        if len(entries) < 2:
            continue
        expected, expected_root = entries[0]
        for actual, actual_root in entries[1:]:
            if actual != expected:
                changed = ", ".join(
                    field for field in expected if expected[field] != actual[field]
                )
                raise ValueError(
                    f"Main rerun provider {provider} has inconsistent run identity across "
                    f"{expected_root} and {actual_root}: {changed}"
                )
    for (provider, route), entries in config_identities.items():
        expected, expected_root = entries[0]
        for actual, actual_root in entries[1:]:
            if actual != expected:
                raise ValueError(
                    f"Main rerun provider {provider} has inconsistent run identity across "
                    f"{expected_root} and {actual_root}: provider_config_sha256 "
                    f"for {route or 'unspecified config route'}"
                )


def _require_canonical_cases(
    dataset: dict,
    canonical_cases: dict[str, dict],
    rows: list[dict],
    label: str,
    *,
    equivalent_capture_bindings: bool = False,
) -> None:
    supplied = {case.get("id"): case for case in dataset.get("cases", [])}
    if len(supplied) != len(dataset.get("cases", [])):
        raise ValueError(f"{label} has duplicate case definitions")
    for case_id in {row["case_id"] for row in rows}:
        incoming = deepcopy(supplied.get(case_id))
        canonical = deepcopy(canonical_cases.get(case_id))
        if equivalent_capture_bindings and incoming is not None and canonical is not None:
            # Independently refreshed short batches may have different capture
            # timestamps while binding the exact same reference bytes and task
            # contract.  Every other source-verification field remains strict.
            # These fields describe the independent capture instance rather
            # than the frozen evaluation contract. The reference lifecycle
            # separately binds and verifies their exact bytes and timing.
            for verification in (
                incoming.get("source_verification", {}),
                canonical.get("source_verification", {}),
            ):
                status = verification.get("http_status")
                if status is not None and (type(status) is not int or not 100 <= status <= 599):
                    raise ValueError(f"{label} has malformed capture HTTP status: {case_id}")
                for field in (
                    "reference_sha256",
                    "source_snapshot_sha256",
                    "extracted_reference_sha256",
                    "browser_observation_sha256",
                ):
                    digest = verification.get(field)
                    if digest is not None and (
                        not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    ):
                        raise ValueError(f"{label} has invalid capture hash: {case_id}/{field}")
            for field in (
                "checked_at",
                "http_status",
                "source_snapshot_sha256",
                "extracted_reference_sha256",
                "browser_observation_sha256",
            ):
                incoming.get("source_verification", {}).pop(field, None)
                canonical.get("source_verification", {}).pop(field, None)
        if incoming != canonical:
            raise ValueError(f"{label} case/reference binding differs from canonical: {case_id}")


def _require_reproducible_verdicts(root: Path) -> None:
    """Reject retained verdicts that the public scorer cannot reproduce."""
    dataset = _read_json(root / "dataset.json")
    cases = {case["id"]: case for case in dataset.get("cases", [])}
    list_references = {
        case_id: load_list_reference(case, root) for case_id, case in cases.items()
    }
    flips = []
    for row in _read_records(root):
        case = cases[row["case_id"]]
        raw = contained_path(root, row["artifact_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["metrics"].get("content_sha256"):
            raise ValueError(f"Assembled artifact hash differs from result: {row['attempt_id']}")
        before = task_passed(
            bool(row["ok"]), row["metrics"], case.get("task_kind", "body")
        )
        rescored = score_attempt(
            case,
            raw,
            ok=bool(row["ok"]),
            status_code=row.get("status_code"),
            title=row.get("title"),
            final_url=row.get("final_url"),
            metadata=row.get("provider_metadata", {}),
            previous_metrics=row["metrics"],
            list_reference=list_references[row["case_id"]],
        )
        after = task_passed(bool(row["ok"]), rescored, case.get("task_kind", "body"))
        if before != after:
            flips.append(f"{row['provider']}/{row['case_id']} ({before}->{after})")
    if flips:
        preview = ", ".join(flips[:5])
        suffix = "" if len(flips) <= 5 else f", and {len(flips) - 5} more"
        raise ValueError(
            "Assembled persisted verdict differs from public scorer: "
            f"{preview}{suffix}"
        )


def _copy_artifacts(output: Path, selections: list[tuple[dict, Path]]) -> None:
    by_root = defaultdict(list)
    for row, root in selections:
        by_root[root].append(row)
    for root, rows in by_root.items():
        materialize_bound_artifacts(output, root, rows)


def _physical_record(record: dict) -> dict:
    physical = record.get("physical_execution")
    if not isinstance(physical, dict):
        return record
    value = deepcopy(record)
    value["provider"] = physical["provider"]
    value.pop("physical_execution", None)
    return value


def _bundle_identity(root: Path, execution_path: Path) -> dict:
    return {
        "label": root.name,
        "dataset_sha256": _sha256(root / "dataset.json"),
        "results_sha256": _sha256(root / "results.jsonl"),
        "execution_file": execution_path.name,
        "execution_sha256": _sha256(execution_path),
    }


def _composite_correction_provenance(receipts: list[dict], decisions: list[dict]) -> dict:
    provenances = [receipt.get("correction_provenance") for receipt in receipts
                   if isinstance(receipt.get("correction_provenance"), dict)]
    if not provenances:
        raise ValueError("Selected source revisions lack correction provenance")
    regression_ids = list(dict.fromkeys(
        identifier
        for provenance in provenances
        for identifier in provenance.get("regression_ids", [])
    ))
    registry = load_scoring_regressions()
    categories = {registry[identifier]["category"] for identifier in regression_ids}
    category = next(iter(categories)) if len(categories) == 1 else "multi-root"
    causes = list(dict.fromkeys(provenance["root_cause"] for provenance in provenances))
    return build_correction_provenance(
        category,
        "Composite assembly preserves audited source causes: " + " | ".join(causes),
        regression_ids,
        decisions,
    )


def _write_composite_revisions(output: Path, selections: list[tuple[dict, Path]]) -> None:
    """Carry only selected, validated source corrections into the final bundle."""
    by_root: dict[Path, list[dict]] = {}
    for row, root in selections:
        by_root.setdefault(root, []).append(row)
    dataset_hash = _sha256(output / "dataset.json")
    results_hash = _sha256(output / "results.jsonl")
    scoring_changes, scoring_decisions, scoring_sources, scoring_receipts = {}, {}, [], []
    reference_changes, reference_decisions, reference_sources, reference_receipts = {}, {}, [], []
    history = output / "history" / "source-revisions"
    for index, (root, selected) in enumerate(by_root.items()):
        attempts = {row["attempt_id"] for row in selected}
        cases = {row["case_id"] for row in selected}
        for kind, filename in (("scoring", "scoring-revisions.json"),
                               ("reference", "reference-revisions.json")):
            path = root / filename
            if not path.is_file():
                continue
            receipts = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(receipts, list) or not receipts:
                raise ValueError(f"Invalid selected source revision receipt: {path}")
            if kind == "scoring":
                current = (_sha256(root / "dataset.json"), _sha256(root / "results.jsonl"))
                batches, _ = _source_batches(root)
                projected = any(
                    isinstance(batch.get("projection"), dict)
                    and isinstance(batch["projection"].get("source_execution_sha256"), str)
                    and re.fullmatch(
                        r"[0-9a-f]{64}", batch["projection"]["source_execution_sha256"]
                    )
                    for batch in batches
                )
                derived_bundle = projected or (root / "selection-audit.json").is_file()
                if (derived_bundle and not valid_scoring_revision(root)):
                    raise ValueError(f"Selected source has invalid scoring revision binding: {root}")
                allowed_kinds = {
                    "audited_body_scoring_revision", "audited_scoring_rule_revision",
                    *( ["audited_assembled_scoring_revision"] if derived_bundle else [] ),
                }
                if any(not isinstance(receipt, dict)
                       or receipt.get("kind") not in allowed_kinds
                       or receipt.get("provider_calls") != 0
                       or ((receipt.get("output_dataset_sha256"),
                           receipt.get("output_results_sha256")) != current and not derived_bundle)
                       or not valid_correction_provenance(receipt) for receipt in receipts):
                    raise ValueError(f"Selected source has invalid scoring revision binding: {root}")
            if kind == "reference":
                current = (_sha256(root / "dataset.json"), _sha256(root / "results.jsonl"))
                batches, _ = _source_batches(root)
                projected = any(
                    isinstance(batch.get("projection"), dict)
                    and isinstance(batch["projection"].get("source_execution_sha256"), str)
                    and re.fullmatch(
                        r"[0-9a-f]{64}", batch["projection"]["source_execution_sha256"]
                    )
                    for batch in batches
                )
                derived_bundle = projected or (root / "selection-audit.json").is_file()
                if any(not isinstance(receipt, dict)
                       or receipt.get("kind") != "audited_reference_correction"
                       or receipt.get("provider_calls") != 0
                       or (receipt.get("output_dataset_sha256"),
                           receipt.get("output_results_sha256")) != current and not derived_bundle
                       or not valid_correction_provenance(receipt) for receipt in receipts):
                    raise ValueError(f"Selected source has invalid reference revision binding: {root}")
                if (not derived_bundle and any(receipt.get("changed_cases") for receipt in receipts)
                        and not reference_revision_cases(root)):
                    raise ValueError(f"Selected source has invalid reference revision changes: {root}")
            history.mkdir(parents=True, exist_ok=True)
            relative = f"history/source-revisions/{index:03d}-{root.name}-{filename}"
            shutil.copy2(path, output / relative)
            source = {
                "source_dataset_sha256": _sha256(root / "dataset.json"),
                "source_results_sha256": _sha256(root / "results.jsonl"),
                "receipt_path": relative,
                "receipt_sha256": _sha256(path),
            }
            changes = scoring_changes if kind == "scoring" else reference_changes
            decisions = scoring_decisions if kind == "scoring" else reference_decisions
            sources = scoring_sources if kind == "scoring" else reference_sources
            all_receipts = scoring_receipts if kind == "scoring" else reference_receipts
            sources.append(source)
            for receipt in receipts:
                all_receipts.append(receipt)
                for change in receipt.get("changed_cases", []):
                    if change.get("case_id") in cases:
                        changes[json.dumps(change, sort_keys=True)] = deepcopy(change)
                for decision in receipt.get("task_decision_changes", []):
                    if decision.get("attempt_id") in attempts:
                        decisions[decision["attempt_id"]] = deepcopy(decision)
    if scoring_sources:
        # This is a provenance index over independently revised source cohorts,
        # not a no-op scoring revision of the assembled matrix.  Give it a
        # deterministic synthetic input identity so later review-merging starts
        # at the first real matrix revision whose input is the reviewed output
        # below, rather than re-reviewing every historical source correction.
        source_index = json.dumps(
            scoring_sources, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()
        receipt = {
            "schema_version": 2, "kind": "audited_scoring_rule_revision",
            "input_dataset_sha256": hashlib.sha256(b"dataset\0" + source_index).hexdigest(),
            "input_results_sha256": hashlib.sha256(b"results\0" + source_index).hexdigest(),
            "output_dataset_sha256": dataset_hash, "output_results_sha256": results_hash,
            "changed_cases": list(scoring_changes.values()),
            "task_decision_changes": list(scoring_decisions.values()), "provider_calls": 0,
            "composite_semantics": "index-only; no reconstructed historical input state",
            "source_receipts": scoring_sources,
            "correction_provenance": _composite_correction_provenance(
                scoring_receipts, list(scoring_decisions.values())
            ),
        }
        (output / "scoring-revisions.json").write_text(json.dumps([receipt], indent=2) + "\n")
        if not valid_scoring_revision(output):
            raise ValueError("Assembled scoring revision provenance is invalid")
    if reference_changes:
        receipt = {
            "schema_version": 2, "kind": "audited_reference_correction",
            "input_dataset_sha256": dataset_hash, "input_results_sha256": results_hash,
            "output_dataset_sha256": dataset_hash, "output_results_sha256": results_hash,
            "changed_cases": list(reference_changes.values()),
            "task_decision_changes": list(reference_decisions.values()), "provider_calls": 0,
            "composite_semantics": "index-only; no reconstructed historical input state",
            "source_receipts": reference_sources,
            "correction_provenance": _composite_correction_provenance(
                reference_receipts, list(reference_decisions.values())
            ),
        }
        (output / "reference-revisions.json").write_text(json.dumps([receipt], indent=2) + "\n")
        if not reference_revision_cases(output):
            raise ValueError("Assembled reference revision provenance is invalid")


def _assemble(
    baseline: Path,
    main_reruns: list[Path],
    speed_cohort: Path | None,
    canonical_path: Path,
    plan_path: Path,
    output: Path,
) -> None:
    plan = _read_json(plan_path)
    canonical = _read_json(canonical_path)
    speed_not_measured = _same_window_speed_not_measured(plan)
    if speed_not_measured and speed_cohort is not None:
        raise ValueError(
            "Refresh plan declares no same-window speed evidence but a speed cohort was supplied"
        )
    if not speed_not_measured and speed_cohort is None:
        raise ValueError("Refresh plan requires a speed cohort input")
    canonical_cases = {case["id"]: case for case in canonical.get("cases", [])}
    providers = _provider_order(canonical)
    upgrades = _implementation_upgrades(plan, canonical)
    impact_receipts = _validate_impact_receipts(plan, plan_path, canonical)
    upgrade_by_logical = {upgrade["logical_provider"]: upgrade for upgrade in upgrades}
    logical_by_baseline = {
        upgrade["baseline_provider"]: upgrade["logical_provider"] for upgrade in upgrades
    }
    expected_main = expected_refresh_pairs(plan, canonical)
    correction_layers = _correction_layers(plan, canonical, expected_main)
    correction_scope_receipts = _validate_correction_scope_receipts(
        correction_layers, plan_path
    )
    expected_final = {(provider, case_id) for provider in providers for case_id in canonical_cases}
    if plan["main_matrix"].get("final_logical_attempts") != len(expected_final):
        raise ValueError("Final logical attempt count differs from the canonical matrix")

    baseline_binding = plan.get("baseline", {})
    if _sha256(baseline / "dataset.json") != baseline_binding.get("dataset_sha256") or _sha256(
        baseline / "results.jsonl"
    ) != baseline_binding.get("results_sha256"):
        raise ValueError("Baseline bytes differ from evaluation/current-rerun.json")
    baseline_dataset = _read_json(baseline / "dataset.json")
    baseline_rows = _read_records(baseline)
    baseline_providers = _provider_order(baseline_dataset)
    baseline_cases = {case["id"]: case for case in baseline_dataset.get("cases", [])}
    baseline_pairs = {
        (provider, case_id) for provider in baseline_providers for case_id in baseline_cases
    }
    mapped_baseline_providers = {
        logical_by_baseline.get(provider, provider) for provider in baseline_providers
    }
    if baseline_binding.get("logical_attempts") != len(baseline_rows) or (
        mapped_baseline_providers != set(providers)
        or len(mapped_baseline_providers) != len(baseline_providers)
    ):
        raise ValueError("Baseline cohort differs from the refresh plan")
    _validate_record_identity(baseline_rows, allowed_pairs=baseline_pairs, label="baseline")
    _verify_artifacts(baseline, baseline_rows)
    baseline_batches, baseline_execution = _validate_source_batches(
        baseline, baseline_dataset, baseline_rows, references=False
    )
    baseline_attempt_identities = _batch_identity_by_attempt(
        baseline_batches,
        baseline_rows,
        baseline,
        required_providers={upgrade["baseline_provider"] for upgrade in upgrades},
    ) if upgrades else {}
    for upgrade in upgrades:
        identities = {
            json.dumps(baseline_attempt_identities[row["attempt_id"]], sort_keys=True)
            for row in baseline_rows if row["provider"] == upgrade["baseline_provider"]
        }
        expected_identity = json.dumps(
            upgrade["baseline_implementation_identity"], sort_keys=True
        )
        if identities != {expected_identity}:
            raise ValueError("Baseline implementation identity differs from upgrade plan")

    replacements = canonical.get("comparison", {}).get("case_replacements")
    if replacements is None:
        replacements = {}
    if not isinstance(replacements, dict) or len(set(replacements.values())) != len(replacements):
        raise ValueError("Canonical case replacement map is missing or invalid")
    baseline_only = set(baseline_cases) - set(canonical_cases)
    canonical_only = set(canonical_cases) - set(baseline_cases)
    active_replacements = {
        old: new for old, new in replacements.items()
        if old in baseline_only or new in canonical_only
    }
    if baseline_only != set(active_replacements):
        raise ValueError("Baseline-only cases differ from the replacement map")
    if canonical_only != set(active_replacements.values()):
        raise ValueError("Canonical-only cases differ from the replacement map")
    matched_cases = set(plan["main_matrix"]["matched_case_ids"])
    upgrade_cases = {
        case_id for upgrade in upgrades for case_id in upgrade["affected_case_ids"]
    }
    if not set(active_replacements.values()) <= matched_cases | upgrade_cases:
        raise ValueError("Every active replacement case must be in the matched rerun scope")

    rerun_by_pair: dict[tuple[str, str], tuple[dict, Path]] = {}
    rerun_batches: list[tuple[list[dict], list[dict], Path, Path]] = []
    source_identities = []
    equivalent_capture_bindings = (
        canonical.get("comparison", {}).get("reference_binding_mode")
        == "equivalent_reference_bytes_with_independent_batch_captures"
    )
    ordered_main_reruns = (
        sorted(main_reruns, key=lambda root: _sha256(root / "results.jsonl"))
        if correction_layers else main_reruns
    )
    consumed_layers = set()
    for root in ordered_main_reruns:
        dataset = _read_json(root / "dataset.json")
        rows = _read_records(root)
        row_pairs = [(row.get("provider"), row.get("case_id")) for row in rows]
        if len(row_pairs) != len(set(row_pairs)):
            raise ValueError(f"Main rerun source contains duplicate provider/case results: {root}")
        all_pairs = set(row_pairs)
        _validate_record_identity(rows, allowed_pairs=all_pairs, label="main rerun source")
        batches, execution_path = _validate_source_batches(root, dataset, rows, references=True)
        bound_layers = [
            layer for layer in correction_layers
            if _bundle_matches_layer(root, execution_path, layer)
        ]
        if correction_layers and not bound_layers:
            raise ValueError(f"Main rerun source is not bound by a correction layer: {root}")
        selected_pairs = (
            {(layer["provider"], case_id) for layer in bound_layers
             for case_id in layer["case_ids"]}
            if correction_layers else all_pairs
        )
        selected_rows = [
            row for row in rows if (row["provider"], row["case_id"]) in selected_pairs
        ]
        _reject_account_capacity_results(selected_rows, label="main rerun")
        _require_canonical_cases(
            dataset,
            canonical_cases,
            selected_rows,
            "main rerun",
            equivalent_capture_bindings=equivalent_capture_bindings,
        )
        validate_report_ids(canonical, selected_rows)
        _verify_artifacts(root, selected_rows)
        rows_by_pair = {(row["provider"], row["case_id"]): row for row in rows}
        for layer in bound_layers:
            _validate_layer_execution(layer, rows_by_pair, batches, root)
            consumed_layers.add(layer["id"])
        source_identities.append(_bundle_identity(root, execution_path))
        rerun_batches.append((batches, selected_rows, root, execution_path))
        for row in selected_rows:
            pair = (row.get("provider"), row.get("case_id"))
            if pair in rerun_by_pair:
                raise ValueError(f"Main reruns contain duplicate provider/case results: {pair}")
            rerun_by_pair[pair] = (row, root)
    if correction_layers and consumed_layers != {layer["id"] for layer in correction_layers}:
        raise ValueError("Not every correction layer has a bound source bundle")
    rerun_rows = [row for row, _ in rerun_by_pair.values()]
    if not correction_layers:
        _validate_consistent_provider_run_identities(rerun_batches)
    _validate_record_identity(rerun_rows, allowed_pairs=expected_main, label="main reruns")
    rerun_attempt_identities = {}
    rerun_upgrade_providers = {upgrade["rerun_provider"] for upgrade in upgrades}
    for batches, rows, root, _source_path in rerun_batches:
        rerun_attempt_identities.update(_batch_identity_by_attempt(
            batches,
            rows,
            root,
            required_providers=rerun_upgrade_providers,
        ))
    for upgrade in upgrades:
        expected_identity = json.dumps(upgrade["rerun_implementation_identity"], sort_keys=True)
        identities = {
            json.dumps(rerun_attempt_identities[row["attempt_id"]], sort_keys=True)
            for row in rerun_rows if row["provider"] == upgrade["rerun_provider"]
        }
        if identities != {expected_identity}:
            raise ValueError("Rerun implementation identity differs from upgrade plan")

    baseline_by_pair = {(row["provider"], row["case_id"]): row for row in baseline_rows}
    selected: list[tuple[dict, Path]] = []
    audit = []
    for provider in providers:
        for case in canonical["cases"]:
            logical_pair = (provider, case["id"])
            upgrade = upgrade_by_logical.get(provider)
            affected = upgrade is not None and case["id"] in upgrade["affected_case_ids"]
            physical_provider = (
                upgrade["rerun_provider"] if affected else
                upgrade["baseline_provider"] if upgrade is not None else provider
            )
            physical_pair = (physical_provider, case["id"])
            if affected or logical_pair in rerun_by_pair:
                source_pair = physical_pair if affected else logical_pair
                row, root = rerun_by_pair[source_pair]
                old_case_id = next(
                    (old for old, new in active_replacements.items() if new == case["id"]),
                    case["id"],
                )
                old_provider = upgrade["baseline_provider"] if upgrade is not None else provider
                old = baseline_by_pair.get((old_provider, old_case_id))
                selected_row = deepcopy(row)
                if upgrade is not None:
                    selected_row["provider"] = provider
                    selected_row["physical_execution"] = {
                        "provider": row["provider"],
                        "attempt_id": row["attempt_id"],
                        "implementation_identity": rerun_attempt_identities[row["attempt_id"]],
                        "source_bundle": _bundle_identity(
                            root, next(path for _, _, candidate, path in rerun_batches
                                       if candidate == root)
                        ),
                    }
                selected.append((selected_row, root))
                audit.append(
                    {
                        "attempt_id": selected_row["attempt_id"],
                        "provider": provider,
                        "physical_provider": row["provider"],
                        "case_id": case["id"],
                        "replaces_case_id": old_case_id,
                        "selection_reason": "predeclared refresh scope; no score-based selection",
                        "declared_scopes": _pair_scope_reasons(
                            plan, row["provider"], case["id"]
                        ),
                        "source_results_sha256": _sha256(root / "results.jsonl"),
                        **({
                            "correction_layer": next(
                                layer["id"] for layer in correction_layers
                                if row["provider"] == layer["provider"]
                                and case["id"] in layer["case_ids"]
                            )
                        } if correction_layers else {}),
                        "excluded_attempt": (
                            {
                                key: old.get(key)
                                for key in ("attempt_id", "run_id", "started_at", "elapsed_ms")
                            }
                            if old
                            else None
                        ),
                    }
                )
            else:
                old_case_id = next(
                    (old for old, new in active_replacements.items() if new == case["id"]),
                    case["id"],
                )
                old = baseline_by_pair.get((physical_provider, old_case_id))
                if old is None:
                    raise ValueError(f"No baseline or rerun result for final pair: {logical_pair}")
                selected_row = deepcopy(old)
                if upgrade is not None:
                    selected_row["provider"] = provider
                    selected_row["physical_execution"] = {
                        "provider": old["provider"],
                        "attempt_id": old["attempt_id"],
                        "implementation_identity": baseline_attempt_identities[old["attempt_id"]],
                        "source_bundle": _bundle_identity(baseline, baseline_execution),
                    }
                    audit.append({
                        "attempt_id": selected_row["attempt_id"],
                        "provider": provider,
                        "physical_provider": old["provider"],
                        "case_id": case["id"],
                        "replaces_case_id": old_case_id,
                        "selection_reason": (
                            "predeclared implementation-impact complement; no score-based selection"
                        ),
                        "declared_scopes": [{
                            "scope": "implementation_upgrade_unaffected_complement",
                            **upgrade["reason"],
                            "baseline_provider": upgrade["baseline_provider"],
                        }],
                        "source_results_sha256": _sha256(baseline / "results.jsonl"),
                        "excluded_attempt": None,
                    })
                selected.append((selected_row, baseline))
    final_rows = [row for row, _ in selected]
    inherited_baseline_rows = [
        _physical_record(row) for row, root in selected if root == baseline
    ]
    _require_canonical_cases(
        baseline_dataset,
        canonical_cases,
        inherited_baseline_rows,
        "baseline inherited",
        equivalent_capture_bindings=equivalent_capture_bindings,
    )
    _validate_record_identity(
        final_rows, allowed_pairs=expected_final, label="assembled main matrix",
        allow_physical_projection=bool(upgrades),
    )

    speed_plan = plan.get("same_window_speed_cohort", {})
    if not speed_not_measured:
        assert speed_cohort is not None
        speed_dataset = _read_json(speed_cohort / "dataset.json")
        speed_rows = _read_records(speed_cohort)
        speed_case_inventory = [case.get("id") for case in speed_dataset.get("cases", [])]
        if (
            not speed_case_inventory
            or len(speed_case_inventory) != len(set(speed_case_inventory))
            or not set(speed_case_inventory) <= set(canonical_cases)
        ):
            raise ValueError("Invalid speed cohort case inventory")
        required_speed = speed_plan.get("required")
        if type(required_speed) is not bool:
            raise ValueError("Invalid speed cohort plan")
        if required_speed:
            speed_cases = speed_plan.get("case_ids")
            if (
                speed_plan.get("reuse_existing") is not False
                or not isinstance(speed_cases, list)
                or len(speed_cases) != len(set(speed_cases))
                or speed_cases != speed_case_inventory
            ):
                raise ValueError("Invalid measured speed cohort plan")
            if speed_plan.get("provider_count") != len(providers):
                raise ValueError("Speed provider count differs from the main cohort")
            expected_speed_calls = len(providers) * len(speed_cases)
            if speed_plan.get("planned_new_calls") != expected_speed_calls:
                raise ValueError("Speed call count differs from its exact matrix")
        else:
            if (
                speed_plan.get("reuse_existing") is not True
                or speed_plan.get("planned_new_calls") != 0
            ):
                raise ValueError("Unmeasured speed cohort must explicitly reuse existing evidence")
            if not isinstance(speed_plan.get("reason"), str) or not speed_plan["reason"].strip():
                raise ValueError("Reused speed cohort requires a reason")
            bindings = {
                "dataset_sha256": _sha256(speed_cohort / "dataset.json"),
                "results_sha256": _sha256(speed_cohort / "results.jsonl"),
                "execution_sha256": _sha256(speed_cohort / "execution.json"),
                "speed_comparison_sha256": _sha256(speed_cohort / "speed-comparison.json"),
            }
            if any(speed_plan.get(field) != digest for field, digest in bindings.items()):
                raise ValueError("Reused speed cohort bytes differ from the refresh plan")
            auxiliary_names = (
                "result-review.json", "scoring-revisions.json", "reference-revisions.json"
            )
            actual_auxiliary = {
                name: _sha256(speed_cohort / name)
                for name in auxiliary_names if (speed_cohort / name).is_file()
            }
            if speed_plan.get("auxiliary_sha256") != actual_auxiliary:
                raise ValueError("Reused speed auxiliary bytes differ from the refresh plan")
            speed_cases = speed_case_inventory
        speed_providers = _provider_order(speed_dataset)
        mapped_speed_providers = [logical_by_baseline.get(name, name) for name in speed_providers]
        if mapped_speed_providers != providers:
            raise ValueError("Speed provider cohort differs from the main cohort")
        expected_speed = {
            (provider, case_id) for provider in speed_providers for case_id in speed_cases
        }
        # A hash-bound reused speed cohort is historical measured evidence, not a
        # fresh execution against today's canonical scoring annotations.  Its exact
        # dataset/results/execution/summary and auxiliary receipts were verified
        # above, so do not reinterpret it as if those calls used later task rules.
        # A newly measured cohort must still match the current canonical cases.
        if required_speed:
            _require_canonical_cases(
                speed_dataset,
                canonical_cases,
                speed_rows,
                "speed cohort",
                equivalent_capture_bindings=equivalent_capture_bindings,
            )
        _validate_record_identity(speed_rows, allowed_pairs=expected_speed, label="speed cohort")
        _verify_artifacts(speed_cohort, speed_rows)
        _speed_batches, speed_execution = _validate_source_batches(
            speed_cohort, speed_dataset, speed_rows, references=True
        )
        if speed_execution.name != "execution.json":
            raise ValueError("Speed cohort requires aggregate execution.json")
        speed_execution_value = _read_json(speed_execution)
        if speed_execution_value.get("schema_version") != 1:
            raise ValueError("Unsupported speed execution schema")
        expected_speed_summary = build_speed_summary(
            speed_dataset,
            speed_rows,
            _speed_batches,
            speed_execution_value.get("aggregate"),
        )
        expected_speed_summary["bindings"] = {
            "dataset_sha256": _sha256(speed_cohort / "dataset.json"),
            "results_sha256": _sha256(speed_cohort / "results.jsonl"),
        }
        packaged_speed_summary = _read_json(speed_cohort / "speed-comparison.json")
        if packaged_speed_summary != expected_speed_summary:
            raise ValueError(
                "Speed comparison schema, bindings, or rows differ from measured evidence"
            )

    output.mkdir(parents=True)
    materialize_task_references(output, {name: input_tree(canonical_path,name)
        for name in ('sources','list-references')}, [case['id'] for case in canonical['cases']])
    _copy_artifacts(output, selected)
    materialize_reference_checks(output, baseline, baseline_batches)
    for source_batches, _rows, root, _path in rerun_batches:
        materialize_reference_checks(output, root, source_batches)

    selected_baseline = [row for row, root in selected if root == baseline]
    batches = _prune_shadowed_batch_attempts(
        deepcopy(baseline_batches), [_physical_record(row) for row in selected_baseline]
    )
    for source_batches, rows, _root, _ in rerun_batches:
        chosen = [row for row in rows if (row["provider"], row["case_id"]) in expected_main]
        batches.extend(_prune_shadowed_batch_attempts(deepcopy(source_batches), chosen))
    batch_ids = Counter(attempt for batch in batches for attempt in batch.get("attempt_ids", []))
    if batch_ids != Counter(row["attempt_id"] for row in final_rows):
        raise ValueError("Selected execution batches do not exactly cover the final main matrix")

    # Preserve the canonical dataset bytes, not just an equivalent JSON object.
    dataset_bytes = read_dataset_bytes(canonical_path)
    results_bytes = b"".join(
        (json.dumps(row, ensure_ascii=True) + "\n").encode() for row in final_rows
    )
    (output / "dataset.json").write_bytes(dataset_bytes)
    (output / "results.jsonl").write_bytes(results_bytes)
    (output / "execution.json").write_text(
        json.dumps({"batches": batches}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "selection-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _require_reproducible_verdicts(output)
    _write_composite_revisions(output, selected)

    history = output / "history" / "source-executions"
    history.mkdir(parents=True)
    execution_sources = [
        (baseline, baseline_execution),
        *[(root, path) for _, _, root, path in rerun_batches],
    ]
    for index, (root, path) in enumerate(execution_sources):
        shutil.copy2(path, history / f"{index:03d}-{root.name}-{path.name}")

    if upgrades:
        impact_history = output / "history" / "implementation-impact"
        impact_history.mkdir(parents=True)
        for index, (upgrade, binding) in enumerate(zip(upgrades, impact_receipts, strict=True)):
            source = contained_path(
                plan_path.parent, upgrade["impact_receipt"]["path"], reject_symlinks=True
            )
            relative = (
                f"history/implementation-impact/{index:03d}-"
                f"{upgrade['logical_provider']}.json"
            )
            shutil.copy2(source, output / relative)
            binding["stored_path"] = relative

    if correction_layers:
        scope_history = output / "history" / "correction-layer-scopes"
        scope_history.mkdir(parents=True)
        receipts_by_layer = {item["layer_id"]: item for item in correction_scope_receipts}
        for index, layer in enumerate(correction_layers):
            source = contained_path(
                plan_path.parent, layer["scope_receipt"]["path"], reject_symlinks=True
            )
            relative = f"history/correction-layer-scopes/{index:03d}-{layer['id']}.json"
            shutil.copy2(source, output / relative)
            receipts_by_layer[layer["id"]]["stored_path"] = relative

    if not speed_not_measured:
        assert speed_cohort is not None
        _copy_speed_bundle(
            speed_cohort, output / "speed-cohort", speed_dataset, speed_rows, _speed_batches
        )
        speed_receipt = {
            **_bundle_identity(speed_cohort, speed_execution),
            "logical_attempts": len(speed_rows),
            "new_calls": speed_plan["planned_new_calls"],
            "reused_existing": speed_plan.get("reuse_existing") is True,
            "stored_below": "speed-cohort/",
            "excluded_from_main_results": True,
            "excluded_from_main_execution": True,
        }
    else:
        speed_receipt = {
            "evidence_state": "not_measured_same_window",
            "reason": speed_plan["reason"],
            "new_calls": 0,
            "reused_existing": False,
            "stored_below": None,
            "excluded_from_main_results": True,
            "excluded_from_main_execution": True,
        }
    receipt = {
        "schema_version": 2 if upgrades else 1,
        "kind": "predeclared_webfetch_refresh_assembly",
        "assembled_at": datetime.now(UTC).isoformat(),
        "plan_sha256": _sha256(plan_path),
        "canonical_dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "baseline": {
            **_bundle_identity(baseline, baseline_execution),
            "selected_attempts": len(selected_baseline),
        },
        "main_rerun_sources": source_identities,
        "declared_new_calls": len(expected_main),
        "selected_rerun_attempts": len(rerun_rows),
        "inherited_attempts": len(selected_baseline),
        "final_logical_attempts": len(final_rows),
        "output_dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "output_results_sha256": hashlib.sha256(results_bytes).hexdigest(),
        "output_execution_sha256": _sha256(output / "execution.json"),
        "selection_audit_sha256": _sha256(output / "selection-audit.json"),
        "speed_cohort": speed_receipt,
        "provider_calls_performed_by_assembler": 0,
        "selection_policy": "exact profile scope; never select by score or retry outcome",
    }
    if upgrades:
        receipt["implementation_upgrades"] = []
        for upgrade, impact_receipt in zip(upgrades, impact_receipts, strict=True):
            logical = upgrade["logical_provider"]
            logical_rows = [row for row in final_rows if row["provider"] == logical]
            rerun_cases = set(upgrade["affected_case_ids"])
            receipt["implementation_upgrades"].append({
                "logical_provider": logical,
                "baseline_provider": upgrade["baseline_provider"],
                "rerun_provider": upgrade["rerun_provider"],
                "baseline_implementation_identity": upgrade[
                    "baseline_implementation_identity"
                ],
                "rerun_implementation_identity": upgrade["rerun_implementation_identity"],
                "affected_case_ids": upgrade["affected_case_ids"],
                "affected_case_ids_sha256": _case_ids_sha256(upgrade["affected_case_ids"]),
                "inherited_attempts": sum(
                    row["case_id"] not in rerun_cases for row in logical_rows
                ),
                "rerun_attempts": sum(row["case_id"] in rerun_cases for row in logical_rows),
                "final_logical_attempts": len(logical_rows),
                "impact_receipt": impact_receipt,
                "physical_identity_policy": (
                    "logical provider projection only; attempt IDs, artifact bindings, public "
                    "call logs, source bundle hashes and implementation identities are immutable"
                ),
            })
    if correction_layers:
        receipts_by_layer = {item["layer_id"]: item for item in correction_scope_receipts}
        receipt["correction_layers"] = [{
            "id": layer["id"],
            "provider": layer["provider"],
            "case_ids": layer["case_ids"],
            "case_ids_sha256": _case_ids_sha256(layer["case_ids"]),
            "source_bundle": layer["source_bundle"],
            "execution_contract": layer["execution_contract"],
            "scope_receipt": receipts_by_layer[layer["id"]],
            "selection_policy": "predeclared provider/case layer; outcome fields ignored",
        } for layer in correction_layers]
    (output / "refresh-assembly.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def assemble(
    baseline: Path,
    main_reruns: list[Path],
    speed_cohort: Path | None,
    output: Path,
    *,
    canonical_dataset: Path = (ROOT / "evaluation/dataset.json"),
    plan: Path = (ROOT / "evaluation/current-rerun.json"),
) -> None:
    """Validate into a sibling staging directory and expose the result atomically."""
    if speed_cohort is not None:
        reject_evidence_symlinks(speed_cohort)
    for root in [baseline, *main_reruns]:
        reject_evidence_symlinks(root)
        for name in ("dataset.json", "results.jsonl", "manifest.json", "execution.json"):
            contained_path(root, name, reject_symlinks=True)
    baseline = baseline.resolve()
    main_reruns = [root.resolve() for root in main_reruns]
    speed_cohort = speed_cohort.resolve() if speed_cohort is not None else None
    output = output.resolve()
    if not main_reruns:
        raise ValueError("At least one main rerun bundle is required")
    if output.exists():
        raise ValueError("Destination exists; evidence must not be overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    staging = work / "evidence"
    try:
        _assemble(
            baseline,
            main_reruns,
            speed_cohort,
            canonical_dataset.resolve(),
            plan.resolve(),
            staging,
        )
        staging.replace(output)
        work.rmdir()
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--main-rerun", required=True, action="append", type=Path)
    parser.add_argument("--speed-cohort", type=Path)
    parser.add_argument("--canonical-dataset", type=Path, default=(ROOT / "evaluation/dataset.json"))
    parser.add_argument("--plan", type=Path, default=(ROOT / "evaluation/current-rerun.json"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    assemble(
        args.baseline,
        args.main_rerun,
        args.speed_cohort,
        args.output,
        canonical_dataset=args.canonical_dataset,
        plan=args.plan,
    )
    print(f"Assembled refresh evidence: {args.output}")
