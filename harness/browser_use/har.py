"""Browser-neutral CDP network capture emitted as evaluator-compatible HAR."""

from __future__ import annotations

import base64
import json
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

_REDACTED = "[REDACTED]"
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-m2-admin-auto-login",
        "x-m2-customer-auto-login",
        "x-postmill-auto-login",
    }
)
# WebArena-Verified v1.2.3 has five shopping evaluators that inspect the
# non-authentication Magento flash-message cookie.  Keep only that explicit
# value in the parsed HAR cookie array; the raw Set-Cookie header and every
# authentication/session cookie remain redacted.
_EVALUATOR_SAFE_RESPONSE_COOKIES = frozenset({"mage-messages"})
_EVALUATOR_SAFE_REQUEST_COOKIES = frozenset({"_osm_directions_engine"})
_EVALUATOR_PARSEABLE_BODY_TYPES = (
    "application/json",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
)
HAR_CREATOR_NAME = "browser-eval-cdp"
HAR_CREATOR_VERSION = "3"


def _redacted_request_cookie_header(value: Any) -> str:
    parsed = SimpleCookie()
    try:
        parsed.load(str(value))
    except Exception:
        return _REDACTED
    safe = [
        f"{name}={morsel.value}"
        for name, morsel in parsed.items()
        if name.lower() in _EVALUATOR_SAFE_REQUEST_COOKIES
    ]
    if not safe:
        return _REDACTED
    if len(safe) != len(parsed):
        safe.append(_REDACTED)
    return "; ".join(safe)


def _headers(values: dict[str, Any] | None) -> list[dict[str, str]]:
    return [
        {
            "name": str(name),
            "value": (
                _redacted_request_cookie_header(value)
                if str(name).lower() == "cookie"
                else _REDACTED
                if str(name).lower() in _SENSITIVE_HEADERS
                else str(value)
            ),
        }
        for name, value in (values or {}).items()
    ]


