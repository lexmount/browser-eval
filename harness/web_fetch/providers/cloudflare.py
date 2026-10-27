from __future__ import annotations

import base64
import hashlib
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlencode, urlsplit

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.models import ProviderResult
from ..retrieval_hints import native_contract_for_case, native_default_case, plan_for_case
from lexbench_webfetch.paths import validate_http_url
from ..native_projection import native_answer_projection
from .base import Provider
from ..native_routes import NativeRoute
from .http_client import HttpOnlyRedirectHandler, HttpResponse, request

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
    def __init__(self, name, config):
        super().__init__(name, config)
        native = bool(config.get('uniform_native_parameters'))
        self._prepare_timeouts = self._native_timeouts if native else self._diagnostic_timeouts
        self._completion = self._native_completion if native else self._diagnostic_completion
        self._convert_document = self._native_document_route if native else self._diagnostic_document_route
        self._prepare_media = self._native_media if native else self._diagnostic_media

    @staticmethod
    def _native_timeouts(body, timeout_ms):
        return None

    @staticmethod
    def _diagnostic_timeouts(body, timeout_ms):
        if 'url' in body:
            body['gotoOptions']['timeout'] = timeout_ms
        body['actionTimeout'] = timeout_ms
        if 'waitForSelector' in body:
            body['waitForSelector']['timeout'] = timeout_ms
        return timeout_ms

    @staticmethod
    def _native_completion(case, body, timeout, service_timeout_ms):
        return None, None

    @staticmethod
    def _diagnostic_completion(case, body, timeout, service_timeout_ms):
        if case.track != 'dynamic' or native_default_case(case) or 'url' not in body:
            return None, None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('invalid dynamic retrieval_hints v1')
        plan = plan_for_case(case)
        contract = native_contract_for_case(case)
        return plan or (contract.completion_plan(service_timeout_ms) if contract else None), contract

    @staticmethod
    def _native_document_route(case):
        return False

    def _diagnostic_document_route(self, case):
        return self.config.get('browser') != 'kitesurf' and _uses_direct_document_route(case)

    @staticmethod
    def _native_media(body, include_video_page_links, timeout_ms):
        return None

    def _diagnostic_media(self, body, include_video_page_links, timeout_ms):
        if include_video_page_links:
            body['elements'] = [{'selector': 'a[href]'}]
        self._diagnostic_timeouts(body, timeout_ms)

    def native_route(self, case) -> NativeRoute:
        return NativeRoute('http', self.native_http_interface(case), ('result', 'meta.title', 'meta.finalUrl',))

    def native_http_interface(self, case):
        return ('/content' if self.type == 'cloudflare_content' else '/scrape'
                if case.task_kind == 'resource' and case.track in {'image', 'video'} else '/markdown')

    def native_http_arguments(self, case, timeout):
        return self._http_arguments(case, timeout, self.native_http_interface(case))

    def _http_arguments(self, case, timeout, interface):
        body = {'url': case.url, 'gotoOptions': {'waitUntil': self.config.get('wait_until', 'networkidle2')}}
        if interface == '/scrape':
            selectors = (['img', 'picture source'] if case.track == 'image' else
                         ['video', 'video source', 'audio', 'audio source', 'iframe', 'a[href]'])
            body['elements'] = [{'selector': value} for value in selectors]
        endpoint = 'https://api.cloudflare.com/client/v4/accounts/'+str(self.config.get('account_id', ''))+'/browser-rendering'+interface
        if interface != '/content' and self.config.get('browser'):
            endpoint += '?' + urlencode({'browser': self.config['browser']})
        return endpoint, self.native_http_options(body, {'Authorization': 'Bearer '+str(self.config.get('api_token', ''))}, timeout)

    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float, *, native_route_ledger=None) -> ProviderResult:
        self.check_native_call(case)
        return self._fetch_payload(case, timeout, native_route_ledger=native_route_ledger)

    def fetch_html(self, case: EvalCase, html: str, timeout: float) -> ProviderResult:
        """Run the product's documented inline-HTML route for frozen benchmarks."""
        return self._fetch_payload(
            case,
            timeout,
            {
                "html": html,
                "rejectResourceTypes": [
                    "document", "stylesheet", "image", "media", "font", "script", "texttrack",
                    "xhr", "fetch", "prefetch", "eventsource", "websocket", "manifest",
                    "signedexchange", "ping", "cspviolationreport", "preflight", "other",
                ],
            },
        )

    def _fetch_payload(self, case: EvalCase, timeout: float, request_body: dict | None = None, *, native_route_ledger=None) -> ProviderResult:
        account_id = str(self.config.get("account_id", ""))
        http_timeout = self.http_timeout_budget(timeout)
        if (
            case.task_kind == "resource"
            and case.track in {"image", "video"}
            and self.config.get("page_output") != "native_markdown"
        ):
            return self._fetch_media_resources(case, http_timeout=http_timeout, native_route_ledger=native_route_ledger)
        if self._convert_document(case):
            return self._fetch_direct_document(
                case,
                account_id=account_id,
                http_timeout=http_timeout,
            )
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        endpoint, arguments = self._http_arguments(case, http_timeout, '/markdown')
        # Inline HTML is a distinct diagnostic input. URL calls share exactly
        # the same native base request, adding only diagnostic readiness limits.
        if request_body is not None and "html" in request_body:
            arguments['json_body'] = dict(request_body)
        request_body = arguments['json_body']
        disclosed_timeout_ms = self._prepare_timeouts(request_body, service_timeout_ms)
        route = "browser_rendering_markdown"
        wait_metadata = None
        try:
            plan, native_contract = self._completion(case, request_body, timeout, service_timeout_ms)
        except (KeyError, TypeError, ValueError):
            return ProviderResult(False, "", error_type="configuration",
                                  error_message="invalid dynamic retrieval_hints v1")

        if plan is not None:
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
            }
            self._prepare_timeouts(request_body, effective_timeout_ms)
            # Quick Actions documents these as independent readiness timers, not
            # an ordered selector-then-delay chain. Supplying both can let the
            # shorter timer win, so choose the strongest single native signal.
            if plan.frame_selector:
                # The endpoint cannot target a child browsing context. For this
                # route a predeclared delay is stronger than the outer iframe,
                # which is normally present before its child content is ready.
                if plan.delay_ms:
                    request_body["waitForTimeout"] = plan.delay_ms
                    strategy = "predeclared_delay"
                else:
                    strategy = "native_completion_unsupported"
            else:
                request_body["waitForSelector"] = {"selector": plan.selector}
                self._prepare_timeouts(request_body, effective_timeout_ms)
                strategy = "selector"
            count_enforced = plan.frame_selector is None and plan.minimum_count == 1
            exact = count_enforced and plan.delay_ms == 0
            unsupported = []
            if plan.frame_selector:
                unsupported.extend(["child_frame_selector", "selector"])
            if plan.minimum_count != 1:
                unsupported.append("minimum_count")
            if plan.minimum_text_fragments:
                unsupported.append("minimum_text_fragments")
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
                "timeout_ms": (
                    effective_timeout_ms
                ),
                "client_http_timeout_ms": int(http_timeout * 1000),
                "wait_until": wait_until,
            }
        if native_contract is not None and native_contract.include_subdocuments:
            wait_metadata = dict(wait_metadata or {})
            wait_metadata["subdocuments"] = {
                "requested": True,
                "support": "unsupported",
                "harness_content_assembly": False,
            }
        response = self.native_http_request(case, request, endpoint, native_route_ledger=native_route_ledger, **arguments)
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
                "api_http_status": response.status,
                **({'native_answer_projection': native_answer_projection('cloudflare_markdown', case, payload, response.status, interface='/markdown')}
                   if (envelope_success and isinstance(payload.get('result', ''), str)) or meta else {}),
                "route": route,
                "wait": wait_metadata,
                "browser": self.config.get("browser"),
                "service_timeout_ms": disclosed_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
                "input_mode": "inline_html" if "html" in request_body else "url",
                "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                **({"raw_response": response.text()} if case.task_kind == "file" else {}),
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
        http_timeout: float,
        native_route_ledger=None,
    ) -> ProviderResult:
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        native = self._native_mode
        include_video_page_links = case.track == "video" and (native or any(
            isinstance(resource, dict) and resource.get("kind") == "video-link"
            for resource in case.expected_resources
        ))
        endpoint, arguments = self._http_arguments(case, http_timeout, '/scrape')
        request_body = arguments['json_body']
        self._prepare_media(request_body, include_video_page_links, service_timeout_ms)
        disclosed_timeout_ms = None if native else service_timeout_ms
        selectors = [item['selector'] for item in request_body['elements']]
        response = self.native_http_request(case, request, endpoint, native_route_ledger=native_route_ledger, **arguments)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        envelope_success = payload.get("success") is True
        from ..native_projection import project_native_answer, SRCSET_PARSER
        answer_key = 'images' if case.track == 'image' else 'videos'
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        effective_url = meta.get('finalUrl') or case.url
        projection = {'schema_version': 2, 'content_pointer': ['result'],
                      'content_format': 'json', 'result_fields': {'final_url': {
                          'pointer': ['meta', 'finalUrl'], 'fallback': 'request_url'}},
                      'answer_fields': {answer_key: {
                          'pointer': ['result'], 'format': 'html_attribute_urls',
                          'srcset_parser': SRCSET_PARSER, 'base_url': effective_url}}}
        answer, fields = ('', {})
        if envelope_success:
            answer, fields = project_native_answer(payload, projection)
        urls = fields.get(answer_key, [])
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
            content=answer,
            status_code=response.status,
            final_url=effective_url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "api_http_status": response.status,
                "route": "browser_rendering_scrape_resources",
                **({'native_answer_projection': (native_answer_projection('cloudflare_markdown', case, payload, response.status))}
                   if envelope_success or meta else {}),
                "browser": self.config.get("browser"),
                "endpoint_route": "/browser-rendering/scrape" + ("?" + urlencode({"browser": self.config["browser"]}) if self.config.get("browser") else ""),
                "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
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
                "service_timeout_ms": disclosed_timeout_ms,
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
                "api_http_status": response.status,
                **({'native_answer_projection': {'schema_version': 2,
                    'content_pointer': ['result'], 'content_format': 'text', 'optional_content': True,
                    'result_fields': {'final_url': {'format': 'transport_response_url'}}}}
                   if envelope_success and isinstance(payload.get('result', ''), str) else {}),
                "route": "workers_ai_tomarkdown",
                "source_status": source.status,
                "source_sha256": hashlib.sha256(source.body).hexdigest(),
                "source_bytes": len(source.body),
                "source_content_type": content_type,
                "source_final_url": source.url,
                "source_transport_receipt": {"response_url": source.url,
                    "body_sha256": hashlib.sha256(source.body).hexdigest()},
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


