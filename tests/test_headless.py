import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.container_resources import ContainerCall
from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.headless import (
    HeadlessProvider,
    _resolve_binary,
    cli_command,
    completion_metadata,
    output_mode_for_case,
)
from harness.web_fetch.providers.headless_worker import fetch
from harness.web_fetch.review_execution import (
    INDEPENDENCE_RULES,
    RESOURCE_EQUIVALENCE_RULES,
    SCHEMA,
    anomaly_investigation_packet,
    blind_review_prompt,
    independent_attempt,
    review_main,
    validate_review_evidence,
)
from harness.web_fetch.runner import run_evaluation


class NativeMetadataTests(unittest.TestCase):
    def test_chromium_public_dns_uses_strict_secure_profile(self):
        import sys
        from types import SimpleNamespace
        from harness.web_fetch.network_dns import DNS_ENDPOINT
        observed = {}
        class Page:
            url = 'https://example.com/'
            def goto(self, *args, **kwargs):
                return SimpleNamespace(status=200)
        class Context:
            browser = SimpleNamespace(version='fixture-chromium')
            def new_page(self): return Page()
            def close(self): observed['closed'] = True
        class Chromium:
            def launch_persistent_context(self, directory, **kwargs):
                observed['local_state'] = json.loads(
                    (Path(directory) / 'Local State').read_text()
                )
                return Context()
        class Driver:
            def __enter__(self):
                return SimpleNamespace(chromium=Chromium())
            def __exit__(self, *args):
                observed['driver_closed'] = True
        request = {
            'engine': 'chromium',
            'binary': '/chromium',
            'url': 'https://example.com/',
            'timeout': 1,
            'output_mode': 'resource',
            'track': 'image',
            'dns_mode': 'public_doh',
        }
        module = SimpleNamespace(sync_playwright=Driver, Error=RuntimeError)
        with patch.dict(sys.modules, {'playwright.sync_api': module}), \
             patch('harness.web_fetch.providers.headless_worker._page_projection',
                   return_value='{"urls":["https://example.com/image.png"]}'):
            result = fetch(request)
        self.assertTrue(result['ok'])
        self.assertEqual(observed['local_state']['dns_over_https'], {
            'mode': 'secure', 'templates': DNS_ENDPOINT + '{?dns}',
        })
        self.assertTrue(observed['local_state']['built_in_dns_client_enabled'])
        self.assertFalse(result['metadata']['system_dns_fallback'])
        self.assertTrue(observed['closed'] and observed['driver_closed'])

    def test_kitesurf_page_deliverables_use_the_native_markdown_endpoint(self):
        from harness.web_fetch.models import ProviderResult
        for track,kind,url in (
            ('image','resource','https://example.com/'),
            ('video','resource','https://example.com/'),
            ('document','body','https://example.com/viewer/'),
            ('video','body','https://example.com/watch'),
        ):
            with self.subTest(track=track,kind=kind):
                provider=HeadlessProvider('kitesurf',{'type':'headless_markdown','engine':'kitesurf',
                    'account_id':'account','api_token':'token','wait_until':'networkidle2'})
                case=EvalCase('case','Case',url,'public',[],track=track,task_kind=kind)
                native=ProviderResult(True,'public Markdown',metadata={
                    'route':'browser_rendering_markdown',
                    'extractor':'cloudflare_native_markdown',
                    'browser':'kitesurf',
                })
                with patch.object(provider,'_run_local_worker',side_effect=AssertionError('CDP projection is forbidden')) as worker, \
                        patch('harness.web_fetch.providers.cloudflare.CloudflareMarkdownProvider.fetch',return_value=native) as markdown:
                    result=provider.fetch(case,45)
                worker.assert_not_called()
                markdown.assert_called_once_with(case,45)
                self.assertEqual(result.content,'public Markdown')
                self.assertEqual(result.metadata['extractor'],'cloudflare_native_markdown')
                self.assertNotIn('output_projection_sha256',result.metadata)

    def test_native_envelope_preserves_content_and_actual_destination(self):
        from types import SimpleNamespace
        for engine, envelope in (
            ('lightpanda', {'content': '# Body', 'url': 'https://example.com/final', 'http_status': 200}),
            ('obscura', {'content': '# Body', 'final_url': 'https://example.com/final', 'title': 'Body'}),
        ):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                script = Path(directory) / 'native.js'
                script.write_text('"# Body"')
                metadata = {'format': 'lightpanda_json'} if engine == 'lightpanda' else {
                    'format': 'obscura_eval', 'script_path': str(script),
                    'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}
                request = {'engine': engine, 'binary': '/browser', 'url': 'https://example.com/start',
                           'timeout': 5, 'output_mode': 'markdown', 'native_metadata': metadata}
                response = SimpleNamespace(returncode=0, stdout=json.dumps(envelope).encode(), stderr=b'')
                with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=response) as run:
                    result = fetch(request)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['content'], '# Body')
                self.assertEqual(result['final_url'], 'https://example.com/final')
                self.assertEqual(base64.b64decode(result['metadata']['_raw_stdout_b64']), response.stdout)
                command = run.call_args.args[0]
                if engine == 'lightpanda':
                    self.assertIn('--json', command)
                else:
                    self.assertNotIn('--dump', command)
                    self.assertIn('location.href', command[command.index('--eval')+1])

    def test_missing_destination_does_not_invent_requested_url(self):
        from types import SimpleNamespace
        request = {'engine': 'lightpanda', 'binary': '/browser', 'url': 'https://example.com/start',
                   'timeout': 5, 'output_mode': 'markdown',
                   'native_metadata': {'format': 'lightpanda_json'}}
        response = SimpleNamespace(returncode=0, stdout=b'{"content":"Body"}', stderr=b'')
        with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=response):
            result = fetch(request)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['final_url'])
        response.stdout = b'{"url":"https://example.com/final"}'
        with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=response):
            result = fetch(request)
        self.assertFalse(result['ok'])
        self.assertIn('string content', result['error_message'])

    def test_modified_vendor_script_is_rejected_before_browser_call(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'native.js'
            script.write_text('"changed"')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                cli_command('obscura', '/browser', 'https://example.com/', 5, 'markdown',
                            native_metadata={'format': 'obscura_eval', 'script_path': str(script),
                                             'script_sha256': '0'*64})


    def test_isolated_resource_worker_needs_no_host_script_path(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'native.js'
            script.write_text('"# Isolated body"')
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            provider = HeadlessProvider('obscura', {
                'type': 'headless_markdown', 'engine': 'obscura',
                'resource_sample_interval_ms': 50,
                'resource_container_image': 'fixture:local', 'resource_binary': '/browser',
                'native_metadata': {'format': 'obscura_eval', 'script_path': str(script),
                                    'script_sha256': digest},
            })
            captured = []
            class IsolatedWorker:
                def run(self, request, timeout):
                    captured.append(request)
                    # Model the container boundary: the host source file is
                    # unavailable when the worker constructs its browser call.
                    script.unlink()
                    response = SimpleNamespace(returncode=0, stderr=b'', stdout=json.dumps({
                        'content': '# Isolated body', 'final_url': 'https://example.com/final'}).encode())
                    with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=response) as command:
                        result = fetch(request)
                    if result['ok']:
                        self.expression = command.call_args.args[0][command.call_args.args[0].index('--eval') + 1]
                    return ContainerCall(stdout=json.dumps(result).encode(), stderr=b'', exit_code=0,
                        duration_ms=1, resource={'cpu_ms': 1, 'memory_peak_bytes': 2, 'network_rx_bytes': 3},
                        environment={'backend': 'docker_cgroup_v2'})
            runner = IsolatedWorker()
            identity = {'binary_sha256': 'a'*64, 'reported_version': 'obscura 0.2.4',
                        'version_probe_error': None}
            case = EvalCase('case', 'Case', 'https://example.com/start', 'web', [])
            with patch.object(provider, '_resource_runner', return_value=runner), \
                 patch.object(provider, 'implementation_identity', return_value=identity):
                result = provider.fetch(case, 1)
            self.assertTrue(result.ok, result.error_message)
            self.assertEqual(result.content, '# Isolated body')
            self.assertEqual(result.final_url, 'https://example.com/final')
            self.assertEqual(result.metadata['native_metadata']['script_sha256'], digest)
            self.assertNotIn('script_path', captured[0]['native_metadata'])
            self.assertIn('location.href', runner.expression)
            self.assertIn('"# Isolated body"', runner.expression)

    def test_altered_in_transit_native_script_is_rejected_before_browser_launch(self):
        request = {'engine': 'obscura', 'binary': '/browser', 'url': 'https://example.com/',
                   'timeout': 1, 'output_mode': 'markdown',
                   'native_metadata': {'format': 'obscura_eval', 'script_source': '"changed"',
                                       'script_sha256': hashlib.sha256(b'"original"').hexdigest()}}
        with patch('harness.web_fetch.providers.headless_worker.subprocess.run') as command:
            result = fetch(request)
        self.assertFalse(result['ok'])
        self.assertIn('hash mismatch', result['error_message'])
        command.assert_not_called()

class HeadlessTests(unittest.TestCase):
    def test_local_browser_request_carries_declared_public_dns_environment(self):
        provider = HeadlessProvider('lightpanda', {
            'type': 'headless_markdown',
            'engine': 'lightpanda',
            'binary': '/lightpanda',
            'dns_mode': 'public_doh',
            'native_dns_library': '/resolver.dylib',
        })
        raw = json.dumps({'ok': True, 'content': '# Body', 'metadata': {}}).encode()
        identity = {'binary_sha256': 'a' * 64, 'reported_version': 'lightpanda fixture',
                    'version_probe_error': None}
        case = EvalCase('case', 'Case', 'https://example.com/', 'web', [])
        with patch.object(provider, '_run_local_worker',
                          return_value=(raw, '', None, 0, len(raw), 0)) as worker, \
             patch.object(provider, 'implementation_identity', return_value=identity):
            result = provider.fetch(case, 45)
        request = worker.call_args.args[1]
        self.assertTrue(result.ok)
        self.assertEqual(request['dns_mode'], 'public_doh')
        self.assertEqual(request['native_dns_library'], '/resolver.dylib')
        self.assertEqual(result.metadata['dns_mode'], 'public_doh')

    @patch("harness.web_fetch.retrieval_hints._verify_contract_source")
    def test_lightpanda_receives_shared_native_frame_contract(self, _verify_source):
        provider = HeadlessProvider('lightpanda', {
            'type': 'headless_markdown', 'engine': 'lightpanda', 'binary': '/lightpanda'})
        case = EvalCase('dynamic-iframe', 'Frame', 'https://example.com/', 'dynamic', [],
            track='dynamic', retrieval_hints={
                'version': 3, 'mode': 'native',
                'contract': 'iframe-fourteen-family-headings'})
        raw = json.dumps({'ok': True, 'content': 'native frame body', 'metadata': {}}).encode()
        identity = {'binary_sha256': 'a' * 64, 'reported_version': 'lightpanda fixture',
                    'version_probe_error': None}
        with patch.object(provider, '_run_local_worker', return_value=(raw, '', None, 0, len(raw), 0)) as worker, \
             patch.object(provider, 'implementation_identity', return_value=identity):
            result = provider.fetch(case, 45)
        request = worker.call_args.args[1]
        self.assertTrue(request['include_subdocuments'])
        self.assertEqual(request['output_mode'], 'markdown')
        self.assertIn('h3.family-name', request['retrieval_plan']['wait_value'])
        self.assertEqual(result.metadata['subdocuments']['interface'],
                         '--load-resources iframe + --with-frames')
        self.assertEqual(result.metadata['subdocuments']['support'],
                         'native_markdown_omits_child_document')
        self.assertEqual(result.metadata['extractor'], 'lightpanda_native_markdown')
        self.assertEqual(result.metadata['output_contract'], 'markdown')
        self.assertFalse(result.metadata['subdocuments']['harness_content_assembly'])

    @patch("harness.web_fetch.retrieval_hints._verify_contract_source")
    def test_engines_without_native_frame_markdown_do_not_assemble_child_content(
        self, _verify_source
    ):
        case = EvalCase('dynamic-iframe', 'Frame', 'https://example.com/', 'dynamic', [],
            track='dynamic', retrieval_hints={
                'version': 3, 'mode': 'native',
                'contract': 'iframe-fourteen-family-headings'})
        raw = json.dumps({'ok': True, 'content': 'outer document only', 'metadata': {}}).encode()
        identity = {'binary_sha256': 'a' * 64, 'reported_version': 'fixture',
                    'version_probe_error': None}
        for engine in ('obscura', 'chromium'):
            provider = HeadlessProvider(engine, {
                'type': 'headless_markdown', 'engine': engine, 'binary': '/' + engine})
            with self.subTest(engine=engine), \
                 patch.object(provider, '_run_local_worker', return_value=(raw, '', None, 0, len(raw), 0)) as worker, \
                 patch.object(provider, 'implementation_identity', return_value=identity), \
                 patch('harness.web_fetch.providers.headless.converter_package', return_value=Path('/converter')):
                result = provider.fetch(case, 45)
            request = worker.call_args.args[1]
            self.assertTrue(request['include_subdocuments'])
            self.assertEqual(result.metadata['subdocuments']['support'], 'unsupported')
            self.assertIsNone(result.metadata['subdocuments']['interface'])
            self.assertFalse(result.metadata['subdocuments']['harness_content_assembly'])

    def test_resource_mode_routes_remote_client_through_isolated_container(self):
        provider = HeadlessProvider('kitesurf', {
            'type': 'headless_markdown', 'engine': 'kitesurf',
            'account_id': 'account', 'api_token': 'token',
            'resource_sample_interval_ms': 50,
            'resource_container_image': 'fixture:local',
        })
        runner = unittest.mock.MagicMock()
        runner.run.return_value = ContainerCall(
            stdout=json.dumps({'ok': True, 'content': '# Example', 'html': '',
                               'status_code': 200, 'final_url': 'https://example.com/',
                               'metadata': {}}).encode(),
            stderr=b'', exit_code=0, duration_ms=10,
            resource={'cpu_ms': 1, 'memory_peak_bytes': 2, 'network_rx_bytes': 3},
            environment={'backend': 'docker_cgroup_v2'},
        )
        case = EvalCase('case', 'Case', 'https://example.com/', 'web', [])
        with patch.object(provider, '_resource_runner', return_value=runner):
            result = provider.fetch(case, 1)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata['client_resource']['network_rx_bytes'], 3)
        self.assertEqual(result.metadata['resource_environment']['backend'], 'docker_cgroup_v2')
        request = runner.run.call_args.args[0]
        self.assertTrue(request['remote_markdown_api'])
        self.assertEqual(request['api_token'], 'token')
    def test_body_review_rejects_machine_percentage_as_evidence(self):
        with self.assertRaisesRegex(ValueError, 'scores and percentages'):
            validate_review_evidence({
                'attempt_id':'p__pdf__r001', 'verdict':'pass',
                'evidence':'Coverage is 100%, above the 80% threshold.',
            }, 'body')
        with self.assertRaisesRegex(ValueError, 'scores and percentages'):
            validate_review_evidence({
                'attempt_id':'p__pdf__r002', 'verdict':'fail',
                'evidence':'The middle region scored 42%.',
            }, 'body')
        with self.assertRaisesRegex(ValueError, 'scores and percentages'):
            validate_review_evidence({
                'attempt_id':'p__pdf__r003', 'verdict':'pass',
                'evidence':(
                    'beginning: <actual passage>; middle: <actual passage>; '
                    'end: <actual passage>'),
            }, 'body')

    def test_body_review_accepts_direct_positive_and_negative_observations(self):
        validate_review_evidence({
            'attempt_id':'p__pdf__r001', 'verdict':'pass',
            'evidence':(
                'beginning: Annual Climate Report; '
                'middle: Global temperature anomalies by region; '
                'end: Methods and references'),
        }, 'body')
        validate_review_evidence({
            'attempt_id':'p__pdf__r002', 'verdict':'fail',
            'evidence':(
                'missing: Methods and references section; '
                'observed: artifact stops after the regional temperature table'),
        }, 'body')
        validate_review_evidence({
            'attempt_id':'p__docx__r001', 'verdict':'fail',
            'evidence':(
                'missing: readable document title and tables; '
                'observed: raw response begins <html><body>PK archive bytes'),
        }, 'body')

    def test_independence_prompt_forbids_production_scorer_recalculation(self):
        self.assertIn('Do not import, execute, or reimplement the production scorer',
                      INDEPENDENCE_RULES)
        self.assertIn('Judge the saved artifact directly', INDEPENDENCE_RULES)
        self.assertIn('beginning:', INDEPENDENCE_RULES)
        self.assertIn('missing:', INDEPENDENCE_RULES)
        self.assertIn('Separately list any suspicious anomaly', INDEPENDENCE_RULES)
        self.assertIn('Anomalies are not verdict evidence', INDEPENDENCE_RULES)

    def test_blind_resource_guidance_limits_wikimedia_host_equivalence_to_same_asset(self):
        self.assertIn('upload.wikimedia.org', RESOURCE_EQUIVALENCE_RULES)
        self.assertIn('thumb.wikimedia.org', RESOURCE_EQUIVALENCE_RULES)
        self.assertIn('original asset filename must match', RESOURCE_EQUIVALENCE_RULES)
        self.assertIn('A different original asset path or filename',
                      RESOURCE_EQUIVALENCE_RULES)
        self.assertNotIn('machine verdict', RESOURCE_EQUIVALENCE_RULES)
        self.assertNotIn('metrics', RESOURCE_EQUIVALENCE_RULES)
        prompt = blind_review_prompt(Path('/synthetic-packet.json'), 1)
        self.assertIn(RESOURCE_EQUIVALENCE_RULES, prompt)

    def test_codex_review_schema_requires_every_declared_field(self):
        item = SCHEMA['properties']['attempts']['items']
        self.assertEqual(set(item['required']), set(item['properties']))

    def test_review_source_guards_case_only_and_declared_truncation_mistakes(self):
        prompt = blind_review_prompt(Path('/synthetic-packet.json'), 1)
        self.assertIn('Capitalization alone is an ordinary presentation difference', prompt)
        self.assertIn('provider-authored notice that explicitly says', prompt)

    def test_explicit_empty_chromium_binary_never_uses_playwright_fallback(self):
        self.assertIsNone(_resolve_binary('chromium', ''))

    def test_resource_chromium_disables_inner_sandbox_inside_locked_container(self):
        provider = HeadlessProvider('chromium', {
            'type': 'headless_markdown', 'engine': 'chromium',
            'resource_sample_interval_ms': 50,
            'resource_container_image': 'fixture:local',
            'resource_binary': '/usr/bin/chromium',
        })
        runner = unittest.mock.MagicMock()
        runner.implementation_identity.return_value = {
            'binary_sha256': 'a' * 64, 'reported_version': 'Chromium 152.0.7977.82',
            'version_probe_error': None,
        }
        runner.run.return_value = ContainerCall(
            stdout=json.dumps({'ok': True, 'content': 'Example', 'html': '',
                               'metadata': {}}).encode(),
            stderr=b'', exit_code=0, duration_ms=10,
            resource={'cpu_ms': 1}, environment={'backend': 'docker_cgroup_v2'},
        )
        case = EvalCase('case', 'Case', 'https://example.com/', 'web', [])
        with patch.object(provider, '_resource_runner', return_value=runner):
            result = provider.fetch(case, 1)
        self.assertTrue(result.ok)
        request = runner.run.call_args.args[0]
        self.assertEqual(request['browser_args'], ['--no-sandbox'])
        self.assertEqual(result.metadata['browser_launch_args'], ['--no-sandbox'])

    def test_review_packets_can_be_prepared_for_one_provider_without_verdicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root/'evidence'; work = root/'work'
            evidence.mkdir()
            dataset = {'cases': [
                {'id':'a','task_kind':'body','required_facts':[]},
                {'id':'b','task_kind':'body','required_facts':[]},
            ]}
            records = [
                {'attempt_id':f'{provider}__{case_id}__r001','provider':provider,
                 'case_id':case_id,'artifact_path':f'artifacts/{provider}-{case_id}.txt',
                 'ok':True,'error_type':None,'metrics':{'content_pass':True}}
                for provider in ('p','q') for case_id in ('a','b')
            ]
            (evidence/'dataset.json').write_text(json.dumps(dataset))
            for record in records:
                artifact = evidence / record['artifact_path']
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b'Independent synthetic answer')
                record['metrics']['content_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            (evidence/'results.jsonl').write_text(
                ''.join(json.dumps(row)+'\n' for row in records))
            argv = ['review_headless_results.py','--evidence',str(evidence),
                    '--work',str(work),'--batch-cases','1','--model','inherited',
                    '--reviewer','internal','--scope','all','--provider','p',
                    '--confirm-full-review','--prepare-only']
            with patch('sys.argv', argv):
                review_main()
            packets = sorted(work.glob('batch-*-input.json'))
            self.assertEqual(len(packets), 2)
            attempts = [attempt for path in packets
                        for attempt in json.loads(path.read_text())['attempts']]
            self.assertEqual({row['attempt_id'] for row in attempts},
                             {'p__a__r001','p__b__r001'})
            self.assertTrue(all('metrics' not in row for row in attempts))

    def test_review_defaults_to_failures_and_full_scope_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence=root/'evidence';work=root/'work'
            evidence.mkdir()
            dataset={'cases':[{'id':'a','task_kind':'body'}]}
            rows=[
                {'attempt_id':'p__a__r001','provider':'p','case_id':'a',
                 'artifact_path':'a.txt','ok':True,'error_type':None,
                 'metrics':{'content_pass':False}},
                {'attempt_id':'q__a__r001','provider':'q','case_id':'a',
                 'artifact_path':'b.txt','ok':True,'error_type':None,
                 'metrics':{'content_pass':True}},
            ]
            (evidence/'dataset.json').write_text(json.dumps(dataset))
            for row in rows:
                artifact = evidence / row['artifact_path']
                artifact.write_bytes(b'Independent synthetic answer')
                row['metrics']['content_sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            (evidence/'results.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
            argv=['review_headless_results.py','--evidence',str(evidence),
                  '--work',str(work),'--model','inherited','--reviewer','internal',
                  '--prepare-only']
            with patch('sys.argv',argv): review_main()
            packet=json.loads(next(work.glob('batch-*-input.json')).read_text())
            self.assertEqual([x['attempt_id'] for x in packet['attempts']],['p__a__r001'])
            with patch('sys.argv',argv[:-1]+['--scope','all','--prepare-only']):
                with self.assertRaises(SystemExit): review_main()
            pass_work=root/'pass-work'
            pass_argv=argv[:-1]+['--work',str(pass_work),'--scope','machine-passes',
                                 '--confirm-full-review','--prepare-only']
            with patch('sys.argv',pass_argv): review_main()
            pass_packet=json.loads(next(pass_work.glob('batch-*-input.json')).read_text())
            self.assertEqual([x['attempt_id'] for x in pass_packet['attempts']],['q__a__r001'])

    def test_required_anomaly_pass_is_added_to_default_failure_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence=root/'evidence';work=root/'work';evidence.mkdir()
            dataset={'comparison':{'require_anomaly_review':True},
                     'cases':[{'id':'a','task_kind':'body'}]}
            row={'attempt_id':'p__a__r001','provider':'p','case_id':'a',
                 'artifact_path':'empty.txt','ok':True,'error_type':None,
                 'metrics':{'content_pass':True}}
            (evidence/'empty.txt').write_text('')
            row['metrics']['content_sha256'] = hashlib.sha256(b'').hexdigest()
            (evidence/'dataset.json').write_text(json.dumps(dataset))
            (evidence/'results.jsonl').write_text(json.dumps(row)+'\n')
            argv=['review_headless_results.py','--evidence',str(evidence),
                  '--work',str(work),'--model','inherited','--reviewer','internal',
                  '--prepare-only']
            with patch('sys.argv',argv): review_main()
            packet=json.loads(next(work.glob('batch-*-input.json')).read_text())
            self.assertEqual([x['attempt_id'] for x in packet['attempts']],['p__a__r001'])

    def test_independent_review_receives_real_file_evidence(self):
        row = {'attempt_id':'a','provider':'moli-1.1.3','artifact':'empty.txt',
               'request_ok':True,'error_type':None,'file_artifacts':['file.bin'],
               'raw_response_artifact':'raw.bin',
               'raw_response_artifacts':[{'kind':'raw_response','path':'response.bin'}],
               'answer_metadata_artifact':'answer.json',
               'metrics':{'raw_file_pass':True}}
        packet = independent_attempt(row)
        self.assertEqual(packet['file_artifacts'], ['file.bin'])
        self.assertNotIn('raw_response_artifact',packet)
        self.assertNotIn('raw_response_artifacts',packet)
        self.assertNotIn('request_ok',packet)
        self.assertNotIn('error_type',packet)
        self.assertEqual(packet['answer_metadata_artifact'], 'answer.json')
        self.assertNotIn('metrics', packet)

    def test_raw_and_scorer_evidence_only_enters_post_verdict_anomaly_packet(self):
        evidence=Path('/evidence')
        dataset={'cases':[{'id':'a','task_kind':'body'}]}
        records=[{'attempt_id':'p__a','provider':'p','case_id':'a','url':'https://x',
                  'artifact_path':'answer.txt','ok':True,'status_code':200,
                  'metrics':{'content_pass':False},'provider_metadata':{
                      'raw_response_path':'private_artifacts/p/a.response.bin',
                      'raw_response_sha256':'a'*64,'raw_response_bytes':10}}]
        attempts=[{'attempt_id':'p__a','agent_verdict':'fail','agent_evidence':'missing body',
                   'reviewer_anomalies':[],'machine_verdict':'fail'}]
        packet=anomaly_investigation_packet(
            evidence,dataset,records,attempts,{'p__a':['request-ok-empty-answer']})
        self.assertTrue(packet['blind_verdicts_recorded'])
        item=packet['attempts'][0]
        self.assertEqual(item['blind_review']['agent_verdict'],'fail')
        self.assertEqual(item['raw_artifacts'][0]['bytes'],10)
        self.assertEqual(item['scorer'],{'content_pass':False})
        self.assertIn('Use not_applicable, not insufficient', packet['instructions'])
        self.assertIn('Request success means the provider completed target retrieval',
                      packet['instructions'])
        self.assertIn('does not mean that the returned body is nonempty',
                      packet['instructions'])
        self.assertIn('success:false, or tool error stays a request failure',
                      packet['instructions'])
        self.assertIn('outer transport HTTP 2xx alone', packet['instructions'])
        self.assertIn('a source-code fix alone does not correct historical evidence',
                      packet['instructions'])

    def test_review_answer_metadata_whitelists_only_answer_fields(self):
        from harness.web_fetch.result_review import review_answer_metadata
        record={'title':'Page title','provider_metadata':{
            'links':[{'href':'/asset.png'}], 'images':['/image.png'],
            'request_url':'secret request', 'final_url':'diagnostic redirect',
            'logs':['not answer evidence'], 'resource_candidate_urls':['derived'],
        }}
        self.assertEqual(review_answer_metadata(record), {
            'title':'Page title', 'links':[{'href':'/asset.png'}],
            'images':['/image.png'],
        })
        self.assertEqual(review_answer_metadata(record, include_resources=False), {
            'title':'Page title',
        })

    def test_whitespace_normalization_preserves_original_semantics(self):
        import itertools
        import re

        from lexbench_webfetch.metrics import normalize_for_match
        old = r'(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])'
        new = r'(?<=[\u3400-\u9fff])\s+|(?<!\s)\s+(?=[\u3400-\u9fff])'
        for parts in itertools.product(('a','中','\n',' ','\t','1',''), repeat=5):
            text = ''.join(parts)
            self.assertEqual(re.sub(old, '', text), re.sub(new, '', text))
        self.assertEqual(normalize_for_match('left'+' '*100_000+'right'), 'left right')
        self.assertEqual(normalize_for_match('中'+' '*100_000+'文'), '中文')

    def test_visible_link_titles_and_markdown_escapes_do_not_change_facts(self):
        from lexbench_webfetch.metrics import fact_in_text, normalize_for_match
        actual = '请[联系客服](zh-CN\n"en-US")或前往后台；deep\\-thoughts'
        expected = '请联系客服或前往后台；deep-thoughts'
        self.assertEqual(normalize_for_match(actual), normalize_for_match(expected))
        self.assertFalse(fact_in_text(normalize_for_match('interface forasynchronously executing'),
                                     normalize_for_match('interface for asynchronously executing')))
        self.assertFalse(fact_in_text(normalize_for_match('interface for synchronously executing'),
                                     normalize_for_match('interface for asynchronously executing')))

    def test_command_time_units_and_shared_html_contract(self):
        for engine in ('moli', 'obscura', 'lightpanda'):
            command = cli_command(engine, '/binary', 'https://example.com/', 1.5)
            self.assertEqual(command[:6], ['/binary', 'fetch', '--dump', 'html', '--wait-until', 'domcontentloaded'])
            self.assertEqual(command[-1], 'https://example.com/')
            flag = '--timeout' if engine != 'lightpanda' else '--http-timeout'
            self.assertEqual(command[command.index(flag)+1], '2' if engine == 'obscura' else '1500')

    def test_native_markdown_commands_keep_each_cli_readiness_contract(self):
        moli = cli_command('moli', '/moli', 'https://example.com/', 1.5, 'markdown')
        lightpanda = cli_command('lightpanda', '/lp', 'https://example.com/', 1.5, 'markdown')
        obscura = cli_command('obscura', '/obscura', 'https://example.com/', 1.5, 'markdown')
        self.assertEqual(moli[:6], ['/moli','fetch','--dump','markdown','--wait-until','done'])
        self.assertEqual(cli_command('moli','/moli','https://example.com/',1.5,'markdown','networkidle')[5],
                         'networkidle')
        self.assertIn('--terminate-ms', lightpanda)
        self.assertNotIn('--wait-until', obscura)

    def test_subdocuments_use_only_native_serializer_flags(self):
        for engine in ('moli', 'lightpanda'):
            command = cli_command(engine, '/' + engine, 'https://example.com/', 5,
                                  'markdown', include_subdocuments=True)
            self.assertEqual(command.count('--with-frames'), 1)
            if engine == 'lightpanda':
                self.assertEqual(command[command.index('--load-resources') + 1], 'iframe')
            else:
                self.assertNotIn('--load-resources', command)
            self.assertNotIn('--eval', command)
        obscura = cli_command('obscura', '/obscura', 'https://example.com/', 5,
                              'markdown', include_subdocuments=True)
        self.assertNotIn('--with-frames', obscura)
        self.assertNotIn('--eval', obscura)

    def test_lightpanda_json_envelope_preserves_completion_and_frame_flags(self):
        plan = {
            'version': 3, 'wait_until': 'load', 'selector': 'li',
            'minimum_count': 100, 'timeout_ms': 45000, 'delay_ms': 0,
            'frame_selector': 'iframe', 'minimum_text_fragments': 0,
            'wait_kind': 'script',
            'wait_value': '() => document.querySelectorAll("li").length >= 100',
        }
        command = cli_command(
            'lightpanda', '/lightpanda', 'https://example.com/', 45, 'markdown',
            retrieval_plan=plan, include_subdocuments=True,
            native_metadata={'format': 'lightpanda_json'})
        self.assertEqual(command[command.index('--dump') + 1], 'markdown')
        self.assertEqual(command.count('--json'), 1)
        self.assertEqual(command[command.index('--wait-until') + 1], 'load')
        self.assertEqual(command[command.index('--load-resources') + 1], 'iframe')
        self.assertEqual(command.count('--with-frames'), 1)
        self.assertEqual(command[command.index('--wait-script') + 1], plan['wait_value'])

    def test_lightpanda_script_wait_does_not_claim_enforcement(self):
        plan = {
            'version': 3, 'wait_until': 'load', 'selector': '.quote',
            'minimum_count': 10, 'timeout_ms': 45000, 'delay_ms': 0,
            'frame_selector': None, 'minimum_text_fragments': 3,
            'wait_kind': 'script', 'wait_value': '() => false',
        }
        metadata = completion_metadata('lightpanda', plan, 'markdown')
        self.assertEqual(metadata['strategy'], 'native_script_not_enforced')
        self.assertEqual(metadata['completion_support'], 'unsupported')
        self.assertFalse(metadata['minimum_count_enforced'])
        self.assertEqual(metadata['minimum_text_fragments'], 3)
        self.assertIn('script_enforcement', metadata['unsupported'])
        self.assertIn('minimum_text_fragments', metadata['unsupported'])

    def test_lightpanda_selector_wait_keeps_its_native_capability(self):
        plan = {
            'version': 3, 'wait_until': 'load', 'selector': '.ready',
            'minimum_count': 1, 'timeout_ms': 45000, 'delay_ms': 0,
            'frame_selector': None, 'minimum_text_fragments': 0,
            'wait_kind': 'selector', 'wait_value': '.ready',
        }
        metadata = completion_metadata('lightpanda', plan, 'markdown')
        self.assertEqual(metadata['completion_support'], 'exact')
        self.assertEqual(metadata['minimum_text_fragments'], 0)
        self.assertEqual(metadata['unsupported'], [])

    def test_lightpanda_iframe_capability_receipt_does_not_overclaim_markdown(self):
        receipt = json.loads(Path(
            'research/lightpanda-100-native-iframe-capability.json').read_text())
        self.assertEqual(receipt['provider_calls'], 0)
        observations = receipt['observations']
        both = ['--load-resources', 'iframe', '--with-frames']
        self.assertTrue(any(row['dump'] == 'html' and row['flags'] == both
                            and row['child_marker_present'] for row in observations))
        self.assertTrue(all(not row['child_marker_present']
                            for row in observations if row['dump'] == 'markdown'))

    def test_obscura_loopback_fixture_requires_explicit_private_network_flag(self):
        command = cli_command(
            'obscura', '/obscura', 'http://127.0.0.1:8123/case/x', 1.5,
            'markdown', allow_private_network=True,
        )
        self.assertEqual(command[:3], ['/obscura', '--allow-private-network', 'fetch'])

    def test_https_fixture_ca_is_passed_to_native_clients(self):
        for engine in ('moli', 'lightpanda'):
            command = cli_command(
                engine, '/' + engine, 'https://localhost:8123/case/x', 1.5,
                'markdown', ca_cert='/tmp/fixture-ca.pem',
            )
            self.assertEqual(command[command.index('--ca-cert') + 1], '/tmp/fixture-ca.pem')

    def test_frozen_origin_proxy_is_passed_to_every_native_client(self):
        for engine in ('moli', 'lightpanda', 'obscura'):
            command = cli_command(
                engine, '/' + engine, 'https://example.test/page', 1.5,
                'markdown', http_proxy='http://127.0.0.1:8123',
            )
            flag = '--proxy' if engine == 'obscura' else '--http-proxy'
            self.assertEqual(command[command.index(flag) + 1], 'http://127.0.0.1:8123')

    def test_obscura_https_fixture_uses_dedicated_ca_environment(self):
        from types import SimpleNamespace
        request = dict(
            engine='obscura', binary='/obscura', url='https://localhost:8123/case/x',
            timeout=1, output_mode='markdown', allow_private_network=True,
            ca_cert='/tmp/fixture-ca.pem',
        )
        completed = SimpleNamespace(returncode=0, stdout=b'# output', stderr=b'')
        with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=completed) as run:
            result = fetch(request)
        self.assertTrue(result['ok'])
        self.assertEqual(run.call_args.kwargs['env']['SSL_CERT_FILE'], '/tmp/fixture-ca.pem')

    def test_native_browser_diagnostics_retain_terminal_error_after_long_warnings(self):
        from types import SimpleNamespace
        diagnostic = b'warning\n' * 1024 + b'ERROR: terminal cause after warnings\n'
        for engine in ('moli', 'obscura', 'lightpanda'):
            for code in (0, 1):
                request = dict(engine=engine, binary='/' + engine,
                               url='https://example.test/page', timeout=1, output_mode='markdown')
                completed = SimpleNamespace(returncode=code, stdout=b'# body', stderr=diagnostic)
                with self.subTest(engine=engine, code=code), patch(
                    'harness.web_fetch.providers.headless_worker.subprocess.run', return_value=completed
                ):
                    result = fetch(request)
                self.assertEqual(result['metadata']['browser_stderr'], diagnostic.decode())
                self.assertEqual(result['ok'], code == 0)
                if code:
                    self.assertTrue(result['error_message'].endswith('ERROR: terminal cause after warnings\n'))

    def test_moli_raw_document_command_omits_dump(self):
        command = cli_command('moli', '/moli', 'https://example.com/paper.pdf', 1.5,
                              'document')
        self.assertEqual(command[:2], ['/moli', 'fetch'])
        self.assertNotIn('--dump', command)
        self.assertNotIn('--wait-until', command)
        self.assertEqual(command[-1], 'https://example.com/paper.pdf')

    def test_moli_raw_file_command_omits_dump(self):
        command = cli_command('moli', '/moli', 'https://example.com/image.png', 1.5, 'file')
        self.assertNotIn('--dump', command)
        self.assertNotIn('--wait-until', command)
        self.assertEqual(command[-1], 'https://example.com/image.png')

    def test_every_measured_moli_version_uses_raw_route_for_direct_files(self):
        pdf = EvalCase('pdf', 'PDF', 'https://example.com/paper.pdf', 'document', [],
                       track='document')
        embedded = EvalCase('embedded', 'Embedded document',
                            'https://example.com/viewer.html', 'document', [],
                            track='document')
        image = EvalCase('image', 'Image', 'https://example.com/image.png', 'image', [],
                         track='image', task_kind='file')
        for name in ('moli-1.0.6', 'moli', 'moli-1.1.3'):
            with self.subTest(provider=name):
                self.assertEqual(output_mode_for_case('moli', 'headless_markdown', pdf),
                                 'markdown')
                self.assertEqual(output_mode_for_case('moli', 'headless_markdown', embedded),
                                 'markdown')
                self.assertEqual(output_mode_for_case('moli', 'headless_markdown', image),
                                 'file')

    def test_unknown_cli_rejected(self):
        with self.assertRaises(ValueError):
            cli_command('unknown', '/binary', 'https://example.com', 45)

    def test_converter_shared_and_nonzero_not_converted(self):
        from types import SimpleNamespace
        request = dict(engine='moli', binary='/binary', url='https://example.com', timeout=1)
        with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=b'<h1>Example</h1><p>Body</p>', stderr=b'')):
            result = fetch(request)
            self.assertTrue(result['ok'])
            self.assertIn('Example', result['content'])
            self.assertIn('<h1>', result['html'])
            self.assertEqual(result['metadata']['extractor_version'], '2.0.0')
        with patch('harness.web_fetch.providers.headless_worker.subprocess.run', return_value=SimpleNamespace(
                returncode=1, stdout=b'<h1>Partial</h1>', stderr=b'failure')):
            result = fetch(request)
            self.assertFalse(result['ok'])
            self.assertEqual(result['content'], '')
            self.assertEqual(result['error_type'], 'browser_exit')

    def test_private_url_validation_and_timeout_validation(self):
        provider = build_provider('moli', dict(type='headless_html', engine='moli'))
        case = EvalCase('x', 'x', 'file:///etc/passwd', 'x', [])
        with self.assertRaises(ValueError):
            provider.fetch(case, 1)
        case = EvalCase('x', 'x', 'https://example.com', 'x', [])
        with self.assertRaises(ValueError):
            provider.fetch(case, float('nan'))

    def test_implementation_identity_is_cached_outside_timed_calls_and_refresh_detects_change(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)/'moli'
            binary.write_bytes(b'first executable')
            provider = build_provider('moli', dict(type='headless_markdown', engine='moli',
                                                    binary=str(binary)))
            provider._resolved_binary = str(binary)
            completed = SimpleNamespace(returncode=0, stdout='moli 1.0\n', stderr='')
            with patch('harness.web_fetch.providers.headless.subprocess.run',
                       return_value=completed) as run:
                first = provider.implementation_identity()
                cached = provider.implementation_identity()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(first, cached)
                binary.write_bytes(b'second executable')
                refreshed = provider.implementation_identity(refresh=True)
                self.assertEqual(run.call_count, 2)
                self.assertNotEqual(first['binary_sha256'], refreshed['binary_sha256'])

    def test_raw_html_is_separate_hashed_evidence(self):
        from harness.web_fetch.models import ProviderResult
        html = '<h1>Fixture</h1>'
        provider = build_provider('moli', dict(type='headless_html', engine='moli'))
        case = EvalCase('x', 'x', 'https://example.com', 'x', [])
        result = ProviderResult(True, 'Fixture', metadata={'_rendered_html': html,
            'rendered_html_sha256': hashlib.sha256(html.encode()).hexdigest()})
        with tempfile.TemporaryDirectory() as directory, patch.object(provider, 'validate', return_value=[]), \
                patch.object(provider, 'fetch', return_value=result):
            root = Path(directory)
            records, _ = run_evaluation(output_dir=root, dataset_meta={'id':'fixture','cases':[]},
                dataset_path=root/'input.json', cases=[case], providers=[provider],
                provider_config_path=root/'config.json', runs=1, timeout=1, delay=0)
            meta = records[0]['provider_metadata']
            self.assertNotIn('_rendered_html', meta)
            self.assertEqual((root/meta['rendered_html_path']).read_text(), html)
            self.assertEqual((root/records[0]['artifact_path']).read_text(), 'Fixture')


if __name__ == '__main__':
    unittest.main()
