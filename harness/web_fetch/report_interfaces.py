"""Interfaces actually used by the benchmark, not a claim to cover entire SDKs."""
import json
import re
from html import escape
from lexbench_webfetch.paths import contained_path
from .methods import native_browser_engine, browser_method_label
from .report_components import measurement_presentation

from .methods import report_method, report_method_catalog

# Views of the shared catalog; execution aliases inherit their engine's metadata.
SPECS = {name: item['interface'] for name, item in report_method_catalog()['methods'].items()
         if item.get('interface')}
HEADLESS_ACCESS = {name: item['access'] for name, item in report_method_catalog()['methods'].items()
                   if item.get('access')}

def method_provenance(out):
 path=out/'implementation-provenance.json'
 return {item['binary_sha256']:item for item in json.loads(path.read_text()).get('implementations',[])} if path.is_file() else {}


def method_access(name, records):
 selected=next((row for row in records if row.get('provider')==name),None)
 registered=report_method(name,selected)
 access=registered.get('access')
 if access:
  template=registered.get('release_url_template')
  versions=set()
  for row in records:
   if row.get('provider')!=name:continue
   value=row.get('provider_metadata',{}).get('reported_version','')
   match=re.fullmatch(registered.get('release_version_pattern',r'(?!)'),value)
   if match:versions.add(match.group(1))
   else:versions.add(None)
  if template and len(versions)==1 and None not in versions:
   version=next(iter(versions))
   return {**access,'links':[(registered['label']['zh']+' '+version,template.format(version=version))]}
  return access
 if registered:return None
 return {'account':'未登记 / Not registered',
         'configuration':'Account and installation requirements have not been registered.',
         'env_vars':[], 'links':[]}


def method_spec(name, record, *, timeout_seconds=None):
 if record and record.get('provider_type')=='moli_webfetch':
  base=SPECS['moli-webfetch']
  meta=record.get('provider_metadata') or {}
  policy=meta.get('internal_timeout_policy')
  budget=(f'{timeout_seconds:g} 秒外层截止' if isinstance(timeout_seconds,(int,float)) and not isinstance(timeout_seconds,bool) and timeout_seconds>0 else '外层截止时间未记录')
  if policy=='native_default':
   timeout_description=budget+'；Moli 总等待、HTTP 和连接超时保留该二进制默认值'
   timeout_note='这批调用未传入 Moli 内部超时参数。'
  elif policy=='explicit':
   timeout_ms=meta.get('http_connect_timeout_ms')
   timeout_description=(budget+'；实测命令同时显式传入 Moli 总等待、HTTP 和连接超时'
                        +((' '+f'{timeout_ms:,}'+' 毫秒') if isinstance(timeout_ms,int) else ''))
   timeout_note='这批调用使用保存记录中的显式 Moli 超时，未写成产品默认值。'
  else:
   timeout_description=budget+'；Moli 内部超时以逐次调用记录为准'
   timeout_note='保存证据未提供可核对的 Moli 内部超时策略。'
  params=[(key,kind,timeout_description if key=='外层截止' else description)
          for key,kind,description in base['params']]
  entry=base['entry'] if name in SPECS else 'CLI · Moli · 路线以逐次调用记录为准'
  return {**base,'entry':entry,'params':params,'notes':base['notes']+' '+timeout_note}
 registered=report_method(name,record)
 base=registered.get('interface') or {
  'entry':'未登记 / Not registered','auth':'Not registered','params':[],
  'fields':[],'request':{},'source':'Not registered',
  'notes':'Interface metadata has not been registered; consult saved call evidence.'}
 meta=(record or {}).get('provider_metadata',{})
 if registered.get('dom_conversion'):
  request={**base['request']}
  params=list(base['params'])
  wait=meta.get('navigation_wait_until')
  if wait in {'load','domcontentloaded','networkidle','commit'}:
   request['wait_until']=wait
   params=[(key,kind,wait if key=='wait_until' else description)
           for key,kind,description in params]
  converter_sha=meta.get('converter_sha256')
  notes=base['notes']
  if isinstance(converter_sha,str) and re.fullmatch(r'[0-9a-f]{64}',converter_sha):
   request['converter_sha256']=converter_sha
   params=[(key,kind,
            description+'；包装脚本 SHA-256 '+converter_sha
            if key=='converter' else description)
           for key,kind,description in params]
   notes+=' 转换包装还负责代码块围栏与语言标签；本轮以完整脚本 SHA-256 绑定，不把它写成未修改的包默认行为。'
  return {**base,'request':request,'params':params,'notes':notes}
 native={k:v for k,v in meta.get('native_metadata',{}).items() if k in {'format','script_sha256'}}
 format_name=native.get('format')
 if (name,format_name) not in {('lightpanda','lightpanda_json'),('obscura','obscura_eval')}:return base
 command=('lightpanda fetch URL --dump markdown --json' if name=='lightpanda'
          else 'obscura fetch URL --eval <upstream-native-markdown-and-location>')
 request={**base['request'],'command':command,'native_metadata':native}
 if name=='obscura':request.pop('dump',None)
 return {**base,'entry':'CLI · '+command,'request':request,
         'params':[row for row in base['params'] if name!='obscura' or row[0]!='--dump'],
         'notes':'Lightpanda 解包同一次调用返回的原生 Markdown；Obscura 执行固定哈希的官方 Markdown 脚本。网页正文、嵌入文档以及网页中的图片或视频信息均保留该原生输出。直接文件分别使用 Lightpanda CDP 与 Obscura --dump original。评测端不再加工 DOM 或外部解析文档；获取与输出处理均计入耗时。'}



