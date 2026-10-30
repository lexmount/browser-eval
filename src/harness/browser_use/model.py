"""OpenAI Responses transport adapter for CowAgent's model protocol."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import requests


def _text_from_blocks(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def cow_messages_to_responses(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate CowAgent's Claude-shaped history to Responses input items."""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        text = _text_from_blocks(content)
        if text:
            items.append({"role": role, "content": text})
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(block.get("id") or ""),
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(
                            block.get("input") or {}, ensure_ascii=False, separators=(",", ":")
                        ),
                    }
                )
            elif block.get("type") == "tool_result":
                output = block.get("content")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(block.get("tool_use_id") or ""),
                        "output": output,
                    }
                )
    return items


def cow_tools_to_responses(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": str(tool["name"]),
            "description": str(tool.get("description") or ""),
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            "strict": False,
        }
        for tool in tools or []
    ]


def response_to_chat_chunk(response: dict[str, Any]) -> dict[str, Any]:
    content: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in response.get("output") or []:
        if item.get("type") == "message":
            for block in item.get("content") or []:
                if block.get("type") == "output_text" and block.get("text"):
                    content.append(str(block["text"]))
        elif item.get("type") == "function_call":
            calls.append(
                {
                    "index": len(calls),
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }
            )
    delta: dict[str, Any] = {"content": "".join(content)}
    if calls:
        delta["tool_calls"] = calls
    return {
        "choices": [
            {
                "delta": delta,
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": response.get("usage"),
        "response_id": response.get("id"),
    }


class OpenAIResponsesModel:
    """Minimal adapter: CowAgent loop and tools stay unchanged; only transport differs."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-6-astra",
        reasoning_effort: str = "low",
        timeout_seconds: float = 180,
        session: requests.Session | None = None,
    ) -> None:
        if reasoning_effort != "low":
            raise ValueError("pilot protocol fixes OpenAI reasoning effort to low")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.last_response: dict[str, Any] | None = None
        self._previous_response_id: str | None = None
        self._seen_messages: tuple[str, ...] = ()
        self.usage: dict[str, int] = {}
        self.request_count = 0
        self.request_attempt_count = 0
        self.transport_errors: list[dict[str, str]] = []

    def reset(self) -> None:
        """Start a fresh Responses conversation for the next benchmark attempt."""
        self.last_response = None
        self._previous_response_id = None
        self._seen_messages = ()
        self.usage = {}
        self.request_count = 0
        self.request_attempt_count = 0
        self.transport_errors = []

    def verify_access(self) -> None:
        """Fail before benchmark attempts if the configured project cannot access the model."""
        response = self.session.get(
            f"{self.base_url}/models/{self.model}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        if response.status_code < 400:
            payload = response.json()
            if payload.get("id") != self.model:
                raise ValueError(f"model lookup returned an unexpected id: {payload.get('id')!r}")
            return
        if response.status_code not in {403, 404, 405}:
            response.raise_for_status()

        # Some OpenAI-compatible gateways expose a team-filtered collection but
        # disable the single-model route. The collection remains a read-only
        # access preflight and avoids spending a benchmark model call.
        collection = self.session.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        collection.raise_for_status()
        ids = {
            str(item.get("id"))
            for item in collection.json().get("data") or []
            if isinstance(item, dict) and item.get("id")
        }
        if self.model not in ids:
            raise PermissionError(f"configured API project does not expose model {self.model!r}")

    def call_stream(self, request: Any) -> Iterable[dict[str, Any]]:
        messages = list(request.messages or [])
        # CowAgent can compact or rewrite history in place. Keep an immutable
        # content snapshot: a message count cannot establish a shared prefix.
        message_snapshot = tuple(
            json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for message in messages
        )
        continues_history = bool(self._previous_response_id) and (
            message_snapshot[: len(self._seen_messages)] == self._seen_messages
        )
        if continues_history:
            new_messages = messages[len(self._seen_messages) :]
            input_items = cow_messages_to_responses(
                [message for message in new_messages if message.get("role") != "assistant"]
            )
        else:
            input_items = cow_messages_to_responses(messages)
            # A replay has no server-side calls to satisfy orphan outputs. Reject
            # malformed compaction locally instead of losing observations or
            # attaching them to the conversation that was explicitly replaced.
            pending_calls: set[str] = set()
            for item in input_items:
                if item.get("type") == "function_call":
                    pending_calls.add(item["call_id"])
                elif item.get("type") == "function_call_output":
                    call_id = item["call_id"]
                    if not call_id or call_id not in pending_calls:
                        raise ValueError(
                            f"tool output {call_id!r} has no matching preceding call in history"
                        )
                    pending_calls.remove(call_id)
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": str(getattr(request, "system", "") or ""),
            "input": input_items,
            "reasoning": {"effort": self.reasoning_effort},
            "tools": cow_tools_to_responses(request.tools),
        }
        if continues_history:
            payload["previous_response_id"] = self._previous_response_id
        if getattr(request, "max_tokens", None):
            payload["max_output_tokens"] = int(request.max_tokens)
        self.request_attempt_count += 1
        try:
            response = self.session.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            self.transport_errors.append({"type": type(exc).__name__, "message": str(exc)[:500]})
            raise
        self.last_response = response.json()
        response_id = self.last_response.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise ValueError("OpenAI Responses API success payload omitted a non-empty id")
        if not isinstance(self.last_response.get("output"), list):
            raise ValueError("OpenAI Responses API success payload omitted the output list")
        self.request_count += 1
        usage = self.last_response.get("usage") or {}
        details = usage.get("output_tokens_details") or {}
        for key, value in (
            ("input_tokens", usage.get("input_tokens")),
            ("output_tokens", usage.get("output_tokens")),
            ("reasoning_tokens", details.get("reasoning_tokens")),
            ("total_tokens", usage.get("total_tokens")),
        ):
            self.usage[key] = self.usage.get(key, 0) + int(value or 0)
        self._previous_response_id = response_id
        self._seen_messages = message_snapshot
        yield response_to_chat_chunk(self.last_response)
