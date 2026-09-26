"""Deterministic execution of CowAgent BrowserTool-equivalent calls."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from typing import Any
from urllib.parse import urlsplit

from .suite import validate_selector
from .targets import locator_for

TABLE_COLUMN_EXPRESSION = r"""(table, options) => {
  const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
  const wanted = normalize(options.header).toLowerCase();
  const headers = Array.from(table.querySelectorAll('thead th'));
  const matches = headers
    .map((element, index) => ({index, text: normalize(element.innerText)}))
    .filter(item => item.text.toLowerCase() === wanted);
  if (matches.length !== 1) {
    throw new Error(`table header ${options.header} matched ${matches.length} columns`);
  }
  const index = matches[0].index;
  return Array.from(table.querySelectorAll('tbody tr'))
    .slice(0, options.maxItems)
    .map(row => {
      const cells = row.querySelectorAll('th, td');
      if (index >= cells.length) {
        throw new Error('table row does not contain the requested column');
      }
      return normalize(cells[index].innerText);
    });
}"""

NAVIGATION_NETWORK_IDLE_TIMEOUT_MS = 1_500
NAVIGATION_RENDER_GRACE_MS = 300
TEXT_OBSERVATION_MAX_CHARS = 40_000
# BrowserTool returns complete JSON evaluate results.  Keep that retrieval
# contract while placing a finite ceiling on one captured observation.
STRUCTURED_OBSERVATION_MAX_BYTES = 4 * 1024 * 1024


def _same_document_url(left: str, right: str) -> bool:
    first = urlsplit(left)
    second = urlsplit(right)
    return (
        first.scheme,
        first.netloc,
        first.path or "/",
        first.query,
    ) == (
        second.scheme,
        second.netloc,
        second.path or "/",
        second.query,
    )


def _completed_action_navigation_timeout(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "click action done" in message and "waiting for scheduled navigations to finish" in message
    )


def _body_text(page: Any, timeout: int, limit: int) -> str:
    return page.locator("body").first.inner_text(timeout=timeout)[:limit]


def _evaluate_script(page: Any, script: str, script_mode: Any) -> Any:
    if script_mode is None:
        return page.evaluate(script)
    if script_mode == "function_body":
        return page.evaluate(f"(async () => {{\n{script}\n}})()")
    raise ValueError("evaluate script_mode must be function_body when provided")


def _bounded_evaluate_observation(value: Any, requested_max_chars: Any = None) -> str:
    """Serialize evaluate observations without producing partial JSON."""

    structured = not isinstance(value, str)
    if structured:
        observation = json.dumps(value, ensure_ascii=False, allow_nan=False)
    else:
        observation = value
        try:
            json.loads(observation)
        except (TypeError, ValueError):
            pass
        else:
            structured = True
    limit = TEXT_OBSERVATION_MAX_CHARS if requested_max_chars is None else int(requested_max_chars)
    if limit < 1:
        raise ValueError("observation max_chars must be positive")
    if structured:
        if requested_max_chars is not None and len(observation) > limit:
            raise ValueError(f"structured observation exceeds the {limit}-character evidence limit")
        byte_length = len(observation.encode("utf-8"))
        if byte_length > STRUCTURED_OBSERVATION_MAX_BYTES:
            raise ValueError("structured observation exceeds the 4194304-byte evidence limit")
        return observation
    return observation[:limit]


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


def _prepare_geometry_action(
    page: Any,
    call: dict[str, Any],
    action: str,
    publish_layout: Callable[[], None],
    remaining: Callable[[], int],
    enabled: bool,
) -> None:
    """Wait for one actionable layout, then let Playwright dispatch exactly once."""

    if not enabled:
        return
    require_enabled = action not in {"hover"}
    while True:
        publish_layout()
        probe_call = {**call, "timeout_ms": remaining(), "target_timeout_ms": remaining()}
        try:
            locator = locator_for(page, probe_call, wait_for_target=False)
        except ValueError as exc:
            if "matched 0" not in str(exc):
                raise
        else:
            if action == "press":
                budget = remaining()
                call["timeout_ms"] = budget
                call["target_timeout_ms"] = min(
                    int(call.get("target_timeout_ms", budget)), budget
                )
                return
            ready = locator.is_visible() and (not require_enabled or locator.is_enabled())
            if ready and action == "fill":
                ready = locator.is_editable()
            if ready:
                if action in {"fill", "select"}:
                    budget = remaining()
                    call["timeout_ms"] = budget
                    call["target_timeout_ms"] = min(
                        int(call.get("target_timeout_ms", budget)), budget
                    )
                    return
                try:
                    trial_timeout = min(500, remaining())
                    if action == "hover":
                        locator.hover(timeout=trial_timeout, trial=True)
                    else:
                        locator.click(timeout=trial_timeout, trial=True)
                except Exception as exc:
                    if "timeout" not in str(exc).lower():
                        raise
                else:
                    budget = remaining()
                    call["timeout_ms"] = budget
                    call["target_timeout_ms"] = min(
                        int(call.get("target_timeout_ms", budget)), budget
                    )
                    return
        page.wait_for_timeout(min(100, remaining()))


def execute_steps(
    page: Any,
    tool_calls: Iterable[dict[str, Any]],
    *,
    trace: list[dict[str, Any]] | None = None,
    outputs: dict[str, str] | None = None,
    page_observer: Callable[[Any], None] | None = None,
    layout_observer: Callable[[Any, int], None] | None = None,
    navigation_response_observer: Callable[[Any], None] | None = None,
    task_timeout_ms: int | None = None,
    default_action_timeout_ms: int = 30_000,
) -> dict[str, str]:
    """Execute declared calls; implicit readiness waits are internal and not calls."""
    if task_timeout_ms is not None and task_timeout_ms < 1:
        raise ValueError("task_timeout_ms must be positive when provided")
    if default_action_timeout_ms < 1:
        raise ValueError("default_action_timeout_ms must be positive")
    captured = outputs if outputs is not None else {}
    records = trace if trace is not None else []
    deadline = time.monotonic() + task_timeout_ms / 1000 if task_timeout_ms is not None else None
    for index, call in enumerate(tool_calls, start=1):
        action = call["action"]
        declared_timeout = int(call.get("timeout_ms", default_action_timeout_ms))
        if deadline is None:
            timeout = declared_timeout
        else:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms < 1:
                raise TimeoutError(
                    f"task wall-clock budget exhausted before action {index}: {action}"
                )
            timeout = min(declared_timeout, remaining_ms)
        started = time.perf_counter()
        action_deadline = started + timeout / 1_000

        def action_remaining(
            deadline: float = action_deadline,
            action_name: str = action,
        ) -> int:
            remaining = int((deadline - time.perf_counter()) * 1_000)
            if remaining < 1:
                raise TimeoutError(f"{action_name} action budget exhausted")
            return remaining

        def operation_timeout(original_timeout: int = timeout) -> int:
            return action_remaining() if layout_observer is not None else original_timeout

        locator_call = {**call, "timeout_ms": timeout}
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
            if action == "navigate" and call.get("new_page"):
                context = getattr(page, "context", None)
                if context is None or not callable(getattr(context, "new_page", None)):
                    raise RuntimeError("navigate new_page requires a browser context")
                action_page = context.new_page()
            else:
                tab_index = int(call.get("tab_index", 0))
                action_page = _page_for_tab(
                    page,
                    tab_index,
                    action_remaining() if layout_observer is not None else timeout,
                )
            if page_observer is not None:
                page_observer(action_page)

            def publish_layout(current_page: Any = action_page) -> None:
                if layout_observer is not None:
                    layout_observer(current_page, action_remaining())

            hidden_selector = call.get("wait_for_selector_hidden")
            if hidden_selector is not None:
                if not isinstance(hidden_selector, str) or not hidden_selector:
                    raise ValueError("wait_for_selector_hidden must be a non-empty selector")
                if layout_observer is None:
                    action_page.locator(hidden_selector).wait_for(state="hidden", timeout=timeout)
                else:
                    while True:
                        publish_layout()
                        try:
                            action_page.locator(hidden_selector).wait_for(
                                state="hidden", timeout=min(action_remaining(), 500)
                            )
                            break
                        except Exception as exc:
                            if "timeout" not in str(exc).lower():
                                raise
                            action_remaining()
            observation = ""
            if action == "navigate":
                prior_url = str(getattr(action_page, "url", ""))
                response = action_page.goto(
                    call["url"],
                    wait_until=call.get("wait_until", "domcontentloaded"),
                    timeout=action_remaining(),
                )
                if (
                    response is None
                    and prior_url
                    and _same_document_url(prior_url, str(call["url"]))
                ):
                    response = action_page.reload(
                        wait_until=call.get("wait_until", "domcontentloaded"),
                        timeout=action_remaining(),
                    )
                if navigation_response_observer is not None:
                    navigation_response_observer(response)
                # BrowserTool returns after DOMContentLoaded, then gives ordinary
                # background requests a short chance to settle.  Network idle is
                # deliberately best effort: analytics or streaming requests must
                # not turn a completed navigation into a failed replay action.
                try:
                    action_page.wait_for_load_state(
                        "networkidle",
                        timeout=min(action_remaining(), NAVIGATION_NETWORK_IDLE_TIMEOUT_MS),
                    )
                except Exception:
                    pass
                try:
                    action_page.wait_for_timeout(
                        min(action_remaining(), NAVIGATION_RENDER_GRACE_MS)
                    )
                except TimeoutError:
                    pass
                publish_layout()
                limit = int(call.get("max_chars", 40_000))
                title = str(
                    action_page.locator("html").first.evaluate(
                        "element => element.ownerDocument.title",
                        None,
                        timeout=action_remaining(),
                    )
                    or ""
                )
                status = getattr(response, "status", "") if response is not None else ""
                body = _body_text(action_page, action_remaining(), limit)
                observation = (
                    f"Navigated to: {getattr(action_page, 'url', call['url'])}\n"
                    f"Title: {title}\nStatus: {status}\n\n--- Page Snapshot ---\n{body}"
                )
            elif action == "click":
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                navigation = (
                    action_page.expect_navigation(
                        wait_until=call.get("wait_until", "domcontentloaded"),
                        timeout=operation_timeout(),
                    )
                    if call.get("wait_for_navigation")
                    else nullcontext()
                )
                with navigation:
                    try:
                        locator_for(action_page, locator_call).click(
                            timeout=operation_timeout(),
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
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                locator_for(action_page, locator_call).dblclick(
                    timeout=operation_timeout(),
                    no_wait_after=bool(call.get("no_wait_after", False)),
                )
                observation = "Double-clicked successfully."
            elif action == "fill":
                if (call.get("source") or {}).get("unresolved_external_input"):
                    raise ValueError("trajectory contains unresolved external input")
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                locator_for(action_page, locator_call).fill(
                    str(call.get("text", "")), timeout=operation_timeout()
                )
                observation = "Filled text successfully."
            elif action == "select":
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                navigation = (
                    action_page.expect_navigation(
                        wait_until=call.get("wait_until", "domcontentloaded"),
                        timeout=operation_timeout(),
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
                    locator_for(action_page, locator_call).select_option(
                        selected,
                        timeout=operation_timeout(),
                        no_wait_after=bool(call.get("no_wait_after", False)),
                    )
                observation = f"Selected option {call.get('value', '')}."
            elif action == "check":
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                locator_for(action_page, locator_call).check(timeout=operation_timeout())
                observation = "Checked successfully."
            elif action == "uncheck":
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                locator_for(action_page, locator_call).uncheck(timeout=operation_timeout())
                observation = "Unchecked successfully."
            elif action == "hover":
                _prepare_geometry_action(
                    action_page,
                    locator_call,
                    action,
                    publish_layout,
                    action_remaining,
                    layout_observer is not None,
                )
                locator_for(action_page, locator_call).hover(timeout=operation_timeout())
                observation = "Hovered successfully."
            elif action == "get_text":
                locator = locator_for(action_page, locator_call)
                expected_text = call.get("wait_for_text")
                if isinstance(expected_text, str):
                    wait_timeout = min(timeout, int(call.get("wait_timeout_ms", 5_000)))
                    action_deadline = time.monotonic() + wait_timeout / 1000
                    observation = ""
                    while time.monotonic() < action_deadline:
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
                values = locator_for(
                    action_page, locator_call, all_matches=True
                ).all_text_contents()
                if call.get("strip"):
                    values = [value.strip() for value in values]
                max_items = int(call.get("max_items", 1_000))
                max_chars = int(call.get("max_chars", 40_000))
                observation = json.dumps(values[:max_items], ensure_ascii=False)[:max_chars]
            elif action == "get_table_column":
                locator = locator_for(action_page, locator_call)
                options = {
                    "header": str(call["header"]),
                    "maxItems": int(call.get("max_items", 1_000)),
                }
                wait_pattern = call.get("wait_for_value_regex")
                wait_timeout = (
                    timeout
                    if wait_pattern is None
                    else min(timeout, int(call.get("wait_timeout_ms", 10_000)))
                )
                action_deadline = time.monotonic() + wait_timeout / 1000
                while True:
                    publish_layout()
                    evaluate_timeout = min(timeout, wait_timeout)
                    if layout_observer is not None:
                        loop_remaining = max(1, int((action_deadline - time.monotonic()) * 1_000))
                        evaluate_timeout = min(action_remaining(), loop_remaining)
                    values = locator.evaluate(
                        TABLE_COLUMN_EXPRESSION,
                        options,
                        timeout=evaluate_timeout,
                    )
                    if wait_pattern is None or any(
                        re.search(str(wait_pattern), str(value), flags=re.IGNORECASE)
                        for value in values
                    ):
                        break
                    if time.monotonic() >= action_deadline:
                        raise TimeoutError(
                            f"table column {call['header']!r} did not match value regex "
                            f"{wait_pattern!r}"
                        )
                    time.sleep(0.05)
                observation = json.dumps(values, ensure_ascii=False)[
                    : int(call.get("max_chars", 40_000))
                ]
            elif action == "get_attribute":
                value = locator_for(action_page, locator_call).get_attribute(
                    str(call["name"]), timeout=timeout
                )
                observation = "" if value is None else str(value)
            elif action == "get_all_attribute":
                values = locator_for(action_page, locator_call, all_matches=True).evaluate_all(
                    "(elements, name) => elements.map(element => element.getAttribute(name))",
                    str(call["name"]),
                )
                max_items = int(call.get("max_items", 1_000))
                max_chars = int(call.get("max_chars", 40_000))
                observation = json.dumps(values[:max_items], ensure_ascii=False)[:max_chars]
            elif action == "get_property":
                value = locator_for(action_page, locator_call).evaluate(
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
                    action_deadline = time.monotonic() + wait_timeout / 1000
                    observation = ""
                    while time.monotonic() < action_deadline:
                        publish_layout()
                        inner_timeout = min(1_000, wait_timeout)
                        if layout_observer is not None:
                            loop_remaining = max(
                                1, int((action_deadline - time.monotonic()) * 1_000)
                            )
                            inner_timeout = min(inner_timeout, action_remaining(), loop_remaining)
                        observation = locator.inner_text(timeout=inner_timeout)
                        if re.search(wait_pattern, observation, flags=re.IGNORECASE):
                            break
                        time.sleep(0.05)
                    else:
                        raise TimeoutError(f"{selector} did not match text regex {wait_pattern!r}")
                else:
                    publish_layout()
                    observation = locator.inner_text(timeout=operation_timeout())
                observation = observation[: int(call.get("max_chars", 40_000))]
            elif action == "evaluate":
                script = call.get("script")
                if not isinstance(script, str) or not script.strip():
                    raise ValueError("evaluate requires a non-empty script")
                try:
                    value = _evaluate_script(action_page, script, call.get("script_mode"))
                except Exception as exc:
                    pattern = call.get("accept_navigation_url_regex")
                    if not (
                        isinstance(pattern, str)
                        and pattern
                        and "Execution context was destroyed" in str(exc)
                    ):
                        raise
                    action_page.wait_for_url(
                        re.compile(pattern), wait_until="domcontentloaded", timeout=timeout
                    )
                    if not re.search(pattern, str(action_page.url)):
                        raise ValueError("evaluate navigation ended at the wrong URL") from exc
                    record["accepted_navigation"] = {
                        "url": str(action_page.url),
                        "expected_url_regex": pattern,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    observation = f"Navigated to: {action_page.url}"
                else:
                    observation = _bounded_evaluate_observation(value, call.get("max_chars"))
            elif action == "wait_for_url":
                action_page.wait_for_url(
                    str(call["url"]),
                    wait_until=call.get("wait_until", "domcontentloaded"),
                    timeout=timeout,
                )
                observation = f"URL ready: {getattr(action_page, 'url', '')}"
            elif action == "wait":
                wait_selector = call.get("selector")
                if wait_selector is not None:
                    wait_timeout = min(timeout, int(call.get("timeout", timeout)))
                    wait_state = str(call.get("wait_state", "visible"))
                    if wait_state not in {"attached", "detached", "visible", "hidden"}:
                        raise ValueError(
                            "wait_state must be attached, detached, visible, or hidden"
                        )
                    action_page.locator(str(wait_selector)).first.wait_for(
                        state=wait_state, timeout=wait_timeout
                    )
                    observation = f"Selector {wait_state}: {wait_selector}"
                else:
                    requested_ms = (
                        int(call["timeout"])
                        if "timeout" in call
                        else round(float(call["seconds"]) * 1000)
                    )
                    if requested_ms > timeout:
                        raise TimeoutError("wait exceeds the remaining action budget")
                    action_page.wait_for_timeout(requested_ms)
                    observation = f"Waited {requested_ms} ms."
            elif action == "back":
                action_page.go_back(wait_until=call.get("wait_until", "commit"), timeout=timeout)
                observation = f"Navigated back to: {getattr(action_page, 'url', '')}"
            elif action == "forward":
                action_page.go_forward(wait_until=call.get("wait_until", "commit"), timeout=timeout)
                observation = f"Navigated forward to: {getattr(action_page, 'url', '')}"
            elif action == "scroll":
                publish_layout()
                direction = str(call.get("direction", "down"))
                amount = int(call.get("amount", 700))
                dx = amount if direction == "right" else -amount if direction == "left" else 0
                dy = amount if direction == "down" else -amount if direction == "up" else 0
                action_page.mouse.wheel(dx, dy)
                observation = f"Scrolled {direction} by {amount}px."
            elif action == "press":
                target = call.get("target") or call.get("selector")
                if target:
                    _prepare_geometry_action(
                        action_page,
                        locator_call,
                        action,
                        publish_layout,
                        action_remaining,
                        layout_observer is not None,
                    )
                    locator_for(action_page, locator_call).press(
                        str(call["key"]),
                        timeout=operation_timeout(),
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
