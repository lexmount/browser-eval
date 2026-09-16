"""Evidence-first report: measured tracks, canonical cases, no review workflow."""
from __future__ import annotations
import hashlib
import html
import json
import re
import shutil
import statistics
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import urlsplit,urlencode
from .metrics import percentile, task_passed, task_measured
from .methods import method_info
from .accounts import report_access_registry
from .report_interfaces import HEADLESS_ACCESS
from .report_methods import PROFILES, adoption_counts
from .report_style import STYLE
from .report_components import tabs, metric_guide, metric_legend, examples, reference_status, basis_label, reference_label, sources_panel, stable_contract_details, task_instruction
from .failure_diagnosis import diagnose
from .safety import contained_path, safe_href, validate_report_ids
from .models import execution_id
from .report_resources import render_resource_comparison
from .webmainbench_diagnostics import ERROR_PAGE

TRACKS={'web':'静态网站正文','dynamic':'动态网站正文','challenge':'热门网站正文','document':'文档正文','image':'图片获取','video':'视频获取'}
PROVIDER_LABELS={'moli-1.0.6':'Moli 1.0.6','moli':'Moli 1.1.5','moli-1.1.3':'Moli 1.1.3'}
ROUTE_LABELS={
 'cloudflare_native_markdown':'Cloudflare Browser Run /markdown 直接返回 Markdown',
 'lightpanda_native_markdown':'Lightpanda CLI 直接返回 Markdown',
 'obscura_native_markdown':'Obscura CLI 直接返回 Markdown',
 'moli_native_markdown':'Moli CLI 直接返回 Markdown',
 'node-html-markdown':'Chromium 渲染 DOM → node-html-markdown 2.0.0',
}
def provider_label(name):return PROVIDER_LABELS.get(name,name)
def active_tracks(cases):
 return [(key,label) for key,label in TRACKS.items() if any(c.get('track','web')==key for c in cases.values())]

def track_count_label(count):return str(count)

def is_headless_cohort(records):
 return bool(records) and all(r.get('provider_type') in {'headless_html','headless_markdown'} for r in records)

def provider_configuration(name,records,info):
 """Describe only the route evidenced for this provider."""
 provider_type=records[0].get('provider_type') if records else ''
 if provider_type in {'headless_html','headless_markdown'}:
  metadata=[r.get('provider_metadata',{}) for r in records]
  extractors=sorted({ROUTE_LABELS.get(m.get('extractor'),m.get('extractor')) for m in metadata if m.get('extractor')})
  contracts=sorted({str(m.get('output_contract')) for m in metadata if m.get('output_contract')})
  route='；'.join(extractors) or info['endpoint']
  output='、'.join(contracts) or info['output']
  return f'本轮记录的交付路线：{route}；输出契约：{output}。未启用额外的理解模型或付费后备方法。'
 resource_routes={
  'firecrawl':'正文与资源题请求 Markdown、links、images；原文件题改用唯一 rawBase64 格式并验收解码字节。动态顶层选择器使用 wait action，数量与 iframe 条件明确标为未完整执行。',
  'tavily_extract':'本轮请求 Markdown；图片资源仅来自同一 Extract 响应的 images 字段。',
  'exa_contents':'本轮请求正文文本；资源链接仅来自同一 Contents 响应的 extras 字段。',
 }
 return resource_routes.get(provider_type,'本轮使用该方法的默认正文或文件交付路线。')+' 未启用额外的理解模型或付费后备方法。'
JS=r'''function readTheme(){try{return localStorage.getItem('webfetch-theme')}catch{return 'light'}}
function saveTheme(t){try{localStorage.setItem('webfetch-theme',t)}catch{}}

function theme(){let t=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=t;saveTheme(t)}
function tr(zh,en){return document.documentElement?.lang==='en'?en:zh}
function filterRows(){let count=0;document.querySelectorAll('#rows tr').forEach(r=>{let show=[...document.querySelectorAll('[data-filter]')].every(s=>!s.value||(s.dataset.filter==='query'?(r.textContent+' '+(r.dataset.search||'')).toLowerCase().includes(s.value.trim().toLowerCase()):r.dataset[s.dataset.filter]===s.value));r.hidden=!show;if(show)count++});let c=document.querySelector('#count');if(c)c.textContent=tr(`显示 ${count} 条`,`Showing ${count}`)}
function paintSort(t){let col=Number(t.dataset.col);if(!Number.isFinite(col)||!t.tHead?.rows[0]?.cells[col])return;[...t.tHead.rows[0].cells].forEach((h,i)=>{h.classList.toggle('sorted',i===col);h.removeAttribute('aria-sort')});t.tHead.rows[0].cells[col].setAttribute('aria-sort',t.dataset.dir==='asc'?'ascending':'descending')}
function sortTable(id,col){let t=document.getElementById(id),dir=t.dataset.col===String(col)&&t.dataset.dir==='desc'?'asc':'desc';let rows=[...t.tBodies[0].rows];rows.sort((a,b)=>{let av=a.cells[col].dataset.sort,bv=b.cells[col].dataset.sort;if(av==='-1'||bv==='-1')return av===bv?0:av==='-1'?1:-1;let result=(av==null||bv==null)?a.cells[col].textContent.localeCompare(b.cells[col].textContent,document.documentElement.lang):Number(av)-Number(bv);return dir==='asc'?result:-result});rows.forEach((r,i)=>{r.cells[0].textContent=i+1;t.tBodies[0].append(r)});t.dataset.col=String(col);t.dataset.dir=dir;paintSort(t);if(t.hasAttribute?.('data-wmb-table'))wmbFilters();if(t.id==='wmb-slices')wmbSliceFilters()}
function chooseTab(group,key){let box=document.getElementById(group);if(!box)return;box.querySelectorAll(':scope > .tab-bar > button').forEach(b=>{let on=b.dataset.tab===key;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1});box.querySelectorAll(':scope > [role="tabpanel"]').forEach(p=>p.hidden=p.dataset.panel!==key)}
function tabKeys(event,group){if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;let bs=[...document.getElementById(group).querySelectorAll(':scope > .tab-bar > button')],i=bs.indexOf(event.target);i=event.key==='Home'?0:event.key==='End'?bs.length-1:(i+(event.key==='ArrowRight'?1:-1)+bs.length)%bs.length;event.preventDefault();chooseTab(group,bs[i].dataset.tab);bs[i].focus()}
function cascadeFilters(changed){let rows=[...document.querySelectorAll('#rows tr')],selects=[...document.querySelectorAll('select[data-filter]')],prior=[];selects.forEach(s=>{let available=new Set(rows.filter(r=>prior.every(p=>!p.value||r.dataset[p.dataset.filter]===p.value)).map(r=>r.dataset[s.dataset.filter]));if(s.dataset.filter!=='result'){let failureFilter=['failureStage','failureCategory'].includes(s.dataset.filter);[...s.options].forEach(o=>o.disabled=!!o.value&&!available.has(o.value));if(s.value&&!available.has(s.value))s.value=failureFilter?'':([...s.options].find(o=>o.value&&!o.disabled)?.value||'');if(failureFilter)s.disabled=![...s.options].some(o=>o.value&&!o.disabled);}prior.push(s)});filterRows()}
function outcomes(row,key){return new Set((row.dataset[key]||'').split(' ').filter(Boolean))}
function compareMethods(){let a=document.getElementById('compare-a'),relation=document.getElementById('compare-relation'),track=document.getElementById('compare-track'),count=document.getElementById('compare-count');if(!a||!relation||!track||!count)return;let boxes=[...document.querySelectorAll('[data-compare-method]')];boxes.forEach(box=>{box.disabled=box.value===a.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});let others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value),selected=[a.value,...others],shown=0;document.querySelectorAll('#compare-rows tr').forEach(row=>{let pass=outcomes(row,'pass'),fail=outcomes(row,'fail'),match=others.length>0&&(!track.value||row.dataset.track===track.value);if(match)match={mixed:selected.some(x=>pass.has(x))&&selected.some(x=>fail.has(x)),a_pass_others_fail:pass.has(a.value)&&others.every(x=>fail.has(x)),a_fail_others_pass:fail.has(a.value)&&others.every(x=>pass.has(x)),all_pass:selected.every(x=>pass.has(x)),all_fail:selected.every(x=>fail.has(x)),exact_pass:selected.every(x=>pass.has(x))&&pass.size===selected.length}[relation.value];row.hidden=!match;if(match)shown++});count.textContent=others.length?tr(`找到 ${shown} 个任务`,`${shown} tasks found`):tr('请至少选择一个对比方法','Select at least one comparison method')}
function wmbCompare(){let a=document.getElementById('wmb-compare-a'),relation=document.getElementById('wmb-compare-relation'),axis=document.getElementById('wmb-compare-axis'),category=document.getElementById('wmb-compare-category'),count=document.getElementById('wmb-compare-count');if(!a||!relation||!axis||!category||!count)return;let boxes=[...document.querySelectorAll('[data-wmb-compare-method]')];boxes.forEach(box=>{box.disabled=box.value===a.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});let others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value),selected=[a.value,...others],rows=[...document.querySelectorAll('#wmb-compare-rows tr')],allowed=new Set(rows.map(row=>row.dataset[axis.value]).filter(Boolean));for(let option of category.options)option.disabled=!!option.value&&!!axis.value&&!allowed.has(option.value);if(category.value&&!allowed.has(category.value))category.value='';category.disabled=!axis.value;let shown=0;for(let row of rows){let delivered=outcomes(row,'delivered'),failed=outcomes(row,'failed'),match=others.length>0&&(!axis.value||!category.value||row.dataset[axis.value]===category.value);if(match)match={mixed:selected.some(x=>delivered.has(x))&&selected.some(x=>failed.has(x)),a_success_others_fail:delivered.has(a.value)&&others.every(x=>failed.has(x)),a_fail_others_success:failed.has(a.value)&&others.every(x=>delivered.has(x)),all_success:selected.every(x=>delivered.has(x)),all_fail:selected.every(x=>failed.has(x)),exact_success:selected.every(x=>delivered.has(x))&&delivered.size===selected.length}[relation.value];row.hidden=!match;if(match){shown++;row.cells[0].textContent=shown}}count.textContent=others.length?tr(`显示 ${shown} / ${rows.length} 页`,`Showing ${shown} / ${rows.length} pages`):tr('请至少选择一个对比方法','Select at least one comparison method')}
let callLogTrigger=null;
function openCallLog(button){let dialog=document.getElementById(button.dataset.callLogOpen);if(!dialog)return;callLogTrigger=button;if(!dialog.dataset.bound){dialog.dataset.bound='1';dialog.addEventListener('close',()=>{let trigger=callLogTrigger;callLogTrigger=null;trigger?.focus()});dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();if(typeof dialog.close==='function')dialog.close();else{dialog.removeAttribute('open');let trigger=callLogTrigger;callLogTrigger=null;trigger?.focus()}}});dialog.addEventListener('click',event=>{if(event.target===dialog){if(typeof dialog.close==='function')dialog.close();else closeCallLog({closest:()=>dialog})}})}if(typeof fill==='function')dialog.querySelectorAll('pre[data-file],pre[data-record]').forEach(pre=>fill(pre));if(typeof dialog.showModal==='function')dialog.showModal();else{dialog.setAttribute('open','');dialog.setAttribute('aria-modal','true')}dialog.querySelector('[data-call-log-close]')?.focus()}
function closeCallLog(button){let dialog=button.closest('dialog');if(!dialog)return;if(typeof dialog.close==='function')dialog.close();else{dialog.removeAttribute('open');let trigger=callLogTrigger;callLogTrigger=null;trigger?.focus()}}
function applyReportAnchor(anchor){if(!anchor)return;if(wmbInspect(anchor))return;if(anchor.startsWith('track-')){chooseTab('dataset-hub','cases');let s=document.querySelector('select[data-filter="track"]');if(s){s.value=anchor.slice(6);document.querySelectorAll('select[data-filter]').forEach(x=>{if(x!==s)x.value=''});cascadeFilters(s)}}else if(['references','sources'].includes(anchor)){chooseTab('dataset-hub',anchor)}else{let target=document.getElementById(anchor);for(let p=target;p;p=p.parentElement){if(p.dataset?.panel)chooseTab(p.parentElement.id,p.dataset.panel);if(p.tagName==='DETAILS')p.open=true}}}
function wmbFilters(){let table=document.querySelector('[data-wmb-table]');if(!table)return;let rows=table.tBodies[0].rows,controls=[...document.querySelectorAll('[data-wmb-filter]')],values=Object.fromEntries(controls.map(c=>[c.dataset.wmbFilter,c.value])),axis=values.axis||'',query=(values.query||'').trim().toLowerCase();let category=controls.find(c=>c.dataset.wmbFilter==='category');if(category){let allowed=new Set();for(let row of rows)if(!values.method||row.dataset.method===values.method)allowed.add(row.dataset[axis]);for(let option of category.options)option.disabled=!!option.value&&!allowed.has(option.value);if(category.value&&!allowed.has(category.value)){category.value='';values.category=''}}let failure=controls.find(c=>c.dataset.wmbFilter==='failure');if(failure){let available=new Set();for(let row of rows)if((!values.method||row.dataset.method===values.method)&&(!values.request||row.dataset.request===values.request)&&row.dataset.failure)available.add(row.dataset.failure);for(let option of failure.options)option.disabled=!!option.value&&!available.has(option.value);failure.disabled=values.request==='success'||available.size===0;if(failure.disabled||failure.value&&!available.has(failure.value)){failure.value='';values.failure=''}}let band=controls.find(c=>c.dataset.wmbFilter==='band');if(band){band.disabled=!values.metric;if(band.disabled){band.value='';values.band=''}}let count=0;for(let row of rows){let search=row.dataset.searchNormalized||(row.dataset.searchNormalized=(row.dataset.search||'').toLowerCase());let show=(!values.method||row.dataset.method===values.method)&&(!axis||!values.category||row.dataset[axis]===values.category)&&(!values.request||row.dataset.request===values.request)&&(!values.failure||row.dataset.failure===values.failure)&&(!query||search.includes(query));if(show&&values.metric){let raw=row.dataset[values.metric],number=raw===''||raw==null?null:Number(raw);if(values.band==='unavailable')show=number===null;else if(values.band){let [low,high]=values.band.split(':').map(Number);show=number!==null&&number>=low&&number<(high===1?1.0000001:high)}}row.hidden=!show;if(show){count++;row.cells[0].textContent=count}}let indicator=document.querySelector('[data-wmb-count]');if(indicator)indicator.textContent=tr(`显示 ${count} / ${rows.length} 条`,`Showing ${count} / ${rows.length}`)}
function wmbInspect(anchor){if(!anchor.startsWith('inspect?'))return false;let params=new URLSearchParams(anchor.slice(8).replace(/\+/g,'%2B'));document.querySelectorAll('[data-wmb-filter]').forEach(c=>{let value=params.get(c.dataset.wmbFilter);if(value!==null)c.value=value});wmbFilters();(document.querySelector('.wmb-controls')||document.querySelector('[data-wmb-table]')?.closest('section'))?.scrollIntoView({block:'start'});return true}
function wmbReset(){document.querySelectorAll('[data-wmb-filter]').forEach(c=>{c.value=c.dataset.wmbDefault||''});wmbFilters()}
function wmbSliceFilters(){let table=document.getElementById('wmb-slices'),axis=document.getElementById('wmb-slice-axis'),category=document.getElementById('wmb-slice-category'),method=document.getElementById('wmb-slice-method'),count=document.getElementById('wmb-slice-count');if(!table||!axis||!category||!method||!count)return;let rows=[...table.tBodies[0].rows],available=new Set(rows.filter(r=>r.dataset.axis===axis.value&&(!method.value||r.dataset.method===method.value)).map(r=>r.dataset.category));for(let option of category.options)option.disabled=!!option.value&&!available.has(option.value);if(category.value&&!available.has(category.value))category.value='';let shown=0;for(let row of rows){let visible=row.dataset.axis===axis.value&&(!category.value||row.dataset.category===category.value)&&(!method.value||row.dataset.method===method.value);row.hidden=!visible;if(visible)row.cells[0].textContent=++shown}count.textContent=tr(`显示 ${shown} / ${rows.length} 条分类结果`,`Showing ${shown} / ${rows.length} category results`)}
function wmbDistribution(){let table=document.getElementById('wmb-distribution'),axis=document.getElementById('wmb-distribution-axis'),count=document.getElementById('wmb-distribution-count');if(!table||!axis||!count)return;let rows=[...table.tBodies[0].rows],shown=0,parent=0,sample=0;for(let row of rows){let visible=row.dataset.axis===axis.value;row.hidden=!visible;if(visible){row.cells[0].textContent=++shown;parent+=Number(row.dataset.parent||0);sample+=Number(row.dataset.sample||0)}}count.textContent=tr(`${shown} 个分类 · 母集合计 ${parent} · 样本合计 ${sample}`,`${shown} categories · parent total ${parent} · sample total ${sample}`)}
function initReport(){document.documentElement.dataset.theme=readTheme()||'light';document.querySelectorAll('[data-filter]').forEach(e=>e.addEventListener(e.tagName==='INPUT'?'input':'change',e.tagName==='INPUT'?filterRows:()=>cascadeFilters(e)));let wmbTimer;document.querySelectorAll('[data-wmb-filter]').forEach(e=>e.addEventListener(e.tagName==='INPUT'?'input':'change',e.tagName==='INPUT'?()=>{clearTimeout(wmbTimer);wmbTimer=setTimeout(wmbFilters,90)}:wmbFilters));document.querySelectorAll('[data-wmb-slice-filter]').forEach(e=>e.addEventListener('change',wmbSliceFilters));document.querySelectorAll('[data-wmb-distribution-filter]').forEach(e=>e.addEventListener('change',wmbDistribution));document.querySelectorAll('[data-compare-method],[data-compare-control]').forEach(e=>e.addEventListener('change',compareMethods));document.querySelectorAll('[data-wmb-compare-method],[data-wmb-compare-control]').forEach(e=>e.addEventListener('change',wmbCompare));cascadeFilters();wmbFilters();wmbSliceFilters();wmbDistribution();compareMethods();wmbCompare();document.querySelectorAll('table[data-col]').forEach(paintSort);applyReportAnchor(location.hash.slice(1))}
window.addEventListener('load',()=>{if(location.hash.startsWith('#inspect?'))setTimeout(()=>applyReportAnchor(location.hash.slice(1)),150)});
window.addEventListener('report-ready',initReport);
window.addEventListener('DOMContentLoaded',initReport);
let wmbMermaidModule;
function wmbLoadMermaid(){if(!wmbMermaidModule)wmbMermaidModule=new Promise((resolve,reject)=>{let script=document.createElement('script');script.src='https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js';script.integrity='sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I';script.crossOrigin='anonymous';script.onload=()=>resolve(window.mermaid);script.onerror=()=>{script.remove();wmbMermaidModule=null;reject(new Error('Diagram library unavailable'))};document.head.appendChild(script)});return wmbMermaidModule}
async function wmbRenderFlow(){let node=document.querySelector('.wmb-flow .mermaid');if(!node||node.dataset.rendering)return;node.dataset.rendering='1';try{let mermaid=await wmbLoadMermaid();mermaid.initialize({startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral',flowchart:{htmlLabels:true}});let rendered=await mermaid.render('wmb-evaluation-flow',node.textContent);if(!node.isConnected)return;node.innerHTML=rendered.svg;node.hidden=false;node.closest('.wmb-flow').querySelector('[data-wmb-flow-fallback]').hidden=true}catch(error){node.hidden=true;delete node.dataset.rendering}}
window.addEventListener('report-ready',wmbRenderFlow);
window.addEventListener('DOMContentLoaded',wmbRenderFlow);
window.addEventListener('hashchange',()=>applyReportAnchor(location.hash.slice(1)));
document.addEventListener('click',event=>{let open=event.target.closest('[data-call-log-open]');if(open){openCallLog(open);return}let close=event.target.closest('[data-call-log-close]');if(close)closeCallLog(close)});
'''
EXTRA='''
.table-wrap{margin-top:16px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{vertical-align:top;padding:12px 14px;border-bottom:1px solid var(--line);text-align:left}th{background:var(--panel2);font-size:12px}td:first-child,th:first-child{width:48px;white-space:nowrap}.ranking{min-width:1240px}.ranking td{white-space:nowrap}.ranking td:nth-child(2){white-space:nowrap;min-width:220px;max-width:none;overflow-wrap:normal;word-break:normal}.sort-button{border:0;background:none;color:inherit;font:inherit;font-weight:700;cursor:pointer;padding:0;text-align:left}.grid .card{grid-column:span 4}.card p{margin-bottom:0}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.filters select,.filters input{min-width:150px;padding:10px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:20px}.compare>div{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:620px;overflow:auto}.break{overflow-wrap:anywhere}.status{white-space:nowrap}.source-note{color:var(--muted);font-size:13px}details{padding:10px 0}.accounts-table{min-width:1300px}.accounts-table code{white-space:nowrap}.hero h1{font-size:clamp(30px,4vw,48px)}section[id]{scroll-margin-top:75px}.actions{display:flex;gap:16px;flex-wrap:wrap}nav{gap:16px}.tiny{font-size:12px} @media(max-width:700px){.grid .card{grid-column:span 12}.compare{grid-template-columns:1fr}.wrap{width:calc(100% - 28px)}.filters>*{width:100%}}
'''

EXTRA+='''
[hidden]{display:none!important}.ranking{min-width:980px}.ranking th,table th{white-space:nowrap}.ranking td:nth-child(2){min-width:220px;max-width:none;white-space:nowrap;overflow-wrap:normal;word-break:normal}th.sorted{background:var(--accent-soft);color:var(--accent)}.comparison-winner,.comparison-winner a{color:#117541!important;font-weight:800!important}[data-theme="dark"] .comparison-winner,[data-theme="dark"] .comparison-winner a{color:#74e1a8!important}.tab-bar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 22px}.tab-bar button{padding:10px 18px;border:1px solid var(--line);border-radius:999px;color:var(--ink);background:var(--panel);font:inherit;cursor:pointer}.tab-bar button[aria-selected="true"]{background:#075ccf;color:white;border-color:#075ccf}.tab-bar button:focus-visible,.sort-button:focus-visible{outline:3px solid #428bff;outline-offset:3px}.guide-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.guide-card{padding:22px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}.guide-card h3{margin:0 0 10px;font-size:19px}.guide-card p{margin:0;line-height:1.7}.worked-example{display:flex;gap:16px;flex-wrap:wrap;padding:20px;background:var(--panel2);border-radius:14px;margin-top:20px}.tabs [role="tabpanel"]{min-width:0}#filtered-table{min-width:1080px}#filtered-table td:last-child{min-width:170px;max-width:350px;overflow-wrap:anywhere}#filtered-table td:nth-child(2){min-width:155px}.section{padding:40px 0}.hero{padding:38px 0}.top nav{flex-wrap:wrap}.source-note{line-height:1.7}@media(max-width:700px){.guide-grid{grid-template-columns:1fr}.tab-bar button{padding:8px 12px}.section{padding:28px 0}}
'''

EXTRA+='''
.metric-help{margin:16px 0;padding:16px 20px;background:var(--panel2);border-radius:12px}.metric-help dl{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 24px;margin:0}.metric-help dt{font-size:13px;font-weight:700}.metric-help dd{font-size:13px;color:var(--muted);line-height:1.6;margin:3px 0 0}.metric-help>p{margin:10px 0 0}#filtered-table{min-width:760px}#filtered-table td:last-child{min-width:90px;max-width:none;white-space:nowrap}#filtered-table td:nth-child(2){min-width:160px}details[id^="evidence-"]{scroll-margin-top:85px}@media(max-width:700px){.metric-help dl{grid-template-columns:1fr}.metric-help{padding:12px}}
'''

EXTRA+='''
.interface-panel{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:14px 18px;margin:12px 0;scroll-margin-top:85px}.interface-panel>summary{font-weight:700;cursor:pointer}.interface-panel .guide-card{padding:18px;min-width:0}.interface-panel pre{font-size:12px;line-height:1.6;max-height:330px;margin:10px 0 0;padding:14px}.interface-endpoint{overflow-wrap:anywhere;font-size:13px}.interface-fields{font-size:13px;margin:0}.interface-fields>div{padding:9px 0;border-bottom:1px solid var(--line)}.interface-fields dt{overflow-wrap:anywhere}.interface-fields small{color:var(--muted)}.interface-fields dd{margin:5px 0 0;color:var(--muted)}.interface-panel h4{margin:20px 0 8px}.interface-panel .guide-grid{align-items:start}#filtered-table td:nth-last-child(2){min-width:120px}
'''

EXTRA+='''
.capability-legend{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;padding:18px;background:var(--panel2);border-radius:12px;margin:18px 0}.capability-legend dt{font-weight:700;font-size:14px}.capability-legend dd{margin:5px 0 0;color:var(--muted);font-size:13px}.capability-matrix{min-width:960px}.capability-matrix td{font-size:13px;min-width:115px}.capability-matrix td:first-child{min-width:36px}.capability-matrix td:nth-child(2){min-width:165px;max-width:180px;overflow-wrap:anywhere}.capability-matrix small{display:block;color:var(--muted);font-size:12px;line-height:1.5;margin-top:5px}.capability-runtime{min-width:960px}.capability-runtime td{font-size:13px;max-width:270px;min-width:150px}.capability-runtime td:first-child{min-width:36px}#capability-views>.tab-bar button{font-size:14px;padding:8px 14px}@media(max-width:700px){.capability-legend{grid-template-columns:1fr}.capability-matrix,.capability-runtime{min-width:900px}}
'''

EXTRA+='''
.pricing-table{min-width:940px}.pricing-table th{white-space:normal;line-height:1.5}.pricing-table th:first-child{white-space:nowrap}.pricing-table td{font-size:13px;line-height:1.65;min-width:125px}.pricing-table td:first-child{min-width:36px}.pricing-table td:nth-child(2){min-width:150px}.pricing-table td:last-child{min-width:140px}#method-guide-pricing h3{margin-top:30px}#method-guide-pricing h4{margin-top:0}#method-guide-pricing .worked-example span{flex:1;min-width:200px}@media(max-width:700px){#method-guide-pricing .worked-example{display:block}#method-guide-pricing .worked-example strong{display:block;margin-bottom:8px}}
'''

EXTRA+='''
.attempt-summary{display:grid;grid-template-columns:minmax(260px,1.4fr) repeat(4,minmax(120px,.7fr));gap:12px;align-items:stretch}.attempt-verdict{padding:20px 22px;border:1px solid var(--line);border-left:5px solid var(--good);border-radius:12px;background:var(--success-bg)}.attempt-verdict.failure{border-left-color:var(--bad);background:var(--failure-bg)}.attempt-verdict .status-line{display:block;font-size:24px;font-weight:850;margin-bottom:6px}.attempt-verdict p{margin:0;line-height:1.7}.attempt-fact{padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}.attempt-fact b{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}.attempt-jumps{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px;font-weight:700}.evidence-intro{margin:0 0 18px;color:var(--muted)}.evidence-compare{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}.evidence-pane{min-width:0;border:1px solid var(--line);border-radius:14px;background:var(--panel);overflow:hidden}.evidence-pane>header{padding:17px 19px;border-bottom:1px solid var(--line);background:var(--panel2)}.evidence-pane>header h3{margin:0 0 4px}.evidence-pane>header p{margin:0;color:var(--muted);font-size:13px}.evidence-pane>.pane-body{padding:16px}.evidence-pane pre{margin:0;max-height:560px;border-radius:9px}.answer-list{display:grid;gap:12px}.answer-item{padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--bg)}.answer-item b{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}.answer-item blockquote{margin:0;padding:0;border:0;font-size:15px;white-space:pre-wrap}.encoding-warning{margin:0 0 14px}.decision-lead{padding:16px 18px;border-radius:12px;background:var(--accent-soft);line-height:1.75}.anchor-grid{display:grid;gap:12px;margin-top:16px}.anchor-card,.region-card{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:18px}.anchor-card.pass,.region-card.pass{border-left:4px solid var(--good)}.anchor-card.fail,.region-card.fail{border-left:4px solid var(--bad)}.anchor-head,.region-head{display:flex;align-items:start;justify-content:space-between;gap:18px}.anchor-head h3,.region-head h3{margin:0;font-size:18px}.verdict-badge{white-space:nowrap;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:750;background:var(--panel2)}.anchor-card.pass .verdict-badge,.region-card.pass .verdict-badge{color:var(--good);background:var(--success-bg)}.anchor-card.fail .verdict-badge,.region-card.fail .verdict-badge{color:var(--bad);background:var(--failure-bg)}.evidence-pair,.region-pair{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.evidence-sample{min-width:0;padding:13px 14px;border-radius:10px;background:var(--panel2)}.evidence-sample b{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}.evidence-sample p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.coverage-rule{margin:14px 0 18px}.coverage-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:9px;margin:16px 0}.coverage-chip{display:block;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--ink)}.coverage-chip strong,.coverage-chip span{display:block}.coverage-chip span{font-size:12px;color:var(--muted)}.coverage-chip.good{border-color:var(--good)}.coverage-chip.bad{border-color:var(--bad)}.region-card{margin-top:14px}.region-card pre{max-height:260px;margin:8px 0 0;font-size:12px}.region-head p{margin:4px 0 0;color:var(--muted)}.technical-details{margin-top:18px;border-top:1px solid var(--line)}.technical-details>summary{cursor:pointer;font-weight:700;color:var(--accent);padding:14px 0}.technical-details .table-wrap{box-shadow:none}.section[id]{scroll-margin-top:78px}@media(max-width:980px){.attempt-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.attempt-verdict{grid-column:1/-1}.coverage-strip{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:700px){.attempt-summary,.evidence-compare,.evidence-pair,.region-pair{grid-template-columns:1fr}.coverage-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.evidence-pane pre{max-height:440px}.anchor-head,.region-head{display:block}.verdict-badge{display:inline-block;margin-top:8px}}
'''

EXTRA+='''
.quality-steps{list-style:none;counter-reset:quality-step;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;padding:0;margin:18px 0}.quality-steps li{counter-increment:quality-step;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}.quality-steps li:before{content:counter(quality-step);display:inline-grid;place-items:center;width:26px;height:26px;margin-bottom:10px;border-radius:50%;background:var(--accent);color:white;font-weight:800}.quality-steps b,.quality-steps span{display:block}.quality-steps span{margin-top:6px;color:var(--muted);font-size:13px;line-height:1.6}.review-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.review-summary .card{grid-column:auto}.review-table{min-width:1320px}.review-table td:nth-child(7),.review-table td:nth-child(8){min-width:280px;white-space:normal}.review-table td:nth-child(2){min-width:145px}@media(max-width:900px){.quality-steps,.review-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:600px){.quality-steps,.review-summary{grid-template-columns:1fr}}
'''

EXTRA+='''
.review-rounds{display:grid;gap:14px;margin-top:18px}.review-round{display:grid;grid-template-columns:110px 1fr;gap:18px;padding:20px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}.review-round .round-label{color:var(--accent);font-weight:800}.review-round h3{margin:0 0 8px;font-size:19px}.review-round p{margin:5px 0;line-height:1.7}.review-round .round-result{color:var(--muted)}@media(max-width:600px){.review-round{grid-template-columns:1fr;gap:7px}}
'''

EXTRA+='''
.method-comparison{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:24px;min-width:0;box-shadow:0 12px 32px rgba(25,45,70,.06)}.comparison-heading{display:flex;align-items:start;justify-content:space-between;gap:24px;margin-bottom:22px}.comparison-heading h3{margin:0 0 6px;font-size:22px}.comparison-heading p{color:var(--muted);margin:0;line-height:1.65}.comparison-count{white-space:nowrap;padding:8px 12px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}.comparison-controls{display:grid;grid-template-columns:minmax(170px,1fr) minmax(230px,1.4fr) minmax(170px,1fr);gap:14px}.comparison-controls label{display:grid;gap:6px;font-weight:700}.comparison-controls select{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink);font:inherit}.comparison-methods{border:0;padding:0;margin:20px 0 0}.comparison-methods legend{font-weight:750;margin-bottom:10px}.method-checks{display:flex;gap:8px;flex-wrap:wrap}.method-checks label{display:flex;align-items:center;gap:7px;padding:8px 11px;border:1px solid var(--line);border-radius:999px;background:var(--bg);cursor:pointer}.method-checks label.disabled{opacity:.45;cursor:not-allowed}.method-checks input{accent-color:var(--accent)}.comparison-result{margin-top:20px}.comparison-table{min-width:720px;table-layout:fixed}.comparison-table th:nth-child(2),.comparison-table td:nth-child(2){width:34%}.comparison-table th:nth-child(3),.comparison-table td:nth-child(3),.comparison-table th:nth-child(4),.comparison-table td:nth-child(4){width:33%}.task-cell a{font-weight:750}.task-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:7px}.track-badge{display:inline-block;padding:3px 8px;border-radius:999px;background:var(--panel2);color:var(--muted);font-size:12px;white-space:nowrap}.outcome-list{display:flex;gap:6px;flex-wrap:wrap}.outcome-pill{display:inline-block;padding:5px 8px;border-radius:8px;font-size:13px;text-decoration:none}.outcome-pill.pass{background:var(--success-bg);color:var(--good)}.outcome-pill.fail{background:var(--failure-bg);color:var(--bad)}.large-evidence-output{display:block;width:100%;min-height:520px;max-height:70vh;resize:vertical;padding:16px;border:0;border-radius:9px;background:#071722;color:#e6edf3;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;overflow:auto}.deferred-evidence{cursor:pointer;min-height:110px;display:flex;align-items:center;color:#c9d7e3}.deferred-evidence:focus-visible{outline:3px solid #428bff;outline-offset:3px}@media(max-width:760px){.method-comparison{padding:18px}.comparison-heading{display:block}.comparison-count{display:inline-block;margin-top:12px}.comparison-controls{grid-template-columns:1fr}.large-evidence-output{min-height:360px}.comparison-table{min-width:660px}}
'''

