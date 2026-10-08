from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any

from ..safety import validate_http_url


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


def inner_timeout_budget(outer_timeout: float) -> float:
    """Forward the full shared budget; the runner rejects late answers."""
    if not math.isfinite(outer_timeout) or outer_timeout <= 0:
        raise ValueError("outer timeout must be positive and finite")
    return outer_timeout


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
    try:
        response = urllib.request.build_opener(HttpOnlyRedirectHandler()).open(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    try:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeded max_bytes={max_bytes}")
        return HttpResponse(
            status=response.status,
            url=response.geturl(),
            headers={key.lower(): value for key, value in response.headers.items()},
            body=body,
        )
    finally:
        response.close()