def _merged_headers(
    base: dict[str, Any] | None, extra: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge CDP ExtraInfo headers case-insensitively, preferring full values."""
    merged: dict[str, Any] = dict(base or {})
    names = {str(name).lower(): name for name in merged}
    for name, value in (extra or {}).items():
        previous = names.get(str(name).lower())
        if previous is not None and previous != name:
            merged.pop(previous, None)
        merged[str(name)] = value
        names[str(name).lower()] = str(name)
    return merged


def _header(values: dict[str, Any] | None, name: str) -> str:
    return next(
        (str(value) for key, value in (values or {}).items() if str(key).lower() == name.lower()),
        "",
    )


def _post_data(request: dict[str, Any]) -> dict[str, str] | None:
    text = request.get("postData")
    if text is None:
        return None
    headers = {
        str(key).lower(): str(value) for key, value in (request.get("headers") or {}).items()
    }
    return {"mimeType": headers.get("content-type", "application/octet-stream"), "text": str(text)}


def _response_cookies(
    *sources: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Preserve cookies from base and ExtraInfo headers; later values win by name."""
    cookies: dict[str, str] = {}
    for values in sources:
        header = next(
            (
                str(value)
                for key, value in (values or {}).items()
                if str(key).lower() == "set-cookie"
            ),
            "",
        )
        if not header:
            continue
        parsed = SimpleCookie()
        try:
            parsed.load(header)
        except Exception:
            continue
        for name, morsel in parsed.items():
            cookies[name] = (
                morsel.value
                if name.lower() in _EVALUATOR_SAFE_RESPONSE_COOKIES
                else _REDACTED
            )
    return [{"name": name, "value": value} for name, value in cookies.items()]


class CdpHarRecorder:
    """Capture the same CDP events from both engines and fail on missing essentials."""

    def __init__(self, page: Any) -> None:
        self._context = page.context
        self._sessions: dict[int, Any] = {}
        self._attached_pages: set[int] = set()
        self._requests: dict[str, dict[str, Any]] = {}
        self._responses: dict[str, dict[str, Any]] = {}
        self._request_extra_info: dict[str, dict[str, Any]] = {}
        self._response_extra_info: dict[str, dict[str, Any]] = {}
        self._redirects: list[dict[str, Any]] = []
        self._finished: set[str] = set()
        self._failed: set[str] = set()
        for existing in list(self._context.pages):
            self.attach(existing)
        self._context.on("page", self.attach)

    @staticmethod
    def _key(session_id: int, event: dict[str, Any]) -> str:
        return f"{session_id}:{event['requestId']}"

    def attach(self, page: Any) -> None:
        """Idempotently enable Network events before the page's measured action."""
        page_id = id(page)
        if page_id in self._attached_pages:
            return
        session_id = len(self._sessions)
        session = self._context.new_cdp_session(page)
        self._attached_pages.add(page_id)
        self._sessions[session_id] = session
        session.on(
            "Network.requestWillBeSent",
            lambda event: self._record_request(session_id, event),
        )
        session.on(
            "Network.responseReceived",
            lambda event: self._responses.__setitem__(self._key(session_id, event), event),
        )
        session.on(
            "Network.requestWillBeSentExtraInfo",
            lambda event: self._request_extra_info.__setitem__(
                self._key(session_id, event), event
            ),
        )
        session.on(
            "Network.responseReceivedExtraInfo",
            lambda event: self._response_extra_info.__setitem__(
                self._key(session_id, event), event
            ),
        )
        session.on(
            "Network.loadingFinished",
            lambda event: self._finished.add(self._key(session_id, event)),
        )
        session.on(
            "Network.loadingFailed",
            lambda event: self._failed.add(self._key(session_id, event)),
        )
        session.send("Network.enable", {"maxTotalBufferSize": 100_000_000})

    def _record_request(self, session_id: int, event: dict[str, Any]) -> None:
        """Preserve every hop when CDP reuses a request id for redirects."""
        key = self._key(session_id, event)
        previous = self._requests.get(key)
        redirect_response = event.get("redirectResponse")
        if previous is not None and isinstance(redirect_response, dict):
            self._redirects.append(
                {
                    "session_id": session_id,
                    "request_id": str(event["requestId"]),
                    "request_event": previous,
                    "response_event": {
                        "requestId": event["requestId"],
                        "response": redirect_response,
                    },
                    "request_extra_event": self._request_extra_info.pop(key, None),
                    "response_extra_event": self._response_extra_info.pop(key, None),
                }
            )
            self._responses.pop(key, None)
            self._finished.discard(key)
            self._failed.discard(key)
        self._requests[key] = event

    def ensure_pages(self) -> None:
        """Attach pages discovered after recorder construction.

        Playwright's context ``page`` event may be dispatched after the caller has
        already received a newly created page.  Executors should call this before
        the first measured action on a tab so its initial request is not missed.
        """
        for page in list(self._context.pages):
            self.attach(page)

    def _unfinished_keys(self) -> set[str]:
        return set(self._requests).difference(self._finished, self._failed)

    def _drain(self, timeout_ms: int) -> None:
        """Pump CDP events briefly so evaluator-relevant response bodies can finish.

        Some applications keep long-lived requests open, so this is deliberately
        bounded.  A zero timeout preserves the immediate-snapshot behavior for
        callers that explicitly need it.
        """
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while self._unfinished_keys() and time.monotonic() < deadline:
            pages = list(self._context.pages)
            if not pages or not hasattr(pages[0], "wait_for_timeout"):
                return
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            pages[0].wait_for_timeout(min(50, remaining_ms))

    def stop(self, path: str | Path, *, drain_timeout_ms: int = 1_000) -> dict[str, Any]:
        self.ensure_pages()
        self._drain(drain_timeout_ms)
        self._context.remove_listener("page", self.attach)
        requests = dict(self._requests)
        responses = dict(self._responses)
        request_extra_info = dict(self._request_extra_info)
        response_extra_info = dict(self._response_extra_info)
        finished = set(self._finished)
        failed = set(self._failed)
        sessions = dict(self._sessions)
        entries: list[dict[str, Any]] = [
            self._entry(
                sessions[item["session_id"]],
                item["request_id"],
                False,
                item["request_event"],
                item["response_event"],
                item["request_extra_event"],
                item["response_extra_event"],
            )
            for item in self._redirects
        ]
        missing_response = 0
        for key, event in requests.items():
            response_event = responses.get(key)
            if response_event is None:
                if key not in failed:
                    missing_response += 1
                continue
            session_id_text, request_id = key.split(":", 1)
            entry = self._entry(
                sessions[int(session_id_text)],
                request_id,
                key in finished,
                event,
                response_event,
                request_extra_info.get(key),
                response_extra_info.get(key),
            )
            entries.append(entry)
        for session in sessions.values():
            try:
                session.send("Network.disable")
            finally:
                session.detach()
        payload = {
            "log": {
                "version": "1.2",
                "creator": {"name": HAR_CREATOR_NAME, "version": HAR_CREATOR_VERSION},
                "entries": entries,
            }
        }
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return {
            "entry_count": len(entries),
            "request_count": len(requests) + len(self._redirects),
            "missing_response_count": missing_response,
            "failed_request_count": len(failed),
            "unfinished_request_count": len(set(requests).difference(finished, failed)),
        }

    def _entry(
        self,
        session: Any,
        request_id: str,
        finished: bool,
        request_event: dict[str, Any],
        response_event: dict[str, Any],
        request_extra_event: dict[str, Any] | None,
        response_extra_event: dict[str, Any] | None,
    ) -> dict[str, Any]:
        request = request_event["request"]
        response = response_event["response"]
        request_headers = _merged_headers(
            request.get("headers"),
            (request_extra_event or {}).get("headers"),
        )
        response_headers = _merged_headers(
            response.get("headers"),
            (response_extra_event or {}).get("headers"),
        )
        post_data = _post_data({**request, "headers": request_headers})
        response_body: dict[str, Any] = {}
        if finished:
            try:
                response_body = session.send("Network.getResponseBody", {"requestId": request_id})
            except Exception:
                response_body = {}
        body = str(response_body.get("body") or "")
        mime_type = str(response.get("mimeType") or "")
        content: dict[str, Any] = {
            "size": int(response.get("encodedDataLength") or len(body)),
            "mimeType": mime_type,
        }
        if body and any(kind in mime_type.lower() for kind in _EVALUATOR_PARSEABLE_BODY_TYPES):
            content["text"] = body
            if response_body.get("base64Encoded"):
                content["encoding"] = "base64"
                try:
                    content["size"] = len(base64.b64decode(body))
                except ValueError:
                    pass
        split = urlsplit(str(request["url"]))
        started = float(request_event.get("wallTime") or time.time())
        return {
            "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "time": 0,
            "request": {
                "method": str(request["method"]),
                "url": str(request["url"]),
                "httpVersion": "HTTP/1.1",
                "headers": _headers(request_headers),
                "queryString": [
                    {"name": name, "value": value}
                    for name, value in parse_qsl(split.query, keep_blank_values=True)
                ],
                "cookies": [],
                "headersSize": -1,
                "bodySize": len(str(request.get("postData") or "")),
                **({"postData": post_data} if post_data else {}),
            },
            "response": {
                "status": int(response["status"]),
                "statusText": str(response.get("statusText") or ""),
                "httpVersion": str(response.get("protocol") or "HTTP/1.1"),
                "headers": _headers(response_headers),
                "cookies": _response_cookies(
                    response.get("headers"),
                    (response_extra_event or {}).get("headers"),
                ),
                "content": content,
                "redirectURL": _header(response_headers, "location"),
                "headersSize": -1,
                "bodySize": int(response.get("encodedDataLength") or -1),
            },
            "cache": {},
            "timings": {"send": 0, "wait": 0, "receive": 0},
        }