class CloudflareContentProvider(CloudflareMarkdownProvider):
    """Rendered HTML diagnostic. It is deliberately excluded from the readable-text leaderboard."""

    def validate(self) -> list[str]:
        return [name for name in ("api_token", "account_id") if not self.config.get(name)]

    def fetch(self, case: EvalCase, timeout: float, *, native_route_ledger=None) -> ProviderResult:
        self.check_native_call(case)
        http_timeout = self.http_timeout_budget(timeout)
        service_timeout_ms = min(max(1, int(http_timeout * 1000)), 60_000)
        endpoint, arguments = self._http_arguments(case, http_timeout, '/content')
        disclosed_timeout_ms = self._prepare_timeouts(arguments['json_body'], service_timeout_ms)
        response = self.native_http_request(case, request, endpoint, native_route_ledger=native_route_ledger, **arguments)
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
            final_url=meta.get("finalUrl") or case.url,
            error_type=error_type,
            error_message=error_message,
            metadata={
                "_raw_response_b64": base64.b64encode(response.body).decode("ascii"),
                "raw_response_sha256": hashlib.sha256(response.body).hexdigest(),
                "raw_response_bytes": len(response.body),
                "api_http_status": response.status,
                **({'native_answer_projection': native_answer_projection('cloudflare_content', case, payload, response.status)}
                   if (envelope_success and isinstance(payload.get('result', ''), str)) or meta else {}),
                "output_contract": "rendered_html",
                "upstream_status": upstream_status,
                "empty_content": envelope_success and not content,
                "error_count": len(errors),
                "service_timeout_ms": disclosed_timeout_ms,
                "client_http_timeout_ms": int(http_timeout * 1000),
            },
        )
