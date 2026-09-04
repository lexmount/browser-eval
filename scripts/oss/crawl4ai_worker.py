#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import base64
import tempfile
from pathlib import Path

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode


async def main() -> None:
    download_dir = tempfile.TemporaryDirectory(prefix="webfetch-crawl-downloads-")
    browser_config = BrowserConfig(
        browser_type="chromium",
        chrome_channel="chrome",
        headless=True,
        verbose=False,
        accept_downloads=True,
        downloads_path=download_dir.name,
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            if not line.strip():
                continue
            request = json.loads(line)
            try:
                run_config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=int(request["timeout_seconds"] * 1000),
                    wait_until="networkidle",
                    remove_overlay_elements=True,
                    process_iframes=True,
                )
                result = await crawler.arun(url=request["url"], config=run_config)
                markdown = result.markdown.raw_markdown if result.markdown else ""
                files = []
                for filename in result.downloaded_files or []:
                    path = Path(filename).resolve()
                    if not path.is_relative_to(Path(download_dir.name).resolve()) or path.stat().st_size > 20000000:
                        raise ValueError("Invalid or oversized downloaded file")
                    files.append({"base64": base64.b64encode(path.read_bytes()).decode("ascii")})
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


asyncio.run(main())
