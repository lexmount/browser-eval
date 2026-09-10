from __future__ import annotations

import pytest

from harness.browser_use.targets import locator_for, validate_target


class Locator:
    def __init__(self, count):
        self._count = count

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def wait_for(self, **kwargs):
        return None


class Page:
    def __init__(self, count=1):
        self.count = count
        self.calls = []

    def locator(self, value):
        self.calls.append(("locator", value))
        return Locator(self.count)

    def get_by_role(self, role, **kwargs):
        self.calls.append(("role", role, kwargs))
        return Locator(self.count)

    def get_by_label(self, value, **kwargs):
        self.calls.append(("label", value, kwargs))
        return Locator(self.count)

    def get_by_text(self, value, **kwargs):
        self.calls.append(("text", value, kwargs))
        return Locator(self.count)

    def get_by_test_id(self, value):
        self.calls.append(("test_id", value))
        return Locator(self.count)


def test_structured_role_target_uses_public_playwright_locator():
    page = Page()
    locator_for(
        page,
        {"target": {"by": "role", "role": "button", "name": "Save", "exact": False}},
    )
    assert page.calls == [("role", "button", {"name": "Save", "exact": False})]


def test_structured_target_fails_on_ambiguous_match():
    with pytest.raises(ValueError, match="exactly one element; matched 2"):
        locator_for(Page(count=2), {"target": {"by": "css", "value": ".item"}})


def test_structured_target_waits_for_dynamic_match_before_cardinality_gate():
    class DynamicLocator(Locator):
        def __init__(self):
            super().__init__(0)
            self.wait = None

        def wait_for(self, **kwargs):
            self.wait = kwargs
            self._count = 1

    page = Page()
    dynamic = DynamicLocator()
    page.get_by_text = lambda *_args, **_kwargs: dynamic

    assert (
        locator_for(
            page,
            {
                "target": {"by": "text", "value": "Loaded"},
                "target_timeout_ms": 4_000,
            },
        )
        is dynamic
    )
    assert dynamic.wait == {"state": "attached", "timeout": 4_000}


def test_all_matches_returns_structured_locator_without_cardinality_gate():
    locator = locator_for(
        Page(count=2),
        {"target": {"by": "css", "value": ".item"}},
        all_matches=True,
    )

    assert locator.count() == 2


def test_target_rejects_selector_and_structured_target_together():
    with pytest.raises(ValueError, match="not both"):
        validate_target({"selector": "#save", "target": {"by": "role", "role": "button"}})
