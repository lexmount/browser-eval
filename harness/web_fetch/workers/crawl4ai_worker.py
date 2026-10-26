#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing
import os
import sys
import tempfile
from contextlib import AsyncExitStack, redirect_stdout
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.async_configs import HTTPCrawlerConfig
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
from crawl4ai.processors.pdf import PDFContentScrapingStrategy, PDFCrawlerStrategy

from harness.web_fetch.native_routes import declared_input_media_kind
from harness.web_fetch.network_dns import crawl4ai_public_dns_browser, validate_dns_mode

LIBRARY_VERSION = version("crawl4ai")
PDF_IMPLEMENTATION = (
    "crawl4ai.processors.pdf.PDFCrawlerStrategy+crawl4ai.processors.pdf.PDFContentScrapingStrategy"
)
_PDF_RETURN_MARGIN_SECONDS = 1.0


def require_normalized_retrieval_plan(request):
    """Enforce plan presence at the worker boundary without reparsing its schema."""
    plan = request.get("retrieval_plan")
    if request.get("uniform_native_parameters") and plan:
        raise ValueError("fixed native calls prohibit per-case retrieval plans")
    if request.get("track") == "dynamic" and not plan and request.get("retrieval_mode") != "native_default":
        raise ValueError("dynamic tasks require a normalized retrieval_plan")
    if request.get("track") != "dynamic" and plan:
        raise ValueError("retrieval_plan is only valid for dynamic tasks")
    return plan


def dynamic_completion_config(request):
    """Translate the runner-normalized, answer-free plan to Crawl4AI settings."""
    plan = require_normalized_retrieval_plan(request)
    if request.get("track") != "dynamic" or plan is None:
        return {}, {"strategy": "lifecycle", "wait_until": "domcontentloaded"}
    wait_kind = plan["wait_kind"]
    wait_for = ("css:" if wait_kind == "selector" else "js:") + plan["wait_value"]
    strategy = (
        "frame_selector_count_js"
        if plan.get("frame_selector")
        else "css_selector"
        if wait_kind == "selector"
        else "selector_count_js"
    )
    config = {
        "wait_until": plan["wait_until"],
        "wait_for": wait_for,
        "wait_for_timeout": plan["timeout_ms"],
        "delay_before_return_html": plan["delay_ms"] / 1000,
    }
    metadata = {
        "strategy": strategy,
        "wait_until": plan["wait_until"],
        "selector": plan["selector"],
        "frame_selector": plan.get("frame_selector"),
        "minimum_count": plan["minimum_count"],
        "timeout_ms": plan["timeout_ms"],
        "delay_ms": plan["delay_ms"],
        "completion_support": "exact",
    }
    return config, metadata


def dynamic_extraction_config(request):
    """Translate the normalized v2 extraction scope into post-wait DOM capture."""
    plan = require_normalized_retrieval_plan(request)
    extraction = (plan or {}).get("extraction")
    selector = extraction.get("selector") if isinstance(extraction, dict) else None
    if request.get("track") != "dynamic" or not selector:
        return {}, None
    maximum_count = int(extraction["maximum_count"])
    frame_selector = extraction.get("frame_selector")
    exclusions = list(extraction.get("exclude_selectors", []))
    fallback = extraction["fallback"]
    marker = "[data-browser-eval-extraction-root]"
    # Crawl4AI executes js_code after wait_for and before HTML capture. Clone a
    # bounded, answer-free predeclared selection into a synthetic root so the
    # crawler's own Markdown pipeline processes exactly that result surface.
    script = f"""
const selector = {json.dumps(selector)};
const frameSelector = {json.dumps(frame_selector)};
const maximumCount = {maximum_count};
const exclusions = {json.dumps(exclusions)};
const fallback = {json.dumps(fallback)};
const sourceDocument = frameSelector
  ? document.querySelector(frameSelector)?.contentDocument
  : document;
let selected = sourceDocument
  ? Array.from(sourceDocument.querySelectorAll(selector)).slice(0, maximumCount)
  : [];
if (!selected.length && fallback === "body" && sourceDocument?.body) selected = [sourceDocument.body];
const root = document.createElement("main");
root.setAttribute("data-browser-eval-extraction-root", "");
root.setAttribute("data-browser-eval-selected-count", String(selected.length));
for (const [index, source] of selected.entries()) {{
  if (index) {{
    root.appendChild(document.createElement("br"));
    root.appendChild(document.createElement("br"));
  }}
  const clone = source.cloneNode(true);
  for (const excluded of exclusions) {{
    for (const node of clone.querySelectorAll(excluded)) node.remove();
    if (clone.matches && clone.matches(excluded)) clone.replaceChildren();
  }}
  if (source === document.body) {{
    while (clone.firstChild) root.appendChild(clone.firstChild);
  }} else {{
    root.appendChild(clone);
  }}
}}
document.body.replaceChildren(root);
""".strip()
    return {
        "js_code": script,
        "css_selector": marker,
    }, {
        "strategy": "post_wait_bounded_dom_clone",
        "selector": selector,
        "frame_selector": frame_selector,
        "maximum_count": maximum_count,
        "maximum_count_enforced": True,
        "exclude_selectors": exclusions,
        "fallback": fallback,
        "scope_support": "exact",
    }


