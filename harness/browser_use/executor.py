"""Deterministic Playwright task execution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def execute_steps(page: Any, steps: Iterable[dict[str, Any]]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for step in steps:
        action = step["action"]
        timeout = int(step.get("timeout_ms", 30_000))
        if action == "navigate":
            page.goto(
                step["url"],
                wait_until=step.get("wait_until", "domcontentloaded"),
                timeout=timeout,
            )
        elif action == "click":
            page.locator(step["selector"]).first.click(timeout=timeout)
        elif action == "fill":
            page.locator(step["selector"]).first.fill(str(step.get("value", "")), timeout=timeout)
        elif action == "press":
            page.keyboard.press(str(step["key"]))
        elif action == "wait":
            page.locator(step["selector"]).first.wait_for(
                state=step.get("state", "visible"), timeout=timeout
            )
        elif action == "extract":
            text = page.locator(step["selector"]).first.inner_text(timeout=timeout)
            limit = int(step.get("max_chars", 40_000))
            outputs[str(step["save_as"])] = text[:limit]
        elif action == "snapshot":
            outputs[str(step.get("save_as", "snapshot"))] = page.locator("body").first.inner_text(
                timeout=timeout
            )[: int(step.get("max_chars", 40_000))]
        elif action == "back":
            page.go_back(wait_until=step.get("wait_until", "commit"), timeout=timeout)
        elif action == "forward":
            page.go_forward(wait_until=step.get("wait_until", "commit"), timeout=timeout)
        elif action == "scroll":
            page.mouse.wheel(0, int(step.get("pixels", 700)))
        else:
            raise ValueError(f"unsupported action: {action}")
    return outputs


def validate_outputs(outputs: dict[str, str], assertions: Iterable[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for assertion in assertions:
        name = str(assertion.get("output") or "")
        value = outputs.get(name, "")
        expected = assertion.get("contains")
        if isinstance(expected, str) and expected.casefold() not in value.casefold():
            failures.append(f"{name} does not contain {expected!r}")
        minimum = assertion.get("min_chars")
        if isinstance(minimum, int) and len(value) < minimum:
            failures.append(f"{name} has {len(value)} chars, expected at least {minimum}")
    return failures
