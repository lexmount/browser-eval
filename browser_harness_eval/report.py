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
from .metrics import percentile
from .methods import method_info
from .accounts import ACCOUNT_REGISTRY
from .report_style import STYLE
from .report_components import tabs, metric_guide, metric_legend, examples, reference_status, basis_label, reference_label, sources_panel
from .failure_diagnosis import diagnose

TRACKS={'web':'网页正文','document':'文档正文','image':'图片获取','video':'视频获取','challenge':'真实网站访问'}
CONTRACTS={
 'web':'100 条网页，读取完整正文；项目仓库以 README 为准。',
 'document':'10 条：6 个直接 PDF、1 个 DOCX、1 个 TXT 和 2 个嵌入文档页面。必须读出文档文字，仅给下载链接不算正文通过。',
 'image':'10 条：9 个含图页面和 1 个直接 PNG。含图页面检查指定正文图片地址；直接 PNG 检查原文件。没有测试图片内容理解。',
 'video':'10 条：7 个视频入口页面、2 个直接视频文件、1 个视频基本信息页。分别检查播放入口、原文件、标题与发布者；没有测试字幕转写或画面理解。',
 'challenge':'10 条真实网站：电商、生活社区、影视和开发社区。不给方法提供站点登录凭据，不操作验证码；目标内容、登录限制与接口故障分别记录。',
}
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

def esc(s):return html.escape(str(s if s is not None else ''))
def readable_output(s):
 return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',lambda m:'\\x'+format(ord(m[0]),'02x'),s)
def pct(n):return '—' if n is None else f'{n*100:.1f}%'
def ms(n):return '—' if n is None else f'{n:,.0f} ms'
def link(url,label):return f'<a href="{esc(url)}">{esc(label)}</a>'
def cell(value,sort=None):return f'<td'+(f' data-sort="{sort}"' if sort is not None else '')+f'>{value}</td>'
def rate(a,b):return pct(a/b) if b else '—'
def section(title,body,id=''):return f'<section class="section"'+(f' id="{id}"' if id else '')+f'><div class="wrap"><h2>{title}</h2>{body}</div></section>'
def hero(title,desc):return f'<section class="hero"><div class="wrap"><div class="eyebrow">Web Fetch · 实测报告</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'
def table(headers,rows,id='',sortable=False,cls='',metrics=None):
 head='<th>序号</th>'+''.join(f'<th><button class="sort-button" onclick="sortTable(\'{id}\',{i+1})">{h} ↕</button></th>' if sortable else f'<th>{h}</th>' for i,h in enumerate(headers))
 body=''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))
 return metric_legend(headers if metrics is None else metrics)+f'<div class="table-wrap"><table class="{cls}"'+(f' id="{id}"' if id else '')+f'><thead><tr>{head}</tr></thead><tbody'+(' id="rows"' if id=='filtered-table' else '')+f'>{body}</tbody></table></div>'
def write(out,path,title,body):
 depth=len(Path(path).parts)-1;base='../'*depth
 nav=[('index.html','首页'),('datasets/index.html','评测集'),('methodology/index.html','评测方法'),('analysis/index.html','结果分析'),('adoption/index.html','方法介绍'),('environment/index.html','环境'),('reproduce/index.html','复现'),('integration/index.html','集成'),('accounts/index.html','账号')]
 page=f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap"><a class="brand" href="{base}index.html">Web Fetch 评测</a>'+''.join(link(base+url,label) for url,label in nav)+f'<button class="theme-toggle" onclick="theme()">切换主题</button></nav></header><main>{body}</main><footer class="wrap source-note"><p>所有分数由本轮调用记录计算。网页与服务可能变化；结论仅适用于本轮样本和配置。</p></footer></body></html>'
 target=out/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

def succeeded(r,c):
 if not r['ok']:return False
 if c.get('task_kind')=='structure':return bool(r['metrics'].get('list_task_pass'))
 if c.get('task_kind')=='resource':return bool(r['metrics'].get('resource_pass'))
 if c.get('task_kind')=='file':return bool(r['metrics'].get('raw_file_pass'))
 return bool(r['metrics']['content_pass'])

