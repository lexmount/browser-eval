from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
        return self.body.decode(charset, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = 30,
    max_bytes: int = 10_000_000,
) -> HttpResponse:
    data = None
    final_headers = {"User-Agent": "browser-harness-eval/0.1", **(headers or {})}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=final_headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response exceeded max_bytes={max_bytes}")
    return HttpResponse(
        status=response.status,
        url=response.geturl(),
        headers={key.lower(): value for key, value in response.headers.items()},
        body=body,
    )
