"""Lossless case-sharded delivery for hosts with a bounded file count.

The report renderer and checks remain authoritative. This exporter transports
their generated pages and raw evidence without generating alternative findings.
"""

import base64
import gzip
import json
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

from harness.web_search.core import digest, write_json

BRIDGE = r"""
const nativeFetch=window.fetch.bind(window);
let deliveryIndex;
async function index(){return deliveryIndex||(deliveryIndex=await (await nativeFetch('delivery-index.json')).json())}
const shards=new Map();
async function stored(path){
 const idx=await index(),name=idx.files[path];
 if(!name)throw Error('Evidence not registered: '+path);
 if(!shards.has(name))shards.set(name,nativeFetch(name).then(r=>{if(!r.ok)throw Error('HTTP '+r.status);if(name.endsWith('.gz'))return new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).json();return r.json()}));
 const data=await shards.get(name);if(!(path in data))throw Error('Missing evidence: '+path);return data[path];
}
function restoreBytes(value){
 if(typeof value==='string')return new TextEncoder().encode(value);
 return Uint8Array.from(atob(value.base64),c=>c.charCodeAt(0));
}
window.fetch=async function(input,options){
 const path=typeof input==='string'?input:'';
 if(path.startsWith('calls/'))return new Response(restoreBytes(await stored(path)),{headers:{'Content-Type':'application/json'}});
 return nativeFetch(input,options);
};
function routeLink(value){
 if(!value||/^(https?:|#)/.test(value))return value;
 if(/(^|\/)details\//.test(value))return 'detail.html#'+encodeURIComponent(value);
 if(value.startsWith('calls/'))return 'evidence.html#'+encodeURIComponent(value);
 return value;
}
function rewriteLinks(root){
 root.querySelectorAll('[href],[data-detail]').forEach(e=>{
  for(const a of ['href','data-detail'])if(e.hasAttribute(a))e.setAttribute(a,routeLink(e.getAttribute(a)));
 });
}
rewriteLinks(document);
"""

LOADER = r"""
async function loadSavedPage(){
 let target=document.querySelector('[data-delivery-target]');
 if(!target){target=document.createElement('pre');target.dataset.deliveryTarget='';document.querySelector('main').appendChild(target)}
 const path=decodeURIComponent(location.hash.slice(1));
 try{
  if(!path)throw Error('No result selected');
  const bytes=restoreBytes(await stored(path));
  const text=new TextDecoder('utf-8').decode(bytes);
  if(path.endsWith('.html')){
   const doc=new DOMParser().parseFromString(text,'text/html');
   document.documentElement.lang=doc.documentElement.lang;
   document.title=doc.title;
   document.querySelector('nav').replaceWith(doc.querySelector('nav'));
   document.querySelector('main').replaceWith(doc.querySelector('main'));
   document.querySelector('dialog').replaceWith(doc.querySelector('dialog'));
   rewriteLinks(document);
   const script=document.createElement('script');
   script.textContent='{'+doc.querySelector('script').textContent+'}';
   document.body.appendChild(script);
  }else{
   let formatted=text;try{formatted=JSON.stringify(JSON.parse(text),null,2)}catch{}
   target.textContent=formatted;
   let download=document.querySelector('[data-evidence-download]');
   if(!download){download=document.createElement('a');download.dataset.evidenceDownload='';target.before(download)}
   if(download.href.startsWith('blob:'))URL.revokeObjectURL(download.href);
   download.href=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'}));
   download.download=path.split('/').pop();download.textContent=document.documentElement.lang.startsWith('zh')?'下载原始响应':'Download original response';
  }
 }catch(e){target.textContent='Unable to load saved evidence: '+e.message}
}
window.addEventListener('hashchange',loadSavedPage);loadSavedPage();
"""


def _delivery_link(value):
    if not value or value.startswith(("http://", "https://", "#")):
        return value
    if "/details/" in value or value.startswith("details/"):
        return "detail.html#" + quote(value, safe="")
    if value.startswith("calls/"):
        return "evidence.html#" + quote(value, safe="")
    return value


