"""Browser-facing controls for shared output scope and forbidden answer repair."""
import json
import hashlib
import os
import threading
import unittest
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers.moli import MoliWebFetchProvider
from harness.web_fetch.providers.headless_worker import fetch


class NativeTaskOutputTests(unittest.TestCase):
    def test_resource_discovery_reads_dom_without_mining_unrendered_modules(self):
        engines = {name: os.environ.get(name.upper() + '_BINARY')
                   for name in ('moli', 'lightpanda', 'obscura', 'chromium')}
        engines = {name: binary for name, binary in engines.items() if binary}
        if not engines:
            self.skipTest('set native browser binary variables for actual controls')
        calls = Counter()
        page = b'''<!doctype html><title>Resource fixture</title>
        <img src="/visible.png" srcset="/small.png 1x, /large.png 2x">
        <img src="/image/a,b.png"><img src="/image/thumb.png?crop=10,20">
        <img srcset="/set/a,b.png 1x, /set/thumb.png?crop=10,20 2x">
        <video src="/visible.mp4"></video>
        <script type="module" src="/module.js"></script>'''
        hidden = 'https://answer-repair.invalid/unrendered.mp4'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls[self.path] += 1
                body = page if self.path == '/' else ('const unrendered = ' + json.dumps(hidden) + ';').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html' if self.path == '/' else 'application/javascript')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for engine, binary in engines.items():
                with self.subTest(engine=engine):
                    calls.clear()
                    if engine == 'moli':
                        import hashlib, subprocess
                        provider = MoliWebFetchProvider('moli', {'binary':binary,
                            'block_private_networks':False, 'expected_reported_version':subprocess.check_output([binary, '--version'], text=True).strip(),
                            'expected_binary_sha256':hashlib.sha256(__import__('pathlib').Path(binary).read_bytes()).hexdigest()})
                        case = EvalCase('unseen-resource', 'Fixture', origin + '/', 'image', [], track='image', task_kind='resource')
                        result = provider.fetch(case, 10)
                        self.assertTrue(result.ok, result.error_message)
                        content = result.content
                    else:
                        result = fetch({'engine':engine, 'binary':binary, 'url':origin + '/', 'timeout':10,
                                        'output_mode':'resource', 'task_kind':'resource', 'track':'image', 'allow_private_network':True})
                        self.assertTrue(result['ok'], result)
                        content = result['content']
                    links = set(json.loads(content)['links'])
                    self.assertTrue({origin+'/visible.png', origin+'/small.png', origin+'/large.png',
                                     origin+'/image/a,b.png', origin+'/image/thumb.png?crop=10,20',
                                     origin+'/set/a,b.png', origin+'/set/thumb.png?crop=10,20'} <= links, links)
                    self.assertNotIn(origin+'/image/a', links)
                    self.assertNotIn(origin+'/20', links)
                    self.assertNotIn(hidden, links)
                    self.assertNotIn(origin+'/visible.mp4', links, 'an image task must not return video sources')
                    self.assertEqual(calls['/'], 1)
                    self.assertLessEqual(calls['/module.js'], 1, 'adapter must not refetch scripts to manufacture resource answers')
        finally:
            server.shutdown(); server.server_close(); thread.join(1)

    def test_native_document_projection_rejects_bytes_and_preserves_native_text(self):
        from unittest.mock import patch
        from harness.web_fetch.providers.headless_worker import _projection_text
        from harness.web_fetch.task_outputs import DOCUMENT_PAGE_EXPRESSION
        import time
        self.assertNotIn('fetch(', DOCUMENT_PAGE_EXPRESSION)
        for kind in ('text', 'page_text'):
            with self.subTest(kind=kind):
                meta={}
                actual=_projection_text(json.dumps({'kind':kind,'text':'Native document content'}),
                                        'document_page',meta,time.monotonic()+10)
                self.assertEqual(actual,'Native document content')
        with patch('harness.web_fetch.providers.headless_worker.extract_document_text',
                   side_effect=AssertionError('external parsing is forbidden')) as parser:
            with self.assertRaises(ValueError):
                _projection_text(json.dumps({'kind':'pdf_base64','data':'JVBERg=='}),
                                 'document_page',{},time.monotonic()+10)
            parser.assert_not_called()


