"""Task 108's date change contract through the public replay dispatcher."""

from __future__ import annotations

import json
from pathlib import Path

from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.trajectory_replay.webarena_replay import _execute_replay_call

CANDIDATE = (
    Path(__file__).resolve().parents[2]
    / 'evaluation/trajectory-replay/webarena-verified/'
    'retrieval-candidates/task-108-ajax-proposal.json'
)


def test_task108_explicit_tab_commits_date_where_no_tab_does_not() -> None:
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    calls = candidate["tool_calls"]
    validate_contract(candidate["retrieval_contract"], calls)
    assert len(calls) == 16
    assert calls[11] == {"action": "press", "key": "Tab"}
    assert candidate["retrieval_contract"]["sources"][0]["step"] == 16

    class DateState:
        value = ""
        shifted_value = ""
        committed_value = ""
        focused = False

    class Locator:
        first = None

        def __init__(self, state: DateState):
            self.state = state
            self.first = self

        def evaluate(self, script: str, *_args: object) -> str | None:
            if "element.focus()" in script:
                self.state.focused = True
                return "Linux x86_64"
            return None

    class Keyboard:
        def __init__(self, state: DateState):
            self.state = state

        def press(self, key: str) -> None:
            if key == "Backspace" and self.state.focused:
                self.state.value = ""
            if key == "Tab":
                self.state.focused = False
                self.state.shifted_value = self.state.value
                self.state.committed_value = self.state.value.lstrip("0")

        def insert_text(self, value: str) -> None:
            self.state.value = value

    class Page:
        def __init__(self, state: DateState):
            self.keyboard = Keyboard(state)
            self.state = state

        def locator(self, _selector: str) -> Locator:
            return Locator(self.state)

    class Service:
        def __init__(self, state: DateState):
            self._page = Page(state)

        def _submit(self, callback):
            return callback()

    class Result:
        status = "success"
        result = "pressed"

    class Tool:
        def __init__(self, state: DateState):
            self.service = Service(state)

        def _get_service(self) -> Service:
            return self.service

        def execute(self, arguments: dict) -> Result:
            assert arguments == {"action": "press", "key": "Tab"}
            self.service._page.keyboard.press(arguments["key"])
            return Result()

    red = DateState()
    red_tool = Tool(red)
    assert _execute_replay_call(red_tool, calls[9])[0] == "success"
    assert _execute_replay_call(red_tool, calls[10])[0] == "success"
    assert (red.value, red.shifted_value, red.committed_value, red.focused) == (
        "05/31/2023", "", "", True
    )

    green = DateState()
    green_tool = Tool(green)
    assert _execute_replay_call(green_tool, calls[9])[0] == "success"
    assert _execute_replay_call(green_tool, calls[10])[0] == "success"
    assert _execute_replay_call(green_tool, calls[11])[0] == "success"
    assert (green.value, green.shifted_value, green.committed_value, green.focused) == (
        "05/31/2023", "05/31/2023", "5/31/2023", False
    )
