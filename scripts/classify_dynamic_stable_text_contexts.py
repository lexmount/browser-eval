#!/usr/bin/env python3
"""Classify stable-text candidates as page chrome or structured records.

The stable observations intentionally did not inspect provider output.  This
supplement performs one additional anonymous, no-interaction source load for
structured cases and records whether each already-observed candidate is
inside a link or record container whose URL matches the declared target path.
It does not decide anchors and it does not call the evaluated provider.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.capture_headless_browser_observations import chromium_host_rule, resolve_public_host


EXPRESSION = r"""
({hosts, entryPath, candidates}) => {
  const wanted = new Set(candidates);
  const pattern = entryPath ? new RegExp('^(?:' + entryPath + ')$') : null;
  const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
  const targetLink = link => {
    if (!pattern || !hosts.length || !link || !link.href) return null;
    try {
      const url = new URL(link.href, location.href);
      const path = url.pathname.replace(/\/$/, '') || '/';
      return hosts.includes(url.hostname) && pattern.test(path) ? url.href : null;
    } catch (_) { return null; }
  };
  const visible = element => {
    if (!element || element.closest('[hidden],[aria-hidden="true"]')) return false;
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden'
      && Number(style.opacity) !== 0 && element.getClientRects().length > 0;
  };
  const containerSelector = 'article,li,tr,[role="row"],[class*="card" i],'
    + '[class*="item" i],[class*="result" i],[class*="product" i],'
    + '[class*="news" i],[class*="story" i]';
  const rows = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, element = node.parentElement;
    const text = normalize(node.nodeValue);
    if (!wanted.has(text) || !visible(element)) continue;
    const ownLink = element.closest('a[href]');
    const directTarget = targetLink(ownLink);
    let recordTarget = directTarget;
    let container = element.closest(containerSelector);
    if (!recordTarget && container) {
      for (const link of container.querySelectorAll('a[href]')) {
        recordTarget = targetLink(link);
        if (recordTarget) break;
      }
    }
    const landmark = element.closest('nav,footer,header,main,aside');
    rows.push({
      text,
      tag: element.tagName.toLowerCase(),
      landmark: landmark ? landmark.tagName.toLowerCase() : 'body',
      linked: !!ownLink,
      href: ownLink ? ownLink.href : null,
      target_record: !!recordTarget,
      target_record_url: recordTarget,
      record_container: container ? container.tagName.toLowerCase() : null,
    });
  }
  return {rows, scrollX, scrollY};
}
"""


def digest_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-contexts", type=Path, action="append", default=[])
    parser.add_argument("--timeout-ms", type=int, default=45000)
    parser.add_argument("--browser-binary")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; classification evidence is immutable")
    dataset_raw = args.dataset.read_bytes()
    dataset = json.loads(dataset_raw)
    observed_raw = args.observations.read_bytes()
    observed = json.loads(observed_raw)
    if observed.get("dataset_sha256") != hashlib.sha256(dataset_raw).hexdigest():
        parser.error("observations are bound to a different dataset")
    skipped = set()
    skipped_inputs = []
    for path in args.skip_contexts:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if (payload.get("dataset_sha256") != hashlib.sha256(dataset_raw).hexdigest()
                or payload.get("source_observations_sha256") != hashlib.sha256(observed_raw).hexdigest()):
            parser.error(f"skip contexts are bound to different source evidence: {path}")
        skipped.update((payload.get("cases") or {}).keys())
        skipped_inputs.append({"sha256": hashlib.sha256(raw).hexdigest(), "cases": len(payload.get("cases") or {})})
    selected = [case for case in dataset["cases"]
                if (case.get("acceptance_contract") or {}).get("schema")
                and case["id"] not in skipped]
    checkpoint = args.output.with_name(args.output.name + ".partial")
    if checkpoint.exists():
        output = json.loads(checkpoint.read_text())
    else:
        output = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset_sha256": hashlib.sha256(dataset_raw).hexdigest(),
            "source_observations_sha256": hashlib.sha256(observed_raw).hexdigest(),
            "interaction": "one anonymous initial load; no click, input, scroll, search, or pagination",
            "skipped_context_inputs": skipped_inputs,
            "cases": {},
        }
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        for index, case in enumerate(selected, 1):
            if case["id"] in output["cases"]:
                print(f"Retained {index}/{len(selected)}: {case['id']}", flush=True)
                continue
            contract = case["acceptance_contract"]
            candidates = (observed.get("cases", {}).get(case["id"], {})
                          .get("common_ordinary_text_candidates", []))
            row = {
                "case_id": case["id"], "url": case["url"],
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "status": "failure", "rows": [],
            }
            browser = None
            try:
                host, address, method = resolve_public_host(case["url"])
                browser = playwright.chromium.launch(
                    headless=True, executable_path=args.browser_binary,
                    args=[f"--host-resolver-rules={chromium_host_rule(host, address)}"],
                )
                context = browser.new_context()
                page = context.new_page()
                started = time.monotonic()
                response = page.goto(case["url"], wait_until="domcontentloaded", timeout=args.timeout_ms)
                page.wait_for_timeout(min(1500, args.timeout_ms))
                classified = page.evaluate(EXPRESSION, {
                    "hosts": contract.get("hosts", []),
                    "entryPath": contract.get("entry_path"),
                    "candidates": candidates,
                })
                row.update({
                    "status": "success", "final_url": page.url,
                    "http_status": response.status if response else None,
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "scroll_x": classified.get("scrollX"),
                    "scroll_y": classified.get("scrollY"),
                    "rows": classified.get("rows", []),
                    "resolved_ip": address, "resolution_method": method,
                })
                context.close()
            except Exception as error:
                row["error_type"] = type(error).__name__
                row["error_message"] = str(error)[:2000]
            finally:
                if browser is not None:
                    browser.close()
            row["classification_sha256"] = digest_json(row)
            output["cases"][case["id"]] = row
            atomic_write(checkpoint, output)
            print(f"Classified {index}/{len(selected)}: {case['id']} ({row['status']})", flush=True)
    output["evidence_sha256"] = digest_json(output)
    atomic_write(args.output, output)
    checkpoint.unlink(missing_ok=True)
    print(json.dumps({
        "cases": len(output["cases"]),
        "failed": [key for key, row in output["cases"].items() if row["status"] != "success"],
        "evidence_sha256": output["evidence_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