def failure_category(r,c):
 if succeeded(r,c):return '—'
 return (r.get('diagnosis') or diagnose(r,c,'') or {'category':'任务规则未满足'})['category']

def reason(r,c):
 if r.get('diagnosis'):return r['diagnosis']['evidence']
 if not r['ok']:
  names={'dns':'请求路径中的域名无法解析','timeout':'请求超时','network':'请求路径网络故障','api_error':'供应商接口报错','WEB_BLOCKED_URL':'方法的 URL 安全规则拒绝请求'}
  return names.get(r.get('error_type'),r.get('error_type') or '请求失败')+': '+str(r.get('error_message') or '')[:300]
 if c.get('task_kind')=='structure':return f"检出 {r['metrics'].get('named_entry_count',0)} 个具名详情入口；前三条中 {r['metrics'].get('confirmed_entries',0)} 条已独立核实名称与详情身份。"
 if c.get('task_kind')=='file':return '没有返回可核对的原始文件；文字或重复输入地址不算文件。'
 if c.get('task_kind')=='resource':
  missing=[x['id'] for x in r['metrics'].get('resource_results',[]) if not x['pass']]
  return '已找到目标资源。' if not missing else '未找到目标资源：'+', '.join(missing)
 m=r['metrics'];parts=[]
 if not m.get('length_pass'):parts.append('正文过短')
 if m.get('forbidden_hits'):parts.append('命中排除文本：'+', '.join(m['forbidden_hits']))
 missing=[x['id'] for x in m.get('fact_results',[]) if not x['pass']]
 if missing:parts.append('缺少核对点：'+', '.join(missing))
 for x in m.get('pattern_results',[]):
  if not x['pass']:parts.append(f"{x['id']}仅{x['count']}项，要求{x['minimum']}项")
 return '；'.join(parts) or '正文核对点通过；完整程度另见全文指标。'

def ranking(records,cases,track,base='',id=None):
 dynamic=track=='challenge' and any(c.get('task_kind')=='structure' for c in cases.values())
 groups=defaultdict(list)
 for r in records:
  if cases[r['case_id']].get('track','web')==track:groups[r['provider']].append(r)
 rows=[]
 for name,rs in sorted(groups.items(),key=lambda kv:(-sum(succeeded(r,cases[r['case_id']]) for r in kv[1])/len(kv[1]),kv[0])):
  n=len(rs);ok=sum(bool(r['ok']) for r in rs);passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  body=[r for r in rs if cases[r['case_id']].get('task_kind','body')=='body'];bodyok=sum(r['ok'] for r in body);bodypass=sum(r['ok'] and r['metrics']['content_pass'] for r in body)
  if dynamic:bodyok,bodypass=ok,passed
  full=[r for r in rs if r['metrics'].get('word_f1') is not None]
  avg=lambda key:statistics.mean(r['metrics'][key] for r in full) if full else None
  times=[r['elapsed_ms'] for r in rs if succeeded(r,cases[r['case_id']])]
  values=cell(link(base+f'providers/{name}.html',name))+cell(rate(ok,n),ok/n)
  if track in {'web','document','challenge'}:
   values+=cell(rate(passed,n),passed/n)
   if track!='challenge':values+=''.join(cell(pct(avg(k)),avg(k) if full else -1) for k in ['word_recall','word_precision','word_f1'])
  else:
   resources=[r for r in rs if cases[r['case_id']].get('task_kind')=='resource'];files=[r for r in rs if cases[r['case_id']].get('task_kind')=='file']
   values+=cell(rate(passed,n),passed/n)
   for subset,total in [(resources,sum(succeeded(r,cases[r['case_id']]) for r in resources)),(files,sum(succeeded(r,cases[r['case_id']]) for r in files)),(body,bodypass)]:
    values+=cell(rate(total,len(subset)),total/len(subset) if subset else -1)
  values+=cell(ms(statistics.mean(times)) if times else '—',statistics.mean(times) if times else -1)+cell(ms(percentile(times,.5)),percentile(times,.5) or -1)+cell(ms(percentile(times,.95)),percentile(times,.95) or -1)
  rows.append(('',values))
 heads=['方法','请求成功率']
 if track in {'web','document','challenge'}:
  heads+=['任务通过率']
  if track!='challenge':heads+=['完整度','纯净度','F1']
 else:heads+=['任务通过率','资源命中率','文件取得率','信息通过率']
 heads+=['均值','P50','P95']
 note='请求成功率和任务通过率以全部任务为分母。'
 if dynamic:note='通过须返回至少 3 条具名详情链接，且前三条均有独立内容证据。分母为全部任务；未证实不算通过，但不等于内容错误。不测实时排名、价格和整页 F1。'
 elif track in {'web','document'}:note+=f' 全文指标覆盖 {len(next(iter(groups.values()),[]))} 例；请求失败计 0，请求成功但任务未通过仍按实际正文评分。'
 elif track!='challenge':note='任务通过率以全部任务为分母；资源、文件、信息三列各按对应子任务计算。耗时只统计通过项。'
 result=table(heads,rows,id or 'ranking-'+track,True,'ranking')
 return '<p class="source-note">'+note+'</p>'+result.replace('<table class="ranking"','<table class="ranking" data-col="3" data-dir="desc"')

