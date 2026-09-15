// Pure synthetic DOM test: no browser or network.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../scripts/package_artifact_hub.py',import.meta.url),'utf8');
const js=source.match(/LOADER=r'''([\s\S]*?)'''/)[1];
let fields=[{dataset:{filter:'provider'},value:'lexmount-global'},{dataset:{filter:'query'},value:'正文核对点-1'},
 {dataset:{wmbFilter:'method'},value:'moli'},{dataset:{wmbFilter:'metric'},value:'table_TEDS'}],calls=0,wmbCalls=0;
const context=vm.createContext({TextDecoder,URL,fetch:async()=>({ok:true,json:async()=>({})}),
 window:{scrollX:0,scrollY:650,addEventListener(){}},
 document:{querySelector:()=>({}),querySelectorAll:()=>fields,addEventListener(){}},
 cascadeFilters(){calls++},wmbFilters(){wmbCalls++}
});
vm.runInContext(js,context);
vm.runInContext("currentPage='analysis/index.html';savePageState()",context);
fields.forEach(f=>f.value='');
const saved=vm.runInContext("restorePageState('analysis/index.html')",context);
assert.deepEqual(fields.map(f=>f.value),['lexmount-global','正文核对点-1','moli','table_TEDS']);
assert.equal(saved.y,650);assert.equal(calls,1);assert.equal(wmbCalls,1);
assert.equal(vm.runInContext("restorePageState('missing.html')",context),null);
console.log('PASS: packaged report restores per-page filters, query and scroll position');
const anchors=[false,true].map(download=>({dataset:{},getAttribute:()=> 'article.html',hasAttribute:n=>n==='download'&&download}));
context.linkRoot={querySelectorAll:()=>anchors};
vm.runInContext("fixLinks(linkRoot,'guide/index.html',{files:{'guide/article.html':{}}})",context);
assert.equal(anchors[0].href,'#page=guide%2Farticle.html');
assert.equal(anchors[1].dataset.download,'guide/article.html');
console.log('PASS: HTML export downloads while ordinary HTML links navigate');

const media=[{getAttribute:()=> '../assets/methodology/scoring.png'},
 {getAttribute:()=> 'data:image/png;base64,AAAA'}];
context.imageRoot={querySelectorAll:()=>media};
context.Blob=Blob;
vm.runInContext("fileBytes=async path=>new Uint8Array([1,2,3])",context);
const urls=await vm.runInContext("fillImages(imageRoot,'guide/index.html',{files:{'assets/methodology/scoring.png':{mime:'image/png'}}})",context);
assert.equal(urls.length,1);assert.match(media[0].src,/^blob:/);assert.equal(media[1].src,undefined);
urls.forEach(url=>URL.revokeObjectURL(url));
await assert.rejects(vm.runInContext("fillImages(imageRoot,'guide/index.html',{files:{}})",context),/图片文件不存在/);
console.log('PASS: packaged images resolve relative virtual paths to blob assets; embedded images stay untouched');

const largeListeners=new Map();let replacement=null,largeReads=0;
const largePre={dataset:{file:'outputs/large.md',readable:'1'},textContent:'',classList:{add(){},remove(){}},
 setAttribute(name,value){this[name]=value},removeAttribute(name){delete this[name]},
 addEventListener(name,handler){largeListeners.set(name,handler)},removeEventListener(name){largeListeners.delete(name)},
 replaceWith(node){replacement=node}};
const largeContext=vm.createContext({TextDecoder,URL,fetch:async()=>({ok:true,json:async()=>({files:{'outputs/large.md':{size:300000}}})}),
 window:{addEventListener(){}},document:{addEventListener(){},createElement:()=>({setAttribute(name,value){this[name]=value}})}});
