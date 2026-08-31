from __future__ import annotations

from harness.browser_use.executor import execute_steps


class FakePage:
    def __init__(self):
        self.calls = []

    def goto(self, url, **kwargs):
        self.calls.append(("navigate", url))

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

            def wait_for(self, **kwargs):
                page.calls.append(("wait", selector))

            def inner_text(self, **kwargs):
                page.calls.append(("extract", selector))
                return "Example Domain"

        return Locator()

    def go_back(self, **kwargs):
        self.calls.append(("back", kwargs["wait_until"]))

    def go_forward(self, **kwargs):
        self.calls.append(("forward", kwargs["wait_until"]))


def test_execute_steps_counts_only_declared_browser_operations():
    page = FakePage()
    outputs = execute_steps(
        page,
        (
            {"action": "navigate", "url": "https://example.com"},
            {"action": "wait", "selector": "h1"},
            {"action": "extract", "selector": "h1", "save_as": "heading"},
        ),
    )

    assert outputs == {"heading": "Example Domain"}
    assert page.calls == [
        ("navigate", "https://example.com"),
        ("first", "h1"),
        ("wait", "h1"),
        ("first", "h1"),
        ("extract", "h1"),
    ]


def test_history_steps_wait_for_commit_to_support_bfcache_restores():
    page = FakePage()

    execute_steps(page, ({"action": "back"}, {"action": "forward"}))

    assert page.calls == [("back", "commit"), ("forward", "commit")]
