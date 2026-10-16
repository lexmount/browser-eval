import tempfile
import json
from pathlib import Path
import unittest
from harness.web_fetch.report import EXTRA,active_tracks,failure_category,filter_controls,case_attrs,failure_filters,overall_ranking,overview_matched_latency,provider_configuration,review_cell,table,write
from harness.web_fetch.report_interfaces import SPECS,HEADLESS_ACCESS
from harness.web_fetch.report_style import STYLE

class ReportRevisionTests(unittest.TestCase):
 def test_overview_latency_includes_every_configuration_and_same_case_mean(self):
  cases={cid:{'id':cid,'name':cid,'task_kind':'body'} for cid in ('a','b','c')}
  providers=['moli','chromium','lightpanda','obscura','kitesurf','moli-1.0.6','moli-1.1.3']
  records=[]
  for provider in providers:
   for cid,value in zip(cases,[1000,3000,99000]):
    passed=not(provider=='moli-1.0.6' and cid=='c')
    records.append({'provider':provider,'case_id':cid,'ok':passed,
                    'metrics':{'content_pass':passed},'elapsed_ms':value,
                    'started_at':'2026-09-04T00:00:00+00:00'})
  rendered=overview_matched_latency(records,cases)
  self.assertIn('全部 7 种受测配置',rendered)
  self.assertIn('<strong>2/3</strong>',rendered)
  self.assertIn('同题均值',rendered)
  self.assertIn('同题 P95',rendered)
  self.assertIn('<summary>按需查看共同成功任务耗时</summary>',rendered)
  self.assertEqual(rendered.count('<strong>2,000 ms</strong>'),14)
  self.assertEqual(rendered.count('<strong>3,000 ms</strong>'),7)
  for provider in providers:self.assertIn('providers/'+provider+'.html',rendered)
  self.assertIn('跨日期、跨批次',rendered)
  self.assertNotIn('99,000 ms',rendered)
  self.assertIn('调用矩阵尚不完整',overview_matched_latency(records[1:],cases))

 def row(self,**kw):return dict({'ok':True,'metrics':{'content_pass':False,'length_pass':True}},**kw)
 def test_provider_filter_defaults_to_the_complete_population(self):
  controls=filter_controls([('provider','方法',[('one','One'),('two','Two')])])
  self.assertIn('data-filter="provider" aria-label="方法"><option value="">全部</option>',controls)
 def test_case_search_includes_identifier_and_source_url(self):
  c={'id':'case-a','name':'Readable title','url':'https://example.com/source','category':'test','task_kind':'body'}
  r=self.row(provider='one',case_id='case-a')
  attrs=case_attrs(r,c)
  self.assertIn('data-search="case-a Readable title https://example.com/source ',attrs)
 def test_result_default_failure(self):
  controls=filter_controls([('result','结果',[('pass','任务通过'),('fail','任务失败')])])
  self.assertIn('value="fail" selected',controls)
 def test_overall_ranking_shows_all_call_latency_without_affecting_pass_order(self):
  cases={'a':{'id':'a','track':'web','task_kind':'body'},
         'b':{'id':'b','track':'web','task_kind':'body'}}
  records=[
   {'provider':'fast-low-pass','case_id':'a','ok':True,'elapsed_ms':10,'metrics':{'content_pass':True}},
   {'provider':'fast-low-pass','case_id':'b','ok':False,'elapsed_ms':20,'metrics':{'content_pass':False}},
   {'provider':'slow-high-pass','case_id':'a','ok':True,'elapsed_ms':100,'metrics':{'content_pass':True}},
   {'provider':'slow-high-pass','case_id':'b','ok':True,'elapsed_ms':200,'metrics':{'content_pass':True}},
  ]
  rendered=overall_ranking(records,cases,call_budget_seconds=45)
  from bs4 import BeautifulSoup
  headers=[cell.get_text(' ',strip=True) for cell in BeautifulSoup(rendered,'html.parser').select('#ranking-overall thead th')]
  self.assertEqual(headers[-3:],['总耗时均值 ↕','总耗时 P50 ↕','总耗时 P95 ↕'])
  self.assertNotIn('全调用', ' '.join(headers))
  self.assertIn('总耗时均值 / P50 / P95',rendered)
  self.assertIn('包含失败重试和重试等待',rendered)
  self.assertIn('每次物理调用预算为 45 秒',rendered)
  from harness.web_fetch.report_i18n import translate_text
  self.assertEqual([translate_text(label) for label in ('均值','P50','P95')],['Mean','P50','P95'])
  self.assertIn('包含通过与失败',rendered)
  self.assertLess(rendered.index('slow-high-pass'),rendered.index('fast-low-pass'))
  self.assertIn('150 ms',rendered)
 def test_ranking_method_names_do_not_wrap_and_tables_remain_scrollable(self):
  self.assertIn('.table-wrap{overflow:auto',STYLE)
  self.assertRegex(EXTRA,r'\.ranking td:nth-child\(2\)\{[^}]*min-width:220px[^}]*white-space:nowrap')
  self.assertRegex(EXTRA,r'\.ranking td:nth-child\(2\)\{[^}]*overflow-wrap:normal[^}]*word-break:normal')
 def test_failure_filter_defaults_and_deduplication(self):
  missing={'content_pass':False,'length_pass':True,'resource_pass':False,
           'resource_results':[{'id':'target','pass':False}], 'unexpected_resource_urls':[]}
  rows=[self.row(case_id='case',metrics=missing),self.row(case_id='case',metrics=missing),
        self.row(case_id='case',metrics={'content_pass':True,'resource_pass':True})]
  options=failure_filters(rows,{'case':{'task_kind':'resource'}})
  self.assertEqual(options[1][2],[('缺少目标资源','缺少目标资源')])
  controls=filter_controls(options)
  for key in ('failureStage','failureCategory'):
   self.assertRegex(controls,'data-filter="'+key+'"[^>]*><option value="">全部</option>')
 def test_failure_stage_partition(self):
  case={'category':'test','task_kind':'body'}
  for ok,passed,expected in [(False,False,'request'),(True,False,'task'),(True,True,'')]:
   r=self.row(ok=ok,provider='test',metrics={'content_pass':passed,'length_pass':False})
   self.assertIn(f'data-failure-stage="{expected}"',case_attrs(r,case))
   if passed:self.assertIn('data-failure-category=""',case_attrs(r,case))
 def test_api_auth_is_not_site_failure(self):
  self.assertEqual(failure_category(self.row(ok=False,error_type='authentication',status_code=403),{}),'接口鉴权失败')
 def test_upstream_access_denied(self):
  self.assertEqual(failure_category(self.row(ok=False,error_type='CRAWL_HTTP_401'),{}),'源站拒绝该次访问')
 def test_resource_missing(self):
  missing=self.row(metrics={'content_pass':False,'resource_pass':False,
                            'resource_results':[{'id':'target','pass':False}],
                            'unexpected_resource_urls':[]})
  self.assertEqual(failure_category(missing,{'task_kind':'resource'}),'缺少目标资源')
 def test_pass_no_failure(self):
  self.assertEqual(failure_category(self.row(metrics={'content_pass':True}),{}),'—')
 def test_all_interfaces(self):
  from harness.web_fetch.report_methods import PROFILES
  self.assertEqual(set(SPECS),set(PROFILES))
  self.assertTrue({'kitesurf','lightpanda','obscura','moli','chromium'} <= set(SPECS))
  for s in SPECS.values():
   self.assertTrue(s['params'] and s['fields'] and s['request'] and s['source'])
 def test_current_interface_and_capability_copy_is_localized(self):
  from harness.web_fetch.report_capabilities import CAPABILITIES, SOURCES
  from harness.web_fetch.report_i18n import translate_text
  current_interface_copy=[
   SPECS['moli-webfetch']['entry'],
   *(description for _,_,description in SPECS['moli-webfetch']['params']),
   SPECS['moli-webfetch']['notes'],
   SPECS['tavily']['params'][1][2], SPECS['tavily']['notes'],
   SPECS['exa']['params'][1][2], SPECS['exa']['notes'],
   SPECS['crawl4ai']['notes'],
   *(description for _,_,description in SPECS['firecrawl']['params']),
   *(description for _,_,description in SPECS['firecrawl']['fields']),
   SPECS['firecrawl']['notes'],
  ]
  current_capability_copy=[]
  for method in ('moli-webfetch','tavily','exa','firecrawl','cloudflare-markdown','crawl4ai'):
   row=CAPABILITIES[method]
   current_capability_copy.extend([row['scope'],row['tested'],row['notes']])
   current_capability_copy.extend(row[field]['detail'] for field in ('web','document','image','av','file','js'))
  current_capability_copy.extend(SOURCES[key][0]+' ↗' for key in ('moli','moli-recipes','moli-release'))
  for source in current_interface_copy+current_capability_copy:
   self.assertNotRegex(translate_text(source),r'[\u4e00-\u9fff]',source)
 def test_regional_endpoint_distinct(self):
  self.assertNotEqual(SPECS['lexmount-cn']['entry'],SPECS['lexmount-global']['entry'])
 def test_review_label_links_records_without_claiming_fresh_verification(self):
  audit={'attempts':[{'attempt_id':'one','final_verdict':'pass'}]}
  row=review_cell(audit,[{'attempt_id':'one'}])
  self.assertIn('1/1 · 复核记录',row)
  self.assertNotIn('已核验',row)
 def test_headless_access_covers_every_method_and_lists_setup_variables(self):
  self.assertEqual(set(HEADLESS_ACCESS),{'kitesurf','lightpanda','obscura','moli-1.0.6','moli','moli-1.1.3','moli-1.1.7','moli-1.1.11','moli-pr643','chromium'})
  for access in HEADLESS_ACCESS.values():
   self.assertTrue(access['env_vars'])
   self.assertTrue(access['links'])
 def test_language_toggle_uses_one_shared_report_tree(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   write(root,'index.html','中文','<p>正文</p>')
   from harness.web_fetch.report_i18n import write_english_report
   write_english_report(root)
   zh=(root/'index.html').read_text();en=(root/'en/index.html').read_text()
   self.assertIn('href="en/index.html" hreflang="en">English</a>',zh)
   self.assertIn('lang="en"',en)
   self.assertIn('href="../index.html" hreflang="zh-CN">中文</a>',en)
   self.assertNotIn('>首页</a>',zh)
   self.assertNotIn('>Home</a>',en)
 def test_english_tables_can_localize_the_index_header(self):
  rendered=table(['Method'],[('', '<td>Moli</td>')],index_header='No.')
  self.assertIn('<th>No.</th>',rendered)
  self.assertNotIn('<th>序号</th>',rendered)
 def test_overall_copy_and_columns_follow_the_actual_dataset(self):
  cases={
   'a':{'id':'a','track':'web','task_kind':'body'},
   'b':{'id':'b','track':'document','task_kind':'body'},
  }
  records=[{'case_id':key,'provider':'p','ok':True,'metrics':{'content_pass':True},'elapsed_ms':1}
           for key in cases]
  rendered=overall_ranking(records,cases)
  self.assertIn('计划的 2 个任务',rendered)
  self.assertIn('回到 2 个分榜',rendered)
  self.assertEqual(active_tracks(cases),[('web','静态网站正文'),('document','文档正文')])
  self.assertNotIn('150 个任务',rendered)
  self.assertNotIn('六类',rendered)
 def test_provider_configuration_is_method_specific(self):
  default={'endpoint':'endpoint','output':'text'}
  firecrawl=provider_configuration('firecrawl',[{'provider_type':'firecrawl'}],default)
  builtin=provider_configuration('builtin',[{'provider_type':'cowagent_builtin'}],default)
  chromium=provider_configuration('chromium',[{'provider_type':'headless_markdown','provider_metadata':{'extractor':'node-html-markdown','output_contract':'markdown'}}],default)
  self.assertIn('rawBase64',firecrawl)
  self.assertNotIn('Exa、Tavily、Firecrawl',builtin)
  self.assertIn('渲染 DOM',chromium)
  self.assertIn('node-html-markdown 2.0.0',chromium)
  moli=provider_configuration('moli',[{'provider_type':'headless_markdown','provider_metadata':{'extractor':'node-html-markdown','output_contract':'markdown'}}],default)
  self.assertNotIn('Chromium',moli)
  self.assertIn('node-html-markdown 2.0.0',moli)
 def test_dynamic_cohort_copy_has_english_translations(self):
  from harness.web_fetch.report_i18n import translate_text
  self.assertEqual(translate_text('90 个案例 · 12 种方法 · 4类分榜与一个综合通过率总榜。'),
                   '90 tasks · 12 methods · 4 family rankings and one overall coverage ranking.')
  self.assertEqual(translate_text('4类任务概览'),'4 task families')
  translated=translate_text('本轮请求正文文本；资源链接仅来自同一 Contents 响应的 extras 字段。 未启用额外的理解模型或付费后备方法。')
  self.assertNotIn('本轮',translated)
  self.assertIn('Contents response',translated)
 def test_speed_caption_localizes_its_actual_sample_count(self):
  from harness.web_fetch.report_i18n import translate_text,untranslated_english_narrative
  source=('同窗速度子集：预先声明 17 题，12 种方法使用同一完整任务样本。'
          '全调用耗时包含成功、失败和截止超时；通过调用耗时按各方法自己的成功子集计算，'
          '分母必须同时阅读。每种方法最多 17 个单次观察，P95 只作本次样本诊断。')
  translated=translate_text(source)
  self.assertIn('17 tasks were declared',translated)
  self.assertIn('at most 17 single observations',translated)
  self.assertNotIn('20 single observations',translated)
  samples=[
   '全部 12 种方法共同通过 0/17 题；共同通过数只作诊断，不用于筛选测速样本。'
   '报告保留预声明的全部 204 次调用，不按结果重新选题；全调用耗时可在相同分母下阅读，'
   '通过调用耗时只描述各方法的条件分布，不能解释为同题速度排名。',
   '这份旧版测速汇总只使用 8/20 个全部 7 种方法共同通过的同窗任务；它保留原报告的共同成功样本口径，'
   '不能补充解释失败等待，也不能与跨批次主结果直接排名。',
   '静态网页通过数：chromium 99/100。综合榜按 150 个任务等权计数；6 类分榜仍分别解释，'
   '耗时和文字质量不混入综合通过率。',
  ]
  for sample in samples:
   self.assertEqual(untranslated_english_narrative('<p>'+translate_text(sample)+'</p>'),[])
 def test_english_article_audit_excludes_preserved_evidence_and_task_names(self):
  from harness.web_fetch.report_i18n import untranslated_english_narrative
  page=('<html><body><p>English explanation</p><p>中文任务名</p>'
        '<pre>标准答案原文</pre><code>实际输出原文</code></body></html>')
  self.assertEqual(untranslated_english_narrative(page, {'中文任务名'}),[])
  self.assertEqual(untranslated_english_narrative('<p>漏译的解释</p>'),['漏译的解释'])
  self.assertEqual(untranslated_english_narrative('<img alt="漏译的替代文字">'),
                   ['漏译的替代文字'])
 def test_generic_speed_and_scope_narrative_is_fully_localized(self):
  from harness.web_fetch.report_i18n import translate_text,untranslated_english_narrative
  samples=[
   '同一时间窗的速度怎样读',
   '主结果来自不同批次，耗时只用于逐次诊断；下面单列预先声明的同窗子集。'
   '全调用统计保留失败与截止超时，回答完整任务队列要等多久；通过调用统计只描述各方法已通过任务，'
   '成功分母可能不同，不能单独拿来排同题速度。',
   '统计按实际调用记录计算，未运行和缺少验收证据的项目不记为失败。静态网站正文、动态网站正文与热门网站正文分别陈述，'
   '文档、资源地址和原文件按各自契约验收。100 个普通网页中，GitHub 与 Python 官方文档占 48 个，样本偏向技术文档；'
   '每个方法与案例组合只有一次观察，不能估计波动或外推为全网成功率。调用日期相邻不构成同时间对照，公开练习站也不能代表生产网站分布。',
  ]
  localized=''.join('<p>'+translate_text(sample)+'</p>' for sample in samples)
  self.assertEqual(untranslated_english_narrative(localized),[])
 def test_english_article_gate_rejects_untranslated_narrative(self):
  from harness.web_fetch.report_i18n import write_english_report
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'guide').mkdir()
   (root/'guide/article.html').write_text('<html lang="zh-CN"><body><p>漏译的解释</p></body></html>')
   with self.assertRaisesRegex(ValueError,'Untranslated English article'):
    write_english_report(root)
 def test_english_article_gate_keeps_source_evidence(self):
  from harness.web_fetch.report_i18n import write_english_report
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'guide').mkdir()
   (root/'dataset.json').write_text('{"cases":[{"name":"中文任务名"}]}')
   (root/'guide/article.html').write_text(
       '<html lang="zh-CN"><body><p>中文任务名</p><pre>标准答案与实际输出原文</pre></body></html>')
   write_english_report(root)
   rendered=(root/'en/guide/article.html').read_text()
   self.assertIn('中文任务名',rendered)
   self.assertIn('标准答案与实际输出原文',rendered)
 def test_visible_accessibility_attributes_are_localized(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_i18n import localize_html
  page=('<html lang="zh-CN"><body><img alt="页面中稳定标题、栏目与说明作为内容核对点；'
        '变化条目检查数量、字段类型和结构；全文参考单独声明范围。"></body></html>')
  rendered=localize_html(page,PurePosixPath('guide/article.html'))
  self.assertIn('alt="Stable headings, sections, and explanations',rendered)
  self.assertNotIn('页面中稳定标题',rendered)
 def test_methodology_figure_ui_and_captions_pass_the_article_gate(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_explainer import METHODOLOGY_FIGURES
  from harness.web_fetch.report_i18n import localize_html,untranslated_english_narrative
  figures=[]
  for index,(_,title,alt,caption) in enumerate(METHODOLOGY_FIGURES,1):
   figures.append(f'<figure><img alt="{alt}"><a aria-label="获取原图：{title}"></a>'
                  f'<figcaption>图 {index} · {title}。{caption} 点击图像获取原图。</figcaption></figure>')
  rendered=localize_html('<html lang="zh-CN"><body>'+''.join(figures)+'</body></html>',
                         PurePosixPath('guide/article.html'))
  self.assertEqual(untranslated_english_narrative(rendered),[])
  self.assertIn('Figure 3 · Scoring:',rendered)
  self.assertIn('aria-label="Download original:',rendered)
 def test_article_aria_labels_localize_without_touching_evidence_attributes(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_i18n import localize_html
  markup='<nav aria-label="文章目录" data-evidence="文章目录"></nav><img src="data:image/png;base64,YQ==" alt="文章目录">'
  translated=localize_html(markup,PurePosixPath('guide/article.html'))
  self.assertIn('aria-label="Article contents"',translated)
  self.assertIn('data-evidence="文章目录"',translated)
  self.assertIn('alt="Article contents"',translated)
  self.assertIn('src="data:image/png;base64,YQ=="',translated)
  other=localize_html(markup,PurePosixPath('attempts/example.html'))
  self.assertIn('aria-label="Article contents"',other)
  self.assertIn('alt="Article contents"',other)
  self.assertIn('data-evidence="文章目录"',other)
 def test_article_selects_localized_assets_before_rewriting_relative_urls(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_i18n import localize_html
  markup='<a href="figures/zh.png" data-en-href="figures/en.png"><img src="figures/zh.png" data-en-src="figures/en.png" alt="文章目录" data-evidence="原始证据"></a>'
  for route in ('guide/index.html','guide/article.html'):
   translated=localize_html(markup,PurePosixPath(route))
   self.assertIn('href="../../guide/figures/en.png"',translated)
   self.assertIn('src="../../guide/figures/en.png"',translated)
   self.assertNotIn('data-en-',translated)
   self.assertIn('data-evidence="原始证据"',translated)
 def test_methodology_selects_english_figure_without_localizing_measured_markup(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_i18n import localize_html
  markup='<img src="../assets/methodology/zh.png" data-en-src="../assets/methodology/en.png" alt="参考标注"><pre><img src="https://example.org/evidence.png" data-en-src="https://example.org/wrong.png"></pre>'
  result=localize_html(markup,PurePosixPath('methodology/index.html'))
  self.assertIn('src="../../assets/methodology/en.png"',result)
  self.assertNotIn('src="../../assets/methodology/zh.png"',result)
  self.assertIn('<pre><img src="https://example.org/evidence.png" data-en-src="https://example.org/wrong.png"></pre>',result)

 def test_article_embedded_assets_and_measured_markup_stay_distinct(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_i18n import localize_html
  markup='<img src="data:image/png;base64,YQ==" data-en-src="data:image/png;base64,Yg==" alt="文章目录"><pre><img src="data:image/png;base64,YQ==" alt="文章目录"></pre>'
  translated=localize_html(markup,PurePosixPath('guide/article.html'))
  self.assertIn('src="data:image/png;base64,Yg==" alt="Article contents"',translated)
  self.assertIn('<pre><img src="data:image/png;base64,YQ==" alt="文章目录"></pre>',translated)
  outside=localize_html(markup,PurePosixPath('attempts/example.html'))
  self.assertIn('src="data:image/png;base64,YQ==" data-en-src="data:image/png;base64,Yg=="',outside)


def test_methodology_image_parity_rejects_wrong_or_missing_english_companion(tmp_path):
 import pytest
 from harness.web_fetch.report_check import PageShape, check_localized_image_sources
 zh=PageShape(tmp_path/'methodology/index.html')
 zh.feed('<img src="zh.png" data-en-src="en.png">')
 for source in ('en.png','zh.png','missing.png',None):
  en=PageShape(tmp_path/'en/methodology/index.html')
  en.feed('<img src="'+source+'">' if source else '<p>No figure</p>')
  if source=='en.png':
   check_localized_image_sources(zh,en,lambda page,value:value)
   assert zh.data==en.data
  else:
   with pytest.raises(ValueError,match='Localized image'):
    check_localized_image_sources(zh,en,lambda page,value:value)
class MeasuredInterfaceIdentityTests(unittest.TestCase):
 def render(self, records, name='moli'):
  import json
  from harness.web_fetch.report_interfaces import render_interfaces
  with tempfile.TemporaryDirectory() as directory:
   out=Path(directory);(out/'adoption').mkdir();(out/'answer.md').write_text('Body')
   rows=[{'case_id':'static-simple','attempt_id':str(i),'artifact_path':'answer.md',**r} for i,r in enumerate(records)]
   render_interfaces(out,rows)
   return json.loads((out/'adoption/interfaces'/f'{name}.json').read_text())
 def test_generic_moli_uses_observed_release_and_task_adapter(self):
  record={'provider':'moli','provider_type':'moli_webfetch','provider_metadata':{'reported_version':'moli 1.1.15'}}
  output=self.render([record]);access=output['access']
  self.assertIn('/tag/v1.1.15',access['links'][0][1])
  self.assertNotIn('1.1.5',str(access))
  self.assertEqual(output['interface']['source'],'harness/web_fetch/providers/moli.py')
  old={'provider':'moli-1.0.6','provider_type':'headless_markdown','provider_metadata':{'reported_version':'moli 1.0.6'}}
  output=self.render([old],'moli-1.0.6')
  self.assertIn('1.0.6',output['access']['links'][0][1])
  self.assertEqual(output['interface'],json.loads(json.dumps(SPECS['moli-1.0.6'])))
 def test_unknown_or_mixed_generic_versions_do_not_invent_a_release(self):
  rows=[{'provider':'moli','provider_metadata':{'reported_version':'moli '+v}} for v in ('1.1.14','1.1.15')]
  for records in ([],rows):
   self.assertNotIn('/tag/',str(self.render(records)['access']))
 def test_native_metadata_envelopes_keep_native_output_and_actual_cli_examples(self):
  engines=[('lightpanda','lightpanda_json','--json'),('obscura','obscura_eval','--eval')]
  for name,format_name,flag in engines:
   record={'provider':name,'provider_type':'headless_markdown','provider_metadata':{'native_metadata':{'format':format_name,'script_sha256':'a'*64,'script_path':'/private/local-script.js'}}}
   output=self.render([record],name)
   self.assertIn(flag,output['interface']['entry'])
   self.assertNotIn('/private/local-script.js',str(output))
   if name=='obscura':self.assertNotIn('--dump',str(output['interface']['request']))
