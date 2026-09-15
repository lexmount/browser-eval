from __future__ import annotations

import base64
import hashlib
import math
import time
import urllib.error
import urllib.request
from pathlib import PurePosixPath
from urllib.parse import urlencode, urljoin, urlsplit

from ..models import EvalCase, ProviderResult
from ..retrieval_hints import plan_for_case
from ..safety import validate_http_url
from .base import Provider
from .http_client import HttpOnlyRedirectHandler, HttpResponse, inner_timeout_budget, request

_DIRECT_DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".odt",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
)
_MEDIA_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".mp3", ".wav", ".ogg")
_VIDEO_ARCHIVE_EXTENSIONS = (".mp4.zip", ".webm.zip", ".mov.zip", ".m4v.zip")


def _video_page_href(path: str) -> bool:
    """Classify a page link by its URL route, not by the expected answer.

    A video-link task needs detail-page hrefs, but ordinary site navigation
    must not be reported as a video merely because it was in ``a[href]``.
    This is intentionally a conservative candidate filter; it does not claim
    to discover every provider's opaque video URL scheme.
    """
    return any(segment in {"video", "videos", "watch"}
               for segment in path.lower().split("/") if segment)


def _uses_direct_document_route(case: EvalCase) -> bool:
    """Choose file conversion only when the supplied URL itself names a file.

    Viewer pages remain browser tasks.  ArXiv's canonical PDF route omits a
    filename extension, so its documented ``/pdf/<id>`` path is also direct.
    This decision uses only the submitted URL, never reference-answer data.
    """
    if case.track != "document":
        return False
    parsed = urlsplit(case.url)
    path = parsed.path.lower().rstrip("/")
    if path.endswith(_DIRECT_DOCUMENT_EXTENSIONS):
        return True
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host == "arxiv.org" and path.startswith("/pdf/")


def _multipart_body(*, filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    boundary = "browser-eval-" + hashlib.sha256(data).hexdigest()[:24]
    safe_filename = "".join(
        char for char in filename if char.isascii() and (char.isalnum() or char in "._-")
    ) or "document"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{safe_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("ascii")
    return prefix + data + f"\r\n--{boundary}--\r\n".encode("ascii"), boundary


def _post_multipart(
    url: str,
    *,
    token: str,
    filename: str,
    content_type: str,
    data: bytes,
    timeout: float,
    max_bytes: int,
) -> HttpResponse:
    deadline = time.monotonic() + timeout
    body, boundary = _multipart_body(
        filename=filename,
        content_type=content_type,
        data=data,
    )
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "browser-harness-eval/0.1",
        },
    )
    try:
        response = urllib.request.build_opener(HttpOnlyRedirectHandler()).open(
            req, timeout=timeout
        )
    except urllib.error.HTTPError as error:
        response = error
    try:
        response_body = _read_response_body(
            response,
            deadline=deadline,
            max_bytes=max_bytes,
            timeout_message="Cloudflare conversion exceeded its inner deadline",
        )
        return HttpResponse(
            status=response.status,
            url=response.geturl(),
            headers={key.lower(): value for key, value in response.headers.items()},
            body=response_body,
        )
    finally:
        response.close()


def _read_response_body(
    response: object,
    *,
    deadline: float,
    max_bytes: int,
    timeout_message: str,
) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(timeout_message)
        # urllib's timeout applies to individual socket operations, not to the
        # complete response body. Tighten it before every read so a streaming
        # response cannot silently consume the runner's persistence reserve.
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None and hasattr(sock, "settimeout"):
            sock.settimeout(remaining)
        chunk = response.read(min(65_536, max_bytes + 1 - size))  # type: ignore[attr-defined]
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"response exceeded max_bytes={max_bytes}")
    return b"".join(chunks)


def _get_document(url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
    validate_http_url(url)
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": (
                "application/pdf,"
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
                "application/octet-stream;q=0.9,*/*;q=0.1"
            ),
            "User-Agent": "browser-harness-eval/0.1",
        },
    )
    try:
        response = urllib.request.build_opener(HttpOnlyRedirectHandler()).open(
            req, timeout=timeout
        )
    except urllib.error.HTTPError as error:
        response = error
    try:
        body = _read_response_body(
            response,
            deadline=deadline,
            max_bytes=max_bytes,
            timeout_message="Cloudflare document download exceeded its inner deadline",
        )
        return HttpResponse(
            status=response.status,
            url=response.geturl(),
            headers={key.lower(): value for key, value in response.headers.items()},
            body=body,
        )
    finally:
        response.close()


