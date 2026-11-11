"""Select independently annotated content requirements before acquisition or scoring."""

from __future__ import annotations

import hashlib
import json
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
        substantive = any(
            requirements.get(k)
            for k in (
                "required_facts",
                "required_patterns",
                "ground_truth_main_content",
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
            or not evidence.get("scope")
            or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("source_sha256", "")))
        ):
            raise ValueError(f"{name} scope needs a source hash and reference scope")
    main = profiles["main"]["requirements"]
    full = profiles["full"]["requirements"]
    if main.get("forbidden_any", []) != full.get("forbidden_any", []):
        raise ValueError("Content scope cannot change blocked-page rejection rules")
    components = {
        "title_contract",
        "comment_contract",
        "post_contract",
        "comment_qualification_proof",
        "comment_presence_review",
        "post_qualification_proof",
    }
    benchmarks = [profile.get("source_benchmark", {}) for profile in (main, full)]
    identities = [{k: v for k, v in value.items() if k not in components} for value in benchmarks]
    if identities[0] != identities[1]:
        raise ValueError("Content scope cannot change source or navigation identity")
    effective = deepcopy(case)
    effective.update(deepcopy(REQUIREMENT_DEFAULTS))
    effective.update(deepcopy(profiles[selected]["requirements"]))
    effective["content_scope"] = selected
    binding = hashlib.sha256(
        json.dumps(
            profiles[selected], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return effective, binding
