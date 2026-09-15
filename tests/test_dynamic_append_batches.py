"""Offline six-case/twelve-provider append; no canonical files or runtimes used."""
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.append_dynamic_content import append


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class DynamicAppendBatchesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.reference, self.measured = [self.root / name
            for name in ('source', 'reference', 'measured')]
        self.names = [f'provider-{i:02}' for i in range(12)]
        old_cases = [{'id': f'old-{i}', 'track': 'web' if i < 140 else 'dynamic'}
                     for i in range(144)]
        self.baseline_dataset = {'cases': old_cases, 'coverage_plan': {
            'web': ['web-only', *self.names], 'dynamic': list(reversed(self.names))}}
        dump(self.source / 'dataset.json', self.baseline_dataset)
        # Exactly 1728 pre-existing records, unusual whitespace, Unicode and CRLF.
        old_records = [{'attempt_id': f"{name}__{case['id']}", 'case_id': case['id'],
                        'provider': name, 'metrics': {'content_pass': False, 'word_f1': .125},
                        'note': '原始证据'} for case in old_cases for name in self.names]
        self.baseline_bytes = ''.join(json.dumps(r, ensure_ascii=False,
            separators=(',  ', ': ')) + '\r\n' for r in old_records).encode('utf-8')
        (self.source / 'results.jsonl').write_bytes(self.baseline_bytes)
        self.baseline_execution = {'retained': 'execution provenance', 'batches': [{
            'batch_id': 'baseline', 'attempt_ids': [r['attempt_id'] for r in old_records]}]}
        dump(self.source / 'execution.json', self.baseline_execution)
        dump(self.source / 'sources/old-140/evidence.json', {'keep': 'old evidence'})
        dump(self.source / 'artifacts/old/result.txt', {'keep': 'old output'})
        self.cases, self.checks, self.records, self.batches = [], [], [], []
        for i in range(6):
            key = f'new-{i}'
            rendered, initial = f'{key} 完整记录', '<main>Loading</main>'
            raw = rendered.encode('utf-8')
            case = {'id': key, 'name': key, 'url': f'https://example.test/{key}',
                    'category': 'async', 'track': 'dynamic', 'task_kind': 'body',
                    'expected_any': [], 'required_facts': [{'id': 'record', 'alternatives': [rendered]}],
                    'min_content_chars': 5, 'fact_coverage_threshold': 1.0,
                    'ground_truth_main_content': rendered,
                    'source_benchmark': {'capture_scope': 'one complete record', 'expected_entries': 1},
                    'source_verification': {'reference_sha256': digest(raw)}}
            self.cases.append(case)
            folder = self.reference / 'sources' / key
            folder.mkdir(parents=True)
            for name, content in [('initial.html.txt', initial.encode()), ('rendered.txt', raw),
                                  ('reference.txt', raw)]:
                (folder / name).write_bytes(content)
            dump(folder / 'evidence.json', {'initial_sha256': digest(initial.encode()),
                 'rendered_sha256': digest(raw), 'reference_sha256': digest(raw)})
            self.checks.append({'id': key, 'entries': [rendered], 'satisfied': True,
                'timeout_ms': 1000, 'elapsed_ms': 10, 'checked_at': '2026-09-03T12:00:00+00:00'})
        for directory in (self.reference, self.measured):
            dump(directory / 'dataset.json', {'cases': self.cases})
        # Historical pre-certification orchestration shape: nine single-provider
        # batches plus one local-three batch.  Omitting schema_version is
        # intentional: these fixtures exercise append transaction invariants,
        # not the current certified-run reference protocol.
        groups = [[name] for name in self.names[:9]] + [self.names[9:]]
        for index, names in enumerate(groups):
            start = datetime(2026, 9, 3, tzinfo=timezone.utc) + timedelta(minutes=index)
            batch = {'batch_id': f'measured-{index}', 'tool': 'web_fetch',
                     'started_at': start.isoformat(),
                     'completed_at': (start + timedelta(seconds=30)).isoformat(),
                     'dataset': {'source': f'original-batch-{index}/frozen.json', 'case_count': 6},
                     'providers': [{'name': name, 'type': 'offline', 'min_interval_seconds': index / 10}
                                   for name in names],
                     'parameters': {'runs': 1, 'timeout': 45, 'delay': .5,
                                    'provider_concurrency': len(names)},
                     'provider_config_source': f'original-batch-{index}/providers.json',
                     'runtime_identity_record': {'fixture': index},
                     'attempt_ids': [], 'attempt_count': len(names) * 6}
            for name in names:
                for case in self.cases:
                    artifact = f"artifacts/{name}/{case['id']}/run-001.txt"
                    raw = case['ground_truth_main_content'].encode('utf-8')
                    (self.measured / artifact).parent.mkdir(parents=True, exist_ok=True)
                    (self.measured / artifact).write_bytes(raw)
                    record = {'attempt_id': f"{name}__{case['id']}__r001", 'provider': name,
                              'provider_type': 'offline', 'case_id': case['id'],
                              'case_name': case['name'], 'url': case['url'], 'category': case['category'],
                              'run': 1, 'started_at': (start + timedelta(seconds=1)).isoformat(),
                              'elapsed_ms': 12.34, 'artifact_path': artifact,
                              'metrics': {'content_sha256': digest(raw), 'content_pass': False,
                                          'word_f1': .123456789, 'unknown_metric': [1, None]},
                              'provider_metadata': {'configured_min_interval_seconds': index / 10}}
                    self.records.append(record)
                    batch['attempt_ids'].append(record['attempt_id'])
            self.batches.append(batch)
        self.save()

    def save(self):
        dump(self.measured / 'execution.json', {'batches': self.batches})
        (self.measured / 'results.jsonl').write_text(
            ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in self.records), encoding='utf-8')

    def snapshot(self, directory=None):
        directory = directory or self.source
        return {p.relative_to(directory).as_posix(): p.read_bytes()
                for p in directory.rglob('*') if p.is_file()}

    def run_append(self):
        append(self.source, self.reference, self.measured, self.checks)

    def rejects(self, message):
        self.save()
        before = self.snapshot()
        with patch('scripts.append_dynamic_content.tempfile.mkdtemp') as staging:
            with self.assertRaisesRegex(ValueError, message):
                self.run_append()
            staging.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_six_by_twelve_preserves_1728_bytes_batches_settings_and_scores(self):
        before = self.snapshot()
        measured_before, reference_before = self.snapshot(self.measured), self.snapshot(self.reference)
        self.run_append()
        combined = (self.source / 'results.jsonl').read_bytes()
        self.assertEqual(combined[:len(self.baseline_bytes)], self.baseline_bytes)
        self.assertEqual(len(combined.splitlines()), 1800)
        self.assertEqual([json.loads(r) for r in combined.splitlines()[1728:]], self.records)
        execution = json.loads((self.source / 'execution.json').read_text())
        self.assertEqual(execution, {**self.baseline_execution,
            'batches': self.baseline_execution['batches'] + self.batches})
        dataset = json.loads((self.source / 'dataset.json').read_text())
        self.assertEqual(dataset['cases'][:144], self.baseline_dataset['cases'])
        self.assertEqual(dataset['coverage_plan'], self.baseline_dataset['coverage_plan'])
        self.assertEqual(sum(c['track'] == 'dynamic' for c in dataset['cases']), 10)
        for name, raw in before.items():
            if name not in {'dataset.json', 'results.jsonl', 'execution.json'}:
                self.assertEqual((self.source / name).read_bytes(), raw)
        self.assertEqual(self.snapshot(self.measured), measured_before)
        self.assertEqual(self.snapshot(self.reference), reference_before)
        self.assertEqual(list(self.root.glob('.source-append-*')), [])

    def test_baseline_without_final_newline_preserved(self):
        self.baseline_bytes = self.baseline_bytes.rstrip(b'\r\n')
        (self.source / 'results.jsonl').write_bytes(self.baseline_bytes)
        self.run_append()
        raw = (self.source / 'results.jsonl').read_bytes()
        self.assertEqual(raw[:len(self.baseline_bytes)], self.baseline_bytes)
        self.assertEqual(len(raw.splitlines()), 1800)

    def test_case_shards_of_same_provider_are_original_batches(self):
        batch = self.batches.pop(0)
        for index in range(2):
            shard = copy.deepcopy(batch)
            shard['batch_id'] += f'-shard-{index}'
            shard['attempt_ids'] = batch['attempt_ids'][index * 3:(index + 1) * 3]
            shard['attempt_count'] = shard['dataset']['case_count'] = 3
            self.batches.append(shard)
        self.save()
        self.run_append()
        self.assertEqual(json.loads((self.source / 'execution.json').read_text())['batches'][1:],
                         self.batches)

    def test_runner_batches_without_batch_ids_are_not_given_synthetic_ids(self):
        for batch in self.batches:
            del batch['batch_id']
        self.save()
        self.run_append()
        self.assertEqual(json.loads((self.source / 'execution.json').read_text())['batches'][1:],
                         self.batches)

    def test_tampered_duplicate_and_missing_batch_attempts(self):
        original = copy.deepcopy(self.batches)
        for operation in ('unknown', 'duplicate_within', 'duplicate_across', 'missing', 'count'):
            with self.subTest(operation=operation):
                self.batches = copy.deepcopy(original)
                batch = self.batches[0]
                if operation == 'unknown':
                    batch['attempt_ids'][0] = 'unmeasured'
                elif operation == 'duplicate_within':
                    batch['attempt_ids'][0] = batch['attempt_ids'][1]
                elif operation == 'duplicate_across':
                    self.batches.append(copy.deepcopy(batch))
                    self.batches[-1]['batch_id'] = 'other-batch'
                elif operation == 'missing':
                    self.batches.pop(0)
                else:
                    batch['attempt_count'] += 1
                self.rejects('partition|manifest mismatch')

    def test_duplicate_and_existing_batch_ids_rejected(self):
        for batch_id in ('baseline', self.batches[1]['batch_id'], ''):
            with self.subTest(batch_id=batch_id):
                self.batches[0]['batch_id'] = batch_id
                self.rejects('batch ID')

    def test_provider_membership_settings_and_casecount_mismatch(self):
        original = copy.deepcopy(self.batches)
        for operation in ('replace', 'extra', 'duplicate', 'type', 'interval', 'case_count', 'runs'):
            with self.subTest(operation=operation):
                self.batches = copy.deepcopy(original)
                batch = self.batches[0]
                if operation == 'replace':
                    batch['providers'][0]['name'] = 'different'
                elif operation == 'extra':
                    batch['providers'].append({'name': 'extra'})
                elif operation == 'duplicate':
                    batch['providers'] *= 2
                elif operation == 'type':
                    batch['providers'][0]['type'] = 'different'
                elif operation == 'interval':
                    batch['providers'][0]['min_interval_seconds'] = 99
                elif operation == 'case_count':
                    batch['dataset']['case_count'] = 5
                else:
                    batch['parameters']['runs'] = 2
                self.rejects('provider|case_count|exactly once')

    def test_missing_and_duplicate_case_provider_pairs(self):
        original_records, original_batches = copy.deepcopy(self.records), copy.deepcopy(self.batches)
        for operation in ('missing', 'duplicate', 'duplicate_identity', 'baseline_identity'):
            with self.subTest(operation=operation):
                self.records, self.batches = copy.deepcopy(original_records), copy.deepcopy(original_batches)
                if operation == 'missing':
                    self.records.pop(0)
                    self.batches[0]['attempt_ids'].pop(0)
                    self.batches[0]['attempt_count'] = self.batches[0]['dataset']['case_count'] = 5
                elif operation == 'duplicate':
                    self.records[0]['case_id'] = self.records[1]['case_id']
                elif operation == 'duplicate_identity':
                    self.records[0]['attempt_id'] = self.records[1]['attempt_id']
                else:
                    self.records[0]['attempt_id'] = 'provider-00__old-0'
                self.rejects('attempt|case_count')

    def test_complete_but_different_planned_cohort_rejected(self):
        # All remaining pairs and batch metadata are internally valid (6 x 11).
        self.batches.pop(0)
        self.records = [r for r in self.records if r['provider'] != self.names[0]]
        self.rejects('existing dynamic planned cohort')

    def test_same_size_but_different_planned_cohort_rejected(self):
        self.batches[0]['providers'][0]['name'] = 'replacement-provider'
        for record in self.records[:6]:
            record['provider'] = 'replacement-provider'
        self.rejects('existing dynamic planned cohort')

    def test_missing_existing_dynamic_plan_rejected(self):
        dataset = copy.deepcopy(self.baseline_dataset)
        del dataset['coverage_plan']['dynamic']
        dump(self.source / 'dataset.json', dataset)
        self.rejects('planned provider cohort')

    def test_completed_timestamps_and_attempt_bounds(self):
        original = copy.deepcopy(self.batches)
        for value in (None, '', 'invalid', '2026-09-03T00:00:01',
                      '2026-09-02T23:59:59+00:00', '2026-09-03T00:00:01+00:00'):
            with self.subTest(completed_at=value):
                self.batches = copy.deepcopy(original)
                if value is None:
                    del self.batches[0]['completed_at']
                else:
                    self.batches[0]['completed_at'] = value
                self.rejects('timestamp|completed|timing')

    def test_invalid_attempt_timing_and_provider_identity(self):
        original = copy.deepcopy(self.records[0])
        for field, value in (('started_at', '2026-09-02T23:59:59+00:00'),
                             ('elapsed_ms', -1), ('elapsed_ms', float('nan')),
                             ('elapsed_ms', 1e300), ('provider_type', 'wrong'),
                             ('batch_id', 'wrong'), ('url', 'https://example.test/other')):
            with self.subTest(field=field, value=value):
                self.records[0] = {**original, field: value}
                self.rejects('timing|identity')

    def test_frozen_scoring_mismatch_rejected(self):
        changed = copy.deepcopy(self.cases)
        changed[0]['fact_coverage_threshold'] = .1
        dump(self.measured / 'dataset.json', {'cases': changed})
        self.rejects('frozen reference')

    def test_execution_is_authoritative_not_fallback_to_manifest(self):
        dump(self.measured / 'manifest.json', self.batches[0])
        self.batches = []
        self.rejects('execution batches')

    def test_hash_path_and_collision_checks_remain_preflight(self):
        original = copy.deepcopy(self.records[0])
        for artifact in ('../outside.txt', '/outside.txt', 'sources/new-0/render-audit.json'):
            with self.subTest(artifact=artifact):
                self.records[0] = {**original, 'artifact_path': artifact}
                self.rejects('relative|inside artifacts')
        self.records[0] = original
        target = self.measured / original['artifact_path']
        raw = target.read_bytes()
        target.write_bytes(b'tampered')
        self.rejects('Output hash mismatch')
        target.write_bytes(raw)
        dump(self.source / original['artifact_path'], {'existing': True})
        self.rejects('Output collision')

    def test_transaction_failure_restores_all_1728_original_records(self):
        before = self.snapshot()
        rename = Path.rename

        def fail_promotion(path, target):
            if path.name == 'staged':
                raise OSError('promotion failed')
            return rename(path, target)

        with patch.object(Path, 'rename', fail_promotion):
            with self.assertRaisesRegex(OSError, 'promotion failed'):
                self.run_append()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.root.glob('.source-append-*')), [])


if __name__ == '__main__':
    unittest.main()
