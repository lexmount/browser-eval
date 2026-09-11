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
from .metrics import percentile, task_passed, task_measured
from .methods import method_info
from .accounts import ACCOUNT_REGISTRY
from .report_interfaces import HEADLESS_ACCESS
from .report_style import STYLE
from .report_components import tabs, metric_guide, metric_legend, examples, reference_status, basis_label, reference_label, sources_panel, stable_contract_details, task_instruction
from .failure_diagnosis import diagnose
from .safety import contained_path, safe_href, validate_report_ids
from .models import execution_id

TRACKS={'web':'静态网站正文','dynamic':'动态网站正文','challenge':'热门网站正文','document':'文档正文','image':'图片获取','video':'视频获取'}
PROVIDER_LABELS={'moli-1.0.6':'Moli 1.0.6','moli':'Moli 1.1.1','moli-1.1.3':'Moli 1.1.3'}
ROUTE_LABELS={
 'cloudflare_native_markdown':'Cloudflare Browser Run /markdown 直接返回 Markdown',
 'lightpanda_native_markdown':'Lightpanda CLI 直接返回 Markdown',
 'obscura_native_markdown':'Obscura CLI 直接返回 Markdown',
 'moli_native_markdown':'Moli CLI 直接返回 Markdown',
 'node-html-markdown':'Chromium 渲染 DOM → node-html-markdown 2.0.0',
}
def provider_label(name):return PROVIDER_LABELS.get(name,name)
JS=r'''
function theme(){let t=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=t;localStorage.setItem('webfetch-theme',t)}
function tr(zh,en){return document.documentElement?.lang==='en'?en:zh}
function filterRows(){let count=0;document.querySelectorAll('#rows tr').forEach(r=>{let show=[...document.querySelectorAll('[data-filter]')].every(s=>!s.value||(s.dataset.filter==='query'?(r.textContent+' '+(r.dataset.search||'')).toLowerCase().includes(s.value.trim().toLowerCase()):r.dataset[s.dataset.filter]===s.value));r.hidden=!show;if(show)count++});let c=document.querySelector('#count');if(c)c.textContent=tr(`显示 ${count} 条`,`Showing ${count}`)}
function paintSort(t){let col=Number(t.dataset.col);[...t.tHead.rows[0].cells].forEach((h,i)=>{h.classList.toggle('sorted',i===col);h.removeAttribute('aria-sort')});t.tHead.rows[0].cells[col].setAttribute('aria-sort',t.dataset.dir==='asc'?'ascending':'descending');[...t.tBodies[0].rows].forEach(r=>[...r.cells].forEach((c,i)=>c.classList.toggle('sorted',i===col)))}
function sortTable(id,col){let t=document.getElementById(id),dir=t.dataset.col===String(col)&&t.dataset.dir==='desc'?'asc':'desc';let rows=[...t.tBodies[0].rows];rows.sort((a,b)=>{let av=a.cells[col].dataset.sort,bv=b.cells[col].dataset.sort;if(av==='-1'||bv==='-1')return av===bv?0:av==='-1'?1:-1;let result=(av==null||bv==null)?a.cells[col].textContent.localeCompare(b.cells[col].textContent,document.documentElement.lang):Number(av)-Number(bv);return dir==='asc'?result:-result});rows.forEach((r,i)=>{r.cells[0].textContent=i+1;t.tBodies[0].append(r)});t.dataset.col=String(col);t.dataset.dir=dir;paintSort(t)}
function chooseTab(group,key){let box=document.getElementById(group);if(!box)return;box.querySelectorAll(':scope > .tab-bar > button').forEach(b=>{let on=b.dataset.tab===key;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1});box.querySelectorAll(':scope > [role="tabpanel"]').forEach(p=>p.hidden=p.dataset.panel!==key)}
function tabKeys(event,group){if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;let bs=[...document.getElementById(group).querySelectorAll(':scope > .tab-bar > button')],i=bs.indexOf(event.target);i=event.key==='Home'?0:event.key==='End'?bs.length-1:(i+(event.key==='ArrowRight'?1:-1)+bs.length)%bs.length;event.preventDefault();chooseTab(group,bs[i].dataset.tab);bs[i].focus()}
function cascadeFilters(changed){let rows=[...document.querySelectorAll('#rows tr')],selects=[...document.querySelectorAll('select[data-filter]')],prior=[];selects.forEach(s=>{let available=new Set(rows.filter(r=>prior.every(p=>!p.value||r.dataset[p.dataset.filter]===p.value)).map(r=>r.dataset[s.dataset.filter]));if(s.dataset.filter!=='result'){let failureFilter=['failureStage','failureCategory'].includes(s.dataset.filter);[...s.options].forEach(o=>o.disabled=!!o.value&&!available.has(o.value));if(s.value&&!available.has(s.value))s.value=failureFilter?'':([...s.options].find(o=>o.value&&!o.disabled)?.value||'');if(failureFilter)s.disabled=![...s.options].some(o=>o.value&&!o.disabled);}prior.push(s)});filterRows()}
function outcomes(row,key){return new Set((row.dataset[key]||'').split(' ').filter(Boolean))}
function compareMethods(){let a=document.getElementById('compare-a'),relation=document.getElementById('compare-relation'),track=document.getElementById('compare-track'),count=document.getElementById('compare-count');if(!a||!relation||!track||!count)return;let boxes=[...document.querySelectorAll('[data-compare-method]')];boxes.forEach(box=>{box.disabled=box.value===a.value;box.closest('label')?.classList.toggle('disabled',box.disabled);if(box.disabled)box.checked=false});let others=boxes.filter(box=>box.checked&&!box.disabled).map(box=>box.value),selected=[a.value,...others],shown=0;document.querySelectorAll('#compare-rows tr').forEach(row=>{let pass=outcomes(row,'pass'),fail=outcomes(row,'fail'),match=others.length>0&&(!track.value||row.dataset.track===track.value);if(match)match={mixed:selected.some(x=>pass.has(x))&&selected.some(x=>fail.has(x)),a_pass_others_fail:pass.has(a.value)&&others.every(x=>fail.has(x)),a_fail_others_pass:fail.has(a.value)&&others.every(x=>pass.has(x)),all_pass:selected.every(x=>pass.has(x)),all_fail:selected.every(x=>fail.has(x)),exact_pass:selected.every(x=>pass.has(x))&&pass.size===selected.length}[relation.value];row.hidden=!match;if(match)shown++});count.textContent=others.length?tr(`找到 ${shown} 个任务`,`${shown} tasks found`):tr('请至少选择一个对比方法','Select at least one comparison method')}
function applyReportAnchor(anchor){if(!anchor)return;if(anchor.startsWith('track-')){chooseTab('dataset-hub','cases');let s=document.querySelector('select[data-filter="track"]');if(s){s.value=anchor.slice(6);document.querySelectorAll('select[data-filter]').forEach(x=>{if(x!==s)x.value=''});cascadeFilters(s)}}else if(['references','sources'].includes(anchor)){chooseTab('dataset-hub',anchor)}else{let target=document.getElementById(anchor);for(let p=target;p;p=p.parentElement){if(p.dataset?.panel)chooseTab(p.parentElement.id,p.dataset.panel);if(p.tagName==='DETAILS')p.open=true}}}
window.addEventListener('DOMContentLoaded',()=>{document.documentElement.dataset.theme=localStorage.getItem('webfetch-theme')||'light';document.querySelectorAll('[data-filter]').forEach(e=>e.addEventListener(e.tagName==='INPUT'?'input':'change',e.tagName==='INPUT'?filterRows:()=>cascadeFilters(e)));document.querySelectorAll('[data-compare-method],[data-compare-control]').forEach(e=>e.addEventListener('change',compareMethods));cascadeFilters();compareMethods();document.querySelectorAll('table[data-col]').forEach(paintSort);applyReportAnchor(location.hash.slice(1))});
window.addEventListener('hashchange',()=>applyReportAnchor(location.hash.slice(1)));
'''
EXTRA='''
.table-wrap{margin-top:16px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{vertical-align:top;padding:12px 14px;border-bottom:1px solid var(--line);text-align:left}th{background:var(--panel2);font-size:12px}td:first-child,th:first-child{width:48px;white-space:nowrap}.ranking{min-width:1240px}.ranking td{white-space:nowrap}.ranking td:nth-child(2){white-space:normal;min-width:165px}.sort-button{border:0;background:none;color:inherit;font:inherit;font-weight:700;cursor:pointer;padding:0;text-align:left}.grid .card{grid-column:span 4}.card p{margin-bottom:0}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.filters select,.filters input{min-width:150px;padding:10px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:20px}.compare>div{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:620px;overflow:auto}.break{overflow-wrap:anywhere}.status{white-space:nowrap}.source-note{color:var(--muted);font-size:13px}details{padding:10px 0}.accounts-table{min-width:1300px}.accounts-table code{white-space:nowrap}.hero h1{font-size:clamp(30px,4vw,48px)}section[id]{scroll-margin-top:75px}.actions{display:flex;gap:16px;flex-wrap:wrap}nav{gap:16px}.tiny{font-size:12px} @media(max-width:700px){.grid .card{grid-column:span 12}.compare{grid-template-columns:1fr}.wrap{width:calc(100% - 28px)}.filters>*{width:100%}}
'''

