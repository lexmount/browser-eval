"""Shared offline scoring orchestration; no provider calls or lifecycle decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .dynamic_lists import evaluate_dynamic_list
from .list_verification import verify_list
from .metrics import evaluate_content, evaluate_resources
from .models import EvalCase
from .safety import contained_path


# Bind orchestration as well as the underlying deterministic matchers. Computed
# once at import, shared by live/rescore output; never a reference-freshness claim.
SCORING_SOURCE_SHA256 = hashlib.sha256(b"\0".join(
    Path(__file__).with_name(name).read_bytes() for name in (
        "scoring.py", "metrics.py", "stable_tasks.py", "dynamic_lists.py",
        "list_verification.py", "reference_quality.py", "dynamic_entry_matching.py",
        "response_input.py", "body_coverage.py",
    )
)).hexdigest()


def _case_dict(case: EvalCase | dict[str, Any]) -> dict[str, Any]:
    return vars(case) if isinstance(case, EvalCase) else case


def load_list_reference(case: EvalCase | dict[str, Any], source: Path) -> dict | None:
    """Load only evidence required by the legacy list verifier, never by stable tasks."""
    case = _case_dict(case)
    benchmark = case.get("source_benchmark", {})
    if (not case.get("acceptance_contract")
            and benchmark.get("mode") == "dynamic_list_structure"
            and benchmark.get("verification_scope") == "first_three_name_detail_pairs"):
        return json.loads(contained_path(source, f'list-references/{case["id"]}.json').read_text())
    return None


# These conditional outputs must not survive a change of acceptance mode. All
# unconditional content/resource fields are overwritten below. Other evidence
# (e.g. raw_file_*, original reference binding and deadline) is retained, not
# re-certified by text scoring.
_CONDITIONAL_FIELDS = {
    "acceptance_contract_sha256", "content_decision_basis", "factual_correctness",
    "structure_pass", "named_entry_count", "minimum_entries", "snapshot_f1",
    "dynamic_titles", "verification_note", "confirmed_entries", "checked_entries",
}


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
    from .response_input import answer_input
    # Live callers forward the conversion completed inside the timed boundary.
    # Offline callers reconstruct it from immutable evidence by the same function.
    content, links, ok, status_basis = (prepared_answer if prepared_answer is not None else
        answer_input(artifact, metadata, ok=ok, status_code=status_code))
    result = {key: value for key, value in (previous_metrics or {}).items()
              if key not in _CONDITIONAL_FIELDS and not key.startswith(("stable_", "list_"))}
    keys = ("required_facts", "forbidden_any", "min_content_chars",
            "fact_coverage_threshold", "ground_truth_main_content", "required_patterns",
            "acceptance_contract", "body_requirements")
    result.update(evaluate_content(content, case.get("expected_any", []),
        base_url=case["url"], named_links=links, **{key: case[key] for key in keys if key in case}))
    result.update(request_ok=ok, request_status_basis=status_basis,
                  normalized_named_link_count=len(links))
    result["task_kind"] = case.get("task_kind", "body")
    file_evaluation = (metadata or {}).get('file_evaluation')
    if result['task_kind'] == 'file' and isinstance(file_evaluation, dict) and 'raw_file_pass' not in result:
        result['raw_file_pass'] = bool(file_evaluation.get('pass'))
    result["content_score_applicable"] = case.get("content_score_applicable", True)
    contract = case.get("acceptance_contract")
    if contract:
        result["acceptance_contract_sha256"] = hashlib.sha256(json.dumps(
            contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
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
    result.update(evaluate_resources(content, metadata or {}, case.get("expected_resources", []),
                                    base_url=case["url"], eligible=ok))
    benchmark = case.get("source_benchmark", {})
    if not contract and benchmark.get("mode") == "dynamic_list_structure":
        result.update(evaluate_dynamic_list(content, benchmark, case["url"], ok, links))
        if benchmark.get("verification_scope") == "first_three_name_detail_pairs":
            if list_reference is None:
                raise ValueError("Legacy list scoring requires independent list reference")
            result.update(verify_list(content, benchmark, case["url"], list_reference, ok, links))
    result["content_sha256"] = hashlib.sha256(artifact).hexdigest()
    result["scoring_source_sha256"] = SCORING_SOURCE_SHA256
    return result
