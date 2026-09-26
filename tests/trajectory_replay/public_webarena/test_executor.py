from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from harness.trajectory_replay.public_webarena.executor import (
    execute_steps,
    judge_outputs,
    validate_outputs,
)


class FakePage:
    def __init__(self):
        self.calls = []
        self.url = "about:blank"
        self.mouse = self
        self.keyboard = self
        self.visible = True
        self.enabled = True
        self.trial_ready = True

    def goto(self, url, **kwargs):
        self.calls.append(("navigate", url))
        self.url = url

        class Response:
            status = 200

        return Response()

    def title(self):
        return "Example"

    def wait_for_url(self, url, **kwargs):
        self.calls.append(("wait_for_url", url, kwargs))
        self.url = "https://example.com/reports/ready"

    def wait_for_timeout(self, milliseconds):
        self.calls.append(("wait", milliseconds))

    def wait_for_load_state(self, state, **kwargs):
        self.calls.append(("load_state", state, kwargs))

    def evaluate(self, script, argument=None):
        self.calls.append(("evaluate", script, argument))
        if script == "() => ({ok: true, count: 2})":
            return {"ok": True, "count": 2}
        return {"found": True, "rect": {"width": 0}}

    def locator(self, selector):
        page = self

        class Locator:
            @property
            def first(self):
                page.calls.append(("first", selector))
                return self

            def click(self, **kwargs):
                kind = "trial_click" if kwargs.get("trial") else "click"
                page.calls.append((kind, selector, kwargs))
                if kwargs.get("trial") and not page.trial_ready:
                    raise TimeoutError("trial timeout: element is covered by an overlay")

            def is_visible(self):
                page.calls.append(("is_visible", selector))
                return page.visible

            def is_enabled(self):
                page.calls.append(("is_enabled", selector))
                return page.enabled

            def is_editable(self):
                page.calls.append(("is_editable", selector))
                return page.enabled

            def hover(self, **kwargs):
                kind = "trial_hover" if kwargs.get("trial") else "hover"
                page.calls.append((kind, selector, kwargs))

            def wait_for(self, **kwargs):
                page.calls.append(("wait_for", selector, kwargs))

            def dblclick(self, **kwargs):
                page.calls.append(("double_click", selector, kwargs))

            def fill(self, value, **kwargs):
                page.calls.append(("fill", selector, value))

            def inner_text(self, **kwargs):
                page.calls.append(("get_text", selector))
                return "Example Domain"

            def text_content(self, **kwargs):
                page.calls.append(("get_text", selector))
                return "Example Domain"

            def all_text_contents(self):
                page.calls.append(("get_all_text", selector))
                return [" First ", "Second"]

            def select_option(self, value, **kwargs):
                page.calls.append(("select", selector, value, kwargs))

            def press(self, key, **kwargs):
                page.calls.append(("press_target", selector, key, kwargs))

            def get_attribute(self, name, **kwargs):
                page.calls.append(("get_attribute", selector, name))
                return "redbox"

            def evaluate_all(self, expression, name):
                page.calls.append(("get_all_attribute", selector, name))
                return ["one", "two"]

            def evaluate(self, expression, argument, **kwargs):
                if isinstance(argument, dict) and "header" in argument:
                    page.calls.append(("get_table_column", selector, argument, kwargs))
                    return ["First", "Second"]
                page.calls.append(("get_property", selector, argument))
                return True if argument == "checked" else "Lexmount"

        return Locator()

    def go_back(self, **kwargs):
        self.calls.append(("back", kwargs["wait_until"]))

    def go_forward(self, **kwargs):
        self.calls.append(("forward", kwargs["wait_until"]))

    def wheel(self, dx, dy):
        self.calls.append(("scroll", dx, dy))

    def press(self, key):
        self.calls.append(("press", key))


def test_declared_navigation_wait_surrounds_form_action():
    page = FakePage()

    @contextmanager
    def expect_navigation(**kwargs):
        page.calls.append(("navigation_start", kwargs["wait_until"]))
        yield
        page.calls.append(("navigation_complete",))

    page.expect_navigation = expect_navigation
    execute_steps(
        page,
        (
            {
                "action": "select",
                "selector": "#author",
                "value": "Jane Austen",
                "wait_for_navigation": True,
                "wait_until": "domcontentloaded",
            },
        ),
    )
    assert page.calls[0] == ("navigation_start", "domcontentloaded")
    assert page.calls[-1] == ("navigation_complete",)
    assert any(call[:3] == ("select", "#author", "Jane Austen") for call in page.calls)