EXTRA+='''
[hidden]{display:none!important}.ranking{min-width:980px}.ranking th,table th{white-space:nowrap}.ranking td:nth-child(2){min-width:130px;max-width:180px}.sorted,.sorted a{color:#117541!important;font-weight:750!important}[data-theme="dark"] .sorted,[data-theme="dark"] .sorted a{color:#74e1a8!important}.tab-bar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 22px}.tab-bar button{padding:10px 18px;border:1px solid var(--line);border-radius:999px;color:var(--ink);background:var(--panel);font:inherit;cursor:pointer}.tab-bar button[aria-selected="true"]{background:#075ccf;color:white;border-color:#075ccf}.tab-bar button:focus-visible,.sort-button:focus-visible{outline:3px solid #428bff;outline-offset:3px}.guide-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.guide-card{padding:22px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}.guide-card h3{margin:0 0 10px;font-size:19px}.guide-card p{margin:0;line-height:1.7}.worked-example{display:flex;gap:16px;flex-wrap:wrap;padding:20px;background:var(--panel2);border-radius:14px;margin-top:20px}.tabs [role="tabpanel"]{min-width:0}#filtered-table{min-width:1080px}#filtered-table td:last-child{min-width:170px;max-width:350px;overflow-wrap:anywhere}#filtered-table td:nth-child(2){min-width:155px}.section{padding:40px 0}.hero{padding:38px 0}.top nav{flex-wrap:wrap}.source-note{line-height:1.7}@media(max-width:700px){.guide-grid{grid-template-columns:1fr}.tab-bar button{padding:8px 12px}.section{padding:28px 0}}
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

def esc(s):return html.escape(str(s if s is not None else ''))
def readable_output(s):
 return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',lambda m:'\\x'+format(ord(m[0]),'02x'),s)
def pct(n):return '—' if n is None else f'{n*100:.1f}%'
def ms(n):return '—' if n is None else f'{n:,.0f} ms'
def link(url,label):return f'<a href="{esc(safe_href(url))}">{esc(label)}</a>'
def cell(value,sort=None):return f'<td'+(f' data-sort="{sort}"' if sort is not None else '')+f'>{value}</td>'
def rate(a,b):return pct(a/b) if b else '—'
def section(title,body,id=''):return f'<section class="section"'+(f' id="{id}"' if id else '')+f'><div class="wrap"><h2>{title}</h2>{body}</div></section>'
def hero(title,desc):return f'<section class="hero"><div class="wrap"><div class="eyebrow">Web Fetch · 实测报告</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'
def table(headers,rows,id='',sortable=False,cls='',metrics=None,index_header='序号'):
 head=f'<th>{esc(index_header)}</th>'+''.join(f'<th><button class="sort-button" onclick="sortTable(\'{id}\',{i+1})">{h} ↕</button></th>' if sortable else f'<th>{h}</th>' for i,h in enumerate(headers))
 body=''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))
 return metric_legend(headers if metrics is None else metrics)+f'<div class="table-wrap"><table class="{cls}"'+(f' id="{id}"' if id else '')+f'><thead><tr>{head}</tr></thead><tbody'+(' id="rows"' if id=='filtered-table' else '')+f'>{body}</tbody></table></div>'
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
 reviews=[json.loads(path.read_text())]
 speed_review=out/'speed-cohort/result-review.json'
 if speed_review.is_file():reviews.append(json.loads(speed_review.read_text()))
 checked=[row.get('reviewed_attempts',0) for row in reviews]
 total=[row.get('expected_attempts',0) for row in reviews]
 unresolved=sum(row.get('unresolved',0) for row in reviews)
 scopes=f'主结果 {checked[0]:,}/{total[0]:,}'
 if len(reviews)>1:scopes+=f'；同窗测速 {checked[1]:,}/{total[1]:,}'
 return (f'<p class="callout success"><b>独立核验：{scopes}；未决 {unresolved}</b>。'
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
  counts=f"检出 {m.get('stable_record_count','—')} 条，字段完整 {m.get('stable_complete_record_count','—')} 条，完整且唯一 {m.get('stable_unique_record_count','—')} 条，要求至少 {m.get('stable_minimum_records','—')} 条。"
  success='稳定内容与结构检查通过。' if m.get('stable_anchor_results') or m.get('stable_page_pattern_results') else '条目结构检查通过；本例未设置稳定文字核对点。'
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
  return '已找到目标资源。' if not missing else '未找到目标资源：'+', '.join(missing)
 m=r['metrics'];parts=[]
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
  for track in TRACKS:
   subset=[r for r in rs if cases[r['case_id']].get('track','web')==track and measured(r,cases[r['case_id']])]
   values+=cell(f'{sum(succeeded(r,cases[r["case_id"]]) for r in subset)}/{len(subset)}',sum(succeeded(r,cases[r['case_id']]) for r in subset)/len(subset) if subset else -1)
  if audit:values+=review_cell(audit,rs)
  rows.append((passed,name,('',values)))
 rows=[row for _,_,row in sorted(rows,key=lambda x:(-x[0],x[1]))]
 heads=['方法','请求成功率','综合任务通过率']+[TRACKS[t] for t in TRACKS]
 if audit:heads+=['审查结果']
 intro=('<p class="decision-lead"><b>总榜口径：</b>每种方法都执行同一组 150 个任务，每题计一次通过或失败，直接相加；不把耗时、全文质量或任务类别难度混成加权分。总榜适合看整体交付覆盖，具体能力仍应回到六个分榜。</p>')
 help=('<details class="ranking-metric-help"><summary>总榜指标说明</summary><div class="metric-help"><dl>'
       '<div><dt>综合任务通过率</dt><dd>150 个任务中满足各自验收规则的数量；每题等权。</dd></div>'
       '<div><dt>六类通过数</dt><dd>各分榜通过数 / 本类任务数，用于定位总分来自哪些能力。</dd></div>'
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
 audit=json.loads((out/'result-review.json').read_text()) if (out/'result-review.json').is_file() else None
 dynamic=any(c.get('task_kind')=='structure' for c in cases.values())
 bycase=defaultdict(list);byprovider=defaultdict(list)
 for r in records:bycase[r['case_id']].append(r);byprovider[r['provider']].append(r)
 cards=''
 for track,label in TRACKS.items():
  cards+=overview_card(records,cases,track,label,names)
 home='<div class="home-page">'+hero('Web Fetch 评测报告',f'{len(cases)} 个案例 · {len(names)} 种方法 · 六类分榜与一个综合通过率总榜。')
 from .report_explainer import article_title,render_explainer
 TITLE=article_title(records)
 home+='<aside class="wrap home-reading" aria-label="报告解读"><div><strong>报告解读</strong><p>'+link('guide/index.html',TITLE)+'</p></div><p>从分数差异、失败案例和实现机制，理解如何选择抓取方法。</p>'+link('guide/index.html','阅读全文 →')+'</aside>'
 if (out/'browser-use/browser-use-evaluation-latest.html').is_file():
  home+='<aside class="wrap home-reading" aria-label="浏览器操作轨迹评测"><div><strong>浏览器操作轨迹评测</strong><p>'+link('browser-use/browser-use-evaluation-latest.html','Chromium 与 Moli：公开参考轨迹实测')+'</p></div><p>仓库原有 30 例是确定性工具微基准；本页另列 30 条经资格验证的 WebArena 官方人工轨迹双浏览器正式评测，两者不合并统计。</p>'+link('browser-use/browser-use-evaluation-latest.html','查看报告 →')+'</aside>'
 write(out,'guide/index.html','报告解读',render_explainer(out,cases,records,table=table,cell=cell,link=link))
 panels=[]
 for track,label in TRACKS.items():
  if not any(c.get('track','web')==track for c in cases.values()):continue
  selected=[c for c in cases.values() if c.get('track','web')==track]
  coverage=f'{len(selected)} 个案例 · {len({r["provider"] for r in records if cases[r["case_id"]].get("track","web")==track})} 种已测方法'
  panels.append((track,label,f'<div class="track-heading"><h3>{label}</h3><span>{coverage}</span></div>'+ranking(records,cases,track,compact=True,audit=audit)+f'<p>{link("datasets/index.html#track-"+track,"案例与参考 →")}</p>'))
 home+=section('六类任务概览',review_summary(out)+'<p class="callout success"><b>调用完整：</b>'+str(reliability['expected_calls'])+'/'+str(reliability['expected_calls'])+' 次均有结果。</p><p class="source-note">只在同一任务类别内比较。</p><div class="grid home-overview-grid">'+cards+'</div>','overview')
 home+=section('综合通过率总榜',overall_ranking(records,cases,audit),'overall-ranking')
 home+=section('六类分榜','<p class="ranking-orientation">分榜用于判断具体能力。默认按任务通过率排序，点击列名可切换；质量分数与通过率分别计算。</p>'+tabs('home-rankings',panels),'results')
 write(out,'index.html','评测报告',home+'</div>')
 controls=filter_controls([('track','类型',list(TRACKS.items())),('category','类别',[(x,x) for x in dict.fromkeys(c['category'] for c in cases.values())])])
 rows=[]
 for c in cases.values():
  verified=c.get('source_verification',{});state='受限' if verified.get('state')=='restricted' else '有效'
  rows.append((dataset_attrs(c),cell(link(c['id']+'.html',c['name']))+cell(esc(c['category']))+cell(link(c['url'],'源页 ↗'))+cell(link(c['id']+'.html#facts',basis_label(c)))+cell(reference_label(c))+cell(state)+cell(case_rate(bycase[c['id']],c))))
 anchors=''.join(f'<span id="track-{t}"></span>' for t in TRACKS)
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
  controls=filter_controls([('track','类型',list(TRACKS.items())),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(rs,cases))
  body=hero(esc(provider_label(name)),esc(info['contract']))+section('本轮配置',f'<p><code>{esc(info["endpoint"])}</code></p><p>{esc(info["freshness"])}</p><p>静态网站正文使用默认正文输出；资源任务为支持资源返回的 Exa、Tavily、Firecrawl 开启原生资源字段。未开启额外的理解模型或付费后备方法。</p>')
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
            '<a href="#decision">查看逐项判定</a></p>')
  if r.get('diagnosis') and not passed:
   d=r['diagnosis'];s=d.get('independent_source_check') or {}
   quote_issue=_quote_field_issue(m)
   facts=[('失败分类','字段边界丢失' if quote_issue else d['category']),
          ('发生阶段','字段解析' if quote_issue else d['stage']),
          ('判定依据',reason(r,c)),('归因边界',d['certainty'])]
   if s:facts.extend([('独立源页复查',f"{s.get('checked_at','')} · HTTP {s.get('status','未取得')} · {s.get('title') or s.get('error','')}"),('复查边界',d['source_check_boundary'])])
   summary+='<details class="technical-details"><summary>查看失败分类与归因边界</summary>'+table(['项目','依据'],[('',cell(esc(k))+cell(esc(v))) for k,v in facts],metrics=[])+"</details>"
  body=hero(esc(c['name']),hero_meta)+section('结论',summary,'overview')
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
  actual_body=corruption+f'<pre data-kind="output">{esc(readable_output(output))}</pre>'
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
                 '同一个实际片段只能计入一个位置，避免重复文字被多次借用。全文完整度、纯净度和 F1 仍按完整文本另行计算。</div>')
   body+=section('正文是否抓全',region_intro+table(['标准答案位置','标准片段数','命中片段数','覆盖率','判定'],region_rows,metrics=[])+
                 '<details class="technical-details"><summary>查看各位置的完整标准正文和人工定位样例</summary>'+''.join(region_cards)+'</details>','body-coverage')
  if title_details:body+=section('标题文字质量',title_details)
  runtime=('<details class="technical-details"><summary>查看完整运行、哈希与评分字段</summary>'
           +metric_legend(detail_metrics)+table(['项目','值'],rows,metrics=[])+
           f'<details><summary>接口返回的附加字段</summary><pre data-kind="metadata">{esc(json.dumps(r.get("provider_metadata",{}),ensure_ascii=False,indent=2))}</pre></details></details>'
           f'<p class="actions">{link(c["url"],"打开源页 ↗")}{link("../datasets/"+c["id"]+".html","查看该任务的全部方法")}</p>')
  body+=section('运行与评分信息',runtime,'run-info')
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
 controls=filter_controls([('provider','方法',[(n,provider_label(n)) for n in names]),('track','类型',list(TRACKS.items())),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(records,cases))
 comparison=section('方法组合对比','<p class="source-note">按同一任务逐项比较，不用各方法不同的成功样本拼接结论。成功与失败均沿用主榜已核验判定；点击方法可查看标准答案、实际返回和原因。</p>'+method_comparisons(cases,bycase,names),'method-comparison')
 write(out,'analysis/index.html','结果分析',hero('结果分析','先比较方法在同一任务上的成败组合，再筛选全部执行记录。')+comparison+section('全部执行记录',controls+table(['方法']+headers,[(case_attrs(r,cases[r['case_id']]),cell(link('../providers/'+r['provider']+'.html',provider_label(r['provider'])))+attempt_row(r,cases[r['case_id']])[1]) for r in records],'filtered-table')))
 write_support(out,records,manifests)
 from .report_changes import render_changes
 render_changes(out,cases,records)
 from .report_i18n import write_english_report
 write_english_report(out)
 return out/'index.html'


def write_review_page(out,records):
 path=out/'result-review.json'
 if not path.is_file():
  write(out,'review/index.html','核验记录',hero('核验记录','本轮没有绑定独立核验记录。'))
  return
 audit=json.loads(path.read_text());reviewer=audit.get('reviewer',{})
 speed_audit={}
 speed_path=out/'speed-cohort/result-review.json'
 if speed_path.is_file():speed_audit=json.loads(speed_path.read_text())
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
 reviewed_attempts=audit.get('attempts',[])+speed_audit.get('attempts',[])
 disagreements=[row for row in reviewed_attempts
                if row.get('agent_verdict') != row.get('machine_verdict')]
 adjudicated=[row for row in disagreements if row.get('final_verdict') in {'pass','fail'}]
 unresolved=audit.get('unresolved',0)+speed_audit.get('unresolved',0)
 conclusion=(f'<p class="callout success">主结果 {audit.get("reviewed_attempts",0):,}/{audit.get("expected_attempts",0):,}；'
             f'同窗测速 {speed_audit.get("reviewed_attempts",0):,}/{speed_audit.get("expected_attempts",0):,}；'
             f'代理分歧 {len(disagreements)}；已裁定 {len(adjudicated)}；未决 {unresolved}。</p>')
 page+=section('当前状态',conclusion)
 page+=section('核验轮次','<div class="review-rounds">'+rounds_html+'</div>')
 method=(f'<details class="technical-details"><summary>核验协议与执行者</summary><p>执行者：{esc(reviewer.get("agent","独立代理"))}；模型：{esc(reviewer.get("model","—"))}；推理强度：{esc(reviewer.get("effort","—"))}。'
         '核验时隐藏机器结论和分数，只提供任务契约、标准答案与完整实际输出；分歧回到原始证据裁定，未决非零则禁止发布。任务和输出未变化时沿用原核验；发生变化的调用必须重新盲审。</p></details>')
 page+=section('核验口径',method)
 write(out,'review/index.html','核验记录',page)

def write_support(out,records,manifests):
 headless=bool(records) and all(r.get('provider_type') in {'headless_html','headless_markdown'} for r in records)
 from .report_components import task_contract_guide
 methodology=hero('评测方法','按任务定义验收规则。同组已测方法使用相同的冻结任务与参考，覆盖数量在各榜单列明。')
 if headless:
  methodology+=section('从参考到发布','<div class="guide-grid"><article class="guide-card"><h3>1 · 冻结参考</h3><p>独立采集源页；需要渲染的页面另做浏览器观测，通过完整范围校验后才冻结。</p></article><article class="guide-card"><h3>2 · 执行调用</h3><p>同一 URL、任务和截止时间；各方法使用其最快的正式任务原生产物路线，保留版本、输出与哈希。</p></article><article class="guide-card"><h3>3 · 测后核验</h3><p>独立重取精确参考页，确认调用期间标准答案未改变；不把参考变化算成浏览器失败。</p></article><article class="guide-card"><h3>4 · 结果独立审查</h3><p>强代理逐次读取完整输出、标准答案与任务规则；分歧必须裁定，未裁定项会阻止构建。</p></article></div>'+review_summary(out,'../'),'评测流程')
 examples_html=re.sub(r'<a href="../datasets/([^"#]+).html">([^<]*)</a>',lambda m:m[0] if (out/'datasets'/(m[1]+'.html')).exists() else m[2],examples())
 methodology+=section('每类怎么测',examples_html)+section('指标说明',metric_guide())
 methodology+=section('共同输入、计时边界与调用预算','<p>所有方法进入同一个评分入口，但按任务交付物验收，而不是强制使用同一种中间格式。网页与文档正文最终验收可读文本；原始文件任务验收 PDF、DOCX、图片或视频字节；结构任务验收字段和稳定文字。HTML/Markdown 链接与明确配对的结构化名称—链接字段按同一规则核验；不把请求回显或诊断日志当成答案。原始输出与原始适配器状态保留，报告层不按方法名称改写任务结果。</p><div class="guide-grid"><article class="guide-card"><h3>计入耗时</h3><p>从准备发起该次 provider 调用开始，到形成可送入统一评分器的最终答案为止：线程调度、SDK/CLI/HTTP 调用、远端处理与网络下载、浏览器渲染与正文提取、本地解析、统一输入转换，以及文件字节读取、格式识别和 SHA-256 计算均在计时内。</p></article><article class="guide-card"><h3>不计入耗时</h3><p>数据集与参考加载、provider 配置校验、调用间限速等待、最终答案形成后的证据落盘、统一评分计算、报告汇总与页面生成不计入。供应商上报的服务端耗时只作附加证据，排名使用客户端观测的端到端耗时。</p></article></div><p>这个边界比较的是“拿到本任务最终交付物”所需时间：服务直接返回成品时计完整往返；本地方法若还需下载、渲染或解析，也必须完成后才停表。正文任务中，方法只返回原始 PDF 或 DOCX 时，本地正文解析也计时；原始文件任务则到字节校验完成即停止，不做无关的 Markdown 转换。所有方法使用同一外层计时器和截止预算。超时答案不接收；无法取消的调用会被隔离，整批结果因此不完整并触发不可靠状态，构建产物不得发布。</p><p>完整正文任务除原有核对点外，按参考正文最多五个连续位置检查字符六元组覆盖：每个位置至少达到 60%，通常要求至少四处达到 80%；若页面存在经独立证据确认的合法重排，案例可显式登记不同位置门槛。比较忽略空白和排版符号但保留文字顺序；全文 F1 独立保留实际值。文件任务必须有字节验收，资源 URL 不等于文件。</p>')
 if (out/'scoring-revisions.json').exists():
  methodology+=section('评分来源与修订','<p>当前成绩包含对保存输出的离线重算；文件任务另在 2026-09-04 真实补测，选取 36 次新调用替换原缺测项，详情见'+link('../changes/index.html','变更记录')+'。Moli 时延纠正只替换固定四题、三个版本的 12 个耗时值，并统一取三轮配对实验的最终轮；旧耗时和全部 36 次物理调用保留在'+link('../latency-corrections.json','时延纠正收据')+'中。其余历史调用的输出与计时保留，不能把跨批次视图当成同时重跑。'+link('../scoring-revisions.json','评分规则修订收据')+'和'+link('../reference-revisions.json','参考答案修订收据')+'记录输入、输出哈希、判定变化及供应商调用数，不把新规则伪装成原调用时的运行配置。</p>')
 methodology+=section('稳定任务契约与全文诊断',task_contract_guide(),'task-contracts')
 figures=''.join(f'<figure><img src="../assets/methodology/{name}.png" alt="{label}" style="max-width:100%;height:auto" loading="lazy"><figcaption>{label}</figcaption></figure>' for name,label in [('annotation','参考标注'),('validation','任务验收'),('scoring','评分口径')] if (out/'assets/methodology'/f'{name}.png').is_file())
 if figures:methodology+=section('方法图解',figures)
 methodology+=section('参考与匹配','<p>原始快照保留不变；评分参考清除零宽空格、BOM 和软连字符，并检查空正文、乱码、重复核对点及核对点是否存在于参考中。通过检查不等于语义完整性已获证明，正文范围仍以来源记录为准。</p><p>关键事实按规范化后的可见文本机械匹配：统一 Unicode、大小写及排版空白，菜单路径如 Settings &gt; AI 与 Settings&gt;AI 等价；忽略 Markdown 链接地址、行内代码与加粗标记。一个已被完整记录边界约束的字段允许丢失内联元素间空白，例如标签列表 <code>change deep-thoughts</code> 与 <code>changedeep-thoughts</code> 内容等价；相邻独立链接仍按独立词段核对，不猜测跨记录的粘连分词。保留数字、正负号、否定词、标点和英文词边界。不使用大模型或模糊相似度判定通过。</p>')
 methodology+=section('调用时间','<p>各方法使用同一套冻结任务与参考。不同批次的耗时不直接排名；同窗速度子集单独标识。实时网页内容、登录状态和站点策略可能变化，成绩只代表所记录的调用。环境、版本与命令见“环境与复现”。</p>')
 if headless:
  methodology+=section('变更后重跑范围','<p>不把“代码有更新”直接等同于全部重跑。只改展示时重建报告；只改裁判且原始输出足够时离线重算；某个参考变化时重跑该题的全部方法；新增或升级单个实现时重跑该方法的全部题；计时边界变化时废弃并重跑同窗测速。只有全局任务输入、调用边界或所有方法共同依赖发生变化，才需要全量重跑。</p>')
 methodology+=section('计算与边界','''<details><summary>全文评分公式</summary><p>完整度＝取回的参考词数 ÷ 参考词数；纯净度＝匹配的正文词数 ÷ 返回词数；F1＝2 × 完整度 × 纯净度 ÷ 两者之和。英文按词、中文按字，统一大小写与 Unicode 字符形式；Markdown 链接只计文字。对有完整参考的全部案例取平均；请求失败计 0，请求成功但任务未通过仍按实际正文评分，没有参考不计算。</p></details><details><summary>参考范围与公平性</summary><p>网页、文档使用独立保存的参考正文；仓库以 README 为准，列表只覆盖指定的已加载范围，不要求无限滚动或跨页翻页。登录页和验证码不能代替目标正文。参考正文自身包含的“404 Not Found”等术语不作为错误页判据。</p><p>不同方法并行，同一方法串行；正文结束后再测其他任务。Firecrawl 每次完成后冷却 6.1 秒。本报告不是 WCXB 官方榜单。</p><p>媒体网址只证明找到入口，不代表取得文件。文件任务没有另接下载器代替参评方法完成。</p></details>''')
 if headless:
  methodology=methodology.replace('Firecrawl 每次完成后冷却 6.1 秒。','七种方法每次使用新进程或新上下文。静态网页正文：Kitesurf 走 Cloudflare 账户级 /markdown，Lightpanda、Obscura 与 Moli 1.0.6、1.1.1、1.1.3 使用各自 CLI 正文能力，Chromium 取完整渲染 DOM 后由 node-html-markdown 2.0.0 转换。三个 Moli 版本遇到直接 PDF、DOCX、图片或视频 URL 时均使用不带 --dump 和页面等待参数的自动响应；正文任务在预算内解析可读文字，文件任务直接校验字节。原始主矩阵未启用失败重试，每次调用有 45 秒端到端预算。运行器可在测量前声明至多一次失败重试：保留全部物理证据和总耗时，并固定采用最后一次结果，不能择优。已确认的命令、版本、裁判、参考或测量错误可按完整声明范围重测并替换权威逻辑结果；旧证据不得删除。本报告另对四题、三个 Moli 版本做三轮配对时延纠正，统一采用最终轮的 12 个耗时，不挑最快；内容、评分和判定未改变。方法之间并行、同一方法串行。')
 write(out,'methodology/index.html','评测方法',methodology)
 downloads=' · '.join(link('../'+path,label) for path,label in [('dataset.json','评测集与参考'),('results.jsonl','全部调用记录'),('summary.json','分类汇总'),('execution.json','执行批次')]+([('implementation-provenance.json','版本回溯证据')] if (out/'implementation-provenance.json').is_file() else [])+([('reference-revisions.json','参考修订收据')] if (out/'reference-revisions.json').is_file() else [])+([('scoring-revisions.json','评分修订收据')] if (out/'scoring-revisions.json').is_file() else [])+([('latency-corrections.json','时延纠正收据')] if (out/'latency-corrections.json').is_file() else []))
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
  versions=sorted({str(r.get('provider_metadata',{}).get('browser_version')) for r in subset if r.get('provider_metadata',{}).get('browser_version')})
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
  support+=section('同窗速度证据','<p>'+link('../speed-cohort/speed-comparison.json','速度汇总与共同成功样本')+' · '+link('../speed-cohort/results.jsonl','速度子集逐次记录')+'</p>')
 support+=section('公共条件',f'<p>最终报告纳入 {len(records):,} 次调用。任务与参考冻结后执行；每个方法内部串行，不复用个人登录状态。</p>'+table(['参数','值'],common,metrics=[]))
 support+=section('方法版本','<p>“事后核验”只说明留存二进制与官方归档完全一致，不改写成当时已经记录了版本。移动 nightly 无法追到不可得的源修订时，只用 SHA-256 标识精确可执行文件。</p>'+table(['方法','版本','身份依据','部署路径','二进制 SHA-256','调用数'],provider_rows,metrics=[]))
 if headless:
  support+=section('发布与加载','<p>本地构建为每个任务、方法和证据生成可独立检查的逻辑路由；这不等于线上部署同样数量的独立 HTML。发布器会把整份报告编译成一个通用 HTML 外壳、共享样式与脚本，以及按内容寻址的压缩数据块。页面切换由前端路由完成，证据只在打开对应页面时读取，超过 256 KiB 的完整文本还需用户主动加载。</p><p class="source-note">发布清单记录逻辑路由数、源文件数、压缩块数和逐文件 SHA-256；发布门禁会重建每个文件并验证内部链接与原始下载字节。这样保留逐项可审计性，同时避免重复页面骨架和一次性加载全部证据。</p>')
 support+=section('核心复现指令',reproduce+f'<p>运行配置：<code>configs/providers.headless.json</code>；账号变量名称见本报告“账号”页，实际密钥只放仓库根目录忽略提交的 <code>.env</code>。冻结参考、完整重测与核验门禁见 {link(repo+"/blob/main/harness/web_fetch/HEADLESS.md","仓库复现说明")}。</p>')
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
  accounts=ACCOUNT_REGISTRY
  accountrows=[('',cell(esc(a['provider']))+cell(link(a['registration_url'],'注册 / 登录')+'<br>'+link(a['key_url'],'获取凭据'))+cell('<br>'.join('<code>'+esc(x)+'</code>' for x in a['env_vars']))+cell(esc(a['steps']))+cell(esc(a['free_note']))) for a in accounts]
  write(out,'accounts/index.html','账号',hero('账号与凭据','只列官方入口和环境变量名称，不保存实际凭据。')+section('注册清单',table(['提供方','官方入口','环境变量','取得方式','免费 / 准入说明'],accountrows,cls='accounts-table')))
 from .report_methods import render_methods
 write(out,'adoption/index.html','方法介绍',render_methods(out,records,hero=hero,section=section,table=table,cell=cell,link=link,esc=esc))
