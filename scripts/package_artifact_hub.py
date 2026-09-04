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
(ROOT/'publish').mkdir(exist_ok=True)
DEST=Path(tempfile.mkdtemp(prefix='artifact-hub-',dir=ROOT/'publish'))

LOADER=r'''
const manifestPromise=fetch('manifest.json').then(r=>{if(!r.ok)throw Error('数据目录加载失败');return r.json()});
const decoder=new TextDecoder();let currentPage='index.html',renderId=0;
const pageStates=new Map();
function savePageState(){
 if(!document.querySelector('main'))return;
 pageStates.set(currentPage,{filters:[...document.querySelectorAll('[data-filter]')].map(e=>[e.dataset.filter,e.value]),x:window.scrollX,y:window.scrollY});
}
function restorePageState(path){
 const saved=pageStates.get(path);if(!saved)return null;
 const fields=new Map(saved.filters);
 document.querySelectorAll('[data-filter]').forEach(e=>{if(fields.has(e.dataset.filter))e.value=fields.get(e.dataset.filter)});
 cascadeFilters();return saved;
}
const packs=new Map();
async function pack(name){
 if(!packs.has(name))packs.set(name,fetch(name).then(async r=>{if(!r.ok)throw Error('内容加载失败');return new Uint8Array(await new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer())}));
 const result=await packs.get(name);
 if(packs.size>6){const first=packs.keys().next().value;if(first!==name)packs.delete(first)}
 return result;
}
async function fileBytes(path){
 const m=await manifestPromise,f=m.files[path];if(!f)throw Error('文件不存在：'+path);
 const result=new Uint8Array(f.size);let offset=0;
 for(const hash of f.parts){const part=m.blocks[hash],bytes=await pack(part.pack);result.set(bytes.subarray(part.offset,part.offset+part.size),offset);offset+=part.size}
 return result;
}
async function textFile(path){return decoder.decode(await fileBytes(path))}
let recordsPromise;
async function metadata(index){
 if(!recordsPromise)recordsPromise=textFile('results.jsonl').then(s=>s.trim().split('\n').map(JSON.parse));
 return JSON.stringify((await recordsPromise)[index].provider_metadata||{},null,2);
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
   if(!file||!file.mime.startsWith('image/'))throw Error('图片文件不存在：'+path);
   const bytes=await fileBytes(path),url=URL.createObjectURL(new Blob([bytes],{type:file.mime}));
   urls.push(url);img.src=url;
  }
  return urls;
 }catch(error){urls.forEach(url=>URL.revokeObjectURL(url));throw error}
}
async function fill(pre){
 if(pre.dataset.loaded)return;pre.dataset.loaded='1';pre.textContent='正在加载完整内容…';
 try{let text=pre.dataset.record?await metadata(Number(pre.dataset.record)-1):await textFile(pre.dataset.file);
  if(pre.dataset.readable)text=text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,c=>'\\x'+c.charCodeAt(0).toString(16).padStart(2,'0'));
  pre.textContent=text;
 }catch(error){pre.dataset.loaded='';pre.textContent=error.message}
}
async function render(){
 savePageState();
 const token=++renderId,params=new URLSearchParams(location.hash.slice(1)),path=params.get('page')||'index.html';
 let imageUrls=[];
 try{
  const [m,text]=await Promise.all([manifestPromise,textFile(path)]);if(token!==renderId)return;
  if(!path.endsWith('.html'))throw Error('不是报告页面');
  const page=new DOMParser().parseFromString(text,'text/html');
  imageUrls=await fillImages(page.body,path,m);
  if(token!==renderId)return;
  document.title=page.title;currentPage=path;fixLinks(page.body,path,m);document.body.innerHTML=page.body.innerHTML;
  currentImageUrls.forEach(url=>URL.revokeObjectURL(url));currentImageUrls=imageUrls;imageUrls=[];
  window.dispatchEvent(new Event('report-ready'));
  const saved=restorePageState(path);
  document.querySelectorAll('pre[data-file],pre[data-record]').forEach(pre=>{
   const detail=pre.closest('details');if(detail&&!detail.open)detail.addEventListener('toggle',()=>{if(detail.open)fill(pre)});else fill(pre);
  });
  const anchor=params.get('anchor');applyReportAnchor(anchor);if(anchor)document.getElementById(anchor)?.scrollIntoView();else window.scrollTo(saved?.x||0,saved?.y||0);
 }catch(error){
  if(token!==renderId)return;
  currentImageUrls.forEach(url=>URL.revokeObjectURL(url));currentImageUrls=[];
  document.body.replaceChildren();const p=document.createElement('p');p.textContent=error.message;document.body.append(p)
 }finally{imageUrls.forEach(url=>URL.revokeObjectURL(url))}
}
async function downloadClick(event){
 const routeLink=event.target.closest('a[href^="#page="]');
 if(routeLink){event.preventDefault();location.hash=routeLink.getAttribute('href');return}
 const a=event.target.closest('a[data-download]');if(!a)return;event.preventDefault();
 const path=a.dataset.download,old=a.textContent;a.textContent='正在准备完整文件…';
 try{const bytes=await fileBytes(path),m=await manifestPromise,url=URL.createObjectURL(new Blob([bytes],{type:m.files[path].mime}));
  const link=document.createElement('a');link.href=url;link.download=path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
 }catch(error){alert(error.message)}finally{a.textContent=old}
}
window.addEventListener('hashchange',render);
window.addEventListener('DOMContentLoaded',render);
document.addEventListener('click',downloadClick);
'''

