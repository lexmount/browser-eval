"""Browserbase's hosted Stagehand extraction API, including native inference."""

import json
import os
import time

from harness.web_fetch.providers.http_client import request

BASE = "https://api.stagehand.browserbase.com/v1"
MODEL = "openai/gpt-4.1"
INSTRUCTION = (
    "Extract the main product current price, SKU and stock availability. "
    "Use null for absent fields."
)


def decode_response(response):
    """Keep the complete SSE payload while selecting only its completion event."""
    text = response.text()
    if not any(line.startswith("data:") for line in text.splitlines()):
        try:
            return response.json(), []
        except ValueError:
            return {"success": False, "raw_response": text}, []
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
        )
        if not data:
            continue
        event = json.loads(data)
        events.append(event)
        if event.get("type") == "system":
            value = event.get("data", {})
            if value.get("status") == "error":
                return {"success": False, "message": value.get("error")}, events
            if value.get("status") == "finished":
                return {"success": True, "result": value.get("result")}, events
    raise ValueError("Stagehand stream ended without a completion event")


def extract(url, schema, *, timeout=120):
    headers = {
        "x-bb-api-key": os.environ["BROWSERBASE_API_KEY"],
        "x-bb-project-id": os.environ["BROWSERBASE_PROJECT_ID"],
        "x-stream-response": "true",
        "browserbase-cache-bypass": "true",
    }
    calls = []
    session = None
    phase = "session_setup"

    def call(path, body):
        start = time.monotonic()
        receipt = {"phase": phase}
        calls.append(receipt)
        try:
            response = request(
                BASE + path,
                method="POST",
                headers=headers,
                json_body=body,
                timeout=timeout,
                max_bytes=10_000_000,
            )
            receipt.update(http_status=response.status, raw_response=response.text())
            payload, events = decode_response(response)
            receipt.update(response=payload, events=events)
        except Exception as error:
            receipt["error"] = str(error)
            raise
        finally:
            receipt["elapsed_seconds"] = time.monotonic() - start
        if response.status != 200 or payload.get("success") is False:
            return payload, response.status, False
        return payload, response.status, True

    try:
        payload, status, ok = call(
            "/sessions/start",
            {
                "modelName": MODEL,
                "browserbaseSessionCreateParams": {
                    "projectId": os.environ["BROWSERBASE_PROJECT_ID"],
                    "timeout": timeout,
                    "keepAlive": False,
                    "browserSettings": {"solveCaptchas": False},
                },
            },
        )
        if ok:
            session = payload.get("data", {}).get("sessionId")
            if not session:
                raise ValueError("Stagehand returned no native session ID")
            headers["x-bb-session-id"] = session
            phase = "snapshot_navigation"
            payload, status, ok = call(
                f"/sessions/{session}/navigate",
                {
                    "url": url,
                    "options": {"waitUntil": "domcontentloaded", "timeoutMs": 60000},
                },
            )
        if ok:
            phase = "field_extraction"
            payload, status, ok = call(
                f"/sessions/{session}/extract",
                {
                    "instruction": INSTRUCTION,
                    "schema": schema,
                    "options": {"timeout": timeout * 1000},
                },
            )
        return {
            "state": "response_received" if ok else "provider_error",
            "http_status": status,
            "failure_phase": None if ok else phase,
            "error": "" if ok else str(payload.get("message") or payload.get("error") or payload),
            "response": {"result": payload.get("result") if ok else None, "calls": calls},
            "extractor": {"api": BASE, "model": MODEL, "inference": "native Model Gateway"},
        }
    except Exception as error:
        return {
            "state": "call_error",
            "http_status": calls[-1].get("http_status") if calls else None,
            "failure_phase": phase,
            "error": str(error),
            "response": {"result": None, "calls": calls},
            "extractor": {"api": BASE, "model": MODEL, "inference": "native Model Gateway"},
        }
    finally:
        if session:
            phase = "session_cleanup"
            try:
                call(f"/sessions/{session}/end", {})
            except Exception:
                pass  # call() already retained the failed cleanup attempt.