def filter_controls(options):
 controls=''
 for key,label,choices in options:
  ordered=([('','全部')]+choices) if key in {'track','category','failureStage','failureCategory'} else (choices+[('','全部')])
  controls+=f'<label>{label} <select data-filter="{key}" aria-label="{label}">'+''.join(f'<option value="{esc(v)}"'+(' selected' if key=='result' and v=='fail' else '')+f'>{esc(t)}</option>' for v,t in ordered)+'</select></label>'
 return '<div class="filters">'+controls+'<input data-filter="query" aria-label="搜索" placeholder="搜索案例、方法或原因"><span id="count" role="status" aria-live="polite"></span></div>'

def failure_filters(records,cases):
 categories=sorted({failure_category(r,cases[r['case_id']]) for r in records if not succeeded(r,cases[r['case_id']])})
 return [('failureStage','失败环节',[('request','请求失败'),('task','请求成功但任务失败')]),('failureCategory','失败类别',[(x,x) for x in categories])]

def case_attrs(r,c):
 content='na' if c.get('task_kind','body')!='body' else 'pass' if r['ok'] and r['metrics']['content_pass'] else 'fail'
 resources='na' if not c.get('expected_resources') else 'pass' if r['ok'] and r['metrics'].get('resource_pass') else 'fail'
 stage='' if succeeded(r,c) else 'task' if r['ok'] else 'request'
 category='' if not stage else failure_category(r,c)
 return f' data-track="{c.get("track","web")}" data-category="{esc(c["category"])}" data-provider="{esc(r["provider"])}" data-transport="{"pass" if r["ok"] else "fail"}" data-result="{"pass" if succeeded(r,c) else "fail"}" data-content="{content}" data-resources="{resources}" data-failure-stage="{stage}" data-failure-category="{esc(category)}" data-search="{esc(reason(r,c))}"'

def attempt_row(r,c,base='../'):
 m=r['metrics'];state='通过' if succeeded(r,c) else '未通过'
 if c.get('task_kind')=='structure':state='通过' if succeeded(r,c) else '未通过'
 values=cell(link(base+'attempts/'+r['attempt_id']+'.html',c['name']))+cell(TRACKS[c.get('track','web')])+cell(f'<span class="{"good" if succeeded(r,c) else "bad"}">{state}</span>')+cell(esc(failure_category(r,c)))+cell(ms(r['elapsed_ms']))
 return case_attrs(r,c),values