class NativeRoutePublicationTests(unittest.TestCase):
    def test_page_routes_prioritize_native_output_and_keep_required_identity(self):
        from harness.web_fetch.task_outputs import require_native_task_routes
        pages=[{'id':'page','url':'https://example.com/page','track':'challenge','task_kind':'structure'}]
        accepted={'moli':{'route':'ordinary_markdown','output':'markdown'},
                  'lightpanda':{'extractor':'lightpanda_native_markdown'},
                  'obscura':{'extractor':'obscura_native_markdown'},
                  'chromium':{'extractor':'node-html-markdown','extractor_version':'2.0.0'},
                  'kitesurf':{'route':'browser_rendering_markdown','extractor':'cloudflare_native_markdown','browser':'kitesurf'}}
        for provider,meta in accepted.items():
            row={'provider':provider,'case_id':'page','provider_metadata':meta}
            require_native_task_routes(pages,[row])
            with self.subTest(provider=provider),self.assertRaises(ValueError):
                require_native_task_routes(pages,[{**row,'provider_metadata':{}}])
        with self.assertRaises(ValueError):
            require_native_task_routes(pages,[{'provider':'moli','case_id':'page','provider_metadata':{
                'route':'challenge_json_trace','output':'final_dom_html+local_markdown+network_diagnostics'}}])
        identity=[{**pages[0],'track':'risk_control','task_kind':'body','source_benchmark':{'risk_control':{'navigation_contract':{
            'requested_url':pages[0]['url'],'observed_final_url':'https://example.com/final'}}}}]
        meta={'route':'page_identity_json','output':'final_dom_html+local_markdown','final_url_source':'same_call_json'}
        row={'provider':'moli','case_id':'page','provider_metadata':meta}
        with self.assertRaises(ValueError):
            require_native_task_routes(identity,[row])
        require_native_task_routes(identity,[{**row,'provider_metadata':accepted['moli']}])
        for provider,meta in [('moli',{'route':'ordinary_markdown','output':'markdown'}),
                              ('chromium',{'extractor':'node-html-markdown'})]:
            case_scope=identity if provider=='moli' else pages
            stopped={'provider':provider,'case_id':'page','ok':False,'error_type':'timeout','provider_metadata':meta}
            require_native_task_routes(case_scope,[stopped])
            if provider=='chromium':
                with self.assertRaises(ValueError):
                    require_native_task_routes(case_scope,[{**stopped,'ok':True}])

    def test_configuration_failures_cannot_be_published_as_browser_results(self):
        from harness.web_fetch.task_outputs import require_native_task_routes
        cases=[{'id':'pdf','url':'https://example.com/file.pdf','track':'document','task_kind':'body'}]
        native={'moli':{'route':'ordinary_markdown','output':'markdown'},
                'lightpanda':{'extractor':'lightpanda_native_markdown'},
                'obscura':{'extractor':'obscura_native_markdown'},
                'chromium':{'extractor':'node-html-markdown','extractor_version':'2.0.0'},
                'kitesurf':{'route':'browser_rendering_markdown','extractor':'cloudflare_native_markdown','browser':'kitesurf'}}
        for provider,meta in native.items():
            row={'provider':provider,'case_id':'pdf','provider_metadata':meta,'ok':False}
            with self.subTest(provider=provider):
                require_native_task_routes(cases,[{**row,'error_type':'api_error'}])
                with self.assertRaisesRegex(ValueError,'Native task route contract violated'):
                    require_native_task_routes(cases,[{**row,'error_type':'configuration'}])

    def test_document_text_cannot_be_supplied_by_external_converter(self):
        from harness.web_fetch.task_outputs import require_native_task_routes
        cases=[{'id':'pdf','url':'https://example.com/file.pdf','track':'document','task_kind':'body'}]
        native={'moli':{'route':'ordinary_markdown','output':'markdown'},
                'lightpanda':{'extractor':'lightpanda_native_markdown'},
                'obscura':{'extractor':'obscura_native_markdown'},
                'chromium':{'extractor':'node-html-markdown','extractor_version':'2.0.0'},
                'kitesurf':{'route':'browser_rendering_markdown','extractor':'cloudflare_native_markdown','browser':'kitesurf'}}
        for provider,meta in native.items():
            row={'provider':provider,'case_id':'pdf','provider_metadata':meta,'ok':False}
            require_native_task_routes(cases,[row])
            for bad in ({**meta,'document_extractor':'pypdf'},
                        {'route':'raw_document','extractor':provider+'_cdp_response+document_text'},
                        {'route':'workers_ai_tomarkdown','extractor':'external_conversion'}):
                with self.subTest(provider=provider,bad=bad),self.assertRaises(ValueError):
                    require_native_task_routes(cases,[{**row,'provider_metadata':bad}])

    def test_kitesurf_resource_backend_must_be_bound_explicitly(self):
        from harness.web_fetch.task_outputs import require_native_task_routes
        cases=[{'id':'images','url':'https://example.com/','track':'image','task_kind':'resource'}]
        from harness.web_fetch.task_outputs import resource_expression
        meta={'browser':'kitesurf','endpoint_route':'/browser-run/devtools/browser?browser=kitesurf',
              'output_projection_sha256':hashlib.sha256(resource_expression('image').encode()).hexdigest()}
        row={'provider':'kitesurf','case_id':'images','provider_metadata':meta}
        require_native_task_routes(cases,[row])
        for field in ('browser','endpoint_route','output_projection_sha256'):
            bad={k:v for k,v in meta.items() if k!=field}
            with self.subTest(missing=field),self.assertRaises(ValueError):
                require_native_task_routes(cases,[{**row,'provider_metadata':bad}])

    def test_network_timeout_with_only_runner_receipts_is_not_an_adapter_substitution(self):
        from harness.web_fetch.task_outputs import require_native_task_routes
        cases=[{'id':'images','url':'https://example.com/','track':'image','task_kind':'resource'}]
        row={'provider':'kitesurf','case_id':'images','ok':False,'error_type':'timeout',
             'provider_metadata':{'call_log':{'path':'call.json','sha256':'0'*64},
                                  'configured_min_interval_seconds':0,'client_throttle_wait_ms':0}}
        require_native_task_routes(cases,[row])
        with self.assertRaises(ValueError):
            require_native_task_routes(cases,[{**row,'provider_metadata':{**row['provider_metadata'],'browser':'chromium'}}])

    def test_shared_projection_hash_rejects_one_provider_extra_crawler(self):
        import hashlib
        from harness.web_fetch.task_outputs import require_native_task_routes, resource_expression
        cases=[{'id':'images','url':'https://example.com/','track':'image','task_kind':'resource'}]
        digest=hashlib.sha256(resource_expression('image').encode()).hexdigest()
        for provider in ('moli','lightpanda','obscura','chromium'):
            row={'provider':provider,'case_id':'images','provider_metadata':{'output_projection_sha256':digest}}
            require_native_task_routes(cases,[row])
            with self.subTest(provider=provider),self.assertRaises(ValueError):
                require_native_task_routes(cases,[{**row,'provider_metadata':{'output_projection_sha256':'0'*64}}])


