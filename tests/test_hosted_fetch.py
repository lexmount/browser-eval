import base64
import hashlib
import json
import tempfile
import unittest
import threading
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from harness.web_fetch.diagnostics import CallDiagnostics
from harness.web_fetch.methods import method_info
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch.providers.hosted_fetch import ENDPOINTS
from harness.web_fetch.runner import run_evaluation
from lexbench_webfetch.metrics import task_passed


TYPES = ('brightdata_unlocker', 'browser_use_fetch', 'browserbase_fetch',
         'deepseek_fetch', 'crawl4ai_cloud')


class HostedFetchTest(unittest.TestCase):
    def setUp(self):
        self.case = EvalCase('article', 'Article', 'https://example.com', 'static', ['Required article text'])

    def provider(self, kind, **config):
        return build_provider(kind, {'type': kind, 'api_key': 'private-test-key', 'zone': 'test-zone', **config})

    def response(self, kind, text='Required article text', status=200):
        payload = ({'body': text, 'status_code': status, 'final_url': self.case.url}
                   if kind == 'browser_use_fetch' else {'markdown': text, 'origin_status': status}
                   if kind == 'crawl4ai_cloud' else {'content': text, 'title': 'Title', 'statusCode': status})
        body = text.encode() if kind == 'brightdata_unlocker' else json.dumps(payload).encode()
        return HttpResponse(200, 'https://service.example/', {}, body)


    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_explicit_service_errors_do_not_require_success_fields(self, call):
        for kind in TYPES[1:]:
            for payload, expected in (({'error': 'blocked'}, 'api_error'),
                                      ({}, 'invalid_response')):
                with self.subTest(kind=kind, payload=payload):
                    raw = json.dumps(payload).encode()
                    call.return_value = HttpResponse(200, ENDPOINTS[kind], {}, raw)
                    result = self.provider(kind).fetch(self.case, 30)
                    self.assertEqual(result.error_type, expected)
                    self.assertEqual(result.content, '')
                    self.assertEqual(base64.b64decode(result.metadata['_raw_response_b64']), raw)

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_brightdata_invalid_utf8_retains_response_evidence(self, call):
        self.case.source_benchmark['input_media_kind'] = 'html_page'
        raw = b'Invalid UTF-8: \xff'
        call.return_value = HttpResponse(200, ENDPOINTS['brightdata_unlocker'], {}, raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = {'id': 'invalid-utf8', 'cases': [vars(self.case)]}
            dataset_path = root / 'input.json'
            dataset_path.write_text(json.dumps(dataset))
            records, _ = run_evaluation(
                output_dir=root, dataset_meta=dataset,
                dataset_path=dataset_path, cases=[self.case],
                providers=[self.provider('brightdata_unlocker', uniform_native_parameters=True)],
                provider_config_path=Path('providers.json'), runs=1,
                timeout=30, delay=0, max_failure_retries=0)
            record = records[0]
            self.assertFalse(record['ok'])
            self.assertEqual(record['error_type'], 'invalid_response')
            self.assertEqual((root / record['artifact_path']).read_bytes(), b'')
            metadata = record['provider_metadata']
            self.assertEqual((root / metadata['raw_response_path']).read_bytes(), raw)
            self.assertEqual(metadata['raw_response_sha256'], hashlib.sha256(raw).hexdigest())
            call.assert_called_once()

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_crawl4ai_cloud_errors_and_projection(self, call):
        from harness.web_fetch.native_projection import require_native_answer_projection
        provider = self.provider('crawl4ai_cloud')
        self.assertEqual(provider.validate(), [])
        for payload, status, expected in [
            ({'markdown':'Native body'}, 200, None),
            ({'markdown':'Native body','origin_status':404}, 200, 'http_error'),
            ({'markdown':'','error':'blocked'}, 403, 'access_denied'),
            ({'content':'Wrong field'}, 200, 'invalid_response'),
            ({'markdown':''}, 200, 'empty_content'),
            ({'markdown':'body','success':False}, 200, 'api_error'),
            ({'markdown':'body','ok':False}, 200, 'api_error'),
            ({'success':False,'error':'blocked'}, 200, 'api_error'),
            ({'ok':False}, 200, 'api_error'),
        ]:
            call.reset_mock()
            raw=json.dumps(payload).encode()
            call.return_value=HttpResponse(status,'https://api.crawl4ai.com/scrape',{},raw)
            result=provider.fetch(self.case,45)
            self.assertEqual(result.error_type,expected)
            self.assertEqual(base64.b64decode(result.metadata['_raw_response_b64']),raw)
            call.assert_called_once()
            require_native_answer_projection(result.content,result.metadata,raw,
                title=result.title,final_url=result.final_url,request_url=self.case.url)
        for option in ({'engine':'invalid'},{'proxy':'invalid'},{'output_format':'html'}):
            self.assertTrue(self.provider('crawl4ai_cloud',**option).validate())
        for field in ('engine', 'proxy'):
            for value in ([], {}, None, True, 1):
                with self.subTest(field=field, value=value):
                    self.assertIn('unsupported ' + field,
                        self.provider('crawl4ai_cloud', **{field: value}).validate())

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_latest_article_track_preserves_native_answer_and_rejects_tampering(self, call):
        from harness.web_fetch.native_projection import require_native_answer_projection
        case = EvalCase('article-new', 'Article', 'https://example.com/article', 'article', [], track='articles')
        for kind in TYPES:
            with self.subTest(kind=kind):
                response = self.response(kind)
                call.return_value = response
                result = self.provider(kind).fetch(case, 45)
                self.assertTrue(result.ok)
                self.assertEqual(result.content, 'Required article text')
                if kind != 'brightdata_unlocker':
                    args = dict(title=result.title, final_url=result.final_url, request_url=case.url)
                    require_native_answer_projection(result.content, result.metadata, response.body, **args)
                    with self.assertRaises(ValueError):
                        require_native_answer_projection('Changed answer', result.metadata, response.body, **args)

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_routes_and_preserved_evidence(self, call):
        for kind in TYPES:
            with self.subTest(kind=kind):
                call.reset_mock()
                response = self.response(kind)
                call.return_value = response
                result = self.provider(kind).fetch(self.case, 45)
                self.assertTrue(result.ok)
                self.assertEqual(result.content, 'Required article text')
                self.assertEqual(base64.b64decode(result.metadata['_raw_response_b64']), response.body)
                self.assertNotIn('private-test-key', json.dumps(result.metadata))
                self.assertNotEqual(method_info(kind)['contract'], '未知')
                call.assert_called_once()
                body = call.call_args.kwargs['json_body']
                self.assertEqual(body['url'], self.case.url)
                self.assertEqual(call.call_args.kwargs['timeout'], 45)
                self.assertEqual(result.metadata['request_timeout_seconds'], 45)
                self.assertEqual(result.metadata['dns_mode'], 'system')
                self.assertEqual(result.metadata['dns_trace'], [])
                self.assertEqual(result.metadata['service_retry_policy'], 'provider_default_not_overridden')
                if kind == 'brightdata_unlocker':
                    self.assertEqual(body['data_format'], 'markdown')
                    self.assertEqual(body['format'], 'raw')
                    self.assertIsNone(result.status_code)
                elif kind == 'browser_use_fetch':
                    self.assertNotIn('retry', body)
                    self.assertNotIn('timeout_ms', body)
                    self.assertEqual(body['output_format'], 'markdown')
                    session = body['session_id']
                    self.provider(kind).fetch(self.case, 45)
                    self.assertNotEqual(session, call.call_args.kwargs['json_body']['session_id'])
                elif kind == 'browserbase_fetch':
                    self.assertTrue(body['allowRedirects'])
                    self.assertTrue(body['proxies'])
                    self.assertNotIn('projectId', body)
                elif kind == 'deepseek_fetch':
                    self.assertEqual(body, {'url': self.case.url})
                    self.assertEqual(result.title, 'Title')
                    self.assertIsNone(result.status_code)
                    self.assertEqual(call.call_args.kwargs['headers'], {'Authorization': 'Bearer private-test-key'})
                else:
                    self.assertEqual(body, {'url': self.case.url, 'format': 'md', 'engine': 'browser', 'proxy': 'none'})

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_brightdata_native_render_option_is_explicit(self, call):
        call.return_value = self.response('brightdata_unlocker')
        for value in (None, True, False):
            config = {} if value is None else {'render': value}
            result = self.provider('brightdata_unlocker', **config).fetch(self.case, 45)
            body = call.call_args.kwargs['json_body']
            self.assertEqual(body.get('render'), None if value is None else str(value).lower())
            self.assertEqual(result.metadata['render'], value)
            self.assertNotIn('wait', body)
            self.assertNotIn('selector', body)
        self.assertIn('render must be boolean', self.provider('brightdata_unlocker', render='true').validate())

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_only_brightdata_receives_documented_native_debug_flag(self, call):
        for kind in TYPES:
            call.return_value = self.response(kind)
            self.provider(kind, log_level='debug', debug=True).fetch(self.case, 45)
            body = call.call_args.kwargs['json_body']
            if kind == 'brightdata_unlocker':
                self.assertIs(body['debug'], True)
            else:
                self.assertNotIn('debug', body)
        self.assertIn('debug must be boolean', self.provider(TYPES[0], debug='true').validate())

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_unsupported_output_formats_are_rejected_before_transport(self, call):
        for kind, output in (('browserbase_fetch', 'json'), ('browser_use_fetch', 'structured')):
            provider = self.provider(kind, output_format=output)
            self.assertTrue(provider.validate())
            with self.assertRaisesRegex(ValueError, 'requires markdown'):
                provider.fetch(self.case, 30)
        call.assert_not_called()

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_http_200_is_not_enough(self, call):
        for kind in TYPES:
            call.return_value = self.response(kind, '')
            result = self.provider(kind).fetch(self.case, 45)
            self.assertFalse(result.ok)
            self.assertEqual(result.error_type, 'empty_content')
        for kind in TYPES[1:3]:
            call.return_value = self.response(kind, 'Required article text', 403)
            self.assertEqual(self.provider(kind).fetch(self.case, 45).error_type, 'http_error')
            for raw in (b'not json', b'{"message":"Required article text"}',
                        b'{"content":{},"statusCode":200}'):
                call.return_value = HttpResponse(200, 'https://service.example/', {}, raw)
                self.assertFalse(self.provider(kind).fetch(self.case, 45).ok)
        for kind, payload in (
            ('browser_use_fetch', {'body':'Required article text','status_code':200,'error':'failed'}),
            ('browserbase_fetch', {'content':'Required article text','statusCode':200,'encoding':'base64'}),
        ):
            call.return_value = HttpResponse(200, 'https://service.example/', {}, json.dumps(payload).encode())
            self.assertFalse(self.provider(kind).fetch(self.case, 45).ok)

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_api_errors_are_separate_from_content(self, call):
        for kind in TYPES:
            for status, label in ((401,'authentication'), (402,'quota'), (429,'rate_limit'), (500,'api_error')):
                call.return_value = HttpResponse(status, 'https://service.example/', {}, b'{"error":{"message":"actual error"}}')
                result = self.provider(kind).fetch(self.case, 45)
                self.assertFalse(result.ok)
                self.assertEqual(result.content, '')
                self.assertEqual(result.error_type, label)
                if kind == 'deepseek_fetch':
                    self.assertEqual(result.metadata['service_error']['message'], 'actual error')

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    def test_unsupported_delivery_never_calls_service(self, call):
        case = EvalCase('file', 'File', 'https://example.com/file.pdf', 'document', [], track='document', task_kind='file')
        for kind in TYPES:
            self.assertEqual(self.provider(kind).fetch(case, 45).error_type, 'unsupported_task')
            self.assertIn('api_key', build_provider(kind, {'type':kind}).validate())
        call.assert_not_called()

    @patch('harness.web_fetch.providers.hosted_fetch.request')
    @patch('harness.web_fetch.network_dns.lookup_public_dns')
    def test_single_request_without_dns_fallback_or_task_assistance(self, lookup, call):
        self.case.retrieval_hints.update(version=1, completion={
            'selector':'#answer', 'minimum_count':10, 'timeout_ms':30000})
        for kind in TYPES:
            with self.subTest(kind=kind):
                call.reset_mock()
                call.return_value = HttpResponse(503, 'https://service.example/', {}, b'Unavailable')
                result = self.provider(kind).fetch(self.case, 45)
                self.assertFalse(result.ok)
                call.assert_called_once()
                body = call.call_args.kwargs['json_body']
                self.assertNotIn('#answer', json.dumps(body))
                self.assertNotIn('retry', body)
                self.assertFalse(result.metadata['retrieval_hints_applied'])
        lookup.assert_not_called()

    @patch('harness.web_fetch.providers.http_client.urllib.request.build_opener')
    def test_runner_scores_and_saves_hosted_methods(self, call):
        def respond(request, **kwargs):
            kind = next(kind for kind in TYPES if ENDPOINTS[kind] == request.full_url)
            response = self.response(kind)
            return Mock(status=response.status, headers={'x-brd-debug':'req_id=test-native-id; render=true'},
                        read=Mock(return_value=response.body), geturl=Mock(return_value=response.url))
        call.return_value.open.side_effect = respond
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root/'sources/article/reference.txt'
            reference.parent.mkdir(parents=True)
            reference.write_text('Required article text')
            self.case.source_benchmark.update(stable_annotation={
                'anchor_evidence': 'sources/article/reference.txt',
                'anchor_evidence_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
            })
            self.case.acceptance_contract.update(version=1, schema='body_text', minimum_records=0,
                stable_text_anchors=[{'id':'article','alternatives':['Required article text']}])
            dataset = {'tool':'web_fetch','id':'hosted-test','name':'Test','cases':[vars(self.case)],
                       'coverage_plan':{'web':list(TYPES)},
                       'comparison':{'evidence_state':'diagnostic','kind':'web_fetch_diagnostic'}}
            dataset_path = root/'dataset.json'
            dataset_path.write_text(json.dumps(dataset))
            config_path = root/'providers.json'
            config_path.write_text('{}')
            records, _ = run_evaluation(output_dir=root/'run', dataset_meta=dataset,
                dataset_path=dataset_path, cases=[self.case], providers=[self.provider(k, log_level='debug') for k in TYPES],
                provider_config_path=config_path, runs=1, timeout=45, delay=0, provider_concurrency=1,max_failure_retries=0)
            self.assertEqual(len(records), len(TYPES))
            self.assertTrue(all(task_passed(r['ok'],r['metrics'],'body') for r in records))
            for record in records:
                self.assertTrue((root/'run'/record['provider_metadata']['raw_response_path']).is_file())
                log_path = root/'run'/record['provider_metadata']['diagnostic_log_path']
                raw_log = log_path.read_bytes()
                self.assertEqual(hashlib.sha256(raw_log).hexdigest(), record['provider_metadata']['diagnostic_log_sha256'])
                self.assertNotIn(b'private-test-key', raw_log)
                for event in ('http_request_started', 'http_response_received', 'collection_completed'):
                    self.assertIn(event, raw_log.decode())
                self.assertIn('req_id=test-native-id', raw_log.decode())
            # Reporting must work from the saved run after original references
            # disappear; it must never need another provider call.
            reference.unlink()
            from harness.web_fetch.report_build import build_report
            report = build_report(root/'run', root/'run/report', diagnostic_preview=True, language='en')
            self.assertTrue(report.is_file())
            self.assertEqual(call.call_count, len(TYPES))

    def test_diagnostics_redact_new_auth_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'log.jsonl'
            log = CallDiagnostics(path, 'test')
            log.emit('caller','request',headers={'X-BB-API-Key':'secret-bb','X-Browser-Use-API-Key':'secret-bu'})
            log.file.close()
            self.assertNotIn('secret-bb',path.read_text())
            self.assertNotIn('secret-bu',path.read_text())

    @patch('harness.web_fetch.providers.http_client.urllib.request.build_opener')
    def test_runner_retains_http_errors_and_exception_trace(self, opener):
        responses = [Mock(status=200, headers={}, read=Mock(return_value=b'{"title":"Title","content":"Body"}'),
                          geturl=Mock(return_value='https://service.example/')),
                     Mock(status=500, headers={}, read=Mock(return_value=b'{"error":{"message":"native failure"}}'),
                          geturl=Mock(return_value='https://service.example/')),
                     urllib.error.URLError('synthetic DNS failure')]
        opener.return_value.open.side_effect = responses
        cases = [EvalCase(str(i),'Control',self.case.url,'static',[]) for i in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows,_=run_evaluation(output_dir=root/'run',dataset_meta={'cases':[vars(c) for c in cases]},
                dataset_path=root/'dataset.json', cases=cases,
                providers=[self.provider('deepseek_fetch',log_level='debug')],provider_config_path=root/'providers.json',
                runs=1,timeout=45,delay=0,max_failure_retries=0)
            self.assertEqual([r['ok'] for r in rows],[True,False,False])
            self.assertEqual(opener.return_value.open.call_count,3)
            for row in rows:
                log=(root/'run'/row['provider_metadata']['diagnostic_log_path']).read_text()
                self.assertNotIn('private-test-key',log)
                self.assertIn('http_request_started',log)
            failure_log=(root/'run'/rows[-1]['provider_metadata']['diagnostic_log_path']).read_text()
            self.assertIn('synthetic DNS failure',failure_log)
            self.assertIn('Traceback',failure_log)
            server_log=(root/'run'/rows[1]['provider_metadata']['diagnostic_log_path']).read_text()
            self.assertIn('native failure',server_log)

    def test_historical_unscored_collection_cannot_build_report(self):
        from harness.web_fetch.report_build import build_report
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'manifest.json').write_text('{"collection_only":true}')
            with self.assertRaisesRegex(ValueError, 'Collection-only evidence'):
                build_report(root, root/'report')

    @patch('harness.web_fetch.providers.http_client.urllib.request.build_opener')
    def test_runner_timeout_has_actual_deadline_event_and_no_retry(self, opener):
        release=threading.Event()
        def blocked(*args,**kwargs):
            release.wait(1)
            raise TimeoutError('simulated socket timeout')
        opener.return_value.open.side_effect=blocked
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                rows,_=run_evaluation(output_dir=root/'run',dataset_meta={'cases':[vars(self.case)]},
                    dataset_path=root/'dataset.json',cases=[self.case],
                    providers=[self.provider('deepseek_fetch',log_level='debug')],provider_config_path=root/'providers.json',
                    runs=1,timeout=0.02,delay=0,max_failure_retries=0)
                self.assertEqual(rows[0]['error_type'],'timeout')
                log=(root/'run'/rows[0]['provider_metadata']['diagnostic_log_path']).read_text()
                self.assertIn('answer_timeout',log)
                self.assertIn('End-to-end answer deadline exceeded',log)
                self.assertEqual(opener.return_value.open.call_count,1)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
