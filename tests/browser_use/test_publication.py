from harness.browser_use.publication import sanitize, trajectory
from harness.environments.webarena.topology import RECORDING_GATEWAYS

GW = RECORDING_GATEWAYS[0]


def test_sanitize_redacts_credentials_origins_and_local_paths() -> None:
    value = sanitize(
        {
            "password": "synthetic-secret",
            "response_cookies": None,
            "url": "http://host.docker.internal:8023/dashboard/todos",
            "message": "Bearer abc.def from /Users/example/run/output.json",
        }
    )

    assert value["password"] == "[REDACTED]"
    assert value["response_cookies"] is None
    assert value["url"] == "__GITLAB__/dashboard/todos"
    assert value["message"] == "Bearer [REDACTED] from <LOCAL_PATH>"


def test_trajectory_pairs_tool_start_and_end_events() -> None:
    events = [
        {"type": "turn_start", "timestamp": 1, "data": {"turn": 3}},
        {
            "type": "tool_execution_start",
            "timestamp": 2,
            "data": {
                "tool_call_id": "call-1",
                "tool_name": "browser",
                "arguments": {"action": "navigate", "url": ("http://" + GW + ":8023")},
            },
        },
        {
            "type": "tool_execution_end",
            "timestamp": 3,
            "data": {
                "tool_call_id": "call-1",
                "tool_name": "browser",
                "status": "success",
                "result": "ok",
                "execution_time": 1.25,
            },
        },
    ]

    assert trajectory(events) == [
        {
            "step": 1,
            "turn": 3,
            "timestamp": 2,
            "tool": "browser",
            "arguments": {"action": "navigate", "url": "__GITLAB__"},
            "status": "success",
            "duration_seconds": 1.25,
            "result": "ok",
            "error": None,
        }
    ]
