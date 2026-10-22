"""Interfaces actually used by the benchmark, not a claim to cover entire SDKs."""
import json
import re
from types import SimpleNamespace
from html import escape
from harness.numbers import positive_number
from lexbench_webfetch.paths import contained_path
from .methods import native_browser_engine, browser_method_label
from .report_components import measurement_presentation, bilingual_text
from .providers.headless import output_mode_for_case

from .methods import report_method, report_method_catalog, method_access, metadata_matches, method_page_model, browser_method_registry

# Views of the shared catalog; execution aliases inherit their engine's metadata.
SPECS = {name: item['interface'] for name, item in report_method_catalog()['methods'].items()
         if item.get('interface')}

def method_provenance(out):
 path=out/'implementation-provenance.json'
 return {item['binary_sha256']:item for item in json.loads(path.read_text()).get('implementations',[])} if path.is_file() else {}


def method_spec(name, record, *, timeout_seconds=None, language="zh"):
 bound=browser_method_registry().get(name,{}).get('report_interface')
 if bound is not None:
  return bound[language] if set(bound)=={'zh','en'} else bound
 def t(zh,en):return zh if language=="zh" else en
 catalog=report_method_catalog() if language=='zh' else report_method_catalog(language)
 owner=catalog['adapters'].get((record or {}).get('provider_type'),{}).get('interface_method')
 registered=catalog['methods'][owner] if owner else report_method(name,record,language=language)
 base=registered.get('interface') or {
  'entry':t('未登记','Not registered'),'auth':t('未登记','Not registered'),'params':[],
  'fields':[],'request':{},'source':t('未登记','Not registered'),
  'notes':t('接口资料尚未登记；请查阅保存的调用证据。','Interface metadata has not been registered; consult saved call evidence.')}
 meta=(record or {}).get('provider_metadata',{})
 if registered.get('runtime_parameters'):
  budget=(t(f'{timeout_seconds:g} 秒外层截止',f'{timeout_seconds:g}-second outer deadline')
          if positive_number(timeout_seconds)
          else t('外层截止时间未记录','Outer deadline not recorded'))
  context={**meta,'report_outer_budget':budget}
  params=list(base['params']);notes=base['notes'];request=dict(base['request'])
  for parameter in registered['runtime_parameters']:
   rule=next((rule for rule in parameter['rules'] if metadata_matches(context,rule.get('metadata_equals',{}))),None)
   if rule is None:continue
   description=_route_template(rule['description'],context)
   if description is None:continue
   params=[(key,kind,(detail+description if parameter.get('append') else description)
            if key==parameter['parameter'] else detail) for key,kind,detail in params]
   for key,template in rule.get('request_fields',{}).items():
    value=_route_template(template,context)
    if value is not None:request[key]=value
   if rule.get('note'):notes+=' '+rule['note']
  entry=base['entry'] if name in catalog['methods'] else registered.get('unregistered_participant_entry',base['entry'])
  base={**base,'entry':entry,'params':params,'notes':notes,'request':request}
 for variant in registered.get('interface_variants',[]):
  if not metadata_matches(meta,variant['metadata_equals']):continue
  native={k:v for k,v in meta.get('native_metadata',{}).items() if k in {'format','script_sha256'}}
  command=variant['command']
  request={**base['request'],'command':command,'native_metadata':native}
  for key in variant.get('remove_request_fields',[]):request.pop(key,None)
  return {**base,'entry':'CLI · '+command,'request':request,
          'params':[row for row in base['params'] if row[0] not in variant.get('remove_parameters',[])],
          'notes':variant['notes']}
 return base



def _observed_wait(meta, *, language="zh"):
 navigation=meta.get('navigation_wait_until')
 completion=meta.get('wait_until')
 if language=='en':
  if navigation and completion and navigation!=completion:
   return f'Navigation wait {navigation}; output completion {completion}'
  if navigation:return f'Navigation wait {navigation}'
  if completion:return f'Output completion {completion}'
  return 'Call records do not provide a verifiable wait condition'
 if navigation and completion and navigation!=completion:
  return f'导航等待 {navigation}；输出完成条件 {completion}'
 if navigation:return f'导航等待 {navigation}'
 if completion:return f'输出完成条件 {completion}'
 return '调用记录未提供可核对的等待条件'


