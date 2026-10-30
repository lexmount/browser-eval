"""Compact generated replay detail files into query-addressed static bundles."""

# ruff: noqa: E501

from __future__ import annotations

import html
import json
import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from harness.reporting.visual import browser_executable, local_report_server

SHARD_COUNT = 16


def _task_id(value: str) -> int:
    match = re.search(r"(?:^|-)(\d+)$", value)
    if match is None:
        raise ValueError(f"compact route has no task id: {value}")
    return int(match.group(1))


def _rewrite_links(text: str, *, detail_kind: str | None = None,
                   source_route: str = 'index.html', target_route: str = 'index.html') -> str:
    def rewrite(match):
        name, raw = match[1], html.unescape(match[2])
        url = urlsplit(raw)
        if url.scheme or url.netloc or not url.path or raw.startswith('/'):
            return match[0]
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_route), url.path))
        target = resolved
        query = parse_qsl(url.query, keep_blank_values=True)
        route = re.fullmatch(r'((?:en/)?)task/(\d+)[.]html', resolved)
        result = re.fullmatch(r'((?:en/)?)result/([A-Za-z0-9_.-]+)[.]html', resolved)
        evidence = re.fullmatch(r'data/evidence/([A-Za-z0-9_.-]+)[.]json', resolved)
        if route or result:
            item = route or result
            target = item[1] + ('task.html' if route else 'result.html')
            query.insert(0, ('id' if route else 'key', item[2]))
        elif evidence:
            target = ('en/' if source_route.startswith('en/') else '') + 'evidence-raw.html'
            query.insert(0, ('key', evidence[1]))
        relative = posixpath.relpath(target, posixpath.dirname(target_route) or '.')
        return name + '="' + html.escape(urlunsplit(('', '', relative, urlencode(query), url.fragment)), quote=True) + '"'
    return re.sub(r'\b(href|src)="([^"]*)"', rewrite, text).replace(
        '>下载净化 JSON<', '>查看净化 JSON<').replace('>Download sanitized JSON<', '>Inspect sanitized JSON<')


def _javascript_source(source: str) -> str:
    doubled = chr(92) * 2
    while doubled in source:
        source = source.replace(doubled, chr(92))
    return source


def _loader(kind: str, lang: str = "zh") -> str:
    parameter = "id" if kind == "task" else "key"
    loading = "Loading detail…" if lang == "en" else "正在读取详情…"
    error = "Unable to read detail: " if lang == "en" else "详情读取失败："
    return _javascript_source(f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{"Replay detail" if lang == "en" else "轨迹重放详情"}</title></head><body><main><p>{loading}</p></main><script>
(async()=>{{
  const value=new URLSearchParams(location.search).get('{parameter}')||'';
  const match=value.match(/(?:^|-)(\\d+)$/);
  if(!match)throw Error('invalid {kind} route');
  const shard=String(Number(match[1])%{SHARD_COUNT}).padStart(2,'0');
  const response=await fetch(`pages/{kind}-${{shard}}.json`);
  if(!response.ok)throw Error(`HTTP ${{response.status}}`);
  const page=(await response.json())[value];
  if(!page)throw Error('{kind} route not found');
  document.open();document.write(page);document.close();
}})().catch(error=>document.querySelector('main').textContent='{error}'+error.message);
</script></body></html>''')


def _raw_evidence_loader(lang: str = "zh") -> str:
    title = "Sanitized evidence JSON" if lang == "en" else "单结果净化 JSON"
    back = "Back to result" if lang == "en" else "返回结果"
    loading = "Loading…" if lang == "en" else "正在读取…"
    error = "Unable to read evidence: " if lang == "en" else "证据读取失败："
    return _javascript_source(f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{margin:0;background:#071722;color:#e6edf3;font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}main{{max-width:1180px;margin:auto;padding:24px}}a{{color:#86b7ff}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style></head><body><main><p><a href="javascript:history.back()">{back}</a></p><h1>{title}</h1><pre>{loading}</pre></main><script>
(async()=>{{
  const key=new URLSearchParams(location.search).get('key')||'';
  const match=key.match(/(?:^|-)(\\d+)$/);
  if(!match)throw Error('invalid evidence route');
  const shard=String(Number(match[1])%{SHARD_COUNT}).padStart(2,'0');
  const response=await fetch(`{"../" if lang == "en" else ""}data/evidence-${{shard}}.json`);
  if(!response.ok)throw Error(`HTTP ${{response.status}}`);
  const value=(await response.json())[key];
  if(value===undefined)throw Error('evidence route not found');
  document.querySelector('pre').textContent=JSON.stringify(value,null,2);
}})().catch(error=>document.querySelector('pre').textContent='{error}'+error.message);
</script></body></html>''')