EXTRA+='''
.wmb-controls{display:grid;grid-template-columns:repeat(4,minmax(160px,1fr));gap:12px;margin:18px 0}.wmb-controls label{display:grid;gap:5px;color:var(--muted);font-size:13px;font-weight:700}.wmb-controls select,.wmb-controls input{width:100%;min-width:0;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink);font:inherit}.wmb-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.wmb-toolbar button{border:1px solid var(--line);background:var(--panel);border-radius:9px;padding:8px 12px;color:var(--ink);cursor:pointer}.wmb-scroll-note{margin:8px 0;color:var(--muted);font-size:13px}.wmb-table-wrap{max-height:72vh;overflow:auto;scrollbar-gutter:stable}.wmb-table{min-width:1580px}.wmb-table th,.wmb-table td{white-space:nowrap}.wmb-table td:nth-child(2){min-width:250px;max-width:250px;overflow:hidden;text-overflow:ellipsis}.wmb-table td:nth-child(3){min-width:125px}.wmb-table td:last-child{min-width:105px}.wmb-table a{font-weight:700}.wmb-metric-note{margin:0 0 16px}.wmb-attempt-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.wmb-attempt-summary .card{grid-column:auto}.wmb-attempt-summary .card b{display:block;font-size:12px;color:var(--muted)}.wmb-attempt-summary .card strong{display:block;font-size:20px}.wmb-diagnostic{font-size:13px;color:var(--muted)}.wmb-evidence pre{max-height:65vh}.wmb-evidence .evidence-pane{min-width:0}.wmb-evidence .evidence-pane .pane-body{padding:14px}.wmb-pill{display:inline-block;padding:2px 8px;border-radius:99px;background:var(--panel2);font-size:12px}.wmb-pill.failure{background:var(--failure-bg);color:var(--bad)}.wmb-pill.success{background:var(--success-bg);color:var(--good)}@media(max-width:950px){.wmb-controls{grid-template-columns:repeat(2,minmax(0,1fr))}.wmb-attempt-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:700px){.wmb-controls,.wmb-attempt-summary,.wmb-evidence{grid-template-columns:1fr}.wmb-table-wrap{max-height:65vh}.wmb-table{min-width:1450px}}
.wmb-table th:first-child,.wmb-table td:first-child{position:sticky;left:0;z-index:2;min-width:58px;background:var(--panel)}.wmb-table th:nth-child(2),.wmb-table td:nth-child(2){position:sticky;left:58px;z-index:2;min-width:320px;max-width:none;overflow:visible;text-overflow:clip;background:var(--panel)}.wmb-table th:first-child,.wmb-table th:nth-child(2){z-index:4}
.wmb-controls{scroll-margin-top:76px}
.wmb-source-pre{max-height:500px;overflow:auto;white-space:pre;scrollbar-gutter:stable}.wmb-attempt-summary{grid-template-columns:repeat(3,minmax(0,1fr));margin:16px 0}.wmb-evidence{align-items:start}.wmb-evidence .evidence-pane{align-self:start}.wmb-evidence pre{height:430px;max-height:50vh;overflow:auto;white-space:pre}.wmb-provenance{font-size:13px;color:var(--muted)}.wmb-provenance code{overflow-wrap:anywhere}.wmb-attempt-summary strong{overflow-wrap:anywhere}@media(max-width:700px){.wmb-attempt-summary{grid-template-columns:1fr}.wmb-evidence pre{height:300px}}
@media(max-width:700px){.wmb-controls{scroll-margin-top:155px}.wmb-table th:first-child,.wmb-table td:first-child{min-width:40px}.wmb-table th:nth-child(2),.wmb-table td:nth-child(2){left:40px;min-width:310px}}
@media(max-width:700px){.wmb-table td:nth-child(2) a{display:inline-block;max-width:278px;white-space:normal;overflow-wrap:anywhere}}
.wmb-comparison-table{min-width:860px}.wmb-comparison-table th:nth-child(2),.wmb-comparison-table td:nth-child(2){min-width:170px}.wmb-comparison-table th:nth-child(3),.wmb-comparison-table td:nth-child(3){min-width:220px}.wmb-comparison-table th:nth-child(4),.wmb-comparison-table td:nth-child(4),.wmb-comparison-table th:nth-child(5),.wmb-comparison-table td:nth-child(5){min-width:245px}.wmb-comparison-table .outcome-pill{margin:3px}.method-comparison .table-wrap{max-height:70vh;overflow:auto}.wmb-comparison-table thead th{position:sticky;top:0;z-index:2}.wmb-guide-nav{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.wmb-guide-nav a{padding:8px 12px;border:1px solid var(--line);border-radius:999px;text-decoration:none;background:var(--panel)}.wmb-guide-lead{max-width:900px;font-size:18px;line-height:1.75}.wmb-comparison-table .outcome-list{display:flex;flex-wrap:wrap}
.method-comparison .comparison-controls{grid-template-columns:repeat(4,minmax(145px,1fr))}@media(max-width:760px){.method-comparison .comparison-controls{grid-template-columns:1fr}}
.wide-prose-table{min-width:1600px;table-layout:fixed}.wide-prose-table th,.wide-prose-table td{vertical-align:top;overflow-wrap:anywhere;line-height:1.6}.wide-prose-table th:nth-child(2),.wide-prose-table td:nth-child(2){width:190px}.wide-prose-table th:nth-child(3),.wide-prose-table td:nth-child(3){width:230px}.wide-prose-table th:nth-child(4),.wide-prose-table td:nth-child(4){width:280px}.wide-prose-table th:nth-child(5),.wide-prose-table td:nth-child(5){width:250px}.wide-prose-table th:nth-child(6),.wide-prose-table td:nth-child(6){width:280px}.wide-prose-table th:nth-child(7),.wide-prose-table td:nth-child(7){width:370px}.wide-prose-table a{display:inline-block;max-width:100%}.wide-prose-table+.source-note{margin-top:8px}.wmb-slice-controls{display:grid;grid-template-columns:repeat(3,minmax(190px,1fr));gap:12px;margin:18px 0}.wmb-slice-controls label{display:grid;gap:6px;font-weight:700;color:var(--muted)}.wmb-slice-controls select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink);font:inherit}.wmb-slice-table{min-width:1320px;white-space:nowrap}.wmb-slice-table-wrap{max-height:70vh;overflow:auto}.wmb-slice-table th{position:sticky;top:0;background:var(--panel2);z-index:2}@media(max-width:700px){.wmb-slice-controls{grid-template-columns:1fr}}
.wmb-guide-example-table{min-width:1100px;white-space:nowrap}
.wide-prose-table{min-width:1720px}
.wmb-failure-detail-table{min-width:1160px;table-layout:fixed}.wmb-failure-detail-table th:nth-child(1),.wmb-failure-detail-table td:nth-child(1){width:140px}.wmb-failure-detail-table th:nth-child(2),.wmb-failure-detail-table td:nth-child(2){width:350px}.wmb-failure-detail-table th:nth-child(3),.wmb-failure-detail-table td:nth-child(3){width:250px}.wmb-failure-detail-table td{vertical-align:top;overflow-wrap:anywhere}
.metric-value{display:block}.tiny{display:block;color:var(--muted);font-size:11px;font-weight:500;margin-top:3px}.wmb-core-table{min-width:1280px;white-space:nowrap}.wmb-distribution-controls{display:grid;grid-template-columns:minmax(220px,360px) 1fr;gap:16px;align-items:end;margin:18px 0}.wmb-distribution-controls label{display:grid;gap:6px;font-weight:700;color:var(--muted)}.wmb-distribution-controls select{padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink);font:inherit}.wmb-flow{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:18px}.wmb-flow .mermaid{min-width:980px}.wmb-flow-fallback{display:flex;gap:8px;align-items:center;min-width:900px}.wmb-flow-fallback span{padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel2)}.wmb-flow-fallback b{color:var(--accent)}@media(max-width:700px){.wmb-distribution-controls{grid-template-columns:1fr}.wmb-flow{padding:12px}}
'''

def esc(s):return html.escape(str(s if s is not None else ''))
def readable_output(s):
 return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',lambda m:'\\x'+format(ord(m[0]),'02x'),s)

_PRIVATE_LOG_KEYS={'raw_response_path','private_artifacts','private_artifact','private_path'}
def public_provider_metadata(value):
 """Remove pointers to private envelopes while keeping public adapter evidence."""
 if isinstance(value,dict):
  return {key:public_provider_metadata(item) for key,item in value.items()
          if key not in _PRIVATE_LOG_KEYS and not key.startswith('private_')}
 if isinstance(value,list):return [public_provider_metadata(item) for item in value]
 return value

def call_log_bindings(record):
 metadata=record.get('provider_metadata') or {}
 candidates=[]
 retry=metadata.get('retry') or {}
 if isinstance(retry,dict):
  for index,attempt in enumerate(retry.get('attempts',[]),1):
   if isinstance(attempt,dict):candidates.append((f'物理调用 {attempt.get("physical_attempt",index)}',attempt.get('call_log')))
 candidates.extend([('最终物理调用',record.get('call_log')),('最终物理调用',metadata.get('call_log')),
                    ('最终物理调用',{'path':record.get('call_log_path'),'sha256':record.get('call_log_sha256')}),
                    ('最终物理调用',{'path':metadata.get('call_log_path'),'sha256':metadata.get('call_log_sha256')})])
 result=[];seen=set()
 for label,binding in candidates:
  if not isinstance(binding,dict):continue
  path=binding.get('path')
  if not path or str(path).startswith('private_artifacts/') or str(path) in seen:continue
  seen.add(str(path));result.append((label,dict(binding)))
 return result

def call_log_path(record):
 bindings=call_log_bindings(record)
 return bindings[-1][1]['path'] if bindings else None

def empty_output_reason(record,case,output):
 if output:return ''
 metadata=record.get('provider_metadata') or {}
 delivered=metadata.get('file_evaluation',{}).get('files',[])
 if case.get('task_kind')=='file' and delivered:
  return f'本题的最终交付物是 {len(delivered)} 个原始文件；文本输出为空不表示没有交付，文件字节在详情页下方单独列出。'
 if record.get('error_message'):
  return f'最终输出确实为空。适配器记录的原因：{record["error_message"]}'
 if not record.get('ok'):
  error_type=record.get('error_type') or '未记录错误类型'
  return f'最终输出确实为空。请求未成功，错误类型为 {error_type}；历史记录没有保存更多错误文字。'
 if metadata.get('empty_content'):
  return '最终输出确实为空。上游请求有响应，但适配器没有得到可提取的正文。'
 return '最终输出确实为空；本次历史记录没有保存更具体的原因，不能据此推断是服务端、网络还是适配器问题。'

def call_log_dialog(out,record,case,output):
 """Render one honest drill-down view from a native log or legacy result fields."""
 metadata=record.get('provider_metadata') or {}
 public_metadata=public_provider_metadata(metadata)
 logs=[]
 for label,binding in call_log_bindings(record):
  path=contained_path(out,binding['path'])
  if not path.is_file():continue
  log_bytes=path.read_bytes();observed=hashlib.sha256(log_bytes).hexdigest()
  if binding.get('sha256') and binding['sha256']!=observed:raise ValueError(f'Call log hash mismatch: {record["attempt_id"]}')
  if binding.get('bytes') is not None and binding['bytes']!=len(log_bytes):raise ValueError(f'Call log size mismatch: {record["attempt_id"]}')
  text=readable_output(log_bytes.decode('utf-8',errors='replace'))
  try:payload=json.loads(log_bytes)
  except (UnicodeDecodeError,json.JSONDecodeError):payload={}
  logs.append({'label':label,'path':binding['path'],'text':text,'sha256':observed,
               'bound':bool(binding.get('sha256')),'payload':payload})
 if logs:
  native=(f'{len(logs)} 个物理调用日志已保存并通过 SHA-256 校验。' if all(item['bound'] for item in logs) else
          '事件日志文件可以读取，但至少一条记录未声明其 SHA-256；页面会展示内容，但不能证明全部日志均与运行记录绑定。')
 elif call_log_bindings(record):
  native='记录声明了事件日志，但公开证据中没有可读取文件；下面仅展示可核验的结果字段。'
 else:
  native='这次历史运行没有保存统一事件日志。下面由最终输出、结果字段和公开适配器元数据合成，不能还原未记录的逐行过程。'
 final_log=logs[-1]['payload'] if logs else {}
 log_error=final_log.get('error') if isinstance(final_log.get('error'),dict) else {}
 log_process=final_log.get('process') if isinstance(final_log.get('process'),dict) else {}
 error_type=record.get('error_type') or log_error.get('type') or '—（未记录）'
 error_message=(record.get('error_message') or log_error.get('message') or log_process.get('stderr') or
                ('无报错；请求记录为成功。' if record.get('ok') else '请求失败，但历史记录未保存错误文字。'))
 error_class=' call-log-error' if record.get('error_type') or record.get('error_message') or not record.get('ok') else ''
 if output:
  output_html=f'<pre data-kind="call-output">{esc(readable_output(output))}</pre>'
 else:
  output_html=f'<div class="call-log-empty">{esc(empty_output_reason(record,case,output))}</div>'
 log_request=final_log.get('request') if isinstance(final_log.get('request'),dict) else {}
 route=(log_request.get('route') or metadata.get('route') or metadata.get('extractor') or
        metadata.get('output_contract') or '—（未记录）')
 status=('成功' if record.get('ok') else '失败')+' · HTTP '+(str(record.get('status_code')) if record.get('status_code') is not None else '未提供')
 retry=metadata.get('retry')
 retry_count=(len(retry.get('attempts',[])) if isinstance(retry,dict) else
              record.get('physical_attempt_count') or 1)
 log_section=('' if not logs else '<div class="call-log-section"><h3>原生事件日志</h3>'+''.join(
              f'<details open><summary>{esc(item["label"])}</summary><p class="source-note">SHA-256：<code class="break">{esc(item["sha256"])}</code></p><pre data-kind="call-log">{esc(item["text"])}</pre></details>'
              for item in logs)+'</div>')
 retry_section=('<p>本次没有登记重试；只保留一个物理调用。</p>' if not retry else
                f'<pre data-kind="call-retry">{esc(json.dumps(public_provider_metadata(retry),ensure_ascii=False,indent=2))}</pre>')
 private_note=('原始供应商响应信封仅在内部证据中保留，不进入公开弹窗或发布包。'
               if metadata.get('raw_response_path') or metadata.get('private_artifacts') else
               '没有登记仅限内部保存的原始响应信封。')
 facts=[('请求路线',route),('请求状态',status),('总耗时',ms(record.get('elapsed_ms'))),
        ('开始时间',record.get('started_at') or '—（未记录）'),('完成时间',record.get('completed_at') or '—（未记录）'),
        ('物理调用',str(retry_count))]
 fact_html=''.join(f'<div class="call-log-fact"><b>{esc(label)}</b><span>{esc(value)}</span></div>' for label,value in facts)
 return (f'<dialog class="call-log-dialog" id="call-log-dialog" aria-labelledby="call-log-title"><div class="call-log-shell">'
         f'<header class="call-log-head"><div><h2 id="call-log-title">调用记录</h2><p><code>{esc(record.get("run_id") or execution_id(record))}</code></p></div><button type="button" class="call-log-close" data-call-log-close data-i18n-attrs aria-label="关闭调用记录">关闭</button></header>'
         f'<div class="call-log-body"><p class="callout call-log-availability"><b>日志可用性：</b>{esc(native)}</p>'
         f'<div class="call-log-priority"><article class="call-log-card"><h3>最后输出</h3>{output_html}</article>'
         f'<article class="call-log-card{error_class}"><h3>最后报错</h3><p><b>{esc(error_type)}</b></p><p>{esc(error_message)}</p></article></div>'
         f'<div class="call-log-facts">{fact_html}</div>{log_section}'
         f'<div class="call-log-section"><h3>重试与物理调用</h3>{retry_section}</div>'
         f'<div class="call-log-section"><details><summary>查看公开适配器字段</summary><pre data-kind="call-metadata">{esc(json.dumps(public_metadata,ensure_ascii=False,indent=2))}</pre></details><p class="call-log-private">{esc(private_note)}</p></div>'
         '</div></div></dialog>')
def pct(n):return '—' if n is None else f'{n*100:.1f}%'
def ms(n):return '—' if n is None else f'{n:,.0f} ms'
def link(url,label):return f'<a href="{esc(safe_href(url))}">{esc(label)}</a>'
def cell(value,sort=None):return f'<td'+(f' data-sort="{sort}"' if sort is not None else '')+f'>{value}</td>'
def rate(a,b):return pct(a/b) if b else '—'
def section(title,body,id=''):return f'<section class="section"'+(f' id="{id}"' if id else '')+f'><div class="wrap"><h2>{title}</h2>{body}</div></section>'
def hero(title,desc):return f'<section class="hero"><div class="wrap"><div class="eyebrow">Web Fetch · 实测报告</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'
def table(headers,rows,id='',sortable=False,cls='',metrics=None,index_header='序号',default_sort=None):
 head=f'<th>{esc(index_header)}</th>'+''.join(f'<th><button class="sort-button" onclick="sortTable(\'{id}\',{i+1})">{h} ↕</button></th>' if sortable else f'<th>{h}</th>' for i,h in enumerate(headers))
 body=''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))
 sort_attrs=(f' data-col="{default_sort[0]}" data-dir="{default_sort[1]}"' if default_sort else '')
 return metric_legend(headers if metrics is None else metrics)+f'<div class="table-wrap"><table class="{cls}"'+(f' id="{id}"' if id else '')+sort_attrs+f'><thead><tr>{head}</tr></thead><tbody'+(' id="rows"' if id=='filtered-table' else '')+f'>{body}</tbody></table></div>'
EXTRA+='\n.change-log section{padding:24px 0}.change-log h2{font-size:24px}.change-log p{margin:10px 0;line-height:1.75}.change-log summary{font-weight:650;color:var(--accent)}.change-log .table-wrap{overflow-x:auto}\n'

_RELIABILITY_BANNER=''

