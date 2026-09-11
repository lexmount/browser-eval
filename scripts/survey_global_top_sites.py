#!/usr/bin/env python3
"""Capture bounded anonymous-browser observations for a frozen popular-site list.

This is research tooling, not a benchmark runner. It never calls a registered
Web Fetch provider and deliberately does not visit domains excluded by policy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
async def observe(
    page, domain: str, requested_url: str, timeout_ms: int, settle_ms: int
) -> dict:
    base = {
        "domain": domain,
        "requested_url": requested_url,
        "observer": "Playwright Chromium, fresh anonymous browser context",
    }

    begin = asyncio.get_running_loop().time()
    response_status = None
    navigation_error = None
    try:
        response = await page.goto(
            base["requested_url"], wait_until="domcontentloaded", timeout=timeout_ms
        )
        response_status = response.status if response else None
    except Exception as exc:  # Browser errors are evidence and must not abort the survey.
        navigation_error = re.sub(r"\s+", " ", str(exc))[:500]

    try:
        await page.wait_for_timeout(settle_ms)
        observed = await page.evaluate(
            """() => {
                const body = document.body;
                const text = (body?.innerText || '').replace(/\\s+/g, ' ').trim();
                const links = [...document.querySelectorAll('a[href]')];
                const controls = [...document.querySelectorAll('input, textarea, select, button')];
                const sameOrigin = links.filter((link) => {
                    try { return new URL(link.href).origin === location.origin; }
                    catch (_) { return false; }
                });
                const lower = text.toLowerCase();
                return {
                    final_url: location.href,
                    title: document.title,
                    document_lang: document.documentElement.lang || null,
                    visible_text_length: text.length,
                    visible_text_sha256_input: text,
                    visible_text_excerpt: text.slice(0, 1000),
                    link_count: links.length,
                    same_origin_link_count: sameOrigin.length,
                    form_count: document.querySelectorAll('form').length,
                    control_count: controls.length,
                    has_password_input: !!document.querySelector('input[type=password]'),
                    has_login_language: /\\b(log ?in|sign ?in|create account)\\b/.test(lower) ||
                        /(登录|登入|ログイン|войти)/.test(lower),
                    has_captcha_language: /captcha|verify you are human|robot check|人机验证/.test(lower),
                    has_error_language: /access denied|page not found|service unavailable|error 403|错误/.test(lower)
                };
            }"""
        )
        observed.pop("visible_text_sha256_input")
    except Exception as exc:
        observed = {}
        if not navigation_error:
            navigation_error = re.sub(r"\s+", " ", str(exc))[:500]

    return {
        **base,
        "observation_state": "captured" if observed else "capture_error",
        "http_status": response_status,
        "navigation_error": navigation_error,
        "elapsed_ms": round((asyncio.get_running_loop().time() - begin) * 1000),
        **observed,
    }


async def run(
    input_path: Path,
    output_path: Path,
    timeout_ms: int,
    concurrency: int,
    browser_binary: str | None,
    settle_ms: int,
) -> None:
    from playwright.async_api import async_playwright

    source = json.loads(input_path.read_text())
    if "cases" in source:
        targets = [(item["domain"], item["url"]) for item in source["cases"]]
    elif "candidates" in source:
        targets = [
            (item["domain"], item["candidate_url"]) for item in source["candidates"]
        ]
    else:
        targets = [(domain, f"https://{domain}/") for domain in source["domains"]]
    if len(targets) != len({domain for domain, _ in targets}):
        raise SystemExit("Candidate source contains duplicate domains")
    if output_path.exists():
        raise SystemExit(f"Refusing to overwrite immutable observation: {output_path}")

    semaphore = asyncio.Semaphore(concurrency)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=browser_binary
        )

        async def worker(target: tuple[str, str]) -> dict:
            domain, requested_url = target
            async with semaphore:
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
                try:
                    return await observe(
                        page, domain, requested_url, timeout_ms, settle_ms
                    )
                finally:
                    await context.close()

        observations = await asyncio.gather(*(worker(target) for target in targets))
        await browser.close()

    result = {
        "schema_version": 1,
        "purpose": "candidate eligibility research; not provider benchmark evidence",
        "source_file": str(input_path.resolve().relative_to(ROOT)),
        "capture_contract": {
            "requested_page": "https://<ranked-domain>/",
            "anonymous_fresh_context_per_domain": True,
            "locale": "en-US",
            "timeout_ms": timeout_ms,
            "post_domcontentloaded_wait_ms": settle_ms,
            "adult_domains_present": False,
            "provider_calls": False,
        },
        "observations": observations,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Captured {len(observations)} observations -> {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "research/similarweb-global-candidate-domains-2026-05.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--browser-binary")
    parser.add_argument("--settle-ms", type=int, default=1200)
    args = parser.parse_args()
    asyncio.run(
        run(
            args.input,
            args.output,
            args.timeout_ms,
            args.concurrency,
            args.browser_binary,
            args.settle_ms,
        )
    )


if __name__ == "__main__":
    main()
