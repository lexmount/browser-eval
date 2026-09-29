#!/usr/bin/env python3
"""Capture bounded visible-text observations for exact references that require a browser."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.reference_lifecycle import (
    _resource_target_matches,
    requires_live_contract_check,
    resource_source_scope_identity,
    scope_identity,
)
from harness.web_fetch.metrics import normalize_for_match
from scripts.collect_source_evidence import extract_html


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

MAX_RESOURCE_CANDIDATES = 512


def evaluate_popular_source_contract(case: dict, source_html: str,
                                     rendered_text: str, visible_links: list[dict],
                                     final_url: str | None = None) -> dict:
    """Score only rendered source text and visible links; retain raw-DOM diagnosis."""
    from harness.web_fetch.stable_tasks import evaluate_stable_task

    contract = case['acceptance_contract']
    raw = evaluate_stable_task(source_html, contract, base_url=final_url or case['url'])
    projection = rendered_text
    if contract['schema'] == 'named_link':
        projection += '\n' + '\n'.join(
            '[{name}]({url})'.format(
                name=' '.join(link['name'].replace('[', ' ').replace(']', ' ').split()),
                url=link['url'])
            for link in visible_links if link.get('name') and link.get('url'))
    result = evaluate_stable_task(projection, contract,
                                  base_url=final_url or case['url'])
    return {
        'pass': result['stable_task_pass'],
        'raw_html_pass': raw['stable_task_pass'],
        'missing_anchor_ids': [anchor['id'] for anchor in result['stable_anchor_results']
                               if not anchor['pass']],
        'errors': result['stable_task_errors'],
        'record_count': result['stable_record_count'],
    }


def source_contract_sha256(case: dict) -> str:
    serialized = json.dumps(case['acceptance_contract'], ensure_ascii=False,
                            sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(serialized).hexdigest()


def match_resource_targets(case: dict, candidates: list[dict]) -> list[dict]:
    """Return one complete, deterministic target-to-observed-URL audit."""
    return _resource_target_matches(case, candidates)


def capture_resource_observation(page, case: dict, timeout_ms: int, begin: float) -> dict:
    """Observe bounded, already-rendered DOM resource URLs without fetching assets."""
    expression = """
