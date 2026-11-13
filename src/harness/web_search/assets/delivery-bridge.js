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
 if(path&&!/^(?:[a-z]+:|\/)/i.test(path)&&(!options?.method||options.method.toUpperCase()==='GET')){
  const idx=await index();
  if(Object.hasOwn(idx.files,path))return new Response(restoreBytes(await stored(path)),{headers:{'Content-Type':path.endsWith('.html')?'text/html; charset=utf-8':'application/json'}});
 }
 return nativeFetch(input,options);
};
function routeLink(value){
 if(!value||/^(https?:|#)/.test(value))return value;
 if(/(^|\/)details\//.test(value))return 'detail.html#'+encodeURIComponent(value);
 if(value.startsWith('calls/'))return 'evidence.html#'+encodeURIComponent(value);
 if(deliveryIndex?.files&&Object.hasOwn(deliveryIndex.files,value))return 'evidence.html#'+encodeURIComponent(value);
 return value;
}
function rewriteLinks(root){
 root.querySelectorAll('[href],[data-detail]').forEach(e=>{
  for(const a of ['href','data-detail'])if(e.hasAttribute(a))e.setAttribute(a,routeLink(e.getAttribute(a)));
 });
}
rewriteLinks(document);
