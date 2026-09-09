import tempfile
from pathlib import Path
import unittest
from harness.web_fetch.report import failure_category,filter_controls,case_attrs,failure_filters,review_cell,table,write
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
   write(root,'en/index.html','English edition','<p>English</p>')
   zh=(root/'index.html').read_text();en=(root/'en/index.html').read_text()
   self.assertIn('href="en/index.html">English</a>',zh)
   self.assertIn('lang="en"',en)
   self.assertIn('href="../index.html">中文</a>',en)
 def test_english_tables_can_localize_the_index_header(self):
  rendered=table(['Method'],[('', '<td>Moli</td>')],index_header='No.')
  self.assertIn('<th>No.</th>',rendered)
  self.assertNotIn('<th>序号</th>',rendered)