() => {
  const rows = [];
  const add = (tag, attribute, value) => {
    if (typeof value !== 'string' || !value.trim()) return;
    try { rows.push({tag, attribute, url: new URL(value, document.baseURI).href}); }
    catch (_) {}
  };
  for (const node of document.querySelectorAll('img,source,video,a,meta,link')) {
    const tag = node.tagName.toLowerCase();
    if (tag === 'img') {
      add(tag, 'currentSrc', node.currentSrc); add(tag, 'src', node.getAttribute('src'));
      for (const item of (node.getAttribute('srcset') || '').split(',')) add(tag, 'srcset', item.trim().split(/\\s+/)[0]);
    } else if (tag === 'source') {
      add(tag, 'src', node.getAttribute('src'));
      for (const item of (node.getAttribute('srcset') || '').split(',')) add(tag, 'srcset', item.trim().split(/\\s+/)[0]);
    } else if (tag === 'video') {
      add(tag, 'currentSrc', node.currentSrc); add(tag, 'src', node.getAttribute('src')); add(tag, 'poster', node.getAttribute('poster'));
    } else if (tag === 'a') {
      add(tag, 'href', node.getAttribute('href'));
    } else if (tag === 'meta') {
      const key = (node.getAttribute('property') || node.getAttribute('name') || '').toLowerCase();
      if (/^(?:og|twitter):(image|video)(?::url)?$/.test(key)) add(tag, key, node.getAttribute('content'));
    } else if (tag === 'link') {
      const rel = (node.getAttribute('rel') || '').toLowerCase();
      const as = (node.getAttribute('as') || '').toLowerCase();
      if (rel.includes('image_src') || (rel.includes('preload') && ['image', 'video'].includes(as))) add(tag, `rel=${rel};as=${as}`, node.getAttribute('href'));
    }
  }
  return rows;
}
"""
    deadline = begin + timeout_ms / 1000
    candidates: list[dict] = []
    target_matches: list[dict] = []
    while time.monotonic() <= deadline:
        candidates = page.evaluate(expression)
        deduplicated = {(row['tag'], row['attribute'], row['url']): row for row in candidates}
        candidates = [deduplicated[key] for key in sorted(deduplicated)][:MAX_RESOURCE_CANDIDATES]
        target_matches = match_resource_targets(case, candidates)
        if target_matches and all(row['satisfied'] for row in target_matches):
            break
        page.wait_for_timeout(min(250, max(1, int((deadline - time.monotonic()) * 1000))))
    elapsed = (time.monotonic() - begin) * 1000
    candidate_bytes = json.dumps(candidates, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode()
    return {
        'content_text': '',
        'selector': 'bounded rendered DOM resource attributes',
        'observation_method': 'independent Chromium DOM resource observation (no provider output; assets not downloaded)',
        'observed_records': len(candidates),
        'resource_scope_identity': resource_source_scope_identity(case),
        'resource_candidates': candidates,
        'resource_candidates_sha256': hashlib.sha256(candidate_bytes).hexdigest(),
        'target_matches': target_matches,
        'completion': {
            'satisfied': bool(target_matches) and all(row['satisfied'] for row in target_matches),
            'elapsed_ms': min(elapsed, float(timeout_ms)),
            'timeout_ms': timeout_ms,
            'condition': 'all declared expected_resources observed in bounded rendered DOM candidates',
        },
    }


def extract_pruned_selector_text(outer_html: str, url: str) -> str:
    """Apply the canonical HTTP reference DOM scope to a rendered selector.

    The browser capture must not turn navigation or other site chrome back into
    reference text after ``collect_source_evidence.extract_html`` excluded it.
    Keeping the selected element's outer HTML also makes a selector that targets
    excluded chrome fail closed instead of exposing its children as body text.
    """
    document = f'<!doctype html><html><body>{outer_html}</body></html>'.encode()
    _title, text, _selector, _resources = extract_html(document, url)
    return text


def url_host(url: str) -> str:
    """Return one validated ASCII host suitable for a Chromium mapping rule."""
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('browser reference URL must use HTTP(S) and include a host')
    try:
        return parsed.hostname.encode('idna').decode('ascii').lower()
    except UnicodeError as error:
        raise ValueError('browser reference URL host is invalid') from error


def public_ip(value: str) -> str:
    """Validate and canonicalize an address before Chromium is allowed to use it."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError('resolved browser reference address is invalid') from error
    if not address.is_global:
        raise ValueError('resolved browser reference address is not public')
    return str(address)


def resolve_public_host(url: str, explicit_ip: str | None = None) -> tuple[str, str, str]:
    """Resolve a case host independently and reject local/non-public targets."""
    host = url_host(url)
    if explicit_ip is not None:
        return host, public_ip(explicit_ip), 'explicit_cli_override'
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RuntimeError('system DNS could not resolve browser reference host') from error
    candidates = []
    for answer in answers:
        try:
            address = public_ip(answer[4][0])
        except (IndexError, TypeError, ValueError):
            continue
        if address not in candidates:
            candidates.append(address)
    if not candidates:
        raise ValueError('system DNS returned no public address for browser reference host')
    # Prefer IPv4 because Chromium host-resolver rules accept it uniformly on
    # IPv4-only and dual-stack runners. Retain IPv6 support for IPv6-only hosts.
    candidates.sort(key=lambda value: ipaddress.ip_address(value).version)
    return host, candidates[0], 'system_dns_getaddrinfo'


def chromium_host_rule(host: str, resolved_ip: str) -> str:
    destination = (f'[{resolved_ip}]'
                   if ipaddress.ip_address(resolved_ip).version == 6 else resolved_ip)
    return f'MAP {host} {destination}'