def _route_template(rule, metadata):
 """Render a registered route only when all its evidence fields are present."""
 if not metadata_matches(metadata,rule.get('metadata_equals',{})):return None
 values={}
 for name,path in rule.get('fields',{}).items():
  value=metadata
  for key in path.split('.'):
   value=value.get(key) if isinstance(value,dict) else None
  if value is None or value=='':return None
  if name in rule.get('field_values',{}) and value not in rule['field_values'][name]:return None
  if name in rule.get('field_patterns',{}) and (not isinstance(value,str) or not re.fullmatch(rule['field_patterns'][name],value)):return None
  if name in rule.get('positive_numeric_fields',[]):
   if not positive_number(value):return None
  values[name]=value
 for name,fragments in rule.get('fragments',{}).items():
  values[name]=next((text for fragment in fragments
                    if (text:=_route_template(fragment,metadata)) is not None),'')
 return rule['text'].format(**values)


def _observed_route(engine, meta, *, language="zh", compact=False):
 """Describe only the registered interface persisted by one measured call."""
 def t(zh,en):return zh if language=='zh' else en
 route=meta.get('route')
 extractor=meta.get('extractor')
 contract=meta.get('output_contract')
 route_note=t(f'（route={route}）',f' (route={route})') if route and not compact else ''
 for rule in report_method(engine,language=language).get('observed_routes',[]):
  description=_route_template(rule,meta)
  if description is not None:
   return rule.get('compact_text',description) if compact else description+route_note
 if contract=='original_file_bytes':
  source=(t('调用记录 extractor=','Recorded extractor=')+str(extractor) if extractor
          else t('调用记录未注明原始文件接口','Original-file interface not recorded'))
  return source+(t(' → 文件字节',' → file bytes') if compact else t(' → 原始文件字节',' → original file bytes'))+route_note
 fields=[]
 for key in ('route','extractor','output_contract','dump'):
  if meta.get(key):fields.append(f'{key}={meta[key]}')
 if fields:
  return t('调用记录：','Call record: ')+t('；','; ').join(fields)+t('；未据此补写未记录的转换步骤','; unrecorded conversion steps are not inferred')
 if compact:return t('接口未记录','Interface not recorded')
 return t('调用记录未提供可核对的接口路线；不推断原生 Markdown、HTML 或额外转换','Call records do not provide a verifiable route; native Markdown, HTML or additional conversion is not inferred')