def generate(out:Path,dataset:dict,records:list,manifests:list):
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA);(out/'assets/report.js').write_text(JS)
 cases={c['id']:c for c in dataset['cases']};names=sorted(set(r['provider'] for r in records))
 dynamic=any(c.get('task_kind')=='structure' for c in cases.values())
 bycase=defaultdict(list);byprovider=defaultdict(list)
 for r in records:bycase[r['case_id']].append(r);byprovider[r['provider']].append(r)
 cards=''
 for track,label in TRACKS.items():
  rs=[r for r in records if cases[r['case_id']].get('track','web')==track]
  counts={n:sum(succeeded(r,cases[r['case_id']]) for r in rs if r['provider']==n) for n in names};maximum=max(counts.values(),default=0)
  winners='、'.join(n for n in names if counts[n]==maximum) if maximum else '本轮无通过任务'
  count=len([c for c in cases.values() if c.get('track','web')==track])
  if not count:continue
  cards+=f'<div class="card"><div class="eyebrow">{count} 例</div><h3>{link("#home-rankings-"+track,label)}</h3><p>{esc(winners)}</p><p>最高任务通过 <b>{pct(maximum/count)}</b></p></div>'
 home=hero('Web Fetch 评测',f'{len(cases)} 例 · {len(names)} 种方法 · 网页、文档、图片、视频与真实网站。')
 home+=section('结果概览','<div class="grid">'+cards+'</div><p class="source-note">小样本实测，不代表全网成功率。真实网站组核对前三条名称与详情身份；其他组按完整正文或指定资源判定。</p>')
 webrows={n:[r for r in byprovider[n] if cases[r['case_id']].get('track','web')=='web'] for n in names}
 quality={n:statistics.mean(r['metrics']['word_f1'] for r in rs if r['metrics'].get('word_f1') is not None) for n,rs in webrows.items() if any(r['metrics'].get('word_f1') is not None for r in rs)}
 leader=max(quality,key=quality.get) if quality else None
 mainleader=max(webrows,key=lambda n:sum(succeeded(r,cases[r['case_id']]) for r in webrows[n]))
 files=[r for r in records if cases[r['case_id']].get('task_kind')=='file']
 if leader:
  home+=section('主要结论',f'<p>网页任务通过率以 {esc(mainleader)} 最高；全文 F1 以 {esc(leader)} 最高（{pct(quality[leader])}）。选型需同时看关键内容是否齐全、全文是否完整。</p><p>文档测文字，图片与视频测入口或文件，不测内容理解。本轮 {sum(succeeded(r,cases[r["case_id"]]) for r in files)} 次原文件调用通过，共 {len(files)} 次。</p>')
 panels=[]
 for track,label in TRACKS.items():
  if not any(c.get('track','web')==track for c in cases.values()):continue
  panels.append((track,label,f'<h3>{label}</h3><p>{CONTRACTS[track]}</p>'+ranking(records,cases,track)+f'<p>{link("datasets/index.html#track-"+track,"案例与参考 →")}</p>'))
 home+=section('实测结果',tabs('home-rankings',panels))
 write(out,'index.html','首页',home)
 controls=filter_controls([('track','类型',list(TRACKS.items())),('category','类别',[(x,x) for x in dict.fromkeys(c['category'] for c in cases.values())])])
 rows=[]
 for c in cases.values():
  verified=c.get('source_verification',{});state='受限' if verified.get('state')=='restricted' else '有效'
  rows.append((f' data-track="{c.get("track","web")}" data-category="{c["category"]}"',cell(link(c['id']+'.html',c['name']))+cell(esc(c['category']))+cell(link(c['url'],'源页 ↗'))+cell(link(c['id']+'.html#facts',basis_label(c)))+cell(reference_label(c))+cell(state)+cell(rate(sum(succeeded(r,c) for r in bycase[c['id']]),len(bycase[c['id']])))))
 anchors=''.join(f'<span id="track-{t}"></span>' for t in TRACKS)
 dataset_panels=[('cases','案例',anchors+controls+'<p class="source-note">核对依据可点击展开。访问受限不等于网站失效；动态列表核验前三条名称与链接，不固定当天的榜单。</p>'+table(['案例','类别','URL','核对依据','参考状态','原访问','达标率'],rows,'filtered-table')),('references','参考状态','<span id="references"></span>'+reference_status(list(cases.values()))),('sources','来源','<span id="sources"></span>'+sources_panel())]
 write(out,'datasets/index.html','评测集',hero('评测集',f'{len(cases)} 个案例，按任务类型查看。')+section('案例与依据',tabs('dataset-hub',dataset_panels)))
 headers=['案例','类型','结果','失败分类','耗时']
 for c in cases.values():
  v=c.get('source_verification',{});ref=c.get('ground_truth_main_content') or c.get('reference_answer') or '本例按资源或结构规则评分，不以页面短摘要计算全文 F1。'
  (out/'references').mkdir(exist_ok=True);(out/'references'/f'{c["id"]}.txt').write_text(ref)
  facts=[('',cell(esc(f['id']))+cell(esc(' / '.join(f['alternatives'])))) for f in c.get('required_facts',[])]
  resources=[('',cell(esc(x['id']))+cell('<br>'.join(link(u,u) for u in x.get('urls',[]))+('<br>入口地址规则：<code>'+esc(' / '.join(x['url_patterns']))+'</code>' if x.get('url_patterns') else ''))) for x in c.get('expected_resources',[])]
  rules=''.join(f'<p>{esc(x["id"])}：至少 {x.get("minimum",1)} 项；<code>{esc(x["pattern"])}</code></p>' for x in c.get('required_patterns',[]))
  status_note=f'<p class="callout">参考状态：{reference_label(c)}。</p>'
  if c.get('task_kind')=='structure':
   status_note='<p class="callout">核对前三条的名称与详情身份；参考未覆盖的条目须补独立证据，不将榜单变化判作内容错误。</p><p>'+link('../list-references/'+c['id']+'.json','名称与链接核验依据')+' · '+link('../sources/'+c['id']+'/reference.txt','公开条目快照')+'</p>'
   rules='<div class="guide-grid"><article class="guide-card"><h3>固定任务</h3><p>至少 3 个不同条目，每个包含有意义的名称及本站详情入口。</p></article><article class="guide-card"><h3>不固定内容</h3><p>不要求特定标题、排序、价格或条目 ID；登录页、重复入口、空链接不计入。</p></article></div>'
  source_record=link('../sources/'+c['id']+'/evidence.json','获取记录') if (out/'sources'/c['id']/'evidence.json').exists() else '未保存源页快照'
  body=hero(esc(c['name']),esc(c.get('task_prompt','')))+section('源页与参考',status_note+f'<p>{link(c["url"],c["url"])}</p><details><summary>来源记录</summary><p>{esc(v.get("method"))} · {esc(v.get("checked_at"))}</p><p>{esc(v.get("note"))}</p></details><p>{link("../references/"+c["id"]+".txt","下载参考")} · {source_record}</p><details><summary>参考正文 / 目标结果</summary><pre>{esc(ref)}</pre></details>')
  body+=section('评分核对点',(table(['核对点','允许表达'],facts) if facts else '')+rules+(table(['资源','允许地址'],resources) if resources else '')+('<p>检查是否返回可核对的原始文件。</p>' if c.get('task_kind')=='file' else ''),'facts')
  if c.get('task_kind')=='structure':
   example=out/'sources'/c['id']/'items.json'
   if example.exists():
    items=json.loads(example.read_text())
    sample=[('',cell(esc(x.get('title') or x.get('text','')))+cell(link(x['url'],'详情 ↗') if x.get('url') else '—')) for x in items]
    body+=section('公开条目快照','<p class="source-note">保留采集时的公开字段，可用于核验名称与详情身份；不要求返回相同名单或排序。</p><details><summary>展开 '+str(len(items))+' 条</summary>'+table(['名称 / 内容','入口'],sample)+'</details>')
  body+=section('全部方法执行结果',table(['方法']+headers[1:],[(attrs,values.replace(esc(c['name']),esc(r['provider']),1)) for r in bycase[c['id']] for attrs,values in [attempt_row(r,c)]]))
  write(out,'datasets/'+c['id']+'.html',c['name'],body)
 for name,rs in byprovider.items():
  info=method_info(rs[0]['provider_type'])
  controls=filter_controls([('track','类型',list(TRACKS.items())),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(rs,cases))
  body=hero(esc(name),esc(info['contract']))+section('本轮配置',f'<p><code>{esc(info["endpoint"])}</code></p><p>{esc(info["freshness"])}</p><p>网页主榜使用正文默认输出；分类试测为支持资源返回的 Exa、Tavily、Firecrawl 开启原生资源字段。未开启额外的理解模型或付费后备方法。</p>')
  body+=section(f'{len(rs)} 条执行记录',controls+table(headers,[attempt_row(r,cases[r['case_id']]) for r in rs],'filtered-table'))
  write(out,'providers/'+name+'.html',name,body)
 for r in records:
  c=cases[r['case_id']];m=r['metrics'];ref=c.get('ground_truth_main_content') or c.get('reference_answer') or '检查下列目标资源。'
  output=(out/r['artifact_path']).read_text(encoding='utf-8',errors='replace')
  rows=[('',cell(k)+cell(v)) for k,v in [('方法',link('../providers/'+r['provider']+'.html',r['provider'])),('请求','成功' if r['ok'] else '失败'),('任务结果','通过' if succeeded(r,c) else '未通过'),('HTTP 状态',esc(r.get('status_code'))),('耗时',ms(r['elapsed_ms'])),('全文完整度 / 纯净度 / F1',f'{pct(m.get("word_recall"))} / {pct(m.get("word_precision"))} / {pct(m.get("word_f1"))}'),('输出字符',f'{len(output):,}'),('输出校验值',f'<code class="break">{esc(m.get("content_sha256"))}</code>')]]
  if c.get('task_kind')=='structure':
   rows[2]=('',cell('任务结果')+cell('通过' if succeeded(r,c) else '未通过'))
   rows[5]=('',cell('条目内容核验')+cell(f"{m.get('confirmed_entries',0)} 条已确认；范围为前三条。整页 F1 不适用。"))
   rows.append(('',cell('具名详情入口')+cell(str(m.get('named_entry_count',0)))))
  checks=[('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',x.get('expected_urls',[])))))+cell('命中' if x['pass'] else '缺失')+cell(esc(' / '.join(x.get('matched',x.get('matched_urls',[])))))) for x in m.get('fact_results',[])+m.get('resource_results',[])]
  if c.get('task_kind')=='structure':
   checks=[('',cell(link(x['url'],x['title']))+cell(esc(' / '.join(x['reference_titles']) or '没有匹配的独立名称证据'))+cell('已确认' if x['verdict']=='confirmed' else '访问受限 · 未证实' if x.get('access_restricted') else '未确认')+cell(esc(' · '.join(filter(None,[x.get('evidence_checked_at'),x.get('access_note')]))))) for x in m.get('list_checks',[])]
  caveat=''
  if c.get('task_kind')=='structure':caveat='<p class="source-note">检验前三条名称与详情是否对应；不核验当前排名、价格、整页召回。'+link('../datasets/'+c['id']+'.html','规则与独立参考')+'</p>'
  detail_metrics=['请求','结果','耗时','HTTP 状态','输出字符','输出校验值']+(['具名详情入口'] if c.get('task_kind')=='structure' else ['完整度','纯净度','F1'] if c.get('ground_truth_main_content') else [])
  body=hero(esc(c['name']),esc(c.get('task_prompt','')))+section('执行结果',caveat+f'<div class="callout">{esc(reason(r,c))}</div><details><summary>指标说明</summary>'+metric_legend(detail_metrics)+'</details>'+table(['项目','结果'],rows,metrics=[])+f'<p>{link(c["url"],"打开源页 ↗")} · {link("../datasets/"+c["id"]+".html","该案例与全部方法")}</p>')
  if r.get('diagnosis'):
   d=r['diagnosis'];s=d.get('independent_source_check') or {}
   facts=[('失败分类',d['category']),('发生阶段',d['stage']),('判定依据',d['evidence']),('归因边界',d['certainty'])]
   if s:facts.extend([('独立源页复查',f"{s.get('checked_at','')} · HTTP {s.get('status','未取得')} · {s.get('title') or s.get('error','')}"),('复查边界',d['source_check_boundary'])])
   body+=section('失败原因',table(['项目','依据'],[('',cell(esc(k))+cell(esc(v))) for k,v in facts],metrics=[]))
  body+=section('参考与实际输出',f'<div class="compare"><div><h3>完整参考 / 目标结果</h3><p>{link("../references/"+c["id"]+".txt","下载参考")}</p><pre>{esc(ref)}</pre></div><div><h3>本次完整输出</h3><p>{link("../"+r["artifact_path"],"下载原始输出")}</p>'+('<p class="source-note">不可显示的控制字节以 \\xNN 展示；下载文件保留原始输出。</p>' if readable_output(output)!=output else '')+f'<pre>{esc(readable_output(output))}</pre></div></div>')
  body+=section('逐项判定',(table(['核对点','目标','判定','实际命中'],checks) if checks else '<p>本例按任务结构或原文件输出判定。</p>')+''.join(f'<p>{esc(x["id"])}：实际 {x["count"]}，要求至少 {x["minimum"]}。</p>' for x in m.get('pattern_results',[]))+f'<details><summary>接口返回的附加字段</summary><pre>{esc(json.dumps(r.get("provider_metadata",{}),ensure_ascii=False,indent=2))}</pre></details>')
  write(out,'attempts/'+r['attempt_id']+'.html',c['name']+' · '+r['provider'],body)
 controls=filter_controls([('provider','方法',[(n,n) for n in names]),('track','类型',list(TRACKS.items())),('result','结果',[('pass','任务通过'),('fail','任务失败')])]+failure_filters(records,cases))
 write(out,'analysis/index.html','结果分析',hero('结果分析','按方法、任务结果、失败环节和失败类别筛选；完整输出见案例详情。')+section('执行记录',controls+table(['方法']+headers,[(case_attrs(r,cases[r['case_id']]),cell(link('../providers/'+r['provider']+'.html',r['provider']))+attempt_row(r,cases[r['case_id']])[1]) for r in records],'filtered-table')))
 write_support(out,records,manifests)
 return out/'index.html'

