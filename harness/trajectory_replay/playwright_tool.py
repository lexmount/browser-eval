"""CowAgent-free execution of frozen BrowserTool replay calls over Chromium CDP."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from playwright.sync_api import sync_playwright
from requests.adapters import HTTPAdapter

from harness.environments.webarena.runtime import (
    BENCHMARK_VIEWPORT,
)
from harness.trajectory_replay.public_webarena.executor import execute_steps

SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS = 1_500
SNAPSHOT_NAVIGATION_GRACE_MS = 300
REQUEST_BODY_CAPTURE_BINDING = "__browserEvalCaptureRequestBody"
REQUEST_BODY_CAPTURE_HEADER = "x-browser-eval-request-body-id"
NATIVE_FORM_REDIRECT_HEADER = "x-browser-eval-native-form-location"
REQUEST_BODY_CAPTURE_LIMIT_BYTES = 20_000_000
REQUEST_BODY_CAPTURE_MAX_PENDING = 256
REQUEST_BODY_CAPTURE_TTL_SECONDS = 120.0
_REQUEST_BODY_CAPTURE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class _HostOverrideAdapter(HTTPAdapter):
    """Connect selected HTTP hosts to a pinned address without changing URL identity."""

    def __init__(self, overrides: tuple[tuple[str, str], ...]):
        super().__init__()
        self._overrides = overrides

    def _address_for(self, hostname: str) -> str | None:
        hostname = hostname.lower()
        for pattern, address in self._overrides:
            if hostname == pattern or (
                pattern.startswith("*.") and hostname.endswith(pattern[1:])
            ):
                return address
        return None

    def get_connection_with_tls_context(
        self,
        request: Any,
        verify: bool | str,
        proxies: dict[str, str] | None = None,
        cert: Any = None,
    ) -> Any:
        parsed = urlsplit(request.url)
        address = self._address_for(parsed.hostname or "")
        if address is None:
            return super().get_connection_with_tls_context(
                request, verify, proxies=proxies, cert=cert
            )
        if parsed.scheme != "http":
            raise ValueError("request bridge host overrides support HTTP origins only")
        request.headers["Host"] = parsed.netloc
        return self.poolmanager.connection_from_host(
            scheme="http",
            host=address,
            port=parsed.port or 80,
        )


def _validated_host_overrides(
    overrides: list[dict[str, str]] | None,
) -> tuple[tuple[str, str], ...]:
    normalized: list[tuple[str, str]] = []
    for row in overrides or []:
        pattern = row.get("pattern") if isinstance(row, dict) else None
        address = row.get("address") if isinstance(row, dict) else None
        if (
            not isinstance(pattern, str)
            or not isinstance(address, str)
            or not pattern
            or pattern != pattern.lower()
            or pattern.count("*") > 1
            or ("*" in pattern and not pattern.startswith("*."))
            or any(char in pattern for char in "/:,\n\r\t ")
        ):
            raise ValueError("request bridge host override pattern is invalid")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ValueError("request bridge host override address is invalid") from None
        normalized.append((pattern, address))
    if len({pattern for pattern, _ in normalized}) != len(normalized):
        raise ValueError("request bridge host override pattern is duplicated")
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item[0].startswith("*."),
                -len(item[0]),
                item[0],
            ),
        )
    )


def _http_origin(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        return ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def _navigation_interrupted_snapshot(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "navigation to finish",
            "execution context was destroyed",
            "cannot find context with specified id",
            "frame was detached",
            "inspected target navigated or closed",
        )
    )


@dataclass
class ReplayResult:
    status: str
    result: str


def _request_body_capture_script(origins: frozenset[str]) -> str:
    """Capture selected fetch bodies before Kitesurf drops them from CDP."""

    return f"""
