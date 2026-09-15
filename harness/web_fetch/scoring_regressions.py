"""Executable synthetic controls for previously observed scoring failure modes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "evaluation/scoring-regressions.json"
FAILURE_MODES = {"false-positive", "false-negative"}
ROOT_CAUSE_CATEGORIES = {
    "acceptance-contract", "answer-surface", "body-scope",
    "format-normalization", "reference-evidence", "structured-extraction",
    "task-predicate", "multi-root",
}
REGRESSION_CATEGORIES = ROOT_CAUSE_CATEGORIES - {"multi-root"}


def load_scoring_regressions(path: Path = DEFAULT_REGISTRY) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("Unsupported scoring regression registry")
    rows: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(document["cases"], 1):
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in rows:
            raise ValueError(f"Scoring regression {index} requires a unique nonempty id")
        if row.get("failure_mode") not in FAILURE_MODES:
            raise ValueError(f"{identifier}: invalid failure_mode")
        if row.get("category") not in REGRESSION_CATEGORIES:
            raise ValueError(f"{identifier}: invalid root-cause category")
        if not isinstance(row.get("root_cause"), str) or not row["root_cause"].strip():
            raise ValueError(f"{identifier}: missing root_cause")
        if not isinstance(row.get("case"), dict) or not row["case"].get("url"):
            raise ValueError(f"{identifier}: missing synthetic case and URL")
        if not isinstance(row.get("artifact"), str):
            raise ValueError(f"{identifier}: artifact must be synthetic text")
        if not isinstance(row.get("ok"), bool) or not isinstance(row.get("expected_task_pass"), bool):
            raise ValueError(f"{identifier}: expected verdict fields must be booleans")
        if not isinstance(row.get("expected_metrics", {}), dict):
            raise ValueError(f"{identifier}: expected_metrics must be an object")
        rows[identifier] = row
    if {row["failure_mode"] for row in rows.values()} != FAILURE_MODES:
        raise ValueError("Scoring regression registry must guard both false positives and false negatives")
    return rows


def require_scoring_regressions(identifiers: list[str] | tuple[str, ...], *,
                                category: str | None = None) -> list[str]:
    unique = list(dict.fromkeys(identifiers))
    if not unique:
        raise ValueError("A scoring correction must cite at least one executable regression id")
    registry = load_scoring_regressions()
    missing = sorted(set(unique) - set(registry))
    if missing:
        raise ValueError("Unknown scoring regression ids: " + ", ".join(missing))
    mismatched = [identifier for identifier in unique
                  if category is not None and registry[identifier]["category"] != category]
    if mismatched:
        raise ValueError("Scoring regressions do not match root-cause category: "
                         + ", ".join(mismatched))
    return unique


def verdict_correction_counts(decisions: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, int]:
    decisions = list(decisions)
    if any(row.get("before") not in {True, False}
           or row.get("after") not in {True, False}
           or row.get("before") == row.get("after") for row in decisions):
        raise ValueError("Scoring revision decision changes must be boolean verdict flips")
    return {
        "false_positives": sum(row["before"] is True and row["after"] is False
                               for row in decisions),
        "false_negatives": sum(row["before"] is False and row["after"] is True
                               for row in decisions),
        "total": len(decisions),
    }


def build_correction_provenance(category: str, root_cause: str,
                                regression_ids: list[str] | tuple[str, ...],
                                decisions=()) -> dict[str, Any]:
    """Build a fail-closed cause/control binding for a derived-evidence correction."""
    if category not in ROOT_CAUSE_CATEGORIES:
        raise ValueError("Unsupported correction root-cause category")
    root_cause = (root_cause or "").strip()
    if len(root_cause) < 20:
        raise ValueError("Correction root cause must be concrete, not a short label")
    regression_ids = require_scoring_regressions(
        regression_ids, category=None if category == "multi-root" else category
    )
    registry = load_scoring_regressions()
    source_categories = sorted({registry[identifier]["category"] for identifier in regression_ids})
    if category == "multi-root" and len(source_categories) < 2:
        raise ValueError("A multi-root correction must cite regressions from multiple categories")
    result = {
        "category": category,
        "root_cause": root_cause,
        "regression_ids": regression_ids,
        "verdict_corrections": verdict_correction_counts(list(decisions)),
    }
    if category == "multi-root":
        result["root_cause_categories"] = source_categories
    return result


def valid_correction_provenance(receipt: dict[str, Any]) -> bool:
    """Validate schema-2 cause/regression bindings; legacy receipts stay readable."""
    schema = receipt.get("schema_version")
    if schema in {None, 1}:
        return True
    if schema != 2:
        return False
    provenance = receipt.get("correction_provenance")
    if not isinstance(provenance, dict):
        return False
    try:
        category = provenance.get("category")
        regression_ids = require_scoring_regressions(
            provenance.get("regression_ids", []),
            category=None if category == "multi-root" else category,
        )
        registry = load_scoring_regressions()
        source_categories = sorted({registry[identifier]["category"] for identifier in regression_ids})
        if category == "multi-root" and len(source_categories) < 2:
            return False
        expected = verdict_correction_counts(receipt.get("task_decision_changes", []))
    except (OSError, TypeError, ValueError):
        return False
    return (
        provenance.get("category") in ROOT_CAUSE_CATEGORIES
        and isinstance(provenance.get("root_cause"), str)
        and len(provenance["root_cause"].strip()) >= 20
        and provenance.get("regression_ids") == regression_ids
        and (category != "multi-root"
             or provenance.get("root_cause_categories") == source_categories)
        and provenance.get("verdict_corrections") == expected
    )