def browser_task_interface_matrix(cases, providers, records=None, *, compact=False, language="zh"):
 """Describe task routes, preferring persisted call metadata when available."""
 from collections import Counter
 def t(zh,en):return zh if language=='zh' else en
 methods=(report_method_catalog() if language=='zh' else report_method_catalog(language))['methods']
 selected={name:next((row for row in (records or []) if row['provider']==name),None)
           for name in providers}
 engines={name:native_browser_engine(name,selected[name]) for name in providers}
 routes={name:item.get('routes',{}) for name,item in methods.items()}
 capabilities={name:item.get('capabilities','Not registered') for name,item in routes.items()}
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
   engine=engines[provider]
   if engine not in methods:
    cells[provider]=[{'mode':'unregistered','count':len(group),'description':t('未登记','Not registered'),'wait':'Not recorded'}]
    continue
   parts=[];counts=Counter()
   for case in group:
    case_id=case.get('id',case_ids[id(case)])
    case_metadata={**case,'track':case.get('track','web'),'task_kind':case.get('task_kind','body')}
    plan=next((rule for rule in methods[engine].get('task_interfaces',[])
               if metadata_matches(case_metadata,rule['case_equals'])),None)
    measured=indexed.get((provider,case_id))
    provider_type=(measured or selected[provider] or {}).get('provider_type') or browser_method_registry().get(provider,{}).get('type','headless_markdown')
    selector_type=report_method_catalog()['adapters'].get(provider_type,{}).get('task_mode_provider_type',provider_type)
    mode=output_mode_for_case(engine, selector_type, SimpleNamespace(
     **{**case_metadata, 'url': case.get('url', '')}))
    if records is not None:
     meta=(measured or {}).get('provider_metadata') or {}
     if compact:
      observed=next((rule for rule in methods[engine].get('observed_routes',[])
                     if _route_template(rule,meta) is not None),None)
      mode=observed['mode'] if observed else 'unregistered'
     description=_observed_route(engine,meta,language=language,compact=compact)
     wait=_observed_wait(meta,language=language)
    elif plan:
     description=plan['description'].format(**routes.get(engine,{}))
     wait=plan['wait'].format(**routes.get(engine,{}))
    else:
     description=t('未登记','Not registered')
     wait='Not recorded'
    counts[(mode,description,wait)]+=1
   for (mode,description,wait),count in counts.items():
    parts.append({'count':count,'mode':mode,'description':description,'wait':wait})
   if compact and records is not None:
    grouped={}
    for part in parts:
     description=part['description']
     mode=part['mode']
     key=(mode,description)
     if key not in grouped:grouped[key]={'mode':mode,'count':0,'description':description,'wait':''}
     grouped[key]['count']+=part['count']
    parts=list(grouped.values())
   cells[provider]=parts
  rows.append({'track':track,'count':len(group),'cells':cells})
 selected_capabilities={name:capabilities.get(engines[name],t('未登记','Not registered')) for name in providers}
 if records is not None:
  for provider in providers:
   engine=engines[provider]
   observed=[]
   for row in records:
    if row['provider']!=provider:continue
    description=_observed_route(engine,row.get('provider_metadata') or {},language=language)
    if description not in observed:observed.append(description)
   selected_capabilities[provider]=(t('本轮调用记录：','Recorded calls: ')+t('；','; ').join(observed)
     if observed else t('本轮没有可核对的调用接口记录。','No verifiable call interfaces were recorded.'))
 return {'providers':providers,'capabilities':selected_capabilities,'rows':rows}


