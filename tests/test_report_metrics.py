import unittest
from harness.web_fetch.metrics import evaluate_content,evaluate_resources,word_overlap,summarize
from harness.web_fetch.report import succeeded,ranking,case_attrs,table,rate,filter_controls
from harness.web_fetch.report_components import basis_label,reference_label,tabs

def record(ok=True,passed=True):
 return {'provider':'test','provider_type':'fixture','case_id':'c','case_name':'case','category':'test','url':'https://example.com','elapsed_ms':100,'ok':ok,'error_type':None,'metrics':{**evaluate_content('hello world',['hello']), 'content_pass':passed}}

class ReportMetricsTests(unittest.TestCase):
 def test_request_and_task_denominators(self):
  rows=[record() for _ in range(6)]+[record(passed=False) for _ in range(2)]+[record(ok=False,passed=False) for _ in range(2)]
  result=summarize(rows)['providers']['test']
  self.assertEqual(result['transport_success_rate'],.8)
  self.assertEqual(result['task_pass_rate'],.6)
  self.assertNotIn('conditional_content_pass_rate',result)
 def test_no_requests_does_not_create_conditional_metrics(self):
  self.assertNotIn('conditional_content_pass_rate',summarize([record(ok=False)])['providers']['test'])
 def test_failed_transport_cannot_pass(self):
  self.assertEqual(summarize([record(ok=False)])['providers']['test']['task_pass_rate'],0)
  self.assertFalse(succeeded(record(ok=False),{'task_kind':'body'}))
 def test_cjk_and_markdown(self):
  self.assertEqual(word_overlap('[你好](https://example.com) 世界','你好 世界'),(1,1,1))
  self.assertEqual(word_overlap('你好','你好世界')[1],.5)
 def test_url_echo_is_not_resource_discovery(self):
  r=evaluate_resources('https://example.com/a.png',{},[{'urls':['https://example.com/a.png']}],base_url='https://example.com/a.png')
  self.assertFalse(r['resource_pass'])
 def test_resized_image_same_asset(self):
  r=evaluate_resources('https://images.pexels.com/photos/123/a.jpeg?w=500',{},[{'urls':['https://images.pexels.com/photos/123/a.jpeg?w=1000']}],base_url='https://example.com')
  self.assertTrue(r['resource_pass'])
 def test_generic_query_not_dropped(self):
  r=evaluate_resources('https://example.com/file?id=2',{},[{'urls':['https://example.com/file?id=1']}],base_url='https://example.com')
  self.assertFalse(r['resource_pass'])
 def test_structural_gate(self):
  r=evaluate_content('homepage []',[],required_patterns=[{'id':'posts','pattern':r'/post/\d+','minimum':3}])
  self.assertFalse(r['content_pass'])
 def test_reference_error_terms_are_not_block_pages(self):
  text='HTTP status 404 Not Found means the target resource was not found.'
  r=evaluate_content(text,[],required_facts=[{'alternatives':['target resource']}],forbidden_any=['404 Not Found'],ground_truth_main_content=text)
  self.assertTrue(r['content_pass']);self.assertEqual(r['forbidden_hits'],[])
  self.assertEqual(r['reference_term_exemptions'],['404 Not Found'])
  error=evaluate_content('404 Not Found',[],required_facts=[{'alternatives':['target resource']}],forbidden_any=['404 Not Found'],ground_truth_main_content=text,min_content_chars=30)
  self.assertFalse(error['content_pass'])
  missing_ref=evaluate_content('404 Not Found',[],forbidden_any=['404 Not Found'])
  self.assertFalse(missing_ref['content_pass'])
 def test_inapplicable_not_failure(self):
  attrs=case_attrs(record(),{'category':'image','track':'image','task_kind':'file'})
  self.assertIn('data-content="na"',attrs)
  self.assertIn('data-resources="na"',attrs)
 def test_tables_numbered_and_resource_success(self):
  self.assertIn('<th>序号</th>',table(['x'],[('', '<td>y</td>')]))
  r=record(passed=False);r['metrics']['resource_pass']=True
  self.assertTrue(succeeded(r,{'task_kind':'resource'}))
 def test_denominators_visible_in_ranking(self):
  value=ranking([record(),record(ok=False,passed=False)],{'c':{'track':'web'}},'web')
  self.assertIn('50.0%',value)
  self.assertNotIn('条件通过率',value);self.assertIn('已测任务',value)
  self.assertIn('data-col="3"',value)
  self.assertLess(value.index('本表指标说明'),value.index('<table'))
  self.assertNotIn('(1/2)',value)
 def test_compact_rates(self):
  self.assertEqual(rate(99,100),'99.0%');self.assertEqual(rate(0,0),'—')
 def test_dynamic_ranking_is_not_factual_success(self):
  rows=[record(),record(),record(ok=False)]
  for i,r in enumerate(rows):r['metrics'].update(structure_pass=True,list_task_pass=i==0,word_f1=None)
  value=ranking(rows,{'c':{'track':'challenge','task_kind':'structure'}},'challenge')
  self.assertIn('任务通过率',value);self.assertNotIn('条件达标率',value);self.assertIn('33.3%',value)
  self.assertIn('独立内容证据',value);self.assertNotIn('原规则命中',value)
 def test_zero_facts_are_not_zero_evidence(self):
  self.assertEqual(basis_label({'task_kind':'file'}),'原文件')
  self.assertEqual(basis_label({'expected_resources':[{}]}),'资源 · 1 项')
  self.assertEqual(basis_label({'required_patterns':[{}]}),'列表 1 项')
  self.assertEqual(reference_label({'id':'challenge-03'}),'未提供完整参考')
  self.assertEqual(reference_label({'id':'challenge-04'}),'未提供完整参考')
  self.assertEqual(reference_label({'id':'video-09','track':'video','required_facts':[{},{}]}),'文字信息')
 def test_first_option_and_panel_default(self):
  html=filter_controls([('track','类型',[('web','网页'),('image','图片')])])
  self.assertLess(html.index('value=""'),html.index('value="web"'))
  html=tabs('test',[('web','网页','first'),('image','图片','second')])
  self.assertIn('data-panel="web">first',html)
  self.assertIn('data-panel="image" hidden>second',html)
