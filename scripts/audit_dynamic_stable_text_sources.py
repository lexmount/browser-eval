#!/usr/bin/env python3
"""Capture three state-isolated source observations for stable-text review.

This command reads only the frozen dataset.  It never reads provider results or
artifacts and it never clicks, types, scrolls, searches, or paginates.  Every
observation uses a fresh anonymous Chromium context.  Candidate text is limited
to directly visible DOM text and is classified before review so button/control
text and image-associated text cannot become stable anchors accidentally.

The intersection emitted here is only a candidate inventory.  Three matching
loads do not by themselves prove that text is long-lived or independently
identifying; a later source-only review must make that judgment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.capture_headless_browser_observations import (
    chromium_host_rule,
    resolve_public_host,
)


VISIBLE_TEXT_EXPRESSION = r"""
() => {
  const root = document.body;
  if (!root) return {ordinary: [], excluded: [], totalTextNodes: 0};
  const ordinary = [], excluded = [];
  let totalTextNodes = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
  const visible = element => {
    if (!element || element.closest('[hidden],[aria-hidden="true"]')) return false;
    if (typeof element.checkVisibility === 'function') {
      try {
        return element.checkVisibility({
          checkOpacity: true, checkVisibilityCSS: true, contentVisibilityAuto: true
        });
      } catch (_) {}
    }
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden'
        || Number(style.opacity) === 0) return false;
    return element.getClientRects().length > 0;
  };
  const landmark = element => {
    const owner = element.closest('nav,footer,header,main,aside');
    return owner ? owner.tagName.toLowerCase() : 'body';
  };
  const reason = element => {
    if (element.closest('script,style,noscript,template')) return 'non_rendered_markup';
    if (element.closest('button,input,textarea,select,option,[role="button"],'
        + '[role="switch"],[role="checkbox"],[role="radio"],[role="slider"],'
        + '[contenteditable="true"]')) return 'interactive_control';
    if (element.closest('svg,canvas,picture,[role="img"]')) return 'image_or_graphic';
    return null;
  };
  while (walker.nextNode() && totalTextNodes < 50000) {
    totalTextNodes += 1;
    const node = walker.currentNode, element = node.parentElement;
    const text = normalize(node.nodeValue);
    if (!text || !element || !visible(element)) continue;
    const row = {
      text,
      tag: element.tagName.toLowerCase(),
      landmark: landmark(element),
      linked: !!element.closest('a[href]')
    };
    const excludedReason = reason(element);
    if (excludedReason) excluded.push({...row, reason: excludedReason});
    else ordinary.push(row);
  }
  return {ordinary, excluded, totalTextNodes};
}
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_text(value: str) -> str:
    return " ".join(value.split())


