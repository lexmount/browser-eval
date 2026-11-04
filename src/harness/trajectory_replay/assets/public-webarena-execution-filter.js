(()=>{
const cards=[...document.querySelectorAll('.execution-card')];
const engines=__ENGINE_LIST__;
const ids=['task','engine','status','combined','complexity','action','tag','error','duration','query'];
const el=Object.fromEntries(ids.map(x=>[x,document.getElementById('f-'+x)]));
function apply(){
 let n=0;
 for(const c of cards){
  const d=Number(c.dataset.duration||0),duration=el.duration.value;
  const ok=(!el.task.value||c.dataset.task===el.task.value)&&(!el.engine.value||c.dataset.engine===el.engine.value)&&(!el.status.value||c.dataset.status===el.status.value)&&(!el.combined.value||c.dataset.combinedStatus===el.combined.value)&&(!el.complexity.value||c.dataset.complexity===el.complexity.value)&&(!el.action.value||c.dataset.actions.split(',').includes(el.action.value))&&(!el.tag.value||c.dataset.tags.split(',').includes(el.tag.value))&&(!el.error.value||c.dataset.error.split(',').includes(el.error.value))&&(!duration||(duration==='lt1000'&&d<1000)||(duration==='1000-5000'&&d>=1000&&d<5000)||(duration==='gte5000'&&d>=5000))&&(!el.query.value||c.dataset.search.toLowerCase().includes(el.query.value.toLowerCase()));
  c.hidden=!ok;if(ok)n++;
 }
 document.getElementById('f-count').textContent=`显示 ${n} / ${cards.length} 个任务 × 浏览器记录`;
 const empty=document.getElementById('execution-empty');empty.hidden=n!==0;
 const providerOnly=el.engine.value&&el.status.value==='fail'&&ids.filter(x=>!['engine','status'].includes(x)).every(x=>!el[x].value);
 empty.textContent=providerOnly?`${el.engine.value} 本轮没有失败任务。`:'当前筛选条件下没有匹配的任务。';
}
function clear(){for(const x of ids)el[x].value='';}
function defaults(){clear();el.status.value='fail';apply();}
function reveal(){
 const raw=location.hash.slice(1);
 if(raw.startsWith('provider=')){
  const params=new URLSearchParams(raw),provider=params.get('provider');
  if(!engines.includes(provider))return;
  clear();el.engine.value=provider;el.status.value=params.get('status')==='fail'?'fail':'';apply();
  document.getElementById('execution-filters').scrollIntoView({block:'start'});return;
 }
 let id;try{id=decodeURIComponent(raw);}catch{return;}
 const card=document.getElementById(id);
 if(card&&card.classList.contains('execution-card')){
  clear();apply();card.style.outline='3px solid #f4b400';
  requestAnimationFrame(()=>requestAnimationFrame(()=>card.scrollIntoView({block:'start'})));
 }
}
for(const x of ids)el[x].addEventListener(x==='query'?'input':'change',apply);
document.getElementById('f-all').onclick=()=>{clear();apply()};
document.getElementById('f-reset').onclick=defaults;
addEventListener('hashchange',reveal);apply();reveal();
})()