class AllFamilyInterfaceTests(unittest.TestCase):
    def test_reader_matrix_exposes_all_families_and_non_native_processing(self):
        from copy import deepcopy
        from harness.web_fetch.report_interfaces import browser_task_interface_matrix
        providers=['moli','lightpanda','obscura','chromium','kitesurf']
        cases={track:{'id':track,'url':'https://example.test/page','track':track,'task_kind':'body'}
               for track in ['web','dynamic','challenge','risk_control','document','image','video']}
        cases['document']['url']='https://example.test/file.pdf'
        cases['image']['task_kind']=cases['video']['task_kind']='resource'
        cases['risk_control']['source_benchmark']={'risk_control':{'navigation_contract':{
            'requested_url':'https://example.test/page','observed_final_url':'https://example.test/final'}}}
        matrix=browser_task_interface_matrix(cases,providers)
        self.assertEqual(len(matrix['rows']),7)
        self.assertEqual(sum(len(row['cells']) for row in matrix['rows']),35)
        rows={row['track']:row for row in matrix['rows']}
        for provider in providers:
            self.assertIn('不使用外部 PDF/DOCX 解析器',rows['document']['cells'][provider][0]['description'])
            self.assertIn('不转 Markdown',rows['image']['cells'][provider][0]['description'])
            self.assertTrue(rows['web']['cells'][provider][0]['wait'])
        self.assertIn('共同转换',rows['web']['cells']['chromium'][0]['description'])
        self.assertIn('内置转换',rows['challenge']['cells']['moli'][0]['description'])
        self.assertIn('内置转换',rows['risk_control']['cells']['moli'][0]['description'])
        self.assertNotIn('共同转换',rows['risk_control']['cells']['moli'][0]['description'])
        changed=deepcopy(cases)
        for case in changed.values():
            case.update(ground_truth_main_content='Different hidden answer',expected_resources=[{'url':'https://secret.test/answer'}])
        self.assertEqual(browser_task_interface_matrix(changed,providers),matrix)

    def test_wait_labels_distinguish_page_interfaces_from_document_and_media_interfaces(self):
        from harness.web_fetch.report_interfaces import browser_task_interface_matrix
        cases={
            'page':{'url':'https://example.test/page','track':'web','task_kind':'body'},
            'embedded':{'url':'https://example.test/viewer.html','track':'document','task_kind':'body'},
            'metadata':{'url':'https://example.test/watch','track':'video','task_kind':'body'},
            'resources':{'url':'https://example.test/gallery','track':'image','task_kind':'resource'},
        }
        providers=['lightpanda','kitesurf','obscura']
        rows={row['track']:row for row in browser_task_interface_matrix(cases,providers)['rows']}
        self.assertIn('done',rows['web']['cells']['lightpanda'][0]['wait'])
        self.assertIn('networkidle2',rows['web']['cells']['kitesurf'][0]['wait'])
        for track in ('document','video'):
            for provider in providers:
                self.assertTrue(rows[track]['cells'][provider][0]['wait'].startswith('CDP 页面提取等待 load'))
        for provider in ('lightpanda','kitesurf'):
            self.assertEqual(rows['image']['cells'][provider][0]['wait'],'CDP 页面提取等待 load')
        self.assertIn('自适应静默等待',rows['image']['cells']['obscura'][0]['wait'])

    def test_all_thirty_five_combinations_follow_deliverable_without_answer_values(self):
        from harness.web_fetch.providers.headless import ENGINES, output_mode_for_case
        from harness.web_fetch.task_outputs import resource_detail_links, resource_expression
        families = [('web','body','https://example.test/page','markdown'),
                    ('dynamic','body','https://example.test/app','markdown'),
                    ('challenge','body','https://example.test/popular','markdown'),
                    ('risk_control','body','https://example.test/protected','markdown'),
                    ('document','body','https://example.test/paper.pdf#page=2','markdown'),
                    ('image','resource','https://example.test/gallery','resource'),
                    ('video','resource','https://example.test/watch','resource')]
        for engine in ENGINES:
            for track, kind, url, expected_mode in families:
                with self.subTest(engine=engine, track=track):
                    case = EvalCase('arbitrary-id','Arbitrary',url,track,[],track=track,task_kind=kind)
                    self.assertEqual(output_mode_for_case(engine,'headless_markdown',case),expected_mode)
        for answer in ('https://secret-answer.test/video/42','https://different-answer.test/watch/99'):
            case = EvalCase('arbitrary-id','Arbitrary','https://example.test/', 'video',[],
                            track='video',task_kind='resource',expected_resources=[{'kind':'video-link','url':answer}])
            expression = resource_expression(case.track,resource_detail_links(case))
            self.assertNotIn(answer,expression)
            self.assertEqual(expression,resource_expression('video',True))

