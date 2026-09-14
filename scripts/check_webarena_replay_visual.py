#!/usr/bin/env python3
"""Render every replay report route class and reject unreadable layouts."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _browser_executable() -> str:
    configured = os.environ.get("BROWSER_USE_VISUAL_BROWSER") or os.environ.get(
        "CHROMIUM_BINARY"
    )
    for candidate in (
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError("visual QA requires Chrome or Chromium")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


def _routes(report: Path) -> list[str]:
    results = json.loads((report / "data/results.json").read_text(encoding="utf-8"))
    failed_key = next(
        (item["key"] for item in results if item["status"] == "fail"),
        None,
    )
    fixed = [
        "index.html",
        "dataset.html",
        "results.html",
        "environment.html",
        "review.html",
    ]
    routes = [*fixed, *(f"en/{item}" for item in fixed)]
    if failed_key is not None:
        routes.extend(
            (
                f"result/{failed_key}.html",
                f"en/result/{failed_key}.html",
                f"evidence-viewer.html?file=data/evidence/{failed_key}.json",
                f"en/evidence-viewer.html?file=../data/evidence/{failed_key}.json",
            )
        )
    return routes


def check(report: Path, receipt: Path) -> None:
    report = report.resolve()
    receipt = receipt.resolve()
    screenshot_dir = receipt.parent / "trajectory-replay-visual"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    handler = lambda *args, **kwargs: _QuietHandler(  # noqa: E731
        *args, directory=str(report), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failures: list[dict[str, object]] = []
    states: list[dict[str, object]] = []
    executable = _browser_executable()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable,
                headless=True,
            )
            try:
                for index, route in enumerate(_routes(report)):
                    for width, height in ((1440, 1000), (390, 844)):
                        for theme in ("light", "dark"):
                            page = browser.new_page(viewport={"width": width, "height": height})
                            url = f"http://127.0.0.1:{server.server_port}/{route}"
                            page.goto(url, wait_until="load", timeout=10_000)
                            if "evidence-viewer" in route:
                                page.wait_for_function(
                                    """() => {
const node=document.querySelector('[data-evidence-viewer]');
return node && node.textContent.trim().startsWith('{');
}""",
                                    timeout=5_000,
                                )
                            page.evaluate(
                                "value => document.documentElement.dataset.theme=value",
                                theme,
                            )
                            result = page.evaluate(
                                """() => {
const root=document.documentElement;
const clipped=[];
const layoutSelector='h1,h2,.filters input,.filters select,'+
  '.diagnosis-grid article,.summary-grid article';
for(const element of document.querySelectorAll(layoutSelector)){
  if(!element.getClientRects().length)continue;
  const box=element.getBoundingClientRect();
  if(box.left < -2 || box.right > innerWidth + 2){
    clipped.push(element.tagName+'.'+element.className);
  }
}
const tiny=[];
for(const element of document.querySelectorAll('main p,main td,main th,main a,main button')){
  if(element.getClientRects().length &&
     parseFloat(getComputedStyle(element).fontSize) < 12){
    tiny.push(element.tagName);
  }
}
const viewer=document.querySelector('[data-evidence-viewer]');
const topology=document.querySelector('.mermaid svg,[data-topology-fallback]:not([hidden])');
const rawMermaid=[...document.querySelectorAll('.mermaid')].some(
  node => !node.hidden && node.textContent.includes('flowchart LR')
);
return {
  overflow:root.scrollWidth-root.clientWidth,
  clipped:[...new Set(clipped)],
  tiny:[...new Set(tiny)],
  viewerError:viewer ? /could not be loaded|未选择|No evidence/.test(viewer.textContent) : false,
  topologyMissing:location.pathname.endsWith('/environment.html') && !topology,
  rawMermaid
};
}"""
                            )
                            state = {
                                "route": route,
                                "width": width,
                                "theme": theme,
                                **result,
                            }
                            states.append(state)
                            if any(
                                (
                                    result["overflow"] > 2,
                                    result["clipped"],
                                    result["tiny"],
                                    result["viewerError"],
                                    result["topologyMissing"],
                                    result["rawMermaid"],
                                )
                            ):
                                failures.append(state)
                            if (
                                theme == "light"
                                and (
                                    route
                                    in {
                                        "index.html",
                                        "results.html",
                                        "environment.html",
                                    }
                                    or route.startswith("result/")
                                )
                            ):
                                shot = screenshot_dir / f"{index}-{width}.png"
                                page.screenshot(path=str(shot), full_page=True)
                            page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_replay_visual_qa/1",
                "report_tree_sha256": _tree_sha256(report),
                "browser_executable": executable,
                "route_classes": len(_routes(report)),
                "states_checked": len(states),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if failures:
        raise ValueError(f"visual QA failed: {failures[0]}")
    print(f"Trajectory replay visual QA passed: {len(states)} rendered states")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path("publish/trajectory-replay-visual-qa.json"),
    )
    args = parser.parse_args()
    check(args.report, args.receipt)


if __name__ == "__main__":
    main()
