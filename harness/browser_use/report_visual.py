"""Review each Browser Use top-level page once and reject layout failures."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.report_languages import generated_languages
from harness.reporting.visual import (
    browser_executable as _browser_executable,
)
from harness.reporting.visual import (
    checker_sources,
    file_sha256,
    layout_passes,
    local_page,
    local_report_server,
    prepare_check,
    settle_theme,
    validate_routes,
    write_receipt,
)
from harness.reporting.visual import (
    receipt_is_current as _receipt_is_current,
)
from harness.reporting.visual import (
    tree_sha256 as _shared_tree_sha256,
)


def report_tree_sha256(root: Path) -> str:
    return _shared_tree_sha256(root, framed_paths=True)


def _routes(report: Path) -> list[str]:
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
    routes = fixed + (
        ["en/" + route for route in fixed] if "en" in generated_languages(report) else []
    )
    missing = [route for route in routes if not (report / route).is_file()]
    if missing:
        raise ValueError(f"visual QA route is missing: {missing[0]}")
    return routes
VISUAL_SCHEMA = "lex_browser_use_visual_qa/3"
VIEWPORTS = ((1440, 1000),)


def _state_passes(state: dict) -> bool:
    return layout_passes(state, empty_lists=("clipped", "vertical", "textOverflow"), false_flags=())


def receipt_is_current(report: Path, receipt: Path) -> bool:
    try:
        routes = _routes(report)
        validate_routes(report, routes)
        return _receipt_is_current(
            receipt,
            schema=VISUAL_SCHEMA,
            report_sha256=report_tree_sha256(report),
            source=Path(__file__),
            routes=routes,
            viewports=VIEWPORTS,
            state_passes=_state_passes,
            browser=_browser_executable,
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def check(report: Path, receipt: Path) -> None:
    prepare_check(report, receipt)
    from playwright.sync_api import sync_playwright

    source_hashes = checker_sources(Path(__file__))
    before = report_tree_sha256(report)
    executable = _browser_executable()
    browser_hash = file_sha256(Path(executable))
    routes = _routes(report)
    validate_routes(report, routes)
    states = []
    failures = []
    with local_report_server(report) as origin:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            try:
                for route in routes:
                    for width, height in VIEWPORTS:
                        for theme in ("light",):
                            page = local_page(
                                browser, origin, viewport={"width": width, "height": height}
                            )
                            page.goto(
                                origin + "/" + route,
                                wait_until="load",
                            )
                            page.evaluate(
                                "value => { document.documentElement.dataset.theme = value; }",
                                theme,
                            )
                            settle_theme(page)
                            result = page.evaluate(
                                """() => {
    const root=document.documentElement;
    const overflow=root.scrollWidth-root.clientWidth;
    const clipped=[];
    const selectors='.filters input,.filters select,.attempt-fact,'+
      '.evidence-card,.review-round,.action-card';
    for(const element of document.querySelectorAll(selectors)){
      if(!element.getClientRects().length)continue;
      const box=element.getBoundingClientRect();
      if(box.left < -2 || box.right > innerWidth + 2){
        clipped.push(element.className || element.tagName);
      }
    }
    const vertical=[];
    for(const button of document.querySelectorAll('th .sort-button')){
      const box=button.getBoundingClientRect(),style=getComputedStyle(button);
      const line=parseFloat(style.lineHeight)||16;
      if(button.textContent.trim().length>2 && box.height>line*2.6 && box.width<52){
        vertical.push(button.textContent.trim());
      }
    }
    const textOverflow=[];
    for(const element of document.querySelectorAll('.hero h1,.hero .lead')){
      if(element.scrollWidth > element.clientWidth + 2){
        textOverflow.push(element.tagName+'.'+element.className);
      }
    }
    return {overflow,clipped:[...new Set(clipped)],vertical,textOverflow};
    }
    """
                            )
                            state = {
                                "route": route,
                                "width": width,
                                "height": height,
                                "theme": theme,
                                **result,
                            }
                            states.append(state)
                            if not _state_passes(state):
                                failures.append(state)
                            page.close()
            finally:
                browser.close()
    if report_tree_sha256(report) != before or file_sha256(Path(executable)) != browser_hash:
        raise ValueError("visual QA inputs changed during rendering")
    if checker_sources(Path(__file__)) != source_hashes:
        raise ValueError("visual QA checker changed during rendering")
    write_receipt(
        receipt,
        {
            "schema": VISUAL_SCHEMA,
            "report_tree_sha256": before,
            "checker_sources": source_hashes,
            "browser_executable": executable,
            "browser_sha256": browser_hash,
            "route_classes": len(routes),
            "states_checked": len(states),
            "states": states,
            "failures": failures,
        },
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
