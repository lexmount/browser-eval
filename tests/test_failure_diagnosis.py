import hashlib,json,unittest
from copy import deepcopy
from harness.web_fetch.failure_diagnosis import diagnose
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.response_input import apply_request_status
from lexbench_webfetch.metrics import evaluate_content
from lexbench_webfetch.stable_tasks import evaluate_stable_task

class FailureDiagnosisTests(unittest.TestCase):
 def record(self,**fields):return {'provider':'test','ok':False,'status_code':502,'metrics':{},'provider_metadata':{},**fields}
 def test_nested_upstream_not_generic_502(self):
  r=self.record();d=diagnose(r,{},json.dumps({'error':{'upstream_status_code':403,'message':'unexpected status code: 403'}}))
  self.assertEqual(d['category'],'源站拒绝该次访问')
 def test_platform_regional_capability_restriction_is_not_origin_rejection(self):
  raw=json.dumps({'error':{'upstream_status_code':403,'message':
   'Use of headers, actions, and the FIRE-1 agent is not allowed by default in your country'}})
  d=diagnose(self.record(error_type='CRAWL_HTTP_403'),{},raw)
  self.assertEqual(d['category'],'平台调用能力受限')
  self.assertEqual(d['stage'],'服务接口')
  self.assertIn('不据此归责源站',d['certainty'])
 def test_platform_capability_rule_does_not_swallow_ordinary_upstream_403(self):
  raw=json.dumps({'error':{'upstream_status_code':403,'message':'unexpected status code: 403'}})
  d=diagnose(self.record(error_type='CRAWL_HTTP_403'),{},raw)
  self.assertEqual(d['category'],'源站拒绝该次访问')
 def test_empty_api_success_is_extraction_failure(self):
  r=self.record(provider='cloudflare-markdown',ok=True,status_code=200);raw='{"success":true,"result":""}'
  r['provider_metadata']={'raw_response_sha256':hashlib.sha256(raw.encode()).hexdigest(),
   'native_answer_projection':{'schema_version':2,'content_pointer':['result'],'content_format':'text'}}
  r['metrics']=score_attempt({'url':'https://example.com'},raw.encode(),ok=True,status_code=200,metadata=r['provider_metadata'])
  apply_request_status(r);self.assertTrue(r['ok']);self.assertEqual(diagnose(r,{},raw)['category'],'正文提取为空')
 def test_unknown_remote_internals_remain_unknown(self):
  d=diagnose(self.record(error_type='CRAWL_UNKNOWN_ERROR'),{},'')
  self.assertEqual(d['category'],'服务取页失败');self.assertIn('未披露',d['certainty'])
 def test_timeout_option_name_is_not_timeout_error(self):
  raw=json.dumps({'errors':[{'message':'Execution context was destroyed. Check waitForTimeout options.'}]})
  self.assertEqual(diagnose(self.record(),{},raw)['category'],'浏览器执行上下文中断')
  self.assertEqual(diagnose(self.record(),{},'Check waitForTimeout options.')['category'],'服务取页失败')
  self.assertEqual(diagnose(self.record(error_type='request_timeout'),{},'')['category'],'加载 / 提取超时')
 def test_explicit_output_rejection_precedes_incidental_timeout_configuration(self):
  for debug in ['timeout_ms=40500','request_timeout_ms=45000','timeout=45000','"timeout": 45000']:
   message='DEBUG waiting '+debug+' stage=Load\nError: failed to fetch\nReason: raw download output only supports automatic output or --dump json'
   row=self.record(error_type='tool_error',error_message=message)
   before=deepcopy(row)
   result=diagnose(row,{'task_kind':'body'},'')
   self.assertEqual(result['category'],'不支持所需输出格式')
   self.assertEqual(result['stage'],'内容交付')
   self.assertIn('raw download output only supports',result['evidence'])
   self.assertNotIn(debug,result['evidence'])
   self.assertEqual(row,before)
 def test_debug_timeout_fields_do_not_establish_a_timeout(self):
  for debug in ['timeout_ms=40500','request_timeout_ms=45000','timeout=45000','"timeout": 45000']:
   self.assertEqual(diagnose(self.record(error_type='tool_error',error_message=debug),{},'')['category'],'服务取页失败')
  for message in ['request timed out','Timeout 45000ms exceeded','deadline exceeded']:
   self.assertEqual(diagnose(self.record(error_type='tool_error',error_message=message),{},'')['category'],'加载 / 提取超时')
  self.assertEqual(diagnose(self.record(error_type='request_timeout'),{},'')['category'],'加载 / 提取超时')

 def test_capability_and_security_separated(self):
  for code,expected in [('WEB_REDIRECT_BLOCKED','方法禁止跨站跳转'),('WEB_UNSUPPORTED_CONTENT_TYPE','不支持输入类型'),('WEB_BLOCKED_URL','方法安全限制')]:
   self.assertEqual(diagnose(self.record(error_type=code),{},'')['category'],expected)
 def test_entry_binding_failure_does_not_claim_every_field_absent(self):
  r=self.record(ok=True,status_code=200,metrics={'content_pass':False,'length_pass':True,
    'fact_results':[{'id':'entry-1-price','pass':False,'match_scope':'entry'}]})
  d=diagnose(r,{'track':'dynamic','task_kind':'body'},'A product and some text')
  self.assertEqual(d['category'],'条目未完整匹配')
  self.assertIn('不表示每个字段都不存在',d['evidence'])
 def test_declared_body_truncation_has_specific_failure(self):
  r=self.record(ok=True,status_code=200,metrics={
   'content_pass':False,
   'declared_truncation_hits':['(Content truncated. Fetch a more specific URL or section for the full text.)'],
  })
  d=diagnose(r,{'task_kind':'body'},'')
  self.assertEqual(d['category'],'正文范围不完整')
  self.assertIn('明确声明',d['evidence'])
 def test_target_only_resource_extras_have_specific_failure(self):
  r=self.record(ok=True,status_code=200,metrics={
   'resource_pass':False,
   'resource_results':[{'id':'hero','pass':True}],
   'unexpected_resource_urls':['https://example.test/logo.png'],
  })
  d=diagnose(r,{'task_kind':'resource'},'')
  self.assertEqual(d['category'],'返回非目标资源')
  self.assertIn('logo.png',d['evidence'])
 def test_missing_target_with_extra_does_not_claim_target_hit(self):
  r=self.record(ok=True,status_code=200,metrics={
   'resource_pass':False,
   'resource_results':[{'id':'hero','pass':False}],
   'unexpected_resource_urls':['https://example.test/logo.png'],
  })
  d=diagnose(r,{'task_kind':'resource'},'')
  self.assertEqual(d['category'],'缺少目标资源')
  self.assertIn('hero',d['evidence'])
  self.assertIn('logo.png',d['evidence'])
  self.assertNotIn('目标资源已命中',d['evidence'])