def build():
 reliability=json.loads((SOURCE/'reliability.json').read_text())
 assert reliability.get('complete') is True, 'Refusing to package an incomplete or unjudged benchmark report'
 DEST.mkdir(parents=True,exist_ok=True)
 (DEST/'data').mkdir(exist_ok=True)
 records=[json.loads(x) for x in (SOURCE/'results.jsonl').read_text().splitlines()]
 by_attempt={r['attempt_id']:(i,r) for i,r in enumerate(records)}
 scored_paths={r['artifact_path'] for r in records}
 files={};blocks={};raw_blocks={};original={};pages={};redactions=[]
 def add(path,data):
  parts=[]
  for start in range(0,len(data),65536):
   chunk=data[start:start+65536];key=hashlib.sha256(chunk).hexdigest()
   raw_blocks.setdefault(key,chunk);parts.append(key)
  files[path]={'parts':parts,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mime':mimetypes.guess_type(path)[0] or 'application/octet-stream'}
 for path in sorted(SOURCE.rglob('*')):
  if not path.is_file():continue
  name=path.relative_to(SOURCE).as_posix();data=path.read_bytes()
  if name=='.generated-report':continue
  if path.is_symlink():raise ValueError('Publication source must not contain symlinks: '+name)
  data,redaction=project_file(name,data)
  if redaction:
   if name in scored_paths:raise ValueError('Scored output requires credential review: '+name)
   redactions.append(redaction)
  if path.suffix=='.html':
   text=data.decode()
   if path.parent.name=='attempts':
    idx,record=by_attempt[path.stem];matches=list(re.finditer(r'<pre data-kind="(reference|contract|output|metadata)">(.*?)</pre>',text,re.S))
    assert len(matches)==3,(name,len(matches))
    replacements={'reference':f'<pre data-file="references/{record["case_id"]}.txt"></pre>',
                  'contract':f'<pre data-file="contracts/{record["case_id"]}.json"></pre>',
                  'output':f'<pre data-file="{record["artifact_path"]}" data-readable="1"></pre>',
                  'metadata':f'<pre data-record="{idx+1}"></pre>'}
    for match in reversed(matches):text=text[:match.start()]+replacements[match[1]]+text[match.end():]
   elif path.parent.name=='datasets' and path.name!='index.html':
    text=re.sub(r'<pre>.*?</pre>',f'<pre data-file="references/{path.stem}.txt"></pre>',text,count=1,flags=re.S)
   privacy_href=(HUB+'#page=guide/index.html' if name=='guide/article.html' else '../' * (len(path.relative_to(SOURCE).parts)-1)+'publication-privacy.json')
   notice='<aside class="source-note">接口账号使用占位符；第三方原始 HTML 中匹配到的密钥值已脱敏。本地原件保留，评分与测量不变。原始哈希与发布副本哈希可能不同，详见 <a href="'+privacy_href+'">在线报告的发布脱敏清单</a>。脱敏文件不适用于逐字节复算原始证据哈希。</aside>'
   text=re.sub(r'(<body\b[^>]*>)',lambda match:match[0]+notice,text,count=1)
   data=text.encode();pages[name]=text
  else:original[name]=hashlib.sha256(data).hexdigest()
  add(name,data)
 privacy={'schema_version':1,'scope':'supplemental_raw_html_only',
          'scored_outputs_unchanged':True,'original_evidence_preserved_locally':True,
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
 manifest={'files':files,'blocks':blocks}
 (DEST/'manifest.json').write_text(json.dumps(manifest,separators=(',',':')))
 (DEST/'style.css').write_bytes((SOURCE/'assets/style.css').read_bytes())
 js=(SOURCE/'assets/report.js').read_text().replace("window.addEventListener('DOMContentLoaded',", "window.addEventListener('report-ready',")
 js=js.replace("localStorage.getItem('webfetch-theme')",'readTheme()').replace("localStorage.setItem('webfetch-theme',t)",'saveTheme(t)')
 js="function readTheme(){try{return localStorage.getItem('webfetch-theme')}catch{return 'light'}}\nfunction saveTheme(t){try{localStorage.setItem('webfetch-theme',t)}catch{}}\n"+js
 (DEST/'report.js').write_text(js)
 (DEST/'loader.js').write_text(LOADER)
 (DEST/'index.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Web Fetch 评测</title><link rel="stylesheet" href="style.css"><script defer src="report.js"></script><script defer src="loader.js"></script></head><body><p>正在加载报告…</p></body></html>')
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
   if b['pack']==name:assert hashlib.sha256(raw[b['offset']:b['offset']+b['size']]).hexdigest()==key
 total=sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file())
 print(json.dumps({'original_files':len(files),'published_files':len(list(DEST.rglob('*')))-1,'published_bytes':total,'all_downloads_byte_identical':not redactions,'redacted_supplemental_files':len(redactions),'scored_outputs_unchanged':True,'all_virtual_links_valid':True}))
 # Artifact Hub publishes large bundles through staged per-file uploads.
 # Its documented total limit is 300 MB, not the former local 50 MB budget.
 assert total<=300_000_000, 'Published package exceeds the 300 MB service limit'
 archive=DEST.parent/'webfetch-sites.zip'
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for path in sorted(DEST.rglob('*')):
   if path.is_file():z.write(path,path.relative_to(DEST))
 print(archive)

if __name__=='__main__':
 try:build()
 finally:shutil.rmtree(DEST)