vm.runInContext(js,largeContext);
largeContext.readLarge=async()=>{largeReads++;return '完整大文本'};
vm.runInContext('textFile=readLarge',largeContext);
largeContext.largePre=largePre;
await vm.runInContext('fill(largePre)',largeContext);
assert.equal(largeReads,0,'large evidence must not download during page render');
assert.equal(largePre.role,'button');assert.match(largePre.textContent,/点击/);
const deferredClick=largeListeners.get('click');
await vm.runInContext('fill(largePre)',largeContext);
assert.equal(largeListeners.get('click'),deferredClick,'re-entry must not attach another activation listener');
await largeListeners.get('click')({type:'click',preventDefault(){}});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(largeReads,1);assert.equal(replacement.tagName,undefined);
assert.equal(replacement.className,'large-evidence-output');assert.equal(replacement.readOnly,true);
assert.equal(replacement.value,'完整大文本');
console.log('PASS: large evidence defers network and DOM insertion until requested, then uses a read-only text control');

// Keep the real render/fillImages path: the old page creates one Blob, then
// waits for a second image while a newer page finishes rendering.
function deferred(){
 let resolve,reject;
 const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
 return {promise,resolve,reject};
}
const waitingForOldImage=deferred(),oldImage=deferred(),created=[],revoked=[];
class TrackedURL extends URL{
 static createObjectURL(){const url=`blob:test-${created.length+1}`;created.push(url);return url}
 static revokeObjectURL(url){revoked.push(url)}
}
const raceLocation={hash:'#page=guide%2Fold.html'};
const raceBody={innerHTML:'initial page',
 replaceChildren(){this.innerHTML=''},append(element){this.innerHTML+=element.textContent}};
const raceDocument={body:raceBody,title:'initial',addEventListener(){},
 querySelector:()=>({}),querySelectorAll:()=>[],createElement:()=>({})};
const imageFiles=Object.fromEntries(['old-first','old-pending','new'].map(name=>[
 `assets/${name}.png`,{mime:'image/png'}]));
const raceContext=vm.createContext({TextDecoder,URL:TrackedURL,URLSearchParams,Blob,
 location:raceLocation,document:raceDocument,
 fetch:async()=>({ok:true,json:async()=>({files:imageFiles})}),
 DOMParser:class{
  parseFromString(path){
   const images=(path==='guide/old.html'?['old-first','old-pending']:['new'])
    .map(name=>({getAttribute:()=>`../assets/${name}.png`}));
   return {title:path,body:{innerHTML:path,
    querySelectorAll:selector=>selector==='img[src]'?images:[]}};
  }
 },
 Event:class{},window:{scrollX:0,scrollY:0,addEventListener(){},dispatchEvent(){},scrollTo(){}},
 cascadeFilters(){},applyReportAnchor(){},
 async readImage(path){
  if(path==='assets/old-pending.png'){
   waitingForOldImage.resolve();
   return await oldImage.promise;
  }
  return new Uint8Array([1,2,3]);
 }
});
vm.runInContext(js,raceContext);
vm.runInContext("textFile=async path=>path;fileBytes=readImage;currentImageUrls=['blob:initial']",raceContext);
const oldRender=vm.runInContext('render()',raceContext);
await waitingForOldImage.promise;
assert.deepEqual(created,['blob:test-1']);
raceLocation.hash='#page=guide%2Fnew.html';
await vm.runInContext('render()',raceContext);
assert.equal(raceBody.innerHTML,'guide/new.html');
assert.deepEqual(revoked,['blob:initial']);
oldImage.reject(new Error('Old page image failed after navigation'));
await oldRender;
assert.equal(raceBody.innerHTML,'guide/new.html','stale failure must not clear the newer page');
assert.equal(raceDocument.title,'guide/new.html');
assert.equal(vm.runInContext('currentPage',raceContext),'guide/new.html');
assert.deepEqual(Array.from(vm.runInContext('currentImageUrls',raceContext)),['blob:test-2']);
assert.deepEqual(revoked,['blob:initial','blob:test-1'],
 'release only the replaced page and failed render Blobs, never the live new image');
console.log('PASS: rejected stale render preserves the newer page and releases only its own partial image Blobs');