class UnifiedTextFailureTests(unittest.TestCase):
    tracks = ('web', 'dynamic', 'document', 'challenge')
    links = '[Alpha](/item/1)\n[Beta](/item/2)\n[Gamma](/item/3)'
    base_url = 'https://example.test/catalog'

    def contract(self, **extra):
        return dict(version=1, schema='named_link', minimum_records=3,
                    hosts=['example.test'], entry_path=r'/item/\d+', **extra)

    def record(self, metrics, **extra):
        return dict(provider='test', case_id='case', ok=True, status_code=200,
                    provider_metadata={}, metrics=metrics, **extra)

    def assert_category(self, metrics, expected, **case_fields):
        for track in self.tracks:
            for kind in ('body', 'structure') if 'stable_task_pass' in metrics else ('body',):
                with self.subTest(track=track, kind=kind, expected=expected):
                    case = dict(track=track, task_kind=kind, **case_fields)
                    record = self.record(metrics)
                    before = deepcopy((record, case))
                    result = diagnose(record, case, '')
                    self.assertEqual(result['category'], expected)
                    self.assertEqual(result['stage'], '内容核验')
                    self.assertEqual((record, case), before)
        return result

    def test_prompt_diagnostics_do_not_replace_missing_target_reason(self):
        contract = self.contract(page_patterns=['Catalog'], stable_text_anchors=[
            {'id': 'heading', 'alternatives': ['Browse products']}])
        metrics = evaluate_stable_task('Access denied\n' + self.links, contract, self.base_url)
        self.assertTrue(metrics['stable_structure_pass'])
        self.assertTrue(metrics['stable_blocked_page'])
        self.assertNotIn('blocked_page', metrics['stable_task_errors'])
        result = diagnose(self.record(metrics), {'task_kind': 'body', 'acceptance_contract': contract}, '')
        self.assertIsNotNone(result)
        self.assertNotIn('blocked_page', result['evidence'])

    def test_qualified_content_with_diagnostic_hint_has_no_failure(self):
        facts = [{'id': 'target', 'alternatives': ['Required target content']}]
        metrics = evaluate_content('Required target content\nAccess denied', [], required_facts=facts,
            diagnostic_any=['Access denied'])
        self.assertEqual(metrics['diagnostic_hits'], ['Access denied'])
        self.assertIsNone(diagnose(self.record(metrics), {'task_kind': 'body'}, ''))
        missing = evaluate_content('Access denied', [], required_facts=facts, diagnostic_any=['Access denied'])
        self.assertFalse(missing['content_pass'])
        self.assertIsNotNone(diagnose(self.record(missing), {'task_kind': 'body'}, ''))

    def test_prompt_diagnostics_do_not_veto_delivered_resource(self):
        for heading in ("# Making sure you're not a bot!", '# 验证中心'):
            metrics = evaluate_content(heading, [])
            metrics.update(resource_pass=True, resource_results=[{'id': 'hero', 'pass': True}])
            self.assertTrue(metrics['blocked_page'])
            self.assertIsNone(diagnose(self.record(metrics), {'task_kind': 'resource'}, ''))

    def test_too_many_records_reports_upper_bound_not_too_few(self):
        contract = self.contract(maximum_records=3)
        metrics = evaluate_stable_task(self.links + '\n[Delta](/item/4)', contract, self.base_url)
        self.assertEqual(metrics['stable_task_errors'], ['too_many_records'])
        self.assertEqual(metrics['stable_unique_record_count'], 4)
        result = self.assert_category(metrics, '有效条目过多', acceptance_contract=contract)
        self.assertIn('4 条', result['evidence'])
        self.assertIn('最多允许 3 条', result['evidence'])
        self.assertNotIn('不足', result['evidence'])

    def test_stable_anchor_and_ordinary_fact_missing_share_label(self):
        anchor = {'id': 'heading', 'alternatives': ['Browse products']}
        contract = self.contract(stable_text_anchors=[anchor])
        stable = evaluate_stable_task(self.links, contract, self.base_url)
        body = evaluate_content(self.links, [], required_facts=[anchor])
        self.assertEqual(stable['stable_task_errors'], ['missing_stable_anchor:heading'])
        for metrics in (stable, body):
            result = self.assert_category(metrics, '缺少关键信息')
            self.assertIn('heading', result['evidence'])
        identity = evaluate_stable_task(self.links, self.contract(page_patterns=['Catalog']), self.base_url)
        self.assert_category(identity, '缺少关键信息')
        marker = evaluate_content('Other description', ['Browse products'])
        self.assert_category(marker, '缺少关键信息')

    def test_matched_entry_fact_does_not_mask_missing_body_fact(self):
        metrics = {'content_pass': False, 'length_pass': True, 'fact_results': [
            {'id': 'entry', 'pass': True, 'match_scope': 'entry'},
            {'id': 'heading', 'pass': False}]}
        result = self.assert_category(metrics, '缺少关键信息')
        self.assertIn('heading', result['evidence'])

    def test_record_shortfall_uses_real_counts_for_stable_and_legacy_tasks(self):
        stable = evaluate_stable_task('[Alpha](/item/1)', self.contract(), self.base_url)
        self.assert_category(stable, '有效条目不足')
        body = evaluate_content('entry-1', [], required_patterns=[
            {'id': 'entries', 'pattern': r'entry-\d+', 'minimum': 3}])
        self.assert_category(body, '有效条目不足')
        from lexbench_webfetch.dynamic_lists import evaluate_dynamic_list
        for track in self.tracks:
            legacy = evaluate_dynamic_list('[Alpha](/item/1)',
                {'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'minimum_entries': 5}, self.base_url)
            result = diagnose(self.record(legacy), {'track': track, 'task_kind': 'structure'}, '')
            self.assertEqual(result['category'], '有效条目不足')
            self.assertIn('至少需要 5 条', result['evidence'])

    def test_duplicate_and_incomplete_records_keep_distinct_labels(self):
        # A finite scope makes a malformed table row a required record. An
        # open-ended at-least-N contract intentionally treats extra incomplete
        # candidates as parse noise and reports only a complete-record shortfall.
        contract = dict(version=1, schema='product', minimum_records=2, maximum_records=2)
        for content, expected, error in (
            ('Alpha\n$10\n\nAlpha\n$10', '条目重复', 'duplicate_records'),
            ('| Name | Price |\n| --- | --- |\n| Alpha | $10 |\n| Beta | invalid |',
             '字段格式或记录不完整', 'incomplete_records')):
            metrics = evaluate_stable_task(content, contract)
            self.assertIn(error, metrics['stable_task_errors'])
            self.assert_category(metrics, expected)
        malformed = evaluate_stable_task('[Broken](https://[invalid/item/1)', self.contract(), self.base_url)
        self.assertIn('invalid_link_output', malformed['stable_task_errors'])
        self.assert_category(malformed, '字段格式或记录不完整')

    def test_unrecognized_stable_error_does_not_invent_record_shortfall(self):
        for errors in ([], ['future_contract_rule'], ['request_ineligible']):
            result = self.assert_category({'stable_task_pass': False, 'stable_task_errors': errors},
                                         '任务规则未满足')
            self.assertNotIn('不足', result['evidence'])
            self.assertNotIn('记录 0', result['evidence'])

    def test_legacy_enough_links_with_missing_fields_is_not_shortfall(self):
        for track in self.tracks:
            for coverage, expected in ((0.5, '字段格式或记录不完整'), (1, '内容结构不完整')):
                metrics = dict(named_entry_count=5, minimum_entries=5,
                               structure_pass=False, list_field_coverage=coverage)
                result = diagnose(self.record(metrics), {'track': track, 'task_kind': 'structure'}, '')
                self.assertEqual(result['category'], expected)

    def test_request_error_priority_is_unchanged_across_text_tracks(self):
        metrics = evaluate_stable_task('Access denied\n' + self.links, self.contract(), self.base_url)
        for track in self.tracks:
            for error, status, expected in (
                ('authentication', 403, '接口鉴权失败'),
                ('rate_limit', 429, '限流 / 配额'),
                ('request_timeout', 504, '加载 / 提取超时'),
                ('CRAWL_HTTP_403', 502, '源站拒绝该次访问'),
                ('WEB_REDIRECT_BLOCKED', 502, '方法禁止跨站跳转'),
                ('dns', 502, '域名解析失败'),
                ('provider_exception', 502, '本地适配器执行失败')):
                with self.subTest(track=track, error=error):
                    record = self.record(metrics)
                    record.update(ok=False, status_code=status, error_type=error)
                    result = diagnose(record, {'track': track, 'acceptance_contract': self.contract()}, '')
                    self.assertEqual(result['category'], expected)

    def test_filters_deduplicate_normalized_labels_and_keep_pass_fail_partition(self):
        from harness.web_fetch.report import case_attrs, failure_category, failure_filters, filter_controls
        anchor = {'id': 'heading', 'alternatives': ['Browse products']}
        stable = evaluate_content(self.links, [], acceptance_contract=self.contract(stable_text_anchors=[anchor]),
                                  base_url=self.base_url)
        ordinary = evaluate_content(self.links, [], required_facts=[anchor])
        passed = evaluate_content('Browse products\n' + self.links, [],
            acceptance_contract=self.contract(stable_text_anchors=[anchor]), base_url=self.base_url)
        rows, cases = [], {}
        for track in self.tracks:
            case = dict(category='test', track=track, task_kind='body')
            cases[track] = case
            for metrics in (stable, ordinary, passed):
                record = self.record(deepcopy(metrics))
                record['case_id'] = track
                before = deepcopy(record)
                rows.append(record)
                category = failure_category(record, case)
                record['diagnosis'] = diagnose(record, case, '')
                self.assertEqual(failure_category(record, case), category)
                self.assertEqual(record['metrics'], before['metrics'])
                attrs = case_attrs(record, case)
                if metrics['content_pass']:
                    self.assertIsNone(record['diagnosis'])
                    self.assertIn('data-result="pass"', attrs)
                    self.assertIn('data-failure-category=""', attrs)
                else:
                    self.assertIn('data-result="fail"', attrs)
                    self.assertIn('data-failure-stage="task"', attrs)
                    self.assertIn('data-failure-category="缺少关键信息"', attrs)
        filters = failure_filters(rows, cases)
        self.assertEqual(filters[1][2], [('缺少关键信息', '缺少关键信息')])
        html = filter_controls([('result', '结果', [('pass', '任务通过'), ('fail', '任务失败')])] + filters)
        self.assertIn('value="fail" selected', html)
        for key in ('failureStage', 'failureCategory'):
            self.assertRegex(html, 'data-filter="' + key + '"[^>]*><option value="">全部</option>')

    def test_passed_stable_task_ignores_legacy_failure_metrics(self):
        metrics = evaluate_stable_task(self.links, self.contract(), self.base_url)
        metrics.update(content_pass=False, length_pass=False, list_task_pass=False)
        for track in self.tracks:
            self.assertIsNone(diagnose(self.record(metrics), {'track': track, 'task_kind': 'body'}, ''))
