import tempfile
from pathlib import Path
import unittest
from harness.web_fetch.report import failure_category,filter_controls,case_attrs,failure_filters,review_cell,table,write,overview_matched_latency
from harness.web_fetch.report_interfaces import SPECS,HEADLESS_ACCESS

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
  self.assertEqual(rendered.count('<strong>2,000 ms</strong>'),14)
  self.assertEqual(rendered.count('<strong>3,000 ms</strong>'),7)
  for provider in providers:self.assertIn('providers/'+provider+'.html',rendered)
  self.assertIn('跨日期、跨批次',rendered)
  self.assertNotIn('99,000 ms',rendered)
  self.assertIn('调用矩阵尚不完整',overview_matched_latency(records[1:],cases))

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
  self.assertIn('aria-label="文章目录"',other)
  self.assertIn('alt="文章目录"',other)
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
 def test_article_embedded_assets_and_measured_markup_stay_distinct(self):
  from pathlib import PurePosixPath
  from harness.web_fetch.report_i18n import localize_html
  markup='<img src="data:image/png;base64,YQ==" data-en-src="data:image/png;base64,Yg==" alt="文章目录"><pre><img src="data:image/png;base64,YQ==" alt="文章目录"></pre>'
  translated=localize_html(markup,PurePosixPath('guide/article.html'))
  self.assertIn('src="data:image/png;base64,Yg==" alt="Article contents"',translated)
  self.assertIn('<pre><img src="data:image/png;base64,YQ==" alt="文章目录"></pre>',translated)
  outside=localize_html(markup,PurePosixPath('attempts/example.html'))
  self.assertIn('src="data:image/png;base64,YQ==" data-en-src="data:image/png;base64,Yg=="',outside)
