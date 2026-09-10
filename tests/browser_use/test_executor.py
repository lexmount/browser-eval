from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from harness.browser_use.executor import execute_steps, judge_outputs, validate_outputs


class FakePage:
    def __init__(self):
        self.calls = []
        self.url = "about:blank"
        self.mouse = self
        self.keyboard = self

    def goto(self, url, **kwargs):
        self.calls.append(("navigate", url))
        self.url = url

        class Response:
            status = 200

        return Response()

    def title(self):
        return "Example"

    def evaluate(self, script, argument=None):
        return {"found": True, "rect": {"width": 0}}

    def locator(self, selector):
        page = self

        class Locator:
            @property
            def first(self):
                page.calls.append(("first", selector))
                return self

            def click(self, **kwargs):
                page.calls.append(("click", selector, kwargs))

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
        ("first", "body"),
        ("get_text", "body"),
        ("first", "h1"),
        ("get_text", "h1"),
    ]


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
def test_navigation_triggering_actions_can_disable_playwright_auto_wait(
    call, record_prefix
):
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
    monkeypatch.setattr("harness.browser_use.executor.time.monotonic", monotonic)
    monkeypatch.setattr("harness.browser_use.executor.time.sleep", lambda _: None)

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