def write_support(out,records,manifests):
 methodology=hero('评测方法','正文质量、资源获取与访问能力分开测，所有参评方法面对同一组任务。')
 examples_html=re.sub(r'<a href="../datasets/([^"#]+).html">([^<]*)</a>',lambda m:m[0] if (out/'datasets'/(m[1]+'.html')).exists() else m[2],examples())
 methodology+=section('每类怎么测',examples_html)+section('指标说明',metric_guide())
 methodology+=section('动态列表边界','<p>至少返回 3 个不同条目的名称和详情入口，再以独立页面或已保存快照核验前三条名称与详情身份。三条均确认才算任务通过；有链接但没有内容证据，标为“条目内容未获证实”，不是“内容错误”。名单、排序、价格、推荐位置与整页召回均不在本任务范围，不以过去名单约束现在的输出。</p>')
 methodology+=section('计算与边界','''<details><summary>全文评分公式</summary><p>完整度＝取回的参考词数 ÷ 参考词数；纯净度＝匹配的正文词数 ÷ 返回词数；F1＝2 × 完整度 × 纯净度 ÷ 两者之和。英文按词、中文按字，统一大小写与 Unicode 字符形式；Markdown 链接只计文字。对有完整参考的全部案例取平均；请求失败计 0，请求成功但任务未通过仍按实际正文评分，没有参考不计算。</p></details><details><summary>参考范围与公平性</summary><p>网页、文档使用独立保存的参考正文；仓库以 README 为准，列表只覆盖指定的已加载范围，不要求无限滚动或跨页翻页。登录页和验证码不能代替目标正文。参考正文自身包含的“404 Not Found”等术语不作为错误页判据。</p><p>不同方法并行，同一方法串行；正文结束后再测其他任务。Firecrawl 每次完成后冷却 6.1 秒。本报告不是 WCXB 官方榜单。</p><p>媒体网址只证明找到入口，不代表取得文件。文件任务没有另接下载器代替参评方法完成。</p></details>''')
 write(out,'methodology/index.html','评测方法',methodology)
 downloads=' · '.join(link('../'+path,label) for path,label in [('dataset.json','评测集与参考'),('results.jsonl','全部调用记录'),('summary.json','分类汇总'),('execution.json','运行参数')])
 write(out,'reproduce/index.html','复现',hero('数据与复现','使用冻结的任务、评分结果与原始输出生成报告。')+section('数据下载','<p>'+downloads+'</p>')+section('重建报告','<p>在评测仓库中执行；读取 evaluation/ 的当前实测数据，不调用供应商接口。</p><pre>python3 scripts/build_report.py</pre><p>输出目录：reports/webfetch/。重新实测使用 scripts/run_benchmark.py，正文配置为 configs/providers.example.json，资源配置为 configs/providers.resources.json。凭据仅从本地环境加载，不包含在报告中。</p>'))
 rows=[]
 for i,m in enumerate(manifests):
  for k,v in [('执行组',i+1),('开始（UTC）',m['started_at']),('完成（UTC）',m.get('completed_at')),('纳入调用数',m.get('attempt_count')),('Python',m['environment']['python']),('平台',m['environment']['platform']),('并发范围',m['parameters'].get('concurrency_scope')),('超时秒',m['parameters']['timeout'])]:rows.append(('',cell(k)+cell(esc(v))))
 errors=Counter((r['provider'],r.get('error_type') or 'unknown') for r in records if not r['ok'])
 errorrows=[('',cell(esc(n))+cell(esc(t))+cell(str(count))) for (n,t),count in sorted(errors.items())]
 write(out,'environment/index.html','环境',hero('执行环境','各组使用的运行环境、时间与请求参数。')+section('测试条件',f'<p>共纳入 {len(records):,} 次调用。任务范围与方法配置以冻结数据为准；不向参评方法提供网站登录凭据，不操作验证码。参考与调用的会话可能不同，因此动态任务只确认前三条名称和详情身份，不比较个性化名单。</p>')+section('运行参数',table(['参数','值'],rows))+section('请求失败统计',table(['方法','故障分类','次数'],errorrows)))
 write(out,'integration/index.html','集成',hero('按任务选择方法','分别看正文、文档、资源和文件任务的结果。')+section('使用边界',table(['任务','接入方式'],[('',cell(a)+cell(b)) for a,b in [('网页正文','结合任务通过率、完整度、F1 和通过耗时选择，保留原始输出及错误类型。'),('文档','确认方法实际返回文档文字；下载链接不等于文档正文。'),('图片、视频','资源地址、原文件和内容理解是不同任务；本轮不测多模态理解。'),('登录、验证码','交互操作另行授权和评测；登录页不能算作取得目标内容。')]])))
 accountrows=[('',cell(esc(a['provider']))+cell(link(a['registration_url'],'注册 / 登录')+'<br>'+link(a['key_url'],'获取凭据'))+cell('<br>'.join('<code>'+esc(x)+'</code>' for x in a['env_vars']))+cell(esc(a['steps']))+cell(esc(a['free_note']))) for a in ACCOUNT_REGISTRY]
 write(out,'accounts/index.html','账号',hero('账号与凭据','只列官方入口和环境变量名称，不保存实际凭据。')+section('注册清单',table(['提供方','官方入口','环境变量','取得方式','免费 / 准入说明'],accountrows,cls='accounts-table')))
 from .report_methods import render_methods
 write(out,'adoption/index.html','方法介绍',render_methods(out,records,hero=hero,section=section,table=table,cell=cell,link=link,esc=esc))
