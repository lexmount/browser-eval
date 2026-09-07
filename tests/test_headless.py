import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.web_fetch.models import EvalCase
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.headless import cli_command
from harness.web_fetch.runner import run_evaluation
from scripts.headless_worker import fetch


class HeadlessTests(unittest.TestCase):
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
