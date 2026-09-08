#!/usr/bin/env python3
"""Capture bounded visible-text observations for exact references that require a browser."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.reference_lifecycle import scope_identity
from harness.web_fetch.metrics import normalize_for_match


COMPLETION = {
    'javascript-spa': {
        'selector': '.quote',
        'minimum': 10,
        'description': 'browser-visible body text after all 10 quote cards rendered',
    },
    'live-025-api-reference': {
        'selector': 'main',
        'minimum': 1,
        'description': 'rendered main DOM text, including preloaded response-code panels',
        'include_hidden_dom_text': True,
    },
}


def capture(case: dict, timeout_ms: int, browser_binary: str | None = None,
            resolved_ip: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    rule = COMPLETION.get(case['id'])
    selector = (rule or {}).get('selector') or case.get('source_verification',{}).get('selector') or 'body'
    begin = time.monotonic()
    with sync_playwright() as playwright:
        launch_args = ([f'--host-resolver-rules=MAP quotes.toscrape.com {resolved_ip}']
                       if resolved_ip else [])
        browser = playwright.chromium.launch(headless=True, executable_path=browser_binary,
                                             args=launch_args)
        page = browser.new_page()
        page.goto(case['url'], wait_until='domcontentloaded', timeout=timeout_ms)
        if rule:
            page.wait_for_function(
                '([selector, minimum]) => document.querySelectorAll(selector).length >= minimum',
                arg=[selector, rule['minimum']], timeout=timeout_ms)
        else:
            page.locator(selector).wait_for(state='visible', timeout=timeout_ms)
        count = page.locator(selector).count()
        content_locator = page.locator('body' if case['id'] == 'javascript-spa' else selector)
        if rule and rule.get('include_hidden_dom_text'):
            content = content_locator.text_content(timeout=timeout_ms) or ''
            observation_method = 'rendered_dom_text_including_preloaded_hidden_panels'
        else:
            content = content_locator.inner_text(timeout=timeout_ms)
            observation_method = 'visible_inner_text'
        browser.close()
    elapsed = (time.monotonic() - begin) * 1000
    normalized = normalize_for_match(content)
    facts_present = all(any(normalize_for_match(value) in normalized
                            for value in fact['alternatives'])
                        for fact in case.get('required_facts', []))
    return {
        'case_id': case['id'], 'independent': True,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'content_text': content, 'selector': rule['description'] if rule else selector,
        'observation_method': observation_method,
        'observed_records': count, 'scope_identity': scope_identity(case),
        'observer': {'browser': 'Chromium', 'binary': 'explicit' if browser_binary else 'Playwright default',
                     'dns_override': bool(resolved_ip)},
        'completion': {'satisfied': count >= (rule['minimum'] if rule else 1) and facts_present,
                       'elapsed_ms': elapsed,
                       'timeout_ms': timeout_ms},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/dataset.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case-ids', default='javascript-spa')
    parser.add_argument('--timeout-ms', type=int, default=120000)
    parser.add_argument('--browser-binary')
    parser.add_argument('--resolved-ip', help='Audited temporary DNS override for quotes.toscrape.com')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; observations are immutable')
    cases = {case['id']: case for case in json.loads(args.dataset.read_text())['cases']}
    wanted = args.case_ids.split(',')
    if set(wanted) - cases.keys():
        parser.error('Unknown case ID')
    result = {cid: capture(cases[cid], args.timeout_ms, args.browser_binary, args.resolved_ip)
              for cid in wanted}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('Captured browser observations:', len(result))


if __name__ == '__main__':
    main()
