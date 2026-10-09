"""Official one-shot Moli CLI, with stdout kept separate from diagnostics."""

from __future__ import annotations

import base64
from contextlib import ExitStack
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from ..document_text import extract_document_text
from ..markdown import CONVERTER, converter_command, require_converter
from ..models import EvalCase, ProviderResult
from ..retrieval_hints import plan_for_case
from ..safety import validate_http_url
from .base import Provider

_WAIT_UNTIL_VALUES = {
    "done",
    "load",
    "domcontentloaded",
    "networkidle",
    "domstable",
}
_PUBLIC_HOSTNAME = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)

_MARKDOWN_HTTP_ERROR_PAGE = re.compile(
    r"^\s*(?:\*\*)?error\s*:\s*(?:\*\*)?\s*forbidden\s*"
    r"(?:\r?\n)+\s*(?:\*\*)?(?P<status>4\d\d|5\d\d)(?:\*\*)?\s*:\s*forbidden"
    r"(?:\s*id\s*:.*)?\s*$",
    re.IGNORECASE,
)


def _markdown_http_error_status(content: str) -> int | None:
    """Recognize Moli's whole-response HTTP error page, not status prose."""
    if len(content) > 1024:
        return None
    match = _MARKDOWN_HTTP_ERROR_PAGE.fullmatch(content)
    return int(match.group("status")) if match else None


def _diagnostic_main_document_url(stderr: str | bytes, requested_url: str) -> str | None:
    """Read Moli's effective main-document URL from the same call's debug log."""
    pattern = re.compile(
        rb"fetch request complete method=(?:GET|POST) url=(?P<url>\S+) "
        rb"final_url=(?P<final_url>\S+) status=(?P<status>\d{3})\b"
    )

    diagnostic = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
    navigation_pattern = re.compile(
        rb"following pending location navigation asynchronously[^\n]* url=(?P<url>\S+)"
    )

    def field(row: re.Match[bytes], name: str) -> str:
        return row.group(name).decode("utf-8", "replace")

    def identity(value: str) -> tuple | None:
        try:
            validate_http_url(value)
            parts = urlsplit(value)
            return (
                parts.scheme.casefold(),
                (parts.hostname or "").casefold(),
                parts.port if parts.port is not None else (443 if parts.scheme.casefold() == "https" else 80),
                re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(),
                       quote(parts.path, safe="/%:@!$&'()*+,;=")).rstrip("/") or "/",
                sorted(parse_qsl(parts.query, keep_blank_values=True)),
            )
        except ValueError:
            return None

    requested_identity = identity(requested_url)
    if requested_identity is None:
        return None
    rows = list(pattern.finditer(diagnostic))

    # Legacy completion records do not identify redirect hops or request owners.
    # Never infer the document from GET ordering or challenge query parameters.
    row = next((candidate for candidate in rows
                if identity(field(candidate, "url")) == requested_identity), None)
    if row is None or 300 <= int(field(row, "status")) < 400:
        return None
    navigations = list(navigation_pattern.finditer(diagnostic))
    for index, navigation in enumerate(navigations):
        boundary = navigations[index + 1].start() if index + 1 < len(navigations) else len(diagnostic)
        committed = diagnostic.find(
            b"committed main navigation before replacement realm bootstrap",
            navigation.end(), boundary,
        )
        if committed < 0:
            continue
        navigation_identity = identity(field(navigation, "url"))
        if navigation_identity is None:
            return None
        matches = [candidate for candidate in rows
                   if navigation.end() <= candidate.start() < boundary
                   and identity(field(candidate, "url")) == navigation_identity]
        if len(matches) != 1 or 300 <= int(field(matches[0], "status")) < 400:
            return None
        row = matches[0]
    candidate = field(row, "final_url")
    if identity(candidate) is None:
        return None
    try:
        validate_http_url(candidate)
    except ValueError:
        return None
    return candidate



_RESOURCE_PAGE_EXPRESSION = r"""
(async () => {
  const urls = new Set();
  const add = (value, base = location.href) => {
    if (!value) return;
    for (const part of String(value).split(',')) {
      const raw = part.trim().split(/\s+/)[0];
      if (!raw || /^(?:data|blob|javascript|mailto):/i.test(raw)) continue;
      try { urls.add(new URL(raw, base).href); } catch {}
    }
  };

  // Enumerate the top document and bounded, already-loaded same-origin frame
  // documents.  This is answer-free: it follows the page's own document
  // hierarchy and never receives an expected URL or case-specific selector.
  const roots = [document];
  for (let cursor = 0; cursor < roots.length && roots.length < 16; cursor++) {
    for (const frame of roots[cursor].querySelectorAll('iframe,frame')) {
      try {
        if (frame.contentDocument && !roots.includes(frame.contentDocument)) {
          roots.push(frame.contentDocument);
          if (roots.length >= 16) break;
        }
      } catch {}
    }
  }
  for (const root of roots) {
    for (const node of root.querySelectorAll('[src],[href],[data],[poster],[srcset]')) {
      for (const name of ['src', 'href', 'data', 'poster', 'srcset']) {
        add(node.getAttribute(name), node.baseURI || root.baseURI || location.href);
      }
    }
  }
  const queue = roots.flatMap(root => [...root.querySelectorAll(
      'astro-island[component-url],script[type="module"][src]'
    )].map(node => ({
      value: node.getAttribute('component-url') || node.getAttribute('src'),
      base: node.baseURI || root.baseURI || location.href
    })))
    .filter(item => item.value).slice(0, 16)
    .map(item => ({url: new URL(item.value, item.base).href, depth: 0}));
  const seen = new Set();
  while (queue.length && seen.size < 48) {
    const item = queue.shift(), moduleUrl = item.url;
    if (seen.has(moduleUrl) || new URL(moduleUrl).origin !== location.origin) continue;
    seen.add(moduleUrl);
    try {
      const source = await (await fetch(moduleUrl)).text();
      for (const match of source.matchAll(/https?:\/\/[^\s`"'<>]+/g)) add(match[0]);
      if (item.depth < 2) {
        for (const match of source.matchAll(/(?:from\s*|import\s*)[('"`]([^'"`]+)[)'"`]/g)) {
          try {
            const imported = new URL(match[1], moduleUrl).href;
            if (new URL(imported).origin === location.origin) {
              queue.push({url: imported, depth: item.depth + 1});
            }
          } catch {}
        }
      }
    } catch {}
  }
  return JSON.stringify({links: [...urls]});
})()
"""

