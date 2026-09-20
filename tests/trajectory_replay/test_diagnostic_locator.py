import pytest

from harness.trajectory_replay.webarena_replay import _diagnostic_locator


@pytest.mark.parametrize(("selector", "expected"), [
    ('button:visible:has-text("Filters")', 'button:has-text("Filters")'),
    (':nth-match(a[href="/design"]:visible, 2)', 'a[href="/design"]'),
    (':nth-match(a:is([href="/a"],[href="/b"]):visible, 2)', 'a:is([href="/a"],[href="/b"])'),
    ('[title=":visible"]:visible', '[title=":visible"]'),
    (r'[title="a\":visible"]:visible', r'[title="a\":visible"]'),
    ('div:visible-custom', 'div:visible-custom'),
    ('div:visible_foo', 'div:visible_foo'),
    ('div:visible > button:visible', 'div > button'),
])
def test_diagnostic_candidates_preserve_locator_syntax_and_literals(selector, expected):
    assert _diagnostic_locator(selector) == expected
