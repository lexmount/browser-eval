import json,unittest
from copy import deepcopy
from harness.web_fetch.failure_diagnosis import diagnose
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.response_input import apply_request_status
from harness.web_fetch.metrics import evaluate_content
from harness.web_fetch.stable_tasks import evaluate_stable_task

class FailureDiagnosisTests(unittest.TestCase):
 def record(self,**fields):return {'provider':'test','ok':False,'status_code':502,'metrics':{},'provider_metadata':{},**fields}
 def test_nested_upstream_not_generic_502(self):
  r=self.record();d=diagnose(r,{},json.dumps({'error':{'upstream_status_code':403,'message':'unexpected status code: 403'}}))
  self.assertEqual(d['category'],'源站拒绝该次访问')
 def test_empty_api_success_is_extraction_failure(self):
  r=self.record(provider='cloudflare-markdown',status_code=200);raw='{"success":true,"result":""}'
  r['metrics']=score_attempt({'url':'https://example.com'},raw.encode(),ok=False,status_code=200)
  apply_request_status(r);self.assertTrue(r['ok']);self.assertEqual(diagnose(r,{},raw)['category'],'正文提取为空')
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
 def test_entry_binding_failure_does_not_claim_every_field_absent(self):
  r=self.record(ok=True,status_code=200,metrics={'content_pass':False,'length_pass':True,
    'fact_results':[{'id':'entry-1-price','pass':False,'match_scope':'entry'}]})
  d=diagnose(r,{'track':'dynamic','task_kind':'body'},'A product and some text')
  self.assertEqual(d['category'],'条目未完整匹配')
  self.assertIn('不表示每个字段都不存在',d['evidence'])


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

    def test_blocked_page_with_enough_links_is_not_record_shortfall(self):
        contract = self.contract(page_patterns=['Catalog'], stable_text_anchors=[
            {'id': 'heading', 'alternatives': ['Browse products']}])
        metrics = evaluate_stable_task('Access denied\n' + self.links, contract, self.base_url)
        self.assertTrue(metrics['stable_structure_pass'])
        self.assertEqual(metrics['stable_unique_record_count'], 3)
        self.assertTrue(metrics['stable_blocked_page'])
        self.assertIn('blocked_page', metrics['stable_task_errors'])
        self.assertNotIn('too_few_complete_records', metrics['stable_task_errors'])
        result = self.assert_category(metrics, '返回非目标内容', acceptance_contract=contract)
        self.assertIn('blocked_page', result['evidence'])
        self.assertNotIn('条目不足', result['evidence'])
        # Both persisted representations are understood independently.
        without_flag = deepcopy(metrics)
        without_flag.pop('stable_blocked_page')
        self.assert_category(without_flag, '返回非目标内容')
        without_code = deepcopy(metrics)
        without_code['stable_task_errors'].remove('blocked_page')
        self.assert_category(without_code, '返回非目标内容')

    def test_forbidden_and_blocked_page_have_same_semantics(self):
        metrics = evaluate_content('Access denied', [], forbidden_any=['Access denied'])
        self.assert_category(metrics, '返回非目标内容')
        stable = evaluate_content('Access denied\n' + self.links, [],
            forbidden_any=['Access denied'], acceptance_contract=self.contract(), base_url=self.base_url)
        self.assert_category(stable, '返回非目标内容')
        self.assert_category({'stable_task_pass': False, 'stable_task_errors': ['forbidden_content']},
                             '返回非目标内容')

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
        from harness.web_fetch.dynamic_lists import evaluate_dynamic_list
        for track in self.tracks:
            legacy = evaluate_dynamic_list('[Alpha](/item/1)',
                {'hosts': ['example.test'], 'entry_path': r'/item/\d+', 'minimum_entries': 5}, self.base_url)
            result = diagnose(self.record(legacy), {'track': track, 'task_kind': 'structure'}, '')
            self.assertEqual(result['category'], '有效条目不足')
            self.assertIn('至少需要 5 条', result['evidence'])

    def test_duplicate_and_incomplete_records_keep_distinct_labels(self):
        contract = dict(version=1, schema='product', minimum_records=2)
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
