

document.querySelector('[data-theme]').onclick=()=>{document.body.className=document.body.className==='dark'?'light':'dark'};
document.querySelectorAll('table[data-sort-controls]').forEach(table=>{
  const original=new Map([...table.tBodies[0].rows].map((row,index)=>[row,index]));
  function value(cell){
    if(cell.hasAttribute('data-sort'))return {value:Number(cell.dataset.sort),numeric:true};
    if(cell.hasAttribute('data-value'))return {value:cell.dataset.value===''?null:Number(cell.dataset.value),numeric:true};
    const text=cell.textContent.replace(/^★\s*/,'').trim();
    if(!text||text==='—')return {value:null,numeric:false};
    const number=text.replaceAll(',','').replace(/%$/,'');
    return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(number)?{value:Number(number),numeric:true}:{value:text,numeric:false};
  }
  table.querySelectorAll('[data-sort-column]').forEach(button=>button.onclick=()=>{
    const index=Number(button.dataset.sortColumn),ascending=button.parentElement.getAttribute('aria-sort')!=='ascending';
    table.querySelectorAll('thead th').forEach(header=>header.setAttribute('aria-sort','none'));
    button.parentElement.setAttribute('aria-sort',ascending?'ascending':'descending');
    const rows=[...table.tBodies[0].rows];
    rows.sort((a,b)=>{
      const x=value(a.cells[index]),y=value(b.cells[index]);
      if(x.value===null||y.value===null)return x.value===y.value?original.get(a)-original.get(b):x.value===null?1:-1;
      const difference=x.numeric&&y.numeric?x.value-y.value:String(x.value).localeCompare(String(y.value),document.documentElement.lang,{numeric:true});
      return difference*(ascending?1:-1)||original.get(a)-original.get(b);
    });
    rows.forEach(row=>table.tBodies[0].append(row));
  });
});
let pf=document.querySelector('[data-population-filter]');let f=document.querySelector('[data-filter]'),q=document.querySelector('[data-query]'),g=document.querySelector('[data-cohort-filter]'),o=document.querySelector('[data-origin-filter]'),m=document.querySelector('[data-method-filter]');function filter(){document.querySelectorAll('[data-row]').forEach(r=>{r.hidden=(pf&&pf.value!=='all'&&!JSON.parse(pf.selectedOptions[0].dataset.caseIds||'[]').includes(r.dataset.caseId))||(f&&f.value!=='all'&&r.dataset.state!==f.value)||(g&&g.value!=='all'&&r.dataset.cohort!==g.value)||(o&&o.value!=='all'&&r.dataset.origin!==o.value)||(m&&m.value!=='all'&&r.dataset.method!==m.value)||(q&&!r.textContent.toLowerCase().includes(q.value.toLowerCase()))})}if(pf)pf.onchange=filter;if(f)f.onchange=filter;if(q)q.oninput=filter;if(g)g.onchange=filter;if(o)o.onchange=filter;if(m)m.onchange=filter;filter();
document.querySelectorAll('[data-evidence]').forEach(b=>b.onclick=async()=>{let d=document.querySelector('dialog');d.querySelector('a').hidden=true;d.querySelector('pre').textContent='…';d.showModal();try{let r=await fetch(b.dataset.evidence);if(!r.ok)throw Error('HTTP '+r.status);let text=await r.text();if(b.dataset.result!==undefined){let v=JSON.parse(text),items; if(v&&typeof v.body==='string')v=JSON.parse(v.body);else if(v&&v.body&&typeof v.body==='object')v=v.body;items=v?.results??v?.organic??v?.data?.webPages?.value;let index=Number(b.dataset.result);if(!/^[0-9]+$/.test(b.dataset.result)||!Number.isSafeInteger(index)||!Array.isArray(items)||index>=items.length)throw Error('Selected result is missing or invalid');let item=items[index];if(!item||typeof item!=='object'||Array.isArray(item)||!Object.keys(item).length)throw Error('Selected result is missing or invalid');text=JSON.stringify(item,null,2)}else{try{text=JSON.stringify(JSON.parse(text),null,2)}catch{}}d.querySelector('pre').textContent=text}catch(e){d.querySelector('pre').textContent=String(e)}});
document.querySelectorAll('[data-diagnosis]').forEach(b=>b.onclick=()=>{let d=document.querySelector('dialog');d.querySelector('pre').textContent=b.dataset.diagnosis;let a=d.querySelector('a');a.hidden=false;a.href=b.dataset.detail;d.showModal()});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
if(document.querySelector('.mermaid')&&innerWidth>700){let s=document.createElement('script');/*mermaid-script-attributes*/;s.onload=async()=>{mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral'});for(let [i,n] of [...document.querySelectorAll('.mermaid')].entries()){try{let r=await mermaid.render('search-flow-'+i,n.textContent);n.innerHTML=r.svg;n.hidden=false;n.previousElementSibling.hidden=true}catch{}}};document.head.appendChild(s)}