def capture(case: dict, timeout_ms: int, browser_binary: str | None = None,
            resolved_ip: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    rule = COMPLETION.get(case['id'])
    selector = (rule or {}).get('selector') or case.get('source_verification',{}).get('selector') or 'body'
    begin = time.monotonic()
    live_contract = requires_live_contract_check(case)
    source_contract = None
    source_html_sha256 = None
    host = url_host(case['url'])
    if resolved_ip is not None:
        resolved_ip = public_ip(resolved_ip)
    with sync_playwright() as playwright:
        launch_args = ([f'--host-resolver-rules={chromium_host_rule(host, resolved_ip)}']
                       if resolved_ip else [])
        browser = playwright.chromium.launch(headless=True, executable_path=browser_binary,
                                             args=launch_args)
        try:
            page = browser.new_page()
            page.goto(case['url'], wait_until='domcontentloaded', timeout=timeout_ms)
            if case.get('task_kind') == 'resource':
                captured = capture_resource_observation(page, case, timeout_ms, begin)
                final_url = page.url
                title = page.title()
            elif live_contract:
                page.locator('body').wait_for(state='visible', timeout=timeout_ms)
            elif rule:
                page.wait_for_function(
                    '([selector, minimum]) => document.querySelectorAll(selector).length >= minimum',
                    arg=[selector, rule['minimum']], timeout=timeout_ms)
            else:
                page.locator(selector).wait_for(state='visible', timeout=timeout_ms)
            if case.get('task_kind') != 'resource':
                count = page.locator(selector).count()
                content_locator = page.locator('body' if case['id'] == 'javascript-spa' else selector)
                if live_contract:
                    source_html = page.content()
                    source_html_sha256 = hashlib.sha256(source_html.encode()).hexdigest()
                    content = page.locator('body').inner_text(timeout=timeout_ms)
                    visible_links = page.locator('a:visible').evaluate_all('''anchors => anchors
                        .filter(a => !a.closest('[hidden],[aria-hidden="true"]'))
                        .map(a => ({name: a.innerText.trim(), url: a.href}))
                        .filter(a => a.name && /^https?:/.test(a.url))''')
                    source_contract = evaluate_popular_source_contract(
                        case, source_html, content, visible_links, page.url)
                    observation_method = 'independent_rendered_source_contract_check'
                elif rule and rule.get('include_hidden_dom_text'):
                    content = content_locator.text_content(timeout=timeout_ms) or ''
                    observation_method = 'rendered_dom_text_including_preloaded_hidden_panels'
                elif rule:
                    # Special rendered references have explicit, reviewed capture
                    # semantics. Do not replace them with generic article pruning.
                    content = content_locator.inner_text(timeout=timeout_ms)
                    observation_method = 'visible_inner_text'
                else:
                    outer_html = content_locator.evaluate('(element) => element.outerHTML')
                    content = extract_pruned_selector_text(outer_html, case['url'])
                    observation_method = 'rendered_selector_dom_text_with_reference_scope_pruning'
        finally:
            browser.close()
    if case.get('task_kind') == 'resource':
        return {
            'case_id': case['id'], 'status': 'success', 'independent': True,
            'url': case['url'],
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'final_url': final_url, 'title': title,
            'scope_identity': scope_identity(case),
            'observer': {
                'browser': 'Chromium',
                'binary': 'explicit' if browser_binary else 'Playwright default',
                'dns_override': bool(resolved_ip),
                'host': host,
                'resolved_ip': resolved_ip,
                'resolution_method': 'caller_supplied' if resolved_ip else 'chromium_dns',
            },
            **captured,
        }
    elapsed = (time.monotonic() - begin) * 1000
    normalized = normalize_for_match(content)
    facts_present = all(any(normalize_for_match(value) in normalized
                            for value in fact['alternatives'])
                        for fact in case.get('required_facts', []))
    return {
        'case_id': case['id'], 'status': 'success', 'independent': True,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'content_text': content, 'selector': rule['description'] if rule else selector,
        'observation_method': observation_method,
        'observed_records': count, 'scope_identity': scope_identity(case),
        'observer': {
            'browser': 'Chromium',
            'binary': 'explicit' if browser_binary else 'Playwright default',
            'dns_override': bool(resolved_ip),
            'host': host,
            'resolved_ip': resolved_ip,
            'resolution_method': 'caller_supplied' if resolved_ip else 'chromium_dns',
        },
        'source_contract': source_contract,
        'source_contract_sha256': source_contract_sha256(case) if live_contract else None,
        'source_html_sha256': source_html_sha256,
        'completion': {'satisfied': bool(normalized)
                       and count >= (rule['minimum'] if rule else 1) and facts_present
                       and (source_contract is None or source_contract['pass']),
                       'elapsed_ms': elapsed,
                       'timeout_ms': timeout_ms,
                       'condition': ('frozen popular-site contract passes on independent DOM'
                                     if live_contract else 'declared browser reference observed')},
    }


def capture_with_retries(case: dict, timeout_ms: int, attempts: int,
                         browser_binary: str | None = None,
                         resolved_ip: str | None = None) -> dict:
    """Capture one case without allowing it to abort the rest of the batch."""
    errors = []
    last_row: dict = {}
    observer = {
        'browser': 'Chromium',
        'binary': 'explicit' if browser_binary else 'Playwright default',
        'dns_override': resolved_ip is not None,
        'host': None,
        'resolved_ip': None,
        'resolution_method': ('explicit_cli_override' if resolved_ip is not None
                              else 'system_dns_getaddrinfo'),
    }
    for attempt in range(1, attempts + 1):
        try:
            host, attempt_ip, resolution_method = resolve_public_host(
                case['url'], resolved_ip)
            observer.update({
                'dns_override': True,
                'host': host,
                'resolved_ip': attempt_ip,
                'resolution_method': resolution_method,
            })
            row = capture(case, timeout_ms, browser_binary, attempt_ip)
            last_row = row
            row['observer'].update(observer)
            if row['completion']['satisfied']:
                row['attempt_count'] = attempt
                row['attempt_errors'] = errors
                return row
            errors.append({
                'attempt': attempt,
                'type': 'IncompleteObservation',
                'message': 'browser capture did not satisfy declared completion',
            })
        except Exception as error:  # Playwright exposes several transient exception types.
            errors.append({
                'attempt': attempt,
                'type': type(error).__name__,
                'message': str(error)[:1000],
            })
    return {
        **last_row,
        'case_id': case['id'],
        'status': 'failure',
        'independent': True,
        'checked_at': last_row.get('checked_at', datetime.now(timezone.utc).isoformat()),
        'scope_identity': scope_identity(case),
        'observer': {**last_row.get('observer', {}), **observer},
        'attempt_count': attempts,
        'attempt_errors': errors,
        'completion': {
            'satisfied': False,
            'elapsed_ms': last_row.get('completion', {}).get('elapsed_ms'),
            'timeout_ms': timeout_ms,
            'condition': last_row.get('completion', {}).get('condition'),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/dataset.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case-ids', default='javascript-spa')
    parser.add_argument('--timeout-ms', type=int, default=120000)
    parser.add_argument('--attempts', type=int, default=2,
                        help='Bounded attempts per case (default: 2)')
    parser.add_argument('--browser-binary')
    parser.add_argument(
        '--resolved-ip',
        help='Audited public IP override, mapped only to each current case URL host')
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error('--attempts must be at least 1')
    if args.resolved_ip is not None:
        try:
            args.resolved_ip = public_ip(args.resolved_ip)
        except ValueError as error:
            parser.error(str(error))
    if args.output.exists():
        parser.error('Output exists; observations are immutable')
    cases = {case['id']: case for case in json.loads(args.dataset.read_text())['cases']}
    wanted = args.case_ids.split(',')
    if set(wanted) - cases.keys():
        parser.error('Unknown case ID')
    # Keep every case isolated. A DNS or renderer failure for one URL must not
    # erase successfully captured observations for the other requested cases.
    result = {
        cid: capture_with_retries(
            cases[cid], args.timeout_ms, args.attempts,
            args.browser_binary, args.resolved_ip)
        for cid in wanted
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    failures = [cid for cid, row in result.items()
                if row.get('status') != 'success'
                or not row.get('completion', {}).get('satisfied')]
    print('Captured browser observations:', len(result) - len(failures),
          'successful,', len(failures), 'failed')
    if failures:
        print('Failed browser observations:', ', '.join(failures), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
