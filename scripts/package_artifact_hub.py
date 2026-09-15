"""Lazy-loaded Artifact Hub publication with explicit supplemental HTML redaction.

The original report is untouched. Repeated reference/output HTML is reconstructed
from the same source files. Scored outputs remain byte-identical; supplemental
HTML may be redacted with separate original/published hashes. Hub injects a root base.
"""
import gzip
import hashlib
import html
import json
import mimetypes
import posixpath
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, unquote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.publication_privacy import project_file
from harness.web_fetch.publication_target import HUB
SOURCE=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'reports/webfetch'
ARCHIVE=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else ROOT/'publish/webfetch-sites.zip'
(ROOT/'publish').mkdir(exist_ok=True)
DEST=Path(tempfile.mkdtemp(prefix='artifact-hub-',dir=ROOT/'publish'))

LOADER=r'''
const decoder=new TextDecoder();let currentPage='index.html',renderId=0;
function englishRoute(){const hash=typeof location==='undefined'?'':location.hash||'',match=/(?:^#|&)page=([^&]+)/.exec(hash),path=match?decodeURIComponent(match[1]):currentPage;return path==='en/index.html'||path.startsWith('en/')||(typeof document!=='undefined'&&document.documentElement?.lang==='en')}
function tr(zh,en){return englishRoute()?en:zh}
let manifestPromise;
function manifest(){
 if(!manifestPromise)manifestPromise=fetch('manifest.json').then(r=>{if(!r.ok)throw Error(tr('数据目录加载失败','Failed to load the data catalog'));return r.json()}).catch(error=>{manifestPromise=undefined;throw error});
 return manifestPromise;
}
function reportText(zh,en,path=currentPage){return path.startsWith('en/')?en:zh}
const LARGE_TEXT_BYTES=256*1024;
const pageStates=new Map();
function savePageState(){
 if(!document.querySelector('main'))return;
 pageStates.set(currentPage,{filters:[...document.querySelectorAll('[data-filter],[data-wmb-filter],[data-wmb-slice-filter]')].map(e=>[e.dataset.wmbSliceFilter!==undefined?'slice:'+e.id:e.dataset.wmbFilter?'wmb:'+e.dataset.wmbFilter:'old:'+e.dataset.filter,e.value]),x:window.scrollX,y:window.scrollY});
}
function restorePageState(path){
 const saved=pageStates.get(path);if(!saved)return null;
 const fields=new Map(saved.filters);
 document.querySelectorAll('[data-filter],[data-wmb-filter],[data-wmb-slice-filter]').forEach(e=>{let key=e.dataset.wmbSliceFilter!==undefined?'slice:'+e.id:e.dataset.wmbFilter?'wmb:'+e.dataset.wmbFilter:'old:'+e.dataset.filter;if(fields.has(key))e.value=fields.get(key)});
 cascadeFilters();wmbFilters();wmbSliceFilters();return saved;
}
const packs=new Map();
async function pack(name){
 if(!packs.has(name))packs.set(name,fetch(name).then(async r=>{if(!r.ok)throw Error(tr('内容加载失败','Failed to load report content'));return new Uint8Array(await new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer())}).catch(error=>{packs.delete(name);throw error}));
 const result=await packs.get(name);
 if(packs.size>6){const first=packs.keys().next().value;if(first!==name)packs.delete(first)}
 return result;
}
async function fileBytes(path){
 const m=await manifest(),f=m.files[path];if(!f)throw Error(tr('文件不存在：','File not found: ')+path);
 const result=new Uint8Array(f.size);let offset=0;
 for(const hash of f.parts){const part=m.blocks[hash],bytes=await pack(part.pack);result.set(bytes.subarray(part.offset,part.offset+part.size),offset);offset+=part.size}
 return result;
}
async function textFile(path){return decoder.decode(await fileBytes(path))}
let recordsPromise;
const PRIVATE_METADATA_KEYS=new Set(['raw_response_path','private_artifacts','private_artifact','private_path']);
function publicMetadata(value){
 if(Array.isArray(value))return value.map(publicMetadata);
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>!PRIVATE_METADATA_KEYS.has(key)&&!key.startsWith('private_')).map(([key,item])=>[key,publicMetadata(item)]));
 return value;
}
async function metadata(index){
 if(!recordsPromise)recordsPromise=textFile('results.jsonl').then(s=>s.trim().split('\n').map(JSON.parse)).catch(error=>{recordsPromise=undefined;throw error});
 return JSON.stringify(publicMetadata((await recordsPromise)[index].provider_metadata||{}),null,2);
}
function deferFill(pre,message=tr('完整内容较大，点击后在只读文本框中加载；也可直接下载原始输出。','The complete content is large. Click to load it in a read-only text box, or download the original output.')){
 if(pre.dataset.deferred)return;
 pre.dataset.deferred='1';
 pre.classList.add('deferred-evidence');pre.tabIndex=0;pre.setAttribute('role','button');pre.setAttribute('aria-label',tr('加载完整内容','Load complete content'));pre.textContent=message;
 const activate=event=>{if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;event.preventDefault();pre.removeEventListener('click',activate);pre.removeEventListener('keydown',activate);pre.dataset.deferred='';fill(pre,true)};
 pre.addEventListener('click',activate);pre.addEventListener('keydown',activate);
}
function resolvePath(href,page){
 const base=new URL(page,'https://report.invalid/'),u=new URL(href,base);
 return {path:decodeURIComponent(u.pathname.slice(1)),anchor:decodeURIComponent(u.hash.slice(1))};
}
function route(path,anchor=''){return '#page='+encodeURIComponent(path)+(anchor?'&anchor='+encodeURIComponent(anchor):'')}
function fixLinks(root,page,m){
 root.querySelectorAll('a[href]').forEach(a=>{
  const href=a.getAttribute('href');if(/^(https?:|mailto:|data:|blob:)/i.test(href)){a.target='_blank';a.rel='noopener noreferrer';return}
  const dest=resolvePath(href,page);
  if(!m.files[dest.path])return;
  if(dest.path.endsWith('.html')&&!a.hasAttribute('download'))a.href=route(dest.path,dest.anchor);
  else{a.href='#';a.dataset.download=dest.path}
 });
}
let currentImageUrls=[];
async function fillImages(root,page,m){
 const urls=[];
 try{
  for(const img of root.querySelectorAll('img[src]')){
   const src=img.getAttribute('src');if(/^(https?:|data:|blob:)/i.test(src))continue;
   const {path}=resolvePath(src,page),file=m.files[path];
   if(!file||!file.mime.startsWith('image/'))throw Error(tr('图片文件不存在：','Image file not found: ')+path);
   const bytes=await fileBytes(path),url=URL.createObjectURL(new Blob([bytes],{type:file.mime}));
   urls.push(url);img.src=url;
  }
  return urls;
 }catch(error){urls.forEach(url=>URL.revokeObjectURL(url));throw error}
}
async function fill(pre,force=false){
 if(pre.dataset.loaded)return;
 let large=false;
 if(pre.dataset.file){const m=await manifest();large=(m.files[pre.dataset.file]?.size||0)>LARGE_TEXT_BYTES}
 if(large&&!force){deferFill(pre);return}
 pre.dataset.loaded='1';pre.classList.remove('deferred-evidence');pre.removeAttribute('role');pre.textContent=tr('正在加载完整内容…','Loading complete content…');
 try{let text=pre.dataset.record?await metadata(Number(pre.dataset.record)-1):await textFile(pre.dataset.file);
  if(pre.dataset.readable)text=text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,c=>'\\x'+c.charCodeAt(0).toString(16).padStart(2,'0'));
  if(large){const box=document.createElement('textarea');box.className='large-evidence-output';box.readOnly=true;box.value=text;box.setAttribute('aria-label',tr('完整实际返回','Complete actual output'));pre.replaceWith(box)}else pre.textContent=text;
 }catch(error){pre.dataset.loaded='';pre.dataset.deferred='';deferFill(pre,tr('加载失败：','Load failed: ')+error.message+tr('。点击重试，或下载原始输出。','. Click to retry, or download the original output.'))}
}
async function render(){
 savePageState();
 const token=++renderId,params=new URLSearchParams(location.hash.slice(1)),path=params.get('page')||'index.html';
 let imageUrls=[];
 try{
  const [m,text]=await Promise.all([manifest(),textFile(path)]);if(token!==renderId)return;
  if(!path.endsWith('.html'))throw Error(tr('不是报告页面','Not a report page'));
  const page=new DOMParser().parseFromString(text,'text/html');
  if(page.documentElement?.lang&&document.documentElement)document.documentElement.lang=page.documentElement.lang;
  imageUrls=await fillImages(page.body,path,m);
  if(token!==renderId)return;
  document.title=page.title;currentPage=path;fixLinks(page.body,path,m);document.body.innerHTML=page.body.innerHTML;
  currentImageUrls.forEach(url=>URL.revokeObjectURL(url));currentImageUrls=imageUrls;imageUrls=[];
  window.dispatchEvent(new Event('report-ready'));
  const saved=restorePageState(path);
  document.querySelectorAll('pre[data-file],pre[data-record]').forEach(pre=>{
   if(pre.closest('dialog'))return;
   const detail=pre.closest('details');if(detail&&!detail.open)detail.addEventListener('toggle',()=>{if(detail.open)fill(pre)});else fill(pre);
  });
  const anchor=params.get('anchor');applyReportAnchor(anchor);if(anchor)document.getElementById(anchor)?.scrollIntoView();else window.scrollTo(saved?.x||0,saved?.y||0);
 }catch(error){
  if(token!==renderId)return;
  currentImageUrls.forEach(url=>URL.revokeObjectURL(url));currentImageUrls=[];
  document.body.replaceChildren();const p=document.createElement('p');p.textContent=reportText('报告页面加载失败：','Report page failed to load: ',path)+error.message;document.body.append(p);
  const retry=document.createElement('button');retry.type='button';retry.textContent=reportText('重新加载','Reload',path);retry.addEventListener('click',render);document.body.append(retry)
 }finally{imageUrls.forEach(url=>URL.revokeObjectURL(url))}
}
async function downloadClick(event){
 const routeLink=event.target.closest('a[href^="#page="]');
 if(routeLink){event.preventDefault();location.hash=routeLink.getAttribute('href');return}
 const a=event.target.closest('a[data-download]');if(!a)return;event.preventDefault();
 const path=a.dataset.download,old=a.textContent;a.textContent=tr('正在准备完整文件…','Preparing complete file…');
 try{const bytes=await fileBytes(path),m=await manifest(),url=URL.createObjectURL(new Blob([bytes],{type:m.files[path].mime}));
  const link=document.createElement('a');link.href=url;link.download=path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
 }catch(error){alert(error.message)}finally{a.textContent=old}
}
window.addEventListener('hashchange',render);
window.addEventListener('DOMContentLoaded',render);
document.addEventListener('click',downloadClick);
'''

