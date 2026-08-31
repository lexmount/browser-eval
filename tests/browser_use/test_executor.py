from __future__ import annotations

from harness.browser_use.executor import execute_steps


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