def is_direct_pdf_document(request):
    # Older subprocess requests do not carry ``track``. In that case the body
    # contract plus a direct-PDF URL is sufficient; when track is present, fail
    # closed rather than routing a non-document benchmark through the PDF parser.
    if request.get("track") not in {None, "document"}:
        return False
    if request.get("task_kind", "body") != "body":
        return False
    if request.get("uniform_native_parameters"):
        return declared_input_media_kind(request) == "direct_pdf"
    path = urlsplit(request["url"]).path.lower().rstrip("/")
    return path.endswith(".pdf") or "/pdf/" in path


def encode_downloads(paths, directory):
    files = []
    for filename in paths or []:
        path = Path(filename).resolve()
        if not path.is_relative_to(Path(directory).resolve()) or path.stat().st_size > 20000000:
            raise ValueError("Invalid or oversized downloaded file")
        files.append({"base64": base64.b64encode(path.read_bytes()).decode("ascii")})
    return files


async def fetch_file(request, directory):
    # Native library file strategy, selected before the call; no fallback request.
    async with AsyncHTTPCrawlerStrategy(
        browser_config=HTTPCrawlerConfig(downloads_path=directory)
    ) as crawler:
        result = await crawler.crawl(
            request["url"],
            config=CrawlerRunConfig(**({} if request.get("uniform_native_parameters") else {"page_timeout": int(request["timeout_seconds"] * 1000)})),
        )
    return {
        "ok": 200 <= result.status_code < 300,
        "content": result.html or "",
        "status_code": result.status_code,
        "final_url": result.redirected_url or request["url"],
        "files": encode_downloads(result.downloaded_files, directory),
        "metadata": {
            "implementation": "crawl4ai.AsyncHTTPCrawlerStrategy",
            "library_version": LIBRARY_VERSION,
            "output_contract": "native_downloaded_files",
            "strategy_selected_before_call": True,
        },
    }


async def _run_pdf_document(request):
    """Run Crawl4AI's paired native PDF strategies inside the call child."""
    timeout_seconds = float(request["timeout_seconds"])
    scraping_strategy = PDFContentScrapingStrategy()
    async with AsyncWebCrawler(crawler_strategy=PDFCrawlerStrategy()) as crawler:
        result = await crawler.arun(
            url=request["url"],
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                **({} if request.get("uniform_native_parameters") else {"page_timeout": int(timeout_seconds * 1000)}),
                scraping_strategy=scraping_strategy,
                verbose=request.get("log_level") == "debug",
            ),
        )
    markdown = result.markdown.raw_markdown if result.markdown else ""
    return {
        "ok": bool(result.success),
        "content": markdown,
        "files": [],
        # PDFCrawlerStrategy returns a synthetic 200 placeholder before its
        # scraper downloads the PDF, so it is not an origin status code.
        "status_code": None,
        "final_url": request["url"],
        "error_type": None if result.success else "crawl_error",
        "error_message": None if result.success else result.error_message,
        "metadata": {
            "implementation": PDF_IMPLEMENTATION,
            "library_version": LIBRARY_VERSION,
            "output_contract": "native_pdf_extracted_markdown",
            "strategy_selected_before_call": True,
            "origin_status_code_available": False,
            "final_url_source": "requested_url",
            "images": result.media.get("images", []),
            "links": result.links,
        },
    }


