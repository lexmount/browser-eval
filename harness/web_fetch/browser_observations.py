#!/usr/bin/env python3
"""Capture bounded visible-text observations for exact references that require a browser."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import socket
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from harness.reporting.publication import atomic_create, json_bytes
from lexbench_webfetch.metrics import normalize_for_match
from harness.web_fetch.network_dns import (
    configure_chromium_dns_profile,
    validate_dns_mode,
)
from harness.web_fetch.reference_inputs import read_dataset_bytes
from harness.web_fetch.reference_lifecycle import (
    _resource_target_matches,
    resource_source_scope_identity,
    scope_identity,
)
from lexbench_webfetch.paths import safe_identifier
from harness.web_fetch.source_capture import extract_html

# innerText includes opacity-zero content. Descend only through ancestors of
# transparent subtrees; unaffected branches retain the browser's text layout.
PAINTED_TEXT = r"""(root) => {
  for (let n = root; n; n = n.parentElement) {
    if (Number(getComputedStyle(n).opacity) === 0) return '';
  }
  const affected = new Set();
  for (const node of root.querySelectorAll('*')) {
    if (Number(getComputedStyle(node).opacity) !== 0) continue;
    for (let n = node.parentElement; n && !affected.has(n); n = n.parentElement) {
      affected.add(n);
      if (n === root) break;
    }
  }
  const read = node => {
    if (node.nodeType === Node.TEXT_NODE) {
      return getComputedStyle(node.parentElement).visibility === 'visible'
        ? node.textContent : '';
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    const style = getComputedStyle(node);
    if (style.display === 'none' || Number(style.opacity) === 0) return '';
    if (!affected.has(node)) return node.innerText || '';
    const block = !['inline', 'inline-block', 'contents'].includes(style.display);
    const text = Array.from(node.childNodes, read).join('');
    return block ? '\n' + text + '\n' : text;
  };
  return read(root).trim();
}"""


CONTRACTS = Path(__file__).parent / "data/browser-observation-contracts.json"


@dataclass(frozen=True)
class CapturePolicy:
    selector: str = "body"
    minimum: int = 1
    content_selector: str = "body"
    text_mode: str = "reference"
    description: str = "body"


def capture_policy(case: dict, observation_scope: str = "reference") -> CapturePolicy:
    if observation_scope == "visible_page":
        return CapturePolicy(text_mode="visible")
    if observation_scope != "reference":
        raise ValueError("Unknown browser observation scope")
    verification = case.get("source_verification", {})
    rule = verification.get("browser_observation")
    if rule is None:
        contracts = json.loads(CONTRACTS.read_bytes())
        if contracts.get("schema_version") != 1:
            raise ValueError("Unknown browser observation contract schema")
        registered = contracts["contracts"].get(case["id"])
        if registered is not None:
            fingerprint = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
            if fingerprint != registered["case_sha256"]:
                raise ValueError(
                    "Historical observation contract changed; declare browser_observation"
                )
            rule = registered["capture"]
    selector = verification.get("selector") or "body"
    if rule is None:
        rule = {"selector": selector}
    if not isinstance(rule, dict) or set(rule) - set(CapturePolicy.__dataclass_fields__):
        raise ValueError("Invalid browser observation policy")
    policy = CapturePolicy(
        **{
            "content_selector": rule.get("selector", "body"),
            "description": rule.get("selector", "body"),
            **rule,
        }
    )
    if (
        type(policy.minimum) is not int
        or policy.minimum < 1
        or policy.text_mode not in {"reference", "visible", "dom"}
        or any(
            not isinstance(value, str) or not value.strip()
            for value in (policy.selector, policy.content_selector, policy.description)
        )
    ):
        raise ValueError("Invalid browser observation policy")
    return policy


def remaining_ms(begin: float, timeout_ms: int) -> int:
    remaining = int(timeout_ms - (time.monotonic() - begin) * 1000)
    if remaining <= 0:
        raise TimeoutError("Browser observation deadline exceeded")
    return remaining


MAX_RESOURCE_CANDIDATES = 512


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
      for (const item of (node.getAttribute('srcset') || '').split(','))
        add(tag, 'srcset', item.trim().split(/\\s+/)[0]);
    } else if (tag === 'source') {
      add(tag, 'src', node.getAttribute('src'));
      for (const item of (node.getAttribute('srcset') || '').split(','))
        add(tag, 'srcset', item.trim().split(/\\s+/)[0]);
    } else if (tag === 'video') {
      add(tag, 'currentSrc', node.currentSrc); add(tag, 'src', node.getAttribute('src'));
      add(tag, 'poster', node.getAttribute('poster'));
    } else if (tag === 'a') {
      add(tag, 'href', node.getAttribute('href'));
    } else if (tag === 'meta') {
      const key = (node.getAttribute('property') || node.getAttribute('name') || '').toLowerCase();
      if (/^(?:og|twitter):(image|video)(?::url)?$/.test(key))
        add(tag, key, node.getAttribute('content'));
    } else if (tag === 'link') {
      const rel = (node.getAttribute('rel') || '').toLowerCase();
      const as = (node.getAttribute('as') || '').toLowerCase();
      if (rel.includes('image_src') || (rel.includes('preload') && ['image', 'video'].includes(as)))
        add(tag, `rel=${rel};as=${as}`, node.getAttribute('href'));
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
        deduplicated = {(row["tag"], row["attribute"], row["url"]): row for row in candidates}
        candidates = [deduplicated[key] for key in sorted(deduplicated)][:MAX_RESOURCE_CANDIDATES]
        target_matches = match_resource_targets(case, candidates)
        if target_matches and all(row["satisfied"] for row in target_matches):
            break
        page.wait_for_timeout(min(250, max(1, int((deadline - time.monotonic()) * 1000))))
    elapsed = (time.monotonic() - begin) * 1000
    candidate_bytes = json.dumps(
        candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "content_text": "",
        "selector": "bounded rendered DOM resource attributes",
        "observation_method": (
            "independent Chromium DOM resource observation "
            "(no provider output; assets not downloaded)"
        ),
        "observed_records": len(candidates),
        "resource_scope_identity": resource_source_scope_identity(case),
        "resource_candidates": candidates,
        "resource_candidates_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "target_matches": target_matches,
        "completion": {
            "satisfied": elapsed <= timeout_ms
            and bool(target_matches)
            and all(row["satisfied"] for row in target_matches),
            "elapsed_ms": elapsed,
            "timeout_ms": timeout_ms,
            "condition": (
                "all declared expected_resources observed in bounded rendered DOM candidates"
            ),
        },
    }


def extract_pruned_selector_text(outer_html: str, url: str) -> str:
    """Apply the canonical HTTP reference DOM scope to a rendered selector.

    The browser capture must not turn navigation or other site chrome back into
    reference text after ``collect_source_evidence.extract_html`` excluded it.
    Keeping the selected element's outer HTML also makes a selector that targets
    excluded chrome fail closed instead of exposing its children as body text.
    """
    document = f"<!doctype html><html><body>{outer_html}</body></html>".encode()
    _title, text, _selector, _resources = extract_html(document, url)
    return text


def url_host(url: str) -> str:
    """Return one validated ASCII host suitable for a Chromium mapping rule."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("browser reference URL must use HTTP(S) and include a host")
    try:
        return parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("browser reference URL host is invalid") from error


def public_ip(value: str) -> str:
    """Validate and canonicalize an address before Chromium is allowed to use it."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("resolved browser reference address is invalid") from error
    if not address.is_global:
        raise ValueError("resolved browser reference address is not public")
    return str(address)


def resolve_public_host(url: str, explicit_ip: str | None = None) -> tuple[str, str, str]:
    """Resolve a case host independently and reject local/non-public targets."""
    host = url_host(url)
    if explicit_ip is not None:
        return host, public_ip(explicit_ip), "explicit_cli_override"
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RuntimeError("system DNS could not resolve browser reference host") from error
    candidates = []
    for answer in answers:
        try:
            address = public_ip(answer[4][0])
        except (IndexError, TypeError, ValueError):
            continue
        if address not in candidates:
            candidates.append(address)
    if not candidates:
        raise ValueError("system DNS returned no public address for browser reference host")
    # Prefer IPv4 because Chromium host-resolver rules accept it uniformly on
    # IPv4-only and dual-stack runners. Retain IPv6 support for IPv6-only hosts.
    candidates.sort(key=lambda value: ipaddress.ip_address(value).version)
    return host, candidates[0], "system_dns_getaddrinfo"


def chromium_host_rule(host: str, resolved_ip: str) -> str:
    destination = (
        f"[{resolved_ip}]" if ipaddress.ip_address(resolved_ip).version == 6 else resolved_ip
    )
    return f"MAP {host} {destination}"


def capture(
    case: dict,
    timeout_ms: int,
    browser_binary: str | None = None,
    resolved_ip: str | None = None,
    *,
    observation_scope: str = "reference",
    dns_mode: str = "system",
) -> dict:
    from playwright.sync_api import sync_playwright

    if type(timeout_ms) is not int or timeout_ms < 1:
        raise ValueError("Browser observation timeout must be positive")
    dns_mode = validate_dns_mode(dns_mode)
    if dns_mode == "public_doh" and resolved_ip is not None:
        raise ValueError("public_doh cannot use a resolved-IP override")
    policy = capture_policy(case, observation_scope)
    begin = time.monotonic()
    host = url_host(case["url"])
    if resolved_ip is not None:
        resolved_ip = public_ip(resolved_ip)
    frames = []
    dns_metadata = {"dns_mode": dns_mode}
    browser_version = None
    primary_response_server_addr = None
    with sync_playwright() as playwright, ExitStack() as resources:
        launch_args = (
            [f"--host-resolver-rules={chromium_host_rule(host, resolved_ip)}"]
            if resolved_ip
            else []
        )
        context = None
        launch = dict(
            headless=True, executable_path=browser_binary, args=launch_args,
            timeout=remaining_ms(begin, timeout_ms),
        )
        if dns_mode == "public_doh":
            profile = resources.enter_context(
                tempfile.TemporaryDirectory(prefix="browser-observation-dns-")
            )
            dns_metadata = configure_chromium_dns_profile(profile, dns_mode)
            context = playwright.chromium.launch_persistent_context(profile, **launch)
            browser = context.browser
            page = context.new_page()
        else:
            browser = playwright.chromium.launch(**launch)
            page = browser.new_page()
        try:
            browser_version = browser.version if browser is not None else None
            response = page.goto(
                case["url"], wait_until="domcontentloaded", timeout=remaining_ms(begin, timeout_ms)
            )
            if response is not None:
                try:
                    address = response.server_addr()
                    if isinstance(address, dict):
                        ip_address = address.get("ip_address", address.get("ipAddress"))
                        primary_response_server_addr = {
                            **({"ip_address": ip_address} if ip_address else {}),
                            **({"port": address["port"]} if "port" in address else {}),
                        } or None
                except Exception:
                    primary_response_server_addr = None
            if case.get("task_kind") == "resource":
                captured = capture_resource_observation(page, case, timeout_ms, begin)
                final_url, title = page.url, page.title()
            else:
                page.wait_for_function(
                    "([selector, minimum]) => "
                    "document.querySelectorAll(selector).length >= minimum",
                    arg=[policy.selector, policy.minimum],
                    timeout=remaining_ms(begin, timeout_ms),
                )
                content_locator = page.locator(policy.content_selector)
                if content_locator.count() != 1:
                    raise ValueError(
                        "Browser observation content_selector must match exactly one element"
                    )
                if policy.text_mode == "visible":
                    content_locator.wait_for(
                        state="visible", timeout=remaining_ms(begin, timeout_ms)
                    )
                if observation_scope == "visible_page":
                    page.wait_for_timeout(min(2000, remaining_ms(begin, timeout_ms) / 2))
                final_url, title = page.url, page.title()
                document_language = page.locator("html").get_attribute(
                    "lang", timeout=remaining_ms(begin, timeout_ms)
                )
                outer_html = content_locator.evaluate("(element) => element.outerHTML")
                count = page.locator(policy.selector).count()
                if policy.text_mode == "dom":
                    content = (
                        content_locator.text_content(timeout=remaining_ms(begin, timeout_ms)) or ""
                    )
                    observation_method = "rendered_dom_text_including_preloaded_hidden_panels"
                elif policy.text_mode == "visible":
                    content = content_locator.evaluate(PAINTED_TEXT, timeout=remaining_ms(begin, timeout_ms))
                    observation_method = (
                        "painted_whole_page_text"
                        if observation_scope == "visible_page"
                        else "painted_visible_text"
                    )
                else:
                    content = extract_pruned_selector_text(outer_html, case["url"])
                    observation_method = "rendered_selector_dom_text_with_reference_scope_pruning"
                if observation_scope == "visible_page":
                    for frame in page.frames:
                        if frame == page.main_frame:
                            continue
                        try:
                            current, shown = frame, True
                            while current.parent_frame is not None:
                                if not current.frame_element().evaluate(
                                    "element => element.checkVisibility({opacityProperty: true, "
                                    "visibilityProperty: true})"
                                ):
                                    shown = False
                                    break
                                current = current.parent_frame
                            if not shown:
                                continue
                            text = frame.locator("body").evaluate(
                                PAINTED_TEXT, timeout=remaining_ms(begin, timeout_ms)
                            )
                            frames.append(
                                {"url": frame.url, "status": "success", "content_text": text}
                            )
                            if text.strip():
                                content += "\n\n[可见嵌入页面：" + frame.url + "]\n" + text
                        except Exception as error:
                            frames.append(
                                {"url": frame.url, "status": "failure", "error": str(error)[:1000]}
                            )
        finally:
            if context is not None:
                context.close()
            elif browser is not None:
                browser.close()
    observer = {
        "browser": "Chromium",
        "binary": "explicit" if browser_binary else "Playwright default",
        "dns_override": bool(resolved_ip),
        "host": host,
        "resolved_ip": resolved_ip,
        "resolution_method": (
            "chromium_secure_dns"
            if dns_mode == "public_doh"
            else "caller_supplied" if resolved_ip else "chromium_dns"
        ),
        "browser_version": browser_version,
        "primary_response_server_addr": primary_response_server_addr,
        **dns_metadata,
    }
    if case.get("task_kind") == "resource":
        return {
            "case_id": case["id"],
            "status": "success",
            "independent": True,
            "url": case["url"],
            "checked_at": datetime.now(UTC).isoformat(),
            "final_url": final_url,
            "title": title,
            "scope_identity": scope_identity(case),
            "observer": observer,
            **captured,
        }
    elapsed = (time.monotonic() - begin) * 1000
    normalized = normalize_for_match(content)
    facts_present = all(
        any(normalize_for_match(value) in normalized for value in fact["alternatives"])
        for fact in case.get("required_facts", [])
    )
    return {
        "case_id": case["id"],
        "status": "success",
        "independent": True,
        "url": case["url"],
        "final_url": final_url,
        "title": title,
        "document_language": document_language,
        "checked_at": datetime.now(UTC).isoformat(),
        "content_text": content,
        "selector": policy.description,
        "capture_policy": asdict(policy),
        "observation_method": observation_method,
        **(
            {"outer_html": outer_html, "settle_budget_ms": 2000, "visible_frames": frames}
            if observation_scope == "visible_page"
            else {}
        ),
        "observed_records": count,
        "scope_identity": scope_identity(case),
        "observer": observer,
        "completion": {
            "satisfied": bool(normalized)
            and elapsed <= timeout_ms
            and all(frame["status"] == "success" for frame in frames)
            and (
                observation_scope == "visible_page" or (count >= policy.minimum and facts_present)
            ),
            "elapsed_ms": elapsed,
            "timeout_ms": timeout_ms,
        },
    }


def capture_with_retries(
    case: dict,
    timeout_ms: int,
    attempts: int,
    browser_binary: str | None = None,
    resolved_ip: str | None = None,
    *,
    observation_scope: str = "reference",
    dns_mode: str = "system",
) -> dict:
    """Capture one case without allowing it to abort the rest of the batch."""
    dns_mode = validate_dns_mode(dns_mode)
    if dns_mode == "public_doh" and resolved_ip is not None:
        raise ValueError("public_doh cannot use a resolved-IP override")
    errors = []
    last_row: dict = {}
    observer = {
        "browser": "Chromium",
        "binary": "explicit" if browser_binary else "Playwright default",
        "dns_override": resolved_ip is not None,
        "host": None,
        "resolved_ip": None,
        "resolution_method": (
            "chromium_secure_dns"
            if dns_mode == "public_doh"
            else "explicit_cli_override" if resolved_ip is not None else "system_dns_getaddrinfo"
        ),
        "dns_mode": dns_mode,
        **(
            {"system_dns_fallback": False}
            if dns_mode == "public_doh"
            else {}
        ),
    }
    for attempt in range(1, attempts + 1):
        try:
            if dns_mode == "public_doh":
                host, attempt_ip, resolution_method = (
                    url_host(case["url"]), None, "chromium_secure_dns"
                )
            else:
                host, attempt_ip, resolution_method = resolve_public_host(case["url"], resolved_ip)
            observer.update(
                {
                    "dns_override": attempt_ip is not None,
                    "host": host,
                    "resolved_ip": attempt_ip,
                    "resolution_method": resolution_method,
                }
            )
            options = (
                {"observation_scope": observation_scope} if observation_scope != "reference" else {}
            )
            if dns_mode != "system":
                options["dns_mode"] = dns_mode
            row = capture(case, timeout_ms, browser_binary, attempt_ip, **options)
            last_row = row
            row["observer"].update(observer)
            if row["completion"]["satisfied"]:
                row["attempt_count"] = attempt
                row["attempt_errors"] = errors
                return row
            errors.append(
                {
                    "attempt": attempt,
                    "type": "IncompleteObservation",
                    "message": "browser capture did not satisfy declared completion",
                }
            )
        except Exception as error:  # Playwright exposes several transient exception types.
            errors.append(
                {
                    "attempt": attempt,
                    "type": type(error).__name__,
                    "message": str(error)[:1000],
                }
            )
    return {
        **last_row,
        "case_id": case["id"],
        "status": "failure",
        "independent": True,
        "checked_at": last_row.get("checked_at", datetime.now(UTC).isoformat()),
        "scope_identity": scope_identity(case),
        "observer": {**last_row.get("observer", {}), **observer},
        "attempt_count": attempts,
        "attempt_errors": errors,
        "completion": {
            "satisfied": False,
            "elapsed_ms": last_row.get("completion", {}).get("elapsed_ms"),
            "timeout_ms": timeout_ms,
            "condition": last_row.get("completion", {}).get("condition"),
        },
    }


def capture_source_identity() -> dict:
    names = (
        "browser_observations.py",
        "network_dns.py",
        "source_capture.py",
        "reference_lifecycle.py",
        "safety.py",
        "data/browser-observation-contracts.json",
    )
    root = Path(__file__).parent
    from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256

    return {**{name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names},
            "lexbench_webfetch/scoring": SCORING_SOURCE_SHA256}


def capture_batch(
    cases,
    *,
    timeout_ms,
    attempts,
    browser_binary=None,
    resolved_ip=None,
    dns_mode="system",
    observation_scope="reference",
    checkpoints=None,
    workers=1,
):
    """Checkpoint independent observations against capture code, policy and binary."""
    if any(type(value) is not int or value < 1 for value in (timeout_ms, attempts, workers)):
        raise ValueError("Browser observation budgets and workers must be positive integers")
    dns_mode = validate_dns_mode(dns_mode)
    if dns_mode == "public_doh" and resolved_ip is not None:
        raise ValueError("public_doh cannot use a resolved-IP override")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Invalid browser observation batch: duplicate case IDs")
    for case in cases:
        safe_identifier(case["id"])
        url_host(case["url"])
        capture_policy(case, observation_scope)
    if resolved_ip is not None:
        public_ip(resolved_ip)
    if checkpoints and not browser_binary:
        raise ValueError("Checkpoint reuse requires an explicit browser binary")
    sources = capture_source_identity()
    binary_sha = (
        hashlib.sha256(Path(browser_binary).read_bytes()).hexdigest() if browser_binary else None
    )
    if checkpoints:
        if checkpoints.is_symlink():
            raise ValueError("Browser observation checkpoints must not be symlinked")
        checkpoints.mkdir(parents=True, exist_ok=True)

    def guard():
        current_binary = (
            hashlib.sha256(Path(browser_binary).read_bytes()).hexdigest()
            if browser_binary
            else None
        )
        if current_binary != binary_sha or capture_source_identity() != sources:
            raise ValueError("Browser observation implementation changed during capture")

    def observe(case):
        guard()
        inputs = dict(
            case=case,
            timeout_ms=timeout_ms,
            attempts=attempts,
            binary_sha256=binary_sha,
            resolved_ip=resolved_ip,
            dns_mode=dns_mode,
            observation_scope=observation_scope,
            capture_schema=3,
            capture_sources=sources,
        )
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        checkpoint = checkpoints / (safe_identifier(case["id"]) + ".json") if checkpoints else None
        if checkpoint and checkpoint.is_symlink():
            raise ValueError("Browser observation checkpoint must not be symlinked")
        if checkpoint and checkpoint.exists():
            saved = json.loads(checkpoint.read_text())
            if (
                saved.get("input_sha256") != fingerprint
                or saved.get("observation", {}).get("case_id") != case["id"]
            ):
                raise ValueError("Checkpoint inputs changed: " + case["id"])
            if (
                saved.get("observation_sha256")
                != hashlib.sha256(
                    json.dumps(saved["observation"], sort_keys=True).encode()
                ).hexdigest()
            ):
                raise ValueError("Checkpoint observation changed: " + case["id"])
            guard()
            return case["id"], saved["observation"]
        options = (
            {"observation_scope": observation_scope} if observation_scope != "reference" else {}
        )
        if dns_mode != "system":
            options["dns_mode"] = dns_mode
        row = capture_with_retries(
            case, timeout_ms, attempts, browser_binary, resolved_ip, **options
        )
        guard()
        if checkpoint:
            atomic_create(
                checkpoint,
                json_bytes(
                    dict(
                        input_sha256=fingerprint,
                        observation=row,
                        observation_sha256=hashlib.sha256(
                            json.dumps(row, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                ),
            )
        return case["id"], row

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(observe, cases))


def main(argv=None, *, root: Path | None = None) -> None:
    root = root or Path.cwd()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=root / "evaluation/dataset.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-ids", default="javascript-spa")
    parser.add_argument("--timeout-ms", type=int, default=120000)
    parser.add_argument(
        "--attempts", type=int, default=2, help="Bounded attempts per case (default: 2)"
    )
    parser.add_argument("--browser-binary")
    parser.add_argument(
        "--observation-scope", choices=("reference", "visible_page"), default="reference"
    )
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dns-mode", choices=("system", "public_doh"), default="system")
    parser.add_argument(
        "--resolved-ip",
        help="Audited public IP override, mapped only to each current case URL host",
    )
    args = parser.parse_args(argv)
    if min(args.attempts, args.timeout_ms, args.workers) < 1:
        parser.error("Attempts, timeout and workers must be positive")
    if args.resolved_ip is not None:
        try:
            args.resolved_ip = public_ip(args.resolved_ip)
        except ValueError as error:
            parser.error(str(error))
    if args.dns_mode == "public_doh" and args.resolved_ip is not None:
        parser.error("public_doh cannot use --resolved-ip")
    if args.output.exists() or args.output.is_symlink():
        parser.error("Output exists; observations are immutable")
    rows = json.loads(read_dataset_bytes(args.dataset))["cases"]
    cases = {case["id"]: case for case in rows}
    if len(cases) != len(rows):
        parser.error("Duplicate dataset case IDs")
    wanted = args.case_ids.split(",")
    if len(wanted) != len(set(wanted)):
        parser.error("Duplicate requested case IDs")
    if set(wanted) - cases.keys():
        parser.error("Unknown case ID")
    # Keep every case isolated. A DNS or renderer failure for one URL must not
    # erase successfully captured observations for the other requested cases.
    result = capture_batch(
        [cases[cid] for cid in wanted],
        timeout_ms=args.timeout_ms,
        attempts=args.attempts,
        browser_binary=args.browser_binary,
        resolved_ip=args.resolved_ip,
        dns_mode=args.dns_mode,
        observation_scope=args.observation_scope,
        checkpoints=args.checkpoint_dir,
        workers=args.workers,
    )
    atomic_create(args.output, json_bytes(result))
    failures = [
        cid
        for cid, row in result.items()
        if row.get("status") != "success" or not row.get("completion", {}).get("satisfied")
    ]
    print(
        "Captured browser observations:",
        len(result) - len(failures),
        "successful,",
        len(failures),
        "failed",
    )
    if failures:
        print("Failed browser observations:", ", ".join(failures), file=sys.stderr)
        raise SystemExit(1)