_DOCUMENT_PAGE_EXPRESSION = r"""
(async () => {
  const app = globalThis.PDFViewerApplication;
  if (app) {
    await app.initializedPromise;
    const pdf = app.pdfDocument;
    if (pdf) {
      const pages = [];
      for (let number = 1; number <= pdf.numPages; number++) {
        const page = await pdf.getPage(number), text = await page.getTextContent();
        pages.push(text.items.map(item => item.str).join(' '));
      }
      return JSON.stringify({
        kind: 'text', source: new URL(app.url, location.href).href,
        text: pages.join('\n\n')
      });
    }
  }
  const nodes = [...document.querySelectorAll('embed[src],iframe[src],object[data],a[href]')];
  const source = nodes.map(node => node.src || node.data || node.href)
    .find(value => value && /\.pdf(?:$|[?#])/i.test(value));
  if (!source) return JSON.stringify({kind: 'page_text', text: document.body?.innerText || ''});
  const response = await fetch(new URL(source, location.href));
  if (!response.ok) throw new Error(`embedded PDF HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  }
  return JSON.stringify({kind: 'pdf_base64', source: response.url, data: btoa(binary)});
})()
"""

_DOCUMENT_PAGE_READY = r"""
() => {
  const viewer = document.querySelector('#viewer');
  if (viewer) return !!globalThis.PDFViewerApplication?.pdfDocument;
  return !!document.querySelector('embed[src],iframe[src],object[data],a[href*=".pdf"]')
    || !!document.body?.innerText?.trim();
}
"""

_DOCUMENT_RUNTIME_FETCH_EXPRESSION = r"""
(async () => {
  const response = await fetch(location.href, {credentials: 'include'});
  if (!response.ok) throw new Error(`document HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  }
  return JSON.stringify({
    kind: 'document_base64', source: response.url, status: response.status,
    content_type: response.headers.get('content-type'), data: btoa(binary)
  });
})()
"""


_MEDIA_METADATA_EXPRESSION = r"""
(() => {
  const values = [];
  const add = value => {
    const text = String(value || '').trim();
    if (text && !values.includes(text)) values.push(text);
  };
  add(document.title);
  add(document.body?.innerText);
  for (const node of document.querySelectorAll(
    'meta[name="author"],meta[property$=":author"],meta[itemprop="author"],'
    + '[itemprop="author"],[itemprop="creator"],[itemprop="publisher"]'
  )) {
    add(node.content);
    add(node.getAttribute?.('content'));
    add(node.innerText);
    for (const name of node.querySelectorAll?.('[itemprop="name"]') || []) {
      add(name.content);
      add(name.getAttribute?.('content'));
      add(name.innerText);
    }
  }
  let visited = 0;
  const visit = (value, depth = 0) => {
    if (++visited > 2048 || depth > 6 || value == null) return;
    if (Array.isArray(value)) {
      for (const item of value.slice(0, 64)) visit(item, depth + 1);
      return;
    }
    if (typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value).slice(0, 128)) {
      if (/^(?:author|creator|publisher)$/i.test(key)) {
        if (typeof item === 'string') add(item);
        else if (item && typeof item === 'object') add(item.name);
      }
      visit(item, depth + 1);
    }
  };
  for (const script of [...document.querySelectorAll('script[type="application/ld+json"]')]
    .slice(0, 16)) {
    try { visit(JSON.parse(script.textContent)); } catch {}
  }
  return values.join('\n\n');
})()
"""


def _dynamic_text_expression(plan) -> str:
    """Build an answer-free rendered-text extraction for the declared scope."""
    extraction_selector = plan.extraction_selector or plan.selector
    selector = json.dumps(extraction_selector, ensure_ascii=False)
    extraction_frame = plan.extraction_frame_selector if plan.version == 2 else plan.frame_selector
    if extraction_frame:
        frame = json.dumps(extraction_frame, ensure_ascii=False)
        root = f"document.querySelector({frame})?.contentDocument"
    else:
        root = "document"
    maximum_count = plan.extraction_maximum_count if plan.version == 2 else plan.minimum_count
    limit = ".slice(0, " + str(maximum_count) + ")"
    exclusions = json.dumps(list(plan.extraction_exclude_selectors), ensure_ascii=False)
    fallback = (
        "((root?.body || root?.documentElement || document.body || "
        "document.documentElement)?.innerText?.trim() || '')"
        if plan.extraction_fallback == "body"
        else "''"
    )
    return (
        "(() => { const root = "
        + root
        + "; const selected = root ? [...root.querySelectorAll("
        + selector
        + ")]"
        + limit
        + " : []; const exclusions = "
        + exclusions
        + "; const lines = node => (node.innerText || node.textContent || '').split(/\\n+/)"
        + ".map(value => value.trim()).filter(Boolean); const text = node => { const values = "
        + "lines(node); if (!exclusions.length) return values; const omitted = new Set("
        + "exclusions.flatMap(item => [...node.querySelectorAll(item)].flatMap(lines))); return "
        + "values.filter(value => !omitted.has(value)); }; const records = selected.map(node => "
        + "text(node).join('\\n'))"
        + ".filter(Boolean); const fallback = "
        + fallback
        + "; return records.length ? records.join('\\n\\n') : fallback; })()"
    )