def _pdf_failure(request, error_type, error_message):
    return {
        "ok": False,
        "content": "",
        "files": [],
        "status_code": None,
        "final_url": request["url"],
        "error_type": error_type,
        "error_message": error_message,
        "metadata": {
            "implementation": PDF_IMPLEMENTATION,
            "library_version": LIBRARY_VERSION,
            "output_contract": "native_pdf_extracted_markdown",
            "strategy_selected_before_call": True,
            "origin_status_code_available": False,
            "final_url_source": "requested_url",
        },
    }


def _pdf_child(request, temp_directory, connection):
    """Own blocking requests/pypdf work in a process the worker can kill."""
    tempfile.tempdir = temp_directory
    try:
        # Crawl4AI versions may emit progress on stdout despite verbose=False.
        # Keep the parent worker's stdout strictly JSONL.
        with redirect_stdout(sys.stderr):
            output = asyncio.run(_run_pdf_document(request))
    except BaseException as error:
        output = _pdf_failure(request, type(error).__name__, str(error))
    try:
        connection.send(output)
    finally:
        connection.close()


def _stop_pdf_child(process, *, force):
    if not force:
        process.join(timeout=0.05)
    if process.is_alive():
        process.terminate()
        process.join(timeout=0.25)
    if process.is_alive():
        process.kill()
        process.join(timeout=0.25)


async def fetch_pdf_document(request):
    """Extract a PDF in a killable child with parent-owned temporary storage."""
    timeout_seconds = float(request["timeout_seconds"])
    margin = min(_PDF_RETURN_MARGIN_SECONDS, max(0.05, timeout_seconds * 0.05))
    child_timeout = max(0.001, timeout_seconds - margin)
    output = None

    with tempfile.TemporaryDirectory(prefix="webfetch-crawl-pdf-") as temp_directory:
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_pdf_child,
            args=(request, temp_directory, child_connection),
            daemon=True,
        )
        deadline = asyncio.get_running_loop().time() + child_timeout
        process.start()
        child_connection.close()
        timed_out = False
        try:
            while True:
                if parent_connection.poll():
                    try:
                        output = parent_connection.recv()
                    except EOFError:
                        output = None
                    break
                if not process.is_alive():
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    timed_out = True
                    break
                await asyncio.sleep(0.01)
        finally:
            _stop_pdf_child(process, force=timed_out)
            parent_connection.close()

        if timed_out:
            output = _pdf_failure(
                request,
                "timeout",
                f"PDF download and extraction timed out after {child_timeout:.3f}s",
            )
        elif output is None:
            output = _pdf_failure(
                request,
                "ChildProcessError",
                f"PDF worker exited without a result (exit code {process.exitcode})",
            )

    output["metadata"].update(
        {
            "execution_boundary": "per_call_child_process",
            "temporary_storage": "parent_owned_per_call_directory",
            "temporary_storage_cleanup": "completed",
            "child_timeout_seconds": child_timeout,
        }
    )
    return output


