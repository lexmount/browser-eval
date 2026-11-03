(async()=>{
  const value=new URLSearchParams(location.search).get('__ROUTE_PARAMETER__')||'';
  const match=value.match(/(?:^|-)(\d+)$/);
  if(!match)throw Error('invalid __ROUTE_KIND__ route');
  const shard=String(Number(match[1])%__SHARD_COUNT__).padStart(2,'0');
  const response=await fetch(`pages/__ROUTE_KIND__-${shard}.json`);
  if(!response.ok)throw Error(`HTTP ${response.status}`);
  const page=(await response.json())[value];
  if(!page)throw Error('__ROUTE_KIND__ route not found');
  document.open();document.write(page);document.close();
})().catch(error=>document.querySelector('main').textContent='__ERROR_PREFIX__'+error.message);
