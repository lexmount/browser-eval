"""CDP network recorder that writes evaluator-compatible HAR."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any


def _headers(values: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": str(name), "value": str(value)} for name, value in values.items()]


def _har_request(request: dict[str, Any]) -> dict[str, Any]:
    headers = request.get("headers", {})
    result: dict[str, Any] = {
        "method": str(request["method"]),
        "url": str(request["url"]),
        "headers": _headers(headers),
        "cookies": [],
    }
    if request.get("postData") is not None:
        content_type = next(
            (str(value) for name, value in headers.items() if name.lower() == "content-type"),
            "",
        )
        result["postData"] = {"mimeType": content_type, "text": str(request["postData"])}
    return result


def _har_response(response: dict[str, Any], body: dict[str, Any] | None) -> dict[str, Any]:
    headers = response.get("headers", {})
    mime_type = str(response.get("mimeType") or "")
    content: dict[str, Any] = {"mimeType": mime_type, "text": ""}
    if body is not None:
        content["text"] = str(body.get("body") or "")
        if body.get("base64Encoded"):
            content["encoding"] = "base64"
    return {
        "status": int(response["status"]),
        "statusText": str(response.get("statusText") or ""),
        "headers": _headers(headers),
        "cookies": [],
        "redirectURL": next(
            (str(value) for name, value in headers.items() if name.lower() == "location"),
            "",
        ),
        "content": content,
    }


class CdpHarRecorder:
    """Capture raw CDP requests from an existing browser context.

    Playwright trace ZIPs from an attached CDP browser can omit multipart POST
    bodies. Raw ``Network.requestWillBeSent`` events retain those bodies, which
    the official WebArena-Verified mutation evaluators require.
    """

    def __init__(self) -> None:
        self._context: Any = None
        self._sessions: list[Any] = []
        self._requests: dict[tuple[int, str], dict[str, Any]] = {}
        self._responses: list[tuple[Any, str, dict[str, Any], dict[str, Any]]] = []
        self._finished: set[tuple[int, str]] = set()
        self.errors: list[str] = []

    def start(self, context: Any) -> None:
        self._context = context
        context.on("page", self._attach)
        for page in context.pages:
            self._attach(page)

    def stop(self, context: Any, path: Path) -> None:
        if self._context is not None:
            context.remove_listener("page", self._attach)
        entries: list[dict[str, Any]] = []
        last_response_by_key = {
            (id(session), request_id): index
            for index, (session, request_id, _request, _response) in enumerate(self._responses)
        }
        for index, (session, request_id, request, response) in enumerate(self._responses):
            body = None
            key = (id(session), request_id)
            mime_type = str(response.get("mimeType") or "")
            if (
                index == last_response_by_key[key]
                and key in self._finished
                and (mime_type == "application/json" or mime_type.endswith("+json"))
            ):
                try:
                    body = session.send("Network.getResponseBody", {"requestId": request_id})
                    if body.get("base64Encoded"):
                        base64.b64decode(body.get("body") or "", validate=True)
                except Exception as exc:
                    self.errors.append(f"{request.get('url')}: response body unavailable: {exc}")
            entries.append(
                {"request": _har_request(request), "response": _har_response(response, body)}
            )
        for session in self._sessions:
            try:
                session.detach()
            except Exception as exc:
                self.errors.append(f"CDP detach failed: {exc}")
        self._context = None
        path.write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "browser-eval", "version": "1"},
                        "entries": entries,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _attach(self, page: Any) -> None:
        try:
            assert self._context is not None
            session = self._context.new_cdp_session(page)
            session_index = id(session)
            session.on(
                "Network.requestWillBeSent",
                lambda event: self._record_request(session, session_index, event),
            )
            session.on(
                "Network.responseReceived",
                lambda event: self._record_response(session, session_index, event),
            )
            session.on(
                "Network.loadingFinished",
                lambda event: self._finished.add((session_index, str(event["requestId"]))),
            )
            session.send("Network.enable")
            self._sessions.append(session)
        except Exception as exc:
            self.errors.append(f"CDP attach failed: {exc}")

    def _record_request(self, session: Any, session_index: int, event: dict[str, Any]) -> None:
        request_id = str(event["requestId"])
        key = (session_index, request_id)
        previous = self._requests.get(key)
        redirect_response = event.get("redirectResponse")
        if previous is not None and redirect_response is not None:
            self._responses.append((session, request_id, dict(previous), redirect_response))
        self._requests[key] = event["request"]

    def _record_response(self, session: Any, session_index: int, event: dict[str, Any]) -> None:
        request_id = str(event["requestId"])
        request = self._requests.get((session_index, request_id))
        if request is not None:
            self._responses.append((session, request_id, dict(request), event["response"]))
