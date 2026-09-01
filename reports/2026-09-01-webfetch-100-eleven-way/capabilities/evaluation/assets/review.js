
const key = id => `webfetch-hil:${id}`;
function loadReview(id) {
  const raw = localStorage.getItem(key(id)); if (!raw) return;
  const data = JSON.parse(raw);
  for (const [name, value] of Object.entries(data)) { const el = document.querySelector(`[name="${name}"]`); if (el) el.value = value; }
  const state = document.getElementById('review-state'); if (state) state.textContent = `已保存：${data.decision || '未判定'} · ${data.saved_at || ''}`;
}
function saveReview(id) {
  const form = document.getElementById('hil-form'); const data = Object.fromEntries(new FormData(form).entries());
  if (!form.reportValidity()) return false;
  data.attempt_id = id; data.saved_at = new Date().toISOString(); localStorage.setItem(key(id), JSON.stringify(data)); loadReview(id);
  return true;
}
function downloadReview(id) {
  if (!saveReview(id)) return; const blob = new Blob([JSON.stringify(JSON.parse(localStorage.getItem(key(id))), null, 2)], {type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=`${id}.review.json`; a.click(); URL.revokeObjectURL(a.href);
}
function exportAllReviews() {
  const out=[]; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k.startsWith('webfetch-hil:')) out.push(JSON.parse(localStorage.getItem(k)));}
  const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='webfetch-human-reviews.json';a.click();URL.revokeObjectURL(a.href);
}
function filterFailures() {
  const provider=(document.getElementById('failure-provider')?.value||'').toLowerCase();
  const category=(document.getElementById('failure-category')?.value||'').toLowerCase();
  const query=(document.getElementById('failure-query')?.value||'').toLowerCase();
  let visible=0;
  document.querySelectorAll('#failure-rows tr').forEach(row=>{const show=(!provider||row.dataset.provider===provider)&&(!category||row.dataset.category===category)&&(!query||row.textContent.toLowerCase().includes(query));row.hidden=!show;if(show)visible++;});
  const count=document.getElementById('failure-count');if(count)count.textContent=`显示 ${visible} 条`;
}
function sortTable(tableId, column, type, initialDirection) {
  const table=document.getElementById(tableId); if(!table) return;
  const body=table.tBodies[0]; const header=table.tHead.rows[0].cells[column];
  const previous=Number(table.dataset.sortColumn); const current=table.dataset.sortDirection;
  const direction=previous===column?(current==='desc'?'asc':'desc'):(initialDirection||'desc');
  const value=row=>{const cell=row.cells[column];const raw=cell?.dataset.sort??cell?.textContent??'';return type==='number'?(Number(raw)||0):raw.trim().toLowerCase();};
  const rows=[...body.rows].sort((a,b)=>{const av=value(a),bv=value(b);const result=type==='number'?av-bv:av.localeCompare(bv,'zh-CN');return direction==='asc'?result:-result;});
  rows.forEach(row=>body.appendChild(row)); table.dataset.sortColumn=String(column);table.dataset.sortDirection=direction;
  [...table.tHead.rows[0].cells].forEach(cell=>cell.removeAttribute('aria-sort'));header.setAttribute('aria-sort',direction==='asc'?'ascending':'descending');
  rows.forEach((row,index)=>{const rank=row.querySelector('[data-rank]');if(rank)rank.textContent=String(index+1);});
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const button=document.getElementById('theme-toggle');
  if(button){button.textContent=theme==='dark'?'切换白底':'切换深色';button.setAttribute('aria-pressed',theme==='dark'?'true':'false');}
}
function toggleTheme(){const next=document.documentElement.dataset.theme==='dark'?'light':'dark';localStorage.setItem('webfetch-theme',next);applyTheme(next);}
window.addEventListener('DOMContentLoaded',()=>applyTheme(localStorage.getItem('webfetch-theme')||'light'));