@pytest.mark.parametrize("action", ["click", "fill", "select"])
def test_ref_only_calls_fail_clearly_without_browser_actions(action):
    page = FakePage()
    trace = []
    with pytest.raises(ValueError, match="ref is not supported"):
        execute_steps(page, [{"action": action, "ref": 1}], trace=trace)
    assert page.calls == []
    assert trace[0]["status"] == "fail"
    assert trace[0]["error"].startswith("ValueError:")


def test_execute_steps_counts_only_declared_browser_operations():
    page = FakePage()
    outputs = execute_steps(
        page,
        (
            {"action": "navigate", "url": "https://example.com", "save_as": "page"},
            {"action": "get_text", "selector": "h1", "save_as": "heading"},
        ),
    )

    assert outputs["heading"] == "Example Domain"
    assert "Navigated to: https://example.com" in outputs["page"]
    assert page.calls == [
        ("navigate", "https://example.com"),
        ("load_state", "networkidle", {"timeout": 1500}),
        ("wait", 300),
        ("first", "html"),
        ("get_property", "html", None),
        ("first", "body"),
        ("get_text", "body"),
        ("first", "h1"),
        ("get_text", "h1"),
    ]


def test_navigate_can_create_a_new_page_and_publish_it_to_observers():
    first = FakePage()
    second = FakePage()

    class Context:
        pages = [first]

        def new_page(self):
            self.pages.append(second)
            return second

    first.context = Context()
    observed = []

    execute_steps(
        first,
        ({"action": "navigate", "url": "https://example.com/second", "new_page": True},),
        page_observer=observed.append,
    )

    assert observed == [second]
    assert first.calls == []
    assert second.calls[0] == ("navigate", "https://example.com/second")


def test_trace_has_one_record_per_declared_tool_call():
    trace = []
    execute_steps(
        FakePage(),
        ({"action": "navigate", "url": "https://example.com", "save_as": "page"},),
        trace=trace,
    )

    assert len(trace) == 1
    assert trace[0]["action"] == "navigate"
    assert trace[0]["status"] == "pass"


def test_evaluate_runs_declared_page_script_and_captures_json():
    page = FakePage()

    outputs = execute_steps(
        page,
        (
            {
                "action": "evaluate",
                "script": "() => ({ok: true, count: 2})",
                "save_as": "result",
            },
        ),
    )

    assert outputs["result"] == '{"ok": true, "count": 2}'
    assert page.calls == [("evaluate", "() => ({ok: true, count: 2})", None)]


def test_evaluate_keeps_large_structured_json_complete_for_retrieval():
    value = {"scope": True, "complete": True, "records": ["x" * 1_000] * 50}

    class StructuredPage(FakePage):
        def evaluate(self, script, argument=None):
            self.calls.append(("evaluate", script, argument))
            return value

    outputs = execute_steps(
        StructuredPage(),
        ({"action": "evaluate", "script": "collect()", "save_as": "retrieval"},),
    )

    assert len(outputs["retrieval"]) > 40_000
    assert json.loads(outputs["retrieval"]) == value


def test_evaluate_never_slices_structured_json_at_an_explicit_limit():
    value = {"records": ["x" * 100]}

    class StructuredPage(FakePage):
        def evaluate(self, script, argument=None):
            return value

    trace = []
    with pytest.raises(ValueError, match="structured observation exceeds"):
        execute_steps(
            StructuredPage(),
            ({"action": "evaluate", "script": "collect()", "max_chars": 20},),
            trace=trace,
        )

    assert trace[0]["status"] == "fail"
    assert trace[0]["output"] == ""


def test_evaluate_keeps_parseable_json_strings_atomic_but_bounds_plain_text():
    values = iter((json.dumps({"record": "x" * 100}), "x" * 50_000))

    class StringPage(FakePage):
        def evaluate(self, script, argument=None):
            return next(values)

    page = StringPage()
    structured = execute_steps(
        page,
        ({"action": "evaluate", "script": "json()", "save_as": "result"},),
    )
    plain = execute_steps(
        page,
        ({"action": "evaluate", "script": "text()", "save_as": "result"},),
    )

    assert json.loads(structured["result"])["record"] == "x" * 100
    assert len(plain["result"]) == 40_000


