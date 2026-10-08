"""Assemble a declared provider-cohort refresh without selecting results by score."""

from __future__ import annotations

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

from harness.web_fetch.artifacts import (
    materialize_bound_artifacts,
    verify_record_artifacts,
)
from harness.web_fetch.assembly import (
    _copy_speed_bundle,
    _prune_shadowed_batch_attempts,
)
from harness.web_fetch.reference_inputs import input_tree, read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    reference_revision_cases,
    require_verified_reference_checks,
    valid_scoring_revision,
)
from harness.web_fetch.safety import contained_path, validate_report_ids
from harness.web_fetch.scoring_regressions import (
    build_correction_provenance,
    load_scoring_regressions,
    valid_correction_provenance,
)
from harness.web_fetch.speed_cohort import (
    build_summary as build_speed_summary,
)

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(read_dataset_bytes(path))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_records(root: Path) -> list[dict]:
    path = contained_path(root, "results.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
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


def expected_refresh_pairs(plan: dict, canonical: dict) -> set[tuple[str, str]]:
    """Return the exact main refresh scope, rejecting an internally inconsistent plan."""
    matrix = plan.get("main_matrix")
    if plan.get("schema_version") != 2 or not isinstance(matrix, dict):
        raise ValueError("Unsupported refresh plan")
    if set(plan) != {
        "schema_version", "baseline", "main_matrix", "same_window_speed_cohort",
        "total_planned_new_calls",
    } or not isinstance(plan.get("baseline"), dict) or set(plan["baseline"]) != {
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
    if matrix.get("planned_new_calls") != len(expected):
        raise ValueError("Refresh plan call count does not match its declared scopes")
    speed = plan.get("same_window_speed_cohort", {})
    if not isinstance(speed, dict) or type(speed.get("required")) is not bool:
        raise ValueError("Refresh plan has invalid speed cohort")
    speed_common = {"required", "reuse_existing", "reason", "planned_new_calls"}
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
    if plan.get("total_planned_new_calls") != len(expected) + speed_calls:
        raise ValueError("Refresh plan total call count is inconsistent")
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
    if not scopes:
        raise ValueError(f"Rerun pair has no declared scope reason: {provider}/{case_id}")
    return scopes


def _validate_record_identity(
    rows: list[dict], *, allowed_pairs: set[tuple[str, str]], label: str
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
        expected = f"{row['provider']}__{row['case_id']}__r001"
        if row.get("run") != 1 or row.get("attempt_id") != expected:
            raise ValueError(
                f"{label} has a noncanonical attempt identity: {row.get('attempt_id')}"
            )


def _verify_artifacts(root: Path, rows: list[dict]) -> None:
    for row in rows:
        verify_record_artifacts(root, row)


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


def _copy_artifacts(output: Path, selections: list[tuple[dict, Path]]) -> None:
    by_root = defaultdict(list)
    for row, root in selections:
        by_root[root].append(row)
    for root, rows in by_root.items():
        materialize_bound_artifacts(output, root, rows)


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
    speed_cohort: Path,
    canonical_path: Path,
    plan_path: Path,
    output: Path,
) -> None:
    plan = _read_json(plan_path)
    canonical = _read_json(canonical_path)
    canonical_cases = {case["id"]: case for case in canonical.get("cases", [])}
    providers = _provider_order(canonical)
    expected_main = expected_refresh_pairs(plan, canonical)
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
    if baseline_binding.get("logical_attempts") != len(baseline_rows) or set(
        baseline_providers
    ) != set(providers):
        raise ValueError("Baseline cohort differs from the refresh plan")
    _validate_record_identity(baseline_rows, allowed_pairs=baseline_pairs, label="baseline")
    _verify_artifacts(baseline, baseline_rows)
    baseline_batches, baseline_execution = _validate_source_batches(
        baseline, baseline_dataset, baseline_rows, references=False
    )

    replacements = canonical.get("comparison", {}).get("case_replacements")
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
    if not set(active_replacements.values()) <= matched_cases:
        raise ValueError("Every active replacement case must be in the matched rerun scope")

    rerun_by_pair: dict[tuple[str, str], tuple[dict, Path]] = {}
    rerun_batches: list[tuple[list[dict], list[dict], Path, Path]] = []
    source_identities = []
    equivalent_capture_bindings = (
        canonical.get("comparison", {}).get("reference_binding_mode")
        == "equivalent_reference_bytes_with_independent_batch_captures"
    )
    for root in main_reruns:
        dataset = _read_json(root / "dataset.json")
        rows = _read_records(root)
        _reject_account_capacity_results(rows, label="main rerun")
        _require_canonical_cases(
            dataset,
            canonical_cases,
            rows,
            "main rerun",
            equivalent_capture_bindings=equivalent_capture_bindings,
        )
        validate_report_ids(canonical, rows)
        _verify_artifacts(root, rows)
        batches, execution_path = _validate_source_batches(root, dataset, rows, references=True)
        source_identities.append(_bundle_identity(root, execution_path))
        rerun_batches.append((batches, rows, root, execution_path))
        for row in rows:
            pair = (row.get("provider"), row.get("case_id"))
            if pair in rerun_by_pair:
                raise ValueError(f"Main reruns contain duplicate provider/case results: {pair}")
            rerun_by_pair[pair] = (row, root)
    rerun_rows = [row for row, _ in rerun_by_pair.values()]
    _validate_consistent_provider_run_identities(rerun_batches)
    _validate_record_identity(rerun_rows, allowed_pairs=expected_main, label="main reruns")

    baseline_by_pair = {(row["provider"], row["case_id"]): row for row in baseline_rows}
    selected: list[tuple[dict, Path]] = []
    audit = []
    for provider in providers:
        for case in canonical["cases"]:
            pair = (provider, case["id"])
            if pair in rerun_by_pair:
                row, root = rerun_by_pair[pair]
                old_case_id = next(
                    (old for old, new in active_replacements.items() if new == case["id"]),
                    case["id"],
                )
                old = baseline_by_pair.get((provider, old_case_id))
                selected.append((row, root))
                audit.append(
                    {
                        "attempt_id": row["attempt_id"],
                        "provider": provider,
                        "case_id": case["id"],
                        "replaces_case_id": old_case_id,
                        "selection_reason": "predeclared refresh scope; no score-based selection",
                        "declared_scopes": _pair_scope_reasons(plan, provider, case["id"]),
                        "source_results_sha256": _sha256(root / "results.jsonl"),
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
                old = baseline_by_pair.get(pair)
                if old is None:
                    raise ValueError(f"No baseline or rerun result for final pair: {pair}")
                selected.append((old, baseline))
    final_rows = [row for row, _ in selected]
    _validate_record_identity(
        final_rows, allowed_pairs=expected_final, label="assembled main matrix"
    )

    speed_plan = plan.get("same_window_speed_cohort", {})
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
        if speed_plan.get("reuse_existing") is not True or speed_plan.get("planned_new_calls") != 0:
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
    if _provider_order(speed_dataset) != providers:
        raise ValueError("Speed provider cohort differs from the main cohort")
    expected_speed = {(provider, case_id) for provider in providers for case_id in speed_cases}
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
        raise ValueError("Speed comparison schema, bindings, or rows differ from measured evidence")

    output.mkdir(parents=True)
    for folder in ("sources", "list-references"):
        source = input_tree(canonical_path, folder)
        if not source.is_dir():
            raise ValueError(f"Canonical input tree is missing: {folder}")
        shutil.copytree(source, output / folder)
    _copy_artifacts(output, selected)
    for root in [baseline, *main_reruns]:
        if (root / "reference-checks").is_dir():
            shutil.copytree(
                root / "reference-checks", output / "reference-checks", dirs_exist_ok=True
            )

    selected_baseline = [row for row, root in selected if root == baseline]
    batches = _prune_shadowed_batch_attempts(deepcopy(baseline_batches), selected_baseline)
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
    _write_composite_revisions(output, selected)

    history = output / "history" / "source-executions"
    history.mkdir(parents=True)
    execution_sources = [
        (baseline, baseline_execution),
        *[(root, path) for _, _, root, path in rerun_batches],
    ]
    for index, (root, path) in enumerate(execution_sources):
        shutil.copy2(path, history / f"{index:03d}-{root.name}-{path.name}")

    _copy_speed_bundle(
        speed_cohort, output / "speed-cohort", speed_dataset, speed_rows, _speed_batches
    )
    receipt = {
        "schema_version": 1,
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
        "speed_cohort": {
            **_bundle_identity(speed_cohort, speed_execution),
            "logical_attempts": len(speed_rows),
            "new_calls": speed_plan["planned_new_calls"],
            "reused_existing": speed_plan.get("reuse_existing") is True,
            "stored_below": "speed-cohort/",
            "excluded_from_main_results": True,
            "excluded_from_main_execution": True,
        },
        "provider_calls_performed_by_assembler": 0,
        "selection_policy": "exact profile scope; never select by score or retry outcome",
    }
    (output / "refresh-assembly.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def assemble(
    baseline: Path,
    main_reruns: list[Path],
    speed_cohort: Path,
    output: Path,
    *,
    canonical_dataset: Path = ROOT / "evaluation/dataset.json",
    plan: Path = ROOT / "evaluation/current-rerun.json",
) -> None:
    """Validate into a sibling staging directory and expose the result atomically."""
    if speed_cohort.is_symlink():
        raise ValueError("Speed cohort root must not be a symlink")
    for root in [baseline, *main_reruns]:
        if root.is_symlink():
            raise ValueError("Evidence root must not be a symlink")
        for name in ("dataset.json", "results.jsonl", "manifest.json", "execution.json"):
            contained_path(root, name, reject_symlinks=True)
    baseline = baseline.resolve()
    main_reruns = [root.resolve() for root in main_reruns]
    speed_cohort = speed_cohort.resolve()
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
    parser.add_argument("--speed-cohort", required=True, type=Path)
    parser.add_argument("--canonical-dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--plan", type=Path, default=ROOT / "evaluation/current-rerun.json")
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
