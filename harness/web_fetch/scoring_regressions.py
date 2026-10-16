"""Executable synthetic controls for previously observed scoring failure modes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.distribution import installed_data_path

ROOT = Path(__file__).resolve().parents[2]
FAILURE_MODES = {"false-positive", "false-negative"}
ROOT_CAUSE_CATEGORIES = {
    "acceptance-contract", "answer-surface", "body-scope",
    "format-normalization", "reference-evidence", "structured-extraction",
    "task-predicate", "multi-root",
}
REGRESSION_CATEGORIES = ROOT_CAUSE_CATEGORIES - {"multi-root"}


def default_registry_path() -> Path:
    """Resolve registry data only when a scoring revision needs it."""
    if (ROOT / "pyproject.toml").is_file():
        return ROOT / "evaluation/scoring-regressions.json"
    return installed_data_path("share/browser-harness-eval/scoring-regressions.json")


def load_scoring_regressions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = default_registry_path() if path is None else path
    document = json.loads(path.read_text())
    if document.get("schema_version") != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("Unsupported scoring regression registry")
    rows: dict[str, dict[str, Any]] = {}
    if document.get('control_revisions'):
        raise ValueError('Historical controls cannot override inputs or expected results; add new IDs')
    if document.get('historical_policy_versions'):
        raise ValueError('Historical policy selectors belong in frozen scorer snapshots')
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
        if "expected_task_measured" in row and not isinstance(row["expected_task_measured"], bool):
            raise ValueError(f"{identifier}: expected_task_measured must be a boolean")
        if not isinstance(row.get("expected_metrics", {}), dict):
            raise ValueError(f"{identifier}: expected_metrics must be an object")
        rows[identifier] = row
    if {row["failure_mode"] for row in rows.values()} != FAILURE_MODES:
        raise ValueError("Scoring regression registry must guard both false positives and false negatives")
    return rows


def active_scoring_regressions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Run current policy controls while preserving immutable historical inputs."""
    path = default_registry_path() if path is None else path
    rows = load_scoring_regressions(path)
    mapping = json.loads(path.read_text()).get("superseded_cases", {})
    if not isinstance(mapping, dict):
        raise ValueError("Scoring supersessions must map explicit control IDs")
    for old, new in mapping.items():
        if old not in rows or new not in rows or old == new or new in mapping:
            raise ValueError("Invalid scoring policy supersession")
        if any(rows[old].get(field) != rows[new].get(field) for field in
               ("case", "artifact", "ok", "metadata", "title", "final_url")):
            raise ValueError("Policy supersession must preserve synthetic inputs")
    import hashlib
    archived = json.loads(path.read_text()).get('superseded_controls', {}).get('controls', [])
    for item in archived:
        key = item['id']
        if key in rows:
            digest = hashlib.sha256(json.dumps(rows[key], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if digest != item['sha256']:
                raise ValueError('Archived scoring control identity changed: ' + key)
    excluded = {item['id'] for item in archived}
    return {key: row for key, row in rows.items() if key not in mapping and key not in excluded}


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
