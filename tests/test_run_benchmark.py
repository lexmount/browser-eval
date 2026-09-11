"""Track-scoped execution uses the real runner with local fixture providers."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_benchmark
from harness.web_fetch.providers.firecrawl import FirecrawlProvider


class BenchmarkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root/'run'
        cases = [{'id': name, 'name': name, 'url': 'https://example.com/'+name,
                  'category': track, 'track': track, 'expected_any': ['content']}
                 for name, track in [('web-a', 'web'), ('dynamic-a', 'dynamic'), ('dynamic-b', 'dynamic')]]
        self.dataset = {'id': 'test', 'tool': 'web_fetch', 'cases': cases}
        (self.root/'evaluation').mkdir()
        (self.root/'configs').mkdir()
        (self.root/'evaluation/dataset.json').write_text(json.dumps(self.dataset))
        for case in cases:
            source = self.root/'evaluation/sources'/case['id']
            source.mkdir(parents=True)
            (source/'reference.txt').write_text('content')
        for filename in ['providers.example.json', 'providers.resources.json']:
            (self.root/'configs'/filename).write_text(json.dumps({'providers': {
                'fixture-one': {'type': 'fixture'}, 'fixture-two': {'type': 'fixture'}}}))

    def run_main(self, args):
        with patch.object(run_benchmark, 'ROOT', self.root), \
                patch.object(run_benchmark, 'load_runtime_env'), \
                patch.object(run_benchmark, 'build_report', return_value='built') as build, \
                redirect_stdout(io.StringIO()):
            run_benchmark.main(['--output', str(self.output), *args])
        return build

    def test_dynamic_subset_real_fixture_calls_and_exact_coverage(self):
        self.run_main(['--track', 'dynamic', '--providers', 'fixture-one,fixture-two'])
        data = json.loads((self.output/'dataset.json').read_text())
        self.assertEqual(data['cases'], self.dataset['cases'][1:])
        self.assertEqual(data['coverage_plan'], {'dynamic': ['fixture-one', 'fixture-two']})
        records = [json.loads(s) for s in (self.output/'results.jsonl').read_text().splitlines()]
        self.assertEqual(len(records), 4)
        self.assertEqual({(r['provider'],r['case_id']) for r in records},
                         {(p,c) for p in ['fixture-one','fixture-two'] for c in ['dynamic-a','dynamic-b']})
        batches = json.loads((self.output/'execution.json').read_text())['batches']
        self.assertEqual(len(batches), 2)
        self.assertTrue(all(b['dataset']['case_count']==2 for b in batches))
        self.assertTrue(all(b['provider_config_source'].endswith('providers.resources.json') for b in batches))
        self.assertEqual({p.name for p in (self.output/'sources').iterdir()}, {'dynamic-a','dynamic-b'})
        self.assertFalse((self.output/'workers/fixture-one/web').exists())

    def test_validate_only_does_not_measure_build_or_write(self):
        with patch.object(run_benchmark, 'run_evaluation') as run:
            build = self.run_main(['--track','dynamic','--providers','fixture-one','--validate-only'])
        run.assert_not_called()
        build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_exact_case_subset_can_skip_report(self):
        build = self.run_main(['--case-ids','dynamic-b','--providers','fixture-one','--skip-report'])
        build.assert_not_called()
        data = json.loads((self.output/'dataset.json').read_text())
        self.assertEqual([case['id'] for case in data['cases']], ['dynamic-b'])
        records = [json.loads(s) for s in (self.output/'results.jsonl').read_text().splitlines()]
        self.assertEqual([(row['provider'],row['case_id']) for row in records],
                         [('fixture-one','dynamic-b')])

    def test_dynamic_subset_builds_a_real_report(self):
        with patch.object(run_benchmark, 'ROOT', self.root), \
                patch.object(run_benchmark, 'load_runtime_env'), \
                redirect_stdout(io.StringIO()):
            run_benchmark.main(['--output', str(self.output), '--track', 'dynamic',
                                '--providers', 'fixture-one'])
        report = self.output/'report'
        self.assertTrue((report/'index.html').is_file())
        data = json.loads((report/'dataset.json').read_text())
        self.assertEqual({c['id'] for c in data['cases']}, {'dynamic-a','dynamic-b'})
        self.assertFalse((report/'sources/web-a').exists())

    def test_unselected_configuration_is_not_required(self):
        (self.root/'configs/providers.example.json').unlink()
        self.run_main(['--track','dynamic','--providers','fixture-one','--validate-only'])
        self.assertFalse(self.output.exists())

    def test_invalid_selection_rejected_before_output_creation(self):
        for args in [['--track','absent'], ['--providers','fixture-one,fixture-one'],
                     ['--providers',','], ['--workers','0'],
                     ['--case-ids','absent'], ['--case-ids','dynamic-a,dynamic-a']]:
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.run_main(args)
            self.assertFalse(self.output.exists())

    def test_reports_all_missing_configuration_without_requests(self):
        configs = {'providers': {'firecrawl': {'type':'firecrawl'},
                                'tavily': {'type':'tavily_extract'},
                                'exa': {'type':'exa_contents'}}}
        (self.root/'configs/providers.resources.json').write_text(json.dumps(configs))
        stderr = io.StringIO()
        with redirect_stderr(stderr), patch.object(run_benchmark, 'run_evaluation') as run:
            with self.assertRaises(SystemExit):
                self.run_main(['--track','dynamic','--providers','firecrawl,tavily,exa','--validate-only'])
        run.assert_not_called()
        for provider in ['firecrawl','tavily','exa']:
            self.assertIn(f'resources/{provider}: missing api_key', stderr.getvalue())
        self.assertFalse(self.output.exists())


class FirecrawlValidationTests(unittest.TestCase):
    def test_hosted_requires_key_self_hosted_may_be_anonymous(self):
        self.assertEqual(FirecrawlProvider('firecrawl',{}).validate(), ['api_key'])
        self.assertEqual(FirecrawlProvider('firecrawl',{'api_key':'   '}).validate(), ['api_key'])
        self.assertEqual(FirecrawlProvider('firecrawl',{'api_key':'test'}).validate(), [])
        self.assertEqual(FirecrawlProvider('local',{'base_url':'http://127.0.0.1:3002'}).validate(), [])
