"""Participant identity is distinct from the browser runtime adapter."""

from __future__ import annotations

import re
from typing import Any


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