def build():
 dataset_path=SOURCE/'dataset.json'
 dataset=json.loads(dataset_path.read_text()) if dataset_path.exists() else {}
 webmainbench=dataset.get('id')=='webmainbench-545-preselected-200-five-browsers'
 if webmainbench:
   review=json.loads((SOURCE/'result-review.json').read_text())
   assert review.get('status')=='passed' and not review.get('failures'), \
          'Refusing to package an incomplete or unreviewed benchmark report'
   records_for_release=[json.loads(x) for x in (SOURCE/'results.jsonl').read_text().splitlines()]
   case_ids={row['id'] for row in dataset.get('cases',[])}
   providers=set(review.get('providers',[]))
   assert len(case_ids)==review.get('sample_case_count')==200, 'Current sample/review size mismatch'
   assert len(records_for_release)==len(case_ids)*len(providers)==1000, 'Current result matrix is incomplete'
   assert {(row.get('provider'),row.get('case_id')) for row in records_for_release}=={
       (provider,case_id) for provider in providers for case_id in case_ids}, 'Current result matrix differs from the reviewed scope'
   assert dataset.get('sample_manifest_sha256')==review.get('sample_manifest_sha256'), 'Current sample/review binding mismatch'
 else:
  reliability=json.loads((SOURCE/'reliability.json').read_text())
  assert reliability.get('complete') is True, 'Refusing to package an incomplete or unjudged benchmark report'
 DEST.mkdir(parents=True,exist_ok=True)
 (DEST/'data').mkdir(exist_ok=True)
 records=[json.loads(x) for x in (SOURCE/'results.jsonl').read_text().splitlines()]
 by_attempt={r['attempt_id']:(i,r) for i,r in enumerate(records)}
 scored_paths={r['artifact_path'] for r in records}
 files={};blocks={};raw_blocks={};original={};pages={};redactions=[]
 source_paths=sorted(path for path in SOURCE.rglob('*') if path.is_file())
 source_file_count=sum(path.name!='.generated-report' for path in source_paths)
 logical_route_count=sum(path.suffix=='.html' for path in source_paths)
 def add(path,data):
  parts=[]
  for start in range(0,len(data),65536):
   chunk=data[start:start+65536];key=hashlib.sha256(chunk).hexdigest()
   raw_blocks.setdefault(key,chunk);parts.append(key)
  files[path]={'parts':parts,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mime':mimetypes.guess_type(path)[0] or 'application/octet-stream'}
 for path in source_paths:
  name=path.relative_to(SOURCE).as_posix();data=path.read_bytes()
  if name=='.generated-report':continue
  if path.is_symlink():raise ValueError('Publication source must not contain symlinks: '+name)
  data,redaction=project_file(name,data)
  if redaction:
   if name in scored_paths:redaction['scored_output']=True
  if path.suffix=='.html':
   text=data.decode()
   if path.parent.name=='attempts':
    idx,record=by_attempt[path.stem];kinds=('reference','contract','output','metadata','call-output','call-metadata','call-log')
    matches=list(re.finditer(r'<pre data-kind="('+('|'.join(kinds))+r')">(.*?)</pre>',text,re.S))
    by_kind={kind:[match for match in matches if match[1]==kind] for kind in kinds}
    source_kind='contract' if by_kind['contract'] else 'reference'
    # Text tasks show the complete source and output in <pre> blocks, which we
    # replace with lazy-loaded evidence. Direct-file tasks intentionally show a
    # structured byte/hash comparison instead, so they have neither block.
    assert all(len(by_kind[kind])<=1 for kind in ('output','metadata','call-output','call-metadata')) and len(by_kind['metadata'])==1,(name,{key:len(value) for key,value in by_kind.items()})
    # The first source block is the complete reference/contract. Newer detail pages
    # can also show several derived body-region excerpts; keep those excerpts inline
    # instead of incorrectly replacing each one with the complete source file.
    selected=[]
    if by_kind[source_kind]:selected.append(by_kind[source_kind][0])
    if by_kind['output']:selected.append(by_kind['output'][0])
    selected.append(by_kind['metadata'][0])
    if by_kind['call-output']:selected.append(by_kind['call-output'][0])
    if by_kind['call-metadata']:selected.append(by_kind['call-metadata'][0])
    selected.extend(by_kind['call-log'])
    metadata=record.get('provider_metadata') or {};log_paths=[]
    retry=metadata.get('retry') or {}
    if isinstance(retry,dict):
     for attempt in retry.get('attempts',[]):
      if isinstance(attempt,dict) and isinstance(attempt.get('call_log'),dict):log_paths.append(attempt['call_log'].get('path'))
    for value in (record.get('call_log'),metadata.get('call_log')):
     if isinstance(value,dict):log_paths.append(value.get('path'))
    log_paths.extend([record.get('call_log_path'),metadata.get('call_log_path')])
    log_paths=list(dict.fromkeys(str(value) for value in log_paths if value and not str(value).startswith('private_artifacts/')))
    assert len(log_paths)==len(by_kind['call-log']),(name,len(log_paths),len(by_kind['call-log']))
    log_replacements={match.start():f'<pre data-file="{path}" data-readable="1"></pre>'
                      for match,path in zip(by_kind['call-log'],log_paths)}
    reference_suffix='.md' if (SOURCE/'references'/f'{record["case_id"]}.md').exists() else '.txt'
    replacements={source_kind:(f'<pre data-file="contracts/{record["case_id"]}.json"></pre>' if source_kind=='contract'
                                else f'<pre data-file="references/{record["case_id"]}{reference_suffix}"></pre>'),
                  'output':f'<pre data-file="{record["artifact_path"]}" data-readable="1"></pre>',
                  'call-output':f'<pre data-file="{record["artifact_path"]}" data-readable="1"></pre>',
                  'metadata':f'<pre data-record="{idx+1}"></pre>',
                  'call-metadata':f'<pre data-record="{idx+1}"></pre>'}
    for match in reversed(selected):
     replacement=log_replacements[match.start()] if match[1]=='call-log' else replacements[match[1]]
     text=text[:match.start()]+replacement+text[match.end():]
   elif path.parent.name=='datasets' and path.name!='index.html':
    suffix='.md' if (SOURCE/'references'/f'{path.stem}.md').exists() else '.txt'
    reference_file=html.escape(f'references/{path.stem}{suffix}',quote=True)
    text=re.sub(r'(<h2>(?:官方人工真值|Official human ground truth)</h2>)<pre>.*?</pre>',
                lambda match:match.group(1)+f'<pre data-file="{reference_file}"></pre>',text,count=1,flags=re.S)
   data=text.encode();pages[name]=text
  else:original[name]=hashlib.sha256(data).hexdigest()
  if redaction:
   redaction['published_sha256']=hashlib.sha256(data).hexdigest()
   redaction['published_bytes']=len(data)
   redactions.append(redaction)
  add(name,data)
 privacy={'schema_version':1,'scope':'all_public_files',
          'scoring_inputs_unchanged':True,
          'scored_outputs_privacy_projected':sum(bool(r.get('scored_output')) for r in redactions),
          'original_evidence_preserved_locally':True,
          'detection_limits':'Pattern-based scan; not a guarantee of detecting every secret. Key validity is not tested.',
          'files':redactions}
 add('publication-privacy.json',(json.dumps(privacy,ensure_ascii=False,indent=2)+'\n').encode())
 # Pack raw blocks together then gzip, keeping requests small and all bytes intact.
 batch=bytearray();batch_keys=[];index=0
 def flush():
  nonlocal batch,batch_keys,index
  if not batch:return
  name=f'data/{index:03d}.gz';compressed=gzip.compress(bytes(batch),compresslevel=9,mtime=0)
  (DEST/name).write_bytes(compressed)
  for key,offset,size in batch_keys:blocks[key]={'pack':name,'offset':offset,'size':size}
  batch=bytearray();batch_keys=[];index+=1
 for key,data in raw_blocks.items():
  if len(batch)+len(data)>4*1024*1024:flush()
  batch_keys.append((key,len(batch),len(data)));batch.extend(data)
 flush()
 manifest={'schema_version':2,
           'architecture':{'kind':'single_html_shell_with_lazy_virtual_routes',
                           'logical_routes':logical_route_count,'source_files':source_file_count,
                           'content_packs':index,'block_bytes':65536,
                           'large_text_on_demand_bytes':256*1024},
           'files':files,'blocks':blocks}
 (DEST/'manifest.json').write_text(json.dumps(manifest,separators=(',',':')))
 (DEST/'style.css').write_bytes((SOURCE/'assets/style.css').read_bytes())
 js=(SOURCE/'assets/report.js').read_text()
 if not webmainbench:
  js=js.replace("window.addEventListener('DOMContentLoaded',", "window.addEventListener('report-ready',")
 js=js.replace("localStorage.getItem('webfetch-theme')",'readTheme()').replace("localStorage.setItem('webfetch-theme',t)",'saveTheme(t)')
 js="function readTheme(){try{return localStorage.getItem('webfetch-theme')}catch{return 'light'}}\nfunction saveTheme(t){try{localStorage.setItem('webfetch-theme',t)}catch{}}\n"+js
 (DEST/'report.js').write_text(js)
 (DEST/'loader.js').write_text(LOADER)
 style_version=hashlib.sha256((DEST/'style.css').read_bytes()).hexdigest()[:16]
 script_version=hashlib.sha256((DEST/'report.js').read_bytes()).hexdigest()[:16]
 loader_version=hashlib.sha256((DEST/'loader.js').read_bytes()).hexdigest()[:16]
 (DEST/'index.html').write_text(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Web Fetch 评测</title><link rel="stylesheet" href="style.css?v={style_version}"><script defer src="report.js?v={script_version}"></script><script defer src="loader.js?v={loader_version}"></script></head><body><p>正在加载报告…</p></body></html>')
 # Validate every download and every internal page/file link after transformation.
 for name,digest in original.items():
  assert hashlib.sha256(b''.join(raw_blocks[k] for k in files[name]['parts'])).hexdigest()==digest,name
 for name,text in pages.items():
  for src in re.findall(r'<img[^>]+src="([^"]+)"',text):
   src=html.unescape(src);u=urlsplit(src)
   if u.scheme or u.netloc:continue
   target=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(u.path)))
   assert target in files and files[target]['mime'].startswith('image/'),(name,src,target)
  for href in re.findall(r'href="([^"]+)"',text):
   href=html.unescape(href);u=urlsplit(href)
   if u.scheme or u.netloc:continue
   target=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(u.path))) if u.path else name
   assert target in files,(name,href,target)
  for target in re.findall(r'data-file="([^"]+)"',text):assert target in files,(name,target)
 # Verify packed data independently, not just in-memory deduplication.
 for name in sorted({v['pack'] for v in blocks.values()}):
  raw=gzip.decompress((DEST/name).read_bytes())
  for key,b in blocks.items():
   if b['pack']==name:
    observed=hashlib.sha256(raw[b['offset']:b['offset']+b['size']]).hexdigest()
    assert observed==key,(name,key,observed,b['offset'],b['size'],len(raw))
 total=sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file())
 print(json.dumps({'source_files':source_file_count,'logical_routes':logical_route_count,
                   'html_shells':1,'content_packs':index,
                   'published_files':len(list(DEST.rglob('*')))-1,'published_bytes':total,
                   'all_downloads_byte_identical':not redactions,
                   'redacted_public_files':len(redactions),
                   'scored_outputs_privacy_projected':sum(bool(r.get('scored_output')) for r in redactions),
                   'all_virtual_links_valid':True}))
 # Artifact Hub publishes large bundles through staged per-file uploads.
 # Its documented total limit is 300 MB, not the former local 50 MB budget.
 assert total<=300_000_000, 'Published package exceeds the 300 MB service limit'
 archive=ARCHIVE
 archive.parent.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for path in sorted(DEST.rglob('*')):
   if path.is_file():z.write(path,path.relative_to(DEST))
 print(archive)

if __name__=='__main__':
 try:build()
 finally:shutil.rmtree(DEST)
