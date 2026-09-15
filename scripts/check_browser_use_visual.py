#!/usr/bin/env python3
"""Render every Browser Use route class and reject structural layout failures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def report_tree_sha256(report: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in report.rglob("*") if item.is_file()):
        relative = path.relative_to(report).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _browser_executable() -> str:
    configured = os.environ.get("BROWSER_USE_VISUAL_BROWSER") or os.environ.get(
        "CHROMIUM_BINARY"
    )
    candidates = (
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError(
        "visual QA requires Chrome/Chromium; set BROWSER_USE_VISUAL_BROWSER to its executable"
    )


def _route_classes(report: Path) -> list[Path]:
    fixed = [
        "index.html",
        "datasets/index.html",
        "methodology/index.html",
        "analysis/index.html",
        "review/index.html",
        "changes/index.html",
        "adoption/index.html",
        "reproduce/index.html",
        "accounts/index.html",
    ]
    dynamic = []
    for folder in ("datasets", "results", "engines"):
        pages = sorted((report / folder).glob("*.html"))
        if pages:
            dynamic.append(pages[0].relative_to(report).as_posix())
        if folder == "results":
            failed_page = next(
                (page for page in pages if 'id="diagnosis"' in page.read_text(encoding="utf-8")),
                None,
            )
            if failed_page:
                failed_route = failed_page.relative_to(report).as_posix()
                if failed_route not in dynamic:
                    dynamic.append(failed_route)
    chinese = [report / name for name in (*fixed, *dynamic)]
    routes = chinese + [report / "en" / page.relative_to(report) for page in chinese]
    missing = [path for path in routes if not path.is_file()]
    if missing:
        raise ValueError(f"visual QA route is missing: {missing[0]}")
    return routes


def check(report: Path, receipt: Path) -> None:
    states = []
    failures = []
    executable = _browser_executable()
    routes = _route_classes(report)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            for route in routes:
                for width, height in ((1440, 1000), (820, 1180), (390, 844)):
                    for theme in ("light", "dark"):
                        page = browser.new_page(viewport={"width": width, "height": height})
                        page.goto(route.resolve().as_uri(), wait_until="load")
                        page.evaluate(
                            "value => { document.documentElement.dataset.theme = value; }", theme
                        )
                        result = page.evaluate(
                            """() => {
const root=document.documentElement;
const overflow=root.scrollWidth-root.clientWidth;
const clipped=[];
for(const element of document.querySelectorAll('.filters input,.filters select,.attempt-fact,.evidence-card,.review-round,.action-card')){
  if(!element.getClientRects().length)continue;
  const box=element.getBoundingClientRect();
  if(box.left < -2 || box.right > innerWidth + 2)clipped.push(element.className || element.tagName);
}
const vertical=[];
for(const button of document.querySelectorAll('th .sort-button')){
  const box=button.getBoundingClientRect(),style=getComputedStyle(button),line=parseFloat(style.lineHeight)||16;
  if(button.textContent.trim().length>2 && box.height>line*2.6 && box.width<52)vertical.push(button.textContent.trim());
}
const textOverflow=[];
for(const element of document.querySelectorAll('.hero h1,.hero .lead')){
  if(element.scrollWidth > element.clientWidth + 2)textOverflow.push(element.tagName+'.'+element.className);
}
return {overflow,clipped:[...new Set(clipped)],vertical,textOverflow};
}
"""
                        )
                        state = {
                            "route": route.relative_to(report).as_posix(),
                            "width": width,
                            "height": height,
                            "theme": theme,
                            **result,
                        }
                        states.append(state)
                        if (
                            result["overflow"] > 2
                            or result["clipped"]
                            or result["vertical"]
                            or result["textOverflow"]
                        ):
                            failures.append(state)
                        page.close()
        finally:
            browser.close()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "lex_browser_use_visual_qa/1",
                "report_tree_sha256": report_tree_sha256(report),
                "browser_executable": executable,
                "route_classes": len(routes),
                "states_checked": len(states),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if failures:
        first = failures[0]
        raise ValueError(
            f"visual QA failed at {first['route']} {first['width']}px {first['theme']}: {first}"
        )
    print(f"Browser Use visual QA passed: {len(states)} rendered states")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path("publish/browser-use-visual-qa.json"),
    )
    args = parser.parse_args()
    check(args.report, args.receipt)


if __name__ == "__main__":
    main()