def digest_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def visible_target(page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() and locator.first.is_visible():
                return selector
        except Exception:
            continue
    return None


def capture_once(browser, case: dict, timeout_ms: int, ordinal: int,
                 resolved_ip: str) -> dict:
    context = browser.new_context()
    page = context.new_page()
    started = time.monotonic()
    try:
        response = page.goto(case["url"], wait_until="domcontentloaded",
                             timeout=timeout_ms)
        completion = case.get("source_verification", {}).get("completion", {})
        selectors = completion.get("target_selectors") or ["body"]
        selectors = [value for value in selectors if isinstance(value, str) and value]
        if not selectors:
            selectors = ["body"]
        stable_required = completion.get("stable_samples", 4)
        stable_required = stable_required if type(stable_required) is int else 4
        stable_required = max(1, min(stable_required, 12))
        poll_ms = completion.get("poll_interval_ms", 250)
        poll_ms = poll_ms if type(poll_ms) in (int, float) and math.isfinite(poll_ms) else 250
        poll_ms = max(50, min(int(poll_ms), 2000))
        deadline = started + timeout_ms / 1000
        last_digest = None
        stable_seen = 0
        target_selector = None
        snapshot = None
        while time.monotonic() < deadline:
            target_selector = visible_target(page, selectors)
            snapshot = page.evaluate(VISIBLE_TEXT_EXPRESSION)
            current = digest_json(snapshot.get("ordinary", []))
            if target_selector and current == last_digest and snapshot.get("ordinary"):
                stable_seen += 1
            elif target_selector and snapshot.get("ordinary"):
                stable_seen = 1
            else:
                stable_seen = 0
            last_digest = current
            if stable_seen >= stable_required:
                break
            page.wait_for_timeout(min(poll_ms, max(1, int((deadline-time.monotonic())*1000))))
        if snapshot is None:
            raise RuntimeError("No visible-text observation was produced")
        ordinary = snapshot.get("ordinary", [])
        excluded = snapshot.get("excluded", [])
        ordinary_texts = sorted({canonical_text(row.get("text", "")) for row in ordinary
                                 if canonical_text(row.get("text", ""))})
        excluded_counts: dict[str, int] = {}
        for row in excluded:
            key = row.get("reason", "unknown")
            excluded_counts[key] = excluded_counts.get(key, 0) + 1
        elapsed_ms = (time.monotonic() - started) * 1000
        result = {
            "ordinal": ordinal,
            "status": "success" if target_selector and stable_seen >= stable_required else "incomplete",
            "checked_at": utc_now(),
            "requested_url": case["url"],
            "final_url": page.url,
            "title": page.title(),
            "http_status": response.status if response is not None else None,
            "anonymous_context": True,
            "interaction": {
                "clicks": 0, "inputs": 0, "scrolls": 0,
                "searches": 0, "pagination_actions": 0,
            },
            "completion": {
                "target_selector": target_selector,
                "stable_samples_required": stable_required,
                "stable_samples_observed": stable_seen,
                "poll_interval_ms": poll_ms,
                "elapsed_ms": elapsed_ms,
                "timeout_ms": timeout_ms,
                "satisfied": bool(target_selector and stable_seen >= stable_required),
            },
            "ordinary_text_nodes": ordinary,
            "ordinary_texts": ordinary_texts,
            "excluded_text_summary": excluded_counts,
            "excluded_text_examples": excluded[:100],
            "total_text_nodes_examined": snapshot.get("totalTextNodes", 0),
        }
        result["observation_sha256"] = digest_json(result)
        return result
    finally:
        context.close()


def capture_case(playwright, case: dict, observations: int, timeout_ms: int,
                 browser_binary: str | None) -> dict:
    host, resolved_ip, resolution_method = resolve_public_host(case["url"])
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=browser_binary,
        args=[f"--host-resolver-rules={chromium_host_rule(host, resolved_ip)}"],
    )
    rows = []
    try:
        for ordinal in range(1, observations + 1):
            try:
                rows.append(capture_once(browser, case, timeout_ms, ordinal, resolved_ip))
            except Exception as error:
                rows.append({
                    "ordinal": ordinal,
                    "status": "failure",
                    "checked_at": utc_now(),
                    "requested_url": case["url"],
                    "anonymous_context": True,
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:2000],
                })
    finally:
        browser.close()
    successful = [row for row in rows if row.get("status") == "success"]
    intersection = set(successful[0]["ordinary_texts"]) if successful else set()
    for row in successful[1:]:
        intersection &= set(row["ordinary_texts"])
    return {
        "case_id": case["id"],
        "name": case["name"],
        "url": case["url"],
        "observer": {
            "browser": "Chromium",
            "state_isolation": "fresh browser context per observation; no supplied storage state",
            "host": host,
            "resolved_ip": resolved_ip,
            "resolution_method": resolution_method,
        },
        "observation_count": len(rows),
        "successful_observation_count": len(successful),
        "all_observations_complete": len(successful) == observations,
        "common_ordinary_text_candidates": sorted(intersection),
        "observations": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-ids", help="Comma-separated IDs; default is every dataset case")
    parser.add_argument("--observations", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=45000)
    parser.add_argument("--browser-binary")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; source observations are immutable")
    if args.observations < 3:
        parser.error("Stable-text review requires at least three observations")
    if args.timeout_ms < 1:
        parser.error("--timeout-ms must be positive")
    dataset = json.loads(args.dataset.read_text())
    indexed = {case["id"]: case for case in dataset["cases"]}
    wanted = args.case_ids.split(",") if args.case_ids else list(indexed)
    unknown = sorted(set(wanted) - set(indexed))
    if unknown:
        parser.error("Unknown case IDs: " + ", ".join(unknown))
    from playwright.sync_api import sync_playwright
    dataset_sha256 = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    protocol = {
        "observations_per_case": args.observations,
        "timeout_ms": args.timeout_ms,
        "anonymous": "fresh browser context without supplied cookies or storage state",
        "interaction": "initial automatic load only; no click, input, scroll, search, or pagination",
        "candidate_scope": "directly visible ordinary DOM text; controls and image/graphic text excluded",
        "warning": "candidate intersection is not an automatic stable-anchor decision",
    }
    checkpoint = args.output.with_name(args.output.name + ".partial")
    if checkpoint.exists():
        output = json.loads(checkpoint.read_text())
        if (output.get("dataset_sha256") != dataset_sha256
                or output.get("protocol") != protocol
                or set(output.get("cases", {})) - set(wanted)):
            parser.error("Existing checkpoint does not match this dataset, protocol, and case scope")
    else:
        output = {
            "schema_version": 1,
            "created_at": utc_now(),
            "dataset_sha256": dataset_sha256,
            "protocol": protocol,
            "cases": {},
        }
    with sync_playwright() as playwright:
        for index, case_id in enumerate(wanted, 1):
            if case_id in output["cases"]:
                print(f"Retained {index}/{len(wanted)} from checkpoint: {case_id}", flush=True)
                continue
            try:
                row = capture_case(
                    playwright, indexed[case_id], args.observations,
                    args.timeout_ms, args.browser_binary)
            except Exception as error:
                row = {
                    "case_id": case_id,
                    "name": indexed[case_id]["name"],
                    "url": indexed[case_id]["url"],
                    "observation_count": 0,
                    "successful_observation_count": 0,
                    "all_observations_complete": False,
                    "common_ordinary_text_candidates": [],
                    "observations": [],
                    "case_error": {
                        "checked_at": utc_now(),
                        "type": type(error).__name__,
                        "message": str(error)[:2000],
                    },
                }
            output["cases"][case_id] = row
            atomic_write_json(checkpoint, output)
            print(f"Captured {index}/{len(wanted)}: {case_id}", flush=True)
    output["evidence_sha256"] = digest_json(output)
    atomic_write_json(args.output, output)
    checkpoint.unlink()
    incomplete = [case_id for case_id, row in output["cases"].items()
                  if not row["all_observations_complete"]]
    print(json.dumps({"cases": len(wanted), "incomplete": incomplete,
                      "evidence_sha256": output["evidence_sha256"]}, ensure_ascii=False))
    if incomplete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
