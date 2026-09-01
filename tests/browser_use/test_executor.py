from __future__ import annotations

from harness.browser_use.executor import execute_steps, judge_outputs, validate_outputs

import pytest


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

        return Locator()

    def go_back(self, **kwargs):
        self.calls.append(("back", kwargs["wait_until"]))

    def go_forward(self, **kwargs):
        self.calls.append(("forward", kwargs["wait_until"]))

    def wheel(self, dx, dy):
        self.calls.append(("scroll", dx, dy))

    def press(self, key):
        self.calls.append(("press", key))



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
