(()=>{
const originalFetch=window.fetch.bind(window);const compactCache=new Map();
const compactBase=new URL("../",document.currentScript.src);
const compactRoute=raw=>{
  const url=new URL(raw,location.href);const match=url.pathname.match(/data\/(trajectory|evidence)\/([A-Za-z0-9_.-]+)\.json$/);
  if(!match)return null;
  const kind=match[1];const key=match[2];const id=(key.match(/(?:^|-)(\d+)$/)||[])[1];
  if(id===undefined)return null;
  return {kind,key,shard:String(Number(id)%__SHARD_COUNT__).padStart(2,'0')};
};
window.fetch=async(input,init)=>{
  const route=compactRoute(typeof input==='string'?input:input.url);
  if(!route)return originalFetch(input,init);
  const cacheKey=route.kind+'-'+route.shard;
  if(!compactCache.has(cacheKey))compactCache.set(cacheKey,originalFetch(new URL('data/'+route.kind+'-'+route.shard+'.json',compactBase)).then(response=>{if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json()}));
  const value=(await compactCache.get(cacheKey))[route.key];
  return value===undefined?new Response('',{status:404}):new Response(JSON.stringify(value),{status:200,headers:{'content-type':'application/json'}});
};
document.addEventListener('click',event=>{
  const anchor=event.target.closest?.('a[href]');if(!anchor)return;
  const route=compactRoute(anchor.href);if(route?.kind!=='evidence')return;
  event.preventDefault();
  location.href='evidence-raw.html?key='+encodeURIComponent(route.key);
},true);
})();
