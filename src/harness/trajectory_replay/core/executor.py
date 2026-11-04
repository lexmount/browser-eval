"""Executor helpers shared by both Trajectory Replay tracks.

Each track keeps its own ``execute_steps``: their action vocabularies, timeout
budgets and target resolution differ, and those differences are action semantics.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def body_text(page: Any, timeout: int, limit: int) -> str:
    return page.locator("body").first.inner_text(timeout=timeout)[:limit]


def actionability_probe(page: Any, selector: str) -> dict[str, Any]:
    """Capture post-failure DOM/actionability evidence without affecting scoring."""
    return page.evaluate(
        """selector => {
          const element = document.querySelector(selector);
          if (!element) return {found: false};
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return {
            found: true,
            connected: element.isConnected,
            disabled: Boolean(element.disabled),
            ariaDisabled: element.getAttribute('aria-disabled'),
            display: style.display,
            visibility: style.visibility,
            opacity: style.opacity,
            pointerEvents: style.pointerEvents,
            rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
            clientRectCount: element.getClientRects().length,
            text: (element.textContent || '').trim().slice(0, 500)
          };
        }""",
        selector,
    )


def judge_outputs(
    outputs: dict[str, str], assertions: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for assertion in assertions:
        name = str(assertion.get("output") or "")
        actual = outputs.get(name, "")
        expected = assertion.get("equals")
        passed = isinstance(expected, str) and actual == expected
        checks.append(
            {
                "output": name,
                "kind": "exact string equality",
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    return checks


def validate_outputs(outputs: dict[str, str], assertions: Iterable[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for check in judge_outputs(outputs, assertions):
        if not check["passed"]:
            failures.append(
                f"{check['output']} exact mismatch: expected {check['expected']!r}, "
                f"got {check['actual']!r}"
            )
    return failures