def test_evaluate_structured_limit_counts_utf8_bytes():
    value = {"records": ["😀" * 1_100_000]}

    class StructuredPage(FakePage):
        def evaluate(self, script, argument=None):
            return value

    with pytest.raises(ValueError, match="4194304-byte evidence limit"):
        execute_steps(
            StructuredPage(),
            ({"action": "evaluate", "script": "collect()"},),
        )


def test_evaluate_explicit_function_body_executes_side_effect_once():
    class ReturnPage(FakePage):
        def __init__(self):
            super().__init__()
            self.effects = 0

        def evaluate(self, script, argument=None):
            self.calls.append(("evaluate", script, argument))
            self.effects += 1
            return self.effects

    page = ReturnPage()
    outputs = execute_steps(
        page,
        (
            {
                "action": "evaluate",
                "script": "window.count += 1; return window.count",
                "script_mode": "function_body",
                "save_as": "count",
            },
        ),
    )

    assert outputs == {"count": "1"}
    assert page.effects == 1
    assert len(page.calls) == 1
    assert page.calls[0][1].startswith("(async () => {")


def test_evaluate_never_retries_default_script_after_illegal_return():
    class ReturnPage(FakePage):
        def evaluate(self, script, argument=None):
            self.calls.append(("evaluate", script, argument))
            raise RuntimeError("SyntaxError: Illegal return statement")

    page = ReturnPage()
    with pytest.raises(RuntimeError, match="Illegal return statement"):
        execute_steps(
            page,
            ({"action": "evaluate", "script": "sideEffect(); return true"},),
        )
    assert page.calls == [("evaluate", "sideEffect(); return true", None)]


def test_evaluate_rejects_unknown_script_mode_before_page_execution():
    page = FakePage()
    with pytest.raises(ValueError, match="script_mode"):
        execute_steps(
            page,
            ({"action": "evaluate", "script": "1", "script_mode": "retry"},),
        )
    assert page.calls == []


def test_evaluate_does_not_retry_a_runtime_error():
    class BrokenPage(FakePage):
        def evaluate(self, script, argument=None):
            self.calls.append(("evaluate", script, argument))
            raise RuntimeError("ReferenceError: missing is not defined")

    page = BrokenPage()
    with pytest.raises(RuntimeError, match="ReferenceError"):
        execute_steps(page, ({"action": "evaluate", "script": "missing()"},))
    assert page.calls == [("evaluate", "missing()", None)]


def test_navigation_network_idle_is_best_effort_and_keeps_render_grace():
    class BusyPage(FakePage):
        def wait_for_load_state(self, state, **kwargs):
            self.calls.append(("load_state", state, kwargs))
            raise TimeoutError("page keeps a streaming request open")

    page = BusyPage()
    responses = []
    execute_steps(
        page,
        ({"action": "navigate", "url": "https://example.com"},),
        navigation_response_observer=responses.append,
    )
    assert ("load_state", "networkidle", {"timeout": 1500}) in page.calls
    assert ("wait", 300) in page.calls
    assert len(responses) == 1
    assert responses[0].status == 200


def test_same_document_navigation_without_response_reloads_for_network_evidence():
    class SamePage(FakePage):
        def __init__(self):
            super().__init__()
            self.url = "https://example.com/"

        def goto(self, url, **kwargs):
            self.calls.append(("navigate", url, kwargs))
            self.url = url + "/"
            return None

        def reload(self, **kwargs):
            self.calls.append(("reload", kwargs))

            class Response:
                status = 200

            return Response()

    page = SamePage()
    responses = []
    execute_steps(
        page,
        ({"action": "navigate", "url": "https://example.com"},),
        navigation_response_observer=responses.append,
    )

    assert any(call[0] == "reload" for call in page.calls)
    assert len(responses) == 1
    assert responses[0].status == 200


