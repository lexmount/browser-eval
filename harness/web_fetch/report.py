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
from .report_style import STYLE
from .report_components import tabs, metric_guide, metric_legend, examples, reference_status, basis_label, reference_label, sources_panel, stable_contract_details, task_instruction
from .failure_diagnosis import diagnose
from .safety import contained_path, safe_href, validate_report_ids
from .models import execution_id

TRACKS={'web':'网站正文','dynamic':'动态网站正文','challenge':'热门网站正文','document':'文档正文','image':'图片获取','video':'视频获取'}
JS=r'''
function theme(){let t=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=t;localStorage.setItem('webfetch-theme',t)}
function filterRows(){let count=0;document.querySelectorAll('#rows tr').forEach(r=>{let show=[...document.querySelectorAll('[data-filter]')].every(s=>!s.value||(s.dataset.filter==='query'?(r.textContent+' '+(r.dataset.search||'')).toLowerCase().includes(s.value.trim().toLowerCase()):r.dataset[s.dataset.filter]===s.value));r.hidden=!show;if(show)count++});let c=document.querySelector('#count');if(c)c.textContent=`显示 ${count} 条`}
function paintSort(t){let col=Number(t.dataset.col);[...t.tHead.rows[0].cells].forEach((h,i)=>{h.classList.toggle('sorted',i===col);h.removeAttribute('aria-sort')});t.tHead.rows[0].cells[col].setAttribute('aria-sort',t.dataset.dir==='asc'?'ascending':'descending');[...t.tBodies[0].rows].forEach(r=>[...r.cells].forEach((c,i)=>c.classList.toggle('sorted',i===col)))}
function sortTable(id,col){let t=document.getElementById(id),dir=t.dataset.col===String(col)&&t.dataset.dir==='desc'?'asc':'desc';let rows=[...t.tBodies[0].rows];rows.sort((a,b)=>{let av=a.cells[col].dataset.sort,bv=b.cells[col].dataset.sort;if(av==='-1'||bv==='-1')return av===bv?0:av==='-1'?1:-1;let result=(av==null||bv==null)?a.cells[col].textContent.localeCompare(b.cells[col].textContent,'zh-CN'):Number(av)-Number(bv);return dir==='asc'?result:-result});rows.forEach((r,i)=>{r.cells[0].textContent=i+1;t.tBodies[0].append(r)});t.dataset.col=String(col);t.dataset.dir=dir;paintSort(t)}
function chooseTab(group,key){let box=document.getElementById(group);if(!box)return;box.querySelectorAll(':scope > .tab-bar > button').forEach(b=>{let on=b.dataset.tab===key;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1});box.querySelectorAll(':scope > [role="tabpanel"]').forEach(p=>p.hidden=p.dataset.panel!==key)}
function tabKeys(event,group){if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;let bs=[...document.getElementById(group).querySelectorAll(':scope > .tab-bar > button')],i=bs.indexOf(event.target);i=event.key==='Home'?0:event.key==='End'?bs.length-1:(i+(event.key==='ArrowRight'?1:-1)+bs.length)%bs.length;event.preventDefault();chooseTab(group,bs[i].dataset.tab);bs[i].focus()}
function cascadeFilters(changed){let rows=[...document.querySelectorAll('#rows tr')],selects=[...document.querySelectorAll('select[data-filter]')],prior=[];selects.forEach(s=>{let available=new Set(rows.filter(r=>prior.every(p=>!p.value||r.dataset[p.dataset.filter]===p.value)).map(r=>r.dataset[s.dataset.filter]));if(s.dataset.filter!=='result'){let failureFilter=['failureStage','failureCategory'].includes(s.dataset.filter);[...s.options].forEach(o=>o.disabled=!!o.value&&!available.has(o.value));if(s.value&&!available.has(s.value))s.value=failureFilter?'':([...s.options].find(o=>o.value&&!o.disabled)?.value||'');if(failureFilter)s.disabled=![...s.options].some(o=>o.value&&!o.disabled);}prior.push(s)});filterRows()}
function applyReportAnchor(anchor){if(!anchor)return;if(anchor.startsWith('track-')){chooseTab('dataset-hub','cases');let s=document.querySelector('select[data-filter="track"]');if(s){s.value=anchor.slice(6);document.querySelectorAll('select[data-filter]').forEach(x=>{if(x!==s)x.value=''});cascadeFilters(s)}}else if(['references','sources'].includes(anchor)){chooseTab('dataset-hub',anchor)}else{let target=document.getElementById(anchor);for(let p=target;p;p=p.parentElement){if(p.dataset?.panel)chooseTab(p.parentElement.id,p.dataset.panel);if(p.tagName==='DETAILS')p.open=true}}}
window.addEventListener('DOMContentLoaded',()=>{document.documentElement.dataset.theme=localStorage.getItem('webfetch-theme')||'light';document.querySelectorAll('[data-filter]').forEach(e=>e.addEventListener(e.tagName==='INPUT'?'input':'change',e.tagName==='INPUT'?filterRows:()=>cascadeFilters(e)));cascadeFilters();document.querySelectorAll('table[data-col]').forEach(paintSort);applyReportAnchor(location.hash.slice(1))});
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
def table(headers,rows,id='',sortable=False,cls='',metrics=None):
 head='<th>序号</th>'+''.join(f'<th><button class="sort-button" onclick="sortTable(\'{id}\',{i+1})">{h} ↕</button></th>' if sortable else f'<th>{h}</th>' for i,h in enumerate(headers))
 body=''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))
 return metric_legend(headers if metrics is None else metrics)+f'<div class="table-wrap"><table class="{cls}"'+(f' id="{id}"' if id else '')+f'><thead><tr>{head}</tr></thead><tbody'+(' id="rows"' if id=='filtered-table' else '')+f'>{body}</tbody></table></div>'
EXTRA+='\n.change-log section{padding:24px 0}.change-log h2{font-size:24px}.change-log p{margin:10px 0;line-height:1.75}.change-log summary{font-weight:650;color:var(--accent)}.change-log .table-wrap{overflow-x:auto}\n'

def write(out,path,title,body):
 depth=len(Path(path).parts)-1;base='../'*depth
 nav=[('index.html','首页'),('guide/index.html','报告解读'),('datasets/index.html','评测集'),('methodology/index.html','评测方法'),('analysis/index.html','结果分析'),('adoption/index.html','方法介绍'),('environment/index.html','环境'),('reproduce/index.html','复现'),('accounts/index.html','账号')]
 nav.insert(2,('changes/index.html','变更记录'))
 page=f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap"><a class="brand" href="{base}index.html">Web Fetch 评测</a>'+''.join(link(base+url,label) for url,label in nav)+f'<button class="theme-toggle" onclick="theme()">切换主题</button></nav></header><main>{body}</main><footer class="wrap source-note"><p>所有分数由本轮调用记录计算。网页与服务可能变化；结论仅适用于本轮样本和配置。</p></footer></body></html>'
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
 winners='、'.join(n for n in names if rates.get(n)==maximum) if maximum else '本轮无通过任务' if rates else '暂无调用记录'
 return f'<div class="card"><div class="eyebrow">{len(selected)} 例 · 已测 {len({r["provider"] for r in rs})}/{len(names)} 种方法</div><h3>{link("#home-rankings-"+track,label)}</h3><p>{esc(winners)}</p><p>最高任务通过率 <b>{pct(maximum)}</b></p></div>'

def failure_category(r,c):
 if not measured(r,c):return '未测：缺少验收证据'
 if succeeded(r,c):return '—'
 return (r.get('diagnosis') or diagnose(r,c,'') or {'category':'任务规则未满足'})['category']

def reason(r,c):
 if not measured(r,c):return '未测：历史调用未记录原文件字节验收证据，不计入任务通过率或失败数。'
 if r['metrics'].get('deadline_overrun'):return '超过该调用登记的端到端时间预算；保留实际耗时和返回内容，但不计为按时交付。'
 if r['ok'] and 'stable_task_pass' in r['metrics']:
  m=r['metrics']
  counts=f"检出 {m.get('stable_record_count','—')} 条，字段完整 {m.get('stable_complete_record_count','—')} 条，完整且唯一 {m.get('stable_unique_record_count','—')} 条，要求至少 {m.get('stable_minimum_records','—')} 条。"
  success='稳定内容与结构检查通过。' if m.get('stable_anchor_results') or m.get('stable_page_pattern_results') else '条目结构检查通过；本例未设置稳定文字核对点。'
  return counts+(success if succeeded(r,c) else '稳定任务规则未满足：'+'；'.join(m.get('stable_task_errors') or m.get('forbidden_hits') or ['见稳定检查结果']))
 if r.get('diagnosis'):return r['diagnosis']['evidence']
 if not r['ok']:
  names={'dns':'请求路径中的域名无法解析','timeout':'请求超时','network':'请求路径网络故障','api_error':'供应商接口报错','WEB_BLOCKED_URL':'方法的 URL 安全规则拒绝请求'}
  return names.get(r.get('error_type'),r.get('error_type') or '请求失败')+': '+str(r.get('error_message') or '')[:300]
 if c.get('task_kind')=='structure':
  from .report_components import list_rule_description
  return f"检出 {r['metrics'].get('named_entry_count',0)} 个具名详情入口。"+list_rule_description(c)
 if c.get('task_kind')=='file':return '没有返回可核对的原始文件；文字或重复输入地址不算文件。'
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
 return content+table(['条目字段','结构判定','原因'],records)+('<p>任务检查错误：'+esc('；'.join(m['stable_task_errors']))+'</p>' if m.get('stable_task_errors') else '')

def ranking(records,cases,track,base='',id=None,compact=False):
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
  values=cell(link(base+f'providers/{name}.html',name))+cell(rate(ok,len(rs)),ok/len(rs))
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
  values+=cell(f'{n}/{len(rs)}',n/len(rs))
  values+=cell(f'{len({r["case_id"] for r in rs})}/{len(selected)}')
  rows.append(('',values))
 heads=['方法','请求成功率']
 if track in {'web','dynamic','document','challenge'}:
  heads+=['任务通过率','完整度','纯净度','F1','文字核对覆盖']
 else:heads+=['任务通过率','资源命中率','文件取得率','信息通过率']
 heads+=['均值','P50','P95','已判分/已调用','已执行/总案例']
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
 else:note='任务通过率以有验收证据的任务为分母；原文件历史记录缺少字节验收时显示未测，不计为失败。资源、文件、信息三列各按已测子任务计算。'
 note+=' 已判分/已调用：能判通过或失败的次数/调用次数；已执行/总案例：调用过的不同案例数/本类案例数。两者都不是通过率或正文完整度。未测不进入通过率和失败数。均值、P50、P95 仅统计各方法自身通过项，不能直接视为同题速度排名。'
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
 return ('<details class="ranking-metric-help"><summary>辅助诊断 · 返回条目的标题文字质量</summary>'
  '<p>按详情链接对应独立名称参考，不固定榜单成员和名次。仅统计能对应参考的返回标题；不同方法覆盖的案例可能不同，不能据此直接比较整页正文质量，也不能替代任务验收。</p>'
  +table(['方法','标题完整度','标题纯净度','标题 F1','标题参考覆盖'],rows,identifier+'-titles',True)+ '</details>')

def filter_controls(options):
 controls=''
 for key,label,choices in options:
  ordered=([('','全部')]+choices) if key in {'track','category','failureStage','failureCategory'} else (choices+[('','全部')])
  controls+=f'<label>{label} <select data-filter="{key}" aria-label="{label}">'+''.join(f'<option value="{esc(v)}"'+(' selected' if key=='result' and v=='fail' else '')+f'>{esc(t)}</option>' for v,t in ordered)+'</select></label>'
 return '<div class="filters">'+controls+'<input data-filter="query" aria-label="搜索" placeholder="搜索案例、方法或原因"><span id="count" role="status" aria-live="polite"></span></div>'

def failure_filters(records,cases):
 categories=sorted({failure_category(r,cases[r['case_id']]) for r in records if measured(r,cases[r['case_id']]) and not succeeded(r,cases[r['case_id']])})
 return [('failureStage','失败环节',[('request','请求失败'),('task','请求成功但任务失败')]),('failureCategory','失败类别',[(x,x) for x in categories])]

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
 validate_report_ids(dataset,records)
 for path in ['assets/style.css','assets/report.js','references']:
  contained_path(out,path)
 for c in dataset['cases']:
  contained_path(out,'references/'+c['id']+'.txt')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA);(out/'assets/report.js').write_text(JS)
 cases={c['id']:c for c in dataset['cases']};names=sorted(set(r['provider'] for r in records))
 dynamic=any(c.get('task_kind')=='structure' for c in cases.values())
 bycase=defaultdict(list);byprovider=defaultdict(list)
 for r in records:bycase[r['case_id']].append(r);byprovider[r['provider']].append(r)
 cards=''
 for track,label in TRACKS.items():
  cards+=overview_card(records,cases,track,label,names)
 home='<div class="home-page">'+hero('Web Fetch 评测',f'{len(cases)} 个案例 · {len(names)} 种方法 · 六类任务分别评测，不混成一个总分。')
 from .report_explainer import TITLE,render_explainer
 home+='<aside class="wrap home-reading" aria-label="报告解读"><div><strong>报告解读</strong><p>'+link('guide/index.html',TITLE)+'</p></div><p>从分数差异、失败案例和实现机制，理解如何选择抓取方法。</p>'+link('guide/index.html','阅读全文 →')+'</aside>'
 write(out,'guide/index.html','报告解读',render_explainer(out,cases,records,table=table,cell=cell,link=link))
 panels=[]
 for track,label in TRACKS.items():
  if not any(c.get('track','web')==track for c in cases.values()):continue
  selected=[c for c in cases.values() if c.get('track','web')==track]
  coverage=f'{len(selected)} 个案例 · {len({r["provider"] for r in records if cases[r["case_id"]].get("track","web")==track})} 种已测方法'
  panels.append((track,label,f'<div class="track-heading"><h3>{label}</h3><span>{coverage}</span></div>'+ranking(records,cases,track,compact=True)+f'<p>{link("datasets/index.html#track-"+track,"案例与参考 →")}</p>'))
 home+=section('实测结果','<p class="ranking-orientation">按任务选榜，不合并跨类型总分。默认按任务通过率排序，点击列名可切换；质量分数与通过率分别计算。</p>'+tabs('home-rankings',panels),'results')
 home+='<div class="wrap"><details class="home-overview"><summary>各类结果概览 · 样本与最高通过率</summary><p class="source-note">不同榜单的任务要求不同，最高通过率不能直接作跨榜能力比较。</p><div class="grid">'+cards+'</div></details></div>'
 write(out,'index.html','首页',home+'</div>')
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
  body=hero(esc(c['name']),esc(task_instruction(c)))+section('源页与参考',status_note+f'<p>{link(c["url"],c["url"])}</p><details><summary>来源记录</summary><p>{esc(v.get("method"))} · {esc(v.get("checked_at"))}</p><p>{esc(v.get("note"))}</p></details><p>{link("../references/"+c["id"]+".txt","下载参考")} · {source_record}</p><details><summary>参考正文 / 目标结果</summary><pre>{esc(ref)}</pre></details>')
  body+=section('评分核对点',(table(['核对点','允许表达'],facts) if facts else '')+rules+(table(['资源','允许地址'],resources) if resources else '')+('<p>检查是否返回可核对的原始文件。</p>' if c.get('task_kind')=='file' else ''),'facts')
  if c.get('task_kind')=='structure':
   example=out/'sources'/c['id']/'items.json'
   if example.exists():
    items=json.loads(example.read_text())
    sample=[('',cell(esc(x.get('title') or x.get('text','')))+cell(link(x['url'],'详情 ↗') if x.get('url') else '—')) for x in items]
    body+=section('公开条目快照','<p class="source-note">采集时的公开字段作为来源证据保留，不要求返回相同名单或排序；稳定任务按上方内容锚点与字段要求判定。</p><details><summary>展开 '+str(len(items))+' 条</summary>'+table(['名称 / 内容','入口'],sample)+'</details>')
  if c.get('body_requirements'):
   rules=c['body_requirements']
   body+=section('正文范围验收',f'<p>参考正文分为 {len(rules["regions"])} 个连续区域，每区词语计数召回至少 {pct(rules["minimum_coverage"])}。这项覆盖下限与全文 F1 独立；词频重合不证明顺序、归属或逐句事实正确。</p>')
  body+=section('全部方法执行结果',table(['方法']+headers[1:],[(attrs,values.replace(esc(c['name']),esc(r['provider']),1)) for r in bycase[c['id']] for attrs,values in [attempt_row(r,c)]]))
  write(out,'datasets/'+c['id']+'.html',c['name'],body)
 for name,rs in byprovider.items():
  info=method_info(rs[0]['provider_type'])
  controls=filter_controls([('track','类型',list(TRACKS.items())),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(rs,cases))
  body=hero(esc(name),esc(info['contract']))+section('本轮配置',f'<p><code>{esc(info["endpoint"])}</code></p><p>{esc(info["freshness"])}</p><p>网站正文使用默认正文输出；资源任务为支持资源返回的 Exa、Tavily、Firecrawl 开启原生资源字段。未开启额外的理解模型或付费后备方法。</p>')
  price_anchor='pricing-'+name if name in {'firecrawl','tavily','exa','cloudflare-markdown'} else 'method-guide-pricing'
  body+=section('方法资料','<p class="actions">'+link('../adoption/index.html#'+price_anchor,'价格与计费 →')+link('../adoption/index.html#interface-'+name,'接口与示例 →')+'</p>')
  body+=section(f'{len(rs)} 条执行记录',controls+table(headers,[attempt_row(r,cases[r['case_id']]) for r in rs],'filtered-table'))
  write(out,'providers/'+name+'.html',name,body)
 for r in records:
  c=cases[r['case_id']];m=r['metrics'];ref=c.get('ground_truth_main_content') or c.get('reference_answer') or '检查下列目标资源。'
  output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
  rows=[('',cell(k)+cell(v)) for k,v in [('方法',link('../providers/'+r['provider']+'.html',r['provider'])),('请求','成功' if r['ok'] else '失败'),('任务结果','通过' if succeeded(r,c) else '未通过'),('HTTP 状态',esc(r.get('status_code'))),('耗时',ms(r['elapsed_ms'])),('全文完整度 / 纯净度 / F1',f'{pct(m.get("word_recall"))} / {pct(m.get("word_precision"))} / {pct(m.get("word_f1"))}'),('输出字符',f'{len(output):,}'),('输出校验值',f'<code class="break">{esc(m.get("content_sha256"))}</code>')]]
  if not c.get('ground_truth_main_content'):
   rows[5]=('',cell('全文完整度 / 纯净度 / F1')+cell('— / — / —（全文指标无完整参考，未评测）'))
  else:
   from .report_components import comparable_body_reference
   if not comparable_body_reference(c):
    rows[5]=('',cell('快照文字诊断 · 完整度 / 纯净度 / F1')+cell(' / '.join(pct(m.get(k)) for k in ['word_recall','word_precision','word_f1'])+'；参考含不固定取值的字段，仅为历史快照诊断，不进入主表全文均值。'))
  if 'content_check_total' in m:
   rows.append(('',cell('文字核对覆盖')+cell(pct(m.get('content_check_coverage'))+f' · {m["content_check_hits"]}/{m["content_check_total"]} 组')))
  rows[2]=('',cell('任务结果')+cell(result_label(r,c)))
  if r.get('raw_request_status'):
   rows.append(('',cell('原始适配器状态')+cell(esc(json.dumps(r['raw_request_status'],ensure_ascii=False)))))
  rows.append(('',cell('评分实现 SHA-256')+cell(esc(m.get('scoring_source_sha256')))))
  title_details=''
  if 'title_quality' in r:
   q=r['title_quality']
   rows.append(('',cell('标题完整度 / 标题纯净度 / 标题 F1')+cell(' / '.join(pct(q[k]) for k in ['recall','precision','f1']))))
   rows.append(('',cell('标题参考覆盖')+cell(f'{q["scored_titles"]}/{q["returned_titles"]} 条；仅衡量已返回且有独立参考的标题')))
   checks=[('',cell(esc(x['title']))+cell(esc(x['reference_used'] or '无独立名称参考，未评分'))+cell(link(x['url'],'详情链接'))+cell(' / '.join(pct(x[k]) for k in ['recall','precision','f1']))) for x in q['checks']]
   title_details='<details><summary>逐条标题对照与来源</summary><p>'+link('../'+q['reference_path'],'独立参考')+' · 采集时间 '+esc(q.get('reference_checked_at'))+'</p>'+table(['返回标题','参考标题','条目身份','完整度 / 纯净度 / F1'],checks,metrics=[])+f'<p>参考 SHA-256：<code class="break">{esc(q["reference_sha256"])}</code></p><p>评分实现 SHA-256：<code class="break">{esc(q["scorer_sha256"])}</code></p></details>'
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   rows[2]=('',cell('任务结果')+cell(result_label(r,c)))
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
  body=hero(esc(c['name']),esc(task_instruction(c)))+section('执行结果',caveat+f'<div class="callout">{esc(reason(r,c))}</div><details><summary>指标说明</summary>'+metric_legend(detail_metrics)+'</details>'+table(['项目','结果'],rows,metrics=[])+f'<p>{link(c["url"],"打开源页 ↗")} · {link("../datasets/"+c["id"]+".html","该案例与全部方法")}</p>')
  if title_details:body+=section('标题文字质量',metric_legend(['标题完整度','标题纯净度','标题 F1','标题参考覆盖'])+title_details)
  if r.get('diagnosis') and not succeeded(r,c):
   d=r['diagnosis'];s=d.get('independent_source_check') or {}
   facts=[('失败分类',d['category']),('发生阶段',d['stage']),('判定依据',d['evidence']),('归因边界',d['certainty'])]
   if s:facts.extend([('独立源页复查',f"{s.get('checked_at','')} · HTTP {s.get('status','未取得')} · {s.get('title') or s.get('error','')}"),('复查边界',d['source_check_boundary'])])
   body+=section('失败原因',table(['项目','依据'],[('',cell(esc(k))+cell(esc(v))) for k,v in facts],metrics=[]))
  reference_heading='列表任务规则 / 参考依据' if c.get('task_kind')=='structure' else '完整参考 / 目标结果'
  reference_kind='reference';reference_download='../references/'+c['id']+'.txt'
  if 'stable_task_pass' in m and c.get('acceptance_contract'):
   reference_heading='稳定任务契约'
   reference_kind='contract';reference_download='../contracts/'+c['id']+'.json'
   ref=json.dumps(c['acceptance_contract'],ensure_ascii=False,indent=2)
  body+=section('参考与实际输出',f'<div class="compare"><div><h3>{reference_heading}</h3><p>{link(reference_download,"下载契约" if reference_kind=="contract" else "下载参考")}</p><pre data-kind="{reference_kind}">{esc(ref)}</pre></div><div><h3>本次完整输出</h3><p>{link("../"+r["artifact_path"],"下载原始输出")}</p>'+('<p class="source-note">不可显示的控制字节以 \\xNN 展示；下载文件保留原始输出。</p>' if readable_output(output)!=output else '')+f'<pre data-kind="output">{esc(readable_output(output))}</pre></div></div>')
  checks_html=stable_checks(m) if 'stable_task_pass' in m else (table(['核对点','目标','判定','实际命中'],checks) if checks else '<p>本例按任务结构或原文件输出判定。</p>')+''.join(f'<p>{esc(x["id"])}：实际 {x["count"]}，要求至少 {x["minimum"]}。</p>' for x in m.get('pattern_results',[]))
  if m.get('body_region_results'):
   checks_html+=table(['正文区域','覆盖','最低要求','判定'],[('',cell(esc(x['id']))+cell(pct(x['coverage']))+cell(pct(x['minimum']))+cell('通过' if x['pass'] else '未通过')) for x in m['body_region_results']],metrics=[])
  if m.get('stable_field_results'):
   checks_html+=table(['记录字段','有效记录数','至少要求','判定'],[('',cell(esc(x['field']))+cell(str(x['count']))+cell(str(x['minimum']))+cell('通过' if x['pass'] else '未通过')) for x in m['stable_field_results']],metrics=[])
  body+=section('逐项判定',checks_html+f'<details><summary>接口返回的附加字段</summary><pre data-kind="metadata">{esc(json.dumps(r.get("provider_metadata",{}),ensure_ascii=False,indent=2))}</pre></details>')
  write(out,'attempts/'+r['attempt_id']+'.html',c['name']+' · '+r['provider'],body)
 controls=filter_controls([('provider','方法',[(n,n) for n in names]),('track','类型',list(TRACKS.items())),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(records,cases))
 write(out,'analysis/index.html','结果分析',hero('结果分析','按方法、任务结果、失败环节和失败类别筛选；完整输出见案例详情。')+section('执行记录',controls+table(['方法']+headers,[(case_attrs(r,cases[r['case_id']]),cell(link('../providers/'+r['provider']+'.html',r['provider']))+attempt_row(r,cases[r['case_id']])[1]) for r in records],'filtered-table')))
 write_support(out,records,manifests)
 from .report_changes import render_changes
 render_changes(out,cases,records)
 return out/'index.html'

def write_support(out,records,manifests):
 from .report_components import task_contract_guide
 methodology=hero('评测方法','按任务定义验收规则。同组已测方法使用相同的冻结任务与参考，覆盖数量在各榜单列明。')
 examples_html=re.sub(r'<a href="../datasets/([^"#]+).html">([^<]*)</a>',lambda m:m[0] if (out/'datasets'/(m[1]+'.html')).exists() else m[2],examples())
 methodology+=section('每类怎么测',examples_html)+section('指标说明',metric_guide())
 methodology+=section('共同输入与调用预算','<p>所有方法进入同一个评分入口。正文、HTML/Markdown 链接与明确配对的结构化名称—链接字段按同一规则核验；不把请求回显或诊断日志当成答案。原始输出与原始适配器状态保留，报告层不按方法名称改写请求或任务结果。</p><p>计时覆盖调用、下载、渲染、本地解析和必要输入转换。每次使用一个端到端预算；超时内容不再接收。无法取消的本地调用会隔离该方法，剩余未执行案例列为未运行，不继续并发堆积请求。历史记录仅按登记耗时与预算重新判断交付资格，不补认证当时存在硬截止。</p><p>完整正文任务除原有核对点外，按参考正文最多五个连续区域逐区检查 80% 词语计数覆盖。它是内容覆盖下限，不保证词序、归属或语义；全文 F1 独立保留实际值。文件任务缺少字节验收时为未测，资源 URL 不等于文件。</p>')
 if (out/'scoring-revisions.json').exists():
  methodology+=section('评分来源与修订','<p>当前成绩包含对保存输出的离线重算；文件任务另在 2026-09-04 真实补测，选取 36 次新调用替换原缺测项，详情见'+link('../changes/index.html','变更记录')+'。其他历史调用的输出与计时保留，不能把跨批次视图当成同时重跑。'+link('../scoring-revisions.json','逐调用的原评分、新评分及规则校验值')+'记录离线修订，不把新规则伪装成原调用时的运行配置。</p>')
 methodology+=section('稳定任务契约与全文诊断',task_contract_guide(),'task-contracts')
 figures=''.join(f'<figure><img src="../assets/methodology/{name}.png" alt="{label}" style="max-width:100%;height:auto" loading="lazy"><figcaption>{label}</figcaption></figure>' for name,label in [('annotation','参考标注'),('validation','任务验收'),('scoring','评分口径')] if (out/'assets/methodology'/f'{name}.png').is_file())
 if figures:methodology+=section('方法图解',figures)
 methodology+=section('参考与匹配','<p>原始快照保留不变；评分参考清除零宽空格、BOM 和软连字符，并检查空正文、乱码、重复核对点及核对点是否存在于参考中。通过检查不等于语义完整性已获证明，正文范围仍以来源记录为准。</p><p>关键事实按规范化后的可见文本机械匹配：统一 Unicode、大小写及排版空白，菜单路径如 Settings &gt; AI 与 Settings&gt;AI 等价；忽略 Markdown 链接地址、行内代码与加粗标记。相邻独立链接的标签按独立词段核对，不猜测粘连纯文本的分词。保留数字、正负号、否定词、标点和英文词边界。不使用大模型或模糊相似度判定通过。</p>')
 methodology+=section('调用时间','<p>各方法使用同一套冻结任务与参考，但执行批次并非同时发生。时间和运行环境见环境页；实时网页内容、登录状态和站点策略可能变化，成绩只代表所记录的调用。</p>')
 methodology+=section('计算与边界','''<details><summary>全文评分公式</summary><p>完整度＝取回的参考词数 ÷ 参考词数；纯净度＝匹配的正文词数 ÷ 返回词数；F1＝2 × 完整度 × 纯净度 ÷ 两者之和。英文按词、中文按字，统一大小写与 Unicode 字符形式；Markdown 链接只计文字。对有完整参考的全部案例取平均；请求失败计 0，请求成功但任务未通过仍按实际正文评分，没有参考不计算。</p></details><details><summary>参考范围与公平性</summary><p>网页、文档使用独立保存的参考正文；仓库以 README 为准，列表只覆盖指定的已加载范围，不要求无限滚动或跨页翻页。登录页和验证码不能代替目标正文。参考正文自身包含的“404 Not Found”等术语不作为错误页判据。</p><p>不同方法并行，同一方法串行；正文结束后再测其他任务。Firecrawl 每次完成后冷却 6.1 秒。本报告不是 WCXB 官方榜单。</p><p>媒体网址只证明找到入口，不代表取得文件。文件任务没有另接下载器代替参评方法完成。</p></details>''')
 write(out,'methodology/index.html','评测方法',methodology)
 downloads=' · '.join(link('../'+path,label) for path,label in [('dataset.json','评测集与参考'),('results.jsonl','全部调用记录'),('summary.json','分类汇总'),('execution.json','运行参数')])
 write(out,'reproduce/index.html','复现',hero('数据与复现','使用冻结的任务、评分结果与原始输出生成报告。')+section('数据下载','<p>'+downloads+'</p>')+section('重建报告','<p>在评测仓库中执行；读取 evaluation/ 的当前实测数据，不调用供应商接口。</p><pre>python3 scripts/build_report.py</pre><p>输出目录：reports/webfetch/。重新实测使用 scripts/run_benchmark.py，正文配置为 configs/providers.example.json，资源配置为 configs/providers.resources.json。凭据仅从本地环境加载，不包含在报告中。</p>'))
 rows=[]
 for i,m in enumerate(manifests):
  for k,v in [('执行组',i+1),('开始（UTC）',m['started_at']),('完成（UTC）',m.get('completed_at')),('纳入调用数',m.get('attempt_count')),('Python',m['environment']['python']),('平台',m['environment']['platform']),('并发范围',m['parameters'].get('concurrency_scope')),('超时秒',m['parameters']['timeout'])]:rows.append(('',cell(k)+cell(esc(v))))
  rows.append(('',cell('方法')+cell(esc('、'.join(p['name'] for p in m.get('providers',[]))))))
  implementation=m.get('implementation')
  if implementation:
   rows.append(('',cell('方法版本')+cell(esc(implementation['provider']+' '+implementation['version']))))
   rows.append(('',cell('可执行文件 SHA-256')+cell('<code class="tiny break">'+esc(implementation['sha256'])+'</code>')))
 errors=Counter((r['provider'],r.get('error_type') or 'unknown') for r in records if not r['ok'])
 errorrows=[('',cell(esc(n))+cell(esc(t))+cell(str(count))) for (n,t),count in sorted(errors.items())]
 write(out,'environment/index.html','环境',hero('执行环境','各组使用的运行环境、时间与请求参数。')+section('测试条件',f'<p>共纳入 {len(records):,} 次调用。任务范围与方法配置以保存的数据为准；不向参评方法提供网站登录凭据，不操作验证码。稳定内容锚点与字段结构、类型、数量及唯一性按声明范围验收，不固定频繁变化的名单和价格。任务通过不证明变化值的事实准确性或实时性。</p>')+section('运行参数',table(['参数','值'],rows))+section('请求失败统计',table(['方法','故障分类','次数'],errorrows)))
 accountrows=[('',cell(esc(a['provider']))+cell(link(a['registration_url'],'注册 / 登录')+'<br>'+link(a['key_url'],'获取凭据'))+cell('<br>'.join('<code>'+esc(x)+'</code>' for x in a['env_vars']))+cell(esc(a['steps']))+cell(esc(a['free_note']))) for a in ACCOUNT_REGISTRY]
 write(out,'accounts/index.html','账号',hero('账号与凭据','只列官方入口和环境变量名称，不保存实际凭据。')+section('注册清单',table(['提供方','官方入口','环境变量','取得方式','免费 / 准入说明'],accountrows,cls='accounts-table')))
 from .report_methods import render_methods
 write(out,'adoption/index.html','方法介绍',render_methods(out,records,hero=hero,section=section,table=table,cell=cell,link=link,esc=esc))
