import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.headless import cli_command, output_mode_for_case
from harness.web_fetch.providers.headless import _resolve_binary
from harness.web_fetch.runner import run_evaluation
from scripts.headless_worker import fetch
from scripts.review_headless_results import independent_attempt
from scripts.review_headless_results import main as review_main


class HeadlessTests(unittest.TestCase):
    def test_explicit_empty_chromium_binary_never_uses_playwright_fallback(self):
        self.assertIsNone(_resolve_binary('chromium', ''))

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

    def test_independent_review_receives_real_file_evidence(self):
        row = {'attempt_id':'a','provider':'moli-1.1.3','artifact':'empty.txt',
               'request_ok':True,'error_type':None,'file_artifacts':['file.bin'],
               'raw_response_artifact':'raw.bin',
               'answer_metadata_artifact':'answer.json',
               'metrics':{'raw_file_pass':True}}
        packet = independent_attempt(row)
        self.assertEqual(packet['file_artifacts'], ['file.bin'])
        self.assertEqual(packet['raw_response_artifact'], 'raw.bin')
        self.assertEqual(packet['answer_metadata_artifact'], 'answer.json')
        self.assertNotIn('metrics', packet)

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
        from harness.web_fetch.metrics import normalize_for_match
        old = r'(?<=[\u3400-\u9fff])\s+|\s+(?=[\u3400-\u9fff])'
        new = r'(?<=[\u3400-\u9fff])\s+|(?<!\s)\s+(?=[\u3400-\u9fff])'
        for parts in itertools.product(('a','中','\n',' ','\t','1',''), repeat=5):
            text = ''.join(parts)
            self.assertEqual(re.sub(old, '', text), re.sub(new, '', text))
        self.assertEqual(normalize_for_match('left'+' '*100_000+'right'), 'left right')
        self.assertEqual(normalize_for_match('中'+' '*100_000+'文'), '中文')

    def test_visible_link_titles_and_markdown_escapes_do_not_change_facts(self):
        from harness.web_fetch.metrics import fact_in_text, normalize_for_match
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
        image = EvalCase('image', 'Image', 'https://example.com/image.png', 'image', [],
                         track='image', task_kind='file')
        for name in ('moli-1.0.6', 'moli', 'moli-1.1.3'):
            with self.subTest(provider=name):
                self.assertEqual(output_mode_for_case('moli', 'headless_markdown', pdf),
                                 'document')
                self.assertEqual(output_mode_for_case('moli', 'headless_markdown', image),
                                 'file')

    def test_moli_raw_pdf_is_extracted_and_preserved(self):
        from types import SimpleNamespace
        raw = b'%PDF-fixture'
        request = dict(engine='moli', binary='/moli', url='https://example.com/paper.pdf',
                       timeout=1, output_mode='document')
        with patch('scripts.headless_worker.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=raw, stderr=b'')), patch(
                'scripts.headless_worker.extract_document_text', return_value=('Paper body', 'pypdf')):
            result = fetch(request)
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], 'Paper body')
        self.assertEqual(result['metadata']['document_extractor'], 'pypdf')
        self.assertEqual(result['metadata']['raw_stdout_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertIn('_raw_stdout_b64', result['metadata'])

    def test_moli_raw_file_is_returned_without_text_decoding(self):
        from types import SimpleNamespace
        raw = b'\x89PNG\r\n\x1a\n' + b'payload'
        request = dict(engine='moli', binary='/moli', url='https://example.com/image.png',
                       timeout=1, output_mode='file')
        with patch('scripts.headless_worker.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=raw, stderr=b'')):
            result = fetch(request)
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], '')
        self.assertEqual(result['files_b64'], [base64.b64encode(raw).decode('ascii')])

    def test_moli_document_html_fallback_remains_a_scored_text_answer(self):
        from types import SimpleNamespace
        request = dict(engine='moli', binary='/moli', url='https://example.com/file.docx',
                       timeout=1, output_mode='document')
        completed = [
            SimpleNamespace(returncode=0, stdout=b'<h1>Unsupported document</h1>', stderr=b''),
            SimpleNamespace(returncode=0, stdout='v1'.encode(), stderr=b''),
            SimpleNamespace(returncode=0, stdout=b'# Unsupported document\n', stderr=b''),
        ]
        with patch('scripts.headless_worker.subprocess.run', side_effect=completed):
            result = fetch(request)
        self.assertTrue(result['ok'])
        self.assertEqual(result['content'], '# Unsupported document\n')
        self.assertEqual(result['metadata']['document_response'],
                         'serialized_html_not_raw_document')

    def test_unknown_cli_rejected(self):
        with self.assertRaises(ValueError):
            cli_command('unknown', '/binary', 'https://example.com', 45)

    def test_converter_shared_and_nonzero_not_converted(self):
        from types import SimpleNamespace
        request = dict(engine='moli', binary='/binary', url='https://example.com', timeout=1)
        with patch('scripts.headless_worker.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=b'<h1>Example</h1><p>Body</p>', stderr=b'')):
            result = fetch(request)
            self.assertTrue(result['ok'])
            self.assertIn('Example', result['content'])
            self.assertIn('<h1>', result['html'])
            self.assertEqual(result['metadata']['extractor_version'], '2.0.0')
        with patch('scripts.headless_worker.subprocess.run', return_value=SimpleNamespace(
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