def _observed_wait(meta):
 navigation=meta.get('navigation_wait_until')
 completion=meta.get('wait_until')
 if navigation and completion and navigation!=completion:
  return f'导航等待 {navigation}；输出完成条件 {completion}'
 if navigation:return f'导航等待 {navigation}'
 if completion:return f'输出完成条件 {completion}'
 return '调用记录未提供可核对的等待条件'


def _observed_route(engine, meta):
 """Describe only the interface persisted by one measured call."""
 route=meta.get('route')
 extractor=meta.get('extractor')
 contract=meta.get('output_contract')
 dump=meta.get('dump')
 browser=meta.get('browser')
 native=meta.get('native_metadata') or {}
 route_note=f'（route={route}）' if route else ''
 if engine=='moli' and extractor=='moli_native_markdown' and dump=='markdown':
  flags=' --layout --with-frames' if (meta.get('layout') and meta.get('with_frames')) else ''
  return 'moli fetch --dump markdown'+flags+' → Moli 原生 Markdown'+route_note
 if engine=='lightpanda' and extractor=='lightpanda_native_markdown':
  json_flag=' --json' if native.get('format')=='lightpanda_json' else ''
  return 'Lightpanda --dump markdown'+json_flag+' → 原生 Markdown'+route_note
 if engine=='obscura' and extractor=='obscura_native_markdown':
  script='（脚本 SHA-256 '+native['script_sha256']+'）' if native.get('script_sha256') else ''
  return 'Obscura --eval 官方 Markdown 脚本'+script+' → 原生 Markdown'+route_note
 if engine=='chromium' and extractor=='node-html-markdown':
  version=(' '+str(meta['extractor_version'])) if meta.get('extractor_version') else ''
  return 'Chromium 渲染 HTML → node-html-markdown'+version+' → Markdown'+route_note
 if (engine=='kitesurf' and browser=='kitesurf'
     and extractor=='cloudflare_native_markdown'):
  return 'Cloudflare /markdown?browser=kitesurf → 托管原生 Markdown'+route_note
 if contract=='original_file_bytes':
  origins={
   'moli_cdp_response':'Moli CDP 原始响应',
   'lightpanda_cdp_response':'Lightpanda CDP 原始响应',
   'obscura_original_response':'Obscura --dump original 原始响应',
   'kitesurf_cdp_response':'Kitesurf 官方 CDP 原始响应',
   'browser_native_response_or_download':'Chromium 原始响应或原生下载',
  }
  source=origins.get(extractor,('调用记录 extractor='+str(extractor)
    if extractor else '调用记录未注明原始文件接口'))
  return source+' → 原始文件字节'+route_note
 fields=[]
 for key,value in (('route',route),('extractor',extractor),
                   ('output_contract',contract),('dump',dump)):
  if value:fields.append(f'{key}={value}')
 if fields:
  return '调用记录：'+'；'.join(fields)+'；未据此补写未记录的转换步骤'
 return '调用记录未提供可核对的接口路线；不推断原生 Markdown、HTML 或额外转换'