def _markdown_conversion_content(payload: dict[str, object]) -> str:
    result = payload.get("result")
    if isinstance(result, list):
        return "\n\n".join(
            item["data"]
            for item in result
            if isinstance(item, dict) and isinstance(item.get("data"), str)
        )
    if isinstance(result, dict) and isinstance(result.get("data"), str):
        return result["data"]
    return result if isinstance(result, str) else ""


def _resource_urls(
    payload: dict[str, object],
    *,
    base_url: str,
    track: str,
    include_video_page_links: bool = False,
) -> list[str]:
    result = payload.get("result")
    if not isinstance(result, list):
        return []
    raw_values: list[tuple[str, str]] = []
    for group in result:
        if not isinstance(group, dict) or not isinstance(group.get("results"), list):
            continue
        if include_video_page_links and group.get("selector") != "a[href]":
            # A declared detail-link task does not ask for player/audio/iframe
            # sources. Those can be ordinary utility embeds on a homepage.
            continue
        for element in group["results"]:
            if not isinstance(element, dict) or not isinstance(element.get("attributes"), list):
                continue
            for attribute in element["attributes"]:
                if not isinstance(attribute, dict):
                    continue
                name, value = attribute.get("name"), attribute.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    folded = name.casefold()
                    if folded in {"src", "srcset", "href", "data-src", "data-original"}:
                        raw_values.append((folded, value))
    urls: set[str] = set()
    for name, value in raw_values:
        candidates = [value]
        if name == "srcset":
            candidates = [part.strip().split()[0] for part in value.split(",") if part.strip()]
        for candidate in candidates:
            try:
                absolute = urljoin(base_url, candidate.strip())
                parsed = urlsplit(absolute)
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            # A downloadable video may be packaged as a ZIP (for example,
            # ``movie.mp4.zip``).  Keep those media archives without admitting
            # unrelated page/navigation links or arbitrary ZIP files.
            if name == "href" and track == "video":
                media_file = parsed.path.lower().endswith(
                    _MEDIA_EXTENSIONS + _VIDEO_ARCHIVE_EXTENSIONS
                )
                video_page = (include_video_page_links
                              and _video_page_href(parsed.path))
                if not (media_file or video_page):
                    continue
            if name == "href" and track == "image":
                continue
            urls.add(absolute)
    return sorted(urls)