_ROUTE_OUTPUTS = {
    "raw_file": "raw_bytes",
    "document_runtime_fetch": "runtime_document_base64+local_text",
    "raw_document": "raw_document_bytes+local_text",
    "document_page_runtime": "document_text_or_embedded_document",
    "resource_page_runtime": "structured_resource_urls",
    "challenge_json_trace": "final_dom_html+local_markdown+network_diagnostics",
    "page_identity_json": "final_dom_html+local_markdown",
    "dynamic_selected_text": "selected_records_or_rendered_body_fallback",
    "media_metadata_runtime": "visible_text_and_standard_media_metadata",
    "ordinary_markdown": "markdown",
}


def _route_for_case(case: EvalCase) -> str:
    """Select one output contract before applying page-family extraction rules."""
    if case.task_kind == "file":
        return "raw_file"
    if case.task_kind == "resource":
        return "resource_page_runtime"
    if case.track == "document":
        path = urlsplit(case.url).path.casefold()
        if path.endswith(".docx"):
            return "document_runtime_fetch"
        if path.endswith(".pdf") or "/pdf/" in path:
            return "raw_document"
        if path.endswith((".html", "/")):
            return "document_page_runtime"
    navigation = ((case.source_benchmark or {}).get("risk_control") or {}).get("navigation_contract")
    if case.track == "challenge":
        return "challenge_json_trace"
    if (case.task_kind == "body" and isinstance(navigation, dict)
            and navigation.get("requested_url") and navigation.get("observed_final_url")):
        return "page_identity_json"
    if case.track == "dynamic":
        return "dynamic_selected_text"
    if case.track == "video" and case.task_kind == "body":
        return "media_metadata_runtime"
    return "ordinary_markdown"


