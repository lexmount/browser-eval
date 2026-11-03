(async()=>{
  const key=new URLSearchParams(location.search).get('key')||'';
  const match=key.match(/(?:^|-)(\d+)$/);
  if(!match)throw Error('invalid evidence route');
  const shard=String(Number(match[1])%__SHARD_COUNT__).padStart(2,'0');
  const response=await fetch(`__DATA_PREFIX__data/evidence-${shard}.json`);
  if(!response.ok)throw Error(`HTTP ${response.status}`);
  const value=(await response.json())[key];
  if(value===undefined)throw Error('evidence route not found');
  document.querySelector('pre').textContent=JSON.stringify(value,null,2);
})().catch(error=>document.querySelector('pre').textContent='__ERROR_PREFIX__'+error.message);