def write(out,path,title,body):
 depth=len(Path(path).parts)-1;base='../'*depth
 nav=[('datasets/index.html','评测集'),('methodology/index.html','评测方法'),('analysis/index.html','结果分析'),('review/index.html','核验记录'),('guide/index.html','报告解读'),('adoption/index.html','方法介绍'),('reproduce/index.html','环境与复现'),('changes/index.html','变更记录'),('accounts/index.html','账号与安装')]
 brand=link(base+'index.html','Web Fetch')
 counterpart=base+'en/'+path
 language=f'<a href="{safe_href(counterpart)}" hreflang="en">English</a>'
 footer='所有分数由本轮调用记录计算。网页与服务可能变化；结论仅适用于本轮样本和配置。'
 page=f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}'+''.join(link(base+url,label) for url,label in nav)+f'{language}<button class="theme-toggle" onclick="theme()">切换主题</button></nav></header><main>{_RELIABILITY_BANNER}{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'
 target=contained_path(out,path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

_RESEARCH_ROUTES=[
 ('datasets/index.html','评测集','Datasets'),
 ('methodology/index.html','评测方法','Methodology'),
 ('analysis/index.html','调研结论','Research findings'),
 ('review/index.html','核验记录','Verification'),
 ('guide/index.html','报告解读','Guide'),
 ('adoption/index.html','采用情况','Adoption'),
 ('reproduce/index.html','环境与复现','Reproduction'),
 ('changes/index.html','变更记录','Changes'),
 ('accounts/index.html','账号与安装','Accounts and setup'),
]

def _research_links(urls):
 return '<br>'.join(link(url,url.replace('https://','')) for url in urls)

def _research_hero(title,desc,english=False):
 eyebrow='Web Fetch · Research report' if english else 'Web Fetch · 调研报告'
 return f'<section class="hero"><div class="wrap"><div class="eyebrow">{eyebrow}</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'

def _research_write(out,path,title,body,english=False):
 local_path=('en/'+path) if english else path
 depth=len(Path(local_path).parts)-1;base='../'*depth
 nav=''.join(link(base+('en/' if english else '')+url,en if english else zh) for url,zh,en in _RESEARCH_ROUTES)
 brand=link(base+('en/index.html' if english else 'index.html'),'Web Fetch')
 counterpart=base+path if english else base+'en/'+path
 language=f'<a href="{safe_href(counterpart)}" hreflang="{"zh-CN" if english else "en"}">{"中文" if english else "English"}</a>'
 status=('Research edition · 0 measured results' if english else '调研版 · 0 条实测结果')
 footer=('Research was verified from public papers, repositories, datasets and scorers. No benchmark result is claimed in this edition.' if english else '调研依据为公开论文、仓库、数据和评分器；本版不声称任何实测成绩。')
 page=f'<!doctype html><html lang="{"en" if english else "zh-CN"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{nav}{language}<button class="theme-toggle" onclick="theme()">{"Theme" if english else "切换主题"}</button></nav></header><main><div class="wrap reliability" role="status"><strong>{status}</strong></div>{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'
 target=contained_path(out,local_path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

def generate_public_benchmark_research(out:Path,research:dict):
 """Render the research-only public benchmark report with no measured-result paths."""
 if research.get('status')!='research_only' or research.get('measured_results')!=0:
  raise ValueError('Public benchmark research must explicitly contain zero measured results')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA,encoding='utf-8')
 (out/'assets/report.js').write_text(JS,encoding='utf-8')
 direct=research['direct_benchmarks'];adjacent=research['adjacent_browser_benchmarks']
 for english in (False,True):
  def h(zh,en):return en if english else zh
  def field(row,key):return row.get(key) if english else row.get(key+'_zh',row.get(key))
  def research_table(headers,rows,**kwargs):return table(headers,rows,index_header=h('序号','No.'),**kwargs)
  artifact_prefix='../../' if english else '../'
  home=_research_hero(h('公开 Web Fetch 评测集调研','Public Web Fetch benchmark research'),h('已核对公开数据、官方评分器和采用证据；当前只定义下一轮实测边界。','Public data, official scorers and adoption evidence are verified; this edition only defines the next measurement boundary.'),english)
  cards=''.join(f'<article class="card"><h3>{esc(x["name"])}</h3><p><b>{x["pages"]:,}</b> {h("页","pages")}</p><p>{esc(field(x,"scope"))}</p></article>' for x in direct)
  qualification=research['decision']['qualification_en' if english else 'qualification_zh']
  home+=section(h('结论','Decision'),f'<p class="callout success"><b>{h("选定：","Selected: ")}</b>{esc(research["decision"]["summary_en" if english else "summary_zh"])}</p><p>{esc(qualification)}</p>')
  home+=section(h('候选基准','Candidate benchmarks'),'<div class="grid">'+cards+'</div>')
  home+=section(h('当前状态','Current status'),f'<div class="review-summary"><article class="card"><h3>0</h3><p>{h("实测结果", "measured results")}</p></article><article class="card"><h3>{len(direct)}</h3><p>{h("直接赛道","direct tracks")}</p></article><article class="card"><h3>{len(adjacent)}</h3><p>{h("相关浏览器基准","adjacent browser benchmarks")}</p></article></div>')
  _research_write(out,'index.html',h('公开评测集调研','Public benchmark research'),home,english)

  rows=[]
  for x in direct:
   rows.append(('',cell(esc(x['name']))+cell(f'{x["pages"]:,}')+cell(esc(field(x,'metric')))+cell(esc(field(x,'aggregation')))+cell(esc(field(x,'maturity')))+cell(_research_links(x['sources']))))
  datasets=_research_hero(h('评测集','Datasets'),h('只纳入能直接衡量网页主内容或 Markdown 转换的公开赛道。','Only public tracks that directly measure page main-content or Markdown conversion are included.'),english)
  datasets+=section(h('主赛道','Primary tracks'),research_table([h('名称','Name'),h('页数','Pages'),h('官方指标','Official metric'),h('聚合','Aggregation'),h('成熟度','Maturity'),h('一手来源','Sources')],rows,metrics=[]))
  decision_rows=[('',cell(esc(x['name']))+cell(esc(field(x,'disposition')))+cell(esc(x['license']))) for x in direct]
  datasets+=section(h('选型结果','Selection result'),research_table([h('候选','Candidate'),h('处理','Disposition'),h('许可证','License')],decision_rows,metrics=[]))
  subsets=next(x for x in direct if x['id']=='wceb')['subsets']
  datasets+=section('WCEB '+h('组成','composition'),research_table([h('子集','Subset'),h('页数','Pages')],[('',cell(esc(name))+cell(f'{count:,}')) for name,count in subsets],metrics=[])+f'<p>{h("合计","Total")}: <b>{sum(x[1] for x in subsets):,}</b></p>')
  wcxb=next(x for x in direct if x['id']=='wcxb')
  datasets+=section('WCXB '+h('分层','stratification'),f'<p>{h("开发集 1,497 页，测试集 511 页；两个 split 的 HTML 和 ground truth 均已核对存在。","The development split has 1,497 pages and the test split 511; HTML and ground truth were verified present for both.")}</p><p>{h("页面类型：","Page types: ")}'+esc(', '.join(wcxb['page_types']))+'</p>')
  frozen=research.get('frozen_sample')
  if frozen:
   distribution_rows=[]
   for axis in frozen['sampling']['official_axes']:
    parent_dist=frozen['parent_distribution'][axis];sample_dist=frozen['sample_distribution'][axis]
    for category in sorted(set(parent_dist)|set(sample_dist)):
     distribution_rows.append(('',cell(esc(axis))+cell(esc(category))+cell(str(parent_dist.get(category,0)))+cell(str(sample_dist.get(category,0)))))
   datasets+=section(h('已冻结的 200 例分布','Frozen 200-case distribution'),f'<p>{h("母集与样本分类计数如下；稀有语言只有 1–2 例，不应据此下语言能力结论。","Parent and sample counts follow. Rare languages have only one or two cases and do not support language-capability claims.")} {link(artifact_prefix+"sample-manifest.json",h("完整清单 JSON →","Full manifest JSON →"))}</p>'+research_table([h('轴','Axis'),h('分类','Category'),h('母集 545','Parent 545'),h('样本 200','Sample 200')],distribution_rows,metrics=[]))
  embedded=''.join(f'<article class="guide-card"><h3>{esc(x["name"])}</h3><p>{esc(field(x,"disposition"))}</p><p>{_research_links(x["sources"])}</p></article>' for x in research['embedded_or_supplementary'])
  datasets+=section(h('嵌入集与补充集','Embedded and supplementary sets'),'<div class="guide-grid">'+embedded+'</div>')
  _research_write(out,'datasets/index.html',h('评测集','Datasets'),datasets,english)

  method=_research_hero(h('评测方法','Methodology'),h('尊重每个公开基准的原始数据、预处理、评分器和分母。','Preserve each benchmark\'s original data, preprocessing, scorer and denominator.'),english)
  contract=research['next_run_contract' if english else 'next_run_contract_zh']
  rules=''.join(f'<li><b>{i+1}</b><span>{esc(rule)}</span></li>' for i,rule in enumerate(contract))
  method+=section(h('冻结合同','Frozen contract'),'<ol class="quality-steps">'+rules+'</ol>')
  sampling=research['sampling_contract']
  sample_text=(f'{sampling["size"]} · {esc(sampling["seed"])} · '+esc(sampling['reason' if english else 'reason_zh']))
  method+=section(h('200 例抽样合同','200-case sampling contract'),f'<p><b>{sample_text}</b></p><p>{h("官方分层轴：","Official axes: ")}<code>{esc(", ".join(sampling["official_axes"]))}</code></p><p>{h("算法：","Algorithm: ")}{esc(sampling["algorithm"])}</p>')
  no_combined=h('公开 200 例使用 WebMainBench-545 的官方分项；现有 150 例继续回答真实访问、资源、动态渲染与时延。两套结果并列，不做重标度综合分。',"The public 200 cases use WebMainBench-545's official components; the current 150 cases continue to measure live access, resources, dynamic rendering and latency. Show both without a rescaled combined score.")
  method+=section(h('不可混分','No synthetic combined score'),'<p>'+no_combined+'</p>')
  _research_write(out,'methodology/index.html',h('评测方法','Methodology'),method,english)

  analysis=_research_hero(h('调研结论','Research findings'),h('有直接公开基准，但没有一个基准同时覆盖“现场访问成功”与“抽取内容质量”。','Direct public benchmarks exist, but none jointly isolate live access success and extraction quality.'),english)
  analysis+=section(h('最小完整方案','Smallest complete design'),f'<p>{esc(research["decision"]["summary_en" if english else "summary_zh"])}</p><p>{h("公开集提供冻结 HTML，所以回答“同一输入下抽取和 Markdown 转换怎样”；现有 150 例继续回答真实 URL 到结果的端到端能力。","The public set freezes HTML and answers extraction/Markdown fidelity on identical input; the current 150 cases continue to answer end-to-end URL-to-output capability.")}</p>')
  limitations='<ul>'+''.join('<li>'+esc(x)+'</li>' for x in wcxb['limitations' if english else 'limitations_zh'])+'</ul>'
  analysis+=section('WCXB '+h('实查与处理','audit and disposition'),f'<p>{esc(field(wcxb,"disposition"))}</p>'+limitations)
  current=research['current_benchmark']
  track_text=' · '.join(f'{name} {count}' for name,count in current['tracks'].items())
  analysis+=section(h('与现有 150 例的关系','Relationship to the current 150 cases'),f'<p><b>{esc(track_text)}</b></p><p>{esc(field(current,"measures"))}</p><p>{esc(field(current,"difference"))}</p>')
  fusion_rows=[('',cell(esc(field(x,'layer')))+cell(esc(field(x,'keep')))) for x in research['fusion_plan']]
  analysis+=section(h('融合的是流程与诊断，不是总分','Fuse process and diagnostics, not scores'),research_table([h('层','Layer'),h('采用方案','Decision')],fusion_rows,metrics=[]))
  analysis+=section(h('不纳入主榜的工作','What stays outside the main leaderboard'),f'<p>{h("WebArena、Mind2Web、WebVoyager 和 BrowseComp 都是重要的浏览器/搜索基准，但它们测行为决策、任务完成或研究问答，不能把差异归因到 Web Fetch 层。","WebArena, Mind2Web, WebVoyager and BrowseComp matter for browser/search systems, but score actions, task completion or research QA and cannot attribute differences to the fetch layer.")}</p>')
  _research_write(out,'analysis/index.html',h('调研结论','Research findings'),analysis,english)

  review=_research_hero(h('核验记录','Verification'),h('这是来源核验，不是结果复核。','This verifies sources, not benchmark results.'),english)
  audit=next(x for x in direct if x['id']=='wceb')['download_audit']
  review+=section(h('已核对','Verified'),f'<p>{h("核对日期","Checked")}: {esc(research["verified_at"])}</p><p>WCEB <code>{esc(audit["artifact"])}</code>: {audit["bytes"]:,} bytes · SHA-256 <code class="break">{audit["sha256"]}</code> · {audit["observed_pages"]:,} {h("页","pages")}</p><p>BrowseComp: 1,266 {h("数据行（不含表头）","data rows excluding the header")}</p>')
  repo_audit=wcxb['repository_audit']
  review+=section('WCXB '+h('全量仓库实查','full repository audit'),f'<p>dev: {repo_audit["development_html"]:,} HTML / {repo_audit["development_ground_truth"]:,} ground truth · test: {repo_audit["test_html"]:,} HTML / {repo_audit["test_ground_truth"]:,} ground truth</p><p>{h("空正文","Empty main content")}: {repo_audit["empty_main_content"]} · with mismatch: {esc(repo_audit["with_snippets_not_in_main_content"])} · without mismatch: {esc(repo_audit["without_snippets_present_in_main_content"])} · test result rows: {repo_audit["published_test_result_rows"]}/{repo_audit["official_test_rows"]}</p>')
  selected=next(x for x in direct if x['id']=='webmainbench-545')
  scorer_items=''.join('<li>'+esc(item)+'</li>' for item in selected['scorer_audit' if english else 'scorer_audit_zh'])
  review+=section('WebMainBench-545 '+h('评分器实查','scorer audit'),'<ul>'+scorer_items+'</ul>')
  if frozen:
   qa=frozen['quality_audit']
   review+=section('WebMainBench-545 '+h('发布文件全量实查','released-file full audit'),f'<p>{frozen["parent"]["bytes"]:,} bytes · SHA-256 <code class="break">{esc(frozen["parent"]["sha256"])}</code> · {frozen["case_count"]} {h("例已冻结","cases frozen")}</p><p>groundtruth_content empty: {qa["empty_groundtruth_content"]}/545 · HTML empty: {qa["empty_html"]}/545 · main_html empty: {qa["empty_main_html"]}/545 · convert_main_content empty: {qa["empty_convert_main_content"]}/545 · style non-null: {qa["non_null_style"]}/545 · language non-null: {qa["non_null_language"]}/545</p>')
  review+=section(h('未开始','Not started'),f'<p class="callout">{h("实测结果、排名、分数和独立结果审查均为 0。","Measured results, rankings, scores and independent result reviews are all zero.")}</p>')
  _research_write(out,'review/index.html',h('核验记录','Verification'),review,english)

  guide=_research_hero(h('报告解读','Guide'),h('这一版回答“该测什么”，还不回答“谁更好”。','This edition answers what to test, not which method wins.'),english)
  guide+=section(h('阅读顺序','Reading order'),f'<div class="guide-grid"><article class="guide-card"><h3>1 · {h("选型","Selection")}</h3><p>{h("看为什么唯一选择 WebMainBench-545。","See why WebMainBench-545 is the sole selection.")}</p></article><article class="guide-card"><h3>2 · {h("抽样","Sampling")}</h3><p>{h("看 200 例如何覆盖官方分类。","See how 200 cases cover official categories.")}</p></article><article class="guide-card"><h3>3 · {h("采用","Adoption")}</h3><p>{h("区分真实使用、集成和仅提及。","Separate execution, integration and mere mentions.")}</p></article><article class="guide-card"><h3>4 · {h("实测","Measurement")}</h3><p>{h("完整分母和复核通过后才出排名。","Rank only after complete denominators and review.")}</p></article></div>')
  _research_write(out,'guide/index.html',h('报告解读','Guide'),guide,english)

  adoption=_research_hero(h('采用情况','Adoption'),h('用论文、实际评测仓库和基准框架证明采用；Star 只做背景。','Adoption is evidenced by papers, executable benchmark repositories and framework integrations; stars are context only.'),english)
  adoption+=section(h('证据边界','Evidence boundary'),f'<p>{h("已核对到论文运行、开源项目适配和可执行评测仓库；没有找到可独立核验的商业生产部署数量，因此报告不把“支持某抽取器”或搜索命中写成商业采用。","Verified evidence covers paper runs, open-source adapters and executable benchmark repositories. No independently auditable commercial production-deployment count was found, so extractor support and search hits are not presented as commercial adoption.")}</p>')
  adoption_rows=[]
  for x in direct:
   adoption_rows.append(('',cell(esc(x['name']))+cell('<br>'.join(esc(u) for u in x['users']))+cell(esc(field(x,'maturity')))+cell(_research_links(x['sources']))))
  adoption+=section(h('直接基准','Direct benchmarks'),research_table([h('基准','Benchmark'),h('已核对使用者','Verified users'),h('判断','Assessment'),h('证据','Evidence')],adoption_rows,metrics=[]))
  browser_rows=[('',cell(esc(x['name']))+cell(esc(field(x,'size')))+cell(esc(field(x,'measures')))+cell(esc(field(x,'adoption')))+cell(esc(field(x,'why_separate')))+cell(_research_links(x['sources']))) for x in adjacent]
  adoption+=section(h('浏览器与搜索基准','Browser and search benchmarks'),research_table([h('名称','Name'),h('规模','Scale'),h('测量目标','Measures'),h('采用','Adoption'),h('为何分开','Why separate'),h('来源','Sources')],browser_rows,metrics=[]))
  stars=[('',cell(esc(name))+cell(str(stars))+cell(str(forks))) for name,stars,forks in research['adoption_snapshot']['repositories']]
  warning=research['adoption_snapshot']['warning' if english else 'warning_zh']
  adoption+=section(h('GitHub 快照','GitHub snapshot'),f'<p class="source-note">{esc(warning)}</p>'+research_table([h('仓库','Repository'),'Stars','Forks'],stars,metrics=[]))
  _research_write(out,'adoption/index.html',h('采用情况','Adoption'),adoption,english)

  reproduce=_research_hero(h('环境与复现','Reproduction'),h('本版只复现调研报告，不触发供应商调用。','This edition only reproduces the research report and makes no provider calls.'),english)
  reproduce+=section(h('构建','Build'),'<pre>make public-benchmark-research</pre>')
  reproduce+=section(h('冻结 200 例清单','Freeze the 200-case manifest'),'<pre>make public-benchmark-sample SOURCE=/absolute/path/to/WebMainBench_545.jsonl</pre>')
  if frozen: reproduce+=section(h('冻结产物','Frozen artifact'),f'<p>{link(artifact_prefix+"sample-manifest.json",h("下载 200 例清单 JSON →","Download the 200-case manifest JSON →"))}</p>')
  reproduce+=section(h('下一轮需冻结','Freeze before measurement'),f'<p>{h("为每条赛道记录数据修订、评分器修订、环境、许可证和完整分母；供应商输出仍存 Git 外。","Record dataset and scorer revisions, environment, licenses and the complete denominator for each track; provider outputs remain outside Git.")}</p>')
  _research_write(out,'reproduce/index.html',h('环境与复现','Reproduction'),reproduce,english)

  changes=_research_hero(h('变更记录','Changes'),h('只记录读者可见的范围与口径。','Reader-visible scope and methodology changes only.'),english)
  changes+=section('2026-09-14',f'<p>{h("完成候选实查并选定 WebMainBench-545；定义覆盖官方分类的固定 200 例抽样合同；补充 WCXB 全量标准答案/评分器问题、采用证据和与现有 150 例的分层融合方案。未加入供应商实测结果。","Completed candidate audits and selected WebMainBench-545; defined a fixed 200-case official-category coverage contract; added WCXB ground-truth/scorer findings, adoption evidence and a layered integration with the current 150 cases. No provider result was added.")}</p>')
  _research_write(out,'changes/index.html',h('变更记录','Changes'),changes,english)

  accounts=_research_hero(h('账号与安装','Accounts and setup'),h('调研版不需要供应商账号。','The research edition needs no provider account.'),english)
  accounts+=section(h('当前要求','Current requirement'),f'<p class="callout success">{h("只读取已提交的调研与样本清单 JSON；不读取 .env、Token、Cookie 或个人登录态。","Only the committed research and sample-manifest JSON files are read; no .env, token, cookie or personal login state is used.")}</p>')
  accounts+=section(h('实测阶段','Measurement phase'),f'<p>{h("届时按方法单独列出官方安装入口、版本和所需环境变量名，不公开值。","The measured edition will list official installation sources, versions and required environment-variable names per method, never their values.")}</p>')
  _research_write(out,'accounts/index.html',h('账号与安装','Accounts and setup'),accounts,english)
 (out/'research.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return out/'index.html'

def succeeded(r,c):
 return task_passed(r['ok'],r['metrics'],c.get('task_kind','body'))

def measured(r,c):
 return task_measured(r['metrics'],c.get('task_kind','body'))

def result_label(r,c):
 if not measured(r,c):return '未测'
 state='通过' if succeeded(r,c) else '未通过'
 return '列表任务：'+state if c.get('task_kind')=='structure' else state

def case_rate(rs,c):
 rs=[r for r in rs if measured(r,c)]
 value=rate(sum(succeeded(r,c) for r in rs),len(rs))
 return value

def overview_card(records,cases,track,label,names):
 selected=[c for c in cases.values() if c.get('track','web')==track]
 if not selected:return ''
 rs=[r for r in records if cases[r['case_id']].get('track','web')==track]
 eligible=[r for r in rs if measured(r,cases[r['case_id']])]
 rates={n:sum(succeeded(r,cases[r['case_id']]) for r in eligible if r['provider']==n)/sum(r['provider']==n for r in eligible) for n in names if any(r['provider']==n for r in eligible)}
 maximum=max(rates.values(),default=None)
 winners='、'.join(provider_label(n) for n in names if rates.get(n)==maximum) if maximum else '本轮无通过任务' if rates else '暂无调用记录'
 return f'<div class="card"><div class="eyebrow">{len(selected)} 个任务</div><h3>{link("#home-rankings-"+track,label)}</h3><p>本类最高：<b>{pct(maximum)}</b></p><p>{esc(winners)}</p></div>'

def review_summary(out, prefix=''):
 path=out/'result-review.json'
 if not path.is_file():return ''
 audit=json.loads(path.read_text())
 scope=audit.get('review_protocol',{}).get('scope','all')
 subject='机器失败' if scope=='machine-failures' else '全量'
 return (f'<p class="callout success"><b>独立核验：{subject} '
         f'{audit.get("reviewed_attempts",0):,}/{audit.get("expected_attempts",0):,}；'
         f'未决 {audit.get("unresolved",0)}</b>。'
         f' {link(prefix+"review/index.html","查看核验轮次 →")}</p>')

def review_index(audit):
 return {x['attempt_id']:x for x in (audit or {}).get('attempts',[])}

def review_cell(audit, rows, base=''):
 if not audit:return ''
 indexed=review_index(audit);items=[indexed[r['attempt_id']] for r in rows if r['attempt_id'] in indexed]
 unresolved=sum(not x.get('final_verdict') for x in items)
 label=f'{len(items)}/{len(rows)} · Codex 已核验'
 if unresolved:label+=f' · 未决 {unresolved}'
 return cell(link(base+'review/index.html',label),len(items)/len(rows) if rows else -1)

def _evidence_chunks(text, width=620):
 chunks=[]
 for raw in text.splitlines():
  line=raw.strip()
  if not line:continue
  if len(line)<=width:chunks.append(line)
  else:
   for start in range(0,len(line),width-100):chunks.append(line[start:start+width])
 return chunks

def _best_evidence_excerpt(expected, output):
 from .body_coverage import tokens
 wanted=Counter(tokens(expected))
 if not wanted:return ''
 best='';best_score=0
 for chunk in _evidence_chunks(output):
  found=Counter(tokens(chunk));score=sum((wanted & found).values())/sum(wanted.values())
  if score>best_score:best,best_score=chunk,score
 return best if best_score>=min(.25,3/max(1,sum(wanted.values()))) else ''

def _position_label(index,total):
 names=['开头','前部','中部','后部','结尾'] if total==5 else [f'位置 {i+1}' for i in range(total)]
 start=round(index*100/total);end=round((index+1)*100/total)
 return f'{names[index]}（标准答案 {start}–{end}%）'

def body_region_rows(case, metrics, output):
 from .body_coverage import evaluate_body_requirements
 contract=case.get('body_requirements') or {}
 if not contract:return [],''
 recalculated={x['id']:x for x in evaluate_body_requirements(output,contract)['body_region_results']}
 regions={x['id']:x for x in contract['regions']}
 rows=[];cards=[];total=len(regions)
 for index,item in enumerate(metrics.get('body_region_results',[]),1):
  actual={**recalculated.get(item['id'],{}),**item}
  expected=regions.get(item['id'],{}).get('text','')
  expected_count=actual.get('expected_units',actual.get('expected_tokens',len(expected.split())))
  matched=actual.get('matched_units',actual.get('matched_tokens',round(actual['coverage']*expected_count)))
  passed=actual.get('target_pass',actual['pass'])
  label=_position_label(index-1,total)
  judgement=('达到目标' if passed else '低于目标，但达到硬下限' if actual.get('floor_pass') else '未达到硬下限')
  css='pass' if passed else 'fail'
  rows.append(('',cell(esc(label))+cell(f'{expected_count:,}')+cell(f'{matched:,}')+
               cell(pct(actual['coverage']))+cell(esc(judgement))))
  excerpt=_best_evidence_excerpt(expected,output)
  actual_html=(f'<pre data-kind="region-output">{esc(excerpt)}</pre>' if excerpt else
               '<p class="empty">没有可供人工定位的连续文字样例；机器覆盖率仍按上方完整实际返回计算。</p>')
  cards.append(f'<article class="region-card {css}" id="region-{index}"><div class="region-head"><div><h3>{index}. {esc(label)}</h3><p>这一段标准答案产生 {expected_count:,} 个连续六字符片段；在上方完整实际返回（{len(output):,} 字符）中命中 {matched:,} 个，所以覆盖率为 {matched:,} ÷ {expected_count:,} = {pct(actual["coverage"])}。目标 {pct(actual["minimum"])}，硬下限 {pct(actual.get("floor",actual["minimum"]))}。</p></div><span class="verdict-badge">{esc(judgement)}</span></div><div class="region-pair"><div><b>这一位置的完整标准正文</b><pre data-kind="reference">{esc(expected)}</pre></div><div><b>实际输出中的定位样例（仅供人工查看）</b><p class="source-note">这是从完整实际返回中选出的一小段，便于确认文字确实出现；它不是覆盖率计算所使用的全部实际输出。</p>{actual_html}</div></div></article>')
 return rows,cards

def stable_answer_panel(contract):
 anchors=contract.get('stable_text_anchors',[])
 items=[]
 for index,anchor in enumerate(anchors,1):
  alternatives=anchor.get('alternatives',[])
  text='\n或\n'.join(alternatives)
  items.append(f'<div class="answer-item"><b>必须出现的稳定文字 {index}（任一写法即可）</b><blockquote>{esc(text)}</blockquote></div>')
 minimum=contract.get('minimum_records')
 maximum=contract.get('maximum_records')
 count=(f'至少 {minimum} 条完整且不重复的记录' if maximum is None else
        f'{minimum}–{maximum} 条完整且不重复的记录')
 return f'<div class="answer-list"><div class="answer-item"><b>结构要求</b><blockquote>{esc(count)}</blockquote></div>{"".join(items)}</div>'

def _stable_field_comparison(metrics,item):
 """Find the closest parsed record for a structured anchor, for display only."""
 schema=metrics.get('stable_task_schema')
 if schema!='quote':return None
 from .metrics import normalize_for_match
 from .stable_tasks import SCHEMA_FIELDS,_parse_records,_visible
 candidates=[]
 for alternative in item.get('alternatives',[]):
  expected=_parse_records(_visible(alternative,schema),schema)
  candidates.extend(row['fields'] for row in expected if row.get('valid'))
 actual=[row['fields'] for row in metrics.get('stable_records',[]) if row.get('valid')]
 if not candidates or not actual:return None
 def norm(value):return normalize_for_match(value).strip('“”"‘’ ')
 best=None
 for expected in candidates:
  for returned in actual:
   exact={field:norm(returned.get(field,''))==norm(expected.get(field,'')) for field in SCHEMA_FIELDS[schema]}
   score=sum(exact.values())+2*exact.get('quote',False)+exact.get('author',False)
   if best is None or score>best[0]:best=(score,expected,returned,exact)
 return best[1:] if best else None

def _quote_field_issue(metrics):
 comparisons=[]
 for item in metrics.get('stable_anchor_results',[]):
  if item.get('pass'):continue
  comparison=_stable_field_comparison(metrics,item)
  if comparison and comparison[2].get('quote') and comparison[2].get('author'):
   comparisons.append(comparison)
 if not comparisons:return ''
 from .metrics import normalize_for_match
 compact=0
 for expected,returned,_ in comparisons:
  left=normalize_for_match(expected.get('tags','')).replace(' ','')
  right=normalize_for_match(returned.get('tags','')).replace(' ','')
  compact+=bool(left and left==right)
 contaminated=len(comparisons)-compact
 parts=[f'{len(comparisons)} 个未通过核对点的引文和作者均与标准答案一致']
 if compact:parts.append(f'{compact} 条的标签文字齐全，但标签之间没有可读分隔')
 if contaminated:parts.append(f'{contaminated} 条的标签字段还混入或缺少文字')
 return '；'.join(parts)+'。任务要求标签列表保留字段边界，因此仍判未通过'

def stable_anchor_evidence(metrics,output):
 cards=[]
 for index,item in enumerate(metrics.get('stable_anchor_results',[]),1):
  alternatives=item.get('alternatives',[]);expected='\n或\n'.join(alternatives)
  excerpt=''
  for alternative in alternatives:
   candidate=_best_evidence_excerpt(alternative,output)
   if candidate:excerpt=candidate;break
  passed=bool(item.get('pass'));css='pass' if passed else 'fail'
  comparison=_stable_field_comparison(metrics,item) if not passed else None
  field_detail='';badge='命中' if passed else '未命中'
  if comparison:
   expected_fields,returned_fields,exact=comparison
   labels={'quote':'引文','author':'作者','tags':'标签'};rows=[]
   from .metrics import normalize_for_match
   for field in ('quote','author','tags'):
    wanted=expected_fields.get(field,'');got=returned_fields.get(field,'')
    compact=(normalize_for_match(wanted).replace(' ','')==normalize_for_match(got).replace(' ',''))
    verdict='命中' if exact[field] else '文字齐全，但分隔丢失' if field=='tags' and compact else '不一致'
    rows.append(('',cell(esc(labels[field]))+cell(esc(wanted))+cell(esc(got))+cell(esc(verdict))))
   field_detail='<p class="source-note">整条记录按字段判定；文字出现不等于列表边界完整。</p>'+table(['字段','标准答案','实际解析','判定'],rows,metrics=[])
   badge='字段边界不合格'
  actual=(f'<p>{esc(excerpt)}</p>' if excerpt else '<p class="empty">实际返回中未找到可供定位的连续原文。</p>')
  cards.append(f'<article class="anchor-card {css}"><div class="anchor-head"><h3>稳定文字 {index}</h3><span class="verdict-badge">{badge}</span></div><div class="evidence-pair"><div class="evidence-sample"><b>标准答案</b><p>{esc(expected)}</p></div><div class="evidence-sample"><b>实际返回证据</b>{actual}</div></div>{field_detail}</article>')
 return '<div class="anchor-grid">'+''.join(cards)+'</div>' if cards else ''

def failure_category(r,c):
 if not measured(r,c):return '未测：缺少验收证据'
 if succeeded(r,c):return '—'
 if _quote_field_issue(r['metrics']):return '字段边界丢失'
 return (r.get('diagnosis') or diagnose(r,c,'') or {'category':'任务规则未满足'})['category']

def stable_failure_summary(metrics):
 parts=[]
 minimum=metrics.get('stable_minimum_records')
 complete=metrics.get('stable_unique_record_count')
 if isinstance(minimum,int) and isinstance(complete,int) and complete < minimum:
  parts.append(f'只有 {complete} 条记录同时做到字段完整且不重复，少于要求的 {minimum} 条')
 quote_issue=_quote_field_issue(metrics)
 if quote_issue:return quote_issue
 missing=[]
 for item in metrics.get('stable_anchor_results',[]):
  if not item.get('pass'):
   alternatives=item.get('alternatives',[])
   if alternatives:missing.append(' / '.join(alternatives))
 if missing:parts.append('缺少稳定文字“'+'”、“'.join(missing)+'”')
 if metrics.get('text_encoding_corruption'):
  parts.append(f'输出存在明显乱码：{metrics.get("replacement_character_count",0):,} 个替换字符，占 {pct(metrics.get("replacement_character_ratio"))}')
 errors=set(metrics.get('stable_task_errors') or [])
 labels=[
  ('declared_truncation','方法明确声明正文已截断'),
  ('script_payload_shell','返回的是脚本或序列化状态，不是可读正文'),
  ('content_too_short','正文短于任务声明的最低长度'),
  ('duplicate_records','返回记录存在重复'),('incomplete_records','部分记录缺少必需字段'),
  ('too_many_complete_records','完整记录数超过任务声明范围'),
  ('blocked_page','实际返回是访问限制或验证页面'),
 ]
 for key,label in labels:
  if key in errors and label not in parts:parts.append(label)
 return '；'.join(parts) or '稳定文字或记录结构没有满足任务要求'

def reason(r,c):
 if not measured(r,c):return '未测：历史调用未记录原文件字节验收证据，不计入任务通过率或失败数。'
 if r['metrics'].get('deadline_overrun'):return '超过该调用登记的端到端时间预算；保留实际耗时和返回内容，但不计为按时交付。'
 if r['ok'] and 'stable_task_pass' in r['metrics']:
  m=r['metrics']
  success='稳定内容与结构检查通过。' if m.get('stable_anchor_results') or m.get('stable_page_pattern_results') else '条目结构检查通过；本例未设置稳定文字核对点。'
  if m.get('stable_task_schema') == 'body_text':
   return success if succeeded(r,c) else stable_failure_summary(m)+'。'
  counts=f"检出 {m.get('stable_record_count','—')} 条，字段完整 {m.get('stable_complete_record_count','—')} 条，完整且唯一 {m.get('stable_unique_record_count','—')} 条，要求至少 {m.get('stable_minimum_records','—')} 条。"
  return counts+(success if succeeded(r,c) else stable_failure_summary(m)+'。')
 if not r['ok']:
  if r.get('diagnosis'):return r['diagnosis']['evidence']
  names={'dns':'请求路径中的域名无法解析','timeout':'请求超时','network':'请求路径网络故障','api_error':'供应商接口报错','WEB_BLOCKED_URL':'方法的 URL 安全规则拒绝请求'}
  return names.get(r.get('error_type'),r.get('error_type') or '请求失败')+': '+str(r.get('error_message') or '')[:300]
 m=r['metrics']
 if m.get('text_encoding_corruption'):
  return f'实际返回存在明显乱码：{m.get("replacement_character_count",0):,} 个替换字符，占 {pct(m.get("replacement_character_ratio"))}；损坏文字不能作为有效正文。'
 if m.get('body_region_results') and not m.get('body_coverage_pass'):
  regions=m['body_region_results'];failed=[]
  for index,item in enumerate(regions):
   if not item.get('target_pass',item.get('pass')):
    failed.append(f'{_position_label(index,len(regions)).split("（")[0]} {pct(item.get("coverage"))}')
  return '正文没有抓全；低于目标的位置为：'+'、'.join(failed)+'。下方会把完整标准答案与实际返回并排展示。'
 if r.get('diagnosis'):return r['diagnosis']['evidence']
 if c.get('task_kind')=='structure':
  from .report_components import list_rule_description
  return f"检出 {r['metrics'].get('named_entry_count',0)} 个具名详情入口。"+list_rule_description(c)
 if c.get('task_kind')=='file':return '已返回原始文件，格式特征、长度及目标 SHA-256 校验通过。' if succeeded(r,c) else '没有返回可核对的原始文件；文字或重复输入地址不算文件。'
 if c.get('task_kind')=='resource':
  missing=[x['id'] for x in r['metrics'].get('resource_results',[]) if not x['pass']]
  unexpected=r['metrics'].get('unexpected_resource_urls',[])
  if missing and unexpected:
   return ('未找到目标资源：'+', '.join(missing)
           +'；同时返回非目标资源：'+', '.join(unexpected))
  if unexpected:return '已找到目标资源，但答案字段还返回了非目标资源：'+', '.join(unexpected)
  return '已找到目标资源。' if not missing else '未找到目标资源：'+', '.join(missing)
 m=r['metrics'];parts=[]
 if m.get('declared_truncation_hits'):parts.append('供应商声明正文已截断')
 if not m.get('length_pass'):parts.append('正文过短')
 if m.get('forbidden_hits'):parts.append('命中排除文本：'+', '.join(m['forbidden_hits']))
 missing=[x['id'] for x in m.get('fact_results',[]) if not x['pass']]
 if missing:parts.append(('条目字段未完整匹配：' if any(x.get('match_scope')=='entry' for x in m.get('fact_results',[])) else '缺少核对点：')+', '.join(missing))
 for x in m.get('pattern_results',[]):
  if not x['pass']:parts.append(f"{x['id']}仅{x['count']}项，要求{x['minimum']}项")
 return '；'.join(parts) or '正文核对点通过；完整程度另见全文指标。'

def stable_checks(m):
 checks=[]
 for x in m.get('stable_anchor_results',[]):
  checks.append(('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',[]))))+cell('通过' if x['pass'] else '未通过')+cell(esc(' / '.join(x.get('matched',[]))))))
 for i,x in enumerate(m.get('stable_page_pattern_results',[]),1):
  checks.append(('',cell(f'页面模式 {i}')+cell(esc(x['pattern']))+cell('通过' if x['pass'] else '未通过')+cell('—')))
 content=table(['稳定内容核对点','目标','判定','实际命中'],checks) if checks else '<p>本例未设置稳定文字核对点，不作文字内容已核验的结论。</p>'
 records=[('',cell(esc(json.dumps(x.get('fields',{}),ensure_ascii=False)))+cell('通过' if x.get('valid') else '未通过')+cell(esc('；'.join(x.get('errors',[])) or '—'))) for x in m.get('stable_records',[])]
 return content+table(['条目字段','结构判定','原因'],records)+('<p>未通过原因：'+esc(stable_failure_summary(m))+'。</p>' if m.get('stable_task_errors') else '')

def ranking(records,cases,track,base='',id=None,compact=False,audit=None):
 from .report_components import comparable_body_reference
 text_track=track in {'web','dynamic','document','challenge'}
 title_scope=track=='challenge' and any('title_quality' in r for r in records if cases[r['case_id']].get('track')==track)
 selected={k:c for k,c in cases.items() if c.get('track','web')==track}
 groups=defaultdict(list)
 for r in records:
  if cases[r['case_id']].get('track','web')==track:groups[r['provider']].append(r)
 def sort_key(item):
  name,rs=item
  n=sum(measured(r,cases[r['case_id']]) for r in rs)
  return (-sum(succeeded(r,cases[r['case_id']]) for r in rs)/n if n else 1,name)
 rows=[];text_scopes={}
 for name,rs in sorted(groups.items(),key=sort_key):
  n=sum(measured(r,cases[r['case_id']]) for r in rs);ok=sum(bool(r['ok']) for r in rs);passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  body=[r for r in rs if cases[r['case_id']].get('task_kind','body')=='body'];bodypass=sum(succeeded(r,cases[r['case_id']]) for r in body)
  full=[r for r in rs if comparable_body_reference(cases[r['case_id']]) and r['metrics'].get('word_f1') is not None]
  def avg(key):
   per_case=defaultdict(list)
   for r in full:
    if r['metrics'].get(key) is not None:per_case[r['case_id']].append(r['metrics'][key] if r['ok'] else 0)
   return statistics.mean(statistics.mean(values) for values in per_case.values()) if per_case else None
  times=[r['elapsed_ms'] for r in rs if succeeded(r,cases[r['case_id']])]
  values=cell(link(base+f'providers/{name}.html',provider_label(name)))+cell(rate(ok,len(rs)),ok/len(rs))
  if track in {'web','dynamic','document','challenge'}:
   values+=cell(rate(passed,n),passed/n if n else -1)
   values+=''.join(cell(pct(avg(k)),avg(k) if full else -1) for k in ['word_recall','word_precision','word_f1'])
   per_case=defaultdict(list)
   for r in rs:
    m=r['metrics']; checks=m.get('stable_anchor_results',[]) if cases[r['case_id']].get('acceptance_contract') else m.get('fact_results',[])
    if checks:per_case[r['case_id']].append(sum(bool(x.get('pass')) for x in checks)/len(checks) if r['ok'] else 0)
   coverage=statistics.mean(statistics.mean(v) for v in per_case.values()) if per_case else None
   text_scopes[name]=frozenset(per_case)
   values+=cell(pct(coverage),coverage if coverage is not None else -1)
  else:
   resources=[r for r in rs if cases[r['case_id']].get('task_kind')=='resource'];files=[r for r in rs if cases[r['case_id']].get('task_kind')=='file']
   values+=cell(rate(passed,n),passed/n if n else -1)
   for subset,total in [(resources,sum(succeeded(r,cases[r['case_id']]) for r in resources)),(files,sum(succeeded(r,cases[r['case_id']]) for r in files)),(body,bodypass)]:
    subset=[r for r in subset if measured(r,cases[r['case_id']])]
    values+=cell(rate(total,len(subset)),total/len(subset) if subset else -1)
  values+=cell(ms(statistics.mean(times)) if times else '—',statistics.mean(times) if times else -1)+cell(ms(percentile(times,.5)),percentile(times,.5) or -1)+cell(ms(percentile(times,.95)),percentile(times,.95) or -1)
  values+=cell(f'{n}/{len(selected)}',n/len(selected) if selected else -1)
  if audit:values+=review_cell(audit,rs,base)
  rows.append(('',values))
 heads=['方法','请求成功率']
 if track in {'web','dynamic','document','challenge'}:
  heads+=['任务通过率','完整度','纯净度','F1','文字核对覆盖']
 else:heads+=['任务通过率','资源命中率','文件取得率','信息通过率']
 heads+=['均值','P50','P95','判分完整性']
 if audit:heads+=['审查结果']
 note='请求成功率和任务通过率以全部任务为分母。'
 if text_track:
  covered=sum(comparable_body_reference(c) for c in selected.values())
  note=f'完整参考覆盖 {covered}/{len(selected)}；缺少参考只影响全文指标，不影响任务结果。有参考时，请求失败计 0；请求成功但任务未通过仍按实际正文评分。'
  from .report_components import list_rule_description
  identity_rules=list(dict.fromkeys(list_rule_description(c) for c in selected.values()
   if c.get('task_kind')=='structure' and not c.get('acceptance_contract')))
  if identity_rules:note+=' '+esc(' '.join(identity_rules))
  note+=' 文字核对覆盖只衡量各例登记的必需文字组，不等于全文完整度；没有文字核对依据的案例不进入该项均值。'
  if text_scopes and len(set(text_scopes.values()))==1:note+=f' 文字核对范围：各方法均为 {len(next(iter(text_scopes.values())))}/{len(selected)} 例。'
  elif text_scopes:note+=' 各方法文字核对范围不同，详见下方覆盖范围。'
  if track=='challenge':note+=' 列表、固定字段标签与说明共同验收；标题文字质量仅作下方辅助诊断。'
  if track=='dynamic':note+=' 含有已排除易变字段的旧快照文字分仅保留在调用详情，不进入本表全文均值。'
 else:note='直接文件必须有字节验收才能判分；任何缺失都会使整份报告标为不可靠。资源、文件、信息三列分别按对应任务计算。'
 note+=' 判分完整性是已有明确判定的调用数/计划调用数，不是通过率；任何方法未满即表示整份报告不完整，禁止发布。均值、P50、P95 仅统计各方法自身通过项，不能直接视为同题速度排名。'
 unmeasured=sorted({r['case_id'] for rs in groups.values() for r in rs if not measured(r,cases[r['case_id']])})
 if unmeasured:note+=' 缺少验收证据的案例：'+'、'.join(link(base+'datasets/'+key+'.html',cases[key]['name']) for key in unmeasured)+'。'+link(base+'changes/index.html#file-gap','为什么未测、补测进展 →')
 if track=='dynamic':
  missing=sorted({r['provider'] for r in records}-set(groups))
  note+=' 各类型按各自任务范围比较，不合并为跨类型总分。'
  if missing:note+=' 未测方法（不计为失败）：'+esc('、'.join(missing))+'。'
 result=table(heads,rows,id or 'ranking-'+track,True,'ranking',metrics=[] if compact else None)
 if text_scopes and len(set(text_scopes.values()))>1:
  result='<details><summary>文字核对覆盖范围</summary><ul>'+''.join('<li>'+esc(name)+f'：{len(scope)}/{len(selected)} 例；'+esc('、'.join(selected[k]['name'] for k in sorted(scope)) or '无文字核对记录')+'</li>' for name,scope in sorted(text_scopes.items()))+'</ul></details>'+result
 if compact:
  result='<details class="ranking-metric-help"><summary>指标说明 · 通过率、质量与耗时</summary>'+metric_legend(heads)+'</details>'+result
 if title_scope:result+=title_diagnostics(groups,selected,base,id or 'ranking-'+track)
 result+=matched_latency(groups,cases,base)
 from .report_components import task_pass_guide
 return task_pass_guide(track,base,selected)+'<p class="source-note">'+note+'</p>'+result.replace('<table class="ranking"','<table class="ranking" data-col="3" data-dir="desc"')

def matched_latency(groups,cases,base):
 scopes=[{r['case_id'] for r in rows if succeeded(r,cases[r['case_id']])} for rows in groups.values()]
 common=set.intersection(*scopes) if scopes else set()
 note=f'当前参评方法共同通过 {len(common)} 个案例；仅在这个相同子集内比较耗时，不代表失败任务的成本或生产 SLA。'
 rows=[]
 if common:
  for provider, records in sorted(groups.items()):
   bycase=defaultdict(list)
   for r in records:
    if r['case_id'] in common and succeeded(r,cases[r['case_id']]):bycase[r['case_id']].append(r['elapsed_ms'])
   times=[statistics.mean(v) for v in bycase.values()]
   rows.append(('',cell(esc(provider))+cell(ms(statistics.mean(times)))+cell(ms(percentile(times,.5)))))
 links='、'.join(link(base+'datasets/'+key+'.html',cases[key].get('name',key)) for key in sorted(common))
 return '<details><summary>同题耗时 · 全部方法共同通过子集</summary><p>'+note+'</p>'+ (table(['方法','同题均值','同题 P50'],rows,metrics=[])+ '<p>'+links+'</p>' if rows else '')+'</details>'

def overview_matched_latency(records,cases):
 from .report_explainer import _article_speed_population
 names=sorted({r['provider'] for r in records})
 keys={(r['provider'],r['case_id']) for r in records}
 if not names or len(keys)!=len(records) or any((name,cid) not in keys for name in names for cid in cases):
  return '<p>调用矩阵尚不完整，暂不汇总全部方法共同成功任务的耗时。</p>'
 common=set.intersection(*({r['case_id'] for r in records if r['provider']==name and succeeded(r,cases[r['case_id']])} for name in names))
 if not common:return '<p>全部方法尚无共同成功任务，不计算同题耗时。</p>'
 population=_article_speed_population(cases,records,names,execution_scope='Main-evaluation historical calls across batches; all registered methods and configurations, not a same-window controlled experiment.')
 metrics=['mean_ms','p50_ms','p95_ms'];rows=[]
 minima={key:min(row[key] for row in population['rows']) for key in metrics}
 for row in population['rows']:
  values=cell(link('providers/'+row['provider']+'.html',provider_label(row['provider'])))
  for key in metrics:
   value=ms(row[key]);values+=cell('<strong>'+value+'</strong>' if row[key]==minima[key] else value,row[key])
  rows.append(('',values))
 count=population['common_pass_count']
 note=(f'<p>全部 {len(names)} 种受测配置共同通过 <strong>{count}/{len(cases)}</strong> 个任务。下表每行使用这同一组任务；均值、P50、P95 的最低值加粗。</p>'
       '<p class="source-note">范围包含报告登记的全部方法与版本。统计沿用主评测的历史调用及已披露时延修正，调用跨日期、跨批次；这些数值不代表同一时间窗口内的受控速度比较，也不包含失败任务的耗时。</p>')
 dates=[]
 for row in population['rows']:
  observed=row['recorded_start_dates']
  if observed:
   dates.append(esc(provider_label(row['provider']))+'：'+esc('、'.join(f'{date}（{number} 题）' for date,number in observed.items())))
 links='、'.join(link('datasets/'+cid+'.html',cases[cid].get('name',cid)) for cid in population['common_pass_cases'])
 content=(note+table(['方法','同题均值','同题 P50','同题 P95'],rows,'overall-matched-latency-table',metrics=[])
          +'<details><summary>共同任务与调用日期</summary><p>'+links+'</p><p>'+'<br>'.join(dates)+'</p></details>')
 return '<details class="ranking-metric-help"><summary>按需查看共同成功任务耗时</summary>'+content+'</details>'

def overview_resources(out):
 path=out/'resource-cohort/resource-comparison.json'
 if not path.is_file():
  return '<p>当前证据没有独立的客户端资源测量；不从耗时或进程体积推测 CPU 与内存。</p>'
 summary=json.loads(path.read_text())
 rendered,_=render_resource_comparison(summary,['lightpanda','moli','obscura','chromium','kitesurf'])
 return rendered