def test_form_submission_evaluate_continues_only_after_expected_navigation():
    class FormPage(FakePage):
        def evaluate(self, script, argument=None):
            self.calls.append(("evaluate", script, argument))
            raise RuntimeError(
                "Execution context was destroyed, most likely because of a navigation"
            )

        def wait_for_url(self, url, **kwargs):
            self.calls.append(("wait_for_url", url.pattern, kwargs))
            self.url = "http://gitlab.example/byteblaze/new_project"

    page = FormPage()
    trace = []
    outputs = execute_steps(
        page,
        [
            {
                "action": "evaluate",
                "script": "() => document.querySelector('form').submit()",
                "accept_navigation_url_regex": r"/byteblaze/new_project$",
                "save_as": "submitted",
            },
            {"action": "get_text", "selector": "body", "save_as": "landed"},
        ],
        trace=trace,
    )

    assert outputs["submitted"].endswith("/byteblaze/new_project")
    assert outputs["landed"] == "Example Domain"
    assert trace[0]["accepted_navigation"]["expected_url_regex"] == r"/byteblaze/new_project$"


def test_form_submission_evaluate_rejects_wrong_navigation():
    class WrongPage(FakePage):
        def evaluate(self, script, argument=None):
            raise RuntimeError(
                "Execution context was destroyed, most likely because of a navigation"
            )

        def wait_for_url(self, url, **kwargs):
            self.url = "http://gitlab.example/byteblaze/another_project"

    with pytest.raises(ValueError, match="wrong URL"):
        execute_steps(
            WrongPage(),
            [
                {
                    "action": "evaluate",
                    "script": "() => document.querySelector('form').submit()",
                    "accept_navigation_url_regex": r"/byteblaze/new_project$",
                }
            ],
        )


def test_action_can_wait_for_loading_mask_to_disappear_before_clicking():
    page = FakePage()
    execute_steps(
        page,
        (
            {
                "action": "click",
                "selector": "button.filters",
                "wait_for_selector_hidden": ".loading-mask",
            },
        ),
    )
    assert page.calls[:2] == [
        ("wait_for", ".loading-mask", {"state": "hidden", "timeout": 30000}),
        ("first", "button.filters"),
    ]


def test_wait_selector_uses_visible_readiness_instead_of_sleeping():
    page = FakePage()

    execute_steps(
        page,
        ({"action": "wait", "selector": "div.message-success:visible", "timeout": 12_000},),
    )

    assert page.calls == [
        ("first", "div.message-success:visible"),
        (
            "wait_for",
            "div.message-success:visible",
            {"state": "visible", "timeout": 12_000},
        ),
    ]


def test_wait_selector_can_use_attached_readiness_for_non_geometric_status_messages():
    page = FakePage()

    execute_steps(
        page,
        (
            {
                "action": "wait",
                "selector": "div.message-success",
                "wait_state": "attached",
                "timeout": 12_000,
            },
        ),
    )

    assert page.calls == [
        ("first", "div.message-success"),
        (
            "wait_for",
            "div.message-success",
            {"state": "attached", "timeout": 12_000},
        ),
    ]


def test_default_action_timeout_is_thirty_seconds():
    page = FakePage()

    class Locator:
        @property
        def first(self):
            return self

        def click(self, **kwargs):
            page.calls.append(("timeout", kwargs["timeout"]))

    page.locator = lambda _selector: Locator()

    execute_steps(page, ({"action": "click", "selector": "#target"},))

    assert page.calls == [("timeout", 30_000)]


def test_wait_for_url_uses_declared_readiness_contract():
    page = FakePage()

    execute_steps(
        page,
        (
            {
                "action": "wait_for_url",
                "url": "**/reports/**",
                "wait_until": "domcontentloaded",
                "timeout_ms": 120_000,
            },
        ),
    )

    assert page.calls == [
        (
            "wait_for_url",
            "**/reports/**",
            {"wait_until": "domcontentloaded", "timeout": 120_000},
        )
    ]


@pytest.mark.parametrize("duration,expected", [({"timeout": 700}, 700), ({"seconds": 2}, 2000)])
def test_wait_preserves_recorded_duration(duration, expected):
    page = FakePage()
    execute_steps(page, ({"action": "wait", **duration},))
    assert page.calls == [("wait", expected)]


def test_task_wall_clock_budget_caps_each_action_timeout(monkeypatch):
    page = FakePage()

    class Locator:
        @property
        def first(self):
            return self

        def click(self, **kwargs):
            page.calls.append(("timeout", kwargs["timeout"]))

    page.locator = lambda _selector: Locator()
    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.executor.time.monotonic",
        MagicMock(side_effect=(10.0, 10.2)),
    )

    execute_steps(
        page,
        ({"action": "click", "selector": "#target", "timeout_ms": 30_000},),
        task_timeout_ms=5_000,
    )

    assert page.calls == [("timeout", 4_800)]


