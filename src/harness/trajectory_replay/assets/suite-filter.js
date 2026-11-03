(()=>{const cards=[...document.querySelectorAll('.suite-task')];
const controls=Object.fromEntries(['query','difficulty','site','action'].map(k=>[k,document.getElementById('suite-'+k)]));
function apply(){const q=controls.query.value.trim().toLowerCase();let count=0;for(const card of cards){const d=card.dataset;
const match=(!q||d.search.toLowerCase().includes(q))&&(!controls.difficulty.value||d.difficulty===controls.difficulty.value)&&(!controls.site.value||d.site===controls.site.value)&&(!controls.action.value||d.actions.split(',').includes(controls.action.value));
card.hidden=!match;if(match)count++;}document.getElementById('suite-count').textContent=`显示 ${count} / ${cards.length} 个任务`;document.getElementById('suite-empty').hidden=count!==0;}
for(const [key,el] of Object.entries(controls))el.addEventListener(key==='query'?'input':'change',apply);
document.getElementById('suite-reset').onclick=()=>{for(const el of Object.values(controls))el.value='';apply();};apply();})();