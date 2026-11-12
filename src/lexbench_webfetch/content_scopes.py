"""Select independently annotated content requirements before acquisition or scoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy

SCOPES = ("main", "full")
# A scope replaces the complete acceptance surface. Keeping an old footer rule
# when selecting main content would silently continue testing the full page.
REQUIREMENT_DEFAULTS = {
    "task_prompt": "",
    "reference_answer": "",
    "expected_any": [],
    "required_facts": [],
    "forbidden_any": [],
    "required_patterns": [],
    "min_content_chars": 1,
    "fact_coverage_threshold": 1.0,
    "ground_truth_main_content": "",
    "body_requirements": {},
    "acceptance_contract": {},
    "composite_acceptance": {},
    "expected_resources": [],
    "resource_output_contract": {},
    "source_benchmark": {},
    "mutable_body_policy": {},
    "document_structure": {},
    "diagnostic_any": [],
}


def select_content_scope(case: dict, scope: str | None = None) -> tuple[dict, str | None]:
    """Fail closed for an unannotated scope; never infer it from a provider answer.

    Historical tasks remain executable unchanged when no scope is requested.
    An explicit main/full comparison requires both independently bound profiles.
    URL, task identity, language and runtime controls cannot be changed by a profile.
    """
    selected = scope or case.get("content_scope")
    profiles = case.get("content_scopes") or {}
    if selected is None and not profiles:
        return case, None
    if case.get('ordered_tree_reference') is not None or case.get('ordered_tree_min_similarity') is not None:
        raise ValueError('Content scopes require independent ordered-tree profiles; inherited full-page trees are unsupported')
    selected = selected or "full"
    if selected not in SCOPES:
        raise ValueError("content_scope must be main or full")
    if profiles.get("version") != 1 or set(profiles) != {"version", *SCOPES}:
        raise ValueError("Explicit content scope requires annotated main and full profiles")
    for name in SCOPES:
        profile = profiles[name]
        if not isinstance(profile, dict) or set(profile) != {"requirements", "evidence"}:
            raise ValueError(f"{name} scope needs requirements and independent evidence")
        requirements, evidence = profile["requirements"], profile["evidence"]
        if not isinstance(requirements, dict):
            raise ValueError(f"{name} scope requirements must be an object")
        threshold = requirements.get("fact_coverage_threshold", 1.0)
        if (requirements.get("required_facts") and (
                type(threshold) not in (int, float) or not math.isfinite(threshold)
                or not 0 < threshold <= 1)
                or any(type(rule.get("minimum", 1)) is not int or rule.get("minimum", 1) < 1
                       for rule in requirements.get("required_patterns", []))):
            raise ValueError(f"{name} scope must declare substantive acceptance requirements")
        substantive = any(
            requirements.get(k)
            for k in (
                "required_facts",
                "required_patterns",
                "body_requirements",
                "acceptance_contract",
                "composite_acceptance",
                "expected_resources",
                "document_structure",
            )
        ) or (
            case.get("task_kind") == "file"
            and (case.get("source_verification") or {}).get("file_sha256")
        )
        if (
            set(requirements) - set(REQUIREMENT_DEFAULTS)
            or not requirements.get("task_prompt")
            or not substantive
        ):
            raise ValueError(f"{name} scope must declare substantive acceptance requirements")
        if (
            not isinstance(evidence, dict)
            or ('reference_versions' in evidence and (
                not isinstance(evidence['reference_versions'],list)
                or any(not re.fullmatch(r'[0-9a-f]{64}',str(v)) for v in evidence['reference_versions'])))
            or not evidence.get("scope")
            or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("source_sha256", "")))
        ):
            raise ValueError(f"{name} scope needs a source hash and reference scope")
    main = profiles["main"]["requirements"]
    full = profiles["full"]["requirements"]
    forbidden = case.get("forbidden_any", [])
    if any(profile.get("forbidden_any", forbidden) != forbidden for profile in (main, full)):
        raise ValueError("Content scope cannot change blocked-page rejection rules")
    components = {
        "title_contract",
        "comment_contract",
        "post_contract",
        "comment_qualification_proof",
        "comment_presence_review",
        "post_qualification_proof",
    }
    original_benchmark = case.get("source_benchmark", {})
    original_identity = {k:v for k,v in original_benchmark.items() if k not in components}
    benchmarks = [profile.get("source_benchmark", original_identity) for profile in (main, full)]
    identities = [{k: v for k, v in value.items() if k not in components} for value in benchmarks]
    if any(identity != original_identity for identity in identities):
        raise ValueError("Content scope cannot change source or navigation identity")
    effective = deepcopy(case)
    effective.update(deepcopy(REQUIREMENT_DEFAULTS))
    effective.update(deepcopy(profiles[selected]["requirements"]))
    effective["forbidden_any"] = deepcopy(forbidden)
    effective["source_benchmark"] = deepcopy(profiles[selected]["requirements"].get("source_benchmark", original_identity))
    effective["content_scope"] = selected
    binding = hashlib.sha256(
        json.dumps(
            profiles[selected], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return effective, binding