def transform_page(text):
    doc = BeautifulSoup(text, "html.parser")
    bridge = doc.new_tag("script")
    bridge.string = BRIDGE
    doc.head.append(bridge)
    # Static links are rewritten at export, including diagnosis links assigned later.
    for node in doc.select("[href],[data-detail]"):
        for attr in ("href", "data-detail"):
            value = node.get(attr, "")
            if value:
                node[attr] = _delivery_link(value)
    # Paginated comparison cards are created after load, so their canonical
    # links live in structured data rather than DOM attributes.
    for node in doc.select("script[data-comparison-data]"):
        data = json.loads(node.string)
        for case in data["cases"]:
            for result in case["outputs"].values():
                if result is not None:
                    result["detail"] = _delivery_link(result["detail"])
        node.string = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    return str(doc)


def export(source, destination):
    """Keep one shard per case, including both languages and all physical calls."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((source / "manifest.json").read_text())
    shards, mapping, hashes = {}, {}, {}
    for row in manifest["rows"]:
        shard = shards.setdefault(row["case_id"], {})
        paths = [row["evidence"]["path"], *[a["evidence"]["path"] for a in row.get("attempts", [])]]
        paths += [route for route in manifest["routes"] if route.endswith("details/" + row["id"] + ".html")]
        for path in paths:
            raw = (source / path).read_bytes()
            # JSON transport must not normalize CRLF or decode arbitrary provider bytes.
            shard[path] = {"base64": base64.b64encode(raw).decode("ascii")}
            mapping[path] = "case-data/" + row["case_id"] + ".json.gz"
            hashes[path] = digest((source / path).read_bytes())
    for cid, data in shards.items():
        target = destination / "case-data" / (cid + ".json.gz")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode(), mtime=0))
    public_files = set(manifest["routes"]) | set(manifest.get("language_notices", [])) | {a["path"] for a in manifest["bundle"]} | {"manifest.json"}
    for path in sorted(public_files - set(mapping)):
        raw = (source / path).read_bytes()
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(transform_page(raw.decode()).encode() if path.endswith(".html") else raw)
    write_json(destination / "delivery-index.json", {
        "schema": "search-report-delivery/2",
        "source_manifest_sha256": digest((source / "manifest.json").read_bytes()),
        "files": mapping, "sha256": hashes,
    })
    shell = BeautifulSoup((destination / "index.html").read_text(), "html.parser")
    shell.main.clear()
    pre = shell.new_tag("pre")
    pre["data-delivery-target"] = ""
    pre.string = "Loading saved evidence… / 正在加载证据…"
    shell.main.append(pre)
    # Drop the report's listeners until the requested page has been installed.
    for script in shell.select("body script, [data-report-language-entry]"):
        script.decompose()
    loader = shell.new_tag("script")
    loader.string = LOADER
    shell.body.append(loader)
    for route in ("detail.html", "evidence.html"):
        (destination / route).write_text(str(shell))
    verify(source, destination)
    return destination


def verify(source, destination):
    """Every virtual file restores exactly; every ordinary page has one transform."""
    source, destination = Path(source), Path(destination)
    index = json.loads((destination / "delivery-index.json").read_text())
    if digest((source / "manifest.json").read_bytes()) != index["source_manifest_sha256"]:
        raise ValueError("Delivery source manifest changed")
    cache = {}
    for path, shard in index["files"].items():
        if shard not in cache:
            cache[shard] = json.loads(gzip.decompress((destination / shard).read_bytes()))
        value = cache[shard][path]
        raw = base64.b64decode(value["base64"], validate=True)
        if digest(raw) != index["sha256"][path] or raw != (source / path).read_bytes():
            raise ValueError("Packed evidence changed")
    manifest = json.loads((source / "manifest.json").read_text())
    for path in set(manifest["routes"]) | set(manifest.get("language_notices", [])) | {a["path"] for a in manifest["bundle"]} | {"manifest.json"}:
        if path in index["files"]:
            continue
        raw = (source / path).read_bytes()
        expected = transform_page(raw.decode()).encode() if path.endswith(".html") else raw
        if (destination / path).read_bytes() != expected:
            raise ValueError("Delivery page changed")
    return True
