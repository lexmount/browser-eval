"""Capture each Web Search chapter once on a local desktop browser."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lexbench_webfetch.paths import contained_path

from harness.reporting import visual as shared_visual
from harness.reporting.visual import (
    browser_executable,
    file_sha256,
    layout_passes,
    local_page,
    local_report_server,
    prepare_check,
    settle_theme,
    validate_routes,
    write_receipt,
)
from harness.web_search import pages
from harness.web_search.pages import reader_pages

SCHEMA = 'web-search-reader-capture/2'
WIDTH, HEIGHT = 1280, 900


def producer_sources() -> dict[str, str]:
    """Qualify same-named modules so neither producer binding is overwritten."""

    return {
        'harness/web_search/visual.py': file_sha256(Path(__file__)),
        'harness/web_search/pages.py': file_sha256(Path(pages.__file__)),
        'harness/reporting/visual.py': file_sha256(Path(shared_visual.__file__)),
    }


def routes(report: Path) -> list[tuple[str, str, str]]:
    manifest = json.loads((report / 'manifest.json').read_text())
    chapters = [(p["key"], p["route"]) for p in reader_pages((manifest.get("authored") or {}).get("presentation"), reviewed=bool(manifest.get("independent_review_sha256")))]
    languages = manifest.get('languages') or [manifest['primary_language']]
    result = [(lang, name, (lang + '/' if lang != 'zh' else '') + route)
              for lang in languages for name, route in chapters]
    validate_routes(report, [route for _, _, route in result])
    return result


def state_passes(state: dict) -> bool:
    return (layout_passes(state, empty_lists=('clipped', 'tiny', 'page_errors', 'failed_resources'))
            and type(state.get('heading_count')) is int and state['heading_count'] >= 1
            and state.get('theme') == 'light' and state.get('width') == WIDTH
            and state.get('height') == HEIGHT
            and state.get('page_language', '').split('-')[0] == state.get('language')
            and state.get('final_path') == state.get('route'))


def verify(report: Path, receipt: Path) -> bool:
    """Reject stale/legacy claims; accept only the maintained producer contract."""
    prepare_check(report, receipt)
    proof = json.loads(receipt.read_text())
    if (proof.get('schema') != SCHEMA or proof.get('producer_sources') != producer_sources()):
        raise ValueError('Incomplete visual producer proof; regenerate with check_search_report_visual.py')
    if proof.get('manifest_sha256') != file_sha256(report / 'manifest.json'):
        raise ValueError('Visual check belongs to a different build')
    binary = Path(browser_executable())
    if (proof.get('browser_executable') != str(binary)
            or proof.get('browser_sha256') != file_sha256(binary)
            or not isinstance(proof.get('browser_version'), str) or not proof['browser_version']):
        raise ValueError('Visual browser identity mismatch')
    expected = set(routes(report))
    captures = proof.get('captures')
    if not isinstance(captures, list) or len(captures) != len(expected):
        raise ValueError('Incomplete focused visual coverage')
    actual = set()
    for capture in captures:
        key = (capture.get('language'), capture.get('route_class'), capture.get('route'))
        if key in actual or key not in expected or not state_passes(capture):
            raise ValueError('Unreadable or duplicate visual state')
        actual.add(key)
        image = contained_path(receipt.parent, capture['path'], reject_symlinks=True)
        if image.suffix != '.png' or file_sha256(image) != capture.get('sha256'):
            raise ValueError('Visual evidence mismatch')
    if actual != expected:
        raise ValueError('Incomplete focused visual coverage')
    return True


def capture(report: Path, receipt: Path) -> Path:
    from playwright.sync_api import sync_playwright

    prepare_check(report, receipt)
    selected_routes = routes(report)
    manifest_hash = file_sha256(report / 'manifest.json')
    sources = producer_sources()
    executable = browser_executable()
    binary_hash = file_sha256(Path(executable))
    images = receipt.parent / (receipt.stem + '-captures')
    if images.is_symlink():
        raise ValueError('Visual screenshot directory must not be a symlink')
    images.mkdir(parents=True, exist_ok=True)
    captures = []
    with local_report_server(report) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            version = browser.version
            for lang, chapter, route in selected_routes:
                page = local_page(browser, origin, viewport={'width': WIDTH, 'height': HEIGHT}, color_scheme='light')
                errors, failed_resources = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('response', lambda response: failed_resources.append(str(response.status) + ' ' + response.url)
                        if response.status >= 400 and response.url.startswith(origin) else None)
                try:
                    response = page.goto(origin + '/' + route + ('?report-language=zh' if lang == 'zh' else ''), wait_until='load')
                    if response is None or not response.ok:
                        raise ValueError('Reader route failed: ' + route)
                    page.evaluate("document.body.classList.remove('dark');document.body.classList.add('light')")
                    settle_theme(page)
                    state = page.evaluate('''() => {
const clipped=[],tiny=[];
for(const node of document.querySelectorAll('h1,h2,nav a,input,select,button')){
  if(!node.getClientRects().length||node.closest('dialog:not([open])'))continue;
  const box=node.getBoundingClientRect();
  if(box.left < -2 || box.right > innerWidth+2)clipped.push(node.tagName+':'+node.textContent.trim().slice(0,60));
  if(parseFloat(getComputedStyle(node).fontSize)<12)tiny.push(node.tagName);
}
return {overflow:document.documentElement.scrollWidth-innerWidth,clipped,tiny,
heading_count:document.querySelectorAll('main h1').length,
page_language:document.documentElement.lang,final_path:decodeURIComponent(location.pathname).slice(1)};
}''')
                    state.update(language=lang, route_class=chapter, route=route, width=WIDTH,
                                 height=HEIGHT, theme='light', page_errors=errors,
                                 failed_resources=failed_resources)
                    if not state_passes(state):
                        raise ValueError('Reader layout failed: ' + json.dumps(state, ensure_ascii=False))
                    image = images / f'{lang}-{chapter}.png'
                    if image.is_symlink():
                        raise ValueError('Visual screenshot must not be a symlink')
                    page.screenshot(path=str(image), full_page=True)
                    state.update(path=image.relative_to(receipt.parent).as_posix(), sha256=file_sha256(image))
                    captures.append(state)
                finally:
                    page.close()
        finally:
            browser.close()
    if (manifest_hash != file_sha256(report / 'manifest.json') or sources != producer_sources()
            or binary_hash != file_sha256(Path(executable))):
        raise ValueError('Visual inputs changed during capture')
    write_receipt(receipt, {'schema': SCHEMA, 'producer_sources': sources,
                           'manifest_sha256': manifest_hash, 'browser_executable': executable,
                           'browser_sha256': binary_hash, 'browser_version': version, 'captures': captures})
    verify(report, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description='Capture Web Search top-level chapters once locally')
    parser.add_argument('report', type=Path)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args(argv)
    print(capture(args.report, args.receipt))