def _data_fetch_adapter() -> str:
    return _javascript_source(rf'''(()=>{{
const originalFetch=window.fetch.bind(window);const compactCache=new Map();
const compactBase=new URL("../",document.currentScript.src);
const compactRoute=raw=>{{
  const url=new URL(raw,location.href);const match=url.pathname.match(/data\/(trajectory|evidence)\/([A-Za-z0-9_.-]+)\.json$/);
  if(!match)return null;
  const kind=match[1];const key=match[2];const id=(key.match(/(?:^|-)(\\d+)$/)||[])[1];
  if(id===undefined)return null;
  return {{kind,key,shard:String(Number(id)%{SHARD_COUNT}).padStart(2,'0')}};
}};
window.fetch=async(input,init)=>{{
  const route=compactRoute(typeof input==='string'?input:input.url);
  if(!route)return originalFetch(input,init);
  const cacheKey=route.kind+'-'+route.shard;
  if(!compactCache.has(cacheKey))compactCache.set(cacheKey,originalFetch(new URL('data/'+route.kind+'-'+route.shard+'.json',compactBase)).then(response=>{{if(!response.ok)throw Error(`HTTP ${{response.status}}`);return response.json()}}));
  const value=(await compactCache.get(cacheKey))[route.key];
  return value===undefined?new Response('',{{status:404}}):new Response(JSON.stringify(value),{{status:200,headers:{{'content-type':'application/json'}}}});
}};
document.addEventListener('click',event=>{{
  const anchor=event.target.closest?.('a[href]');if(!anchor)return;
  const route=compactRoute(anchor.href);if(route?.kind!=='evidence')return;
  event.preventDefault();
  location.href='evidence-raw.html?key='+encodeURIComponent(route.key);
}},true);
}})();
''')


def compact_report(source: Path, destination: Path) -> dict[str, int]:
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    manifest = json.loads((source / 'report-manifest.json').read_text())
    languages = manifest.get('languages') or ['zh']
    prefixes = ['' if language == 'zh' else language + '/' for language in languages]
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        if any(relative.startswith(prefix + kind + '/') for prefix in prefixes for kind in ('task', 'result')):
            continue
        if relative.startswith(('data/trajectory/', 'data/evidence/')):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.html':
            target.write_text(_rewrite_links(path.read_text(), source_route=relative, target_route=relative))
        else:
            shutil.copy2(path, target)
    for prefix in prefixes:
        language = 'en' if prefix else 'zh'
        root = destination / prefix
        pages = root / 'pages'
        pages.mkdir()
        for kind in ('task', 'result'):
            buckets = [{} for _ in range(SHARD_COUNT)]
            for path in sorted((source / prefix / kind).glob('*.html')):
                key = path.stem
                buckets[_task_id(key) % SHARD_COUNT][key] = _rewrite_links(
                    path.read_text(), source_route=path.relative_to(source).as_posix(),
                    target_route=prefix + kind + '.html')
            for index, rows in enumerate(buckets):
                (pages / f'{kind}-{index:02d}.json').write_text(json.dumps(rows, ensure_ascii=False, separators=(',', ':')))
            (root / f'{kind}.html').write_text(_loader(kind, language))
        (root / 'evidence-raw.html').write_text(_raw_evidence_loader(language))
    (destination / 'data').mkdir(exist_ok=True)
    for kind in ('trajectory', 'evidence'):
        shards = [{} for _ in range(SHARD_COUNT)]
        for path in sorted((source / f'data/{kind}').glob('*.json')):
            shards[_task_id(path.stem) % SHARD_COUNT][path.stem] = json.loads(path.read_text())
        for index, rows in enumerate(shards):
            (destination / f'data/{kind}-{index:02d}.json').write_text(json.dumps(rows, ensure_ascii=False, separators=(',', ':')))
    app = destination / 'assets/app.js'
    app.write_text(_data_fetch_adapter() + app.read_text())
    return {'files': sum(path.is_file() for path in destination.rglob('*')),
            'task_routes': len(list((source / 'task').glob('*.html'))),
            'result_routes': len(list((source / 'result').glob('*.html')))}


