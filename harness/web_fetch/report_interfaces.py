"""Interfaces actually used by the benchmark, not a claim to cover entire SDKs."""

import json
import re
from collections import Counter
from html import escape
from types import SimpleNamespace

from lexbench_webfetch.paths import contained_path

from harness.numbers import positive_number
from harness.web_fetch import artifacts as evidence_artifacts
from harness.web_fetch.methods import (
 browser_method_label,
 browser_method_registry,
 metadata_matches,
 method_access,
 method_page_model,
 native_browser_engine,
 report_method,
 report_method_catalog,
)
from harness.web_fetch.models import REPORT_FAMILIES, report_family
from harness.web_fetch.providers.headless import output_mode_for_case
from harness.web_fetch.report_components import bilingual_text, measurement_presentation

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
 catalog=report_method_catalog() if language=='zh' else report_method_catalog(language)
 owner=catalog['adapters'].get((record or {}).get('provider_type'),{}).get('interface_method')
 registered=catalog['methods'][owner] if owner else report_method(name,record,language=language)
 messages=catalog['messages']
 base=registered.get('interface') or catalog['unregistered_interface']
 meta=(record or {}).get('provider_metadata',{})
 if registered.get('runtime_parameters'):
  budget=(messages['outer_deadline'].format(timeout=f'{timeout_seconds:g}')
          if positive_number(timeout_seconds)
          else messages['outer_deadline_unrecorded'])
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
 messages=report_method_catalog(language)['messages']
 if navigation and completion and navigation!=completion:
  return messages['wait_both'].format(navigation=navigation,completion=completion)
 if navigation:return messages['wait_navigation'].format(navigation=navigation)
 if completion:return messages['wait_completion'].format(completion=completion)
 return messages['wait_unrecorded']


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
 messages=report_method_catalog(language)['messages']
 route=meta.get('route')
 extractor=meta.get('extractor')
 contract=meta.get('output_contract')
 route_note=messages['route_note'].format(route=route) if route and not compact else ''
 for rule in report_method(engine,language=language).get('observed_routes',[]):
  description=_route_template(rule,meta)
  if description is not None:
   return rule.get('compact_text',description) if compact else description+route_note
 if contract=='original_file_bytes':
  source=(messages['extractor_prefix']+str(extractor) if extractor
          else messages['original_file_unrecorded'])
  return source+(messages['file_bytes'] if compact else messages['original_file_bytes'])+route_note
 fields=[]
 for key in ('route','extractor','output_contract','dump'):
  if meta.get(key):fields.append(f'{key}={meta[key]}')
 if fields:
  return messages['call_prefix']+messages['separator'].join(fields)+messages['conversion_unrecorded']
 if compact:return messages['interface_unrecorded']
 return messages['route_unrecorded']


def browser_task_interface_matrix(cases, providers, records=None, *, compact=False, language="zh"):
 """Describe task routes, preferring persisted call metadata when available."""
 catalog=report_method_catalog(language)
 messages=catalog['messages']
 methods=catalog['methods']
 selected={name:next((row for row in (records or []) if row['provider']==name),None)
           for name in providers}
 engines={name:native_browser_engine(name,selected[name]) for name in providers}
 routes={name:item.get('routes',{}) for name,item in methods.items()}
 capabilities={name:item.get('capabilities',messages['not_registered']) for name,item in routes.items()}
 tracks=[]
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
    cells[provider]=[{'mode':'unregistered','count':len(group),'description':messages['not_registered'],'wait':messages['not_recorded']}]
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
     description=messages['not_registered']
     wait=messages['not_recorded']
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
 selected_capabilities={name:capabilities.get(engines[name],messages['not_registered']) for name in providers}
 if records is not None:
  for provider in providers:
   engine=engines[provider]
   observed=[]
   for row in records:
    if row['provider']!=provider:continue
    description=_observed_route(engine,row.get('provider_metadata') or {},language=language)
    if description not in observed:observed.append(description)
   selected_capabilities[provider]=(messages['calls_prefix']+messages['separator'].join(observed)
     if observed else messages['calls_unrecorded'])
 return {'providers':providers,'capabilities':selected_capabilities,'rows':rows}


