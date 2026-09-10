"""Deterministic execution of CowAgent BrowserTool-equivalent calls."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from typing import Any

from .suite import validate_selector
from .targets import locator_for


def _completed_action_navigation_timeout(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "click action done" in message
        and "waiting for scheduled navigations to finish" in message
    )


def _body_text(page: Any, timeout: int, limit: int) -> str:
    return page.locator("body").first.inner_text(timeout=timeout)[:limit]


def _actionability_probe(page: Any, selector: str) -> dict[str, Any]:
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


def _page_for_tab(page: Any, tab_index: int, timeout: int) -> Any:
    context = getattr(page, "context", None)
    pages = getattr(context, "pages", None)
    if pages is None:
        if tab_index == 0:
            return page
        raise TimeoutError(f"tab {tab_index} did not become available")
    deadline = time.monotonic() + timeout / 1000
    while len(context.pages) <= tab_index and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    if len(context.pages) <= tab_index:
        raise TimeoutError(f"tab {tab_index} did not become available")
    return context.pages[tab_index]


def execute_steps(
    page: Any,
    tool_calls: Iterable[dict[str, Any]],
    *,
    trace: list[dict[str, Any]] | None = None,
    outputs: dict[str, str] | None = None,
    page_observer: Callable[[Any], None] | None = None,
) -> dict[str, str]:
    """Execute declared calls; implicit readiness waits are internal and not calls."""
    captured = outputs if outputs is not None else {}
    records = trace if trace is not None else []
    for index, call in enumerate(tool_calls, start=1):
        action = call["action"]
        default_timeout = 30_000
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
            validate_selector(call)
            tab_index = int(call.get("tab_index", 0))
            action_page = _page_for_tab(page, tab_index, timeout)
            if page_observer is not None:
                page_observer(action_page)
            observation = ""
            if action == "navigate":
                response = action_page.goto(
                    call["url"],
                    wait_until=call.get("wait_until", "domcontentloaded"),
                    timeout=timeout,
                )
                limit = int(call.get("max_chars", 40_000))
                title_fn = getattr(action_page, "title", None)
                title = title_fn() if callable(title_fn) else ""
                status = getattr(response, "status", "") if response is not None else ""
                body = _body_text(action_page, timeout, limit)
                observation = (
                    f"Navigated to: {getattr(action_page, 'url', call['url'])}\n"
                    f"Title: {title}\nStatus: {status}\n\n--- Page Snapshot ---\n{body}"
                )
            elif action == "click":
                navigation = (
                    action_page.expect_navigation(
                        wait_until=call.get("wait_until", "domcontentloaded"), timeout=timeout
                    )
                    if call.get("wait_for_navigation")
                    else nullcontext()
                )
                with navigation:
                    try:
                        locator_for(action_page, call).click(
                            timeout=timeout,
                            no_wait_after=bool(call.get("no_wait_after", False)),
                        )
                    except Exception as exc:
                        if not (
                            call.get("accept_completed_action_navigation_timeout")
                            and _completed_action_navigation_timeout(exc)
                        ):
                            raise
                        record["accepted_navigation_timeout"] = {
                            "reason": "click completed before scheduled-navigation timeout",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                observation = "Clicked successfully."
            elif action == "double_click":
                locator_for(action_page, call).dblclick(
                    timeout=timeout,
                    no_wait_after=bool(call.get("no_wait_after", False)),
                )
                observation = "Double-clicked successfully."
            elif action == "fill":
                if (call.get("source") or {}).get("unresolved_external_input"):
                    raise ValueError("trajectory contains unresolved external input")
                locator_for(action_page, call).fill(str(call.get("text", "")), timeout=timeout)
                observation = "Filled text successfully."
            elif action == "select":
                navigation = (
                    action_page.expect_navigation(
                        wait_until=call.get("wait_until", "domcontentloaded"), timeout=timeout
                    )
                    if call.get("wait_for_navigation")
                    else nullcontext()
                )
                with navigation:
                    selected = call.get("value", "")
                    selected = (
                        [str(value) for value in selected]
                        if isinstance(selected, list)
                        else str(selected)
                    )
                    locator_for(action_page, call).select_option(
                        selected,
                        timeout=timeout,
                        no_wait_after=bool(call.get("no_wait_after", False)),
                    )
                observation = f"Selected option {call.get('value', '')}."
            elif action == "check":
                locator_for(action_page, call).check(timeout=timeout)
                observation = "Checked successfully."
            elif action == "uncheck":
                locator_for(action_page, call).uncheck(timeout=timeout)
                observation = "Unchecked successfully."
            elif action == "hover":
                locator_for(action_page, call).hover(timeout=timeout)
                observation = "Hovered successfully."
            elif action == "get_text":
                locator = locator_for(action_page, call)
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
                if call.get("strip"):
                    observation = observation.strip()
                observation = observation[: int(call.get("max_chars", 40_000))]
            elif action == "get_all_text":
                values = locator_for(action_page, call, all_matches=True).all_text_contents()
                if call.get("strip"):
                    values = [value.strip() for value in values]
                max_items = int(call.get("max_items", 1_000))
                max_chars = int(call.get("max_chars", 40_000))
                observation = json.dumps(values[:max_items], ensure_ascii=False)[:max_chars]
            elif action == "get_attribute":
                value = locator_for(action_page, call).get_attribute(
                    str(call["name"]), timeout=timeout
                )
                observation = "" if value is None else str(value)
            elif action == "get_all_attribute":
                values = locator_for(action_page, call, all_matches=True).evaluate_all(
                    "(elements, name) => elements.map(element => element.getAttribute(name))",
                    str(call["name"]),
                )
                max_items = int(call.get("max_items", 1_000))
                max_chars = int(call.get("max_chars", 40_000))
                observation = json.dumps(values[:max_items], ensure_ascii=False)[:max_chars]
            elif action == "get_property":
                value = locator_for(action_page, call).evaluate(
                    "(element, name) => element[name]", str(call["name"]), timeout=timeout
                )
                if isinstance(value, bool):
                    observation = "true" if value else "false"
                elif value is None:
                    observation = ""
                else:
                    observation = str(value)
            elif action == "snapshot":
                selector = str(call.get("selector") or "body")
                locator = action_page.locator(selector).first
                wait_pattern = call.get("wait_for_text_regex")
                if wait_pattern is not None:
                    if not isinstance(wait_pattern, str) or not wait_pattern:
                        raise ValueError("snapshot wait_for_text_regex must be non-empty")
                    wait_timeout = min(timeout, int(call.get("wait_timeout_ms", 10_000)))
                    deadline = time.monotonic() + wait_timeout / 1000
                    observation = ""
                    while time.monotonic() < deadline:
                        observation = locator.inner_text(timeout=min(1_000, wait_timeout))
                        if re.search(wait_pattern, observation, flags=re.IGNORECASE):
                            break
                        time.sleep(0.05)
                    else:
                        raise TimeoutError(
                            f"{selector} did not match text regex {wait_pattern!r}"
                        )
                else:
                    observation = locator.inner_text(timeout=timeout)
                observation = observation[: int(call.get("max_chars", 40_000))]
            elif action == "back":
                action_page.go_back(wait_until=call.get("wait_until", "commit"), timeout=timeout)
                observation = f"Navigated back to: {getattr(action_page, 'url', '')}"
            elif action == "forward":
                action_page.go_forward(wait_until=call.get("wait_until", "commit"), timeout=timeout)
                observation = f"Navigated forward to: {getattr(action_page, 'url', '')}"
            elif action == "scroll":
                direction = str(call.get("direction", "down"))
                amount = int(call.get("amount", 700))
                dx = amount if direction == "right" else -amount if direction == "left" else 0
                dy = amount if direction == "down" else -amount if direction == "up" else 0
                action_page.mouse.wheel(dx, dy)
                observation = f"Scrolled {direction} by {amount}px."
            elif action == "press":
                target = call.get("target") or call.get("selector")
                if target:
                    locator_for(action_page, call).press(
                        str(call["key"]),
                        timeout=timeout,
                        no_wait_after=bool(call.get("no_wait_after", False)),
                    )
                else:
                    action_page.keyboard.press(str(call["key"]))
                observation = f"Pressed key: {call['key']}"
            else:
                raise ValueError(f"unsupported action: {action}")

            save_as = call.get("save_as")
            if save_as:
                captured[str(save_as)] = observation
            record["status"] = "pass"
            record["output"] = observation
        except Exception as exc:
            record["status"] = "fail"
            record["error"] = f"{type(exc).__name__}: {exc}"
            selector = call.get("selector")
            if action in {"click", "fill", "select"} and isinstance(selector, str):
                try:
                    record["actionability_probe"] = _actionability_probe(action_page, selector)
                except Exception as probe_exc:
                    record["actionability_probe_error"] = f"{type(probe_exc).__name__}: {probe_exc}"
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return captured


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
