"""Portable element targets shared by curated and imported browser trajectories."""

from __future__ import annotations

from typing import Any

TARGET_KINDS = frozenset({"css", "playwright", "role", "label", "text", "test_id"})


def validate_target(step: dict[str, Any]) -> None:
    """Validate an element target without binding the suite to one locator syntax."""
    selector = step.get("selector")
    target = step.get("target")
    if selector is not None and target is not None:
        raise ValueError("declare selector or target, not both")
    if selector is not None:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        return
    if not isinstance(target, dict):
        raise ValueError("requires selector or target")
    kind = str(target.get("by") or "")
    if kind not in TARGET_KINDS:
        raise ValueError(f"unsupported target kind {kind!r}")
    required = {
        "css": "value",
        "playwright": "value",
        "role": "role",
        "label": "value",
        "text": "value",
        "test_id": "value",
    }[kind]
    if not isinstance(target.get(required), str) or not str(target[required]).strip():
        raise ValueError(f"{kind} target requires non-empty {required}")
    if kind == "role" and "name" in target and not isinstance(target["name"], str):
        raise ValueError("role target name must be a string")


def locator_for(page: Any, step: dict[str, Any], *, all_matches: bool = False) -> Any:
    """Resolve the common target schema to a Playwright locator.

    Legacy selectors retain their old ``first`` behavior. Structured targets are
    strict by default so a changed page cannot silently redirect a reference path
    to another element.
    """
    validate_target(step)
    selector = step.get("selector")
    if selector is not None:
        locator = page.locator(selector)
        return locator if all_matches else locator.first

    target = step["target"]
    kind = target["by"]
    exact = bool(target.get("exact", True))
    if kind in {"css", "playwright"}:
        locator = page.locator(target["value"])
    elif kind == "role":
        kwargs: dict[str, Any] = {"exact": exact}
        if "name" in target:
            kwargs["name"] = target["name"]
        locator = page.get_by_role(target["role"], **kwargs)
    elif kind == "label":
        locator = page.get_by_label(target["value"], exact=exact)
    elif kind == "text":
        locator = page.get_by_text(target["value"], exact=exact)
    elif kind == "test_id":
        locator = page.get_by_test_id(target["value"])
    else:  # pragma: no cover - validation above keeps this unreachable
        raise ValueError(f"unsupported target kind {kind!r}")

    if all_matches:
        return locator
    if bool(step.get("allow_many", False)):
        return locator.first
    count = locator.count()
    if count == 0:
        first = locator.first
        wait_for = getattr(first, "wait_for", None)
        if callable(wait_for):
            wait_for(
                state="attached",
                timeout=int(step.get("target_timeout_ms", step.get("timeout_ms", 30_000))),
            )
            count = locator.count()
    if count != 1:
        raise ValueError(f"structured target must match exactly one element; matched {count}")
    return locator