class MoliWebFetchProvider(Provider):
    def __init__(self, name, config):
        super().__init__(name, config)
        self._process_lock = threading.Lock()
        self._cancel_requested = False
        self._active_process = None

    def begin_call(self):
        with self._process_lock:
            self._cancel_requested = False
            self._active_process = None

    def _resolve_binary(self) -> str | None:
        configured = os.path.expandvars(
            os.path.expanduser(str(self.config.get("binary") or "moli"))
        )
        return shutil.which(configured)

    def _configuration_errors(self, binary: str | None) -> list[str]:
        errors = []
        from ..network_dns import validate_dns_mode, validate_native_dns_library
        try:
            if validate_dns_mode(self.config.get("dns_mode", "system")) == "public_doh":
                validate_native_dns_library(self.config.get("native_dns_library"))
        except ValueError as error:
            errors.append(str(error))
        if not binary:
            errors.append("binary (set MOLI_BINARY or install moli on PATH)")
        expected_version = self.config.get("expected_reported_version")
        if not isinstance(expected_version, str) or not expected_version.strip():
            errors.append("expected_reported_version")
        expected_sha256 = self.config.get("expected_binary_sha256")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            errors.append("expected_binary_sha256")
        if self.config.get("wait_until", "done") not in _WAIT_UNTIL_VALUES:
            errors.append("wait_until")
        if int(self.config.get("max_bytes", 20_000_000)) <= 0:
            errors.append("max_bytes")
        retain_diagnostic = self.config.get("retain_complete_diagnostic_log", False)
        if not isinstance(retain_diagnostic, bool):
            errors.append("retain_complete_diagnostic_log")
        if retain_diagnostic and int(self.config.get("max_diagnostic_bytes", 100_000_000)) <= 0:
            errors.append("max_diagnostic_bytes")
        if not retain_diagnostic and "max_diagnostic_bytes" in self.config:
            errors.append("max_diagnostic_bytes (requires retain_complete_diagnostic_log)")
        if self.config.get("log_level") not in {
            None, "debug", "info", "warn", "error", "fatal"
        }:
            errors.append("log_level")
        if self.config.get("trace_network") not in {None, False}:
            errors.append("trace_network (requires JSON output and is not allowed for Markdown scoring)")
        if not isinstance(
            self.config.get("local_report_complete_diagnostic_links", False), bool
        ):
            errors.append("local_report_complete_diagnostic_links")
        elif self.config.get("local_report_complete_diagnostic_links") and not retain_diagnostic:
            errors.append(
                "local_report_complete_diagnostic_links (requires retain_complete_diagnostic_log)"
            )
        allowlist = self.config.get("private_network_allowlist_hosts", [])
        if self.config.get("uniform_native_parameters") and allowlist:
            errors.append("private_network_allowlist_hosts cannot vary uniform native parameters")
        if (
            not isinstance(allowlist, list)
            or any(
                not isinstance(host, str)
                or host != host.casefold()
                or not _PUBLIC_HOSTNAME.fullmatch(host)
                for host in allowlist
            )
            or len(set(allowlist)) != len(allowlist)
        ):
            errors.append("private_network_allowlist_hosts")
        return errors

    def validate(self) -> list[str]:
        binary = self._resolve_binary()
        return self._validate_resolved_binary(binary)

    def _validate_resolved_binary(self, binary: str | None) -> list[str]:
        for attribute in (
            "_binary_sha256",
            "_reported_version",
            "_binary_identity",
            "_implementation_identity",
        ):
            if hasattr(self, attribute):
                delattr(self, attribute)
        errors = self._configuration_errors(binary)
        if not errors:
            assert binary is not None
            try:
                with open(binary, "rb") as source:
                    binary_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
                stat = Path(binary).stat()
                reported_version = subprocess.check_output(
                    [binary, "--version"],
                    text=True,
                    stderr=subprocess.STDOUT,
                    timeout=10,
                ).strip()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                errors.append("Moli version probe")
            except OSError:
                errors.append("binary (unreadable)")
            else:
                expected_version = self.config["expected_reported_version"].strip()
                expected_sha256 = self.config["expected_binary_sha256"]
                if reported_version != expected_version:
                    errors.append(f"Moli reported version {expected_version}")
                if binary_sha256 != expected_sha256:
                    errors.append(f"Moli binary SHA-256 {expected_sha256}")
                if not errors:
                    self._binary_sha256 = binary_sha256
                    self._reported_version = reported_version
                    self._binary_identity = (
                        binary,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                    )
                    self._implementation_identity = {
                        "reported_version": reported_version,
                        "binary_sha256": binary_sha256,
                    }
                    if self.config.get("dns_mode", "system") == "public_doh":
                        from ..network_dns import validate_native_dns_library
                        _, binding = validate_native_dns_library(self.config.get("native_dns_library"))
                        self._implementation_identity["native_dns"] = binding
        return errors

    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        self.check_native_call(case)
        validate_http_url(case.url)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        try:
            # Validate the dynamic contract before selecting any content route.
            # A future dynamic file/document case must not bypass fail-closed
            # retrieval planning merely because it takes the raw-byte path.
            retrieval_plan = plan_for_case(case)
        except ValueError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=str(error),
            )
        route = _route_for_case(case)
        if retrieval_plan and (route == "raw_file" or (
            retrieval_plan.version == 2 and route != "dynamic_selected_text"
        )):
            return ProviderResult(
                False, "", error_type="configuration",
                error_message=(
                    "DOM completion cannot be applied to raw file delivery"
                    if route == "raw_file" else
                    "Declared text extraction requires the dynamic text route"
                ),
            )
        call_deadline = time.monotonic() + timeout
        binary = self._resolve_binary()
        errors = self._configuration_errors(binary)
        if not errors and not hasattr(self, "_implementation_identity"):
            errors = self._validate_resolved_binary(binary)
        if errors:
            return ProviderResult(
                False, "", error_type="configuration", error_message="; ".join(errors)
            )
        wait = self.config.get("wait_until", "done")
        file_task = route == "raw_file"
        document_task = route in {"raw_document", "document_runtime_fetch"}
        document_runtime_fetch_task = route == "document_runtime_fetch"
        document_page_task = route == "document_page_runtime"
        media_metadata_task = route == "media_metadata_runtime"
        challenge_task = route == "challenge_json_trace"
        json_page_task = route in {"challenge_json_trace", "page_identity_json"}
        if json_page_task:
            try:
                markdown_package = require_converter(self.config.get('node_html_markdown_path'))
            except ValueError as error:
                return ProviderResult(False, '', error_type='configuration', error_message=str(error))
        raw_task = file_task or (document_task and not document_runtime_fetch_task)
        timeout_ms = str(max(1, int(timeout * 1000)))
        if raw_task:
            # Direct file delivery uses Moli's automatic response mode. Forcing
            # --dump html/markdown can reject or serialize a non-HTML response
            # before the evaluator receives the original bytes.
            dump = None
            command = [
                binary,
                "fetch",
                "--timeout",
                timeout_ms,
                "--http-timeout",
                timeout_ms,
            ]
            wait_metadata = None
        else:
            dump = "html" if case.task_kind == "resource" else "markdown"
            try:
                if document_runtime_fetch_task:
                    wait_arguments = [
                        "--eval",
                        _DOCUMENT_RUNTIME_FETCH_EXPRESSION,
                        "--wait-until",
                        wait,
                    ]
                    wait_metadata = {
                        "wait_until": wait,
                        "document_delivery": "page_runtime_second_http_fetch",
                        "completion_support": "exact",
                    }
                    dump = None
                elif document_page_task:
                    wait_arguments = [
                        "--eval",
                        _DOCUMENT_PAGE_EXPRESSION,
                        "--wait-script",
                        _DOCUMENT_PAGE_READY,
                        "--wait-until",
                        wait,
                    ]
                    wait_metadata = {
                        "wait_until": wait,
                        "embedded_document": "page_runtime",
                        "completion_support": "exact",
                    }
                    dump = None
                elif json_page_task:
                    wait_arguments = (["--trace-network"] if challenge_task else []) + ["--wait-until", wait]
                    wait_metadata = {
                        "wait_until": wait,
                        "extraction": "json_final_dom_then_local_markdown",
                        "network_trace": "metadata_without_response_bodies" if challenge_task else "not_requested",
                    }
                    dump = "json"
                elif route == "resource_page_runtime":
                    wait_arguments = ["--eval", _RESOURCE_PAGE_EXPRESSION, "--wait-until", wait]
                    wait_metadata = {
                        "wait_until": wait,
                        "resource_discovery": "rendered_dom_and_same_origin_component_modules",
                    }
                    dump = None
                elif route == "dynamic_selected_text":
                    plan = retrieval_plan
                    assert plan is not None
                    wait_arguments = ["--eval", _dynamic_text_expression(plan)]
                    dump = None
                    wait_metadata = {
                        "extraction": (
                            "declared_scope_or_rendered_body_fallback"
                            if plan.version == 2
                            else "selected_records_or_rendered_body_fallback"
                        ),
                    }
                    if plan.version == 2:
                        wait_metadata["extraction_scope"] = {
                            "selector": plan.extraction_selector,
                            "maximum_count": plan.extraction_maximum_count,
                            "frame_selector": plan.extraction_frame_selector,
                            "exclude_selectors": list(plan.extraction_exclude_selectors),
                            "fallback": plan.extraction_fallback,
                        }
                elif media_metadata_task:
                    wait_arguments = [
                        "--eval",
                        _MEDIA_METADATA_EXPRESSION,
                        "--wait-until",
                        wait,
                    ]
                    wait_metadata = {
                        "wait_until": wait,
                        "extraction": "visible_text_and_standard_media_metadata",
                    }
                    dump = None
                else:
                    wait_arguments = ["--wait-until", wait]
                    wait_metadata = {"wait_until": wait, **({"strategy": "native_default_completion"} if case.track == "dynamic" else {})}
            except ValueError as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message=str(error),
                )
            if retrieval_plan:
                plan = retrieval_plan
                # Completion is independent of the selected output representation.
                # Replace the default navigation wait without losing route flags.
                if "--wait-until" in wait_arguments:
                    index = wait_arguments.index("--wait-until")
                    del wait_arguments[index:index + 2]
                wait_arguments.extend([
                    "--wait-selector" if plan.wait_kind == "selector" else "--wait-script",
                    plan.wait_value, "--wait-until", plan.wait_until,
                ])
                if plan.delay_ms:
                    wait_arguments.extend(["--delay-ms", str(plan.delay_ms)])
                wait_metadata.update({
                    "strategy": (
                        "frame_selector_count_script" if plan.frame_selector else
                        "selector" if plan.wait_kind == "selector" else "selector_count_script"
                    ),
                    "selector": plan.selector,
                    "minimum_count": plan.minimum_count,
                    "frame_selector": plan.frame_selector,
                    "delay_ms": plan.delay_ms,
                    "timeout_ms": plan.timeout_ms,
                    "wait_until": plan.wait_until,
                    "completion_support": "exact",
                })
                if plan.minimum_text_fragments:
                    wait_metadata["minimum_text_fragments"] = plan.minimum_text_fragments
                timeout_ms = str(min(int(timeout_ms), plan.timeout_ms))
            command = [binary, "fetch"]
            if dump is not None:
                command.extend(["--dump", dump])
            command.extend(
                [
                    *wait_arguments,
                    "--timeout",
                    timeout_ms,
                    "--http-timeout",
                    timeout_ms,
                ]
            )
        # Forward the common limits to the native transport as well as stdout.
        command.extend(["--http-connect-timeout", timeout_ms,
                        "--http-max-response-size", str(int(self.config.get("max_bytes", 20_000_000)))])
        if self.config.get("uniform_native_parameters"):
            # These are product timers, not the runner's wall-clock deadline.
            for flag in ("--timeout", "--http-timeout", "--http-connect-timeout"):
                if flag in command:
                    index = command.index(flag)
                    del command[index:index + 2]
        block_private_networks = bool(self.config.get("block_private_networks", True))
        requested_host = (urlsplit(case.url).hostname or "").casefold()
        private_network_guard_exception = block_private_networks and requested_host in set(
            self.config.get("private_network_allowlist_hosts", [])
        )
        if block_private_networks and not private_network_guard_exception:
            command.append("--block-private-networks")
        log_level = self.config.get("log_level")
        if log_level:
            command.extend(["--log-level", str(log_level)])
        command.append(case.url)
        limit = int(self.config.get("max_bytes", 20_000_000))
        retain_diagnostic = bool(self.config.get("retain_complete_diagnostic_log", False))
        diagnostic_limit = int(self.config.get("max_diagnostic_bytes", 100_000_000))
        diagnostic_read_limit = diagnostic_limit if retain_diagnostic else limit
        metadata = {
            "dump": dump,
            "http_connect_timeout_ms": None if self.config.get("uniform_native_parameters") else int(timeout_ms),
            "internal_timeout_policy": "native_default" if self.config.get("uniform_native_parameters") else "explicit",
            "http_max_response_bytes": int(self.config.get("max_bytes", 20_000_000)),
            "route": route,
            "output": _ROUTE_OUTPUTS[route],
            "wait": wait_metadata,
            "wait_until": None if raw_task else wait_metadata.get("wait_until"),
            "binary": Path(binary).name,
            "block_private_networks": block_private_networks,
            "private_network_guard_exception": private_network_guard_exception,
            "private_network_guard_exception_host": (
                requested_host if private_network_guard_exception else None
            ),
            "log_level": log_level,
            "network_trace_requested": challenge_task,
            "local_report_complete_diagnostic_links": bool(
                self.config.get("local_report_complete_diagnostic_links", False)
            ),
        }
        try:
            if hasattr(self, "_binary_identity"):
                stat = Path(binary).stat()
                if (binary, stat.st_ino, stat.st_size, stat.st_mtime_ns) != self._binary_identity:
                    return ProviderResult(
                        False,
                        "",
                        error_type="configuration",
                        error_message="Moli binary changed; validate runtime before measurement",
                    )
            if (
                getattr(self, "_reported_version", None)
                != self.config["expected_reported_version"].strip()
                or getattr(self, "_binary_sha256", None) != self.config["expected_binary_sha256"]
            ):
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message="Moli runtime identity does not match the configured pins",
                )
            metadata["binary_sha256"] = getattr(self, "_binary_sha256", None)
            metadata["reported_version"] = getattr(self, "_reported_version", None)
        except OSError as error:
            return ProviderResult(
                False,
                "",
                error_type="configuration",
                error_message=f"Cannot read Moli binary: {error}",
                metadata=metadata,
            )
        # Spool artifacts to files rather than retaining arbitrary CLI output in RAM.
        with ExitStack() as dns_stack, ExitStack() as spool_stack:
            sink = getattr(self, "_call_diagnostics", None)
            output = spool_stack.enter_context(
                Path(str(sink.path) + ".moli.stdout").open("x+b") if sink else tempfile.TemporaryFile())
            diagnostic = spool_stack.enter_context(
                Path(str(sink.path) + ".moli.stderr").open("x+b") if sink else tempfile.TemporaryFile())
            if sink: sink.emit("caller", "native_cli_start", command=command)
            from ..network_dns import NativeDnsBroker, validate_native_dns_library
            dns_broker = None
            process_environment = None
            metadata["dns_mode"] = self.config.get("dns_mode", "system")
            if metadata["dns_mode"] == "public_doh":
                library, binding = validate_native_dns_library(self.config.get("native_dns_library"))
                dns_broker = dns_stack.enter_context(NativeDnsBroker(diagnostics=sink))
                remaining = call_deadline - time.monotonic()
                if remaining <= 0:
                    return ProviderResult(False, "", error_type="timeout", error_message="Moli fetch exceeded time budget", metadata=metadata)
                dns_broker.begin_call(remaining)
                process_environment = dns_broker.environment(library)
                metadata["native_dns_binding"] = binding
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=diagnostic,
                    start_new_session=os.name == "posix",
                    env=process_environment,
                )
                with self._process_lock:
                    self._active_process = process
                    if self._cancel_requested:
                        self._kill(process)
            except OSError as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="configuration",
                    error_message=f"Cannot start Moli: {error}",
                    metadata=metadata,
                )
            with process:
                stopped = None
                while True:
                    if os.fstat(output.fileno()).st_size > limit:
                        stopped = "output_too_large"
                        break
                    if os.fstat(diagnostic.fileno()).st_size > diagnostic_read_limit:
                        stopped = (
                            "diagnostic_log_too_large" if retain_diagnostic
                            else "output_too_large"
                        )
                        break
                    if process.poll() is not None:
                        break
                    remaining = call_deadline - time.monotonic()
                    if remaining <= 0:
                        stopped = "timeout"
                        break
                    try:
                        process.wait(timeout=min(0.05, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                if stopped:
                    self._kill(process)
                    process.wait()
                if dns_broker is not None:
                    metadata["dns_queries"] = list(dns_broker.traces)
                stdout_bytes = os.fstat(output.fileno()).st_size
                stderr_bytes = os.fstat(diagnostic.fileno()).st_size
                output.seek(0)
                raw = output.read(limit + 1)
                diagnostic.seek(0)
                diagnostic_raw = diagnostic.read(diagnostic_read_limit + 1)
                public_preview_limit = 1_000_000 if retain_diagnostic else limit
                stderr = diagnostic_raw[:public_preview_limit].decode("utf-8", "replace")
                metadata.update(
                    exit_code=process.returncode,
                    stdout_bytes=stdout_bytes,
                    stdout_truncated=stdout_bytes > limit,
                    stderr=stderr,
                    stderr_bytes=stderr_bytes,
                    stderr_truncated=stderr_bytes > public_preview_limit,
                )
                if retain_diagnostic:
                    metadata.update(
                        diagnostic_log_sha256=hashlib.sha256(diagnostic_raw).hexdigest(),
                        diagnostic_log_bytes=len(diagnostic_raw),
                        diagnostic_log_truncated=stopped == "diagnostic_log_too_large",
                        diagnostic_log_process_completed=stopped is None,
                        _diagnostic_log_b64=base64.b64encode(diagnostic_raw).decode("ascii"),
                    )
        if stopped == "timeout":
            return ProviderResult(
                False,
                "",
                error_type="timeout",
                error_message="Moli fetch exceeded time budget",
                metadata=metadata,
            )
        if stopped == "diagnostic_log_too_large":
            return ProviderResult(
                False,
                "",
                error_type="diagnostic_log_too_large",
                error_message=(
                    "Moli diagnostic log exceeded max_diagnostic_bytes; "
                    "the cohort must not be certified with a truncated log"
                ),
                metadata=metadata,
            )
        if stopped == "output_too_large" or len(raw) > limit:
            return ProviderResult(
                False,
                "",
                error_type="output_too_large",
                error_message="Moli stdout exceeds max_bytes",
                metadata=metadata,
            )
        if time.monotonic() >= call_deadline:
            return ProviderResult(
                False,
                "",
                error_type="timeout",
                error_message="Moli fetch exceeded time budget",
                metadata=metadata,
            )
        if file_task:
            ok = process.returncode == 0
            return ProviderResult(
                ok,
                "",
                files=[{"data": raw}] if raw else [],
                error_type=None if ok else "tool_error",
                error_message=None if ok else stderr or "Moli file request failed",
                metadata=metadata,
            )
        if document_runtime_fetch_task:
            if process.returncode != 0:
                return ProviderResult(
                    False,
                    "",
                    error_type="tool_error",
                    error_message=stderr or "Moli document runtime fetch failed",
                    metadata=metadata,
                )
            try:
                envelope = json.loads(raw.decode("utf-8"))
                if not isinstance(envelope, dict) or envelope.get("kind") != "document_base64":
                    raise ValueError("unknown document envelope kind")
                document_bytes = base64.b64decode(envelope["data"], validate=True)
                if not document_bytes.startswith(b"PK"):
                    raise ValueError("runtime response is not an OOXML ZIP document")
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as error:
                metadata.update(
                    raw_stdout_sha256=hashlib.sha256(raw).hexdigest(),
                    _raw_stdout_b64=base64.b64encode(raw).decode("ascii"),
                )
                return ProviderResult(
                    False,
                    "",
                    error_type="invalid_output",
                    error_message=f"Moli document runtime output is invalid: {error}",
                    metadata=metadata,
                )
            metadata.update(
                document_runtime_source=envelope.get("source"),
                document_runtime_status=envelope.get("status"),
                document_runtime_content_type=envelope.get("content_type"),
                document_runtime_second_http_fetch=True,
                raw_stdout_sha256=hashlib.sha256(document_bytes).hexdigest(),
                _raw_stdout_b64=base64.b64encode(document_bytes).decode("ascii"),
            )
            try:
                content, extractor = extract_document_text(document_bytes)
            except Exception as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="document_parse_error",
                    error_message=str(error),
                    metadata=metadata,
                )
            metadata["document_extractor"] = extractor
            if time.monotonic() >= call_deadline:
                return ProviderResult(
                    False,
                    "",
                    error_type="timeout",
                    error_message="Moli document runtime extraction exceeded time budget",
                    metadata=metadata,
                )
            ok = True
            status = envelope.get("status")
            return ProviderResult(
                ok,
                content,
                status_code=status if isinstance(status, int) else None,
                final_url=envelope.get("source"),
                error_type=None,
                error_message=None,
                metadata=metadata,
            )
        if document_task and raw.startswith((b"%PDF", b"PK")):
            try:
                content, extractor = extract_document_text(raw)
            except Exception as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="document_parse_error",
                    error_message=str(error),
                    metadata=metadata,
                )
            metadata.update(
                document_extractor=extractor,
                raw_stdout_sha256=hashlib.sha256(raw).hexdigest(),
                _raw_stdout_b64=base64.b64encode(raw).decode("ascii"),
            )
            if time.monotonic() >= call_deadline:
                return ProviderResult(
                    False,
                    "",
                    error_type="timeout",
                    error_message="Moli document extraction exceeded time budget",
                    metadata=metadata,
                )
            ok = process.returncode == 0
            return ProviderResult(
                ok,
                content,
                error_type=None if ok else "tool_error",
                error_message=None if ok else stderr or "Moli document request failed",
                metadata=metadata,
            )
        if document_task:
            metadata.update(
                document_response="serialized_text_not_raw_document",
                raw_stdout_sha256=hashlib.sha256(raw).hexdigest(),
                _raw_stdout_b64=base64.b64encode(raw).decode("ascii"),
            )
            return ProviderResult(
                False,
                "",
                error_type="invalid_output" if process.returncode == 0 else "tool_error",
                error_message=(
                    "Moli serialized a direct document instead of returning raw bytes"
                    if process.returncode == 0
                    else stderr or "Moli did not return raw document bytes"
                ),
                metadata=metadata,
            )
        if document_page_task:
            if process.returncode != 0:
                return ProviderResult(
                    False,
                    "",
                    error_type="tool_error",
                    error_message=stderr or "Moli document-page extraction failed",
                    metadata=metadata,
                )
            try:
                envelope = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="invalid_output",
                    error_message=f"Moli document-page output is invalid JSON: {error}",
                    metadata=metadata,
                )
            metadata["embedded_document_url"] = envelope.get("source")
            if envelope.get("kind") == "pdf_base64":
                try:
                    embedded = base64.b64decode(envelope["data"], validate=True)
                    content, extractor = extract_document_text(embedded)
                except Exception as error:
                    return ProviderResult(
                        False,
                        "",
                        error_type="document_parse_error",
                        error_message=str(error),
                        metadata=metadata,
                    )
                metadata.update(
                    document_extractor=extractor,
                    raw_stdout_sha256=hashlib.sha256(embedded).hexdigest(),
                    _raw_stdout_b64=base64.b64encode(embedded).decode("ascii"),
                )
            elif envelope.get("kind") in {"text", "page_text"}:
                content = str(envelope.get("text") or "")
                metadata["document_extractor"] = (
                    "pdfjs_runtime" if envelope.get("kind") == "text" else "rendered_page_text"
                )
            else:
                return ProviderResult(
                    False,
                    "",
                    error_type="invalid_output",
                    error_message="Moli document-page output has an unknown kind",
                    metadata=metadata,
                )
            if time.monotonic() >= call_deadline:
                return ProviderResult(
                    False,
                    "",
                    error_type="timeout",
                    error_message="Moli document-page extraction exceeded time budget",
                    metadata=metadata,
                )
            ok = True
            return ProviderResult(
                ok,
                content,
                error_type=None,
                error_message=None,
                metadata=metadata,
            )
        if json_page_task:
            metadata["final_url_source"] = "same_call_json"
            metadata.update(
                raw_stdout_sha256=hashlib.sha256(raw).hexdigest(),
                _raw_stdout_b64=base64.b64encode(raw).decode("ascii"),
            )
            try:
                envelope = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                return ProviderResult(
                    False,
                    "",
                    error_type="tool_error" if process.returncode else "invalid_output",
                    error_message=(stderr or "Moli page request failed") if process.returncode
                    else f"Moli page JSON is invalid: {error}",
                    metadata=metadata,
                )
            if not isinstance(envelope, dict) or not isinstance(envelope.get("html"), str):
                return ProviderResult(
                    False,
                    "",
                    status_code=envelope.get("status") if isinstance(envelope, dict) else None,
                    final_url=envelope.get("final_url") if isinstance(envelope, dict) else None,
                    title=envelope.get("title") if isinstance(envelope, dict) else None,
                    error_type="invalid_output",
                    error_message="Moli page JSON omitted final DOM HTML",
                    metadata=metadata,
                )
            trace = envelope.get("network") if isinstance(envelope.get("network"), dict) else {}
            main = (
                trace.get("main_document") if isinstance(trace.get("main_document"), dict) else {}
            )
            subresources = (
                trace.get("subresources") if isinstance(trace.get("subresources"), list) else []
            )
            metadata["network_trace"] = {
                "main_document": {
                    key: main.get(key)
                    for key in (
                        "url",
                        "requested_url",
                        "status",
                        "content_type",
                        "redirected",
                        "redirect_count",
                    )
                    if key in main
                },
                "subresource_count": len(subresources),
                "failed_subresource_count": sum(
                    isinstance(item, dict) and item.get("ok") is False for item in subresources
                ),
                "response_header_names": sorted(
                    {
                        str(item.get("name", "")).casefold()
                        for item in envelope.get("headers", [])
                        if isinstance(item, dict) and item.get("name")
                    }
                ),
                "redirect_chain": [
                    {key: item.get(key) for key in ("from_url", "to_url", "status")}
                    for item in envelope.get("redirect_chain", [])[:20]
                    if isinstance(item, dict)
                ],
            }
            remaining = call_deadline - time.monotonic()
            if remaining <= 0:
                return ProviderResult(
                    False,
                    "",
                    status_code=envelope.get("status"),
                    final_url=envelope.get("final_url"),
                    title=envelope.get("title"),
                    error_type="timeout",
                    error_message="Moli page conversion exceeded time budget",
                    metadata=metadata,
                )
            try:
                metadata['converter_sha256'] = hashlib.sha256(CONVERTER.read_bytes()).hexdigest()
                metadata['extractor_version'] = '2.0.0'
                converted = subprocess.run(
                    converter_command(markdown_package),
                    input=envelope["html"].encode("utf-8"),
                    capture_output=True,
                    timeout=remaining,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return ProviderResult(
                    False,
                    "",
                    status_code=envelope.get("status"),
                    final_url=envelope.get("final_url"),
                    title=envelope.get("title"),
                    error_type="timeout",
                    error_message="Moli page conversion exceeded time budget",
                    metadata=metadata,
                )
            if converted.returncode != 0 or len(converted.stdout) > limit:
                return ProviderResult(
                    False,
                    "",
                    status_code=envelope.get("status"),
                    final_url=envelope.get("final_url"),
                    title=envelope.get("title"),
                    error_type="conversion_error" if converted.returncode else "output_too_large",
                    error_message=(
                        converted.stderr.decode("utf-8", "replace")[:2000]
                        if converted.returncode
                        else "Converted page output exceeds max_bytes"
                    ),
                    metadata=metadata,
                )
            content = converted.stdout.decode("utf-8", "replace")
            status = envelope.get("status")
            http_ok = isinstance(status, int) and 200 <= status < 400
            ok = process.returncode == 0 and http_ok
            return ProviderResult(
                ok,
                content,
                status_code=status if isinstance(status, int) else None,
                final_url=envelope.get("final_url"),
                title=envelope.get("title"),
                error_type=(
                    None
                    if ok
                    else "api_error"
                    if isinstance(status, int) and status >= 400
                    else "tool_error"
                ),
                error_message=(
                    None
                    if ok
                    else stderr or f"Moli returned HTTP {status}"
                    if isinstance(status, int) and status >= 400
                    else stderr or "Moli challenge request failed"
                ),
                metadata=metadata,
            )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ProviderResult(
                False,
                "",
                error_type="invalid_output",
                error_message="Moli markdown output is not UTF-8",
                metadata=metadata,
            )
        parsed_error_status = _markdown_http_error_status(content)
        ok = process.returncode == 0 and parsed_error_status is None
        final_url = _diagnostic_main_document_url(diagnostic_raw, case.url)
        if final_url:
            metadata["final_url_source"] = "same_call_debug_log"
        # The debug trace is part of this same provider call, so page identity
        # does not require a second request. Moli's compact whole-response error
        # page remains the only status envelope recognized in Markdown output.
        return ProviderResult(
            ok,
            content,
            status_code=parsed_error_status,
            final_url=final_url,
            error_type=(
                None if ok else "api_error" if parsed_error_status is not None else "tool_error"
            ),
            error_message=(
                None
                if ok
                else f"Moli returned HTTP {parsed_error_status}"
                if parsed_error_status is not None
                else stderr or "Moli request failed"
            ),
            metadata=metadata,
        )

    def cancel(self) -> None:
        # Remember cancellation during Popen without blocking the deadline
        # thread on process creation. The launching thread kills a late child.
        with self._process_lock:
            self._cancel_requested = True
            self._kill(self._active_process)

    @property
    def cancellation_cleanup_seconds(self) -> float:
        return 1.0

    @staticmethod
    def _kill(process):
        if process is not None and process.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    return
                except ProcessLookupError:
                    return
                except OSError:
                    # Sandboxes can forbid process-group signalling while
                    # still allowing the direct child to be terminated.
                    pass
            try:
                if process.poll() is None:
                    process.kill()
            except OSError:
                pass
