from types import SimpleNamespace

import pytest

from harness.browser_viewport import (
    BENCHMARK_VIEWPORT,
    set_verified_viewport,
    validate_viewport_receipt,
)


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {},
        {"requested": BENCHMARK_VIEWPORT, "status": "verified"},
        {
            "requested": BENCHMARK_VIEWPORT,
            "actual": {"width": 780, "height": 437},
            "status": "verified",
        },
        {
            "requested": {"width": 1920, "height": 1080},
            "actual": BENCHMARK_VIEWPORT,
            "status": "verified",
        },
        {"requested": BENCHMARK_VIEWPORT, "actual": BENCHMARK_VIEWPORT, "status": "configured"},
        {
            "requested": BENCHMARK_VIEWPORT,
            "actual": {"width": 1280.0, "height": 900},
            "status": "verified",
        },
    ],
)
def test_qualification_rejects_unverified_or_wrong_viewport(receipt):
    with pytest.raises(ValueError, match="viewport"):
        validate_viewport_receipt(receipt)


def test_qualification_accepts_measured_shared_viewport():
    validate_viewport_receipt(
        {
            "requested": dict(BENCHMARK_VIEWPORT),
            "actual": dict(BENCHMARK_VIEWPORT),
            "status": "verified",
        }
    )


def test_viewport_is_explicitly_set_and_verified_not_assumed_from_config():
    events = []

    def set_size(value):
        events.append(("set", dict(value)))

    def evaluate(script):
        events.append(("read", script))
        return {"width": 1280, "height": 900}

    receipt = set_verified_viewport(SimpleNamespace(set_viewport_size=set_size, evaluate=evaluate))
    assert [item[0] for item in events] == ["set", "read"]
    assert events[0][1] == BENCHMARK_VIEWPORT
    assert receipt == {
        "requested": BENCHMARK_VIEWPORT,
        "actual": BENCHMARK_VIEWPORT,
        "status": "verified",
    }


@pytest.mark.parametrize(
    "actual", [{"width": 780, "height": 437}, {"width": 1920, "height": 1080}, None]
)
def test_viewport_unsupported_or_ignored_override_fails_closed(actual):
    page = SimpleNamespace(set_viewport_size=lambda value: None, evaluate=lambda script: actual)
    with pytest.raises(RuntimeError, match="viewport contract mismatch"):
        set_verified_viewport(page)


def test_browser_use_applies_viewport_before_login_and_records_receipt():
    import inspect

    from harness.browser_use import runner
    source = inspect.getsource(runner._run_attempt_locked)
    assert source.index("set_verified_viewport(service._page)") < source.index(
        "_login_context("
    )
    assert '"viewport": viewport_receipt' in source
    assert "set_verified_viewport(page)" in inspect.getsource(runner._login_context)