(() => {{
  const origins = new Set({json.dumps(sorted(origins))});
  const binding = {json.dumps(REQUEST_BODY_CAPTURE_BINDING)};
  const header = {json.dumps(REQUEST_BODY_CAPTURE_HEADER)};
  const nativeRedirectHeader = {json.dumps(NATIVE_FORM_REDIRECT_HEADER)};
  const limit = {REQUEST_BODY_CAPTURE_LIMIT_BYTES};
  if (globalThis.__browserEvalRequestBodyCaptureInstalled) return;
  globalThis.__browserEvalRequestBodyCaptureInstalled = true;
  const prefix = `${{Date.now().toString(36)}}-${{Math.random().toString(36).slice(2)}}`;
  let sequence = 0;
  let keepaliveBodyBytes = 0;

  const selected = url => {{
    try {{ return origins.has(new URL(url, document.baseURI).origin); }}
    catch (_) {{ return false; }}
  }};
  const byteLength = text => {{
    if (text.length > limit) {{
      throw new Error('request body exceeds the capture limit');
    }}
    const size = new TextEncoder().encode(text).byteLength;
    if (size > limit) {{
      throw new Error('request body exceeds the capture limit');
    }}
    return size;
  }};
  const checkedText = text => {{
    byteLength(text);
    return text;
  }};
  const urlEncodedLength = text => {{
    let size = 0;
    for (const character of text) {{
      size += /^[A-Za-z0-9*._-]$/.test(character) ? 1 : byteLength(character) * 3;
      if (size > limit) throw new Error('request body exceeds the capture limit');
    }}
    return size;
  }};
  const encode = body => {{
    if (typeof body === 'string') return {{kind: 'text', text: checkedText(body)}};
    if (body instanceof URLSearchParams) {{
      let count = 0;
      let total = 0;
      for (const pair of body.entries()) {{
        if (count >= 10000) {{
          throw new Error('request body has too many URLSearchParams fields');
        }}
        total += urlEncodedLength(String(pair[0]));
        total += urlEncodedLength(String(pair[1]));
        total += count === 0 ? 1 : 2;
        if (total > limit) {{
          throw new Error('request body exceeds the capture limit');
        }}
        count += 1;
      }}
      return {{kind: 'text', text: checkedText(body.toString())}};
    }}
    if (body instanceof FormData) {{
      const entries = [];
      let total = 0;
      for (const pair of body.entries()) {{
        if (entries.length >= 10000) {{
          throw new Error('request body has too many FormData fields');
        }}
        const name = String(pair[0]);
        const value = pair[1];
        total += byteLength(name);
        if (typeof value === 'string') {{
          total += byteLength(value);
          entries.push({{name, value}});
        }} else {{
          if (!(value instanceof File) || value.size !== 0) {{
            throw new Error('request body capture supports only empty FormData files');
          }}
          total += byteLength(value.name);
          total += byteLength(value.type);
          entries.push({{
            name,
            file: {{name: value.name, type: value.type, base64: ''}},
          }});
        }}
        if (total > limit) {{
          throw new Error('request body exceeds the capture limit');
        }}
      }}
      return {{kind: 'form-data', entries}};
    }}
    throw new Error(`unsupported request body ${{Object.prototype.toString.call(body)}}`);
  }};
  const capture = async (method, url, body, source = 'fetch') => {{
    const absolute = new URL(url, document.baseURI).href;
    if (body === undefined || body === null || !selected(absolute)) return null;
    const requestId = `${{prefix}}-${{sequence += 1}}`;
    await globalThis[binding]({{
      requestId,
      method: String(method || 'GET').toUpperCase(),
      url: absolute,
      body: encode(body),
      source,
    }});
    return requestId;
  }};

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async function(input, init) {{
    const options = init || {{}};
    const method = options.method || (input && input.method) || 'GET';
    const url = typeof input === 'string' ? input : String(input && input.url || input);
    const hasInitBody = Object.prototype.hasOwnProperty.call(options, 'body');
    const hasBody = hasInitBody
      ? options.body !== undefined && options.body !== null
      : input instanceof Request && input.body !== null;
    if (hasInitBody) {{
      const normalizedMethod = String(method).toUpperCase();
      if (
        hasBody
        && (normalizedMethod === 'GET' || normalizedMethod === 'HEAD')
      ) {{
        throw new TypeError('Request with GET/HEAD method cannot have body.');
      }}
    }}
    let keepaliveSize = 0;
    const effectiveKeepalive = Object.prototype.hasOwnProperty.call(options, 'keepalive')
      ? Boolean(options.keepalive)
      : input instanceof Request && input.keepalive;
    if (hasBody && effectiveKeepalive) {{
      const absolute = new URL(url, document.baseURI).href;
      const probe = hasInitBody
        ? new Request(input instanceof Request ? input : absolute, options)
        : input.clone();
      keepaliveSize = (await probe.clone().arrayBuffer()).byteLength;
      if (keepaliveBodyBytes + keepaliveSize > 65536) {{
        throw new TypeError('Failed to fetch');
      }}
      keepaliveBodyBytes += keepaliveSize;
    }}
    try {{
      if (hasInitBody) {{
        const requestId = await capture(method, url, options.body);
        if (requestId !== null) {{
          const inherited = input instanceof Request ? input.headers : undefined;
          const headers = new Headers(options.headers || inherited);
          if (!headers.has('content-type')) {{
            if (options.body instanceof URLSearchParams) {{
              headers.set('content-type', 'application/x-www-form-urlencoded;charset=UTF-8');
            }} else if (typeof options.body === 'string') {{
              headers.set('content-type', 'text/plain;charset=UTF-8');
            }}
          }}
          headers.set(header, requestId);
          return await originalFetch.call(this, input, {{...options, headers, body: ''}});
        }}
      }} else if (input instanceof Request && input.body !== null && selected(url)) {{
        throw new Error('request body capture requires the body in fetch init');
      }}
      return await originalFetch.call(this, input, init);
    }} finally {{
      keepaliveBodyBytes -= keepaliveSize;
    }}
  }};

  const originalSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function() {{
    const method = String(this.method || 'get').toUpperCase();
    const url = this.action || document.URL;
    if (method !== 'POST' || !selected(url) || (this.target && this.target !== '_self')) {{
      return originalSubmit.call(this);
    }}
    const enctype = String(this.enctype || 'application/x-www-form-urlencoded').toLowerCase();
    const formData = new FormData(this);
    let body;
    if (enctype === 'application/x-www-form-urlencoded') {{
      body = new URLSearchParams();
      for (const [name, value] of formData.entries()) {{
        const normalizedName = String(name).replace(/\\r\\n|\\r|\\n/g, '\\r\\n');
        const rawValue = typeof value === 'string' ? value : value.name;
        body.append(normalizedName, String(rawValue).replace(/\\r\\n|\\r|\\n/g, '\\r\\n'));
      }}
    }} else if (enctype === 'multipart/form-data') {{
      body = formData;
    }} else {{
      throw new Error(`unsupported native form enctype ${{enctype}}`);
    }}
    void (async () => {{
      const requestId = await capture(method, url, body, 'native-form');
      const headers = new Headers();
      headers.set(header, requestId);
      const response = await originalFetch(url, {{
        method,
        credentials: 'same-origin',
        body,
        headers,
        redirect: 'manual',
      }});
      const redirect = response.headers.get(nativeRedirectHeader);
      if (!redirect) throw new Error('native form bridge returned no supported redirect');
      location.assign(redirect);
    }})();
  }};
}})();
"""


class PlaywrightReplayTool:
    """Expose the small BrowserTool contract consumed by the shared replay runner.

    The shared snapshot-ref path still dispatches DOM click/fill/select itself,
    preserving its compiled-ref behavior on either backend.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        engine: str = "chromium",
        headers: dict[str, str] | None = None,
        request_bridge_origins: set[str] | None = None,
        request_bridge_host_overrides: list[dict[str, str]] | None = None,
    ):
        if engine not in {"chromium", "moli", "lightpanda", "obscura", "kitesurf"}:
            raise ValueError(f"unknown Playwright replay engine: {engine}")
        self._engine = engine
        self._supports_multiple_pages = engine not in {"lightpanda", "obscura", "kitesurf"}
        self._supports_network_reset_cdp = engine not in {"obscura", "kitesurf"}
        self._supports_storage_cdp = engine not in {"lightpanda", "obscura", "kitesurf"}
        self._native_login_form_submit = engine == "kitesurf"
        self._fresh_context_per_task = True
        self._supports_context_request_cookie_sharing = engine not in {
            "lightpanda",
            "kitesurf",
        }
        self.har_capture_transport = "playwright" if engine == "kitesurf" else "cdp"
        self.har_request_transform = (
            self.transform_playwright_har_request if engine == "kitesurf" else None
        )
        self._layout_sessions: dict[Any, Any] = {}
        self._viewport_sessions: dict[Any, Any] = {}
        self._layout_connection_poisoned = False
        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        self.runtime_metadata: dict[str, Any] | None = None
        self.request_bridge_stats = {"requests": 0, "failures": 0}
        self._request_bridge_session = None
        self._request_bridge_origins: frozenset[str] = frozenset()
        self._request_bridge_bodies: dict[str, dict[str, Any]] = {}
        self._request_bridge_body_bytes = 0
        self._request_bridge_har_bodies: dict[str, dict[str, Any]] = {}
        self._request_bridge_har_body_bytes = 0
        try:
            connect_options: dict[str, Any] = {"timeout": 90_000}
            if headers is not None:
                connect_options["headers"] = dict(headers)
            self._browser = self._playwright.chromium.connect_over_cdp(
                endpoint, **connect_options
            )
            if engine == "kitesurf":
                browser_session = self._browser.new_browser_cdp_session()
                try:
                    version = browser_session.send("Browser.getVersion")
                finally:
                    browser_session.detach()
                self.runtime_metadata = {
                    key: version.get(key)
                    for key in (
                        "protocolVersion",
                        "product",
                        "revision",
                        "userAgent",
                        "jsVersion",
                    )
                    if version.get(key) is not None
                }
                if len(self._browser.contexts) != 1:
                    raise RuntimeError("Kitesurf did not expose exactly one default context")
                self._context = self._browser.contexts[0]
                if len(self._context.pages) != 1:
                    raise RuntimeError("Kitesurf did not expose exactly one default page")
                self._page = self._context.pages[0]
                self._install_request_bridge(
                    request_bridge_origins or set(),
                    request_bridge_host_overrides,
                )
                self._page.set_viewport_size(dict(BENCHMARK_VIEWPORT))
                # Kitesurf applies emulation to the next document; its initial
                # provider-owned about:blank retains the service default height.
                self._page.goto("about:blank", wait_until="load", timeout=10_000)
            else:
                self._context = self._browser.new_context(
                    viewport=dict(BENCHMARK_VIEWPORT), locale="en-US"
                )
                self._page = self._context.new_page()
        except Exception:
            self.close()
            raise
        self._owns_cdp_context = True

    def _install_request_bridge(
        self,
        origins: set[str],
        host_overrides: list[dict[str, str]] | None = None,
    ) -> None:
        """Fetch selected private origins from the local Playwright driver."""

        parsed_origins = [urlsplit(origin) for origin in origins]
        if any(
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            for parsed in parsed_origins
        ):
            raise ValueError("request bridge origins must be bare HTTP(S) origins")
        normalized = {_http_origin(origin) for origin in origins}
        if not normalized:
            return
        self._request_bridge_origins = frozenset(normalized)
        self._request_bridge_bodies = {}
        self._request_bridge_body_bytes = 0
        self._request_bridge_har_bodies = {}
        self._request_bridge_har_body_bytes = 0
        self._request_bridge_session = requests.Session()
        self._request_bridge_session.trust_env = False
        normalized_overrides = _validated_host_overrides(host_overrides)
        if normalized_overrides:
            for scheme in ("http://", "https://"):
                self._request_bridge_session.mount(
                    scheme, _HostOverrideAdapter(normalized_overrides)
                )
        self._context.expose_binding(
            REQUEST_BODY_CAPTURE_BINDING, self._capture_request_body
        )
        self._context.add_init_script(
            _request_body_capture_script(self._request_bridge_origins)
        )

        def selected(url: str) -> bool:
            return _http_origin(url) in normalized

        def bridge(route) -> None:
            self.request_bridge_stats["requests"] += 1
            try:
                request = route.request
                headers = {
                    name: value
                    for name, value in request.all_headers().items()
                    if name.lower()
                    not in {
                        "accept-encoding",
                        "connection",
                        "content-length",
                        "host",
                        "proxy-connection",
                        "signature",
                        "signature-agent",
                        "signature-input",
                        "transfer-encoding",
                        REQUEST_BODY_CAPTURE_HEADER,
                    }
                }
                request_headers = request.all_headers()
                capture_id = request_headers.get(REQUEST_BODY_CAPTURE_HEADER)
                data = request.post_data_buffer
                content_type = None
                if capture_id is not None:
                    data, content_type = self._request_bridge_body_for_route(
                        capture_id, request.method, request.url
                    )
                    if content_type is not None:
                        headers = {
                            name: value
                            for name, value in headers.items()
                            if name.lower() != "content-type"
                        }
                        headers["content-type"] = content_type
                response = self._request_bridge_session.request(
                    method=request.method,
                    url=request.url,
                    headers=headers,
                    data=data,
                    allow_redirects=False,
                    timeout=60,
                )
                if capture_id is not None:
                    self._record_request_bridge_redirect(
                        capture_id, request.method, request.url, response
                    )
                    self._record_request_bridge_response(
                        capture_id,
                        request.method,
                        request.url,
                        response,
                    )
                self._sync_request_bridge_cookies(
                    str(response.url), response.cookies
                )
                response_headers = {
                    name: value
                    for name, value in response.headers.items()
                    if name.lower()
                    not in {
                        "connection",
                        "content-encoding",
                        "content-length",
                        "set-cookie",
                        "transfer-encoding",
                    }
                }
                source = self._request_bridge_har_bodies.get(
                    capture_id or "", {}
                ).get("source")
                if source == "native-form":
                    redirect_url = self._native_form_redirect_url(request.url, response)
                    route.fulfill(
                        status=200,
                        headers={NATIVE_FORM_REDIRECT_HEADER: redirect_url},
                        body=b"",
                    )
                else:
                    route.fulfill(
                        status=response.status_code,
                        headers=response_headers,
                        body=response.content,
                    )
            except Exception:
                self.request_bridge_stats["failures"] += 1
                try:
                    route.abort("failed")
                except Exception:
                    pass
                raise

        self._context.route(selected, bridge)

    def transform_playwright_har_request(
        self, method: str, url: str, headers: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
        """Restore a Kitesurf fetch body and remove its private correlation header."""

        capture_id = next(
            (
                str(value)
                for name, value in headers.items()
                if str(name).lower() == REQUEST_BODY_CAPTURE_HEADER
            ),
            None,
        )
        cleaned = {
            name: value
            for name, value in headers.items()
            if str(name).lower() != REQUEST_BODY_CAPTURE_HEADER
        }
        normalized_url = urlsplit(url)._replace(fragment="").geturl()
        request_key = (method.upper(), normalized_url)
        record = self._request_bridge_har_bodies.get(capture_id or "")
        if record is None:
            return cleaned, None, None
        if request_key not in record["allowed_requests"]:
            raise RuntimeError("HAR request body does not match its routed request")
        response_transform = record.get("response_transforms", {}).get(request_key)
        if request_key not in record["body_requests"]:
            return cleaned, None, response_transform
        if record["content_type"] is not None:
            cleaned = {
                name: value
                for name, value in cleaned.items()
                if str(name).lower() != "content-type"
            }
            cleaned["content-type"] = record["content_type"]
        return cleaned, record["post_data"], response_transform

    def _request_bridge_body_for_route(
        self, request_id: str, method: str, url: str
    ) -> tuple[bytes | None, str | None]:
        normalized_url = urlsplit(url)._replace(fragment="").geturl()
        request_key = (method.upper(), normalized_url)
        self._purge_captured_request_bodies()
        if request_id in self._request_bridge_bodies:
            source = self._request_bridge_bodies[request_id].get("source", "fetch")
            captured = self._pop_captured_request_body(request_id, method, url)
            data, content_type = self._decode_captured_request_body(
                request_id, captured
            )
            if (
                self._request_bridge_har_body_bytes + len(data)
                > REQUEST_BODY_CAPTURE_LIMIT_BYTES
            ):
                raise RuntimeError("HAR request bodies exceed the total size limit")
            self._request_bridge_har_bodies[request_id] = {
                "post_data": data.decode("utf-8"),
                "body_bytes": data,
                "content_type": content_type,
                "allowed_requests": {request_key},
                "body_requests": {request_key},
                "source": source,
                "response_transforms": {},
            }
            self._request_bridge_har_body_bytes += len(data)
            return data, content_type
        record = self._request_bridge_har_bodies.get(request_id)
        if record is None or request_key not in record["allowed_requests"]:
            raise RuntimeError("request body capture ID is missing or expired")
        if request_key not in record["body_requests"]:
            return None, None
        return record["body_bytes"], record["content_type"]

    def _record_request_bridge_response(
        self, request_id: str, method: str, url: str, response: Any
    ) -> None:
        record = self._request_bridge_har_bodies.get(request_id)
        if record is None:
            return
        raw_cookie = next(
            (
                str(value)
                for name, value in response.headers.items()
                if str(name).lower() == "set-cookie" and "mage-messages=" in str(value)
            ),
            "",
        )
        request_key = (
            method.upper(),
            urlsplit(url)._replace(fragment="").geturl(),
        )
        if request_key not in record["allowed_requests"]:
            raise RuntimeError("response evidence does not match its routed request")
        transform: dict[str, Any] = {}
        if raw_cookie:
            transform["extra_headers"] = {"set-cookie": raw_cookie}
        if record.get("source") == "native-form":
            transform.update(
                {
                    "status": int(response.status_code),
                    "statusText": str(getattr(response, "reason", "") or ""),
                    "headers": {
                        str(name): str(value)
                        for name, value in response.headers.items()
                        if str(name).lower()
                        not in {
                            "connection",
                            "content-encoding",
                            "content-length",
                            "set-cookie",
                            "transfer-encoding",
                        }
                    },
                }
            )
        if transform:
            record["response_transforms"][request_key] = transform

    def _native_form_redirect_url(self, request_url: str, response: Any) -> str:
        if int(response.status_code) not in {302, 303}:
            raise RuntimeError(
                f"native form bridge requires a 302/303 redirect; got {response.status_code}"
            )
        location = response.headers.get("location")
        if not location:
            raise RuntimeError("native form bridge redirect has no location")
        target = urljoin(request_url, str(location))
        if _http_origin(target) != _http_origin(request_url):
            raise RuntimeError("native form bridge rejected a cross-origin redirect")
        return target

    def _record_request_bridge_redirect(
        self, request_id: str, method: str, url: str, response: Any
    ) -> None:
        record = self._request_bridge_har_bodies.get(request_id)
        if record is None or int(response.status_code) not in {301, 302, 303, 307, 308}:
            return
        location = next(
            (
                str(value)
                for name, value in response.headers.items()
                if str(name).lower() == "location"
            ),
            "",
        )
        if not location:
            return
        current_method = method.upper()
        status = int(response.status_code)
        if status == 303 and current_method != "HEAD":
            next_method = "GET"
        elif status in {301, 302} and current_method == "POST":
            next_method = "GET"
        else:
            next_method = current_method
        next_url = urlsplit(urljoin(url, location))._replace(fragment="").geturl()
        next_key = (next_method, next_url)
        record["allowed_requests"].add(next_key)
        current_key = (
            current_method,
            urlsplit(url)._replace(fragment="").geturl(),
        )
        if next_method == current_method and current_key in record["body_requests"]:
            record["body_requests"].add(next_key)

    def _capture_request_body(self, _source: Any, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ValueError("request body capture payload must be an object")
        method = payload.get("method")
        url = payload.get("url")
        body = payload.get("body")
        source = payload.get("source", "fetch")
        request_id = payload.get("requestId")
        if (
            not isinstance(request_id, str)
            or _REQUEST_BODY_CAPTURE_ID.fullmatch(request_id) is None
        ):
            raise ValueError("request body capture has an invalid request ID")
        if not isinstance(method, str) or not isinstance(url, str):
            raise ValueError("request body capture is missing its method or URL")
        method = method.upper()
        parsed = urlsplit(url)
        origin = _http_origin(url)
        if origin not in self._request_bridge_origins:
            raise ValueError("request body capture origin is not allowed")
        if not isinstance(body, dict):
            raise ValueError("request body capture is missing its body")
        if source not in {"fetch", "native-form"}:
            raise ValueError("request body capture has an invalid source")
        body_size = self._validate_captured_request_body(body)
        normalized_url = parsed._replace(fragment="").geturl()
        self._purge_captured_request_bodies()
        if (
            request_id in self._request_bridge_bodies
            or request_id in self._request_bridge_har_bodies
        ):
            raise ValueError("request body capture reused a request ID")
        if (
            len(self._request_bridge_bodies) + len(self._request_bridge_har_bodies)
            >= REQUEST_BODY_CAPTURE_MAX_PENDING
        ):
            raise RuntimeError("request body capture count exceeded its bound")
        if (
            self._request_bridge_body_bytes
            + self._request_bridge_har_body_bytes
            + body_size
            > REQUEST_BODY_CAPTURE_LIMIT_BYTES
        ):
            raise RuntimeError("pending request bodies exceed the total size limit")
        self._request_bridge_bodies[request_id] = {
            "captured_at": time.monotonic(),
            "method": method,
            "url": normalized_url,
            "body": body,
            "size": body_size,
            "source": source,
        }
        self._request_bridge_body_bytes += body_size

    @staticmethod
    def _validate_captured_request_body(body: dict[str, Any]) -> int:
        kind = body.get("kind")
        if kind == "text":
            text = body.get("text")
            if not isinstance(text, str):
                raise ValueError("captured text body is invalid")
            size = len(text.encode("utf-8"))
            if size > REQUEST_BODY_CAPTURE_LIMIT_BYTES:
                raise ValueError("captured text body exceeds the size limit")
            return size
        if kind == "form-data":
            entries = body.get("entries")
            if not isinstance(entries, list) or len(entries) > 10_000:
                raise ValueError("captured form body is invalid")
            total = 0
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                    raise ValueError("captured form entry is invalid")
                if "value" in entry:
                    if not isinstance(entry["value"], str):
                        raise ValueError("captured form value is invalid")
                    total += len(entry["value"].encode("utf-8"))
                elif "file" in entry:
                    file = entry["file"]
                    if (
                        not isinstance(file, dict)
                        or not isinstance(file.get("name"), str)
                        or not isinstance(file.get("type"), str)
                        or file.get("base64") != ""
                    ):
                        raise ValueError("captured form supports only empty files")
                    total += len(file["name"].encode("utf-8"))
                    total += len(file["type"].encode("utf-8"))
                else:
                    raise ValueError("captured form entry has no value")
                total += len(entry["name"].encode("utf-8"))
                if total > REQUEST_BODY_CAPTURE_LIMIT_BYTES:
                    raise ValueError("captured form body exceeds the size limit")
            return total
        raise ValueError(f"unsupported captured request body: {body.get('type', kind)}")

    def _purge_captured_request_bodies(self) -> None:
        cutoff = time.monotonic() - REQUEST_BODY_CAPTURE_TTL_SECONDS
        expired = [
            request_id
            for request_id, capture in self._request_bridge_bodies.items()
            if capture["captured_at"] < cutoff
        ]
        for request_id in expired:
            capture = self._request_bridge_bodies.pop(request_id)
            self._request_bridge_body_bytes -= capture["size"]

    def _pop_captured_request_body(
        self, request_id: str, method: str, url: str
    ) -> dict[str, Any]:
        self._purge_captured_request_bodies()
        parsed = urlsplit(url)
        normalized_url = parsed._replace(fragment="").geturl()
        capture = self._request_bridge_bodies.pop(request_id, None)
        if capture is None:
            raise RuntimeError("request body capture ID is missing or expired")
        self._request_bridge_body_bytes -= capture["size"]
        if capture["method"] != method.upper() or capture["url"] != normalized_url:
            raise RuntimeError("request body capture does not match the routed request")
        return capture["body"]

    @staticmethod
    def _decode_captured_request_body(
        request_id: str, body: dict[str, Any]
    ) -> tuple[bytes, str | None]:
        kind = body["kind"]
        if kind == "text":
            return body["text"].encode("utf-8"), None
        boundary = f"browser-eval-{hashlib.sha256(request_id.encode()).hexdigest()[:24]}"
        chunks: list[bytes] = []
        for entry in body["entries"]:
            name = entry["name"]
            if "\r" in name or "\n" in name:
                raise ValueError("captured form field name contains a newline")
            escaped_name = name.replace('"', "%22")
            disposition = f'Content-Disposition: form-data; name="{escaped_name}"'
            content = b""
            content_type = ""
            if "value" in entry:
                content = entry["value"].encode("utf-8")
            else:
                file = entry["file"]
                filename = file["name"]
                if "\r" in filename or "\n" in filename:
                    raise ValueError("captured form filename contains a newline")
                escaped_filename = filename.replace('"', "%22")
                disposition += f'; filename="{escaped_filename}"'
                content_type = file["type"] or "application/octet-stream"
                if "\r" in content_type or "\n" in content_type:
                    raise ValueError("captured form content type contains a newline")
                content = base64.b64decode(file["base64"], validate=True)
            chunks.extend((f"--{boundary}\r\n".encode(), disposition.encode(), b"\r\n"))
            if content_type:
                chunks.extend((f"Content-Type: {content_type}\r\n".encode(),))
            chunks.extend((b"\r\n", content, b"\r\n"))
        chunks.append(f"--{boundary}--\r\n".encode())
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def _sync_request_bridge_cookies(
        self, response_url: str, response_cookies: Any
    ) -> None:
        parsed = urlsplit(response_url)
        origin = _http_origin(response_url)
        if origin not in self._request_bridge_origins:
            raise RuntimeError("request bridge cookie response origin is not allowed")
        host = (parsed.hostname or "").lower()
        cookies = []
        for cookie in response_cookies:
            item: dict[str, Any] = {
                "name": cookie.name,
                "value": cookie.value,
                "secure": bool(cookie.secure),
                "httpOnly": any(
                    str(name).lower() == "httponly" for name in cookie._rest
                ),
            }
            if cookie.domain_specified:
                cookie_domain = str(cookie.domain).lstrip(".").lower()
                if host != cookie_domain and not host.endswith(f".{cookie_domain}"):
                    raise RuntimeError("request bridge rejected an invalid cookie domain")
                item["domain"] = cookie.domain
                item["path"] = cookie.path or "/"
            else:
                if (cookie.path or "/") != "/":
                    raise RuntimeError(
                        "request bridge cannot preserve a non-root host-only cookie path"
                    )
                item["url"] = f"{origin}/"
            if cookie.expires is not None:
                item["expires"] = float(cookie.expires)
            same_site = next(
                (
                    value
                    for name, value in cookie._rest.items()
                    if str(name).lower() == "samesite"
                ),
                None,
            )
            if isinstance(same_site, str) and same_site.capitalize() in {
                "Strict",
                "Lax",
                "None",
            }:
                item["sameSite"] = same_site.capitalize()
            cookies.append(item)
        if cookies:
            self._context.add_cookies(cookies)

    def _request_login_form(
        self,
        *,
        source_origin: str,
        method: str,
        url: str,
        fields: list[Any],
        referer: str,
    ) -> Any:
        current_method = method
        current_url = url
        current_fields: list[Any] | None = fields
        current_referer = referer
        for _ in range(11):
            parsed = urlsplit(current_url)
            if f"{parsed.scheme}://{parsed.netloc}" != source_origin:
                raise RuntimeError("login form bridge rejected a cross-origin redirect")
            kwargs: dict[str, Any] = {
                "method": current_method,
                "url": current_url,
                "headers": {"referer": current_referer},
                "allow_redirects": False,
                "timeout": 60,
            }
            if current_fields is not None:
                kwargs["params" if current_method == "get" else "data"] = current_fields
            response = self._request_bridge_session.request(**kwargs)
            self._sync_request_bridge_cookies(str(response.url), response.cookies)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location:
                raise RuntimeError("login form bridge redirect has no location")
            current_referer = str(response.url)
            current_url = urljoin(str(response.url), location)
            if response.status_code == 303 or (
                response.status_code in {301, 302} and current_method == "post"
            ):
                current_method = "get"
                current_fields = None
        raise RuntimeError("login form bridge exceeded ten redirects")

    def _submit_login_form(self, page: Any, submitter: Any) -> str:
        form = submitter.evaluate(
            """element => {
                const form = element.form;
                if (!form) throw new Error('login submitter has no form');
                const fields = Array.from(new FormData(form), ([name, value]) => [
                    name,
                    typeof value === 'string' ? value : value.name,
                ]);
                if (element.name) fields.push([element.name, element.value || '']);
                return {action: form.action, method: form.method || 'get', fields};
            }"""
        )
        action = str(form.get("action") or "")
        method = str(form.get("method") or "get").lower()
        fields = form.get("fields")
        source = urlsplit(page.url)
        source_origin = f"{source.scheme}://{source.netloc}"
        target = urlsplit(action)
        if (
            method not in {"get", "post"}
            or not isinstance(fields, list)
            or target.scheme != source.scheme
            or target.netloc != source.netloc
        ):
            raise RuntimeError("login form bridge rejected an invalid submission")
        response = self._request_login_form(
            source_origin=source_origin,
            method=method,
            url=action,
            fields=fields,
            referer=page.url,
        )
        final = urlsplit(str(response.url))
        if (
            not 200 <= response.status_code < 300
            or final.scheme != source.scheme
            or final.netloc != source.netloc
        ):
            raise RuntimeError(
                f"login form bridge returned invalid status or origin: {response.status_code}"
            )
        page.goto(str(response.url), wait_until="domcontentloaded", timeout=90_000)
        return str(response.url)

    def _wait_for_login_ready(
        self,
        page: Any,
        selector: str,
        *,
        exact_text: str | None = None,
        timeout_ms: int = 60_000,
    ) -> None:
        """Wait for a login marker without Kitesurf's broken Locator visibility path."""

        if self._engine != "kitesurf":
            if exact_text is not None:
                page.get_by_role(
                    "button", name=exact_text, exact=True
                ).wait_for(timeout=timeout_ms)
                return
            locator = page.locator(selector)
            if "," in selector:
                locator = locator.first
            locator.wait_for(timeout=timeout_ms)
            return
        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            ready = page.evaluate(
                """({selector, exactText}) => Array.from(
                    document.querySelectorAll(selector)
                ).some(element => {
                    if (exactText !== null && element.textContent.trim() !== exactText) {
                        return false;
                    }
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                })""",
                {"selector": selector, "exactText": exact_text},
            )
            if ready:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"login readiness marker did not appear: {selector}")
            page.wait_for_timeout(100)

    def _get_service(self) -> PlaywrightReplayTool:
        return self

    def _submit(self, callback):
        return callback()

    @property
    def layout_observer(self):
        return self._publish_page_layout if self._engine == "moli" else None

    @property
    def viewport_observer(self):
        if self._engine != "obscura":
            return None
        return self._observe_page_viewport

    @property
    def document_observer(self):
        return self._observe_document if self._engine == "kitesurf" else None

    def _observe_document(
        self, page: Any, timeout_ms: int, limit: int
    ) -> tuple[str, str]:
        if timeout_ms < 1:
            raise ValueError("document observation timeout must be positive")
        if threading.current_thread() is not threading.main_thread() or not hasattr(
            signal, "setitimer"
        ):
            raise RuntimeError(
                "Kitesurf document observation requires the POSIX worker main thread"
            )
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise RuntimeError("Kitesurf document observation found an active process timer")
        previous_handler = signal.getsignal(signal.SIGALRM)

        def expire(_signum: int, _frame: Any) -> None:
            raise TimeoutError(
                f"Kitesurf document observation exceeded {timeout_ms} ms"
            )

        signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, timeout_ms / 1_000)
        try:
            value = page.evaluate(
                """limit => ({
                    title: document.title || '',
                    body: (document.body?.innerText || '').slice(0, limit),
                })""",
                limit,
            )
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
        if not isinstance(value, dict):
            raise RuntimeError("Kitesurf document observation returned an invalid payload")
        return str(value.get("title") or ""), str(value.get("body") or "")

    def _observe_page_viewport(self, page: Any) -> dict[str, int | float]:
        """Read renderer state through CDP instead of echoing Playwright config."""

        sessions = getattr(self, "_viewport_sessions", None)
        if sessions is None:
            sessions = self._viewport_sessions = {}
        session = sessions.get(page)
        if session is None:
            session = self._context.new_cdp_session(page)
            sessions[page] = session
        metrics = session.send("Page.getLayoutMetrics")
        viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport")
        if not isinstance(viewport, dict):
            raise RuntimeError("Obscura returned no observed visual viewport")
        runtime = session.send(
            "Runtime.evaluate",
            {"expression": "devicePixelRatio", "returnByValue": True},
        )
        scale = (runtime.get("result") or {}).get("value")
        width = viewport.get("clientWidth")
        height = viewport.get("clientHeight")
        if not all(isinstance(value, (int, float)) for value in (width, height, scale)):
            raise RuntimeError("Obscura returned incomplete viewport metrics")
        return {
            "width": width,
            "height": height,
            "device_scale_factor": scale,
        }

    @property
    def failure_capture_available(self) -> bool:
        """Whether another renderer command can still complete reliably."""

        return not self._layout_connection_poisoned

    def _publish_page_layout(self, page: Any, timeout_ms: int = 5_000) -> None:
        """Publish Moli's current layout before Playwright consumes geometry.

        Moli keeps ordinary geometry reads side effect free. Its explicit CDP
        extension publishes the same layout tree without paint, rasterization,
        or screenshot encoding.
        """

        if getattr(self, "_engine", "chromium") != "moli":
            return
        if timeout_ms < 1:
            raise TimeoutError("Moli layout publication budget exhausted")
        sessions = getattr(self, "_layout_sessions", None)
        if sessions is None:
            sessions = self._layout_sessions = {}
        session = sessions.get(page)
        if session is None:
            session = self._context.new_cdp_session(page)
            sessions[page] = session

        async def publish() -> dict[str, Any]:
            return await asyncio.wait_for(
                session._impl_obj.send("Page.getLayoutMetrics", {"publishLayout": True}),
                timeout=timeout_ms / 1_000,
            )

        try:
            result = session._sync(publish())
        except TimeoutError as exc:
            sessions.pop(page, None)
            # The renderer still owns the timed-out CDP command.  Sending a
            # screenshot or graceful close behind it can block forever, so let
            # the catalog runner terminate the isolated browser process.
            self._layout_connection_poisoned = True
            raise TimeoutError("Moli layout publication timed out") from exc
        if not isinstance(result, dict) or not isinstance(result.get("layoutViewport"), dict):
            raise RuntimeError("Moli layout publication returned no layout metrics")

    def _snapshot(self, selector: str, timeout: int) -> str:
        deadline = time.monotonic() + timeout / 1_000

        def remaining(maximum: int | None = None) -> int:
            value = int((deadline - time.monotonic()) * 1_000)
            if value < 1:
                raise TimeoutError("snapshot action budget exhausted")
            return value if maximum is None else min(value, maximum)

        def observe(maximum: int | None = None) -> str:
            observation_deadline = deadline
            if maximum is not None:
                observation_deadline = min(observation_deadline, time.monotonic() + maximum / 1_000)
            while True:
                try:
                    self._page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=min(remaining(maximum), SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS),
                    )
                except Exception:
                    pass
                self._publish_page_layout(self._page, remaining())
                locator = self._page.locator(selector).first
                wait_ms = int((observation_deadline - time.monotonic()) * 1_000)
                if wait_ms < 1:
                    raise TimeoutError("snapshot selector did not become visible")
                try:
                    locator.wait_for(state="visible", timeout=min(wait_ms, 1_000))
                except Exception as exc:
                    if _navigation_interrupted_snapshot(exc):
                        raise
                    if (
                        "timeout" not in str(exc).lower()
                        or time.monotonic() >= observation_deadline
                    ):
                        raise
                    continue
                return locator.aria_snapshot(timeout=remaining(maximum))

        try:
            return observe()
        except Exception as exc:
            if not _navigation_interrupted_snapshot(exc):
                raise
            self._page.wait_for_load_state(
                "domcontentloaded",
                timeout=remaining(SNAPSHOT_NAVIGATION_SETTLE_TIMEOUT_MS),
            )
            self._page.wait_for_timeout(min(remaining(), SNAPSHOT_NAVIGATION_GRACE_MS))
            return observe()

    def execute(self, arguments: dict[str, Any]) -> ReplayResult:
        action = arguments.get("action")
        if action == "snapshot":
            try:
                selector = str(arguments.get("selector") or "body")
                timeout = int(arguments.get("timeout_ms", 30_000))
                if timeout < 1:
                    raise ValueError("snapshot timeout_ms must be positive")
                value = self._snapshot(selector, timeout)
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError("snapshot returned an empty observation")
                return ReplayResult("success", value[: int(arguments.get("max_chars", 40_000))])
            except Exception as exc:
                return ReplayResult("error", f"Snapshot failed: {exc}")
        records: list[dict[str, Any]] = []
        try:
            execute_steps(
                self._page,
                [arguments],
                trace=records,
                layout_observer=self.layout_observer,
                document_observer=self.document_observer,
            )
        except Exception as exc:
            return ReplayResult("error", f"{type(exc).__name__}: {exc}")
        value = records[0]["output"]
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return ReplayResult("success", value)

    def close(self) -> None:
        bridge_close_errors: list[BaseException] = []
        if getattr(self, "_request_bridge_session", None) is not None:
            try:
                unroute_all = getattr(self._context, "unroute_all", None)
                if unroute_all is not None:
                    unroute_all(behavior="wait")
            except BaseException as exc:
                bridge_close_errors.append(exc)
            try:
                self._request_bridge_session.close()
            except BaseException as exc:
                bridge_close_errors.append(exc)
        try:
            if not getattr(self, "_layout_connection_poisoned", False):
                try:
                    if self._context is not None and self._engine != "kitesurf":
                        self._context.close()
                finally:
                    if self._browser is not None:
                        self._browser.close()
        except BaseException as exc:
            for bridge_error in bridge_close_errors:
                exc.add_note(f"request bridge close also failed: {bridge_error}")
            raise
        finally:
            self._playwright.stop()
        if bridge_close_errors:
            raise bridge_close_errors[0]