// One transient request failure must not poison a cached promise for this visit.
let manifestCalls=0,packCalls=0;
const retryContext=vm.createContext({TextDecoder,URL,Response,DecompressionStream,
 window:{addEventListener(){}},document:{addEventListener(){}},
 fetch:async path=>{
  if(path==='manifest.json'){
   manifestCalls++;
   if(manifestCalls===1)throw new TypeError('Failed to fetch');
   return {ok:true,json:async()=>({files:{}})};
  }
  packCalls++;
  if(packCalls===1)throw new TypeError('Failed to fetch');
  const {gzipSync}=await import('node:zlib');
  return new Response(gzipSync(Buffer.from('recovered evidence')));
 }
});
vm.runInContext(js,retryContext);
await assert.rejects(vm.runInContext('manifest()',retryContext),/Failed to fetch/);
await vm.runInContext('Promise.all([manifest(),manifest()])',retryContext);
assert.equal(manifestCalls,2,'retry manifest once and share successful/in-flight requests');
await assert.rejects(vm.runInContext("pack('data/033.gz')",retryContext),/Failed to fetch/);
const recovered=await vm.runInContext("pack('data/033.gz')",retryContext);
assert.equal(new TextDecoder().decode(recovered),'recovered evidence');
await vm.runInContext("pack('data/033.gz')",retryContext);
assert.equal(packCalls,2,'successful content remains cached after retry');
console.log('PASS: transient manifest and content failures can recover without refreshing the report');

let retryButton;
const failedBody={replaceChildren(){},append(node){if(node.type==='button')retryButton=node}};
const failureContext=vm.createContext({TextDecoder,URL,URLSearchParams,location:{hash:'#page=attempts%2Fexample.html'},
 window:{addEventListener(){},scrollX:0,scrollY:0},
 document:{body:failedBody,addEventListener(){},querySelector:()=>null,
 createElement:()=>({addEventListener(name,handler){this[name]=handler}})},
 fetch:async()=>{throw new TypeError('Failed to fetch')}
});
vm.runInContext(js,failureContext);
await vm.runInContext('render()',failureContext);
assert.equal(retryButton.textContent,'重新加载');
assert.equal(typeof retryButton.click,'function');
assert.equal(retryButton.click,vm.runInContext('render',failureContext));
console.log('PASS: failed report route offers an explicit reload action without altering its result');
let metadataCalls=0;
retryContext.readRecords=async()=>{
 metadataCalls++;
 if(metadataCalls===1)throw new TypeError('Failed to fetch');
 return JSON.stringify({provider_metadata:{status:'preserved'}});
};
vm.runInContext('textFile=readRecords',retryContext);
await assert.rejects(vm.runInContext('metadata(0)',retryContext),/Failed to fetch/);
assert.equal(JSON.parse(await vm.runInContext('metadata(0)',retryContext)).status,'preserved');
assert.equal(metadataCalls,2);
console.log('PASS: metadata retries also discard rejected parsed-record promises');

const smallListeners=new Map();let smallReads=0;
const smallPre={dataset:{file:'outputs/small.md'},classList:{add(){},remove(){}},
 setAttribute(name,value){this[name]=value},removeAttribute(name){delete this[name]},
 addEventListener(name,handler){smallListeners.set(name,handler)},removeEventListener(name){smallListeners.delete(name)}};
retryContext.smallPre=smallPre;
retryContext.readSmall=async()=>{
 if(++smallReads===1)throw new TypeError('Failed to fetch');
 return 'original output bytes';
};
vm.runInContext('textFile=readSmall',retryContext);
await vm.runInContext('fill(smallPre)',retryContext);
assert.equal(smallPre.role,'button');assert.match(smallPre.textContent,/点击重试/);
await smallListeners.get('click')({type:'click',preventDefault(){}});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(smallPre.textContent,'original output bytes');
assert.equal(smallReads,2);assert.equal(smallPre.role,undefined);
console.log('PASS: small evidence has a working retry action just like large evidence');
vm.runInContext("location.hash='#page=en%2Fattempts%2Fexample.html'",failureContext);
await vm.runInContext('render()',failureContext);
assert.equal(retryButton.textContent,'Reload','failed English route uses its requested path before render completes');
vm.runInContext("currentPage='en/attempts/example.html';textFile=async()=>{throw new Error('Network error')}",retryContext);
smallPre.dataset.loaded='';
await vm.runInContext('fill(smallPre)',retryContext);
assert.match(smallPre.textContent,/Click to retry/);
assert.equal(smallPre['aria-label'],'Load complete content');
console.log('PASS: route and evidence retry controls use the active report language');
