"""CDP network recorder that writes evaluator-compatible HAR."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any


def _headers(values: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": str(name), "value": str(value)} for name, value in values.items()]


def _har_request(request: dict[str, Any]) -> dict[str, Any]:
    headers = dict(request.get("headers", {}))
    if request.get("__resource_type") == "Document" and not any(
        name.lower() == "accept" for name in headers
    ):
        # CDP marks the request as a top-level document even when a browser
        # implementation omits default Accept headers from requestWillBeSent.
        headers["Accept"] = "text/html"
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


def _response_cookies(
    base_headers: dict[str, Any] | None, extra_headers: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Expose only the Magento flash cookie needed by official evaluators."""
    cookies: dict[str, str] = {}
    for headers in (base_headers, extra_headers):
        raw = next(
            (
                str(value)
                for name, value in (headers or {}).items()
                if str(name).lower() == "set-cookie"
            ),
            "",
        )
        if not raw:
            continue
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except Exception:
            continue
        if "mage-messages" in parsed:
            cookies["mage-messages"] = parsed["mage-messages"].value
    return [{"name": name, "value": value} for name, value in cookies.items()]


def _har_response(
    response: dict[str, Any],
    body: dict[str, Any] | None,
    extra_headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = response.get("headers", {})
    mime_type = str(response.get("mimeType") or "")
    content: dict[str, Any] = {"mimeType": mime_type, "text": ""}
    if body is not None:
        content["text"] = str(body.get("body") or "")
    return {
        "status": int(response["status"]),
        "statusText": str(response.get("statusText") or ""),
        "headers": _headers(headers),
        "cookies": _response_cookies(headers, extra_headers),
        "redirectURL": next(
            (str(value) for name, value in headers.items() if name.lower() == "location"),
            "",
        ),
        "content": content,
    }


def _is_json_mime_type(value: Any) -> bool:
    """Match the JSON MIME essence without mistaking parameters for a subtype."""

    essence = str(value or "").split(";", 1)[0].strip().lower()
    return essence == "application/json" or ("/" in essence and essence.endswith("+json"))


class CdpHarRecorder:
    """Capture raw CDP requests from an existing browser context.

    Playwright trace ZIPs from an attached CDP browser can omit multipart POST
    bodies. Raw ``Network.requestWillBeSent`` events retain those bodies, which
    the official WebArena-Verified mutation evaluators require.
    """

    _DOCUMENT_DRAIN_TIMEOUT_MS = 10_000
    # JSON may trigger navigation before loadingFinished's client-side callback
    # can read it. Opt into bounded cross-document retention explicitly; neither
    # browser defaults nor a later retry guarantee that the body still exists.
    _NETWORK_CAPTURE_OPTIONS = {
        "enableDurableMessages": True,
        "maxTotalBufferSize": 10 * 1024 * 1024,
        "maxResourceBufferSize": 1024 * 1024,
    }

    def __init__(self) -> None:
        self._context: Any = None
        self._sessions: list[Any] = []
        self._requests: dict[tuple[int, str], dict[str, Any]] = {}
        self._request_ordinals: dict[tuple[int, str], int] = {}
        self._next_request_sequence = 0
        self._session_ordinals: dict[int, int] = {}
        self._completion_timestamps: dict[tuple[int, str, int], Any] = {}
        self._extra_headers: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self._extra_response_headers: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self._responses: list[tuple[Any, str, dict[str, Any], dict[str, Any]]] = []
        self._finished: set[tuple[int, str]] = set()
        self._failed: dict[tuple[int, str], dict[str, Any]] = {}
        self._request_sessions: dict[tuple[int, str], Any] = {}
        self._session_pages: dict[int, Any] = {}
        self._response_bodies: dict[tuple[int, str, int], dict[str, Any]] = {}
        self._response_body_errors: dict[tuple[int, str, int], str] = {}
        self._payload: str | None = None
        self._stopped = False
        self.errors: list[str] = []

    def start(self, context: Any) -> None:
        self._context = context
        context.on("page", self._attach)
        for page in context.pages:
            self._attach(page)

    def stop(self, context: Any, path: Path) -> None:
        if self._stopped:
            return
        if self._payload is None:
            # Network events and command responses share the ordered CDP channel.
            # A command round trip pumps Playwright's sync dispatcher without
            # executing page JavaScript, so events already emitted by the browser
            # are visible before the evidence boundary is chosen.
            self._protocol_barrier()
            boundary_responses = list(self._responses)
            boundary_requests = {
                (*key, int(request.get("__ordinal", 0))) for key, request in self._requests.items()
            }
            boundary_requests.update(
                (id(session), request_id, int(request.get("__ordinal", 0)))
                for session, request_id, request, _response in boundary_responses
            )
            eligible_documents = self._unfinished_document_keys()
            self._drain_documents(eligible_documents)
            self._protocol_barrier()
            if self._context is not None:
                try:
                    context.remove_listener("page", self._attach)
                except Exception as exc:
                    self.errors.append(f"page listener detach failed: {exc}")
            entries: list[dict[str, Any]] = []
            # No Playwright call occurs while these lifecycle views are copied.
            # Body fallback below may pump more callbacks, but those events belong
            # after this immutable evidence boundary and cannot mix with it.
            requests_snapshot = {key: dict(request) for key, request in self._requests.items()}
            responses_snapshot = list(self._responses)
            finished_snapshot = set(self._finished)
            failed_snapshot = {key: dict(event) for key, event in self._failed.items()}
            bodies_snapshot = dict(self._response_bodies)
            body_errors_snapshot = dict(self._response_body_errors)
            completion_snapshot = dict(self._completion_timestamps)
            extra_headers_snapshot = {
                key: [dict(headers) for headers in values]
                for key, values in self._extra_headers.items()
            }
            extra_response_headers_snapshot = {
                key: [dict(headers) for headers in values]
                for key, values in self._extra_response_headers.items()
            }
            responses = [
                observation
                for observation in responses_snapshot
                if self._observation_is_within_boundary(
                    observation[0],
                    observation[1],
                    observation[2],
                    boundary_requests,
                    eligible_documents,
                )
            ]
            represented = {
                (id(session), request_id, int(request.get("__ordinal", 0)))
                for session, request_id, request, _response in responses
            }
            # A request is evaluator evidence even when finalization races its
            # response.  Retain requestWillBeSent-only observations with a HAR
            # status of zero instead of silently dropping the mutation.
            pending_requests: list[tuple[Any, str, dict[str, Any], None]] = []
            for key, request in requests_snapshot.items():
                ordinal = int(request.get("__ordinal", 0))
                if (*key, ordinal) not in boundary_requests and key not in eligible_documents:
                    continue
                if (*key, ordinal) in represented:
                    continue
                session = self._request_sessions.get(key)
                if session is not None:
                    pending_requests.append((session, key[1], dict(request), None))
            unordered_observations = [*responses, *pending_requests]
            observations = [
                observation
                for _index, observation in sorted(
                    enumerate(unordered_observations),
                    key=lambda indexed: self._observation_order(*indexed),
                )
            ]
            last_response_by_key = {
                (id(session), request_id): index
                for index, (session, request_id, _request, _response) in enumerate(observations)
            }
            for index, (session, request_id, request, response) in enumerate(observations):
                key = (id(session), request_id)
                request = dict(request)
                ordinal = int(request.pop("__ordinal", 0))
                request.pop("__sequence", None)
                body_key = (*key, ordinal)
                body = bodies_snapshot.get(body_key)
                extras = extra_headers_snapshot.get(key, [])
                if ordinal < len(extras):
                    request["headers"] = {**request.get("headers", {}), **extras[ordinal]}
                response_extras = extra_response_headers_snapshot.get(key, [])
                response_extra = (
                    response_extras[ordinal] if ordinal < len(response_extras) else None
                )
                mime_type = str((response or {}).get("mimeType") or "")
                if (
                    index == last_response_by_key[key]
                    and key in finished_snapshot
                    and response is not None
                    and body is None
                    and _is_json_mime_type(mime_type)
                ):
                    try:
                        body = session.send("Network.getResponseBody", {"requestId": request_id})
                        body = self._decode_body(body)
                    except Exception as exc:
                        prior = body_errors_snapshot.get(body_key)
                        detail = f"{prior}; stop fallback: {exc}" if prior else str(exc)
                        self.errors.append(
                            f"{request.get('url')}: response body unavailable: {detail}"
                        )
                        body = None
                entry = {
                    "request": _har_request(request),
                    "response": (
                        _har_response(response, body, response_extra)
                        if response is not None
                        else {
                            "status": 0,
                            "statusText": "",
                            "headers": [],
                            "cookies": [],
                            "redirectURL": "",
                            "content": {"mimeType": "", "text": ""},
                        }
                    ),
                }
                # Keep raw CDP clocks distinct: timestamp is monotonic seconds,
                # wallTime is epoch seconds; ResourceTiming offsets are milliseconds.
                # Redirect observation is not a fabricated responseReceived event.
                timing = {**request.get("__cdp", {}), **(response or {}).get("__cdp", {})}
                if response is not None and "timing" in response:
                    timing["responseTiming"] = response["timing"]
                if response is not None and "responseTime" in response:
                    timing["responseTime"] = response["responseTime"]
                completion_key = (id(session), request_id, ordinal)
                if completion_key in completion_snapshot:
                    timing["loadingFinishedTimestamp"] = completion_snapshot[completion_key]
                if timing:
                    entry["_cdp"] = timing
                current = requests_snapshot.get(key)
                is_current = current is not None and int(current.get("__ordinal", 0)) == ordinal
                if is_current and key in failed_snapshot:
                    failure = failed_snapshot[key]
                    entry["_capture"] = {
                        "state": "failed",
                        **(
                            {"errorText": str(failure["errorText"])}
                            if failure.get("errorText") is not None
                            else {}
                        ),
                    }
                elif is_current and key not in finished_snapshot:
                    entry["_capture"] = {
                        "state": "incomplete",
                        "reason": "response-not-terminal-at-finalization",
                    }
                if "requestWallTime" in timing:
                    try:
                        entry["startedDateTime"] = (
                            datetime.fromtimestamp(timing["requestWallTime"], tz=UTC)
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                    except (ValueError, TypeError, OverflowError, OSError):
                        # Retain raw evidence even if the supplied wall clock is invalid.
                        pass
                entries.append(entry)
            for session in self._sessions:
                try:
                    session.detach()
                except Exception as exc:
                    self.errors.append(f"CDP detach failed: {exc}")
            self._context = None
            self._payload = json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "browser-eval", "version": "1"},
                        "entries": entries,
                    }
                },
                ensure_ascii=False,
            )
        path.write_text(self._payload, encoding="utf-8")
        self._stopped = True

    @staticmethod
    def _observation_order(
        index: int,
        observation: tuple[Any, str, dict[str, Any], dict[str, Any] | None],
    ) -> tuple[int, int]:
        sequence = observation[2].get("__sequence")
        if sequence is None:
            # Compatibility for synthetic callers that populate _responses
            # directly; real CDP observations always carry a request sequence.
            return (2**63 - 1, index)
        return (int(sequence), index)

    @staticmethod
    def _observation_is_within_boundary(
        session: Any,
        request_id: str,
        request: dict[str, Any],
        boundary_requests: set[tuple[int, str, int]],
        eligible_documents: set[tuple[int, str]],
    ) -> bool:
        key = (id(session), request_id)
        ordinal = int(request.get("__ordinal", 0))
        return (*key, ordinal) in boundary_requests or key in eligible_documents

    def _protocol_barrier(self) -> None:
        """Deliver CDP events emitted before finalization reaches this boundary."""

        for session in list(self._sessions):
            try:
                # A read-only page command pumps the same ordered channel
                # without resetting Network.enable's retention configuration.
                session.send("Page.getFrameTree", {})
            except Exception as exc:
                self.errors.append(f"CDP finalization barrier failed: {exc}")

    def _unfinished_document_keys(self) -> set[tuple[int, str]]:
        return {
            key
            for key, request in self._requests.items()
            if request.get("__resource_type") == "Document"
            and key not in self._finished
            and key not in self._failed
        }

    def _drain_documents(self, eligible: set[tuple[int, str]]) -> None:
        """Bound finalization to navigations observed at the protocol barrier.

        This deliberately excludes future timer-created requests and unfinished
        subresources. Short Playwright event-loop pumps stop as soon as every
        eligible CDP request emits loadingFinished or loadingFailed; they do not
        depend on a top-level load event and therefore also cover iframes.
        """

        if not eligible:
            return
        pages = {
            self._session_pages[session_id]
            for session_id, _request_id in eligible
            if session_id in self._session_pages
        }
        if not pages:
            return
        page = next(iter(pages))
        deadline = time.monotonic() + self._DOCUMENT_DRAIN_TIMEOUT_MS / 1000
        while True:
            remaining = eligible.difference(self._finished, self._failed)
            if not remaining:
                return
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms == 0:
                return
            try:
                # wait_for_timeout is Playwright's supported sync event-loop pump.
                # The 50 ms slice is not a fixed post-action delay: the lifecycle
                # predicate above terminates immediately after the Document does.
                page.wait_for_timeout(min(50, remaining_ms))
            except Exception:
                # A timeout or closed page leaves a truthful incomplete entry.
                return
            self._protocol_barrier()

    def _attach(self, page: Any) -> None:
        try:
            assert self._context is not None
            session = self._context.new_cdp_session(page)
            session_index = id(session)
            self._session_pages[session_index] = page
            session.on(
                "Network.requestWillBeSent",
                lambda event: self._record_request(session, session_index, event),
            )
            session.on(
                "Network.responseReceived",
                lambda event: self._record_response(session, session_index, event),
            )
            session.on(
                "Network.requestWillBeSentExtraInfo",
                lambda event: self._extra_headers.setdefault(
                    (session_index, str(event["requestId"])), []
                ).append(event.get("headers", {})),
            )
            session.on(
                "Network.responseReceivedExtraInfo",
                lambda event: self._extra_response_headers.setdefault(
                    (session_index, str(event["requestId"])), []
                ).append(event.get("headers", {})),
            )
            session.on(
                "Network.loadingFinished",
                lambda event: self._record_finished(session, session_index, event),
            )
            session.on(
                "Network.loadingFailed",
                lambda event: self._record_failed(session_index, event),
            )
            session.send("Network.enable", dict(self._NETWORK_CAPTURE_OPTIONS))
            self._sessions.append(session)
        except Exception as exc:
            self.errors.append(f"CDP attach failed: {exc}")

    def _record_request(self, session: Any, session_index: int, event: dict[str, Any]) -> None:
        request_id = str(event["requestId"])
        key = (session_index, request_id)
        self._finished.discard(key)
        self._failed.pop(key, None)
        self._request_sessions[key] = session
        previous = self._requests.get(key)
        redirect_response = event.get("redirectResponse")
        if previous is not None and redirect_response is not None:
            redirect_response = dict(redirect_response)
            redirect_response["__cdp"] = {
                "responseSource": "requestWillBeSent.redirectResponse",
                **(
                    {"redirectObservedTimestamp": event["timestamp"]}
                    if "timestamp" in event
                    else {}
                ),
            }
            self._responses.append((session, request_id, dict(previous), redirect_response))
        ordinal = self._request_ordinals.get(key, 0)
        self._request_ordinals[key] = ordinal + 1
        request = dict(event["request"])
        request["__ordinal"] = ordinal
        request["__sequence"] = self._next_request_sequence
        self._next_request_sequence += 1
        request["__resource_type"] = event.get("type")
        request["__cdp"] = {
            "sessionOrdinal": self._session_ordinals.setdefault(
                session_index, len(self._session_ordinals)
            ),
            "requestId": request_id,
            "redirectOrdinal": ordinal,
            **({"requestTimestamp": event["timestamp"]} if "timestamp" in event else {}),
            **({"requestWallTime": event["wallTime"]} if "wallTime" in event else {}),
        }
        self._requests[key] = request

    def _record_response(self, session: Any, session_index: int, event: dict[str, Any]) -> None:
        request_id = str(event["requestId"])
        request = self._requests.get((session_index, request_id))
        if request is not None:
            response = dict(event["response"])
            response["__cdp"] = {
                "responseSource": "responseReceived",
                **(
                    {"responseReceivedTimestamp": event["timestamp"]}
                    if "timestamp" in event
                    else {}
                ),
            }
            self._responses.append((session, request_id, dict(request), response))

    @staticmethod
    def _decode_body(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("base64Encoded"):
            return {
                "body": base64.b64decode(body.get("body") or "", validate=True).decode(
                    "utf-8", errors="replace"
                ),
                "base64Encoded": False,
            }
        return body

    def _record_finished(self, session: Any, session_index: int, event: dict[str, Any]) -> None:
        """Capture JSON bodies while CDP still retains them.

        Waiting until the whole agent run ends is too late for long tasks: CDP may
        evict an earlier response body even though the request metadata remains.
        """

        request_id = str(event["requestId"])
        key = (session_index, request_id)
        request = self._requests.get(key)
        ordinal = int(request.get("__ordinal", 0)) if request is not None else 0
        body_key = (session_index, request_id, ordinal)
        if request is not None and "timestamp" in event:
            self._completion_timestamps[body_key] = event["timestamp"]
        self._finished.add(key)
        response = next(
            (
                item_response
                for item_session, item_request_id, _request, item_response in reversed(
                    self._responses
                )
                if id(item_session) == session_index and item_request_id == request_id
            ),
            None,
        )
        mime_type = str((response or {}).get("mimeType") or "")
        if not _is_json_mime_type(mime_type):
            return
        try:
            self._response_bodies[body_key] = self._decode_body(
                session.send("Network.getResponseBody", {"requestId": request_id})
            )
            self._response_body_errors.pop(body_key, None)
        except Exception as exc:
            # stop() retries once before exposing a capture error.
            self._response_body_errors[body_key] = str(exc)

    def _record_failed(self, session_index: int, event: dict[str, Any]) -> None:
        key = (session_index, str(event["requestId"]))
        self._failed[key] = dict(event)