class ResourceAttributeParsingTests(unittest.TestCase):
    def test_single_url_commas_and_srcset_candidates_preserve_address_identity(self):
        import shutil, subprocess
        from harness.web_fetch.task_outputs import resource_expression
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required by the repository UI test runtime')
        attributes = [{'src': '/image/a,b.png'}, {'src': '/image/thumb.png?crop=10,20'},
                      {'srcset': '/set/a,b.png 1x, /set/thumb.png?crop=10,20 2x'},
                      {'srcset': '/first.png, /second.png 2x'}, {'src': 'data:image/png;base64,AA=='}]
        setup = '''const location={href:'https://example.test/page'};
        const nodes=ATTRS.map(attrs=>({baseURI:location.href,getAttribute:name=>attrs[name]||null}));
        const document={baseURI:location.href,querySelectorAll:selector=>selector==='iframe,frame'?[]:nodes};
        process.stdout.write(eval(EXPR));'''
        code = setup.replace('ATTRS', json.dumps(attributes)).replace('EXPR', json.dumps(resource_expression('image')))
        result = subprocess.run([node, '-e', code], check=True, capture_output=True, text=True)
        self.assertEqual(set(json.loads(result.stdout)['links']), {
            'https://example.test/image/a,b.png', 'https://example.test/image/thumb.png?crop=10,20',
            'https://example.test/set/a,b.png', 'https://example.test/set/thumb.png?crop=10,20',
            'https://example.test/first.png', 'https://example.test/second.png'})
