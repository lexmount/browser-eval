from pathlib import Path

from harness.trajectory_replay.remote_execution import (
    NETWORK_SETTLE_MS,
    _finalize_network_recorder,
)


def test_network_finalization_waits_for_background_response_before_stop() -> None:
    events = []

    class Page:
        def wait_for_timeout(self, value):
            events.append(("wait", value))

    class Recorder:
        def stop(self, context, path):
            events.append(("stop", context, path))

    class Service:
        _page = Page()
        _context = "context"

        def _submit(self, function):
            return function()

    path = Path("network.har")
    _finalize_network_recorder(Service(), Recorder(), path)
    assert events == [("wait", NETWORK_SETTLE_MS), ("stop", "context", path)]
    assert NETWORK_SETTLE_MS >= 2_000