def title_diagnostics(groups,selected,base,identifier):
 rows=[]
 for name,rs in sorted(groups.items()):
  measured=[r for r in rs if r.get('title_quality',{}).get('f1') is not None]
  values=cell(link(base+f'providers/{name}.html',name))
  for key in ['recall','precision','f1']:
   per_case=defaultdict(list)
   for r in measured:per_case[r['case_id']].append(r['title_quality'][key])
   value=statistics.mean(statistics.mean(v) for v in per_case.values()) if per_case else None
   values+=cell(pct(value),value if value is not None else -1)
  count=sum(r.get('title_quality',{}).get('scored_titles',0) for r in rs)
  total=sum(r.get('title_quality',{}).get('returned_titles',0) for r in rs)
  coverage=f'{count}/{total} · {len({r["case_id"] for r in measured})}/{len(selected)} 例'
  if not measured:coverage+='（无可评分标题）'
  rows.append(('',values+cell(coverage)))
 returned=sum(r.get('title_quality',{}).get('returned_titles',0) for rs in groups.values() for r in rs)
 scored=sum(r.get('title_quality',{}).get('scored_titles',0) for rs in groups.values() for r in rs)
 cases_scored=len({r['case_id'] for rs in groups.values() for r in rs if r.get('title_quality',{}).get('f1') is not None})
 steps=(f'<ol class="quality-steps"><li><b>识别返回条目</b><span>从结构化结果中读取标题与详情链接，共 {returned} 个标题。</span></li>'
        f'<li><b>按详情链接对齐参考</b><span>{scored}/{returned} 个标题找到独立名称证据；没有对应证据的不评分。</span></li>'
        f'<li><b>逐标题比较文字</b><span>分别计算标题完整度、纯净度和 F1，格式符号不当作标题内容。</span></li>'
        f'<li><b>汇总可比结果</b><span>覆盖 {cases_scored}/{len(selected)} 个案例；这是标题诊断，不参与任务通过判定。</span></li></ol>')
 return ('<details class="ranking-metric-help"><summary>标题文字质量 · 计算过程与结果</summary>'
  '<p>该诊断回答“已经返回的条目标题是否完整、干净”，不衡量榜单召回率、实时性或整页正文质量。</p>'+steps
  +table(['方法','标题完整度','标题纯净度','标题 F1','标题参考覆盖（可评分 / 返回）'],rows,identifier+'-titles',True,metrics=[])+ '</details>')

def overall_ranking(records,cases,audit=None):
 groups=defaultdict(list)
 for r in records:groups[r['provider']].append(r)
 rows=[]
 for name,rs in groups.items():
  n=sum(measured(r,cases[r['case_id']]) for r in rs)
  passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  values=cell(link(f'providers/{name}.html',provider_label(name)))+cell(rate(sum(bool(r['ok']) for r in rs),len(rs)))+cell(rate(passed,n),passed/n if n else -1)
  for track,_ in active_tracks(cases):
   subset=[r for r in rs if cases[r['case_id']].get('track','web')==track and measured(r,cases[r['case_id']])]
   values+=cell(f'{sum(succeeded(r,cases[r["case_id"]]) for r in subset)}/{len(subset)}',sum(succeeded(r,cases[r['case_id']]) for r in subset)/len(subset) if subset else -1)
  times=[r['elapsed_ms'] for r in rs]
  values+=cell(ms(statistics.mean(times)),statistics.mean(times))
  values+=cell(ms(percentile(times,.5)),percentile(times,.5))
  values+=cell(ms(percentile(times,.95)),percentile(times,.95))
  if audit:values+=review_cell(audit,rs)
  rows.append((passed,name,('',values)))
 rows=[row for _,_,row in sorted(rows,key=lambda x:(-x[0],x[1]))]
 tracks=active_tracks(cases)
 heads=['方法','请求成功率','综合任务通过率']+[label for _,label in tracks]+['均值','P50','P95']
 if audit:heads+=['审查结果']
 case_count=len(cases);track_count=len(tracks);track_label=track_count_label(track_count)
 if case_count==150 and track_count==6:
  intro='<p class="decision-lead"><b>总榜口径：</b>每种方法都执行同一组 150 个任务，每题计一次通过或失败，直接相加；耗时单列展示，不参与通过率排序。总榜适合看整体交付覆盖，具体能力仍应回到六个分榜。</p>'
 else:
  intro=(f'<p class="decision-lead"><b>总榜口径：</b>每种方法都执行当前评测集计划的 {case_count} 个任务，每题计一次通过或失败，直接相加；耗时单列展示，不参与通过率排序。总榜适合看整体交付覆盖，具体能力仍应回到 {track_label} 个分榜。</p>')
 help=('<details class="ranking-metric-help"><summary>总榜指标说明</summary><div class="metric-help"><dl>'
       f'<div><dt>综合任务通过率</dt><dd>{case_count} 个任务中满足各自验收规则的数量；每题等权。</dd></div>'
       f'<div><dt>{track_label} 类通过数</dt><dd>各分榜通过数 / 本类任务数，用于定位总分来自哪些能力。</dd></div>'
       f'<div><dt>全调用均值 / P50 / P95</dt><dd>每种方法在同一组 {case_count} 个计划任务上的端到端耗时，包含通过与失败；三项不参与通过率排名。主矩阵调用跨日期、跨批次，不等同于同窗受控速度比较。</dd></div>'
       '<div><dt>审查结果</dt><dd>独立盲审覆盖数、审查方法和未决数。</dd></div>'
       '</dl></div></details>')
 return intro+help+table(heads,rows,'ranking-overall',True,'ranking',metrics=[]).replace('<table class="ranking"','<table class="ranking" data-col="3" data-dir="desc"')

def filter_controls(options,all_first=()):
 controls=''
 for key,label,choices in options:
  ordered=([('','全部')]+choices) if key in {'track','category','failureStage','failureCategory',*all_first} else (choices+[('','全部')])
  controls+=f'<label>{label} <select data-filter="{key}" aria-label="{label}">'+''.join(f'<option value="{esc(v)}"'+(' selected' if key=='result' and v=='fail' else '')+f'>{esc(t)}</option>' for v,t in ordered)+'</select></label>'
 return '<div class="filters">'+controls+'<input data-filter="query" aria-label="搜索" placeholder="搜索案例、方法或原因"><span id="count" role="status" aria-live="polite"></span></div>'

def failure_filters(records,cases):
 categories=sorted({failure_category(r,cases[r['case_id']]) for r in records if measured(r,cases[r['case_id']]) and not succeeded(r,cases[r['case_id']])})
 return [('failureStage','失败环节',[('request','请求失败'),('task','请求成功但任务失败')]),('failureCategory','失败类别',[(x,x) for x in categories])]

def comparison_rows(cases,bycase,names):
 rows=[]
 for case_id,c in cases.items():
  attempts={r['provider']:r for r in bycase.get(case_id,[])}
  passed=[name for name in names if name in attempts and measured(attempts[name],c) and succeeded(attempts[name],c)]
  failed=[name for name in names if name in attempts and measured(attempts[name],c) and not succeeded(attempts[name],c)]
  method_links=lambda selected,state:('<span class="outcome-list">'+''.join(f'<a class="outcome-pill {state}" href="{esc(safe_href("../attempts/"+attempts[name]["attempt_id"]+".html"))}">{esc(provider_label(name))}</a>' for name in selected)+'</span>') if selected else '—'
  attrs=(dataset_attrs(c)+f' data-pass="{esc(" ".join(passed))}" data-fail="{esc(" ".join(failed))}"')
  task=(f'<div class="task-cell">{link("../datasets/"+case_id+".html",c["name"])}<div class="task-meta"><code class="tiny">{esc(case_id)}</code>'
        f'<span class="track-badge">{esc(TRACKS[c.get("track","web")])}</span></div></div>')
  values=cell(task)+cell(method_links(passed,'pass'))+cell(method_links(failed,'fail'))
  rows.append((attrs,values))
 return rows

def comparison_table(rows,body_id):
 return table(['任务','成功方法','失败方法'],rows,cls='comparison-table',metrics=[]).replace('<tbody>',f'<tbody id="{body_id}">',1)

def method_comparisons(cases,bycase,names):
 all_attempts=[attempt for attempts in bycase.values() for attempt in attempts]
 ordered=sorted(names,key=lambda name:(-sum(succeeded(attempt,cases[attempt['case_id']]) for attempt in all_attempts if attempt['provider']==name),provider_label(name)))
 if len(ordered)<2:return '<p class="decision-lead">至少需要两种方法才能比较任务交集。</p>'
 a=ordered[0];b=ordered[1]
 options=lambda selected:''.join(f'<option value="{esc(name)}"'+(' selected' if name==selected else '')+f'>{esc(provider_label(name))}</option>' for name in ordered)
 tracks='<option value="">全部类型</option>'+''.join(f'<option value="{esc(key)}">{esc(label)}</option>' for key,label in TRACKS.items())
 rows=comparison_rows(cases,bycase,names)
 checks=''.join(f'<label><input type="checkbox" value="{esc(name)}" data-compare-method'+(' checked' if name==b else '')+f'> {esc(provider_label(name))}</label>' for name in ordered)
 return (f'<article class="method-comparison"><header class="comparison-heading"><div><h3>同题结果组合</h3><p>以方法 A 为基准，可选择一个或多个方法共同比较；方法名可直达该次完整证据。</p></div><b class="comparison-count" id="compare-count" role="status" aria-live="polite"></b></header>'
         '<div class="comparison-controls">'
         f'<label>方法 A<select id="compare-a" data-compare-control>{options(a)}</select></label>'
         '<label>比较条件<select id="compare-relation" data-compare-control><option value="mixed">结果有分歧</option><option value="a_pass_others_fail">A 成功，其余失败</option><option value="a_fail_others_pass">A 失败，其余成功</option><option value="all_pass">所选方法全部成功</option><option value="all_fail">所选方法全部失败</option><option value="exact_pass">仅所选方法成功</option></select></label>'
         f'<label>任务类型<select id="compare-track" data-compare-control>{tracks}</select></label></div>'
         f'<fieldset class="comparison-methods"><legend>对比方法（可多选）</legend><div class="method-checks">{checks}</div></fieldset>'
         f'<div class="comparison-result">{comparison_table(rows,"compare-rows")}</div></article>')

def dataset_attrs(c):
 return f' data-track="{esc(c.get("track","web"))}" data-category="{esc(c["category"])}"'

def case_attrs(r,c):
 content='na' if c.get('task_kind','body')!='body' else 'pass' if r['ok'] and r['metrics']['content_pass'] else 'fail'
 resources='na' if not c.get('expected_resources') else 'pass' if r['ok'] and r['metrics'].get('resource_pass') else 'fail'
 stage='' if succeeded(r,c) or not measured(r,c) else 'task' if r['ok'] else 'request'
 category='' if not stage else failure_category(r,c)
 result='unmeasured' if not measured(r,c) else 'pass' if succeeded(r,c) else 'fail'
 return dataset_attrs(c)+f' data-provider="{esc(r["provider"])}" data-transport="{"pass" if r["ok"] else "fail"}" data-result="{result}" data-content="{content}" data-resources="{resources}" data-failure-stage="{stage}" data-failure-category="{esc(category)}" data-search="{esc(reason(r,c))}"'

def attempt_row(r,c,base='../'):
 m=r['metrics'];state=result_label(r,c)
 values=cell(link(base+'attempts/'+r['attempt_id']+'.html',c['name']))+cell(TRACKS[c.get('track','web')])+cell(f'<span class="{"good" if succeeded(r,c) else "bad"}">{state}</span>')+cell(esc(failure_category(r,c)))+cell(ms(r['elapsed_ms']))
 values+=cell(f'<code class="tiny break">{esc(r.get("run_id") or execution_id(r))}</code>')
 return case_attrs(r,c),values

