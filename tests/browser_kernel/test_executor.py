from __future__ import annotations

from contextlib import contextmanager

import pytest

from harness.browser_kernel.executor import execute_steps, judge_outputs, validate_outputs


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
                page.calls.append(("click", selector))

            def fill(self, value, **kwargs):
                page.calls.append(("fill", selector, value))

            def inner_text(self, **kwargs):
                page.calls.append(("get_text", selector))
                return "Example Domain"

            def text_content(self, **kwargs):
                page.calls.append(("get_text", selector))
                return "Example Domain"

            def select_option(self, value, **kwargs):
                page.calls.append(("select", selector, value))

            def get_attribute(self, name, **kwargs):
                page.calls.append(("get_attribute", selector, name))
                return "redbox"

            def evaluate(self, expression, name, **kwargs):
                page.calls.append(("get_property", selector, name))
                return True if name == "checked" else "Lexmount"

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
    assert ("select", "#author", "Jane Austen") in page.calls


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
