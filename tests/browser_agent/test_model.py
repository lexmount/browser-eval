import pytest

from harness.browser_agent.model import (
    AstraResponsesModel,
    cow_messages_to_responses,
    cow_tools_to_responses,
    response_to_chat_chunk,
)


class _Request:
    system = "system"
    tools = []
    max_tokens = 100

    def __init__(self, messages):
        self.messages = messages


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.payloads = []

    def post(self, _url, **kwargs):
        self.payloads.append(kwargs["json"])
        call_id = f"call_{len(self.payloads)}"
        return _Response(
            {
                "id": f"resp_{len(self.payloads)}",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": "browser",
                        "arguments": "{}",
                    }
                ],
            }
        )

    def get(self, _url, **_kwargs):
        return _Response({"id": "gpt-6-astra"})


def test_translates_tool_round_trip() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "open it"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "browser",
                    "input": {"action": "navigate", "url": "https://example.com"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "done"}
            ],
        },
    ]
    assert cow_messages_to_responses(messages) == [
        {"role": "user", "content": "open it"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "browser",
            "arguments": '{"action":"navigate","url":"https://example.com"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "done"},
    ]


def test_translates_tool_schema_and_response() -> None:
    tools = cow_tools_to_responses(
        [{"name": "browser", "description": "Browse", "input_schema": {"type": "object"}}]
    )
    assert tools[0]["name"] == "browser"
    chunk = response_to_chat_chunk(
        {
            "id": "resp_1",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_2",
                    "name": "browser",
                    "arguments": '{"action":"snapshot"}',
                }
            ],
        }
    )
    assert chunk["choices"][0]["finish_reason"] == "tool_calls"
    assert chunk["choices"][0]["delta"]["tool_calls"][0]["id"] == "call_2"


def test_model_continues_responses_conversation_without_resending_assistant_call() -> None:
    session = _Session()
    model = AstraResponsesModel(api_key="test", session=session)
    list(model.call_stream(_Request([{"role": "user", "content": "open it"}])))
    list(
        model.call_stream(
            _Request(
                [
                    {"role": "user", "content": "open it"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "call_1",
                                "name": "browser",
                                "input": {},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call_1",
                                "content": "done",
                            }
                        ],
                    },
                ]
            )
        )
    )
    assert session.payloads[1]["previous_response_id"] == "resp_1"
    assert session.payloads[1]["input"] == [
        {"type": "function_call_output", "call_id": "call_1", "output": "done"}
    ]


def test_model_rejects_malformed_success_payload() -> None:
    class MalformedSession:
        def post(self, _url, **_kwargs):
            return _Response({"output": []})

    model = AstraResponsesModel(api_key="test", session=MalformedSession())

    with pytest.raises(ValueError, match="non-empty id"):
        list(model.call_stream(_Request([{"role": "user", "content": "open it"}])))


def test_model_access_preflight_checks_exact_model() -> None:
    model = AstraResponsesModel(api_key="test", session=_Session())

    model.verify_access()