def generate(out:Path,dataset:dict,records:list,manifests:list):
 global _RELIABILITY_BANNER
 from .reliability import assess_reliability
 reliability=assess_reliability(dataset,records)
 if reliability['complete']:
  _RELIABILITY_BANNER=''
 else:
  _RELIABILITY_BANNER='<div class="wrap reliability failure" role="alert"><strong>报告不可靠：评测未完整</strong><span>'+esc('；'.join(reliability['errors']))+'。本页排名不得用于结论或发布。</span></div>'
 validate_report_ids(dataset,records)
 for path in ['assets/style.css','assets/report.js','references']:
  contained_path(out,path)
 for c in dataset['cases']:
  contained_path(out,'references/'+c['id']+'.txt')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA);(out/'assets/report.js').write_text(JS)
 cases={c['id']:c for c in dataset['cases']};names=sorted(set(r['provider'] for r in records))
 tracks=active_tracks(cases);track_count=len(tracks);track_label=track_count_label(track_count)
 audit=json.loads((out/'result-review.json').read_text()) if (out/'result-review.json').is_file() else None
 audited_attempts=review_index(audit)
 dynamic=any(c.get('task_kind')=='structure' for c in cases.values())
 bycase=defaultdict(list);byprovider=defaultdict(list)
 for r in records:bycase[r['case_id']].append(r);byprovider[r['provider']].append(r)
 cards=''
 for track,label in tracks:
  cards+=overview_card(records,cases,track,label,names)
 home='<div class="home-page">'+hero('Web Fetch 评测报告',f'{len(cases)} 个案例 · {len(names)} 种方法 · {track_label} 类分榜与一个综合通过率总榜。')
 from .report_explainer import article_title,render_explainer
 TITLE=article_title(records)
 home+='<aside class="wrap home-reading" aria-label="报告解读"><div><strong>报告解读</strong><p>'+link('guide/index.html',TITLE)+'</p></div><p>从分数差异、失败案例和实现机制，理解如何选择抓取方法。</p>'+link('guide/index.html','阅读全文 →')+'</aside>'
 write(out,'guide/index.html','报告解读',render_explainer(out,cases,records,table=table,cell=cell,link=link))
 panels=[]
 for track,label in tracks:
  selected=[c for c in cases.values() if c.get('track','web')==track]
  coverage=f'{len(selected)} 个案例 · {len({r["provider"] for r in records if cases[r["case_id"]].get("track","web")==track})} 种已测方法'
  panels.append((track,label,f'<div class="track-heading"><h3>{label}</h3><span>{coverage}</span></div>'+ranking(records,cases,track,compact=True,audit=audit)+f'<p>{link("datasets/index.html#track-"+track,"案例与参考 →")}</p>'))
 home+=section(f'{track_label} 类任务概览',review_summary(out)+'<p class="callout success"><b>调用完整：</b>'+str(reliability['expected_calls'])+'/'+str(reliability['expected_calls'])+' 次均有结果。</p><p class="source-note">只在同一任务类别内比较。</p><div class="grid home-overview-grid">'+cards+'</div>','overview')
 home+=section('综合通过率总榜',overall_ranking(records,cases,audit),'overall-ranking')
 home+=section('全部方法共同成功任务的耗时',overview_matched_latency(records,cases),'overall-matched-latency')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  home+=section('CPU、内存与网络',overview_resources(out),'overall-resources')
 home+=section(f'{track_label} 类分榜','<p class="ranking-orientation">分榜用于判断具体能力。默认按任务通过率排序，点击列名可切换；质量分数与通过率分别计算。</p>'+tabs('home-rankings',panels),'results')
 write(out,'index.html','评测报告',home+'</div>')
 controls=filter_controls([('track','类型',tracks),('category','类别',[(x,x) for x in dict.fromkeys(c['category'] for c in cases.values())])])
 rows=[]
 for c in cases.values():
  verified=c.get('source_verification',{});state='受限' if verified.get('state')=='restricted' else '有效'
  rows.append((dataset_attrs(c),cell(link(c['id']+'.html',c['name']))+cell(esc(c['category']))+cell(link(c['url'],'源页 ↗'))+cell(link(c['id']+'.html#facts',basis_label(c)))+cell(reference_label(c))+cell(state)+cell(case_rate(bycase[c['id']],c))))
 anchors=''.join(f'<span id="track-{t}"></span>' for t,_ in tracks)
 dataset_panels=[('cases','案例',anchors+controls+'<p class="source-note">核对依据可点击展开。访问受限不等于网站失效；稳定内容与条目结构按登记契约核验，不固定频繁变化的榜单。动态网站正文按各案例声明的完整范围核验。</p>'+table(['案例','类别','URL','核对依据','参考状态','原访问','达标率'],rows,'filtered-table')),('references','参考状态','<span id="references"></span>'+reference_status(list(cases.values()))),('sources','来源','<span id="sources"></span>'+sources_panel())]
 write(out,'datasets/index.html','评测集',hero('评测集',f'{len(cases)} 个案例，按任务类型查看。')+section('案例与依据',tabs('dataset-hub',dataset_panels)))
 headers=['案例','类型','结果','失败分类','耗时','运行 ID']
 for c in cases.values():
  v=c.get('source_verification',{});ref=c.get('ground_truth_main_content') or c.get('reference_answer') or '本例按资源或结构规则评分，不以页面短摘要计算全文 F1。'
  (out/'references').mkdir(exist_ok=True);(out/'references'/f'{c["id"]}.txt').write_text(ref)
  if c.get('acceptance_contract'):
   target=contained_path(out,'contracts/'+c['id']+'.json');target.parent.mkdir(exist_ok=True)
   target.write_text(json.dumps(c['acceptance_contract'],ensure_ascii=False,indent=2)+'\n')
  facts=[('',cell(esc(f['id']))+cell(esc(' / '.join(f['alternatives'])))) for f in c.get('required_facts',[])]
  resources=[('',cell(esc(x['id']))+cell('<br>'.join(link(u,u) for u in x.get('urls',[]))+('<br>入口地址规则：<code>'+esc(' / '.join(x['url_patterns']))+'</code>' if x.get('url_patterns') else ''))) for x in c.get('expected_resources',[])]
  rules=''.join(f'<p>{esc(x["id"])}：至少 {x.get("minimum",1)} 项；<code>{esc(x["pattern"])}</code></p>' for x in c.get('required_patterns',[]))
  status_note=f'<p class="callout">参考状态：{reference_label(c)}。</p>'
  if c.get('resource_output_contract',{}).get('unexpected')=='reject':
   status_note+='<p class="callout">资源输出契约：只允许在答案图片字段中返回登记的目标、别名或匹配规则；额外图片会使任务未通过。</p>'
  if c.get('task_kind')=='structure':
   from .report_components import list_rule_description
   status_note='<p class="callout">'+esc(list_rule_description(c))+'</p><p>任务结果与耗时进入主榜；全文指标无完整参考时显示 —。</p>'
   if (out/'list-references'/f'{c["id"]}.json').exists():status_note+='<p>'+link('../list-references/'+c['id']+'.json','名称与链接核验依据')+'</p>'
   rules='<div class="guide-grid"><article class="guide-card"><h3>登记任务规则</h3><p>'+esc(list_rule_description(c))+'</p></article><article class="guide-card"><h3>稳定内容与变化字段</h3><p>稳定标题、栏目、说明按登记内容核对；频繁变化的名单、条目标题、排序、价格或 ID 不固定为旧快照取值，按声明的字段结构、类型、数量及唯一性核验。登录页、重复入口、空链接不计入。</p></article></div>'
  if c.get('acceptance_contract'):
   facts=[]
   rules=stable_contract_details(c)
  source_record=link('../sources/'+c['id']+'/evidence.json','获取记录') if (out/'sources'/c['id']/'evidence.json').exists() else '未保存源页快照'
  if c.get('track')=='dynamic':
   source_record+=' · '+link('../sources/'+c['id']+'/initial.html.txt','初始 HTML')+' · '+link('../sources/'+c['id']+'/rendered.txt','渲染正文')+' · '+link('../sources/'+c['id']+'/render-audit.json','配对证据校验')
   observed=sorted({r['provider'] for r in bycase[c['id']]})
   status_note+='<p class="source-note">动态网站正文已测方法：'+esc('、'.join(observed))+'。未测方法不计为失败。</p>'
  body=hero(esc(c['name']),'<strong>任务 ID：<code>'+esc(c['id'])+'</code></strong><br>'+esc(task_instruction(c)))+section('标准答案与来源',status_note+f'<p>{link(c["url"],"打开任务源页 ↗")} · {link("../references/"+c["id"]+".txt","下载标准答案")} · {source_record}</p><div class="evidence-pane"><header><h3>本任务的标准答案 / 目标结果</h3><p>下方直接展示用于判定的完整内容；长内容可在栏内滚动。</p></header><div class="pane-body"><pre data-kind="reference">{esc(ref)}</pre></div></div><details class="technical-details"><summary>查看来源记录</summary><p>{esc(v.get("method"))} · {esc(v.get("checked_at"))}</p><p>{esc(v.get("note"))}</p></details>')
  body+=section('评分核对点',(table(['核对点','允许表达'],facts) if facts else '')+rules+(table(['资源','允许地址'],resources) if resources else '')+('<p>检查是否返回可核对的原始文件。</p>' if c.get('task_kind')=='file' else ''),'facts')
  if c.get('task_kind')=='structure':
   example=out/'sources'/c['id']/'items.json'
   if example.exists():
    items=json.loads(example.read_text())
    sample=[('',cell(esc(x.get('title') or x.get('text','')))+cell(link(x['url'],'详情 ↗') if x.get('url') else '—')) for x in items]
    body+=section('公开条目快照','<p class="source-note">采集时的公开字段作为来源证据保留，不要求返回相同名单或排序；稳定任务按上方内容锚点与字段要求判定。</p><details><summary>展开 '+str(len(items))+' 条</summary>'+table(['名称 / 内容','入口'],sample)+'</details>')
  if c.get('body_requirements'):
   rules=c['body_requirements']
   region_cards=[];total=len(rules['regions'])
   for index,region in enumerate(rules['regions']):
    label=_position_label(index,total)
    region_cards.append(f'<article class="region-card" id="reference-region-{index+1}"><div class="region-head"><div><h3>{index+1}. {esc(label)}</h3><p>这是同一份标准答案在该位置的原文，不是独立任务。</p></div></div><pre data-kind="reference">{esc(region["text"])}</pre></article>')
   if rules.get('version')==3:
    policy=(f'每段硬下限 {pct(rules["minimum_floor"])}；至少 '
            f'{rules["minimum_region_passes"]}/{len(rules["regions"])} 段达到 '
            f'{pct(rules["minimum_coverage"])}。')
   else:policy=f'每段至少达到 {pct(rules["minimum_coverage"])}。'
   body+=section('如何检查是否抓全',f'<p class="decision-lead"><b>这是一份完整答案，不是 {total} 个任务。</b> 只检查标题和几个关键词，可能让“只抓到开头”的残缺结果通过。因此系统按前后位置检查同一份标准答案。{policy} 比对忽略空白、排版符号和 Markdown 链接地址；每次执行页会并列显示各位置的标准原文和实际证据。</p>'+''.join(region_cards))
  body+=section('全部方法执行结果',table(['方法']+headers[1:],[(attrs,values.replace(esc(c['name']),esc(provider_label(r['provider'])),1)) for r in bycase[c['id']] for attrs,values in [attempt_row(r,c)]]))
  write(out,'datasets/'+c['id']+'.html',c['name'],body)
 for name,rs in byprovider.items():
  info=method_info(rs[0]['provider_type'])
  controls=filter_controls([('track','类型',tracks),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(rs,cases))
  body=hero(esc(provider_label(name)),esc(info['contract']))+section('本轮配置',f'<p><code>{esc(info["endpoint"])}</code></p><p>{esc(info["freshness"])}</p><p>{esc(provider_configuration(name,rs,info))}</p>')
  price_anchor='pricing-'+name if name in {'firecrawl','tavily','exa','cloudflare-markdown'} else 'method-guide-pricing'
  body+=section('方法资料','<p class="actions">'+link('../adoption/index.html#'+price_anchor,'价格与计费 →')+link('../adoption/index.html#interface-'+name,'接口与示例 →')+'</p>')
  body+=section(f'{len(rs)} 条执行记录',controls+table(headers,[attempt_row(r,cases[r['case_id']]) for r in rs],'filtered-table'))
  write(out,'providers/'+name+'.html',name,body)
 for r in records:
  c=cases[r['case_id']];m=r['metrics'];ref=c.get('ground_truth_main_content') or c.get('reference_answer') or '检查下列目标资源。'
  output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
  rows=[('',cell(k)+cell(v)) for k,v in [('任务 ID',f'<code>{esc(c["id"])}</code>'),('方法',link('../providers/'+r['provider']+'.html',provider_label(r['provider']))),('请求','成功' if r['ok'] else '失败'),('任务结果','通过' if succeeded(r,c) else '未通过'),('HTTP 状态',esc(r.get('status_code'))),('耗时',ms(r['elapsed_ms'])),('全文完整度 / 纯净度 / F1',f'{pct(m.get("word_recall"))} / {pct(m.get("word_precision"))} / {pct(m.get("word_f1"))}'),('输出字符',f'{len(output):,}'),('输出校验值',f'<code class="break">{esc(m.get("content_sha256"))}</code>')]]
  if not c.get('ground_truth_main_content'):
   rows[6]=('',cell('全文完整度 / 纯净度 / F1')+cell('— / — / —（全文指标无完整参考，未评测）'))
  else:
   from .report_components import comparable_body_reference
   if not comparable_body_reference(c):
    rows[6]=('',cell('快照文字诊断 · 完整度 / 纯净度 / F1')+cell(' / '.join(pct(m.get(k)) for k in ['word_recall','word_precision','word_f1'])+'；参考含不固定取值的字段，仅为历史快照诊断，不进入主表全文均值。'))
  if r.get('status_code') is None:
   rows[4]=('',cell('HTTP 状态')+cell('—（接口未提供）'))
  if c.get('task_kind')=='file':
   rows[7]=('',cell('文本输出字符')+cell(f'{len(output):,}（文件字节另列）'))
   rows[8]=('',cell('文本输出校验值')+cell(f'<code class="break">{esc(m.get("content_sha256"))}</code>'))
  if 'content_check_total' in m:
   rows.append(('',cell('文字核对覆盖')+cell(pct(m.get('content_check_coverage'))+f' · {m["content_check_hits"]}/{m["content_check_total"]} 组')))
  if c.get('task_kind')=='resource':
   rows.append(('',cell('资源召回率 / 纯净度')+cell(
       f'{pct(m.get("resource_recall"))} / {pct(m.get("resource_precision"))}')))
   if m.get('unexpected_resource_urls'):
    rows.append(('',cell('非目标资源')+cell('<br>'.join(
        f'<code class="break">{esc(url)}</code>' for url in m['unexpected_resource_urls']))))
  rows[3]=('',cell('任务结果')+cell(result_label(r,c)))
  if r.get('raw_request_status'):
   rows.append(('',cell('原始适配器状态')+cell(esc(json.dumps(r['raw_request_status'],ensure_ascii=False)))))
  rows.append(('',cell('评分实现 SHA-256')+cell(esc(m.get('scoring_source_sha256')))))
  title_details=''
  if 'title_quality' in r:
   q=r['title_quality']
   rows.append(('',cell('标题完整度 / 标题纯净度 / 标题 F1')+cell(' / '.join(pct(q[k]) for k in ['recall','precision','f1']))))
   rows.append(('',cell('标题参考覆盖')+cell(f'{q["scored_titles"]}/{q["returned_titles"]} 条；仅衡量已返回且有独立参考的标题')))
   checks=[('',cell(esc(x['title']))+cell(esc(x['reference_used'] or '无独立名称参考，未评分'))+cell(link(x['url'],'详情链接'))+cell(' / '.join(pct(x[k]) for k in ['recall','precision','f1']))) for x in q['checks']]
   steps=(f'<ol class="quality-steps"><li><b>读取条目</b><span>实际返回 {q["returned_titles"]} 个带详情链接的标题。</span></li>'
          f'<li><b>对齐独立参考</b><span>{q["scored_titles"]}/{q["returned_titles"]} 个标题能按详情链接找到参考名称。</span></li>'
          f'<li><b>比较标题文字</b><span>完整度 / 纯净度 / F1 为 '+ ' / '.join(pct(q[k]) for k in ['recall','precision','f1']) + '。</span></li>'
          '<li><b>解释结果</b><span>该分数只描述已返回标题的文字质量，不参与本任务通过判定。</span></li></ol>')
   comparison=(table(['返回标题','参考标题','条目身份','完整度 / 纯净度 / F1'],checks,metrics=[])
               if checks else '<p class="empty">本次没有可逐条对照的返回标题，因此不显示空表。</p>')
   title_details=steps+'<details><summary>逐条标题对照与来源</summary><p>'+link('../'+q['reference_path'],'独立参考')+' · 采集时间 '+esc(q.get('reference_checked_at'))+'</p>'+comparison+f'<details class="technical-details"><summary>技术校验值</summary><p>参考 SHA-256：<code class="break">{esc(q["reference_sha256"])}</code></p><p>评分实现 SHA-256：<code class="break">{esc(q["scorer_sha256"])}</code></p></details></details>'
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   rows[3]=('',cell('任务结果')+cell(result_label(r,c)))
   if c.get('source_benchmark',{}).get('verification_scope')=='first_three_name_detail_pairs':
    rows.append(('',cell('名称—链接身份核验')+cell(f"{m.get('confirmed_entries',0)} 条已确认；不代表事实准确性或实时性。")))
   rows.append(('',cell('具名详情入口')+cell(str(m.get('named_entry_count',0)))))
  if 'stable_task_pass' in m:
   for label,key in [('稳定内容检查','stable_content_pass'),('条目结构检查','stable_structure_pass')]:
    no_checks=key=='stable_content_pass' and not (m.get('stable_anchor_results') or m.get('stable_page_pattern_results'))
    rows.append(('',cell(label)+cell('—（未设置）' if no_checks else '—' if m.get(key) is None else '通过' if m[key] else '未通过')))
   for label,key in [('结构类型','stable_task_schema'),('检出条目数','stable_record_count'),('完整条目数','stable_complete_record_count'),('完整且唯一条目数','stable_unique_record_count'),('声明最少条目数','stable_minimum_records'),('声明最多条目数','stable_maximum_records')]:
    rows.append(('',cell(label)+cell(esc(m.get(key) if m.get(key) is not None else '—'))))
  elif 'effective_entries' in m:
   minimum=m.get('minimum_entries',m.get('minimum'))
   rows.append(('',cell('有效条目数 / 声明最少数量')+cell(esc(m['effective_entries'])+' / '+esc(minimum if minimum is not None else '未提供'))))
  rows.extend(('',cell(k)+cell(f'<code class="break">{esc(v)}</code>')) for k,v in [('运行 ID',r.get('run_id') or execution_id(r)),('执行记录',r['attempt_id']),('开始时间（UTC）',r.get('started_at','—')),('供应商请求 ID',r.get('provider_metadata',{}).get('request_id') or '未返回')])
  scoring=r.get('scoring') or {}
  for label,key in [('评分方式','mode'),('评分时间','scored_at'),('规则校验值','rule_sha256')]:
   if scoring.get(key) is not None:
    value='保存输出离线评分' if key=='mode' and scoring[key]=='offline_rescore' else scoring[key]
    rows.append(('',cell(label)+cell(esc(value))))
  checks=[('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',x.get('expected_urls',[])))))+cell('命中' if x['pass'] else '条目未完整匹配' if x.get('match_scope')=='entry' else '缺失')+cell(esc(' / '.join(x.get('matched',x.get('matched_urls',[])))))) for x in m.get('fact_results',[])+m.get('resource_results',[])]
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   checks=[('',cell(link(x['url'],x['title']))+cell(esc(' / '.join(x['reference_titles']) or '没有匹配的独立名称证据'))+cell('已确认' if x['verdict']=='confirmed' else '访问受限 · 未证实' if x.get('access_restricted') else '未确认')+cell(esc(' · '.join(filter(None,[x.get('evidence_checked_at'),x.get('access_note')]))))) for x in m.get('list_checks',[])]
  caveat=''
  if c.get('track')=='dynamic':caveat='<p class="source-note">按本例实际登记规则判定。“条目未完整匹配”表示未找到完整字段组合，不表示每个字段都不存在。稳定标题、栏目、说明按登记内容核对；频繁变化的名单和价格按字段结构、类型、数量及唯一性核验，不固定旧快照取值。全文指标仅在有完整、可比的参考时另算，不证明变化值的事实准确性或实时性。</p>'
  if c.get('task_kind')=='structure':
   from .report_components import list_rule_description
   caveat='<p class="source-note">'+esc(list_rule_description(c))+link('../datasets/'+c['id']+'.html','规则与独立参考')+'</p>'
  if 'stable_task_pass' in m:
   caveat='<p class="source-note">本次任务按稳定内容与条目结构共同判定。全文指标须有完整、可比的参考，不能由条目数量推算。</p>'
  detail_metrics=['请求','结果','耗时','HTTP 状态','输出字符','输出校验值']+(['具名详情入口'] if c.get('task_kind')=='structure' else ['完整度','纯净度','F1'] if c.get('ground_truth_main_content') else [])
  state=result_label(r,c);passed=succeeded(r,c)
  provider_meta=r.get('provider_metadata',{})
  if provider_meta.get('document_extractor'):
   route_label=f'处理路线：方法直接取得原始文档 → {provider_meta["document_extractor"]} → 可读正文'
  elif c.get('task_kind')=='file':
   route_label='处理路线：方法直接取得原始文件 → 格式、长度与 SHA-256 验收'
  else:
   extractor=str(provider_meta.get('extractor') or '')
   route_label='处理路线：'+ROUTE_LABELS.get(extractor,extractor or '参评方法直接返回正文')
  delivered_summary=provider_meta.get('file_evaluation',{}).get('files',[])
  actual_summary=(f'实际交付：{len(delivered_summary)} 个文件、{sum(x.get("bytes",0) for x in delivered_summary):,} 字节'
                  if c.get('task_kind')=='file' else f'实际输出：{len(output):,} 字符')
  hero_meta=(f'<strong>任务 ID：<code>{esc(c["id"])}</code></strong><br>{esc(task_instruction(c))}'
             f'<span class="meta"><span class="pill">方法：{esc(provider_label(r["provider"]))}</span>'
             f'<span class="pill">运行 ID：{esc(r.get("run_id") or execution_id(r))}</span></span>')
  summary=(f'<div class="attempt-verdict {"" if passed else "failure"}">'
           f'<span class="status-line">{esc(state)}</span><p>{esc(reason(r,c))}</p>'
           f'<p class="source-note">方法：{link("../providers/"+r["provider"]+".html",provider_label(r["provider"]))} · '
           f'请求：{"成功" if r["ok"] else "失败"} · 耗时：{ms(r["elapsed_ms"])} · {actual_summary}</p></div>')
  summary+=f'<p class="source-note">{esc(route_label)}</p>'
  summary+=('<p class="actions"><a href="#evidence-reference">查看判定使用的标准答案</a>'
            '<a href="#evidence-output">查看本次完整实际返回</a>'
            '<a href="#decision">查看逐项判定</a>'
            '<button type="button" class="call-log-button" data-call-log-open="call-log-dialog" aria-haspopup="dialog">打开完整调用记录</button></p>')
  if r.get('diagnosis') and not passed:
   d=r['diagnosis'];s=d.get('independent_source_check') or {}
   quote_issue=_quote_field_issue(m)
   facts=[('失败分类','字段边界丢失' if quote_issue else d['category']),
          ('发生阶段','字段解析' if quote_issue else d['stage']),
          ('判定依据',reason(r,c)),('归因边界',d['certainty'])]
   if s:facts.extend([('独立源页复查',f"{s.get('checked_at','')} · HTTP {s.get('status','未取得')} · {s.get('title') or s.get('error','')}"),('复查边界',d['source_check_boundary'])])
   summary+='<details class="technical-details"><summary>查看失败分类与归因边界</summary>'+table(['项目','依据'],[('',cell(esc(k))+cell(esc(v))) for k,v in facts],metrics=[])+"</details>"
  body=hero(esc(c['name']),hero_meta)+section('结论',summary,'overview')
  reviewed=audited_attempts.get(r['attempt_id'])
  if reviewed:
   independent='通过' if reviewed.get('agent_verdict')=='pass' else '未通过'
   final='通过' if reviewed.get('final_verdict')=='pass' else '未通过'
   review_rows=[
    ('独立核验结论',independent),
    ('直接证据',reviewed.get('agent_evidence') or '—'),
    ('最终结论',final),
   ]
   if reviewed.get('adjudication'):
    review_rows.append(('分歧裁定',reviewed['adjudication']))
   body+=section('独立结果核验',table(
       ['项目','核验记录'],
       [('',cell(esc(label))+cell(esc(value))) for label,value in review_rows],
       metrics=[]),'independent-review')
  reference_heading='完整标准答案' if c.get('task_kind')!='structure' else '标准答案与验收规则'
  reference_kind='reference';reference_download='../references/'+c['id']+'.txt'
  source_body=f'<pre data-kind="reference">{esc(ref)}</pre>'
  source_meta=f'{len(ref):,} 字符 · {link(reference_download,"下载完整标准答案")}'
  delivered=r.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[])
  if 'stable_task_pass' in m and c.get('acceptance_contract'):
   reference_kind='contract';reference_download='../contracts/'+c['id']+'.json'
   contract_text=json.dumps(c['acceptance_contract'],ensure_ascii=False,indent=2)
   source_body=(stable_answer_panel(c['acceptance_contract'])+
                '<details class="technical-details"><summary>查看机器可读契约</summary>'
                f'<pre data-kind="contract">{esc(contract_text)}</pre></details>')
   source_meta='这里直接列出本任务真正要求的文字和记录数量，不要求用户阅读 JSON。'
  corruption=''
  if m.get('text_encoding_corruption'):
   corruption=(f'<p class="callout failure encoding-warning"><b>实际输出存在解码损坏：</b>'
               f'含 {m.get("replacement_character_count",0):,} 个 �，占 {pct(m.get("replacement_character_ratio"))}。'
               '这些字符在保存前已丢失，不能从当前 Markdown 还原。</p>')
  output_note=(f'{len(output):,} 字符 · '+link('../'+r['artifact_path'],'下载原始输出'))
  if readable_output(output)!=output:output_note+=' · 控制字节在页面中显示为 \\xNN'
  actual_body=(corruption+f'<pre data-kind="output">{esc(readable_output(output))}</pre>' if output else
               f'<div class="call-log-empty">{esc(empty_output_reason(r,c,output))}</div>')
  if c.get('task_kind')=='file':
   reference_heading='目标文件'
   expected_hash=c.get('source_verification',{}).get('file_sha256') or '未登记'
   source_meta='本任务验收原始文件字节，不要求 Markdown。'
   source_body=(f'<div class="answer-list"><div class="answer-item"><b>文件要求</b><blockquote>{esc(ref)}</blockquote></div>'
                f'<div class="answer-item"><b>目标 SHA-256</b><code class="break">{esc(expected_hash)}</code></div></div>')
   if delivered:
    actual_rows=[('',cell(esc(f.get('mime') or '未知格式'))+cell(f'{f.get("bytes",0):,}')+
                  cell(f'<code class="break">{esc(f.get("sha256"))}</code>')+cell('匹配' if f.get('pass') else '未匹配')) for f in delivered]
    actual_body=table(['识别格式','字节数','SHA-256','目标校验'],actual_rows,metrics=[])
    output_note=f'实际交付 {len(delivered)} 个文件 · '+link('../'+delivered[0]['artifact_path'],'下载交付文件')
   else:
    actual_body='<p class="empty">本次没有交付可校验的原始文件字节。</p>'
    output_note='实际交付 0 个文件'
  reference_scope=('' if not (c.get('track')=='document' and 0 < len(ref) < 100) else
                   f'<p class="callout"><b>参考范围：</b>这是最小文档解析样本。冻结源文件的全部可提取正文共 {len(ref):,} 个字符，标准答案没有省略。它只验证短文档文字提取，不用于证明多页、表格或附录完整性。</p>')
  evidence=(f'<p class="evidence-intro">左边是判定时使用的标准答案或明确验收规则，右边是该次调用原样保存的完整输出。两栏都是真实内容，不是摘要；长内容可在栏内滚动。</p>'+reference_scope+
            f'<div class="evidence-compare"><article class="evidence-pane" id="evidence-reference"><header><h3>{esc(reference_heading)}</h3><p>{source_meta}</p></header><div class="pane-body">{source_body}</div></article>'
            f'<article class="evidence-pane" id="evidence-output"><header><h3>实际返回</h3><p>{output_note}</p></header><div class="pane-body">{actual_body}</div></article></div>')
  body+=section('标准答案与实际返回',evidence,'evidence')
  checks_html=''
  if 'stable_task_pass' in m:
   checks_html+=stable_anchor_evidence(m,output)
   checks_html+=(f'<p class="decision-lead"><b>结构结果：</b>实际解析出 {m.get("stable_unique_record_count",0)} 条完整且不重复的记录，'
                 f'要求至少 {m.get("stable_minimum_records","—")} 条；结构检查{("通过" if m.get("stable_structure_pass") else "未通过")}，'
                 f'稳定文字检查{("通过" if m.get("stable_content_pass") else "未通过")}。</p>')
   checks_html+='<details class="technical-details"><summary>查看解析出的全部记录和机器检查字段</summary>'+stable_checks(m)+'</details>'
  else:
   checks_html+=(table(['核对点','标准答案','判定','实际命中'],checks) if checks else '<p class="decision-lead">本例按任务结构或原文件输出判定。</p>')
   checks_html+=''.join(f'<p>{esc(x["id"])}：实际 {x["count"]}，要求至少 {x["minimum"]}。</p>' for x in m.get('pattern_results',[]))
  if c.get('task_kind')=='resource' and m.get('resource_candidates'):
   unexpected=set(m.get('unexpected_resource_urls',[]))
   candidate_rows=[('',cell(f'<code class="break">{esc(item["url"])}</code>')
                    +cell(esc('、'.join(item.get('sources',[]))))
                    +cell('非目标资源' if item['url'] in unexpected else '目标或允许变体'))
                   for item in m['resource_candidates']]
   checks_html+=table(['候选资源地址','答案字段来源','范围判定'],candidate_rows,metrics=[])
  if m.get('stable_field_results'):
   checks_html+=table(['记录字段','有效记录数','至少要求','判定'],[('',cell(esc(x['field']))+cell(str(x['count']))+cell(str(x['minimum']))+cell('通过' if x['pass'] else '未通过')) for x in m['stable_field_results']],metrics=[])
  body+=section('为什么这样判',caveat+checks_html,'decision')
  if m.get('body_region_results'):
   region_rows,region_cards=body_region_rows(c,m,output);rules=c.get('body_requirements',{})
   required=rules.get('minimum_region_passes',len(rules.get('regions',[])))
   region_count=len(rules.get('regions',[]))
   region_intro=(f'<div class="decision-lead"><b>这项分数怎样计算：</b>评分使用上方完整标准答案和完整实际返回，不使用下面展示的短定位样例。'
                 '两边先统一大小写和常见排版差异，去掉 Markdown 链接地址、空白及标点，再切成连续六字符片段。'
                 f'标准答案按前后位置分成 {region_count} 份；每份覆盖率 = 在完整实际返回中找到的片段数 ÷ 该份标准片段总数。'
                 f'本任务要求每个位置至少达到 {pct(rules.get("minimum_floor",rules.get("minimum_coverage")))}，且至少 {required}/{region_count} 个位置达到 {pct(rules.get("minimum_coverage"))}。'
                 '所有位置同时做全局容量分配，且同一个实际片段实例只能计入一个位置；因此重复文字不会被多次借用，位置排列顺序也不会改变判定。全文完整度、纯净度和 F1 仍按完整文本另行计算。</div>')
   body+=section('正文是否抓全',region_intro+table(['标准答案位置','标准片段数','命中片段数','覆盖率','判定'],region_rows,metrics=[])+
                 '<details class="technical-details"><summary>查看各位置的完整标准正文和人工定位样例</summary>'+''.join(region_cards)+'</details>','body-coverage')
  if title_details:body+=section('标题文字质量',title_details)
  runtime=('<details class="technical-details"><summary>查看完整运行、哈希与评分字段</summary>'
           +metric_legend(detail_metrics)+table(['项目','值'],rows,metrics=[])+
           f'<details><summary>接口返回的公开附加字段</summary><pre data-kind="metadata">{esc(json.dumps(public_provider_metadata(r.get("provider_metadata",{})),ensure_ascii=False,indent=2))}</pre></details></details>'
           f'<p class="actions">{link(c["url"],"打开源页 ↗")}{link("../datasets/"+c["id"]+".html","查看该任务的全部方法")}</p>')
  body+=section('运行与评分信息',runtime,'run-info')
  body+=call_log_dialog(out,r,c,output)
  if r.get('provider_metadata',{}).get('rendered_html_path'):
   body+=section('浏览器 HTML 证据','<p>'+link('../'+r['provider_metadata']['rendered_html_path'],'下载转换前的完整 DOM HTML')+'</p>')
  if r.get('provider_metadata',{}).get('raw_stdout_path'):
   metadata=r['provider_metadata'];extractor=metadata.get('document_extractor')
   if extractor:
    body+=section('原始文档证据','<p>'+link('../'+metadata['raw_stdout_path'],'下载方法直接取得的原始文档')+f'。正文由 <code>{esc(extractor)}</code> 在同一次调用的计时预算内提取；上方“实际返回”展示的是送入统一评分器的文本。</p>')
   else:
    body+=section('原始响应证据','<p>'+link('../'+metadata['raw_stdout_path'],'下载方法直接取得的原始响应字节')+'。若任务要求正文而响应没有形成可读文本，会按实际结果判为未通过。</p>')
  if delivered:
   file_rows=[('',cell(link('../'+f['artifact_path'],'下载交付文件'))+cell(esc(f.get('mime')))+cell(str(f['bytes']))+cell('<code class="break">'+esc(f['sha256'])+'</code>')+cell('匹配' if f.get('pass') else '未匹配')) for f in delivered]
   body+=section('原始文件字节证据',table(['文件','格式特征','字节数','SHA-256','目标校验'],file_rows,metrics=[]))
  write(out,'attempts/'+r['attempt_id']+'.html',c['name']+' · '+r['provider'],body)
 controls=filter_controls([('provider','方法',[(n,provider_label(n)) for n in names]),('track','类型',tracks),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(records,cases))
 comparison=section('方法组合对比','<p class="source-note">按同一任务逐项比较，不用各方法不同的成功样本拼接结论。成功与失败均沿用主榜已核验判定；点击方法可查看标准答案、实际返回和原因。</p>'+method_comparisons(cases,bycase,names),'method-comparison')
 write(out,'analysis/index.html','结果分析',hero('结果分析','先比较方法在同一任务上的成败组合，再筛选全部执行记录。')+comparison+section('全部执行记录',controls+table(['方法']+headers,[(case_attrs(r,cases[r['case_id']]),cell(link('../providers/'+r['provider']+'.html',provider_label(r['provider'])))+attempt_row(r,cases[r['case_id']])[1]) for r in records],'filtered-table')))
 write_support(out,records,manifests)
 from .report_changes import render_changes
 render_changes(out,cases,records,dataset)
 from .report_i18n import write_english_report
 write_english_report(out)
 return out/'index.html'


def write_review_page(out,records):
 path=out/'result-review.json'
 if not path.is_file():
  write(out,'review/index.html','核验记录',hero('核验记录','本轮没有绑定独立核验记录。'))
  return
 audit=json.loads(path.read_text());reviewer=audit.get('reviewer',{})
 metadata_path=Path(__file__).resolve().parents[2]/'research/report-changes.json'
 if not metadata_path.is_file():
  raise ValueError('A reviewed report requires research/report-changes.json')
 changes=json.loads(metadata_path.read_text())
 from .report_changes import require_review_rounds
 rounds=require_review_rounds(changes)
 rounds_html=''.join(
  f'<article class="review-round"><div class="round-label">第 {i} 轮<br><small>{esc(row.get("date",""))}</small></div><div><h3>{esc(row.get("title",""))}</h3><p><b>范围：</b>{esc(row.get("scope",""))}</p><p><b>发现：</b>{esc(row.get("finding",""))}</p><p><b>纠正：</b>{esc(row.get("fix",""))}</p><p class="round-result"><b>未完成：</b>{esc(row.get("pending",""))}</p></div></article>'
  for i,row in enumerate(rounds,1))
 page=hero('核验记录','记录每轮核验的范围、发现、纠正和未决事项。')
 reviewed_attempts=audit.get('attempts',[])
 disagreements=[row for row in reviewed_attempts
                if row.get('agent_verdict') != row.get('machine_verdict')]
 adjudicated=[row for row in disagreements if row.get('final_verdict') in {'pass','fail'}]
 unresolved=audit.get('unresolved',0)
 def coverage(label,item):
  scope=item.get('review_protocol',{}).get('scope','all')
  if scope=='machine-failures':
   return (f'{label}机器失败 {item.get("reviewed_attempts",0):,}/{item.get("expected_attempts",0):,}；'
           f'机器通过未逐条审查 {item.get("accepted_unreviewed_passes",0):,}')
  return f'{label}全量 {item.get("reviewed_attempts",0):,}/{item.get("total_attempts",item.get("expected_attempts",0)):,}'
 conclusion=(f'<p class="callout success">{coverage("主结果",audit)}；'
             f'代理分歧 {len(disagreements)}；已裁定 {len(adjudicated)}；未决 {unresolved}。</p>')
 page+=section('当前状态',conclusion)
 page+=section('核验轮次','<div class="review-rounds">'+rounds_html+'</div>')
 method=(f'<details class="technical-details"><summary>核验协议与执行者</summary><p>执行者：{esc(reviewer.get("agent","独立代理"))}；模型：{esc(reviewer.get("model","—"))}；推理强度：{esc(reviewer.get("effort","—"))}。'
         '核验时隐藏机器结论和分数，只提供任务契约、标准答案与完整实际输出；分歧回到原始证据裁定，未决非零则禁止发布。任务和输出未变化时沿用原核验；发生变化的调用必须重新盲审。</p></details>')
 page+=section('核验口径',method)
 write(out,'review/index.html','核验记录',page)

def write_support(out,records,manifests):
 headless=is_headless_cohort(records)
 from .report_components import task_contract_guide
 methodology=hero('评测方法','按任务定义验收规则。同组已测方法使用相同的冻结任务与参考，覆盖数量在各榜单列明。')
 if headless:
  methodology+=section('从参考到发布','<div class="guide-grid"><article class="guide-card"><h3>1 · 冻结参考</h3><p>独立采集源页；需要渲染的页面另做浏览器观测，通过完整范围校验后才冻结。</p></article><article class="guide-card"><h3>2 · 执行调用</h3><p>同一 URL、任务和截止时间；各方法使用其最快的正式任务原生产物路线，保留版本、输出与哈希。</p></article><article class="guide-card"><h3>3 · 测后核验</h3><p>独立重取精确参考页，确认调用期间标准答案未改变；不把参考变化算成浏览器失败。</p></article><article class="guide-card"><h3>4 · 结果独立审查</h3><p>强代理逐次读取完整输出、标准答案与任务规则；分歧必须裁定，未裁定项会阻止构建。</p></article></div>'+review_summary(out,'../'),'评测流程')
 examples_html=re.sub(r'<a href="../datasets/([^"#]+).html">([^<]*)</a>',lambda m:m[0] if (out/'datasets'/(m[1]+'.html')).exists() else m[2],examples())
 methodology+=section('每类怎么测',examples_html)+section('指标说明',metric_guide())
 methodology+=section('共同输入、计时边界与调用预算','<p>所有方法进入同一个评分入口，但按任务交付物验收，而不是强制使用同一种中间格式。网页与文档正文最终验收可读文本；原始文件任务验收 PDF、DOCX、图片或视频字节；结构任务验收字段和稳定文字。HTML/Markdown 链接与明确配对的结构化名称—链接字段按同一规则核验；不把请求回显或诊断日志当成答案。原始输出与原始适配器状态保留，报告层不按方法名称改写任务结果。</p><div class="guide-grid"><article class="guide-card"><h3>计入耗时</h3><p>从准备发起该次 provider 调用开始，到形成可送入统一评分器的最终答案为止：线程调度、SDK/CLI/HTTP 调用、远端处理与网络下载、浏览器渲染与正文提取、本地解析、统一输入转换，以及文件字节读取、格式识别和 SHA-256 计算均在计时内。</p></article><article class="guide-card"><h3>不计入耗时</h3><p>数据集与参考加载、provider 配置校验、调用间限速等待、最终答案形成后的证据落盘、统一评分计算、报告汇总与页面生成不计入。供应商上报的服务端耗时只作附加证据，排名使用客户端观测的端到端耗时。</p></article></div><p>这个边界比较的是“拿到本任务最终交付物”所需时间：服务直接返回成品时计完整往返；本地方法若还需下载、渲染或解析，也必须完成后才停表。正文任务中，方法只返回原始 PDF 或 DOCX 时，本地正文解析也计时；原始文件任务则到字节校验完成即停止，不做无关的 Markdown 转换。所有方法使用同一外层计时器和截止预算。超时答案不接收；无法取消的调用会被隔离，整批结果因此不完整并触发不可靠状态，构建产物不得发布。</p><p>完整正文任务除原有核对点外，按参考正文最多五个连续位置检查字符六元组覆盖：每个位置至少达到 60%，通常要求至少四处达到 80%。所有位置一次性做全局容量分配；同一返回片段实例只能计入一个位置，位置排列顺序不影响结论。若页面存在经独立证据确认的合法重排，案例可显式登记不同位置门槛。比较忽略空白和排版符号但保留文字顺序；全文 F1 独立保留实际值。文件任务必须有字节验收，资源 URL 不等于文件。</p>')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  methodology+=section('资源测量边界','<p>CPU、内存和网络使用独立的 20 题同窗队列，不回填历史正确率调用。每次调用在受限的 Linux 容器中运行，使用 cgroup v2 累计 CPU、独立网络命名空间累计收发字节，并以 50 ms 采样内存与瞬时速率。容器固定为 2 个逻辑 CPU、2 GiB 内存和 512 个 PID；宿主控制器与容器启停不计入。</p><p>资源汇总只纳入四种本地配置共同通过的任务，避免把快速失败当成节省资源，并覆盖容器内 worker、浏览器与转换过程。Kitesurf 的远端 CPU、内存和上游流量不可见，因此不进入资源表。缺少任一资源字段、非 Linux/cgroup v2、镜像或二进制身份变化、并发不为 1，都会阻断资源包构建。</p><p>CPU 与网络总量来自累计计数；内存峰值和速率分位数依赖 50 ms 采样，可能遗漏更短的尖峰。报告分别呈现 CPU、内存、网络和任务通过率，不合成总分。</p>')
 if (out/'scoring-revisions.json').exists():
  revision_links=[link('../scoring-revisions.json','评分规则修订收据')]
  if (out/'reference-revisions.json').exists():revision_links.append(link('../reference-revisions.json','参考答案修订收据'))
  if (out/'latency-corrections.json').exists():revision_links.append(link('../latency-corrections.json','时延纠正收据'))
  if (out/'request-status-corrections.json').exists():revision_links.append(link('../request-status-corrections.json','请求状态纠正收据'))
  detail='当前成绩包含对保存输出的离线重算；收据记录输入、输出哈希、判定变化及供应商调用数，不把新规则伪装成原调用时的运行配置。'
  if headless and (out/'latency-corrections.json').exists():
   latency_receipt=json.loads((out/'latency-corrections.json').read_text())
   changes=latency_receipt.get('changes',[])
   if changes:
    affected='、'.join(provider_label(name) for name in sorted({change['provider'] for change in changes}))
    detail+=(f' 当前报告的 {affected} 时延纠正仅替换 {len(changes)} 个耗时值，'
             f'统一取配对实验的第 {latency_receipt.get("selected_round", "—")} 轮；'
             '旧耗时和原始物理调用保留在离线证据，不能把跨批次视图当成同时重跑。')
  methodology+=section('评分来源与修订','<p>'+detail+' '+' · '.join(revision_links)+'</p>')
 methodology+=section('稳定任务契约与全文诊断',task_contract_guide(),'task-contracts')
 figures=''.join(f'<figure><img src="../assets/methodology/{name}.png" alt="{label}" style="max-width:100%;height:auto" loading="lazy"><figcaption>{label}</figcaption></figure>' for name,label in [('annotation','参考标注'),('validation','任务验收'),('scoring','评分口径')] if (out/'assets/methodology'/f'{name}.png').is_file())
 if figures:methodology+=section('方法图解',figures)
 methodology+=section('参考与匹配','<p>原始快照保留不变；评分参考清除零宽空格、BOM 和软连字符，并检查空正文、乱码、重复核对点及核对点是否存在于参考中。通过检查不等于语义完整性已获证明，正文范围仍以来源记录为准。</p><p>关键事实按规范化后的可见文本机械匹配：统一 Unicode、大小写及排版空白，菜单路径如 Settings &gt; AI 与 Settings&gt;AI 等价；忽略 Markdown 链接地址、行内代码与加粗标记。一个已被完整记录边界约束的字段允许丢失内联元素间空白，例如标签列表 <code>change deep-thoughts</code> 与 <code>changedeep-thoughts</code> 内容等价；相邻独立链接仍按独立词段核对，不猜测跨记录的粘连分词。保留数字、正负号、否定词、标点和英文词边界。不使用大模型或模糊相似度判定通过。</p>')
 methodology+=section('调用时间','<p>各方法使用同一套冻结任务与参考。不同批次的耗时不直接排名；同窗速度子集单独标识。实时网页内容、登录状态和站点策略可能变化，成绩只代表所记录的调用。环境、版本与命令见“环境与复现”。</p>')
 methodology+=section('变更后重跑范围','<p>不把“代码有更新”直接等同于全部重跑。只改展示时重建报告；只改裁判且原始输出足够时离线重算；某个参考变化时重跑该题的全部方法；新增或升级单个实现时重跑该方法的全部题；计时边界变化时废弃并重跑同窗测速。只有全局任务输入、调用边界或所有方法共同依赖发生变化，才需要全量重跑。</p>')
 methodology+=section('计算与边界','''<details><summary>全文评分公式</summary><p>完整度＝取回的参考词数 ÷ 参考词数；纯净度＝匹配的正文词数 ÷ 返回词数；F1＝2 × 完整度 × 纯净度 ÷ 两者之和。英文按词、中文按字，统一大小写与 Unicode 字符形式；Markdown 链接只计文字。对有完整参考的全部案例取平均；请求失败计 0，请求成功但任务未通过仍按实际正文评分，没有参考不计算。</p></details><details><summary>参考范围与公平性</summary><p>网页、文档使用独立保存的参考正文；仓库以 README 为准，列表只覆盖指定的已加载范围，不要求无限滚动或跨页翻页。登录页和验证码不能代替目标正文。参考正文自身包含的“404 Not Found”等术语不作为错误页判据。</p><p>不同方法并行，同一方法串行；正文结束后再测其他任务。Firecrawl 每次完成后冷却 6.1 秒。本报告不是 WCXB 官方榜单。</p><p>媒体网址只证明找到入口，不代表取得文件。文件任务没有另接下载器代替参评方法完成。</p></details>''')
 if headless:
  methodology=methodology.replace('Firecrawl 每次完成后冷却 6.1 秒。','五种方法每次使用新进程或新上下文。静态网页正文：Kitesurf 走 Cloudflare 账户级 /markdown，Lightpanda、Obscura 与 Moli 1.1.1 使用各自 CLI 正文能力，Chromium 取完整渲染 DOM 后由 node-html-markdown 2.0.0 转换。Moli 遇到直接 PDF、DOCX、图片或视频 URL 时使用不带 --dump 和页面等待参数的自动响应；正文任务在预算内解析可读文字，文件任务直接校验字节。原始主矩阵未启用失败重试，每次调用有 45 秒端到端预算。运行器可在测量前声明至多一次失败重试：保留全部物理证据和总耗时，并固定采用最后一次结果，不能择优。已确认的命令、版本、裁判、参考或测量错误可按完整声明范围重测并替换权威逻辑结果；旧证据不得删除。历史报告中的 Moli 1.0.6 与 1.1.3 证据继续归档，但不进入当前报告矩阵。方法之间并行、同一方法串行。')
 write(out,'methodology/index.html','评测方法',methodology)
 downloads=' · '.join(link('../'+path,label) for path,label in [('dataset.json','评测集与参考'),('results.jsonl','全部调用记录'),('summary.json','分类汇总'),('execution.json','执行批次')]+([('implementation-provenance.json','版本回溯证据')] if (out/'implementation-provenance.json').is_file() else [])+([('reference-revisions.json','参考修订收据')] if (out/'reference-revisions.json').is_file() else [])+([('scoring-revisions.json','评分修订收据')] if (out/'scoring-revisions.json').is_file() else [])+([('latency-corrections.json','时延纠正收据')] if (out/'latency-corrections.json').is_file() else [])+([('request-status-corrections.json','请求状态纠正收据')] if (out/'request-status-corrections.json').is_file() else []))
 reproduce='<p>在评测仓库中执行；读取 evaluation/ 的当前实测数据，不调用供应商接口。</p><pre>make setup\nmake report EVIDENCE=/absolute/path/to/evidence\nmake report-check</pre><p>输出目录：reports/webfetch/。固定的 Python 3.11 与 uv.lock 依赖不满足时会立即失败。凭据仅从本地环境加载，不包含在报告中。</p>'
 if headless:
  reproduce='<p>在评测仓库中执行；从明确指定的持久化冻结证据构建，不重新调用浏览器。</p><pre>make setup\nmake report EVIDENCE=/absolute/path/to/evidence\nmake report-check</pre><p>唯一输出目录为 reports/webfetch/。固定的 Python 3.11 与 uv.lock 依赖不满足时会立即失败。重新测量使用 uv run --frozen python scripts/run_headless_benchmark.py 和 configs/providers.headless.json；先独立采集并冻结参考，再调用，最后做测后核验。完整命令与版本见仓库 harness/web_fetch/HEADLESS.md。</p>'
 common=[]
 for label,path in [('平台',('environment','platform')),('Python',('environment','python')),('超时秒',('parameters','timeout')),('并发方式',('parameters','concurrency_scope'))]:
  values=sorted({str(m.get(path[0],{}).get(path[1],'—')) for m in manifests})
  common.append(('',cell(label)+cell(esc('；'.join(values)))))
 provenance={}
 if (out/'implementation-provenance.json').is_file():
  provenance={x['binary_sha256']:x for x in json.loads((out/'implementation-provenance.json').read_text()).get('implementations',[])}
 provider_rows=[]
 for name in sorted({r['provider'] for r in records}):
  subset=[r for r in records if r['provider']==name]
  versions=sorted({str(value) for r in subset for value in
                   [r.get('provider_metadata',{}).get('browser_version') or
                    r.get('provider_metadata',{}).get('library_version')]
                   if value})
  hashes=sorted({str(r.get('provider_metadata',{}).get('binary_sha256')) for r in subset if r.get('provider_metadata',{}).get('binary_sha256')})
  versions=versions or sorted({provenance[h].get('version') or provenance[h].get('identity') for h in hashes if h in provenance})
  deployments=sorted({str(r.get('provider_metadata',{}).get('deployment','—')) for r in subset})
  timings={provenance[h].get('identity_timing') for h in hashes if h in provenance}
  if not hashes:
   basis='托管后端未公开版本' if name=='kitesurf' else '无本地二进制'
  elif any(t and t.startswith('post_hoc') for t in timings):
   basis='事后与官方归档同哈希；原调用未记版本字符串'
  elif versions:
   basis='运行时版本与 SHA-256'
  else:
   basis='运行时 SHA-256；nightly 源修订不可得'
  provider_rows.append(('',cell(link('../providers/'+name+'.html',provider_label(name)))+cell(esc('；'.join(versions) or '版本未记录'))+cell(esc(basis))+cell(esc('；'.join(deployments)))+cell('<br>'.join('<code class="tiny break">'+esc(x)+'</code>' for x in hashes) or '—')+cell(str(len(subset)))))
 write_review_page(out,records)
 repo='https://github.com/lexmount/browser-eval'
 support=hero('环境与复现','代码、评测集和复现说明以 browser-eval 仓库为准；本页只列本轮必要配置。')
 support+=section('代码与评测集',f'<p>{link(repo,"browser-eval 仓库")}保存当前评测代码、任务定义和独立参考；本报告另提供本轮冻结结果供复核。</p><p>{downloads}</p>')
 if (out/'speed-cohort/speed-comparison.json').is_file():
  support+=section('同窗速度证据','<p>'+link('../speed-cohort/speed-comparison.json','速度汇总与样本分母')+' · '+link('../speed-cohort/results.jsonl','速度子集逐次记录')+'</p>')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  support+=section('隔离资源证据','<p>'+link('../resource-cohort/resource-comparison.json','CPU、内存、网络汇总与共同成功样本')+' · '+link('../resource-cohort/results.jsonl','资源子集逐次记录')+' · '+link('../resource-cohort/execution.json','容器环境与镜像身份')+'</p>')
 support+=section('公共条件',f'<p>最终报告纳入 {len(records):,} 次调用。任务与参考冻结后执行；每个方法内部串行，不复用个人登录状态。</p>'+table(['参数','值'],common,metrics=[]))
 support+=section('方法版本','<p>“事后核验”只说明留存二进制与官方归档完全一致，不改写成当时已经记录了版本。移动 nightly 无法追到不可得的源修订时，只用 SHA-256 标识精确可执行文件。</p>'+table(['方法','版本','身份依据','部署路径','二进制 SHA-256','调用数'],provider_rows,metrics=[]))
 support+=section('发布与加载','<p>本地构建为每个任务、方法和证据生成可独立检查的逻辑路由；这不等于线上部署同样数量的独立 HTML。发布器会把整份报告编译成一个通用 HTML 外壳、共享样式与脚本，以及按内容寻址的压缩数据块。页面切换由前端路由完成，证据只在打开对应页面时读取，超过 256 KiB 的完整文本还需用户主动加载。</p><p class="source-note">发布清单记录逻辑路由数、源文件数、压缩块数和逐文件 SHA-256；发布门禁会重建每个文件并验证内部链接与原始下载字节。这样保留逐项可审计性，同时避免重复页面骨架和一次性加载全部证据。</p>')
 if headless:
  reproduce+=f'<p>运行配置：<code>configs/providers.headless.json</code>；账号变量名称见本报告“账号”页，实际密钥只放仓库根目录忽略提交的 <code>.env</code>。冻结参考、完整重测与核验门禁见 {link(repo+"/blob/main/harness/web_fetch/HEADLESS.md","仓库复现说明")}。</p>'
 else:
  reproduce+='<p>方法配置、凭据变量和本轮实际版本分别见“方法介绍”“账号”和上方方法版本表；不存在的 headless 专用配置不属于本 cohort。</p>'
 support+=section('核心复现指令',reproduce)
 write(out,'reproduce/index.html','环境与复现',support)
 if headless:
  access_rows=[]
  for name in sorted({r['provider'] for r in records},key=lambda value:(provider_label(value).lower(),value)):
   access=HEADLESS_ACCESS[name]
   official='<br>'.join(link(url,label) for label,url in access['links'])
   variables='<br>'.join('<code>'+esc(value)+'</code>' for value in access['env_vars'])
   access_rows.append(('',cell(provider_label(name))+cell(esc(access['account']))+cell(official)+cell(variables)+cell(esc(access['configuration']))))
  account_intro='只有 Kitesurf 的账户级 Cloudflare Browser Run 路线需要供应商账号与凭据；其余六种方法是本地二进制，不需要账号，但仍需安装并明确指定可执行文件。'
  account_body=hero('账号与安装',account_intro)
  account_body+=section('本轮方法清单','<p>这里列的是完成本报告所需的全部外部准备：账号、官方获取入口和变量名称。变量值、本机路径、登录会话、Cookie 和 Token 均不进入报告。</p>'+table(['方法','账号要求','官方获取入口','变量名称','本轮配置'],access_rows,cls='accounts-table',metrics=[]))
  account_body+=section('公开内容边界','<p class="callout success"><b>报告只公开复现所需的名称和入口，不公开凭据值。</b> 发布门禁会扫描 HTML、JSON、下载文件和逐次输出中的常见 Token、私钥、Authorization 值与用户目录路径；第三方原始 HTML 中命中密钥形态的片段单独脱敏，计分输出保持逐字节不变。扫描结果和脱敏文件哈希随发布包保存。</p>')
  write(out,'accounts/index.html','账号与安装',account_body)
 else:
  accounts=report_access_registry(
   {r['provider'] for r in records},
   fixture_providers={r['provider'] for r in records if r.get('provider_type')=='fixture'},
  )
  accountrows=[('',cell(esc(a['provider']))+cell(link(a['registration_url'],'注册 / 登录')+'<br>'+link(a['key_url'],'获取凭据'))+cell('<br>'.join('<code>'+esc(x)+'</code>' for x in a['env_vars']))+cell(esc(a['steps']))+cell(esc(a['free_note']))) for a in accounts]
  write(out,'accounts/index.html','账号',hero('账号与凭据','只列官方入口和环境变量名称，不保存实际凭据。')+section('注册清单',table(['提供方','官方入口','环境变量','取得方式','免费 / 准入说明'],accountrows,cls='accounts-table')))
 from .report_methods import render_methods
 write(out,'adoption/index.html','方法介绍',render_methods(out,records,hero=hero,section=section,table=table,cell=cell,link=link,esc=esc))


_WMB_ROUTES=[
 ('datasets/index.html','评测集','Dataset'),('methodology/index.html','评测方法','Methodology'),
 ('analysis/index.html','结果分析','Analysis'),('review/index.html','核验记录','Review'),
 ('guide/index.html','报告解读','Guide'),('adoption/index.html','方法介绍','Methods'),
 ('reproduce/index.html','环境与复现','Reproduction'),('changes/index.html','变更记录','Changes'),
 ('accounts/index.html','账号与安装','Accounts and setup'),
]


def _wmb_write(out,path,title,body,english=False):
 if english:
  body=body.replace('<div class="eyebrow">Web Fetch · 实测报告</div>','<div class="eyebrow">Web Fetch · Measured report</div>')
  body=body.replace('aria-label="内容选择"','aria-label="Choose content"').replace('<th>序号</th>','<th>No.</th>')
 local=('en/'+path) if english else path;depth=len(Path(local).parts)-1;base='../'*depth
 nav=''.join(link(base+('en/' if english else '')+route,en if english else zh) for route,zh,en in _WMB_ROUTES)
 brand=link(base+('en/index.html' if english else 'index.html'),'Web Fetch')
 counterpart=base+path if english else base+'en/'+path
 language=f'<a href="{safe_href(counterpart)}">{"中文" if english else "English"}</a>'
 footer=('Scores come only from this preselected 200-page frozen run. They represent neither the live web nor the official 545-toolkit or 7,809-page leaderboards.' if english else '分数只来自本轮预选的 200 页冻结输入；不代表实时网页，也不等同于官方 545 例工具链榜单或 7,809 页论文榜单。')
 page=f'<!doctype html><html lang="{"en" if english else "zh-CN"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{nav}{language}<button class="theme-toggle" onclick="theme()">{"Theme" if english else "切换主题"}</button></nav></header><main>{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'
 page=page.replace(f'href="{base}assets/style.css"',f'href="{base}assets/style.css?v={hashlib.sha256((STYLE+EXTRA).encode()).hexdigest()[:16]}"').replace(f'src="{base}assets/report.js"',f'src="{base}assets/report.js?v={hashlib.sha256(JS.encode()).hexdigest()[:16]}"')
 target=contained_path(out,local);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')


def _wmb_category(case,axis):
 value=case['meta'].get(axis)
 return '+'.join(value) if isinstance(value,list) and value else ('none' if isinstance(value,list) else str(value or 'unavailable'))


def _wmb_inspect(base='analysis/index.html',**filters):
 values={key:value for key,value in filters.items() if value is not None and value!=''}
 return base+'#inspect?'+urlencode(values)


def _wmb_controls(cases,method_labels,english=False,include_method=True,include_metric=True,default_request='',failure_types=(),include_delivery=True):
 def h(zh,en):return en if english else zh
 axes=('language','level','table','code','equation')
 axis_labels={'language':h('语言','Language'),'level':h('难度','Difficulty'),'table':h('表格类型','Table type'),'code':h('代码类型','Code type'),'equation':h('公式类型','Formula type')}
 categories=sorted({_wmb_category(case,axis) for case in cases.values() for axis in axes})
 fields=[]
 def select(key,label,options,default=''):
  entries=''.join(f'<option value="{esc(value)}"'+(' selected' if value==default else '')+f'>{esc(text)}</option>' for value,text in options)
  fields.append(f'<label>{label}<select data-wmb-filter="{key}" data-wmb-default="{esc(default)}" aria-label="{label}">{entries}</select></label>')
 if include_method:
  select('method',h('方法','Method'),[('',h('全部方法','All methods'))]+[(key,value) for key,value in method_labels.items()])
 select('axis',h('分类维度','Category dimension'),[('',h('全部分类','All categories'))]+[(axis,axis_labels[axis]) for axis in axes])
 select('category',h('分类','Category'),[('',h('全部','All'))]+[(value,value) for value in categories])
 if include_delivery:
  select('request',h('是否生成正文','Body text produced'),[('',h('全部','All')),('failed',h('没有正文','No body text')),('success',h('已生成正文','Body text produced'))],default_request)
  if failure_types:
   select('failure',h('错误类型','Error type'),[('',h('全部','All'))]+[(value,value) for value in failure_types])
 if include_metric:
  select('metric',h('定位指标','Inspect metric'),[('',h('全部指标','All metrics'))]+[(key,key) for key in ('text_edit','code_edit','formula_edit','table_edit','table_TEDS')])
  select('band',h('指标分数区间','Metric score band'),[('',h('不限','Any')),("0:0.25",'0–25%'),("0.25:0.5",'25–50%'),("0.5:0.75",'50–75%'),("0.75:1",'75–100%'),('unavailable',h('未计算 / 不适用','Unavailable / not applicable'))])
 fields.append(f'<label>{h("搜索 Track ID、URL 或错误","Search track ID, URL or error")}<input data-wmb-filter="query" aria-label="{h("搜索","Search")}" placeholder="{h("输入关键词","Enter keywords")}"></label>')
 return '<div class="wmb-controls">'+''.join(fields)+'</div><div class="wmb-toolbar"><strong data-wmb-count></strong><button type="button" onclick="wmbReset()">'+h('清除筛选','Clear filters')+'</button></div>'


def _wmb_row_attrs(case,record=None):
 parts=[]
 for axis in ('language','level','table','code','equation'):
  parts.append(f' data-{axis}="{esc(_wmb_category(case,axis))}"')
 if record:
  error=str(record.get('error_message') or '')
  # Search a bounded excerpt, including the terminal error; full logs stay in evidence.
  if len(error)>1024:error=error[:512]+' … '+error[-512:]
  parts.extend([f' data-method="{esc(record["provider"])}"',f' data-request="{"success" if record.get("_body_present",record["ok"]) else "failed"}"',f' data-failure="{esc(record.get("_output_error") or record.get("error_type") or "")}"',
                f' data-search="{esc(case["id"]+" "+case["url"]+" "+str(record.get("error_type") or "")+" "+error)}"'])
  for metric in ('text_edit','code_edit','formula_edit','table_edit','table_TEDS'):
   item=record.get('metrics',{}).get(metric,{})
   parts.append(f' data-{metric}="{item.get("score") if item.get("success") else ""}"')
 else:
  parts.append(f' data-search="{esc(case["id"]+" "+case["url"])}"')
 return ''.join(parts)


def _wmb_pct(value):
 if value is not None and 0<value<.0005:return f'{value*100:.6g}%'
 return pct(value)


def _wmb_metric_cell(record,metric):
 item=record.get('metrics',{}).get(metric,{})
 return cell(_wmb_pct(item.get('score')) if item.get('success') else '—',item.get('score') if item.get('success') else -1)


def _wmb_error_page(text):
 return bool(ERROR_PAGE.search(text))


def _wmb_output_label(record,text,english=False):
 chars=len(text)
 if _wmb_error_page(text):return ('Error page in output' if english else '输出是错误页')+f' · {chars:,}'
 if not record.get('ok'):
  return (('Error response' if english else '有错误信息')+f' · {chars:,}' if chars else ('No readable response' if english else '无可读响应'))
 if not chars:return 'Empty body text' if english else '正文为空'
 return f'Body text · {chars:,} characters' if english else f'正文 · {chars:,} 字符'


def _wmb_body_state(record,text,english=False):
 if _wmb_error_page(text):return 'Error page, not target content' if english else '错误页，非目标正文'
 if not record.get('ok') or not text.strip():return 'No body text' if english else '没有正文'
 return 'Body text produced' if english else '正文已生成'


def _wmb_filter_table(headers,rows,id,english=False):
 result=table(headers,rows,id,True,metrics=[],index_header='No.' if english else '序号')
 return result.replace('<div class="table-wrap">','<div class="table-wrap wmb-table-wrap">',1).replace('<table class=""','<table class="wmb-table" data-wmb-table',1)


def _wmb_comparison(cases,bycase,names,labels,english=False):
 def h(zh,en):return en if english else zh
 default_a=names[0]
 options=''.join(f'<option value="{esc(name)}"'+(' selected' if name==default_a else '')+f'>{esc(labels[name])}</option>' for name in names)
 relations=[('mixed',h('正文生成有分歧','Body-text outcome differs')),('a_success_others_fail',h('A 有正文，其余没有','A produced body text; others did not')),('a_fail_others_success',h('A 无正文，其余都有','A produced no body text; others did')),('all_success',h('所选方法都有正文','All selected methods produced body text')),('all_fail',h('所选方法都没有正文','No selected method produced body text')),('exact_success',h('仅所选方法有正文','Only selected methods produced body text'))]
 relation_options=''.join(f'<option value="{value}">{esc(label)}</option>' for value,label in relations)
 axes=('language','level','table','code','equation')
 axis_labels={'language':h('语言','Language'),'level':h('难度','Difficulty'),'table':h('表格类型','Table type'),'code':h('代码类型','Code type'),'equation':h('公式类型','Formula type')}
 axis_options='<option value="">'+h('全部分类','All categories')+'</option>'+''.join(f'<option value="{axis}">{axis_labels[axis]}</option>' for axis in axes)
 categories=sorted({_wmb_category(case,axis) for case in cases.values() for axis in axes})
 category_options='<option value="">'+h('全部','All')+'</option>'+''.join(f'<option value="{esc(value)}">{esc(value)}</option>' for value in categories)
 checks=''.join(f'<label'+(' class="disabled"' if name==default_a else '')+f'><input type="checkbox" data-wmb-compare-method value="{esc(name)}"'+(' disabled' if name==default_a else ' checked' if name==names[1] else '')+f'> {esc(labels[name])}</label>' for name in names)
 controls='<div class="comparison-controls"><label>'+h('方法 A','Method A')+'<select id="wmb-compare-a" data-wmb-compare-control>'+options+'</select></label><label>'+h('比较条件','Comparison condition')+'<select id="wmb-compare-relation" data-wmb-compare-control>'+relation_options+'</select></label><label>'+h('分类轴','Category axis')+'<select id="wmb-compare-axis" data-wmb-compare-control>'+axis_options+'</select></label><label>'+h('分类','Category')+'<select id="wmb-compare-category" data-wmb-compare-control>'+category_options+'</select></label></div><fieldset class="comparison-methods"><legend>'+h('对比方法（可多选）','Comparison methods (select multiple)')+'</legend><div class="method-checks">'+checks+'</div></fieldset>'
 rows=[]
 for case in cases.values():
  results={r['provider']:r for r in bycase[case['id']]}
  delivered=[name for name in names if results[name]['_body_present']]
  failed=[name for name in names if not results[name]['_body_present']]
  def pills(selected,kind):
   return '<span class="outcome-list">'+''.join('<a class="outcome-pill '+kind+'" href="../attempts/'+esc(results[name]['attempt_id'])+'.html">'+esc(labels[name])+'</a>' for name in selected)+'</span>' if selected else '—'
  values=cell(link('../datasets/'+case['id']+'.html',case['id']))+cell(esc(_wmb_category(case,'language'))+' · '+esc(_wmb_category(case,'level')))+cell(pills(delivered,'pass'))+cell(pills(failed,'fail'))
  attrs=' data-delivered="'+esc(' '.join(delivered))+'" data-failed="'+esc(' '.join(failed))+'"'+_wmb_row_attrs(case)
  rows.append((attrs,values))
  # All 200 frozen pages remain in the DOM; filtering is client-side and evidence links stay per call.
 result='<div class="method-comparison"><div class="comparison-heading"><div><h3>'+h('同一页是否生成正文','Body text on the same page')+'</h3><p>'+h('这里只比较截止时间内是否得到非空 Markdown 正文，不代表内容质量通过；打开方法可看该页分数、人工真值和实际输出。','This only compares whether non-empty Markdown body text was produced by the deadline; it is not a content-quality pass. Open a method for scores, the human reference and actual output.')+'</p></div><strong class="comparison-count" id="wmb-compare-count"></strong></div>'+controls+'<div class="table-wrap"><table class="wmb-comparison-table"><thead><tr><th>'+h('序号','No.')+'</th><th>Track ID</th><th>'+h('分类','Category')+'</th><th>'+h('生成正文的方法','Methods with body text')+'</th><th>'+h('没有正文的方法','Methods without body text')+'</th></tr></thead><tbody id="wmb-compare-rows">'+''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))+'</tbody></table></div></div>'
 return result


