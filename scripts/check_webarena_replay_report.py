#!/usr/bin/env python3
"""Run deterministic release checks for the WebArena replay report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def _local_target(page: Path, raw: str) -> Path | None:
    parsed = urlsplit(raw)
    if parsed.scheme or raw.startswith(("#", "mailto:", "javascript:")):
        return None
    return (page.parent / unquote(parsed.path)).resolve()


def check(report_dir: Path) -> dict[str, int]:
    root = report_dir.resolve()
    manifest = json.loads((root / "report-manifest.json").read_text(encoding="utf-8"))
    results = json.loads((root / "data/results.json").read_text(encoding="utf-8"))
    if manifest["logical_results"] != 180 or len(results) != 180:
        raise ValueError("report must contain the complete 90-task participant matrix")
    failures = [item for item in results if item["status"] != "pass"]
    if len(failures) != 36 or manifest["reviewed_failures"] != 36:
        raise ValueError("report must contain all 36 reviewed final failures")
    if any(not item["reason_zh"] or not item["reason_en"] for item in failures):
        raise ValueError("every failed result needs a bilingual reviewed cause")

    zh_details = {path.name for path in (root / "result").glob("*.html")}
    en_details = {path.name for path in (root / "en/result").glob("*.html")}
    if zh_details != en_details or len(zh_details) != 180:
        raise ValueError("Chinese and English result routes must match one-to-one")
    core_names = (
        "index.html",
        "dataset.html",
        "results.html",
        "environment.html",
        "review.html",
        "evidence-viewer.html",
    )
    core = [
        *(root / name for name in core_names),
        *(root / "en" / name for name in core_names),
    ]
    forbidden = (
        "用户提示词",
        "我的思考",
        "assistant reasoning",
        "内部工作日志",
        "中位数",
        "median",
        "后续 179 题",
    )
    for page in core:
        text = page.read_text(encoding="utf-8")
        if any(term in text for term in forbidden):
            raise ValueError(f"formal report contains internal or retired prose: {page}")
        if "<svg" in text.lower():
            raise ValueError(f"report source contains a hand-authored SVG: {page}")

    checked_links = 0
    for page in root.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for raw in re.findall(r'(?:href|src)=["\']([^"\']+)', text):
            target = _local_target(page, raw)
            if target is None:
                continue
            checked_links += 1
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file() or (target != root and root not in target.parents):
                raise ValueError(f"broken or escaping local link in {page}: {raw}")

    distributable = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".json", ".js", ".css"}
    )
    if "/Users/" in distributable or re.search(r"sk-[A-Za-z0-9_-]{12,}", distributable):
        raise ValueError("report contains a local path or credential-like token")
    css = (root / "assets/report.css").read_text(encoding="utf-8")
    if ".inventory{height:" not in css or "overflow:auto" not in css:
        raise ValueError("result inventory must be fixed-height and horizontally scrollable")
    results_html = (root / "results.html").read_text(encoding="utf-8")
    if '<option value="fail">' not in results_html or 'id="diagnosis-dialog"' not in results_html:
        raise ValueError("result inventory must default to failure and open a diagnosis dialog")
    return {"html_pages": len(list(root.rglob("*.html"))), "checked_links": checked_links}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.report_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
