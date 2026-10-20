"""Track-scoped execution uses the real runner with local fixture providers."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch import benchmark_runner as run_benchmark
from harness.web_fetch.models import ProviderResult
from harness.web_fetch.providers.base import Provider
from harness.web_fetch.providers.firecrawl import FirecrawlProvider


class BenchmarkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root/'run'
        cases = [{'id': name, 'name': name, 'url': 'https://example.com/'+name,
                  'category': track, 'track': track, 'expected_any': ['content'],
                  **({'retrieval_hints': {
                      'version': 1,
                      'wait_until': 'domcontentloaded',
                      'completion': {'selector': 'main', 'minimum_count': 1,
                                     'timeout_ms': 1000, 'delay_ms': 0},
                  }} if track == 'dynamic' else {})}
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

    def test_alternating_cohort_keeps_each_task_pair_adjacent_and_all_outputs(self):
        calls = []

        class ObservedProvider(Provider):
            def fetch(self, case, timeout):
                if timeout != 30:
                    raise AssertionError('CLI deadline did not reach provider')
                calls.append((self.name, case.id))
                return ProviderResult(True, f"content {self.name} {case.id}")

        config = self.root / 'configs/providers.example.json'
        with patch.object(run_benchmark, 'build_provider', side_effect=ObservedProvider):
            self.run_main([
                '--dataset', str(self.root / 'evaluation/dataset.json'),
                '--provider-config', str(config), '--providers', 'fixture-one,fixture-two',
                '--workers', '1', '--call-order', 'case-alternating', '--measure-only', '--timeout', '30',
            ])
        self.assertEqual(calls, [
            ('fixture-one', 'web-a'), ('fixture-two', 'web-a'),
            ('fixture-two', 'dynamic-a'), ('fixture-one', 'dynamic-a'),
            ('fixture-one', 'dynamic-b'), ('fixture-two', 'dynamic-b'),
        ])
        records = [json.loads(line) for line in (self.output / 'results.jsonl').read_text().splitlines()]
        self.assertEqual(len(records), 6)
        for record in records:
            expected = f"content {record['provider']} {record['case_id']}"
            self.assertEqual((self.output / record['artifact_path']).read_text(), expected)
        execution = json.loads((self.output / 'execution.json').read_text())
        self.assertEqual(execution['aggregate']['call_order'], 'case-alternating')
        self.assertEqual(execution['batches'][0]['parameters']['provider_concurrency'], 1)
        self.assertEqual(execution['batches'][0]['parameters']['timeout'], 30)
        observations = self.root / 'observations.json'
        observations.write_text('[]\n')
        from harness.web_fetch.evidence import finalize_aggregate
        finalize_aggregate(self.output, observations)
        finalized = json.loads((self.output / 'execution.json').read_text())
        self.assertEqual(finalized['batches'][0]['attempt_ids'],
                         execution['batches'][0]['attempt_ids'])

    def test_alternating_cohort_rejects_parallel_workers_before_creating_evidence(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_main(['--call-order', 'case-alternating', '--workers', '2'])
        self.assertFalse(self.output.exists())

    def test_invalid_deadline_is_rejected_before_creating_evidence(self):
        for timeout in ('0', '-1', 'nan', 'inf'):
            with self.subTest(timeout=timeout), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.run_main(['--timeout', timeout])
        self.assertFalse(self.output.exists())

    def test_alternating_cohort_stops_when_a_timed_out_call_cannot_be_cancelled(self):
        import threading
        from harness.web_fetch.config import load_dataset
        from harness.web_fetch.runner import IncompleteEvaluationError, run_evaluation

        release = threading.Event()
        finished = threading.Event()
        calls = []

        class BlockingProvider(Provider):
            def fetch(self, case, timeout):
                calls.append((self.name, case.id))
                if self.name == 'blocked':
                    try:
                        release.wait(5)
                    finally:
                        finished.set()
                return ProviderResult(True, 'content')

        dataset, cases = load_dataset(self.root / 'evaluation/dataset.json')
        providers = [BlockingProvider(name, {'type': 'fixture'}) for name in ('blocked', 'next')]
        try:
            with redirect_stdout(io.StringIO()), self.assertRaises(IncompleteEvaluationError):
                run_evaluation(
                    output_dir=self.output, dataset_meta=dataset,
                    dataset_path=self.root / 'evaluation/dataset.json', cases=cases,
                    providers=providers, provider_config_path=self.root / 'configs/providers.example.json',
                    runs=1, timeout=.01, delay=0, call_order='case-alternating',
                )
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
        self.assertEqual(calls, [('blocked', 'web-a')])
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['attempt_count'], 1)
        self.assertTrue(manifest['unexecuted'])

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

    def test_invalid_explicit_config_fails_before_environment_or_dataset_access(self):
        for config in (self.root / 'missing.json', self.root / 'configs'):
            with self.subTest(config=config), \
                    patch.object(run_benchmark, 'load_runtime_env') as environment, \
                    patch.object(run_benchmark, 'load_dataset') as dataset, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                run_benchmark.main(['--provider-config', str(config), '--output', str(self.output)])
            environment.assert_not_called()
            dataset.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_instance_provider_configuration_does_not_mutate_shared_defaults(self):
        import hashlib
        defaults={p:p.read_bytes() for p in (self.root/'configs').iterdir()}
        config=self.root/'instance-provider.json'
        config.write_text(json.dumps({'providers':{'instance-only':{
            'type':'fixture','fail_cases':['dynamic-b']}}}))
        self.run_main(['--providers','instance-only','--provider-config',str(config),'--measure-only'])
        rows=[json.loads(s) for s in (self.output/'results.jsonl').read_text().splitlines()]
        self.assertEqual({r['case_id']:r['ok'] for r in rows},
            {'web-a':True,'dynamic-a':True,'dynamic-b':False})
        batches=json.loads((self.output/'execution.json').read_text())['batches']
        self.assertEqual({attempt for batch in batches for attempt in batch['attempt_ids']},
                         {row['attempt_id'] for row in rows})
        self.assertTrue(all(b['provider_config_source']==str(config.resolve()) for b in batches))
        self.assertTrue(all(b['provider_config_sha256']==hashlib.sha256(config.read_bytes()).hexdigest() for b in batches))
        self.assertEqual({p:p.read_bytes() for p in defaults},defaults)

    def test_validate_only_does_not_measure_build_or_write(self):
        with patch.object(run_benchmark, 'run_evaluation') as run:
            build = self.run_main(['--track','dynamic','--providers','fixture-one','--validate-only'])
        run.assert_not_called()
        build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_explicit_provider_config_does_not_use_repository_scope_files(self):
        import shutil
        custom = self.root / 'custom-providers.json'
        custom.write_text(json.dumps({'providers': {'custom': {'type': 'fixture'}}}))
        shutil.rmtree(self.root / 'configs')
        self.run_main(['--track', 'dynamic', '--providers', 'custom',
                       '--provider-config', str(custom)])
        records = [json.loads(line) for line in
                   (self.output / 'results.jsonl').read_text().splitlines()]
        self.assertEqual({(row['provider'], row['case_id']) for row in records},
                         {('custom', 'dynamic-a'), ('custom', 'dynamic-b')})
        batches = json.loads((self.output / 'execution.json').read_text())['batches']
        self.assertTrue(all(batch['provider_config_source'] == str(custom.resolve()) for batch in batches))

    def test_capacity_preflight_receives_whole_worst_case_before_output_or_fetch(self):
        capacity_calls = []
        fetch_calls = []

        class CapacityProvider(Provider):
            def preflight_capacity(self, declared_calls, timeout=10):
                capacity_calls.append((self.name, declared_calls, timeout))
                raise ValueError('insufficient declared capacity')

            def fetch(self, case, timeout):
                fetch_calls.append(case.id)
                return ProviderResult(True, 'content')

        for filename in ['providers.example.json', 'providers.resources.json']:
            (self.root/'configs'/filename).write_text(json.dumps({'providers': {
                'capacity': {'type': 'fixture'},
            }}))
        with patch.object(
            run_benchmark, 'build_provider',
            side_effect=lambda name, config: CapacityProvider(name, config),
        ), self.assertRaisesRegex(ValueError, 'insufficient declared capacity'):
            self.run_main([
                '--providers', 'capacity', '--runs', '2',
                '--max-failure-retries', '1', '--measure-only',
            ])

        self.assertEqual(capacity_calls, [('capacity', 12, 10)])
        self.assertEqual(fetch_calls, [])
        self.assertFalse(self.output.exists())

    def test_validate_only_never_calls_capacity_hook(self):
        capacity_calls = []

        class CapacityProvider(Provider):
            def preflight_capacity(self, declared_calls, timeout=10):
                capacity_calls.append((declared_calls, timeout))

            def fetch(self, case, timeout):
                raise AssertionError('fetch must not be called')

        (self.root/'configs/providers.resources.json').write_text(json.dumps({
            'providers': {'capacity': {'type': 'fixture'}},
        }))
        with patch.object(
            run_benchmark, 'build_provider',
            side_effect=lambda name, config: CapacityProvider(name, config),
        ):
            self.run_main([
                '--track', 'dynamic', '--providers', 'capacity', '--validate-only',
            ])

        self.assertEqual(capacity_calls, [])
        self.assertFalse(self.output.exists())

    def test_exact_case_subset_and_measure_only(self):
        build = self.run_main(['--case-ids','dynamic-b,web-a',
                               '--providers','fixture-one','--measure-only'])
        build.assert_not_called()
        data = json.loads((self.output/'dataset.json').read_text())
        self.assertEqual([case['id'] for case in data['cases']], ['web-a','dynamic-b'])
        records = [json.loads(line) for line in (self.output/'results.jsonl').read_text().splitlines()]
        self.assertEqual({row['case_id'] for row in records}, {'web-a','dynamic-b'})
        execution = json.loads((self.output/'execution.json').read_text())
        self.assertEqual(execution['aggregate']['providers'], ['fixture-one'])
        self.assertEqual(execution['aggregate']['case_ids'], ['web-a','dynamic-b'])
        self.assertEqual(execution['aggregate']['batch_count'], 2)
        self.assertFalse((self.output/'report').exists())

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
                     ['--providers',','], ['--case-ids',','],
                     ['--case-ids','dynamic-a,dynamic-a'], ['--case-ids','absent'],
                     ['--track','dynamic','--case-ids','web-a,dynamic-b'],
                     ['--workers','0'], ['--runs','0']]:
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

    def test_materializes_all_hash_bound_worker_artifacts(self):
        worker = self.root / 'worker'
        output = self.root / 'aggregate'
        files = {
            'artifacts/p/c/run-001.txt': b'answer',
            'artifacts/p/c/run-001.html.txt': b'<main>answer</main>',
            'artifacts/p/c/run-001-file-000.bin': b'\x89PNG\r\n\x1a\nbytes',
            'artifacts/p/c/run-000.txt': b'failed answer',
            'artifacts/p/c/run-000.bin': b'failed raw bytes',
        }
        for relative, content in files.items():
            path = worker / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        digest = lambda value: __import__('hashlib').sha256(value).hexdigest()
        record = {
            'artifact_path': 'artifacts/p/c/run-001.txt',
            'metrics': {'content_sha256': digest(files['artifacts/p/c/run-001.txt'])},
            'provider_metadata': {
                'rendered_html_path': 'artifacts/p/c/run-001.html.txt',
                'rendered_html_sha256': digest(files['artifacts/p/c/run-001.html.txt']),
                'file_evaluation': {'files': [{
                    'artifact_path': 'artifacts/p/c/run-001-file-000.bin',
                    'sha256': digest(files['artifacts/p/c/run-001-file-000.bin']),
                }]},
                'retry': {'attempts': [{
                    'artifact_path': 'artifacts/p/c/run-000.txt',
                    'artifact_paths': [
                        'artifacts/p/c/run-000.txt', 'artifacts/p/c/run-000.bin',
                    ],
                    'content_sha256': digest(files['artifacts/p/c/run-000.txt']),
                    'artifact_sha256': {relative: digest(content) for relative, content in files.items()
                                        if 'run-000.' in relative},
                }]},
            },
        }

        run_benchmark.materialize_bound_artifacts(output, worker, [record])

        for relative, content in files.items():
            self.assertEqual((output / relative).read_bytes(), content)
        retry = record['provider_metadata']['retry']['attempts'][0]
        self.assertEqual(
            retry['artifact_sha256']['artifacts/p/c/run-000.bin'],
            digest(files['artifacts/p/c/run-000.bin']),
        )


class FirecrawlValidationTests(unittest.TestCase):
    def test_hosted_requires_key_self_hosted_may_be_anonymous(self):
        self.assertEqual(FirecrawlProvider('firecrawl',{}).validate(), ['api_key'])
        self.assertEqual(FirecrawlProvider('firecrawl',{'api_key':'   '}).validate(), ['api_key'])
        self.assertEqual(FirecrawlProvider('firecrawl',{'api_key':'test'}).validate(), [])
        self.assertEqual(FirecrawlProvider('local',{'base_url':'http://127.0.0.1:3002'}).validate(), [])
