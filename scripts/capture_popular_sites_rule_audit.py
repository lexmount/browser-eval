#!/usr/bin/env python3
"""Capture provider-free, repeated evidence for the 100-site rule audit.

The script reads only the frozen dataset. It never reads measured results,
clicks a page, follows a detail link, logs in, or solves a challenge. Raw
captures and screenshots belong outside Git under the private evidence root.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT = 80_000
MAX_ELEMENTS = 3_000
MAX_LINKS = 2_000
MAX_CONTROLS = 500
MAX_IMAGES = 1_000


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def observe(browser, case: dict, *, settle_ms: int, timeout_ms: int,
                  screenshot: Path | None, round_id: str) -> dict:
    context = await browser.new_context(
        locale="en-US",
        viewport={"width": 1365, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
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
        except Exception as error:
            navigation_error = compact(str(error))[:500]
        await page.wait_for_timeout(settle_ms)
        payload = await page.evaluate(
            """({maxText,maxElements,maxLinks,maxControls,maxImages}) => {
              const norm = value => (value || '').replace(/\s+/g, ' ').trim();
              const visible = element => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                  rect.width > 0 && rect.height > 0;
              };
              const body = norm(document.body?.innerText || '');
              const elements = [...document.querySelectorAll(
                'h1,h2,h3,h4,h5,h6,p,li,nav,a[href],button,label,' +
                '[role=heading],[role=button],[role=searchbox],[role=combobox]')]
                .filter(visible)
                .map(element => ({
                  tag: element.tagName.toLowerCase(),
                  role: element.getAttribute('role'),
                  text: norm(element.innerText).slice(0, 1000),
                  aria_label: norm(element.getAttribute('aria-label')).slice(0, 500),
                  title: norm(element.getAttribute('title')).slice(0, 500),
                }))
                .filter(item => item.text || item.aria_label || item.title)
                .slice(0, maxElements);
              const links = [...document.querySelectorAll('a[href]')]
                .filter(visible)
                .map(element => {
                  let url = '';
                  try { url = new URL(element.href, location.href).href; } catch (_) {}
                  return {
                    text: norm(element.innerText).slice(0, 500),
                    aria_label: norm(element.getAttribute('aria-label')).slice(0, 500),
                    title: norm(element.getAttribute('title')).slice(0, 500),
                    image_alt: norm(element.querySelector('img')?.getAttribute('alt')).slice(0, 500),
                    url,
                  };
                })
                .filter(item => /^https?:/.test(item.url))
                .slice(0, maxLinks);
              const controls = [...document.querySelectorAll(
                'input,textarea,select,button,[role=button],[role=searchbox],[role=combobox]')]
                .filter(visible)
                .map(element => ({
                  tag: element.tagName.toLowerCase(),
                  type: element.getAttribute('type'),
                  visible_text: norm(element.innerText).slice(0, 500),
                  aria_label: norm(element.getAttribute('aria-label')).slice(0, 500),
                  placeholder: norm(element.getAttribute('placeholder')).slice(0, 500),
                  title: norm(element.getAttribute('title')).slice(0, 500),
                  name: norm(element.getAttribute('name')).slice(0, 500),
                }))
                .filter(item => item.visible_text || item.aria_label || item.placeholder || item.title)
                .slice(0, maxControls);
              const images = [...document.querySelectorAll('img')]
                .filter(visible)
                .map(element => ({
                  alt: norm(element.getAttribute('alt')).slice(0, 500),
                  aria_label: norm(element.getAttribute('aria-label')).slice(0, 500),
                  title: norm(element.getAttribute('title')).slice(0, 500),
                  width: Math.round(element.getBoundingClientRect().width),
                  height: Math.round(element.getBoundingClientRect().height),
                }))
                .slice(0, maxImages);
              return {
                final_url: location.href,
                title: document.title,
                document_lang: document.documentElement.lang || null,
                visible_text: body.slice(0, maxText),
                visible_text_length: body.length,
                visible_text_truncated: body.length > maxText,
                visible_text_sha256: null,
                elements, links, controls, images,
              };
            }""",
            {
                "maxText": MAX_TEXT,
                "maxElements": MAX_ELEMENTS,
                "maxLinks": MAX_LINKS,
                "maxControls": MAX_CONTROLS,
                "maxImages": MAX_IMAGES,
            },
        )
        payload["visible_text_sha256"] = hashlib.sha256(
            payload["visible_text"].encode()
        ).hexdigest()
        screenshot_record = None
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                await page.screenshot(path=str(screenshot), full_page=False, timeout=10_000)
                screenshot_record = {
                    "path": screenshot.name,
                    "sha256": digest(screenshot),
                    "scope": "initial 1365x900 viewport; OCR is diagnostic only",
                }
            except Exception as error:
                screenshot_record = {
                    "path": None,
                    "error": compact(str(error))[:500],
                    "scope": "screenshot failure does not discard captured DOM evidence",
                }
        return {
            "round": round_id,
            "settle_ms": settle_ms,
            "state": "captured",
            "http_status": status,
            "navigation_error": navigation_error,
            "screenshot": screenshot_record,
            **payload,
        }
    except Exception as error:
        return {
            "round": round_id,
            "settle_ms": settle_ms,
            "state": "capture_error",
            "http_status": status,
            "navigation_error": navigation_error or compact(str(error))[:500],
        }
    finally:
        await context.close()


async def run(args: argparse.Namespace) -> None:
    from playwright.async_api import async_playwright

    dataset = json.loads(args.dataset.read_text())
    cases = dataset.get("cases", [])
    if len(cases) != 100 or len({case["id"] for case in cases}) != 100:
        raise SystemExit("Expected the frozen 100-case dataset")
    if args.rounds < 1 or args.settle_ms < 0 or args.timeout_ms < 1:
        raise SystemExit("rounds and timeout must be positive; settle-ms cannot be negative")
    requested = set(args.case_id)
    if requested:
        unknown = requested - {case["id"] for case in cases}
        if unknown:
            raise SystemExit(f"Unknown case ids: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in requested]
    if args.output.exists() or args.screenshot_dir.exists():
        raise SystemExit("Refusing to overwrite append-only audit evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.screenshot_dir.mkdir(parents=True)
    semaphore = asyncio.Semaphore(args.concurrency)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=args.browser_binary
        )

        async def capture_case(case: dict) -> dict:
            async with semaphore:
                rounds = []
                for index in range(1, args.rounds + 1):
                    screenshot = (
                        args.screenshot_dir / f"{case['id']}.png" if index == 1 else None
                    )
                    rounds.append(await observe(
                        browser, case, settle_ms=args.settle_ms, timeout_ms=args.timeout_ms,
                        screenshot=screenshot, round_id=f"{args.round_prefix}-{index}",
                    ))
                return {"case_id": case["id"], "url": case["url"], "rounds": rounds}

        captured = await asyncio.gather(*(capture_case(case) for case in cases))
        await browser.close()
    output = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": "Provider-free repeated direct-page evidence for the 100-site rule audit",
        "dataset": {"path": str(args.dataset.relative_to(ROOT)), "sha256": digest(args.dataset)},
        "contract": {
            "cases": len(cases),
            "rounds": args.rounds,
            "settle_ms": args.settle_ms,
            "timeout_ms": args.timeout_ms,
            "fresh_anonymous_context_per_round": True,
            "locale": "en-US",
            "viewport": [1365, 900],
            "selected_url_only": True,
            "clicks": False,
            "recursive_navigation": False,
            "provider_calls": False,
            "ocr_used_for_scoring": False,
        },
        "screenshot_directory": str(args.screenshot_dir),
        "cases": captured,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "cases": len(captured),
        "rounds": sum(len(item["rounds"]) for item in captured),
        "captured": sum(round_["state"] == "captured" for item in captured for round_ in item["rounds"]),
        "output": str(args.output),
        "sha256": digest(args.output),
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=ROOT / "evaluation/popular-sites-dataset.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshot-dir", type=Path, required=True)
    parser.add_argument("--browser-binary")
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--settle-ms", type=int, default=5_000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--round-prefix", default="primary")
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()
    args.dataset = args.dataset.resolve()
    args.output = args.output.resolve()
    args.screenshot_dir = args.screenshot_dir.resolve()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