def test_initial_sweep_can_shorten_only_undeclared_action_waits():
    page = FakePage()
    execute_steps(
        page,
        (
            {"action": "click", "selector": "#first"},
            {"action": "click", "selector": "#second", "timeout_ms": 25_000},
        ),
        default_action_timeout_ms=15_000,
    )
    timeouts = [call[2]["timeout"] for call in page.calls if call[0] == "click"]
    assert timeouts == [15_000, 25_000]


def test_shorter_action_budget_also_caps_missing_structured_target_wait():
    page = FakePage()
    observed = {}

    class MissingLocator:
        @property
        def first(self):
            return self

        def count(self):
            return 0

        def wait_for(self, **kwargs):
            observed.update(kwargs)

    page.get_by_text = lambda *_args, **_kwargs: MissingLocator()
    with pytest.raises(ValueError, match="matched 0"):
        execute_steps(
            page,
            ({"action": "click", "target": {"by": "text", "value": "missing"}},),
            default_action_timeout_ms=15_000,
        )
    assert observed["timeout"] == 15_000


def test_task_wall_clock_budget_stops_before_next_action(monkeypatch):
    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.executor.time.monotonic",
        MagicMock(side_effect=(10.0, 11.1)),
    )

    with pytest.raises(TimeoutError, match="before action 1"):
        execute_steps(
            FakePage(),
            ({"action": "navigate", "url": "https://example.com"},),
            task_timeout_ms=1_000,
        )


@pytest.mark.parametrize(
    "wait_call",
    (
        {
            "action": "get_text",
            "selector": "h1",
            "wait_for_text": "Example Domain",
        },
        {
            "action": "get_table_column",
            "selector": "table",
            "header": "Name",
            "wait_for_value_regex": "First",
        },
        {
            "action": "snapshot",
            "selector": "body",
            "wait_for_text_regex": "Example Domain",
        },
    ),
)
def test_action_wait_deadline_does_not_replace_task_deadline(wait_call):
    page = FakePage()

    execute_steps(
        page,
        (
            wait_call,
            {"action": "click", "selector": "#target", "timeout_ms": 55_000},
        ),
        task_timeout_ms=60_000,
    )

    click = next(call for call in page.calls if call[:2] == ("click", "#target"))
    assert click[2]["timeout"] > 50_000


@pytest.mark.parametrize(
    ("call", "record_prefix"),
    (
        ({"action": "click", "selector": "#target"}, ("click", "#target")),
        ({"action": "double_click", "selector": "#target"}, ("double_click", "#target")),
        (
            {"action": "select", "selector": "#target", "value": "x"},
            ("select", "#target", "x"),
        ),
        (
            {"action": "press", "selector": "#target", "key": "Enter"},
            ("press_target", "#target", "Enter"),
        ),
    ),
)
def test_navigation_triggering_actions_can_disable_playwright_auto_wait(call, record_prefix):
    page = FakePage()
    execute_steps(page, ({**call, "no_wait_after": True},))

    record = next(item for item in page.calls if item[: len(record_prefix)] == record_prefix)
    assert record[-1]["no_wait_after"] is True


def test_click_may_accept_only_a_proven_completed_action_navigation_timeout():
    page = FakePage()
    trace = []

    class Locator:
        @property
        def first(self):
            return self

        def click(self, **kwargs):
            raise TimeoutError(
                "Locator.click timed out\n"
                "- performing click action\n"
                "- click action done\n"
                "- waiting for scheduled navigations to finish"
            )

    page.locator = lambda _selector: Locator()
    execute_steps(
        page,
        (
            {
                "action": "click",
                "selector": "#submit",
                "accept_completed_action_navigation_timeout": True,
            },
        ),
        trace=trace,
    )

    assert trace[0]["status"] == "pass"
    assert trace[0]["accepted_navigation_timeout"]["reason"].startswith("click completed")


