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
PROVIDER_LABELS={'moli-1.0.6':'Moli 1.0.6','moli':'Moli 1.1.5','moli-1.1.3':'Moli 1.1.3','moli-1.1.7':'Moli 1.1.7','moli-pr643':'Moli 1.1.7 · PR #643'}


def format_wcxb_conditional_latency(value: float | None) -> str:
 return f'{value:,.0f} ms' if value is not None else '—'


def generate_wcxb(out: Path, rows: list[dict], summary: dict, ground_truth: dict,
                  identities: dict, profile: dict) -> None:
 """Render the current WCXB comparison from one certified semantic model."""
 from collections import Counter as WordCounter
 from urllib.parse import quote
 providers=list(summary['providers'])
 audit=summary['reference_audit']
 case_count=summary['case_count']
 quality_count=summary['quality_case_count']
 result_count=case_count*len(providers)
 visible_count=quality_count*len(providers)
 review_scope=json.loads((out/'research/wcxb-representative-review.json').read_text())
 reviewed_cases=review_scope['case_count']
 reviewed_results=review_scope['result_count']
 unreviewed_results=result_count-reviewed_results
 exclusion_ids={row['file_id']:row['reason'] for row in summary.get('exclusions',[])}
 missing_body_count=sum(reason=='reference_body_absent_from_frozen_html' for reason in exclusion_ids.values())
 missing_body_subject=(f'{missing_body_count} frozen body was' if missing_body_count==1 else f'{missing_body_count} frozen bodies were')
 source_repository=profile['source_repository'].rstrip('/')
 source_commit=profile['source_commit']
 by_case=defaultdict(dict)
 for row in rows:by_case[row['case_id']][row['provider']]=row
 if len(by_case)!=case_count or any(set(group)!=set(providers) for group in by_case.values()):
  raise ValueError('WCXB same-case matrix is incomplete')
 from .wcxb_latency import load_full_paired_latency, summarize_full_paired_latency
 _, selected_latency = load_full_paired_latency(out/'research/wcxb-full-paired-latency', profile, set(by_case))
 paired_latency = summarize_full_paired_latency(selected_latency, set(by_case))
 timing_rows=[json.loads(line) for line in (out/'research/wcxb-full-paired-latency/calls.jsonl').read_text().splitlines()]
 first_round={(row['case'],row['method']):row for row in timing_rows if row['round']==0}
 first_round_delta=sum(first_round[case,'pr']['elapsed_ms']-first_round[case,'release']['elapsed_ms']
                       for case in by_case)/case_count
 adoption_data=json.loads((Path(__file__).resolve().parents[2]/'research/provider-adoption.json').read_text())
 cloudflare_matches=[(entry['repo'], match['path']) for entry in adoption_data['included']
                     for match in entry['matches'] if match['signature']=='cloudflare-markdown']
 cloudflare_repo_count=len({repo for repo,_ in cloudflare_matches})
 cloudflare_file_count=len(set(cloudflare_matches))
 style='''*{box-sizing:border-box}body{margin:0;font:16px/1.6 system-ui,sans-serif;color:#172334;background:#f7f9fb}body.dark{color:#ecf1f6;background:#101923}a{color:#155bb2}body.dark a{color:#8bbdff}.wrap{max-width:1180px;margin:auto;padding:0 22px}header{background:#142d4b;color:white}header .wrap{display:flex;align-items:center;gap:22px;flex-wrap:wrap;padding-top:14px;padding-bottom:14px}header a{color:white;text-decoration:none}header a:hover{text-decoration:underline}header strong{margin-right:auto}nav{display:flex;gap:16px;flex-wrap:wrap}button,select,input{font:inherit}button,select,input{border:1px solid #a8b7c8;border-radius:7px;padding:7px 10px;background:white;color:#172334}button{cursor:pointer}.hero{padding:54px 0 34px}.hero h1{font-size:clamp(30px,4vw,48px);line-height:1.15;margin:0 0 12px}.hero p{max-width:900px;font-size:19px;color:#526376}body.dark .hero p,body.dark .muted{color:#b8c7d6}section{margin:24px auto 38px}h2{font-size:25px;margin:0 0 12px}h3{margin:8px 0}.panel{background:white;border:1px solid #dbe3eb;border-radius:13px;padding:22px;margin:16px 0}.panel code{overflow-wrap:anywhere;word-break:break-word}body.dark .panel{background:#1c2a39;border-color:#35465a}.scroll{overflow:auto;max-height:68vh;border:1px solid #dbe3eb;border-radius:10px;background:white}body.dark .scroll{background:#1c2a39;border-color:#35465a}table{border-collapse:collapse;width:100%;min-width:760px}th,td{padding:10px 13px;text-align:left;border-bottom:1px solid #dbe3eb;white-space:nowrap}th{position:sticky;top:0;background:#eaf0f7;color:#172334;z-index:1}body.dark th{background:#283c52;color:#fff}tr:hover{background:#edf5ff}body.dark tr:hover{background:#28405a}.filters{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}.filters label{display:grid;gap:4px;font-size:13px;font-weight:700}.number{font-variant-numeric:tabular-nums}.bad{color:#a92c2c;font-weight:700}.good{color:#147448;font-weight:700}body.dark .bad{color:#ff9a9a}body.dark .good{color:#86e0b0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0d1c2d;color:#eef5ff;padding:18px;border-radius:9px;max-height:60vh;overflow:auto;font:13px/1.55 ui-monospace,monospace}details{border:1px solid #dbe3eb;border-radius:9px;padding:12px 16px;margin:12px 0}body.dark details{border-color:#35465a}summary{cursor:pointer;font-weight:700}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.grid .panel{margin:0}.muted,footer{color:#526376}.small{font-size:13px}.tag{display:inline-block;padding:2px 8px;border-radius:99px;background:#eaf0f7;color:#314861}body.dark .tag{background:#344b63;color:white}.source{overflow-wrap:anywhere}footer{padding:35px 22px}body.dark button,body.dark select,body.dark input{background:#213348;color:white;border-color:#52677c}@media(max-width:640px){header .wrap{display:block}nav{margin:12px 0}.wrap{padding:0 16px}.panel{padding:15px}th,td{padding:8px 10px}}'''
 script='''function theme(){document.body.classList.toggle('dark');try{localStorage.setItem('wcxb-theme',document.body.classList.contains('dark')?'dark':'light')}catch(e){}}let savedTheme='light';try{savedTheme=localStorage.getItem('wcxb-theme')}catch(e){}if(savedTheme==='dark')document.addEventListener('DOMContentLoaded',()=>document.body.classList.add('dark'));function filter(){let q=(document.getElementById('q')?.value||'').toLowerCase(),p=document.getElementById('provider')?.value||'',t=document.getElementById('type')?.value||'',scope=document.getElementById('scope')?.value||'issues',n=0;document.querySelectorAll('tr[data-provider]').forEach(r=>{let show=(!q||r.dataset.search.includes(q))&&(!p||r.dataset.provider===p)&&(!t||r.dataset.type===t)&&(scope==='all'||(scope==='issues'?r.dataset.diagnosis!=='none':r.dataset.diagnosis===scope));r.hidden=!show;if(show)n++});let c=document.getElementById('count');if(c)c.textContent=n}const evidenceCache=new Map();function evidence(path){if(!evidenceCache.has(path))evidenceCache.set(path,fetch(path).then(r=>{if(!r.ok)throw Error(r.status);return r.json()}));return evidenceCache.get(path)}function loadOutput(d){let pre=d.querySelector('pre[data-src]');if(!pre||pre.dataset.loaded)return;pre.dataset.loaded='1';evidence(pre.dataset.src).then(data=>pre.dataset.provider?data.outputs[pre.dataset.provider]:data.source_html).then(value=>pre.textContent=value).catch(e=>pre.textContent='Unable to load evidence: '+e)}function showCase(){let s=document.getElementById('case-select'),body=document.getElementById('case-matrix');if(!s||!body)return;let d=window.WCXB_CASES?.[s.value];if(!d)return;let prefix=document.documentElement.lang==='en'?'en/':'';body.innerHTML=d.rows.map(r=>'<tr><td><a href="'+prefix+'cases/'+d.id+'.html#'+r.provider+'">'+r.provider+'</a></td><td>'+r.f1+'</td><td>'+r.precision+'</td><td>'+r.recall+'</td><td>'+r.diagnosis+'</td></tr>').join('')}document.addEventListener('DOMContentLoaded',()=>{document.querySelectorAll('.filters input,.filters select').forEach(x=>x.addEventListener('input',filter));document.querySelectorAll('details[data-output]').forEach(d=>d.addEventListener('toggle',()=>{if(d.open)loadOutput(d)}));document.getElementById('case-select')?.addEventListener('change',showCase);showCase();filter()})'''
 script=script[:script.index('function filter(){')]+script[script.index('const evidenceCache=new Map();'):]
 L={
  'zh':{'home':'首页','results':'结果分析','method':'评测方法','guide':'报告解读','repro':'环境与复现','theme':'切换主题','headline':'WCXB 主内容提取对比','core':'核心结果','provider':'浏览器','delivered':'生成非空内容','precision':'精确率','recall':'召回率','f1':'F1','latency':'平均端到端耗时','type':'页面类型','case':'网页','diagnosis':'定位','detail':'查看同页证据','all':'全部','issues':'待查问题','query':'搜索网页或错误','count':'条结果','reference':'人工主内容参考','output':'完整浏览器输出','error':'原始程序错误','missing':'参考中未匹配的高频词','extra':'输出中多出的高频词','summary':'同一网页的五种结果','note':'低精确率通常表示输出夹带较多参考外文字；低召回率表示参考词语大量缺失。这是定位线索，不能仅凭词袋分数确定程序故障。','scope':'统计口径','limit':'边界与已知标注问题','source':'冻结来源','version':'实测版本或服务身份','no_review':f'典型异常审查覆盖 {reviewed_cases} 页、{reviewed_results} 条输出；其余 {unreviewed_results:,} 条尚未逐项人工审查。定位分类由确定性规则产生，不等同于根因。'},
  'en':{'home':'Conclusions','results':'Results analysis','method':'Method','guide':'Interpretation','repro':'Reproduce','theme':'Theme','headline':'WCXB main-content extraction comparison','core':'Core results','provider':'Browser','delivered':'Nonempty outputs','precision':'Precision','recall':'Recall','f1':'F1','latency':'Mean end-to-end latency','type':'Page type','case':'Page','diagnosis':'Locator','detail':'Same-page evidence','all':'All','issues':'Issues to inspect','query':'Search page or error','count':'results','reference':'Human main-content reference','output':'Complete browser output','error':'Exact program error','missing':'Frequent unmatched reference words','extra':'Frequent extra output words','summary':'Five results for this page','note':'Low precision often means output contains much text outside the reference; low recall means much reference text is missing. These are locators, not proof of a program defect.','scope':'Population and calculation','limit':'Limits and annotation issues','source':'Frozen source','version':'Measured version or service identity','no_review':f'Targeted anomaly review covers {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} results have not been individually reviewed. Deterministic locators are not root causes.'}}
 L['zh'].update({'dataset':'评测集','review':'核验记录','adoption':'方法介绍','changes':'变更记录','accounts':'账号与安装'})
 L['en'].update({'dataset':'Dataset','review':'Verification','adoption':'Methods compared','changes':'Changes','accounts':'Accounts and setup'})
 L['zh']['headline']='WCXB 主内容提取：五款浏览器、六种配置'
 L['en']['headline']='WCXB main-content extraction: five browsers, six configurations'
 L['zh']['summary']='同一网页的全部方法结果'
 L['en']['summary']='All method results for this page'
 L['zh']['intro']=f'五款浏览器的 {len(providers)} 种配置处理同一批 {case_count} 页冻结网页；Moli 正式版与 PR 修复版分别计分。F1 比较提取词语与人工主内容的重合，不衡量 Markdown 结构或链接可用性。'
 L['en']['intro']=f'{len(providers)} configurations across five browsers processed the same {case_count} frozen pages; released and repaired Moli builds have separate results. F1 measures word overlap with human main content, not Markdown structure or link usability.'
 style+='''body{background:#f5f8fc}body.dark{background:#101923}header{background:#fff;color:#1b2c41;border-bottom:1px solid #dce5ee}body.dark header{background:#172534;color:#eaf3ff;border-color:#31475d}header a{color:#1156ae}body.dark header a{color:#92c3ff}header .wrap{max-width:1220px;padding-top:13px;padding-bottom:13px}header strong{font-family:ui-monospace,monospace;color:#1554a0}.wrap{max-width:1220px}.hero{padding:58px 0 46px}.hero h1{font-size:clamp(34px,4.5vw,56px);letter-spacing:-.035em}.hero p{font-size:17px}.eyebrow{font:700 12px ui-monospace,monospace;letter-spacing:.1em;color:#1457ac}.callout{padding:16px 20px;border-left:4px solid #21845b;background:#eaf8f2;border-radius:0 12px 12px 0;font-weight:650}.panel{box-shadow:0 8px 24px rgba(25,56,88,.04)}.lead-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.lead-grid .panel{margin:0}.lead-grid b{display:block;color:#1457ac;margin-bottom:7px}.section-lead{max-width:840px;color:#50657c}.winner{color:#117647;font-weight:800}body.dark .winner{color:#8fe3b5}.tabs{display:flex;gap:8px;flex-wrap:wrap}.tabs a{padding:7px 10px;border-radius:8px;background:#e9f0f8;text-decoration:none}.evidence-links{display:flex;gap:12px;flex-wrap:wrap}.reader-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}@media(max-width:700px){.lead-grid,.reader-grid{grid-template-columns:1fr}header nav{gap:8px 14px}header .wrap{display:block}header strong{display:block;margin-bottom:8px}.hero{padding:34px 0 24px}}'''
 style+='''.hero{position:relative;isolation:isolate;padding:70px 0 54px}.hero:before{content:"";position:absolute;left:50%;top:0;transform:translateX(-50%);height:100%;width:100vw;background:#f1f6ff;border-bottom:1px solid #dbe5f1;z-index:-1}body.dark .hero:before{background:#152638;border-color:#31465c}body.dark .callout{background:#163b31;color:#e5fff0}body.dark .lead-grid b{color:#9bc6ff}.panel p{overflow-wrap:anywhere}header button{border-color:#cbd9e8;background:#fff;color:#243a52}.scroll{scrollbar-gutter:stable}h2{letter-spacing:-.02em}.hero+.callout{margin-top:26px;margin-bottom:42px}@media(max-width:700px){.hero{padding:42px 0 30px}.hero+.callout{margin-top:18px}}'''
 style+='''.flow{overflow:auto;padding:14px;border:1px solid #dbe3eb;border-radius:12px;background:#fff}.flow .mermaid{max-height:none;min-width:920px;background:transparent;color:inherit}.flow-fallback{display:flex;align-items:center;gap:8px;min-width:820px}.flow-fallback .tag{border-radius:9px;padding:8px 12px;white-space:nowrap}@media(max-width:700px){.flow-fallback{min-width:0;display:grid;grid-template-columns:1fr}.flow-fallback .arrow{text-align:center;transform:rotate(90deg)}}'''
 style+='''.comparison-winner{color:#117647;font-weight:800}body.dark .comparison-winner{color:#8fe3b5}.comparison-winner:before{content:"★ ";font-size:.8em}.scroll table{min-width:880px}#wcxb-core th button{padding:0;border:0;background:transparent;color:inherit;font-weight:750;white-space:nowrap;text-align:left}#wcxb-core th button:hover{text-decoration:underline}.result-scroll{max-height:65vh}.result-scroll td{max-width:260px;overflow:hidden;text-overflow:ellipsis}.result-scroll tr:focus-within{background:#e9f3ff}.summary-band{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}.summary-band span{background:#e9f1fb;border-radius:8px;padding:7px 11px}body.dark .summary-band span{background:#25405c}.comparison-controls{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:14px}.comparison-controls label{display:grid;gap:4px;font-weight:650;font-size:13px}.choice-list{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.choice-list label{display:inline-flex;align-items:center;gap:5px;border:1px solid #c8d7e7;border-radius:99px;padding:5px 10px}.choice-list input{margin:0}.comparison-result{margin-top:12px}.comparison-result td{white-space:normal}dialog{border:1px solid #aec3dc;border-radius:14px;padding:0;max-width:min(820px,94vw);width:100%;max-height:88vh;background:#fff;color:#172334}dialog::backdrop{background:rgba(9,25,45,.55)}dialog .dialog-inner{padding:22px;overflow:auto;max-height:88vh}body.dark dialog{background:#1c2a39;color:#eef5ff}dialog .dialog-close{float:right}body.dark .summary-band span{color:#eef5ff}@media(max-width:700px){.comparison-controls{grid-template-columns:1fr}.result-scroll{max-height:440px}.summary-band{display:block}.summary-band span{display:block;margin:7px 0}}'''
 style+='''.essay-hero{max-width:920px}.essay-hero .eyebrow{font-size:13px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#376a9d}.essay-toc{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 32px;padding:12px 0;border-top:1px solid #dbe3eb;border-bottom:1px solid #dbe3eb}.essay-toc a{padding:4px 9px;border-radius:6px;background:#eaf0f7;text-decoration:none}.essay-toc a:hover{text-decoration:underline}body.dark .essay-toc a{background:#25405c}.essay-table{max-height:none}.essay-table table{min-width:680px}.essay-specimen .specimen-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}.specimen-grid>div{border:1px solid #dbe3eb;background:#f5f8fc;border-radius:9px;padding:14px;min-width:0}body.dark .specimen-grid>div{background:#24384e;border-color:#35465a}.specimen-grid p{margin:7px 0 0}.essay-conclusion{border-left:5px solid #147448}@media(max-width:700px){.essay-specimen .specimen-grid{grid-template-columns:1fr}.essay-toc{gap:5px}}'''
 style+='''.repair-story{display:grid;gap:18px}.repair-case{border:1px solid #dbe3eb;border-radius:12px;padding:18px;background:#fff}.repair-case h3{margin:0 0 6px}.repair-case figure{margin:14px 0}.repair-case figure img{display:block;width:100%;height:auto;border:1px solid #dbe3eb;border-radius:10px;background:#fff}.repair-case figcaption{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:8px;font-size:13px;font-weight:700;color:#344b63;text-align:center}.repair-case figcaption span{padding:5px 7px;border-radius:6px;background:#edf3f9}.repair-case figcaption span:nth-child(2){background:#fff0ed;color:#892f27}.repair-case figcaption span:nth-child(3){background:#eaf7ef;color:#216342}.repair-case .repair-fact{margin:7px 0;line-height:1.55}.repair-case .repair-evidence{font-size:14px;margin:9px 0 0}body.dark .repair-case{background:#1c2a39;border-color:#35465a}body.dark .repair-case figcaption{color:#e7edf4}body.dark .repair-case figcaption span{background:#31485e}body.dark .repair-case figcaption span:nth-child(2){background:#53332f;color:#ffc9c2}body.dark .repair-case figcaption span:nth-child(3){background:#274b3c;color:#b4edcc}@media(max-width:700px){.repair-case{padding:12px}.repair-case figure{overflow-x:auto}.repair-case figure img{width:max(760px,100%)}.repair-case figcaption{width:max(760px,100%);font-size:12px}}'''
 style+='''.repair-samples{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0}.repair-samples>div{min-width:0;border:1px solid #d6e3f0;border-radius:8px;padding:9px;background:#f4f8fc}.repair-samples b{font-size:13px}.repair-samples pre{margin:6px 0 0;padding:10px;min-height:115px;font-size:12px;max-height:none}.repair-mechanism{margin:12px 0 0}.repair-case figure img{max-height:390px;object-fit:contain}@media(max-width:780px){.repair-samples{grid-template-columns:1fr}.repair-samples pre{min-height:0}.repair-case figure img{max-height:none}}body.dark .repair-samples>div{background:#24384e;border-color:#47617b}'''
 style+='''.repair-case figure img{height:340px;max-height:none;object-fit:cover}@media(max-width:700px){.repair-case figure{overflow:visible}.repair-case figure img{height:auto;width:100%;object-fit:contain}.repair-case figcaption{width:100%;font-size:11px}}'''
 script+=''';function renderFlow(){let node=document.querySelector('.flow .mermaid');if(!node||window.innerWidth<=700)return;let loader=document.createElement('script');loader.src='https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js';loader.integrity='sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I';loader.crossOrigin='anonymous';loader.onload=async()=>{try{mermaid.initialize({startOnLoad:false,theme:document.body.classList.contains('dark')?'dark':'neutral'});let result=await mermaid.render('wcxb-flow',node.textContent);node.innerHTML=result.svg;node.hidden=false;node.parentElement.querySelector('.flow-fallback').hidden=true}catch(e){}};document.head.appendChild(loader)}document.addEventListener('DOMContentLoaded',renderFlow);'''
 script+='''function filter(){let q=(document.getElementById('q')?.value||'').toLowerCase(),p=document.getElementById('provider')?.value||'',t=document.getElementById('type')?.value||'',scope=document.getElementById('scope')?.value||'fail',diagnosis=document.getElementById('diagnosis')?.value||'',n=0;document.querySelectorAll('tr[data-provider]').forEach(r=>{let show=(!q||r.dataset.search.includes(q))&&(!p||r.dataset.provider===p)&&(!t||r.dataset.type===t)&&(scope==='all'||r.dataset.result===scope)&&(!diagnosis||(diagnosis==='issues'?r.dataset.diagnosis!=='none':r.dataset.diagnosis===diagnosis));r.hidden=!show;if(show)n++});let c=document.getElementById('count');if(c)c.textContent=n}'''
 script+=''';function compare(){let root=document.getElementById('comparison-rows');if(!root)return;let baseline=document.getElementById('compare-baseline').value,selected=[...document.querySelectorAll('[name="compare-provider"]:checked')].map(x=>x.value).filter(p=>p!==baseline),q=document.getElementById('compare-query').value.trim().toLowerCase(),kind=document.getElementById('compare-type').value,relation=document.getElementById('compare-relation').value,records=window.WCXB_COMPARE||{},shown=0;document.querySelectorAll('[name="compare-provider"]').forEach(x=>{x.disabled=x.value===baseline});root.replaceChildren();for(let [id,d] of Object.entries(records)){if(q&&!((id+' '+d.url).toLowerCase().includes(q))||kind&&kind!==d.type)continue;let compared=selected.map(p=>{let b=d.rows[baseline],r=d.rows[p],delta=r.f1-b.f1;return {p,delta,relation:delta>.0000001?'higher':delta<-.0000001?'lower':'equal'}});if(relation&&!compared.some(r=>r.relation===relation))continue;let tr=document.createElement('tr'),page=document.createElement('td'),a=document.createElement('a');a.href=(document.documentElement.lang==='en'?'en/':'')+'cases/'+encodeURIComponent(id)+'.html';a.textContent=id;page.append(a);tr.append(page);let type=document.createElement('td');type.textContent=d.label;tr.append(type);let base=document.createElement('td');base.textContent=(d.rows[baseline].f1*100).toFixed(1)+'%';tr.append(base);for(let item of compared){let td=document.createElement('td'),link=document.createElement('a');link.href=a.href+'#'+encodeURIComponent(item.p);link.textContent=item.p+': '+(item.delta>=0?'+':'')+(item.delta*100).toFixed(1)+' pp';td.append(link);tr.append(td)}root.append(tr);shown++}document.getElementById('compare-count').textContent=shown;document.getElementById('compare-head').innerHTML='<th>Page</th><th>'+((document.documentElement.lang==='zh')?'类型':'Type')+'</th><th>'+baseline+' F1</th>'+selected.map(p=>'<th>'+p+' ΔF1</th>').join('')}let diagnosisFocus=null;function openDiagnosis(button){let dialog=document.getElementById('diagnosis-dialog');if(!dialog)return;diagnosisFocus=button;let r=button.closest('tr');dialog.querySelector('[data-diagnosis-title]').textContent=r.dataset.provider+' · '+r.dataset.case;dialog.querySelector('[data-diagnosis-stage]').textContent=r.dataset.diagnosisLabel;dialog.querySelector('[data-diagnosis-error]').textContent=r.dataset.error||((document.documentElement.lang==='zh')?'无程序错误；此条由质量阈值定位。':'No program error; this result is located by a quality threshold.');let link=dialog.querySelector('[data-diagnosis-link]');link.href=r.querySelector('a').href;dialog.showModal();dialog.querySelector('[data-call-log-close]').focus()}function closeDiagnosis(){document.getElementById('diagnosis-dialog')?.close();diagnosisFocus?.focus()}document.addEventListener('DOMContentLoaded',()=>{let type=document.getElementById('home-type');type?.addEventListener('change',()=>document.querySelectorAll('[data-page-type]').forEach(r=>r.hidden=!!type.value&&r.dataset.pageType!==type.value));document.querySelectorAll('#compare-baseline,#compare-query,#compare-type,#compare-relation,[name="compare-provider"]').forEach(n=>n.addEventListener('input',compare));document.querySelectorAll('[data-diagnosis-open]').forEach(n=>n.addEventListener('click',()=>openDiagnosis(n)));document.querySelector('[data-call-log-close]')?.addEventListener('click',closeDiagnosis);document.getElementById('diagnosis-dialog')?.addEventListener('close',()=>diagnosisFocus?.focus());compare()});'''
 script=script.replace("if(c)c.textContent=n}","if(c)c.textContent=n;let empty=document.getElementById('empty-results');if(empty)empty.hidden=n!==0}")
 script=script.replace("+(item.delta*100).toFixed(1)+' pp'","+(item.delta*100).toFixed(1)+(document.documentElement.lang==='zh'?' 个百分点':' pp')")
 script=script.replace("innerHTML='<th>Page</th><th>'","innerHTML='<th>'+((document.documentElement.lang==='zh')?'网页':'Page')+'</th><th>'")
 script=script.replace("+p+' ΔF1</th>'","+p+((document.documentElement.lang==='zh')?' F1 差值</th>':' ΔF1</th>')")
 script+=''';function sortCore(button){let table=document.getElementById('wcxb-core'),body=table.tBodies[0],column=Number(button.dataset.coreSort),direction=button.dataset.nextDirection||((column>=5)?'asc':'desc');let rows=[...body.rows];rows.sort((a,b)=>{let cellA=a.cells[column],cellB=b.cells[column],valueA=Number(cellA.dataset.sort??cellA.textContent.split('/')[0]),valueB=Number(cellB.dataset.sort??cellB.textContent.split('/')[0]);return (direction==='asc'?1:-1)*(valueA-valueB)});body.replaceChildren(...rows);table.querySelectorAll('th').forEach(th=>th.removeAttribute('aria-sort'));button.closest('th').setAttribute('aria-sort',direction==='asc'?'ascending':'descending');button.dataset.nextDirection=direction==='asc'?'desc':'asc'}document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('[data-core-sort]').forEach(button=>button.addEventListener('click',()=>sortCore(button))));'''
 script+=''';function filterDataset(){let q=(document.getElementById('dataset-query')?.value||'').trim().toLowerCase(),type=document.getElementById('dataset-type')?.value||'',count=0;document.querySelectorAll('#wcxb-dataset tbody tr').forEach(row=>{let show=(!q||row.dataset.datasetCase.includes(q))&&(!type||row.dataset.pageType===type);row.hidden=!show;if(show)count++});let output=document.getElementById('dataset-count');if(output)output.textContent=count}document.addEventListener('DOMContentLoaded',()=>{document.getElementById('dataset-query')?.addEventListener('input',filterDataset);document.getElementById('dataset-type')?.addEventListener('change',filterDataset)});'''
 style+='''.frozen-preview{display:block;width:100%;height:65vh;min-height:360px;border:1px solid #cbd9e8;border-radius:10px;background:#fff}.result-scroll:has(#wcxb-dataset){max-height:70vh}'''
 (out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(style,encoding='utf-8')
 (out/'assets/report.js').write_text(script,encoding='utf-8')
 def href(lang,path):return ('en/' if lang=='en' else '')+path
 def page(lang,path,title,body):
  x=L[lang]
  route=('en/' if lang=='en' else '')+path
  root_prefix='../'*route.count('/')
  nav=''.join(f'<a href="{href(lang,p)}">{esc(x[k])}</a>' for k,p in [('home','index.html'),('dataset','datasets/index.html'),('method','methodology/index.html'),('results','analysis/index.html'),('review','review/index.html'),('guide','guide/index.html'),('adoption','adoption/index.html'),('repro','reproduce/index.html'),('changes','changes/index.html'),('accounts','accounts/index.html')])
  other=('en/' if lang=='zh' else '')+path
  head=f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base href="{root_prefix or "./"}"><title>{esc(title)} · WCXB</title><link rel="stylesheet" href="assets/style.css"><script defer src="assets/report.js"></script></head><body><header><div class="wrap"><strong><a href="{href(lang,"index.html")}">Web Fetch</a></strong><nav>{nav}</nav><a href="{other}">{"English" if lang=="zh" else "中文"}</a><button onclick="theme()">{esc(x["theme"])}</button></div></header><main class="wrap">{body}</main><footer class="wrap">WCXB v1.0 · {case_count} {"页评测样本" if lang=="zh" else "evaluated test pages"} · CC BY 4.0 · <a href="{source_repository}/tree/{source_commit}">{esc(x["source"])}</a></footer></body></html>'
  target=out/('en' if lang=='en' else '')/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(head,encoding='utf-8')
 def fmt(v,digits=1):return f'{v*100:.{digits}f}%'
 diag_labels={
  'zh':{'none':'未触发定位','request_error':'请求失败','empty_output':'输出为空','error_page':'错误页内容','empty_reference':'标准答案为空','excluded':'参考不合格','low_precision_and_recall':'精确率与召回率均低','low_precision':'精确率低','low_recall':'召回率低'},
  'en':{'none':'No locator triggered','request_error':'Request error','empty_output':'Empty output','error_page':'Error page','empty_reference':'Empty reference','excluded':'Reference excluded','low_precision_and_recall':'Low precision and recall','low_precision':'Low precision','low_recall':'Low recall'},
 }
 def diag(lang,value):return diag_labels[lang].get(value,value.replace('_',' '))
 type_labels={'article':'文章','service':'服务','product':'商品','collection':'合集','forum':'论坛','listing':'列表','documentation':'文档'}
 def type_label(lang,value):return type_labels.get(value,value) if lang=='zh' else value
 def browser_label(provider):
  if provider=='moli-pr643':return 'Moli PR #643 · 384d1d2a'
  if provider=='moli':return 'Moli 1.1.8'
  version=identities[provider].get('reported_version') or ''
  match=re.search(r'\d+(?:\.\d+)+',version)
  number=(match.group(0).split('.')[0] if provider=='chromium' else match.group(0)) if match else ''
  return provider.capitalize()+(' '+number if number else '')
 def comparison_cell(value, metric, direction, winning, display):
  marker=f'<strong class="comparison-winner">{display}</strong>' if winning else display
  return f'<td data-metric="{metric}" data-metric-direction="{direction}" data-sort="{value}">{marker}</td>'
 def unique_winner(values, direction):
  extreme=(min if direction=='min' else max)(values.values())
  return next((name for name,value in values.items() if value==extreme),None) if list(values.values()).count(extreme)==1 else None
 def score_digits(values, direction='max'):
  winner=unique_winner(values,direction)
  if winner is None:return 1
  for digits in (1,2,3,4):
   rounded={name:round(value*100,digits) for name,value in values.items()}
   if unique_winner(rounded,direction)==winner:return digits
  return 4
 for lang in ('zh','en'):
  x=L[lang]
  best=max(providers,key=lambda p:summary['providers'][p]['quality_f1']);fast=min(providers,key=lambda p:summary['providers'][p]['mean_latency_ms'])
  metrics=(('quality_precision','max','precision'),('quality_recall','max','recall'),('quality_f1','max','f1'),('mean_latency_ms','min','mean'),('p50_latency_ms','min','p50'),('p95_latency_ms','min','p95'))
  winners={key:unique_winner({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in metrics}
  metric_digits={key:score_digits({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in metrics if direction=='max'}
  if 'moli' in providers and 'moli-pr643' in providers:
   metric_digits['quality_f1']=4
  core_rows=[]
  for p in sorted(providers,key=lambda name:summary['providers'][name]['quality_f1'],reverse=True):
   s=summary['providers'][p]
   cells=''.join(comparison_cell(s[key],label,direction,p==winners[key],fmt(s[key],metric_digits[key]) if direction=='max' else f'{s[key]:,.0f} ms') for key,direction,label in metrics)
   core_rows.append(f'<tr><td><a href="{href(lang,"providers/"+p+".html")}">{esc(browser_label(p))}</a></td><td>{s["delivered"]}/{case_count}</td>{cells}</tr>')
  core_headers=[x['provider'],x['delivered'],x['precision'],x['recall'],f'F1 ({quality_count})',('平均耗时' if lang=='zh' else 'Mean latency'),('P50 耗时' if lang=='zh' else 'P50 latency'),('P95 耗时' if lang=='zh' else 'P95 latency')]
  core_header_html=[]
  for index,label in enumerate(core_headers):
   sort_attr=' aria-sort="descending"' if index==4 else ''
   next_direction=' data-next-direction="asc"' if index==4 else ''
   core_header_html.append(f'<th{sort_attr}><button type="button" data-core-sort="{index}"{next_direction}>{esc(label)} ↕</button></th>' if index else f'<th>{esc(label)}</th>')
  core='<div class="scroll"><table id="wcxb-core"><thead><tr>'+''.join(core_header_html)+'</tr></thead><tbody>'+''.join(core_rows)+'</tbody></table></div>'
  moli_before=summary['providers'].get('moli')
  moli_after=summary['providers'].get('moli-pr643')
  if moli_before and moli_after:
   page_deltas=[by_case[c]['moli-pr643']['f1']-by_case[c]['moli']['f1'] for c in by_case if c not in exclusion_ids]
   up=sum(delta>1e-10 for delta in page_deltas);down=sum(delta< -1e-10 for delta in page_deltas);same=quality_count-up-down
   precision_delta=(moli_after['quality_precision']-moli_before['quality_precision'])*100
   recall_delta=(moli_after['quality_recall']-moli_before['quality_recall'])*100
   f1_delta=(moli_after['quality_f1']-moli_before['quality_f1'])*100
   paired=(f'<section id="moli-change" class="panel"><h2>Moli 修复版改善了什么</h2><p>PR #643 修复代码内容粘连，并防止可选清理删掉商品详情。{quality_count} 页中，5412、5413 的逐页 F1 上升，5411、5458 下降，其余 {same} 页不变。召回均值提高 {recall_delta:+.4f} 个百分点，精确率均值变化 {precision_delta:+.4f} 个百分点，F1 变化 {f1_delta:+.4f} 个百分点。F1 是逐页 F1 的平均，并非用首页平均精确率和召回率重新计算；两项降幅略大，故召回提高而平均 F1 略降。5458 的内联 HTML 标签名还会进入精确率分母。</p><p><a href="{href(lang,"cases/5458.html")}">查看代码内容对照</a> · <a href="{href(lang,"guide/index.html")}">查看逐页原因</a></p></section>' if lang=='zh' else f'<section id="moli-change" class="panel"><h2>What the Moli fix improves</h2><p>PR #643 separates joined code values and keeps product details when optional UI stripping is used. Per-page F1 rises on 5412 and 5413, falls on 5411 and 5458, and is unchanged on the other {same} pages. Mean recall changes {recall_delta:+.4f} percentage points, mean precision {precision_delta:+.4f}, and mean F1 {f1_delta:+.4f}. F1 is the mean of the {quality_count} page-level F1 values, not a recomputation from the headline mean precision and recall. The two decreases are slightly larger, so mean recall rises while average F1 falls slightly; on 5458, raw scoring also counts the inline HTML tag names in the precision denominator.</p><p><a href="{href(lang,"cases/5458.html")}">Compare the code output</a> · <a href="{href(lang,"guide/index.html")}">Read the page-level analysis</a></p></section>')
  else:paired=''
  latency_context=(f'<p id="moli-latency-context">Moli 1.1.8 与实测 PR 在同一批 {case_count} 页做交叉顺序复测；主表取预定最终轮：稳定版 {moli_before["mean_latency_ms"]:,.0f} ms，PR 版 {moli_after["mean_latency_ms"]:,.0f} ms，逐页平均差 {paired_latency["paired_delta_mean_ms"]:+.1f} ms。两版源码基线不同，耗时差不能全归因于本 PR。<a href="{href(lang,"guide/index.html")}">查看测速依据</a></p>' if lang=='zh' else f'<p id="moli-latency-context">Moli 1.1.8 and the measured PR were measured in a balanced same-window crossover on all {case_count} pages. The preselected final round gives {moli_before["mean_latency_ms"]:,.0f} ms for the release and {moli_after["mean_latency_ms"]:,.0f} ms for the PR, a {paired_latency["paired_delta_mean_ms"]:+.1f} ms page-paired difference. The codebases diverge, so this difference cannot all be attributed to this PR. <a href="{href(lang,"guide/index.html")}">Read the timing basis</a></p>')
  lead=(f'{browser_label(best)} 在 {quality_count} 页有效参考上的平均 F1 最高（{fmt(summary["providers"][best]["quality_f1"])}）；{browser_label(fast)} 的 {case_count} 次调用平均端到端耗时最短（{summary["providers"][fast]["mean_latency_ms"]:,.0f} ms）。' if lang=='zh' else f'{browser_label(best)} leads mean F1 across {quality_count} qualified pages ({fmt(summary["providers"][best]["quality_f1"])}); {browser_label(fast)} has the shortest mean end-to-end latency across {case_count} calls ({summary["providers"][fast]["mean_latency_ms"]:,.0f} ms).')
  type_counts=WordCounter(gt['page_type'] for c,gt in ground_truth.items() if c not in exclusion_ids)
  slice_values={t:{p:sum(by_case[c][p]['f1'] for c,g in ground_truth.items() if g['page_type']==t and c not in exclusion_ids)/type_counts[t] for p in providers} for t in type_counts}
  slices=''.join(f'<tr data-page-type="{esc(t)}"><td>{esc(type_label(lang,t))}</td><td>{type_counts[t]}</td>'+''.join(comparison_cell(slice_values[t][p],f'type-{t}','max',p==unique_winner(slice_values[t],'max'),fmt(slice_values[t][p],score_digits(slice_values[t]))) for p in providers)+'</tr>' for t in sorted(type_counts))
  slice_table='<div class="filters"><label>'+esc(x['type'])+'<select id="home-type"><option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in sorted(type_counts))+'</select></label></div><div class="scroll"><table id="wcxb-types"><thead><tr><th>'+esc(x['type'])+'</th><th>'+('网页数' if lang=='zh' else 'Pages')+'</th>'+''.join(f'<th>{esc(browser_label(p))} F1</th>' for p in providers)+'</tr></thead><tbody>'+slices+'</tbody></table></div>'
  cards=([('输入：同一批冻结网页',f'质量比较使用 {quality_count} 页有效参考；不访问实时源站。'),('目标：人工主内容','正文与参考文本重合；导航、广告和推荐文字会拉低精确率。'),('分数：逐页词袋 F1','逐页等权平均；请求失败或空输出按实际结果计分。')] if lang=='zh' else [('Input: frozen pages',f'Quality compares the same {quality_count} qualified pages; no live-origin visits.'),('Target: human main content','Surplus navigation, ads and recommendations reduce precision.'),('Score: per-page word F1','Pages have equal weight; request failures and empty outputs retain their measured scores.')])
  card_html=''.join(f'<div class="panel"><b>{esc(title)}</b><span>{esc(body)}</span></div>' for title,body in cards)
  home=f'<div class="hero"><span class="eyebrow">Web Fetch · WCXB v1.0</span><h1>{esc(x["headline"])}</h1><p>{esc(x["intro"])}</p></div><section><h2>{esc(x["core"])}</h2><p>{esc(lead)}</p><p class="small muted">'+((f'绿色星标是该列唯一最优值。质量和三项耗时均以同一批 {case_count} 页为分母，耗时包含失败重试。P50 是中位数，P95 是 95% 调用不超过的耗时。不同方法的批次跨日期；Moli 两版耗时已用同窗交叉实测纠正。') if lang=='zh' else f'Green stars mark a unique column best. Quality and all three latency measures use the same {case_count} pages; latency includes failed retries. P50 is the median and P95 is the time within which 95% of calls complete. Other methods ran on different dates; the two Moli timings use the corrected same-window crossover.')+f'</p>{core}{latency_context}</section>'+paired+f'<section><h2>{"不同网页类型" if lang=="zh" else "By page type"}</h2><p>{"筛选页面类型；每格是该类型的逐页平均 F1，绿色星标是该行唯一最高值。" if lang=="zh" else "Filter by page type. Each cell is mean page F1; a green star marks the unique row leader."}</p>{slice_table}</section><section class="lead-grid"><div class="panel"><h3>{esc(x["dataset"])}</h3><p>{"查看冻结网页、来源和人工参考。" if lang=="zh" else "Inspect frozen pages, source URLs and references."}</p><a href="{href(lang,"datasets/index.html")}">{esc(x["dataset"])} →</a></div><div class="panel"><h3>{esc(x["results"])}</h3><p>{"筛选逐项结果，查看响应和定位证据。" if lang=="zh" else "Filter individual results and inspect responses and evidence."}</p><a href="{href(lang,"analysis/index.html")}">{esc(x["results"])} →</a></div><div class="panel"><h3>{esc(x["guide"])}</h3><p>{"读懂结果差异与评分边界。" if lang=="zh" else "Understand differences and scoring limits."}</p><a href="{href(lang,"guide/index.html")}">{esc(x["guide"])} →</a></div></section>'
  success_rows=''.join(f'<tr><td>{esc(p)}</td><td>{summary["providers"][p]["success_latency_count"]}/{case_count}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_mean_latency_ms"])}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_p50_latency_ms"])}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_p95_latency_ms"])}</td></tr>' for p in providers)
  if any(summary['providers'][p]['success_latency_count'] != case_count for p in providers):
   home+='<section><h2>'+('仅成功交付的条件耗时' if lang=='zh' else 'Conditional latency for delivered content')+'</h2><p>'+('这张表只计算最终交付非空正文的页面，分母随方法变化；每次失败重试的耗时仍计入该页。完整共同分母的同题耗时见首页核心表。' if lang=='zh' else 'Only pages with a final nonempty response enter this table, so denominators differ by method; failed retry time remains part of that page. The core table above retains the full common-denominator latency.')+'</p><div class="scroll"><table><thead><tr><th>'+esc(x['provider'])+'</th><th>'+('成功页数' if lang=='zh' else 'Delivered pages')+'</th><th>Mean</th><th>P50</th><th>P95</th></tr></thead><tbody>'+success_rows+'</tbody></table></div></section>'
  issue_counts={(p,d):sum(r['provider']==p and r['quality_eligible'] and r['diagnosis']==d for r in rows) for p in providers for d in ('request_error','empty_output','error_page','low_precision_and_recall','low_precision','low_recall')}
  issue_types=[d for d in ('request_error','empty_output','error_page','low_precision_and_recall','low_precision','low_recall') if any(issue_counts[p,d] for p in providers)]
  issue_rows=''.join('<tr><td>'+esc(p)+'</td>'+''.join(f'<td>{issue_counts[p,d]}</td>' for d in issue_types)+'</tr>' for p in providers)
  issue_table='<div class="scroll"><table><thead><tr><th>'+esc(x['provider'])+'</th>'+''.join('<th>'+esc(diag(lang,d))+'</th>' for d in issue_types)+'</tr></thead><tbody>'+issue_rows+'</tbody></table></div>'
  home+='<section><h2>'+('问题分布' if lang=='zh' else 'Issue distribution')+'</h2><p>'+((f'每格是该浏览器 {quality_count} 页有效参考中的程序定位次数；选择一种定位可在结果分析中查看对应网页。') if lang=='zh' else f'Programmatic locators across {quality_count} qualified pages per browser; filter the results analysis to inspect corresponding pages.')+'</p>'+issue_table+'</section>'
  page(lang,'index.html',x['headline'],home)
  opts=lambda values:'<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in values)
  types=sorted({g['page_type'] for g in ground_truth.values()})
  type_options='<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in types)
  filters=f'<div class="filters"><label>{esc(x["query"])}<input id="q" type="search"></label><label>{esc(x["provider"])}<select id="provider">{opts(providers)}</select></label><label>{esc(x["type"])}<select id="type">{type_options}</select></label><label>{"诊断结果" if lang=="zh" else "Diagnostic result"}<select id="scope"><option value="fail">{"待查" if lang=="zh" else "Inspect"}</option><option value="pass">{"未触发" if lang=="zh" else "No locator"}</option><option value="all">{esc(x["all"])}</option></select></label><label>{esc(x["diagnosis"])}<select id="diagnosis"><option value="">{esc(x["all"])}</option><option value="issues">{esc(x["issues"])}</option>'+''.join(f'<option value="{esc(v)}">{esc(diag(lang,v))}</option>' for v in sorted({r['diagnosis'] for r in rows if r['quality_eligible'] and r['diagnosis']!='none'}))+'</select></label></div>'
  inventory=[]
  for r in rows:
   if not r['quality_eligible']:continue
   case=r['case_id'];linkto=f'cases/{case}.html#{r["provider"]}'
   if lang=='en':linkto='en/'+linkto
   label=r['diagnosis']
   delivery='pass' if label=='none' else 'fail'
   error_text=(r['error_type'] or '')+((': '+r['error_message']) if r['error_message'] else '')
   inventory.append(f'<tr data-case="{esc(case)}" data-provider="{esc(r["provider"])}" data-type="{esc(r["page_type"])}" data-result="{delivery}" data-diagnosis="{esc(label)}" data-diagnosis-label="{esc(diag(lang,label))}" data-error="{esc(error_text)}" data-search="{esc((case+" "+r["original_url"]+" "+str(r["error_message"] or "")).lower())}"><td><a href="{linkto}">{esc(case)}</a></td><td>{esc(type_label(lang,r["page_type"]))}</td><td>{esc(r["provider"])}</td><td class="number">{fmt(r["f1"])}</td><td class="number">{fmt(r["precision"])}</td><td class="number">{fmt(r["recall"])}</td><td>{esc(diag(lang,label))}</td><td class="number">{r["total_elapsed_ms"]:,.0f} ms</td><td><button type="button" data-diagnosis-open>{"查看原因" if lang=="zh" else "Inspect"}</button></td></tr>')
  comparison_data={case:{'type':ground_truth[case]['page_type'],'label':type_label(lang,ground_truth[case]['page_type']),'url':by_case[case][providers[0]]['original_url'],'rows':{p:{'f1':by_case[case][p]['f1']} for p in providers}} for case in sorted(by_case) if case not in exclusion_ids}
  comparison_json=json.dumps(comparison_data,ensure_ascii=True).replace('<','\\u003c')
  comparison_options=''.join(f'<option value="{esc(p)}">{esc(p)}</option>' for p in providers)
  comparison_types='<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in types)
  comparison_choices=''.join(f'<label><input type="checkbox" name="compare-provider" value="{esc(p)}"{" checked" if p==providers[1] else ""}> {esc(p)}</label>' for p in providers)
  explorer=f'<section class="panel"><h2>{"同页方法组合比较" if lang=="zh" else "Same-page method comparison"}</h2><p>{"以一款浏览器为基准，多选其他浏览器；按 F1 差值筛选有效参考。正值表示对比浏览器的 F1 较高，单位为百分点。" if lang=="zh" else "Choose a baseline and comparison browsers, then filter qualified pages by F1 difference. A positive F1 percentage-point difference means the comparison browser scored higher."}</p><div class="comparison-controls"><label>{"基准浏览器" if lang=="zh" else "Baseline browser"}<select id="compare-baseline">{comparison_options}</select></label><label>{esc(x["type"])}<select id="compare-type">{comparison_types}</select></label><label>{"结果关系" if lang=="zh" else "Outcome relation"}<select id="compare-relation"><option value="">{esc(x["all"])}</option><option value="higher">{"对比方法较高" if lang=="zh" else "Comparison higher"}</option><option value="lower">{"对比方法较低" if lang=="zh" else "Comparison lower"}</option><option value="equal">{"相同" if lang=="zh" else "Equal"}</option></select></label></div><div class="choice-list">{comparison_choices}</div><label>{"搜索网页 ID 或网址" if lang=="zh" else "Search page ID or URL"} <input id="compare-query" type="search"></label><p class="summary-band"><span><b id="compare-count"></b> / {quality_count} {"页" if lang=="zh" else "pages"}</span></p><div class="scroll result-scroll comparison-result"><table><thead><tr id="compare-head"></tr></thead><tbody id="comparison-rows"></tbody></table></div><script>window.WCXB_COMPARE={comparison_json}</script></section>'
  dialog=f'<dialog id="diagnosis-dialog" aria-labelledby="diagnosis-title"><div class="dialog-inner"><button type="button" class="dialog-close" data-call-log-close aria-label="{"关闭" if lang=="zh" else "Close"}">×</button><h2 id="diagnosis-title" data-diagnosis-title></h2><p><b>{"定位类别" if lang=="zh" else "Locator"}:</b> <span data-diagnosis-stage></span></p><p><b>{"程序错误" if lang=="zh" else "Program error"}:</b> <span data-diagnosis-error></span></p><p>{"此处显示程序定位，未独立复核根因。完整响应、人工参考和重试证据见详情。" if lang=="zh" else "This is a programmatic locator without independently reviewed root cause. The detail has complete output, human reference and retry evidence."}</p><p><a data-diagnosis-link href="#">{"查看完整同页证据 →" if lang=="zh" else "Open complete same-page evidence →"}</a></p></div></dialog>'
  inv=f'<div class="hero"><h1>{esc(x["results"])}</h1><p>{"先比较同一网页上的方法差异，再筛选有效参考结果。" if lang=="zh" else "Compare methods on the same page, then filter qualified-reference results."}</p></div>{explorer}<section><h2>{"逐项结果" if lang=="zh" else "Individual results"}</h2>{filters}<p><b id="count"></b> {esc(x["count"])} / {visible_count:,}</p><p id="empty-results" class="muted" hidden>{"当前筛选没有结果；请调整页面、浏览器或诊断条件。" if lang=="zh" else "No results match these filters. Adjust the page, browser or locator."}</p><div class="scroll result-scroll"><table><thead><tr>'+''.join('<th>'+esc(x[k])+'</th>' for k in ('case','type','provider','f1','precision','recall','diagnosis','latency'))+f'<th>{"原因与证据" if lang=="zh" else "Cause and evidence"}</th></tr></thead><tbody>'+''.join(inventory)+f'</tbody></table></div>{dialog}</section>'
  page(lang,'analysis/index.html',x['results'],inv)
  flow_labels=(['冻结 HTML',f'{len(providers)} 种配置转换','最终响应与重试','官方逐页评分','有效参考汇总'] if lang=='zh' else ['Frozen HTML',f'{len(providers)} conversions','Final response and retries','Official page score','Qualified aggregate'])
  flow_source='flowchart LR\n A["'+flow_labels[0]+'"] --> B["'+flow_labels[1]+'"]\n B --> C["'+flow_labels[2]+'"]\n C --> D["'+flow_labels[3]+'"]\n D --> E["'+flow_labels[4]+'"]\n C -- '+('失败重试' if lang=='zh' else 'failure retry')+' --> B'
  flow='<div class="panel"><h2>'+('一页怎样变成一行结果' if lang=='zh' else 'From one page to one result')+'</h2><div class="flow"><div class="flow-fallback">'+' <b class="arrow">→</b> '.join(f'<span class="tag">{esc(label)}</span>' for label in flow_labels)+'</div><pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div></div>'
  if lang=='zh':
   method=f'<div class="hero"><h1>评测方法</h1><p>{case_count} 份冻结 HTML、六种执行配置、一套官方词语重合评分。</p></div>'+flow+f'<section class="panel"><h2>输入与运行边界</h2><p>WCXB v1.0 test 分区中，按冻结输入和人工参考核定的 {case_count} 页进入本报告。六种配置读取完全相同的冻结 HTML；本地五种配置通过隔离的回环 HTTPS 服务读取，Kitesurf 通过 Cloudflare HTML 输入接口读取。外部子资源被阻断。每页一次逻辑调用；失败最多再试一次，全部物理调用保留并累计端到端耗时，最后一次响应是唯一正式结果。</p><p>Kitesurf、Lightpanda、Obscura 和 Moli 返回原生 Markdown；Chromium 渲染整页 DOM，再用 node-html-markdown 2.0.0 转换。没有共同的主内容选择器。</p></section><section class="panel"><h2>分母与评分</h2><p>官方评估器将文本转为小写词语，按词频计算精确率、召回率和 F1。质量比较只使用事前验证的 {quality_count} 页有效参考，逐页等权。F1 不计段落顺序、Markdown 结构或表格行列。应包含和应排除片段另作诊断，不进入 F1。</p><p>质量与耗时都使用同一批 {case_count} 页；正式版与 PR 构建版分别保留原始调用证据。</p></section><section class="panel"><h2>定位与耗时</h2><p>请求失败、空输出和错误页单独定位；非空参考下精确率或召回率低于 25% 时显示对应低值。该阈值只是筛选条件，不是 WCXB 官方及格线。耗时包括调用及失败重试，不是浏览器内核速度；六种配置的工作路线和远近端部署不同。</p></section>'
   guide=''
  else:
   method=f'<div class="hero"><h1>Method</h1><p>All {case_count} frozen HTML pages, six configurations, one official word-overlap evaluator.</p></div>'+flow+f'<section class="panel"><h2>Inputs and execution</h2><p>The {case_count} WCXB v1.0 test pages qualified by frozen input and human reference enter this report. The six configurations read the same frozen HTML. Five local configurations use an isolated loopback HTTPS fixture; Kitesurf receives HTML through Cloudflare’s input field. External subresources are blocked. Each page has one logical result; a failed call may be retried once. Every physical call and its latency are retained, with the final response authoritative.</p><p>Kitesurf, Lightpanda, Obscura and Moli return native Markdown. Chromium renders the full DOM and converts it with node-html-markdown 2.0.0. No shared main-content selector is applied.</p></section><section class="panel"><h2>Denominators and score</h2><p>The official evaluator lowercases and tokenizes text, then computes word-frequency precision, recall and F1. Quality compares only the {quality_count} references qualified before calls, with equal page weights. F1 does not score paragraph order, Markdown structure or table layout. Required and forbidden snippets are separate diagnostics outside F1.</p><p>Quality and latency use the same {case_count} pages. Released and PR-build Moli calls each retain their own immutable evidence.</p></section><section class="panel"><h2>Locators and latency</h2><p>Request errors, empty outputs and error pages are separate locators. Precision or recall below 25% on nonempty references triggers a low-value locator; this is a filter, not an official WCXB pass threshold. Latency includes calls and failed retries, not pure browser-core time; local and remote routes differ.</p></section>'
   guide=''
  qualified=[c for c in by_case if c not in exclusion_ids]
  type_averages={t:sum(by_case[c][p]['f1'] for c,g in ground_truth.items() if c not in exclusion_ids and g['page_type']==t for p in providers)/(type_counts[t]*len(providers)) for t in type_counts}
  hardest=min(type_averages,key=type_averages.get)
  hardest_case=min((c for c in qualified if ground_truth[c]['page_type']==hardest),key=lambda c:sum(by_case[c][p]['f1'] for p in providers))
  if lang=='zh':
   guide+=f'<section class="panel"><h2>哪些网页更难</h2><p>按七种页面类型、六种配置的有效参考 F1 等权汇总，{esc(type_label(lang,hardest))}最低，为 {fmt(type_averages[hardest])}（{type_counts[hardest]} 页）。这描述本轮数据中的重合程度，不说明该类型网页在互联网上普遍更难。<a href="{href(lang,"cases/"+hardest_case+".html")}">{hardest_case} 页</a>可查看这一类型的低分输入与六份输出；首页按类型表保留各方法完整数值。</p></section>'
  else:
   guide+=f'<section class="panel"><h2>Which pages were harder</h2><p>Across seven page types and six configurations, {esc(hardest)} has the lowest mean qualified F1, {fmt(type_averages[hardest])} on {type_counts[hardest]} pages. This describes overlap in this cohort, not a general difficulty ranking for the web. <a href="{href(lang,"cases/"+hardest_case+".html")}">Page {hardest_case}</a> shows a low-scoring input and all six outputs; the home page retains every method-by-type value.</p></section>'
  review_record=json.loads((out/'research/wcxb-representative-review.json').read_text())
  reviewed_ids={item['case_id'] for item in review_record['cases']}
  if reviewed_ids != {item['case_id'] for item in review_scope['cases']} or review_record['result_count'] != reviewed_results:
   raise ValueError('WCXB representative review inventory changed')
  review_links=lambda case,provider:href(lang,'cases/'+case+'.html#'+provider)
  obscura_wins=sum(by_case[c]['obscura']['f1']>by_case[c]['moli']['f1'] for c in qualified)
  obscura_longer=sum(by_case[c]['obscura']['output_chars']>by_case[c]['moli']['output_chars'] for c in qualified)
  obscura_chars=statistics.fmean(by_case[c]['obscura']['output_chars'] for c in qualified)
  moli_chars=statistics.fmean(by_case[c]['moli']['output_chars'] for c in qualified)
  guide_metrics=(('quality_f1','max',2),('precision','max',1),('recall','max',1),('mean_latency_ms','min',0))
  guide_winners={key:unique_winner({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in guide_metrics}
  guide_rows=''.join('<tr><td><a href="'+href(lang,'providers/'+p+'.html')+'">'+esc(browser_label(p))+'</a></td>'+''.join(comparison_cell(summary['providers'][p][key],'guide-'+key,direction,p==guide_winners[key],(fmt(summary['providers'][p][key],digits) if direction=='max' else f'{summary["providers"][p][key]:,.0f} ms')) for key,direction,digits in guide_metrics)+'</tr>' for p in sorted(providers,key=lambda p:summary['providers'][p]['quality_f1'],reverse=True))
  if lang=='zh':
   guide_lead=f'<div class="hero essay-hero"><p class="eyebrow">WCXB · {quality_count} 页 · 五款浏览器 / 六种配置</p><h1>报告解读</h1><p>Obscura 的主内容词语重合分最高，Moli 正式版和修复版接近；但高分也可能漏正文，低精确率也可能来自可用的 Markdown 链接。下面用同一批网页的输出解释分数、真实缺陷和修复边界。</p></div>'
   guide_lead+=f'<nav class="essay-toc" aria-label="本页目录"><a href="#guide-results">结果</a><a href="#guide-score">分数怎样读</a><a href="#guide-timing">Moli 耗时</a><a href="#guide-cases">典型网页</a><a href="#guide-repair">Moli 修复</a><a href="#guide-methods">各方法问题</a><a href="#guide-conclusion">结论</a></nav><section class="panel" id="guide-results"><h2>先看结果</h2><p>六种配置都处理同一批 {quality_count} 页。表中精确率、召回率和 F1 是逐页等权平均；耗时为包含失败重试的端到端调用均值，跨方法的输入路线不同。绿色星标是该列唯一最优值。</p><div class="scroll essay-table"><table><thead><tr><th>方法</th><th>F1</th><th>精确率</th><th>召回率</th><th>平均耗时</th></tr></thead><tbody>{guide_rows}</tbody></table></div><p>Obscura 的 F1 为 {fmt(summary["providers"]["obscura"]["quality_f1"],2)}；Moli 1.1.8 为 {fmt(summary["providers"]["moli"]["quality_f1"],2)}。这张表回答“原始重合分谁高”，不替代正文完整性判断。<a href="{href(lang,"index.html")}">完整指标与 P50/P95 →</a></p></section>'
  else:
   guide_lead=f'<div class="hero essay-hero"><p class="eyebrow">WCXB · {quality_count} pages · five browsers / six configurations</p><h1>Interpreting the results</h1><p>Obscura leads word overlap with the human main-content reference, while released and repaired Moli are close. Yet a high score can miss body text, and low precision can include useful Markdown links. The same-page outputs below separate scores, real defects and repair boundaries.</p></div>'
   guide_lead+=f'<nav class="essay-toc" aria-label="On this page"><a href="#guide-results">Results</a><a href="#guide-score">Reading scores</a><a href="#guide-timing">Moli latency</a><a href="#guide-cases">Case evidence</a><a href="#guide-repair">Moli repair</a><a href="#guide-methods">Method issues</a><a href="#guide-conclusion">Conclusion</a></nav><section class="panel" id="guide-results"><h2>Results at a glance</h2><p>All six configurations process the same {quality_count} pages. Precision, recall and F1 are equal-weight per-page means; latency is end-to-end call time including failed retries. Input routes differ across methods. Green stars mark unique column leaders.</p><div class="scroll essay-table"><table><thead><tr><th>Method</th><th>F1</th><th>Precision</th><th>Recall</th><th>Mean latency</th></tr></thead><tbody>{guide_rows}</tbody></table></div><p>Obscura scores {fmt(summary["providers"]["obscura"]["quality_f1"],2)} F1; released Moli 1.1.8 scores {fmt(summary["providers"]["moli"]["quality_f1"],2)}. This table ranks raw overlap, not body completeness. <a href="{href(lang,"index.html")}">Full metrics and P50/P95 →</a></p></section>'
  guide=guide_lead+guide
  if lang=='zh':
   guide+='<section class="panel"><h2>精确率为什么会低</h2><p>官方精确率＝与人工参考重合的词数 ÷ 输出总词数；召回率＝重合词数 ÷ 参考总词数。若只统计匹配词，多带导航、广告或无关内容就不会付出代价。这里的关键限制是官方程序直接对 Markdown 字符串分词，链接目标、图片地址和元数据也进入“输出总词数”，即使它们对 Markdown 读者有用。人工参考字段是主内容纯文本，不是完整 Markdown 的逐字标准答案。因此首页 F1 是官方原始字符串重合分，不等于可见正文质量或 Markdown 保真度。</p><p>文章 <a href="'+review_links('5649','moli')+'">5649</a> 中，Moli 与 Obscura 的参考召回率都为 100%，但精确率分别为 '+fmt(by_case['5649']['moli']['precision'])+' 和 '+fmt(by_case['5649']['obscura']['precision'])+'。Moli 响应含 184 个 URL 起始标记，Obscura 为 117 个；官方分词把网址组成词计入精确率分母。这个差异不能单独证明 Moli 少提了正文。</p></section>'
   guide+='<section class="panel"><h2>Obscura 为什么分数更高</h2><p>同一批 '+str(quality_count)+' 页中，Obscura 在 '+str(obscura_wins)+' 页的原始 F1 高于 Moli；平均精确率分别为 '+fmt(summary['providers']['obscura']['precision'])+' 与 '+fmt(summary['providers']['moli']['precision'])+'，参考召回率均约 99.9%。但 Obscura 在 '+str(obscura_longer)+' 页输出更长，平均 '+format(obscura_chars,',.0f')+' 字符，Moli 为 '+format(moli_chars,',.0f')+' 字符；高分不能简单解释成删除了更多网页内容。</p><p><a href="'+review_links('4923','moli')+'">4923 页</a>中，Moli 把区块链接的目标地址重复写入多个片段，增加了原始评分中的网址词；减少这种重复值得研究，但应保留可用的链接。反例是<a href="'+review_links('4461','obscura')+'">4461 页</a>：Obscura 的 F1 较高，却将相邻价格与会员标签拼成 <code>$149.95VIP</code>，参考召回率 '+fmt(by_case['4461']['obscura']['recall'])+'，低于 Moli 的 '+fmt(by_case['4461']['moli']['recall'])+'。正确方向是保留正文、词边界和链接语义，减少无意义的重复；不按原始分数照搬输出策略。</p></section>'
   guide+='<section class="panel"><h2>Moli 两版耗时</h2><p>稳定版 v1.1.8 与实测 PR 在同一批 '+str(case_count)+' 页做两轮串行交叉实验，逐页对调调用顺序，事先固定最终轮更新主表。最终轮稳定版平均 '+format(paired_latency['release']['mean_ms'],'.1f')+' ms，PR 平均 '+format(paired_latency['pr']['mean_ms'],'.1f')+' ms；差 '+format(paired_latency['paired_delta_mean_ms'],'+.1f')+' ms。两版均有完整输入、二进制哈希与 '+str(case_count*4)+' 次调用记录。<a href="research/wcxb-full-paired-latency/calls.jsonl">逐次计时</a> · <a href="research/wcxb-full-paired-latency/certification.json">完整性证明</a>。</p></section>'
   guide+='<section class="panel"><h2>这能说明 PR 变慢吗</h2><p>两轮逐页平均差分别为 '+format(first_round_delta,'+.1f')+' ms 和 '+format(paired_latency['paired_delta_mean_ms'],'+.1f')+' ms，方向相同，幅度约一毫秒。两版源码基线不同，因此实验可报告当前两个二进制的实测差，不能把差额全归给 PR 的三项修改。代码修复价值以真实输出问题判断。</p></section>'
   guide+='<section class="panel"><h2>典型异常与处理</h2><div class="scroll"><table><thead><tr><th>网页</th><th>直接证据</th><th>判断与处理</th></tr></thead><tbody>'
   guide+='<tr><td><a href="'+review_links('4017','lightpanda')+'">4017 · 商品</a></td><td>Lightpanda F1 '+fmt(by_case['4017']['lightpanda']['f1'])+' 最高，但召回率只有 '+fmt(by_case['4017']['lightpanda']['recall'])+'；其余四款均找回全部参考词。</td><td>短输出减少了无关文字，也漏了参考内容；较高 F1 不等于更完整。</td></tr>'
   guide+='<tr><td><a href="'+href(lang,'cases/4021.html#reference')+'">4021 · 合集</a></td><td>人工参考 602 字符，只标注一组商品；原五款整页响应均超过 20,000 字符。</td><td>共同的页面范围差异；这条参考不能代表整页 Markdown 的完整标准答案。</td></tr>'
   guide+='<tr><td><a href="'+review_links('5571','moli')+'">5571 · 列表</a></td><td>冻结 HTML 有 34 个空 SVG 占位图；Moli 将其 data URI 写进 Markdown，Chromium 未输出这些占位地址。</td><td>图片地址属于 Markdown 引用；像素空白不能证明引用无用。纯文本参考不覆盖图片，原始 F1 会把地址词计入分母。</td></tr>'
   guide+='<tr><td><a href="'+review_links('4394','moli')+'">4394 · 合集</a></td><td>冻结页含 1×1 GIF；Moli、Kitesurf、Obscura 各输出 131 条该地址，Chromium 未输出。原五款参考召回率均为 100%。</td><td>多款方法保留图片引用；单像素或透明不足以证明可以删图。纯文本参考不能验证图片保真度。</td></tr>'
   guide+='<tr><td><a href="'+review_links('5458','moli')+'">5458 · 文档</a></td><td>两个相邻代码元素分别写着 name、string；Moli 输出单一代码值 namestring，Chromium 输出两个代码值并加入空格。</td><td>稳定版确实丢失节点边界。候选版用内联 HTML 保留两个 code 节点；冻结页的可见间距来自 CSS，转换器没有布局信息，因此它不声称还原视觉间距。</td></tr>'
   guide+='<tr><td><a href="'+review_links('5412','moli')+'">5412 · 文档</a></td><td>代码示例以两个区块分别存放语言标签 ts 与正文 function；Moli 将其拼成 tsfunction，其他四款保留词边界。</td><td>预格式区块边界丢失；候选修复按 HTML 区块结构保留换行。</td></tr>'
   guide+='<tr><td><a href="'+review_links('4923','moli')+'">4923 · 列表</a></td><td>冻结 HTML 有 161 个链接元素；Moli 输出 501 个网址起始标记，其他方法为 196–257 个，Moli 的参考召回率仍为 99.8%。</td><td>链接包裹区块时会重复序列化目标地址；目前没有证据证明链接不可用或正文缺失，因此不为提高原始词分删链接。</td></tr>'
   guide+='<tr><td><a href="'+review_links('5677','lightpanda')+'">5677 · 文章</a></td><td>Lightpanda 1,650 字符响应漏掉正文开头，召回率 '+fmt(by_case['5677']['lightpanda']['recall'])+'；另外四款均为 100%。</td><td>该方法实际漏掉参考正文，不属于网址分词造成的误扣；正式结果保留，修复需重新测量。</td></tr></tbody></table></div></section>'
   guide+='<section class="panel"><h2>五款浏览器、六种配置暴露的问题</h2><div class="scroll"><table><thead><tr><th>方法</th><th>本轮证据支持的现存问题</th><th>证据入口</th></tr></thead><tbody>'
   guide+='<tr><td>Kitesurf</td><td>原生 Markdown 带页面元数据及整页链接；商品 4017 找回参考词，但大量其他页面词拉低精确率。</td><td><a href="'+review_links('4017','kitesurf')+'">4017 输出</a></td></tr>'
   guide+='<tr><td>Lightpanda</td><td>有正文遗漏：文章 5677 的参考开头缺失；商品 4017 的较高 F1 伴随较低召回率。</td><td><a href="'+review_links('5677','lightpanda')+'">5677 输出</a></td></tr>'
   guide+='<tr><td>Obscura</td><td>仍返回整页导航与链接；合集 4021 的低精确率与其他整页方法同源。商品合集 4461 还把相邻价格与会员标签拼成同一个词。</td><td><a href="'+review_links('4021','obscura')+'">4021</a> · <a href="'+review_links('4461','obscura')+'">4461</a></td></tr>'
   guide+='<tr><td>Moli 1.1.8 正式版</td><td>保留 SVG 与内嵌图片引用；纯文本评分把地址词计入精确率分母。相邻代码值和预格式区块会被拼接，UI 清理还会删除详情正文。原结果与分数完整保留。</td><td><a href="'+review_links('5571','moli')+'">5571</a> · <a href="'+review_links('4394','moli')+'">4394</a> · <a href="'+review_links('5458','moli')+'">5458</a> · <a href="'+review_links('5412','moli')+'">5412</a></td></tr>'
   guide+='<tr><td>Moli PR #643 构建版</td><td>从同一冻结输入重新测得独立结果。源码修复详情正文误删及预格式区块粘连；相邻 code 节点改为结构无损回退，但不恢复 CSS 间距。图片引用继续保留。</td><td><a href="'+review_links('5571','moli-pr643')+'">5571</a> · <a href="'+review_links('4394','moli-pr643')+'">4394</a> · <a href="'+review_links('5458','moli-pr643')+'">5458</a> · <a href="'+review_links('5412','moli-pr643')+'">5412</a></td></tr>'
   guide+='<tr><td>Chromium</td><td>整页 DOM 转 Markdown 也包含导航、推荐与链接；商品 4017 的参考召回率为 100%，精确率仅 '+fmt(by_case['4017']['chromium']['precision'])+'。</td><td><a href="'+review_links('4017','chromium')+'">4017 输出</a></td></tr></tbody></table></div>'
   guide+='<section class="panel"><h2>两页降分的原因</h2><p><a href="'+review_links('5411','moli-pr643')+'">5411</a> 的人工参考把分块的 <code>ts</code> 与 <code>function</code> 拼成一词，PR 恢复换行后原始 F1 下降。<a href="'+review_links('5458','moli-pr643')+'">5458</a> 的人工参考实际是分开的 <code>name string</code>；PR 的参考召回率从 98.45% 升至 100%，但原始字符串评分又把内联 HTML 的 <code>code</code> 标签名称计入输出词数，使精确率和 F1 下降。这里暴露的是评分器直接对 Markdown 源码分词的限制，不是参考答案要求粘连。</p></section>'
   guide+='<section class="panel"><h2>参考与片段标注的限制</h2><p>源评测集的 main_content 是人工选出的纯文本；它可用于词语重合，却不能验证 Markdown 链接、图片、列表或表格是否完整。另有 '+str(audit['without_present_in_main_content'])+' 个“应排除”片段同时出现在参考正文，涉及 23 页；这类片段得分照官方程序保留，但不能据此归因浏览器错误。典型审查覆盖 '+str(reviewed_cases)+' 页、'+str(reviewed_results)+' 条输出；其余 '+format(unreviewed_results,',')+' 条未接受逐项人工审查。</p></section>'
  else:
   guide+='<section class="panel"><h2>Why precision falls</h2><p>Official precision divides the number of overlapping words by all output words; recall divides it by reference words. Counting only matching words would leave surplus navigation and unrelated content unpenalized. The evaluator tokenizes the raw Markdown string, so link destinations, image URLs and metadata also enter the denominator. The human main_content field is selected plain text, not a complete Markdown reference. Raw F1 therefore does not measure visible body quality or Markdown fidelity.</p><p>On <a href="'+review_links('5649','moli')+'">page 5649</a>, Moli and Obscura both recall 100% of reference words, but precision is '+fmt(by_case['5649']['moli']['precision'])+' versus '+fmt(by_case['5649']['obscura']['precision'])+'. Moli outputs 184 URL starts, versus 117 for Obscura. URL words enter the official denominator; this difference alone does not prove missing body content.</p></section>'
   guide+='<section class="panel"><h2>Why Obscura scores higher</h2><p>Across the same '+str(quality_count)+' pages, Obscura has a higher raw F1 than Moli on '+str(obscura_wins)+'. Mean precision is '+fmt(summary['providers']['obscura']['precision'])+' versus '+fmt(summary['providers']['moli']['precision'])+'; both recall about 99.9% of the reference words. Obscura nevertheless returns more characters on '+str(obscura_longer)+' pages, averaging '+format(obscura_chars,',.0f')+' characters versus Moli’s '+format(moli_chars,',.0f')+'. Its score cannot be explained simply as discarding more page content.</p><p>On <a href="'+review_links('4923','moli')+'">page 4923</a>, Moli repeats block-link destinations across fragments, increasing URL tokens in the raw score. Reducing repetition is worth investigating while preserving working links. <a href="'+review_links('4461','obscura')+'">Page 4461</a> shows the other side: Obscura scores a higher F1 but joins adjacent price and membership text into <code>$149.95VIP</code>. Its reference recall is '+fmt(by_case['4461']['obscura']['recall'])+', below Moli’s '+fmt(by_case['4461']['moli']['recall'])+'. Preserve body text, word boundaries and link meaning while removing needless repetition; do not copy output behavior solely for its score.</p></section>'
   guide+='<section class="panel"><h2>Moli latency</h2><p>The v1.1.8 release and measured PR ran two serial crossover rounds on the same '+str(case_count)+' pages, reversing order per page and preselecting the final round for the main table. Final-round means were '+format(paired_latency['release']['mean_ms'],'.1f')+' ms and '+format(paired_latency['pr']['mean_ms'],'.1f')+' ms, a '+format(paired_latency['paired_delta_mean_ms'],'+.1f')+' ms page-paired difference. Frozen inputs, binary hashes and all '+str(case_count*4)+' calls are bound. <a href="research/wcxb-full-paired-latency/calls.jsonl">Every call</a> · <a href="research/wcxb-full-paired-latency/certification.json">Certification</a>.</p></section>'
   guide+='<section class="panel"><h2>Is the PR slower?</h2><p>Page-paired mean differences in the two rounds were '+format(first_round_delta,'+.1f')+' ms and '+format(paired_latency['paired_delta_mean_ms'],'+.1f')+' ms, both around one millisecond in the same direction. The release and PR codebases diverge, so the measured binary difference cannot all be attributed to the three PR changes. The repair is justified by actual output defects.</p></section>'
   guide+='<section class="panel"><h2>Representative anomalies and treatment</h2><div class="scroll"><table><thead><tr><th>Page</th><th>Direct evidence</th><th>Finding</th></tr></thead><tbody>'
   guide+='<tr><td><a href="'+review_links('4017','lightpanda')+'">4017 · product</a></td><td>Lightpanda leads raw F1 at '+fmt(by_case['4017']['lightpanda']['f1'])+' but recalls only '+fmt(by_case['4017']['lightpanda']['recall'])+'; the other four recall all reference words.</td><td>A shorter response removes surplus text and also misses reference content; higher F1 does not mean more complete output.</td></tr>'
   guide+='<tr><td><a href="'+href(lang,'cases/4021.html#reference')+'">4021 · collection</a></td><td>The 602-character human reference covers a product list; every full-page response exceeds 20,000 characters.</td><td>Shared scope mismatch. The reference is not a complete-page Markdown answer.</td></tr>'
   guide+='<tr><td><a href="'+review_links('5571','moli')+'">5571 · listing</a></td><td>The frozen HTML has 34 empty SVG image placeholders. Moli serializes their data URIs; Chromium does not.</td><td>Image addresses are Markdown references; blank pixels do not prove they are dispensable. The plain-text reference does not cover images, while raw F1 counts URI words.</td></tr>'
   guide+='<tr><td><a href="'+review_links('4394','moli')+'">4394 · collection</a></td><td>The frozen page has a 1×1 GIF. Moli, Kitesurf and Obscura each emit its address 131 times; Chromium omits it. All five recall 100% of reference words.</td><td>Several methods retain image references. A single pixel or transparency does not justify deletion; the plain-text reference cannot assess image fidelity.</td></tr>'
   guide+='<tr><td><a href="'+review_links('5458','moli')+'">5458 · document</a></td><td>Adjacent code elements contain separate name and string values. Moli emits one namestring value; Chromium emits two values with a space.</td><td>The release loses a node boundary. The candidate uses inline HTML to preserve two code nodes. Their visible gap comes from page CSS, which this layout-independent converter does not reproduce.</td></tr>'
   guide+='<tr><td><a href="'+review_links('5412','moli')+'">5412 · document</a></td><td>Two blocks inside a code sample hold the ts language label and body starting with function. Moli joins them as tsfunction; the other four retain the word boundary.</td><td>A preformatted block boundary is lost. The candidate preserves line breaks from HTML block structure.</td></tr>'
   guide+='<tr><td><a href="'+review_links('4923','moli')+'">4923 · listing</a></td><td>The frozen HTML has 161 anchor elements. Moli emits 501 URL starts versus 196–257 for peers while recalling 99.8% of reference words.</td><td>Block-spanning links repeat destinations. There is no evidence yet of broken links or missing body, so URL deletion would only optimize the raw word score.</td></tr>'
   guide+='<tr><td><a href="'+review_links('5677','lightpanda')+'">5677 · article</a></td><td>Lightpanda’s 1,650-character response omits the opening body and recalls '+fmt(by_case['5677']['lightpanda']['recall'])+'; the other four recall 100%.</td><td>This method genuinely misses reference body text; URL tokenization does not explain it. Retain the measured result and remeasure after repair.</td></tr></tbody></table></div></section>'
   guide+='<section class="panel"><h2>Issues observed for each method</h2><div class="scroll"><table><thead><tr><th>Method</th><th>Current evidence</th><th>Case</th></tr></thead><tbody>'
   guide+='<tr><td>Kitesurf</td><td>Native Markdown includes page metadata and whole-page links. On product 4017, surplus text lowers precision despite full reference recall.</td><td><a href="'+review_links('4017','kitesurf')+'">4017</a></td></tr>'
   guide+='<tr><td>Lightpanda</td><td>Article 5677 omits body text; the higher F1 on product 4017 comes with lower recall.</td><td><a href="'+review_links('5677','lightpanda')+'">5677</a></td></tr>'
   guide+='<tr><td>Obscura</td><td>Returns whole-page navigation and links; collection 4021 shares the low-precision scope mismatch. On product collection 4461 it also joins adjacent price and membership text into one word.</td><td><a href="'+review_links('4021','obscura')+'">4021</a> · <a href="'+review_links('4461','obscura')+'">4461</a></td></tr>'
   guide+='<tr><td>Released Moli 1.1.8</td><td>Retains SVG and embedded-image references, whose URI words enter the plain-text score. It also merges adjacent code values and preformatted blocks and loses detail text with UI stripping. Its original results and scores remain intact.</td><td><a href="'+review_links('5571','moli')+'">5571</a> · <a href="'+review_links('4394','moli')+'">4394</a> · <a href="'+review_links('5458','moli')+'">5458</a> · <a href="'+review_links('5412','moli')+'">5412</a></td></tr>'
   guide+='<tr><td>Moli PR #643 build</td><td>A separately measured result on the same frozen input. Source changes repair deleted disclosure text and joined preformatted blocks. Adjacent code uses a structure-preserving fallback without reconstructing CSS spacing. Image references remain intact.</td><td><a href="'+review_links('5571','moli-pr643')+'">5571</a> · <a href="'+review_links('4394','moli-pr643')+'">4394</a> · <a href="'+review_links('5458','moli-pr643')+'">5458</a> · <a href="'+review_links('5412','moli-pr643')+'">5412</a></td></tr>'
   guide+='<tr><td>Chromium</td><td>Full-DOM conversion retains navigation, recommendations and links. Product 4017 recalls 100% of reference words but precision is '+fmt(by_case['4017']['chromium']['precision'])+'.</td><td><a href="'+review_links('4017','chromium')+'">4017</a></td></tr></tbody></table></div>'
   guide+='<section class="panel"><h2>Why two pages score lower</h2><p>The human reference for <a href="'+review_links('5411','moli-pr643')+'">5411</a> joins the separate <code>ts</code> and <code>function</code> blocks as one word. The PR restores the newline, lowering raw F1. The reference for <a href="'+review_links('5458','moli-pr643')+'">5458</a> actually contains separate <code>name string</code> tokens: reference recall rises from 98.45% to 100%, but raw-source tokenization also counts every inline-HTML <code>code</code> tag name, lowering precision and F1. That is a limitation of scoring Markdown source as words, not a reference that requires joined text.</p></section>'
   guide+='<section class="panel"><h2>Reference and snippet limits</h2><p>Source main_content is human-selected plain text: it supports word overlap, not complete Markdown structure, links, images, lists or tables. '+str(audit['without_present_in_main_content'])+' forbidden snippets also occur in the reference body across 23 pages. Official snippet scores remain, but these conflicts cannot diagnose browser errors. This targeted review covers '+str(reviewed_cases)+' pages and '+str(reviewed_results)+' outputs; the other '+format(unreviewed_results,',')+' results were not individually reviewed.</p></section>'
  repair=(out/f'content/wcxb-repair-{lang}.fragment').read_text(encoding='utf-8').rstrip()
  if lang=='zh':
   repair+=f'<p class="source-note">实测 PR 源码 {esc(profile["candidate_source_commit"][:8])} 在 {quality_count} 页中，F1 有 {up} 页上升、{down} 页下降、{same} 页不变，平均差 {((moli_after["quality_f1"]-moli_before["quality_f1"])*100):+.4f} 个百分点。分数变化很小；代码边界和可选清理的修复应看原始输出。v1.1.8 与 PR 源码有其他版本差异，整批对照不能单独证明每项差异由本 PR 引起。</p></section>'
  else:
   repair+=f'<p class="source-note">Measured PR source {esc(profile["candidate_source_commit"][:8])} raises per-page F1 on {up} of {quality_count} pages, lowers it on {down}, and leaves {same} unchanged. The mean difference is {((moli_after["quality_f1"]-moli_before["quality_f1"])*100):+.4f} percentage points. Inspect raw outputs for code boundaries and optional stripping. v1.1.8 and the PR have other version differences, so cohort differences alone do not isolate this PR as the cause.</p></section>'
  specimen=('<section id="guide-cases"><h2>原始样例</h2><p>下方三张图给出冻结 HTML、两版输出摘录和完整结果入口。</p></section>' if lang=='zh' else '<section id="guide-cases"><h2>Source examples</h2><p>The three figures below connect frozen HTML, both output excerpts and the complete results.</p></section>')
  conclusion=(f'<section class="panel essay-conclusion" id="guide-conclusion"><h2>结论</h2><p>Obscura 在官方词语重合分领先；这不证明其 Markdown 结构处处最佳。Lightpanda 在 5677 漏正文，Obscura 在 4461 粘连文字。Moli 1.1.8 的两类代码边界错误和可选界面清理误删正文均可重现；实测 PR 修复这些输出问题，整批 F1 变化很小。人工参考是主内容纯文本，不能评价链接、图片和代码结构。此次代表性审查覆盖 {reviewed_cases} 页、{reviewed_results} 条输出，其余 {unreviewed_results:,} 条未逐项人工审查。</p></section>' if lang=='zh' else f'<section class="panel essay-conclusion" id="guide-conclusion"><h2>Conclusions</h2><p>Obscura leads official word overlap, which does not establish that its Markdown structure is best on every page. Lightpanda misses body text on 5677, and Obscura joins text on 4461. Both code-boundary defects and optional UI stripping loss in Moli 1.1.8 are reproducible; the measured PR repairs those outputs, while cohort F1 changes little. The human reference is main-content plain text and does not assess link, image, or code structure. Representative review covers {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} have not been individually reviewed.</p></section>')
  score_title='精确率为什么会低' if lang=='zh' else 'Why precision falls'
  timing_title='Moli 两版耗时' if lang=='zh' else 'Moli latency'
  methods_title='五款浏览器、六种配置暴露的问题' if lang=='zh' else 'Issues observed for each method'
  anomalies_title='典型异常与处理' if lang=='zh' else 'Representative anomalies and treatment'
  guide=guide.replace(f'<section class="panel"><h2>{score_title}</h2>',f'<section class="panel" id="guide-score"><h2>{score_title}</h2>')
  guide=guide.replace(f'<section class="panel"><h2>{timing_title}</h2>',f'<section class="panel" id="guide-timing"><h2>{timing_title}</h2>')
  guide=guide.replace(f'<section class="panel"><h2>{methods_title}</h2>',f'<section class="panel" id="guide-methods"><h2>{methods_title}</h2>')
  guide=guide.replace(f'<section class="panel"><h2>{anomalies_title}</h2>',specimen+repair+f'<section class="panel"><h2>{anomalies_title}</h2>')
  guide+=conclusion
  method+='<section class="panel"><h2>'+('片段标注边界' if lang=='zh' else 'Snippet annotation boundary')+'</h2><p>'+((f'{audit["without_present_in_main_content"]} 个应排除片段同时出现在人工主内容参考中。官方片段分照原程序保留，但这类冲突不用于判断浏览器错误；逐页详情会明确标注冲突。') if lang=='zh' else f'{audit["without_present_in_main_content"]} forbidden snippets also appear in their human main-content references. Official snippet values are retained, but these conflicts do not diagnose browser errors; result pages identify them explicitly.')+'</p></section>'
  page(lang,'methodology/index.html',x['method'],method)
  page(lang,'guide/index.html',x['guide'],guide)
  dataset_rows=[]
  for case in sorted(ground_truth):
   gt=ground_truth[case]
   source_url=by_case[case][providers[0]]['original_url']
   reference=gt['main_content']
   dataset_rows.append(
    f'<tr data-dataset-case="{esc(case+" "+source_url.lower())}" data-page-type="{esc(gt["page_type"])}">'
    f'<td>{esc(case)}</td><td>{esc(type_label(lang,gt["page_type"]))}</td>'
    f'<td class="source">{esc(source_url)}</td><td class="number">{len(reference):,}</td>'
    f'<td><a href="{href(lang,"cases/"+case+".html#preview")}">{"阅读冻结 HTML" if lang=="zh" else "Read frozen HTML"}</a></td>'
    f'<td><a href="{href(lang,"cases/"+case+".html#reference")}">{"人工主内容" if lang=="zh" else "Human main content"}</a></td>'
    f'<td><a href="{href(lang,"cases/"+case+".html")}">{"查看结果" if lang=="zh" else "View results"}</a></td></tr>')
  dataset_intro=(f'本轮任务是从 {case_count} 页冻结 HTML 提取主内容。这里列出输入和人工参考；六种配置的实测分数在结果分析中。' if lang=='zh' else f'The task is to extract main content from {case_count} frozen HTML pages. This inventory owns inputs and human references; the measured browser scores are in Results analysis.')
  dataset=f'<div class="hero dataset-hero"><h1>{esc(x["dataset"])}</h1><p>{esc(dataset_intro)}</p></div><section><h2>{"冻结网页与参考" if lang=="zh" else "Frozen pages and references"}</h2><p>{"人工参考是源评测集标注的主内容纯文本；并非完整 Markdown 标准答案。字符数只描述标注范围，不代表网页正文完整性。" if lang=="zh" else "The source benchmark provides human-selected main-content plain text, not a complete Markdown answer. Character count describes annotation scope, not page completeness."}</p><div class="filters"><label>{"搜索网页 ID 或地址" if lang=="zh" else "Search page ID or URL"}<input id="dataset-query" type="search"></label><label>{esc(x["type"])}<select id="dataset-type">{type_options}</select></label></div><p><b id="dataset-count">{case_count}</b> / {case_count} {"页" if lang=="zh" else "pages"}</p><div class="scroll result-scroll"><table id="wcxb-dataset"><thead><tr><th>ID</th><th>{esc(x["type"])}</th><th>{"原网页地址" if lang=="zh" else "Original URL"}</th><th>{"参考字符数" if lang=="zh" else "Reference characters"}</th><th>{"冻结输入" if lang=="zh" else "Frozen input"}</th><th>{"人工参考" if lang=="zh" else "Human reference"}</th><th>{"评测结果" if lang=="zh" else "Results"}</th></tr></thead><tbody>{"".join(dataset_rows)}</tbody></table></div></section>'
  page(lang,'datasets/index.html',x['dataset'],dataset)
  review_intro=(f'认证覆盖 {result_count:,}/{result_count:,} 条逻辑结果；源文件在调用前后哈希相同。典型异常审查核对 {reviewed_cases} 页、{reviewed_results} 条输出，其余 {unreviewed_results:,} 条未逐项人工审查。' if lang=='zh' else f'Certification covers {result_count:,}/{result_count:,} logical results; source bytes matched before and after all calls. Targeted anomaly review inspected {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} results were not individually reviewed.')
  review_zh=f"当前评测范围按冻结 HTML 和人工参考的有效性确定，不依据浏览器输出或得分。六种配置在同一批 {case_count} 页上比较。"
  review_en=f"The current evaluation population was qualified from frozen HTML and human references before reading browser outputs or scores. All six configurations are compared on the same {case_count} pages."
  review=f'<div class="hero"><h1>{esc(x["review"])}</h1><p>{esc(review_intro)}</p></div><section class="panel"><h2>{"源数据资格检查" if lang=="zh" else "Source qualification"}</h2><p>{review_zh if lang=="zh" else review_en}</p></section><section class="panel"><h2>{"执行证据" if lang=="zh" else "Execution evidence"}</h2><p>SHA-256: <code>{summary["source_sha256"]}</code></p><p><a href="certification.json">{"完整认证记录" if lang=="zh" else "Certification receipt"}</a> · <a href="wcxb-score-summary.json">{"评分汇总 JSON" if lang=="zh" else "Score summary JSON"}</a> · <a href="evidence-manifest.json">{"逐页证据哈希" if lang=="zh" else "Per-page evidence hashes"}</a> · <a href="research/wcxb-representative-review.json">{"典型审查记录" if lang=="zh" else "Representative review record"}</a></p></section>'
  page(lang,'review/index.html',x['review'],review)
  route_descriptions={'kitesurf':('Cloudflare Browser Run /markdown · remote HTML input','Cloudflare Browser Run /markdown · remote HTML input'),'lightpanda':('Lightpanda CLI · 原生 Markdown','Lightpanda CLI · native Markdown'),'obscura':('Obscura CLI · 原生 Markdown','Obscura CLI · native Markdown'),'moli':('Moli CLI 1.1.8 正式版 · 原生 Markdown','Moli CLI 1.1.8 release · native Markdown'),'moli-pr643':('Moli CLI PR #643 构建版 · 原生 Markdown','Moli CLI PR #643 build · native Markdown'),'chromium':('Chrome for Testing · 完整 DOM → node-html-markdown 2.0.0','Chrome for Testing · full DOM → node-html-markdown 2.0.0')}
  versions=''.join(f'<tr><td><a href="{href(lang,"providers/"+p+".html")}">{esc(browser_label(p))}</a></td><td>{esc(profile["versions"][p] if p=="moli-pr643" else identities[p].get("reported_version") or identities[p].get("endpoint") or "service version undisclosed")}</td><td>{esc(route_descriptions[p][0 if lang=="zh" else 1])}</td><td><code>{esc(identities[p].get("binary_sha256") or "hosted")}</code></td></tr>' for p in providers)
  interface_example='Kitesurf: POST Browser Run /markdown {html: <frozen HTML>}\nLightpanda / Obscura / Moli: CLI fetch <loopback fixture URL> → Markdown\nChromium: Playwright page content → node-html-markdown 2.0.0'
  adoption=f'<div class="hero"><h1>{esc(x["adoption"])}</h1><p>{"六种实际执行配置、版本、输入路线和输出契约。" if lang=="zh" else "Six measured configurations, versions, input routes and output contracts."}</p></div><section class="panel"><h2>{"本轮实现" if lang=="zh" else "Measured implementations"}</h2><div class="scroll"><table><thead><tr><th>{esc(x["provider"])}</th><th>{esc(x["version"])}</th><th>{"路线" if lang=="zh" else "Route"}</th><th>SHA-256</th></tr></thead><tbody>{versions}</tbody></table></div></section><section class="panel"><h2>{"接口示例" if lang=="zh" else "Interface examples"}</h2><pre>{esc(interface_example)}</pre><p>{"此表列的是实测接口，不把公开仓库搜索命中数当作产品采用量。" if lang=="zh" else "These are measured interfaces; repository search hits are not treated as product adoption counts."}</p></section>'
  adoption+='<section class="panel"><h2>'+('公开代码采用线索' if lang=='zh' else 'Public-code adoption signals')+'</h2><p>'+((f'{adoption_data["generated_at"][:10]} 的签名核验样本中，Cloudflare /markdown 接口在 {cloudflare_repo_count} 个仓库、{cloudflare_file_count} 个文件出现。该签名没有区分 Kitesurf 后端，也不能推算实际用户数。本轮其余五种 CLI / 浏览器配置没有同口径签名统计，不作采用量排名。') if lang=='zh' else f'In the {adoption_data["generated_at"][:10]} verified signature sample, the Cloudflare /markdown interface appears in {cloudflare_repo_count} repositories and {cloudflare_file_count} files. This signature does not identify the Kitesurf backend or user count. The other five CLI/browser configurations have no matching signature inventory, so no adoption ranking is made.')+'</p><p><a href="research/provider-adoption.json">'+('核验样本与来源' if lang=='zh' else 'Verified sample and sources')+'</a></p></section>'
  page(lang,'adoption/index.html',x['adoption'],adoption)
  for p in providers:
   s=summary['providers'][p]
   profile_body=f'<div class="hero"><h1>{esc(p)}</h1><p>{esc(route_descriptions[p][0 if lang=="zh" else 1])}</p></div><section class="panel"><h2>{"本轮结果" if lang=="zh" else "Measured result"}</h2><p>{"有效参考 F1" if lang=="zh" else "Qualified F1"}: <strong>{fmt(s["quality_f1"])}</strong> ({quality_count}) · {"非空输出" if lang=="zh" else "Nonempty outputs"}: {s["delivered"]}/{case_count}</p><p>{"完整调用端到端耗时均值 / P50 / P95" if lang=="zh" else "All-call end-to-end mean / P50 / P95"}: {s["mean_latency_ms"]:,.0f} / {s["p50_latency_ms"]:,.0f} / {s["p95_latency_ms"]:,.0f} ms ({case_count})</p><p>{esc(x["version"])}: {esc(identities[p].get("reported_version") or identities[p].get("endpoint") or "service version undisclosed")}</p><p><code>{esc(identities[p].get("binary_sha256") or "hosted")}</code></p></section><p><a href="{href(lang,"analysis/index.html")}">{esc(x["results"])} →</a></p>'
   page(lang,'providers/'+p+'.html',p,profile_body)
  changes_zh=f"六种配置完成全部 {case_count} 页调用；质量展示只使用事前核定的 {quality_count} 页有效参考，共 {visible_count:,} 条结果。端到端耗时保留每款 {case_count} 次调用，以免事后剔除慢或失败的调用。"
  changes_en=f"All six configurations completed {case_count} calls. Quality displays use only {quality_count} references qualified before calls, or {visible_count:,} results. End-to-end latency retains all {case_count} calls per method so slow or failed calls are not removed after measurement."
  changes=f'<div class="hero"><h1>{esc(x["changes"])}</h1><p>{"当前报告的结果、分母和证据范围。" if lang=="zh" else "Current result, denominator and evidence scope."}</p></div><section class="panel"><h2>{"有效参考与完整调用" if lang=="zh" else "Qualified references and all calls"}</h2><p>{changes_zh if lang=="zh" else changes_en}</p><p><a href="research/report-changes.json">{"结构化变更记录" if lang=="zh" else "Structured change record"}</a></p></section>'
  page(lang,'changes/index.html',x['changes'],changes)
  account_rows=''.join(f'<tr><td>{esc(p)}</td><td>{esc("、".join(HEADLESS_ACCESS[p]["env_vars"]))}</td><td>{esc(HEADLESS_ACCESS[p]["account"] if lang=="zh" else ("Cloudflare Browser Run account and API token required." if p=="kitesurf" else "No provider account or API key required."))}</td><td>'+''.join(f'<a href="{esc("https://github.com/lexmount/moli/releases/tag/v1.1.8" if p=="moli" else url)}">{esc(("Moli 1.1.8 正式发布" if p=="moli" else label) if lang=="zh" else ("Moli 1.1.8 official release" if p=="moli" else "Official source"))}</a> ' for label,url in HEADLESS_ACCESS[p]['links'])+'</td></tr>' for p in providers)
  accounts=f'<div class="hero"><h1>{esc(x["accounts"])}</h1><p>{"只有 Kitesurf 需要 Cloudflare 账号凭据；本地配置值不进入报告。" if lang=="zh" else "Only Kitesurf needs Cloudflare account credentials; local values are not published."}</p></div><section class="panel"><div class="scroll"><table><thead><tr><th>{esc(x["provider"])}</th><th>{"环境变量" if lang=="zh" else "Environment variables"}</th><th>{"账号要求" if lang=="zh" else "Account requirement"}</th><th>{"官方入口" if lang=="zh" else "Official source"}</th></tr></thead><tbody>{account_rows}</tbody></table></div></section>'
  page(lang,'accounts/index.html',x['accounts'],accounts)
  repro=f'<div class="hero"><h1>{esc(x["repro"])}</h1><p>{"版本和来源由本轮认证记录固定。" if lang=="zh" else "Versions and source are bound by this cohort certificate."}</p></div><section class="panel"><p>WCXB v1.0 test · {case_count} × {len(providers)} · SHA-256 <code>{summary["source_sha256"]}</code></p><p>{esc(x["source"])}: <a href="{source_repository}/tree/{source_commit}">GitHub {source_commit[:7]}</a> · <a href="sample-manifest.json">{"完整 test 清单" if lang=="zh" else "Full test manifest"}</a> · <a href="certification.json">certification.json</a> · <a href="wcxb-score-summary.json">score summary JSON</a></p><div class="scroll"><table><thead><tr><th>{esc(x["provider"])}</th><th>{esc(x["version"])}</th><th>{"路线" if lang=="zh" else "Route"}</th><th>SHA-256</th></tr></thead><tbody>{versions}</tbody></table></div><pre>uv run --frozen python scripts/run_certified_benchmark.py wcxb --dataset &lt;WCXB source&gt; --sample-manifest evaluation/public-wcxb-test-qualified.json --output-root &lt;new evidence dir&gt; --env-file .env\nuv run --frozen python scripts/score_wcxb_results.py --source &lt;WCXB source&gt; --sample-manifest evaluation/public-wcxb-test-qualified.json --evidence-root &lt;certified evidence dir&gt;\nuv run --frozen python scripts/build_report.py --evidence &lt;certified evidence dir&gt; --wcxb-source &lt;WCXB source&gt; --output reports/webfetch</pre><p class="small muted">{esc(x["no_review"])}</p></section>'
  page(lang,'reproduce/index.html',x['repro'],repro)
  for case,group in sorted(by_case.items()):
   if case in exclusion_ids:continue
   gt=ground_truth[case];reference=gt['main_content'];evidence_path=f'evidence/{case}.json';source_link=f'{source_repository}/blob/{source_commit}/test/html/{case}.html.gz';source_html=contained_path(out,f'inputs/{case}.txt').read_text(encoding='utf-8')
   same=''.join(f'<tr><td><a href="{href(lang,"cases/"+case+".html#"+p)}">{esc(p)}</a></td><td>{fmt(group[p]["precision"])}</td><td>{fmt(group[p]["recall"])}</td><td>{fmt(group[p]["f1"])}</td><td>{fmt(group[p]["with_rate"])}</td><td>{fmt(group[p]["without_rate"])}</td><td>{esc(diag(lang,group[p]["diagnosis"]))}</td></tr>' for p in providers)
   panels=[]
   for p in providers:
    r=group[p];output_path=contained_path(out,f'artifacts/{p}/{case}.txt');prediction=output_path.read_text(encoding='utf-8')
    pred_words=WordCounter(re.findall(r'\w+',prediction.lower()));ref_words=WordCounter(re.findall(r'\w+',reference.lower()))
    missing=', '.join(f'{word} ×{count}' for word,count in (ref_words-pred_words).most_common(12)) or '—'
    extra=', '.join(f'{word} ×{count}' for word,count in (pred_words-ref_words).most_common(12)) or '—'
    missing_snippets=[s for s in gt['with'] if s.lower() not in prediction.lower()]
    conflicts=[s for s in gt['without'] if s.lower() in reference.lower()]
    included_forbidden=[s for s in gt['without'] if s.lower() in prediction.lower() and s not in conflicts]
    snippet_detail='<p>'+('缺失的应包含片段' if lang=='zh' else 'Missing required snippets')+': '+esc('; '.join(missing_snippets) or '—')+'</p><p>'+('误带入的有效应排除片段' if lang=='zh' else 'Included valid forbidden snippets')+': '+esc('; '.join(included_forbidden) or '—')+'</p>'+(('<p>'+('标注冲突：这些应排除片段也出现在人工主内容参考中，不能据此归因给浏览器。' if lang=='zh' else 'Annotation conflict: these forbidden snippets also occur in the human main-content reference, so they cannot diagnose a browser failure.')+' '+esc('; '.join(conflicts))+'</p>') if conflicts else '')
    retries=r.get('retry_failures') or []
    retry_html=('<details><summary>'+('前次失败调用' if lang=='zh' else 'Earlier failed calls')+'</summary><ul>'+''.join(f'<li>#{v["physical_attempt"]} · {v["elapsed_ms"]:,.0f} ms · {esc(v["error_type"])} · {esc(v["error_message"])} · <a href="{esc(v["artifact_path"])}">'+('原始响应' if lang=='zh' else 'Raw response')+'</a></li>' for v in retries)+'</ul></details>') if retries else ''
    timing_note=('<p class="small muted">'+('耗时取同页交叉实验最终轮；正文与评分来自原始调用。' if lang=='zh' else 'Latency is from the selected final crossover round; content and scores are from the original call.')+' <a href="research/wcxb-full-paired-latency/calls.jsonl">'+('逐次测速' if lang=='zh' else 'Timing calls')+'</a></p>') if r.get('latency_source')=='paired_final_round' else ''
    panels.append(f'<section class="panel" id="{p}"><h2>{esc(p)} · F1 {fmt(r["f1"])}</h2><p><span class="tag">{esc(diag(lang,"excluded" if case in exclusion_ids else r["diagnosis"]))}</span> · {r["total_elapsed_ms"]:,.0f} ms · {r["physical_attempt_count"]} calls · {esc(r["attempt_id"])}</p>'+timing_note+f'<p>{esc(x["missing"])}: {esc(missing)}</p><p>{esc(x["extra"])}: {esc(extra)}</p>'+snippet_detail+(f'<p class="bad">{esc(x["error"])}: {esc(r["error_type"])}{(" · HTTP "+str(r["status_code"])) if r.get("status_code") else ""}{(" · "+esc(r["error_message"])) if r["error_message"] else ""}</p>' if r['error_type'] else '')+retry_html+f'<details data-output><summary>{esc(x["output"])} · {r["output_chars"]:,} characters</summary><p><a href="{evidence_path}">{"完整证据 JSON" if lang=="zh" else "Complete evidence JSON"} ↓</a></p><pre data-src="{evidence_path}" data-provider="{p}"></pre></details></section>')
   qualification=(f'<p class="callout">{"此页参考不合格，保留官方原始分与输出，不计入 504 页主质量均值。" if lang=="zh" else "This reference was excluded before calls; official raw scores and outputs remain visible, but it does not enter the 504-page main quality mean."} {esc(exclusion_ids[case])}</p>' if case in exclusion_ids else '')
   preview=_wmb_html_preview(source_html,lang=='en').replace('class="wmb-html-preview"','class="frozen-preview"')
   raw_input=evidence_path
   preview_note=('这里只按冻结 HTML 的文档顺序展示文字和结构，不是原网页截图。评测集未保存站点样式和图片文件，因此无法从这份输入还原原站排版；脚本、外部资源与跳转均已禁用。' if lang=='zh' else 'This reading view follows the frozen HTML document order; it is not a screenshot of the original website. The dataset does not preserve site stylesheets or image files, so the original layout cannot be reconstructed from this input. Scripts, external resources and navigation are disabled.')
   preview_section=f'<section class="panel" id="preview"><h2>{"冻结 HTML 阅读视图" if lang=="zh" else "Frozen HTML reading view"}</h2><p>{preview_note} <a href="{raw_input}">{"下载完整输入与证据 JSON" if lang=="zh" else "Download complete input and evidence JSON"}</a></p>{preview}</section>'
   body=f'<div class="hero"><h1>{esc(case)} · {esc(type_label(lang,gt["page_type"]))}</h1><p class="source"><a href="{href(lang,"cases/"+case+".html#preview")}" title="{"阅读冻结 HTML" if lang=="zh" else "Read frozen HTML"}">{esc(group[providers[0]]["original_url"])}</a></p></div>{qualification}<section><h2>{esc(x["summary"])}</h2><div class="scroll"><table><thead><tr>'+''.join(f'<th>{esc(x[k])}</th>' for k in ('provider','precision','recall','f1'))+f'<th>{"应包含命中" if lang=="zh" else "Required hits"}</th><th>{"应排除误命中" if lang=="zh" else "Forbidden hits"}</th><th>{esc(x["diagnosis"])}</th></tr></thead><tbody>'+same+f'</tbody></table></div></section>{preview_section}<section class="panel" id="reference"><h2>{esc(x["reference"])}</h2><p><a href="{evidence_path}">{"完整证据 JSON" if lang=="zh" else "Complete evidence JSON"}</a> · <a href="{source_link}">{esc(x["source"])} HTML.gz</a></p><details data-output><summary>{"冻结 HTML 输入" if lang=="zh" else "Frozen HTML input"}</summary><pre data-src="{evidence_path}"></pre></details><pre>{esc(readable_output(reference))}</pre></section>'+''.join(panels)
   page(lang,f'cases/{case}.html',case,body)
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
async function wmbRenderFlow(){let node=document.querySelector('.wmb-flow .mermaid');if(!node||node.dataset.rendering||window.innerWidth<=700)return;node.dataset.rendering='1';try{let mermaid=await wmbLoadMermaid();mermaid.initialize({startOnLoad:false,theme:document.documentElement.dataset.theme==='dark'?'dark':'neutral',flowchart:{htmlLabels:true}});let rendered=await mermaid.render('wmb-evaluation-flow',node.textContent);if(!node.isConnected)return;node.innerHTML=rendered.svg;node.hidden=false;node.closest('.wmb-flow').querySelector('[data-wmb-flow-fallback]').hidden=true}catch(error){node.hidden=true;delete node.dataset.rendering}}
window.addEventListener('report-ready',wmbRenderFlow);
window.addEventListener('DOMContentLoaded',wmbRenderFlow);
window.addEventListener('hashchange',()=>applyReportAnchor(location.hash.slice(1)));
document.addEventListener('click',event=>{let jump=event.target.closest('[data-log-line]');if(jump){let line=document.getElementById(jump.dataset.logLine);line?.scrollIntoView({block:'center'});line?.focus({preventScroll:true});return}let open=event.target.closest('[data-call-log-open]');if(open){openCallLog(open);return}let close=event.target.closest('[data-call-log-close]');if(close)closeCallLog(close)});
'''
EXTRA='''
.wmb-log-focus{background:#684f0a;color:#fff3bb;outline:2px solid #b98b1d;display:block}.wmb-log>span{scroll-margin:20px}.wmb-detail-dialog{width:min(1120px,calc(100vw - 24px));max-height:90vh;padding:0;border:1px solid var(--line);border-radius:16px;background:var(--panel);color:var(--ink)}.wmb-detail-dialog::backdrop{background:#0009}.wmb-detail-dialog .call-log-shell>header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel);z-index:1}.wmb-detail-dialog h2{font-size:24px;margin:0;overflow-wrap:anywhere}.wmb-dialog-content{padding:20px;min-width:0}.wmb-detail-dialog pre{max-height:60vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--code-bg);color:var(--code-ink);padding:16px;border-radius:8px}.call-log-button,.wmb-detail-dialog [data-call-log-close]{font:inherit;color:var(--accent);background:var(--panel);border:1px solid var(--line);padding:8px 12px;border-radius:8px;cursor:pointer}.call-log-button:focus-visible,.wmb-detail-dialog button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}.wmb-evidence-actions{display:flex;gap:12px;flex-wrap:wrap}.wmb-diagnosis{display:grid;grid-template-columns:150px 1fr;gap:12px}.wmb-diagnosis dt{font-weight:700}.wmb-diagnosis dd{margin:0;overflow-wrap:anywhere}.wmb-detail-scores{min-width:650px}.wmb-detail-scores th,.wmb-detail-scores td{white-space:nowrap}.wmb-detail-dialog .evidence-compare{min-width:0}.wmb-detail-dialog .evidence-pane{min-width:0}@media(max-width:700px){.wmb-diagnosis{grid-template-columns:1fr;gap:6px}.wmb-diagnosis dd{margin-bottom:14px}.wmb-detail-dialog .evidence-compare{display:grid;grid-template-columns:1fr}.wmb-dialog-content{padding:12px}.wmb-detail-dialog .call-log-shell>header{padding:12px}.wmb-detail-dialog h2{font-size:20px}}
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
.wmb-profile-table{min-width:2250px}.wmb-profile-table th:nth-child(8),.wmb-profile-table td:nth-child(8){width:150px;white-space:nowrap}.wmb-profile-table th:nth-child(9),.wmb-profile-table td:nth-child(9){width:450px;white-space:normal}
.wmb-failure-detail-table{min-width:1160px;table-layout:fixed}.wmb-failure-detail-table th:nth-child(1),.wmb-failure-detail-table td:nth-child(1){width:140px}.wmb-failure-detail-table th:nth-child(2),.wmb-failure-detail-table td:nth-child(2){width:350px}.wmb-failure-detail-table th:nth-child(3),.wmb-failure-detail-table td:nth-child(3){width:250px}.wmb-failure-detail-table td{vertical-align:top;overflow-wrap:anywhere}
.metric-value{display:block}.tiny{display:block;color:var(--muted);font-size:11px;font-weight:500;margin-top:3px}.wmb-core-table{min-width:1280px;white-space:nowrap}.wmb-distribution-controls{display:grid;grid-template-columns:minmax(220px,360px) 1fr;gap:16px;align-items:end;margin:18px 0}.wmb-distribution-controls label{display:grid;gap:6px;font-weight:700;color:var(--muted)}.wmb-distribution-controls select{padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink);font:inherit}.wmb-flow{overflow:auto;border:1px solid var(--line);border-radius:16px;background:var(--panel);padding:18px}.wmb-flow .mermaid{min-width:980px}.wmb-flow-fallback{display:flex;gap:8px;align-items:center;min-width:900px}.wmb-flow-fallback span{padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel2)}.wmb-flow-fallback b{color:var(--accent)}@media(max-width:700px){.wmb-distribution-controls{grid-template-columns:1fr}.wmb-flow{padding:12px}.wmb-flow-fallback{min-width:0;flex-direction:column;align-items:stretch}.wmb-flow-fallback span{width:100%}}
'''

EXTRA+='''
.wmb-editorial{max-width:860px}.wmb-editorial .essay-head{max-width:820px}.wmb-editorial .lead strong{color:var(--good)}.wmb-verdict-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:24px 0}.wmb-verdict{padding:18px;border:1px solid var(--line);border-top:4px solid var(--accent);border-radius:13px;background:var(--panel)}.wmb-verdict:nth-child(2){border-top-color:var(--violet)}.wmb-verdict:nth-child(3){border-top-color:var(--lime)}.wmb-verdict b,.wmb-verdict span{display:block}.wmb-verdict b{font-size:20px}.wmb-verdict span{margin-top:7px;color:var(--muted);font-size:13px;line-height:1.65}.wmb-score-formula{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:24px 0}.wmb-score-part{padding:14px 10px;border:1px solid var(--line);border-radius:11px;background:var(--panel);text-align:center}.wmb-score-part b,.wmb-score-part span,.wmb-score-part small{display:block}.wmb-score-part b{font-size:21px;color:var(--accent)}.wmb-score-part span{font-size:12px;font-weight:700}.wmb-score-part small{margin-top:5px;color:var(--muted)}.wmb-score-result{grid-column:1/-1;padding:14px 16px;border-radius:11px;background:var(--accent-soft);font-weight:750;text-align:center}.wmb-case-study{margin:26px 0;padding:22px;border:1px solid var(--line);border-radius:15px;background:var(--panel)}.wmb-case-study>header{display:flex;align-items:start;justify-content:space-between;gap:18px;margin-bottom:16px}.wmb-case-study h3{margin:0}.wmb-case-study>header p{margin:5px 0 0;color:var(--muted);font-size:13px}.wmb-case-study>header a{white-space:nowrap;font-size:13px}.wmb-evidence-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.wmb-evidence-pair article{min-width:0;padding:15px;border-radius:10px;background:var(--panel2)}.wmb-evidence-pair h4{margin:0 0 8px}.wmb-evidence-pair pre{margin:0;max-height:260px;font-size:12px;line-height:1.6}.wmb-case-stats{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.wmb-case-stats span{padding:5px 9px;border-radius:999px;background:var(--accent-soft);font-size:12px}.wmb-cause-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:22px 0}.wmb-cause-grid article{padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}.wmb-cause-grid b{display:block;font-size:24px;color:var(--accent)}.wmb-cause-grid span{display:block;margin-top:6px;font-weight:700}.wmb-cause-grid p{margin:7px 0 0;color:var(--muted);font-size:13px;line-height:1.65}.wmb-choice-table{min-width:760px}.wmb-choice-table td:nth-child(2){font-weight:750}.wmb-editorial .source-note a{font-weight:650}@media(max-width:720px){.wmb-verdict-grid,.wmb-score-formula,.wmb-cause-grid,.wmb-evidence-pair{grid-template-columns:1fr}.wmb-score-result{grid-column:auto}.wmb-case-study{padding:16px}.wmb-case-study>header{display:block}.wmb-case-study>header a{display:inline-block;margin-top:8px}.wmb-editorial .editorial-table table{min-width:760px}}
'''

EXTRA+='''
.wmb-html-preview{display:block;width:100%;height:65vh;min-height:360px;border:1px solid var(--line);border-radius:12px;background:white}.wmb-scope-map,.wmb-table-examples{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.wmb-scope-map article,.wmb-table-examples article{min-width:0;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:12px}.wmb-scope-map b{color:var(--accent)}.wmb-scope-map p,.wmb-table-examples p{font-size:14px;line-height:1.65;margin-bottom:0}.wmb-table-examples pre{height:150px;overflow:auto;font-size:12px}.wmb-table-render{min-height:145px;overflow:auto}.wmb-table-render table{width:100%;min-width:0}.wmb-table-render th,.wmb-table-render td{padding:8px;border:1px solid var(--line)}.wmb-table-examples h3{font-size:18px;margin:0 0 14px}.wmb-input-routes{display:grid;grid-template-columns:1fr 1fr;gap:14px}.wmb-input-routes article{border-left:4px solid var(--accent);background:var(--panel);padding:16px}.wmb-input-routes code{display:block;overflow-wrap:anywhere;margin:12px 0}.wmb-source-pre{max-height:60vh;overflow:auto}@media(max-width:720px){.wmb-scope-map,.wmb-table-examples,.wmb-input-routes{grid-template-columns:1fr}}
'''

EXTRA+='''
.wmb-reading-key{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0;padding:16px;background:var(--accent-soft);border-radius:12px}.wmb-reading-key span{flex:1 1 280px;font-size:14px;line-height:1.7}.wmb-reading-key b{display:block}.wmb-latency-population{padding:12px 16px;background:var(--accent-soft);border-radius:10px;font-size:14px}.wmb-guide-toc{display:flex;flex-wrap:wrap;gap:10px 22px;margin:30px 0;padding:20px 0;border-block:1px solid var(--line)}.wmb-guide-toc a{font-size:14px;font-weight:650}.wmb-guide-results table{min-width:850px}.wmb-guide-results td{white-space:nowrap}.wmb-guide-results small{display:block;margin-top:5px;color:var(--muted);white-space:nowrap;font-size:12px}.wmb-guide-latency{min-width:640px}.wmb-version-case summary{cursor:pointer;font-size:16px;font-weight:700;line-height:1.6}.wmb-version-case[open] summary{margin-bottom:14px}.wmb-editorial section[id]{scroll-margin-top:90px}.wmb-editorial table{min-width:680px}.wmb-editorial th,.wmb-editorial td:nth-child(2){white-space:nowrap}.wmb-editorial #version-differences table{min-width:760px}.wmb-editorial .wmb-evidence-pair pre{white-space:pre-wrap;overflow-wrap:anywhere}.wmb-editorial .wmb-score-formula{grid-template-columns:repeat(5,minmax(0,1fr))}@media(max-width:720px){.wmb-editorial section[id]{scroll-margin-top:180px}.wmb-editorial .wmb-score-formula{grid-template-columns:repeat(2,minmax(0,1fr))}.wmb-editorial .wmb-score-result{grid-column:1/-1}.wmb-guide-toc{gap:10px 15px}}
.wmb-denominator-example{margin:24px 0 4px}.wmb-denominator-example h3{margin:0 0 12px;font-size:20px}.wmb-denominator-example .wmb-flow{margin-bottom:12px}.wmb-denominator-matrix{width:100%;min-width:580px;border-collapse:collapse;background:var(--panel)}.wmb-denominator-matrix th,.wmb-denominator-matrix td{border:1px solid var(--line);padding:12px;text-align:left}.wmb-denominator-matrix thead th{background:var(--panel2)}.wmb-denominator-matrix tbody td:first-child{width:24%;font-weight:700}.wmb-denominator-matrix td b{display:block;font-size:24px;color:var(--accent)}.wmb-denominator-matrix td span{display:block;font-size:13px;color:var(--muted)}.wmb-denominator-example .wmb-flow-fallback{min-width:0;flex-wrap:wrap}.wmb-denominator-example .wmb-flow-fallback span{flex:1 1 220px}.wmb-denominator-example .wmb-flow .mermaid{min-width:0}.wmb-version-cases{display:grid;gap:12px;margin:18px 0}.wmb-version-case{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:16px}.wmb-version-case h3{margin:0 0 8px;font-size:17px}.wmb-version-case p{margin:8px 0;line-height:1.65}.wmb-version-case .actions{font-size:13px}
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
_ENGLISH_GENERATED=False

def write(out,path,title,body):
 depth=len(Path(path).parts)-1;base='../'*depth
 nav=[('datasets/index.html','评测集'),('methodology/index.html','评测方法'),('analysis/index.html','结果分析'),('review/index.html','核验记录'),('guide/index.html','报告解读'),('adoption/index.html','方法介绍'),('reproduce/index.html','环境与复现'),('changes/index.html','变更记录'),('accounts/index.html','账号与安装')]
 brand=link(base+'index.html','Web Fetch')
 counterpart=(base+'en/'+path if _ENGLISH_GENERATED else base+'en/index.html')
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

def generate_public_benchmark_research(out:Path,research:dict,*,include_english:bool=False):
 """Render the research-only public benchmark report with no measured-result paths."""
 if research.get('status')!='research_only' or research.get('measured_results')!=0:
  raise ValueError('Public benchmark research must explicitly contain zero measured results')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA,encoding='utf-8')
 (out/'assets/report.js').write_text(JS,encoding='utf-8')
 from .report_changes import localized_release_history, render_localized_release_history, ROOT
 release_history=localized_release_history(
  json.loads((ROOT/'research/report-changes.json').read_text(encoding='utf-8')),
  'public_benchmark_research')
 (out/'report-changes.json').write_text(
  json.dumps({'schema_version':2,'releases':release_history},ensure_ascii=False,indent=2)+'\n',
  encoding='utf-8')
 direct=research['direct_benchmarks'];adjacent=research['adjacent_browser_benchmarks']
 for english in ((False,True) if include_english else (False,)):
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

  changes=render_localized_release_history(
   {'releases':release_history},english,kind='public_benchmark_research')
  _research_write(out,'changes/index.html',h('变更记录','Changes'),changes,english)

  accounts=_research_hero(h('账号与安装','Accounts and setup'),h('调研版不需要供应商账号。','The research edition needs no provider account.'),english)
  accounts+=section(h('当前要求','Current requirement'),f'<p class="callout success">{h("只读取已提交的调研与样本清单 JSON；不读取 .env、Token、Cookie 或个人登录态。","Only the committed research and sample-manifest JSON files are read; no .env, token, cookie or personal login state is used.")}</p>')
  accounts+=section(h('实测阶段','Measurement phase'),f'<p>{h("届时按方法单独列出官方安装入口、版本和所需环境变量名，不公开值。","The measured edition will list official installation sources, versions and required environment-variable names per method, never their values.")}</p>')
  _research_write(out,'accounts/index.html',h('账号与安装','Accounts and setup'),accounts,english)
 (out/'research.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 from harness.report_languages import write_language_contract
 write_language_contract(out,include_english=include_english)
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

def generate(out:Path,dataset:dict,records:list,manifests:list,*,include_english:bool=False):
 global _RELIABILITY_BANNER,_ENGLISH_GENERATED
 _ENGLISH_GENERATED=include_english
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
 if include_english:
  from .report_i18n import write_english_report
  write_english_report(out)
 from harness.report_languages import write_language_contract
 write_language_contract(out,include_english=include_english)
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


def _wmb_html_preview(source,english=False):
 from bs4 import BeautifulSoup
 soup=BeautifulSoup(source,'html.parser')
 # This is an inert reading view, never a replay of untrusted website scripts.
 for node in soup.select('script,iframe,object,embed,base,meta,link,svg,math,video,audio,source'):
  node.decompose()
 for node in soup.find_all(True):
  for attr in list(node.attrs):
   if attr.lower().startswith('on') or attr.lower() in {'src','srcset','href','action','formaction','background','ping','xlink:href','srcdoc'}:
    del node.attrs[attr]
 for img in soup.find_all('img'):
  placeholder=soup.new_tag('span')
  placeholder.string=('[Image not loaded' if english else '［图片未加载')+(': '+img.get('alt','') if img.get('alt') else '')+(']' if english else '］')
  img.replace_with(placeholder)
 policy="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"
 document='<meta http-equiv="Content-Security-Policy" content="'+html.escape(policy,quote=True)+'"><style>body{margin:20px;color:#172331;background:white;overflow-wrap:anywhere}img,table{max-width:100%}pre{white-space:pre-wrap}</style>'+str(soup)
 title='Frozen HTML reading view' if english else '冻结 HTML 阅读视图'
 return '<iframe class="wmb-html-preview" sandbox="" referrerpolicy="no-referrer" loading="lazy" title="'+title+'" srcdoc="'+html.escape(document,quote=True)+'"></iframe>'


def _wmb_table_example(english=False):
 h=lambda zh,en:en if english else zh
 examples=[
  (h('可识别，内容完整','Recognized, complete'), '| Name | Value |\n| --- | --- |\n| A | 10 |\n| B | 20 |', '<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>20</td></tr></table>',h('进入表格比较；与参考完全相同时为满分。','Enters table comparison; an exact reference match scores full marks.')),
  (h('可识别，但少一行','Recognized, one row missing'), '| Name | Value |\n| --- | --- |\n| A | 10 |', '<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr></table>',h('仍然计分；缺失行和内容会扣分，不要求先完整恢复。','Still scored; missing content and structure reduce the score. Complete recovery is not a prerequisite.')),
  (h('文字存在，表格未识别','Text exists, table not recognized'), 'Name Value A 10 B 20', '<p>Name Value A 10 B 20</p>',h('若参考有表格，两项表格分均为 0；这些文字仍参与 text_edit。','With a table in the reference, both table scores are zero; this text still contributes to text_edit.')),
 ]
 return '<div class="wmb-table-examples">'+''.join('<article><h3>'+title+'</h3><pre>'+esc(md)+'</pre><div class="wmb-table-render">'+rendered+'</div><p>'+note+'</p></article>' for title,md,rendered,note in examples)+'</div><p class="source-note">'+h('以上为规则示意，不是新增实测数据。网页看起来像表格还不够：评分器必须从输出中识别到表格。table_edit 比较规范化后的表格 HTML 字符串，TEDS 比较行、单元格及内容；两项都受结构缺失影响。','These are rule illustrations, not new measurements. A table-like appearance is insufficient: the scorer must recognize a table in the output. Table_edit compares normalized table HTML strings; TEDS compares rows, cells and content. Missing structure affects both.')+'</p>'


def _wmb_denominator_example(records, english, case_count):
 if not any(row['provider']=='moli-1.1.7' for row in records):return ''
 h=lambda zh,en:en if english else zh
 counts=Counter()
 for row in records:
  if row['provider']!='moli-1.1.7':
   continue
  metric=row.get('metrics',{}).get('formula_edit')
  if not row['_body_present']:
   counts['no_body']+=1
  elif metric and metric.get('success'):
   detail=metric['details']
   counts[(bool(detail['groundtruth_formula_length']),bool(detail['predicted_formula_length']))]+=1
  elif metric and metric.get('error')=='Both predicted and groundtruth are empty':
   counts[(False,False)]+=1
  else:
   raise ValueError('Unclassified formula denominator outcome: '+row['attempt_id'])
 scored=sum(value for key,value in counts.items() if isinstance(key,tuple) and key!=(False,False))
 if sum(counts.values())!=case_count or scored!=counts[(False,True)]+counts[(True,False)]+counts[(True,True)]:
  raise ValueError('Formula denominator example does not cover the frozen cohort')
 flow_source=h(
  'flowchart LR\n A["200 页固定输入"] --> B{"最终调用成功？"}\n B -- "否：1 页" --> X["不参与质量均值"]\n B -- "是：199 页" --> C{"两侧都无公式？"}\n C -- "是：51 页" --> Y["跳过公式分"]\n C -- "否：148 页" --> Z["算分并计入平均；含 0 分"]',
  'flowchart LR\n A["200 frozen pages"] --> B{"Final call succeeded?"}\n B -- "No: 1 page" --> X["Outside quality means"]\n B -- "Yes: 199 pages" --> C{"No formula on either side?"}\n C -- "Yes: 51 pages" --> Y["Skip formula component"]\n C -- "No: 148 pages" --> Z["Score and include, even if zero"]')
 fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback><span>'+h('200 页固定输入','200 frozen pages')+'</span><span>'+h('199 页调用成功；1 页调用失败，不评分','199 calls succeeded; 1 failed and is not scored')+'</span><span>'+h(f'{counts[(False,False)]} 页两侧无公式，跳过；其余 {scored} 页计入公式均值，包括 0 分',f'{counts[(False,False)]} pages have no formula on either side and are skipped; the other {scored} enter the formula mean, including zeros')+'</span></div>'
 matrix='<div class="table-wrap"><table class="wmb-denominator-matrix"><thead><tr><th>'+h('人工答案 / 方法输出','Reference / output')+'</th><th>'+h('输出无公式','No output formula')+'</th><th>'+h('输出有公式','Output formula present')+'</th></tr></thead><tbody>'
 matrix+='<tr><td>'+h('答案无公式','No reference formula')+'</td><td><b>'+str(counts[(False,False)])+'</b><span>'+h('两侧空：跳过','Both empty: skip')+'</span></td><td><b>'+str(counts[(False,True)])+'</b><span>'+h('只在输出：0 分计入','Output only: zero enters')+'</span></td></tr>'
 matrix+='<tr><td>'+h('答案有公式','Reference formula present')+'</td><td><b>'+str(counts[(True,False)])+'</b><span>'+h('只在答案：0 分计入','Reference only: zero enters')+'</span></td><td><b>'+str(counts[(True,True)])+'</b><span>'+h('两侧都有：相似度计入','Both present: similarity enters')+'</span></td></tr></tbody></table></div>'
 return '<div class="wmb-denominator-example"><h3 id="denominator-example">'+h('看懂“计入 148/200 页”：以 Moli 1.1.7 公式分为例','What “scored on 148/200 pages” means: Moli 1.1.7 formulas')+'</h3><div class="wmb-flow">'+fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'+matrix+'<p class="source-note">'+h(f'公式分只平均右上、左下、右下共 {scored} 页；其中 {counts[(False,True)]+counts[(True,False)]} 页计 0 分。分母由两份 Markdown 中识别出的公式决定，不是“有正文的页数”，也不是“答对的页数”。',f'The formula mean uses the upper-right, lower-left and lower-right cells: {scored} pages, including {counts[(False,True)]+counts[(True,False)]} zero scores. Recognition in the two Markdown files determines the denominator; it is neither the body-text count nor a count of correct pages.')+'</p></div>'


def _wmb_version_analysis(english, bycase, score, denom):
 h=lambda zh,en:en if english else zh
 def pair(case_id):
  rows={row['provider']:row for row in bycase[case_id]}
  return rows['moli'],rows['moli-1.1.7']
 def evidence(case_id):
  old,new=pair(case_id)
  return '<p class="actions">'+link('../attempts/'+old['attempt_id']+'.html',h('1.1.5 输出与评分','1.1.5 output and score'))+' · '+link('../attempts/'+new['attempt_id']+'.html',h('1.1.7 输出与评分','1.1.7 output and score'))+' · '+link('../datasets/'+case_id+'.html',h('同页人工答案','Same-page reference'))+'</p>'
 def card(title,body,case_id):
  return '<article class="wmb-version-case"><h3>'+title+'</h3><p>'+body+'</p>'+evidence(case_id)+'</article>'
 case_text='357c9ba1-1c6c-4d8f-a75e-0bc4a3bc9b3c'
 case_code='14f8fe79-f899-4074-9878-1bf9f301ad95'
 case_code_new='63cfc5dd-1737-485f-9bc9-cacc65299ba8'
 case_formula='ac11d851-aed7-4b9e-89d1-39c142fa152d'
 case_false_formula='0b7ac530-19ea-4a14-a8bd-adb9cd646351'
 text_old,text_new=pair(case_text)
 code_old,code_new=pair(case_code)
 formula_old,formula_new=pair(case_formula)
 false_old,false_new=pair(case_false_formula)
 shared_code=[];shared_formula=[];new_only_formula=[];text_longer=0;text_lower=0;code_new_zero=[]
 for rows in bycase.values():
  versions={row['provider']:row for row in rows}
  old,new=versions['moli'],versions['moli-1.1.7']
  before_text=old['metrics'].get('text_edit',{});after_text=new['metrics'].get('text_edit',{})
  if before_text.get('success') and after_text.get('success'):
   text_longer+=new['output_chars']>old['output_chars']
   text_lower+=after_text['score']<before_text['score']
  for metric,shared in (('code_edit',shared_code),('formula_edit',shared_formula)):
   before=old['metrics'].get(metric,{});after=new['metrics'].get(metric,{})
   if before.get('success') and after.get('success'):
    shared.append(after['score'])
  before=old['metrics'].get('formula_edit',{});after=new['metrics'].get('formula_edit',{})
  if not before.get('success') and after.get('success'):
   new_only_formula.append(after['score'])
  before_code=old['metrics'].get('code_edit',{});after_code=new['metrics'].get('code_edit',{})
  if not before_code.get('success') and after_code.get('success'):
   code_new_zero.append(after_code['score'])
 if (len(shared_code)!=40 or len(shared_formula)!=97 or len(new_only_formula)!=51
     or len(code_new_zero)!=2 or any(code_new_zero) or any(new_only_formula)
     or text_longer!=184 or text_lower!=171):
  raise ValueError('Moli version-analysis scope changed; review the paired interpretation')
 rows=[
  ('',cell('text_edit')+cell(_wmb_pct(score('moli','text_edit'))+' · '+str(denom('moli','text_edit')))+cell(_wmb_pct(score('moli-1.1.7','text_edit'))+' · '+str(denom('moli-1.1.7','text_edit')))+cell(h('同 199 页下降 1.0 个百分点','Down 1.0 point on the same 199 pages'))),
  ('',cell('code_edit')+cell(_wmb_pct(score('moli','code_edit'))+' · '+str(denom('moli','code_edit')))+cell(_wmb_pct(score('moli-1.1.7','code_edit'))+' · '+str(denom('moli-1.1.7','code_edit')))+cell(h(f'旧版计入的 40 页，新版均值 {_wmb_pct(statistics.fmean(shared_code))}；另有 2 页以 0 分计入',f'On the older 40 scored pages, the new mean is {_wmb_pct(statistics.fmean(shared_code))}; two added pages enter with zero'))),
  ('',cell('formula_edit')+cell(_wmb_pct(score('moli','formula_edit'))+' · '+str(denom('moli','formula_edit')))+cell(_wmb_pct(score('moli-1.1.7','formula_edit'))+' · '+str(denom('moli-1.1.7','formula_edit')))+cell(h(f'旧版计入的 97 页，新版均值 {_wmb_pct(statistics.fmean(shared_formula))}；另有 51 页以 0 分计入',f'On the older 97 scored pages, the new mean is {_wmb_pct(statistics.fmean(shared_formula))}; 51 added pages enter with zero'))),
 ]
 body='<p>'+h(f'共同变化是 1.1.7 输出更长、Markdown 排版和转义方式变化：文字分共同计入的 199 页里，{text_longer} 页输出变长，{text_lower} 页分数下降。缩进段落会被代码提取器当作代码；被转义的公式可能漏识别，转义网址也可能误识别成公式。以下是逐页证据。',f'The shared change is longer output and different Markdown layout and escaping in 1.1.7. Of 199 pages scored for text in both versions, {text_longer} outputs grow longer and {text_lower} text scores fall. The code extractor can read indented prose as code; the formula extractor can miss escaped math or mistake escaped URLs for math. Page evidence follows.')+'</p>'+table([h('指标','Metric'),'Moli 1.1.5<br>'+h('分数 · 计入页','score · pages'),'Moli 1.1.7<br>'+h('分数 · 计入页','score · pages'),h('同页核对','Same-page check')],rows,metrics=[])
 body+='<div class="wmb-version-cases">'
 body+=card(h('正文：额外页面内容拉低相似度','Text: extra page content reduces similarity'),h(f'服装网站的人工主内容为 5,094 字符；旧版输出 {text_old["output_chars"]:,} 字符，text_edit {_wmb_pct(text_old["metrics"]["text_edit"]["score"])}；新版展开更多菜单、商品介绍和链接标题，输出 {text_new["output_chars"]:,} 字符，得分 {_wmb_pct(text_new["metrics"]["text_edit"]["score"])}。这是完整输出与主内容参考范围不一致的实测差异。',f'The clothing-page reference has 5,094 characters. The older output has {text_old["output_chars"]:,} characters and scores {_wmb_pct(text_old["metrics"]["text_edit"]["score"])}. The newer output expands more menu, product and link-title text to {text_new["output_chars"]:,} characters and scores {_wmb_pct(text_new["metrics"]["text_edit"]["score"])}. The measured output contains much more than the main-content reference.'),case_text)
 body+=card(h('代码：评分器把额外缩进文字也当作代码','Code: extra indented prose enters the code extractor'),h(f'书籍页面的人工答案把简介放在代码围栏中；旧版识别出 867 字符，得 {_wmb_pct(code_old["metrics"]["code_edit"]["score"])}；新版还把其他缩进文字识别为代码，共 {code_new["metrics"]["code_edit"]["details"]["predicted_code_length"]:,} 字符，得 {_wmb_pct(code_new["metrics"]["code_edit"]["score"])}。这里的“代码分”含有书籍文案，不能解释为程序代码生成能力。',f'The book reference fences its synopsis as code. The older extraction is 867 characters and scores {_wmb_pct(code_old["metrics"]["code_edit"]["score"])}; the new extraction also captures other indented prose, reaching {code_new["metrics"]["code_edit"]["details"]["predicted_code_length"]:,} characters and scoring {_wmb_pct(code_new["metrics"]["code_edit"]["score"])}. This code metric includes book copy and is not a programming-capability result.'),case_code)
 body+=card(h('代码分母：缩进的普通文字新计入零分','Code denominator: indented ordinary text adds a zero'),h('天涯页面的人工答案没有代码；旧版输出也没有被识别的代码，该页跳过。新版把大量普通段落缩进，官方提取器将其中 3,867 字符识别为代码，因此以 0 分计入。另一个新增代码计分页也以 0 分计入。','The Tianya reference has no code, and the older output has no recognized code, so the component skips it. In 1.1.7, many ordinary paragraphs are indented; the pinned extractor reads 3,867 characters as code and includes this page with a zero. The other newly scored code page also scores zero.'),case_code_new)
 body+=card(h('公式：公式仍在输出中，但定界符被转义','Formulas: present in the output, but delimiters are escaped'),h(f'Overleaf 页面旧版的公式提取长度为 840 字符，得 {_wmb_pct(formula_old["metrics"]["formula_edit"]["score"])}。新版仍保留公式内容，却把反斜线和定界符转义；固定版本评分器的正则未识别出公式，提取长度为 0，得 {_wmb_pct(formula_new["metrics"]["formula_edit"]["score"])}。该页下降来自定界符被转义后，评分器漏识别公式。',f'On the Overleaf page, the older output yields 840 extracted formula characters and scores {_wmb_pct(formula_old["metrics"]["formula_edit"]["score"])}. The new output still contains the formulas but escapes backslashes and delimiters. The pinned scorer regex extracts zero characters, producing {_wmb_pct(formula_new["metrics"]["formula_edit"]["score"])}. The score falls because the scorer misses formulas with the escaped delimiters.'),case_formula)
 body+=card(h('公式分母：网址转义被误认成公式','Formula denominator: an escaped URL is mistaken for math'),h('新华网页面的人工答案没有公式。旧版输出也未识别出公式，所以该页跳过；新版把网址中的 <code>javascript:void(0)</code> 写成含转义括号的 Markdown，评分器从中提取出“0”，该页以 0 分加入公式均值。新增的 51 个公式计分页全部为 0 分。','The Xinhua reference has no formula. The older output is also formula-free, so the component skips this page. The new Markdown escapes parentheses in a <code>javascript:void(0)</code> URL; the scorer extracts “0” as a formula and includes this page with a zero. All 51 added formula-scored pages have zero scores.'),case_false_formula)
 body+='</div><p class="source-note">'+h('因此：本轮 text_edit 的同页分数确实下降；code_edit 的均值变化主要受 2 个新增零分页影响；formula_edit 同时受定界符转义和新增的误识别零分页影响。以上保留官方固定评分规则与原分数，解释的是这轮输出和规则如何共同形成结果。','In this run, text scores do fall on matched pages. The code mean is driven mainly by two newly included zero pages. The formula mean reflects escaped delimiters and newly included false-positive zero pages. These observations explain the frozen output under the pinned scorer; the recorded scores remain unchanged.')+'</p>'
 return section(h('Moli 1.1.7 其他分项为何变低','Why other Moli 1.1.7 component scores fall'),body,'version-differences')


def _wmb_latency_summary(records,names):
 """Final successful fetch time; retain all rows and historical execution cost."""
 by_method={name:[r for r in records if r['provider']==name] for name in names}
 successful={name:[r for r in rows if r['_body_present']] for name,rows in by_method.items()}
 common=set.intersection(*(set(r['case_id'] for r in rows) for rows in successful.values())) if names else set()
 def stats(values):
  return {'count':len(values),'mean':statistics.fmean(values) if values else None,'p50':statistics.median(values) if values else None,'p95':percentile(values,.95) if values else None}
 return {'common_cases':common,
         'successful':{name:stats([r['elapsed_ms'] for r in rows]) for name,rows in successful.items()},
         'matched':{name:stats([r['elapsed_ms'] for r in rows if r['case_id'] in common]) for name,rows in successful.items()},
         'cost':{name:stats([r['total_elapsed_ms'] for r in rows]) for name,rows in by_method.items()}}


def _wmb_table_population(records,names,labels,english=False,prefix='../'):
 h=lambda zh,en:en if english else zh
 reference_pages={r['case_id'] for r in records if r.get('metrics',{}).get('table_edit',{}).get('details',{}).get('groundtruth_table_length',0)>0}
 rows=[]
 for name in names:
  eligible=extra=0;example=None
  for r in records:
   if r['provider']!=name or not r.get('ok'):continue
   detail=r.get('metrics',{}).get('table_edit',{}).get('details',{})
   gt=detail.get('groundtruth_table_length',0)>0;pred=detail.get('predicted_table_length',0)>0
   eligible+=gt
   if pred and not gt:extra+=1;example=example or r['attempt_id']
  rows.append(('',cell(link(prefix+'providers/'+name+'.html',labels[name]))+cell(str(eligible),eligible)+cell(str(extra),extra)+cell('<b>'+str(eligible+extra)+'</b>',eligible+extra)+cell(link(prefix+'attempts/'+example+'.html',h('查看输出独有表格','Inspect output-only tables')) if example else '—')))
 return '<div class="wmb-table-population"><h3>'+h('为什么表格计分页数不同？','Why do table-scoring counts differ?')+'</h3><p>'+h(f'每页都有人工答案，但只有 {len(reference_pages)} 页的答案含可识别表格。调用成功后，答案有表格的页都会计分；答案无表格、输出却识别出表格的页也会以 0 分计入。两侧都无表格才跳过。',f'Every page has a human reference, but only {len(reference_pages)} references contain recognizable tables. After a successful call, every reference-table page is scored. A page with tables only in the output also enters the average at zero. Only pages with no tables on either side are skipped.')+'</p>'+table([h('方法','Method'),h('答案有表格且调用成功','Reference table, successful call'),h('仅输出有表格','Output-only table'),h('计分页数（前两列相加）','Scored pages (sum)'),h('原始证据','Raw evidence')],rows,cls='wmb-table-population-table',metrics=[])+'<p class="source-note">'+h('“仅输出有表格”可能来自人工主内容答案排除的页面区域，或结构被识别成表格；不是人工答案缺失。Kitesurf 的两个含表格页面调用失败，未送入评分。','Output-only tables may come from page regions excluded from the main-content reference, or structures parsed as tables; the human reference is not missing. Two Kitesurf calls on reference-table pages failed and were excluded before scoring.')+'</p></div>'


def _wmb_timing_cell(stats,name,key):
 value=stats[name][key]
 values=[item[key] for item in stats.values() if item[key] is not None]
 minimum=min(values) if values else None
 marked=value is not None and value==minimum and values.count(minimum)==1
 content=ms(value)
 if marked:content='<strong class="comparison-winner">'+content+'</strong>'
 return cell(content,value).replace('<td', '<td data-metric-direction="min" data-metric="'+key+'"',1)


def _wmb_timing_note(english=False,matched=False):
 if matched:return ("Green marks the unique minimum in each column. P50 is the median; P95 is the time within which 95% finish." if english else "绿色标出本列唯一最低耗时。P50：一半在此时间内完成；P95：95% 在此时间内完成。")
 return ('Green marks the unique maximum score or minimum duration in this column. Timing uses final successful fetches only. P50: half finish within this time; P95: 95% finish within this time. Success counts define the timing population; scoring counts do not.' if english else '绿色标出本列唯一最高分或最低耗时。三项耗时均仅统计最终成功的 fetch；P50：一半在此时间内完成，P95：95% 在此时间内完成。计时页数见成功页数，与各项计分页数无关。')


def _wmb_matched_latency(records,names,labels,english=False,prefix='../'):
 h=lambda zh,en:en if english else zh
 data=_wmb_latency_summary(records,names);common=data['common_cases']
 excluded=sorted({r['case_id'] for r in records}-common)
 rows=[]
 for name in names:
  stat=data['matched'][name]
  rows.append(('',cell(link(prefix+'providers/'+name+'.html',labels[name]))+''.join(_wmb_timing_cell(data['matched'],name,key) for key in ('mean','p50','p95'))))
 return '<div class="wmb-matched-latency"><h3>'+h(f'同题耗时对照：共同成功的 {len(common)} 页',f'Matched-page timing: {len(common)} pages with success across all methods')+'</h3><p>'+h(f'以下每行使用完全相同的 {len(common)} 页，只取最终成功 fetch 的单次耗时。它用于检查同页速度差异；共同成功页是在运行后确定的，所以不据此给整个 200 页评测排速度名次。',f'Every row uses the same {len(common)} pages and the duration of the final successful fetch only. This diagnoses same-page timing differences. The shared-success subset is determined after execution, so it does not rank speed over the entire 200-page benchmark.')+'</p><p class="source-note">'+_wmb_timing_note(english,matched=True)+'</p>'+table([h('方法','Method'),h('均值耗时','Mean latency'),h('P50 耗时','P50 latency'),h('P95 耗时','P95 latency')],rows,cls='wmb-matched-latency-table',metrics=[])+'<details><summary>'+h(f'未进入同题对照的 {len(excluded)} 页（仍保留在完整结果中）',f'{len(excluded)} pages outside this comparison (retained in the full matrix)')+'</summary><ul>'+''.join('<li>'+link(prefix+'datasets/'+case_id+'.html',case_id)+'</li>' for case_id in excluded)+'</ul></details></div>'


def _wmb_latency_rerun(diagnostic,english=False,prefix='',formal_round=None):
 h=lambda zh,en:en if english else zh
 if not diagnostic:return ''
 runs=diagnostic['balanced_200']['runs'];tail=diagnostic['tail_repeat'];resources=diagnostic['resource']
 tail_count=len(tail['cases'])
 tail_outliers=[row for row in tail['cases']
                if abs(row['older_median_ms']-row['newer_median_ms'])>20]
 tail_newer_faster=sum(row['newer_median_ms']<row['older_median_ms'] for row in tail_outliers)
 tail_older_faster=len(tail_outliers)-tail_newer_faster
 labels={'1.1.5':'Moli 1.1.5','1.1.7':'Moli 1.1.7'}
 def timing_cell(version,key):
  value=runs[version][key];other='1.1.7' if version=='1.1.5' else '1.1.5'
  content='<strong class="comparison-winner">'+ms(value)+'</strong>' if value<runs[other][key] else ms(value)
  return cell(content,value).replace('<td',f'<td data-metric="rerun-{key}" data-metric-direction="min"',1)
 rows=[('',cell(labels[version])+cell(str(runs[version]['n'])+'/'+str(runs[version]['calls']))+
        ''.join(timing_cell(version,key) for key in ('mean','p50','p95')))
       for version in ('1.1.5','1.1.7')]
 case_rows=[]
 for row in tail['cases']:
  label=urlsplit(row['url']).netloc+' · '+row['case_id'][:8]
  case_rows.append(('',cell(link(prefix+'datasets/'+row['case_id']+'.html',label))+
                    cell(ms(row['older_median_ms']),row['older_median_ms'])+
                    cell(ms(row['newer_median_ms']),row['newer_median_ms'])+
                    cell(ms(row['older_max_ms']),row['older_max_ms'])+
                    cell(ms(row['newer_max_ms']),row['newer_max_ms'])))
 resource_rows=[('',cell(label)+cell(str(resources[name]['samples']))+
                 cell(f"{resources[name]['min_idle_cpu_pct']:.1f}%")+
                 cell(f"{resources[name]['min_unused_memory_mb']:.0f} MB")+
                 cell(str(resources[name]['swapouts_delta'])))
                for name,label in (('moli-115-control','Moli 1.1.5'),('moli-117-repeat','Moli 1.1.7'))]
 body='<p>'+h('同一批 200 页各测两轮；每页两版轮流先请求，所有调用串行执行。下表仅统计成功且有正文的调用，两版都是 398/400 次成功；它是版本耗时诊断，六方法正式结果仍见核心表。',
             'The same 200 pages were run twice. On each page the two versions took turns going first, and all calls ran serially. The table counts only successful calls with body text: both versions succeeded on 398/400 calls. This diagnoses version timing; the formal six-method results remain in the core table.')+'</p>'
 if formal_round is not None:
  body+=f'<p class="source-note" data-timing-context="prior-overlap" data-formal-round="{int(formal_round)}">'+h('这组较早的复测与另一项浏览器任务同时运行，只用于定位慢页和调用顺序效应；首页正式耗时采用之后无并发认证复测的预选第二轮，因此两张表的数值不同。',
                                     'This earlier crossover overlapped another browser benchmark and is retained only to diagnose slow pages and call order. The formal home-page timing uses the preselected second round of a later certified run without concurrent benchmarks, so the table values differ.')+'</p>'
 body+=table([h('版本','Version'),h('成功调用 / 全部调用','Successful / all calls'),h('均值耗时','Mean latency'),h('P50 耗时','P50 latency'),h('P95 耗时','P95 latency')],rows,cls='wmb-rerun-table',metrics=[])
 body+='<p class="source-note">'+h('绿色标出同页交叉复测中该列较低的耗时。','Green marks the lower latency in this matched crossover.')+'</p>'
 balanced=diagnostic['balanced_200']
 body+='<p>'+h(f'最初单批测试出现的 1.1.7 耗时劣势，在交叉顺序复测中没有复现。连续两次请求同一页时，先请求的一次平均多花 {balanced["first_call_extra_mean_ms"]:.0f} ms（{balanced["first_call_slower_count"]}/{balanced["paired_success_calls"]} 对更慢）；两版都受影响。单次调用仍会偶发变慢，不能保证每一页每一次都耗时相同。',
                 f'The apparent 1.1.7 slowdown from the initial separate batches did not recur in the balanced crossover. When the same page was fetched twice in succession, the first call took {balanced["first_call_extra_mean_ms"]:.0f} ms longer on average ({balanced["first_call_slower_count"]}/{balanced["paired_success_calls"]} pairs slower); both versions were affected. Individual calls still have slow outliers, so equal latency on every call cannot be guaranteed.')+'</p>'
 repetitions={row['repetitions_per_version'] for row in tail['cases']}
 if len(repetitions)!=1 or tail['within_20_ms']+len(tail_outliers)!=tail_count:
  raise ValueError('Latency tail summary disagrees with the per-page evidence')
 repetition=repetitions.pop()
 tail_calls=tail_count*2*repetition
 tail_direction=(f'<span data-tail-outliers="{len(tail_outliers)}" data-tail-newer-faster="{tail_newer_faster}" data-tail-older-faster="{tail_older_faster}">'
                 +h(f'其余页面中新版更快 {tail_newer_faster} 页、旧版更快 {tail_older_faster} 页。',
                    f'Among the rest, 1.1.7 was faster on {tail_newer_faster} pages and 1.1.5 on {tail_older_faster}.')+'</span>')
 body+='<p>'+h(f'针对最初差距最大的 {tail_count} 页，两版每页再各测 {repetition} 次，共 {tail_calls} 次均成功，且同一版本的正文哈希保持一致。{tail["within_20_ms"]}/{tail_count} 页的两版中位耗时相差不超过 20 ms；',
                 f'For the {tail_count} pages with the largest initial gaps, each version was called {repetition} more times per page: all {tail_calls} calls succeeded, and output hashes stayed constant within each version. Median timing differed by no more than 20 ms on {tail["within_20_ms"]}/{tail_count} pages; ')+tail_direction+h('下表逐页列出典型耗时和最慢一次，便于区分稳定差异与偶发慢调用。',' The table shows each page’s typical and slowest call.')+'</p>'
 body+='<details><summary>'+h('查看差异最大的 20 页及逐页复测','Inspect all 20 divergent pages and repeated calls')+'</summary>'+table([h('页面','Page'),h('1.1.5 中位耗时','1.1.5 median'),h('1.1.7 中位耗时','1.1.7 median'),h('1.1.5 最慢一次','1.1.5 slowest'),h('1.1.7 最慢一次','1.1.7 slowest')],case_rows,cls='wmb-rerun-cases',metrics=[])+'</details>'
 failure=diagnostic['failure_retest']
 body+='<p>'+h(f'两版各有 1 页未成功，均为同一页。各自独立重测时，首次和一次重试仍因页面脚本跳转原站而失败；禁用页面脚本后两版都返回正文。继续按相同条件重试不能恢复该页；禁用脚本会改变评测条件，因此该页仍计为失败，覆盖率为 199/200。',
                 f'Both versions failed on the same page. In separate retests, each initial call and retry again failed after the page script navigated to the origin site. Disabling page-authored JavaScript returned body text for both versions. Repeating the same condition did not recover the page; disabling the script changes the test, so this page remains failed and coverage is 199/200.')+' '+link(prefix+'datasets/'+failure['case_id']+'.html',h('查看失败页','Inspect the failed page'))+'</p>'
 body+=table([h('本轮','Rerun'),h('系统采样','System samples'),h('最低空闲 CPU','Minimum idle CPU'),h('最低未用内存','Minimum unused memory'),h('新增 swap-out','New swap-outs')],resource_rows,cls='wmb-rerun-resource',metrics=[])
 body+='<p class="source-note">'+h('下表是此前单批运行期间的宿主机采样，不能代替交叉复测时的逐调用资源测量，也不能证明负载导致慢调用。',
             'The resource samples below come from the preceding separate batches. They are not per-call measurements from the crossover and do not prove host load caused slow calls.')+' '+link(('../' if english else '')+prefix+'latency-diagnostic.json',h('下载逐页复测与证据绑定','Download per-page repeats and evidence bindings'))+'</p>'
 return '<div class="wmb-rerun-diagnostic">'+body+'</div>'


def _wmb_editorial_guide(*,english,names,labels,cases,bycase,case_count,score,denom,
                         failures, out=None, latency_diagnostic=None, formal_round=None):
 # Editorial analysis of the frozen WMB-200 cohort published as
 # ver_dab94ad9c17f4db6bec7bff98a82c660. Narrative numbers and selected examples
 # are a reviewed snapshot: a new cohort/scoring revision requires re-reviewing
 # this article, not merely substituting numbers into its recommendations.
 def h(zh,en):return en if english else zh
 fail_total=sum(len(failures(name)) for name in names)
 body_total=case_count*len(names)-fail_total
 editorial_timing=(_wmb_latency_summary([row for rows in bycase.values() for row in rows],names)['successful']
                   if out is not None else None)
 common_low=sum(all(any(r['provider']==name and r.get('metrics',{}).get('text_edit',{}).get('success') and r['metrics']['text_edit']['score']<.1 for r in bycase[case_id]) for name in names) for case_id in cases)
 def score_part(metric,label):
  return '<div class="wmb-score-part"><span>'+esc(label)+'</span><b>'+_wmb_pct(score('kitesurf',metric))+'</b><small>'+h('计入 ','Scored on ')+str(denom('kitesurf',metric))+'/'+str(case_count)+h(' 页',' pages')+'</small></div>'
 score_formula=''.join((score_part('text_edit',h('全文文字与标记','Full Markdown')),score_part('code_edit',h('代码','Code')),score_part('formula_edit',h('公式','Formula')),score_part('table_edit',h('表格内容与标记','Table content and markup')),score_part('table_TEDS',h('表格结构','Table structure'))))
 score_formula+='<div class="wmb-score-result">'+h('五项均值再平均 = Kitesurf overall 32.4%','Mean of the five component means = Kitesurf overall 32.4%')+'</div>'
 scope_case_id='c42e7f28-f109-459b-8423-2ce20f651ac9'
 if scope_case_id in cases:
  scope_rows=sorted(bycase[scope_case_id],key=lambda item:item['provider'])
  scope_stats=''
  for r in scope_rows:
   metric=r.get('metrics',{}).get('text_edit',{})
   length=(metric.get('details') or {}).get('predicted_text_length')
   length_label=f'{length:,} '+h('字符','chars') if length is not None else h('长度未记录','Length not recorded')
   scope_stats+='<span>'+esc(labels[r['provider']])+': '+_wmb_pct(metric.get('score'))+' · '+length_label+'</span>'
  scope_example='<div class="wmb-case-study"><header><div><h3>Pantacordis scotinella</h3><p>'+h('同一张 Wikipedia 冻结页；人工答案 1,315 字符。','The same frozen Wikipedia page; the human reference has 1,315 characters.')+'</p></div>'+link('../datasets/'+scope_case_id+'.html',h('打开完整六方法对照','Open the complete six-method comparison'))+'</header><div class="wmb-evidence-pair"><article><h4>'+h('人工答案保留的正文','What the human reference keeps')+'</h4><pre># Pantacordis scotinella\n\nPantacordis scotinella is a moth of the family Autostichidae. It is found on Crete.\n\n## References</pre></article><article><h4>'+h('方法输出开头','How method output begins')+'</h4><pre>Main menu\nNavigation\n- Main page\n- Contents\n- Current events\n- Random article\n- About Wikipedia\n- Contact us\n- Donate\n\nContribute\n- Help\n- Learn to edit</pre></article></div><div class="wmb-case-stats">'+scope_stats+'</div></div>'
 else:
  scope_example='<p class="source-note">'+h('完整报告会在这里展示一页人工答案与六种输出的逐字证据。','The full report shows the human reference and six outputs for one page here.')+'</p>'
 table_case_id='686205e4-4549-42ce-bf42-f285f941c868'
 if table_case_id in cases:
  table_rows=sorted(bycase[table_case_id],key=lambda item:-((item.get('metrics',{}).get('table_TEDS',{}).get('score') or 0)))
  table_stats=''.join('<span>'+esc(labels[r['provider']])+': TEDS '+_wmb_pct(r.get('metrics',{}).get('table_TEDS',{}).get('score'))+'</span>' for r in table_rows)
  table_example='<div class="wmb-case-study"><header><div><h3>'+h('Python 随机模块','Python random module')+'</h3><p>'+h('人工答案是一张两列表格，六种方法都返回了正文。','The reference contains a two-column table, and every method returned body text.')+'</p></div>'+link('../datasets/'+table_case_id+'.html',h('打开完整六方法对照','Open the complete six-method comparison'))+'</header><div class="wmb-evidence-pair"><article><h4>'+h('可以重新解析的 Markdown 表格','Parseable Markdown table')+'</h4><pre>| 方法 | 描述 |\n|---|---|\n| seed() | 初始化随机数生成器。 |\n| getstate() | 返回当前内部状态。 |\n| randint() | 返回给定范围之间的随机数。 |</pre></article><article><h4>'+h('Moli 1.1.5 返回的同一段内容','The same content from Moli 1.1.5')+'</h4><pre>方法描述seed()初始化随机数生成器。getstate()返回随机数生成器的当前内部状态。setstate()恢复随机数生成器的内部状态……</pre></article></div><div class="wmb-case-stats">'+table_stats+'</div></div>'
 else:
  table_example='<p class="source-note">'+h('完整报告会在这里展示同一表格的结构化输出与摊平文字。','The full report shows structured and flattened versions of the same table here.')+'</p>'
 def paragraph(zh,en):return '<p>'+h(zh,en)+'</p>'
 def chapter(key,zh,en,body):return '<section id="'+key+'"><h2>'+h(zh,en)+'</h2>'+body+'</section>'
 def evidence_pair(case_id,left_provider,right_provider,needle='',size=550):
  if out is None or case_id not in cases:return ''
  rows={r['provider']:r for r in bycase[case_id]}
  def excerpt(provider):
   path=('references/'+case_id+'.md') if provider is None else rows[provider]['artifact_path']
   text=contained_path(out,path).read_text(encoding='utf-8')
   start=max(0,text.find(needle)) if needle else 0
   if case_id=='686205e4-4549-42ce-bf42-f285f941c868':
    start=max(0,text.find('方法描述' if provider=='moli' else '| 方法'))
   if provider is None and case_id=='c42e7f28-f109-459b-8423-2ce20f651ac9':
    start=text.index('Pantacordis scotinella is a moth')
    return text[start:text.index('<table>',start)].strip()
   if needle:
    line_start=text.rfind('\n',0,start)+1
    if start-line_start<80:start=line_start
   return text[start:start+size]
  return '<div class="wmb-evidence-pair">'+''.join('<article><h4>'+esc(labels.get(provider,h('人工答案','Human reference')))+' · '+h('原文节选','verbatim excerpt')+'</h4><pre>'+esc(excerpt(provider))+'</pre></article>' for provider in (left_provider,right_provider))+'</div>'
 if out is not None:
  from bs4 import BeautifulSoup
  if scope_case_id in cases:
   parsed=BeautifulSoup(scope_example,'html.parser')
   parsed.select_one('.wmb-evidence-pair').replace_with(BeautifulSoup(evidence_pair(scope_case_id,None,'moli',size=430),'html.parser'))
   scope_example=str(parsed)
  if table_case_id in cases:
   parsed=BeautifulSoup(table_example,'html.parser')
   parsed.select_one('.wmb-evidence-pair').replace_with(BeautifulSoup(evidence_pair(table_case_id,'moli','moli-1.1.7','方法',size=400),'html.parser'))
   table_example=str(parsed)
 # The article starts with the complete registry; the interpretation follows its evidence.
 metric_names=[('overall',h('综合分','Overall')),('text_edit',h('全文','Text')),('code_edit',h('代码','Code')),('formula_edit',h('公式','Formulas')),('table_edit',h('表格内容','Table content')),('table_TEDS',h('表格结构','Table structure'))]
 result_rows=[]
 for name in sorted(names,key=lambda name:score(name,'overall'),reverse=True):
  values=cell(link('../providers/'+name+'.html',labels[name]))
  for metric,_ in metric_names:
   value=score(name,metric)
   best=max(score(other,metric) for other in names)
   display=_wmb_pct(value)
   if value==best and sum(score(other,metric)==best for other in names)==1:display='<strong class="comparison-winner">'+display+'</strong>'
   values+=cell(display+('' if metric=='overall' else '<small>'+str(denom(name,metric))+h(' 页计分',' scored pages')+'</small>'))
  result_rows.append(('',values))
 head='<div class="wrap essay"><article class="essay-body publication-article wmb-editorial" aria-labelledby="article-title"><header class="essay-head"><p class="eyebrow">Web Fetch · WebMainBench</p><h1 id="article-title">'+h('同一份网页，六种 Markdown：200 页转换结果解读','One webpage, six Markdown outputs: interpreting 200 conversions')+'</h1>'
 head+=paragraph('网页转成 Markdown 后，文章有没有混进菜单，表格还能不能还原，公式会不会变成普通文字？这轮评测把同一批 200 份网页 HTML 交给六种配置，对照人工整理的主内容答案，比较转换质量和耗时。','After a webpage becomes Markdown, does navigation crowd out the article? Can software reconstruct its tables and recognize its formulas? This evaluation gives the same 200 HTML documents to six configurations and compares their output with human main-content references, alongside conversion latency.')
 if editorial_timing and all(name in editorial_timing for name in ('kitesurf','moli-1.1.7','lightpanda')):
  head+=paragraph(
   f'Kitesurf、Moli 1.1.7 和 Lightpanda 的综合分分别为 32.4%、32.0% 和 31.7%，相差不到 1 个百分点；本版成功 fetch 均值耗时分别为 {ms(editorial_timing["kitesurf"]["mean"])}、{ms(editorial_timing["moli-1.1.7"]["mean"])} 和 {ms(editorial_timing["lightpanda"]["mean"])}，成功页数分别为 195、199 和 200。得分接近，输出特征和速度并不相同。',
   f'Kitesurf, Moli 1.1.7 and Lightpanda score 32.4%, 32.0% and 31.7% overall, less than one percentage point apart. Successful-fetch means in this edition are {ms(editorial_timing["kitesurf"]["mean"])}, {ms(editorial_timing["moli-1.1.7"]["mean"])} and {ms(editorial_timing["lightpanda"]["mean"])}, on 195, 199 and 200 successful pages. Similar aggregate scores conceal different outputs and timing.')
 else:
  head+=paragraph('六种配置的综合分与各项分数见下表；每项的计分页数需结合具体案例阅读。',
                  'The table below shows overall and component scores for all six configurations. Read each component population alongside individual cases.')
 head+='</header><div class="wmb-guide-results">'+table([h('方法','Method')]+[label for _,label in metric_names],result_rows,metrics=[])+'</div><p class="source-note">'+h('分数越高，越接近人工答案；绿色标出单项唯一最高值。综合分、表格结构分为修正后复算值。各项下方是参与该项平均的页数；综合分是五项均值的平均，并非通过率。宽表可横向滚动。','Higher scores mean closer agreement with the human reference; green marks a unique leader. Overall and table structure are recomputed after correction. The page count under each component is its averaging population. Overall averages five component means; it is not a pass rate. Scroll wide tables horizontally.')+'</p>'
 sections=[('scope','评测对象','What was measured'),('overall','分数与计分页','Scores and populations'),('content','全文范围','Content scope'),('tables','表格结构','Table structure'),('version-differences','语法与版本变化','Syntax and version changes'),('delivery','速度与返回结果','Latency and delivery'),('decision','如何选择','Choosing a method')]
 head+='<nav class="wmb-guide-toc" aria-label="'+h('解读目录','Article contents')+'">'+''.join(link('#'+key,str(i+1).zfill(2)+' '+h(zh,en)) for i,(key,zh,en) in enumerate(sections))+'</nav>'
 scope=chapter('scope','01 · 比较的是网页转换，输入在调用前已固定','01 · The input is frozen before conversion',paragraph('Web Fetch 通常从 URL 获取网页，再把内容转成适合检索、阅读或交给模型的文本。本轮单独比较其中的 HTML → Markdown：从 WebMainBench 公开的 545 页子集中预先选定 200 页，保留原始 HTML 和人工主内容答案。原网址只作来源记录，运行时不重新抓取。','Web Fetch usually retrieves a URL and turns the page into text for reading, retrieval or a model. This run isolates HTML → Markdown: 200 pages were selected in advance from WebMainBench’s public 545-page subset, retaining the original HTML and human main-content references. Source URLs are provenance, not live retrieval targets.')+paragraph('六种配置都接收同一份 HTML。Kitesurf 经远程服务转换，Lightpanda、Obscura 和 Moli 使用各自的原生 Markdown 接口；Chromium 152 先取得整页 DOM，再交给 node-html-markdown 2.0.0。页面内联脚本可以执行，外部资源被隔离。因而结果同时包含内容选择与格式转换的差异。','All six configurations receive identical HTML. Kitesurf converts through a remote service; Lightpanda, Obscura and Moli use their native Markdown interfaces. Chromium 152 provides the whole-page DOM to node-html-markdown 2.0.0. Inline scripts may run, while external resources are isolated. Results therefore include both content-selection and format-conversion differences.')+'<p class="source-note">'+link('../datasets/index.html',h('200 页样本与人工答案','The 200 pages and references'))+' · '+link('../reproduce/index.html',h('版本、环境与执行路线','Versions, environment and execution routes'))+'</p>')
 definitions=[('text_edit',h('整份 Markdown 的文字与标记；菜单、链接标题也参与比较。','The entire Markdown string, including markup, menus and link titles.')),('code_edit',h('围栏或缩进块中提取的代码字符串；普通文案也可能被识别进去。','Code strings extracted from fenced or indented blocks, which may include ordinary prose.')),('formula_edit',h('按数学定界符识别出的公式字符串；受转义方式影响。','Formula strings recognized by math delimiters; escaping affects recognition.')),('table_edit',h('识别表格后，比较规范化的表格 HTML 字符串。','Normalized HTML strings after tables are recognized.')),('table_TEDS',h('识别表格后，比较表格、行、单元格的树结构和内容。','The tree of tables, rows and cells, including cell content.'))]
 scoring=paragraph('编辑相似度衡量“把输出改成人工答案需要改多少”。遗漏会扣分，多出来的内容也会扣分。下面五项分别比较不同表示，因此“全文 40%”不能读作“只取到了 40% 的正文”。','Edit similarity measures how much the output must change to match the reference. Both missing and extra content reduce it. The five components compare different representations; a 40% text score does not mean that 40% of the article was retrieved.')+table([h('指标','Metric'),h('实际比较什么','What is compared')],[('',cell(metric)+cell(explanation)) for metric,explanation in definitions],metrics=[])
 scoring+=paragraph('每一项都先确定哪些页需要计分：最终调用失败的结果不送入评分；调用成功后，只有“人工答案和输出都没有该类内容”才跳过这一项。只要任一侧识别出了内容，就计分；只有一侧有，按 0 分计入。因此，各方法的平均分母会随输出中的结构识别结果变化。','Each component first determines which pages enter its mean. Failed final calls are not sent to the scorer. Among successful calls, a component is skipped only when neither reference nor output contains that content type. Content on only one side enters as zero. Each method’s recognized output structures therefore affect its denominator.')
 complete=[r for rs in bycase.values() for r in rs]
 if out is not None and 'moli-1.1.7' in names:
  scoring+=_wmb_denominator_example(complete,english,case_count)
 scoring+=paragraph('计分也不等于生成了目标正文。例如 Chromium 有 199 页目标正文，但全文计分是 200 页：其中一页返回“ERR_ACCESS_DENIED”错误页，调用状态仍为成功，官方文字相似度照算为 1.48%。报告将它标为未生成正文，保留原分数。','Scoring does not establish target-body delivery. Chromium produces target content on 199 pages but has 200 text scores: one successful call returns an ERR_ACCESS_DENIED error page, which still receives a 1.48% text similarity score. The report marks it as missing target content and retains the score.')+'<p class="source-note">'+link('../attempts/chromium__ccb6033c-0a12-4f9c-8c68-794f26129841__r001.html',h('查看错误页为何仍有分数','Why the error page still has a score'))+'</p>' if 'ccb6033c-0a12-4f9c-8c68-794f26129841' in cases else ''
 scoring+='<h3>'+h('再把五个分项均值等权平均','Then average the five component means equally')+'</h3><div class="wmb-score-formula">'+score_formula+'</div>'
 scoring+=paragraph('每项都占综合分的五分之一，与该项计入多少页无关。这让少量代码页与大量全文页拥有相同权重，也使新增的零分页能拉低分项均值。表格 TEDS 和综合分使用经树结构适配修正后的复算值；原始官方结果保留在核对页。','Each component contributes one fifth of overall, irrespective of its page count. A small code population has the same weight as the much larger text population, and newly included zeros can lower a component mean. TEDS and overall use the recomputation with the corrected tree adapter; the original official results remain available for comparison.')+'<p class="source-note">'+link('../methodology/index.html',h('完整评分规则','Full scoring rules'))+' · '+link('../review/index.html',h('原始分与 TEDS 修正依据','Original scores and TEDS correction evidence'))+'</p>'
 overall=chapter('overall','02 · 分数衡量相似度，计分页由两侧内容共同决定','02 · Similarity scores use a population determined by both sides',scoring)
 content=chapter('content','03 · 六种方法共同的损失：把整页当成主内容','03 · A shared source of loss: the whole page exceeds the main content',paragraph(f'{common_low} 页在六种方法上的全文分都低于 10%。Wikipedia 的这页就是一例：人工答案只有 1,315 字符，聚焦文章与参考资料，输出却带上导航、编辑入口和其他页面区域。六种输出的长度与得分放在下方，可以直接看到范围差距。',f'{common_low} pages have text scores below 10% for all six methods. This Wikipedia page illustrates the gap: its 1,315-character human reference focuses on the article and references, while outputs include navigation, editing links and other page regions. Their lengths and scores show the scope difference.')+scope_example+paragraph('全文指标直接比较完整 Markdown，额外菜单、链接标题和重复布局都增加编辑距离。这个问题横跨六种配置，说明正文选择是共同的改进方向。只修复一种表格或公式语法，并不能消除这些文字差异。','The text metric compares complete Markdown. Extra menus, link titles and repeated layout increase edit distance. Because the issue spans all six configurations, main-content selection is a shared improvement opportunity. Fixing table or formula syntax alone will not remove this mismatch.'))
 tables=chapter('tables','04 · 表格的关键是保住行列，而不只是留下文字','04 · Tables need rows and columns, as well as words',paragraph('表格需要先被 Markdown 解析器识别，才进入内容和树结构比较。Python 随机模块页展示了直接的格式差异：同一组方法与说明，保留分隔线就能还原为两列，压成连续文字后则无法识别为表格。','A Markdown parser must recognize a table before its content and tree structure can be compared. The Python random-module page shows a direct format difference: separators preserve two columns, while a continuous string of the same words cannot be recognized as a table.')+table_example+paragraph('Moli 1.1.5 的 40 个表格计分页全部为 0；1.1.7 在这 40 页中的 33 页获得正的 table_edit，表格修复确实体现在同页结果上。新版还输出了更多可识别表格，计分页增至 73 页，表格内容均值为 18.5%、结构均值为 13.9%。','Moli 1.1.5 scores zero on all 40 table-scored pages. Version 1.1.7 gains positive table_edit scores on 33 of those same pages: the table repair is visible in matched-page evidence. It also produces more recognized tables, increasing the scored population to 73 pages, with means of 18.5% for table content and 13.9% for structure.')+paragraph('横向比较中，Chromium 的表格内容分最高，为 25.7%；Lightpanda 的结构分最高，为 16.2%。两种第一名不同，因为字符串相似度和行列树结构测的不是同一件事。表格整体得分仍低，单页修复到 100% 并不意味着所有页面都保真。','Across methods, Chromium leads table content at 25.7%, while Lightpanda leads structure at 16.2%. Different leaders are expected because string similarity and the row-and-cell tree measure different representations. Aggregate table scores remain low; a perfect result on one page does not establish fidelity across the cohort.'))
 tables=tables.removesuffix('</section>')+_wmb_table_population([r for rs in bycase.values() for r in rs],names,labels,english)+'</section>'
 version=''
 if all(name in names for name in ('moli','moli-1.1.7')) and '357c9ba1-1c6c-4d8f-a75e-0bc4a3bc9b3c' in cases:
  version=_wmb_version_analysis(english,bycase,score,denom)
  version=version.replace(h('Moli 1.1.7 其他分项为何变低','Why other Moli 1.1.7 component scores fall'),h('05 · 表格改善之外，排版和转义改变了三个分项','05 · Alongside better tables, layout and escaping change three components'))
  # Keep actual specimens beside the mechanisms, without five long open cards.
  from bs4 import BeautifulSoup
  parsed=BeautifulSoup(version,'html.parser')
  for card,case_id,left,right,needle in zip(parsed.select('.wmb-version-case'),['357c9ba1-1c6c-4d8f-a75e-0bc4a3bc9b3c','14f8fe79-f899-4074-9878-1bf9f301ad95','63cfc5dd-1737-485f-9bc9-cacc65299ba8','ac11d851-aed7-4b9e-89d1-39c142fa152d','0b7ac530-19ea-4a14-a8bd-adb9cd646351'],['moli']*5,['moli-1.1.7']*5,['Sign in','The classic comedic', '5月10日 20:14', 'remember syntax', 'javascript:void']):
   card.name='details'
   if case_id=='ac11d851-aed7-4b9e-89d1-39c142fa152d':card['open']=''
   card.h3.name='summary'
   specimen=evidence_pair(case_id,left,right,needle)
   card.insert(2,BeautifulSoup(specimen,'html.parser'))
  version=str(parsed)
 latency_rows=[]
 timed=[r for rs in bycase.values() for r in rs if 'elapsed_ms' in r and 'total_elapsed_ms' in r]
 timing=_wmb_latency_summary(timed,names)
 for name in names:
  stat=timing['successful'][name]
  latency_rows.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(str(stat['count'])+'/'+str(case_count))+''.join(_wmb_timing_cell(timing['successful'],name,key) for key in ('mean','p50','p95'))))
 old_timing=timing['successful'].get('moli');new_timing=timing['successful'].get('moli-1.1.7')
 if old_timing and new_timing and out is not None and (out/'latency-corrections.json').is_file():
  latency_interpretation=paragraph(
   f'Moli 两版的正式耗时现采用同一批 200 页交叉复测第二轮，均有 199 页成功。1.1.5 的均值与 P50 为 {ms(old_timing["mean"])}、{ms(old_timing["p50"])}；1.1.7 为 {ms(new_timing["mean"])}、{ms(new_timing["p50"])}。旧批次耗时保留在纠正收据中。其余四种方法仍来自原有批次，跨方法速度需结合执行时间窗看。绿色只标本列最低值。',
   f'Formal Moli timing now uses round two of the same 200-page balanced crossover, with 199 successful pages per version. Mean and P50 are {ms(old_timing["mean"])} and {ms(old_timing["p50"])} for 1.1.5, versus {ms(new_timing["mean"])} and {ms(new_timing["p50"])} for 1.1.7. Prior batch timings remain in the correction receipt. The other four methods retain their original batches; cross-method speed also depends on execution windows. Green marks column minima.')
 elif old_timing and new_timing:
  latency_interpretation=paragraph(
   f'六方法正式表中，Moli 1.1.5 与 1.1.7 的 P50 分别为 {ms(old_timing["p50"])} 和 {ms(new_timing["p50"])}，各有 199 页成功。两版来自独立批次，不能凭此判断版本快慢；下方给出交叉复测。绿色只标出本列最低值。',
   f'The formal six-method table records Moli P50 values of {ms(old_timing["p50"])} for 1.1.5 and {ms(new_timing["p50"])} for 1.1.7, with 199 successful pages each. Those separate batches do not establish a version speed difference; the balanced crossover follows. Green marks column minima.')
 else:
  latency_interpretation=''
 delivery=chapter('delivery','06 · 成功 fetch 的耗时，与同页对照','06 · Successful-fetch latency and matched pages',paragraph('每页采用最终结果。计时从这次 fetch 请求开始，到返回转换结果为止；只有最终调用成功、返回非空目标正文且不是错误页，才进入下表。前一次失败的耗时不相加。例如先失败 800 ms、再成功 200 ms，这页的成功 fetch 耗时就是 200 ms。','Each page uses its final result. Timing runs from that fetch request to its converted response. A page enters this table only when the final call succeeds with non-empty target content, not an error page. Previous failed calls are not added: an 800 ms failure followed by a 200 ms success contributes 200 ms.')+paragraph('均值是成功 fetch 耗时之和除以成功页数；P50 是这些耗时的中位数；P95 是约 95% 的成功 fetch 所需时间上限。三项使用同一批成功页，与文字、公式或表格的计分页数无关。','The mean divides successful-fetch time by the success count. P50 is the median of those durations; P95 is the time within which roughly 95% of successful fetches finish. All three use the same success population, independently of text, formula or table scoring populations.')+'<p class="wmb-latency-population"><strong>'+h('仅最终成功 fetch；成功页数单列，失败不进入耗时均值','Final successful fetches only; success counts shown separately, failures excluded')+'</strong></p>'+table([h('方法','Method'),h('成功页 / 总页数','Successful / all pages'),h('均值耗时','Mean latency'),h('P50 耗时','P50 latency'),h('P95 耗时','P95 latency')],latency_rows,cls='wmb-guide-latency',metrics=[])+latency_interpretation+(_wmb_matched_latency(timed,names,labels,english) if timed else '')+paragraph(f'完整结果仍保留全部 {case_count*len(names):,} 条：{body_total:,} 条有目标正文，{fail_total} 条没有，集中在 6 页。失败反映在成功覆盖中，不以 0 ms 冒充快速返回，也不混进成功耗时。异常证据分别指向脚本与环境冲突、离开冻结页后的网络隔离，以及复杂表格触发远程服务边界。',f'The full matrix retains all {case_count*len(names):,} results: {body_total:,} with target content and {fail_total} without it, concentrated on six pages. Failures reduce coverage; they are neither counted as fast zero-duration results nor mixed into successful-fetch timing. Anomaly evidence identifies script/runtime conflicts, network isolation after navigation away from a frozen page, and complex tables hitting a remote-service boundary.')+paragraph('同题对照统一了页面，但没有消除执行环境差异：Kitesurf 包含远程服务与网络开销，其他路线在本地运行；各批次也并非同一时间窗。这些数字比较的是本轮端到端转换耗时。原始六方法比较没有可比的 CPU、内存或网络资源测量；下方的 Moli 复跑另有宿主机采样。','Matching pages controls the input population, not the execution environment: Kitesurf includes remote service and network overhead, while the other routes run locally. Batches also ran in different time windows. These numbers compare this run’s end-to-end conversion latency; the original six-method comparison has no comparable CPU, memory or network-resource measurements; the Moli rerun below adds host-level samples.')+'<p class="source-note">'+link('../methodology/index.html#latency-policy',h('完整计时规则与原始执行成本','Timing rules and retained execution cost'))+' · '+link('../review/index.html',h('异常审核与证据','Anomaly review and evidence'))+'</p>')
 decision=chapter('decision','07 · 选型取决于要交付的内容','07 · Choose for the content you need to deliver',paragraph('如果需要可被程序继续读取的 Markdown，表格边界、公式定界符和代码块应成为明确的验收条件。Moli 1.1.7 修复了旧版表格摊平问题，综合分从 30.0% 升到 32.0%；与此同时，全文相似度下降、缩进文案进入代码评分、转义影响公式识别，都应纳入版本取舍。','For machine-readable Markdown, table boundaries, formula delimiters and code blocks should be explicit acceptance criteria. Moli 1.1.7 repairs flattened tables and raises overall from 30.0% to 32.0%. Its lower full-text similarity, indented prose entering code scoring and escaped formula syntax are also part of the version tradeoff.')+paragraph('Kitesurf 以 32.4% 综合分领先，适合优先核验质量、可以接受秒级耗时的场景。Lightpanda 的 31.7% 综合分、200/200 页有正文和 129 ms 中位耗时，使它值得进入低延迟候选，但必须检查慢尾部。Moli 1.1.5 的文字和代码均值领先，代价是表格不可解析；两版实际速度应看同页交叉顺序复测。Chromium 可作整页 DOM 转换基线；Obscura 的公式均值最高，但表格结构几乎为零，不能用单项最高代替整体选择。','Kitesurf leads overall at 32.4%, making it a candidate where quality is prioritized and seconds of latency are acceptable. Lightpanda combines 31.7% overall, body text on 200/200 pages and a 129 ms median, but its slow tail needs attention. Moli 1.1.5 leads the text and code means, at the cost of unparseable tables. The matched crossover below is the relevant version-speed check. Chromium provides a whole-DOM conversion baseline. Obscura has the highest formula mean but almost zero table structure; one leading component is insufficient for an overall choice.')+paragraph('共同的下一步是先确定正文范围，再要求结构保真。实时网站还涉及访问限制、外部资源与动态请求，应在同一时间窗另做真实 URL 验证。本轮结果可用于筛选转换方案，实时交付能力由后续实站测试验证。','The shared next step is to define main-content scope, then require structural fidelity. Live websites add access restrictions, external resources and dynamic requests, so they need a separate same-window URL evaluation. This cohort establishes results for the 200 frozen conversions.')+'<p class="source-note">'+h('分数、输出哈希与重试链已对全部 1,200 个结果做程序核对；异常审核覆盖 6 页的 36 个结果，其余 1,164 个未做逐条人工内容审核。文中的版本案例是针对性核查，不等于全量内容验收。','Scores, output hashes and retry chains were checked programmatically for all 1,200 results. Anomaly review covers 36 results on six pages; the remaining 1,164 have no individual human content review. Targeted version examples are not a full content acceptance review.')+'</p><p class="actions">'+link('../analysis/index.html',h('逐页结果与完整输出','Per-page results and full outputs'))+' · '+link('../review/index.html',h('审核与修正记录','Reviews and corrections'))+'</p>')
 if latency_diagnostic:
  delivery=delivery.removesuffix('</section>')+'<h3>'+h('Moli 同机耗时：版本差异是否稳定？','Moli on one host: is the version gap stable?')+'</h3>'+_wmb_latency_rerun(latency_diagnostic,english,prefix='../',formal_round=formal_round)+'</section>'
 return head+scope+overall+content+tables+version+delivery+decision+'</article></div>'


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
  select('band',h('指标分数区间','Metric score band'),[('',h('不限','Any')),("0:0.1",'0–10%'),("0:0.25",'0–25%'),("0.25:0.5",'25–50%'),("0.5:0.75",'50–75%'),("0.75:1",'75–100%'),('unavailable',h('未计算 / 不适用','Unavailable / not applicable'))])
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


def _wmb_result_detail(r,c,output,reference,labels,metric_labels,failure_notes,corrected,english=False):
 h=lambda zh,en:en if english else zh
 dialogs=[]
 def button(target,label):
  return '<button type="button" class="call-log-button" data-call-log-open="'+target+'" aria-haspopup="dialog">'+esc(label)+'</button>'
 def dialog(target,title,body):
  dialogs.append('<dialog class="call-log-dialog wmb-detail-dialog" id="'+target+'" aria-labelledby="'+target+'-title"><div class="call-log-shell"><header><h2 id="'+target+'-title">'+esc(title)+'</h2><button type="button" data-call-log-close aria-label="'+h('关闭','Close')+'">'+h('关闭','Close')+'</button></header><div class="wmb-dialog-content">'+body+'</div></div></dialog>')
  return button(target,title)
 raw_prefix='../../' if english else '../'
 metadata=r.get('provider_metadata') or {}
 case_id=c['id'];failed=not r['_body_present']
 body=hero(labels[r['provider']],h('页面 ID：','Page ID: ')+case_id)
 body+='<nav class="wrap actions">'+link('../analysis/index.html',h('全部结果','All results'))+' · '+link('../analysis/index.html#inspect?query='+case_id,h('同页各方法对照','Compare methods on this page'))+' · '+link('../datasets/'+case_id+'.html#frozen-input',h('冻结输入与参考','Frozen input and reference'))+'</nav>'
 summary=[(h('结果','Outcome'),_wmb_body_state(r,output,english)),(h('最终调用耗时','Final-call latency'),ms(r['elapsed_ms'])),(h('逐页综合分','Per-page score'),_wmb_pct(r.get('metrics',{}).get('overall',{}).get('score')) if r.get('metrics',{}).get('overall',{}).get('success') else '—')]
 body+=section(h('结果摘要','Result summary'),'<div class="wmb-attempt-summary" data-result-summary>'+''.join('<article class="card"><b>'+esc(k)+'</b><strong>'+esc(v)+'</strong></article>' for k,v in summary)+'</div><p class="source-note">'+h('正文存在不等于质量通过。逐页综合分平均本页可计算的分项；首页综合分平均五项全局均值。','Body text does not imply quality acceptance. Per-page score averages this page’s available components; the home score averages five cohort means.')+'</p>','result-summary')
 # Exact error and available stderr streams have one owner: the log dialog.
 error=r.get('error_message') or (output if _wmb_error_page(output) else '')
 streams=[]
 if error:streams.append((h('最终错误原文','Final error verbatim'),str(error),'error'))
 for key in ('stderr','browser_stderr','stdout'):
  value=metadata.get(key)
  if value and not any(str(value)==text for _,text,_ in streams):streams.append((key,str(value),key))
 log_html='<p>'+h('以下均来自最终调用记录；日志中的警告不自动等于失败根因。','These are recorded fields from the final call; warnings are not automatically root causes.')+'</p>'
 for label,text,kind in streams:
  lines=text.splitlines(keepends=True)
  matches=[i for i,line in enumerate(lines) if re.search(r'(?i)(\berror\b|^reason:|ERR_[A-Z_]+)',line)]
  locator=(matches[-1] if matches else len(lines)-1)
  target='log-'+kind+'-'+str(locator)
  log_html+='<h3>'+esc(label)+'</h3><button type="button" class="call-log-button" data-log-line="'+target+'">'+h('定位最后一条错误记录','Locate last error record')+'</button><pre class="wmb-log" data-kind="'+kind+'">'+''.join('<span id="log-'+kind+'-'+str(i)+'"'+(' class="wmb-log-focus" tabindex="-1"' if i==locator else '')+'>'+esc(line)+'</span>' for i,line in enumerate(lines))+'</pre>'
 if not streams:log_html+='<p data-log-unavailable>'+h('本次记录没有错误信息或标准输出日志。','No error or output logs were retained for this call.')+'</p>'
 status={key:r.get(key) for key in ('ok','status_code','error_type','started_at','completed_at','elapsed_ms')}
 status.update({key:metadata[key] for key in ('exit_code','browser_exit_code','upstream_status','completion','stderr_truncated','stdout_truncated') if key in metadata})
 log_html+='<details><summary>'+h('调用状态与时间','Call status and timestamps')+'</summary><pre>'+esc(json.dumps(status,ensure_ascii=False,indent=2))+'</pre></details>'
 log_button=dialog('result-logs',h('错误与运行日志','Errors and runtime logs'),log_html)
 if failed:
  reviewed=(r['provider'],case_id) in failure_notes
  observed,cause=failure_notes.get((r['provider'],case_id),('',''))
  stage=h('正文校验','Output validation') if r['ok'] else h('转换调用（内部阶段未记录）','Conversion call (internal stage not recorded)')
  if not r['ok'] and re.search(r'(?i)(failed to navigate|failed to fetch|curl request failed)',str(error)):stage=h('导航或网页请求','Navigation or page request')
  diagnosis=[(h('失败位置','Failure location'),esc(stage)),(h('程序错误类别','Program error category'),'<code>'+esc(r.get('_output_error') or r.get('error_type') or 'empty_body')+'</code>'),(h('复核结论','Reviewed cause'),esc(cause) if reviewed else h('未核实','Unverified'))]
  body+=section(h('失败定位','Failure diagnosis'),'<dl class="wmb-diagnosis" data-failure-diagnosis>'+''.join('<dt>'+k+'</dt><dd>'+v+'</dd>' for k,v in diagnosis)+'</dl><p>'+log_button+' '+button('result-output',h('检查返回内容','Inspect returned content'))+'</p>'+('<details><summary>'+h('复核依据','Review evidence')+'</summary><p>'+esc(observed)+'</p>'+link(raw_prefix+'webmainbench-anomaly-review.json',h('完整复核记录','Full review record'))+'</details>' if reviewed else ''),'failure-diagnosis')
 metric_rows=[]
 for metric,label in metric_labels.items():
  item=r.get('metrics',{}).get(metric,{})
  detail='<pre data-kind="score">'+esc(json.dumps(item,ensure_ascii=False,indent=2))+'</pre>'
  if corrected and metric in ('overall','table_TEDS'):
   detail+='<details><summary>'+h('官方原始分','Original official score')+'</summary><pre>'+esc(json.dumps(r.get('official_metrics',{}).get(metric,{}),ensure_ascii=False,indent=2))+'</pre></details>'
  open_score=dialog('score-'+metric,label+' · '+h('计算依据','Scoring evidence'),detail)
  metric_rows.append(('',cell(esc(label))+cell(h('已计算','Calculated') if item.get('success') else h('未计算','Not calculated'))+cell(_wmb_pct(item.get('score')) if item.get('success') else '—')+cell(open_score)))
 body+=section(h('逐项评分','Component scores'),table([h('指标','Metric'),h('状态','Status'),h('分数','Score'),h('评分依据','Evidence')],metric_rows,cls='wmb-detail-scores',metrics=[]),'component-scores')
 output_html='<p>'+link(raw_prefix+r['artifact_path'],h('下载方法输出','Download method output'))+' · '+link(raw_prefix+'references/'+case_id+'.md',h('下载人工参考','Download human reference'))+'</p><div class="evidence-compare wmb-evidence">'+''.join('<div class="evidence-pane"><header><h3>'+title+'</h3></header><div class="pane-body"><pre data-kind="'+kind+'">'+esc(text)+'</pre></div></div>' for title,kind,text in [(h('人工参考','Human reference'),'reference',reference),(h('最终实际输出','Final actual output'),'output',readable_output(output))])+'</div>'
 output_button=dialog('result-output',h('参考与输出对照','Reference and output comparison'),output_html)
 provenance={key:r.get(key) for key in ('run_id','attempt_id','case_id','input_html_sha256','reference_sha256','output_sha256')}
 fields={key:value for key,value in metadata.items() if key not in ('stderr','browser_stderr','stdout')}
 provenance_button=dialog('result-provenance',h('来源与接口字段','Provenance and response fields'),'<h3>'+h('标识与哈希','IDs and hashes')+'</h3><pre>'+esc(json.dumps(provenance,ensure_ascii=False,indent=2))+'</pre><h3>'+h('接口字段','Response fields')+'</h3><pre data-kind="metadata">'+esc(json.dumps(fields,ensure_ascii=False,indent=2))+'</pre>')
 body+=section(h('证据','Evidence'),'<div class="actions wmb-evidence-actions">'+output_button+('' if failed else log_button)+provenance_button+'</div>','result-evidence')
 return '<div data-report-detail="failed">'+body+''.join(dialogs)+'</div>' if failed else '<div data-report-detail="success">'+body+''.join(dialogs)+'</div>'


def generate_webmainbench(out:Path,dataset:dict,records:list,summaries:dict,manifests:list,review:dict|None=None,research:dict|None=None,corrections:dict|None=None,adoption_data:dict|None=None,fixture_correction:dict|None=None,teds_revision:tuple|None=None,anomaly_review:dict|None=None,latency_diagnostic:dict|None=None,*,include_english:bool=False):
 """Render the current WebMainBench report with the established report shell."""
 from .report_changes import render_webmainbench_history, webmainbench_history, ROOT
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
 labels={'kitesurf':'Kitesurf','lightpanda':'Lightpanda','obscura':'Obscura','moli':'Moli 1.1.5','moli-1.1.7':'Moli 1.1.7','chromium':'Chromium 152'}
 metric_order=['overall','text_edit','code_edit','formula_edit','table_edit','table_TEDS']
 corrected=teds_revision is not None
 display_score=_wmb_pct
 def score(name,metric):return summaries[name]['official_aggregate']['scores'][metric]
 def denom(name,metric):return summaries[name]['official_aggregate']['denominators'].get(metric)
 timing=_wmb_latency_summary(records,names)
 latency_corrected=(out/'latency-corrections.json').is_file()
 formal_round=json.loads((out/'latency-corrections.json').read_text(encoding='utf-8'))['selected_round'] if latency_corrected else None
 def failures(name):return [r for r in records if r['provider']==name and not r['_body_present']]
 def unique_winner(values,higher=True):
  ordered=sorted(values,key=lambda item:item[1],reverse=higher)
  return ordered[0][0] if len(ordered)==1 or ordered[0][1]!=ordered[1][1] else None
 ranked_metrics=metric_order if corrected else metric_order[1:-1]
 score_winners={metric:unique_winner([(name,score(name,metric)) for name in names]) for metric in ranked_metrics}
 latency_values=timing['successful']
 def value_html(value,winner=False,n=None,population=None,english=False):
  content=f'<span class="metric-value">{value}</span>'
  if n is not None:
   count=case_count if population is None else population
   label=f'scored on {n}/{count} pages' if english else f'计入 {n}/{count} 页'
   content+=f'<span class="tiny">{label}</span>'
  return f'<span class="comparison-winner">{content}</span>' if winner else content
 def core_rows(english=False):
  rows=[]
  for name in sorted(names,key=lambda item:(-score(item,'overall'),labels[item])):
   values=cell(link('providers/'+name+'.html',labels[name]))
   values+=cell(value_html(display_score(score(name,'overall')),winner=score_winners.get('overall')==name),score(name,'overall'))
   for metric in metric_order[1:]:
    values+=cell(value_html(display_score(score(name,metric)),winner=score_winners.get(metric)==name,n=denom(name,metric),english=english),score(name,metric))
   values+=cell(str(latency_values[name]['count'])+'/'+str(case_count))
   for key in ('mean','p50','p95'):
    values+=_wmb_timing_cell(latency_values,name,key)
   rows.append(('',values))
  return rows
 for english in ((False,True) if include_english else (False,)):
  h=lambda zh,en:en if english else zh
  corrected_label=h('修正后复算','recomputed after correction')
  corrected_suffix=(f' ({corrected_label})' if english else f'（{corrected_label}）')
  metric_labels={name:name+(corrected_suffix if corrected and name in ('overall','table_TEDS') else '') for name in metric_order}
  status=(h(f'{case_count} 页 × {len(names)} 方法 = {result_count:,} 条结果；证据与算术核验通过，表格结构评分的有效性尚未通过。',f'{case_count} pages × {len(names)} methods = {result_count:,} results; evidence and arithmetic checks passed, but table-structure score validity did not.') if review and review.get('status')=='passed' else h('测量完成；独立审查尚未通过，当前页面不可发布。','Measurement complete; independent review has not passed, so this build is not publishable.'))
  if corrected:
   status=h(f'{case_count} 页 × {len(names)} 种方法；官方仓库和榜单保持原样。报告用独立复算器修复已证实的 TEDS 计算缺陷，再处理同一批保存输出。',f'{case_count} pages × {len(names)} methods. The official repository and leaderboard remain unchanged. An independent recomputation corrects the demonstrated TEDS defect on the same saved outputs.')
  home=hero(h(f'WebMainBench-545 · 预选 200 页 · {len(names)} 种方法实测',f'WebMainBench-545 · preselected 200 · {len(names)} measured methods'),h(f'{len(names)} 种方法读取同一批冻结 HTML，并与同一份人工校准 Markdown 比较；逐页响应、空正文和原因放在结果详情中。',f'{len(names)} methods read the same frozen HTML and are compared with the same human-calibrated Markdown; page-level responses, empty body text and causes live in result details.'))
  home+='<div class="wrap reliability'+('' if review and review.get('status')=='passed' else ' failure')+'"><strong>'+esc(status)+'</strong></div>'
  home+=section(h('先看这张榜在测什么','What this ranking measures'),'<div class="wmb-scope-map"><article><b>'+h('输入：同一份网页存档','Input: identical archived HTML')+'</b><p>'+h(f'{case_count} 页 × {len(names)} 种方法；不访问实时源站。',f'{case_count} pages × {len(names)} methods; no live origin fetch.')+'</p></article><article><b>'+h('目标：主内容，而非整页所有文字','Target: main content, not every page element')+'</b><p>'+h('正文、代码、公式、表格；导航、广告和推荐链接不属于目标。','Body text, code, formulas and tables; navigation, ads and recommendations are outside the target.')+'</p></article><article><b>'+h('分数：与人工主内容答案有多接近','Score: similarity to the human main-content reference')+'</b><p>'+h('多带页面模板会扣分；表格文字保留但结构丢失，也会扣分。低分不等于网页没有获取到。','Extra boilerplate and lost table structure reduce scores. A low score does not mean the page was not retrieved.')+'</p></article></div><p class="actions">'+link('guide/index.html#content',h('看真实页面怎样被扣分 →','See a real scored page →'))+' · '+link('methodology/index.html#input-routes',h(f'{len(names)} 种方法怎样读取同一份 HTML →',f'How all {len(names)} methods read the same HTML →'))+'</p><p class="source-note">'+h('这不是实时抓取成功率或浏览器综合能力排名。','This is not a live-fetch success rate or an overall browser-capability ranking.')+'</p>','benchmark-scope')
  core_headers=[h('方法','Method'),metric_labels['overall'] if corrected else h('overall（待校验）','overall (under review)'),'text_edit','code_edit','formula_edit','table_edit',metric_labels['table_TEDS'] if corrected else h('table_TEDS（待校验）','table_TEDS (under review)'),h('成功页 / 总页数','Successful / all pages'),h('均值耗时','Mean latency'),h('P50 耗时','P50 latency'),h('P95 耗时','P95 latency')]
  core_note=_wmb_timing_note(english)
  core_note+=h(' TEDS 和 overall 为修正后复算值。',' TEDS and overall are recomputed after correction.') if corrected else h(' 原始 TEDS 存在已证实的计算缺陷；它和 overall 不标领先者。',' Original TEDS has a proven calculation defect; it and overall have no highlighted leader.')
  core_note+=' '+(h(' Moli 两版耗时采用同一批 200 页交叉复测的第二轮；旧耗时与完整纠正收据保留供核对。其余四种方法仍来自各自原批次。',' Moli timing uses round two of the same 200-page crossover; prior timing and the full correction receipt remain available. The other four methods retain their original separate batches.') if latency_corrected else h(' Moli 两版来自独立耗时批次，不能凭此表断定 1.1.7 更慢；下方同页交叉复测轮换了调用顺序。',' The Moli versions were timed in separate batches; this table does not establish that 1.1.7 is slower. The matched crossover below alternates call order.'))
  core_note+=' '+link('review/index.html',h('原始分与修正依据 →','Original scores and correction evidence →'))
  timing_key='<div class="wmb-reading-key"><span><b>'+h('质量分','Quality scores')+'</b> '+h('按每项下方的计分页数求平均','Averaged over the pages shown under each component')+'</span><span><b>'+h('均值耗时 · P50 · P95','Mean latency · P50 · P95')+'</b> '+h('仅最终成功 fetch；不加前次失败时间，成功页数单列','Final successful fetch only; previous failures are not added, and the success count is shown separately')+'</span></div>'
  coverage_note=''
  if latency_diagnostic:
   failed_case=latency_diagnostic['failure_retest']['case_id']
   coverage_note='<p class="source-note">'+h('成功页数反映浏览器读取本地测试服务器所提供冻结网页的结果。一页自带脚本跳转原站，Moli 两版重试后仍被隔离网络阻断；禁用脚本能返回正文，但改变了测试条件。该页的失败保留，不能把 199/200 解释成版本固有能力或实时抓取成功率。','Success counts describe browsers reading frozen pages from the local test server. One page script navigates to its live origin; both Moli versions still fail after a retry under network isolation. Disabling the script returns body text but changes the test condition. The failure stays in the results; 199/200 is neither an inherent version-capability claim nor a live-fetch success rate.')+' '+link('datasets/'+failed_case+'.html',h('查看该页证据','Inspect this page'))+'</p>'
  home+=section(h('核心结果','Core results'),timing_key+'<p class="source-note">'+core_note+'</p>'+table(core_headers,core_rows(english),'wmb-core-results',True,cls='wmb-core-table',metrics=[],default_sort=(2,'desc'))+coverage_note+_wmb_table_population(records,names,labels,english,prefix='')+'<details><summary>'+h('公式计分示例与计算流程','Formula-scoring example and flow')+'</summary>'+_wmb_denominator_example(records,english,case_count)+'</details>'+f'<p class="actions">{link("analysis/index.html",h("筛选逐页结果与响应 →","Filter page-level results and responses →"))} · {link("methodology/index.html",h("完整评分规则 →","Full scoring rules →"))} · {link("review/index.html",h("官方原始分与纠正记录 →","Original scores and corrections →"))}</p>')
  home+=section(h('相同页面上的耗时','Timing on identical pages'),_wmb_matched_latency(records,names,labels,english,prefix=''),'matched-latency')
  if latency_diagnostic:
   home+=section(h('Moli 版本耗时比较','Moli version latency comparison'),_wmb_latency_rerun(latency_diagnostic,english,formal_round=formal_round),'moli-latency-rerun')
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
  flow_source=(r'''

flowchart LR
    A["1  冻结 HTML<br/>同一批 200 页"] --> B["2  六种方法转换<br/>每页得到最终响应"]
    B --> C{"3  最终调用<br/>是否成功"}
    C -->|"失败"| D["结果详情<br/>错误类型 · 响应 · 原因"]
    C -->|"成功"| E["4  与人工 Markdown 比较<br/>文本 · 代码 · 公式 · 表格"]
    E --> F["5  逐页分数<br/>两侧都没有该类内容：跳过<br/>只有一侧有：计 0 分"]
    F --> G["6  分项均值<br/>分数下方显示计入页数 / 200"]
    E --> H["排除空内容与错误页<br/>最终成功 fetch：均值 · P50 · P95"]''' if not english else r'''flowchart LR
    A["1  Frozen HTML<br/>the same 200 pages"] --> B["2  Six methods convert<br/>one final response per page"]
    B --> C{"3  Did the final<br/>call succeed?"}
    C -->|"No"| D["Result detail<br/>error type · response · cause"]
    C -->|"Yes"| E["4  Compare with human Markdown<br/>text · code · formula · table"]
    E --> F["5  Per-page components<br/>absent on both sides: skipped<br/>present on one side: scored zero"]
    F --> G["6  Component means<br/>scored pages / 200 shown below score"]
    E --> H["Exclude empty content and error pages<br/>final successful fetch: mean · P50 · P95"]''')
  flow_fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback>'+''.join(f'<span><b>{index}</b> {label}</span>' for index,label in enumerate(([h('冻结输入','Frozen input'),h('方法转换','Conversion'),h('检查响应与正文','Inspect response and body'),h('逐页评分','Per-page scoring'),h('按计入页数求均值','Mean over scored pages')]),1))+'</div>'
  flow='<div class="wmb-flow">'+flow_fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'
  method+=section(h('一页怎样变成一行结果','How one page becomes one result'),'<p class="source-note">'+h('先看整条路径，再按需展开下面的实现细节。正文是否生成、接口是否响应和为什么为空，属于逐页结果；质量分只回答已生成内容与人工答案有多接近。','Start with the full path, then open implementation details only when needed. Whether body text exists, whether the interface responded and why it is empty belong to the page result; quality scores only describe similarity to the human reference.')+'</p>'+flow)
  input_text=h('每个方法接收预选 200 页官方 <code>html</code> 字段的同一组 UTF-8 字节。四款本地方法从 OS 网络隔离的 loopback HTTPS 读取原样正文，每批使用专用临时 CA；HTTPS 避免冻结页因协议差异触发伪跳转。它们的原始批次覆盖母集 545 页，报告只投影到预先冻结的 200-ID 清单，不按结果选题。Kitesurf 从头只执行这 200 页，使用 Cloudflare 官方 <code>html</code> 输入并拒绝外部资源类型。冻结 HTML 自带的内联脚本可以执行，但非 loopback 子资源和数据请求不能出网；原 URL 只保留为来源元数据。每个方法内部严格串行；不同方法的批次可重叠，因此耗时是本轮客户端观测，不是隔离硬件微基准。',"Every method receives the same UTF-8 bytes from the official <code>html</code> field for the preselected 200 pages. Four local methods read the unchanged body over loopback HTTPS under an OS network sandbox, using a dedicated ephemeral CA per batch; HTTPS prevents false redirects caused only by a scheme mismatch. Their source batches measured all 545 parent pages, while this report projects only the pre-frozen 200-ID manifest and never selects by outcome. Kitesurf runs these 200 pages from scratch through Cloudflare's documented <code>html</code> input with external resource types rejected. Inline scripts contained in the frozen HTML may run, but non-loopback subresource and data requests cannot reach the network; the original URL is evidence metadata only. Calls are serial within each method; provider batches may overlap, so latency is this run's client observation rather than an isolated hardware microbenchmark.")
  cue_text=h('数据 HTML 含公开标注属性 <code>cc-select</code>；六种通用配置均未按该属性定制，但这是数据集固有的潜在线索。','The dataset HTML contains public <code>cc-select</code> annotation attributes. None of the six generic configurations is customized to use them, but they remain an inherent benchmark cue.')
  method+=section(h('输入与运行边界','Input and runtime boundary'),'<div class="wmb-input-routes"><article><b>'+h('Kitesurf · Cloudflare 接口','Kitesurf · Cloudflare API')+'</b><code>'+h('冻结 HTML → API html 字段 → Markdown','Frozen HTML → API html field → Markdown')+'</code><span>'+h('直接提交同一份 HTML 字节，不让远端重新抓原网址。','The same HTML bytes are submitted directly; the remote service does not refetch the source URL.')+'</span></article><article><b>Lightpanda · Moli · Obscura · Chromium</b><code>'+h('冻结 HTML → 本地 HTTPS → 浏览器 → Markdown','Frozen HTML → local HTTPS → browser → Markdown')+'</code><span>'+h('浏览器打开本地地址，服务器返回同一份 HTML。','The browser opens a local URL whose server returns the same HTML.')+'</span></article></div><p>'+h('每页有一份冻结 HTML 和一份人工校准 Markdown。六种方法转换同一份 HTML；原始 URL 只记录来源，本轮不访问实时网站。','Each page has frozen HTML and a human-calibrated Markdown reference. All six methods convert the same HTML; the original URL records provenance and is not fetched live in this run.')+'</p><details><summary>'+h('查看网络隔离、本地 HTTPS 服务和批次细节','Network isolation, local HTTPS server and batch details')+'</summary><p>'+input_text+'</p><p class="callout">'+cue_text+'</p></details><p class="actions">'+link('../datasets/index.html',h('选一页查看完整输入和答案','Open a page with full input and reference'))+'</p>','input-routes')
  method+=section(h('表格怎样进入评分','How tables enter scoring'),_wmb_table_example(english),'table-scoring')
  formulas=[('text_edit',h('完整 Markdown 与人工答案','Full Markdown against the human reference'),h('字符编辑距离 ÷ 两份文本中较长者的长度；导航、页脚等多余内容也会拉低分数。','Character edit distance divided by the longer text; extra navigation and footer content can lower the score.')),('code_edit',h('从两份 Markdown 找出的代码块','Code blocks found in the two Markdown files'),h('只比较提取出的代码字符，按较长代码长度归一化。','Compare extracted code characters, normalized by the longer code length.')),('formula_edit',h('从两份 Markdown 找出的公式','Formulas found in the two Markdown files'),h('只比较提取出的公式字符，按较长公式长度归一化。','Compare extracted formula characters, normalized by the longer formula length.')),('table_edit',h('从两份 Markdown 找出的表格内容','Tables found in the two Markdown files'),h('比较规范化后的表格字符；普通段落里有表格字样，不等于输出了可识别的表格。','Compare normalized table characters; table words in a paragraph do not form a recognized table.')),('table_TEDS',h('Markdown 表格还原出的“表格—行—单元格”结构','The table–row–cell structure reconstructed from Markdown'),h('先把 Markdown 表格还原成表格、每一行和每个单元格，再计算把输出结构改成人工答案结构需要多少次增、删、改。只有单元格文字、没有可识别的 Markdown 表格语法时，无法还原表格结构。','Reconstruct tables, rows and cells from Markdown, then count the insertions, deletions and changes needed to turn the output structure into the reference structure. Cell text without recognizable Markdown table syntax cannot be reconstructed as a table.'))]
  method+=section(h('每项分数怎么来','How component scores are produced'),'<p>'+h('先从实际输出和人工答案里分别识别代码、公式和表格；text_edit 则保留整份 Markdown。字符项的分数＝1 − 编辑距离 ÷ 较长文本长度，完全相同为 100%，只剩一侧内容时就是 0%。表格结构项按下文记录的计算缺陷修正后复算。','The scorer first extracts code, formulas and tables from the output and reference; text_edit instead keeps the entire Markdown. For character metrics, score = 1 − edit distance / longer length: identical content scores 100%, while content on only one side scores 0%. The table-structure component is recomputed after correcting the calculation defect documented below.')+'</p>'+table([h('指标','Metric'),h('比较对象','Compared content'),h('计算与读法','Calculation and reading')],[('',cell(k)+cell(subject)+cell(explanation)) for k,subject,explanation in formulas],cls='wide-prose-table',metrics=[])+f'<p>{h("使用官方提交 <code>9d991bd</code> 的确定性 <code>use_llm=false</code> 配置。代码、公式或表格在两侧都没有时，官方编辑距离返回失败，该项退出均值；只有一侧有内容时，编辑距离正常计算为 0 分并进入均值。","The pinned official commit is <code>9d991bd</code> with deterministic <code>use_llm=false</code>. When code, formulas or tables are absent on both sides, the official edit-distance metric returns a failed result and the component is excluded from the mean. When content exists on only one side, edit distance succeeds with a zero score and the page enters the mean.")}</p>')
  denominator_rows=[('',cell(h('两侧都没有','Absent on both sides'))+cell(h('不计分，不进入该项均值','No score; outside the component mean'))),('',cell(h('只在人工答案中出现','Reference only'))+cell(h('0 分，进入均值','Zero score; included'))),('',cell(h('只在方法输出中出现','Output only'))+cell(h('0 分，进入均值','Zero score; included'))),('',cell(h('两侧都有','Present on both sides'))+cell(h('计算相似度，进入均值','Similarity score; included')))]
  method+=section(h('哪些页面进入每项平均','Which pages enter each component mean'),'<p>'+h('每种方法固定处理 200 页。最终调用失败时不评分；调用成功时，评分器分别从方法输出和人工答案识别该项内容，即使输出后来被识别为错误页，也保留实际计算的分数；“未计算”不等于 0 分。若评分器本身执行失败，该页也不进入该项均值。','Each method receives 200 fixed pages. Failed final calls are not scored. Successful calls are scored from output and reference content; calculated scores remain even when the output is later classified as an error page. “Not calculated” is different from zero. A metric execution error also excludes the page from that component mean.')+'</p>'+table([h('该项内容状态','Component content'),h('官方处理','Official treatment')],denominator_rows,metrics=[])+f'<p class="actions">{link("../index.html#denominator-example",h("看 Moli 公式分的实际 200 页分流","See the actual 200-page Moli formula split"))}</p>','denominator-rules')
  method+=section(h('表格结构分为什么要复算','Why the table-structure score is recomputed'),'<p>'+h('官方固定版本先把“表格—行—单元格”结构写成一段括号字符串，再把字符串直接交给 APTED。APTED 在这里需要的是能逐层读取子项的树；收到字符串后，它没有读到真实的行和单元格。实测一页的输出只有 5 个结构项，人工答案有 570 个，官方记录的编辑距离却小于 1，因而给出 99.8% 的 TEDS。仅把相差的 565 个结构项补齐，代价就不可能小于 565。','The pinned official version first serializes the table–row–cell structure into one bracketed string and then passes the string directly to APTED. APTED needs a tree whose children can be traversed; with the string it does not see the real rows and cells. In one measured page, the output has 5 structural items and the reference has 570, yet the official recorded edit distance is below 1 and the TEDS score is 99.8%. Adding the 565 missing structural items alone cannot cost less than 565 edits.')+'</p><p class="callout failure">'+h('本报告没有改动官方仓库或官方榜单。我们在独立复算器中把同一张表按“表格—行—单元格”交给 APTED，沿用官方的 Markdown 解析、文字处理和增删改代价。浏览器输出、计分页数和耗时都没有变化；官方原始分与修正后复算分并列保留。table_edit 仍比较规范化后的表格 HTML 字符串。','This report does not modify the official repository or leaderboard. An independent recomputation passes the same table to APTED as an explicit table–row–cell tree while retaining the official Markdown parsing, text handling and edit costs. Browser outputs, scored-page counts and latency are unchanged; original official scores and recomputed values are both retained. Table_edit still compares normalized table HTML strings.')+'</p><p class="actions">'+link('../attempts/obscura__949e1468-eed2-43ab-96e6-5dcb07c52d4b__r001.html',h('查看 5 对 570 个结构项的完整答案和输出','Inspect the complete 5-versus-570-item case'))+' · '+link('https://github.com/opendatalab/WebMainBench/blob/9d991bdc00c57b57521499494d96be85c31317ba/webmainbench/metrics/teds_metrics.py',h('官方 TEDS 源码','Official TEDS source'))+'</p>')
  method+=section(h('聚合、计时和不可比较项','Aggregation, timing and non-comparable fields'),'<p>'+h('每项聚合分＝该项成功计算的逐页分数之和 ÷ 实际计入页数；榜单 overall＝五项聚合分的算术平均。逐页 overall 则平均该页成功计算的项目，因此它不能直接拿来替代榜单 overall。逐页显示“未计算”只有两类原因：代码、公式或表格在参考答案和实际输出中都不存在，或者评分器执行失败；详情页会显示该页属于哪一类。','Each component aggregate is the sum of successfully calculated page scores divided by its actual scored-page count; leaderboard overall is the arithmetic mean of the five aggregates. Per-page overall averages only that page’s calculated components, so it cannot replace leaderboard overall. A page-level component is marked “not calculated” for exactly two reasons: the content type is absent from both the reference and output, or the scorer failed. The result detail identifies which reason applies.')+'</p><p>'+h('成功 fetch 耗时只取最终成功调用，从请求开始到转换响应结束，前次失败不相加。全部调用的累计时间另存为执行成本。浏览器批次各自串行，但不同方法可能重叠；Cloudflare 远端时间和本地 CLI 时间处于不同执行环境。是否生成正文、质量分和耗时分开报告，不能混成一个分数。','Successful-fetch time is the final successful call from request start to converted response, excluding earlier failures. Accumulated time across all calls is retained separately as execution cost. Calls are serial within a browser batch, but method batches may overlap; Cloudflare remote time and local CLI time come from different execution environments. Body-text outcome, quality and latency are reported separately, not folded into one score.')+'</p>','calculation')
  cost_rows=[('',cell(labels[name])+cell(str(case_count))+''.join(cell(ms(timing['cost'][name][key])) for key in ('mean','p50','p95'))) for name in names]
  method+=section(h('成功耗时、同题诊断与执行成本','Successful timing, matched diagnosis and execution cost'),'<p>'+h('主表只使用最终成功且有目标正文的 fetch，取 elapsed_ms；成功页数与均值、P50、P95 同时展示。错误页、空内容和最终调用失败均排除。一次机会还是重试不改变最终结果的身份，也不把前次耗时加到成功 fetch 上。','The primary table uses elapsed_ms from the final successful fetch with target content, alongside the success count, mean, P50 and P95. Error pages, empty content and failed final calls are excluded. A retry does not create a separate logical result or add the previous call’s time to the successful fetch.')+'</p><p>'+h('同题诊断取六种配置都成功的页面交集，每种方法用完全相同的页面。这个运行后确定的子集不代表全部 200 页，因此不用于速度排名，也不删除其余页面。每页的原始调用链和累计执行成本保留，便于复核，但不混入成功 fetch 耗时。','The matched diagnosis uses the intersection of successful pages across all six configurations. Every method uses identical pages. This post-execution subset is not a speed ranking for all 200 pages and does not remove any other results. Original call chains and accumulated execution cost remain available for audit, separately from successful-fetch latency.')+'</p><details><summary>'+h('原口径：全部 200 页的累计执行成本（非成功 fetch 速度）','Previous measure: accumulated execution cost on all 200 pages (not successful-fetch speed)')+'</summary><p>'+h('这里保留 total_elapsed_ms：每页所有物理调用耗时相加，再对固定 200 页求均值、P50、P95。它描述这轮执行花费的总时间。','This retains total_elapsed_ms: sum all physical-call durations per page, then calculate mean, P50 and P95 over the fixed 200 pages. It describes execution cost for this run.')+'</p>'+table([h('方法','Method'),h('页数','Pages'),h('均值','Mean'),'P50','P95'],cost_rows,metrics=[])+'</details>','latency-policy')
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
      value=value_html(display_score(aggregate['scores'][metric]),winner=category_winners.get(metric)==name,n=n,population=population,english=english) if n is None or n>0 else '—'
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
   vals+=cell(ms(r['elapsed_ms']),r['elapsed_ms'])+cell(link(target,h('逐项证据 →','Metric evidence →')))
   inspect_rows.append((_wmb_row_attrs(case,r),vals))
  analysis+=section(h('逐页结果与响应','Page results and responses'),'<p class="wmb-metric-note">'+h(f'完整矩阵共有 200×6 条结果，默认先显示 {failed_request_count} 条没有生成正文的转换。每行直接说明是否得到正文，以及接口或程序最后留下的是正文、错误信息、错误页还是无可读响应。点击 Track ID 或方法名进入详情，核对完整响应、人工答案、逐项评分和目前能证实的原因。',f'The complete matrix contains 200×6 results and initially shows the {failed_request_count} conversions without body text. Each row states whether body text was produced and whether the final API/worker material is body text, an error response, an error page or no readable response. Open the track ID or method for the complete response, human reference, component scores and the currently supported cause.')+'</p>'+_wmb_controls(cases,labels,english,default_request='failed',failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('表格可横向滚动查看五项分数和耗时；Track ID 与方法名都可直接进入详情。','Scroll horizontally for component scores and latency; both the track ID and method name open the detail.')+'</p>'+_wmb_filter_table([h('Track ID','Track ID'),h('方法','Method'),h('语言','Language'),h('难度','Difficulty'),h('表格','Table'),h('代码','Code'),h('公式','Formula'),h('是否生成正文','Body text produced'),h('响应与正文','Response and body')]+[metric_labels[m] for m in metric_order[1:]]+[h('最终 fetch 耗时','Final fetch latency'),h('详情','Detail')],inspect_rows,'wmb-attempt-matrix',english))
  _wmb_write(out,'analysis/index.html',h('结果分析','Analysis'),analysis+analysis_tail,english)

  review_body=hero(h('核验记录','Review'),h('保留官方原始结果，记录计算修正的范围和影响；逐页原因见结果详情。','Original official results are retained alongside the scope and effects of corrections; page-level causes are in result details.'))
  raw_prefix='../../' if english else '../'
  if corrected:
   receipt,revision_review=teds_revision
   effects=[]
   for name in names:
    impact=revision_review['impacts'][name];before=impact['official'];after=impact['corrected']
    effects.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(display_score(before['scores']['table_TEDS']))+cell(display_score(after['scores']['table_TEDS']))+cell(display_score(before['scores']['overall']))+cell(display_score(after['scores']['overall']))+cell(str(after['denominators']['table_TEDS']))))
   review_body+=section(h('TEDS 计算修正','TEDS calculation correction'),'<p>'+h(f'同一批 {revision_review["logical_results"]:,} 条结果全部重新计算，{revision_review["changed_scores"]} 条 TEDS 分数变化。没有重新调用浏览器，输入、原始输出、计分页数和耗时均未改变。本报告没有修改官方仓库或官方榜单；表中的“修正后”是报告侧独立复算值。',f'All {revision_review["logical_results"]:,} results were reprocessed; {revision_review["changed_scores"]} TEDS values changed. No browser was called again. Inputs, raw outputs, scored-page counts and latency are unchanged. This report does not modify the official repository or leaderboard; “corrected” values are independently recomputed by this report.')+'</p>'+table([h('方法','Method'),h('原始 TEDS','Original TEDS'),h('修正 TEDS','Corrected TEDS'),h('原始 overall','Original overall'),h('修正 overall','Corrected overall'),h('TEDS 计分页数','Pages scored for TEDS')],effects,'wmb-correction-effects',metrics=[])+
    '<p class="actions">'+link('../methodology/index.html',h('树节点错误及修正原理','Tree-node defect and correction'))+' · '+link(raw_prefix+'teds-correction.json',h('纠正范围与源码校验值','Correction scope and source hashes'))+' · '+link(raw_prefix+'teds-correction-review.json',h('完整性与算术核验','Integrity and arithmetic audit'))+' · '+link(raw_prefix+'original-results.jsonl',h('官方原始逐页结果','Original official page results'))+'</p>')
  if review:
   review_body+=section(h('原始结果证据检查','Original-result evidence checks'),'<p>'+h('原始核验检查了 1,200 条结果的覆盖范围、文件哈希、调用耗时、文本类评分及分项聚合。它不能单独证明评分算法正确，也不能证明每条失败都已找到根因。TEDS 的纠正及重新核验单独记录在上方。','The original audit checked the 1,200-result scope, file hashes, call latency, text metrics and component aggregation. It does not alone establish algorithm validity or the root cause of every failure. The TEDS correction and its follow-up checks are recorded above.')+'</p><p>'+link(raw_prefix+'result-review.json',h('下载原始核验记录','Download the original audit'))+'</p>')
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
   review_body+=section(h('异常结果根因复核','Root-cause review of anomalous results'),'<p>'+h(f'自动规则从 1,200 条结果中触发 {scope["signal_triggered_results"]} 条，实际集中在 {scope["distinct_cases"]} 个页面。复核读取正式输出、人工答案、程序错误和同页六方法差异，并用不计分的删减对照定位触发条件。所有触发项均已归入下列原因；对照输入不是原始任务，因此没有替换调用、分数、分母或耗时。',f'Deterministic rules triggered {scope["signal_triggered_results"]} of 1,200 results, concentrated in {scope["distinct_cases"]} pages. Review used the authoritative outputs, human references, program errors and same-page six-method differences, then non-scoring ablation controls to isolate triggers. Every triggered result maps to a cause below. Controls are not the original task, so no call, score, denominator or latency was replaced.')+'</p>'+table([h('页面','Page'),h('受影响方法','Affected methods'),h('对照观察','Control observation'),h('根因与证据边界','Cause and evidence boundary')],anomaly_rows,cls='wide-prose-table',metrics=[])+f'<p>{link(anomaly_href,h("下载绑定哈希的复核记录","Download the hash-bound review record"))}</p>')
  review_body+=section(h('核验边界','Review boundary'),'<p>'+h(f'正式结果中有 {failed_request_count} 次转换没有目标正文，其中 {error_outputs} 条留下的是浏览器错误页；它们现在都能从逐页详情进入对应的直接证据和已验证原因。异常复核覆盖确定性规则触发的 36 条结果，不等于对其余正常结果逐条做人工作品质量复审。服务未公开的内部资源阈值继续明确标为不可再细分。',f'The authoritative matrix has {failed_request_count} conversions without target body text, including {error_outputs} browser error-page output. Each now links from its detail page to direct evidence and a verified cause. The anomaly review covers the 36 deterministically triggered results; it is not a manual editorial-quality review of every remaining normal result. Undisclosed service-internal resource thresholds remain explicitly indivisible.')+'</p><p>'+link('../analysis/index.html#inspect?request=failed',h('查看没有正文的结果','Inspect results without body text'))+'</p>')
  if fixture_correction:
   correction_rows=[]
   for provider,effect in fixture_correction['effects'].items():
    correction_rows.append(('',cell(link('../attempts/'+provider+'__'+fixture_correction['case_id']+'__r001.html',labels[provider]))+cell(pct(effect['old_text_edit']))+cell(pct(effect['new_text_edit']))+cell(esc(effect['old_output_sha256'][:12]))+cell(esc(effect['new_output_sha256'][:12]))))
   receipt_href='../../fixture-correction.json' if english else '../fixture-correction.json'
   review_body+=section(h('冻结页查询跳转的纠正','Frozen-page query-navigation correction'),'<p>'+h('一页冻结 HTML 在运行脚本后给当前路径加查询参数；原本地测试服务器只接受无查询参数的路径，三款方法因此拿到 404 错误页，却被记为有输出。现在仍从同一冻结 HTML 开始，本地测试服务器按路径找到同一页，原始网页 URL 仍不参与抓取。该页三款方法用相同二进制和原评分器重新测量；旧证据保留在独立存档里。','One frozen page appended a query parameter after its inline script ran. The former local test server served only the bare path, so three methods converted a 404 error page that was counted as output. The local test server now serves the same frozen page by path even with a query; the original source URL is still not fetched. The three methods were remeasured with matching binaries and the unchanged scorer; prior evidence remains in a separate archive.')+'</p>'+table([h('方法','Method'),h('原页 text_edit','Prior page text_edit'),h('纠正后 text_edit','Corrected page text_edit'),h('旧输出校验前缀','Prior output digest prefix'),h('新输出校验前缀','New output digest prefix')],correction_rows,cls='wide-prose-table',metrics=[])+f'<p>{link(receipt_href,h("下载完整纠正影响","Download complete correction effects"))}</p>')
  _wmb_write(out,'review/index.html',h('核验记录','Review'),review_body,english)

  failure_notes={}
  for finding in (anomaly_review or {}).get('cases',[]):
   observed=finding['observed_en' if english else 'observed_zh']
   cause=finding['cause_en' if english else 'cause_zh']
   for provider in finding.get('affected_providers',[]):
    failure_notes[(provider,finding['case_id'])]=(observed,cause)
  guide=_wmb_editorial_guide(english=english,names=names,labels=labels,cases=cases,
                             bycase=bycase,case_count=case_count,score=score,denom=denom,
                             failures=failures,out=out,latency_diagnostic=latency_diagnostic,
                             formal_round=formal_round)
  _wmb_write(out,'guide/index.html',h('报告解读','Guide'),guide,english)

  provider_rows=[]
  identities={m['provider']:m['implementation_identity'] for m in manifests}
  for name in names:
   ident=identities[name]
   provider_rows.append(('',cell(labels[name])+cell(esc(ident.get('reported_version') or h('托管后端未公开 / nightly 无版本命令','Hosted backend undisclosed / nightly has no version command')))+cell('<code class="tiny break">'+esc(ident.get('binary_sha256') or '—')+'</code>')+cell(esc(ident.get('deployment')))))
  adoption=hero(h('方法介绍','Methods'),h('比较六种执行配置，其中 Moli 1.1.5 与 1.1.7 分别保留结果。','The comparison covers six configurations, including separate results for Moli 1.1.5 and 1.1.7.'))
  adoption+=section(h('本轮实现','Measured implementations'),table([h('方法','Method'),h('版本','Version'),h('二进制 SHA-256','Binary SHA-256'),h('部署','Deployment')],provider_rows,metrics=[]))
  interface_rows=[]
  routes={
   'kitesurf':(h('Cloudflare Browser Run /markdown?browser=kitesurf','Cloudflare Browser Run /markdown?browser=kitesurf'),h('POST html=官方冻结 HTML；拒绝外部资源类型','POST html=official frozen HTML; reject external resource types'),h('服务端原生 Markdown','Server-native Markdown')),
   'lightpanda':('Lightpanda CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
   'obscura':('Obscura CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
   'moli':('Moli CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
   'moli-1.1.7':('Moli CLI',h('fetch --dump markdown；loopback HTTPS 冻结页面','fetch --dump markdown; frozen loopback HTTPS page'),h('CLI 原生 Markdown','CLI-native Markdown')),
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
   adoption+=section(h('开源采用线索','Open-source adoption evidence'),'<p>'+h('这份独立源码快照包括已登记方法，不只包括本轮六种方法。搜索候选有排序、活跃度和星数门槛；数值是已核验候选集中的签名命中，不是全网份额或生产用户数。点击方法可核对仓库和文件。','This independent source snapshot covers registered methods, not only the six measured configurations. Search candidates have ranking, activity and star thresholds; the counts are verified signature hits in the bounded candidate set, not global share or production users. Open a method to inspect repositories and files.')+'</p><p class="source-note">'+esc(adoption_data['generated_at'])+' · '+link(data_prefix+'evidence.json',h('下载完整来源证据','Full source evidence'))+' · '+link(data_prefix+'counts.json',h('下载逐方法统计','Per-method counts'))+'</p>'+table([h('方法','Method'),h('命中仓库','Matched repositories'),h('命中文件','Matched files')],count_rows,'wmb-adoption-counts',True,metrics=[])+''.join(details))
  _wmb_write(out,'adoption/index.html',h('方法介绍','Methods'),adoption,english)

  reproduce=hero(h('环境与复现','Reproduction'),h('数据、评分器、浏览器与结果都由哈希或提交固定。','Dataset, scorer, browsers and outputs are fixed by hashes or commits.'))
  reproduce+=section(h('核心复现指令','Core reproduction commands'),'<p>'+h('以下命令使用本仓库锁定依赖；运行六种方法和官方评分需要先取得公开母集、固定提交的评分器和本轮方法二进制。评测凭据只从仓库本地 .env 读取，不进入报告。命令中的尖括号为本机路径占位符。','The commands use this repository’s locked dependencies. Running all six methods and official scoring first requires the public parent set, the pinned scorer checkout and the measured binaries. Evaluation credentials come only from the local repository .env and are not included in this report. Angle brackets denote local paths.')+'</p><pre>uv run --frozen --extra headless-eval python scripts/run_webmainbench_benchmark.py --provider &lt;name&gt; --case-ids-file evaluation/public-webmainbench-200.json --output &lt;method-evidence&gt;\nuv run --frozen --extra webmainbench-eval python scripts/score_webmainbench_results.py --evidence &lt;method-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen python scripts/assemble_webmainbench_evidence.py --source-root &lt;six-method-batches&gt; --output &lt;assembled-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --sample-manifest evaluation/public-webmainbench-200.json --review &lt;independent-review.json&gt;\nuv run --frozen --extra webmainbench-eval python scripts/correct_webmainbench_teds.py --source &lt;assembled-evidence&gt; --output &lt;corrected-evidence&gt; --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen --extra webmainbench-eval python scripts/review_webmainbench_teds_correction.py --source &lt;assembled-evidence&gt; --corrected &lt;corrected-evidence&gt;\nmake public-benchmark-report EVIDENCE=&lt;corrected-evidence&gt;</pre>')
  reproduce+=section(h('固定标识','Pinned identities'),f'<p>Parent dataset SHA-256: <code>{esc(dataset["source_dataset_sha256"])}</code></p><p>Sample manifest SHA-256: <code>{esc(dataset["sample_manifest_sha256"])}</code> · {link("../sample-manifest.json" if not english else "../../sample-manifest.json",h("下载 200 页清单","Download 200-page manifest"))}</p><p>Official scorer commit: <code>9d991bdc00c57b57521499494d96be85c31317ba</code></p>')
  batch_rows=[]
  for manifest in manifests:
   batch_rows.append(('',cell(labels[manifest['provider']])+cell(esc(manifest['started_at']))+cell(esc(manifest['completed_at']))+cell(str(manifest['case_count']))+cell(str(manifest['timeout_seconds']))+cell('<code class="tiny break">'+esc(manifest['runner_sha256'])+'</code>')))
  reproduce+=section(h('实际执行批次','Observed execution batches'),table([h('方法','Method'),h('开始（UTC）','Started (UTC)'),h('完成（UTC）','Completed (UTC)'),h('案例','Cases'),h('截止秒','Deadline seconds'),h('Runner SHA-256','Runner SHA-256')],batch_rows,metrics=[]))
  _wmb_write(out,'reproduce/index.html',h('环境与复现','Reproduction'),reproduce,english)

  release_path=out/'report-changes.json'
  if not release_path.is_file():
   release_data={'schema_version':2,'releases':webmainbench_history(json.loads((ROOT/'research/report-changes.json').read_text(encoding='utf-8')))}
   release_path.write_text(json.dumps(release_data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  else:
   release_data=json.loads(release_path.read_text(encoding='utf-8'))
  release_links=(
   ('latency-corrections.json','查看逐页纠正收据','Inspect the per-page correction receipt'),
   ('prior-latency-results.jsonl','下载原始逐页结果','Download prior per-page results'),
  ) if latency_corrected else ()
  changes=render_webmainbench_history(release_data,english,release_links)
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
   body=hero(h('冻结页面','Frozen page')+' · '+c['id'],h('官方网页快照与人工校准参考文本','Official HTML snapshot and human-calibrated reference'))+'<nav class="wrap actions">'+link('#frozen-input',h('查看网页存档','Read archived page'))+' · '+link(c['url'],h('打开来源网站 ↗','Open source website ↗'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('比较方法输出','Compare method outputs'))+'</nav>'
   body+=section(h('来源与元数据','Source and metadata'),f'<p>{link(c["url"],h("原始 URL ↗","Original URL ↗"))}</p><pre>{esc(json.dumps(c["meta"],ensure_ascii=False,indent=2))}</pre><p>HTML SHA-256: <code>{esc(c["input_html_sha256"])}</code></p>')
   body+=section(h('人工校准参考文本','Human-calibrated reference'),'<p>'+h('这份 Markdown 是官方标注的主内容参考，不包含整页导航和推荐区，也不保证覆盖图片中的文字。评分器从转换结果与参考文本中提取正文、代码、公式和表格，并计算五项分数。','This Markdown is the official main-content reference, not the entire page; it excludes navigation and recommendations and does not guarantee coverage of text in images. The scorer extracts text, code, formulas and tables from each conversion and reference, then calculates five components.')+'</p><p class="actions">'+link(ref_prefix+'references/'+c['id']+'.md',h('下载参考文本','Download reference'))+' · '+link('../methodology/index.html',h('查看官方计算方法','Official scoring method'))+'</p><pre class="wmb-source-pre">'+esc(ref)+'</pre>')
   body+=section(h('冻结网页阅读视图','Frozen-page reading view'),'<p class="actions">'+link(c['url'],h('来源网页 ↗（实时内容，可能已变化）','Source website ↗ (live, may have changed)'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('查看该页方法结果','Inspect method results for this page'))+'</p><p class="source-note">'+h('下方直接展示本轮 HTML 存档。为安全阅读，预览禁用脚本、外部资源及页面跳转；图片以占位文字显示。这是静态阅读视图，不是实测浏览器截图，也不重现运行脚本后的页面。完整输入保留在源码中。','Read the HTML archive used in this run below. This safe preview disables scripts, external resources and navigation; images appear as placeholders. It is a static reading view, not a measured browser screenshot or a replay of script execution. The complete input remains in the source view.')+'</p>'+_wmb_html_preview(frozen_html,english)+'<details><summary>'+h('查看完整 HTML 源码','View complete HTML source')+'</summary><pre class="wmb-source-pre">'+esc(frozen_html)+'</pre><p>'+link(ref_prefix+'sources/'+c['id']+'.html.txt',h('另存原始文件','Save raw file'))+'</p></details>','frozen-input')
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
    values+=cell(ms(r['elapsed_ms']),r['elapsed_ms'])+cell(link('../datasets/'+r['case_id']+'.html',h('页面与参考','Page and reference')))
    rows.append((_wmb_row_attrs(c,r),values))
   body=hero(labels[name],h(f'{case_count} 条最终结果；耗时取最终 fetch，不累计先前失败。',f'{case_count} final results; latency is the final fetch duration, excluding earlier failures.'))
   body+=section(h('逐页结果','Per-page results'),'<p class="source-note">'+h('点 Track ID 查看评分、完整响应和原因，末列打开冻结页面与人工参考。“正文已生成”只说明存在可读 Markdown，不等于内容通过；错误页会在“响应与正文”列明确标出。','Open a track ID for scores, the complete response and cause; the last column opens the frozen page and human reference. “Body text produced” only states that readable Markdown exists, not that content passed; error pages are explicit in the response column.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('表格可横向和纵向滚动。','Scroll horizontally and vertically within the table.')+'</p>'+_wmb_filter_table([h('Track ID','Track ID'),h('语言','Language'),h('难度','Difficulty'),h('表格','Table'),h('代码','Code'),h('公式','Formula'),h('是否生成正文','Body text produced'),h('响应与正文','Response and body') ]+[metric_labels[m] for m in metric_order[1:]]+[h('最终 fetch 耗时','Final fetch latency'),h('页面与参考','Page and reference')],rows,'wmb-provider-'+name,english))
   _wmb_write(out,'providers/'+name+'.html',labels[name],body,english)

  for r in records:
   c=cases[r['case_id']];case_id=r['case_id'];attempt_id=r['attempt_id']
   output_path=contained_path(out,r['artifact_path'])
   output_text=output_path.read_text(encoding='utf-8',errors='replace') if output_path.is_file() else ''
   reference_text=contained_path(out,'references/'+case_id+'.md').read_text(encoding='utf-8')
   attempt_body=_wmb_result_detail(r,c,output_text,reference_text,labels,metric_labels,failure_notes,corrected,english)
   _wmb_write(out,'attempts/'+attempt_id+'.html',labels[r['provider']]+' · '+case_id,attempt_body,english)
 from harness.report_languages import write_language_contract
 write_language_contract(out,include_english=include_english)
