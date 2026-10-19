
const decoder=new TextDecoder();let currentPage='index.html',renderId=0;
function englishRoute(){const hash=typeof location==='undefined'?'':location.hash||'',match=/(?:^#|&)page=([^&]+)/.exec(hash),path=match?decodeURIComponent(match[1]):currentPage;return path==='en/index.html'||path.startsWith('en/')||(typeof document!=='undefined'&&document.documentElement?.lang==='en')}
function tr(zh,en){return englishRoute()?en:zh}
async function compactCatalog(descriptor){
 if(descriptor.schema_version!==3)return descriptor;
 const compressed=await fetch('manifest.json.gz');if(!compressed.ok)throw Error(tr('数据目录加载失败','Failed to load the data catalog'));
 const m=await new Response(compressed.body.pipeThrough(new DecompressionStream('gzip'))).json();
 const response=await fetch('catalog.bin.gz');if(!response.ok)throw Error(tr('内容索引加载失败','Failed to load the content index'));
 const buffer=await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
 m.binary=new DataView(buffer);m.sizes=new Uint16Array(m.block_count);m.offsets=new Uint32Array(m.block_count);m.packIds=new Uint16Array(m.block_count);
 let packId=0,offset=0;
 for(let i=0;i<m.block_count;i++){
  if(packId+1<m.pack_starts.length&&i===m.pack_starts[packId+1]){packId++;offset=0}
  m.sizes[i]=m.binary.getUint16(i*2,true);m.offsets[i]=offset;m.packIds[i]=packId;offset+=m.sizes[i];
 }
 return m;
}
let manifestPromise;
function manifest(){
 if(!manifestPromise)manifestPromise=fetch('manifest.json').then(r=>{if(!r.ok)throw Error(tr('数据目录加载失败','Failed to load the data catalog'));return r.json()}).then(compactCatalog).catch(error=>{manifestPromise=undefined;throw error});
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
 if(!packs.has(name))packs.set(name,manifest().then(m=>fetch(m.external_packs?.[name]?.url||name)).then(async r=>{if(!r.ok)throw Error(tr('内容加载失败','Failed to load report content'));return new Uint8Array(await new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer())}).catch(error=>{packs.delete(name);throw error}));
 const result=await packs.get(name);
 if(packs.size>6){const first=packs.keys().next().value;if(first!==name)packs.delete(first)}
 return result;
}
async function fileBytes(path){
 const m=await manifest(),f=m.files[path];if(!f)throw Error(tr('文件不存在：','File not found: ')+path);
 if(f.inline_base64!==undefined){const bytes=Uint8Array.from(atob(f.inline_base64),c=>c.charCodeAt(0));if(bytes.length!==f.size)throw Error(tr('文件长度错误：','Invalid file length: ')+path);return bytes}
 const result=new Uint8Array(f.size);let offset=0;
 if(m.schema_version===3){
  let id=0;
  for(let i=0;i<f.parts[1];i++){
   const delta=m.binary.getUint32(m.block_count*2+(f.parts[0]+i)*4,true);id+=(delta>>>1)^-(delta&1);
   const bytes=await pack(m.packs[m.packIds[id]]),start=m.offsets[id],size=m.sizes[id];
   result.set(bytes.subarray(start,start+size),offset);offset+=size;
  }
 }else{
  for(const hash of f.parts){const part=m.blocks[hash],bytes=await pack(part.pack);result.set(bytes.subarray(part.offset,part.offset+part.size),offset);offset+=part.size}
 }
 if(offset!==f.size)throw Error(tr('文件长度错误：','Invalid file length: ')+path);
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