async def main() -> None:
    download_dir = tempfile.TemporaryDirectory(prefix="webfetch-crawl-downloads-")
    browser_config = BrowserConfig(
        browser_type="chromium",
        chrome_channel="chrome",
        headless=True,
        verbose=os.environ.get("BROWSER_EVAL_LOG_LEVEL") == "debug",
        accept_downloads=True,
        downloads_path=download_dir.name,
    )
    crawler = None
    browser_dns_mode = None
    dns_metadata = {"dns_mode": "system"}
    async with AsyncExitStack() as stack:
        stack.enter_context(download_dir)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            if not line.strip():
                continue
            request = json.loads(line)
            completion_metadata = None
            extraction_metadata = None
            try:
                # Guard every worker route, including file/PDF branches that do
                # not otherwise construct browser completion settings.
                require_normalized_retrieval_plan(request)
                requested_dns_mode = validate_dns_mode(request.get("dns_mode", "system"))
                if requested_dns_mode == "public_doh" and (request.get("task_kind") == "file" or is_direct_pdf_document(request)):
                    raise ValueError("Crawl4AI public_doh supports the browser route only")
                if request.get("task_kind") == "file":
                    output = await fetch_file(request, download_dir.name)
                    print(json.dumps(output, ensure_ascii=True), flush=True)
                    continue
                if is_direct_pdf_document(request):
                    output = await fetch_pdf_document(request)
                    print(json.dumps(output, ensure_ascii=True), flush=True)
                    continue
                if browser_dns_mode is not None and browser_dns_mode != requested_dns_mode:
                    raise ValueError("worker DNS mode cannot change after browser startup")
                if crawler is None:
                    if requested_dns_mode == "public_doh":
                        browser_config, dns_metadata = await stack.enter_async_context(
                            crawl4ai_public_dns_browser(browser_config)
                        )
                    browser_dns_mode = requested_dns_mode
                    crawler = await stack.enter_async_context(
                        AsyncWebCrawler(config=browser_config)
                    )
                completion_config, completion_metadata = dynamic_completion_config(request)
                extraction_config, extraction_metadata = dynamic_extraction_config(request)
                route_timeout_ms = int(float(request["timeout_seconds"]) * 1000)
                if "timeout_ms" in completion_metadata:
                    route_timeout_ms = min(route_timeout_ms, completion_metadata["timeout_ms"])
                    completion_config["wait_for_timeout"] = route_timeout_ms
                completion_metadata["effective_route_timeout_ms"] = route_timeout_ms
                run_config_values = {
                    "cache_mode": CacheMode.BYPASS,
                    "page_timeout": route_timeout_ms,
                    "wait_until": "domcontentloaded",
                    "remove_overlay_elements": True,
                    "process_iframes": True,
                }
                if request.get("uniform_native_parameters"):
                    run_config_values.pop("page_timeout", None)
                run_config_values.update(completion_config)
                run_config_values.update(extraction_config)
                run_config = CrawlerRunConfig(**run_config_values)
                result = await asyncio.wait_for(
                    crawler.arun(url=request["url"], config=run_config),
                    timeout=route_timeout_ms / 1000,
                )
                markdown = result.markdown.raw_markdown if result.markdown else ""
                files = encode_downloads(result.downloaded_files, download_dir.name)
                output = {
                    "ok": bool(result.success),
                    # Preserve HTML, canonical Markdown, headers and native errors
                    # privately; answer selection remains raw_markdown.
                    "raw_tool_response": result.model_dump(mode="json", fallback=str),
                    "content": markdown,
                    "files": files,
                    "status_code": result.redirected_status_code if result.redirected_status_code is not None else result.status_code,
                    "final_url": result.redirected_url or result.url,
                    "error_type": None if result.success else "crawl_error",
                    "error_message": None if result.success else result.error_message,
                    "metadata": {
                        "implementation": "crawl4ai.AsyncWebCrawler",
                        "library_version": LIBRARY_VERSION,
                        "dns_environment": dns_metadata,
                        "images": result.media.get("images", []),
                        "videos": result.media.get("videos", []),
                        "audios": result.media.get("audios", []),
                        "links": result.links,
                        "native_initial_status_code": result.status_code,
                        "internal_links": result.links.get("internal", []),
                        "external_links": result.links.get("external", []),
                        "completion": completion_metadata,
                        "extraction": extraction_metadata,
                    },
                }
            except Exception as error:
                implementation = (
                    PDF_IMPLEMENTATION
                    if is_direct_pdf_document(request)
                    else "crawl4ai.AsyncWebCrawler"
                )
                metadata = {
                    "implementation": implementation,
                    "library_version": LIBRARY_VERSION,
                }
                if completion_metadata is not None:
                    metadata["completion"] = completion_metadata
                if extraction_metadata is not None:
                    metadata["extraction"] = extraction_metadata
                output = {
                    "ok": False,
                    "content": repr(error),
                    "final_url": request["url"],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "metadata": metadata,
                }
            print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
