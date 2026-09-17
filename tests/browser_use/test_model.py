import pytest

from harness.browser_use.model import (
    OpenAIResponsesModel,
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
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

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
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "done"}],
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
    model = OpenAIResponsesModel(api_key="test", session=session)
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

    model = OpenAIResponsesModel(api_key="test", session=MalformedSession())

    with pytest.raises(ValueError, match="non-empty id"):
        list(model.call_stream(_Request([{"role": "user", "content": "open it"}])))


@pytest.mark.parametrize("rewrite", ["shorten", "replace", "extend", "mutate"])
def test_model_replays_rewritten_history_without_stale_response_chain(rewrite) -> None:
    session = _Session()
    model = OpenAIResponsesModel(api_key="test", session=session)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "old objective"}]},
        {"role": "assistant", "content": "old observation"},
        {"role": "user", "content": "continue"},
    ]
    list(model.call_stream(_Request(messages)))
    call = {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "call_1", "name": "browser", "input": {}}],
    }
    result = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "new observation"}],
    }
    if rewrite == "shorten":
        rewritten = [call, result]
    elif rewrite == "replace":
        rewritten = [{"role": "user", "content": "summary"}, call, result]
    elif rewrite == "extend":
        rewritten = [{"role": "user", "content": "summary"}, *messages, call, result]
    else:
        messages[0]["content"][0]["text"] = "corrected objective"
        rewritten = [*messages, call, result]

    list(model.call_stream(_Request(rewritten)))

    assert "previous_response_id" not in session.payloads[1]
    assert session.payloads[1]["input"] == cow_messages_to_responses(rewritten)
    assert session.payloads[1]["input"][-1]["output"] == "new observation"
    # The replacement starts a new valid chain; its next extension is incremental.
    list(model.call_stream(_Request([*rewritten, {"role": "user", "content": "next"}])))
    assert session.payloads[2]["previous_response_id"] == "resp_2"
    assert session.payloads[2]["input"] == [{"role": "user", "content": "next"}]


def test_model_rejects_orphan_tool_output_after_history_rewrite_before_post() -> None:
    session = _Session()
    model = OpenAIResponsesModel(api_key="test", session=session)
    list(model.call_stream(_Request([{"role": "user", "content": "old objective"}])))
    orphan = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "observation"}],
        }
    ]

    with pytest.raises(ValueError, match="tool output.*call_1.*matching.*call"):
        list(model.call_stream(_Request(orphan)))

    assert len(session.payloads) == 1
    assert model.request_attempt_count == 1


def test_model_reset_forgets_previous_history() -> None:
    session = _Session()
    model = OpenAIResponsesModel(api_key="test", session=session)
    request = _Request([{"role": "user", "content": "objective"}])
    list(model.call_stream(request))
    model.reset()
    list(model.call_stream(request))
    assert "previous_response_id" not in session.payloads[1]
    assert session.payloads[1]["input"] == cow_messages_to_responses(request.messages)


def test_model_failed_replay_does_not_advance_history_state() -> None:
    requests = __import__("requests")

    class RetrySession(_Session):
        fail_next = False

        def post(self, url, **kwargs):
            if self.fail_next:
                self.fail_next = False
                raise requests.Timeout("timed out")
            return super().post(url, **kwargs)

    session = RetrySession()
    model = OpenAIResponsesModel(api_key="test", session=session)
    list(model.call_stream(_Request([{"role": "user", "content": "original"}])))
    replacement = _Request([{"role": "user", "content": "compacted history"}])
    session.fail_next = True
    with pytest.raises(requests.Timeout):
        list(model.call_stream(replacement))

    list(model.call_stream(replacement))
    assert "previous_response_id" not in session.payloads[-1]
    assert session.payloads[-1]["input"] == cow_messages_to_responses(replacement.messages)
    assert model.request_attempt_count == 3
    assert model.request_count == 2


def test_model_records_failed_transport_attempts() -> None:
    class TimeoutSession:
        def post(self, _url, **_kwargs):
            raise __import__("requests").Timeout("timed out")

    model = OpenAIResponsesModel(api_key="test", session=TimeoutSession())

    with pytest.raises(__import__("requests").Timeout):
        list(model.call_stream(_Request([{"role": "user", "content": "open it"}])))

    assert model.request_count == 0
    assert model.request_attempt_count == 1
    assert model.transport_errors == [{"type": "Timeout", "message": "timed out"}]


def test_model_records_http_error_attempts() -> None:
    requests = __import__("requests")

    class ErrorResponse:
        def raise_for_status(self):
            raise requests.HTTPError("503 Service Unavailable")

    class HttpErrorSession:
        def post(self, _url, **_kwargs):
            return ErrorResponse()

    model = OpenAIResponsesModel(api_key="test", session=HttpErrorSession())

    with pytest.raises(requests.HTTPError):
        list(model.call_stream(_Request([{"role": "user", "content": "open it"}])))

    assert model.request_count == 0
    assert model.request_attempt_count == 1
    assert model.transport_errors == [{"type": "HTTPError", "message": "503 Service Unavailable"}]


def test_model_access_preflight_checks_exact_model() -> None:
    model = OpenAIResponsesModel(api_key="test", session=_Session())

    model.verify_access()


def test_model_access_preflight_supports_filtered_collection_gateway() -> None:
    class GatewaySession:
        def get(self, url, **_kwargs):
            if url.endswith("/models/gpt-5.6-sol"):
                return _Response({"error": "route disabled"}, status_code=403)
            return _Response({"data": [{"id": "gpt-5.6-sol"}]})

    model = OpenAIResponsesModel(api_key="test", model="gpt-5.6-sol", session=GatewaySession())

    model.verify_access()


def test_model_access_preflight_rejects_missing_gateway_model() -> None:
    class GatewaySession:
        def get(self, url, **_kwargs):
            if url.endswith("/models/gpt-6-astra"):
                return _Response({"error": "not allowed"}, status_code=403)
            return _Response({"data": [{"id": "gpt-5.6-sol"}]})

    model = OpenAIResponsesModel(api_key="test", session=GatewaySession())

    with pytest.raises(PermissionError, match="does not expose"):
        model.verify_access()