def validate_compact_report(report: Path, *, max_files: int, source: Path | None = None) -> dict[str, int]:
    files = [path for path in report.rglob('*') if path.is_file()]
    if not files or len(files) > max_files:
        raise ValueError(f'compact report file count is outside 1..{max_files}: {len(files)}')
    manifest = json.loads((report / 'report-manifest.json').read_text())
    prefixes = ['' if lang == 'zh' else lang + '/' for lang in manifest.get('languages') or ['zh']]
    for prefix in prefixes:
        for kind, field in [('task', 'task_count'), ('result', 'logical_results')]:
            root = report / prefix
            if not (root / f'{kind}.html').is_file() or not (root / 'evidence-raw.html').is_file():
                raise ValueError('compact report route bundles are incomplete')
            values = {}
            for index in range(SHARD_COUNT):
                path = root / f'pages/{kind}-{index:02d}.json'
                if not path.is_file():
                    raise ValueError('compact report route bundles are incomplete')
                bucket = json.loads(path.read_text())
                if values.keys() & bucket.keys():
                    raise ValueError('duplicate compact logical route')
                values.update(bucket)
            if len(values) != manifest[field]:
                raise ValueError('compact report route coverage differs from its manifest')
            if source is not None:
                expected = {p.stem: _rewrite_links(p.read_text(), source_route=p.relative_to(source).as_posix(),
                            target_route=prefix + kind + '.html') for p in (source / prefix / kind).glob('*.html')}
                if values != expected:
                    raise ValueError('compact report pages differ from validated source')
    for kind in ('trajectory', 'evidence'):
        values = {}
        for index in range(SHARD_COUNT):
            path = report / f'data/{kind}-{index:02d}.json'
            if not path.is_file():
                raise ValueError('compact evidence bundles are incomplete')
            bucket = json.loads(path.read_text())
            if values.keys() & bucket.keys():
                raise ValueError('duplicate compact evidence key')
            values.update(bucket)
        if source is not None:
            expected = {p.stem: json.loads(p.read_text()) for p in (source / f'data/{kind}').glob('*.json')}
            if values != expected:
                raise ValueError('compact evidence differs from validated source')
    return {'files': len(files), 'task_routes': manifest['task_count'], 'result_routes': manifest['logical_results']}


def check_compact_navigation(report: Path) -> None:
    """Exercise the exported query routes and evidence, in every saved language."""
    from playwright.sync_api import sync_playwright


    manifest = json.loads((report / 'report-manifest.json').read_text())
    rows = json.loads((report / 'data/results.json').read_text())
    samples = [next((row for row in rows if (row['status'] == 'pass') == passed), None)
               for passed in (True, False)]
    with local_report_server(report) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_executable(), headless=True)
        try:
            for language in manifest.get('languages') or ['zh']:
                prefix = '' if language == 'zh' else language + '/'
                page = browser.new_page()
                for row in samples:
                    if row is None:
                        continue
                    page.goto(f"{origin}/{prefix}result.html?key={row['key']}")
                    page.locator('header.top').wait_for()
                    if page.locator('html').get_attribute('lang') != language:
                        raise ValueError('compact result language differs')
                    step = page.locator('button[data-open-step]').first
                    if step.count():
                        step.click()
                        page.locator('#step-dialog[open]').wait_for()
                        page.wait_for_function("() => document.querySelector('[data-step-planned]')?.textContent.includes('action')")
                        page.keyboard.press('Escape')
                    page.goto(f"{origin}/{prefix}evidence-raw.html?key={row['key']}")
                    page.wait_for_function("() => document.querySelector('pre')?.textContent.trim().startsWith('{')")
                    observed = json.loads(page.locator('pre').inner_text())
                    expected = json.loads((report / f"data/evidence-{int(row['task_id']) % SHARD_COUNT:02d}.json").read_text())[row['key']]
                    if observed != expected:
                        raise ValueError('compact served evidence differs from its bound payload')
                    page.goto(f"{origin}/{prefix}task.html?id={row['task_id']}")
                    page.locator('header.top').wait_for()
                    page.locator('a[href*="result.html?key="]').first.click()
                    page.locator('header.top').wait_for()
                    page.locator('nav a[href*="index.html"]').first.click()
                    page.locator('header.top').wait_for()
                page.close()
        finally:
            browser.close()
