"""Render every replay report route class and reject unreadable layouts."""

from __future__ import annotations

import argparse
import http.server
import json
import re
import threading
from pathlib import Path

from harness.reporting.visual import (
    browser_executable as _browser_executable,
)
from harness.reporting.visual import (
    checker_sources,
    file_sha256,
    layout_passes,
    local_page,
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
from harness.trajectory_replay.report_languages import report_languages


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(css: str) -> float:
        if not re.fullmatch(r"rgb\(\d+,\s*\d+,\s*\d+\)", css):
            raise ValueError(f"expected opaque computed RGB color: {css}")
        channels = [int(channel) / 255 for channel in re.findall(r"\d+", css)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(
            value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
        )

    values = sorted((luminance(foreground), luminance(background)))
    return (values[1] + 0.05) / (values[0] + 0.05)


def _tree_sha256(root: Path) -> str:
    return _shared_tree_sha256(root, framed_paths=False)


def _hosted_preview_error_message(errors: list[tuple[int, str]]) -> str:
    details = []
    for code, path in errors[:3]:
        if path.startswith("/api/preview/replay~fixture/"):
            cause = "missing file on versioned preview"
        elif path.startswith("/api/preview/replay/"):
            cause = "asset requested without preview version"
        else:
            cause = "hosted preview request failed"
        details.append(f"{code} {path} ({cause})")
    return "hosted preview HTTP errors: " + "; ".join(details)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def send_error(self, code: int, *args: object, **kwargs: object) -> None:
        # Both missing version credentials and missing files on a versioned route
        # break a hosted report; the final diagnostic identifies which occurred.
        if code >= 400 and self.path.startswith("/api/preview/"):
            getattr(self.server, "preview_errors", []).append((code, self.path))
        super().send_error(code, *args, **kwargs)

    def do_GET(self) -> None:
        # Artifact Site serves private HTML at the plain route but denies its assets.
        for prefix in ("/api/preview/replay~fixture/", "/api/preview/replay/"):
            if not self.path.startswith(prefix):
                continue
            route = self.path[len(prefix) :].split("?", 1)[0]
            target = (Path(self.directory) / route).resolve()
            if not target.is_relative_to(Path(self.directory).resolve()) or not target.is_file():
                self.send_error(404)
                return
            if prefix == "/api/preview/replay/" and target.suffix != ".html":
                self.send_error(404)
                return
            if target.suffix == ".html":
                content = target.read_text(encoding="utf-8")
                content = content.replace(
                    "<head>", '<head><base href="/api/preview/replay~fixture/">', 1
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


def _validate_hosted_resource_state(state: dict[str, str], origin: str, route: str) -> None:
    root = origin + "/api/preview/replay~fixture/"
    folder = route.rpartition("/")[0]
    expected_base = root + (folder + "/" if folder else "")
    expected = {
        "base": expected_base,
        "css": root + "assets/report.css",
        "script": root + "assets/app.js",
    }
    for key, target in expected.items():
        if state.get(key) != target:
            raise ValueError(f"hosted {route} {key} resolves outside its private version")
    if state.get("background") == "rgba(0, 0, 0, 0)":
        raise ValueError(f"hosted {route} report stylesheet did not apply")


def _check_hosted_resources(page, origin: str, route: str) -> None:
    page.wait_for_load_state("load")
    state = page.evaluate(
        """() => ({
            base: document.baseURI,
            css: document.querySelector('link[href$="assets/report.css"]')?.href || '',
            script: document.querySelector('script[src$="assets/app.js"]')?.src || '',
            background: getComputedStyle(document.body).backgroundColor,
        })"""
    )
    _validate_hosted_resource_state(state, origin, route)


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
    if task_id is None:
        task_id = next((item.get("task_id") for item in results), None)
    fixed = [
        "index.html",
        "dataset.html",
        "results.html",
        "environment.html",
        "review.html",
    ]
    if json.loads((report / "report-manifest.json").read_text()).get("interpretation"):
        fixed.extend(("interpretation.html", "article.html"))
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
        routes.extend(
            (
                f"result/{passed_key}.html",
                f"en/result/{passed_key}.html",
                f"evidence-viewer.html?file=data/evidence/{passed_key}.json",
                f"en/evidence-viewer.html?file=../data/evidence/{passed_key}.json",
            )
        )
    if "en" not in report_languages(report):
        routes = [route for route in routes if not route.startswith("en/")]
        routes.append("en/index.html")
    return routes


VISUAL_SCHEMA = "lex_webarena_replay_visual_qa/4"
VIEWPORTS = ((1440, 1000), (390, 844))


def _state_passes(state: dict) -> bool:
    return layout_passes(
        state,
        empty_lists=("clipped", "tiny", "low_contrast_headers"),
        false_flags=("viewerError", "topologyMissing", "rawMermaid"),
    )


def receipt_is_current(report: Path, receipt: Path) -> bool:
    try:
        routes = _routes(report)
        validate_routes(report, routes)
        return _receipt_is_current(
            receipt,
            schema=VISUAL_SCHEMA,
            report_sha256=_tree_sha256(report),
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
    before = _tree_sha256(report)
    executable = _browser_executable()
    browser_hash = file_sha256(Path(executable))
    routes = _routes(report)
    validate_routes(report, routes)
    report = report.resolve()
    receipt = receipt.resolve()
    screenshot_dir = receipt.parent / "trajectory-replay-visual"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    handler = lambda *args, **kwargs: _QuietHandler(  # noqa: E731
        *args, directory=str(report), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.preview_errors = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failures: list[dict[str, object]] = []
    states: list[dict[str, object]] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable,
                headless=True,
            )
            try:
                failed_key = _first_failed_key(report)
                hosted_origin = f"http://127.0.0.1:{server.server_port}"
                if failed_key is not None:
                    route_page = local_page(
                        browser,
                        hosted_origin,
                    )
                    page_errors: list[str] = []
                    route_page.on("pageerror", lambda error: page_errors.append(str(error)))
                    route_page.goto(
                        f"http://127.0.0.1:{server.server_port}/api/preview/replay~fixture/result/{failed_key}.html",
                        wait_until="domcontentloaded",
                    )
                    _check_hosted_resources(route_page, hosted_origin, f"result/{failed_key}.html")
                    route_page.wait_for_function("() => !!document.documentElement.dataset.theme")
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
                        route_page.locator("#step-dialog[open]").wait_for(timeout=5_000)
                    except Exception as exc:
                        state = route_page.evaluate(
                            """() => ({dialog:document.querySelector('#step-dialog')?.open,
                            button:document.querySelector(
                              '.aligned-step button[data-open-step]')?.outerHTML,
                            fields:['title','status','planned','actual','result','error']
                              .map(key=>[key,!!document.querySelector(
                                '#step-dialog [data-step-'+key+']')]),
                            script:[...document.scripts].map(item=>item.src),
                            theme:document.documentElement.dataset.theme})"""
                        )
                        raise ValueError(
                            f"step dialog did not open: {state}; errors: {page_errors}"
                        ) from exc
                    route_page.wait_for_function(
                        "() => document.querySelector('[data-step-planned]')"
                        "?.textContent.includes('action')",
                        timeout=5_000,
                    )
                    assert route_page.locator("[data-step-actual]").inner_text()
                    assert (
                        route_page.locator("[data-step-result]").inner_text()
                        or route_page.locator("[data-step-error]").inner_text()
                    )
                    route_page.keyboard.press("Escape")
                    assert route_page.locator("#step-dialog[open]").count() == 0
                    assert step_button.evaluate("element => element === document.activeElement")
                    if (report / "interpretation.html").is_file():
                        route_page.locator("nav a[data-report-interpretation]").click()
                        route_page.wait_for_url(
                            f"http://127.0.0.1:{server.server_port}/api/preview/replay~fixture/interpretation.html"
                        )
                        route_page.locator("article.interpretation h1").wait_for()
                        article_nav = route_page.locator("nav a[data-report-interpretation]")
                        assert article_nav.get_attribute("aria-current") == "page"
                    route_page.locator('nav a[href*="index.html"]').first.click()
                    route_page.wait_for_url(
                        re.compile(
                            re.escape(f"{hosted_origin}/api/preview/replay~fixture/index.html")
                            + r"(?:\?.*)?$"
                        )
                    )
                    route_page.locator("h1").wait_for()
                    _check_hosted_resources(route_page, hosted_origin, "index.html")
                    route_page.close()
                    for language in report_languages(report):
                        modal_page = local_page(
                            browser, hosted_origin, viewport={"width": 390, "height": 844}
                        )
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
                            "() => document.querySelector('[data-step-planned]')"
                            "?.textContent.includes('action')",
                            timeout=5_000,
                        )
                        dialog_box = modal_page.locator("#step-dialog").bounding_box()
                        assert dialog_box and dialog_box["x"] >= 0
                        assert dialog_box["x"] + dialog_box["width"] <= 390
                        assert (
                            modal_page.locator("[data-step-result]").inner_text()
                            or modal_page.locator("[data-step-error]").inner_text()
                        )
                        modal_page.locator("#step-dialog").screenshot(
                            path=str(screenshot_dir / f"step-dialog-{language}.png")
                        )
                        modal_page.keyboard.press("Escape")
                        assert modal_page.locator("#step-dialog[open]").count() == 0
                        assert modal_step.evaluate("element => element === document.activeElement")
                        modal_page.close()
                results = json.loads((report / "data/results.json").read_text(encoding="utf-8"))
                expected_failure_profiles = len(
                    {item["participant_id"] for item in results if item["status"] != "pass"}
                )
                passed_key = next(
                    (item["key"] for item in results if item["status"] == "pass"), None
                )
                if passed_key is not None:
                    passed_page = local_page(
                        browser,
                        hosted_origin,
                    )
                    passed_page.goto(
                        hosted_origin + f"/api/preview/replay~fixture/result/{passed_key}.html",
                        wait_until="domcontentloaded",
                    )
                    _check_hosted_resources(passed_page, hosted_origin, f"result/{passed_key}.html")
                    passed_page.close()
                drill_page = local_page(
                    browser,
                    hosted_origin,
                )
                if "en" not in report_languages(report):
                    drill_page.goto(
                        hosted_origin + "/api/preview/replay~fixture/index.html",
                        wait_until="domcontentloaded",
                    )
                    drill_page.locator("a.language").click()
                    drill_page.locator("[data-english-not-generated]").wait_for()
                    drill_page.locator('a[href="../index.html"]').click()
                    drill_page.wait_for_url(
                        hosted_origin + "/api/preview/replay~fixture/index.html"
                    )
                    _check_hosted_resources(drill_page, hosted_origin, "index.html")
                drill_page.goto(
                    f"http://127.0.0.1:{server.server_port}/dataset.html",
                    wait_until="domcontentloaded",
                )
                drill_task = drill_page.locator(
                    "[data-dataset-table] tbody tr"
                ).first.get_attribute("data-task")
                assert drill_task
                drill_page.locator("[data-dataset-table] tbody tr .dataset-actions a").nth(
                    1
                ).click()
                drill_page.wait_for_url(
                    f"http://127.0.0.1:{server.server_port}/results.html?task={drill_task}&status="
                )
                drill_page.locator('[data-results-ready="true"]').wait_for(timeout=5_000)
                measured_task_rows = [
                    row
                    for row in json.loads(
                        (report / "data/results.json").read_text(encoding="utf-8")
                    )
                    if str(row["task_id"]) == drill_task
                ]
                assert drill_page.locator("[data-results] tbody tr:not([hidden])").count() == len(
                    measured_task_rows
                )
                drill_page.close()
                for index, route in enumerate(routes):
                    for width, height in VIEWPORTS:
                        for theme in ("light", "dark"):
                            page = local_page(
                                browser, hosted_origin, viewport={"width": width, "height": height}
                            )
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
                                page.wait_for_function(
                                    "() => document.querySelector('[data-dataset-count]')"
                                    "?.textContent.includes('/')",
                                    timeout=5_000,
                                )
                                selected_type = page.locator(
                                    "[data-dataset-table] tbody tr"
                                ).first.get_attribute("data-type")
                                assert selected_type
                                page.locator('[data-dataset-filter="type"]').select_option(
                                    selected_type
                                )
                                page.wait_for_function(
                                    "type => [...document.querySelectorAll("
                                    "'[data-dataset-table] tbody tr:not([hidden])')]"
                                    ".every(row => row.dataset.type === type)",
                                    arg=selected_type,
                                )
                                visible_rows = page.locator(
                                    "[data-dataset-table] tbody tr:not([hidden])"
                                )
                                assert visible_rows.count() > 0
                                task_id = visible_rows.first.get_attribute("data-task")
                                assert task_id
                                page.locator('[data-dataset-filter="q"]').fill(task_id)
                                assert (
                                    page.locator(
                                        "[data-dataset-table] tbody tr:not([hidden])"
                                        f'[data-task="{task_id}"]'
                                    ).count()
                                    == 1
                                )
                                assert f"task={task_id}&status=" in page.locator(
                                    "[data-dataset-table] tbody "
                                    f'tr[data-task="{task_id}"] .dataset-actions a'
                                ).last.get_attribute("href")
                                page.locator('[data-dataset-filter="q"]').fill("")
                                page.locator('[data-dataset-filter="type"]').select_option("")
                            if route.endswith("index.html") and not route.startswith("en/"):
                                metrics = page.locator("table.metrics[data-enhanced=true]")
                                metrics.wait_for(timeout=5_000)
                                labels = metrics.locator("tbody tr td:first-child")
                                original = labels.all_inner_texts()
                                expected = page.evaluate(
                                    "items => [...items].sort((left, right) => "
                                    "left.toLocaleLowerCase().localeCompare("
                                    "right.toLocaleLowerCase(), 'zh'))",
                                    original,
                                )
                                metrics.locator("thead .table-sort").first.click()
                                ascending = labels.all_inner_texts()
                                metrics.locator("thead .table-sort").first.click()
                                descending = labels.all_inner_texts()
                                assert ascending == expected
                                assert descending == list(reversed(expected))
                                controls_id = metrics.get_attribute("data-table-controls")
                                assert controls_id is not None
                                table_filter = page.locator(
                                    f'[data-table-controls="{controls_id}"] [data-table-filter]'
                                )
                                table_filter.fill(original[0])
                                expected_matches = sum(
                                    original[0].casefold() in label.casefold() for label in original
                                )
                                assert (
                                    metrics.locator("tbody tr:not([hidden])").count()
                                    == expected_matches
                                )
                                assert all(
                                    original[0].casefold() in label.casefold()
                                    for label in metrics.locator(
                                        "tbody tr:not([hidden]) td:first-child"
                                    ).all_inner_texts()
                                )
                                table_filter.fill("")
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
                                page.locator('[data-filter="status"]').select_option("")
                                diagnosis_trigger = page.locator(
                                    ".inventory tbody tr:not([hidden]) [data-open-diagnosis]"
                                ).first
                                diagnosis_trigger.click()
                                page.locator("#diagnosis-dialog[data-loaded]").wait_for(
                                    timeout=5_000
                                )
                                page.keyboard.press("Escape")
                                assert page.locator("#diagnosis-dialog[open]").count() == 0
                                assert diagnosis_trigger.evaluate(
                                    "element => element === document.activeElement"
                                )
                            if route.endswith("index.html") and not route.startswith("en/"):
                                assert (
                                    page.locator("[data-failure-signatures] [data-method]").count()
                                    == expected_failure_profiles
                                )
                            if route.endswith(("interpretation.html", "article.html")):
                                assert (
                                    page.locator("[data-browser-failure-profile]").count()
                                    == expected_failure_profiles
                                )
                                assert (
                                    page.locator(
                                        "[data-browser-failure-profile] [data-cause-detail] a"
                                    ).count()
                                    > expected_failure_profiles
                                )
                            if route.endswith("article.html"):
                                # Script-free exports follow the OS preference, not
                                # the dashboard's JavaScript theme attribute.
                                page.emulate_media(color_scheme=theme)
                                page.evaluate("delete document.documentElement.dataset.theme")
                            else:
                                page.evaluate(
                                    "value => document.documentElement.dataset.theme=value",
                                    theme,
                                )
                            settle_theme(page)
                            result = page.evaluate(
                                """() => {
const root=document.documentElement;
const clipped=[];
const layoutSelector='h1,h2,.filters input,.filters select,'+
  '.diagnosis-grid article,.summary-grid article,.fact-strip>div,'+
  '.failure-signature-grid article,.failure-explanations article,.failure-bars li';
for(const element of document.querySelectorAll(layoutSelector)){
  if(!element.getClientRects().length)continue;
  const box=element.getBoundingClientRect();
  if(box.left < -2 || box.right > innerWidth + 2){
    clipped.push(element.tagName+'.'+element.className);
  }
}
const tiny=[];
for(const element of document.querySelectorAll('.successful-distributions td:first-child')){
  if(element.getClientRects().length && element.getBoundingClientRect().width < 140){
    clipped.push('successful-distributions metric label');
  }
}
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
                                "height": height,
                                "theme": theme,
                                "low_contrast_headers": [],
                                **result,
                            }
                            states.append(state)
                            if route.endswith(("article.html", "interpretation.html")):
                                colors = page.locator(".interpretation th").evaluate_all(
                                    "nodes=>nodes.map(e=>{const s=getComputedStyle(e);"
                                    "return [s.color,s.backgroundColor]})"
                                )
                                state["low_contrast_headers"] = [
                                    pair for pair in colors if _contrast_ratio(*pair) < 4.5
                                ]
                            if not _state_passes(state):
                                failures.append(state)
                            if theme == "light" and (
                                route
                                in {
                                    "index.html",
                                    "results.html",
                                    "environment.html",
                                    "interpretation.html",
                                    "article.html",
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
    if server.preview_errors:
        raise ValueError(_hosted_preview_error_message(server.preview_errors))
    if _tree_sha256(report) != before or file_sha256(Path(executable)) != browser_hash:
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
