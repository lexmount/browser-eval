#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import base64
import tempfile
from pathlib import Path
from contextlib import AsyncExitStack

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy
from crawl4ai.async_configs import HTTPCrawlerConfig


PAGE_TIMEOUT_HEADROOM_MS = 5000
PAGE_SETTLE_SECONDS = 1.0


def build_browser_run_config(request):
    # Return a structured Crawl4AI result before the harness-wide deadline so
    # browser cleanup and JSONL serialization are not mistaken for a timeout.
    deadline_ms = int(request["timeout_seconds"] * 1000)
    page_timeout_ms = max(1000, deadline_ms - PAGE_TIMEOUT_HEADROOM_MS)
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=page_timeout_ms,
        wait_until="domcontentloaded",
        delay_before_return_html=PAGE_SETTLE_SECONDS,
        remove_overlay_elements=True,
        process_iframes=False,
    )


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
    async with AsyncHTTPCrawlerStrategy(browser_config=HTTPCrawlerConfig(downloads_path=directory)) as crawler:
        result = await crawler.crawl(request['url'], config=CrawlerRunConfig(
            page_timeout=int(request['timeout_seconds'] * 1000)))
    return {'ok': 200 <= result.status_code < 300, 'content': result.html or '',
        'status_code': result.status_code, 'final_url': result.redirected_url or request['url'],
        'files': encode_downloads(result.downloaded_files, directory),
        'metadata': {'implementation': 'crawl4ai.AsyncHTTPCrawlerStrategy',
                     'output_contract': 'native_downloaded_files', 'strategy_selected_before_call': True}}


async def main() -> None:
    download_dir = tempfile.TemporaryDirectory(prefix="webfetch-crawl-downloads-")
    browser_config = BrowserConfig(
        browser_type="chromium",
        # Use the browser revision pinned by the isolated Playwright runtime.
        # "chrome" would silently depend on a mutable system installation.
        chrome_channel="chromium",
        headless=True,
        verbose=False,
        accept_downloads=True,
        downloads_path=download_dir.name,
    )
    crawler = None
    async with AsyncExitStack() as stack:
        stack.enter_context(download_dir)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            if not line.strip():
                continue
            request = json.loads(line)
            try:
                if request.get('task_kind') == 'file':
                    output = await fetch_file(request, download_dir.name)
                    print(json.dumps(output, ensure_ascii=True), flush=True)
                    continue
                if crawler is None:
                    crawler = await stack.enter_async_context(AsyncWebCrawler(config=browser_config))
                run_config = build_browser_run_config(request)
                result = await crawler.arun(url=request["url"], config=run_config)
                markdown = result.markdown.raw_markdown if result.markdown else ""
                files = encode_downloads(result.downloaded_files, download_dir.name)
                output = {
                    "ok": bool(result.success),
                    "content": markdown,
                    "files": files,
                    "status_code": result.status_code,
                    "final_url": result.url,
                    "error_type": None if result.success else "crawl_error",
                    "error_message": None if result.success else result.error_message,
                    "metadata": {
                        "implementation": "crawl4ai.AsyncWebCrawler",
                        "wait_until": run_config.wait_until,
                        "page_timeout_ms": run_config.page_timeout,
                        "delay_before_return_html_seconds": run_config.delay_before_return_html,
                        "process_iframes": run_config.process_iframes,
                        "images": result.media.get("images", []),
                        "videos": result.media.get("videos", []),
                        "audios": result.media.get("audios", []),
                        "internal_links": result.links.get("internal", []),
                        "external_links": result.links.get("external", []),
                    },
                }
            except Exception as error:
                output = {
                    "ok": False,
                    "content": repr(error),
                    "final_url": request["url"],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "metadata": {"implementation": "crawl4ai.AsyncWebCrawler"},
                }
            print(json.dumps(output, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