def render_interfaces(out,records):
 from .artifacts import record_public_call_log_bindings, verify_public_call_log
 dataset_path=out/"dataset.json"
 dataset=json.loads(dataset_path.read_text()) if dataset_path.is_file() else {}
 comparison=dataset.get("comparison",{})
 native_default=measurement_presentation(dataset).native_outputs
 def pre(value):return '<pre>'+escape(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
 def fields(rows,english):
  if len(rows)!=len(english):raise ValueError('Interface field language mismatch')
  return '<dl class="interface-fields">'+''.join('<div><dt><code>'+bilingual_text(k,ek)+'</code> <small>'+bilingual_text(t,et)+'</small></dt><dd>'+bilingual_text(d,ed)+'</dd></div>' for (k,t,d),(ek,et,ed) in zip(rows,english))+'</dl>'

 measured={r['provider'] for r in records}
 names=sorted(set(SPECS)|measured,key=lambda name:(name not in measured,name))
 body='<p>按本轮适配器介绍，不混用完整产品能力。输入示例展示正文配置，资源配置差异单列；它们不是每条历史请求的逐字回放。各调用所属批次及实际配置见“环境与复现”。输出示例取存档调用，经适配器整理后的真实节选，不冒充原始 HTTP 响应。Key 和账号均用占位符。</p>'
 if native_default:
  body='<p>已测方法的输入展示保存的公开调用字段，输出展示同一次调用的实际节选。完整配置、原始响应和日志见环境与调用详情。未参评方法保留仓库登记示例。</p>'
 case_order={case['id']: index for index, case in enumerate(dataset.get('cases', []))}
 for i,name in enumerate(names):
  r=min((r for r in records if r['provider']==name), key=lambda row: case_order.get(row['case_id'], len(case_order)), default=None)
  s=method_spec(name,r,timeout_seconds=comparison.get("timeout_seconds"))
  english=method_spec(name,r,timeout_seconds=comparison.get("timeout_seconds"),language="en")
  bindings=record_public_call_log_bindings(r) if r else []
  if r and native_default and not bindings:
   raise ValueError("Measured native interface requires a saved call log: "+name)
  if bindings:
   request=json.loads(verify_public_call_log(out,bindings[-1]).read_text())["request"]
   model=method_page_model(name,name,[x for x in records if x["provider"]==name])
   s={**s,"entry":model["endpoint"],"params":[],"request":request,"notes":"此处展示该次公开调用记录保存的请求字段；完整配置和日志见环境与调用详情。产品内部超时以冻结配置和实际请求为准。"}
   english_model=method_page_model(name,name,[x for x in records if x["provider"]==name],language="en")
   english={**english,"entry":english_model["endpoint"],"params":[],"request":request,"notes":"These are the request fields saved in this public call record. Full configuration and logs are in the environment and call details; internal product timeouts follow the frozen configuration and actual request."}
  access=method_access(name,records)
  english_access=method_access(name,records,language="en")
  if r:
   content=contained_path(out,r['artifact_path']).read_text();sample={k:r.get(k) for k in ['ok','status_code','title','final_url']};sample['content_excerpt']=content[:450];sample['content_characters']=len(content)
  else:sample={'measurement':None,'note':'仓库登记接口，未纳入本轮；此处不提供虚构的运行输出。'}
  # Explicit whitelist: no local paths, request IDs, credentials or session metadata.
  meta=r.get('provider_metadata',{}) if r else {};sample['metadata']={k:meta[k] for k in ['engine','upstream_status','links_count','images_count','extractor','truncated','document_count'] if k in meta}
  dest=out/'adoption'/'interfaces';dest.mkdir(exist_ok=True);(dest/(name+'.json')).write_text(json.dumps({'interface':s,'access':access,'sample':sample,'attempt_id':r['attempt_id'] if r else None},ensure_ascii=False,indent=2)+'\n')
  body+=f'<details class="interface-panel" id="interface-{name}"'+(' open' if i==0 else '')+'><summary>'+bilingual_text(browser_method_label(name),browser_method_label(name,language='en'))+'</summary>'
  body+='<p class="interface-endpoint"><code>'+bilingual_text(s['entry'],english['entry'])+'</code></p><p class="source-note">'+bilingual_text(s['auth'],english['auth'])+'</p>'
  if access:
   body+='<h3>账号与获取入口</h3><p>'+bilingual_text(access['account'],english_access['account'])+'</p><p>'+bilingual_text(access['configuration'],english_access['configuration'])+'</p><p>'+ ' · '.join('<a href="'+escape(url,quote=True)+'">'+bilingual_text(label,english_access['links'][index][0])+'</a>' for index,(label,url) in enumerate(access['links']))+'</p><p class="source-note">实际版本和接口见运行记录；下方链接指向官方说明。</p>'
  if access and access.get('env_vars'):
   body+='<p>'+', '.join('<code>'+escape(value)+'</code>' for value in access['env_vars'])+'</p>'
  sample_label='本轮输出节选 · 统一记录' if r else '本轮未执行'
  body+='<div class="guide-grid"><article class="guide-card"><h3>输入</h3>'+fields(s['params'],english['params'])+('<h4>本轮保存的调用字段</h4>' if bindings else '<h4>调用输入示例</h4>')+pre(s['request'])+'</article><article class="guide-card"><h3>输出</h3>'+fields(s['fields'],english['fields'])+'<h4>'+sample_label+'</h4>'+pre(sample)+'</article></div>'
  measured_link='<a href="../attempts/'+escape(r['attempt_id'])+'.html">该次完整输出与判定 →</a>' if r else '未纳入本轮；参数说明为仓库登记模式，不计入本轮结果'
  example_note='' if bindings else bilingual_text('以下接口说明与数值属于仓库登记示例，不是这次调用的请求记录。', 'The following interface descriptions and values are registered examples, not this call’s request record.')
  body+='<p>'+example_note+bilingual_text(s['notes'],english['notes'])+'</p><p class="source-note">核对实现：<code>'+escape(s['source'])+'</code> · '+measured_link+' · <a href="interfaces/'+name+'.json">示例 JSON</a></p></details>'
 return body
