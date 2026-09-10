import tempfile
from pathlib import Path
import unittest
from harness.web_fetch.report import active_tracks,failure_category,filter_controls,case_attrs,failure_filters,overall_ranking,provider_configuration,review_cell,table,write
from harness.web_fetch.report_interfaces import SPECS,HEADLESS_ACCESS

class ReportRevisionTests(unittest.TestCase):
 def row(self,**kw):return dict({'ok':True,'metrics':{'content_pass':False,'length_pass':True}},**kw)
 def test_result_default_failure(self):
  controls=filter_controls([('result','结果',[('pass','任务通过'),('fail','任务失败')])])
  self.assertIn('value="fail" selected',controls)
 def test_failure_filter_defaults_and_deduplication(self):
  rows=[self.row(case_id='case'),self.row(case_id='case'),self.row(case_id='case',metrics={'content_pass':True})]
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
  self.assertEqual(failure_category(self.row(),{'task_kind':'resource'}),'缺少目标资源')
 def test_pass_no_failure(self):
  self.assertEqual(failure_category(self.row(metrics={'content_pass':True}),{}),'—')
 def test_all_interfaces(self):
  from harness.web_fetch.report_methods import PROFILES
  self.assertEqual(set(SPECS),set(PROFILES))
  self.assertTrue({'kitesurf','lightpanda','obscura','moli','chromium'} <= set(SPECS))
  for s in SPECS.values():
   self.assertTrue(s['params'] and s['fields'] and s['request'] and s['source'])
 def test_regional_endpoint_distinct(self):
  self.assertNotEqual(SPECS['lexmount-cn']['entry'],SPECS['lexmount-global']['entry'])
 def test_review_label_names_codex(self):
  audit={'attempts':[{'attempt_id':'one','final_verdict':'pass'}]}
  row=review_cell(audit,[{'attempt_id':'one'}])
  self.assertIn('1/1 · Codex 已核验',row)
 def test_headless_access_covers_every_method_and_lists_setup_variables(self):
  self.assertEqual(set(HEADLESS_ACCESS),{'kitesurf','lightpanda','obscura','moli-1.0.6','moli','moli-1.1.3','chromium'})
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
  self.assertIn('links 与 images',firecrawl)
  self.assertNotIn('Exa、Tavily、Firecrawl',builtin)
  self.assertIn('Chromium 渲染 DOM',chromium)
 def test_dynamic_cohort_copy_has_english_translations(self):
  from harness.web_fetch.report_i18n import translate_text
  self.assertEqual(translate_text('90 个案例 · 12 种方法 · 4类分榜与一个综合通过率总榜。'),
                   '90 tasks · 12 methods · 4 family rankings and one overall coverage ranking.')
  self.assertEqual(translate_text('4类任务概览'),'4 task families')
  translated=translate_text('本轮请求正文文本；资源链接仅来自同一 Contents 响应的 extras 字段。 未启用额外的理解模型或付费后备方法。')
  self.assertNotIn('本轮',translated)
  self.assertIn('Contents response',translated)
