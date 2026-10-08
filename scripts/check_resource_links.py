#!/usr/bin/env python3
"""Verify frozen resource URLs independently of supplier outputs."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.reference_inputs import read_dataset_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/dataset.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    dataset = json.loads(read_dataset_bytes(args.dataset))
    expected: dict[str, list[str]] = {}
    for case in dataset["cases"]:
        for resource in case.get("expected_resources", []):
            mime_types = [str(item) for item in resource.get("mime_types", [])]
            for value in resource.get("urls", []):
                expected[urljoin(case["url"], value)] = mime_types
    checks = []
    for url, expected_mime in sorted(expected.items()):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "browser-harness-eval/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                checks.append({
                    "url": url,
                    "status": response.status,
                    "final_url": response.geturl(),
                    "content_type": response.headers.get_content_type(),
                    "expected_mime_types": expected_mime,
                    "pass": response.status < 400 and response.headers.get_content_type() in expected_mime,
                })
        except (urllib.error.URLError, TimeoutError) as error:
            checks.append({"url": url, "status": None, "final_url": None, "content_type": None, "expected_mime_types": expected_mime, "pass": False, "error": str(error)})
    payload = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "method": "HTTP HEAD；记录最终 URL、状态与 MIME",
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"checked {len(checks)} resource URLs; passed {sum(item['pass'] for item in checks)}")


if __name__ == "__main__":
    main()