def browser_task_interface_matrix(cases, providers, records=None, *, compact=False):
 """Describe task routes, preferring persisted call metadata when available."""
 from collections import Counter
 from types import SimpleNamespace
 from .providers.headless import output_mode_for_case
 routes={name:item.get('routes',{}) for name,item in report_method_catalog()['methods'].items()}
 capabilities={name:item.get('capabilities','Not registered') for name,item in routes.items()}
 markdown={name:item.get('markdown','Not registered') for name,item in routes.items()}
 original={name:item.get('original','Not registered') for name,item in routes.items()}
 resource={name:item.get('resource','Not registered') for name,item in routes.items()}
 runtime={name:item.get('runtime','Not registered') for name,item in routes.items()}
 waits={name:item.get('waits','Not recorded') for name,item in routes.items()}
 tracks=[]
 from .models import REPORT_FAMILIES, report_family
 for case in cases.values():
  track=report_family(case)
  if track not in tracks:tracks.append(track)
 order=list(REPORT_FAMILIES)
 tracks.sort(key=lambda track:order.index(track) if track in order else len(order))
 rows=[]
 indexed={(row['provider'],row['case_id']):row for row in (records or [])}
 case_ids={id(case):case_id for case_id,case in cases.items()}
 for track in tracks:
  group=[case for case in cases.values() if report_family(case)==track]
  cells={}
  for provider in providers:
   engine=native_browser_engine(provider)
   if engine not in capabilities:
    cells[provider]=[{'mode':'unregistered','count':len(group),'description':'未登记 / Not registered','wait':'Not recorded'}]
    continue
   parts=[];counts=Counter()
   for case in group:
    case_id=case.get('id',case_ids[id(case)])
    mode=output_mode_for_case(engine,'headless_markdown',SimpleNamespace(
     url=case['url'],track=case.get('track','web'),task_kind=case.get('task_kind','body')))
    measured=indexed.get((provider,case_id))
    if records is not None:
     meta=(measured or {}).get('provider_metadata') or {}
     counts[(mode,_observed_route(engine,meta),_observed_wait(meta))]+=1
    else:
     counts[(mode,None,None)]+=1
   for (mode,observed_description,observed_wait),count in counts.items():
    if mode=='markdown':
     if track=='document' and engine=='moli':
      description=markdown[engine]+'；HTML/TXT 可直接返回正文，直接 PDF/DOCX 未提供可读正文；评测端不使用外部解析器'
     else:
      description=markdown[engine]+('；文档必须由此接口直接返回文字，不使用外部 PDF/DOCX 解析器' if track=='document' else '')
    elif mode=='document':description=original[engine]+' → 原始 PDF/DOCX；只取得字节不能通过文档正文题，评测端不外部解析'
    elif mode=='file':description=original[engine]+' → 原始文件字节（不转 Markdown）'
    elif mode=='resource':description=resource[engine]+(
      ' → 图片或视频资源地址（直接从原生 Markdown 核对）' if engine=='moli'
      else ' → 图片或视频资源地址（不转 Markdown）')
    elif mode=='document_page':description=runtime[engine]+' 读取嵌入文档 → 已加载 PDF 查看器原生文字，或页面可见文字 → 文档正文；不下载后另行解析'
    elif mode=='media_metadata':description=runtime[engine]+' 执行共用脚本，读取正文、作者及 JSON-LD → 页面正文与媒体元数据（不转 Markdown）'
    else:raise ValueError('Unsupported browser task interface mode: '+mode)
    wait=('读取原始响应或下载文件，不等待查看器绘制' if mode in {'file','document'} else waits[engine])
    if engine=='moli' and track=='dynamic' and mode=='markdown':
     description+='；动态正文整类统一启用 --layout 和 --with-frames，不使用逐题 selector'
     wait='动态网站正文整类使用 CLI 原生 domstable'
    if mode not in {'file','document','markdown'}:
     if engine in {'lightpanda','kitesurf'} or (engine=='obscura' and mode in {'document_page','media_metadata'}):
      wait='CDP 页面提取等待 load'
    if mode=='document_page':wait+='；随后等待查看器文档就绪或页面内容'
    if records is not None:
     description=observed_description
     wait=observed_wait
    parts.append({'count':count,'mode':mode,'description':description,'wait':wait})
   if compact and records is not None:
    grouped={}
    for part in parts:
     description=part['description']
     if description.startswith('调用记录未提供'):
      description='接口未记录'
     elif 'node-html-markdown' in description:
      description='渲染 HTML → node-html-markdown → Markdown'
     elif '原生 Markdown' in description or '官方 Markdown 脚本' in description:
      description='官方脚本 → Markdown' if engine=='obscura' else '原生 Markdown'
     elif '原始文件字节' in description:
      description=description.split(' → ')[0]+' → 文件字节'
     elif description.startswith('调用记录未提供'):
      description='接口未记录'
     else:
      description=description.split('（route=')[0]
     mode='markdown' if 'Markdown' in description else part['mode']
     key=(mode,description)
     if key not in grouped:grouped[key]={'mode':mode,'count':0,'description':description,'wait':''}
     grouped[key]['count']+=part['count']
    parts=list(grouped.values())
   cells[provider]=parts
  rows.append({'track':track,'count':len(group),'cells':cells})
 selected_capabilities={name:capabilities.get(native_browser_engine(name),'未登记 / Not registered') for name in providers}
 if records is not None:
  for provider in providers:
   engine=native_browser_engine(provider)
   observed=[]
   for row in records:
    if row['provider']!=provider:continue
    description=_observed_route(engine,row.get('provider_metadata') or {})
    if description not in observed:observed.append(description)
   selected_capabilities[provider]=('本轮调用记录：'+'；'.join(observed)
     if observed else '本轮没有可核对的调用接口记录。')
 return {'providers':providers,'capabilities':selected_capabilities,'rows':rows}


