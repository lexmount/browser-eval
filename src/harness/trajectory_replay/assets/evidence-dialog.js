(()=>{
const dialog=document.getElementById('evidence-dialog');
if(!dialog || typeof dialog.showModal!=='function') return;
const title=document.getElementById('evidence-title');
const content=document.getElementById('evidence-content');
const action=document.getElementById('evidence-action');
let opener=null;
document.addEventListener('click',event=>{
  const link=event.target.closest('a[data-evidence]');
  if(!link || event.button!==0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const source=document.getElementById(link.dataset.evidence);
  if(!source?.querySelector('pre')) return;
  event.preventDefault(); opener=link;
  title.textContent=link.getAttribute('aria-label') || '原始证据';
  content.textContent=source.querySelector('pre').textContent;
  const cell=link.closest('td');
  const method=cell?.querySelector('.action-name')?.textContent;
  action.textContent=method ? '浏览器动作：'+method : '';
  action.hidden=!method;
  document.documentElement.classList.add('evidence-modal-open');
  dialog.showModal(); content.scrollTop=0;
});
dialog.addEventListener('close',()=>{
  document.documentElement.classList.remove('evidence-modal-open');
  opener?.focus({preventScroll:true});
});
dialog.addEventListener('click',event=>{
  if(event.target!==dialog) return;
  const r=dialog.getBoundingClientRect();
  if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) dialog.close();
});
document.documentElement.classList.add('evidence-enhanced');
// Old deep links remain readable, even with the floating viewer enabled.
function revealHash(){
  const id=location.hash.slice(1);
  if(!/^evidence-\d+$/.test(id)) return;
  document.querySelector('a[data-evidence="'+id+'"]')?.click();
}
window.addEventListener('hashchange',revealHash); revealHash();
})();