"""Trajectory Replay registries: participants, runtime adapters, labels and default model.

Participant identity is distinct from the browser runtime adapter.
"""

from __future__ import annotations

import re
from typing import Any

from harness.environments.browser import DEFAULT_BROWSER_IMAGES

# The model that explains failed traces; LLM output never judges success.
DEFAULT_DIAGNOSIS_MODEL = "gpt-5.6-sol"
# Runtime adapters the Playwright tool and the catalog runner can drive.
BROWSER_ADAPTERS = ("chromium", "moli", "lightpanda", "obscura", "kitesurf")
ENGINE_LABELS = {"chromium": "Chromium", "moli": "Moli"}
SAFE_ENGINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def engine_label(engine: str) -> str:
    return ENGINE_LABELS.get(engine, engine.replace("_", " ").strip().title())


def summary_engines(summary: dict[str, Any]) -> tuple[str, ...]:
    """Read the participant set and its order from one summary's own results."""
    functional = summary.get("functional") or {}
    resources = (summary.get("resource_comparison") or {}).get("by_engine") or {}
    engines = tuple(str(engine) for engine in functional or resources)
    if not all(SAFE_ENGINE.fullmatch(engine) for engine in engines):
        raise ValueError("summary engine IDs must be safe stable identifiers")
    return engines


def attempt_engines(
    manifest: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[str, ...]:
    """Return one saved run's participants; the first is the ratio baseline.

    A manifest without ``protocol.engines`` predates the field, so its row order
    says nothing: registered engines keep their registry order, then the others.
    """
    seen = tuple(dict.fromkeys(str(row["engine"]) for row in rows))
    protocol = manifest.get("protocol") or {}
    declared = tuple(str(engine) for engine in protocol.get("engines") or ())
    if declared:
        if len(set(declared)) != len(declared) or set(declared) != set(seen):
            raise ValueError(
                f"manifest protocol.engines {list(declared)} "
                f"differ from attempt engines {sorted(seen)}"
            )
        return declared
    registered = tuple(engine for engine in DEFAULT_BROWSER_IMAGES if engine in seen)
    return registered + tuple(engine for engine in seen if engine not in registered)


def participant_registry(methods: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    registry = {}
    for method in methods:
        identity = method.get("id")
        if (not isinstance(identity, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identity)
                or identity in registry or not method.get("engine")):
            raise ValueError("participant registry requires unique safe method ids and engines")
        registry[identity] = method
    if not registry:
        raise ValueError("participant registry is required")
    return registry


def resolve_participant_id(
    row: dict[str, Any], registry: dict[str, dict[str, Any]]
) -> str:
    identity = row.get("participant_id")
    if identity is not None:
        if identity not in registry or row.get("engine") != registry[identity]["engine"]:
            raise ValueError("participant identity is unknown or disagrees with its engine")
        return identity
    matches = [key for key, method in registry.items() if method["engine"] == row.get("engine")]
    if len(matches) != 1:
        raise ValueError("legacy engine identity is ambiguous or absent from the registry")
    return matches[0]


def bind_participant(
    row: dict[str, Any], registry: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Return a normalized copy; never rewrite retained evidence."""
    return {**row, "participant_id": resolve_participant_id(row, registry)}
