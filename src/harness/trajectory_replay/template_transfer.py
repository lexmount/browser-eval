"""Substitute frozen task parameters in replay candidate values."""

from __future__ import annotations

from typing import Any


def transfer_values(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, list):
        return [transfer_values(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: transfer_values(item, replacements) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    result = value
    for before, after in replacements:
        if before == result:
            result = after
        elif len(before) >= 3 and not before.isdigit():
            result = result.replace(before, after)
    return result
