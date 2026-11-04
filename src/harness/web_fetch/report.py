"""Evidence-first report: measured tracks, canonical cases, no review workflow."""
from __future__ import annotations


from harness.distribution import repository_root

import hashlib
import html
import json
import re
import shutil
import statistics
from collections import Counter, defaultdict
from collections import Counter as WordCounter
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from math import ceil
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup
from lexbench_webfetch.body_coverage import (
 _compact_with_positions,
 _global_region_allocation,
 compact_ngrams,
 evaluate_body_requirements,
 native_markup_body_text,
 tokens,
)
from lexbench_webfetch.dynamic_lists import visible_html_text
from lexbench_webfetch.metrics import normalize_for_match, percentile, task_measured, task_passed
from lexbench_webfetch.models import TRACKS
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.reference_quality import is_html_document_or_fragment
from lexbench_webfetch.response_input import answer_input
from lexbench_webfetch.stable_tasks import SCHEMA_FIELDS, _parse_records, _visible

from harness.numbers import nonnegative_number
from harness.record_io import load_jsonl
from harness.report_languages import (
 check_language_contract,
 english_notice,
 generated_languages,
 primary_report_language,
 select_report_language,
 write_language_contract,
)
from harness.reporting.mermaid import mermaid_script_attributes
from harness.reporting.presentation import esc
from harness.web_fetch.adjudication_evidence import adjudication_reason
from harness.web_fetch.failure_diagnosis import diagnose
from harness.web_fetch.methods import (
 ROUTE_LABELS,
 browser_method_label,
 browser_method_registry,
 is_browser_comparison,
 method_access,
 method_page_model,
 method_version,
 method_versions,
 native_browser_engine,
 report_method,
 report_method_catalog,
 versioned_method_label,
 with_measured_method_labels,
)
from harness.web_fetch.models import REPORT_FAMILIES, execution_id, report_family
from harness.web_fetch.providers.headless import engine_definition
from harness.web_fetch.publication_privacy import (
 project_evidence_view,
 public_render_views,
 read_evidence_binding,
 require_public_method_options,
)
from harness.web_fetch.reliability import assess_reliability
from harness.web_fetch.report_changes import (
 ROOT,
 localized_release_history,
 render_changes,
 render_localized_release_history,
 render_webmainbench_history,
 require_review_rounds,
)
from harness.web_fetch.report_components import (
 basis_label,
 comparable_body_reference,
 examples,
 list_rule_description,
 measurement_presentation,
 metric_guide,
 metric_legend,
 reference_label,
 reference_status,
 reference_text,
 render_acceptance_scope,
 sources_panel,
 stable_contract_details,
 tabs,
 task_contract_guide,
 task_instruction,
 task_pass_guide,
)
from harness.web_fetch.report_diagnostic import (
 FAILURE_TYPES,
 diagnostic_call_summary,
 diagnostic_cohort_summary,
 diagnostic_failure_counts,
 diagnostic_findings,
 diagnostic_specimen_excerpt,
)
from harness.web_fetch.report_explainer import (
 _article_speed_population,
 load_browser_failure_review,
 render_explainer,
)
from harness.web_fetch.report_i18n import pair, report_text, t, write_english_report
from harness.web_fetch.report_interfaces import _observed_route, method_provenance, method_spec
from harness.web_fetch.report_methods import PROFILES, adoption_counts, render_methods
from harness.web_fetch.report_quality import render_quality_observations
from harness.web_fetch.report_reading import render_archived_reading
from harness.web_fetch.report_resources import render_resource_comparison
from harness.web_fetch.report_style import STYLE, asset_text
from harness.web_fetch.result_ledger import validate_ledger
from harness.web_fetch.safety import safe_href, validate_report_ids
from harness.web_fetch.wcxb_latency import load_full_paired_latency, summarize_full_paired_latency
from harness.web_fetch.wcxb_scoring import paired_latency_methods
from harness.web_fetch.webmainbench_diagnostics import ERROR_PAGE


def format_wcxb_conditional_latency(value: float | None) -> str:
 return f'{value:,.0f} ms' if value is not None else '—'


def generate_wcxb(out: Path, rows: list[dict], summary: dict, ground_truth: dict,
                  identities: dict, profile: dict, report_content: dict, *,
                  include_english: bool = False) -> None:
 """Render the current WCXB comparison from one certified semantic model."""
 languages=('zh','en') if include_english else ('zh',)
 if (report_content.get('schema_version') != 1
     or not isinstance(report_content.get('guide'),dict)
     or any(not isinstance(report_content['guide'].get(lang),str)
            or not report_content['guide'][lang].strip() for lang in languages)):
  raise ValueError('WCXB authored interpretation lacks requested languages')
 providers=list(summary['providers'])
 comparison=profile.get('paired_comparison') or {}
 baseline=comparison.get('baseline')
 candidate=comparison.get('candidate')
 if comparison and (baseline == candidate or {baseline,candidate} - set(providers)):
  raise ValueError('WCXB paired comparison must name two registered participants')
 audit=summary['reference_audit']
 case_count=summary['case_count']
 quality_count=summary['quality_case_count']
 result_count=case_count*len(providers)
 visible_count=quality_count*len(providers)
 review_scope=json.loads((out/'research/wcxb-representative-review.json').read_text(encoding='utf-8'))
 reviewed_cases=review_scope['case_count']
 reviewed_results=review_scope['result_count']
 unreviewed_results=result_count-reviewed_results
 exclusion_ids={row['file_id']:row['reason'] for row in summary.get('exclusions',[])}
 source_repository=profile['source_repository'].rstrip('/')
 source_commit=profile['source_commit']
 by_case=defaultdict(dict)
 for row in rows:by_case[row['case_id']][row['provider']]=row
 if len(by_case)!=case_count or any(set(group)!=set(providers) for group in by_case.values()):
  raise ValueError('WCXB same-case matrix is incomplete')
 paired_methods={}
 pair={}
 paired_latency=None
 if profile.get('latency_correction_receipt_sha256'):
  paired_methods=paired_latency_methods(profile,{"providers":[{"provider":p,"identity":identities[p]} for p in providers]})
  pair={role:provider for provider,role in paired_methods.items()}
  _, selected_latency = load_full_paired_latency(out/'research/wcxb-full-paired-latency', profile, set(by_case))
  paired_latency = summarize_full_paired_latency(selected_latency, set(by_case))

 adoption_data=json.loads((out/'research/provider-adoption.json').read_text(encoding='utf-8'))
 cloudflare_matches=[(entry['repo'], match['path']) for entry in adoption_data['included']
                     for match in entry['matches'] if match['signature']=='cloudflare-markdown']
 cloudflare_repo_count=len({repo for repo,_ in cloudflare_matches})
 cloudflare_file_count=len(set(cloudflare_matches))
 style=asset_text('wcxb.css')
 script=asset_text('wcxb.js').replace('/*mermaid-script-attributes*/',mermaid_script_attributes('loader'))
 L={
  'zh':{'home':'首页','results':t('analysis', 'Analysis'),'method':t('methodology', 'Methodology'),'guide':t('interpretation', 'Interpretation'),'repro':t('reproduction', 'Reproduction'),'theme':t('toggle_theme', 'Toggle theme'),'headline':'WCXB 主内容提取对比','core':'核心结果','provider':t('web_text', 'Web text'),'delivered':'生成非空内容','precision':'精确率','recall':'召回率','f1':'F1','latency':'平均端到端耗时','type':'页面类型','case':'网页','diagnosis':'定位','detail':'查看同页证据','all':t('all', 'All'),'issues':'待查问题','query':'搜索网页或错误','count':'条结果','reference':'人工主内容参考','output':'完整浏览器输出','error':'原始程序错误','missing':'参考中未匹配的高频词','extra':'输出中多出的高频词','summary':t('all_method_results_for_the_same_page', 'All method results for the same page'),'note':'低精确率通常表示输出夹带较多参考外文字；低召回率表示参考词语大量缺失。这是定位线索，不能仅凭词袋分数确定程序故障。','scope':t('counting_methodology', 'Counting methodology'),'limit':'边界与已知标注问题','source':'冻结来源','version':'实测版本或服务身份','no_review':f'典型异常审查覆盖 {reviewed_cases} 页、{reviewed_results} 条输出；其余 {unreviewed_results:,} 条尚未逐项人工审查。定位分类由确定性规则产生，不等同于根因。'},
  'en':{'home':'Conclusions','results':'Results analysis','method':'Method','guide':'Interpretation','repro':'Reproduce','theme':'Theme','headline':'WCXB main-content extraction comparison','core':'Core results','provider':'Browser','delivered':'Nonempty outputs','precision':'Precision','recall':'Recall','f1':'F1','latency':'Mean end-to-end latency','type':'Page type','case':'Page','diagnosis':'Locator','detail':'Same-page evidence','all':'All','issues':'Issues to inspect','query':'Search page or error','count':'results','reference':'Human main-content reference','output':'Complete browser output','error':'Exact program error','missing':'Frequent unmatched reference words','extra':'Frequent extra output words','summary':'All method results for this page','note':'Low precision often means output contains much text outside the reference; low recall means much reference text is missing. These are locators, not proof of a program defect.','scope':'Population and calculation','limit':'Limits and annotation issues','source':'Frozen source','version':'Measured version or service identity','no_review':f'Targeted anomaly review covers {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} results have not been individually reviewed. Deterministic locators are not root causes.'}}
 L['zh'].update({'dataset':t('dataset', 'Dataset'),'review':t('review_record', 'Review record'),'adoption':t('methods', 'Methods'),'changes':t('changelog', 'Changelog'),'accounts':t('access_setup', 'Access & setup')})
 L['en'].update({'dataset':'Dataset','review':'Verification','adoption':'Methods compared','changes':'Changes','accounts':'Accounts and setup'})
 L['zh']['headline']=t('wcxb_main_content_extraction_configurations', 'WCXB main-content extraction: {count} configurations', count=len(providers))
 L['en']['headline']=f'WCXB main-content extraction: {len(providers)} configurations'
 L['zh']['summary']=t('all_method_results_for_the_same_page', 'All method results for the same page')
 L['en']['summary']='All method results for this page'
 L['zh']['intro']=t('configurations_process_the_same_frozen_pages_each', '{count} configurations process the same {case_count} frozen pages; each is scored separately. F1 compares extracted words with the human main-content reference and does not measure Markdown structure or link usability.', count=len(providers), case_count=case_count)

 L['en']['intro']=f'{len(providers)} configurations processed the same {case_count} frozen pages; each configuration has separate results. F1 measures word overlap with human main content, not Markdown structure or link usability.'
 (out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(style,encoding='utf-8')
 (out/'assets/report.js').write_text(script,encoding='utf-8')
 def href(lang,path):return ('en/' if lang=='en' else '')+path
 def page(lang,path,title,body):
  x=L[lang]
  route=('en/' if lang=='en' else '')+path
  root_prefix='../'*route.count('/')
  nav=''.join(f'<a href="{href(lang,p)}">{esc(x[k])}</a>' for k,p in [('home','index.html'),('dataset','datasets/index.html'),('method','methodology/index.html'),('results','analysis/index.html'),('review','review/index.html'),('guide','guide/index.html'),('adoption','adoption/index.html'),('repro','reproduce/index.html'),('changes','changes/index.html'),('accounts','accounts/index.html')])
  other=('en/'+path if include_english else 'en/index.html') if lang=='zh' else path
  head=f'''<!doctype html><html lang="{lang}"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base href="{root_prefix or './'}"><title>{esc(title)} · WCXB</title><link rel="stylesheet" href="assets/style.css"><script defer src="assets/report.js"></script></head><body><header><div class="wrap"><strong><a href="{href(lang, 'index.html')}">Web Fetch</a></strong><nav>{nav}</nav><a href="{other}">{('English' if lang == 'zh' else '中文')}</a><button onclick="theme()">{esc(x['theme'])}</button></div></header><main class="wrap">{body}</main><footer class="wrap">WCXB v1.0 · {case_count} {report_text('zh' if lang == 'zh' else 'en')('evaluated_test_pages', 'evaluated test pages')} · CC BY 4.0 · <a href="{source_repository}/tree/{source_commit}">{esc(x['source'])}</a></footer></body></html>'''
  target=out/('en' if lang=='en' else '')/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(head,encoding='utf-8')
 def fmt(v,digits=1):return f'{v*100:.{digits}f}%'
 diag_labels={
  'zh':{'none':'未触发定位','request_error':t('request_failure', 'Request failure'),'empty_output':'输出为空','error_page':'错误页内容','empty_reference':'标准答案为空','excluded':'参考不合格','low_precision_and_recall':'精确率与召回率均低','low_precision':'精确率低','low_recall':'召回率低'},
  'en':{'none':'No locator triggered','request_error':'Request error','empty_output':'Empty output','error_page':'Error page','empty_reference':'Empty reference','excluded':'Reference excluded','low_precision_and_recall':'Low precision and recall','low_precision':'Low precision','low_recall':'Low recall'},
 }
 def diag(lang,value):return diag_labels[lang].get(value,value.replace('_',' '))
 type_labels={'article':'文章','service':'服务','product':'商品','collection':'合集','forum':'论坛','listing':'列表','documentation':t('documents', 'Documents')}
 def type_label(lang,value):return type_labels.get(value,value) if lang=='zh' else value
 access_records=[{'provider':p, 'provider_metadata':identities[p]} for p in providers]
 def browser_label(provider):
  if profile.get('participants',{}).get(provider,{}).get('label'):
   return profile['participants'][provider]['label']
  record={'provider':provider, 'provider_metadata':identities[provider]}
  registered=report_method(provider,record)
  base=registered.get('label',{}).get('en',identities[provider].get('engine') or provider)
  version=identities[provider].get('reported_version')
  label=versioned_method_label(registered,base,version) if version else base
  if provider == candidate and profile.get('candidate_source_commit'):
   label+=' · '+profile['candidate_source_commit'][:8]
  return label

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
 for lang in (('zh','en') if include_english else ('zh',)):
  x=L[lang]
  best_value=max(summary['providers'][p]['quality_f1'] for p in providers)
  fast_value=min(summary['providers'][p]['mean_latency_ms'] for p in providers)
  best=[p for p in providers if summary['providers'][p]['quality_f1']==best_value]
  fast=[p for p in providers if summary['providers'][p]['mean_latency_ms']==fast_value]
  metrics=(('quality_precision','max','precision'),('quality_recall','max','recall'),('quality_f1','max','f1'),('mean_latency_ms','min','mean'),('p50_latency_ms','min','p50'),('p95_latency_ms','min','p95'))
  winners={key:unique_winner({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in metrics}
  metric_digits={key:score_digits({p:summary['providers'][p][key] for p in providers},direction) for key,direction,_ in metrics if direction=='max'}
  metric_digits['quality_f1']=max(metric_digits['quality_f1'],4)

  core_rows=[]
  for p in sorted(providers,key=lambda name:summary['providers'][name]['quality_f1'],reverse=True):
   s=summary['providers'][p]
   cells=''.join(comparison_cell(s[key],label,direction,p==winners[key],fmt(s[key],metric_digits[key]) if direction=='max' else f'{s[key]:,.0f} ms') for key,direction,label in metrics)
   core_rows.append(f'<tr><td><a href="{href(lang,"providers/"+p+".html")}">{esc(browser_label(p))}</a></td><td>{s["delivered"]}/{case_count}</td>{cells}</tr>')
  core_headers=[x['provider'],x['delivered'],x['precision'],x['recall'],f'F1 ({quality_count})',(report_text('zh' if lang == 'zh' else 'en')('mean_latency', 'Mean latency')),(report_text('zh' if lang == 'zh' else 'en')('p50_latency', 'P50 latency')),(report_text('zh' if lang == 'zh' else 'en')('p95_latency', 'P95 latency'))]
  core_header_html=[]
  for index,label in enumerate(core_headers):
   sort_attr=' aria-sort="descending"' if index==4 else ''
   next_direction=' data-next-direction="asc"' if index==4 else ''
   core_header_html.append(f'<th{sort_attr}><button type="button" data-core-sort="{index}"{next_direction}>{esc(label)} ↕</button></th>' if index else f'<th>{esc(label)}</th>')
  core='<div class="scroll"><table id="wcxb-core"><thead><tr>'+''.join(core_header_html)+'</tr></thead><tbody>'+''.join(core_rows)+'</tbody></table></div>'
  moli_before=summary['providers'].get(baseline)
  moli_after=summary['providers'].get(candidate)
  if moli_before and moli_after:
   page_deltas=[by_case[c][candidate]['f1']-by_case[c][baseline]['f1'] for c in by_case if c not in exclusion_ids]
   up=sum(delta>1e-10 for delta in page_deltas);down=sum(delta< -1e-10 for delta in page_deltas);same=quality_count-up-down
   precision_delta=(moli_after['quality_precision']-moli_before['quality_precision'])*100
   recall_delta=(moli_after['quality_recall']-moli_before['quality_recall'])*100
   f1_delta=(moli_after['quality_f1']-moli_before['quality_f1'])*100
   example_case=max((c for c in by_case if c not in exclusion_ids), key=lambda c: abs(by_case[c][candidate]['f1']-by_case[c][baseline]['f1']))
   relation_counts={'higher':up,'lower':down,'equal':same}
   relation_labels=({'higher':'上升','lower':'下降','equal':'不变'} if lang=='zh' else {'higher':'higher','lower':'lower','equal':'unchanged'})
   change_counts=' · '.join(f'<span data-f1-relation="{relation}" data-count="{count}">{count} {relation_labels[relation]}</span>' for relation,count in relation_counts.items())
   paired=(report_text('zh' if lang == 'zh' else 'en')('section_id_configuration_change_class_panel_data', '<section id="configuration-change" class="panel" data-baseline="{baseline}" data-candidate="{candidate}"><h2>Page-level changes between configurations</h2><p>F1 across {quality_count} pages: {change_counts}. Mean recall changes {recall_delta:+.4f} percentage points, mean precision {precision_delta:+.4f}, and mean F1 {f1_delta:+.4f}. F1 is the mean of page-level F1 values, not a recomputation from mean precision and recall.</p><p><a href="{href}">Inspect same-page evidence</a> · <a href="{href2}">Read the page-level analysis</a></p></section>', baseline=esc(baseline), candidate=esc(candidate), quality_count=quality_count, change_counts=change_counts, recall_delta=recall_delta, precision_delta=precision_delta, f1_delta=f1_delta, href=href(lang, 'cases/' + example_case + '.html'), href2=href(lang, 'guide/index.html')))
  else:paired=''
  before=summary['providers'].get(pair.get('release'))
  after=summary['providers'].get(pair.get('pr'))
  latency_context=(report_text('zh' if lang == 'zh' else 'en')('p_id_paired_latency_context_and_were', '<p id="paired-latency-context">{browser_label} and {browser_label2} were measured in a balanced same-window crossover on all {case_count} pages. The preselected final round gives {mean_latency_ms:,.0f} ms for the release and {mean_latency_ms2:,.0f} ms for the PR, a {paired_delta_mean_ms:+.1f} ms page-paired difference. Timing differences describe this measured comparison; timing alone does not establish their cause. <a href="{href}">Read the timing basis</a></p>', browser_label=esc(browser_label(pair['release'])), browser_label2=esc(browser_label(pair['pr'])), case_count=case_count, mean_latency_ms=before['mean_latency_ms'], mean_latency_ms2=after['mean_latency_ms'], paired_delta_mean_ms=paired_latency['paired_delta_mean_ms'], href=href(lang, 'guide/index.html'))) if before and after and paired_latency else ''
  best_labels=('、' if lang=='zh' else ', ').join(browser_label(p) for p in best)
  fast_labels=('、' if lang=='zh' else ', ').join(browser_label(p) for p in fast)
  lead=(f"{best_labels} 在 {quality_count} 页有效参考上的平均 F1 {('并列最高' if len(best) > 1 else '最高')}（{fmt(best_value)}）；{fast_labels} 的 {case_count} 次调用平均端到端耗时{('并列最短' if len(fast) > 1 else '最短')}（{fast_value:,.0f} ms）。" if lang=='zh' else f'{best_labels} {"tie for the highest" if len(best)>1 else "has the highest"} mean F1 across {quality_count} qualified pages ({fmt(best_value)}); {fast_labels} {"tie for the shortest" if len(fast)>1 else "has the shortest"} mean end-to-end latency across {case_count} calls ({fast_value:,.0f} ms).')

  type_counts=WordCounter(gt['page_type'] for c,gt in ground_truth.items() if c not in exclusion_ids)
  slice_values={t:{p:sum(by_case[c][p]['f1'] for c,g in ground_truth.items() if g['page_type']==t and c not in exclusion_ids)/type_counts[t] for p in providers} for t in type_counts}
  slices=''.join(f'<tr data-page-type="{esc(t)}"><td>{esc(type_label(lang,t))}</td><td>{type_counts[t]}</td>'+''.join(comparison_cell(slice_values[t][p],f'type-{t}','max',p==unique_winner(slice_values[t],'max'),fmt(slice_values[t][p],score_digits(slice_values[t]))) for p in providers)+'</tr>' for t in sorted(type_counts))
  slice_table='<div class="filters"><label>'+esc(x['type'])+'<select id="home-type"><option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in sorted(type_counts))+'</select></label></div><div class="scroll"><table id="wcxb-types"><thead><tr><th>'+esc(x['type'])+'</th><th>'+(report_text('zh' if lang == 'zh' else 'en')('pages', 'Pages'))+'</th>'+''.join(f'<th>{esc(browser_label(p))} F1</th>' for p in providers)+'</tr></thead><tbody>'+slices+'</tbody></table></div>'
  home=f'<div class="hero"><span class="eyebrow">Web Fetch · WCXB v1.0</span><h1>{esc(x["headline"])}</h1><p>{esc(x["intro"])}</p></div><section><h2>{esc(x["core"])}</h2><p>{esc(lead)}</p><p class="small muted">'+(report_text('zh' if lang == 'zh' else 'en')('green_stars_mark_a_unique_column_best', 'Green stars mark a unique column best. Quality and all three latency measures use the same {case_count} pages; latency includes failed retries. P50 is the median and P95 is the time within which 95% of calls complete. Each method describes its measured batch; the designated pair’s timings use the certified same-window crossover.', case_count=case_count))+f'</p>{core}{latency_context}</section>'+f'''<section><h2>{report_text('zh' if lang == 'zh' else 'en')('by_page_type', 'By page type')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('filter_by_page_type_each_cell_is', 'Filter by page type. Each cell is mean page F1; a green star marks the unique row leader.')}</p>{slice_table}</section><section class="lead-grid"><div class="panel"><h3>{esc(x['dataset'])}</h3><p>{report_text('zh' if lang == 'zh' else 'en')('inspect_frozen_pages_source_urls_and_references', 'Inspect frozen pages, source URLs and references.')}</p><a href="{href(lang, 'datasets/index.html')}">{esc(x['dataset'])} →</a></div><div class="panel"><h3>{esc(x['results'])}</h3><p>{report_text('zh' if lang == 'zh' else 'en')('filter_individual_results_and_inspect_responses_and', 'Filter individual results and inspect responses and evidence.')}</p><a href="{href(lang, 'analysis/index.html')}">{esc(x['results'])} →</a></div><div class="panel"><h3>{esc(x['guide'])}</h3><p>{report_text('zh' if lang == 'zh' else 'en')('understand_differences_and_scoring_limits', 'Understand differences and scoring limits.')}</p><a href="{href(lang, 'guide/index.html')}">{esc(x['guide'])} →</a></div></section>'''
  home+=paired
  success_rows=''.join(f'<tr><td>{esc(p)}</td><td>{summary["providers"][p]["success_latency_count"]}/{case_count}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_mean_latency_ms"])}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_p50_latency_ms"])}</td><td>{format_wcxb_conditional_latency(summary["providers"][p]["success_p95_latency_ms"])}</td></tr>' for p in providers)
  if any(summary['providers'][p]['success_latency_count'] != case_count for p in providers):
   home+='<section><h2>'+(report_text('zh' if lang == 'zh' else 'en')('conditional_latency_for_delivered_content', 'Conditional latency for delivered content'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('only_pages_with_a_final_nonempty_response', 'Only pages with a final nonempty response enter this table, so denominators differ by method; failed retry time remains part of that page. The core table above retains the full common-denominator latency.'))+'</p><div class="scroll"><table><thead><tr><th>'+esc(x['provider'])+'</th><th>'+(report_text('zh' if lang == 'zh' else 'en')('delivered_pages', 'Delivered pages'))+'</th><th>Mean</th><th>P50</th><th>P95</th></tr></thead><tbody>'+success_rows+'</tbody></table></div></section>'
  issue_counts={(p,d):sum(r['provider']==p and r['quality_eligible'] and r['diagnosis']==d for r in rows) for p in providers for d in ('request_error','empty_output','error_page','low_precision_and_recall','low_precision','low_recall')}
  issue_types=[d for d in ('request_error','empty_output','error_page','low_precision_and_recall','low_precision','low_recall') if any(issue_counts[p,d] for p in providers)]
  issue_rows=''.join('<tr><td>'+esc(p)+'</td>'+''.join(f'<td>{issue_counts[p,d]}</td>' for d in issue_types)+'</tr>' for p in providers)
  issue_table='<div class="scroll"><table><thead><tr><th>'+esc(x['provider'])+'</th>'+''.join('<th>'+esc(diag(lang,d))+'</th>' for d in issue_types)+'</tr></thead><tbody>'+issue_rows+'</tbody></table></div>'
  home+='<section><h2>'+(report_text('zh' if lang == 'zh' else 'en')('issue_distribution', 'Issue distribution'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('programmatic_locators_across_qualified_pages_per_browser', 'Programmatic locators across {quality_count} qualified pages per browser; filter the results analysis to inspect corresponding pages.', quality_count=quality_count))+'</p>'+issue_table+'</section>'
  page(lang,'index.html',x['headline'],home)
  opts=lambda values,x=x:'<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(v)}">{esc(v)}</option>' for v in values)
  types=sorted({g['page_type'] for g in ground_truth.values()})
  type_options='<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in types)
  filters=f"""<div class="filters"><label>{esc(x['query'])}<input id="q" type="search"></label><label>{esc(x['provider'])}<select id="provider">{opts(providers)}</select></label><label>{esc(x['type'])}<select id="type">{type_options}</select></label><label>{report_text('zh' if lang == 'zh' else 'en')('diagnostic_result', 'Diagnostic result')}<select id="scope"><option value="fail">{report_text('zh' if lang == 'zh' else 'en')('inspect', 'Inspect')}</option><option value="pass">{report_text('zh' if lang == 'zh' else 'en')('no_locator', 'No locator')}</option><option value="all">{esc(x['all'])}</option></select></label><label>{esc(x['diagnosis'])}<select id="diagnosis"><option value="">{esc(x['all'])}</option><option value="issues">{esc(x['issues'])}</option>"""+''.join(f'<option value="{esc(v)}">{esc(diag(lang,v))}</option>' for v in sorted({r['diagnosis'] for r in rows if r['quality_eligible'] and r['diagnosis']!='none'}))+'</select></label></div>'
  inventory=[]
  for r in rows:
   if not r['quality_eligible']:continue
   case=r['case_id'];linkto=f'cases/{case}.html#{r["provider"]}'
   if lang=='en':linkto='en/'+linkto
   label=r['diagnosis']
   delivery='pass' if label=='none' else 'fail'
   error_text=(r['error_type'] or '')+((': '+r['error_message']) if r['error_message'] else '')
   inventory.append(f'''<tr data-case="{esc(case)}" data-provider="{esc(r['provider'])}" data-type="{esc(r['page_type'])}" data-result="{delivery}" data-diagnosis="{esc(label)}" data-diagnosis-label="{esc(diag(lang, label))}" data-error="{esc(error_text)}" data-search="{esc((case + ' ' + r['original_url'] + ' ' + str(r['error_message'] or '')).lower())}"><td><a href="{linkto}">{esc(case)}</a></td><td>{esc(type_label(lang, r['page_type']))}</td><td>{esc(r['provider'])}</td><td class="number">{fmt(r['f1'])}</td><td class="number">{fmt(r['precision'])}</td><td class="number">{fmt(r['recall'])}</td><td>{esc(diag(lang, label))}</td><td class="number">{r['total_elapsed_ms']:,.0f} ms</td><td><button type="button" data-diagnosis-open>{report_text('zh' if lang == 'zh' else 'en')('inspect_2', 'Inspect')}</button></td></tr>''')
  comparison_data={case:{'type':ground_truth[case]['page_type'],'label':type_label(lang,ground_truth[case]['page_type']),'url':by_case[case][providers[0]]['original_url'],'rows':{p:{'f1':by_case[case][p]['f1']} for p in providers}} for case in sorted(by_case) if case not in exclusion_ids}
  comparison_json=json.dumps(comparison_data,ensure_ascii=True).replace('<','\\u003c')
  comparison_options=''.join(f'<option value="{esc(p)}">{esc(p)}</option>' for p in providers)
  comparison_types='<option value="">'+esc(x['all'])+'</option>'+''.join(f'<option value="{esc(t)}">{esc(type_label(lang,t))}</option>' for t in types)
  comparison_choices=''.join(f'<label><input type="checkbox" name="compare-provider" value="{esc(p)}"{" checked" if p==providers[1] else ""}> {esc(p)}</label>' for p in providers)
  explorer=f"""<section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('same_page_method_comparison', 'Same-page method comparison')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('choose_a_baseline_and_comparison_browsers_then', 'Choose a baseline and comparison browsers, then filter qualified pages by F1 difference. A positive F1 percentage-point difference means the comparison browser scored higher.')}</p><div class="comparison-controls"><label>{report_text('zh' if lang == 'zh' else 'en')('baseline_browser', 'Baseline browser')}<select id="compare-baseline">{comparison_options}</select></label><label>{esc(x['type'])}<select id="compare-type">{comparison_types}</select></label><label>{report_text('zh' if lang == 'zh' else 'en')('outcome_relation', 'Outcome relation')}<select id="compare-relation"><option value="">{esc(x['all'])}</option><option value="higher">{report_text('zh' if lang == 'zh' else 'en')('comparison_higher', 'Comparison higher')}</option><option value="lower">{report_text('zh' if lang == 'zh' else 'en')('comparison_lower', 'Comparison lower')}</option><option value="equal">{report_text('zh' if lang == 'zh' else 'en')('equal', 'Equal')}</option></select></label></div><div class="choice-list">{comparison_choices}</div><label>{report_text('zh' if lang == 'zh' else 'en')('search_page_id_or_url', 'Search page ID or URL')} <input id="compare-query" type="search"></label><p class="summary-band"><span><b id="compare-count"></b> / {quality_count} {report_text('zh' if lang == 'zh' else 'en')('pages_2', 'pages')}</span></p><div class="scroll result-scroll comparison-result"><table><thead><tr id="compare-head"></tr></thead><tbody id="comparison-rows"></tbody></table></div><script>window.WCXB_COMPARE={comparison_json}</script></section>"""
  dialog=f'''<dialog id="diagnosis-dialog" aria-labelledby="diagnosis-title"><div class="dialog-inner"><button type="button" class="dialog-close" data-call-log-close aria-label="{report_text('zh' if lang == 'zh' else 'en')('close', 'Close')}">×</button><h2 id="diagnosis-title" data-diagnosis-title></h2><p><b>{report_text('zh' if lang == 'zh' else 'en')('locator', 'Locator')}:</b> <span data-diagnosis-stage></span></p><p><b>{report_text('zh' if lang == 'zh' else 'en')('program_error', 'Program error')}:</b> <span data-diagnosis-error></span></p><p>{report_text('zh' if lang == 'zh' else 'en')('this_is_a_programmatic_locator_without_independently', 'This is a programmatic locator without independently reviewed root cause. The detail has complete output, human reference and retry evidence.')}</p><p><a data-diagnosis-link href="#">{report_text('zh' if lang == 'zh' else 'en')('open_complete_same_page_evidence', 'Open complete same-page evidence →')}</a></p></div></dialog>'''
  inv=f"""<div class="hero"><h1>{esc(x['results'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('compare_methods_on_the_same_page_then', 'Compare methods on the same page, then filter qualified-reference results.')}</p></div>{explorer}<section><h2>{report_text('zh' if lang == 'zh' else 'en')('individual_results', 'Individual results')}</h2>{filters}<p><b id="count"></b> {esc(x['count'])} / {visible_count:,}</p><p id="empty-results" class="muted" hidden>{report_text('zh' if lang == 'zh' else 'en')('no_results_match_these_filters_adjust_the', 'No results match these filters. Adjust the page, browser or locator.')}</p><div class="scroll result-scroll"><table><thead><tr>"""+''.join('<th>'+esc(x[k])+'</th>' for k in ('case','type','provider','f1','precision','recall','diagnosis','latency'))+f"<th>{report_text('zh' if lang == 'zh' else 'en')('cause_and_evidence', 'Cause and evidence')}</th></tr></thead><tbody>"+''.join(inventory)+f'</tbody></table></div>{dialog}</section>'
  page(lang,'analysis/index.html',x['results'],inv)
  flow_labels=(['冻结 HTML',f'{len(providers)} 种配置转换','最终响应与重试','官方逐页评分','有效参考汇总'] if lang=='zh' else ['Frozen HTML',f'{len(providers)} conversions','Final response and retries','Official page score','Qualified aggregate'])
  flow_source='flowchart LR\n A["'+flow_labels[0]+'"] --> B["'+flow_labels[1]+'"]\n B --> C["'+flow_labels[2]+'"]\n C --> D["'+flow_labels[3]+'"]\n D --> E["'+flow_labels[4]+'"]\n C -- '+(report_text('zh' if lang == 'zh' else 'en')('failure_retry', 'failure retry'))+' --> B'
  flow='<div class="panel"><h2>'+(report_text('zh' if lang == 'zh' else 'en')('from_one_page_to_one_result', 'From one page to one result'))+'</h2><div class="flow"><div class="flow-fallback">'+' <b class="arrow">→</b> '.join(f'<span class="tag">{esc(label)}</span>' for label in flow_labels)+'</div><pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div></div>'
  measured_records={p:{'provider':p,'provider_metadata':identities[p]} for p in providers}
  route_descriptions={p:_observed_route(native_browser_engine(p,measured_records[p]),identities[p],language=lang) for p in providers}
  interface_specs={p:method_spec(p,measured_records[p],language=lang) for p in providers}
  measured_routes='<ul>'+''.join(f'<li>{esc(browser_label(p))}: {esc(route_descriptions[p])}</li>' for p in providers)+'</ul>'
  if lang=='zh':
   method=('<div class="hero"><h1>' + t('methodology', 'Methodology') + f"""</h1><p>{case_count} 份冻结 HTML、全部执行配置、一套官方词语重合评分。</p></div>{flow}<section class="panel"><h2>输入与运行边界</h2><p>WCXB v1.0 test 分区中，按冻结输入和人工参考核定的 {case_count} 页进入本报告。全部配置读取完全相同的冻结 HTML；本地配置通过隔离的回环 HTTPS 服务读取，远程配置使用 HTML 输入接口。外部子资源被阻断。每页一次逻辑调用；失败最多重试 {profile.get('max_failure_retries', 0)} 次，全部物理调用保留并累计端到端耗时，最后一次响应是唯一正式结果。</p><p>没有共同的主内容选择器。</p>{measured_routes}</section><section class="panel"><h2>分母与评分</h2><p>官方评估器将文本转为小写词语，按词频计算精确率、召回率和 F1。质量比较只使用事前验证的 {quality_count} 页有效参考，逐页等权。F1 不计段落顺序、Markdown 结构或表格行列。应包含和应排除片段另作诊断，不进入 F1。</p><p>质量与耗时都使用同一批 {case_count} 页；正式版与 PR 构建版分别保留原始调用证据。</p></section><section class="panel"><h2>定位与耗时</h2><p>""" + t('request_failures_empty_output_and_error_pages', 'Request failures, empty output and error pages are located separately; with a non-empty reference, precision or recall below 25% shows the matching low value. This threshold is only a filter, not a WCXB official pass mark. Latency includes calls and failure retries and is not browser-engine speed; configurations differ in work route and deployment distance.') + '</p></section>')
  else:
   method=f'<div class="hero"><h1>Method</h1><p>All {case_count} frozen HTML pages, participating configurations, one official word-overlap evaluator.</p></div>'+flow+f'<section class="panel"><h2>Inputs and execution</h2><p>The {case_count} WCXB v1.0 test pages qualified by frozen input and human reference enter this report. The participating configurations read the same frozen HTML. Local configurations use an isolated loopback HTTPS fixture; remote configurations use an HTML input interface. External subresources are blocked. Each page has one logical result; a failed call allows up to {profile.get("max_failure_retries",0)} retries. Every physical call and its latency are retained, with the final response authoritative.</p><p>No shared main-content selector is applied.</p>{measured_routes}</section><section class="panel"><h2>Denominators and score</h2><p>The official evaluator lowercases and tokenizes text, then computes word-frequency precision, recall and F1. Quality compares only the {quality_count} references qualified before calls, with equal page weights. F1 does not score paragraph order, Markdown structure or table layout. Required and forbidden snippets are separate diagnostics outside F1.</p><p>Quality and latency use the same {case_count} pages. The paired configurations’ calls each retain their own immutable evidence.</p></section><section class="panel"><h2>Locators and latency</h2><p>Request errors, empty outputs and error pages are separate locators. Precision or recall below 25% on nonempty references triggers a low-value locator; this is a filter, not an official WCXB pass threshold. Latency includes calls and failed retries, not pure browser-core time; local and remote routes differ.</p></section>'

  guide=report_content['guide'][lang]
  method+='<section class="panel"><h2>'+(report_text('zh' if lang == 'zh' else 'en')('snippet_annotation_boundary', 'Snippet annotation boundary'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('forbidden_snippets_also_appear_in_their_human', '{without_present_in_main_content} forbidden snippets also appear in their human main-content references. Official snippet values are retained, but these conflicts do not diagnose browser errors; result pages identify them explicitly.', without_present_in_main_content=audit['without_present_in_main_content']))+'</p></section>'
  page(lang,'methodology/index.html',x['method'],method)
  page(lang,'guide/index.html',x['guide'],guide)
  dataset_rows=[]
  for case in sorted(ground_truth):
   gt=ground_truth[case]
   source_url=by_case[case][providers[0]]['original_url']
   reference=gt['main_content']
   dataset_rows.append(
    f'''<tr data-dataset-case="{esc(case + ' ' + source_url.lower())}" data-page-type="{esc(gt['page_type'])}"><td>{esc(case)}</td><td>{esc(type_label(lang, gt['page_type']))}</td><td class="source">{esc(source_url)}</td><td class="number">{len(reference):,}</td><td><a href="{href(lang, 'cases/' + case + '.html#preview')}">{report_text('zh' if lang == 'zh' else 'en')('read_frozen_html', 'Read frozen HTML')}</a></td><td><a href="{href(lang, 'cases/' + case + '.html#reference')}">{report_text('zh' if lang == 'zh' else 'en')('human_main_content', 'Human main content')}</a></td><td><a href="{href(lang, 'cases/' + case + '.html')}">{report_text('zh' if lang == 'zh' else 'en')('view_results', 'View results')}</a></td></tr>''')
  dataset_intro=(report_text('zh' if lang == 'zh' else 'en')('the_task_is_to_extract_main_content', 'The task is to extract main content from {case_count} frozen HTML pages. This inventory owns inputs and human references; the measured browser scores are in Results analysis.', case_count=case_count))
  dataset=f"""<div class="hero dataset-hero"><h1>{esc(x['dataset'])}</h1><p>{esc(dataset_intro)}</p></div><section><h2>{report_text('zh' if lang == 'zh' else 'en')('frozen_pages_and_references', 'Frozen pages and references')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('the_source_benchmark_provides_human_selected_main', 'The source benchmark provides human-selected main-content plain text, not a complete Markdown answer. Character count describes annotation scope, not page completeness.')}</p><div class="filters"><label>{report_text('zh' if lang == 'zh' else 'en')('search_page_id_or_url_2', 'Search page ID or URL')}<input id="dataset-query" type="search"></label><label>{esc(x['type'])}<select id="dataset-type">{type_options}</select></label></div><p><b id="dataset-count">{case_count}</b> / {case_count} {report_text('zh' if lang == 'zh' else 'en')('pages_2', 'pages')}</p><div class="scroll result-scroll"><table id="wcxb-dataset"><thead><tr><th>ID</th><th>{esc(x['type'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('original_url', 'Original URL')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('reference_characters', 'Reference characters')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('frozen_input', 'Frozen input')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('human_reference', 'Human reference')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('results', 'Results')}</th></tr></thead><tbody>{''.join(dataset_rows)}</tbody></table></div></section>"""
  page(lang,'datasets/index.html',x['dataset'],dataset)
  review_intro=(report_text('zh' if lang == 'zh' else 'en')('certification_covers_logical_results_source_bytes_matched', 'Certification covers {result_count:,}/{result_count:,} logical results; source bytes matched before and after all calls. Targeted anomaly review inspected {reviewed_cases} pages and {reviewed_results} outputs; the other {unreviewed_results:,} results were not individually reviewed.', result_count=result_count, reviewed_cases=reviewed_cases, reviewed_results=reviewed_results, unreviewed_results=unreviewed_results))
  review_zh=f"当前评测范围按冻结 HTML 和人工参考的有效性确定，不依据浏览器输出或得分。全部配置在同一批 {case_count} 页上比较。"
  review_en=f"The current evaluation population was qualified from frozen HTML and human references before reading browser outputs or scores. All participating configurations are compared on the same {case_count} pages."
  review=f"""<div class="hero"><h1>{esc(x['review'])}</h1><p>{esc(review_intro)}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('source_qualification', 'Source qualification')}</h2><p>{(review_zh if lang == 'zh' else review_en)}</p></section><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('execution_evidence', 'Execution evidence')}</h2><p>SHA-256: <code>{summary['source_sha256']}</code></p><p><a href="certification.json">{report_text('zh' if lang == 'zh' else 'en')('certification_receipt', 'Certification receipt')}</a> · <a href="wcxb-score-summary.json">{report_text('zh' if lang == 'zh' else 'en')('score_summary_json', 'Score summary JSON')}</a> · <a href="evidence-manifest.json">{report_text('zh' if lang == 'zh' else 'en')('per_page_evidence_hashes', 'Per-page evidence hashes')}</a> · <a href="research/wcxb-representative-review.json">{report_text('zh' if lang == 'zh' else 'en')('representative_review_record', 'Representative review record')}</a></p></section>"""
  page(lang,'review/index.html',x['review'],review)

  versions=''.join(f'<tr><td><a href="{href(lang,"providers/"+p+".html")}">{esc(browser_label(p))}</a></td><td>{esc(identities[p].get("reported_version") or identities[p].get("endpoint") or "service version undisclosed")}</td><td>{esc(route_descriptions[p])}</td><td><code>{esc(identities[p].get("binary_sha256") or "hosted")}</code></td></tr>' for p in providers)
  interface_example='\n\n'.join(browser_label(p)+': '+interface_specs[p]['entry']+'\n'+json.dumps(interface_specs[p]['request'],ensure_ascii=False,indent=2) for p in providers)
  adoption=f"""<div class="hero"><h1>{esc(x['adoption'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('measured_configurations_versions_input_routes_and_output', 'Measured configurations, versions, input routes and output contracts.')}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('measured_implementations', 'Measured implementations')}</h2><div class="scroll"><table><thead><tr><th>{esc(x['provider'])}</th><th>{esc(x['version'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('route', 'Route')}</th><th>SHA-256</th></tr></thead><tbody>{versions}</tbody></table></div></section><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('interface_examples', 'Interface examples')}</h2><pre>{esc(interface_example)}</pre><p>{report_text('zh' if lang == 'zh' else 'en')('these_are_registered_interface_examples_measured_routes', 'These are registered interface examples; measured routes follow the saved call records.')}</p></section>"""
  adoption+='<section class="panel"><h2>'+(report_text('zh' if lang == 'zh' else 'en')('public_code_adoption_signals', 'Public-code adoption signals'))+'</h2><p>'+(report_text('zh' if lang == 'zh' else 'en')('in_the_verified_signature_sample_the_cloudflare', 'In the {generated_at} verified signature sample, the Cloudflare /markdown interface appears in {cloudflare_repo_count} repositories and {cloudflare_file_count} files. This signature does not identify the Kitesurf backend or user count. The other CLI/browser configurations have no matching signature inventory, so no adoption ranking is made.', generated_at=adoption_data['generated_at'][:10], cloudflare_repo_count=cloudflare_repo_count, cloudflare_file_count=cloudflare_file_count))+'</p><p><a href="research/provider-adoption.json">'+(report_text('zh' if lang == 'zh' else 'en')('verified_sample_and_sources', 'Verified sample and sources'))+'</a></p></section>'

  page(lang,'adoption/index.html',x['adoption'],adoption)
  for p in providers:
   s=summary['providers'][p]
   profile_body=f'''<div class="hero"><h1>{esc(p)}</h1><p>{esc(route_descriptions[p])}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('measured_result', 'Measured result')}</h2><p>{report_text('zh' if lang == 'zh' else 'en')('qualified_f1', 'Qualified F1')}: <strong>{fmt(s['quality_f1'])}</strong> ({quality_count}) · {report_text('zh' if lang == 'zh' else 'en')('nonempty_outputs', 'Nonempty outputs')}: {s['delivered']}/{case_count}</p><p>{report_text('zh' if lang == 'zh' else 'en')('all_call_end_to_end_mean_p50', 'All-call end-to-end mean / P50 / P95')}: {s['mean_latency_ms']:,.0f} / {s['p50_latency_ms']:,.0f} / {s['p95_latency_ms']:,.0f} ms ({case_count})</p><p>{esc(x['version'])}: {esc(identities[p].get('reported_version') or identities[p].get('endpoint') or 'service version undisclosed')}</p><p><code>{esc(identities[p].get('binary_sha256') or 'hosted')}</code></p></section><p><a href="{href(lang, 'analysis/index.html')}">{esc(x['results'])} →</a></p>'''
   page(lang,'providers/'+p+'.html',p,profile_body)
  changes_zh=f"全部配置完成全部 {case_count} 页调用；质量展示只使用事前核定的 {quality_count} 页有效参考，共 {visible_count:,} 条结果。端到端耗时保留每款 {case_count} 次调用，以免事后剔除慢或失败的调用。"
  changes_en=f"All participating configurations completed {case_count} calls. Quality displays use only {quality_count} references qualified before calls, or {visible_count:,} results. End-to-end latency retains all {case_count} calls per method so slow or failed calls are not removed after measurement."
  changes=f"""<div class="hero"><h1>{esc(x['changes'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('current_result_denominator_and_evidence_scope', 'Current result, denominator and evidence scope.')}</p></div><section class="panel"><h2>{report_text('zh' if lang == 'zh' else 'en')('qualified_references_and_all_calls', 'Qualified references and all calls')}</h2><p>{(changes_zh if lang == 'zh' else changes_en)}</p><p><a href="research/report-changes.json">{report_text('zh' if lang == 'zh' else 'en')('structured_change_record', 'Structured change record')}</a></p></section>"""
  page(lang,'changes/index.html',x['changes'],changes)
  account_rows=[]
  for p in providers:
   access=method_access(p,access_records,language=lang)
   links=' '.join(f'<a href="{esc(url)}">{esc(label)}</a>' for label,url in access['links'])
   account_rows.append(f'<tr><td>{esc(browser_label(p))}</td><td>{esc("、".join(access["env_vars"]))}</td><td>{esc(access["account"])}</td><td>{links}</td><td>{esc(access["configuration"])}</td></tr>')
  accounts=f"""<div class="hero"><h1>{esc(x['accounts'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('account_requirements_come_from_the_method_registry', 'Account requirements come from the method registry. Missing installation bindings are disclosed; local paths and credential values are not published.')}</p></div><section class="panel"><div class="scroll"><table><thead><tr><th>{esc(x['provider'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('environment_variables', 'Environment variables')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('account_requirement', 'Account requirement')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('official_source', 'Official source')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('installation_configuration', 'Installation configuration')}</th></tr></thead><tbody>{''.join(account_rows)}</tbody></table></div></section>"""

  page(lang,'accounts/index.html',x['accounts'],accounts)
  repro=f'''<div class="hero"><h1>{esc(x['repro'])}</h1><p>{report_text('zh' if lang == 'zh' else 'en')('versions_and_source_are_bound_by_this', 'Versions and source are bound by this cohort certificate.')}</p></div><section class="panel"><p>WCXB v1.0 test · {case_count} × {len(providers)} · SHA-256 <code>{summary['source_sha256']}</code></p><p>{esc(x['source'])}: <a href="{source_repository}/tree/{source_commit}">GitHub {source_commit[:7]}</a> · <a href="sample-manifest.json">{report_text('zh' if lang == 'zh' else 'en')('full_test_manifest', 'Full test manifest')}</a> · <a href="certification.json">certification.json</a> · <a href="wcxb-score-summary.json">score summary JSON</a></p><div class="scroll"><table><thead><tr><th>{esc(x['provider'])}</th><th>{esc(x['version'])}</th><th>{report_text('zh' if lang == 'zh' else 'en')('route', 'Route')}</th><th>SHA-256</th></tr></thead><tbody>{versions}</tbody></table></div><pre>uv run --frozen python scripts/run_certified_benchmark.py wcxb --dataset &lt;WCXB source&gt; --sample-manifest evaluation/public-wcxb-test-qualified.json --output-root &lt;new evidence dir&gt; --env-file .env\nuv run --frozen python -m harness.web_fetch wcxb-score --source &lt;WCXB source&gt; --sample-manifest evaluation/public-wcxb-test-qualified.json --evidence-root &lt;certified evidence dir&gt;\nuv run --frozen python scripts/build_report.py --evidence &lt;certified evidence dir&gt; --wcxb-source &lt;WCXB source&gt; --output reports/webfetch</pre><p class="small muted">{esc(x['no_review'])}</p></section>'''
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
    snippet_detail='<p>'+(report_text('zh' if lang == 'zh' else 'en')('missing_required_snippets', 'Missing required snippets'))+': '+esc('; '.join(missing_snippets) or '—')+'</p><p>'+(report_text('zh' if lang == 'zh' else 'en')('included_valid_forbidden_snippets', 'Included valid forbidden snippets'))+': '+esc('; '.join(included_forbidden) or '—')+'</p>'+(('<p>'+(report_text('zh' if lang == 'zh' else 'en')('annotation_conflict_these_forbidden_snippets_also_occur', 'Annotation conflict: these forbidden snippets also occur in the human main-content reference, so they cannot diagnose a browser failure.'))+' '+esc('; '.join(conflicts))+'</p>') if conflicts else '')
    retries=r.get('retry_failures') or []
    retry_html=('<details><summary>'+(report_text('zh' if lang == 'zh' else 'en')('earlier_failed_calls', 'Earlier failed calls'))+'</summary><ul>'+''.join(f'<li>#{v["physical_attempt"]} · {v["elapsed_ms"]:,.0f} ms · {esc(v["error_type"])} · {esc(v["error_message"])} · <a href="{esc(v["artifact_path"])}">'+(report_text('zh' if lang == 'zh' else 'en')('raw_response', 'Raw response'))+'</a></li>' for v in retries)+'</ul></details>') if retries else ''
    timing_note=('<p class="small muted">'+(report_text('zh' if lang == 'zh' else 'en')('latency_is_from_the_selected_final_crossover', 'Latency is from the selected final crossover round; content and scores are from the original call.'))+' <a href="research/wcxb-full-paired-latency/calls.jsonl">'+(report_text('zh' if lang == 'zh' else 'en')('timing_calls', 'Timing calls'))+'</a></p>') if r.get('latency_source')=='paired_final_round' else ''
    panels.append(f'<section class="panel" id="{p}"><h2>{esc(p)} · F1 {fmt(r["f1"])}</h2><p><span class="tag">{esc(diag(lang,"excluded" if case in exclusion_ids else r["diagnosis"]))}</span> · {r["total_elapsed_ms"]:,.0f} ms · {r["physical_attempt_count"]} calls · {esc(r["attempt_id"])}</p>'+timing_note+f'<p>{esc(x["missing"])}: {esc(missing)}</p><p>{esc(x["extra"])}: {esc(extra)}</p>'+snippet_detail+(f'<p class="bad">{esc(x["error"])}: {esc(r["error_type"])}{(" · HTTP "+str(r["status_code"])) if r.get("status_code") else ""}{(" · "+esc(r["error_message"])) if r["error_message"] else ""}</p>' if r['error_type'] else '')+retry_html+f'''<details data-output><summary>{esc(x['output'])} · {r['output_chars']:,} characters</summary><p><a href="{evidence_path}">{report_text('zh' if lang == 'zh' else 'en')('complete_evidence_json', 'Complete evidence JSON')} ↓</a></p><pre data-src="{evidence_path}" data-provider="{p}"></pre></details></section>''')
   qualification=(f"""<p class="callout">{report_text('zh' if lang == 'zh' else 'en')('this_reference_was_excluded_before_calls_official', 'This reference was excluded before calls; official raw scores and outputs remain visible, but it does not enter the main quality mean.')} {esc(exclusion_ids[case])}</p>""" if case in exclusion_ids else '')
   preview=_wmb_html_preview(source_html,lang=='en').replace('class="wmb-html-preview"','class="frozen-preview"')
   raw_input=evidence_path
   preview_note=(report_text('zh' if lang == 'zh' else 'en')('this_reading_view_follows_the_frozen_html', 'This reading view follows the frozen HTML document order; it is not a screenshot of the original website. The dataset does not preserve site stylesheets or image files, so the original layout cannot be reconstructed from this input. Scripts, external resources and navigation are disabled.'))
   preview_section=f'''<section class="panel" id="preview"><h2>{report_text('zh' if lang == 'zh' else 'en')('frozen_html_reading_view', 'Frozen HTML reading view')}</h2><p>{preview_note} <a href="{raw_input}">{report_text('zh' if lang == 'zh' else 'en')('download_complete_input_and_evidence_json', 'Download complete input and evidence JSON')}</a></p>{preview}</section>'''
   body=f'''<div class="hero"><h1>{esc(case)} · {esc(type_label(lang, gt['page_type']))}</h1><p class="source"><a href="{href(lang, 'cases/' + case + '.html#preview')}" title="{report_text('zh' if lang == 'zh' else 'en')('read_frozen_html', 'Read frozen HTML')}">{esc(group[providers[0]]['original_url'])}</a></p></div>{qualification}<section><h2>{esc(x['summary'])}</h2><div class="scroll"><table><thead><tr>'''+''.join(f'<th>{esc(x[k])}</th>' for k in ('provider','precision','recall','f1'))+f"<th>{report_text('zh' if lang == 'zh' else 'en')('required_hits', 'Required hits')}</th><th>{report_text('zh' if lang == 'zh' else 'en')('forbidden_hits', 'Forbidden hits')}</th><th>{esc(x['diagnosis'])}</th></tr></thead><tbody>"+same+f'''</tbody></table></div></section>{preview_section}<section class="panel" id="reference"><h2>{esc(x['reference'])}</h2><p><a href="{evidence_path}">{report_text('zh' if lang == 'zh' else 'en')('complete_evidence_json', 'Complete evidence JSON')}</a> · <a href="{source_link}">{esc(x['source'])} HTML.gz</a></p><details data-output><summary>{report_text('zh' if lang == 'zh' else 'en')('frozen_html_input', 'Frozen HTML input')}</summary><pre data-src="{evidence_path}"></pre></details><pre>{esc(readable_output(reference))}</pre></section>'''+''.join(panels)
   page(lang,f'cases/{case}.html',case,body)
 write_language_contract(out, include_english=include_english)

def provider_label(name):
 return browser_method_label(name)

def case_track_label(case):
 family=report_family(case)
 if family in {'web_media','media_file'}:return REPORT_FAMILIES[family]
 if case.get('track','web')=='web':
  return {'file':t('file_delivery', 'File delivery'),'resource':t('resource_discovery', 'Resource discovery'),'structure':t('structured_content', 'Structured content')}.get(case.get('task_kind','body'),TRACKS['web'])
 return TRACKS[case['track']]
REPORT_TRACKS = {key: TRACKS[key] for key in ('web', 'dynamic', 'challenge', 'risk_control', 'document', 'image', 'video')}

def active_tracks(cases):
 rows=[]
 for key,label in REPORT_FAMILIES.items():
  selected=[c for c in cases.values() if report_family(c)==key]
  if not selected:continue
  if key=='web' and any(c.get('task_kind','body')!='body' for c in selected):
   labels={case_track_label(c) for c in selected}
   label=next(iter(labels)) if len(labels)==1 else t('web_tasks', 'Web tasks')
  rows.append((key,label))
 return rows

def track_count_label(count):return str(count)

def is_headless_cohort(records):
 return is_browser_comparison(records)

def provider_configuration(name,records,info):
 """Compatibility accessor for the generic method-page model."""
 return method_page_model(name,provider_label(name),records)['configuration']

def provider_page_body(name,records,cases,headers,tracks,*,native_default=False):
 """Render one method page from generic result records and method metadata."""
 model=method_page_model(name,provider_label(name),records)
 english_model=method_page_model(name,browser_method_label(name,language='en'),records,language='en')
 def field(key):return model_text(model[key],english_model[key])
 controls=filter_controls([('track',t('family', 'Family'),tracks),('result',t('result', 'Result'),[('pass',t('pass', 'Pass')),('fail',t('fail', 'Fail'))])]+failure_filters(records,cases))
 configuration=f'<p><code>{field("endpoint")}</code></p>'
 if native_default:
  configuration+='<p>'+model_text(*pair('call_counts_retries_and_deadlines_come_from', 'Call counts, retries and deadlines come from run records; internal parameters come from saved requests.'))+'</p>'
  configuration+='<p>'+link('../methodology/index.html',t('view_run_parameters', 'View run parameters'),'View execution parameters')+' · '+link('../reproduce/index.html',t('view_frozen_configuration_and_evidence', 'View frozen configuration and evidence'),'View frozen configuration and evidence')+'</p>'
 else:
  configuration+=f'<p>{field("freshness")}</p><p>{field("configuration")}</p>'
 body=hero(field('title'),field('contract'))+section(t('measured_configuration', 'Measured configuration'),configuration)
 body+=section(t('method_resources', 'Method resources'),'<p class="actions">'+link('../adoption/index.html#'+model['pricing_anchor'],t('pricing_billing', 'Pricing & billing →'))+link('../adoption/index.html#interface-'+name,t('interfaces_examples', 'Interfaces & examples →'))+'</p>')
 return body+section(t('execution_records', '{count} execution records', count=len(records)),controls+table(headers,[attempt_row(r,cases[r['case_id']]) for r in records],'filtered-table'))
JS=asset_text('report.js').replace('/*mermaid-script-attributes*/',mermaid_script_attributes('script'))
EXTRA=asset_text('report-extra.css')

def readable_output(s):
 return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',lambda m:'\\x'+format(ord(m[0]),'02x'),s)

_PRIVATE_LOG_KEYS={'raw_response_path','diagnostic_log_path','private_artifacts','private_artifact','private_path'}
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
   if isinstance(attempt,dict):candidates.append((t('physical_call', 'Physical call {get}', get=attempt.get('physical_attempt', index)),attempt.get('call_log')))
 candidates.extend([(t('final_physical_call', 'Final physical call'),record.get('call_log')),(t('final_physical_call', 'Final physical call'),metadata.get('call_log')),
                    (t('final_physical_call', 'Final physical call'),{'path':record.get('call_log_path'),'sha256':record.get('call_log_sha256')}),
                    (t('final_physical_call', 'Final physical call'),{'path':metadata.get('call_log_path'),'sha256':metadata.get('call_log_sha256')})])
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

def _error_locator(message):
 """Use a retained error line, keeping complete traces in the log section."""
 text=str(message or '')
 if len(text)<=300:return text
 lines=text.splitlines()
 for line in reversed(lines):
  if re.search(r'^(?:Reason:|Error:|[\w.]*Error:)|\b(?:timed out|timeout_phase=)',line,re.I):
   return line[:300]
 return t('error_details_are_in_this_run_s', "Error details are in this run's log.")


def empty_output_reason(record,case,output):
 if output:return ''
 metadata=record.get('provider_metadata') or {}
 delivered=metadata.get('file_evaluation',{}).get('files',[])
 if case.get('task_kind')=='file' and delivered:
  return t('this_task_delivered_original_files_empty_text', 'This task delivered {count} original files. Empty text does not mean nothing was delivered; the file bytes are listed separately below.', count=len(delivered))
 if record.get('error_message'):
  return t('the_final_output_is_genuinely_empty_the', 'The final output is genuinely empty. The adapter recorded this reason: {error_locator}', error_locator=_error_locator(record['error_message']))
 if not record.get('ok'):
  error_type=record.get('error_type') or t('error_type_not_recorded', 'Error type not recorded')
  return t('the_final_output_is_genuinely_empty_the_2', 'The final output is genuinely empty. The request failed with error type {error_type}; the historical record retained no further error text.', error_type=error_type)
 if metadata.get('empty_content'):
  return t('the_final_output_is_genuinely_empty_the_3', 'The final output is genuinely empty. The upstream request returned a response, but the adapter obtained no extractable body text.')
 return t('the_final_output_is_genuinely_empty_this', 'The final output is genuinely empty. This historical record retained no more specific reason, so it cannot establish whether the cause was the server, network, or adapter.')

def _reader_log_excerpt(text):
 """Select real log lines for the reader while preserving original line numbers."""
 lines=text.splitlines();selected=set()
 if lines:selected.add(0)
 diagnostic=re.compile(r'error|warn|fail|timeout|timed out|panic|denied|challenge',re.I)
 lifecycle=re.compile(r'fetch request complete|final_url=|document_to_markdown|extract|completed.*fetch|finished.*fetch',re.I)
 for index,line in enumerate(lines):
  if diagnostic.search(line):selected.update(range(max(0,index-1),min(len(lines),index+2)))
  elif lifecycle.search(line) and len(selected)<12:selected.add(index)
  if len(selected)>=18:break
 if not selected:selected.update(range(min(12,len(lines))))
 return '\n'.join(f'{index+1:>6}  {lines[index]}' for index in sorted(selected)[:18])

def _worker_response_stream(row):
 """Recognize saved worker protocol responses, which belong to output evidence."""
 if not isinstance(row,dict) or row.get('source')!='native_process_stdout':return False
 try:payload=json.loads(row.get('text',''))
 except (ValueError,TypeError):return False
 return isinstance(payload,dict) and isinstance(payload.get('ok'),bool) and any(key in payload for key in ('content','raw_tool_response','error_type'))

def runtime_log_text(text):
 """Exclude protocol responses from the public log; retain private capture unchanged."""
 lines=[]
 for line in text.splitlines(keepends=True):
  try:row=json.loads(line)
  except ValueError:row=None
  if not _worker_response_stream(row):lines.append(line)
 return ''.join(lines)

def _separate_observed_logs(text):
 """Separate captured product streams from caller events without inventing steps."""
 markers=list(re.finditer(r'^--- native [^\n]+ ---\s*$',text,re.M))
 caller=text[:markers[0].start()] if markers else text
 native='\n'.join(text[m.end():markers[i+1].start() if i+1<len(markers) else len(text)].lstrip('\n') for i,m in enumerate(markers))
 events=[];library=[]
 for line in caller.splitlines():
  try:row=json.loads(line)
  except json.JSONDecodeError:
   events.append(line);continue
  if _worker_response_stream(row):continue
  if isinstance(row,dict) and row.get('source') in ('native_process_stderr','native_process_stdout'):
   stream=str(row.get('text',''))
   try:payload=json.loads(stream)
   except ValueError:payload=None
   if isinstance(payload,dict) and payload.get('source')=='caller_worker':events.append(stream.rstrip('\n'))
   else:library.append(stream)
  elif isinstance(row,dict) and row.get('source')=='native_library':
   library.append(str(row.get('message',''))+(('\n'+str(row['exception'])) if row.get('exception') else ''))
  else:events.append(line)
 if library:native+='\n'+'\n'.join(library)
 return native.strip('\n'), '\n'.join(events)


def _log_evidence_dialog(relative, label):
    """Use the shared evidence dialog, backed by one existing public file."""
    target='saved-log-'+hashlib.sha256(relative.encode()).hexdigest()[:16]
    return ((f'''<button type="button" class="call-log-button" data-call-log-open="{target}" aria-haspopup="dialog">{model_text(*pair('open_complete_record', 'Open complete record'))}</button><dialog class="call-log-dialog wmb-detail-dialog" id="{target}" aria-labelledby="{target}-title"><div class="call-log-shell"><header><h2 id="{target}-title">{label}</h2><button type="button" data-call-log-close aria-label="''' + t('close', 'Close') + '">' + t('close', 'Close') + f'</button></header><div class="wmb-dialog-content"><pre data-file="{esc(relative)}" data-readable="1" data-evidence-kind="log"></pre></div></div></dialog>'))


def diagnostic_view_page(out, relative, label, note):
    """Show the real sanitized text in HTML; retain its original download bytes."""
    source=contained_path(out,relative)
    raw=source.read_bytes()
    view=Path(relative).with_suffix('.html').as_posix()
    body=(hero(label,t('actual_saved_records_formatted_for_browsing_without', 'Actual saved records, formatted for browsing without additional product calls.'))+
          section(t('record_source', 'Record source'),f"""<p>{esc(note)}</p><p>{link('../' + relative, t('download_original_public_file', 'Download original public file'))} · {str(len(raw))} 字节 · SHA-256：<code class="break">{hashlib.sha256(raw).hexdigest()}</code></p>""")+
          section(model_text(*pair('record_excerpt', 'Record excerpt')),'<pre>'+esc(_diagnostic_log_excerpt(raw.decode('utf-8','replace'))[:6000])+'</pre>')+
          section(model_text(*pair('complete_record', 'Complete record')),_log_evidence_dialog(relative,esc(label))))
    write(out,view,label,body)
    return view


def run_log_section(out,record,output):
 """Render the approved summary → fields → real-log drill-down."""
 metadata=record.get('provider_metadata') or {}
 bindings=call_log_bindings(record);logs=[]
 for label,binding in bindings:
  path=contained_path(out,binding['path'])
  if not path.is_file():continue
  raw=path.read_bytes();observed=hashlib.sha256(raw).hexdigest()
  if _RENDER_CONTEXT.get().score_audit:
   from harness.web_fetch.published_snapshot import read_snapshot_public_file
   read_snapshot_public_file(out,binding)
  else:
   if binding.get('sha256') and binding['sha256']!=observed:raise ValueError(f'Call log hash mismatch: {record["attempt_id"]}')
   if binding.get('bytes') is not None and binding['bytes']!=len(raw):raise ValueError(f'Call log size mismatch: {record["attempt_id"]}')
  try:payload=json.loads(raw)
  except (UnicodeDecodeError,json.JSONDecodeError):payload={}
  logs.append({'label':label,'path':binding['path'],'sha256':observed,
               'bytes':len(raw),'payload':payload})
 payload=logs[-1]['payload'] if logs else {}
 process=payload.get('process') if isinstance(payload.get('process'),dict) else {}
 timing=payload.get('timing') if isinstance(payload.get('timing'),dict) else {}
 native_public=metadata.get('publication_views',{}).get('native_response')
 native_public_link=('<br>'+link('../'+native_public['path'],t('download_the_complete_native_response_public_copy', 'Download the complete native response (public copy)'),'Download complete native response (public view)')+' · '+link('../'+native_public['receipt'],t('original_and_public_copy_verification_records', 'Original and public-copy verification records'),'Original/public identity record')) if native_public else ''
 artifact_relative=record.get('artifact_path')
 artifact=contained_path(out,artifact_relative) if artifact_relative else None
 output_data=artifact.read_bytes() if artifact and artifact.is_file() else output.encode('utf-8')
 output_bytes=len(output_data)
 stderr_total=0;any_stderr=False;any_truncated=False;diagnostics=[]
 for index,log in enumerate(logs,1):
  log_process=(log['payload'].get('process')
               if isinstance(log['payload'].get('process'),dict) else {})
  stderr_text=readable_output(str(log_process.get('stderr') or ''))
  stderr_data=stderr_text.encode('utf-8');stderr_total+=len(stderr_data)
  truncated=bool(log_process.get('stderr_truncated') or metadata.get('stderr_truncated'))
  any_stderr=any_stderr or bool(stderr_data);any_truncated=any_truncated or truncated
  raw_download=((f"""<a href="../{esc(log['path'])}" download>""" + t('download_call_log_json', 'Download call-log JSON') + f"""</a> · <code class="break">JSON SHA-256：{esc(log['sha256'])}</code>"""))
  if stderr_data:
   rows=''.join(f'<li value="{int(match.group(1))}"><code>{esc(match.group(2))}</code></li>'
                for line in _reader_log_excerpt(stderr_text)[:6000].splitlines()
                if (match:=re.match(r'\s*(\d+)\s{2}(.*)',line)))
   completeness=(t('this_public_log_contains_the_leading_portion', 'This public log contains the leading portion retained in the call record; that record marks it as truncated.') if truncated else
                 t('the_lines_below_are_quoted_from_the', 'The lines below are quoted from the complete stderr / debug log saved for this physical call.'))
   body=(('<p class="source-note">' + t('line_numbers_refer_to_the_process_stderr', '{completeness} Line numbers refer to the process.stderr field in the call log JSON; human notes are not written into the log box.', completeness=completeness) + f"""</p>{(f'<ol class="log-lines">{rows}</ol>' if rows else '')}<p>{raw_download}</p>{_log_evidence_dialog(log['path'], esc(log['label']))}"""))
  else:
   body=(('<p class="source-note">' + t('no_displayable_stderr_or_debug_log_was', 'No displayable stderr or debug log was saved for this physical call.') + f"</p><p>{raw_download}</p>{_log_evidence_dialog(log['path'], esc(log['label']))}"))
  diagnostics.append(f'<article><h3>{esc(log["label"])}</h3>{body}</article>')
 complete_label=(t('yes', 'Yes') if any_stderr and not any_truncated else
                 t('no_at_least_one_public_log_is', 'No (at least one public log is truncated)') if any_stderr else t('not_applicable_no_debug_log_retained', 'Not applicable (no debug log retained)'))
 public_debug=metadata.get('public_debug_log_path')
 if public_debug:
  debug_path=contained_path(out,public_debug)
  debug_bytes=debug_path.read_bytes()
  if hashlib.sha256(debug_bytes).hexdigest()!=metadata.get('public_debug_log_sha256'):
   raise ValueError('Public diagnostic log hash mismatch: '+record['attempt_id'])
  metadata['public_debug_view_path']=diagnostic_view_page(out,public_debug,t('saved_diagnostic_logs', 'Saved diagnostic logs'),metadata.get('public_log_note') or t('contains_only_observed_logs', 'Contains only observed logs.'))
  stderr_total=len(debug_bytes);any_stderr=bool(debug_bytes)
  any_truncated=bool(metadata.get('diagnostic_log_truncated'))
  complete_label=t('saved_file_truncated_see_native_log_coverage', 'Saved file truncated; see native log coverage notes') if any_truncated else t('saved_file_not_truncated_see_native_log', 'Saved file not truncated; see native log coverage notes')
  native_text,caller_text=_separate_observed_logs(debug_bytes.decode("utf-8", "replace"))
  native_view=('<pre data-log-source="product">'+esc(_reader_log_excerpt(native_text)[:6000])+'</pre>' if native_text else ('<p data-log-unavailable>' + t('no_native_product_or_dependency_logs_were', 'No native product or dependency logs were obtained for this call. Saved caller records appear below.') + '</p>'))
  diagnostics=[('<article><h3>' + t('observed_diagnostic_logs_products_and_dependencies', 'Observed diagnostic logs · products and dependencies') + f"</h3><p>{esc(metadata.get('public_log_note') or t('only_observed_records_are_shown_unavailable_internal', 'Only observed records are shown; unavailable internal steps are not inferred.'))}</p><p>" + t('returned_json_is_shown_separately_under_raw', 'Returned JSON is shown separately under raw responses. Adapter records appear below under caller events.') + f'</p>{native_view}<details><summary>' + t('caller_events_test_program_records', 'Caller events (test program records)') + f"""</summary><pre data-log-source="caller">{esc(_reader_log_excerpt(caller_text)[:6000])}</pre></details>{_log_evidence_dialog(public_debug, model_text(*pair('complete_diagnostic_log', 'Complete diagnostic log')))}<p><a href="../{esc(public_debug)}" download>""" + t('download_runtime_logs_returned_content_separated', 'Download runtime logs (returned content separated)') + '</a> ' + t('public_sha_256', '· Public SHA-256:') + f"""<code class="break">{esc(metadata['public_debug_log_sha256'])}</code> """ + t('original_capture_bytes_may_include_protocol_responses', '· Original capture: {value:,} bytes (may include protocol responses) · Original SHA-256:', value=int(metadata.get('diagnostic_log_bytes') or 0)) + f"""<code class="break">{esc(metadata.get('diagnostic_log_sha256') or '—')}</code></p></article>""")]
 version=method_version(record,method_provenance(out)) or '—'
 command=metadata.get('command_args') or metadata.get('command') or t('the_call_record_did_not_retain_a', '— (the call record did not retain a complete displayable command)')
 if isinstance(command,list):command=' '.join(str(item) for item in command)
 status=t('succeeded', 'Succeeded') if record.get('ok') else t('failed', 'Failed')
 result_note=(t('this_call_produced_output_that_could_be', 'This call produced output that could be scored.') if record.get('ok') else
              t('this_call_did_not_produce_output_that', 'This call did not produce output that could pass scoring.'))
 top_log_size=(f"{stderr_total:,} 字节{(t('at_least_one_is_truncated', ' (at least one is truncated)') if any_truncated else '')}"
               if any_stderr else t('0_bytes', '0 bytes'))
 fields=(('<dt>' + t('call_status', 'Call status') + f'</dt><dd>{status}</dd><dt>' + t('elapsed_time', 'Elapsed time') + f"</dt><dd>{ms(record.get('elapsed_ms'))}</dd><dt>" + t('exit_code', 'Exit code') + f"</dt><dd>{esc(process.get('exit_code', metadata.get('exit_code', '—')))}</dd><dt>" + t('raw_output', 'Raw output') + '</dt><dd>' + t('bytes', '{output_bytes:,} bytes', output_bytes=output_bytes) + '</dd><dt>' + t('run_logs', 'Run logs') + f'</dt><dd>{top_log_size}</dd><dt>' + t('complete_log_retained', 'Complete log retained') + f'</dt><dd>{complete_label}</dd><dt>' + t('run_id', 'Run ID') + f"</dt><dd>{esc(record.get('run_id') or execution_id(record) or '—')}</dd><dt>" + t('tool_version', 'Tool version') + f'</dt><dd>{esc(version)}</dd>'))
 full_fields=(('<dt>' + t('started_at', 'Started at') + f"</dt><dd>{esc(record.get('started_at') or timing.get('started_at') or '—')}</dd><dt>" + t('finished_at', 'Finished at') + f"</dt><dd>{esc(record.get('completed_at') or timing.get('completed_at') or '—')}</dd><dt>" + t('log_level', 'Log level') + f"</dt><dd>{esc(metadata.get('log_level') or (t('observed_diagnostics_saved_see_native_log_coverage', 'Observed diagnostics saved (see native log coverage notes)') if public_debug else t('stderr_retained_level_not_recorded', 'stderr retained (level not recorded)') if any_stderr else t('debug_logging_disabled', 'Debug logging disabled')))}</dd><dt>" + t('public_log', 'Public log') + f'</dt><dd>{top_log_size}</dd><dt>' + t('public_log_redacted', 'Public log redacted') + f"</dt><dd>{(t('credentials_and_local_paths_hidden', 'Credentials and local paths hidden') if public_debug else t('no_2', 'No'))}</dd><dt>" + t('method_sha_256', 'Method SHA-256') + f"</dt><dd>{esc(metadata.get('binary_sha256') or '—')}</dd><dt>" + t('official_command', 'Official command') + f'</dt><dd>{esc(command)}</dd>'))
 runtime_error=''
 if not record.get('ok') or record.get('error_type'):
  runtime_error=('<div class="runtime-error"><h3>' + t('program_error', 'Program error') + '</h3><p>' + t('classification', 'Classification: ') + f"<code>{esc(record.get('error_type') or t('not_recorded', 'Not recorded'))}</code></p><pre>{esc(record.get('error_message') or t('the_call_record_provides_no_error_text', 'The call record provides no error text.'))}</pre></div>")
 diagnostic=''.join(diagnostics) if diagnostics else ('<p class="source-note">' + t('this_historical_call_retained_no_displayable_stderr', 'This historical call retained no displayable stderr/debug log; no missing log is reconstructed.') + '</p>')
 raw_record=' · '.join(f'<a href="../{esc(log["path"])}" download>下载{esc(log["label"])}记录</a>' for log in logs)
 if artifact_relative:
  if raw_record:raw_record+=' · '
  _answer_view=metadata.get('publication_views',{}).get('scored_answer')
  if _answer_view and _answer_view['original_sha256']!=_answer_view['published_sha256']:
   raw_record+=f'<a href="../{esc(_answer_view["path"])}" download>'+model_text(*pair('download_public_output_view', 'Download public output view'))+'</a>'
  else:
   raw_record+=(f'<a href="../{esc(artifact_relative)}" download>' + t('download_the_raw_output_retained_for_this', 'Download the raw output retained for this call') + '</a>')
 raw_record+=native_public_link
 public_response=metadata.get('public_response_path')
 if public_response:
  response_view=diagnostic_view_page(out,public_response,t('complete_original_response', 'Complete original response'),t('actual_product_response_credentials_and_local_paths', 'Actual product response; credentials and local paths are hidden in this public copy.'))
  if raw_record:raw_record+=' · '
  raw_record+=(f'<a href="../{esc(response_view)}" target="_blank">' + t('view_complete_api_response_credentials_and_local', 'View complete API response (credentials and local paths hidden)') + '</a>')
 if metadata.get("public_log_note"):
  raw_record+='<p class="source-note">'+esc(metadata["public_log_note"])+'</p>'
 public_debug=metadata.get('public_debug_log_path')
 if public_debug:
  if raw_record:raw_record+=' · '
  raw_record+=(f"""<a href="../{esc(metadata.get('public_debug_view_path') or public_debug)}" target="_blank">""" + t('view_saved_diagnostic_logs_credentials_and_local', 'View saved diagnostic logs (credentials and local paths hidden)') + '</a>')
 diagnostic_path=metadata.get('diagnostic_log_path')
 if metadata.get('local_report_complete_diagnostic_links') and diagnostic_path:
  if raw_record:raw_record+=' · '
  diagnostic_size=int(metadata.get('diagnostic_log_bytes') or 0)
  diagnostic_sha=metadata.get('diagnostic_log_sha256') or t('not_registered', 'Not registered')
  diagnostic_truncated=t('yes', 'Yes') if metadata.get('diagnostic_log_truncated') else t('no_2', 'No')
  raw_record+=((f'<a href="../../{esc(diagnostic_path)}" download>' + t('download_the_complete_raw_debug_log', 'Download the complete raw debug log') + f'</a>（{diagnostic_size:,} 字节 · SHA-256：<code class="break">{esc(diagnostic_sha)}</code> · 截断：{diagnostic_truncated}）'))
 if not raw_record:raw_record=t('this_historical_call_has_no_downloadable_raw', 'This historical call has no downloadable raw record')
 evidence_identity_note=(model_text(*pair('scoring_originals_remain_private_public_downloads_hide', 'Scoring originals remain private; public downloads hide paths or sensitive values and bind original/public identities separately.')) if any(view.get('redactions') for view in metadata.get('publication_views',{}).values()) else t('raw_output_keeps_the_bytes_saved_after', 'Raw output keeps the bytes saved after the call.'))
 if record.get('scoring',{}).get('mode')=='published_snapshot_rescore':
  evidence_identity_note=t('this_run_only_rescored_call_logs_keep', 'This run only rescored. Call logs keep the original call details; this scoring input and its raw and public hashes are listed under the actual output.')
 return (('<section class="section" id="run-log"><div class="wrap"><h2>' + t('run_logs_and_evidence', 'Run logs and evidence') + f'</h2><dl class="diagnostic-fields">{fields}</dl>{runtime_error}<details class="technical-details"><summary>' + t('view_complete_run_fields', 'View complete run fields') + f'</summary><dl class="diagnostic-fields">{full_fields}</dl></details><p class="log-summary"><b>' + t('result_note', 'Result note: ') + f'</b>{result_note}</p><details class="diagnostic-log"><summary>' + t('view_this_run_s_real_log', "View this run's real log") + f'</summary><div class="diagnostic-log-body">{diagnostic}</div></details></div></section><section class="section"><div class="wrap"><h2>' + t('original_run_evidence', 'Original run evidence') + '</h2><p>' + t('the_run_record_keeps_request_status_latency', '{raw_record}. The run record keeps request status, latency and evidence paths; {evidence_identity_note}', raw_record=raw_record, evidence_identity_note=evidence_identity_note) + '</p></div></section>'))
def pct(n):return '—' if n is None else f'{n*100:.1f}%'
def ms(n):return '—' if n is None else f'{n:,.0f} ms'
def link(url,label,english=None):return f'<a href="{esc(safe_href(url))}">{model_text(label,english)}</a>'
def cell(value,sort=None):return '<td'+(f' data-sort="{sort}"' if sort is not None else '')+f'>{value}</td>'

def model_text(value,english=None):
 return esc(value) if english is None else '<span data-report-en="'+esc(english)+'">'+esc(value)+'</span>'
def rate(a,b):return pct(a/b) if b else '—'
def section(title,body,id=''):return '<section class="section"'+(f' id="{id}"' if id else '')+f'><div class="wrap"><h2>{title}</h2>{body}</div></section>'
def hero(title,desc):
 banner=_RENDER_CONTEXT.get().banner
 label=t('synthetic_example', 'Synthetic example') if t('synthetic_example_not_a_formal_benchmark', 'Synthetic example · not a formal benchmark') in banner else t('diagnostic_run', 'Diagnostic run') if t('diagnostic_run_not_a_formal_benchmark', 'Diagnostic run · not a formal benchmark') in banner else t('measured_report', 'Measured report')
 return f'<section class="hero"><div class="wrap"><div class="eyebrow">Web Fetch · {label}</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'
def comparative_rows(rows, directions):
 """Mark raw-value column extrema without ranking unavailable values or ties."""
 matches=[list(re.finditer(r'<td\b[^>]*>.*?</td>',values,re.S)) for _,values in rows]
 numbers={}
 for index,(metric,direction) in directions.items():
  values=[]
  for cells in matches:
   match=re.search(r'data-sort="([^"]+)"',cells[index].group())
   value=float(match.group(1)) if match else None
   values.append(value if value is not None and value>=0 else None)
  valid=[v for v in values if v is not None]
  extreme=(max(valid) if direction=='max' else min(valid)) if valid else None
  numbers[index]=(values,extreme,valid.count(extreme)==1)
 result=[]
 for row_number,((attrs,values),cells) in enumerate(zip(rows,matches,strict=True)):
  for index in sorted(directions,reverse=True):
   metric,direction=directions[index];column,extreme,unique=numbers[index]
   fragment=cells[index].group();opening,body=fragment.split('>',1)
   if column[row_number] is None:opening=re.sub(r' data-sort="[^"]+"','',opening)
   opening+=' data-metric="'+metric+'" data-metric-direction="'+direction+'"'
   content=body[:-5]
   if unique and column[row_number] is not None and column[row_number]==extreme:
    content='<strong class="comparison-winner good">'+content+'</strong>'
   values=values[:cells[index].start()]+opening+'>'+content+'</td>'+values[cells[index].end():]
  result.append((attrs,values))
 return result


def table(headers,rows,id='',sortable=False,cls='',metrics=None,index_header=t('no', 'No.'),default_sort=None,include_index=True):
 head=(f'<th>{esc(index_header)}</th>' if include_index else '')+''.join(f'<th><button class="sort-button" onclick="sortTable(\'{id}\',{i+int(include_index)})">{h} ↕</button></th>' if sortable else f'<th>{h}</th>' for i,h in enumerate(headers))
 body=''.join(f'<tr{attrs}>'+ (f'<td>{i+1}</td>' if include_index else '')+f'{values}</tr>' for i,(attrs,values) in enumerate(rows))
 sort_attrs=(f' data-col="{default_sort[0]}" data-dir="{default_sort[1]}"' if default_sort else '')
 if not include_index:sort_attrs+=' data-row-index="false"'
 return metric_legend(headers if metrics is None else metrics)+f'<div class="table-wrap"><table class="{cls}"'+(f' id="{id}"' if id else '')+sort_attrs+f'><thead><tr>{head}</tr></thead><tbody'+(' id="rows"' if id=='filtered-table' else '')+f'>{body}</tbody></table></div>'
@dataclass(frozen=True)
class RenderContext:
 banner: str = ''
 english_generated: bool = False
 score_audit: dict | None = None


_RENDER_CONTEXT=ContextVar('webfetch_render_context',default=RenderContext())


def _scoped_render(function):
 @wraps(function)
 def render(*args,**kwargs):
  token=_RENDER_CONTEXT.set(RenderContext())
  try:return function(*args,**kwargs)
  finally:_RENDER_CONTEXT.reset(token)
 return render

def write(out,path,title,body,*,root_links=False):
 depth=len(Path(path).parts)-1;base='../'*depth
 document_base='<base href="'+(base or './')+'">' if root_links else ''
 if root_links:base=''
 nav=[('datasets/index.html',t('dataset', 'Dataset')),('methodology/index.html',t('methodology', 'Methodology')),('analysis/index.html',t('analysis', 'Analysis')),('review/index.html',t('review_record', 'Review record')),('guide/index.html',t('interpretation', 'Interpretation')),('adoption/index.html',t('methods', 'Methods')),('reproduce/index.html',t('reproduction', 'Reproduction')),('changes/index.html',t('changelog', 'Changelog')),('accounts/index.html',t('access_setup', 'Access & setup'))]
 brand=link(base+'index.html','Web Fetch')
 context=_RENDER_CONTEXT.get()
 if context.score_audit and path.startswith('attempts/'):
  identifier=Path(path).stem
  reviewed=next((item for item in context.score_audit['changes'] if item['attempt_id']==identifier),None)
  if reviewed:
   body+=section(t('acceptance_basis', 'Acceptance basis'),('<div data-score-audit><dl class="diagnostic-fields"><dt>' + t('acceptance_requirement', 'Acceptance requirement') + f"</dt><dd>{esc(reviewed['requirement'])}</dd><dt>" + t('actual_output', 'Actual output') + f"</dt><dd>{esc(reviewed['actual'])}</dd></dl><p>{link('../audit/index.html#' + identifier, t('view_sources_and_judgment_basis', 'View sources and judgment basis'))}</p></div>"))
 counterpart=(base+'en/'+path if context.english_generated else base+'en/index.html')
 language=f'<a href="{safe_href(counterpart)}" hreflang="en">English</a>'
 footer=t('all_scores_are_computed_from_the_recorded', 'All scores are computed from the recorded calls. Web pages and services may change; conclusions apply only to this sample and configuration.')
 evidence_attr=' data-evidence-state="published_snapshot_score_audit"' if context.score_audit else ''
 if context.score_audit:footer=link(base+'methodology/index.html',t('methodology', 'Methodology'))+' · '+link(base+'changes/index.html',t('version_records', 'Version records'))
 banner=context.banner.replace('href="audit/index.html"','href="'+base+'audit/index.html"')
 page=(f'''<!doctype html><html lang="zh-CN"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{document_base}<title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css?v={hashlib.sha256((STYLE + EXTRA).encode()).hexdigest()[:16]}"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{''.join((link(base + url, label) for url, label in nav))}{language}<button class="theme-toggle" onclick="theme()">''' + t('toggle_theme', 'Toggle theme') + f'</button></nav></header><main{evidence_attr}>{banner}{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>')
 target=contained_path(out,path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

_RESEARCH_ROUTES=[
 ('datasets/index.html',t('dataset', 'Dataset'),'Datasets'),
 ('methodology/index.html',t('methodology', 'Methodology'),'Methodology'),
 ('analysis/index.html',t('research_findings', 'Research findings'),'Research findings'),
 ('review/index.html',t('review_record', 'Review record'),'Verification'),
 ('guide/index.html',t('interpretation', 'Interpretation'),'Guide'),
 ('adoption/index.html',t('adoption', 'Adoption'),'Adoption'),
 ('reproduce/index.html',t('reproduction', 'Reproduction'),'Reproduction'),
 ('changes/index.html',t('changelog', 'Changelog'),'Changes'),
 ('accounts/index.html',t('access_setup', 'Access & setup'),'Accounts and setup'),
]

@_scoped_render
def generate_cohort_diagnostic(out, model):
 """Render live cohort diagnostics through the maintained WebFetch components."""
 token=_RENDER_CONTEXT.set(RenderContext(('<div class="wrap callout"><strong>' + t('live_page_diagnostic', 'Live page diagnostic') + f"</strong><p>{esc(model['scope_note'])}</p></div>")))
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA+asset_text('cohort-diagnostic.css'),encoding='utf-8')
 (out/'assets/report.js').write_text(JS+asset_text('cohort-diagnostic.js'),encoding='utf-8')
 groups={g['id']:g for g in model['cohorts']};rows=model['rows'];labels=model['cause_labels']
 files={r[field]['path']:(out/r[field]['path']).read_bytes() for r in rows for field in ('log','reference')}
 findings={r['id']:diagnostic_findings(r,files) for r in rows}
 failure_types=diagnostic_failure_counts(model,files)
 def page(route,title,body):write(out,route,title,body,root_links=True)
 def detail_link(row):return 'attempts/'+row['id']+'.html'
 def blocks(name):
  body=''
  for block in model.get('pages',{}).get(name,[]):
   text=''.join('<p>'+esc(p)+'</p>' for p in block['paragraphs'])
   if block.get('case_ids'):
    text+='<p class="actions">'+''.join(link('attempts/'+cid+'.html',cid+' →') for cid in block['case_ids'])+'</p>'
   body+=section(esc(block['title']),text)
  return body
 def distribution(counts):
  return '<figure class="editorial-figure">'+''.join('<div class="editorial-bar-row"><span>'+esc(label)+'</span><div class="editorial-bar-track"><i style="width:'+str(100*n/population)+'%"></i></div><b>'+str(n)+' / '+str(population)+'</b></div>' for label,n,population in counts)+'</figure>'
 try:
  home=hero(esc(model['title']),esc(model['subtitle']))
  overall=diagnostic_call_summary(rows,files);n=overall['total']
  versions={g.get('version') or t('see_the_call_record', 'See the call record') for g in groups.values()}
  values=cell(t('all_tasks', 'All tasks'))+cell(esc(next(iter(versions)) if len(versions)==1 else t('several_versions_see_groups', 'Several versions; see groups')))+cell(str(n),n)
  for key in ('delivered','accepted'):
   value=overall[key]
   values+=cell('—' if value is None else f'{value} / {n}（{100*value/n:.1f}%）',-1 if value is None else value)
  values+=''.join(cell('—' if overall[k] is None else f'{overall[k]/1000:.2f}',-1 if overall[k] is None else overall[k]) for k in ('mean_ms','p50_ms','p95_ms'))
  overall_table=table([t('dataset', 'Dataset'),t('implementation_version', 'Implementation version'),t('tasks', 'Tasks'),t('call_success', 'Call success'),t('pass', 'Pass'),t('mean_latency_s', 'Mean latency (s)'),t('p50_latency_s', 'P50 latency (s)'),t('p95_latency_s', 'P95 latency (s)')],[(' data-summary-overall="true"',values)],'overall-summary')
  overall_section=section(t('overall_evaluation_results', 'Overall evaluation results'),(f'{overall_table}<p>' + t('all_planned_tasks_form_one_evaluation_set', 'All planned tasks form one evaluation set. A successful call does not mean a passed task; latency merges all calls including failures and does not average per-group percentiles. Latency merged across execution routes only describes this evaluation set and does not rank routes.') + '</p>'),'overall-results')
  home+=overall_section
  summary_rows=[]
  for s in diagnostic_cohort_summary(model,files):
   g=groups[s['id']];n=s['total']
   values=cell(esc(g['label']))+cell(esc(g.get('version') or t('see_the_call_record', 'See the call record')))+cell(str(n),n)+cell('—' if s['delivered'] is None else str(s['delivered'])+' / '+str(n),-1 if s['delivered'] is None else s['delivered'])+cell(str(s['accepted'])+' / '+str(n),s['accepted'])
   values+=''.join(cell('—' if s[k] is None else f'{s[k]/1000:.2f}',-1 if s[k] is None else s[k]) for k in ('mean_ms','p50_ms','p95_ms'))
   summary_rows.append((' data-summary-group="'+esc(s['id'])+'"',values))
  home+=section(t('results_by_task_family', 'Results by task family'),(f"{table([t('task_group', 'Task group'), t('implementation_version', 'Implementation version'), t('tasks', 'Tasks'), t('call_success', 'Call success'), t('pass', 'Pass'), t('mean_latency_s', 'Mean latency (s)'), t('p50_latency_s', 'P50 latency (s)'), t('p95_latency_s', 'P95 latency (s)')], summary_rows, 'cohort-summary', sortable=True)}{distribution([(groups[g]['label'], *model['metrics'][g]) for g in groups])}<p>" + t('a_successful_call_only_means_the_request', 'A successful call only means the request delivered; a task pass also requires the body, text and structure rules. All three latency figures cover every call in the group; P50 is the median and P95 the 95th percentile. Groups without complete timing show —.') + f"</p><p>{esc(model['metric_note'])}</p>"),'cohort-results')
  failures=Counter(r['cause'] for r in rows if not r['initial_pass'])
  home+=blocks('overview')
  home+=section(t('what_failed_outputs_mostly_miss', 'What failed outputs mostly miss'),(f'{distribution([(FAILURE_TYPES[k], n, sum(failures.values())) for k, n in failure_types.most_common(3)])}<p>' + t('the_denominator_is_all_first_round_failed', 'The denominator is all {count} first-round failed tasks. One task can miss both text and records, so the counts do not add up. {link}', count=str(sum(failures.values())), link=link('analysis/index.html', t('view_failure_types_and_specific_samples', 'View failure types and specific samples →'))) + '</p>'))
  audit=model['success_audit'];counts=audit['source_state_counts']
  home+=section(t('passes_are_also_checked_against_the_source', 'Passes are also checked against the source page'),(f"{distribution([(model['source_labels'][k], n, audit['audited_passes']) for k, n in counts.items()])}<p>" + t('passing_results_have_three_way_check_records', '{audited_passes} passing results have three-way check records; comparable content and access barriers on the current page are recorded separately. {link}', audited_passes=str(audit['audited_passes']), link=link('review/index.html', t('check_scope_and_conclusions', 'Check scope and conclusions →'))) + '</p>'))
  home+=section(t('where_to_read_next', 'Where to read next'),'<div class="guide-grid">'+''.join('<article class="guide-card"><h3>'+link(route,title)+'</h3><p>'+text+'</p></article>' for route,title,text in [('datasets/index.html',t('dataset', 'Dataset'),t('view_samples_tasks_and_independent_reference_requirements', 'View samples, tasks and independent reference requirements.')),('analysis/index.html',t('analysis', 'Analysis'),t('filter_passes_failures_and_attribution_and_open', 'Filter passes, failures and attribution, and open complete evidence.')),('guide/index.html',t('interpretation', 'Interpretation'),t('understand_access_challenges_extraction_omissions_and_evaluation', 'Understand access challenges, extraction omissions and evaluation revisions.'))])+'</div>')
  page('index.html',model['title'],home)
  controls=('<div class="filters"><label>' + t('group', 'Group') + '<select data-filter="cohort"><option value="">' + t('all', 'All') + f"""</option>{''.join(('<option value="' + esc(k) + '">' + esc(g['label']) + '</option>' for k, g in groups.items()))}</select></label><label>""" + t('result', 'Result') + '<select data-filter="result"><option value="fail">' + t('fail_2', 'Fail') + '</option><option value="pass">' + t('pass_2', 'Pass') + '</option><option value="">' + t('all', 'All') + '</option></select></label><label>' + t('failure_type', 'Failure type') + '<select data-filter="issue"><option value="">' + t('all', 'All') + f"""</option>{''.join(('<option value="' + esc(k) + '">' + esc(FAILURE_TYPES[k]) + '</option>' for k in failure_types))}</select></label><label>""" + t('check_leads', 'Check leads') + '<select data-filter="cause"><option value="">' + t('all', 'All') + f"""</option>{''.join(('<option value="' + esc(k) + '">' + esc(v) + '</option>' for k, v in labels.items()))}</select></label><label>""" + t('search', 'Search') + '<input data-filter="query" aria-label="搜索样本" placeholder="编号或网址"></label></div><p id="count" role="status"></p>')
  inventory=[]
  for r in rows:
   kinds=sorted({f['kind'] for f in findings[r['id']]})
   attrs=' data-cohort="'+esc(r['cohort'])+'" data-result="'+('pass' if r['initial_pass'] else 'fail')+'" data-cause="'+esc(r['cause'])+'" data-issue="'+' '.join(kinds)+'" data-search="'+esc(r['url'])+'"'
   values=cell(link(detail_link(r),urlsplit(r['url']).hostname or r['id'])+'<br><small>'+esc(r['id'])+'</small>')+cell(esc(groups[r['cohort']]['label']))+cell(t('pass_2', 'Pass') if r['initial_pass'] else t('fail_2', 'Fail'))+cell(esc('；'.join(FAILURE_TYPES[k] for k in kinds) or '—'))+cell(esc(labels[r['cause']]))+cell(link(detail_link(r),t('requirement_actual_and_checks', 'Requirement, actual and checks →')))
   inventory.append((attrs,values))
  type_rows=[]
  for kind,n in failure_types.most_common():
   example=next(r for r in rows if any(f['kind']==kind for f in findings[r['id']]))
   type_rows.append((' data-failure-type="'+kind+'" data-count="'+str(n)+'"',cell(link('analysis/index.html#issue='+kind,FAILURE_TYPES[kind]))+cell(str(n)+' / '+str(sum(failures.values())))+cell(link(detail_link(example),urlsplit(example['url']).hostname or example['id']))))
  analysis=hero(str(len(rows))+' 例结果分析',t('start_with_the_requirements_outputs_violated_then', 'Start with the requirements outputs violated, then see how far independent checks can explain them.'))
  analysis+=blocks('analysis')
  analysis+=section(t('failure_types_and_counts', 'Failure types and counts'),(f"{table([t('requirements_unmet_in_the_first_round', 'Requirements unmet in the first round'), t('affected_tasks', 'Affected tasks'), t('sample_evidence', 'Sample evidence')], type_rows)}<p>" + t('counted_from_the_saved_first_round_scores', 'Counted from the saved first-round scores; one task can involve several failures, so counts do not add up. Select a type to filter all related tasks.') + '</p>'),'failure-types')
  analysis+=section(t('what_independent_checks_found', 'What independent checks found'),(f"{table([t('check_leads', 'Check leads'), t('failed_tasks', 'Failed tasks')], [('', cell(esc(labels[k])) + cell(str(n))) for k, n in failures.items()])}<p>" + t('these_leads_are_counted_separately_from_the', 'These leads are counted separately from the output defects above. An independent browser also being blocked only proves this check hit an access barrier; a later fetch passing does not prove the internal cause of the first-round failure.') + '</p>'))
  analysis+=section('逐项结果',controls+table([t('site_and_sample', 'Site and sample'),t('group', 'Group'),t('first_round_result', 'First-round result'),t('failure_type', 'Failure type'),t('check_leads', 'Check leads'),'证据'],inventory,'filtered-table',sortable=True),'failure-inventory')
  page('analysis/index.html',t('analysis', 'Analysis'),analysis)
  guide=hero(t('extraction_results_for_these_pages', 'Extraction results for these pages'),(f"{str(model['metrics']['overall'][0])} / {str(len(rows))} " + t('first_calls_met_the_full_acceptance_requirements', 'first calls met the full acceptance requirements of the time; the diagnostics below cover the designated sample.')))
  guide+=overall_section+section(t('results_and_measurement_scope', 'Results and measurement scope'),(f"{table([t('task_group', 'Task group'), t('implementation_version', 'Implementation version'), t('tasks', 'Tasks'), t('call_success', 'Call success'), t('pass', 'Pass'), t('mean_latency_s', 'Mean latency (s)'), t('p50_latency_s', 'P50 latency (s)'), t('p95_latency_s', 'P95 latency (s)')], summary_rows, 'guide-summary', sortable=True)}<p>" + t('latency_covers_all_calls_in_each_group', '{metric_note} Latency covers all calls in each group including failures; P50 is the median. Groups without complete timing are not computed.', metric_note=esc(model['metric_note'])) + '</p>'),'guide-results')
  guide+=section(t('requirements_failures_violate_most_often', 'Requirements failures violate most often'),(f"{table([t('output_defects', 'Output defects'), t('affected_failed_tasks', 'Affected failed tasks')], [('', cell(link('analysis/index.html#issue=' + k, FAILURE_TYPES[k])) + cell(str(n) + ' / ' + str(sum(failures.values())))) for k, n in failure_types.most_common()])}<p>" + t('one_task_can_involve_several_defects_so', "One task can involve several defects, so counts do not add up. Missing text is a fact about output against acceptance, not a finding about the tested tool's internal fault.") + '</p>'))
  guide+=section(t('failure_classes_after_checking', 'Failure classes after checking'),(f"{table([t('check_conclusion', 'Check conclusion'), t('failed_tasks', 'Failed tasks'), t('sample_evidence', 'Sample evidence')], [('', cell(esc(labels[k])) + cell(str(n) + ' / ' + str(sum(failures.values()))) + cell(link(detail_link(next((r for r in rows if not r['initial_pass'] and r['cause'] == k))), t('view_specific_requirements_output_and_source_page', 'View specific requirements, output and source page')))) for k, n in failures.most_common()])}<p>" + t('each_failed_task_belongs_to_exactly_one', 'Each failed task belongs to exactly one class, and the classes sum to all failed tasks. Access restrictions, reference-scope mismatches and output omissions are handled separately; an inaccessible current source page is not evidence of an engine fault.') + '</p>'),'guide-reviewed-causes')
  examples=model.get('interpretation_case_ids')
  if examples is None:
   examples=[next(r['id'] for r in rows if not r['initial_pass'])] if failures else []
   if any(r['initial_pass'] for r in rows):examples.append(next(r['id'] for r in rows if r['initial_pass']))
  for cid in examples:
   r=next(r for r in rows if r['id']==cid)
   panels=''
   for title,field in [(t('acceptance_requirement', 'Acceptance requirement'),'reference'),(t('first_round_actual_output', 'First-round actual output'),'first_answer'),(t('independent_source_page_observation', 'Independent source-page observation'),'source')]:
    binding=r.get(field)
    specimen_files={r[f]['path']:(out/r[f]['path']).read_bytes() for f in ('reference','first_answer','source') if r.get(f)}
    excerpt=diagnostic_specimen_excerpt(r,field,specimen_files,model)
    empty_note=('<p>' + t('output_is_empty_0_bytes', 'Output is empty (0 bytes).') + '</p>') if field=='first_answer' and not excerpt else ''
    panels+='<div data-specimen-field="'+field+'"'+(' data-sha256="'+binding['sha256']+'"' if binding else '')+'><h3>'+title+'</h3>'+empty_note+'<pre>'+esc(excerpt)+'</pre><p>'+esc(binding.get('scope','') if binding else t('independent_observation_did_not_obtain_target_body', 'Independent observation did not obtain target body text'))+'</p></div>'
   guide+=section((urlsplit(r['url']).hostname or cid)+'：'+(t('pass_2', 'Pass') if r['initial_pass'] else t('fail_2', 'Fail')),'<div data-interpretation-case="'+esc(cid)+'"><p>'+esc(r['task'])+'</p><div class="diagnostic-specimen">'+panels+'</div><p>'+esc(r['cause_detail'])+'</p><p>'+link(detail_link(r),t('view_the_complete_reference_output_live_page', 'View the complete reference, output, live page and logs →'))+'</p></div>')
  guide+=blocks('guide')
  guide+=section(t('conclusions_and_check_scope', 'Conclusions and check scope'),(f"<p>{esc(model['scope_note'])}</p><p>" + t('source_page_observations_and_output_checks_are', 'Source-page observations and output checks are kept separately. When an independent browser hits a verification page or access problem, this neither counts as a pass for the tested tool nor establishes that the tool is wrong. {link}', link=link('review/index.html', t('view_check_scope_and_handling', 'View check scope and handling →'))) + '</p>'),'guide-conclusions')
  page('guide/index.html',t('interpretation', 'Interpretation'),guide)
  dataset=[]
  for r in rows:
   dataset.append((' data-search="'+esc(r['url'])+'"',cell(link('datasets/'+r['id']+'.html',r['id']))+cell(esc(groups[r['cohort']]['label']))+cell(link(r['url'],t('actual_page', 'Actual page ↗')))+cell(esc(r['task']))))
   reference=(out/r['reference']['path']).read_text(encoding='utf-8')
   page('datasets/'+r['id']+'.html',(f"{r['id']} " + t('task_and_reference', ' · task and reference')),hero(esc(r['id']),esc(r['task']))+section(t('page_and_scope', 'Page and scope'),'<p>'+link(r['url'],r['url'])+'</p><p>'+esc(r['reference']['scope'])+'</p>')+section(t('independent_acceptance_requirements', 'Independent acceptance requirements'),('<details open><summary>' + t('view_the_reference_contract', 'View the reference contract') + f'</summary><pre>{esc(reference)}</pre></details>'))+section(t('result_entry', 'Result entry'),'<p>'+link(detail_link(r),t('view_this_task_s_output_and_three', "View this task's output and three-way check →"))+'</p>'))
  dataset_search=('<div class="filters"><label>' + t('search_tasks', 'Search tasks') + '<input data-filter="query" aria-label="' + t('search_tasks', 'Search tasks') + '" placeholder="编号、网址或任务"></label></div><p id="count" role="status"></p>')
  page('datasets/index.html',t('dataset', 'Dataset'),hero(t('web_task_set', 'Web task set'),t('each_group_keeps_its_own_sampling_and', 'Each group keeps its own sampling and acceptance scope; task pages show no measured scores.'))+blocks('datasets')+section(t('all_tasks', 'All tasks'),dataset_search+table([t('sample', 'Sample'),t('group', 'Group'),t('source_page', 'Source page'),t('task', 'Task')],dataset,'filtered-table',sortable=True)))
  for r in rows:
   dialogs=[]
   def evidence(target,title,body,kind,dialogs=dialogs):
    dialogs.append((f'<dialog class="call-log-dialog wmb-detail-dialog" id="{target}" aria-labelledby="{target}-title"><div class="call-log-shell"><header><h2 id="{target}-title">{esc(title)}</h2><button type="button" data-call-log-close aria-label="' + t('close', 'Close') + '">' + t('close', 'Close') + f'</button></header><div class="wmb-dialog-content" data-evidence-kind="{kind}">{body}</div></div></dialog>'))
    return '<button type="button" class="call-log-button" data-call-log-open="'+target+'" aria-haspopup="dialog">'+esc(title)+'</button>'
   actions=''
   for field,title in [('reference',t('reference_and_acceptance_contract', 'Reference and acceptance contract')),('first_answer',t('first_round_actual_output', 'First-round actual output')),('answer',t('diagnostic_output', 'Diagnostic output')),('source',t('actual_browser_page', 'Actual browser page')),('log',t('errors_and_run_records', 'Errors and run records'))]:
    if field=='answer' and r[field]==r['first_answer']:continue
    binding=r.get(field)
    if binding is None:continue
    raw=(out/binding['path']).read_text(encoding='utf-8')
    kind_attr=' data-kind="error"' if field=='log' else ''
    lazy=' data-evidence-file="'+esc(binding['path'])+'"' if len(raw)>32768 else ''
    contents='<p>'+link(binding['path'],t('download_complete_evidence', 'Download complete evidence'))+'</p><p>'+esc(binding.get('scope',t('saved_call_evidence', 'Saved call evidence')))+'</p><pre'+kind_attr+lazy+'>'+esc(t('reading_complete_evidence', 'Reading complete evidence…') if lazy else raw)+'</pre>'
    actions+=evidence('result-logs' if field=='log' else 'evidence-'+field,title,contents,field)
   summary=(f"""<div data-result-summary><h2>{(t('first_round_pass', 'First-round pass') if r['initial_pass'] else t('first_round_failure', 'First-round failure'))}</h2><p>{esc(labels[r['cause']])}</p><dl class="diagnostic-fields"><dt>""" + t('task_id', 'Task ID') + f"</dt><dd>{esc(r['id'])}</dd><dt>" + t('run_id', 'Run ID') + f"</dt><dd>{esc(r['run_id'])}</dd><dt>" + t('source_page_observation_time', 'Source-page observation time') + f"</dt><dd>{esc(r.get('source_checked_at') or r.get('success_audit', {}).get('source_checked_at') or t('see_the_independent_observation_record', 'See the independent observation record'))}</dd></dl></div>")
   body=hero(esc(r['id']),esc(r['task']))+section(t('result', 'Result'),summary)
   if findings[r['id']]:
    check_rows=[(' data-finding-kind="'+f['kind']+'"',cell(esc(f['label']))+cell(esc(f['expected']))+cell(esc(f['actual']))) for f in findings[r['id']]]
    body+=section(t('what_the_first_round_failed_to_meet', 'What the first round failed to meet'),(f"{table([t('failed_item', 'Failed item'), t('requirement', 'Requirement'), t('actual', 'Actual')], check_rows)}<p>" + t('this_table_explains_the_saved_first_round', 'This table explains the saved first-round score and does not substitute later fetched output. The complete source text and scoring record are in the evidence below.') + '</p>'),'failed-requirements')
   body+=section(t('independent_checks_and_handling', 'Independent checks and handling'),(f"<div data-failure-diagnosis><p>{esc(r['cause_detail'])}</p><p>" + t('handling_status', 'Handling status:') + f"{esc(r['followup_status'])}</p></div>"))
   if r.get('success_audit'):
    a=r['success_audit'];audit_rows=[]
    for title,status_key,actual_key,required_key in [(t('named_links', 'Named links'),'source_named_links_result','source_visible_named_links','source_required_named_links'),('表格行','source_table_result','source_scoped_table_rows','source_required_table_rows')]:
     status=a.get(status_key,'unavailable');status_label={'not_applicable':t('no_such_task_requirement', 'No such task requirement'),'meets':t('meets_the_count_requirement', 'Meets the count requirement'),'below':t('below_the_count_requirement', 'Below the count requirement'),'unavailable':t('check_count_not_obtained', 'Check count not obtained')}.get(status,status)
     audit_rows.append(('',cell(title)+cell(esc(status_label))+cell(str(a.get(actual_key)) if a.get(actual_key) is not None else '—')+cell(str(a.get(required_key)) if a.get(required_key) is not None else '—')))
    body+=section(t('three_way_check', 'Three-way check'),(f"<p>{esc(model['source_labels'][a['source_state']])}</p>{table([t('check', 'Check'), t('current_source_page_status', 'Current source-page status'), t('actual', 'Actual'), t('requirement', 'Requirement')], audit_rows)}<p>" + t('means_the_task_has_no_such_requirement', '— means the task has no such requirement or the count was not obtained; it does not count as a pass.') + '</p>'))
   if not r.get('source'):body+=section(t('source_page_observation', 'Source-page observation'),'<p>'+esc(r.get('source_error') or t('independent_browser_observation_obtained_no_downloadable_target', "Independent browser observation obtained no downloadable target body text; access status is in this task's check record."))+'</p>')
   body+=section(t('reference_output_and_live_page_evidence', 'Reference, output and live-page evidence'),'<div class="actions wmb-evidence-actions">'+actions+'</div><p>'+link('datasets/'+r['id']+'.html',t('task_and_reference_2', 'Task and reference →'))+' · '+link('analysis/index.html',t('back_to_result_analysis', 'Back to result analysis →'))+'</p>')+''.join(dialogs)
   page(detail_link(r),(f"{r['id']} " + t('result_2', ' · result')),'<div data-report-detail="'+('success' if r['initial_pass'] else 'failed')+'" data-task-id="'+esc(r['id'])+'">'+body+'</div>')
  for name,title in [('methodology',t('methodology', 'Methodology')),('review',t('review_record', 'Review record')),('reproduce',t('reproduction', 'Reproduction')),('changes',t('changelog', 'Changelog')),('adoption',t('methods', 'Methods')),('accounts',t('access_setup', 'Access & setup'))]:
   page(name+'/index.html',title,hero(title,esc(model.get('page_intros',{}).get(name,'')))+blocks(name))
  page('en/index.html','English edition','<div class="wrap"><h1>English edition not generated</h1><p>'+link('index.html','返回中文报告')+'</p></div>')
 finally:_RENDER_CONTEXT.reset(token)


def _research_links(urls):
 return '<br>'.join(link(url,url.replace('https://','')) for url in urls)

def _research_hero(title,desc,english=False):
 eyebrow=report_text('en' if english else 'zh')('web_fetch_research_report', 'Web Fetch · Research report')
 return f'<section class="hero"><div class="wrap"><div class="eyebrow">{eyebrow}</div><h1>{title}</h1><p class="lead">{desc}</p></div></section>'

def _research_write(out,path,title,body,english=False):
 local_path=('en/'+path) if english else path
 depth=len(Path(local_path).parts)-1;base='../'*depth
 nav=''.join(link(base+('en/' if english else '')+url,en if english else zh) for url,zh,en in _RESEARCH_ROUTES)
 brand=link(base+('en/index.html' if english else 'index.html'),'Web Fetch')
 counterpart=base+path if english else base+'en/'+path
 language=f'''<a href="{safe_href(counterpart)}" hreflang="{('zh-CN' if english else 'en')}">{('中文' if english else 'English')}</a>'''
 status=(report_text('en' if english else 'zh')('research_edition_0_measured_results', 'Research edition · 0 measured results'))
 footer=(report_text('en' if english else 'zh')('research_was_verified_from_public_papers_repositories', 'Research was verified from public papers, repositories, datasets and scorers. No benchmark result is claimed in this edition.'))
 page=f'''<!doctype html><html lang="{('en' if english else 'zh-CN')}"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{nav}{language}<button class="theme-toggle" onclick="theme()">{report_text('en' if english else 'zh')('toggle_theme', 'Theme')}</button></nav></header><main><div class="wrap reliability" role="status"><strong>{status}</strong></div>{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'''
 target=contained_path(out,local_path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')

def generate_public_benchmark_research(out:Path,research:dict,*,include_english:bool=False):
 """Render the research-only public benchmark report with no measured-result paths."""
 if research.get('status')!='research_only' or research.get('measured_results')!=0:
  raise ValueError('Public benchmark research must explicitly contain zero measured results')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA,encoding='utf-8')
 (out/'assets/report.js').write_text(JS,encoding='utf-8')
 release_history=localized_release_history(
  json.loads(((ROOT / 'research/report-changes.json')).read_text(encoding='utf-8')),
  'public_benchmark_research')
 (out/'report-changes.json').write_text(
  json.dumps({'schema_version':2,'releases':release_history},ensure_ascii=False,indent=2)+'\n',
  encoding='utf-8')
 direct=research['direct_benchmarks'];adjacent=research['adjacent_browser_benchmarks']
 for english in ((False,True) if include_english else (False,)):
  h=report_text('en' if english else 'zh')
  sample_size=research['sampling_contract']['size']
  parent_pages=next(x for x in direct if x['id']=='webmainbench-545')['pages']
  current_cases=sum(research['current_benchmark']['tracks'].values())
  def field(row,key,english=english):return row.get(key) if english else row.get(key+'_zh',row.get(key))
  def research_table(headers,rows,**kwargs):return table(headers,rows,index_header=h('no', 'No.'),**kwargs)
  artifact_prefix='../../' if english else '../'
  home=_research_hero(h('public_web_fetch_benchmark_research', 'Public Web Fetch benchmark research'),h('public_data_official_scorers_and_adoption_evidence', 'Public data, official scorers and adoption evidence are verified; this edition only defines the next measurement boundary.'),english)
  cards=''.join(f"""<article class="card"><h3>{esc(x['name'])}</h3><p><b>{x['pages']:,}</b> {h('pages_2', 'pages')}</p><p>{esc(field(x, 'scope'))}</p></article>""" for x in direct)
  qualification=research['decision']['qualification_en' if english else 'qualification_zh']
  home+=section(h('decision', 'Decision'),f"""<p class="callout success"><b>{h('selected', 'Selected: ')}</b>{esc(research['decision']['summary_en' if english else 'summary_zh'])}</p><p>{esc(qualification)}</p>""")
  home+=section(h('candidate_benchmarks', 'Candidate benchmarks'),'<div class="grid">'+cards+'</div>')
  home+=section(h('current_status', 'Current status'),f"""<div class="review-summary"><article class="card"><h3>0</h3><p>{h('measured_results', 'measured results')}</p></article><article class="card"><h3>{len(direct)}</h3><p>{h('direct_tracks', 'direct tracks')}</p></article><article class="card"><h3>{len(adjacent)}</h3><p>{h('adjacent_browser_benchmarks', 'adjacent browser benchmarks')}</p></article></div>""")
  _research_write(out,'index.html',h('public_benchmark_research', 'Public benchmark research'),home,english)

  rows=[]
  for x in direct:
   rows.append(('',cell(esc(x['name']))+cell(f'{x["pages"]:,}')+cell(esc(field(x,'metric')))+cell(esc(field(x,'aggregation')))+cell(esc(field(x,'maturity')))+cell(_research_links(x['sources']))))
  datasets=_research_hero(h('dataset', 'Datasets'),h('only_public_tracks_that_directly_measure_page', 'Only public tracks that directly measure page main-content or Markdown conversion are included.'),english)
  datasets+=section(h('primary_tracks', 'Primary tracks'),research_table([h('name', 'Name'),h('pages_3', 'Pages'),h('official_metric', 'Official metric'),h('aggregation', 'Aggregation'),h('maturity', 'Maturity'),h('sources', 'Sources')],rows,metrics=[]))
  decision_rows=[('',cell(esc(x['name']))+cell(esc(field(x,'disposition')))+cell(esc(x['license']))) for x in direct]
  datasets+=section(h('selection_result', 'Selection result'),research_table([h('candidate', 'Candidate'),h('disposition', 'Disposition'),h('license', 'License')],decision_rows,metrics=[]))
  subsets=next(x for x in direct if x['id']=='wceb')['subsets']
  datasets+=section('WCEB '+h('composition', 'composition'),research_table([h('subset', 'Subset'),h('pages_3', 'Pages')],[('',cell(esc(name))+cell(f'{count:,}')) for name,count in subsets],metrics=[])+f"<p>{h('total', 'Total')}: <b>{sum((x[1] for x in subsets)):,}</b></p>")
  wcxb=next(x for x in direct if x['id']=='wcxb')
  development_pages,test_pages=wcxb['repository_audit']['development_html'],wcxb['repository_audit']['test_html']
  datasets+=section('WCXB '+h('stratification', 'stratification'),f"<p>{h('the_development_split_has_pages_and_the', 'The development split has {development_pages:,} pages and the test split {test_pages:,}; HTML and ground truth were verified present for both.', development_pages=development_pages, test_pages=test_pages)}</p><p>{h('page_types', 'Page types: ')}"+esc(', '.join(wcxb['page_types']))+'</p>')
  frozen=research.get('frozen_sample')
  if frozen:
   distribution_rows=[]
   for axis in frozen['sampling']['official_axes']:
    parent_dist=frozen['parent_distribution'][axis];sample_dist=frozen['sample_distribution'][axis]
    for category in sorted(set(parent_dist)|set(sample_dist)):
     distribution_rows.append(('',cell(esc(axis))+cell(esc(category))+cell(str(parent_dist.get(category,0)))+cell(str(sample_dist.get(category,0)))))
   datasets+=section(h('frozen_case_distribution', 'Frozen {sample_size}-case distribution', sample_size=sample_size),f"<p>{h('parent_and_sample_counts_follow_rare_languages', 'Parent and sample counts follow. Rare languages have only one or two cases and do not support language-capability claims.')} {link(artifact_prefix + 'sample-manifest.json', h('full_manifest_json', 'Full manifest JSON →'))}</p>"+research_table([h('axis', 'Axis'),h('category', 'Category'),h('parent', 'Parent {parent_pages}', parent_pages=parent_pages),h('sample_2', 'Sample {sample_size}', sample_size=sample_size)],distribution_rows,metrics=[]))
  embedded=''.join(f'<article class="guide-card"><h3>{esc(x["name"])}</h3><p>{esc(field(x,"disposition"))}</p><p>{_research_links(x["sources"])}</p></article>' for x in research['embedded_or_supplementary'])
  datasets+=section(h('embedded_and_supplementary_sets', 'Embedded and supplementary sets'),'<div class="guide-grid">'+embedded+'</div>')
  _research_write(out,'datasets/index.html',h('dataset', 'Datasets'),datasets,english)

  method=_research_hero(h('methodology', 'Methodology'),h('preserve_each_benchmark_s_original_data_preprocessing', "Preserve each benchmark's original data, preprocessing, scorer and denominator."),english)
  contract=research['next_run_contract' if english else 'next_run_contract_zh']
  rules=''.join(f'<li><b>{i+1}</b><span>{esc(rule)}</span></li>' for i,rule in enumerate(contract))
  method+=section(h('frozen_contract', 'Frozen contract'),'<ol class="quality-steps">'+rules+'</ol>')
  sampling=research['sampling_contract']
  sample_text=(f'{sampling["size"]} · {esc(sampling["seed"])} · '+esc(sampling['reason' if english else 'reason_zh']))
  method+=section(h('case_sampling_contract', '{sample_size}-case sampling contract', sample_size=sample_size),f"<p><b>{sample_text}</b></p><p>{h('official_axes', 'Official axes: ')}<code>{esc(', '.join(sampling['official_axes']))}</code></p><p>{h('algorithm', 'Algorithm: ')}{esc(sampling['algorithm'])}</p>")
  no_combined=h('the_public_cases_use_webmainbench_545_s', "The public {sample_size} cases use WebMainBench-545's official components; the current {current_cases} cases continue to measure live access, resources, dynamic rendering and latency. Show both without a rescaled combined score.", sample_size=sample_size, current_cases=current_cases)
  method+=section(h('no_synthetic_combined_score', 'No synthetic combined score'),'<p>'+no_combined+'</p>')
  _research_write(out,'methodology/index.html',h('methodology', 'Methodology'),method,english)

  analysis=_research_hero(h('research_findings', 'Research findings'),h('direct_public_benchmarks_exist_but_none_jointly', 'Direct public benchmarks exist, but none jointly isolate live access success and extraction quality.'),english)
  analysis+=section(h('smallest_complete_design', 'Smallest complete design'),f"<p>{esc(research['decision']['summary_en' if english else 'summary_zh'])}</p><p>{h('the_public_set_freezes_html_and_answers', 'The public set freezes HTML and answers extraction/Markdown fidelity on identical input; the current {current_cases} cases continue to answer end-to-end URL-to-output capability.', current_cases=current_cases)}</p>")
  limitations='<ul>'+''.join('<li>'+esc(x)+'</li>' for x in wcxb['limitations' if english else 'limitations_zh'])+'</ul>'
  analysis+=section('WCXB '+h('audit_and_disposition', 'audit and disposition'),f'<p>{esc(field(wcxb,"disposition"))}</p>'+limitations)
  current=research['current_benchmark']
  track_text=' · '.join(f'{name} {count}' for name,count in current['tracks'].items())
  analysis+=section(h('relationship_to_the_current_cases', 'Relationship to the current {current_cases} cases', current_cases=current_cases),f'<p><b>{esc(track_text)}</b></p><p>{esc(field(current,"measures"))}</p><p>{esc(field(current,"difference"))}</p>')
  fusion_rows=[('',cell(esc(field(x,'layer')))+cell(esc(field(x,'keep')))) for x in research['fusion_plan']]
  analysis+=section(h('fuse_process_and_diagnostics_not_scores', 'Fuse process and diagnostics, not scores'),research_table([h('layer', 'Layer'),h('decision_2', 'Decision')],fusion_rows,metrics=[]))
  analysis+=section(h('what_stays_outside_the_main_leaderboard', 'What stays outside the main leaderboard'),f"<p>{h('webarena_mind2web_webvoyager_and_browsecomp_matter_for', 'WebArena, Mind2Web, WebVoyager and BrowseComp matter for browser/search systems, but score actions, task completion or research QA and cannot attribute differences to the fetch layer.')}</p>")
  _research_write(out,'analysis/index.html',h('research_findings', 'Research findings'),analysis,english)

  review=_research_hero(h('review_record', 'Verification'),h('this_verifies_sources_not_benchmark_results', 'This verifies sources, not benchmark results.'),english)
  audit=next(x for x in direct if x['id']=='wceb')['download_audit']
  review+=section(h('verified', 'Verified'),f"""<p>{h('checked', 'Checked')}: {esc(research['verified_at'])}</p><p>WCEB <code>{esc(audit['artifact'])}</code>: {audit['bytes']:,} bytes · SHA-256 <code class="break">{audit['sha256']}</code> · {audit['observed_pages']:,} {h('pages_2', 'pages')}</p><p>BrowseComp: 1,266 {h('data_rows_excluding_the_header', 'data rows excluding the header')}</p>""")
  repo_audit=wcxb['repository_audit']
  review+=section('WCXB '+h('full_repository_audit', 'full repository audit'),f"<p>dev: {repo_audit['development_html']:,} HTML / {repo_audit['development_ground_truth']:,} ground truth · test: {repo_audit['test_html']:,} HTML / {repo_audit['test_ground_truth']:,} ground truth</p><p>{h('empty_main_content', 'Empty main content')}: {repo_audit['empty_main_content']} · with mismatch: {esc(repo_audit['with_snippets_not_in_main_content'])} · without mismatch: {esc(repo_audit['without_snippets_present_in_main_content'])} · test result rows: {repo_audit['published_test_result_rows']}/{repo_audit['official_test_rows']}</p>")
  selected=next(x for x in direct if x['id']=='webmainbench-545')
  scorer_items=''.join('<li>'+esc(item)+'</li>' for item in selected['scorer_audit' if english else 'scorer_audit_zh'])
  review+=section('WebMainBench-545 '+h('scorer_audit', 'scorer audit'),'<ul>'+scorer_items+'</ul>')
  if frozen:
   qa=frozen['quality_audit']
   review+=section('WebMainBench-545 '+h('released_file_full_audit', 'released-file full audit'),f"""<p>{frozen['parent']['bytes']:,} bytes · SHA-256 <code class="break">{esc(frozen['parent']['sha256'])}</code> · {frozen['case_count']} {h('cases_frozen', 'cases frozen')}</p><p>groundtruth_content empty: {qa['empty_groundtruth_content']}/{parent_pages} · HTML empty: {qa['empty_html']}/{parent_pages} · main_html empty: {qa['empty_main_html']}/{parent_pages} · convert_main_content empty: {qa['empty_convert_main_content']}/{parent_pages} · style non-null: {qa['non_null_style']}/{parent_pages} · language non-null: {qa['non_null_language']}/{parent_pages}</p>""")
  review+=section(h('not_started', 'Not started'),f"""<p class="callout">{h('measured_results_rankings_scores_and_independent_result', 'Measured results, rankings, scores and independent result reviews are all zero.')}</p>""")
  _research_write(out,'review/index.html',h('review_record', 'Verification'),review,english)

  guide=_research_hero(h('interpretation', 'Guide'),h('this_edition_answers_what_to_test_not', 'This edition answers what to test, not which method wins.'),english)
  guide+=section(h('reading_order', 'Reading order'),f"""<div class="guide-grid"><article class="guide-card"><h3>1 · {h('selection', 'Selection')}</h3><p>{h('see_why_webmainbench_545_is_the_sole', 'See why WebMainBench-545 is the sole selection.')}</p></article><article class="guide-card"><h3>2 · {h('sampling', 'Sampling')}</h3><p>{h('see_how_cases_cover_official_categories', 'See how {sample_size} cases cover official categories.', sample_size=sample_size)}</p></article><article class="guide-card"><h3>3 · {h('adoption_2', 'Adoption')}</h3><p>{h('separate_execution_integration_and_mere_mentions', 'Separate execution, integration and mere mentions.')}</p></article><article class="guide-card"><h3>4 · {h('measurement', 'Measurement')}</h3><p>{h('rank_only_after_complete_denominators_and_review', 'Rank only after complete denominators and review.')}</p></article></div>""")
  _research_write(out,'guide/index.html',h('interpretation', 'Guide'),guide,english)

  adoption=_research_hero(h('adoption', 'Adoption'),h('adoption_is_evidenced_by_papers_executable_benchmark', 'Adoption is evidenced by papers, executable benchmark repositories and framework integrations; stars are context only.'),english)
  adoption+=section(h('evidence_boundary', 'Evidence boundary'),f"<p>{h('verified_evidence_covers_paper_runs_open_source', 'Verified evidence covers paper runs, open-source adapters and executable benchmark repositories. No independently auditable commercial production-deployment count was found, so extractor support and search hits are not presented as commercial adoption.')}</p>")
  adoption_rows=[]
  for x in direct:
   adoption_rows.append(('',cell(esc(x['name']))+cell('<br>'.join(esc(u) for u in x['users']))+cell(esc(field(x,'maturity')))+cell(_research_links(x['sources']))))
  adoption+=section(h('direct_benchmarks', 'Direct benchmarks'),research_table([h('benchmark', 'Benchmark'),h('verified_users', 'Verified users'),h('assessment', 'Assessment'),h('evidence', 'Evidence')],adoption_rows,metrics=[]))
  browser_rows=[('',cell(esc(x['name']))+cell(esc(field(x,'size')))+cell(esc(field(x,'measures')))+cell(esc(field(x,'adoption')))+cell(esc(field(x,'why_separate')))+cell(_research_links(x['sources']))) for x in adjacent]
  adoption+=section(h('browser_and_search_benchmarks', 'Browser and search benchmarks'),research_table([h('name', 'Name'),h('scale', 'Scale'),h('measures', 'Measures'),h('adoption_2', 'Adoption'),h('why_separate', 'Why separate'),h('sources_2', 'Sources')],browser_rows,metrics=[]))
  stars=[('',cell(esc(name))+cell(str(stars))+cell(str(forks))) for name,stars,forks in research['adoption_snapshot']['repositories']]
  warning=research['adoption_snapshot']['warning' if english else 'warning_zh']
  adoption+=section(h('github_snapshot', 'GitHub snapshot'),f'<p class="source-note">{esc(warning)}</p>'+research_table([h('repository', 'Repository'),'Stars','Forks'],stars,metrics=[]))
  _research_write(out,'adoption/index.html',h('adoption', 'Adoption'),adoption,english)

  reproduce=_research_hero(h('reproduction', 'Reproduction'),h('this_edition_only_reproduces_the_research_report', 'This edition only reproduces the research report and makes no provider calls.'),english)
  reproduce+=section(h('build', 'Build'),'<pre>make public-benchmark-research</pre>')
  reproduce+=section(h('freeze_the_case_manifest', 'Freeze the {sample_size}-case manifest', sample_size=sample_size),'<pre>make public-benchmark-sample SOURCE=/absolute/path/to/WebMainBench_545.jsonl</pre>')
  if frozen: reproduce+=section(h('frozen_artifact', 'Frozen artifact'),f"<p>{link(artifact_prefix + 'sample-manifest.json', h('download_the_case_manifest_json', 'Download the {sample_size}-case manifest JSON →', sample_size=sample_size))}</p>")
  reproduce+=section(h('freeze_before_measurement', 'Freeze before measurement'),f"<p>{h('record_dataset_and_scorer_revisions_environment_licenses', 'Record dataset and scorer revisions, environment, licenses and the complete denominator for each track; provider outputs remain outside Git.')}</p>")
  _research_write(out,'reproduce/index.html',h('reproduction', 'Reproduction'),reproduce,english)

  changes=render_localized_release_history(
   {'releases':release_history},english,kind='public_benchmark_research')
  _research_write(out,'changes/index.html',h('changelog', 'Changes'),changes,english)

  accounts=_research_hero(h('access_setup', 'Accounts and setup'),h('the_research_edition_needs_no_provider_account', 'The research edition needs no provider account.'),english)
  accounts+=section(h('current_requirement', 'Current requirement'),f"""<p class="callout success">{h('only_the_committed_research_and_sample_manifest', 'Only the committed research and sample-manifest JSON files are read; no .env, token, cookie or personal login state is used.')}</p>""")
  accounts+=section(h('measurement_phase', 'Measurement phase'),f"<p>{h('the_measured_edition_will_list_official_installation', 'The measured edition will list official installation sources, versions and required environment-variable names per method, never their values.')}</p>")
  _research_write(out,'accounts/index.html',h('access_setup', 'Accounts and setup'),accounts,english)
 (out/'research.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 write_language_contract(out,include_english=include_english)
 return out/'index.html'

def succeeded(r,c):
 return task_passed(r['ok'],r['metrics'],c.get('task_kind','body'))

def measured(r,c):
 return task_measured(r['metrics'],c.get('task_kind','body'))

def result_label(r,c):
 if r['metrics'].get('evaluation_excluded'):return t('excluded_from_comparison', 'Excluded from comparison')
 if not measured(r,c):return t('undecidable', 'Undecidable')
 state=t('pass_2', 'Pass') if succeeded(r,c) else t('fail_2', 'Fail')
 return (t('list_tasks', 'List tasks:') + f'{state}') if c.get('task_kind')=='structure' else state

def case_rate(rs,c):
 rs=[r for r in rs if measured(r,c)]
 value=rate(sum(succeeded(r,c) for r in rs),len(rs))
 return value

def overview_card(records,cases,track,label,names,*,diagnostic=False):
 selected=[c for c in cases.values() if report_family(c)==track]
 if not selected:return ''
 rs=[r for r in records if report_family(cases[r['case_id']])==track]
 eligible=[r for r in rs if measured(r,cases[r['case_id']])]
 if diagnostic:
  return ('<div class="card"><div class="eyebrow">' + t('tasks_2', '{count} tasks', count=len(selected)) + f"</div><h3>{link('#home-rankings-' + track, label)}</h3><p>" + t('local_diagnostic_results_no_ranks_are_assigned', 'Local diagnostic results; no ranks are assigned.') + '</p></div>')
 rates={n:sum(succeeded(r,cases[r['case_id']]) for r in eligible if r['provider']==n)/sum(r['provider']==n for r in eligible) for n in names if any(r['provider']==n for r in eligible)}
 maximum=max(rates.values(),default=None)
 winners='、'.join(provider_label(n) for n in names if rates.get(n)==maximum) if maximum else t('no_passing_tasks_in_this_run', 'No passing tasks in this run') if rates else t('no_call_records_yet', 'No call records yet')
 return ('<div class="card"><div class="eyebrow">' + t('tasks_2', '{count} tasks', count=len(selected)) + f"</div><h3>{link('#home-rankings-' + track, label)}</h3><p>" + t('best_in_family', 'Best in family:') + f'<b>{pct(maximum)}</b></p><p>{esc(winners)}</p></div>')

def review_summary(out, prefix='', *, highlight=True):
 path=out/'result-review.json'
 if not path.is_file():return ''
 audit=json.loads(path.read_text(encoding='utf-8'))
 if audit.get('retained_reviews'):
  mode_note = (model_text(*pair('this_round_checked_evidence_with_known_machine', ' This round checked evidence with known machine outcomes; it was not an independent blind review.')) if audit.get('review_protocol',{}).get('blind_initial_verdict') is False else '')
  if audit.get('review_protocol',{}).get('fresh_blind_initial_verdict') is True:
   mode_note=model_text(*pair('fresh_reviews_judged_answers_with_machine_outcomes', ' Fresh reviews judged answers with machine outcomes hidden, then investigated disagreements and anomalies; retained reviews preserve their original protocol.'))
  if audit.get('renewed_context_reviewed_attempts',0):
   blind_count=audit.get('fresh_blind_reviewed_attempts',0);renewed_count=audit['renewed_context_reviewed_attempts']
   mode_note=model_text(*pair('of_these_received_initial_judgments_with_machine', ' Of these, {blind_count} received initial judgments with machine outcomes hidden; {renewed_count} received supplemental adjudication of corrected contracts or request context, preserving prior opinions and inspecting current evidence and machine outcomes.', blind_count=blind_count, renewed_count=renewed_count))
  return ('<p'+(' class="callout"' if highlight else '')+'>'+model_text(*pair('current_review_coverage_freshly_reviewed_prior_reviews', 'Current review coverage: {get}/{get2}: {get3} freshly reviewed; {get4} prior reviews retained after verifying unchanged decision inputs.', get=audit.get('reviewed_attempts', 0), get2=audit.get('expected_attempts', 0), get3=audit.get('fresh_reviewed_attempts', 0), get4=audit.get('retained_reviewed_attempts', 0)))+mode_note+' '+link(prefix+'review/index.html',t('view_review_rounds', 'View review rounds →'))+'</p>')
 if audit.get('adjudication_revision'):
  count=len(audit['adjudication_revision']['adjudicated_attempt_ids'])
  return ('<p'+(' class="callout"' if highlight else '')+'>'+model_text(*pair('current_review_coverage_case_specific_adjudications_supplemented', 'Current review coverage: {get}/{get2}; {count} case-specific adjudications supplemented. No new full blind review or certification is claimed.', get=audit.get('reviewed_attempts', 0), get2=audit.get('expected_attempts', 0), count=count))+' '+link(prefix+'review/index.html',t('view_review_rounds', 'View review rounds →'))+'</p>')
 scope=audit.get('review_protocol',{}).get('scope','all')
 if scope=='supplement-25-local-audit':
  return (f"""<p class="callout success"><b>新增25例结果审核 {audit.get('reviewed_attempts', 0):,}/{audit.get('expected_attempts', 0):,}；未决 {audit.get('unresolved', 0)}；75例沿用既有审核记录</b>。 {link(prefix + 'review/index.html', t('view_review_rounds', 'View review rounds →'))}</p>""")
 subject='机器失败' if scope=='machine-failures' else '全量'
 return (f"""<p class="callout success"><b>独立核验：{subject} {audit.get('reviewed_attempts', 0):,}/{audit.get('expected_attempts', 0):,}；未决 {audit.get('unresolved', 0)}</b>。 {link(prefix + 'review/index.html', t('view_review_rounds', 'View review rounds →'))}</p>""")

def review_index(audit):
 return {x['attempt_id']:x for x in (audit or {}).get('attempts',[])}

def review_cell(audit, rows, base=''):
 if not audit:return ''
 indexed=review_index(audit);items=[indexed[r['attempt_id']] for r in rows if r['attempt_id'] in indexed]
 unresolved=sum(not x.get('final_verdict') for x in items)
 label=t('review_records', '{count}/{count2} · review records', count=len(items), count2=len(rows))
 if unresolved:label+=f' · 未决 {unresolved}'
 return cell(link(base+'review/index.html',label),len(items)/len(rows) if rows else -1)

def _evidence_chunks(text, width=620, overlap=100):
 chunks=[]
 for raw in text.splitlines():
  line=raw.strip()
  if not line:continue
  if len(line)<=width:chunks.append(line)
  else:
   for start in range(0,len(line),width-overlap):chunks.append(line[start:start+width])
 return chunks

def _best_evidence_excerpt(expected, output):
 wanted=Counter(tokens(expected))
 if not wanted:return ''
 best='';best_score=0
 for chunk in _evidence_chunks(output):
  found=Counter(tokens(chunk));score=sum((wanted & found).values())/sum(wanted.values())
  if score>best_score:best,best_score=chunk,score
 return best if best_score>=min(.25,3/max(1,sum(wanted.values()))) else ''

def _normalized_evidence_lines(output):
 return [[line,normalize_for_match(line),None] for raw in output.splitlines() if (line:=raw.strip())]

def _exact_evidence_lines(expected, output, *, normalized_lines=None):
 """Locate one bounded, verbatim fragment; never repeat a whole long HTML line."""
 normalized_expected=normalize_for_match(expected)
 lines=_normalized_evidence_lines(output) if normalized_lines is None else normalized_lines
 for entry in lines:
  line,normalized_line=entry[:2]
  if not normalized_expected or normalized_expected not in normalized_line:continue
  if len(line)<=1200:return line
  # Retain the full-line match requirement: cutting Markdown syntax must not
  # turn an otherwise excluded destination into a spurious visible-text hit.
  if entry[2] is None:
   entry[2]=[(chunk,normalize_for_match(chunk))
             for chunk in _evidence_chunks(line,width=1200,overlap=600)]
  for chunk,normalized_chunk in entry[2]:
   if normalized_expected in normalized_chunk:return chunk
 return ''

def _diagnostic_log_excerpt(text):
 """Keep exact retained log lines around reader-relevant diagnostics."""
 pattern=re.compile(r'error|warn|fail|timeout|timed out|panic|denied|challenge',re.I)
 lines=text.splitlines();selected=set()
 for index,line in enumerate(lines):
  if pattern.search(line):selected.update(range(max(0,index-1),min(len(lines),index+2)))
  if len(selected)>=36:break
 if not selected:selected.update(range(min(12,len(lines))))
 return '\n'.join(f'{index+1:>6}  {lines[index]}' for index in sorted(selected))

def _call_log_excerpt(item):
 """Prefer exact structured diagnostics while retaining plain legacy logs."""
 payload=item['payload']
 if not payload:return _diagnostic_log_excerpt(item['text'])
 parts=[]
 error=payload.get('error') if isinstance(payload.get('error'),dict) else {}
 process=payload.get('process') if isinstance(payload.get('process'),dict) else {}
 events=payload.get('events') if isinstance(payload.get('events'),list) else []
 if error.get('type') or error.get('message'):
  parts.append('error: '+str(error.get('type') or '—')+' · '+str(error.get('message') or '—'))
 if process.get('stderr'):
  parts.append('stderr:\n'+str(process['stderr']))
 elif process.get('stdout'):
  parts.append('stdout:\n'+str(process['stdout']))
 for event in events:
  if isinstance(event,dict) and (event.get('error_type') or event.get('stage')):
   parts.append('event: '+json.dumps(event,ensure_ascii=False,sort_keys=True))
 return _diagnostic_log_excerpt('\n'.join(parts) if parts else item['text'])

def _body_region_evidence(content,contract):
 """Map canonical scorer allocations to complete lines without changing scoring output."""

 version=contract['version'];size=6 if version==3 else 3
 units=tokens(content) if version==1 else compact_ngrams(content,size)
 predicted=Counter(units);expected={}
 for region in contract['regions']:
  values=(region['text'].split() if version==1 else compact_ngrams(region['text'],size))
  expected[region['id']]=Counter(values)
 if version==3:
  floor=contract['minimum_floor'];target=contract['minimum_coverage']
  floors={identifier:ceil(floor*sum(row.values())-1e-12) for identifier,row in expected.items()}
  targets={identifier:ceil(target*sum(row.values())-1e-12) for identifier,row in expected.items()}
  assigned,_=_global_region_allocation(
   predicted,expected,floors,targets,contract['minimum_region_passes'])
 else:
  assigned={identifier:predicted & row for identifier,row in expected.items()}
 lines=[raw.strip() for raw in content.splitlines() if raw.strip()]
 evidence_lines={identifier:set() for identifier in assigned}
 if version==1:
  occurrences=defaultdict(list);flattened=[]
  for line_index,line in enumerate(lines):
   for unit in tokens(line):
    flattened.append(unit);occurrences[unit].append((line_index,))
  if flattened!=tokens(content):
   raise ValueError('Body token evidence differs from scorer normalization')
  for identifier,row in assigned.items():
   for unit,count in row.items():
    for location in occurrences[unit][:count]:evidence_lines[identifier].update(location)
 else:
  compact_lines=[compact_ngrams(line,1) for line in lines]
  flattened=[character for line in compact_lines for character in line]
  if flattened!=compact_ngrams(content,1):
   raise ValueError('Body fragment evidence differs from scorer normalization')
  character_lines=[]
  for line_index,line in enumerate(compact_lines):character_lines.extend([line_index]*len(line))
  occurrences=defaultdict(list)
  for index,unit in enumerate(compact_ngrams(content,size)):
   occurrences[unit].append(tuple(dict.fromkeys(character_lines[index:index+size])))
  offsets=Counter()
  for identifier in sorted(assigned):
   for unit,count in assigned[identifier].items():
    start=offsets[unit] if version==3 else 0
    selected=occurrences[unit][start:start+count]
    if len(selected)!=count:raise ValueError('Body evidence allocation exceeds scorer units')
    for location in selected:evidence_lines[identifier].update(location)
    if version==3:offsets[unit]+=count
 evidence={identifier:[lines[index] for index in sorted(indices)]
           for identifier,indices in evidence_lines.items()}
 return evidence,{identifier:sum(row.values()) for identifier,row in assigned.items()}

def _position_label(index,total):
 names=[t('opening', 'Opening'),t('early_section', 'Early section'),t('middle', 'Middle'),t('late_section', 'Late section'),t('ending', 'Ending')] if total==5 else [f'位置 {i+1}' for i in range(total)]
 start=round(index*100/total);end=round((index+1)*100/total)
 return f'{names[index]}（标准答案 {start}–{end}%）'

def _body_scoring_text(record, output):
 """Use the scorer's body input; titles belong only to answer diagnostics."""
 content=answer_input(output.encode('utf-8'),record.get('provider_metadata'),
                      ok=record.get('ok',False),status_code=record.get('status_code'))[0]
 if is_html_document_or_fragment(content):
  content=visible_html_text(content)
 else:
  fallback=html.unescape(re.sub(r"<(script|style|template)\b[^>]*>.*?</\1\s*>","",content,flags=re.I|re.S))
  content=native_markup_body_text(content,fallback)
 return content

def body_region_rows(case, metrics, output, *, frozen_scores=False):
 contract=case.get('body_requirements') or {}
 if not contract:return [],''
 saved_regions=metrics.get('body_region_results',[])
 recalculated={}
 if frozen_scores:
  # The frozen-build boundary validates the saved metric and answer hashes.
  # A newer normalizer or a redacted public answer cannot reproduce the old
  # matching-text identity. Render the saved allocation without rescoring it.
  evidence={}
  for item in saved_regions:
   passages=[]
   for passage in item.get('matched_output_passages',[]):
    if passage.get('text'):
     public,_=project_evidence_view('public-evidence/excerpt.txt',passage['text'].encode(),role='scored_answer',content_format='text')
     passages.append(public.decode())
   evidence[item['id']]=passages
  evidence_counts={item['id']:item.get('matched_units',item.get('matched_tokens',0)) for item in saved_regions}
  recalculated={item['id']:item for item in saved_regions}
 elif saved_regions and all('matched_output_passages' in item for item in saved_regions):
  matching_text=_compact_with_positions(output)[0]
  digest=hashlib.sha256(matching_text.encode()).hexdigest()
  if any(item.get('matching_text_sha256')!=digest for item in saved_regions):
   raise ValueError('Saved body evidence differs from the scorer input')
  evidence={item['id']:[passage.get('text','') for passage in item.get('matched_output_passages',[]) if passage.get('text')]
            for item in saved_regions}
  evidence_counts={item['id']:item.get('matched_units',item.get('matched_tokens',0)) for item in saved_regions}
  recalculated={item['id']:item for item in saved_regions}
 else:
  recalculated={x['id']:x for x in evaluate_body_requirements(output,contract)['body_region_results']}
  evidence,evidence_counts=_body_region_evidence(output,contract)
 regions={x['id']:x for x in contract['regions']}
 rows=[];cards=[];total=len(regions)
 for index,item in enumerate(metrics.get('body_region_results',[]),1):
  actual={**recalculated.get(item['id'],{}),**item}
  if recalculated and evidence_counts.get(item['id'])!=recalculated.get(item['id'],{}).get('matched_units'):
   raise ValueError('Body evidence allocation differs from scorer: '+item['id'])
  expected=regions.get(item['id'],{}).get('text','')
  expected_count=actual.get('expected_units',actual.get('expected_tokens',len(expected.split())))
  matched=actual.get('matched_units',actual.get('matched_tokens',round(actual['coverage']*expected_count)))
  passed=actual.get('target_pass',actual['pass'])
  label=_position_label(index-1,total)
  judgement=(t('meets_target', 'Meets target') if passed else t('below_target_above_hard_floor', 'Below target; above hard floor') if actual.get('floor_pass') else t('below_hard_floor', 'Below hard floor'))
  css='pass' if passed else 'fail'
  rows.append(('',cell(esc(label))+cell(f'{expected_count:,}')+cell(f'{matched:,}')+
               cell(pct(actual['coverage']))+cell(esc(judgement))))
  passage='\n\n'.join(evidence.get(item['id'],[]))
  missing=(model_text(*pair('the_historical_score_has_no_saved_matching', 'The historical score has no saved matching passage for this region; inspect the complete output.'))
           if frozen_scores and 'matched_output_passages' not in item else t('the_scorer_assigned_no_displayable_actual_output', 'The scorer assigned no displayable actual-output text to this region.'))
  actual_html=(f'<pre data-kind="region-output">{esc(passage)}</pre>' if passage else
               '<p class="empty">'+missing+'</p>')
  cards.append((f'<article class="region-card {css}" id="region-{index}"><div class="region-head"><div><h3>{index}. {esc(label)}</h3><p>' + t('this_region_has_coverage_every_region_must', 'This region has {coverage} coverage. Every region must reach at least {get}; reaching {minimum} counts as a substantially complete region.', coverage=pct(actual['coverage']), get=pct(actual.get('floor', actual['minimum'])), minimum=pct(actual['minimum'])) + f'</p></div><span class="verdict-badge">{esc(judgement)}</span></div><div class="region-pair"><div><b>' + t('complete_reference_text_for_this_region', 'Complete reference text for this region') + f'</b><pre data-kind="reference">{esc(expected)}</pre></div><div><b>' + t('complete_actual_output_lines_used_to_score', 'Complete actual-output lines used to score this region') + f'</b>{actual_html}</div></div></article>'))
 return rows,cards

def stable_answer_panel(contract):
 anchors=contract.get('stable_text_anchors',[])
 anchors_required=contract.get('stable_text_role','required')!='diagnostic'
 anchor_label=t('stable_text_that_must_appear', 'Stable text that must appear') if anchors_required else t('stable_text_for_scope_checks', 'Stable text for scope checks')
 items=[]
 for index,anchor in enumerate(anchors,1):
  alternatives=anchor.get('alternatives',[])
  text=('\n<span>' + t('or', 'or') + '</span>\n').join(f'<span translate="no">{esc(value)}</span>' for value in alternatives)
  items.append(f'<div class="answer-item"><b>{anchor_label} {index}（任一写法即可）</b><blockquote>{text}</blockquote></div>')
 minimum=contract.get('minimum_records')
 maximum=contract.get('maximum_records')
 count=(t('at_least_complete_unique_records', 'At least {minimum} complete, unique records', minimum=minimum) if maximum is None else
        f'{minimum}–{maximum} 条完整且不重复的记录')
 scroll=' data-scroll-after="5"' if len(anchors)>5 else ''
 role_note=t('all_belong_to_the_reference_answer', 'All belong to the reference answer') if anchors_required else t('used_only_for_scope_checks_does_not', 'Used only for scope checks; does not affect the result')
 note=(f'<p class="source-note">共 {len(anchors)} 组稳定文字，{role_note}；当前区域显示约 5 组高度，向下滚动查看全部。</p>'
       if len(anchors)>5 else '')
 return (f'{note}<div class="answer-list"{scroll}><div class="answer-item"><b>' + t('structure_requirements', 'Structure requirements') + f"</b><blockquote>{esc(count)}</blockquote></div>{''.join(items)}</div>")

def required_text_answer_panel(case):
 items=[('<div class="answer-item"><b>' + t('body_length_requirement', 'Body length requirement') + '</b><blockquote>' + t('at_least_characters', 'At least {get:,} characters', get=case.get('min_content_chars', 1)) + '</blockquote></div>')]
 for index,fact in enumerate(case.get('required_facts',[]),1):
  text=('\n<span>' + t('or', 'or') + '</span>\n').join(f'<span translate="no">{esc(value)}</span>' for value in fact.get('alternatives',[]))
  items.append(('<div class="answer-item"><b>' + t('required_text_group_any_alternative_may_match', 'Required text group {index} (any alternative may match)', index=index) + f'</b><blockquote>{text}</blockquote></div>'))
 threshold=case.get('fact_coverage_threshold',1.0)
 note=(('<p class="source-note">' + t('every_text_group_must_match', 'Every text group must match.') + '</p>') if threshold==1 else
       ('<p class="source-note">' + t('text_group_coverage_must_reach_at_least', 'Text-group coverage must reach at least {threshold}.', threshold=pct(threshold)) + '</p>'))
 return note+'<div class="answer-list">'+''.join(items)+'</div>'


def machine_contract(case):
 contract={key:case[key] for key in ('acceptance_contract','composite_acceptance','body_requirements') if case.get(key)}
 return contract

def composite_contract_details(contract):
 labels={'named_link':t('name_and_real_link', 'Name and real link'),'name_description':t('name_and_description', 'Name and description'),'table':t('table_fields', 'Table fields'),
         'linked_table':t('same_row_fields_and_real_link', 'Same-row fields and real link'),'literals':t('fixed_text_groups', 'Fixed text groups'),'field_report':t('same_report_fields', 'Same report fields')}
 rows=[]
 for group in contract.get('groups',[]):
  scope=[]
  if group.get('start_pattern'):scope.append((t('start', 'Start:') + f"{group['start_pattern']}"))
  if group.get('end_pattern'):scope.append((t('end', 'End:') + f"{group['end_pattern']}"))
  requirements=[]
  if group.get('minimum_records') is not None:requirements.append(f'至少 {group["minimum_records"]} 条')
  if group.get('hosts'):requirements.append((t('hosts', 'Hosts: ') + f"{'、'.join(group['hosts'])}"))
  if group.get('entry_path'):requirements.append((t('path', 'Path:') + f"{group['entry_path']}"))
  if group.get('required_fields'):requirements.append((t('same_entry_fields', 'Same-entry fields:') + f"{'、'.join((field['id'] for field in group['required_fields']))}"))
  if group.get('required_columns'):requirements.append((t('same_row_columns', 'Same-row columns:') + f"{'、'.join((field['id'] for field in group['required_columns']))}"))
  if group.get('required_literals'):requirements.append((t('all_text', 'All text:') + f"{'、'.join(group['required_literals'])}"))
  if group.get('fixed_values'):requirements.append((t('fixed_value', 'Fixed value:') + f"{'、'.join((f'{key}={value}' for key, value in group['fixed_values'].items()))}"))
  rows.append(('',cell(esc(group['id']))+cell(esc(labels.get(group['kind'],group['kind'])))+
               cell(esc('；'.join(scope) or t('whole_response', 'Whole response')))+cell(esc('；'.join(requirements)))))
 return (('<div class="answer-item"><b>' + t('combined_rule', 'Combined rule') + '</b><p>' + t('each_group_is_computed_separately_and_every', 'Each group is computed separately and every group must pass; counts, links and extra fields cannot be stitched across lists or entries.') + f"</p>{table([t('rule_group', 'Rule group'), t('check_type', 'Check type'), t('scope', 'Scope'), t('must_satisfy', 'Must satisfy')], rows, metrics=[])}</div>"))

def composite_result_evidence(metrics):
 results=metrics.get('composite_group_results',[])
 if not results:return ''
 labels={'named_link':t('name_and_real_link', 'Name and real link'),'name_description':t('name_and_description', 'Name and description'),'table':t('table_fields', 'Table fields'),
         'linked_table':t('same_row_fields_and_real_link', 'Same-row fields and real link'),'literals':t('fixed_text_groups', 'Fixed text groups'),'field_report':t('same_report_fields', 'Same report fields')}
 rows=[];details=[]
 for result in results:
  count='—' if result.get('count') is None else str(result['count'])
  minimum='—' if result.get('minimum') is None else str(result['minimum'])
  rows.append(('',cell(esc(result['id']))+cell(esc(labels.get(result['kind'],result['kind'])))+
               cell(count)+cell(minimum)+cell(t('pass_2', 'Pass') if result.get('pass') else t('fail_2', 'Fail'))))
  payload={key:result[key] for key in ('records','literal_results','fixed_value_results') if result.get(key)}
  if payload:
   details.append((f"<details><summary>{esc(result['id'])} " + t('parsing_result', ' parsing result') + f'</summary><pre data-kind="metadata">{esc(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></details>'))
 return (('<div class="decision-lead"><b>' + t('combined_rule_result', 'Combined-rule result:') + '</b>' + t('each_group_is_checked_independently_entries_from', 'Each group is checked independently; entries from another list cannot fill a gap, and fields from different entries cannot be joined.') + f"</div>{table([t('rule_group', 'Rule group'), t('check_type', 'Check type'), t('valid_entries', 'Valid entries'), t('at_least', 'At least'), t('verdict', 'Verdict')], rows, metrics=[])}{''.join(details)}"))

def _stable_field_comparison(metrics,item):
 """Find the closest parsed record for a structured anchor, for display only."""
 schema=metrics.get('stable_task_schema')
 if schema!='quote':return None
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
 compact=0
 for expected,returned,_ in comparisons:
  left=normalize_for_match(expected.get('tags','')).replace(' ','')
  right=normalize_for_match(returned.get('tags','')).replace(' ','')
  compact+=bool(left and left==right)
 contaminated=len(comparisons)-compact
 parts=[t('quotes_and_authors_of_failed_checks_match', 'Quotes and authors of {count} failed checks match the reference', count=len(comparisons))]
 if compact:parts.append(t('records_have_complete_tag_text_but_no', '{compact} records have complete tag text but no readable separator between tags', compact=compact))
 if contaminated:parts.append(t('records_have_tag_fields_mixed_with_extra', '{contaminated} records have tag fields mixed with extra text or missing text', contaminated=contaminated))
 return (f"{'；'.join(parts)}" + t('the_task_requires_the_tag_list_to', '. The task requires the tag list to keep field boundaries, so it still fails'))

def stable_anchor_evidence(metrics,output):
 cards=[];normalized_lines=None
 anchors_required=metrics.get('stable_anchors_required',True)
 expected_label=t('reference_answer', 'Reference answer') if anchors_required else t('scope_check_text', 'Scope-check text')
 for index,item in enumerate(metrics.get('stable_anchor_results',[]),1):
  alternatives=item.get('alternatives',[]);expected=('\n<span>' + t('or', 'or') + '</span>\n').join(f'<span translate="no">{esc(value)}</span>' for value in alternatives)
  excerpt=''
  for alternative in alternatives:
   if normalized_lines is None:normalized_lines=_normalized_evidence_lines(output)
   candidate=_exact_evidence_lines(alternative,output,normalized_lines=normalized_lines)
   if candidate:excerpt=candidate;break
  passed=bool(item.get('pass'));css='pass' if passed else 'fail'
  comparison=_stable_field_comparison(metrics,item) if not passed else None
  field_detail='';badge=t('matched', 'Matched') if passed else t('not_matched', 'Not matched')
  if comparison:
   expected_fields,returned_fields,exact=comparison
   labels={'quote':t('quote', 'Quote'),'author':t('author', 'Author'),'tags':t('tags', 'Tags')};rows=[]
   for field in ('quote','author','tags'):
    wanted=expected_fields.get(field,'');got=returned_fields.get(field,'')
    compact=(normalize_for_match(wanted).replace(' ','')==normalize_for_match(got).replace(' ',''))
    verdict=t('matched', 'Matched') if exact[field] else t('text_complete_but_separators_lost', 'Text complete, but separators lost') if field=='tags' and compact else t('different', 'Different')
    rows.append(('',cell(esc(labels[field]))+cell(f'<span translate="no">{esc(wanted)}</span>')+cell(f'<span translate="no">{esc(got)}</span>')+cell(esc(verdict))))
   field_detail=('<p class="source-note">' + t('whole_records_are_judged_by_field_text', 'Whole records are judged by field; text appearing does not mean list boundaries are intact.') + f"</p>{table([t('field', 'Field'), t('reference_answer', 'Reference answer'), t('actually_parsed', 'Actually parsed'), t('verdict', 'Verdict')], rows, metrics=[])}")
   badge=t('invalid_field_boundaries', 'Invalid field boundaries')
  actual=(f'<p translate="no">{esc(excerpt)}</p>' if excerpt else ('<p class="empty">' + t('no_continuous_source_excerpt_could_be_located', 'No continuous source excerpt could be located in the actual output.') + '</p>'))
  cards.append((f'<article class="anchor-card {css}"><div class="anchor-head"><h3>' + t('stable_text', 'Stable text {index}', index=index) + f'</h3><span class="verdict-badge">{badge}</span></div><div class="evidence-pair"><div class="evidence-sample"><b>{expected_label}</b><p>{expected}</p></div><div class="evidence-sample"><b>' + t('actual_output_evidence', 'Actual-output evidence') + f'</b>{actual}</div></div>{field_detail}</article>'))
 if not cards:return ''
 role_note=(t('all_count_toward_scoring', 'All count toward scoring') if anchors_required else
            t('used_only_for_scope_checks_does_not', 'Used only for scope checks; does not affect the result'))
 note=(f'<p class="source-note">共 {len(cards)} 项稳定文字判定，{role_note}；当前区域显示约 5 项高度，向下滚动查看全部。</p>'
       if len(cards)>5 else '')
 scroll=' data-scroll-after="5"' if len(cards)>5 else ''
 return note+f'<div class="anchor-grid"{scroll}>'+''.join(cards)+'</div>'

def failure_category(r,c):
 if r['metrics'].get('evaluation_excluded'):return t('evaluation_environment_defect_excluded_across_all_methods', 'Evaluation environment defect: excluded across all methods')
 if not measured(r,c):return t('undecidable_evidence_of_the_final_page_identity', 'Undecidable: evidence of the final page identity is missing') if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status')=='unavailable' else t('unmeasured_acceptance_evidence_is_missing', 'Unmeasured: acceptance evidence is missing')
 if succeeded(r,c):return '—'
 if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status') != 'verified':
  return t('final_page_identity_is_not_verified', 'Final page identity is not verified')
 # Classification describes an already failed result; hints never veto success.
 metrics=r['metrics']
 if (r.get('ok',False) and metrics.get('request_ok',True)
     and (metrics.get('blocked_page') or metrics.get('stable_blocked_page'))):
  return t('verification_or_block_page_returned', 'Verification or block page returned')
 if c.get('track') == 'risk_control':
  metrics=r['metrics']
  if not metrics.get('request_ok',r.get('ok',False)):
   return t('request_or_program_execution_failed', 'Request or program execution failed')
  content_chars=metrics.get('content_chars')
  if content_chars == 0:
   return t('completely_empty_response', 'Completely empty response')
  if (
   isinstance(content_chars,int) and content_chars <= 100
   and not metrics.get('markdown_headings')
   and not metrics.get('markdown_links')
   and not metrics.get('stable_anchor_hits')
   and not metrics.get('content_check_hits')
  ):
   return t('waiting_message_or_page_shell_only', 'Waiting message or page shell only')
  return t('acceptance_rules_not_met', 'Acceptance rules not met')
 if r['metrics'].get('composite_acceptance_pass') is False:return t('combined_rule_not_met', 'Combined rule not met')
 if _quote_field_issue(r['metrics']):return t('field_boundaries_lost', 'Field boundaries lost')
 return (r.get('diagnosis') or diagnose(r,c,'') or {'category':t('task_requirements_not_met', 'Task requirements not met')})['category']

def stable_failure_summary(metrics):
 parts=[]
 minimum=metrics.get('stable_minimum_records')
 complete=metrics.get('stable_unique_record_count')
 if isinstance(minimum,int) and isinstance(complete,int) and complete < minimum:
  parts.append(t('only_records_are_both_complete_and_non', 'Only {complete} records are both complete and non-duplicate, fewer than the required {minimum}', complete=complete, minimum=minimum))
 quote_issue=_quote_field_issue(metrics)
 if quote_issue:return quote_issue
 missing=[]
 for item in metrics.get('stable_anchor_results',[]):
  if not item.get('pass'):
   alternatives=item.get('alternatives',[])
   if alternatives:missing.append(' / '.join(alternatives))
 if missing:parts.append((t('stable_text_missing', 'Stable text missing: "') + f"{'”、“'.join(missing)}”"))
 if metrics.get('text_encoding_corruption'):
  parts.append(t('output_is_visibly_garbled_replacement_characters_of', 'Output is visibly garbled: {get:,} replacement characters, {get2} of it', get=metrics.get('replacement_character_count', 0), get2=pct(metrics.get('replacement_character_ratio'))))
 errors=set(metrics.get('stable_task_errors') or [])
 labels=[
  ('declared_truncation',t('the_method_explicitly_declares_the_body_truncated', 'The method explicitly declares the body truncated')),
  ('script_payload_shell',t('returned_script_or_serialized_state_not_readable', 'Returned script or serialized state, not readable body text')),
  ('content_too_short',t('body_shorter_than_the_task_s_declared', "Body shorter than the task's declared minimum")),
  ('duplicate_records',t('returned_records_contain_duplicates', 'Returned records contain duplicates')),('incomplete_records',t('some_records_miss_required_fields', 'Some records miss required fields')),
  ('too_many_complete_records',t('complete_records_exceed_the_declared_task_range', 'Complete records exceed the declared task range')),
 ]
 for key,label in labels:
  if key in errors and label not in parts:parts.append(label)
 return '；'.join(parts) or t('stable_text_or_record_structure_did_not', 'Stable text or record structure did not meet the task requirements')

def reason(r,c):
 if r['metrics'].get('evaluation_excluded'):
  return r['metrics']['evaluation_exclusion_reason']
 if not measured(r,c):
  if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status')=='unavailable':
   return t('undecidable_the_actual_response_is_saved_but', 'Undecidable: the actual response is saved, but the final page identity cannot be verified; it counts toward neither passes, failures nor the pass-rate denominator.')
  return t('unmeasured_the_historical_call_has_no_original', 'Unmeasured: the historical call has no original-file byte acceptance evidence and counts toward neither the task pass rate nor failures.')
 if r['metrics'].get('deadline_overrun'):return t('the_call_exceeded_its_registered_end_to', 'The call exceeded its registered end-to-end deadline. Actual latency and returned content are retained, but the result does not count as on-time delivery.')
 if r['metrics'].get('forbidden_requirement_hits'):
  return (t('matched_content_the_task_explicitly_forbids', 'Matched content the task explicitly forbids:') + f"{'、'.join(r['metrics']['forbidden_requirement_hits'])}。")
 if r['ok'] and 'stable_task_pass' in r['metrics']:
  m=r['metrics']
  if m.get('composite_acceptance_pass') is False:
   failed=[row['id'] for row in m.get('composite_group_results',[]) if not row.get('pass')]
   return t('combined_rule_not_met_2', 'Combined rule not met: {value}.', value='、'.join(failed) if failed else t('failed_rule_group_not_registered', 'Failed rule group not registered'))
  success=t('stable_content_and_structure_checks_passed', 'Stable content and structure checks passed.') if m.get('stable_anchor_results') or m.get('stable_page_pattern_results') else t('record_structure_passed_this_task_has_no', 'Record structure passed; this task has no stable-text checks.')
  if m.get('stable_task_schema') == 'body_text':
   return success if succeeded(r,c) else stable_failure_summary(m)+'。'
  counts=f"检出 {m.get('stable_record_count','—')} 条，字段完整 {m.get('stable_complete_record_count','—')} 条，完整且唯一 {m.get('stable_unique_record_count','—')} 条，要求至少 {m.get('stable_minimum_records','—')} 条。"
  return counts+(success if succeeded(r,c) else stable_failure_summary(m)+'。')
 if not r['ok']:
  if r.get('diagnosis'):return r['diagnosis']['evidence']
  names={'dns':t('a_domain_on_the_request_path_could', 'A domain on the request path could not be resolved'),'timeout':t('request_timed_out', 'Request timed out'),'network':t('network_failure_on_the_request_path', 'Network failure on the request path'),'api_error':t('provider_interface_error', 'Provider interface error'),'WEB_BLOCKED_URL':t('the_method_s_url_safety_rule_rejected', "The method's URL safety rule rejected the request")}
  return names.get(r.get('error_type'),r.get('error_type') or t('request_failure', 'Request failure'))+': '+_error_locator(r.get('error_message'))
 m=r['metrics']
 if m.get('text_encoding_corruption'):
  return t('the_returned_output_is_visibly_garbled_replacement', 'The returned output is visibly garbled: {get:,} replacement characters, {get2} of it; damaged text is not valid body text.', get=m.get('replacement_character_count', 0), get2=pct(m.get('replacement_character_ratio')))
 if m.get('body_region_results') and not m.get('body_coverage_pass'):
  regions=m['body_region_results'];failed=[]
  for index,item in enumerate(regions):
   if not item.get('target_pass',item.get('pass')):
    failed.append(f'{_position_label(index,len(regions)).split("（")[0]} {pct(item.get("coverage"))}')
  return '正文没有抓全；低于目标的位置为：'+'、'.join(failed)+'。下方会把完整标准答案与实际返回并排展示。'
 if r.get('diagnosis'):return r['diagnosis']['evidence']
 if c.get('task_kind')=='structure':
  return t('found_named_detail_entries', 'Found {get} named detail entries.', get=r['metrics'].get('named_entry_count', 0))+list_rule_description(c)
 if r['metrics'].get('page_identity_required') and r['metrics'].get('page_identity_status') != 'verified':
  return t('failed_final_page_identity_lacks_evidence_this', 'Failed: final page identity lacks evidence; this result remains in the planned task denominator.')
 if c.get('task_kind')=='file':return t('original_file_bytes_were_returned_and_passed', 'Original-file bytes were returned and passed format signature, length, and target SHA-256 validation.') if succeeded(r,c) else t('no_verifiable_original_file_was_returned_text', 'No verifiable original file was returned; text or repeating the input URL does not count as a file.')
 if c.get('task_kind')=='resource':
  missing=[x['id'] for x in r['metrics'].get('resource_results',[]) if not x['pass']]
  unexpected=r['metrics'].get('unexpected_resource_urls',[])
  if missing and unexpected:
   return ((t('target_resource_not_found', 'Target resource not found:') + f"{', '.join(missing)}" + t('also_returned_unexpected_resources', '; also returned unexpected resources: ') + f"{', '.join(unexpected)}"))
  if unexpected:return t('the_target_resource_was_found_but_answer_2', 'The target resource was found, but answer-bearing fields also returned unexpected resources: {join}', join=', '.join(unexpected))
  return t('the_required_resource_was_found', 'The required resource was found.') if not missing else t('target_resource_not_found_2', 'Target resource not found: {join}', join=', '.join(missing))
 m=r['metrics'];parts=[]
 if m.get('declared_truncation_hits'):parts.append(t('the_provider_declares_that_the_body_was', 'The provider declares that the body was truncated'))
 if not m.get('length_pass'):parts.append(t('body_too_short', 'Body too short'))
 missing=[x['id'] for x in m.get('fact_results',[]) if not x['pass']]
 if missing:parts.append((t('entry_fields_not_fully_matched', 'Entry fields not fully matched:') if any(x.get('match_scope')=='entry' for x in m.get('fact_results',[])) else t('missing_checks', 'Missing checks:'))+', '.join(missing))
 for x in m.get('pattern_results',[]):
  if not x['pass']:parts.append(t('only_items_required', '{id} only {count} items, {minimum} required', id=x['id'], count=x['count'], minimum=x['minimum']))
 return '；'.join(parts) or t('required_body_text_checks_passed_full_text', 'Required body-text checks passed; full-text coverage is reported separately.')

def stable_checks(m):
 checks=[]
 for x in m.get('stable_anchor_results',[]):
  checks.append(('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',[]))))+cell(t('pass_2', 'Pass') if x['pass'] else t('fail_2', 'Fail'))+cell(esc(' / '.join(x.get('matched',[]))))))
 for i,x in enumerate(m.get('stable_page_pattern_results',[]),1):
  checks.append(('',cell(t('page_pattern', 'Page pattern {i}', i=i))+cell(esc(x['pattern']))+cell(t('pass_2', 'Pass') if x['pass'] else t('fail_2', 'Fail'))+cell('—')))
 content=table([t('stable_content_checks', 'Stable-content checks'),t('requirement_2', 'Requirement'),t('verdict', 'Verdict'),t('observed_match', 'Observed match')],checks) if checks else ('<p>' + t('no_stable_text_checks_are_registered_for', 'No stable text checks are registered for this task, so no claim is made that its text content was validated.') + '</p>')
 records=[('',cell(esc(json.dumps(x.get('fields',{}),ensure_ascii=False)))+cell(t('pass_2', 'Pass') if x.get('valid') else t('fail_2', 'Fail'))+cell(esc('；'.join(x.get('errors',[])) or '—'))) for x in m.get('stable_records',[])]
 record_table=table([t('record_field', 'Record field'),t('structure_verdict', 'Structure verdict'),t('reason', 'Reason')],records)
 if len(records)>5:record_table=f'<div class="record-scroll" data-scroll-after="5">{record_table}</div>'
 return content+record_table+(('<p>' + t('failure_reason', 'Failure reason:') + f'{esc(stable_failure_summary(m))}。</p>') if m.get('stable_task_errors') else '')

def ranking(records,cases,track,base='',id=None,compact=False,audit=None,*,diagnostic=False):
 excluded={r['case_id'] for r in records if r['metrics'].get('evaluation_excluded')}
 records=[r for r in records if not r['metrics'].get('evaluation_excluded')]
 title_scope=track=='challenge' and any('title_quality' in r for r in records if report_family(cases[r['case_id']])==track)
 selected={k:c for k,c in cases.items() if report_family(c)==track and k not in excluded}
 text_track=track in {'web','dynamic','document','challenge','risk_control'} and any(c.get('task_kind','body') in {'body','structure'} for c in selected.values())
 groups=defaultdict(list)
 for r in records:
  if report_family(cases[r['case_id']])==track:groups[r['provider']].append(r)
 def sort_key(item):
  name,rs=item
  n=sum(measured(r,cases[r['case_id']]) for r in rs)
  return (-sum(succeeded(r,cases[r['case_id']]) for r in rs)/n if n else 1,name)
 rows=[];text_scopes={}
 for name,rs in sorted(groups.items(),key=sort_key):
  n=sum(measured(r,cases[r['case_id']]) for r in rs);ok=sum(bool(r['ok']) for r in rs);passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  body=[r for r in rs if cases[r['case_id']].get('task_kind','body')=='body'];bodypass=sum(succeeded(r,cases[r['case_id']]) for r in body)
  full=[r for r in rs if comparable_body_reference(cases[r['case_id']]) and r['metrics'].get('word_f1') is not None]
  def avg(key,full=full):
   per_case=defaultdict(list)
   for r in full:
    if r['metrics'].get(key) is not None:per_case[r['case_id']].append(r['metrics'][key] if r['ok'] else 0)
   return statistics.mean(statistics.mean(values) for values in per_case.values()) if per_case else None
  times=[r['elapsed_ms'] for r in rs if succeeded(r,cases[r['case_id']])]
  values=cell(link(base+f'providers/{name}.html',provider_label(name),browser_method_label(name,language='en')))+cell(rate(ok,len(rs)),ok/len(rs))
  if text_track:
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
 heads=[t('method', 'Method'),t('request_success_rate', 'Request success rate')]
 if text_track:
  heads+=[t('task_pass_rate', 'Task pass rate'),t('recall', 'Recall'),t('precision', 'Precision'),'F1',t('required_text_coverage', 'Required-text coverage')]
 else:heads+=[t('task_pass_rate', 'Task pass rate'),t('resource_match_rate', 'Resource match rate'),t('file_delivery_rate', 'File delivery rate'),t('metadata_pass_rate', 'Metadata pass rate')]
 heads+=[t('mean', 'Mean'),'P50','P95',t('judgement_coverage', 'Judgement coverage')]
 if audit:heads+=[t('review_coverage', 'Review coverage')]
 note=t('the_request_success_rate_uses_all_calls', 'The request success rate uses all calls as its denominator; the task pass rate uses decidable results, and missing acceptance evidence counts as neither pass nor failure.')
 if text_track:
  covered=sum(comparable_body_reference(c) for c in selected.values())
  note=f'完整参考覆盖 {covered}/{len(selected)}；缺少参考只影响全文指标，不影响任务结果。有参考时，请求失败计 0；请求成功但任务未通过仍按实际正文评分。'
  identity_rules=list(dict.fromkeys(list_rule_description(c) for c in selected.values()
   if c.get('task_kind')=='structure' and not c.get('acceptance_contract')))
  if identity_rules:note+=' '+esc(' '.join(identity_rules))
  note+=(' ' + t('required_text_coverage_measures_only_registered_required', 'Required-text coverage measures only registered required-text groups; it is not full-text recall. Tasks without text evidence are excluded from that average.'))
  if text_scopes and len(set(text_scopes.values()))==1:note+=(' ' + t('required_text_coverage_tasks_for_every_method', 'Required-text coverage: {count}/{count2} tasks for every method.', count=len(next(iter(text_scopes.values()))), count2=len(selected)))
  elif text_scopes:note+=(' ' + t('text_check_coverage_differs_by_method_see', 'Text-check coverage differs by method; see the coverage below.'))
  if track=='challenge':note+=(' ' + t('lists_stable_field_labels_and_descriptions_are', 'Lists, stable field labels, and descriptions are judged together; title-text quality is only a supporting diagnostic below.'))
  if track=='dynamic':note+=(' ' + t('legacy_snapshot_text_scores_containing_excluded_volatile', "Legacy snapshot-text scores containing excluded volatile fields remain in attempt details and are excluded from this table's full-text averages."))
 else:note=t('direct_file_tasks_require_byte_validation_any', 'Direct-file tasks require byte validation. Any missing evidence makes the report unreliable. Resource, file, and metadata columns are calculated on their corresponding task subsets.')
 note+=(' ' + t('judgement_coverage_is_judged_calls_divided_by', "Judgement coverage is judged calls divided by planned calls, not a pass rate. If any method is incomplete, the entire report is incomplete and publication is blocked.Mean, P50, and P95 include only each method's own passing calls and are not a matched-task speed ranking."))
 unmeasured=sorted({r['case_id'] for rs in groups.values() for r in rs if not measured(r,cases[r['case_id']])})
 if unmeasured:
  note+=(' ' + t('tasks_missing_acceptance_evidence', 'Tasks missing acceptance evidence:') + f"{'、'.join((link(base + 'datasets/' + key + '.html', cases[key]['name']) for key in unmeasured))}" + t('counts_as_neither_pass_nor_failure_the', '. Counts as neither pass nor failure; the pass-rate denominator includes only decidable results.'))
 if track=='dynamic':
  missing=sorted({r['provider'] for r in records}-set(groups))
  note+=(' ' + t('each_family_is_compared_within_its_own', 'Each family is compared within its own task scope and is not combined into a cross-family weighted score.'))
  if missing:note+=(' ' + t('unmeasured_methods_not_counted_as_failures', 'Unmeasured methods (not counted as failures):') + f"{esc('、'.join(missing))}。")
 directions={i:('quality_'+str(i),'max') for i in range(1,len(heads)) if heads[i] not in {t('mean', 'Mean'),'P50','P95',t('judgement_coverage', 'Judgement coverage'),t('review_coverage', 'Review coverage')}}
 directions.update({i:('latency_'+str(i),'min') for i,h in enumerate(heads) if h in {t('mean', 'Mean'),'P50','P95'}})
 rows=comparative_rows(rows,directions)
 result=table(heads,rows,id or 'ranking-'+track,True,'ranking',metrics=[] if compact else None,include_index=not diagnostic,default_sort=(2+int(not diagnostic),'desc'))
 if text_scopes and len(set(text_scopes.values()))>1:
  result=('<details><summary>' + t('text_check_coverage', 'Text-check coverage') + f"""</summary><ul>{''.join((f"<li>{esc(name)}：{len(scope)}/{len(selected)} 例；{esc('、'.join((selected[k]['name'] for k in sorted(scope))) or t('no_text_check_record', 'No text-check record'))}</li>" for name, scope in sorted(text_scopes.items())))}</ul></details>{result}""")
 if compact:
  result=('<details class="ranking-metric-help"><summary>' + t('metric_definitions_pass_rate_quality_and_latency', 'Metric definitions · pass rate, quality, and latency') + f'</summary>{metric_legend(heads)}</details>{result}')
 if title_scope:result+=title_diagnostics(groups,selected,base,id or 'ranking-'+track)
 result+=matched_latency(groups,cases,base)
 return task_pass_guide(track,base,selected)+'<p class="source-note">'+note+'</p>'+result

def matched_latency(groups,cases,base):
 scopes=[{r['case_id'] for r in rows if succeeded(r,cases[r['case_id']])} for rows in groups.values()]
 common=set.intersection(*scopes) if scopes else set()
 note=t('the_evaluated_methods_jointly_pass_tasks_latency', 'The evaluated methods jointly pass {count} tasks. Latency is compared only on this identical subset; it does not represent the cost of failed tasks or a production SLA.', count=len(common))
 rows=[]
 if common:
  for provider, records in sorted(groups.items()):
   bycase=defaultdict(list)
   for r in records:
    if r['case_id'] in common and succeeded(r,cases[r['case_id']]):bycase[r['case_id']].append(r['elapsed_ms'])
   times=[statistics.mean(v) for v in bycase.values()]
   rows.append(('',cell(esc(provider))+cell(ms(statistics.mean(times)))+cell(ms(percentile(times,.5)))))
 links='、'.join(link(base+'datasets/'+key+'.html',cases[key].get('name',key)) for key in sorted(common))
 return ('<details><summary>' + t('matched_task_latency_tasks_passed_by_every', 'Matched-task latency · tasks passed by every method') + f"</summary><p>{note}</p>{(table([t('method', 'Method'), t('mean_on_shared_tasks', 'Mean on shared tasks'), t('p50_on_shared_tasks', 'P50 on shared tasks')], rows, metrics=[]) + '<p>' + links + '</p>' if rows else '')}</details>")

def overview_all_call_latency(records,cases):
 excluded={r['case_id'] for r in records if r['metrics'].get('evaluation_excluded')}
 records=[r for r in records if r['case_id'] not in excluded]
 cases={key:case for key,case in cases.items() if key not in excluded}
 names=sorted({r['provider'] for r in records})
 keys={(r['provider'],r['case_id']) for r in records}
 if len(keys)!=len(records) or any((name,cid) not in keys for name in names for cid in cases):
  raise ValueError('All-call latency requires the full common task matrix')
 rows=[]
 for name in names:
  times=[r['elapsed_ms'] for r in records if r['provider']==name]
  rows.append(('',cell(link('providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell(str(len(times)))+
               cell(ms(statistics.mean(times)))+cell(ms(percentile(times,.5)))+cell(ms(percentile(times,.95)))))
 return ('<p>'+model_text(*pair('each_method_uses_the_same_tasks_including', 'Each method uses the same {count} tasks, including passes, failures, errors and timeouts. Latency covers one call from input to output, excluding cooldown intervals between calls. Each method executes cases sequentially; method concurrency and limits are documented in the call-chain audit. Local/remote execution, network conditions and native product limits still affect latency.', count=len(cases)))+'</p>'+
         table([t('method', 'Method'),t('calls', 'Calls'),t('all_call_mean', 'All-call mean'),t('all_call_p50', 'All-call P50'),t('all_call_p95', 'All-call P95')],rows,'all-call-latency-table',metrics=[]))


def result_ledger_section(out, records, prefix=''):
 path=out/'local-evidence/result-ledger.json'
 if not path.is_file():return ''
 ledger=validate_ledger(json.loads(path.read_text(encoding='utf-8')),records,source=out)
 rows=[('',cell(esc(row['id']))+cell(str(row['before_passes']))+cell(str(row['added_passes']))+
           cell(str(row['removed_passes']))+cell(str(row['totals']['pass']))+
           cell(str(row['totals']['excluded']))) for row in ledger['rounds']]
 return section(t('scoring_revision_ledger', 'Scoring revision ledger'),('<p>' + t('additions_removals_and_final_passes_are_derived', 'Additions, removals and final passes are derived from individual result states. Excluded results retain their original calls and stay outside comparison denominators.') + f"</p>{table([t('stage', 'Stage'), t('passes_before', 'Passes before'), t('passes_added', 'Passes added'), t('passes_removed', 'Passes removed'), t('passes_after', 'Passes after'), t('excluded_from_comparison', 'Excluded from comparison')], rows, metrics=[])}{link(prefix + 'local-evidence/result-ledger.json', t('download_per_result_revision_ledger', 'Download per-result revision ledger'))}"),'result-ledger')

def overview_matched_latency(records,cases):
 names=sorted({r['provider'] for r in records})
 keys={(r['provider'],r['case_id']) for r in records}
 if not names or len(keys)!=len(records) or any((name,cid) not in keys for name in names for cid in cases):
  return ('<p>' + t('the_call_matrix_is_incomplete_so_latency', 'The call matrix is incomplete, so latency on tasks passed by every method is not yet summarized.') + '</p>')
 common=set.intersection(*({r['case_id'] for r in records if r['provider']==name and succeeded(r,cases[r['case_id']])} for name in names))
 if not common:return ('<p>' + t('no_task_is_yet_passed_by_every', 'No task is yet passed by every method, so shared-task latency is not calculated.') + '</p>')
 population=_article_speed_population(cases,records,names,execution_scope='Main-evaluation historical calls across batches; all registered methods and configurations, not a same-window controlled experiment.')
 metrics=['mean_ms','p50_ms','p95_ms'];rows=[]
 minima={key:min(row[key] for row in population['rows']) for key in metrics}
 for row in population['rows']:
  values=cell(link('providers/'+row['provider']+'.html',provider_label(row['provider']),browser_method_label(row['provider'],language='en')))
  for key in metrics:
   value=ms(row[key]);values+=cell('<strong>'+value+'</strong>' if row[key]==minima[key] else value,row[key])
  rows.append(('',values))
 count=population['common_pass_count']
 note=(('<p>' + t('all_tested_configurations_pass', 'All {count} tested configurations pass', count=len(names)) + f' <strong>{count}/{len(cases)}</strong> ' + t('tasks_in_common_every_row_below_uses', 'tasks in common. Every row below uses this same task set; the lowest mean, P50, and P95 are shown in bold.') + '</p><p class="source-note">' + t('this_includes_all_methods_and_versions_registered', 'This includes all methods and versions registered in the report. Statistics use recorded primary calls and disclosed latency corrections across dates and batches. These values are not a controlled speed comparison within one time window and exclude failed-task latency.') + '</p>'))
 dates=[]
 for row in population['rows']:
  observed=row['recorded_start_dates']
  if observed:
   dates.append(model_text(provider_label(row['provider']),browser_method_label(row['provider'],language="en"))+model_text('：'+'、'.join(f'{date}（{number} 题）' for date,number in observed.items()),': '+', '.join(f'{date} ({number} tasks)' for date,number in observed.items())))
 links='、'.join(link('datasets/'+cid+'.html',cases[cid].get('name',cid)) for cid in population['common_pass_cases'])
 content=((f"{note}{table([t('method', 'Method'), t('mean_on_shared_tasks', 'Mean on shared tasks'), t('p50_on_shared_tasks', 'P50 on shared tasks'), t('p95_on_shared_tasks', 'P95 on shared tasks')], rows, 'overall-matched-latency-table', metrics=[])}<details><summary>" + t('shared_tasks_and_call_dates', 'Shared tasks and call dates') + f"</summary><p>{links}</p><p>{'<br>'.join(dates)}</p></details>"))
 return ('<details class="ranking-metric-help"><summary>' + t('view_shared_pass_latency_on_demand', 'View shared-pass latency on demand') + f'</summary>{content}</details>')

def overview_resources(out):
 path=out/'resource-cohort/resource-comparison.json'
 if not path.is_file():
  return ('<p>' + t('the_current_evidence_has_no_independent_client_2', 'The current evidence has no independent client-side resource measurement; CPU and memory are not inferred from latency or process size.') + '</p>')
 summary=json.loads(path.read_text(encoding='utf-8'))
 rendered,_=render_resource_comparison(summary,summary['providers'])
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
  coverage=t('tasks_3', '{count}/{total} · {count2}/{count3} tasks', count=count, total=total, count2=len({r['case_id'] for r in measured}), count3=len(selected))
  if not measured:coverage+=t('no_scorable_titles', ' (no scorable titles)')
  rows.append(('',values+cell(coverage)))
 returned=sum(r.get('title_quality',{}).get('returned_titles',0) for rs in groups.values() for r in rs)
 scored=sum(r.get('title_quality',{}).get('scored_titles',0) for rs in groups.values() for r in rs)
 cases_scored=len({r['case_id'] for rs in groups.values() for r in rs if r.get('title_quality',{}).get('f1') is not None})
 steps=(('<ol class="quality-steps"><li><b>' + t('identify_returned_records', 'Identify returned records') + '</b><span>' + t('read_titles_and_detail_urls_from_structured', 'Read titles and detail URLs from structured results: {returned} returned titles.', returned=returned) + '</span></li><li><b>' + t('join_references_by_detail_url', 'Join references by detail URL') + '</b><span>' + t('titles_join_to_independent_name_evidence_titles', '{scored}/{returned} titles join to independent name evidence; titles without matching evidence are not scored.', scored=scored, returned=returned) + '</span></li><li><b>' + t('compare_title_text', 'Compare title text') + '</b><span>' + t('calculate_title_recall_precision_and_f1_separately', 'Calculate title recall, precision, and F1 separately; formatting marks are not title content.') + '</span></li><li><b>' + t('aggregate_comparable_results', 'Aggregate comparable results') + '</b><span>' + t('coverage_tasks_this_is_a_title_diagnostic', 'Coverage: {cases_scored}/{count} tasks. This is a title diagnostic and does not determine task acceptance.', cases_scored=cases_scored, count=len(selected)) + '</span></li></ol>'))
 return (('<details class="ranking-metric-help"><summary>' + t('title_text_quality_method_and_results', 'Title-text quality · method and results') + '</summary><p>' + t('this_diagnostic_asks_whether_titles_that_were', 'This diagnostic asks whether titles that were returned are complete and clean. It does not measure list recall, freshness, or whole-page body quality.') + f"</p>{steps}{table([t('method', 'Method'), t('title_recall', 'Title recall'), t('title_precision', 'Title precision'), t('title_f1', 'Title F1'), t('title_reference_coverage_scorable_returned', 'Title reference coverage (scorable / returned)')], rows, identifier + '-titles', True, metrics=[])}</details>"))

def input_suite_comparison(dataset, cases, records):
 """Compare the selected input groups declared by the frozen source inventory."""
 sources=dataset.get('comparison',{}).get('input_sources',[])
 if not sources or not any('case_ids' in source for source in sources):return ''
 labels=dataset.get('comparison',{}).get('report_suite_labels',{})
 selected=set(cases);seen=set();groups=[]
 for index,source in enumerate(sources,1):
  ids=set(source.get('case_ids',[])) & selected
  if seen & ids:raise ValueError('Input suite comparison contains overlapping tasks')
  seen |= ids
  if ids:
   label=labels.get(source.get('path'),{})
   title=model_text(label.get('zh',t('input_group', 'Input group {index}', index=index)),label.get('en',f'Input group {index}'))
   groups.append((ids,title))
 if seen != selected:raise ValueError('Input suite comparison omits selected tasks')
 providers=sorted({row['provider'] for row in records});rows=[]
 for provider in providers:
  values=cell(link('providers/'+provider+'.html',provider_label(provider)))
  for ids,_ in groups:
   subset=[row for row in records if row['provider']==provider and row['case_id'] in ids]
   if {row['case_id'] for row in subset} != ids or len(subset)!=len(ids):
    raise ValueError('Input suite comparison requires one result per method and task')
   available=[row for row in subset if measured(row,cases[row['case_id']])]
   passed=sum(succeeded(row,cases[row['case_id']]) for row in available)
   value=f'{passed}/{len(available)} · '+rate(passed,len(available))
   if len(available)!=len(ids):value+=' · '+model_text(*pair('unscored_tasks', '{value} unscored tasks', value=len(ids) - len(available)))
   values+=cell(value,passed/len(available) if available else -1)
  rows.append(('',values))
 directions={index+1:(f'input-suite-{index+1}','max') for index in range(len(groups))}
 rows=comparative_rows(rows,directions)
 return section(model_text(*pair('comparison_by_input_group', 'Comparison by input group')),
  '<p>'+model_text(*pair('groups_follow_the_frozen_input_inventory_denominators', 'Groups follow the frozen input inventory; denominators count selected, scored tasks. These samples test particular scenarios and do not represent randomly sampled webpages.'))+'</p>'+
  table([model_text(*pair('method', 'Method'))]+[title+f' · n={len(ids)}' for ids,title in groups],rows,'input-suite-comparison',metrics=[]),
  'input-suite-comparison-section')


def overall_ranking(records,cases,audit=None,*,diagnostic=False,call_budget_seconds=None):
 excluded={r['case_id'] for r in records if r['metrics'].get('evaluation_excluded')}
 records=[r for r in records if not r['metrics'].get('evaluation_excluded')]
 cases={k:c for k,c in cases.items() if k not in excluded}
 has_unknown=any(not measured(r,cases[r["case_id"]]) for r in records)
 groups=defaultdict(list)
 for r in records:groups[r['provider']].append(r)
 rows=[]
 for name,rs in groups.items():
  n=sum(measured(r,cases[r['case_id']]) for r in rs)
  passed=sum(succeeded(r,cases[r['case_id']]) for r in rs)
  values=cell(link(f'providers/{name}.html',provider_label(name),browser_method_label(name,language='en')))+cell(rate(sum(bool(r['ok']) for r in rs),len(rs)),sum(bool(r['ok']) for r in rs)/len(rs))+cell(rate(passed,n)+(f'<br><small>{passed}/{n}</small>' if has_unknown else ''),passed/n if n else -1)
  if has_unknown:values+=cell(str(len(rs)-n))
  for track,_ in active_tracks(cases):
   subset=[r for r in rs if report_family(cases[r['case_id']])==track and measured(r,cases[r['case_id']])]
   values+=cell(f'{sum(succeeded(r,cases[r["case_id"]]) for r in subset)}/{len(subset)}',sum(succeeded(r,cases[r['case_id']]) for r in subset)/len(subset) if subset else -1)
  times=[r['elapsed_ms'] for r in rs]
  values+=cell(ms(statistics.mean(times)),statistics.mean(times))
  values+=cell(ms(percentile(times,.5)),percentile(times,.5))
  values+=cell(ms(percentile(times,.95)),percentile(times,.95))
  if audit:values+=review_cell(audit,rs)
  rows.append((passed/n if n else -1,name,('',values)))
 rows=[row for _,_,row in sorted(rows,key=lambda x:(-x[0],x[1]))]
 tracks=active_tracks(cases)
 heads=[t('method', 'Method'),t('request_success_rate', 'Request success rate'),t('overall_task_pass_rate', 'Overall task pass rate')]+([t('undecidable', 'Undecidable')] if has_unknown else [])+[label for _,label in tracks]+[t('mean_total_latency', 'Mean total latency'),t('p50_total_latency', 'P50 total latency'),t('p95_total_latency', 'P95 total latency')]
 if audit:heads+=[t('review_coverage', 'Review coverage')]
 case_count=len(cases);track_count=len(tracks);track_label=track_count_label(track_count)
 if case_count==150 and track_count==6:
  intro=('<p class="decision-lead"><b>' + t('overall_ranking_basis', 'Overall-ranking basis:') + '</b>' + t('every_method_runs_the_same_150_tasks', 'Every method runs the same 150 tasks. Each task contributes one pass or fail and the results are summed directly; latency is shown separately and does not affect pass-rate ranking. The overall ranking shows delivery coverage, while specific capabilities belong in the six family rankings.') + '</p>')
 else:
  intro=(('<p class="decision-lead"><b>' + t('overall_ranking_basis', 'Overall-ranking basis:') + '</b>' + t('every_method_runs_the_current_dataset_s', "Every method runs the current dataset's {case_count} planned tasks. Each task contributes one pass or fail and the results are summed directly; latency is shown separately and does not affect pass-rate ranking. The overall ranking shows delivery coverage, while specific capabilities belong in the {track_label} family rankings.", case_count=case_count, track_label=track_label) + '</p>'))
 if has_unknown:
  intro=('<p class="decision-lead"><b>' + t('overall_ranking_basis', 'Overall-ranking basis:') + '</b>' + t('every_method_calls_the_same_tasks_the', 'Every method calls the same {case_count} tasks. The pass rate is passes / decidable results; undecidable counts are listed separately and count as neither passes nor failures. Decidable denominators differ by method, so the ratio covers only what could be accepted and is not a definite pass rate across all tasks. Latency does not affect pass-rate order.', case_count=case_count) + '</p>')
 help=(('<details class="ranking-metric-help"><summary>' + t('overall_ranking_metrics', 'Overall-ranking metrics') + '</summary><div class="metric-help"><dl><div><dt>' + t('overall_task_pass_rate', 'Overall task pass rate') + '</dt><dd>' + t('passed_decidable_results_among_planned_tasks_each', 'Passed / decidable results among {case_count} planned tasks; each task has equal weight, and undecidable results are excluded from the denominator.', case_count=case_count) + '</dd></div><div><dt>' + t('passes_across_task_families', 'Passes across {track_label} task families', track_label=track_label) + '</dt><dd>' + t('passes_divided_by_tasks_in_each_family', 'Passes divided by tasks in each family, showing which capabilities contribute to the overall result.') + '</dd></div><div><dt>' + t('mean_p50_p95_total_latency', 'Mean / P50 / P95 total latency') + '</dt><dd>' + t('end_to_end_latency_for_each_method', 'End-to-end latency for each method across the same {case_count} planned tasks, including both passes and failures. These metrics do not affect pass-rate ranking. Primary-matrix calls span dates and batches and are not a same-window controlled speed comparison.', case_count=case_count) + '</dd></div><div><dt>' + t('review_coverage', 'Review coverage') + '</dt><dd>' + t('coverage_of_the_independent_blind_review_its', 'Coverage of the independent blind review, its protocol, and unresolved count.') + '</dd></div></dl></div></details>'))
 directions={i:('acceptance_'+str(i),'max') for i in range(1,3+len(tracks)+int(has_unknown))}
 directions.update({i:('all_latency_'+str(i),'min') for i in range(3+len(tracks)+int(has_unknown),6+len(tracks)+int(has_unknown))})
 if has_unknown:directions.pop(3,None)
 rows=comparative_rows(rows,directions)
 timing_note=('<p>' + t('total_latency_accumulates_all_physical_calls_for', 'Total latency accumulates all physical calls for each task, including failure retries and retry waits; each task remains one sample.'))
 if call_budget_seconds is not None:
  timing_note+=('<span>' + t('each_physical_call_has_a_second_budget', 'Each physical call has a {call_budget_seconds:g}-second budget; accumulated latency after retries can exceed it.', call_budget_seconds=call_budget_seconds) + '</span>')
 timing_note+='</p>'
 if diagnostic:
  intro=('<p class="decision-lead">' + t('local_uncertified_diagnostic_results_show_saved_task', 'Local uncertified diagnostic results show saved task judgments and latency, not product rankings. Passing does not establish clean content; also inspect full-text metrics and challenge indicators.') + '</p>')
  help=help.replace(t('overall_ranking_metrics', 'Overall-ranking metrics'),t('diagnostic_metric_definitions', 'Diagnostic metric definitions'))
  if _RENDER_CONTEXT.get().score_audit:
   intro=''
   help=help.replace(t('diagnostic_metric_definitions', 'Diagnostic metric definitions'),t('metric_definitions', 'Metric definitions'))
 rendered=table(heads,rows,'ranking-overall',True,'ranking',metrics=[],include_index=not diagnostic,default_sort=(2+int(not diagnostic),'desc'))
 return intro+timing_note+help+rendered

def filter_controls(options,all_first=()):
 controls=''
 for key,label,choices in options:
  ordered=([('',t('all', 'All'))]+choices) if key in {'provider','track','category','failureStage','failureCategory',*all_first} else (choices+[('',t('all', 'All'))])
  controls+=f'<label>{label} <select data-filter="{key}" aria-label="{label}">'+''.join(f'<option value="{esc(v)}"'+(' selected' if key=='result' and v=='fail' else '')+(f' data-report-en="{esc(browser_method_label(v,language="en"))}"' if key=='provider' and v else '')+f'>{esc(t)}</option>' for v,t in ordered)+'</select></label>'
 return (f'<div class="filters">{controls}<input data-filter="query" aria-label="' + t('search', 'Search') + '" placeholder="' + t('search_cases_methods_or_reasons', 'Search cases, methods, or reasons') + '"><span id="count" role="status" aria-live="polite"></span></div>')

def failure_filters(records,cases):
 categories=sorted({failure_category(r,cases[r['case_id']]) for r in records if measured(r,cases[r['case_id']]) and not succeeded(r,cases[r['case_id']])})
 return [('failureStage',t('failure_stage', 'Failure stage'),[('request',t('request_failure', 'Request failure')),('task',t('request_succeeded_task_failed', 'Request succeeded; task failed'))]),('failureCategory',t('failure_category', 'Failure category'),[(x,x) for x in categories])]

def comparison_rows(cases,bycase,names):
 rows=[]
 for case_id,c in cases.items():
  attempts={r['provider']:r for r in bycase.get(case_id,[])}
  passed=[name for name in names if name in attempts and measured(attempts[name],c) and succeeded(attempts[name],c)]
  failed=[name for name in names if name in attempts and measured(attempts[name],c) and not succeeded(attempts[name],c)]
  method_links=lambda selected,state,attempts=attempts:('<span class="outcome-list">'+''.join(f'<a class="outcome-pill {state}" href="{esc(safe_href("../attempts/"+attempts[name]["attempt_id"]+".html"))}">{model_text(provider_label(name),browser_method_label(name,language="en"))}</a>' for name in selected)+'</span>') if selected else '—'
  attrs=(dataset_attrs(c)+f' data-pass="{esc(" ".join(passed))}" data-fail="{esc(" ".join(failed))}"')
  task=(f'<div class="task-cell">{link("../datasets/"+case_id+".html",c["name"])}<div class="task-meta"><code class="tiny">{esc(case_id)}</code>'
        f'<span class="track-badge">{esc(case_track_label(c))}</span></div></div>')
  values=cell(task)+cell(method_links(passed,'pass'))+cell(method_links(failed,'fail'))
  rows.append((attrs,values))
 return rows

def comparison_table(rows,body_id):
 return table([t('task', 'Task'),t('passing_methods', 'Passing methods'),t('failing_methods', 'Failing methods')],rows,cls='comparison-table',metrics=[]).replace('<tbody>',f'<tbody id="{body_id}">',1)

def method_comparisons(cases,bycase,names):
 all_attempts=[attempt for attempts in bycase.values() for attempt in attempts]
 ordered=sorted(names,key=lambda name:(-sum(succeeded(attempt,cases[attempt['case_id']]) for attempt in all_attempts if attempt['provider']==name),provider_label(name)))
 if len(ordered)<2:return ('<p class="decision-lead">' + t('at_least_two_methods_are_needed_to', 'At least two methods are needed to compare shared tasks.') + '</p>')
 a=ordered[0];b=ordered[1]
 options=lambda selected:''.join(f'<option value="{esc(name)}"'+(' selected' if name==selected else '')+f' data-report-en="{esc(browser_method_label(name,language="en"))}">{esc(provider_label(name))}</option>' for name in ordered)
 tracks=('<option value="">' + t('all_families', 'All families') + f"""</option>{''.join((f'<option value="{esc(key)}">{esc(label)}</option>' for key, label in REPORT_TRACKS.items()))}""")
 rows=comparison_rows(cases,bycase,names)
 checks=''.join(f'<label><input type="checkbox" value="{esc(name)}" data-compare-method'+(' checked' if name==b else '')+f'> {model_text(provider_label(name),browser_method_label(name,language="en"))}</label>' for name in ordered)
 return (('<article class="method-comparison"><header class="comparison-heading"><div><h3>' + t('same_task_outcome_comparison', 'Same-task outcome comparison') + '</h3><p>' + t('use_method_a_as_the_baseline_and', 'Use Method A as the baseline and select one or more methods for a joint comparison. Each method name opens its complete evidence.') + '</p></div><b class="comparison-count" id="compare-count" role="status" aria-live="polite"></b></header><div class="comparison-controls"><label>' + t('method_a', 'Method A') + f'<select id="compare-a" data-compare-control>{options(a)}</select></label><label>' + t('comparison_condition', 'Comparison condition') + '<select id="compare-relation" data-compare-control><option value="mixed">' + t('outcomes_differ', 'Outcomes differ') + '</option><option value="a_pass_others_fail">' + t('a_passes_all_others_fail', 'A passes; all others fail') + '</option><option value="a_fail_others_pass">' + t('a_fails_all_others_pass', 'A fails; all others pass') + '</option><option value="all_pass">' + t('all_selected_methods_pass', 'All selected methods pass') + '</option><option value="all_fail">' + t('all_selected_methods_fail', 'All selected methods fail') + '</option><option value="exact_pass">' + t('only_the_selected_methods_pass', 'Only the selected methods pass') + '</option></select></label><label>' + t('task_family', 'Task family') + f'<select id="compare-track" data-compare-control>{tracks}</select></label></div><fieldset class="comparison-methods"><legend>' + t('methods_to_compare_multiple_selection', 'Methods to compare (multiple selection)') + f"""</legend><div class="method-checks">{checks}</div></fieldset><div class="comparison-result">{comparison_table(rows, 'compare-rows')}</div></article>"""))

def dataset_attrs(c):
 return f' data-track="{esc(report_family(c))}" data-category="{esc(c["category"])}"'

def case_attrs(r,c):
 content='na' if c.get('task_kind','body')!='body' else 'pass' if r['ok'] and r['metrics']['content_pass'] else 'fail'
 resources='na' if not c.get('expected_resources') else 'pass' if r['ok'] and r['metrics'].get('resource_pass') else 'fail'
 stage='' if succeeded(r,c) or not measured(r,c) else 'task' if r['ok'] else 'request'
 category='' if not stage else failure_category(r,c)
 result='excluded' if r['metrics'].get('evaluation_excluded') else 'unmeasured' if not measured(r,c) else 'pass' if succeeded(r,c) else 'fail'
 return dataset_attrs(c)+f' data-provider="{esc(r["provider"])}" data-transport="{"pass" if r["ok"] else "fail"}" data-result="{result}" data-content="{content}" data-resources="{resources}" data-failure-stage="{stage}" data-failure-category="{esc(category)}" data-search="{esc(str(c.get("id",r.get("case_id","")))+" "+str(c.get("name",""))+" "+str(c.get("url",""))+" "+reason(r,c))}"'

def attempt_row(r,c,base='../',*,label=None):
 state=result_label(r,c)
 values=cell(link(base+'attempts/'+r['attempt_id']+'.html',c['name'] if label is None else label))+cell(case_track_label(c))+cell(f'<span class="{"muted" if not measured(r,c) else "good" if succeeded(r,c) else "bad"}">{state}</span>')+cell(esc(failure_category(r,c)))+cell(ms(r['elapsed_ms']))
 values+=cell(f'<code class="tiny break">{esc(r.get("run_id") or execution_id(r))}</code>')
 return case_attrs(r,c),values

def local_call_chain_section(dataset, prefix="../"):
 audit=dataset.get('comparison',{}).get('call_chain_audit')
 if not audit:return ''
 rows=[]
 for item in audit.get('methods',[]):
  note=item['native_log_note']
  rows.append(('',cell(link(prefix+'providers/'+item['provider']+'.html',provider_label(item['provider']),browser_method_label(item['provider'],language='en')))+cell(esc(note))))
 intro=('<p>' + t('every_call_retains_harness_events_start_time', 'Every call retains harness events: start time, input parameters, return time, and errors. These appear under Caller events in each case detail.') + '</p><p>' + t('product_and_dependency_logs_contain_runtime_information', 'Product and dependency logs contain runtime information actually emitted by the product or underlying libraries. Harness events do not substitute for unavailable cloud-internal logs. The table lists what was retained for each method.') + '</p>')
 return section(t('logs_retained_in_this_run', 'Logs retained in this run'),(f"{intro}{table([t('method', 'Method'), t('product_and_dependency_log_coverage', 'Product and dependency log coverage')], rows, metrics=[])}<p>" + t('full_retained_logs_are_available_in_each', 'Full retained logs are available in each case detail. They help locate problems, but unrecorded internal processes cannot be inferred; a log file does not mean every internal process was recorded.') + '</p>'),'call-chain-audit')


def local_pass_rate_ranking(dataset):
 return dataset.get('comparison',{}).get('presentation',{}).get('ranking') == 'saved_task_pass_rate'


def local_audit_banner(dataset):
 if dataset.get('comparison',{}).get('publication_state')!='local_audit_only_not_for_release':
  return ''
 if measurement_presentation(dataset).local_matrix:
  if local_pass_rate_ranking(dataset):
   return '<p class="wrap source-note" data-evidence-state="local_measurement" data-ranking-scope="saved-task-pass-rate">'+model_text(*pair('ordered_by_pass_rate_on_the_listed', 'Ordered by pass rate on the listed tasks; repository certification is incomplete. See details for test dates and verification scope.'))+'</p>'
  note=t('review_records_are_available_see_the_review', 'Review records are available; see the review page for coverage and adjudication evidence.') if dataset['comparison'].get('independent_review') else t('results_are_machine_judgments_without_independent_human', 'Results are machine judgments without independent human review.')
  return ('<div class="wrap callout" role="alert" data-evidence-state="local_measurement"><strong>' + t('local_uncertified_diagnostic_snapshot_not_for_ranking', 'Local uncertified diagnostic snapshot · not for ranking') + '</strong><p>' + t('sources_were_not_fully_verified_before_and', 'Sources were not fully verified before and after calls. Saved scores, quality metrics and review records are diagnostic evidence, not a certified release.') + f'</p><p>{note}</p></div>')
 return '<div class="wrap callout"><strong>'+model_text(*pair('local_audit_report', 'Local audit report'))+'</strong><p>'+model_text(*pair('this_report_is_not_a_formal_release', 'This report is not a formal release. Consult the saved records for tool versions, call times and verification scope.'))+'</p></div>'


@with_measured_method_labels
@_scoped_render
def generate(out:Path,dataset:dict,records:list,manifests:list,*,include_english:bool=False,authored_interpretation:bool=True,presentation_only:bool=False,show_local_status_banners:bool=True,detail_attempt_ids:set[str]|None=None,dataset_case_ids:set[str]|None=None,frozen_scores:bool=False,english_routes=None,diagnostic_view_paths=()):
 _public_views=public_render_views(out,records)
 if english_routes is not None and not presentation_only:
  raise ValueError('Scoped English refresh requires presentation_only=True')
 for _record in records:
  if _record['attempt_id'] in _public_views:
   _record.setdefault('provider_metadata',{})['publication_views']=_public_views[_record['attempt_id']]
 if detail_attempt_ids is not None:
  if not presentation_only:
   raise ValueError('Scoped detail refresh requires presentation_only=True')
  unknown=set(detail_attempt_ids)-{row['attempt_id'] for row in records}
  if unknown:raise ValueError('Unknown detail refresh attempts: '+', '.join(sorted(unknown)))
 if dataset_case_ids is not None:
  if not presentation_only:raise ValueError('Scoped dataset refresh requires presentation_only=True')
  unknown=set(dataset_case_ids)-{case['id'] for case in dataset['cases']}
  if unknown:raise ValueError('Unknown dataset refresh cases: '+', '.join(sorted(unknown)))
 local_snapshot=dataset.get('comparison',{}).get('publication_state')=='local_audit_only_not_for_release'
 score_binding=dataset.get('comparison',{}).get('score_audit')
 score_audit=json.loads(contained_path(out,score_binding['path']).read_text(encoding='utf-8')) if score_binding else None
 diagnostic=bool(score_audit) or (local_snapshot and not local_pass_rate_ranking(dataset))
 if local_snapshot and not show_local_status_banners:
  raise ValueError('Uncertified local diagnostic status cannot be hidden')
 reliability=assess_reliability(dataset,records)
 if reliability['complete']:
  reliability_banner=''
 elif (measurement_presentation(dataset).explain_unjudged
       and dataset.get('comparison',{}).get('publication_state')=='local_audit_only_not_for_release'
       and not any(reliability[key] for key in ('missing','extra','duplicates'))):
  excluded=[r for r in records if r['metrics'].get('evaluation_excluded')]
  if len(excluded)==len(reliability['unjudged']):
   reliability_banner='<div class="wrap callout">'+model_text(*pair('results_are_excluded_across_all_methods_for', '{count} results are excluded across all methods for a verified evaluation-environment defect; original calls and outputs are retained.', count=len(excluded)))+'</div>'
  else:
   reliability_banner=('<div class="wrap reliability failure" role="alert"><strong>' + t('calls_are_complete_but_some_results_are', 'Calls are complete, but some results are undecidable') + f"</strong><span>{esc(t('all_calls_have_records_lack_sufficient_acceptance', 'All {present_calls} calls have records; {count} lack sufficient acceptance evidence. The pass rate uses decidable results, with the denominator and undecidable count shown separately. A local report is not full acceptance or formal publication.', present_calls=reliability['present_calls'], count=len(reliability['unjudged'])))}</span></div>")
 else:
  reliability_banner=('<div class="wrap reliability failure" role="alert"><strong>' + t('report_unreliable_evaluation_incomplete', 'Report unreliable: evaluation incomplete') + f"</strong><span>{esc('；'.join(reliability['errors']))}。本页排名不得用于结论或发布。</span></div>")
 if not show_local_status_banners and dataset.get('comparison',{}).get('publication_state')=='local_audit_only_not_for_release' and not any(reliability[key] for key in ('missing','extra','duplicates')):
  reliability_banner=''
 if show_local_status_banners:
  reliability_banner+=local_audit_banner(dataset)
 known=dataset.get('comparison',{}).get('call_chain_audit',{}).get('known_scoring_false_negatives',[])
 if known:
  reliability_banner+=('<div class="wrap callout" role="alert"><strong>' + t('confirmed_scoring_issue', 'Confirmed scoring issue') + f"</strong><p>{esc(t('table_results_were_misjudged_as_failures_by', '{count} table results were misjudged as failures by the scorer. Statistics keep the original verdict, and the current pass rate must not be used to rank products; see the call-chain checks.', count=len(known)))}</p></div>")
 if records and all(row.get('provider_type') == 'fixture' for row in records):
  reliability_banner += ('<div class="wrap callout" role="status"><strong>' + t('synthetic_example_not_a_formal_benchmark', 'Synthetic example · not a formal benchmark') + '</strong><p>' + t('content_and_failures_are_predefined_scenarios_for', 'Content and failures are predefined scenarios for checking installation and report functionality, not browser measurements.') + '</p></div>')
 elif dataset.get('comparison', {}).get('publication_state') == 'diagnostic_not_for_release':
  reliability_banner += ('<div class="wrap callout" role="status"><strong>' + t('diagnostic_run_not_a_formal_benchmark', 'Diagnostic run · not a formal benchmark') + '</strong><p>' + t('these_calls_check_the_selected_tasks_and', 'These calls check the selected tasks and methods without the cohort certification required for a formal benchmark.') + '</p></div>')
 _RENDER_CONTEXT.set(RenderContext(reliability_banner,include_english,score_audit))
 validate_report_ids(dataset,records)
 for path in ['assets/style.css','assets/report.js','references']:
  contained_path(out,path)
 for c in dataset['cases']:
  contained_path(out,'references/'+c['id']+'.txt')
 out.mkdir(parents=True,exist_ok=True);(out/'assets').mkdir(exist_ok=True)
 (out/'assets/style.css').write_text(STYLE+EXTRA, encoding='utf-8');(out/'assets/report.js').write_text(JS, encoding='utf-8')
 cases={c['id']:c for c in dataset['cases']};names=sorted(set(r['provider'] for r in records))
 tracks=active_tracks(cases);track_count=len(tracks);track_label=track_count_label(track_count)
 audit=json.loads((out/'result-review.json').read_text(encoding='utf-8')) if (out/'result-review.json').is_file() else None
 audited_attempts=review_index(audit)
 failure_reviews={}
 content_binding=dataset.get('comparison',{}).get('report_interpretation')
 if content_binding:
  article_content=json.loads(contained_path(out,content_binding['path']).read_text(encoding='utf-8'))
  failure_reviews=load_browser_failure_review(out,cases,records,article_content)
 bycase=defaultdict(list);byprovider=defaultdict(list)
 for r in records:bycase[r['case_id']].append(r);byprovider[r['provider']].append(r)
 cards=''
 for track,label in tracks:
  cards+=overview_card(records,cases,track,label,names,diagnostic=diagnostic)
 description=(t('tasks_methods_local_uncertified_diagnostic_snapshot', '{count} tasks · {count2} methods · local uncertified diagnostic snapshot.', count=len(cases), count2=len(names)) if diagnostic else t('tasks_methods_family_rankings_and_one_overall', '{count} tasks · {count2} methods · {track_label} family rankings and one overall coverage ranking.', count=len(cases), count2=len(names), track_label=track_label))
 if score_audit:
  description=f'{len(cases)} 个案例 · {len(names)} 种方法 · {track_label} 类任务。'
 if local_pass_rate_ranking(dataset):
  description=model_text(*pair('cases_methods_ordered_by_task_pass_rate', '{count} cases · {count2} methods · Ordered by task pass rate for this sample.', count=len(cases), count2=len(names)))
 release_path=out/'benchmark-release.json'
 if release_path.is_file():
  release=json.loads(release_path.read_text(encoding='utf-8'))
  description='<span>'+description+'</span> · <a data-benchmark-release="'+esc(release['version'])+'" href="benchmark-release.json">'+esc('bench v'+release['version'])+'</a>'
 home='<div class="home-page">'+hero(t('web_fetch_benchmark_report', 'Web Fetch benchmark report'),description)
 TITLE=t('call_chain_checks_and_log_scope_for', 'Call-chain checks and log scope for this run') if dataset.get('comparison',{}).get('call_chain_audit') else t('task_scope_and_acceptance_rules', 'Task scope and acceptance rules')
 if authored_interpretation and dataset.get('comparison',{}).get('report_interpretation'):
  binding=dataset['comparison']['report_interpretation']
  TITLE=json.loads(contained_path(out,binding['path']).read_text(encoding='utf-8'))['title']
 reading_note=t('use_score_differences_failure_evidence_and_implementation', 'Use score differences, failure evidence, and implementation boundaries to choose the right retrieval method.') if authored_interpretation else t('see_the_tasks_result_counts_and_acceptance', 'See the tasks, result counts and acceptance rules used in this report.')
 if dataset.get('comparison',{}).get('call_chain_audit'):
  reading_url='guide/index.html'
  home+=('<aside class="wrap home-reading" aria-label="' + t('interpretation', 'Interpretation') + '"><div><strong>' + t('interpretation', 'Interpretation') + f"</strong><p>{link(reading_url, t('after_a_web_fetch_succeeds_how_far', 'After a web fetch succeeds, how far is the content from usable?'))}</p></div><p>" + t('use_score_differences_failure_evidence_and_implementation', 'Use score differences, failure evidence, and implementation boundaries to choose the right retrieval method.') + f"</p>{link(reading_url, t('read_the_interpretation', 'Read the interpretation →'))}</aside>")
 if not dataset.get('comparison',{}).get('call_chain_audit'):home+=('<aside class="wrap home-reading" aria-label="' + t('interpretation', 'Interpretation') + '"><div><strong>' + t('interpretation', 'Interpretation') + f"</strong><p>{link('guide/index.html', TITLE)}</p></div><p>{reading_note}</p>{link('guide/index.html', t('read_the_interpretation', 'Read the interpretation →'))}</aside>")
 if dataset.get('comparison',{}).get('call_chain_audit'):
  TITLE=t('call_chain_checks_and_log_scope_for', 'Call-chain checks and log scope for this run')
  guide=section(t('how_to_read_these_results', 'How to read these results'),('<p>' + t('rates_apply_only_to_the_cases_and', 'Rates apply only to the cases and configurations in this report. Start with the overall and category rankings, then open individual cases to compare requirements with actual output. Request success means the method returned normally; task success means the returned content met every requirement for that case.') + '</p><p>' + t('see_methodology_for_scoring_and_timing_review', 'See Methodology for scoring and timing, Review history for checks and corrections, and Reproduce for method versions, fixed options and log coverage.') + '</p>'))
 else:
  guide=render_explainer(out,cases,records,table=table,cell=cell,link=link,authored=authored_interpretation)
 guide=render_archived_reading(out) or guide
 write(out,'guide/index.html',t('interpretation', 'Interpretation'),guide)
 panels=[]
 for track,label in tracks:
  selected=[c for c in cases.values() if report_family(c)==track]
  coverage=t('tasks_measured_methods', '{count} tasks · {count2} measured methods', count=len(selected), count2=len({r['provider'] for r in records if cases[r['case_id']].get('track', 'web') == track}))
  panels.append((track,label,f'<div class="track-heading"><h3>{label}</h3><span>{coverage}</span></div>'+ranking(records,cases,track,compact=True,audit=audit,diagnostic=diagnostic)+f"<p>{link('datasets/index.html#track-' + track, t('tasks_and_references', 'Tasks and references →'))}</p>"))
 overall_title=(t('task_judgments_not_a_ranking', 'Task judgments (not a ranking)') if diagnostic else model_text(*pair('task_pass_rate_ranking_for_this_sample', 'Task pass-rate ranking for this sample')) if local_pass_rate_ranking(dataset) else t('overall_coverage_ranking', 'Overall coverage ranking'))
 if score_audit:overall_title=t('task_results', 'Task results')
 budgets={m['timeout_seconds'] for m in manifests if m.get('timeout_seconds') is not None}
 home+=section(overall_title,overall_ranking(records,cases,audit,diagnostic=diagnostic,call_budget_seconds=next(iter(budgets)) if len(budgets)==1 else None),'overall-ranking')
 home+=input_suite_comparison(dataset,cases,records)
 if diagnostic and not score_audit:
  home+=result_ledger_section(out,records)
  low=[r for r in records if not r['metrics'].get('evaluation_excluded') and succeeded(r,cases[r['case_id']]) and cases[r['case_id']].get('ground_truth_main_content') and r['metrics'].get('word_precision') is not None and r['metrics']['word_precision']<.3]
  home+=section(t('content_diagnostics_for_passing_results', 'Content diagnostics for passing results'),('<p>' + t('passing_results_have_reference_word_precision_below', '{count} passing results have reference word precision below 30%. This locates inspection candidates, not a universal failure threshold. Passing does not establish clean body text; reference scope and time also affect this metric.', count=len(low)) + '</p>'),'coverage-quality')
 if measurement_presentation(dataset).all_call_latency:
  home+=section(t('all_call_latency_on_the_same_task', 'All-call latency on the same task set'),overview_all_call_latency(records,cases),'all-call-latency')
 home+=section(t('task_families', '{track_label} task families', track_label=track_label),(f'{review_summary(out)}<p class="callout success"><b>' + t('result_coverage', 'Result coverage:') + '</b>' + t('calls_have_results', '{expected_calls}/{expected_calls} calls have results.', expected_calls=str(reliability['expected_calls'])) + '</p><p class="source-note">' + t('compare_results_only_within_the_same_task', 'Compare results only within the same task family.') + f'</p><div class="grid home-overview-grid">{cards}</div>'),'overview')
 home+=section(t('latency_on_tasks_passed_by_every_method', 'Latency on tasks passed by every method'),overview_matched_latency(records,cases),'overall-matched-latency')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  home+=section(t('cpu_memory_and_network', 'CPU, memory, and network'),overview_resources(out),'overall-resources')
 home+=section(t('view_results_by_task_family', 'View results by task family') if score_audit else t('diagnostic_results_by_task_family', 'Diagnostic results by task family') if diagnostic else t('rankings_for_task_families', 'Rankings for {track_label} task families', track_label=track_label),(('<p>' + t('task_pass_rate_content_quality_and_latency', 'Task pass rate, content quality and latency are shown separately.') + '</p>') if score_audit else ('<p class="ranking-orientation">' + t('methods_are_sorted_by_task_pass_rate', 'Methods are sorted by task pass rate in descending order without ranks. Task judgments, content quality and latency are shown separately.') + '</p>') if diagnostic else ('<p class="ranking-orientation">' + t('family_rankings_show_specific_capabilities_they_default', 'Family rankings show specific capabilities. They default to task pass rate; select a column heading to sort. Text quality and pass rate are calculated separately.') + '</p>'))+tabs('home-rankings',panels),'results')
 write(out,'index.html',t('benchmark_report', 'Benchmark report'),home+'</div>')
 controls=filter_controls([('track',t('family', 'Family'),tracks),('category',t('family_2', 'Family'),[(x,x) for x in dict.fromkeys(c['category'] for c in cases.values())])])
 rows=[]
 for c in cases.values():
  verified=c.get('source_verification',{});state=t('access_restricted', 'Access restricted') if verified.get('state')=='restricted' else t('valid', 'Valid')
  rows.append((dataset_attrs(c),cell(link(c['id']+'.html',c['name']))+cell(esc(c['category']))+cell(link(c['url'],t('source_page_2', 'Source page ↗')))+cell(link(c['id']+'.html#facts',basis_label(c)))+cell(reference_label(c))+cell(state)))
 anchors=''.join(f'<span id="track-{t}"></span>' for t,_ in tracks)
 dataset_panels=[('cases',t('task_2', 'Task'),(f'{anchors}{controls}<p class="source-note">' + t('open_any_evidence_basis_for_details_restricted', "Open any evidence basis for details. Restricted access does not mean a site is down. Stable content and record structure are checked against registered contracts rather than frequently changing lists. Rendered-web body text is judged within each task's declared complete scope.") + f"</p>{table([t('task_2', 'Task'), t('family_2', 'Family'), 'URL', t('reference_basis', 'Reference basis'), t('reference_status', 'Reference status'), t('original_access', 'Original access')], rows, 'filtered-table')}")),('references',t('reference_status', 'Reference status'),'<span id="references"></span>'+reference_status(list(cases.values()))),('sources',t('sources_2', 'Source'),'<span id="sources"></span>'+sources_panel())]
 write(out,'datasets/index.html',t('dataset', 'Dataset'),hero(t('dataset', 'Dataset'),t('tasks_grouped_by_task_family', '{count} tasks, grouped by task family.', count=len(cases)))+section(t('tasks_and_evidence', 'Tasks and evidence'),tabs('dataset-hub',dataset_panels)))
 headers=[t('task_2', 'Task'),t('family', 'Family'),t('result', 'Result'),t('failure_class', 'Failure class'),t('elapsed_time', 'Elapsed time'),t('run_id', 'Run ID')]
 for c in ([case for case in cases.values() if case['id'] in (dataset_case_ids or set())] if presentation_only else cases.values()):
  v=c.get('source_verification',{});ref=reference_text(c)
  (out/'references').mkdir(exist_ok=True);(out/'references'/f'{c["id"]}.txt').write_text(ref, encoding='utf-8')
  if c.get('acceptance_contract'):
   target=contained_path(out,'contracts/'+c['id']+'.json');target.parent.mkdir(exist_ok=True)
   target.write_text(json.dumps(machine_contract(c),ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
  facts=[('',cell(esc(f['id']))+cell(esc(' / '.join(f['alternatives'])))) for f in c.get('required_facts',[])]
  resources=[('',cell(esc(x['id']))+cell('<br>'.join(link(u,u) for u in x.get('urls',[]))+(('<br>' + t('entry_url_rule', 'Entry URL rule:') + f"<code>{esc(' / '.join(x['url_patterns']))}</code>") if x.get('url_patterns') else ''))) for x in c.get('expected_resources',[])]
  rules=''.join(f'<p>{esc(x["id"])}：至少 {x.get("minimum",1)} 项；<code>{esc(x["pattern"])}</code></p>' for x in c.get('required_patterns',[]))
  status_note=f'<p class="callout">参考状态：{reference_label(c)}。</p>'
  if c.get('resource_output_contract',{}).get('unexpected')=='reject':
   status_note+=('<p class="callout">' + t('resource_output_contract_answer_bearing_image_fields', 'Resource output contract: answer-bearing image fields may contain only registered targets, aliases, or matching patterns; additional images fail the task.') + '</p>')
  if c.get('task_kind')=='structure':
   status_note=(f'<p class="callout">{esc(list_rule_description(c))}</p><p>' + t('task_verdict_and_latency_enter_the_primary', 'Task verdict and latency enter the primary ranking; full-text metrics are shown as — without a complete reference.') + '</p>')
   if (out/'list-references'/f'{c["id"]}.json').exists():status_note+='<p>'+link('../list-references/'+c['id']+'.json',t('name_and_url_validation_basis', 'Name and URL validation basis'))+'</p>'
   rules=('<div class="guide-grid"><article class="guide-card"><h3>' + t('registered_task_rules', 'Registered task rules') + f'</h3><p>{esc(list_rule_description(c))}</p></article><article class="guide-card"><h3>' + t('stable_content_and_changing_fields', 'Stable content and changing fields') + '</h3><p>' + t('stable_titles_sections_and_descriptions_are_checked', 'Stable titles, sections and descriptions are checked against registered content; frequently changing lists, entry titles, order, prices or IDs are not pinned to old snapshot values and are checked by declared field structure, type, count and uniqueness. Login pages, duplicate entries and empty links do not count.') + '</p></article></div>')
  if c.get('acceptance_contract'):
   facts=[]
   rules=stable_contract_details(c,evidence_root=out)
   if c.get('composite_acceptance'):
    rules+=composite_contract_details(c['composite_acceptance'])
  source_record=link('../sources/'+c['id']+'/evidence.json',t('retrieval_record', 'Retrieval record')) if (out/'sources'/c['id']/'evidence.json').exists() else t('no_source_page_snapshot_saved', 'No source-page snapshot saved')
  if c.get('track')=='dynamic':
   projected=c.get('stable_text_evidence') or {}
   if projected:
    source_record+=' · '+link('../'+projected['path'],t('independent_visible_text', 'Independent visible text'))+' · '+link('../'+projected['manifest_path'],t('source_verification_record', 'Source verification record'))
   elif (out/'sources'/c['id']/'mixed-session-stable-text.json').is_file():
    source_record+=' · '+link('../sources/'+c['id']+'/mixed-session-stable-text.json',t('independent_visible_text_and_source_verification_records', 'Independent visible text and source verification records'))
   elif all((out/'sources'/c['id']/name).is_file() for name in ('initial.html.txt','rendered.txt','render-audit.json')):
    source_record+=' · '+link('../sources/'+c['id']+'/initial.html.txt',t('initial_html', 'Initial HTML'))+' · '+link('../sources/'+c['id']+'/rendered.txt',t('rendered_body_text', 'Rendered body text'))+' · '+link('../sources/'+c['id']+'/render-audit.json',t('paired_evidence_validation', 'Paired-evidence validation'))
   observed=sorted({r['provider'] for r in bycase[c['id']]})
   status_note+=('<p class="source-note">' + t('methods_measured_on_dynamic_web_text', 'Methods measured on dynamic web text:') + f"{esc('、'.join(observed))}" + t('unmeasured_methods_do_not_count_as_failures', '. Unmeasured methods do not count as failures.') + '</p>')
  body=hero(esc(c['name']),('<strong>' + t('task_id_2', 'Task ID: ') + f"<code>{esc(c['id'])}</code></strong><br>{esc(task_instruction(c))}"))+section(t('reference_and_source', 'Reference and source'),(f"""{status_note}<p>{link(c['url'], t('open_task_source', 'Open task source ↗'))} · {link('../references/' + c['id'] + '.txt', t('download_reference', 'Download reference'))} · {source_record}</p><div class="evidence-pane"><header><h3>""" + t('reference_answer_required_deliverable', 'Reference answer / required deliverable') + '</h3><p>' + t('the_complete_material_used_for_the_verdict', 'The complete material used for the verdict appears below; long content scrolls within its pane.') + f'</p></header><div class="pane-body"><pre data-kind="reference">{esc(ref)}</pre></div></div><details class="technical-details"><summary>' + t('view_source_record', 'View source record') + f"</summary><p>{esc(v.get('method'))} · {esc(v.get('checked_at'))}</p><p>{esc(v.get('note'))}</p></details>"))
  body+=section(t('scoring_checks', 'Scoring checks'),(table([t('check_2', 'Check'),t('accepted_variants', 'Accepted variants')],facts) if facts else '')+rules+(table([t('resource', 'Resource'),t('accepted_urls', 'Accepted URLs')],resources) if resources else '')+(('<p>' + t('checks_that_a_verifiable_original_file_was', 'Checks that a verifiable original file was returned.') + '</p>') if c.get('task_kind')=='file' else ''),'facts')
  if c.get('task_kind')=='structure':
   example=out/'sources'/c['id']/'items.json'
   if example.exists():
    items=json.loads(example.read_text(encoding='utf-8'))
    sample=[('',cell(esc(x.get('title') or x.get('text','')))+cell(link(x['url'],t('details', 'Details ↗')) if x.get('url') else '—')) for x in items]
    body+=section(t('public_entry_snapshot', 'Public entry snapshot'),('<p class="source-note">' + t('public_fields_at_capture_time_are_kept', 'Public fields at capture time are kept as source evidence; the same list or order need not be returned, and stable tasks are judged by the content anchors and field requirements above.') + f"</p><details><summary>展开 {str(len(items))} 条</summary>{table([t('name_content', 'Name / content'), t('entry', 'Entry')], sample)}</details>"))
  if c.get('body_requirements'):
   rules=c['body_requirements']
   region_cards=[];total=len(rules['regions'])
   for index,region in enumerate(rules['regions']):
    label=_position_label(index,total)
    region_cards.append((f'<article class="region-card" id="reference-region-{index + 1}"><div class="region-head"><div><h3>{index + 1}. {esc(label)}</h3><p>' + t('this_is_the_text_from_the_same', 'This is the text from the same reference at this position, not a separate task.') + f"""</p></div></div><pre data-kind="reference">{esc(region['text'])}</pre></article>"""))
   if rules.get('version')==3:
    policy=(t('hard_floor_per_region_at_least_regions', 'Hard floor per region {minimum_floor}; at least {minimum_region_passes}/{count} regions reach {minimum_coverage}.', minimum_floor=pct(rules['minimum_floor']), minimum_region_passes=rules['minimum_region_passes'], count=len(rules['regions']), minimum_coverage=pct(rules['minimum_coverage'])))
   else:policy=f'每段至少达到 {pct(rules["minimum_coverage"])}。'
   body+=section(t('how_body_coverage_is_checked', 'How body coverage is checked'),('<p class="decision-lead"><b>' + t('this_is_one_complete_answer_not_tasks', 'This is one complete answer, not {total} tasks.', total=total) + '</b> ' + t('checking_only_the_title_and_a_few', "Checking only the title and a few keywords could let an opening-only result pass, so the same reference is checked by position. {policy} comparison ignores whitespace, layout symbols and Markdown link targets; each execution page shows each position's reference text and actual evidence side by side.", policy=policy) + f"</p>{''.join(region_cards)}"))
  body+='<p class="actions">'+link('../analysis/index.html#inspect?query='+c['id'],t('view_execution_results_for_all_methods', 'View execution results for all methods →'))+'</p>'
  write(out,'datasets/'+c['id']+'.html',c['name'],body)
 for name,rs in byprovider.items():
  write(out,'providers/'+name+'.html',name,provider_page_body(name,rs,cases,headers,tracks,native_default=measurement_presentation(dataset).native_outputs))
 detail_records=([r for r in records if r['attempt_id'] in (detail_attempt_ids or set())] if presentation_only else records)
 for r in detail_records:
  c=cases[r['case_id']];m=r['metrics'];ref=reference_text(c)
  output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
  rows=[('',cell(k)+cell(v)) for k,v in [(t('task_id', 'Task ID'),f'<code>{esc(c["id"])}</code>'),(t('method', 'Method'),link('../providers/'+r['provider']+'.html',provider_label(r['provider']),browser_method_label(r['provider'],language='en'))),(t('request', 'Request'),t('succeeded', 'Succeeded') if r['ok'] else t('failed', 'Failed')),(t('task_result', 'Task result'),result_label(r,c)),(t('http_status', 'HTTP status'),esc(r.get('status_code'))),(t('elapsed_time', 'Elapsed time'),ms(r['elapsed_ms'])),(t('full_text_recall_precision_f1', 'Full-text recall / precision / F1'),f'{pct(m.get("word_recall"))} / {pct(m.get("word_precision"))} / {pct(m.get("word_f1"))}'),(t('output_characters', 'Output characters'),f'{len(output):,}'),(t('output_checksum', 'Output checksum'),f'<code class="break">{esc(m.get("content_sha256"))}</code>')]]
  if not c.get('ground_truth_main_content'):
   rows[6]=('',cell(t('full_text_recall_precision_f1', 'Full-text recall / precision / F1'))+cell(t('not_measured_no_complete_full_text_reference', '— / — / — (not measured; no complete full-text reference)')))
  else:
   from harness.web_fetch.report_components import comparable_body_reference
   if not comparable_body_reference(c):
    rows[6]=('',cell(t('snapshot_text_diagnostic_recall_precision_f1', 'Snapshot-text diagnostic · recall / precision / F1'))+cell((f"{' / '.join((pct(m.get(k)) for k in ['word_recall', 'word_precision', 'word_f1']))}" + t('the_reference_includes_fields_without_fixed_values', '; the reference includes fields without fixed values, so it is only a historical snapshot diagnostic and stays out of the main-table full-text mean.'))))
  if r.get('status_code') is None:
   rows[4]=('',cell(t('http_status', 'HTTP status'))+cell(t('not_provided_by_the_interface', '— (not provided by the interface)')))
  if c.get('task_kind')=='file':
   rows[7]=('',cell(t('text_output_characters', 'Text-output characters'))+cell(t('file_bytes_listed_separately', '{count:,} (file bytes listed separately)', count=len(output))))
   rows[8]=('',cell(t('text_output_checksum', 'Text-output checksum'))+cell(f'<code class="break">{esc(m.get("content_sha256"))}</code>'))
  _answer_view=r.get('provider_metadata',{}).get('publication_views',{}).get('scored_answer')
  if _answer_view and _answer_view['original_sha256']!=_answer_view['published_sha256']:
   rows[7]=('',cell(model_text(*pair('public_view_characters', 'Public-view characters')))+cell(f'{len(output):,}'))
   rows[8]=('',cell(model_text(*pair('original_scored_output_hash', 'Original scored output hash')))+cell(f'<code class="break">{esc(m.get("content_sha256"))}</code>'))
   rows.append(('',cell(model_text(*pair('public_view_hash', 'Public-view hash')))+cell(f'<code class="break">{esc(_answer_view["published_sha256"])}</code>')))
  if 'content_check_total' in m:
   rows.append(('',cell(t('required_text_coverage', 'Required-text coverage'))+cell(pct(m.get('content_check_coverage'))+f' · {m["content_check_hits"]}/{m["content_check_total"]} 组')))
  if c.get('task_kind')=='resource':
   rows.append(('',cell(t('resource_recall_precision', 'Resource recall / precision'))+cell(
       f'{pct(m.get("resource_recall"))} / {pct(m.get("resource_precision"))}')))
   if m.get('unexpected_resource_urls'):
    rows.append(('',cell(t('unexpected_resources', 'Unexpected resources'))+cell('<br>'.join(
        f'<code class="break">{esc(url)}</code>' for url in m['unexpected_resource_urls']))))
  rows[3]=('',cell(t('task_result', 'Task result'))+cell(result_label(r,c)))
  if r.get('raw_request_status'):
   rows.append(('',cell(t('raw_adapter_status', 'Raw adapter status'))+cell(esc(json.dumps(r['raw_request_status'],ensure_ascii=False)))))
  rows.append(('',cell(t('scorer_sha_256', 'Scorer SHA-256'))+cell(esc(m.get('scoring_source_sha256')))))
  title_details=''
  if 'title_quality' in r:
   q=r['title_quality']
   rows.append(('',cell(t('title_recall_title_precision_title_f1', 'Title recall / title precision / title F1'))+cell(' / '.join(pct(q[k]) for k in ['recall','precision','f1']))))
   rows.append(('',cell(t('title_reference_coverage', 'Title reference coverage'))+cell(t('only_returned_titles_with_independent_references_are', '{scored_titles}/{returned_titles}; only returned titles with independent references are measured', scored_titles=q['scored_titles'], returned_titles=q['returned_titles']))))
   checks=[('',cell(esc(x['title']))+cell(esc(x['reference_used'] or t('no_independent_name_reference_not_scored', 'No independent name reference; not scored')))+cell(link(x['url'],t('details_2', 'Details')))+cell(' / '.join(pct(x[k]) for k in ['recall','precision','f1']))) for x in q['checks']]
   steps=(('<ol class="quality-steps"><li><b>' + t('parse_records', 'Parse records') + '</b><span>' + t('the_output_contains_titles_with_detail_urls', 'The output contains {returned_titles} titles with detail URLs.', returned_titles=q['returned_titles']) + '</span></li><li><b>' + t('join_independent_references', 'Join independent references') + '</b><span>' + t('titles_have_independent_name_references_joined_by', '{scored_titles}/{returned_titles} titles have independent name references joined by detail URL.', scored_titles=q['scored_titles'], returned_titles=q['returned_titles']) + '</span></li><li><b>' + t('compare_title_text_2', 'Compare title text') + '</b><span>' + t('recall_precision_f1', 'Recall / precision / F1: {join}.', join=' / '.join((pct(q[k]) for k in ['recall', 'precision', 'f1']))) + '</span></li><li><b>' + t('interpret_the_result', 'Interpret the result') + '</b><span>' + t('this_score_describes_only_the_text_quality', 'This score describes only the text quality of returned titles and does not determine task acceptance.') + '</span></li></ol>'))
   comparison=(table([t('returned_title', 'Returned title'),t('reference_title', 'Reference title'),t('record_identity', 'Record identity'),t('recall_precision_f1_2', 'Recall / precision / F1')],checks,metrics=[])
               if checks else ('<p class="empty">' + t('no_returned_titles_can_be_compared_item', 'No returned titles can be compared item by item, so an empty table is not shown.') + '</p>'))
   title_details=(f'{steps}<details><summary>' + t('per_title_comparison_and_source', 'Per-title comparison and source') + f"""</summary><p>{link('../' + q['reference_path'], t('independent_reference', 'Independent reference'))} · 采集时间 {esc(q.get('reference_checked_at'))}</p>{comparison}<details class="technical-details"><summary>""" + t('technical_checksums', 'Technical checksums') + '</summary><p>' + t('reference_sha_256', 'Reference SHA-256:') + f"""<code class="break">{esc(q['reference_sha256'])}</code></p><p>""" + t('scorer_sha_256_2', 'Scorer SHA-256:') + f"""<code class="break">{esc(q['scorer_sha256'])}</code></p></details></details>""")
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   rows[3]=('',cell(t('task_result', 'Task result'))+cell(result_label(r,c)))
   if c.get('source_benchmark',{}).get('verification_scope')=='first_three_name_detail_pairs':
    rows.append(('',cell(t('name_and_link_identity_check', 'Name-and-link identity check'))+cell(t('confirmed_this_does_not_establish_factual_accuracy', '{get} confirmed; this does not establish factual accuracy or currency.', get=m.get('confirmed_entries', 0)))))
   rows.append(('',cell(t('named_detail_records', 'Named detail records'))+cell(str(m.get('named_entry_count',0)))))
  if 'stable_task_pass' in m:
   for label,key in [(t('stable_content_check', 'Stable-content check'),'stable_content_pass'),(t('record_structure_check', 'Record-structure check'),'stable_structure_pass')]:
    no_checks=key=='stable_content_pass' and not (m.get('stable_anchor_results') or m.get('stable_page_pattern_results'))
    rows.append(('',cell(label)+cell(t('not_configured', '— (not configured)') if no_checks else '—' if m.get(key) is None else t('pass_2', 'Pass') if m[key] else t('fail_2', 'Fail'))))
   for label,key in [(t('structure_type', 'Structure type'),'stable_task_schema'),(t('records_found', 'Records found'),'stable_record_count'),(t('complete_records', 'Complete records'),'stable_complete_record_count'),(t('complete_unique_records', 'Complete unique records'),'stable_unique_record_count'),(t('declared_minimum_records', 'Declared minimum records'),'stable_minimum_records'),(t('declared_maximum_records', 'Declared maximum records'),'stable_maximum_records')]:
    rows.append(('',cell(label)+cell(esc(m.get(key) if m.get(key) is not None else '—'))))
  elif 'effective_entries' in m:
   minimum=m.get('minimum_entries',m.get('minimum'))
   rows.append(('',cell(t('valid_entries_declared_minimum', 'Valid entries / declared minimum'))+cell(esc(m['effective_entries'])+' / '+esc(minimum if minimum is not None else t('not_provided', 'Not provided')))))
  rows.extend(('',cell(k)+cell(f'<code class="break">{esc(v)}</code>')) for k,v in [(t('run_id', 'Run ID'),r.get('run_id') or execution_id(r)),(t('execution_record', 'Execution record'),r['attempt_id']),(t('started_utc', 'Started (UTC)'),r.get('started_at','—')),(t('provider_request_id', 'Provider request ID'),r.get('provider_metadata',{}).get('request_id') or t('not_returned', 'Not returned'))])
  scoring=r.get('scoring') or {}
  for label,key in [(t('scoring_method', 'Scoring method'),'mode'),(t('scored_at', 'Scored at'),'scored_at'),(t('rule_checksum', 'Rule checksum'),'rule_sha256')]:
   if scoring.get(key) is not None:
    value=t('offline_scoring_of_the_saved_output', 'Offline scoring of the saved output') if key=='mode' and scoring[key]=='offline_rescore' else scoring[key]
    rows.append(('',cell(label)+cell(esc(value))))
  checks=[('',cell(esc(x['id']))+cell(esc(' / '.join(x.get('alternatives',x.get('expected_urls',[])))))+cell(t('matched', 'Matched') if x['pass'] else t('entry_not_fully_matched', 'Entry not fully matched') if x.get('match_scope')=='entry' else t('missing', 'Missing'))+cell(esc(' / '.join(x.get('matched',x.get('matched_urls',[])))))) for x in m.get('fact_results',[])+m.get('resource_results',[])]
  if c.get('task_kind')=='structure' and 'stable_task_pass' not in m:
   checks=[('',cell(link(x['url'],x['title']))+cell(esc(' / '.join(x['reference_titles']) or t('no_matching_independent_name_evidence', 'No matching independent name evidence')))+cell(t('confirmed', 'Confirmed') if x['verdict']=='confirmed' else t('access_restricted_unconfirmed', 'Access restricted · unconfirmed') if x.get('access_restricted') else t('unconfirmed', 'Unconfirmed'))+cell(esc(' · '.join(filter(None,[x.get('evidence_checked_at'),x.get('access_note')]))))) for x in m.get('list_checks',[])]
  caveat=''
  if c.get('track')=='dynamic':caveat=('<p class="source-note">' + t('judged_by_the_rules_actually_registered_for', 'Judged by the rules actually registered for this task. "Entry not fully matched" means no complete field combination was found, not that every field is absent. Stable titles, sections and descriptions are checked against registered content; frequently changing lists and prices are checked by field structure, type, count and uniqueness, not pinned to old snapshot values. Full-text metrics are computed separately only with a complete, comparable reference and do not prove changing values accurate or current.') + '</p>')
  if c.get('task_kind')=='structure':
   caveat='<p class="source-note">'+esc(list_rule_description(c))+link('../datasets/'+c['id']+'.html',t('rules_and_independent_reference', 'Rules and independent reference'))+'</p>'
  if 'stable_task_pass' in m:
   caveat=('<p class="source-note">' + t('this_task_is_judged_jointly_on_stable', 'This task is judged jointly on stable content and record structure. Full-text metrics require a complete comparable reference and cannot be inferred from record counts.') + '</p>')
  detail_metrics=[t('request', 'Request'),t('result', 'Result'),t('elapsed_time', 'Elapsed time'),t('http_status', 'HTTP status'),t('output_characters', 'Output characters'),t('output_checksum', 'Output checksum')]+([t('named_detail_records', 'Named detail records')] if c.get('task_kind')=='structure' else [t('recall', 'Recall'),t('precision', 'Precision'),'F1'] if c.get('ground_truth_main_content') else [])
  state=result_label(r,c);passed=succeeded(r,c)
  provider_meta=r.get('provider_metadata',{})
  route_english=None
  if provider_meta.get('document_extractor'):
   route_label=t('route_the_method_obtains_the_original_document', 'Route: the method obtains the original document directly → {document_extractor} → readable body text', document_extractor=provider_meta['document_extractor'])
  elif c.get('task_kind')=='file':
   route_label=t('retrieval_path_method_obtains_original_file_format', 'Retrieval path: method obtains original file → format, length, and SHA-256 validation')
  elif provider_meta.get('output_contract') in report_method_catalog()['output_contract_labels']:
   contract=provider_meta['output_contract']
   route_label=report_method_catalog()['output_contract_labels'][contract].format(method=provider_label(r['provider']))
   route_english=report_method_catalog('en')['output_contract_labels'][contract].format(method=browser_method_label(r['provider'],language='en'))
  else:
   extractor=str(provider_meta.get('extractor') or '')
   route_label=(t('retrieval_path', 'Retrieval path: ') + f"{ROUTE_LABELS.get(extractor, extractor or t('tested_method_returns_body_text_directly', 'Tested method returns body text directly'))}")
   route_english='Retrieval path: '+report_method_catalog('en')['route_labels'].get(extractor,extractor or 'Text returned directly by the method')
  delivered_summary=provider_meta.get('file_evaluation',{}).get('files',[])
  actual_summary=(f'实际交付：{len(delivered_summary)} 个文件、{sum(x.get("bytes",0) for x in delivered_summary):,} 字节'
                  if c.get('task_kind')=='file' else f'实际输出：{len(output):,} 字符')
  hero_meta=(('<strong>' + t('task_id_2', 'Task ID: ') + f"""<code>{esc(c['id'])}</code></strong><br>{esc(task_instruction(c))}<span class="meta"><span class="pill">""" + t('method_2', 'Method: {model_text}', model_text=model_text(provider_label(r['provider']), browser_method_label(r['provider'], language='en'))) + '</span><span class="pill">' + t('run_id_2', 'Run ID: {value}', value=esc(r.get('run_id') or execution_id(r))) + '</span></span>'))
  summary=((f"""<div class="attempt-verdict {('' if passed or not measured(r, c) else 'failure')}"><span class="status-line">{esc(state)}</span><p>{esc(reason(r, c) if passed or not measured(r, c) else (r.get('diagnosis') or {}).get('category') or t('task_requirements_not_met', 'Task requirements not met'))}</p><p class="source-note">""" + t('method_3', 'Method: ') + f"{link('../providers/' + r['provider'] + '.html', provider_label(r['provider']), browser_method_label(r['provider'], language='en'))} · 请求：{(t('succeeded', 'Succeeded') if r['ok'] else t('failed', 'Failed'))} · 耗时：{ms(r['elapsed_ms'])} · {actual_summary}</p></div>"))
  summary+=f'<p class="source-note">{model_text(route_label,route_english)}</p>'
  summary+=(('<p class="actions"><a href="#evidence-reference">' + t('view_the_reference_used_for_scoring', 'View the reference used for scoring') + '</a><a href="#evidence-output">' + t('view_the_complete_saved_output', 'View the complete saved output') + '</a><a href="#decision">' + t('view_item_level_verdicts', 'View item-level verdicts') + '</a><a href="#run-log">' + t('view_run_logs_and_evidence', 'View run logs and evidence') + '</a></p>'))
  if r.get('diagnosis') and not passed and measured(r,c):
   d=r['diagnosis'];s=d.get('independent_source_check') or {}
   quote_issue=_quote_field_issue(m)
   facts=[(t('failure_class', 'Failure class'),t('field_boundaries_lost', 'Field boundaries lost') if quote_issue else d['category']),
          (t('stage_2', 'Stage'),t('field_parsing', 'Field parsing') if quote_issue else d['stage']),
          (t('decision_basis', 'Decision basis'),reason(r,c)),(t('attribution_boundary', 'Attribution boundary'),d['certainty'])]
   if s:facts.extend([(t('independent_source_page_check', 'Independent source-page check'),f"{s.get('checked_at', '')} · HTTP {s.get('status', t('not_obtained', 'Not obtained'))} · {s.get('title') or s.get('error', '')}"),(t('recheck_boundary', 'Recheck boundary'),d['source_check_boundary'])])
   summary+=('<details class="technical-details"><summary>' + t('view_failure_class_and_attribution_boundary', 'View failure class and attribution boundary') + f"</summary>{table([t('field_2', 'Field'), t('evidence_2', 'Evidence')], [('', cell(esc(k)) + cell(esc(v))) for k, v in facts], metrics=[])}</details>")
  body=hero(esc(c['name']),hero_meta)+section(t('decision', 'Conclusion'),summary,'overview')
  body+=section(t('target_coverage_and_additional_content', 'Target coverage and additional content'),render_quality_observations(r,c,output,esc=esc),'quality-diagnostics')
  reviewed=audited_attempts.get(r['attempt_id'])
  if reviewed:
   independent={'pass':t('pass_2', 'Pass'),'fail':t('fail_2', 'Fail'),'insufficient':t('insufficient_evidence', 'Insufficient evidence')}.get(reviewed.get('agent_verdict'),t('not_recorded', 'Not recorded'))
   final={'pass':t('pass_2', 'Pass'),'fail':t('fail_2', 'Fail'),'unavailable':t('undecidable', 'Undecidable')}.get(reviewed.get('final_verdict'),t('not_adjudicated_the_current_score_stands', 'Not adjudicated; the current score stands'))
   review_rows=[
    (t('independent_review_verdict', 'Independent review verdict'),independent),
    (t('direct_evidence', 'Direct evidence'),reviewed.get('agent_evidence') or '—'),
    (t('final_verdict', 'Final verdict'),final),
   ]
   adjudication_en=None
   if reviewed.get('adjudication_evidence'):
    proof=reviewed['adjudication_evidence']
    adjudication_en=proof.get('reason_en')
    if not adjudication_en and proof.get('rule_assertions'):
     adjudication_en=adjudication_reason(proof['rule_assertions'],language='en')
    review_rows.append((t('disagreement_adjudication', 'Disagreement adjudication'),proof.get('summary_zh',reviewed['adjudication'])))
   body+=section(t('independent_result_review', 'Independent result review'),table(
       [t('field_2', 'Field'),t('review_record', 'Review record')],
       [('',cell(esc(label))+cell(
           model_text(value,adjudication_en) if label==t('disagreement_adjudication', 'Disagreement adjudication')
           else '<span translate="no">'+esc(value)+'</span>' if label==t('direct_evidence', 'Direct evidence') else esc(value))) for label,value in review_rows],
       metrics=[]),'independent-review')
   if reviewed.get('adjudication_evidence',{}).get('summary_zh'):
    body+='<details><summary>'+model_text(*pair('full_adjudication_record_original', 'Full adjudication record (original)'))+'</summary><pre translate="no">'+esc(reviewed['adjudication'])+'</pre></details>'
  failure_review=failure_reviews.get(r['attempt_id'])
  if failure_review:
   cause_body='<p><strong>'+esc(failure_review['category_label'])+'</strong></p><p>'+esc(failure_review['reason'])+'</p>'
   cause_body+=('<p>' + t('resolution', 'Resolution: {remediation_status}', remediation_status=esc(failure_review['remediation_status'])) + f"</p><p>{link('../guide/index.html#browser-' + r['provider'], t('similar_failures_and_topic_analysis', 'Similar failures and topic analysis'))}</p>")
   cause_body+=('<details><summary>' + t('topic_review_basis', 'Topic review basis') + f"</summary><ul>{''.join(('<li>' + link('../' + item['path'], '证据 ' + str(index)) + '</li>' for index, item in enumerate(failure_review['evidence_paths'], 1)))}</ul></details>")
   body+=section(t('topic_check_of_failure_causes', 'Topic check of failure causes'),cause_body,'failure-review')
  reference_heading=t('complete_reference_answer', 'Complete reference answer') if c.get('task_kind')!='structure' else t('reference_and_acceptance_contract', 'Reference and acceptance contract')
  reference_download='../references/'+c['id']+'.txt'
  source_body=f'<pre data-kind="reference">{esc(ref)}</pre>'
  source_meta=f"{len(ref):,} 字符 · {link(reference_download, t('download_complete_reference', 'Download complete reference'))}"
  if not (c.get('ground_truth_main_content') or c.get('reference_answer')) and c.get('task_kind','body')=='body':
   reference_heading=t('acceptance_rules_no_full_text_reference', 'Acceptance rules (no full-text reference)')
   source_meta=t('full_text_recall_precision_and_f1_do', '{link}; full-text recall, precision and F1 do not apply.', link=link(reference_download, t('download_acceptance_rules', 'Download acceptance rules')))
  if (c.get('track','web')=='web' and c.get('task_kind','body')=='body'
      and c.get('required_facts') and not c.get('acceptance_contract')):
   rules={key:c[key] for key in ('min_content_chars','expected_any','required_facts',
          'fact_coverage_threshold','required_patterns','body_requirements') if key in c}
   source_body=required_text_answer_panel(c)
   if c.get('ground_truth_main_content') or c.get('reference_answer'):
    source_body+=('<h4>' + t('complete_reference_body', 'Complete reference body') + f'</h4><pre data-kind="reference">{esc(ref)}</pre>')
   source_body+=(('<details class="technical-details"><summary>' + t('view_raw_acceptance_rules', 'View raw acceptance rules') + f'</summary><pre data-kind="contract">{esc(json.dumps(rules, ensure_ascii=False, indent=2))}</pre></details>'))
  delivered=r.get('provider_metadata',{}).get('file_evaluation',{}).get('files',[])
  if 'stable_task_pass' in m and c.get('acceptance_contract'):
   reference_download='../contracts/'+c['id']+'.json'
   contract_text=json.dumps(machine_contract(c),ensure_ascii=False,indent=2)
   source_body=((f"""{stable_answer_panel(c['acceptance_contract'])}{(composite_contract_details(c['composite_acceptance']) if c.get('composite_acceptance') else '')}<details class="technical-details"><summary>""" + t('view_machine_readable_contract', 'View machine-readable contract') + f'</summary><pre data-kind="contract">{esc(contract_text)}</pre></details>'))
   source_meta=(t('the_required_text_and_record_counts_are', 'The required text and record counts are shown directly; readers do not need to inspect JSON.')
                if c['acceptance_contract'].get('stable_text_role','required')!='diagnostic' else
                t('this_section_lists_the_body_acceptance_rules', 'This section lists the body acceptance rules and text used to check retrieval scope; scope-check text does not affect acceptance.'))
  corruption=''
  if m.get('text_encoding_corruption'):
   corruption=(('<p class="callout failure encoding-warning"><b>' + t('the_returned_text_contains_decoding_corruption', 'The returned text contains decoding corruption:') + '</b>' + t('contains_characters_of_it_these_characters_were', 'Contains {get:,} � characters, {get2} of it. These characters were lost before saving and cannot be restored from the current Markdown.', get=m.get('replacement_character_count', 0), get2=pct(m.get('replacement_character_ratio'))) + '</p>'))
  answer_view=r.get('provider_metadata',{}).get('publication_views',{}).get('scored_answer')
  output_note=(f"{len(output):,} 字符 · {link('../' + r['artifact_path'], t('download_raw_output', 'Download raw output'))}")
  if answer_view and answer_view['original_sha256']!=answer_view['published_sha256']:
   output_note=(f"{len(output):,} 字符 · {link('../' + answer_view['path'], t('download_public_copy', 'Download public copy'), 'Download public view')} · {link('../' + answer_view['receipt'], t('view_redacted_record', 'View redacted record'), 'View redaction record')}<br>{model_text(*pair('paths_or_sensitive_values_are_hidden_scores', 'Paths or sensitive values are hidden; scores come from the original. Original/public hashes are listed in the run fields.'))}")
  if r.get('scoring',{}).get('mode')=='published_snapshot_rescore':
   view=r['scoring']['artifact_view']
   view_label={'original':t('saved_output_identical_to_the_original_hash', 'Saved output identical to the original hash'),'recovered_original':t('output_restored_by_the_original_hash_this', 'Output restored by the original hash (this page shows the public copy)'),'published_projection':t('public_redacted_copies', 'Public redacted copies')}[view]
   output_note=t('characters_this_scoring_input', '{count:,} characters · this scoring input: {view_label} · {link}', count=len(output), view_label=esc(view_label), link=link('../' + r['artifact_path'], t('download_public_output', 'Download public output')))
   output_note+=('<br>' + t('original_call_output_sha_256', 'Original call output SHA-256:') + f"""<code class="break">{esc(r['scoring']['original_content_sha256'])}</code><br>""" + t('this_scoring_input_sha_256', 'This scoring input SHA-256:') + f"""<code class="break">{esc(r['scoring']['scored_content_sha256'])}</code>""")
  if readable_output(output)!=output:output_note+=' · 控制字节在页面中显示为 \\xNN'
  actual_body=(corruption+f'<pre data-kind="output">{esc(readable_output(output))}</pre>' if output else
               f'<div class="call-log-empty">{esc(empty_output_reason(r,c,output))}</div>')
  if c.get('task_kind')=='file':
   reference_heading=t('target_file', 'Target file')
   expected_hash=c.get('source_verification',{}).get('file_sha256') or t('not_registered', 'Not registered')
   source_meta=t('this_task_validates_original_file_bytes_markdown', 'This task validates original-file bytes; Markdown is not required.')
   source_body=(('<div class="answer-list"><div class="answer-item"><b>' + t('file_requirements', 'File requirements') + f'</b><blockquote translate="no">{esc(ref)}</blockquote></div><div class="answer-item"><b>' + t('target_sha_256', 'Target SHA-256') + f'</b><code class="break">{esc(expected_hash)}</code></div></div>'))
   if delivered:
    actual_rows=[('',cell(esc(f.get('mime') or t('unknown_format', 'Unknown format')))+cell(f'{f.get("bytes",0):,}')+
                  cell(f'<code class="break">{esc(f.get("sha256"))}</code>')+cell(t('match', 'Match') if f.get('pass') else t('not_matched_2', 'Not matched'))) for f in delivered]
    actual_body=table([t('detected_format', 'Detected format'),t('bytes_2', 'Bytes'),'SHA-256',t('target_check', 'Target check')],actual_rows,metrics=[])
    output_note=f"实际交付 {len(delivered)} 个文件 · {link('../' + delivered[0]['artifact_path'], t('download_delivered_file', 'Download delivered file'))}"
   else:
    actual_body=('<p class="empty">' + t('this_call_did_not_deliver_verifiable_original', 'This call did not deliver verifiable original-file bytes.') + '</p>')
    output_note=t('0_files_delivered', '0 files delivered')
  reference_scope=('' if not (c.get('track')=='document' and 0 < len(ref) < 100) else
                   ('<p class="callout"><b>' + t('reference_scope', 'Reference scope:') + '</b>' + t('this_is_a_minimal_document_parsing_sample', 'This is a minimal document-parsing sample. All extractable body text of the frozen source is {count:,} characters, and the reference omits nothing. It only verifies short document text extraction, not multi-page, table or appendix completeness.', count=len(ref)) + '</p>'))
  evidence_intro=t('the_left_side_is_the_reference_or', 'The left side is the reference or explicit acceptance contract used for the verdict. The right side is the complete output saved from this call. Neither side is a summary; long content scrolls within its pane.')
  if answer_view and answer_view['original_sha256']!=answer_view['published_sha256']:
   evidence_intro=model_text(*pair('the_left_pane_contains_the_scoring_reference', 'The left pane contains the scoring reference or acceptance rules. The right pane contains the public output view with paths or sensitive values hidden. Scores retain the original verdict; original and public hashes are identified separately. Long content scrolls within each pane.'))
  evidence=((f'<p class="evidence-intro">{evidence_intro}</p>{reference_scope}<div class="evidence-compare"><article class="evidence-pane" id="evidence-reference"><header><h3>{esc(reference_heading)}</h3><p>{source_meta}</p></header><div class="pane-body">{source_body}</div></article><article class="evidence-pane" id="evidence-output"><header><h3>' + t('actual_output', 'Actual output') + f'</h3><p>{output_note}</p></header><div class="pane-body">{actual_body}</div></article></div>'))
  body+=section(t('reference_and_actual_output', 'Reference and actual output'),evidence,'evidence')
  checks_html=''
  if 'stable_task_pass' in m:
   checks_html+=stable_anchor_evidence(m,output)
   checks_html+=(('<p class="decision-lead"><b>' + t('structure_result', 'Structure result:') + f"</b>实际解析出 {m.get('stable_unique_record_count', 0)} 条完整且不重复的记录，要求至少 {m.get('stable_minimum_records', '—')} 条；结构检查{(t('pass_2', 'Pass') if m.get('stable_structure_pass') else t('fail_2', 'Fail'))}，稳定文字检查{(t('pass_2', 'Pass') if m.get('stable_content_pass') else t('fail_2', 'Fail'))}。</p>"))
   checks_html+=('<details class="technical-details"><summary>' + t('view_all_parsed_records_and_machine_check', 'View all parsed records and machine-check fields') + f'</summary>{stable_checks(m)}</details>')
   checks_html+=composite_result_evidence(m)
  else:
   checks_html+=(table([t('check_2', 'Check'),t('reference_answer', 'Reference answer'),t('verdict', 'Verdict'),t('observed_match', 'Observed match')],checks) if checks else '<p class="decision-lead">'+(t('this_case_is_judged_against_the_text', 'This case is judged against the text acceptance rules on the left.') if c.get('task_kind','body')=='body' else t('this_task_is_judged_by_its_record', 'This task is judged by its record structure or original-file output.'))+'</p>')
   checks_html+=''.join(f'<p>{esc(x["id"])}：实际 {x["count"]}，要求至少 {x["minimum"]}。</p>' for x in m.get('pattern_results',[]))
  if c.get('task_kind')=='resource' and m.get('resource_candidates'):
   unexpected=set(m.get('unexpected_resource_urls',[]))
   candidate_rows=[('',cell(f'<code class="break">{esc(item["url"])}</code>')
                    +cell(esc('、'.join(item.get('sources',[]))))
                    +cell(t('unexpected_resources', 'Unexpected resources') if item['url'] in unexpected else t('target_or_allowed_variant', 'Target or allowed variant')))
                   for item in m['resource_candidates']]
   checks_html+=table([t('candidate_resource_url', 'Candidate resource URL'),t('answer_field_source', 'Answer-field source'),t('scope_decision', 'Scope decision')],candidate_rows,metrics=[])
  if m.get('stable_field_results'):
   checks_html+=table([t('record_fields', 'Record fields'),t('valid_records', 'Valid records'),t('at_least', 'At least'),t('verdict', 'Verdict')],[('',cell(esc(x['field']))+cell(str(x['count']))+cell(str(x['minimum']))+cell(t('pass_2', 'Pass') if x['pass'] else t('fail_2', 'Fail'))) for x in m['stable_field_results']],metrics=[])
  body+=section(t('why_this_verdict', 'Why this verdict'),caveat+checks_html,'decision')
  if m.get('body_region_results'):
   scoring_text=_body_scoring_text(r,output)
   region_rows,region_cards=body_region_rows(c,m,scoring_text,frozen_scores=frozen_scores);rules=c.get('body_requirements',{})
   required=rules.get('minimum_region_passes',len(rules.get('regions',[])))
   region_count=len(rules.get('regions',[]))
   region_intro=(('<div class="decision-lead"><b>' + t('body_text_rule', 'Body-text rule: ') + f"</b>本例分为 {region_count} 段。每段至少达到 {pct(rules.get('minimum_floor', rules.get('minimum_coverage')))}，其中至少 {required} 段达到 {pct(rules.get('minimum_coverage'))}。先看下表；需要核对原文时，再展开下面的左右对照。</div>"))
   if frozen_scores:
    region_intro+='<p class="source-note">'+model_text(*pair('this_section_retains_the_values_and_passages', 'This section retains the values and passages saved with the score. Sensitive values are hidden in public passages. Missing historical passages are not reconstructed with current rules, and public copies are not used to recalculate original metrics.'))+'</p>'
   body+=section(t('body_coverage', 'Body coverage'),(f"""{region_intro}{table([t('reference_region', 'Reference region'), t('reference_shingles', 'Reference shingles'), t('matched_shingles', 'Matched shingles'), t('coverage', 'Coverage'), t('verdict', 'Verdict')], region_rows, metrics=[])}<details class="technical-details"><summary>""" + t('view_complete_reference_text_and_scoring_lines', 'View complete reference text and scoring lines for each region') + f"</summary>{''.join(region_cards)}</details>"),'body-coverage')
  if title_details:body+=section(t('title_text_quality', 'Title-text quality'),title_details)
  runtime=(('<details class="technical-details"><summary>' + t('view_complete_run_hashes_and_scoring_fields', 'View complete run, hashes, and scoring fields') + f"</summary>{metric_legend(detail_metrics)}{table([t('field_2', 'Field'), t('value', 'Value')], rows, metrics=[])}<details><summary>" + t('public_fields_returned_by_the_adapter', 'Public fields returned by the adapter') + f"""</summary><pre data-kind="metadata">{esc(json.dumps(public_provider_metadata(r.get('provider_metadata', {})), ensure_ascii=False, indent=2))}</pre></details></details><p class="actions">{link(c['url'], t('open_source_page', 'Open source page ↗'))}{link('../datasets/' + c['id'] + '.html', t('view_all_methods_for_this_task', 'View all methods for this task'))}</p>"""))
  body+=section(t('run_and_scoring_details', 'Run and scoring details'),runtime,'run-info')
  body+=run_log_section(out,r,output)
  if r.get('provider_metadata',{}).get('rendered_html_path'):
   body+=section(t('browser_html_evidence', 'Browser HTML evidence'),'<p>'+link('../'+r['provider_metadata']['rendered_html_path'],t('download_the_complete_pre_conversion_dom_html', 'Download the complete pre-conversion DOM HTML'))+'</p>')
  if r.get('provider_metadata',{}).get('raw_stdout_path'):
   metadata=r['provider_metadata'];extractor=metadata.get('document_extractor')
   if extractor:
    body+=section(t('original_document_evidence', 'Original-document evidence'),('<p>' + t('the_body_text_was_extracted_by', '{link}. The body text was extracted by', link=link('../' + metadata['raw_stdout_path'], t('download_the_original_document_obtained_by_the', 'Download the original document obtained by the method'))) + f' <code>{esc(extractor)}</code> ' + t('within_the_same_call_s_timing_budget', ' within the same call\'s timing budget; the "Actual response" above is the text sent to the shared scorer.') + '</p>'))
   else:
    body+=section(t('original_response_evidence', 'Original-response evidence'),('<p>' + t('if_the_task_requires_body_text_and', '{link}. If the task requires body text and the response forms no readable text, it fails on the actual result.', link=link('../' + metadata['raw_stdout_path'], t('download_the_raw_response_bytes_the_method', 'Download the raw response bytes the method obtained directly'))) + '</p>'))
  if delivered:
   file_rows=[('',cell(link('../'+f['artifact_path'],t('download_delivered_file', 'Download delivered file')))+cell(esc(f.get('mime')))+cell(str(f['bytes']))+cell('<code class="break">'+esc(f['sha256'])+'</code>')+cell(t('match', 'Match') if f.get('pass') else t('not_matched_2', 'Not matched'))) for f in delivered]
   body+=section(t('original_file_byte_evidence', 'Original-file byte evidence'),table([t('file', 'File'),t('format_signature', 'Format signature'),t('bytes_2', 'Bytes'),'SHA-256',t('target_check', 'Target check')],file_rows,metrics=[]))
  write(out,'attempts/'+r['attempt_id']+'.html',c['name']+' · '+r['provider'],body)
 controls=filter_controls([('provider',t('method', 'Method'),[(n,provider_label(n)) for n in names]),('track',t('family', 'Family'),tracks),('result',t('result', 'Result'),[('pass',t('pass', 'Pass')),('fail',t('fail', 'Fail'))])]+failure_filters(records,cases))
 comparison=section(t('method_comparison', 'Method comparison'),('<p class="source-note">' + t('methods_are_compared_task_by_task_conclusions', 'Methods are compared task by task; conclusions never splice together different success samples. Pass and fail states use the primary program verdicts. Open a method to inspect its reference, output, and rationale.') + f'</p>{method_comparisons(cases, bycase, names)}'),'method-comparison')
 write(out,'analysis/index.html',t('analysis', 'Analysis'),hero(t('analysis', 'Analysis'),t('compare_methods_on_the_same_tasks_first', 'Compare methods on the same tasks first, then filter the complete execution record.'))+comparison+section(t('all_execution_records', 'All execution records'),controls+table([t('method', 'Method')]+headers,[(case_attrs(r,cases[r['case_id']]),cell(link('../providers/'+r['provider']+'.html',provider_label(r['provider']),browser_method_label(r['provider'],language='en')))+attempt_row(r,cases[r['case_id']])[1]) for r in records],'filtered-table',cls='all-method-results')))
 write_support(out,records,manifests,authored_interpretation=authored_interpretation)
 if score_audit:
  render_score_audit(out,score_audit,score_binding)
 if records and all(row.get('provider_type') == 'fixture' for row in records):
  write(out,'changes/index.html',t('about_this_example', 'About this example'),hero(t('synthetic_example', 'Synthetic example'),t('this_report_was_generated_by_a_local', 'This report was generated by a local test adapter and has no formal benchmark release history.')))
 elif not authored_interpretation:
  review_path=out/'result-review.json'
  review=json.loads(review_path.read_text(encoding='utf-8')) if review_path.is_file() else {}
  updates=''.join('<article class="review-round"><div class="round-label">'+esc(row.get('date',''))+'</div><div><h3>'+model_text(row.get('title',''),row.get('title_en'))+'</h3><p>'+model_text(row.get('fix',''),row.get('fix_en'))+'</p><p class="source-note">'+model_text(row.get('scope',''),row.get('scope_en'))+'</p></div></article>' for row in review.get('review_rounds',[]))
  write(out,'changes/index.html',t('changelog', 'Changelog'),hero(t('changelog', 'Changelog'),t('corrections_after_verification_of_this_run_use', 'Corrections after verification of this run use saved responses; they do not represent new product calls.'))+section(t('corrections_to_this_run', 'Corrections to this run'),'<div class="review-rounds">'+updates+'</div>')+section(t('supporting_evidence', 'Supporting evidence'),'<p>'+link('../review/index.html',t('review_scope_and_corrections', 'Review scope and corrections'))+' · '+link('../analysis/index.html',t('per_task_results_and_evidence', 'Per-task results and evidence'))+' · '+link('../reproduce/index.html',t('run_parameters_and_versions', 'Run parameters and versions'))+'</p>'))
 else:
  render_changes(out,cases,records,dataset)
 for relative in diagnostic_view_paths:
  with (out/relative).open(encoding='utf-8') as source_header:
   previous=BeautifulSoup(source_header.read(8192),'html.parser')
  heading=previous.find('h1');source_heading=previous.find('h2',string=t('record_source', 'Record source'))
  if heading is None or source_heading is None:raise ValueError('Saved diagnostic viewer header is unavailable: '+relative)
  note=source_heading.find_next('p')
  diagnostic_view_page(out,str(Path(relative).with_suffix('.txt')),heading.get_text(),note.get_text() if note else '')
 if include_english:
  write_english_report(out,source_routes=english_routes)
 if presentation_only and (out/'report-languages.json').is_file():
  if ('en' in generated_languages(out)) != include_english:
   raise ValueError('Presentation refresh cannot change generated languages')
  if not include_english:
   (out/'en').mkdir(exist_ok=True)
   (out/'en/index.html').write_text(english_notice(), encoding='utf-8')
  if primary_report_language(out) == 'en':
   select_report_language(out, 'en')
  check_language_contract(out)
 else:
  write_language_contract(out,include_english=include_english)
 return out/'index.html'


def current_local_review_findings(out,dataset,records,audit):
 """Describe the current snapshot from bound calls, never cached release prose."""
 receipt=json.loads(read_evidence_binding(out,dataset['comparison']['task_replacement']).read_text(encoding='utf-8'))
 added=receipt['provider_calls_added']
 cases={c['id']:c for c in dataset['cases']}
 eligible=[r for r in records if measured(r,cases[r['case_id']])]
 passed=sum(succeeded(r,cases[r['case_id']]) for r in eligible)
 adjudicated=len(audit.get('adjudication_revision',{}).get('adjudicated_attempt_ids',[]))
 unreviewed=len(audit.get('unreviewed_attempt_ids',[]))
 rows=[
  (t('the_current_report_contains_results_reuse_historical', 'The current report contains {count} results: {inherited_results} reuse historical calls and {added} come from new calls. Original responses, call times and latencies are kept.', count=len(records), inherited_results=receipt['inherited_results'], added=added),
   f"The current report contains {len(records)} results: {receipt['inherited_results']} inherited calls and {added} new calls. Original answers, timestamps and latencies are retained."),
  (t('currently_pass_are_comparable_and_are_excluded', 'Currently {passed} pass, {count} are comparable and {value} are excluded. Pages and downloadable summaries use the same saved results.', passed=passed, count=len(eligible), value=len(records) - len(eligible)),
   f"Currently {passed} pass among {len(eligible)} eligible results; {len(records)-len(eligible)} are excluded. Pages and downloadable summaries use the same saved results."),
  (t('independent_review_records_retained_another_are_not', 'Independent review records retained: {get}/{count}; another {unreviewed} are not independently reviewed, {get2} of them machine passes. {adjudicated} supplemental adjudications are not called a new full blind review.', get=audit.get('reviewed_attempts', 0), count=len(records), unreviewed=unreviewed, get2=audit.get('accepted_unreviewed_passes', 0), adjudicated=adjudicated),
   f"Retained independent review covers {audit.get('reviewed_attempts',0)}/{len(records)} results. Another {unreviewed} are not independently reviewed, including {audit.get('accepted_unreviewed_passes',0)} machine passes. There are {adjudicated} supplemental adjudications; this is not a new full blind review."),
  (t('target_content_requirements_body_quality_and_diagnostic', 'Target-content requirements, body quality and diagnostic text are shown separately; scoring implementation hashes are on each result page, and diagnostic words alone do not decide a task.'),
   "Target requirements, body quality and diagnostic text are reported separately. Each result binds its scorer hash; diagnostic words alone do not determine acceptance."),
 ]
 if audit.get('retained_reviews'):
  rows[2] = (
   t('current_review_coverage_rechecked_under_current_requirements', 'Current review coverage {reviewed_attempts}/{count}: {fresh_reviewed_attempts} rechecked under current requirements this time; {retained_reviewed_attempts} reuse the original review. Before reuse, each positive requirement, saved response and conclusion was confirmed unchanged, and no old judgment relied on a removed restriction. Reuse does not count as a new blind review.', reviewed_attempts=audit['reviewed_attempts'], count=len(records), fresh_reviewed_attempts=audit['fresh_reviewed_attempts'], retained_reviewed_attempts=audit['retained_reviewed_attempts']),
   f"Current review covers {audit['reviewed_attempts']}/{len(records)} results: {audit['fresh_reviewed_attempts']} newly checked under current requirements; {audit['retained_reviewed_attempts']} original reviews retained. Retention verifies unchanged positive requirements, saved answers and verdicts, with no dependency on removed restrictions. Retention is not a fresh blind review.")
 supplements = audit.get('supplemental_reviews', [])
 if supplements and audit.get('retained_reviews'):
  fresh = [row for row in audit.get('attempts', []) if row.get('review_origin') == 'fresh']
  conflicts = sum(row.get('agent_verdict') != row.get('final_verdict') for row in fresh)
  if audit.get('review_protocol',{}).get('blind_initial_verdict') is False:
   rows.append((t('this_review_knew_the_program_verdict_and', 'This review knew the program verdict and checked each saved response, task requirement and native field; it is not an independent blind review and added no calls to tested pages. The original opinions of the earlier review are kept.'),
                'This round inspected saved answers, task requirements and native fields with known machine outcomes. It was not an independent blind review and added no benchmark webpage calls. Original opinions from retained reviews are preserved.'))
  else:
   rows.append((t('this_new_review_first_hid_the_program', 'This new review first hid the program verdict; the initial judgment disagreed with the program on {conflicts} items, adjudicated against original responses and task requirements. The original independent opinions are kept, and no web calls were added.', conflicts=conflicts),
                f'Fresh review withheld machine verdicts. {conflicts} initial disagreements were adjudicated against the original answers and task requirements. Initial independent opinions are preserved; no new webpage calls were added.'))
 elif supplements:
  supplement_rows = [json.loads(read_evidence_binding(out, item).read_text(encoding='utf-8')) for item in supplements]
  reviewed = sum(item['reviewed_attempts'] for item in supplement_rows)
  rows.append((t('new_calls_received_a_supplemental_item_by', '{reviewed} new calls received a supplemental item-by-item review matching the program; the machine summary was known beforehand, so this is not a blind review. A content pass does not guarantee runnable code; specific quality issues are in the review records.', reviewed=reviewed),
               f'{reviewed} new-call results received supplemental per-answer review and agree with the scorer. The machine summary was already known; this was not blind review. A content pass does not guarantee executable code; quality issues are retained in the review records.'))
 if local_pass_rate_ranking(dataset):
  rows.append((t('rank_follows_the_current_task_pass_rate', 'Rank follows the current task pass rate; latency is shown separately and does not affect rank. Historical calls did not complete full before-and-after source verification, so actual check dates and scope are kept and no source certification is claimed.'),
               'Ranks use current task pass rates. Latency is separate and does not determine rank. Historical calls lack complete before/after source verification; actual check dates and scope are retained without claiming source certification.'))
 return [{'text':zh,'text_en':en} for zh,en in rows]


def write_review_page(out,records):
 path=out/'result-review.json'
 if not path.is_file():
  write(out,'review/index.html',t('review_record', 'Review record'),hero(t('review_record', 'Review record'),t('no_independent_review_record_is_bound_to', 'No independent review record is bound to this build.')))
  return
 audit=json.loads(path.read_text(encoding='utf-8'));reviewer=audit.get('reviewer',{})
 dataset_path=out/'dataset.json'
 dataset=json.loads(dataset_path.read_text(encoding='utf-8')) if dataset_path.is_file() else {}
 metadata_path=repository_root()/'research/report-changes.json'
 if not metadata_path.is_file():
  raise ValueError('A reviewed report requires research/report-changes.json')
 changes=json.loads(metadata_path.read_text(encoding='utf-8'))
 rounds=(audit['review_rounds'] if audit.get('kind')=='local_independent_review' else require_review_rounds(changes))
 rounds_html=''.join(
  ('<article class="review-round"><div class="round-label">' + t('round', 'Round {i}', i=i) + f"<br><small>{esc(row.get('date', ''))}</small></div><div><h3>{model_text(row.get('title', ''), row.get('title_en'))}</h3><p><b>" + t('scope_2', 'Scope:') + f"</b>{model_text(row.get('scope', ''), row.get('scope_en'))}</p><p><b>" + t('finding', 'Finding:') + f"</b>{model_text(row.get('finding', ''), row.get('finding_en'))}</p><p><b>" + t('correction', 'Correction:') + f"""</b>{model_text(row.get('fix', ''), row.get('fix_en'))}</p><p class="round-result"><b>""" + t('outstanding', 'Outstanding:') + f"</b>{model_text(row.get('pending', ''), row.get('pending_en'))}</p></div></article>")
  for i,row in enumerate(rounds,1))
 page=hero(t('review_record', 'Review record'),t('each_review_round_records_its_scope_findings', 'Each review round records its scope, findings, corrections, and unresolved items.'))
 reviewed_attempts=audit.get('attempts',[])
 disagreements=[row for row in reviewed_attempts
                if row.get('agent_verdict') != row.get('machine_verdict')]
 adjudicated=[row for row in disagreements if row.get('final_verdict') in {'pass','fail','unavailable'}]
 unresolved=audit.get('unresolved',0)
 def coverage(label,item):
  scope=item.get('review_protocol',{}).get('scope','all')
  if scope=='machine-failures':
   return (f'{label}机器失败 {item.get("reviewed_attempts",0):,}/{item.get("expected_attempts",0):,}；'
           f'机器通过未逐条审查 {item.get("accepted_unreviewed_passes",0):,}')
  return f'{label}全量 {item.get("reviewed_attempts",0):,}/{item.get("total_attempts",item.get("expected_attempts",0)):,}'
 conclusion=(f"""<p class="callout success">{coverage('主结果', audit)}；代理分歧 {len(disagreements)}；已裁定 {len(adjudicated)}；未决 {unresolved}。</p>""")
 if audit.get('adjudication_revision'):
  conclusion=review_summary(out,'../',highlight=not local_pass_rate_ranking(dataset))
 page+=section(t('current_status', 'Current status'),conclusion)
 page+=result_ledger_section(out,records,'../')
 page+=section(t('review_rounds', 'Review rounds'),'<p>'+model_text(*pair('historical_rounds_retain_the_treatment_and_limits', 'Historical rounds retain the treatment and limits recorded at the time. Current scores, task scope and ranking state are described by the current summary on this page.'))+'</p><div class="review-rounds">'+rounds_html+'</div>')
 method=(('<details class="technical-details"><summary>' + t('review_protocol_and_reviewer', 'Review protocol and reviewer') + '</summary><p>' + t('reviewer', 'Reviewer:') + f"{esc(reviewer.get('agent', t('independent_agent', 'Independent agent')))}" + t('model', '; model: ') + f"{esc(reviewer.get('model', '—'))}" + t('reasoning_effort', '; reasoning effort: ') + f"{esc(reviewer.get('effort', '—'))}" + t('machine_verdicts_and_scores_are_hidden_during', '. Machine verdicts and scores are hidden during review; only the task contract, reference answer, and complete actual output are provided. Disagreements are adjudicated from original evidence, and any unresolved item blocks publication. Existing reviews are retained when task and output are unchanged; changed calls require a new blind review.') + '</p></details>'))
 if audit.get('adjudication_revision'):
  method='<p>'+model_text(*pair('original_independent_and_supplemental_opinions_remain_in', 'Original independent and supplemental opinions remain in the per-result records. This supplemental adjudication inspected saved scores, contracts and original answers; it is not a new full blind review. Each adjudication records specific rules and original excerpts. Completing adjudication does not certify source validity.'))+'</p>'
 if audit.get('review_protocol',{}).get('blind_initial_verdict') is False and audit.get('retained_reviews'):
  method='<p>'+model_text(*pair('the_newly_checked_records_received_evidence_review', 'The newly checked records received evidence review with known machine outcomes, checking complete saved answers, task requirements and native fields. Other records retain prior opinions after dependency checks. The two scopes are reported separately; this evidence review is not a new independent blind review and does not complete historical source certification.'))+'</p>'
 if audit.get('review_protocol',{}).get('fresh_blind_initial_verdict') is True and audit.get('retained_reviews'):
  method='<p>'+model_text(*pair('fresh_reviews_received_task_requirements_frozen_references', 'Fresh reviews received task requirements, frozen references and saved answers, with machine outcomes hidden for the initial judgment. Disagreements and anomalies were then investigated through raw responses, field selection, call logs and scoring rules. Initial opinions remain immutable. Prior reviews with unchanged decision inputs are counted separately and retain their original protocol. Review completion does not provide historical source certification.'))+'</p>'
 if audit.get('renewed_context_reviewed_attempts',0):
  blind_count=audit.get('fresh_blind_reviewed_attempts',0);renewed_count=audit['renewed_context_reviewed_attempts']
  method='<p>'+model_text(*pair('saved_answers_received_initial_independent_judgments_with', '{blind_count} saved answers received initial independent judgments with machine outcomes hidden, followed by disagreement and anomaly investigation. Another {renewed_count} received supplemental adjudication after task-contract or same-call request-context corrections, checking current requirements, original answers and final outcomes. This supplemental adjudication inspected machine outcomes and preserves original opinions. Remaining records retain prior reviews after dependency checks. The scopes are counted separately; review completion does not provide historical source certification.', blind_count=blind_count, renewed_count=renewed_count))+'</p>'
 page+=section(t('review_protocol', 'Review protocol'),method)
 dataset_path=out/'dataset.json'
 if dataset_path.is_file():
  dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
  findings=dataset.get('comparison',{}).get('call_chain_audit',{}).get('findings',[])
  if dataset.get('comparison',{}).get('task_replacement'):
   findings=current_local_review_findings(out,dataset,records,audit)
  if findings:
   page+=section(t('corrections_and_review_limits', 'Corrections and review limits'),''.join('<p>'+model_text(item['text'],item.get('text_en'))+'</p>' for item in findings))
 write(out,'review/index.html',t('review_record', 'Review record'),page)

NATIVE_OPTION_LABELS={'wait_until':t('page_completion_condition', 'Page completion condition'),'block_private_networks':t('block_private_network_access', 'Block private-network access'),'max_bytes':t('returned_content_limit_bytes', 'Returned content limit (bytes)'),'max_age':t('maximum_cache_age', 'Maximum cache age'),'proxy':t('proxy_selection', 'Proxy selection'),'formats':t('requested_output_format', 'Requested output format'),'only_main_content':t('main_content_only', 'Main content only'),'extract_depth':t('extraction_depth', 'Extraction depth'),'include_images':t('include_images', 'Include images'),'text_verbosity':t('body_detail_level', 'Body detail level'),'max_age_hours':t('maximum_cache_age_hours', 'Maximum cache age (hours)'),'include_resource_links':t('include_resource_links', 'Include resource links'),'rich_links_limit':t('named_link_limit', 'Named-link limit'),'document_max_bytes':t('document_size_limit_bytes', 'Document size limit (bytes)'),'min_interval_seconds':t('call_interval_seconds', 'Call interval (seconds)'),'log_level':t('log_level', 'Log level'),'retain_complete_diagnostic_log':t('keep_complete_diagnostic_logs', 'Keep complete diagnostic logs'),'max_diagnostic_bytes':t('log_retention_limit_bytes', 'Log retention limit (bytes)'),'uniform_native_parameters':t('use_uniform_fixed_native_options', 'Use uniform fixed native options'),'dns_mode':t('name_resolution_mode', 'Name resolution mode')}

def load_frozen_method_options(out,records):
 """Bind displayed controls to their actual configuration and call scope."""
 document=json.loads((out/'frozen-method-options.json').read_text(encoding='utf-8'))
 if document.get('schema_version')!=2 or not document.get('source_config'):
  raise ValueError('Frozen option display requires a bound configuration and call scope')
 binding=document['source_config']
 source_config=json.loads(read_evidence_binding(out,binding).read_text(encoding='utf-8'))
 configuration=source_config['providers']
 config_hash=binding['sha256']
 if source_config.get('projection'):
  require_public_method_options(configuration)
  if source_config['projection']!='allowlisted_method_options':
   raise ValueError('Unknown frozen option configuration projection')
  config_hash=source_config.get('original_provider_config_sha256')
  contract=json.loads((out/'local-run-contract.json').read_text(encoding='utf-8'))
  if (not config_hash or config_hash!=contract.get('provider_config_sha256')
      or any(key not in NATIVE_OPTION_LABELS for options in configuration.values() for key in options)):
   raise ValueError('Public option configuration differs from its original call binding')
 identifiers=document.get('case_ids',[])
 selected=[row for row in records if row['case_id'] in identifiers]
 providers=document.get('providers',{})
 if (not identifiers or len(identifiers)!=len(set(identifiers)) or not providers
     or document.get('provider_config_sha256')!=config_hash
     or document.get('result_count')!=len(selected)
     or len(selected)!=len(identifiers)*len(providers)
     or {(r['provider'],r['case_id']) for r in selected}
        !={(p,c) for p in providers for c in identifiers}
     or any(not document.get(key) for key in
            ('scope_title','scope_title_en','scope_description','scope_description_en'))):
  raise ValueError('Frozen option display scope differs from saved calls')
 for name,options in providers.items():
  if (name not in configuration or not isinstance(options,dict)
      or any(key not in NATIVE_OPTION_LABELS or key not in configuration[name]
             or type(configuration[name][key]) is not type(value)
             or configuration[name][key]!=value for key,value in options.items())):
   raise ValueError('Displayed option differs from the frozen configuration')
 return document


def render_account_requirements(records):
 names=sorted({row['provider'] for row in records},key=lambda name:(provider_label(name).lower(),name))
 accesses={name:method_access(name,records) for name in names}
 english_accesses={name:method_access(name,records,language='en') for name in names}
 hosted=[name for name in names if accesses[name].get('type')=='hosted']
 intro=model_text(*pair('account_and_installation_requirements_come_from_the', 'Account and installation requirements come from the participating method registrations; missing requirements are marked.'))
 if hosted:
  intro+=' '+model_text(*pair('methods_requiring_a_provider_account', 'Methods requiring a provider account: '))+model_text('、'.join(provider_label(name) for name in hosted),', '.join(browser_method_label(name,language='en') for name in hosted))
 rows=[]
 for name,access in accesses.items():
  english=english_accesses[name]
  official='<br>'.join(link(url,label,english['links'][index][0])
                      for index,(label,url) in enumerate(access['links']))
  variables='<br>'.join('<code>'+esc(value)+'</code>' for value in access['env_vars'])
  rows.append(('',cell(model_text(provider_label(name),browser_method_label(name,language='en')))+cell(model_text(access['account'],english['account']))+cell(official)+cell(variables)+cell(model_text(access['configuration'],english['configuration']))))
 body=hero(t('access_setup', 'Access & setup'),intro)
 body+=section(t('methods_in_this_benchmark', 'Methods in this benchmark'),('<p>' + t('this_is_the_complete_external_setup_required', 'This is the complete external setup required for the benchmark: accounts, official sources, and variable names. Values, local paths, login sessions, cookies, and tokens are never included.') + f"</p>{table([t('method', 'Method'), t('account_requirement', 'Account requirement'), t('official_source_2', 'Official source'), t('variable_names', 'Variable names'), t('measured_configuration', 'Measured configuration')], rows, cls='accounts-table', metrics=[])}"))
 body+=section(t('public_data_boundary', 'Public-data boundary'),('<p class="callout success"><b>' + t('the_report_publishes_only_the_names_and', 'The report publishes only the names and official entry points needed for reproduction, never credential values.') + '</b> ' + t('the_publication_gate_scans_html_json_downloads', 'The publication gate scans HTML, JSON, downloads, and per-call outputs for common token forms, private keys, Authorization values, and user-directory paths. Secret-shaped fragments in third-party raw HTML are redacted separately while scored output remains byte-for-byte unchanged. Scan results and redacted-file hashes are retained with the release bundle.') + '</p>'))
 return body


def render_execution_contract(dataset,records,manifests):
 """Render recorded budgets and scheduling without inferring missing values."""
 def seconds(value):
  if not nonnegative_number(value):
   return model_text(*pair('not_recorded', 'Not recorded'))
  return model_text(*pair('seconds', '{value:g} seconds', value=value))
 def count(value):
  return str(value) if isinstance(value,int) and not isinstance(value,bool) and value>=0 else model_text(*pair('not_recorded', 'Not recorded'))
 declared=dataset.get('comparison',{}).get('timeout_seconds')
 intro=''
 if declared is not None:
  intro='<p>'+model_text(*pair('predeclared_outer_budget', 'Predeclared outer budget: '))+seconds(declared)+'</p>'
 names={row['provider'] for row in records}
 rows=[]
 represented=set()
 for index,manifest in enumerate(manifests,1):
  parameters=manifest.get('parameters',{})
  for provider in manifest.get('providers',[]):
   name=provider.get('name')
   if name not in names:continue
   represented.add(name)
   values=[model_text(provider_label(name),browser_method_label(name,language="en")),str(index),seconds(parameters.get('timeout')),
           seconds(provider.get('min_interval_seconds')),seconds(parameters.get('delay')),
           count(parameters.get('runs')),count(parameters.get('max_failure_retries'))]
   rows.append(('', ''.join(cell(value) for value in values)))
 for name in sorted(names-represented):
  rows.append(('',cell(model_text(provider_label(name),browser_method_label(name,language="en")))+cell('—')+
               ''.join(cell(model_text(*pair('not_recorded', 'Not recorded'))) for _ in range(5))))
 body=intro+table([model_text(*pair('method', 'Method')),model_text(*pair('batch', 'Batch')),
     model_text(*pair('outer_timeout', 'Outer timeout')),model_text(*pair('method_minimum_interval', 'Method minimum interval')),
     model_text(*pair('runner_delay_between_calls', 'Runner delay between calls')),model_text(*pair('planned_runs', 'Planned runs')),
     model_text(*pair('failure_retry_limit', 'Failure retry limit'))],rows,metrics=[])
 body+='<p>'+model_text(*pair('the_table_reads_saved_execution_batches_missing', 'The table reads saved execution batches; missing values are not replaced with current defaults. Method intervals and runner delays are separate settings, not measured waiting time. Inter-method concurrency and actual scheduling follow batch records and call timestamps. Native loading, connection and processing limits come from frozen configuration and actual requests, not from the outer budget.'))+' '+link('../reproduce/index.html',t('reproduction', 'Reproduction'),'Environment and reproduction')+'</p>'
 return body


def write_support(out,records,manifests,*,authored_interpretation=True):
 dataset=json.loads((out/'dataset.json').read_text(encoding='utf-8')) if (out/'dataset.json').is_file() else {}
 local_matrix=measurement_presentation(dataset).local_matrix
 headless=is_headless_cohort(records)
 methodology=hero(t('methodology', 'Methodology'),t('acceptance_rules_are_defined_by_task_measured', 'Acceptance rules are defined by task. Measured methods within a family use the same frozen tasks and references, and each ranking states its coverage.'))
 if headless:
  methodology+=section(t('from_reference_capture_to_publication', 'From reference capture to publication'),('<div class="guide-grid"><article class="guide-card"><h3>' + t('1_freeze_references', '1 · Freeze references') + '</h3><p>' + t('locks_frozen_tasks_references_and_acceptance_rules', 'Locks frozen tasks, references and acceptance rules; source pages are recaptured only for tasks explicitly declaring a live reference.') + '</p></article><article class="guide-card"><h3>' + t('2_run_provider_calls', '2 · Run provider calls') + '</h3><p>' + t('use_the_same_url_task_and_deadline', 'Use the same URL, task, and deadline. Each method uses its fastest official task-native output path, while version, output, and hashes are retained.') + '</p></article><article class="guide-card"><h3>' + t('3_post_run_validation', '3 · Post-run validation') + '</h3><p>' + t('checks_frozen_tasks_references_rules_and_call', 'Checks frozen tasks, references, rules and call records; tasks that need a live reference are captured before and after to confirm it is unchanged.') + '</p></article><article class="guide-card"><h3>' + t('4_independent_result_review', '4 · Independent result review') + '</h3><p>' + t('independent_review_follows_an_explicitly_specified_scope', "Independent review follows an explicitly specified scope and checks output, references and acceptance rules; this round's coverage is below, and program scoring is not treated as independent review.") + f"</p></article></div>{review_summary(out, '../')}"),t('evaluation_workflow', 'Evaluation workflow'))
 examples_html=re.sub(r'<a href="../datasets/([^"#]+).html">([^<]*)</a>',lambda m:m[0] if (out/'datasets'/(m[1]+'.html')).exists() else m[2],examples({c['id']:c for c in dataset.get('cases',[])}))
 methodology+=section(t('how_each_task_family_is_evaluated', 'How each task family is evaluated'),examples_html)+section(t('metric_definitions', 'Metric definitions'),metric_guide())
 methodology+=section(t('shared_inputs_timing_boundary_and_call_budget', 'Shared inputs, timing boundary, and call budget'),('<p>' + t('every_method_enters_the_same_scoring_pipeline', "Every method enters the same scoring pipeline, while acceptance follows each task's required deliverable. Web and document tasks require readable body text; web-media tasks require the specified resource URL or registered text; direct-media tasks require image or video bytes; structure tasks require the declared fields and stable text. HTML and Markdown links follow the same checks as explicitly paired structured name–URL fields. Request echoes and diagnostic logs do not count as answers. Raw output and adapter status are preserved, and the report layer does not alter results by method name.") + '</p><div class="guide-grid"><article class="guide-card"><h3>' + t('included_in_latency', 'Included in latency') + '</h3><p>' + t('timing_starts_when_the_provider_call_is', 'Timing starts when the provider call is prepared and ends when the final deliverable is ready for the shared scorer. It includes thread scheduling, SDK/CLI/HTTP execution, remote processing and network transfer, browser rendering and body extraction, local parsing, normalized input conversion, file-byte reading, format detection, and SHA-256 calculation.') + '</p></article><article class="guide-card"><h3>' + t('excluded_from_latency', 'Excluded from latency') + '</h3><p>' + t('dataset_and_reference_loading_provider_configuration_validation', 'Dataset and reference loading, provider configuration validation, inter-call rate-limit waits, evidence persistence after the deliverable is ready, shared scoring, report aggregation, and page generation are excluded. Provider-reported server timing is supplementary evidence; rankings use client-observed end-to-end latency.') + '</p></article></div><p>' + t('this_boundary_measures_the_time_needed_to', "This boundary measures the time needed to obtain the task's final deliverable. A service that returns a finished result includes the full round trip; a local method stops only after any required download, rendering, or parsing. The evaluated method must deliver document body text in readable form. Direct-media tasks stop after byte validation and do not perform an unrelated Markdown conversion. Every method uses the same outer timer and deadline. Late answers are rejected. Calls that cannot be cancelled are isolated, which leaves the batch incomplete and blocks publication.") + '</p><p>' + t('alongside_the_existing_checks_complete_body_tasks', 'Alongside the existing checks, complete-body tasks measure six-character-shingle coverage across up to five consecutive reference regions. Every region must reach 60%, and normally at least four must reach 80%. A global capacity assignment ensures that each returned fragment instance counts toward only one region, regardless of region order. A task may declare different regional thresholds when independent evidence establishes legitimate reordering. Matching ignores whitespace and presentation marks while preserving text order; full-text F1 remains a separate measurement. Direct-media tasks require byte validation, and a resource URL does not count as a file.') + '</p>'))
 if (out/'resource-cohort/resource-comparison.json').is_file():
  resource_summary=json.loads((out/'resource-cohort/resource-comparison.json').read_text(encoding='utf-8'))
  resource_environment=resource_summary.get('environment',{})
  resource_parameters=[
   ((t('tasks', 'Tasks'),'Task count'),len(resource_summary.get('planned_cases',[]))),
   ((t('methods_2', 'Methods'),'Method count'),len(resource_summary.get('providers',[]))),
   ((t('cpu_cores', 'CPU cores'),'CPU cores'),resource_environment.get('cpus','—')),
   ((t('memory_limit', 'Memory limit'),'Memory limit'),resource_environment.get('memory','—')),
   ((t('process_limit', 'Process limit'),'Process limit'),resource_environment.get('pids_limit','—')),
   ((t('sampling_interval_ms', 'Sampling interval (ms)'),'Sampling interval (ms)'),resource_environment.get('sample_interval_ms','—')),
  ]
  methodology+=section(t('resource_measurement_boundary', 'Resource measurement boundary'),'<p>'+model_text(*pair('cpu_memory_and_network_measurements_come_from', 'CPU, memory and network measurements come from a separate resource cohort. The primary table includes all predeclared calls, including failures; successful-call distributions are conditional and the common-pass subset is diagnostic only.'))+'</p>'
   +table([model_text(*pair('parameter', 'Parameter')),model_text(*pair('value', 'Value'))],[('',cell(model_text(*key))+cell(esc(value))) for key,value in resource_parameters],metrics=[])
   +'<p>'+model_text(*pair('cumulative_counters_provide_cpu_and_network_totals', 'Cumulative counters provide CPU and network totals. Memory peaks and rate percentiles depend on the recorded sampling interval and may miss shorter peaks. The resource evidence identifies the environment, scope and exclusions.'))+' '
   +link('../resource-cohort/resource-comparison.json',t('resource_measurement_evidence', 'Resource measurement evidence'))+'</p>')
 if (out/'scoring-revisions.json').exists():
  revision_links=[link('../scoring-revisions.json',t('scoring_rule_revision_receipt', 'Scoring-rule revision receipt'))]
  if (out/'reference-revisions.json').exists():revision_links.append(link('../reference-revisions.json',t('reference_answer_revision_receipt', 'Reference-answer revision receipt')))
  if (out/'latency-corrections.json').exists():revision_links.append(link('../latency-corrections.json',t('latency_correction_receipt', 'Latency-correction receipt')))
  if (out/'request-status-corrections.json').exists():revision_links.append(link('../request-status-corrections.json',t('request_status_correction_receipt', 'Request-status correction receipt')))
  detail=t('current_scores_include_offline_rescoring_of_saved', 'Current scores include offline rescoring of saved outputs. Receipts record input and output hashes, verdict changes, and provider-call counts without presenting new rules as the original run configuration.')
  if headless and (out/'latency-corrections.json').exists():
   latency_receipt=json.loads((out/'latency-corrections.json').read_text(encoding='utf-8'))
   changes=latency_receipt.get('changes',[])
   if changes:
    affected='、'.join(provider_label(name) for name in sorted({change['provider'] for change in changes}))
    detail+=((' ' + t('the_latency_correction_in_the_current_report', 'The {affected} latency correction in the current report replaces only {count} timings, all from round {get} of the paired experiment; old timings and original physical calls stay in offline evidence, and a cross-batch view must not be read as a simultaneous rerun.', affected=affected, count=len(changes), get=latency_receipt.get('selected_round', '—'))))
  methodology+=section(t('scoring_provenance_and_revisions', 'Scoring provenance and revisions'),'<p>'+detail+' '+' · '.join(revision_links)+'</p>')
 methodology+=section(t('stable_task_contracts_and_full_text_diagnostics', 'Stable task contracts and full-text diagnostics'),task_contract_guide(),'task-contracts')
 figures=''
 for name,label in [('annotation',t('reference_annotations', 'Reference annotations')),('validation',t('task_acceptance', 'Task acceptance')),('scoring',t('scoring_scope', 'Scoring scope'))]:
  if not (out/'assets/methodology'/f'{name}.png').is_file():
   continue
  english_asset=(f' data-en-src="../assets/methodology/{name}.en.png"'
                 if (out/'assets/methodology'/f'{name}.en.png').is_file() else '')
  figures+=f'<figure><img src="../assets/methodology/{name}.png"{english_asset} alt="{label}" style="max-width:100%;height:auto" loading="lazy"><figcaption>{label}</figcaption></figure>'
 if figures:methodology+=section(t('method_diagrams', 'Method diagrams'),figures)
 methodology+=section(t('reference_normalization_and_matching', 'Reference normalization and matching'),('<p>' + t('original_snapshots_remain_unchanged_scoring_references_remove', 'Original snapshots remain unchanged. Scoring references remove zero-width spaces, BOMs, and soft hyphens, and are checked for empty bodies, decoding corruption, duplicate checks, and whether each check exists in the reference. Passing validation does not prove semantic completeness; source records still define body scope.') + '</p><p>' + t('key_facts_are_matched_mechanically_against_normalized', 'Key facts are matched mechanically against normalized visible text: Unicode, letter case, and layout whitespace are normalized, so menu paths such as Settings &gt; AI and Settings&gt;AI are equivalent; Markdown link destinations, inline-code markers, and emphasis markers are ignored. A field whose complete record boundary is known may omit whitespace between inline elements, such as the tag list') + ' <code>change deep-thoughts</code> ' + t('and', 'and') + ' <code>changedeep-thoughts</code> ' + t('is_content_equivalent_adjacent_independent_links_are', 'is content-equivalent. Adjacent independent links are still checked as separate token segments, with no guessed segmentation across records. Numbers, signs, negation, punctuation, and English word boundaries are preserved. Acceptance never relies on an LLM or fuzzy similarity.') + '</p>'))
 methodology+=section(t('call_timing', 'Call timing'),('<p>' + t('all_methods_use_the_same_frozen_tasks', 'All methods use the same frozen tasks and references. Latencies from different batches are not ranked directly; the same-window cohort is identified separately. Live content, login state, and site policy may change, so results represent only the recorded calls. See Reproduction for environment, versions, and commands.') + '</p>'))
 methodology+=section(t('rerun_scope_after_a_change', 'Rerun scope after a change'),('<p>' + t('a_code_change_does_not_automatically_require', 'A code change does not automatically require a full rerun. Presentation-only changes rebuild the report; judge-only changes are rescored offline when saved output is sufficient; a changed reference reruns every method for that task; a new or upgraded implementation reruns that method across all tasks; a timing-boundary change invalidates and reruns the same-window cohort. A full rerun is reserved for changes to global task inputs, call boundaries, or dependencies shared by every method.') + '</p>'))
 methodology+=section(t('calculations_and_limits', 'Calculations and limits'),('<details><summary>' + t('full_text_scoring_formula', 'Full-text scoring formula') + '</summary><p>' + t('recall_equals_recovered_reference_units_divided_by', 'Recall equals recovered reference units divided by reference units; precision equals matched body units divided by returned units; F1 equals 2 × recall × precision ÷ their sum. English is tokenized by word and Chinese by character, with case and Unicode normalized; only Markdown link text is counted. Values are averaged across tasks with complete references. Request failures score 0, successful requests that fail acceptance retain their observed text score, and tasks without references are excluded.') + '</p></details><details><summary>' + t('reference_scope_and_fairness', 'Reference scope and fairness') + '</summary><p>' + t('web_and_document_tasks_use_independently_retained', 'Web and document tasks use independently retained body references. Repository tasks target the README; list tasks cover only the declared loaded range and do not require infinite scrolling or pagination. Login and CAPTCHA pages cannot replace the target body. Terms such as “404 Not Found” inside the reference itself are not treated as error-page signals.') + '</p><p>' + t('a_media_url_proves_only_that_an', "A media URL proves only that an endpoint was found, not that the file was obtained. File tasks do not use a separate downloader to complete the evaluated method's work.") + '</p></details>'))
 methodology+=section(model_text(*pair('run_parameters_and_call_boundaries', 'Run parameters and call boundaries')),
                      render_execution_contract(dataset,records,manifests))
 if measurement_presentation(dataset).native_outputs:
  methodology+='<p>'+model_text(*pair('text_acceptance_uses_content_delivered_by_the', 'Text acceptance uses content delivered by the native interface, without an additional parser completing the answer. Original-file tasks use their byte-acceptance rules; requests, outputs and errors remain in per-call evidence.'))+'</p>'
 if dataset.get('cases'):
  methodology+=render_acceptance_scope({case['id']:case for case in dataset['cases']},records)
 score_binding=dataset.get('comparison',{}).get('score_audit')
 if score_binding:
  methodology+=section(t('data_scope', 'Data scope'),'<div data-scoring-input-scope><p>'+esc(t('this_report_uses_the_existing_call_records', 'This report uses the existing {total_attempts} call records and latencies with no new browser calls. {projected_scoring_inputs} scoring inputs are redacted public copies and {recovered_originals} are outputs restored by the original hash. Item-by-item check scope is {reviewed_attempts}/{total_attempts} verdict changes.', total_attempts=score_binding['total_attempts'], projected_scoring_inputs=score_binding['projected_scoring_inputs'], recovered_originals=score_binding['recovered_originals'], reviewed_attempts=score_binding['reviewed_attempts']))+'</p><p>'+link('../audit/index.html',t('input_differences_and_per_task_acceptance_records', 'Input differences and per-task acceptance records'))+'</p></div>')
  input_rows=[]
  for name in sorted({row['provider'] for row in records}):
   selected=[row for row in records if row['provider']==name]
   counts=Counter(row.get('scoring',{}).get('artifact_view','original') for row in selected)
   input_rows.append(('',cell(esc(provider_label(name)))+cell(str(counts['original']))+cell(str(counts['recovered_original']))+cell(str(counts['published_projection']))+cell(str(len(selected)))))
  methodology+=section(t('scoring_input_sources', 'Scoring input sources'),table([t('method', 'Method'),t('raw_output', 'Raw output'),t('recovered_by_hash', 'Recovered by hash'),t('public_redacted_copies', 'Public redacted copies'),t('total', 'Total')],input_rows,metrics=[]))
 revision=dataset.get('definition_revision')
 if revision:
  methodology+=section(t('scoring_definitions', 'Scoring definitions'),('<p>' + t('dataset_2', 'Dataset:') + f"""<code class="break">{esc(dataset['id'])}</code></p><p>""" + t('definition_revision', 'Definition revision:') + f"""{link('../' + revision['path'], revision['id'])} · SHA-256：<code class="break">{esc(revision['sha256'])}</code></p><p>""" + t('original_definition_dataset', 'Original definition dataset:') + f"""<code class="break">{esc(revision['parent_dataset_id'])}</code> · SHA-256：<code class="break">{esc(revision['parent_dataset_sha256'])}</code></p>"""))

 write(out,'methodology/index.html',t('methodology', 'Methodology'),methodology)
 downloads=' · '.join(link('../'+path,label) for path,label in [('dataset.json',t('dataset_and_references', 'Dataset and references')),('results.jsonl',t('all_call_records', 'All call records')),('summary.json',t('summary_by_family', 'Summary by family')),('execution.json',t('execution_batches', 'Execution batches'))]+([('implementation-provenance.json',t('browser_versions_and_binary_sources', 'Browser versions and binary sources'))] if (out/'implementation-provenance.json').is_file() else [])+([('reference-revisions.json',t('reference_revision_receipt', 'Reference-revision receipt'))] if (out/'reference-revisions.json').is_file() else [])+([('scoring-revisions.json',t('scoring_revision_receipt', 'Scoring-revision receipt'))] if (out/'scoring-revisions.json').is_file() else [])+([('latency-corrections.json',t('latency_correction_receipt', 'Latency-correction receipt'))] if (out/'latency-corrections.json').is_file() else [])+([('request-status-corrections.json',t('request_status_correction_receipt', 'Request-status correction receipt'))] if (out/'request-status-corrections.json').is_file() else []))
 reproduce=('<p>' + t('run_in_the_benchmark_repository_using_the', 'Run in the benchmark repository using the current measured data in evaluation/; this does not call provider APIs.') + '</p><pre>make setup\nmake report PROFILE=/absolute/path/to/profile.json EVIDENCE=/absolute/path/to/evidence\nmake report-check PROFILE=/absolute/path/to/profile.json</pre><p>' + t('output_directory_reports_webfetch_the_command_fails', 'Output directory: reports/webfetch/. The command fails immediately unless the pinned Python 3.11 and uv.lock dependencies are satisfied. Credentials are loaded only from the local environment and are not included in the report.') + '</p>')
 if headless:
  reproduce=('<p>' + t('run_in_the_benchmark_repository_and_build', 'Run in the benchmark repository and build from an explicitly selected persistent frozen-evidence directory; browsers are not called again.') + '</p><pre>make setup\nmake report PROFILE=/absolute/path/to/profile.json EVIDENCE=/absolute/path/to/evidence\nmake report-check PROFILE=/absolute/path/to/profile.json</pre><p>唯一输出目录为 reports/webfetch/。固定的 Python 3.11 与 uv.lock 依赖不满足时会立即失败。重新测量使用 uv run --frozen python scripts/run_certified_benchmark.py 和本轮方法配置；先锁定题目、答案、规则和执行范围，再调用；明确声明需要实时参考的任务才做前后采集。完整命令与版本见仓库 harness/web_fetch/HEADLESS.md。</p>')
 profile_path=dataset.get('comparison',{}).get('reproduction_profile')
 if profile_path:
  candidate=Path(profile_path)
  if candidate.is_absolute() or not candidate.parts or '..' in candidate.parts or candidate.suffix!='.json':
   raise ValueError('Report reproduction profile must be a relative JSON path without parent traversal')
  destination=dataset['comparison'].get('report_output','reports/webfetch')
  command=f'uv run --frozen python scripts/build_report.py --profile {profile_path} --evidence /absolute/path/to/evidence --output {destination}\nuv run --frozen python scripts/check_report.py {destination} --profile {profile_path}'
  reproduce=('<p>' + t('built_from_this_run_s_independent_configuration', "Built from this run's independent configuration and frozen evidence, without calling browsers again.") + f'</p><pre>{esc(command)}</pre><p>重新测量使用仓库的认证入口；既定题目和验收规则在调用前冻结，只有明确声明需要实时参考的任务才做前后采集。</p>')
 if local_matrix:
  reproduce=('<p>以下命令只从本轮证据重建本地中文报告，不重新调用产品。</p><pre>uv run --frozen python scripts/build_report.py --local-audit --evidence /absolute/path/to/report-evidence --output /absolute/path/to/report\nuv run --frozen python scripts/check_report.py /absolute/path/to/report</pre><p>' + t('call_time_configuration_implementation_versions_and_scorer', "Call-time configuration, implementation versions and scorer hashes are in {link}; each call's actual request parameters are in the call log on its detail page. Current source configuration may differ from call-time configuration, so the frozen protocol and original batch manifests govern. Credentials are read only from the repository .env.", link=link('../local-run-contract.json', t('pre_call_frozen_protocol', 'Pre-call frozen protocol'))) + '</p>')
 common=[]
 for label,path in [(t('platform', 'Platform'),('environment','platform')),('Python',('environment','python')),(t('timeout_seconds', 'Timeout (seconds)'),('parameters','timeout')),(t('concurrency_model', 'Concurrency model'),('parameters','concurrency_scope'))]:
  values=sorted({str(m.get(path[0],{}).get(path[1],'—')) for m in manifests})
  common.append(('',cell(label)+cell(esc('；'.join(values)))))
 provenance={}
 if (out/'implementation-provenance.json').is_file():
  provenance={x['binary_sha256']:x for x in json.loads((out/'implementation-provenance.json').read_text(encoding='utf-8')).get('implementations',[])}
 provider_rows=[]
 for name in sorted({r['provider'] for r in records}):
  subset=[r for r in records if r['provider']==name]
  versions=method_versions(subset,provenance)
  identities=[p.get('implementation_identity',{}) for m in manifests for p in m.get('providers',[]) if p.get('name')==name]
  versions=versions or sorted({str(identity['reported_version']) for identity in identities if identity.get('reported_version')})
  versions=versions or sorted({str(identity['repository']['head']) for identity in identities if identity.get('repository',{}).get('head')})
  hashes=sorted({str(r.get('provider_metadata',{}).get('binary_sha256')) for r in subset if r.get('provider_metadata',{}).get('binary_sha256')})
  versions=versions or sorted({provenance[h].get('version') or provenance[h].get('identity') for h in hashes if h in provenance})
  deployments=sorted({str(r.get('provider_metadata',{}).get('deployment','—')) for r in subset})
  timings={provenance[h].get('identity_timing') for h in hashes if h in provenance}
  if not hashes:
   basis=t('source_revision_and_file_hashes_are_locked', 'Source revision and file hashes are locked; see the execution batch') if any(identities) else t('cloud_api_backend_version_not_disclosed', 'Cloud API backend version not disclosed')
  elif any(t and t.startswith('post_hoc') for t in timings):
   basis=t('post_run_hash_matches_the_official_archive', 'Post-run hash matches the official archive; the original call did not record a version string')
  elif versions:
   basis=t('runtime_version_and_sha_256', 'Runtime version and SHA-256')
  else:
   basis=t('runtime_sha_256_the_nightly_source_revision', 'Runtime SHA-256; the nightly source revision is unavailable')
  if local_matrix:
   version='；'.join(versions) or (t('the_cloud_service_does_not_disclose_its', 'The cloud service does not disclose its backend version') if not hashes and not any(identities) else t('no_verifiable_version_information_is_available', 'No verifiable version information is available'))
   if versions and all(re.fullmatch(r'[0-9a-f]{40}', value) for value in versions):
    version=t('source_commit', 'Source commit: {version}', version=version)
   provider_rows.append(('',cell(link('../providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell('<span class="break">'+esc(version)+'</span>')))
  else:
   provider_rows.append(('',cell(link('../providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell(esc('；'.join(versions) or t('version_not_recorded', 'Version not recorded')))+cell(esc(basis))+cell(esc('；'.join(deployments)))+cell('<br>'.join('<code class="tiny break">'+esc(x)+'</code>' for x in hashes) or '—')+cell(str(len(subset)))))
 write_review_page(out,records)
 repo='https://github.com/lexmount/browser-eval'
 support=hero(t('reproduction', 'Reproduction'),t('the_browser_eval_repository_is_authoritative_for', 'The browser-eval repository is authoritative for code, dataset, and reproduction instructions. This page lists only the configuration required for this run.'))
 support+=section(t('code_and_dataset', 'Code and dataset'),(f"<p>{link(repo, t('browser_eval_repository', 'browser-eval repository'))}" + t('contains_the_current_benchmark_code_task_definitions', ' contains the current benchmark code, task definitions, and independent references. This report separately provides the frozen results for this run.') + f'</p><p>{downloads}</p>'))
 if (out/'speed-cohort/speed-comparison.json').is_file():
  support+=section(t('same_window_latency_evidence', 'Same-window latency evidence'),'<p>'+link('../speed-cohort/speed-comparison.json',t('latency_summary_and_sample_denominators', 'Latency summary and sample denominators'))+' · '+link('../speed-cohort/results.jsonl',t('per_call_latency_cohort_records', 'Per-call latency-cohort records'))+'</p>')
 if (out/'resource-cohort/resource-comparison.json').is_file():
  support+=section(t('isolated_resource_evidence', 'Isolated resource evidence'),'<p>'+link('../resource-cohort/resource-comparison.json',t('cpu_memory_and_network_summary_with_shared', 'CPU, memory, and network summary with shared-success cases'))+' · '+link('../resource-cohort/results.jsonl',t('per_call_resource_cohort_records', 'Per-call resource-cohort records'))+' · '+link('../resource-cohort/execution.json',t('container_environment_and_image_identity', 'Container environment and image identity'))+'</p>')
 conditions=(t('the_final_report_includes_calls_execution_began', 'The final report includes {count:,} calls. Execution began after tasks and references were frozen; calls were sequential within each method and did not reuse personal login state.', count=len(records)) if authored_interpretation else t('this_report_includes_logical_results_run_parameters', 'This report includes {count:,} logical results. Run parameters are listed below; each result detail contains its retained physical calls and outputs.', count=len(records)))
 support+=section(t('shared_conditions', 'Shared conditions'),'<p>'+conditions+'</p>'+table([t('parameter', 'Parameter'),t('value', 'Value')],common,metrics=[]))
 if local_matrix:
  support+=section(t('method_versions', 'Method versions'),('<p>' + t('software_versions_or_source_commits_used_for', 'Software versions or source commits used for this run. Undisclosed cloud backend versions are stated explicitly; this does not mean the service has no version.') + f"</p>{table([t('method', 'Method'), t('version_source_commit_used_in_this_run', 'Version / source commit used in this run')], provider_rows, metrics=[])}"))
 else:
  support+=section(t('method_versions', 'Method versions'),('<p>' + t('versions_use_call_time_records_where_available', 'Versions use call-time records where available; source implementations use frozen commits. Undisclosed cloud backend versions are stated explicitly. Binary hashes identify the executable, and retrospectively verified versions state their evidence.') + f"</p>{table([t('method', 'Method'), t('version', 'Version'), t('identity_evidence', 'Identity evidence'), t('deployment', 'Deployment'), t('binary_sha_256', 'Binary SHA-256'), t('calls', 'Calls')], provider_rows, metrics=[])}"))
 if local_matrix:
  options_path=out/'frozen-method-options.json'
  if options_path.is_file():
   options_document=load_frozen_method_options(out,records)
   options=options_document['providers']
   labels=NATIVE_OPTION_LABELS
   def option_text(value):
    return t('on', 'On') if value is True else t('close', 'Close') if value is False else '、'.join(map(str,value)) if isinstance(value,list) else str(value)
   config_rows=[('',cell(link('../providers/'+name+'.html',provider_label(name),browser_method_label(name,language='en')))+cell(''.join('<div>'+esc(labels.get(k,k))+'：<code>'+esc(option_text(v))+'</code></div>' for k,v in value.items()))) for name,value in options.items()]
   support+=section(model_text(options_document['scope_title'],options_document['scope_title_en']),
                    '<p>'+model_text(options_document['scope_description'],options_document['scope_description_en'])+'</p>'
                    +table([t('method', 'Method'),t('fixed_options', 'Fixed options')],config_rows,metrics=[]))
  support+=local_call_chain_section(json.loads((out/'dataset.json').read_text(encoding='utf-8')))
 if not local_matrix:
  support+=section(t('publishing_and_loading', 'Publishing and loading'),('<p>' + t('the_local_build_creates_an_independently_inspectable', 'The local build creates an independently inspectable logical route for every task, method, and evidence item; this does not mean the deployment contains the same number of standalone HTML shells. The publisher compiles the report into one reusable shell, shared styles and scripts, and content-addressed compressed data blocks. Client-side routing changes pages, evidence loads only when its route is opened, and complete text over 256 KiB requires explicit user loading.') + '</p><p class="source-note">' + t('the_publication_manifest_records_logical_routes_source', 'The publication manifest records logical routes, source-file count, compressed blocks, and per-file SHA-256. The publication gate reconstructs every file and validates internal links plus original download bytes. This retains item-level auditability without duplicating page shells or loading all evidence at once.') + '</p>'))
 if not authored_interpretation:
  reproduce=('<p>' + t('run_with_this_task_and_method_configuration', 'Run with this task and method configuration, or rebuild the report from saved results:') + '</p><pre>browser-harness-eval run --dataset DATASET --provider-config CONFIG --providers METHODS --output NEW_EVIDENCE\nbrowser-harness-eval report EVIDENCE</pre><p>' + t('rerunning_calls_the_configured_methods_rebuilding_reads', 'Rerunning calls the configured methods. Rebuilding reads saved results only. Parameters, versions and executable hashes are listed above.') + '</p>')
 elif headless:
  reproduce+=('<p>' + t('runtime_configuration', 'Runtime configuration:') + '<code>configs/providers.headless.json</code>' + t('credential_variable_names_are_listed_on_access', '; credential variable names are listed on Access & setup, while actual secrets exist only in the ignored repository-root') + ' <code>.env</code>' + t('reference_freezing_complete_reruns_and_review_gates', '. Reference freezing, complete reruns, and review gates are documented in') + f" {link(repo + '/blob/main/harness/web_fetch/HEADLESS.md', t('repository_reproduction_guide', 'repository reproduction guide'))}。</p>")
 else:
  reproduce+=('<p>' + t('see_methods_access_and_the_version_table', 'See Methods, Access, and the version table above for method configuration, credential variable names, and measured versions. Headless-only configuration that does not exist for this cohort is out of scope.') + '</p>')
 if local_matrix:
  frozen_binding=(out/'presentation-binding.json').is_file()
  build_binding=' --presentation-binding /absolute/path/to/report-evidence/presentation-binding.json' if frozen_binding else ''
  check_binding=' --presentation-binding /absolute/path/to/report/presentation-binding.json' if frozen_binding else ''
  reproduce=('<p>' + t('these_commands_build_the_local_report_from', 'These commands build the local report from saved evidence without additional product calls:') + f"</p><pre>uv run --frozen python scripts/build_report.py --local-audit --language {('zh --include-english' if _RENDER_CONTEXT.get().english_generated else 'zh')} --evidence /absolute/path/to/report-evidence --output /absolute/path/to/report{build_binding}\nuv run --frozen python scripts/check_report.py /absolute/path/to/report{check_binding}</pre><p>" + t('use_the_evidence_directory_corresponding_to_these', 'Use the evidence directory corresponding to these results. Call-time versions, configuration, and scorer hashes are in') + f" {link('../local-run-contract.json', t('pre_call_frozen_protocol', 'Pre-call frozen protocol'))} " + t('and', 'and') + f" {link('../execution.json', t('execution_batches', 'Execution batches'))}" + t('the_build_commands_read_saved_scores_after', '. The build commands read saved scores. After changing scoring rules, rescore the declared scope before rebuilding; rebuilding pages alone does not update scores.') + '</p>')
 if local_matrix and frozen_binding:
  public_profile=json.loads((out/'presentation-binding.json').read_text(encoding='utf-8'))
  if public_profile.get('publication_projection'):
   reproduce+='<p>'+model_text(*pair('this_public_copy_omits_host_paths_the', 'This public copy omits host paths. The manifest distinguishes original and public file hashes; public hashes do not claim to be original call identities. Scores, timings and review decisions are unchanged.'))+' '+link('../'+public_profile['publication_projection']['path'],'PUBLICATION-PROJECTION.json')+'</p>'
  if public_profile.get('publication_evidence_views'):
   reproduce+='<p>'+model_text(*pair('scoring_originals_remain_private_public_answers_and', 'Scoring originals remain private. Public answers and complete native responses hide paths or sensitive values. The inventory preserves original scoring identities and separately binds public views. Public files rebuild this report but cannot reconstruct omitted content. Text-quality diagnostics retain the original calculations and may not reproduce exactly from redacted text. Review coordinates refer to originals; public views verify only the visible excerpts.'))+' '+link('../'+public_profile['publication_evidence_views']['path'],'PUBLIC-EVIDENCE-VIEWS.json')+'</p>'
 support+=section(t('core_reproduction_commands', 'Core reproduction commands'),reproduce)
 write(out,'reproduce/index.html',t('reproduction', 'Reproduction'),support)
 write(out,'accounts/index.html',t('access_setup', 'Access & setup'),render_account_requirements(records))

 write(out,'adoption/index.html',t('methods', 'Methods'),render_methods(out,records,hero=hero,section=section,table=table,cell=cell,link=link,esc=esc,authored=authored_interpretation))


_WMB_ROUTES=[
 ('datasets/index.html',t('dataset', 'Dataset'),'Dataset'),('methodology/index.html',t('methodology', 'Methodology'),'Methodology'),
 ('analysis/index.html',t('analysis', 'Analysis'),'Analysis'),('review/index.html',t('review_record', 'Review record'),'Review'),
 ('guide/index.html',t('interpretation', 'Interpretation'),'Guide'),('adoption/index.html',t('methods', 'Methods'),'Methods'),
 ('reproduce/index.html',t('reproduction', 'Reproduction'),'Reproduction'),('changes/index.html',t('changelog', 'Changelog'),'Changes'),
 ('accounts/index.html',t('access_setup', 'Access & setup'),'Accounts and setup'),
]


def _wmb_html_preview(source,english=False):
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
  placeholder.string=(report_text('en' if english else 'zh')('image_not_loaded', '[Image not loaded'))+(': '+img.get('alt','') if img.get('alt') else '')+(']' if english else '］')
  img.replace_with(placeholder)
 policy="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"
 document='<meta http-equiv="Content-Security-Policy" content="'+html.escape(policy,quote=True)+'"><style>body{margin:20px;color:#172331;background:white;overflow-wrap:anywhere}img,table{max-width:100%}pre{white-space:pre-wrap}</style>'+str(soup)
 title=report_text('en' if english else 'zh')('frozen_html_reading_view', 'Frozen HTML reading view')
 return '<iframe class="wmb-html-preview" sandbox="" referrerpolicy="no-referrer" loading="lazy" title="'+title+'" srcdoc="'+html.escape(document,quote=True)+'"></iframe>'


def _wmb_table_example(english=False):
 h=report_text('en' if english else 'zh')
 examples=[
  (h('recognized_complete', 'Recognized, complete'), '| Name | Value |\n| --- | --- |\n| A | 10 |\n| B | 20 |', '<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>20</td></tr></table>',h('enters_table_comparison_an_exact_reference_match', 'Enters table comparison; an exact reference match scores full marks.')),
  (h('recognized_one_row_missing', 'Recognized, one row missing'), '| Name | Value |\n| --- | --- |\n| A | 10 |', '<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>10</td></tr></table>',h('still_scored_missing_content_and_structure_reduce', 'Still scored; missing content and structure reduce the score. Complete recovery is not a prerequisite.')),
  (h('text_exists_table_not_recognized', 'Text exists, table not recognized'), 'Name Value A 10 B 20', '<p>Name Value A 10 B 20</p>',h('with_a_table_in_the_reference_both', 'With a table in the reference, both table scores are zero; this text still contributes to text_edit.')),
 ]
 return '<div class="wmb-table-examples">'+''.join('<article><h3>'+title+'</h3><pre>'+esc(md)+'</pre><div class="wmb-table-render">'+rendered+'</div><p>'+note+'</p></article>' for title,md,rendered,note in examples)+'</div><p class="source-note">'+h('these_are_rule_illustrations_not_new_measurements', 'These are rule illustrations, not new measurements. A table-like appearance is insufficient: the scorer must recognize a table in the output. Table_edit compares normalized table HTML strings; TEDS compares rows, cells and content. Missing structure affects both.')+'</p>'


def _wmb_denominator_example(records, english, case_count, provider=None):
 h=report_text('en' if english else 'zh')
 if not records:return ''
 provider=provider or records[0]['provider']
 selected=[row for row in records if row['provider']==provider]
 if any('_body_present' not in row or not row.get('metrics',{}).get('formula_edit') or (row.get('metrics',{}).get('formula_edit',{}).get('success') and not {'groundtruth_formula_length','predicted_formula_length'} <= set(row['metrics']['formula_edit'].get('details',{}))) for row in selected):return '<p>'+h('formula_scoring_population_unavailable', 'Formula-scoring population unavailable')+'</p>'
 counts=Counter()
 for row in records:
  if row['provider']!=provider:
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
   return '<p>'+h('formula_scoring_population_unavailable', 'Formula-scoring population unavailable')+'</p>'
 scored=sum(value for key,value in counts.items() if isinstance(key,tuple) and key!=(False,False))
 if sum(counts.values())!=case_count or scored!=counts[(False,True)]+counts[(True,False)]+counts[(True,True)]:
  raise ValueError('Formula denominator example does not cover the frozen cohort')
 successful=case_count-counts['no_body']
 flow_source='flowchart LR\n A["'+str(case_count)+h('frozen_pages', ' frozen pages')+'"] --> B{"'+h('final_call_succeeded', 'Final call succeeded?')+'"}\n B --> C["'+str(successful)+h('successful_pages', ' successful pages')+'"]\n C --> D["'+str(scored)+h('formula_scored_pages', ' formula-scored pages')+'"]'
 fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback><span>'+str(case_count)+h('frozen_pages', ' frozen pages')+'</span><span>'+str(successful)+h('calls_succeeded', ' calls succeeded; ')+str(counts['no_body'])+h('failed_and_are_not_scored', ' failed and are not scored')+'</span><span>'+str(counts[(False,False)])+h('pages_have_no_formula_on_either_side', ' pages have no formula on either side and are skipped; ')+str(scored)+h('pages_enter_the_formula_mean_including_zeros', ' pages enter the formula mean, including zeros')+'</span></div>'
 matrix='<div class="table-wrap"><table class="wmb-denominator-matrix"><thead><tr><th>'+h('reference_output', 'Reference / output')+'</th><th>'+h('no_output_formula', 'No output formula')+'</th><th>'+h('output_formula_present', 'Output formula present')+'</th></tr></thead><tbody>'
 matrix+='<tr><td>'+h('no_reference_formula', 'No reference formula')+'</td><td><b>'+str(counts[(False,False)])+'</b><span>'+h('both_empty_skip', 'Both empty: skip')+'</span></td><td><b>'+str(counts[(False,True)])+'</b><span>'+h('output_only_zero_enters', 'Output only: zero enters')+'</span></td></tr>'
 matrix+='<tr><td>'+h('reference_formula_present', 'Reference formula present')+'</td><td><b>'+str(counts[(True,False)])+'</b><span>'+h('reference_only_zero_enters', 'Reference only: zero enters')+'</span></td><td><b>'+str(counts[(True,True)])+'</b><span>'+h('both_present_similarity_enters', 'Both present: similarity enters')+'</span></td></tr></tbody></table></div>'
 return '<div class="wmb-denominator-example"><h3 id="denominator-example">'+h('formula_scoring_population', 'Formula-scoring population: ')+esc(provider)+' · '+str(scored)+' / '+str(case_count)+'</h3><div class="wmb-flow">'+fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'+matrix+'<p class="source-note">'+h('the_formula_mean_uses_the_upper_right', 'The formula mean uses the upper-right, lower-left and lower-right cells: {scored} pages, including {value} zero scores. Recognition in the two Markdown files determines the denominator; it is neither the body-text count nor a count of correct pages.', scored=scored, value=counts[False, True] + counts[True, False])+'</p></div>'


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
 h=report_text('en' if english else 'zh')
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
  rows.append(('',cell(link(prefix+'providers/'+name+'.html',labels[name]))+cell(str(eligible),eligible)+cell(str(extra),extra)+cell('<b>'+str(eligible+extra)+'</b>',eligible+extra)+cell(link(prefix+'attempts/'+example+'.html',h('inspect_output_only_tables', 'Inspect output-only tables')) if example else '—')))
 return '<div class="wmb-table-population"><h3>'+h('why_do_table_scoring_counts_differ', 'Why do table-scoring counts differ?')+'</h3><p>'+h('every_page_has_a_human_reference_but', 'Every page has a human reference, but only {count} references contain recognizable tables. After a successful call, every reference-table page is scored. A page with tables only in the output also enters the average at zero. Only pages with no tables on either side are skipped.', count=len(reference_pages))+'</p>'+table([h('method', 'Method'),h('reference_table_successful_call', 'Reference table, successful call'),h('output_only_table', 'Output-only table'),h('scored_pages_sum', 'Scored pages (sum)'),h('raw_evidence', 'Raw evidence')],rows,cls='wmb-table-population-table',metrics=[])+'<p class="source-note">'+h('output_only_tables_may_come_from_page', 'Output-only tables may come from page regions excluded from the main-content reference, or structures parsed as tables; the human reference is not missing. Two Kitesurf calls on reference-table pages failed and were excluded before scoring.')+'</p></div>'


def _wmb_timing_cell(stats,name,key):
 value=stats[name][key]
 values=[item[key] for item in stats.values() if item[key] is not None]
 minimum=min(values) if values else None
 marked=value is not None and value==minimum and values.count(minimum)==1
 content=ms(value)
 if marked:content='<strong class="comparison-winner">'+content+'</strong>'
 return cell(content,value).replace('<td', '<td data-metric-direction="min" data-metric="'+key+'"',1)


def _wmb_timing_note(english=False,matched=False):
 if matched:return (report_text('en' if english else 'zh')('green_marks_the_unique_minimum_in_each', 'Green marks the unique minimum in each column. P50 is the median; P95 is the time within which 95% finish.'))
 return (report_text('en' if english else 'zh')('green_marks_the_unique_maximum_score_or', 'Green marks the unique maximum score or minimum duration in this column. Timing uses final successful fetches only. P50: half finish within this time; P95: 95% finish within this time. Success counts define the timing population; scoring counts do not.'))


def _wmb_matched_latency(records,names,labels,english=False,prefix='../'):
 h=report_text('en' if english else 'zh')
 data=_wmb_latency_summary(records,names);common=data['common_cases']
 excluded=sorted({r['case_id'] for r in records}-common)
 rows=[]
 for name in names:
  rows.append(('',cell(link(prefix+'providers/'+name+'.html',labels[name]))+''.join(_wmb_timing_cell(data['matched'],name,key) for key in ('mean','p50','p95'))))
 return '<div class="wmb-matched-latency"><h3>'+h('matched_page_timing_pages_with_success_across', 'Matched-page timing: {count} pages with success across all methods', count=len(common))+'</h3><p>'+h('every_row_uses_the_same_pages_and', 'Every row uses the same {count} pages and the duration of the final successful fetch only. This diagnoses same-page timing differences. The shared-success subset is determined after execution, so it does not rank speed over the entire declared benchmark.', count=len(common))+'</p><p class="source-note">'+_wmb_timing_note(english,matched=True)+'</p>'+table([h('method', 'Method'),h('mean_latency_2', 'Mean latency'),h('p50_latency', 'P50 latency'),h('p95_latency', 'P95 latency')],rows,cls='wmb-matched-latency-table',metrics=[])+'<details><summary>'+h('pages_outside_this_comparison_retained_in_the', '{count} pages outside this comparison (retained in the full matrix)', count=len(excluded))+'</summary><ul>'+''.join('<li>'+link(prefix+'datasets/'+case_id+'.html',case_id)+'</li>' for case_id in excluded)+'</ul></details></div>'


def _wmb_latency_rerun(diagnostic,english=False,prefix='',formal_round=None):
 """Render recorded crossover summaries without assuming versions or cohort size."""
 h=report_text('en' if english else 'zh')
 if not diagnostic:return ''
 runs=diagnostic['balanced_200']['runs']
 rows=[]
 for version,run in runs.items():
  values=[]
  for key in ('mean','p50','p95'):
   comparable=[other[key] for other in runs.values()]
   value=run[key]
   won=value==min(comparable) and comparable.count(value)==1
   content='<strong class="comparison-winner">'+ms(value)+'</strong>' if won else ms(value)
   values.append(cell(content,value).replace('<td',f'<td data-metric="rerun-{key}" data-metric-direction="min"',1))
  rows.append(('',cell(esc(version))+cell(f"{run['n']}/{run['calls']}")+''.join(values)))
 body='<p>'+h('mean_p50_and_p95_use_successful_calls', 'Mean, P50 and P95 use successful calls with body text; successful and total call counts are shown together. Versions, matched tasks, call order and the selected round follow the bound receipts; failed calls remain retained.')+'</p>'
 if formal_round is not None:
  body+=f'<p class="source-note" data-timing-context="separate-diagnostic" data-formal-round="{int(formal_round)}">'+h('this_table_retains_an_earlier_diagnostic_formal', 'This table retains an earlier diagnostic. Formal home-page timing comes from preselected round {formal_round} of a separate certified receipt. Their calls and environment conditions are bound separately and are not one measurement.', formal_round=formal_round)+'</p>'
 body+=table([h('version', 'Version'),h('successful_all_calls', 'Successful / all calls'),h('mean_latency_2', 'Mean latency'),h('p50_latency', 'P50 latency'),h('p95_latency', 'P95 latency')],rows,cls='wmb-rerun-table',metrics=[])
 tail=diagnostic['tail_repeat'];cases=tail['cases']
 outliers=[row for row in cases if abs(row['older_median_ms']-row['newer_median_ms'])>20]
 if tail['within_20_ms']+len(outliers)!=len(cases):
  raise ValueError('Latency tail summary disagrees with the per-page evidence')
 if cases:
  body+='<details><summary>'+h('inspect_repeated_calls_for_pages', 'Inspect repeated calls for {count} pages', count=len(cases))+'</summary>'
  body+=table([h('page', 'Page'),h('earlier_version_median', 'Earlier-version median'),h('later_version_median', 'Later-version median'),h('earlier_version_maximum', 'Earlier-version maximum'),h('later_version_maximum', 'Later-version maximum')],[('',cell(link(prefix+'datasets/'+row['case_id']+'.html',row['case_id']))+cell(ms(row['older_median_ms']),row['older_median_ms'])+cell(ms(row['newer_median_ms']),row['newer_median_ms'])+cell(ms(row['older_max_ms']),row['older_max_ms'])+cell(ms(row['newer_max_ms']),row['newer_max_ms'])) for row in cases],cls='wmb-rerun-cases',metrics=[])
  body+='</details>'
 resource_rows=[]
 for name,resource in diagnostic['resource'].items():
  resource_rows.append(('',cell(esc(name))+cell(str(resource['samples']))+cell(f"{resource['min_idle_cpu_pct']:.1f}%")+cell(f"{resource['min_unused_memory_mb']:.0f} MB")+cell(str(resource['swapouts_delta']))))
 newer_faster=sum(row['newer_median_ms']<row['older_median_ms'] for row in outliers)
 older_faster=len(outliers)-newer_faster
 body+=f'<p data-tail-outliers="{len(outliers)}" data-tail-newer-faster="{newer_faster}" data-tail-older-faster="{older_faster}">'+h('pages_were_repeated_among_pages_differing_by', '{count} pages were repeated; among pages differing by more than 20 ms in median latency, the later version was faster on {newer_faster} and the earlier version on {older_faster}.', count=len(cases), newer_faster=newer_faster, older_faster=older_faster)+'</p>'
 failure=diagnostic.get('failure_retest') or {}
 if failure.get('case_id'):
  body+='<p>'+h('failure_retests_record_calls_per_version_formal', 'Failure retests record {get} calls per version; formal failed results remain unchanged.', get=failure.get('attempts_per_version', '—'))+' '+link(prefix+'datasets/'+failure['case_id']+'.html',h('inspect_the_failed_page', 'Inspect the failed page'))+'</p>'
  if failure.get('script_disabled_returns_body'):
   body+='<p>'+h('disabling_page_authored_javascript_returned_body_text', 'Disabling page-authored JavaScript returned body text in the diagnostic calls. This changed the execution condition and does not replace formal outcomes.')+'</p>'
 body+=table([h('recorded_run', 'Recorded run'),h('system_samples', 'System samples'),h('minimum_idle_cpu', 'Minimum idle CPU'),h('minimum_unused_memory', 'Minimum unused memory'),h('new_swap_outs', 'New swap-outs')],resource_rows,cls='wmb-rerun-resource',metrics=[])
 body+='<p class="source-note">'+h('host_samples_alone_do_not_establish_that', 'Host samples alone do not establish that load caused slow calls. Responses, failure retests and evidence bindings remain in the source receipt.')+' '+link(('../' if english else '')+prefix+'latency-diagnostic.json',h('download_repeated_call_evidence', 'Download repeated-call evidence'))+'</p>'
 return '<div class="wmb-rerun-diagnostic">'+body+'</div>'


def _wmb_guide(*, english, names, labels, cases, bycase, case_count, score, denom,
               failures, out=None, latency_diagnostic=None, formal_round=None):
 h=report_text('en' if english else 'zh')
 records=[row for rows in bycase.values() for row in rows]
 metrics=['overall','text_edit','code_edit','formula_edit','table_edit','table_TEDS']
 def chapter(key,title,body):return '<section id="'+key+'"><h2>'+title+'</h2>'+body+'</section>'
 def paragraph(key,source,**values):return '<p>'+h(key,source,**values)+'</p>'
 def highlighted(name,metric):
  value=score(name,metric)
  values=[score(candidate,metric) for candidate in names]
  present=[v for v in values if v is not None]
  winner=value is not None and present and value==max(present) and present.count(value)==1
  text=_wmb_pct(value)
  return '<span class="comparison-winner">'+text+'</span>' if winner else text
 rows=[]
 for name in names:
  body=cell(link('../providers/'+name+'.html',labels.get(name,name)))
  for metric in metrics:
   body+=cell(highlighted(name,metric)+'<small>'+str(denom(name,metric) or 0)+' / '+str(case_count)+'</small>',score(name,metric))
  rows.append(('',body))
 overall=chapter('overall',h('current_results', 'Current results'),
  paragraph('this_run_contains_pages_and_methods_scores', 'This run contains {case_count} pages and {count} methods. Scores come from saved results; counts below each score show its population. Green marks the unique column maximum.', case_count=case_count, count=len(names))+
  table([h('method', 'Method'),h('overall', 'Overall'),h('full_text_similarity', 'Full-text similarity'),h('code_similarity', 'Code similarity'),h('formula_similarity', 'Formula similarity'),h('table_content_similarity', 'Table-content similarity'),h('table_structure_similarity', 'Table-structure similarity')],rows,cls='wmb-guide-results',metrics=[]))
 scope=chapter('scope',h('inputs_and_scoring_scope', 'Inputs and scoring scope'),
  paragraph('inputs_are_frozen_html_and_references_cover', 'Inputs are frozen HTML and references cover selected main content. Similarity reflects omissions, additional content and formatting; it is not a direct fraction of body text retrieved. Component populations may differ.')+
  _wmb_denominator_example(records,english,case_count))
 def specimen(metric):
  if not cases:return paragraph('no_task_is_available', 'No task is available.')
  case_id=next(iter(cases))
  selected=bycase.get(case_id,[])
  stats=[]
  for row in selected:
   details=row.get('metrics',{}).get(metric,{})
   length=(details.get('details') or {}).get('predicted_text_length')
   length_text=f'{length:,}' if length is not None else h('length_not_recorded', 'Length not recorded')
   stats.append('<span>'+esc(labels.get(row['provider'],row['provider']))+': '+_wmb_pct(details.get('score'))+' · '+length_text+'</span>')
  evidence=''
  if out is not None:
   paths=[(h('reference', 'Reference'),Path('references')/(case_id+'.md'))]
   if selected:paths.append((labels.get(selected[0]['provider'],selected[0]['provider']),Path(selected[0]['artifact_path'])))
   for title,relative in paths:
    path=contained_path(out,relative,reject_symlinks=True)
    if path.is_file():
     evidence+='<article><h3>'+esc(title)+'</h3><pre>'+esc(path.read_text(encoding='utf-8')[:550])+'</pre></article>'
  return '<div class="wmb-case-study">'+link('../datasets/'+case_id+'.html',h('inspect_the_task_and_every_output', 'Inspect the task and every output'))+'<div class="wmb-evidence-pair">'+evidence+'</div><div class="wmb-case-stats">'+''.join(stats)+'</div></div>'
 content=chapter('content',h('output_and_reference', 'Output and reference'),specimen('text_edit'))
 tables=chapter('tables',h('table_recognition_and_structure', 'Table recognition and structure'),
  _wmb_table_example(english)+_wmb_table_population(records,names,labels,english)+specimen('table_TEDS'))
 version=chapter('version-differences',h('evidence_of_method_differences', 'Evidence of method differences'),
  paragraph('per_page_results_retain_outputs_component_scores', 'Per-page results retain outputs, component scores and errors for each method. Causes of version differences require review evidence bound to these results; aggregate scores alone do not establish them.')+
  link('../analysis/index.html',h('compare_same_page_results', 'Compare same-page results')))
 timing=_wmb_latency_summary([row for row in records if '_body_present' in row],names)['successful']
 timing_rows=[]
 for name in names:
  item=timing[name]
  values=cell(labels.get(name,name))+cell(item['count'])
  for key in ('mean','p50','p95'):
   present=[value[key] for value in timing.values() if value[key] is not None]
   value=item[key]
   winner=value is not None and present and value==min(present) and present.count(value)==1
   display=ms(value)
   values+=cell('<span class="comparison-winner">'+display+'</span>' if winner else display,value)
  timing_rows.append(('',values))
 delivery=chapter('delivery',h('delivery_and_latency', 'Delivery and latency'),
  paragraph('mean_p50_and_p95_use_each_method', 'Mean, P50 and P95 use each method’s final calls that successfully deliver body text, with sample counts shown. Failed-call and retry execution costs remain in the result evidence.')+
  table([h('method', 'Method'),h('delivered_pages', 'Successful pages'),h('mean_latency_2', 'Mean latency'),'P50','P95'],timing_rows,cls='wmb-guide-latency',metrics=[]))
 if latency_diagnostic is not None:
  delivery=delivery.removesuffix('</section>')+_wmb_latency_rerun(latency_diagnostic,english,prefix='../',formal_round=formal_round)+'</section>'
 decision=chapter('decision',h('using_these_results', 'Using these results'),
  paragraph('use_component_scores_and_per_page_evidence', 'Use component scores and per-page evidence for the required output. Conclusions apply to the declared inputs, versions and conditions; a new experiment needs its own evidence and interpretation.'))
 return '<div class="wrap wmb-editorial"><article><h1>'+h('reading_the_results', 'Reading the results')+'</h1>'+overall+scope+content+tables+version+delivery+decision+'</article></div>'


def _wmb_write(out,path,title,body,english=False):
 if english:
  body=body.replace(('<div class="eyebrow">' + t('web_fetch_evaluation_report', 'Web Fetch · Evaluation report') + '</div>'),'<div class="eyebrow">Web Fetch · Measured report</div>')
  body=body.replace('aria-label="内容选择"','aria-label="Choose content"').replace(('<th>' + t('no', 'No.') + '</th>'),'<th>No.</th>')
 local=('en/'+path) if english else path;depth=len(Path(local).parts)-1;base='../'*depth
 nav=''.join(link(base+('en/' if english else '')+route,en if english else zh) for route,zh,en in _WMB_ROUTES)
 brand=link(base+('en/index.html' if english else 'index.html'),'Web Fetch')
 counterpart=base+path if english else base+'en/'+path
 language=f'''<a href="{safe_href(counterpart)}">{('中文' if english else 'English')}</a>'''
 footer=(report_text('en' if english else 'zh')('scores_apply_to_the_frozen_inputs_and', 'Scores apply to the frozen inputs and methods declared in this report. They do not represent the live web or the official WebMainBench leaderboards.'))
 page=f'''<!doctype html><html lang="{('en' if english else 'zh-CN')}"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Fetch</title><link rel="stylesheet" href="{base}assets/style.css"><script src="{base}assets/report.js" defer></script></head><body><header class="top"><nav class="wrap">{brand}{nav}{language}<button class="theme-toggle" onclick="theme()">{report_text('en' if english else 'zh')('toggle_theme', 'Theme')}</button></nav></header><main>{body}</main><footer class="wrap source-note"><p>{footer}</p></footer></body></html>'''
 page=page.replace(f'href="{base}assets/style.css"',f'href="{base}assets/style.css?v={hashlib.sha256((STYLE+EXTRA).encode()).hexdigest()[:16]}"').replace(f'src="{base}assets/report.js"',f'src="{base}assets/report.js?v={hashlib.sha256(JS.encode()).hexdigest()[:16]}"')
 target=contained_path(out,local);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(page,encoding='utf-8')


def _wmb_category(case,axis):
 value=case['meta'].get(axis)
 return '+'.join(value) if isinstance(value,list) and value else ('none' if isinstance(value,list) else str(value or 'unavailable'))


def _wmb_inspect(base='analysis/index.html',**filters):
 values={key:value for key,value in filters.items() if value is not None and value!=''}
 return base+'#inspect?'+urlencode(values)


def _wmb_controls(cases,method_labels,english=False,include_method=True,include_metric=True,default_request='',failure_types=(),include_delivery=True):
 h=report_text('en' if english else 'zh')
 axes=('language','level','table','code','equation')
 axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
 categories=sorted({_wmb_category(case,axis) for case in cases.values() for axis in axes})
 fields=[]
 def select(key,label,options,default=''):
  entries=''.join(f'<option value="{esc(value)}"'+(' selected' if value==default else '')+f'>{esc(text)}</option>' for value,text in options)
  fields.append(f'<label>{label}<select data-wmb-filter="{key}" data-wmb-default="{esc(default)}" aria-label="{label}">{entries}</select></label>')
 if include_method:
  select('method',h('method', 'Method'),[('',h('all_methods', 'All methods'))]+[(key,value) for key,value in method_labels.items()])
 select('axis',h('category_dimension', 'Category dimension'),[('',h('all_categories', 'All categories'))]+[(axis,axis_labels[axis]) for axis in axes])
 select('category',h('category', 'Category'),[('',h('all', 'All'))]+[(value,value) for value in categories])
 if include_delivery:
  select('request',h('body_text_produced', 'Body text produced'),[('',h('all', 'All')),('failed',h('no_body_text', 'No body text')),('success',h('body_text_produced_2', 'Body text produced'))],default_request)
  if failure_types:
   select('failure',h('error_type', 'Error type'),[('',h('all', 'All'))]+[(value,value) for value in failure_types])
 if include_metric:
  select('metric',h('inspect_metric', 'Inspect metric'),[('',h('all_metrics', 'All metrics'))]+[(key,key) for key in ('text_edit','code_edit','formula_edit','table_edit','table_TEDS')])
  select('band',h('metric_score_band', 'Metric score band'),[('',h('any', 'Any')),("0:0.1",'0–10%'),("0:0.25",'0–25%'),("0.25:0.5",'25–50%'),("0.5:0.75",'50–75%'),("0.75:1",'75–100%'),('unavailable',h('unavailable_not_applicable', 'Unavailable / not applicable'))])
 fields.append(f'''<label>{h('search_track_id_url_or_error', 'Search track ID, URL or error')}<input data-wmb-filter="query" aria-label="{h('search', 'Search')}" placeholder="{h('enter_keywords', 'Enter keywords')}"></label>''')
 return '<div class="wmb-controls">'+''.join(fields)+'</div><div class="wmb-toolbar"><strong data-wmb-count></strong><button type="button" onclick="wmbReset()">'+h('clear_filters', 'Clear filters')+'</button></div>'


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
 if _wmb_error_page(text):return (report_text('en' if english else 'zh')('error_page_in_output', 'Error page in output'))+f' · {chars:,}'
 if not record.get('ok'):
  return ((report_text('en' if english else 'zh')('error_response', 'Error response'))+f' · {chars:,}' if chars else (report_text('en' if english else 'zh')('no_readable_response', 'No readable response')))
 if not chars:return report_text('en' if english else 'zh')('empty_body_text', 'Empty body text')
 return report_text('en' if english else 'zh')('body_text_characters', 'Body text · {chars:,} characters', chars=chars)


def _wmb_body_state(record,text,english=False):
 if _wmb_error_page(text):return report_text('en' if english else 'zh')('error_page_not_target_content', 'Error page, not target content')
 if not record.get('ok') or not text.strip():return report_text('en' if english else 'zh')('no_body_text', 'No body text')
 return report_text('en' if english else 'zh')('body_text_produced_3', 'Body text produced')


def _wmb_filter_table(headers,rows,id,english=False):
 result=table(headers,rows,id,True,metrics=[],index_header=report_text('en' if english else 'zh')('no', 'No.'))
 return result.replace('<div class="table-wrap">','<div class="table-wrap wmb-table-wrap">',1).replace('<table class=""','<table class="wmb-table" data-wmb-table',1)


def _wmb_comparison(cases,bycase,names,labels,english=False):
 h=report_text('en' if english else 'zh')
 default_a=names[0]
 options=''.join(f'<option value="{esc(name)}"'+(' selected' if name==default_a else '')+f'>{esc(labels[name])}</option>' for name in names)
 relations=[('mixed',h('body_text_outcome_differs', 'Body-text outcome differs')),('a_success_others_fail',h('a_produced_body_text_others_did_not', 'A produced body text; others did not')),('a_fail_others_success',h('a_produced_no_body_text_others_did', 'A produced no body text; others did')),('all_success',h('all_selected_methods_produced_body_text', 'All selected methods produced body text')),('all_fail',h('no_selected_method_produced_body_text', 'No selected method produced body text')),('exact_success',h('only_selected_methods_produced_body_text', 'Only selected methods produced body text'))]
 relation_options=''.join(f'<option value="{value}">{esc(label)}</option>' for value,label in relations)
 axes=('language','level','table','code','equation')
 axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
 axis_options='<option value="">'+h('all_categories', 'All categories')+'</option>'+''.join(f'<option value="{axis}">{axis_labels[axis]}</option>' for axis in axes)
 categories=sorted({_wmb_category(case,axis) for case in cases.values() for axis in axes})
 category_options='<option value="">'+h('all', 'All')+'</option>'+''.join(f'<option value="{esc(value)}">{esc(value)}</option>' for value in categories)
 checks=''.join('<label'+(' class="disabled"' if name==default_a else '')+f'><input type="checkbox" data-wmb-compare-method value="{esc(name)}"'+(' disabled' if name==default_a else ' checked' if name==names[1] else '')+f'> {esc(labels[name])}</label>' for name in names)
 controls='<div class="comparison-controls"><label>'+h('method_a', 'Method A')+'<select id="wmb-compare-a" data-wmb-compare-control>'+options+'</select></label><label>'+h('comparison_condition', 'Comparison condition')+'<select id="wmb-compare-relation" data-wmb-compare-control>'+relation_options+'</select></label><label>'+h('category_axis', 'Category axis')+'<select id="wmb-compare-axis" data-wmb-compare-control>'+axis_options+'</select></label><label>'+h('category', 'Category')+'<select id="wmb-compare-category" data-wmb-compare-control>'+category_options+'</select></label></div><fieldset class="comparison-methods"><legend>'+h('methods_to_compare_multiple_selection', 'Comparison methods (select multiple)')+'</legend><div class="method-checks">'+checks+'</div></fieldset>'
 rows=[]
 for case in cases.values():
  results={r['provider']:r for r in bycase[case['id']]}
  delivered=[name for name in names if results[name]['_body_present']]
  failed=[name for name in names if not results[name]['_body_present']]
  def pills(selected,kind,results=results):
   return '<span class="outcome-list">'+''.join('<a class="outcome-pill '+kind+'" href="../attempts/'+esc(results[name]['attempt_id'])+'.html">'+esc(labels[name])+'</a>' for name in selected)+'</span>' if selected else '—'
  values=cell(link('../datasets/'+case['id']+'.html',case['id']))+cell(esc(_wmb_category(case,'language'))+' · '+esc(_wmb_category(case,'level')))+cell(pills(delivered,'pass'))+cell(pills(failed,'fail'))
  attrs=' data-delivered="'+esc(' '.join(delivered))+'" data-failed="'+esc(' '.join(failed))+'"'+_wmb_row_attrs(case)
  rows.append((attrs,values))
  # All selected frozen pages remain in the DOM; filtering is client-side and evidence links stay per call.
 result='<div class="method-comparison"><div class="comparison-heading"><div><h3>'+h('body_text_on_the_same_page', 'Body text on the same page')+'</h3><p>'+h('this_only_compares_whether_non_empty_markdown', 'This only compares whether non-empty Markdown body text was produced by the deadline; it is not a content-quality pass. Open a method for scores, the human reference and actual output.')+'</p></div><strong class="comparison-count" id="wmb-compare-count"></strong></div>'+controls+'<div class="table-wrap"><table class="wmb-comparison-table"><thead><tr><th>'+h('no', 'No.')+'</th><th>Track ID</th><th>'+h('category', 'Category')+'</th><th>'+h('methods_with_body_text', 'Methods with body text')+'</th><th>'+h('methods_without_body_text', 'Methods without body text')+'</th></tr></thead><tbody id="wmb-compare-rows">'+''.join(f'<tr{attrs}><td>{i+1}</td>{values}</tr>' for i,(attrs,values) in enumerate(rows))+'</tbody></table></div></div>'
 return result


def _wmb_result_detail(r,c,output,reference,labels,metric_labels,failure_notes,corrected,english=False):
 h=report_text('en' if english else 'zh')
 dialogs=[]
 def button(target,label):
  return '<button type="button" class="call-log-button" data-call-log-open="'+target+'" aria-haspopup="dialog">'+esc(label)+'</button>'
 def dialog(target,title,body):
  dialogs.append('<dialog class="call-log-dialog wmb-detail-dialog" id="'+target+'" aria-labelledby="'+target+'-title"><div class="call-log-shell"><header><h2 id="'+target+'-title">'+esc(title)+'</h2><button type="button" data-call-log-close aria-label="'+h('close', 'Close')+'">'+h('close', 'Close')+'</button></header><div class="wmb-dialog-content">'+body+'</div></div></dialog>')
  return button(target,title)
 raw_prefix='../../' if english else '../'
 metadata=r.get('provider_metadata') or {}
 case_id=c['id'];failed=not r['_body_present']
 body=hero(labels[r['provider']],h('page_id', 'Page ID: ')+case_id)
 body+='<nav class="wrap actions">'+link('../analysis/index.html',h('all_results', 'All results'))+' · '+link('../analysis/index.html#inspect?query='+case_id,h('compare_methods_on_this_page', 'Compare methods on this page'))+' · '+link('../datasets/'+case_id+'.html#frozen-input',h('frozen_input_and_reference', 'Frozen input and reference'))+'</nav>'
 summary=[(h('result', 'Outcome'),_wmb_body_state(r,output,english)),(h('final_call_latency', 'Final-call latency'),ms(r['elapsed_ms'])),(h('per_page_score', 'Per-page score'),_wmb_pct(r.get('metrics',{}).get('overall',{}).get('score')) if r.get('metrics',{}).get('overall',{}).get('success') else '—')]
 body+=section(h('result_summary', 'Result summary'),'<div class="wmb-attempt-summary" data-result-summary>'+''.join('<article class="card"><b>'+esc(k)+'</b><strong>'+esc(v)+'</strong></article>' for k,v in summary)+'</div><p class="source-note">'+h('body_text_does_not_imply_quality_acceptance', 'Body text does not imply quality acceptance. Per-page score averages this page’s available components; the home score averages five cohort means.')+'</p>','result-summary')
 # Exact error and available stderr streams have one owner: the log dialog.
 error=r.get('error_message') or (output if _wmb_error_page(output) else '')
 streams=[]
 if error:streams.append((h('final_error_verbatim', 'Final error verbatim'),str(error),'error'))
 for key in ('stderr','browser_stderr','stdout'):
  value=metadata.get(key)
  if value and not any(str(value)==text for _,text,_ in streams):streams.append((key,str(value),key))
 log_html='<p>'+h('these_are_recorded_fields_from_the_final', 'These are recorded fields from the final call; warnings are not automatically root causes.')+'</p>'
 for label,text,kind in streams:
  lines=text.splitlines(keepends=True)
  matches=[i for i,line in enumerate(lines) if re.search(r'(?i)(\berror\b|^reason:|ERR_[A-Z_]+)',line)]
  locator=(matches[-1] if matches else len(lines)-1)
  target='log-'+kind+'-'+str(locator)
  log_html+='<h3>'+esc(label)+'</h3><button type="button" class="call-log-button" data-log-line="'+target+'">'+h('locate_last_error_record', 'Locate last error record')+'</button><pre class="wmb-log" data-kind="'+kind+'">'+''.join('<span id="log-'+kind+'-'+str(i)+'"'+(' class="wmb-log-focus" tabindex="-1"' if i==locator else '')+'>'+esc(line)+'</span>' for i,line in enumerate(lines))+'</pre>'
 if not streams:log_html+='<p data-log-unavailable>'+h('no_error_or_output_logs_were_retained', 'No error or output logs were retained for this call.')+'</p>'
 status={key:r.get(key) for key in ('ok','status_code','error_type','started_at','completed_at','elapsed_ms')}
 status.update({key:metadata[key] for key in ('exit_code','browser_exit_code','upstream_status','completion','stderr_truncated','stdout_truncated') if key in metadata})
 log_html+='<details><summary>'+h('call_status_and_timestamps', 'Call status and timestamps')+'</summary><pre>'+esc(json.dumps(status,ensure_ascii=False,indent=2))+'</pre></details>'
 log_button=dialog('result-logs',h('errors_and_runtime_logs', 'Errors and runtime logs'),log_html)
 if failed:
  reviewed=(r['provider'],case_id) in failure_notes
  observed,cause=failure_notes.get((r['provider'],case_id),('',''))
  stage=h('output_validation', 'Output validation') if r['ok'] else h('conversion_call_internal_stage_not_recorded', 'Conversion call (internal stage not recorded)')
  if not r['ok'] and re.search(r'(?i)(failed to navigate|failed to fetch|curl request failed)',str(error)):stage=h('navigation_or_page_request', 'Navigation or page request')
  diagnosis=[(h('failure_location', 'Failure location'),esc(stage)),(h('program_error_category', 'Program error category'),'<code>'+esc(r.get('_output_error') or r.get('error_type') or 'empty_body')+'</code>'),(h('reviewed_cause', 'Reviewed cause'),esc(cause) if reviewed else h('unverified', 'Unverified'))]
  body+=section(h('failure_diagnosis', 'Failure diagnosis'),'<dl class="wmb-diagnosis" data-failure-diagnosis>'+''.join('<dt>'+k+'</dt><dd>'+v+'</dd>' for k,v in diagnosis)+'</dl><p>'+log_button+' '+button('result-output',h('inspect_returned_content', 'Inspect returned content'))+'</p>'+('<details><summary>'+h('review_evidence', 'Review evidence')+'</summary><p>'+esc(observed)+'</p>'+link(raw_prefix+'webmainbench-anomaly-review.json',h('full_review_record', 'Full review record'))+'</details>' if reviewed else ''),'failure-diagnosis')
 metric_rows=[]
 for metric,label in metric_labels.items():
  item=r.get('metrics',{}).get(metric,{})
  detail='<pre data-kind="score">'+esc(json.dumps(item,ensure_ascii=False,indent=2))+'</pre>'
  if corrected and metric in ('overall','table_TEDS'):
   detail+='<details><summary>'+h('original_official_score', 'Original official score')+'</summary><pre>'+esc(json.dumps(r.get('official_metrics',{}).get(metric,{}),ensure_ascii=False,indent=2))+'</pre></details>'
  open_score=dialog('score-'+metric,label+' · '+h('scoring_evidence', 'Scoring evidence'),detail)
  metric_rows.append(('',cell(esc(label))+cell(h('calculated', 'Calculated') if item.get('success') else h('not_calculated', 'Not calculated'))+cell(_wmb_pct(item.get('score')) if item.get('success') else '—')+cell(open_score)))
 body+=section(h('component_scores', 'Component scores'),table([h('metric', 'Metric'),h('status', 'Status'),h('score', 'Score'),h('evidence_3', 'Evidence')],metric_rows,cls='wmb-detail-scores',metrics=[]),'component-scores')
 output_html='<p>'+link(raw_prefix+r['artifact_path'],h('download_method_output', 'Download method output'))+' · '+link(raw_prefix+'references/'+case_id+'.md',h('download_human_reference', 'Download human reference'))+'</p><div class="evidence-compare wmb-evidence">'+''.join('<div class="evidence-pane"><header><h3>'+title+'</h3></header><div class="pane-body"><pre data-kind="'+kind+'">'+esc(text)+'</pre></div></div>' for title,kind,text in [(h('human_reference', 'Human reference'),'reference',reference),(h('final_actual_output', 'Final actual output'),'output',readable_output(output))])+'</div>'
 output_button=dialog('result-output',h('reference_and_output_comparison', 'Reference and output comparison'),output_html)
 provenance={key:r.get(key) for key in ('run_id','attempt_id','case_id','input_html_sha256','reference_sha256','output_sha256')}
 fields={key:value for key,value in metadata.items() if key not in ('stderr','browser_stderr','stdout')}
 provenance_button=dialog('result-provenance',h('provenance_and_response_fields', 'Provenance and response fields'),'<h3>'+h('ids_and_hashes', 'IDs and hashes')+'</h3><pre>'+esc(json.dumps(provenance,ensure_ascii=False,indent=2))+'</pre><h3>'+h('response_fields', 'Response fields')+'</h3><pre data-kind="metadata">'+esc(json.dumps(fields,ensure_ascii=False,indent=2))+'</pre>')
 body+=section(h('evidence', 'Evidence'),'<div class="actions wmb-evidence-actions">'+output_button+('' if failed else log_button)+provenance_button+'</div>','result-evidence')
 return '<div data-report-detail="failed">'+body+''.join(dialogs)+'</div>' if failed else '<div data-report-detail="success">'+body+''.join(dialogs)+'</div>'


@with_measured_method_labels
def generate_webmainbench(out:Path,dataset:dict,records:list,summaries:dict,manifests:list,review:dict|None=None,research:dict|None=None,corrections:dict|None=None,adoption_data:dict|None=None,fixture_correction:dict|None=None,teds_revision:tuple|None=None,anomaly_review:dict|None=None,latency_diagnostic:dict|None=None,*,include_english:bool=False,report_content:dict|None=None):
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
 case_count=len(cases);method_count=len(names);result_count=case_count*method_count
 labels={name: browser_method_registry().get(name,{}).get('label') or (native_browser_engine(name) or name).capitalize() for name in names}
 def scope(text):
  return text.replace('{case_count}',str(case_count)).replace('{method_count}',str(len(names))).replace('{result_count}',str(result_count))
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
   label=report_text('en' if english else 'zh')('scored_on_pages', 'scored on {n}/{count} pages', n=n, count=count)
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
  h=lambda key,source,/,text_=report_text('en' if english else 'zh'),**values:scope(text_(key,source,**values))
  corrected_label=h('recomputed_after_correction', 'recomputed after correction')
  corrected_suffix=(f' ({corrected_label})' if english else f'（{corrected_label}）')
  metric_labels={name:name+(corrected_suffix if corrected and name in ('overall','table_TEDS') else '') for name in metric_order}
  status=(h('pages_methods_results_evidence_and_arithmetic_checks', '{case_count} pages × {count} methods = {result_count:,} results; evidence and arithmetic checks passed, but table-structure score validity did not.', case_count=case_count, count=len(names), result_count=result_count) if review and review.get('status')=='passed' else h('measurement_complete_independent_review_has_not_passed', 'Measurement complete; independent review has not passed, so this build is not publishable.'))
  if corrected:
   status=h('pages_methods_the_official_repository_and_leaderboard', '{case_count} pages × {count} methods. The official repository and leaderboard remain unchanged. An independent recomputation corrects the demonstrated TEDS defect on the same saved outputs.', case_count=case_count, count=len(names))
  home=hero(h('webmainbench_545_preselected_measured_methods', 'WebMainBench-545 · preselected {case_count} · {count} measured methods', case_count=case_count, count=len(names)),h('methods_read_the_same_frozen_html_and', '{count} methods read the same frozen HTML and are compared with the same human-calibrated Markdown; page-level responses, empty body text and causes live in result details.', count=len(names)))
  home+='<div class="wrap reliability'+('' if review and review.get('status')=='passed' else ' failure')+'"><strong>'+esc(status)+'</strong></div>'
  home+=section(h('what_this_ranking_measures', 'What this ranking measures'),'<div class="wmb-scope-map"><article><b>'+h('input_identical_archived_html', 'Input: identical archived HTML')+'</b><p>'+h('pages_methods_no_live_origin_fetch', '{case_count} pages × {count} methods; no live origin fetch.', case_count=case_count, count=len(names))+'</p></article><article><b>'+h('target_main_content_not_every_page_element', 'Target: main content, not every page element')+'</b><p>'+h('body_text_code_formulas_and_tables_navigation', 'Body text, code, formulas and tables; navigation, ads and recommendations are outside the target.')+'</p></article><article><b>'+h('score_similarity_to_the_human_main_content', 'Score: similarity to the human main-content reference')+'</b><p>'+h('extra_boilerplate_and_lost_table_structure_reduce', 'Extra boilerplate and lost table structure reduce scores. A low score does not mean the page was not retrieved.')+'</p></article></div><p class="actions">'+link('guide/index.html#content',h('see_a_real_scored_page', 'See a real scored page →'))+' · '+link('methodology/index.html#input-routes',h('how_all_methods_read_the_same_html', 'How all {count} methods read the same HTML →', count=len(names)))+'</p><p class="source-note">'+h('this_is_not_a_live_fetch_success', 'This is not a live-fetch success rate or an overall browser-capability ranking.')+'</p>','benchmark-scope')
  core_headers=[h('method', 'Method'),metric_labels['overall'] if corrected else h('overall_under_review', 'overall (under review)'),'text_edit','code_edit','formula_edit','table_edit',metric_labels['table_TEDS'] if corrected else h('table_teds_under_review', 'table_TEDS (under review)'),h('successful_all_pages', 'Successful / all pages'),h('mean_latency_2', 'Mean latency'),h('p50_latency', 'P50 latency'),h('p95_latency', 'P95 latency')]
  core_note=_wmb_timing_note(english)
  core_note+=' '+h('timing_corrections_use_preselected_round_physical_calls', 'Timing corrections use preselected round {formal_round}; physical calls and correction receipts remain available.', formal_round=formal_round) if latency_corrected else ''
  core_note+=' '+h('timing_corrections_use_preselected_round_physical_calls', 'Timing corrections use preselected round {formal_round}; physical calls and correction receipts remain available.', formal_round=formal_round) if latency_corrected else ''
  core_note+=' '+h('timing_corrections_use_preselected_round_physical_calls', 'Timing corrections use preselected round {formal_round}; physical calls and correction receipts remain available.', formal_round=formal_round) if latency_corrected else ''
  timing_key='<div class="wmb-reading-key"><span><b>'+h('quality_scores', 'Quality scores')+'</b> '+h('averaged_over_the_pages_shown_under_each', 'Averaged over the pages shown under each component')+'</span><span><b>'+h('mean_latency_p50_p95', 'Mean latency · P50 · P95')+'</b> '+h('final_successful_fetch_only_previous_failures_are', 'Final successful fetch only; previous failures are not added, and the success count is shown separately')+'</span></div>'
  coverage_note=''
  if latency_diagnostic:
   coverage_note='<p class="source-note">'+h('delivered_page_counts_and_failure_details_describe', 'Delivered-page counts and failure details describe the selected frozen pages, not live-site retrieval ability.')+'</p>'
   coverage_note=coverage_note.replace('</p>',' '+link('datasets/'+latency_diagnostic['failure_retest']['case_id']+'.html',h('inspect_the_failed_page_evidence', 'Inspect the failed-page evidence'))+'</p>')
  home+=section(h('core_results', 'Core results'),timing_key+'<p class="source-note">'+core_note+'</p>'+table(core_headers,core_rows(english),'wmb-core-results',True,cls='wmb-core-table',metrics=[],default_sort=(2,'desc'))+coverage_note+_wmb_table_population(records,names,labels,english,prefix='')+'<details><summary>'+h('formula_scoring_example_and_flow', 'Formula-scoring example and flow')+'</summary>'+_wmb_denominator_example(records,english,case_count)+'</details>'+f"""<p class="actions">{link('analysis/index.html', h('filter_page_level_results_and_responses', 'Filter page-level results and responses →'))} · {link('methodology/index.html', h('full_scoring_rules', 'Full scoring rules →'))} · {link('review/index.html', h('original_scores_and_corrections', 'Original scores and corrections →'))}</p>""")
  home+=section(h('timing_on_identical_pages', 'Timing on identical pages'),_wmb_matched_latency(records,names,labels,english,prefix=''),'matched-latency')
  if latency_diagnostic:
   home+=section(h('version_latency_comparison', 'Version latency comparison'),_wmb_latency_rerun(latency_diagnostic,english,formal_round=formal_round),'moli-latency-rerun')
  _wmb_write(out,'index.html',h('benchmark_report', 'Evaluation report'),home,english)

  dataset_rows=[]
  for c in cases.values():
   meta=c['meta']
   dataset_rows.append((_wmb_row_attrs(c),cell(link(c['id']+'.html',c['id']))+cell(esc(meta.get('language') or 'unavailable'))+cell(esc(meta.get('level')))+cell(esc('+'.join(meta.get('table') or []) or 'none'))+cell(esc('+'.join(meta.get('code') or []) or 'none'))+cell(esc('+'.join(meta.get('equation') or []) or 'none'))))
  body=hero(h('dataset', 'Dataset'),h('a_frozen_page_sample_from_the_webmainbench', 'A frozen {{case_count}}-page sample from the WebMainBench parent, selected before any method result was produced.'))
  body+=section(h('pages_4', '{{case_count}} pages'),'<p class="source-note">'+h('filter_by_language_difficulty_and_content_type', 'Filter by language, difficulty and content type. Open a track ID for the frozen HTML, human-calibrated Markdown reference and official metadata; method results are in the analysis section. Official scoring compares each conversion with the reference directly, without a prewritten per-task checklist.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,include_metric=False,include_delivery=False)+_wmb_filter_table(['Track ID',h('language', 'Language'),h('difficulty', 'Difficulty'),h('table', 'Table'),h('code', 'Code'),h('formula', 'Formula')],dataset_rows,'wmb-cases',english))
  sample=dataset.get('sample',{});sampling=sample.get('sampling',{})
  if sampling:
   distribution_rows=[]
   parent_distribution=sample.get('parent_distribution',{});sample_distribution=sample.get('sample_distribution',{})
   axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
   for axis in ('language','level','table','code','equation'):
    parent_values=parent_distribution.get(axis,{});sample_values=sample_distribution.get(axis,{})
    for category in sorted(set(parent_values)|set(sample_values)):
     parent_count=parent_values.get(category,0);sample_count=sample_values.get(category,0)
     distribution_rows.append((f' data-axis="{esc(axis)}" data-parent="{parent_count}" data-sample="{sample_count}"',cell(esc(category))+cell(str(parent_count))+cell(str(sample_count))))
   manifest_href='../sample-manifest.json' if not english else '../../sample-manifest.json'
   distribution_controls='<div class="wmb-distribution-controls"><label>'+h('distribution_axis', 'Distribution axis')+'<select id="wmb-distribution-axis" data-wmb-distribution-filter>'+''.join(f'<option value="{axis}">{label}</option>' for axis,label in axis_labels.items())+'</select></label><p id="wmb-distribution-count" class="source-note" aria-live="polite"></p></div>'
   distribution_table=table([h('category', 'Category'),h('parent_2', 'Parent'),h('sample_3', 'Sample {{case_count}}')],distribution_rows,'wmb-distribution',metrics=[])
   body+=section(h('how_these_pages_were_selected', 'How these {{case_count}} pages were selected'),'<p>'+h('language_difficulty_table_code_and_formula_are', 'Language, difficulty, table, code and formula are five independent classification axes; every page belongs to one category on each axis. After selecting an axis, the “Sample {{case_count}}” column sums to {{case_count}}. Counts across different axes must not be added together. The manifest was fixed before any browser ran; scores did not affect selection.')+'</p><p><b>'+h('fixed_seed', 'Fixed seed: ')+'</b><code>'+esc(sampling.get('seed',''))+'</code> · '+link(manifest_href,h('download_the_page_list', 'Download the {{case_count}}-page list'))+'</p>'+distribution_controls+distribution_table)
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
   body+=section(h('why_webmainbench', 'Why WebMainBench'),'<p>'+esc(research['decision']['summary_en' if english else 'summary_zh'])+'</p><p class="callout">'+esc(research['decision']['qualification_en' if english else 'qualification_zh'])+'</p>'+table([h('candidate', 'Candidate'),h('scope_3', 'Scope'),h('metric', 'Metric'),h('named_users', 'Named users'),h('decision', 'Decision'),h('evidence', 'Evidence')],selection,cls='wide-prose-table',metrics=[]))
   wcxb=next((item for item in research.get('direct_benchmarks',[]) if item.get('id')=='wcxb'),None)
   if wcxb:
    limits=wcxb.get('limitations' if english else 'limitations_zh',[])
    body+=section(h('wcxb_assessment', 'WCXB assessment'),'<p>'+esc(wcxb.get('maturity' if english else 'maturity_zh',''))+'</p><ul>'+''.join('<li>'+esc(value)+'</li>' for value in limits)+'</ul>')
   adjacent=[]
   for item in research.get('adjacent_browser_benchmarks',[]):
    adjacent.append(('',cell(esc(item['name']))+cell(esc(item.get('size' if english else 'size_zh')))+cell(esc(item.get('measures' if english else 'measures_zh')))+cell(esc(item.get('adoption' if english else 'adoption_zh')))+cell(esc(item.get('why_separate' if english else 'why_separate_zh')))+cell(evidence_links(item.get('sources',[])))))
   body+=section(h('adjacent_browser_and_web_agent_benchmarks', 'Adjacent browser and web-agent benchmarks'),table([h('benchmark_2', 'Benchmark'),h('scale', 'Size'),h('measures_2', 'Measures'),h('adoption', 'Adoption'),h('why_separate_2', 'Why separate'),h('evidence', 'Evidence')],adjacent,cls='wide-prose-table',metrics=[]))
  _wmb_write(out,'datasets/index.html',h('dataset', 'Dataset'),body,english)

  method=hero(h('methodology', 'Methodology'),h('start_with_one_frozen_page_what_the', 'Start with one frozen page: what the browser delivered, how the official scorer calculates it, and which figures cannot currently support conclusions.'))
  flow_source=scope(report_text('zh' if not english else 'en')('flowchart_lr_a_1_frozen_html_br', 'flowchart LR\n    A["1  Frozen HTML<br/>the same {{case_count}} pages"] --> B["2  {{method_count}} methods convert<br/>one final response per page"]\n    B --> C{{"3  Did the final<br/>call succeed?"}}\n    C -->|"No"| D["Result detail<br/>error type · response · cause"]\n    C -->|"Yes"| E["4  Compare with human Markdown<br/>text · code · formula · table"]\n    E --> F["5  Per-page components<br/>absent on both sides: skipped<br/>present on one side: scored zero"]\n    F --> G["6  Component means<br/>scored pages / {{case_count}} shown below score"]\n    E --> H["Exclude empty content and error pages<br/>final successful fetch: mean · P50 · P95"]'))
  flow_fallback='<div class="wmb-flow-fallback" data-wmb-flow-fallback>'+''.join(f'<span><b>{index}</b> {label}</span>' for index,label in enumerate(([h('frozen_input', 'Frozen input'),h('conversion', 'Conversion'),h('inspect_response_and_body', 'Inspect response and body'),h('per_page_scoring', 'Per-page scoring'),h('mean_over_scored_pages', 'Mean over scored pages')]),1))+'</div>'
  flow='<div class="wmb-flow">'+flow_fallback+'<pre class="mermaid" hidden>'+esc(flow_source)+'</pre></div>'
  method+=section(h('from_one_page_to_one_result', 'How one page becomes one result'),'<p class="source-note">'+h('start_with_the_full_path_then_open', 'Start with the full path, then open implementation details only when needed. Whether body text exists, whether the interface responded and why it is empty belong to the page result; quality scores only describe similarity to the human reference.')+'</p>'+flow)
  input_text=h('every_method_receives_the_same_selected_frozen', 'Every method receives the same selected frozen HTML bytes. Submission interfaces, isolation controls and physical calls follow the execution evidence; the current scope is not inferred from historical batches.')
  cue_text=h('the_dataset_html_contains_public_code_cc', 'The dataset HTML contains public <code>cc-select</code> annotation attributes. None of the {{method_count}} generic configurations is customized to use them, but they remain an inherent benchmark cue.')
  method+=section(h('input_and_runtime_boundary', 'Input and runtime boundary'),'<div class="wmb-input-routes"><article><b>'+h('kitesurf_cloudflare_api', 'Kitesurf · Cloudflare API')+'</b><code>'+h('frozen_html_api_html_field_markdown', 'Frozen HTML → API html field → Markdown')+'</code><span>'+h('the_same_html_bytes_are_submitted_directly', 'The same HTML bytes are submitted directly; the remote service does not refetch the source URL.')+'</span></article><article><b>Lightpanda · Moli · Obscura · Chromium</b><code>'+h('frozen_html_local_https_browser_markdown', 'Frozen HTML → local HTTPS → browser → Markdown')+'</code><span>'+h('the_browser_opens_a_local_url_whose', 'The browser opens a local URL whose server returns the same HTML.')+'</span></article></div><p>'+h('each_page_has_frozen_html_and_a', 'Each page has frozen HTML and a human-calibrated Markdown reference. All {{method_count}} methods convert the same HTML; the original URL records provenance and is not fetched live in this run.')+'</p><details><summary>'+h('network_isolation_local_https_server_and_batch', 'Network isolation, local HTTPS server and batch details')+'</summary><p>'+input_text+'</p><p class="callout">'+cue_text+'</p></details><p class="actions">'+link('../datasets/index.html',h('open_a_page_with_full_input_and', 'Open a page with full input and reference'))+'</p>','input-routes')
  method+=section(h('how_tables_enter_scoring', 'How tables enter scoring'),_wmb_table_example(english),'table-scoring')
  formulas=[('text_edit',h('full_markdown_against_the_human_reference', 'Full Markdown against the human reference'),h('character_edit_distance_divided_by_the_longer', 'Character edit distance divided by the longer text; extra navigation and footer content can lower the score.')),('code_edit',h('code_blocks_found_in_the_two_markdown', 'Code blocks found in the two Markdown files'),h('compare_extracted_code_characters_normalized_by_the', 'Compare extracted code characters, normalized by the longer code length.')),('formula_edit',h('formulas_found_in_the_two_markdown_files', 'Formulas found in the two Markdown files'),h('compare_extracted_formula_characters_normalized_by_the', 'Compare extracted formula characters, normalized by the longer formula length.')),('table_edit',h('tables_found_in_the_two_markdown_files', 'Tables found in the two Markdown files'),h('compare_normalized_table_characters_table_words_in', 'Compare normalized table characters; table words in a paragraph do not form a recognized table.')),('table_TEDS',h('the_table_row_cell_structure_reconstructed_from', 'The table–row–cell structure reconstructed from Markdown'),h('reconstruct_tables_rows_and_cells_from_markdown', 'Reconstruct tables, rows and cells from Markdown, then count the insertions, deletions and changes needed to turn the output structure into the reference structure. Cell text without recognizable Markdown table syntax cannot be reconstructed as a table.'))]
  method+=section(h('how_component_scores_are_produced', 'How component scores are produced'),'<p>'+h('the_scorer_first_extracts_code_formulas_and', 'The scorer first extracts code, formulas and tables from the output and reference; text_edit instead keeps the entire Markdown. For character metrics, score = 1 − edit distance / longer length: identical content scores 100%, while content on only one side scores 0%. The table-structure component is recomputed after correcting the calculation defect documented below.')+'</p>'+table([h('metric', 'Metric'),h('compared_content', 'Compared content'),h('calculation_and_reading', 'Calculation and reading')],[('',cell(k)+cell(subject)+cell(explanation)) for k,subject,explanation in formulas],cls='wide-prose-table',metrics=[])+f"<p>{h('the_pinned_official_commit_is_code_9d991bd', 'The pinned official commit is <code>9d991bd</code> with deterministic <code>use_llm=false</code>. When code, formulas or tables are absent on both sides, the official edit-distance metric returns a failed result and the component is excluded from the mean. When content exists on only one side, edit distance succeeds with a zero score and the page enters the mean.')}</p>")
  denominator_rows=[('',cell(h('absent_on_both_sides', 'Absent on both sides'))+cell(h('no_score_outside_the_component_mean', 'No score; outside the component mean'))),('',cell(h('reference_only', 'Reference only'))+cell(h('zero_score_included', 'Zero score; included'))),('',cell(h('output_only', 'Output only'))+cell(h('zero_score_included', 'Zero score; included'))),('',cell(h('present_on_both_sides', 'Present on both sides'))+cell(h('similarity_score_included', 'Similarity score; included')))]
  method+=section(h('which_pages_enter_each_component_mean', 'Which pages enter each component mean'),'<p>'+h('each_method_receives_fixed_pages_failed_final', 'Each method receives {{case_count}} fixed pages. Failed final calls are not scored. Successful calls are scored from output and reference content; calculated scores remain even when the output is later classified as an error page. “Not calculated” is different from zero. A metric execution error also excludes the page from that component mean.')+'</p>'+table([h('component_content', 'Component content'),h('official_treatment', 'Official treatment')],denominator_rows,metrics=[])+f"""<p class="actions">{link('../index.html#denominator-example', h('see_the_actual_page_moli_formula_split', 'See the actual {{case_count}}-page Moli formula split'))}</p>""",'denominator-rules')
  method+=section(h('why_the_table_structure_score_is_recomputed', 'Why the table-structure score is recomputed'),'<p>'+h('the_pinned_official_version_first_serializes_the', 'The pinned official version first serializes the table–row–cell structure into one bracketed string and then passes the string directly to APTED. APTED needs a tree whose children can be traversed; with the string it does not see the real rows and cells. In one measured page, the output has 5 structural items and the reference has 570, yet the official recorded edit distance is below 1 and the TEDS score is 99.8%. Adding the 565 missing structural items alone cannot cost less than 565 edits.')+'</p><p class="callout failure">'+h('this_report_does_not_modify_the_official', 'This report does not modify the official repository or leaderboard. An independent recomputation passes the same table to APTED as an explicit table–row–cell tree while retaining the official Markdown parsing, text handling and edit costs. Browser outputs, scored-page counts and latency are unchanged; original official scores and recomputed values are both retained. Table_edit still compares normalized table HTML strings.')+'</p><p class="actions">'+link('../attempts/obscura__949e1468-eed2-43ab-96e6-5dcb07c52d4b__r001.html',h('inspect_the_complete_5_versus_570_item', 'Inspect the complete 5-versus-570-item case'))+' · '+link('https://github.com/opendatalab/WebMainBench/blob/9d991bdc00c57b57521499494d96be85c31317ba/webmainbench/metrics/teds_metrics.py',h('official_teds_source', 'Official TEDS source'))+'</p>')
  method+=section(h('aggregation_timing_and_non_comparable_fields', 'Aggregation, timing and non-comparable fields'),'<p>'+h('each_component_aggregate_is_the_sum_of', 'Each component aggregate is the sum of successfully calculated page scores divided by its actual scored-page count; leaderboard overall is the arithmetic mean of the five aggregates. Per-page overall averages only that page’s calculated components, so it cannot replace leaderboard overall. A page-level component is marked “not calculated” for exactly two reasons: the content type is absent from both the reference and output, or the scorer failed. The result detail identifies which reason applies.')+'</p><p>'+h('successful_fetch_time_is_the_final_successful', 'Successful-fetch time is the final successful call from request start to converted response, excluding earlier failures. Accumulated time across all calls is retained separately as execution cost. Calls are serial within a browser batch, but method batches may overlap; Cloudflare remote time and local CLI time come from different execution environments. Body-text outcome, quality and latency are reported separately, not folded into one score.')+'</p>','calculation')
  cost_rows=[('',cell(labels[name])+cell(str(case_count))+''.join(cell(ms(timing['cost'][name][key])) for key in ('mean','p50','p95'))) for name in names]
  method+=section(h('successful_timing_matched_diagnosis_and_execution_cost', 'Successful timing, matched diagnosis and execution cost'),'<p>'+h('the_primary_table_uses_elapsed_ms_from', 'The primary table uses elapsed_ms from the final successful fetch with target content, alongside the success count, mean, P50 and P95. Error pages, empty content and failed final calls are excluded. A retry does not create a separate logical result or add the previous call’s time to the successful fetch.')+'</p><p>'+h('the_matched_diagnosis_uses_the_intersection_of', 'The matched diagnosis uses the intersection of successful pages across all {{method_count}} configurations. Every method uses identical pages. This post-execution subset is not a speed ranking for all {{case_count}} pages and does not remove any other results. Original call chains and accumulated execution cost remain available for audit, separately from successful-fetch latency.')+'</p><details><summary>'+h('previous_measure_accumulated_execution_cost_on_all', 'Previous measure: accumulated execution cost on all {{case_count}} pages (not successful-fetch speed)')+'</summary><p>'+h('this_retains_total_elapsed_ms_sum_all', 'This retains total_elapsed_ms: sum all physical-call durations per page, then calculate mean, P50 and P95 over the fixed {{case_count}} pages. It describes execution cost for this run.')+'</p>'+table([h('method', 'Method'),h('pages_3', 'Pages'),h('mean', 'Mean'),'P50','P95'],cost_rows,metrics=[])+'</details>','latency-policy')
  _wmb_write(out,'methodology/index.html',h('methodology', 'Methodology'),method,english)

  analysis=hero(h('analysis', 'Analysis'),h('compare_methods_on_the_same_page_then_2', 'Compare methods on the same page, then inspect category scores, and finally drill into each response, body text and cause.'))
  analysis+=section(h('method_comparison', 'Method group comparison'),_wmb_comparison(cases,bycase,names,labels,english),'comparison')
  slice_rows=[];slice_categories=set();slice_axis_labels={'language':h('language', 'Language'),'level':h('difficulty', 'Difficulty'),'table':h('table_type', 'Table type'),'code':h('code_type', 'Code type'),'equation':h('formula_type', 'Formula type')}
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
  slice_filter='<div class="wmb-slice-controls"><label>'+h('category_dimension', 'Category dimension')+'<select id="wmb-slice-axis" data-wmb-slice-filter>'+''.join('<option value="'+esc(axis)+'">'+esc(slice_axis_labels[axis])+'</option>' for axis in ('language','level','table','code','equation'))+'</select></label><label>'+h('category', 'Category')+'<select id="wmb-slice-category" data-wmb-slice-filter><option value="">'+h('all', 'All')+'</option>'+''.join('<option value="'+esc(category)+'">'+esc(category)+'</option>' for category in sorted(slice_categories))+'</select></label><label>'+h('method', 'Method')+'<select id="wmb-slice-method" data-wmb-slice-filter><option value="">'+h('all', 'All')+'</option>'+''.join('<option value="'+esc(name)+'">'+esc(labels[name])+'</option>' for name in names)+'</select></label></div><p id="wmb-slice-count" class="source-note" aria-live="polite"></p>'
  slice_table=table([h('category_axis', 'Axis'),h('category', 'Category'),h('method', 'Method')]+[metric_labels[m] for m in metric_order],slice_rows,'wmb-slices',True,cls='wmb-slice-table',metrics=[]).replace('<div class="table-wrap">','<div class="table-wrap wmb-slice-table-wrap">',1)
  analysis_tail=section(h('compare_by_page_category', 'Compare by page category'),'<p class="source-note">'+h('select_one_independent_classification_axis_then_narrow', 'Select one independent classification axis, then narrow by category or method. The small fraction below a score is scored pages / all pages in that category; “—” means no scored page, not zero. Green marks a unique highest score within the category and component. TEDS and overall use the correction labeled on the home page.')+'</p>'+slice_filter+slice_table)
  inspect_rows=[]
  for r in sorted(records,key=lambda row:(not row['ok'],row['case_id'],row['provider'])):
   case=cases[r['case_id']]
   target='../attempts/'+r['attempt_id']+'.html'
   output=contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')
   state_class='success' if r['ok'] and output.strip() and not _wmb_error_page(output) else 'failure'
   request=f'<span class="wmb-pill {state_class}">{esc(_wmb_body_state(r,output,english))}</span>'
   vals=cell(link(target,r['case_id']))+cell(link(target,labels[r['provider']]))+cell(esc(_wmb_category(case,'language')))+cell(esc(_wmb_category(case,'level')))+cell(esc(_wmb_category(case,'table')))+cell(esc(_wmb_category(case,'code')))+cell(esc(_wmb_category(case,'equation')))+cell(request)+cell(esc(_wmb_output_label(r,output,english)))
   vals+=''.join(_wmb_metric_cell(r,metric) for metric in metric_order[1:])
   vals+=cell(ms(r['elapsed_ms']),r['elapsed_ms'])+cell(link(target,h('metric_evidence', 'Metric evidence →')))
   inspect_rows.append((_wmb_row_attrs(case,r),vals))
  analysis+=section(h('page_results_and_responses', 'Page results and responses'),'<p class="wmb-metric-note">'+h('the_complete_matrix_contains_results_and_initially', 'The complete matrix contains {case_count}×{method_count} results and initially shows the {failed_request_count} conversions without body text. Each row states whether body text was produced and whether the final API/worker material is body text, an error response, an error page or no readable response. Open the track ID or method for the complete response, human reference, component scores and the currently supported cause.', case_count=case_count, method_count=method_count, failed_request_count=failed_request_count)+'</p>'+_wmb_controls(cases,labels,english,default_request='failed',failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('scroll_horizontally_for_component_scores_and_latency', 'Scroll horizontally for component scores and latency; both the track ID and method name open the detail.')+'</p>'+_wmb_filter_table(['Track ID',h('method', 'Method'),h('language', 'Language'),h('difficulty', 'Difficulty'),h('table', 'Table'),h('code', 'Code'),h('formula', 'Formula'),h('body_text_produced', 'Body text produced'),h('response_and_body', 'Response and body')]+[metric_labels[m] for m in metric_order[1:]]+[h('final_fetch_latency', 'Final fetch latency'),h('detail', 'Detail')],inspect_rows,'wmb-attempt-matrix',english))
  _wmb_write(out,'analysis/index.html',h('analysis', 'Analysis'),analysis+analysis_tail,english)

  review_body=hero(h('review_record', 'Review'),h('original_official_results_are_retained_alongside_the', 'Original official results are retained alongside the scope and effects of corrections; page-level causes are in result details.'))
  raw_prefix='../../' if english else '../'
  if corrected:
   receipt,revision_review=teds_revision
   effects=[]
   for name in names:
    impact=revision_review['impacts'][name];before=impact['official'];after=impact['corrected']
    effects.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(display_score(before['scores']['table_TEDS']))+cell(display_score(after['scores']['table_TEDS']))+cell(display_score(before['scores']['overall']))+cell(display_score(after['scores']['overall']))+cell(str(after['denominators']['table_TEDS']))))
   review_body+=section(h('teds_calculation_correction', 'TEDS calculation correction'),'<p>'+h('all_results_were_reprocessed_teds_values_changed', 'All {logical_results:,} results were reprocessed; {changed_scores} TEDS values changed. No browser was called again. Inputs, raw outputs, scored-page counts and latency are unchanged. This report does not modify the official repository or leaderboard; “corrected” values are independently recomputed by this report.', logical_results=revision_review['logical_results'], changed_scores=revision_review['changed_scores'])+'</p>'+table([h('method', 'Method'),h('original_teds', 'Original TEDS'),h('corrected_teds', 'Corrected TEDS'),h('original_overall', 'Original overall'),h('corrected_overall', 'Corrected overall'),h('pages_scored_for_teds', 'Pages scored for TEDS')],effects,'wmb-correction-effects',metrics=[])+
    '<p class="actions">'+link('../methodology/index.html',h('tree_node_defect_and_correction', 'Tree-node defect and correction'))+' · '+link(raw_prefix+'teds-correction.json',h('correction_scope_and_source_hashes', 'Correction scope and source hashes'))+' · '+link(raw_prefix+'teds-correction-review.json',h('integrity_and_arithmetic_audit', 'Integrity and arithmetic audit'))+' · '+link(raw_prefix+'original-results.jsonl',h('original_official_page_results', 'Original official page results'))+'</p>')
  if review:
   review_body+=section(h('original_result_evidence_checks', 'Original-result evidence checks'),'<p>'+h('the_original_audit_checked_the_result_scope', 'The original audit checked the {{result_count}}-result scope, file hashes, call latency, text metrics and component aggregation. It does not alone establish algorithm validity or the root cause of every failure. The TEDS correction and its follow-up checks are recorded above.')+'</p><p>'+link(raw_prefix+'result-review.json',h('download_the_original_audit', 'Download the original audit'))+'</p>')
  else:
   review_body+=section(h('original_audit_missing', 'Original audit missing'),'<p class="callout failure">'+h('the_original_audit_is_missing_publication_is', 'The original audit is missing; publication is blocked.')+'</p>')
  error_outputs=sum(1 for r in records if _wmb_error_page(contained_path(out,r['artifact_path']).read_text(encoding='utf-8',errors='replace')))
  if anomaly_review:
   anomaly_rows=[]
   for finding in anomaly_review['cases']:
    providers=(', ' if english else '、').join(labels[name] for name in finding['affected_providers'])
    anomaly_rows.append(('',cell(link('../datasets/'+finding['case_id']+'.html',finding['case_id']))+cell(esc(providers))+cell(esc(finding['observed_en' if english else 'observed_zh']))+cell(esc(finding['cause_en' if english else 'cause_zh']))))
   anomaly_scope=anomaly_review['scope']
   anomaly_href='../../webmainbench-anomaly-review.json' if english else '../webmainbench-anomaly-review.json'
   review_body+=section(h('root_cause_review_of_anomalous_results', 'Root-cause review of anomalous results'),'<p>'+h('deterministic_rules_triggered_of_results_concentrated_in', 'Deterministic rules triggered {signal_triggered_results} of {result_count} results, concentrated in {distinct_cases} pages. Review used the authoritative outputs, human references, program errors and same-page six-method differences, then non-scoring ablation controls to isolate triggers. Every triggered result maps to a cause below. Controls are not the original task, so no call, score, denominator or latency was replaced.', result_count=result_count, signal_triggered_results=anomaly_scope['signal_triggered_results'], distinct_cases=anomaly_scope['distinct_cases'])+'</p>'+table([h('page', 'Page'),h('affected_methods', 'Affected methods'),h('control_observation', 'Control observation'),h('cause_and_evidence_boundary', 'Cause and evidence boundary')],anomaly_rows,cls='wide-prose-table',metrics=[])+f"<p>{link(anomaly_href, h('download_the_hash_bound_review_record', 'Download the hash-bound review record'))}</p>")
  review_body+=section(h('review_boundary', 'Review boundary'),'<p>'+h('the_authoritative_matrix_has_conversions_without_target', 'The authoritative matrix has {failed_request_count} conversions without target body text, including {error_outputs} browser error-page output. Each now links from its detail page to direct evidence and a verified cause. The anomaly review covers the 36 deterministically triggered results; it is not a manual editorial-quality review of every remaining normal result. Undisclosed service-internal resource thresholds remain explicitly indivisible.', failed_request_count=failed_request_count, error_outputs=error_outputs)+'</p><p>'+link('../analysis/index.html#inspect?request=failed',h('inspect_results_without_body_text', 'Inspect results without body text'))+'</p>')
  if fixture_correction:
   correction_rows=[]
   for provider,effect in fixture_correction['effects'].items():
    correction_rows.append(('',cell(link('../attempts/'+provider+'__'+fixture_correction['case_id']+'__r001.html',labels[provider]))+cell(pct(effect['old_text_edit']))+cell(pct(effect['new_text_edit']))+cell(esc(effect['old_output_sha256'][:12]))+cell(esc(effect['new_output_sha256'][:12]))))
   receipt_href='../../fixture-correction.json' if english else '../fixture-correction.json'
   review_body+=section(h('frozen_page_query_navigation_correction', 'Frozen-page query-navigation correction'),'<p>'+h('one_frozen_page_appended_a_query_parameter', 'One frozen page appended a query parameter after its inline script ran. The former local test server served only the bare path, so three methods converted a 404 error page that was counted as output. The local test server now serves the same frozen page by path even with a query; the original source URL is still not fetched. The three methods were remeasured with matching binaries and the unchanged scorer; prior evidence remains in a separate archive.')+'</p>'+table([h('method', 'Method'),h('prior_page_text_edit', 'Prior page text_edit'),h('corrected_page_text_edit', 'Corrected page text_edit'),h('prior_output_digest_prefix', 'Prior output digest prefix'),h('new_output_digest_prefix', 'New output digest prefix')],correction_rows,cls='wide-prose-table',metrics=[])+f"<p>{link(receipt_href, h('download_complete_correction_effects', 'Download complete correction effects'))}</p>")
  _wmb_write(out,'review/index.html',h('review_record', 'Review'),review_body,english)

  failure_notes={}
  for finding in (anomaly_review or {}).get('cases',[]):
   observed=finding['observed_en' if english else 'observed_zh']
   cause=finding['cause_en' if english else 'cause_zh']
   for provider in finding.get('affected_providers',[]):
    failure_notes[(provider,finding['case_id'])]=(observed,cause)
  guide=report_content["guide"]["en" if english else "zh"] if report_content is not None else _wmb_guide(english=english,names=names,labels=labels,cases=cases,
                             bycase=bycase,case_count=case_count,score=score,denom=denom,
                             failures=failures,out=out,latency_diagnostic=latency_diagnostic,
                             formal_round=formal_round)
  _wmb_write(out,'guide/index.html',h('interpretation', 'Guide'),guide,english)

  provider_rows=[]
  identities={m['provider']:m['implementation_identity'] for m in manifests}
  for name in names:
   ident=identities[name]
   provider_rows.append(('',cell(labels[name])+cell(esc(ident.get('reported_version') or h('hosted_backend_undisclosed_nightly_has_no_version', 'Hosted backend undisclosed / nightly has no version command')))+cell('<code class="tiny break">'+esc(ident.get('binary_sha256') or '—')+'</code>')+cell(esc(ident.get('deployment')))))
  adoption=hero(h('methods', 'Methods'),h('compare_the_registered_configurations_versions_and_interfaces', 'Compare the {{method_count}} registered configurations; versions and interfaces are listed below.'))
  adoption+=section(h('measured_implementations', 'Measured implementations'),table([h('method', 'Method'),h('version', 'Version'),h('binary_sha_256', 'Binary SHA-256'),h('deployment_2', 'Deployment')],provider_rows,metrics=[]))
  interface_rows=[]
  language='en' if english else 'zh'
  for name in names:
   example=next((r for r in records if r['provider']==name and r['ok']),next(r for r in records if r['provider']==name))
   entry=method_spec(name,example,language=language)['entry']
   metadata=example.get('provider_metadata',{})
   input_route=_observed_route(native_browser_engine(name,example),metadata,language=language)
   output_route=report_method_catalog(language)['output_contract_labels'].get(metadata.get('output_contract'),h('not_recorded', 'Not recorded'))
   official=browser_method_registry().get(name,{}).get('source_url') or report_method(name).get('source_url','')

   interface_rows.append(('',cell(link('../providers/'+name+'.html',labels[name]))+cell(esc(entry))+cell(esc(input_route))+cell(esc(output_route))+cell(link('../attempts/'+example['attempt_id']+'.html',h('actual_input_output_and_component_scores', 'Actual input, output and component scores')))+cell(link(official,h('official_source_2', 'Official source')))))
  adoption+=section(h('run_inputs_outputs_and_examples', 'Run inputs, outputs and examples'),'<p>'+h('these_are_the_frozen_html_routes_used', 'These are the frozen-HTML routes used for this public benchmark, not live fetching of original URLs. Example links open archived calls; account fields show variable names or placeholders only.')+'</p>'+table([h('method', 'Method'),h('interface_program', 'Interface / program'),h('run_input', 'Run input'),h('run_output', 'Run output'),h('archived_call_example', 'Archived call example'),h('official_source_3', 'Official source')],interface_rows,cls='wide-prose-table',metrics=[]))
  if research:
   use_rows=[]
   for item in research.get('direct_benchmarks',[]):
    source_links='<br>'.join(link(url,urlsplit(url).netloc) for url in item.get('sources',[]))
    use_rows.append(('',cell(esc(item['name']))+cell(esc('；'.join(item.get('users',[])) or '—'))+cell(esc(item.get('maturity' if english else 'maturity_zh','—')))+cell(source_links)))
   adoption+=section(h('named_use_of_public_benchmarks', 'Named use of public benchmarks'),'<p>'+h('papers_executable_benchmark_repositories_and_framework_integrations', 'Papers, executable benchmark repositories and framework integrations document public use; no independently auditable commercial production-deployment total was found. Stars or extractor support are not presented as production adoption.')+'</p>'+table([h('dataset', 'Benchmark'),h('verified_users', 'Verified users'),h('assessment', 'Assessment'),h('primary_evidence', 'Primary evidence')],use_rows,metrics=[]))
  if adoption_data:
   counts=adoption_counts(adoption_data,sorted(PROFILES))
   (out/'adoption/counts.json').write_text(json.dumps({'snapshot':adoption_data['generated_at'],'scope':'verified source-signature hits in a bounded candidate set, not production deployment','methods':counts},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
   count_rows=[]
   details=[]
   for item in counts:
    name=item['provider'];n=item['repository_count'];known=n is not None
    label=labels.get(name,name)+(h('not_measured', ' (not measured)') if name not in names else '')
    count_rows.append(('',cell(link('#evidence-'+name,label) if known else esc(label))+cell(str(n) if known else '—',n if known else -1)+cell(str(item['file_count']) if known else '—',item['file_count'] if known else -1)))
    if known:
     evidence_rows=[('',cell(link(repo['url'],repo['repo']))+cell('<br>'.join(link(match['url'],match['path']) for match in repo['matches']))) for repo in item['repositories']]
     details.append('<details id="evidence-'+esc(name)+'"><summary>'+esc(label)+f' · {n} '+h('candidate_repositories', 'candidate repositories')+'</summary>'+table([h('repository', 'Repository'),h('matched_files', 'Matched files')],evidence_rows,metrics=[] )+'</details>')
   data_prefix='../../adoption/' if english else ''
   adoption+=section(h('open_source_adoption_evidence', 'Open-source adoption evidence'),'<p>'+h('this_independent_source_snapshot_covers_registered_methods', 'This independent source snapshot covers registered methods, not only the {{method_count}} measured configurations. Search candidates have ranking, activity and star thresholds; the counts are verified signature hits in the bounded candidate set, not global share or production users. Open a method to inspect repositories and files.')+'</p><p class="source-note">'+esc(adoption_data['generated_at'])+' · '+link(data_prefix+'evidence.json',h('full_source_evidence', 'Full source evidence'))+' · '+link(data_prefix+'counts.json',h('per_method_counts', 'Per-method counts'))+'</p>'+table([h('method', 'Method'),h('matched_repositories', 'Matched repositories'),h('matched_files', 'Matched files')],count_rows,'wmb-adoption-counts',True,metrics=[])+''.join(details))
  _wmb_write(out,'adoption/index.html',h('methods', 'Methods'),adoption,english)

  reproduce=hero(h('reproduction', 'Reproduction'),h('dataset_scorer_browsers_and_outputs_are_fixed', 'Dataset, scorer, browsers and outputs are fixed by hashes or commits.'))
  parent_sha=esc(dataset['source_dataset_sha256'])
  reproduce+=section(h('core_reproduction_commands', 'Core reproduction commands'),'<p>'+h('the_commands_use_this_repository_s_locked', 'The commands use this repository’s locked dependencies. Running all {{method_count}} methods and official scoring first requires the public parent set, the pinned scorer checkout and the measured binaries. Evaluation credentials come only from the local repository .env and are not included in this report. Angle brackets denote local paths.')+'</p><pre>uv run --frozen --extra headless-eval python scripts/run_webmainbench_benchmark.py --provider &lt;name&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --expected-dataset-sha256 '+parent_sha+' --case-ids-file &lt;sample-manifest&gt; --output &lt;method-evidence&gt;\nuv run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-score --evidence &lt;method-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --expected-dataset-sha256 '+parent_sha+' --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen python -m harness.web_fetch webmainbench-assemble --source-root &lt;six-method-batches&gt; --output &lt;assembled-evidence&gt; --dataset &lt;WebMainBench_545.jsonl&gt; --sample-manifest &lt;sample-manifest&gt; --review &lt;independent-review.json&gt;\nuv run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-teds-correction --source &lt;assembled-evidence&gt; --output &lt;corrected-evidence&gt; --scorer-repo &lt;pinned-scorer-checkout&gt;\nuv run --frozen --extra webmainbench-eval python -m harness.web_fetch webmainbench-teds-review --source &lt;assembled-evidence&gt; --corrected &lt;corrected-evidence&gt;\nmake public-benchmark-report PROFILE=&lt;profile&gt; EVIDENCE=&lt;corrected-evidence&gt;</pre>')
  reproduce+=section(h('pinned_identities', 'Pinned identities'),f"<p>Parent dataset SHA-256: <code>{esc(dataset['source_dataset_sha256'])}</code></p><p>Sample manifest SHA-256: <code>{esc(dataset['sample_manifest_sha256'])}</code> · {link('../sample-manifest.json' if not english else '../../sample-manifest.json', h('download_page_manifest', 'Download {{case_count}}-page manifest'))}</p><p>Official scorer commit: <code>9d991bdc00c57b57521499494d96be85c31317ba</code></p>")
  batch_rows=[]
  for manifest in manifests:
   batch_rows.append(('',cell(labels[manifest['provider']])+cell(esc(manifest['started_at']))+cell(esc(manifest['completed_at']))+cell(str(manifest['case_count']))+cell(str(manifest['timeout_seconds']))+cell('<code class="tiny break">'+esc(manifest['runner_sha256'])+'</code>')))
  reproduce+=section(h('observed_execution_batches', 'Observed execution batches'),table([h('method', 'Method'),h('started_utc_2', 'Started (UTC)'),h('completed_utc', 'Completed (UTC)'),h('task_2', 'Cases'),h('deadline_seconds', 'Deadline seconds'),'Runner SHA-256'],batch_rows,metrics=[]))
  _wmb_write(out,'reproduce/index.html',h('reproduction', 'Reproduction'),reproduce,english)

  release_path=out/'report-changes.json'
  if not release_path.is_file():
   release_data={'schema_version':2,'releases':[]}
   release_path.write_text(json.dumps(release_data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  else:
   release_data=json.loads(release_path.read_text(encoding='utf-8'))
  release_links=(
   ('latency-corrections.json','查看逐页纠正收据','Inspect the per-page correction receipt'),
   ('prior-latency-results.jsonl','下载原始逐页结果','Download prior per-page results'),
  ) if latency_corrected else ()
  changes=(render_webmainbench_history(release_data,english,release_links) if release_data.get('releases') else '<p>'+h('no_releases_recorded', 'No releases recorded.')+'</p>')
  _wmb_write(out,'changes/index.html',h('changelog', 'Changes'),changes,english)

  account_rows=[]
  for name in names:
   access=method_access(name,records)

   account_rows.append(('',cell(labels[name])+cell(esc(access['account']))+cell('<br>'.join('<code>'+esc(v)+'</code>' for v in access['env_vars']))+cell('<br>'.join(link(url,label) for label,url in access['links']))))
  accounts=hero(h('access_setup', 'Accounts and setup'),h('only_the_kitesurf_cloudflare_route_needs_account', 'Only the Kitesurf Cloudflare route needs account credentials; values are never published.'))
  accounts+=section(h('configuration', 'Configuration'),table([h('method', 'Method'),h('account', 'Account'),h('variables', 'Variables'),h('official_source', 'Official source')],account_rows,cls='accounts-table',metrics=[]))
  _wmb_write(out,'accounts/index.html',h('access_setup', 'Accounts and setup'),accounts,english)

  for c in cases.values():
   ref=contained_path(out,'references/'+c['id']+'.md').read_text(encoding='utf-8')
   frozen_html=contained_path(out,'sources/'+c['id']+'.html.txt').read_text(encoding='utf-8',errors='replace')
   ref_prefix='../../' if english else '../'
   body=hero(h('frozen_page', 'Frozen page')+' · '+c['id'],h('official_html_snapshot_and_human_calibrated_reference', 'Official HTML snapshot and human-calibrated reference'))+'<nav class="wrap actions">'+link('#frozen-input',h('read_archived_page', 'Read archived page'))+' · '+link(c['url'],h('open_source_website', 'Open source website ↗'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('compare_method_outputs', 'Compare method outputs'))+'</nav>'
   body+=section(h('source_and_metadata', 'Source and metadata'),f"<p>{link(c['url'], h('original_url_2', 'Original URL ↗'))}</p><pre>{esc(json.dumps(c['meta'], ensure_ascii=False, indent=2))}</pre><p>HTML SHA-256: <code>{esc(c['input_html_sha256'])}</code></p>")
   body+=section(h('human_calibrated_reference', 'Human-calibrated reference'),'<p>'+h('this_markdown_is_the_official_main_content', 'This Markdown is the official main-content reference, not the entire page; it excludes navigation and recommendations and does not guarantee coverage of text in images. The scorer extracts text, code, formulas and tables from each conversion and reference, then calculates five components.')+'</p><p class="actions">'+link(ref_prefix+'references/'+c['id']+'.md',h('download_reference_2', 'Download reference'))+' · '+link('../methodology/index.html',h('official_scoring_method', 'Official scoring method'))+'</p><pre class="wmb-source-pre">'+esc(ref)+'</pre>')
   body+=section(h('frozen_page_reading_view', 'Frozen-page reading view'),'<p class="actions">'+link(c['url'],h('source_website_live_may_have_changed', 'Source website ↗ (live, may have changed)'))+' · '+link('../analysis/index.html#inspect?query='+c['id'],h('inspect_method_results_for_this_page', 'Inspect method results for this page'))+'</p><p class="source-note">'+h('read_the_html_archive_used_in_this', 'Read the HTML archive used in this run below. This safe preview disables scripts, external resources and navigation; images appear as placeholders. It is a static reading view, not a measured browser screenshot or a replay of script execution. The complete input remains in the source view.')+'</p>'+_wmb_html_preview(frozen_html,english)+'<details><summary>'+h('view_complete_html_source', 'View complete HTML source')+'</summary><pre class="wmb-source-pre">'+esc(frozen_html)+'</pre><p>'+link(ref_prefix+'sources/'+c['id']+'.html.txt',h('save_raw_file', 'Save raw file'))+'</p></details>','frozen-input')
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
    values+=cell(ms(r['elapsed_ms']),r['elapsed_ms'])+cell(link('../datasets/'+r['case_id']+'.html',h('page_and_reference', 'Page and reference')))
    rows.append((_wmb_row_attrs(c,r),values))
   body=hero(labels[name],h('final_results_latency_is_the_final_fetch', '{case_count} final results; latency is the final fetch duration, excluding earlier failures.', case_count=case_count))
   body+=section(h('per_page_results', 'Per-page results'),'<p class="source-note">'+h('open_a_track_id_for_scores_the', 'Open a track ID for scores, the complete response and cause; the last column opens the frozen page and human reference. “Body text produced” only states that readable Markdown exists, not that content passed; error pages are explicit in the response column.')+'</p>'+_wmb_controls(cases,labels,english,include_method=False,failure_types=failure_types)+'<p class="wmb-scroll-note">'+h('scroll_horizontally_and_vertically_within_the_table', 'Scroll horizontally and vertically within the table.')+'</p>'+_wmb_filter_table(['Track ID',h('language', 'Language'),h('difficulty', 'Difficulty'),h('table', 'Table'),h('code', 'Code'),h('formula', 'Formula'),h('body_text_produced', 'Body text produced'),h('response_and_body', 'Response and body') ]+[metric_labels[m] for m in metric_order[1:]]+[h('final_fetch_latency', 'Final fetch latency'),h('page_and_reference', 'Page and reference')],rows,'wmb-provider-'+name,english))
   _wmb_write(out,'providers/'+name+'.html',labels[name],body,english)

  for r in records:
   c=cases[r['case_id']];case_id=r['case_id'];attempt_id=r['attempt_id']
   output_path=contained_path(out,r['artifact_path'])
   output_text=output_path.read_text(encoding='utf-8',errors='replace') if output_path.is_file() else ''
   reference_text=contained_path(out,'references/'+case_id+'.md').read_text(encoding='utf-8')
   attempt_body=_wmb_result_detail(r,c,output_text,reference_text,labels,metric_labels,failure_notes,corrected,english)
   _wmb_write(out,'attempts/'+attempt_id+'.html',labels[r['provider']]+' · '+case_id,attempt_body,english)
 write_language_contract(out,include_english=include_english)


def webmainbench_full_machine_issue(root: Path, row: dict) -> bool:
    """Identify request failures and successful calls without usable page text."""
    if not row["ok"] or not row["output_chars"]:
        return True
    return bool(ERROR_PAGE.search(contained_path(root, row["artifact_path"]).read_text(encoding="utf-8")))


WEBMAINBENCH_FULL_CAUSE_OWNERS = {
    "Moli 解析器崩溃": "Moli 引擎",
    "Kitesurf 服务错误": "Kitesurf 服务",
    "Kitesurf 请求超时": "Kitesurf 服务或传输，内部阶段未定位",
    "Kitesurf 空正文": "Kitesurf 接口，内部原因未知",
    "Obscura 空响应": "Obscura",
    "Obscura 导航循环": "Obscura",
    "冻结页跳转受阻": "共享输入与隔离环境",
}


def generate_webmainbench_full(out: Path, dataset: dict, records: list[dict],
                               summaries: dict, profile: dict) -> None:
    """Render the complete frozen WebMainBench cohort inside the shared report module."""
    case_count = profile["case_count"]
    reference_pages = next(iter(summaries.values()))["official_aggregate"]["denominators"]
    content = json.loads((out / "report-content.json").read_text(encoding="utf-8"))
    failure_analysis = json.loads((out / "failure-analysis.json").read_text(encoding="utf-8"))
    reviewed_failures = {(row["provider"], row["case_id"]): row for row in failure_analysis["reviewed_failures"]}
    machine_issue_count = sum(webmainbench_full_machine_issue(out, row) for row in records)
    adoption = json.loads((out / "provider-adoption.json").read_text(encoding="utf-8"))
    cloudflare_matches = {(entry["repo"], match["path"]) for entry in adoption["included"]
                          for match in entry["matches"] if match["signature"] == "cloudflare-markdown"}
    cloudflare_repositories = len({repo for repo, _ in cloudflare_matches})
    def prose(key):
        return esc(content[key])
    correction_path = out / 'case-correction.json'
    correction = json.loads(correction_path.read_text(encoding='utf-8')) if correction_path.exists() else None
    scoring_correction_path = out / 'scoring-correction.json'
    scoring_correction = json.loads(scoring_correction_path.read_text(encoding='utf-8')) if scoring_correction_path.exists() else None
    def illustration(name, caption):
        if not (out / 'assets' / name).is_file():
            return ''
        return ('<figure data-report-illustration="' + name + '"><div style="overflow-x:auto"><a href="assets/' + name +
                '"><img style="width:100%;min-width:800px;height:auto" loading="lazy" src="assets/' + name +
                '" alt="' + esc(caption) + '"></a></div><figcaption>' + esc(caption) + '（窄屏可横向滚动，点击查看原图。）</figcaption></figure>')
    providers = profile["providers"]
    method_error_profiles = content.get("method_error_profiles")
    if (not isinstance(method_error_profiles, dict)
            or set(method_error_profiles) != set(providers)
            or any(set(item) != {"runtime", "quality", "reading"}
                   for item in method_error_profiles.values())):
        raise ValueError("Report content lacks a complete registered-method error profile")
    moli_fix_profiles = content.get("moli_fix_profiles")
    expected_moli_fix_images = [f"moli-fix-{index:02d}-{suffix}.png" for index, suffix in (
        (1, "visibility-disclosure"), (2, "fallback-fragments"),
        (3, "live-form-state"), (4, "media-candidates"),
        (5, "semantic-boundaries"), (6, "parser-resume-ownership"))]
    if (not isinstance(moli_fix_profiles, list)
            or [item.get("image") for item in moli_fix_profiles] != expected_moli_fix_images
            or any(set(item) != {"image", "title", "symptom", "root", "fix", "verification"}
                   for item in moli_fix_profiles)):
        raise ValueError("Report content lacks the six bound Moli fix profiles")
    cases = {case["id"]: case for case in dataset["cases"]}
    grouped = defaultdict(dict)
    for row in records:
        grouped[row["case_id"]][row["provider"]] = row
    variant_labels = profile.get("variant_labels", {})
    labels = {p: (variant_labels[p] if p in variant_labels else profile["versions"][p] if engine_definition(p).playwright_launched else p.capitalize() +
                  (" " + profile["versions"][p] if not engine_definition(p).hosted else "")) for p in providers}
    content_fixed_commit = json.loads((out / 'moli-content-fixed-report.json').read_text(encoding='utf-8'))['source_commit']
    content_fixed_version = profile['versions']['moli-content-fixed']
    measures = [("overall", "综合分"), ("text_edit", "全文"), ("code_edit", "代码"),
                ("formula_edit", "公式"), ("table_edit", "表格内容"), ("table_TEDS", "表格结构")]

    def percent(value):
        return "—" if value is None else f"{value * 100:.1f}%"

    def score(row, metric):
        return row.get("metrics", {}).get(metric, {}).get("score")

    def classify(row):
        if not row["ok"]:
            return t('request_failure', 'Request failure')
        if not row["output_chars"]:
            return "空输出"
        if ERROR_PAGE.search(contained_path(out, row["artifact_path"]).read_text(encoding="utf-8")):
            return "错误页"
        if score(row, "text_edit") is not None and score(row, "text_edit") < .1:
            return "低全文相似度"
        return "未触发诊断"

    categories = {row["attempt_id"]: classify(row) for row in records}
    counts = Counter(categories.values())
    target_body = {
        row["attempt_id"]: categories[row["attempt_id"]] not in {t('request_failure', 'Request failure'), "空输出", "错误页"}
        for row in records
    }

    def target_body_count(provider):
        return sum(target_body[row["attempt_id"]] for row in records if row["provider"] == provider)
    css = asset_text("webmainbench-full.css")
    js = asset_text("webmainbench-full.js")
    def js_string(value):
        return (json.dumps(value, ensure_ascii=False)
                .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    js = js.replace("__PROVIDER_LABELS_JSON__", js_string(labels))
    (out / "assets").mkdir(exist_ok=True)
    (out / "assets/style.css").write_text(css, encoding="utf-8")
    (out / "assets/report.js").write_text(js, encoding="utf-8")
    asset_version = hashlib.sha256((css + "\0" + js).encode()).hexdigest()[:12]

    def anchor(path, label):
        return '<a href="' + esc(safe_href(path)) + '">' + esc(label) + '</a>'

    def table(headers, rows, sort_types=None, table_id=None):
        sort_types = sort_types or [None] * len(headers)
        if len(sort_types) != len(headers):
            raise ValueError("Table sort contract differs from its columns")
        header_cells = []
        for label, sort_type in zip(headers, sort_types, strict=True):
            if sort_type:
                header_cells.append('<th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="' +
                                    esc(sort_type) + '">' + esc(label) + '<span aria-hidden="true"> ↕</span></button></th>')
            else:
                header_cells.append('<th>' + esc(label) + '</th>')
        identity = ' id="' + esc(table_id) + '"' if table_id else ''
        return '<div class="scroll"><table' + identity + '><thead><tr>' + ''.join(header_cells) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'

    navigation = [("总览", "index.html")]
    navigation += [("数据集", "dataset/index.html"),
                  (t('analysis', 'Analysis'), "analysis/index.html"), ("解读", "guide/index.html"),
                  (t('method', 'Method'), "methodology/index.html"), ("复现", "reproduce/index.html"),
                  ("更新记录", "history/index.html")]

    def page(route, title, body):
        prefix = "../" * route.count("/")
        links = ''.join(anchor(path, label) for label, path in navigation)
        markup = ((f'''<!doctype html><html lang="zh"><head><meta name="browser-eval-report" content="1"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base href="{prefix or './'}"><title>{esc(title)} · WebMainBench 545</title><link rel="stylesheet" href="assets/style.css?v={asset_version}"><script defer src="assets/report.js?v={asset_version}"></script></head><body><header><div class="wrap"><strong>{anchor('index.html', 'WebMainBench · 545')}</strong><nav>{links}</nav>{anchor('en/index.html', 'English')}<button onclick="theme()">''' + t('toggle_theme', 'Toggle theme') + f"""</button></div></header><main class="wrap">{body}</main><footer class="wrap">WebMainBench 545 例 · {str(len(providers))} 种方法 · {anchor('https://github.com/lexmount/WebMainBench/tree/lex-main', 'lex-main 数据与评分器')}</footer></body></html>"""))
        target = out / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markup, encoding="utf-8")

    def metric_table():
        rows = []
        for provider in providers:
            summary = summaries[provider]
            scores = summary["official_aggregate"]["scores"]
            denominators = summary["official_aggregate"]["denominators"]
            method_detail = ('<small>实测提交 ' + esc(content_fixed_commit[:9]) + '；Moli ' +
                             esc(content_fixed_version) + '</small>'
                             if provider == 'moli-content-fixed' else '')
            cells = ['<td data-sort="' + esc(labels[provider]) + '">' + anchor("analysis/index.html?provider=" + provider, labels[provider]) + method_detail + "</td>"]
            for metric, _ in measures:
                value = scores[metric]
                all_values = [summaries[other]["official_aggregate"]["scores"][metric] for other in providers]
                winner = "winner comparison-winner" if value == max(all_values) and all_values.count(value) == 1 else ""
                detail = "<small>" + str(denominators[metric]) + " 页计分</small>" if metric != "overall" else ""
                digits = 2 if metric == 'overall' else 3 if metric == 'code_edit' else 1
                cells.append('<td class="number" data-metric="' + metric + '" data-metric-direction="max" data-sort="' + str(value) +
                             '"><span class="' + winner + '">' + f'{value * 100:.{digits}f}%' + "</span>" + detail + "</td>")
            cells.append('<td class="number" data-target-body-provider="' + provider + '" data-sort="' + str(target_body_count(provider)) + '">' +
                         str(target_body_count(provider)) + "/" + str(case_count) + "</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return table([t('method', 'Method')] + [label for _, label in measures] + ["成功生成正文"], rows,
                     ["text"] + ["number"] * len(measures) + ["number"], "core-results")

    def latency_table():
        rows = []
        by_provider = {provider: [row for row in records if row["provider"] == provider] for provider in providers}
        successful_by_provider = {
            provider: [row["elapsed_ms"] for row in by_provider[provider] if target_body[row["attempt_id"]]]
            for provider in providers
        }
        values_by_provider = {provider: ([statistics.mean(values), percentile(values, .5), percentile(values, .95)] if values else [None] * 3) +
                              [sum(row["total_elapsed_ms"] for row in by_provider[provider])]
                              for provider, values in successful_by_provider.items()}
        for provider in providers:
            numbers = []
            for index, value in enumerate(values_by_provider[provider]):
                eligible = [values_by_provider[other][index] for other in providers if other not in variant_labels and values_by_provider[other][index] is not None]
                unique = provider not in variant_labels and value is not None and value == min(eligible) and eligible.count(value) == 1
                marker = '<strong class="comparison-winner winner">' if unique else '<span>'
                end = '</strong>' if unique else '</span>'
                numbers.append('<td class="number" data-metric="success-latency-' + str(index) + '" data-metric-direction="' + ('neutral' if provider in variant_labels else 'min') + '"' +
                               (' data-sort="' + str(value) + '"' if value is not None else '') + '>' + marker +
                               (f"{value:,.0f} ms" if value is not None else "—") + end + '</td>')
            rows.append("<tr><td>" + esc(labels[provider]) + "</td><td data-sort=\"" + str(len(successful_by_provider[provider])) + "\">" + str(len(successful_by_provider[provider])) + "/" + str(case_count) + "</td>" + ''.join(numbers) + "</tr>")
        return table([t('method', 'Method'), "成功生成正文", t('mean', 'Mean'), "P50", "P95", "含重试总耗时"], rows,
                     ["text", "number", "number", "number", "number", "number"])

    def all_call_latency_table():
        distributions = {provider: [row["elapsed_ms"] for row in records if row["provider"] == provider] for provider in providers}
        values = {provider: [statistics.mean(items), percentile(items, .5), percentile(items, .95)]
                  for provider, items in distributions.items()}
        rows = []
        for provider in providers:
            cells = []
            for index, number in enumerate(values[provider]):
                column = [values[other][index] for other in providers if other not in variant_labels]
                unique = provider not in variant_labels and number == min(column) and column.count(number) == 1
                marker = '<strong class="comparison-winner winner">' if unique else '<span>'
                cells.append('<td class="number" data-metric="all-call-latency-' + str(index) + '" data-metric-direction="' + ('neutral' if provider in variant_labels else 'min') + '" data-sort="' +
                             str(number) + '">' + marker + f'{number:,.0f} ms' + ('</strong>' if unique else '</span>') + '</td>')
            rows.append('<tr><td>' + esc(labels[provider]) + '</td><td data-sort="' + str(case_count) + '">' + str(case_count) + '/' + str(case_count) + '</td>' + ''.join(cells) + '</tr>')
        return table([t('method', 'Method'), '全部逻辑调用', t('mean', 'Mean'), 'P50', 'P95'], rows,
                     ['text', 'number', 'number', 'number', 'number'])

    quality_issue_specs = (
        ('delivery', f'未生成正文 / {case_count}', None, None),
        ('text', f'全文 <10% / {reference_pages["text_edit"]}', 'text_edit', .1),
        ('code', f'代码 <50% / {reference_pages["code_edit"]}', 'code_edit', .5),
        ('formula', f'公式 <50% / {reference_pages["formula_edit"]}', 'formula_edit', .5),
        ('table', f'表格内容 <50% / {reference_pages["table_edit"]}', 'table_edit', .5),
        ('teds', f'表格结构 <50% / {reference_pages["table_TEDS"]}', 'table_TEDS', .5),
    )

    def quality_issue_flags(row):
        flags = set()
        if not target_body[row['attempt_id']]:
            flags.add('delivery')
        for key, _, metric, threshold in quality_issue_specs[1:]:
            entry = row.get('metrics', {}).get(metric, {})
            if (entry.get('success') is True
                    and isinstance(entry.get('score'), (int, float))
                    and entry['score'] < threshold):
                flags.add(key)
        return flags

    quality_flags = {row['attempt_id']: quality_issue_flags(row) for row in records}

    def quality_issue_matrix():
        counts_by_provider = {
            provider: {
                key: sum(key in quality_flags[row['attempt_id']]
                         for row in records if row['provider'] == provider)
                for key, _, _, _ in quality_issue_specs
            }
            for provider in providers
        }
        rows = []
        for provider in providers:
            cells = ['<td data-sort="' + esc(labels[provider]) + '">' +
                     anchor('analysis/index.html?provider=' + provider, labels[provider]) + '</td>']
            for key, _, _, _ in quality_issue_specs:
                value = counts_by_provider[provider][key]
                column = [counts_by_provider[other][key] for other in providers]
                winner = 'winner comparison-winner' if value == min(column) and column.count(value) == 1 else ''
                cells.append('<td class="number" data-metric="issue-' + key +
                             '" data-metric-direction="min" data-sort="' + str(value) + '">' +
                             '<a class="' + winner + '" href="analysis/index.html?provider=' + esc(provider) +
                             '&metric=' + esc(key) + '&kind=all">' + str(value) + '</a></td>')
            rows.append('<tr data-quality-provider="' + provider + '">' + ''.join(cells) + '</tr>')
        return table([t('method', 'Method'), *[label for _, label, _, _ in quality_issue_specs]], rows,
                     ['text'] + ['number'] * len(quality_issue_specs), 'quality-issue-matrix')

    home = (f"""<section class="hero"><h1>{prose('home_title')}</h1><p>{prose('home_lead')}</p></section><section><h2>核心结果</h2>{metric_table()}<p class="note">{prose('score_note')}</p></section><section><h2>各方法的质量风险定位</h2>{quality_issue_matrix()}<p class="note">同一结果可同时进入多列。未生成正文按 {case_count} 页统计；全文按 {reference_pages['text_edit']} 页统计；代码、公式、表格内容与表格结构分别只统计 {reference_pages['code_edit']}、{reference_pages['formula_edit']}、{reference_pages['table_edit']}、{reference_pages['table_TEDS']} 页固定参考总体。绿色仅表示该列唯一最少，不代表综合质量排名。</p></section><section><h2>全部页面的调用耗时</h2>{all_call_latency_table()}<p class="note">{prose('all_latency_note')}</p></section><section><h2>目标正文页面的调用耗时与重试成本</h2>{latency_table()}<p class="note">{prose('latency_note')}</p></section><section><h2>问题分布</h2>{table(['自动诊断', '结果数'], ['<tr><td>' + anchor('analysis/index.html?kind=' + kind, kind) + '</td><td data-sort="' + str(count) + '">' + str(count) + '</td></tr>' for kind, count in sorted(counts.items())], ['text', 'number'])}<p class="note">{prose('diagnostic_note')}</p></section>""")
    page("index.html", "完整结果", home)
    dataset_rows = []
    for case in dataset["cases"]:
        case_id = case["id"]
        meta = case["meta"]
        language = meta.get("language") or "—"
        level = meta.get("level") or "—"
        dataset_rows.append('<tr data-dataset data-language="' + esc(language) + '" data-level="' + esc(level) + '" data-search="' +
                            esc((case_id + ' ' + case['url'] + ' ' + language + ' ' + level).lower()) + '"><td>' + anchor("cases/index.html?id=" + case_id, case_id) + "</td><td>" +
                            esc(language) + "</td><td>" + esc(level) +
                            '</td><td class="source">' + anchor(case["url"], case["url"]) + "</td><td>" +
                            anchor("viewer/index.html?path=sources/" + case_id + ".html.txt", "冻结网页") + " · " +
                            anchor("viewer/index.html?path=references/" + case_id + ".md", "人工答案") + "</td></tr>")
    dataset_languages = sorted({(case['meta'].get('language') or '—') for case in dataset['cases']})
    dataset_levels = sorted({(case['meta'].get('level') or '—') for case in dataset['cases']})
    dataset_filters = ('<div class="filters"><label>搜索任务或网址<input id="dataset-q" type="search"></label>'
                       '<label>语言<select id="dataset-language"><option value="">全部语言</option>' +
                       ''.join('<option value="' + esc(value) + '">' + esc(value) + '</option>' for value in dataset_languages) +
                       '</select></label><label>难度<select id="dataset-level"><option value="">全部难度</option>' +
                       ''.join('<option value="' + esc(value) + '">' + esc(value) + '</option>' for value in dataset_levels) +
                       '</select></label></div><p>当前显示 <strong id="dataset-count"></strong> 题。'
                       '<span id="dataset-empty" hidden>当前条件无任务。</span></p>')
    page("dataset/index.html", "数据集", f"""<section class="hero"><h1>冻结网页与人工答案</h1><p>{prose('dataset_lead')}</p></section>{dataset_filters}{table([t('task_id', 'Task ID'), '语言', '难度', t('sources_2', 'Source'), '证据'], dataset_rows, ['text', 'text', 'text', 'text', None], 'dataset-inventory')}""")
    options = ["issue", "all", t('request_failure', 'Request failure'), "空输出", "错误页", "低全文相似度", "未触发诊断"]
    cause_owners = WEBMAINBENCH_FULL_CAUSE_OWNERS
    reviewed_cause_labels = {row["category_label"] for row in failure_analysis["reviewed_failures"]}
    cause_order = tuple(cause for cause in cause_owners if cause in reviewed_cause_labels)
    filters = (('<div class="filters"><label>搜索任务或网址<input id="q" type="search"></label><label>' + t('method', 'Method') + f"""<select id="provider"><option value="">全部方法</option>{''.join(('<option value="' + provider + '">' + esc(labels[provider]) + '</option>' for provider in providers))}</select></label><label>诊断<select id="kind">{''.join(('<option value="' + esc(kind) + '">' + esc('全部问题' if kind == 'issue' else '全部结果' if kind == 'all' else kind) + '</option>' for kind in options))}</select></label><label>质量风险<select id="metric"><option value="">全部类别</option>{''.join(('<option value="' + key + '">' + esc(label) + '</option>' for key, label, _, _ in quality_issue_specs))}</select></label><label>已复核原因<select id="cause"><option value="">全部原因</option>{''.join(('<option value="' + esc(kind) + '">' + esc(kind) + '</option>' for kind in cause_order))}</select></label></div>"""))
    result_rows = []
    for row in records:
        case_id, provider = row["case_id"], row["provider"]
        kind = categories[row["attempt_id"]]
        review = reviewed_failures.get((provider, case_id))
        result_rows.append('<tr data-result data-provider="' + provider + '" data-case-id="' + case_id + '" data-kind="' + esc(kind) +
                           '" data-cause="' + esc(review["category_label"] if review else "") +
                           '" data-quality="' + esc(' '.join(sorted(quality_flags[row['attempt_id']]))) +
                           '" data-search="' + esc((case_id + " " + row["original_url"]).lower()) + '"><td>' +
                           anchor("cases/index.html?id=" + case_id, case_id) + "</td><td>" +
                           anchor("results/index.html?provider=" + provider + "&id=" + case_id, labels[provider]) + "</td><td>" +
                           ('<button type="button" data-diagnose onclick="openDiagnosis(this)">' + esc(kind) + ' · 诊断</button>' if kind != '未触发诊断' else esc(kind)) + "</td>" +
                           '<td data-sort="' + (str(score(row, "text_edit")) if score(row, "text_edit") is not None else '') + '">' +
                           percent(score(row, "text_edit")) + '</td><td data-sort="' + str(row["elapsed_ms"]) + '">' + f'{row["elapsed_ms"]:,.0f} ms' +
                           "</td><td>" + anchor("cases/index.html?id=" + case_id, "同页") + "</td></tr>")
    page("analysis/index.html", t('analysis', 'Analysis'), (f"""<section class="hero"><h1>逐项结果与问题定位</h1><p>{prose('analysis_lead')}</p></section>{filters}<p>当前显示 <strong id="count"></strong> 条。<span id="empty" hidden>当前条件无结果。</span></p>{table([t('task', 'Task'), t('method', 'Method'), '诊断', '全文分', '最终调用', '同页'], result_rows, ['text', 'text', 'text', 'number', 'number', None], 'result-inventory')}<dialog id="diagnosis-dialog" aria-labelledby="diagnosis-title"><button type="button" id="diagnosis-close">关闭诊断</button><h2 id="diagnosis-title"></h2><p id="diagnosis-stage"></p><p id="diagnosis-error"></p><p id="diagnosis-cause"></p><p id="diagnosis-treatment"></p><details><summary>原始运行日志</summary><pre id="diagnosis-log"></pre></details><p class="actions"><a id="diagnosis-result">完整结果与调用记录</a><a id="diagnosis-output">""" + t('raw_output', 'Raw output') + '</a><a id="diagnosis-case">同页各方法对照</a></p></dialog>'))
    page("cases/index.html", "同页比较",
         (f"""<section class="hero"><h1>人工答案与各方法的完整输出</h1><p id="case-id"></p><p class="source" id="case-url"></p></section><p class="actions"><a id="case-source">冻结网页</a><a id="case-reference">人工答案</a>{anchor('dataset/index.html', '返回数据集')}</p><div class="scroll"><table id="case-result-table"><thead><tr><th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="text">""" + t('method', 'Method') + f"""<span aria-hidden="true"> ↕</span></button></th><th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="text">诊断<span aria-hidden="true"> ↕</span></button></th>{''.join(('<th aria-sort="none"><button type="button" class="sort-button" data-table-sort onclick="sortReportTable(this)" data-sort-type="number">' + esc(label) + '<span aria-hidden="true"> ↕</span></button></th>' for _, label in measures))}</tr></thead><tbody id="case-results"></tbody></table></div><section><h2>同页内容对照</h2><div class="compare-grid" id="case-comparison"></div></section>"""))
    page("results/index.html", "逐项结果",
         ('<div data-report-detail="pending"><section class="hero"><h1 id="result-title">正在载入结果…</h1><p class="source" id="result-source"></p></section><section class="panel" data-result-summary><h2>结果摘要</h2><p id="result-summary"></p></section><p id="result-correction" hidden>本条为保留原网址后的认证补测结果，已替换旧环境下同一方法的结果。<a href="guide/index.html#fixture-correction">原因、各方法对照与旧调用记录</a></p><section class="panel" data-failure-diagnosis id="result-failure" hidden><h2>错误与诊断</h2><p id="result-stage"></p><p id="result-error"></p><p id="result-cause"></p><p id="result-treatment"></p><details id="result-logs"><summary>原始运行日志</summary><pre data-kind="error" id="result-log"></pre></details></section><section><h2>五项质量指标</h2><div class="scroll"><table><thead><tr><th>' + t('metric', 'Metric') + '</th><th>分数</th><th>状态</th></tr></thead><tbody id="result-metrics"></tbody></table></div></section><details id="result-physical" hidden><summary>每次物理调用</summary><ol id="result-physical-list"></ol></details><p class="actions"><a id="result-case">返回同页比较</a><a id="result-output">完整输出</a><a id="result-reference">人工答案</a><a id="result-source-link">冻结网页</a></p></div>'))
    shards = defaultdict(lambda: {"results": {}, "artifacts": {}, "blobs": {}, "physical": {}})
    result_index = {}
    artifact_index = {}
    def shard_name(provider, case_id):
        bucket = int(hashlib.sha256(case_id.encode()).hexdigest()[:2], 16) % 8
        return f"shards/{provider}-{bucket}.json"
    def bound_artifact(path, expected, shard_path):
        source = contained_path(out, path)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Public artifact changed: " + path)
        shards[shard_path]["artifacts"][path] = expected
        shards[shard_path]["blobs"][expected] = raw.decode("utf-8")
        artifact_index[path] = shard_path
    for row in records:
        key = row["provider"] + "|" + row["case_id"]
        target = shard_name(row["provider"], row["case_id"])
        shards[target]["results"][key] = {**row, "diagnosis": categories[row["attempt_id"]],
                                          "review": reviewed_failures.get((row["provider"], row["case_id"]))}
        result_index[key] = target
        bound_artifact(row["artifact_path"], row["output_sha256"], target)
    physical_path = out / "physical-attempts.jsonl"
    physical = load_jsonl(physical_path, skip_blank=True) if physical_path.is_file() else []
    for attempt in physical:
        case_id = attempt["track_id"]
        target = shard_name(attempt["provider"], case_id)
        shards[target]["physical"].setdefault(case_id, []).append(attempt)
        bound_artifact(attempt["physical_artifact_path"], attempt["output_sha256"], target)
    for historical_name, path_key in [('superseded-case-results.jsonl', 'artifact_path'),
                                      ('superseded-case-calls.jsonl', 'physical_artifact_path')]:
        historical_path = out / historical_name
        if historical_path.is_file():
            for attempt in iter(load_jsonl(historical_path)):
                case_id = attempt.get('case_id', attempt.get('track_id'))
                target = shard_name(attempt['provider'], case_id)
                bound_artifact(attempt[path_key], attempt['output_sha256'], target)
    for case in dataset["cases"]:
        case_id = case["id"]
        target = shard_name("source", case_id)
        bound_artifact(f"sources/{case_id}.html.txt", case["input_html_sha256"], target)
        bound_artifact(f"references/{case_id}.md", case["reference_sha256"], target)
    for relative, data in shards.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "result-shard-index.json").write_text(json.dumps(result_index, separators=(",", ":")), encoding="utf-8")
    (out / "artifact-shard-index.json").write_text(json.dumps(artifact_index, separators=(",", ":")), encoding="utf-8")
    shutil.rmtree(out / "artifacts")
    shutil.rmtree(out / "sources")
    shutil.rmtree(out / "references")
    interface_rows = ['<tr><td>' + esc(labels[provider]) + '</td><td><code>' + esc(content['interfaces'][provider]) + '</code></td></tr>'
                      for provider in providers]
    retry_count = sum(row["physical_attempt_count"] - 1 for row in records)
    recovered_count = sum(row["ok"] and row["physical_attempt_count"] > 1 for row in records)
    final_request_failures = sum(not row["ok"] for row in records)
    failed_call_counts = Counter(row['physical_attempt_count'] for row in records if not row['ok'])
    failure_calls_note = ('（' + '，'.join(f'{count} 条调用 {calls} 次'
                          for calls, count in sorted(failed_call_counts.items())) + '）') if failed_call_counts else ''
    page("methodology/index.html", t('methodology', 'Methodology'),
         (f"""<section class="hero"><h1>测量与评分方法</h1><p>{prose('method_lead')}</p></section><section class="panel"><h2>""" + t('input', 'Input') + f"""</h2><p>{prose('method_input')}</p><p>数据 SHA-256：<code>{esc(profile['source_dataset_sha256'])}</code>。原始评分器提交：<code>{esc(profile['official_scorer_commit'])}</code>{('；lex-main 修正评分器提交：<code>' + esc(scoring_correction['scorer_commit']) + '</code>' if scoring_correction else '')}。</p></section><section class="panel"><h2>失败重试</h2><p>本轮共 {str(len(records) + retry_count)} 次调用，其中 {str(retry_count)} 次为失败后的重试。{str(recovered_count)} 条请求在重试后成功，{str(final_request_failures)} 条请求最终失败{failure_calls_note}。返回成功但正文为空或为错误页的结果不触发请求失败重试。</p></section><section class="panel"><h2>评分</h2><p>{prose('method_scoring')}</p><p>{prose('method_correction')}</p>{(anchor('teds-correction.json', '历史 TEDS 修正收据') + ' · ' + anchor('teds-correction-review.json', '历史修正复核') if (out / 'teds-correction.json').is_file() else anchor('scoring-correction.json', 'lex-main 统一评分修正收据'))}</section><section><h2>调用接口</h2>{table([t('method', 'Method'), '本轮调用方式'], interface_rows)}</section><p class="note">{prose('kitesurf_version_note')}</p><section class="panel"><h2>公开采用证据</h2><p>{prose('adoption_lead')} 截至 {esc(adoption['cutoff'][:10])}，Cloudflare Markdown 接口在筛选范围内见于 {str(cloudflare_repositories)} 个仓库、{str(len(cloudflare_matches))} 个文件。</p><p>{prose('adoption_limit')}</p>{anchor('provider-adoption.json', '查看检索样本与纳入规则')}</section>"""))
    example_id = dataset["cases"][0]["id"]
    example_row = grouped[example_id][providers[0]]
    example_links = " · ".join([
        anchor("viewer/index.html?path=sources/" + example_id + ".html.txt", "冻结 HTML"),
        anchor("viewer/index.html?path=references/" + example_id + ".md", "人工答案"),
        anchor("viewer/index.html?path=" + example_row["artifact_path"], (f'{labels[providers[0]]} ' + t('output', 'Output'))),
        anchor("cases/index.html?id=" + example_id, "各方法同页比较"),
    ])
    overall_scores = {provider: summaries[provider]["official_aggregate"]["scores"]["overall"]
                      for provider in providers}
    highest = max(overall_scores.values())
    leaders = [provider for provider in providers if overall_scores[provider] == highest]
    outcome = (f"本轮 {case_count} 页中，" + labels[leaders[0]] + " 的综合分最高，为 " + f'{highest * 100:.2f}%' + "。"
               if len(leaders) == 1 else
               f"本轮 {case_count} 页的综合分最高值为 " + percent(highest) + "，由 " +
               "、".join(labels[provider] for provider in leaders) + " 并列取得。")
    variant_ids = [item['provider'] for item in profile.get('variants', [])]
    if len(variant_ids) != 2:
        raise ValueError('The complete report requires both independently measured Moli repair variants')
    prior_moli, content_moli = variant_ids
    changed_moli = [case_id for case_id in cases if len({
        target_body[grouped[case_id][provider]['attempt_id']]
        for provider in ('moli', prior_moli, content_moli)
    }) > 1]
    changed_moli_rows = ["<tr><td>" + anchor("cases/index.html?id=" + case_id, case_id) +
                         "</td><td>" + ("有正文" if target_body[grouped[case_id]['moli']['attempt_id']] else "无正文") +
                         "</td><td>" + ("有正文" if target_body[grouped[case_id][prior_moli]['attempt_id']] else "无正文") +
                         "</td><td>" + ("有正文" if target_body[grouped[case_id][content_moli]['attempt_id']] else "无正文") +
                         "</td><td>" + anchor("cases/index.html?id=" + case_id, "查看三版输出") +
                         "</td></tr>" for case_id in changed_moli]
    comparison_rows = []
    for provider in ('moli', prior_moli, content_moli):
        aggregate = summaries[provider]['official_aggregate']['scores']
        comparison_rows.append('<tr><td>' + esc(labels[provider]) + '</td><td>' +
                               str(target_body_count(provider)) + '/' + str(case_count) + '</td>' + ''.join(
                               '<td>' + f"{aggregate[metric] * 100:.2f}%" + '</td>'
                               for metric, _ in measures) + '</tr>')
    fixed_section = (f"""<section id="moli-repair-lineage"><h2>Moli 三个版本：两次修复各自保留</h2><p>{prose('fixed_quality')}</p><p>{prose('fixed_mechanism')}</p>{table([t('version', 'Version'), '目标正文', *[label for _, label in measures]], comparison_rows)}{(table([t('task', 'Task'), '原版正文', labels[prior_moli], labels[content_moli], '逐项证据'], changed_moli_rows) if changed_moli_rows else '')}<p class="note">两个修复版均为独立的 {case_count} 页认证运行，运行日期不同，耗时不作版本快慢结论。{anchor(profile.get('method_binding_file', 'seven-method-binding.json'), '三轮证据绑定')} · {anchor('https://github.com/lexmount/moli/pull/785', 'PR785')} · {anchor('https://github.com/lexmount/moli/pull/814', 'PR814（历史前身）')} · {anchor('https://github.com/lexmount/moli/pull/919', 'PR919（本次实测来源）')}</p><p class="note">上表内容修复版绑定 PR919 提交 <code>{esc(content_fixed_commit[:9])}</code>：在最新 main 上完成 release 编译和 {case_count} 页认证运行。历史 PR814 结果保留在更新记录中，不混入本表。</p></section>""")
    correction_section = ''
    if correction:
        corrected_id = correction['preflight']['case_id']
        correction_section = (f"""<section id="fixture-correction"><h2>狼人杀规则页：测试地址导致的假失败已纠正</h2><p>页面脚本会检查当前域名和路径。旧环境把地址替换为 localhost，触发跳回原站；隔离规则随后阻断访问。修正后的环境保留原网址，由本地代理提供同一份冻结 HTML，继续阻断外部资源。七种方法使用各自原受测二进制完成同页认证补测，均返回正文。</p>{table([t('method', 'Method'), '补测正文字符数', '补测全文分', '证据'], ['<tr><td>' + esc(labels[p]) + '</td><td>' + str(grouped[corrected_id][p]['output_chars']) + '</td><td>' + percent(score(grouped[corrected_id][p], 'text_edit')) + '</td><td>' + anchor('results/index.html?provider=' + p + '&id=' + corrected_id, '完整输出') + '</td></tr>' for p in providers])}<p>这七条结果统一替换旧环境下的同页结果，其他 {case_count - 1} 页不变。两个修复版 Moli 均生成正文 {min(target_body_count(prior_moli), target_body_count(content_moli))}/{case_count}；历史 {case_count - 1}/{case_count} 对应修正前的测试环境。旧调用额外耗时 {correction['superseded_execution_ms']:,.0f} ms 单独保留，不混入补测最终调用延迟。</p><p>{anchor('case-correction.json', '认证与修订依据')} · {anchor('superseded-case-results.jsonl', '被替换的七条历史结果')}</p></section>""")
    method_rows = []
    diagnostic_rows = []
    diagnostic_kinds = (t('request_failure', 'Request failure'), "空输出", "错误页", "低全文相似度")
    for provider in providers:
        selected = [row for row in records if row["provider"] == provider]
        issues = sum(categories[row["attempt_id"]] != "未触发诊断" for row in selected)
        profile_item = method_error_profiles[provider]
        method_rows.append("<tr><td>" + anchor("analysis/index.html?provider=" + provider, labels[provider]) +
                           "</td><td>" + str(target_body_count(provider)) +
                           "/" + str(case_count) + "</td><td>" + str(issues) + "</td><td>" + esc(profile_item["runtime"]) +
                           "</td><td>" + esc(profile_item["quality"]) + "</td><td>" +
                           esc(profile_item["reading"]) + "</td></tr>")
        diagnostic_rows.append("<tr><td>" + esc(labels[provider]) + "</td>" + "".join(
            '<td data-diagnostic-provider="' + provider + '" data-kind="' + kind + '">' +
            anchor("analysis/index.html?provider=" + provider + "&kind=" + kind,
                   str(sum(categories[row["attempt_id"]] == kind for row in selected))) + "</td>"
            for kind in diagnostic_kinds) + "</tr>")
    cause_counts = Counter((row["category_label"], row["provider"])
                           for row in failure_analysis["reviewed_failures"])
    unknown_causes = {cause for cause, _ in cause_counts} - set(cause_owners)
    if unknown_causes:
        raise ValueError("Reviewed failure cause lacks an owner: " + ", ".join(sorted(unknown_causes)))
    cause_rows = []
    for cause in cause_order:
        cells = []
        for provider in providers:
            count = cause_counts[cause, provider]
            label = str(count) if count else "—"
            target = "analysis/index.html?provider=" + provider + "&cause=" + cause
            cells.append('<td data-cause-provider="' + provider + '" data-cause-kind="' + esc(cause) + '">' +
                         (anchor(target, label) if count else label) + '</td>')
        cause_rows.append('<tr><td>' + esc(cause) + '</td>' + ''.join(cells) +
                          '<td>' + esc(cause_owners[cause]) + '</td></tr>')
    moli_fix_slides = []
    for item in moli_fix_profiles:
        facts = ''.join('<div><strong>' + label + '</strong>' + esc(item[key]) + '</div>'
                        for label, key in (("现象", "symptom"), ("根因", "root"),
                                           ("修复", "fix"), ("验证", "verification")))
        moli_fix_slides.append(
            '<article class="presentation-slide" data-moli-fix-slide="' + esc(item["image"]) + '"><h3>' +
            esc(item["title"]) + '</h3>' + illustration(item["image"], item["title"]) +
            '<div class="slide-facts">' + facts + '</div></article>')
    moli_fix_figures = ('<section id="moli-fix-types"><h2>Moli：六类问题各用一张图说明</h2>'
                        '<p class="note">每张图按现象、根因、修复和泛化验证组织，可单独作为评审幻灯片。</p>' +
                        ''.join(moli_fix_slides) + '</section>')
    guide_error_landscape = (
        f"""<section id="error-landscape"><h2>各方法错在哪、差在哪</h2><div class="presentation-slide">{illustration('seven-method-error-taxonomy.png', '错误先分为运行交付故障和内容质量缺口，再按方法定位主要问题族。')}</div><div class="presentation-slide"><h3>精确数量：固定总体质量风险</h3>{illustration('method-error-landscape.png', '七种方法的正文交付、全文、代码、公式和表格问题分布。')}{quality_issue_matrix()}<p class="note">同一结果可以同时属于多类问题；低分阈值用于定位，不等于二元通过率。</p></div><div class="presentation-slide"><h3>运行结果类型与已复核根因</h3>{table([t('method', 'Method'), *diagnostic_kinds], diagnostic_rows)}{table(['根因', *[labels[provider] for provider in providers], '归属或定位'], cause_rows)}<p>{str(len(reviewed_failures))}/{str(machine_issue_count)} {prose('review_scope_failure')}；{str(len(records) - len(reviewed_failures))} {prose('review_scope_pass')}</p>{anchor('failure-analysis.json', '查看逐条失败原因与处理')}</div><div class="presentation-slide"><h3>逐方法错误画像</h3>{table([t('method', 'Method'), '目标正文', '自动诊断数', '运行交付错误', '主要质量缺口', '处理优先级'], method_rows, table_id='method-error-profiles')}<p class="note">质量缺口是固定阈值下的测量结果；只有有直接证据的运行故障才给出根因归属。</p></div></section>""")
    page("guide/index.html", "结果解读",
         (f"""<section class="hero"><h1>结果解读</h1><p>{esc(outcome)} {prose('guide_lead')}</p></section>{illustration('seven-method-overview.png', f'{case_count} 页七方法结果、目标正文覆盖与两次 Moli 修复的关系；分数与计分范围以表格为准。')}<section><h2>核心结果</h2>{metric_table()}<p class="note">{prose('guide_scope')}</p></section>{guide_error_landscape}{moli_fix_figures}{illustration('moli-repair-mechanisms.png', '从内容缺陷、实现原因到修复层和输出变化的对应关系。')}{fixed_section}{correction_section}<section><h2>核对完整输出</h2><p>{prose('guide_outputs')}</p>{anchor('analysis/index.html?kind=低全文相似度', '查看低全文分案例')}</section><section><h2>可检查的一页</h2><p>{prose('guide_example')}</p><p>{example_links}</p></section><section id="content-audit"><h2>{case_count} 页内容审计如何进入修复</h2><p>{prose('audit_scope')}</p><p>{prose('audit_findings')}</p><p>{prose('audit_resolution')}</p><p class="note">{prose('audit_limit')}</p></section><section><h2>""" + t('decision', 'Conclusion') + f"</h2><p>{prose('guide_conclusion')}</p></section>"))
    evidence_links = ["current-report.json", "cohort-certificate.json", "scoring-certificate.json",
                      *[item['profile'] for item in profile.get('variants', [])],
                      *[item['provider'] + "-cohort-certificate.json" for item in profile.get('variants', [])],
                      *[item['provider'] + "-scoring-certificate.json" for item in profile.get('variants', [])],
                      profile.get("method_binding_file", "seven-method-binding.json"),
                      "execution.json", "dataset.json",
                      "results.jsonl", "physical-attempts.jsonl",
                      "score-summaries.json", "scoring-correction.json",
                      "report-content.json", "failure-analysis.json"]
    if (out / "teds-correction-review.json").is_file():
        evidence_links.append("teds-correction-review.json")
    if correction:
        evidence_links += ['case-correction.json', 'case-correction-scoring.json',
                           'superseded-case-results.jsonl', 'superseded-case-calls.jsonl']
    page("reproduce/index.html", "复现依据",
         '<section class="hero"><h1>复现依据</h1><p>' + prose('reproduce_lead') + '</p></section><p>' +
         " · ".join(anchor(name, name) for name in evidence_links) + "</p>")
    releases = json.loads((repository_root() / 'research/report-changes.json').read_text(encoding='utf-8'))['releases']
    webmainbench_releases = [item for item in releases if 'webmainbench_frozen_html_to_markdown' in item.get('comparison_kinds', [])]
    history_rows = ['<tr data-release-id="' + esc(item['id']) + '"><td>' + esc(item['date']) +
                    '</td><td>' + esc(item['title']) + '</td><td>' + esc(item['summary']) + '</td></tr>'
                    for item in webmainbench_releases]
    page('history/index.html', '更新记录',
         f"""<section class="hero"><h1>报告更新记录</h1><p>按发布记录说明读者可见的结果与内容变化。</p></section>{table(['日期', '更新', '内容'], history_rows)}""")
    page("viewer/index.html", "证据查看器",
         ('<section class="hero"><h1>证据查看器</h1><p>按需载入原始证据。</p></section><div class="panel"><p id="evidence-path"></p><p><a id="evidence-raw" href="#">' + t('original_file', 'Original file') + '</a></p><pre id="evidence-content">正在载入…</pre></div>'))
    page("en/index.html", "英文版未生成",
         f"""<section class="hero"><h1>英文版未生成</h1><p>本轮报告仅生成中文版。</p></section>{anchor('index.html', '返回中文报告')}""")
    (out / "report-manifest.json").write_text(json.dumps({
        "schema_version": 1, "report": "webmainbench-545-full", "languages": ["zh"],
        "case_count": case_count, "provider_count": len(providers), "result_count": len(records),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_score_audit(out, audit, binding):
 """Explain changed decisions from one bound review, beside native results."""
 def verdict(value):return t('pass_2', 'Pass') if value else t('failed', 'Failed')
 rows=[]
 for item in audit['providers']:
  rows.append((' data-audit-summary-provider="'+esc(item['provider'])+'"',cell(esc(provider_label(item['provider'])))+cell(str(item['published_passes']))+cell(str(item['historical_passes']))+cell(str(item['initial_rescore_passes']))+(cell(str(item['original_definition_passes'])) if 'original_definition_passes' in item else '')+cell(str(item['corrected_passes']))+cell(str(item['total']))))
 body=hero(audit['title'],audit['summary'])
 body+=section('判定变化来自哪里','<figure><a href="../'+esc(binding['figure'])+'"><img src="../'+esc(binding['figure'])+'" alt="对同一份保存输出核对正文术语、文字和链接格式、卡片字段归属，以及原始与公开副本哈希" style="width:100%;height:auto"></a><figcaption>'+esc(audit['figure_caption'])+'</figcaption></figure>')
 body+=section('版本结果对照','<p>'+esc(audit['metric_note'])+'</p>'+table([t('method', 'Method'),'原页面汇总','按旧规则核对旧记录','修复前重算']+([t('original_definitions_current_scorer', 'Original definitions + current scorer')] if all('original_definition_passes' in item for item in audit['providers']) else [])+[t('revised_definitions_current_scorer', 'Revised definitions + current scorer'),'题数'],rows,id='score-audit-comparison',metrics=[]))
 body+=section(esc(audit['focus_heading']), '<p>'+esc(audit['focus_summary'])+'</p>')
 inventory=[]
 for item in audit['changes']:
  identifier=item['attempt_id']
  inventory.append(('',cell(esc(item['provider']))+cell(esc(item['case_id']))+cell(verdict(item['historical_pass']))+cell(verdict(item['initial_rescore_pass']))+cell(verdict(item['after_fix_pass']))+cell(esc(item['classification']))+cell(link('#'+identifier,'原因与证据'))))
 controls='<label>筛选方法 <select id="audit-provider" onchange="document.querySelectorAll(\'[data-audit-provider]\').forEach(r=>r.hidden=this.value!==\'\'&&r.dataset.auditProvider!==this.value)"><option value="">全部方法</option>'+''.join('<option value="'+esc(item['provider'])+'">'+esc(provider_label(item['provider']))+'</option>' for item in audit['providers'])+'</select></label>'
 markup=table([t('method', 'Method'),'题目','旧判定','修复前','修复后','原因分类','证据'],inventory,metrics=[])
 # Keep paragraphs in the evidence sections, outside the fixed-height inventory.
 document=BeautifulSoup(markup,'html.parser')
 for element,item in zip(document.select('tbody tr'),audit['changes'],strict=True):element['data-audit-provider']=item['provider']
 body+=section('逐项判定',controls+'<div style="max-height:60vh;overflow:auto">'+str(document)+'</div>')
 body+=section('脱敏副本的影响','<p>'+esc(audit['privacy_summary'])+'</p>'+''.join('<p>'+esc(text)+'</p>' for text in audit['privacy_findings']))
 details=[]
 for item in audit['changes']:
  identifier=item['attempt_id']
  facts=''.join('<dt>'+label+'</dt><dd>'+esc(item[key])+'</dd>' for key,label in [('requirement',t('acceptance_requirement', 'Acceptance requirement')),('actual',t('actual_output', 'Actual output')),('reason','变化原因'),('treatment','处理结果'),('source_scope','来源范围')])
  evidence=' · '.join(link('../'+('audit/history/' if evidence.get('historical') else '')+evidence['path'],evidence.get('label') or evidence['path']) for evidence in item.get('evidence',[]))
  quotations=''.join('<h4>'+esc(quote['label'])+'</h4><pre dir="auto">'+esc(quote['text'])+'</pre>' for quote in item.get('quotes',[]))
  details.append(f'''<article id="{esc(identifier)}" class="panel"><h3>{esc(item['provider'] + ' · ' + item['case_id'])}</h3><p>{esc(item['classification'])} · {verdict(item['historical_pass'])} → {verdict(item['initial_rescore_pass'])} → {verdict(item['after_fix_pass'])}</p><dl class="diagnostic-fields">{facts}</dl><details><summary>原文、规则与实际输出片段</summary>{quotations}<p>{evidence}</p></details><p>{link('../attempts/' + identifier + '.html', '完整结果、实际输出和调用日志')}</p></article>''')
 body+=section('每条变化的依据',''.join(details))
 body+=section('审查范围','<p>'+esc(audit['review_scope'])+'</p><p>'+link('score-changes.json','下载逐条复核记录')+' · '+link('scoring-inputs.json','下载原始与公开评分输入哈希')+'</p>')
 write(out,'audit/index.html',audit['title'],body)
 write(out,'review/index.html',t('review_record', 'Review record'),hero(t('review_record', 'Review record'),'逐题验收依据与数据来源。')+section('核验范围','<p>'+esc(audit['review_scope'])+'</p><p>'+esc(audit['summary'])+'</p><p>'+link('../audit/index.html','逐条原因、原文与验收规则')+'</p>'))
