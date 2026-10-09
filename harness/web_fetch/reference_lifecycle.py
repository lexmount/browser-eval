"""Offline, bounded reference checks for new body measurements.

No reference is refreshed here. Independent observations must be supplied by the
caller. A completed certificate describes one run, not freshness for another.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from harness.record_io import load_jsonl, parse_jsonl

from .metrics import normalize_for_match, normalize_resource_url
from .reference_inputs import input_tree
from .render_evidence import validate_render_reference
from .safety import contained_path, safe_identifier

MAX_REFERENCE_AGE_SECONDS = 30 * 60


class ReferenceLifecycleError(ValueError):
    """Reference uncertainty is never a provider failure."""

    def __init__(self, message: str, status: str = "reference_unverified"):
        super().__init__(message)
        self.status = status


def _get(case, key, default=None):
    return case.get(key, default) if isinstance(case, dict) else getattr(case, key, default)


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def scoring_revision_history_sha256(rows) -> str:
    """Hash prior receipts canonically so an assembled receipt forms a verifiable chain."""
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(payload)


def timestamp(value) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    raise ReferenceLifecycleError("Invalid reference timestamp: explicit timezone required")


def _now(now=None) -> datetime:
    return timestamp(now) if now is not None else datetime.now(timezone.utc)


def _fresh(value, now: datetime) -> datetime:
    checked = timestamp(value)
    if not 0 <= (now - checked).total_seconds() <= MAX_REFERENCE_AGE_SECONDS:
        raise ReferenceLifecycleError("Reference capture is future-dated or older than 30 minutes")
    return checked


def _not_future(value, now: datetime) -> datetime:
    """Parse an evidence timestamp and reject captures after receipt creation.

    Post-call freshness is measured against the relevant provider call, not the
    later time at which an offline certificate is assembled.  Keeping this
    check separate prevents a valid retained observation from expiring merely
    because certification is resumed later.
    """
    checked = timestamp(value)
    if checked > now:
        raise ReferenceLifecycleError("Reference capture is future-dated")
    return checked


def source_unqualified_contract_cases(cases) -> list[str]:
    """Invariant rule validation is not current source-solvability evidence.

    The generic lifecycle does not currently qualify live acceptance contracts.
    Specialized frozen-input tracks have their own certificates and boundaries.
    """
    return [_get(case, 'id') for case in cases if _get(case, 'acceptance_contract', {})]


def requires_fresh_reference(case) -> bool:
    contract = _get(case, "acceptance_contract", {})
    if contract:
        # This mode evaluates invariant rules, not equality with live values.
        # Validate before choosing it; snapshot-bound runs retain all checks.
        from .stable_tasks import validate_acceptance_contract
        validate_acceptance_contract(contract)
        return False
    # Tiny synthetic fixtures intentionally have no independent verification.
    # Production body cases carry this metadata, including invalid attestations.
    return (_get(case, "track", "web") in {"web", "dynamic", "challenge"}
            and _get(case, "task_kind", "body") == "body"
            and bool((_get(case, "ground_truth_main_content", "") or "").strip())
            and bool(_get(case, "source_verification", {})))


def expected_providers(dataset, case):
    """Require an explicit, unambiguous matched cohort for a reference check."""
    names = dataset.get('coverage_plan', {}).get(_get(case, 'track', 'web'))
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) for name in names)
            or len(names) != len(set(names))):
        raise ValueError('Reference checks require a nonempty unique provider coverage plan')
    return {safe_identifier(name) for name in names}


def reference_cases(dataset):
    """Select the same live reference population for acquisition and certification."""
    cases, seen = {}, set()
    for case in dataset['cases']:
        identifier = safe_identifier(case['id'])
        if identifier in seen:
            raise ValueError('Duplicate reference case: ' + identifier)
        seen.add(identifier)
        if requires_fresh_reference(case):
            expected_providers(dataset, case)
            requires_browser_observation(case)
            cases[identifier] = case
    return cases


def requires_browser_observation(case):
    """Select reference acquisition from declared provenance, never a task ID."""
    verification = _get(case, 'source_verification', {}) or {}
    route = verification.get('observation_route')
    if route not in (None, 'http', 'browser'):
        raise ValueError('Reference observation_route must be http or browser')
    # Existing frozen inputs already name these acquisition methods. Preserve
    # their meaning without changing their historical bytes or task identifiers.
    browser = verification.get('method') in {
        'Independent visible browser DOM; complete declared scope',
        'Independent rendered browser DOM; complete declared scope',
    }
    if browser and route == 'http':
        raise ValueError('HTTP observation route contradicts browser reference provenance')
    return browser or route == 'browser'


def completed_case_records(dataset, case, records):
    """Return one logical result per declared method, or wait for missing results."""
    expected = expected_providers(dataset, case)
    relevant = [row for row in records if row['case_id'] == _get(case, 'id')]
    methods = [row['provider'] for row in relevant]
    if set(methods) - expected:
        raise ValueError('Undeclared provider in reference-check cohort')
    if len(methods) != len(set(methods)):
        raise ValueError('Duplicate provider in reference-check cohort')
    return relevant if set(methods) == expected else None


def scope_identity(case) -> dict[str, Any]:
    verification = _get(case, "source_verification", {}) or {}
    benchmark = _get(case, "source_benchmark", {}) or {}
    render = benchmark.get("render_evidence", {})
    return deepcopy({
        "case_id": _get(case, "id"), "url": _get(case, "url"),
        "track": _get(case, "track", "web"), "task_kind": _get(case, "task_kind", "body"),
        "task_prompt": _get(case, "task_prompt", ""),
        "reference_scope": verification.get("reference_scope"),
        "selector": verification.get("selector"),
        "source_benchmark": {key: value for key, value in benchmark.items() if key != "render_evidence"},
        "render_scope": render.get("scope"),
        "evaluation_contract": {key: _get(case, key, default) for key, default in (
            ("expected_any", []), ("required_facts", []), ("required_patterns", []),
            ("forbidden_any", []), ("min_content_chars", 1), ("fact_coverage_threshold", 1.0),
            ("content_score_applicable", True), ("expected_resources", []),
            ("resource_output_contract", {}), ("reference_answer", ""))},
    })


def resource_source_scope_identity(case) -> dict[str, Any]:
    """Stable source-observation scope for resource ground truth."""
    return deepcopy({
        "case_id": _get(case, "id"),
        "url": _get(case, "url"),
        "track": _get(case, "track", "web"),
        "task_kind": _get(case, "task_kind", "body"),
        "expected_resources": _get(case, "expected_resources", []),
        "resource_output_contract": _get(case, "resource_output_contract", {}),
    })


def _resource_target_matches(case, candidates: list[dict]) -> list[dict]:
    base_url = _get(case, "url")
    observed = sorted({url for row in candidates
                       if isinstance(row, dict)
                       and (url := normalize_resource_url(str(row.get("url", "")), base_url))})
    results = []
    for index, target in enumerate(_get(case, "expected_resources", [])):
        aliases = {url for item in [*target.get("urls", []), *target.get("aliases", [])]
                   if (url := normalize_resource_url(str(item), base_url))}
        patterns = target.get("url_patterns", [])
        try:
            matched = sorted({url for url in observed
                              if url in aliases or any(re.search(pattern, url) for pattern in patterns)})
        except re.error as error:
            raise ReferenceLifecycleError("Invalid resource source URL pattern") from error
        results.append({
            "id": str(target.get("id") or f"resource-{index + 1}"),
            "matched_urls": matched,
            "satisfied": bool(matched),
        })
    return results


def validate_resource_source_evidence(case, dataset_path: Path) -> dict | None:
    """Fail closed unless every resource target has independent, hash-bound evidence.

    HTTP probes remain useful diagnostics, but a blocked probe cannot become a
    valid source merely because an unbound human-review note says the page was
    checked. A successful browser observation may qualify only when its bounded
    candidate list, completion condition and exact resource scope are retained.
    """
    if _get(case, "task_kind", "body") != "resource":
        return None
    verification = _get(case, "source_verification", {}) or {}
    # Tiny synthetic fixtures may intentionally omit source metadata. Once a
    # resource case claims verification, however, the persistent evidence is
    # mandatory and validated before any provider call.
    if not verification:
        return None
    identifier = safe_identifier(_get(case, "id"))
    if verification.get("state") != "valid":
        raise ReferenceLifecycleError(
            f"{identifier}: independent resource source verification is not valid")
    expected_path = f"sources/{identifier}/evidence.json"
    evidence_path = verification.get("evidence_path")
    evidence_hash = verification.get("evidence_sha256")
    if evidence_path != expected_path or not isinstance(evidence_hash, str) \
            or re.fullmatch(r"[0-9a-f]{64}", evidence_hash) is None:
        raise ReferenceLifecycleError(f"{identifier}: resource evidence binding is missing")
    try:
        root = input_tree(Path(dataset_path), "sources")
        evidence_raw = contained_path(root, f"{identifier}/evidence.json").read_bytes()
        if sha256(evidence_raw) != evidence_hash:
            raise ReferenceLifecycleError(
                f"{identifier}: resource evidence SHA-256 mismatch", "reference_changed")
        evidence = json.loads(evidence_raw)
    except ReferenceLifecycleError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise ReferenceLifecycleError(f"{identifier}: invalid resource source evidence") from error
    source = evidence.get("source") if isinstance(evidence, dict) else None
    if (not isinstance(evidence, dict) or evidence.get("case_id") != identifier
            or evidence.get("url") != _get(case, "url")
            or not isinstance(source, str) or "independent" not in source.casefold()
            or "no provider output" not in source.casefold()):
        raise ReferenceLifecycleError(f"{identifier}: resource evidence is not independent or in scope")
    timestamp(evidence.get("checked_at"))

    candidates = evidence.get("resources", [])
    observed_candidates = evidence.get("observed_resource_candidates", [])
    if (not isinstance(candidates, list) or not isinstance(observed_candidates, list)
            or len(candidates) + len(observed_candidates) > 4096):
        raise ReferenceLifecycleError(f"{identifier}: invalid or unbounded resource candidates")
    candidates = [*candidates, *observed_candidates]
    source_qualified = evidence.get("state") == "captured" and evidence.get("status") == 200

    browser_binding = evidence.get("independent_browser_observation")
    if browser_binding is not None:
        if (not isinstance(browser_binding, dict)
                or browser_binding.get("path") != "browser-observation.json"
                or not isinstance(browser_binding.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", browser_binding["sha256"]) is None):
            raise ReferenceLifecycleError(f"{identifier}: invalid browser observation binding")
        try:
            folder = contained_path(root, identifier)
            observation_raw = contained_path(folder, browser_binding["path"]).read_bytes()
            if sha256(observation_raw) != browser_binding["sha256"]:
                raise ReferenceLifecycleError(
                    f"{identifier}: browser observation SHA-256 mismatch", "reference_changed")
            observation = json.loads(observation_raw)
        except ReferenceLifecycleError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise ReferenceLifecycleError(f"{identifier}: invalid browser observation") from error
        observed_candidates = observation.get("resource_candidates", [])
        canonical = json.dumps(observed_candidates, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        if (observation.get("case_id") != identifier
                or observation.get("url") != _get(case, "url")
                or observation.get("independent") is not True
                or observation.get("resource_scope_identity") != resource_source_scope_identity(case)
                or not isinstance(observed_candidates, list) or len(observed_candidates) > 512
                or observation.get("resource_candidates_sha256") != sha256(canonical)):
            raise ReferenceLifecycleError(f"{identifier}: browser observation scope/hash mismatch")
        timestamp(observation.get("checked_at"))
        browser_matches = _resource_target_matches(case, observed_candidates)
        browser_qualified = (
            observation.get("status") == "success"
            and observation.get("completion", {}).get("satisfied") is True
            and browser_matches
            and all(row["satisfied"] for row in browser_matches)
            and observation.get("target_matches") == browser_matches
        )
        if browser_binding.get("status") != observation.get("status") \
                or browser_binding.get("completion_satisfied") is not observation.get("completion", {}).get("satisfied"):
            raise ReferenceLifecycleError(f"{identifier}: browser observation summary mismatch")
        if browser_qualified:
            candidates = observed_candidates
            source_qualified = True

    matches = _resource_target_matches(case, candidates)
    if (not source_qualified or not matches or not all(row["satisfied"] for row in matches)):
        raise ReferenceLifecycleError(
            f"{identifier}: independent evidence does not cover every expected resource")
    return {
        "evidence_path": evidence_path,
        "evidence_sha256": evidence_hash,
        "scope_identity": resource_source_scope_identity(case),
        "target_matches": matches,
    }


def evaluation_scope_identity(case) -> dict[str, Any]:
    """Return the scoring scope, excluding capture-procedure wording.

    ``source_verification.selector`` documents how one independent observer
    bounded its capture.  Distinct observers may describe the same scoring
    scope differently, so assembled equivalent-reference batches compare the
    contract, URL, reference scope and rendered scope while preserving the
    exact selector in each batch's strict binding and postcheck.
    """
    identity = scope_identity(case)
    identity.pop("selector", None)
    _normalize_draft_mutable_body_metadata(identity)
    return identity


def _binding_evaluation_scope_identity(binding: dict) -> dict[str, Any]:
    identity = deepcopy(binding.get("scope_identity", {}))
    identity.pop("selector", None)
    _normalize_draft_mutable_body_metadata(identity)
    return identity


def _normalize_draft_mutable_body_metadata(identity: dict[str, Any]) -> None:
    """Remove report-only body metadata that never affected a provider call.

    A short-lived dataset revision placed this policy under source_benchmark.
    The authoritative schema now binds it through mutable_body_policy and the
    scoring revision receipt.  List-task annotations remain capture scope and
    must not be normalized away.
    """
    benchmark = identity.get("source_benchmark", {})
    annotation = benchmark.get("stable_annotation", {})
    if annotation.get("content_kind") == "changing_public_body":
        benchmark.pop("snapshot_metric_scope", None)
        benchmark.pop("stable_annotation", None)


def _completion(completion) -> None:
    if not isinstance(completion, dict) or completion.get("satisfied") is not True:
        raise ReferenceLifecycleError("Independent completion was not satisfied")
    elapsed, timeout = completion.get("elapsed_ms"), completion.get("timeout_ms")
    if (type(elapsed) not in (int, float) or type(timeout) not in (int, float)
            or not math.isfinite(elapsed) or not math.isfinite(timeout)
            or not 0 <= elapsed <= timeout or timeout <= 0):
        raise ReferenceLifecycleError("Independent completion is unbounded or exceeded its deadline")


def build_observations(run_dir: Path, captures_root: Path,
                       browser_observations: Path | None = None) -> list[dict]:
    """Validate independent post-call receipts without inventing their provenance."""
    manifest_path = contained_path(run_dir, "manifest.json", reject_symlinks=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bindings = manifest.get("reference_checks", {}).get("cases")
    if not isinstance(bindings, dict) or not bindings:
        raise ValueError("Postcheck requires frozen reference bindings")
    rendered = {}
    if browser_observations is not None:
        if browser_observations.is_symlink():
            raise ValueError("Symlinked browser evidence is forbidden")
        payload = json.loads(browser_observations.read_text(encoding="utf-8"))
        rows = (payload.get("observations", list(payload.values()))
                if isinstance(payload, dict) else payload)
        if not isinstance(rows, list):
            raise ValueError("Browser observations must be a list or case mapping")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Browser observation must be an object")
            identifier = safe_identifier(row.get("case_id"))
            if identifier in rendered:
                raise ValueError("Duplicate browser observation: " + identifier)
            rendered[identifier] = row
        if isinstance(payload, dict) and "observations" not in payload:
            if any(key != row.get("case_id") for key, row in payload.items()):
                raise ValueError("Browser observation mapping identity mismatch")
        if set(rendered) - set(bindings):
            raise ValueError("Browser observations contain undeclared cases")
    observations = []
    for case_id, binding in bindings.items():
        safe_identifier(case_id)
        scope = binding["scope_identity"]
        if case_id in rendered:
            row = rendered[case_id]
            if (row.get("independent") is not True
                    or row.get("scope_identity") != scope):
                raise ValueError("Browser observation independence/scope mismatch: " + case_id)
            timestamp(row.get("checked_at"))
            _completion(row.get("completion"))
            observations.append(deepcopy(row))
            continue
        if requires_browser_observation(binding):
            raise ValueError("Browser observation required: " + case_id)
        folder = contained_path(captures_root, case_id, reject_symlinks=True)
        evidence_path = contained_path(folder, "evidence.json", reject_symlinks=True)
        capture = json.loads(evidence_path.read_text(encoding="utf-8"))
        if (not isinstance(capture, dict) or capture.get("case_id") != case_id
                or capture.get("url") != scope.get("url")
                or capture.get("scope_identity") != scope
                or capture.get("independent") is not True
                or capture.get("source") != "independent HTTP GET (no provider output)"
                or capture.get("state") != "captured" or capture.get("status") != 200
                or capture.get("missing_facts") != [] or capture.get("quality_issues") != []):
            raise ValueError("Incomplete or out-of-scope post-call capture: " + case_id)
        timestamp(capture.get("checked_at"))
        _completion(capture.get("completion"))
        content_path = contained_path(folder, "reference.txt", reject_symlinks=True)
        content = content_path.read_text(encoding="utf-8")
        if not content.strip() or sha256(content) != capture.get("reference_sha256"):
            raise ValueError("Post-call capture content hash mismatch: " + case_id)
        observations.append({
            "case_id": case_id, "independent": True,
            "checked_at": capture["checked_at"], "content_text": content,
            "content_sha256": capture["reference_sha256"],
            "scope_identity": deepcopy(scope),
            "completion": deepcopy(capture["completion"]),
        })
    return observations


def observations_main(argv=None) -> None:
    """Command boundary for assembling independently captured postchecks."""
    import argparse

    parser = argparse.ArgumentParser(description=build_observations.__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--browser-observations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output exists")
    observations = build_observations(args.run_dir, args.captures, args.browser_observations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(observations, ensure_ascii=False, indent=2) + "\n")
    print("Postcheck observations:", len(observations))


def _binding(case, dataset_path: Path, now: datetime) -> dict[str, Any]:
    identifier = safe_identifier(_get(case, "id"))
    verification = _get(case, "source_verification")
    reference = _get(case, "ground_truth_main_content")
    if not isinstance(verification, dict) or verification.get("state") != "valid":
        raise ReferenceLifecycleError(f"{identifier}: independent source verification is not valid")
    if not isinstance(verification.get("method"), str) or not verification["method"].strip():
        raise ReferenceLifecycleError(f"{identifier}: independent verification method is missing")
    _fresh(verification.get("checked_at"), now)
    actual_hash = sha256(reference)
    if verification.get("reference_sha256") != actual_hash:
        raise ReferenceLifecycleError(f"{identifier}: reference SHA-256 mismatch", "reference_changed")
    binding = {
        "reference_sha256": actual_hash,
        "expected_normalized_sha256": sha256(normalize_for_match(reference)),
        "checked_at": verification["checked_at"],
        "scope_identity": scope_identity(case),
        "source_verification": deepcopy(verification),
    }
    if _get(case, "track", "web") == "dynamic":
        render = (_get(case, "source_benchmark", {}) or {}).get("render_evidence", {})
        completion = render.get("completion", {})
        _completion(completion)
        _fresh(completion.get("checked_at"), now)
        source = Path(identifier)
        try:
            root = input_tree(Path(dataset_path), "sources")
            initial = contained_path(root, source / "initial.html.txt").read_bytes()
            rendered_raw = contained_path(root, source / "rendered.txt").read_bytes()
            reference_raw = contained_path(root, source / "reference.txt").read_bytes()
            evidence_raw = contained_path(root, source / "evidence.json").read_bytes()
            evidence = json.loads(evidence_raw)
            rendered = rendered_raw.decode("utf-8")
        except (OSError, ValueError) as error:
            raise ReferenceLifecycleError(f"{identifier}: missing or invalid paired source evidence") from error
        if not isinstance(evidence, dict):
            raise ReferenceLifecycleError(f"{identifier}: invalid source evidence")
        _fresh(evidence.get("checked_at"), now)
        _fresh(evidence.get("initial_checked_at"), now)
        if (evidence.get("case_id") != identifier or evidence.get("url") != _get(case, "url")
                or evidence.get("completion") != render.get("scope")):
            raise ReferenceLifecycleError(f"{identifier}: paired source scope mismatch")
        audit = validate_render_reference(case, initial, rendered)
        if not audit["valid"]:
            raise ReferenceLifecycleError(f"{identifier}: incomplete render reference: {audit['errors']}")
        if (sha256(reference_raw) != actual_hash
                or normalize_for_match(rendered) != normalize_for_match(reference)
                or any(evidence.get(key) != value for key, value in (
                    ("initial_sha256", sha256(initial)), ("rendered_sha256", sha256(rendered_raw)),
                    ("reference_sha256", actual_hash)))):
            raise ReferenceLifecycleError(f"{identifier}: paired source hash/content mismatch", "reference_changed")
        count = (_get(case, "source_benchmark", {}) or {}).get("expected_entries")
        if count is not None and (evidence.get("expected_entries") != count or evidence.get("observed_entries") != count):
            raise ReferenceLifecycleError(f"{identifier}: incomplete declared source scope")
        binding["render_evidence"] = deepcopy(render)
        binding["source_files_sha256"] = {str(Path("sources") / source / name): sha256(raw) for name, raw in (
            ("initial.html.txt", initial), ("rendered.txt", rendered_raw),
            ("reference.txt", reference_raw), ("evidence.json", evidence_raw))}
    return binding


def preflight_reference_checks(cases, dataset_path: Path, now=None) -> dict | None:
    """Read-only preflight; call before creating outputs or invoking providers."""
    try:
        input_tree(dataset_path, "sources")
    except ValueError as error:
        raise ReferenceLifecycleError('Invalid reference input tree: ' + str(error)) from error
    checked = _now(now)
    bindings = {}
    for case in cases:
        validate_resource_source_evidence(case, dataset_path)
        if requires_fresh_reference(case):
            identifier = _get(case, "id")
            if identifier in bindings:
                raise ReferenceLifecycleError("Duplicate reference case")
            bindings[identifier] = _binding(case, dataset_path, checked)
    if not bindings:
        return None
    return {"schema_version": 1, "status": "awaiting_postcheck",
            "preflight_checked_at": checked.isoformat(),
            "max_age_seconds": MAX_REFERENCE_AGE_SECONDS, "cases": bindings}


def check_case_reference_fresh(case, dataset_path: Path, binding: dict | None, now=None) -> None:
    """Recheck after throttling, immediately before fetch, outside its exception handler."""
    if binding is None and not requires_fresh_reference(case):
        return
    if binding is None or not requires_fresh_reference(case):
        raise ReferenceLifecycleError("Reference eligibility changed during the run", "reference_changed")
    if _binding(case, dataset_path, _now(now)) != binding:
        raise ReferenceLifecycleError("Frozen reference binding changed during the run", "reference_changed")


def check_case_reference_binding(case, binding: dict) -> None:
    """Offline rescores must use the original capture; no freshness claim is made."""
    reference = _get(case, "ground_truth_main_content", "")
    if (sha256(reference) != binding.get("reference_sha256")
            or sha256(normalize_for_match(reference)) != binding.get("expected_normalized_sha256")
            or scope_identity(case) != binding.get("scope_identity")
            or _get(case, "source_verification", {}) != binding.get("source_verification")
            or ("render_evidence" in binding and
                (_get(case, "source_benchmark", {}) or {}).get("render_evidence") != binding["render_evidence"])):
        raise ReferenceLifecycleError("Rescore requires the original frozen reference and capture binding", "reference_changed")


def check_case_equivalent_reference_binding(case, binding: dict) -> None:
    """Allow distinct independent captures only when reference bytes and scope are identical."""
    reference = _get(case, "ground_truth_main_content", "")
    verification = _get(case, "source_verification", {})
    if (sha256(reference) != binding.get("reference_sha256")
            or sha256(normalize_for_match(reference)) != binding.get("expected_normalized_sha256")
            or evaluation_scope_identity(case) != _binding_evaluation_scope_identity(binding)
            or (requires_fresh_reference(case)
                and verification.get("reference_sha256") != binding.get("reference_sha256"))
            or ("render_evidence" in binding and
                (_get(case, "source_benchmark", {}) or {}).get("render_evidence") != binding["render_evidence"])):
        raise ReferenceLifecycleError(
            f"{_get(case, 'id')}: assembled batch differs in reference bytes or evaluation scope",
            "reference_changed")


def check_case_scoring_revision_binding(case, binding: dict) -> None:
    """Validate immutable capture/task scope while allowing an audited rule correction.

    This is intentionally separate from normal rescoring.  It permits only the
    evaluation-contract portion of the original scope identity to differ; the
    URL, reference bytes, reference scope and render scope remain bound to the
    measured call.  Resource-task wording may change only for the audited
    target-only ``reject`` to recall-only ``allow`` correction whose target
    inventory and answer-bearing candidate scope are unchanged.
    """
    reference = _get(case, "ground_truth_main_content", "")
    verification = _get(case, "source_verification", {})
    current = evaluation_scope_identity(case)
    original = _binding_evaluation_scope_identity(binding)
    before_contract = original.get("evaluation_contract", {})
    after_contract = current.get("evaluation_contract", {})
    before_policy = before_contract.get("resource_output_contract", {})
    after_policy = after_contract.get("resource_output_contract", {})
    before_targets = before_contract.get("expected_resources", [])
    after_targets = after_contract.get("expected_resources", [])
    aliases_expand_only = (
        len(before_targets) == len(after_targets)
        and all(
            prior.get("id") == current_target.get("id")
            and prior.get("kind") == current_target.get("kind")
            and prior.get("url_patterns", []) == current_target.get("url_patterns", [])
            and set(prior.get("urls", [])) <= set(current_target.get("urls", []))
            for prior, current_target in zip(before_targets, after_targets)
        )
    )
    prompt_policy_correction = (
        current.get("task_kind") == original.get("task_kind") == "resource"
        and aliases_expand_only
        and before_policy.get("candidate_scope") == after_policy.get("candidate_scope")
            == "image_answer_fields"
        and before_policy.get("unexpected") == "reject"
        and after_policy.get("unexpected") == "allow"
        and {key: value for key, value in before_policy.items() if key != "unexpected"}
            == {key: value for key, value in after_policy.items() if key != "unexpected"}
    )
    current.pop("evaluation_contract", None)
    original.pop("evaluation_contract", None)
    if prompt_policy_correction:
        current.pop("task_prompt", None)
        original.pop("task_prompt", None)
    if (sha256(reference) != binding.get("reference_sha256")
            or current != original
            or (requires_fresh_reference(case)
                and verification.get("reference_sha256") != binding.get("reference_sha256"))
            or ("render_evidence" in binding and
                (_get(case, "source_benchmark", {}) or {}).get("render_evidence") != binding["render_evidence"])):
        raise ReferenceLifecycleError(
            f"{_get(case, 'id')}: scoring revision differs from the captured task or reference scope",
            "reference_changed")


def _selected_records(batch, records, original_elapsed=None):
    ids = batch.get("attempt_ids")
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or batch.get("attempt_count") != len(ids)):
        raise ReferenceLifecycleError("Incomplete batch attempt identity")
    selected = [r for r in records if r.get("attempt_id") in ids]
    if len(selected) != len(ids) or {r.get("attempt_id") for r in selected} != set(ids):
        raise ReferenceLifecycleError("Missing or duplicate measured attempts")
    start, end = timestamp(batch.get("started_at")), timestamp(batch.get("completed_at"))
    if end < start:
        raise ReferenceLifecycleError("Invalid batch time bounds")
    for record in selected:
        began = timestamp(record.get("started_at"))
        elapsed = (original_elapsed or {}).get(
            (record.get("provider"), record.get("case_id")), record.get("elapsed_ms"))
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ReferenceLifecycleError("Invalid call elapsed time")
        finished = began + timedelta(milliseconds=elapsed)
        # New runners retain the exact finish; older records have start + elapsed.
        if record.get("completed_at") is not None:
            finished = max(finished, timestamp(record["completed_at"]))
        if not start <= began <= finished <= end + timedelta(milliseconds=.01):
            raise ReferenceLifecycleError("Call outside batch time bounds")
    return selected


def _postcheck_case(binding, case, observation, records, now):
    check_case_reference_binding(case, binding)
    if observation.get("independent") is not True:
        raise ReferenceLifecycleError("Observation must attest independent capture, not provider output")
    if observation.get("scope_identity") != binding["scope_identity"]:
        raise ReferenceLifecycleError("Observation scope identity mismatch")
    _completion(observation.get("completion"))
    checked = _not_future(observation.get("checked_at"), now)
    if not records:
        raise ReferenceLifecycleError("Reference case has no measured call")
    finishes = []
    for record in records:
        if record.get("metrics", {}).get("reference_sha256") != binding["reference_sha256"]:
            raise ReferenceLifecycleError("Measured reference hash mismatch")
        finish = timestamp(record["started_at"]) + timedelta(milliseconds=record["elapsed_ms"])
        if record.get("completed_at") is not None:
            finish = max(finish, timestamp(record["completed_at"]))
        finishes.append(finish)
    latest = max(finishes)
    if not 0 < (checked - latest).total_seconds() <= MAX_REFERENCE_AGE_SECONDS:
        raise ReferenceLifecycleError("Observation must follow the latest relevant call within 30 minutes")
    content = observation.get("content_text")
    if not isinstance(content, str) or not content.strip():
        raise ReferenceLifecycleError("Observation content is missing")
    actual = sha256(content)
    normalized = sha256(normalize_for_match(content))
    if "content_sha256" in observation and observation["content_sha256"] != actual:
        raise ReferenceLifecycleError("Observation content hash mismatch")
    return {"status": "verified" if normalized == binding["expected_normalized_sha256"] else "reference_changed",
            "checked_at": observation["checked_at"], "latest_call_finished_at": latest.isoformat(),
            "content_sha256": actual, "normalized_sha256": normalized}


def finalize_reference_checks(run_dir: Path, observations_path: Path, now=None) -> dict:
    """Finalize manifest/execution batches using an offline observation list.

    JSON is a list (or {"observations": [...]}) of objects containing case_id,
    independent: true, content_text, checked_at, scope_identity (copied exactly
    from the binding), and completion {satisfied, elapsed_ms, timeout_ms}.
    Only reference_checks.status/postcheck may change in original manifests.
    Legacy batches are left untouched and never acquire a freshness claim.
    """
    root = Path(run_dir).resolve()
    checked = _now(now)
    documents = {}
    for filename in ("manifest.json", "execution.json"):
        path = contained_path(root, filename)
        if path.exists():
            documents[filename] = json.loads(path.read_bytes())
    batches = []
    if "manifest.json" in documents:
        batches.append(documents["manifest.json"])
    if "execution.json" in documents:
        execution_batches = documents["execution.json"].get("batches")
        if not isinstance(execution_batches, list) or not execution_batches:
            raise ReferenceLifecycleError("Invalid execution batches")
        batches.extend(execution_batches)
    guarded = [b for b in batches if "reference_checks" in b]
    if not guarded:
        raise ReferenceLifecycleError("No live reference lifecycle; historical batches cannot be certified retroactively")
    raw = Path(observations_path).read_bytes()
    # Hash-addressed, exclusive evidence files never replace existing raw data.
    evidence_rel = f"reference-checks/observations-{sha256(raw)}.json"
    evidence_path = contained_path(root, evidence_rel)
    if evidence_path.exists() and evidence_path.read_bytes() != raw:
        raise ReferenceLifecycleError("Postcheck evidence hash pointer mismatch")
    records_raw = contained_path(root, "results.jsonl").read_bytes()
    records = parse_jsonl(records_raw, skip_blank=True)
    dataset_raw = contained_path(root, "dataset.json").read_bytes()
    dataset_cases = json.loads(dataset_raw)["cases"]
    cases = {_get(c, "id"): c for c in dataset_cases}
    if len(cases) != len(dataset_cases):
        raise ReferenceLifecycleError("Duplicate dataset case IDs")
    expected = {key for b in guarded for key in b["reference_checks"]["cases"]}
    observations, input_error = {}, None
    try:
        payload = json.loads(raw)
        rows = payload.get("observations") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ReferenceLifecycleError("Missing observation list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
                raise ReferenceLifecycleError("Invalid observation")
            identifier = row["case_id"]
            if identifier in observations or identifier not in expected:
                raise ReferenceLifecycleError("Duplicate or unknown observation case")
            observations[identifier] = row
        if observations.keys() != expected:
            raise ReferenceLifecycleError("Missing required case observations")
    except (ValueError, TypeError) as error:
        input_error = str(error)
    outcomes = []
    for batch in guarded:
        checks = batch["reference_checks"]
        results = {}
        try:
            if input_error:
                raise ReferenceLifecycleError(input_error)
            selected = _selected_records(batch, records)
            for identifier, binding in checks["cases"].items():
                try:
                    if identifier not in cases:
                        raise ReferenceLifecycleError("Reference case absent from frozen dataset")
                    results[identifier] = _postcheck_case(binding, cases[identifier], observations[identifier],
                        [r for r in selected if r["case_id"] == identifier], checked)
                except (ReferenceLifecycleError, KeyError, TypeError, ValueError, OverflowError) as error:
                    results[identifier] = {"status": getattr(error, "status", "reference_unverified"), "error": str(error)}
        except (ReferenceLifecycleError, KeyError, TypeError, ValueError, OverflowError) as error:
            results = {identifier: {"status": "reference_unverified", "error": str(error)} for identifier in checks["cases"]}
        statuses = {row["status"] for row in results.values()}
        status = ("reference_changed" if "reference_changed" in statuses else
                  "verified" if statuses == {"verified"} else "reference_unverified")
        # Never erase an observed change, expired pre-call check, or earlier
        # failed postcheck by retrying until the page happens to match again.
        if checks.get("status") in {"reference_changed", "reference_unverified"}:
            status = checks["status"]
        if "postcheck" in checks:
            raise ReferenceLifecycleError("Batch already postchecked; retained certificate is immutable")
        checks["status"] = status
        checks["postcheck"] = {"checked_at": checked.isoformat(), "status": status,
            "evidence_path": evidence_rel, "evidence_sha256": sha256(raw),
            "results_sha256": sha256(records_raw), "dataset_sha256": sha256(dataset_raw), "cases": results}
        outcomes.append(status)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    if not evidence_path.exists():
        with evidence_path.open("xb") as stream:
            stream.write(raw)
    for filename, payload in documents.items():
        # Do not rewrite a legacy-only manifest alongside lifecycle batches.
        affected = ([payload] if filename == "manifest.json" else payload["batches"])
        if any("reference_checks" in batch for batch in affected):
            contained_path(root, filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "reference_changed" if "reference_changed" in outcomes else
            "verified" if set(outcomes) == {"verified"} else "reference_unverified",
            "batches": outcomes, "evidence_path": evidence_rel, "evidence_sha256": sha256(raw)}


def valid_scoring_revision(source) -> bool:
    """Return true only for a continuous receipt chain ending at current evidence."""
    if source is None:
        return False
    root = Path(source)
    receipt_path = root/'scoring-revisions.json'
    if not receipt_path.is_file() or not (root/'dataset.json').is_file() or not (root/'results.jsonl').is_file():
        return False
    try:
        rows = json.loads(receipt_path.read_text())
    except (OSError, ValueError, TypeError):
        return False
    current_dataset = sha256((root/'dataset.json').read_bytes())
    current_results = sha256((root/'results.jsonl').read_bytes())
    review_path = root/'result-review.json'
    review_hash = sha256(review_path.read_bytes()) if review_path.is_file() else None
    assembled_review_valid = False
    blind_review_hashes = set()
    review_candidates = ([review_path] if review_path.is_file() else [])
    review_history = root/'review-history'
    if review_history.is_dir() and not review_history.is_symlink():
        review_candidates.extend(path for path in review_history.glob('*.json')
                                 if path.is_file() and not path.is_symlink())
    dataset = None
    records = None
    try:
        dataset = json.loads((root/'dataset.json').read_text())
        records = load_jsonl(root/'results.jsonl')
    except (OSError, ValueError, TypeError):
        dataset = None
        records = None
    if dataset is not None and records is not None:
        from .result_review import require_blind_result_review
        for candidate in review_candidates:
            try:
                raw = candidate.read_bytes()
                review = json.loads(raw)
                require_blind_result_review(root, dataset, records, review)
                blind_review_hashes.add(sha256(raw))
            except (OSError, ValueError, TypeError, KeyError, StopIteration):
                continue
    if review_path.is_file():
        try:
            from .result_review import require_result_review
            if dataset is None or records is None:
                dataset = json.loads((root/'dataset.json').read_text())
                records = load_jsonl(root/'results.jsonl')
            assembled_review_valid = require_result_review(root, dataset, records) is not None
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            assembled_review_valid = False

    from .scoring_regressions import valid_correction_provenance

    def revision_edge(value):
        if not isinstance(value, dict):
            return None
        before = (value.get('input_dataset_sha256'), value.get('input_results_sha256'))
        after = (value.get('output_dataset_sha256'), value.get('output_results_sha256'))
        if not all(isinstance(item, str) and re.fullmatch(r'[0-9a-f]{64}', item)
                   for item in (*before, *after)):
            return None
        return before, after

    reference_edges = set()
    reference_path = root/'reference-revisions.json'
    if reference_path.is_file():
        try:
            reference_rows = json.loads(reference_path.read_text())
        except (OSError, ValueError, TypeError):
            return False
        for receipt in reference_rows if isinstance(reference_rows, list) else ():
            if (receipt.get('kind') == 'audited_reference_correction'
                    and receipt.get('provider_calls') == 0
                    and valid_correction_provenance(receipt)):
                transitions = receipt.get('revision_transitions')
                if isinstance(transitions, list):
                    reference_edges.update(filter(None, map(revision_edge, transitions)))
                else:
                    # Legacy rebinding changed the top-level output to the later
                    # bundle identity.  Its prior_output fields retain the true
                    # reference-correction endpoint.
                    legacy = dict(receipt)
                    if receipt.get('prior_output_dataset_sha256') is not None:
                        legacy['output_dataset_sha256'] = receipt['prior_output_dataset_sha256']
                        legacy['output_results_sha256'] = receipt['prior_output_results_sha256']
                    edge = revision_edge(legacy)
                    if edge is not None:
                        reference_edges.add(edge)

    def bridged(before, after):
        frontier = {before}
        seen = set()
        while frontier:
            current = frontier.pop()
            if current == after:
                return True
            if current in seen:
                continue
            seen.add(current)
            frontier.update(target for source, target in reference_edges if source == current)
        return False

    def valid_assembled_binding(row, index):
        source_hash = row.get('source_scoring_receipts_sha256')
        review_binding = row.get('independent_result_review_sha256')
        return (row.get('kind') == 'audited_assembled_scoring_revision'
                and isinstance(source_hash, str)
                and re.fullmatch(r'[0-9a-f]{64}', source_hash) is not None
                and source_hash == scoring_revision_history_sha256(rows[:index])
                and isinstance(review_binding, str)
                and re.fullmatch(r'[0-9a-f]{64}', review_binding) is not None
                and (index != len(rows) - 1
                     or ((assembled_review_valid and review_binding == review_hash)
                         or review_binding in blind_review_hashes)))

    if not isinstance(rows, list) or not rows:
        return False
    allowed_fields = {
        'body_requirements', 'required_patterns', 'required_facts',
        'acceptance_contract', 'forbidden_any', 'expected_resources',
        'resource_output_contract', 'task_prompt',
        'mutable_body_policy',
    }
    previous_output = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return False
        input_binding = (row.get('input_dataset_sha256'),
                         row.get('input_results_sha256'))
        output_binding = (row.get('output_dataset_sha256'),
                          row.get('output_results_sha256'))
        if (any(not isinstance(value, str)
                or re.fullmatch(r'[0-9a-f]{64}', value) is None
                for value in (*input_binding, *output_binding))
                or (previous_output is not None and input_binding != previous_output
                    and not bridged(previous_output, input_binding))):
            return False
        if (row.get('kind') not in {'audited_body_scoring_revision',
                                    'audited_scoring_rule_revision'}
                and not valid_assembled_binding(row, index)):
            return False
        if (row.get('provider_calls') != 0
                or not isinstance(row.get('task_decision_changes'), list)
                or not isinstance(row.get('changed_cases'), list)
                or not valid_correction_provenance(row)
                or not all(isinstance(change, dict)
                           and set(change.get('fields', ())) <= allowed_fields
                           for change in row['changed_cases'])):
            return False
        previous_output = output_binding
    return previous_output is not None and bridged(
        previous_output, (current_dataset, current_results)
    )


def latency_correction_original_elapsed(source, *, records=None) -> dict[tuple[str, str], float]:
    """Return original timings for a receipt bound to current corrected results.

    Reference certificates describe the content-producing call.  A later paired
    diagnostic may supersede only its reported latency, so certificate chronology
    must continue to use the original elapsed value retained in the correction
    receipt and record metadata.
    """
    if source is None:
        return {}
    root = Path(source)
    receipt_path = root / "latency-corrections.json"
    results_path = root / "results.jsonl"
    if not receipt_path.is_file():
        if records is None:
            records = (load_jsonl(results_path)
                       if results_path.is_file() else [])
        if any(row.get('provider_metadata', {}).get('latency_correction') for row in records):
            raise ReferenceLifecycleError("Invalid latency correction: missing bound receipt")
        return {}
    if not results_path.is_file():
        raise ReferenceLifecycleError("Latency correction receipt has no bound results")
    try:
        receipt = json.loads(receipt_path.read_text())
        records = load_jsonl(results_path)
    except (OSError, ValueError, TypeError) as error:
        raise ReferenceLifecycleError("Invalid latency correction receipt") from error
    if (not isinstance(receipt, dict)
            or receipt.get("schema_version") != 1
            or receipt.get("output_results_sha256") != sha256(results_path.read_bytes())
            or receipt.get("task_decision_changes") != 0
            or receipt.get("selection") not in {
                "same four cases for all three Moli versions; final round always selected, never best-of",
                "same four cases for Moli 1.1.1; projected from reviewed three-version final-round correction",
            }
            or not isinstance(receipt.get("changes"), list)
            or receipt.get("superseded_logical_results") != len(receipt["changes"])):
        raise ReferenceLifecycleError("Invalid latency correction receipt binding")
    by_key = {(row.get("provider"), row.get("case_id")): row for row in records}
    covered: dict[tuple[str, str], float] = {}
    for change in receipt["changes"]:
        if not isinstance(change, dict):
            raise ReferenceLifecycleError("Invalid latency correction entry")
        key = (change.get("provider"), change.get("case_id"))
        before, after = change.get("before_ms"), change.get("after_ms")
        record = by_key.get(key)
        correction = (record or {}).get("provider_metadata", {}).get("latency_correction", {})
        if (key in covered or not all(isinstance(value, (int, float))
                                      and not isinstance(value, bool)
                                      and math.isfinite(value) and value >= 0
                                      for value in (before, after))
                or record is None or record.get("elapsed_ms") != after
                or correction.get("kind") != "documented_final_round_supersession"
                or correction.get("original_elapsed_ms") != before
                or correction.get("corrected_elapsed_ms") != after
                or correction.get("diagnostic_results_sha256") != receipt.get("diagnostic_results_sha256")):
            raise ReferenceLifecycleError("Invalid latency correction entry")
        covered[key] = before
    expected = {key for key, row in by_key.items()
                if row.get('provider_metadata', {}).get('latency_correction')}
    if set(covered) != expected:
        raise ReferenceLifecycleError("Invalid latency correction coverage")
    return covered


def reference_revision_cases(source) -> dict[str, set[str]]:
    """Return original reference hashes covered by a bound offline correction.

    A reference correction never certifies a new browser call.  It only permits
    saved output bytes to be rescored against independently retained, corrected
    reference evidence.  The receipt is deliberately bound to the complete
    derived dataset/results pair so it cannot be copied to another cohort.
    """
    if source is None:
        return {}
    root = Path(source)
    path = root / "reference-revisions.json"
    if not path.is_file() or not (root / "dataset.json").is_file() or not (root / "results.jsonl").is_file():
        return {}
    try:
        receipts = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    current_dataset = sha256((root / "dataset.json").read_bytes())
    current_results = sha256((root / "results.jsonl").read_bytes())
    from .scoring_regressions import valid_correction_provenance
    if not isinstance(receipts, list):
        return {}

    def hash_pair(row, prefix):
        pair = (row.get(prefix + "_dataset_sha256"), row.get(prefix + "_results_sha256"))
        return pair if all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                           for value in pair) else None

    def receipt_transitions(receipt):
        transitions = receipt.get("revision_transitions")
        if isinstance(transitions, list):
            return [edge for row in transitions
                    if isinstance(row, dict)
                    for edge in [(hash_pair(row, "input"), hash_pair(row, "output"))]
                    if all(edge)]
        before = hash_pair(receipt, "input")
        if receipt.get("prior_output_dataset_sha256") is not None:
            after = (receipt.get("prior_output_dataset_sha256"),
                     receipt.get("prior_output_results_sha256"))
            if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in after):
                after = None
        else:
            after = hash_pair(receipt, "output")
        return [(before, after)] if before and after else []

    valid_references = []
    edges = set()
    for receipt in receipts:
        if (not isinstance(receipt, dict)
                or receipt.get("kind") != "audited_reference_correction"
                or receipt.get("provider_calls") != 0
                or not valid_correction_provenance(receipt)):
            continue
        valid_references.append(receipt)
        edges.update(receipt_transitions(receipt))

    scoring_path = root / "scoring-revisions.json"
    if scoring_path.is_file():
        try:
            scoring = json.loads(scoring_path.read_text())
        except (OSError, ValueError, TypeError):
            return {}
        allowed = {
            "body_requirements", "required_patterns", "required_facts",
            "acceptance_contract", "forbidden_any", "expected_resources",
            "resource_output_contract", "task_prompt", "mutable_body_policy",
        }
        for receipt in scoring if isinstance(scoring, list) else ():
            before, after = hash_pair(receipt, "input"), hash_pair(receipt, "output")
            if (isinstance(receipt, dict) and before and after
                    and receipt.get("provider_calls") == 0
                    and valid_correction_provenance(receipt)
                    and isinstance(receipt.get("changed_cases"), list)
                    and all(isinstance(change, dict)
                            and set(change.get("fields", ())) <= allowed
                            for change in receipt["changed_cases"])):
                edges.add((before, after))

    current = (current_dataset, current_results)

    def reaches_current(start):
        frontier, seen = {start}, set()
        while frontier:
            node = frontier.pop()
            if node == current:
                return True
            if node in seen:
                continue
            seen.add(node)
            frontier.update(after for before, after in edges if before == node)
        return False

    covered: dict[str, set[str]] = {}
    for receipt in valid_references:
        # Legacy rebound receipts use prior_output as their immutable endpoint;
        # consolidated assembly indexes may instead bind directly to current.
        terminals = {after for _, after in receipt_transitions(receipt)}
        top = hash_pair(receipt, "output")
        if receipt.get("prior_output_dataset_sha256") is None and top:
            terminals.add(top)
        if not any(reaches_current(terminal) for terminal in terminals):
            continue
        for change in receipt.get("changed_cases", ()):
            if (not isinstance(change, dict) or not isinstance(change.get("case_id"), str)
                    or not isinstance(change.get("old_reference_sha256"), str)
                    or not isinstance(change.get("new_reference_sha256"), str)
                    or set(change.get("fields", ())) - {
                        "ground_truth_main_content", "source_verification",
                        "body_requirements", "required_patterns", "reference_answer",
                        "ground_truth_evidence",
                    }):
                return {}
            covered.setdefault(change["case_id"], set()).add(change["old_reference_sha256"])
    return covered


def valid_reference_revision(source) -> bool:
    return bool(reference_revision_cases(source))


def check_case_reference_revision_binding(case, binding: dict, original_hashes: set[str]) -> None:
    """Validate immutable task scope for an explicitly audited reference fix."""
    original = _binding_evaluation_scope_identity(binding)
    current = evaluation_scope_identity(case)
    for scope in (original, current):
        scope.pop("evaluation_contract", None)
    # The frozen reference hash is expected to differ; URL, prompt and all
    # non-reference scope must remain identical.
    if (binding.get("reference_sha256") not in original_hashes
            or current != original):
        raise ReferenceLifecycleError(
            "Reference correction differs from the captured task scope", "reference_changed")


def check_case_reference_and_scoring_revision_binding(
    case, binding: dict, original_hashes: set[str]
) -> None:
    """Validate a cohort with independently receipted reference and scoring fixes."""
    original = _binding_evaluation_scope_identity(binding)
    current = evaluation_scope_identity(case)
    before_contract = original.pop("evaluation_contract", {})
    after_contract = current.pop("evaluation_contract", {})
    before_targets = before_contract.get("expected_resources", [])
    after_targets = after_contract.get("expected_resources", [])
    aliases_expand_only = (
        len(before_targets) == len(after_targets)
        and all(
            prior.get("id") == revised.get("id")
            and prior.get("kind") == revised.get("kind")
            and prior.get("url_patterns", []) == revised.get("url_patterns", [])
            and set(prior.get("urls", [])) <= set(revised.get("urls", []))
            for prior, revised in zip(before_targets, after_targets)
        )
    )
    before_policy = before_contract.get("resource_output_contract", {})
    after_policy = after_contract.get("resource_output_contract", {})
    if (
        current.get("task_kind") == original.get("task_kind") == "resource"
        and aliases_expand_only
        and before_policy.get("candidate_scope") == after_policy.get("candidate_scope")
            == "image_answer_fields"
        and before_policy.get("unexpected") == "reject"
        and after_policy.get("unexpected") == "allow"
    ):
        current.pop("task_prompt", None)
        original.pop("task_prompt", None)
    if binding.get("reference_sha256") not in original_hashes or current != original:
        raise ReferenceLifecycleError(
            f"{_get(case, 'id')}: combined reference/scoring correction differs from captured task scope",
            "reference_changed",
        )


def require_certifiable_evidence(dataset=None, manifests=()) -> None:
    """Diagnostic intent survives dataset copies and cannot become certification."""
    comparison = dataset.get('comparison', {}) if isinstance(dataset, dict) else {}
    candidates = [comparison, *manifests]
    if any(isinstance(value, dict) and (
            value.get('evidence_state') == 'diagnostic'
            or value.get('publication_state') == 'diagnostic'
            or value.get('unqualified_reference_cases')) for value in candidates):
        raise ReferenceLifecycleError('Diagnostic evidence cannot enter certified assembly or reports')


def require_verified_reference_checks(manifests, *, cases=None, records=None, source=None,
                                      equivalent_capture_bindings=False,
                                      allow_scoring_rule_revision=False) -> None:
    """Fail closed for explicit live lifecycles; leave historical batches readable.

    Call before report/append writes. The minimum call validates certificate
    structure, per-case success, capture/finish time bounds and evidence pointer.
    Optional cases (sequence or ID mapping) and records verify portable bindings
    without comparing whole merged dataset/results byte hashes. For batches that
    declare the current web-fetch manifest protocol (schema version 1), every
    measured fresh-reference attempt must be covered by exactly one batch
    certificate. Unversioned historical manifests remain readable. Optional
    source verifies retained observation bytes against their hash and certificate.
    This checks a completed historical certificate, NOT freshness as of today.
    """
    if isinstance(manifests, dict):
        manifests = [manifests]
    if not isinstance(manifests, (list, tuple)):
        raise ReferenceLifecycleError("Expected manifest batches")
    require_certifiable_evidence(cases, manifests)
    case_map = None
    if cases is not None:
        if isinstance(cases, dict) and "cases" in cases and isinstance(cases["cases"], list):
            cases = cases["cases"]
        values = list(cases.values()) if isinstance(cases, dict) else list(cases)
        case_map = {_get(case, "id"): case for case in values}
        if len(case_map) != len(values):
            raise ReferenceLifecycleError("Duplicate reference case IDs")
    if records is not None:
        records = list(records)

    def current_protocol(batch):
        return (isinstance(batch, dict)
                and batch.get("schema_version") == 1
                and batch.get("tool") == "web_fetch")

    fresh_attempt_coverage: dict[str, int] | None = None
    if case_map is not None and records is not None:
        current_protocol_attempts = {
            attempt_id
            for batch in manifests
            if current_protocol(batch)
            for attempt_id in (batch.get("attempt_ids")
                               if isinstance(batch.get("attempt_ids"), list) else ())
            if isinstance(attempt_id, str) and attempt_id
        }
        fresh_attempt_coverage = {}
        for record in records:
            # Reverse coverage is a certification invariant introduced by the
            # versioned runner protocol.  Do not reinterpret unversioned legacy
            # or synthetic evidence before its own aggregate checks run.
            if (not isinstance(record, dict)
                    or record.get("attempt_id") not in current_protocol_attempts):
                continue
            case_id = record.get("case_id")
            if case_id not in case_map:
                raise ReferenceLifecycleError("Measured case absent from dataset")
            if requires_fresh_reference(case_map[case_id]):
                attempt_id = record.get("attempt_id")
                if not isinstance(attempt_id, str) or not attempt_id:
                    raise ReferenceLifecycleError("Fresh-reference record has no attempt identity")
                if attempt_id in fresh_attempt_coverage:
                    raise ReferenceLifecycleError("Duplicate fresh-reference measured attempt")
                fresh_attempt_coverage[attempt_id] = 0

    def valid_hash(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    allow_scoring_rule_revision = (allow_scoring_rule_revision or valid_scoring_revision(source))
    revised_references = reference_revision_cases(source)
    original_latency = latency_correction_original_elapsed(source, records=records)
    try:
        for batch in manifests:
            if not isinstance(batch, dict):
                raise ReferenceLifecycleError("Invalid manifest")
            if "reference_checks" not in batch:
                continue
            checks = batch["reference_checks"]
            if not isinstance(checks, dict) or checks.get("status") != "verified":
                raise ReferenceLifecycleError("Live reference checks are not verified")
            bindings, post = checks.get("cases"), checks.get("postcheck")
            if (checks.get("schema_version") != 1
                    or checks.get("max_age_seconds") != MAX_REFERENCE_AGE_SECONDS
                    or not isinstance(bindings, dict) or not bindings
                    or not isinstance(post, dict) or post.get("status") != "verified"
                    or not isinstance(post.get("cases"), dict) or post["cases"].keys() != bindings.keys()):
                raise ReferenceLifecycleError("Missing or incomplete per-case reference certificate")
            for key in ("evidence_sha256", "dataset_sha256", "results_sha256"):
                if not valid_hash(post.get(key)):
                    raise ReferenceLifecycleError("Invalid reference certificate hash")
            if post.get("evidence_path") != f"reference-checks/observations-{post['evidence_sha256']}.json":
                raise ReferenceLifecycleError("Invalid reference certificate evidence pointer")
            preflight_at = timestamp(checks.get("preflight_checked_at"))
            finalized_at = timestamp(post.get("checked_at"))
            if finalized_at < preflight_at:
                raise ReferenceLifecycleError("Reference postcheck precedes preflight")
            selected = _selected_records(batch, records, original_latency) if records is not None else None
            if fresh_attempt_coverage is not None and current_protocol(batch):
                for record in selected or []:
                    attempt_id = record.get("attempt_id")
                    if (attempt_id in fresh_attempt_coverage
                            and record.get("case_id") in bindings):
                        fresh_attempt_coverage[attempt_id] += 1
            observations = None
            if source is not None:
                raw = contained_path(Path(source), post["evidence_path"]).read_bytes()
                if sha256(raw) != post["evidence_sha256"]:
                    raise ReferenceLifecycleError("Reference certificate evidence hash mismatch")
                payload = json.loads(raw)
                rows = payload.get("observations") if isinstance(payload, dict) else payload
                if not isinstance(rows, list):
                    raise ReferenceLifecycleError("Invalid certificate observations")
                observations = {}
                for row in rows:
                    if (not isinstance(row, dict) or not isinstance(row.get("case_id"), str)
                            or row["case_id"] in observations):
                        raise ReferenceLifecycleError("Invalid or duplicate certificate observation")
                    observations[row["case_id"]] = row
                if not bindings.keys() <= observations.keys():
                    raise ReferenceLifecycleError("Missing certificate observations")
            for identifier, binding in bindings.items():
                safe_identifier(identifier)
                result = post["cases"][identifier]
                if (not isinstance(result, dict) or result.get("status") != "verified"
                        or not valid_hash(binding.get("reference_sha256"))
                        or not valid_hash(binding.get("expected_normalized_sha256"))
                        or not valid_hash(result.get("content_sha256"))
                        or result.get("normalized_sha256") != binding["expected_normalized_sha256"]
                        or not isinstance(binding.get("scope_identity"), dict)
                        or binding["scope_identity"].get("case_id") != identifier
                        or not isinstance(binding["scope_identity"].get("evaluation_contract"), dict)):
                    raise ReferenceLifecycleError("Invalid or nonmatching case postcheck")
                _fresh(binding.get("checked_at"), preflight_at)
                observed_at = _not_future(result.get("checked_at"), finalized_at)
                finished_at = timestamp(result.get("latest_call_finished_at"))
                if not preflight_at <= finished_at < observed_at:
                    raise ReferenceLifecycleError("Invalid postcheck call/capture ordering")
                _fresh(finished_at, observed_at)
                if case_map is not None:
                    if identifier not in case_map:
                        raise ReferenceLifecycleError("Certificate case absent from dataset")
                    if (identifier in revised_references
                            and binding.get("reference_sha256") in revised_references[identifier]):
                        checker = (
                            check_case_reference_and_scoring_revision_binding
                            if allow_scoring_rule_revision
                            else check_case_reference_revision_binding
                        )
                        checker(case_map[identifier], binding, revised_references[identifier])
                    elif allow_scoring_rule_revision:
                        check_case_scoring_revision_binding(case_map[identifier], binding)
                    elif equivalent_capture_bindings:
                        check_case_equivalent_reference_binding(case_map[identifier], binding)
                    else:
                        check_case_reference_binding(case_map[identifier], binding)
                relevant = [r for r in selected if r["case_id"] == identifier] if selected is not None else None
                if relevant is not None:
                    if not relevant:
                        raise ReferenceLifecycleError("Certificate has no measured reference call")
                    latest = None
                    for record in relevant:
                        # This metric is part of the immutable physical call and
                        # must remain bound to the reference frozen for that
                        # call.  A later audited reference revision connects the
                        # old certificate to the current case; it must not make
                        # the historical record pretend it used new bytes.
                        expected_record_reference = binding["reference_sha256"]
                        if record.get("metrics", {}).get("reference_sha256") != expected_record_reference:
                            raise ReferenceLifecycleError("Record reference SHA differs from certificate")
                        began = timestamp(record["started_at"])
                        _fresh(binding["checked_at"], began)
                        certificate_elapsed = original_latency.get(
                            (record.get("provider"), record.get("case_id")), record["elapsed_ms"])
                        finish = began + timedelta(milliseconds=certificate_elapsed)
                        if record.get("completed_at") is not None:
                            finish = max(finish, timestamp(record["completed_at"]))
                        latest = finish if latest is None else max(latest, finish)
                    if ((not equivalent_capture_bindings and latest != finished_at)
                            or (equivalent_capture_bindings and latest > finished_at)):
                        raise ReferenceLifecycleError("Certificate does not follow the latest relevant call")
                if observations is not None:
                    observation = observations[identifier]
                    _completion(observation.get("completion"))
                    content = observation.get("content_text")
                    if (observation.get("independent") is not True or not isinstance(content, str) or not content.strip()
                            or observation.get("scope_identity") != binding["scope_identity"]
                            or observation.get("checked_at") != result["checked_at"]
                            or sha256(content) != result["content_sha256"]
                            # A reviewed scoring revision may itself correct
                            # visible-text normalization.  The certificate's
                            # exact captured-byte hash remains immutable and is
                            # stronger than re-running newer normalization code.
                            or (identifier not in revised_references
                                and not allow_scoring_rule_revision
                                and sha256(normalize_for_match(content)) != result["normalized_sha256"])
                            or ("content_sha256" in observation and observation["content_sha256"] != sha256(content))):
                        raise ReferenceLifecycleError(
                            f"Postcheck evidence differs from certificate: {identifier}")
    except ReferenceLifecycleError:
        raise
    except (OSError, KeyError, AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ReferenceLifecycleError(f"Invalid reference certificate: {error}") from error
    if fresh_attempt_coverage is not None:
        missing = [attempt for attempt, count in fresh_attempt_coverage.items() if count == 0]
        duplicate = [attempt for attempt, count in fresh_attempt_coverage.items() if count > 1]
        if missing or duplicate:
            detail = []
            if missing:
                detail.append("missing=" + ", ".join(sorted(missing)))
            if duplicate:
                detail.append("duplicate=" + ", ".join(sorted(duplicate)))
            raise ReferenceLifecycleError(
                "Fresh-reference measured attempts require exactly one certificate: "
                + "; ".join(detail))
