"""Compact generated replay detail files into query-addressed static bundles."""

# ruff: noqa: E501

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

SHARD_COUNT = 16


def _task_id(value: str) -> int:
    match = re.search(r"(?:^|-)(\d+)$", value)
    if match is None:
        raise ValueError(f"compact route has no task id: {value}")
    return int(match.group(1))


def _rewrite_links(text: str, *, detail_kind: str | None = None) -> str:
    text = re.sub(
        r'href="(?:\.\./)?task/(\d+)\.html([#?][^"]*)?"',
        lambda match: f'href="task.html?id={match.group(1)}{match.group(2) or ""}"',
        text,
    )
    text = re.sub(
        r'href="(?:\.\./)?result/([A-Za-z0-9_.-]+)\.html([#?][^"]*)?"',
        lambda match: f'href="result.html?key={match.group(1)}{match.group(2) or ""}"',
        text,
    )
    if detail_kind == "result":
        text = re.sub(
            r'href="([A-Za-z0-9_.-]+-\d+)\.html([#?][^"]*)?"',
            lambda match: f'href="result.html?key={match.group(1)}{match.group(2) or ""}"',
            text,
        )
    text = re.sub(
        r'href="(?:\.\./)*data/evidence/([A-Za-z0-9_.-]+)\.json"',
        lambda match: f'href="evidence-raw.html?key={match.group(1)}"',
        text,
    )
    return (
        text.replace('="../', '="')
        .replace(">下载净化 JSON<", ">查看净化 JSON<")
        .replace(">Download sanitized JSON<", ">Inspect sanitized JSON<")
    )


def _javascript_source(source: str) -> str:
    doubled = chr(92) * 2
    while doubled in source:
        source = source.replace(doubled, chr(92))
    return source


def _loader(kind: str) -> str:
    parameter = "id" if kind == "task" else "key"
    return _javascript_source(f'''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>轨迹重放详情</title></head><body><main><p>正在读取详情…</p></main><script>
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
}})().catch(error=>document.querySelector('main').textContent='详情读取失败：'+error.message);
</script></body></html>''')


def _raw_evidence_loader() -> str:
    return _javascript_source(f'''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>单结果净化 JSON</title><style>body{{margin:0;background:#071722;color:#e6edf3;font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}main{{max-width:1180px;margin:auto;padding:24px}}a{{color:#86b7ff}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style></head><body><main><p><a href="javascript:history.back()">返回结果</a></p><h1>单结果净化 JSON</h1><pre>正在读取…</pre></main><script>
(async()=>{{
  const key=new URLSearchParams(location.search).get('key')||'';
  const match=key.match(/(?:^|-)(\\d+)$/);
  if(!match)throw Error('invalid evidence route');
  const shard=String(Number(match[1])%{SHARD_COUNT}).padStart(2,'0');
  const response=await fetch(`data/evidence-${{shard}}.json`);
  if(!response.ok)throw Error(`HTTP ${{response.status}}`);
  const value=(await response.json())[key];
  if(value===undefined)throw Error('evidence route not found');
  document.querySelector('pre').textContent=JSON.stringify(value,null,2);
}})().catch(error=>document.querySelector('pre').textContent='证据读取失败：'+error.message);
</script></body></html>''')


def _data_fetch_adapter() -> str:
    return _javascript_source(rf'''(()=>{{
const originalFetch=window.fetch.bind(window);const compactCache=new Map();
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
  if(!compactCache.has(cacheKey))compactCache.set(cacheKey,originalFetch('data/'+route.kind+'-'+route.shard+'.json').then(response=>{{if(!response.ok)throw Error(`HTTP ${{response.status}}`);return response.json()}}));
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
    excluded = {"task", "result"}
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.parts[0] in excluded or relative.parts[:2] in {
            ("data", "trajectory"),
            ("data", "evidence"),
        }:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".html":
            target.write_text(_rewrite_links(path.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(path, target)

    pages = destination / "pages"
    pages.mkdir()
    for kind in ("task", "result"):
        buckets = [{} for _ in range(SHARD_COUNT)]
        for path in sorted((source / kind).glob("*.html")):
            key = path.stem
            buckets[_task_id(key) % SHARD_COUNT][key] = _rewrite_links(
                path.read_text(encoding="utf-8"), detail_kind=kind
            )
        for index, rows in enumerate(buckets):
            (pages / f"{kind}-{index:02d}.json").write_text(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
        (destination / f"{kind}.html").write_text(_loader(kind), encoding="utf-8")
    (destination / "evidence-raw.html").write_text(
        _raw_evidence_loader(), encoding="utf-8"
    )

    (destination / "data").mkdir(exist_ok=True)
    for kind in ("trajectory", "evidence"):
        shards = [{} for _ in range(SHARD_COUNT)]
        for path in sorted((source / f"data/{kind}").glob("*.json")):
            key = path.stem
            shards[_task_id(key) % SHARD_COUNT][key] = json.loads(
                path.read_text(encoding="utf-8")
            )
        for index, rows in enumerate(shards):
            (destination / f"data/{kind}-{index:02d}.json").write_text(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
    app = destination / "assets/app.js"
    app.write_text(_data_fetch_adapter() + app.read_text(encoding="utf-8"), encoding="utf-8")
    files = [path for path in destination.rglob("*") if path.is_file()]
    return {
        "files": len(files),
        "task_routes": len(list((source / "task").glob("*.html"))),
        "result_routes": len(list((source / "result").glob("*.html"))),
    }


def validate_compact_report(report: Path, *, max_files: int) -> dict[str, int]:
    files = [path for path in report.rglob("*") if path.is_file()]
    if not files or len(files) > max_files:
        raise ValueError(f"compact report file count is outside 1..{max_files}: {len(files)}")
    required = [
        report / "index.html",
        report / "task.html",
        report / "result.html",
        report / "evidence-raw.html",
    ]
    required += [
        report / f"pages/{kind}-{index:02d}.json"
        for kind in ("task", "result")
        for index in range(SHARD_COUNT)
    ]
    required += [
        report / f"data/{kind}-{index:02d}.json"
        for kind in ("trajectory", "evidence")
        for index in range(SHARD_COUNT)
    ]
    if any(not path.is_file() for path in required):
        raise ValueError("compact report route bundles are incomplete")
    manifest = json.loads((report / "report-manifest.json").read_text(encoding="utf-8"))
    task_routes = sum(
        len(json.loads((report / f"pages/task-{index:02d}.json").read_text()))
        for index in range(SHARD_COUNT)
    )
    result_routes = sum(
        len(json.loads((report / f"pages/result-{index:02d}.json").read_text()))
        for index in range(SHARD_COUNT)
    )
    if task_routes != manifest["task_count"] or result_routes != manifest["logical_results"]:
        raise ValueError("compact report route coverage differs from its manifest")
    return {"files": len(files), "task_routes": task_routes, "result_routes": result_routes}
