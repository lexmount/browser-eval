
const key = id => `webfetch-hil:${id}`;
function loadReview(id) {
  const raw = localStorage.getItem(key(id)); if (!raw) return;
  const data = JSON.parse(raw);
  for (const [name, value] of Object.entries(data)) { const el = document.querySelector(`[name="${name}"]`); if (el) el.value = value; }
  const state = document.getElementById('review-state'); if (state) state.textContent = `已保存：${data.decision || '未判定'} · ${data.saved_at || ''}`;
}
function saveReview(id) {
  const form = document.getElementById('hil-form'); const data = Object.fromEntries(new FormData(form).entries());
  data.attempt_id = id; data.saved_at = new Date().toISOString(); localStorage.setItem(key(id), JSON.stringify(data)); loadReview(id);
}
function downloadReview(id) {
  saveReview(id); const blob = new Blob([JSON.stringify(JSON.parse(localStorage.getItem(key(id))), null, 2)], {type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=`${id}.review.json`; a.click(); URL.revokeObjectURL(a.href);
}
function exportAllReviews() {
  const out=[]; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k.startsWith('webfetch-hil:')) out.push(JSON.parse(localStorage.getItem(k)));}
  const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='webfetch-human-reviews.json';a.click();URL.revokeObjectURL(a.href);
}