def _origin_status(value: object) -> int | None:
    """Normalize Cloudflare's target-page status without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _interface_error(*, response_status: int,
                     envelope_success: bool,
                     origin_status: int | None) -> tuple[str | None, str | None]:
    """Classify target retrieval without using answer presence as success."""
    if response_status in (401, 403):
        return "authentication", f"HTTP {response_status}"
    if response_status == 429:
        return "rate_limit", f"HTTP {response_status}"
    if not 200 <= response_status < 300:
        return "api_error", f"HTTP {response_status}"
    if not envelope_success:
        return "api_error", "Cloudflare reported an unsuccessful response"
    if origin_status is not None and not 200 <= origin_status < 300:
        return "http_status", f"Origin returned HTTP {origin_status}"
    return None, None


class CloudflareMarkdownProvider(Provider):
    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        http_timeout = inner_timeout_budget(timeout)
        if case.task_kind == "resource" and case.track in {"image", "video"}:
            return self._fetch_media_resources(
                case,
                account_id=account_id,
                http_timeout=http_timeout,
            )
        if _uses_direct_document_route(case):
            return self._fetch_direct_document(
                case,
                account_id=account_id,
                http_timeout=http_timeout,
            )
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        query = urlencode({"browser": self.config["browser"]}) if self.config.get("browser") else ""
        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/markdown"
        )
        request_body = {
            "url": case.url,
            "gotoOptions": {
                "waitUntil": self.config.get("wait_until", "networkidle2"),
                "timeout": service_timeout_ms,
            },
            "actionTimeout": service_timeout_ms,
        }
        route = "browser_rendering_markdown"
        wait_metadata = None
        if case.track == "dynamic":
            try:
                if not math.isfinite(timeout) or timeout <= 0:
                    raise ValueError
                plan = plan_for_case(case)
                assert plan is not None
            except (KeyError, TypeError, ValueError):
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="invalid dynamic retrieval_hints v1",
                )

            # The Markdown endpoint exposes waitForSelector, but no selector-count
            # or arbitrary-function wait. Use only independently declared,
            # answer-free readiness signals and disclose unsupported semantics.
            effective_timeout_ms = min(service_timeout_ms, plan.timeout_ms)
            wait_until = {
                "networkidle": "networkidle2",
                "domcontentloaded": "domcontentloaded",
                "load": "load",
            }.get(plan.wait_until)
            if wait_until is None or effective_timeout_ms < 1:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="invalid dynamic retrieval_hints v1",
                )
            request_body["gotoOptions"] = {
                "waitUntil": wait_until,
                "timeout": effective_timeout_ms,
            }
            request_body["actionTimeout"] = effective_timeout_ms
            # Quick Actions documents these as independent readiness timers, not
            # an ordered selector-then-delay chain. Supplying both can let the
            # shorter timer win, so choose the strongest single native signal.
            if plan.frame_selector:
                # The endpoint cannot target a child browsing context. For this
                # route a predeclared delay is stronger than the outer iframe,
                # which is normally present before its child content is ready.
                request_body["waitForTimeout"] = plan.delay_ms
                strategy = "predeclared_delay"
            else:
                request_body["waitForSelector"] = {
                    "selector": plan.selector,
                    "timeout": effective_timeout_ms,
                }
                strategy = "selector"
            count_enforced = plan.frame_selector is None and plan.minimum_count == 1
            exact = count_enforced and plan.delay_ms == 0
            unsupported = []
            if plan.frame_selector:
                unsupported.extend(["child_frame_selector", "selector"])
            if plan.minimum_count != 1:
                unsupported.append("minimum_count")
            if plan.delay_ms and not plan.frame_selector:
                unsupported.append("post_selector_delay")
            route = "browser_rendering_markdown_dynamic"
            wait_metadata = {
                "strategy": strategy,
                "selector": plan.selector,
                "frame_selector": plan.frame_selector,
                "minimum_count": plan.minimum_count,
                "minimum_count_enforced": count_enforced,
                "completion_support": "exact" if exact else "approximate",
                "unsupported": unsupported,
                "delay_ms": plan.delay_ms,
                "timeout_ms": effective_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
                "wait_until": wait_until,
            }
        response = request(
            endpoint + ("?" + query if query else ""),
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body=request_body,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        content = payload.get("result") if isinstance(payload.get("result"), str) else ""
        errors = payload.get("errors")
        errors = errors if isinstance(errors, list) else []
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        origin_headers = meta.get("headers")
        origin_headers = origin_headers if isinstance(origin_headers, dict) else {}
        upstream_status = _origin_status(meta.get("status"))
        envelope_success = payload.get("success") is True
        error_type, error_message = _interface_error(
            response_status=response.status,
            envelope_success=envelope_success,
            origin_status=upstream_status,
        )
        ok = error_type is None
        return ProviderResult(
            ok=ok,
            # The API envelope remains private, but its declared result is answer
            # evidence even when the origin is blocked or the result is empty.
            # Task scoring, not request classification, decides its usefulness.
            content=content if envelope_success else "",
            status_code=response.status,
            final_url=meta.get("finalUrl") or case.url,
            title=meta.get("title"),
            error_type=error_type,
            error_message=error_message,
            metadata={
                # The runner removes this internal value into hash-bound private
                # evidence before writing public results.
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "route": route,
                "wait": wait_metadata,
                "browser": self.config.get("browser"),
                "service_timeout_ms": service_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
                "upstream_status": upstream_status,
                "empty_content": envelope_success and not content,
                "content_type": origin_headers.get("content-type"),
                "redirect_chain": meta.get("redirectChain"),
                "error_count": len(errors),
                "retry_after": response.headers.get("retry-after"),
                "browser_ms_used": response.headers.get("x-browser-ms-used"),
            },
        )

    def _fetch_media_resources(
        self,
        case: EvalCase,
        *,
        account_id: str,
        http_timeout: float,
    ) -> ProviderResult:
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        include_video_page_links = case.track == "video" and any(
            isinstance(resource, dict) and resource.get("kind") == "video-link"
            for resource in case.expected_resources
        )
        selectors = (
            ["img", "picture source"]
            if case.track == "image"
            else (["a[href]"] if include_video_page_links else
                  ["video", "video source", "audio", "audio source", "iframe", "a[href]"])
        )
        response = request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/scrape",
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body={
                "url": case.url,
                "elements": [{"selector": selector} for selector in selectors],
                "gotoOptions": {
                    "waitUntil": self.config.get("wait_until", "networkidle2"),
                    "timeout": service_timeout_ms,
                },
                "actionTimeout": service_timeout_ms,
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        envelope_success = payload.get("success") is True
        # The declared output *kind* is part of the public task contract, not
        # answer/reference data. A video-link task needs page hrefs; playback
        # resource tasks still exclude ordinary navigation links.
        urls = _resource_urls(
            payload,
            base_url=case.url,
            track=case.track,
            include_video_page_links=include_video_page_links,
        )
        error_type, error_message = _interface_error(
            response_status=response.status,
            envelope_success=envelope_success,
            origin_status=None,
        )
        errors = payload.get("errors")
        errors = errors if isinstance(errors, list) else []
        answer_key = "images" if case.track == "image" else "videos"
        return ProviderResult(
            ok=error_type is None,
            content="\n".join(urls) if envelope_success else "",
            status_code=response.status,
            final_url=case.url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "route": "browser_rendering_scrape_resources",
                "request_method": "POST",
                "request_summary": {
                    "selectors": selectors,
                    "wait_until": self.config.get("wait_until", "networkidle2"),
                },
                answer_key: urls,
                "resource_count": len(urls),
                "empty_content": envelope_success and not urls,
                "error_count": len(errors),
                "retry_after": response.headers.get("retry-after"),
                "browser_ms_used": response.headers.get("x-browser-ms-used"),
                "service_timeout_ms": service_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
            },
        )

    def _fetch_direct_document(
        self,
        case: EvalCase,
        *,
        account_id: str,
        http_timeout: float,
    ) -> ProviderResult:
        """Download a direct file URL, then use Cloudflare's file converter.

        The download and conversion share the provider call's single inner
        deadline.  The downloaded bytes are not public output; only their
        hash, size and non-secret transport metadata are retained.
        """
        started = time.monotonic()
        max_input_bytes = int(
            self.config.get("document_max_bytes", self.config.get("max_bytes", 20_000_000))
        )
        try:
            source = _get_document(
                case.url,
                timeout=http_timeout,
                max_bytes=max_input_bytes,
            )
        except TimeoutError:
            return ProviderResult(
                ok=False,
                content="",
                status_code=None,
                final_url=case.url,
                error_type="timeout",
                error_message="Cloudflare document download exceeded the call deadline",
                metadata={
                    "route": "workers_ai_tomarkdown",
                    "request_method": "GET then POST",
                    "source_elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    "conversion_attempted": False,
                    "client_http_timeout_ms": int(http_timeout * 1000),
                },
            )
        source_elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        if not 200 <= source.status < 300:
            return ProviderResult(
                ok=False,
                content="",
                status_code=source.status,
                final_url=source.url,
                error_type="http_status",
                error_message=f"Source returned HTTP {source.status}",
                metadata={
                    "_raw_response_b64": base64.b64encode(source.body).decode("ascii"),
                    "raw_response_sha256": hashlib.sha256(source.body).hexdigest(),
                    "raw_response_bytes": len(source.body),
                    "route": "workers_ai_tomarkdown",
                    "source_status": source.status,
                    "source_content_type": source.headers.get("content-type"),
                    "source_final_url": source.url,
                    "source_elapsed_ms": source_elapsed_ms,
                    "conversion_attempted": False,
                    "client_http_timeout_ms": int(http_timeout * 1000),
                },
            )

        remaining = http_timeout - (time.monotonic() - started)
        if remaining <= 0:
            return ProviderResult(
                ok=False,
                content="",
                status_code=source.status,
                final_url=source.url,
                error_type="timeout",
                error_message="Cloudflare document download exhausted the call deadline",
                metadata={
                    "route": "workers_ai_tomarkdown",
                    "source_status": source.status,
                    "source_sha256": hashlib.sha256(source.body).hexdigest(),
                    "source_bytes": len(source.body),
                    "source_content_type": source.headers.get("content-type"),
                    "source_final_url": source.url,
                    "source_elapsed_ms": source_elapsed_ms,
                    "conversion_attempted": False,
                    "client_http_timeout_ms": int(http_timeout * 1000),
                },
            )

        content_type = source.headers.get("content-type", "application/octet-stream").split(
            ";", 1
        )[0].strip().lower() or "application/octet-stream"
        filename = PurePosixPath(urlsplit(source.url).path).name or "document"
        if "." not in filename:
            suffix = {
                "application/pdf": ".pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            }.get(content_type, "")
            filename += suffix
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/tomarkdown"
        try:
            response = _post_multipart(
                endpoint,
                token=str(self.config.get("api_token", "")),
                filename=filename,
                content_type=content_type,
                data=source.body,
                timeout=remaining,
                max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            )
        except TimeoutError:
            return ProviderResult(
                ok=False,
                content="",
                status_code=None,
                final_url=source.url,
                error_type="timeout",
                error_message="Cloudflare document conversion exceeded the call deadline",
                metadata={
                    "route": "workers_ai_tomarkdown",
                    "request_method": "POST",
                    "source_status": source.status,
                    "source_sha256": hashlib.sha256(source.body).hexdigest(),
                    "source_bytes": len(source.body),
                    "source_content_type": content_type,
                    "source_final_url": source.url,
                    "source_elapsed_ms": source_elapsed_ms,
                    "conversion_attempted": True,
                    "conversion_http_timeout_ms": int(remaining * 1000),
                    "client_http_timeout_ms": int(http_timeout * 1000),
                },
            )
        conversion_elapsed_ms = round(
            (time.monotonic() - started) * 1000 - source_elapsed_ms, 3
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        envelope_success = payload.get("success") is True
        content = _markdown_conversion_content(payload)
        error_type, error_message = _interface_error(
            response_status=response.status,
            envelope_success=envelope_success,
            origin_status=source.status,
        )
        errors = payload.get("errors")
        errors = errors if isinstance(errors, list) else []
        return ProviderResult(
            ok=error_type is None,
            content=content if envelope_success else "",
            status_code=response.status,
            final_url=source.url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "route": "workers_ai_tomarkdown",
                "source_status": source.status,
                "source_sha256": hashlib.sha256(source.body).hexdigest(),
                "source_bytes": len(source.body),
                "source_content_type": content_type,
                "source_final_url": source.url,
                "source_elapsed_ms": source_elapsed_ms,
                "conversion_elapsed_ms": conversion_elapsed_ms,
                "conversion_attempted": True,
                "empty_content": envelope_success and not content,
                "error_count": len(errors),
                "retry_after": response.headers.get("retry-after"),
                "client_http_timeout_ms": int(http_timeout * 1000),
                "conversion_http_timeout_ms": int(remaining * 1000),
            },
        )


class CloudflareContentProvider(Provider):
    """Rendered HTML diagnostic. It is deliberately excluded from the readable-text leaderboard."""

    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        http_timeout = inner_timeout_budget(timeout)
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        response = request(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/content",
            method="POST",
            timeout=http_timeout,
            max_bytes=int(self.config.get("max_bytes", 20_000_000)),
            headers={"Authorization": f"Bearer {self.config.get('api_token', '')}"},
            json_body={
                "url": case.url,
                "gotoOptions": {
                    "waitUntil": self.config.get("wait_until", "networkidle2"),
                    "timeout": service_timeout_ms,
                },
                "actionTimeout": service_timeout_ms,
            },
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        content = payload.get("result") if isinstance(payload.get("result"), str) else ""
        errors = payload.get("errors")
        errors = errors if isinstance(errors, list) else []
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        upstream_status = _origin_status(meta.get("status"))
        envelope_success = payload.get("success") is True
        error_type, error_message = _interface_error(
            response_status=response.status,
            envelope_success=envelope_success,
            origin_status=upstream_status,
        )
        ok = error_type is None
        return ProviderResult(
            ok=ok,
            content=content if envelope_success else "",
            status_code=response.status,
            final_url=case.url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "output_contract": "rendered_html",
                "upstream_status": upstream_status,
                "empty_content": envelope_success and not content,
                "error_count": len(errors),
                "service_timeout_ms": service_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
            },
        )