def render_interfaces(out,records):
 messages=report_method_catalog()['messages']
 english_messages=report_method_catalog('en')['messages']
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
 body=messages['interface_intro_html']
 if native_default:
  body=messages['interface_measured_intro_html']
 case_order={case['id']: index for index, case in enumerate(dataset.get('cases', []))}
 for i,name in enumerate(names):
  r=min((r for r in records if r['provider']==name), key=lambda row: case_order.get(row['case_id'], len(case_order)), default=None)
  s=method_spec(name,r,timeout_seconds=comparison.get("timeout_seconds"))
  english=method_spec(name,r,timeout_seconds=comparison.get("timeout_seconds"),language="en")
  bindings=evidence_artifacts.record_public_call_log_bindings(r) if r else []
  if r and native_default and not bindings:
   raise ValueError("Measured native interface requires a saved call log: "+name)
  if bindings:
   request=json.loads(evidence_artifacts.verify_public_call_log(out,bindings[-1]).read_text())["request"]
   model=method_page_model(name,name,[x for x in records if x["provider"]==name])
   s={**s,"entry":model["endpoint"],"params":[],"request":request,"notes":messages['interface_request_note']}
   english_model=method_page_model(name,name,[x for x in records if x["provider"]==name],language="en")
   english={**english,"entry":english_model["endpoint"],"params":[],"request":request,"notes":english_messages['interface_request_note']}
  access=method_access(name,records)
  english_access=method_access(name,records,language="en")
  if r:
   content=contained_path(out,r['artifact_path']).read_text();sample={k:r.get(k) for k in ['ok','status_code','title','final_url']};sample['content_excerpt']=content[:450];sample['content_characters']=len(content)
  else:sample={'measurement':None,'note':messages['interface_unmeasured_sample']}
  # Explicit whitelist: no local paths, request IDs, credentials or session metadata.
  meta=r.get('provider_metadata',{}) if r else {};sample['metadata']={k:meta[k] for k in ['engine','upstream_status','links_count','images_count','extractor','truncated','document_count'] if k in meta}
  dest=out/'adoption'/'interfaces';dest.mkdir(exist_ok=True);(dest/(name+'.json')).write_text(json.dumps({'interface':s,'access':access,'sample':sample,'attempt_id':r['attempt_id'] if r else None},ensure_ascii=False,indent=2)+'\n')
  body+=f'<details class="interface-panel" id="interface-{name}"'+(' open' if i==0 else '')+'><summary>'+bilingual_text(browser_method_label(name),browser_method_label(name,language='en'))+'</summary>'
  body+='<p class="interface-endpoint"><code>'+bilingual_text(s['entry'],english['entry'])+'</code></p><p class="source-note">'+bilingual_text(s['auth'],english['auth'])+'</p>'
  if access:
   body+=messages['interface_accounts_html']+bilingual_text(access['account'],english_access['account'])+'</p><p>'+bilingual_text(access['configuration'],english_access['configuration'])+'</p><p>'+ ' · '.join('<a href="'+escape(url,quote=True)+'">'+bilingual_text(label,english_access['links'][index][0])+'</a>' for index,(label,url) in enumerate(access['links']))+messages['interface_source_note_html']
  if access and access.get('env_vars'):
   body+='<p>'+', '.join('<code>'+escape(value)+'</code>' for value in access['env_vars'])+'</p>'
  sample_label=messages['interface_sample_label'] if r else messages['interface_not_run_label']
  body+=messages['interface_input_card_html']+fields(s['params'],english['params'])+(messages['interface_saved_fields_html'] if bindings else messages['interface_input_example_html'])+pre(s['request'])+messages['interface_output_card_html']+fields(s['fields'],english['fields'])+'<h4>'+sample_label+'</h4>'+pre(sample)+'</article></div>'
  measured_link='<a href="../attempts/'+escape(r['attempt_id'])+messages['interface_full_result_link_html'] if r else messages['interface_unmeasured_note']
  example_note='' if bindings else bilingual_text(messages['interface_example_note'], english_messages['interface_example_note'])
  body+='<p>'+example_note+bilingual_text(s['notes'],english['notes'])+messages['interface_verify_html']+escape(s['source'])+'</code> · '+measured_link+' · <a href="interfaces/'+name+messages['interface_example_json_link_html']
 return body
