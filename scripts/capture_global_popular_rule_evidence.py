#!/usr/bin/env python3
"""Capture bounded direct-page evidence for popular-site rule authoring.

This research helper opens each selected URL once in a fresh anonymous browser
context. It records only content and links rendered by that page; it never
clicks or recursively visits a linked detail page and never calls a provider.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_CHARS = 80_000
MAX_LINKS = 2_000
MAX_HEADINGS = 100
MAX_CONTROLS = 100


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


async def observe(playwright, browser, case: dict, timeout_ms: int, settle_ms: int) -> dict:
    context = await browser.new_context(
        locale="en-US",
        viewport={"width": 1365, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    status = None
    navigation_error = None
    try:
        try:
            response = await page.goto(
                case["url"], wait_until="domcontentloaded", timeout=timeout_ms
            )
            status = response.status if response else None
        except Exception as exc:
            navigation_error = compact(str(exc))[:500]
        await page.wait_for_timeout(settle_ms)
        observed = await page.evaluate(
            """({maxText, maxLinks, maxHeadings, maxControls}) => {
                const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
                const bodyText = normalize(document.body?.innerText || '');
                const links = [];
                for (const element of document.querySelectorAll('a[href]')) {
                    const text = normalize(
                        element.innerText || element.getAttribute('aria-label') ||
                        element.getAttribute('title') ||
                        element.querySelector('img')?.getAttribute('alt') || ''
                    );
                    let url;
                    try { url = new URL(element.href, location.href).href; }
                    catch (_) { continue; }
                    if (!text || !/^https?:/.test(url)) continue;
                    links.push({text: text.slice(0, 500), url});
                    if (links.length >= maxLinks) break;
                }
                const headings = [...document.querySelectorAll('h1,h2,h3,h4,[role=heading]')]
                    .map((element) => ({
                        level: element.getAttribute('aria-level') ||
                            (/^H[1-6]$/.test(element.tagName) ? element.tagName.slice(1) : null),
                        text: normalize(element.innerText),
                    }))
                    .filter((item) => item.text)
                    .slice(0, maxHeadings);
                const controls = [...document.querySelectorAll(
                    'input,textarea,select,button,[role=button],[role=searchbox],[role=combobox]')]
                    .map((element) => ({
                        tag: element.tagName.toLowerCase(),
                        type: element.getAttribute('type') || null,
                        label: normalize(
                            element.getAttribute('aria-label') ||
                            element.getAttribute('placeholder') ||
                            element.getAttribute('title') ||
                            element.innerText ||
                            element.getAttribute('name') || ''
                        ).slice(0, 500),
                    }))
                    .filter((item) => item.label)
                    .slice(0, maxControls);
                return {
                    final_url: location.href,
                    title: document.title,
                    document_lang: document.documentElement.lang || null,
                    full_visible_text: bodyText,
                    visible_text: bodyText.slice(0, maxText),
                    visible_text_truncated: bodyText.length > maxText,
                    visible_text_length: bodyText.length,
                    headings,
                    controls,
                    links,
                    total_http_links: [...document.querySelectorAll('a[href]')]
                        .filter((element) => /^https?:/.test(element.href)).length,
                };
            }""",
            {
                "maxText": MAX_TEXT_CHARS,
                "maxLinks": MAX_LINKS,
                "maxHeadings": MAX_HEADINGS,
                "maxControls": MAX_CONTROLS,
            },
        )
        full_text = observed.pop("full_visible_text")
        observed["visible_text_sha256"] = hashlib.sha256(
            full_text.encode("utf-8")
        ).hexdigest()
        return {
            "id": case["id"],
            "domain": case["domain"],
            "url": case["url"],
            "label": case["label"],
            "observer": "Playwright Chromium, fresh anonymous browser context",
            "observation_state": "captured",
            "http_status": status,
            "navigation_error": navigation_error,
            **observed,
        }
    except Exception as exc:
        return {
            "id": case["id"],
            "domain": case["domain"],
            "url": case["url"],
            "label": case["label"],
            "observer": "Playwright Chromium, fresh anonymous browser context",
            "observation_state": "capture_error",
            "http_status": status,
            "navigation_error": navigation_error or compact(str(exc))[:500],
        }
    finally:
        await context.close()


async def run(args: argparse.Namespace) -> None:
    from playwright.async_api import async_playwright

    source = json.loads(args.input.read_text())
    cases = source["cases"]
    if len(cases) != len({case["id"] for case in cases}):
        raise SystemExit("Selection contains duplicate case IDs")
    if len(cases) != len({case["domain"] for case in cases}):
        raise SystemExit("Selection contains duplicate domains")
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite immutable evidence: {args.output}")

    semaphore = asyncio.Semaphore(args.concurrency)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=args.browser_binary
        )

        async def worker(case: dict) -> dict:
            async with semaphore:
                return await observe(
                    playwright, browser, case, args.timeout_ms, args.settle_ms
                )

        observations = await asyncio.gather(*(worker(case) for case in cases))
        await browser.close()

    result = {
        "schema_version": 1,
        "purpose": "independent direct-page evidence for per-site acceptance-rule authoring",
        "source_file": str(args.input.resolve().relative_to(ROOT)),
        "capture_contract": {
            "selected_url_only": True,
            "clicks_or_recursive_navigation": False,
            "anonymous_fresh_context_per_case": True,
            "locale": "en-US",
            "timeout_ms": args.timeout_ms,
            "post_domcontentloaded_wait_ms": args.settle_ms,
            "maximum_visible_text_chars_saved": MAX_TEXT_CHARS,
            "maximum_links_saved": MAX_LINKS,
            "maximum_controls_saved": MAX_CONTROLS,
            "provider_calls": False,
        },
        "observations": observations,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Captured {len(observations)} direct-page observations -> {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "research/global-popular-selection.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--settle-ms", type=int, default=5_000)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--browser-binary")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
