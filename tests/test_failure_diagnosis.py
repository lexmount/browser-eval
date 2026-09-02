import json,unittest
from browser_harness_eval.failure_diagnosis import diagnose,normalize_empty_response

class FailureDiagnosisTests(unittest.TestCase):
 def record(self,**fields):return {'provider':'test','ok':False,'status_code':502,'metrics':{},'provider_metadata':{},**fields}
 def test_nested_upstream_not_generic_502(self):
  r=self.record();d=diagnose(r,{},json.dumps({'error':{'upstream_status_code':403,'message':'unexpected status code: 403'}}))
  self.assertEqual(d['category'],'源站拒绝该次访问')
 def test_empty_api_success_is_extraction_failure(self):
  r=self.record(provider='cloudflare-markdown',status_code=200);raw='{"success":true,"result":""}'
  normalize_empty_response(r,raw);self.assertTrue(r['ok']);self.assertEqual(diagnose(r,{},raw)['category'],'正文提取为空')
 def test_unknown_remote_internals_remain_unknown(self):
  d=diagnose(self.record(error_type='CRAWL_UNKNOWN_ERROR'),{},'')
  self.assertEqual(d['category'],'服务取页失败');self.assertIn('未披露',d['certainty'])
 def test_timeout_option_name_is_not_timeout_error(self):
  raw=json.dumps({'errors':[{'message':'Execution context was destroyed. Check waitForTimeout options.'}]})
  self.assertEqual(diagnose(self.record(),{},raw)['category'],'浏览器执行上下文中断')
  self.assertEqual(diagnose(self.record(),{},'Check waitForTimeout options.')['category'],'服务取页失败')
  self.assertEqual(diagnose(self.record(error_type='request_timeout'),{},'')['category'],'加载 / 提取超时')
 def test_capability_and_security_separated(self):
  for code,expected in [('WEB_REDIRECT_BLOCKED','方法禁止跨站跳转'),('WEB_UNSUPPORTED_CONTENT_TYPE','不支持输入类型'),('WEB_BLOCKED_URL','方法安全限制')]:
   self.assertEqual(diagnose(self.record(error_type=code),{},'')['category'],expected)
