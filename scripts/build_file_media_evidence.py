#!/usr/bin/env python3
"""Derive document-link and media-resource tables from a persisted run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DISPLAY = {
    "cowagent-built-in": "CowAgent 内置 WebFetch",
    "lexmount-global": "Lexmount WebFetch Global",
    "lexmount-cn": "Lexmount WebFetch CN",
    "firecrawl-resources": "Firecrawl Scrape（links/images）",
    "tavily-resources": "Tavily Extract（include_images）",
    "exa-resources": "Exa Contents（extras links）",
    "cloudflare-markdown": "Cloudflare Markdown",
    "openclaw-built-in": "OpenClaw 内置 web_fetch",
    "deepseek-harness-built-in": "DeepSeek Harness web_fetch",
    "langchain-webbase": "LangChain WebBaseLoader",
    "crawl4ai": "Crawl4AI",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--evidence", type=Path, default=Path("research/file-media-capabilities.json"))
    parser.add_argument("--link-checks", type=Path, default=Path("research/resource-link-checks-2026-09-01.json"))
    args = parser.parse_args()
    records = [json.loads(line) for line in (args.run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    data = json.loads(args.evidence.read_text(encoding="utf-8"))
    link_checks = {}
    if args.link_checks.exists():
        checks = json.loads(args.link_checks.read_text(encoding="utf-8"))
        link_checks = {item["url"]: item for item in checks.get("checks", [])}
    else:
        checks = {"schema_version": 1, "checks": []}

    document_rows = []
    for record in records:
        if record["case_id"] not in {"direct-pdf", "direct-docx"}:
            continue
        results = record["metrics"].get("resource_results", [])
        matched = next((url for item in results for url in item.get("matched_urls", [])), None)
        check = link_checks.get(matched or "", {})
        document_rows.append({
            "method": DISPLAY.get(record["provider"], record["provider"]),
            "document": "PDF" if record["case_id"] == "direct-pdf" else "DOCX",
            "link_pass": bool(record["ok"] and record["metrics"].get("resource_pass")),
            "returned_url": matched,
            "observed_http": check.get("status"),
            "observed_mime": check.get("content_type"),
            "text_result": "正文通过" if record["ok"] and record["metrics"]["content_pass"] else "正文未通过",
            "attempt_id": record["attempt_id"],
        })

    media_rows = []
    for record in records:
        if record["case_id"] not in {"embedded-image-page", "embedded-video-page"}:
            continue
        hits = int(record["metrics"].get("resource_hits") or 0)
        total = int(record["metrics"].get("resource_total") or 0)
        matched_urls = [
            url
            for result in record["metrics"].get("resource_results", [])
            for url in result.get("matched_urls", [])
        ]
        verified = sum(bool(link_checks.get(url, {}).get("pass")) for url in matched_urls)
        media_rows.append({
            "method": DISPLAY.get(record["provider"], record["provider"]),
            "track": "图片" if record["case_id"] == "embedded-image-page" else "视频",
            "case": record["case_name"],
            "resource_recall": record["metrics"].get("resource_recall"),
            "observed": f"发现 {hits}/{total} 个必需资源",
            "link_validation": f"{verified}/{len(matched_urls)} 个返回链接通过 HTTP/最终 URL/MIME 核验" if matched_urls else "未返回可核验资源链接",
            "evidence": f"attempts/{record['attempt_id']}.html",
        })

    data["dataset_id"] = "webfetch-file-media-v2"
    data["actual_runs"] = list(dict.fromkeys([*data.get("actual_runs", []), args.run_dir.as_posix()]))
    data["document_link_checks"] = sorted(
        document_rows,
        key=lambda item: (not item["link_pass"], item["document"], item["method"]),
    )
    data["media_resource_results"] = sorted(
        media_rows,
        key=lambda item: (item["track"], -(item["resource_recall"] or 0), item["method"]),
    )
    data["resource_link_checks"] = checks
    args.evidence.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {args.evidence}: {len(document_rows)} document rows, {len(media_rows)} media rows")


if __name__ == "__main__":
    main()
