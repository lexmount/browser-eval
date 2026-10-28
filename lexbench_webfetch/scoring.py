"""Shared offline scoring orchestration; no provider calls or lifecycle decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .dynamic_lists import evaluate_dynamic_list
from .list_verification import verify_list
from .metrics import evaluate_content, evaluate_resources
from .models import EvalCase
from .paths import contained_path


# Bind orchestration as well as the underlying deterministic matchers. Computed
# once at import, shared by live/rescore output; never a reference-freshness claim.
SCORING_SOURCE_FILES = tuple(Path(__file__).with_name(name) for name in (
    "scoring.py", "metrics.py", "stable_tasks.py", "dynamic_lists.py",
    "list_verification.py", "reference_quality.py", "dynamic_entry_matching.py",
    "response_input.py", "body_coverage.py", "structured_tables.py",
    "ordered_tree.py", "composite_tasks.py", "dynamic_reference.py",
    "file_acceptance.py", "evaluation.py", "models.py", "paths.py", "article_posts.py",
    "article_comments.py", "article_title.py", "article_text.py", "required_components.py",
    "resource_urls.py", "document_structure.py",
))
SCORING_SOURCE_SHA256 = hashlib.sha256(b"\0".join(
    path.read_bytes() for path in SCORING_SOURCE_FILES)).hexdigest()


def _case_dict(case: EvalCase | dict[str, Any]) -> dict[str, Any]:
    return vars(case) if isinstance(case, EvalCase) else case


def load_list_reference(case: EvalCase | dict[str, Any], source: Path) -> dict | None:
    """Load declared independent list/post evidence, never infer it from an answer."""
    case = _case_dict(case)
    benchmark = case.get("source_benchmark", {})
    if (not case.get("acceptance_contract")
            and benchmark.get("mode") == "dynamic_list_structure"
            and benchmark.get("verification_scope") == "first_three_name_detail_pairs"):
        return json.loads(contained_path(source, f'list-references/{case["id"]}.json').read_text())
    return None


# Only collection evidence survives an offline score. Derived acceptance fields
# are recomputed, including fields no longer emitted by the current scorer.
# File fingerprints stay readable; a verdict needs current file verification.
_RETAINED_EVIDENCE_FIELDS = {
    "reference_sha256", "deadline_overrun", "deadline_budget_ms", "file_evidence",
    "raw_file_sha256",

}


def _same_page_url(actual: str, expected: str) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    try:
        actual_parts, expected_parts = urlsplit(actual), urlsplit(expected)
        actual_port = actual_parts.port or {"http": 80, "https": 443}.get(actual_parts.scheme)
        expected_port = expected_parts.port or {"http": 80, "https": 443}.get(expected_parts.scheme)
    except ValueError:
        return False
    actual_path = actual_parts.path.rstrip("/") or "/"
    expected_path = expected_parts.path.rstrip("/") or "/"
    if ((actual_parts.scheme, actual_parts.hostname, actual_port, actual_path)
            != (expected_parts.scheme, expected_parts.hostname, expected_port, expected_path)):
        return False
    # A declared query can select a different page on the same path (for
    # example, two distinct search results). Compare its decoded parameters;
    # a fragment does not change the fetched page.
    def page_query(parts):
        return sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                      if not key.casefold().startswith('utm_')
                      and key.casefold() not in {'gclid', 'fbclid'})
    return page_query(actual_parts) == page_query(expected_parts)


def score_attempt(
    case: EvalCase | dict[str, Any],
    artifact: bytes,
    *,
    ok: bool,
    metadata: dict[str, Any] | None = None,
    previous_metrics: dict[str, Any] | None = None,
    list_reference: dict | None = None,
    status_code: int | None = None,
    prepared_answer: tuple | None = None,
    delivered_files: list[bytes] | None = None,
    title: str | None = None,
    final_url: str | None = None,
) -> dict[str, Any]:
    """Score the exact persisted bytes identically for live and offline callers.

    Callers own integrity checks, reference lifecycle, clocks and persistence.
    API envelopes and explicit answer links use the same normalization for every
    caller. Original adapter status is retained by persistence, not overwritten
    in the report renderer.
    Previous non-text
    evidence is preserved; the caller must have validated its artifact binding.
    """
    case = _case_dict(case)
    if delivered_files is not None:
        from .file_acceptance import evaluate_files
        metadata = dict(metadata or {})
        metadata['file_evaluation'] = evaluate_files(case, list(delivered_files))
    if 'acceptance_policy_version' in case:
        raise ValueError('Historical policy selectors require their frozen scorer; current scoring has one contract')
    identity_required = case.get('page_identity_required', False)
    if not isinstance(identity_required, bool):
        raise ValueError('page_identity_required must be boolean')
    contract = case.get('acceptance_contract') or {}
    if contract.get('content_scope') == 'full_page_stable_coverage':
        if (contract.get('minimum_records', 0) == 0
                and not (case.get('body_requirements') or {}).get('regions')):
            raise ValueError('Full-page coverage requires target records or body regions')
    language_match = case.get("document_language_match", "primary")
    if language_match not in {"primary", "exact"}:
        raise ValueError("document_language_match must be primary or exact")
    from .response_input import answer_input, answer_format, answer_prose_input
    representation = answer_format(metadata)
    # Live callers forward the conversion completed inside the timed boundary.
    # Offline callers reconstruct it from immutable evidence by the same function.
    content, links, ok, status_basis = (prepared_answer if prepared_answer is not None else
        answer_input(artifact, metadata, ok=ok, status_code=status_code))
    result = {key: value for key, value in (previous_metrics or {}).items()
              if key in _RETAINED_EVIDENCE_FIELDS}

    keys = ("required_facts", "forbidden_any", "diagnostic_any", "min_content_chars",
            "fact_coverage_threshold", "ground_truth_main_content", "required_patterns",
            "acceptance_contract", "body_requirements", "composite_acceptance", "document_structure")
    task_kind = case.get("task_kind", "body")
    rule_review = case.get("rule_review") or {}
    scoring_values = {key: case[key] for key in keys if key in case}
    # An unresolved rule is still kept in the 100-case audit inventory, but it
    # is not an executable acceptance contract.  Score available diagnostics
    # without validating or applying that provisional contract, then mark the
    # result unavailable through the shared task predicate below.
    if rule_review.get("status") == "unresolved":
        scoring_values["acceptance_contract"] = {}
    # Relative links belong to the actual response document after redirects.
    # Do not substitute an expected redirect without observed URL evidence.
    base_url = case["url"]
    if isinstance(final_url, str):
        try:
            destination = urlsplit(final_url)
            if (destination.scheme in {"http", "https"} and destination.hostname
                    and not destination.username and not destination.password):
                base_url = final_url
        except ValueError:
            pass
    result.update(evaluate_content(
        answer_prose_input(content, representation), case.get("expected_any", []), title=title,
        base_url=base_url, named_links=links, raw_content=content,
        allow_reference_block_exemptions=(
            task_kind == "body" and not case.get("acceptance_contract")
        ),
        **scoring_values,
    ))
    if representation is not None:
        result["answer_format"] = representation
    result.update(request_ok=ok, request_status_basis=status_basis,
                  normalized_named_link_count=len(links))
    language_page_identity = bool(
        case.get("expected_document_language") or case.get("target_language")
        or case.get("expected_document_language_present") is False
    )
    risk_navigation = (
        ((case.get("source_benchmark") or {}).get("risk_control") or {}).get(
            "navigation_contract"
        )
    )
    risk_page_identity = bool(
        isinstance(risk_navigation, dict)
        and isinstance(risk_navigation.get("requested_url"), str)
        and isinstance(risk_navigation.get("observed_final_url"), str)
    )
    if language_page_identity:
        observed_language = (metadata or {}).get("document_language")
        language_present = case.get("expected_document_language_present", True)
        if not isinstance(language_present, bool):
            raise ValueError("expected_document_language_present must be boolean")
        if not language_present and case.get("expected_document_language"):
            raise ValueError("An absent source language cannot have an expected language tag")
        expected_document_language = (
            case.get("expected_document_language") or case.get("target_language")
        )
        final_consistent = bool(
            isinstance(final_url, str) and final_url
            and _same_page_url(final_url, case.get("expected_final_url") or case["url"])
        )
        language_consistent = (
            observed_language == "" if not language_present else bool(
                isinstance(observed_language, str) and observed_language.strip()
                and (observed_language.casefold() == expected_document_language.casefold()
                     if language_match == "exact" else
                     observed_language.split("-", 1)[0].casefold()
                     == expected_document_language.split("-", 1)[0].casefold())
            )
        )
        identity_status = (
            "verified" if final_consistent and language_consistent
            else "mismatch" if final_url and (observed_language or
                (not language_present and isinstance(observed_language, str)))
            else "unavailable"
        )
        result.update(
            page_identity_required=identity_required,
            page_identity_status=identity_status,
            final_url_consistent=final_consistent,
            language_consistent=language_consistent,
            observed_document_language=observed_language,
        )
    elif risk_page_identity:
        acceptable_urls = list(
            dict.fromkeys(
                [risk_navigation["requested_url"], risk_navigation["observed_final_url"]]
            )
        )
        final_consistent = bool(
            isinstance(final_url, str)
            and final_url
            and any(_same_page_url(final_url, expected) for expected in acceptable_urls)
        )
        result.update(
            page_identity_required=identity_required,
            page_identity_status=(
                "verified" if final_consistent else "mismatch" if final_url else "unavailable"
            ),
            final_url_consistent=final_consistent,
            acceptable_final_urls=acceptable_urls,
        )
    elif identity_required:
        consistent = bool(final_url and _same_page_url(
            final_url, case.get("expected_final_url") or case["url"]))
        result.update(page_identity_required=True, final_url_consistent=consistent,
                      page_identity_status="verified" if consistent else
                      "mismatch" if final_url else "unavailable")
    else:
        result.update(page_identity_required=False, page_identity_status="not_required")
    result["task_kind"] = task_kind
    result["rule_review_status"] = rule_review.get("status", "resolved")
    if rule_review.get("reason"):
        result["rule_review_reason"] = rule_review["reason"]
    # A truncation notice defeats sparse anchors, but it is not evidence that
    # reference text is missing when every declared body region has exact
    # recall. Extra provider prose does not reduce recall, so require 1.0 for
    # every region rather than the ordinary threshold/floor contract.
    declared_regions = (case.get("body_requirements") or {}).get("regions") or []
    measured_regions = result.get("body_region_results") or []
    exact_body_proven = bool(
        declared_regions
        and len(measured_regions) == len(declared_regions)
        and all(region.get("coverage") == 1.0 for region in measured_regions)
        and result.get("body_coverage_pass")
    )
    if ((result["task_kind"] == "body" or declared_regions)
            and result["declared_truncation_hits"]
            and not exact_body_proven):
        result["content_pass"] = False
        if contract := case.get("acceptance_contract"):
            result["stable_task_pass"] = False
            result["stable_content_pass"] = False
            if "declared_truncation" not in result["stable_task_errors"]:
                result["stable_task_errors"].append("declared_truncation")
    result['body_delivery_pass'] = bool(
        result['body_coverage_pass']
        and (not result['declared_truncation_hits'] or exact_body_proven))
    file_evaluation = (metadata or {}).get('file_evaluation')
    if result['task_kind'] == 'file' and isinstance(file_evaluation, dict) and ('raw_file_pass' not in result or delivered_files is not None):
        result['raw_file_pass'] = bool(file_evaluation.get('pass'))
    result["content_score_applicable"] = case.get("content_score_applicable", True)
    contract = scoring_values.get("acceptance_contract")
    if contract:
        result["acceptance_contract_sha256"] = hashlib.sha256(json.dumps(
            contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if composite := case.get("composite_acceptance"):
        result["composite_acceptance_sha256"] = hashlib.sha256(json.dumps(
            composite, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if body_requirements := case.get("body_requirements"):
        result["body_requirements_sha256"] = hashlib.sha256(json.dumps(
            body_requirements, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if not result["content_score_applicable"] or not ok:
        result["content_pass"] = False
    if not ok:
        if contract:
            result["stable_task_pass"] = False
        # Preserve the existing request-failure convention, but never zero text
        # overlap merely because a successful request failed task acceptance.
        if result["word_f1"] is not None:
            result.update(word_precision=0.0, word_recall=0.0, word_f1=0.0)
    checks = result.get("stable_anchor_results", []) if contract else result["fact_results"]
    total = len(checks)
    hits = sum(bool(check["pass"]) for check in checks) if ok else 0
    result.update(content_check_hits=hits, content_check_total=total,
                  content_check_coverage=round(hits / total, 4) if total else None)
    result.update(evaluate_resources(
        content, metadata or {}, case.get("expected_resources", []),
        base_url=base_url, eligible=ok,
        output_contract=case.get("resource_output_contract", {}),
    ))
    if case.get("expected_resources") or case.get("resource_output_contract"):
        resource_contract = {
            "expected_resources": case.get("expected_resources", []),
            "resource_output_contract": case.get("resource_output_contract", {}),
        }
        result["resource_acceptance_contract_sha256"] = hashlib.sha256(json.dumps(
            resource_contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    benchmark = case.get("source_benchmark", {})
    if rule_review.get("status") != "unresolved":
        from .required_components import COMPONENTS
        required = {}
        for field, component in COMPONENTS.items():
            if field not in benchmark:
                continue
            component_contract = benchmark[field]
            component_result = component.verify(content, component_contract, case["url"])
            result.update(component_result)
            required[component.name] = component_result[component.pass_metric] is True
            result[component.digest_metric] = hashlib.sha256(json.dumps(
                component_contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if required:
            result["content_decision_basis"] = "base_acceptance_and_required_components"
            result["required_components"] = required
            result["content_pass"] = bool(result["content_pass"] and all(required.values()))
    if (rule_review.get("status") != "unresolved" and not contract
            and benchmark.get("mode") == "dynamic_list_structure"):
        result.update(evaluate_dynamic_list(content, benchmark, base_url, ok, links))
        if benchmark.get("verification_scope") == "first_three_name_detail_pairs":
            if list_reference is None:
                raise ValueError("Legacy list scoring requires independent list reference")
            result.update(verify_list(content, benchmark, base_url, list_reference, ok, links))
    result["content_sha256"] = hashlib.sha256(artifact).hexdigest()
    if case.get('ordered_tree_reference') is not None:
        from .ordered_tree import tree_distance
        tree_result = tree_distance(json.loads(artifact), case['ordered_tree_reference'])
        result['ordered_tree'] = tree_result
        result['ordered_tree_similarity'] = tree_result['score']
        # A threshold is only an explicit task requirement, not a benchmark-wide
        # interpretation of continuous similarity as a pass rate.
        if 'ordered_tree_min_similarity' in case:
            result['ordered_tree_pass'] = bool(
                tree_result['score'] >= case['ordered_tree_min_similarity'])
            result['content_pass'] = bool(
                result['content_pass'] and ok and result['ordered_tree_pass']
                and not result['forbidden_requirement_hits'])
    result["acceptance_checks"] = acceptance_checks(case, result)
    result["scoring_source_sha256"] = SCORING_SOURCE_SHA256
    return result


def acceptance_checks(case: dict, metrics: dict) -> dict[str, bool | None]:
    """Select declared requirements, never a website- or provider-specific scorer.

    Missing evidence for a selected requirement remains unavailable. Undeclared
    requirements are omitted; similarity diagnostics never become pass gates.
    """
    kind = case.get('task_kind', 'body')
    checks = {}
    if case.get('acceptance_contract'):
        checks['records_and_anchors'] = metrics.get('stable_task_pass')
    elif (case.get('source_benchmark') or {}).get('mode') == 'dynamic_list_structure':
        checks['records'] = metrics.get('list_task_pass')
    elif kind == 'body' and case.get('content_score_applicable', True):
        checks['content'] = metrics.get('content_pass')
    if case.get('content_score_applicable', True):
        if case.get('expected_any'):
            checks['literals'] = metrics.get('legacy_marker_pass')
        checks['length'] = metrics.get('content_length_pass')
    if case.get('required_facts'):
        checks['facts'] = metrics.get('fact_coverage_pass')
    if case.get('required_patterns'):
        checks['patterns'] = metrics.get('pattern_pass')
    if case.get('body_requirements'):
        checks['body'] = metrics.get('body_delivery_pass')
    if case.get('composite_acceptance'):
        checks['composite'] = metrics.get('composite_acceptance_pass')
    if case.get('expected_resources') or case.get('resource_output_contract') or kind == 'resource':
        checks['resources'] = metrics.get('resource_pass')
    if kind == 'file':
        checks['file'] = metrics.get('raw_file_pass')
    if 'ordered_tree_min_similarity' in case:
        checks['tree'] = metrics.get('ordered_tree_pass')
    checks.update(metrics.get('required_components', {}))
    return checks