def test_click_does_not_accept_an_actionability_timeout():
    page = FakePage()

    class Locator:
        @property
        def first(self):
            return self

        def click(self, **kwargs):
            raise TimeoutError("waiting for element to be visible")

    page.locator = lambda _selector: Locator()
    with pytest.raises(TimeoutError, match="visible"):
        execute_steps(
            page,
            (
                {
                    "action": "click",
                    "selector": "#submit",
                    "accept_completed_action_navigation_timeout": True,
                },
            ),
        )


def test_snapshot_can_wait_for_async_visible_text(monkeypatch):
    page = FakePage()
    values = iter(("Calculating route", "Route 5 h 47 min"))

    class Locator:
        @property
        def first(self):
            return self

        def inner_text(self, **kwargs):
            return next(values)

    page.locator = lambda _selector: Locator()
    monotonic = MagicMock(side_effect=(0, 0, 0.1))
    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.executor.time.monotonic", monotonic
    )
    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.executor.time.sleep", lambda _: None
    )

    outputs = execute_steps(
        page,
        (
            {
                "action": "snapshot",
                "selector": "body",
                "save_as": "route",
                "wait_for_text_regex": r"\d+\s*h\s*\d+\s*min",
                "wait_timeout_ms": 1_000,
            },
        ),
    )

    assert outputs["route"] == "Route 5 h 47 min"


def test_page_observer_runs_before_the_measured_action():
    page = FakePage()

    def observe(action_page):
        assert action_page is page
        page.calls.append(("observe",))

    execute_steps(
        page,
        ({"action": "navigate", "url": "https://example.com"},),
        page_observer=observe,
    )

    assert page.calls[0] == ("observe",)
    assert page.calls[1] == ("navigate", "https://example.com")


def test_layout_observer_runs_before_actions_and_after_navigation():
    page = FakePage()

    def publish_layout(action_page, timeout_ms):
        assert action_page is page
        assert timeout_ms > 0
        page.calls.append(("layout", page.url))

    execute_steps(
        page,
        (
            {"action": "navigate", "url": "https://example.com"},
            {"action": "click", "selector": "#target"},
        ),
        layout_observer=publish_layout,
    )

    navigation = page.calls.index(("navigate", "https://example.com"))
    first_layout = page.calls.index(("layout", "https://example.com"))
    assert navigation < first_layout
    assert ("layout", "about:blank") not in page.calls
    click = next(index for index, call in enumerate(page.calls) if call[0] == "click")
    action_layout = max(
        index
        for index, call in enumerate(page.calls[:click])
        if call == ("layout", "https://example.com")
    )
    locator_resolution = page.calls.index(("first", "#target"))
    assert action_layout < locator_resolution < click


def test_dynamic_geometry_is_refreshed_before_one_side_effect_dispatch():
    page = FakePage()
    page.visible = False
    publications = 0

    def publish_layout(action_page, timeout_ms):
        nonlocal publications
        assert action_page is page
        assert timeout_ms > 0
        publications += 1
        if publications == 2:
            page.visible = True

    execute_steps(
        page,
        ({"action": "click", "selector": "#late"},),
        layout_observer=publish_layout,
    )

    assert publications == 2
    assert sum(call[0] == "click" for call in page.calls) == 1


def test_dynamic_overlay_is_refreshed_before_one_side_effect_dispatch():
    page = FakePage()
    page.trial_ready = False
    publications = 0

    def publish_layout(action_page, timeout_ms):
        nonlocal publications
        assert action_page is page
        assert timeout_ms > 0
        publications += 1
        if publications == 2:
            page.trial_ready = True

    execute_steps(
        page,
        ({"action": "click", "selector": "#covered"},),
        layout_observer=publish_layout,
    )

    assert publications == 2
    assert sum(call[0] == "trial_click" for call in page.calls) == 2
    assert sum(call[0] == "click" for call in page.calls) == 1


def test_fill_select_and_press_keep_native_non_click_actionability():
    page = FakePage()
    page.trial_ready = False

    execute_steps(
        page,
        (
            {"action": "fill", "selector": "#field", "text": "value"},
            {"action": "select", "selector": "#choice", "value": "one"},
            {"action": "press", "selector": "#field", "key": "Enter"},
        ),
        layout_observer=lambda _page, _timeout: None,
    )

    assert not any(call[0].startswith("trial_") for call in page.calls)
    assert sum(call[0] == "fill" for call in page.calls) == 1
    assert sum(call[0] == "select" for call in page.calls) == 1
    assert sum(call[0] == "press_target" for call in page.calls) == 1


