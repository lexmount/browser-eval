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
    configured = os.environ.get("BROWSER_USE_VISUAL_BROWSER") or os.environ.get("CHROMIUM_BINARY")
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

    def do_GET(self) -> None:
        for prefix in ("/api/preview/replay~fixture/", "/api/preview/replay/"):
            if not self.path.startswith(prefix):
                continue
            route = self.path[len(prefix) :].split("?", 1)[0]
            target = (Path(self.directory) / route).resolve()
            if not target.is_relative_to(Path(self.directory).resolve()) or not target.is_file():
                self.send_error(404)
                return
            if prefix.endswith("~fixture/") and target.suffix == ".html":
                content = target.read_text(encoding="utf-8")
                content = content.replace(
                    "<head>",
                    '<head><base href="/api/preview/replay~fixture/">',
                    1,
                )
                encoded = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            self.path = "/" + route
            return super().do_GET()
        return super().do_GET()


def _first_failed_key(report: Path) -> str | None:
    results = json.loads((report / "data/results.json").read_text(encoding="utf-8"))
    return next((item["key"] for item in results if item["status"] == "fail"), None)


def _routes(report: Path) -> list[str]:
    results = json.loads((report / "data/results.json").read_text(encoding="utf-8"))
    failed_key = _first_failed_key(report)
    passed_key = next(
        (item["key"] for item in results if item["status"] == "pass"),
        None,
    )
    task_id = next((item.get("task_id") for item in results if item["status"] == "fail"), None)
    fixed = [
        "index.html",
        "dataset.html",
        "results.html",
        "interpretation.html",
        "environment.html",
        "review.html",
    ]
    routes = [*fixed, *(f"en/{item}" for item in fixed)]
    if task_id is not None and (report / "task" / f"{task_id}.html").is_file():
        routes.extend((f"task/{task_id}.html", f"en/task/{task_id}.html"))
    if failed_key is not None:
        routes.extend(
            (
                f"result/{failed_key}.html",
                f"en/result/{failed_key}.html",
                f"evidence-viewer.html?file=data/evidence/{failed_key}.json",
                f"en/evidence-viewer.html?file=../data/evidence/{failed_key}.json",
            )
        )
    if passed_key is not None and (report / "result" / f"{passed_key}.html").is_file():
        routes.extend((f"result/{passed_key}.html", f"en/result/{passed_key}.html"))
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
                failed_key = _first_failed_key(report)
                if failed_key is not None:
                    route_page = browser.new_page()
                    page_errors: list[str] = []
                    route_page.on("pageerror", lambda error: page_errors.append(str(error)))
                    route_page.goto(
                        f"http://127.0.0.1:{server.server_port}/api/preview/replay~fixture/result/{failed_key}.html",
                        wait_until="domcontentloaded",
                    )
                    route_page.wait_for_function(
                        "() => document.baseURI.includes('/api/preview/replay~fixture/result/')"
                    )
                    route_page.wait_for_function("() => !!document.documentElement.dataset.theme")
                    route_page.wait_for_function(
                        "() => getComputedStyle(document.body).backgroundColor"
                        " !== 'rgba(0, 0, 0, 0)'"
                    )
                    assert "/api/preview/replay~fixture/result/" in route_page.locator(
                        ".peer-list a"
                    ).first.evaluate("element => element.href")
                    assert "/api/preview/replay~fixture/evidence-viewer.html" in route_page.locator(
                        "#evidence .evidence-actions a"
                    ).first.evaluate("element => element.href")
                    step_button = route_page.locator(
                        ".aligned-step.failed-step button[data-open-step]"
                    ).first
                    if step_button.count() == 0:
                        step_button = route_page.locator(
                            ".aligned-step button[data-open-step]"
                        ).first
                    step_button.click()
                    try:
                        route_page.locator('#step-dialog[open]').wait_for(timeout=5_000)
                    except Exception as exc:
                        state = route_page.evaluate(
                            """() => ({dialog:document.querySelector('#step-dialog')?.open,
                            button:document.querySelector('.aligned-step button[data-open-step]')?.outerHTML,
                            fields:['title','status','planned','actual','result','error'].map(key=>[key,!!document.querySelector('#step-dialog [data-step-'+key+']')]),
                            script:[...document.scripts].map(item=>item.src),
                            theme:document.documentElement.dataset.theme})"""
                        )
                        raise ValueError(f"step dialog did not open: {state}; errors: {page_errors}") from exc
                    route_page.wait_for_function(
                        "() => document.querySelector('[data-step-planned]')?.textContent.includes('action')",
                        timeout=5_000,
                    )
                    assert route_page.locator('[data-step-actual]').inner_text()
                    assert route_page.locator('[data-step-result]').inner_text()
                    route_page.locator('[data-close-step]').click()
                    route_page.locator('nav a[href="../index.html"]').first.click()
                    route_page.wait_for_url(
                        f"http://127.0.0.1:{server.server_port}/api/preview/replay~fixture/index.html"
                    )
                    route_page.locator("h1").wait_for()
                    route_page.close()
                    for language in ("zh", "en"):
                        modal_page = browser.new_page(viewport={"width": 390, "height": 844})
                        prefix = "" if language == "zh" else "en/"
                        modal_page.goto(
                            f"http://127.0.0.1:{server.server_port}/{prefix}result/{failed_key}.html",
                            wait_until="domcontentloaded",
                        )
                        modal_step = modal_page.locator(
                            ".aligned-step.failed-step button[data-open-step]"
                        ).first
                        if modal_step.count() == 0:
                            modal_step = modal_page.locator(
                                ".aligned-step button[data-open-step]"
                            ).first
                        modal_step.click()
                        modal_page.wait_for_function(
                            "() => document.querySelector('[data-step-planned]')?.textContent.includes('action')",
                            timeout=5_000,
                        )
                        dialog_box = modal_page.locator("#step-dialog").bounding_box()
                        assert dialog_box and dialog_box["x"] >= 0
                        assert dialog_box["x"] + dialog_box["width"] <= 390
                        assert modal_page.locator("[data-step-result]").inner_text()
                        modal_page.locator("#step-dialog").screenshot(
                            path=str(screenshot_dir / f"step-dialog-{language}.png")
                        )
                        modal_page.close()
                drill_page = browser.new_page()
                drill_page.goto(
                    f"http://127.0.0.1:{server.server_port}/dataset.html",
                    wait_until="domcontentloaded",
                )
                drill_task = drill_page.locator("[data-dataset-table] tbody tr").first.get_attribute(
                    "data-task"
                )
                assert drill_task
                drill_page.locator("[data-dataset-table] tbody tr .dataset-actions a").nth(1).click()
                drill_page.wait_for_url(
                    f"http://127.0.0.1:{server.server_port}/results.html?task={drill_task}&status="
                )
                drill_page.locator('[data-results-ready="true"]').wait_for(timeout=5_000)
                participants = json.loads(
                    (report / "report-manifest.json").read_text(encoding="utf-8")
                )["participants"]
                assert drill_page.locator("[data-results] tbody tr:not([hidden])").count() == len(
                    participants
                )
                drill_page.close()
                for index, route in enumerate(_routes(report)):
                    for width, height in ((1440, 1000), (390, 844)):
                        for theme in ("light", "dark"):
                            page = browser.new_page(viewport={"width": width, "height": height})
                            url = f"http://127.0.0.1:{server.server_port}/{route}"
                            # The optional Mermaid module is hosted remotely; core report
                            # content and its local interactions are ready at DOMContentLoaded.
                            page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                            if "evidence-viewer" in route:
                                page.wait_for_function(
                                    """() => {
const node=document.querySelector('[data-evidence-viewer]');
return node && node.textContent.trim().startsWith('{');
}""",
                                    timeout=5_000,
                                )
                            if route.endswith("dataset.html"):
                                page.wait_for_function("() => document.querySelector('[data-dataset-count]')?.textContent.includes('/')", timeout=5_000)
                                page.locator('[data-dataset-filter="type"]').select_option("RETRIEVE")
                                page.wait_for_function("() => [...document.querySelectorAll('[data-dataset-table] tbody tr:not([hidden])')].every(row => row.dataset.type === 'RETRIEVE')")
                                visible_rows = page.locator('[data-dataset-table] tbody tr:not([hidden])')
                                assert visible_rows.count() > 0
                                task_id = visible_rows.first.get_attribute("data-task")
                                assert task_id
                                page.locator('[data-dataset-filter="q"]').fill(task_id)
                                assert page.locator(f'[data-dataset-table] tbody tr:not([hidden])[data-task="{task_id}"]').count() == 1
                                assert f"task={task_id}&status=" in page.locator(f'[data-dataset-table] tbody tr[data-task="{task_id}"] .dataset-actions a').last.get_attribute('href')
                                page.locator('[data-dataset-filter="q"]').fill("")
                                page.locator('[data-dataset-filter="type"]').select_option("")
                            if route.endswith("results.html"):
                                page.wait_for_function(
                                    "() => document.querySelector('[data-matrix-count]')"
                                    "?.textContent.includes('/')",
                                    timeout=5_000,
                                )
                                page.locator("[data-matrix-relation]").select_option("all-pass")
                                page.wait_for_function(
                                    "() => [...document.querySelectorAll("
                                    "'[data-task-matrix] tbody tr:not([hidden])')]"
                                    ".every(row => !row.dataset.fail)"
                                )
                                page.locator("[data-matrix-relation]").select_option("different")
                                page.wait_for_function(
                                    "() => [...document.querySelectorAll("
                                    "'[data-task-matrix] tbody tr:not([hidden])')]"
                                    ".every(row => !!row.dataset.pass && !!row.dataset.fail)"
                                )
                                assert page.locator("[data-matrix-method]").count() >= 2
                                page.locator('[data-results-ready="true"]').wait_for(timeout=5_000)
                                page.locator(
                                    ".inventory tbody tr:not([hidden]) [data-open-diagnosis]"
                                ).first.click()
                                page.locator("#diagnosis-dialog[data-loaded]").wait_for(
                                    timeout=5_000
                                )
                                page.locator(".dialog-head button").click()
                            page.evaluate(
                                "value => document.documentElement.dataset.theme=value",
                                theme,
                            )
                            result = page.evaluate(
                                """() => {
const root=document.documentElement;
const clipped=[];
const layoutSelector='h1,h2,.filters input,.filters select,'+
  '.diagnosis-grid article,.summary-grid article,.fact-strip>div';
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
                            if theme == "light" and (
                                route
                                in {
                                    "index.html",
                                    "results.html",
                                    "environment.html",
                                }
                                or route.startswith(("result/", "task/"))
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