def render_interfaces(out,records):
 from .artifacts import record_public_call_log_bindings, verify_public_call_log
 dataset_path=out/"dataset.json"
 dataset=json.loads(dataset_path.read_text()) if dataset_path.is_file() else {}
 comparison=dataset.get("comparison",{})
 native_default=measurement_presentation(dataset).native_outputs
 def pre(value):return '<pre>'+escape(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
 def fields(rows):return '<dl class="interface-fields">'+''.join('<div><dt><code>'+escape(k)+'</code> <small>'+escape(t)+'</small></dt><dd>'+escape(d)+'</dd></div>' for k,t,d in rows)+'</dl>'
 measured={r['provider'] for r in records}
 names=sorted(set(SPECS)|measured,key=lambda name:(name not in measured,name))
 body='<p>按本轮适配器介绍，不混用完整产品能力。输入示例展示正文配置，资源配置差异单列；它们不是每条历史请求的逐字回放。各调用所属批次及实际配置见“环境与复现”。输出示例取存档调用，经适配器整理后的真实节选，不冒充原始 HTTP 响应。Key 和账号均用占位符。</p>'
 if native_default:
  body='<p>已测方法的输入展示保存的公开调用字段，输出展示同一次调用的实际节选。完整配置、原始响应和日志见环境与调用详情。未参评方法保留仓库登记示例。</p>'
 for i,name in enumerate(names):
  r=next((r for r in records if r['provider']==name and r['case_id']=='static-simple'),next((r for r in records if r['provider']==name),None))
  s=method_spec(name,r,timeout_seconds=comparison.get("timeout_seconds"))
  bindings=record_public_call_log_bindings(r) if r else []
  if r and native_default and not bindings:
   raise ValueError("Measured native interface requires a saved call log: "+name)
  if bindings:
   request=json.loads(verify_public_call_log(out,bindings[-1]).read_text())["request"]
   from .methods import method_page_model
   model=method_page_model(name,name,[x for x in records if x["provider"]==name])
   s={**s,"entry":model["endpoint"],"params":[],"request":request,"notes":"此处展示该次公开调用记录保存的请求字段；完整配置和日志见环境与调用详情。产品内部超时以冻结配置和实际请求为准。"}
  access=method_access(name,records)
  if r:
   content=contained_path(out,r['artifact_path']).read_text();sample={k:r.get(k) for k in ['ok','status_code','title','final_url']};sample['content_excerpt']=content[:450];sample['content_characters']=len(content)
  else:sample={'measurement':None,'note':'仓库登记接口，未纳入本轮；此处不提供虚构的运行输出。'}
  # Explicit whitelist: no local paths, request IDs, credentials or session metadata.
  meta=r.get('provider_metadata',{}) if r else {};sample['metadata']={k:meta[k] for k in ['engine','upstream_status','links_count','images_count','extractor','truncated','document_count'] if k in meta}
  dest=out/'adoption'/'interfaces';dest.mkdir(exist_ok=True);(dest/(name+'.json')).write_text(json.dumps({'interface':s,'access':access,'sample':sample,'attempt_id':r['attempt_id'] if r else None},ensure_ascii=False,indent=2)+'\n')
  body+=f'<details class="interface-panel" id="interface-{name}"'+(' open' if i==0 else '')+'><summary>'+escape(browser_method_label(name))+'</summary>'
  body+='<p class="interface-endpoint"><code>'+escape(s['entry'])+'</code></p><p class="source-note">'+escape(s['auth'])+'</p>'
  if access:
   body+='<h3>账号与获取入口</h3><p>'+escape(access['account'])+'</p><p>'+escape(access['configuration'])+'</p><p>'+ ' · '.join('<a href="'+escape(url,quote=True)+'">'+escape(label)+'</a>' for label,url in access['links'])+'</p><p class="source-note">实际版本和接口见运行记录；下方链接指向官方说明。</p>'
  if access and access.get('env_vars'):
   body+='<p>'+', '.join('<code>'+escape(value)+'</code>' for value in access['env_vars'])+'</p>'
  sample_label='本轮输出节选 · 统一记录' if r else '本轮未执行'
  body+='<div class="guide-grid"><article class="guide-card"><h3>输入</h3>'+fields(s['params'])+('<h4>本轮保存的调用字段</h4>' if bindings else '<h4>调用输入示例</h4>')+pre(s['request'])+'</article><article class="guide-card"><h3>输出</h3>'+fields(s['fields'])+'<h4>'+sample_label+'</h4>'+pre(sample)+'</article></div>'
  measured_link='<a href="../attempts/'+escape(r['attempt_id'])+'.html">该次完整输出与判定 →</a>' if r else '未纳入本轮；参数说明为仓库登记模式，不计入本轮结果'
  from .report_components import bilingual_text
  example_note='' if bindings else bilingual_text('以下接口说明与数值属于仓库登记示例，不是这次调用的请求记录。', 'The following interface descriptions and values are registered examples, not this call’s request record.')
  body+='<p>'+example_note+escape(s['notes'])+'</p><p class="source-note">核对实现：<code>'+escape(s['source'])+'</code> · '+measured_link+' · <a href="interfaces/'+name+'.json">示例 JSON</a></p></details>'
 return body