@pytest.mark.parametrize(("visible", "enabled"), ((False, True), (True, False)))
def test_targeted_press_preserves_native_visibility_and_enabled_semantics(
    visible, enabled
):
    page = FakePage()
    page.visible = visible
    page.enabled = enabled

    execute_steps(
        page,
        ({"action": "press", "selector": "#field", "key": "Enter"},),
        layout_observer=lambda _page, _timeout: None,
    )

    assert sum(call[0] == "press_target" for call in page.calls) == 1
    assert not any(call[0] in {"is_visible", "is_enabled"} for call in page.calls)


def test_history_steps_wait_for_commit_to_support_bfcache_restores():
    page = FakePage()

    execute_steps(page, ({"action": "back"}, {"action": "forward"}))

    assert page.calls == [("back", "commit"), ("forward", "commit")]


def test_attribute_and_property_outputs_are_exact_strings():
    page = FakePage()

    outputs = execute_steps(
        page,
        (
            {
                "action": "get_attribute",
                "selector": "#box0",
                "name": "class",
                "save_as": "class_name",
            },
            {
                "action": "get_property",
                "selector": "#check",
                "name": "checked",
                "save_as": "checked",
            },
        ),
    )

    assert outputs == {"class_name": "redbox", "checked": "true"}


def test_multi_element_observations_are_json_arrays():
    page = FakePage()

    outputs = execute_steps(
        page,
        (
            {
                "action": "get_all_text",
                "selector": ".title",
                "strip": True,
                "save_as": "titles",
            },
            {
                "action": "get_all_attribute",
                "selector": ".item",
                "name": "data-id",
                "save_as": "ids",
            },
        ),
    )

    assert outputs == {"titles": '["First", "Second"]', "ids": '["one", "two"]'}


def test_table_column_is_selected_by_visible_header_instead_of_position():
    page = FakePage()

    outputs = execute_steps(
        page,
        (
            {
                "action": "get_table_column",
                "selector": "table.data-grid",
                "header": "Search Query",
                "save_as": "queries",
                "max_items": 3,
            },
        ),
    )

    assert outputs["queries"] == '["First", "Second"]'
    assert (
        "get_table_column",
        "table.data-grid",
        {"header": "Search Query", "maxItems": 3},
        {"timeout": 30_000},
    ) in page.calls


def test_table_column_can_wait_for_async_filtered_value(monkeypatch):
    page = FakePage()
    values = iter((["Loading"], ["8015551212"]))

    class Locator:
        def evaluate(self, *_args, **_kwargs):
            return next(values)

    page.locator = lambda _selector: type(
        "LegacyLocator", (), {"first": property(lambda self: Locator())}
    )()
    monotonic = MagicMock(side_effect=(0, 0, 0.1))
    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.executor.time.monotonic", monotonic
    )
    monkeypatch.setattr(
        "harness.trajectory_replay.public_webarena.executor.time.sleep", lambda _: None
    )

    outputs = execute_steps(
        page,
        (
            {
                "action": "get_table_column",
                "selector": "table",
                "header": "Phone",
                "wait_for_value_regex": r"^8015551212$",
                "save_as": "phones",
            },
        ),
    )

    assert outputs["phones"] == '["8015551212"]'


def test_exact_ground_truth_judge_reports_expected_and_actual():
    assertions = [{"output": "answer", "equals": "unique-73"}]

    assert validate_outputs({"answer": "unique-73"}, assertions) == []
    assert "exact mismatch" in validate_outputs({"answer": "near-73"}, assertions)[0]
    assert judge_outputs({"answer": "unique-73"}, assertions)[0]["passed"] is True


def test_failed_click_records_actionability_probe_without_adding_a_step():
    class FailingPage(FakePage):
        def locator(self, selector):
            class Locator:
                @property
                def first(self):
                    return self

                def click(self, **kwargs):
                    raise TimeoutError("element is not visible")

            return Locator()

    trace = []
    with pytest.raises(TimeoutError):
        execute_steps(
            FailingPage(),
            ({"action": "click", "selector": "#dynamic"},),
            trace=trace,
        )

    assert len(trace) == 1
    assert trace[0]["status"] == "fail"
    assert trace[0]["actionability_probe"]["rect"]["width"] == 0