def generate_webmainbench(out:Path,dataset:dict,records:list,summaries:dict,manifests:list,review:dict|None=None,research:dict|None=None,corrections:dict|None=None,adoption_data:dict|None=None,fixture_correction:dict|None=None,teds_revision:tuple|None=None,anomaly_review:dict|None=None):
 """Render the current WebMainBench report with the established report shell."""
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA,encoding='utf-8')
 (out/'assets/report.js').write_text(JS,encoding='utf-8')
 # Presentation-only classification: do not change provider status, scores or evidence.
 classified=[]
 for record in records:
  text=contained_path(out,record['artifact_path']).read_text(encoding='utf-8',errors='replace')
  error_page=_wmb_error_page(text)
  classified.append({**record,'_body_present':bool(record['ok'] and text.strip() and not error_page),
                     '_output_error':'error_page' if error_page else 'empty_body' if record['ok'] and not text.strip() else record.get('error_type')})
 records=classified
 cases={row['id']:row for row in dataset['cases']};bycase=defaultdict(list)
 for row in records:bycase[row['case_id']].append(row)
 names=list(dataset['coverage_plan']['static_html'])
 failure_types=sorted({r['_output_error'] for r in records if not r['_body_present'] and r['_output_error']})
 failed_request_count=sum(not r['_body_present'] for r in records)
 case_count=len(cases);result_count=case_count*len(names)
 labels={'kitesurf':'Kitesurf','lightpanda':'Lightpanda','obscura':'Obscura','moli':'Moli 1.1.5','chromium':'Chromium 152'}
 metric_order=['overall','text_edit','code_edit','formula_edit','table_edit','table_TEDS']
 corrected=teds_revision is not None
 display_score=_wmb_pct
 def score(name,metric):return summaries[name]['official_aggregate']['scores'][metric]
 def denom(name,metric):return summaries[name]['official_aggregate']['denominators'].get(metric)
 def times(name):return [r['total_elapsed_ms'] for r in records if r['provider']==name]
 def failures(name):return [r for r in records if r['provider']==name and not r['_body_present']]
 def unique_winner(values,higher=True):
  ordered=sorted(values,key=lambda item:item[1],reverse=higher)
  return ordered[0][0] if len(ordered)==1 or ordered[0][1]!=ordered[1][1] else None
 ranked_metrics=metric_order if corrected else metric_order[1:-1]
 score_winners={metric:unique_winner([(name,score(name,metric)) for name in names]) for metric in ranked_metrics}
 latency_values={name:{'mean':statistics.fmean(times(name)),'p50':statistics.median(times(name)),'p95':percentile(times(name),.95)} for name in names}
 latency_winners={key:unique_winner([(name,values[key]) for name,values in latency_values.items()],higher=False) for key in ('mean','p50','p95')}
 def value_html(value,winner=False,n=None,population=None):
  content=f'<span class="metric-value">{value}</span>'
  if n is not None:content+=f'<span class="tiny">n={n} / {case_count if population is None else population}</span>'
  return f'<span class="comparison-winner">{content}</span>' if winner else content
 def core_rows(english=False):
  rows=[]
  for name in sorted(names,key=lambda item:(-score(item,'text_edit'),labels[item])):
   values=cell(link('providers/'+name+'.html',labels[name]))
   values+=cell(value_html(display_score(score(name,'overall')),winner=score_winners.get('overall')==name),score(name,'overall'))
   for metric in metric_order[1:]:
    values+=cell(value_html(display_score(score(name,metric)),winner=score_winners.get(metric)==name,n=denom(name,metric)),score(name,metric))
   for key in ('mean','p50','p95'):
    values+=cell(value_html(ms(latency_values[name][key]),winner=latency_winners[key]==name),latency_values[name][key])
   rows.append(('',values))
  return rows
 for english in (False,True):
  h=lambda zh,en:en if english else zh
  corrected_label=h('本地修正版','locally corrected')
  metric_labels={name:name+(f'（{corrected_label}）' if corrected and name in ('overall','table_TEDS') else '') for name in metric_order}
  status=(h(f'{case_count} 页 × 5 方法 = {result_count:,} 条结果；证据与算术核验通过，表格结构评分的有效性尚未通过。',f'{case_count} pages × 5 methods = {result_count:,} results; evidence and arithmetic checks passed, but table-structure score validity did not.') if review and review.get('status')=='passed' else h('测量完成；独立审查尚未通过，当前页面不可发布。','Measurement complete; independent review has not passed, so this build is not publishable.'))
  if corrected:
   status=h(f'{case_count} 页 × {len(names)} 种方法；TEDS 使用本地修正版，原始分与纠正记录均保留。',f'{case_count} pages × {len(names)} methods; TEDS is locally corrected, with original scores and correction records retained.')
  home=hero(h('WebMainBench-545 · 预选 200 页 · 五款浏览器实测','WebMainBench-545 · preselected 200 · five-browser evaluation'),h('五款方法读取同一批冻结 HTML，并与同一份人工校准 Markdown 比较；逐页响应、空正文和原因放在结果详情中。','Five methods read the same frozen HTML and are compared with the same human-calibrated Markdown; page-level responses, empty body text and causes live in result details.'))
  home+='<div class="wrap reliability'+('' if review and review.get('status')=='passed' else ' failure')+'"><strong>'+esc(status)+'</strong></div>'
  home+=section(h('本轮规模','Run scope'),'<div class="grid">'+''.join(f'<article class="card kpi"><div class="n">{value}</div><p>{label}</p></article>' for value,label in [(str(case_count),h('预选冻结页面','preselected frozen pages')),('5',h('浏览器方法','browser methods')),(f'{result_count:,}',h('逻辑结果','logical results'))])+'</div>')
  core_headers=[h('方法','Method'),metric_labels['overall'] if corrected else h('overall（待校验）','overall (under review)'),'text_edit','code_edit','formula_edit','table_edit',metric_labels['table_TEDS'] if corrected else h('table_TEDS（待校验）','table_TEDS (under review)'),h('均值耗时','Mean latency'),'P50','P95']
  core_note=h('分数越高越接近参考答案，耗时越低越快；绿色标出该列唯一最高分或最低耗时。n 是实际参与该项平均的页数，不是成功页数；不同方法的计分页可能不同。耗时覆盖每种方法全部 200 次结果，包含失败和已登记调用。','Higher scores are closer to the reference; lower latency is faster. Green marks the unique highest score or lowest latency in each column. n counts pages included in that component mean, not passed pages; the scored pages can differ by method. Latency covers all 200 results per method, including failed and declared calls.')
  core_note+=h(' TEDS 和 overall 使用本地修正版，修正的是树节点读取错误；其余评分规则、分母和浏览器输出未变。',' TEDS and overall are locally corrected for a tree-node input defect; other rules, denominators and browser outputs are unchanged.') if corrected else h(' 原始 TEDS 存在已证实的计算缺陷；它和 overall 不标领先者。',' Original TEDS has a proven calculation defect; it and overall have no highlighted leader.')
  home+=section(h('核心结果','Core results'),'<p class="source-note">'+core_note+'</p>'+table(core_headers,core_rows(english),'wmb-core-results',True,cls='wmb-core-table',metrics=[],default_sort=(3,'desc'))+f'<p class="actions">{link("analysis/index.html",h("筛选逐页结果与响应 →","Filter page-level results and responses →"))} · {link("methodology/index.html",h("评分流程与分母 →","Scoring and denominators →"))} · {link("review/index.html",h("官方原始分与纠正记录 →","Original scores and corrections →"))}</p>')
  _wmb_write(out,'index.html',h('评测报告','Evaluation report'),home,english)

  dataset_rows=[]
  for c in cases.values():
   meta=c['meta']
   dataset_rows.append((_wmb_row_attrs(c),cell(link(c['id']+'.html',c['id']))+cell(esc(meta.get('language') or 'unavailable'))+cell(esc(meta.get('level')))+cell(esc('+'.join(meta.get('table') or []) or 'none'))+cell(esc('+'.join(meta.get('code') or []) or 'none'))+cell(esc('+'.join(meta.get('equation') or []) or 'none'))))
  body=hero(h('评测集','Dataset'),h('WebMainBench-545 母集里预先冻结的 200 页；筛选发生在任何方法结果产生之前。','A frozen 200-page sample from the WebMainBench-545 parent, selected before any method result was produced.'))
  body+=section(h('200 页案例','200 pages'),'<p class="source-note">'+h('可按语言、难度和内容类型查找页面。打开 Track ID 可查看冻结 HTML、人工校准的 Markdown 参考文本和官方元数据；方法成绩在结果分析中单独查看。官方评分直接比较转换结果与参考文本，没有预先写好的逐题得分清单。','Filter by language, difficulty and content type. Open a track ID for the frozen HTML, human-calibrated Markdown reference and official metadata; method results are in the analysis section. Official scoring compares each conversion with the reference directly, without a prewritten per-task checklist.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,include_metric=False,include_delivery=False)+_wmb_filter_table([h('Track ID','Track ID'),h('语言','Language'),h('难度','Difficulty'),h('表格','Table'),h('代码','Code'),h('公式','Formula')],dataset_rows,'wmb-cases',english))
  sample=dataset.get('sample',{});sampling=sample.get('sampling',{})
  if sampling:
   distribution_rows=[]
   parent_distribution=sample.get('parent_distribution',{});sample_distribution=sample.get('sample_distribution',{})
   axis_labels={'language':h('语言','Language'),'level':h('难度','Difficulty'),'table':h('表格类型','Table type'),'code':h('代码类型','Code type'),'equation':h('公式类型','Formula type')}
   for axis in ('language','level','table','code','equation'):
    parent_values=parent_distribution.get(axis,{});sample_values=sample_distribution.get(axis,{})
    for category in sorted(set(parent_values)|set(sample_values)):
     parent_count=parent_values.get(category,0);sample_count=sample_values.get(category,0)
     distribution_rows.append((f' data-axis="{esc(axis)}" data-parent="{parent_count}" data-sample="{sample_count}"',cell(esc(category))+cell(str(parent_count))+cell(str(sample_count))))
   manifest_href='../sample-manifest.json' if not english else '../../sample-manifest.json'
   distribution_controls='<div class="wmb-distribution-controls"><label>'+h('查看哪一类分布','Distribution axis')+'<select id="wmb-distribution-axis" data-wmb-distribution-filter>'+''.join(f'<option value="{axis}">{label}</option>' for axis,label in axis_labels.items())+'</select></label><p id="wmb-distribution-count" class="source-note" aria-live="polite"></p></div>'
   distribution_table=table([h('分类','Category'),h('母集 545','Parent 545'),h('样本 200','Sample 200')],distribution_rows,'wmb-distribution',metrics=[])
   body+=section(h('这 200 页怎么选出来的','How these 200 pages were selected'),'<p>'+h('语言、难度、表格、代码和公式是五套彼此独立的分类，同一页会同时属于每套分类。选择任一分类轴后，“样本 200”这一列会合计为 200；不能把五套分类混在一起相加。完整名单在运行任何浏览器之前已经确定，没有按分数挑页。','Language, difficulty, table, code and formula are five independent classification axes; every page belongs to one category on each axis. After selecting an axis, the “Sample 200” column sums to 200. Counts across different axes must not be added together. The manifest was fixed before any browser ran; scores did not affect selection.')+'</p><p><b>'+h('固定种子：','Fixed seed: ')+'</b><code>'+esc(sampling.get('seed',''))+'</code> · '+link(manifest_href,h('下载 200 页名单','Download the 200-page list'))+'</p>'+distribution_controls+distribution_table)
  if research:
   def evidence_links(urls):
    values=[]
    for url in urls:
     parsed=urlsplit(url);label=(parsed.netloc+parsed.path.rstrip('/')) or url
     values.append(link(url,label))
    return '<br>'.join(values) or '—'
   selection=[]
   for item in research.get('direct_benchmarks',[]):
    sources=evidence_links(item.get('sources',[]))
    selection.append(('',cell(esc(item['name']))+cell(esc(item.get('scope' if english else 'scope_zh','—')))+cell(esc(item.get('metric' if english else 'metric_zh','—')))+cell(esc('；'.join(item.get('users',[])) or '—'))+cell(esc(item.get('disposition' if english else 'disposition_zh','—')))+cell(sources)))
   body+=section(h('为什么选择 WebMainBench-545','Why WebMainBench-545'),'<p>'+esc(research['decision']['summary_en' if english else 'summary_zh'])+'</p><p class="callout">'+esc(research['decision']['qualification_en' if english else 'qualification_zh'])+'</p>'+table([h('候选','Candidate'),h('范围','Scope'),h('指标','Metric'),h('采用者 / 使用者','Named users'),h('结论','Decision'),h('证据','Evidence')],selection,cls='wide-prose-table',metrics=[]))
   wcxb=next((item for item in research.get('direct_benchmarks',[]) if item.get('id')=='wcxb'),None)
   if wcxb:
    limits=wcxb.get('limitations' if english else 'limitations_zh',[])
    body+=section(h('WCXB 判断','WCXB assessment'),'<p>'+esc(wcxb.get('maturity' if english else 'maturity_zh',''))+'</p><ul>'+''.join('<li>'+esc(value)+'</li>' for value in limits)+'</ul>')
   adjacent=[]
   for item in research.get('adjacent_browser_benchmarks',[]):
    adjacent.append(('',cell(esc(item['name']))+cell(esc(item.get('size' if english else 'size_zh')))+cell(esc(item.get('measures' if english else 'measures_zh')))+cell(esc(item.get('adoption' if english else 'adoption_zh')))+cell(esc(item.get('why_separate' if english else 'why_separate_zh')))+cell(evidence_links(item.get('sources',[])))))
   body+=section(h('相邻 Browser / Web Agent 评测','Adjacent browser and web-agent benchmarks'),table([h('评测','Benchmark'),h('规模','Size'),h('测什么','Measures'),h('采用情况','Adoption'),h('为何不混榜','Why separate'),h('证据','Evidence')],adjacent,cls='wide-prose-table',metrics=[]))
  _wmb_write(out,'datasets/index.html',h('评测集','Dataset'),body,english)

  method=hero(h('评测方法','Methodology'),h('从一页冻结网页开始，看浏览器交付了什么、官方怎样给分，以及哪些分数现在不能用。','Start with one frozen page: what the browser delivered, how the official scorer calculates it, and which figures cannot currently support conclusions.'))
  flow_source=(r'''flowchart LR
    A["1  冻结 HTML<br/>同一批 200 页"] --> B["2  五款方法转换<br/>每页得到最终响应"]
    B --> C{"3  是否得到<br/>可读 Markdown 正文"}
    C -->|"没有"| D["结果详情<br/>错误类型 · 响应 · 原因"]
    C -->|"有"| E["4  与人工 Markdown 比较<br/>文本 · 代码 · 公式 · 表格"]
    E --> F["5  逐页分数<br/>不存在的内容可能不计分"]
    F --> G["6  分项均值<br/>分数下方显示实际 n / 200"]
    B --> H["全部 200 次逻辑结果<br/>均值 · P50 · P95 耗时"]''' if not english else r'''flowchart LR
    A["1  Frozen HTML<br/>the same 200 pages"] --> B["2  Five methods convert<br/>one final response per page"]
    B --> C{"3  Readable Markdown<br/>body text produced?"}
    C -->|"No"| D["Result detail<br/>error type · response · cause"]
    C -->|"Yes"| E["4  Compare with human Markdown<br/>text · code · formula · table"]
    E --> F["5  Per-page components<br/>absent content may be unscored"]
    F --> G["6  Component means<br/>actual n / 200 shown below score"]
    B --> H["All 200 logical results<br/>mean · P50 · P95 latency"]''')
  flow_fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback>'+''.join(f'<span><b>{index}</b> {label}</span>' for index,label in enumerate(([h('冻结输入','Frozen input'),h('方法转换','Conversion'),h('检查响应与正文','Inspect response and body'),h('逐页评分','Per-page scoring'),h('按实际 n 聚合','Aggregate with actual n')]),1))+'</div>'
  flow='<div class="wmb-flow">'+flow_fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'
  method+=section(h('一页怎样变成一行结果','How one page becomes one result'),'<p class="source-note">'+h('先看整条路径，再按需展开下面的实现细节。正文是否生成、接口是否响应和为什么为空，属于逐页结果；质量分只回答已生成内容与人工答案有多接近。','Start with the full path, then open implementation details only when needed. Whether body text exists, whether the interface responded and why it is empty belong to the page result; quality scores only describe similarity to the human reference.')+'</p>'+flow)
  input_text=h('每个方法接收预选 200 页官方 <code>html</code> 字段的同一组 UTF-8 字节。四款本地方法从 OS 网络隔离的 loopback HTTPS 读取原样正文，每批使用专用临时 CA；HTTPS 避免冻结页因协议差异触发伪跳转。它们的原始批次覆盖母集 545 页，报告只投影到预先冻结的 200-ID 清单，不按结果选题。Kitesurf 从头只执行这 200 页，使用 Cloudflare 官方 <code>html</code> 输入并拒绝外部资源类型。冻结 HTML 自带的内联脚本可以执行，但非 loopback 子资源和数据请求不能出网；原 URL 只保留为来源元数据。每个方法内部严格串行；不同方法的批次可重叠，因此耗时是本轮客户端观测，不是隔离硬件微基准。',"Every method receives the same UTF-8 bytes from the official <code>html</code> field for the preselected 200 pages. Four local methods read the unchanged body over loopback HTTPS under an OS network sandbox, using a dedicated ephemeral CA per batch; HTTPS prevents false redirects caused only by a scheme mismatch. Their source batches measured all 545 parent pages, while this report projects only the pre-frozen 200-ID manifest and never selects by outcome. Kitesurf runs these 200 pages from scratch through Cloudflare's documented <code>html</code> input with external resource types rejected. Inline scripts contained in the frozen HTML may run, but non-loopback subresource and data requests cannot reach the network; the original URL is evidence metadata only. Calls are serial within each method; provider batches may overlap, so latency is this run's client observation rather than an isolated hardware microbenchmark.")
  cue_text=h('数据 HTML 含公开标注属性 <code>cc-select</code>；五款通用转换器均未按该属性定制，但这是数据集固有的潜在线索。','The dataset HTML contains public <code>cc-select</code> annotation attributes. None of the five generic converters is customized to use them, but they remain an inherent benchmark cue.')
  method+=section(h('输入与运行边界','Input and runtime boundary'),'<p>'+h('每页有一份冻结 HTML 和一份人工校准 Markdown。五款方法转换同一份 HTML；原始 URL 只记录来源，本轮不访问实时网站。','Each page has frozen HTML and a human-calibrated Markdown reference. All five methods convert the same HTML; the original URL records provenance and is not fetched live in this run.')+'</p><details><summary>'+h('查看网络隔离、HTTPS 夹具和批次细节','Network isolation, HTTPS fixture and batch details')+'</summary><p>'+input_text+'</p><p class="callout">'+cue_text+'</p></details><p class="actions">'+link('../datasets/index.html',h('选一页查看完整输入和答案','Open a page with full input and reference'))+'</p>')
  formulas=[('text_edit',h('完整 Markdown 与人工答案','Full Markdown against the human reference'),h('字符编辑距离 ÷ 两份文本中较长者的长度；导航、页脚等多余内容也会拉低分数。','Character edit distance divided by the longer text; extra navigation and footer content can lower the score.')),('code_edit',h('从两份 Markdown 找出的代码块','Code blocks found in the two Markdown files'),h('只比较提取出的代码字符，按较长代码长度归一化。','Compare extracted code characters, normalized by the longer code length.')),('formula_edit',h('从两份 Markdown 找出的公式','Formulas found in the two Markdown files'),h('只比较提取出的公式字符，按较长公式长度归一化。','Compare extracted formula characters, normalized by the longer formula length.')),('table_edit',h('从两份 Markdown 找出的表格内容','Tables found in the two Markdown files'),h('比较规范化后的表格字符；普通段落里有表格字样，不等于输出了可识别的表格。','Compare normalized table characters; table words in a paragraph do not form a recognized table.')),('table_TEDS',h('把识别出的表格解析为节点','Recognized tables parsed into nodes'),h('比较节点的树编辑距离；本地修正版让算法实际读取行、列和单元格子节点，保留官方解析规则和编辑代价。','Compare node-based tree edit distance. The local correction exposes row, column and cell children to the algorithm, preserving official parsing rules and edit costs.'))]
  method+=section(h('每项分数怎么来','How component scores are produced'),'<p>'+h('先从实际输出和人工答案里分别识别代码、公式和表格；text_edit 则保留整份 Markdown。字符项的分数＝1 − 编辑距离 ÷ 较长文本长度，完全相同为 100%，只剩一侧内容时可为 0%。表格结构项使用下文说明的 TEDS 本地修正版。','The scorer first extracts code, formulas and tables from the output and reference; text_edit instead keeps the entire Markdown. For character metrics, score = 1 − edit distance / longer length: identical content scores 100%, while content on only one side can score 0%. Table structure uses the locally corrected TEDS described below.')+'</p>'+table([h('指标','Metric'),h('比较对象','Compared content'),h('计算与读法','Calculation and reading')],[('',cell(k)+cell(subject)+cell(explanation)) for k,subject,explanation in formulas],cls='wide-prose-table',metrics=[])+f'<p>{h("使用官方提交 <code>9d991bd</code> 的确定性 <code>use_llm=false</code> 配置。代码、公式和表格在两侧都不存在时，该项不算分；只有一侧存在时，该项仍可能给 0 分。","The pinned official commit is <code>9d991bd</code> with deterministic <code>use_llm=false</code>. If neither side contains code, a formula or a table, the component is not scored; content on only one side can yield a scored zero.")}</p>')
  method+=section(h('分母为什么会不同','Why denominators differ'),'<p>'+h('每个方法都转换固定的 200 页。没有生成目标正文的页面不参加质量均值；有正文的页面再由官方逐项判断是否能算分。参考和输出都没有该类内容时通常跳过；一侧有而另一侧没有时计入 0 分。于是“n=76 / 200”表示 76 个逐页分数参与平均，并不表示 76 页全部写对，也不表示五个方法使用相同的 76 页。','Every method receives the same 200 pages. Pages without target body text do not enter quality means; pages with body text are then considered component by component. A type absent on both sides is usually skipped; present on only one side can be scored zero. Thus “n=76 / 200” means 76 page scores enter the mean, not 76 correct pages or the same 76 pages for every method.')+'</p>'+table([h('表格例子','Table example'),h('固定输入','Frozen inputs'),h('生成目标正文的页面','Pages with target body text'),h('计入表格均值','Included in table mean'),h('这批页实际是什么','What those pages contain')],[('',cell(link('../providers/kitesurf.html','Kitesurf'))+cell(str(case_count))+cell(str(case_count-len(failures('kitesurf'))))+cell(str(denom('kitesurf','table_edit')))+cell(h('既包括两侧都有表格的页，也包括输出有表格、人工答案没有表格而计 0 分的页。','Includes pages with tables on both sides and pages where only the output has a table, scored zero.'))),('',cell(link('../providers/moli.html','Moli 1.1.5'))+cell(str(case_count))+cell(str(case_count-len(failures('moli'))))+cell(str(denom('moli','table_edit')))+cell(h('这 40 页的人工答案有表格，但 Moli 的原生 Markdown 没有官方能识别的表格；逐页都计入 0 分。','The human references contain tables on these 40 pages, but Moli’s native Markdown has no officially recognized table; all 40 score zero.')))],cls='wide-prose-table',metrics=[])+f'<p class="actions">{link("../analysis/index.html",h("按方法查看每页计分状态","Inspect per-page scoring status"))}</p>')
  method+=section(h('表格结构评分的修正','Table-structure scoring correction'),'<p>'+h('固定版官方 TEDS 把表格节点写成一整段带括号的字符串，却直接把这个字符串交给 APTED。APTED 的默认子节点读取规则对字符串返回空列表，因此没有按真实行、列和单元格做树编辑。实测一页只有 5 个预测节点、人工答案有 570 个节点，官方记录的编辑距离却小于 1，给出 99.8% 的 TEDS。若真的按每个节点插入或删除至少花 1 分，光节点数量差就至少需要 565 次编辑。','The pinned official TEDS serializes table nodes into one parenthesized string and passes that string directly to APTED. APTED’s child accessor finds no children on a string, so it does not edit the actual rows, columns and cells as a tree. In one measured page, the prediction has 5 nodes and the reference 570, yet the recorded distance is below 1 and TEDS is 99.8%. With unit-cost node insertions/deletions, the 565-node count difference alone requires at least 565 edits.')+'</p><p class="callout failure">'+h('修正后，APTED 直接读取真实节点及其子节点。解析、节点编辑代价和计分页数保持不变；计算超出安全范围且无法证明精确解时直接报错，不用近似分替代。报告中的 table_TEDS 和 overall 标为本地修正版，官方原始分保留在核验记录和逐页详情中。table_edit 仍只比较表格文本。','APTED now reads explicit nodes and their children. Parsing, node edit costs and scored-page counts are unchanged. If safe computation cannot certify an exact result, it fails instead of emitting an approximation. Reported table_TEDS and overall are labeled locally corrected; original official scores remain in the review and page details. Table_edit still compares table text only.')+'</p><p class="actions">'+link('../attempts/obscura__949e1468-eed2-43ab-96e6-5dcb07c52d4b__r001.html',h('查看 5 对 570 节点的完整答案和输出','Inspect the complete 5-versus-570-node case'))+' · '+link('https://github.com/opendatalab/WebMainBench/blob/9d991bdc00c57b57521499494d96be85c31317ba/webmainbench/metrics/teds_metrics.py',h('官方 TEDS 源码','Official TEDS source'))+'</p>')
  method+=section(h('聚合、计时和不可比较项','Aggregation, timing and non-comparable fields'),'<p>'+h('每项聚合分＝该项成功计算的逐页分数之和 ÷ 实际计入页数；榜单 overall＝五项聚合分的算术平均。逐页 overall 则平均该页成功计算的项目，因此它不能直接拿来替代榜单 overall。官方分项的“未计算”可能是没有相应内容，也可能是评分器错误；页面详情保留原因。','Each component aggregate is the sum of successfully calculated page scores divided by its actual scored-page count; leaderboard overall is the arithmetic mean of the five aggregates. Per-page overall averages only that page’s calculated components, so it cannot replace leaderboard overall. A component marked “not calculated” may be absent content or a scorer error; the result detail retains the reason.')+'</p><p>'+h('单次总耗时从请求开始到最终响应结束，失败和声明重试均计入。浏览器批次各自串行，但不同方法可能重叠；Cloudflare 远端时间和本地 CLI 时间处于不同执行环境。是否生成正文、质量分和耗时分开报告，不能混成一个分数。','Total call time runs from request start to final response and includes failures and declared retries. Calls are serial within a browser batch, but method batches may overlap; Cloudflare remote time and local CLI time come from different execution environments. Body-text outcome, quality and latency are reported separately, not folded into one score.')+'</p>','calculation')
  _wmb_write(out,'methodology/index.html',h('评测方法','Methodology'),method,english)

  analysis=hero(h('结果分析','Analysis'),h('先比较同一页上的方法差异，再按分类查看分数，最后下钻到每次响应、正文和原因。','Compare methods on the same page, then inspect category scores, and finally drill into each response, body text and cause.'))
  analysis+=section(h('方法组合对比','Method group comparison'),_wmb_comparison(cases,bycase,names,labels,english),'comparison')
  slice_rows=[];slice_categories=set();slice_axis_labels={'language':h('语言','Language'),'level':h('难度','Difficulty'),'table':h('表格类型','Table type'),'code':h('代码类型','Code type'),'equation':h('公式类型','Formula type')}
  for axis in ('language','level','table','code','equation'):
   categories=sorted({category for name in names for category in summaries[name].get('dimensions',{}).get(axis,{})})
   slice_categories.update(categories)
   for category in categories:
    category_winners={}
    for metric in ranked_metrics:
     eligible=[]
     for candidate in names:
      candidate_aggregate=summaries[candidate]['dimensions'][axis][category]
      if candidate_aggregate['denominators'].get(metric,0)>0:eligible.append((candidate,candidate_aggregate['scores'][metric]))
     category_winners[metric]=unique_winner(eligible) if eligible else None
    for name in sorted(names,key=lambda x:(-score(x,'overall'),x)):
     aggregate=summaries[name]['dimensions'][axis][category]
     values=[]
     for metric in metric_order:
      n=aggregate['denominators'].get(metric,0) if metric!='overall' else None
      population=sum(_wmb_category(case,axis)==category for case in cases.values())
      value=value_html(display_score(aggregate['scores'][metric]),winner=category_winners.get(metric)==name,n=n,population=population) if n is None or n>0 else '—'
      values.append(cell(value,aggregate['scores'][metric] if n is None or n>0 else -1))
     slice_rows.append((f' data-axis="{esc(axis)}" data-category="{esc(category)}" data-method="{esc(name)}"',cell(esc(slice_axis_labels[axis]))+cell(link(_wmb_inspect('index.html',method=name,axis=axis,category=category),category))+cell(link('../providers/'+name+'.html',labels[name]))+''.join(values)))
  slice_filter='<div class="wmb-slice-controls"><label>'+h('分类维度','Category dimension')+'<select id="wmb-slice-axis" data-wmb-slice-filter>'+''.join('<option value="'+esc(axis)+'">'+esc(slice_axis_labels[axis])+'</option>' for axis in ('language','level','table','code','equation'))+'</select></label><label>'+h('分类','Category')+'<select id="wmb-slice-category" data-wmb-slice-filter><option value="">'+h('全部','All')+'</option>'+''.join('<option value="'+esc(category)+'">'+esc(category)+'</option>' for category in sorted(slice_categories))+'</select></label><label>'+h('方法','Method')+'<select id="wmb-slice-method" data-wmb-slice-filter><option value="">'+h('全部','All')+'</option>'+''.join('<option value="'+esc(name)+'">'+esc(labels[name])+'</option>' for name in names)+'</select></label></div><p id="wmb-slice-count" class="source-note" aria-live="polite"></p>'
  slice_table=table([h('分类轴','Axis'),h('分类','Category'),h('方法','Method')]+[metric_labels[m] for m in metric_order],slice_rows,'wmb-slices',True,cls='wmb-slice-table',metrics=[]).replace('<div class="table-wrap">','<div class="table-wrap wmb-slice-table-wrap">',1)
  analysis_tail=section(h('按页面分类比较','Compare by page category'),'<p class="source-note">'+h('先选择一套独立分类，再按分类或方法缩小范围。分数下方是计分页数 / 当前分类总页数；“—”表示没有可计算页，不等于 0 分。绿色只标出当前分类、当前指标的唯一最高分。TEDS 和 overall 沿用首页注明的修正版本。','Select one independent classification axis, then narrow by category or method. The small fraction below a score is scored pages / all pages in that category; “—” means no scored page, not zero. Green marks a unique highest score within the category and component. TEDS and overall use the correction labeled on the home page.')+'</p>'+slice_filter+slice_table)
  inspect_rows=[]
  for r in sorted(records,key=lambda row:(not row['ok'],row['case_id'],row['provider'])):
   case=cases[r['case_id']]
   target='../attempts/'+r['attempt_id']+'.html'
   output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
   state_class='success' if r['ok'] and output.strip() and not _wmb_error_page(output) else 'failure'
   request=f'<span class="wmb-pill {state_class}">{esc(_wmb_body_state(r,output,english))}</span>'
   vals=cell(link(target,r['case_id']))+cell(link(target,labels[r['provider']]))+cell(esc(_wmb_category(case,'language')))+cell(esc(_wmb_category(case,'level')))+cell(esc(_wmb_category(case,'table')))+cell(esc(_wmb_category(case,'code')))+cell(esc(_wmb_category(case,'equation')))+cell(request)+cell(esc(_wmb_output_label(r,output,english)))
   vals+=''.join(_wmb_metric_cell(r,metric) for metric in metric_order[1:])
   vals+=cell(ms(r['total_elapsed_ms']),r['total_elapsed_ms'])+cell(link(target,h('逐项证据 →','Metric evidence →')))
   inspect_rows.append((_wmb_row_attrs(case,r),vals))
  analysis+=section(h('逐页结果与响应','Page results and responses'),'<p class="wmb-metric-note">'+h(f'完整矩阵共有 200×5 条结果，默认先显示 {failed_request_count} 条没有生成正文的转换。每行直接说明是否得到正文，以及接口或程序最后留下的是正文、错误信息、错误页还是无可读响应。点击 Track ID 或方法名进入详情，核对完整响应、人工答案、逐项评分和目前能证实的原因。',f'The complete matrix contains 200×5 results and initially shows the {failed_request_count} conversions without body text. Each row states whether body text was produced and whether the final API/worker material is body text, an error response, an error page or no readable response. Open the track ID or method for the complete response, human reference, component scores and the currently supported cause.')+'</p>'+_wmb_controls(cases,labels,english,default_request='failed',failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('表格可横向滚动查看五项分数和耗时；Track ID 与方法名都可直接进入详情。','Scroll horizontally for component scores and latency; both the track ID and method name open the detail.')+'</p>'+_wmb_filter_table([h('Track ID','Track ID'),h('方法','Method'),h('语言','Language'),h('难度','Difficulty'),h('表格','Table'),h('代码','Code'),h('公式','Formula'),h('是否生成正文','Body text produced'),h('响应与正文','Response and body')]+[metric_labels[m] for m in metric_order[1:]]+[h('总耗时','Total latency'),h('详情','Detail')],inspect_rows,'wmb-attempt-matrix',english))
  _wmb_write(out,'analysis/index.html',h('结果分析','Analysis'),analysis+analysis_tail,english)

  review_body=hero(h('核验记录','Review'),h('保留官方原始结果，记录计算修正的范围和影响；逐页原因见结果详情。','Original official results are retained alongside the scope and effects of corrections; page-level causes are in result details.'))
  raw_prefix='../../' if english else '../'
  if corrected:
   receipt,revision_review=teds_revision
   effects=[]
   for name in names:
    impact=revision_review['impacts'][name];before=impact['official'];after=impact['corrected']
    effects.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(display_score(before['scores']['table_TEDS']))+cell(display_score(after['scores']['table_TEDS']))+cell(display_score(before['scores']['overall']))+cell(display_score(after['scores']['overall']))+cell(str(after['denominators']['table_TEDS']))))
   review_body+=section(h('TEDS 计算修正','TEDS calculation correction'),'<p>'+h(f'同一批 {revision_review["logical_results"]:,} 条结果全部重新计算，{revision_review["changed_scores"]} 条 TEDS 分数变化。没有重新调用浏览器，输入、原始输出、计分页数和耗时均未改变。表中修正分是本地修正版，不是官方发布的榜单。',f'All {revision_review["logical_results"]:,} results were reprocessed; {revision_review["changed_scores"]} TEDS values changed. No browser was called again. Inputs, raw outputs, scored-page counts and latency are unchanged. Corrected scores are a local implementation correction, not an official leaderboard.')+'</p>'+table([h('方法','Method'),h('原始 TEDS','Original TEDS'),h('修正 TEDS','Corrected TEDS'),h('原始 overall','Original overall'),h('修正 overall','Corrected overall'),h('TEDS 计分页数','Pages scored for TEDS')],effects,'wmb-correction-effects',metrics=[])+
    '<p class="actions">'+link('../methodology/index.html',h('树节点错误及修正原理','Tree-node defect and correction'))+' · '+link(raw_prefix+'teds-correction.json',h('纠正范围与源码校验值','Correction scope and source hashes'))+' · '+link(raw_prefix+'teds-correction-review.json',h('完整性与算术核验','Integrity and arithmetic audit'))+' · '+link(raw_prefix+'original-results.jsonl',h('官方原始逐页结果','Original official page results'))+'</p>')
  if review:
   review_body+=section(h('原始结果证据检查','Original-result evidence checks'),'<p>'+h('原始核验检查了 1,000 条结果的覆盖范围、文件哈希、调用耗时、文本类评分及分项聚合。它不能单独证明评分算法正确，也不能证明每条失败都已找到根因。TEDS 的纠正及重新核验单独记录在上方。','The original audit checked the 1,000-result scope, file hashes, call latency, text metrics and component aggregation. It does not alone establish algorithm validity or the root cause of every failure. The TEDS correction and its follow-up checks are recorded above.')+'</p><p>'+link(raw_prefix+'result-review.json',h('下载原始核验记录','Download the original audit'))+'</p>')
  else:
   review_body+=section(h('原始核验缺失','Original audit missing'),'<p class="callout failure">'+h('没有原始核验文件，不能发布。','The original audit is missing; publication is blocked.')+'</p>')
  error_outputs=sum(1 for r in records if _wmb_error_page(contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')))
  if anomaly_review:
   anomaly_rows=[]
   for finding in anomaly_review['cases']:
    providers=(', ' if english else '、').join(labels[name] for name in finding['affected_providers'])
    anomaly_rows.append(('',cell(link('../datasets/'+finding['case_id']+'.html',finding['case_id']))+cell(esc(providers))+cell(esc(finding['observed_en' if english else 'observed_zh']))+cell(esc(finding['cause_en' if english else 'cause_zh']))))
   scope=anomaly_review['scope']
   anomaly_href='../../webmainbench-anomaly-review.json' if english else '../webmainbench-anomaly-review.json'
   review_body+=section(h('异常结果根因复核','Root-cause review of anomalous results'),'<p>'+h(f'自动规则从 1,000 条结果中触发 {scope["signal_triggered_results"]} 条，实际集中在 {scope["distinct_cases"]} 个页面。复核读取正式输出、人工答案、程序错误和同页五方法差异，并用不计分的删减对照定位触发条件。所有触发项均已归入下列原因；对照输入不是原始任务，因此没有替换调用、分数、分母或耗时。',f'Deterministic rules triggered {scope["signal_triggered_results"]} of 1,000 results, concentrated in {scope["distinct_cases"]} pages. Review used the authoritative outputs, human references, program errors and same-page method differences, then non-scoring ablation controls to isolate triggers. Every triggered result maps to a cause below. Controls are not the original task, so no call, score, denominator or latency was replaced.')+'</p>'+table([h('页面','Page'),h('受影响方法','Affected methods'),h('对照观察','Control observation'),h('根因与证据边界','Cause and evidence boundary')],anomaly_rows,cls='wide-prose-table',metrics=[])+f'<p>{link(anomaly_href,h("下载绑定哈希的复核记录","Download the hash-bound review record"))}</p>')
  review_body+=section(h('核验边界','Review boundary'),'<p>'+h(f'正式结果中有 {failed_request_count} 次转换没有目标正文，其中 {error_outputs} 条留下的是浏览器错误页；它们现在都能从逐页详情进入对应的直接证据和已验证原因。异常复核覆盖确定性规则触发的 30 条结果，不等于对其余正常结果逐条做人工作品质量复审。服务未公开的内部资源阈值继续明确标为不可再细分。',f'The authoritative matrix has {failed_request_count} conversions without target body text, including {error_outputs} browser error-page output. Each now links from its detail page to direct evidence and a verified cause. The anomaly review covers the 30 deterministically triggered results; it is not a manual editorial-quality review of every remaining normal result. Undisclosed service-internal resource thresholds remain explicitly indivisible.')+'</p><p>'+link('../analysis/index.html#inspect?request=failed',h('查看没有正文的结果','Inspect results without body text'))+'</p>')
  if fixture_correction:
   correction_rows=[]
   for provider,effect in fixture_correction['effects'].items():
    correction_rows.append(('',cell(link('../attempts/'+provider+'__'+fixture_correction['case_id']+'__r001.html',labels[provider]))+cell(pct(effect['old_text_edit']))+cell(pct(effect['new_text_edit']))+cell(esc(effect['old_output_sha256'][:12]))+cell(esc(effect['new_output_sha256'][:12]))))
   receipt_href='../../fixture-correction.json' if english else '../fixture-correction.json'
   review_body+=section(h('冻结页查询跳转的纠正','Frozen-page query-navigation correction'),'<p>'+h('一页冻结 HTML 在运行脚本后给当前路径加查询参数；原测试夹具只接受无查询参数的路径，三款方法因此拿到 404 错误页，却被记为有输出。现在仍从同一冻结 HTML 开始，夹具按路径找到同一页，原始网页 URL 仍不参与抓取。该页三款方法用相同二进制和原评分器重新测量；旧证据保留在独立存档里。','One frozen page appended a query parameter after its inline script ran. The former fixture served only the bare path, so three methods converted a 404 error page that was counted as output. The fixture now serves the same frozen page by path even with a query; the original source URL is still not fetched. The three methods were remeasured with matching binaries and the unchanged scorer; prior evidence remains in a separate archive.')+'</p>'+table([h('方法','Method'),h('原页 text_edit','Prior page text_edit'),h('纠正后 text_edit','Corrected page text_edit'),h('旧输出校验前缀','Prior output digest prefix'),h('新输出校验前缀','New output digest prefix')],correction_rows,cls='wide-prose-table',metrics=[])+f'<p>{link(receipt_href,h("下载完整纠正影响","Download complete correction effects"))}</p>')
  _wmb_write(out,'review/index.html',h('核验记录','Review'),review_body,english)

  guide=hero(h('同一网页，五种 Markdown 结果','One web page, five Markdown results'),h('200 页冻结 HTML 由五款浏览器分别转换。这里把内容质量、能否交付和耗时拆开看，并用同页证据解释差异。','Five browsers convert the same 200 frozen HTML pages. This guide separates content quality, delivery and latency, then uses same-page evidence to explain differences.'))
  guide_sections=[('background',h('01 测了什么','01 What was measured')),('results',h('02 质量、交付与耗时','02 Quality, delivery and latency')),('examples',h('03 同题实例','03 Same-page examples')),('configuration',h('04 浏览器与测试配置','04 Browsers and test configuration')),('limits',h('05 失败与正文缺失','05 Failures and missing content')),('outlook',h('06 结论与适用边界','06 Findings and limits'))]
  guide+='<div class="wrap"><nav class="wmb-guide-nav" aria-label="'+h('章节导航','Section navigation')+'">'+''.join('<a href="#'+anchor+'">'+esc(title)+'</a>' for anchor,title in guide_sections)+'</nav></div>'
  guide+=section(guide_sections[0][1],'<p class="wmb-guide-lead">'+h('本报告从 WebMainBench-545 母集预先选定 200 页。每款方法读到相同的官方冻结 HTML，并与同一份人工校准 Markdown 比较；没有请求原 URL 的实时内容。','This report uses 200 pages preselected from WebMainBench-545. Every method receives the same official frozen HTML and is compared with the same human-calibrated Markdown; original URLs are not fetched live.')+'</p><p>'+h('这 200 页先覆盖语言、难度、表格、代码、公式分类，再按固定哈希顺序补齐。它不是随机样本；以下结果只描述这 200 页，不代表母集、论文全量榜单或实时互联网。','Selection first covers language, difficulty, table, code and formula categories, then fills by a fixed hash order. It is not a random sample; the results describe only these 200 pages, not the parent set, the paper leaderboard or the live web.')+'</p><p class="actions">'+link('../datasets/index.html',h('查看选题分布和冻结清单','Selection distribution and frozen manifest'))+' · '+link('../methodology/index.html',h('查看官方指标与执行方法','Official metrics and execution'))+'</p>','background')
  guide+=section(guide_sections[1][1],'<p>'+h('首页用一张表回答两件事：输出与人工答案有多接近，以及完成一次转换花了多久。分项分数下方显示实际计入均值的页数；耗时则始终使用每款方法全部 200 次逻辑结果。逐页是否生成正文、接口留下了什么响应以及为什么为空，只在结果分析和详情页展开。','The home table answers two questions: how closely output matches the human reference, and how long a conversion takes. Each component shows its actual scored-page count below the value; latency always uses all 200 logical results per method. Whether body text exists, what response the interface left and why it is empty are expanded only in analysis and result details.')+'</p><p>'+h('TEDS 和 overall 使用本地修正版。修正的是评分器读取树节点的方式，没有改浏览器输出或计分页数。不同方法的分项均值仍可能来自不同页面，整体名次也不能代替对目标内容类型的检查。','TEDS and overall use a local correction to how the scorer reads tree nodes. Browser outputs and scored-page counts are unchanged. Component means can still cover different pages across methods, so overall order cannot replace inspection of the target content type.')+'</p><p class="actions">'+link('../index.html',h('查看核心结果','Core results'))+' · '+link('../analysis/index.html',h('筛选逐页响应','Filter page-level responses'))+'</p><p class="source-note">'+h('本轮没有可比较的 CPU、内存或网络测量，也没有官方统一的内容及格线。','This run has no comparable CPU, memory or network measurements, and no official uniform content-pass threshold.')+'</p>','results')
  guide+=section(h('表格文字保留，不代表结构保留','Table text can survive while structure is lost'),'<p>'+h('Moli 的 40 个计分表格页，人工答案都有表格，实际 Markdown 却没有评分器能识别的表格标签或 Markdown 表格。打开原文能看到很多单元格文字被串成普通段落：网页并非完全没返回，丢的是表格结构。官方于是对这 40 页逐页给 0；这是当前原生输出格式与官方表格定义不匹配，不能说浏览器请求全失败。','On all 40 Moli pages scored for tables, the reference has a table but the output contains no table markup recognized by the scorer. Many cell values are present as ordinary paragraphs in the archived Markdown: the page did return content, while table structure was lost. The official scorer therefore assigns 40 page-level zeros. This is an output-format mismatch with the official table definition, not 40 failed browser requests.')+'</p><p>'+h('Obscura 的 41 个计分页中，39 页实际输出没有可识别表格；另 2 页识别出表格，其中 1 页 table_edit 只有约 1%，但官方 TEDS 却给了约 99.8%。原始 TEDS 汇总的 2.4% 几乎全由这一条产生。该条预测 5 个节点、答案 570 个节点，官方“树距离”不到 1；源码把整棵树当字符串处理。修正树节点读取后，TEDS 汇总降至约 0.000732%；table_edit 汇总约 0.02346%。报告保留这些小数，不再把很低的非零分显示成 0.0%。','Of Obscura’s 41 scored pages, 39 outputs contain no recognized table. Two contain tables; one has about 1% table_edit but about 99.8% official TEDS. Nearly all of its original 2.4% TEDS aggregate came from that single page. Its prediction has 5 nodes versus 570 in the reference, while the recorded tree distance is below 1; the source treats the whole tree as a string. Correcting tree-node input reduces aggregate TEDS to about 0.000732%; aggregate table_edit is about 0.02346%. These small nonzero scores are no longer displayed as 0.0%.')+'</p><p class="actions">'+link('../attempts/moli__6edcd7ea-0503-4ed9-b493-4a6aad2ee584__r001.html',h('看表格文字被摊平的输出','Inspect flattened table content'))+' · '+link('../attempts/obscura__949e1468-eed2-43ab-96e6-5dcb07c52d4b__r001.html',h('看 5 对 570 节点的原文和评分','Inspect the 5-versus-570-node score'))+' · '+link('../methodology/index.html',h('看评分器处理表格的原理','How the table scorer works'))+'</p>')
  if fixture_correction:
   corrected_case=esc(fixture_correction['case_id'])
   affected=esc(', '.join(labels[name] for name in fixture_correction['providers']))
   guide+=section(h('错误页面也会被当成输出','An error page can be counted as output'),'<p>'+h(f'Track ID {corrected_case} 的冻结 HTML 在脚本执行后给本地路径加了查询参数。原夹具把这个路径当成未知页面，返回 404；{affected} 把 404 转成非空 Markdown，旧结果因此看似“交付成功”，正文相似度却低到约 0.9%。它不是三款浏览器同时漏掉同一段正文，而是我们的夹具发错了内容。',f'The frozen HTML for track ID {corrected_case} appended a query parameter to its local path after an inline script ran. The former fixture treated that path as unknown and returned 404. {affected} converted the 404 into non-empty Markdown: the prior records appeared delivered but scored around 0.9% on text. This was the fixture serving the wrong content, not three browsers independently losing the same article.')+'</p><p>'+h('保留同一 HTML、二进制和官方评分器后重新测量，三款方法该页的正文分已回到约 60.8%、63.4% 和 63.0%。报告现在展示的是纠正后的结果；旧输出和得分影响在核验记录中可查。','With the same HTML, binaries and official scorer, the three corrected page scores are about 60.8%, 63.4% and 63.0%. The report shows corrected results, while the prior output digests and score effects remain in the review record.')+'</p><p class="actions">'+link('../datasets/'+corrected_case+'.html',h('看这页的冻结输入和人工答案','Frozen input and human reference'))+' · '+link('../review/index.html',h('看三款方法的纠正影响','Correction effects for all three methods'))+'</p>')
  origin_case='ccb6033c-0a12-4f9c-8c68-794f26129841'
  if origin_case in cases:
   guide+=section(h('另一种低分：页面跳回原站','A different low score: navigation to the original site'),'<p>'+h('这页的内联脚本根据当前域名决定跳转地址。冻结 HTML 在本地隔离域名上运行时，脚本尝试离开本地夹具访问原站；网络隔离拒绝了这个跳转。Chromium 最终输出的是浏览器“无法访问此网站”，而非目标正文；Moli 和 Obscura 没有交付。Kitesurf 和 Lightpanda 在同一页取得了正文。这里的分歧首先暴露了冻结页的域名依赖，不能直接归因为三款方法的正文提取能力。', 'An inline script on this page chooses a redirect based on the current hostname. Running the frozen HTML on the isolated fixture host caused a navigation toward the original site, which the network sandbox denied. Chromium finally output a browser access-error page rather than the target text; Moli and Obscura did not deliver content. Kitesurf and Lightpanda obtained body text on the same page. This divergence first exposes the frozen page’s hostname dependency and cannot be attributed solely to three methods’ extraction quality.')+'</p><p>'+h('冻结 HTML 和人工答案不能为了让浏览器通过而悄悄改写。这页继续保留在 200 页矩阵中，并标记为解释受限；若要消除此限制，需对所有方法以同一新输入和网络条件重新实验。','The frozen HTML and human reference must not be silently rewritten to make a browser pass. This page remains in the 200-page matrix with a limited interpretation; removing the limitation requires a new, identical input and network setup for every method.')+'</p><p class="actions">'+link('../datasets/'+origin_case+'.html',h('查看内联脚本和官方参考','Inline script and reference'))+' · '+link('../analysis/index.html#inspect?query='+origin_case,h('查看同页五方法结果','Five method results for the page'))+'</p>')
  mixed_case=next((c for c in cases.values() if 0<sum(r['_body_present'] for r in bycase[c['id']])<len(names)),None)
  def text_scores(c):
   return [r['metrics']['text_edit']['score'] for r in bycase[c['id']] if r['ok'] and r.get('metrics',{}).get('text_edit',{}).get('success')]
  scored_cases=[(max(text_scores(c))-min(text_scores(c)),c) for c in cases.values() if len(text_scores(c))>=2]
  quality_case=max(scored_cases,key=lambda item:(item[0],item[1]['id']))[1] if scored_cases else None
  example_cases=[]
  if mixed_case:example_cases.append((mixed_case,h('是否生成正文出现分歧','Body-text outcome differs')))
  if quality_case and (not mixed_case or quality_case['id']!=mixed_case['id']):example_cases.append((quality_case,h('已交付方法的 text_edit 差距较大','Large text_edit spread among delivered methods')))
  for metric,title in [('code_edit',h('代码块差异','Code-block difference')),('formula_edit',h('公式差异','Formula difference')),('table_TEDS',h('表格结构差异','Table-structure difference'))]:
   candidates=[]
   for c in cases.values():
    values=[r['metrics'][metric]['score'] for r in bycase[c['id']] if r['ok'] and r.get('metrics',{}).get(metric,{}).get('success')]
    if len(values)>=3:candidates.append((max(values)-min(values),c['id'],c))
   if candidates:
    chosen=max(candidates)[2]
    if chosen['id'] not in {item[0]['id'] for item in example_cases}:example_cases.append((chosen,title))
  example_panels=[]
  for index,(c,reason) in enumerate(example_cases,1):
   rs=bycase[c['id']]
   source=urlsplit(c['url']);source_label=(source.netloc+source.path)[:105] or c['url']
   example_body='<p>'+h('从五款方法的同页结果继续打开单次调用，就能并排核对完整人工真值、最终输出与每项评分原因。此页因方法之间的差别较大而选作实例，不代表总体平均。','Open a same-page method result, then its call, to compare the complete human reference, final output and each component scorer reason. This page is selected for its large cross-method difference, not as a representative average.')+'</p><h3>'+link('../datasets/'+c['id']+'.html',source_label)+'</h3><p><code>'+esc(c['id'])+'</code> · '+esc(reason)+' · '+h('生成正文的方法','Methods with body text')+f': {sum(r["_body_present"] for r in rs)}/{len(names)}</p>'+table([h('方法','Method'),h('是否生成正文','Body text produced'),'text_edit','code_edit','formula_edit','table_TEDS',h('逐页 overall','Per-page overall')],[('',cell(link('../attempts/'+r['attempt_id']+'.html',labels[r['provider']]))+cell(h('有正文','Yes') if r['_body_present'] else h('无正文','No'))+''.join(_wmb_metric_cell(r,m) for m in ('text_edit','code_edit','formula_edit','table_TEDS','overall'))) for r in sorted(rs,key=lambda row:names.index(row['provider']))],cls='wmb-guide-example-table',metrics=[])
   example_panels.append((str(index),reason,example_body))
  guide+=section(guide_sections[2][1],tabs('wmb-guide-examples',example_panels)+'<p class="actions">'+link('../analysis/index.html#comparison',h('按交付组合找更多同题实例','Find more same-page delivery patterns'))+'</p>','examples')
  guide_config=[]
  for name in names:
   identity=next(m['implementation_identity'] for m in manifests if m['provider']==name)
   input_route=h('Cloudflare Browser Rendering 的 html 输入','Cloudflare Browser Rendering html input') if name=='kitesurf' else h('隔离的 loopback HTTPS 冻结网页','Isolated loopback HTTPS frozen page')
   output_route=h('渲染 DOM 后用 node-html-markdown 转换','Rendered DOM converted by node-html-markdown') if name=='chromium' else h('浏览器或 CLI 原生 Markdown','Browser or CLI native Markdown')
   guide_config.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(input_route)+cell(output_route)+cell(esc(identity.get('reported_version') or h('托管版本未公开','Hosted version undisclosed')))+cell(link('../reproduce/index.html',h('核对批次与哈希','Run and hash evidence')))))
  guide+=section(guide_sections[3][1],'<p>'+h('四款本地方法从网络隔离的 loopback HTTPS 读取相同冻结 HTML；Kitesurf 直接使用 Cloudflare 官方 html 输入。冻结页里的内联脚本可以执行，外部子资源不能出网。每款方法内部串行调用，版本和批次身份单独记录。','Four local methods read the same frozen HTML via network-isolated loopback HTTPS; Kitesurf uses Cloudflare’s documented html input. Inline scripts in the frozen page may run, while external subresources cannot reach the network. Each method runs calls serially, with version and batch identity recorded separately.')+'</p>'+table([h('方法','Method'),h('输入','Input'),h('输出路线','Output route'),h('报告版本','Reported version'),h('运行证据','Run evidence')],guide_config,cls='wide-prose-table',metrics=[]),'configuration')
  fail_total=sum(len(failures(name)) for name in names)
  guide_fail_rows=[]
  for name in names:
   counts=Counter(r['_output_error'] or 'unknown' for r in failures(name))
   guide_fail_rows.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(link(_wmb_inspect('../analysis/index.html',method=name,request='failed'),str(len(failures(name)))))+cell('、'.join(f'{esc(key)}: {value}' for key,value in sorted(counts.items())) or '—')))
  guide+=section(guide_sections[4][1],'<p>'+h(f'1,000 条逻辑结果中有 {fail_total} 条没有生成 Markdown 正文。下面按方法和错误类型汇总；没有正文与正文低分是两件事。有正文的页面仍可能漏文字、代码、公式或表格，须对照真值和实际输出。“—”可能是该项没有可评分内容，也可能是评分错误。',f'{fail_total} of 1,000 logical results produced no Markdown body text. The table summarizes method and error type; no body text and low body-text quality are different events. A page with body text may still omit text, code, formulas or tables; compare the reference with actual output. “—” may be inapplicability or a scorer error.')+'</p>'+table([h('方法','Method'),h('没有正文','No body text'),h('错误类型','Error types')],guide_fail_rows,metrics=[])+'<p class="actions">'+link('../analysis/index.html#inspect?request=failed',h('逐条看没有正文的结果','Inspect results without body text'))+' · '+link('../analysis/index.html#inspect?request=success&metric=text_edit&band=0%3A0.25',h('查看有正文但文字低分','Body text produced but low text score'))+' · '+link('../review/index.html',h('独立核验记录','Independent review'))+'</p>','limits')
  failure_notes={}
  for finding in (anomaly_review or {}).get('cases',[]):
   observed=finding['observed_en' if english else 'observed_zh']
   cause=finding['cause_en' if english else 'cause_zh']
   for provider in finding.get('affected_providers',[]):
    failure_notes[(provider,finding['case_id'])]=(observed,cause)
  delivery_leader=max(names,key=lambda name:case_count-len(failures(name)))
  guide+=section(guide_sections[5][1],'<p>'+h(f'在这 200 页里，{labels[delivery_leader]} 生成正文的页面最多（{case_count-len(failures(delivery_leader))}/{case_count}）。这只说明正文存在。修正后的 TEDS 排除了已证实的计算错误，但计分页数、内容类型和冻结页运行限制仍影响比较。Moli 与 Obscura 的表格低分保留了输出格式损失这一事实。选择方法时应先看目标内容类型对应的逐页输出，再看全部 200 次调用的耗时。',f'On these 200 pages, {labels[delivery_leader]} produces body text on the most pages ({case_count-len(failures(delivery_leader))}/{case_count}). This only establishes that body text exists. Corrected TEDS removes the proven calculation defect, but scored-page counts, content types and frozen-page runtime limits still affect comparisons. The low table scores for Moli and Obscura continue to reflect output-format loss. Inspect page-level output for the target content type before latency across all 200 calls.')+'</p><p>'+h('这次给方法的是冻结 HTML，不是实时网页。需要比较当前网站、外部资源加载或 545 页母集，应另做相应实验。','Methods received frozen HTML, not live webpages. Current-site behavior, external-resource loading and the complete 545-page parent set require separate experiments.')+'</p>','outlook')
  _wmb_write(out,'guide/index.html',h('报告解读','Guide'),guide,english)

  provider_rows=[]
  identities={m['provider']:m['implementation_identity'] for m in manifests}
  for name in names:
   ident=identities[name]
   provider_rows.append(('',cell(labels[name])+cell(esc(ident.get('reported_version') or h('托管后端未公开 / nightly 无版本命令','Hosted backend undisclosed / nightly has no version command')))+cell('<code class="tiny break">'+esc(ident.get('binary_sha256') or '—')+'</code>')+cell(esc(ident.get('deployment')))))
  adoption=hero(h('方法介绍','Methods'),h('比较的是五种执行实现，不再把旧 Moli 版本重复算作不同浏览器。','The comparison covers five implementations; historical Moli versions are no longer counted as separate browsers.'))
  adoption+=section(h('本轮实现','Measured implementations'),table([h('方法','Method'),h('版本','Version'),h('二进制 SHA-256','Binary SHA-256'),h('部署','Deployment')],provider_rows,metrics=[]))
  interface_rows=[]
  routes={
   'kitesurf':(h('Cloudflare Browser Run /markdown?browser=kitesurf','Cloudflare Browser Run /markdown?browser=kitesurf'),h('POST html=官方冻结 HTML；拒绝外部资源类型','POST html=official frozen HTML; reject external resource types'),h('服务端原生 Markdown','Server-native Markdown')),
   'lightpanda':('Lightpanda CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
   'obscura':('Obscura CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
   'moli':('Moli CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
   'chromium':('Playwright Chromium',h('渲染冻结页面 DOM → node-html-markdown 2.0.0','Render frozen page DOM → node-html-markdown 2.0.0'),h('转换后的 Markdown','Converted Markdown')),
  }
  for name in names:
   example=next((r for r in records if r['provider']==name and r['ok']),next(r for r in records if r['provider']==name))
   entry,input_route,output_route=routes[name]
   official=PROFILES[name][2]
   interface_rows.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(esc(entry))+cell(esc(input_route))+cell(esc(output_route))+cell(link('../attempts/'+example['attempt_id']+'.html',h('查看实际输入、返回和逐项评分','Actual input, output and component scores')))+cell(link(official,h('官方获取入口','Official source')))))
  adoption+=section(h('本轮输入、输出与实例','Run inputs, outputs and examples'),'<p>'+h('这里列的是公开集冻结 HTML 的实际路线，不是原 URL 的实时抓取接口。实例链接对应存档调用；账号字段只展示变量名和占位符。','These are the frozen-HTML routes used for this public benchmark, not live fetching of original URLs. Example links open archived calls; account fields show variable names or placeholders only.')+'</p>'+table([h('方法','Method'),h('接口 / 程序','Interface / program'),h('本轮输入','Run input'),h('本轮输出','Run output'),h('真实调用实例','Archived call example'),h('官方来源','Official source')],interface_rows,cls='wide-prose-table',metrics=[]))
  if research:
   use_rows=[]
   for item in research.get('direct_benchmarks',[]):
    source_links='<br>'.join(link(url,urlsplit(url).netloc) for url in item.get('sources',[]))
    use_rows.append(('',cell(esc(item['name']))+cell(esc('；'.join(item.get('users',[])) or '—'))+cell(esc(item.get('maturity' if english else 'maturity_zh','—')))+cell(source_links)))
   adoption+=section(h('公开评测集的具名使用','Named use of public benchmarks'),'<p>'+h('论文、可执行评测仓库和框架集成可证明公开使用；没有可独立核验的商业生产部署数量。这里不把 GitHub Star 或“支持某工具”写成生产采用。','Papers, executable benchmark repositories and framework integrations document public use; no independently auditable commercial production-deployment total was found. Stars or extractor support are not presented as production adoption.')+'</p>'+table([h('评测集','Benchmark'),h('已核对使用者','Verified users'),h('判断','Assessment'),h('一手证据','Primary evidence')],use_rows,metrics=[]))
  if adoption_data:
   counts=adoption_counts(adoption_data,sorted(PROFILES))
   (out/'adoption/counts.json').write_text(json.dumps({'snapshot':adoption_data['generated_at'],'scope':'verified source-signature hits in a bounded candidate set, not production deployment','methods':counts},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
   count_rows=[]
   details=[]
   for item in counts:
    name=item['provider'];n=item['repository_count'];known=n is not None
    label=labels.get(name,name)+(h('（未参评）',' (not measured)') if name not in names else '')
    count_rows.append(('',cell(link('#evidence-'+name,label) if known else esc(label))+cell(str(n) if known else '—',n if known else -1)+cell(str(item['file_count']) if known else '—',item['file_count'] if known else -1)))
    if known:
     evidence_rows=[('',cell(link(repo['url'],repo['repo']))+cell('<br>'.join(link(match['url'],match['path']) for match in repo['matches']))) for repo in item['repositories']]
     details.append('<details id="evidence-'+esc(name)+'"><summary>'+esc(label)+f' · {n} '+h('个候选仓库','candidate repositories')+'</summary>'+table([h('仓库','Repository'),h('命中文件','Matched files')],evidence_rows,metrics=[] )+'</details>')
   data_prefix='../../adoption/' if english else ''
   adoption+=section(h('开源采用线索','Open-source adoption evidence'),'<p>'+h('这份独立源码快照包括已登记方法，不只包括本轮五款浏览器。搜索候选有排序、活跃度和星数门槛；数值是已核验候选集中的签名命中，不是全网份额或生产用户数。点击方法可核对仓库和文件。','This independent source snapshot covers registered methods, not only the five measured browsers. Search candidates have ranking, activity and star thresholds; the counts are verified signature hits in the bounded candidate set, not global share or production users. Open a method to inspect repositories and files.')+'</p><p class="source-note">'+esc(adoption_data['generated_at'])+' · '+link(data_prefix+'evidence.json',h('下载完整来源证据','Full source evidence'))+' · '+link(data_prefix+'counts.json',h('下载逐方法统计','Per-method counts'))+'</p>'+table([h('方法','Method'),h('命中仓库','Matched repositories'),h('命中文件','Matched files')],count_rows,'wmb-adoption-counts',True,metrics=[])+''.join(details))
  _wmb_write(out,'adoption/index.html',h('方法介绍','Methods'),adoption,english)

  reproduce=hero(h('环境与复现','Reproduction'),h('数据、评分器、浏览器与结果都由哈希或提交固定。','Dataset, scorer, browsers and outputs are fixed by hashes or commits.'))
  reproduce+=section(h('核心复现指令','Core reproduction commands'),'<p>'+h('以下命令使用本仓库锁定依赖；运行五款方法和官方评分需要先取得公开母集、固定提交的评分器和本轮方法二进制。评测凭据只从仓库本地 .env 读取，不进入报告。命令中的尖括号为本机路径占位符。','The commands use this repository’s locked dependencies. Running all five methods and official scoring first requires the public parent set, the pinned scorer checkout and the measured binaries. Evaluation credentials come only from the local repository .env and are not included in this report. Angle brackets denote local paths.')+'</p><pre>uv run --frozen --extra headless-eval python scripts/run_webmainbench_benchmark.py --provider &lt;name&gt; --case-ids-file evaluation/public-webmainbench-200.json --output &lt;method-evidence&gt;\nuv run --frozen --extra webmainbench-eval python scripts/score_webmainbench_results.py --evidence &lt;method-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen python scripts/assemble_webmainbench_evidence.py --source-root &lt;five-method-batches&gt; --output &lt;assembled-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --sample-manifest evaluation/public-webmainbench-200.json --review &lt;independent-review.json&gt;\nuv run --frozen --extra webmainbench-eval python scripts/correct_webmainbench_teds.py --source &lt;assembled-evidence&gt; --output &lt;corrected-evidence&gt; --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen --extra webmainbench-eval python scripts/review_webmainbench_teds_correction.py --source &lt;assembled-evidence&gt; --corrected &lt;corrected-evidence&gt;\nmake public-benchmark-report EVIDENCE=&lt;corrected-evidence&gt;</pre>')
  reproduce+=section(h('固定标识','Pinned identities'),f'<p>Parent dataset SHA-256: <code>{esc(dataset["source_dataset_sha256"])}</code></p><p>Sample manifest SHA-256: <code>{esc(dataset["sample_manifest_sha256"])}</code> · {link("../sample-manifest.json" if not english else "../../sample-manifest.json",h("下载 200 页清单","Download 200-page manifest"))}</p><p>Official scorer commit: <code>9d991bdc00c57b57521499494d96be85c31317ba</code></p>')
  batch_rows=[]
  for manifest in manifests:
   batch_rows.append(('',cell(labels[manifest['provider']])+cell(esc(manifest['started_at']))+cell(esc(manifest['completed_at']))+cell(str(manifest['case_count']))+cell(str(manifest['timeout_seconds']))+cell('<code class="tiny break">'+esc(manifest['runner_sha256'])+'</code>')))
  reproduce+=section(h('实际执行批次','Observed execution batches'),table([h('方法','Method'),h('开始（UTC）','Started (UTC)'),h('完成（UTC）','Completed (UTC)'),h('案例','Cases'),h('截止秒','Deadline seconds'),h('Runner SHA-256','Runner SHA-256')],batch_rows,metrics=[]))
  _wmb_write(out,'reproduce/index.html',h('环境与复现','Reproduction'),reproduce,english)

  changes=hero(h('变更记录','Changes'),h('本版从自建 150 题矩阵切换为 WebMainBench-545 的预选 200 页公开子集。','This release replaces the custom 150-task matrix with a preselected 200-page public subset of WebMainBench-545.'))
  changes+=section(h('本版变化','Changes in this edition'),'<p>'+h('每页可以从 Track ID 进入输入、人工答案、实际输出和逐项评分；结果矩阵同时显示交付状态、最终内容长度与可识别的错误页面。评测方法给出每项算法、计分页数形成过程和已发现的 TEDS 结构评分缺陷。证据与算术检查通过，不代表综合分已通过有效性审查。','Track IDs now lead to the frozen input, human reference, actual output and component scores. The result matrix shows delivery, final-output length and recognizable error pages. The methodology details each component, scored-page inclusion and the demonstrated TEDS structural-score defect. Passing evidence and arithmetic checks does not validate the overall score.')+'</p>')
  if fixture_correction:
   affected=', '.join(labels[name] for name in fixture_correction['providers'])
   changes+=section(h('冻结网页 404 夹具错误','Frozen-page fixture 404 correction'),'<p>'+h(f'一页冻结网页在脚本执行后给当前路径添加查询参数。原本地夹具错误返回 404，{affected} 把错误页转成了非空输出。三款方法现以同一冻结输入、二进制和官方评分器完成整页纠正测量；正文、公式和 overall 的均值及该页耗时随之变化，旧物理调用证据不被覆盖。',f'One frozen page appended a query parameter to its current path. The former local fixture mistakenly returned 404, and {affected} converted that error page to non-empty output. The full page was remeasured for the three methods with the same frozen input, binaries and official scorer; text, formula and overall means and page latency changed. Prior physical-call evidence was not overwritten.')+'</p><p class="actions">'+link('../review/index.html',h('查看逐方法纠正影响','Per-method correction effects'))+'</p>')
  changes+=section(h('读者可见变化','Reader-visible changes'),'<ul><li>'+h('五款浏览器，Moli 升级到 2026-09-15 核验的最新正式版 v1.1.5。','Five browsers; Moli is upgraded to v1.1.5, verified as the latest release on 2026-09-15.')+'</li><li>'+h('正式范围是结果产生前冻结的 200 页，不是母集全部 545 页；页面公开选择算法、种子、分类分布与清单哈希。','The formal scope is the 200 pages frozen before results, not all 545 parent pages; the selection algorithm, seed, category distributions and manifest hash are published.')+'</li><li>'+h('公开冻结输入、官方六项指标、逐页分数、分类切片与逐指标分母，并区分逐页 overall 和榜单 overall。','Frozen public inputs, six official metrics, per-page scores, category slices and per-metric denominators are published, with per-page and leaderboard overall kept distinct.')+'</li><li>'+h('质量、请求成功率、失败和耗时分开报告；受连续 DNS 故障污染的 Kitesurf 批次完整废弃后从头重跑，不拼接也不择优。','Quality, request success, failures and latency are separate. The Kitesurf batch contaminated by a continuous DNS outage was wholly superseded and rerun from the start, without splicing or best-response selection.')+'</li><li>'+h('公开评测集调研加入具名采用证据，并把 WCXB 作为有用但暂不适合作为唯一可信度锚点的保留集。','Public-benchmark research now includes named adoption evidence and treats WCXB as useful but not suitable as the sole credibility anchor.')+'</li></ul>')
  changes+=section(h('逐条证据入口','Individual evidence access'),'<p>'+h('榜单、失败数和分类切片现在可直接打开带筛选条件的完整结果矩阵；每条结果可继续查看五项分数与错误原因、物理重试、官方人工真值、实际输出和冻结输入。此项只改变查阅方式，不改变成绩、分母、范围或调用数。','Leaderboard, failure counts and category slices now lead directly to the filtered full result matrix. Each result then opens component scores and error details, physical retries, official human reference, actual output and frozen input. This changes navigation only, not scores, denominators, scope or call counts.')+'</p>')
  changes+=section(h('同题对比与报告解读','Same-page comparison and interpretation'),'<p>'+h(f'结果分析可按方法组合、官方分类和交付失败类型查找差异；逐条矩阵默认定位 {failed_request_count} 条未交付结果，也可切换到已交付或全部 1,000 条。首页分开显示交付、端到端耗时和五项官方分数；报告解读用同页原文说明低表格分数、夹具 404 和原站跳转。交付组合不等于官方质量通过。',f'Analysis filters differences by method group, official category and delivery-failure type. The individual matrix initially locates {failed_request_count} undelivered results, or can show delivered and all 1,000 results. The home page separates delivery, end-to-end latency and five official components. The guide uses original same-page evidence to explain low table scores, the fixture 404 and origin-site navigation. Delivery patterns are not official content-pass verdicts.')+'</p><p>'+h('方法介绍保留各浏览器的实际输入路线、输出实例和具名公开采用证据。样本与官方评分器提交没有改变；夹具错误纠正后的三款方法结果和耗时已改变，完整影响见上文。','The methods section retains each browser’s actual input route, output example and named public-adoption evidence. The sample and pinned official scorer commit did not change. The three methods’ corrected fixture-page results and latency did change; the effects are linked above.')+'</p>')
  if corrections:
   correction=next((item for item in corrections.get('corrections',[]) if item.get('id')=='kitesurf-dns-outage-complete-scope-rerun'),None)
   if correction:
    before=correction['superseded'];after=correction['corrected']
    def correction_row(label,key,formatter=str):return ('',cell(label)+cell(formatter(before[key]))+cell(formatter(after[key])))
    correction_rows=[
     correction_row(h('请求成功','Request successes'),'request_successes'),correction_row(h('请求失败','Request failures'),'request_failures'),correction_row(h('物理调用','Physical attempts'),'physical_attempts'),
     correction_row('overall','official_scores',lambda value:pct(value['overall'])),
     ('',cell('text n')+cell(str(before['denominators']['text_edit']))+cell(str(after['denominators']['text_edit']))),
     ('',cell('code n')+cell(str(before['denominators']['code_edit']))+cell(str(after['denominators']['code_edit']))),
     ('',cell('formula n')+cell(str(before['denominators']['formula_edit']))+cell(str(after['denominators']['formula_edit']))),
     ('',cell('table n')+cell(str(before['denominators']['table_edit']))+cell(str(after['denominators']['table_edit']))),
     ('',cell('TEDS n')+cell(str(before['denominators']['table_TEDS']))+cell(str(after['denominators']['table_TEDS']))),
     correction_row(h('累计耗时','Total elapsed'),'total_elapsed_ms',ms),correction_row('P50','median_elapsed_ms',ms),correction_row(h('P95 耗时','P95 elapsed'),'p95_elapsed_ms',ms),
    ]
    correction_href='../webmainbench-corrections.json' if not english else '../../webmainbench-corrections.json'
    changes+=section(h('Kitesurf DNS 故障纠正影响','Kitesurf DNS-outage correction effects'),'<p>'+esc(correction['reason_zh' if not english else 'reason'])+'</p>'+table([h('项目','Measure'),h('废弃批次','Superseded batch'),h('纠正批次','Corrected batch')],correction_rows,metrics=[])+f'<p>{link(correction_href,h("下载机器可读纠正记录","Download machine-readable correction record"))}</p>')
  _wmb_write(out,'changes/index.html',h('变更记录','Changes'),changes,english)

  account_rows=[]
  for name in names:
   access=HEADLESS_ACCESS[name]
   account_rows.append(('',cell(labels[name])+cell(esc(access['account']))+cell('<br>'.join('<code>'+esc(v)+'</code>' for v in access['env_vars']))+cell('<br>'.join(link(url,label) for label,url in access['links']))))
  accounts=hero(h('账号与安装','Accounts and setup'),h('只有 Kitesurf 的 Cloudflare 路线需要账号凭据；报告不公开值。','Only the Kitesurf Cloudflare route needs account credentials; values are never published.'))
  accounts+=section(h('配置','Configuration'),table([h('方法','Method'),h('账号','Account'),h('变量','Variables'),h('官方入口','Official source')],account_rows,cls='accounts-table',metrics=[]))
  _wmb_write(out,'accounts/index.html',h('账号与安装','Accounts and setup'),accounts,english)

  for c in cases.values():
   ref=contained_path(out,'references/'+c['id']+'.md').read_text(encoding='utf-8')
   frozen_html=contained_path(out,'sources/'+c['id']+'.html.txt').read_text(encoding='utf-8',errors='replace')
   ref_prefix='../../' if english else '../'
   body=hero(h('冻结页面','Frozen page')+' · '+c['id'],h('官方网页快照与人工校准参考文本','Official HTML snapshot and human-calibrated reference'))
   body+=section(h('来源与元数据','Source and metadata'),f'<p>{link(c["url"],h("原始 URL ↗","Original URL ↗"))}</p><pre>{esc(json.dumps(c["meta"],ensure_ascii=False,indent=2))}</pre><p>HTML SHA-256: <code>{esc(c["input_html_sha256"])}</code></p>')
   body+=section(h('人工校准参考文本','Human-calibrated reference'),'<p>'+h('这份 Markdown 是该页的完整参考。评分器从转换结果与参考文本中提取正文、代码、公式和表格，并计算五项分数。','This Markdown is the complete page reference. The scorer extracts text, code, formulas and tables from each conversion and reference, then calculates five components.')+'</p><p class="actions">'+link(ref_prefix+'references/'+c['id']+'.md',h('下载参考文本','Download reference'))+' · '+link('../methodology/index.html',h('查看官方计算方法','Official scoring method'))+'</p><pre class="wmb-source-pre">'+esc(ref)+'</pre>')
   body+=section(h('冻结 HTML 输入','Frozen HTML input'),'<p>'+h('五种方法均使用下方同一份冻结 HTML；原始 URL 只用于标识来源，不参与实时抓取。','All five methods receive this same frozen HTML; the source URL identifies provenance and was not fetched live.')+'</p><p class="actions">'+link(ref_prefix+'sources/'+c['id']+'.html.txt',h('下载完整 HTML','Download full HTML'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('查看该页方法结果','Inspect method results for this page'))+'</p><pre class="wmb-source-pre">'+esc(frozen_html)+'</pre>')
   _wmb_write(out,'datasets/'+c['id']+'.html',c['id'],body,english)

  for name in names:
   rs=[r for r in records if r['provider']==name]
   rows=[]
   for r in rs:
    c=cases[r['case_id']]
    output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
    state_class='success' if r['ok'] and output.strip() and not _wmb_error_page(output) else 'failure'
    request=f'<span class="wmb-pill {state_class}">{esc(_wmb_body_state(r,output,english))}</span>'
    values=cell(link('../attempts/'+r['attempt_id']+'.html',r['case_id']))+cell(esc(_wmb_category(c,'language')))+cell(esc(_wmb_category(c,'level')))+cell(esc(_wmb_category(c,'table')))+cell(esc(_wmb_category(c,'code')))+cell(esc(_wmb_category(c,'equation')))+cell(request)+cell(esc(_wmb_output_label(r,output,english)))
    values+=''.join(_wmb_metric_cell(r,metric) for metric in metric_order[1:])
    values+=cell(ms(r['total_elapsed_ms']),r['total_elapsed_ms'])+cell(link('../datasets/'+r['case_id']+'.html',h('页面与参考','Page and reference')))
    rows.append((_wmb_row_attrs(c,r),values))
   body=hero(labels[name],h(f'{case_count} 条逻辑结果；失败重试的耗时已累计。',f'{case_count} logical results; failed-retry latency is accumulated.'))
   body+=section(h('逐页结果','Per-page results'),'<p class="source-note">'+h('点 Track ID 查看评分、完整响应和原因，末列打开冻结页面与人工参考。“正文已生成”只说明存在可读 Markdown，不等于内容通过；错误页会在“响应与正文”列明确标出。','Open a track ID for scores, the complete response and cause; the last column opens the frozen page and human reference. “Body text produced” only states that readable Markdown exists, not that content passed; error pages are explicit in the response column.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('表格可横向和纵向滚动。','Scroll horizontally and vertically within the table.')+'</p>'+_wmb_filter_table([h('Track ID','Track ID'),h('语言','Language'),h('难度','Difficulty'),h('表格','Table'),h('代码','Code'),h('公式','Formula'),h('是否生成正文','Body text produced'),h('响应与正文','Response and body') ]+[metric_labels[m] for m in metric_order[1:]]+[h('总耗时','Total latency'),h('页面与参考','Page and reference')],rows,'wmb-provider-'+name,english))
   _wmb_write(out,'providers/'+name+'.html',labels[name],body,english)

  for r in records:
   c=cases[r['case_id']];case_id=r['case_id'];attempt_id=r['attempt_id']
   output_path=contained_path(out,r['artifact_path'])
   output_text=output_path.read_text(encoding='utf-8',errors='replace') if output_path.is_file() else ''
   reference_text=contained_path(out,'references/'+case_id+'.md').read_text(encoding='utf-8')
   metric_rows=[]
   for metric in metric_order:
    item=r.get('metrics',{}).get(metric,{})
    detail=item.get('details',{})
    concise=[]
    for key in ('distance','predicted_length','groundtruth_length','predicted_text_length','groundtruth_text_length','predicted_code_length','groundtruth_code_length','predicted_formula_length','groundtruth_formula_length','predicted_table_length','groundtruth_table_length'):
     if key in detail:concise.append(f'{key}: {detail[key]}')
    if item.get('error'):concise.append(item['error'])
    description='<br>'.join(esc(value) for value in concise) or '—'
    if detail:description+=f'<details><summary>{h("完整评分字段","Complete scorer fields")}</summary><pre>{esc(json.dumps(detail,ensure_ascii=False,indent=2))}</pre></details>'
    if corrected and metric in ('overall','table_TEDS'):
     original=r.get('official_metrics',{}).get(metric,{})
     original_score=display_score(original.get('score')) if original.get('success') else '—'
     description+='<details class="wmb-original-score"><summary>'+h('官方原始分：','Original official score: ')+original_score+'</summary><pre>'+esc(json.dumps(original,ensure_ascii=False,indent=2))+'</pre></details>'
    metric_rows.append(('',cell(metric_labels[metric])+cell(h('已计算','Calculated') if item.get('success') else h('未计算','Not calculated'))+cell(display_score(item.get('score')) if item.get('success') else '—',item.get('score') if item.get('success') else -1)+cell(description)))
   delivery=_wmb_body_state(r,output_text,english)
   metadata=r.get('provider_metadata',{}) or {}
   failure_text=(esc(r.get('error_type') or 'unknown')+': '+esc(r.get('error_message') or '') if not r['ok']
                 else h('程序返回的是浏览器错误页，不是目标网页正文。','The program returned a browser error page rather than target-page body text.') if _wmb_error_page(output_text) else '')
   raw_prefix='../../' if english else '../'
   attempt_body=hero(labels[r['provider']],h('Track ID: ','Track ID: ')+case_id)
   attempt_body+='<div class="wrap"><p class="actions">'+link('../datasets/'+case_id+'.html',h('查看该页输入与参考','Page input and reference'))+' · '+link('../providers/'+r['provider']+'.html',h('该方法全部结果','All results for this method'))+' · '+link('../analysis/index.html',h('返回结果筛选','Back to result filters'))+'</p></div>'
   call_state=(h('Cloudflare 接口 HTTP ','Cloudflare API HTTP ')+str(r['status_code']) if r['provider']=='kitesurf' and r.get('status_code') is not None else h('Cloudflare 接口没有返回 HTTP 状态','No Cloudflare API HTTP status') if r['provider']=='kitesurf' else h('本地程序退出码 ','Local worker exit code ')+str(metadata.get('exit_code','—')))
   output_state=_wmb_output_label(r,output_text,english)
   attempt_body+=section(h('接口返回和最终内容','API/worker response and final content'),'<div class="wmb-attempt-summary">'+''.join('<article class="card"><b>'+esc(label)+'</b><strong>'+esc(value)+'</strong></article>' for label,value in [(h('接口或程序','API or worker'),call_state),(h('最终内容','Final content'),output_state),(h('浏览器进程退出码','Browser process exit code'),str(metadata.get('browser_exit_code','—')))])+'</div><p class="source-note">'+h('本地程序退出码不是源站 HTTP 状态；本轮读取冻结 HTML，未请求原始 URL。最终内容为空与接口完全无响应也不是同一件事：上面的状态、下方错误信息和完整供应商字段应合起来看。','A local worker exit code is not an origin-site HTTP status; this run read frozen HTML instead of the original URL. Empty final content is different from no API/worker response: inspect the state above together with the error and raw provider fields below.')+'</p>'+(('<p class="callout failure">'+h('程序返回了非空文字，但文字是错误页面。此页的官方分数不能代表目标网页的提取质量。','The program returned non-empty text, but it is an error page. Its official score does not measure extraction quality on the target page.')+'</p>') if _wmb_error_page(output_text) else ''))
   attempt_body+=section(h('这次转换的结果','Conversion outcome'),'<div class="wmb-attempt-summary">'+''.join(f'<article class="card"><b>{label}</b><strong>{esc(value)}</strong></article>' for label,value in [(h('是否生成正文','Body text produced'),delivery),(h('逐页综合分','Per-page overall'),pct(r.get('metrics',{}).get('overall',{}).get('score')) if r.get('metrics',{}).get('overall',{}).get('success') else '—'),(h('累计耗时','Total elapsed'),ms(r['total_elapsed_ms']))])+'</div><p class="source-note">'+h('“正文已生成”只表示最终得到可读 Markdown，不是质量通过。官方没有逐页通过阈值；逐页分只平均本页成功计算的指标，和首页 overall 的算法不同。','“Body text produced” only means readable Markdown was returned, not that quality passed. The official benchmark has no per-page pass threshold; per-page score averages only components calculated on this page and differs from home-page overall.')+'</p>'+('<div class="callout failure"><b>'+h('没有生成目标正文','No target body text produced')+'</b><p>'+failure_text+'</p></div>' if not r['_body_present'] else '')+'<p class="actions">'+link(raw_prefix+'sources/'+case_id+'.html.txt',h('冻结输入 HTML','Frozen input HTML'))+' · '+link(raw_prefix+r['artifact_path'],h('完整方法输出','Complete method output'))+'</p><details><summary>'+h('来源、标识与哈希','Provenance, IDs and hashes')+'</summary><div class="wmb-provenance"><p>Run ID: <code>'+esc(r.get('run_id','—'))+'</code><br>Attempt ID: <code>'+esc(attempt_id)+'</code><br>Track ID: <code>'+esc(case_id)+'</code></p><p>Input SHA-256: <code class="break">'+esc(c['input_html_sha256'])+'</code><br>Reference SHA-256: <code class="break">'+esc(c['reference_sha256'])+'</code><br>Output SHA-256: <code class="break">'+esc(r.get('output_sha256','—'))+'</code></p><p>'+link(c['url'],h('原始来源 URL','Original source URL'))+'</p></div></details>')
   if not r['_body_present']:
    observed,interpretation=failure_notes.get((r['provider'],case_id),(h('程序没有生成目标正文','Worker produced no target body text'),h('现有证据不能再区分服务内部阶段；详情保留了完整响应，避免把未知原因写成浏览器结论。','The available evidence cannot separate the service-internal stage further. The complete response remains available rather than turning an unknown into a browser conclusion.')))
    attempt_body+=section(h('目前能确认的原因范围','Supported cause'),'<div class="guide-grid"><article class="guide-card"><h3>'+h('直接观察','Observed')+'</h3><p>'+esc(observed)+'</p></article><article class="guide-card"><h3>'+h('证据支持的判断','Supported interpretation')+'</h3><p>'+esc(interpretation)+'</p></article></div><p class="source-note">'+h('这是基于本次输入、程序状态、错误文字和同页其他方法结果的证据边界；没有证据的根因不会补写。','This boundary is based on the input, worker state, error text and same-page method results; unsupported root causes are not invented.')+'</p>')
   attempt_body+=section(h('逐项评分与原因','Component scores and reasons'),'<p class="source-note">'+h('“未计算”可能是该页不存在对应内容，也可能是评分错误；逐行的官方错误和长度字段用于区分。逐页 overall 只平均成功计算的分项，不是首页榜单 overall。','“Not calculated” can mean absent content or a scorer error; official errors and length fields below distinguish them. Per-page overall averages only calculated components and differs from leaderboard overall.')+'</p>'+table([h('指标','Metric'),h('状态','Status'),h('分数','Score'),h('长度、距离与官方错误','Lengths, distance and official error')],metric_rows,'wmb-metrics-'+attempt_id,True,metrics=[]))
   attempt_body+=section(h('人工真值与完整实际输出','Human reference and complete actual output'),'<p class="source-note">'+h('左侧是官方人工校准 Markdown，右侧是该次调用最终采用的完整返回；可分别滚动，原始文件可从上方下载。冻结 HTML 输入另行提供，原 URL 不参与本轮实时抓取。','The official human-calibrated Markdown is on the left; the complete final authoritative return is on the right. Each pane scrolls independently, and original files are downloadable above. Frozen input HTML is separate; the original URL was not fetched live.')+'</p><div class="evidence-compare wmb-evidence"><div class="evidence-pane"><header><h3>'+h('官方人工真值','Official human reference')+'</h3></header><div class="pane-body"><pre data-kind="reference">'+esc(reference_text)+'</pre></div></div><div class="evidence-pane"><header><h3>'+h('最终实际输出','Final actual output')+'</h3></header><div class="pane-body"><pre data-kind="output">'+esc(readable_output(output_text))+'</pre></div></div></div>')
   attempt_body+=section(h('完整接口记录','Complete API/worker record'),'<p>'+h('输入方式','Input mode')+': <code>'+esc(metadata.get('input_mode') or '—')+'</code> · '+h('上游状态','Upstream status')+': <code>'+esc(metadata.get('upstream_status') if metadata.get('upstream_status') is not None else '—')+'</code></p><details><summary>'+h('供应商返回字段','Provider response fields')+'</summary><pre data-kind="metadata">'+esc(json.dumps(metadata,ensure_ascii=False,indent=2))+'</pre></details>')
   _wmb_write(out,'attempts/'+attempt_id+'.html',labels[r['provider']]+' · '+case_id,attempt_body,english)
