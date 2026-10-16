from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

from lexbench_webfetch.paths import validate_http_url
from ..diagnostics import emit


@dataclass
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        header = Message()
        header['content-type'] = content_type
        charset = header.get_content_charset() or 'utf-8'
        try:
            return self.body.decode(charset, errors="replace")
        except (LookupError, ValueError):
            return self.body.decode('utf-8', errors='replace')

    def json(self) -> Any:
        return json.loads(self.text())


class HttpOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def inner_timeout_budget(outer_timeout: float, *, native_default: bool = False) -> float:
    """Keep ordinary calls inside the deadline; native cohorts use the full budget."""
    if not math.isfinite(outer_timeout) or outer_timeout <= 0:
        raise ValueError("outer timeout must be positive and finite")
    if native_default:
        return outer_timeout
    reserve = max(1.0, min(5.0, outer_timeout * 0.1))
    return outer_timeout / 2 if reserve >= outer_timeout else outer_timeout - reserve


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = 30,
    max_bytes: int = 10_000_000,
) -> HttpResponse:
    validate_http_url(url)
    data = None
    final_headers = {"User-Agent": "browser-harness-eval/0.1", **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=final_headers, method=method)
    emit("http_request_started", url=url, method=method, headers=final_headers, json_body=json_body, timeout=timeout)
    try:
        response = urllib.request.build_opener(HttpOnlyRedirectHandler()).open(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    try:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeded max_bytes={max_bytes}")
        emit("http_response_received", status=response.status, url=response.geturl(), headers=dict(response.headers.items()), body_bytes=len(body), body=body.decode("utf-8", "replace"))
        return HttpResponse(
            status=response.status,
            url=response.geturl(),
            headers={key.lower(): value for key, value in response.headers.items()},
            body=body,
        )
    finally:
        response.close()
