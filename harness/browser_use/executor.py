"""Deterministic execution of CowAgent BrowserTool-equivalent calls."""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any


def _body_text(page: Any, timeout: int, limit: int) -> str:
    return page.locator("body").first.inner_text(timeout=timeout)[:limit]


def execute_steps(
    page: Any,
    tool_calls: Iterable[dict[str, Any]],
    *,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Execute declared calls; implicit readiness waits are internal and not calls."""
    outputs: dict[str, str] = {}
    records = trace if trace is not None else []
    for index, call in enumerate(tool_calls, start=1):
        action = call["action"]
        default_timeout = (
            30_000
            if action == "navigate"
            else 10_000
            if action in {"back", "forward"}
            else 5_000
        )
        timeout = int(call.get("timeout_ms", default_timeout))
        started = time.perf_counter()
        record = {
            "index": index,
            "action": action,
            "arguments": {key: value for key, value in call.items() if key != "save_as"},
            "save_as": call.get("save_as"),
            "status": "running",
            "duration_ms": None,
            "output": "",
        }
        records.append(record)
        try:
            observation = ""
            if action == "navigate":
                response = page.goto(
                    call["url"],
                    wait_until=call.get("wait_until", "domcontentloaded"),
                    timeout=timeout,
                )
                limit = int(call.get("max_chars", 40_000))
                title_fn = getattr(page, "title", None)
                title = title_fn() if callable(title_fn) else ""
                status = getattr(response, "status", "") if response is not None else ""
                body = _body_text(page, timeout, limit)
                observation = (
                    f"Navigated to: {getattr(page, 'url', call['url'])}\n"
                    f"Title: {title}\nStatus: {status}\n\n--- Page Snapshot ---\n{body}"
                )
            elif action == "click":
                page.locator(call["selector"]).first.click(timeout=timeout)
                observation = "Clicked successfully."
            elif action == "fill":
                page.locator(call["selector"]).first.fill(
                    str(call.get("text", "")), timeout=timeout
                )
                observation = "Filled text successfully."
            elif action == "select":
                page.locator(call["selector"]).first.select_option(
                    str(call.get("value", "")), timeout=timeout
                )
                observation = f"Selected option {call.get('value', '')}."
            elif action == "get_text":
                locator = page.locator(call["selector"]).first
                expected_text = call.get("wait_for_text")
                if isinstance(expected_text, str):
                    wait_timeout = min(timeout, int(call.get("wait_timeout_ms", 5_000)))
                    deadline = time.monotonic() + wait_timeout / 1000
                    observation = ""
                    while time.monotonic() < deadline:
                        observation = locator.text_content(timeout=min(1_000, wait_timeout)) or ""
                        if observation.strip() == expected_text:
                            break
                        time.sleep(0.05)
                    else:
                        raise TimeoutError(
                            f"{call['selector']} did not become {expected_text!r}; "
                            f"last value was {observation!r}"
                        )
                else:
                    observation = locator.text_content(timeout=timeout) or ""
                observation = observation[: int(call.get("max_chars", 40_000))]
            elif action == "snapshot":
                selector = str(call.get("selector") or "body")
                observation = page.locator(selector).first.inner_text(timeout=timeout)
                observation = observation[: int(call.get("max_chars", 40_000))]
            elif action == "back":
                page.go_back(wait_until=call.get("wait_until", "commit"), timeout=timeout)
                observation = f"Navigated back to: {getattr(page, 'url', '')}"
            elif action == "forward":
                page.go_forward(wait_until=call.get("wait_until", "commit"), timeout=timeout)
                observation = f"Navigated forward to: {getattr(page, 'url', '')}"
            elif action == "scroll":
                direction = str(call.get("direction", "down"))
                amount = int(call.get("amount", 700))
                dx = amount if direction == "right" else -amount if direction == "left" else 0
                dy = amount if direction == "down" else -amount if direction == "up" else 0
                page.mouse.wheel(dx, dy)
                observation = f"Scrolled {direction} by {amount}px."
            elif action == "press":
                page.keyboard.press(str(call["key"]))
                observation = f"Pressed key: {call['key']}"
            else:
                raise ValueError(f"unsupported action: {action}")

            save_as = call.get("save_as")
            if save_as:
                outputs[str(save_as)] = observation
            record["status"] = "pass"
            record["output"] = observation
        except Exception as exc:
            record["status"] = "fail"
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return outputs


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
